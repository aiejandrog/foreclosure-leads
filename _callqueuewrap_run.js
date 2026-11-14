/* _callqueuewrap_run.js -- run via _callqueuewraptest.py, which builds the page from SOURCE first.
 * F-site-02 (2026-11-13): "Queue clear." was shown while untouched leads remained. Step past every
 * card with Next (which writes nothing), work the last one, and `i` sat one past the end of a pool
 * that still held every lead stepped past. Runs the real page code under node; invented rows only.
 */
const fs = require('fs');
const vm = require('vm');

const PAGE = process.argv[2];
if (!PAGE) { console.error('usage: node _callqueuewrap_run.js <built call page>'); process.exit(2); }
const html = fs.readFileSync(PAGE, 'utf8');
const scripts = [];
const re = /<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi;
let m;
while ((m = re.exec(html)) !== null) if (m[1].trim()) scripts.push(m[1]);
if (!scripts.length) { console.error('no inline scripts found'); process.exit(2); }

// ---- browser stubs (same shape as _callwfpage_run.js) ------------------------------------------
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
const documentStub = {
  getElementById(id){ return elCache[id] || (elCache[id] = El()); },
  querySelector(){ return El(); }, querySelectorAll(){ return []; },
  createElement(){ return El(); }, addEventListener(){},
  body: El(), documentElement: El(), hidden: false, title: '', activeElement: null,
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
  requestAnimationFrame(){}, queueMicrotask(fn){}, scrollTo(){},
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
for (let i = 0; i < scripts.length; i++) {
  try { vm.runInContext(scripts[i], ctx, { filename: 'inline-' + i + '.js', timeout: 20000 }); }
  catch (e) { console.log('  [block ' + i + ' threw during boot, expected: ' + String(e).slice(0, 90) + ']'); }
}
const need = ['pool', 'render', 'navNext', 'navBack', 'advance', 'logOutcome'];
const missing = need.filter(f => typeof ctx[f] !== 'function');
if (missing.length) { console.error('PAGE MISSING: ' + missing.join(', ')); process.exit(2); }

let pass = 0, fail = 0;
function T(name, cond, detail) { if (cond) { pass++; console.log('  PASS  ' + name); } else { fail++; console.log('  FAIL  ' + name + (detail ? '  [' + detail + ']' : '')); } }
const run = js => vm.runInContext(js, ctx);
const app = () => String(documentStub.getElementById('app').innerHTML || '');
const state = () => run('({i: i, n: pool().length, cur: cur && cur.c, screen: SCREEN, view: QVIEW})');
const shows = () => { const s = state(); return /Queue clear/.test(app()) ? 'QUEUE_CLEAR' : (s.cur && s.i < s.n ? 'LEAD' : 'OTHER'); };
function row(c, phone) { return { c, o: 'TEST ' + c, a: '1 TEST ST', p: [phone], r: [''], k: 0, d: 30, x: '10/01/2026', lp: 1, sb: 0, pcs: null, pk: 'PT' + c }; }
function fresh(n) {
  const rows = [];
  for (let k = 1; k <= n; k++) rows.push(row('CASE-' + k, '305555010' + k));
  ctx.__rows = rows;
  for (const k of Object.keys(store)) delete store[k];
  store.fcCaller = 'Tester';
  run("ROWS = __rows; notes = {}; lane = 'all'; QVIEW = 'untouched'; i = 0; cur = null; SCREEN = 'lead';"
    + " BLANE = null; _NAVB = []; _NAVF = []; _QVBACK = null; LKFROM = null; _WORKED.length = 0; render();");
}
const talked = "(function(){ var r = cur, o = OUTCOMES.filter(function(x){ return x.k === 'talked'; })[0];"
  + " logOutcome(r, o, r.p[0]); SCREEN = 'outcome'; advance(r.c, null); })()";

console.log('\n== baseline: six untouched invented leads ==');
fresh(6);
let s = state();
T('six leads in the Untouched pool, first one on screen', s.n === 6 && s.cur === 'CASE-1' && shows() === 'LEAD', JSON.stringify(s));

console.log('\n== F-site-02: Next past five cards, then Talked on the last ==');
for (let k = 0; k < 5; k++) run('navNext()');
s = state();
T('five Next taps land on the sixth lead and write nothing', s.cur === 'CASE-6' && s.n === 6 && run('JSON.stringify(notes)') === '{}', JSON.stringify(s));
run(talked);
s = state();
T('the worked lead left Untouched; the five stepped past are still there', s.n === 5 && run('pool().every(function(r){ return r.c !== "CASE-6"; })'), JSON.stringify(s));
T('no Queue clear while the pool holds leads', shows() !== 'QUEUE_CLEAR', shows() + ' ' + JSON.stringify(s));
T('a lead card is on screen, and it is one of the five left', shows() === 'LEAD' && s.i < s.n && /^CASE-[1-5]$/.test(String(s.cur)), JSON.stringify(s));
T('it wrapped to the top of what is left', s.i === 0 && s.cur === 'CASE-1', JSON.stringify(s));
run('navBack()'); s = state();
T('Back after the wrap still opens a lead that is in the pool', shows() === 'LEAD' && s.i < s.n, JSON.stringify(s));

console.log('\n== Next off the last card: back to the first, never Queue clear ==');
fresh(3);
for (let k = 0; k < 3; k++) run('navNext()');
s = state();
T('three Next taps on three leads show a lead, not Queue clear', shows() === 'LEAD' && s.n === 3 && s.cur === 'CASE-1', shows() + ' ' + JSON.stringify(s));
T('still nothing written', run('JSON.stringify(notes)') === '{}');

console.log('\n== a pool that really is empty still says so ==');
fresh(1);
run(talked);
s = state();
T('one lead, worked: pool is empty and the empty-view text shows', s.n === 0 && /No untouched leads/.test(app()) && !/Queue clear/.test(app()), JSON.stringify(s));
T('the session count is shown', /1 lead worked this session/.test(app()), app().slice(-300));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
