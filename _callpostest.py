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
POSK = grab_var('var _POSK = ') + grab_var('var _QV = ')
rec('restore accepts only a known view, never a view-less older save',
    "if(typeof s.v !== 'string' || _QV.indexOf(s.v) < 0) return false;" in SRC)
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
var _QVBACK = null;
function _qvRestore(){}
var LANES = [{k:'soon'}, {k:'lp'}, {k:'worker'}];
var DATA = {soon:['A','B','C','D','E','F'], lp:['L1','L2','L3'], worker:['W1']}, GONE = {};
function pool(){ return (DATA[lane] || []).filter(function(c){ return !GONE[c]; }).map(function(c){ return {c:c}; }); }
function render(){ var P = pool(); if(i < P.length){ cur = P[i]; } _posSave(P, i); }
function toast(){}
""" + FNS + r"""
function reload(defLane){   // what a page reload does: memory gone, storage kept, start() runs
  _NAVB = []; _NAVF = []; cur = null; i = 0; lane = defLane || 'worker'; QVIEW = 'untouched'; _QVBACK = null;
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
// HISTORY UNKNOWN (2026-10-19): a reload reopens the view he was working, at his place. start()
// sets Untouched before restoring, and a saved place in any other view used to be refused.
DATA.soon = ['A','B','C','D','E','F','G']; GONE = {}; _NAVB = []; _NAVF = [];
lane = 'soon'; QVIEW = 'history_unknown'; i = 0; render(); navNext(); navNext(); navNext();   // on D
out.hu = reload('soon'); out.hu_view = QVIEW;
out.hu_back = (function(){ navBack(); return cur.c; })();
// the lead on screen was dialled and left the view: the next one, still in that view
i = pool().map(function(r){ return r.c; }).indexOf('D'); render(); GONE.D = 1;
out.hu_gone = reload('soon'); out.hu_gone_view = QVIEW; GONE = {};
// Reached out and Callbacks are sessions too
QVIEW = 'retries'; i = 2; render(); out.retries = reload('soon'); out.retries_view = QVIEW;
QVIEW = 'callbacks'; i = 1; render(); out.cb = reload('soon'); out.cb_view = QVIEW;
// a one-off opened from the lookup is saved as the session it interrupted, not as a retry session
QVIEW = 'history_unknown'; i = 3; render();                                                   // on D
_QVBACK = {v:'history_unknown', l:'soon', c:'D'}; QVIEW = 'retries'; i = 5; render();          // one-off on F
out.oneoff = reload('soon'); out.oneoff_view = QVIEW;
// a save from an older build (no view) and an unknown view are never restored
QVIEW = 'history_unknown'; i = 3; render();
var S2 = JSON.parse(STORE[_POSK]); delete S2.v; STORE[_POSK] = JSON.stringify(S2);
out.noview = reload('soon'); out.noview_view = QVIEW;
S2 = JSON.parse(STORE[_POSK]); S2.v = 'mixed'; STORE[_POSK] = JSON.stringify(S2);
out.badview = reload('soon'); out.badview_view = QVIEW;
// that view emptied since: the default opens, in the default view
DATA.lp = ['L1','L2','L3']; QVIEW = 'history_unknown'; i = 3; render(); DATA.soon = []; out.hu_empty = reload('lp'); out.hu_empty_view = QVIEW;
DATA.soon = ['A','B','C','D','E','F','G'];
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
rec('History unknown: a reload returns to his place in that view', res.get('hu') == 'D@soon' and res.get('hu_view') == 'history_unknown', res)
rec('History unknown: Back still works after the reload', res.get('hu_back') == 'C', res)
rec('History unknown: a dialled lead that left the view gives way to the next', res.get('hu_gone') == 'E@soon' and res.get('hu_gone_view') == 'history_unknown', res)
rec('Reached out is restored too', res.get('retries') == 'C@soon' and res.get('retries_view') == 'retries', res)
rec('Callbacks is restored too', res.get('cb') == 'B@soon' and res.get('cb_view') == 'callbacks', res)
rec('a one-off retry is saved as the session it interrupted', res.get('oneoff') == 'D@soon' and res.get('oneoff_view') == 'history_unknown', res)
rec('an older save with no view is not restored', res.get('noview') == 'A@soon' and res.get('noview_view') == 'untouched', res)
rec('an unknown view is not restored', res.get('badview') == 'A@soon' and res.get('badview_view') == 'untouched', res)
rec('that view emptied since: default lane and view', res.get('hu_empty') == 'L1@lp' and res.get('hu_empty_view') == 'untouched', res)
rec('key is per page, file and directory URL alike', res.get('key') == 'fcCallPos:/dealflow-board/call/', res)
rec('no notes written', res.get('notes') == '{}', res)

print('\n%s (%d failed)' % ('ALL PASS' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
