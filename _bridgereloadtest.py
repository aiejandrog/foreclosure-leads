"""_bridgereloadtest -- the send bridge restarts itself on new code, only when that is safe.

Run:  python _bridgereloadtest.py   (exit 0 = pass; no network beyond 127.0.0.1, no real data)

WHY (2026-10-07). #172 and #173 merged, the 05:30 refresh pulled them, and the laptop's bridge,
started days earlier, kept answering /health with the old Quo text hold. Alejandro chose
"Self-restart". These checks pin the rules from the independent review on #175:
  - only a pulled commit that IS origin/main triggers it (not a branch, not a scratch file);
  - it waits while a request is open, after recent sends, while a first-touch slot is held in
    memory, and all day after a send whose dedupe lives only in memory;
  - code that does not import keeps the running bridge;
  - the old process is gone before the new one serves, and exactly one owns the port.

The live part runs a copy of the repo's .py files in a temp git repo on a spare port, so the real
folder's ledgers are never read or written.
"""
import datetime
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


# ---------------------------------------------------------------- unit: the trigger
class _Srv:
    def __init__(self):
        self.down = 0

    def shutdown(self):
        self.down += 1


def run_watch(seq, blocks=(), imports_ok=lambda: True, boot='A', pychange=lambda f, b, h: (['x.py'], False)):
    """Drive _code_watch over a list of (HEAD, origin/main) polls.
    Returns (reloaded head, polls used, shutdown calls, log text)."""
    it = iter(seq)
    used = []
    blk = list(blocks)

    def heads(_f):
        used.append(1)
        try:
            return next(it)
        except StopIteration:
            raise KeyboardInterrupt

    srv, state = _Srv(), {'reload': None}
    tmp = tempfile.mkdtemp(prefix='bridgereload_u_')
    try:
        try:
            S._code_watch(srv, state, {'head': boot}, poll_s=0, folder=tmp, sleep=lambda s: None,
                          heads=heads, imports=lambda f: (imports_ok(), 'boom'),
                          blocker=lambda: blk.pop(0) if blk else '', pychange=pychange)
        except KeyboardInterrupt:
            pass
        lp = os.path.join(tmp, 'send_server.log')
        log = open(lp, encoding='utf-8').read() if os.path.exists(lp) else ''
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return state['reload'], len(used), srv.down, log


r = run_watch([('A', 'A'), ('A', 'A')])
rec('no new commit: no restart', r[0] is None and r[2] == 0)
r = run_watch([('B', 'B'), ('B', 'B')])
rec('a pulled commit restarts once it stays put for one poll', r[0] == 'B' and r[1] == 2 and r[2] == 1, r)
r = run_watch([('C', 'A')] * 4)
rec('a branch checked out locally (HEAD is not origin/main) never restarts', r[0] is None, r)
r = run_watch([(None, None)] * 3)
rec('git unreadable: no restart', r[0] is None)
r = run_watch([('B', 'B')] * 5, blocks=['a request is being handled', 'a request is being handled'])
rec('a blocker makes it wait, logged once, then it restarts',
    r[0] == 'B' and r[1] == 4 and r[3].count('is waiting') == 1, r)
r = run_watch([('B', 'B')] * 4, imports_ok=lambda: False)
rec('code that does not import never restarts', r[0] is None and 'does not import' in r[3], r)
calls = {'n': 0}


def _imp():
    calls['n'] += 1
    return calls['n'] > 1


r = run_watch([('B', 'B')] * 3 + [('D', 'D')] * 2, imports_ok=_imp)
rec('a broken commit is skipped; the next good pull restarts', r[0] == 'D' and calls['n'] == 2, r)
r = run_watch([('B', 'B')] * 4, pychange=lambda f, b, h: ([], False))
rec('a pull that changed no .py file (a docs/ publish) does not restart', r[0] is None and r[2] == 0, r)
r = run_watch([('B', 'B')] * 4, pychange=lambda f, b, h: (['x.py'], True))
rec('uncommitted .py changes hold the restart', r[0] is None and 'uncommitted' in r[3], r)
r = run_watch([('B', 'B')] * 4, pychange=lambda f, b, h: (None, None))
rec('git unable to diff: no restart', r[0] is None)

