"""A 2Captcha solve that never got a task queued gives its paid_reads reservation back; a queued one keeps it.

Run: python _captcharefundtest.py   (no network, throwaway ledger)"""
import os, sys, tempfile
from unittest.mock import patch
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix='capref_')
os.environ['DEALFLOW_PAID_LEDGER'] = os.path.join(TMP, 'ledger.json')
os.environ.pop('DEALFLOW_PAID_MONTHLY_CAP', None)
os.environ['TWOCAPTCHA_KEY'] = 'fake-key-for-test'
import paid_reads as PR
PR.CONFIG = os.path.join(TMP, 'paid_reads.json')
import captcha_solver as CS

FAILS = []
def check(n, c, d=''):
    print(('PASS ' if c else 'FAIL ') + n + (('  -- ' + str(d)) if d and not c else ''))
    if not c: FAILS.append(n)

def spent():
    return round(PR.status()['spent'], 6)

class R:
    def __init__(self, j): self._j = j
    def json(self): return self._j

U = PR.RECAPTCHA_V2_USD
v2 = PR.guarded(CS.solve_recaptcha_v2, 'palmbeach_liens', unit=U)

with patch.object(CS.requests, 'post', return_value=R({'status': 0, 'request': 'ERROR_ZERO_BALANCE'})):
    for _ in range(5):
        v2('sk', 'https://x')
check('five rejected submits book nothing', spent() == 0, spent())

with patch.object(CS.requests, 'post', side_effect=OSError('timeout')):
    v2('sk', 'https://x')
check('in.php that never answered keeps the charge (a task may exist)', abs(spent() - U) < 1e-9, spent())

with patch.object(CS.requests, 'post', return_value=R({'status': 1, 'request': '123'})), \
     patch.object(CS.requests, 'get', return_value=R({'status': 0, 'request': 'ERROR_CAPTCHA_UNSOLVABLE'})), \
     patch.object(CS.time, 'sleep', lambda s: None):
    v2('sk', 'https://x')
check('a queued task keeps the charge', abs(spent() - 2 * U) < 1e-9, spent())

with patch.object(CS.requests, 'post', return_value=R({'status': 1, 'request': '9'})), \
     patch.object(CS.requests, 'get', return_value=R({'status': 1, 'request': 'TOKEN'})), \
     patch.object(CS.time, 'sleep', lambda s: None):
    tok = v2('sk', 'https://x')
check('a solved token keeps the charge', tok == 'TOKEN' and abs(spent() - 3 * U) < 1e-9, spent())

ts = PR.guarded(CS.solve_turnstile, 'lis_pendens')
with patch.object(CS.requests, 'post', return_value=R({'status': 0, 'request': 'ERROR_KEY_DOES_NOT_EXIST'})):
    ts('sk', 'https://x')
check('turnstile rejection refunded too', abs(spent() - 3 * U) < 1e-9, spent())

other = PR.guarded(lambda: None, 'other')
other()
check('a solver that is not captcha_solver keeps the charge', abs(spent() - 3 * U - PR.SOLVE_USD) < 1e-9, spent())

print('\n%s' % ('ALL PASS' if not FAILS else 'FAILED: %s' % FAILS))
sys.exit(1 if FAILS else 0)
