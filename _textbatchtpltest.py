#!/usr/bin/env python3
"""_textbatchtpltest -- the Morning Worker shows the EN/ES text template before a batch sends,
and nothing about texting still runs through Quo.

2026-10-07, Alejandro: "REMOVE QUO from operations completely ... make it straight text messages
and template it spanish and english like the template from morning worker whenever its about to
send a text batch."

  1. _textTplSamples renders every _textBodies branch with placeholders, byte-for-byte the same
     function the texts are built from, and every sample keeps the EN and ES let-me-know line.
  2. _smsTplKey picks the branch _textBodies would take, in _textBodies' own order.
  3. Start text batch shows the template screen first: one card per group in the batch, English
     and Spanish, counts, and NOTHING opens, counts or posts until Start sending.
  4. Start sending runs the old start (hold, hours window, then the step). A held batch never
     reaches the template screen.
  5. Each step shows the message as an English block and a Spanish block.
  6. No live code imports quo_sync or reads a Quo file; the bridge refuses with text_hold.

No network, no browser, no real people. Runs the JS that ships, extracted by name, under node.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TPL_SRC = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()

checks, fails = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra[:220]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


def extract(src, name):
    i = src.find('function ' + name + '(')
    if i < 0:
        raise SystemExit('ANCHOR GONE: function %s()' % name)
    j = src.index('{', i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise SystemExit('unbalanced braces extracting ' + name)


def node(js):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(js)
        p = f.name
    try:
        r = subprocess.run(['node', p], capture_output=True, text=True, timeout=60,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0:
            raise SystemExit('node failed:\n' + (r.stderr or '')[:3000])
        return json.loads(r.stdout)
    finally:
        os.unlink(p)


def worker_blob_js(src):
    """Same recovery _textbatchtest.py uses: rebuild genMorningWorker's string expression."""
    at = src.find('function genMorningWorker(')
    start = src.index("+ '<script>(function(){'", at)
    end = src.index("+ '})();<\\/script>';", start)
    frag = src[src.rindex('\n', 0, start) + 1:src.index('\n', end)]
    pieces, in_block = [], False
    for line in frag.split('\n'):
        t = line.lstrip()
        if in_block:
            if '*/' in t:
                in_block = False
            continue
        if t.startswith('/*'):
            if '*/' not in t:
                in_block = True
            continue
        if t.startswith('//') or not t:
            continue
        pieces.append(t)
    expr = '\n'.join(pieces).replace("+ '})();<\\/script>';", "+ '})();<\\/script>'")
    stubs = ('var qData=JSON.stringify({urgent:[]}),lane="urgent",cap={max:50,warnAt:40},'
             'laneStats={},autostart=false,seedLog="[]",seedStats="{}",persistq=[],'
             'bounceBaked=null;\nfunction WORKER_LANES_META(){return {};}\n')
    doc = node(stubs + 'var DOC = ("" \n' + expr + ');\n'
               'var m = /<script>\\(function\\(\\)\\{([\\s\\S]*)\\}\\)\\(\\);<\\/script>/.exec(DOC);\n'
               'console.log(JSON.stringify({js: m ? m[1] : ""}));')
    if not doc['js']:
        raise SystemExit('could not recover the worker blob script')
    return doc['js']


