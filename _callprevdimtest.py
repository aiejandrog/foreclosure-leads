"""Call Mode: Previous lead dims when it cannot move, and call log rows open the read-only record
(2026-11-13, ticket F-site-03).

Two narrow things, both view-only:

(a) "Previous lead" was drawn at full strength whenever this lane had ANY trail entry, even when every
    lead on the trail had since left pool() (an outcome was logged, so it is in its cooldown). Tapping
    it then only toasts "No earlier lead in this lane". The button now dims exactly when navBack()
    would find nothing: no trail entry of this lane, other than the lead on screen, is still in pool().
    Stepping over a dispositioned lead is deliberate (_callnavtest.py) and is unchanged.

(b) A call log row was plain text. It now opens the read-only PROSPECT RECORD for that case, and Back
    on the record returns to the log. The record carries no tel:, sms: or mailto: link and nothing is
    written to a note.

Not here on purpose: no undo of an outcome, no change to PENDING_HOLD_H, no change to any outcome
branch or suppression check.

Invented people only. The JS is extracted by name from call_mode.py and run under node with a small
fake DOM. No browser, no board, no network; DEALFLOW_DIR is a temp folder.
"""
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile

os.environ['DEALFLOW_DIR'] = tempfile.mkdtemp(prefix='dealflow-prevdim-')

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if (extra and not ok) else ''))
    if not ok:
        fails.append(name)


def grab_fn(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}', i)
    nl = SRC.find('\n', i)
    if i >= 0 and nl > 0 and SRC[i:nl].rstrip().endswith('}'):   # a one-line function
        rec('function present: ' + name, True)
        return SRC[i:nl + 1]
    k = SRC.find('\n', j + 2)
    rec('function present: ' + name, i >= 0 and j > 0)
    return SRC[i:k + 1] if i >= 0 and j > 0 else ''


def grab_var(name):
    i = SRC.find('var ' + name + ' ')
    j = SRC.find(';\n', i)
    rec('var present: ' + name, i >= 0 and j > 0)
    return SRC[i:j + 2] if i >= 0 else ''


def node(js):
    fd, path = tempfile.mkstemp(suffix='.js')
    os.close(fd)
    io.open(path, 'w', encoding='utf-8').write(js)
    try:
        p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.remove(path)


