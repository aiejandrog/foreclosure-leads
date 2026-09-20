"""_calldetailtest -- the Morning Worker call queue keeps the property details up during the call.

Run:  python _calldetailtest.py   (exit 0 = safe; no network, no browser, no board, no real data)

WHAT BROKE (2026-09-20, reported by Alejandro: "WHEN I PRESS ON THE CALL KEEP THE DETAILS OF THE
PROPERTY THERE FOR ME TO READ OFF IT BECAUSE WHEN I PREWSS ON IT IT DISAPPEARS AND I CANNOT SEE
ANYTHING").

He was describing it literally. In the "Call these -- phone only, no email" panel, the click
delegate's data-qcall branch did this, 700 ms after the tap:

    var row = qc.closest("div");
    row.innerHTML = "<div ...>" + _esc2(cc) + " — what happened?</div>" + CALL_OUTCOMES buttons;

`qc` is the Call anchor and `qc.closest("div")` is the ENTIRE flex row, so that assignment
destroyed the owner name, the phone number, the property address, the judgment chip and the WP
button -- every field on the row -- and put the CASE NUMBER in their place. The case number is the
one value on that row that is of no use whatsoever on a live call. The dialer opens, the homeowner
picks up, and the screen he is reading from is blank. The same shape on the card path:
callOutcome() replaced the whole lead card with a screen carrying only the name and phones[0], so
the address, plaintiff, judgment and sale date went with it.

THE FIX, and why it is this one. The row is no longer written over by the handler at all. _qrow
renders the disposition strip itself, from CALLQ/TEXTQ data, whenever QCALLOPEN[case] is set --
so the details are re-rendered from the queue rather than preserved by luck, and there is no
innerHTML write that could take them out. It also means the strip SURVIVES a renderCallQ() repaint
(a bad-number mark elsewhere, a lead joining the queue) instead of disappearing mid-call. The
buttons are data-qout, driven by the document-level delegate -- the 2026-09-18 dead-button class
(see _textbatchtest) cannot come back through them.

Two smaller things ride along because the fix forced them: pressing Call now repaints a 320px
scroller holding 25+ rows, so the repaint has to keep the operator's scroll position (_keepScroll)
and put the dialled row back in view (_qrowIntoView). Without those, every call would throw him
back to the top of the list.

HOW THIS TESTS. The JS is EXTRACTED BY NAME from tracker_template.html and run under node, so what
is asserted is the code that ships, not a copy of it that can drift. renderCallQ is run for real
against a fake #mwcallqlist, once with the row idle and once with it on-call, and the rendered HTML
is searched for each field by value. The click delegate is run for real too, against a synthetic
tap on the Call anchor, with every collaborator stubbed into a recorder -- so the branch ORDER is
covered as well as the branch body. Anything that cannot be executed is asserted as an anchored
source shape, and the anchor is named in the failure so a rename fails loudly here instead of
silently on the board.

WHAT THIS FILE DOES NOT PROVE. It runs the shipped functions, not a browser: no Playwright, so the
real worker tab is never opened (the design preview builds no worker-eligible leads, so there is
nothing to open it on in a container). Layout -- that the enlarged number and address actually fit
the row on his screen -- was eyeballed against a rendering of the real _qrow output and is not
asserted here.

Nothing on the reserved suppression surface (CLAUDE.md) is touched or tested here; the two
suppression behaviours that already ride this delegate -- a STOP outcome scrubbing TEXTQ, and the
bad-number confirm + rate guard -- are asserted UNCHANGED so this fix cannot have loosened them.
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


def extract(src, name):
    """`function NAME(...){...}` by balanced-brace scan -- the trick _textbatchtest.py uses."""
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


def block_from(src, at, label):
    """The balanced `{...}` block that starts at the first brace at/after `at`, with its head."""
    j = src.index('{', at)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                return src[at:k + 1]
    raise SystemExit('unbalanced braces extracting ' + label)


def node(js):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(js)
        p = f.name
    try:
        # encoding= is not optional here -- same reason _textbatchtest spells it out: the board HTML
        # carries bytes the Windows locale codec cannot map, and the failure surfaces three frames
        # away as a JSON error if the pipe is decoded with it.
        r = subprocess.run(['node', p], capture_output=True, text=True, timeout=60,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0:
            raise SystemExit('node failed:\n' + (r.stderr or '')[:3000])
        return json.loads(r.stdout)
    finally:
        os.unlink(p)


TPL_SRC = open(TPL, encoding='utf-8').read()


# ------------------------------------------------------------------ the worker tab's blob script
# genMorningWorker builds the whole worker document as one big string expression. Rebuild that
# expression (dropping the Python-side // and /* */ commentary between the pieces, which is not
# part of the string) and evaluate it under node to recover the JS that actually ships inside the
# Blob. Kept identical to _textbatchtest's recovery on purpose: if the worker tab is restructured,
# both suites should fail at the same anchor rather than one of them quietly passing on nothing.
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
               'console.log(JSON.stringify({js: m ? m[1] : ""}));')
    if not doc['js']:
        raise SystemExit('could not recover the worker blob script from genMorningWorker.')
    return doc['js']


WORKER_JS = worker_blob_js(TPL_SRC)
rec('worker blob script recovered from genMorningWorker', len(WORKER_JS) > 20000,
    '%d chars' % len(WORKER_JS))

with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
    f.write(WORKER_JS)
    _p = f.name
_chk = subprocess.run(['node', '--check', _p], capture_output=True, text=True,
                      encoding='utf-8', errors='replace')
os.unlink(_p)
rec('worker blob script parses under node', _chk.returncode == 0, (_chk.stderr or '')[:400])


# ============================================================ 1. the row, rendered for real
# One lead, every field distinct and searchable by value, so "the address is still on the row" is
# a string the test can look for rather than a shape it has to trust. The case number is NOT a
# substring of any other field -- the old code replaced the row WITH the case number, so a test
# that could confuse the two would pass on the bug.
LEAD = {
    'c': 'CACE-24-01777',
    'name': 'ORENSTEIN, NORMAN C',
    'phone': '2124658093',
    'addr': '17125 N BAY RD 3207, Sunny Isles Beach, FL 33160',
    'judg': 322356,
    'wp': 'https://example.invalid/wp',
}

RENDER_JS = '\n'.join(extract(WORKER_JS, n) for n in
                      ('_esc2', '_telHref', '_qoutStrip', '_qrow', '_keepScroll', 'renderCallQ'))

# CALL_OUTCOMES is a `var` array literal, not a function -- take it by anchored source slice.
_co_at = WORKER_JS.find('var CALL_OUTCOMES=[')
if _co_at < 0:
    raise SystemExit('ANCHOR GONE: var CALL_OUTCOMES=[ is not in the blob script.')
CALL_OUTCOMES_SRC = WORKER_JS[_co_at:WORKER_JS.index('];', _co_at) + 2]

HARNESS = """
%(outcomes)s
%(render)s
var QCALLOPEN = {};
var CALLQ = [%(lead)s];
var scrollSet = [];
var listNode = {
  _html: "",
  set innerHTML(v){ this._html = v; },
  get innerHTML(){ return this._html; },
  _top: 140,
  set scrollTop(v){ scrollSet.push(v); this._top = v; },
  get scrollTop(){ return this._top; }
};
var wrapNode = { style: {} };
var nNode = { textContent: "" };
document = { getElementById: function(id){
  if(id === "mwcallqlist") return listNode;
  if(id === "mwcallq") return wrapNode;
  if(id === "mwcallqn") return nNode;
  return null;
} };

