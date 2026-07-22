#!/usr/bin/env python3
"""broward_plaintiff.py — resolve a Broward foreclosure PLAINTIFF + case type straight from the
authoritative court source (Broward Clerk of Courts, browardclerk.org/Web2/CaseSearchECA).

Why this exists: broward_liens.py searches Official Records (AcclaimWeb) by OWNER NAME, which misses
cases the recorded chain doesn't index under the owner (companies like 'LATITUDE PROPERTY' return 0
recs). The court's own case-number search returns the case STYLE ("PLAINTIFF vs DEFENDANT") and the
case type directly, so we can tell a bank foreclosing the 1st mortgage from an HOA/junior plaintiff.

Transport: native Windows curl (Schannel TLS) for the browardclerk GET/POST — the same fingerprint
trick broward_liens.py uses. The case-number form posts to CaseNumberSearchResultsCAPTCHA and is gated
by Google reCAPTCHA v2 (checkbox); we solve it via 2Captcha (captcha_solver.solve_recaptcha_v2) and
submit the g-recaptcha-response with the page's anti-forgery token + session cookies.

    python broward_plaintiff.py --case CACE-24-005649
    python broward_plaintiff.py --case CACE-24-005649 --raw   # also dump the results HTML
"""
import argparse, json, os, re, subprocess, tempfile, time, html as _html

import captcha_solver
import broward_liens as BL   # reuse the plaintiff -> MORTGAGE/HOA classifier (_fc_type_plaintiff)

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 'https://www.browardclerk.org/Web2/CaseSearchECA'
LANDING = BASE + '/'
POST_URL = 'https://www.browardclerk.org//Web2/CaseSearchECA/CaseNumberSearchResultsCAPTCHA'
SITE_KEY = '6LeomjoqAAAAANqUs56ZxerFIcoUS1qL14rTH4aF'   # reCAPTCHA v2 site key on the case search page
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
CURL = r'C:\Windows\System32\curl.exe' if os.name == 'nt' and os.path.exists(r'C:\Windows\System32\curl.exe') else 'curl'


def _curl(url, jar, post=None, timeout=45, headers=None, dump_headers=False):
    cmd = [CURL, '-s', '-m', str(timeout), '-A', UA, '-c', jar, '-b', jar,
           '-H', 'Accept: text/html,application/json,*/*;q=0.8', '-H', 'Accept-Language: en-US,en;q=0.9']
    if dump_headers:
        cmd += ['-D', '-']            # write response headers to stdout, before the body
    for h in (headers or []):
        cmd += ['-H', h]
    if post is not None:
        cmd += ['-X', 'POST', '-H', 'Content-Type: application/x-www-form-urlencoded']
        for k, v in post:
            cmd += ['--data-urlencode', f'{k}={v}']
    cmd += [url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout + 10)
        return r.stdout or ''
    except Exception:
        return ''


def fetch_case_html(case, verbose=True):
    """Return the raw results HTML for a case number, or '' if blocked/failed."""
    jar = os.path.join(tempfile.gettempdir(), 'brw_plaintiff_cookies.txt')
    try: os.remove(jar)
    except OSError: pass
    land = _curl(LANDING, jar)
    if 'caseSearchForm' not in land:
        if verbose: print('  landing failed (no caseSearchForm) — blocked?')
        return ''
    i = land.find('id="caseSearchForm"')
    m = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', land[i:i + 800])
    if not m:
        if verbose: print('  could not find anti-forgery token')
        return ''
    token = m.group(1)
    if verbose: print(f'  session up, token {len(token)}c — solving reCAPTCHA v2...')
    t0 = time.time()
    cap = captcha_solver.solve_recaptcha_v2(SITE_KEY, LANDING)
    if not cap:
        if verbose: print('  captcha solve failed')
        return ''
    if verbose: print(f'  captcha solved ({len(cap)}c) in {int(time.time()-t0)}s — posting case...')
    resp = _curl(POST_URL, jar, post=[
        ('__RequestVerificationToken', token),
        ('CaseNumber', case),
        ('AccessLevel', 'ANONYMOUS'),
        ('g-recaptcha-response', cap),
    ], headers=[
        'Referer: ' + LANDING,
        'Origin: https://www.browardclerk.org',
    ], dump_headers=True)
    # the successful POST 302-redirects to the encrypted Results URL; a captcha/token reject 302s home.
    m = re.search(r'(?im)^location:\s*(\S+)', resp)
    if not m:
        if verbose: print('  no redirect Location on POST (rejected?)')
        return ''
    loc = m.group(1).strip()
    if 'Results' not in loc:
        if verbose: print(f'  POST redirected to {loc[:60]} (not Results — captcha/token rejected)')
        return ''
    if loc.startswith('/'):
        loc = 'https://www.browardclerk.org' + loc
    loc = _html.unescape(loc)
    if verbose: print('  got Results URL — fetching case detail...')
    return _curl(loc, jar, headers=['Referer: ' + LANDING])


