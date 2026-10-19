"""_cadenceskiptest — a tracked case list holds follow-ups until that line is removed.

cadence_skip.json is case numbers and a reason. build_cadence_queue.py will not enroll a
listed case. cadence.py will not mail it, including when the case is already in a queue and
already in cadence state. Deleting the line enrolls and sends on the next run. A file that
will not parse enrolls nobody and sends nothing.

Fake 2099- numbers and example.com only. The two seeded case numbers are asserted from the
tracked file and never paired with a name, phone, or address. No network.
"""
import datetime as dt
import io
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
ok, bad = [], []
REASON = 'unaudited 2026-10-12 first send, hold pending audit'
SEEDED = {'CACE-25-009495', '502025CA009919XXXAMB'}


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:400]) if d and not cond else ''))


import cadence as C

repo_skip = os.path.join(HERE, 'cadence_skip.json')
raw = io.open(repo_skip, encoding='utf-8').read()
rec('seed file is case numbers and a reason only', '@' not in raw and 'phone' not in raw.lower())
seed = json.loads(raw)
rec('seed file is strict JSON with the two cases', set(seed) == SEEDED and set(seed.values()) == {REASON}, seed)
cases, problem = C.load_skip(repo_skip)
rec('load_skip reads the seed', problem == '' and cases == SEEDED, (problem, cases))

tmp = tempfile.mkdtemp()
kept = ''.join(ln for ln in raw.splitlines(True) if 'CACE-25-009495' not in ln)
open(os.path.join(tmp, 'drop-first.json'), 'w', encoding='utf-8').write(kept)
cases, problem = C.load_skip(os.path.join(tmp, 'drop-first.json'))
rec('removing the first line drops only that hold',
    problem == '' and cases == {'502025CA009919XXXAMB'}, (problem, cases))
kept = ''.join(ln for ln in raw.splitlines(True) if '502025CA009919XXXAMB' not in ln)
open(os.path.join(tmp, 'drop-last.json'), 'w', encoding='utf-8').write(kept)
cases, problem = C.load_skip(os.path.join(tmp, 'drop-last.json'))
rec('removing the last line drops only that hold',
    problem == '' and cases == {'CACE-25-009495'}, (problem, cases))
open(os.path.join(tmp, 'torn.json'), 'w', encoding='utf-8').write('{')
cases, problem = C.load_skip(os.path.join(tmp, 'torn.json'))
rec('a torn skip list is a hold, not an empty list',
    cases == set() and 'UNREADABLE' in problem, problem)
open(os.path.join(tmp, 'blank.json'), 'w', encoding='utf-8').write('{"2099-000401-CA-01": ""}')
cases, problem = C.load_skip(os.path.join(tmp, 'blank.json'))
rec('a case with no reason holds the whole list', 'no reason' in problem and cases == set(), problem)
rec('a legacy bounce list is read as addresses',
    C._bounce_addrs(['Bounced@example.com', '']) == {'bounced@example.com'})
rec('a bounce object is read as addresses',
    C._bounce_addrs({'Keep@example.com': {'type': 'hard'}}) == {'keep@example.com'})

# ---- enrolment -------------------------------------------------------------------------------
import build_cadence_queue as B
import outreach_email as OE

def _lead(case):
    return {'case': case, 'tier': 'A', 'owners': 'SAMPLE PERSON', 'emails': ['keep@example.com'],
            'auction': '12/01/2027', 'days': 40, 'addr': '1 EXAMPLE AVE'}

OE._load_leads = lambda: [_lead(c) for c in (
    'CACE-25-009495', '502025CA009919XXXAMB', 'cace-25-009495', '2099-000501-CA-01')]
OE._load_ledger = lambda: []
OE._load_optouts = lambda: set()
OE._eligible = lambda *a, **k: (False, 'worker already emailed - follow-ups are the worker\'s job')
B._DGQ.check = lambda row: {'hold': False, 'code': '', 'why': ''}
qdir = tempfile.mkdtemp()
B.QUEUE = os.path.join(qdir, 'cadence_queue.json')
buf = io.StringIO()
_o = sys.stdout
sys.stdout = buf
try:
    rc = B.main([])
finally:
    sys.stdout = _o