# ---------------------------------------------------------------- 1-2. board-side template
labels_at = TPL_SRC.index('const TEXT_TPL_LABELS')
LABELS = TPL_SRC[labels_at:TPL_SRC.index(';', labels_at) + 1]
BOARD = '\n'.join([
    "const SENDER_DEFAULTS = {name:'Default Sender'}; var sender = {name:'Test Sender'};",
    "var TONE = 'cold'; function _defaultTextTone(r){ return TONE; }",
    extract(TPL_SRC, '_textBodies'), LABELS,
    extract(TPL_SRC, '_smsTplKey'), extract(TPL_SRC, '_textTplSamples'),
])
out = node(BOARD + r"""
var S = _textTplSamples(), same = {};
Object.keys(TEXT_TPL_LABELS).forEach(function(k){
  var B = _textBodies({first:'[first name]', sender:'Test Sender', stEN:'[street]', stES:'[street]',
                       td:k==='td', lp:k==='lp', tone:(k==='td'||k==='lp')?'cold':k});
  same[k] = (S[k].en === B.en && S[k].es === B.es);
});
var keys = {};
[['urgent','CC'],['t3','LP'],['followup','LP'],['cold','TD'],['cold','LP'],['cold','CC'],['retired','CC']]
  .forEach(function(p){ TONE = p[0]; keys[p[0]+'/'+p[1]] = _smsTplKey({st:p[1]}); });
console.log(JSON.stringify({S:S, same:same, keys:keys}));
""")
S = out['S']
rec('a template for every _textBodies branch',
    sorted(S) == sorted(['cold', 'urgent', 'lp', 'td', 'followup', 't3']), sorted(S))
rec('every template is the exact _textBodies output', all(out['same'].values()), out['same'])
for k, v in S.items():
    rec('%s: English has the placeholders and the EN let-me-know line' % k,
        '[first name]' in v['en'] and "If now's not a good time, just let me know and I won't text you again." in v['en'],
        v['en'])
    rec('%s: Spanish has the ES let-me-know line' % k,
        'Si ahora no es buen momento, solo dígamelo y no le vuelvo a escribir.' in v['es'], v['es'])
    rec('%s: no "Reply STOP" in either language' % k,
        'reply stop' not in (v['en'] + v['es']).lower() and 'responda stop' not in (v['en'] + v['es']).lower())
    rec('%s: the sender name is filled in' % k, 'Test Sender' in v['en'] and 'Test Sender' in v['es'])
K = out['keys']
rec('_smsTplKey follows _textBodies order',
    K == {'urgent/CC': 'urgent', 't3/LP': 't3', 'followup/LP': 'followup', 'cold/TD': 'td',
          'cold/LP': 'lp', 'cold/CC': 'cold', 'retired/CC': 'cold'}, K)

# ---------------------------------------------------------------- 3-5. the worker screens
WJS = worker_blob_js(TPL_SRC)
fns = '\n'.join(extract(WJS, n) for n in
                ('_esc2', 'textBatchStart', 'textBatchPreview', 'textBatchGo', 'textBatchStep'))
