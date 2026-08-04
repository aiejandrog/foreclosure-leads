#!/usr/bin/env python3
"""cash_buyers.py — build the proven-auction-buyer list from court records.

WHY: GAMEPLAN.md line 45 — "buyer names on certificates of title = proven, active auction buyers."
Every other prospect list for this business is a guess. A certificate of title is not: it names the
party that actually WON a foreclosure auction and paid cash, on a date, for a known amount. That is
the exact audience for an Auction Bid Brief, and it is the only list that cannot be bought.

HOW: for any case whose sale date has passed, the Miami-Dade OCS case record carries the winner as
a party with partyTypeCode 'TPB' (Third Party Bidder), and the docket carries the Certificate of
Title with its date. When the sale went back to the plaintiff there is no TPB — that is a real
finding too (it tells you the bank took it), so we record it rather than dropping the case.

  python cash_buyers.py --days 90            # every past-auction case we know about
  python cash_buyers.py --days 90 --refresh  # re-pull cases already cached
  python cash_buyers.py --report             # ranked buyer list from the cache

NOTE ON COVERAGE: this reads cases DEALFLOW already tracks. The board naturally sheds a property
once it sells, so the honest yield is "every sale we watched", not "every sale in the county".
Full-county coverage needs the realforeclose results view, which is behind an operator login —
see the automation note in the report footer.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop', 'DEALFLOW',
                     'Foreclosure Lead Tracker.html')
CACHE = os.path.join(HERE, 'cash_buyers.json')
OCS = 'https://www2.miamidadeclerk.gov/ocs/'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

# A plaintiff take-back is not a cash buyer. These never belong on an outreach list.
INSTITUTIONAL = re.compile(
    r'\b(BANK|MORTGAGE|N\.?A\.?|NATIONAL ASSOC|FEDERAL|FANNIE|FREDDIE|HUD|SECRETARY|'
    r'TRUSTEE FOR|SERVICING|LOAN|CREDIT UNION|ASSOCIATION\b|CONDOMINIUM|HOMEOWNERS)\b', re.I)


def _d(s):
    for f in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(str(s).strip(), f).date()
        except (ValueError, TypeError):
            continue
    return None


def _load_board():
    txt = open(BOARD, encoding='utf-8', errors='ignore').read()
    m = re.search(r'const RAW\s*=\s*(\[.*?\]);\s*\n', txt, re.S)
    return json.loads(m.group(1)) if m else []


def _ocs(session, case):
    h = {'User-Agent': UA, 'Referer': OCS}
    try:
        r = session.get(f'{OCS}api/CaseInfo/encrypt/{case}', headers=h, timeout=30)
        qs = (r.json() or {}).get('qs')
        if not qs:
            return None
        time.sleep(0.35)
        r2 = session.post(f'{OCS}api/CaseInfo/GetSingleCaseResult?qs={qs}',
                          headers={**h, 'Content-Type': 'application/json'}, data='""', timeout=30)
        return r2.json()
    except Exception:
        return None


def extract_buyer(rec):
    """(buyer_name, cert_title_date, sale_date) from an OCS case record."""
    if not isinstance(rec, dict):
        return None, '', ''
    buyer = ''
    for p in (rec.get('parties') or []):
        if str(p.get('partyTypeCode') or '').upper() == 'TPB' or \
           'THIRD PARTY' in str(p.get('partyTypeDesc') or '').upper():
            buyer = (p.get('partyName') or '').strip()
            break
    cert = sale = ''
    for x in (rec.get('dockets') or []):
        desc = str(x.get('docketDescrition') or '')
        dt = str(x.get('eventDate') or '')[:10]
        if not cert and re.search(r'certificate of title', desc, re.I):
            cert = dt
        if not sale and re.search(r'certificate of sale|foreclosure sale', desc, re.I):
            sale = dt
    return buyer, cert, sale


def load_cache():
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE, encoding='utf-8')) or {}
        except Exception:
            return {}
    return {}


def save_cache(d):
    tmp = CACHE + '.tmp'
    json.dump(d, open(tmp, 'w', encoding='utf-8'), indent=1, sort_keys=True)
    os.replace(tmp, CACHE)


def report(cache):
    from collections import defaultdict
    buyers = defaultdict(list)
    takebacks = 0
    for case, v in cache.items():
        b = (v.get('buyer') or '').strip()
        if not b:
            takebacks += 1
            continue
        buyers[b].append(v)
    print(f'\n{len(buyers)} distinct third-party buyers across {len(cache)} watched sales '
          f'({takebacks} went back to the plaintiff / no third-party bidder)\n')
    rows = sorted(buyers.items(), key=lambda kv: -len(kv[1]))
    print(f'{"BUYS":>4}  {"BUYER":44} {"LATEST":11} {"PROPERTY"}')
    print('-' * 108)
    for name, hits in rows:
        h = sorted(hits, key=lambda x: x.get('cert') or '', reverse=True)[0]
        inst = ' [institutional]' if INSTITUTIONAL.search(name) else ''
        print(f'{len(hits):>4}  {name[:44]:44} {str(h.get("cert") or "-"):11} '
              f'{str(h.get("addr") or "")[:38]}{inst}')
    real = [n for n, _ in rows if not INSTITUTIONAL.search(n)]
    print(f'\n{len(real)} look like genuine private/investor buyers (institutional names filtered).')
    if real:
        print('Outreach-ready names:')
        for n in real:
            print(f'   - {n}')
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--days', type=int, default=90)
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--limit', type=int, default=200)
    ap.add_argument('--mature-days', type=int, default=21,
                    help='wait this long after a sale before looking for the certificate of title '
                         '(observed: 20 days on the Martin condo)')
    a = ap.parse_args()

    cache = load_cache()
    if a.report:
        report(cache)
        return 0

    today = datetime.date.today()
    cut = today - datetime.timedelta(days=a.days)
    targets = []

    # ARCHIVE FIRST. The board sheds a property the moment it sells, so sourcing targets from it
    # capped this tool at "sales still visible today" — 15 of an entire summer on 2026-08-04.
    # auction_archive.py remembers every sale we ever watched, which is what lets the buyer list
    # build itself over time instead of being re-derived from a shrinking window.
    try:
        import auction_archive
        arc = auction_archive.load()
    except Exception:
        arc = {}
    for case, v in arc.items():
        dt = _d(v.get('auction'))
        if not dt or not re.search(r'-(CA|CC)-', case):
            continue
        # Only cases whose certificate of title has had time to issue (Martin: sale 05/27 ->
        # cert 06/16, twenty days). Checking sooner caches a false "no buyer".
        if cut <= dt <= (today - datetime.timedelta(days=a.mature_days)):
            targets.append({'case': case, 'auction': v.get('auction'), 'addr': v.get('addr'),
                            'value': v.get('value'), 'judg': v.get('judg')})
    if not targets:      # archive not seeded yet — fall back to whatever the board still shows
        for r in _load_board():
            dt = _d(r.get('auction'))
            case = (r.get('case') or '').strip()
            if dt and case and re.search(r'-(CA|CC)-', case) \
                    and cut <= dt <= (today - datetime.timedelta(days=a.mature_days)):
                targets.append(r)
    if not a.refresh:
        targets = [r for r in targets if r.get('case') not in cache]
    targets = targets[:a.limit]
    print(f'{len(targets)} matured past-auction case(s) to check '
          f'(sold {cut} .. {today - datetime.timedelta(days=a.mature_days)}; '
          f'archive holds {len(arc)})')

    s = requests.Session()
    found = 0
    for i, r in enumerate(targets, 1):
        case = r['case']
        rec = _ocs(s, case)
        buyer, cert, sale = extract_buyer(rec)
        cache[case] = {'buyer': buyer, 'cert': cert, 'sale': sale,
                       'auction': r.get('auction'), 'addr': r.get('addr'),
                       'value': r.get('value'), 'judg': r.get('judg'),
                       'checked': today.isoformat()}
        if buyer:
            found += 1
            print(f'  [{i}/{len(targets)}] {case}  BUYER: {buyer[:44]}  cert {cert}')
        else:
            print(f'  [{i}/{len(targets)}] {case}  no third-party bidder (plaintiff take-back)')
        if i % 10 == 0:
            save_cache(cache)
        time.sleep(0.4)
    save_cache(cache)
    print(f'\nDONE: {found} third-party buyer(s) found -> {os.path.basename(CACHE)}')
    report(cache)
    return 0


if __name__ == '__main__':
    sys.exit(main())
