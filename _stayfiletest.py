"""_stayfiletest -- §362 stay flags must be in leads_final.json, not only on the built board.

Run:  python _stayfiletest.py     (exit 0 = safe; no network, no browser, synthetic data only)

2026-09-24: a rebuild on main was blocked by healthcheck's compliance rule "§362 stay flags reach
the build" while the board it had just built carried 80 stays. The rule counts stays in the LEAD
FILES (cache -> leads -> board). main() wrote leads_final.json straight from the scrape, which
carries no stay flags, and only make_tracker restored them from sale_history_cache.json -- in
memory, after the file was already on disk. Unless sale_history.py re-stamped the file later in the
run (run-leads.bat never runs it), the file read 0 stays and the gate failed a correct board.
"""
import json
import os
import re
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if 'playwright' not in sys.modules:                  # import shim: browser dependency, not under test
    _pw = types.ModuleType('playwright'); _sa = types.ModuleType('playwright.sync_api')
    _sa.sync_playwright = lambda *a, **k: None
    sys.modules['playwright'], sys.modules['playwright.sync_api'] = _pw, _sa

import foreclosure_leads as F

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


CACHE = {
    '2099-000001-CA-01': {'a': True, 'bd': '09/01/2026', 'b': 1, 's': 2, 'n': 3},
    '2099-000002-CA-01': {'a': True, 'bd': '08/15/2026'},
    '2099-000003-CA-01': {'a': False, 'b': 1, 'sl': True},        # stay lifted: not active
}
scrape = [{'Case #': c} for c in ('2099-000001-CA-01', '2099-000002-CA-01', '2099-000003-CA-01',
                                  '2099-000004-CA-01')]

