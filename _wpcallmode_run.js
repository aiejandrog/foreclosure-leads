/* _wpcallmode_run.js -- run via _wpcallmodetest.py, which builds the Call Mode page from SOURCE first.
 * "Look up on Whitepages" on a Find-a-number card (2026-10-30, Alejandro). Real page code under node,
 * fake bridge answers, no network. Asserts:
 *   - the button shows only on lk (no-number) cards, names the price, and is not a confirm dialog;
 *   - a tap posts {case} to /wp/lookup once and shows a spinner while it waits;
 *   - owner and likely numbers render with their label and a tap-to-dial button; the lead's own
 *     do-not-call number is dropped; texting has no path (no Text button, saved numbers are call-only);
 *   - notes.wpm and notes.wpl {ts, n, cached} are saved; the button locks ("looked up, N numbers"),
 *     also for a cached answer and for zero numbers, and a second tap or a reload sends nothing;
 *   - blocked key (403) and budget show their plain messages and leave the button usable;
 *   - tapping a returned number saves it as a call-only wpm number on a normal card.
 */
const fs = require('fs');
const vm = require('vm');

const PAGE = process.argv[2];
if (!PAGE) { console.error('usage: node _wpcallmode_run.js <built call page>'); process.exit(2); }
const html0 = fs.readFileSync(PAGE, 'utf8');
const scripts = [];
const re = /<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi;
let m;
while ((m = re.exec(html0)) !== null) if (m[1].trim()) scripts.push(m[1]);

const store = {};
function El(id) {
  const ls = {};
  return {
    id, style: {}, dataset: {}, disabled: false, value: '',
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    _inner: '', set innerHTML(v){ this._inner = v; }, get innerHTML(){ return this._inner; },
    set outerHTML(v){}, textContent: '', onclick: null, onkeydown: null,
    appendChild(){}, removeChild(){}, focus(){},
    addEventListener(t, fn){ (ls[t] = ls[t] || []).push(fn); },
    removeEventListener(){},
    click(){ const ev = { preventDefault(){}, stopImmediatePropagation(){} };
             (ls.click || []).forEach(fn => fn.call(this, ev)); if (this.onclick) this.onclick(ev); },
    querySelectorAll(){ return []; }, querySelector(){ return null; },
    getAttribute(k){ return this['_a_' + k] == null ? null : this['_a_' + k]; },
    setAttribute(k, v){ this['_a_' + k] = String(v); }, removeAttribute(){},
  };
}
const elCache = {};
function appEl(){ const e = El('app'); Object.defineProperty(e, 'innerHTML', {
  set(v){ this._inner = v; Object.keys(elCache).forEach(k => { if (k !== 'app' && k !== 'sheet') delete elCache[k]; }); },
  get(){ return this._inner; } }); return e; }
elCache.app = appEl();
const documentStub = {
  getElementById(id){ return elCache[id] || (elCache[id] = El(id)); },
  querySelector(){ return El(); }, querySelectorAll(){ return []; },
  createElement(){ return El(); }, addEventListener(){},
  body: El('body'), documentElement: El('html'), hidden: false, title: '',
};
let timers = [];
const ctx = {
  console, document: documentStub,
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
  },
  location: { hash: '', href: 'https://example.test/call/', protocol: 'https:', search: '', pathname: '/call/index.html', reload(){} },
  navigator: { userAgent: 'harness', clipboard: { writeText: async () => {} } },
  history: { replaceState(){} },
  alert(){}, confirm(){ return false; }, prompt(){ return null; },
  setTimeout(fn){ timers.push(fn); return timers.length; }, clearTimeout(){}, setInterval(){ return 0; }, clearInterval(){},
  requestAnimationFrame(){}, queueMicrotask(fn){},
  fetch: async () => { throw new Error('offline harness'); },
  atob: s => Buffer.from(s, 'base64').toString('binary'),
  btoa: s => Buffer.from(s, 'binary').toString('base64'),
  crypto: { getRandomValues(a){ for (let i = 0; i < a.length; i++) a[i] = (i * 7 + 3) & 255; return a; },
            subtle: { importKey: async () => { throw new Error('no subtle in harness'); } } },
  TextEncoder: require('util').TextEncoder, TextDecoder: require('util').TextDecoder,
  JSON, Math, Date, Object, Array, String, Number, Boolean, RegExp, Promise, Error, parseInt,
  parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent, Set, Map,
  Response: class { constructor(b, o){ this.b = b; this.status = (o && o.status) || 200; } },
};
ctx.addEventListener = function(){}; ctx.removeEventListener = function(){};
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);
for (let i = 0; i < scripts.length; i++) {
  try { vm.runInContext(scripts[i], ctx, { filename: 'inline-' + i + '.js', timeout: 20000 }); }
  catch (e) { console.log('  [block ' + i + ' threw during boot: ' + String(e).slice(0, 90) + ']'); }
}
const need = ['wpLookup', 'wpBlock', 'wpDone', 'lookupList', 'lkSave', 'bridgeFetch'];
const missing = need.filter(f => typeof ctx[f] !== 'function');
if (missing.length) { console.error('PAGE MISSING: ' + missing.join(', ')); process.exit(2); }

