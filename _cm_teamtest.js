/* _cm_teamtest.js -- run: node _cm_teamtest.js   (tests the BUILT page, so rebuild first)
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

const PAGE = require('path').join(__dirname, 'docs', 'call', 'index.html');
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
    style: {}, dataset: {}, classList: { add(){}, remove(){}, contains(){ return false; } },
    _inner: '', set innerHTML(v){ this._inner = v; }, get innerHTML(){ return this._inner; },
    set outerHTML(v){}, textContent: '', value: '', onclick: null, onkeydown: null,
    appendChild(){}, addEventListener(){}, focus(){}, click(){},
    querySelectorAll(){ return []; }, querySelector(){ return null; },
    getAttribute(){ return null; }, setAttribute(){}, removeAttribute(){},
  };
}
const elCache = {};
const documentStub = {
  getElementById(id){ return elCache[id] || (elCache[id] = El()); },
  querySelector(){ return El(); }, querySelectorAll(){ return []; },
  createElement(){ return El(); }, addEventListener(){},
  body: El(), documentElement: El(), hidden: false, title: '',
};
const ctx = {
  console, document: documentStub,
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
  },
  location: { hash: '', href: 'https://example.test/call/', search: '', reload(){} },
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
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);

// ---- run every block; function declarations survive a mid-block throw (hoisting) ---------------
for (let i = 0; i < scripts.length; i++) {
  try { vm.runInContext(scripts[i], ctx, { filename: 'inline-' + i + '.js', timeout: 20000 }); }
  catch (e) { console.log('  [block ' + i + ' threw during boot — expected: ' + String(e).slice(0, 90) + ']'); }
}

// ---- confirm the functions under test exist in the SHIPPED page --------------------------------
const need = ['pool', 'suppressed', 'hardSuppressed', 'logOutcome', 'lastCall', '_teammateCall', 'caller'];
const missing = need.filter(f => typeof ctx[f] !== 'function');
if (missing.length) { console.error('SHIPPED PAGE MISSING: ' + missing.join(', ')); process.exit(2); }

// ---- test fixtures -----------------------------------------------------------------------------
const NOW = Date.now();
function row(c, phone, pcs) {
  return { c, o: 'TEST ' + c, a: '1 TEST ST', p: [phone], r: [''], k: 0, d: 30, x: '10/01/2026',
           lp: 1, sb: 0, pcs: pcs || null, pk: 'PTESTPERSON' };
}
const A = row('CASE-A', '3055550001', ['CASE-A', 'CASE-B']);
const B = row('CASE-B', '3055550002', ['CASE-A', 'CASE-B']);   // same human, second case row
const C = row('CASE-C', '3055550003', null);                    // retroactive-72h scenario
const D = row('CASE-D', '3055550004', null);                    // teammate takeover scenario
const E = row('CASE-E', '3055550005', null);                    // unrelated control — must survive
ctx.ROWS = [A, B, C, D, E];
ctx.notes = {};
ctx.lane = 'lp';           // all fixtures are LP rows
store.fcCaller = 'Alejandro';   // caller() identity source, if the page reads it

let pass = 0, fail = 0;
function T(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (detail ? '  [' + detail + ']' : '')); }
}
const inPool = c => ctx.pool().some(r => r.c === c);

console.log('\n== BEFORE any outcome: everyone must be dialable (the can-fail half) ==');
T('A in queue before', inPool('CASE-A'));
T('B in queue before', inPool('CASE-B'));
T('C in queue before', inPool('CASE-C'));
T('E in queue before', inPool('CASE-E'));

console.log('\n== CHAIN 1+2: a logged NO lands in the store and suppresses the PERSON ==');
let logged = false;
try { ctx.logOutcome(A, { k: 'notint', t: 'Not interested', h: 720, s: false }, '3055550001'); logged = true; }
catch (e) { console.log('  logOutcome threw: ' + String(e).slice(0, 120)); }
T('logOutcome executed', logged);
T('the NO landed in the note store', !!(ctx.notes['CASE-A'] && ctx.notes['CASE-A'].status === 'Not interested'),
  JSON.stringify(ctx.notes['CASE-A'] || null).slice(0, 80));
T('CASE-A gone from next queue build', !inPool('CASE-A'), ctx.suppressed(A));
T('CASE-B (same person, sibling case) ALSO gone', !inPool('CASE-B'), 'suppressed says: ' + ctx.suppressed(B));
T('unrelated CASE-E still dialable', inPool('CASE-E'));

console.log('\n== CHAIN 2b: retroactive floor — an OLD note with the stale 72h cooldown ==');
ctx.notes['CASE-C'] = { status: 'Not interested', cooldownH: 72,
  touches: [{ d: '2026-08-28', ts: 'x', tsu: NOW - 5 * 86400000, ch: 'call', out: 'Not interested', by: 'Alejandro' }] };
T('5-day-old NO with stale 72h note still suppresses (720 floor)', !inPool('CASE-C'), ctx.suppressed(C));

console.log('\n== CHAIN 3: dial-time — the already-painted screen (takeover trigger) ==');
ctx.notes['CASE-D'] = { touches: [{ d: '2026-09-02', ts: 'x', tsu: NOW - 5 * 60000, ch: 'call', out: 'No answer', by: 'Carlos' }],
                        cooldownH: 24 };
const tD = ctx._teammateCall(D);
T('teammate call 5min ago triggers the takeover', !!tD && tD.by === 'Carlos', JSON.stringify(tD));
ctx.notes['CASE-D'].touches[0].by = ctx.caller() || 'Alejandro';
T('my OWN call does NOT trigger it (multi-number sequences survive)', !ctx._teammateCall(D));

console.log('\n== CHAIN 4: DNC at queue build AND at dial gate ==');
try { ctx.logOutcome(E, { k: 'dnc', t: 'DNC — do not contact', h: 0, s: true }, '3055550005'); } catch (e) {}
T('DNC gone from queue', !inPool('CASE-E'), ctx.suppressed(E));
T('DNC blocks at the HARD gate too (texts, dial screen)', !!ctx.hardSuppressed(E), ctx.hardSuppressed(E));

console.log('\n== SIBLING TAKEOVER (the hole I flagged to the DEALFLOW session) ==');
ctx.notes = {};
ctx.notes['CASE-B'] = { touches: [{ d: '2026-09-02', ts: 'x', tsu: NOW - 10 * 60000, ch: 'call', out: 'Talked', by: 'Carlos' }],
                        cooldownH: 72 };
const sib = ctx._teammateCall(A);   // Carlos called the SIBLING case; screen shows CASE-A
T('KNOWN GAP (expected FAIL until pcs-aware): sibling-case teammate call triggers takeover',
  !!sib && sib.by === 'Carlos', 'queue drop covers it (suppressed: "' + ctx.suppressed(A) + '") but the painted screen does not');

console.log('\n================================');
console.log(pass + ' passed, ' + fail + ' failed');
process.exit(0);   // exit code reserved; the report reads the lines
