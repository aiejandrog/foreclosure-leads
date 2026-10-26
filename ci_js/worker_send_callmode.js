// Fake-data regression: the Morning Worker's "Send to Call Mode" button (2026-10-26).
//   node ci_js/worker_send_callmode.js <repo>/tracker_template.html
// Runs the REAL _cmSet / sendToCallMode / post extracted from genMorningWorker with a fake board
// (window.opener) and a fake button. Pass = every phone-only lead (Call list, text batch, lane cards
// with a phone and no sendable email) gets exactly one "callq" mark with hand:1 and its own case,
// email-sendable and phoneless cards get none, and a closed board tab is reported, not hidden.
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
let fails = 0;
const ok = (c, m) => { console.log((c ? 'ok   ' : 'FAIL ') + m); if (!c) fails++; };
ok(src.includes('id="mwsendcm"'), 'button is on the worker page');
ok(/closest\("#mwsendcm"\)[\s\S]{0,80}sendToCallMode\(scm\)/.test(src), 'click delegate wires the button');
const code = ['var CMBUSY=false;', fn('_cmSet'), fn('sendToCallMode'), fn('post')].join('\n');

function run(closed) {
  const marks = [], logs = [];
  const el = () => ({ style: {}, classList: { add() {}, remove() {} }, textContent: '', innerHTML: '', appendChild() {} });
  const lbl = el(), fill = el(), msg = el();
  const btn = Object.assign(el(), { querySelector: s => (s === '.lbl' ? lbl : fill) });
  const env = {
    CALLQ: [{ c: 'FAKE-1' }, { c: 'FAKE-2' }],
    TEXTQ: [{ c: 'FAKE-2' }, { c: 'FAKE-3' }],
    LANES: { urgent: [{ c: 'FAKE-4', phones: ['3055550104'], mailTo: '' },          // phone only
                      { c: 'FAKE-5', phones: ['3055550105'], mailTo: 'x@fake.invalid' }, // email-sendable
                      { c: 'FAKE-6', phones: [], mailTo: '' }],                          // no phone
             active: [{ c: 'FAKE-1', phones: ['3055550101'], mailTo: '' }] },
    Q: [], i: 0, MSEQ: 0, LKCASES: ['FAKE-7', 'FAKE-1'],
    window: { opener: closed ? { closed: true } : { closed: false, postMessage: m => marks.push(m.workerAct) } },
    document: { getElementById: () => msg, createElement: () => ({ remove() {} }) },
    setTimeout: f => f(), _pendWatch() {}, _markBuf() {}, addLog: (k, w, t) => logs.push(t),
  };
  new Function('__env', '__btn', 'with(__env){' + code + '\nsendToCallMode(__btn);}')(env, btn);
  return { marks, logs, lbl, msg };
}
const r = run(false);
const cs = r.marks.map(m => m.c).sort();
ok(JSON.stringify(cs) === JSON.stringify(['FAKE-1', 'FAKE-2', 'FAKE-3', 'FAKE-4', 'FAKE-7']), 'one mark per phone-only and no-number lead, no duplicates: ' + cs);
ok(r.marks.every(m => m.k === 'callq' && m.extra && m.extra.hand === 1 && m.extra.qc === m.c), 'every mark is a callq hand-off on its own case');
ok(!cs.includes('FAKE-5') && !cs.includes('FAKE-6'), 'email-sendable cards, and lane cards outside the lookup panel, are not sent');
ok(/5 sent to Call Mode/.test(r.lbl.textContent), 'button reports the count: ' + r.lbl.textContent);
ok(/5 leads handed to Call Mode<\/b> \(4 to call, 1 to look up/.test(r.msg.innerHTML), 'confirmation names the count, to call vs to look up');
const c = run(true);
ok(c.marks.length === 0 && /0 of 5 reached the board/.test(c.msg.innerHTML), 'a closed board tab is reported, not shown as sent');
console.log(fails ? 'FAIL' : 'PASS');
process.exit(fails ? 1 : 0);
