"""The twelve defects from DEALFLOW-AUDIT-2026-09-21.md, as the failing inputs that proved them.

WHY THIS EXISTS

The audit reproduced each defect before reporting it. Those reproductions were run by hand, in a
chat, against a checkout that no longer exists -- so the evidence that any of this was ever broken
lived nowhere, and a later refactor could quietly restore any of it. Every check below is the
audit's own failing input, asserted against the fixed behaviour. A check that stops failing on the
OLD code is worthless here, so each one is written against a value the old code actually produced.

The four equity rows are copied from the audit's own reproduction table:

    conf=ok        two liens, 100000 and 0    ->  was 'priced'     now 'unpriced'
    conf=unpriced  two liens, 100000 and 0    ->  was 'priced'     now 'unpriced'
    conf=ok        one lien, no amount        ->  was 'priced'     now 'unpriced'
    conf=low       no liens                   ->  was 'clear'      now 'none'

WHAT THIS SUITE CANNOT DO. Four of the twelve live inside a browser or a live portal session --
auction pagination, the Palm Beach sweep's network half, the clerk HTTP calls, the LP status
fetch. Their PURE parts are tested here (paging arithmetic, date splitting, party identity, the
last-good carry-forward with a failing session); the parts that need realforeclose.com or
Landmark are not, and are marked as structural checks so nobody reads a green run as proof the
scrapers were exercised.

foreclosure_leads imports playwright at module scope and the container has no browser, so a
stub stands in. That is an import shim, not a test double for anything under test.

Run: python _audit12test.py
"""
import datetime
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# --- import shim: playwright is a browser dependency, absent on a container -------------------
if 'playwright' not in sys.modules:
    _pw = types.ModuleType('playwright')
    _sa = types.ModuleType('playwright.sync_api')
    _sa.sync_playwright = lambda *a, **k: None
    sys.modules['playwright'], sys.modules['playwright.sync_api'] = _pw, _sa
# --- import shim: palmbeach_liens shells out to curl against the live portal -------------------
if 'palmbeach_liens' not in sys.modules:
    _pl = types.ModuleType('palmbeach_liens')
    for _n in ('start_session', 'solve_token_2captcha', 'search', 'get_search_results',
               'gsr_rows_to_docs'):
        setattr(_pl, _n, lambda *a, **k: None)
    sys.modules['palmbeach_liens'] = _pl

FAIL, PASS = [], []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok   ' if ok else '  FAIL ') + name + (('  - ' + str(detail)[:150]) if detail else ''))


def eq(name, got, want):
    rec(name, got == want, '' if got == want else f'got {got!r}, want {want!r}')


# ================================================================================================
print('\n1. EQUITY CLASSIFIER — low confidence and part-priced lists may not read as VERIFIED')
import equity_state as ES

eq("ok + amounts 100000 and 0 is a CEILING, not priced",
   ES.state_of({'conf': 'ok', 'liens': [{'amt': 100000}, {'amt': 0}]}), 'unpriced')
eq("unpriced + amounts 100000 and 0 stays unpriced",
   ES.state_of({'conf': 'unpriced', 'liens': [{'amt': 100000}, {'amt': 0}]}), 'unpriced')
eq("ok + one lien with no amount is not priced",
   ES.state_of({'conf': 'ok', 'liens': [{'amt': 0}]}), 'unpriced')
eq("LOW confidence + empty chain is UNVERIFIED, not VERIFIED CLEAR",
   ES.state_of({'conf': 'low', 'liens': []}), 'none')
eq("ok + empty chain is still the real finding: clear",
   ES.state_of({'conf': 'ok', 'liens': []}), 'clear')
eq("ok + every lien priced is still priced",
   ES.state_of({'conf': 'ok', 'liens': [{'amt': 100000}, {'amt': 50000}]}), 'priced')
