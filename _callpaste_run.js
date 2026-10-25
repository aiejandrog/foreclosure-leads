/* _callpaste_run.js -- run via _callpastetest.py, which builds the Call Mode page from SOURCE first.
 * Paste a Whitepages number on the lead card and call it (2026-10-24, Alejandro). Runs the real
 * page code under node and asserts:
 *   - the card carries the paste box and a disabled Call button until a 10-digit number is in it;
 *   - a pasted number is refused when it is on the lead's do-not-call list, marked bad, or the
 *     person is opted out / a hard no, and nothing is written in those cases;
 *   - a good paste saves notes.wpm (+ notes.phone when empty), adds the number to the row tagged
 *     'wpm', puts it on the dial button, and the dial logs a pending dial with that number's ph4
 *     and opens the outcome screen -- the same path as a traced number;
 *   - a number pasted on another device (notes.wpm from a sync) rides along on the next render;
 *   - the board's mergeNotes unions wpm from both sides;
 *   - paste an EMAIL in the same box (2026-10-24): mailCheck refuses an opted-out person, the investor
 *     lane, a reply on file, a closed three-email ladder and a cold email under the bridge's bounce
 *     pause; composeMail renders the board's own bodies (first touch / early / tax deed / follow /
 *     final, EN and ES) with no token left; the email screen hands off to the mail app or Gmail and
 *     writes one ch:'email' touch + notes.wpe only after the operator confirms it sent.
 */
const fs = require('fs');
const vm = require('vm');

const PAGE = process.argv[2];
if (!PAGE) { console.error('usage: node _callpaste_run.js <built call page>'); process.exit(2); }
const html = fs.readFileSync(PAGE, 'utf8');
const scripts = [];
const re = /<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi;
let m;
while ((m = re.exec(html)) !== null) if (m[1].trim()) scripts.push(m[1]);

// ---- browser stubs: elements remember their listeners so click() fires them ---------------------
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
// Setting #app's innerHTML replaces every element inside it in a browser; mirror that so a
// listener wired on the previous paint's dial button does not survive into the next paint.
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
};
ctx.addEventListener = function(){}; ctx.removeEventListener = function(){};
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);
for (let i = 0; i < scripts.length; i++) {
  try { vm.runInContext(scripts[i], ctx, { filename: 'inline-' + i + '.js', timeout: 20000 }); }
  catch (e) { console.log('  [block ' + i + ' threw during boot — expected: ' + String(e).slice(0, 90) + ']'); }
}
const need = ['pasteCheck', 'pasteCall', 'pasteBlock', 'applyPasted', 'wpmNums', 'screenLead', 'hardSuppressed', 'pool'];
const missing = need.filter(f => typeof ctx[f] !== 'function');
if (missing.length) { console.error('PAGE MISSING: ' + missing.join(', ')); process.exit(2); }

let pass = 0, fail = 0;
function T(name, cond, detail) { if (cond) { pass++; console.log('  PASS  ' + name); } else { fail++; console.log('  FAIL  ' + name + (detail ? '  [' + detail + ']' : '')); } }
function row(c, phone) { return { c, o: 'TEST ' + c, on: 'Test Owner', a: '1 TEST ST', p: [phone], r: [''], ps: [''], k: 0, d: 30, x: '10/01/2026', sb: 0, pcs: null, pk: 'PT' + c, dn: [['3055559999', 'h']] }; }
const A = row('CASE-A', '3055550001'), B = row('CASE-B', '3055550002'), S = row('CASE-S', '3055550003');
ctx.ROWS = [A, B, S]; ctx.notes = {}; ctx.lane = 'all'; store.fcCaller = 'Alejandro'; ctx.SCREEN = 'lead';
const app = () => documentStub.getElementById('app').innerHTML;
function runTimers(){ const t = timers; timers = []; t.forEach(fn => { try { fn(); } catch (e) { console.log('  [timer threw: ' + String(e).slice(0, 120) + ']'); } }); }

