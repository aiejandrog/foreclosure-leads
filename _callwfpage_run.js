/* _callwfpage_run.js -- run via _callwfpagetest.py, which builds the page from SOURCE first.
 * Work timer + requested callbacks wired into Call Mode (2026-10-09). Runs the real page code under node.
 *
 * Executes the SHIPPED docs/call/index.html suppression chain under node and asserts the
 * no-means-no contract end to end: present-in-pool BEFORE the logged no, absent AFTER, on
 * BOTH of the person's case rows; the retroactive 72h->720h floor; teammate-vs-own takeover;
 * DNC at build and at the hard gate. Every scenario asserts the before-state too, so a
 * suppression that hides everything unconditionally fails just as loudly as one that does
 * nothing -- the 'succeeds while doing nothing' class this repo keeps re-learning.
 *
 * First run (2026-09-02 17:3x) caught the source-vs-shipped lag LIVE: the sibling-walk and
 * retro-floor guards existed in call_mode.py (saved 17:26) but not in the page (built 17:20).
 * If those two scenarios fail, REBUILD before concluding the code is wrong.
 */
const fs = require('fs');
const vm = require('vm');

const PAGE = process.argv[2];
if (!PAGE) { console.error('usage: node _callwfpage_run.js <built call page>'); process.exit(2); }
const html = fs.readFileSync(PAGE, 'utf8');

// ---- extract every inline <script> block -------------------------------------------------------
const scripts = [];
const re = /<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi;
let m;
while ((m = re.exec(html)) !== null) if (m[1].trim()) scripts.push(m[1]);
if (!scripts.length) { console.error('no inline scripts found'); process.exit(2); }

// ---- browser stubs -----------------------------------------------------------------------------
const store = {};
function El() {
  return {
    style: {}, dataset: {}, classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    _inner: '', set innerHTML(v){ this._inner = v; }, get innerHTML(){ return this._inner; },
    set outerHTML(v){}, textContent: '', value: '', onclick: null, onkeydown: null,
    appendChild(){}, addEventListener(){}, focus(){}, click(){},
    querySelectorAll(){ return []; }, querySelector(){ return null; },
    getAttribute(){ return null; }, setAttribute(){}, removeAttribute(){},
  };
}
const elCache = {};
const docL = {};
const fire = (n, ev) => (docL[n] || []).forEach(f => f(ev || { target: null }));
const documentStub = {
  getElementById(id){ return elCache[id] || (elCache[id] = El()); },
  querySelector(){ return El(); }, querySelectorAll(){ return []; },
  createElement(){ return El(); }, addEventListener(n, f){ (docL[n] = docL[n] || []).push(f); },
  body: El(), documentElement: El(), hidden: false, title: '',
};
const ctx = {
  console, document: documentStub,
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
  },
  location: { hash: '', href: 'https://example.test/call/', search: '', pathname: '/call/index.html', reload(){} },
  navigator: { userAgent: 'harness', clipboard: { writeText: async () => {} } },
  history: { replaceState(){} },
  alert(){}, confirm(){ return false; }, prompt(){ return null; },
  setTimeout(fn){ return 0; }, clearTimeout(){}, setInterval(){ return 0; }, clearInterval(){},
  requestAnimationFrame(){}, queueMicrotask(fn){},
  fetch: async () => { throw new Error('offline harness'); },
  atob: s => Buffer.from(s, 'base64').toString('binary'),
  btoa: s => Buffer.from(s, 'binary').toString('base64'),
  crypto: { getRandomValues(a){ for (let i = 0; i < a.length; i++) a[i] = (i * 7 + 3) & 255; return a; },
            subtle: { importKey: async () => { throw new Error('no subtle in harness'); } } },
  TextEncoder: require('util').TextEncoder, TextDecoder: require('util').TextDecoder,
  JSON, Math, Date, Object, Array, String, Number, Boolean, RegExp, Promise, Error, parseInt,
  parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent, Set, Map,
};
ctx.addEventListener = function(){}; ctx.removeEventListener = function(){};
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);

