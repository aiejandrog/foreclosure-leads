#!/usr/bin/env python3
"""captcha_solver.py — 2Captcha client for county Official Records / court walls.

Uses official API v1 (https://2captcha.com/2captcha-api) — in.php + res.php — same protocol
the MD Turnstile path already relies on. API v2 createTask/getTaskResult is equivalent; we stay
on v1 so existing MD/BW callers keep working.

  reCAPTCHA v2: method=userrecaptcha + googlekey + pageurl
                → token goes in g-recaptcha-response  (PB Landmark, Broward CaseSearch)
  reCAPTCHA v3: method=userrecaptcha + version=v3 + action + min_score
  Turnstile:    method=turnstile + sitekey + pageurl
                → MD Official Records (x-recaptcha-token header)

Key: captcha.key (gitignored) or TWOCAPTCHA_KEY / CAPTCHA_KEY env. Never log the key.

Docs: https://2captcha.com/api-docs/recaptcha-v2
      https://2captcha.com/api-docs/cloudflare-turnstile
      https://2captcha.com/2captcha-api
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


def has_key():
    """True when a 2Captcha key is configured. Does not return or log the key."""
    return bool(_key())


def _poll(key, cid, timeout, poll, label='2captcha'):
    """Poll res.php until ready. Returns token string or None. Never logs the API key."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(poll)
        try:
            g = requests.get(
                RES_URL,
                params={'key': key, 'action': 'get', 'id': cid, 'json': 1},
                timeout=30,
            ).json()
        except Exception:
            continue
        if g.get('status') == 1:
            return g['request']
        if g.get('request') != 'CAPCHA_NOT_READY':  # 2captcha's (sic) not-ready sentinel
            print(f'  [{label}] solve failed: {g.get("request")}')
            return None
    print(f'  [{label}] timed out waiting for token')
    return None


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
    return _poll(key, r['request'], timeout, poll, label='2captcha-v3')


def solve_recaptcha_v2(site_key, page_url, timeout=240, poll=5):
    """Return a solved reCAPTCHA v2 checkbox token (g-recaptcha-response), or None.

    Official API v1: POST in.php method=userrecaptcha googlekey=<sitekey> pageurl=<full URL>
    Used by: Palm Beach Landmark (erec.mypalmbeachclerk.com), Broward CaseSearchECA.
    """
    key = _key()
    if not key:
        print('  [2captcha] no key — cannot solve recaptcha v2')
        return None
    if not site_key or not page_url:
        print('  [2captcha] v2 requires site_key + full page_url')
        return None
    try:
        # https://2captcha.com/2captcha-api#solving_recaptchav2_new
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
    print(f'  [2captcha] v2 task {r["request"]} queued — polling…')
    return _poll(key, r['request'], timeout, poll, label='2captcha-v2')


def solve_turnstile(site_key, page_url, action=None, timeout=140, poll=5):
    """Return a solved Cloudflare Turnstile token, or None. Miami-Dade Official Records migrated from
    reCAPTCHA v3 to Turnstile (running in reCAPTCHA-compatibility mode), so the OLD reCAPTCHA site key
    is dead and the app now feeds a TURNSTILE token into the same x-recaptcha-token header. 2Captcha
    solves Turnstile via method=turnstile (API v1)."""
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
    return _poll(key, r['request'], timeout, poll, label='2captcha-turnstile')


def balance():
    key = _key()
    if not key:
        return None
    try:
        return requests.get(RES_URL, params={'key': key, 'action': 'getbalance', 'json': 1}, timeout=20).json().get('request')
    except Exception:
        return None


if __name__ == '__main__':
    # live smoke test: solve MD Official Records Turnstile and prove the token unlocks a search.
    import records_liens as R
    print('2captcha key configured:', has_key())
    print('2captcha balance: $', balance())
    page = 'https://onlineservices.miamidadeclerk.gov/officialrecords/'
    print('solving Turnstile for MD Official Records...')
    t0 = time.time()
    tok = solve_turnstile(R.TS_SITE_KEY, page)
    print(f'token: {"<none>" if not tok else tok[:40] + "... (" + str(len(tok)) + " chars)"}  in {int(time.time()-t0)}s')
    if not tok:
        raise SystemExit('SOLVE FAILED')
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
