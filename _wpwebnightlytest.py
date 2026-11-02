"""Nightly Whitepages web lookup: bounded runtime, stop-on-block, cooldown, off-by-default, alert.

No network, no browser, no real people: fixtures are invented, DEALFLOW_DIR is a temp folder, the
browser is a fake. Pins:
  - the run stops BETWEEN leads once WP_WEB_MAX_RUNTIME_S has passed (reason time_budget)
  - a login wall, bot challenge, HTTP 403/429 or 'sign in' page stops the whole run at once
  - challenge / rate_limited write a 24 h cooldown; the next automated run does not start
  - wp_web_status.json holds counts and a reason only (no name, phone, email)
  - the nightly cap is 15 by default and never above 25
  - refresh-dealflow.bat runs the step only when WP_WEB_NIGHTLY=1, before the rebuild, fail-soft,
    and the step publishes / git-adds nothing
  - pipeline_alerts 'wp-web': silent when off, WARN (never fail) on a bad reason or a stale status

Run: python _wpwebnightlytest.py
"""
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TD = tempfile.mkdtemp(prefix='wpnight_')
os.environ['DEALFLOW_DIR'] = TD
for k in ('WP_WEB_NIGHTLY', 'WP_WEB_NIGHTLY_MAX', 'WP_WEB_MAX_RUNTIME_S'):
    os.environ.pop(k, None)
os.environ['WP_WEB_THROTTLE_S'] = '0'

import wp_web as W                      # noqa: E402
import whitepages_lookup as WL          # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


WL.CACHE = os.path.join(TD, 'wp.json')
W.CACHE = WL.CACHE
NAME, PHONE, EMAIL = 'ZZTESTOWNER, QUENTIN', '3055550142', 'qzz@example.com'
LEADS = [{'Case #': 'T-%d' % i, 'Address': '%d NW 1ST AVE, MIAMI, FL 33101' % (100 + i)} for i in range(1, 6)]


def entry():
    return {'result': {'ok': 1}, '_http': 200, 'owners': [NAME], 'phones': [PHONE], 'emails': [EMAIL]}


