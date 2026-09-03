/* _cm_notint_spec.js -- run: node _cm_notint_spec.js
 *
 * ACCEPTANCE GATE for Alejandro's final "no" policy (decided 2026-09-02, SPLIT IT).
 * Written test-first ON PURPOSE: the retire+resurface logic exists NOWHERE yet, so this file
 * is RED today. That red is the spec. When it goes green against source, the policy is built;
 * when _cm_teamtest.js (built page) also goes green, it is live on the phone.
 *
 * THE POLICY, verbatim from the decision:
 *   (a) HARD NO / "stop calling"  -> permanent DNC, person-keyed, checked at queue build AND
 *       dial time. Never resurfaces. Its own outcome button (so a rep stops burying hard nos
 *       under "not interested"), and it behaves like `dnc` in the after-call panel (skips it).
 *   (b) SOFT NO / "we're set"      -> retired from the automatic rotation. NOT a calendar
 *       cooldown -- the 30-day window DIES. Eligible for exactly ONE deliberate resurface,
 *       EVENT-driven:
 *         - lead HAD a sale date at soft-no time -> resurface at T-14 before that auction;
 *         - lead was LP (no date) at soft-no time -> resurface when a sale date APPEARS
 *           (this branch is why the feature is not dead for 1,115/1,471 rows).
 *       After the one resurface is spent, any further no of either kind = permanent.
 *
 * OBSERVABLE CONTRACT: everything is asserted through suppressed(r) -- the single queue-build
 * predicate the whole page already routes through -- returning truthy (hidden) / falsy (dialable).
 * The dial-time hard-no gate is asserted through hardSuppressed(r).
 *
 * STATE SCHEMA (proposed by this session; DEALFLOW owns call_mode.py, so if they represent it
 * differently they update this file IN THE SAME COMMIT -- the test and the code ship together
 * or the gate is fiction). Fields on notes[caseId]:
 *   n.no      = 'hard' | 'soft'      -- which button was pressed
 *   n.noAt    = epoch ms of the no
 *   n.noWasLp = true if the lead had NO sale date when the soft-no was logged
 *   n.resurf  = count of resurfaces already spent (0/undefined = one still available)
 * Row fields (already built by call_rows): r.d days-to-auction (9999 = none), r.lp, r.c, r.pcs.
 */
const fs = require('fs');
const path = require('path');
const SRC = fs.readFileSync(path.join(__dirname, 'call_mode.py'), 'utf8');

function extract(name) {
  const i = SRC.indexOf('function ' + name + '(');
  if (i < 0) return null;
  let depth = 0, j = SRC.indexOf('{', i);
  for (let k = j; k < SRC.length; k++) {
    if (SRC[k] === '{') depth++;
    else if (SRC[k] === '}') { depth--; if (depth === 0) return SRC.slice(i, k + 1); }
  }
  return null;
}

const NOW = Date.parse('2026-09-02T20:00:00Z');
const sandbox = {
  notes: {}, Math, String, Number, JSON,
  Date: class extends Date { constructor(...a){ super(...(a.length ? a : [NOW])); } static now(){ return NOW; } },
  optPhones: () => ({}), caller: () => 'Alejandro', hardSuppressed: null,
};

const names = ['agoTxt', 'lastCall', 'hardSuppressed', 'suppressed'];
const missing = names.filter(n => !extract(n));
const body = names.map(extract).filter(Boolean).join('\n');
let fns = {};
try {
  fns = new Function('ctx', 'with (ctx) {' + body +
    '; return { suppressed: typeof suppressed==="function"?suppressed:null,' +
    ' hardSuppressed: typeof hardSuppressed==="function"?hardSuppressed:null }; }')(sandbox);
} catch (e) { console.log('  (source did not evaluate: ' + e.message + ')'); }
if (sandbox.hardSuppressed === null && fns.hardSuppressed) sandbox.hardSuppressed = fns.hardSuppressed;

