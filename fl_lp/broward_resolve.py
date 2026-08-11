#!/usr/bin/env python
"""fl_lp.broward_resolve — put an ADDRESS on Broward lis pendens rows.

Broward's recorder grid returns no legal description and no parcel (verified live), so the MD
resolver's legal-description ladder has nothing to climb. What Broward DOES give us is the
defendant's name, and BCPA's public JSON search turns a name into folio + site address
(verified live: MARTIN,SHAWN E -> 494134CB0170, 1760 NW 73 AVE #48 PLANTATION).

THE SAFETY BAR — same religion as lp_resolve: a name alone is ONE signal, and a single shared
surname is how you knock on the wrong Garcia. So:
  * every BCPA hit must contain EVERY significant token of the LP defendant's name
    (order-free subset — lp_resolve2.owner_agrees, reused verbatim);
  * exactly ONE matching parcel in the county, else it's candidates for a human;
  * the second signal is the FL cadastral roll on that folio: HOMESTEAD there means the person
    the county says owns-and-lives-at that parcel is the person being foreclosed -> 'high'
    (outreach-usable, value attached). No homestead corroboration -> 'medium' (advisory only,
    lp_leads renders it "verify before contact" and no outreach path consumes it).
  * condo-format folios (letters in the id) can't cross-check against the cadastral (its
    PARCEL_ID join strips non-digits) -> capped at 'medium' regardless.

Writes into lp_addresses.json (merge by case) in the exact shape lp_leads.build() consumes.

Run:  python fl_lp/broward_resolve.py            # resolve all unresolved BROWARD rows
      python fl_lp/broward_resolve.py --limit 25
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import requests                                     # noqa: E402
from lp_resolve2 import owner_agrees, toks          # noqa: E402  (the wrong-Garcia guard)
import fl_cadastral as FC                           # noqa: E402

SRC = os.path.join(REPO, 'lis_pendens.json')
OUT = os.path.join(REPO, 'lp_addresses.json')
BCPA = 'https://web.bcpa.net/BcpaClient/search.aspx/GetData'

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                'Chrome/126.0 Safari/537.36',
                  'Content-Type': 'application/json',
                  'Referer': 'https://web.bcpa.net/BcpaClient/'})


def bcpa_name(name, tries=3):
    """-> list of {folioNumber, ownerName1/2, siteAddress1/2} or None on transport failure."""
    body = {"value": name, "cities": "", "orderBy": "NAME", "pageNumber": "1",
            "pageCount": "60", "arrayOfValues": "", "selectedFromList": "false",
            "totalCount": "Y"}
    for _ in range(tries):
        try:
            j = S.post(BCPA, json=body, timeout=40).json()
            return (j.get('d') or {}).get('resultListk__BackingField') or []
        except Exception:
            time.sleep(1.5)
    return None


def _queries(owner):
    """Broad-to-narrow BCPA query ladder. The query only controls RECALL — precision comes from
    the owner_agrees token-subset filter afterwards, so it's safe to search wide.
      'MARTIN,SHAWN E'     -> ['MARTIN, SHAWN', 'MARTIN']
      'PAULA ANNE DRALUCK' -> ['DRALUCK, PAULA', 'DRALUCK']   (clerk sometimes files FIRST..LAST)
      'K HOLDING LLC'      -> ['K HOLDING LLC', 'K HOLDING']  (entities: as-is, then sans suffix)
    """
    o = ' '.join(str(owner or '').split())
    if re.search(r'\b(LLC|INC|CORP|TRUST|ASSN|ASSOC|CO|LP|LTD|DMCC|PLLC)\b', o.upper()):
        base = re.sub(r'\b(LLC|INC|CORP|LTD|PLLC)\.?\s*$', '', o.upper()).strip()
        return [q for q in [re.sub(r',\s*', ', ', o), base] if q][:2]
    if ',' in o:
        last, given = o.split(',', 1)
        first = (given.split() or [''])[0]
        qs = ['%s, %s' % (last.strip(), first) if first else last.strip(), last.strip()]
    else:
        parts = o.split()
        qs = ['%s, %s' % (parts[-1], parts[0]), parts[-1]] if len(parts) > 1 else [o]
    seen, out = set(), []
    for q in qs:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _split_site(hit):
    """('1760 NW 73 AVE #48', 'PLANTATION', '33313') from siteAddress1/2."""
    a1 = ' '.join(str(hit.get('siteAddress1') or '').split())
    a2 = str(hit.get('siteAddress2') or '')
    m = re.match(r'\s*([^,]+),\s*FL\s*(\d{5})?', a2)
    return a1, (m.group(1).strip().title() if m else ''), (m.group(2) or '' if m else '')


def resolve_row(rec):
    owner = rec.get('owner') or ''
    hits, tried, unreachable = [], [], True
    for q in _queries(owner):
        got = bcpa_name(q)
        tried.append(q)
        if got is None:
            continue
        unreachable = False
        if got:
            hits = got
            break
        time.sleep(0.3)
    if unreachable:
        return {'confidence': 'none', 'evidence': 'BCPA unreachable', 'candidates': []}
    seen_f = set()
    matches = []
    for h in hits:
        if owner_agrees(owner, h.get('ownerName1', ''), h.get('ownerName2', '')):
            f = str(h.get('folioNumber') or '')
            if f not in seen_f:
                seen_f.add(f)
                matches.append(h)
    if not matches:
        return {'confidence': 'none',
                'evidence': 'BCPA: %d hit(s) across %s, none carrying every name token'
                            % (len(hits), tried),
                'candidates': []}
    if len(matches) > 1:
        return {'confidence': 'low',
                'evidence': 'BCPA: %d parcels match the full name — ambiguous, needs a human'
                            % len(matches),
                'candidates': [{'folio': h.get('folioNumber'), 'addr': h.get('siteAddress1'),
                                'city': _split_site(h)[1], 'owner': h.get('ownerName1')}
                               for h in matches[:8]]}
    h = matches[0]
    folio = str(h.get('folioNumber') or '').strip()
    a1, city, zc = _split_site(h)
    row = {'folio': folio, 'addr': a1, 'city': city, 'zip': zc,
           'paOwner': (h.get('ownerName1') or '').strip(), 'candidates': [],
           'ownerMismatch': False}
    condo = bool(re.search(r'[A-Z]', folio))
    ev = 'BCPA: exactly one parcel carries every token of %r' % owner
    if not condo:
        cad = None
        try:
            cad = FC.enrich(parcel_id=folio)
        except Exception:
            pass
        if cad:
            row['value'] = int(cad.get('market_value') or 0)
            row['hs'] = bool(cad.get('homestead'))
            if row['hs']:
                row['confidence'] = 'high'
                row['rung'] = 'bcpa-name'
                row['evidence'] = ev + ' + cadastral shows HOMESTEAD there (owner lives at the parcel)'
                row['needsHuman'] = False
                return row
            ev += ' + cadastral corroborates the parcel (no homestead — absentee or 2nd home)'
    row['confidence'] = 'medium'
    row['rung'] = 'bcpa-name'
    row['evidence'] = ev + ('; condo folio — no cadastral cross-check possible' if condo else '')
    row['needsHuman'] = True
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    recs = [r for r in json.load(open(SRC, encoding='utf-8'))
            if (r.get('county') or '').upper() == 'BROWARD']
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT, encoding='utf-8'))
        except Exception:
            prev = {}
    todo = [r for r in recs if r.get('case') and r['case'] not in prev]
    if a.limit:
        todo = todo[:a.limit]
    print('%d BROWARD LP rows, %d to resolve' % (len(recs), len(todo)))
    counts = {}
    for i, rec in enumerate(todo, 1):
        res = resolve_row(rec)
        res.update({'case': rec['case'], 'lpOwner': rec.get('owner') or '',
                    'legal': rec.get('legal') or '', 'county': 'BROWARD'})
        prev[rec['case']] = res
        counts[res['confidence']] = counts.get(res['confidence'], 0) + 1
        if i % 25 == 0 or i == len(todo):
            print('  [%d/%d] %s' % (i, len(todo), counts))
            tmp = OUT + '.tmp'
            json.dump(prev, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
            os.replace(tmp, OUT)
        time.sleep(0.45)
    tmp = OUT + '.tmp'
    json.dump(prev, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    os.replace(tmp, OUT)
    print('DONE:', counts, '-> lp_addresses.json (merged)')


if __name__ == '__main__':
    main()
