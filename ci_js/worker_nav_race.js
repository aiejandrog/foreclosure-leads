// Fake-data regression: what the Morning Worker does in the moment after a /send answers.
//   node ci_js/worker_nav_race.js <repo>/tracker_template.html
// Runs the REAL worker functions (doSend, post, advance, stopRun, pause, startRun, runOne, setLane,
// sendInFlight), extracted from genMorningWorker. Fake clock, fake fetch, fake leads (*.invalid).
//
// The answer used to move the queue on a bare 700-900ms timer nobody could cancel or check:
//   1. a lane switch inside that window let the timer advance the NEW lane, skipping its lead 1;
//   2. clearing that timer on Stop (the obvious fix) leaves `i` on the lead just emailed, so Start
//      re-sends it and the bridge's dedupe 409 logs a "skip" on a lead that was delivered.
// Pass = after the answer, the queue has already moved off the answered lead; a lane switch never
// skips a lead; Stop then Start resumes at the next lead with no second /send and no skip mark.
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
  fn('pause'), fn('startRun'), fn('runOne'), fn('setLane')].join('\n');

const ANSWERS = {
  '200 delivered': [200, { ok: true }],
  '409 deferred': [409, { ok: false, skip: true, err: 'deferred' }],
  '403 held': [403, { ok: false, blocked: 'evidence', skip: true }],
  '500 bridge error': [500, { ok: false, err: 'synthetic' }],
};
const mk = (p, x) => ({ c: 'FAKE-2099-' + p + x, first: 'FAKE ' + x, mailSubj: 's', mailBody: 'b', mailTo: p + x.toLowerCase() + '@fake.invalid', mailBcc: '', portfolio: [] });

function scenario(answer, action) {
  let now = 0, timers = [];
  const setT = (f, ms) => { const t = { at: now + (ms || 0), f }; timers.push(t); return t; };
  const clrT = t => { timers = timers.filter(x => x !== t); };
  const fetches = [], marks = [], logs = [], seen = {};
  function fakeFetch(url, opts) {
    const body = JSON.parse(opts.body), to = body.to, n = fetches.length;
    fetches.push({ t: now, c: body.meta.c });
    return new Promise(res => {
      // send_server's 24h dedupe: a second /send to an address it already took is a 409 skip
      const [st, j] = seen[to] ? [409, { ok: false, skip: true, err: 'already emailed inside 24h' }]
                                : (n === 0 ? ANSWERS[answer] : [200, { ok: true }]);
      if (st === 200) seen[to] = 1;
      setT(() => res({ status: st, json: () => Promise.resolve(Object.assign({ message_id: '<m' + n + '>' }, j)) }), 2000);
    });
  }
  const urgent = ['A', 'B', 'C'].map(x => mk('U', x)), active = ['X', 'Y'].map(x => mk('V', x));
  const env = `
    var LANES={urgent:__U, active:__V}, Q=LANES.urgent, i=0, auto=false, autoAll=false, lane='urgent', tbOn=false,
        ASTIMER=null, ASBOX=null, tick=null, healing=null, nextStep=null, awaitingReturn=false, MSEQ=0,
        BRIDGE_OK=true, BRIDGE_HOLD='', BOUNCE=null, TBSENT=0, TBSKIP=0, TEXTQ=[], TBI=0;
    var LMETA={urgent:{t:'Urgent',ic:'',d:''}, active:{t:'Active',ic:'',d:''}}, LANE_ORDER=['urgent','active'], CAP={max:999};
    var window={opener:{closed:false, postMessage:function(m){ __marks.push({t:Date.now(), c:m.workerAct.c, k:m.workerAct.k}); }}, __cfWatch:null};
    var document={querySelectorAll:function(){ return []; }, querySelector:function(){ return null; }};
    function render(){} function renderRunBar(){} function bridgeUp(){} function renderBridge(){} function _cacheLive(){}
    var SYNCWAIT=null, SYNCTRY=0, SYNC_MAX=3, SYNCALL=false; function cancelSyncWait(){} function schedSync(){}
    function probeBridge(){} function hopNextLane(){ return false; } function sentToday(){ return 0; }
    function _pendWatch(){} function _markBuf(){} function openHere(){ __logs.push('COMPOSER'); return true; } function confirmSend(){}
    function addLog(k, who, msg){ __logs.push(k+': '+who+': '+msg); }
    function runlog(){} var STATS={};
    function attempt(r){ return doSend(r); }
  `;
  const run = new Function('__U', '__V', '__marks', '__logs', 'fetch', 'AbortController', 'setTimeout', 'clearTimeout', 'clearInterval', 'Date',
    env + code + '\nreturn {startRun:startRun, stopRun:stopRun, setLane:setLane, st:function(){ return {lane:lane, i:i, c:(Q[i]||{}).c, auto:auto}; }};');
  const api = run(urgent, active, marks, logs, fakeFetch, undefined, setT, clrT, clrT, { now: () => now });
  return (async () => {
    const flush = async () => { for (let k = 0; k < 6; k++) await new Promise(r => setImmediate(r)); };
    const step = async (to) => { while (now < to) { now += 50; let due; while ((due = timers.filter(t => t.at <= now)).length) { timers = timers.filter(t => t.at > now); due.forEach(t => t.f()); } await flush(); } };
    api.startRun(false, 'test');
    await step(2050);                       // A's answer has landed; the next lead is not started yet
    const after = api.st();
    if (action === 'lane' || action === 'laneStart') api.setLane('active');
    if (action === 'laneStart') api.startRun(false, 'test');   // switch and Start inside the wait
    if (action === 'stop') api.stopRun('stop');
    await step(4000);
    const settled = api.st();
    if (action === 'laneStart') await step(30000);
    else if (action !== 'stay') { api.startRun(false, 'test'); await step(30000); }
    else await step(30000);
    return { after, settled, fetches, marks, logs };
  })();
}

