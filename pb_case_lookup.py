#!/usr/bin/env python3
"""pb_case_lookup.py — Palm Beach County (15th Judicial Circuit) court-case PLAINTIFF + case-type
resolver for a foreclosure CASE NUMBER, via the Clerk's eCaseView portal (appsgp.mypalmbeachclerk.com).

WHY: RealForeclose gives only a "Plaintiff Max Bid" dollar figure, never the plaintiff NAME, so we
cannot tell a bank foreclosing a 1st mortgage (nothing senior survives) from an HOA / junior / private
LLC (a senior mortgage survives). The authoritative source is the court docket. eCaseView's Search
returns the case STYLE ("PLAINTIFF V DEFENDANT"), the Case Type, the court type, filed date and status.

THE WALL — and how we beat it:
  eCaseView is a public *free* guest-search, but the Search POST is gated by reCAPTCHA v3 (score-based,
  invisible), site key 6LesMAssAAAAAKMaRLSl1d8DFRK5qaocke3wSoJf, action 'case_search'. Findings from
  cracking it (2026-07-22):
    * The guest-authenticated Search page renders recaptchaEnabled=true + the real key + action
      'case_search'. (An unauthenticated/cURL fetch renders a broken recaptchaEnabled=false variant
      whose "RECAPTCHA_DISABLED" token the server rejects — a dead end.)
    * A HEADLESS browser's own grecaptcha, and even 2Captcha-minted v3 tokens (any action/score), are
      REJECTED with "Could not verify the ReCaptcha" — the server's score/behavior check is strict.
    * A HEADED real browser doing the natural flow (page's own grecaptcha, action 'case_search') PASSES.
  So this runs a HEADED Chromium (like palmbeach_liens.py --headed). No human interaction is needed —
  reCAPTCHA v3 is invisible; the operator just leaves the window open. Guest-login once, loop the cases.

  Raw curl/requests is NOT viable end-to-end: the F5/TSPD JS anti-bot 400s header-less POSTs AND the
  reCAPTCHA needs a real browser. Playwright handles TSPD, antiforgery, TempData cookies and the score.

CASE-NUMBER FORMAT: the auction/lead UCN is e.g. 502026CA000685XXXAMB (county 50 + year 2026 + type CA
+ seq 000685 + division/branch XXXAMB). eCaseView's Case Number box wants YEAR+TYPE+SEQ only:
2026CA000685  ==  case[2:14].  (Example the site itself shows: 2015TR900123.)

    from pb_case_lookup import resolve_cases
    rows = resolve_cases(['502026CA000685XXXAMB'])   # -> [{'case','ucn','plaintiff','defendant',
                                                     #      'case_type','court_type','filed','status',
                                                     #      'is_bank_foreclosing_first'}]
"""
import re
import sys
import time

BASE = 'https://appsgp.mypalmbeachclerk.com/eCaseView'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
UCN_RE = re.compile(r'\d{2}-\d{4}-[A-Z]{2}-\d{6}-[A-Z0-9]{3,4}-[A-Z]{2}')
STYLE_SPLIT = re.compile(r'\s+V\.?S?\.?\s+', re.I)   # "PLAINTIFF V DEFENDANT" / " VS " / " V. "

# Bank / institutional-lender markers -> a 1st-mortgage foreclosure (nothing senior survives).
BANK_RE = re.compile(r'\b(BANK|MORTGAGE|LOAN|FINANC|CAPITAL|FUNDING|LENDING|LENDER|N\.?A\.?|'
                     r'NATIONAL ASSOCIATION|TRUST|SERVICING|SAVINGS|FEDERAL|CREDIT UNION|'
                     r'WELLS FARGO|CHASE|CITI|ROCKET|CROSSCOUNTRY|FREEDOM|LAKEVIEW|PENNYMAC|'
                     r'NEWREZ|CARRINGTON|NATIONSTAR|MR\.? COOPER|LOANDEPOT|FANNIE|FREDDIE|HUD|'
                     r'SECRETARY OF HOUSING|US BANK|U\.?S\.? BANK|DEUTSCHE|BNY|BANKUNITED|TRUIST|'
                     r'PNC|REGIONS|SANTANDER|FLAGSTAR|SELENE|SHELLPOINT|SPECIALIZED LOAN)\b')
