#!/usr/bin/env python3
"""DEALFLOW_FOLLOWUPS_ON gates follow-up email and does not gate a first touch.

Fake 2099- case numbers and example.com addresses. No network, no real people.
Run: python _followupswitchtest.py
"""
import datetime as dt
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ok, bad = [], []
_SAVED = os.environ.pop('DEALFLOW_FOLLOWUPS_ON', None)


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:400]) if d and not cond else ''))


class _Boom(object):
    def __contains__(self, key):
        raise OSError('denied')

    def get(self, key, default=None):
        raise OSError('denied')


import send_server as S

rec('unset is off', S.followups_status({}) == ('off', ''))
rec('exactly 1 is on', S.followups_status({'DEALFLOW_FOLLOWUPS_ON': '1'}) == ('on', ''))
for raw in ('1 ', '1\n', 'true', 'yes', '0', ''):
    mode, why = S.followups_status({'DEALFLOW_FOLLOWUPS_ON': raw})
    rec('value %r is fail-closed' % raw, mode == 'bad' and 'not exactly 1' in why, (mode, why))
mode, why = S.followups_status(_Boom())
rec('an unreadable environment is fail-closed', mode == 'bad' and 'unreadable' in why, (mode, why))
mode, why = S.followups_status({'DEALFLOW_FOLLOWUPS_ON': 'owner@example.com'})
rec('a value with an address is redacted', mode == 'bad' and 'example.com' not in why and 'redacted' in why, why)

rec('a cold first touch is not a follow-up', S.followup_due(0, True, 'About 1 EXAMPLE AVE') is False)
rec('a Re: subject is a follow-up', S.followup_due(0, True, 'Re: About 1 EXAMPLE AVE') is True)
rec('Fwd: and Fw: are follow-ups',
    S.followup_due(None, True, 'Fwd: x') and S.followup_due(None, True, '  Fw: x'))
rec('cadence step 1 is a follow-up even when it still looks like a first touch',
    S.followup_due(1, True, 'About 1 EXAMPLE AVE') is True)
rec('an address already mailed is a follow-up', S.followup_due(None, False, 'About 1 EXAMPLE AVE') is True)
rec('an unreadable step is a follow-up', S.followup_due('nope', True, 'About 1 EXAMPLE AVE') is True)
rec('the dry-run line is the case and the step',
    S.followup_list_line('2099-000401-CA-01', 2) == '2099-000401-CA-01 2')
rec('an address is not a case number on that line', S.followup_list_line('a@example.com', 2) == '- 2')
rec('step 0 with no prior send prints as step 2', S.followup_step_number({}, 0) == 2)
rec('a 0-based cadence index prints one higher', S.followup_step_number({'step': 1}, 0) == 2)

os.environ.pop('DEALFLOW_FOLLOWUPS_ON', None)
rec('switch off blocks a Re: send',
    S.followup_block(True, 'Re: About 1 EXAMPLE AVE', False, None)[0] == 'off')
rec('switch off does not block a cold first touch',
    S.followup_block(True, 'About 1 EXAMPLE AVE', False, None) is None)
rec('a test send is not a follow-up',
    S.followup_block(False, 'Re: About 1 EXAMPLE AVE', True, 1) is None)
os.environ['DEALFLOW_FOLLOWUPS_ON'] = '1'
# PR #192: cadence asks the send bridge's stay verdict (bk_lookup.contact_blocked_reason) before it mails.
# These fixtures carry no docket data and are not about the stay gate (_staygatesfailclosedtest owns it),
# so the verdict is stubbed clear here.
import bk_lookup as _BKL_STUB
_BKL_STUB.contact_blocked_reason = lambda case, here=None: (False, '')
rec('switch on lets a follow-up proceed',
    S.followup_block(False, 'Re: About 1 EXAMPLE AVE', False, 1) is None)