# ---------------------------------------------------------------- unit: what blocks it
rec('the per-address send dedupe set is not shadowed by the request counter',
    isinstance(S._INFLIGHT, set) and isinstance(S._REQS_ACTIVE, int))
today = datetime.date.today().isoformat()
S._LAST_POST = 0.0
rec('nothing in memory: free to restart', S._reload_blocker() == '')
S._mark_memory_only()
rec('a send whose dedupe is memory-only blocks it for the life of the process',
    'memory' in S._reload_blocker() and 'memory' in S._memory_held())
S._MEMORY_ONLY = False
S._FT_SLOTS[(today, 'warm@wu.example')] = 1
rec('a first-touch slot held in memory blocks it', 'slot' in S._reload_blocker())
S._FT_SLOTS.clear()
S._LAST_POST = time.time()
rec('a recent POST blocks it', 'last' in S._reload_blocker())
S._LAST_POST = 0.0
src = open(os.path.join(HERE, 'send_server.py'), encoding='utf-8').read()
rec('both lost-outcome send paths mark the process memory-only',
    src.count('            _mark_memory_only()') == 2)
rec('after the drain, the whole handover is checked again before the port closes',
    'held = _handover_why(new)' in src)
H = lambda h, o: (lambda f: (h, o))
P = lambda d: (lambda f, b, hh: ([], d))
rec('handover goes ahead when nothing changed', S._handover_why('N', heads=H('N', 'N'), pychange=P(False),
                                                                blocker=lambda: '') == '')
rec('handover stops if a POST came in during the preflight or drain',
    'last' in S._handover_why('N', heads=H('N', 'N'), pychange=P(False),
                              blocker=lambda: 'sends were made in the last 300 seconds'))
rec('handover stops if the checkout moved after it was validated',
    'moved' in S._handover_why('N', heads=H('M', 'M'), pychange=P(False), blocker=lambda: '')
    and 'moved' in S._handover_why('N', heads=H('N', 'M'), pychange=P(False), blocker=lambda: ''))
rec('handover stops on uncommitted .py edits made after validation',
    'uncommitted' in S._handover_why('N', heads=H('N', 'N'), pychange=P(True), blocker=lambda: ''))
_t = tempfile.mkdtemp(prefix='bridgereload_c_')
try:
    open(os.path.join(_t, 'mail_guard.py'), 'w').write('def broken(:\n')
    ok, err = S._code_compiles(_t, ['mail_guard.py'])
    rec('a changed dependency that does not compile is caught', ok is False and 'mail_guard' in err, err)
finally:
    shutil.rmtree(_t, ignore_errors=True)
rec('the import preflight covers the lazily imported bridge dependencies',
    {'stay_gate', 'optout_sync', 'mail_guard', 'outreach_copy', 'pacer_stay', 'bk_lookup'} <= set(S._RELOAD_IMPORT))
rec('a half-sent request times out instead of stalling the drain', S.Handler.timeout == 60)
rec('the drain before exit has no time limit',
    'while _REQS_ACTIVE > 0:                  # no time limit' in src)


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
    """pids listening on port (Linux /proc only; None elsewhere)."""
    if not os.path.isdir('/proc/net'):
        return None
    inodes, hexport = set(), '%04X' % port
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


def git(work, *a):
    return subprocess.run(['git', '-c', 'user.email=t@example.com', '-c', 'user.name=t', '-c',
                           'commit.gpgsign=false'] + list(a), cwd=work, check=True, capture_output=True,
                          text=True).stdout.strip()


def pull(work, msg, fname, body, mode='a'):
    """A commit that origin/main also points at: what a pull of main leaves behind."""
    with open(os.path.join(work, fname), mode, encoding='utf-8') as fh:
        fh.write(body)
    git(work, 'add', '-A')
    git(work, 'commit', '-qm', msg)
    git(work, 'update-ref', 'refs/remotes/origin/main', 'HEAD')


def logtxt(work):
    p = os.path.join(work, 'send_server.log')
    return open(p, encoding='utf-8').read() if os.path.exists(p) else ''