# ---------------------------------------------------------------- (a) Previous lead dims
NAV = ''.join(grab_fn(n) for n in ('_qvRestore', '_navPush', '_navSeek', '_navHasBack', 'navBack', '_navRow'))
NAVJS = 'var _NAVB=[], _NAVF=[], _NAVP = null, _QVBACK = null, LKFROM = null, _MOVE = 0, _lkReturn = function(){};\n' + NAV + r"""
var SCREEN = 'lead', lane = 'soon', QVIEW = 'untouched', i = 0, cur = null, toasts = [];
var ALL = [{c:'A'}, {c:'B'}, {c:'C'}], GONE = {};
function pool(){ return ALL.filter(function(r){ return !GONE[r.c]; }); }
function render(){ var P = pool(); cur = i < P.length ? P[i] : cur; }
function toast(t){ toasts.push(t); }
function dim(){ var h = _navRow(), m = /id="navback" style="([^"]*)"/.exec(h); return m ? (/opacity:\s*\.45/.test(m[1]) ? 'dim' : 'full') : 'missing'; }
var o = {};
render();
o.start = dim();                                   // nothing behind yet
/* WORK 2 LEADS: an outcome on A, then on B. Each leaves the pool (cooldown) and the trail keeps it. */
_navPush('A'); GONE.A = 1; i = 0; render();        // now on B
_navPush('B'); GONE.B = 1; i = 0; render();        // now on C
o.on = cur.c;
o.worked2 = dim();                                 // both trail leads are out of pool(): must be dim
toasts = []; navBack(); o.worked2_tap = cur.c + '|' + toasts.length;   // and the tap really goes nowhere
/* control: a lead only stepped past (Next, no outcome) is still in the pool -> full, and Back moves */
GONE = {}; _NAVB = []; _NAVF = []; i = 0; render(); _navPush('A'); i = 1; render();
o.stepped = dim();
toasts = []; navBack(); o.stepped_tap = cur.c + '|' + toasts.length;
/* one worked, one only stepped past: still somewhere to go */
GONE = {A:1}; _NAVB = [{c:'A', l:'soon'}, {c:'B', l:'soon'}]; _NAVF = []; i = 1; render();   // pool B,C -> on C
o.mixed = cur.c + ':' + dim();
/* another lane's trail does not light this lane's button */
GONE = {}; _NAVB = [{c:'A', l:'lp'}]; i = 1; render(); o.other_lane = dim();
/* the only trail entry is the lead already on screen (navBack passes it over) */
_NAVB = [{c:'B', l:'soon'}]; i = 1; render(); o.only_here = cur.c + ':' + dim();
/* the dim test wrote nothing to either trail */
_NAVB = [{c:'A', l:'soon'}, {c:'C', l:'lp'}]; _NAVF = [{c:'B', l:'soon'}]; i = 1; render(); dim(); dim();
o.trail_kept = _NAVB.length + '|' + _NAVF.length;
/* the Find-a-number return button is not this button and stays as it was */
LKFROM = 'B'; _NAVB = []; o.lk = dim();
console.log(JSON.stringify(o));
"""
rc, so, se = node(NAVJS)
rec('nav harness runs', rc == 0, se[-600:])
a = json.loads(so.strip().splitlines()[-1]) if rc == 0 and so.strip() else {}
rec('fresh lane: Previous lead is dim', a.get('start') == 'dim', a)
rec('work 2 leads: Previous lead is dim (no trail entry is in pool())', a.get('on') == 'C' and a.get('worked2') == 'dim', a)
rec('work 2 leads: the tap stays put and says so (unchanged)', a.get('worked2_tap') == 'C|1', a)
rec('a lead only stepped past keeps the button at full strength', a.get('stepped') == 'full', a)
rec('and Back still returns to it', a.get('stepped_tap') == 'A|0', a)
rec('one worked plus one stepped past: full strength', a.get('mixed') == 'C:full', a)
rec('another lane\'s trail does not light the button', a.get('other_lane') == 'dim', a)
rec('a trail holding only the lead on screen is dim', a.get('only_here') == 'B:dim', a)
rec('checking the button never edits the trail', a.get('trail_kept') == '2|1', a)
rec('Back to Find a number is untouched', a.get('lk') == 'full', a)
hb = SRC[SRC.find('function _navHasBack('):SRC.find('function navBack(')]
for bad in ('saveNotes', 'queueSync', 'logOutcome', '_WORKED', 'notes[', 'dials', 'cooldown', 'splice', 'push('):
    rec('_navHasBack never touches ' + bad, bad not in hb)
rec('the 6 hour pending hold is unchanged', SRC.count('var PENDING_HOLD_H = 6;') == 1)

# ---------------------------------------------------------------- (a2) one-off view, Queue clear, pool passes
# QA r1: with a lead opened on purpose (_openCase) the view on screen is not the view navBack() seeks in,
# because navBack() runs _qvRestore() first. This harness uses the REAL _qvRestore, _openCase and render,
# and a pool() that depends on lane and QVIEW.
OO = ''.join(grab_fn(n) for n in ('_qvRestore', '_navPush', '_navSeek', '_navHasBack', 'navBack', '_navRow',
                                  '_openCase', 'render'))
