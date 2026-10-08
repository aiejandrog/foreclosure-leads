"""Whole-flow tests for the PR 182 review corrections (2026-10-08).

Runs the REAL code under node: syncPull/mergeNotes/_mergeLead as extracted onto the phone page,
_histWhy / _qvRestore / advance extracted by name from call_mode.py, and ledger_audit /
stamp_unknown / history_coverage from call_mode.py. Nothing here touches live notes or ledgers.
"""
import io, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_mode  # noqa: E402

SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
TPL = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:240]) if extra else ''))
    if not ok:
        fails.append(name)


def grab(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}\n', i)
    assert i >= 0 and j > 0, name
    return SRC[i:j + 2]


def node(js):
    fd, path = tempfile.mkstemp(suffix='.js')
    os.close(fd)
    io.open(path, 'w', encoding='utf-8').write(js)
    try:
        p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=90)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.remove(path)


def run(name, js):
    rc, out, err = node(js)
    ok = rc == 0 and 'ASSERT_FAIL' not in out
    rec(name, ok, (out + err).strip().replace('\n', ' / ')[-240:] if not ok else out.strip().splitlines()[-1:] and out.strip().splitlines()[-1])


SYNC = call_mode.extract_sync_js(TPL)
STUBS = r"""
var store = {}, saves = 0, renders = 0, fails = [];
var localStorage = {getItem:function(k){return k in store ? store[k] : null;}, setItem:function(k,v){store[k]=String(v);}, removeItem:function(k){delete store[k];}};
var notes = {};
function save(){ saves++; } function recompute(){} function render(){ renders++; } function syncFreshness(){}
function _today(){ return '2026-10-08'; }
function check(c, m){ if(!c){ console.log('ASSERT_FAIL ' + m); fails.push(m); } }
var document = {getElementById:function(){return null;}};
var navigator = {userAgent:'t'};
"""

# ---- finding 4: dial-only merge persists ------------------------------------------------------
run('dials-only union marks a change and saves', STUBS + SYNC + r"""
notes.C1 = {note:'x'};
var s = mergeNotes({C1:{note:'x', dials:[{ts:'2026-10-08 09:00:00', ph4:'1234'}]}});
check(s.dials === 1, 'sum.dials counts the union: ' + JSON.stringify(s));
check(saves === 1, 'dials-only merge saved once: ' + saves);
check((notes.C1.dials||[]).length === 1, 'dial present in notes');
var s2 = mergeNotes({C1:{note:'x', dials:[{ts:'2026-10-08 09:00:00', ph4:'1234'}]}});
check(s2.dials === 0 && saves === 1, 'identical dials do not save again: ' + saves);
console.log('done');
""")

# ---- finding 1: pull coverage -----------------------------------------------------------------
PULL = STUBS + SYNC + r"""
var webcrypto = require('crypto').webcrypto; var crypto = webcrypto;
var TextEncoder = require('util').TextEncoder, TextDecoder = require('util').TextDecoder;
function _nowTS(){ return '2026-10-08 10:00:00'; }
function toast(){}
store.fcTeamKey = 'k-one'; store.fcDevice = 'dev-me';
(async function(){
  var good = await _encBlob({C9:{note:'hi'}}, 'k-one');
  var rows = [{device_id:'dev-me', payload:good}, {device_id:'dev-b', payload:good}];
  var mode = 'ok';
  fetch = async function(){ if(mode==='throw') throw new Error('net');
    return {ok: mode!=='500', status: mode==='500'?500:200, json: async function(){ return rows; }}; };
  var r = await syncPull();
  var ps = JSON.parse(store.fcPullStat);
  check(ps.ok === true && ps.devices === 1 && ps.failed === 0, 'clean pull records coverage: ' + store.fcPullStat);
  check(!!store.fcLastPull, 'clean pull stamps fcLastPull');
  check(ps.kf === _keyFp('k-one'), 'key fingerprint recorded');
  delete store.fcLastPull;
  rows.push({device_id:'dev-c', payload:'not-json'});
  await syncPull(); ps = JSON.parse(store.fcPullStat);
  check(ps.ok === false && ps.failed === 1, 'a blob that will not read is a failed device: ' + store.fcPullStat);
  check(!store.fcLastPull, 'a partial pull does NOT stamp fcLastPull');
  rows.pop(); mode = 'throw'; await syncPull(); ps = JSON.parse(store.fcPullStat);
  check(ps.ok === false, 'network failure records ok:false');
  mode = '500'; await syncPull(); ps = JSON.parse(store.fcPullStat);
  check(ps.ok === false, 'http 500 records ok:false');
  console.log('done');
})().catch(function(e){ console.log('ASSERT_FAIL threw ' + e); });
"""
run('syncPull records coverage; partial pulls are not fresh', PULL)

