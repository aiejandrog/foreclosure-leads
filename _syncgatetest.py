"""_syncgatetest -- the 07:15 opt-out sync and the fail-closed hold on every send when it has not
finished OK today (decided 2026-09-26).

WHAT IT PINS
  1. sync_gate.verdict: HOLD for no status file, an unreadable one, yesterday's run, a run that
     started and never finished (killed / still running), a finished run with a failed step (and
     the reason names the step and its exit code), a finish time in the future. PASS only for a
     run that finished today with every step OK.
  2. morning_sync.py end to end, with FAKE replies.py / optout_sync.py / ledger_sync.py in a temp
     dir (the real ones are never run here): all OK -> exit 0 and the gate passes; replies.py
     exiting 0 WITHOUT rewriting replies.json (its IMAP-failure path) -> failure; any non-zero
     step -> failure; a step past its timeout -> failure; every step still runs after one fails;
     a run killed after it started leaves "running" -> hold.
  3. send_server.py for real, on a scratch port with SMTP faked and fake addresses only: no sync
     today -> /send is HELD with blocked=optout_stale (the refusal today's Morning Worker answers
     by pausing), test sends included, NOTHING reaches SMTP and no ledger row is written; /health
     shows sync_ok=false and the reason; a clean sync today -> the same send goes through; a
     failed sync -> held again and the reason names the step.
  4. The wiring: cadence-daily.bat asks sync_gate.py before cadence.py and a hold never reaches
     cadence.py; run-optout-sync.bat never builds or pushes; the 07:15 task template is valid,
     wakes the machine, catches up, and fires at least 30 minutes before the 08:00 Morning Worker.

    python _syncgatetest.py
"""
import datetime as dt
import importlib.util
import json
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:220]) if d else ''))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


G = load(HERE / 'sync_gate.py', 'sync_gate_under_test')
T = pathlib.Path(tempfile.mkdtemp(prefix='syncgate_'))
NOW = time.mktime((2026, 9, 28, 8, 0, 0, 0, 0, -1))           # Monday 08:00 local
TODAY = dt.datetime.fromtimestamp(NOW).date().isoformat()
YDAY = (dt.datetime.fromtimestamp(NOW).date() - dt.timedelta(days=1)).isoformat()


def status(**kw):
    p = T / ('st_%d.json' % len(list(T.glob('st_*.json'))))
    if kw.get('_raw') is not None:
        p.write_text(kw['_raw'], encoding='utf-8')
    else:
        p.write_text(json.dumps(kw), encoding='utf-8')
    return str(p)


OK_STEPS = [{'name': 'replies', 'rc': 0, 'ok': True}, {'name': 'optout_sync', 'rc': 0, 'ok': True},
            {'name': 'ledger_sync', 'rc': 0, 'ok': True}]
t715 = NOW - 45 * 60

# ---- 1. the verdict
v = G.verdict(NOW, str(T / 'nope.json'))
rec('no status file -> HOLD', not v['ok'] and 'no opt-out sync has ever been recorded' in v['reason'], v['reason'])
v = G.verdict(NOW, status(_raw='{not json'))
rec('unreadable status -> HOLD', not v['ok'] and 'cannot be read' in v['reason'], v['reason'])
v = G.verdict(NOW, status(_raw='[1,2]'))
rec('a status that is not an object -> HOLD', not v['ok'])
v = G.verdict(NOW, status(date=YDAY, state='finished', ok=True, started_at=t715 - 86400, finished_at=t715 - 86300, steps=OK_STEPS))
rec("yesterday's clean run -> HOLD (not today's list)", not v['ok'] and "has not run (last run: %s)" % YDAY in v['reason'], v['reason'])
v = G.verdict(NOW, status(date=TODAY, state='running', ok=False, started_at=t715, steps=[]))
rec("today's run started, never finished -> HOLD", not v['ok'] and 'has not finished' in v['reason'], v['reason'])
v = G.verdict(NOW, status(date=TODAY, state='finished', ok=False, started_at=t715, finished_at=t715 + 90,
                          steps=[OK_STEPS[0], {'name': 'optout_sync', 'rc': 2, 'ok': False, 'why': 'exit code 2'}, OK_STEPS[2]]))
