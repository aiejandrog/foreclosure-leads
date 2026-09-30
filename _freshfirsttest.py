"""Call Mode puts never-called leads ahead of retries (2026-09-28).

Alejandro, 09-28: "i have the same old people on my dealflow call mode list". A no-answer came back
after its 24h cooldown at its old rank, above every lead nobody had dialled. pool() now ends in
_freshFirst(): never-called first, retries after, each group in rank order; on Fresh filings the
never-called group is newest filing first. advance() must not skip past the lead that slid up.

The JS is extracted by name from call_mode.py and run under node. Order only: nothing here adds or
removes a lead, and supReason() and the suppression surface are untouched.
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


FNS = ''.join(grab_fn(n) for n in ('lastCall', '_filedMs', '_freshFirst', '_navPush', 'advance'))
FNS = 'var _NAVB=[], _NAVF=[], lane=\'soon\';\n' + FNS
rec('pool() returns through _freshFirst', 'return _freshFirst(keep, lane);' in SRC)

HARNESS = FNS + r"""
var notes = {
  B: {touches: [{ch: 'call', ts: '2026-09-27T10:00:00', out: 'noanswer'}]},
  D: {dials: [{tsu: 1758000000000, oc: 'voicemail'}]},
  E: {touches: [{ch: 'email', ts: '2026-09-27T10:00:00'}]}      // emailed, never called
};
var rows = [{c:'A', x:'9/1/2026'}, {c:'B', x:'9/20/2026'}, {c:'C', x:'8/15/2026'},
            {c:'D', x:'9/25/2026'}, {c:'E', x:'9/22/2026'}];
var out = {};
out.soon = _freshFirst(rows, 'soon').map(function(r){ return r.c; }).join('');
out.lp = _freshFirst(rows, 'lp').map(function(r){ return r.c; }).join('');
out.same = _freshFirst(rows, 'soon').length === rows.length;
out.nomut = rows.map(function(r){ return r.c; }).join('');
// advance(): the lead at slot 0 (A) was just dialled and dropped behind the never-called ones.
var SCREEN = 'lead', cur = null, i = 0, P = null, rendered = 0;
function render(){ rendered++; }
function pool(){ return P; }
notes.A = {touches: [{ch: 'call', ts: '2026-09-28T10:00:00', out: 'noanswer'}]};
P = _freshFirst(rows, 'soon');
advance('A');
out.adv_moved = i;                       // C slid into slot 0; must stay 0, not jump to the end
i = 0; P = [{c:'X'}, {c:'Y'}]; advance('X'); out.adv_plain = i;   // not called: step to next
console.log(JSON.stringify(out));
"""
rc, so, se = node(HARNESS)
rec('harness runs', rc == 0, se)
res = json.loads(so.strip().splitlines()[-1]) if rc == 0 else {}
rec('never-called first, retries after, rank order kept in each group', res.get('soon') == 'ACEBD', res)
rec('an emailed-but-never-called lead counts as never called', res.get('soon', '').index('E') < res.get('soon', 'B').index('B') if res.get('soon') else False, res)
rec('Fresh filings: never-called newest filing first, then retries', res.get('lp') == 'EACBD', res)
rec('nothing added or dropped', res.get('same') is True, res)
rec('input list not mutated', res.get('nomut') == 'ABCDE', res)
rec('advance() stays on the slot the next lead slid into', res.get('adv_moved') == 0, res)
rec('advance() still steps forward when nothing moved', res.get('adv_plain') == 1, res)

print('\n%s (%d failed)' % ('ALL PASS' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
