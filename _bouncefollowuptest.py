"""A follow-up never reaches an address whose earlier email hard-bounced. Fake addresses
(example.com), fake IMAP, fake SMTP, temp folders; no network, no real data.

WHAT THIS GUARDS (2026-10-19). Follow-ups reached two recipients whose earlier emails had already
bounced, and both bounced again. /send, cadence and outreach_email all refuse an address on
bounced_emails.json, but that list only knows what bounces.py last harvested, and nothing checked
that it had harvested since the previous email. So:
  1. bounce_scan.followup_hold: a follow-up (an address this ledger already mailed) waits until a
     clean bounces.py scan started at least DEALFLOW_BOUNCE_SETTLE_H after that email. No status,
     an unreadable status, or a scan that failed = held. A first touch is never held here.
  2. bounces.py scans every mailbox this box holds a key for (not only the first), takes
     postmaster@ notices as well as mailer-daemon@, records the scan in bounce_scan_status.json
     only after the list is written, and a failed mailbox never counts as a clean scan.
  3. /send holds the follow-up (451, skip, blocked=bounce_scan_stale) before SMTP, and after the
     scan refuses the bounced address as dead while a live one sends.
  4. cadence holds the step without consuming it; outreach_email's CLI drops the row.
"""
import datetime as dt
import email.message
import importlib.util
import io
import json
import os
# Follow-up sends are off unless this is exactly 1 (PR #294). This fixture expects one to leave.
os.environ['DEALFLOW_FOLLOWUPS_ON'] = '1'
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
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = dt.date.today()


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:300]) if d and not cond else ''))


def iso(hours_ago):
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


def row(to, hours_ago, case='2099-000003-CA-01', mid=None, ts=True, login='box-a@example.org'):
    r = {'d': (NOW - dt.timedelta(hours=hours_ago)).date().isoformat(), 'ch': 'email',
         'from': 'alejandro@bsgfl.com', 'login': login, 'to': to, 'bcc': '', 'case': case,
         'message_id': mid or '<%s-%d@example.com>' % (to.split('@')[0], hours_ago)}
    if ts:
        r['ts_utc'] = iso(hours_ago)
    return r


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


import bounce_scan as BS

# ------------------------------------------------------------------ 1. the rule
print('-- bounce_scan.followup_hold')
T = pathlib.Path(tempfile.mkdtemp(prefix='dfbf_'))
U = T / 'unit'
U.mkdir()
A, B_ = 'box-a@example.org', 'box-b@example.org'
SINCE_OK = (NOW - dt.timedelta(days=14)).date().isoformat()


def status(d, v):
    (d / BS.STATUS).write_text(v if isinstance(v, str) else json.dumps(v), encoding='utf-8')


def mb(ok_hours_ago, since=SINCE_OK, run_ok=True):
    r = {'last_run_started_utc': iso(ok_hours_ago or 0), 'last_run_ok': run_ok, 'last_ok_since': since}
    if ok_hours_ago is not None:
        r['last_ok_started_utc'] = iso(ok_hours_ago)
    return r


def v2(boxes, listed=()):
    return {'version': 2, 'ok': True, 'mailboxes': boxes,
            'list_hashes': sorted(BS.addr_hash(x) for x in listed)}


hold = lambda addr, rows, bounced=frozenset(): BS.followup_hold(addr, rows, str(U), bounced=set(bounced))
L = [row('gone@example.com', 26)]
rec('never-mailed address (a first touch) is not held', hold('new@example.com', L) == '')
rec('no status file: a follow-up is held', 'no bounce scan on record' in hold('gone@example.com', L))
status(U, '{torn')
rec('unreadable status: held', 'unreadable' in hold('gone@example.com', L))
status(U, {'ok': True, 'last_ok_started_utc': iso(1)})
rec('an older-format status (no per-mailbox record): held', 'version-2' in hold('gone@example.com', L))
status(U, v2({A: mb(72)}))
rec('clean scan BEFORE the last email: held', 'has not covered' in hold('gone@example.com', L))
status(U, v2({A: mb(25)}))
rec('clean scan 1h after the email (inside the 2h settle): held', hold('gone@example.com', L) != '')
status(U, v2({A: mb(20)}))
rec('clean scan 6h after the email, right mailbox, right dates: clear', hold('gone@example.com', L) == '')
rec('the LAST email counts, not the first', hold('gone@example.com', L + [row('gone@example.com', 10)]) != '')
rec('a Bcc counts as an email to that address',
    hold('b@example.com', [dict(row('x@example.com', 10), bcc='a@example.com, B@example.com')]) != '')
