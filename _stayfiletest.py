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
    json.dump(scrape, open(os.path.join(tmp, 'leads_final.json'), 'w'))
    on_disk = sum(1 for r in json.load(open(os.path.join(tmp, 'leads_final.json'))) if r.get('sale_bk_active'))
    check('written to disk, the lead file carries what the cache carries (healthcheck: leads == cache)',
          on_disk == cache_act)
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
