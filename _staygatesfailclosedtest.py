#!/usr/bin/env python
"""_staygatesfailclosedtest — the Miami-Dade docket stay paths fail CLOSED. No network.

Run:  python _staygatesfailclosedtest.py    (exit 0 = safe)

2026-09-29, the follow-up to _bkfailclosedtest. Each of these released stayed leads when its
stay data broke:
  * sale_history read a corrupt sale_history_cache.json as {} and then overwrote it, erasing every
    active stay it did not re-read; a capped or failed read left lis pendens rows unstamped;
  * the board bake restored no stays from a bad cache and published every Miami owner workable;
  * cadence re-checked stays only through row flags and sent through _smtp_send, not /send;
  * the Lob letter queue released Miami on a docket stay_active or stay_data_unavailable;
  * the CRM / 3-DAY fallback to raw county files carried no federal holds;
  * an unreadable never_contact.json held only the 3 built-in cases on the board;
  * the CourtListener pull and pre-send search overwrote a corrupt bk_lead_cache.json.
Temp folders only; the tracked sale_history_cache.json is never read or written. Synthetic
case numbers (2099 / CACE-99), no names.
"""
import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = pathlib.Path(tempfile.mkdtemp(prefix='staygates_'))

for _k in ('COURTLISTENER_TOKEN', 'DEALFLOW_BK_PROVIDER', 'DEALFLOW_BK_MAX_AGE_DAYS',
           'DEALFLOW_BK_CACHE', 'DEALFLOW_BK_FILINGS', 'DEALFLOW_BK_OVERRIDES',
           'DEALFLOW_BK_STATUS', 'DEALFLOW_BK_BUDGET', 'DEALFLOW_BK_PULL_STATE',
           'DEALFLOW_BK_ALLOW_CL_CLEAR', 'DEALFLOW_CLERK_BK'):
    os.environ.pop(_k, None)
os.environ['DEALFLOW_DIR'] = str(TMP)
for _n, _f in (('CACHE', 'bk_lead_cache.json'), ('FILINGS', 'bk_filings.json'),
               ('OVERRIDES', 'bk_overrides.json'), ('STATUS', 'bk_lookup_status.json'),
               ('BUDGET', 'bk_budget.json'), ('PULL_STATE', 'bk_pull_state.json')):
    os.environ['DEALFLOW_BK_' + _n] = str(TMP / _f)

import stay_gate as SG            # noqa: E402
import bk_lookup as BL            # noqa: E402
import sale_history as SH         # noqa: E402
import foreclosure_leads as FL    # noqa: E402
import outreach_mail as OM        # noqa: E402
import sheets_crm as CRM          # noqa: E402
import three_day as TD            # noqa: E402

NC_PATH = TMP / SG.NEVER_CONTACT_NAME
SG._never_contact_path = lambda: str(NC_PATH)
BL.PACER_ROOT = str(TMP)

FAILS = []
MISSING = object()
MIA = '2099-000801-CA-01'
MIA2 = '2099-000802-CA-01'
BRO = 'CACE-99-555801'


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)[:400]) if not cond else ''))
    if not cond:
        FAILS.append(name)


@contextlib.contextmanager
def blocked(name):
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


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = fn(*a, **k)
    return res, buf.getvalue()


def raises(*_a, **_k):
    raise OSError('boom')


def fresh_dir(label):
    d = TMP / label
    d.mkdir(parents=True, exist_ok=True)
    return d


def put(d, name, obj):
    p = pathlib.Path(d) / name
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=1), encoding='utf-8')
    return p


def raw(d, name):
    p = pathlib.Path(d) / name
    return p.read_bytes() if p.exists() else None


# =============================================================================== 1. sale_history
CLEAN = [{'docketDescrition': 'Notice of Foreclosure Sale', 'eventDate': '01/05/2026'}]
DOCKETS, CALLS = {}, []


def fake_fetch(session, case):
    CALLS.append(case)
    return DOCKETS.get(case, CLEAN)


SH._fetch = fake_fetch
SH.time.sleep = lambda s: None
NOW = SH.time.time()
OLD = NOW - 30 * 86400


def ent(a=False, t=NOW, **kw):
    e = {'s': 0, 'n': 1, 'd': 0, 'w': '', 'b': 0, 'a': a, 'bd': '', 'sl': '', 't': t, 'v': SH.CACHE_VER}
    e.update(kw)
    return e


def lp(case, filed='2026-09-01'):
    return {'county': 'MIAMI-DADE', 'st': 'LP', 'case': case, 'owners': 'TEST OWNER', 'filedDate': filed}