# ---- _histWhy on top of the recorded coverage -----------------------------------------------
HW = STUBS + SYNC + r"""
function _nowTS(){ return '2026-10-08 10:00:00'; }
var HISTCOV = {ledgers_ok:true, why:''}, _NOTESBAD = false, SEAT = {n:2, i:0};
""" + grab('_seat') + grab('_histWhy') + r"""
var realNow = Date.now; var stamp = new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,19).replace('T',' ');
function stat(o){ var b = {ok:true, kf:_keyFp('k-one'), devices:1, failed:0, ts:stamp}; for(var k in o) b[k]=o[k]; store.fcPullStat = JSON.stringify(b); }
check(_histWhy() === '', 'no team key: ok');
store.fcTeamKey = 'k-one';
check(/not completed/.test(_histWhy()), 'key, no stat: unknown');
store.fcLastPull = stamp; stat();
check(_histWhy() === '', 'clean fresh covering pull: ok -> ' + _histWhy());
stat({ok:false}); check(/did not complete/.test(_histWhy()), 'ok:false');
stat({failed:1}); check(/could not be read/.test(_histWhy()), 'failed>0');
stat({kf:'zzz'}); check(/different team key/.test(_histWhy()), 'other key');
stat({ts:'2020-01-01 00:00:00'}); check(/6 hours/.test(_histWhy()), 'stale');
stat({devices:0}); check(/only 0 of 1/.test(_histWhy()), 'saw too few devices');
SEAT = null; store.fcSeat = JSON.stringify({n:3, i:0}); stat({devices:1});
check(/only 1 of 2/.test(_histWhy()), 'three seats need two others');
console.log('done');
"""
run('_histWhy requires key, freshness, no failures and enough devices', HW)

# ---- finding 2: one-off open then Next, whole flow ---------------------------------------------
FLOW = r"""
var QVIEW = 'untouched', lane = 'all', i = 0, cur = null, SCREEN = 'lead', _QVBACK = null, _NAVB = [], _NAVF = [];
var fails = [], renders = 0, shown = [];
function check(c, m){ if(!c){ console.log('ASSERT_FAIL ' + m); fails.push(m); } }
var ALL = [{c:'U1', v:'untouched'}, {c:'U2', v:'untouched'}, {c:'R1', v:'retries'}, {c:'R2', v:'retries'}, {c:'R3', v:'retries'}];
function pool(){ return ALL.filter(function(r){ return r.v === QVIEW; }); }
function render(){ var P = pool(); renders++; cur = P[i] || null; shown.push(cur ? cur.c : 'QUEUE_CLEAR'); }
function _navPush(c){ if(c) _NAVB.push({c:c, l:lane}); }
function _contactTier(r){ return r.v === 'retries' ? 1 : 0; }
""" + grab('_qvRestore') + grab('advance') + r"""
// Alex is on U2 (second untouched), taps the THIRD retry from a board list: the open handler's effect.
i = 1; render();
check(cur.c === 'U2', 'start on U2');
_QVBACK = {v:QVIEW, l:lane, c:cur.c}; QVIEW = 'retries'; var P = pool();
for(var j = 0; j < P.length; j++) if(P[j].c === 'R3') i = j; render();
check(cur.c === 'R3', 'opened R3');
advance('R3', null);   // Next / worked: first move off the one-off
check(QVIEW === 'untouched', 'view restored: ' + QVIEW);
check(shown[shown.length-1] === 'U2', 'lands back on the card he was on, not QUEUE_CLEAR: ' + shown.join(','));
check(shown.indexOf('QUEUE_CLEAR') < 0, 'never a false Queue clear: ' + shown.join(','));
// same-view one-off: opened from another lane, same view; first Next must not skip U2
ALL.push({c:'X', v:'untouched'}); shown.length = 0; QVIEW = 'untouched'; i = 1; render();
check(cur.c === 'U2', 'again on U2');
_QVBACK = {v:'untouched', l:'all', c:'U2'}; lane = 'other'; var Q = pool();
for(var j2 = 0; j2 < Q.length; j2++) if(Q[j2].c === 'X') i = j2; render();
check(cur.c === 'X', 'opened X');
advance('X', null);
check(shown[shown.length-1] === 'U2' && lane === 'all', 'lands back on U2, not past X: ' + shown.join(',') + ' lane=' + lane);
// explicit view choice cancels the one-off restore
_QVBACK = {v:'untouched', l:'all', c:'U1'}; QVIEW = 'retries';
_QVBACK = null;  // what the data-v click handler does first
var before = QVIEW; _qvRestore(); check(QVIEW === before, 'explicit choice is not undone by a later move');
console.log('done');
"""
run('one-off open then Next restores view, lane and card (no false Queue clear)', FLOW)

