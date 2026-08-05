"""send_server.py — ledger-write resilience (added 2026-08-05).

Root cause traced live in send_server_run.log: a real, already-delivered email hit a transient
Windows PermissionError (WinError 5) on the ledger's os.replace() call. That happens AFTER the SMTP
send has already succeeded, and the old code had no try/except around it — the exception escaped
do_POST entirely, so the worker's fetch() never got a response. The worker's own bridge-offline
handler (added a few commits earlier, precisely to stop composer popups during an unattended run)
then read that as "the bridge just died" and paused the whole auto-run — even though the email had
gone out fine and the bridge was completely healthy. One logging hiccup masqueraded as an outage.

Two scenarios, run against two separate server instances (each needs a FIXED os.replace behavior
for its whole process lifetime, so this can't share one server the way _sendbridgetest.py does):

  A. TRANSIENT failure (os.replace throws twice, then succeeds) -> the retry inside _append_ledger
     must absorb it silently: 200, a real message_id, exactly one ledger row, no warning surfaced.
  B. PERSISTENT failure (os.replace always throws) -> the client must still see 200 with the real
     message_id (a delivered email must never be reported as failed), a ledger_warn must be present
     so the operator can find it, and the server process must still be alive afterward (proving the
     exception stayed contained to one request thread, not the whole process).
"""
import json, pathlib, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
ok, bad = [], []
def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, path, payload=None, timeout=8):
    url = f'http://127.0.0.1:{port}{path}'
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/json'} if data else {},
        method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {'err': str(e)}


def run_scenario(label, fail_mode):
    """fail_mode: 'transient' (fails twice then succeeds) or 'persistent' (always fails)."""
    port = free_port()
    work = pathlib.Path(tempfile.mkdtemp(prefix='dfledger_'))
    proc = None
    try:
        shutil.copy(HERE / 'send_server.py', work / 'send_server.py')
        (work / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
        (work / 'sender.json').write_text(json.dumps({'name': 'Test Sender'}), encoding='utf-8')

        fails = '2' if fail_mode == 'transient' else '999999'
        shim = work / '_run_bridge.py'
        shim.write_text(
            'import sys, smtplib, os\n'
            'class _FakeSMTP:\n'
            '    def __init__(self, *a, **k): pass\n'
            '    def __enter__(self): return self\n'
            '    def __exit__(self, *a): return False\n'
            '    def login(self, u, p): pass\n'
            '    def send_message(self, m): return {}\n'
            'smtplib.SMTP_SSL = _FakeSMTP\n'
            '_real_replace = os.replace\n'
            '_state = {"left": %s}\n'
            'def _flaky_replace(src, dst):\n'
            '    if _state["left"] > 0 and str(dst).endswith("mail_sent.json"):\n'
            '        _state["left"] -= 1\n'
            '        raise PermissionError(5, "Access is denied (test-injected)")\n'
            '    return _real_replace(src, dst)\n'
            'os.replace = _flaky_replace\n'
            'sys.argv = ["send_server.py", "--port", "%d", "--limit", "10"]\n'
            'exec(open("send_server.py", encoding="utf-8").read())\n' % (fails, port),
            encoding='utf-8')

        proc = subprocess.Popen([sys.executable, str(shim)], cwd=str(work),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        up = False
        for _ in range(40):
            st, _j = call(port, '/health')
            if st == 200:
                up = True
                break
            time.sleep(0.25)
        rec(f'[{label}] server starts', up)
        if not up:
            return

        st, j = call(port, '/send', {'to': 'a@example.com', 'subj': 'S1', 'body': 'B1',
                                     'meta': {'c': 'CASE-1', 'owner': 'A'}})
        rec(f'[{label}] a delivered email is reported as 200, never as a broken connection',
            st == 200, {'st': st, 'j': j})
        rec(f'[{label}] the real message_id still comes back',
            '@' in str(j.get('message_id', '')), j.get('message_id'))

        led_path = work / 'mail_sent.json'
        led = json.loads(led_path.read_text(encoding='utf-8')) if led_path.exists() else []

        if fail_mode == 'transient':
            rec(f'[{label}] retry absorbed it — exactly one ledger row written',
                len(led) == 1, len(led))
            rec(f'[{label}] no ledger_warn surfaced for a transient hiccup the retry fixed',
                'ledger_warn' not in j, j.get('ledger_warn'))
        else:
            rec(f'[{label}] persistent failure still returns ledger_warn so it is findable',
                bool(j.get('ledger_warn')), j.get('ledger_warn'))
            rec(f'[{label}] known accepted gap: unrecorded send writes NO ledger row',
                len(led) == 0, len(led))
            # the whole point of the fix: one bad request thread must not take the server down
            st2, h2 = call(port, '/health')
            rec(f'[{label}] server survives — still answers /health after the failure',
                st2 == 200 and h2.get('ready') is True, {'st': st2, 'h': h2})
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        shutil.rmtree(work, ignore_errors=True)


def main():
    run_scenario('transient x2', 'transient')
    run_scenario('persistent', 'persistent')
    print(f"\n==== {len(ok)}/{len(ok)+len(bad)} ledger-write resilience checks passed ====")
    if bad:
        print('FAILED:', bad)
        sys.exit(1)


if __name__ == '__main__':
    main()
