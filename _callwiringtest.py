"""Call Mode / board <-> send bridge wiring (2026-10-30, after the #246 device-key merge).

Pins, each one failing on b3c407f:
  1. DEVICE KEY. One helper (bridgeFetch) attaches X-Dealflow-Key from localStorage (dfBridgeKey) and
     honours dfBridgeBase; with no key it adds NO header and uses http://127.0.0.1:8823 exactly as
     before. A 401 blocked 'device_key' becomes a plain message. The helper block is byte-identical in
     call_mode.py's page and tracker_template.html, no raw fetch to 8823 is left in either (except the
     unload sendBeacon, which cannot carry a header), and the two popup scripts the board writes
     (outcome popup, Morning Worker) get the helper through _bridgeSrc().
  2. The bridge allows the header in a browser preflight (send_server CORS list), and the 401 shape the
     helper keys on is the one send_server sends.
  3. CALL MODE -> DISK. saveNotes() schedules a debounced POST /notes (notes only; no workerLog /
     sentArchive keys, which the server would read as a deletion). Before this the call page never
     posted /notes at all.
  4. TEXT LEDGER. "Composer opened" and "Yes, it sent" post /text (confirmed false / true) with the
     board's body shape. Before this a text sent from Call Mode never reached text_sent.json.
  5. Every Call Mode bridge route (text/check, health, send) carries the key and shows the plain
     message on a 401.

Invented leads only; JS is extracted by name from the built page and run under node with a fake
localStorage and a fake fetch. Nothing listens on a port, nothing is sent.
Run: DEALFLOW_DIR=<temp> python _callwiringtest.py
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='callwire_')
os.environ.setdefault('DEALFLOW_DIR', _T)
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')

import call_mode  # noqa: E402

fails = []
NODE = os.environ.get('NODE_BIN', 'node')


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


tpl = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 0, "shipped": 0}')
srv = io.open(os.path.join(HERE, 'send_server.py'), encoding='utf-8').read()

PLAIN = 'This device needs a bridge key; on the bridge PC run python bridge_auth.py add <name>'
S, E = 'BRIDGE-KEY-START', 'BRIDGE-KEY-END'


def block(src):
    if S not in src or E not in src:
        return ''
    a = src.index(S)
    return src[src.rindex('/*', 0, a):src.index(E) + len(E)] + ' */'


def norm(s):
    return re.sub(r'\s+', ' ', s).strip()


def fn(name, end='\n}\n'):
    a = html.find('function %s(' % name)
    if a < 0:
        return ''
    return html[a:html.index(end, a) + len(end)]


def run_node(js, tag):
    path = os.path.join(_T, tag + '.js')
    io.open(path, 'w', encoding='utf-8').write(js)
    p = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        return None, p.stderr[-500:]
    try:
        return json.loads(p.stdout.strip().splitlines()[-1]), ''
    except Exception as e:  # noqa
        return None, 'bad output: ' + p.stdout[-300:]


# ---------------------------------------------------------------- 1. one helper, two pages
print('\nTHE HELPER')
hc, ht = block(html), block(tpl)
rec('helper block present in the Call Mode page', bool(hc))
rec('helper block present in tracker_template.html', bool(ht))
rec('the two copies are identical', bool(hc) and norm(hc) == norm(ht))
rec('header name matches bridge_auth.KEY_HEADER', "'X-Dealflow-Key'" in hc and "KEY_HEADER = 'X-Dealflow-Key'" in
    io.open(os.path.join(HERE, 'bridge_auth.py'), encoding='utf-8').read())
rec('server preflight allows the header', re.search(r"Access-Control-Allow-Headers', '[^']*X-Dealflow-Key", srv) is not None)
rec('server 401 shape is blocked device_key', "{'ok': False, 'blocked': 'device_key'" in srv)

HARN = r"""
var __store = {}, __calls = [], __toasts = [], __resp = null;
var localStorage = {getItem:function(k){ return Object.prototype.hasOwnProperty.call(__store,k)?__store[k]:null; },
                    setItem:function(k,v){ __store[k]=String(v); }, removeItem:function(k){ delete __store[k]; }};
var window = globalThis;
function toast(t,o){ __toasts.push(t); }
globalThis.fetch = function(url, opts){ __calls.push({url:url, opts:opts||{}});
  var r = __resp || {status:200, body:{ok:true}};
  return Promise.resolve(new Response(JSON.stringify(r.body), {status:r.status, headers:{'Content-Type':'application/json'}})); };