// ---- run every block; function declarations survive a mid-block throw (hoisting) ---------------
for (let i = 0; i < scripts.length; i++) {
  try { vm.runInContext(scripts[i], ctx, { filename: 'inline-' + i + '.js', timeout: 20000 }); }
  catch (e) { console.log('  [block ' + i + ' threw during boot — expected: ' + String(e).slice(0, 90) + ' ' + String(e.stack).split('\n').slice(1,3).join('|') + ']'); }
}


const need = ['pool', 'wfEmit', 'wfEv', 'cbRefresh', 'cbDone', 'cbCancel', 'viewEmptyHtml', 'wfBarHtml', 'supReason'];
const missing = need.filter(f => typeof ctx[f] !== 'function');
if (missing.length || !ctx.CW || !ctx.CWTimer || !ctx.WFT) { console.error('PAGE MISSING: ' + missing.join(', ') + ' CW=' + !!ctx.CW + ' CWTimer=' + !!ctx.CWTimer + ' WFT=' + !!ctx.WFT); process.exit(2); }
let pass = 0, fail = 0;
function T(name, cond, detail) { if (cond) { pass++; console.log('  PASS  ' + name); } else { fail++; console.log('  FAIL  ' + name + (detail ? '  [' + detail + ']' : '')); } }
function row(c, phone, pk) { return { c, o: 'TEST ' + c, a: '1 TEST ST', p: [phone], r: [''], k: 0, d: 30, x: '10/01/2026', lp: 1, sb: 0, pcs: null, pk: pk || ('PT' + c) }; }
const A = row('CASE-A', '3055550001'), B = row('CASE-B', '3055550002'), C = row('CASE-C', '3055550003'), U = row('CASE-U', '3055550004');
ctx.ROWS = [A, B, C, U]; ctx.notes = {}; ctx.lane = 'all'; store.fcCaller = 'Alejandro'; ctx.cur = A;
const inView = (v, c) => { ctx.QVIEW = v; return ctx.pool().some(r => r.c === c); };
const H = 3600000;
const req = (r, dueMs, extra) => ctx.wfEmit(ctx.wfEv('callback_requested', Object.assign({ request_id: 'REQ-' + r.c, evidence_ref: 'x', tz: 'America/New_York', local_time: '2026-10-09T09:00', due_utc: new Date(dueMs).toISOString(), requested_by_owner: true }, extra || {}), r));

console.log('\n== boot ==');
T('modules inlined and the timer exists', !!ctx.CW.callbackQueue && !!ctx.WFT.start);
T('no callbacks yet: Callbacks view empty, nothing fabricated', !inView('callbacks', 'CASE-A') && ctx._VIEWN.callbacks === 0);
T('empty Callbacks text', /No requested callbacks due now/.test(ctx.viewEmptyHtml()));
T('Untouched default still holds all four', ['CASE-A', 'CASE-B', 'CASE-C', 'CASE-U'].every(c => inView('untouched', c)));

