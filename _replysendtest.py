"""_replysendtest -- a reply to a homeowner goes through the stay, do-not-contact and ledger gates.

Run: python _replysendtest.py   (exit 0 = safe; no network, SMTP and the ledger stubbed, synthetic data only)

WHY (2026-10-12). Four homeowner replies were sent over SMTP by a script that called
outreach_email._smtp_send directly. That function only runs mail_guard, so the stay gate, the case
and same-owner do-not-contact checks, the bounce list and the dedupe never ran, and the send never
reached mail_sent.json. Three of the homeowners had no passing stay check on file.

Pins: (1) _smtp_send refuses outside a gate unless the mail goes only to us; (2) reply_send.gate
refuses a held, opted-out, same-owner-opted-out, bounced, duplicate, unresolvable-case,
stale-ledger and unconfirmed-sync reply and lets a clear one through; (3) a send writes a ledger
row stamped with its source and machine, a failed send writes an error row, and a refused send
writes nothing; (4) every sender in the repo that reaches _smtp_send is inside a gate.
"""
import json, os, pathlib, sys, tempfile, types
os.environ.setdefault('DEALFLOW_ADVISOR_EMAILS', 'advisor@example.org')   # the advisor is local config now, not a literal
import datetime as dt

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:200]) if d and not cond else ''))


import mail_guard as MG
import reply_send as RS

GOOD, CASE, OWNER_EMAIL = 'owner@example.com', '2099-000101-CA-01', 'owner@example.com'


def fakes(optout_cases=(), optout_emails=(), ledger=(), stay_ok=True, stay_code='clear',
          readable=True, age=0.1, sync_ok=True, bk_held=False, raise_in=None, known=True, ledger_bad=False,
          stay_bad_case=None):
    def _raise(name):
        if raise_in == name:
            raise RuntimeError('boom')
    ss = types.SimpleNamespace(
        OPTOUT_MAX_AGE_DAYS=2.0,
        _optout_readable=lambda: (_raise('readable') or readable),
        _optout_age_days=lambda: age,
        _sync_verdict=lambda: {'ok': sync_ok, 'reason': 'not run today'},
        _optout_set=lambda: (set(optout_cases), set(optout_emails)),
        _load_ledger=lambda: list(ledger) + ([{'to': GOOD, 'case': CASE, 'subj': 'First note', 'message_id': '<k@x>',
                                               'ts_utc': '2020-01-01T00:00:00+00:00'}] if known else []),
        _ledger_unreadable=lambda: ledger_bad)
    sg = types.SimpleNamespace(
        pacer_key=lambda c: str(c).strip().upper() if str(c).strip() else '',
        case_stem=lambda c: str(c).strip() if '-CA-' in str(c) else '',
        check=lambda c, p: ({'ok': True, 'code': ''} if (stay_ok and str(c).upper() != str(stay_bad_case or '').upper()) else
                            {'ok': False, 'code': stay_code, 'why': 'held for the test'}))
    bk = types.SimpleNamespace(send_hold=lambda c, here=None: ((True, 'open case') if bk_held else (False, '')))
    return dict(ss=ss, sg=sg, bk=bk)


def verdict(case=CASE, to=GOOD, subj='Re: hello', **kw):
    tmp = tempfile.mkdtemp()
    ex = kw.pop('here_files', {})
    for n, v in ex.items():
        open(os.path.join(tmp, n), 'w').write(v)
    return RS.gate(case, to, subj, here=tmp, **fakes(**kw))


