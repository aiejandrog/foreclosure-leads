#!/usr/bin/env python3
"""captcha_solver.py — solve Google reCAPTCHA v3 via 2Captcha, so the county Official Records wall
stops blocking us.

The Miami-Dade / Broward / Palm Beach clerk sites gate their record search behind reCAPTCHA v3 —
score-based and invisible, so there is no image challenge to click. A headless (or even headed)
browser gets scored as a bot and rejected. 2Captcha generates a high-score v3 token from residential
infrastructure and returns it; we hand that token to the clerk's `standardsearch` POST in the same
`x-recaptcha-token` header the browser mint used. reCAPTCHA v3 scores the TOKEN (site secret-key
verification), not the submitting IP, so a 2Captcha token submitted by plain `requests` validates.

Key lives in captcha.key (gitignored) or the TWOCAPTCHA_KEY / CAPTCHA_KEY env var.

    from captcha_solver import solve_recaptcha_v3
    token = solve_recaptcha_v3(SITE_KEY, 'standardsearch', 'https://onlineservices.miamidadeclerk.gov/officialrecords/')
"""
import os
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
IN_URL = 'https://2captcha.com/in.php'
RES_URL = 'https://2captcha.com/res.php'


def _key():
    k = (os.environ.get('TWOCAPTCHA_KEY') or os.environ.get('CAPTCHA_KEY') or '').strip()
    if k:
        return k
    p = os.path.join(HERE, 'captcha.key')
    if os.path.exists(p):
        return open(p, encoding='utf-8').read().strip()
    return ''


def solve_recaptcha_v3(site_key, action, page_url, min_score=0.3, timeout=140, poll=5):
    """Return a solved reCAPTCHA v3 token, or None on failure/timeout. Blocking (polls 2Captcha)."""
    key = _key()
    if not key:
        print('  [2captcha] no key (captcha.key / TWOCAPTCHA_KEY) — cannot solve')
        return None
    try:
        r = requests.post(IN_URL, data={
            'key': key, 'method': 'userrecaptcha', 'version': 'v3',
            'googlekey': site_key, 'pageurl': page_url, 'action': action,
            'min_score': min_score, 'json': 1,
        }, timeout=30).json()
    except Exception as e:
        print(f'  [2captcha] in.php error: {str(e)[:80]}')
        return None
    if r.get('status') != 1:
        print(f'  [2captcha] submit rejected: {r.get("request")}')
        return None
    cid = r['request']
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(poll)
        try:
            g = requests.get(RES_URL, params={'key': key, 'action': 'get', 'id': cid, 'json': 1}, timeout=30).json()
        except Exception:
            continue
        if g.get('status') == 1:
            return g['request']
        if g.get('request') != 'CAPCHA_NOT_READY':      # 2captcha's (sic) not-ready sentinel
            print(f'  [2captcha] solve failed: {g.get("request")}')
            return None
    print('  [2captcha] timed out waiting for token')
    return None


def solve_recaptcha_v2(site_key, page_url, timeout=180, poll=5):
    """Return a solved reCAPTCHA v2 (checkbox) token ('g-recaptcha-response'), or None. Used by the
    Broward Clerk court case search (browardclerk.org/Web2/CaseSearchECA), which renders a v2 widget
    (grecaptcha.render with a callback) rather than the score-based v3 the official-records sites use."""
    key = _key()
    if not key:
        print('  [2captcha] no key — cannot solve recaptcha v2')
        return None
    try:
        r = requests.post(IN_URL, data={
            'key': key, 'method': 'userrecaptcha',
            'googlekey': site_key, 'pageurl': page_url, 'json': 1,
        }, timeout=30).json()
    except Exception as e:
        print(f'  [2captcha] v2 in.php error: {str(e)[:80]}')
        return None
    if r.get('status') != 1:
        print(f'  [2captcha] v2 submit rejected: {r.get("request")}')
        return None
    cid = r['request']
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(poll)
        try:
            g = requests.get(RES_URL, params={'key': key, 'action': 'get', 'id': cid, 'json': 1}, timeout=30).json()
        except Exception:
            continue
        if g.get('status') == 1:
            return g['request']
        if g.get('request') != 'CAPCHA_NOT_READY':
            print(f'  [2captcha] v2 solve failed: {g.get("request")}')
            return None
    print('  [2captcha] v2 timed out waiting for token')
    return None


