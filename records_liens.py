"""Pillar 1 — automate the recorded mortgage/lien pull so the equity number stops being a guess.

For each lead's owner, pull the Miami-Dade Official Records chain, match SATISFACTION docs to their
MORTGAGE, and surface the OPEN (unsatisfied) mortgages on the subject folio — i.e. the hidden 2nd that
made Hondroulis's "$655k equity" a fantasy. Output -> records_liens.json (gitignored), keyed by Case #.

Reliability trick: the gated part is only the *search* (standardsearch POST). The RESULTS fetch
(getStandardRecords GET) is NOT gated, so a valid search token (qs) is the entire cost of an owner.

TOKEN SOURCES, in the order tried (2026-08-22):

  1. CACHED qs      records_qs.json — plain requests, no browser, free and instant.
  2. CAMOUFOX       drives the real search UI; Turnstile runs invisible/managed here and hands an
                    anti-detect browser a token unprompted. FREE. Measured 4/4 (CAMOUFOX-EVAL.md);
                    vanilla headless chromium gets nothing on the identical flow, so this is not
                    "any browser works". ~17s per owner. The captured qs is written back to
                    records_qs.json, so each owner costs that once and lands on path 1 afterwards.
  3. 2CAPTCHA       fetch_via_turnstile — ~$0.003 and ~6s per solve. Faster than Camoufox but not
                    free. Kept as the fallback for the day the county stops being generous, and
                    reachable directly with --no-camoufox.
  4. mint_and_fetch legacy Playwright reCAPTCHA-v3 mint. Effectively dead — the site migrated off
                    the sitekey it was built for — and skipped silently when its JS template is gone.

So the trade is time for money: Camoufox is ~3x slower per uncached owner than a 2Captcha solve, but
costs nothing and compounds into the cache. On a backlog that matters; once records_qs.json is warm,
most owners never reach step 2 at all.

Usage:
  python records_liens.py --case 2024-023366-CA-01     # one lead (prove it)
  python records_liens.py --tier A                      # a tier
  python records_liens.py --all --cached-only           # everyone we already have a token for (fast, no browser)
  python records_liens.py --all                         # everyone; free Camoufox tokens for the rest
  python records_liens.py --all --no-camoufox           # skip Camoufox, buy tokens from 2Captcha
"""
import argparse, datetime, json, os, re, time, urllib.parse


def _parse_recd(s):
    """Parse a Miami-Dade recorded-date string into a real date.

    The clerk emits `M/D/YYYY` (with an occasional trailing time slice, e.g. '2/1/2002 1'), which
    means string comparison `'1/10/2006' >= '10/31/2006'` is True — the day-in-January reads as
    NEWER than the day-in-October and reversed classifications of every lien pair whose months
    started with different digits. Return None on unreadable input so a comparison with a real date
    is False either way; callers must guard on both operands existing before ordering.
    """
    parts = (s or '').split()
    s = parts[0].strip() if parts else ''
    if not s:
        return None
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m-%d-%Y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
QS_CACHE = os.path.join(HERE, 'records_qs.json')      # owner_clean -> search token (from gen_records_qs.py)
OUT = os.path.join(HERE, 'records_liens.json')         # Case # -> lien result  (gitignored)
OR_BASE = 'https://onlineservices.miamidadeclerk.gov/officialrecords/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|USA|UNITED STATES|COUNTY|CITY OF)\b', re.I)
SITE_KEY = '6LfI8ikaAAAAAH0qlQMApskMGd1U6EqDyniH5t0x'   # legacy reCAPTCHA (DEAD — site migrated)
# Miami-Dade Official Records migrated from reCAPTCHA v3 to Cloudflare TURNSTILE (running in
# reCAPTCHA-compat mode, so the token still rides the x-recaptcha-token header). This is the live
# Turnstile sitekey; 2Captcha solves it in ~6s and the search accepts it. Verified 2026-07-21.
TS_SITE_KEY = '0x4AAAAAAD1vWBs-1bsZ5Z5M'

S = requests.Session()
S.headers.update({'User-Agent': UA, 'Accept': 'application/json', 'Referer': OR_BASE})


def norm_folio(s):
    return re.sub(r'\D', '', str(s or '')).lstrip('0')

def num(x):
    try: return float(x or 0)
    except Exception: return 0