# the click handlers must cancel the restore
rec('view and lane buttons cancel _QVBACK',
    SRC.count("b.onclick=function(){ _QVBACK=null;") == 2)

# ---- finding 3: ledger audit ------------------------------------------------------------------
td = tempfile.mkdtemp()
mp, tp = os.path.join(td, 'mail.json'), os.path.join(td, 'text.json')
json.dump([
    {'ch': 'email', 'message_id': 'm1', 'case': 'GOOD', 'ts_utc': '2026-10-01T10:00:00'},
    {'ch': 'email', 'message_id': 'm2', 'case': 'BADTS', 'ts_utc': 'garbage'},
    {'ch': 'email', 'message_id': 'm3', 'ts_utc': '2026-10-01T10:00:00'},          # no case: unattributed
    {'ch': 'email', 'error': 'smtp'},                                                # failure: ignored
], io.open(mp, 'w'))
json.dump([
    {'ch': 'text', 'confirmed': True, 'case': 'TBAD', 'ts_utc': '', 'pkey': 'PK1'},
    {'ch': 'text', 'confirmed': True, 'pkey': 'PK2', 'ts_utc': 'x'},
    {'ch': 'text', 'confirmed': False, 'case': 'OPENED', 'ts_utc': 'x'},            # composer open: ignored
], io.open(tp, 'w'))
a = call_mode.ledger_audit(mp, tp)
rec('audit finds undated confirmed sends', a['cases'] == {'BADTS', 'TBAD'} and a['pkeys'] == {'PK1', 'PK2'}, a)
rec('audit counts case-less email as unattributed', a['unattributed'] == 1, a)
rows = [{'c': 'BADTS'}, {'c': 'X', 'pcs': ['TBAD']}, {'c': 'Y', 'pk': 'PK2'}, {'c': 'GOOD'}, {'c': 'OPENED'}]
n = call_mode.stamp_unknown(rows, a)
rec('stamp_unknown marks by case, sibling case and person key', n == 3 and [r.get('lu') for r in rows] == [1, 1, 1, None, None], rows)
import shutil
td2 = tempfile.mkdtemp()
shutil.copy(mp, os.path.join(td2, 'mail_sent.json')); shutil.copy(tp, os.path.join(td2, 'text_sent.json'))
_here = call_mode.HERE; call_mode.HERE = td2
hc = json.loads(call_mode.history_coverage(10, 10, a))
rec('unattributed confirmed send turns ledgers_ok off', hc['ledgers_ok'] is False and 'name no case' in hc['why'], hc)
hc2 = json.loads(call_mode.history_coverage(10, 10, {'cases': {'BADTS'}, 'pkeys': set(), 'unattributed': 0}))
call_mode.HERE = _here
rec('a send that is only undated does not turn ledgers_ok off by itself (it is stamped lu)',
    hc2['ledgers_ok'] is True, hc2)
rec('_contactTier counts lu as contacted', 'if(r.le || r.lt || r.lu) return 1;' in SRC)

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
