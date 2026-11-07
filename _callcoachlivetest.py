"""Live call coach (call_coach_live.html + call_coach_server.py) and its dock in Call Mode.

Pins: the gate keeps the consent line and the responsibility checkbox; audio chunks upload one at a
time in order (the server appends, so order is the file) and stop waits for held chunks before it
saves; the lead fill takes the first name after the comma of a "LAST, FIRST" owner; call_sessions/
is gitignored; Call Mode docks the coach instead of opening a tab; and a session round-trip through
the real server (run from a temp copy, so nothing lands in the repo) writes audio.webm,
transcript.txt and meta.json. The JS checks need node; they are skipped, loudly, without it.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, 'call_coach_live.html')
SERVER = os.path.join(HERE, 'call_coach_server.py')
bad = []


def chk(c, m):
    print(('ok   ' if c else 'FAIL ') + m)
    if not c:
        bad.append(m)


html = open(PAGE, encoding='utf-8').read()

# --- gate ----------------------------------------------------------------------------------
chk('I record my calls so I keep your file straight. That okay with you?' in html, 'consent line on the gate')
chk("I accept responsibility for consent on this line." in html, 'responsibility checkbox text')
chk(re.search(r'<button[^>]*id="startbtn"[^>]*disabled', html) is not None, 'Start is disabled until the box is ticked')
chk(not re.search(r'<script[^>]+src=', html), 'no external script')

# --- audio upload order --------------------------------------------------------------------
up = html[html.index('function postChunk('):html.index('function startRecorder(')]
chk('S.uploading' in up and 'S.pending[0]' in up and 'S.pending.shift()' in up, 'one chunk in flight, head of the queue first')
chk('.catch(function(){ S.pending.push' not in up, 'a failed chunk is not re-queued behind later ones')
fin = html[html.index('function finish('):]
chk('drainAudio(' in fin and fin.index('drainAudio(') < fin.index("/transcript'"), 'stop waits for held chunks before saving')

# --- gitignore -----------------------------------------------------------------------------
gi = open(os.path.join(HERE, '.gitignore'), encoding='utf-8').read().splitlines()
chk('call_sessions/' in gi, 'call_sessions/ is gitignored (homeowner audio never committed)')

# --- Call Mode dock ------------------------------------------------------------------------
cm = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
chk("'http://127.0.0.1:8777/?case='" in cm, 'Call Mode carries the Coach link')
chk('function coachDock(' in cm and 'allow="microphone' in cm, 'Call Mode docks the coach with mic allowed')
chk("closest('a[href^=\"' + COACH_ORIGIN" in cm and '_coachDockable()' in cm, 'only the coach link is intercepted, and only on a computer')

# --- JS behaviour in node ------------------------------------------------------------------
node = shutil.which('node')
if not node:
    print('SKIP JS checks: node not on PATH')
else:
    name = html[html.index('  /* County rolls write'):html.index("  var st1=String(m.address")]
    js = r"""
function first(owners){ var m={owners:owners}; var v={value:''}; var $=function(){return v;};
  %s
  return v.value; }
var up = %s;
var S={pending:[],uploading:false,sid:'s'}, inflight=0, maxIn=0, order=[], fail=1, q=[];
function now(){return Date.now();}
global.fetch=function(u,o){ inflight++; maxIn=Math.max(maxIn,inflight);
  return new Promise(function(res,rej){ q.push(function(){ inflight--; if(fail){fail--; rej(0);} else { order.push(o.body); res({ok:true}); } }); }); };
global.setInterval=function(){};
eval(up);
postChunk('a'); postChunk('b'); postChunk('c');
(async function(){
  for(var i=0;i<20;i++){ var f=q.shift(); if(f){ f(); await new Promise(r=>setTimeout(r,0)); } else { pump(); await new Promise(r=>setTimeout(r,0)); } }
  console.log(JSON.stringify({names:[first('DOE, MARIA'), first('Maria Doe'), first('ACME HOLDINGS LLC'), first('PEREZ, JOSE & PEREZ, ANA')],
                              order:order, maxIn:maxIn}));
})();
"""
    src = js % (name, json.dumps(up))
    r = subprocess.run([node, '-e', src], capture_output=True, text=True, timeout=60)
    if r.returncode:
        chk(False, 'JS runs in node: %s' % r.stderr[-400:])
    else:
        o = json.loads(r.stdout.strip().splitlines()[-1])
        chk(o['names'] == ['Maria', 'Maria', '', 'Jose'], 'first name from "LAST, FIRST", none for a company: %s' % o['names'])
        chk(o['order'] == ['a', 'b', 'c'], 'chunks land in order after a failed first POST: %s' % o['order'])
        chk(o['maxIn'] == 1, 'never more than one chunk in flight')

# --- server round-trip from a temp copy ----------------------------------------------------
def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, path, body=None, raw=False):
    data, hdr = None, {}
    if body is not None:
        data = body if raw else json.dumps(body).encode()
        hdr['Content-Type'] = 'application/octet-stream' if raw else 'application/json'
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data, headers=hdr)
    with urllib.request.urlopen(req, timeout=10) as r:
        b = r.read()
        return json.loads(b) if 'json' in r.headers.get('Content-Type', '') else b.decode('utf-8', 'replace')


with tempfile.TemporaryDirectory() as d:
    shutil.copy(SERVER, d)
    shutil.copy(PAGE, d)
    with open(os.path.join(d, 'leads_final.json'), 'w', encoding='utf-8') as f:
        json.dump([{'Case #': '2026-000001-CA-01', 'Address': '100 NW 1st Ave, Miami, FL 33128',
                    'owners': 'DOE, JANE', 'AuctionDate': '2099-01-02'}], f)
    port = free_port()
    proc = subprocess.Popen([sys.executable, os.path.join(d, 'call_coach_server.py')], cwd=d,
                            env=dict(os.environ, COACH_PORT=str(port)),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        h = {}
        for _ in range(50):
            try:
                h = call(port, '/api/health')
                break
            except Exception:
                time.sleep(0.1)
        chk(h.get('ok') and h.get('leads') == 1, 'server up from a temp copy with 1 lead')
        chk('Coach.start()' in call(port, '/'), 'server serves call_coach_live.html at /')
        m = call(port, '/api/lead?q=nw+1st').get('matches') or []
        chk(len(m) == 1 and m[0]['case'] == '2026-000001-CA-01', 'lead lookup by address fragment')
        sid = call(port, '/api/session/start', {'case': '2026-000001-CA-01'})['session_id']
        call(port, '/api/session/%s/audio' % sid, b'\x1aE\xdf\xa3' + b'0' * 100, raw=True)
        a = call(port, '/api/session/%s/audio' % sid, b'1' * 50, raw=True)
        chk(a.get('bytes') == 154, 'audio chunks append')
        call(port, '/api/session/%s/transcript' % sid, {'lines': [{'t': '00:01', 'text': 'hello'}]})
        e = call(port, '/api/session/%s/end' % sid, {'duration_s': 3, 'consent_ack': True})
        sd = os.path.join(d, 'call_sessions', sid)
        chk(all(os.path.exists(os.path.join(sd, n)) for n in ('audio.webm', 'transcript.txt', 'meta.json')),
            'session folder has audio.webm, transcript.txt, meta.json')
        chk(e['meta'].get('consent_ack') is True, 'meta.json records consent_ack')
    finally:
        proc.terminate()
        proc.wait(timeout=5)

print()
print('FAIL %d' % len(bad) if bad else 'ALL OK')
raise SystemExit(1 if bad else 0)
