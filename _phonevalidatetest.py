"""phone_validate: what it spends, what it stores, and what Call Mode does with the answer.

No network, no key, invented numbers only (555-01xx). Run: python _phonevalidatetest.py
"""
import io
import json
import os
import sys
import tempfile
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='phval_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')   # no federal cache: no holds
os.environ['TWILIO_VALIDATE_CACHE'] = os.path.join(_T, 'phone_validation.json')
os.environ['TWILIO_VALIDATE_DAILY_CAP'] = '0.05'
os.environ['TWILIO_KEY_FILE'] = os.path.join(_T, 'twilio.key')

import phone_validate as PV  # noqa: E402
import call_mode  # noqa: E402

FAIL, PASS = [], []
CACHE = os.environ['TWILIO_VALIDATE_CACHE']
CREDS = ('AC' + '0' * 32, 'f' * 32)


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok   ' if ok else '  FAIL ') + name + (('  -- ' + str(detail)[:200]) if not ok and detail else ''))


def reset():
    for p in (CACHE, CACHE + '.lock', PV.status_path()):
        if os.path.exists(p):
            os.remove(p)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake(statuses, calls):
    """opener answering line_status by the number in the URL."""
    def _open(req, timeout=None):
        calls.append(req.full_url)
        num = req.full_url.split('%2B1')[1].split('?')[0]
        st = statuses.get(num, 'active')
        if st == 401:
            raise urllib.error.HTTPError(req.full_url, 401, 'no', {}, None)
        return Resp(json.dumps({'valid': True, 'line_status': {'status': st, 'error_code': None}}).encode())
    return _open


print('\nCACHE HOLDS NO PHONE NUMBER')
reset()
calls = []
st = PV.run(numbers=['(305) 555-0101', '+1 305 555 0102'], creds=CREDS,
            opener=fake({'3055550101': 'inactive', '3055550102': 'unreachable'}, calls))
raw = open(CACHE, encoding='utf-8').read()
rec('two numbers checked', st['checked'] == 2, st)
rec('no digits of either number in the cache', '5550101' not in raw and '5550102' not in raw, raw[:200])
rec('verdicts read back by number', PV.line_status('3055550101') == 'inactive'
    and PV.line_status('13055550102') == 'unreachable', PV.verdicts())
rec('the request asks for line_status on the +1 number',
    calls and calls[0].endswith('%2B13055550101?Fields=line_status'), calls[:1])
rec('the key is sent, not printed: no credential in the status file',
    CREDS[1] not in open(PV.status_path(), encoding='utf-8').read())

print('\nONE LOOKUP PER NUMBER')
calls = []
st = PV.run(numbers=['3055550101', '3055550102'], creds=CREDS, opener=fake({}, calls))
rec('a cached number is not bought again', not calls and st['checked'] == 0, (calls, st))

print('\nTHE DAY CAP IS A CEILING ACROSS RUNS')
reset()
calls = []
nums = ['30555501%02d' % i for i in range(20)]
st = PV.run(numbers=nums, creds=CREDS, opener=fake({}, calls))
rec('$0.05 at $0.007 buys 7 lookups', st['checked'] == 7 and len(calls) == 7, st)
rec('stops on the day cap', 'daily cap' in st['stopped'], st['stopped'])
calls = []
st = PV.run(numbers=nums, creds=CREDS, opener=fake({}, calls))
rec('a second run the same day buys nothing', not calls, st)
c = json.load(open(CACHE))
rec('the ledger never goes over the cap', max(c['spend'].values()) <= 0.05 + 1e-9, c['spend'])

print('\nRUN CAP')
reset()
calls = []
st = PV.run(numbers=nums, creds=CREDS, opener=fake({}, calls), max_spend=0.014)
rec('--max-spend 0.014 buys 2', st['checked'] == 2, st)

print('\nFAILS CLOSED')
reset()
calls = []
st = PV.run(numbers=nums, creds=CREDS, opener=fake({'3055550100': 401}, calls))
rec('bad credentials stop the run after one request', len(calls) == 1 and 'credentials' in st['stopped'], st)
rec('and that request is still counted as spent', json.load(open(CACHE))['spend'], json.load(open(CACHE)))
open(CACHE, 'w').write('{torn')
calls = []
st = PV.run(numbers=nums, creds=CREDS, opener=fake({}, calls))
rec('a torn cache is not overwritten and nothing is spent', not calls and open(CACHE).read() == '{torn', st)
PV._VERDICTS['mtime'] = None
rec('a torn cache drops no number from Call Mode', PV.verdicts() == {}, PV.verdicts())
reset()
st = PV.run(numbers=nums, creds=None, opener=fake({}, []))
rec('no key file: skipped, nothing spent', 'no twilio.key' in st['stopped'] and st['usd'] == 0, st)
os.environ['TWILIO_VALIDATE_DAILY_CAP'] = 'abc'
rec('a garbled cap spends nothing', PV.daily_cap() == 0.0)
os.environ['TWILIO_VALIDATE_DAILY_CAP'] = '0.05'