rec('failed step -> HOLD, naming the step and rc', not v['ok'] and 'FAILED' in v['reason'] and 'optout_sync rc=2' in v['reason'], v['reason'])
v = G.verdict(NOW, status(date=TODAY, state='finished', ok=True, started_at=t715, finished_at=NOW + 3600, steps=OK_STEPS))
rec('finish time in the future -> HOLD', not v['ok'] and 'future' in v['reason'])
v = G.verdict(NOW, status(date=TODAY, state='finished', ok='yes', started_at=t715, finished_at=t715 + 90, steps=OK_STEPS))
rec('ok must be literally true, not truthy -> HOLD', not v['ok'])
v = G.verdict(NOW, status(date=TODAY, state='finished', ok=True, started_at=t715, finished_at=t715 + 90, steps=OK_STEPS))
rec("today's clean run -> PASS", v['ok'], v['reason'])
rec('every hold says how to clear it', all('run-optout-sync.bat' in G.verdict(NOW, p)['reason']
    for p in (str(T / 'nope.json'), status(date=YDAY, state='finished', ok=True, finished_at=1, steps=[]))))
h = G.health(NOW, str(T / 'nope.json'))
rec('health(): sync_ok false and sync_hold set when holding', h['sync_ok'] is False and h['sync_hold'])
lines = []
G._last_logged['reason'] = None
hv = G.verdict(NOW, str(T / 'nope.json'))
G.log_hold(hv, lines.append); G.log_hold(hv, lines.append); G.log_hold(hv, lines.append)
rec('a hold is logged once per reason, not once per send', len(lines) == 1 and lines[0].startswith('SENDS HELD'), lines)

# ---- 2. morning_sync end to end with fake steps
def fake_dir(replies='ok', optout='ok', ledger='ok'):
    d = pathlib.Path(tempfile.mkdtemp(prefix='msync_', dir=str(T)))
    shutil.copy(HERE / 'morning_sync.py', d / 'morning_sync.py')
    shutil.copy(HERE / 'sync_gate.py', d / 'sync_gate.py')
    body = {
        'ok': "import json,os; json.dump({'@x@example.com': {}}, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'replies.json'),'w')); print('replies.json written')",
        'imapfail': "print('IMAP check failed: fake'); print('  (app password wrong, IMAP disabled in Gmail settings, or network.)')",
    }
    (d / 'replies.py').write_text(body.get(replies, replies), encoding='utf-8')
    (d / 'optout_sync.py').write_text({'ok': 'print("ok")', 'fail': 'raise SystemExit(2)'}.get(optout, optout), encoding='utf-8')
    (d / 'ledger_sync.py').write_text({'ok': 'print("SYNCED")', 'fail': 'raise SystemExit("FAIL-LOUD: push rejected")'}.get(ledger, ledger), encoding='utf-8')
    return d


def msync(d):
    p = subprocess.run([sys.executable, 'morning_sync.py'], cwd=str(d), capture_output=True, text=True, timeout=120)
    st = json.loads((d / 'sync_status.json').read_text(encoding='utf-8'))
    return p.returncode, p.stdout + p.stderr, st


d = fake_dir()
rc, out, st = msync(d)
rec('all three steps OK -> exit 0, status ok, state finished', rc == 0 and st['ok'] is True and st['state'] == 'finished', out[-200:])
rec('status records today, the host and the single writer', st['date'] == dt.date.today().isoformat() and st.get('host')
    and 'ledger_add' in st.get('writer', ''))
rec('...and the gate then PASSES', G.verdict(path=str(d / 'sync_status.json'))['ok'])
rec('steps ran in order replies -> optout_sync -> ledger_sync', [s['name'] for s in st['steps']] == ['replies', 'optout_sync', 'ledger_sync'])

d = fake_dir(replies='imapfail')
rc, out, st = msync(d)
rec('replies.py exits 0 but never rewrote replies.json (IMAP failed) -> FAILURE', rc == 1 and not st['ok']
    and not st['steps'][0]['ok'] and 'did not rewrite replies.json' in st['steps'][0].get('why', ''), st['steps'][0])
rec('...and the later steps still ran (each only adds suppression)', all(s['ok'] for s in st['steps'][1:]))
v = G.verdict(path=str(d / 'sync_status.json'))
rec('...and the gate HOLDS, naming replies', not v['ok'] and 'replies rc=0' in v['reason'], v['reason'])