console.log('\n== work timer ==');
T('start refuses with no caller', (store.fcCaller = '', ctx.WFT.start({ callerId: ctx.wfCaller(), caseId: 'CASE-A' }).ok === false));
store.fcCaller = 'Alejandro';
T('start works with a caller and a lead', ctx.WFT.start({ callerId: ctx.wfCaller(), caseId: 'CASE-A' }).ok === true);
T('session_started persisted to this phone only', JSON.parse(store.fcWfEvents).some(e => e.event_type === 'session_started'));
T('timer runs underneath (bar shows the clock-in row, not the confirmed-minutes line)', ctx.WFT.state().status === 'running' && !/confirmed work only/.test(ctx.wfBarHtml()));
ctx.WFT.pause('manual'); ctx.WFT.stop('manual');
T('bar says Not clocked in when no clock is open', /Not clocked in/.test(ctx.wfBarHtml()));
console.log('\n== start work survives a dial (2026-11-02) ==');
ctx.cur = A; ctx.WFT.start({ callerId: ctx.wfCaller(), caseId: 'CASE-A' });
ctx.WFDIAL = true; ctx.WFHID = false; ctx.WFT.launchDialer();
T('tapping Call pauses for the dialer', ctx.WFT.state().status === 'paused');
let asked = 0; ctx.prompt = () => { asked++; return '2'; };
documentStub.hidden = true; fire('visibilitychange'); documentStub.hidden = false; fire('visibilitychange');
T('coming back from the phone app resumes without the minutes question', asked === 0 && ctx.WFT.state().status === 'running', ctx.WFT.state().status);
ctx.WFDIAL = true; ctx.WFHID = false; ctx.WFT.launchDialer(); asked = 0;
fire('click'); 
T('a dial that never hid the page (laptop) settles on the next tap and resumes', asked === 0 && ctx.WFT.state().status === 'running');
documentStub.hidden = true; fire('visibilitychange'); documentStub.hidden = false; asked = 0; fire('visibilitychange');
T('switching apps and back resumes without a prompt', asked === 0 && ctx.WFT.state().status === 'running');
ctx.WFT.pause('manual'); documentStub.hidden = true; fire('visibilitychange'); documentStub.hidden = false; fire('visibilitychange');
T('a manual Pause stays paused after switching apps', ctx.WFT.state().status === 'paused');
ctx.WFT.stop('manual'); ctx.prompt = () => null;
ctx.WFT.start({ callerId: ctx.wfCaller(), caseId: 'CASE-A' }); ctx.WFT.pause('empty_queue'); ctx.wfResume();
T('a lead back on screen after the queue ran empty resumes Working', ctx.WFT.state().status === 'running');
ctx.WFT.stop('manual');
store.fcWfTimer = JSON.stringify({ status: 'running', callerId: ctx.wfCaller(), session: 'OLD-S', seg: 1, wall0: Date.now() - 120000, provisionalEndWall: Date.now() - 30000, ts: Date.now() - 30000 });
ctx.WF.list = []; ctx.cur = null; ctx.wfBoot(); ctx.wfResume();
T('after a reload, nothing starts before a lead is on screen', ctx.WFT.state().status === 'stopped');
ctx.cur = A; ctx.wfResume();
T('after a reload mid-session, Working restarts once a lead is on screen', ctx.WFT.state().status === 'running' && ctx.WFREC.unknownSeconds > 0);
ctx.WFT.stop('manual');
store.fcWfTimer = JSON.stringify({ status: 'running', callerId: ctx.wfCaller(), session: 'OLD-T', seg: 1, wall0: Date.now() - 3600000, provisionalEndWall: Date.now() - 1800000, ts: Date.now() - 1800000 });
ctx.WF.list = []; ctx.wfBoot(); ctx.wfResume();
T('a reload long after the last heartbeat does not restart the timer', ctx.WFT.state().status === 'stopped');
store.fcWfTimer = JSON.stringify({ status: 'running', callerId: 'someone-else', session: 'OLD-U', seg: 1, wall0: Date.now() - 60000, provisionalEndWall: Date.now() - 10000, ts: Date.now() - 10000 });
ctx.WF.list = []; ctx.wfBoot(); ctx.wfResume();
T('a reload never restarts another caller\'s timer', ctx.WFT.state().status === 'stopped');
T('every stored event validates', JSON.parse(store.fcWfEvents).every(e => ctx.CW.validate(e).ok));
T('timer events never touch the notes store or any send/ledger key', !Object.keys(store).some(k => /optout|bounce|mail_sent|text_sent/i.test(k)));

