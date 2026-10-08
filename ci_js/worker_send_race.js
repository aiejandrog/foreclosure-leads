// Fake-data regression: Morning Worker Stop -> Start while a /send is still out.
//   node test_worker_stop_start_race.js <repo>/tracker_template.html
// Runs the REAL worker functions the board bakes (doSend, post, advance, stopRun, pause, startRun,
// runOne, sendInFlight and its var line), extracted from genMorningWorker. Only DOM/log/bridge-probe
// helpers are stubbed, and attempt() is stubbed to its email step (`if(step.k==="email") return
// doSend(r)`). Fake clock, fake fetch, fake leads (*.invalid). No network, nothing sent.
//
// Fake bridge: lead A's first /send answers late (or never); any /send to an address that already
// has one out gets 409 skip (send_server's in-flight claim); every other /send answers 200 in 10s.
// Operator: Start at 0s, Stop at 5s, Start again later. Pass = no second /send for A while its first
// is out (where a guard can see it), every "sent" is recorded on the case of the request whose
// message id it carries, every "skip" on a case the bridge actually refused, no Gmail composer.
const fs = require('fs');
const lines = fs.readFileSync(process.argv[2], 'utf8').split('\n');
const a = lines.findIndex(l => l.startsWith('function genMorningWorker('));
let b = a + 1; while (b < lines.length && !/^function /.test(lines[b])) b++;
const src = lines.slice(a, b).map(l => { const m = l.match(/^\s*\+\s*('.*')\s*$/); if (!m) return ''; try { return eval(m[1]); } catch (e) { return ''; } }).join('');
function fn(name) {
  const k = src.indexOf('function ' + name + '('); if (k < 0) throw new Error('missing ' + name);
  for (let j = src.indexOf('}', k); j > 0; j = src.indexOf('}', j + 1)) { const s = src.slice(k, j + 1); try { new Function(s); return s; } catch (e) {} }
  throw new Error('unbalanced ' + name);
}
const varLine = (src.match(/var sending=false[^;]*;/) || [])[0];
if (!varLine) throw new Error('sending var line missing');
const code = [varLine, fn('sendInFlight'), fn('doSend'), fn('post'), fn('advance'), fn('stopRun'),
  fn('pause'), fn('startRun'), fn('runOne')].join('\n');

function scenario(lateMs, startAgainMs, noAbort, manualAt) {
  let now = 0, timers = [];
  const setT = (f, ms) => { const t = { at: now + (ms || 0), f }; timers.push(t); return t; };
  const clrT = t => { timers = timers.filter(x => x !== t); };
  const fetches = [], marks = [], logs = [], replies = [], mids = {}, inflight = {};
  function fakeFetch(url, opts) {
    const body = JSON.parse(opts.body), to = body.to, n = fetches.length, mid = '<m' + n + '>';
    fetches.push({ t: now, c: body.meta.c });
    return new Promise((res, rej) => {
      const reply = (status, j) => { delete inflight[to]; res({ status, json: () => Promise.resolve(j) }); };
      if (opts.signal) opts.signal.l.push(() => { delete inflight[to]; const e = new Error('aborted'); e.name = 'AbortError'; rej(e); });
      if (inflight[to]) {
        replies.push({ t: now + 50, c: body.meta.c });
        setT(() => res({ status: 409, json: () => Promise.resolve({ ok: false, skip: true, err: 'already in flight' }) }), 50);
        return;
      }
      inflight[to] = 1; mids[mid] = body.meta.c;
      if (n === 0) { if (lateMs != null) setT(() => reply(200, { ok: true, message_id: mid }), lateMs); return; }
      setT(() => reply(200, { ok: true, message_id: mid }), 10000);
    });
  }
  class FakeAbort { constructor() { this.signal = { l: [] }; } abort() { this.signal.l.forEach(f => f()); } }
  const Q = ['A', 'B', 'C', 'D', 'E', 'F'].map(x => ({ c: 'FAKE-2099-00000' + x, first: 'FAKE ' + x, mailSubj: 's', mailBody: 'b', mailTo: x.toLowerCase() + '@fake.invalid', mailBcc: '', portfolio: [] }));
  const env = `
    var Q=__Q, i=0, auto=false, autoAll=false, lane='urgent', tbOn=false, ASTIMER=null, ASBOX=null, tick=null, healing=null,
        nextStep=null, awaitingReturn=false, MSEQ=0, BRIDGE_OK=true, BRIDGE_HOLD='', BOUNCE=null, TBSENT=0, TBSKIP=0, TEXTQ=[], TBI=0;
    var LMETA={urgent:{t:'Urgent'}}, LANE_ORDER=['urgent'], CAP={max:999};
    var window={opener:{closed:false, postMessage:function(m){ __marks.push({t:Date.now(), c:m.workerAct.c, k:m.workerAct.k, mid:(m.workerAct.extra||{}).mid}); }}, __cfWatch:null};
    function render(){} function renderRunBar(){} function bridgeUp(){} function renderBridge(){} function _cacheLive(){}
    var SYNCWAIT=null, SYNCTRY=0, SYNC_MAX=3, SYNCALL=false; function cancelSyncWait(){} function schedSync(){}
    function probeBridge(){} function hopNextLane(){ return false; } function sentToday(){ return 0; }
    function _pendWatch(){} function _markBuf(){} function openHere(){ __logs.push('COMPOSER'); return true; } function confirmSend(){}
    function addLog(k, who, msg){ __logs.push(Date.now()+' '+k+': '+who+': '+msg); }
    function attempt(r){ return doSend(r); }
  `;
  const run = new Function('__Q', '__marks', '__logs', 'fetch', 'AbortController', 'setTimeout', 'clearTimeout', 'clearInterval', 'Date',
    env + code + '\nreturn {startRun:startRun, stopRun:stopRun, doSend:doSend, cur:function(){ return Q[i]; }};');
  const api = run(Q, marks, logs, fakeFetch, noAbort ? undefined : FakeAbort, setT, clrT, clrT, { now: () => now });
  return (async () => {
    const step = async (to) => { while (now < to) { now += 50; let due; while ((due = timers.filter(t => t.at <= now)).length) { timers = timers.filter(t => t.at > now); due.forEach(t => t.f()); } for (let k = 0; k < 5; k++) await new Promise(r => setImmediate(r)); } };
    api.startRun(false, 'test');
    if (manualAt) { await step(manualAt); api.doSend(api.cur()); }   // the card's Email button, mid-send
    await step(5000);
    api.stopRun('stop'); await step(startAgainMs);
    api.startRun(false, 'test'); await step(400000);
    return { fetches, marks, logs, replies, mids };
  })();
}

