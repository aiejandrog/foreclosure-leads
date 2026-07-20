#!/usr/bin/env python3
"""palmbeach_liens.py — recorded-lien chains for Palm Beach leads via the clerk's Landmark Web
(erec.mypalmbeachclerk.com). Writes palmbeach_liens.json in the SAME schema broward_liens.json
uses, which make_tracker's county merge bakes into the deal-modal prefills automatically.

THE WALL: every search endpoint here is gated by reCAPTCHA v2 (the 304x78 checkbox widget —
sitekey 6LdBHOorAAAAALwRLkAZpnNsfcp7qfFS4YIGIRTU). There is NO programmatic mint like MD's v3
(verified 2026-07-20: NameSearch/ParcelIdSearch/QuickSearch all answer 'Invalid Captcha' without
a solved token). Two run modes:

  (default)  curl-only. Works on days ShowCaptcha flips False (site policy changes); otherwise
             traces nothing and says so — never silently.
  --headed   opens a VISIBLE browser once; the operator clicks the single 'I'm not a robot'
             checkbox. The script harvests the solved token from the page, fires searches, and
             re-checks ShowCaptcha: if the site then trusts the session, the rest of the book
             runs token-free over curl. If not, it re-prompts per search (one click per lead).

GHA note: the daily refresh CANNOT run this (no human on the runner) — PB traces happen on
Alejandro's machine via --headed. The actions/cache persists palmbeach_liens.json so results
flow into the daily builds once traced.

PRECISION: PARCEL search first (the lead's 17-digit PCN isolates the property exactly — no
namesakes, no sister-LLC blending); owner NAME search only when the lead has no folio.
The chain analysis itself is broward_liens.analyze — the same guarded satisfaction matching
(book/page -> assignee-chain -> lender-window -> refi-kill) as every other county.
"""
import argparse, json, os, re, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from broward_liens import analyze, _fc_type_plaintiff, _num   # the shared chain engine

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'palmbeach_leads.json')
OUT = os.path.join(HERE, 'palmbeach_liens.json')
BASE = 'https://erec.mypalmbeachclerk.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
JAR = os.path.join(tempfile.gettempdir(), 'pb_liens_cookies.txt')
CURL = r'C:\Windows\System32\curl.exe' if os.name == 'nt' and os.path.exists(r'C:\Windows\System32\curl.exe') else 'curl'
DUMP = os.path.join(HERE, '_pb_last_results.html')             # raw first-success dump (parser iteration aid)


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
    """Fire a search POST. Returns the results HTML, or None when captcha-gated/blocked."""
    body = _curl(BASE + '/Search/' + payload[0], post=payload[1] + [('g-recaptcha-response', token)])
    if 'Invalid Captcha' in body:
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
# UNVERIFIED against a live success (the v2 wall has never let us see one). Landmark renders its
# grid server-side; rows carry doc type / record date / parties / consideration / book-page or
# instrument. We map columns by header text and dump the raw HTML on any uncertainty so the first
# --headed run self-instruments (iterate the parser on _pb_last_results.html, never guess).
def parse_results(html):
    if not html:
        return None
    m = re.search(r'<table[^>]*>(.*?)</table>', html, re.S | re.I)
    if not m:
        open(DUMP, 'w', encoding='utf-8').write(html)
        return None
    tbl = m.group(1)
    heads = [re.sub(r'<[^>]+>|\s+', ' ', h).strip().lower()
             for h in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', tbl.split('</tr>')[0], re.S | re.I)]
    def col(*names):
        for i, h in enumerate(heads):
            if any(n in h for n in names):
                return i
        return -1
    ci = {'type': col('type', 'document'), 'date': col('date', 'record'), 'name': col('name', 'party', 'grantor'),
          'amt': col('consider', 'amount'), 'bp': col('book', 'page', 'instrument')}
    docs = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, re.S | re.I)[1:]:
        cells = [re.sub(r'<[^>]+>|\s+', ' ', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S | re.I)]
        if len(cells) < 3:
            continue
        def get(k): return cells[ci[k]] if 0 <= ci[k] < len(cells) else ''
        dt = get('date')
        mdt = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', dt)
        ms = 0
        if mdt:
            import datetime
            ms = int(datetime.datetime(int(mdt.group(3)), int(mdt.group(1)), int(mdt.group(2))).timestamp() * 1000)
        docs.append({'Name': (get('name') or '').upper(), 'DocTypeDescription': (get('type') or '').upper(),
                     'Consideration': get('amt').replace('$', '').replace(',', ''),
                     'RecordDate': f'/Date({ms})/' if ms else '', 'Party': 'FROM', 'BookPage': get('bp')})
    if not docs:
        open(DUMP, 'w', encoding='utf-8').write(html)
        return None
    return docs


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--headed', action='store_true', help='human-in-the-loop captcha bootstrap')
    a = ap.parse_args()

    leads = json.load(open(LEADS, encoding='utf-8'))
    out = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    picked = []
    for r in leads:
        case = r.get('case', '') or ''
        if a.case and case != a.case: continue
        if not (r.get('folio') or '').strip() and not (r.get('owners') or '').strip(): continue
        if '(owner via title search)' in (r.get('owners') or ''): continue
        if case in out and not (a.refresh or a.case): continue
        picked.append(r)
    if a.limit: picked = picked[:a.limit]
    print(f"{len(picked)} Palm Beach lead(s) to trace via Landmark Web (v2-captcha gated)")
    if not picked:
        return

    if not start_session():
        print('ABORT: no Landmark session (site down / fingerprint block). Try again later.')
        return
    gated = show_captcha()
    token = ''
    if gated:
        if not a.headed:
            print('CAPTCHA-GATED today (ShowCaptcha=True). Re-run with --headed and click the one checkbox.')
            return
        token = harvest_token()
        if not token:
            print('ABORT: no token harvested (checkbox never solved).')
            return
        gated = False    # we have a live token; re-checked after the first search

    done = blocked = 0
    for r in picked:
        case = r.get('case', ''); owner = (r.get('owners') or '').strip()
        pcn = re.sub(r'\D', '', r.get('folio', '') or '')
        payload = parcel_payload(pcn) if len(pcn) >= 14 else name_payload(owner)
        html = search(payload, token)
        if html is None:
            blocked += 1
            print(f"  --  {case:24} {owner[:26]:26} (captcha-gated)")
            if a.headed:
                token = harvest_token()         # one more click, keep going
                if not token: break
            else:
                break
            continue
        docs = parse_results(html)
        if docs is None:
            blocked += 1
            print(f"  ??  {case:24} {owner[:26]:26} (unparsed results -> {os.path.basename(DUMP)})")
            continue
        ftype = r.get('ftype') or _fc_type_plaintiff(r.get('plaintiff', '')) or ''
        res = analyze(docs, owner, _num(r.get('judg')), ftype=ftype, lead_case=case)
        res['traced'] = time.strftime('%Y-%m-%d'); res['owner'] = owner
        out[case] = res
        done += 1
        flag = ''
        if res.get('deeded'): flag = f"  <-- TAKEN->{res['deeded']['grantee']}"
        elif res.get('second_fc'): flag = f"  <-- 2ND-FC {res['second_fc']['case']}"
        elif res.get('surv_first'): flag = f"  <-- surv 1st ~${res['surv_first']:,}"
        elif res.get('open_count', 0) >= 2 and res.get('junior'): flag = f"  <-- 2nd ~${res['junior']:,}"
        print(f"  ok  {case:24} {owner[:26]:26} {res['nrec']:>3} recs{flag} (conf {res['conf']})")
        json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
        token = ''                              # v2 tokens are one-shot
        if show_captcha():
            if a.headed:
                token = harvest_token()
                if not token: break
            else:
                print('session re-gated; stopping the curl batch (use --headed)')
                break
        time.sleep(0.6)
    print(f"\nDONE: {done} traced, {blocked} blocked/unparsed. -> palmbeach_liens.json")


if __name__ == '__main__':
    main()
