"""Whitepages APN gate (2026-10-21).

The WP cache is case-keyed (correct), but a cross-check of every current join found half the
records resolved to a DIFFERENT parcel -- same house number, wrong street -- because the address
fed to Whitepages at lookup time was garbled. foreclosure_leads.wp_parcel_verdict(apn, folio) is the
seam that refuses those records before their stranger phones get baked into a dialable lead.

Default: synthetic joins only, so this runs in CI without homeowner data.
Use --live on the armed laptop to also check the private cache and leads.
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from foreclosure_leads import wp_parcel_verdict  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:200]) if extra else ''))
    if not ok:
        fails.append(name)


# --- unit: the three verdicts, with format tolerance -------------------------------------------
rec('apn == folio (dashes vs digits) -> ok',
    wp_parcel_verdict('30-4018-001-0860', '3040180010860') == 'ok')
rec('apn != folio -> mismatch',
    wp_parcel_verdict('30-5928-004-1110', '3049160240470') == 'mismatch')
rec('apn present, folio empty -> unverified',
    wp_parcel_verdict('30-3116-009-2650', '') == 'unverified')
rec('apn present, folio is junk non-digits -> unverified',
    wp_parcel_verdict('30-3116-009-2650', 'Property Appraiser') == 'unverified')
rec('no apn (person-only record) -> ok (nothing to contradict)',
    wp_parcel_verdict('', '3040180010860') == 'ok')
rec('neither present -> ok', wp_parcel_verdict(None, None) == 'ok')
rec('apn with stray spaces still matches', wp_parcel_verdict(' 3040180010860 ', '30-40-18-001-0860') == 'ok')
rec('a one-digit-off apn is a mismatch, not ok',
    wp_parcel_verdict('3040180010861', '3040180010860') == 'mismatch')

# --- integration: the live cache must not bake a wrong-parcel record ----------------------------
# Every current lead whose WP record claims a parcel that disagrees with the lead folio must be
# refused. Clean matches must pass. This is the exact split the 2026-10-21 cross-check found.
live = '--live' in sys.argv
if live:
    try:
        wp = json.load(io.open(os.path.join(HERE, 'whitepages_lookup.json'), encoding='utf-8'))
        lf = json.load(io.open(os.path.join(HERE, 'leads_final.json'), encoding='utf-8'))
    except Exception as e:
        rec('live cache + leads load', False, e)
        wp = lf = None
else:
    # Invented keys/parcel ids: a clean match, mismatch, and missing anchor must all be exercised.
    wp = {'TEST-OK': {'result': {'apn': '99-9999-999-9991'}},
          'TEST-WRONG': {'result': {'apn': '99-9999-999-9992'}},
          'TEST-UNKNOWN': {'result': {'apn': '99-9999-999-9993'}}}
    lf = [{'Case #': 'TEST-OK', 'Folio': '9999999999991'},
          {'Case #': 'TEST-WRONG', 'Folio': '9999999999994'},
          {'Case #': 'TEST-UNKNOWN', 'Folio': 'Property Appraiser'}]

if wp is not None:
    folio_by_case = {}
    for r in lf:
        c = (r.get('Case #') or '').strip()
        if not c:
            continue
        f = re.sub(r'\D', '', str(r.get('Folio') or ''))
        if len(f) != 13:
            # fall back to Parcel ID if it is a real 13-digit parcel (not the "Property Appraiser" stub)
            p = re.sub(r'\D', '', str(r.get('Parcel ID') or ''))
            f = p if len(p) == 13 else ''
        folio_by_case[c] = f

    joined = [c for c in wp if c in folio_by_case]
    verdicts = {}
    for c in joined:
        apn = (wp[c].get('result') or {}).get('apn') or ''
        verdicts[c] = wp_parcel_verdict(apn, folio_by_case.get(c, ''))

    n_ok = sum(1 for v in verdicts.values() if v == 'ok')
    n_bad = sum(1 for v in verdicts.values() if v in ('mismatch', 'unverified'))
    rec('live join produced a verdict for every joined case', len(verdicts) == len(joined),
        f'{len(verdicts)}/{len(joined)}')
    if not live:
        rec('synthetic join keeps the match and refuses both uncertain/wrong parcels',
            verdicts == {'TEST-OK': 'ok', 'TEST-WRONG': 'mismatch', 'TEST-UNKNOWN': 'unverified'}, verdicts)
    # No record that passes may have an apn that disagrees with a present folio.
    leaks = [c for c, v in verdicts.items()
             if v == 'ok'
             and re.sub(r'\D', '', str((wp[c].get('result') or {}).get('apn') or ''))
             and folio_by_case.get(c)
             and re.sub(r'\D', '', str((wp[c].get('result') or {}).get('apn') or '')) != folio_by_case[c]]
    rec('no wrong-parcel record slips through as ok', not leaks, leaks[:5])
    print(f'  ({"live" if live else "synthetic"}: {len(joined)} joined, {n_ok} pass the gate, {n_bad} suppressed)')

print('\n%d failed' % len(fails))
sys.exit(1 if fails else 0)
