"""Enrich Broward + Palm Beach lead JSONs with dor_desc (property type) from county appraisers.

Miami-Dade already gets dor_desc for free via county_leads.py's apps.miamidadepa.gov fetch, so
this script only touches broward_leads.json and palmbeach_leads.json.

Sources:
  Broward   -> bcpa.net/RecInfo.asp?URL_Folio={folio}   (use code in `XX-YY` cells)
  Palm Beach -> pbcpao.gov/Property/Details?parcelId={folio}  (use code + description inline)

Cached in property_types_cache.json keyed by folio — property type never changes, so a folio
we've enriched once is never fetched again on the daily refresh. Cache misses only happen for
brand-new leads. Fail-soft: any HTTP/parse error simply leaves dor_desc blank for that lead;
the tracker renders no chip for those rows and never breaks.

Run pattern (mirrors county_leads.py):
  python property_types.py                    # both counties
  python property_types.py --county broward   # one
  python property_types.py --county palmbeach

Wired into GHA workflow between county_leads.py and the final make_tracker rebuild.
"""

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'property_types_cache.json')

# Florida DOR use-code table (2-digit prefix). Sourced from FL DOR Form DR-500 / property
# appraiser manuals. Codes stable since ~2010; the two-digit prefix is what BCPA exposes in
# its `XX-YY` cell where YY is a Broward-specific subtype we don't need.
DOR_TABLE = {
    '00': 'Vacant Residential',
    '01': 'Single Family',
    '02': 'Mobile Home',
    '03': 'Multi-Family (< 10 units)',
    '04': 'Condominium',
    '05': 'Cooperative',
    '06': 'Retirement Home',
    '07': 'Boarding Home',
    '08': 'Multi-Family (10+ units)',
    '09': 'Undefined Residential',
    '10': 'Vacant Commercial',
    '11': 'Store, One Story',
    '12': 'Mixed Use (Store + Office/Residential)',
    '13': 'Department Store',
    '14': 'Supermarket',
    '15': 'Regional Shopping Center',
    '16': 'Community Shopping Center',
    '17': 'Office, One Story',
    '18': 'Office, Multi-Story',
    '19': 'Professional Building',
    '20': 'Airport / Bus Terminal',
    '21': 'Restaurant / Cafeteria',
    '22': 'Drive-in Restaurant',
    '23': 'Financial Institution',
    '24': 'Insurance Office',
    '25': 'Repair Service Shop',
    '26': 'Service Station',
    '27': 'Auto Sales / Repair',
    '28': 'Parking Lot / Garage',
    '29': 'Wholesale Outlet',
    '30': 'Florist / Greenhouse',
    '31': 'Drive-in Theater',
    '32': 'Enclosed Theater',
    '33': 'Nightclub / Bar',
    '34': 'Bowling / Skating',
    '35': 'Tourist Attraction',
    '38': 'Golf Course',
    '39': 'Hotel / Motel',
    '40': 'Vacant Industrial',
    '41': 'Light Industrial',
    '42': 'Heavy Industrial',
    '43': 'Lumber Yard / Sawmill',
    '44': 'Packing Plant',
    '48': 'Warehouse / Distribution',
    '49': 'Open Storage',
    '50': 'Improved Agricultural',
    '60': 'Grazing Land',
    '66': 'Orchard / Grove',
    '67': 'Poultry / Bee / Fish',
    '68': 'Dairy',
    '69': 'Ornamental / Nursery',
    '70': 'Vacant Institutional',
    '71': 'Church',
    '72': 'Private School',
    '73': 'Hospital',
    '74': 'Home for the Aged',
    '75': 'Orphanage',
    '76': 'Mortuary / Cemetery',
    '77': 'Club / Union Hall',
    '78': 'Sanitarium',
    '79': 'Cultural (Library, Museum)',
    '80': 'Undefined Government',
    '82': 'Forest / Park / Recreation',
    '83': 'Public School',
    '86': 'County Government',
    '87': 'State Government',
    '88': 'Federal Government',
    '89': 'Municipal Government',
    '90': 'Leasehold Interest',
    '91': 'Utility',
    '92': 'Mining / Petroleum',
    '93': 'Subsurface Rights',
    '94': 'Right-of-Way',
    '95': 'River / Lake / Submerged',
    '96': 'Sewage Disposal / Landfill',
    '97': 'Outdoor Recreation',
    '99': 'Non-Agricultural Acreage',
}

