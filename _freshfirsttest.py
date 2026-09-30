"""Call Mode puts never-contacted leads ahead of retries (2026-09-28, widened 2026-09-30).

Alejandro, 09-28: "i have the same old people on my dealflow call mode list". A no-answer came back
after its 24h cooldown at its old rank, above every lead nobody had dialled. pool() now ends in
_freshFirst(): never-called first, retries after, each group in rank order; on Fresh filings the
never-called group is newest filing first. advance() must not skip past the lead that slid up.

The JS is extracted by name from call_mode.py and run under node. Order only: nothing here adds or
removes a lead, and supReason() and the suppression surface are untouched.

2026-09-30: "every lead i see there ive contacted already". Three groups now: untouched on every
channel, reached another way (notes email/text touches or the baked server ledgers r.le / r.lt from
stamp_ledger), then called.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:200]) if extra else ''))
    if not ok:
        fails.append(name)


def grab_fn(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}\n', i)
    rec('function present: ' + name, i >= 0 and j > 0)
    return SRC[i:j + 2] if i >= 0 and j > 0 else ''


def node(js):
    fd, path = tempfile.mkstemp(suffix='.js')
    os.close(fd)
    io.open(path, 'w', encoding='utf-8').write(js)
    try:
        p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.remove(path)


FNS = 'var OUTREACH_CH = {call:1, text:1, email:1, letter:1, door:1};\n' + ''.join(grab_fn(n) for n in (
    'lastCall', '_inbound', 'lastOutreach', '_filedMs', '_contactTier', '_freshFirst', 'advance'))
rec('pool() orders through _freshFirst', 'var _ff = _freshFirst(keep, lane);' in SRC and 'return _ff;' in SRC)

HARNESS = FNS + r"""
var notes = {
  A: {touches: [{ch: 'email', ts: '2026-09-27T10:00:00'}]},      // emailed from the board, never called
  B: {touches: [{ch: 'call', ts: '2026-09-27T10:00:00', out: 'noanswer'}]},
  C: {},
  E: {touches: [{ch: 'text', ts: '2026-09-27T10:00:00', out: 'THEY REPLIED (inbound)'}]}  // inbound only
};
var rows = [{c:'A', x:'9/1/2026'}, {c:'B', x:'9/20/2026'}, {c:'C', x:'8/15/2026'},
            {c:'D', x:'9/25/2026', le: 1758000000000}, {c:'E', x:'9/22/2026'}];
var out = {};
out.soon = _freshFirst(rows, 'soon').map(function(r){ return r.c; }).join('');
out.lp = _freshFirst(rows, 'lp').map(function(r){ return r.c; }).join('');
out.same = _freshFirst(rows, 'soon').length === rows.length;
out.nomut = rows.map(function(r){ return r.c; }).join('');
// advance(): the lead at slot 0 (A) was just dialled and dropped behind the never-called ones.
var SCREEN = 'lead', cur = null, i = 0, P = null, rendered = 0;
function render(){ rendered++; }
function pool(){ return P; }
notes.C = {touches: [{ch: 'call', ts: '2026-09-28T10:00:00', out: 'noanswer'}]};
P = _freshFirst(rows, 'soon');
advance('C');
out.adv_moved = i;                       // E slid into slot 0; must stay 0, not jump to the end
i = 0; P = [{c:'X'}, {c:'Y'}]; advance('X'); out.adv_plain = i;   // not called: step to next
console.log(JSON.stringify(out));
"""
rc, so, se = node(HARNESS)
rec('harness runs', rc == 0, se)
res = json.loads(so.strip().splitlines()[-1]) if rc == 0 else {}
rec('untouched first, reached-another-way next, called last, rank order kept in each group',
    res.get('soon') == 'CEADB', res)
rec('an inbound reply is not outreach: E stays in the untouched group', res.get('soon', '')[:2] == 'CE', res)
rec('a server-ledger email (r.le) counts as contacted', res.get('soon', '').index('D') > 1 if res.get('soon') else False, res)
rec('Fresh filings: untouched newest filing first, then the rest', res.get('lp') == 'ECADB', res)
rec('nothing added or dropped', res.get('same') is True, res)
rec('input list not mutated', res.get('nomut') == 'ABCDE', res)
rec('advance() stays on the slot the next lead slid into', res.get('adv_moved') == 0, res)
rec('advance() still steps forward when nothing moved', res.get('adv_plain') == 1, res)

# stamp_ledger (python half): newest confirmed send per case, walks sibling cases, stamps nothing else.
sys.path.insert(0, HERE)
_ns = {}
_i = SRC.find('def stamp_ledger(')
exec(SRC[_i:SRC.find('\ndef ', _i + 10)], _ns)
_rows = [{'c': 'A'}, {'c': 'B', 'pcs': ['Z']}, {'c': 'C'}]
_n = _ns['stamp_ledger'](_rows, {'A': {'n': 2, 'last': 5}, 'Z': {'n': 1, 'last': 9}}, {'A': {'n': 1, 'last': 7}})
rec('stamp_ledger stamps le/lt, walks pcs, counts stamped rows',
    _rows == [{'c': 'A', 'le': 5, 'lt': 7}, {'c': 'B', 'pcs': ['Z'], 'le': 9}, {'c': 'C'}] and _n == 2, _rows)
rec('stamp_ledger ignores a text composer opened but never sent',
    _ns['stamp_ledger']([{'c': 'O'}], {}, {'O': {'n': 0, 'opens': 1, 'last': 8}}) == 0)
rec('stamp_ledger tolerates missing ledgers', _ns['stamp_ledger']([{'c': 'Q'}], None, None) == 0)
_sr = SRC[SRC.find('function supReason('):SRC.find('\n}\n', SRC.find('function supReason('))]
_su = SRC[SRC.find('function suppressed('):SRC.find('\n}\n', SRC.find('function suppressed('))]
rec('hiding rules never read the server ledger fields', not any(t in _sr + _su for t in ('r.le', 'r.lt', '_contactTier')))
rec('build wires stamp_ledger', 'call_mode.stamp_ledger(_cm_all[0], _mlog, _tlog)' in
    io.open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read())

print('\n%s (%d failed)' % ('ALL PASS' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