rec('rows without a message_id (never sent) do not count',
    hold('gone@example.com', [dict(row('gone@example.com', 1), message_id='')]) == '')
rec('a row with no ts_utc counts as the end of its day',
    BS.last_mailed_utc([row('old@example.com', 30, ts=False)], 'old@example.com')
    == dt.datetime.combine((NOW - dt.timedelta(hours=30)).date() + dt.timedelta(days=1), dt.time(), dt.timezone.utc))
os.environ['DEALFLOW_BOUNCE_SCAN_GATE'] = '0'
rec('DEALFLOW_BOUNCE_SCAN_GATE=0 turns the hold off', hold('gone@example.com', L + [row('gone@example.com', 1)]) == '')
del os.environ['DEALFLOW_BOUNCE_SCAN_GATE']
os.environ['DEALFLOW_BOUNCE_SETTLE_H'] = '7'
rec('DEALFLOW_BOUNCE_SETTLE_H widens the settle window', hold('gone@example.com', L) != '')
del os.environ['DEALFLOW_BOUNCE_SETTLE_H']

print('-- review gap 1: a clean scan of one mailbox never clears mail sent from another')
LB = [row('gone@example.com', 26, login=B_)]
status(U, v2({A: mb(20)}))
rec('sent from mailbox B, only mailbox A scanned cleanly: held', B_ in hold('gone@example.com', LB), hold('gone@example.com', LB))
status(U, v2({A: mb(20), B_: mb(20)}))
rec('...once B has its own clean scan: clear', hold('gone@example.com', LB) == '')
status(U, v2({A: mb(20)}))
mix = [row('gone@example.com', 24 * 5, login=B_), row('gone@example.com', 26, login=A)]
rec('B mailed it 5 days ago, A mailed it last; A scanned, B never: held', B_ in hold('gone@example.com', mix), hold('gone@example.com', mix))
old_b = [row('gone@example.com', 24 * 60, login=B_), row('gone@example.com', 26, login=A)]
rec('B mailed it 60 days ago (past the lookback), A mailed it last and is covered: clear', hold('gone@example.com', old_b) == '')
nolog = [dict(row('gone@example.com', 26), login='', **{'from': ''})]
status(U, v2({A: mb(20), B_: mb(None, run_ok=False)}))
rec('a row naming no mailbox needs every mailbox on record clean: held', hold('gone@example.com', nolog) != '')
status(U, v2({}))
rec('...and with no mailbox on record at all: held', hold('gone@example.com', nolog) != '')
alias = [dict(row('gone@example.com', 26), login='', **{'from': 'alias@example.net'})]
status(U, v2({A: mb(20)}))
rec('no login on the row: the From mailbox must be scanned: held', 'alias@example.net' in hold('gone@example.com', alias))

print('-- review gap 2: the clean scan must cover the email\'s dates and still be current')
status(U, v2({A: mb(20, since=(NOW - dt.timedelta(hours=26)).date().isoformat())}))
rec('the scan searched only from the email\'s own day: held (a day of margin is required)',
    'searched from' in hold('gone@example.com', L), hold('gone@example.com', L))
status(U, v2({A: mb(20, since=NOW.date().isoformat())}))
rec('the scan searched only from today, after the email: held', 'searched from' in hold('gone@example.com', L))
status(U, v2({A: mb(20, run_ok=False)}))
rec('a clean scan followed by a FAILED run of that mailbox: held', 'failed' in hold('gone@example.com', L), hold('gone@example.com', L))
LOLD = [row('gone@example.com', 24 * 4)]
status(U, v2({A: mb(24 * 2)}))
rec('the covering clean scan is 48h old (over 36h): held', 'older than' in hold('gone@example.com', LOLD), hold('gone@example.com', LOLD))
os.environ['DEALFLOW_BOUNCE_SCAN_MAX_AGE_H'] = '72'
rec('...DEALFLOW_BOUNCE_SCAN_MAX_AGE_H=72 accepts it', hold('gone@example.com', LOLD) == '')
del os.environ['DEALFLOW_BOUNCE_SCAN_MAX_AGE_H']
status(U, v2({A: {'last_run_ok': True, 'last_ok_started_utc': iso(20)}}))
rec('a clean scan with no recorded search start: held', 'search start' in hold('gone@example.com', L))

