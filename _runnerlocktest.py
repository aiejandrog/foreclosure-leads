"""_runnerlocktest -- runner_lock.py, the CROSS-MACHINE runner lease, against a real git server.

The "server" is a local bare repo standing in for aiejandrog/dealflow-ledgers-private; each simulated
MACHINE is its own scratch git dir + host name (DEALFLOW_LOCK_GITDIR / DEALFLOW_LOCK_HOST), exactly
the isolation two PCs have. Every race here is between separate OS processes, so the only thing
that can make exactly one of them win is the server's compare-and-swap - which is the claim.

WHAT IT PINS
  1. basic: A takes it; B is refused with rc=9 and told WHO holds it; B's release cannot free A's
     lock; A's release frees it; B then takes it.
  2. RACE: 8 processes (4 per machine) acquire a free lock at the same instant -> exactly one rc=0,
     and the server's lock carries that winner's token. Repeated several rounds.
  3. STALE: an expired lease is broken by the next acquirer, loudly; the dead holder's late release
     does not delete the new holder's lock; its renew reports LOST (rc=10).
  4. STALE RACE: 6 processes break the same expired lock at once -> exactly one wins.
  5. FAIL-CLOSED: an unreachable server -> rc=9 "UNUSABLE", no local lease recorded; a lock commit
     this tool did not write is treated as HELD, never as free.
  6. check (fencing): 0 only for the live holder; 9 for the other machine and after expiry.
  7. break: refuses a live lease without --force; --force removes it.
  8. run wrapper: runs the command and returns ITS exit code, holds the lock while running (the
     other machine is refused), releases after; never starts the command when the lock is held;
     KILLS the command when the lease runs out mid-run (a laptop that slept) and exits 10.
  9. the default remote is the PRIVATE ledgers repo, never this public one.

    python _runnerlocktest.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
LOCK = str(HERE / 'runner_lock.py')
REF = 'refs/heads/dealflow-runner-lock'
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


T = pathlib.Path(tempfile.mkdtemp(prefix='runnerlock_'))
SERVER = T / 'server.git'
subprocess.run(['git', 'init', '-q', '--bare', str(SERVER)], check=True)
# The armed-machine check reads a declared id, never the hostname. Pin both sides to "laptop"
# so the existing races keep taking the lock; the cases below override them.
IDFILE = T / 'engine.id'
IDFILE.write_text('laptop\n', encoding='utf-8')


def env_for(machine, server=None, skew='0', **over):
    e = {**os.environ, 'DEALFLOW_LOCK_REMOTE': str(server or SERVER),
         'DEALFLOW_LOCK_GITDIR': str(T / ('m-' + machine)), 'DEALFLOW_LOCK_HOST': machine.upper(),
         'DEALFLOW_LOCK_SKEW': skew, 'PYTHONIOENCODING': 'utf-8',
         'DEALFLOW_RUNNER_LOCK': '1', 'DEALFLOW_ARMED_MACHINE': 'laptop',
         'DEALFLOW_ENGINE_ID': 'laptop', 'DEALFLOW_ENGINE_ID_FILE': str(IDFILE)}
    for k, v in over.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return e


def lock(machine, *args, server=None, timeout=120, **over):
    p = subprocess.run([sys.executable, LOCK] + list(args), capture_output=True, text=True,
                       env=env_for(machine, server, **over), timeout=timeout, cwd=str(T))
    return p.returncode, p.stdout + p.stderr


def spawn(machine, *args):
    return subprocess.Popen([sys.executable, LOCK] + list(args), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=env_for(machine), cwd=str(T))


def server_lock():
    p = subprocess.run(['git', '--git-dir', str(SERVER), 'log', '-1', '--format=%B', REF],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return None
    body = p.stdout.strip()
    return json.loads(body.split('\n', 1)[1]) if '\n' in body else None


def mine(machine):
    try:
        return json.load(open(T / ('m-' + machine) / 'dealflow-mine.json'))
    except (OSError, ValueError):
        return None


def clear():
    subprocess.run(['git', '--git-dir', str(SERVER), 'update-ref', '-d', REF], capture_output=True)
    for m in T.glob('m-*/dealflow-mine.json'):
        m.unlink()


# ---- 1. basic two-machine exchange
rc, out = lock('laptop', 'acquire', '--runner', 'refresh-dealflow.bat')
rec('laptop acquires a free lock (rc 0)', rc == 0, out.strip().splitlines()[-1:])
rc, out = lock('desktop', 'acquire', '--runner', 'refresh-dealflow.bat')
rec('desktop is refused while the laptop holds it (rc 9)', rc == 9)
rec('the refusal names the holder host and runner', 'HELD by refresh-dealflow.bat on LAPTOP' in out, out.strip()[:160])
rec('the refusal says nothing was built or pushed', 'nothing built, nothing pushed' in out)
rc, out = lock('desktop', 'release')
rec("desktop's release (it holds nothing) exits 0", rc == 0)
rec("...and the laptop's lock survives it", (server_lock() or {}).get('host') == 'LAPTOP')
rc, out = lock('laptop', 'status')
rec('status shows the holder', rc == 0 and 'LAPTOP' in out)
rc, out = lock('laptop', 'release')
rec('laptop releases (rc 0) and the ref is gone', rc == 0 and server_lock() is None, out.strip()[-80:])
rec('laptop forgot its local lease', mine('laptop') is None)
rc, out = lock('desktop', 'acquire', '--runner', 'run-phones-nightly.bat')
rec('desktop can take it once released', rc == 0 and (server_lock() or {}).get('host') == 'DESKTOP')
lock('desktop', 'release')

# ---- 2. the race: 8 processes, two machines, one free lock
for rnd in range(3):
    clear()
    procs = [spawn(('laptop' if i % 2 else 'desktop') + str(i), 'acquire', '--runner', 'r%d' % i) for i in range(8)]
    res = [(p.wait(timeout=180), p.stdout.read()) for p in procs]
    winners = [i for i, (rc, _) in enumerate(res) if rc == 0]
    held = server_lock() or {}
    rec('race round %d: exactly one of 8 racing processes wins' % (rnd + 1), len(winners) == 1,
        'winners=%s rcs=%s' % (winners, [r for r, _ in res]))
    if len(winners) == 1:
        w = winners[0]
        m = mine(('laptop' if w % 2 else 'desktop') + str(w))
        rec('race round %d: the server lock is the winner\'s token' % (rnd + 1),
            m is not None and held.get('token') == m.get('token'))
        rec('race round %d: every loser exited 9, none crashed' % (rnd + 1),
            all(rc == 9 for i, (rc, _) in enumerate(res) if i != w), [r for r, _ in res])
clear()

# ---- 3. stale lease
rc, _ = lock('laptop', 'acquire', '--runner', 'refresh-dealflow.bat', '--ttl', '1')
old = mine('laptop')
time.sleep(2.2)
rc, out = lock('desktop', 'acquire', '--runner', 'refresh-dealflow.bat')
rec('an expired lease is broken by the next acquirer', rc == 0 and (server_lock() or {}).get('host') == 'DESKTOP')
rec('...loudly', 'BROKE A STALE LOCK' in out and 'LAPTOP' in out, out.strip()[:120])
rc, out = lock('laptop', 'release')
rec("the dead holder's late release exits 0 and does NOT delete the new lock",
    rc == 0 and (server_lock() or {}).get('host') == 'DESKTOP', out.strip()[-100:])
# put the old lease back to simulate the laptop still believing it holds it, then renew
(T / 'm-laptop').mkdir(exist_ok=True)
json.dump(old, open(T / 'm-laptop' / 'dealflow-mine.json', 'w'))
rc, out = lock('laptop', 'renew')
rec("the dead holder's renew reports LOST (rc 10)", rc == 10, out.strip()[-100:])
rc, out = lock('laptop', 'check')
rec("the dead holder's check says do not publish (rc 9)", rc == 9 and 'Do not publish' in out)
rc, out = lock('desktop', 'check')
rec("the live holder's check passes (rc 0)", rc == 0)
lock('desktop', 'release')

# ---- 4. stale race
for rnd in range(2):
    clear()
    lock('ghost', 'acquire', '--runner', 'dead-run', '--ttl', '1')
    time.sleep(2.2)
    procs = [spawn('breaker%d' % i, 'acquire', '--runner', 'b%d' % i) for i in range(6)]
    res = [(p.wait(timeout=180), p.stdout.read()) for p in procs]
    winners = [i for i, (rc, _) in enumerate(res) if rc == 0]
    rec('stale race round %d: exactly one of 6 breakers wins the expired lock' % (rnd + 1), len(winners) == 1,
        [r for r, _ in res])
    rec('stale race round %d: the lock is no longer the dead one' % (rnd + 1),
        (server_lock() or {}).get('host', '').startswith('BREAKER'))
clear()

# ---- 5. fail-closed
rc, out = lock('laptop', 'acquire', '--runner', 'refresh-dealflow.bat', server=T / 'no-such-server.git')
rec('unreachable lock server -> rc 9', rc == 9)
rec('...reported as UNUSABLE, not as held', 'UNUSABLE' in out and 'HELD' not in out, out.strip()[:140])
rec('...and no local lease recorded', mine('laptop') is None)
rc, out = lock('laptop', 'status', server=T / 'no-such-server.git')
rec('status against an unreachable server still exits 0', rc == 0 and 'UNUSABLE' in out)
# a foreign commit on the lock ref
env = {**os.environ, 'GIT_AUTHOR_NAME': 'x', 'GIT_AUTHOR_EMAIL': 'x@x', 'GIT_COMMITTER_NAME': 'x',
       'GIT_COMMITTER_EMAIL': 'x@x'}
tree = subprocess.run(['git', '--git-dir', str(SERVER), 'mktree'], input='', capture_output=True, text=True).stdout.strip()
junk = subprocess.run(['git', '--git-dir', str(SERVER), 'commit-tree', tree, '-m', 'not a lock at all'],
                      capture_output=True, text=True, env=env).stdout.strip()
subprocess.run(['git', '--git-dir', str(SERVER), 'update-ref', REF, junk], check=True)
rc, out = lock('laptop', 'acquire', '--runner', 'refresh-dealflow.bat')
rec('a lock commit this tool did not write is HELD, never free (rc 9)', rc == 9 and 'UNREADABLE' in out, out.strip()[:120])
clear()

# ---- 7. break
lock('laptop', 'acquire', '--runner', 'refresh-dealflow.bat')
rc, out = lock('desktop', 'break')
rec('break refuses a live lease without --force (rc 9)', rc == 9 and server_lock() is not None)
rc, out = lock('desktop', 'break', '--force')
rec('break --force removes it', rc == 0 and server_lock() is None and 'FORCED' in out)
clear()

# ---- 8. the run wrapper
marker = T / 'ran.txt'
child = [sys.executable, '-c', 'import sys,time; time.sleep(3); open(sys.argv[1],"w").write("x"); sys.exit(3)', str(marker)]
p = spawn('laptop', 'run', '--runner', 'refresh-dealflow.bat', '--', *child)
time.sleep(1.5)
rc2, out2 = lock('desktop', 'acquire', '--runner', 'refresh-dealflow.bat')
rec('run: the other machine is refused while the wrapped command runs', rc2 == 9)
rc = p.wait(timeout=120)
out = p.stdout.read()
rec("run: returns the command's own exit code (3)", rc == 3, out.strip()[-120:])
rec('run: the command actually ran', marker.exists())
rec('run: the lock is released afterwards', server_lock() is None)
marker.unlink()
lock('desktop', 'acquire', '--runner', 'run-phones-nightly.bat')
rc, out = lock('laptop', 'run', '--runner', 'refresh-dealflow.bat', '--', *child)
rec('run: when the other machine holds it, rc 9 and the command NEVER starts', rc == 9 and not marker.exists())
clear()
# lease lost mid-run: a 3s lease that is never renewed (renew interval far away) and a 60s command
slow = [sys.executable, '-c', 'import time; time.sleep(60)']
t0 = time.time()
rc, out = lock('laptop', 'run', '--runner', 'refresh-dealflow.bat', '--ttl', '3', '--every', '3600', '--', *slow,
               timeout=120)
took = time.time() - t0
rec('run: a lease that runs out mid-run KILLS the command and exits 10', rc == 10, 'rc=%s after %.0fs' % (rc, took))
rec('run: ...promptly, not after the command would have finished', took < 30, '%.1fs' % took)
rec('run: ...and says so in the log', 'LEASE LOST mid-run' in out and 'KILLING' in out, out.strip()[-160:])
rc, out = lock('desktop', 'acquire', '--runner', 'refresh-dealflow.bat')
rec('run: the other machine can take over after that', rc == 0)
clear()
# renewal keeps a short lease alive across a longer command
rc, out = lock('laptop', 'run', '--runner', 'refresh-dealflow.bat', '--ttl', '4', '--every', '1', '--',
               sys.executable, '-c', 'import time; time.sleep(9)', timeout=120)
rec('run: renewals keep a 4s lease alive through a 9s command (rc 0)', rc == 0, out.strip()[-160:])
rec('run: ...and it is released at the end', server_lock() is None)

# ---- 9. default remote
src = open(LOCK, encoding='utf-8').read()
import importlib.util
spec = importlib.util.spec_from_file_location('rl', LOCK)
rl = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(HERE))
spec.loader.exec_module(rl)
for k in ('DEALFLOW_LOCK_REMOTE',):
    os.environ.pop(k, None)
rec('the default lock remote is the PRIVATE ledgers repo', rl.remote().endswith('dealflow-ledgers-private.git'), rl.remote())
rec('...and never this public repo', 'foreclosure-leads' not in rl.remote())

# ---- 10. the deploy wrapper
bat = (HERE / 'run-locked.bat').read_text(encoding='utf-8')
rec('run-locked.bat wraps the runner in `runner_lock.py run ... -- cmd /c`',
    'python -u runner_lock.py run --runner "%~1" -- cmd /c "%~dp0%~1"' in bat)
rec("run-locked.bat hands back runner_lock's exit code", 'endlocal & exit /b %errorlevel%' in bat)
rec('run-locked.bat never builds, gates or pushes anything itself',
    not any(w in bat.lower() for w in ('git push', 'git add', 'git commit', 'publish_guard', 'make_tracker')))
rec('run-locked.bat names the env gate', 'DEALFLOW_RUNNER_LOCK' in bat)

# ---- 11. the gate and the armed machine
# A one-shot command. The gate-off case points at a server that does not exist: touching it is a fail.
off_marker = T / 'off.txt'
off_child = [sys.executable, '-c',
             'import os,sys; open(sys.argv[1],"w").write(os.environ.get("DEALFLOW_LOCK_INNER","")); sys.exit(7)',
             str(off_marker)]
rc, out = lock('offbox', 'run', '--runner', 'refresh-dealflow.bat', '--', *off_child,
               server=T / 'no-such-server.git', DEALFLOW_RUNNER_LOCK=None,
               DEALFLOW_ENGINE_ID='desktop', timeout=30)
rec('gate off: the command runs (rc 7) even with a desktop identity and no server',
    rc == 7 and off_marker.exists(), out.strip()[-180:])
rec('gate off: the log says the lease is off', 'lease OFF' in out)
rec('gate off: no scratch repo was created', not (T / 'm-offbox' / 'objects').exists())
inner = off_marker.read_text(encoding='utf-8') if off_marker.exists() else ''
rec('gate off: the child is marked held- so the .bat does not take the lease again', inner.startswith('held-'), inner)
rc, out = lock('offbox', 'run', '--runner', 'refresh-dealflow.bat', '--', sys.executable, '-c', 'raise SystemExit(0)',
               DEALFLOW_RUNNER_LOCK='please', timeout=30)
rec('an unrecognised DEALFLOW_RUNNER_LOCK refuses (rc 9) and does not run the command',
    rc == 9 and 'not a value' in out, out.strip()[-160:])

clear()
desk = T / 'desk.id'
desk.write_text('desktop\n', encoding='utf-8')
spent = T / 'spent.txt'
rc, out = lock('desktop', 'run', '--runner', 'refresh-dealflow.bat', '--',
               sys.executable, '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("x")', str(spent),
               DEALFLOW_ENGINE_ID='desktop', DEALFLOW_ENGINE_ID_FILE=str(desk), timeout=30)
rec('desktop identity cannot run the refresh (rc 9)', rc == 9 and not spent.exists(), out.strip()[-180:])
rec('...the refusal names both machines', '"desktop"' in out and '"laptop"' in out, out.strip()[-180:])
rec('...and the lease was not taken', server_lock() is None)
rc, out = lock('laptop', 'run', '--runner', 'cadence-daily.bat', '--', sys.executable, '-c', 'raise SystemExit(0)',
               DEALFLOW_ENGINE_ID=None, DEALFLOW_ENGINE_ID_FILE=str(T / 'missing-engine.id'), timeout=30)
rec('a send with no declared identity refuses', rc == 9 and 'cannot prove' in out, out.strip()[-160:])
rec('...still without taking the lease', server_lock() is None)
rc, out = lock('laptop', 'run', '--runner', 'run-phones-nightly.bat', '--', sys.executable, '-c', 'raise SystemExit(0)',
               DEALFLOW_ARMED_MACHINE=None, timeout=30)
rec('a paid runner with DEALFLOW_ARMED_MACHINE unset refuses', rc == 9 and 'DEALFLOW_ARMED_MACHINE is unset' in out)
rc, out = lock('laptop', 'run', '--runner', 'run-phones.bat', '--', sys.executable, '-c', 'raise SystemExit(0)',
               DEALFLOW_ENGINE_ID='cloud', timeout=30)
rec('engine.id and DEALFLOW_ENGINE_ID disagreeing refuses (fail closed)', rc == 9 and 'disagrees' in out, out.strip()[-160:])
rc, out = lock('laptop', 'run', '--runner', 'run-phones.bat', '--', sys.executable, '-c', 'raise SystemExit(0)',
               DEALFLOW_ENGINE_ID=None, DEALFLOW_ENGINE_ID_FILE=str(T), timeout=30)
rec('an unreadable engine.id refuses', rc == 9 and 'unreadable' in out, out.strip()[-160:])
rc, out = lock('desktop', 'run', '--runner', 'run-replies-daily.bat', '--', sys.executable, '-c', 'raise SystemExit(4)',
               DEALFLOW_ENGINE_ID='desktop', DEALFLOW_ENGINE_ID_FILE=str(desk), timeout=60)
rec('the reply bake is not an armed-machine check: a desktop identity still runs it (rc 4)',
    rc == 4, out.strip()[-180:])
rec('...and releases the lease when it finishes', server_lock() is None)
# the lease records the declared identity, not only the hostname
rc, out = lock('laptop', 'acquire', '--runner', 'run-replies-daily.bat')
held = server_lock() or {}
rec('the lease carries the declared engine id', held.get('engine') == 'laptop' and held.get('host') == 'LAPTOP', held)
lock('laptop', 'release')

# the .bats enter through the lease, and only a held- child skips it
for name in ('refresh-dealflow.bat', 'run-phones-nightly.bat', 'run-phones.bat',
             'run-replies-daily.bat', 'run-leads.bat'):
    body = (HERE / name).read_text(encoding='utf-8')
    rec('%s enters through runner_lock.py unless already inside a held- child' % name,
        'runner_lock.py run --runner "%~nx0"' in body and 'LOCKMARK:~0,6%"=="_held-"' in body and ':runner_lock_held' in body)
    # LOCKMARK is never empty: a substring of an UNDEFINED variable is not an empty string in cmd,
    # and on 2026-09-28 a hand-run run-phones.bat died on it ("The syntax of the command is incorrect").
    rec('%s keeps LOCKMARK defined before taking a substring of it' % name,
        'set "LOCKMARK=_%DEALFLOW_LOCK_INNER%"' in body)
cad = (HERE / 'cadence-daily.bat').read_text(encoding='utf-8')
gate_at = cad.find('runner_lock.py run --runner cadence-daily.bat -- python -u cadence.py')
sync_at = cad.find('python -u sync_gate.py')
rec('cadence-daily.bat holds the lease around cadence.py, after the opt-out hold',
    sync_at >= 0 and gate_at > sync_at)
rec('a lease refusal does not reach cadence.py', ':refused' in cad and 'exit /b 9' in cad.split(':refused', 1)[1].split(':held', 1)[0])

print('\n==== %d/%d runner-lock checks passed ====' % (len(ok), len(ok) + len(bad)))
sys.exit(1 if bad else 0)