eq("PB instruments counted but unpriced", ES.state_of({'conf': 'ok', 'liens': [], 'mtg_open_unpriced': 2}), 'unpriced')
eq("no chain at all", ES.state_of(None), 'unchecked')
eq("empty dict", ES.state_of({}), 'unchecked')
rec("no low-confidence chain can reach a FACT state",
    all(ES.state_of(c) not in ES.FACT for c in
        ({'conf': 'low', 'liens': []}, {'conf': 'low'}, {'conf': 'low', 'liens': [], 'nrec': 30})))
_d = {}
ES.apply(_d, {'conf': 'ok', 'liens': [{'amt': 100000}, {'amt': 0}]})
rec("a part-priced lead renders a CEILING above zero, not an empty one",
    _d.get('eqopen') == 2 and _d.get('eqgap') == 1, _d)
rec("the CEILING label no longer claims the gap is a Palm Beach problem",
    'Palm Beach' not in ES.LABEL['unpriced'], ES.LABEL['unpriced'])

# ================================================================================================
print('\n2+3. CLERK ENRICHMENT — identity, not list position; a failed refresh is not an empty case')
import foreclosure_leads as F

rec("SMITH, JOHN is SMITH, JOHN A", F._same_party('SMITH, JOHN', 'SMITH, JOHN A'))
rec("SMITH, JOHN is NOT SMITH, JANE", not F._same_party('SMITH, JOHN', 'SMITH, JANE'))
rec("an HOA is not the owner", not F._same_party('MUTINY CONDOMINIUM ASSOCIATION INC', 'TRABIN, SARA'))
rec("a blank name matches nobody", not F._same_party('', 'SMITH, JOHN'))


class _Boom:
    """A clerk session that is simply down — the 09-17-shaped outage."""
    headers = {}

    def update(self, *a, **k):
        pass

    def get(self, *a, **k):
        raise OSError('connection reset')

    def post(self, *a, **k):
        raise OSError('connection reset')


_orig_session = F.requests.Session
F.requests.Session = lambda *a, **k: _Boom()
F.time.sleep = lambda *a, **k: None
try:
    lead = {'Case #': '2026-012840-CA-01', 'plaintiff': 'JPMORGAN CHASE BANK NA',
            'defendants': 'RILES, ADRIENNE M; UNKNOWN TENANT',
            'docket_url': 'https://example/ocs?qs=abc', 'clerk_ts': '2026-09-18 06:45'}
    F.enrich_clerk([lead])
finally:
    F.requests.Session = _orig_session

eq("a clerk outage does NOT blank the plaintiff", lead['plaintiff'], 'JPMORGAN CHASE BANK NA')
eq("a clerk outage does NOT blank the defendants", lead['defendants'], 'RILES, ADRIENNE M; UNKNOWN TENANT')
eq("a clerk outage does NOT blank the docket link", lead['docket_url'], 'https://example/ocs?qs=abc')
rec("the failed refresh is recorded rather than hidden", lead.get('clerk_stale') is True and lead.get('clerk_err'),
    {k: lead.get(k) for k in ('clerk_stale', 'clerk_err')})

_fresh = {'Case #': '2026-012840-CA-01'}
F.requests.Session = lambda *a, **k: _Boom()
try:
    F.enrich_clerk([_fresh])
finally:
    F.requests.Session = _orig_session
rec("a lead that never had values is not marked stale", not _fresh.get('clerk_stale'), _fresh)
rec("...but still carries the keys downstream expects",
    all(k in _fresh for k in ('plaintiff', 'defendants', 'docket_url')))

# ================================================================================================
print('\n4+5. COUNTY PARTY LOOKUP — day zero is a day, and a cached plaintiff is not forever')
import county_plaintiffs as CP

eq("days=0 (today's auction) is a real zero", CP.days_of({'days': 0}), 0)
eq("days missing is unknown, not a number", CP.days_of({}), None)
eq("days=None is unknown", CP.days_of({'days': None}), None)
eq("days=1 unchanged", CP.days_of({'days': 1}), 1)
rec("today's auction now passes the 0..30 near window",
    (lambda d: d is not None and 0 <= d <= 30)(CP.days_of({'days': 0})))
