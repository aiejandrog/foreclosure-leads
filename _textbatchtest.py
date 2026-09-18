"""_textbatchtest -- the Morning Worker's "Start text batch" button and every dial link.

Run:  python _textbatchtest.py   (exit 0 = safe; no network, no browser, no board, no real data)

WHAT BROKE (2026-09-18, reported by Alejandro: "the buttons of the start text batch to work where
i am just sending messages in batch doesnt work and the other buttons its not linked to the phone
link or something?").

  1. THE START-TEXT-BATCH BUTTON WAS DEAD, on essentially every morning. The queue-clear screen in
     genMorningWorker's render() appended the button with `main.innerHTML+=` and then assigned
     `dtb.onclick=textBatchStart` -- and the Call-Mode block directly below it did
     `main.innerHTML+=` a SECOND time. Assigning innerHTML re-serializes and re-parses every child
     of #mwmain, so the second append discarded the exact node the handler sat on and rebuilt a
     handler-less copy. CALLQ is non-empty on any normal run (it is seeded at open from the durable
     fcCallQueue and takes every phone-only lead that is not textable), so the second append almost
     always ran. Pressing the button did nothing and logged nothing -- there was no handler left to
     write a log line -- which is how a morning ends at "texts 0 - attempts 0".

     Now the screen is built as ONE string and #mwmain is assigned ONCE, and the button carries
     `data-textbatch` so the document-level click delegate runs it. A delegate on `document` cannot
     be detached by a re-parse, so a third block appended here later cannot kill it again.

  2. EVERY DIAL LINK WAS BUILT BY HAND, in two shapes, neither normalized:
        'tel:'   + digits   -- bare body; an 11-digit "19545551234" passed straight through
        'tel:+1' + digits   -- "+119545551234" as soon as the number already carried its country
                               code, which is an invalid number the dialer drops, and a live
                               "tel:+1" that dials nothing when there is no number at all.
     Operator-typed numbers (the Talk-to prompt, the notes phone field) and scraped Whitepages rows
     both arrive 11-digit, so both shapes were reachable. _smsNum has always normalized correctly,
     which is why the sms: links worked and the tel: ones did not. Board scope now has _telNum()
     built on _smsNum; the worker tab is a Blob document with its own global scope, so it carries
     _telHref() -- and this test runs BOTH and asserts they agree case for case.

HOW THIS TESTS. The JS is EXTRACTED BY NAME from tracker_template.html and run under node, so what
is asserted is the code that ships, not a copy of it that can drift. The queue-clear screen is run
for real against a fake #mwmain that counts assignments, which is what proves the re-parse is gone.
Anything this file cannot execute is asserted as an anchored source shape, and the anchor is named
in the failure so a rename fails loudly here instead of silently on the board.
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
    if len(extra) > 180:
        extra = extra[:180] + ' ...'
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra) if extra else ''))
    if not ok:
        fails.append(name)


# Commentary in this file is prose ABOUT the broken shapes, so it contains them verbatim. Strip it
# before scanning the source, or the test fails on its own explanation of what it is testing.
def no_comments(src):
    out = []
    for line in src.split('\n'):
        t = line.lstrip()
        if t.startswith('//') or t.startswith('* ') or t.startswith('/*'):
            continue
        out.append(line)
    return '\n'.join(out)


def extract(src, name):
    """`function NAME(...){...}` by balanced-brace scan -- same trick _wplinktest.py uses."""
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
        r = subprocess.run(['node', p], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise SystemExit('node failed:\n' + (r.stderr or '')[:3000])
        return json.loads(r.stdout)
    finally:
        os.unlink(p)


TPL_SRC = open(TPL, encoding='utf-8').read()


# ------------------------------------------------------------------ the worker tab's blob script
# genMorningWorker builds the whole worker document as one big string expression. Rebuild that
# expression (dropping the // and /* */ commentary between the pieces, which is Python-side source
# and not part of the string) and evaluate it under node to recover the JS that actually ships
# inside the Blob. If this stops matching, the worker tab was restructured and this test must be
# rewritten with it rather than quietly passing on nothing.
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

# node must accept it as a program at all -- a syntax error here ships a worker tab that is
# completely inert, which looks exactly like "the buttons do not work".
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
    f.write(WORKER_JS)
    _p = f.name
_chk = subprocess.run(['node', '--check', _p], capture_output=True, text=True)
os.unlink(_p)
rec('worker blob script parses under node', _chk.returncode == 0, (_chk.stderr or '')[:400])


# ============================================================ 1. the dial links, run for real
# One table, both implementations. The two middle rows are the ones that were broken: a number
# that already carries its country code, in the two spellings the board actually stores.
CASES = [
    ('(954) 555-0143', 'tel:+19545550143', 'formatted 10-digit -- the common case'),
    ('9545550143', 'tel:+19545550143', 'bare 10-digit'),
    ('19545550143', 'tel:+19545550143', '11-digit with country code (was tel:+119545550143)'),
    ('+1 954-555-0143', 'tel:+19545550143', 'already E.164 (was tel:+119545550143)'),
    ('1-954-555-0143', 'tel:+19545550143', 'operator-typed with a leading 1'),
    ('', '', 'no number -- render no link, not a live tel:+1'),
    (None, '', 'null number'),
    ('   ', '', 'whitespace only'),
]

BOARD_JS = '\n'.join(extract(TPL_SRC, n) for n in ('_smsNum', '_telNum'))
board = node(BOARD_JS + '\nconsole.log(JSON.stringify(' +
             json.dumps([c[0] for c in CASES]) + '.map(function(p){return _telNum(p);})));')

WORKER_TEL = extract(WORKER_JS, '_telHref')
worker = node(WORKER_TEL + '\nconsole.log(JSON.stringify(' +
              json.dumps([c[0] for c in CASES]) + '.map(function(p){return _telHref(p);})));')

for n, (raw, want, why) in enumerate(CASES):
    rec('board _telNum(%r) -> %r  (%s)' % (raw, want, why), board[n] == want, board[n])
    rec('worker _telHref(%r) agrees with the board' % (raw,), worker[n] == board[n],
        '%r vs %r' % (worker[n], board[n]))

# A tel: link the OS will actually dial: "+1" then exactly ten digits, or nothing at all.
rec('every dial link is E.164 or empty',
    all(v == '' or re.fullmatch(r'tel:\+1\d{10}', v) for v in board + worker),
    [v for v in board + worker if v and not re.fullmatch(r'tel:\+1\d{10}', v)])

# _telNum must be BUILT ON _smsNum, not be a second copy of the same rules that can drift from it.
# The whole reason the sms: links worked and the tel: links did not is that there was one correct
# normalizer and the dial sites did not call it.
rec('_telNum is built on _smsNum (one normalizer, not two)',
    '_smsNum(ph)' in extract(TPL_SRC, '_telNum'))

# ...and nothing may hand-roll a tel: href again. Both broken shapes, anywhere in the file.
TPL_CODE = no_comments(TPL_SRC)
hand_rolled = re.findall(r'href="tel:[^"]{0,40}', TPL_CODE)
rec('no hand-built tel: href survives in the board source', not hand_rolled, hand_rolled[:4])
rec('no "tel:+1"+digits concatenation survives',
    "'tel:+1'+" not in TPL_CODE and '"tel:+1"+String' not in TPL_CODE)


# ====================================================== 2. the queue-clear screen, run for real
# Pull the `if(i>=Q.length){ ... }` branch of render() out of the shipped blob script and run it
# against a fake #mwmain that records every assignment. Two things must hold:
#   - #mwmain is assigned EXACTLY ONCE. A second assignment is a re-parse, and a re-parse is what
#     detached the button's handler. Counting assignments is the regression itself, not a proxy.
#   - both the text-batch button and the Call-Mode link survive into that single assignment.
at = WORKER_JS.index('if(i>=Q.length){')
depth, done_branch = 0, None
for k in range(WORKER_JS.index('{', at), len(WORKER_JS)):
    if WORKER_JS[k] == '{':
        depth += 1
    elif WORKER_JS[k] == '}':
        depth -= 1
        if depth == 0:
            done_branch = WORKER_JS[at:k + 1]
            break
if not done_branch:
    raise SystemExit('ANCHOR GONE: the if(i>=Q.length) queue-clear branch of render().')

HARNESS = """
var writes = [];
var main = { set innerHTML(v){ writes.push(v); }, get innerHTML(){ return writes[writes.length-1]||""; } };
var prog = { textContent: "" };
var STATS = {callout:3, call:3, sent:12, text:0, email:0, wp:1, skip:2};
var LANES = {replied:[]};
var CALLQ = [{c:"A"},{c:"B"}];        // the normal morning: phone-only, not textable
var TEXTQ = [{c:"C"},{c:"D"},{c:"E"}];// three leads waiting on a text
var Q = [1,2,3], i = 3, tbOn = false;
var CAP = {max:50};
function sentToday(){ return 12; }
function renderLog(){}
function run(){ %s }
run();
console.log(JSON.stringify({n: writes.length, html: writes.join("\\n<<<SEPARATE WRITE>>>\\n")}));
""" % done_branch

out = node(HARNESS)

rec('queue-clear screen assigns #mwmain exactly once (no re-parse)', out['n'] == 1,
    '%d assignment(s); a second one is the bug that detached the handler' % out['n'])
rec('no re-parse marker in the rendered screen', '<<<SEPARATE WRITE>>>' not in out['html'])
rec('the Start-text-batch button is rendered', 'mwdonetext' in out['html'])
rec('it carries the text batch count', 'Start text batch (3)' in out['html'], out['html'][-400:])
rec('the Call-Mode hand-off is rendered alongside it', 'mwdonecall' in out['html'])

# The handler. A property handler on a node inside an innerHTML write is exactly what died here;
# the button must be driven by the document-level delegate instead.
rec('the button is wired by data attribute, not by a property handler',
    'data-textbatch' in out['html'])
rec('nothing assigns .onclick to the queue-clear button any more',
    'mwdonetext").onclick' not in WORKER_JS and 'dtb.onclick' not in WORKER_JS,
    'a property handler here does not survive a sibling innerHTML write')
rec('the panel click delegate matches the queue-clear button',
    '#mwtextqgo,[data-textbatch]' in WORKER_JS,
    'anchor: the closest() call that reaches textBatchStart')
rec('the delegate still runs textBatchStart',
    re.search(r'closest\("#mwtextqgo,\[data-textbatch\]"\);\s*if\(go\)\{[^}]*textBatchStart\(\)',
              WORKER_JS) is not None)

# Whatever else this screen grows, it must not go back to appending.
rec('the queue-clear branch never appends to innerHTML',
    'main.innerHTML+=' not in done_branch and 'main.innerHTML +=' not in done_branch)


# ============================================ 3. what the batch does once the button works again
# Not a rewrite of the batch -- just a guard that this fix did not quietly open the gates the batch
# has always enforced, since the only reason to press the button is that it now sends.
start = extract(WORKER_JS, 'textBatchStart')
step = extract(WORKER_JS, 'textBatchStep')
rec('batch start still refuses outside the 8am-8pm window', '_wftsa()' in start)
rec('batch start still refuses an empty queue', 'TEXTQ.length' in start)
rec('every step re-checks the hours window', '_wftsa()' in step)
rec('every step re-checks the daily cap', 'sentToday()>=CAP.max' in step)
rec('nothing is auto-sent -- the step opens the composer and waits for a human confirmation',
    'openHere(x.sms)' in step and 'confirmSend(' in step)

# The batch and the per-row Text button are the same gate. A row that renders a dial anchor with
# no number is the small sibling of a dead button: it looks live and does nothing.
render_tq = extract(WORKER_JS, 'renderTextQ')
rec('a text-queue row with no number renders no dial anchor',
    '_telHref(x.phone)?' in render_tq, 'anchor: the guard in renderTextQ\'s _qrow call')


print('\n%s -- %d check(s), %d failure(s)' % ('FAILED' if fails else 'PASSED',
                                              len(checks), len(fails)))
if fails:
    print('failed: ' + ', '.join(fails))
sys.exit(1 if fails else 0)
