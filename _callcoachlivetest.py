"""Live call coach recorder (call_coach_live.html + call_coach_server.py).

Pins: the gate keeps the consent line and the responsibility checkbox verbatim and Start stays
disabled until it is ticked; the page loads nothing from the network; 9 stages, 12 cards, 16 red
flags; no scripted line fires an objection card or a red flag; the common homeowner lines fire the
right card; the guardrail phrases fire; and a full session round-trip through the real server (run
from a temp copy, so nothing lands in the repo) writes audio.webm, transcript.txt and meta.json.
call_sessions/ is gitignored. The engine checks need node; they are skipped, loudly, without it.
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
chk('"Quick thing before we start — I record my calls so I keep your file straight. That okay with you?"' in html,
    'consent line verbatim on the gate')
chk("I read that line and I'm recording this call. I accept responsibility for consent on this line." in html,
    'responsibility checkbox text verbatim')
chk(re.search(r'<button[^>]*id="startbtn"[^>]*disabled', html) is not None, 'Start session is disabled until the box is ticked')
chk('onclick="Coach.start()"' in html and 'onclick="Coach.plain()"' in html, 'gate buttons call Coach.start / Coach.plain')
chk("processed by Google's speech" in html and 'Speakerphone only' in html, 'honest footnotes present')

# --- no network beyond the local server ----------------------------------------------------
chk(not re.search(r'<script[^>]+src=', html) and not re.search(r'<link[^>]+href=', html), 'no external script or stylesheet')
chk(not re.search(r'https?://(?!127\.0\.0\.1)', html), 'no URL other than 127.0.0.1')
fetches = set(re.findall(r"""(?:api|postJSON|fetch)\(\s*'([^']+)""", html))
chk(all(f.startswith('/api/') for f in fetches) and fetches, 'every request goes to this server\'s /api/: %s' % sorted(fetches))
chk('localStorage' not in html and 'indexedDB' not in html, 'no browser storage')

# --- gitignore -----------------------------------------------------------------------------
gi = open(os.path.join(HERE, '.gitignore'), encoding='utf-8').read().splitlines()
chk('call_sessions/' in gi, 'call_sessions/ is gitignored (homeowner audio never committed)')

# --- engine in node ------------------------------------------------------------------------
node = shutil.which('node')
if not node:
    print('SKIP engine checks: node not on PATH')
else:
    eng = re.search(r'<script id="engine">([\s\S]*?)</script>', html).group(1)
    js = r"""
const E = (new Function('module', %s + ';return CoachEngine;'))({});
const F = {first:'Maria', st1:'NW 12th Ave', date:'November 18', sender:'Alejandro'};
const out = {stages:E.STAGES.length, objs:E.OBJECTIONS.length, flags:E.REDFLAGS.length,
             ids:E.OBJECTIONS.map(c=>c.id), scriptFires:[], scriptFlags:[], hits:{}, flags_on:{}};
E.STAGES.forEach(s=>[s.say].concat(s.probes).forEach(l=>{
  const b=E.scoreWindow(E.fill(l,F)); if(b&&b.score>=3) out.scriptFires.push([s.id,b.id,l.slice(0,50)]);
  if(E.redflags(l).length) out.scriptFlags.push(l.slice(0,50)); }));