def solve_turnstile(site_key, page_url, action=None, timeout=140, poll=5):
    """Return a solved Cloudflare Turnstile token, or None. Miami-Dade Official Records migrated from
    reCAPTCHA v3 to Turnstile (running in reCAPTCHA-compatibility mode), so the OLD reCAPTCHA site key
    is dead and the app now feeds a TURNSTILE token into the same x-recaptcha-token header. 2Captcha
    solves Turnstile via method=turnstile."""
    key = _key()
    if not key:
        print('  [2captcha] no key — cannot solve turnstile')
        return None
    data = {'key': key, 'method': 'turnstile', 'sitekey': site_key, 'pageurl': page_url, 'json': 1}
    if action:
        data['action'] = action
    try:
        r = requests.post(IN_URL, data=data, timeout=30).json()
    except Exception as e:
        print(f'  [2captcha] turnstile in.php error: {str(e)[:80]}')
        return None
    if r.get('status') != 1:
        print(f'  [2captcha] turnstile submit rejected: {r.get("request")}')
        return None
    cid = r['request']
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(poll)
        try:
            g = requests.get(RES_URL, params={'key': key, 'action': 'get', 'id': cid, 'json': 1}, timeout=30).json()
        except Exception:
            continue
        if g.get('status') == 1:
            return g['request']
        if g.get('request') != 'CAPCHA_NOT_READY':
            print(f'  [2captcha] turnstile solve failed: {g.get("request")}')
            return None
    print('  [2captcha] turnstile timed out')
    return None


def balance():
    key = _key()
    if not key:
        return None
    try:
        return requests.get(RES_URL, params={'key': key, 'action': 'getbalance', 'json': 1}, timeout=20).json().get('request')
    except Exception:
        return None


if __name__ == '__main__':
    # live smoke test: solve MD Official Records reCAPTCHA v3 and prove the token unlocks a search.
    import records_liens as R
    print('2captcha balance: $', balance())
    site = R.SITE_KEY
    page = 'https://onlineservices.miamidadeclerk.gov/officialrecords/'
    print('solving reCAPTCHA v3 for MD Official Records...')
    t0 = time.time()
    tok = solve_recaptcha_v3(site, 'standardsearch', page)
    print(f'token: {"<none>" if not tok else tok[:40] + "... (" + str(len(tok)) + " chars)"}  in {int(time.time()-t0)}s')
    if not tok:
        raise SystemExit('SOLVE FAILED')
    # now try the actual search with a real owner name, requests-only
    import urllib.parse
    name = 'ECHEVERRI EDUARDO'
    url = ('https://onlineservices.miamidadeclerk.gov/officialrecords/api/home/standardsearch'
           '?partyName=' + urllib.parse.quote(name) + '&dateRangeFrom=&dateRangeTo=&documentType='
           '&searchT=&firstQuery=y&searchtype=' + urllib.parse.quote('Name/Document'))
    s = requests.Session()
    s.headers.update({'User-Agent': R.UA, 'Accept': 'application/json',
                      'Referer': page, 'x-recaptcha-token': tok,
                      'content-type': 'application/json; charset=utf-8'})
    try:
        resp = s.post(url, data='', timeout=30)
        j = resp.json()
        qs = j.get('qs')
        print(f'standardsearch HTTP {resp.status_code}, qs={"<none>" if not qs else qs[:30] + "..."}')
        if qs:
            recs = R.records_by_qs(qs)
            print(f'*** SUCCESS: pulled {len(recs) if recs else 0} recorded docs for {name} via 2Captcha')
        else:
            print('search returned no qs — token maybe rejected; raw:', str(j)[:200])
    except Exception as e:
        print('search error:', str(e)[:120])