print('\nGATE')
v = verdict()
rec('a clear case with a clean recipient passes', v['ok'] and v['code'] == 'clear', v)
v = verdict(stay_ok=False, stay_code='stay_active')
rec('an active-stay case is refused', not v['ok'] and v['code'] == 'stay_stay_active', v)
v = verdict(stay_ok=False, stay_code='stay_unverified')
rec('a stay that cannot be verified is refused (the three homeowners)', not v['ok'] and 'unverified' in v['code'], v)
v = verdict(stay_ok=False, stay_code='stay_case_unresolvable')
rec('a case the stay gate cannot resolve is refused', not v['ok'], v)
v = verdict(case='')
rec('no case number is refused', not v['ok'] and v['code'] == 'case_unresolvable', v)
v = verdict(bk_held=True)
rec('a federal bankruptcy hold is refused', not v['ok'] and v['code'] == 'bk_hold', v)
v = verdict(optout_cases={CASE.lower()})
rec('a case on the do-not-contact ledger is refused', not v['ok'] and v['code'] == 'optout_case', v)
v = verdict(optout_emails={OWNER_EMAIL})
rec('an address on the do-not-contact ledger is refused', not v['ok'] and v['code'] == 'optout_email', v)
led = [{'to': GOOD, 'case': '2099-000102-CA-01', 'message_id': '<a@x>', 'ts_utc': '2020-01-01T00:00:00+00:00'}]
v = verdict(optout_cases={'2099-000102-cA-01'.lower()}, ledger=led)
rec("a stop on the owner's OTHER case (mailed to this address) refuses the reply", not v['ok'] and 'same owner' in v['why'], v)
v = verdict(optout_cases=set(), optout_emails=set(), ledger=led)
rec('...and a clean other case does not', v['ok'], v)
v2 = RS.gate(CASE, GOOD, 's', also_cases=['2099-000103-CA-01'], here=tempfile.mkdtemp(),
             **fakes(optout_cases={'2099-000103-ca-01'}))
rec('--also-case stops are honoured', not v2['ok'] and v2['code'] == 'optout_case', v2)
v = verdict(readable=False)
rec('an unreadable do-not-contact ledger refuses', not v['ok'] and v['code'] == 'optout_unreadable', v)
v = verdict(age=None)
rec('a missing ledger (age None) refuses', not v['ok'] and v['code'] == 'optout_stale', v)
v = verdict(age=9.0)
rec('a stale ledger refuses', not v['ok'] and v['code'] == 'optout_stale', v)
v = verdict(sync_ok=False)
rec("an unconfirmed 07:15 sync refuses", not v['ok'] and v['code'] == 'optout_sync_unconfirmed', v)
v = verdict(here_files={'bounced_emails.json': json.dumps({GOOD: {'d': '2026-09-01'}})})
rec('a hard-bounced recipient is refused', not v['ok'] and v['code'] == 'bounced', v)
v = verdict(here_files={'bounced_emails.json': '{not json'})
rec('an unreadable bounce list refuses', not v['ok'] and v['code'] == 'bounce_list_unreadable', v)
now = dt.datetime.now(dt.timezone.utc)
dup = [{'to': GOOD, 'subj': 'Re: hello', 'case': CASE, 'message_id': '<m@x>', 'ts_utc': (now - dt.timedelta(hours=2)).isoformat()}]
v = verdict(ledger=dup)
rec('the same subject to the same address inside 24h is refused', not v['ok'] and v['code'] == 'duplicate', v)
old = [dict(dup[0], ts_utc=(now - dt.timedelta(hours=40)).isoformat())]
v = verdict(ledger=old)
rec('...but not after 24h', v['ok'], v)
v = verdict(ledger=[dict(dup[0], test_mode=True)])
rec('a test-mode row does not count as a prior send', v['ok'], v)
v = verdict(to='a@b.com, c@d.com')
rec('two recipients in one field are refused', not v['ok'] and v['code'] == 'bad_recipient', v)
v = verdict(raise_in='readable')
rec('a gate that raises refuses instead of passing', not v['ok'] and v['code'] == 'gate_error', v)
v = RS.gate(CASE, GOOD, 's', here=tempfile.mkdtemp(), ss=fakes()['ss'], sg=None, bk=fakes()['bk'])
rec('the REAL stay_gate with no stay cache refuses (fail closed)', not v['ok'], v)

