"""Miami-Dade name-only filings resolved by the county parcel layer (2026-11-26). Offline: PaGis and the
state roll are faked, so this checks the GRADING and the never-guess rules, not the network."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'fl_lp'))
import palmbeach_resolve as P  # noqa: E402

FAIL = []


def check(name, cond):
    print(('  ok   ' if cond else '  FAIL ') + name)
    if not cond:
        FAIL.append(name)


def parcel(folio, o1, addr='100 SW 1 ST', o2=''):
    return {'FOLIO': folio, 'TRUE_OWNER1': o1, 'TRUE_OWNER2': o2, 'TRUE_SITE_ADDR': addr,
            'TRUE_SITE_CITY': 'MIAMI', 'TRUE_SITE_ZIP_CODE': '33130-1234'}


def fq(rows, trunc=False):
    return lambda where: (rows, trunc)


HS = lambda f: {'homestead': True, 'market_value': 400000, 'mail_addr': '100 SW 1 ST MIAMI FL'}   # noqa: E731
NOHS = lambda f: {'homestead': False, 'market_value': 300000}                                       # noqa: E731
NONE = lambda f: None                                                                                # noqa: E731
rec = {'case': 'X', 'owner': 'RIVERA, LUIS M', 'plaintiff': 'SOME BANK NA'}

r = P.resolve_pagis_name(rec, q=fq([parcel('1', 'RIVERA LUIS MIGUEL')]), enrich=HS)
check('one parcel + homestead = high, no human needed', r['confidence'] == 'high' and not r['needsHuman']
      and r['rung'] == 'md-pagis' and r['zip'] == '33130' and r['hs'] is True)
r = P.resolve_pagis_name(rec, q=fq([parcel('1', 'RIVERA LUIS MIGUEL')]), enrich=NOHS)
check('one parcel, no homestead = medium + needsHuman', r['confidence'] == 'medium' and r['needsHuman'])
r = P.resolve_pagis_name(rec, q=fq([parcel('1', 'RIVERA LUIS MIGUEL')]), enrich=NONE)
check('one parcel, roll has no record = medium, never high', r['confidence'] == 'medium')
r = P.resolve_pagis_name(rec, q=fq([parcel('1', 'RIVERA LUIS A'), parcel('2', 'RIVERA LUIS B', '9 NW 2 AVE')]),
                         enrich=HS)
check('two parcels carry the name = low with candidates, no address taken',
      r['confidence'] == 'low' and len(r['candidates']) == 2 and not r.get('addr'))
r = P.resolve_pagis_name(rec, q=fq([parcel('1', 'RIVERAS LUIS')]), enrich=HS)
check('a similar-looking surname (word-boundary re-check) is not a match', r['confidence'] == 'none')
r = P.resolve_pagis_name(rec, q=fq([parcel('1', 'RIVERA LUIS')], trunc=True), enrich=HS)
check('a full page (truncated) never picks a parcel', r['confidence'] == 'none')
r = P.resolve_pagis_name(rec, q=fq(None), enrich=HS)
check('layer down = unreachable, not "no such owner"', r['confidence'] == 'none' and r.get('unreachable'))
r = P.resolve_pagis_name(dict(rec, owner='FLIP 7 LLC'), q=fq([parcel('1', 'FLIP 7 LLC')]), enrich=HS)
check('a company is refused', r['confidence'] == 'none')
r = P.resolve_pagis_name(dict(rec, owner='RIVERA'), q=fq([parcel('1', 'RIVERA JOSE')]), enrich=HS)
check('a one-word name is never queried', r['confidence'] == 'none')
r = P.resolve_pagis_name(rec, q=fq([parcel('1', 'RIVERA LUIS', addr='')]), enrich=HS)
check('a parcel with no street address is not an address', r['confidence'] == 'none')

SELFMAIL = lambda f: {'homestead': False, 'market_value': 1, 'mail_addr': '100 SW 1 ST MIAMI FL 33130'}   # noqa: E731
r = P.resolve_pagis_name(rec, q=fq([parcel('1', 'RIVERA LUIS MIGUEL')]), enrich=SELFMAIL)
check('no homestead but the tax mailing address is the parcel = high', r['confidence'] == 'high')
OTHERMAIL = lambda f: {'homestead': False, 'market_value': 1, 'mail_addr': '9 NE 5 AVE ORLANDO FL 32801'}   # noqa: E731
r = P.resolve_pagis_name(rec, q=fq([parcel('1', 'RIVERA LUIS MIGUEL')]), enrich=OTHERMAIL)
check('mailed elsewhere (absentee) stays medium', r['confidence'] == 'medium')

# wiring: the state roll finds nothing, the county layer answers
import lp_resolve as LR  # noqa: E402
orig_q = LR.q
LR.q = fq([parcel('7', 'RIVERA LUIS')])
orig_enrich = P.FC.enrich
P.FC.enrich = lambda *a, **k: HS(0)
try:
    out = P.resolve_row(rec, fetch=lambda where, n: [], pause=0, co_no=53, rung='md-name')
    check('resolve_row falls through to the county layer for Miami-Dade', out['confidence'] == 'high'
          and out['rung'] == 'md-pagis')
    out = P.resolve_row(rec, fetch=lambda where, n: [], pause=0, co_no=60, rung='pb-name')
    check('Palm Beach is untouched by the Miami-Dade fallback', out['confidence'] == 'none')
finally:
    LR.q = orig_q
    P.FC.enrich = orig_enrich

check('md-pagis is a settled rung (never re-asked once resolved)',
      P._due({'rung': 'md-pagis', 'confidence': 'high', 'v': 3}, __import__('datetime').date.today()) is False)
check('an old "none" is asked again (v3 bump)',
      P._due({'rung': '', 'confidence': 'none', 'v': 2, 'asked': '2026-11-25'},
             __import__('datetime').date.today()) is True)

print('\n%s' % ('all passed' if not FAIL else '%d FAILED' % len(FAIL)))
sys.exit(1 if FAIL else 0)
