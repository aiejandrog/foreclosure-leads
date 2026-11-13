"""The first-touch bounce pause is GONE everywhere, and a broken merge is still not a send.

2026-11-13 (Alejandro): the first-email gates were removed. This file used to prove /send, cadence
and outreach_email all asked one shared pause. It now proves none of them holds a first email on
the bounce rate, in a fixture the old rule would have paused, and that the two things that must
still stop a send here do: a merge that did not fill, and a bounce list that will not parse.

These checks use example.com, 2099- case numbers, and EXAMPLE AVE. No network.
"""
import datetime as dt
import io
import json
import os
import sys
# Follow-up sends are off unless this is exactly 1. This fixture expects one to leave.
os.environ['DEALFLOW_FOLLOWUPS_ON'] = '1'
# PR #192: cadence asks the send bridge's stay verdict (bk_lookup.contact_blocked_reason) before it mails.
# These fixtures carry no docket data and are not about the stay gate (_staygatesfailclosedtest owns it),
# so the verdict is stubbed clear here.
import bk_lookup as _BKL_STUB
_BKL_STUB.contact_blocked_reason = lambda case, here=None: (False, '')
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:400]) if d and not cond else ''))


import send_server as S

TODAY = dt.date.today().isoformat()
OLD = (dt.date.today() - dt.timedelta(days=5)).isoformat()

# ---- the shared reading ------------------------------------------------------------------------
empty = tempfile.mkdtemp()
S.HERE = empty
S.SENT_LEDGER = os.path.join(empty, 'mail_sent.json')
json.dump([], open(S.SENT_LEDGER, 'w'))
_h = S._bounce_health()
rec('a missing bounce list: measured as unmeasured, enforced as clear, nothing blocked',
    _h.get('measured_rule') == 'unmeasured' and _h.get('ft_rule') == 'clear' and _h.get('blocked') is False, _h)
rec('the pause helpers no longer exist on the bridge',
    not any(hasattr(S, n) for n in ('first_touch_paused', '_first_touch_bounce_paused',
                                    '_ft_restart_ceiling', '_restart_ceiling_on',
                                    '_probe_quota_left', 'PROBE_DAILY_CAP')))

# ---- cadence: in a fixture the old rule paused, the first touch now sends ----------------------
paused = tempfile.mkdtemp()
S.HERE = paused
S.SENT_LEDGER = os.path.join(paused, 'mail_sent.json')
rows, dead = [], []
for i in range(10):
    addr = ('dead%d@example.com' % i) if i < 2 else ('live%d@example.com' % i)
    if i < 2:
        dead.append(addr)
    rows.append({
        'd': TODAY, 'ts_utc': TODAY + 'T15:00:00+00:00', 'ch': 'email',
        'to': addr, 'from': 'ft@warm.example', 'message_id': '<m%d@example.com>' % i,
        'touch': 'first',
    })
json.dump(rows, open(S.SENT_LEDGER, 'w'))
json.dump(dead, open(os.path.join(paused, 'bounced_emails.json'), 'w'))
_h = S._bounce_health()
rec('the fixture is one the old rule would have paused, and it blocks nothing now',
    _h.get('measured_rule') == 'paused' and _h.get('ft_rule') == 'clear'
    and _h.get('blocked') is False and _h.get('day_blocked') is False, _h)

import cadence as C
import diligence_gate as DG
CFG = {
    'main_domain': 'bsgflorida.com', 'main_domain_cap': 40,
    'ramp_start': (dt.date.today() - dt.timedelta(days=30)).isoformat(),
    'ramp': [{'through_day': 9999, 'per_day': 100}],
    'lanes': {'early': 'early@warm.example', 'active': 'active@corp.example',
              'default': 'alejandro@bsgflorida.com'},
    # per_day 100: the fixture has 10 first touches today from this sender, and the sender's own
    # daily cap is a gate that STAYS. Room is left so this file tests the pause, not that cap.
    'first_touch': {'from': ['ft@warm.example'], 'per_day': 100},
}
ZB = {'v': 'ok', 'why': 'zerobounce:valid', 'd': TODAY}