def auction(case, days=40):
    t = SH.time.localtime(NOW + days * 86400)
    return {'Case #': case, 'AuctionDate': SH.time.strftime('%m/%d/%Y', t), 'sale_type': 'FC'}


def sh_world(label):
    d = fresh_dir(label)
    SH.HERE = str(d)
    SH.CACHE = str(d / 'sale_history_cache.json')
    del CALLS[:]
    DOCKETS.clear()
    del SH._NC_WARNED[:]
    return d


for label, body in (('truncated', '{"2099-000801-CA-01": {"a": tr'), ('list', '[1, 2]')):
    d = sh_world('sh_corrupt_' + label)
    put(d, 'leads_final.json', [auction(MIA)])
    put(d, 'lp_leads.json', [lp(MIA2)])
    put(d, 'sale_history_cache.json', body)
    before = {n: raw(d, n) for n in ('sale_history_cache.json', 'leads_final.json', 'lp_leads.json')}
    rc, out = quiet(SH.main, ['--limit', '5'])
    after = {n: raw(d, n) for n in before}
    check('sale_history, %s cache: refuses with exit 2 and reads nothing' % label,
          rc == 2 and CALLS == [] and 'unreadable' in out, (rc, CALLS, out[-300:]))
    check('sale_history, %s cache: writes NOTHING (cache, leads_final, lp_leads byte-identical)' % label,
          before == after, [n for n in before if before[n] != after[n]])
    check('sale_history, %s cache: no .bak or .tmp left from a refused run' % label,
          raw(d, 'sale_history_cache.json.bak') is None and raw(d, 'sale_history_cache.json.tmp') is None)

d = sh_world('sh_capped')
NEW_LP = '2099-000810-CA-01'          # never read: takes the one fetch
STALE_ACT = '2099-000811-CA-01'       # stale, cached ACTIVE, past the budget
STALE_CLR = '2099-000812-CA-01'       # stale, cached clear, past the budget
FAIL_ACT = '2099-000813-CA-01'        # stale ACTIVE, the fetch fails
FAIL_CLR = '2099-000814-CA-01'        # stale clear, the fetch fails
AUC_ACT = '2099-000815-CA-01'         # auction row, stale ACTIVE, past the budget
put(d, 'leads_final.json', [auction(AUC_ACT)])
put(d, 'lp_leads.json', [lp(NEW_LP, '2026-09-20'), lp(STALE_ACT), lp(STALE_CLR),
                         dict(lp(FAIL_ACT), saleLift='2099-01-01')])
cache0 = {STALE_ACT: ent(a=True, bd='2099-01-02', t=OLD), STALE_CLR: ent(t=OLD),
          FAIL_ACT: ent(a=True, bd='2099-01-03', t=OLD - 10), AUC_ACT: ent(a=True, bd='2099-01-04', t=OLD)}
put(d, 'sale_history_cache.json', cache0)
rc, out = quiet(SH.main, ['--limit', '1'])
rows = {r['case']: r for r in json.loads(raw(d, 'lp_leads.json'))}
lf = json.loads(raw(d, 'leads_final.json'))
c1 = json.loads(raw(d, 'sale_history_cache.json'))
check('capped: the one fetch went to the never-read LP case', CALLS == [NEW_LP], CALLS)
check('capped: an LP row whose stale cache entry is ACTIVE is held',
      rows[STALE_ACT].get('saleBkAct') is True and rows[STALE_ACT].get('saleBkD') == '2099-01-02', rows[STALE_ACT])
check('capped: an LP row whose stale entry is clear stays unflagged', not rows[STALE_CLR].get('saleBkAct'), rows[STALE_CLR])
check('capped: an auction row whose stale entry is ACTIVE is held',
      lf[0].get('sale_bk_active') is True, lf[0])
check('capped: the cache entries are untouched (the next live read decides)',
      c1[STALE_ACT] == cache0[STALE_ACT] and c1[STALE_CLR] == cache0[STALE_CLR] and c1[AUC_ACT] == cache0[AUC_ACT])
check('capped: the log says how many kept their cached stay', 'kept the ACTIVE stay' in out, out[-300:])

d = sh_world('sh_failed')
put(d, 'lp_leads.json', [dict(lp(FAIL_ACT), saleLift='2099-01-01'), lp(FAIL_CLR)])
cache0 = {FAIL_ACT: ent(a=True, bd='2099-01-03', t=OLD), FAIL_CLR: ent(t=OLD)}
put(d, 'sale_history_cache.json', cache0)
DOCKETS[FAIL_ACT] = None
DOCKETS[FAIL_CLR] = None
rc, out = quiet(SH.main, ['--limit', '5'])
rows = {r['case']: r for r in json.loads(raw(d, 'lp_leads.json'))}
c1 = json.loads(raw(d, 'sale_history_cache.json'))
check('failed read: a cached ACTIVE stay still holds the LP row and drops its lift date',
      rows[FAIL_ACT].get('saleBkAct') is True and 'saleLift' not in rows[FAIL_ACT], rows[FAIL_ACT])
