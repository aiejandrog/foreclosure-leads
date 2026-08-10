#!/usr/bin/env python
"""carlos_deep_zones.py — deep door-inventory search for zones the foreclosure board has tapped out.

Why this exists (2026-08-10): Alejandro asked for "more properties in Tamiami and a deeper search
in West Miami" after the week-route run put 0 and 1 doors there. The audit showed the mortgage-
foreclosure board is genuinely dry in both rings — 19 Tamiami doors already routed, everything
else BK-stayed, condo'd, or dead. The untapped inventory is the county's OWN distress file:
code-enforcement cases (CCVIOL, the layer code_liens.py already trusts). Status 4 = recorded
lien accruing daily against the parcel; status 9 = the county is FORECLOSING on its lien. Those
owners are in real distress and are NOT on the mortgage-foreclosure board.

The pitch at these doors is different from foreclosure rescue — "the county recorded a lien on
your house / is suing to foreclose over fines" — so every list this script emits is labeled.

Quality gates (the wasted-knock filters):
  * individual owners only — an LLC's door is answered by a tenant, not the decision-maker
  * houses only — CONDO_FLAG + DOR_DESC from the parcel roll, not address heuristics
  * homestead exemption is the top rank signal — owner-occupied means the person who answers
    the door owns the problem
  * nothing already on the board (those are routed/excluded for their own reasons), nothing
    Carlos has already walked
"""
import datetime
import json
import math
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _carlos_route as CR              # noqa: E402
import carlos_week_routes as W          # noqa: E402

CCVIOL = ('https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/'
          'CCVIOL_gdb/FeatureServer/0/query')
PAGIS = ('https://gisweb.miamidade.gov/arcgis/rest/services/MD_ComparableSales/'
         'MapServer/5/query')
S = requests.Session()
S.headers.update({'User-Agent': 'foreclosure-leads/1.0 (deep-zone-doors)'})

ZONES = [  # name, lat, lng, radius_mi — same anchors the week packet used
    ('Tamiami',    25.7587, -80.4020, 3.0),
    ('West Miami', 25.7634, -80.2963, 3.0),
]
STATUS_LABEL = {'1': 'Open case', '4': 'RECORDED LIEN', '6': 'Civil',
                '8': 'Referred to compliance', '9': 'COUNTY FORECLOSING'}
PER_ZONE_CAP = 12          # Carlos knocks 8-15 doors a day; a longer list just goes stale

# an entity's door is answered by a tenant — the decision-maker is elsewhere.
# PaGis truncates TRUE_OWNER1, so suffixes like LLC can be cut off — the phrase tokens
# (BEGINNINGS, MINISTRIES...) catch business names that lost their suffix.
_ENTITY = re.compile(r'\b(LLC|L L C|INC|CORP|CORPORATION|HOLDINGS?|INVESTMENTS?|PROPERTIES|'
                     r'PARTNERS?|CAPITAL|VENTURES?|GROUP|TRUST(?!EE)|BANK|MORTGAGE|LP|LLP|'
                     r'ASSOC|COMPANY|CO\b|ENTERPRISES?|REALTY|HOMES|FUND|BEGINNINGS|'
                     r'MINISTR|CHURCH|IGLESIA|SERVICES|SOLUTIONS|DEVELOP)\b')

_SEV_HIGH = re.compile(r'MINIMUM HOUSING|UNSAFE|DEMOLI|STRUCTURE|WITHOUT|PERMIT|FORECLOSED|'
                       r'EMERGENCY|VACANT|ELECTRICAL|ROOF|PLUMBING')
_SEV_LOW = re.compile(r'TOW TRUCK|BOAT|HOUSE NUMBER|SIGN|ANIMAL|NOISE|TRAILER|\bRV\b')


def _severity(problem):
    p = str(problem or '').upper()
    if _SEV_HIGH.search(p):
        return 2
    if _SEV_LOW.search(p):
        return 0
    return 1                              # junk/overgrowth, cert-of-use, setbacks, zoning...


def _envelope(lat, lng, mi):
    dlat = mi / 69.05
    dlng = mi / (69.17 * math.cos(math.radians(lat)))
    return {'xmin': lng - dlng, 'ymin': lat - dlat, 'xmax': lng + dlng, 'ymax': lat + dlat,
            'spatialReference': {'wkid': 4326}}


def _ccviol_zone(lat, lng, mi):
    """All concern-status violation points inside the zone envelope, paged."""
    feats, offset = [], 0
    while True:
        p = {'where': "CASE_STATUS IN ('1','4','6','8','9')",
             'geometry': json.dumps(_envelope(lat, lng, mi)),
             'geometryType': 'esriGeometryEnvelope', 'inSR': 4326,
             'spatialRel': 'esriSpatialRelIntersects',
             'outFields': 'CASE_NUM,CASE_DATE,CASE_STATUS,STAT_DESC,ADDRESS,FOLIO,PROBLEM_DESC,'
                          'LN_RECDATE,LN_RECBOOK,LN_RECPAGE,LAST_ACTV',
             'outSR': 4326, 'returnGeometry': 'true', 'f': 'json',
             'resultOffset': offset, 'resultRecordCount': 2000}
        j = S.get(CCVIOL, params=p, timeout=60).json()
        if 'features' not in j:
            raise RuntimeError('CCVIOL error: %s' % str(j)[:200])
        feats += j['features']
        if not j.get('exceededTransferLimit'):
            return feats
        offset += 2000