EN = S['cold']['en'].replace('[first name]', 'Ana').replace('[street]', '1 Example St')
ES = S['cold']['es'].replace('[first name]', 'Ana').replace('[street]', '1 Example St')
sms = 'sms:+15550100101?body=' + __import__('urllib.parse').parse.quote(EN + '\n\n' + ES)
run = node(r"""
var els = {}, logs = [], opened = [], posts = [], steps = 0, runbar = 0;
var main = { _h:'', set innerHTML(v){ this._h = v; els = {};
  var re = /id='([^']+)'/g, m; while((m = re.exec(v))) els[m[1]] = {id:m[1], onclick:null}; },
  get innerHTML(){ return this._h; } };
var document = { getElementById: function(id){ return els[id] || null; } };
function addLog(k, who, t){ logs.push([k, who, t]); }
function renderRunBar(){ runbar++; } function renderTextQ(){} function render(){}
function sentToday(){ return 3; } var CAP = {max: 50};
var SAFE = true; function _wftsa(){ return {safe: SAFE, label: 'closed'}; }
function openHere(u){ opened.push(u); return true; } function post(k, x){ posts.push(k); }
function confirmSend(){} function textBatchDone(){ tbOn = false; }
var TEXT_HOLD = '', TBI = 0, TBSENT = 0, TBSKIP = 0, tbOn = false, SYNCWAIT = null; function cancelSyncWait(){}
var TEXTTPL = """ + json.dumps(S) + r""";
var TEXTQ = [
  {c:'2099-000001-CA-01', name:'Ana', phone:'5550100101', sms:""" + json.dumps(sms) + r""", tpl:'cold'},
  {c:'2099-000002-CA-01', name:'Bo',  phone:'5550100102', sms:'sms:+15550100102?body=x', tpl:'cold'},
  {c:'2099-000003-CA-01', name:'Cy',  phone:'5550100103', sms:'sms:+15550100103?body=x', tpl:'followup'},
  {c:'2099-000004-CA-01', name:'Di',  phone:'5550100104', sms:'sms:+15550100104?body=x', tpl:''}
];
""" + fns + r"""
var R = {};
TEXT_HOLD = 'HOLD texting: fixture'; textBatchStart();
R.held = {html: main.innerHTML, tbOn: tbOn, log: logs.slice(-1)[0]};
TEXT_HOLD = ''; SAFE = false; textBatchStart();
R.closed = {html: main.innerHTML, tbOn: tbOn};
SAFE = true; textBatchStart();
R.preview = {html: main.innerHTML, tbOn: tbOn, opened: opened.length, posts: posts.length,
             go: !!els['tb-go'], cancel: !!els['tb-cancel']};
els['tb-cancel'].onclick();
R.cancel = {tbOn: tbOn, opened: opened.length, log: logs.slice(-1)[0]};
textBatchStart(); TEXT_HOLD = 'HOLD texting: fixture'; els['tb-go'].onclick();
R.goHeld = {tbOn: tbOn, opened: opened.length};
TEXT_HOLD = ''; textBatchStart(); els['tb-go'].onclick();
R.step = {html: main.innerHTML, tbOn: tbOn, TBI: TBI, opened: opened.length};
console.log(JSON.stringify(R));
""")
rec('a held batch never reaches the template screen',
    run['held']['html'] == '' and run['held']['tbOn'] is False and 'fixture' in run['held']['log'][2],
    run['held'])
rec('outside 8 AM-8 PM the template screen does not open', run['closed']['html'] == '' and not run['closed']['tbOn'])
P = run['preview']
rec('Start text batch shows the template, not the first composer',
    'This is what goes out' in P['html'] and P['tbOn'] is False and P['opened'] == 0 and P['posts'] == 0, P)
rec('the template has English and Spanish', 'English' in P['html'] and 'Espa&ntilde;ol' in P['html'])
rec('one card per group, with its count',
    'First text &middot; 2 people' in P['html'] and 'Second text (follow-up) &middot; 1 person' in P['html'], P['html'][:600])
rec('the card bodies are the baked templates', S['cold']['es'] in P['html'] and S['followup']['en'] in P['html'])
rec('a lead with no group is counted, not dropped', '1 more already have their message filled in' in P['html'])
rec('the screen tells him to mark stops from his phone in Call Mode', 'Do Not Contact in Call Mode' in P['html'])
rec('Start sending and Not now are both on the screen', P['go'] and P['cancel'])
rec('Not now sends nothing and starts nothing',
    run['cancel']['tbOn'] is False and run['cancel']['opened'] == 0 and 'nothing sent' in run['cancel']['log'][2])
rec('Start sending re-checks the hold', run['goHeld']['tbOn'] is False and run['goHeld']['opened'] == 0)
ST = run['step']
rec('Start sending runs the batch from the first lead',
    ST['tbOn'] is True and ST['TBI'] == 0 and 'Text batch &middot; 1 of 4' in ST['html'], ST['html'][:300])
rec('the step shows the English block and the Spanish block separately',
    re.search(r'English</div><div[^>]*>[^<]*Ana', ST['html']) is not None
    and re.search(r'Espa&ntilde;ol</div><div[^>]*>Hola Ana', ST['html']) is not None, ST['html'][:900])
rec('the step still waits for his tap before opening Messages', ST['opened'] == 0)