os.environ['DEALFLOW_FOLLOWUPS_ON'] = 'yes'
blk = S.followup_block(False, 'Re: About 1 EXAMPLE AVE', False, 1)
rec('a bad value blocks the follow-up', blk and blk[0] == 'bad', blk)
os.environ.pop('DEALFLOW_FOLLOWUPS_ON', None)


# ---- cadence hand run ------------------------------------------------------------------------
import cadence as C
import diligence_gate as DG

TODAY = dt.date.today()
OLD = (TODAY - dt.timedelta(days=5)).isoformat()
CASE_FU = '2099-000401-CA-01'
CASE_FT = '2099-000402-CA-01'
FU_EMAIL = 'follow@example.com'
FT_EMAIL = 'first@example.com'


def _arm(ctmp):
    C.HERE = ctmp
    C.QUEUE = os.path.join(ctmp, 'cadence_queue.json')
    C.STATE = os.path.join(ctmp, 'cadence_state.json')
    C.OPTOUTS = os.path.join(ctmp, 'optouts.json')
    C._lock_dir = lambda: os.path.join(ctmp, 'cadence.lock')
    S.HERE = ctmp
    S.SENT_LEDGER = os.path.join(ctmp, 'mail_sent.json')
    json.dump([], open(S.SENT_LEDGER, 'w'))
    json.dump({}, open(os.path.join(ctmp, 'bounced_emails.json'), 'w'))
    json.dump({'_dealflow_notes': 1, 'notes': {}}, open(C.OPTOUTS, 'w'))
    json.dump({'date': TODAY.isoformat(), 'state': 'finished', 'ok': True,
               'started_at': 0, 'finished_at': 1, 'steps': []},
              open(os.path.join(ctmp, 'sync_status.json'), 'w'))
    C._oe._load_optouts = lambda: set()
    C._oe._load_leads = lambda: [
        {'case': CASE_FU, 'days': 40}, {'case': CASE_FT, 'days': 40}]
    DG.gate = lambda row: {'hold': False, 'code': '', 'why': ''}
    C.load_key = lambda: ('login@example.com', 'app-password')
    C.imap_replies = lambda *a, **k: {}
    C.steps = lambda s, sender: [
        ('subj %d' % i, 'body %d about 1 EXAMPLE AVE' % i) for i in range(4)]
    C._ss._load_senders = lambda: {
        'main_domain': 'example.com', 'main_domain_cap': 40,
        'lanes': {'default': 'login@example.com'},
        'first_touch': {'from': [], 'per_day': 0}}
    C._ss._senders_active = lambda user, cfg: False
    C._ss._deliverability_evidence = lambda: {
        'bounced': set(), 'replied': set(),
        'proven': {FU_EMAIL},
        'ver': {FT_EMAIL: {'v': 'ok', 'why': 'zerobounce:valid', 'd': TODAY.isoformat()},
                FU_EMAIL: {'v': 'ok', 'why': 'zerobounce:valid', 'd': TODAY.isoformat()}},
        'last_mailed': {FU_EMAIL: OLD}}


def cadence_hand(env_value, rows, state):
    ctmp = tempfile.mkdtemp()
    _arm(ctmp)
    json.dump({'sender': {'name': 'Test Sender'}, 'queue': rows}, open(C.QUEUE, 'w'))
    json.dump(state, open(C.STATE, 'w'))
    before = json.load(open(C.STATE))
    sent = []
    C._ss._smtp_send = lambda *a, **k: sent.append(a[3] if len(a) > 3 else k.get('to_addr')) or '<mid@example.com>'
    if env_value is None:
        os.environ.pop('DEALFLOW_FOLLOWUPS_ON', None)
    else:
        os.environ['DEALFLOW_FOLLOWUPS_ON'] = env_value
    argv, buf = sys.argv, io.StringIO()
    sys.argv = ['cadence.py']
    _o = sys.stdout
    sys.stdout = buf
    try:
        rc = C.main()
    finally:
        sys.stdout = _o
        sys.argv = argv
        os.environ.pop('DEALFLOW_FOLLOWUPS_ON', None)
    return rc, sent, buf.getvalue(), json.load(open(C.STATE)), before


