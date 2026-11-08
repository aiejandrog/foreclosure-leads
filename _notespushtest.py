"""F-wire-02 (page half): Call Mode must say so, and keep trying, when a Do Not Contact has not reached the bridge.

Before: a Do Not Contact tapped in Call Mode was saved on the phone and POSTed once to /notes
(callNotesPush). If that POST rejected, the bridge answered non-2xx, or the body said ok:false, nothing
was shown and nothing was retried, so the opt-out could stay off optouts.json with nobody the wiser.

Pins (a)-(g), run through node against the notes-push block extracted from call_mode.py (from
`var _cnT` to the text-ledger comment, so a helper added inside the block is picked up):
  (a) fetch rejects, a DNC lead is not yet acknowledged -> strip 'notes-unsent-strip' mentioning Do Not
      Contact, retries at 4000, 8000, 12000 ms, then none (strip stays)
  (b) fetch rejects, no DNC lead in notes -> no strip, no retry
  (c) 200 {ok:true} -> strip removed, acknowledged set (fcDncAck) equals the DNC case ids, no retry
  (d) HTTP 500, or 200 {ok:false} -> treated as a failure, same as (a)
  (e) an earlier-acknowledged DNC plus a later non-DNC failure -> no strip (no nagging)
  (f) the body handed to bridgeFetch is byte-identical to the old code's body, success or failure
  (g) after the ladder is spent, the next saveNotes (callNotesPushSoon) starts a fresh ladder

Invented leads only (2099-* cases, 555 numbers). No network, nothing listens on a port.
Run: DEALFLOW_DIR=<temp> python _notespushtest.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_T = tempfile.mkdtemp(prefix='notespush_')
os.environ.setdefault('DEALFLOW_DIR', _T)
NODE = os.environ.get('NODE_BIN', 'node')

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:500]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
a = SRC.index('var _cnT = null')
b = SRC.index('/* The text ledger (text_sent.json)')
BLOCK = SRC[a:b]

HARN = r"""
var __store = {}, __timers = [], __calls = [], __doc = {};
var localStorage = {getItem:function(k){ return Object.prototype.hasOwnProperty.call(__store,k)?__store[k]:null; },
  setItem:function(k,v){ __store[k]=String(v); }, removeItem:function(k){ delete __store[k]; }};
var realST = setTimeout;
setTimeout = function(f, ms){ __timers.push({f:f, ms:ms, off:false}); return __timers.length; };
clearTimeout = function(id){ if(id && __timers[id-1]) __timers[id-1].off = true; };
function pending(){ return __timers.filter(function(t){ return !t.off && !t.done; }); }
function fire(){ var p = pending(); if(!p.length) return false; p[0].done = true; p[0].f(); return true; }
var document = {
  body: {kids: [], appendChild: function(e){ e.parentNode = this; this.kids.push(e); return e; },
         removeChild: function(e){ this.kids = this.kids.filter(function(k){ return k !== e; }); e.parentNode = null; }},
  createElement: function(t){ return {tag:t, id:'', style:{}, textContent:'', setAttribute:function(){}, parentNode:null}; },
  getElementById: function(id){ return document.body.kids.filter(function(k){ return k.id === id; })[0] || null; }
};
function strip(){ return document.getElementById('notes-unsent-strip'); }
function today(){ return '2026-11-08'; }
var notes = {};
var MODE = 'reject';
function resp(status, body){ return {status:status, ok: status>=200 && status<300, clone:function(){ return this; },
  json:function(){ return Promise.resolve(body); }}; }