def _cadence_run(smtp, here_bounce=None):
    ctmp = tempfile.mkdtemp()
    C.HERE = ctmp
    C.QUEUE = os.path.join(ctmp, 'cadence_queue.json')
    C.STATE = os.path.join(ctmp, 'cadence_state.json')
    C.OPTOUTS = os.path.join(ctmp, 'optouts.json')
    C._lock_dir = lambda: os.path.join(ctmp, 'cadence.lock')
    json.dump({'_dealflow_notes': 1, 'notes': {}}, open(C.OPTOUTS, 'w'))
    json.dump({'date': TODAY, 'state': 'finished', 'ok': True,
               'started_at': 0, 'finished_at': 1, 'steps': []},
              open(os.path.join(ctmp, 'sync_status.json'), 'w'))
    json.dump({} if here_bounce is None else here_bounce,
              open(os.path.join(ctmp, 'bounced_emails.json'), 'w'))
    json.dump({'sender': {'name': 'Test Sender'}, 'queue': [
        {'case': '2099-000301-CA-01', 'owner': 'A', 'email': 'first@example.com', 'step': 0},
        {'case': '2099-000302-CA-01', 'owner': 'B', 'email': 'follow@example.com', 'step': 1},
    ]}, open(C.QUEUE, 'w'))
    C._oe._load_optouts = lambda: set()
    C._oe._load_leads = lambda: [
        {'case': '2099-000301-CA-01', 'days': 40},
        {'case': '2099-000302-CA-01', 'days': 40},
    ]
    DG.gate = lambda row: {'hold': False, 'code': '', 'why': ''}
    C.load_key = lambda: ('alejandro@bsgflorida.com', 'app-password')
    C.imap_replies = lambda *a, **k: {}
    C.steps = lambda s, sender: [('subj %d' % i, 'body %d about 1 EXAMPLE AVE' % i) for i in range(4)]
    C._ss._load_senders = lambda: dict(CFG)
    C._ss._deliverability_evidence = lambda: {
        'bounced': set(), 'replied': set(), 'proven': {'follow@example.com'},
        'ver': {'first@example.com': ZB},
        'last_mailed': {'follow@example.com': OLD},
    }
    C._ss._smtp_send = smtp
    argv, buf = sys.argv, io.StringIO()
    sys.argv = ['cadence.py']
    _o = sys.stdout
    sys.stdout = buf
    try:
        rc = C.main()
    finally:
        sys.stdout = _o
        sys.argv = argv
    st = json.load(open(C.STATE))
    return rc, buf.getvalue(), st


sent = []
rc, out, st = _cadence_run(
    lambda user, pw, disp, to, subj, body, bcc='', attach=None, from_addr=None:
        sent.append(to) or '<mid@example.com>')
rec('cadence sends the first touch: no bounce pause any more',
    'first@example.com' in sent and 'bounce pause' not in out, out[-500:])
rec('cadence advances the first-touch step it sent',
    st['2099-000301-CA-01']['step'] == 1, st['2099-000301-CA-01'])
rec('cadence still sends the follow-up',
    'follow@example.com' in sent and st['2099-000302-CA-01']['step'] == 2, (sent, st['2099-000302-CA-01']))
rec('cadence run exits 0', rc == 0, rc)

# ---- cadence: a merge refusal does not advance the step ---------------------------------------
clear = tempfile.mkdtemp()
S.HERE = clear
S.SENT_LEDGER = os.path.join(clear, 'mail_sent.json')
json.dump([], open(S.SENT_LEDGER, 'w'))
S._FT_SLOTS.clear()
sent2 = []


def _smtp_merge(user, pw, disp, to, subj, body, bcc='', attach=None, from_addr=None):
    if to == 'first@example.com':
        raise C._MG.UnsendableError('the subject names the fallback "your property"')
    sent2.append(to)
    return '<mid2@example.com>'


rc, out, st = _cadence_run(_smtp_merge)
rec('cadence refuses a broken merge and does not consume the step',
    'first@example.com' not in sent2 and st['2099-000301-CA-01']['step'] == 0
    and 'NOT consumed' in out, out[-500:])
rec('cadence still sends the other step after a merge refusal',
    'follow@example.com' in sent2, sent2)

# ---- outreach_email: holds only on an unreadable bounce list ----------------------------------
import outreach_email as OE
otmp = tempfile.mkdtemp()
OE.PREVIEW_FILE = os.path.join(otmp, 'preview.html')
OE.SENT_LEDGER = os.path.join(otmp, 'mail_sent.json')
OE._WORKER_HIST = None
OE._load_credentials = lambda: ('login@example.com', 'pw')
OE._load_sender = lambda: {'name': 'Test Sender', 'addr': '1 EXAMPLE AVE, MIAMI, FL 33101'}
OE._load_leads = lambda: [{
    'case': '2099-000201-CA-01', 'owners': 'DOE, JANE', 'tier': 'A', 'days': 30,
    'emails': ['jane@example.com'], 'addr': '1 EXAMPLE AVE',
}]
OE._load_optouts = lambda: set()
OE._load_ledger = lambda: []
OE._eligible = lambda *a, **k: (True, '')
mailed = []
OE._smtp_send = lambda *a, **k: mailed.append(a[3] if len(a) > 3 else k.get('to_addr')) or (
    '<mid-oe@example.com>', '250 ok')


def _oe(argv):
    mailed.clear()
    OE._WORKER_HIST = None
    old, buf = sys.argv, io.StringIO()
    sys.argv = ['outreach_email.py'] + argv
    _o = sys.stdout
    sys.stdout = buf
    try:
        rc = OE.main()
    finally:
        sys.stdout = _o
        sys.argv = old
    return rc, buf.getvalue()