def _fu_state(step=1):
    return {
        'step': step, 'next': TODAY.isoformat(), 'status': 'active',
        'owner': 'A', 'email': FU_EMAIL, 'addr': '1 EXAMPLE AVE',
        'log': [{'d': OLD, 'ev': 'sent step 1'}],
    }


rc, sent, out, st, before = cadence_hand(None, [
    {'case': CASE_FU, 'owner': 'A', 'email': FU_EMAIL, 'addr': '1 EXAMPLE AVE', 'step': 1},
], {CASE_FU: _fu_state()})
rec('hand run with the switch off sends nothing', sent == [] and rc == 0, (rc, sent, out[-500:]))
rec('hand run with the switch off does not advance the step',
    st[CASE_FU]['step'] == 1 and st[CASE_FU]['next'] == before[CASE_FU]['next']
    and st[CASE_FU]['log'] == before[CASE_FU]['log']
    and not any(str(ev.get('ev') or '').startswith('sent step') and ev.get('d') == TODAY.isoformat()
                for ev in st[CASE_FU]['log']), st[CASE_FU])
rec('the dry-run line is the case and the step only',
    ('%s 2' % CASE_FU) in out.splitlines() and FU_EMAIL not in out, out[-500:])

rc, sent, out, st, before = cadence_hand('1', [
    {'case': CASE_FU, 'owner': 'A', 'email': FU_EMAIL, 'addr': '1 EXAMPLE AVE', 'step': 1},
], {CASE_FU: _fu_state()})
rec('hand run with the switch on still sends the follow-up',
    sent == [FU_EMAIL] and rc == 0 and st[CASE_FU]['step'] == 2, (rc, sent, st[CASE_FU]))

rc, sent, out, st, before = cadence_hand('yes', [
    {'case': CASE_FU, 'owner': 'A', 'email': FU_EMAIL, 'addr': '1 EXAMPLE AVE', 'step': 1},
], {CASE_FU: _fu_state()})
rec('a bad value sends nothing and exits non-zero',
    sent == [] and rc == 2 and st[CASE_FU]['step'] == 1 and FU_EMAIL not in out, (rc, sent, out[-400:]))

rc, sent, out, st, before = cadence_hand('1 ', [
    {'case': CASE_FU, 'owner': 'A', 'email': FU_EMAIL, 'addr': '1 EXAMPLE AVE', 'step': 1},
], {CASE_FU: _fu_state()})
rec('a padded 1 is not on', sent == [] and rc == 2 and st[CASE_FU]['step'] == 1, (rc, sent))

_later = _fu_state()
_later['next'] = (TODAY + dt.timedelta(days=3)).isoformat()
rc, sent, out, st, before = cadence_hand('yes', [
    {'case': CASE_FU, 'owner': 'A', 'email': FU_EMAIL, 'addr': '1 EXAMPLE AVE', 'step': 1},
], {CASE_FU: _later})
rec('a bad value exits non-zero even when no follow-up is due',
    rc == 2 and sent == [] and st[CASE_FU]['next'] == _later['next'] and st[CASE_FU]['step'] == 1,
    (rc, sent, st[CASE_FU]))
rc, sent, out, st, before = cadence_hand(None, [
    {'case': CASE_FU, 'owner': 'A', 'email': FU_EMAIL, 'addr': '1 EXAMPLE AVE', 'step': 1},
], {CASE_FU: _later})
rec('switch off with nothing due exits 0 and lists nothing',
    rc == 0 and sent == [] and ('%s 2' % CASE_FU) not in out.splitlines(), (rc, out[-300:]))

