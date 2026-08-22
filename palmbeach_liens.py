#!/usr/bin/env python3
"""palmbeach_liens.py — recorded-lien chains for Palm Beach leads via the clerk's Landmark Web
(erec.mypalmbeachclerk.com). Writes palmbeach_liens.json in the SAME schema broward_liens.json
uses, which make_tracker's county merge bakes into the deal-modal prefills automatically.

THE WALL: every search endpoint here is gated by reCAPTCHA v2 (the 304x78 checkbox widget —
sitekey 6LdBHOorAAAAALwRLkAZpnNsfcp7qfFS4YIGIRTU). There is NO programmatic mint like MD's v3
(verified 2026-07-20: NameSearch/ParcelIdSearch/QuickSearch all answer 'Invalid Captcha' without
a solved token). Run modes:

  (default)  curl-only when ShowCaptcha=False. When gated: try 2Captcha reCAPTCHA v2 first
             (captcha.key / captcha_solver.solve_recaptcha_v2), then fail clearly if no token.
  --headed   fallback only after 2Captcha fails (or --no-2captcha): opens a VISIBLE browser;
             operator clicks 'I'm not a robot' once; we harvest the token.

GHA note: daily refresh can use 2Captcha when captcha.key is in secrets; otherwise PB traces
happen on Alejandro's machine. Cache persists palmbeach_liens.json for daily builds.

PRECISION: PARCEL search first (the lead's 17-digit PCN isolates the property exactly — no
namesakes, no sister-LLC blending); then owner NAME search supplements docs Landmark does not
index on the parcel (LP / FJ / Lady Bird). Empty GetSearchResults is a real empty hit — never
invent liens. The chain analysis itself is broward_liens.analyze — the same guarded satisfaction
matching (book/page -> assignee-chain -> lender-window -> refi-kill) as every other county.
"""
import argparse, json, os, re, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from broward_liens import analyze, _fc_type_plaintiff, _num   # the shared chain engine
from records_liens import untraceable_owner            # shared junk-owner rule

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'palmbeach_leads.json')
OUT = os.path.join(HERE, 'palmbeach_liens.json')
BASE = 'https://erec.mypalmbeachclerk.com'
# Landmark Web reCAPTCHA v2 (checkbox) — same key the site embeds in grecaptcha.render
SITE_KEY = '6LdBHOorAAAAALwRLkAZpnNsfcp7qfFS4YIGIRTU'
# Full page URL where the v2 widget loads (2Captcha requires pageurl = page with the captcha).
PAGE_URL = (BASE + '/search/index?theme=.blue&section=searchCriteriaParcelId'
            '&quickSearchSelection=')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
JAR = os.path.join(tempfile.gettempdir(), 'pb_liens_cookies.txt')
CURL = r'C:\Windows\System32\curl.exe' if os.name == 'nt' and os.path.exists(r'C:\Windows\System32\curl.exe') else 'curl'
DUMP = os.path.join(HERE, '_pb_last_results.html')             # raw first-success dump (parser iteration aid)


def solve_token_2captcha():
    """Solve Landmark reCAPTCHA v2 via 2Captcha. Returns token or ''."""
    try:
        from captcha_solver import solve_recaptcha_v2
    except Exception as e:
        print(f'  [2captcha] import failed: {e}')
        return ''
    print('>> solving Landmark reCAPTCHA v2 via 2Captcha (1–3 min)…')
    t0 = time.time()
    tok = solve_recaptcha_v2(SITE_KEY, PAGE_URL) or ''
    if tok:
        print(f'  [2captcha] token ok ({len(tok)}c) in {int(time.time() - t0)}s')
    else:
        print(f'  [2captcha] failed after {int(time.time() - t0)}s')
    return tok