v = verdict(to='x<owner@example.com>')
rec('a display-name recipient cannot smuggle an opted-out address past the checks', not v['ok'] and v['code'] == 'bad_recipient', v)
v = verdict(to='Owner <owner@example.com>')
rec('a "Name <addr>" recipient is refused', not v['ok'] and v['code'] == 'bad_recipient', v)
v = verdict(ledger_bad=True)
rec('an unreadable mail_sent.json refuses (no silent empty history)', not v['ok'] and v['code'] == 'ledger_unreadable', v)
v = verdict(known=False)
rec('an address we never mailed or heard from under this case is refused (not a reply)', not v['ok'] and v['code'] == 'unknown_recipient', v)
v = verdict(known=False, here_files={'replies.json': json.dumps({CASE: {'email': GOOD}})})
rec('...but an address that wrote in under this case is a reply', v['ok'], v)
v = verdict(here_files={'bounced_emails.json': '["a@b.com"]'})
rec('a bounce list given as a list is read', v['ok'], v)
v = verdict(here_files={'bounced_emails.json': '"oops"'})
rec('a bounce list of the wrong shape refuses', not v['ok'] and v['code'] == 'bounce_list_unreadable', v)
led2 = [{'to': GOOD, 'case': '2099-000102-CA-01', 'message_id': '<a@x>', 'ts_utc': '2020-01-01T00:00:00+00:00'}]
v = verdict(ledger=led2, stay_bad_case='2099-000102-ca-01')
rec("a stay on the owner's OTHER case (mailed to this address) refuses the reply", not v['ok'] and v['code'].startswith('stay_'), v)
v = verdict(ledger=led2, bk_held=True)
rec("a federal hold applies to the owner's other cases too", not v['ok'] and v['code'] == 'bk_hold', v)

print('\nSEND AND LEDGER')
rows, calls = [], []
def smtp_ok(u, p, d, to, subj, body):
    calls.append((to, MG.current_gate())); return '<id1@bsg>', (250, 'ok')
def smtp_bad(u, p, d, to, subj, body):
    raise RuntimeError('smtp down')
import outreach_email as OE
saved = (OE._load_credentials, OE._load_sender)
OE._load_credentials = lambda: ('login@example.com', 'pw')
OE._load_sender = lambda: {'name': 'Test'}
try:
    tmp = tempfile.mkdtemp()
    r = RS.send_reply(CASE, GOOD, 'Re: hello', 'body', send=False, here=tmp, smtp=smtp_ok, ledger=rows.append, **fakes())
    rec('dry run: gate verdict only, nothing sent, nothing ledgered', r['ok'] and not r['sent'] and not calls and not rows, r)
    r = RS.send_reply(CASE, GOOD, 'Re: hello', 'body', send=True, here=tmp, smtp=smtp_ok, ledger=rows.append,
                      **fakes(stay_ok=False, stay_code='stay_active'))
    rec('a held case with --send transmits nothing and writes no row', not r['ok'] and not calls and not rows, r)
    r = RS.send_reply(CASE, GOOD, 'Re: hello', 'body', send=True, here=tmp, smtp=smtp_ok, ledger=rows.append, **fakes())
    rec('a clear case with --send transmits once, inside the reply_send gate', r['sent'] and calls == [(GOOD, 'reply_send')], (r, calls))
    row = rows[-1] if rows else {}
    rec('the ledger row carries to/case/message_id/source/machine/ch', row.get('to') == GOOD and row.get('case') == CASE
        and row.get('message_id') == '<id1@bsg>' and row.get('source') == 'reply_send' and row.get('machine')
        and row.get('ch') == 'email' and row.get('ts_utc'), row)
    rec('the gate token is released after the send', MG.current_gate() == '')
    n = len(rows)
    r = RS.send_reply(CASE, GOOD, 'Re: hello', 'body', send=True, here=tmp, smtp=smtp_bad, ledger=rows.append, **fakes())
    rec('a failed send returns not-sent and writes an error row (no message_id)',
        not r['sent'] and r['code'] == 'send_failed' and len(rows) == n + 1 and rows[-1].get('error')
        and not rows[-1].get('message_id'), (r, rows[-1:]))
finally:
    OE._load_credentials, OE._load_sender = saved