def split_owner(clean):
    # kimi: companies have no LAST/FIRST — pass the full name as one partyName token string
    # (the clerk search tokenizes it). analyze() isolates by folio+subdivision, never by name.
    if COMPANY_RE.search(clean or ''):
        return (clean.strip(), '')
    toks = [t for t in (clean or '').split() if len(t.strip('.')) > 1]
    if len(toks) < 2:
        return None
    # MD owner_clean is FIRST [MIDDLE] LAST. The clerk indexes SURNAME-FIRST, and its name search is
    # order-sensitive: for "MARIE FLORETTE FLEURIMOND" the old ' '.join(toks[1:]) made the surname
    # "FLORETTE FLEURIMOND" and returned ZERO — searching the true surname (last token) first
    # returned 120 docs. So: surname = LAST token, given = everything before it. (2-token names are
    # unchanged: "EDUARDO ECHEVERRI" -> surname ECHEVERRI, given EDUARDO.)
    return (toks[-1], ' '.join(toks[:-1]))   # (SURNAME, GIVEN)


# A government body or a bare street address is not a party whose mortgage chain means anything.
# Both still cost a mint attempt every run and both come back empty — measured on this board:
# CITY OF NORTH MIAMI BEACH FLORIDA, STATE OF FLORIDA'S DEPARTMENT OF REVENUE and
# UNITED STATES OF AMERICA - DEPARTMENT OF HOUSING all traced to 0 open mtg / conf=None, and
# '10867 NW 59TH ST DORAL FL 33178' never resolved at all.
#
# THE TRAP, and why this is deliberately narrow: most address-shaped owners on this board are REAL
# entities named after their address — 5838 ALTON ROAD LLC, 13925 OLD CUTLER ROAD LLC,
# 6828 NW 3 AVE LLC — and every one of the 20 owners containing digits is a live company. Companies
# are worth tracing: 63 of them have been traced and 8 came back carrying an open mortgage. So a
# company suffix ALWAYS wins; the address rule only fires on a name that has no entity marker at all.
COMPANY_SUFFIX_RE = re.compile(
    r'\b(LLC|L\.L\.C|CORP|INC|TRUST|ASSOC|ASSN|COMPANY|HOLDINGS|GROUP|PARTNERS|VENTURES'
    r'|INVESTMENTS|PROPERTIES|REALTY|MANAGEMENT|LP|LLP|LTD|PA|PLLC)\b', re.I)
GOV_RE = re.compile(
    r'(\bCITY OF\b|\bSTATE OF\b|\bCOUNTY OF\b|\bUNITED STATES\b|\bDEPARTMENT OF\b|\bDEPT OF\b'
    r'|\bCOUNTY\s*$|\bTAX COLLECTOR\b|\bCLERK OF\b|\bSHERIFF\b)', re.I)
STREET_RE = re.compile(
    r'\b(ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|BLVD|CT|COURT|LN|LANE|WAY|TER|TERR|PL|PLACE'
    r'|HWY|CIR|CIRCLE|PKWY|APT|UNIT|STE)\b', re.I)


def untraceable_owner(clean):
    """Reason string when this owner_clean is not worth a search token, else ''.

    Checked BEFORE a lead is picked, so junk never reaches a mint — free or paid.
    """
    s = (clean or '').strip()
    if not s:
        return 'empty'
    if COMPANY_SUFFIX_RE.search(s):
        return ''                                   # a company is a real party — always trace it
    if GOV_RE.search(s):
        return 'government body'
    # bare street address: starts with a house number AND carries a street word, no entity marker
    if re.match(r'^\d', s) and STREET_RE.search(s):
        return 'street address, not a name'
    return ''


# ---- fetch the owner's recorded documents -----------------------------------------------------
def records_by_qs(qs):
    try:
        r = S.get(OR_BASE + 'api/SearchResults/getStandardRecords?qs=' + qs, timeout=30)
        if r.status_code != 200:
            return None
        return (r.json() or {}).get('recordingModels') or []
    except Exception:
        return None


def fetch_via_turnstile(owner_lf, tries=3):
    """THE UNLOCK — pull an owner's recorded docs by solving Cloudflare Turnstile (2Captcha), no
    browser. Solves the token, POSTs the same standardsearch the app uses with it in the
    x-recaptcha-token header, then the (ungated) getStandardRecords GET. Returns models or None.
    owner_lf = (LAST..., FIRST) or (COMPANY, '')."""
    try:
        from captcha_solver import solve_turnstile
    except Exception:
        return None
    # THE MD CLERK ACCEPTS ONE WORD. Any name with a space (`WHITE SHUROD`) or comma (`WHITE, SHUROD`)
    # returns `{isValidSearch:false, qs:null}` with the token accepted. Verified with a $12 wallet
    # and a fresh solve per test: only `WHITE` alone succeeded. That is why 41 of 43 leads couldn't
    # be pulled today. Submit the last name only; the downstream analyzer already isolates by folio,
    # so a broad last-name pool is safe. Companies stay whole (owner_lf[1] is empty for them).
    party = owner_lf[0].strip()
    url = (OR_BASE + 'api/home/standardsearch?partyName=' + urllib.parse.quote(party)
           + '&dateRangeFrom=&dateRangeTo=&documentType=&searchT=&firstQuery=y&searchtype='
           + urllib.parse.quote('Name/Document'))
    for _ in range(max(1, tries)):
        tok = solve_turnstile(TS_SITE_KEY, OR_BASE)
        if not tok:
            continue
        try:
            r = S.post(url, headers={'x-recaptcha-token': tok,
                                     'content-type': 'application/json; charset=utf-8'},
                       data='', timeout=30)
            j = r.json()
        except Exception:
            continue
        qs = j.get('qs') if isinstance(j, dict) else None
        if qs:
            return records_by_qs(qs)
        # isValidSearch:false with a fresh token = a bad solve; loop and re-solve
        time.sleep(1)
    return None

