#!/usr/bin/env python3
"""lp_values.py — put a PRICE on the lis pendens parcels lp_resolve.py located.

WHY THIS EXISTS, AND WHY IT IS NOT OPTIONAL
lp_resolve.py answers "where is this filing?" — it matches the legal description to a parcel and
gives us a street address and a folio. It does not answer "what is it worth?", and on the board
those are two completely different jobs:

  * The funnel ranks every outreach stage by NET EQUITY, best first. That is the whole premise —
    call the owner with $400k of equity before the one with $8k. Net equity is derived from the
    parcel's value, so a lead carrying value=0 computes to zero equity and sorts DEAD LAST in
    every stage. Resolving 85 addresses and stopping there would have quietly buried all 85 at
    the bottom of the very list they were resolved to join.
  * A value-less row also cannot render a value chip, a deal verdict, or comps.

So: read the folios lp_resolve already found, ask the same free Miami-Dade parcel layer what each
one is assessed at, and write the numbers back into lp_addresses.json. lp_leads.py then carries
them onto the board.

NO API KEY, NO CAPTCHA, NO COST. Same unauthenticated MDC.PaGis endpoint the resolver uses.
Only folios already in lp_addresses.json are queried, so this can never wander off the lead set.

WHICH ENDPOINT, AND WHY NOT THE ONE THE RESOLVER USES. The MDC.PaGis layer lp_resolve queries
DECLARES value columns (TOTAL_VAL_CUR, ASSESSED_VAL_CUR) but serves them NULL — verified live on
all 85 folios: beds, baths, heated area and year built come back populated, every value comes back
None. So this uses the Property Appraiser's own proxy instead, the same GetPropertySearchByFolio
call foreclosure_leads.py already enriches Miami-Dade auction leads with. One folio per request,
no key, no captcha.

WHICH NUMBER WE USE. The market/just value out of Assessment.AssessmentInfos[].TotalValue — the
identical field foreclosure_leads.py stores as `market_value`, so an LP lead and an auction lead
are priced on the same basis and the funnel can rank them against each other honestly. Assessed
value is deliberately NOT used: Save Our Homes caps assessed on long-held homesteads, which
understates a property by six figures and would make a great lead look marginal.

Run:  python lp_values.py              # fill values for every high-confidence resolved parcel
      python lp_values.py --all        # include medium/low rows too (advisory addresses)
      python lp_values.py --dry-run    # show what it would fetch, write nothing
"""
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ADDR = os.path.join(HERE, 'lp_addresses.json')
CACHE = os.path.join(HERE, '_lp_values_cache.json')

PA = 'https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx'

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0',
                  'Referer': 'https://apps.miamidadepa.gov/PropertySearch/'})


def _has_homestead(benefits):
    return any('HOMESTEAD' in str(b.get('Description', '')).upper() for b in (benefits or []))


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def _fetch_one(folio):
    """One folio -> the fields the board needs. Returns {} on any failure; the caller records that
    as an explicit miss so a re-run does not re-ask the county for the same dead parcel daily."""
    try:
        d = S.get(PA, params={'Operation': 'GetPropertySearchByFolio',
                              'clientAppName': 'PropertySearch',
                              'folioNumber': folio}, timeout=25).json()
    except Exception as e:
        print('  folio %s failed: %s' % (folio, str(e)[:90]))
        return {}
    try:
        pi = d.get('PropertyInfo') or {}
        infos = (d.get('Assessment') or {}).get('AssessmentInfos') or []
        mkt = next((a['TotalValue'] for a in infos if a.get('TotalValue')), 0)
        benefits = (d.get('Benefit') or {}).get('BenefitInfos') or []
        owners = [o.get('Name', '') for o in (d.get('OwnerInfos') or []) if o.get('Name')]
        return {
            'value': int(mkt or 0),
            'hs': _has_homestead(benefits),
            'beds': pi.get('BedroomCount') or 0,
            'baths': pi.get('BathroomCount') or 0,
            'sqft': pi.get('BuildingHeatedArea') or 0,
            'built': pi.get('YearBuilt') or 0,
            'dor': pi.get('DORDescription') or '',
            'paOwners': '; '.join(owners),
        }
    except Exception as e:
        print('  folio %s parse failed: %s' % (folio, str(e)[:90]))
        return {}


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    every = '--all' in args

    addrs = _load(ADDR, {})
    if not addrs:
        print('no lp_addresses.json — run lp_resolve.py first. Nothing to price.')
        return

    cache = _load(CACHE, {})
    # Only rows that actually carry a folio. medium/low rows are included only with --all, because
    # pricing an address we are not confident in dresses a guess up as a analyzed deal.
    want = {}
    for case, rec in addrs.items():
        folio = str((rec or {}).get('folio') or '').strip()
        if not folio:
            continue
        if not every and str(rec.get('confidence') or '') != 'high':
            continue
        want[case] = folio

    todo = sorted({f for f in want.values() if f not in cache})
    print(f'{len(want)} resolved parcel(s) in scope · {len(todo)} need a value '
          f'({len(want) - len(todo)} cached)')

    if dry:
        for f in todo[:12]:
            print('   would fetch folio', f)
        if len(todo) > 12:
            print(f'    ... and {len(todo)-12} more')
        return

    # One folio per request — the PA proxy has no bulk mode. Paced so a daily cloud run stays a
    # polite neighbour; 85 folios is ~40 seconds.
    for i, f in enumerate(todo, 1):
        cache[f] = _fetch_one(f)          # {} on failure = an explicit, cached miss
        if i % 20 == 0 or i == len(todo):
            print(f'  {i}/{len(todo)} folios')
        time.sleep(0.4)

    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=0)

    filled = 0
    for case, folio in want.items():
        a = cache.get(folio) or {}
        if not a.get('value'):
            continue
        rec = addrs[case]
        for k in ('value', 'hs', 'beds', 'baths', 'sqft', 'built', 'dor', 'paOwners'):
            if a.get(k) not in (None, ''):
                rec[k] = a[k]
        filled += 1

    json.dump(addrs, open(ADDR, 'w', encoding='utf-8'), indent=1)
    vals = [addrs[c].get('value', 0) for c in want if addrs[c].get('value')]
    print(f'\nlp_addresses.json updated — {filled} parcel(s) priced')
    if vals:
        vals.sort()
        print(f'  median ${vals[len(vals)//2]:,} · range ${vals[0]:,} to ${vals[-1]:,}')
    print('Rebuild to carry them onto the board:  python lp_leads.py && python -c "import json,'
          'foreclosure_leads as F; F.make_tracker(json.load(open(\'leads_final.json\',encoding=\'utf-8\')))"')


if __name__ == '__main__':
    main()