# 2026-11-19: render() and _openCase() read the open-call / one-card-per-person state
OOJS = ('var _NAVB=[], _NAVF=[], _NAVP = null, _QVBACK = null, LKFROM = null, BLANE = null;\n'
        'var _MOVE = 0, _PAINT = null, _PKPIN = null;\nfunction _callOpen(){ return false; }\nfunction _lkReturn(){}\n') + OO + r"""
var SCREEN = 'lead', lane = 'soon', QVIEW = 'untouched', i = 0, cur = null, phIdx = 0, toasts = [], _WORKED = [];
var ROWS = [{c:'A', v:'untouched', ln:'soon', p:['x']}, {c:'B', v:'untouched', ln:'soon', p:['x']},
            {c:'C', v:'untouched', ln:'soon', p:['x']}, {c:'R', v:'retries', ln:'soon', p:['x']},
            {c:'X', v:'retries', ln:'lp', p:['x']}];
var LANES = [{k:'soon', pred:function(r){ return r.ln === 'soon'; }}, {k:'lp', pred:function(r){ return r.ln === 'lp'; }}];
var GONE = {}, POOLN = 0, LASTPOOL = '';
function _viewOf(r){ return r.v; }
function pool(){ POOLN++; LASTPOOL = lane + '/' + QVIEW;
  return ROWS.filter(function(r){ return r.ln === lane && !GONE[r.c] && r.v === QVIEW; }); }
function _clmOwner(){ return null; }
function toast(t){ toasts.push(t); }
function promoteLookups(){} function applyPasted(){} function logErr(){}
function head(){ return '<div id="hd"></div>'; } function wire(){}
function viewEmptyHtml(){ return '<b>None.</b><div class="sub">x</div>'; }
function _posSave(){} var WFT = {pause:function(){}};
function nextLivePh(){ return 0; } function _clmTake(){} function wfResume(){}
var _els = {}, app = {_h:''};
Object.defineProperty(app, 'innerHTML', {get:function(){ return this._h; }, set:function(h){ this._h = String(h); _els = {}; }});
function $(id){ if(id === 'app') return app; if(app._h.indexOf('id="' + id + '"') < 0) return null; return _els[id] || (_els[id] = {}); }
/* the two lines of screenLead() this ticket touches, in the order the page evaluates them */
function screenLead(){ app.innerHTML = _navRow() + '<div class="sub">' + (i+1) + ' of ' + (_NAVP||pool()).length + '</div>'; }
function dim(){ var m = /id="navback" style="([^"]*)"/.exec(app._h); return m ? (/opacity:\s*\.45/.test(m[1]) ? 'dim' : 'full') : 'missing'; }
function reset(){ GONE = {}; _NAVB = []; _NAVF = []; _QVBACK = null; QVIEW = 'untouched'; lane = 'soon'; i = 0; SCREEN = 'lead'; toasts = []; render(); }
function stepPastA(){ _navPush('A'); i = 1; render(); }      // Next / Skip log nothing: A is on the trail and still in the pool
var o = {};
try{
  /* a plain lead paint builds the pool twice, as it did before this ticket */
  reset(); stepPastA(); o.plain = cur.c + ':' + dim();
  POOLN = 0; render(); o.plain_passes = POOLN;
  /* SAME-LANE ONE-OFF: a Reached-out lead of this lane opened on purpose */
  _openCase('R');
  o.oneoff = cur.c + ':' + dim() + ':' + QVIEW + ':' + lane + ':' + JSON.stringify(_QVBACK);
  var snap = function(){ return JSON.stringify([QVIEW, lane, i, _QVBACK, _NAVB, _NAVF, cur && cur.c]); };
  var s0 = snap(); _navRow(); _navRow(); o.oneoff_readonly = snap() === s0;
  o.oneoff_counters = LASTPOOL;                       // pool()'s counters are left for the view on screen
  POOLN = 0; render(); o.oneoff_passes = POOLN;
  toasts = []; navBack(); o.oneoff_tap = cur.c + '|' + QVIEW + '|' + lane + '|' + toasts.length + '|' + (_QVBACK === null);
  /* one-off, but the only trail lead has left the pool Back uses: dim, and the tap only toasts */
  reset(); _navPush('A'); GONE.A = 1; i = 0; render(); _openCase('R');
  o.oneoff_gone = cur.c + ':' + dim();
  toasts = []; navBack(); o.oneoff_gone_tap = cur.c + '|' + toasts.length;
  /* one-off that lands in ANOTHER lane: Back still seeks in the lane he came from */
  reset(); stepPastA(); _openCase('X');
  o.other = cur.c + ':' + lane + ':' + dim();
  toasts = []; navBack(); o.other_tap = cur.c + '|' + lane + '|' + QVIEW + '|' + toasts.length;
  /* PAST THE END (F-site-02): `i` beyond a pool that still holds leads goes back to the first lead with
     one toast. "Queue clear" is not painted, and the Back button follows the same rule as any lead paint. */
  reset(); _NAVB = [{c:'A', l:'soon'}]; GONE = {A:1}; i = 9; toasts = []; render();
  o.qc_gone = (app._h.indexOf('Queue clear') > -1) + '|' + cur.c + '|' + i + '|' + dim() + '|' + toasts.length;
  reset(); _NAVB = [{c:'B', l:'soon'}]; i = 9; POOLN = 0; toasts = []; render();
  o.qc_live = (app._h.indexOf('Queue clear') > -1) + '|' + cur.c + '|' + i + '|' + dim() + '|' + toasts.length;
  o.qc_passes = POOLN;
  toasts = []; navBack(); o.qc_tap = cur.c + '|' + toasts.length;
  /* past the end while a one-off is open: answered for the view Back restores */
  reset(); stepPastA(); _openCase('R'); i = 9; render();
  o.qc_oneoff = (app._h.indexOf('Queue clear') > -1) + '|' + cur.c + '|' + dim() + '|' + QVIEW;
  toasts = []; navBack(); o.qc_oneoff_tap = cur.c + '|' + QVIEW + '|' + toasts.length;
}catch(e){ o.err = String(e && e.stack || e).slice(0, 400); }
console.log(JSON.stringify(o));
"""
rc, so, se = node(OOJS)
rec('one-off harness runs', rc == 0, se[-600:])
c = json.loads(so.strip().splitlines()[-1]) if rc == 0 and so.strip() else {}
rec('one-off: no exception in the walk', not c.get('err'), c.get('err'))
rec('a stepped-past lead keeps the button at full strength (real render)', c.get('plain') == 'B:full', c)
rec('a plain lead paint builds the pool 2 times, not 3', c.get('plain_passes') == 2, c)
rec('same-lane one-off: Previous lead is at full strength (a trail lead is in the pool Back uses)',
    c.get('oneoff') == 'R:full:retries:soon:{"v":"untouched","l":"soon","c":"B"}', c)