(async () => {
  let pass = 0, fail = 0;
  function T(name, cond, detail) { if (cond) { pass++; console.log('  PASS  ' + name); } else { fail++; console.log('  FAIL  ' + name + (detail ? '  [' + String(detail).slice(0, 300) + ']' : '')); } }
  const flush = () => new Promise(r => setImmediate(r));
  async function settle() { for (let i = 0; i < 8; i++) await flush(); }

  function cov(c, extra) { return Object.assign({ c, o: 'TEST ' + c, on: 'Test Owner', a: '1 TEST ST', x: '11/20/2026', d: 20, sb: 0, pk: 'PK' + c, lk: 1 }, extra || {}); }
  const L1 = cov('2099-000001-CA-01'), L2 = cov('2099-000002-CA-01'), L3 = cov('2099-000003-CA-01'),
        L4 = cov('2099-000004-CA-01'), L5 = cov('2099-000005-CA-01');
  const LN = { c: '2099-000050-CA-01', o: 'TEST DIAL', on: 'Dial Owner', a: '2 TEST ST', x: '11/20/2026', d: 20, sb: 0, pk: 'PKN' };   // no lk: not a lookup card
  ctx.COV = [L1, L2, L3, L4, L5, LN]; ctx.ROWS = []; ctx.notes = {}; ctx.lane = 'all'; ctx.BLANE = '__lk'; ctx.SCREEN = 'lead';
  store.fcCaller = 'Alejandro';
  ctx.notes[L1.c] = { badph: ['3055559999'] };   // he marked that number bad on this lead earlier

  const posts = [];
  const answers = {};
  ctx.__bridgeOrigFetch = async (url, o) => {
    posts.push({ url, body: o && o.body, key: o && o.headers && o.headers['X-Dealflow-Key'] });
    const c = JSON.parse(o.body).case;
    const a = answers[c];
    if (!a) throw new Error('no answer planned');
    return { status: 200, clone() { return this; }, json: async () => a };
  };
  answers[L1.c] = { ok: true, cached: false, spent: 0.22, numbers: [
    { n: '3055550111', kind: 'owner', type: 'mobile' }, { n: '3055550301', kind: 'likely', type: 'landline' },
    { n: '3055559999', kind: 'owner', type: 'landline' } ] };            // the last is a number he marked bad on this lead
  answers[L2.c] = { ok: true, cached: true, spent: 0, numbers: [{ n: '3055550222', kind: 'owner', type: 'mobile' }] };
  answers[L3.c] = { ok: false, blocked: 'key', http: 403, err: 'Whitepages rejected the key (HTTP 403).' };
  answers[L4.c] = { ok: false, blocked: 'budget', err: 'Whitepages daily cap reached.' };
  answers[L5.c] = { ok: true, cached: false, spent: 0.44, numbers: [] };

  console.log('\n== the button ==');
  let html = ctx.lookupList();
  const btnCount = (html.match(/class="wplk"/g) || []).length;
  T('every Find-a-number card carries the lookup button (5 lk cards)', btnCount === 5, btnCount);
  T('the button names the price before the tap', /Look up on Whitepages \(about \$0\.22\)/.test(html));
  T('a card that is not on the lookup list has no button', !/2099-000050-CA-01/.test(html));
  T('no confirm dialog in the lookup code', !/confirm\(/.test(String(ctx.wpLookup)));
  T('the wiring binds the button to wpLookup', /button\.wplk[\s\S]{0,200}wpLookup\(/.test(html0));
  T('no Text button on the list', !/id="tx"|>Text</.test(html));

  console.log('\n== a tap: spinner, one request, numbers ==');
  ctx.wpLookup(L1.c);
  T('while waiting: spinner and a disabled button', /wpspin/.test(ctx.wpBlock(L1)) && /class="wplk" disabled>Looking up/.test(ctx.wpBlock(L1)), ctx.wpBlock(L1));
  ctx.wpLookup(L1.c);
  await settle();
  T('exactly one request, to /wp/lookup, carrying only the case',
    posts.length === 1 && /\/wp\/lookup$/.test(posts[0].url) && posts[0].body === JSON.stringify({ case: L1.c }), JSON.stringify(posts));
  const n1 = ctx.notes[L1.c] || {};
  T('wpm holds the owner and the likely number only (not the one marked bad)', JSON.stringify(n1.wpm) === '["3055550111","3055550301"]', JSON.stringify(n1.wpm));
  T('wpl records ts, n, cached', n1.wpl && typeof n1.wpl.ts === 'number' && n1.wpl.n === 2 && n1.wpl.cached === false, JSON.stringify(n1.wpl));
  T('notes.phone is NOT set (nothing dialed yet)', !n1.phone);
  html = ctx.lookupList();
  const blk = ctx.wpBlock(L1);
  T('the button is locked and says looked up, 2 numbers', /class="wplk" disabled>Looked up, 2 numbers</.test(blk), blk);
  T('owner and likely owner are labelled, each with a tap-to-dial',
    /\(305\) 555-0111 &middot; owner/.test(blk) && /\(305\) 555-0301 &middot; likely owner/.test(blk) && (blk.match(/class="wpdial"/g) || []).length === 2, blk);
  T('the likely-owner line says call only, no text', /Call only\. A likely owner[^<]*no text until a call reaches them/.test(blk));
  T('there is no Text button on the list after a lookup', !/id="tx"|>Text</.test(html));
  T('the lead\'s do-not-call number is nowhere on the card', !/555-9999/.test(html));
  const before = posts.length;
  ctx.wpLookup(L1.c); await settle();
  T('a second tap sends nothing', posts.length === before);
  ctx.WPBUSY[L1.c] = 0;
  T('the saved note alone locks the button (reload / another phone)', /disabled>Looked up, 2 numbers/.test(ctx.wpBlock(L1)));

  console.log('\n== cached, and a lookup that finds nothing, also lock ==');
  ctx.wpLookup(L2.c); await settle();
  T('cached answer: saved with cached:true and says no charge', (ctx.notes[L2.c].wpl || {}).cached === true && /No charge/.test(ctx.wpBlock(L2)), JSON.stringify(ctx.notes[L2.c]));
  T('cached answer: the button is locked', /disabled>Looked up, 1 number</.test(ctx.wpBlock(L2)));
  ctx.wpLookup(L5.c); await settle();
  T('zero numbers still locks the button (it was paid for) and says so',
    /disabled>Looked up, 0 numbers</.test(ctx.wpBlock(L5)) && /no number/.test(ctx.wpBlock(L5)), ctx.wpBlock(L5));

  console.log('\n== refusals ==');
  ctx.wpLookup(L3.c); await settle();
  T('403: the plain key message', ctx.WPERR[L3.c] === 'Whitepages key rejected (403): check the key on the bridge PC', ctx.WPERR[L3.c]);
  T('403: nothing saved and the button is usable again', !(ctx.notes[L3.c] || {}).wpl && /class="wplk" data-c=/.test(ctx.wpBlock(L3)) && /key rejected \(403\)/.test(ctx.wpBlock(L3)));
  ctx.wpLookup(L4.c); await settle();
  T('budget: the plain cap message, nothing saved', ctx.WPERR[L4.c] === 'Whitepages daily cap reached' && !(ctx.notes[L4.c] || {}).wpl);
  const L7 = cov('2099-000007-CA-01');
  ctx.COV.push(L7); ctx.notes[L7.c] = { optout: true };
  const b2 = posts.length;
  ctx.wpLookup(L7.c); await settle();
  T('an opted-out person is not looked up (no request, no spend)', posts.length === b2);

  console.log('\n== the device key rides along ==');
  store.dfBridgeKey = 'DEVKEY-TEST';
  const L8 = cov('2099-000008-CA-01'); ctx.COV.push(L8);
  answers[L8.c] = { ok: true, cached: false, spent: 0.22, numbers: [] };
  ctx.wpLookup(L8.c); await settle();
  T('bridgeFetch added X-Dealflow-Key', posts[posts.length - 1].key === 'DEVKEY-TEST', JSON.stringify(posts[posts.length - 1]));
  delete store.dfBridgeKey;

  console.log('\n== tap-to-dial: a normal card, the number is call-only ==');
  ctx.ROWS = [];
  try { ctx.lkSave(L1.c, '3055550111'); } catch (e) { console.log('  [lkSave threw: ' + String(e).slice(0, 160) + ']'); }
  const row = ctx.ROWS.filter(r => r.c === L1.c)[0];
  T('the number became a dial row tagged wpm (the call-only path)', !!row && row.p[0] === '3055550111' && row.ps[0] === 'wpm', JSON.stringify(row));
  T('it is saved as the lead phone', ctx.notes[L1.c].phone === '3055550111');
  T('the saved lookup (wpl) survives the dial', ctx.notes[L1.c].wpl && ctx.notes[L1.c].wpl.n === 2);

  console.log('\n' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('HARNESS ERROR: ' + (e && e.stack || e)); process.exit(2); });
