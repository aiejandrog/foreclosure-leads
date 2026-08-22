#!/usr/bin/env python
"""buybox_miamilakes — fill beds/baths/sqft for a ZIP set, then rank the 4BR buy-box.

WHY (2026-08-18, Jose's ask relayed by Alejandro): a buyer needs a 4-bedroom in Miami Lakes —
three kids, each wants a room, and renting a 4BR is brutal, so buying beats renting. The board
could not answer "show me the 4BRs": only 19% of leads carried a bedroom count, so the filter was
blind on 81% of the pipeline.

WHAT IT DOES
  1. Selects every board lead whose address falls in the target ZIPs (Miami Lakes + the Hialeah /
     Hialeah Gardens / Country Club edges people mean when they say "Miami Lakes").
  2. Fills beds / baths / sqft / built / value / homestead from the SAME free, keyless Miami-Dade
     PaGis endpoint lp_values.py uses (no API key, no captcha, no cost) — folio only, so
     Broward/PB rows in range are skipped honestly rather than guessed.
  3. Writes the enrichment back into the county lead files so the board carries it after the next
     rebuild, and caches per-folio so a re-run never re-asks the county for the same parcel.
  4. Prints a ranked buy-box: 4BR first, then 3BR-with-room-to-convert (>=1,700 sqft).

RUN
  python buybox_miamilakes.py                # enrich + rank
  python buybox_miamilakes.py --no-write     # rank only, touch nothing
"""
import argparse
import json
import os
import re
import sys
import time

import requests
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
TWIN = os.path.join(P.DEALFLOW_DIR, 'Foreclosure Lead Tracker.html')
CACHE = os.path.join(HERE, 'pa_property_cache.json')
PA = 'https://www.miamidade.gov/Apps/PA/PApublicServiceProxy/PaServicesProxy.ashx'
ZIPS = ('33014', '33015', '33016', '33018')
TARGET_BEDS = 4
CONVERTIBLE_SQFT = 1300          # a 3BR at/above this usually has a den or garage that becomes
                                 # bedroom 4. Started at 1700 and it excluded a 1,693 sf house in
                                 # Miami Lakes proper by SEVEN FEET — an arbitrary line beating a
                                 # real candidate is the filter failing, not the house.

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                '(KHTML, like Gecko) Chrome/126.0 Safari/537.36'})


