"""One cadence run sends one note per owner, then waits the existing step gap.

A hand run mailed a step-2 follow-up and the final note to the same owner seconds
apart: two due sequences, one person. Fake 2099- numbers and example.com only. No network.
"""
import datetime as dt
import io
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:400]) if d and not cond else ''))


import cadence as C
import diligence_gate as DG
import send_server as S

TODAY = dt.date.today()
TODAY_S = TODAY.isoformat()
OLD = (TODAY - dt.timedelta(days=5)).isoformat()
AGO = lambda n: (TODAY - dt.timedelta(days=n)).isoformat()

rec('the cadence spacing is still day 0/2/4/7', C.TOUCH_GAPS == (0, 2, 2, 3) and C._MIN_GAP == 2)
rec('owner labels fold case and token order',
    C._norm_owner('PERSON, SAMPLE') == C._norm_owner('Sample Person') == 'person sample')
rec('a blank owner is not an identity', C._norm_owner(' , ') == '')
rec('a first touch with no prior note may send',
    C._next_touch(0, TODAY, None, False) is None)
rec('a follow-up waits 2 days',
    C._next_touch(1, TODAY, TODAY - dt.timedelta(days=1), False) == TODAY + dt.timedelta(days=1))
rec('a follow-up whose gap has elapsed may send',
    C._next_touch(1, TODAY, TODAY - dt.timedelta(days=2), False) is None)
rec('the final note waits 3 days',
    C._next_touch(3, TODAY, TODAY - dt.timedelta(days=2), False) == TODAY + dt.timedelta(days=1))
rec('a second note in the same run waits at least the minimum gap',
    C._next_touch(0, TODAY, None, True) == TODAY + dt.timedelta(days=2))
rec('a final note already touched this run waits its own 3 days',
    C._next_touch(3, TODAY, TODAY - dt.timedelta(days=10), True) == TODAY + dt.timedelta(days=3))

wide = {}
for i in range(9):
    wide['2099-0006%02d-CA-01' % i] = {
        'email': 'firm@example.com', 'owner': 'OWNER %d' % i, 'addr': '%d OTHER AVE' % i}
idx = C._touch_index(wide)
rec('an email on more than 8 cases does not link them',
    all('em:firm@example.com' not in ks for ks in idx.values()) and len(idx) == 9)
pair = {
    '2099-000701-CA-01': {'email': 'one@example.com', 'owner': 'SAMPLE PERSON', 'addr': '1 EXAMPLE AVE'},
    '2099-000702-CA-01': {'email': 'two@example.com', 'owner': 'PERSON, SAMPLE', 'addr': '2 EXAMPLE AVE'},
}
pidx = C._touch_index(pair)
rec('two cases with the same owner share an identity key',
    C._related_cases(pidx, '2099-000701-CA-01') == {'2099-000701-CA-01', '2099-000702-CA-01'})


def _run(rows):
    ctmp = tempfile.mkdtemp()
    S.HERE = ctmp
    S.SENT_LEDGER = os.path.join(ctmp, 'mail_sent.json')
    json.dump([], open(S.SENT_LEDGER, 'w'))
    json.dump({}, open(os.path.join(ctmp, 'bounced_emails.json'), 'w'))
    C.HERE = ctmp
    C.QUEUE = os.path.join(ctmp, 'cadence_queue.json')
    C.STATE = os.path.join(ctmp, 'cadence_state.json')
    C.OPTOUTS = os.path.join(ctmp, 'optouts.json')
    C._lock_dir = lambda: os.path.join(ctmp, 'cadence.lock')
    json.dump({'_dealflow_notes': 1, 'notes': {}}, open(C.OPTOUTS, 'w'))
    json.dump({'date': TODAY_S, 'state': 'finished', 'ok': True,
               'started_at': 0, 'finished_at': 1, 'steps': []},
              open(os.path.join(ctmp, 'sync_status.json'), 'w'))
    queue, state, leads = [], {}, []
    emails = []
    for row in rows:
        case = row['case']
        emails.append(row['email'])
        queue.append({'case': case, 'owner': row['owner'], 'email': row['email'],
                      'addr': row['addr'], 'step': row['step']})
        state[case] = {
            'step': row['step'], 'next': row.get('next', TODAY_S), 'status': 'active',
            'owner': row['owner'], 'email': row['email'], 'addr': row['addr'],
            'log': list(row.get('log') or []),
        }
        leads.append({'case': case, 'days': 40})
    json.dump({'sender': {'name': 'Test Sender'}, 'queue': queue}, open(C.QUEUE, 'w'))
    json.dump(state, open(C.STATE, 'w'))
    C._oe._load_optouts = lambda: set()
    C._oe._load_leads = lambda: list(leads)
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
        'bounced': set(), 'replied': set(), 'proven': set(emails),
        'ver': {}, 'last_mailed': {e: OLD for e in emails}}
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
    return sent, st, rc, buf.getvalue()


