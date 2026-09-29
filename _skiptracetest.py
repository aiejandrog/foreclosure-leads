"""skiptrace.py fail-loud behaviour — the guard that makes the 23/23-403 incident impossible.

Added 2026-08-02. Before the hardening, skiptrace did one POST + raise_for_status per lead and
turned EVERY error into one stdout line, then exited 0 — so a dead key burned the whole queue and
the downstream `if errorlevel 1` never tripped, rebuilding + pushing a phone-poor board over a good
one. These checks pin the new contract:

  exit 0  every queued lead attempted (misses are fine)
  exit 2  provider rejected the key (bad key / exhausted balance) -> abort on the FIRST such error
  exit 3  three consecutive transient failures -> provider looks down
  exit 4  projected spend over --max-spend -> nothing called

No network, no real key, no spend: requests.Session is replaced by a scripted fake, time.sleep is
a no-op (so the 429 backoff test is instant), and the results file is a throwaway temp.
"""
import json, os, pathlib, sys, tempfile, types
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import requests

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import skiptrace as ST
import bd_budget

# The shared daily budget is GLOBAL by design — which means an un-isolated test run would spend the
# operator's real allowance on fake calls and then start failing for lack of budget. Point the
# ledger at a throwaway and lift the cap so each scenario measures skiptrace, not the wallet.
os.environ['BATCHDATA_DAILY_CAP'] = '9999'

ok, bad = [], []
def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