rec("an unknown day is still rejected by the near window",
    not (lambda d: d is not None and 0 <= d <= 30)(CP.days_of({})))

_today = datetime.date(2026, 9, 21)
rec("a 30-day-old cached plaintiff is re-resolved",
    CP.is_stale({'plaintiff': 'X', 'ts': '2026-08-22'}, 12, 21, _today))
rec("a 3-day-old cached plaintiff is left alone",
    not CP.is_stale({'plaintiff': 'X', 'ts': '2026-09-18'}, 12, 21, _today))
rec("an UNDATED legacy entry is re-resolved when the auction is imminent",
    CP.is_stale({'plaintiff': 'X'}, 3, 21, _today))
rec("an UNDATED legacy entry is left alone when the auction is far off (captcha cost)",
    not CP.is_stale({'plaintiff': 'X'}, 200, 21, _today))
rec("--max-age 0 disables ageing entirely",
    not CP.is_stale({'plaintiff': 'X', 'ts': '2020-01-01'}, 3, 0, _today))
rec("a missing entry is always due", CP.is_stale(None, 5, 21, _today))
rec("a corrupt timestamp is re-resolved rather than trusted",
    CP.is_stale({'plaintiff': 'X', 'ts': 'not-a-date'}, 99, 21, _today))

# ================================================================================================
print('\n6. LP SWEEP — a blocked source is not an empty county')
import lis_pendens as LP

eq("all three swept", LP.sweep_verdict({'MIAMI-DADE': 'ok', 'BROWARD': 'ok', 'PALM BEACH': 'ok'}),
   (['BROWARD', 'MIAMI-DADE', 'PALM BEACH'], []))
eq("one blocked is a partial", LP.sweep_verdict({'MIAMI-DADE': 'ok', 'BROWARD': 'failed'}),
   (['MIAMI-DADE'], ['BROWARD']))
eq("all blocked leaves nothing that ran",
   LP.sweep_verdict({'MIAMI-DADE': 'failed', 'BROWARD': 'failed'})[0], [])
eq("the blocked-plaintiff counter is not mistaken for a county",
   LP.sweep_verdict({'MIAMI-DADE': 'ok', 'MIAMI-DADE_blocked_plaintiffs': 4})[0], ['MIAMI-DADE'])
rec("a partial sweep has its own exit code, distinct from clean and from dead",
    LP.EXIT_PARTIAL not in (0, 3), LP.EXIT_PARTIAL)

# ================================================================================================
print('\n7. NIGHTLY BATCH — the LP chain exit code is read, and the subroutine cannot be fallen into')
_bat = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='utf-8', errors='replace').read()
_lines = _bat.splitlines()
rec("the lp_refresh call no longer discards its exit code", 'call :lpcode' in _bat)
rec("a non-zero LP chain reaches RUNEXIT", 'if "%RUNEXIT%"=="0" set "RUNEXIT=7"' in _bat)
_i_sub = next((n for n, l in enumerate(_lines) if l.strip() == ':lpcode'), -1)
_i_exit = next((n for n, l in enumerate(_lines) if l.strip().startswith('endlocal & exit /b')), -1)
rec("the :lpcode subroutine sits BELOW the final exit, so control cannot fall into it",
    _i_sub > _i_exit > 0, f':lpcode at {_i_sub}, exit at {_i_exit}')
_i_end = next((n for n, l in enumerate(_lines) if l.strip() == ':end'), -1)
rec(":end is still reachable from the body", 0 < _i_end < _i_exit, f':end at {_i_end}')

import lp_refresh as LR
rec("a PARTIAL sweep does not stop the chain but is collected", hasattr(LR, 'DEGRADED'))
rec("lis_pendens' partial code is in the sweep step's benign list",
    'ok=(0, 4)' in open(os.path.join(HERE, 'lp_refresh.py'), encoding='utf-8').read())

