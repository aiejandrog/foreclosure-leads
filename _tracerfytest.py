"""Tracerfy credit diagnosis and the free balance probe. No network, no key, no spend.

Fixtures only: invented log lines and a scripted check_balance. Nothing here calls Tracerfy.
"""
import json, os, pathlib, sys, tempfile
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bd_budget
import skiptrace as ST
import skiptrace_health as SH
import tracerfy_mcp as TM

ok, bad = [], []

def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


REFUSAL = (
    'Insufficient credits. Instant trace requires 5 credits per lookup. You have 1 credits.\n'
    '  >>> TOP UP TRACERFY: https://tracerfy.com  (Instant trace needs 5 credits/lookup)\n'
)
STALE = '  [stale local copy — this box is not the runner]'


def rows_of(**kw):
    rows = []
    SH.report_skiptrace_health(lambda *a: rows.append(a), **kw)
    return rows


def freshness(rows):
    return next(r for r in rows if r[1] == 'skiptrace freshness')


# ---- the 09-18..09-24 miss: the nightly ran, the account was empty ---------------------------
base = dict(age_days=7, when_iso='2026-09-18', cached=12, is_runner=True, stale_note='',
            log_text='', balance=None)
rows = rows_of(**base)
rec('stale, no log and no balance: still "not running"',
    freshness(rows)[0] == 'FAIL' and freshness(rows)[2].endswith('the nightly skiptrace is not running')
    and 'credits exhausted' not in freshness(rows)[2], freshness(rows)[2])

rows = rows_of(**{**base, 'log_text': REFUSAL, 'balance': None})
rec('stale, latest log shows the refusal, probe down: credits exhausted',
    'Tracerfy credits exhausted: top up' in freshness(rows)[2]
    and 'not running' not in freshness(rows)[2], freshness(rows)[2])

rows = rows_of(**{**base, 'log_text': '', 'balance': 1})
rec('stale, live balance is 1 credit: credits exhausted, with the count',
    'Tracerfy credits exhausted: top up' in freshness(rows)[2] and '1 credit left' in freshness(rows)[2]
    and 'not running' not in freshness(rows)[2], freshness(rows)[2])
rec('that FAIL line is not repeated as a second warning',
    not any(r[1] == 'tracerfy balance' for r in rows), rows)

rows = rows_of(**{**base, 'log_text': REFUSAL, 'balance': 1})
rec('log and balance agree the account is empty',
    freshness(rows)[0] == 'FAIL' and 'Tracerfy credits exhausted: top up' in freshness(rows)[2])

rows = rows_of(**{**base, 'log_text': REFUSAL, 'balance': 800})
rec('last run was refused but the account can pay now: not "not running", not still empty',
    'not running' not in freshness(rows)[2]
    and 'Tracerfy credits exhausted: top up' not in freshness(rows)[2]
    and 'balance can pay for a lookup now' in freshness(rows)[2]
    and not any(r[1] == 'tracerfy balance' for r in rows),
    freshness(rows)[2])

# ---- low balance warns before the account runs dry, including while traces are still fresh ----
rows = rows_of(**{**base, 'age_days': 0, 'balance': 400})
rec('fresh traces stay PASS', freshness(rows)[0] == 'PASS' and 'not running' not in freshness(rows)[2])
warns = [r for r in rows if r[1] == 'tracerfy balance']
rec('400 credits warns, under the default 500',
    len(warns) == 1 and warns[0][0] == 'WARN' and '400 credits left' in warns[0][2]
    and 'warn under 500' in warns[0][2] and 'exhausted' not in warns[0][2], warns)

rows = rows_of(**{**base, 'age_days': 0, 'balance': 500})
rec('exactly the threshold is not a low-balance warning',
    not any(r[1] == 'tracerfy balance' for r in rows), rows)

rows = rows_of(**{**base, 'age_days': 0, 'balance': 5})
rec('5 credits still buys one lookup, so it warns low rather than exhausted',
    any(r[1] == 'tracerfy balance' and 'low' in r[2] and 'exhausted' not in r[2] for r in rows), rows)

rows = rows_of(**{**base, 'age_days': 3, 'log_text': REFUSAL, 'balance': 1})
rec('a 3-day WARN names credits, not a dead job',
    freshness(rows)[0] == 'WARN' and 'Tracerfy credits exhausted: top up' in freshness(rows)[2]
    and 'not running' not in freshness(rows)[2], freshness(rows)[2])