print('-- review gap 3: an older bounce list never pairs with a newer scan record')
status(U, v2({A: mb(20)}, listed=['gone@example.com', 'other@example.com']))
rec('the list this send read lacks an address the scan wrote: held',
    'older than the last scan' in hold('gone@example.com', L, {'other@example.com'}))
rec('...the list it read has them all (and more since): clear',
    hold('gone@example.com', L, {'gone@example.com', 'other@example.com', 'later@example.com'}) == '')
(U / 'bounced_emails.json').write_text(json.dumps({'other@example.com': {}}), encoding='utf-8')
rec('no list passed: the file on disk is read and checked the same way',
    'older than' in BS.followup_hold('gone@example.com', L, str(U)))
(U / 'bounced_emails.json').write_text('{torn', encoding='utf-8')
rec('...an unreadable list on disk: held', 'unreadable' in BS.followup_hold('gone@example.com', L, str(U)))


# ------------------------------------------------------------------ 2. bounces.py, fake IMAP
def notice(frm, to_login, dead, text):
    m = email.message.EmailMessage()
    m['From'] = frm
    m['To'] = to_login
    m['Subject'] = 'Undeliverable'
    m.set_content(text % dead)
    return m.as_bytes()


INBOX = {}          # login -> [raw message bytes]
BROKEN = set()


class FakeIMAP:
    def __init__(self, host):
        self.user = None

    def login(self, u, pw):
        if u in BROKEN:
            raise OSError('connection reset')
        self.user = u

    def select(self, box, readonly=False):
        return 'OK', [b'1']

    def search(self, charset, crit):
        import re
        froms = [f.lower() for f in re.findall(r'FROM "([^"]+)"', crit)]
        ids = [str(i + 1).encode() for i, raw in enumerate(INBOX.get(self.user, []))
               if any(f in raw.split(b'\n', 1)[0].decode().lower() for f in froms)]
        return 'OK', [b' '.join(ids)]

    def fetch(self, i, what):
        return 'OK', [(b'1', INBOX[self.user][int(i) - 1])]

    def logout(self):
        pass


def run_bounces(d, creds, argv=()):
    import bounces as B
    old = (B.HERE, B.OUT, B.R._cred_map, B.imaplib.IMAP4_SSL, sys.argv)
    B.HERE, B.OUT = str(d), str(d / 'bounced_emails.json')
    B.R._cred_map = lambda: dict(creds)
    B.imaplib.IMAP4_SSL = FakeIMAP
    sys.argv = ['bounces.py'] + list(argv)
    out, so = io.StringIO(), sys.stdout
    sys.stdout = out
    try:
        rc = B.main()
    finally:
        sys.stdout = so
        B.HERE, B.OUT, B.R._cred_map, B.imaplib.IMAP4_SSL, sys.argv = old
    st = json.loads((d / BS.STATUS).read_text(encoding='utf-8')) if (d / BS.STATUS).exists() else {}
    try:
        bl = json.loads((d / 'bounced_emails.json').read_text(encoding='utf-8'))
    except Exception:
        bl = None
    return rc, st, bl, out.getvalue()


print('\n-- bounces.py')
os.environ.pop('GMAIL_APP_PASSWORD', None)
BX = T / 'bounces'
BX.mkdir()
CREDS = [('alejandro@bsgflorida.com', 'pw1'), ('second@example.org', 'pw2')]
INBOX['alejandro@bsgflorida.com'] = [notice('Mail Delivery Subsystem <mailer-daemon@googlemail.com>',
                                            'alejandro@bsgflorida.com', 'gone@example.com',
                                            "Address not found\n550 5.1.1 %s: no such user")]
# the second mailbox's notice comes from postmaster@ (Microsoft style), which the old search skipped
INBOX['second@example.org'] = [notice('postmaster@outlook.com', 'second@example.org', 'msdead@example.com',
                                      'Delivery has failed to these recipients: %s\n550 5.1.10 RESOLVER.ADR.RecipientNotFound')]