print('\nDUE ORDER')
reset()
c = PV.load_cache()
import time  # noqa: E402
c['numbers'][PV.phone_key('3055550111')] = {'ls': 'active', 'ts': int(time.time()) - 100 * 86400}
c['numbers'][PV.phone_key('3055550112')] = {'ls': 'active', 'ts': int(time.time())}
due = PV.candidates(c, sources=['3055550111', '3055550112', '3055550113', '305555011'])
rec('never-checked first, then stale, fresh and malformed skipped', due == ['3055550113', '3055550111'], due)

def fake_err(code, calls):
    def _open(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, code, 'x', {}, None)
    return _open


reset()
calls = []
st = PV.run(numbers=nums, creds=CREDS, opener=fake_err(404, calls))
PV._VERDICTS['mtime'] = None
rec('a 404 stops the run and marks nothing disconnected', len(calls) == 1 and PV.verdicts() == {}, (st, PV.verdicts()))
reset()
calls = []
PV.time.sleep = lambda s: None
st = PV.run(numbers=['3055550100'], creds=CREDS, opener=fake_err(429, calls))
rec('every retried request is counted', abs(st['usd'] - 0.007 * len(calls)) < 1e-9, (st, len(calls)))
reset()
calls = []
st = PV.run(numbers=['3055550100'], creds=CREDS, opener=fake_err(429, calls), max_spend=0.014)
rec('a retry past the run cap is not sent', len(calls) == 2 and st['usd'] <= 0.014 + 1e-9, (st, len(calls)))
c = PV.load_cache()
c['numbers'][PV.phone_key('3055550121')] = {'ls': 'unreachable', 'ts': int(time.time()) - 4 * 86400}
c['numbers'][PV.phone_key('3055550122')] = {'ls': 'active', 'ts': int(time.time()) - 4 * 86400}
due = PV.candidates(c, sources=['3055550121', '3055550122'])
rec('an unreachable number is rechecked after 3 days, an active one is not', due == ['3055550121'], due)

print('\nSCOPE')
_q, _b, _s = PV._queue_numbers, PV._board_numbers, PV._skiptrace_numbers
PV._queue_numbers = lambda: (['3055550131'], [{'phones': ['3055550132']}])
PV._board_numbers = lambda slim: ['3055550132']
PV._skiptrace_numbers = lambda: ['3055550133']
reset()
os.environ.pop('TWILIO_VALIDATE_SCOPE', None)
due = PV.candidates(PV.load_cache())
rec('default scope checks only the Call Mode queue', due == ['3055550131'], due)
os.environ['TWILIO_VALIDATE_SCOPE'] = 'all'
due = PV.candidates(PV.load_cache())
rec('scope all adds the board and skip trace after the queue',
    due == ['3055550131', '3055550132', '3055550133'], due)
os.environ['TWILIO_VALIDATE_SCOPE'] = 'everything'
rec('an unknown scope reads as queue', PV.scope() == 'queue')
os.environ.pop('TWILIO_VALIDATE_SCOPE', None)
PV._queue_numbers = lambda: ([], [])
due = PV.candidates(PV.load_cache())
rec('queue unreadable under default scope: nothing is due', due == [], due)
PV._queue_numbers, PV._board_numbers, PV._skiptrace_numbers = _q, _b, _s
c = PV.load_cache()
c['numbers'][PV.phone_key('3055550141')] = {'ls': 'inactive', 'ts': int(time.time()) - 61 * 86400}
c['numbers'][PV.phone_key('3055550142')] = {'ls': 'inactive', 'ts': int(time.time()) - 5 * 86400}
json.dump(c, open(CACHE, 'w'))
PV._VERDICTS['mtime'] = None
rec('a disconnected verdict past 60 days stops dropping the number, so the queue rechecks it',
    PV.line_status('3055550141') == '' and PV.line_status('3055550142') == 'inactive', PV.verdicts())
reset()

print('\nCALL MODE')


def lead(case, phones, **extra):
    d = {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': '11/01/2026', 'days': 20,
         'tier': 'B', 'score': 50, 'phones': phones, 'value': 400000}
    d.update(extra)
    return d


reset()
PV.run(numbers=['9545550101', '9545550102', '9545550201'], creds=CREDS,
       opener=fake({'9545550101': 'inactive', '9545550102': 'unreachable', '9545550201': 'inactive'}, []))
rows, _ = call_mode.call_rows([
    lead('MIXED', ['9545550102', '9545550101', '9545550103'], phbest=0),
    lead('ALL-DEAD', ['9545550201']),
    lead('UNCHECKED', ['9545550301']),
], None, None)
by = {r['c']: r for r in rows}
rec('a disconnected number leaves the dial list', '9545550101' not in by['MIXED']['p'], by['MIXED']['p'])
rec('an unreachable number is dialled last, even when ranked best',
    by['MIXED']['p'] == ['9545550103', '9545550102'], by['MIXED']['p'])
rec('a lead whose only number is disconnected leaves the queue', 'ALL-DEAD' not in by, list(by))
rec('a never-checked number is untouched', by.get('UNCHECKED', {}).get('p') == ['9545550301'], by.get('UNCHECKED'))
reset()
PV._VERDICTS['mtime'] = None
rows, _ = call_mode.call_rows([lead('ALL-DEAD', ['9545550201'])], None, None)
rec('no cache: nothing is dropped', [r['c'] for r in rows] == ['ALL-DEAD'], rows)

print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