written = json.load(open(B.QUEUE, encoding='utf-8'))
got = [q['case'] for q in written['queue']]
rec('builder exits 0', rc == 0, rc)
rec('builder does not enroll either seeded case, in either case',
    got == ['2099-000501-CA-01'], got)
rec('the drop is counted and the seeded numbers are not printed with an address',
    'cadence skip list' in buf.getvalue() and 'CACE-25-009495' not in buf.getvalue()
    and '502025CA009919XXXAMB' not in buf.getvalue())
bad_skip = os.path.join(qdir, 'torn.json')
open(bad_skip, 'w', encoding='utf-8').write('{')
B.SKIP = bad_skip
B.QUEUE = os.path.join(qdir, 'should-not-exist.json')
sys.stdout = io.StringIO()
try:
    rc = B.main([])
finally:
    sys.stdout = _o
rec('an unreadable skip list writes no queue', rc == 2 and not os.path.exists(B.QUEUE), rc)

# ---- send time, including a queue and state that already exist --------------------------------
import diligence_gate as DG
import send_server as S

TODAY = dt.date.today().isoformat()
OLD = (dt.date.today() - dt.timedelta(days=5)).isoformat()
ctmp = tempfile.mkdtemp()
S.HERE = ctmp
S.SENT_LEDGER = os.path.join(ctmp, 'mail_sent.json')
json.dump([], open(S.SENT_LEDGER, 'w'))
C.HERE = ctmp
C.QUEUE = os.path.join(ctmp, 'cadence_queue.json')
C.STATE = os.path.join(ctmp, 'cadence_state.json')
C.OPTOUTS = os.path.join(ctmp, 'optouts.json')
C.SKIP = os.path.join(ctmp, 'cadence_skip.json')
C._lock_dir = lambda: os.path.join(ctmp, 'cadence.lock')
json.dump({'_dealflow_notes': 1, 'notes': {}}, open(C.OPTOUTS, 'w'))
json.dump({'date': TODAY, 'state': 'finished', 'ok': True,
           'started_at': 0, 'finished_at': 1, 'steps': []},
          open(os.path.join(ctmp, 'sync_status.json'), 'w'))
json.dump(['bounced@example.com'], open(os.path.join(ctmp, 'bounced_emails.json'), 'w'))
json.dump({
    '2099-000401-ca-01': 'test hold',
    '2099-000404-CA-01': 'test hold',
}, open(C.SKIP, 'w'))
CASES = ('2099-000401-CA-01', '2099-000402-CA-01', '2099-000403-CA-01', '2099-000404-CA-01')
EMAIL = {'2099-000401-CA-01': 'skip@example.com', '2099-000402-CA-01': 'keep@example.com',
         '2099-000403-CA-01': 'bounced@example.com', '2099-000404-CA-01': 'new@example.com'}
json.dump({'sender': {'name': 'Test Sender'}, 'queue': [
    {'case': c, 'owner': 'A', 'email': EMAIL[c], 'addr': '1 EXAMPLE AVE', 'step': 1}
    for c in CASES]}, open(C.QUEUE, 'w'))
json.dump({c: {'step': 1, 'next': TODAY, 'status': 'active', 'owner': 'A',
               'email': EMAIL[c], 'addr': '1 EXAMPLE AVE', 'log': []}
           for c in CASES if c != '2099-000404-CA-01'}, open(C.STATE, 'w'))
C._oe._load_optouts = lambda: set()
C._oe._load_leads = lambda: [{'case': c, 'days': 40} for c in CASES]
DG.gate = lambda row: {'hold': False, 'code': '', 'why': '', 'unchecked': False, 'warn': False}
C.load_key = lambda: ('login@example.com', 'app-password')
C.imap_replies = lambda *a, **k: {}
C.steps = lambda s, sender: [('subj %d' % i, 'body %d about 1 EXAMPLE AVE' % i) for i in range(4)]
C._ss._load_senders = lambda: {
    'main_domain': 'example.com', 'main_domain_cap': 40,
    'ramp_start': OLD, 'ramp': [{'through_day': 9999, 'per_day': 100}],
    'lanes': {'early': 'early@example.com', 'active': 'active@example.com',
              'default': 'login@example.com'},
    'first_touch': {'from': ['ft@example.com'], 'per_day': 5}}
