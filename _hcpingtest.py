"""_hcpingtest -- hc_ping.py, the optional dead-man's-switch pings, and their two lines in the nightly.

WHAT IT PINS
  * UNCONFIGURED IS A NO-OP: no env var and no healthcheck.url -> exit 0, no network call at all.
    Merging the file must change nothing on a machine that has not opted in.
  * CONFIGURED: start -> <url>/start?rid=<uuid>; exit N -> <url>/N with the SAME rid, so the
    service pairs them and measures duration; fail -> <url>/fail.
  * IT NEVER CHANGES THE RUN: every verb exits 0, including a dead network, an HTTP 500, a wrong
    URL, junk arguments and an unexpected exception.
  * NOTHING SENSITIVE LEAVES: the body carries rc / host / duration only, and the URL (the check's
    credential) is never printed in full.
  * THE .BAT WIRING: exactly one start ping after the killed-run flag and one exit ping with
    %RUNEXIT% after the healthcheck verdict, both top-level (never inside a parenthesised block,
    see _batsyntaxtest.py), and neither followed by an errorlevel test that would read them.

No network: urlopen is replaced by a recorder. Uses a temp copy of the module directory so the
rid file and healthcheck.url never touch this checkout.

    python _hcpingtest.py
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


FAKE = 'https://hc-ping.example/00000000-1111-4222-8333-444444444444'   # fake check, not a real UUID


def load(tmp):
    shutil.copy(HERE / 'hc_ping.py', tmp / 'hc_ping.py')
    spec = importlib.util.spec_from_file_location('hc_ping_under_test', tmp / 'hc_ping.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.BACKOFF = (0, 0, 0)        # no real sleeping in the suite
    return m


class Resp:
    def __init__(self, status=200):
        self.status = status
    def getcode(self):
        return self.status
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def recorder(behaviour=lambda req: Resp(200)):
    calls = []
    def opener(req, timeout=None):
        calls.append({'url': req.full_url, 'body': req.data.decode('utf-8'), 'method': req.get_method(),
                      'timeout': timeout})
        return behaviour(req)
    return calls, opener


def run(m, argv, opener):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = m.main(['hc_ping.py'] + argv, opener=opener)
    return rc, buf.getvalue()


with tempfile.TemporaryDirectory() as t:
    tmp = pathlib.Path(t)
    m = load(tmp)
    os.environ.pop(m.ENV, None)

    # ---- 1. unconfigured: no-op, no network
    calls, op = recorder()
    for verb in (['start'], ['exit', '0'], ['exit', '7'], ['fail', 'x']):
        rc, out = run(m, verb, op)
        rec('unconfigured %s exits 0' % verb[0], rc == 0)
    rec('unconfigured: not one network call', calls == [], calls)
    rec('unconfigured: no rid file written', not (tmp / 'hc-ping.rid').exists())
    rc, out = run(m, ['status'], op)
    rec('status says it is a no-op', 'no - pings are a no-op' in out, out.strip())

    # ---- 2. configured via env
    os.environ[m.ENV] = FAKE
    calls, op = recorder()
    rc, out = run(m, ['start'], op)
    rec('start exits 0', rc == 0)
    rec('start hits <url>/start?rid=<uuid>',
        len(calls) == 1 and re.fullmatch(re.escape(FAKE) + r'/start\?rid=[0-9a-f-]{36}', calls[0]['url']),
        calls and calls[0]['url'])
    rid = calls[0]['url'].split('rid=')[1] if calls else ''
    rec('start writes the rid file', (tmp / 'hc-ping.rid').read_text().split()[0] == rid)
    rec('pings carry a timeout', calls and calls[0]['timeout'] == m.TIMEOUT)
    calls.clear()
    rc, out = run(m, ['exit', '0'], op)
    rec('exit 0 hits <url>/0 with the SAME rid', calls and calls[0]['url'] == FAKE + '/0?rid=' + rid,
        calls and calls[0]['url'])
    calls.clear()
    rc, out = run(m, ['exit', '7'], op)
    rec('exit 7 (LP chain partial) reports failure code 7', calls and calls[0]['url'].startswith(FAKE + '/7?'))
    calls.clear()
    rc, out = run(m, ['exit', 'banana'], op)
    rec('an unreadable rc is reported as a failure (1), not success', calls and calls[0]['url'].startswith(FAKE + '/1?'))
    calls.clear()
    rc, out = run(m, ['exit', '999'], op)
    rec('rc is clamped to 0-255', calls and calls[0]['url'].startswith(FAKE + '/255?'))
    calls.clear()
    rc, out = run(m, ['fail', 'network', 'never', 'came', 'up'], op)
    rec('fail hits <url>/fail', calls and calls[0]['url'].startswith(FAKE + '/fail?'))
    rec('fail carries the reason', calls and 'network never came up' in calls[0]['body'])

    # ---- 3. nothing sensitive
    rec('the full ping URL is never printed', FAKE not in out and FAKE.split('/')[-1] not in out, out.strip())
    bodies = [c['body'] for c in calls]
    rec('body is rc/host/duration only (short, no JSON, no log)', all(len(b) < 200 for b in bodies), bodies)

    # ---- 4. never changes the run
    def down(req):
        raise urllib.error.URLError('getaddrinfo failed')
    calls, op = recorder(down)
    rc, out = run(m, ['exit', '0'], op)
    rec('network down: exit 0, retried 3 times', rc == 0 and len(calls) == 3, len(calls))
    rec('network down: one plain line in the log, URL redacted', 'did not land' in out and FAKE not in out, out.strip())
    def e500(req):
        raise urllib.error.HTTPError(req.full_url, 500, 'boom', {}, None)
    calls, op = recorder(e500)
    rc, out = run(m, ['start'], op)
    rec('HTTP 500: exit 0, retried', rc == 0 and len(calls) == 3)
    def e404(req):
        raise urllib.error.HTTPError(req.full_url, 404, 'nope', {}, None)
    calls, op = recorder(e404)
    rc, out = run(m, ['start'], op)
    rec('HTTP 404 (wrong check): exit 0, NOT retried', rc == 0 and len(calls) == 1)

    # ---- 5. a bad URL is refused, not pinged
    for badurl in ('http://hc-ping.com/abc', 'hc-ping.com/abc', 'https://', 'https://x/a b', 'javascript:alert(1)'):
        os.environ[m.ENV] = badurl
        calls, op = recorder()
        rc, out = run(m, ['start'], op)
        rec('refuses %r without a call' % badurl, rc == 0 and calls == [], out.strip()[:80])

    # ---- 6. the gitignored file fallback
    os.environ.pop(m.ENV, None)
    (tmp / 'healthcheck.url').write_text(FAKE + '\n')
    calls, op = recorder()
    rc, out = run(m, ['start'], op)
    rec('healthcheck.url is read when the env var is absent', calls and calls[0]['url'].startswith(FAKE + '/start'))

    # ---- 7. as a script: always exit 0, even on junk
    env = {**os.environ, m.ENV: 'https://127.0.0.1:9/nothing-listens-here'}
    p = subprocess.run([sys.executable, str(tmp / 'hc_ping.py'), 'exit', '3'], capture_output=True, text=True,
                       env=env, timeout=120, cwd=t)
    rec('script against a closed port exits 0', p.returncode == 0, (p.stdout + p.stderr).strip()[-120:])
    p = subprocess.run([sys.executable, str(tmp / 'hc_ping.py'), 'nonsense'], capture_output=True, text=True,
                       env=env, timeout=30, cwd=t)
    rec('script with a junk verb exits 0', p.returncode == 0)

# ---- 8. the .bat wiring
bat = (HERE / 'refresh-dealflow.bat').read_text(encoding='utf-8', errors='replace').splitlines()
starts = [i for i, l in enumerate(bat) if re.match(r'\s*python -u hc_ping\.py start\b', l)]
exits = [i for i, l in enumerate(bat) if re.match(r'\s*python -u hc_ping\.py exit %RUNEXIT%', l)]
rec('exactly one start ping in refresh-dealflow.bat', len(starts) == 1, starts)
rec('exactly one exit ping, and it reports %RUNEXIT%', len(exits) == 1, exits)
flag = next((i for i, l in enumerate(bat) if l.startswith('>refresh-running.flag echo')), None)
verdict = next((i for i, l in enumerate(bat) if 'health check complete' in l), None)
ended = next((i for i, l in enumerate(bat) if 'REFRESH ENDED rc=%RUNEXIT%' in l), None)
rec('start comes right after the killed-run flag is written', starts and flag is not None and 0 < starts[0] - flag <= 6)
rec('exit comes after the healthcheck verdict and before the ENDED line',
    exits and verdict is not None and ended is not None and verdict < exits[0] < ended)


def depth_at(lines, idx):
    d = 0
    for l in lines[:idx]:
        s = l.strip()
        if s.lower().startswith('rem') or s.startswith('::'):
            continue
        s = re.sub(r'\^.', '', s)
        s = re.sub(r'"[^"]*"', '', s)
        d += s.count('(') - s.count(')')
    return d


rec('both pings are top-level, not inside a ( ) block', all(depth_at(bat, i) == 0 for i in starts + exits),
    [depth_at(bat, i) for i in starts + exits])
nxt = [next((l for l in bat[i + 1:] if l.strip() and not l.strip().lower().startswith('rem')), '') for i in starts + exits]
rec('no errorlevel test reads a ping', all('errorlevel' not in l.lower() for l in nxt), nxt)
rec('hc-ping.rid and healthcheck.url are both gitignored',
    subprocess.run(['git', 'check-ignore', '-q', 'hc-ping.rid'], cwd=HERE).returncode == 0 and
    subprocess.run(['git', 'check-ignore', '-q', 'healthcheck.url'], cwd=HERE).returncode == 0)

print('\n==== %d/%d hc_ping checks passed ====' % (len(ok), len(ok) + len(bad)))
sys.exit(1 if bad else 0)