E.OBJECTIONS.forEach(c=>{ if(E.redflags(c.body).length) out.scriptFlags.push(c.id); });
const said = %s;
Object.keys(said).forEach(t=>{ const b=E.pick(t,{},Date.now()); out.hits[t]=b?b.id:null; });
const st={lastFiredId:'plan',panelOpen:true,lastFire:{},cooldown:{}};
out.openPanelRefire = !!E.pick('I have a lawyer', st, Date.now());
const st2={lastFiredId:null,panelOpen:false,lastFire:{plan:Date.now()},cooldown:{}};
out.within20s = !!E.pick('I have a lawyer', st2, Date.now());
out.after20s = !!E.pick('I have a lawyer', st2, Date.now()+21000);
const st3={lastFiredId:null,panelOpen:false,lastFire:{},cooldown:{plan:Date.now()+45000}};
out.dismissed = !!E.pick('I have a lawyer', st3, Date.now()+30000);
%s.forEach(t=>{ out.flags_on[t]=E.redflags(t).length>0; });
out.fill = E.fill(E.STAGES[1].say, F); out.fillNoDate = E.fill(E.STAGES[1].say, {first:'Maria', st1:'NW 12th Ave'});
console.log(JSON.stringify(out));
"""
    said = {
        'I already have a lawyer': 'plan', "I'm not interested": 'notint', 'just send me something in writing': 'writing',
        "I'm busy, can you call me back": 'busy', "how do I know this isn't a scam": 'legit',
        'how much do you charge': 'price', 'look I gotta go': 'bail',
        'the bank is working with me on a modification': 'bank', "it's too late, there's nothing anybody can do": 'nothing',
        "I'm waiting on my tax refund": 'money', "I'm worn out, I'd rather walk away": 'worth',
        'I already talked to three of you people': 'talked', 'no me interesa': 'notint',
        'the weather is nice today': None, "don't call me again": None, 'stop calling me': None,
    }
    flags_on = ["we'll stop the sale", 'I guarantee it', 'we can stop the sale in 24 hours', 'sign the deed over today',
                'quitclaim it to us', 'our attorneys handle that', 'my supervisor says', 'it\'s 100% safe',
                'I promise', 'we buy houses', 'you pay us a fee', 'there is an upfront fee', 'we place loans']
    src = js % (json.dumps(eng), json.dumps(said), json.dumps(flags_on))
    r = subprocess.run([node, '-e', src], capture_output=True, text=True, timeout=60)
    if r.returncode:
        chk(False, 'engine runs in node: %s' % r.stderr[-400:])
    else:
        o = json.loads(r.stdout)
        chk(o['stages'] == 9, '9 stages (0 consent .. 8 book the advisor)')
        chk(o['objs'] == 12 and o['ids'] == ['notint', 'writing', 'busy', 'plan', 'legit', 'price', 'bail', 'bank',
                                             'nothing', 'money', 'worth', 'talked'], '12 cards, spec ids')
        chk(o['flags'] == 16, '16 red-flag regexes')
        chk(not o['scriptFires'], 'no stage line or probe fires a card: %s' % o['scriptFires'][:3])
        chk(not o['scriptFlags'], 'no scripted line trips a red flag: %s' % o['scriptFlags'][:3])
        for t, want in said.items():
            chk(o['hits'][t] == want, '%r -> %s (got %s)' % (t, want, o['hits'][t]))
        chk(not o['openPanelRefire'], 'a card already on screen does not re-fire')
        chk(not o['within20s'] and o['after20s'], '20 s re-fire debounce')
        chk(not o['dismissed'], 'manual x holds the card for 45 s')
        for t, on in o['flags_on'].items():
            chk(on, 'red flag fires: %r' % t)
        chk('Hi, Maria?' in o['fill'] and 'NW 12th Ave is in pre-foreclosure with a sale date of November 18?' in o['fill'],
            'merge fields fill the opener')
        chk('pre-foreclosure?' in o['fillNoDate'], 'no sale date -> no date clause')

# --- server round-trip from a temp copy ----------------------------------------------------
def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, path, body=None, raw=False):
    data = None
    hdr = {}
    if body is not None:
        data = body if raw else json.dumps(body).encode()
        hdr['Content-Type'] = 'application/octet-stream' if raw else 'application/json'
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data, headers=hdr)
    with urllib.request.urlopen(req, timeout=10) as r:
        ct = r.headers.get('Content-Type', '')
        b = r.read()
        return json.loads(b) if 'json' in ct else b.decode('utf-8', 'replace')


with tempfile.TemporaryDirectory() as d:
    shutil.copy(SERVER, d)
    shutil.copy(PAGE, d)
    with open(os.path.join(d, 'leads_final.json'), 'w', encoding='utf-8') as f:
        json.dump([{'Case #': '2026-000001-CA-01', 'Address': '100 NW 1st Ave, Miami, FL 33128',
                    'owners': 'DOE, JANE', 'AuctionDate': '2099-01-02'}], f)
    port = free_port()
    env = dict(os.environ, COACH_PORT=str(port))
    proc = subprocess.Popen([sys.executable, os.path.join(d, 'call_coach_server.py')], cwd=d, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                h = call(port, '/api/health')
                break
            except Exception:
                time.sleep(0.1)
        else:
            h = {}
        chk(h.get('ok') and h.get('leads') == 1, 'server up from a temp copy with 1 lead')
        chk('Coach.start()' in call(port, '/'), 'server serves call_coach_live.html at /')
        m = call(port, '/api/lead?q=nw+1st').get('matches') or []
        chk(len(m) == 1 and m[0]['case'] == '2026-000001-CA-01', 'lead lookup by address fragment')
        sid = call(port, '/api/session/start', {'case': '2026-000001-CA-01', 'address': m[0]['address'],
                                                'owners': 'DOE, JANE', 'auction': '2099-01-02', 'phone': None})['session_id']
        a = call(port, '/api/session/%s/audio' % sid, b'\x1aE\xdf\xa3' + b'0' * 100, raw=True)
        a = call(port, '/api/session/%s/audio' % sid, b'1' * 50, raw=True)
        chk(a.get('bytes') == 154, 'audio chunks append in order')
        call(port, '/api/session/%s/transcript' % sid, {'lines': [{'t': '00:01', 'text': 'hello'}]})
        e = call(port, '/api/session/%s/end' % sid, {'duration_s': 3, 'stages_visited': ['consent', 'opener'],
                                                     'objections_fired': [], 'redflags_fired': [], 'consent_ack': {'ack': True}})
        sd = os.path.join(d, 'call_sessions', sid)
        chk(all(os.path.exists(os.path.join(sd, n)) for n in ('audio.webm', 'transcript.txt', 'meta.json')),
            'session folder has audio.webm, transcript.txt, meta.json')
        chk(e['meta'].get('consent_ack') == {'ack': True}, 'meta.json records consent_ack')
    finally:
        proc.terminate()
        proc.wait(timeout=5)

print()
print('FAIL %d' % len(bad) if bad else 'ALL OK')
raise SystemExit(1 if bad else 0)