function bridgeFetch(path, opts){
  __calls.push({path:path, opts:opts});
  if(MODE === 'reject') return Promise.reject(new TypeError('Failed to fetch'));
  if(MODE === '500') return Promise.resolve(resp(500, {ok:false}));
  if(MODE === 'okfalse') return Promise.resolve(resp(200, {ok:false, err:'ledger'}));
  return Promise.resolve(resp(200, {ok:true}));
}
function flush(){ return new Promise(function(r){ realST(r, 8); }); }
""" + BLOCK + r"""
var DNC1 = {status:'DO NOT CONTACT', no:'hard', optout:'2026-11-08', note:''};
var PH1 = {status:'DO NOT CONTACT', optout:'2026-11-08', note:''};
function out(o){ console.log(JSON.stringify(o)); process.exit(0); }
"""


def run(tag, body):
    js = HARN + '\n(async function(){\n' + body + '\n})();\n'
    path = os.path.join(_T, tag + '.js')
    io.open(path, 'w', encoding='utf-8').write(js)
    p = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        return None, p.stderr[-600:]
    try:
        return json.loads(p.stdout.strip().splitlines()[-1]), ''
    except Exception:  # noqa
        return None, 'bad output: ' + p.stdout[-300:] + p.stderr[-300:]


def ladder_body(mode):
    # one DNC lead (case key + person key), nothing acknowledged; drive the whole ladder
    return """
MODE = '%s';
notes = {'2099-000901-CA-01': DNC1, '#3055550901': PH1, '2099-000903-CA-01': {status:'Called', note:'x'}};
callNotesPush(); await flush();
var s1 = strip(), t1 = s1 ? s1.textContent : null;
var ms = [];
for(var i = 0; i < 6; i++){
  var p = pending(); if(!p.length) break;
  ms.push(p[0].ms); fire(); await flush();
}
out({text1:t1, strip_after: !!strip(), ms:ms, left: pending().length, calls: __calls.length,
     ack: __store.fcDncAck || null});
""" % mode


# ---------------------------------------------------------------- (a) and (d)
print('\n(a) fetch rejects, unacknowledged DNC')
for tag, mode, label in (('a', 'reject', 'rejects'), ('d1', '500', 'HTTP 500'), ('d2', 'okfalse', '200 with ok:false')):
    r, err = run(tag, ladder_body(mode))
    if r is None:
        rec('%s: node ran' % label, False, err)
        continue
    rec('%s: strip appears with Do Not Contact wording' % label,
        bool(r['text1']) and 'Do Not Contact' in r['text1'] and 'Not backed up to the bridge' in r['text1'], r)
    rec('%s: strip says 1 change (the case, not its person keys)' % label,
        bool(r['text1']) and r['text1'].split('bridge:')[1].strip().startswith('1 Do Not Contact change(s)'), r)
    rec('%s: retries at 4000, 8000, 12000 then none' % label, r['ms'] == [4000, 8000, 12000], r['ms'])
    rec('%s: nothing left scheduled, strip still up' % label, r['left'] == 0 and r['strip_after'] is True, r)
    rec('%s: 4 posts in total (1 + 3 retries)' % label, r['calls'] == 4, r['calls'])
    rec('%s: nothing acknowledged' % label, r['ack'] in (None, '[]'), r['ack'])

# ---------------------------------------------------------------- (b) no DNC lead
print('\n(b) fetch rejects, no DNC lead')
r, err = run('b', """
MODE = 'reject';
notes = {'2099-000903-CA-01': {status:'Called', note:'x'}, '2099-000904-CA-01': {status:'Interested', note:''}};
callNotesPush(); await flush();
out({strip: !!strip(), left: pending().length, busy: _cnBusy, calls: __calls.length});
""")
rec('b: no strip, no retry, busy flag reset', r is not None and r['strip'] is False and r['left'] == 0
    and r['busy'] is False and r['calls'] == 1, r or err)

# ---------------------------------------------------------------- (c) success removes the strip, acks
print('\n(c) success')
r, err = run('c', """
MODE = 'reject';
notes = {'2099-000901-CA-01': DNC1, '#3055550901': PH1, '2099-000903-CA-01': {status:'Called', note:'x'}};
callNotesPush(); await flush();
var had = !!strip(), n1 = pending().length;
MODE = 'ok';
fire(); await flush();
out({had:had, n1:n1, strip: !!strip(), left: pending().length,
     ack: JSON.parse(__store.fcDncAck || '[]').sort(), busy:_cnBusy});