check('failed read: a cached clear is not turned into a hold', not rows[FAIL_CLR].get('saleBkAct'), rows[FAIL_CLR])
check('failed read: the cache entries are untouched', c1 == cache0, c1)

d = sh_world('sh_healthy')
put(d, 'leads_final.json', [auction(MIA)])
cache0 = {MIA: ent(t=OLD)}
put(d, 'sale_history_cache.json', cache0)
rc, out = quiet(SH.main, ['--limit', '5'])
check('healthy run: exit 0 and a .bak of the cache it started from',
      rc in (None, 0) and json.loads(raw(d, 'sale_history_cache.json.bak')) == cache0, (rc, out[-200:]))
check('healthy run: writes are atomic (no .tmp left) and the cache parses',
      raw(d, 'sale_history_cache.json.tmp') is None and raw(d, 'leads_final.json.tmp') is None
      and isinstance(json.loads(raw(d, 'sale_history_cache.json')), dict))
src = (HERE / 'sale_history.py').read_text(encoding='utf-8')
check('no non-atomic json.dump(..., open(...)) write is left in sale_history', 'json.dump(cache, open(' not in src
      and 'json.dump(leads, open(' not in src)

d = sh_world('sh_nc')
put(d, 'leads_final.json', [auction(MIA), auction(MIA2)])
put(d, 'sale_history_cache.json', {MIA: ent(t=OLD), MIA2: ent(t=OLD)})
NC_PATH.write_text('{not a list', encoding='utf-8')
rc, out = quiet(SH.main, ['--limit', '5'])
check('never-contact unreadable: sale_history says so, once', out.count('never_contact.json') == 1, out[-400:])
c1 = json.loads(raw(d, 'sale_history_cache.json'))
check('never-contact unreadable: no case is written into the cache as a stay (it would outlive the file)',
      not c1[MIA].get('a') and not c1[MIA2].get('a'), c1)

# =========================================================================== 2. bk_lookup
BL._HOLD_MEMO = None
flags = BL.flags_for_cases([MIA, MIA2, BRO])
check('never-contact unreadable: flags_for_cases holds every case, Miami included',
      all(flags.get(c, {}).get('hold') for c in (MIA, MIA2, BRO)), flags)
check('never-contact unreadable: FLAGS_DEGRADED names it', 'never-contact list unreadable' in BL.FLAGS_DEGRADED,
      BL.FLAGS_DEGRADED)
NC_PATH.write_text(json.dumps([MIA2]), encoding='utf-8')
put(TMP, 'bk_lead_cache.json', {MIA2: {'searched': True, 'ok_check': True, 'err': '', 't': NOW,
                                       'src': 'courtlistener', 'cases': []}})
BL._HOLD_MEMO = None
flags = BL.flags_for_cases([MIA, MIA2])
check('never-contact extra case: a hard hold on the board even with a CourtListener clear',
      flags.get(MIA2, {}).get('hard') is True and 'never contacted again' in flags[MIA2].get('why', '')
      and MIA not in flags and BL.FLAGS_DEGRADED == '', (flags, BL.FLAGS_DEGRADED))
NC_PATH.unlink()

bkc = put(TMP, 'bk_lead_cache.json', '{"CACE-99-555801": {"cases": [')
b0 = bkc.read_bytes()
try:
    BL.save_cache({'x': {}})
    saved = 'wrote'
except BL.CacheUnreadable:
    saved = 'refused'
check('bk cache unreadable: save_cache refuses to write over it', saved == 'refused' and bkc.read_bytes() == b0, saved)
TCALLS = []


def transport(*a, **k):
    TCALLS.append(a)
    raise AssertionError('no request should be made')


res, out = quiet(BL.run_nightly, here=str(TMP), env={}, transport=transport)
st = json.loads(raw(TMP, 'bk_lookup_status.json') or b'{}')
check('bk cache unreadable: the nightly pull stops, saves nothing, and says so in its status',
      res.get('reason') == 'cache_unreadable' and bkc.read_bytes() == b0 and st.get('pull_ok') is False
      and st.get('reason') == 'cache_unreadable' and TCALLS == [], (res, st, TCALLS))
res = BL.presend_check(BRO, here=str(TMP), env={}, transport=transport)
check('bk cache unreadable: the pre-send search stops without a request or a write',
      res.get('status') == 'error' and TCALLS == [] and bkc.read_bytes() == b0, res)