S.HERE = empty
S.SENT_LEDGER = os.path.join(empty, 'mail_sent.json')
_real_bh = S._bounce_health
S._bounce_health = lambda: {'list_unreadable': True, 'blocked': True, 'ft_rule': 'clear'}
rc, out = _oe(['--send', '--limit', '1'])
rec('outreach_email --send holds every first touch when the bounce list is unreadable',
    rc == 0 and mailed == [] and 'HELD before send' in out and 'unreadable' in out, out[-400:])
rec('that hold is reported on a dry run too, and still sends nothing',
    _oe(['--limit', '1'])[1].find('would hold every first touch') >= 0 and mailed == [])

S._bounce_health = lambda: (_ for _ in ()).throw(RuntimeError('no file'))
rc, out = _oe(['--send', '--limit', '1'])
rec('outreach_email fails closed when the bounce list cannot be read at all',
    rc == 0 and mailed == [] and 'could not be read' in out, out[-400:])

# A reading the OLD rule would have paused on (2 of 10 bounced today) holds nothing now.
S._bounce_health = lambda: {'list_unreadable': False, 'blocked': False, 'ft_rule': 'clear',
                            'measured_rule': 'paused', 'measured_why': 'hard_bounces'}
OE._compose_single = lambda r, snd, lang='en': {
    'subj': 'Regarding your property at your property',
    'body': "Hi,\n\nA note.\n\nIf now's not a good time, just tell me and I won't reach out again.",
}
rc, out = _oe(['--send', '--limit', '1'])
rec('outreach_email skips a blank-street subject and does not call SMTP',
    rc == 0 and mailed == [] and 'SKIP merge' in out, out[-400:])

OE._compose_single = lambda r, snd, lang='en': {
    'subj': 'Regarding your property at 1 EXAMPLE AVE',
    'body': "Hi Jane,\n\nA note about 1 EXAMPLE AVE.\n\n"
            "If now's not a good time, just tell me and I won't reach out again.",
}
rc, out = _oe(['--send', '--limit', '1'])
row = {}
if os.path.exists(OE.SENT_LEDGER):
    rows = json.load(open(OE.SENT_LEDGER, encoding='utf-8'))
    row = rows[-1] if rows else {}
rec('a first touch sends on a reading the old rule would have paused, and the ledger names this tool',
    rc == 0 and mailed == ['jane@example.com']
    and row.get('source') == 'outreach_email' and row.get('touch') == 'first'
    and row.get('smtp_resp'), (mailed, row))

S._bounce_health = _real_bh

# ---- wiring ------------------------------------------------------------------------------------
osrc = io.open(os.path.join(HERE, 'outreach_email.py'), encoding='utf-8').read()
send = osrc.split('# ---- send', 1)[1]
rec('outreach_email asks hold_first_touches before the send loop',
    'hold_first_touches()' in send.split('for i, e in enumerate', 1)[0])
rec('that hold returns before SMTP', send.find('if _ft_hold') < send.find('_smtp_send'))
csrc = io.open(os.path.join(HERE, 'cadence.py'), encoding='utf-8').read()
rec('cadence no longer asks for a first-email pause', 'first_touch_paused' not in csrc and '_ft_paused' not in csrc)
ssrc = io.open(os.path.join(HERE, 'send_server.py'), encoding='utf-8').read()
i_skip = ssrc.find('SEND SKIP [merge]')
i_mem = ssrc.find('_mark_memory_only()', i_skip)
rec('/send turns a merge refusal into a skip, not a maybe-sent',
    i_skip > 0 and i_mem > i_skip and "'blocked': 'merge_fields'" in ssrc[i_skip:i_mem]
    and '451' in ssrc[i_skip:i_mem])
rec('the /send first-email gates are gone from the source',
    not any(x in ssrc for x in ('first_touch_paused(', '_ft_exempt', 'Verified-only mode is ON',
                                "'blocked': 'probe_quota_exhausted'", 'no warm-up sender configured during slow restart')))
rec('what must still refuse a send is still in /send',
    all(x in ssrc for x in ("'blocked': 'merge_fields'", 'bounce list is UNREADABLE', "'optout'", '_stay_gate')))

sched = []
for root, dirs, files in os.walk(HERE):
    if '.git' in dirs:
        dirs.remove('.git')
    for fn in files:
        if not fn.endswith(('.bat', '.xml', '.yml', '.yaml', '.ps1')):
            continue
        path = os.path.join(root, fn)
        text = io.open(path, encoding='utf-8', errors='replace').read()
        if 'outreach_email.py' in text:
            sched.append(os.path.relpath(path, HERE))
rec('no scheduled task or workflow names outreach_email.py', not sched, sched)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
