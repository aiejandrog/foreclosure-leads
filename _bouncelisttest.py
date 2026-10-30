"""_bouncelisttest — cadence suppresses a legacy bounced_emails.json list at send time.

An older bounce file is a bare list of addresses. _bounce_send_block already treats that
as readable, and the per-address sweep used to ignore it, so those addresses were mailed.
Fake 2099- numbers and example.com only. No network.
"""
import datetime as dt
import io
import json
import os
# Follow-up sends are off unless this is exactly 1 (PR #294). This fixture expects one to leave.
os.environ['DEALFLOW_FOLLOWUPS_ON'] = '1'
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:400]) if d and not cond else ''))


import cadence as C

rec('a legacy bounce list is read as addresses',
    C._bounce_addrs(['Bounced@example.com', '']) == {'bounced@example.com'})
rec('a bounce object is read as addresses',
    C._bounce_addrs({'Keep@example.com': {'type': 'hard'}}) == {'keep@example.com'})
rec('anything else is no addresses', C._bounce_addrs('bounced@example.com') == set())

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
C._lock_dir = lambda: os.path.join(ctmp, 'cadence.lock')
json.dump({'_dealflow_notes': 1, 'notes': {}}, open(C.OPTOUTS, 'w'))
json.dump({'date': TODAY, 'state': 'finished', 'ok': True,
           'started_at': 0, 'finished_at': 1, 'steps': []},
          open(os.path.join(ctmp, 'sync_status.json'), 'w'))
json.dump(['Bounced@example.com'], open(os.path.join(ctmp, 'bounced_emails.json'), 'w'))
json.dump({'sender': {'name': 'Test Sender'}, 'queue': [
    {'case': '2099-000401-CA-01', 'owner': 'A', 'email': 'bounced@example.com', 'step': 1},
    {'case': '2099-000402-CA-01', 'owner': 'B', 'email': 'keep@example.com', 'step': 1},
]}, open(C.QUEUE, 'w'))
json.dump({
    '2099-000401-CA-01': {'step': 1, 'next': TODAY, 'status': 'active', 'owner': 'A',
                          'email': 'bounced@example.com', 'log': []},
    '2099-000402-CA-01': {'step': 1, 'next': TODAY, 'status': 'active', 'owner': 'B',
                          'email': 'keep@example.com', 'log': []},
}, open(C.STATE, 'w'))
C._oe._load_optouts = lambda: set()
C._oe._load_leads = lambda: [
    {'case': '2099-000401-CA-01', 'days': 40},
    {'case': '2099-000402-CA-01', 'days': 40}]
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
    'proven': {'bounced@example.com', 'keep@example.com'},
    'ver': {}, 'last_mailed': {'bounced@example.com': OLD, 'keep@example.com': OLD}}
sent = []
C._ss._smtp_send = lambda *a, **k: sent.append(a[3]) or '<mid@example.com>'
argv, buf = sys.argv, io.StringIO()
sys.argv = ['cadence.py']
_o = sys.stdout
sys.stdout = buf
try:
    rc = C.main()
finally:
    sys.stdout = _o
    sys.argv = argv
st = json.load(open(C.STATE, encoding='utf-8'))
rec('a legacy list suppresses that address at send time',
    'bounced@example.com' not in sent and st['2099-000401-CA-01']['status'] == 'suppressed',
    (sent, st['2099-000401-CA-01'].get('status')))
rec('the suppressed step is not consumed', st['2099-000401-CA-01']['step'] == 1,
    st['2099-000401-CA-01'])
rec('an address that is not on the list still sends',
    'keep@example.com' in sent and st['2099-000402-CA-01']['step'] == 2, (sent, st['2099-000402-CA-01']))
rec('the run exits 0', rc == 0, rc)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
