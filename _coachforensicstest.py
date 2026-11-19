"""Call Coach fixes from the 11-18 forensics on real sessions, tested offline.

The model and the network are faked: urllib.request.urlopen is replaced in-process, the key is a dummy, and
the playbook path points at nothing, so no paid API is ever called and no real key is read. Invented lines only.
Covers: digits that are not figures are spelled out instead of killing the line; a figure gets one re-ask;
one time budget for the whole line (no retry on a refused key, a backoff on a dropped connection, no re-ask
that cannot fit, a too-long line trimmed in code); a repeated line is asked again and then held back; every
ask logs one line with a reason code and no words from the call; a client that hung up does not traceback;
a checkpoint saves the call-so-far without ending it and never overwrites an ended call; and in the page,
a hung request is cut, every outcome is counted, an "unclear" line does not drag the step strip back, and a
checkpoint goes out on pagehide. Needs node for the page checks; skipped, loudly, without it.
"""
import ast
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ['VENICE_API_KEY'] = 'test-not-a-real-key'
os.environ['DEEPGRAM_API_KEY'] = 'test-not-a-real-key'
os.environ['COACH_PLAYBOOK'] = os.path.join(HERE, 'no-such-playbook-for-tests.md')
os.environ['COACH_AI_URL'] = 'http://127.0.0.1:9/never-called'
sys.path.insert(0, HERE)
import call_coach_server as cs  # noqa: E402

bad = []


def chk(c, m, extra=None):
    print(('ok   ' if c else 'FAIL ') + m + ('' if c or extra is None else '  | %s' % (extra,)))
    if not c:
        bad.append(m)