def _load(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def board_rows():
    """The board's own merged payload — same source sheets_crm uses, so this can never disagree."""
    h = open(TWIN, encoding='utf-8').read()
    i = h.find('RAW = ')
    if i < 0:
        sys.exit('no RAW payload in the Desktop twin — run a build first')
    rows, _ = json.JSONDecoder().raw_decode(h, i + len('RAW = '))
    return rows


def in_target(r):
    a = (r.get('addr') or '').upper()
    return 'MIAMI LAKES' in a or any(z in a for z in ZIPS)


def fetch(folio):
    """One folio -> property facts. {} on any failure (recorded as a miss, not retried blindly)."""
    try:
        d = S.get(PA, params={'Operation': 'GetPropertySearchByFolio',
                              'clientAppName': 'PropertySearch',
                              'folioNumber': folio}, timeout=25).json()
        pi = d.get('PropertyInfo') or {}
        infos = (d.get('Assessment') or {}).get('AssessmentInfos') or []
        mkt = next((a['TotalValue'] for a in infos if a.get('TotalValue')), 0)
        return {
            'beds': pi.get('BedroomCount') or 0,
            'baths': pi.get('BathroomCount') or 0,
            'sqft': pi.get('BuildingHeatedArea') or 0,
            'built': pi.get('YearBuilt') or 0,
            'value': int(mkt or 0),
            'dor': pi.get('DORDescription') or '',
        }
    except Exception as e:
        print('  folio %s failed: %s' % (folio, str(e)[:80]))
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-write', action='store_true')
    a = ap.parse_args()

    rows = board_rows()
    targets = [r for r in rows if in_target(r)]
    print('Miami Lakes area (%s): %d lead(s) on the board' % ('/'.join(ZIPS), len(targets)))

    cache = _load(CACHE, {})
    # beds OR sqft — the first version only chased missing BEDS, so 3BRs with no square footage
    # never got enriched and the convertible list came back empty for the wrong reason.
    need = [r for r in targets
            if (not r.get('beds') or not r.get('sqft'))
            and re.sub(r'\D', '', str(r.get('folio') or ''))]
    print('missing a bedroom count: %d  (cached already: %d)'
          % (len(need), sum(1 for r in need if re.sub(r'\D', '', str(r.get('folio'))) in cache)))

    fetched = 0
    for r in need:
        folio = re.sub(r'\D', '', str(r.get('folio')))
        # a cached MISS ({}) is retried once — the county endpoint intermittently returns non-JSON,
        # and caching that forever would permanently blank a real parcel
        if cache.get(folio):
            got = cache[folio]
        else:
            got = fetch(folio)
            cache[folio] = got
            fetched += 1
            time.sleep(0.35)                      # courtesy pace on a free county endpoint
        if got:
            for k in ('beds', 'baths', 'sqft', 'built'):
                if got.get(k):
                    r[k] = got[k]
            if got.get('value') and not r.get('value'):
                r['value'] = got['value']
    if fetched and not a.no_write:
        json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=1)
    print('fetched %d new parcel(s) from the county (free PaGis endpoint)' % fetched)

    # ---- write the enrichment back so the board keeps it after the next rebuild ----------------
    if not a.no_write:
        by_case = {}
        for r in targets:
            if r.get('beds'):
                by_case[r.get('case')] = {k: r.get(k) for k in ('beds', 'baths', 'sqft', 'built')}
        touched = 0
        for fn in ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json', 'lp_leads.json'):
            p = os.path.join(HERE, fn)
            data = _load(p, None)
            if not isinstance(data, list):
                continue
            hit = 0
            for row in data:
                c = row.get('case') or row.get('Case #')
                if c in by_case:
                    for k, v in by_case[c].items():
                        if v:
                            row[k] = v
                    hit += 1
            if hit:
                json.dump(data, open(p, 'w', encoding='utf-8'), indent=1)
                touched += hit
                print('  %-22s %d row(s) enriched' % (fn, hit))
        print('wrote enrichment onto %d lead row(s)' % touched)

    # ---- the buy-box --------------------------------------------------------------------------
    def eq(r):
        v, j = float(r.get('value') or 0), float(r.get('judg') or 0)
        return (v - j) if (v and j) else (v if v else 0)

    four = [r for r in targets if float(r.get('beds') or 0) >= TARGET_BEDS]
    # county data carries -1 as a 'no record' sentinel; treat it as unknown, never as a size
    def _sf(r):
        v = float(r.get('sqft') or 0)
        return v if v > 0 else 0
    conv = [r for r in targets if float(r.get('beds') or 0) == 3 and _sf(r) >= CONVERTIBLE_SQFT]
    unknown = [r for r in targets if not r.get('beds')]

    def line(r):
        return ('%-46s %sBR/%-4s %5s sf  %s  val $%-9s owed %-11s eq ~$%s'
                % ((r.get('addr') or '')[:46], r.get('beds') or '?', str(r.get('baths') or '?'),
                   r.get('sqft') or '?', (r.get('auction') or 'no sale date')[:12],
                   format(int(float(r.get('value') or 0)), ','),
                   ('UNKNOWN' if r.get('ju') else format(int(float(r.get('judg') or 0)), ','))
                   if (r.get('judg') or r.get('ju')) else 'not posted',
                   format(int(eq(r)), ',')))

    print('\n=== 4+ BEDROOM (the buy-box) ===')
    for r in sorted(four, key=lambda x: -eq(x)):
        print(' ', line(r))
    print('\n=== 3BR, %s+ sqft (den/garage usually becomes bedroom 4) ===' % CONVERTIBLE_SQFT)
    for r in sorted(conv, key=lambda x: -eq(x)):
        print(' ', line(r))
    if unknown:
        print('\nstill unknown (no folio or county had no record): %d' % len(unknown))
    return {'four': four, 'conv': conv, 'targets': targets}


if __name__ == '__main__':
    main()