class FakeResp:
    def __init__(self, status, body=None, text=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = text if text is not None else json.dumps(self._body)
    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


class FakeSession:
    """Pops one scripted action per .post(): a FakeResp is returned, an Exception is raised."""
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0
    def post(self, url, json=None, timeout=None, headers=None):
        self.calls = self.calls + 1
        if not self.actions:
            raise AssertionError('FakeSession ran out of scripted actions')
        a = self.actions.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def _lead(case):
    # human owner + a clean mailing address so select() queues it and parse_addr accepts it
    return {'Case #': case, 'owners': 'DOE, JOHN', 'tier': 'A',
            'mailing_address': '123 MAIN ST, MIAMI, FL 33101', 'county': 'MIAMI-DADE'}


def run(leads, actions, argv, seed=None, ledger=None):
    """Run ST.main() against fakes. Returns (exit_code, results_dict, session).
    `ledger` shares one spend ledger across calls — that's how the 'the cap is per DAY, not per
    run' property gets tested (two runs, one wallet)."""
    d = pathlib.Path(tempfile.mkdtemp(prefix='sktest_'))
    tmp = d / 'skiptrace_results.json'
    if seed is not None:
        tmp.write_text(json.dumps(seed), encoding='utf-8')
    ST.RESULTS = str(tmp)
    bd_budget.LEDGER = str(ledger or (d / 'batchdata_spend.json'))   # never the real money ledger
    ST.load_all_leads = lambda: [dict(x) for x in leads]
    ST.load_key = lambda prov: 'FAKEKEY'
    ST.time.sleep = lambda *a, **k: None                 # no 0.3s waits, no 20s 429 backoff
    sess = FakeSession(actions)
    ST.requests.Session = lambda: sess                   # main() calls requests.Session()
    # llc_officers.json read is harmless (leads aren't companies); leave it.
    old_argv = sys.argv
    # Pin the provider the MOCKS are shaped for (BatchData: results.persons[].phoneNumbers). Without
    # this the run relies on pick_provider() auto-detect, which now returns 'tracerfy' whenever a
    # tracerfy.key exists on disk (it does, since 2026-08-11) — and _extract_tracerfy reads a
    # different shape (persons[].phones), so every phone extracted empty. The extraction logic is
    # fine in production; the test just must not depend on which key files happen to exist.
    if '--provider' not in argv:
        argv = argv + ['--provider', 'batchdata']
    # The Tracerfy preflight calls the free balance probe. Tests must not touch the network,
    # and a missing tracerfy_mcp.url used to sys.exit out of that "non-fatal" probe.
    if 'tracerfy' in argv:
        import tracerfy_mcp
        tracerfy_mcp.balance_soft = lambda *a, **k: None
    sys.argv = ['skiptrace.py'] + argv
    code = 0
    try:
        ST.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old_argv
    res = json.loads(tmp.read_text(encoding='utf-8')) if tmp.exists() else {}
    return code, res, sess


PHONES = {'results': {'persons': [{'phoneNumbers': [{'number': '3055551212', 'type': 'mobile'}],
                                   'emails': ['j@example.com'], 'score': 9}]}}
EMPTY = {'results': {'persons': []}}
BAL403 = {'status': {'code': 403, 'message': 'Insufficient balance. Please top up.'}}
KEY401 = {'message': 'Invalid API key'}


# 1 ─ 403 balance on lead #1: abort after ONE call, exit 2, cache NOT poisoned ------------------
code, res, sess = run([_lead('C1'), _lead('C2'), _lead('C3')],
                      [FakeResp(403, BAL403)], ['--all'])
rec('403-balance aborts the run', code == 2, f'exit {code}')
rec('403-balance stops after exactly ONE call (not 3)', sess.calls == 1, f'{sess.calls} calls')
rec('403-balance writes NO cache entry for the failed lead', 'C1' not in res, list(res))

# 2 ─ 401 bad key: same abort path -------------------------------------------------------------
code, res, sess = run([_lead('C1'), _lead('C2')], [FakeResp(401, KEY401)], ['--all'])
rec('401 aborts with exit 2', code == 2, f'exit {code}')
rec('401 stops after one call', sess.calls == 1, f'{sess.calls} calls')

# 3 ─ 429 then 200: backoff, retry, succeed ----------------------------------------------------
code, res, sess = run([_lead('C1')], [FakeResp(429, {'message': 'slow down'}), FakeResp(200, PHONES)],
                      ['--all'])
rec('429-then-200 succeeds (exit 0)', code == 0, f'exit {code}')
rec('429 was retried in place (2 calls for 1 lead)', sess.calls == 2, f'{sess.calls} calls')
rec('429-then-200 caches the phone', bool(res.get('C1', {}).get('phones')), res.get('C1'))

# 4 ─ three consecutive timeouts -> abort exit 3 -----------------------------------------------
code, res, sess = run([_lead('C1'), _lead('C2'), _lead('C3'), _lead('C4')],
                      [requests.exceptions.Timeout('t1'), requests.exceptions.Timeout('t2'),
                       requests.exceptions.Timeout('t3'), FakeResp(200, PHONES)],
                      ['--all'])
rec('3 consecutive transient failures abort (exit 3)', code == 3, f'exit {code}')
rec('abort happened at the 3rd strike, before lead #4', sess.calls == 3, f'{sess.calls} calls')

# 4b ─ two timeouts then a success: strike counter resets, run completes ------------------------
code, res, sess = run([_lead('C1'), _lead('C2'), _lead('C3')],
                      [requests.exceptions.Timeout('t1'), requests.exceptions.Timeout('t2'),
                       FakeResp(200, PHONES)],
                      ['--all'])
rec('2 timeouts then success does NOT abort (exit 0)', code == 0, f'exit {code}')
rec('the recovered lead is cached', bool(res.get('C3', {}).get('phones')), res.get('C3'))

# 5 ─ mixed 200s: phones cached, empty cached, exit 0 ------------------------------------------
code, res, sess = run([_lead('C1'), _lead('C2')], [FakeResp(200, PHONES), FakeResp(200, EMPTY)],
                      ['--all'])
rec('a normal run exits 0', code == 0, f'exit {code}')
rec('200-with-phones is cached with the number', res.get('C1', {}).get('phones'), res.get('C1'))
rec('200-empty is cached (so it is not re-charged next run)',
    'C2' in res and res['C2'].get('phones') == [], res.get('C2'))

# 6 ─ 400 bad address is a per-lead SKIP, not a strike -----------------------------------------
# three 400s in a row must NOT trip the transient abort — bad data isn't a provider outage.
code, res, sess = run([_lead('C1'), _lead('C2'), _lead('C3'), _lead('C4')],
                      [FakeResp(400, {'message': 'bad address'}),
                       FakeResp(400, {'message': 'bad address'}),
                       FakeResp(400, {'message': 'bad address'}),
                       FakeResp(200, PHONES)],
                      ['--all'])
rec('a run of 400s does NOT false-abort (exit 0)', code == 0, f'exit {code}')
rec('the 4th good lead still got traced', bool(res.get('C4', {}).get('phones')), res.get('C4'))

# 7 ─ --max-spend ceiling: exit 4, ZERO calls --------------------------------------------------
code, res, sess = run([_lead(f'C{i}') for i in range(50)], [FakeResp(200, PHONES)],
                      ['--all', '--max-spend', '1'])
rec('--max-spend over budget aborts (exit 4)', code == 4, f'exit {code}')
rec('--max-spend makes ZERO API calls', sess.calls == 0, f'{sess.calls} calls')
rec('--max-spend writes nothing', res == {}, list(res))

# 8 ─ cache dedupe: a cached case is not re-called without --refresh ----------------------------
code, res, sess = run([_lead('C1'), _lead('C2')], [FakeResp(200, PHONES)], ['--all'],
                      seed={'C1': {'name': 'DOE, JOHN', 'phones': [{'number': '3050000000'}],
                                   'emails': [], 'traced': '2026-08-01', 'source': 'batchdata'}})
rec('a cached case is skipped (only the uncached one is called)', sess.calls == 1, f'{sess.calls} calls')
rec('dedupe preserves the existing cached entry', res.get('C1', {}).get('phones'), res.get('C1'))

# 8b ─ --refresh re-calls even cached cases ----------------------------------------------------
code, res, sess = run([_lead('C1'), _lead('C2')], [FakeResp(200, PHONES), FakeResp(200, PHONES)],
                      ['--all', '--refresh'],
                      seed={'C1': {'phones': [], 'traced': '2026-08-01'}})
rec('--refresh re-traces cached cases (2 calls)', sess.calls == 2, f'{sess.calls} calls')

# 9 ─ THE SHARED DAILY BUDGET — the guard that exists because $50 vanished in five minutes -----
# Two BatchData scripts bill one wallet from three schedulers; a per-script cap can't bound that.
# Only a shared ledger can. Cap at $0.30 = exactly 2 lookups, then it must refuse.
os.environ['BATCHDATA_DAILY_CAP'] = '0.30'
SHARED = pathlib.Path(tempfile.mkdtemp(prefix='skbudget_')) / 'batchdata_spend.json'
code, res, sess = run([_lead(f'C{i}') for i in range(10)],
                      [FakeResp(200, PHONES)] * 10, ['--all'], ledger=SHARED)
rec('daily budget stops the run once the cap is spent (exit 5)', code == 5, f'exit {code}')
rec('budget allows EXACTLY the number of calls it can pay for (2 x $0.15 = $0.30)',
    sess.calls == 2, f'{sess.calls} calls')
rec('leads paid for before the cap are kept, not lost', len(res) == 2, f'{len(res)} cached')

# the cap is SHARED: a second script/scheduler run the same day gets nothing more
code2, res2, sess2 = run([_lead('D1')], [FakeResp(200, PHONES)], ['--all'], ledger=SHARED)
rec('a SECOND run the same day is refused (shared ledger, not per-run)',
    sess2.calls == 0 and code2 == 5, f'{sess2.calls} calls, exit {code2}')
os.environ['BATCHDATA_DAILY_CAP'] = '9999'


def _today():
    led = bd_budget._load()
    return led.get(str(__import__('datetime').date.today()))


def _near(got, want):
    try:
        return abs(float(got) - float(want)) < 1e-9
    except (TypeError, ValueError):
        return False


# 10 ─ Tracerfy bills only what the response says it billed ------------------------------------
# Instant lookup: credits_deducted 5 on a hit (~$0.10), 0 on a miss. A 402 refusal, a timeout
# and a DNS failure used to record $0.10 before the call's fate was known.
TFY_HIT = {'hit': True, 'credits_deducted': 5, 'persons': [
    {'phones': [{'number': '3055551212', 'type': 'Mobile', 'rank': 1}],
     'emails': [{'email': 'j@example.com'}]}]}
TFY_MISS = {'hit': False, 'credits_deducted': 0, 'persons_count': 0, 'persons': []}
TFY_402 = {'error': 'Insufficient credits. Instant trace requires 5 credits per lookup. You have 1 credits.'}
TFY_AMBIG = {'persons': [{'phones': [{'number': '3055551212', 'type': 'Mobile', 'rank': 1}]}]}

_saved_tfy_cap = ST.TRACERFY_DAILY_CAP
try:
    code, res, sess = run([_lead('C1'), _lead('C2')], [FakeResp(402, TFY_402)],
                          ['--all', '--provider', 'tracerfy'])
    day = _today()
    rec('tracerfy 402 still aborts the run (exit 2)', code == 2, f'exit {code}')
    rec('tracerfy 402 stops after one call', sess.calls == 1, f'{sess.calls} calls')
    rec('tracerfy 402 records no spend (the refusal was not billed)',
        day is None or _near((day or {}).get('total'), 0), day)

    code, res, sess = run([_lead('C1'), _lead('C2')],
                          [FakeResp(200, TFY_MISS), FakeResp(200, TFY_HIT)],
                          ['--all', '--provider', 'tracerfy'])
    day = _today()
    rec('tracerfy miss then hit exits 0', code == 0, f'exit {code}')
    rec('tracerfy miss is cached and not re-charged next time',
        res.get('C1', {}).get('phones') == [], res.get('C1'))
    rec('tracerfy hit is cached', bool(res.get('C2', {}).get('phones')), res.get('C2'))
    rec('tracerfy miss records $0 and the hit records $0.10',
        day and _near(day.get('total'), 0.10) and _near((day.get('by') or {}).get('skiptrace-tracerfy'), 0.10)
        and 'skiptrace-tracerfy-unconfirmed' not in (day.get('by') or {}), day)

    code, res, sess = run([_lead('C1')], [requests.exceptions.Timeout('timed out')],
                          ['--all', '--provider', 'tracerfy'])
    day = _today()
    rec('tracerfy timeout does not abort a single strike (exit 0)', code == 0, f'exit {code}')
    rec('tracerfy timeout is counted toward the cap but labeled unconfirmed',
        day and _near((day.get('by') or {}).get('skiptrace-tracerfy-unconfirmed'), 0.10)
        and 'skiptrace-tracerfy' not in (day.get('by') or {}), day)
    rec('the tracerfy cap includes that unconfirmed dollar',
        _near(ST._tracerfy_spent_today(), 0.10), ST._tracerfy_spent_today())

    code, res, sess = run([_lead('C1')],
                          [requests.exceptions.ConnectionError('Name or service not known')],
                          ['--all', '--provider', 'tracerfy'])
    day = _today()
    rec('tracerfy DNS failure is unconfirmed spend, not a confirmed hit',
        day and _near((day.get('by') or {}).get('skiptrace-tracerfy-unconfirmed'), 0.10)
        and 'skiptrace-tracerfy' not in (day.get('by') or {}), day)

    code, res, sess = run([_lead('C1')], [FakeResp(200, TFY_AMBIG)],
                          ['--all', '--provider', 'tracerfy'])
    day = _today()
    rec('tracerfy 200 with no receipt is unconfirmed, and the phone is still kept',
        code == 0 and bool(res.get('C1', {}).get('phones'))
        and day and _near((day.get('by') or {}).get('skiptrace-tracerfy-unconfirmed'), 0.10), day)

    code, res, sess = run([_lead('C1')], [FakeResp(500, {'error': 'temporarily unavailable'})],
                          ['--all', '--provider', 'tracerfy'])
    day = _today()
    rec('tracerfy 500 is unconfirmed spend',
        day and _near((day.get('by') or {}).get('skiptrace-tracerfy-unconfirmed'), 0.10), day)

    code, res, sess = run([_lead('C1')],
                          [FakeResp(429, {'message': 'slow down'}), FakeResp(200, TFY_HIT)],
                          ['--all', '--provider', 'tracerfy'])
    day = _today()
    rec('tracerfy 429 is not billed; the following hit is billed once',
        code == 0 and sess.calls == 2 and day and _near(day.get('total'), 0.10)
        and _near((day.get('by') or {}).get('skiptrace-tracerfy'), 0.10), day)

    # Cap stays conservative: unconfirmed dollars consume TRACERFY_DAILY_CAP.
    ST.TRACERFY_DAILY_CAP = 0.10
    code, res, sess = run([_lead('C1'), _lead('C2')],
                          [requests.exceptions.Timeout('t1'), requests.exceptions.Timeout('t2')],
                          ['--all', '--provider', 'tracerfy'])
    rec('unconfirmed spend trips the tracerfy cap before the next call (exit 5)',
        code == 5 and sess.calls == 1, f'exit {code}, {sess.calls} calls')

    # Misses are free, so they must not consume the cap: two misses and a hit all run at $0.10.
    ST.TRACERFY_DAILY_CAP = 0.10
    code, res, sess = run([_lead('C1'), _lead('C2'), _lead('C3')],
                          [FakeResp(200, TFY_MISS), FakeResp(200, TFY_MISS), FakeResp(200, TFY_HIT)],
                          ['--all', '--provider', 'tracerfy'])
    day = _today()
    rec('free misses do not eat the tracerfy cap out from under a later hit',
        code == 0 and sess.calls == 3 and day and _near(day.get('total'), 0.10),
        f'exit {code}, {sess.calls} calls, {day}')
finally:
    ST.TRACERFY_DAILY_CAP = _saved_tfy_cap

# BatchData still charges a miss and a timeout — that provider bills the request, not the hit.
code, res, sess = run([_lead('C1')], [FakeResp(200, EMPTY)], ['--all'])
day = _today()
rec('batchdata miss still charges $0.15', day and _near(day.get('total'), 0.15)
    and _near((day.get('by') or {}).get('skiptrace-batchdata'), 0.15), day)
code, res, sess = run([_lead('C1')], [requests.exceptions.Timeout('t')], ['--all'])
day = _today()
rec('batchdata timeout still charges $0.15', day and _near((day.get('by') or {}).get('skiptrace-batchdata'), 0.15), day)

# --county-first: order only. Miami-Dade leads trace before other counties, each group soonest
# auction first; a lead with no county field is Miami-Dade (leads_final.json rows carry none).
def _cl(case, county, days):
    r = _lead(case); r['days_to_auction'] = days
    if county is None:
        r.pop('county', None)
    else:
        r['county'] = county
    return r
_mix = [_cl('B1', 'BROWARD', 2), _cl('P1', 'PALM BEACH', 3), _cl('M1', 'MIAMI-DADE', 40),
        _cl('M2', None, 20), _cl('B2', 'BROWARD', 1)]
code, res, sess = run(_mix, [FakeResp(200, PHONES)] * 2, ['--all', '--limit', '2', '--county-first', 'miami-dade'])
rec('--county-first MIAMI-DADE spends the --limit on Miami leads before a sooner Broward sale',
    code == 0 and sorted(res) == ['M1', 'M2'], sorted(res))
code, res, sess = run(_mix, [FakeResp(200, PHONES)] * 2, ['--all', '--limit', '2'])
rec('without --county-first the order is unchanged (soonest auction first, any county)',
    code == 0 and sorted(res) == ['B1', 'B2'], sorted(res))
code, res, sess = run(_mix, [FakeResp(200, PHONES)] * 3, ['--all', '--limit', '3', '--county-first', 'MIAMI'])
rec('--county-first fills the rest of the limit from other counties, soonest first',
    code == 0 and sorted(res) == ['B2', 'M1', 'M2'], sorted(res))
rec('county names fold: Miami-Dade / MIAMI / miami dade are one key, Palm Beach two spellings one',
    ST._county_key('Miami-Dade') == ST._county_key('MIAMI') == ST._county_key('miami dade')
    and ST._county_key('PALM BEACH') == ST._county_key('palmbeach') != ST._county_key('BROWARD'))

total = len(ok) + len(bad)
print(f'\n==== {len(ok)}/{total} skiptrace hardening checks passed ====')
raise SystemExit(1 if bad else 0)