let pass = 0, fail = 0;
function T(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (detail !== undefined ? '  [got: ' + JSON.stringify(detail) + ']' : '')); }
}
const sup = r => { try { return fns.suppressed ? fns.suppressed(r) : '__no suppressed()__'; } catch (e) { return '__threw: ' + e.message + '__'; } };
const hard = r => { try { return fns.hardSuppressed ? fns.hardSuppressed(r) : '__no hardSuppressed()__'; } catch (e) { return '__threw__'; } };
const day = 86400000;

console.log('== HARD NO: permanent, never resurfaces, dial-time checked ==');
sandbox.notes = { 'H': { no: 'hard', noAt: NOW - 40 * day,
  touches: [{ ch: 'call', out: 'Stop calling', tsu: NOW - 40 * day, by: 'Alejandro' }] } };
T('hard no hidden from queue even 40 days later', !!sup({ c: 'H', d: 9999, p: [] }), sup({ c: 'H', d: 9999, p: [] }));
T('hard no hidden even at T-1 before a sale', !!sup({ c: 'H', d: 1, p: [] }), sup({ c: 'H', d: 1, p: [] }));
T('hard no blocked at DIAL time (hardSuppressed)', !!hard({ c: 'H', d: 1, p: [] }), hard({ c: 'H', d: 1, p: [] }));

console.log('\n== SOFT NO with a sale date: retired, resurfaces ONCE at T-14 ==');
const soft = () => ({ 'S': { no: 'soft', noAt: NOW - 3 * day, noWasLp: false, resurf: 0,
  touches: [{ ch: 'call', out: 'Not interested', tsu: NOW - 3 * day, by: 'Alejandro' }] } });
sandbox.notes = soft();
T('retired while sale is 40 days out (> T-14)', !!sup({ c: 'S', d: 40, p: [] }), sup({ c: 'S', d: 40, p: [] }));
sandbox.notes = soft();
T('RESURFACES inside T-14 (dialable)', !sup({ c: 'S', d: 10, p: [] }), sup({ c: 'S', d: 10, p: [] }));

console.log('\n== SOFT NO on an LP lead: resurfaces when a date APPEARS, not on a calendar ==');
const softLp = () => ({ 'L': { no: 'soft', noAt: NOW - 20 * day, noWasLp: true, resurf: 0,
  touches: [{ ch: 'call', out: 'Not interested', tsu: NOW - 20 * day, by: 'Alejandro' }] } });
sandbox.notes = softLp();
T('LP soft-no stays retired while still no date (d=9999)', !!sup({ c: 'L', d: 9999, lp: 1, p: [] }), sup({ c: 'L', d: 9999, lp: 1, p: [] }));
sandbox.notes = softLp();
T('LP soft-no RESURFACES once a sale date lands (even 30 days out)', !sup({ c: 'L', d: 30, lp: 0, p: [] }), sup({ c: 'L', d: 30, lp: 0, p: [] }));

console.log('\n== The ONE resurface is spent: second no is permanent ==');
sandbox.notes = { 'S2': { no: 'soft', noAt: NOW - 1 * day, noWasLp: false, resurf: 1,
  touches: [{ ch: 'call', out: 'Not interested', tsu: NOW - 1 * day, by: 'Alejandro' }] } };
T('already-resurfaced soft-no stays retired at T-10', !!sup({ c: 'S2', d: 10, p: [] }), sup({ c: 'S2', d: 10, p: [] }));

console.log('\n== 30-DAY CALENDAR COOLDOWN IS DEAD: a plain old notint must NOT auto-return at day 31 ==');
sandbox.notes = { 'OLD': { status: 'Not interested', cooldownH: 720,
  touches: [{ ch: 'call', out: 'Not interested', tsu: NOW - 31 * day, by: 'Alejandro' }] } };
T('legacy notint at day 31 is NOT silently re-served by a calendar window',
  !!sup({ c: 'OLD', d: 9999, p: [] }), sup({ c: 'OLD', d: 9999, p: [] }));

console.log('\n================================');
if (missing.length) console.log('missing from source: ' + missing.join(', '));
console.log(pass + ' passed, ' + fail + ' failed' +
  (fail ? '  << RED: final-policy not built yet (expected until DEALFLOW lands it)' : '  << GREEN: policy satisfied in source'));
process.exit(fail ? 1 : 0);