PG_FIELDS = ('FOLIO,TRUE_OWNER1,TRUE_OWNER2,TRUE_SITE_ADDR_NO_UNIT,TRUE_SITE_CITY,'
             'TRUE_SITE_ZIP_CODE,CONDO_FLAG,DOR_DESC,'
             'TRUE_MAILING_ADDR1,TRUE_MAILING_CITY,TRUE_MAILING_STATE,LEGAL')


def pagis_batch(folios):
    """Parcel truth for many folios. POST, not GET — a 100-folio IN() blows the ArcGIS GET
    URL limit and the failure comes back as a silent non-'features' response. A failed chunk
    RAISES; a quiet partial join here becomes wrong doors downstream."""
    out = {}
    folios = list(folios)
    for i in range(0, len(folios), 50):
        chunk = folios[i:i + 50]
        where = "FOLIO IN (%s)" % ','.join("'%s'" % f for f in chunk)
        p = {'where': where, 'outFields': PG_FIELDS, 'returnGeometry': 'false',
             'f': 'json', 'resultRecordCount': 1000}
        last = None
        for _ in range(3):
            try:
                j = S.post(PAGIS, data=p, timeout=90).json()
                if 'features' in j:
                    for f in j['features']:
                        at = f['attributes']
                        out[str(at.get('FOLIO') or '').strip()] = at
                    last = 'ok'
                    break
                last = str(j)[:200]
            except Exception as e:
                last = str(e)[:200]
            time.sleep(1.5)
        if last != 'ok':
            raise RuntimeError('PaGis chunk failed at offset %d: %s' % (i, last))
    return out


def _residential_house(pg):
    """Single-family / townhouse / duplex, never a condo, never commercial."""
    if str(pg.get('CONDO_FLAG') or '').upper() in ('Y', 'YES', 'TRUE', '1'):
        return False
    dor = str(pg.get('DOR_DESC') or '').upper()
    if 'CONDO' in dor:
        return False
    return any(k in dor for k in ('SINGLE FAMILY', 'TOWNHOUSE', 'DUPLEX', 'MOBILE HOME'))


def _board_folios():
    fol = set()
    for fn in ('leads_final.json', 'lp_leads.json'):
        rows = CR._load(fn) or []
        if isinstance(rows, list):
            for r in rows:
                f = str(r.get('folio') or r.get('Folio') or '').strip().replace('-', '')
                if len(f) >= 12:
                    fol.add(f)
    return fol


def _week_addrs():
    """Doors the week packet assigned (recomputed, deterministic) — never double-route."""
    leads = CR._load('leads_final.json') or []
    geo = CR._load('geocode_cache.json') or {}
    skip = CR._load('skiptrace_results.json') or {}
    sibs = CR._load('sibling_cases.json') or {}
    _oo = CR._load('optouts.json') or {}
    opt = set((_oo.get('notes') or _oo or {}).keys()) if isinstance(_oo, dict) else set()
    out, seen = set(), set()
    for r in leads:
        c = r.get('case') or r.get('Case #')
        g = geo.get(c) or {}
        if not (g.get('lat') and g.get('lng')):
            continue
        if not W._house_only(r, skip, sibs, opt):
            continue
        a = W._addr(r).lower()
        if not a or a in seen:
            continue
        for name, la, lo in W.CITIES:
            if CR._hav_mi(la, lo, g['lat'], g['lng']) <= W.RADIUS:
                seen.add(a)
                out.add(' '.join(W._addr(r).upper().split()))
                break
    return out


def _dt(ms):
    if not ms:
        return None
    try:
        return datetime.datetime.utcfromtimestamp(ms / 1000).date()
    except Exception:
        return None