bkc.unlink()

stay_dir = fresh_dir('staycache')
for label, body, bad in (('missing', None, True), ('empty', '{}', True), ('truncated', '{"a": ', True),
                         ('list', '[]', True), ('healthy', json.dumps({MIA: ent(a=True)}), False)):
    p = stay_dir / 'sale_history_cache.json'
    if p.exists():
        p.unlink()
    if body is not None:
        p.write_text(body, encoding='utf-8')
    err = BL.stay_cache_error(str(stay_dir))
    check('stay_cache_error, %s cache -> %s' % (label, 'a reason' if bad else "''"), bool(err) is bad, err)

# ======================================================================= 3. the board bake
bake_dir = fresh_dir('bake')
saved_here = FL.HERE


def slim():
    return [{'case': MIA}, {'case': MIA2, 'saleBkAct': True, 'bkWhy': 'docket: active stay'},
            {'case': MIA2.replace('802', '803'), 'saleLift': '2099-01-01'}, {'case': BRO}, {'case': ''}]


try:
    FL.HERE = str(bake_dir)
    for label, body in (('missing', None), ('truncated', '{"x": '), ('empty', '{}')):
        p = bake_dir / 'sale_history_cache.json'
        if p.exists():
            p.unlink()
        if body is not None:
            p.write_text(body, encoding='utf-8')
        rows = slim()
        deg, out = quiet(FL.hold_miami_on_bad_stay_cache, rows)
        miami = [r for r in rows if SG.case_stem(r['case'])]
        check('bake, %s stay cache: every Miami-Dade row is held and the lift date dropped' % label,
              all(r.get('saleBkAct') is True and 'saleLift' not in r for r in miami), rows)
        check('bake, %s stay cache: Broward and blank rows untouched' % label,
              'saleBkAct' not in rows[3] and 'saleBkAct' not in rows[4], rows[3:])
        check('bake, %s stay cache: a row another gate held keeps its wording' % label,
              rows[1]['bkWhy'] == 'docket: active stay' and rows[0].get('bkWhy') == FL.MIAMI_STAY_DATA_WHY, rows[:2])
        check('bake, %s stay cache: degraded is returned and logged' % label,
              deg == 'stay data unavailable' and 'DEGRADED' in out, (deg, out))
    (bake_dir / 'sale_history_cache.json').write_text(json.dumps({MIA: ent()}), encoding='utf-8')
    rows = slim()
    deg, out = quiet(FL.hold_miami_on_bad_stay_cache, rows)
    check('bake, healthy stay cache: nothing held, nothing logged', deg == '' and out == '' and rows == slim(), rows)
    (bake_dir / 'sale_history_cache.json').write_text('[1]', encoding='utf-8')
    rows = slim()
    with blocked('stay_gate'):
        deg, out = quiet(FL.hold_miami_on_bad_stay_cache, rows)
    check('bake, stay_gate will not import: every row with a case number is held',
          all(r.get('saleBkAct') for r in rows[:4]) and 'saleBkAct' not in rows[4] and deg, rows)
    for label, body in (('truncated', '{"x": '), ('a JSON list', '[1, 2]')):
        (bake_dir / 'sale_history_cache.json').write_text(body, encoding='utf-8')
        leads = [{'Case #': MIA}]
        try:
            n, out = quiet(FL.restore_stays_from_cache, leads)
            err = None
        except Exception as e:
            n, out, err = None, '', e
        check('restore_stays_from_cache, %s cache: no crash, nothing written onto the lead rows' % label,
              err is None and n == 0 and 'sale_bk_active' not in leads[0], (err, leads))
        check('restore_stays_from_cache, %s cache: it says the board will hold Miami' % label,
              'unreadable' in out, out)
finally:
    FL.HERE = saved_here

fsrc = (HERE / 'foreclosure_leads.py').read_text(encoding='utf-8')
i_hold = fsrc.find('\n    _stay_degraded = hold_miami_on_bad_stay_cache(slim)')
i_fed = fsrc.find('\n    _bk_held, _bk_degraded = stamp_federal_bk(slim)')
check('make_tracker: the Miami stay hold runs before the federal stamp and feeds bkdeg',
      0 < i_hold < i_fed and "_bk_degraded = '; '.join(x for x in (_stay_degraded, _bk_degraded) if x)" in fsrc)

# ================================================================ 4. cadence, letters, CRM, 3-DAY
csrc = (HERE / 'cadence.py').read_text(encoding='utf-8')
i_cbr = csrc.find('_BKL.contact_blocked_reason(c, here=HERE)')
i_dg = csrc.find("_g = _dg.gate(_row)")
check('cadence: the send-time sweep asks the send bridge verdict before diligence', 0 < i_cbr < i_dg)