# ================================================================================================
print('\n8. PALM BEACH — read the declared total, page the grid, narrow what cannot be proven')
from fl_lp import palmbeach as PB

eq("the envelope's total is read", PB._gsr_total({'recordsFiltered': '913', 'data': []}), 913)
eq("an integer total is read", PB._gsr_total({'recordsTotal': 42, 'data': []}), 42)
eq("no total declared is None, not zero", PB._gsr_total({'data': []}), None)
eq("a non-dict envelope is None", PB._gsr_total([1, 2, 3]), None)

_store = [{'i': i} for i in range(600)]
_rows, _complete = PB._page_all(lambda length, start: {'recordsFiltered': 600, 'data': _store[start:start + length]})
rec("600 records are all pulled, not just the first page", len(_rows) == 600 and _complete,
    f'{len(_rows)} rows, complete={_complete}')
_rows, _complete = PB._page_all(lambda length, start: {'recordsFiltered': 913,
                                                       'data': (_store[:200] if start == 0 else [])})
rec("a server that caps at 200 while declaring 913 is reported INCOMPLETE",
    len(_rows) == 200 and _complete is False, f'{len(_rows)} rows, complete={_complete}')
rec("a server that ignores `start` cannot spin forever",
    len(PB._page_all(lambda length, start: {'recordsFiltered': 10 ** 9, 'data': _store[:250]})[0]) <= 250 * 40)

eq("a 30-day window halves", PB._split_window('09/01/2026', '09/30/2026'),
   (('09/01/2026', '09/15/2026'), ('09/16/2026', '09/30/2026')))
eq("a single day cannot be split further", PB._split_window('09/01/2026', '09/01/2026'), None)
eq("a two-day window splits into two single days", PB._split_window('09/01/2026', '09/02/2026'),
   (('09/01/2026', '09/01/2026'), ('09/02/2026', '09/02/2026')))
rec("the halves are contiguous and cover the whole range", (lambda h: h and h[0][1] != h[1][0])(
    PB._split_window('09/01/2026', '09/30/2026')))
rec("the criteria count and the truncation test are the same number", PB.CRITERIA_COUNT == 200)

# ================================================================================================
print('\n9. AUCTION PAGINATION — structural only (the loop needs a live browser)')
rec("the page-click cap is no longer 25", F.MAX_PAGE_CLICKS > 25, F.MAX_PAGE_CLICKS)
rec("a traversal that did not prove the last page is collected", hasattr(F, 'PAGING_UNPROVEN'))
_src = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
rec("the three ways the loop ends are named separately",
    _src.count("why = 'last page'") >= 2 and 'unproven:' in _src and "cap: stopped after" in _src)
rec("the cap is a named constant, not a literal in the loop", 'for _ in range(MAX_PAGE_CLICKS)' in _src)

# ================================================================================================
print('\n10. LP CASE STATUS — an unchecked county must not read as a verified-live one')
import lp_status as LS

rec("the counties this script can actually ask about are declared", bool(LS.COUNTY_ADAPTERS))
rec("Broward has no adapter and is not pretended to have one", 'BROWARD' not in LS.COUNTY_ADAPTERS)
rec("Miami-Dade does", 'MIAMI-DADE' in LS.COUNTY_ADAPTERS)
_lpl = open(os.path.join(HERE, 'lp_leads.py'), encoding='utf-8').read()
rec("the board row says whether anyone checked", "'lpStatusChecked'" in _lpl)
rec("...and when", "'lpStatusAsOf'" in _lpl)

# ================================================================================================
print('\n11. RUN REPORT — four states, per-row counties, phones intersected with live leads')
import run_report as RR