def _curl(url, post=None, timeout=45):
    cmd = [CURL, '-s', '-m', str(timeout), '-A', UA, '-c', JAR, '-b', JAR, '-L',
           '-H', 'Accept: text/html,application/json,*/*;q=0.8', '-H', 'Accept-Language: en-US,en;q=0.9']
    if post is not None:
        cmd += ['-X', 'POST', '-H', 'Content-Type: application/x-www-form-urlencoded',
                '-H', 'X-Requested-With: XMLHttpRequest', '-H', 'Referer: ' + BASE + '/search/index']
        if post:
            for k, v in post:
                cmd += ['--data-urlencode', f'{k}={v}']
        else:
            cmd += ['-d', '']          # empty body — IIS 411s a POST with no Content-Length
    cmd += [url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout + 10)
        return r.stdout or ''
    except Exception:
        return ''


def start_session():
    """Landmark needs a session before any search page renders (else 'Session Has Expired')."""
    try: os.remove(JAR)
    except OSError: pass
    _curl(BASE + '/')
    _curl(BASE + '/Search/SetDisclaimer', post=[])              # empty POST (IIS wants a body)
    ok = 'Landmark' in _curl(BASE + '/search/index?theme=.blue&section=searchCriteriaName&quickSearchSelection=')
    return ok


def show_captcha():
    return 'true' in _curl(BASE + '/Search/ShowCaptcha', post=[]).lower()


def search(payload, token=''):
    """Fire a search POST. Returns the results HTML shell, or None when captcha-gated/blocked.

    Landmark does NOT embed rows in this HTML — #resultsTable is an empty DataTables shell.
    After a non-captcha response, call get_search_results() for the real JSON grid.
    """
    body = _curl(BASE + '/Search/' + payload[0], post=payload[1] + [('g-recaptcha-response', token)])
    if 'Invalid Captcha' in body:
        return None
    # A FAILED search returns an ASP.NET error page (~880 chars, "<title>Error</title>"), NOT an empty
    # result. Only 'Invalid Captcha' used to be checked, so that error page was returned as success —
    # and because get_search_results() pulls the grid in a SEPARATE request bound to session state, the
    # caller then received the rows from the PREVIOUS successful search. That is worse than a zero: the
    # sweep silently attributes one search's documents to another's parameters. It is what made four
    # different doctype values and two different endpoints all look "byte-identical" during the
    # 2026-08-15 Palm Beach investigation — those were stale reads, not proof the filter was ignored.
    # A real results shell always carries the DataTables container.
    if 'resultsTable' not in body:
        return None
    return body


def parcel_payload(pcn):
    return ('ParcelIdSearch', [('searchLikeType', '0'), ('parcelId', pcn), ('doctype', ''),
                               ('beginDate', '01/01/1900'), ('endDate', time.strftime('%m/%d/%Y')),
                               ('exclude', 'false'), ('ReturnIndexGroups', 'false'),
                               ('recordCount', '200'), ('townName', ''), ('mobileHomesOnly', 'false')])


def name_payload(name):
    return ('NameSearch', [('searchLikeType', '0'), ('type', '0'), ('name', name), ('doctype', ''),
                           ('bookType', '0'), ('beginDate', '01/01/1900'),
                           ('endDate', time.strftime('%m/%d/%Y')), ('recordCount', '200'),
                           ('exclude', 'false'), ('ReturnIndexGroups', 'false'), ('townName', ''),
                           ('selectedNamesIds', ''), ('includeNickNames', 'false'),
                           ('selectedNames', ''), ('mobileHomesOnly', 'false')])


# ---- results parsing ----------------------------------------------------------------------------
# Landmark flow (verified Capri 2026-07-27 via _capri_gsr_*.json):
#   1) POST /Search/ParcelIdSearch|NameSearch  → HTML shell (often "Returned 0 records" in the
#      DataTables i18n strings — that is NOT the live count)
#   2) POST /Search/GetSearchResults          → DataTables JSON {recordsTotal, data:[{0..30},…]}
# Column map (stable across parcel + name): 5 Direct, 6 Reverse, 7 RecordDate, 9 DocType,
# 10 BookType, 11 Book, 12 Page, 13 Instrument, 15 Legal/Case. Never invent rows.

