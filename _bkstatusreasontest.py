"""_bkstatusreasontest -- a skipped or failed federal bankruptcy run says so in its status.

Run:  python _bkstatusreasontest.py

Fixtures only: invented owners, a temp DEALFLOW_DIR, a stubbed transport, a simulated clock.
No network, no real token. Holds are not under test here; the point is that the status reads
time_budget (not ok) when the deadline passed before any lead was searched, and that a failed
pull records its HTTP status and attempt count. Counts and codes only.
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = pathlib.Path(tempfile.mkdtemp(prefix='bkstatusreason_'))
TOKEN = 'cl-test-token-not-real'
NOW = 1_800_000_000.0
for _k in list(os.environ):
    if _k.startswith('DEALFLOW_BK_') or _k in ('BK_MAX_RUNTIME_S', 'COURTLISTENER_TOKEN'):
        os.environ.pop(_k, None)
os.environ['DEALFLOW_DIR'] = str(TMP / 'boot')

import bk_lookup as BL
import pipeline_alerts as PA

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)[:400]) if not cond else ''))
    if not cond:
        FAILS.append(name)


def isolate(name):
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    os.environ['DEALFLOW_DIR'] = str(d)
    for env, f in (('CACHE', 'bk_lead_cache.json'), ('FILINGS', 'bk_filings.json'),
                   ('OVERRIDES', 'bk_overrides.json'), ('STATUS', 'bk_lookup_status.json'),
                   ('BUDGET', 'bk_budget.json'), ('PULL_STATE', 'bk_pull_state.json')):
        os.environ['DEALFLOW_BK_' + env] = str(d / f)
    os.environ['DEALFLOW_BK_ALLOW_UNCHECKED'] = '0'
    os.environ['COURTLISTENER_TOKEN'] = TOKEN
    os.environ.pop('BK_MAX_RUNTIME_S', None)
    BL._HOLD_MEMO = None
    leads = d / 'repo'
    leads.mkdir(exist_ok=True)
    (leads / 'broward_leads.json').write_text(json.dumps([
        {'case': 'CACE-99-777%03d' % i, 'county': 'BROWARD', 'owners': 'ZZNAME%d, PERSON' % i,
         'st': 'OK'} for i in range(3)]), encoding='utf-8')
    return d, leads


def clock():
    return BL.Clock(now=NOW, sleeper=lambda _s: None)


class Stub:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, url, headers):
        self.calls.append(url)
        st, hd, body = self.fn(url, len(self.calls))
        return st, hd or {}, (json.dumps(body).encode() if not isinstance(body, bytes) else body)


def ok_route(url, n):
    return 200, {}, {'results': [], 'next': None}


def run(leads, stub, clk):
    with contextlib.redirect_stdout(io.StringIO()):
        return BL.run_nightly(here=str(leads), transport=stub, clock=clk)


def status(d):
    return json.loads((d / 'bk_lookup_status.json').read_text(encoding='utf-8'))


real_match = BL.match_filings_to_leads


def run_slow(leads, stub, clk):
    """A run whose match step takes 1000 simulated seconds, past the 900 s limit."""
    def slow(*a, **k):
        out = real_match(*a, **k)
        clk.now += 1000.0
        return out
    BL.match_filings_to_leads = slow
    try:
        return run(leads, stub, clk)
    finally:
        BL.match_filings_to_leads = real_match


# 1. the match step outruns the 900 s limit -> the search loop is skipped
print('-- skipped search')
d, leads = isolate('skipped')
stub = Stub(ok_route)
out = run_slow(leads, stub, clock())
st = status(d)
check('status reason is time_budget, not ok', st.get('reason') == 'time_budget', st)
check('leads still needing a search are counted', st.get('needing_search') == 3, st)
check('nothing was searched this run', st.get('searched_this_run') == 0 and st.get('leads_checked') == 0, st)
check('seconds in the match step are recorded', st.get('match_s', 0) >= 1000, st)
check('requests used this run are recorded (the pull only)',
      st.get('requests_this_run') == len(stub.calls) == 1, st)
check('starved run counter starts at 1', st.get('starved_runs') == 1, st)
cache_file = d / 'bk_lead_cache.json'
cache = json.loads(cache_file.read_text(encoding='utf-8')) if cache_file.exists() else {}
check('no cache entry exists for an unsearched lead (holds unchanged)', not cache, cache)
check('run_nightly returns the reason', out.get('reason') == 'time_budget', out)
text = json.dumps(st)
check('status carries no token, owner or case text',
      TOKEN not in text and 'ZZNAME' not in text and 'CACE' not in text, text)

check('one starved run raises no alert', PA.bk_lookup_alert(BL.public_status(), 'now') is None, '')
run_slow(leads, Stub(ok_route), clock())
check('two starved runs raise no alert yet', PA.bk_lookup_alert(BL.public_status(), 'now') is None, '')
run_slow(leads, Stub(ok_route), clock())
st = status(d)
check('three starved runs in a row count 3', st.get('starved_runs') == 3, st)
al = PA.bk_lookup_alert(BL.public_status(), 'now')
check('three starved runs raise the bk-lookup warning',
      al and al['key'] == 'bk-lookup' and al['severity'] == 'warn' and 'unsearched' in al['text'], al)
check('alert text is counts only', al and 'ZZNAME' not in al['text'] and TOKEN not in al['text'], al)

# 2. a normal run searches, resets the counter, records no failure
print('-- normal run')
stub = Stub(ok_route)
run(leads, stub, clock())
st = status(d)
check('normal run reason ok', st.get('reason') == 'ok', st)
check('normal run searched all three', st.get('searched_this_run') == 3 and st.get('leads_checked') == 3, st)
check('normal run resets the starved counter', st.get('starved_runs') == 0, st)
check('normal run records no HTTP failure', st.get('last_http_status') == 0 and st.get('pull_attempts') == 0, st)
check('alert clears', PA.bk_lookup_alert(BL.public_status(), 'now') is None, '')

# 3. failed pull records status, attempts, reason
print('-- failed pull')
d, leads = isolate('fail500')
run(leads, Stub(lambda u, n: (500, {}, {'detail': TOKEN})), clock())
st = status(d)
check('500: reason pull_failed', st.get('reason') == 'pull_failed', st)
check('500: HTTP status recorded', st.get('last_http_status') == 500, st)
check('500: one attempt', st.get('pull_attempts') == 1, st)
check('500: failure label', st.get('pull_fail') == 'http_error', st)
check('500: token not in status', TOKEN not in json.dumps(st), st)

d, leads = isolate('fail429')
stub = Stub(lambda u, n: (429, {}, {}))
run(leads, stub, clock())
st = status(d)
check('429: status, attempts (1 + bounded retries) and label',
      st.get('last_http_status') == 429 and st.get('pull_attempts') == BL.RETRY_429 + 1
      and st.get('pull_fail') == 'rate_limited', st)
pulls = [u for u in stub.calls if 'court=flsb' in u]
check('429: the pull made 1 + the existing bounded retries, no new ones', len(pulls) == BL.RETRY_429 + 1, len(pulls))

d, leads = isolate('failnet')


def boom(url, headers):
    raise OSError('connection reset')


run(leads, boom, clock())
st = status(d)
check('network: label network, no status, one attempt',
      st.get('pull_fail') == 'network' and st.get('last_http_status') == 0
      and st.get('pull_attempts') == 1, st)

print('FAILED: %s' % FAILS if FAILS else 'ALL PASS')
sys.exit(1 if FAILS else 0)