print('\nTHE SENDER REFUSES OUTSIDE A GATE')
try:
    MG.require_gated('owner@example.com', login='login@example.com')
    rec('a bare homeowner send is refused', False)
except MG.UnsendableError:
    rec('a bare homeowner send is refused', True)
rec('mail to the sending login alone is allowed', MG.require_gated('login@example.com', login='login@example.com'))
rec('mail to the advisor alone is allowed', MG.require_gated('advisor@example.org'))
try:
    MG.require_gated('advisor@example.org', bcc='owner@example.com')
    rec('an owner hidden in bcc is refused', False)
except MG.UnsendableError:
    rec('an owner hidden in bcc is refused', True)
with MG.gated('unit'):
    rec('inside gated() a send is allowed and nested tokens restore', MG.require_gated('owner@example.com')
        and MG.current_gate() == 'unit')
    with MG.gated('inner'):
        pass
    rec('the outer token survives a nested gate', MG.current_gate() == 'unit')
try:
    with MG.gated(''):
        pass
    rec('gated() needs a name', False)
except MG.UnsendableError:
    rec('gated() needs a name', True)

sent_wire = []
class _S:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def login(self, u, p): pass
    def send_message(self, m): sent_wire.append(m['To']); return {}
import smtplib
real = smtplib.SMTP_SSL
smtplib.SMTP_SSL = _S
try:
    try:
        OE._smtp_send('login@example.com', 'pw', 'T', GOOD, 'Hello there', 'A real body.\nIf now is not a good time, just tell me and I won\'t reach out again.')
        rec('outreach_email._smtp_send, called directly, does not transmit to a homeowner', False)
    except MG.UnsendableError as e:
        rec('outreach_email._smtp_send, called directly, does not transmit to a homeowner', not sent_wire, e)
    import send_server as SS
    try:
        SS._smtp_send('login@example.com', 'pw', 'T', GOOD, 'Hello there', 'A real body.')
        rec('send_server._smtp_send, called directly, does not transmit to a homeowner', False)
    except MG.UnsendableError:
        rec('send_server._smtp_send, called directly, does not transmit to a homeowner', not sent_wire)
finally:
    smtplib.SMTP_SSL = real

print('\nWIRING: every caller of _smtp_send is inside a gate')
src = {n: (HERE / n).read_text(encoding='utf-8') for n in
       ('outreach_email.py', 'send_server.py', 'cadence.py', 'deal_desk.py', 'reply_send.py')}
rec('outreach_email.main sends inside gated()', "with _MG.gated('outreach_email'):" in src['outreach_email.py'])
rec('send_server /send sends inside gated()', "with _MG.gated('send_server'):" in src['send_server.py'])
rec('cadence sends inside gated()', "with _MG.gated('cadence'):" in src['cadence.py'])
rec('reply_send sends inside gated()', 'with MG.gated(SOURCE):' in src['reply_send.py'])
for mod in ('outreach_email.py', 'send_server.py'):
    fn = src[mod].split('def _smtp_send(', 1)[1].split('\ndef ', 1)[0]
    rec('%s: _smtp_send calls require_gated before building the message' % mod,
        '_MG.require_gated(' in fn.split("sender = (from_addr or user)", 1)[0])
rec('deal_desk mails only the advisor (allowed without a gate)', "JESSE," in src['deal_desk.py'])
calls = []
for f in sorted(HERE.glob('*.py')):
    if f.name.startswith('_') or f.name in ('mail_guard.py',):
        continue
    t = f.read_text(encoding='utf-8', errors='replace')
    if '_smtp_send(' in t and not t.lstrip().startswith('"""_'):
        calls.append(f.name)
known = {'outreach_email.py', 'send_server.py', 'cadence.py', 'deal_desk.py'}
rec('no NEW module calls _smtp_send without being reviewed here: %s' % sorted(set(calls) - known),
    set(calls) <= known | {'reply_send.py'}, sorted(set(calls) - known))

print('\n==== %d/%d reply-send checks passed ====' % (len(ok), len(ok) + len(bad)))
if bad:
    print('FAILED:', bad)
sys.exit(1 if bad else 0)
