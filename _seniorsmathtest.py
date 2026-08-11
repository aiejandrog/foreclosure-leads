#!/usr/bin/env python
"""_seniorsmathtest — regression guard for the two documented money-math bugs.

Run:  python _seniorsmathtest.py   (exit 0 = safe, non-zero = a feed branch broke the seam)

BUG 1 it guards (the $811,577 erasure, case 502024CA012300XXXAMB): records/broward feeds emit
surv = seniors+juniors and need juniors_post subtracted; BatchData emits seniors-only. Applying
the records-style subtraction to a seniors-only feed deleted an entire first mortgage to $0 and
Math.max hid the negative. _senior_surviving() is the ONE seam where feed dialects are allowed
to differ — any fourth feed (Tracerfy? county bulk? PB Landmark) adds a branch THERE and this
test gets a case for it.

BUG 2 adjacency: orsurvsen must be emitted unconditionally (a real 0 included) — the old
`if surv:` guard let the board's fallback read a JUNIOR as a surviving senior ($458,777 of
invented first mortgage across 7 leads). _fwd_flags() is asserted to always set it.
"""
import sys

import foreclosure_leads as F

fails = []


def chk(name, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (name, got, want))


# --- records/broward dialect: surv bundles juniors; subtract juniors_post -----------------------
chk('records: seniors = surv - juniors_post',
    F._senior_surviving({'source': 'records', 'surv': 500_000, 'juniors_post': 120_000}), 380_000)
chk('broward: same subtraction',
    F._senior_surviving({'source': 'broward', 'surv': 500_000, 'juniors_post': 120_000}), 380_000)
chk('records: clamp at 0, never negative',
    F._senior_surviving({'source': 'records', 'surv': 100_000, 'juniors_post': 150_000}), 0)
chk('records: missing juniors_post = no subtraction',
    F._senior_surviving({'source': 'records', 'surv': 250_000}), 250_000)

# --- BatchData dialect: surv is ALREADY seniors-only; subtracting again is the $811k bug --------
chk('batchdata: NO subtraction (the 502024CA012300XXXAMB case)',
    F._senior_surviving({'source': 'batchdata', 'surv': 811_577, 'juniors_post': 811_577}), 811_577)
chk('batchdata: case-insensitive source match',
    F._senior_surviving({'source': 'BatchData', 'surv': 200_000, 'juniors_post': 50_000}), 200_000)

# --- unknown/future feed: MUST default to the records dialect (conservative — subtracts) --------
# If a fourth feed emits seniors-only and nobody adds its branch, this default UNDERSTATES the
# senior (bad but visible) instead of double-counting silently. A new branch flips its case here.
chk('unknown feed defaults to records-style subtraction',
    F._senior_surviving({'source': 'landmark-pb', 'surv': 300_000, 'juniors_post': 100_000}), 200_000)

# --- orsurvsen emitted unconditionally (a real 0 included) --------------------------------------
d = {}
F._fwd_flags(d, {'source': 'records', 'surv': 0, 'juniors_post': 0, 'ftype': 'BANK-1st'}, 'BANK-1st')
if 'orsurvsen' not in d:
    fails.append("_fwd_flags: orsurvsen MISSING on a proved-nothing-survives chain — the phantom-junior "
                 "fallback bug ($458,777 invented) is back")
chk('orsurvsen real zero', d.get('orsurvsen'), 0)

if fails:
    print('FAIL (%d):' % len(fails))
    for f in fails:
        print(' -', f)
    sys.exit(1)
print('seniors-math regression: all %d checks pass' % 8)