def _cell(v):
    """Strip Landmark display prefixes + HTML from a GetSearchResults cell."""
    s = re.sub(r'<[^>]+>', ' ', str(v or ''))
    s = re.sub(r'^(nobreak_|legalfield_|hidden_|unclickable_)', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _js_ms(mmddyyyy):
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', (mmddyyyy or '').strip())
    if not m:
        return 0
    import datetime
    try:
        return int(datetime.datetime(int(m.group(3)), int(m.group(1)), int(m.group(2))).timestamp() * 1000)
    except Exception:
        return 0


def get_search_results(length=250, start=0):
    """DataTables server-side pull for the search just posted. Returns parsed JSON or None."""
    pairs = [('draw', '1'), ('start', str(start)), ('length', str(length)),
             ('search[value]', ''), ('search[regex]', 'false'),
             ('order[0][column]', '0'), ('order[0][dir]', 'desc')]
    for i in range(12):
        pairs += [(f'columns[{i}][data]', str(i)), (f'columns[{i}][name]', ''),
                  (f'columns[{i}][searchable]', 'true'), (f'columns[{i}][orderable]', 'true'),
                  (f'columns[{i}][search][value]', ''), (f'columns[{i}][search][regex]', 'false')]
    # Prefer JSON Accept so Landmark returns the grid body (same as browser XHR).
    cmd = [CURL, '-s', '-m', '60', '-A', UA, '-c', JAR, '-b', JAR, '-L',
           '-H', 'Accept: application/json, text/javascript, */*; q=0.01',
           '-H', 'X-Requested-With: XMLHttpRequest',
           '-H', 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8',
           '-H', 'Referer: ' + BASE + '/search/index']
    for k, v in pairs:
        cmd += ['--data-urlencode', f'{k}={v}']
    cmd += [BASE + '/Search/GetSearchResults']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=70)
        raw = r.stdout or ''
    except Exception:
        raw = ''
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        open(DUMP, 'w', encoding='utf-8').write(raw[:200000])
        return None


def _row_parties(row):
    """Return (direct, reverse, doctype, date, booktype, book, page, instrument, legal)."""
    return (
        _cell(row.get('5')), _cell(row.get('6')), _cell(row.get('9')).upper(),
        _cell(row.get('7')), _cell(row.get('10')), _cell(row.get('11')), _cell(row.get('12')),
        _cell(row.get('13')), _cell(row.get('15')),
    )


def _book_page(booktype, book, page):
    book = (book or '').lstrip('0') or (book or '')
    page = (page or '').lstrip('0') or (page or '')
    if not book and not page:
        return ''
    bt = (booktype or 'O').strip() or 'O'
    return f'{bt} {book}/{page}'.strip()


def _case_from_legal(legal):
    m = re.search(r'Case\s*Number\s*:\s*([A-Z0-9\-]+)', legal or '', re.I)
    return (m.group(1) if m else '').upper()


def gsr_rows_to_docs(rows):
    """Map Landmark GetSearchResults rows → broward_liens.analyze doc schema.

    Party rules (Landmark Direct/Reverse → AcclaimWeb FROM/CrossPartyName):
      MORTGAGE: Direct=borrower (Name/FROM), Reverse=lender (CrossPartyName)
      DEED:     Direct=grantor (Name/FROM), Reverse=grantee (CrossPartyName)
      LIEN / LIS PENDENS / JUDGMENT / NOTICE / CLAIM / CERT:
                Reverse=owner-side when present (Name/FROM), Direct=lienor/plaintiff (Cross)
      else:     Direct=Name/FROM, Reverse=Cross
    Also emit a TO-side mirror when Reverse looks like a distinct party (grantee/defendant
    also searchable). Never fabricate book/page/type — skip rows missing type+date.
    """
    docs = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        direct, reverse, dtype, dt, btype, book, page, inst, legal = _row_parties(row)
        if not dtype and not dt:
            continue
        ms = _js_ms(dt)
        bp = _book_page(btype, book, page)
        cn = _case_from_legal(legal)
        parcel = _cell(row.get('4'))
        # Only keep digit-ish parcel ids (name-search reuses col4 for the matched name).
        if parcel and not re.search(r'\d{8,}', re.sub(r'\D', '', parcel)):
            parcel = ''
        is_lienish = bool(re.match(
            r'^(LIEN|LIS\s*PEND|JUDGMENT|NOTICE|CLAIM|CERT|TAX\s*LIEN)', dtype or '', re.I))
        is_mtg = (dtype or '').upper().startswith('MORTGAGE')
        is_deed = (dtype or '').upper().startswith('DEED')

        def one(name, party, cross):
            if not (name or '').strip():
                return
            docs.append({
                'Name': (name or '').upper(),
                'Party': party,
                'CrossPartyName': (cross or '').upper(),
                'DocTypeDescription': (dtype or '').upper(),
                'Consideration': '',  # Landmark grid has no consideration column
                'RecordDate': f'/Date({ms})/' if ms else '',
                'BookPage': bp,
                'InstrumentNumber': inst,
                'CaseNumber': cn,
                'DocLegalDescription': legal,
                'ParcelNumber': parcel,
            })

        if is_lienish:
            # Defendant/owner sits on Reverse when present; fall back to Direct.
            owner_side = reverse or direct
            other = direct if reverse else ''
            one(owner_side, 'FROM', other or direct)
            if reverse and direct and reverse.upper() != direct.upper():
                one(direct, 'TO', reverse)
        elif is_mtg or is_deed:
            one(direct, 'FROM', reverse)
            if reverse and reverse.upper() != (direct or '').upper():
                one(reverse, 'TO', direct)
        else:
            one(direct or reverse, 'FROM', reverse if direct else '')
            if direct and reverse and reverse.upper() != direct.upper():
                one(reverse, 'TO', direct)
    return docs