def main():
    board = _board_folios()
    routed_week = _week_addrs()
    packet = {}

    for name, lat, lng, radius in ZONES:
        feats = _ccviol_zone(lat, lng, radius)

        # group every concern case by folio; a parcel's story is ALL its cases together
        by_folio = {}
        for f in feats:
            at, gm = f['attributes'], f.get('geometry') or {}
            folio = str(at.get('FOLIO') or '').strip().replace('-', '')
            if len(folio) < 12 or not gm.get('y'):
                continue
            d = CR._hav_mi(lat, lng, gm['y'], gm['x'])
            if d > radius:
                continue
            by_folio.setdefault(folio, {'cases': [], 'dist': d})['cases'].append(at)
            by_folio[folio]['dist'] = min(by_folio[folio]['dist'], d)

        # a door needs real distress: at least one recorded lien or county foreclosure
        cands = {}
        for folio, g in by_folio.items():
            if folio in board:
                continue
            sts = {str(c.get('CASE_STATUS')) for c in g['cases']}
            if not (sts & {'4', '9'}):
                continue
            cands[folio] = g
        pg_all = pagis_batch(cands.keys())
        print('  pagis hit-rate: %d/%d' % (len([f for f in cands if pg_all.get(f)]), len(cands)))

        drop = {'no-parcel': 0, 'not-house': 0, 'entity-owner': 0, 'routed/excluded': 0}
        scored = []
        for folio, g in cands.items():
            pg = pg_all.get(folio)
            if not pg:
                drop['no-parcel'] += 1
                continue
            if not _residential_house(pg):
                drop['not-house'] += 1
                continue
            owner = ' '.join(str(pg.get('TRUE_OWNER1') or '').upper().split())
            if not owner or _ENTITY.search(owner):
                drop['entity-owner'] += 1
                continue
            addr = ' '.join(str(pg.get('TRUE_SITE_ADDR_NO_UNIT') or '').upper().split())
            if not addr or W._excluded(addr) or addr in routed_week:
                drop['routed/excluded'] += 1
                continue
            sts = {str(c.get('CASE_STATUS')) for c in g['cases']}
            sev = max(_severity(c.get('PROBLEM_DESC')) for c in g['cases'])
            # owner-occupied test: tax bill mails to the property itself. The layer's
            # homestead/value columns are null here (verified live), so mailing==site is
            # the working signal — if the owner lives there, the knock reaches them.
            # Suffixes vary between the two columns (TERR vs TER, AVE vs AVENUE).
            def _na(a):
                a = ' %s ' % ' '.join(str(a or '').upper().split())
                for full, ab in (('TERRACE', 'TER'), ('TERR', 'TER'), ('AVENUE', 'AVE'),
                                 ('STREET', 'ST'), ('DRIVE', 'DR'), ('COURT', 'CT'),
                                 ('PLACE', 'PL'), ('LANE', 'LN'), ('ROAD', 'RD')):
                    a = a.replace(' %s ' % full, ' %s ' % ab)
                return a.strip()
            mail, site = _na(pg.get('TRUE_MAILING_ADDR1')), _na(pg.get('TRUE_SITE_ADDR_NO_UNIT'))
            hstead = bool(site) and mail == site
            estate = ' EST ' in ' %s ' % owner or ' ESTATE ' in ' %s ' % owner
            lien_dates = [d for d in (_dt(c.get('LN_RECDATE')) for c in g['cases']) if d]
            bps = ['%s/%s' % (c.get('LN_RECBOOK'), c.get('LN_RECPAGE'))
                   for c in g['cases'] if str(c.get('LN_RECBOOK') or '').strip()]
            probs = sorted({str(c.get('PROBLEM_DESC') or '').strip()
                            for c in g['cases'] if c.get('PROBLEM_DESC')})
            score = (100 * ('9' in sts) + 40 * hstead + 15 * sev + 20 * estate
                     + 4 * min(len(g['cases']), 5))
            scored.append({'folio': folio, 'addr': addr,
                           'city': str(pg.get('TRUE_SITE_CITY') or '').strip(),
                           'zip': str(pg.get('TRUE_SITE_ZIP_CODE') or '').strip(),
                           'owner': owner.title(),
                           'owner_occupied': hstead, 'estate': estate,
                           'statuses': sorted(sts, reverse=True),
                           'lane': 'COUNTY FORECLOSING' if '9' in sts else 'RECORDED LIEN',
                           'n_cases': len(g['cases']), 'problems': probs[:4],
                           'severity': sev, 'dist': round(g['dist'], 2),
                           'mailing': ('%s, %s %s' % (pg.get('TRUE_MAILING_ADDR1') or '',
                                                      pg.get('TRUE_MAILING_CITY') or '',
                                                      pg.get('TRUE_MAILING_STATE') or '')).strip(', '),
                           'lien_bp': bps[0] if bps else '',
                           'latest_lien': str(max(lien_dates)) if lien_dates else '',
                           'score': score})

        print('  drops:', drop)
        scored.sort(key=lambda c: (-c['score'], c['dist']))
        packet[name] = scored[:PER_ZONE_CAP]
        print('%s: %d violation points -> %d parcels -> %d lien/LP parcels -> '
              '%d individual-owner houses -> top %d'
              % (name, len(feats), len(by_folio), len(cands), len(scored),
                 len(packet[name])))

    with open(os.path.join(HERE, 'deep_zone_doors.json'), 'w', encoding='utf-8') as fh:
        json.dump(packet, fh, indent=1)
    print('wrote deep_zone_doors.json')


if __name__ == '__main__':
    main()
