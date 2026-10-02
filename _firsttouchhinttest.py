"""stamp_first_touch_hint (foreclosure_leads.py): the Morning Worker's first-touch hint.
Runs with no data files. Evidence is stubbed to one unrelated verified address so the stamp runs;
with no evidence at all it must stamp nothing (that is not the sending machine)."""
import sys
import foreclosure_leads as F
import send_server as S

ok, bad = [], []
def rec(n, c, d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))

_real_ev = S._deliverability_evidence
def _ev():
    e = _real_ev(); e['ver'] = dict(e['ver'], **{'other@gmail.com': {'v': 'ok', 'why': 'zerobounce:valid'}}); return e

s0 = [{'case': 'A', 'emails': ['a@gmail.com']}]
rec('no evidence on this machine: nothing stamped', F.stamp_first_touch_hint(s0) == 0 and 'fth' not in s0[0])
S._deliverability_evidence = _ev

def rows():
    return [{'case': 'A', 'emails': ['a@gmail.com']}, {'case': 'B', 'emails': []},
            {'case': 'C', 'emails': ['x@weird.example']}, {'case': 'D', 'emails': ['d@gmail.com'], 'fth': 1}]

s = rows(); F.stamp_first_touch_hint(s)
rec('unverified mailable leads are held', [d.get('fth') for d in s] == [1, None, None, 1], [d.get('fth') for d in s])
rec('a lead with no worker-mailable address is not stamped', 'fth' not in s[2])

orig = S._recipient_verdict
S._recipient_verdict = lambda a, ev: ('zerobounce_valid', '') if a == 'a@gmail.com' else orig(a, ev)
s = rows(); F.stamp_first_touch_hint(s)
rec('an address the gate passes clears the hint', 'fth' not in s[0])
S._recipient_verdict = orig

# a case mailed before, at an address since replaced: /send judges only the current addresses
S._recipient_verdict = lambda a, ev: ('awaiting_delivery', '') if a == 'd@gmail.com' else orig(a, ev)
s = rows(); F.stamp_first_touch_hint(s)
rec('a previously mailed case is judged on its current addresses (held when none pass)', s[3].get('fth') == 1)
S._recipient_verdict = lambda a, ev: ('delivered', '') if a == 'd@gmail.com' else orig(a, ev)
s = rows(); F.stamp_first_touch_hint(s)
rec('a current address with delivery evidence clears the hint, stale stamp removed', 'fth' not in s[3])
S._recipient_verdict = orig

od = S._deliverability_evidence
S._deliverability_evidence = lambda: (_ for _ in ()).throw(RuntimeError('boom'))
s = rows(); n = F.stamp_first_touch_hint(s)
rec('send_server failing: nothing stamped, worker unfiltered', n == 0 and all('fth' not in d for d in s))
S._deliverability_evidence = od
S._deliverability_evidence = _real_ev

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
