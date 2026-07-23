"""Generate direct-to-results Miami-Dade Official Records ('Records') links per owner — the owner's
MORTGAGES, liens and judgments (this is how you find the hidden 1st/2nd mortgage Jose asks about).

The clerk migrated Official Records to a Turnstile-gated SPA, which killed the old reCAPTCHA-v3 browser
mint. This now mints the qs the SAME way records_liens.py does — 2Captcha solves Cloudflare Turnstile,
we POST the Name/Document standardsearch with the token, and keep the qs. The qs is a stateless search
token (the getStandardRecords GET + the SearchResults page are UNGATED), so it works as a durable
deep-link: SearchResults?qs=<qs> opens the owner's recorded documents directly. Verified live 2026-07-23.

Cache (records_qs.json) keyed by owner_clean; make_tracker bakes the qs onto the lead as recqs; misses
fall back to open-the-search + copy-the-name.

Usage: python gen_records_qs.py [--tier A] [--limit N] [--refresh]
"""
import argparse
import json
import os
import re
import time
import urllib.parse

import records_liens as R          # split_owner (corrected surname order), OR_BASE, S, TS_SITE_KEY, records_by_qs

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
CACHE = os.path.join(HERE, 'records_qs.json')
MAX_HITS = 100   # a real individual owner rarely has >100 recorded docs; more = common-name over-match -> skip
DEADLINE_SEC = int(os.environ.get('GEN_DEADLINE', '480'))   # stay under the scheduled task's kill; resume next run


def mint_qs(owner_lf, tries=3):
    """Turnstile-mint a durable qs for owner (SURNAME, GIVEN). Returns (qs, record_count) or (None, 0)."""
    from captcha_solver import solve_turnstile
    party = (owner_lf[0] + ' ' + (owner_lf[1] or '')).strip()
    url = (R.OR_BASE + 'api/home/standardsearch?partyName=' + urllib.parse.quote(party)
           + '&dateRangeFrom=&dateRangeTo=&documentType=&searchT=&firstQuery=y&searchtype='
           + urllib.parse.quote('Name/Document'))
    for _ in range(tries):
        tok = solve_turnstile(R.TS_SITE_KEY, R.OR_BASE)
        if not tok:
            continue
        try:
            j = R.S.post(url, headers={'x-recaptcha-token': tok, 'content-type': 'application/json; charset=utf-8'},
                         data='', timeout=30).json()
        except Exception:
            continue
        qs = j.get('qs') if isinstance(j, dict) else None
        if qs:
            recs = R.records_by_qs(qs) or []
            return qs, len(recs)
        time.sleep(1)
    return None, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tier', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--refresh', action='store_true', help='re-mint even owners already cached')
    args = ap.parse_args()

    leads = json.load(open(LEADS, encoding='utf-8'))
    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    todo = {}
    for r in leads:
        if args.tier and (r.get('tier', '') or '') != args.tier:
            continue
        oc = (r.get('owner_clean') or '').strip()
        if not oc or (oc in cache and not args.refresh):
            continue
        sp = R.split_owner(oc)                                  # (SURNAME, GIVEN) — corrected order
        if sp:
            todo[oc] = sp
    items = list(todo.items())
    if args.limit:
        items = items[:args.limit]
    print(f"{len(items)} owner(s) to generate ({len(cache)} cached, of {len(leads)} leads) via Turnstile")
    if not items:
        return

    ok = 0
    _start = time.time()
    for oc, lf in items:
        if time.time() - _start > DEADLINE_SEC:
            print("  .. budget hit; stopping (rest resume next run)"); break
        qs, n = mint_qs(lf)
        if qs and 0 < n <= MAX_HITS:
            cache[oc] = qs; ok += 1
            print(f"  ok  {oc:32} {n} record(s)")
        elif n > MAX_HITS:
            print(f"  ~~  {oc:32} too common ({n}), skip -> fallback")
        else:
            print(f"  --  {oc:32} no records / blocked")
        json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=1)
        time.sleep(0.5)
    print(f"\nDONE: {ok}/{len(items)} owners now have a direct Records deep-link. Cache -> records_qs.json")


if __name__ == '__main__':
    main()