UA = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/125.0 Safari/537.36',
    'Accept-Encoding': 'gzip, deflate',
    'Accept': 'text/html,application/xhtml+xml',
}


def _load_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        return json.load(open(CACHE_PATH, encoding='utf-8'))
    except Exception:
        return {}


def _save_cache(cache):
    try:
        json.dump(cache, open(CACHE_PATH, 'w', encoding='utf-8'), indent=1)
    except Exception as e:
        print(f'  ! cache write failed: {e}', file=sys.stderr)


def _fetch(url, timeout=15):
    """Return decoded HTML or None on any error."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            return raw.decode('utf-8', 'ignore')
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError) as e:
        return None
    except Exception:
        return None


def _bcpa_use(folio):
    """Extract DOR use description from a BCPA property page. Returns text or ''."""
    html = _fetch(f'https://bcpa.net/RecInfo.asp?URL_Folio={folio}')
    if not html:
        return ''
    # Strip HTML tags -> pipe-delimited text (Broward wraps every cell in nested tables)
    text = re.sub(r'<[^>]+>', '|', html)
    text = re.sub(r'\|+', '|', text)
    text = re.sub(r'\s+', ' ', text)
    # Pattern from real pages: "| Use | ... | 01- | 01 |" — first 2-digit run after "Use " label
    m = re.search(r'\|\s*Use\s*\|.{0,120}?\|\s*(\d{2})[-\s]', text)
    if not m:
        return ''
    code = m.group(1)
    return DOR_TABLE.get(code, f'Use code {code}')


def _pbcpao_use(folio):
    """Extract property-use description from a PBCPAO property page. Returns text or ''."""
    html = _fetch(f'https://pbcpao.gov/Property/Details?parcelId={folio}')
    if not html:
        return ''
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)
    # PBCPAO renders "Property Use Code 0100 SINGLE FAMILY" (the dash appears as an entity or
    # unicode em-dash; be liberal on the separator).
    m = re.search(r'Property\s*Use\s*Code\s*(\d{4})[^\w]{1,6}([A-Z][A-Z \-/&]{3,60})', text)
    if not m:
        return ''
    code2 = m.group(1)[:2]
    label = m.group(2).strip().rstrip('Zoning').strip()
    # Prefer the site's own description (they know their local nuance); fall back to DOR table
    return label.title() if len(label) > 3 else DOR_TABLE.get(code2, f'Use code {code2}')


def enrich(county):
    """Enrich the given county's leads JSON in place. Skips leads that already have dor_desc."""
    fn = {'broward': 'broward_leads.json', 'palmbeach': 'palmbeach_leads.json'}[county]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fn)
    if not os.path.exists(path):
        print(f'  ! {fn} missing — skipping')
        return
    leads = json.load(open(path, encoding='utf-8'))
    cache = _load_cache()
    fetcher = _bcpa_use if county == 'broward' else _pbcpao_use
    prefix = 'BCPA' if county == 'broward' else 'PBCPAO'

    fetched = cached = skipped = 0
    for i, r in enumerate(leads):
        if r.get('dor_desc'):
            continue  # already enriched (rare — pre-existing field wins)
        folio = str(r.get('folio') or '').strip()
        if not folio:
            skipped += 1
            continue
        key = f'{county}:{folio}'
        if key in cache:
            r['dor_desc'] = cache[key]
            cached += 1
            continue
        desc = fetcher(folio)
        if desc:
            r['dor_desc'] = desc
            cache[key] = desc
            fetched += 1
            # Save cache every 20 fetches so an interrupted run doesn't lose all progress
            if fetched % 20 == 0:
                _save_cache(cache)
                print(f'  [{prefix}] {fetched} fetched · {cached} cached · {i+1}/{len(leads)}')
        else:
            skipped += 1
        # Polite pacing — county appraisers throttle aggressive scrapers
        time.sleep(0.15)

    _save_cache(cache)
    json.dump(leads, open(path, 'w', encoding='utf-8'), indent=1)
    print(f'{prefix}: {fetched} fetched, {cached} cache-hit, {skipped} skipped '
          f'(no folio / no data) — wrote {fn}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--county', choices=['broward', 'palmbeach', 'all'], default='all')
    args = ap.parse_args()
    counties = ['broward', 'palmbeach'] if args.county == 'all' else [args.county]
    for c in counties:
        enrich(c)


if __name__ == '__main__':
    main()