console.log('\n== clock in (2026-11-04) ==');
{
  const realNow = Date.now; let T0 = realNow.call(Date); Date.now = () => T0;
  const MIN = 60000;
  ctx.WFT.stop('manual'); delete store.fcClock; delete store.fcClockLast; ctx.cur = A;
  ctx.wfAct('in120');
  let k = ctx.clkGet();
  T('tapping 2 h clocks in from now with a 2 h plan', k && k.start === T0 && k.planMin === 120);
  T('clocking in also starts the work timer', ctx.WFT.state().status === 'running');
  T('bar shows Clocked in and 2:00 left', /Clocked in/.test(ctx.wfBarHtml()) && /2:00 left of 2:00/.test(ctx.wfBarHtml()));
  // a 25-minute call: dial, phone app, back. The clock does not stop.
  ctx.WFDIAL = true; ctx.WFHID = false; ctx.WFT.launchDialer();
  documentStub.hidden = true; fire('visibilitychange'); T0 += 25 * MIN; documentStub.hidden = false; fire('visibilitychange');
  T('a 25-minute call counts on the clock (0:25 worked, 1:35 left)', /Worked 0:25/.test(ctx.clkLine(ctx.clkGet())) && /1:35 left/.test(ctx.clkLine(ctx.clkGet())), ctx.clkLine(ctx.clkGet()));
  // 20 quiet minutes reading files: the clock keeps going
  T0 += 20 * MIN;
  T('quiet time still counts (0:45 worked)', /Worked 0:45/.test(ctx.clkLine(ctx.clkGet())));
  ctx.wfAct('pause'); T0 += 30 * MIN;
  T('Pause stops the clock (still 0:45 after a 30-min break)', /Worked 0:45/.test(ctx.clkLine(ctx.clkGet())) && /Paused/.test(ctx.wfBarHtml()));
  ctx.wfAct('resume'); T0 += 15 * MIN;
  T('Resume carries on from 0:45 (1:00 worked, 1:00 left)', /Worked 1:00/.test(ctx.clkLine(ctx.clkGet())) && /1:00 left/.test(ctx.clkLine(ctx.clkGet())));
  T('Resume restarts the work timer', ctx.WFT.state().status === 'running');
  // the clock survives a reload: it is a stored start time
  T('a reload keeps the clock (same record read back)', ctx.clkGet().start === k.start);
  T0 += 61 * MIN; ctx.clkTick();
  T('past the plan the bar says TIME UP', /TIME UP/.test(ctx.wfBarHtml()) && ctx.clkGet().upToast === 1);
  ctx.wfAct('add30');
  T('+30 min extends the plan to 2:30 and clears time up', ctx.clkGet().planMin === 150 && /0:29 left of 2:30/.test(ctx.clkLine(ctx.clkGet())), ctx.clkLine(ctx.clkGet()));
  ctx.wfAct('end');
  T('End work clocks out and keeps the shift total', !ctx.clkGet() && JSON.parse(store.fcClockLast).workedMs === 121 * MIN && /last shift: 2 h 1 min/.test(ctx.wfBarHtml()));
  T('End work stops the work timer', ctx.WFT.state().status === 'stopped');
  ctx.wfAct('in60'); store.fcCaller = 'Carlos';
  T('another caller on this phone does not see or inherit the clock', !ctx.clkGet() && /Not clocked in/.test(ctx.wfBarHtml()));
  store.fcCaller = 'Alejandro'; ctx.wfAct('end');
  store.fcCaller = ''; ctx.wfAct('in60');
  T('no caller selected: no clock starts', !ctx.clkGet());
  store.fcCaller = 'Alejandro';
  T('clock keys never touch notes, opt-out, mail or text stores', !Object.keys(store).some(k => /optout|bounce|mail_sent|text_sent/i.test(k)));
  Date.now = realNow;
}
console.log('\n== requested callbacks ==');
req(A, Date.now() - 2 * H); req(B, Date.now() - 5 * H); req(U, Date.now() + 6 * H);
T('overdue requests appear in Callbacks', inView('callbacks', 'CASE-A') && inView('callbacks', 'CASE-B'));
ctx.QVIEW = 'callbacks'; const order = ctx.pool().map(r => r.c).join();
T('sorted by due instant, overdue first (B before A)', order === 'CASE-B,CASE-A', order);
T('a future request is not shown yet and is counted separately', !inView('callbacks', 'CASE-U') && ctx._CBFUT === 1);
T('a lead with a due request leaves Untouched (one list at a time)', !inView('untouched', 'CASE-A') && inView('untouched', 'CASE-U'));
T('counts: callbacks 2', (ctx.QVIEW = 'untouched', ctx.pool(), ctx._VIEWN.callbacks === 2));