letter = {'case': MIA, 'owners': 'ROE, MARY', 'oname': 'Mary Roe', 'addr': '10 EAST ST, Miami, FL 33101',
          'mail': '10 EAST ST, Miami, FL 33101', 'days': 30, 'tier': 'A'}
for label, fn in (('held', lambda *_a, **_k: (True, 'stay_data_unavailable')), ('raising', raises)):
    with patched(BL, 'contact_blocked_reason', fn):
        q, sk = OM.build_selection([dict(letter)], None, 0, set(), set(), False, 10, trust_selection=True)
    check('letters, verdict %s: the letter is not queued' % label,
          q == [] and sk.get('bankruptcy-stay-check') == 1, (q, dict(sk)))
with blocked('bk_lookup'):
    q, sk = OM.build_selection([dict(letter)], None, 0, set(), set(), False, 10, trust_selection=True)
check('letters, bk_lookup will not import: the letter is not queued', q == [] and sk.get('bankruptcy-stay-check') == 1,
      dict(sk))
with patched(BL, 'contact_blocked_reason', lambda *_a, **_k: (False, '')):
    q, sk = OM.build_selection([dict(letter)], None, 0, set(), set(), False, 10, trust_selection=True)
check('letters, verdict clear: the stay check lets it through (later gates still apply)',
      not sk.get('bankruptcy-stay-check'), dict(sk))
check('letters: send_hold is no longer the letter backstop (it releases Miami on a docket stay)',
      '_BKL.send_hold(' not in (HERE / 'outreach_mail.py').read_text(encoding='utf-8'))


def verdict(case, here=None):
    if case == BRO:
        raise ValueError('bad row')
    return (case == MIA), ('docket stay' if case == MIA else '')


rows = [{'case': MIA, 'saleLift': '2099-01-01'}, {'case': MIA2}, {'case': BRO}]
with patched(BL, 'contact_blocked_reason', verdict):
    quiet(CRM._hold_unbaked, rows)
check('CRM fallback: a row the stay verdict refuses is held with its reason and no lift date',
      rows[0].get('saleBkAct') is True and rows[0].get('bkWhy') == 'docket stay' and 'saleLift' not in rows[0], rows[0])
check('CRM fallback: a cleared row is untouched', 'saleBkAct' not in rows[1], rows[1])
check('CRM fallback: a row whose check raises is held', rows[2].get('saleBkAct') is True, rows[2])
rows = [{'case': MIA2}, {'case': BRO}]
with blocked('bk_lookup'):
    quiet(CRM._hold_unbaked, rows)
check('CRM fallback, bk_lookup will not import: every row is held', all(r.get('saleBkAct') for r in rows), rows)

crm_dir = fresh_dir('crm')
put(crm_dir, 'broward_leads.json', [{'case': BRO}])
put(crm_dir, 'lp_leads.json', [{'case': MIA2, 'county': 'MIAMI-DADE'}])
with patched(CRM, 'HERE', str(crm_dir)), patched(CRM, 'TWIN', str(crm_dir / 'no-twin.html')), \
        patched(BL, 'contact_blocked_reason', lambda c, here=None: (True, 'not cleared')):
    rows, out = quiet(CRM._leads)
check('CRM: with no twin, the raw-file fallback rows come back held',
      len(rows) == 2 and all(r.get('saleBkAct') for r in rows), rows)

today = dt.date(2099, 1, 5)
while today.weekday() != 0:
    today += dt.timedelta(days=1)
sale = today + dt.timedelta(days=2)
jrow = {'case': BRO, 'county': 'BROWARD', 'st': 'FC', 'auction': sale.strftime('%m/%d/%Y'),
        'value': 300000, 'judg': 100000, 'addr': '1 TEST ST', 'owners': 'TEST OWNER', 'plaintiff': 'TEST BANK NA'}
with patched(TD, '_load', lambda fn, default: default), patched(CRM, '_closed_cases', lambda: set()):
    import call_mode as CM
    with patched(CM, 'federal_hold_fn', lambda: (lambda case: False)):
        clear = TD.lane(rows=[dict(jrow)], today=today, min_year=2099)
    with patched(CM, 'federal_hold_fn', lambda: (lambda case: True)):
        held = TD.lane(rows=[dict(jrow)], today=today, min_year=2099)
    with blocked('call_mode'):
        broken = TD.lane(rows=[dict(jrow)], today=today, min_year=2099)
check('3-DAY lane: a qualifying lead with no federal hold is listed (the fixture is valid)',
      [r['case'] for r in clear] == [BRO], clear)
