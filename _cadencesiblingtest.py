#!/usr/bin/env python3
"""_cadencesiblingtest.py: cadence's send-time sweep follows an opt-out to the same owner's other case.

Fake 2099 cases, example.com addresses, temp folders, fake SMTP. No network, no real data.
Run: python _cadencesiblingtest.py
"""
import datetime as dt
import io
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:300]) if d and not cond else ''))


import sibling_optout as SO

A, B, C_, D = '2099-000301-CA-01', '2099-000302-CA-01', '2099-000303-CA-01', '2099-000304-CA-01'
cf = lambda r: r.get('case')
leads = [{'case': A, 'emails': ['a@example.com', 'shared@example.com'], 'phones': ['3055550301']},
         {'case': B, 'emails': ['shared@example.com']},
         {'case': C_, 'emails': ['other@example.com'], 'phones': [{'number': '+1 (305) 555-0301'}]},
         {'case': D, 'emails': ['unrelated@example.com']}]
r = SO.sibling_cases(leads, {A.lower()}, cf)
rec('1a shared email links the other case', B.lower() in r, r)
rec('1b shared phone (dict, 11-digit) links the other case', C_.lower() in r, r)
rec('1c an unrelated lead is left alone', D.lower() not in r and A.lower() not in r, r)
rec('1d empty ledger changes nothing', SO.sibling_cases(leads, set(), cf) == set())
inst = [{'case': 'X%d' % i, 'emails': ['law@example.com']} for i in range(9)] + [{'case': A, 'emails': ['law@example.com']}, {'case': 'Y', 'emails': ['law@example.com']}]
rec('1e an address on more than 8 leads is an institution, not a person',
    SO.sibling_cases(inst, {A.lower()}, cf) == set())
dup = [dict(x) for x in leads] + [dict(x) for x in leads] * 4          # same 4 cases repeated across files
rec('1g the same lead repeated across files does not trip the institution guard',
    B.lower() in SO.sibling_cases(dup, {A.lower()}, cf))
pk = SO.ledger_phone_keys(os.devnull, ['#1 (305) 555-0301', '#bad', 'a@x.com'])
rec('1h phone keys are normalised to 10 digits, junk ignored', pk == {'3055550301'}, pk)
rp = SO.sibling_cases(leads, set(), cf, phone_keys={'3055550301'})
rec('1i a phone-only stop reaches the email sibling of the lead that holds the number', B.lower() in rp, rp)
rec('1j ...and the lead holding the number itself is suppressed', A.lower() in rp and C_.lower() in rp, rp)
rec('1k ...and an unrelated lead is not', D.lower() not in rp, rp)
rec('1f ledger match by email key alone opts the lead out too',
    B.lower() in SO.sibling_cases([{'case': 'Z', 'emails': ['k@example.com', 'x@example.com']},
                                   {'case': B, 'emails': ['x@example.com']}], {'k@example.com'}, cf))

# ---- through cadence's real sweep --------------------------------------------------------------
import cadence as CD
TODAY = dt.date.today()
ctmp = tempfile.mkdtemp()
CD.HERE = ctmp
CD.QUEUE = os.path.join(ctmp, 'cadence_queue.json')
CD.STATE = os.path.join(ctmp, 'cadence_state.json')
CD.OPTOUTS = os.path.join(ctmp, 'optouts.json')
CD._ss.HERE = ctmp
CD._ss.SENT_LEDGER = os.path.join(ctmp, 'mail_sent.json')   # never the real ledger
CD._ss.REFUSAL_LOG = os.path.join(ctmp, 'send_refusals.jsonl')
json.dump([], open(CD._ss.SENT_LEDGER, 'w'))
json.dump({'_dealflow_notes': 1, 'notes': {}}, open(CD.OPTOUTS, 'w'))
# cadence asks the send bridge's stay verdict at send time (2026-09-29): these cases have a docket
# read with no stay, so the sibling / opt-out gates under test are what decides each sequence.
json.dump({c: {'a': False, 'bd': '', 'sl': '', 's': 0, 'n': 0, 'd': 0, 'w': '', 'b': 0, 't': 0, 'v': 5}
           for c in (A, B, C_, D)}, open(os.path.join(ctmp, 'sale_history_cache.json'), 'w'))
