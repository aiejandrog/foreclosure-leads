"""Call Mode two-tab clobber (2026-10-17).

Alejandro, 10-16 22:48: "I refreshed the call mode website and I got the retry of all the leads I
called". TODAY read 0 dials. Call Mode keys history by case number, and the 21:59 rebuild changed no
case key, so the calls were gone from the browser store itself. The page writes its WHOLE in-memory
`notes` on every save and, unlike the board, never listened for another tab's writes: a second Call
Mode tab (or the other seat's page) opened earlier kept an old snapshot, and its next save wrote that
snapshot over everything logged since in the tab in use. A reload then showed it.

Two pages are run under node against one shared localStorage, with the real _mergeLead extracted
from the board (which now carries the no-policy fields itself). No browser, no board data.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
TPL = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra else ''))
    if not ok:
        fails.append(name)


def grab(start, end):
    i = SRC.find(start)
    j = SRC.find(end, i)
    rec('present: ' + start[:40], i >= 0 and j > i)
    return SRC[i:j]


PAGE = grab("var _NOTESBAD=false;", "/* NAME BRIDGE")
SYNC = call_mode.extract_sync_js(TPL)

TAB = r"""
var LS='fcLeadNotes', notes={}, _FCGEN=0, toasts=[], errs=[], rechecks=0;
function _nowTS(){ return '2026-10-16 22:00'; } function _today(){ return '2026-10-16'; }
function save(){ _FCGEN++; saveNotes(); } function render(){} function recompute(){}
function syncFreshness(){} function syncStatus(){} function esc(s){ return String(s); }
function toast(t){ toasts.push(t); } function logErr(e, w){ errs.push(w); }
function teamRecheck(){ rechecks++; }
""" + SYNC + '\n' + PAGE + r"""
loadNotes();
"""

HARNESS = r"""
const vm = require('vm');
const store = {}; let full = false; const listeners = [];
const ls = { getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { if(full) throw new Error('QuotaExceededError'); const old = store[k]; store[k] = String(v);
    listeners.forEach(l => { if(l.who !== cur) l.fn({key:k, newValue:String(v), oldValue:old}); }); },
  removeItem: k => { delete store[k]; } };
let cur = null;
function tab(name, deliver, board){
  const ctx = { console, JSON, Object, Date, Math, String, Array, Set, Number, localStorage: ls,
    crypto: {}, TextEncoder, TextDecoder, setTimeout(){}, clearTimeout(){}, setInterval(){}, fetch: async () => { throw 0; },
    document: { addEventListener(){}, getElementById(){ return null; } } };
  ctx.window = { addEventListener(t, fn){ if(t === 'storage' && deliver && !board) listeners.push({who:name, fn}); } };
  vm.createContext(ctx); cur = name; vm.runInContext(TABSRC, ctx);
  /* the board's own listener (tracker_template.html:16634): merge the incoming store, save if changed */
  if(board) listeners.push({who:name, fn: ev => { cur = name; try { vm.runInContext('mergeNotes(JSON.parse(' + JSON.stringify(ev.newValue) + '))', ctx); } finally { cur = null; } }});
  cur = null;
  return { ctx, run(js){ cur = name; try { return vm.runInContext(js, ctx); } finally { cur = null; } } };
}
const out = {};
const dial = (c, ts) => `(notes['${c}'] = notes['${c}'] || {}).dials = (notes['${c}'].dials||[]).concat([{ts:'${ts}', tsu:${Date.parse('2026-10-16T20:00:00Z')}, d:'Fri Oct 16 2026', oc:'noanswer', by:'Alejandro', ph4:'1111'}]); save();`;

