"""Call Mode wrong-number chooser (2026-10-21).

Alejandro, 10-21: "every time that we call, it just skips the lead to another prospect". 'Wrong
number' writes n.wrongown and drops the whole lead, even when skip trace left three more numbers on
it. Now, when other live numbers remain, the tap asks: only this number (the existing badnum path:
number marked bad, lead stays, next number offered) or the owner is wrong on every number (the
existing wrong path, unchanged). With no other live number it logs 'wrong' exactly as before.

The JS is extracted by name from call_mode.py and run under node. No browser, no board.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra else ''))
    if not ok:
        fails.append(name)


def grab_fn(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}\n', i)
    rec('function present: ' + name, i >= 0 and j > 0)
    return SRC[i:j + 2] if i >= 0 and j > 0 else ''


def node(js):
    fd, path = tempfile.mkstemp(suffix='.js')
    os.close(fd)
    io.open(path, 'w', encoding='utf-8').write(js)
    try:
        p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.remove(path)


# ---- static: the wiring around the chooser -------------------------------------------------------
so = SRC[SRC.find('function screenOutcome('):SRC.find('function otherLivePh(')]
rec('wrong asks only when another live number exists',
    "if(o.k==='wrong'){" in so and 'var _others = otherLivePh(r, phIdx);' in so and 'if(_others > 0){ wrongChoice(' in so)
rec('every other outcome goes straight through', 'finish(o, false);' in so)
rec('the chooser picks an EXISTING outcome key', "finish(OUTCOMES.filter(function(z){return z.k===k;})[0], true)" in so)
rec('badnum confirm still fires on the plain Bad number button',
    "if(o.k==='badnum' && !chosen && !confirm(" in so)
rec('dnc confirm untouched', "if(o.k==='dnc' && !confirm('They asked to stop." in so)
rec('wrong still ends the lead with no follow-up offer',
    "if(o.k==='dnc'||o.k==='wrong'||o.k==='notint'){" in so)
lo = SRC[SRC.find('function logOutcome('):SRC.find('function queueSync(')]
rec('logOutcome wrong still writes wrongown',
    "else if(o.k==='wrong'){ n.wrongown=n.wrongown||today(); n.status=n.status||'Wrong number'; }" in lo)
rec('logOutcome badnum still writes badph', 'n.badph.push(_bd)' in lo)
ac = SRC[SRC.find('function afterCall('):SRC.find('function afterCall(') + 2500]
rec('after-call wraps to an earlier number not rung today',
    'if(_nextPh < 0) _nextPh = untriedEarlierPh(r, phIdx);' in ac)
rec('bad number / not the owner put the next number FIRST on the panel',
    "var _nphTop = (o.k==='badnum'||o.k==='gate') && _nextPh >= 0;" in ac
    and "+ (_nphTop ? '<button id=\"nph\" class=\"big\"" in SRC
    and "(miss && _nextPh >= 0 && !_nphTop)" in SRC)
rec('badnum lands on the after-call panel (the next-number button lives there)',
    "miss = (o.k==='noanswer'||o.k==='voicemail'||o.k==='badnum'||o.k==='gate')" in ac)

# ---- behaviour under node -------------------------------------------------------------------------
FNS = ''.join(grab_fn(n) for n in ('badSet', 'nextLivePh', 'untriedEarlierPh', 'otherLivePh', 'wrongChoice'))
HARNESS = FNS + r"""
var notes = {}, painted = 0, toasts = [];
function today(){ return '2026-10-21'; }
function fmt(x){ return String(x); }
function toast(t){ toasts.push(t); }
function logErr(){}
function screenOutcome(){ painted++; }
function mkBtn(wc){ return {dataset:{wc:wc}, disabled:false, onclick:null}; }
var BOX = null, window = {};
var document = {querySelector: function(sel){ return sel === '.oc' ? BOX : null; }};
function mkBox(){
  var b = {innerHTML:'', _btns:null};
  b.querySelectorAll = function(){
    if(!b._btns){
      var m = b.innerHTML.match(/data-wc="([a-z]+)"/g) || [];
      b._btns = m.map(function(x){ return mkBtn(x.slice(9, -1)); });
    }
    return b._btns;
  };
  return b;
}
var out = {};
var R = {c:'A', p:['3055550101', '3055550102', '3055550103']};
out.others_all_live = otherLivePh(R, 0);
notes.A = {badph:['3055550102']};
out.others_one_bad = otherLivePh(R, 0);
notes.A = {badph:['3055550102', '3055550103']};
out.others_none = otherLivePh(R, 0);
out.others_single = otherLivePh({c:'S', p:['3055550199']}, 0);
notes.A = {badph:['(305) 555-0101']};          // formatted mark still matches by digits
out.others_fmt = otherLivePh(R, 1);
notes = {};
notes.A = {dials:[{d:'2026-10-21', ph4:'0102', oc:'noanswer'}]};
out.wrap = untriedEarlierPh(R, 2);                 // opened on 3, 2 rung today -> 1
notes.A = {dials:[{d:'2026-10-21', ph4:'0101'}, {d:'2026-10-21', ph4:'0102'}]};
out.wrap_none = untriedEarlierPh(R, 2);            // 1 and 2 both rung today -> no loop back
notes.A = {dials:[{d:'2026-10-20', ph4:'0101'}], badph:['3055550102']};
out.wrap_yday = untriedEarlierPh(R, 2);            // rung yesterday counts as untried today
notes = {};