rc, sent, out, st, before = cadence_hand(None, [
    {'case': CASE_FT, 'owner': 'B', 'email': FT_EMAIL, 'addr': '1 EXAMPLE AVE', 'step': 0},
], {CASE_FT: {
    'step': 0, 'next': TODAY.isoformat(), 'status': 'active',
    'owner': 'B', 'email': FT_EMAIL, 'addr': '1 EXAMPLE AVE', 'log': [],
}})
rec('a first touch still sends while follow-ups are off',
    sent == [FT_EMAIL] and rc == 0 and st[CASE_FT]['step'] == 1, (rc, sent, out[-400:]))

rc, sent, out, st, before = cadence_hand(None, [
    {'case': CASE_FT, 'owner': 'B', 'email': FT_EMAIL, 'addr': '1 EXAMPLE AVE', 'step': 0},
    {'case': CASE_FU, 'owner': 'A', 'email': FU_EMAIL, 'addr': '1 EXAMPLE AVE', 'step': 1},
], {
    CASE_FT: {'step': 0, 'next': TODAY.isoformat(), 'status': 'active',
              'owner': 'B', 'email': FT_EMAIL, 'addr': '1 EXAMPLE AVE', 'log': []},
    CASE_FU: _fu_state(),
})
rec('the same run sends the first touch and holds the follow-up',
    sent == [FT_EMAIL] and rc == 0 and st[CASE_FU]['step'] == 1 and st[CASE_FT]['step'] == 1
    and ('%s 2' % CASE_FU) in out.splitlines() and FU_EMAIL not in out, (sent, out[-500:]))


# ---- outreach_email: a Re: row is a follow-up, a cold row is not -----------------------------
import outreach_email as OE

otmp = tempfile.mkdtemp()
OE.PREVIEW_FILE = os.path.join(otmp, 'preview.html')
OE.SENT_LEDGER = os.path.join(otmp, 'mail_sent.json')
OE._WORKER_HIST = None
OE._load_credentials = lambda: ('login@example.com', 'pw')
OE._load_sender = lambda: {'name': 'Test Sender', 'addr': '1 EXAMPLE AVE, MIAMI, FL 33101'}
OE._load_leads = lambda: [{
    'case': CASE_FU, 'owners': 'A', 'tier': 'A', 'days': 30,
    'emails': [FU_EMAIL], 'addr': '1 EXAMPLE AVE',
}]
OE._load_optouts = lambda: set()
OE._load_ledger = lambda: []
OE._eligible = lambda *a, **k: (True, '')
S.HERE = otmp
S.SENT_LEDGER = os.path.join(otmp, 'mail_sent.json')
json.dump([], open(S.SENT_LEDGER, 'w'))
S.first_touch_paused = lambda health=None: False
_oe_subj = {'subj': 'Re: About 1 EXAMPLE AVE'}


def _oe_compose(r, snd, lang='en'):
    return {
        'subj': _oe_subj['subj'],
        'body': "Hi,\n\nA note about 1 EXAMPLE AVE.\n\n"
                "If now's not a good time, just tell me and I won't reach out again.",
    }


OE._compose_single = _oe_compose
oe_sent = []
OE._smtp_send = lambda *a, **k: oe_sent.append(a[3] if len(a) > 3 else k.get('to_addr')) or (
    '<mid-oe@example.com>', '250 ok')


def _oe(env_value):
    oe_sent.clear()
    OE._WORKER_HIST = None
    if os.path.exists(OE.SENT_LEDGER):
        os.remove(OE.SENT_LEDGER)
    if env_value is None:
        os.environ.pop('DEALFLOW_FOLLOWUPS_ON', None)
    else:
        os.environ['DEALFLOW_FOLLOWUPS_ON'] = env_value
    argv, buf = sys.argv, io.StringIO()
    sys.argv = ['outreach_email.py', '--send', '--limit', '1']
    _o = sys.stdout
    sys.stdout = buf
    try:
        rc = OE.main()
    finally:
        sys.stdout = _o
        sys.argv = argv
        os.environ.pop('DEALFLOW_FOLLOWUPS_ON', None)
    return rc, list(oe_sent), buf.getvalue()