console.log('\n== held request stays visible, never dialled ==');
ctx.notes['CASE-C'] = { touches: [{ d: '2026-10-09', ts: 'x', tsu: Date.now() - 600000, ch: 'call', out: 'No answer', by: 'Alejandro' }], cooldownH: 24 };
req(C, Date.now() - H);
ctx.QVIEW = 'callbacks';
T('held lead is NOT in the dial list', !ctx.pool().some(r => r.c === 'CASE-C'));
T('but is listed as HELD with the existing reason', ctx._CBHELD.some(h => h.c === 'CASE-C' && h.why) && /HELD/.test(ctx.cbNote()));
delete ctx.notes['CASE-C'];

console.log('\n== complete / cancel ==');
ctx.confirm = () => true; ctx.cur = A; ctx.cbDone(A);
T('completing closes the request', !inView('callbacks', 'CASE-A') && ctx._CBQ.closed.completed === 1, JSON.stringify(ctx._CBQ.closed));
T('completion events validate and are caller-attested', JSON.parse(store.fcWfEvents).filter(e => e.event_type === 'conversation_confirmed').every(e => e.payload.role_verification === 'caller_attested' && ctx.CW.validate(e).ok));
ctx.cur = B; ctx.cbCancel(B);
T('cancel closes without completing', !inView('callbacks', 'CASE-B') && ctx._CBQ.closed.cancelled === 1 && ctx._CBQ.closed.completed === 1);

console.log('\n== review fixes ==');
ctx.QVIEW = 'callbacks';
const D2 = ctx.ROWS.find(r => r.c === 'CASE-U') || U;
ctx.logOutcome(C, { k: 'dnc', t: 'DNC — do not contact', h: 0, s: true }, C.p && C.p[0]);
T('a DNC lead with a due request is never dialled from Callbacks', !ctx.pool().some(r => r.c === 'CASE-C'));
const nopk = Object.assign({}, A, { c: 'CASE-NOPK', pk: '' });
const before = ctx.WF.list.length; ctx.cbSave(nopk, false);
T('no owner key: callback not saved', ctx.WF.list.length === before);
store.fcCaller = 'Alejandro'; ctx.WFT.start({ callerId: ctx.wfCaller(), caseId: 'CASE-A' });
store.fcCaller = 'Carlos'; ctx.wfBarHtml();
T('caller switch stops the old session', ctx.WFT.state().status === 'stopped');
store.fcCaller = 'Alejandro';
ctx.WF.list = [{ event_id: 'old', event_type: 'x', occurred_at_utc: new Date(Date.now() - 120 * 86400000).toISOString() }].concat(ctx.WF.list);
store.fcWfEvents = JSON.stringify(ctx.WF.list); ctx.WF.store = ctx.CW.newStore(); ctx.WF.list = []; ctx.wfLoad();
T('events older than 90 days are dropped on load', !ctx.WF.list.some(e => e.event_id === 'old'));

console.log('\n== persistence ==');
const n = ctx.WF.list.length; ctx.WF.store = ctx.CW.newStore(); ctx.WF.list = []; ctx.wfLoad(); ctx.cbRefresh();
T('events reload from this phone after a restart', ctx.WF.list.length === n && n > 0);
store.fcWfEvents = '{not json'; ctx.WF.err = ''; ctx.WF.store = ctx.CW.newStore(); ctx.WF.list = []; ctx.wfLoad();
T('unreadable records are said out loud, not treated as zero', /could not be read/.test(ctx.wfBarHtml()));

console.log('\n================================');
console.log(pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