def status():
    p = os.path.join(TD, 'wp_web_status.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def clear_status():
    p = os.path.join(TD, 'wp_web_status.json')
    if os.path.exists(p):
        os.remove(p)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


# ---- classification ---------------------------------------------------------------------------
print('CLASSIFY')
cb = getattr(W, 'classify_block', None)
rec('classify_block exists', cb is not None)
if cb:
    rec('429 is rate_limited', cb('x', '', 429, False) == 'rate_limited')
    rec('403 is challenge', cb('x', '', 403, False) == 'challenge')
    rec('bot-check text with nothing parsed is challenge', cb('Please verify you are human', '', 200, False) == 'challenge')
    rec('signed-out page with nothing parsed is login_required',
        cb('Welcome. Log in to see phone numbers', '', 200, False) == 'login_required')
    rec('a login URL is login_required', cb('whatever', 'https://x/login?next=1', 200, False) == 'login_required')
    rec('a signed-in page that merely has no owners is not a stop',
        cb('Log in  My Account  Sign out\nNo one on file', '', 200, False) is None)
    rec('a page that parsed people is not a stop', cb('Log in', '', 200, True) is None)


class FakeResp:
    def __init__(self, status):
        self.status = status


class FakePage:
    def __init__(self, status=200, text='nothing', url='https://x/address/a'):
        self._s, self._t, self.url = status, text, url

    def goto(self, *a, **k):
        return FakeResp(self._s)

    def wait_for_selector(self, *a, **k):
        return None

    def inner_text(self, *_):
        return self._t


stop = getattr(W, '_Stop', None)
rec('_Stop exists', stop is not None)
if stop:
    try:
        W._scrape_one(FakePage(status=429), LEADS[0])
        got = None
    except stop as e:
        got = e.reason
    rec('_scrape_one raises on HTTP 429 (rate_limited)', got == 'rate_limited', got)

# ---- loop: runtime cap ------------------------------------------------------------------------
print('RUNTIME CAP')
clk = Clock()
calls = []


def slow_scrape(page, lead):
    calls.append(lead['Case #'])
    clk.t += 10
    return entry(), 'text'


W._scrape_one = slow_scrape
W._put = lambda case, e, cache: cache
loop = getattr(W, '_scrape_loop', None)
rec('_scrape_loop exists', loop is not None)
if loop:
    res = loop(None, LEADS, max_runtime=25, clock=clk, sleep=lambda s: None)
    rec('runtime cap stops between leads (3 of 5 done, none cut mid-lead)', calls == ['T-1', 'T-2', 'T-3'], calls)
    rec('reason is time_budget, counts are 3/3', tuple(res[:3]) == (3, 3, 'time_budget') or
        (res[0], res[1], res[2]) == (3, 3, 'time_budget'), res)

    # ---- loop: challenge stops everything ----
    print('STOP ON BLOCK')
    for reason in ('challenge', 'login_required', 'rate_limited'):
        calls[:] = []

        def blocking(page, lead, _r=reason):
            calls.append(lead['Case #'])
            if len(calls) == 2:
                raise W._Stop(_r, 'blocked page text')
            return entry(), 'text'

        W._scrape_one = blocking
        res = loop(None, LEADS, max_runtime=900, clock=Clock(), sleep=lambda s: None)
        rec('%s stops the run at once (no 3rd lead)' % reason, calls == ['T-1', 'T-2'] and res[2] == reason, (calls, res))
        rec('%s: 2 attempted, 1 cached' % reason, res[0] == 2 and res[1] == 1, res)

# ---- _run: status file + cooldown -------------------------------------------------------------
print('STATUS + COOLDOWN')


@contextlib.contextmanager
def fake_browser():
    yield object()


W._browser = fake_browser
clear_status()
W._scrape_one = lambda page, lead: (entry(), 'text')
W._run(LEADS[:2], refresh=True)
st = status() or {}
rec('ok run writes status reason ok, attempted 2, cached 2',
    st.get('reason') == 'ok' and st.get('attempted') == 2 and st.get('cached') == 2, st)
rec('status has a ts', isinstance(st.get('ts'), (int, float)))
rec('no cooldown after ok', not W.cooldown_active())


def block_now(page, lead):
    raise W._Stop('challenge', 'captcha page')


W._scrape_one = block_now
W._run(LEADS[:3], refresh=True)
st = status() or {}
rec('challenge run: reason challenge, attempted 1, cached 0',
    st.get('reason') == 'challenge' and st.get('attempted') == 1 and st.get('cached') == 0, st)
rec('challenge records a cooldown ~24h out',
    23 * 3600 < (st.get('cooldown_until', 0) - time.time()) <= 24 * 3600 + 5, st)
rec('cooldown_active is true', W.cooldown_active())

ran = []
W._run = lambda *a, **k: ran.append(1) or 'ok'
W._select = lambda args: LEADS[:2]
rc = W.main(['--pick-callable', '5'])
rec('cooldown blocks the next automated run (exit 0, no scrape)', rc == 0 and not ran, (rc, ran))
st2 = status() or {}
rec('the blocked attempt leaves the cooldown in place', st2.get('cooldown_until') == st.get('cooldown_until'), st2)
rc = W.main(['--pick-callable', '5', '--plan'])
rec('--plan is not blocked by the cooldown', rc == 0)
rc = W.main(['--pick-callable', '5', '--ignore-cooldown'])
rec('--ignore-cooldown lets a hand run through', ran == [1], ran)

# an expired cooldown lets the run through; rate_limited also sets one
json.dump({'ts': time.time() - 90000, 'reason': 'challenge', 'attempted': 1, 'cached': 0,
           'cooldown_until': time.time() - 100}, open(os.path.join(TD, 'wp_web_status.json'), 'w'))
rec('expired cooldown does not block', not W.cooldown_active())
real_run = None
clear_status()
W.write_status(1, 0, 'rate_limited')
rec('rate_limited sets a cooldown', W.cooldown_active())
clear_status()
W.write_status(1, 0, 'login_required')
rec('login_required sets no cooldown (one request next night is harmless)', not W.cooldown_active())

# ---- status holds counts only -----------------------------------------------------------------
print('PRIVACY')
raw = open(os.path.join(TD, 'wp_web_status.json'), encoding='utf-8').read()
ALLOWED = {'ts', 'reason', 'attempted', 'cached', 'cooldown_until'}
keys = set(json.loads(raw))
rec('status keys are only counts / reason / times', keys <= ALLOWED, keys)
rec('no name, phone or email in the status text',
    not any(t in raw for t in ('ZZTEST', '3055550142', 'example.com', '@')), raw)
rec('write_status refuses an unknown reason (falls back to error)',
    (W.write_status(1, 0, 'ZZTEST QUENTIN') or True) and (status() or {}).get('reason') == 'error')

# ---- cap --------------------------------------------------------------------------------------
print('CAP')
for val, want in ((None, 15), ('7', 7), ('25', 25), ('99', 25), ('0', 15), ('-3', 15), ('abc', 15)):
    if val is None:
        os.environ.pop('WP_WEB_NIGHTLY_MAX', None)
    else:
        os.environ['WP_WEB_NIGHTLY_MAX'] = val
    got = W.nightly_cap() if hasattr(W, 'nightly_cap') else None
    rec('WP_WEB_NIGHTLY_MAX=%s -> %s' % (val, want), got == want, got)
os.environ['WP_WEB_NIGHTLY_MAX'] = '500'
out = subprocess.run([sys.executable, os.path.join(HERE, 'wp_web.py'), '--nightly-cap'],
                     capture_output=True, text=True, timeout=60, env=dict(os.environ))
rec('wp_web.py --nightly-cap prints 25 for 500', out.stdout.strip() == '25', (out.stdout, out.stderr[-200:]))
os.environ.pop('WP_WEB_NIGHTLY_MAX', None)
for val, want in ((None, 900), ('120', 120), ('x', 900), ('0', 900)):
    if val is None:
        os.environ.pop('WP_WEB_MAX_RUNTIME_S', None)
    else:
        os.environ['WP_WEB_MAX_RUNTIME_S'] = val
    got = W.max_runtime_s() if hasattr(W, 'max_runtime_s') else None
    rec('WP_WEB_MAX_RUNTIME_S=%s -> %s' % (val, want), got == want, got)
os.environ.pop('WP_WEB_MAX_RUNTIME_S', None)

# ---- the .bat ---------------------------------------------------------------------------------
print('BAT')
bat = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='utf-8', errors='replace').read()
lines = bat.splitlines()
idx = [i for i, l in enumerate(lines) if 'wp_web.py' in l and not l.lstrip().lower().startswith('rem')]
rec('the bat calls wp_web.py', bool(idx))
if idx:
    first = idx[0]
    block = '\n'.join(lines[max(0, first - 12):first + 8])
    rec('guarded by WP_WEB_NIGHTLY=1', re.search(r'if\s+"%WP_WEB_NIGHTLY%"=="1"', block, re.I) is not None, block)
    rec('uses --pick-callable', any('--pick-callable' in lines[i] for i in idx))
    rec('logs with >> "%LOG%" 2>&1', all('>> "%LOG%" 2>&1' in lines[i] for i in idx if '--nightly-cap' not in lines[i]))
    rec('fail-soft: nonzero logs one line and continues',
        re.search(r'errorlevel\s+1\s+echo', '\n'.join(lines[first:first + 7]), re.I) is not None)
    rec('a progress marker precedes its echo label',
        any(re.match(r'\s*python\s+-u\s+refresh_progress\.py\s+step\s+\S+\s*$', lines[i]) for i in range(max(0, first - 12), first)))
    rebuild = [i for i, l in enumerate(lines) if 'F.make_tracker(' in l]
    rec('runs BEFORE the [4/5] rebuild', rebuild and first < rebuild[0], (first, rebuild))
    tail = '\n'.join(l for l in lines[first - 12:first + 8]
                     if not l.lstrip().lower().startswith('rem')).lower()
    rec('the step publishes / git-adds nothing', 'git ' not in tail and 'publish' not in tail.replace('publish_', 'x'))