def pb_chain_truth(res, docs):
    """Palm Beach chains are UNPRICED — say so instead of letting them read as clean.

    🔴 THE BUG THIS FIXES (measured 2026-08-17). Landmark's search grid has no consideration
    column, so gsr_rows_to_docs emits Consideration='' on every doc. broward_liens.analyze counts
    only mortgages with `_num(Consideration) > 0`, so it drops EVERY Palm Beach mortgage: liens[]
    comes back empty and junior/surv/surv_first/first_est all default to 0. Meanwhile conf stays
    'ok', because its guards (anchored + <=4 opens + <=45 records) all pass trivially on an empty
    set. Result: 0 of 109 PB scans had ever itemized a lien, and 80 of them displayed as a
    verified-clean chain. On Guzman (147 Kings Way, ~$235k of equity riding on it) that zero was
    reported as "0 open juniors" when nothing had been priced at all.

    We cannot invent amounts the county does not publish. What we CAN do is report the instruments
    honestly: how many mortgages are recorded against this owner, how many carry a matching
    satisfaction/release, and therefore how many are UNACCOUNTED FOR. An unaccounted mortgage is
    the operator's question to ask the owner — not a silent zero.
    """
    def _dt(d):
        return (d.get('DocTypeDescription') or '').upper()
    seen_m, seen_s = {}, []
    for d in docs or []:
        t, bp = _dt(d), (d.get('BookPage') or '').strip()
        if t.startswith('MORTGAGE'):
            if bp:
                seen_m[bp] = d
        elif re.match(r'^(SATISFACTION|SATIS|RELEASE|CANCELLATION|DISCHARGE)', t):
            seen_s.append(d)
    # A satisfaction names the instrument it kills in its own text. Match on the mortgage's
    # book/page digits appearing in the satisfaction's legal/case/instrument/cross-party fields.
    killed = set()
    for s in seen_s:
        blob = ' '.join(str(s.get(k) or '') for k in
                        ('DocLegalDescription', 'CaseNumber', 'InstrumentNumber', 'CrossPartyName'))
        blob_d = re.sub(r'\D', '', blob)
        for bp in seen_m:
            b = re.sub(r'\D', '', bp)
            if b and len(b) >= 6 and b in blob_d:
                killed.add(bp)
    res['mtg_recorded'] = len(seen_m)
    res['mtg_released'] = len(killed)
    res['mtg_open_unpriced'] = max(0, len(seen_m) - len(killed))
    # CONFIDENCE, HONESTLY. 'unpriced' is NOT 'ok' and is NOT 'none': we reached the records and we
    # know what instruments exist — we just cannot price them. Render as a question, never as clean.
    if seen_m and not (res.get('liens') or []):
        res['conf'] = 'unpriced'
        res['chain_note'] = ('%d mortgage(s) recorded / %d released / %d UNACCOUNTED. Palm Beach '
                             'records publish no dollar amounts, so no balance was computed. Ask '
                             'the owner what they still owe and to whom.'
                             % (len(seen_m), len(killed), res['mtg_open_unpriced']))
    elif not seen_m and not (res.get('liens') or []):
        res['conf'] = 'none'
        res['chain_note'] = ('No mortgage instrument found for this owner in the Palm Beach index '
                             '- the chain was NOT established. Do not read this as clear title.')
    return res


