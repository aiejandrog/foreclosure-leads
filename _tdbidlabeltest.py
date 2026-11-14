"""_tdbidlabeltest.py - a tax deed's opening bid is never printed under a judgment label (F-data-05).

foreclosure_leads.make_tracker stores a tax deed's Opening Bid in `judgment` (the board's `judg`)
to reuse the money plumbing. Three board spots printed r.judg under a hard-coded judgment label:
  1. the Dial Ready sheet facts row (genDialReady, English and Spanish),
  2. the morning worker's "How did the call go?" screen (the plaintiff / amount / sale line),
  3. the morning worker's lead card facts grid.
A tax deed row (st='TD') must read "Opening bid" there; a foreclosure row must still read "Judgment".

Non-browser: the three source fragments are taken verbatim from tracker_template.html and evaluated
in node. Invented rows only, no network, no data files.
Run: DEALFLOW_DIR=<temp> python _tdbidlabeltest.py"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()

checks, fails = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    if len(extra) > 200:
        extra = extra[:200] + ' ...'
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra) if extra else ''))
    if not ok:
        fails.append(name)


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


def anchor(src, needle, start=0, what=''):
    i = src.find(needle, start)
    if i < 0:
        raise SystemExit('ANCHOR GONE: %s' % (what or needle))
    return i


# ------------------------------------------------------------------ the worker tab's blob script
# Same recovery as _calldetailtest / _textbatchtest: genMorningWorker builds the worker document as
# one string expression; rebuild it without the commentary between the pieces and let node return
# the JS that actually ships inside the Blob.
def worker_blob_js(src):
    at = anchor(src, 'function genMorningWorker(')
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
        raise SystemExit('could not recover the worker blob script from genMorningWorker.')
    return doc['js']


WORKER_JS = worker_blob_js(SRC)
rec('worker blob script recovered from genMorningWorker', len(WORKER_JS) > 20000,
    '%d chars' % len(WORKER_JS))

# ------------------------------------------------------------------ the three fragments, verbatim
# Spot 1: genDialReady, from the value/amount formatting through the end of the Spanish facts row.
_g = anchor(SRC, 'function genDialReady(')
_a = anchor(SRC, 'var valFmt = (r.value ?', _g, 'genDialReady valFmt')
_b = anchor(SRC, 'var factsES', _a, 'genDialReady factsES')
SPOT1 = SRC[_a:SRC.index(';\n', _b) + 1]

# Spot 2: the dial screen's plaintiff / amount / sale line.
_a = anchor(WORKER_JS, 'var _pj =', 0, 'worker dial screen _pj')
_end = 'join(" &middot; ");'
SPOT2 = WORKER_JS[_a:anchor(WORKER_JS, _end, _a, 'worker dial screen _pd') + len(_end)]

# Spot 3: the lead card facts cell that prints fm(r.judg) / fm(r.value), label included.
_amt = 'fm(r.judg)+" / "+fm(r.value)'
_m = anchor(WORKER_JS, _amt, 0, 'worker card amount cell')
_cell = '+"<div><div class=\'k\'>'
_a = WORKER_JS.rfind(_cell, 0, _m)
if _a < 0:
    raise SystemExit('ANCHOR GONE: worker card facts cell opener')
_close = '+"</div></div>"'
SPOT3 = WORKER_JS[_a:anchor(WORKER_JS, _close, _m, 'worker card cell close') + len(_close)]

# The worker card must carry `st`, or spots 2 and 3 cannot tell a tax deed from a foreclosure.
_wc = anchor(SRC, 'function _workerCard(')
_j = anchor(SRC, 'judg: r.judg||0', _wc, '_workerCard judg')
rec("the worker card that carries judg also carries st", "st: r.st||''" in SRC[_j - 600:_j + 200])

# ------------------------------------------------------------------ invented rows
# judg = obid = 12000 on the tax deed, exactly what make_tracker bakes (opening bid stored in judg).
TD = {'case': '2099-0001TD', 'st': 'TD', 'judg': 12000, 'obid': 12000, 'value': 250000,
      'plaintiff': 'Invented County Tax Collector', 'saleDate': '2099-01-15', 'days': 20}
FC = {'case': '2099-000001-CA-01', 'st': 'FC', 'judg': 12000, 'value': 250000,
      'plaintiff': 'Invented Bank NA', 'saleDate': '2099-01-15', 'days': 20}

HARNESS = r'''
function fact(k, v){ return '[' + k + '=' + v + ']'; }
function fm(n){ return n ? "$" + Math.round(+n).toLocaleString('en-US') : "-"; }
var chip = {t: 'chip'};
function spot1(r){ %(s1)s; return {en: factsEN, es: factsES}; }
function spot2(r){ %(s2)s return _pd; }
function spot3(r){ return "" %(s3)s; }
var rows = %(rows)s, out = {};
Object.keys(rows).forEach(function(k){
  out[k] = {s1: spot1(rows[k]), s2: spot2(rows[k]), s3: spot3(rows[k])};
});
console.log(JSON.stringify(out));
''' % {'s1': SPOT1, 's2': SPOT2, 's3': SPOT3, 'rows': json.dumps({'td': TD, 'fc': FC})}

out = node(HARNESS)
td, fc = out['td'], out['fc']


def has(s, w):
    return w.lower() in s.lower()


# --- spot 1: Dial Ready facts row, English and Spanish
rec('dial ready EN, tax deed: label reads Opening bid', has(td['s1']['en'], 'Opening bid'), td['s1']['en'])
rec('dial ready EN, tax deed: no judgment label', not has(td['s1']['en'], 'Judgment'), td['s1']['en'])
rec('dial ready EN, tax deed: the amount is still shown', '12,000' in td['s1']['en'])
rec('dial ready ES, tax deed: label reads Puja inicial', has(td['s1']['es'], 'Puja inicial'), td['s1']['es'])
rec('dial ready ES, tax deed: no Sentencia label', not has(td['s1']['es'], 'Sentencia'), td['s1']['es'])
rec('dial ready EN, foreclosure: label still reads Value / Judgment',
    'Value / Judgment' in fc['s1']['en'] and not has(fc['s1']['en'], 'Opening bid'), fc['s1']['en'])
rec('dial ready ES, foreclosure: label still reads Valor / Sentencia',
    'Valor / Sentencia' in fc['s1']['es'] and not has(fc['s1']['es'], 'Puja'), fc['s1']['es'])

# --- spot 2: worker dial screen line
rec('worker dial screen, tax deed: reads $12,000 opening bid', has(td['s2'], '$12,000 opening bid'), td['s2'])
rec('worker dial screen, tax deed: no judgment word', not has(td['s2'], 'judgment'), td['s2'])
rec('worker dial screen, foreclosure: still reads $12,000 judgment',
    '$12,000 judgment' in fc['s2'] and not has(fc['s2'], 'opening bid'), fc['s2'])

# --- spot 3: worker lead card facts cell
rec('worker card, tax deed: label reads Opening bid / Value', 'Opening bid / Value' in td['s3'], td['s3'])
rec('worker card, tax deed: no judgment label', not has(td['s3'], 'Judgment'), td['s3'])
rec('worker card, tax deed: the amount is still shown', '$12,000 / $250,000' in td['s3'], td['s3'])
rec('worker card, foreclosure: label still reads Judgment / Value',
    'Judgment / Value' in fc['s3'] and not has(fc['s3'], 'Opening bid'), fc['s3'])

print('\n%d checks, %d failed' % (len(checks), len(fails)))
if fails:
    for f in fails:
        print('  FAILED: ' + f)
    sys.exit(1)
print('PASS _tdbidlabeltest')