M1, M2 = CREDS[0][0], CREDS[1][0]
before = dt.datetime.now(dt.timezone.utc)
rc, st, bl, out = run_bounces(BX, CREDS)
rec('both mailboxes scanned', st.get('scanned') == [M1, M2] and not st.get('failed'), st)
rec('the first mailbox\'s mailer-daemon bounce is listed', 'gone@example.com' in bl, sorted(bl))
rec('the SECOND mailbox\'s postmaster bounce is listed', 'msdead@example.com' in bl, sorted(bl))
m = st.get('mailboxes') or {}
rec('clean scan: exit 0, version 2, a clean record per mailbox from this run\'s start',
    rc == 0 and st.get('version') == 2 and set(m) == {M1, M2}
    and all(r.get('last_run_ok') is True and BS._parse(r.get('last_ok_started_utc')) >= before - dt.timedelta(seconds=1)
            for r in m.values()), (rc, st))
rec('each mailbox records the date its search started from',
    all(r.get('last_ok_since') == (TODAY - dt.timedelta(days=14)).isoformat() for r in m.values()), m)
rec('list_hashes are exactly the hashed list as written',
    st.get('list_hashes') == sorted(BS.addr_hash(x) for x in bl), (len(st.get('list_hashes') or []), sorted(bl)))
rec('status carries no recipient address',
    'gone@' not in json.dumps(st) and 'msdead@' not in json.dumps(st) and st.get('hard') == 2, st)
first_ok = {k: v['last_ok_started_utc'] for k, v in m.items()}
time.sleep(0.01)
BROKEN.add(M2)
INBOX[M1].append(notice('mailer-daemon@googlemail.com', M1, 'late@example.com', 'Address not found\n550 5.1.1 %s'))
rc, st, bl, out = run_bounces(BX, CREDS)
m = st.get('mailboxes') or {}
rec('one mailbox fails: exit 2, not ok, failed named', rc == 2 and st.get('ok') is False
    and st.get('failed') == [M2], (rc, st))
rec('...that mailbox records a failed last run and keeps its old clean time',
    m[M2].get('last_run_ok') is False and m[M2].get('last_ok_started_utc') == first_ok[M2], m[M2])
rec('...the mailbox that worked moves forward', m[M1].get('last_run_ok') is True
    and m[M1].get('last_ok_started_utc') != first_ok[M1], m[M1])
rec('...the other mailbox\'s new bounce is still written', 'late@example.com' in bl and 'msdead@example.com' in bl, sorted(bl))
status_dir = str(BX)
rec('...so mail sent from the failed mailbox stays held after it',
    BS.followup_hold('x@example.com', [row('x@example.com', 26, login=M2)], status_dir, bounced=set(bl)) != '')
BROKEN.clear()
keep = json.loads((BX / BS.STATUS).read_text(encoding='utf-8'))
(BX / 'bounced_emails.json').write_text('{torn', encoding='utf-8')
rc, st, bl_raw, out = run_bounces(BX, CREDS)
m = st.get('mailboxes') or {}
rec('a torn bounce list: exit 1, not ok, every scanned mailbox records a failed run, clean times kept',
    rc == 1 and st.get('ok') is False and all(m[k].get('last_run_ok') is False for k in (M1, M2))
    and m[M1].get('last_ok_started_utc') == keep['mailboxes'][M1]['last_ok_started_utc'], (rc, st))
rec('...and the list hashes are not replaced', st.get('list_hashes') == keep.get('list_hashes'))
for p in BX.glob('bounced_emails.json*'):
    p.unlink()
for p in BX.glob(BS.STATUS):
    p.unlink()
rc, st, bl, out = run_bounces(BX, CREDS, ['--account', M2])
rec('--account scans only that mailbox and records only that mailbox',
    st.get('scanned') == [M2] and set(st.get('mailboxes') or {}) == {M2} and sorted(bl) == ['msdead@example.com'], (st, sorted(bl)))
rec('gap 1 end to end: after a one-mailbox scan, mail sent from the other mailbox is still held',
    M1 in BS.followup_hold('y@example.com', [row('y@example.com', 26, login=M1)], str(BX), bounced=set(bl)))