(async () => {
  let ok = true;
  const cases = [
    ['Start at 40s, late success at 60s', 60000, 40000, false],
    ['Start at 40s, no answer at all', null, 40000, false],
    ['Start at 160s (past the wait deadline), late success at 200s', 200000, 160000, false],
    ['no AbortController, Start at 160s, late success at 200s', 200000, 160000, true],
    ['Email button pressed at 2s while A is out, Start at 160s, late success at 200s', 200000, 160000, false, 2000],
  ];
  for (const [name, late, again, noAbort, manualAt] of cases) {
    const r = await scenario(late, again, noAbort, manualAt);
    const firstOutUntil = late == null ? Infinity : late;
    const overlap = r.fetches.some((f, k) => k > 0 && f.c === r.fetches[0].c && f.t < firstOutUntil);
    const sentWrong = r.marks.filter(m => m.k === 'sent' && r.mids[m.mid] !== m.c);
    const skipWrong = r.marks.filter(m => m.k === 'skip' && !r.replies.some(x => x.c === m.c && Math.abs(x.t - m.t) <= 50));
    const aSentOnA = late == null || r.marks.some(m => m.k === 'sent' && m.mid === '<m0>' && m.c.endsWith('A'));
    const composer = r.logs.includes('COMPOSER');
    const aMarks = r.marks.filter(m => m.mid === '<m0>').length;   // A's answer reconciled exactly once
    const once = late == null ? aMarks === 0 : aMarks === 1;
    const pass = once && !overlap && !sentWrong.length && !skipWrong.length && aSentOnA && !composer;
    console.log('\n== ' + name + ': ' + (pass ? 'PASS' : 'FAIL'));
    console.log(' requests:', r.fetches.map(f => (f.t / 1000) + 's ' + f.c.slice(-1)).join(', '));
    console.log(' marks:   ', r.marks.filter(m => m.k === 'sent' || m.k === 'skip').map(m => (m.t / 1000) + 's ' + m.k + '->' + (m.c.slice(-1) || '(none)')).join(', ') || '(none)');
    console.log(' second /send for A while first out:', overlap, '| sent on wrong case:', sentWrong.length,
                '| skip on a case not refused:', skipWrong.length, '| A late answer on A:', aSentOnA, '| composer:', composer, '| A answer recorded', aMarks, 'time(s)');
    if (!pass) ok = false;
  }
  console.log(ok ? '\nPASS' : '\nFAIL (race reproduced)');
  process.exit(ok ? 0 : 1);
})();