class Fake:
    """Stands in for urlopen: each call takes the next queued reply (a string, an exception, or a callable)."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode('utf-8'))
        self.calls.append({'timeout': timeout, 'body': body, 'url': req.full_url,
                           'auth': req.headers.get('Authorization')})
        rep = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if callable(rep) and not isinstance(rep, str):
            rep = rep(timeout)
        if isinstance(rep, BaseException):
            raise rep
        out = json.dumps({'choices': [{'message': {'content': rep}}]}).encode()

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R(out)

    def user(self, i=-1):
        return self.calls[i]['body']['messages'][1]['content']


def run(replies, lines=None, asked=None, shown='', budget=None):
    fake = Fake(replies)
    stat = {}
    lines = lines or [{'spk': 'you', 'text': 'This is Alejandro with Biscayne.'},
                      {'spk': 'them', 'text': 'We are working it out with the bank ourselves.'}]
    old = cs.AI_BUDGET
    if budget is not None:
        cs.AI_BUDGET = budget
    try:
        with mock.patch.object(cs.urllib.request, 'urlopen', fake), mock.patch.object(cs.time, 'sleep', lambda s: None):
            res = cs.ai_line(lines, asked=asked or [], shown=shown, stat=stat)
    finally:
        cs.AI_BUDGET = old
    return res, stat, fake


def form(say, kind='answer', step='problem'):
    return 'HEARD: x\nTYPE: %s\nSTEP: %s\nSAY: %s' % (kind, step, say)


# ---------------------------------------------------------------- digits that are not figures
sd = cs.spell_digits
chk(sd('It takes 5 minutes, that is all.') == 'It takes five minutes, that is all.', 'a lone small number before a word is spelled out', sd('It takes 5 minutes, that is all.'))
chk(sd('He is there 24/7 for you.') == 'He is there around the clock for you.', '24/7 becomes words')
chk(sd('The 1st step is a call.') == 'The first step is a call.', 'an ordinal becomes a word')
chk(sd('Is it chapter 7?') == 'Is it chapter seven?', 'a small number at the end of a question is spelled out')
for keep in ('You could walk away with $50,000.', 'It jumps 30 to 50 percent.', 'That is 5% more.', 'Call at 10:30.',
             'Maybe 5 grand.', 'A 2.5 rate.', 'Since 2019 it grew.', 'Ten at 4 pm works.'):
    chk(cs._AI_BAD.search(sd(keep)) is not None, 'a real figure still has its digits, so it is still dropped: %s' % keep, sd(keep))

r, st, f = run([form('It takes 5 minutes with him. Later today or first thing tomorrow?', 'objection', 'close')])
chk(r.get('line') == 'It takes five minutes with him. Later today or first thing tomorrow?' and len(f.calls) == 1,
    'a line with "5 minutes" is shown in words, with no second request: %s' % r.get('line'))

r, st, f = run([form('A modification can raise it 30 to 50 percent. What then?'), form('A modification can raise the payment a lot. What then?')])
chk(r.get('line', '').startswith('A modification can raise the payment') and len(f.calls) == 2 and 'number written in digits' in f.user(-1),
    'a figure in digits gets ONE re-ask with the rule spelled out, and the clean answer shows: %s' % r)

r, st, f = run([form('You could walk away with $50,000. Want that?')] * 3)
chk(r == {} and st.get('why') == 'digits' and len(f.calls) == 2, 'a figure twice: nothing shown, why=digits, two requests: %s / %s' % (st.get('why'), len(f.calls)))

# ---------------------------------------------------------------- one clock, retries that make sense
t0 = time.monotonic()
r, st, f = run([socket.timeout('timed out')], budget=3.0)
chk(r == {} and st.get('why') == 'model:timeout' and st.get('err') == 'timeout', 'a model timeout is named, not silent: %s' % st)
chk(all(c['timeout'] <= 3.0 + 1e-6 for c in f.calls) and time.monotonic() - t0 < 2.0,
    'no try is given more time than the line has left: %s' % [round(c['timeout'], 2) for c in f.calls])

r, st, f = run([urllib.error.HTTPError(cs.AI_URL, 401, 'Unauthorized', {}, None)])
chk(r == {} and st.get('err') == 'http401' and st.get('tries') == 1, 'a refused key is not retried (one try, err=http401): %s' % st)

r, st, f = run([urllib.error.URLError(ConnectionRefusedError('refused')), form('Who is helping you with the bank right now?')])
chk(r.get('line', '').startswith('Who is helping') and st.get('tries') == 2, 'a dropped connection is retried once and the line still shows: %s' % st)

r, st, f = run([urllib.error.URLError(socket.timeout('connect timed out'))], budget=2.0)
chk(st.get('err') == 'timeout', 'a connect timeout wrapped in URLError is read as a timeout: %s' % st.get('err'))

clock = {'t': 1000.0}
long_line = ('That makes sense and I hear you. The bank side moves slow and nobody tells you the whole thing. '
             'Every week it sits, more gets added on top of what you owe. People in your spot usually find out '
             'too late that the plan had a hole in it. A lot of folks think the bank will wait for them. It will '
             'not wait, it keeps going on the calendar. What would you do if the date came first?')
chk(len(long_line.split()) > 55, 'the long test line is over fifty-five words')


def slow(timeout):
    clock['t'] += 6.5                      # the first answer used most of the 8 s budget
    return form(long_line, 'objection', 'consequence')


with mock.patch.object(cs.time, 'monotonic', lambda: clock['t']):
    r, st, f = run([slow, form('Short. What then?', 'objection', 'consequence')])
chk(len(f.calls) == 1 and r.get('line', '').endswith('What would you do if the date came first?') and len(r['line'].split()) <= 45,
    'no time for a re-ask: the too-long line is cut to whole sentences in code and keeps its question (%d words, %d requests)' % (len(r.get('line', '').split()), len(f.calls)))

tl = cs.trim_line(long_line, 45)
chk(tl.endswith('?') and len(tl.split()) <= 45 and tl.startswith('That makes sense'), 'trim_line keeps the opening and the ask', tl)
chk(cs.trim_line('Short line. Is that okay?', 45) == 'Short line. Is that okay?', 'trim_line leaves a short line alone')

# ---------------------------------------------------------------- repeats
said = 'If the date comes before the bank answers, what is your backup plan for the house?'
r, st, f = run([form(said), form('Who at the bank actually told you they would stop the sale?')], asked=[said])
chk(r.get('line', '').startswith('Who at the bank') and len(f.calls) == 2 and 'repeated a line' in f.user(-1),
    'a line already given this call is asked again in new words: %s' % r.get('line'))
r, st, f = run([form(said.replace('house', 'home'))] * 3, asked=[said])
chk(r == {} and st.get('why') == 'repeat', 'a second repeat shows nothing and keeps the screen as it is: %s' % st.get('why'))
chk(cs.near_dup('Ten tomorrow morning works.', ['Who is helping you?']) == '', 'different lines are not repeats')

# ---------------------------------------------------------------- the server route: reason codes, logs, hang-ups, checkpoints
tmp = tempfile.mkdtemp()
try:
    cs.SESSIONS = os.path.join(tmp, 'call_sessions')
    os.makedirs(cs.SESSIONS)
    srv = cs.ThreadingHTTPServer(('127.0.0.1', 0), cs.Handler)
    port = srv.server_address[1]
    cs.PORT = port
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def post(path, body, hdr=None):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=json.dumps(body).encode(),
                                     headers=dict({'Content-Type': 'application/json'}, **(hdr or {})))
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    real_urlopen = urllib.request.urlopen
    secret = 'MY-HOUSE-IS-ON-ELM-STREET'
    err = io.StringIO()
    fake = Fake([form('You could walk away with $50,000.')])

    def routed(req, timeout=None, **kw):
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        return fake(req, timeout) if url.startswith(cs.AI_URL) else real_urlopen(req, timeout=timeout, **kw)
    with mock.patch.object(urllib.request, 'urlopen', routed), mock.patch.object(cs.sys, 'stderr', err):
        j = post('/api/suggest', {'lines': [{'spk': 'them', 'text': secret}]},
                 {'Origin': 'http://127.0.0.1:%d' % port, 'Sec-Fetch-Site': 'same-origin'})
    log = err.getvalue()
    chk(j.get('line') == '' and j.get('why') == 'digits', 'a blank answer tells the page why: %s' % j)
    chk('suggest blank why=digits' in log and 'tries=2' in log, 'the log has one line with the reason and the tries: %r' % log.strip()[-160:])
    chk(secret not in log and secret.lower() not in log.lower(), 'the log never carries the words of the call')

    # a page that went away while its reply was written: one line, no traceback
    h = cs.Handler.__new__(cs.Handler)
    h.command, h.path, h.close_connection = 'POST', '/api/suggest?x=1', False
    h.send_response = h.send_header = lambda *a, **k: None
    h.end_headers = lambda: None

    class Gone:
        def write(self, b):
            raise ConnectionAbortedError(10053, 'aborted')
    h.wfile = Gone()
    err = io.StringIO()
    try:
        with mock.patch.object(cs.sys, 'stderr', err):
            h._send(200, {'line': 'x'}, private=True)
        ok = True
    except Exception:
        ok = False
    chk(ok and h.close_connection and 'client gone' in err.getvalue(), 'a hung-up page is one log line, not a traceback')

    sid = post('/api/session/start', {'case': 'TEST-1'})['session_id']
    sd_ = os.path.join(cs.SESSIONS, sid)
    fires = [{'id': 'ai', 'at': '00:05', 'ms': 900, 'line': 'Invented line one.'}]
    c1 = post('/api/session/%s/checkpoint' % sid, {'coach_fires': fires, 'duration_s': 30, 'ai_stats': {'asked': 3, 'shown': 1},
                                                  'lines': [{'t': '00:01', 'text': 'invented words'}]})
    m = json.load(open(os.path.join(sd_, 'meta.json'), encoding='utf-8'))
    chk(c1.get('saved') and m.get('coach_fires') == fires and not m.get('ended_utc') and m.get('ai_stats', {}).get('asked') == 3
        and os.path.exists(os.path.join(sd_, 'transcript.json')),
        'a checkpoint saves the coach record, the ask counts and the transcript without ending the call')
    c2 = post('/api/session/%s/checkpoint' % sid, {'coach_fires': [], 'lines': []})
    m = json.load(open(os.path.join(sd_, 'meta.json'), encoding='utf-8'))
    chk(not c2.get('saved') and m.get('coach_fires') == fires, 'a later checkpoint never shortens what is saved')
    post('/api/session/%s/end' % sid, {'duration_s': 40, 'coach_fires': fires + [{'id': 'ai'}], 'ai_stats': {'asked': 4}, 'consent_ack': True})
    c3 = post('/api/session/%s/checkpoint' % sid, {'coach_fires': fires * 5})
    m = json.load(open(os.path.join(sd_, 'meta.json'), encoding='utf-8'))
    chk(not c3.get('saved') and m.get('ended_utc') and len(m.get('coach_fires')) == 2 and m.get('ai_stats') == {'asked': 4},
        'a checkpoint after the end (the reload beacon) changes nothing; the end keeps its ask counts')
    srv.shutdown()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- the page, in node
src_html = open(os.path.join(HERE, 'call_coach_live.html'), encoding='utf-8').read()
src = src_html[src_html.index('<script>') + 8:src_html.rindex('</script>')]
fast = open(os.path.join(HERE, '_fastcoachtest.py'), encoding='utf-8').read()
HARNESS = [ast.literal_eval(n.value) for n in ast.parse(fast).body
           if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'HARNESS'][0]
head = HARNESS[:HARNESS.index('__SRC__')]
JS = head + r"""
global.addEventListener=function(n,f){ (global._ev=global._ev||{})[n]=f; };
var beacons=[];
Object.defineProperty(global,'navigator',{value:{sendBeacon:function(u,b){ beacons.push(u); return true; }},configurable:true,writable:true});
""" + src + r"""
var C=window.Coach, T=C._test, o={};
function wait(ms){ return new Promise(function(r){ setTimeout(r,ms); }); }
(async function(){
  var S=T.S(), posts=[];
  S.plain=false; S.sid='sid-test';
  function arm(text){ S.finals=' '+text; S.ai.n=0; S.ai.line=''; S.ai.busy=false; S.ai.sentAt=0; S.ai.shownAt=0;
    S.fc.lastWord=Date.now()-5000; S.lines=[{t:'00:01',spk:'them?',text:text}]; }
  /* 1. a request that never answers is cut, and the box can ask again */
  T.setCut(60);
  global.fetch=function(u,opt){ posts.push(String(u)); if(String(u).indexOf('suggest')<0) return Promise.resolve({ok:true,json:function(){ return Promise.resolve({}); }});
    return new Promise(function(res,rej){ opt.signal.addEventListener('abort',function(){ var e=new Error('x'); e.name='AbortError'; rej(e); }); }); };
  arm('my husband deals with the bank, not me, he has been calling them');
  T.aiCheck(); await wait(200);
  o.cut=[S.ai.busy, S.ai.stats.asked||0, S.ai.stats.cut||0];
  /* 2. a blank answer is counted with its reason */
  global.fetch=function(u){ return Promise.resolve({ok:true,json:function(){ return Promise.resolve({line:'',why:'digits'}); }}); };
  arm('we are going to sell it ourselves to my cousin next month probably');
  T.aiCheck(); await wait(60);
  o.blank=S.ai.stats['blank:digits']||0;
  /* 3. a shown line is counted */
  global.fetch=function(u){ return Promise.resolve({ok:true,json:function(){ return Promise.resolve({line:'Who is the buyer you have lined up?',step:'solution',type:'answer',heard:'sell it'}); }}); };
  arm('honestly the plan is my brother is going to lend me the money for it');
  T.aiCheck(); await wait(60);
  o.shown=S.ai.stats.shown||0;
  /* Credit failures retain the unheard turn so retry after replenishment needs no new speech. */
  global.fetch=function(){ return Promise.resolve({ok:true,json:function(){ return Promise.resolve({line:'',why:'model:http402'}); }}); };
  arm('my brother is going to lend me the money for it');
  T.aiCheck(); await wait(60);
  o.credit=[S.ai.n, els.aistatus.textContent, els.airetry.style.display];
  global.fetch=function(){ return Promise.resolve({ok:true,json:function(){ return Promise.resolve({line:'What happens if that money falls through?',step:'problem',type:'answer',heard:'my brother'}); }}); };
  C.retryAI(); await wait(220);
  o.recovered=S.ai.line;
  clearTimeout(S.ai.t);
  /* A shared screen with no prospect audio must expose the one-mic recovery during the call. */
  S.lines=[]; S.finals=''; S.ai.n=0; S.ai.line=''; S.sid=null;
  T.dgTwo(true);
  for(var k=0;k<3;k++) T.dg({is_final:true,channel_index:[0,2],channel:{alternatives:[{transcript:'Test microphone sentence '+k+'.'}]}});
  o.micWarning=[els.aimic.style.display,els.aistatus.textContent];
  C.useMicCoach();
  o.micRecovered=[S.dgTwo, S.lines.some(function(l){ return l.spk==='you'; }), els.aimic.style.display];
  var micLines=S.lines.length;
  T.dg({is_final:true,channel_index:[1,2],channel:{alternatives:[{transcript:'Shared audio returned.'}]}});
  o.ignoredShared=S.lines.length===micLines;
  T.dg({is_final:true,channel_index:[0,2],channel:{alternatives:[{transcript:'A microphone answer.'}]}});
  o.heardMic=S.lines.length===micLines+1;
  S.sid='sid-test';
  /* 4. unclear does not pull the strip back; a real answer may */
  S.ai.at=7; T.aiSteps({step:'close',type:'unclear'}); o.st1=S.ai.at;
  T.aiSteps({step:'close',type:'objection'}); o.st2=S.ai.at;
  /* 5. checkpoint: by fetch, not after the end, and by beacon on pagehide */
  var ck=[]; global.fetch=function(u,opt){ ck.push([String(u), JSON.parse(opt.body)]); return Promise.resolve({ok:true,json:function(){ return Promise.resolve({}); }}); };
  S.lines=[{t:'00:01',text:'invented'}]; S.fc.fires=[{id:'ai',line:'x'}]; S.ended=false; S.rolling=false;
  o.ck1=T.ckSave(false);
  o.ckBody=ck.length?[ck[0][0], !!ck[0][1].coach_fires, !!ck[0][1].ai_stats, (ck[0][1].lines||[]).length]:null;
  S.ended=true; o.ck2=T.ckSave(false); S.ended=false;
  S.rolling=true; o.ck3=T.ckSave(false); S.rolling=false;
  if(global._ev&&global._ev.pagehide) global._ev.pagehide();
  o.beacon=beacons.slice();
  console.log('@@'+JSON.stringify(o));
})().catch(function(e){ console.log('@@'+JSON.stringify({err:String(e&&e.stack||e)})); });
"""

node = shutil.which('node')
if not node:
    print('SKIP page checks: node not on PATH')
else:
    r = subprocess.run([node, '-'], input=JS, capture_output=True, text=True, timeout=90, encoding='utf-8')
    line = [x for x in (r.stdout or '').splitlines() if x.startswith('@@')]
    o = json.loads(line[-1][2:]) if line else {'err': ((r.stderr or '') + (r.stdout or ''))[-600:]}
    chk(not o.get('err'), 'the page script runs in node', o.get('err'))
    if not o.get('err'):
        chk(o['cut'] == [False, 1, 1], 'a request that never answers is cut and the box is free to ask again (busy, asked, cut)', o['cut'])
        chk(o['blank'] == 1, 'a blank answer is counted with its reason')
        chk(o['shown'] == 1, 'a shown line is counted')
        chk(o['credit'][0] == 0 and 'credits' in o['credit'][1] and o['credit'][2] == '',
            'credit refusal shows its cause and retains the turn for retry', o['credit'])
        chk(o['recovered'] == 'What happens if that money falls through?',
            'retry recovers the same turn after credits return without more speech', o['recovered'])
        chk(o['micWarning'][0] == '' and 'Only your microphone' in o['micWarning'][1],
            'missing prospect audio exposes a recovery button', o['micWarning'])
        chk(o['micRecovered'] == [False, False, 'none'],
            'microphone recovery removes hard caller-only labels and clears the warning button', o['micRecovered'])
        chk(o['ignoredShared'] and o['heardMic'],
            'microphone recovery hears only channel zero if shared audio resumes')
        chk(o['st1'] == 7 and o['st2'] == 6, 'an unclear line keeps the step strip where it is; an objection may move it', [o['st1'], o['st2']])
        chk(o['ck1'] is True and o['ckBody'] == ['/api/session/sid-test/checkpoint', True, True, 1],
            'the page checkpoints the coach record, the ask counts and the transcript', o['ckBody'])
        chk(o['ck2'] is False and o['ck3'] is False, 'no checkpoint after the end or while the next call is being set up')
        chk(o['beacon'] == ['/api/session/sid-test/checkpoint'], 'closing or reloading the tab sends the checkpoint by beacon', o['beacon'])
    chk("setInterval(function(){ ckSave(false); },60000);" in src, 'the call so far is saved every minute')

print()
print('FAIL %d' % len(bad) if bad else 'ALL OK')
sys.exit(1 if bad else 0)
