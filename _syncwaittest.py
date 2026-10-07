"""_syncwaittest -- the Morning Worker waits out a late 07:15 opt-out sync instead of stopping.

Run:  python _syncwaittest.py   (exit 0 = safe; no network, no browser, no board, no real data)

WHAT BROKE (2026-10-07). send_server /send holds EVERY send until today's 07:15 opt-out sync has
finished (`blocked: optout_stale, hold: sync`). The laptop sat in standby, the sync ran at 08:25,
and the 8am auto-run had already stopped on its first lead with a banner telling the operator to
run ledger_sync.py. Nobody was at the laptop, so 0 of 52 eligible first touches went out.

WHAT THIS PINS. On a sync hold during an auto-run the worker schedules a retry of the SAME lead
(nothing posted, nothing charged), restores the lane/all-lanes mode when it resumes, gives up after
SYNC_MAX tries, never resumes outside sending hours or with the bridge not clear, and Stop, Start
and a lane switch all cancel the wait. Any other optout_stale (a stale or unreadable ledger) and a
hand-pressed Email still stop exactly as before. The bridge stays the only thing that decides a
send; this code only asks it again.

The worker JS is recovered from genMorningWorker the same way _autorunstoptest.py does it, so the
assertions run against the code that ships.
"""
import json
import os
import subprocess
import sys
import tempfile

import contextlib
import io

import types

# _autorunstoptest runs its checks at module level and ends in sys.exit, so exec it into a
# namespace we keep, silence its output, and reuse its extractor and recovered worker script.
A = types.SimpleNamespace()
_ns = {'__name__': '_autorunstoptest', '__file__': os.path.join(os.path.dirname(os.path.abspath(__file__)), '_autorunstoptest.py')}
with contextlib.redirect_stdout(io.StringIO()):
    try:
        exec(compile(open(_ns['__file__'], encoding='utf-8').read(), _ns['__file__'], 'exec'), _ns)
    except SystemExit as e:
        if e.code:
            raise SystemExit('_autorunstoptest fails on its own; fix that first.')
A.WORKER_JS, A.extract = _ns['WORKER_JS'], _ns['extract']

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:200]) if extra else ''))
    if not ok:
        fails.append(name)


JS = A.WORKER_JS
start = JS.index('if(x.j.blocked==="optout_stale" && x.j.hold==="sync"')
end = JS.index('} else if(x.status===200', start)
BRANCH = 'function onResp(x){ sending=false; ' + JS[start:end] + '} }'
DECL = JS[JS.index('var SYNCWAIT='):JS.index(';', JS.index('var SYNCWAIT=')) + 1]
FNS = '\n'.join(A.extract(JS, n) for n in ('cancelSyncWait', 'syncRetry', 'stopRun', 'pause', 'setLane'))

HARNESS = r"""
var timers=[], logs=[], runs=0, probes=0, sent=0, hourSafe=true;
function setTimeout(fn, ms){ timers.push({fn:fn, ms:ms, live:true}); return timers.length; }
function clearTimeout(h){ if(h && timers[h-1]) timers[h-1].live=false; }
function clearInterval(){}
function fire(){ var t=timers.filter(function(t){return t.live && t.fn.name==="syncRetry";}); if(!t.length) return false; t[0].live=false; t[0].fn(); return true; }
var document={ querySelectorAll:function(){ return []; }, querySelector:function(){ return null; } };
var auto=false, autoAll=false, awaitingReturn=false, tbOn=false, tick=null, healing=null, nextStep=null,
    ASTIMER=null, ASBOX=null, TBSENT=0, TBSKIP=0, TEXTQ=[], TBI=0, sending=false, sendingAt=0,
    BRIDGE_OK=true, BRIDGE_HOLD="", CAP={max:50}, i=0, lane="urgent",
    LANES={urgent:[{first:"A"},{first:"B"}], active:[{first:"C"}]}, Q=LANES.urgent, LMETA={urgent:{t:"Urgent"},active:{t:"Active"}};
function addLog(k,w,m){ logs.push(k+"|"+m); }
function render(){} function renderRunBar(){} function renderBridge(){} function bridgeUp(){}
function probeBridge(){ probes++; } function runOne(){ runs++; }
function sentToday(){ return sent; } function sendInFlight(){ return false; }
function _wftsa(){ return {safe:hourSafe, label:"x"}; }
function renderLaneTabs(){} function renderCallQ(){} function renderTextQ(){} function laneStats(){}
var HOLD={status:200, j:{ok:false, blocked:"optout_stale", hold:"sync", err:"HOLD - today's sync has not run"}};
var STALE={status:200, j:{ok:false, blocked:"optout_stale", err:"ledger stale"}};
function reset(){ timers=[]; logs=[]; runs=0; probes=0; auto=false; autoAll=false; BRIDGE_OK=true; BRIDGE_HOLD="";
  SYNCWAIT=null; SYNCTRY=0; SYNCALL=false; hourSafe=true; i=0; lane="urgent"; Q=LANES.urgent; sent=0; }
var out={};
"""