rec('same-lane one-off: the tap moves to the stepped-past lead and puts the view back',
    c.get('oneoff_tap') == 'A|untouched|soon|0|true', c)
rec('one-off: drawing the button changes no view, lane, place, one-off or trail', c.get('oneoff_readonly') is True, c)
rec('one-off: pool() was last run for the view on screen', c.get('oneoff_counters') == 'soon/retries', c)
rec('one-off: one more pool pass, and only then (3)', c.get('oneoff_passes') == 3, c)
rec('one-off with no reachable trail lead: dim, and the tap only toasts',
    c.get('oneoff_gone') == 'R:dim' and c.get('oneoff_gone_tap') == 'R|1', c)
rec('one-off into another lane: full strength, and the tap returns to the lane he came from',
    c.get('other') == 'X:lp:full' and c.get('other_tap') == 'A|soon|untouched|0', c)
rec('past the end, trail lead gone: first lead of the list, no "Queue clear", Back dim, one toast',
    c.get('qc_gone') == 'false|B|0|dim|1', c)
rec('past the end, trail lead in the pool: first lead, no "Queue clear", Back at full strength, one toast',
    c.get('qc_live') == 'false|A|0|full|1', c)
rec('past the end: that Back tap opens the trail lead', c.get('qc_tap') == 'B|0', c)
rec('past the end: the paint builds the pool 2 times, as any lead paint', c.get('qc_passes') == 2, c)
rec('past the end in a one-off: the one-off lead stays, Back at full strength, and the tap moves',
    c.get('qc_oneoff') == 'false|R|full|retries' and c.get('qc_oneoff_tap') == 'A|untouched|0', c)