"""

js = HARN + hc + r"""
var out = {};
(async function(){
  // no key: no header, loopback address, exactly as before
  await bridgeFetch('/health');
  out.nokey = {url: __calls[0].url, hdr: Object.keys(__calls[0].opts.headers||{})};
  // key + caller headers kept, caller opts kept
  __store.dfBridgeKey = '  k-123  ';
  await bridgeFetch('/notes', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
  var c = __calls[1];
  out.key = {url: c.url, method: c.opts.method, ct: c.opts.headers['Content-Type'], k: c.opts.headers['X-Dealflow-Key'], body: c.opts.body};
  // base address
  __store.dfBridgeBase = 'https://laptop.tail1234.ts.net/';
  await bridgeFetch('/send');
  out.base = __calls[2].url;
  __store.dfBridgeBase = 'javascript:alert(1)';
  await bridgeFetch('/send');
  out.badbase = __calls[3].url;
  delete __store.dfBridgeBase;
  // 401 device_key -> plain message, once
  __resp = {status:401, body:{ok:false, blocked:'device_key', err:'a device key is required (header X-Dealflow-Key)'}};
  var r1 = await bridgeFetch('/text/check', {method:'POST'}); var j1 = await r1.json();
  var r2 = await bridgeFetch('/text/check', {method:'POST'});
  out.denied = {status:r1.status, j:j1, toasts:__toasts.slice()};
  // other 401 passes through untouched
  __resp = {status:401, body:{ok:false, err:'something else'}};
  var r3 = await bridgeFetch('/x'); var j3 = await r3.json();
  out.other = {status:r3.status, err:j3.err};
  out.leak = JSON.stringify(__toasts).indexOf('k-123') >= 0;
  console.log(JSON.stringify(out));
})();
"""
o, err = run_node(js, 'helper') if hc else (None, 'no helper')
rec('helper runs under node', o is not None, err)
o = o or {}
rec('no key: no header, 127.0.0.1:8823 (laptop unchanged)',
    o.get('nokey') == {'url': 'http://127.0.0.1:8823/health', 'hdr': []}, o.get('nokey'))
rec('key attached (trimmed), caller headers and opts kept',
    o.get('key') == {'url': 'http://127.0.0.1:8823/notes', 'method': 'POST', 'ct': 'application/json',
                     'k': 'k-123', 'body': '{}'}, o.get('key'))
rec('stored bridge address used, trailing slash dropped', o.get('base') == 'https://laptop.tail1234.ts.net/send', o.get('base'))
rec('a non-http address is ignored', o.get('badbase') == 'http://127.0.0.1:8823/send', o.get('badbase'))
d = o.get('denied') or {}
rec('401 device_key: callers read the plain message in err',
    d.get('status') == 401 and (d.get('j') or {}).get('err') == PLAIN and (d.get('j') or {}).get('blocked') == 'device_key', d)
rec('401 device_key: shown once, not on every request', d.get('toasts') == [PLAIN], d.get('toasts'))
rec('another 401 is left alone', o.get('other') == {'status': 401, 'err': 'something else'}, o.get('other'))
rec('the key is never shown or logged', o.get('leak') is False)

# the key must never be baked, committed or logged
rec('the only write of the key is the settings prompt', hc.count("localStorage.setItem('dfBridgeKey'") == 1)
rec('no console/log call sees the key', 'console.' not in hc and 'logErr' not in hc)

# ---------------------------------------------------------------- 1b. no raw fetch left
print('\nNO RAW FETCH TO THE BRIDGE')
# /refresh is a read-only progress GET the bridge leaves open (refresh_status.py), so W.fetch(.../refresh) stays
raw_cm = re.findall(r"""(?<![\w.])fetch\((?:'|")http://127\.0\.0\.1:8823""", html)
rec('Call Mode page: the fetch wrapper is installed once at load', '_bridgeInstall();' in hc and len(raw_cm) == 4, len(raw_cm))
raw_tp = re.findall(r"""(?<![\w.])fetch\((?:'|")http://127\.0\.0\.1:8823""", tpl)
rec('board: the fetch wrapper is installed once at load', '_bridgeInstall();' in ht and len(raw_tp) >= 16, len(raw_tp))

# popup scripts
rec('board: outcome popup gets the helper', "'<script>' + _bridgeSrc() + 'var _PEND" in tpl)
rec('board: Morning Worker shell carries the helper, worker anchor line untouched',
    "(opts.screen ? '<script>' + _bridgeSrc()" in tpl and chr(10) + "    + '<script>(function(){'" + chr(10) in tpl)
_i = tpl.find('function _bridgeSrc(){')
bsrc = tpl[_i:tpl.index(chr(10), _i)].rstrip() if _i >= 0 else ''
js2 = HARN + ht + "\n" + bsrc + "\nvar src=_bridgeSrc(); var seen=[];\n" + r"""
// the popup gets the functions as text: rebuild them in a clean scope and call one
var f = new Function('localStorage','fetch','window','toast','Response','Promise','Date',
  src + '; return bridgeFetch;');
__store.dfBridgeKey = 'pop-key';
f(localStorage, fetch, window, toast, Response, Promise, Date)('/mark', {method:'POST'}).then(function(){
  console.log(JSON.stringify({k: __calls[0].opts.headers['X-Dealflow-Key'], url: __calls[0].url,
    hasScriptClose: src.indexOf('</script') >= 0}));
});
"""
o2, err2 = run_node(js2, 'popup') if ht else (None, 'no helper')
rec('popup copy of the helper works and cannot close its script tag',
    bool(o2) and o2.get('k') == 'pop-key' and o2.get('url') == 'http://127.0.0.1:8823/mark'
    and o2.get('hasScriptClose') is False, (o2, err2))

# ---------------------------------------------------------------- 5. Call Mode routes
print('\nCALL MODE ROUTES')
PRE = HARN + hc + r"""
var navigator = {userAgent:'desktop'};
function textingHeld(){ return true; }
var TEXTHOLD = null, TEXTHOLDLIVE = false, TEXTHOLDBAK = null;
function render(){}
function firstName(r){ return r.fn || ''; }
function _mailAddr(a){ return String(a||'').trim().toLowerCase(); }
"""
js3 = PRE + fn('textPreflight') + fn('mailViaBridge') + fn('pollTextHold') + r"""
var out = {};
(async function(){
  __store.dfBridgeKey = 'dev-key';
  __resp = {status:200, body:{ok:true}};
  out.pre = await textPreflight('C1', '3055550101', []);
  out.preHdr = __calls[0].opts.headers['X-Dealflow-Key'] + '|' + __calls[0].url;
  var m = await mailViaBridge({c:'C1', a:'1 Main', pcs:[]}, {to:'a@b.co', subj:'s', body:'b', lang:'en'});
  out.mail = m; out.mailHdr = __calls[1].opts.headers['X-Dealflow-Key'] + '|' + __calls[1].url;
  pollTextHold();
  await new Promise(function(r){ setTimeout(r, 20); });
  out.healthHdr = __calls[2].opts.headers['X-Dealflow-Key'] + '|' + __calls[2].url;
  __resp = {status:401, body:{ok:false, blocked:'device_key', err:'a device key is required (header X-Dealflow-Key)'}};
  out.pre401 = await textPreflight('C1', '3055550101', []);
  out.mail401 = await mailViaBridge({c:'C1', a:'1 Main', pcs:[]}, {to:'a@b.co', subj:'s', body:'b', lang:'en'});
  console.log(JSON.stringify(out)); process.exit(0);
})();
"""
o3, err3 = run_node(js3, 'routes') if (fn('textPreflight') and fn('mailViaBridge')) else (None, 'function missing')
rec('Call Mode routes run under node', o3 is not None, err3)
o3 = o3 or {}
rec('text/check carries the key', o3.get('preHdr') == 'dev-key|http://127.0.0.1:8823/text/check', o3.get('preHdr'))
rec('send (paste-an-email) carries the key', o3.get('mailHdr') == 'dev-key|http://127.0.0.1:8823/send', o3.get('mailHdr'))
rec('health poll carries the key', o3.get('healthHdr') == 'dev-key|http://127.0.0.1:8823/health', o3.get('healthHdr'))
rec('text check 401 device_key: refusal reads the plain message',
    o3.get('pre401') == {'ok': False, 'why': PLAIN}, o3.get('pre401'))
rec('email send 401 device_key: refusal reads the plain message',
    (o3.get('mail401') or {}).get('ok') is False and (o3.get('mail401') or {}).get('why') == PLAIN, o3.get('mail401'))

# ---------------------------------------------------------------- 3. call mode -> disk
print('\nCALL MODE -> /notes')
sn = fn('saveNotes', '\n}}\n')
rec('saveNotes found', bool(sn))
js4 = HARN + hc + r"""
var _cnT = null, _cnBusy = false;
var timers = [], LS = 'fcLeadNotes', _LSLAST = null, _SAVEFAIL = 0, _FCGEN = 0;
function setTimeout_(f, ms){ timers.push([f, ms]); return timers.length; }
var realST = setTimeout; setTimeout = function(f, ms){ timers.push([f, ms]); return timers.length; };
function clearTimeout(){}
function _absorbStored(){ return 0; } function logErr(){}
function today(){ return '2026-10-30'; }
var notes = {C1:{status:'Appointment', touches:[{d:'2026-10-30',ch:'call',out:'APPOINTMENT SET'}], nextStep:{date:'2026-11-02',what:'visit'}}};
""" + fn('callNotesPushSoon') + fn('callNotesPush') + sn + r"""
var out = {};
saveNotes();
out.scheduled = timers.length ? timers[timers.length-1][1] : null;
out.stored = !!__store.fcLeadNotes;
(async function(){
  timers[timers.length-1][0]();
  await new Promise(function(r){ realST(r, 20); });
  var c = __calls[0] || {};
  var b = {}; try{ b = JSON.parse(c.opts.body); }catch(e){}
  out.call = {url: c.url, method: (c.opts||{}).method, flag: b._dealflow_notes, keys: Object.keys(b).sort(),
              nextStep: ((b.notes||{}).C1||{}).nextStep};
  console.log(JSON.stringify(out)); process.exit(0);
})();
"""
o4, err4 = run_node(js4, 'notes') if (sn and fn('callNotesPush')) else (None, 'saveNotes does not post /notes')
rec('saveNotes schedules a debounced /notes push', bool(o4) and o4.get('scheduled') == 4000 and o4.get('stored'), (o4, err4))
c4 = (o4 or {}).get('call') or {}
rec('the push is POST /notes with the notes payload flag and the after-the-yes fields',
    c4.get('url') == 'http://127.0.0.1:8823/notes' and c4.get('method') == 'POST' and c4.get('flag') == 1
    and c4.get('nextStep') == {'date': '2026-11-02', 'what': 'visit'}, c4)
rec('workerLog / sentArchive are NOT sent (the server would read [] as a deletion)',
    'workerLog' not in (c4.get('keys') or []) and 'sentArchive' not in (c4.get('keys') or []) and bool(c4.get('keys')), c4)

# ---------------------------------------------------------------- 4. text ledger
print('\nTEXT LEDGER')
js5 = HARN + hc + r"""
function lang(){ return 'es'; } function firstName(r){ return 'Ana'; }
""" + fn('callTextLedger') + r"""
__store.fcDevice = 'carlos-phone';
callTextLedger({c:'C1', pk:'P1', o:'ANA TEST'}, '3055550101', true, 'ladder', 'cold', 'hola');
callTextLedger({c:'C1', pk:'P1'}, '3055550101', false, 'ladder', 'cold', 'hola');
console.log(JSON.stringify(__calls.map(function(c){ return {u:c.url, m:c.opts.method, b:JSON.parse(c.opts.body)}; })));
process.exit(0);
"""
o5, err5 = run_node(js5, 'text') if fn('callTextLedger') else (None, 'callTextLedger missing')
rec('callTextLedger runs', o5 is not None, err5)
if o5:
    b0, b1 = o5[0]['b'], o5[1]['b']
    rec('posts /text with the board body shape',
        o5[0]['u'].endswith('/text') and o5[0]['m'] == 'POST' and b0['case'] == 'C1' and b0['pkey'] == 'P1'
        and b0['to'] == '3055550101' and b0['stage'] == 'cold' and b0['tone'] == 'ladder' and b0['lang'] == 'es'
        and b0['body_len'] == 4 and b0['device'] == 'carlos-phone' and b0['owner'] == 'Ana', b0)
    rec('confirmed flag follows the argument', b0['confirmed'] is True and b1['confirmed'] is False)
yes = html[html.index("$('txy').onclick = function(){"):]
yes = yes[:yes.index('\n    };') + 8]
rec('"Yes, it sent" posts a confirmed text', "callTextLedger(r, r.p[phIdx], true," in yes, yes[:200])
opn = html[html.index('n.textopen = today();'):]
rec('opening the composer posts an unconfirmed text', "callTextLedger(r, r.p[phIdx], false," in opn[:250], opn[:200])

# ---------------------------------------------------------------- both pages still parse
print(chr(10) + 'SYNTAX')
for _n, _src in (('call mode page', html), ('board', tpl)):
    _big = max(re.findall(r'<script[^>]*>(.*?)</script>', _src, re.S), key=len)
    _f = os.path.join(_T, 'syn_%s.js' % _n.split()[0])
    io.open(_f, 'w', encoding='utf-8').write(_big)
    _p = subprocess.run([NODE, '--check', _f], capture_output=True, text=True, timeout=60)
    rec('%s: main script passes node --check' % _n, _p.returncode == 0, _p.stderr[:300])

# ---------------------------------------------------------------- settings
print('\nSETTINGS')
rec('Call Mode: a bridge key button on the session strip', 'id="bkeybtn"' in html and 'setBridgeKey()' in html)
rec('board: a Bridge key button beside the sender fields', 'id="bkeybtn"' in tpl and 'onclick="setBridgeKey()"' in tpl)

print('\n%s' % ('ALL PASS' if not fails else 'FAILS: %d -> %s' % (len(fails), fails)))
sys.exit(1 if fails else 0)
