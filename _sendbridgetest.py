"""send_server.py — first test coverage (added 2026-08-02).

This file handles real Gmail credentials, real outbound mail, a 50/day cap and a 24h per-recipient
dedupe, and until now had ZERO tests. It is the last thing standing between a queue click and a
stranger's inbox, so its refusals matter more than its successes.

Runs the real server on a scratch port against a temp ledger, with SMTP monkeypatched so nothing
leaves the machine. Every assertion is about a REFUSAL or a LEDGER WRITE — the two things that
decide whether the worker's counters can be trusted.
"""
import json, os, pathlib, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request

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


def main():
    port = free_port()
    work = pathlib.Path(tempfile.mkdtemp(prefix='dfbridge_'))
    try:
        # Copy the server into a scratch dir so it reads a THROWAWAY gmail.key / mail_sent.json and
        # can never touch the real ones.
        shutil.copy(HERE / 'send_server.py', work / 'send_server.py')
        (work / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
        (work / 'sender.json').write_text(json.dumps({'name': 'Test Sender'}), encoding='utf-8')

        # Monkeypatch SMTP: capture instead of send. `--limit 3` makes the cap reachable in-test.
        shim = work / '_run_bridge.py'
        shim.write_text(
            'import sys, smtplib\n'
            'class _FakeSMTP:\n'
            '    def __init__(self, *a, **k): pass\n'
            '    def __enter__(self): return self\n'
            '    def __exit__(self, *a): return False\n'
            '    def login(self, u, p):\n'
            '        # A password of "BADPASS" simulates Gmail rejecting the App Password.\n'
            '        if p == "BADPASS":\n'
            '            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")\n'
            '    def send_message(self, m): return {}\n'
            'smtplib.SMTP_SSL = _FakeSMTP\n'
            'sys.argv = ["send_server.py", "--port", "%d", "--limit", "3"]\n'
            'exec(open("send_server.py", encoding="utf-8").read())\n' % port,
            encoding='utf-8')

        proc = subprocess.Popen([sys.executable, str(shim)], cwd=str(work),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # wait for bind
        up = False
        for _ in range(40):
            st, _j = call(port, '/health')
            if st == 200:
                up = True
                break
            time.sleep(0.25)
        rec('server starts and binds 127.0.0.1', up)
        if not up:
            raise SystemExit(1)

        # ---------- /health -------------------------------------------------------------
        st, h = call(port, '/health')
        rec('/health returns 200', st == 200, st)
        rec('/health reports ready with credentials present', h.get('ready') is True, h)
        rec('/health names the sending account', h.get('user') == 'tester@example.com', h.get('user'))
        rec('/health exposes cap + sent_today (the worker badge reads these)',
            'cap' in h and 'sent_today' in h, h)

        # ---------- happy path ----------------------------------------------------------
        st, j = call(port, '/send', {'to': 'a@example.com', 'subj': 'S1', 'body': 'B1',
                                     'meta': {'c': 'CASE-1', 'owner': 'A'}})
        rec('/send 200 on a valid payload', st == 200 and j.get('ok') is True, {'st': st, 'j': j})
        rec('/send returns a Message-ID the client can log',
            '@' in str(j.get('message_id', '')), j.get('message_id'))
        rec('/send increments sent_today', j.get('sent_today') == 1, j.get('sent_today'))

        led = json.loads((work / 'mail_sent.json').read_text(encoding='utf-8'))
        rec('a send writes exactly one ledger row', len(led) == 1, len(led))
        rec('ledger row carries channel, recipient and case',
            led[0].get('ch') == 'email' and led[0].get('to') == 'a@example.com'
            and led[0].get('case') == 'CASE-1', led[0] if led else None)
        rec('ledger row NEVER contains the password',
            'abcdabcdabcdabcd' not in json.dumps(led), 'credential leak check')

        # ---------- 24h dedupe ----------------------------------------------------------
        st, j = call(port, '/send', {'to': 'a@example.com', 'subj': 'S1b', 'body': 'B1b',
                                     'meta': {'c': 'CASE-1b'}})
        rec('same recipient inside 24h is refused 409', st == 409, {'st': st, 'j': j})
        rec('409 tells the client to SKIP rather than retry', j.get('skip') is True, j)
        led = json.loads((work / 'mail_sent.json').read_text(encoding='utf-8'))
        rec('a refused duplicate writes NO ledger row', len(led) == 1, len(led))

        # test:true bypasses the dedupe so smoke-testing to your own inbox stays possible
        st, j = call(port, '/send', {'to': 'a@example.com', 'subj': 'S', 'body': 'B',
                                     'meta': {'c': 'CASE-1c', 'test': True}})
        rec('meta.test bypasses the 24h dedupe (self smoke-tests)', st == 200, {'st': st, 'j': j})

        # ---------- validation ----------------------------------------------------------
        st, j = call(port, '/send', {'to': 'not-an-email', 'subj': 'S', 'body': 'B'})
        rec('malformed recipient refused 400', st == 400, {'st': st, 'j': j})
        st, j = call(port, '/send', {'to': 'c@example.com', 'subj': '', 'body': ''})
        rec('empty subject/body refused 400', st == 400, {'st': st, 'j': j})
        st, j = call(port, '/nope', {'x': 1})
        rec('unknown path refused 404', st == 404, st)

        # ---------- daily cap -----------------------------------------------------------
        # limit=3; two real sends so far (a@ and the test:true one). One more fills it.
        call(port, '/send', {'to': 'd@example.com', 'subj': 'S', 'body': 'B', 'meta': {'c': 'C-D'}})
        st, j = call(port, '/send', {'to': 'e@example.com', 'subj': 'S', 'body': 'B', 'meta': {'c': 'C-E'}})
        rec('cap refuses with 429 once the daily limit is reached', st == 429, {'st': st, 'j': j})
        rec('429 reports the count and the cap so the UI can explain itself',
            'sent_today' in j and 'cap' in j, j)
        led = json.loads((work / 'mail_sent.json').read_text(encoding='utf-8'))
        rec('a capped request writes NO ledger row', len(led) == 3, len(led))
        proc.terminate(); proc.wait(timeout=10)

        # ---------- auth failure --------------------------------------------------------
        # Restart with a password the SMTP shim rejects; a 401 must be distinguishable from a 502
        # because the worker PAUSES the run on 401 and skips the lead on 502.
        (work / 'gmail.key').write_text('tester@example.com:BADPASS\n', encoding='utf-8')
        (work / 'mail_sent.json').unlink(missing_ok=True)
        port2 = free_port()
        shim2 = work / '_run_bridge2.py'
        shim2.write_text(shim.read_text(encoding='utf-8')
                         .replace(f'"{port}"', f'"{port2}"'), encoding='utf-8')
        proc2 = subprocess.Popen([sys.executable, str(shim2)], cwd=str(work),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            if call(port2, '/health')[0] == 200:
                break
            time.sleep(0.25)
        st, j = call(port2, '/send', {'to': 'f@example.com', 'subj': 'S', 'body': 'B',
                                      'meta': {'c': 'C-F'}})
        rec('bad App Password returns 401, not a generic 500', st == 401, {'st': st, 'j': j})
        rec('401 explains it was an auth failure', 'AUTH' in str(j.get('err', '')).upper(), j)
        proc2.terminate(); proc2.wait(timeout=10)

    finally:
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass

    total = len(ok) + len(bad)
    print(f'\n==== {len(ok)}/{total} send-bridge checks passed ====')
    return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(main())