rec('the lead card counts from the pool the nav row built',
    "' of '+(_NAVP||pool()).length+'" in SRC and "' of '+pool().length+'" not in SRC)

# ---------------------------------------------------------------- (b) call log row -> record
TODAY = datetime.date.today()


def mdy(days):
    d = TODAY + datetime.timedelta(days=days)
    return '%d/%d/%d' % (d.month, d.day, d.year)


EQ = {'q': 1, 'f': 1, 'n': 40000, 'lb': 'Clear equity'}
ROWS = [
    {'c': 'W1', 'o': 'Ana Gomez Ruiz', 'on': 'Ana Gomez Ruiz', 'a': '100 NW 1st Ave, MIAMI, FL 33128', 'ct': 'MI', 'x': mdy(25),
     'eqc': EQ, 'p': ['3055550101'], 'e': ['ana@example.com']},
    {'c': 'W2', 'o': 'Ben Lee', 'on': 'Ben Lee', 'a': '200 SW 2nd St, MIAMI, FL 33130', 'ct': 'MI', 'x': mdy(30),
     'eqc': EQ, 'p': ['3055550102']},
    {'c': 'N3', 'o': 'Cal Diaz', 'on': 'Cal Diaz', 'a': '300 NE 3rd Ct, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(40),
     'eqc': EQ, 'p': ['3055550103']},
]
FNS = ('isBalloon', 'liveDays', 'isEquity', 'hardSuppressed', '_wasLp', 'noState', 'routeOf', 'routeAtty',
       'routeBlock', 'esc', '_lkDigits', 'wpmNums', 'wprList', 'relKindNum', 'lkAsDial', '_eqUsd', '_tele24n',
       '_ymd', '_ymdAdd', '_ymdDiff', '_md', '_hm', 'reachedAt', 'bookOpen', 'letterSent', '_rowsByC',
       'routeLeftWhy', 'mailTo', '_whyLine', '_firstLast', '_ocWord', '_countyWord', 'recTimeline', 'recHolds',
       'nextAct', 'prospectFacts', 'prospectPrompt', 'lastCall', 'agoTxt', 'screenRegistry', 'screenRecord')
VARS = ('ROUTE_KINDS', 'ROUTE_MAIL_MIN_DAYS', 'MAIL_AFTER_MISSES', 'MAIL_FOLLOWUP_DAYS', 'MAIL_KNOCK_AFTER_DAYS',
        'MAIL_LEFT_DAYS', 'SX_WORD', 'REACHED_OC')
