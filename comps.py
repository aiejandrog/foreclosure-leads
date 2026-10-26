"""comps.py — radius comparable-sales engine for all three counties.

Broward + Palm Beach: the FL DOR statewide cadastral (public ArcGIS). Per lead: parcel centroid,
then nearby residential parcels with a recent sale (same county, sqft ±45%, sale year >= 2024,
price > $50k), non-arm's-length outliers dropped by $/sqft, subject priced at trimmed-median
comp $/sqft x subject sqft.

Miami-Dade: the county's OWN MD_ComparableSales service (gisweb.miamidade.gov, MDC.PaGis layer)
— found 2026-07-20 after the statewide roll proved to carry no usable MD sale years. Strictly
better data than the statewide layer: per-parcel sale slots carry a QUALIFIED-sale flag
(QU_FLG_1='Q' = arm's length as classified by the PA itself), so the MD comp pool is pre-filtered
to genuine market sales. Coordinates are FL State Plane East feet (X_COORD/Y_COORD), distances
queried in feet. Sqft = BUILDING_HEATED_AREA; use-code match = subject's own 2-digit DOR prefix.

Writes comps.json {case: {arv, psf, n, jv, comps[3]}} (committed as seed). `jv` is the FDOR
just value from the same subject query; it is not an input to the $/sqft median. make_tracker
merges arv / arvconf / comps, and copies `jv` onto a row only when that row's county value is
still 0.

Parcel ids are queried lettered-form first. Broward condo ids ('514205AA0390') miss the
centroid when stripped to digits. A clean miss is remembered for 7 days in comps_miss.json
(case, reason, ids tried — no address) so --limit is not spent on the same rows every night.
A new parcel id on the row is tried immediately.

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


# A no-comp answer is remembered so --limit is not spent on the same unjoined parcel
# every night. Case number, reason, and the parcel ids tried — never an address.
MISS = os.path.join(HERE, 'comps_miss.json')
MISS_TTL_S = 7 * 86400


def parcel_forms(folio):
    """PARCEL_ID candidates, lettered form first. See value_join.parcel_forms."""
    import value_join
    return value_join.parcel_forms(folio)


def row_forms(r):
    import value_join
    return value_join.row_forms(r)


def miss_blocks(entry, forms, now, ttl=MISS_TTL_S):
    """True when this case was tried recently with the same (or a wider) id set.

    A later folio repair — stub_resolve, or the lettered id restored on the row —
    is not blocked: the new id was not in `forms` the night it missed.
    """
    if not isinstance(entry, dict):
        return False
    try:
        age = float(now) - float(entry.get('t') or 0)
    except (TypeError, ValueError):
        return False
    if age < 0 or age >= ttl:
        return False
    tried = entry.get('forms') or []
    if not isinstance(tried, list) or not tried:
        return True
    return set(forms) <= set(str(x) for x in tried)


def select_todo(groups, cache, misses, now, limit=0):
    """(case, forms, co_no) still worth a comp lookup.

    groups is (co_no, rows) in county order. A case already in comps.json is done.
    A fresh miss is skipped. Letter-bearing ids sort ahead of digit-only ids so the
    nightly cap hits parcels the centroid layer can join (Broward condos) before it
    repeats digit-stripped misses. Order inside a rank is the order given.
    Miami-Dade (co_no 'MD') still requires a 13-digit folio.
    """
    import value_join
    cache = cache or {}
    misses = misses or {}
    ranked = []
    seq = 0
    for co_no, rows in groups:
        for r in rows or []:
            case = value_join.row_case(r)
            if not case or cache.get(case):
                continue
            forms = row_forms(r)
            if not forms:
                continue
            if co_no == 'MD' and not any(len(re.sub(r'\D', '', f)) == 13 for f in forms):
                continue
            if miss_blocks(misses.get(case), forms, now):
                continue
            rank = 0 if any(re.search(r'[A-Z]', f) for f in forms) else 1
            ranked.append((rank, seq, case, forms, co_no))
            seq += 1
    ranked.sort()
    todo = [(c, f, co) for _r, _s, c, f, co in ranked]
    if limit and limit > 0:
        todo = todo[:limit]
    return todo


CENT = ('https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/'
        'Florida_Statewide_Parcel_Centroid_Version/FeatureServer/0/query')

# Miami-Dade's own comparable-sales layer (county ArcGIS, not the statewide roll).
MD_LAYER = ('https://gisweb.miamidade.gov/arcgis/rest/services/'
            'MD_ComparableSales/MapServer/5/query')
MD_FT = {0.75: 3960, 1.5: 7920}        # state-plane distances are in FEET


def _subjects_md(folios):
    """Batch-enrich MD folios on the PaGis layer: state-plane point + heated sqft + DOR prefix.
    Same 40-per-call batching as the statewide path."""
    out = {}
    for i in range(0, len(folios), 40):
        chunk = folios[i:i + 40]
        j = _q({'where': 'FOLIO IN (' + ','.join(f"'{f}'" for f in chunk) + ')',
                'outFields': 'FOLIO,X_COORD,Y_COORD,BUILDING_HEATED_AREA,DOR_CODE_CUR',
                'returnGeometry': 'false', 'resultRecordCount': 40}, url=MD_LAYER)
        for f in j.get('features', []):
            a = f['attributes']
            out[a.get('FOLIO')] = {'sqft': a.get('BUILDING_HEATED_AREA') or 0,
                                   'dor2': (a.get('DOR_CODE_CUR') or '01')[:2],
                                   'c': (a.get('X_COORD'), a.get('Y_COORD'))
                                        if a.get('X_COORD') else None}
        time.sleep(0.3)
    return out


def _comps_md(c, sqft, dor2, dist_mi):
    """Qualified (QU_FLG_1='Q') recent sales near a state-plane point. The PA's own arm's-length
    classification replaces the $/sqft-only noise filter the statewide path needs."""
    lo, hi = (sqft * 0.55 or 0, sqft * 1.45 or 999999)
    where = (f"DOS_1>='20240101' AND PRICE_1>50000 AND QU_FLG_1='Q' "
             f"AND DOR_CODE_CUR LIKE '{dor2}%' "
             f"AND BUILDING_HEATED_AREA>={lo:.0f} AND BUILDING_HEATED_AREA<={hi:.0f}")
    j = _q({'where': where,
            'outFields': 'TRUE_SITE_ADDR,TRUE_SITE_CITY,DOS_1,PRICE_1,BUILDING_HEATED_AREA',
            'geometry': f'{c[0]},{c[1]}', 'geometryType': 'esriGeometryPoint',
            'distance': MD_FT[dist_mi], 'units': 'esriSRUnit_Foot',
            'spatialRel': 'esriSpatialRelIntersects',
            'orderByFields': 'DOS_1 DESC', 'resultRecordCount': 14,
            'returnGeometry': 'false'}, url=MD_LAYER)
    out = []
    for x in j.get('features', []):
        a = x['attributes']
        if not a.get('BUILDING_HEATED_AREA'):
            continue
        psf = a['PRICE_1'] / a['BUILDING_HEATED_AREA']
        if not (PSF_FLOOR <= psf <= PSF_CAP):
            continue
        out.append({'addr': (a.get('TRUE_SITE_ADDR') or '').strip(),
                    'price': round(a['PRICE_1']), 'yr': int(str(a.get('DOS_1') or '0')[:4] or 0),
                    'sqft': a.get('BUILDING_HEATED_AREA'), 'psf': round(psf)})
    return out


def _subjects(folios):
    """Batch-enrich up to 40 folios in ONE centroid-layer query (sqft + point geometry) — replaces
    the per-lead polygon query that made the comp pass take ~30s/lead against the throttled host."""
    out = {}
    for i in range(0, len(folios), 40):
        chunk = folios[i:i + 40]
        j = _q({'where': 'PARCEL_ID IN (' + ','.join(f"'{f}'" for f in chunk) + ')',
                'outFields': 'PARCEL_ID,TOT_LVG_AR,ACT_YR_BLT,JV',
                'returnGeometry': 'true', 'outSR': '4326', 'resultRecordCount': 40}, url=CENT)
        for f in j.get('features', []):
            a = f['attributes']; g = f.get('geometry') or {}
            pid = str(a.get('PARCEL_ID') or '')
            jv = a.get('JV')
            try:
                jv = int(round(float(jv))) if jv not in (None, '') else 0
            except (TypeError, ValueError):
                jv = 0
            out[pid] = {'sqft': a.get('TOT_LVG_AR') or 0,
                        'yr': a.get('ACT_YR_BLT') or 0,
                        'jv': jv,
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


def _subject_for(subs, forms):
    """The first candidate id the centroid actually returned, with a point and sqft."""
    for f in forms or []:
        hit = (subs or {}).get(f)
        if hit and hit.get('c') and hit.get('sqft'):
            return hit
    return None


def compute(sub, co_no, _fetch=None):
    """co_no: statewide county number for BW/PB, or the string 'MD' for the Miami-Dade layer.

    `_fetch` is a test seam (dist → comp list). The nightly path leaves it None and
    queries the public layers. JV on the subject is passed through; it is not an input
    to the $/sqft median.
    """
    if not sub or not sub['c'] or not sub['sqft']:
        return None
    if _fetch is not None:
        fetch = _fetch
    elif co_no == 'MD':
        fetch = lambda dist: _comps_md(sub['c'], sub['sqft'], sub.get('dor2', '01'), dist)
    else:
        fetch = lambda dist: _comps(co_no, sub['c'], sub['sqft'], dist)
    comps = fetch(0.75)
    dist_used = 0.75
    if len(comps) < 3:
        comps = fetch(1.5)
        dist_used = 1.5
    if not comps:
        return None
    # trimmed median $/sqft — drop the extreme 15% at both ends so one distressed flip sale
    # (or one trophy outlier) can't drag the number; display the 3 comps closest to that median.
    psfs = sorted(c['psf'] for c in comps)
    k = max(1, round(len(psfs) * 0.15))
    core = psfs[k:-k] if len(psfs) > 2 * k + 1 else psfs
    # HONEST MEDIAN. `core[len(core)//2]` is the median only when the core has an ODD length; on an
    # even core it returns the UPPER of the two middle values instead of averaging them. The trim
    # makes even cores the norm, not the exception — with the default pool of 14, k = round(2.1) = 2
    # and the core is 10 long. Measured on the live cache: 425 of 534 conf='ok' entries (80%) land on
    # an even core, 354 of them at length 10. The error is ONE-DIRECTIONAL — the ARV can only come out
    # at or above the true median, never below — and it multiplies by the subject's square footage
    # straight into _basisOf(), so it inflates "worth today", net equity, the 75%-of-value offer and
    # est. profit at once. It also makes the 2.5x county ceiling easier to clear, promoting ARVs that
    # should have been rejected into the basis.
    _m = len(core) // 2
    med = core[_m] if len(core) % 2 else (core[_m - 1] + core[_m]) / 2
    arv = round(med * sub['sqft'])
    show = sorted(comps, key=lambda c: abs(c['psf'] - med))[:3]
    try:
        jv = int(sub.get('jv') or 0)
    except (TypeError, ValueError):
        jv = 0
    return {'arv': arv, 'psf': med, 'n': len(comps), 'dist': dist_used,
            'conf': 'ok' if len(comps) >= 3 else 'low',
            'jv': jv,
            'comps': show}


def _load_list(path):
    if not os.path.exists(path):
        return []
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _load_miss():
    if not os.path.exists(MISS):
        return {}
    try:
        data = json.load(open(MISS, encoding='utf-8'))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_miss(misses):
    tmp = MISS + '.tmp'
    json.dump(misses, open(tmp, 'w', encoding='utf-8'), indent=0)
    os.replace(tmp, MISS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    cache = {}
    if os.path.exists(OUT):
        try: cache = json.load(open(OUT, encoding='utf-8'))
        except Exception: cache = {}
    misses = {} if args.all else _load_miss()

    # Miami-Dade rides its own county layer. Folio digits keep leading zeros — PaGis FOLIO is a
    # fixed 13-char string ('0420250580080'); stripping zeros would miss every Hialeah parcel.
    # select_todo keeps that 13-digit rule. --all recomputes cached cases and ignores misses.
    groups = [
        (COUNTY_NO['BROWARD'], _load_list(os.path.join(HERE, 'broward_leads.json'))),
        (COUNTY_NO['PALM BEACH'], _load_list(os.path.join(HERE, 'palmbeach_leads.json'))),
        ('MD', _load_list(os.path.join(HERE, 'leads_final.json'))),
    ]
    todo = select_todo(groups, {} if args.all else cache, misses, time.time(), args.limit)
    print(f'{len(todo)} leads to comp ({len(cache)} cached, {len(misses)} recent misses held)')
    if not todo:
        json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=0)
        return

    sw_folios, md_folios = [], []
    for _case, forms, co in todo:
        (md_folios if co == 'MD' else sw_folios).extend(forms)
    sw_folios = list(dict.fromkeys(sw_folios))
    md_folios = list(dict.fromkeys(md_folios))
    subs = _subjects(sw_folios) if sw_folios else {}   # ONE batch for all subjects (~40/call)
    if md_folios:
        subs.update(_subjects_md(md_folios))
    print(f'subjects enriched: {len(subs)}/{len(todo)}')

    ok = fail = 0
    for case, forms, co_no in todo:
        err = False
        try:
            sub = _subject_for(subs, forms)
            res = compute(sub, co_no)
        except Exception as e:
            print(f'  {case}: error {str(e)[:70]}')
            res = None
            err = True          # transport / layer error — retry next run, do not remember
        if res:
            cache[case] = res
            misses.pop(case, None)
            ok += 1
        else:
            fail += 1
            if not err:
                why = 'no-parcel' if _subject_for(subs, forms) is None else 'no-sales'
                misses[case] = {'t': time.time(), 'why': why, 'forms': list(forms)}
        if (ok + fail) % 20 == 0:
            json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=0)
            _save_miss(misses)
            print(f'  {ok} comped / {fail} without comps...')
        time.sleep(0.25)      # polite — public ArcGIS host throttles bursts

    json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=0)
    _save_miss(misses)
    print(f'comps.json: {len(cache)} total ({ok} new, {fail} without comps)')


if __name__ == '__main__':
    main()
