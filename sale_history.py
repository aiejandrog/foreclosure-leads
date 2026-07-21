#!/usr/bin/env python3
"""sale_history.py — the STALLER signal, from real court-docket sale events (Miami-Dade).

For each MD foreclosure lead, count how many times a foreclosure sale was SET and then CANCELLED /
RESET / RESCHEDULED before now. That "survived N sales" number is the SALAZAR intelligence — it
separates a first-timer (0 survivals, bows to a rescue) from a serial delayer (many survivals, will
almost certainly stall THIS sale too). Filing year alone was a proxy; this is the real thing.

Source: the Miami-Dade Clerk OCS API (fully public, no login) — the SAME endpoint the pipeline already
uses for plaintiff/defendants, so this just mines the docket array it returns:
  GET  /ocs/api/CaseInfo/encrypt/{CASE#}          -> {qs}
  POST /ocs/api/CaseInfo/GetSingleCaseResult?qs=  (body '""')  -> {dockets:[{docketDescrition,eventDate}]}

Cached in sale_history_cache.json with a 7-day TTL (a case's sale count CHANGES when a new sale is
set / cancelled, so unlike a lien chain it must re-check). Writes onto each lead:
  sale_survived  : times a scheduled sale was cancelled/reset (the staller count)
  sale_scheduled : distinct foreclosure-sale notices seen (context)

Miami-Dade only for now — Broward/Palm Beach dockets sit behind different (captcha-walled) clerk
portals, the same wall their lien tracers hit; they fall back to the filing-year FRESH/STALLER proxy.

Run:  python sale_history.py [--limit N] [--ttl-days 7] [--case CASE]
"""
import argparse
import json
import os
import re
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'sale_history_cache.json')
CLERK = 'https://www2.miamidadeclerk.gov'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

# A docket line about a foreclosure sale. CANCEL wins over SCHED (a "reset sale" both cancels the old
# one and sets a new one — we count it as a survival, the meaningful signal).
_SALE = re.compile(r'sale|certificate of title', re.I)
_CANCEL = re.compile(r'cancel|reset|reschedul|vacat|continu', re.I)
_SCHED = re.compile(r'notice of\s+.*sale|foreclosure sale|judicial sale', re.I)
_DONE = re.compile(r'certificate of (?:sale|title)', re.I)


def _count(dockets):
    """(survived, scheduled, completed) from a docket array."""
    surv = sched = done = 0
    for e in dockets or []:
        d = (e.get('docketDescrition') or e.get('docketDescription') or '')
        if not _SALE.search(d):
            continue
        if _DONE.search(d):
            done += 1
        elif _CANCEL.search(d):
            surv += 1
        elif _SCHED.search(d):
            sched += 1
    return surv, sched, done


def _fetch(session, case):
    """OCS docket array for a MD case, or None on any failure."""
    try:
        qs = session.get(f'{CLERK}/ocs/api/CaseInfo/encrypt/{case}', timeout=20).json().get('qs')
        if not qs:
            return None
        d = session.post(f'{CLERK}/ocs/api/CaseInfo/GetSingleCaseResult?qs={qs}',
                         headers={'Content-Type': 'application/json'}, data='""', timeout=20).json()
        if not d or d.get('caseID', -1) == -1:
            return None
        return d.get('dockets') or []
    except Exception:
        return None


def _load_cache():
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE, encoding='utf-8'))
        except Exception:
            return {}
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='max live fetches this run (0 = unlimited)')
    ap.add_argument('--ttl-days', type=float, default=7.0)
    ap.add_argument('--case', default='')
    a = ap.parse_args()

    path = os.path.join(HERE, 'leads_final.json')
    if not os.path.exists(path):
        print('leads_final.json missing'); return
    leads = json.load(open(path, encoding='utf-8'))
    cache = _load_cache()
    now = time.time()
    ttl = a.ttl_days * 86400
    session = requests.Session()
    session.headers.update({'User-Agent': UA, 'Referer': CLERK + '/ocs/'})

    budget = a.limit if a.limit > 0 else 10 ** 9
    changed = fetched = 0
    for r in leads:
        case = (r.get('Case #') or '').strip()
        if a.case and case != a.case:
            continue
        # civil MD foreclosure cases only (tax-deed & non-CA cases aren't in OCS)
        if r.get('sale_type') == 'TD' or not re.match(r'\d{4}-\d+-\w+-\d+', case):
            continue
        ent = cache.get(case)
        if ent and (now - ent.get('t', 0)) < ttl and not a.case:
            r['sale_survived'] = ent['s']; r['sale_scheduled'] = ent.get('n', 0)
            continue
        if budget <= 0:
            break
        dks = _fetch(session, case)
        fetched += 1; budget -= 1
        if dks is not None:
            surv, sched, done = _count(dks)
            r['sale_survived'] = surv; r['sale_scheduled'] = sched
            cache[case] = {'s': surv, 'n': sched, 'd': done, 't': now}
            changed += 1
            if a.case:
                print(f'{case}: survived {surv} sale(s), {sched} scheduled, {done} completed')
        time.sleep(0.25)
        if fetched % 25 == 0:
            json.dump(cache, open(CACHE, 'w', encoding='utf-8'))
            json.dump(leads, open(path, 'w', encoding='utf-8'))
            print(f'  ... {fetched} fetched, {changed} updated')

    json.dump(cache, open(CACHE, 'w', encoding='utf-8'))
    if changed:
        json.dump(leads, open(path, 'w', encoding='utf-8'))
    stallers = sum(1 for r in leads if (r.get('sale_survived') or 0) >= 2)
    print(f'sale_history: {changed} updated, {fetched} fetched live. '
          f'{stallers} serial stallers (survived >=2 sales) flagged.')


if __name__ == '__main__':
    main()