// 1. Stale tab, storage event NOT delivered (frozen background tab): old tab B saves after A logged.
store.fcLeadNotes = JSON.stringify({OLD:{dials:[{ts:'2026-10-15 10:00', oc:'noanswer', ph4:'2'}]}});
let A = tab('A', false), B = tab('B', false);
A.run(dial('C1', '2026-10-16 18:00')); A.run(dial('C2', '2026-10-16 18:05'));
B.run(dial('C3', '2026-10-16 18:10'));
let s = JSON.parse(store.fcLeadNotes);
out.frozen = ['OLD','C1','C2','C3'].map(c => c + ':' + ((s[c]||{}).dials||[]).length).join(',');

// 2. A hard no logged in A survives a stale save from B.
store.fcLeadNotes = JSON.stringify({P:{touches:[{d:'2026-10-10', ch:'call', out:'No answer'}]}});
A = tab('A', false); B = tab('B', false);
A.run("notes.P.no='hard'; notes.P.noAt=1760000000000; notes.P.status='DO NOT CONTACT'; save();");
B.run("(notes.P.touches=notes.P.touches||[]).push({d:'2026-10-16', ch:'call', out:'No answer'}); save();");
s = JSON.parse(store.fcLeadNotes);
out.no = [s.P.no, s.P.noAt, s.P.status, s.P.touches.length].join('|');

// 3. Live tab with the storage event: B sees A's call before it saves anything.
store.fcLeadNotes = '{}';
A = tab('A', false); B = tab('B', true);
A.run(dial('L1', '2026-10-16 19:00'));
out.live = B.run("(notes.L1 && notes.L1.dials.length) + '|' + rechecks");

// 4. Single tab: a second save does not re-merge its own write (no duplicate rows, no churn).
store.fcLeadNotes = '{}';
A = tab('A', false);
A.run(dial('S1', '2026-10-16 19:10')); A.run('save();');
out.single = JSON.parse(store.fcLeadNotes).S1.dials.length + '|' + A.run("typeof _LSLAST !== 'undefined' && _LSLAST === localStorage.getItem(LS)");

// 5. Storage full: the failure is remembered, not just toasted; a later good save clears it.
full = true; A.run(dial('Q1', '2026-10-16 19:20'));
out.full = A.run("(typeof _SAVEFAIL !== 'undefined' && _SAVEFAIL > 0) + '|' + toasts.length");
full = false; A.run('save();');
out.recovered = A.run("typeof _SAVEFAIL === 'undefined' ? 'none' : _SAVEFAIL") + '|' + ('Q1' in JSON.parse(store.fcLeadNotes));

// 6. Notes do not double between two live tabs, nor against the board's "— (merged)" write-back.
store.fcLeadNotes = JSON.stringify({N:{note:'Owner wants a cash offer, call after 5'}});
A = tab('A', true); B = tab('B', true);
for (let k = 0; k < 8; k++) {
  (k % 2 ? B : A).run("notes.N.note += '\\nline " + k + "'; save();");
}
const n1 = JSON.parse(store.fcLeadNotes).N.note;
out.note_len = n1.length + '|' + (n1.match(/Owner wants/g) || []).length + '|' + (n1.match(/line 7/g) || []).length;
const boardForm = 'Owner X' + '\n— (merged) ' + 'Owner X\ncallback Fri';
store.fcLeadNotes = JSON.stringify({N:{note:boardForm}});
A = tab('A', false); A.run("notes.N.note = 'Owner X\\ncallback Fri'; _LSLAST = null; save();");
out.board_form = JSON.parse(store.fcLeadNotes).N.note === 'Owner X\ncallback Fri';

// 6b. Call Mode + the board open together, each step one callback line on the same lead (was 3.5 MB).
store.fcLeadNotes = JSON.stringify({N:{note:'Owner wants a cash offer, call after 5'}});
A = tab('A', true); const Bd = tab('Board', true, true);
for (let k = 0; k < 12; k++) A.run("notes.N.note += '\\ncallback set " + k + "'; save();");
const nb = JSON.parse(store.fcLeadNotes).N.note;
out.board_tab = nb.length + '|' + (nb.match(/Owner wants/g) || []).length + '|' + (nb.match(/callback set 11/g) || []).length;
// 6c. Two frozen tabs append different lines to the same lead, alternating: grows by lines, not by doubling.
store.fcLeadNotes = JSON.stringify({N:{note:'base'}});
A = tab('A', false); B = tab('B', false);
for (let k = 0; k < 12; k++) (k % 2 ? B : A).run("notes.N.note += '\\nl" + k + "'; save();");
const nf = JSON.parse(store.fcLeadNotes).N.note;
out.frozen_notes = nf.length + '|' + [0,1,2,3,4,5,6,7,8,9,10,11].every(k => nf.indexOf('l' + k) >= 0);