rc, sent, out = _oe(None)
_sent_log = out.split('Sending as', 1)[-1]
rec('outreach does not send a Re: row while the switch is off',
    rc == 0 and sent == [] and ('%s 2' % CASE_FU) in out.splitlines() and FU_EMAIL not in _sent_log,
    (rc, sent, _sent_log[-400:]))
_oe_subj['subj'] = 'About 1 EXAMPLE AVE'
rc, sent, out = _oe(None)
rec('outreach still sends a cold first touch while the switch is off',
    rc == 0 and sent == [FU_EMAIL], (rc, sent, out[-400:]))
_oe_subj['subj'] = 'Re: About 1 EXAMPLE AVE'
rc, sent, out = _oe('1')
rec('outreach sends the Re: row when the switch is on',
    rc == 0 and sent == [FU_EMAIL], (rc, sent, out[-300:]))
rc, sent, out = _oe('yes')
_sent_log = out.split('Sending as', 1)[-1]
rec('outreach refuses a Re: row and exits non-zero on a bad value',
    rc == 2 and sent == [] and FU_EMAIL not in _sent_log, (rc, sent, _sent_log[-300:]))


# ---- POST /send -------------------------------------------------------------------------------
def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, payload):
    req = urllib.request.Request(
        'http://127.0.0.1:%d/send' % port, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {'err': str(e)}


def start_bridge(env_value, ledger=None):
    port = free_port()
    work = pathlib.Path(tempfile.mkdtemp(prefix='dffu_'))
    for f in ('send_server.py', 'stay_gate.py', 'mail_guard.py', 'deliverability.py',
              'sync_gate.py', 'text_hold.py', 'bounce_scan.py', 'bridge_auth.py'):
        shutil.copy(HERE / f, work / f)
    today = TODAY.isoformat()
    (work / 'sync_status.json').write_text(json.dumps({
        'date': today, 'state': 'finished', 'ok': True,
        'started_at': time.time() - 120, 'finished_at': time.time() - 60, 'steps': []}),
        encoding='utf-8')
    (work / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
    (work / 'sender.json').write_text(json.dumps({'name': 'Test Sender'}), encoding='utf-8')
    (work / 'senders.json').write_text(json.dumps({
        'main_domain': 'example.com', 'main_domain_cap': 40,
        'ramp_start': '2020-01-01', 'ramp': [{'through_day': 9999, 'per_day': 100}],
        'lanes': {'default': 'tester@example.com', 'active': 'tester@example.com'},
        'first_touch': {'from': ['warm@wu.example'], 'per_day': 100},
    }), encoding='utf-8')
    (work / 'bounced_emails.json').write_text('{}', encoding='utf-8')
    (work / 'optouts.json').write_text(json.dumps({'_dealflow_notes': 1, 'notes': {}}), encoding='utf-8')
    (work / 'sale_history_cache.json').write_text(json.dumps({
        CASE_FU: {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0},
        CASE_FT: {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0},
        '2099-000403-CA-01': {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0},
    }), encoding='utf-8')
    (work / 'verified_emails.json').write_text(json.dumps({
        addr: {'v': 'ok', 'why': 'zerobounce:valid', 'd': today}
        for addr in (FT_EMAIL, FU_EMAIL, 'again@example.com')
    }), encoding='utf-8')
    if ledger:
        (work / 'mail_sent.json').write_text(json.dumps(ledger), encoding='utf-8')
    (work / '_run_bridge.py').write_text(
        'import sys, smtplib\n'
        'class _FakeSMTP:\n'
        '    def __init__(self, *a, **k): pass\n'
        '    def __enter__(self): return self\n'
        '    def __exit__(self, *a): return False\n'
        '    def login(self, u, p): pass\n'
        '    def send_message(self, m, **k):\n'
        '        open("smtp_calls.txt", "a", encoding="utf-8").write(str(m["To"]) + "\\n")\n'
        '        return {}\n'
        'smtplib.SMTP_SSL = _FakeSMTP\n'
        'sys.argv = ["send_server.py", "--port", "%d", "--limit", "50"]\n'
        'exec(open("send_server.py", encoding="utf-8").read())\n' % port, encoding='utf-8')
    env = os.environ.copy()
    env['DEALFLOW_BOUNCE_SCAN_GATE'] = '0'   # PR #290's scan hold is not what this suite tests
    env.pop('DEALFLOW_FOLLOWUPS_ON', None)
    env['PYTHONUNBUFFERED'] = '1'
    if env_value is not None:
        env['DEALFLOW_FOLLOWUPS_ON'] = env_value
    out_f = open(work / 'bridge.out', 'w', encoding='utf-8')
    err_f = open(work / 'bridge.err', 'w', encoding='utf-8')
    proc = subprocess.Popen([sys.executable, str(work / '_run_bridge.py')], cwd=str(work),
                            stdout=out_f, stderr=err_f, env=env)
    up = False
    for _ in range(60):
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/health' % port, timeout=2) as r:
                if r.status == 200:
                    up = True
                    break
        except Exception:
            time.sleep(0.25)
    return port, work, proc, up


def smtp_of(work):
    p = work / 'smtp_calls.txt'
    return p.read_text(encoding='utf-8').split() if p.exists() else []


def stop(proc, work):
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
    shutil.rmtree(work, ignore_errors=True)


port, work, proc, up = start_bridge(None)
rec('bridge starts', up, (work / 'bridge.err').read_text(encoding='utf-8')[-400:] if not up else '')
if up:
    st, j = call(port, {
        'to': FT_EMAIL, 'subj': 'About 1 EXAMPLE AVE',
        'body': 'Hello, a short note about 1 EXAMPLE AVE.',
        'meta': {'c': CASE_FT, 'owner': 'B', 'addr': '1 EXAMPLE AVE', 'wl': 'active'}})
    rec('/send still sends a first touch while follow-ups are off',
        st == 200 and j.get('ok') is True and smtp_of(work) == [FT_EMAIL], (st, j, smtp_of(work)))
    st, j = call(port, {
        'to': FU_EMAIL, 'subj': 'Re: About 1 EXAMPLE AVE',
        'body': 'Hello, a short note about 1 EXAMPLE AVE.',
        'meta': {'c': CASE_FU, 'owner': 'A', 'addr': '1 EXAMPLE AVE', 'wl': 'active'}})
    listed = (work / 'bridge.out').read_text(encoding='utf-8')
    rec('/send refuses a Re: follow-up while the switch is off',
        st == 200 and j.get('ok') is False and j.get('blocked') == 'followups_off'
        and j.get('dry_run') is True and j.get('skip') is False and FU_EMAIL not in smtp_of(work)
        and FU_EMAIL not in json.dumps(j) and ('%s 2' % CASE_FU) in listed.splitlines(),
        (st, j, listed[-300:], smtp_of(work)))
    led_path = work / 'mail_sent.json'
    led = json.loads(led_path.read_text(encoding='utf-8')) if led_path.exists() else []
    rec('the refused follow-up writes no ledger row',
        [r.get('case') for r in led] == [CASE_FT], led)
stop(proc, work)

old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).isoformat()
port, work, proc, up = start_bridge(None, ledger=[{
    'd': OLD, 'ts_utc': old_ts, 'ch': 'email', 'to': 'again@example.com',
    'case': '2099-000403-CA-01', 'message_id': '<old@example.com>', 'from': 'tester@example.com',
}])
if up:
    st, j = call(port, {
        'to': 'again@example.com', 'subj': 'About 1 EXAMPLE AVE',
        'body': 'Hello, a short note about 1 EXAMPLE AVE.',
        'meta': {'c': '2099-000403-CA-01', 'owner': 'C', 'addr': '1 EXAMPLE AVE', 'wl': 'active'}})
    listed = (work / 'bridge.out').read_text(encoding='utf-8')
    rec('/send treats an already-mailed address as a follow-up even without Re:',
        st == 200 and j.get('blocked') == 'followups_off' and 'again@example.com' not in smtp_of(work)
        and '2099-000403-CA-01 2' in listed.splitlines(), (st, j, listed[-300:], smtp_of(work)))
else:
    rec('/send treats an already-mailed address as a follow-up even without Re:', False, 'bridge down')
stop(proc, work)

port, work, proc, up = start_bridge('1')
if up:
    st, j = call(port, {
        'to': FU_EMAIL, 'subj': 'Re: About 1 EXAMPLE AVE',
        'body': 'Hello, a short note about 1 EXAMPLE AVE.',
        'meta': {'c': CASE_FU, 'owner': 'A', 'addr': '1 EXAMPLE AVE', 'wl': 'active'}})
    rec('/send sends the Re: follow-up when the switch is on',
        st == 200 and j.get('ok') is True and smtp_of(work) == [FU_EMAIL], (st, j, smtp_of(work)))
else:
    rec('/send sends the Re: follow-up when the switch is on', False, 'bridge down')
stop(proc, work)

port, work, proc, up = start_bridge('yes')
if up:
    st, j = call(port, {
        'to': FU_EMAIL, 'subj': 'Re: About 1 EXAMPLE AVE',
        'body': 'Hello, a short note about 1 EXAMPLE AVE.',
        'meta': {'c': CASE_FU, 'owner': 'A', 'addr': '1 EXAMPLE AVE', 'wl': 'active'}})
    rec('/send fails closed on a bad value and does not send',
        st == 503 and j.get('blocked') == 'followups_bad' and j.get('skip') is False
        and smtp_of(work) == [] and FU_EMAIL not in json.dumps(j), (st, j, smtp_of(work)))
else:
    rec('/send fails closed on a bad value and does not send', False, 'bridge down')
stop(proc, work)


# ---- every follow-up send path consults the switch before SMTP --------------------------------
csrc = (HERE / 'cadence.py').read_text(encoding='utf-8')
run = csrc.split('def _run', 1)[1]
rec('cadence reads the switch before it builds a later step',
    run.find('followups_status') < run.find('if step >= 1 and _fu_mode') < run.find('subj, body = steps'))
rec('cadence consults the switch before SMTP',
    0 < run.find('if step >= 1 and _fu_mode') < run.find('_ss._smtp_send')
    and run.find('followup_due') < run.find('smtp.send_message'))
ssrc = (HERE / 'send_server.py').read_text(encoding='utf-8')
post = ssrc.split('def do_POST', 1)[1].split('\n    def ', 1)[0]
rec('/send checks the switch after the 24h claim and before SMTP',
    post.find('_claim_recipients') < post.find('followup_block') < post.find('_smtp_send'))
osrc = (HERE / 'outreach_email.py').read_text(encoding='utf-8')
send = osrc.split('# ---- send', 1)[1]
rec('outreach checks the switch before SMTP', send.find('followup_block') < send.find('_smtp_send'))
runbat = (HERE / 'cadence-run.bat').read_text(encoding='utf-8')
daily = (HERE / 'cadence-daily.bat').read_text(encoding='utf-8')
rec('cadence-run.bat is a hand run of cadence.py', 'python cadence.py' in runbat)
rec('cadence-daily.bat starts cadence.py', 'cadence.py' in daily)
rec('there is no separate /cadence route', "startswith('/cadence')" not in ssrc)

if _SAVED is not None:
    os.environ['DEALFLOW_FOLLOWUPS_ON'] = _SAVED

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