# ---- Camoufox: let the browser mint its own Turnstile token, for free ------------------------
# Measured 2026-08-22 (CAMOUFOX-EVAL.md): Turnstile runs invisible/managed on this site, so it hands
# a browser it considers legitimate a token with no interaction. Camoufox got one on 4/4 trials
# (666-688 chars) and pulled 42 records for HONDROULIS. Vanilla headless chromium got NOTHING on the
# same flow — twice the challenges.cloudflare.com traffic and an empty x-recaptcha-token — so this is
# specific to the anti-detect build, not "any browser works now".
#
# It has to drive the real UI. POSTing api/home/standardsearch cold does not work: Turnstile only
# executes as part of a search interaction, so on a freshly loaded page there is no widget and no
# token. The search box itself lives behind the sidebar's Standard Search -> Name/Document.
#
# We do not read the rendered results. We capture the `qs` off the app's OWN getStandardRecords
# request and hand it to records_by_qs(), so every existing parser downstream is untouched — and the
# qs gets cached, which puts the next run on the free plain-requests path.

CF_UNAVAILABLE = None          # set to a reason string once, so we do not retry a missing import


def camoufox_session():
    """One browser for a whole batch. Launching per-owner would dominate the runtime at --limit 60."""
    global CF_UNAVAILABLE
    if CF_UNAVAILABLE:
        return None, None
    try:
        from camoufox.sync_api import Camoufox
    except Exception as e:
        CF_UNAVAILABLE = 'camoufox not installed (%s)' % type(e).__name__
        return None, None
    try:
        cm = Camoufox(headless=True, geoip=True, humanize=True)
        return cm, cm.__enter__()
    except Exception as e:
        CF_UNAVAILABLE = 'camoufox failed to launch: %s' % str(e)[:90]
        return None, None