before_dry = json.loads((BX / BS.STATUS).read_text(encoding='utf-8'))
rc, st, bl, out = run_bounces(BX, CREDS, ['--dry-run'])
rec('--dry-run writes no status', st == before_dry, st)
INBOX.clear()
rc, st, bl, out = run_bounces(BX, CREDS, ['--days', '1'])
rec('gap 2 end to end: a --days 1 scan does not cover an email from 3 days ago',
    'searched from' in BS.followup_hold('z@example.com', [row('z@example.com', 72, login=M1)], str(BX), bounced=set(bl)))


# ------------------------------------------------------------------ 3. /send, the reported scenario
print('\n-- /send: first email bounced, follow-up due')
CASE = '2099-000003-CA-01'
CACHE = {CASE: {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0}}
VERIFIED = {a: {'v': 'ok', 'why': 'zerobounce:valid', 'd': TODAY.isoformat()}
            for a in ('gone@example.com', 'live@example.com', 'elsewhere@example.com')}
FILLER = [row('fill%02d@example.com' % i, 24 * 3, case='2099-0001%02d-CA-01' % i) for i in range(40)]
TL = 'tester@example.com'      # the bridge's login, the mailbox its bounces come back to
LEDGER = [dict(r, login=TL) for r in FILLER] + [row('gone@example.com', 26, login=TL),
                                                row('live@example.com', 26, login=TL),
                                                row('elsewhere@example.com', 26, login='other-login@example.org')]
W = T / 'srv'
W.mkdir()
for f in ('send_server.py', 'stay_gate.py', 'mail_guard.py', 'deliverability.py', 'sync_gate.py',
          'bounce_scan.py', 'bounces.py', 'replies.py', 'paths.py', 'optout_sync.py'):
    shutil.copy(HERE / f, W / f)
w = lambda n, v: (W / n).write_text(json.dumps(v), encoding='utf-8')
w('sync_status.json', {'date': TODAY.isoformat(), 'state': 'finished', 'ok': True,
                       'started_at': time.time() - 120, 'finished_at': time.time() - 60, 'steps': []})
