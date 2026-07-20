"""comps.py — radius comparable-sales engine for Broward + Palm Beach leads.

Sources the FL DOR statewide cadastral (the same public ArcGIS layer fl_cadastral already uses):
per lead it takes the parcel centroid, then pulls nearby residential parcels with a recent sale
(same county, sqft ±45%, sale year >= 2024, price > $50k), drops non-arm's-length outliers by
$/sqft, and prices the subject at median comp $/sqft x subject sqft. Writes comps.json
{case: {arv, psf, n, comps[3]}} (gitignored). make_tracker merges it as r.arv / r.arvconf /
r.comps. Miami-Dade is intentionally skipped for now — the statewide roll has no usable recent
sale years there (verified 2026-07-20: Hialeah returns 0 at any radius/year), so MD gets an
honest 'comps pending' instead of a fake number.

New-only by default (cached cases kept); --all re-computes; --limit N caps new lookups per run.
"""
import argparse, json, os, re, sys, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'comps.json')
LAYER = ('https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/'
         'Florida_Statewide_Cadastral/FeatureServer/0/query')
UA = 'foreclosure-leads/1.1 (comps)'
COUNTY_NO = {'BROWARD': 16, 'PALM BEACH': 60}
RES_UC = "('001','002','003','004','008','009')"
PSF_FLOOR, PSF_CAP = 40, 1000          # $/sqft outside this = non-arm's-length / data noise

_S = requests.Session(); _S.headers.update({'User-Agent': UA, 'Referer': 'https://www.arcgis.com/'})


def _q(params, tries=3, url=None):
    params = dict(params, f='json')
    last = None
    for i in range(tries):
        try:
            r = _S.get(url or LAYER, params=params, timeout=45)
            r.raise_for_status()
            j = r.json()
            if j.get('error'):
                raise RuntimeError(str(j['error'])[:120])
            return j
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def _centroid(geom):
    ring = (geom.get('rings') or [[]])[0]
    if not ring:
        return None
    return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))


def _folio_of(r):
    f = re.sub(r'\D', '', str(r.get('folio') or ''))
    if not f:
        m = re.search(r'URL_Folio=(\d+)', r.get('pa', '') or '')
        f = m.group(1) if m else ''
    return f


CENT = ('https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/'
        'Florida_Statewide_Parcel_Centroid_Version/FeatureServer/0/query')


def _subjects(folios):
    """Batch-enrich up to 40 folios in ONE centroid-layer query (sqft + point geometry) — replaces
    the per-lead polygon query that made the comp pass take ~30s/lead against the throttled host."""
    out = {}
    for i in range(0, len(folios), 40):
        chunk = folios[i:i + 40]
        j = _q({'where': 'PARCEL_ID IN (' + ','.join(f"'{f}'" for f in chunk) + ')',
                'outFields': 'PARCEL_ID,TOT_LVG_AR,ACT_YR_BLT',
                'returnGeometry': 'true', 'outSR': '4326', 'resultRecordCount': 40}, url=CENT)
        for f in j.get('features', []):
            a = f['attributes']; g = f.get('geometry') or {}
            out[a.get('PARCEL_ID')] = {'sqft': a.get('TOT_LVG_AR') or 0,
                                       'yr': a.get('ACT_YR_BLT') or 0,
                                       'c': (g.get('x'), g.get('y')) if g.get('x') else None}
        time.sleep(0.3)
    return out


def _comps(co_no, c, sqft, dist):
    lo, hi = (sqft * 0.55 or 0, sqft * 1.45 or 999999)
    where = (f"CO_NO={co_no} AND DOR_UC IN {RES_UC} AND SALE_YR1>=2024 AND SALE_PRC1>50000 "
             f"AND TOT_LVG_AR>={lo:.0f} AND TOT_LVG_AR<={hi:.0f}")
    j = _q({'where': where,
            'outFields': 'PARCEL_ID,PHY_ADDR1,PHY_CITY,SALE_PRC1,SALE_YR1,TOT_LVG_AR,ACT_YR_BLT',
            'geometry': f'{c[0]},{c[1]}', 'geometryType': 'esriGeometryPoint', 'inSR': '4326',
            'distance': dist, 'units': 'esriSRUnit_StatuteMile',
            'spatialRel': 'esriSpatialRelIntersects',
            'orderByFields': 'SALE_YR1 DESC', 'resultRecordCount': 14, 'returnGeometry': 'false'})
    out = []
    for x in j.get('features', []):
        a = x['attributes']
        if not a.get('TOT_LVG_AR'):
            continue
        psf = a['SALE_PRC1'] / a['TOT_LVG_AR']
        if not (PSF_FLOOR <= psf <= PSF_CAP):
            continue                                   # distressed / non-arm's-length / noise
        out.append({'addr': (a.get('PHY_ADDR1') or '').strip(),
                    'price': round(a['SALE_PRC1']), 'yr': a.get('SALE_YR1'),
                    'sqft': a.get('TOT_LVG_AR'), 'psf': round(psf)})
    return out


def compute(sub, co_no):
    if not sub or not sub['c'] or not sub['sqft']:
        return None
    comps = _comps(co_no, sub['c'], sub['sqft'], 0.75)
    dist_used = 0.75
    if len(comps) < 3:
        comps = _comps(co_no, sub['c'], sub['sqft'], 1.5)
        dist_used = 1.5
    if not comps:
        return None
    # trimmed median $/sqft — drop the extreme 15% at both ends so one distressed flip sale
    # (or one trophy outlier) can't drag the number; display the 3 comps closest to that median.
    psfs = sorted(c['psf'] for c in comps)
    k = max(1, round(len(psfs) * 0.15))
    core = psfs[k:-k] if len(psfs) > 2 * k + 1 else psfs
    med = core[len(core) // 2]
    arv = round(med * sub['sqft'])
    show = sorted(comps, key=lambda c: abs(c['psf'] - med))[:3]
    return {'arv': arv, 'psf': med, 'n': len(comps), 'dist': dist_used,
            'conf': 'ok' if len(comps) >= 3 else 'low',
            'comps': show}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    cache = {}
    if os.path.exists(OUT):
        try: cache = json.load(open(OUT, encoding='utf-8'))
        except Exception: cache = {}

    todo = []
    for fn, co_key in [('broward_leads.json', 'BROWARD'), ('palmbeach_leads.json', 'PALM BEACH')]:
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            continue
        for r in json.load(open(p, encoding='utf-8')):
            case = r.get('case', '')
            if not case or (not args.all and cache.get(case)):
                continue
            f = _folio_of(r)
            if f:
                todo.append((case, f, COUNTY_NO[co_key]))
    if args.limit:
        todo = todo[:args.limit]
    print(f'{len(todo)} leads to comp ({len(cache)} cached)')
    if not todo:
        json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=0)
        return

    subs = _subjects([f for _, f, _ in todo])          # ONE batch for all subjects (~40/call)
    print(f'subjects enriched: {len(subs)}/{len(todo)}')

    ok = fail = 0
    for case, folio, co_no in todo:
        try:
            res = compute(subs.get(folio), co_no)
        except Exception as e:
            print(f'  {case}: error {str(e)[:70]}')
            res = None
        if res:
            cache[case] = res
            ok += 1
        else:
            fail += 1
        if (ok + fail) % 20 == 0:
            print(f'  {ok} comped / {fail} without comps...')
        time.sleep(0.25)      # polite — public ArcGIS host throttles bursts

    json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=0)
    print(f'comps.json: {len(cache)} total ({ok} new, {fail} without comps)')


if __name__ == '__main__':
    main()
