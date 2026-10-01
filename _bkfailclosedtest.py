#!/usr/bin/env python
"""_bkfailclosedtest — federal bankruptcy holds fail CLOSED everywhere they decide contact. No network.

Run:  python _bkfailclosedtest.py    (exit 0 = safe)

2026-09-29: these places turned a broken bankruptcy check into "nobody is held":
  * bk_lookup.flags_for_cases returned {} when stay_gate would not import, one case that raised
    took the whole call down, and an unreadable cache released every Miami lead it had flagged;
  * the board bake caught any bk_lookup error, printed SKIPPED and stamped no saleBkAct, and
    texting, the Morning Worker and cadence read saleBkAct;
  * call_mode's dial queue, the knock planner and outreach_email fell back to "hold only
    non-Miami case numbers", releasing every Miami lead the cache had flagged with an open case;
  * nothing told the owner a build had degraded.
Each now holds, and a degraded bake raises the bk-bake alert. Synthetic case numbers only
(2099 / CACE-99), no names, no token.
"""
import contextlib
import datetime as dt
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
import clerk_bk as CB           # noqa: E402
import call_mode as CM          # noqa: E402
import foreclosure_leads as FL  # noqa: E402
import morning_planner as MP    # noqa: E402
import outreach_email as OE     # noqa: E402
import pipeline_alerts as PA    # noqa: E402

BL.PACER_ROOT = str(TMP)        # no pacer_stay_cache.json here: no PACER release
SG._never_contact_path = lambda: str(TMP / SG.NEVER_CONTACT_NAME)

FAILS = []
MISSING = object()

MIA_ACTIVE = '2099-000701-CA-01'   # CourtListener: open case, exact match -> hard hold
MIA_CLEAR = '2099-000702-CA-01'    # CourtListener: fresh clear -> not held
MIA_NONE = '2099-000703-CA-01'     # no entry: Miami is not held for a missing check
BRO_NONE = 'CACE-99-555701'        # no entry: Broward is held until a fresh clear
CASES = [MIA_ACTIVE, MIA_CLEAR, MIA_NONE, BRO_NONE]
UNAVAILABLE = 'federal bankruptcy check unavailable — lead stays held'


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


@contextlib.contextmanager
def env(name, value):
    saved = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = saved


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
        n, deg = FL.stamp_federal_bk(slim)
    return n, deg, buf.getvalue()


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


def _clerk_active(key, now=None):
    if key == MIA_CLEAR:
        return {'blocks': True, 'code': 'stay_active', 'why': 'clerk docket: active stay'}
    return None


with patched(BL, 'entry_opinion', _raises_for_clear), patched(CB, 'gate_opinion', _clerk_active):
    flags = BL.flags_for_cases(CASES)
check('one case raising: the clerk check still runs for it and its active stay wins',
      flags.get(MIA_CLEAR, {}).get('hard') is True
      and flags[MIA_CLEAR].get('why') == 'clerk docket: active stay', flags)

_real_pk = SG.pacer_key


def _pk_raises(case):
    if str(case or '').strip().upper() == BRO_NONE:
        raise ValueError('bad case string')
    return _real_pk(case)


with patched(SG, 'pacer_key', _pk_raises):
    flags = BL.flags_for_cases(CASES)
check('pacer_key raising for one case: held under its strip+upper key, the rest judged',
      flags.get(BRO_NONE, {}).get('why') == BL.HOLD_ERRORED and flags.get(MIA_ACTIVE, {}).get('hard') is True
      and MIA_NONE not in flags and BL.FLAGS_DEGRADED == '1 case(s) errored', (flags, BL.FLAGS_DEGRADED))


def _overrides_boom():
    raise OSError('overrides unreadable')


with patched(BL, 'load_overrides', _overrides_boom):
    flags = BL.flags_for_cases(CASES)
check('cache load failing: every case is held',
      all(flags.get(c, {}).get('hold') is True for c in CASES), flags)
check('cache load failing: FLAGS_DEGRADED names it',
      BL.FLAGS_DEGRADED.startswith('cache load failed'), BL.FLAGS_DEGRADED)

for label, body in (('a JSON list', '[1, 2, 3]'), ('truncated JSON', '{"2099-000701-CA-01": {"sear')):
    write_cache(body)
    with patched(BL, 'pacer_confirmed', lambda *_a, **_k: True):
        flags = BL.flags_for_cases(CASES)
        deg = BL.FLAGS_DEGRADED
        BL._HOLD_MEMO = None
        dial = {c: BL.federal_hold(c)[0] for c in CASES}
    check('unreadable cache (%s): every lead is held, Miami included, PACER clear or not' % label,
          all(flags.get(c, {}).get('hold') is True for c in CASES)
          and all('unreadable' in flags[c].get('why', '') for c in CASES), flags)
    check('unreadable cache (%s): FLAGS_DEGRADED says so' % label, deg == 'cache unreadable', deg)
    check('unreadable cache (%s): the Call Mode index agrees, Miami included' % label,
          all(dial.values()), dial)

