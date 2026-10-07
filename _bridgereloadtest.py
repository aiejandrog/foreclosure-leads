"""_bridgereloadtest -- the send bridge restarts itself on new code, safely.

Run:  python _bridgereloadtest.py   (exit 0 = pass; no network beyond 127.0.0.1, no real data)

WHY (2026-10-07). #172 and #173 merged, the 05:30 refresh pulled them, and the laptop's bridge,
started days earlier, kept answering /health with the old Quo text hold. Alejandro chose
"Self-restart". These checks pin how it behaves: a settled change restarts it on the new code, a
change that does not compile keeps the running bridge, a file still being written waits a poll,
and the old process is gone before the new one serves, so two bridges never share the port.

The live part runs a copy of the repo's .py files in a temp folder on a spare port, so the real
folder's ledgers are never read or written.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import send_server as S  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra else ''))
    if not ok:
        fails.append(name)


# ---------------------------------------------------------------- unit
a = {'x.py': (1, 10)}
b = {'x.py': (2, 10)}
rec('no change: no reload', S._reload_due(a, a, a) is False)
rec('change still settling (differs from last poll): wait', S._reload_due(a, a, b) is False)
rec('settled change: reload', S._reload_due(a, b, b) is True)
rec('the per-address send dedupe set is not shadowed by the request counter',
    isinstance(S._INFLIGHT, set) and isinstance(S._REQS_ACTIVE, int))


class _Srv:
    def __init__(self):
        self.down = 0

    def shutdown(self):
        self.down += 1


tmp = tempfile.mkdtemp(prefix='bridgereload_')
try:
    for n in S._RELOAD_COMPILE:
        shutil.copy(os.path.join(HERE, n), os.path.join(tmp, n))
    polls = []

    def fake_sleep(_s, _steps=iter(range(10))):
        i = next(_steps)
        polls.append(i)
        if i == 1:   # a pull lands a broken text_hold.py
            with open(os.path.join(tmp, 'text_hold.py'), 'a', encoding='utf-8') as fh:
                fh.write('\ndef broken(:\n')
        if i == 4:   # the next pull fixes it
            shutil.copy(os.path.join(HERE, 'text_hold.py'), os.path.join(tmp, 'text_hold.py'))
            with open(os.path.join(tmp, 'text_hold.py'), 'a', encoding='utf-8') as fh:
                fh.write('\n# next pull\n')

    srv, state = _Srv(), {'reload': False}
    S._code_watch(srv, state, poll_s=0, folder=tmp, sleep=fake_sleep)
    rec('broken pull keeps the bridge, fixed pull restarts it once settled',
        srv.down == 1 and state['reload'] is True and polls[-1] == 5, polls)
    tlog = open(os.path.join(tmp, 'send_server.log'), encoding='utf-8').read()
    rec('the broken pull is logged once, then the restart',
        tlog.count('does not compile') == 1 and 'restarting the bridge' in tlog, tlog)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- live
def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def health(port):
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/health' % port, timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def wait(pred, t=40):
    end = time.time() + t
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.25)
    return None


def listeners(port):
    """pids listening on 127.0.0.1:port (Linux /proc only; None elsewhere)."""
    if not os.path.isdir('/proc/net'):
        return None
    inodes = set()
    hexport = '%04X' % port
    for f in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            for line in open(f).read().splitlines()[1:]:
                parts = line.split()
                if parts[1].endswith(':' + hexport) and parts[3] == '0A':
                    inodes.add(parts[9])
        except OSError:
            pass
    pids = set()
    for pid in filter(str.isdigit, os.listdir('/proc')):
        try:
            for fd in os.listdir('/proc/%s/fd' % pid):
                try:
                    tgt = os.readlink('/proc/%s/fd/%s' % (pid, fd))
                except OSError:
                    continue
                if tgt.startswith('socket:[') and tgt[8:-1] in inodes:
                    pids.add(int(pid))
        except OSError:
            pass
    return pids


work = tempfile.mkdtemp(prefix='bridgereload_live_')
proc = None
try:
    for n in os.listdir(HERE):
        if n.endswith('.py'):
            shutil.copy(os.path.join(HERE, n), os.path.join(work, n))
    port = free_port()
    env = dict(os.environ, DEALFLOW_BRIDGE_RELOAD_POLL_S='0.5')
    proc = subprocess.Popen([sys.executable, os.path.join(work, 'send_server.py'), '--port', str(port)],
                            cwd=work, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    h1 = wait(lambda: health(port))
    rec('bridge answers /health before the change', bool(h1 and h1.get('ok')))
    first = listeners(port)

    with open(os.path.join(work, 'zz_new_module.py'), 'w', encoding='utf-8') as fh:
        fh.write('X = 1\n')
    gone = wait(lambda: proc.poll() is not None, 20)
    rec('old bridge process exits after a settled code change', gone is not None)
    h2 = wait(lambda: health(port))
    rec('a new bridge answers /health on the same port', bool(h2 and h2.get('ok')))
    second = listeners(port)
    if first is not None:
        rec('exactly one process owns the port, and it is a new one',
            len(second or ()) == 1 and not (second & first), {'before': first, 'after': second})
    log = open(os.path.join(work, 'send_server.log'), encoding='utf-8').read() \
        if os.path.exists(os.path.join(work, 'send_server.log')) else ''
    rec('the restart is logged', '[reload] code changed on disk' in log, log[-200:])
finally:
    if proc and proc.poll() is None:
        proc.kill()
    try:
        for pid in (listeners(port) or ()):
            os.kill(pid, 9)
    except Exception:
        pass
    shutil.rmtree(work, ignore_errors=True)

print('\n%s -- %d failure(s)' % ('PASSED' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
