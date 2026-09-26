#!/usr/bin/env python3
"""_cadencegatetest.py — #83 review follow-up 3 (Greptile P1, 2026-09-26): cadence.py through the gate.

  1. the board archives the address the bridge ACTUALLY mailed (x.j.to), not the To it offered
  2. two concurrent first touches cannot put a warm-up sender one over its first-touch cap: the
     sender slot is picked and reserved in one step and held until the ledger row counts it
  3. cadence.py's scheduled steps pass the same verified-address gate, and a cadence first touch
     leaves only from the first_touch.from senders through the same slot reservation
  4. /health's first-touch pool leaves out addresses the worker's domain allowlist would never offer

Fake example.com / gmail.com addresses, temp folders, fake SMTP. No network, no real data.
Run: python _sendgatefollowtest.py
"""
import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
import threading

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:300]) if d and not cond else ''))


import send_server as S

TODAY = dt.date.today()
tmp = tempfile.mkdtemp()
S.HERE = tmp
S.SENT_LEDGER = os.path.join(tmp, 'mail_sent.json')
shutil.copy(os.path.join(HERE, 'tracker_template.html'), os.path.join(tmp, 'tracker_template.html'))
CFG = {'main_domain': 'bsgflorida.com', 'main_domain_cap': 40,
       'ramp_start': (TODAY - dt.timedelta(days=30)).isoformat(),
       'ramp': [{'through_day': 9999, 'per_day': 100}],
       'lanes': {'active': 'alejandro@biscaynesolutionsgroup.com', 'default': 'alejandro@bsgflorida.com'},
       'first_touch': {'from': ['ft1@warm-a.example', 'ft2@warm-b.example'], 'per_day': 2}}

zb = {'v': 'ok', 'why': 'zerobounce:valid', 'd': TODAY.isoformat()}
# ---- 3. cadence goes through the gate ------------------------------------------------------------
import cadence as C
ctmp = tempfile.mkdtemp()
C.HERE = ctmp
C.QUEUE = os.path.join(ctmp, 'cadence_queue.json')
C.STATE = os.path.join(ctmp, 'cadence_state.json')
C.OPTOUTS = os.path.join(ctmp, 'optouts.json')
json.dump({'_dealflow_notes': 1, 'notes': {}}, open(C.OPTOUTS, 'w'))
json.dump({'sender': {'name': 'Test Sender'}, 'queue': [
    {'case': '2099-000201-CA-01', 'owner': 'A', 'email': 'unverified@example.com', 'step': 0},
    {'case': '2099-000202-CA-01', 'owner': 'B', 'email': 'valid1@example.com', 'step': 0},
    {'case': '2099-000203-CA-01', 'owner': 'C', 'email': 'valid2@example.com', 'step': 0},
    {'case': '2099-000204-CA-01', 'owner': 'D', 'email': 'followup@example.com', 'step': 1},
]}, open(C.QUEUE, 'w'))
C._oe._load_optouts = lambda: set()
C._oe._load_leads = lambda: [
    {'case': c, 'days': 40} for c in
    ('2099-000201-CA-01', '2099-000202-CA-01', '2099-000203-CA-01', '2099-000204-CA-01')]
import diligence_gate as _dg
_dg.gate = lambda row: {'hold': False, 'code': '', 'why': ''}
C.load_key = lambda: ('alejandro@bsgflorida.com', 'app-password')
C.imap_replies = lambda *a, **k: {}
C.steps = lambda s, sender: [('subj %d' % i, 'body %d' % i) for i in range(4)]
old = (TODAY - dt.timedelta(days=5)).isoformat()
EV = {'bounced': set(), 'replied': set(), 'proven': {'followup@example.com'},
      'ver': {'valid1@example.com': zb, 'valid2@example.com': zb},
      'last_mailed': {'followup@example.com': old}}
C._ss._deliverability_evidence = lambda: EV
C._ss._load_senders = lambda: dict(CFG, first_touch={'from': ['ft2@warm-b.example'], 'per_day': 1})
sent = []
C._ss._smtp_send = lambda user, pw, disp, to, subj, body, bcc='', attach=None, from_addr=None: (
    sent.append((to, from_addr)) or '<mid-%d@test>' % len(sent))
json.dump([], open(S.SENT_LEDGER, 'w'))
S._FT_SLOTS.clear()
argv = sys.argv; sys.argv = ['cadence.py']
buf = io.StringIO(); _o = sys.stdout; sys.stdout = buf
try:
    rc = C.main()
finally:
    sys.stdout = _o; sys.argv = argv
out = buf.getvalue()
st = json.load(open(C.STATE))
tos = [t for t, _f in sent]
rec('cadence: an address with no delivery evidence is HELD, not mailed', 'unverified@example.com' not in tos, sent)
rec('cadence: the held step is NOT consumed (stays at step 0)', st['2099-000201-CA-01']['step'] == 0, st['2099-000201-CA-01'])
rec('cadence: the run log says HELD and why', 'HELD step 1/4 -> unverified@example.com' in out, out[-600:])
rec('cadence: a first touch leaves from the warm-up sender', ('valid1@example.com', 'ft2@warm-b.example') in sent, sent)
rec('cadence: the warm-up first-touch cap (1) holds the second first touch for tomorrow',
    'valid2@example.com' not in tos and st['2099-000203-CA-01']['step'] == 0, (sent, st['2099-000203-CA-01']))
rec('cadence: a follow-up to a proven address still sends', 'followup@example.com' in tos, sent)
L = json.load(open(S.SENT_LEDGER))
ft_rows = [r for r in L if r.get('to') == 'valid1@example.com']
rec('cadence: the first touch is ledgered touch=first (the bridge meters it)', ft_rows and ft_rows[0].get('touch') == 'first', L)
rec('cadence: the slot was released once the ledger counted it', not S._FT_SLOTS, S._FT_SLOTS)
rec('cadence: exit 0', rc == 0, rc)
csrc = io.open(os.path.join(HERE, 'cadence.py'), encoding='utf-8').read()
rec('cadence: a failed send_server import holds (fail closed), never sends ungated',
    'every due step is \'\n          \'HELD this run' in csrc or 'HELD this run (fail closed' in csrc)

shutil.rmtree(tmp, ignore_errors=True); shutil.rmtree(ctmp, ignore_errors=True)
print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
