"""_autorunstoptest -- the Morning Worker's auto-run controls: Start, Stop, and what a stop leaves.

Run:  python _autorunstoptest.py   (exit 0 = safe; no network, no browser, no board, no real data)

WHAT BROKE (2026-09-19, reported by Alejandro: "fix the auto run buttons operations on the morning
worker to add a stop button and to work and function according to new code structure and etc").

  1. THERE WAS NO STOP. Not a broken one -- none. `auto` was set by the two Start buttons and
     cleared only by the end of the queue, a lane switch (which throws away your place), or
     doSend()'s cap / SMTP-auth / bridge-offline branches calling pause(). Once a run was going the
     operator's only real out was closing the tab, which also drops anything still inside the mark
     retry window. Now: stopRun() behind a `data-run='stop'` button in a bar that lives OUTSIDE
     #mwmain, plus the same control on the lead card, plus the unattended 8am countdown's Cancel --
     all three the same function, so they cannot leave different state behind.

  2. #mwmain WAS WRITTEN ONCE PER RUN, NOT ONCE PER LEAD. runOne() set the header progress chip and
     called attempt(); doSend() resolves without touching #mwmain, and the callq / wp branches
     return straight through advance(). So an entire auto-run sat on the FIRST lead's card while
     the log scrolled behind it -- and the stale card's buttons were live, reading Q[i] from the
     click delegate, so pressing Call under lead 1's name and number dialled whichever lead the run
     had actually reached. runOne() now renders the lead it is about to work.

  3. A STOP COULD BE OVERTAKEN BY ITS OWN PENDING TIMER. advance() scheduled the next lead with a
     bare setTimeout(runOne, 700) and hopNextLane() with 900; nothing held the handle, so a stop
     landing inside that window was followed by the run carrying on. Handles are kept in `nextStep`
     and cleared on stop, and runOne() re-checks `auto` as well.

  4. "Auto-run ALL lanes" WALKED A LANE THAT IS NOT BAKED. LANE_ORDER was a hardcoded five including
     `balloon`, which has a WORKER_LANES entry and a _workerQueue sort but is NOT in the four queues
     genMorningWorker bakes -- so LANES.balloon is undefined. The walk is harmless (laneCount reads
     0) but the button's own subtitle was a hardcoded urgent/active/early that named neither the
     current lane nor replied. LANE_ORDER is now derived from the bake, and the subtitle from
     LANE_ORDER, so the claim and the walk always agree.

  5. THE DIAL PATH IGNORED #32's PHONE SOURCE TAGS AND phbest. _workerCard filtered DNC and
     bad-number flags and took phones[0..2] in build order. textablePhones() had already been given
     the owner-only rule for the SEND path, but the CALL path -- doCall(), the Call list, and the
     number PERSISTQ re-bakes tomorrow -- still dialled phones[0] whatever it was, including a
     lead's own listing agent ('ag') or an office number sitting on 3+ owners ('xl'). That is the
     2026-09-19 Andre/Compass call: the card said the homeowner's name, the number reached an agent.
     _workerPhones() now applies call_mode.py's rule ('ag'/'xl' out, 'hh'/'nm' kept) and leads with
     r.phbest.

WHAT A STOP MUST NOT DO, and this file's real subject: invent or lose a record. A /send already in
flight cannot be recalled, so its own .then still posts the truth. Everything that had not reached
the bridge stays untouched -- no post("sent"), no post("text"), no cap charge, no retire -- so every
unworked lead is still owed a contact. The suppression surface reserved in CLAUDE.md is READ here
and never written.

HOW THIS TESTS. The worker tab's JS is EXTRACTED BY NAME from tracker_template.html and run under
node against fake DOM objects, the way _textbatchtest.py does it, so what is asserted is the code
that ships rather than a copy that can drift. The run loop is driven for real: a fake bridge, a fake
#mwmain that records every write, and a post() that records every mark -- then Stop is pressed
mid-run and the recorded marks are the assertion.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, 'tracker_template.html')

fails = []
checks = []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    if len(extra) > 200:
        extra = extra[:200] + ' ...'
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra) if extra else ''))
    if not ok:
        fails.append(name)


def no_comments(src):
    """Commentary in this file quotes the broken shapes verbatim; strip it before scanning."""
    out = []
    for line in src.split('\n'):
        t = line.lstrip()
        if t.startswith('//') or t.startswith('* ') or t.startswith('/*'):
            continue
        out.append(line)
    return '\n'.join(out)


def extract(src, name):
    """`function NAME(...){...}` by balanced-brace scan -- same trick _textbatchtest.py uses."""
    i = src.find('function ' + name + '(')
    if i < 0:
        raise SystemExit('ANCHOR GONE: function %s() is no longer in the source. '
                         'It was renamed or removed -- update this test with it.' % name)
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
        # encoding= is not optional -- see the same note in _textbatchtest.py. The board HTML this
        # test feeds through node carries bytes the Windows locale codec cannot map.
        r = subprocess.run(['node', p], capture_output=True, text=True, timeout=60,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0:
            raise SystemExit('node failed:\n' + (r.stderr or '')[:3000])
        return json.loads(r.stdout)
    finally:
        os.unlink(p)


TPL_SRC = open(TPL, encoding='utf-8').read()
TPL_CODE = no_comments(TPL_SRC)


# ------------------------------------------------------------------ the worker tab's blob script
# genMorningWorker builds the whole worker document as one string expression. Rebuild it (dropping
# the Python-side // and /* */ commentary between the pieces) and evaluate it under node to recover
# the JS that actually ships inside the Blob.
def worker_blob_js(src):
    at = src.find('function genMorningWorker(')
    if at < 0:
        raise SystemExit("ANCHOR GONE: genMorningWorker() is no longer in tracker_template.html.")
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
    expr = '\n'.join(pieces)
    expr = expr.replace("+ '})();<\\/script>';", "+ '})();<\\/script>'")

    stubs = ('var qData=JSON.stringify({urgent:[]}),lane="urgent",cap={max:50,warnAt:40},'
             'laneStats={},autostart=false,seedLog="[]",seedStats="{}",persistq=[],'
             'bounceBaked=null;\nfunction WORKER_LANES_META(){return {};}\n')
    doc = node(stubs + 'var DOC = ("" \n' + expr + ');\n'
               'var m = /<script>\\(function\\(\\)\\{([\\s\\S]*)\\}\\)\\(\\);<\\/script>/.exec(DOC);\n'
               'console.log(JSON.stringify({js: m ? m[1] : "", doc: DOC}));')
    if not doc['js']:
        raise SystemExit('could not recover the worker blob script from genMorningWorker.')
    return doc['js'], doc['doc']


WORKER_JS, WORKER_DOC = worker_blob_js(TPL_SRC)
rec('worker blob script recovered from genMorningWorker', len(WORKER_JS) > 20000,
    '%d chars' % len(WORKER_JS))

with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
    f.write(WORKER_JS)
    _p = f.name
_chk = subprocess.run(['node', '--check', _p], capture_output=True, text=True)
os.unlink(_p)
rec('worker blob script parses under node', _chk.returncode == 0, (_chk.stderr or '')[:400])


# ==================================================================== 1. the control surface exists
# The run bar is markup in the document, not something the script creates on demand, and it sits
# OUTSIDE #mwmain. That placement is the whole fix: every screen the worker shows -- lead card, pace
# countdown, confirm card, cap card, queue-clear -- rewrites #mwmain, so a Stop inside it would be
# gone for most of a run. Assert the ORDER in the document, because "outside" is positional.
# The body of the worker document is one concatenated expression with a .map() over the lane
# buttons in the middle, so it is read as source rather than evaluated. Scoped to genMorningWorker
# so a match anywhere else in a 18k-line file cannot satisfy it.
_gs = TPL_SRC.index('function genMorningWorker(')
_ge = TPL_SRC.index("+ '})();<\\/script>';", _gs)
GMW = TPL_SRC[_gs:_ge]
rec('#mwrunbar is in the worker document', '<div id="mwrunbar"' in GMW)
rec('#mwrunbar sits OUTSIDE #mwmain, before it',
    GMW.index('<div id="mwrunbar"') < GMW.index('<div id="mwmain"></div>'),
    'a Stop button inside #mwmain is erased by the next lead repaint')
rec('#mwrunbar is a sibling of #mwmain, not nested in it',
    '<div id="mwrunbar" class="mwrun"></div>' in GMW,
    'it is written closed and empty; renderRunBar() fills it')
rec('the run bar is painted by renderRunBar()', 'function renderRunBar(' in WORKER_JS)
rec('stopRun() exists', 'function stopRun(' in WORKER_JS)
rec('startRun() exists', 'function startRun(' in WORKER_JS)

BAR = extract(WORKER_JS, 'renderRunBar')
rec('the bar offers a stop control', "data-run='stop'" in BAR)
rec('the bar offers both starts', "data-run='lane'" in BAR and "data-run='all'" in BAR)
rec('the bar disables Start while the bridge is not confirmed up', 'BRIDGE_OK!==true' in BAR)
rec('the bar reports how many are left, not just the lane total',
    'Q.length-i' in BAR, 'a bar that says 34 after 20 were worked is a lie')


# ============================================== 2. the wiring, which is the 2026-09-18 failure mode
# A handler assigned as a property on a node written by innerHTML dies on the next write to that
# container (PR #25). renderRunBar rewrites #mwrunbar on every state change, so its buttons MUST be
# driven by the document-level delegate. This is the check that keeps the new buttons from becoming
# the old dead one.
rec('no property handler is assigned to a run-bar button',
    not re.search(r'mwrunbar[^;]{0,80}\.onclick', WORKER_JS)
    and not re.search(r'data-run[^;]{0,60}\.onclick', WORKER_JS),
    'anchor: nothing may attach .onclick to a node renderRunBar() will re-parse')
rec('the document click delegate matches [data-run]',
    'closest("[data-run]")' in WORKER_JS)
rec('the delegate routes stop to stopRun and the rest to startRun',
    re.search(r'closest\("\[data-run\]"\);[\s\S]{0,320}?stopRun\(', WORKER_JS) is not None
    and re.search(r'closest\("\[data-run\]"\);[\s\S]{0,400}?startRun\(', WORKER_JS) is not None)
rec('a disabled run-bar button does nothing',
    'rb.hasAttribute("disabled")' in WORKER_JS,
    'the delegate sees clicks the button attribute alone would not stop')

# The lead card carries the same pair, and the card's run controls must be handled ABOVE the
# `var r=Q[i]; if(!r)return;` guard -- there is no current lead on the queue-clear or cap screens,
# and a Stop that only works while a lead is on screen is not a Stop.
# BOUND THE SLICE TO THE LISTENER. The first cut was `WORKER_JS[index('closest(".mwbtn")'):]` --
# everything to the end of the blob, which swallows the unattended-start block and its own (correct)
# bridge gate, so the "gates are not duplicated" check below passed or failed for the wrong reason.
_boot = WORKER_JS.index('renderBridge(); probeBridge();')   # the boot line, after both delegates
CARD_DELEGATE = WORKER_JS[WORKER_JS.index('closest(".mwbtn")'):_boot]
_stop_at = CARD_DELEGATE.index('fn==="stop"')
_r_guard = CARD_DELEGATE.index('var r=Q[i]; if(!r)return;')
rec('the card delegate handles stop BEFORE the current-lead guard', _stop_at < _r_guard,
    'a stop on the queue-clear or cap screen has no Q[i] to find')
rec('the card delegate handles start before the current-lead guard too',
    CARD_DELEGATE.index('fn==="auto"||fn==="autoall"') < _r_guard)
rec('both card run controls go through the shared startRun/stopRun',
    re.search(r'fn==="auto"\|\|fn==="autoall"\)\{ return startRun\(', CARD_DELEGATE) is not None)
rec('the gates are not duplicated in the delegate any more',
    'BRIDGE_OK' not in CARD_DELEGATE,
    'two copies of a gate is how the two start paths drift apart')


# ============================================================= 3. the run loop, driven for real
# Extract the loop and run it against a fake bridge and a fake #mwmain. This is the part that cannot
# be asserted from source shapes: what the queue does when Stop lands mid-flight.
LOOP_FNS = ('startRun', 'stopRun', 'runOne', 'advance', 'pause', 'hopNextLane', 'channels',
            'renderRunBar', 'laneCount', 'allTotal')
LOOP_JS = '\n'.join(extract(WORKER_JS, n) for n in LOOP_FNS)

HARNESS_HEAD = r"""
// ---- fake worker-tab world -----------------------------------------------------------------
var writes = [], marks = [], logs = [];
var main = { set innerHTML(v){ writes.push(String(v)); }, get innerHTML(){ return writes[writes.length-1]||""; },
             parentNode: { insertBefore: function(){}, firstChild: null } };