QT = WJS[WJS.index('closest("[data-qtext]")'):]
QT = QT[:QT.index('openHere(x.sms)')]
rec('the per-row Text button also refuses while texting is held', 'if(TEXT_HOLD)' in QT, QT[:300])
esc = node(r"""
var main = {innerHTML:''}; var document = {getElementById:function(){return null;}};
function addLog(){} function renderRunBar(){} function renderTextQ(){} function render(){}
function sentToday(){return 0;} var CAP={max:5}; function _wftsa(){return {safe:true};}
var TEXT_HOLD='', tbOn=false, TEXTTPL={cold:{label:'First text', en:'Hi <b>x</b> & y', es:'Hola'}},
    TEXTQ=[{c:'1', name:'A', tpl:'cold'}];
""" + extract(WJS, '_esc2') + extract(WJS, 'textBatchPreview') + r"""
textBatchPreview(); console.log(JSON.stringify({h: main.innerHTML}));
""")
rec('template text is HTML-escaped on the screen',
    'Hi &lt;b&gt;x&lt;/b&gt; &amp; y' in esc['h'] and '<b>x</b>' not in esc['h'], esc['h'][:300])

# ---------------------------------------------------------------- 5b. Call Mode bake carries the 07:15 sync
import datetime as _dt
import shutil as _sh
import text_hold as _TH
import call_mode as _CM
_tmp = tempfile.mkdtemp(prefix='dfbake_')
_old = (_TH.OPTOUT_FILE, _CM.HERE)
try:
    _TH.OPTOUT_FILE = os.path.join(_tmp, 'optouts.json')
    open(_TH.OPTOUT_FILE, 'w').write('{}')
    _CM.HERE = _tmp
    b0 = json.loads(_CM._text_hold_json())
    rec('a fresh list with no 07:15 sync bakes held', b0['held'] is True and b0['ok'] is False
        and '07:15' in b0['why'] and b0['syncDay'] == '', b0)
    today = _dt.date.today().isoformat()
    import time as _t
    open(os.path.join(_tmp, 'sync_status.json'), 'w').write(json.dumps({
        'date': today, 'state': 'finished', 'ok': True,
        'started_at': _t.time() - 120, 'finished_at': _t.time() - 60, 'steps': []}))
    b1 = json.loads(_CM._text_hold_json())
    rec("today's sync and a fresh list bake ok with syncDay", b1['held'] is False and b1['ok'] is True
        and b1['syncDay'] == today, b1)
finally:
    _TH.OPTOUT_FILE, _CM.HERE = _old
    _sh.rmtree(_tmp, ignore_errors=True)

# ---------------------------------------------------------------- 6. Quo is out of operations
LIVE = [f for f in os.listdir(HERE) if f.endswith(('.py', '.bat', '.ps1', '.js', '.html'))
        and not f.startswith(('_', 'test_')) and f not in ('design-preview.html',)]
hits = []
for f in LIVE:
    t = open(os.path.join(HERE, f), encoding='utf-8', errors='replace').read()
    for pat in (r'\bimport quo_sync\b', r'quo_sync\.', r'quo_inbound_status', r'quo_calls\.json',
                r'api\.quo\.com', r'__QUOHOLD__', r'\bQUOHOLD\b'):
        if re.search(pat, t):
            hits.append('%s:%s' % (f, pat))
rec('no live file imports quo_sync or reads a Quo file', not hits, hits)
rec('quo_sync.py is deleted', not os.path.exists(os.path.join(HERE, 'quo_sync.py')))
SS = open(os.path.join(HERE, 'send_server.py'), encoding='utf-8').read()
rec("POST /text refuses as text_hold, from text_hold.py",
    "'blocked': 'text_hold'" in SS and 'import text_hold' in SS and "'quo_inbound'" not in SS)
RB = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='utf-8', errors='replace').read()
rec('the nightly refresh no longer runs a Quo step', re.search(r'\bquo\b|quo_|quo\.key', RB, re.I) is None)

print('\n%s -- %d check(s), %d failure(s)' % ('FAILED' if fails else 'PASSED', len(checks), len(fails)))
if fails:
    print('failed: ' + ', '.join(fails))
sys.exit(1 if fails else 0)