renderCallQ();
var idle = listNode.innerHTML;
var idleScroll = listNode.scrollTop;

// the tap: the delegate marks the lead on-call and repaints. Nothing writes over the row.
listNode.scrollTop = 140;
QCALLOPEN[%(case)s] = 1;
renderCallQ();
var oncall = listNode.innerHTML;

console.log(JSON.stringify({
  idle: idle, oncall: oncall,
  idleScroll: idleScroll, oncallScroll: listNode.scrollTop,
  counter: nNode.textContent,
  outcomeKeys: CALL_OUTCOMES.map(function(o){ return o.k; })
}));
""" % {'outcomes': CALL_OUTCOMES_SRC, 'render': RENDER_JS,
       'lead': json.dumps(LEAD), 'case': json.dumps(LEAD['c'])}

out = node(HARNESS)
IDLE, ONCALL = out['idle'], out['oncall']

# --- the idle row is unchanged: this is the baseline the on-call row is compared against
for field, label in (('name', 'owner name'), ('phone', 'phone number'), ('addr', 'property address')):
    rec('idle row carries the %s' % label, LEAD[field] in IDLE, IDLE[:160])
rec('idle row carries the judgment amount', '322,356' in IDLE, IDLE[:160])
rec('idle row carries the Call link', 'data-qcall=' in IDLE and 'tel:+12124658093' in IDLE)
rec('idle row carries no disposition strip', 'data-qout=' not in IDLE)

# --- THE REGRESSION. Every one of these was false before the fix: the row had been replaced.
for field, label in (('name', 'owner name'), ('phone', 'phone number'), ('addr', 'property address')):
    rec('ON THE CALL, the row still shows the %s' % label, LEAD[field] in ONCALL,
        'this is the reported bug -- the row was overwritten | ' + ONCALL[:200])
rec('ON THE CALL, the row still shows the judgment amount', '322,356' in ONCALL, ONCALL[:200])
rec('ON THE CALL, the WP people-search button is still there', LEAD['wp'] in ONCALL)
rec('ON THE CALL, the Call link is still there to redial', 'tel:+12124658093' in ONCALL)
rec('ON THE CALL, the bad-number button is still there', 'data-qbad=' in ONCALL)

# --- and the strip it gained
for k in out['outcomeKeys']:
    rec('disposition strip offers the %r outcome' % k,
        ("data-qout='%s|%s'" % (LEAD['c'], k)) in ONCALL)
rec('disposition strip offers a no-log way out ("still reading")',
    ("data-qoutlater='%s'" % LEAD['c']) in ONCALL)
rec('the strip is a SIBLING of the details, not a replacement for them',
    ONCALL.index(LEAD['addr']) < ONCALL.index('data-qout='),
    'details must render above the buttons he is about to press')
rec('the row is marked on-call so it is findable in a 25-row list',
    'background:#fffdf4' in ONCALL)
rec('every row carries a stable data-qrow identity', ("data-qrow='%s'" % LEAD['c']) in ONCALL)

# --- the scroller. 25 rows in a 320px box: a repaint that resets scrollTop puts him back at the
#     top of the list on every single call, which is its own version of "I cannot see anything".
rec('renderCallQ puts the scroll position back after the repaint',
    out['oncallScroll'] == 140, 'scrollTop came back as %r, expected 140' % out['oncallScroll'])
rec('the row counter still reads from CALLQ', 'to call' in out['counter'], out['counter'])


# ==================================================== 2. the handler may never write over the row
# The fix is not "put the fields back in the string the handler writes" -- it is that the handler
# does not write the row at all. Assert the shape, because a future edit that reintroduces the
# write would still pass section 1 if it happened to re-emit the fields today.
_qc_at = WORKER_JS.find('closest("[data-qcall]")')
if _qc_at < 0:
    raise SystemExit('ANCHOR GONE: the [data-qcall] branch of the click delegate.')
QCALL_BRANCH = block_from(WORKER_JS, WORKER_JS.index('if(qc)', _qc_at), 'data-qcall branch')

rec('the Call handler never assigns row.innerHTML',
    'row.innerHTML' not in QCALL_BRANCH,
    'THE BUG: qc.closest("div").innerHTML = ... wiped name, phone, address and judgment')
rec('the Call handler never reaches for the enclosing div at all',
    'closest("div")' not in QCALL_BRANCH,
    'closest("div") from the Call anchor is the whole row -- there is nothing safe to do with it')
rec('the Call handler marks the lead on-call instead', 'QCALLOPEN[cc]=1' in QCALL_BRANCH)
rec('the Call handler repaints from the queue data',
    'renderCallQ()' in QCALL_BRANCH and 'renderTextQ()' in QCALL_BRANCH)
rec('the Call handler brings the dialled row back into view',
    '_qrowIntoView(cc)' in QCALL_BRANCH)
rec('the dial anchor is still a plain tel: link (calls are never capped)',
    'preventDefault' not in QCALL_BRANCH,
    'preventing the default here would stop the phone dialling')
rec('the outcome buttons are wired by data attribute, not a property handler',
    '.onclick' not in extract(WORKER_JS, '_qoutStrip'),
    'a property handler on an innerHTML-written node dies on the next repaint (2026-09-18)')

# The strip is rendered by _qrow from state, which is what makes it survive a repaint.
_QROW = extract(WORKER_JS, '_qrow')
rec('_qrow renders the strip from QCALLOPEN', 'QCALLOPEN[x.c]' in _QROW)
rec('_qrow renders the strip after the detail block and the buttons',
    _QROW.index('x.addr') < _QROW.index('_qoutStrip(x)'))

# Closing the loop: an outcome, a "still reading", and a bad-number mark all clear the flag, or the
# row would stay stuck in on-call styling for the rest of the session.
rec('logging an outcome clears the on-call flag', 'delete QCALLOPEN[pp[0]]' in WORKER_JS)
rec('"still reading" clears the on-call flag without logging',
    'delete QCALLOPEN[lc]' in WORKER_JS and
    re.search(r'data-qoutlater\]"\);.{0,200}?delete QCALLOPEN\[lc\]', WORKER_JS, re.S) is not None)
rec('a bad-number mark clears the on-call flag', 'delete QCALLOPEN[bc]' in WORKER_JS)


# ================================= 2b. the delegate itself, run against a synthetic Call click
# Section 2 asserts the SHAPE of the branch; this runs it. The whole click handler is lifted out of
# the shipped blob (the one that carries the queue-panel branches, not the lane switcher above it)
# and every collaborator it names is stubbed into a recorder, so what is exercised is the real
# branch ORDER as well as the real branch body -- a new branch inserted above data-qcall that
# swallowed the event would pass every source-shape check in section 2 and fail here.
_qc_for_delegate = WORKER_JS.find('closest("[data-qcall]")')
_deleg_at = WORKER_JS.rfind('document.addEventListener("click", function(e){', 0, _qc_for_delegate)
if _deleg_at < 0:
    raise SystemExit('ANCHOR GONE: the click delegate carrying the queue-panel branches.')
_open = WORKER_JS.index('{', WORKER_JS.index('function(e)', _deleg_at))
_d = 0
DELEGATE_BODY = None
for _k in range(_open, len(WORKER_JS)):
    if WORKER_JS[_k] == '{':
        _d += 1
    elif WORKER_JS[_k] == '}':
        _d -= 1
        if _d == 0:
            DELEGATE_BODY = WORKER_JS[_open + 1:_k]
            break
if not DELEGATE_BODY:
    raise SystemExit('unbalanced braces extracting the click delegate body.')

DELEG_HARNESS = """
%(outcomes)s
var calls = [], QCALLOPEN = {}, CALLQ = [{c:%(case)s}], TEXTQ = [];
var timers = [];
function rec(n){ return function(){ calls.push(n); }; }
var _copyCallList=rec("_copyCallList"), _copyDay=rec("_copyDay"), stopRun=rec("stopRun"),
    startRun=rec("startRun"), textBatchStart=rec("textBatchStart"), openHere=rec("openHere"),
    confirmSend=rec("confirmSend"), post=rec("post"), addLog=rec("addLog"),
    renderCallQ=rec("renderCallQ"), renderTextQ=rec("renderTextQ"),
    _qrowIntoView=rec("_qrowIntoView");
