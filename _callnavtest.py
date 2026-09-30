"""Call Mode Back / Next buttons (2026-09-30).

Alejandro, 09-30: "have a Next and back button without breaking the coding of the site". Next is
the old Skip (advance(cur.c, null)) unless Back left a redo trail; Back returns to the lead he last
left, found BY IDENTITY in a fresh pool(). Neither writes a note, dial, outcome or cooldown, and a
lead suppressed since he left it is stepped over, never reopened.

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


NAMES = ('lastCall', '_navPush', '_navSeek', '_navHasBack', 'navBack', 'navNext', 'advance')
FNS = ''.join(grab_fn(n) for n in NAMES)
rec('state declared once', SRC.count('var _NAVB=[], _NAVF=[];') == 1)
rec('lead card carries Back and Next', "id=\"navback\"" in SRC and '_navRow()' in SRC)
rec('Next is wired to navNext', "$('skip').onclick=function(){ navNext(); };" in SRC)
rec('queue-clear screen offers Back', "if($('navback')) $('navback').onclick=navBack;" in SRC)
nav = SRC[SRC.find('function _navPush('):SRC.find('function _navRow(')]
for bad in ('saveNotes', 'queueSync', 'logOutcome', '_WORKED', 'notes[', 'dials', 'cooldown'):
    rec('nav code never touches ' + bad, bad not in nav)

HARNESS = 'var _NAVB=[], _NAVF=[];\n' + FNS + r"""
var notes = {}, SCREEN = 'lead', lane = 'soon', i = 0, cur = null, toasts = [], writes = 0;
var ALL = [{c:'A'}, {c:'B'}, {c:'C'}, {c:'D'}], GONE = {};
function pool(){ return ALL.filter(function(r){ return !GONE[r.c]; }); }
function render(){ var P = pool(); cur = i < P.length ? P[i] : cur; }
function toast(t){ toasts.push(t); }
var out = {};
render();
out.back_empty = (navBack(), cur.c + '|' + toasts.length);            // nothing behind: stay, say so
navNext(); navNext(); out.after2 = cur.c;                                // A -> B -> C
navBack(); out.back1 = cur.c;                                            // C -> B
navBack(); out.back2 = cur.c;                                            // B -> A
navNext(); out.redo1 = cur.c;                                            // A -> B (redo trail)
navNext(); out.redo2 = cur.c;                                            // B -> C
navNext(); out.fresh = cur.c;                                            // C -> D (plain skip)
// B was just suppressed (do-not-contact written elsewhere): Back steps over it
GONE.B = 1; navBack(); navBack(); out.skip_supp = cur.c;                 // D -> C -> A (B gone)
// re-sort: a dialled lead moved to the end; Back still finds it by code
GONE = {}; ALL = [{c:'A'}, {c:'B'}, {c:'C'}, {c:'D'}]; _NAVB = []; _NAVF = []; i = 0; render();
navNext(); ALL = [{c:'B'}, {c:'C'}, {c:'D'}, {c:'A'}]; render();
navBack(); out.resort = cur.c + '@' + i;                                 // A, now at slot 3
// another lane's trail is ignored
_NAVB = [{c:'C', l:'lp'}]; _NAVF = []; toasts = []; navBack(); out.lane = cur.c + '|' + toasts.length;
// an outcome-driven advance() clears the redo trail
_NAVF = [{c:'D', l:'soon'}]; advance(cur.c, 'B'); out.adv_clears = _NAVF.length + '|' + cur.c;
// queue clear: i past end, Back returns to the last lead left
ALL = [{c:'A'}, {c:'B'}]; _NAVB = []; _NAVF = []; i = 1; render(); navNext(); out.qclear_i = i;
navBack(); out.qclear_back = cur.c + '|' + _NAVF.length;
out.notes = JSON.stringify(notes);
console.log(JSON.stringify(out));
"""
rc, so, se = node(HARNESS)
rec('harness runs', rc == 0, se)
res = json.loads(so.strip().splitlines()[-1]) if rc == 0 else {}
rec('Back with nothing behind stays and says so', res.get('back_empty') == 'A|1', res)
rec('Next steps forward', res.get('after2') == 'C', res)
rec('Back returns to the previous lead', res.get('back1') == 'B' and res.get('back2') == 'A', res)
rec('Next after Back retraces', res.get('redo1') == 'B' and res.get('redo2') == 'C', res)
rec('Next past the redo trail is the old Skip', res.get('fresh') == 'D', res)
rec('Back steps over a lead suppressed since', res.get('skip_supp') == 'A', res)
rec('Back finds a re-sorted lead by identity', res.get('resort') == 'A@3', res)
rec('Back ignores another lane\'s trail', res.get('lane', '').endswith('|1'), res)
rec('an outcome advance clears the redo trail', res.get('adv_clears') == '0|B', res)
rec('Next off the last lead reaches queue clear', res.get('qclear_i') == 2, res)
rec('Back from queue clear returns to the last lead', res.get('qclear_back') == 'B|0', res)
rec('no notes written', res.get('notes') == '{}', res)

print('\n%s (%d failed)' % ('ALL PASS' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