for tool in ('healthcheck.py', 'publish_guard.py'):
    after = [i for i, l in enumerate(lines) if tool in l and not l.lstrip().lower().startswith('rem')]
    rec('%s still runs after the rebuild' % tool, idx and any(i > idx[0] for i in after))

# ---- pipeline_alerts --------------------------------------------------------------------------
print('ALERT')
import pipeline_alerts as PA            # noqa: E402

fn = getattr(PA, 'wp_web_alert', None)
rec('wp_web_alert exists', fn is not None)
if fn:
    at = '2026-11-02T06:00:00-05:00'
    fresh = {'readable': True, 'reason': 'ok', 'age_h': 3.0, 'attempted': 10, 'cached': 8}
    os.environ.pop('WP_WEB_NIGHTLY', None)
    rec('silent when the flag is off, even on a bad status',
        fn({'readable': True, 'reason': 'challenge', 'age_h': 99}, at) is None)
    os.environ['WP_WEB_NIGHTLY'] = '1'
    rec('silent on a fresh ok run', fn(fresh, at) is None)
    for r in ('login_required', 'challenge', 'rate_limited', 'error'):
        a = fn(dict(fresh, reason=r), at)
        rec('%s is a WARN (not fail)' % r, a is not None and a['severity'] == 'warn' and a['key'] == 'wp-web', a)
    a = fn(dict(fresh, age_h=40.0), at)
    rec('older than 36 h is a WARN', a is not None and a['severity'] == 'warn', a)
    rec('time_budget alone is not an alert', fn(dict(fresh, reason='time_budget'), at) is None)
    a = fn({'readable': False}, at)
    rec('flag on and no status file is a WARN', a is not None and a['severity'] == 'warn', a)
    a = fn(dict(fresh, reason='challenge'), at)
    rec('alert text carries no name / phone / email', not re.search(r'ZZTEST|\d{7}|@', a['text']), a)
    sigs = {'wp_web': dict(fresh, reason='challenge')}
    keys = [x['key'] for x in PA.alerts_from(sigs, __import__('datetime').datetime.now().astimezone())]
    rec('alerts_from includes wp-web', 'wp-web' in keys, keys)
    os.environ.pop('WP_WEB_NIGHTLY', None)

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