sent, st, rc, out = _run([
    {'case': '2099-000501-CA-01', 'owner': 'SAMPLE PERSON', 'email': 'one@example.com',
     'addr': '1 EXAMPLE AVE', 'step': 1},
    {'case': '2099-000502-CA-01', 'owner': 'PERSON, SAMPLE', 'email': 'two@example.com',
     'addr': '2 EXAMPLE AVE', 'step': 3,
     'log': [{'d': AGO(10), 'ev': 'sent step 3'}]},
    {'case': '2099-000503-CA-01', 'owner': 'OTHER PERSON', 'email': 'other@example.com',
     'addr': '3 EXAMPLE AVE', 'step': 1},
])
rec('the run exits 0', rc == 0, rc)
rec('a step-2 follow-up sends and the same owner\'s final note does not',
    sent == ['one@example.com', 'other@example.com'], sent)
rec('the final note stays on its step',
    st['2099-000502-CA-01']['step'] == 3 and st['2099-000502-CA-01']['status'] == 'active',
    st['2099-000502-CA-01'])
rec('the final note moves out by its 3-day gap',
    st['2099-000502-CA-01']['next'] == str(TODAY + dt.timedelta(days=3)),
    st['2099-000502-CA-01'].get('next'))
rec('a different owner in the same run still sends',
    st['2099-000503-CA-01']['step'] == 2, st['2099-000503-CA-01'])
rec('the follow-up that did send is scheduled on the existing 2-day gap',
    st['2099-000501-CA-01']['step'] == 2
    and st['2099-000501-CA-01']['next'] == str(TODAY + dt.timedelta(days=C.TOUCH_GAPS[2])),
    st['2099-000501-CA-01'])

sent, st, rc, out = _run([
    {'case': '2099-000511-CA-01', 'owner': 'OWNER ONE', 'email': 'both@example.com',
     'addr': '4 EXAMPLE AVE', 'step': 1},
    {'case': '2099-000512-CA-01', 'owner': 'OWNER TWO', 'email': 'both@example.com',
     'addr': '5 EXAMPLE AVE', 'step': 1},
])
rec('one email address gets one note in the run', sent == ['both@example.com'], sent)
rec('the second case on that address keeps its step and waits 2 days',
    st['2099-000512-CA-01']['step'] == 1
    and st['2099-000512-CA-01']['next'] == str(TODAY + dt.timedelta(days=2)),
    st['2099-000512-CA-01'])

sent, st, rc, out = _run([
    {'case': '2099-000521-CA-01', 'owner': 'OWNER THREE', 'email': 'three@example.com',
     'addr': '9 EXAMPLE AVE', 'step': 1},
    {'case': '2099-000522-CA-01', 'owner': 'OWNER FOUR', 'email': 'four@example.com',
     'addr': '9 example ave.', 'step': 1},
])
rec('one property address gets one note in the run',
    sent == ['three@example.com'] and 'four@example.com' not in sent, sent)
rec('the second case at that property waits 2 days',
    st['2099-000522-CA-01']['step'] == 1
    and st['2099-000522-CA-01']['next'] == str(TODAY + dt.timedelta(days=2)),
    st['2099-000522-CA-01'])

sent, st, rc, out = _run([
    {'case': '2099-000531-CA-01', 'owner': 'OWNER FIVE', 'email': 'five@example.com',
     'addr': '6 EXAMPLE AVE', 'step': 1,
     'log': [{'d': AGO(1), 'ev': 'sent step 1'}]},
])
rec('a follow-up inside the 2-day gap does not send', sent == [], sent)
rec('that follow-up stays due the day the gap ends',
    st['2099-000531-CA-01']['step'] == 1
    and st['2099-000531-CA-01']['next'] == str(TODAY + dt.timedelta(days=1)),
    st['2099-000531-CA-01'])

sent, st, rc, out = _run([
    {'case': '2099-000541-CA-01', 'owner': 'OWNER SIX', 'email': 'six@example.com',
     'addr': '7 EXAMPLE AVE', 'step': 3,
     'log': [{'d': AGO(2), 'ev': 'sent step 3'}]},
])
rec('a final note inside the 3-day gap does not send', sent == [], sent)
rec('that final note waits out the rest of its 3 days',
    st['2099-000541-CA-01']['step'] == 3 and st['2099-000541-CA-01']['status'] == 'active'
    and st['2099-000541-CA-01']['next'] == str(TODAY - dt.timedelta(days=2) + dt.timedelta(days=3)),
    st['2099-000541-CA-01'])

sent, st, rc, out = _run([
    {'case': '2099-000551-CA-01', 'owner': 'OWNER SEVEN', 'email': 'seven@example.com',
     'addr': '8 EXAMPLE AVE', 'step': 3,
     'log': [{'d': AGO(3), 'ev': 'sent step 3'}]},
])
rec('a final note whose 3-day gap has elapsed sends once',
    sent == ['seven@example.com'] and st['2099-000551-CA-01']['status'] == 'completed'
    and st['2099-000551-CA-01']['step'] == 4, (sent, st['2099-000551-CA-01']))

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