var prog = { textContent: "" };
var barEl = { className:"", innerHTML:"" };
function _el(id){ return id==="mwrunbar" ? barEl : null; }
var document = {
  getElementById: _el,
  querySelector: function(){ return null; },
  querySelectorAll: function(){ return []; },
  addEventListener: function(){}, createElement: function(){ return {style:{}}; },
  body: { appendChild: function(){}, removeChild: function(){} }
};
var window = { __cfWatch: null };
var LANES = { urgent: [], active: [], early: [] };
var LMETA = { replied:{t:"REPLIED",ic:"R",d:""}, urgent:{t:"URGENT",ic:"U",d:""},
              active:{t:"ACTIVE",ic:"A",d:""}, early:{t:"EARLY",ic:"E",d:""},
              balloon:{t:"BALLOON",ic:"B",d:""} };
var CAP = { max: 50, warnAt: 40 };
var CALLQ = [], TEXTQ = [], TBI = 0, TBSENT = 0, TBSKIP = 0, tbOn = false;
var BRIDGE_OK = true, BOUNCE = null;
var STATS = { sent:0, text:0, email:0, wp:0, skip:0, callout:0, call:0 };
var LOG = [];
var i = 0, auto = false, autoAll = false, paceMs = 8000, tick = null, healing = null;
var awaitingReturn = false, sentAt = 0, nextStep = null, ASTIMER = null, ASBOX = null;
var lane = "urgent";
var LANE_ALL = ["replied","urgent","active","early","balloon"];
var LANE_ORDER = LANE_ALL.filter(function(k){ return !!LANES[k]; });
var Q = [];
function esc(s){ return String(s==null?"":s); }
function sentToday(){ return (STATS.sent||0)+(STATS.text||0); }
function addLog(kind, who, text, cls){ logs.push({a:kind, who:who||"", detail:text||"", warn:cls==="warn"}); }
function renderLog(){}
function renderCap(){}
function renderCallQ(){}
function renderTextQ(){}
function post(kind, extra){ marks.push({k:kind, extra:extra||null, at:i}); return true; }
function _renderMain(){ main.innerHTML = "SCREEN i=" + i + " auto=" + auto; }
function render(){ _renderMain(); renderRunBar(); }
// attempt() is stubbed to the ONE branch this file is about: an email lead that the bridge accepts.
// The real attempt/doSend are covered by _workeremailtest; what matters here is that the loop steps,
// records a delivery per lead, and stops when told to.
var SENT_AT_LEAD = [];
function attempt(r, idx, tries){
  inflight = setTimeout(function(){
    inflight = null;
    STATS.sent++;
    post("sent", {mid:"m"+r.c, via:"bridge"});
    SENT_AT_LEAD.push(r.c);
    advance();
  }, 5);
}
var inflight = null;
var sending = false, sendingAt = 0;
function sendInFlight(){ return sending && (Date.now()-sendingAt) < 30000; }
function lead(n){ return {c:"C"+n, first:"F"+n, owner:"O"+n, emails:["e"+n+"@x.com"], phones:[], mailTo:"e"+n+"@x.com", mailSubj:"s", mailBody:"b", portfolio:[]}; }
"""

HARNESS_TAIL = """
console.log(JSON.stringify(OUT));
"""


def run_case(body, leads=6):
    js = (HARNESS_HEAD + LOOP_JS
          + '\nLANES.urgent = []; for(var z=0;z<%d;z++) LANES.urgent.push(lead(z));\n' % leads
          + 'Q = LANES.urgent;\nvar OUT = {};\n' + body + HARNESS_TAIL)
    return node(js)


# --- 3a. a run that is never stopped works the whole queue -------------------------------------
out = run_case("""
startRun(false, "test");
OUT.started = auto;
// drive the timers by hand: each lead resolves in 5ms, then advance() schedules 700ms out
var steps = 0;
function pump(done){
  if(steps++ > 40) return done();
  if(inflight){ var f = inflight; inflight = null; f.__run(); return pump(done); }
  if(nextStep){ var g = nextStep; nextStep = null; g.__run(); return pump(done); }
  return done();
}
""")
# setTimeout is not available in the harness above on purpose -- replace it with a recorder so the
# test drives the clock rather than waiting on it.
HARNESS_HEAD = HARNESS_HEAD.replace(
    'var inflight = null;',
    """var inflight = null;