d = fake_dir(optout='fail')
rc, out, st = msync(d)
rec('optout_sync.py non-zero -> FAILURE and HOLD', rc == 1 and not G.verdict(path=str(d / 'sync_status.json'))['ok'])
d = fake_dir(ledger='fail')
rc, out, st = msync(d)
rec('ledger_sync.py non-zero -> FAILURE and HOLD (fail closed)', rc == 1 and not st['ok'] and st['steps'][2]['rc'] == 1)
rec('the log line says every send is HELD', 'every send is HELD' in out, out[-160:])

# a timeout, in-process with a 1s limit
d = fake_dir(optout='import time; time.sleep(30)')
M = load(d / 'morning_sync.py', 'morning_sync_under_test')
M.STEPS = (('replies', 'replies.py', 60, True), ('optout_sync', 'optout_sync.py', 1, False), ('ledger_sync', 'ledger_sync.py', 60, False))
t0 = time.time()
rc = M.main()
st = json.loads((d / 'sync_status.json').read_text(encoding='utf-8'))
rec('a step past its timeout is killed and counts as FAILED', rc == 1 and st['steps'][1]['rc'] == 'timeout' and time.time() - t0 < 25, st['steps'][1])

# killed mid-run: the "running" record is what is left
d = fake_dir(replies='import os,signal,time; os.kill(os.getppid(), signal.SIGKILL) if hasattr(signal,"SIGKILL") else os._exit(1); time.sleep(5)')
subprocess.run([sys.executable, 'morning_sync.py'], cwd=str(d), capture_output=True, timeout=60)
st = json.loads((d / 'sync_status.json').read_text(encoding='utf-8'))
v = G.verdict(path=str(d / 'sync_status.json'))
rec('a sync killed mid-run leaves "running" and the gate HOLDS', st['state'] == 'running' and not v['ok'] and 'has not finished' in v['reason'], v['reason'])

