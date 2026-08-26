#!/usr/bin/env python
"""lp_upgrade -- adjudicate MEDIUM-confidence lis-pendens rows against the county record.

WHY 'MEDIUM' EXISTS, AND WHY IT IS RECOVERABLE. lp_resolve.py calls a row medium when it matched
the PARCEL (legal description, platbook, condo-OR key) but could not confirm the PERSON -- almost
always because the LP's own "owner" string is a company/HOA (usually the plaintiff), so there was
no defendant name to compare. That is a data gap, not a verdict. lp_values.py --all fills the gap:
it pulls each folio's live Property Appraiser record (paOwners / hs / dor / value) onto the row.
Once those fields exist, the question the resolver couldn't answer becomes answerable three ways:

    1. Is the county's CURRENT owner of record a natural person (not an LLC/trust/bank/REO)?
    2. Do they HOMESTEAD the parcel (= they live behind that door)?
    3. When the LP owner IS a person, do the names actually agree?

A door is only a door if the person who answers it is the person the case is about -- the
non-owner traps are the exact thing Jesse got burned on twice (see feedback-jesse-diligence).

First proven 2026-08-24 (72 Dade medium rows -> 27 knockable, 19 entity-owned, 15 condo,
6 different-person, 3 non-homestead, 2 PA-empty). This module is that adjudication moved into
the repo so the daily route generator can rely on it instead of a scratchpad script.

Import:  import lp_upgrade as LU;  buckets = LU.upgrade(addrs)     # addrs = lp_addresses.json dict
CLI:     python lp_upgrade.py            # prints the bucket report for Dade
         python lp_upgrade.py --all      # both counties
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

ENTITY = re.compile(
    r'\b(LLC|L\.L\.C|INC\b|CORP|TRUST|TRS\b|BANK|MORTGAGE|SERVICING|SERVICES|'
    r'ASSOC|ASSN|COMPANY|HOLDINGS|PARTNERS|FUND|REO|CONDOMINIUM|HOA|PROPERTIES|'
    r'INVESTMENT|CAPITAL|GROUP|ENTERPRISE|REALTY|N\.A\b)\b', re.I)

# "PHILIP J LANE EST OF" -- the county's deceased-owner format. Deliberately NOT a bare
# \bESTATE\b: "X REAL ESTATE" would trip it, and entity-gating already caught most of those.
ESTATE = re.compile(r'\bEST OF\b|\bESTATE OF\b', re.I)

# "JOSE O GARCIA LE; REM GENESIS OLIVER" -- life tenant + remainderman. Requires BOTH tokens
# (or the literal words): a bare \bLE\b alone false-positives on the surname LE, and sending a
# real door to the Jose-first pile is safe but sending it every time a Vietnamese family is in
# foreclosure is just wrong.
LIFE = re.compile(r'(?=.*\bLE\b)(?=.*\bREM\b)|\bLIFE ESTATE\b', re.I)


def _toks(s):
    return {t for t in re.split(r'[^A-Z]+', (s or '').upper()) if len(t) > 2}


def classify(v):
    """One resolved-address row -> (bucket, reason). Buckets:
    knockable | special-estate | special-life | entity | condo | mismatch | absentee | no-pa-data
    Only rows the resolver marked medium make sense here; the caller filters confidence."""
    pa = v.get('paOwners') or v.get('paOwner') or ''
    lpo = v.get('lpOwner') or ''
    dor = (v.get('dor') or '').upper()
    addr = (v.get('addr') or '').upper()

    if not v.get('value') and not dor:
        return 'no-pa-data', 'PA returned nothing for this folio -- still unverifiable'
    if ENTITY.search(pa):
        return 'entity', 'owner of record is a company/trust/bank -- not a homeowner door'
    if ('CONDOMINIU' in dor or 'COOPERATIVE' in dor
            or ' #' in ' ' + addr.lower() or re.search(r'\s\d+[A-Z]?$', addr)):
        return 'condo', 'condo / co-op / unit'
    # Estate and life-estate BEFORE the homestead gate: a dead owner's homestead flag can
    # linger on the roll for a season, and "no homestead" on a life estate is the remainderman's
    # problem, not evidence nobody lives there. Both are real leads on the wrong script.
    if ESTATE.search(pa):
        return 'special-estate', 'owner of record is deceased (EST OF) -- probate talk, Jose first'
    if LIFE.search(pa):
        return 'special-life', 'life estate + remainderman -- both signatures needed, Jose first'
    if not v.get('hs'):
        return 'absentee', 'no homestead -- owner does not live there'
    lp_is_person = bool(lpo) and not ENTITY.search(lpo)
    if lp_is_person and not (_toks(pa) & _toks(lpo)):
        return 'mismatch', 'county owner is a DIFFERENT person than the LP defendant'
    return 'knockable', ('name-match' if lp_is_person else 'homestead-verified (LP named the plaintiff)')


def upgrade(addrs, county='DADE'):
    """lp_addresses.json dict -> {'knockable': [rows], 'special': [rows], 'dropped': Counter-ish dict}.
    county='DADE' keeps rows whose county field is NOT BROWARD (Dade rows carry no county tag);
    'BROWARD' keeps tagged ones; 'ALL' keeps everything."""
    out = {'knockable': [], 'special': [], 'dropped': {}}
    for k, v in addrs.items():
        if str(v.get('confidence') or '') != 'medium':
            continue
        is_brw = v.get('county') == 'BROWARD'
        if county == 'DADE' and is_brw:
            continue
        if county == 'BROWARD' and not is_brw:
            continue
        bucket, why = classify(v)
        row = dict(v)
        row.setdefault('case', k)
        row['upgrade'] = bucket
        row['upgrade_why'] = why
        if bucket == 'knockable':
            out['knockable'].append(row)
        elif bucket.startswith('special-'):
            out['special'].append(row)
        else:
            out['dropped'][bucket] = out['dropped'].get(bucket, 0) + 1
    return out


def main():
    county = 'ALL' if '--all' in sys.argv[1:] else 'DADE'
    path = os.path.join(HERE, 'lp_addresses.json')
    if not os.path.exists(path):
        print('no lp_addresses.json -- run lp_resolve.py first.')
        return 1
    addrs = json.load(open(path, encoding='utf-8'))
    b = upgrade(addrs, county)
    n_med = len(b['knockable']) + len(b['special']) + sum(b['dropped'].values())
    print(f'{n_med} medium row(s) adjudicated ({county})')
    print(f'  knockable        : {len(b["knockable"])}')
    print(f'  special (Jose)   : {len(b["special"])}'
          + (f'  ({", ".join(sorted({r["upgrade"] for r in b["special"]}))})' if b['special'] else ''))
    for kk, n in sorted(b['dropped'].items(), key=lambda x: -x[1]):
        print(f'  {kk:<17}: {n}')
    if any(not (r.get('value') or r.get('dor')) for r in b['knockable']):
        print('NOTE: some knockable rows have no PA data -- run  python lp_values.py --all  first.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