function _wftsa(){ return {safe:true, label:""}; }
function sentToday(){ return 0; }
function alert(){ calls.push("alert"); }
function confirm(){ calls.push("confirm"); return true; }
setTimeout = function(fn){ timers.push(fn); };
var window = {};
function onClick(e){ %(body)s }

// a tap on the Call anchor of the row for %(case)s
var prevented = false;
var anchor = {
  getAttribute: function(a){ return a === "data-qcall" ? %(case)s : null; },
  closest: function(sel){ calls.push("closest:" + sel); return null; }
};
onClick({ target: { closest: function(sel){ return sel === "[data-qcall]" ? anchor : null; } },
          preventDefault: function(){ prevented = true; } });
var afterTap = { flag: !!QCALLOPEN[%(case)s], calls: calls.slice(), prevented: prevented,
                 timers: timers.length };

// ...and the 700ms repaint that follows it
calls.length = 0;
timers.forEach(function(f){ f(); });
var afterTimer = calls.slice();

// "Still reading -- hide these": clears the flag, logs nothing, touches no ledger
calls.length = 0;
var later = { getAttribute: function(a){ return a === "data-qoutlater" ? %(case)s : null; } };
onClick({ target: { closest: function(sel){ return sel === "[data-qoutlater]" ? later : null; } },
          preventDefault: function(){} });