REGJS = ('function _heldNow(){ return false; }\nfunction optPhones(){ return {}; }\n'
         'function today(){ return "' + TODAY.isoformat() + '"; }\nfunction nowTS(){ return "now"; }\n'
         'function caller(){ return "Carlos"; }\nvar SAVED = 0; function saveNotes(){ SAVED++; }\nfunction queueSync(){ SAVED++; }\nvar _FCGEN = 0;\n'
         'var OUTCOMES = [{k:"noanswer",t:"No answer"},{k:"voicemail",t:"Left voicemail"},{k:"talked",t:"Talked"}];\n'
         + ''.join(grab_var(v) for v in VARS)
         + ''.join(grab_fn(n) for n in FNS)
         + 'var _RECBACK = "lead", _RECC = null;\n'
         + 'var ROWS = ' + json.dumps(ROWS) + ';\nvar COV = [];\n'
         + r"""
/* a very small DOM: #app holds the html; an id exists only if the html carries it; [data-x] finds rows */
var _els = {}, _found = [];
var appEl = {_h:''};
Object.defineProperty(appEl, 'innerHTML', {get:function(){ return this._h; }, set:function(h){ this._h = String(h); _els = {}; _found = []; }});
var document = {
  getElementById: function(id){
    if(id === 'app') return appEl;
    if(appEl._h.indexOf('id="' + id + '"') < 0) return null;
    return _els[id] || (_els[id] = {id:id});
  },
  querySelectorAll: function(sel){
    var m = /^\[([\w-]+)\]$/.exec(sel), out = [];
    if(!m) return out;
    var re = new RegExp('<([a-z]+)[^>]*\\s' + m[1] + '="([^"]*)"[^>]*>', 'g'), x;
    while((x = re.exec(appEl._h))){ (function(tag, attr, v, open){
      var el = {tag:tag, open:open, getAttribute:function(k){ return k === attr ? v : null; }};
      out.push(el); _found.push(el);
    })(x[1], m[1], x[2], x[0]); }
    return out;
  }
};
function $(id){ return document.getElementById(id); }
function head(){ return '<div id="hd"></div>'; }
var SCREEN = 'lead', RENDERS = 0, COPIED = 0, ROUTES = 0;
function render(){ RENDERS++; }
function _sideEnter(){} function _sideBack(){ SCREEN = 'lead'; render(); }   /* 2026-11-19: the side screens' way in and out */
function screenRoutes(){ ROUTES++; SCREEN = 'routes'; }
function _copyOut(){ COPIED++; }
var NOW = Date.now(), DAYMS = 86400000;
/* WORK 2 LEADS (W1, W2). N3 was never called. T9 was logged by a teammate and is not on this seat's list. */
var notes = {
  W1: {status:'', note:'', dials:[{d:today(), tsu:NOW - 60000, oc:'noanswer', ph4:'0101', by:'Carlos'}]},
  W2: {status:'', note:'', dials:[{d:today(), tsu:NOW - 30000, oc:'voicemail', ph4:'0102', by:'Carlos'}]},
  N3: {status:'', note:''},
  T9: {status:'', note:'', owner:'Dee Fox', dials:[{d:today(), tsu:NOW - 90000, oc:'talked', ph4:'0104', by:'Alejandro'}]}
};
var before = JSON.stringify(notes), o = {};
function clickable(){ return _found.filter(function(e){ return typeof e.onclick === 'function'; }); }
function links(h){ return (h.match(/tel:|sms:|mailto:|href=|<a[\s>]/gi) || []).length; }
try{
  screenRegistry();
  o.reg_screen = SCREEN;
  o.rows = (appEl._h.match(/class="regrow"/g) || []).length;
  var C = clickable();
  o.clickable = C.length;
  o.clickable_are_rows = C.every(function(e){ return /class="regrow"/.test(e.open); });
  o.reg_links = links(appEl._h);
  o.regback = !!$('regback') && typeof $('regback').onclick === 'function';
  var pick = function(c){ return clickable().filter(function(e){ return e.open.indexOf('"' + c + '"') > -1; })[0]; };
  var w1 = pick('W1');
  if(w1){
    w1.onclick({preventDefault:function(){}});
    o.rec_screen = SCREEN; o.rec_case = _RECC;
    o.rec_title = appEl._h.indexOf('PROSPECT RECORD') > -1;
    o.rec_has_case = appEl._h.indexOf('Case W1') > -1;
    o.rec_timeline = appEl._h.indexOf('No answer, number ...0101') > -1;
    o.rec_links = links(appEl._h);
    o.rec_full_phone = /305555010\d|\(305\) 555-010\d/.test(appEl._h);
    o.rec_email = appEl._h.indexOf('ana@example.com') > -1;
    o.rec_buttons = (appEl._h.match(/<button[^>]*id="([^"]*)"/g) || []).map(function(b){ return /id="([^"]*)"/.exec(b)[1]; }).sort();
    /* Back on the record returns to the log, not to the dial card */
    var rb = $('recback'); rb.onclick();
    o.back_screen = SCREEN; o.back_is_log = appEl._h.indexOf('THE CALL LOG') > -1; o.back_renders = RENDERS;
    /* a repaint while the record is up (sync merge, language toggle) keeps that way back */
    pick('W2').onclick({preventDefault:function(){}});
    screenRecord(_RECC);
    $('recback').onclick(); o.repaint_back = SCREEN;
  }
  /* KEYBOARD: the row says role=button, so Enter and Space open it and other keys do not */
  screenRegistry();
  var kd = function(key){ var pd = 0, e = pick('W2'); if(!e || typeof e.onkeydown !== 'function') return 'nohandler';
    SCREEN = 'reg'; e.onkeydown({key:key, preventDefault:function(){ pd++; }}); return SCREEN + '|' + pd; };
  o.key_other = kd('a'); o.key_tab = kd('Tab'); o.key_enter = kd('Enter');
  screenRegistry(); o.key_space = kd(' ');
  screenRegistry(); o.key_handlers = clickable().filter(function(e){ return typeof e.onkeydown === 'function'; }).length;
  /* a teammate's row, not on this seat's list: the record still opens, and says it is off the list */
  screenRegistry();
  var t9 = pick('T9');
  if(t9){ t9.onclick({preventDefault:function(){}}); o.t9 = SCREEN + '|' + _RECC + '|' + (appEl._h.indexOf('Not on this build') > -1) + '|' + links(appEl._h); }
  /* the two older ways into the record still come back the way they did */
  screenRecord('W1', 'lead'); $('recback').onclick(); o.from_lead = SCREEN;
  screenRecord('W1', 'routes'); $('recback').onclick(); o.from_routes = SCREEN + '|' + ROUTES;
  /* the log's own Back button still goes to the queue */
  screenRegistry(); $('regback').onclick(); o.regback_to = SCREEN;
}catch(e){ o.err = String(e && e.stack || e).slice(0, 400); }
o.notes_same = JSON.stringify(notes) === before;
o.saved = SAVED; o.copied = COPIED;
console.log(JSON.stringify(o));
""")
rc, so, se = node(REGJS)
rec('log harness runs', rc == 0, se[-800:])
b = json.loads(so.strip().splitlines()[-1]) if rc == 0 and so.strip() else {}
rec('no exception in the walk', not b.get('err'), b.get('err'))
rec('the log lists the 3 called people, not the uncalled one', b.get('rows') == 3, b)
rec('every call log row is tappable', b.get('clickable') == 3 and b.get('clickable_are_rows') is True, b)
rec('the log itself adds no tel, sms or mailto link', b.get('reg_links') == 0, b)
rec('tapping a call log row opens SCREEN record for that case', b.get('rec_screen') == 'record' and b.get('rec_case') == 'W1', b)
rec('it is the prospect record, with that case and its timeline', b.get('rec_title') and b.get('rec_has_case') and b.get('rec_timeline'), b)
rec('the record adds no tel, sms or mailto link', b.get('rec_links') == 0, b)
rec('the record shows no full phone number and no email address', b.get('rec_full_phone') is False and b.get('rec_email') is False, b)
rec('the record has only Copy for AI and Back', b.get('rec_buttons') == ['recai', 'recback'], b)
rec('Back on the record returns to the call log', b.get('back_screen') == 'reg' and b.get('back_is_log') is True and b.get('back_renders') == 0, b)
rec('a repaint of the record keeps Back pointed at the log', b.get('repaint_back') == 'reg', b)
rec('Enter on a focused call log row opens the record', b.get('key_enter') == 'record|1', b)
rec('Space on a focused call log row opens the record', b.get('key_space') == 'record|1', b)
rec('any other key on a row does nothing', b.get('key_other') == 'reg|0' and b.get('key_tab') == 'reg|0', b)
rec('every row has the key handler', b.get('key_handlers') == 3, b)
rec('a teammate\'s row off this list opens a record with no link', b.get('t9') == 'record|T9|true|0', b)
rec('record opened from the dial card still goes back to it', b.get('from_lead') == 'lead', b)
rec('record opened from Mail and knock still goes back there', b.get('from_routes') == 'routes|1', b)
rec('the log\'s Back to the queue is unchanged', b.get('regback') is True and b.get('regback_to') == 'lead', b)
rec('nothing written: notes unchanged, no save, no sync', b.get('notes_same') is True and b.get('saved') == 0, b)

# the outcome branches this ticket must not touch are byte-for-byte where they were
rec('no undo was added', 'undoOutcome' not in SRC and 'Undo last outcome' not in SRC)

print('\n%s (%d failed)' % ('ALL PASS' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