write_cache('[1, 2, 3]')
slim = rows(*CASES)
n, deg, out = bake(slim)
check('bake, unreadable cache: every row held and the log says DEGRADED',
      held_cases(slim) == sorted(CASES) and deg == 'cache unreadable' and 'DEGRADED' in out, (slim, out))

# ----------------------------------------------------------------------------- the board bake
healthy_cache()
slim = rows(*CASES) + [{'case': ''}]
n, deg, out = bake(slim)
check('bake healthy: the active Miami stay and the unchecked Broward lead are held',
      held_cases(slim) == sorted([MIA_ACTIVE, BRO_NONE]) and n == 2, (held_cases(slim), out))
check('bake healthy: held rows carry the reason', all(d.get('bkWhy') for d in slim if d.get('saleBkAct')), slim)
check('bake healthy: not degraded, no DEGRADED line', deg == '' and 'DEGRADED' not in out, (deg, out))

slim = rows(*CASES) + [{'case': ''}]
with blocked('bk_lookup'):
    n, deg, out = bake(slim)
check('bake, bk_lookup will not import: every row with a case number is held',
      held_cases(slim) == sorted(CASES) and n == len(CASES), (held_cases(slim), out))
check('bake, bk_lookup will not import: a row with no case number is untouched',
      'saleBkAct' not in slim[-1] and 'bkWhy' not in slim[-1], slim[-1])
check('bake, bk_lookup will not import: degraded is returned and logged',
      deg.startswith('bk_lookup failed') and 'DEGRADED' in out, (deg, out))


def _flags_boom(_cases, now=None):
    raise RuntimeError('EXAMPLE-OWNER-NAME leaked into an error')


slim = rows(*CASES)
with patched(BL, 'flags_for_cases', _flags_boom):
    n, deg, out = bake(slim)
check('bake, flags_for_cases raising: every row is held', held_cases(slim) == sorted(CASES), (slim, out))
check('bake, flags_for_cases raising: the log names the class, never the message',
      'DEGRADED' in out and 'RuntimeError' in out and 'EXAMPLE-OWNER-NAME' not in out
      and 'EXAMPLE-OWNER-NAME' not in deg, (deg, out))

slim = rows(*CASES)
with patched(BL, 'flags_for_cases', lambda _cases, now=None: None):
    n, deg, out = bake(slim)
check('bake, flags_for_cases returning a non-dict: every row is held',
      held_cases(slim) == sorted(CASES) and 'DEGRADED' in out, (slim, out))

slim = rows(*CASES) + [{'case': ' cace-99-555702 '}]
with blocked('stay_gate'):
    n, deg, out = bake(slim)
check('bake, stay_gate will not import: every row is held, a messy case number included',
      all(d.get('saleBkAct') for d in slim) and n == len(slim), (slim, out))
check('bake, stay_gate will not import: the log says DEGRADED', 'DEGRADED' in out, out)

# A row a docket stay already held keeps its wording unless the federal hold is an open case.
docket = {'case': BRO_NONE, 'saleBkAct': True, 'saleBkD': '2099-01-02'}
fed_open = {'case': MIA_ACTIVE, 'saleBkAct': True, 'bkWhy': 'docket stay'}
plain = {'case': MIA_CLEAR, 'saleBkAct': True}
with blocked('bk_lookup'):
    bake([docket, dict(plain)])
check('bake, degraded: a docket-stayed row keeps its own wording (no "check unavailable")',
      docket.get('saleBkAct') is True and 'bkWhy' not in docket, docket)
docket = {'case': BRO_NONE, 'saleBkAct': True, 'saleBkD': '2099-01-02'}
healthy_cache()
bake([docket, fed_open, plain])
check('bake, healthy: a soft federal hold does not replace a docket stay\'s wording',
      docket.get('saleBkAct') is True and 'bkWhy' not in docket, docket)
check('bake, healthy: an open federal case does replace it, and stays held',
      fed_open.get('saleBkAct') is True and 'exact match' in fed_open.get('bkWhy', ''), fed_open)
check('bake never clears a saleBkAct another gate stamped (a federal clear included)',
      plain.get('saleBkAct') is True, plain)

# A lifted docket stay must not lift a federal hold: the board's door gate reads saleBkAct && !saleLift.
tpl = (HERE / 'tracker_template.html').read_text(encoding='utf-8')
check('board door gate still reads saleBkAct && !saleLift (why the bake drops saleLift)',
      "if(r.saleBkAct && !r.saleLift) return {ok:false" in tpl)
lifted = {'case': MIA_ACTIVE, 'saleLift': '2099-01-03'}
clear_lifted = {'case': MIA_CLEAR, 'saleLift': '2099-01-03'}
healthy_cache()
bake([lifted, clear_lifted])
check('bake, open federal case on a lifted docket row: held and saleLift dropped',
      lifted.get('saleBkAct') is True and 'saleLift' not in lifted, lifted)
check('bake, a federal clear keeps a lifted row lifted', 'saleLift' in clear_lifted
      and not clear_lifted.get('saleBkAct'), clear_lifted)
lifted = {'case': MIA_NONE, 'saleLift': '2099-01-03'}
with blocked('bk_lookup'):
    bake([lifted])