(W / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
w('sender.json', {'name': 'Test Sender'})
w('optouts.json', {'_dealflow_notes': True, 'notes': {}})
w('sale_history_cache.json', CACHE)
w('verified_emails.json', VERIFIED)
w('bounced_emails.json', {})
w('replies.json', {})
w('mail_sent.json', LEDGER)
w('leads_final.json', [])
w('skiptrace_results.json', {})
# the last clean scan ran three days ago, before the first email: the 07:15 harvest missed since
w(BS.STATUS, v2({TL: mb(72)}))


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def call(port, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data,
                                 headers={'Content-Type': 'application/json'} if data else {},
                                 method='POST' if data else 'GET')
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


port = free_port()
(W / '_run_bridge.py').write_text(
    'import sys, json, smtplib\n'
    'class _FakeSMTP:\n'
    '    def __init__(self, *a, **k): pass\n'
    '    def __enter__(self): return self\n'
    '    def __exit__(self, *a): return False\n'
    '    def login(self, u, p): pass\n'
    '    def send_message(self, m, **k):\n'
    '        open("smtp_calls.jsonl", "a", encoding="utf-8").write(json.dumps({"to": str(m["To"])}) + "\\n")\n'
    '        return {}\n'
    'smtplib.SMTP_SSL = _FakeSMTP\n'
    'sys.argv = ["send_server.py", "--port", "%d", "--limit", "50"]\n'
    'exec(open("send_server.py", encoding="utf-8").read())\n' % port, encoding='utf-8')
env = dict(os.environ, DEALFLOW_EMAIL_HTML='0')
proc = subprocess.Popen([sys.executable, str(W / '_run_bridge.py')], cwd=str(W), env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def smtp():
    p = W / 'smtp_calls.jsonl'
    return [json.loads(x)['to'] for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


def send(to):
    return call(port, '/send', {'to': to, 'bcc': '', 'subj': 'Following up on 1 Main St',
                                'body': 'Hello Jane, following up on my note about 1 Main St.',
                                'meta': {'owner': 'Jane', 'addr': '1 Main St', 'wl': 'active', 'c': CASE}})


try:
    up = False
    for _ in range(60):
        if call(port, '/health')[0] == 200:
            up = True
            break
        time.sleep(0.25)
    rec('bridge starts', up)
    st_, j = send('gone@example.com')
    rec('follow-up to the bounced address, bounce not yet harvested: 451 held bounce_scan_stale',
        st_ == 451 and j.get('skip') is True and j.get('blocked') == 'bounce_scan_stale', (st_, j))
    st_, j = send('live@example.com')
    rec('the same hold applies to every follow-up, not only known-bad ones', st_ == 451
        and j.get('blocked') == 'bounce_scan_stale', (st_, j))
    rf = [json.loads(x) for x in (W / 'send_refusals.jsonl').read_text(encoding='utf-8').splitlines()] \
        if (W / 'send_refusals.jsonl').exists() else []
    rec('holds are logged as gate=bounce_scan with no address',
        len(rf) == 2 and all(r.get('gate') == 'bounce_scan' for r in rf) and '@' not in json.dumps(rf), rf)
    rec('nothing reached SMTP', smtp() == [], smtp())

    # the 07:15 sync's bounces.py now runs and finds the notice
    INBOX.clear()
    INBOX['tester@example.com'] = [notice('mailer-daemon@googlemail.com', 'tester@example.com',
                                          'gone@example.com', 'Address not found\n550 5.1.1 %s')]
    rc, st, bl, out = run_bounces(W, [('tester@example.com', 'pw')])
    rec('bounces.py harvests it and records a clean scan', rc == 0 and 'gone@example.com' in bl and st.get('ok'), (rc, st))
    _, hj = call(port, '/health')
    rec('/health shows the last clean scan, no address',
        (hj.get('bounce_scan') or {}).get('last_clean_utc') == st['mailboxes'][TL]['last_ok_started_utc']
        and '@' not in json.dumps(hj.get('bounce_scan')), hj.get('bounce_scan'))
    good_list = (W / 'bounced_emails.json').read_text(encoding='utf-8')
    (W / 'bounced_emails.json').write_text('{}', encoding='utf-8')
    st_, j = send('gone@example.com')
    rec('gap 3 end to end: an OLDER bounce list beside the new scan record holds, never sends',
        st_ == 451 and j.get('blocked') == 'bounce_scan_stale' and 'older than' in j.get('err', ''), (st_, j))
    (W / 'bounced_emails.json').write_text(good_list, encoding='utf-8')
    st_, j = send('elsewhere@example.com')
    rec('gap 1 end to end: last mailed from a mailbox this scan never opened: held',
        st_ == 451 and j.get('blocked') == 'bounce_scan_stale' and 'other-login' in j.get('err', ''), (st_, j))
    st_, j = send('gone@example.com')
    rec('after the scan the bounced address is refused as dead, never sent',
        st_ == 451 and (j.get('verdicts') or [{}])[0].get('verdict') == 'dead', (st_, j))
    st_, j = send('live@example.com')
    rec('a live follow-up sends once the scan covers its last email', st_ == 200 and j.get('ok'), (st_, j))
    rec('SMTP saw only the live address', smtp() == ['live@example.com'], smtp())
    st_, j = send('fresh@example.com')
    rec('a first touch is not held by this gate (it fails the deliverability gate as before)',
        j.get('blocked') != 'bounce_scan_stale', (st_, j))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


# ------------------------------------------------------------------ 4. cadence and outreach_email
print('\n-- cadence: the step is held, not consumed')
import cadence as CD
C = T / 'cad'
C.mkdir()
CD.HERE = str(C)
CD.QUEUE = str(C / 'cadence_queue.json')
CD.STATE = str(C / 'cadence_state.json')
CD.OPTOUTS = str(C / 'optouts.json')
CD._ss.HERE = str(C)
CD._ss.SENT_LEDGER = str(C / 'mail_sent.json')
CD._ss.REFUSAL_LOG = str(C / 'send_refusals.jsonl')
# PR #192: cadence asks the stay verdict from this folder's own docket cache; these cases have a docket read, no stay.
(C / 'sale_history_cache.json').write_text(json.dumps({c: {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0} for c in ['2099-000031-CA-01', '2099-000032-CA-01', '2099-000099-CA-01']}), encoding='utf-8')
json.dump([row('gone@example.com', 26, case='2099-000031-CA-01')], open(CD._ss.SENT_LEDGER, 'w'))
json.dump({'_dealflow_notes': 1, 'notes': {}}, open(CD.OPTOUTS, 'w'))
json.dump({'date': TODAY.isoformat(), 'state': 'finished', 'ok': True,
           'started_at': 0, 'finished_at': 1, 'steps': []}, open(C / 'sync_status.json', 'w'))
json.dump({}, open(C / 'bounced_emails.json', 'w'))
json.dump(v2({'box-a@example.org': mb(72)}), open(C / BS.STATUS, 'w'))


def cadence_run():
    json.dump({'sender': {'name': 'Test Sender'}, 'queue': [
        {'case': '2099-000031-CA-01', 'owner': 'G', 'email': 'gone@example.com', 'step': 1}]}, open(CD.QUEUE, 'w'))
    if os.path.exists(CD.STATE):
        os.remove(CD.STATE)
    CD._oe._load_optouts = lambda: set()
    CD._oe._load_leads = lambda: [{'case': '2099-000031-CA-01', 'emails': ['gone@example.com'], 'days': 40}]
    import diligence_gate as dg
    dg.gate = lambda r: {'hold': False, 'code': '', 'why': ''}
    CD.load_key = lambda: ('alejandro@bsgflorida.com', 'app-password')
    CD.imap_replies = lambda *a, **k: {}
    CD.steps = lambda s, sender: [('subj %d' % i, 'body %d' % i) for i in range(4)]
    CD._ss._deliverability_evidence = lambda: {
        'bounced': set(json.load(open(C / 'bounced_emails.json'))), 'replied': set(), 'ver': {},
        'proven': {'gone@example.com'}, 'last_mailed': {'gone@example.com': iso(26)[:10]}}
    sent = []
    CD._ss._smtp_send = lambda user, pw, disp, to, subj, body, bcc='', attach=None, from_addr=None: (
        sent.append(to) or '<mid-%d@test>' % len(sent))
    argv, so = sys.argv, sys.stdout
    sys.argv, buf = ['cadence.py'], io.StringIO()
    sys.stdout = buf
    try:
        CD.main()
    finally:
        sys.stdout, sys.argv = so, argv
    return sent, buf.getvalue(), json.load(open(CD.STATE))


sent, out, st = cadence_run()
s = st.get('2099-000031-CA-01', {})
rec('stale bounce scan: cadence does not send the follow-up', sent == [], (sent, out[-400:]))
rec('...the step stays due (not consumed)', s.get('step') == 1, s)
rec('...and the run says why', 'follow-up held' in out, out[-400:])
json.dump({'gone@example.com': {'first_seen': TODAY.isoformat(), 'reason': '550'}}, open(C / 'bounced_emails.json', 'w'))
json.dump(v2({'box-a@example.org': mb(1)}, listed=['gone@example.com']), open(C / BS.STATUS, 'w'))
sent, out, st = cadence_run()
rec('after the scan lists it, cadence suppresses the sequence', sent == []
    and st.get('2099-000031-CA-01', {}).get('status') == 'suppressed', (sent, st))

print('\n-- outreach_email: a previously mailed address is dropped until the scan covers it')
import outreach_email as OE
OE.HERE = str(C)
OE._BOUNCED = set()
OE.DILIGENCE.check = lambda r: {'hold': False, 'code': ''}
lead = {'Case #': '2099-000032-CA-01', 'emails': ['gone2@example.com'], 'Owner': 'Jane', 'days': 30}
OE._primary_email = lambda r: 'gone2@example.com'
import bk_lookup as _BKL
_BKL.contact_blocked_reason = lambda c, here=None: (False, '')
L2 = [row('gone2@example.com', 50, case='2099-000099-CA-01')]
json.dump(v2({'box-a@example.org': mb(72)}), open(C / BS.STATUS, 'w'))
okk, why = OE._eligible(lead, L2, set(), 24)
rec('stale scan: dropped with a follow-up-held reason', not okk and why.startswith('follow-up held'), (okk, why))
json.dump(v2({'box-a@example.org': mb(1)}), open(C / BS.STATUS, 'w'))
okk, why = OE._eligible(lead, L2, set(), 24)
rec('clean scan after the email: not dropped by this gate', not why.startswith('follow-up held'), (okk, why))

shutil.rmtree(T, ignore_errors=True)
print('\n%d passed, %d failed' % (len(ok), len(bad)))
if bad:
    print('FAILED: %s' % bad)
sys.exit(1 if bad else 0)