""")
rec('c: strip was up, then removed on 200 ok:true', r is not None and r['had'] is True and r['strip'] is False, r or err)
rec('c: acknowledged set equals the DNC ids (no non-DNC case)', r is not None and
    r['ack'] == ['#3055550901', '2099-000901-CA-01'], r or err)
rec('c: no retry left after success', r is not None and r['left'] == 0 and r['busy'] is False, r or err)
r, err = run('c2', """
MODE = 'ok';
notes = {'2099-000901-CA-01': DNC1};
callNotesPush(); await flush();
out({strip: !!strip(), left: pending().length, ack: JSON.parse(__store.fcDncAck || '[]')});
""")
rec('c: a plain first-try success acknowledges and shows nothing', r is not None and r['strip'] is False
    and r['left'] == 0 and r['ack'] == ['2099-000901-CA-01'], r or err)

# ---------------------------------------------------------------- (e) no nagging
print('\n(e) old acknowledged DNC, later non-DNC failure')
r, err = run('e', """
MODE = 'reject';
__store.fcDncAck = JSON.stringify(['2099-000902-CA-01']);
notes = {'2099-000902-CA-01': DNC1, '2099-000905-CA-01': {status:'Called', note:'later'}};
callNotesPush(); await flush();
out({strip: !!strip(), left: pending().length, ack: __store.fcDncAck});
""")
rec('e: no strip, no retry, ack unchanged', r is not None and r['strip'] is False and r['left'] == 0
    and json.loads(r['ack']) == ['2099-000902-CA-01'], r or err)

# ---------------------------------------------------------------- (f) payload unchanged
print('\n(f) payload byte-identical')
for tag, mode in (('f1', 'reject'), ('f2', 'ok')):
    r, err = run(tag, """
MODE = '%s';
__store.fcDevice = 'dev-2099';
notes = {'2099-000901-CA-01': DNC1, '#3055550901': PH1, '2099-000903-CA-01': {status:'Called', note:'x'}};
var want = JSON.stringify({_dealflow_notes: 1, exported: today(), device: 'dev-2099', notes: notes});
callNotesPush(); await flush();
var c = __calls[0];
out({path:c.path, method:c.opts.method, ct:c.opts.headers['Content-Type'], same: c.opts.body === want,
     keys: Object.keys(JSON.parse(c.opts.body))});
""" % mode)
    rec('f (%s): same body and request shape as before' % mode, r is not None and r['same'] is True
        and r['path'] == '/notes' and r['method'] == 'POST' and r['ct'] == 'application/json'
        and r['keys'] == ['_dealflow_notes', 'exported', 'device', 'notes'], r or err)

# ---------------------------------------------------------------- (g) next save restarts the ladder
print('\n(g) next save restarts the ladder')
r, err = run('g', """
MODE = 'reject';
notes = {'2099-000901-CA-01': DNC1};
callNotesPush(); await flush();
for(var i = 0; i < 6; i++){ if(!fire()) break; await flush(); }
var spent = pending().length;
callNotesPushSoon();
var first = pending().map(function(t){ return t.ms; });
var ms = [];
for(var j = 0; j < 8; j++){ var p = pending(); if(!p.length) break; ms.push(p[0].ms); fire(); await flush(); }
out({spent:spent, first:first, ms:ms, strip: !!strip()});
""")
rec('g: ladder spent, then saveNotes -> debounce 4000, then a fresh 4000/8000/12000',
    r is not None and r['spent'] == 0 and r['first'] == [4000] and r['ms'] == [4000, 4000, 8000, 12000]
    and r['strip'] is True, r or err)

print('\n%s' % ('ALL PASS' if not fails else 'FAILED: %d' % len(fails)))
sys.exit(1 if fails else 0)