check('bake, degraded: a lifted row is held and saleLift dropped',
      lifted.get('saleBkAct') is True and 'saleLift' not in lifted, lifted)

# ------------------------------------------------------------------------ census + owner alert
src = (HERE / 'foreclosure_leads.py').read_text(encoding='utf-8')
check('census: the bake result feeds bkdeg',
      '_bk_held, _bk_degraded = stamp_federal_bk(slim)' in src and "'bkdeg': 1 if _bk_degraded else 0" in src)


def board(cov):
    d = TMP / ('board_%d' % len(os.listdir(TMP)))
    (d / 'docs').mkdir(parents=True)
    marker = '' if cov is None else '<!-- DEALFLOW-COVERAGE ' + json.dumps(cov) + ' -->\n'
    (d / 'docs' / 'index.html').write_text(marker + '<html></html>', encoding='utf-8')
    return str(d)


check('alert: a degraded build reads as degraded', PA.read_bake_bk(board({'leads': 9, 'bkdeg': 1})) == {'degraded': True})
check('alert: a clean build reads as not degraded', PA.read_bake_bk(board({'leads': 9, 'bkdeg': 0})) == {'degraded': False})
check('alert: an older build with no bkdeg reads as not degraded', PA.read_bake_bk(board({'leads': 9})) == {'degraded': False})
check('alert: a board with no census reads as not degraded', PA.read_bake_bk(board(None)) == {'degraded': False})
a = PA.bake_bk_alert({'degraded': True}, 'at')
check('alert: degraded -> a bk-bake fail with no counts or cases',
      a and a.get('key') == 'bk-bake' and a.get('severity') == 'fail' and not any(ch.isdigit() for ch in a.get('text', '')), a)
check('alert: not degraded -> no alert', PA.bake_bk_alert({'degraded': False}, 'at') is None
      and PA.bake_bk_alert(None, 'at') is None)
_now = dt.datetime(2099, 1, 5, 12, 0, tzinfo=dt.timezone.utc)
keys = [x['key'] for x in PA.alerts_from({'bake_bk': {'degraded': True}}, _now)]
check('alert: alerts_from raises bk-bake', 'bk-bake' in keys, keys)
check('alert: gather() reads the bake signal',
      "'bake_bk': _try(read_bake_bk, {'degraded': False})" in (HERE / 'pipeline_alerts.py').read_text(encoding='utf-8'))

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

# ------------------------------------------------------------------ knock planner + email/cadence
healthy_cache()
knock = {'case': MIA_NONE, 'county': 'MIAMI-DADE', 'st': 'LP', 'owners': 'ROE,MARY', 'addr': '10 EAST ST',
         'days': 9999, 'eq': None, 'phones': ['9545550101'], 'vac': False}
ok_healthy = MP._knock_eligible(dict(knock))
with patched(BL, 'federal_hold', _hold_boom):
    ok_broken = MP._knock_eligible(dict(knock))
check('knock planner: a Miami lead it would route is held when federal_hold raises',
      ok_healthy is True and ok_broken is False, (ok_healthy, ok_broken))
with blocked('bk_lookup'):
    ok_blocked = MP._knock_eligible(dict(knock))
check('knock planner: and when bk_lookup will not import', ok_blocked is False, ok_blocked)

leads = [{'case': MIA_NONE}, {'case': MIA_CLEAR, 'saleLift': True}, {'case': BRO_NONE}]
with blocked('bk_lookup'):
    OE._stamp_federal_holds(leads)
check('outreach_email load, bk_lookup will not import: every row is held for cadence, Miami included',
      all(r.get('saleBkAct') and r.get('sale_bk_active') and r.get('bkWhy') == UNAVAILABLE for r in leads)
      and 'saleLift' not in leads[1], leads)

leads = [{'case': MIA_NONE}, {'case': BRO_NONE}]
with blocked('bk_lookup'), blocked('stay_gate'):
    OE._stamp_federal_holds(leads)
check('outreach_email load: still held when stay_gate will not import either (it used to hold nothing)',
      all(r.get('saleBkAct') for r in leads), leads)

row = {'case': MIA_NONE, 'owners': 'ROE,MARY', 'emails': ['owner@example.invalid']}
with patched(OE, '_worker_hist', lambda ledger=None: {}):
    with patched(BL, 'contact_blocked_reason', lambda *_a, **_k: (_ for _ in ()).throw(OSError('x'))):
        ok, why = OE._eligible(dict(row), None, set(), 0)
    with blocked('bk_lookup'):
        ok2, why2 = OE._eligible(dict(row), None, set(), 0)
check('outreach_email eligibility: a Miami lead is held when the send check raises',
      ok is False and why == UNAVAILABLE, (ok, why))
check('outreach_email eligibility: and when bk_lookup will not import', ok2 is False and why2 == UNAVAILABLE, (ok2, why2))

print()
print('==== %s ====' % ('FAILED: %d check(s)' % len(FAILS) if FAILS
                          else 'all bankruptcy fail-closed checks passed'))
sys.exit(1 if FAILS else 0)