// DRIVEN CLOCK. Real setTimeout would make this suite slow and flaky; a recorder lets the test step
// the loop and, crucially, lets it press Stop with a next-lead timer already pending -- which is
// the race that used to let a stopped run keep going.
var PENDING = [];
function setTimeout(fn, ms){ var h = {fn:fn, ms:ms, dead:false}; PENDING.push(h); return h; }
function clearTimeout(h){ if(h && typeof h === "object") h.dead = true; }
function clearInterval(h){ clearTimeout(h); }
function setInterval(fn, ms){ return setTimeout(fn, ms); }
function pump(n){
  var fired = 0;
  while(fired < n){
    var h = null, k = 0;
    for(; k < PENDING.length; k++){ if(!PENDING[k].dead){ h = PENDING[k]; break; } }
    if(!h) break;
    PENDING.splice(k, 1);
    h.fn(); fired++;
  }
  return fired;
}
function pendingLive(){ var n = 0; PENDING.forEach(function(h){ if(!h.dead) n++; }); return n; }""")


def run_case(body, leads=6):
    js = (HARNESS_HEAD + LOOP_JS
          + '\nLANES.urgent = []; for(var z=0;z<%d;z++) LANES.urgent.push(lead(z));\n' % leads
          + 'Q = LANES.urgent;\nvar OUT = {};\n' + body + HARNESS_TAIL)
    return node(js)


out = run_case("""
startRun(false, "test");
OUT.started = auto;
pump(200);
OUT.sent = SENT_AT_LEAD.slice();
OUT.i = i;
OUT.autoAfter = auto;
OUT.screens = writes.length;
""")
rec('startRun starts the run', out['started'] is True)
rec('an unstopped run works every lead exactly once',
    out['sent'] == ['C0', 'C1', 'C2', 'C3', 'C4', 'C5'], out['sent'])
rec('the run clears `auto` when the lane is done', out['autoAfter'] is False,
    'it did not: `auto` survived queue-end, so the re-entrancy gate then refused every later '
    'Start with "already running" on a run that had finished')
rec('#mwmain is repainted per lead, not once per run', out['screens'] >= 6,
    '%d write(s) for 6 leads -- one write for the whole run is the frozen-screen bug'
    % out['screens'])


# --- 3b. STOP between items: nothing beyond the current lead is touched ------------------------
# pump(5) leaves the loop in the CLEAN between-items state: three leads delivered and the next-lead
# timer pending. That is the case Stop has to win outright.
out = run_case("""
startRun(false, "test");
pump(5);
OUT.before = SENT_AT_LEAD.slice();
OUT.pendingBefore = pendingLive();
stopRun("test");
OUT.autoAfter = auto; OUT.autoAllAfter = autoAll;
OUT.pendingAfter = pendingLive();
pump(200);
OUT.after = SENT_AT_LEAD.slice();
OUT.marks = marks.map(function(m){ return m.k; });
OUT.logs = logs.map(function(l){ return l.a + ":" + l.detail; });
OUT.i = i;
""")
rec('stop clears auto', out['autoAfter'] is False)
rec('stop clears autoAll', out['autoAllAfter'] is False)
rec('stop cancels the pending next-lead timer', out['pendingAfter'] == 0,
    'before=%s after=%s' % (out['pendingBefore'], out['pendingAfter']))
rec('NO lead is worked after a stop between items, even with a timer already pending',
    out['after'] == out['before'] == ['C0', 'C1', 'C2'],
    'before=%s after=%s -- this is the race that made stop advisory' % (out['before'], out['after']))
rec('stop posts NOTHING to the board -- no sent, no text, no done, no skip',
    [k for k in out['marks'] if k != 'sent'] == [], out['marks'])
rec('every "sent" mark corresponds to a lead the fake bridge really accepted',
    out['marks'].count('sent') == len(out['after']),
    '%d mark(s) for %d delivery(ies)' % (out['marks'].count('sent'), len(out['after'])))
rec('stop writes one honest log line saying what is left',
    any(l.startswith('skip:') and 'STOPPED' in l and 'still queued' in l for l in out['logs']),
    out['logs'][-1] if out['logs'] else '(none)')
rec('the stop line does not claim anything was sent that was not',
    any('Nothing was marked sent that was not' in l for l in out['logs']))


# --- 3b-ii. STOP with a send already handed to the bridge --------------------------------------
# pump(6) leaves one lead's fetch outstanding. A message already handed to the SMTP bridge cannot be
# recalled -- pretending otherwise is the dishonesty this whole file exists to prevent -- so that one
# lead still records its delivery, and NOTHING after it is worked.
out = run_case("""
startRun(false, "test");
pump(6);
OUT.before = SENT_AT_LEAD.slice();
sending = true; sendingAt = Date.now();      // the fetch is out there
stopRun("test");
pump(200);
OUT.after = SENT_AT_LEAD.slice();
OUT.marks = marks.map(function(m){ return m.k; });
OUT.logs = logs.map(function(l){ return l.a + ":" + l.detail; });
""")
rec('an in-flight send still lands and is recorded after a stop',
    out['after'] == ['C0', 'C1', 'C2', 'C3'], out['after'])
rec('nothing PAST the in-flight lead is worked',
    'C4' not in out['after'] and 'C5' not in out['after'], out['after'])
rec('the marks still match the deliveries exactly, one per lead',
    out['marks'] == ['sent'] * 4, out['marks'])
rec('the stop line says an email was already handed to the bridge',
    any('already handed to the bridge' in l for l in out['logs']),
    out['logs'][-1] if out['logs'] else '(none)')
rec('Start is refused while that send is outstanding, so the lead is not sent twice',
    run_case("""