work = tempfile.mkdtemp(prefix='bridgereload_live_')
proc, port, seen = None, None, set()
try:
    tracked = subprocess.run(['git', 'ls-files', '*.py'], cwd=HERE, capture_output=True, text=True).stdout.split()
    for n in tracked or [n for n in os.listdir(HERE) if n.endswith('.py') and not n.startswith('_')]:
        if '/' not in n:                                  # tracked code only, never scratch scripts
            shutil.copy(os.path.join(HERE, n), os.path.join(work, n))
    shutil.copy(os.path.join(HERE, 'send_server.py'), os.path.join(work, 'send_server.py'))
    good_text_hold = open(os.path.join(HERE, 'text_hold.py'), encoding='utf-8').read()
    git(work, 'init', '-q')
    git(work, 'add', '-A')
    git(work, 'commit', '-qm', 'base')
    base_branch = git(work, 'rev-parse', '--abbrev-ref', 'HEAD')
    git(work, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    port = free_port()
    env = dict(os.environ, DEALFLOW_BRIDGE_RELOAD_POLL_S='0.5', DEALFLOW_BRIDGE_RELOAD_IDLE_S='0')
    proc = subprocess.Popen([sys.executable, os.path.join(work, 'send_server.py'), '--port', str(port)],
                            cwd=work, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    seen.add(proc.pid)
    h1 = wait(lambda: health(port))
    rec('bridge answers /health with its pid', bool(h1 and h1.get('pid') == proc.pid), h1 and h1.get('pid'))

    # a scratch file and a local branch commit do not restart it
    open(os.path.join(work, '_scratch_probe.py'), 'w').write('X = 1\n')
    git(work, 'checkout', '-qb', 'feature')
    git(work, 'add', '-A')
    git(work, 'commit', '-qm', 'branch work')
    time.sleep(3)
    rec('a scratch file and a branch commit leave the bridge alone',
        proc.poll() is None and (health(port) or {}).get('pid') == proc.pid)
    git(work, 'checkout', '-q', base_branch)

    # a pull that does not import keeps the running bridge
    pull(work, 'broken', 'text_hold.py', '\nraise RuntimeError("broken pull")\n')
    wait(lambda: 'does not import' in logtxt(work), 30)
    rec('a pull that does not import keeps the running bridge',
        proc.poll() is None and (health(port) or {}).get('pid') == proc.pid
        and 'does not import' in logtxt(work), logtxt(work)[-300:])

    # an open request holds the restart; finishing it lets the good pull through
    hold = socket.create_connection(('127.0.0.1', port))
    hold.sendall(b'GET /health HTTP/1.1\r\n')            # request line sent, headers not finished
    time.sleep(0.5)
    pull(work, 'fixed', 'text_hold.py', good_text_hold + '\n# fixed\n', mode='w')
    wait(lambda: 'a request is being handled' in logtxt(work), 20)
    rec('an open request holds the restart',
        proc.poll() is None and 'a request is being handled' in logtxt(work), logtxt(work)[-300:])
    hold.sendall(b'Host: x\r\nConnection: close\r\n\r\n')
    try:
        hold.recv(65536)
    except Exception:
        pass
    hold.close()
    gone = wait(lambda: proc.poll() is not None, 30)
    rec('old bridge exits once the request is done', gone is not None)
    h2 = wait(lambda: (lambda h: h if h and h.get('pid') not in (None, proc.pid) else None)(health(port)))
    if h2:
        seen.add(h2.get('pid'))
    rec('a new bridge answers /health on the same port', bool(h2 and h2.get('ok')), h2 and h2.get('pid'))
    owners = listeners(port)
    if owners is None:
        print('SKIP exactly one process owns the port (needs Linux /proc)')
    else:
        rec('exactly one process owns the port, and it is the new one',
            owners == {h2.get('pid') if h2 else -1}, {'owners': owners, 'old': proc.pid})
    rec('the restart and the new bridge are logged',
        'restarting the bridge' in logtxt(work) and 'new bridge serving, pid' in logtxt(work),
        logtxt(work)[-300:])
finally:
    if proc and proc.poll() is None:
        proc.kill()
    h = health(port) if port else None
    if h and h.get('pid'):
        seen.add(h['pid'])
    for pid in seen:
        try:
            os.kill(pid, 9)
        except Exception:
            pass
    time.sleep(0.5)
    shutil.rmtree(work, ignore_errors=True)

print('\n%s -- %d failure(s)' % ('PASSED' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