check('3-DAY lane: a federal hold keeps it off Jesse\'s list even with no row flag', held == [], held)
check('3-DAY lane: call_mode will not import -> nothing listed', broken == [], broken)

hsrc = (HERE / 'healthcheck.py').read_text(encoding='utf-8')
check('healthcheck: a bad stay cache is a WARN (not skipped, not a crash on a JSON list)',
      "'RULE: §362 stay data readable'" in hsrc and '_shc = _shraw if isinstance(_shraw, dict) else {}' in hsrc)

# ============================================================ 5. round 2 (review findings)
def bad_stay_cache(*_a, **_k):
    return 'stay data unavailable: test'


def healthy_stay_cache(*_a, **_k):
    return ''


# -- sale_history: a missing / empty cache is a lost file, not a first run
d = sh_world('sh_lost')
put(d, 'leads_final.json', [auction(MIA)])
good = {MIA: ent(a=True, bd='2099-01-02', t=OLD)}
put(d, 'sale_history_cache.json.bak', good)
rc, out = quiet(SH.main, ['--limit', '5'])
check('sale_history, cache MISSING but a good .bak exists: refuses, reads and writes nothing',
      rc == 2 and CALLS == [] and raw(d, 'sale_history_cache.json') is None
      and json.loads(raw(d, 'sale_history_cache.json.bak')) == good, (rc, CALLS, out[-200:]))
put(d, 'sale_history_cache.json', {})
rc, out = quiet(SH.main, ['--limit', '5'])
check('sale_history, cache EMPTY ({}): refuses and leaves the file alone',
      rc == 2 and CALLS == [] and json.loads(raw(d, 'sale_history_cache.json')) == {}, (rc, CALLS, out[-200:]))
check('sale_history, empty cache: the .bak is not replaced by an empty copy',
      json.loads(raw(d, 'sale_history_cache.json.bak')) == good)
rc, out = quiet(SH.main, ['--limit', '5', '--init'])
check('sale_history --init: starts a new cache on purpose', rc in (None, 0) and CALLS == [MIA], (rc, CALLS, out[-200:]))
d = sh_world('sh_first')
put(d, 'leads_final.json', [auction(MIA)])
rc, out = quiet(SH.main, ['--limit', '5'])
check('sale_history, no cache and no .bak: a true first run still works', rc in (None, 0) and CALLS == [MIA], (rc, CALLS))

# -- Windows: a briefly locked target must not kill the run
d = fresh_dir('dump_retry')
real_replace, tries = os.replace, []


def flaky_replace(a, b):
    tries.append(1)
    if len(tries) < 3:
        raise PermissionError('locked')
    return real_replace(a, b)


with patched(SH.os, 'replace', flaky_replace), patched(SH.time, 'sleep', lambda s: None):
    SH._dump({'x': 1}, str(d / 'f.json'))
check('sale_history._dump retries a locked target and then writes', len(tries) == 3
      and json.loads(raw(d, 'f.json')) == {'x': 1} and raw(d, 'f.json.tmp') is None, tries)

# -- bk_lookup helpers
with patched(BL, 'stay_cache_error', bad_stay_cache):
    check('miami_stay_data_hold: a Miami case is held on a bad stay cache', BL.miami_stay_data_hold(MIA)[0] is True)
    check('miami_stay_data_hold: Broward / blank are not this check\'s business',
          BL.miami_stay_data_hold(BRO) == (False, '') and BL.miami_stay_data_hold('') == (False, ''))
with patched(BL, 'stay_cache_error', healthy_stay_cache):
    check('miami_stay_data_hold: healthy cache holds nothing', BL.miami_stay_data_hold(MIA) == (False, ''))
with patched(BL, 'stay_cache_error', raises):
    check('miami_stay_data_hold: an error holds', BL.miami_stay_data_hold(MIA)[0] is True)
with patched(BL, 'federal_hold', lambda c, **k: (True, 'open federal case')), patched(BL, 'stay_cache_error', healthy_stay_cache):
    check('raw_row_hold: a federal hold wins', BL.raw_row_hold(MIA) == (True, 'open federal case'))
with patched(BL, 'federal_hold', raises):
    check('raw_row_hold: federal_hold raising holds', BL.raw_row_hold(MIA)[0] is True)

# -- door routes, knock planner, dial sheet
import _carlos_route as CR      # noqa: E402
import morning_planner as MP    # noqa: E402
import call_list as CL          # noqa: E402
import outreach_email as OE     # noqa: E402

