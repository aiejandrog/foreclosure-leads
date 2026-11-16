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

# --- coach handoff into Call Mode ------------------------------------------------------------
chk('function coachDonePick(' in cm and "window.addEventListener('message'" in cm and 'coachDoneFor(r)' in cm,
    'Call Mode listens for the coach handoff and keeps it per lead')
chk("' advisor call booked: '" in cm and "'&date=' + _cda.iso" in cm and 'Tap the lit outcome to log it' in cm,
    'the booked time lands in the note, opens the calendar on that day, and nothing is logged without a tap')
if shutil.which('node'):
    pick = cm[cm.index('var COACH_OC = '):cm.index('/* The handoff for THIS lead')]
    hjs = "var COACH_ORIGIN='http://127.0.0.1:8777';\n" + pick + r"""
var ok={src:'bsg-coach', 'case':'CACE-26-000001', outcome:'appointment', when:'tomorrow at 9 AM', iso:'2026-11-17', hm:'09:00',
        note:'Wants to <keep> it.   Call set for 9.', want:'keep', objection:'has an "attorney"'};
var o=[coachDonePick(ok,'http://127.0.0.1:8777',5),
       coachDonePick(ok,'https://evil.example',5),
       coachDonePick(Object.assign({},ok,{src:'x'}),'http://127.0.0.1:8777',5),
       coachDonePick(Object.assign({},ok,{outcome:'delete_everything'}),'http://127.0.0.1:8777',5),
       coachDonePick(Object.assign({},ok,{'case':'<img src=x>'}),'http://127.0.0.1:8777',5),
       coachDonePick(Object.assign({},ok,{when:'<b>9</b> "x"', iso:'17/11/2026', hm:'25:00'}),'http://127.0.0.1:8777',5)];
console.log(JSON.stringify(o));
"""
    rr = subprocess.run([shutil.which('node'), '-e', hjs], capture_output=True, text=True, timeout=60)
    if rr.returncode:
        chk(False, 'coach handoff check runs in node: %s' % rr.stderr[-300:])
    else:
        ho = json.loads(rr.stdout.strip().splitlines()[-1])
        chk(ho[0] == {'c': 'CACE-26-000001', 'k': 'appt', 'when': 'tomorrow at 9 AM', 'iso': '2026-11-17', 'hm': '09:00',
                      'note': 'Wants to keep it. Call set for 9.', 'want': 'keep', 'objection': 'has an attorney', 'at': 5},
            'a handoff from our coach is accepted: %s' % ho[0])
        chk(ho[1] is None and ho[2] is None and ho[3] is None and ho[4] is None,
            'another origin, another sender, an unknown outcome or markup in the case number is refused')
        chk(ho[1] is None and "' coach: ' + _cdw.note" in cm and '_cdw.k === o.k' in cm,
            'the wrap-up note is saved only for the outcome he tapped, with markup characters dropped')
        chk(ho[5] and ho[5]['when'] == 'b9b x' and ho[5]['iso'] == '' and ho[5]['hm'] == '',
            'the time text is stripped to plain characters and a malformed date or time is dropped: %s' % ho[5])

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
        rep = AI['reply'].pop(0) if isinstance(AI['reply'], list) else AI['reply']
        out = json.dumps({'choices': [{'message': {'content': rep}}]}).encode()
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
    with open(os.path.join(d, 'lp_leads.json'), 'w', encoding='utf-8') as f:
        json.dump([{'case': 'CACE-26-999999', 'addr': '9 Test Ter, Davie, FL', 'oname': 'ROE RICHARD', 'st': 'LP', 'ctype': 'Bank/Mortgage',
                    'county': 'BROWARD', 'days': 9999, 'auction': '', 'eq': 123456}], f)
    with open(os.path.join(d, 'balloon_leads.json'), 'w', encoding='utf-8') as f:
        json.dump([{'case': 'BAL-000042', 'addr': '7 Fund Way, Hollywood, FL', 'oname': 'ACME HOLDINGS LLC', 'st': 'BAL',
                    'ctype': 'Hard-money balloon', 'county': 'BROWARD', 'days': 9999}], f)
    with open(os.path.join(d, 'broward_leads.json'), 'w', encoding='utf-8') as f:
        json.dump([{'case': 'TD-55', 'addr': '3 Tax Ln', 'oname': 'POE PAT', 'st': 'TD', 'ctype': 'Tax Deed', 'county': 'BROWARD', 'days': 20, 'auction': '2099-01-02'},
                   {'case': '2026-000001-CA-01', 'addr': 'DUPLICATE OF THE RICH ROW', 'st': 'FC', 'county': 'BROWARD', 'days': 5}], f)
    with open(os.path.join(d, 'pb.md'), 'w', encoding='utf-8') as f:
        f.write('---\ndate: x\n---\n\n# Notes for people\nNOT-FOR-THE-MODEL\n\n---\n\n## What this call sells\nPLAYBOOK-MARKER five minutes with [[the senior advisor]].\n')
    port = free_port()
    proc = subprocess.Popen([sys.executable, os.path.join(d, 'call_coach_server.py')], cwd=d,
                            env=dict(os.environ, COACH_PORT=str(port), DEEPGRAM_API_KEY='test-not-a-real-key',
                                     VENICE_API_KEY='test-ai-key', COACH_PLAYBOOK=os.path.join(d, 'pb.md'), COACH_AI_URL='http://127.0.0.1:%d/v1' % fake.server_address[1]),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        h = {}
        for _ in range(50):
            try:
                h = call(port, '/api/health')
                break
            except Exception:
                time.sleep(0.1)
        chk(h.get('ok') and h.get('leads') == 4, 'server up from a temp copy: 1 rich lead and 3 from the other lane files, the duplicate case counted once: %s' % h.get('leads'))
        lp = (call(port, '/api/lead?q=CACE-26-999999').get('matches') or [{}])[0]
        chk(lp.get('lane') == 'filing' and lp.get('days') is None and lp.get('equity') is None and lp.get('owners') == 'ROE RICHARD',
            'a new filing opens in the coach: its lane, no sale date, and no unverified number: %s' % {k: lp.get(k) for k in ('lane', 'days', 'equity')})
        chk((call(port, '/api/lead?q=BAL-000042').get('matches') or [{}])[0].get('lane') == 'investor'
            and (call(port, '/api/lead?q=TD-55').get('matches') or [{}])[0].get('lane') == 'taxdeed', 'the investor and tax sale lanes open too')
        dup = (call(port, '/api/lead?q=2026-000001-CA-01').get('matches') or [{}])
        chk(len(dup) == 1 and dup[0].get('address', '').startswith('100 NW'), 'a case in two files opens the rich row')
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
        talk = [{'spk': 'you', 'text': 'This is Alejandro.'}, {'spk': '', 'text': 'We talked to the bank already.'}]
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
        AI['reply'] = 'HEARD: we are talking it out with an attorney\nTYPE: objection\nSTEP: booked\nSAY: Keep your attorney. What has he told you about the sale date?'
        j3 = suggest(talk)[1]
        chk(j3.get('type') == 'objection' and j3.get('heard') == 'we are talking it out with an attorney' and j3.get('step') == 'close',
            'the model reports what the homeowner said and what kind of thing it was; an objection is never the booked step: %s' % j3)
        said = 'Keeping it for your wife and kids, I get that. Has anybody explained what happens to your balance?'
        req = urllib.request.Request('http://127.0.0.1:%d/api/suggest' % port, headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'lines': [{'spk': '', 'text': 'Keeping it for your wife and kids I get that has anybody explained what happens to your balance'},
                                                                {'spk': '', 'text': 'No, nobody did.'}], 'asked': [said]}).encode())
        urllib.request.urlopen(req, timeout=15).read()
        sent_tx = AI['seen'][-1]['body']['messages'][1]['content']
        chk(sent_tx.startswith('CALLER: Keeping it for your wife') and '?: No, nobody did.' in sent_tx,
            'one microphone: a line that is the coach\'s own earlier line read out goes to the model as the caller')
        chk('LISTEN FIRST' in AI['seen'][-1]['body']['messages'][0]['content'] and 'TALK LIKE A PERSON' in AI['seen'][-1]['body']['messages'][0]['content'],
            'the model is told to find the homeowner\'s words first and to talk like a person')
        sys_p = AI['seen'][-1]['body']['messages'][0]['content']
        chk('PLAYBOOK-MARKER five minutes with the senior advisor.' in sys_p and 'NOT-FOR-THE-MODEL' not in sys_p,
            'the playbook note goes to the model: only the part under the last rule, wiki-links flattened')
        req = urllib.request.Request('http://127.0.0.1:%d/api/suggest' % port, headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'lines': talk, 'days': 12}).encode())
        urllib.request.urlopen(req, timeout=15).read()
        chk('the sale is set in about two weeks' in AI['seen'][-1]['body']['messages'][0]['content'],
            'the court sale reaches the model as words, never a date')
        chk(h.get('playbook', 0) > 20, 'health says how much playbook the AI reads: %s' % h.get('playbook'))
        AI['reply'] = 'HEARD: tomorrow at ten\nTYPE: time\nSTEP: booked\nSAY: Tomorrow at 10:15, got it. Save my number for me.'
        j4 = suggest(talk)[1]
        chk(j4.get('step') == 'booked' and '10:15' in j4.get('line', ''), 'the time they gave can be said back; that is the booking: %s' % j4)
        AI['reply'] = 'HEARD: tomorrow\nTYPE: time\nSTEP: booked\nSAY: Tomorrow, and you keep $5,000.'
        chk(suggest(talk)[1].get('line') == '', 'a dollar amount is dropped even on a booking line')
        req = urllib.request.Request('http://127.0.0.1:%d/api/suggest' % port, headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'lines': talk, 'days': 12, 'lane': 'filing'}).encode())
        urllib.request.urlopen(req, timeout=15).read()
        lane_p = AI['seen'][-1]['body']['messages'][0]['content']
        chk('NO sale date has been set' in lane_p and 'the sale is set in about' not in lane_p, 'a new filing tells the model there is no sale date, and none is passed')
        req = urllib.request.Request('http://127.0.0.1:%d/api/suggest' % port, headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'lines': talk, 'lane': 'investor'}).encode())
        urllib.request.urlopen(req, timeout=15).read()
        chk('Talk business, not family' in AI['seen'][-1]['body']['messages'][0]['content'], 'the investor lane gets the investor conversation')

        # The call wrap-up: a draft of the log entry from the whole transcript.
        def wrap(hdr=None):
            req = urllib.request.Request('http://127.0.0.1:%d/api/wrapup' % port, data=json.dumps({'lines': talk, 'lane': 'auction'}).encode(),
                                         headers=dict({'Content-Type': 'application/json'}, **(hdr or {})))
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as ex:
                return ex.code, {}
        AI['reply'] = ('OUTCOME: appointment\nWANT: keep\nOBJECTION: has an attorney\nWHEN: tomorrow at nine\n'
                       'NOTE: Wants to keep the house, attorney is filing next week. Advisor call set for tomorrow morning.')
        st, w = wrap({'Origin': own, 'Sec-Fetch-Site': 'same-origin'})
        chk(st == 200 and w == {'outcome': 'appointment', 'want': 'keep', 'objection': 'has an attorney', 'when': 'tomorrow at nine',
                                'note': 'Wants to keep the house, attorney is filing next week. Advisor call set for tomorrow morning.'},
            'the wrap-up drafts outcome, want, pushback, time and note: %s' % w)
        AI['reply'] = 'OUTCOME: sold the house to us\nNOTE: x'
        chk(wrap()[1].get('outcome') == '', 'an outcome the system does not have is not a draft')
        chk(wrap({'Origin': 'https://example.com', 'Sec-Fetch-Site': 'cross-site'})[0] == 403, 'another site cannot ask for a wrap-up')
        # NEVER FOLD: a goodbye, or a plain "no" read as a stop request, is asked again and never shown.
        no = [{'spk': 'you', 'text': 'Is later today better, or first thing tomorrow?'}, {'spk': '', 'text': 'Really not interested, man.'}]
        fold = 'HEARD: Really not interested, man.\nTYPE: objection\nSTEP: close\nSAY: No problem, I will leave you be. Take care.'
        work = 'HEARD: Really not interested, man.\nTYPE: objection\nSTEP: close\nSAY: Sure. What is it you are not interested in, though, keeping the place?'
        n0 = len(AI['seen']); AI['reply'] = [fold, work]
        def suggest2(lines):
            req = urllib.request.Request('http://127.0.0.1:%d/api/suggest' % port, headers={'Content-Type': 'application/json'},
                                         data=json.dumps({'lines': lines, 'asked': ["So you're not interested in saving your property, or the equity you've got in it?"]}).encode())
            return json.loads(urllib.request.urlopen(req, timeout=15).read())
        j5 = suggest2(no)
        chk(j5.get('line', '').startswith('Sure. What is it') and len(AI['seen']) == n0 + 2 and 'has NOT asked to stop' in AI['seen'][-1]['body']['messages'][0]['content'],
            'a line that gives up on the call is not shown: the model is asked again and told nobody said stop: %s' % j5.get('line'))
        AI['reply'] = [fold, fold]
        chk(suggest2(no).get('line') == '', 'a second goodbye shows nothing rather than ending the call')
        AI['reply'] = ['HEARD: No.\nTYPE: stop\nSTEP: close\nSAY: You got it, I will take you off the list.', work]
        j6 = suggest([{'spk': '', 'text': 'No.'}])[1]
        chk(j6.get('type') == 'objection' and 'off the list' not in j6.get('line', ''), 'a plain no is never a stop request: %s' % j6)
        n1 = len(AI['seen']); AI['reply'] = 'HEARD: stop calling me\nTYPE: stop\nSTEP: close\nSAY: You got it, you are off the list. Take care.'
        j7 = suggest([{'spk': '', 'text': 'Stop calling me, take me off your list.'}])[1]
        chk(j7.get('type') == 'stop' and 'off the list' in j7.get('line', '') and len(AI['seen']) == n1 + 1, 'a real stop request is honored at once: %s' % j7)
        # The challenge: the first "not interested" of a call gets his question, whatever the model wrote.
        AI['reply'] = work
        j8 = suggest(no)[1]
        chk(j8.get('line') == "So you're not interested in saving your property, or the equity you've got in it?" and j8.get('type') == 'objection',
            'the first "not interested" gets the challenge question: %s' % j8.get('line'))
        req = urllib.request.Request('http://127.0.0.1:%d/api/suggest' % port, headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'lines': no, 'asked': [j8.get('line')]}).encode())
        j9 = json.loads(urllib.request.urlopen(req, timeout=15).read())
        chk(j9.get('line', '').startswith('Sure. What is it'), 'the second "not interested" gets a different angle from the model: %s' % j9.get('line'))
        AI['reply'] = 'HEARD: x\nTYPE: stop\nSTEP: close\nSAY: You are off the list. Take care.'
        j10 = suggest([{'spk': '', 'text': 'I am not interested, stop calling me.'}])[1]
        chk(j10.get('type') == 'stop' and 'not interested in saving' not in j10.get('line', ''), 'a stop request is never challenged: %s' % j10.get('line'))
        # The plan fact: when they name a plan, the matching fact goes to the model for that line, once.
        AI['reply'] = work
        suggest([{'spk': 'you', 'text': 'What does taken care of look like?'}, {'spk': '', 'text': 'I am filing bankruptcy next week.'}])
        sp1 = AI['seen'][-1]['body']['messages'][0]['content']
        chk('PLAN FACT FOR THIS LINE' in sp1 and 'seven in ten' in sp1, 'bankruptcy named: the model is handed the bankruptcy fact for that line')
        req = urllib.request.Request('http://127.0.0.1:%d/api/suggest' % port, headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'lines': [{'spk': '', 'text': 'Like I said, bankruptcy.'}], 'asked': ['About seven in ten end up back in foreclosure. What is plan B?']}).encode())
        urllib.request.urlopen(req, timeout=15).read()
        chk('PLAN FACT FOR THIS LINE' not in AI['seen'][-1]['body']['messages'][0]['content'], 'a fact already used on the call is not handed over again')
        suggest([{'spk': '', 'text': 'My lawyer is on it.'}])
        chk('ATTORNEY QUESTION FOR THIS LINE' in AI['seen'][-1]['body']['messages'][0]['content'], 'an attorney named: the model is told to ask what he is doing')
        suggest([{'spk': '', 'text': 'Who is this?'}])
        chk('FOR THIS LINE' not in AI['seen'][-1]['body']['messages'][0]['content'], 'no plan named: no fact is pushed')
        # Busy after pushback is a brush-off; "what do I get" needs a real answer.
        AI['reply'] = work
        req = urllib.request.Request('http://127.0.0.1:%d/api/suggest' % port, headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'lines': [{'spk': '', 'text': 'You know what, I am kinda busy at the moment.'}], 'days': 8,
                                                      'asked': ['Five minutes, free. Is later today better, or first thing tomorrow?']}).encode())
        urllib.request.urlopen(req, timeout=15).read()
        sb = AI['seen'][-1]['body']['messages'][0]['content']
        chk('BUSY AFTER YOU ALREADY ASKED' in sb and 'The sale is set in about a week' in sb and 'calls them at that exact time' in sb,
            'busy after the ask: the model is told it is a brush-off, to isolate it, use the sale clock and pin an exact time')
        suggest([{'spk': '', 'text': 'I am busy right now.'}])
        sb2 = AI['seen'][-1]['body']['messages'][0]['content']
        chk('\n\nBUSY. ' in sb2 and 'BUSY AFTER' not in sb2, 'busy as the first thing said: pin an exact time, no isolate')
        suggest([{'spk': '', 'text': 'Well you guys have not even told me what you are offering.'}])
        chk('THEY ASKED WHAT THEY GET' in AI['seen'][-1]['body']['messages'][0]['content'], '"what are you offering" gets two concrete things the advisor does, not "he lays out options"')
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