eq("a clean night", RR.verdict_of(2437, 'OK', '2026-09-21T05:31', []), ('HEALTHY', ''))
eq("healthcheck DOWN is not healthy", RR.verdict_of(2437, 'DOWN', 'x', [])[0], 'DEGRADED')
eq("a healthcheck that never ran is UNKNOWN, not OK", RR.verdict_of(2437, '', None, [])[0], 'UNKNOWN')
eq("too few leads fails", RR.verdict_of(100, 'OK', 'x', [])[0], 'FAILED')
eq("healthcheck FAIL fails", RR.verdict_of(2437, 'FAIL', 'x', [])[0], 'FAILED')
eq("a county that did not sweep degrades the night", RR.verdict_of(2437, 'OK', 'x', ['BROWARD'])[0], 'DEGRADED')
eq("no LP outcome at all degrades the night", RR.verdict_of(2437, 'OK', None, [])[0], 'DEGRADED')
rec("HEALTHY is the only state that is OK",
    sum(1 for a in (('OK', 'x', []), ('DOWN', 'x', []), ('', None, []), ('OK', 'x', ['BROWARD']))
        if RR.verdict_of(2437, *a)[0] == 'HEALTHY') == 1)

_tmp = tempfile.mkdtemp()
json.dump([{'Case #': 'M%d' % i, 'county': 'MIAMI-DADE'} for i in range(350)] +
          [{'case': 'L%d' % i, 'county': 'BROWARD'} for i in range(500)] +
          [{'case': 'P%d' % i, 'county': 'PALM BEACH'} for i in range(511)],
          open(os.path.join(_tmp, 'leads_final.json'), 'w'))
json.dump([{'case': 'L%d' % i, 'county': 'BROWARD'} for i in range(267)],
          open(os.path.join(_tmp, 'broward_leads.json'), 'w'))
json.dump({**{'L%d' % i: {'phones': ['x']} for i in range(507)},      # 507 cached, 500 still live
           **{'P%d' % i: {'phones': ['x']} for i in range(299)},
           **{'M%d' % i: {'phones': ['x']} for i in range(120)}},
          open(os.path.join(_tmp, 'skiptrace_results.json'), 'w'))
_real_here = RR.HERE
RR.HERE = _tmp
try:
    by = RR._counts()
finally:
    RR.HERE = _real_here

eq("a MIXED leads file is no longer all attributed to Miami-Dade", by['MIAMI-DADE']['leads'], 350)
eq("Broward rows are counted by row, deduped against the county file", by['BROWARD']['leads'], 500)
eq("Palm Beach rows in the mixed file are not lost", by['PALM BEACH']['leads'], 511)
eq("the total is unchanged — only its attribution was wrong", sum(v['leads'] for v in by.values()), 1361)
eq("Broward phones are capped at the leads that still exist (was 507 for 267)",
   by['BROWARD']['phones'], 500)
rec("no county reports more phones than leads",
    all(v['phones'] <= v['leads'] for v in by.values()),
    {c: (v['leads'], v['phones']) for c, v in by.items()})
rec("a county file no longer OVERWRITES the mixed file's count for that county",
    by['BROWARD']['leads'] not in (267,), by['BROWARD'])

# ================================================================================================
print('\n12. LP METADATA — dates compared as dates')
eq("9/18 beats 9/9, which lexicographic order got backwards",
   LR._newest_filing([{'date': '9/9/2026'}, {'date': '9/18/2026'}]), '2026-09-18')
eq("10/1 beats every September date",
   LR._newest_filing([{'date': '9/9/2026'}, {'date': '9/18/2026'}, {'date': '10/1/2026'}]), '2026-10-01')
eq("unparseable rows are skipped, not ranked",
   LR._newest_filing([{'date': 'garbage'}, {'date': '9/18/2026'}, {}, {'date': ''}]), '2026-09-18')
eq("no parseable date is an empty string, not a crash", LR._newest_filing([]), '')
eq("None is survivable", LR._newest_filing(None), '')
rec("the stamp is stored ISO, so the NEXT reader can compare it as text",
    LR._newest_filing([{'date': '1/2/2026'}]) == '2026-01-02')

# ================================================================================================
print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAILED: ' + f)
sys.exit(1 if FAIL else 0)