tmp = tempfile.mkdtemp()
json.dump(CACHE, open(os.path.join(tmp, 'sale_history_cache.json'), 'w'))
_here = F.HERE
F.HERE = tmp
try:
    cache_act = sum(1 for e in CACHE.values() if e.get('a'))
    before = sum(1 for r in scrape if r.get('sale_bk_active'))
    check('reproduced: a fresh scrape carries no stay flags (healthcheck read leads 0 -> STRIPPED)',
          before == 0 and cache_act == 2)
    n = F.restore_stays_from_cache(scrape)
    after = sum(1 for r in scrape if r.get('sale_bk_active'))
    check('restore puts every active cached stay back on the lead rows', n == 2 and after == cache_act,
          '%d restored, %d on rows' % (n, after))
    check('a lifted stay is not re-activated',
          not scrape[2].get('sale_bk_active') and scrape[2].get('sale_stay_lifted') is True)
    check('the stay date rides along', scrape[0].get('sale_bk_date') == '09/01/2026')
    check('a case the cache does not know is untouched', scrape[3] == {'Case #': '2099-000004-CA-01'})
    check('running it twice changes nothing', F.restore_stays_from_cache(scrape) == 0
          and sum(1 for r in scrape if r.get('sale_bk_active')) == 2)

    # THE PRODUCTION WRITE: run main() itself with the network stages stubbed, then read the file it
    # wrote. A check that dumps its own restored rows proves nothing about what main() persists.
    _stubs = {k: getattr(F, k) for k in ('scrape', 'enrich', 'enrich_clerk', 'qualify', 'make_tracker',
                                         '_load_codes')}
    _env = {k: os.environ.get(k) for k in ('DEALFLOW_FORCE', 'DEALFLOW_NO_DESKTOP')}
    seen_by_board = []
    def _qualify(rows):
        for r in rows:
            r.update(score=1, tier='C', sale_type='FC')
        return rows
    try:
        os.environ['DEALFLOW_FORCE'] = '1'; os.environ['DEALFLOW_NO_DESKTOP'] = '1'
        F.scrape = lambda: [{'Case #': c} for c in CACHE] + [{'Case #': '2099-000004-CA-01'}]
        F.enrich = F.enrich_clerk = lambda rows: rows
        F.qualify = _qualify
        F.make_tracker = lambda rows: seen_by_board.extend(r for r in rows if r.get('sale_bk_active'))
        F._load_codes = lambda: []
        F.main()
    finally:
        for k, v in _stubs.items():
            setattr(F, k, v)
        for k, v in _env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    on_disk = [r for r in json.load(open(os.path.join(tmp, 'leads_final.json'))) if r.get('sale_bk_active')]
    check('main() writes the active cached stays into leads_final.json (healthcheck: leads == board)',
          len(on_disk) == cache_act == len(seen_by_board), '%d on disk, %d to the board' % (len(on_disk), len(seen_by_board)))

    # A LIFT MUST STILL WIN. The nightly runs sale_history.py [3e/5] AFTER main() wrote those flags.
    # When a live docket read now shows the stay closed, the row has to lose the flag, and the
    # [4/5] rebuild's make_tracker restore must not put it back from the (now updated) cache.
    import sale_history as SH
    _sh = {k: getattr(SH, k) for k in ('HERE', 'CACHE', '_fetch', '_count', '_bk_count', '_bk_stay', 'time')}
    _argv = sys.argv
    live = {'2099-000001-CA-01': (False, '09/01/2026', '09/20/2026'),   # stay lifted since cached
            '2099-000003-CA-01': (False, '', '')}
    try:
        SH.HERE = tmp; SH.CACHE = os.path.join(tmp, 'sale_history_cache.json')
        SH._fetch = lambda session, case: None if case == '2099-000002-CA-01' else case   # 000002: fetch fails
        SH._count = lambda dks: (0, 0, 0, '')
        SH._bk_count = lambda dks: 1
        SH._bk_stay = lambda case: live.get(case, (False, '', ''))
        SH.time = types.SimpleNamespace(time=__import__('time').time, sleep=lambda s: None)
        sys.argv = ['sale_history.py']
        SH.main()
    finally:
        for k, v in _sh.items():
            setattr(SH, k, v)
        sys.argv = _argv
    after_sh = {r['Case #']: r for r in json.load(open(os.path.join(tmp, 'leads_final.json')))}
    check('a live read showing the stay lifted clears the flag the scrape step wrote',
          not after_sh['2099-000001-CA-01'].get('sale_bk_active')
          and after_sh['2099-000001-CA-01'].get('sale_stay_lifted') == '09/20/2026')
    check('a failed live read never clears a stay', after_sh['2099-000002-CA-01'].get('sale_bk_active') is True)
    rebuilt = list(after_sh.values())
    F.restore_stays_from_cache(rebuilt)
    check('the rebuild does not re-activate the lifted stay from the cache',
          [r['Case #'] for r in rebuilt if r.get('sale_bk_active')] == ['2099-000002-CA-01'])
    os.remove(os.path.join(tmp, 'sale_history_cache.json'))
    check('no cache file: nothing restored, nothing raised', F.restore_stays_from_cache([{'Case #': 'x'}]) == 0)
finally:
    F.HERE = _here

src = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
main_src = src[src.index('\ndef main('):]
_dump = main_src.index("json.dump(leads, open(os.path.join(HERE,'leads_final.json')")
check('main() restores the stays BEFORE it writes leads_final.json',
      0 <= main_src.rfind('restore_stays_from_cache(leads)', 0, _dump))
mt = src[src.index('\ndef make_tracker('):src.index('\n    slim = []\n', src.index('\ndef make_tracker('))]
check('make_tracker still restores them for the board (runs that skip main)',
      'restore_stays_from_cache(leads)' in mt)
check('one copy of the field mapping, not two', src.count("r['sale_bk_active'] = True") == 1,
      src.count("r['sale_bk_active'] = True"))

print('\n%s: %d failure(s)' % ('FAIL' if FAILS else 'OK', len(FAILS)))
sys.exit(1 if FAILS else 0)
