#!/usr/bin/env python3
"""code_liens.py — Miami-Dade code-enforcement cases + recorded liens, folio-joined to the board.

WHY THIS EXISTS
A code-enforcement lien is a JUNIOR lien that does not show up in the mortgage chain, and it is
exactly the thing that turns a "90% equity, strong buy" lead into a loss: unsafe-structure fines,
overgrowth, unpermitted-work penalties accrue daily and attach to the parcel. The board already
warns that equity is a gross number when a mortgage is unknown; this closes the OTHER half —
the lien nobody records against the note but the county absolutely enforces.

It pays off twice, which is why it was worth building:
  1. EQUITY GUARD — every existing lead's folio is checked against the county's code-enforcement
     file. A hit means "there is a code lien or an open case on this parcel; the equity you see is
     optimistic and you must verify the fine balance before you model this deal."
  2. (future) NET-NEW LEADS — the same file holds ~9k open cases and ~7k recorded liens on owners
     who are NOT in mortgage foreclosure at all. Surfacing those as board leads is a bigger change
     (they need skiptrace/comps/value like any lead) and is deliberately NOT done here — this file
     only annotates leads we already have. See the roadmap note at the bottom.

THE SOURCE (verified live before building)
Free, unauthenticated, folio-keyed ArcGIS Feature Service — NO key, NO captcha, NO login:
  https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/CCVIOL_gdb/FeatureServer/0
181,013 records. Status codes (confirmed by a groupBy on the live layer):
  2 Closed (149,395) · 8 Referred to Internal Compliance (15,142) · 1 Open (8,838)
  4 Lien (7,230) · 6 Civil (338) · 9 Active LP (70)
Folio-keyed means it drops straight into the parcel join the pipeline already runs — no fuzzy
name matching, the failure mode that makes probate hard. FOLIO comes back space-padded; strip it.

WHAT COUNTS AS A CONCERN
  - status 4 (Lien)      -> a recorded code lien exists. Hard equity hit.
  - status 9 (Active LP) -> the county is FORECLOSING on its own code lien. Serious.
  - status 1 (Open) / 8 (Referred) -> an unresolved case; fines may be accruing, no lien yet.
  - a populated LN_RECDATE on any status -> a lien was recorded regardless of current status.
Closed (2) with no lien is ignored — a cured violation is not a concern.

Run:  python code_liens.py            # check every Miami-Dade board folio, cached
      python code_liens.py --refresh  # ignore the cache
      python code_liens.py --folio 3059130020010
"""
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'code_liens.json')
CACHE = os.path.join(HERE, '_code_liens_cache.json')

LAYER = ('https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/'
         'CCVIOL_gdb/FeatureServer/0/query')
FIELDS = 'CASE_NUM,CASE_DATE,CASE_STATUS,STAT_DESC,ADDRESS,FOLIO,PROBLEM_DESC,LN_RECDATE,LN_RECBOOK,LN_RECPAGE'
# statuses that mean "unresolved or lien" — 2 (Closed) is dropped unless a lien was recorded
CONCERN = {'1', '4', '6', '8', '9'}
STATUS_LABEL = {'1': 'Open case', '4': 'RECORDED LIEN', '6': 'Civil', '8': 'Referred to compliance', '9': 'County foreclosing (Active LP)'}

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0'})


def _load(p, d):
    if not os.path.exists(p):
        return d
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


def _md_folios():
    """Every Miami-Dade board lead's folio. leads_final.json is the MD auction set; lp_leads.json
    holds resolved lis-pendens folios. Both feed the board, both are worth guarding."""
    fol = {}
    for fn in ('leads_final.json', 'lp_leads.json'):
        for r in _load(fn, []) if isinstance(_load(fn, []), list) else []:
            f = str(r.get('folio') or r.get('Folio') or '').strip().replace('-', '')
            case = str(r.get('case') or r.get('Case #') or '').strip()
            if f and len(f) >= 12:
                fol[f] = case
    return fol


def _fetch(folios):
    """One request per batch. The layer takes an IN() list; strip pad on the way out."""
    quoted = ','.join("'" + f + "'" for f in folios)
    try:
        r = S.get(LAYER, params={'where': 'FOLIO IN (%s)' % quoted, 'outFields': FIELDS,
                                 'returnGeometry': 'false', 'f': 'json',
                                 'resultRecordCount': 2000, 'orderByFields': 'CASE_DATE DESC'},
                  timeout=60)
        d = r.json()
    except Exception as e:
        print('  batch failed:', str(e)[:100]); return {}
    if 'error' in d:
        print('  layer error:', str(d['error'])[:140]); return {}
    out = {}
    for feat in d.get('features', []):
        a = feat.get('attributes') or {}
        f = str(a.get('FOLIO') or '').strip()
        st = str(a.get('CASE_STATUS') or '').strip()
        has_lien = bool(str(a.get('LN_RECDATE') or '').strip())
        if st not in CONCERN and not has_lien:
            continue
        out.setdefault(f, []).append({
            'case': str(a.get('CASE_NUM') or '').strip(),
            'st': st, 'stLabel': STATUS_LABEL.get(st, (a.get('STAT_DESC') or '').strip()),
            'problem': (a.get('PROBLEM_DESC') or '').strip(),
            'lien': has_lien,
            'lienRef': ((str(a.get('LN_RECBOOK') or '').strip() + '/' + str(a.get('LN_RECPAGE') or '').strip())
                        if has_lien else ''),
        })
    return out


def main():
    args = sys.argv[1:]
    if '--folio' in args:
        f = args[args.index('--folio') + 1].strip().replace('-', '')
        print(json.dumps(_fetch([f]), indent=1)); return
    refresh = '--refresh' in args

    folios = _md_folios()
    if not folios:
        print('no Miami-Dade folios on the board — run the scrape first'); return
    cache = {} if refresh else _load(CACHE, {})
    todo = [f for f in folios if f not in cache]
    print('%d Miami-Dade folio(s) · %d to check (%d cached)' % (len(folios), len(todo), len(folios) - len(todo)))

    B = 60
    for i in range(0, len(todo), B):
        chunk = todo[i:i + B]
        hits = _fetch(chunk)
        for f in chunk:
            cache[f] = hits.get(f, [])          # [] = checked, clean — do not re-query next run
        json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=0)
        print('  %d/%d' % (min(i + B, len(todo)), len(todo)))
        if i + B < len(todo):
            time.sleep(0.6)

    # emit only the folios that actually carry a concern, keyed by folio for the board join
    flagged = {f: v for f, v in cache.items() if v}
    json.dump(flagged, open(OUT, 'w', encoding='utf-8'), indent=1)
    liens = sum(1 for v in flagged.values() if any(h['lien'] for h in v))
    fc = sum(1 for v in flagged.values() if any(h['st'] == '9' for h in v))
    print('\ncode_liens.json — %d folio(s) flagged · %d with a RECORDED LIEN · %d the county is foreclosing'
          % (len(flagged), liens, fc))
    if flagged:
        print('Rebuild to surface them on the board.')

    # ROADMAP (not built here): the same layer holds ~9k open + ~7k lien cases on owners NOT in
    # mortgage foreclosure — net-new distressed leads the CA/CC scrape structurally cannot see.
    # Turning those into board leads means skiptrace + comps + value on each, i.e. a real new lane,
    # and is a product decision, not a cross-reference. Left for a deliberate build.


if __name__ == '__main__':
    main()