row = {'case': MIA, 'owners': 'TEST OWNER', 'addr': '1 TEST ST'}
with patched(BL, 'stay_cache_error', bad_stay_cache), patched(BL, 'federal_hold', lambda c, **k: (False, '')):
    check('door routes: a Miami lead with no row flag is dropped while the stay cache is bad',
          CR.stay_held(MIA) is True and CR._live_lead(dict(row), {}) is False)
    check('knock planner: held while the stay cache is bad', MP._knock_eligible(dict(row)) is False)
    check('dial sheet: held while the stay cache is bad', CL._stay_held(dict(row), MIA) is True)
with patched(BL, 'stay_cache_error', healthy_stay_cache), patched(BL, 'federal_hold', lambda c, **k: (False, '')):
    check('door routes + dial sheet: a clean Miami lead on a healthy cache is not held (no mass hold)',
          CR.stay_held(MIA) is False and CL._stay_held(dict(row), MIA) is False)
    check('dial sheet: a row flag still holds, a lifted one does not',
          CL._stay_held(dict(row, sale_bk_active=True), MIA) is True
          and CL._stay_held(dict(row, sale_bk_active=True, sale_stay_lifted='2099-01-01'), MIA) is False)
with patched(BL, 'federal_hold', lambda c, **k: (True, 'open federal case')):
    check('door routes + dial sheet: a federal hold drops the lead',
          CR.stay_held(MIA) is True and CL._stay_held(dict(row), MIA) is True)
with blocked('bk_lookup'):
    check('door routes + dial sheet: bk_lookup will not import -> held',
          CR.stay_held(MIA) is True and CL._stay_held(dict(row), MIA) is True)
check('daily routes: both LP pools and the specials go through stay_held',
      (HERE / 'bsg_daily_routes.py').read_text(encoding='utf-8').count('CR.stay_held(') >= 2)
check('dial sheet: both loops are gated', (HERE / 'call_list.py').read_text(encoding='utf-8').count('            if _stay_held(r, case):') == 2)

# -- email load: Miami + bad stay cache
leads = [{'case': MIA}, {'case': BRO}]
with patched(BL, 'stay_cache_error', bad_stay_cache), patched(BL, 'send_hold', lambda c, here=None: (False, '')):
    OE._stamp_federal_holds(leads)
check('email load: a Miami lead is held while the stay cache is bad (send_hold alone released it)',
      leads[0].get('saleBkAct') is True and not leads[1].get('saleBkAct'), leads)

# -- the board: cached ACTIVE stays reach LP rows
sc_dir = fresh_dir('stampcache')
put(sc_dir, 'sale_history_cache.json', {MIA: ent(a=True, bd='2099-01-02'), MIA2: ent(a=True, bd='2099-01-03', sl='2099-02-01'),
                                        '2099-000803-CA-01': ent()})
saved_here = FL.HERE
try:
    FL.HERE = str(sc_dir)
    rows = [{'case': MIA, 'saleLift': '2099-01-09'}, {'case': MIA2}, {'case': '2099-000803-CA-01'}, {'case': BRO},
            {'case': MIA.replace('801', '899')}, {'case': ''}]
    n, out = quiet(FL.stamp_cached_stays, rows)
    check('board: an ACTIVE cached stay holds its row, with the filing date, and drops saleLift',
          rows[0].get('saleBkAct') is True and rows[0].get('saleBkD') == '2099-01-02' and 'saleLift' not in rows[0], rows[0])
    check('board: a LIFTED entry, a clear entry, no entry, Broward and blank rows are untouched',
          not any(r.get('saleBkAct') for r in rows[1:]) and n == 1, rows)
    put(sc_dir, 'sale_history_cache.json', '[1]')
    rows = [{'case': MIA}]
    n, out = quiet(FL.stamp_cached_stays, rows)
    check('board: a bad cache stamps nothing here (hold_miami_on_bad_stay_cache owns that)',
          n == 0 and not rows[0].get('saleBkAct'))
finally:
    FL.HERE = saved_here
fsrc = (HERE / 'foreclosure_leads.py').read_text(encoding='utf-8')
check('make_tracker: cached stays are stamped before the bad-cache hold',
      0 < fsrc.find('\n    stamp_cached_stays(slim)') < fsrc.find('\n    _stay_degraded = hold_miami_on_bad_stay_cache(slim)'))

# -- letters
dd = fresh_dir('mail_load')
put(dd, 'balloon_leads.json', [{'case': 'BAL-1'}])
put(dd, 'lp_leads.json', [{'case': MIA}])
with patched(OM, 'HERE', str(dd)):
    loaded = OM._load_leads()
check('letters: balloon_leads.json (the investor lane) is not loaded', [r['case'] for r in loaded] == [MIA], loaded)
msrc = (HERE / 'bsg_mail_campaign.py').read_text(encoding='utf-8')
check('LP letter campaign: the stay verdict is a drop bucket',
      "drops['bankruptcy stay check'] += 1" in msrc and 'contact_blocked_reason' in msrc)