function run(choice){
  BOX = mkBox(); painted = 0; var got = [];
  wrongChoice(R, R.p[0], 2, function(k){ got.push(k); });
  var btn = BOX.querySelectorAll().filter(function(x){ return x.dataset.wc === choice; })[0];
  btn.onclick();
  return {got: got, painted: painted, dis: BOX.querySelectorAll().every(function(x){ return x.disabled; }),
          html: BOX.innerHTML};
}
out.pick_this = run('badnum');
out.pick_all = run('wrong');
out.pick_back = run('back');
BOX = null; var g = []; wrongChoice(R, R.p[0], 2, function(k){ g.push(k); }); out.no_box = g;
console.log(JSON.stringify(out));
"""
rc, so_, se = node(HARNESS)
rec('harness ran', rc == 0, se)
o = json.loads(so_.strip().splitlines()[-1]) if rc == 0 else {}
rec('three live numbers: two others', o.get('others_all_live') == 2, o)
rec('one marked bad: one other', o.get('others_one_bad') == 1, o)
rec('all others bad: zero, so wrong logs as before', o.get('others_none') == 0, o)
rec('single-number lead: zero, so wrong logs as before', o.get('others_single') == 0, o)
rec('bad marks compare by digits', o.get('others_fmt') == 1, o)
rec('wrap finds an earlier number not rung today', o.get('wrap') == 0, o)
rec('wrap never loops back to a number rung today', o.get('wrap_none') == -1, o)
rec('wrap skips bad, takes a number rung only yesterday', o.get('wrap_yday') == 0, o)
pt = o.get('pick_this') or {}
rec('"only this number" picks badnum, once', pt.get('got') == ['badnum'] and pt.get('painted') == 0, pt)
rec('chooser says how many other numbers', '2 other numbers' in (pt.get('html') or ''), pt.get('html'))
pa = o.get('pick_all') or {}
rec('"every number" picks wrong, once', pa.get('got') == ['wrong'], pa)
pb = o.get('pick_back') or {}
rec('Back logs nothing and repaints', pb.get('got') == [] and pb.get('painted') == 1, pb)
rec('a tap disables the chooser (no double log)', pt.get('dis') and pa.get('dis'), (pt.get('dis'), pa.get('dis')))
rec('no outcome box: falls back to the old wrong path', o.get('no_box') == ['wrong'], o.get('no_box'))

print('\nALL PASS (0 failed)' if not fails else '\n%d FAILED: %s' % (len(fails), fails))
sys.exit(1 if fails else 0)
