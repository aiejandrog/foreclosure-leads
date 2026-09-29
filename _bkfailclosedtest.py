#!/usr/bin/env python
"""_bkfailclosedtest — the board bake's federal bankruptcy holds fail CLOSED. No network.

Run:  python _bkfailclosedtest.py    (exit 0 = safe)

2026-09-29: three places turned a broken bankruptcy check into "nobody is held":
  * bk_lookup.flags_for_cases returned {} when stay_gate would not import, and one case that
    raised took the whole call down;
  * the board bake caught any bk_lookup error, printed SKIPPED and stamped no saleBkAct, and
    texting, the Morning Worker and cadence read saleBkAct;
  * call_mode's dial queue fell back to "hold only non-Miami case numbers" when its index would
    not load, releasing every Miami lead the cache had flagged with an active stay.
Each now holds. Synthetic case numbers only (2099 / CACE-99), no names, no token.
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = pathlib.Path(tempfile.mkdtemp(prefix='bkfailclosed_'))

for _k in ('COURTLISTENER_TOKEN', 'DEALFLOW_BK_PROVIDER', 'DEALFLOW_BK_MAX_AGE_DAYS',
           'DEALFLOW_BK_CACHE', 'DEALFLOW_BK_FILINGS', 'DEALFLOW_BK_OVERRIDES',
           'DEALFLOW_BK_STATUS', 'DEALFLOW_BK_BUDGET', 'DEALFLOW_BK_PULL_STATE',
           'DEALFLOW_BK_ALLOW_CL_CLEAR', 'DEALFLOW_CLERK_BK'):
    os.environ.pop(_k, None)
os.environ['DEALFLOW_DIR'] = str(TMP)
os.environ['DEALFLOW_BK_CACHE'] = str(TMP / 'bk_lead_cache.json')
os.environ['DEALFLOW_BK_FILINGS'] = str(TMP / 'bk_filings.json')
os.environ['DEALFLOW_BK_OVERRIDES'] = str(TMP / 'bk_overrides.json')
os.environ['DEALFLOW_BK_STATUS'] = str(TMP / 'bk_lookup_status.json')

import bk_lookup as BL          # noqa: E402
import stay_gate as SG          # noqa: E402
import call_mode as CM          # noqa: E402
import foreclosure_leads as FL  # noqa: E402

BL.PACER_ROOT = str(TMP)        # no pacer_stay_cache.json here: no PACER release
SG._never_contact_path = lambda: str(TMP / SG.NEVER_CONTACT_NAME)

FAILS = []
MISSING = object()

MIA_ACTIVE = '2099-000701-CA-01'   # CourtListener: open case, exact match -> hard hold
MIA_CLEAR = '2099-000702-CA-01'    # CourtListener: fresh clear -> not held
MIA_NONE = '2099-000703-CA-01'     # no entry: Miami is not held for a missing check
BRO_NONE = 'CACE-99-555701'        # no entry: Broward is held until a fresh clear
CASES = [MIA_ACTIVE, MIA_CLEAR, MIA_NONE, BRO_NONE]


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)[:400]) if not cond else ''))
    if not cond:
        FAILS.append(name)


@contextlib.contextmanager
def blocked(name):
    """`import name` raises ImportError inside the block, the way a broken module would."""
    saved = sys.modules.get(name, MISSING)
    sys.modules[name] = None
    try:
        yield
    finally:
        if saved is MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved


@contextlib.contextmanager
def patched(obj, attr, value):
    saved = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, saved)


def write_cache(data):
    p = BL.cache_path()
    with open(p, 'w', encoding='utf-8') as f:
        f.write(data if isinstance(data, str) else json.dumps(data))
    BL._HOLD_MEMO = None


def healthy_cache():
    now = time.time()
    write_cache({
        MIA_ACTIVE: {'searched': True, 'ok_check': True, 'err': '', 't': now, 'src': 'courtlistener',
                     'cases': [{'no': '99-55701', 'open': True, 'match': 'exact', 'filed': '2099-01-02'}]},
        MIA_CLEAR: {'searched': True, 'ok_check': True, 'err': '', 't': now, 'src': 'courtlistener',
                    'cases': []},
    })


def rows(*cases):
    return [{'case': c} for c in cases]


def bake(slim):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        n = FL.stamp_federal_bk(slim)
    return n, buf.getvalue()


def held_cases(slim):
    return sorted(str(d.get('case')) for d in slim if d.get('saleBkAct'))


# ---------------------------------------------------------------------------- flags_for_cases
healthy_cache()
flags = BL.flags_for_cases(CASES)
check('healthy: an active Miami stay is a hard hold',
      flags.get(MIA_ACTIVE, {}).get('hold') is True and flags[MIA_ACTIVE].get('hard') is True, flags)
check('healthy: a clear Miami lead and an unchecked Miami lead are not held',
      MIA_CLEAR not in flags and MIA_NONE not in flags, flags)
check('healthy: an unchecked Broward lead is held', flags.get(BRO_NONE, {}).get('hold') is True, flags)
check('healthy: FLAGS_DEGRADED is empty', BL.FLAGS_DEGRADED == '', BL.FLAGS_DEGRADED)

with blocked('stay_gate'):
    flags = BL.flags_for_cases(CASES + [' cace-99-555702 ', '', None])
check('stay_gate missing: every case is held, Miami included',
      all(flags.get(c, {}).get('hold') is True for c in CASES), flags)
check('stay_gate missing: keys are strip+upper, the key the bake tries first',
      flags.get('CACE-99-555702', {}).get('hold') is True, sorted(flags))
check('stay_gate missing: a blank case is not keyed', '' not in flags and 'NONE' not in flags, sorted(flags))
check('stay_gate missing: the reason says the check is unavailable',
      all(v.get('why') == BL.HOLD_UNAVAILABLE and v.get('hard') is False for v in flags.values()), flags)
check('stay_gate missing: FLAGS_DEGRADED names it',
      BL.FLAGS_DEGRADED.startswith('stay_gate unavailable'), BL.FLAGS_DEGRADED)

_real_opinion = BL.entry_opinion


def _raises_for_clear(key, ent, overrides, now):
    if key == MIA_CLEAR:
        raise KeyError('corrupt entry')
    return _real_opinion(key, ent, overrides, now)


try:
    with patched(BL, 'entry_opinion', _raises_for_clear):
        flags = BL.flags_for_cases(CASES)
    raised = None
except Exception as e:
    flags, raised = {}, e
check('one case raising does not take the call down', raised is None, repr(raised))
check('one case raising: that case is held (it was a clear before the error)',
      flags.get(MIA_CLEAR, {}).get('hold') is True and flags[MIA_CLEAR].get('why') == BL.HOLD_ERRORED, flags)
check('one case raising: the rest are still judged normally',
      flags.get(MIA_ACTIVE, {}).get('hard') is True and MIA_NONE not in flags
      and flags.get(BRO_NONE, {}).get('hold') is True, flags)
check('one case raising: FLAGS_DEGRADED counts it', BL.FLAGS_DEGRADED == '1 case(s) errored', BL.FLAGS_DEGRADED)


def _overrides_boom():
    raise OSError('overrides unreadable')


with patched(BL, 'load_overrides', _overrides_boom):
    flags = BL.flags_for_cases(CASES)
check('cache load failing: every case is held',
      all(flags.get(c, {}).get('hold') is True for c in CASES), flags)
check('cache load failing: FLAGS_DEGRADED names it',
      BL.FLAGS_DEGRADED.startswith('cache load failed'), BL.FLAGS_DEGRADED)

write_cache('[1, 2, 3]')          # JSON, but not the dict the cache must be
with patched(BL, 'pacer_confirmed', lambda *_a, **_k: True):
    flags = BL.flags_for_cases(CASES)
    BL._HOLD_MEMO = None
    dial = BL.federal_hold(BRO_NONE)
check('unreadable cache: Broward is held even with a PACER clear on file',
      flags.get(BRO_NONE, {}).get('hold') is True and 'unreadable' in flags[BRO_NONE].get('why', ''), flags)
check('unreadable cache: the board bake and the Call Mode index agree on Broward',
      dial[0] is True, dial)
check('unreadable cache: Miami is not held for it (same rule as HoldIndex)',
      MIA_NONE not in flags, flags)

# ----------------------------------------------------------------------------- the board bake
healthy_cache()
slim = rows(*CASES) + [{'case': ''}]
n, out = bake(slim)
check('bake healthy: the active Miami stay and the unchecked Broward lead are held',
      held_cases(slim) == sorted([MIA_ACTIVE, BRO_NONE]) and n == 2, (held_cases(slim), out))
check('bake healthy: held rows carry the reason', all(d.get('bkWhy') for d in slim if d.get('saleBkAct')), slim)
check('bake healthy: no DEGRADED line', 'DEGRADED' not in out, out)

slim = rows(*CASES) + [{'case': ''}]
with blocked('bk_lookup'):
    n, out = bake(slim)
check('bake, bk_lookup will not import: every row with a case number is held',
      held_cases(slim) == sorted(CASES) and n == len(CASES), (held_cases(slim), out))
check('bake, bk_lookup will not import: a row with no case number is untouched',
      'saleBkAct' not in slim[-1] and 'bkWhy' not in slim[-1], slim[-1])
check('bake, bk_lookup will not import: the log says DEGRADED', 'DEGRADED' in out, out)


def _flags_boom(_cases, now=None):
    raise RuntimeError('EXAMPLE-OWNER-NAME leaked into an error')


slim = rows(*CASES)
with patched(BL, 'flags_for_cases', _flags_boom):
    n, out = bake(slim)
check('bake, flags_for_cases raising: every row is held', held_cases(slim) == sorted(CASES), (slim, out))
check('bake, flags_for_cases raising: the log names the class, never the message',
      'DEGRADED' in out and 'RuntimeError' in out and 'EXAMPLE-OWNER-NAME' not in out, out)

slim = rows(*CASES)
with patched(BL, 'flags_for_cases', lambda _cases, now=None: None):
    n, out = bake(slim)
check('bake, flags_for_cases returning a non-dict: every row is held',
      held_cases(slim) == sorted(CASES) and 'DEGRADED' in out, (slim, out))

slim = rows(*CASES) + [{'case': ' cace-99-555702 '}]
with blocked('stay_gate'):
    n, out = bake(slim)
check('bake, stay_gate will not import: every row is held, a messy case number included',
      all(d.get('saleBkAct') for d in slim) and n == len(slim), (slim, out))
check('bake, stay_gate will not import: the log says DEGRADED', 'DEGRADED' in out, out)

slim = [{'case': MIA_CLEAR, 'saleBkAct': True}]
n, out = bake(slim)
check('bake never clears a saleBkAct another gate stamped', slim[0].get('saleBkAct') is True, slim)

# --------------------------------------------------------------------------- Call Mode dial queue
healthy_cache()
fn = CM.federal_hold_fn()
check('call mode healthy: active Miami held, clear and unchecked Miami callable, Broward held',
      fn(MIA_ACTIVE) is True and fn(MIA_CLEAR) is False and fn(MIA_NONE) is False and fn(BRO_NONE) is True,
      [fn(c) for c in CASES])

with blocked('bk_lookup'):
    fn = CM.federal_hold_fn()
check('call mode, bk_lookup will not import: every lead is held, Miami included',
      all(fn(c) is True for c in CASES), [fn(c) for c in CASES])


def _index_boom(now=None):
    raise OSError('cache locked')


with patched(BL, 'federal_hold_index', _index_boom):
    fn = CM.federal_hold_fn()
check('call mode, the index will not load: every lead is held',
      all(fn(c) is True for c in CASES), [fn(c) for c in CASES])

healthy_cache()
fn = CM.federal_hold_fn()


def _hold_boom(case, here=None, index=None):
    raise ValueError('bad row')


with patched(BL, 'federal_hold', _hold_boom):
    res = [fn(c) for c in CASES]
check('call mode, federal_hold raising for a row: that row is held (a clear Miami lead too)',
      all(r is True for r in res), res)

print()
print('==== %s ====' % ('FAILED: %d check(s)' % len(FAILS) if FAILS
                          else 'all bankruptcy fail-closed checks passed'))
sys.exit(1 if FAILS else 0)
