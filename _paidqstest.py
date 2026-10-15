"""records_liens: the search token a paid 2Captcha solve buys is kept, so the next run for that owner reads it for free."""
import os, sys, tempfile, types
from unittest.mock import patch
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ['DEALFLOW_PAID_LEDGER'] = os.path.join(tempfile.mkdtemp(prefix='paidqs_'), 'l.json')
import records_liens as R

FAILS = []
def check(n, c, d=''):
    print(('PASS ' if c else 'FAIL ') + n + (('  -- ' + str(d)) if d and not c else ''))
    if not c: FAILS.append(n)

class Resp:
    def json(self): return {'qs': 'QS-TOKEN-1', 'isValidSearch': True}

fake_cs = types.ModuleType('captcha_solver')
fake_cs.solve_turnstile = lambda *a, **k: 'tok'
with patch.dict(sys.modules, {'captcha_solver': fake_cs}), \
     patch.object(R, '_may_submit', lambda: True), patch.object(R, '_ledger_save', lambda: None), \
     patch.object(R.S, 'post', lambda *a, **k: Resp()), \
     patch.object(R, 'records_by_qs', lambda qs: [{'qs': qs}]):
    R._PAID_QS['last'] = None
    m = R.fetch_via_turnstile(('WHITE', 'SHUROD'))
check('paid solve returns records', m == [{'qs': 'QS-TOKEN-1'}], m)
check('and remembers the token it bought', R._PAID_QS['last'] == 'QS-TOKEN-1', R._PAID_QS)

src = open(os.path.join(HERE, 'records_liens.py'), encoding='utf-8').read()
i = src.index("models = fetch_via_turnstile(sp)")
check('owner loop writes the paid token to records_qs.json',
      "qs_cache[oc] = _PAID_QS['last']" in src[i:i + 900] and 'json.dump(qs_cache, open(QS_CACHE' in src[i:i + 900])

j = src.index("models = records_by_qs(qs_cache[oc])")
check('an empty cached read is treated as expired, not as an answer',
      'if not models and not a.cached_only:' in src[j:j + 700] and 'models = None' in src[j:j + 700])

print('\n' + ('ALL PASS' if not FAILS else 'FAILED: %s' % FAILS))
sys.exit(1 if FAILS else 0)
