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


# A stand-in for the language model: answers with whatever the test queued, and keeps what it was sent.
import http.server
import threading
AI = {'reply': 'It sounds like the bank is helping. What did they tell you happens next?', 'seen': []}


class FakeModel(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode('utf-8'))
        AI['seen'].append({'auth': self.headers.get('Authorization'), 'body': body})
        out = json.dumps({'choices': [{'message': {'content': AI['reply']}}]}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out))); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


fake = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeModel)
threading.Thread(target=fake.serve_forever, daemon=True).start()

with tempfile.TemporaryDirectory() as d:
    shutil.copy(SERVER, d)
    shutil.copy(PAGE, d)
    with open(os.path.join(d, 'leads_final.json'), 'w', encoding='utf-8') as f:
        json.dump([{'Case #': '2026-000001-CA-01', 'Address': '100 NW 1st Ave, Miami, FL 33128',
                    'owners': 'DOE, JANE', 'AuctionDate': '2099-01-02'}], f)
    port = free_port()
    proc = subprocess.Popen([sys.executable, os.path.join(d, 'call_coach_server.py')], cwd=d,
                            env=dict(os.environ, COACH_PORT=str(port), DEEPGRAM_API_KEY='test-not-a-real-key',
                                     VENICE_API_KEY='test-ai-key', COACH_AI_URL='http://127.0.0.1:%d/v1' % fake.server_address[1]),
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

        # The Deepgram key route: the page's own request gets it, nobody else, and health only says yes/no.
        def keyreq(hdr):
            req = urllib.request.Request('http://127.0.0.1:%d/api/dgkey' % port, headers=hdr)
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read()), r.headers.get('Access-Control-Allow-Origin')
            except urllib.error.HTTPError as ex:
                return ex.code, {}, ex.headers.get('Access-Control-Allow-Origin')
        own = 'http://127.0.0.1:%d' % port
        chk(keyreq({'Origin': own, 'Sec-Fetch-Site': 'same-origin'}) == (200, {'key': 'test-not-a-real-key'}, None),
            'the coach page gets the key from the variable, with no CORS grant on the reply')
        chk(keyreq({'Origin': 'https://example.com', 'Sec-Fetch-Site': 'cross-site'})[:2] == (403, {}), 'another site is refused the key')
        chk(keyreq({'Sec-Fetch-Site': 'cross-site'})[0] == 403 and keyreq({'Host': 'evil.example:%d' % port})[0] == 403,
            'a cross-site request with no Origin, or a foreign Host, is refused')
        chk(h.get('deepgram') is True and 'test-not-a-real-key' not in json.dumps(h), 'health says a key is set and never carries it')

        # The AI next line: the page posts the transcript, the server asks the model, the key stays here.
        def suggest(lines, hdr=None):
            req = urllib.request.Request('http://127.0.0.1:%d/api/suggest' % port, data=json.dumps({'lines': lines}).encode(),
                                         headers=dict({'Content-Type': 'application/json'}, **(hdr or {})))
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as ex:
                return ex.code, {}
        talk = [{'spk': 'you?', 'text': 'This is Alejandro.'}, {'spk': '', 'text': 'We talked to the bank already.'}]
        st, j = suggest(talk, {'Origin': own, 'Sec-Fetch-Site': 'same-origin'})
        sent = AI['seen'][-1] if AI['seen'] else {'auth': '', 'body': {'messages': [{}, {'content': ''}]}}
        prompt = json.dumps(sent['body'])
        chk(st == 200 and j.get('line') == AI['reply'], 'the AI line comes back to the coach page: %s' % j)
        chk(sent['auth'] == 'Bearer test-ai-key' and 'test-ai-key' not in json.dumps(j), 'the key goes to the model and never to the page')
        chk('CALLER: This is Alejandro.' in sent['body']['messages'][1]['content'] and '?: We talked to the bank already.' in sent['body']['messages'][1]['content'],
            'the transcript goes out with its speaker guesses')
        chk('100 NW 1st' not in prompt and '2026-000001-CA-01' not in prompt and 'DOE' not in prompt, 'no address, case number or owner name from the lead file is in the prompt')
        AI['reply'] = 'You could walk away with $50,000. Want that?'
        chk(suggest(talk)[1].get('line') == '', 'a model reply with a number in it is dropped')
        AI['reply'] = 'STEP: close\nSAY: Let me get you on his calendar. Is later today better, or first thing tomorrow?'
        j2 = suggest(talk)[1]
        chk(j2.get('step') == 'close' and j2.get('n') == 7 and j2.get('of') == 8 and j2.get('line', '').startswith('Let me get you on his calendar'),
            'the model names the step of the path its line belongs to: %s' % j2)
        chk('Book the advisor' in json.dumps(AI['seen'][-1]['body']) and 'later today' in json.dumps(AI['seen'][-1]['body']),
            'the model is given the path to the booking, with the script line of each step')
        AI['reply'] = 'SKIP'
        chk(suggest(talk)[1].get('line') == '', 'SKIP (the caller spoke last) shows nothing')
        n_seen = len(AI['seen'])
        chk(suggest(talk, {'Origin': 'https://example.com', 'Sec-Fetch-Site': 'cross-site'})[0] == 403 and len(AI['seen']) == n_seen,
            'another site cannot spend the key')
        chk(h.get('ai') is True and 'test-ai-key' not in json.dumps(h), 'health says an AI key is set and never carries it')
    finally:
        proc.terminate()
        proc.wait(timeout=5)

print()
print('FAIL %d' % len(bad) if bad else 'ALL OK')
raise SystemExit(1 if bad else 0)