rows = rows_of(**{**base, 'age_days': 1, 'balance': 0})
rec('a dry account warns the day it happens, before the trace date goes stale',
    freshness(rows)[0] == 'PASS'
    and any(r[0] == 'WARN' and 'Tracerfy credits exhausted: top up' in r[2] for r in rows))

os.environ['TRACERFY_LOW_CREDITS'] = '50'
try:
    rows = rows_of(**{**base, 'age_days': 0, 'balance': 40})
    rec('TRACERFY_LOW_CREDITS=50 warns at 40',
        any('warn under 50' in r[2] for r in rows if r[1] == 'tracerfy balance'), rows)
    rows = rows_of(**{**base, 'age_days': 0, 'balance': 80})
    rec('TRACERFY_LOW_CREDITS=50 does not warn at 80',
        not any(r[1] == 'tracerfy balance' for r in rows))
finally:
    os.environ.pop('TRACERFY_LOW_CREDITS', None)

rows = rows_of(**{**base, 'age_days': 0, 'balance': None, 'log_text': REFUSAL})
rec('a failed balance probe adds no balance line', not any(r[1] == 'tracerfy balance' for r in rows))

# age thresholds unchanged
rec('3 days is WARN, 4 is still WARN, 5 is FAIL',
    SH.freshness_level(3) == 'WARN' and SH.freshness_level(4) == 'WARN' and SH.freshness_level(5) == 'FAIL'
    and SH.freshness_level(2) == 'PASS')

# ---- a non-runner must not narrate its frozen copy as a live outage ---------------------------
rows = rows_of(**{**base, 'is_runner': False, 'stale_note': STALE, 'log_text': REFUSAL, 'balance': 1})
line = freshness(rows)[2]
rec('non-runner freshness does not say "not running" or blame credits for the copy',
    'not running' not in line and 'credits' not in line and line.endswith(STALE), line)
rec('non-runner still reports the live account balance',
    any(r[1] == 'tracerfy balance' and 'exhausted' in r[2] for r in rows), rows)

# ---- only the latest run counts ----------------------------------------------------------------
old = '==================== REFRESH old ====================\n' + REFUSAL
new = '==================== REFRESH new ====================\nprovider: tracerfy\nDONE: 2/2\n'
rec('an older run\'s refusal is not this run\'s',
    not SH.logs_show_tracerfy_exhausted(SH.latest_run_slice(old + new, ('==================== REFRESH ',))))
rec('the latest run\'s refusal is',
    SH.logs_show_tracerfy_exhausted(SH.latest_run_slice(new + old, ('==================== REFRESH ',))))

d = pathlib.Path(tempfile.mkdtemp(prefix='tfylog_'))
(d / 'leads-run.log').write_text(new, encoding='utf-8')
(d / 'phones-run.log').write_text(
    '==== phones-nightly earlier ====\nall good\n'
    '==== phones-nightly later ====\n' + REFUSAL, encoding='utf-8')
combined = SH.latest_skiptrace_logs(d)
rec('phones-run.log\'s latest nightly is read, and an older phones run is not',
    'You have 1 credits' in combined and 'all good' not in combined)
rec('a clean leads-run does not hide a phones-run refusal',
    SH.logs_show_tracerfy_exhausted(combined))
rec('a missing log is skipped', SH.latest_skiptrace_logs(d / 'nope') == '')

# exit 2 for a bad key must not look like an empty account
rec('a key rejection without the credit strings is not exhaustion',
    not SH.logs_show_tracerfy_exhausted('>>> KEY REJECTED — the API key is bad\nPHONES DEGRADED - skiptrace exit 2'))

# healthcheck actually asks for this, so a revert to the old sentence fails here
hc = (HERE / 'healthcheck.py').read_text(encoding='utf-8')
rec('healthcheck reports freshness through skiptrace_health',
    'skiptrace_health.report_skiptrace_health(' in hc)
rec('healthcheck still probes balance when there is no dated trace',
    'skiptrace_health.probe_tracerfy_balance()' in hc
    and 'skiptrace_health.tracerfy_balance_warning(' in hc)

