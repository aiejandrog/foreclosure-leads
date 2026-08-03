"""docket.py — pull a full Miami-Dade civil docket from the Clerk's OCS API, on demand.

Born 2026-08-03 on the Martin condo (2026-003288-CA-01 / 2026-020206-CC-25): the browser view of
OCS renders an empty shell to automated browsers, but the JSON API behind it answers plain
requests. This is the same encrypt->GetSingleCaseResult dance _lp4_ocs.py and sibling_cases.py
already use, packaged as a CLI so the next "what actually happened on this case?" takes one
command instead of a scraping session.

Usage:
    python docket.py 2026-020206-CC-25                 # print the docket, newest last
    python docket.py 2026-020206-CC-25 2026-003288-CA-01 --json out.json
Notes:
    Miami-Dade only (Broward/Palm Beach clerks are different systems). Read-only, one polite
    request pair per case. Document PDFs are NOT downloadable this way — view them free on
    https://www2.miamidadeclerk.gov/ocs/ by searching the case number.
"""
import argparse
import json
import sys
import time

import requests

H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36',
     'Referer': 'https://www2.miamidadeclerk.gov/ocs/'}
BASE = 'https://www2.miamidadeclerk.gov/ocs/api/CaseInfo'


def pull(case):
    r = requests.get(f'{BASE}/encrypt/{case}', headers=H, timeout=30)
    r.raise_for_status()
    qs = r.json()['qs']
    time.sleep(0.5)
    r2 = requests.post(f'{BASE}/GetSingleCaseResult?qs=' + qs,
                       headers={**H, 'Content-Type': 'application/json'}, data='""', timeout=30)
    r2.raise_for_status()
    return r2.json()


def show(case, j):
    print(f'\n======== {case} ========')
    print(f"style: {j.get('caseStyle', '')}")
    print(f"type:  {j.get('caseType', '')} | status: {j.get('caseStatus', '')} | filed: {str(j.get('filingDate', ''))[:10]}")
    for p in (j.get('parties') or []):
        att = f"  (atty: {p.get('leadAttName')})" if p.get('leadAttName') else ''
        print(f"  PARTY: {p.get('partyTypeDesc', '?'):<18} {p.get('partyName', '')}{att}")
    dk = sorted(j.get('dockets') or [], key=lambda x: str(x.get('oDate') or x.get('eventDate') or ''))
    print(f'  --- {len(dk)} docket entries ---')
    for x in dk:
        dt = str(x.get('eventDate') or x.get('oDate') or '')[:10]
        desc = (x.get('docketDescrition') or '').strip()   # [sic] — the API misspells it
        cmt = str(x.get('comments') or '').strip().replace('\n', ' / ')[:150]
        line = f'  {dt}  {desc}'
        if cmt and cmt.lower() != desc.lower():
            line += f'  :: {cmt}'
        print(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cases', nargs='+', help='Miami-Dade case number(s), e.g. 2026-020206-CC-25')
    ap.add_argument('--json', default='', help='also dump the raw records to this file')
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    out = {}
    for c in args.cases:
        try:
            out[c] = pull(c)
            show(c, out[c])
        except Exception as e:
            print(f'  {c}: FAILED — {str(e)[:200]}')
    if args.json and out:
        json.dump(out, open(args.json, 'w', encoding='utf-8'), indent=1, default=str)
        print(f'\nraw JSON -> {args.json}')


if __name__ == '__main__':
    main()
