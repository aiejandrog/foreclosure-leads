"""No marks across phones on team sync (2026-10-18, "fix the no sync right now").

A soft or hard no is n.no / noAt / noWasLp / resurf on the case note. The board's _mergeLead never
named those fields, and Call Mode's 09-25 wrapper wrote them onto the [out, ch] array it returns,
so a teammate's pull dropped them: a soft no on a lead the other phone had as Contacted stayed on
normal cadence there, and a soft no already resurfaced once could resurface a second time.

Runs the REAL mergeNotes / _mergeLead, extracted from tracker_template.html the way the Call Mode
build extracts them, under node. No browser, no board data.
"""
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TPL = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra else ''))
    if not ok:
        fails.append(name)


SYNC = call_mode.extract_sync_js(TPL)
rec('extracted sync block carries the no-mark merge', '_softStands' in SYNC and "'resurf'" in SYNC)

T1, T2, T3 = 1760000000000, 1760100000000, 1760200000000
HARNESS = r"""
const vm = require('vm');
function device(){
  const store = {};
  const ctx = { console, JSON, Object, Date, Math, String, Array, Set, Number, Infinity,
    localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } },
    crypto: {}, TextEncoder, TextDecoder, setTimeout(){}, clearTimeout(){}, setInterval(){}, fetch: async () => { throw 0; },
    document: { addEventListener(){}, getElementById(){ return null; } }, window: { addEventListener(){} },
    navigator: { userAgent: 'node' } };
  vm.createContext(ctx);
  vm.runInContext("var T1 = " + T1 + ", T2 = " + T2 + ", T3 = " + T3 + ";", ctx);
  vm.runInContext("var notes = {}, saves = 0; function _nowTS(){ return '2026-10-18 14:00'; } function _today(){ return '2026-10-18'; }" +
    " function save(){ saves++; } function recompute(){} function render(){} function syncFreshness(){} function syncStatus(){} function esc(s){ return String(s); }" +
    " function _devSrc(){ return 'test'; }\n" + SYNC, ctx);
  return { run: js => vm.runInContext(js, ctx) };
}
const T1 = %d, T2 = %d, T3 = %d;
const out = {};
const talk = (tsu, oc) => ({ts: 'ts' + tsu, tsu: tsu, ph4: '1111', oc: oc || 'talked', d: '2026-10-18'});

// 1. Teammate's soft no reaches a phone that has the lead as Contacted.
let B = device();
B.run("notes.C = {status:'Contacted', touches:[{d:'2026-10-17', ch:'call', out:'Talked'}]};");
const s1 = B.run("JSON.stringify(mergeNotes(" + JSON.stringify({C:{status:'Not interested', no:'soft', noAt:T1, noWasLp:false, resurf:0,
  touches:[{d:'2026-10-17', ch:'call', out:'Talked'}]}}) + "))");
out.soft = B.run("JSON.stringify({no: notes.C.no, noAt: notes.C.noAt, noWasLp: notes.C.noWasLp, resurf: notes.C.resurf})");
out.soft_saved = B.run('saves') + '|' + JSON.parse(s1).nos;

// 2. Pulling the same blob again changes nothing and saves nothing.
const s2 = JSON.parse(B.run("JSON.stringify(mergeNotes(" + JSON.stringify({C:{status:'Not interested', no:'soft', noAt:T1, noWasLp:false, resurf:0,
  touches:[{d:'2026-10-17', ch:'call', out:'Talked'}]}}) + "))"));
out.idem = s2.nos + '|' + s2.leads + '|' + B.run('saves');

// 3. A resurface spent on the teammate's phone is spent here too.
B.run("mergeNotes(" + JSON.stringify({C:{no:'soft', noAt:T1, noWasLp:false, resurf:1}}) + ")");
out.resurf = B.run('notes.C.resurf');

// 4. Hard beats soft, either direction, and keeps its noAt.
B.run("mergeNotes(" + JSON.stringify({C:{no:'hard', noAt:T2, status:'DO NOT CONTACT', optout:'2026-10-18'}}) + ")");
out.hard = B.run("notes.C.no + '|' + notes.C.noAt + '|' + notes.C.status");
B.run("mergeNotes(" + JSON.stringify({C:{no:'soft', noAt:T1, resurf:0}}) + ")");
out.hard_stays = B.run("notes.C.no");

// 5. Re-engaged: this phone cleared the soft no on a later talk; a stale copy does not put it back.
let A = device();
A.run("notes.R = {status:'Contacted', dials:[" + JSON.stringify(talk(T2)) + "]};");
A.run("mergeNotes(" + JSON.stringify({R:{no:'soft', noAt:T1, noWasLp:true, resurf:0}}) + ")");
out.reeng_local = A.run("String(notes.R.no) + '|' + ('noAt' in notes.R) + '|' + ('resurf' in notes.R)");

// 6. ...and the stale side drops it when the clearing phone's dials arrive.
let S = device();
S.run("notes.R = {status:'Not interested', no:'soft', noAt:T1, noWasLp:true, resurf:0};");
const s6 = JSON.parse(S.run("JSON.stringify(mergeNotes(" + JSON.stringify({R:{status:'Contacted', dials:[talk(T2, 'appt')]}}) + "))"));
out.reeng_remote = S.run("String(notes.R.no) + '|' + Object.keys(notes.R).filter(k => /^no|resurf/.test(k)).length") + '|' + s6.nos;

// 7. A NEW soft no after the re-engagement stands over the older, cleared one.
let N = device();
N.run("notes.R = {no:'soft', noAt:T3, noWasLp:false, resurf:0, dials:[" + JSON.stringify(talk(T2)) + "]};");
N.run("mergeNotes(" + JSON.stringify({R:{no:'soft', noAt:T1, noWasLp:true, resurf:1}}) + ")");
out.newsoft = N.run("notes.R.no + '|' + notes.R.noAt + '|' + notes.R.noWasLp + '|' + notes.R.resurf");

// 8. A talk logged BEFORE the no does not clear it.
let P = device();
P.run("notes.R = {dials:[" + JSON.stringify(talk(T1)) + "]};");
P.run("mergeNotes(" + JSON.stringify({R:{no:'soft', noAt:T2, noWasLp:false, resurf:0}}) + ")");
out.before = P.run("notes.R.no + '|' + notes.R.noAt");

// 9. Two soft nos: the latest noAt with its own noWasLp, resurf is the larger.
let E = device();
E.run("notes.R = {no:'soft', noAt:T2, noWasLp:true, resurf:0};");
E.run("mergeNotes(" + JSON.stringify({R:{no:'soft', noAt:T1, noWasLp:false, resurf:1}}) + ")");
out.two = E.run("notes.R.noAt + '|' + notes.R.noWasLp + '|' + notes.R.resurf");

// 11. Reviewer repro: a later no on the laptop survives a stale earlier no plus the late appointment
//     that cleared only the earlier one.
let L = device();
L.run("notes.R = {no:'soft', noAt:T3, noWasLp:false, resurf:0};");
L.run("mergeNotes(" + JSON.stringify({R:{no:'soft', noAt:T1, noWasLp:true, resurf:0}}) + ")");
L.run("mergeNotes(" + JSON.stringify({R:{status:'Appointment', dials:[talk(T2, 'appt')]}}) + ")");
out.laterno = L.run("notes.R.no + '|' + notes.R.noAt");

// 12. A repeat no (noAt kept from the first, notint row at T3) is not cleared by a talk at T2.
let Rp = device();
Rp.run("notes.R = {no:'soft', noAt:T1, noWasLp:false, resurf:1, dials:[" + JSON.stringify(talk(T3, 'notint')) + "]};");
Rp.run("mergeNotes(" + JSON.stringify({R:{dials:[talk(T2)]}}) + ")");
out.repeat = Rp.run("notes.R.no + '|' + notes.R.resurf");

// 13. A soft no with no noAt cannot be dated: it stands, and a dated one does not lend it a date.
let U = device();
U.run("notes.R = {no:'soft', noWasLp:true, resurf:0, dials:[" + JSON.stringify(talk(T2)) + "]};");
U.run("mergeNotes(" + JSON.stringify({R:{no:'soft', noAt:T1, noWasLp:false, resurf:0}}) + ")");
out.undated = U.run("notes.R.no + '|' + ('noAt' in notes.R) + '|' + notes.R.noWasLp");

// 10. Notes with no marks on either side gain none, and report no change.
let Z = device();
Z.run("notes.Q = {status:'Contacted'};");
const r10 = Z.run("JSON.stringify(_mergeLead(notes.Q, {status:'Contacted'}))");
out.none = r10;

console.log(JSON.stringify(out));
""" % (T1, T2, T3)