def camoufox_qs(browser, party, settle=9000):
    """Run one search in the real UI and return the `qs` the county issued, or None.

    party is the LAST NAME ONLY — same rule fetch_via_turnstile documents: the clerk answers
    isValidSearch:false for anything with a space or comma in it.
    """
    page = browser.new_page()
    grabbed = {}

    def on_req(r):
        if 'getStandardRecords' in r.url and 'qs=' in r.url:
            grabbed.setdefault('qs', urllib.parse.unquote(r.url.split('qs=', 1)[1].split('&')[0]))

    page.on('request', on_req)
    try:
        page.goto(OR_BASE, wait_until='domcontentloaded', timeout=60000)
        try:
            page.wait_for_load_state('networkidle', timeout=25000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        for sel in ('text=Name/Document', 'a:has-text("Name/Document")'):
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.click(timeout=8000)
                    break
            except Exception:
                continue
        page.wait_for_timeout(2500)

        box = None
        for sel in ('#lastName', 'input[name="lastName"]', 'input[placeholder*="Last" i]'):
            try:
                if page.locator(sel).count():
                    box = page.locator(sel).first
                    break
            except Exception:
                continue
        if box is None:
            return None
        box.fill(party)
        page.wait_for_timeout(600)

        for sel in ('button[type="submit"]', 'button:has-text("SEARCH")', 'button:has-text("Search")'):
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.click(timeout=8000)
                    break
            except Exception:
                continue

        # Poll rather than one long sleep — the qs usually lands in 3-5s and there is no reason to
        # pay the worst case on every owner.
        for _ in range(int(settle / 750)):
            if grabbed.get('qs'):
                break
            page.wait_for_timeout(750)
        return grabbed.get('qs')
    except Exception:
        return None
    finally:
        try:
            page.remove_listener('request', on_req)
            page.close()
        except Exception:
            pass


def mint_and_fetch(owner_lf, budget=70, persist=False):
    """Mint a fresh reCAPTCHA token in a browser, then fetch. Defaults to a bounded 3-try attempt.

    persist=True mode: 'never give up' — keeps trying with widening back-off (10s -> 20s -> 40s ->
    60s cap, capped at 25 attempts, ~15-20 min max). Each attempt spins up a fresh browser context
    so the site's per-context rate-limits reset. For a specific lead the operator has flagged as
    important enough to burn time on (Furs, Echeverri) — worth it. Do NOT use inside a batch loop.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    src = open(os.path.join(HERE, 'gen_records_qs.py'), encoding='utf-8').read()
    js = re.search(r'JS = r"""(.*?)"""', src, re.S).group(1).replace('SITEKEY', SITE_KEY)
    if persist:
        # keep hammering until the captcha yields or the retry cap is hit
        attempt = 0
        while attempt < int(os.environ.get('MINT_ATTEMPTS', 25)):
            attempt += 1
            try:
                with sync_playwright() as p:
                    b = p.chromium.launch(headless=True)
                    pg = b.new_context(user_agent=UA, viewport={'width': 1400, 'height': 1000}).new_page()
                    pg.goto(OR_BASE, timeout=40000, wait_until='domcontentloaded')
                    pg.wait_for_timeout(4000 + attempt * 500)   # give the site more settle each try
                    res = pg.evaluate(js, list(owner_lf))
                    b.close()
                    if res and res.get('success') and res.get('qs'):
                        print(f'  mint OK on attempt {attempt}')
                        return records_by_qs(res['qs'])
                    print(f'  mint attempt {attempt} failed — {res.get("error") if res else "no response"}')
            except Exception as e:
                print(f'  mint attempt {attempt} threw: {str(e)[:80]}')
            back = min(60, 10 * (1.4 ** min(attempt, 10)))
            print(f'  backing off {int(back)}s before attempt {attempt + 1}...')
            time.sleep(back)
        print(f'  gave up after {attempt} attempts (captcha remained hostile)')
        return None
    # legacy bounded behaviour (unchanged)
    t0 = time.time()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_context(user_agent=UA, viewport={'width': 1400, 'height': 1000}).new_page()
            res = None
            for _ in range(3):
                if time.time() - t0 > budget: break
                try:
                    pg.goto(OR_BASE, timeout=40000, wait_until='domcontentloaded'); pg.wait_for_timeout(3000)
                    res = pg.evaluate(js, list(owner_lf))
                    if res and res.get('success') and res.get('qs'): break
                except Exception:
                    pg.wait_for_timeout(2500)
            b.close()
        if res and res.get('success') and res.get('qs'):
            return records_by_qs(res['qs'])
    except Exception:
        pass
    return None


# ---- parse the chain: open vs satisfied, isolate the surviving junior --------------------------
def _fc_type(case):
    """HOA/county-court (whole 1st mortgage survives) vs circuit mortgage foreclosure. Miami-Dade case format
    uses -CA- (circuit) / -CC- (county); also handle the Broward-style CACE/COCE prefixes defensively."""
    c = (case or '').upper()
    if '-CA-' in c or c.startswith('CACE'): return 'MORTGAGE'
    if '-CC-' in c or c.startswith(('COCE', 'CONO', 'COWE', 'COSO')): return 'HOA'
    return ''


def _inst(s):
    """Normalize a lender/institution name for satisfaction<->mortgage matching."""
    s = (s or '').upper()
    s = re.sub(r'\b(NA|N A|NATIONAL ASSN|NATIONAL ASSOCIATION|FSB|FA|INC|CORP|CO|LLC|LP|USA|'
               r'TRUST COMPANY|MTGE|MORTGAGE|GROUP|GRP|SVGS|SAVINGS|HOME LOANS?|FINANCIAL|SERVICES?|BANK)\b', '', s)
    return re.sub(r'[^A-Z]', '', s)


def analyze(models, folio, judgment, ftype=''):
    """Open-mortgage picture for the SUBJECT parcel only. Precision > recall: without a folio to isolate
    by, we return nothing rather than risk a namesake's mortgages polluting the number.
    ftype='HOA' means the whole first mortgage survives the sale (surface `surv`), not just a 2nd."""
    fol = norm_folio(folio)
    if not fol:
        return {'liens': [], 'open_count': 0, 'junior': 0, 'first_est': 0, 'surv': 0, 'surv_first': 0,
                'ftype': ftype, 'conf': 'none'}
    # ANCHOR the subject's subdivision from a record that DOES carry the subject folio (usually the deed).
    # folio is blank on most newer mortgages, but subdivision is consistent — so subdivision + owner-name
    # isolates the property, while folio alone would drop the very mortgages we need.
    subj_subdiv = ''
    for r in models:
        if norm_folio(r.get('foliO_NUMBER', '')) == fol:
            sd = (r.get('subdiV_NAME', '') or '').strip().upper()
            if sd: subj_subdiv = sd; break
    # a MORTGAGE is satisfied if a SATISFACTION points at its book/page
    satisfied = set()
    for r in models:
        if 'SATISFACTION' in (r.get('doC_TYPE', '') or '').upper():
            satisfied.add((str(r.get('oriG_REC_BOOK', '')).strip(), str(r.get('oriG_REC_PAGE', '')).strip()))
    def sortkey(r):
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', (r.get('reC_DATE', '') or '').strip())
        return (m.group(3), m.group(1).zfill(2), m.group(2).zfill(2)) if m else ('0000', '00', '00')
    liens, opens = [], []
    for r in sorted(models, key=sortkey):
        if not (r.get('doC_TYPE', '') or '').upper().startswith('MORTGAGE'):
            continue
        rf = norm_folio(r.get('foliO_NUMBER', ''))
        sd = (r.get('subdiV_NAME', '') or '').strip().upper()
        if not ((rf and rf == fol) or (subj_subdiv and sd == subj_subdiv)):
            continue                                   # subject parcel only (folio when present, else subdivision)
        it, cons = num(r.get('intangible')), num(r.get('consideratioN_1'))
        amt = round(it / 0.002) if it > 0 else round(cons)
        if amt <= 0:
            continue                                   # $0 doc = modification/piggyback placeholder, not a real balance
        bp = (str(r.get('reC_BOOK', '')).strip(), str(r.get('reC_PAGE', '')).strip())
        is_open = bp not in satisfied
        row = {'d': (r.get('reC_DATE', '') or '')[:10], 'amt': amt, 'party': (r.get('seconD_PARTY', '') or '')[:40],
               'bp': r.get('reC_BOOKPAGE', ''), 'st': 'OPEN' if is_open else 'SATISFIED',
               '_dt': '-'.join(sortkey(r)), '_lend': _inst(r.get('seconD_PARTY'))}
        liens.append(row)
        if is_open:
            opens.append(row)
    # --- kimi: layered fallback for still-open mortgages (MD) -------------------------------------
    # The book/page match is direct but blind when a satisfaction leaves oriG_* empty (common on
    # assignee-recorded releases). Layered: release by lender OR its recorded assignee; then any
    # LENDER-party release within 24 months; then refi-kill (newer different-lender mortgage
    # >=70% of balance within 36 months).
    _LENDER_RE = re.compile(r'BANK|MORTGAGE|MTGE|LOAN|FINANC|SAVING|CREDIT|FUNDING|SERVICING|FEDERAL|NATIONAL', re.I)
    def _months(a, b):
        try: return (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:7]) - int(a[5:7]))
        except Exception: return 99
    sats2 = [r for r in models if re.search(r'SATISF|RELEASE', (r.get('doC_TYPE', '') or '').upper())]
    for r in sats2: r['_dt'] = '-'.join(sortkey(r))
    assigns = [r for r in models if 'ASSIGNMENT' in (r.get('doC_TYPE', '') or '').upper()]
    for r in assigns: r['_dt'] = '-'.join(sortkey(r))
    # CORRECTNESS: build a per-lender assignment GRAPH (from -> set(to)) and BFS the chain that starts
    # at the mortgage's own lender. Was: _assignees pooled EVERY assignee of every assignment together
    # and returned them regardless of the input `lend`, so a release by an unrelated bank's assignee
    # (chain B) could silently mark chain A's mortgage SATISFIED — the exact "phantom survivor -> false
    # equity" failure the tool exists to catch. Mirrors broward_liens.chain_of. Uses firsT_PARTY as the
    # assignor (which the MD Records API records as the current holder BEFORE the assignment) and
    # seconD_PARTY as the new holder.
    assigngraph = {}
    for a in assigns:
        fr = _inst(a.get('firsT_PARTY')); to = _inst(a.get('seconD_PARTY'))
        if fr and to and fr != to:
            assigngraph.setdefault(fr, set()).add(to)
    def chain_of(lend, after):
        if not lend: return {lend}
        out, frontier, seen = {lend}, [lend], set()
        while frontier:
            cur = frontier.pop()
            if cur in seen: continue
            seen.add(cur)
            for nxt in assigngraph.get(cur, ()):
                if nxt not in out:
                    out.add(nxt); frontier.append(nxt)
        return out
    for o in liens:
        if o['st'] == 'SATISFIED':
            continue
        chain = chain_of(o['_lend'], o['_dt'])
        for s in sats2:
            if not s['_dt'] or s['_dt'] < o['_dt']: continue
            si = _inst(s.get('seconD_PARTY'))
            if si and si in chain:
                o['st'] = 'SATISFIED'; break
    opens = [o for o in liens if o['st'] == 'OPEN']
    # rule 2: a LENDER-party release kills the NEWEST still-open mortgage recorded 3-24 months
    # before it. The 3-month floor is the Echeverri guard: a release dated weeks after a loan was
    # written belongs to an OLDER loan in the chain, never to the new one — so same-year misfires
    # (Echeverri's real New Century senior) can't be killed by a different loan's satisfaction.
    for s in sorted(sats2, key=lambda x: x['_dt']):
        if not (s['_dt'] and _LENDER_RE.search(s.get('seconD_PARTY') or '')): continue
        prior = [o for o in opens if o['_dt'] < s['_dt'] and 3 <= _months(o['_dt'], s['_dt'][:10]) <= 24]
        if prior:
            newest = max(prior, key=lambda o: o['_dt'])
            newest['st'] = 'SATISFIED'
            opens = [o for o in opens if o is not newest]
    # rule 3: refi-kill ONLY in true-refi shape — newer different-lender mortgage >=90% of the
    # older balance within 24 months (a junior second is usually far smaller, so it can't pose as one)
    for o in liens:
        if o['st'] != 'OPEN': continue
        chain3 = chain_of(o['_lend'], o['_dt'])
        for m2 in liens:
            if m2 is o or m2['_dt'] <= o['_dt']: continue
            if (_months(o['_dt'], m2['_dt']) <= 24 and m2['amt'] >= o['amt'] * 0.9
                    and m2['_lend'] != o['_lend'] and m2['_lend'] not in chain3):
                o['st'] = 'SATISFIED'; break
    opens = [o for o in liens if o['st'] == 'OPEN']
    junior = first_amt = surv = surv_first = 0
    juniors_post = 0
    if opens:
        if ftype == 'HOA':                             # HOA sale: the WHOLE first mortgage survives
            surv = sum(o['amt'] for o in opens)
            surv_first = max(o['amt'] for o in opens)
        else:
            anchor = (lambda o: abs(o['amt'] - judgment)) if (judgment and judgment > 0) else (lambda o: -o['amt'])
            fore = min(opens, key=anchor)              # the foreclosing 1st (closest to judgment, else largest)
            first_amt = fore['amt']
            junior = surv = sum(o['amt'] for o in opens if o is not fore)
            # DATES COMPARE AS DATES. `o['d']` is 'M/D/YYYY' straight from the clerk — '1/10/2006'
            # sorts lexically ABOVE '10/31/2006', so string comparison silently classified 1-Jan
            # loans as "recorded AFTER" 10-Oct loans and swapped seniors with juniors in the
            # reconciliation the browser trusts. Falling back to the raw string only when parsing
            # fails means an unreadable date can never claim to be newer than a real one.
            fd = _parse_recd(fore['d'])
            juniors_post = sum(o['amt'] for o in opens if o is not fore and _parse_recd(o['d']) and fd and _parse_recd(o['d']) >= fd)
    # --- open non-mortgage liens (kimi: feeds the deal-modal HOA / code / IRS prefills) ------------
    # Lien/Judgment/Notice records, bucketed by holder. code+HOA require the same parcel isolation the
    # mortgages use (folio/subdivision); IRS + money judgments attach to the person and ride anyway.
    _IRS_RE = re.compile(r'INTERNAL\s+REV|UNITED\s+STATES|\bIRS\b', re.I)
    _CODE_RE = re.compile(r'\bCITY\s+OF\b|\bCOUNTY\b|CODE\s+ENFORCEMENT|MUNICIPAL|MIAMI-?DADE|STATE OF FLORIDA|PACE|CLEAN ENERGY', re.I)
    _HOA_DOC_RE = re.compile(r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|\bMASTER\b|\bVILLAS?\b|COMMUNITY|PROPERTY\s+OWNERS?|TOWNHO|MAINTENANCE', re.I)
    _ASSN_DOC_RE = re.compile(r'(?<!NATIONAL\s)\bASS(?:N|OC(?:IATION)?)\b', re.I)
    _LIEN_DOC_RE = re.compile(r'^(LIEN|JUDGMENT|NOTICE|CLAIM|CERT|FINANCING STATEMENT)', re.I)
    def _norm_party(s):
        s = (s or '').upper()
        s = re.sub(r'\b(NA|N A|INC|CORP|CO|LLC|LP|USA|TRUST|COMPANY|OF|THE|AND|ASSN|ASSOC|ASSOCIATION)\b', '', s)
        return re.sub(r'[^A-Z]', '', s)
    sats_parties = {_norm_party(r.get('seconD_PARTY')) for r in models if 'SATISFACTION' in (r.get('doC_TYPE', '') or '').upper()
                    or 'RELEASE' in (r.get('doC_TYPE', '') or '').upper()}
    hoa_open = code_open = irs_open = 0
    for r in models:
        if not _LIEN_DOC_RE.match((r.get('doC_TYPE', '') or '').upper().strip()):
            continue
        amt = num(r.get('consideratioN_1'))
        if amt <= 0:
            continue
        party = r.get('seconD_PARTY') or ''
        rf = norm_folio(r.get('foliO_NUMBER', ''))
        sd = (r.get('subdiV_NAME') or '').strip().upper()
        on_parcel = bool((rf and rf == fol) or (subj_subdiv and sd == subj_subdiv))
        holder = _norm_party(party)
        if holder and holder in sats_parties:
            continue                                          # released by a same-party satisfaction
        if _IRS_RE.search(party):
            irs_open += amt                                 # person-wide, attaches regardless
        elif _HOA_DOC_RE.search(party) or _ASSN_DOC_RE.search(party):
            if on_parcel: hoa_open += amt
        elif _CODE_RE.search(party):
            if on_parcel: code_open += amt
        elif 'JUDGMENT' in (r.get('doC_TYPE', '') or '').upper():
            code_open += amt                                # debt-buyer money judgments ride as surviving liens
    # confidence: we must have isolated by a real anchor, sane count, and not a common-name over-match
    conf = 'ok'
    if not subj_subdiv: conf = 'low'                   # couldn't anchor the property (no folio-carrying record)
    if len(opens) > 4: conf = 'low'                    # one parcel rarely has >4 live mortgages
    if len(models) > 45: conf = 'low'                  # busy/common name -> results unreliable
    return {'liens': liens, 'open_count': len(opens), 'junior': junior, 'first_est': first_amt,
            'surv': surv, 'surv_first': surv_first, 'juniors_post': juniors_post,
            'hoa_open': hoa_open, 'code_open': code_open, 'irs_open': irs_open,
            'ftype': ftype, 'conf': conf, 'subdiv': subj_subdiv}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', default='')
    ap.add_argument('--tier', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--retries', type=int, default=40,
                    help='max previously-FAILED traces to retry this run (nightly default 40; '
                         'raise to clear a backlog — camoufox mints are free, 2Captcha ~$0.003)')
    ap.add_argument('--cached-only', action='store_true', help="only owners with a cached search token (fast, no browser)")
    ap.add_argument('--no-camoufox', action='store_true',
                    help="skip the free Camoufox token mint and go straight to 2Captcha "
                         "(escape hatch for the day the county stops issuing tokens to it)")
    ap.add_argument('--persist', action='store_true', help="never give up on the captcha — keep minting with back-off until it yields (per-lead cap via MINT_ATTEMPTS env, default 25). This is how we FIGURE OUT the surviving-senior for every lead no matter how hostile the wall is.")
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    leads = json.load(open(LEADS, encoding='utf-8'))
    qs_cache = json.load(open(QS_CACHE, encoding='utf-8')) if os.path.exists(QS_CACHE) else {}
    out = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}

    picked = []
    md_retries = []          # previously-failed traces, retried within the run's budget
    skipped = {}
    for r in leads:
        case = r.get('Case #', '') or ''
        if a.case and case != a.case: continue
        if a.tier and (r.get('tier', '') or '') != a.tier: continue
        oc = (r.get('owner_clean', '') or '').strip()
        if not oc: continue                                        # kimi: companies traced too (folio-isolated)
        # Government bodies and bare street addresses cost a mint every run and return nothing.
        # --case overrides, so a human can still force one if they have a reason.
        if not a.case:
            why = untraceable_owner(oc)
            if why:
                skipped.setdefault(why, []).append(oc)
                continue
        if a.cached_only and oc not in qs_cache: continue
        if case in out and not a.case:
            # A FAILED trace is not a result. Miami-Dade cached conf 'none' FOREVER, so 259 of 370
            # MD leads were frozen as "equity unverified" and the tracer reported nothing left to
            # do — the same bug Broward fixed 2026-08-18 (broward_liens ~L474), which MD never got.
            # It matters more here than it did there: MD returns 'none' when there is no FOLIO to
            # isolate by (analyze(), ~L377), and stub_resolve.py now BACKFILLS folios — so a lead
            # that legitimately failed last week can succeed today with no other change.
            # Capped per run: a mint costs ~$0.003, so retries are bounded like fresh pulls.
            if (out.get(case) or {}).get('conf') == 'none':
                md_retries.append(r)
            continue
        picked.append(r)
    if a.limit: picked = picked[:a.limit]
    # append retries AFTER the fresh cap so new leads always win the budget
    if md_retries:
        room = max(0, (a.limit or len(picked) + a.retries) - len(picked))
        take = md_retries[:min(room, a.retries)]
        if take:
            print(f"retrying {len(take)} previously-failed trace(s) of {len(md_retries)} "
                  f"(folios backfilled since; a cached failure is not a result)")
            picked += take

    cached = sum(1 for r in picked if (r.get('owner_clean','') or '').strip() in qs_cache)
    print(f"{len(picked)} lead(s) to pull ({cached} via cached token / requests, {len(picked)-cached} need a mint)")
    # Say what was dropped and why. A silent filter reads as "there was nothing there".
    for why, names in sorted(skipped.items()):
        uniq = sorted(set(names))
        print(f"  skipped {len(names)} lead(s) — {why}: "
              + ', '.join(n[:34] for n in uniq[:3]) + (' ...' if len(uniq) > 3 else ''))
    if a.dry_run or not picked:
        for r in picked[:20]:
            oc=(r.get('owner_clean','') or '').strip()
            print(f"  {r.get('Case #',''):22} {oc:26} {'cached' if oc in qs_cache else 'MINT'}")
        return

    # One Camoufox for the whole batch, opened only when there is actually something to mint.
    cf_cm = cf_browser = None
    need_mint = any((r.get('owner_clean', '') or '').strip() not in qs_cache for r in picked)
    if need_mint and not a.cached_only and not a.no_camoufox:
        cf_cm, cf_browser = camoufox_session()
        print('  camoufox: %s' % ('ready (free Turnstile tokens)' if cf_browser
                                  else 'UNAVAILABLE — %s; using 2Captcha' % CF_UNAVAILABLE))

    done = hits = cf_free = paid = 0
    try:
        for r in picked:
            case = r.get('Case #', ''); oc = (r.get('owner_clean', '') or '').strip()
            folio = r.get('Folio', '') or r.get('year_folio', '')
            judg = num(r.get('judgment'))
            models = None
            if oc in qs_cache:
                models = records_by_qs(qs_cache[oc])          # free: reuse a still-valid cached token
            if models is None and not a.cached_only:
                sp = split_owner(oc)
                if sp:
                    # 1) CAMOUFOX (2026-08-22): the browser mints its own Turnstile token, so this costs
                    #    nothing. Tried before 2Captcha for exactly that reason. A captured qs is written
                    #    back to records_qs.json, which puts the NEXT run for this owner on the free
                    #    plain-requests path above — the saving compounds instead of repeating.
                    #    Any failure just falls through to the paid path below; it never ends the run.
                    if cf_browser is not None:
                        try:
                            qs = camoufox_qs(cf_browser, sp[0].strip())
                        except Exception as e:
                            qs = None
                            print(f'  camoufox errored ({str(e)[:70]}) — falling back to 2Captcha')
                        if qs:
                            models = records_by_qs(qs)
                            if models is not None:
                                cf_free += 1
                                qs_cache[oc] = qs
                                try:
                                    json.dump(qs_cache, open(QS_CACHE, 'w', encoding='utf-8'), indent=1)
                                except Exception:
                                    pass

                    # 2) 2CAPTCHA (2026-07-21): solve Turnstile for ~$0.003, no browser. This is what
                    # took the wall from ~15% coverage to near-total, and it stays as the fallback for
                    # the day Turnstile stops handing out free tokens. Browser mint is the last resort —
                    # skip it silently when the JS template is missing (site migrated away from the old
                    # reCAPTCHA v3 the mint code was built for), so a broken fallback never masks a real
                    # Turnstile failure. `--persist` on the batch is a no-op when the fallback is dead.
                    if models is None:
                        paid += 1
                        models = fetch_via_turnstile(sp)
                    if models is None:
                        src = open(os.path.join(HERE, 'gen_records_qs.py'), encoding='utf-8').read()
                        if 'JS = r"""' in src:
                            models = mint_and_fetch(sp, persist=a.persist)
            if models is None:
                print(f"  --  {case:22} {oc:26} (no records / blocked)")
                continue
            res = analyze(models, folio, judg, ftype=_fc_type(case))
            res['traced'] = time.strftime('%Y-%m-%d'); res['folio'] = norm_folio(folio); res['owner'] = oc
            out[case] = res
            done += 1
            flag = ''
            if res['open_count'] >= 2:
                hits += 1; flag = f"  <-- OPEN 2ND ~${res['junior']:,} (of {res['open_count']} open mtgs)"
            print(f"  ok  {case:22} {oc:26} {res['open_count']} open mtg{flag}")
            json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
            time.sleep(0.4)
    finally:
        if cf_cm is not None:
            try:
                cf_cm.__exit__(None, None, None)
            except Exception:
                pass

    print(f"\nDONE: {done} traced, {hits} with a surviving 2nd mortgage. -> records_liens.json")
    if cf_free or paid:
        # paid counts owners that reached fetch_via_turnstile; each of those is a 2Captcha solve
        # (~$0.003) that a free Camoufox token would have avoided.
        print(f"     token source: {cf_free} free (camoufox) / {paid} paid (2captcha)"
              f"  ~${paid * 0.003:.3f} spent, ~${cf_free * 0.003:.3f} avoided")


if __name__ == '__main__':
    main()