def parse_results(html_text, case=''):
    """Extract the case record(s) from a CaseSearchECA Results page. The page embeds the model as a
    JS string literal ( var array = "…" ) that is a JSON string of a JSON array — decode iteratively.
    Returns the dict whose CaseNumber matches `case` (dashes ignored), else the first record, else None."""
    m = re.search(r'var array = "(.*?)";\s*\n', html_text, re.S)
    if not m:
        return None
    cur = json.loads('"' + m.group(1) + '"')
    for _ in range(4):
        if isinstance(cur, list):
            break
        try:
            cur = json.loads(cur)
        except Exception:
            return None
    if not isinstance(cur, list) or not cur:
        return None
    want = re.sub(r'[^A-Z0-9]', '', (case or '').upper())
    for r in cur:
        if want and re.sub(r'[^A-Z0-9]', '', (r.get('CaseNumber') or '').upper()) == want:
            return r
    return cur[0]


def _plaintiff_from_style(style):
    """The Style reads 'PLAINTIFF\\n Plaintiff\\nvs.\\n\\nDEFENDANT\\n Defendant'. Take the text before
    the first 'Plaintiff' label. Multiple plaintiffs are newline/comma separated in that block."""
    s = (style or '').replace('\r', '')
    m = re.split(r'\n\s*Plaintiff\b', s, maxsplit=1, flags=re.I)
    head = m[0] if m else s
    return re.sub(r'\s+', ' ', head).strip(' ,\n')


def _defendant_from_style(style):
    s = (style or '').replace('\r', '')
    m = re.search(r'vs\.?\s*(.+?)\s*(?:\n\s*Defendant\b|$)', s, re.I | re.S)
    return re.sub(r'\s+', ' ', m.group(1)).strip(' ,\n') if m else ''


def classify(rec):
    """Turn a court record into {plaintiff, defendant, caseType, courtType, status, filed,
    isBankForeclosingFirst, ftype}. isBankForeclosingFirst is True when the plaintiff is a
    bank/lender/mortgage note-holder (foreclosing 1st mortgage, nothing senior survives) and False
    when it's an HOA/association/individual/junior (a senior mortgage survives)."""
    style = rec.get('Style') or ''
    plaintiff = _plaintiff_from_style(style)
    utype = rec.get('CaseUTypeDesc') or ''
    # plaintiff name is the decisive signal (HOAs sue in circuit court under a CACE all the time)
    ftype = BL._fc_type_plaintiff(plaintiff)
    if not ftype:
        # fall back to the case-type description: "...Fore..." with no HOA/assn wording reads as a
        # mortgage foreclosure; an association/lien type reads HOA.
        u = utype.upper()
        if re.search(r'ASSN|ASSOC|HOMEOWNER|CONDO|LIEN', u):
            ftype = 'HOA'
        elif 'FORE' in u:
            ftype = 'MORTGAGE'
    is_bank_first = (ftype == 'MORTGAGE')
    return {
        'plaintiff': plaintiff,
        'defendant': _defendant_from_style(style),
        'caseType': utype,
        'caseTypeCode': rec.get('CaseUTypeCode') or '',
        'courtType': rec.get('CourtType') or '',
        'status': rec.get('CaseStatusDesc') or '',
        'filed': rec.get('CaseFiledDate') or '',
        'caseNumber': rec.get('CaseNumber') or '',
        'ftype': ftype,
        'isBankForeclosingFirst': is_bank_first,
    }


def resolve(case, verbose=True, retries=2):
    """Full pipeline: fetch the court results page for a case number, parse + classify. Returns the
    classify() dict (with 'ok': True) or {'ok': False, 'error': ...}. Retries a couple of times because
    a 2Captcha solve occasionally returns an unusable token (the POST then 302s home, not to Results)."""
    last = 'unknown'
    for attempt in range(1, retries + 1):
        h = fetch_case_html(case, verbose=verbose)
        if not h:
            last = 'fetch failed (blocked / captcha reject / no Results redirect)'
            if verbose and attempt < retries: print(f'  attempt {attempt} failed; retrying...')
            continue
        rec = parse_results(h, case)
        if not rec:
            last = 'no case record in results (case not found / not public)'
            continue
        out = classify(rec)
        out['ok'] = True
        return out
    return {'ok': False, 'error': last, 'case': case}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', help='one case number, e.g. CACE-24-005649')
    ap.add_argument('--cases', help='comma-separated case numbers for a batch run')
    ap.add_argument('--parse-file', help='parse a previously-saved results HTML (offline, no solve)')
    a = ap.parse_args()

    if a.parse_file:
        rec = parse_results(open(a.parse_file, encoding='utf-8', errors='replace').read(), a.case or '')
        print(json.dumps(classify(rec) if rec else {'ok': False}, indent=2))
        raise SystemExit

    cases = [c.strip() for c in (a.cases.split(',') if a.cases else [a.case]) if c and c.strip()]
    print(f'2captcha balance: ${captcha_solver.balance()}')
    for c in cases:
        print(f'\n=== {c} ===')
        print(json.dumps(resolve(c), indent=2))
