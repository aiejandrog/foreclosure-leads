"""send_server._merge_note: the after-the-yes save (2026-10-29). LEGAL REVIEW (send_server.py).

A newer after-yes save (afterYesAt: nextStep, stage, equityVerified, keepOrSell, stageAt, next) replaces
an older one as a unit; an older push never walks a newer one back; DO NOT CONTACT / Dead is never
overridden and is never handed a next step. Notes with no afterYesAt merge exactly as before.
Invented data only. Run: DEALFLOW_DIR=<temp> python _mergenotetest.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ['DEALFLOW_DIR'] = tempfile.mkdtemp(prefix='mergenote_')

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


print('\nsend_server._merge_note')
import send_server as S  # noqa: E402

M = S._merge_note
T0 = 1000


def note(status, ay=0, ns=None, **k):
    n = {'status': status}
    if ay:
        n['afterYesAt'] = ay
    if ns:
        n['nextStep'] = {'date': ns, 'what': 'w' + ns}
        n['next'] = ns
    n.update(k)
    return n


out, _ = M(note('Appointment', 100, '2026-11-01', keepOrSell='sell'),
           note('Offer made', 200, '2026-11-09', equityVerified={'ok': True, 'source': 's', 'd': '2026-11-01'},
                stageAt={'Offer made': '2026-11-05'}))
rec('newer after-yes save replaces nextStep (a LATER date too), stage, equityVerified, next',
    out['nextStep']['date'] == '2026-11-09' and out['next'] == '2026-11-09' and out['status'] == 'Offer made'
    and out['equityVerified']['ok'] is True and out['afterYesAt'] == 200, out)
rec('keepOrSell not in the newer save is dropped with the unit', 'keepOrSell' not in out, out)
out, _ = M(note('Offer made', 200, '2026-11-09'), note('Appointment', 100, '2026-11-01'))
rec('an older push never walks the stored stage or next step back',
    out['status'] == 'Offer made' and out['nextStep']['date'] == '2026-11-09' and out['afterYesAt'] == 200, out)
out, _ = M(note('Appointment', 100, '2026-11-01', stageAt={'Appointment': '2026-10-20'}),
           note('Offer made', 200, '2026-11-09', stageAt={'Offer made': '2026-11-05', 'Appointment': '2026-10-25'}))
rec('stageAt keeps the earliest date per stage', out['stageAt'] == {'Appointment': '2026-10-20', 'Offer made': '2026-11-05'}, out)
out, _ = M(note('DO NOT CONTACT', 100), note('Offer made', 999, '2026-11-09'))
rec('stored DO NOT CONTACT is never overridden and gets no next step',
    out['status'] == 'DO NOT CONTACT' and 'nextStep' not in out, out)
out, _ = M(note('Appointment', 100, '2026-11-01'), note('DO NOT CONTACT', 50))
rec('an incoming DO NOT CONTACT wins', out['status'] == 'DO NOT CONTACT', out)
out, _ = M(note('Dead', 100), note('Assigned', 999, '2026-11-09'))
rec('Dead is never overridden', out['status'] == 'Dead' and 'nextStep' not in out, out)
out, _ = M(note('Appointment', 100, '2026-11-01'), note('Dead', 50))
rec('an incoming Dead wins over an older stage', out['status'] == 'Dead', out)
out, _ = M({'status': 'Contacted', 'note': 'a'}, {'status': 'Contacted', 'note': 'b'})
rec('notes with no after-yes fields merge exactly as before (fill-only)', out['note'] == 'a' and 'afterYesAt' not in out, out)
out, _ = M(note('Appointment', 0, '2026-11-01'), note('Under contract', 5, '2026-11-20'))
rec('a note that never had afterYesAt takes the first one', out['nextStep']['date'] == '2026-11-20' and out['status'] == 'Under contract', out)

print('\n%s' % ('ALL PASS' if not fails else 'FAILS: %d -> %s' % (len(fails), fails)))
sys.exit(1 if fails else 0)
