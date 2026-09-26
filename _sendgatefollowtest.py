#!/usr/bin/env python3
"""_sendgatefollowtest.py — three of the four follow-ups from the #83 reviews (Greptile, 2026-09-26).
(The fourth, cadence through the gate, is _cadencegatetest.py: cadence.py is the reserved
suppression surface in CLAUDE.md, so it ships separately.)

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

# ---- 2. sender-slot race ----------------------------------------------------------------------
# ledger: ft1 already sent 2 first touches (full), ft2 sent 1 (one slot left)
led = [{'d': TODAY.isoformat(), 'ch': 'email', 'from': 'ft1@warm-a.example', 'to': 'x%d@example.com' % i,
        'message_id': '<m%d>' % i, 'touch': 'first'} for i in range(2)]
led.append({'d': TODAY.isoformat(), 'ch': 'email', 'from': 'ft2@warm-b.example', 'to': 'y@example.com',
            'message_id': '<m9>', 'touch': 'first'})
json.dump(led, open(S.SENT_LEDGER, 'w'))
S._FT_SLOTS.clear()
got = []
barrier = threading.Barrier(8)
def worker():
    barrier.wait()
    got.append(S._reserve_first_touch_from(CFG)[0])
ths = [threading.Thread(target=worker) for _ in range(8)]
[t.start() for t in ths]; [t.join() for t in ths]
winners = [g for g in got if g]
rec('race: 8 concurrent first touches, one slot left -> exactly one sender reserved',
    winners == ['ft2@warm-b.example'], got)
rec('race: the reservation is visible to the picker', S._pick_first_touch_from(CFG)[0] == '', S._FT_SLOTS)
S._release_first_touch_slot('ft2@warm-b.example')
rec('race: released (auth failure / counted by the ledger) -> the slot is free again',
    S._pick_first_touch_from(CFG)[0] == 'ft2@warm-b.example', S._FT_SLOTS)
S._FT_SLOTS[('1999-01-01', 'ft2@warm-b.example')] = 5
rec('race: a reservation from another day never binds today',
    S._pick_first_touch_from(CFG)[0] == 'ft2@warm-b.example', S._FT_SLOTS)
S._FT_SLOTS.clear()
src = io.open(os.path.join(HERE, 'send_server.py'), encoding='utf-8').read()
rec('race: /send reserves (not just picks) its first-touch sender', '_reserve_first_touch_from(_cfg)' in src)
i_led = src.find("'touch': meta.get('touch') or '',")
i_rel = src.find('_release_first_touch_slot(_ft_slot)', i_led)
rec('race: the slot is released only AFTER the ledger row is written', 0 < i_led < i_rel < i_led + 400)
i_gen = src.find("# NOTE: the claim is deliberately NOT released on a generic send failure")
rec('race: an unknown-outcome SMTP failure keeps the slot (counted high, never low)',
    i_gen > 0 and '_release_first_touch_slot' not in src[i_gen:i_gen + 900])

# ---- 4. /health pool uses the worker's allowlist ------------------------------------------------
json.dump([], open(S.SENT_LEDGER, 'w'))
def w(name, obj): json.dump(obj, open(os.path.join(tmp, name), 'w'))
w('leads_final.json', [{'Case #': '2099-000101-CA-01'}, {'Case #': '2099-000102-CA-01'},
                       {'Case #': '2099-000103-CA-01'}])
w('skiptrace_results.json', {
    '2099-000101-CA-01': {'emails': ['owner1@gmail.com']},
    '2099-000102-CA-01': {'emails': ['owner2@lawfirm-example.com']},              # off the allowlist
    '2099-000103-CA-01': {'emails': ['owner3@lawfirm-example.com', 'owner3@yahoo.com']}})
zb = {'v': 'ok', 'why': 'zerobounce:valid', 'd': TODAY.isoformat()}
w('verified_emails.json', {'owner1@gmail.com': zb, 'owner2@lawfirm-example.com': zb,
                           'owner3@lawfirm-example.com': zb, 'owner3@yahoo.com': zb})
w('bounced_emails.json', {}); w('replies.json', {}); w('optouts.json', {'notes': {}})
S._FT_CACHE.update(key=None, val=None)
h = S._first_touch_queue_health()
rec('health: a lead with only an off-allowlist address is not in the pool', h.get('leads') == 2, h)
rec('health: off-allowlist addresses are counted, not hidden', h.get('addresses_not_mailable') == 2, h)
rec('health: the pool says it is the worker-mailable pool', h.get('pool') == 'worker_mailable', h)
rec('health: sendable counts only addresses the worker can offer', h.get('leads_sendable') == 2
    and h.get('addresses') == 2, h)
os.remove(os.path.join(tmp, 'tracker_template.html'))
S._WM_CACHE.update(mtime=None, val=None); S._FT_CACHE.update(key=None, val=None)
h = S._first_touch_queue_health()
rec('health: template unreadable -> labelled unfiltered, nothing dropped', h.get('pool') == 'unfiltered'
    and h.get('leads') == 3 and h.get('addresses_not_mailable') is None, h)
shutil.copy(os.path.join(HERE, 'tracker_template.html'), os.path.join(tmp, 'tracker_template.html'))
d = S._worker_mailable_domains()
rec('health: the allowlist is read from the template (gmail in, *.rr.com family in, firm out)',
    d and S._worker_mailable('a@gmail.com', d) and S._worker_mailable('a@cfl.rr.com', d)
    and not S._worker_mailable('a@lawfirm-example.com', d))

# ---- 1. the board archives the mailed address ------------------------------------------------------
tpl = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
rec('board: the worker passes the bridge\'s actual recipient in the sent mark',
    'post("sent", {mid:x.j.message_id||"", via:"bridge", to:String(x.j.to||""),' in tpl)
i = tpl.find("} else if(d.k === 'sent'){")
blk = tpl[i:i + 3500]
rec('board: the sent handler reads extra.to', "var sentTo = String((d.extra && d.extra.to) || '')" in blk)
rec('board: the Proof Sheet row names the mailed address first', 'to:sentTo||LE.to||em2' in blk)
rec('board: the offered-but-not-mailed To is kept as `offered`', 'offered:(sentTo && LE.to' in blk)
rec('board: the worker log names the mailed address', 'if(sentTo) em2 = sentTo;' in blk)

shutil.rmtree(tmp, ignore_errors=True)
print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
