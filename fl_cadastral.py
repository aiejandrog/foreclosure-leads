"""Statewide Florida property enrichment — one CORS-open API for EVERY county.

The unlock for multi-county DEALFLOW: the FL Dept. of Revenue "FDOR Cadastral" ArcGIS layer is public,
CORS-open (Access-Control-Allow-Origin: *), and covers all 67 counties (keyed by CO_NO). It returns owner,
just/market value, assessed/taxable, homestead, living-area sqft, year built, and the last two sales —
by parcel id or by address. So Broward, Palm Beach, and any FL county get owner+value with no per-county
Property Appraiser integration, and (being CORS-open) the same data can be fetched straight from the browser.

Gap vs a county PA: no beds/baths (the annual DOR roll doesn't carry them). Value is the certified roll
value (annual, not real-time) — fine for a leads tool.

    from fl_cadastral import enrich
    enrich(parcel_id='474331000240')                 # by parcel
    enrich(county='BROWARD', address='100 NW 3 CT')  # by address within a county
"""
import argparse, json, re
import requests

LAYER = ('https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/'
         'Florida_Statewide_Cadastral/FeatureServer/0/query')
UA = 'foreclosure-leads/1.1'

# FL DOR county numbers (from the cadastral's CO_NO). Broward=16 validated live; the tri-county set below
# is verified in the self-test. Add more as needed (query CO_NO=<n> for a known city to confirm).
COUNTY_NO = {   # FDOR CO_NO is alphabetical from 11 (11=Alachua, 13=Bay, 16=Broward...). Broward=16 validated live.
    'MIAMI-DADE': 53, 'MIAMIDADE': 53, 'DADE': 53,
    'BROWARD': 16,
    'PALM BEACH': 60, 'PALMBEACH': 60,
    'MONROE': 54, 'MARTIN': 52,
}
FLDS = ('PARCEL_ID,CO_NO,OWN_NAME,OWN_ADDR1,OWN_CITY,OWN_STATE,OWN_ZIPCD,'
        'PHY_ADDR1,PHY_CITY,PHY_ZIPCD,JV,AV_NSD,TV_NSD,JV_HMSTD,LND_VAL,LND_SQFOOT,'
        'TOT_LVG_AR,NO_BULDNG,ACT_YR_BLT,DOR_UC,SALE_PRC1,SALE_YR1,SALE_PRC2,SALE_YR2,S_LEGAL')

_S = requests.Session(); _S.headers.update({'User-Agent': UA, 'Referer': 'https://www.arcgis.com/'})


def _q(where, n=5, _tries=3):
    import time
    last = None
    for i in range(_tries):
        try:
            r = _S.get(LAYER, params={'where': where, 'outFields': FLDS, 'returnGeometry': 'false',
                                      'resultRecordCount': n, 'f': 'json'}, timeout=40)
            r.raise_for_status()
            j = r.json()
            if j.get('error'):
                raise RuntimeError(j['error'].get('message', 'query error'))
            return [f['attributes'] for f in j.get('features', [])]
        except Exception as e:
            last = e; time.sleep(2 * (i + 1))     # backoff — the free ArcGIS host throttles bursts
    raise last


def _norm(a):
    def money(k):
        v = a.get(k)
        try: return round(float(v)) if v not in (None, '') else 0
        except Exception: return 0
    mail = ' '.join(str(a.get(k, '') or '') for k in ('OWN_ADDR1', 'OWN_CITY', 'OWN_STATE', 'OWN_ZIPCD')).strip()
    site = ' '.join(str(a.get(k, '') or '') for k in ('PHY_ADDR1', 'PHY_CITY', 'PHY_ZIPCD')).strip()
    return {
        'parcel_id': a.get('PARCEL_ID', ''), 'county_no': a.get('CO_NO'),
        'owner': (a.get('OWN_NAME') or '').strip(),
        'site_addr': site, 'mail_addr': mail,
        'market_value': money('JV'), 'assessed_value': money('AV_NSD') or money('TV_NSD'),
        'land_value': money('LND_VAL'), 'lot_sqft': money('LND_SQFOOT'),
        'homestead': money('JV_HMSTD') > 0,
        'living_sqft': money('TOT_LVG_AR'), 'buildings': a.get('NO_BULDNG') or 0,
        'year_built': a.get('ACT_YR_BLT') or 0, 'use_code': a.get('DOR_UC', ''),
        'last_sale_price': money('SALE_PRC1'), 'last_sale_year': a.get('SALE_YR1') or 0,
        'legal': (a.get('S_LEGAL') or '').strip(),
    }


def enrich(parcel_id=None, county=None, address=None):
    """Return a normalized property dict (or None). Match by parcel id, else by county + address."""
    if parcel_id:
        pid = re.sub(r'\D', '', str(parcel_id))
        rows = _q("PARCEL_ID='%s'" % pid, 1)
        if rows: return _norm(rows[0])
        return None
    if county and address:
        cn = COUNTY_NO.get(re.sub(r'[^A-Z ]', '', county.upper()).strip())
        if not cn: raise ValueError('unknown county: %s' % county)
        up = address.upper().replace("'", "''")
        rows = _q("CO_NO=%d AND PHY_ADDR1 LIKE '%s%%'" % (cn, up), 8)
        return [_norm(r) for r in rows]
    raise ValueError('need parcel_id, or county + address')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--parcel', default='')
    ap.add_argument('--county', default='')
    ap.add_argument('--address', default='')
    a = ap.parse_args()
    if a.parcel:
        print(json.dumps(enrich(parcel_id=a.parcel), indent=1))
    elif a.county and a.address:
        for r in enrich(county=a.county, address=a.address):
            print(f"  {r['parcel_id']}  {r['owner'][:26]:26}  ${r['market_value']:>10,}  hs={'Y' if r['homestead'] else 'n'}  {r['site_addr']}")
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
