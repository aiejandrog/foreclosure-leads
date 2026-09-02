/* _cm_sourcecheck.js -- run: node _cm_sourcecheck.js
 *
 * The GREEN half of the red/green pair for the no-means-no guards, runnable BEFORE a rebuild.
 *
 * _cm_teamtest.js tests the BUILT page -- the truth a phone actually loads -- and on 2026-09-02
 * it correctly went RED on the sibling-walk and retro-floor scenarios because the page (built
 * 17:20) predated the source that added them (saved 17:26). That red is the proof the harness
 * detects absence. This file supplies the matching green without touching the DEALFLOW
 * session's live WIP: it extracts suppressed()/lastCall()/agoTxt() by name STRAIGHT OUT OF
 * call_mode.py's page template and executes them in isolation. Same code, same scenarios,
 * source edition.
 *
 * If THIS file is green and _cm_teamtest.js is red on the same scenarios, the fix exists but
 * has not shipped -- rebuild. If both are red, the fix is actually wrong. If both are green,
 * the phone has it. The pair distinguishes the three states one test alone cannot.
 */
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, 'call_mode.py'), 'utf8');

/* Extract `function NAME(...)  {...}` by balanced-brace scan. The functions live inside the
 * Python triple-quoted page template, but they are plain JS text -- no parsing needed beyond
 * brace counting (none of the targets contain unbalanced braces in strings). */
function extract(name) {
  const i = SRC.indexOf('function ' + name + '(');
  if (i < 0) throw new Error('not found in source: ' + name);
  let depth = 0, j = SRC.indexOf('{', i);
  for (let k = j; k < SRC.length; k++) {
    if (SRC[k] === '{') depth++;
    else if (SRC[k] === '}') { depth--; if (depth === 0) return SRC.slice(i, k + 1); }
  }
  throw new Error('unbalanced braces extracting ' + name);
}

const NOW = Date.now();
const sandbox = {
  notes: {},
  Date, Math, String, Number, JSON,
  // suppressed()'s dependencies that are page-state, not logic under test:
  optPhones: () => ({}),
  caller: () => 'Alejandro',
  hardSuppressed: () => '',            // isolate the QUEUE cooldown/sibling logic specifically
};
const body = ['agoTxt', 'lastCall', 'suppressed'].map(extract).join('\n');
const run = new Function('ctx', 'with (ctx) { ' + body + '; return { suppressed: suppressed }; }');
const fns = run(sandbox);

let pass = 0, fail = 0;
function T(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (detail ? '  [' + detail + ']' : '')); }
}

const A = { c: 'CASE-A', pcs: ['CASE-A', 'CASE-B'], p: [] };
const B = { c: 'CASE-B', pcs: ['CASE-A', 'CASE-B'], p: [] };
const C = { c: 'CASE-C', pcs: null, p: [] };

console.log('== source-level: fresh NO suppresses the person, both case rows ==');
sandbox.notes = { 'CASE-A': { status: 'Not interested', cooldownH: 720,
  touches: [{ ch: 'call', out: 'Not interested', tsu: NOW - 3600000, by: 'Alejandro' }] } };
T('case with the NO is suppressed', !!fns.suppressed(A), fns.suppressed(A));
T('SIBLING case of the same person is suppressed', !!fns.suppressed(B), fns.suppressed(B) || '(sibling walk absent)');

console.log('\n== source-level: retroactive floor on an OLD 72h note ==');
sandbox.notes = { 'CASE-C': { status: 'Not interested', cooldownH: 72,
  touches: [{ ch: 'call', out: 'Not interested', tsu: NOW - 5 * 86400000, by: 'Alejandro' }] } };
T('5-day-old NO with stale 72h cooldown still suppressed (720 floor)', !!fns.suppressed(C), fns.suppressed(C) || '(no floor)');

console.log('\n== source-level fail-capability: a no-answer 30h ago with 24h cooldown must be DUE ==');
sandbox.notes = { 'CASE-C': { cooldownH: 24,
  touches: [{ ch: 'call', out: 'No answer', tsu: NOW - 30 * 3600000, by: 'Alejandro' }] } };
T('expired ordinary cooldown does NOT suppress (proves the test can go red)', !fns.suppressed(C), fns.suppressed(C));

console.log('\n================================');
console.log(pass + ' passed, ' + fail + ' failed' + (fail ? '  << source guards broken' : '  << GREEN: guards correct in source'));
