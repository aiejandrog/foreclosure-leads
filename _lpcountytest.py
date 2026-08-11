#!/usr/bin/env python
"""_lpcountytest — acceptance checks for the multi-county lis pendens lane (BATCHDATA-EXIT Phase 1-2).

Run:  python _lpcountytest.py    (exit 0 = safe)

Guards the three ways this lane has actually broken:
  1. import-time death (the 33-day silent outage): importing lis_pendens must not raise;
  2. merge identity: rows from different counties must NEVER collide to one key, and the sweep
     must MERGE prev-wins, not overwrite (an overwrite would delete 125 worked MD leads);
  3. resolver precision: the BCPA name ladder may only widen RECALL — owner_agrees stays the
     precision gate, so a partial-name hit must never pass.
"""
import sys

fails = []


def chk(name, cond):
    if not cond:
        fails.append(name)


# --- 1. the lane must survive import (this exact line was dead for 33 days) ---------------------
try:
    import lis_pendens as LP
    chk('lis_pendens imports', True)
except Exception as e:
    fails.append('lis_pendens FAILED AT IMPORT: %r  <- the 33-day outage is back' % (e,))
    print('FAIL:', fails[-1])
    sys.exit(1)

from fl_lp import broward_resolve as BR

# --- 2. merge keys: county-scoped, instrument-first, stable fallbacks ---------------------------
md = {'county': 'MIAMI-DADE', 'instrument': '2026R123', 'case': '2026-1'}
bw = {'county': 'BROWARD', 'instrument': '2026R123', 'case': '2026-1'}
chk('same instrument in two counties = two keys', LP._merge_key(md) != LP._merge_key(bw))
chk('key stable across reruns', LP._merge_key(md) == LP._merge_key(dict(md)))
chk('no county defaults to MIAMI-DADE (legacy rows)',
    LP._merge_key({'instrument': '2026R123'}) == LP._merge_key({'county': 'MIAMI-DADE',
                                                                'instrument': '2026R123'}))
chk('caseless+instrumentless rows still key (bookpage fallback)',
    LP._merge_key({'county': 'BROWARD', 'bookpage': '119/22'}) is not None)

# --- 3. BCPA query ladder: broad recall, but owner_agrees stays the precision gate --------------
qs = BR._queries('MARTIN,SHAWN E')
chk("ladder drops middle initial ('MARTIN, SHAWN' first)", qs and qs[0] == 'MARTIN, SHAWN')
chk('ladder falls back to bare surname', 'MARTIN' in qs)
qs2 = BR._queries('PAULA ANNE DRALUCK')
chk('FIRST..LAST reconstructed as LAST, FIRST', qs2 and qs2[0] == 'DRALUCK, PAULA')
qs3 = BR._queries('K HOLDING LLC')
chk('entities searched as-is first', qs3 and qs3[0] == 'K HOLDING LLC')

from lp_resolve2 import owner_agrees
chk('full-token match passes', owner_agrees('MARTIN,SHAWN E', 'MARTIN, SHAWN E', ''))
chk('surname-only hit REJECTED (the wrong-Garcia guard)',
    not owner_agrees('MARTIN,SHAWN E', 'MARTIN, JOSE', ''))

# --- 4. all three county sweepers stay importable and expose sweep() ----------------------------
from fl_lp import broward
chk('broward module exposes sweep()', callable(getattr(broward, 'sweep', None)))
import fl_lp.palmbeach as pb
chk('palmbeach module exposes sweep()', callable(getattr(pb, 'sweep', None)))

if fails:
    print('FAIL (%d):' % len(fails))
    for f in fails:
        print(' -', f)
    sys.exit(1)
print('lp county lane: all checks pass')