def parse_results(html_or_ignored=None):
    """Fetch + parse GetSearchResults for the active Landmark session.

    `html_or_ignored` kept for call-site compat (prior HTML-table parser). The shell HTML is
    dumped on hard failure so we can re-iterate without guessing.
    """
    j = get_search_results()
    if not j:
        if html_or_ignored:
            open(DUMP, 'w', encoding='utf-8').write(html_or_ignored if isinstance(html_or_ignored, str) else '')
        return None
    rows = j.get('data') or j.get('aaData') or []
    docs = gsr_rows_to_docs(rows)
    if not docs:
        # Persist raw JSON for parser iteration — do not invent rows.
        open(DUMP, 'w', encoding='utf-8').write(json.dumps(j, indent=1)[:200000])
        # Zero records is a valid empty hit (not a parse failure).
        if int(j.get('recordsTotal') or j.get('recordsFiltered') or 0) == 0 or not rows:
            return []
        return None
    return docs


def _bp_key(d):
    return re.sub(r'\s+', '', (d.get('BookPage') or '')).upper()


def _merge_docs(*groups):
    """Union doc lists by BookPage+Name+DocType; preserve first-seen order."""
    out, seen = [], set()
    for group in groups:
        for d in group or []:
            k = (_bp_key(d), (d.get('Name') or ''), (d.get('DocTypeDescription') or ''),
                 (d.get('Party') or ''))
            if k in seen:
                continue
            seen.add(k)
            out.append(d)
    return out


def _name_query(owner):
    """Landmark name box: 'LAST FIRST' (no comma). Drop middle initial — Landmark indexes
    without it (same lesson as broward_liens._search_name). Companies pass through."""
    raw = (owner or '').strip().upper()
    raw = re.sub(r'\s*&\s*[WH].*$', '', raw)
    raw = re.sub(r'\bH/[EW]\b|\bET\s?UX\b|\bET\s?AL\b|\bJR\b|\bSR\b|\bII+\b|\bTRS?\b', '', raw).strip()
    if re.search(r'\b(LLC|INC|CORP|LTD|LP|TRUST|ASSOC|ASSN)\b', raw):
        return raw
    if ',' in raw:
        last, _, rest = raw.partition(',')
        first = (rest.strip().split() or [''])[0]
        return f'{last.strip()} {first}'.strip()
    toks = raw.split()
    if len(toks) >= 2:
        return f'{toks[0]} {toks[1]}'  # LAST FIRST only — drop middle
    return raw


