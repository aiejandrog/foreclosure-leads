"""runner_lock.py - ONE runner at a time ACROSS MACHINES: a lease both the laptop and the desktop see.

WHY THIS EXISTS
MACHINE-HANDOFF.md section 1 allows exactly one armed machine, and until now that rule was enforced
by nothing but a person remembering it. #45's publish_lock.py is a real lock, but it is a file on
ONE disk: it stops two runners on the same box and says nothing about the other box - its own
header says "Do not read a green lock as 'nobody else is publishing'". The cloud watchdog (issue
#28) has recorded the cost more than once: the same scheduled job publishing twice on one day from
two machines, forked worker state, and whichever push landed last winning the live board.

WHERE THE LOCK LIVES
One git ref, `refs/heads/dealflow-runner-lock`, in the PRIVATE ledgers repo both machines already
authenticate to for ledger_sync.py (`aiejandrog/dealflow-ledgers-private`). No new service, no new
credential, nothing paid. The lock is an orphan commit whose message carries who holds it:

    dealflow-runner-lock v1
    {"host": "...", "runner": "refresh-dealflow.bat", "pid": 1234, "token": "<uuid4>",
     "acquired_at": 1790000000, "expires_at": 1790021600, "renewed_at": 1790000000}

Host name, runner name, pid and times - no lead data. It still goes to the private repo, not this
public one, so a lock commit can never become one more thing on a public remote to reason about.

WHY A GIT REF IS A REAL LOCK
Every change is a compare-and-swap performed by the server: `git push --force-with-lease=REF:EXPECT`
sends EXPECT as the ref's old value, and the server's receive-pack applies the update only if the
ref still holds exactly that value, under its own ref lock. EXPECT empty means "must not exist".
So of any number of machines racing for a free lock, exactly one push succeeds and every other is
rejected as "stale info" - _runnerlocktest.py races eight processes from two simulated machines to
prove it, and races six more for the same EXPIRED lock to prove a stale break is atomic too.

STALE LOCKS
A lease expires (`expires_at`). A runner killed mid-flight - the laptop sleeping at 06:53 on 09-25,
a reboot, a Task Scheduler kill - cannot release, so an expired lease is broken by the next
acquirer with the same compare-and-swap, and the break is logged loudly. Clock skew between the two
PCs is absorbed by SKEW seconds of slack. Two ways to hold it:
  * `acquire` / `release` (CLI, no heartbeat): lease = --ttl, default 6h, the longest
    ExecutionTimeLimit any DEALFLOW task carries (same budget as #45).
  * `run -- <command>` (wrapper, recommended): lease = 30 min, renewed every 10 min while the command
    runs. A dead run frees the lock in 30 minutes instead of six hours. If the lease is lost - a
    renewal finds another holder, or the machine slept past the lease - the wrapper KILLS the
    command (exit 10) rather than let it publish over the other machine. That is the fail-safe
    direction: a killed run costs one night; two publishers cost the board and its baseline.

FAIL DIRECTION: CLOSED
If the lock cannot be read or written (no network, auth, GitHub down), acquire REFUSES with rc=9 and
says it is UNUSABLE, not HELD - different events, same safe outcome (#45's convention, kept so the
two can coexist in the .bat files without a new code). Nothing is built or pushed. release/status
always exit 0: releasing is the last thing a run does and must never turn a good run into a failed
one. release only ever deletes a lock whose token matches the one this machine took.

EXIT CODES
    acquire  0 = yours | 9 = NOT acquired (held elsewhere, or the lock is unusable - the log says which)
    renew    0 = extended | 10 = LOST (someone else holds it, or it is gone) | 9 = could not reach it
    check    0 = this machine holds a live lease | 9 = it does not (fencing: call before a push)
    release  0 always
    status   0 always
    break    0 = no lock now | 9 = refused (live lease and no --force, or unusable)
    run      the command's own exit code | 9 = lock not acquired, command never started
             | 10 = lease lost mid-run, command killed
             With DEALFLOW_RUNNER_LOCK off, `run` does not touch the lease and returns the
             command's own exit code. With it set to a value this file does not understand, rc=9.

    python runner_lock.py status
    python runner_lock.py run --runner refresh-dealflow.bat -- cmd /c refresh-dealflow.bat
    python runner_lock.py acquire --runner run-leads.bat [--ttl 21600]
    python runner_lock.py release
    python runner_lock.py break [--force]

THE GATE — OFF UNLESS A MACHINE TURNS IT ON
DEALFLOW_RUNNER_LOCK defaults to off (unset, empty, 0, off, false, no). That is deliberate.
Once the gate is on, a lease that cannot be read is a refusal: the 05:30 task does no work.
The same class of fault — GitHub or DNS not up yet — already ate three mornings (09-14/16/17),
and the private-repo credential this lock reuses is the one ledger_sync.py already has, which
a scheduled task may not see until somebody confirms it. Shipping the refusal on, the morning
the code lands, would make the rollout the outage. The laptop is the only armed machine today
(MACHINE-HANDOFF.md section 1); turning the guard on is one environment variable on each box
that might run a task, not a default. An unrecognised value (a typo) is NOT off: it refuses,
because a var that was set and not understood must not be treated as "protection is on" while
the run proceeds anyway.

ARMED MACHINE
With the gate on, a runner that SENDS mail or SPENDS money also has to be the configured
machine. DEALFLOW_ARMED_MACHINE is compared to this box's declared identity: DEALFLOW_ENGINE_ID
if set, otherwise the gitignored engine.id file (the same marker engine_drift.py uses — declared,
not sniffed from the hostname). If those two disagree, or the identity cannot be read, or the
armed name is unset, the runner does not start and does not take the lease. A publish-only
runner (the reply bake) still takes the lease, so two machines cannot publish it at once, but
it is not an armed-machine check. Send and spend are: refresh-dealflow.bat, run-leads.bat,
run-phones.bat, run-phones-nightly.bat, cadence-daily.bat, cadence-run.bat.

WHAT THE DESKTOP DOES IF IT COMES BACK
Tasks that are still Disabled do nothing. A desktop whose tasks are enabled and whose
DEALFLOW_RUNNER_LOCK is unset runs, because the gate is off there — setting the var on the
laptop does not reach the other box. Set the same two variables on the desktop too: the lease
then serialises the two machines, and send/spend runners refuse because that box's engine.id
is not the armed one. Copying the laptop's engine.id onto the desktop is the handoff in section
1, and it is the one case where the desktop is allowed to send — after which it contends for
the lease like any other holder.

CONFIGURATION (env, all optional; the tests use them to simulate two machines)
    DEALFLOW_RUNNER_LOCK     1/on/true/yes arms the lease; unset or 0/off/false/no leaves it off
    DEALFLOW_ARMED_MACHINE   engine id allowed to send or spend, required once the gate is on
    DEALFLOW_ENGINE_ID       this machine's declared id (overrides engine.id when it agrees)
    DEALFLOW_ENGINE_ID_FILE  identity file (default: engine.id next to this script; tests)
    DEALFLOW_LOCK_REMOTE     the repo URL or path holding the ref  (default: ledger_sync.REPO_URL)
    DEALFLOW_LOCK_GITDIR     local scratch bare repo                (default: ~/DEALFLOW/runner-lock.git)
    DEALFLOW_LOCK_HOST       this machine's name in the lock        (default: socket.gethostname())
    DEALFLOW_LOCK_SKEW       seconds of clock-skew slack            (default: 120)
    DEALFLOW_LOCK_INNER      set by this file on the child it starts; never set it yourself
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REF = 'refs/heads/dealflow-runner-lock'
MAGIC = 'dealflow-runner-lock v1'
try:                                   # one place for the private repo's address
    from ledger_sync import REPO_URL as _LEDGERS_URL
except Exception:                      # pragma: no cover - ledger_sync always ships with this file
    _LEDGERS_URL = 'https://github.com/aiejandrog/dealflow-ledgers-private.git'

CLI_TTL = 6 * 60 * 60                  # acquire/release without a heartbeat
RUN_TTL = 30 * 60                      # the `run` wrapper's lease...
RUN_RENEW = 10 * 60                    # ...renewed this often
GIT_TIMEOUT = 60
OK, NOT_ACQUIRED, LOST = 0, 9, 10
# Runners that mail a homeowner or spend money. Anything else (the reply bake) takes the lease
# when the gate is on, and does not have to be the armed machine.
SEND_OR_PAID = frozenset({
    'refresh-dealflow.bat',       # skip-trace, 2Captcha, PACER
    'run-leads.bat',              # the manual twin of the refresh
    'run-phones.bat',
    'run-phones-nightly.bat',     # skip-trace
    'cadence-daily.bat',          # unattended homeowner email
    'cadence-run.bat',
})
_ON = ('1', 'on', 'true', 'yes')
_OFF = ('0', 'off', 'false', 'no')


class Unusable(Exception):
    """The lock could not be read or written. Never treated as 'free'."""


def _say(msg):
    print('[runner-lock] ' + msg, flush=True)


def remote():
    return os.environ.get('DEALFLOW_LOCK_REMOTE') or _LEDGERS_URL


def gitdir():
    return os.environ.get('DEALFLOW_LOCK_GITDIR') or os.path.join(
        os.path.expanduser('~'), 'DEALFLOW', 'runner-lock.git')


def host():
    return os.environ.get('DEALFLOW_LOCK_HOST') or socket.gethostname()


def skew():
    try:
        return float(os.environ.get('DEALFLOW_LOCK_SKEW', '120'))
    except ValueError:
        return 120.0


def lock_mode():
    """'on' | 'off' | 'bad'. Unset or blank is off. A value this file does not know is bad,
    which the caller refuses — a typo must not look like the guard is armed while the run goes."""
    if 'DEALFLOW_RUNNER_LOCK' not in os.environ:
        return 'off'
    raw = os.environ.get('DEALFLOW_RUNNER_LOCK', '').strip().lower()
    if raw == '' or raw in _OFF:
        return 'off'
    if raw in _ON:
        return 'on'
    return 'bad'


def _runner_key(runner):
    return os.path.basename(runner or '').strip().lower()


def sends_or_spends(runner):
    return _runner_key(runner) in SEND_OR_PAID


def _read_identity():
    """This machine's declared id, lowercased, or None when nothing declares one.
    DEALFLOW_ENGINE_ID and engine.id must agree when both are present. Unreadable is Unusable,
    never a guess from the hostname."""
    env = (os.environ.get('DEALFLOW_ENGINE_ID') or '').strip().lower()
    path = os.environ.get('DEALFLOW_ENGINE_ID_FILE') or os.path.join(HERE, 'engine.id')
    file_id = None
    if os.path.exists(path):
        try:
            file_id = open(path, encoding='utf-8').read().strip().lower() or None
        except OSError as e:
            raise Unusable('engine.id unreadable (%s): %s' % (path, e))
    if env and file_id and env != file_id:
        raise Unusable('DEALFLOW_ENGINE_ID=%s disagrees with engine.id (%s) — refusing to guess' % (env, file_id))
    return env or file_id


def _child_env():
    env = os.environ.copy()
    # The child bat sees this and does the work. A value the operator might have typed
    # ("1", "yes") does not match, so a stale user variable cannot skip the lease.
    env['DEALFLOW_LOCK_INNER'] = 'held-%d' % os.getpid()
    return env


def assert_armed(runner):
    """None when this runner may start. NOT_ACQUIRED when it may not, already logged.
    Publish-only runners are not checked. Called only with the gate on, and before acquire,
    so a machine that is not armed does not take a lease it is about to refuse."""
    if not sends_or_spends(runner):
        return None
    want = (os.environ.get('DEALFLOW_ARMED_MACHINE') or '').strip().lower()
    if not want:
        _say('NOT ACQUIRED - %s sends mail or spends money, and DEALFLOW_ARMED_MACHINE is unset. '
             'Refusing (fail-closed). Set it to this machine\'s engine.id (the armed laptop\'s is "laptop").'
             % runner)
        return NOT_ACQUIRED
    try:
        got = _read_identity()
    except Unusable as e:
        _say('NOT ACQUIRED - cannot read this machine\'s identity (%s). %s did not start.' % (e, runner))
        return NOT_ACQUIRED
    if not got:
        _say('NOT ACQUIRED - engine.id is missing and DEALFLOW_ENGINE_ID is unset, so this machine '
             'cannot prove it is "%s". Refusing to run %s. Nothing sent, nothing spent.' % (want, runner))
        return NOT_ACQUIRED
    if got != want:
        _say('NOT ACQUIRED - this machine is "%s", and only "%s" may run %s. '
             'Nothing sent, nothing spent, lease not taken.' % (got, want, runner))
        return NOT_ACQUIRED
    _say('armed-machine check: this machine is "%s", allowed to run %s.' % (got, runner))
    return None


def mine_path():
    return os.path.join(gitdir(), 'dealflow-mine.json')


def _git(*args, check=True):
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0',       # a scheduled task cannot answer a prompt
           'GIT_AUTHOR_NAME': 'dealflow-runner-lock', 'GIT_AUTHOR_EMAIL': 'runner-lock@localhost',
           'GIT_COMMITTER_NAME': 'dealflow-runner-lock', 'GIT_COMMITTER_EMAIL': 'runner-lock@localhost'}
    try:
        p = subprocess.run(['git', '--git-dir', gitdir()] + list(args), capture_output=True, text=True,
                           env=env, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unusable('git %s: %s' % (args[0], e))
    if check and p.returncode != 0:
        raise Unusable('git %s rc=%d: %s' % (args[0], p.returncode, (p.stderr or p.stdout).strip()[-300:]))
    return p


def _ensure_gitdir():
    d = gitdir()
    if not os.path.isdir(os.path.join(d, 'objects')):
        os.makedirs(d, exist_ok=True)
        try:
            subprocess.run(['git', 'init', '-q', '--bare', d], check=True, capture_output=True, timeout=GIT_TIMEOUT)
        except Exception as e:
            raise Unusable('cannot create the local scratch repo %s: %s' % (d, e))


def read_remote():
    """(sha, payload_dict_or_None) of the live lock, or (None, None) when there is none.
    payload None with a sha = the ref exists but is not a lock this file wrote: treated as HELD."""
    _ensure_gitdir()
    p = _git('ls-remote', remote(), REF)
    line = next((l for l in p.stdout.splitlines() if l.strip().endswith(REF)), '')
    if not line:
        return None, None
    sha = line.split()[0]
    _git('fetch', '-q', '--no-tags', remote(), REF)
    body = _git('cat-file', 'commit', sha).stdout
    msg = body.split('\n\n', 1)[1] if '\n\n' in body else ''
    ctime = None
    for l in body.splitlines():
        if l.startswith('committer '):
            try:
                ctime = int(l.split()[-2])
            except (ValueError, IndexError):
                pass
    payload = None
    msg = msg.strip()
    if msg.startswith(MAGIC):
        try:
            payload = json.loads(msg[len(MAGIC):].strip())
            if not isinstance(payload, dict) or 'token' not in payload or 'expires_at' not in payload:
                payload = None
        except ValueError:
            payload = None
    if payload is None:
        return sha, {'_unparseable': True, '_ctime': ctime}
    return sha, payload


def _commit(payload):
    # an empty tree: `git mktree` with no input writes it into this scratch repo's object store
    try:
        p = subprocess.run(['git', '--git-dir', gitdir(), 'mktree'], input='', capture_output=True, text=True,
                           timeout=GIT_TIMEOUT)
        tree = p.stdout.strip()
    except Exception as e:
        raise Unusable('mktree: %s' % e)
    if p.returncode != 0 or not tree:
        raise Unusable('mktree rc=%d' % p.returncode)
    msg = MAGIC + '\n' + json.dumps(payload, sort_keys=True)
    return _git('commit-tree', tree, '-m', msg).stdout.strip()


def _cas(new_sha, expect_sha):
    """Server-side compare-and-swap. new_sha None = delete. expect_sha None = must not exist.
    True = applied. False = the ref was not what we expected (lost a race). Unusable = cannot tell."""
    lease = '--force-with-lease=%s:%s' % (REF, expect_sha or '')
    spec = (new_sha + ':' + REF) if new_sha else (':' + REF)
    p = _git('push', '--porcelain', remote(), lease, spec, check=False)
    if p.returncode == 0:
        return True
    out = (p.stdout or '') + (p.stderr or '')
    if 'stale info' in out or 'rejected' in out or 'failed to lock' in out or 'cannot lock ref' in out:
        return False
    raise Unusable('push rc=%d: %s' % (p.returncode, out.strip()[-300:]))


def _age(sec):
    sec = int(max(0, sec))
    return '%dh%02dm' % (sec // 3600, (sec % 3600) // 60) if sec >= 3600 else '%dm%02ds' % (sec // 60, sec % 60)


def describe(payload, now=None):
    now = now or time.time()
    if not payload:
        return '(nobody)'
    if payload.get('_unparseable'):
        return 'an UNREADABLE lock commit (not written by this tool)'
    left = payload['expires_at'] - now
    return '%s on %s, pid %s, since %s (%s ago), lease %s' % (
        payload.get('runner', '?'), payload.get('host', '?'), payload.get('pid', '?'),
        time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(payload.get('acquired_at', 0))),
        _age(now - payload.get('acquired_at', now)),
        ('expires in ' + _age(left)) if left > 0 else ('EXPIRED ' + _age(-left) + ' ago'))


def _expired(payload, now):
    if payload.get('_unparseable'):
        # Unknown content: only an old one is presumed dead, and only by the longest budget.
        c = payload.get('_ctime')
        return c is not None and now > c + CLI_TTL + skew()
    return now > float(payload['expires_at']) + skew()


def _write_mine(d):
    try:
        with open(mine_path(), 'w', encoding='utf-8') as f:
            json.dump(d, f)
    except OSError as e:
        raise Unusable('cannot record the lease locally (%s): %s' % (mine_path(), e))


def _read_mine():
    try:
        with open(mine_path(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _clear_mine():
    try:
        os.remove(mine_path())
    except OSError:
        pass


def acquire(runner, ttl=CLI_TTL, pid=None, attempts=3):
    """Returns (rc, lease). lease = {'sha','token',...} on success."""
    for _ in range(attempts):
        try:
            sha, cur = read_remote()
            now = time.time()
            expect = None
            if sha:
                if not _expired(cur, now):
                    _say('NOT ACQUIRED - HELD by %s.' % describe(cur, now))
                    _say('%s is refusing to start: nothing built, nothing pushed, live site untouched.' % runner)
                    _say('This is the cross-machine guard working. If that runner is really dead and its lease '
                         'is still live, clear it with: python runner_lock.py break --force')
                    return NOT_ACQUIRED, None
                _say('BROKE A STALE LOCK - held by %s.' % describe(cur, now))
                _say('Its lease ran out, so the run that took it is dead (killed, slept, rebooted), not working. '
                     'If this line appears every morning, a runner is dying mid-flight - find that.')
                expect = sha
            token = str(uuid.uuid4())
            try:
                engine = _read_identity() or ''
            except Unusable:
                engine = ''
            payload = {'host': host(), 'runner': runner, 'pid': pid or os.getppid(), 'token': token,
                       'engine': engine, 'acquired_at': int(now), 'renewed_at': int(now),
                       'expires_at': int(now + ttl)}
            new = _commit(payload)
            if _cas(new, expect):
                lease = dict(payload, sha=new)
                _write_mine(lease)
                _say('lock taken by %s on %s (lease %s).' % (runner, host(), _age(ttl)))
                return OK, lease
            # lost the race: loop, re-read, and report whoever won
        except Unusable as e:
            _say('NOT ACQUIRED - the lock is UNUSABLE, this is not another runner holding it: %s' % e)
            _say('%s is refusing to start (fail-closed). Check the network and git access to %s.'
                 % (runner, _redact(remote())))
            return NOT_ACQUIRED, None
    _say('NOT ACQUIRED - lost the race %d times in a row; another machine is taking it right now.' % attempts)
    return NOT_ACQUIRED, None


def _redact(url):
    return url.split('@')[-1] if '://' in url else url


def renew(lease, ttl):
    """Extend a lease this process holds. Returns (rc, lease)."""
    try:
        sha, cur = read_remote()
        if not sha or sha != lease['sha'] or (cur or {}).get('token') != lease['token']:
            _say('LEASE LOST - the lock is now %s.' % (describe(cur) if sha else 'gone'))
            return LOST, lease
        now = time.time()
        payload = {k: v for k, v in lease.items() if k != 'sha'}
        payload.update(renewed_at=int(now), expires_at=int(now + ttl))
        new = _commit(payload)
        if _cas(new, lease['sha']):
            lease = dict(payload, sha=new)
            _write_mine(lease)
            return OK, lease
        _say('LEASE LOST - the lock changed under us during renewal.')
        return LOST, lease
    except Unusable as e:
        _say('could not renew (will retry while the lease lasts): %s' % e)
        return NOT_ACQUIRED, lease


def release(lease=None):
    lease = lease or _read_mine()
    if not lease:
        _say('release: this machine holds no lease - nothing to do.')
        return OK
    try:
        sha, cur = read_remote()
        if not sha:
            _say('release: the lock is already gone.')
        elif (cur or {}).get('token') != lease.get('token'):
            _say('release: the lock is held by %s, not by this lease - leaving it alone.' % describe(cur))
        elif _cas(None, sha):
            held = time.time() - lease.get('acquired_at', time.time())
            _say('lock released by %s on %s after %s.' % (lease.get('runner'), host(), _age(held)))
        else:
            _say('release: the lock changed while releasing - leaving it alone.')
    except Unusable as e:
        _say('release could not reach the lock (%s); the lease expires on its own.' % e)
    _clear_mine()
    return OK


def check(lease=None):
    lease = lease or _read_mine()
    try:
        sha, cur = read_remote()
    except Unusable as e:
        _say('check: UNUSABLE (%s) - treat as NOT held.' % e)
        return NOT_ACQUIRED
    # Fencing is judged STRICTLY: the lease must still have SKEW seconds left, so a machine whose
    # clock runs behind the other's cannot believe it holds a lease the other already broke.
    if (lease and sha and not cur.get('_unparseable') and cur.get('token') == lease.get('token')
            and time.time() < float(cur['expires_at']) - skew()):
        _say('check: this machine holds the lock (%s).' % describe(cur))
        return OK
    _say('check: this machine does NOT hold the lock - it is %s. Do not publish.' % describe(cur))
    return NOT_ACQUIRED


def status():
    try:
        sha, cur = read_remote()
        _say('lock at %s %s: %s' % (_redact(remote()), REF, describe(cur) if sha else 'free'))
    except Unusable as e:
        _say('status: UNUSABLE - %s' % e)
    return OK


def break_lock(force=False):
    try:
        sha, cur = read_remote()
        if not sha:
            _say('break: there is no lock.')
            return OK
        if not _expired(cur, time.time()) and not force:
            _say('break: REFUSED - %s is inside its lease. Re-run with --force only if you are sure '
                 'that run is dead; breaking a live lock lets two machines publish at once.' % describe(cur))
            return NOT_ACQUIRED
        if _cas(None, sha):
            _say('break: removed the lock held by %s%s.' % (describe(cur), ' (FORCED)' if force else ''))
            return OK
        _say('break: the lock changed while breaking it - re-run status.')
        return NOT_ACQUIRED
    except Unusable as e:
        _say('break: UNUSABLE - %s' % e)
        return NOT_ACQUIRED


def _kill_tree(proc):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _spawn(cmd):
    kw = {'env': _child_env()}
    if os.name == 'nt':
        kw['creationflags'] = 0x00000200          # CREATE_NEW_PROCESS_GROUP
    else:
        kw['start_new_session'] = True
    return subprocess.Popen(cmd, **kw)


def _wait_child(proc):
    try:
        return proc.wait()
    except KeyboardInterrupt:
        _kill_tree(proc)
        return 130


def run(runner, cmd, ttl=RUN_TTL, every=RUN_RENEW, poll=5.0):
    # Wake often enough to renew on time and to notice a lapsed lease well inside it.
    poll = max(0.2, min(poll, every, ttl / 4.0))
    mode = lock_mode()
    if mode == 'bad':
        _say('NOT ACQUIRED - DEALFLOW_RUNNER_LOCK=%r is not a value this file understands. '
             'Use 1/on/true/yes to arm the lease, or 0/off/false/no (or unset it) to leave it off. '
             '%s did not start.' % (os.environ.get('DEALFLOW_RUNNER_LOCK'), runner))
        return NOT_ACQUIRED
    if mode == 'off':
        _say('cross-machine lease OFF (DEALFLOW_RUNNER_LOCK is not 1). This run does not coordinate '
             'with another machine and does not check which machine is armed. To turn it on: '
             'DEALFLOW_RUNNER_LOCK=1 and DEALFLOW_ARMED_MACHINE=<this machine\'s engine.id>.')
        try:
            proc = _spawn(cmd)
        except OSError as e:
            _say('could not start %r: %s' % (cmd, e))
            return 1
        return _wait_child(proc)
    refused = assert_armed(runner)
    if refused:
        return refused
    rc, lease = acquire(runner, ttl=ttl, pid=os.getpid())
    if rc != OK:
        return NOT_ACQUIRED
    state = {'lease': lease, 'ok_at': time.time(), 'lost': None}
    try:
        proc = _spawn(cmd)
    except OSError as e:
        _say('could not start %r: %s' % (cmd, e))
        release(lease)
        return 1
    stop = threading.Event()

    def heartbeat():
        next_at = time.time() + every
        while not stop.wait(poll):
            now = time.time()
            # Wall clock, not a sleep counter: after a laptop wakes, this is the first thing that
            # notices the lease ran out while it slept.
            if now - state['ok_at'] > ttl:
                state['lost'] = 'the lease ran out (%s since the last renewal - the machine slept or lost ' \
                                'the network) and another machine may now hold the lock' % _age(now - state['ok_at'])
                break
            if now >= next_at:
                r, state['lease'] = renew(state['lease'], ttl)
                if r == OK:
                    state['ok_at'] = time.time()
                    next_at = state['ok_at'] + every
                elif r == LOST:
                    state['lost'] = 'another machine took the lock'
                    break
                else:
                    next_at = now + min(60, every)
        if state['lost'] and proc.poll() is None:
            _say('LEASE LOST mid-run: %s. KILLING %s so two machines never publish at once. '
                 'This run is lost; the other machine\'s run stands.' % (state['lost'], runner))
            _kill_tree(proc)

    t = threading.Thread(target=heartbeat, daemon=True)
    t.start()
    try:
        crc = proc.wait()
    except KeyboardInterrupt:
        _kill_tree(proc)
        crc = 130
    stop.set()
    t.join(timeout=poll * 2 + 5)
    if state['lost']:
        _clear_mine()
        return LOST
    release(state['lease'])
    return crc


def main(argv):
    import argparse
    args = list(argv[1:])
    cmd = []
    if '--' in args:                       # everything after `--` is the wrapped command, verbatim
        i = args.index('--')
        args, cmd = args[:i], args[i + 1:]
    ap = argparse.ArgumentParser(prog='runner_lock.py', description='cross-machine runner lease')
    ap.add_argument('verb', choices=['acquire', 'renew', 'check', 'release', 'status', 'break', 'run'])
    ap.add_argument('--runner', default=os.environ.get('DEALFLOW_RUNNER', 'unknown'))
    ap.add_argument('--ttl', type=int, default=None)
    ap.add_argument('--every', type=int, default=RUN_RENEW)
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args(args)
    if a.verb == 'acquire':
        return acquire(a.runner, ttl=a.ttl or CLI_TTL)[0]
    if a.verb == 'renew':
        lease = _read_mine()
        if not lease:
            _say('renew: this machine holds no lease.')
            return LOST
        return renew(lease, a.ttl or CLI_TTL)[0]
    if a.verb == 'check':
        return check()
    if a.verb == 'release':
        return release()
    if a.verb == 'status':
        return status()
    if a.verb == 'break':
        return break_lock(a.force)
    if not cmd:
        ap.error('run needs a command after --')
    return run(a.runner, cmd, ttl=a.ttl or RUN_TTL, every=a.every)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