TESTS = r"""
// 1. auto-run hits a sync hold: waits, no BRIDGE_HOLD, nothing re-run yet
reset(); auto=true; autoAll=true; onResp(HOLD);
out.wait_scheduled = timers.filter(function(t){return t.live;}).length===1 && timers[0].ms===SYNC_EVERY;
out.wait_paused = auto===false && runs===0;
out.wait_no_ledger_banner = BRIDGE_HOLD==="";
out.wait_logged = logs.some(function(l){return /opt-out sync has not finished/.test(l);});
// 2. the retry resumes the same lead in the same mode
fire(); out.resume_runs = runs===1 && auto===true && autoAll===true && i===0;
// 3. still held on resume: schedules again, counts tries, stops at SYNC_MAX and falls back to the old stop
reset(); auto=true;
for(var k=0;k<SYNC_MAX;k++){ onResp(HOLD); fire(); }
out.tries_capped = SYNCTRY===SYNC_MAX;
onResp(HOLD); out.after_max_old_stop = BRIDGE_HOLD!=="" && auto===false && !fire();
// 4. Stop during the wait cancels it
reset(); auto=true; onResp(HOLD); stopRun("test");
out.stop_cancels = !fire() && runs===0 && logs.some(function(l){return /stopped waiting for the opt-out sync/.test(l);});
// 5. a lane switch cancels it
reset(); auto=true; onResp(HOLD); setLane("active"); out.lane_cancels = !fire() && runs===0;
// 6. outside sending hours the retry does not resume
reset(); auto=true; onResp(HOLD); hourSafe=false; fire(); out.hours_gate = runs===0 && auto===false;
// 7. bridge not clear at retry time: waits again, does not send
reset(); auto=true; onResp(HOLD); BRIDGE_OK=false; fire();
out.bridge_gate = runs===0 && auto===false && timers.filter(function(t){return t.live;}).length===1 && probes>=1;
// 8. cap reached while waiting: no resume
reset(); auto=true; onResp(HOLD); sent=50; fire(); out.cap_gate = runs===0 && auto===false;
// 9. a stale/unreadable ledger is NOT waited out
reset(); auto=true; onResp(STALE); out.stale_old_stop = BRIDGE_HOLD!=="" && auto===false && timers.length===0;
// 10. a hand-pressed Email (auto off) keeps the old stop
reset(); auto=false; onResp(HOLD); out.manual_old_stop = BRIDGE_HOLD!=="" && timers.length===0;
// 11. a run already going when the retry fires is left alone (no second run)
reset(); auto=true; onResp(HOLD); auto=true; fire(); out.no_double_run = runs===0;
console.log(JSON.stringify(out));
"""

js = HARNESS + DECL + '\n' + FNS + '\n' + BRANCH + '\n' + TESTS
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
    f.write(js)
    p = f.name
try:
    r = subprocess.run(['node', p], capture_output=True, text=True, timeout=60, encoding='utf-8', errors='replace')
finally:
    os.unlink(p)
if r.returncode != 0:
    print('node failed:\n' + r.stderr[:3000])
    sys.exit(1)
res = json.loads(r.stdout.strip().splitlines()[-1])
for k, v in res.items():
    rec(k, v is True)

print('\n%s -- %d check(s), %d failure(s)' % ('PASSED' if not fails else 'FAILED', len(res), len(fails)))
sys.exit(1 if fails else 0)
