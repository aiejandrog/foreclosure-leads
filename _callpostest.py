"""Call Mode keeps his place across a reload (2026-09-30).

Alejandro, 09-30: "every time i go to call mode i go thru leads and lets say refresh it takes me to
the beginning of the leads I've called already". `i`, the lane and the Back/Next trail lived only in
memory, so a reload (pull-to-refresh, freshCheck() reloading for a new build, iOS evicting the tab
during a call) reopened slot 0 of the default lane, the first of the leads he had stepped past.
_posSave / _posRestore put him back, by identity, same page, same day. Order only: pool() still
decides who is shown.

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


def grab_var(prefix):
    i = SRC.find(prefix)
    j = SRC.find('\n', i)
    rec('declared: ' + prefix.strip(), i >= 0)
    return SRC[i:j + 1] if i >= 0 else ''


FNS = ''.join(grab_fn(n) for n in ('_navPush', '_navSeek', '_posSave', '_posRestore', 'navBack', 'navNext', 'advance'))
POSK = grab_var('var _POSK = ')
rec('start() restores before its first paint',
    "try{ _posRestore(); }catch(e){ i=0; }" in SRC[SRC.find('function start('):SRC.find('function paintSync(')])
lead_paint = SRC[SRC.find('function render(){'):SRC.find('function head(){')]
rec('every lead paint saves the place', "cur=P[i]; phIdx=(cur&&cur.c===pc&&pp<cur.p.length)?pp:0;\n  _posSave(P, i);" in lead_paint)
rec('queue-clear paint saves the place', "if(i>=P.length){ _posSave(P, i);" in lead_paint)
pos = SRC[SRC.find('function _posSave('):SRC.find('function _navHasBack(')]
for bad in ('saveNotes', 'queueSync', 'logOutcome', '_WORKED', 'notes[', 'dials', 'cooldown', 'supReason'):
    rec('position code never touches ' + bad, bad not in pos)

HARNESS = r"""
var STORE = {}, localStorage = {getItem:function(k){ return k in STORE ? STORE[k] : null; },
  setItem:function(k, v){ STORE[k] = String(v); }, removeItem:function(k){ delete STORE[k]; }};
var location = {pathname:'/dealflow-board/call/index.html'};
""" + POSK + r"""
var _NAVB = [], _NAVF = [], notes = {}, SCREEN = 'lead', lane = 'soon', i = 0, cur = null, QVIEW = 'untouched';
function _qvRestore(){}
var LANES = [{k:'soon'}, {k:'lp'}, {k:'worker'}];
var DATA = {soon:['A','B','C','D','E','F'], lp:['L1','L2','L3'], worker:['W1']}, GONE = {};
function pool(){ return (DATA[lane] || []).filter(function(c){ return !GONE[c]; }).map(function(c){ return {c:c}; }); }
function render(){ var P = pool(); if(i < P.length){ cur = P[i]; } _posSave(P, i); }
function toast(){}
""" + FNS + r"""
function reload(defLane){   // what a page reload does: memory gone, storage kept, start() runs
  _NAVB = []; _NAVF = []; cur = null; i = 0; lane = defLane || 'worker';
  _posRestore(); render(); return (i < pool().length ? cur.c : 'CLEAR') + '@' + lane;
}
var out = {};
render(); navNext(); navNext(); navNext();                  // A B C -> D, all stepped past with Next
out.basic = reload();                                        // back on D, in soon, not worker's W1
out.back_after = (function(){ navBack(); return cur.c; })(); // the trail survived the reload
// the lead on screen was dialled (hidden now): land on the one after it, not the top
i = pool().map(function(r){ return r.c; }).indexOf('D'); render();
GONE.D = 1; out.gone = reload();
// the whole saved window gone: first lead not already stepped past
GONE = {}; DATA.soon = ['A','B','C','D','E','F','G']; _NAVB = []; _NAVF = []; i = 0; render();
navNext(); navNext(); navNext();                            // stepped past A B C, on D
GONE = {D:1, E:1, F:1}; out.window_gone = reload();
// a re-sorted pool: found by identity, not slot
GONE = {}; DATA.soon = ['A','B','C','D','E','F','G']; i = 3; render();       // on D
DATA.soon = ['D','A','B','C','E','F','G']; out.resort = reload();
DATA.soon = ['A','B','C','D','E','F','G'];
// every lead left was stepped past earlier: the list, not a false queue-clear
GONE = {D:1, E:1, F:1, G:1}; out.all_past = reload();
GONE = {};
// another lane
lane = 'lp'; i = 0; render(); navNext(); out.lp = reload('soon');
// yesterday's place is not restored
var S = JSON.parse(STORE[_POSK]); S.d = 'Mon Jan 01 2001'; STORE[_POSK] = JSON.stringify(S);
out.stale = reload('soon');
// the saved lane has emptied: open the default lane as usual
lane = 'lp'; i = 1; render(); DATA.lp = []; out.empty = reload('soon');
// junk in storage never throws past start()'s guard, and a missing key is a clean start
STORE[_POSK] = '{not json'; out.junk = reload('soon');
delete STORE[_POSK]; out.none = reload('soon');
// per page: Carlos's seat URL has its own key
out.key = _POSK;
out.notes = JSON.stringify(notes);
console.log(JSON.stringify(out));
"""
rc, so, se = node(HARNESS)
rec('harness runs', rc == 0, se)
res = json.loads(so.strip().splitlines()[-1]) if rc == 0 else {}
rec('a reload returns to the lead he was on, in his lane', res.get('basic') == 'D@soon', res)
rec('Back still works after a reload', res.get('back_after') == 'C', res)
rec('lead on screen now hidden: the next one, not the top', res.get('gone') == 'E@soon', res)
rec('saved window all gone: first lead not stepped past', res.get('window_gone') == 'G@soon', res)
rec('all remaining leads stepped past: top of the list, not queue clear', res.get('all_past') == 'A@soon', res)
rec('found by identity after a re-sort', res.get('resort') == 'D@soon', res)
rec('another lane is restored too', res.get('lp') == 'L2@lp', res)
rec('a place from another day is ignored', res.get('stale') == 'A@soon', res)
rec('an emptied lane falls back to the default', res.get('empty') == 'A@soon', res)
rec('unreadable storage is a clean start', res.get('junk') == 'A@soon', res)
rec('no stored place is a clean start', res.get('none') == 'A@soon', res)
rec('key is per page, file and directory URL alike', res.get('key') == 'fcCallPos:/dealflow-board/call/', res)
rec('no notes written', res.get('notes') == '{}', res)

print('\n%s (%d failed)' % ('ALL PASS' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