// 7. Latest noAt wins and a larger local resurf is kept.
store.fcLeadNotes = JSON.stringify({P:{no:'soft', noAt:500, resurf:1}});
A = tab('A', false);
A.run("notes.P.noAt = 50; notes.P.resurf = 3; _LSLAST = null; save();");
s = JSON.parse(store.fcLeadNotes);
out.noat = s.P.noAt + '|' + s.P.resurf + '|' + s.P.no;

// 8. A pre-fix tab overwrites storage with an old snapshot; a later loadNotes (after a team pull
//    with nothing new) keeps what this tab had absorbed instead of replacing it.
store.fcLeadNotes = '{}';
A = tab('A', true);
A.run(dial('K1', '2026-10-16 20:00'));
ls.setItem('fcLeadNotes', JSON.stringify({OLDONLY:{touches:[]}}));   // old build writes its snapshot
A.run('loadNotes(); save();');
s = JSON.parse(store.fcLeadNotes);
out.reload = ('K1' in s) + '|' + ('OLDONLY' in s);

console.log(JSON.stringify(out));
"""

fd, path = tempfile.mkstemp(suffix='.js')
os.close(fd)
io.open(path, 'w', encoding='utf-8').write('const TABSRC = ' + json.dumps(TAB) + ';\n' + HARNESS)
try:
    p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
finally:
    os.remove(path)
rec('harness ran', p.returncode == 0, p.stderr[-600:])
try:
    out = json.loads(p.stdout.strip().splitlines()[-1])
except Exception:
    out = {}
rec('frozen stale tab no longer erases calls logged in the live tab',
    out.get('frozen') == 'OLD:1,C1:1,C2:1,C3:1', out.get('frozen'))
rec('a hard no and DO NOT CONTACT survive a stale tab save', out.get('no') == 'hard|1760000000000|DO NOT CONTACT|2', out.get('no'))
rec('an open tab picks up the other tab\'s call before it saves', out.get('live') == '1|1', out.get('live'))
rec('one tab saving twice adds nothing', out.get('single') == '1|true', out.get('single'))
rec('a refused save stays flagged until one lands', out.get('full') == 'true|1', out.get('full'))
rec('the flag clears and the held call is written on the next good save', out.get('recovered') == '0|true', out.get('recovered'))
rec('a note extended in one tab does not double across saves', out.get('note_len', '').endswith('|1|1'), out.get('note_len'))
rec('with the board tab open the note stays linear (about 12 short lines)', int(out.get('board_tab','99999|').split('|')[0]) < 2000 and out.get('board_tab','').endswith('|1|1'), out.get('board_tab'))
rec('two frozen tabs appending different lines lose no line and do not blow up', out.get('frozen_notes','').endswith('|true') and int(out.get('frozen_notes','99999|').split('|')[0]) < 600, out.get('frozen_notes'))
rec("the board's old+merged+new note collapses back to the new note", out.get('board_form') is True, out.get('board_form'))
rec('latest noAt and the larger resurf are kept; soft no stays', out.get('noat') == '500|3|soft', out.get('noat'))
rec('loadNotes after an old snapshot lands keeps the absorbed calls', out.get('reload') == 'true|true', out.get('reload'))
rec('session strip shows the NOT SAVED line', 'NOT SAVED since' in SRC and '(_SAVEFAIL ?' in SRC)

print('\n%d failed' % len(fails))
sys.exit(1 if fails else 0)