console.log('\n== the card ==');
ctx.cur = A; ctx.i = 0; ctx.phIdx = 0;
let painted = true;
try { ctx.screenLead(); } catch (e) { painted = false; console.log('  [screenLead threw: ' + String(e).slice(0, 200) + ']'); }
T('the lead card paints', painted && /class="card"/.test(app()));
T('the card opens with the shared nav strip: Board / Call Mode / Carlos / Worker (dealflow_nav)', /class="dfnav"/.test(app()) && /href="\.\.\/"[^>]*>Board<\/a>/.test(app()) && /aria-current="page"[^>]*>Call Mode<\/a>/.test(app()) && /#worker=open/.test(app()) && !/#worker=morning/.test(app()), (app().match(/<nav class="dfnav".*?<\/nav>/) || [''])[0]);
T('it carries the paste box and its Call button', /id="pcnum"/.test(app()) && /id="pcgo"/.test(app()));
T('the box says pasted numbers are not registry-checked', /not checked against the do-not-call registry/.test(app()));
const dialHref = () => ((app().match(/<a class="dial" href="([^"]*)"/) || [])[1] || '');
T('the dial button still carries the traced number', /3055550001/.test(dialHref()), dialHref());

console.log('\n== refusals write nothing ==');
const before = JSON.stringify(ctx.notes);
T('fewer than 10 digits', /10-digit/.test(ctx.pasteCheck(A, '305555').why));
T('garbage', /10-digit/.test(ctx.pasteCheck(A, 'call me maybe').why));
T('the lead\'s own do-not-call number', /do-not-call list/.test(ctx.pasteCheck(A, '(305) 555-9999').why));
ctx.notes['CASE-A'] = { badph: ['3055551111'] };
T('a number he marked bad on this lead', /marked that number bad/.test(ctx.pasteCheck(A, '305-555-1111').why));
ctx.notes['CASE-S'] = { optout: true };
T('an opted-out person', /opted out/.test(ctx.pasteCheck(S, '3055552222').why));
ctx.notes['CASE-B'] = { no: 'hard' };
T('a hard no', /hard no/.test(ctx.pasteCheck(B, '3055552222').why));
delete ctx.notes['CASE-B'];
ctx.pasteCall(S, '3055552222'); ctx.pasteCall(A, '3055559999'); ctx.pasteCall(A, '12');
T('a refused paste adds no number to the row', A.p.length === 1 && S.p.length === 1, JSON.stringify([A.p, S.p]));
T('a refused paste writes no wpm and no dial', !(ctx.notes['CASE-A'] || {}).wpm && !(ctx.notes['CASE-S'] || {}).wpm
  && !(ctx.notes['CASE-A'] || {}).dials, JSON.stringify(ctx.notes));

console.log('\n== a good paste calls ==');
ctx.cur = A; ctx.phIdx = 0; timers = [];
ctx.pasteCall(A, '+1 (786) 555-0199');
const nA = ctx.notes['CASE-A'] || {};
T('the number is saved on the lead note as a Whitepages paste', JSON.stringify(nA.wpm) === '["7865550199"]', JSON.stringify(nA.wpm));
T('it fills the board\'s phone box when that was empty', nA.phone === '7865550199', nA.phone);
T('the row carries it, tagged wpm', A.p[1] === '7865550199' && A.ps[1] === 'wpm' && A.r.length === 2, JSON.stringify([A.p, A.ps, A.r]));
T('it is the number on the dial button', ctx.phIdx === 1 && /7865550199/.test(dialHref()), dialHref());
T('the card says where the number came from', /pasted from Whitepages/i.test(app()));
T('the dial was logged as a pending attempt with that number\'s last four',
  (nA.dials || []).length === 1 && nA.dials[0].ph4 === '0199' && nA.dials[0].oc === 'pending', JSON.stringify(nA.dials));
runTimers();
T('the outcome screen opened, the same one a traced number gets', ctx.SCREEN === 'outcome', ctx.SCREEN);
T('a second paste of the same number does not duplicate it', (ctx.SCREEN = 'lead', ctx.pasteCall(A, '7865550199'), A.p.length === 2 && nA.wpm.length === 1), JSON.stringify(A.p));
T('a second, different number is added after the first', (ctx.SCREEN = 'lead', ctx.pasteCall(A, '7865550200'), A.p.length === 3 && nA.wpm.length === 2 && nA.phone === '7865550199'), JSON.stringify([A.p, nA.wpm]));
T('the team sync carries it (notes saved to this phone)', /7865550199/.test(store.fcLeadNotes || ''), Object.keys(store).join());

console.log('\n== a paste made on another device rides along ==');
ctx.notes['CASE-B'] = { wpm: ['3055558888'] };
ctx.applyPasted();
T('render adds the synced number to the row, tagged wpm', B.p.indexOf('3055558888') === 1 && B.ps[1] === 'wpm', JSON.stringify([B.p, B.ps]));
ctx.notes['CASE-B'].badph = ['3055558888']; B.p = ['3055550002']; B.ps = ['']; B.r = [''];
ctx.applyPasted();
T('but not once he marked it bad', B.p.length === 1, JSON.stringify(B.p));

if (typeof ctx.mergeNotes === 'function') {
  console.log('\n== board merge ==');
  ctx.notes = { 'CASE-M': { status: '', note: '', wpm: ['3055550011'] } };
  try { ctx.mergeNotes({ 'CASE-M': { status: '', note: '', wpm: ['3055550022', '3055550011'] } }); } catch (e) { console.log('  [mergeNotes threw: ' + String(e).slice(0, 160) + ']'); }
  const w = (ctx.notes['CASE-M'] || {}).wpm || [];
  T('mergeNotes unions pasted numbers from both devices', w.length === 2 && w.indexOf('3055550011') >= 0 && w.indexOf('3055550022') >= 0, JSON.stringify(w));
}

console.log('\n== paste an email: the board\'s email from the card, no bridge ==');
T('the email block baked into the page', ctx.MAIL_OK === true && typeof ctx._mailBodies === 'function' && typeof ctx._subjLine === 'function');
T('no build-time slot was left unfilled', !/"__ALEXMAIL_EN__"|"__OPTOUT_EN__"|__IDENT_EN__|__IDENT_ES__/.test(html));
ctx._applySealedSender({ name: 'Alejandro Gonzalez', phone: '(786) 631-1823' });   // what a real unlock does with payload.s
ctx.notes = {}; store.fcSender = JSON.stringify({ name: 'Alejandro Gonzalez', llc: 'Biscayne Solutions Group', phone: '(786) 631-1823', addr: '123 Main St, Miami, FL 33101' });
ctx.cur = A; ctx.phIdx = 0; ctx.screenLead();
T('the paste box now takes a number or an email', /NUMBER OR EMAIL ON WHITEPAGES/.test(app()) && /or email\. Pasted numbers are not checked/.test(app()));
T('_mailAddr trims and lower-cases a pasted address and refuses junk', ctx._mailAddr('  Foo.Bar@Example.COM ') === 'foo.bar@example.com' && ctx._mailAddr('305-555-0001') === '' && ctx._mailAddr('nobody@') === '');
let mc = ctx.mailCheck(A, 'owner@example.com');
T('a fresh lead with a good address composes a first touch', !mc.why && mc.stage === 'cold', JSON.stringify(mc));
ctx.notes['CASE-S'] = { status: 'DO NOT CONTACT', note: '' };
mc = ctx.mailCheck(S, 'owner@example.com');
T('an opted-out person is refused', /opted out/.test(mc.why), mc.why);
mc = ctx.mailCheck(Object.assign({}, A, { st: 'BAL' }), 'owner@example.com');
T('the investor lane is refused (its email lives on the board)', /Investor lane/.test(mc.why), mc.why);
ctx.notes['CASE-B'] = { status: '', note: '', touches: [{ d: '2026-10-01', ch: 'email', out: 'THEY REPLIED (inbound)' }] };
mc = ctx.mailCheck(B, 'owner@example.com');
T('a lead who replied is refused: answer them, no template', /already replied/.test(mc.why), mc.why);
ctx.notes['CASE-B'] = { status: '', note: '', touches: [1, 2, 3].map(i => ({ d: '2026-10-0' + i, ch: 'email', out: 'Email sent' })) };
T('three sends close the ladder', /Three emails/.test(ctx.mailCheck(B, 'owner@example.com').why));
ctx.notes['CASE-B'] = {};
T('confirmed bridge sends baked on the row seed the stage', ctx.mailStageCM(Object.assign({}, B, { ln: 2 })) === 'final' && ctx.mailStageCM(Object.assign({}, B, { ln: 3 })) === 'done');
const pauseWas = ctx.MAIL_PAUSE; ctx.MAIL_PAUSE = { rule: 'paused', why: 'rate 11.9%' };
mc = ctx.mailCheck(A, 'owner@example.com');
T('the bridge\'s first-touch bounce pause holds a cold email from the card', /First emails are paused/.test(mc.why) && /11\.9/.test(mc.why), mc.why);
T('the pause does not hold a follow-up', !ctx.mailCheck(Object.assign({}, A, { ln: 1 }), 'owner@example.com').why);
ctx.MAIL_PAUSE = pauseWas;

let M = ctx.composeMail(A, 'owner@example.com', 'en');
T('first touch, sale date: the plain subject the board and the nightly sender use', M.subj === 'Regarding your property at 1 TEST ST', M.subj);
T('the body is Alejandro\'s cold copy with the date, the case number, the identity line and the sign-off', /Hi Test,/.test(M.body) && /auction on 10\/01\/2026/.test(M.body) && /I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney\./.test(M.body) && /Alejandro Gonzalez\nBiscayne Solutions Group\nPhone: \(786\) 631-1823\n123 Main St/.test(M.body), M.body.slice(0, 300));
T('the approved opt-out line closes it', /If now's not a good time, just tell me and I won't reach out again\./.test(M.body));
T('no token or placeholder survives into the body', !/\u0001|__[A-Z_]+__|\$\{/.test(M.body));
M = ctx.composeMail(A, 'owner@example.com', 'es');
T('Spanish: the Spanish subject, greeting, identity and opt-out line', M.subj === 'Referente a su propiedad en 1 TEST ST' && /^Hola Test,/.test(M.body) && /No soy su prestamista/.test(M.body) && /Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar\./.test(M.body), M.body.slice(0, 200));
M = ctx.composeMail(Object.assign({}, A, { lp: 1, x: '09/01/2026' }), 'owner@example.com', 'en');
T('a fresh filing with no sale date gets the early body, never a blank date', M.early && /case filed at the courthouse on 1 TEST ST \(Case No\. CASE-A\)/.test(M.body) && !/auction on /.test(M.body), M.body.slice(0, 200));
M = ctx.composeMail(Object.assign({}, A, { td: 1 }), 'owner@example.com', 'en');
T('a tax deed gets the tax-deed body', /tax-deed sale date of 10\/01\/2026/.test(M.body) && /Certificate\/Case No\. CASE-A/.test(M.body), M.body.slice(0, 200));
M = ctx.composeMail(Object.assign({}, A, { ln: 1 }), 'owner@example.com', 'en');
T('the second email threads under the first and is the follow-up', M.stage === 'follow' && M.subj === 'Re: Regarding your property at 1 TEST ST' && /following up on my earlier note about 1 TEST ST/.test(M.body), M.subj);
M = ctx.composeMail(Object.assign({}, A, { pl: 'A VERY LONG PLAINTIFF NAME THAT WAS CUT AT FORTY-SIX' }), 'owner@example.com', 'en');
T('a plaintiff name the row cut short is left out rather than sent cut', !/A VERY LONG PLAINTIFF/.test(M.body));

console.log('\n== the email screen logs only after you confirm ==');
ctx.notes = {}; ctx.cur = A; ctx.phIdx = 0;
ctx.screenMail(A, 'Owner@Example.com');
T('the screen shows To, Subject, the body and both handoffs', ctx.SCREEN === 'mail' && /owner@example\.com/.test(app()) && /Regarding your property at 1 TEST ST/.test(app()) && /href="mailto:owner@example\.com\?subject=Regarding%20your%20property/.test(app()) && /mail\.google\.com\/mail\/\?view=cm/.test(app()), app().slice(0, 200));
T('nothing is written on the lead by opening the screen', !ctx.notes['CASE-A']);
documentStub.getElementById('mlgm').click();
T('tapping a handoff asks whether it went', documentStub.getElementById('mlconf').style.display === 'block' && documentStub.getElementById('mlsend').style.display === 'none');
documentStub.getElementById('mln').click();
T('"did not send" writes nothing', !ctx.notes['CASE-A'] && documentStub.getElementById('mlsend').style.display === 'block');
documentStub.getElementById('mlapp').click();
documentStub.getElementById('mly').click();
let na = ctx.notes['CASE-A'] || {};
T('confirming logs one ch:email touch with the stage and the handoff, by this caller', (na.touches || []).length === 1 && na.touches[0].ch === 'email' && /Email sent — first touch \(mail app, address pasted from Whitepages\)/.test(na.touches[0].out) && na.touches[0].to === 'owner@example.com' && na.touches[0].by === 'Alejandro', JSON.stringify(na.touches));
T('the address is saved on the lead as a pasted Whitepages email', JSON.stringify(na.wpe) === '["owner@example.com"]', JSON.stringify(na.wpe));
T('it comes back to the card', ctx.SCREEN === 'lead' && /class="card"/.test(app()));
T('the card now offers that address again, as the follow-up', /class="pcem" data-em="owner@example\.com"/.test(app()) && /follow-up #2/.test(app()), (app().match(/<div class="pcsaved">.*?<\/div>/) || [''])[0]);
T('the stage moved to follow-up', ctx.mailStageCM(A) === 'follow');
T('the strip counts the email', /<b>1<\/b> email/.test(ctx.sessStrip()), ctx.sessStrip());
let am = ctx._afterMail(A);
T('the after-call screen gets an Email button for it', /id="aem" class="ghost" data-em="owner@example\.com"/.test(am) && /follow-up #2/.test(am), am);
ctx.notes['CASE-A'].touches.push({ d: '2026-10-02', ch: 'email', out: 'Email sent' }, { d: '2026-10-03', ch: 'email', out: 'Email sent' });
T('a closed ladder removes the after-call button and says why on the card', ctx._afterMail(A) === '' && /Three emails already went/.test(ctx._wpeList(A)));
if (typeof ctx.mergeNotes === 'function') {
  ctx.notes['CASE-M'] = { status: '', note: '', wpe: ['a@example.com'] };
  try { ctx.mergeNotes({ 'CASE-M': { status: '', note: '', wpe: ['B@Example.com', 'a@example.com'] } }); } catch (e) { console.log('  [mergeNotes threw: ' + String(e).slice(0, 160) + ']'); }
  const we = (ctx.notes['CASE-M'] || {}).wpe || [];
  T('mergeNotes unions pasted emails from both devices', we.length === 2 && we.indexOf('a@example.com') >= 0 && we.indexOf('b@example.com') >= 0, JSON.stringify(we));
}

console.log('\n== keep calling: goal, streak, feedback ==');
function dkey(off){ const d = new Date(); d.setDate(d.getDate() - off); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
const td = dkey(0), y1 = dkey(1), y2 = dkey(2), y4 = dkey(4);
function dl(d, n, by){ const o = []; for (let i = 0; i < n; i++) o.push({ d, ts: 'x', tsu: 1, ph4: '000' + i, oc: 'noanswer', by: by || 'Alejandro' }); return o; }
ctx.notes = {
  'K1': { dials: dl(td, 9), touches: [{ d: td, ch: 'call', out: 'Talked', by: 'Alejandro' }] },
  'K2': { dials: dl(y1, 12).concat(dl(y2, 3)), touches: [{ d: y1, ch: 'call', out: 'APPOINTMENT', by: 'Alejandro' }] },
  'K3': { dials: dl(y4, 40) },
  'K4': { dials: dl(td, 5, 'Carlos') },
  '@abc': { dials: dl(td, 99) },
};
store.fcCaller = 'Alejandro';
let st = ctx.dayStats();
T('today counts only my dials on leads (not Carlos, not ledger rows)', st.today.n === 9 && st.today.c === 1, JSON.stringify(st.today));
T('streak counts consecutive days with a dial, ending today', st.streak === 3, st.streak);
T('best day is the biggest earlier day', st.best && st.best.n === 40, JSON.stringify(st.best));
T('default goal is 30', ctx.dialGoal() === 30);
store.fcDialGoal = '10';
T('goal is read from this phone', ctx.dialGoal() === 10);
let strip = ctx.sessStrip();
T('the strip shows dials over goal, conversations, the streak and the bar', /<b>9<\/b><span class="goal"> \/ 10<\/span> dials/.test(strip) && /1<\/b> conv/.test(strip) && /3<\/b>-day streak/.test(strip) && /width:90%/.test(strip), strip);
T('the strip names the best day to beat', /best day 40/.test(strip), strip);
let ch = ctx.cheer({ k: 'noanswer' });
T('one short of goal: says how far', /9 of 10/.test(ch.msg) && !ch.big, JSON.stringify(ch));
ctx.notes['K1'].dials.push(dl(td, 1)[0]);
ch = ctx.cheer({ k: 'noanswer' });
T('hitting the goal is a milestone and names the streak', /GOAL: 10 dials/.test(ch.msg) && /3 days in a row/.test(ch.msg) && ch.big, JSON.stringify(ch));
ch = ctx.cheer({ k: 'appt' });
T('an appointment is always the loud one', /APPOINTMENT/.test(ch.msg) && ch.big);
ctx.notes['K1'].touches = [{ d: td, ch: 'call', out: 'Talked', by: 'Alejandro' }];   // cheer runs AFTER logOutcome wrote it
ch = ctx.cheer({ k: 'talked' });
T('the first conversation of the day outranks the goal milestone', ch.big && /First real conversation/.test(ch.msg), JSON.stringify(ch));
ctx.notes = { 'K3': { dials: dl(y4, 40) } };
st = ctx.dayStats();
T('no dials today and none yesterday: streak is 0', st.streak === 0, st.streak);
ctx.notes = { 'K2': { dials: dl(y1, 2) } };
T('nothing yet today but dialed yesterday: the streak is alive at 1', ctx.dayStats().streak === 1, ctx.dayStats().streak);
T('strip with a 1-day streak prints no streak, and names yesterday as the day to beat', !/streak/.test(ctx.sessStrip()) && /best day 2/.test(ctx.sessStrip()), ctx.sessStrip());

console.log('\n================================');
console.log(pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
