"""Apply listing_status_cache.json onto the three lead files immediately.

listing_status.py's enrich pass only writes each county file when that file's pass completes,
so a long backfill leaves the site status-less for ~40 minutes. This applies whatever the cache
already knows (folio-keyed) so a rebuild can publish partial data NOW. Safe to run while the
backfill is still going: the running pass dumps a superset later, and cached folios are skipped
(not re-fetched) by design, so the two writers always converge to the same values.
"""
import json
import os
import re
import time

base = os.path.dirname(os.path.abspath(__file__))
cache = json.load(open(os.path.join(base, 'listing_status_cache.json'), encoding='utf-8'))


def _folio(r):
    return (re.sub(r'\D', '', str(r.get('folio') or r.get('Folio') or ''))
            or re.sub(r'[^a-z0-9]', '', str(r.get('case') or r.get('Case #') or '').lower()))


now = time.time()
for fn in ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json'):
    p = os.path.join(base, fn)
    if not os.path.exists(p):
        continue
    leads = json.load(open(p, encoding='utf-8'))
    n = 0
    for r in leads:
        ent = cache.get(_folio(r))
        if ent and not r.get('zstatus'):
            r['zstatus'], r['zprice'], r['zdoz'] = ent['s'], ent.get('p', 0), ent.get('d', 0)
            n += 1
    if n:
        json.dump(leads, open(p, 'w', encoding='utf-8'), indent=1)
    print(f'{fn}: +{n} statuses applied from cache')