console.log(JSON.stringify({ afterTap: afterTap, afterTimer: afterTimer,
                             laterCalls: calls.slice(), flagAfterLater: !!QCALLOPEN[%(case)s],
                             queueAfterLater: CALLQ.length }));
""" % {'outcomes': CALL_OUTCOMES_SRC, 'body': DELEGATE_BODY, 'case': json.dumps(LEAD['c'])}

d = node(DELEG_HARNESS)

rec('a Call tap reaches the data-qcall branch of the real delegate',
    'addLog' in d['afterTap']['calls'], d['afterTap']['calls'])
rec('a Call tap marks the lead on-call', d['afterTap']['flag'] is True)
rec('a Call tap never asks the anchor for its enclosing div',
    not any(c.startswith('closest:div') for c in d['afterTap']['calls']),
    d['afterTap']['calls'])
rec('a Call tap does not preventDefault (the tel: link must still dial)',
    d['afterTap']['prevented'] is False)
rec('a Call tap schedules exactly one follow-up repaint', d['afterTap']['timers'] == 1,
    d['afterTap']['timers'])
rec('the repaint rebuilds both queues and scrolls the row into view',
    d['afterTimer'] == ['renderCallQ', 'renderTextQ', '_qrowIntoView'], d['afterTimer'])
rec('a Call tap logs nothing to the board ledger (an outcome does that)',
    'post' not in d['afterTap']['calls'] + d['afterTimer'], 'dialing is not a disposition')

rec('"still reading" clears the flag', d['flagAfterLater'] is False)
rec('"still reading" leaves the lead in the queue', d['queueAfterLater'] == 1)
rec('"still reading" posts nothing and logs nothing',
    'post' not in d['laterCalls'] and 'addLog' not in d['laterCalls'], d['laterCalls'])
rec('"still reading" repaints so the buttons actually go away',
    'renderCallQ' in d['laterCalls'])


# ============================================ 3. the card path lost the details the same way
CARD = extract(WORKER_JS, 'callOutcome')
rec('the card disposition screen carries the property address', 'r.addr' in CARD,
    'it used to show only the name and phones[0]')
rec('the card disposition screen carries the judgment', 'r.judg' in CARD)
rec('the card disposition screen carries the plaintiff and the sale date',
    'r.plaintiff' in CARD and 'r.saleDate' in CARD)
rec('the card disposition screen still shows the name and the number being dialled',
    'r.first||r.owner' in CARD and 'r.phones[0]' in CARD)
rec('the card disposition buttons are unchanged (.mwoc, one per CALL_OUTCOMES)',
    "data-oc='\"+o.k+\"'" in CARD and 'mwocwrap' in CARD)

# doCall still renders the outcome screen BEFORE dialling -- the 2026 ordering fix. An anchor here
# because this change edited the screen that ordering exists to protect.
DOCALL = extract(WORKER_JS, 'doCall')
rec('doCall still renders the outcome screen before handing the URL to the dialer',
    DOCALL.index('callOutcome(r)') < DOCALL.index('a.click()'),
    'reversing this tore the tab down mid-click and the disposition bar never appeared')


# ================================ 4. nothing that suppresses anyone was loosened by this change
# CLAUDE.md reserves the suppression surface. This fix touches the row ABOVE those gates and must
# leave every one of them exactly where it was; these are guards, not new behaviour.
_qo_at = WORKER_JS.find('closest("[data-qout]")')
QOUT_BRANCH = block_from(WORKER_JS, WORKER_JS.index('if(qo)', _qo_at), 'data-qout branch')
rec('a suppressing outcome still scrubs TEXTQ as well as CALLQ',
    'if(oc.sup){ TEXTQ=TEXTQ.filter' in QOUT_BRANCH,
    'a STOP logged from the panel must not leave the owner in the text batch (2026-08-19)')
rec('every outcome still posts to the board ledger', 'post("callout"' in QOUT_BRANCH)
rec('the bad-number confirm is still in the tap path', 'confirm("Mark ' in WORKER_JS)
rec('the bad-number rate guard is still armed', 'window.__badTimes' in WORKER_JS)
rec('the text queue still re-checks the FTSA window before opening a composer',
    '_wftsa()' in extract(WORKER_JS, 'textBatchStep'))

# ...and this suite must not be the thing that edits them. Named files from CLAUDE.md's table.
RESERVED = ('optout_sync.py', 'cadence.py', 'replies.py', 'optouts.json', 'bounced_emails.json')
rec('this change is confined to the worker UI, not the suppression surface',
    all(f not in os.popen('git diff --name-only HEAD').read() for f in RESERVED)
    if os.path.isdir(os.path.join(HERE, '.git')) else True,
    'informational -- re-run from a git checkout to mean anything')


print('\n%s -- %d check(s), %d failure(s)' % ('FAILED' if fails else 'PASSED',
                                              len(checks), len(fails)))
if fails:
    print('failed: ' + ', '.join(fails))
sys.exit(1 if fails else 0)