# ============================================================ 6. round 3 (independent review)
# -- a HEALTHY cache that says ACTIVE must hold the raw-row paths too
hd = fresh_dir('healthy_active')
put(hd, 'sale_history_cache.json', {MIA: ent(a=True, bd='2099-01-02'),
                                    MIA2: ent(a=True, bd='2099-01-03', sl='2099-02-01'),
                                    '2099-000803-CA-01': ent()})
with patched(BL, 'federal_hold', lambda c, **k: (False, '')):
    check('raw_row_hold, healthy cache: an ACTIVE unlifted stay is held',
          BL.raw_row_hold(MIA, here=str(hd))[0] is True and 'ACTIVE' in BL.raw_row_hold(MIA, here=str(hd))[1])
    check('raw_row_hold, healthy cache: a lifted stay, a clear entry, an unread case and Broward are not held (no mass hold)',
          all(BL.raw_row_hold(c, here=str(hd)) == (False, '') for c in (MIA2, '2099-000803-CA-01', '2099-000899-CA-01', BRO)))
    ldir = fresh_dir('lp_pool')
    pool_src = (HERE / 'bsg_daily_routes.py').read_text(encoding='utf-8')
    check('daily routes: the LP pool check is the same stay_held that now sees cached ACTIVE stays',
          'CR.stay_held(base[' in pool_src)
saved_here_cr = CR.HERE
try:
    CR.HERE = str(hd)
    with patched(BL, 'federal_hold', lambda c, **k: (False, '')):
        check('door routes: stay_held reads the cache in ITS folder - active held, clean not',
              CR.stay_held(MIA) is True and CR.stay_held('2099-000803-CA-01') is False)
finally:
    CR.HERE = saved_here_cr

# -- sale_history never leaves an empty cache that blocks the next run
d = sh_world('sh_allfail')
put(d, 'leads_final.json', [auction(MIA)])
DOCKETS[MIA] = None
rc, out = quiet(SH.main, ['--limit', '5'])
check('sale_history first run where every read fails: no empty cache is written',
      raw(d, 'sale_history_cache.json') is None and 'not created' in out, (rc, out[-200:]))
rc, out = quiet(SH.main, ['--limit', '5'])
check('sale_history: and the next run is still a first run, not "empty cache" exit 2', rc in (None, 0) and CALLS.count(MIA) == 2, (rc, CALLS))
d = sh_world('sh_init_fail')
put(d, 'leads_final.json', [auction(MIA)])
put(d, 'sale_history_cache.json', {})
DOCKETS[MIA] = None
rc, out = quiet(SH.main, ['--limit', '5', '--init'])
check('sale_history --init where every read fails: the file stays {} and nothing else changes',
      json.loads(raw(d, 'sale_history_cache.json')) == {}, (rc, out[-200:]))

# -- the .bak: a small rebuilt cache does not replace a fuller one
d = sh_world('sh_bak')
full = {('2099-0009%02d-CA-01' % i): ent(t=NOW) for i in range(10)}
put(d, 'sale_history_cache.json.bak', full)
put(d, 'sale_history_cache.json', {MIA: ent(t=NOW)})
put(d, 'leads_final.json', [auction(MIA)])
rc, out = quiet(SH.main, ['--limit', '0'])
check('.bak: a cache under half its size does not overwrite the fuller .bak',
      len(json.loads(raw(d, 'sale_history_cache.json.bak'))) == 10 and 'kept the fuller' in out, out[-300:])
put(d, 'sale_history_cache.json', dict(full, **{MIA: ent(t=NOW)}))
rc, out = quiet(SH.main, ['--limit', '0'])
check('.bak: a comparable cache does refresh the .bak',
      len(json.loads(raw(d, 'sale_history_cache.json.bak'))) == 11, len(json.loads(raw(d, 'sale_history_cache.json.bak'))))

# -- a target that stays locked: write in place instead of losing the night
d = fresh_dir('dump_locked')


def always_locked(a, b):
    raise PermissionError('locked')


with patched(SH.os, 'replace', always_locked), patched(SH.time, 'sleep', lambda s: None):
    SH._dump({'x': 2}, str(d / 'g.json'))
check('sale_history._dump: still locked after the retries -> written in place, no .tmp left',
      json.loads(raw(d, 'g.json')) == {'x': 2} and raw(d, 'g.json.tmp') is None)

print()
print('==== %s ====' % ('FAILED: %d check(s)' % len(FAILS) if FAILS
                          else 'all stay-gate fail-closed checks passed'))
sys.exit(1 if FAILS else 0)