# ---- balance_soft: free, and soft -------------------------------------------------------------
ledger = pathlib.Path(tempfile.mkdtemp(prefix='tfybal_')) / 'batchdata_spend.json'
bd_budget.LEDGER = str(ledger)
calls = []

def _no_charge(tool, arguments=None, timeout=90):
    calls.append((tool, arguments, timeout))
    raise AssertionError('call should not have run')

TM.call = _no_charge
TM.MCP_URL_F = str(d / 'missing.url')
rec('missing URL file returns None and does not call',
    TM.balance_soft() is None and calls == [])

(d / 'empty.url').write_text('  \n', encoding='utf-8')
TM.MCP_URL_F = str(d / 'empty.url')
rec('blank URL file returns None and does not call', TM.balance_soft() is None and calls == [])

(d / 'tracerfy_mcp.url').write_text('https://example.invalid/mcp\n', encoding='utf-8')
TM.MCP_URL_F = str(d / 'tracerfy_mcp.url')

def _down(tool, arguments=None, timeout=90):
    calls.append((tool, timeout))
    raise ConnectionError('DNS lookup failed')

TM.call = _down
rec('a network failure returns None', TM.balance_soft() is None)
rec('the probe asked for check_balance and nothing else', calls == [('check_balance', 15)], calls)
rec('a failed probe writes no spend', not ledger.exists())

def _exit(tool, arguments=None, timeout=90):
    raise SystemExit('should not escape')

TM.call = _exit
rec('SystemExit from the probe does not escape', TM.balance_soft() is None)

def _ok(tool, arguments=None, timeout=90):
    calls.append(tool)
    return {'credits': 12}

calls.clear()
TM.call = _ok
rec('a real balance comes back as an int', TM.balance_soft() == 12)
rec('only check_balance was called', calls == ['check_balance'], calls)

TM.call = lambda *a, **k: {'ok': True}
rec('a body with no credits key is None, not zero', TM.balance_soft() is None)

TM.call = lambda *a, **k: {'credits': 0}
rec('a reported zero is zero, not a failed probe', TM.balance_soft() == 0)

TM.call = lambda *a, **k: {'_raw': 'not json credits'}
rec('a non-object probe result is None', TM.balance_soft() is None)

# The probe must not record spend even when it succeeds.
rec('a successful probe writes no spend', not ledger.exists() or json.loads(ledger.read_text() or '{}') == {})

# ---- receipt matrix (no HTTP) ------------------------------------------------------------------
cost = 0.10
rec('receipt 5 credits is $0.10 confirmed',
    ST.tracerfy_ledger_charge(200, {'credits_deducted': 5, 'hit': True}, cost)
    == (0.10, ST.TRACERFY_NOTE))
rec('receipt 0 is not recorded',
    ST.tracerfy_ledger_charge(200, {'credits_deducted': 0, 'hit': False, 'persons': []}, cost) is None)
rec('402 insufficient credits is not recorded',
    ST.tracerfy_ledger_charge(402, {'error': 'Insufficient credits. Instant trace requires 5 credits per lookup. You have 1 credits.'}, cost) is None)
rec('no response is the cap price, labeled unconfirmed',
    ST.tracerfy_ledger_charge(None, None, cost) == (cost, ST.TRACERFY_NOTE_UNCONFIRMED))
rec('500 is unconfirmed',
    ST.tracerfy_ledger_charge(500, {'error': 'down'}, cost) == (cost, ST.TRACERFY_NOTE_UNCONFIRMED))
rec('200 with neither hit nor credits_deducted is unconfirmed',
    ST.tracerfy_ledger_charge(200, {'persons': []}, cost) == (cost, ST.TRACERFY_NOTE_UNCONFIRMED))
rec('hit true without a receipt bills the lookup price as confirmed',
    ST.tracerfy_ledger_charge(200, {'hit': True, 'persons': []}, cost) == (cost, ST.TRACERFY_NOTE))
rec('hit false without a receipt is a miss',
    ST.tracerfy_ledger_charge(200, {'hit': False}, cost) is None)

# healthcheck source must keep using the runner gate's stale note, which report still receives
rec('healthcheck passes the runner stale note into the report',
    'stale_note=_STALE_NOTE' in hc)

total = len(ok) + len(bad)
print(f'\n==== {len(ok)}/{total} tracerfy credit checks passed ====')
raise SystemExit(1 if bad else 0)