startRun(false, "test");
pump(6);
sending = true; sendingAt = Date.now();
stopRun("test");
startRun(false, "retry");
OUT.auto = auto;
OUT.logs = logs.map(function(l){ return l.detail; });
""")['auto'] is False,
    'Stop-then-Start over an outstanding fetch is how one homeowner gets the same email twice')


# --- 3c. Start after a stop RESUMES, it does not restart from the top --------------------------
out = run_case("""
startRun(false, "test");
pump(5);                      // clean between-items stop, no outstanding fetch
var atStop = i;
stopRun("test");
OUT.atStop = atStop;
SENT_AT_LEAD.length = 0;
startRun(false, "resume");
pump(200);
OUT.resumed = SENT_AT_LEAD.slice();
""")
rec('Start after a Stop resumes from where it stopped',
    out['resumed'] and out['resumed'][0] == 'C%d' % out['atStop'],
    'stopped at i=%s, resumed with %s' % (out['atStop'], (out['resumed'] or ['(none)'])[0]))
rec('a resumed run does not re-send the leads already delivered',
    out['resumed'] == ['C3', 'C4', 'C5'], out['resumed'])


# --- 3d. the gates on Start ---------------------------------------------------------------------
out = run_case("""
BRIDGE_OK = false;
startRun(false, "test"); OUT.noBridge = auto;
BRIDGE_OK = true;
startRun(false, "test"); OUT.ok = auto;
startRun(true,  "test"); OUT.dupAll = autoAll;
stopRun("test");
STATS.sent = 50;
startRun(false, "test"); OUT.atCap = auto;
STATS.sent = 0;
i = Q.length;
startRun(false, "test"); OUT.emptyTail = auto;
i = 0; tbOn = true;
startRun(false, "test"); OUT.duringBatch = auto;
OUT.logs = logs.map(function(l){ return l.a + ":" + String(l.detail).slice(0,60); });
""")
rec('Start refuses while the send bridge is not confirmed up', out['noBridge'] is False)
rec('a second Start while running does not flip the run to ALL lanes', out['dupAll'] is False,
    'the 2026-08-13 double-start emailed 51 people twice')
rec('the duplicate start says so instead of failing silently',
    any('already running' in l for l in out['logs']))
rec('Start refuses once the daily cap is spent', out['atCap'] is False)
rec('Start refuses when nothing is left to work', out['emptyTail'] is False)
rec('Start refuses while the text batch is running', out['duringBatch'] is False,
    'two loops over one cap is the same defect as a double start')


# --- 3e. stop from the text batch and from the unattended countdown -----------------------------
out = run_case("""
tbOn = true; TBI = 2; TBSENT = 1; TBSKIP = 1;
TEXTQ = [{c:"T0"},{c:"T1"},{c:"T2"},{c:"T3"}];
stopRun("test");
OUT.tbOn = tbOn;
OUT.marks = marks.map(function(m){ return m.k; });
OUT.logs = logs.map(function(l){ return l.a + ":" + l.detail; });
""")
rec('stop ends the text batch', out['tbOn'] is False)
rec('stopping the batch posts nothing -- an unconfirmed text is not a text',
    out['marks'] == [], out['marks'])
rec('the batch stop line says how many are still queued',
    any('still queued' in l for l in out['logs']), out['logs'])

out = run_case("""
ASTIMER = setTimeout(function(){ auto = true; attempt(Q[0],0,0); }, 20000);
ASBOX = { remove: function(){ OUT.boxRemoved = true; } };
stopRun("test");
OUT.astimer = ASTIMER;
pump(200);
OUT.sent = SENT_AT_LEAD.slice();
OUT.logs = logs.map(function(l){ return l.a + ":" + l.detail; });
""")
rec('stop cancels the unattended 8am countdown', out['astimer'] is None)
rec('the countdown cannot fire after a stop -- nothing is sent', out['sent'] == [], out['sent'])
rec('the cancelled countdown removes its own banner', out.get('boxRemoved') is True)
rec('cancelling before the start says the queue is untouched',
    any('untouched' in l for l in out['logs']), out['logs'])

# The countdown's own Cancel button must be the SAME function, or the two produce different state.
rec('the 8am countdown Cancel routes through stopRun',
    'stopRun("countdown Cancel")' in WORKER_JS,
    'anchor: the mwcancel listener')
rec('the countdown interval is held in the shared handle, not a local',
    'ASTIMER=setInterval' in WORKER_JS.replace(' ', ''),
    'a local handle is one the run bar\'s Stop cannot reach')


# --- 3f. runOne is the authority, whatever timer fires -----------------------------------------
RUNONE = extract(WORKER_JS, 'runOne')
rec('runOne refuses to work a lead when the run is not active', 'if(!auto) return render();' in RUNONE,
    'the guard that makes Stop authoritative even if a handle is missed')
rec('runOne renders the lead it is about to work', 'render();' in RUNONE and 'attempt(r,0,0)' in RUNONE)
rec('runOne re-checks the cap after rendering', RUNONE.count('if(!auto) return') >= 2,
    'render() clears auto on the cap screen; the loop must notice')
ADV = extract(WORKER_JS, 'advance')
rec('advance keeps the next-lead timer handle', 'nextStep=setTimeout(runOne' in ADV, ADV)
HOP = extract(WORKER_JS, 'hopNextLane')
rec('the lane hop keeps its timer handle too', 'nextStep=setTimeout(runOne' in HOP)
STOP = extract(WORKER_JS, 'stopRun')
for what, needle in (('the pace/countdown interval', 'clearInterval(tick)'),
                     ('the channel-fallthrough timer', 'clearTimeout(healing)'),
                     ('the next-lead timer', 'clearTimeout(nextStep)'),
                     ('the unattended confirm watchdog', 'clearTimeout(window.__cfWatch)'),
                     ('the 8am countdown', 'clearInterval(ASTIMER)')):
    rec('stop clears ' + what, needle in STOP.replace(' ', ''), STOP[:0])
rec('stop preserves the queue index so Start can resume', 'i=0' not in STOP.replace(' ', ''),
    'resetting i on stop would re-send everything already delivered')
rec('stop never posts a mark itself',
    'post(' not in STOP, 'a stop is the absence of a contact, not a kind of one')
PAUSE = extract(WORKER_JS, 'pause')
rec('pause clears the pending next-lead timer as well', 'nextStep' in PAUSE,
    'a bridge-offline pause followed by its own 700ms timer is not a pause')


# ================================================ 4. "ALL lanes" describes the lanes that exist
rec('LANE_ORDER is derived from the lanes that are actually baked',
    'LANE_ORDER=LANE_ALL.filter' in WORKER_JS.replace(' ', ''),
    'a hardcoded order can name a lane genMorningWorker does not bake (balloon)')
out = node(HARNESS_HEAD + LOOP_JS + """
var OUT = {};
OUT.order = LANE_ORDER.slice();
OUT.all = LANE_ALL.slice();
""" + HARNESS_TAIL)
rec('the derived order drops a lane with no baked queue',
    'balloon' in out['all'] and 'balloon' not in out['order'], out['order'])
rec('the derived order keeps every baked lane in LANE_ALL priority order',
    out['order'] == ['urgent', 'active', 'early'], out['order'])
CARD = WORKER_JS[WORKER_JS.index("mw-autoall"):][:900]
rec('the ALL-lanes subtitle is built from LANE_ORDER, not a hardcoded three',
    'LANE_ORDER.map' in CARD and 'LMETA.urgent.ic' not in CARD, CARD[:160])
rec('the card shows STOP instead of Start while a run is going',
    "data-fn='stop'" in WORKER_JS and re.search(r"\(auto[\s\S]{0,80}mw-stop", WORKER_JS) is not None)


# ================================= 5. the dial path honours #32's source tags and phbest
# Board scope, not the blob: _workerPhones decides the number doCall dials, the number the Call list
# shows, and the number PERSISTQ re-bakes tomorrow.
WP = extract(TPL_SRC, '_workerPhones')
PH_CASES = [
    # (phones, phsrc, phdnc, phbest) -> expected
    (['9540000001', '9540000002'], ['', ''], [False, False], None,
     ['9540000001', '9540000002'], 'untagged numbers keep build order'),
    (['9540000001', '9540000002'], ['ag', ''], [False, False], None,
     ['9540000002'], "the lead's own listing agent is out of the dial list"),
    (['9540000001', '9540000002'], ['xl', ''], [False, False], None,
     ['9540000002'], 'an office number on 3+ owners is out'),
    (['9540000001', '9540000002'], ['hh', ''], [False, False], None,
     ['9540000001', '9540000002'], 'a household number STAYS -- that is why it was kept'),
    (['9540000001', '9540000002'], ['nm', ''], [False, False], None,
     ['9540000001', '9540000002'], 'a namesake STAYS, ranked below the owner'),
    (['9540000001', '9540000002', '9540000003'], ['hh', '', ''], [False, False, False], 1,
     ['9540000002', '9540000001', '9540000003'], "phbest leads, the rest hold order"),
    (['9540000001', '9540000002'], ['ag', ''], [False, False], 0,
     ['9540000002'], 'phbest pointing at a dropped number does not resurrect it'),
    (['9540000001'], ['ag'], [False], None,
     [], 'agent-only lead has NO owner number -> falls through to Lookup'),
    (['9540000001', '9540000002'], ['', ''], [True, False], None,
     ['9540000002'], 'DNC still drops, as it always did'),
    ([], [], [], None, [], 'no numbers at all'),
]
js = ('var notes = {};\n' + WP + '\nconsole.log(JSON.stringify(' + json.dumps(
    [{'case': 'C%d' % n, 'phones': c[0], 'phsrc': c[1], 'phdnc': c[2], 'phbest': c[3]}
     for n, c in enumerate(PH_CASES)]
) + '.map(function(r){ return _workerPhones(r); })));')
got = node(js)
for n, c in enumerate(PH_CASES):
    rec('_workerPhones: %s' % c[5], got[n] == c[4], '%r want %r' % (got[n], c[4]))

rec('the worker card takes its phones from _workerPhones',
    'var phList = _workerPhones(r);' in TPL_SRC,
    'anchor: the phList assignment in _workerCard')
rec('the durable call queue re-vets its stored number against the same rule',
    '_workerPhones(r), _xd' in TPL_SRC,
    'anchor: the PERSISTQ bake -- a number queued by a pre-phsrc build must not be trusted')
rec('_workerPhones applies the same tag rule as the phone page',
    "['ag','xl']" in WP and "'hh'" not in WP.split('return keep')[0].replace("'hh'/'nm'", ''),
    'call_mode.py:1520 drops ag/xl and keeps hh/nm; these two must not drift')
CM = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
rec('call_mode still drops exactly ag/xl from its dial queue',
    "in ('ag', 'xl'):" in CM,
    'if the phone page changes its rule, this pairing has to be revisited')


# ============================================== 6. the reserved suppression surface is not touched
# CLAUDE.md reserves opt-outs, STOP detection, the send-time gates and cadence.py for the desktop
# session. Everything above READS a verdict (smsHref is baked by textablePhones, _workerEligible
# decides who is in PERSISTQ at all) and writes none.
for name in ('stopRun', 'startRun', 'runOne', 'advance', 'renderRunBar'):
    src = extract(WORKER_JS, name)
    rec('%s writes nothing on the suppression surface' % name,
        not re.search(r'optout|is_stop_text|bounced_emails|_isOptedOut|_textContactBlocked', src, re.I),
        name)
rec('_workerPhones reads flags and writes none',
    not re.search(r'=\s*(notes|r)\[', WP) and 'push(' in WP)


print('\n%s -- %d check(s), %d failure(s)' % ('FAILED' if fails else 'PASSED',
                                              len(checks), len(fails)))
if fails:
    print('failed: ' + ', '.join(fails))
sys.exit(1 if fails else 0)
