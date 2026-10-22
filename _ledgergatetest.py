"""The send ledger records WHICH evidence admitted each send.

WHY THIS EXISTS
`_recipient_verdict` admits a first touch on one of three codes (send_server.GATE_PASS):

    delivered         observed: this system mailed the address and it never bounced
    replied           observed: the owner wrote to us from it
    zerobounce_valid  asserted: a paid API said 'valid' and nothing more

Those are not equivalent evidence -- the module's own note records ZeroBounce valid running a 56%
false-valid rate on this list (63 of 113 hard-bounced, 2026-08-26) -- but the ledger did not say
which one let a send out. Reconstructing the 2026-10-21 wave (43 sends, 42 of them to addresses
this system had never mailed, 5 hard bounces) required inferring the gate from per-address
first-send dates. Inference is fine for a post-mortem and useless for metering: any quota that
treats asserted evidence differently from observed evidence has to COUNT it, and it cannot count a
field that was never written.

So this pins the writer, not a behaviour: both _append_ledger() call sites in the /send handler
must record `gate`, and the value must come from `_gate` -- the dict the deliverability gate
already builds for the address it actually mailed.

FAILS ON MAIN: 'the success ledger row records a gate field' and 'the error ledger row records a
gate field' -- on main neither _append_ledger call has a `gate` key at all, so both assertions
fail. The verdict-mapping checks below pass on main too; they are here to prove the value being
recorded is the real gate code rather than a constant.

Pure static + pure-function checks: no server, no socket, no SMTP, no ledger file is read or
written, nothing is sent, invented addresses only.
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import send_server as S   # noqa: E402

ok, bad = [], []


def rec(name, cond, detail=''):
    (ok if cond else bad).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)) if detail else ''))


# ---- 1. the writer records the field -------------------------------------------------------
tree = ast.parse(io.open(os.path.join(HERE, 'send_server.py'), encoding='utf-8').read())
writes = []
for node in ast.walk(tree):
    if (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == '_append_ledger'
            and node.args and isinstance(node.args[0], ast.Dict)):
        keys = [k.value for k in node.args[0].keys if isinstance(k, ast.Constant)]
        writes.append((keys, ast.dump(node.args[0])))

rec('both /send ledger writes were found', len(writes) == 2, '%d found' % len(writes))

success = [w for w in writes if 'message_id' in w[0]]
errored = [w for w in writes if 'error' in w[0]]
rec('one success row and one error row', len(success) == 1 and len(errored) == 1,
    '%d success / %d error' % (len(success), len(errored)))

if success:
    rec('the success ledger row records a gate field', 'gate' in success[0][0], sorted(success[0][0]))
    rec('the gate value is read from the deliverability gate, not hardcoded',
        "'_gate'" in success[0][1] or '_gate' in success[0][1])
    rec('the success row also records how many addresses were dropped',
        'gate_dropped' in success[0][0])
if errored:
    rec('the error ledger row records a gate field', 'gate' in errored[0][0], sorted(errored[0][0]))
    rec('the error row reads it from the same gate dict', '_gate' in errored[0][1])

# ---- 2. the value recorded is a real gate code ---------------------------------------------
# _recipient_verdict is pure given its evidence dict, so drive it directly with invented data.
def ev(bounced=(), ver=None, replied=(), proven=(), last_mailed=None):
    return {'bounced': set(bounced), 'ver': dict(ver or {}), 'replied': set(replied),
            'proven': set(proven), 'last_mailed': dict(last_mailed or {})}


A = 'nobody.invented@example.invalid'
B = 'other.invented@example.invalid'

code, _ = S._recipient_verdict(A, ev(proven=(A,)))
rec("observed acceptance reads 'delivered'", code == 'delivered', code)

code, _ = S._recipient_verdict(A, ev(replied=(A,)))
rec("a reply reads 'replied'", code == 'replied', code)

code, _ = S._recipient_verdict(A, ev(ver={A: {'v': 'ok', 'why': 'zerobounce:valid'}}))
rec("a paid valid reads 'zerobounce_valid', NOT 'delivered'", code == 'zerobounce_valid', code)

code, _ = S._recipient_verdict(A, ev(bounced=(A,)))
rec("a prior hard bounce reads 'dead' and cannot be admitted",
    code == 'dead' and code not in S.GATE_PASS, code)

code, _ = S._recipient_verdict(A, ev())
rec('no evidence at all is not admitted', code not in S.GATE_PASS, code)

rec('all three admitting codes are distinguishable in the ledger',
    len(set(S.GATE_PASS)) == 3 and 'zerobounce_valid' in S.GATE_PASS, S.GATE_PASS)

# ---- 3. _pick_recipient hands back the code for the address it picked -----------------------
e = ev(proven=(B,), ver={A: {'v': 'risky', 'why': 'invented'}})
pick, verdicts = S._pick_recipient([A, B], e)
picked_code = next((c for a, c, _w in verdicts if a == pick), '')
rec('the picked address carries its own verdict (what the ledger stores)',
    pick == B and picked_code == 'delivered', '%s / %s' % (pick, picked_code))

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
