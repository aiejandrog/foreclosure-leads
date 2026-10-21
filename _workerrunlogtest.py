"""Morning Worker flight recorder (2026-10-21): the bridge's POST /runlog and the board hooks.

The 10-21 8 AM run was armed, opened, and sent nothing, and no record said why. /runlog keeps one
counts-and-codes line per run event. This guards: the endpoint writes only safe scalars (nothing
that could carry a name, case or address), bad input is refused, it never touches the send ledger,
and the board still carries every hook that feeds it."""
import json
import os
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('DEALFLOW_BRIDGE_RELOAD_POLL_S', '0')
import send_server as S
import worker_runs as W

FAILS = []


def check(name, ok, why=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else '  :: ' + str(why)))
    if not ok:
        FAILS.append(name)


tmp = tempfile.mkdtemp()
S.HERE = tmp
srv = S.ThreadingHTTPServer(('127.0.0.1', 0), S.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def post(body, raw=False):
    data = body if raw else json.dumps(body).encode()
    req = urllib.request.Request('http://127.0.0.1:%d/runlog' % port, data=data,
                                 headers={'Content-Type': 'application/json'})
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


st, j = post({'ev': 'send_answer', 'x': {'status': 451, 'ok': False, 'blocked': 'placement', 'lane': 'urgent'}})
check('a normal event is recorded', st == 200 and j.get('ok'))
st, j = post({'ev': 'Task Open!!', 'x': {'owner': {'name': 'x'}, 'who': ['a'], 'why': 'e' * 500,
                                          'Bad Key': 1, 'n': 3}})
check('odd event name is slugged, not refused', st == 200)
st, _ = post({'ev': '', 'x': {}})
check('an empty event name is refused', st == 400)
st, _ = post(b'not json', raw=True)
check('bad json is refused', st == 400)
rows = W.load(os.path.join(tmp, 'worker_runs.jsonl'))
check('two rows written', len(rows) == 2, len(rows))
x = rows[1]['x'] if len(rows) > 1 else {}
check('dict and list values are dropped', 'owner' not in x and 'who' not in x, x)
check('long strings are cut to 80', len(x.get('why', '')) == 80, x)
check('key names are slugged', 'badkey' in x and x.get('n') == 3, x)
check('event name is slugged', rows[1]['ev'] == 'taskopen' if len(rows) > 1 else False, rows)
check('the send ledger is untouched', not os.path.exists(os.path.join(tmp, 'mail_sent.json')))
srv.shutdown()

t = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
for ev in ('worker_loaded', 'autostart_skipped', 'autostart_run', 'run_start', 'send_answer', 'send_error',
           'no_email', 'run_end', 'run_paused', 'run_stopped', 'task_open', 'blocked_not_armed',
           'blocked_already_ran_today', 'blocked_stale_board', 'open_requested'):
    check('board records ' + ev, ("'" + ev + "'") in t or ('"' + ev + '"') in t, ev)
check('worker_runs.jsonl is gitignored', 'worker_runs.jsonl' in open(os.path.join(HERE, '.gitignore')).read())

print('\nFAILED: %s' % FAILS if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