C._ss._deliverability_evidence = lambda: {
    'bounced': set(), 'replied': set(),
    'proven': set(EMAIL.values()),
    'ver': {}, 'last_mailed': {e: OLD for e in EMAIL.values()}}
sent = []
C._ss._smtp_send = lambda *a, **k: sent.append(a[3]) or '<mid@example.com>'


def _run():
    sent.clear()
    argv, buf = sys.argv, io.StringIO()
    sys.argv = ['cadence.py']
    _out = sys.stdout
    sys.stdout = buf
    try:
        rc = C.main()
    finally:
        sys.stdout = _out
        sys.argv = argv
    return rc, buf.getvalue(), json.load(open(C.STATE, encoding='utf-8'))


rc, out, st = _run()
rec('a listed case already in state is not mailed', 'skip@example.com' not in sent, sent)
rec('that hold does not consume the step or close the sequence',
    st['2099-000401-CA-01']['step'] == 1 and st['2099-000401-CA-01']['status'] == 'active',
    st['2099-000401-CA-01'])
rec('the run says the case was skipped', 'SKIP (not sent) -> 2099-000401-CA-01' in out, out[-800:])
rec('a listed case that is not yet in state is not enrolled',
    '2099-000404-CA-01' not in st and 'SKIP (not enrolled) -> 2099-000404-CA-01' in out, list(st))
rec('a case that is not listed still sends', 'keep@example.com' in sent, sent)
rec('a legacy bounce list still suppresses at send time',
    'bounced@example.com' not in sent and st['2099-000403-CA-01']['status'] == 'suppressed',
    (sent, st['2099-000403-CA-01'].get('status')))
rec('the held run exits 0', rc == 0, rc)

json.dump({}, open(C.SKIP, 'w'))
rc, out, st = _run()
rec('removing the line sends the follow-up on the next run',
    'skip@example.com' in sent and st['2099-000401-CA-01']['step'] == 2, (sent, st['2099-000401-CA-01']))
rec('removing the line enrolls the case the queue was already holding',
    'new@example.com' in sent and st['2099-000404-CA-01']['status'] == 'active'
    and st['2099-000404-CA-01']['step'] == 2, (sent, st.get('2099-000404-CA-01')))
rec('the bounce list is still in force after the skip line is gone',
    'bounced@example.com' not in sent, sent)

held = tempfile.mkdtemp()
C.HERE = held
C.QUEUE = os.path.join(held, 'cadence_queue.json')
C.STATE = os.path.join(held, 'cadence_state.json')
C.OPTOUTS = os.path.join(held, 'optouts.json')
C.SKIP = os.path.join(held, 'cadence_skip.json')
C._lock_dir = lambda: os.path.join(held, 'cadence.lock')
S.HERE = held
S.SENT_LEDGER = os.path.join(held, 'mail_sent.json')
json.dump([], open(S.SENT_LEDGER, 'w'))
json.dump({'_dealflow_notes': 1, 'notes': {}}, open(C.OPTOUTS, 'w'))
json.dump({'date': TODAY, 'state': 'finished', 'ok': True,
           'started_at': 0, 'finished_at': 1, 'steps': []},
          open(os.path.join(held, 'sync_status.json'), 'w'))
json.dump({}, open(os.path.join(held, 'bounced_emails.json'), 'w'))
open(C.SKIP, 'w', encoding='utf-8').write('{')
json.dump({'sender': {'name': 'Test Sender'}, 'queue': [
    {'case': '2099-000402-CA-01', 'owner': 'A', 'email': 'keep@example.com', 'step': 1}]},
    open(C.QUEUE, 'w'))
json.dump({'2099-000402-CA-01': {'step': 1, 'next': TODAY, 'status': 'active', 'owner': 'A',
                                  'email': 'keep@example.com', 'log': []}}, open(C.STATE, 'w'))
rc, out, st = _run()
rec('an unreadable skip list sends nothing and leaves the step due',
    sent == [] and st['2099-000402-CA-01']['step'] == 1 and 'UNREADABLE' in out, (sent, out[-500:]))

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