def _or_events(docs, limit=20, lead_case=''):
    """Compact recorded-event list for diligence timeline (no invented labels).

    Prefer lead-case rows and recent lien/LP/judgment/deed; demote old unrelated judgments
    so Capri-style LP/FJ/claim-of-lien are not crowded out of the window.
    """
    lc = re.sub(r'[^A-Z0-9]', '', (lead_case or '').upper())
    ev = []
    seen = set()
    for d in docs or []:
        dt = (d.get('DocTypeDescription') or '').upper()
        if not re.search(r'DEED|MORTGAGE|LIEN|LIS\s*PEND|JUDGMENT|CLAIM|NOTICE|CERT|SATISF|RELEASE', dt):
            continue
        if (d.get('Party') or '').upper() == 'TO' and not dt.startswith('DEED'):
            continue
        bp = d.get('BookPage') or ''
        key = (_bp_key(d), dt)
        if key in seen or not bp:
            continue
        seen.add(key)
        ms = 0
        m = re.search(r'/Date\((-?\d+)', d.get('RecordDate') or '')
        if m:
            ms = int(m.group(1))
        import datetime
        date_s = ''
        if ms:
            date_s = (datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=ms)).strftime('%m/%d/%Y')
        who = (d.get('CrossPartyName') or d.get('Name') or '')[:48]
        label = dt.title() if len(dt) < 40 else dt[:40]
        if who:
            label = f'{label} - {who}'
        cn = re.sub(r'[^A-Z0-9]', '', (d.get('CaseNumber') or '').upper())
        case_hit = bool(lc and cn and (lc in cn or cn in lc))
        # Score: case match >> recent lien/LP/FJ/deed >> everything else
        score = 0
        if case_hit:
            score += 1000
        if re.search(r'LIS\s*PEND|LIEN|JUDGMENT|CLAIM|DEED|MORTGAGE', dt):
            score += 50
        if re.search(r'JUDGMENT', dt) and not case_hit and ms and ms < 1577836800000:  # before 2020
            score -= 40
        score += min(ms // 10_000_000_000, 40)  # slight recency bump
        ev.append({'label': label, 'date': date_s, 'bp': bp, '_score': score, '_ms': ms})
    ev.sort(key=lambda x: (x.get('_score') or 0, x.get('_ms') or 0), reverse=True)
    out = []
    for e in ev[:limit]:
        e.pop('_score', None)
        e.pop('_ms', None)
        out.append(e)
    # Chronological for the brief
    def _dkey(e):
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', e.get('date') or '')
        if not m:
            return (0, 0, 0)
        return (int(m.group(3)), int(m.group(1)), int(m.group(2)))
    out.sort(key=_dkey)
    return out


# ---- headed bootstrap (human clicks ONE checkbox; we harvest the token) --------------------------
def harvest_token(timeout=120):
    """Open a visible browser on the search page; return the solved g-recaptcha-response the
    operator produces by clicking the checkbox once, or '' on timeout."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print('playwright unavailable — cannot bootstrap a token'); return ''
    print('>> browser open: click the "I\'m not a robot" checkbox ONCE (waiting up to 2 min)...')
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        pg = b.new_context(user_agent=UA).new_page()
        pg.goto(BASE + '/', wait_until='domcontentloaded', timeout=45000)
        pg.wait_for_timeout(1500)
        pg.evaluate("() => typeof SetDisclaimer === 'function' && SetDisclaimer()")
        pg.wait_for_timeout(2000)
        pg.goto(BASE + '/search/index?theme=.blue&section=searchCriteriaParcelId&quickSearchSelection=',
                wait_until='domcontentloaded', timeout=45000)
        token = ''
        t0 = time.time()
        while time.time() - t0 < timeout:
            token = pg.evaluate("() => (document.getElementById('g-recaptcha-response') || {}).value || ''")
            if token:
                break
            pg.wait_for_timeout(800)
        b.close()
    return token


def _obtain_token(headed=False, use_2captcha=True):
    """Prefer 2Captcha; fall back to headed human click only if requested and 2Captcha fails."""
    token = ''
    if use_2captcha:
        token = solve_token_2captcha()
    if token:
        return token, '2captcha'
    if headed:
        token = harvest_token()
        if token:
            return token, 'headed'
        print('ABORT: no token (2Captcha failed and headed checkbox never solved).')
        return '', ''
    print('CAPTCHA-GATED (ShowCaptcha=True). 2Captcha failed — re-run with --headed to click once.')
    return '', ''


def _search_docs(payload, token, headed=False, use_2captcha=True):
    """POST search + GetSearchResults. Returns (docs|None, fresh_token, token_src, status).

    status: 'ok' | 'captcha' | 'unparsed'
    docs=[] is a valid empty hit; docs=None means captcha/unparsed failure.
    """
    html = search(payload, token)
    tok, src = token, ''
    if html is None:
        tok, src = _obtain_token(headed=headed, use_2captcha=use_2captcha)
        if not tok:
            return None, '', src, 'captcha'
        html = search(payload, tok)
        if html is None:
            return None, '', src, 'captcha'
        tok = ''  # consumed
    docs = parse_results(html)
    if docs is None:
        return None, tok, src, 'unparsed'
    return docs, tok, src, 'ok'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--headed', action='store_true',
                    help='human-in-the-loop captcha fallback after 2Captcha fails')
    ap.add_argument('--no-2captcha', action='store_true',
                    help='skip 2Captcha; use --headed only when gated')
    ap.add_argument('--parse-dump', action='store_true',
                    help='re-parse _pb_last_results.html / JSON dump only (no network)')
    a = ap.parse_args()

    if a.parse_dump:
        raw = open(DUMP, encoding='utf-8', errors='replace').read() if os.path.exists(DUMP) else ''
        if not raw:
            print(f'no dump at {DUMP}'); return
        try:
            j = json.loads(raw)
            docs = gsr_rows_to_docs(j.get('data') or j.get('aaData') or [])
        except Exception:
            docs = parse_results(raw)
        print(f'parsed {0 if docs is None else len(docs)} docs from {os.path.basename(DUMP)}')
        for d in (docs or [])[:25]:
            print(f"  {(d.get('DocTypeDescription') or '')[:28]:28} {(d.get('BookPage') or '')[:18]:18} "
                  f"{(d.get('Name') or '')[:28]} / {(d.get('CrossPartyName') or '')[:28]}")
        return

    leads = json.load(open(LEADS, encoding='utf-8'))
    out = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    picked = []
    skipped = {}
    for r in leads:
        case = r.get('case', '') or ''
        if a.case and case != a.case: continue
        if not (r.get('folio') or '').strip() and not (r.get('owners') or '').strip(): continue
        if '(owner via title search)' in (r.get('owners') or ''): continue
        # A junk owner name is NOT a reason to drop a Palm Beach lead. Landmark is searched by
        # PARCEL first and the 17-digit PCN isolates the property exactly, so a lead like
        # '11750 SAINT ANDREWS PL APT 208' (PCN 73414414460052080) is fully traceable despite the
        # name being an address. Only skip when there is no usable PCN to fall back on — i.e. the
        # unusable name really was the only way in. Dropping it on the name alone would lose a
        # real lead, which is the trap the Miami-Dade version does not have to worry about.
        if not a.case:
            why = untraceable_owner(r.get('owners') or '')
            if why and len(re.sub(r'\D', '', r.get('folio', '') or '')) < 14:
                skipped.setdefault(why + ' and no PCN', []).append(r.get('owners') or '')
                continue
        if case in out and not (a.refresh or a.case): continue
        picked.append(r)
    if a.limit: picked = picked[:a.limit]
    print(f"{len(picked)} Palm Beach lead(s) to trace via Landmark Web (v2-captcha gated)")
    for why, names in sorted(skipped.items()):
        uniq = sorted(set(names))
        print(f"  skipped {len(names)} lead(s) — {why}: "
              + ', '.join(n[:34] for n in uniq[:3]) + (' ...' if len(uniq) > 3 else ''))
    if not picked:
        return

    if not start_session():
        print('ABORT: no Landmark session (site down / fingerprint block). Try again later.')
        return
    gated = show_captcha()
    token = ''
    token_src = ''
    if gated:
        token, token_src = _obtain_token(headed=a.headed, use_2captcha=not a.no_2captcha)
        if not token:
            return
        print(f'  captcha path: {token_src}')

    done = blocked = 0
    for r in picked:
        case = r.get('case', ''); owner = (r.get('owners') or '').strip()
        pcn = re.sub(r'\D', '', r.get('folio', '') or '')
        use_2c = not a.no_2captcha
        docs_parcel = docs_name = None

        if len(pcn) >= 14:
            docs_parcel, token, src, st = _search_docs(
                parcel_payload(pcn), token, headed=a.headed, use_2captcha=use_2c)
            if src:
                token_src = src
            if st == 'captcha':
                blocked += 1
                print(f"  --  {case:24} {owner[:26]:26} (captcha-gated parcel)")
                break
            if st == 'unparsed':
                blocked += 1
                print(f"  ??  {case:24} {owner[:26]:26} (unparsed parcel GSR -> {os.path.basename(DUMP)})")
                continue
            # Parcel alone misses LP/FJ/Lady Bird not indexed on PCN — supplement owner name.
            # Tokens are one-shot; re-solve when ShowCaptcha flips back on.
            token = ''
            # Do not spend a captcha on a name that cannot match anything. _name_query does not
            # filter — it would turn '11750 SAINT ANDREWS PL APT 208' into a search for
            # '11750 SAINT'. Tokens here are ONE-SHOT, so on a parcel+name lead this is the SECOND
            # solve of the same lead: measured 2026-08-22 on 502025CC018497XXXAWB, one v2 solve took
            # 67s, and the parcel leg still returned 18 raw / 9 events on its own. So the skip buys
            # back a full solve and ~a minute per affected lead, against a --limit 6 nightly.
            _junk = untraceable_owner(owner)
            if _junk:
                print(f"  ..  {case:24} name supplement skipped ({_junk}; parcel kept)")
                nq = ''
            else:
                nq = _name_query(owner)
            if nq:
                if show_captcha():
                    token, token_src = _obtain_token(headed=a.headed, use_2captcha=use_2c)
                    if not token:
                        print(f"  ..  {case:24} name supplement skipped (no captcha token; parcel kept)")
                        nq = ''
                if nq:
                    docs_name, token, src, st = _search_docs(
                        name_payload(nq), token, headed=a.headed, use_2captcha=use_2c)
                    if src:
                        token_src = src
                    if st == 'captcha':
                        print(f"  ..  {case:24} name supplement captcha-gated (parcel kept)")
                        docs_name = None
                    elif st == 'unparsed':
                        print(f"  ..  {case:24} name supplement unparsed (parcel kept)")
                        docs_name = None
        else:
            docs_name, token, src, st = _search_docs(
                name_payload(_name_query(owner) or owner), token,
                headed=a.headed, use_2captcha=use_2c)
            if src:
                token_src = src
            if st == 'captcha':
                blocked += 1
                print(f"  --  {case:24} {owner[:26]:26} (captcha-gated)")
                break
            if st == 'unparsed':
                blocked += 1
                print(f"  ??  {case:24} {owner[:26]:26} (unparsed results -> {os.path.basename(DUMP)})")
                continue

        docs = _merge_docs(docs_parcel, docs_name)
        if docs is None:
            blocked += 1
            continue
        ftype = r.get('ftype') or _fc_type_plaintiff(r.get('plaintiff', '')) or ''
        res = analyze(docs, owner, _num(r.get('judg')), ftype=ftype, lead_case=case)
        # Landmark publishes no dollar amounts -> analyze() drops every PB mortgage and its zeros
        # read as a clean chain. Restate what we actually know. See pb_chain_truth.
        res = pb_chain_truth(res, docs)
        res['traced'] = time.strftime('%Y-%m-%d'); res['owner'] = owner
        res['or_events'] = _or_events(docs, lead_case=case)
        res['n_raw'] = len(docs)
        if token_src:
            res['captcha'] = token_src
        out[case] = res
        done += 1
        flag = ''
        if res.get('deeded'): flag = f"  <-- TAKEN->{res['deeded']['grantee']}"
        elif res.get('second_fc'): flag = f"  <-- 2ND-FC {res['second_fc']['case']}"
        elif res.get('surv_first'): flag = f"  <-- surv 1st ~${res['surv_first']:,}"
        elif res.get('open_count', 0) >= 2 and res.get('junior'): flag = f"  <-- 2nd ~${res['junior']:,}"
        n_ev = len(res.get('or_events') or [])
        print(f"  ok  {case:24} {owner[:26]:26} {res['nrec']:>3} recs/{res['n_raw']} raw "
              f"{n_ev} events{flag} (conf {res['conf']})")
        json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
        token = ''                              # v2 tokens are one-shot
        if show_captcha():
            token, token_src = _obtain_token(headed=a.headed, use_2captcha=use_2c)
            if not token:
                print('session re-gated; stopping (no fresh token)')
                break
        time.sleep(0.6)
    print(f"\nDONE: {done} traced, {blocked} blocked/unparsed. -> palmbeach_liens.json")


if __name__ == '__main__':
    main()