HOA_RE = re.compile(r'\b(ASSOCIATION|ASSN|CONDOMINIUM|CONDO|HOMEOWNER|MASTER ASSOC|HOA|COA|'
                    r'TOWNHOM|VILLAS?|COMMUNITY|PROPERTY OWNERS|MAINTENANCE)\b')
# Case-type text that IS a real-property mortgage foreclosure on the docket.
MTG_FC_CT_RE = re.compile(r'MORTGAGE FORECLOS|RPMF|FORECLOSURE.*\$|HOMESTEAD RES FORECLOS', re.I)


def core_case(lead_case):
    """Lead UCN (502026CA000685XXXAMB) -> eCaseView Case Number box value (2026CA000685)."""
    c = re.sub(r'[^0-9A-Za-z]', '', lead_case or '').upper()
    m = re.match(r'\d{2}(\d{4}[A-Z]{2}\d{6})', c)
    return m.group(1) if m else c


def classify_first(plaintiff, case_type, court_type):
    """isBankForeclosingFirst: True only when a bank/institutional lender is foreclosing what reads as
    the 1st mortgage. HOA/junior/individual/LLC plaintiffs, and non-mortgage-foreclosure case types,
    mean a senior mortgage survives -> False. Returns (bool, reason)."""
    pl = (plaintiff or '').upper()
    ct = (case_type or '').upper()
    if not pl:
        return False, 'no plaintiff resolved'
    # HOA/condo association plaintiff -> junior lien, senior 1st mortgage survives.
    pl_no_na = re.sub(r'\bNATIONAL ASSOCIATION\b', ' ', pl)   # don't let a bank "N.A." trip the HOA test
    if HOA_RE.search(pl_no_na) and not BANK_RE.search(pl_no_na):
        return False, 'HOA/condo association plaintiff'
    is_bank = bool(BANK_RE.search(pl))
    is_mtg_ct = bool(MTG_FC_CT_RE.search(ct))
    # County Civil (CC) foreclosures are sub-$50k -> HOA/COA or junior, never a 1st mortgage.
    if (court_type or '').upper().startswith('COUNTY'):
        return False, 'County Civil (sub-$50k) = junior/HOA, senior mortgage survives'
    if is_bank and (is_mtg_ct or 'FORECLOS' in ct or ct == ''):
        return True, 'institutional lender foreclosing (1st mortgage)'
    if is_bank and not is_mtg_ct:
        # a bank plaintiff but the case type is not a mortgage foreclosure (e.g. OTHER RP ACTIONS) —
        # treat as first-mortgage only if clearly a foreclosure; otherwise flag uncertain -> False.
        return False, f'bank plaintiff but case type "{case_type}" is not a mortgage foreclosure'
    # non-bank plaintiff (individual / private LLC / investor) -> not an institutional 1st-mortgage FC.
    return False, 'non-bank (individual/LLC) plaintiff — senior mortgage may survive'


def _guest_login(pg):
    for _ in range(4):
        pg.goto(BASE + '/', wait_until='domcontentloaded', timeout=45000)
        pg.wait_for_timeout(2200)
        try:
            pg.click("button:has-text('Login as Guest User')", timeout=10000)
        except Exception:
            continue
        pg.wait_for_load_state('domcontentloaded')
        pg.wait_for_timeout(1600)
        if 'GuestIn' in pg.url or pg.locator("a[href*='SignOut']").count() > 0:
            return True
    return False