# ---- 3. the real send_server
def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def call(port, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data,
                                 headers={'Content-Type': 'application/json'} if data else {},
                                 method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {'err': str(e)}


LATER_GATES = [f for f in ('stay_gate.py',) if (HERE / f).exists()]
work = pathlib.Path(tempfile.mkdtemp(prefix='syncbridge_', dir=str(T)))
shutil.copy(HERE / 'send_server.py', work / 'send_server.py')
shutil.copy(HERE / 'sync_gate.py', work / 'sync_gate.py')
shutil.copy(HERE / 'mail_guard.py', work / 'mail_guard.py')        # send_server's last pre-SMTP check
for f in LATER_GATES:
    shutil.copy(HERE / f, work / f)
(work / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
(work / 'sender.json').write_text(json.dumps({'name': 'Test Sender'}), encoding='utf-8')
(work / 'optouts.json').write_text('{}', encoding='utf-8')          # fresh and empty: the staleness gate passes


def start_bridge(work, port):
  (work / '_run.py').write_text(
    'import sys, smtplib, os\n'
    'class _F:\n'
    '    def __init__(self, *a, **k): pass\n'
    '    def __enter__(self): return self\n'
    '    def __exit__(self, *a): return False\n'
    '    def login(self, u, p): pass\n'
    '    def send_message(self, m, *a, **k):\n'
    '        open("smtp_calls.txt", "a").write("1\\n"); return {}\n'
    'smtplib.SMTP_SSL = _F\n'
    'sys.argv = ["send_server.py", "--port", "%d", "--limit", "10"]\n'
    'exec(open("send_server.py", encoding="utf-8").read())\n' % port, encoding='utf-8')
  srv = subprocess.Popen([sys.executable, '_run.py'], cwd=str(work), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
  for _ in range(60):
      if call(port, '/health')[0] == 200:
          return srv, True
      time.sleep(0.25)
  return srv, False


port = free_port()
srv, up = start_bridge(work, port)
try:
    rec('bridge starts', up)
    SEND = {'to': 'owner1@example.com', 'subj': 'About your property',
            'body': 'Hello Test Owner,\n\nThis is a fake letter used only by a test.\n\nTest Sender', 'meta': {'c': 'FAKE-CASE-1', 'owner': 'Test Owner'}}
    st_, h = call(port, '/health')
    rec('/health: sync_ok false and sync_hold says why (no sync yet)', h.get('sync_ok') is False and 'has ever been recorded' in (h.get('sync_hold') or ''), h.get('sync_hold'))
    st_, j = call(port, '/send', SEND)
    rec('no sync today -> /send HELD', j.get('ok') is False and j.get('hold') == 'sync', j)
    rec('...as blocked=optout_stale, the refusal the Morning Worker PAUSES on', j.get('blocked') == 'optout_stale')
    st_, j = call(port, '/send', dict(SEND, meta={'c': 'FAKE-CASE-2', 'test': True}))
    rec('...test sends are held too (ALL sends)', j.get('hold') == 'sync', j)
    rec('...nothing reached SMTP', not (work / 'smtp_calls.txt').exists())
    rec('...and no mail_sent.json row was written', not (work / 'mail_sent.json').exists() or
        json.loads((work / 'mail_sent.json').read_text() or '[]') == [])
    today = dt.date.today().isoformat()
    y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    tnow = int(time.time())
    (work / 'sync_status.json').write_text(json.dumps({'date': y, 'state': 'finished', 'ok': True,
        'started_at': tnow - 86400, 'finished_at': tnow - 86300, 'steps': OK_STEPS}), encoding='utf-8')
    st_, j = call(port, '/send', SEND)
    rec("yesterday's clean sync -> still HELD", j.get('hold') == 'sync' and 'has not run' in j.get('err', ''), j.get('err'))
    (work / 'sync_status.json').write_text(json.dumps({'date': today, 'state': 'finished', 'ok': True,
        'started_at': tnow - 120, 'finished_at': tnow - 60, 'steps': OK_STEPS}), encoding='utf-8')
    st_, h = call(port, '/health')
    rec('/health: sync_ok true after a clean sync today', h.get('sync_ok') is True and h.get('sync_hold') is None
        and (h.get('sync_status') or {}).get('date') == today, h.get('sync_status'))
    st_, j = call(port, '/send', SEND)
    smtp_n = lambda: (work / 'smtp_calls.txt').read_text().count('1') if (work / 'smtp_calls.txt').exists() else 0
    if LATER_GATES:
        # another PR's per-case gate (e.g. #72's stay_gate) sits after this one and has no fixture
        # here: all this test can prove is that the SYNC gate let the send past.
        rec('clean sync today -> the send passes the sync gate (a later gate: %s)' % ','.join(LATER_GATES),
            j.get('hold') != 'sync' and j.get('blocked') != 'optout_stale', j)
    else:
        rec('clean sync today -> the same send goes through', st_ == 200 and j.get('ok') is True, j)
        rec('...and reached (fake) SMTP exactly once', smtp_n() == 1)
    allowed = smtp_n()
    (work / 'sync_status.json').write_text(json.dumps({'date': today, 'state': 'finished', 'ok': False,
        'started_at': tnow - 120, 'finished_at': tnow - 60,
        'steps': [OK_STEPS[0], OK_STEPS[1], {'name': 'ledger_sync', 'rc': 1, 'ok': False, 'why': 'exit code 1'}]}), encoding='utf-8')
    st_, j = call(port, '/send', dict(SEND, to='owner2@example.com', meta={'c': 'FAKE-CASE-3'}))
    rec('a failed sync today -> HELD, naming the step', j.get('hold') == 'sync' and 'ledger_sync rc=1' in j.get('err', ''), j.get('err'))
    (work / 'sync_status.json').write_text('{broken', encoding='utf-8')
    st_, j = call(port, '/send', dict(SEND, to='owner3@example.com', meta={'c': 'FAKE-CASE-4'}))
    rec('a corrupt status -> HELD', j.get('hold') == 'sync')
    rec('no held send reached (fake) SMTP', smtp_n() == allowed)
finally:
    srv.terminate()
    try:
        out = srv.communicate(timeout=10)[0] or ''
    except Exception:
        out = ''
rec('the bridge log says SENDS HELD with the reason (once per reason)', 'SENDS HELD - HOLD' in out, out[-200:])
rec('the bridge log never carries a recipient address for a held send',
    not re.search(r'SEND HELD \[sync\][^\n]*@example\.com', out))

# a bridge whose sync_gate.py is missing entirely (bad deploy) must hold, not pass
work2 = pathlib.Path(tempfile.mkdtemp(prefix='syncbridge2_', dir=str(T)))
for f in ['send_server.py', 'mail_guard.py'] + LATER_GATES:
    shutil.copy(HERE / f, work2 / f)
for f in ('gmail.key', 'sender.json', 'optouts.json', 'sync_status.json'):
    if (work / f).exists():
        shutil.copy(work / f, work2 / f)
(work2 / 'sync_status.json').write_text(json.dumps({'date': dt.date.today().isoformat(), 'state': 'finished', 'ok': True,
    'started_at': int(time.time()) - 120, 'finished_at': int(time.time()) - 60, 'steps': OK_STEPS}), encoding='utf-8')
port2 = free_port()
srv2, up2 = start_bridge(work2, port2)
try:
    st_, j = call(port2, '/send', dict(SEND, to='owner4@example.com', meta={'c': 'FAKE-CASE-5'}))
    rec('sync_gate.py missing entirely -> HELD even with a clean status (fail closed)',
        up2 and j.get('ok') is False and j.get('hold') == 'sync' and 'could not be evaluated' in j.get('err', ''), j.get('err'))
    st_, h = call(port2, '/health')
    rec('...and /health says so', h.get('sync_ok') is False and 'could not be evaluated' in (h.get('sync_hold') or ''))
    rec('...nothing reached SMTP', not (work2 / 'smtp_calls.txt').exists())
finally:
    srv2.terminate()
    try:
        srv2.communicate(timeout=10)
    except Exception:
        pass

# ---- 4. wiring
cad = (HERE / 'cadence-daily.bat').read_text(encoding='utf-8').splitlines()
ix = lambda pat: next((i for i, l in enumerate(cad) if re.match(pat, l)), None)
g, e, c, h_ = ix(r'python -u sync_gate\.py'), ix(r'if errorlevel 1 goto :held'), ix(r'python -u cadence\.py'), ix(r':held\s*$')
rec('cadence-daily.bat asks sync_gate.py before cadence.py', None not in (g, e, c) and g < e < c, (g, e, c))
rec('...a hold jumps to :held, which sits after the final exit and never reaches cadence.py',
    h_ is not None and h_ > c and not any('cadence.py' in l for l in cad[h_:]) and
    any(l.strip() == 'exit /b 3' for l in cad[h_:]))
rs = '\n'.join(l for l in (HERE / 'run-optout-sync.bat').read_text(encoding='utf-8').splitlines()
               if not l.strip().lower().startswith('rem'))     # commands only, not the header
rec('run-optout-sync.bat runs repo_guard then morning_sync.py', rs.find('repo_guard.bat') < rs.find('morning_sync.py') and 'repo_guard.bat' in rs)
rec('run-optout-sync.bat never builds, gates or pushes', not any(w in rs.lower() for w in ('git push', 'git add', 'git commit', 'make_tracker', 'publish')))
x = (HERE / 'desktop-setup' / 'task-templates' / 'DealFlow_OptoutSync.xml').read_bytes()
rec('07:15 template is UTF-8 without BOM', not x.startswith(b'\xef\xbb\xbf'))
NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
root = ET.fromstring(x.decode('utf-8'))
start = root.findtext('t:Triggers/t:CalendarTrigger/t:StartBoundary', '', NS)
hh, mm = (int(v) for v in start.split('T')[1].split(':')[:2])
MORNING_WORKER_AT = 8 * 60           # DEALFLOW Morning Worker, MACHINE-HANDOFF.md section 4
rec('the sync fires at 07:15', (hh, mm) == (7, 15), start)
rec('...at least 30 min before the 08:00 Morning Worker', MORNING_WORKER_AT - (hh * 60 + mm) >= 30)
rec('WakeToRun and StartWhenAvailable are true', root.findtext('t:Settings/t:WakeToRun', '', NS) == 'true'
    and root.findtext('t:Settings/t:StartWhenAvailable', '', NS) == 'true')
rec('it runs run-optout-sync.bat from the repo placeholder', root.findtext('t:Actions/t:Exec/t:Command', '', NS) == '__REPO__\\run-optout-sync.bat')
xs = x.decode('utf-8')
rec('no SID and no literal user path in the template', 'S-1-5-' not in xs and not re.search(r'[A-Za-z]:\\\\?Users', xs))
rec('sync_status.json is gitignored', subprocess.run(['git', 'check-ignore', '-q', 'sync_status.json'], cwd=str(HERE)).returncode == 0)

shutil.rmtree(T, ignore_errors=True)
print('\n==== %d/%d sync-gate checks passed ====' % (len(ok), len(ok) + len(bad)))
sys.exit(1 if bad else 0)