json.dump({'date': TODAY.isoformat(), 'state': 'finished', 'ok': True,
           'started_at': 0, 'finished_at': 1, 'steps': []}, open(os.path.join(ctmp, 'sync_status.json'), 'w'))


def run(lead_rows, ledger):
    json.dump({'sender': {'name': 'Test Sender'}, 'queue': [
        {'case': B, 'owner': 'B', 'email': 'shared@example.com', 'step': 1},
        {'case': D, 'owner': 'D', 'email': 'unrelated@example.com', 'step': 1}]}, open(CD.QUEUE, 'w'))
    if os.path.exists(CD.STATE):
        os.remove(CD.STATE)
    CD._oe._load_optouts = lambda: set(ledger)
    CD._oe._load_leads = lambda: [dict(x, days=40) for x in lead_rows]
    import diligence_gate as dg
    dg.gate = lambda row: {'hold': False, 'code': '', 'why': ''}
    CD.load_key = lambda: ('alejandro@bsgflorida.com', 'app-password')
    CD.imap_replies = lambda *a, **k: {}
    CD.steps = lambda s, sender: [('subj %d' % i, 'body %d' % i) for i in range(4)]
    old = (TODAY - dt.timedelta(days=5)).isoformat()
    CD._ss._deliverability_evidence = lambda: {'bounced': set(), 'replied': set(),
        'proven': {'shared@example.com', 'unrelated@example.com'}, 'ver': {},
        'last_mailed': {'shared@example.com': old, 'unrelated@example.com': old}}
    sent = []
    CD._ss._smtp_send = lambda user, pw, disp, to, subj, body, bcc='', attach=None, from_addr=None: (
        sent.append(to) or '<mid-%d@test>' % len(sent))
    argv = sys.argv; sys.argv = ['cadence.py']
    buf = io.StringIO(); o = sys.stdout; sys.stdout = buf
    try:
        rc = CD.main()
    finally:
        sys.stdout = o; sys.argv = argv
    return rc, sent, buf.getvalue(), json.load(open(CD.STATE))


rc, sent, out, st = run(leads, {A.lower()})
rec('2a the sibling case is NOT mailed', 'shared@example.com' not in sent, (sent, out[-500:]))
rec('2b it is marked suppressed with the reason', st[B]['status'] == 'suppressed'
    and 'same owner' in st[B]['log'][-1]['ev'], st[B])
rec('2c an unrelated sequence still sends', 'unrelated@example.com' in sent, (sent, out[-500:]))
# phone-only stop: the ledger holds only '#3055550301' (lead A's number); cadence's email gate drops '#' keys
json.dump({'_dealflow_notes': 1, 'notes': {'#3055550301': {'optout': '2099-01-01'}}}, open(CD.OPTOUTS, 'w'))
import optout_sync as _OS
_OS.NOTES = os.path.join(ctmp, 'worker_notes.json')
rc4, sent4, out4, st4 = run(leads, set())
rec('2f a PHONE-ONLY stop reaches the owner\'s other case in cadence', 'shared@example.com' not in sent4
    and st4[B]['status'] == 'suppressed', (sent4, out4[-400:]))
rec('2g ...and an unrelated sequence still sends', 'unrelated@example.com' in sent4, sent4)
json.dump({'_dealflow_notes': 1, 'notes': {}}, open(CD.OPTOUTS, 'w'))
rc2, sent2, out2, st2 = run(leads, set())
rec('2d control: with no ledger entry the same case sends', 'shared@example.com' in sent2, (sent2, out2[-500:]))

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