(async () => {
  let ok = true;
  const check = (name, pass, detail) => { console.log((pass ? 'PASS ' : 'FAIL ') + name + (pass ? '' : '  :: ' + JSON.stringify(detail))); if (!pass) ok = false; };
  for (const answer of Object.keys(ANSWERS)) {
    const s = await scenario(answer, 'stay');
    check(answer + ' / no interruption: the queue moves off A as soon as A answers', s.after.i === 1, s.after);
    check(answer + ' / no interruption: A, B, C each get one /send, in order',
      s.fetches.map(f => f.c.slice(-1)).join('') === 'ABC', s.fetches.map(f => f.c));

    const l = await scenario(answer, 'lane');
    check(answer + ' / lane switch right after the answer: the new lane still starts at its lead 1',
      l.settled.lane === 'active' && l.settled.i === 0, l.settled);
    const lf = l.fetches.slice(1).map(f => f.c.slice(-1)).join('');
    check(answer + ' / lane switch: Start then sends X before Y, and X is not skipped', lf === 'XY', l.fetches.map(f => f.c));

    const q = await scenario(answer, 'laneStart');
    check(answer + ' / lane switch + Start inside the wait: X and Y each get one /send, in order',
      q.fetches.slice(1).map(f => f.c.slice(-1)).join('') === 'XY', q.fetches.map(f => f.c));
    check(answer + ' / lane switch + Start inside the wait: no "still waiting" refusal from a leftover timer',
      !q.logs.some(x => x.indexOf('still waiting for the bridge') >= 0), q.logs.filter(x => x.indexOf('still waiting') >= 0));

    const p = await scenario(answer, 'stop');
    check(answer + ' / Stop right after the answer: the run rests on B, not on A', p.settled.i === 1 && !p.settled.auto, p.settled);
    check(answer + ' / Stop then Start: A gets no second /send', p.fetches.filter(f => f.c.endsWith('UA')).length === 1, p.fetches.map(f => f.c));
    const aMarks = p.marks.filter(m => m.c.endsWith('UA') && (m.k === 'sent' || m.k === 'skip'));
    check(answer + ' / Stop then Start: A carries exactly the one mark its answer earned', aMarks.length === 1, aMarks);
    check(answer + ' / no Gmail composer anywhere', !s.logs.concat(l.logs, p.logs).includes('COMPOSER'));
  }
  console.log(ok ? '\nPASS' : '\nFAIL');
  process.exit(ok ? 0 : 1);
})();
