/* _boardnotest.js -- run: node _boardnotest.js
 *
 * The Morning Worker follows Call Mode's 2026-09-02 no policy (board, 2026-09-26).
 * A hard no / DNC is person-wide and never re-contacted. "Not interested" is a soft no:
 * retired until one event-driven resurface, not a 720-hour cooldown. hrs:720 stays in
 * CALL_OUTCOMES so the phone and the board share one outcome row; eligibility ignores it.
 *
 * Fake case ids only. No people.
 */
const fs = require('fs');
const path = require('path');
const SRC = fs.readFileSync(path.join(__dirname, 'tracker_template.html'), 'utf8');

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

const names = ['_boardWasLp', '_boardNoState', '_workerNoBlocks'];
const missing = names.filter(n => !extract(n));
const sandbox = { notes: {}, _personCases: r => [(r && r.case) || ''] };
let fns = {};
try {
  fns = new Function('ctx', 'with (ctx) {\n' + names.map(extract).filter(Boolean).join('\n') +
    '\n; return { _boardNoState, _workerNoBlocks, _boardWasLp }; }')(sandbox);
} catch (e) {
  console.log('  (source did not evaluate: ' + e.message + ')');
}

let pass = 0, fail = 0;
function T(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (detail !== undefined ? '  [got: ' + JSON.stringify(detail) + ']' : '')); }
}
function blocks(r) {
  try { return fns._workerNoBlocks(r); }
  catch (e) { return '__threw: ' + e.message; }
}
function state(r) {
  try { return fns._boardNoState(r); }
  catch (e) { return { err: e.message }; }
}

console.log('== hard no is person-wide ==');
sandbox._personCases = () => ['2099-000601-CA-01', '2099-000602-CA-01'];
sandbox.notes = { '2099-000602-CA-01': { no: 'hard', noAt: 1 } };
T('a sibling hard no blocks this case', blocks({ case: '2099-000601-CA-01', days: 10 }) === 'hard',
  blocks({ case: '2099-000601-CA-01', days: 10 }));
sandbox._personCases = r => [r.case];
sandbox.notes = { '2099-000603-CA-01': { status: 'DO NOT CONTACT' } };
T('a DNC status is a hard block', blocks({ case: '2099-000603-CA-01', days: 5 }) === 'hard',
  state({ case: '2099-000603-CA-01', days: 5 }));

console.log('\n== Not interested is a soft no, not a 30-day cooldown ==');
sandbox.notes = { '2099-000604-CA-01': { status: 'Not interested', cooldownH: 720 } };
T('legacy Not interested with no date is retired (soft, not open)',
  blocks({ case: '2099-000604-CA-01', days: 9999, st: 'LP' }) === 'soft',
  state({ case: '2099-000604-CA-01', days: 9999, st: 'LP' }));
sandbox.notes = { '2099-000605-CA-01': { no: 'soft', noWasLp: false, resurf: 0 } };
T('soft no with a sale date is open inside T-14',
  blocks({ case: '2099-000605-CA-01', days: 10 }) === 'open',
  state({ case: '2099-000605-CA-01', days: 10 }));
sandbox.notes = { '2099-000605-CA-01': { no: 'soft', noWasLp: false, resurf: 0 } };
T('the same soft no stays retired at 40 days',
  blocks({ case: '2099-000605-CA-01', days: 40 }) === 'soft',
  state({ case: '2099-000605-CA-01', days: 40 }));
sandbox.notes = { '2099-000606-CA-01': { no: 'soft', noWasLp: false, resurf: 1 } };
T('a spent resurface stays retired at T-10',
  blocks({ case: '2099-000606-CA-01', days: 10 }) === 'soft',
  state({ case: '2099-000606-CA-01', days: 10 }));
sandbox.notes = { '2099-000607-CA-01': { no: 'soft', noWasLp: true, resurf: 0 } };
T('an LP soft no opens once a date appears',
  blocks({ case: '2099-000607-CA-01', days: 30 }) === 'open',
  state({ case: '2099-000607-CA-01', days: 30 }));

console.log('\n== the worker writes the policy, and the 720h row stays for vocabulary ==');
T('callout writes n.no = hard', SRC.includes("n.no = 'hard'"));
T('callout writes n.no = soft', SRC.includes("n.no = 'soft'"));
T('_workerEligible consults _workerNoBlocks',
  /function _workerEligible\(r\)\{[\s\S]*?_workerNoBlocks\(r\)/.test(SRC));
T('notint keeps hrs:720', SRC.includes('hrs:720'));
T('text hold is separate from the email bridge hold',
  SRC.includes('TEXT_HOLD=(j&&j.text_hold)') && SRC.includes('BRIDGE_HOLD=(j&&j.optout_stale)'));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
if (missing.length) console.log('missing from source: ' + missing.join(', '));
process.exit(fail || missing.length ? 1 : 0);