def _parse_result_rows(pg, want_core):
    rows = pg.evaluate("""()=>{const rs=[];document.querySelectorAll('#searchResults tbody tr, table tbody tr')
        .forEach(tr=>rs.push([...tr.querySelectorAll('td,th')].map(td=>(td.innerText||'').trim())));return rs;}""")
    out = []
    for cells in rows:
        joined = ' '.join(cells)
        mu = UCN_RE.search(joined)
        if not mu:
            continue
        ucn = mu.group(0)
        style = next((c for c in cells if STYLE_SPLIT.search(c) and not UCN_RE.search(c)), '')
        court_type = cells[1] if len(cells) > 1 else ''
        case_type = cells[2] if len(cells) > 2 else ''
        filed = next((c for c in cells if re.match(r'\d{1,2}/\d{1,2}/\d{4}$', c)), '')
        status = cells[-1] if cells else ''
        plaintiff = defendant = ''
        if style:
            parts = STYLE_SPLIT.split(style, 1)
            plaintiff = parts[0].strip()
            defendant = parts[1].strip() if len(parts) > 1 else ''
        out.append({'ucn': ucn, 'court_type': court_type, 'case_type': case_type, 'filed': filed,
                    'status': status, 'style': style, 'plaintiff': plaintiff, 'defendant': defendant})
    # prefer the row whose UCN sequence matches the requested core case
    if want_core and len(out) > 1:
        seq = re.sub(r'[^0-9A-Z]', '', want_core)[-6:]
        pref = [r for r in out if seq in re.sub(r'[^0-9A-Z]', '', r['ucn'])]
        if pref:
            return pref
    return out


def _search_one(pg, lead_case, retries=2):
    core = core_case(lead_case)
    for attempt in range(retries + 1):
        pg.goto(BASE + '/Search?handler=NewSearch', wait_until='domcontentloaded', timeout=45000)
        try:
            pg.wait_for_selector('#SearchRequest_CaseNumber', timeout=20000)
        except Exception:
            continue
        pg.fill('#SearchRequest_CaseNumber', core)
        pg.wait_for_timeout(500)
        pg.click('#btnBeginSearch')          # native grecaptcha (invisible v3, action case_search)
        try:
            pg.wait_for_url('**/SearchResults**', timeout=25000)
        except Exception:
            pg.wait_for_timeout(6000)
        txt = pg.evaluate("()=>{const c=document.querySelector('#caseinfo');return c?c.innerText:document.body.innerText;}")
        if 'Could not verify the ReCaptcha' in txt:
            time.sleep(2)
            continue                          # reCAPTCHA scored low this pass — retry
        if 'No cases found' in txt:
            return {'case': lead_case, 'core': core, 'found': False}
        rows = _parse_result_rows(pg, core)
        if rows:
            r = rows[0]
            isbank, reason = classify_first(r['plaintiff'], r['case_type'], r['court_type'])
            r.update({'case': lead_case, 'core': core, 'found': True,
                      'is_bank_foreclosing_first': isbank, 'classify_reason': reason})
            return r
        return {'case': lead_case, 'core': core, 'found': False, 'note': 'search ok, no parsable row'}
    return {'case': lead_case, 'core': core, 'found': False, 'note': 'reCAPTCHA blocked after retries'}


def resolve_cases(lead_cases, headless=False):
    """Resolve a list of lead UCNs. HEADED by default (reCAPTCHA v3 needs a real browser score)."""
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless)
        pg = b.new_context(user_agent=UA, viewport={'width': 1360, 'height': 950}).new_page()
        if not _guest_login(pg):
            b.close()
            raise RuntimeError('eCaseView guest login failed')
        for lc in lead_cases:
            try:
                results.append(_search_one(pg, lc))
            except Exception as e:
                results.append({'case': lc, 'found': False, 'note': f'error: {str(e)[:120]}'})
            time.sleep(0.8)
        b.close()
    return results


if __name__ == '__main__':
    import json
    cases = sys.argv[1:] or ['502026CA000685XXXAMB']
    res = resolve_cases(cases)
    for r in res:
        if r.get('found'):
            print(f"{r['case']}  ->  PLAINTIFF: {r['plaintiff']!r}  | type: {r['case_type']!r} "
                  f"({r['court_type']}) | status: {r['status']} | filed {r['filed']} "
                  f"| isBankForeclosingFirst={r['is_bank_foreclosing_first']} ({r['classify_reason']})")
        else:
            print(f"{r['case']}  ->  NOT RESOLVED  {r.get('note','')}")
    print('\nJSON:')
    print(json.dumps(res, indent=1))
