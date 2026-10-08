// Fake-data test: Morning Worker doSend() against a /send that NEVER answers.
// node test_worker_send_timeout.js <repo>/tracker_template.html
// Extracts the real doSend source the board bakes into the worker tab, runs it with a fetch that
// never settles and fake timers. No network, no real lead, nothing sent.
// On main: FAILS (run stays parked, no log line, no pause). With worker-send-timeout.patch: PASSES.
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8').split('\n');
let on = false, parts = [];
for (const ln of src) {
  if (ln.includes("'function doSend(r){'")) on = true;
  if (on && ln.includes('/* ---- confirmSend')) break;
  if (!on) continue;
  const m = ln.match(/^\s*\+\s*('.*')\s*$/);
  if (m) parts.push(eval(m[1]));
}
const code = parts.join('');
if (!code.startsWith('function doSend')) { console.error('doSend not found'); process.exit(2); }

// fake timers
let now = 0, timers = [];
const setTimeoutF = (fn, ms) => { const t = {at: now + ms, fn}; timers.push(t); return t; };
const clearTimeoutF = t => { timers = timers.filter(x => x !== t); };
const advanceClock = ms => { now += ms; let due; while ((due = timers.filter(t => t.at <= now)).length) {
  timers = timers.filter(t => t.at > now); due.forEach(t => t.fn()); } };

const log = []; let paused = 0, composer = 0, posted = [];
class FakeAbort { constructor(){ this.signal = {aborted:false, l:[]}; }
  abort(){ this.signal.aborted = true; this.signal.l.forEach(f => f()); } }
function fakeFetch(url, opts) {          // never answers; honours an abort signal like a browser
  return new Promise((res, rej) => { if (opts && opts.signal) opts.signal.l.push(() => {
    const e = new Error('aborted'); e.name = 'AbortError'; rej(e); }); });
}
// the worker's own var line (sending, and on the fixed version SENDSEQ / SEND_ABORT_MS)
const varLine = ((src.join('\n').match(/'(var sending=false[^;']*;)'/) || [])[1]) || 'var sending=false, sendingAt=0;';
const LEAD = {first: 'FAKE', owner: 'FAKE OWNER', c: 'FAKE-2099-000001', mailSubj: 's', mailBody: 'b',
        mailTo: 'homeowner@fake.invalid', mailBcc: '', portfolio: []};
const env = {
  BRIDGE_HOLD: '', auto: true, lane: 'urgent', Q: [LEAD], i: 0,
  addLog: (k, who, msg) => log.push(k + ': ' + msg), pause: () => { paused++; env.auto = false; },
  advance: () => log.push('advance'), post: (k) => posted.push(k), bridgeUp: () => {}, renderBridge: () => {},
  _cacheLive: () => {}, openHere: () => { composer++; return true; }, confirmSend: () => {}, render: () => {},
};
const fn = new Function(...Object.keys(env), 'fetch', 'AbortController', 'setTimeout', 'clearTimeout',
  varLine + '\nfunction sendInFlight(){ return sending; }\n' + code + '\nreturn doSend;');
const doSend = fn(...Object.values(env), fakeFetch, FakeAbort, setTimeoutF, clearTimeoutF);
doSend(LEAD);
(async () => {
  for (let k = 0; k < 20; k++) { advanceClock(30000); await new Promise(r => setImmediate(r)); }  // 10 minutes
  console.log('after 10 min of no answer:', {log, paused, composer, posted});
  const ok = paused === 1 && composer === 0 && posted.length === 0 && log.some(l => /has not answered/.test(l));
  console.log(ok ? 'PASS: run ends with a stated reason, no composer, nothing marked'
                 : 'FAIL (bug reproduced): run is parked with no terminal reason');
  process.exit(ok ? 0 : 1);
})();