# Pass the script on stdin ("node -"), not via "-e": the SYNC block plus the harness
# overflow Windows' CreateProcess command-line cap (WinError 206) when inlined as an arg.
_SCRIPT = 'const SYNC = ' + json.dumps(SYNC) + ';\n' + HARNESS
p = subprocess.run(['node', '-'], input=_SCRIPT,
                   capture_output=True, text=True, encoding='utf-8')
if p.returncode != 0:
    rec('node harness ran', False, (p.stderr or p.stdout)[-1500:])
    sys.exit(1)
o = json.loads(p.stdout.strip().splitlines()[-1])

rec("a teammate's soft no lands on a phone that had the lead as Contacted",
    json.loads(o['soft']) == {'no': 'soft', 'noAt': T1, 'noWasLp': False, 'resurf': 0}, o['soft'])
rec('...and that merge is saved (counted as a change)', o['soft_saved'] == '1|1', o['soft_saved'])
rec('pulling the same blob again saves nothing', o['idem'] == '0|0|1', o['idem'])
rec("a resurface spent on the teammate's phone is spent here", o['resurf'] == 1, o['resurf'])
rec('a hard no wins over a soft no and keeps its noAt', o['hard'] == 'hard|%d|DO NOT CONTACT' % T2, o['hard'])
rec('a later soft no never downgrades a hard no', o['hard_stays'] == 'hard', o['hard_stays'])
rec('a soft no cleared by a later talk is not put back by a stale copy', o['reeng_local'] == 'undefined|false|false', o['reeng_local'])
rec("the stale phone drops the soft no when the clearing phone's appointment arrives",
    o['reeng_remote'] == 'undefined|0|1', o['reeng_remote'])
rec('a new soft no after the re-engagement stands, with its own noWasLp',
    o['newsoft'] == 'soft|%d|false|0' % T3, o['newsoft'])
rec('a talk logged before the no does not clear it', o['before'] == 'soft|%d' % T2, o['before'])
rec('two soft nos: latest noAt with its noWasLp, larger resurf', o['two'] == '%d|true|1' % T2, o['two'])
rec('a later no survives a stale earlier no and the appointment that cleared only that one',
    o['laterno'] == 'soft|%d' % T3, o['laterno'])
rec('a repeat no on the resurface is not cleared by a talk between the two nos', o['repeat'] == 'soft|1', o['repeat'])
rec('an undated soft no stands and is not given a date', o['undated'] == 'soft|false|true', o['undated'])
none = json.loads(o['none'])
rec('notes with no marks gain none and report no change',
    not any(k in none[0] for k in ('no', 'noAt', 'noWasLp', 'resurf')) and none[1].get('no') is False, o['none'])

# The phone page must not layer its own rule on top of the board's any more.
CM = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
rec('Call Mode no longer wraps _mergeLead', 'var _base = _mergeLead' not in CM)

print('\n%d failed' % len(fails))
sys.exit(1 if fails else 0)
