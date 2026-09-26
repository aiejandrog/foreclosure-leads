"""First-touch deliverability gate, one address per person, the /health count, and the 07:15
bounce harvest. Fake addresses (example.com), fake SMTP, temp folders; no network, no real data.

WHAT THIS GUARDS (2026-09-26). On 2026-09-19 the laptop mailed 40 first-touch messages to 85 fresh
Tracerfy addresses (To + BCC). 31 hard-bounced, 36%, and the 7-day bounce breaker then shut every
send for a week. None of the 31 was on the bounce list at send time; the BCC'd extras died at 44%.
The bounces were not harvested until 09-24, five days later. So:
  1. /send mails ONE address per person, and only one with evidence it is live: delivered before
     (>= 48h of silence after a send), the owner replied from it, or ZeroBounce 'valid'. Risky,
     catch-all, unknown/unverified, dead, and mailed-but-not-yet-proven addresses are HELD (451 +
     skip, a reason per address, nothing sent, no credit spent). meta.test sends are exempt.
  2. /health reports how many first-touch addresses in the pool that gate would hold.
  3. morning_sync.py runs bounces.py AFTER the sync verdict is written; a bounce-scan failure is
     logged and never holds sends or changes the exit code.
  4. First touches leave only from the warm-up addresses in senders.json first_touch.from, rotating,
     each under a low first-touch cap; bsgflorida.com rests from first touches but keeps replies and
     follow-ups, and a follow-up stays on the sender that started the thread.
"""
import datetime as dt
import importlib.util
import json
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
ok, bad = [], []
TODAY = dt.date.today()


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:240]) if d and not cond else ''))


def day(n):
    return (TODAY - dt.timedelta(days=n)).isoformat()


def ts(n_hours):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=n_hours)).isoformat()


CASE = '2099-000003-CA-01'          # never stayed: the stay gate lets it through
CACHE = {CASE: {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0},
         '2099-000011-CA-01': {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0}}
VERIFIED = {
    'zb@example.com': {'v': 'ok', 'why': 'zerobounce:valid', 'd': day(3)},
    'zb2@example.com': {'v': 'ok', 'why': 'zerobounce:valid', 'd': day(3)},
    'zb3@example.com': {'v': 'ok', 'why': 'zerobounce:valid', 'd': day(3)},
    'zb4@example.com': {'v': 'ok', 'why': 'zerobounce:valid', 'd': day(3)},
    'nb@example.com': {'v': 'ok', 'why': 'neverbounce:valid', 'd': day(3)},
    'risky@example.com': {'v': 'risky', 'why': 'domain 60% dead, unverified', 'd': day(3)},
    'catch@example.com': {'v': 'risky', 'why': 'zerobounce:catch-all/', 'd': day(3)},
    'unk@example.com': {'v': 'unknown', 'why': 'no evidence either way', 'd': day(3)},
    'vdead@example.com': {'v': 'dead', 'why': 'zerobounce:invalid/mailbox_not_found', 'd': day(3)},
    'delivered@example.com': {'v': 'risky', 'why': 'domain 60% dead, unverified', 'd': day(9)},
}
BOUNCED = {'dead@example.com': {'first_seen': day(20), 'reason': 'Address not found'}}
REPLIES = {'@replied@example.com': {'when': 'x'}, CASE: {'when': 'x'}}
LEDGER = [
    {'d': day(5), 'ts_utc': ts(5 * 24), 'ch': 'email', 'from': 'tester@example.com', 'to': 'delivered@example.com',
     'bcc': '', 'case': '2099-000010-CA-01', 'message_id': '<1@example.com>'},
    {'d': day(1), 'ts_utc': ts(30), 'ch': 'email', 'from': 'tester@example.com', 'to': 'recent@example.com',
     'bcc': '', 'case': '2099-000012-CA-01', 'message_id': '<2@example.com>'},
]
# /health pool: A sendable (2nd address valid), B held (risky + catch-all), C held (dead only),
# D already mailed (case in the ledger), E opted out
LEADS = [{'Case #': c} for c in ('2099-000020-CA-01', '2099-000021-CA-01', '2099-000022-CA-01',
                                 '2099-000010-CA-01', '2099-000023-CA-01')]
SKIP = {'2099-000020-CA-01': {'emails': ['unk@example.com', 'zb3@example.com'], 'source': 'tracerfy'},
        '2099-000021-CA-01': {'emails': ['risky@example.com', 'catch@example.com'], 'source': 'tracerfy'},
        '2099-000022-CA-01': {'emails': ['vdead@example.com'], 'source': 'tracerfy'},
        '2099-000010-CA-01': {'emails': ['other@example.com'], 'source': 'tracerfy'},
        '2099-000023-CA-01': {'emails': ['optedout@example.com'], 'source': 'tracerfy'}}
OPTOUTS = {'_dealflow_notes': True, 'notes': {'2099-000023-CA-01': {'status': 'DO NOT CONTACT'}}}


def seed(d):
    for f in ('send_server.py', 'stay_gate.py', 'mail_guard.py', 'sync_gate.py'):
        shutil.copy(HERE / f, d / f)
    w = lambda n, v: (d / n).write_text(json.dumps(v), encoding='utf-8')
    w('sync_status.json', {'date': TODAY.isoformat(), 'state': 'finished', 'ok': True,
                           'started_at': time.time() - 120, 'finished_at': time.time() - 60, 'steps': []})
    (d / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
    w('sender.json', {'name': 'Test Sender'})
    w('optouts.json', OPTOUTS)
    w('sale_history_cache.json', CACHE)
    w('verified_emails.json', VERIFIED)
    w('bounced_emails.json', BOUNCED)
    w('replies.json', REPLIES)
    w('mail_sent.json', LEDGER)
    w('leads_final.json', LEADS)
    w('skiptrace_results.json', SKIP)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------ 1. the verdicts, in process
print('-- verdicts')
T = pathlib.Path(tempfile.mkdtemp(prefix='dfft_'))
U = T / 'unit'
U.mkdir()
seed(U)
S = load(U / 'send_server.py', 'send_server_ft_unit')
ev = S._deliverability_evidence()
want = {'zb@example.com': 'zerobounce_valid', 'delivered@example.com': 'delivered',
        'replied@example.com': 'replied', 'recent@example.com': 'awaiting_delivery',
        'risky@example.com': 'risky', 'catch@example.com': 'catch_all', 'unk@example.com': 'unverified',
        'nb@example.com': 'unverified', 'never-seen@example.com': 'unverified',
        'dead@example.com': 'dead', 'vdead@example.com': 'dead'}
for a, code in want.items():
    got = S._recipient_verdict(a, ev)
    rec('%s -> %s' % (a.split('@')[0], code), got[0] == code and got[1], got)
rec('only delivered / replied / zerobounce_valid pass', set(S.GATE_PASS) == {'delivered', 'replied', 'zerobounce_valid'})
rec('delivered beats a risky verifier verdict (acceptance is evidence)', S._recipient_verdict('delivered@example.com', ev)[0] == 'delivered')
rec('a bounce beats everything', S._recipient_verdict('dead@example.com', dict(ev, proven=ev['proven'] | {'dead@example.com'}))[0] == 'dead')
pick, vs = S._pick_recipient(['unk@example.com', 'risky@example.com', 'zb@example.com', 'zb2@example.com'], ev)
rec('the pick is the FIRST address that passes', pick == 'zb@example.com' and len(vs) == 4, (pick, vs))
pick, vs = S._pick_recipient(['unk@example.com', 'UNK@example.com ', 'catch@example.com'], ev)
rec('nothing passes -> no pick, duplicates collapsed', pick == '' and len(vs) == 2, (pick, vs))
S.FIRST_TOUCH_ADMIT_ZEROBOUNCE_VALID = False
rec('with the ZeroBounce switch off, zerobounce valid is held', S._recipient_verdict('zb@example.com', ev)[0] == 'unverified')
S.FIRST_TOUCH_ADMIT_ZEROBOUNCE_VALID = True
E = T / 'empty'
E.mkdir()
for f in ('send_server.py', 'stay_gate.py', 'mail_guard.py', 'sync_gate.py'):
    shutil.copy(HERE / f, E / f)
S2 = load(E / 'send_server.py', 'send_server_ft_empty')
rec('no verification files at all -> hold (never a pass by default)',
    S2._recipient_verdict('zb@example.com', S2._deliverability_evidence())[0] == 'unverified')
src = (HERE / 'send_server.py').read_text(encoding='utf-8')
rec('the gate never calls a verification API', 'zerobounce.net' not in src and 'neverbounce.com' not in src)
h = S._first_touch_queue_health()
rec('/health pool: 3 first-touch leads (mailed + opted-out excluded)', h.get('leads') == 3, h)
rec('/health pool: 1 sendable, 2 held', h.get('leads_sendable') == 1 and h.get('leads_held') == 2, h)
rec('/health pool: 5 addresses, 1 pass, 3 held unverified (dead counted apart)',
    h.get('addresses') == 5 and h.get('addresses_pass') == 1 and h.get('held_unverified') == 3
    and h.get('by_verdict', {}).get('dead') == 1, h)
rec('/health pool carries no address', '@' not in json.dumps(h), h)


# ------------------------------------------------------------------ 2. the live bridge
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


print('\n-- live bridge')
W = T / 'srv'
W.mkdir()
seed(W)
port = free_port()
(W / '_run_bridge.py').write_text(
    'import sys, json, smtplib\n'
    'class _FakeSMTP:\n'
    '    def __init__(self, *a, **k): pass\n'
    '    def __enter__(self): return self\n'
    '    def __exit__(self, *a): return False\n'
    '    def login(self, u, p): pass\n'
    '    def send_message(self, m, **k):\n'
    '        open("smtp_calls.jsonl", "a", encoding="utf-8").write(json.dumps({"to": str(m["To"]), "bcc": str(m["Bcc"] or "")}) + "\\n")\n'
    '        return {}\n'
    'smtplib.SMTP_SSL = _FakeSMTP\n'
    'sys.argv = ["send_server.py", "--port", "%d", "--limit", "50"]\n'
    'exec(open("send_server.py", encoding="utf-8").read())\n' % port, encoding='utf-8')
proc = subprocess.Popen([sys.executable, str(W / '_run_bridge.py')], cwd=str(W),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    up = False
    for _ in range(60):
        if call(port, '/health')[0] == 200:
            up = True
            break
        time.sleep(0.25)
    rec('bridge starts', up)

    def smtp():
        p = W / 'smtp_calls.jsonl'
        return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []

    def ledger():
        return json.loads((W / 'mail_sent.json').read_text(encoding='utf-8'))

    def refusals():
        p = W / 'send_refusals.jsonl'
        return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []

    def send(to, bcc='', test=False, case=CASE):
        meta = {'owner': 'Jane', 'addr': '1 Main St', 'wl': 'active', 'c': case}
        if test:
            meta['test'] = True
        return call(port, '/send', {'to': to, 'bcc': bcc, 'subj': 'About 1 Main St',
                                    'body': 'Hello Jane, a short note about 1 Main St.', 'meta': meta})

    st, hj = call(port, '/health')
    rec('/health has first_touch_held_unverified = 3', hj.get('first_touch_held_unverified') == 3, hj.get('first_touch_gate'))
    rec('/health first_touch_gate breakdown present', (hj.get('first_touch_gate') or {}).get('leads') == 3)
    rec('existing /health keys unchanged', all(k in hj for k in ('sync_ok', 'bounce', 'stay_data', 'proven_pool', 'recipients_today')))
    n0 = len(ledger())

    for to, code in (('unk@example.com', 'unverified'), ('risky@example.com', 'risky'),
                     ('catch@example.com', 'catch_all'), ('dead@example.com', 'dead'),
                     ('never-seen@example.com', 'unverified')):
        st, j = send(to)
        rec('first touch to a %s address: 451 held, skip, blocked=unverified_first_touch' % code,
            st == 451 and j.get('skip') is True and j.get('blocked') == 'unverified_first_touch'
            and (j.get('verdicts') or [{}])[0].get('verdict') == code and 'HELD' in j.get('err', ''), (st, j))
    st, j = send('recent@example.com')
    rec('follow-up to an address mailed yesterday: held awaiting_delivery (unverified_recipient)',
        st == 451 and j.get('blocked') == 'unverified_recipient'
        and j['verdicts'][0]['verdict'] == 'awaiting_delivery', (st, j))
    st, j = send('unk@example.com', 'risky@example.com, catch@example.com')
    rec('To + BCC all without evidence: held, one verdict per address', st == 451 and len(j.get('verdicts') or []) == 3, j)
    rec('no held send reached SMTP or the ledger', not smtp() and len(ledger()) == n0, (smtp(), len(ledger())))
    rf = refusals()
    rec('every hold is in send_refusals.jsonl as gate=deliverability, with no address',
        len(rf) == 7 and all(r.get('gate') == 'deliverability' for r in rf) and '@' not in json.dumps(rf), rf[:2])

    st, j = send('zb@example.com', 'unk@example.com, risky@example.com')
    c = smtp()
    rec('ZeroBounce-valid first touch sends (200)', st == 200 and j.get('ok') is True, (st, j))
    rec('...to that ONE address: BCC dropped', len(c) == 1 and c[0]['to'] == 'zb@example.com' and c[0]['bcc'] == '', c)
    rec('...response names the address, the verdict and the drop count',
        j.get('to') == 'zb@example.com' and j.get('gate') == 'zerobounce_valid' and j.get('dropped') == 2, j)
    rec('...ledger row has an empty bcc', ledger()[-1]['to'] == 'zb@example.com' and ledger()[-1]['bcc'] == '', ledger()[-1])

    st, j = send('unk@example.com', 'catch@example.com, zb2@example.com, zb4@example.com')
    c = smtp()
    rec('To unverified, a BCC address valid: sent to the first valid one only',
        st == 200 and c[-1] == {'to': 'zb2@example.com', 'bcc': ''} and j.get('to') == 'zb2@example.com'
        and j.get('dropped') == 3, (st, j, c[-1:]))

    st, j = send('delivered@example.com')
    rec('follow-up to an address that delivered before: sends (200) despite a risky verdict',
        st == 200 and j.get('gate') == 'delivered', (st, j))
    st, j = send('replied@example.com')
    rec('an owner who replied from this address: sends (200)', st == 200 and j.get('gate') == 'replied', (st, j))
    st, j = send('advisor-unk@example.com', test=True)
    rec('meta.test (1:1 to the advisor/operator) is exempt from the gate', st == 200 and 'gate' not in j, (st, j))
    st, hj2 = call(port, '/health')
    rec('recipients_today counts one per send: no BCC fan-out', hj2.get('recipients_today') == 5, hj2.get('recipients_today'))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


# ------------------------------------------------------------------ 2b. first touch from the warm-up domains
print('\n-- first touches rotate across the warm-up senders; the main domain rests')
MAIN = 'alejandro@bsgflorida.com'
WU1, WU2 = 'alejandro@bsgfl.com', 'alejandro@biscaynesolutionsgroup.com'
repo_senders = json.loads((HERE / 'senders.json').read_text(encoding='utf-8'))
rec('repo senders.json: first touches from the two warm-up addresses, main domain not among them',
    repo_senders.get('first_touch', {}).get('from') == [WU1, WU2])
SU = T / 'sunit'
SU.mkdir()
seed(SU)
S3 = load(SU / 'send_server.py', 'send_server_ft_senders')
rec('repo config: first-touch cap is 10/address on its first day, never above the alias ramp',
    all(S3._first_touch_cap(repo_senders, a, dt.date.fromisoformat(repo_senders['first_touch']['ramp_start'])) ==
        min(10, S3._ramp_cap(repo_senders, a, dt.date.fromisoformat(repo_senders['first_touch']['ramp_start'])))
        for a in (WU1, WU2)))
rec('repo config: 0 before first_touch.ramp_start',
    S3._first_touch_cap(repo_senders, WU1, dt.date.fromisoformat(repo_senders['first_touch']['ramp_start']) - dt.timedelta(days=1)) == 0)
rec('repo config: the ramp rises slowly (15 in week 2)',
    S3._first_touch_cap(repo_senders, WU1, dt.date.fromisoformat(repo_senders['first_touch']['ramp_start']) + dt.timedelta(days=8))
    == min(15, S3._ramp_cap(repo_senders, WU1, dt.date.fromisoformat(repo_senders['first_touch']['ramp_start']) + dt.timedelta(days=8))))
flat = {'main_domain': 'bsgflorida.com', 'ramp_start': day(40), 'ramp': [{'through_day': 9999, 'per_day': 100}],
        'lanes': {'default': MAIN}, 'first_touch': {'from': [WU1, MAIN, WU2, 'junk']}}
rec('no first_touch ramp -> flat default of 10/day', S3._first_touch_cap(flat, WU1) == S3.FIRST_TOUCH_DEFAULT_PER_DAY == 10)
rec('the main domain and junk are never first-touch senders', S3._first_touch_senders(flat) == [WU1, WU2])
tiny = dict(flat, ramp=[{'through_day': 9999, 'per_day': 1}])
rec('the alias ramp still binds when it is lower', S3._first_touch_cap(tiny, WU1) == 1)
rec('no first_touch block -> no first-touch senders (old lane map)', S3._first_touch_senders({'lanes': {}}) == [])


def start_bridge(d, login):
    (d / 'gmail.key').write_text('%s:abcdabcdabcdabcd\n' % login, encoding='utf-8')
    port_ = free_port()
    (d / '_run_bridge.py').write_text(
        'import sys, json, smtplib\n'
        'class _FakeSMTP:\n'
        '    def __init__(self, *a, **k): pass\n'
        '    def __enter__(self): return self\n'
        '    def __exit__(self, *a): return False\n'
        '    def login(self, u, p): pass\n'
        '    def send_message(self, m, from_addr=None, **k):\n'
        '        open("smtp_calls.jsonl", "a", encoding="utf-8").write(json.dumps({"to": str(m["To"]), "from": str(m["From"]), "env": from_addr}) + "\\n")\n'
        '        return {}\n'
        'smtplib.SMTP_SSL = _FakeSMTP\n'
        'sys.argv = ["send_server.py", "--port", "%d", "--limit", "50"]\n'
        'exec(open("send_server.py", encoding="utf-8").read())\n' % port_, encoding='utf-8')
    pr = subprocess.Popen([sys.executable, str(d / '_run_bridge.py')], cwd=str(d),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if call(port_, '/health')[0] == 200:
            break
        time.sleep(0.25)
    return pr, port_


def smtp_of(d):
    p = d / 'smtp_calls.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


def send_to(port_, to, wl='active'):
    return call(port_, '/send', {'to': to, 'subj': 'About 1 Main St', 'body': 'Hello Jane, a short note about 1 Main St.',
                                 'meta': {'owner': 'Jane', 'addr': '1 Main St', 'wl': wl, 'c': CASE}})


SW = T / 'ssrv'
SW.mkdir()
seed(SW)
cfg = dict(repo_senders)
cfg['ramp_start'] = day(40)                                          # alias ramp: 100/day, not binding
cfg['first_touch'] = dict(repo_senders['first_touch'], ramp_start=TODAY.isoformat(),
                          ramp=[{'through_day': 9999, 'per_day': 2}])  # 2 first touches per address today
(SW / 'senders.json').write_text(json.dumps(cfg), encoding='utf-8')
extra = {'zb%d@example.com' % i: {'v': 'ok', 'why': 'zerobounce:valid', 'd': day(1)} for i in range(10, 20)}
(SW / 'verified_emails.json').write_text(json.dumps(dict(VERIFIED, **extra)), encoding='utf-8')
led = list(LEDGER) + [{'d': day(6), 'ts_utc': ts(6 * 24), 'ch': 'email', 'from': WU2, 'to': 'thread@example.com',
                       'bcc': '', 'case': '2099-000013-CA-01', 'message_id': '<3@example.com>'}]
led[0] = dict(led[0], **{'from': MAIN})
(SW / 'mail_sent.json').write_text(json.dumps(led), encoding='utf-8')
proc2, p2 = start_bridge(SW, MAIN)
try:
    froms = []
    for i in range(10, 14):
        st, j = send_to(p2, 'zb%d@example.com' % i, wl='urgent' if i % 2 else 'active')
        froms.append((st, j.get('sent_from'), j.get('first_touch')))
    rec('4 first touches: all sent, marked first_touch', all(f[0] == 200 and f[2] is True for f in froms), froms)
    rec('...rotating bsgfl -> biscayne -> bsgfl -> biscayne, whatever the lane',
        [f[1] for f in froms] == [WU1, WU2, WU1, WU2], froms)
    c = smtp_of(SW)
    rec('...From header and envelope are the warm-up address, never the main domain',
        all(x['env'] in (WU1, WU2) and MAIN not in x['from'] for x in c), c)
    st, j = send_to(p2, 'zb14@example.com')
    rec('5th first touch: both addresses at their first-touch cap -> 409 skip, nothing sent',
        st == 409 and j.get('skip') is True and j.get('first_touch_cap') is True and len(smtp_of(SW)) == 4, (st, j))
    rows = json.loads((SW / 'mail_sent.json').read_text(encoding='utf-8'))
    rec('ledger marks those rows touch=first', sum(1 for r in rows if r.get('touch') == 'first') == 4)
    st, j = send_to(p2, 'delivered@example.com', wl='active')
    rec('follow-up still sends after first-touch capacity is used, from the main domain that started the thread',
        st == 200 and j.get('sent_from') == MAIN and j.get('first_touch') is False, (st, j))
    (SW / 'verified_emails.json').write_text(json.dumps(dict(VERIFIED, **extra, **{'thread@example.com': {'v': 'unknown', 'why': '', 'd': day(9)}})), encoding='utf-8')
    st, j = send_to(p2, 'thread@example.com', wl='urgent')
    rec('follow-up to a recipient first mailed from biscayne stays on biscayne (lane says main domain)',
        st == 200 and j.get('sent_from') == WU2, (st, j))
    st, j = send_to(p2, 'replied@example.com', wl='replied')
    rec('a reply (owner wrote to us, never mailed) is not a first touch: lane map -> main domain',
        st == 200 and j.get('first_touch') is False and j.get('sent_from') == MAIN, (st, j))
    st, hj = call(p2, '/health')
    ff = hj.get('first_touch_from') or {}
    rec('/health first_touch_from: active, main domain rests, per-address use vs caps',
        ff.get('active') is True and ff.get('rests') == 'bsgflorida.com'
        and [(x['addr'], x['first_touch_today'], x['first_touch_cap']) for x in ff.get('senders', [])] == [(WU1, 2, 2), (WU2, 2, 2)], ff)
finally:
    proc2.terminate()
    try:
        proc2.wait(timeout=5)
    except Exception:
        proc2.kill()

SX = T / 'xsrv'
SX.mkdir()
seed(SX)
(SX / 'senders.json').write_text(json.dumps(cfg), encoding='utf-8')
proc3, p3 = start_bridge(SX, 'tester@example.com')       # a login that cannot send as the aliases
try:
    st, j = send_to(p3, 'zb@example.com')
    rec('login cannot send as the warm-up domains: first touch held (409 skip), nothing sent',
        st == 409 and j.get('first_touch_cap') is True and not smtp_of(SX), (st, j))
    st, j = send_to(p3, 'delivered@example.com')
    rec('...follow-ups still send as before', st == 200, (st, j))
finally:
    proc3.terminate()
    try:
        proc3.wait(timeout=5)
    except Exception:
        proc3.kill()


# ------------------------------------------------------------------ 3. the 07:15 bounce harvest
print('\n-- morning_sync runs bounces.py after the verdict')
FAKE_OK = {
    'replies.py': "import json,os; json.dump({'@x@example.com': {}}, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'replies.json'),'w'))",
    'optout_sync.py': 'print("ok")',
    'ledger_sync.py': 'print("SYNCED")',
}
BOUNCES_OK = ("import json; st = json.load(open('sync_status.json', encoding='utf-8'));"
              "open('bounces_saw.txt', 'w').write(str(st.get('state')) + ' ' + str(st.get('ok'))); print('hard bounces: 0 found')")


def msync(bounces=None, **over):
    d = pathlib.Path(tempfile.mkdtemp(prefix='msync_', dir=str(T)))
    shutil.copy(HERE / 'morning_sync.py', d / 'morning_sync.py')
    shutil.copy(HERE / 'sync_gate.py', d / 'sync_gate.py')
    for n, body in dict(FAKE_OK, **over).items():
        (d / n).write_text(body, encoding='utf-8')
    if bounces is not None:
        (d / 'bounces.py').write_text(bounces, encoding='utf-8')
    p = subprocess.run([sys.executable, 'morning_sync.py'], cwd=str(d), capture_output=True, text=True, timeout=120)
    return d, p.returncode, p.stdout + p.stderr, json.loads((d / 'sync_status.json').read_text(encoding='utf-8'))


G = load(HERE / 'sync_gate.py', 'sync_gate_ft')
d, rc, out, st = msync(BOUNCES_OK)
rec('bounces.py runs, recorded under "after"', [a['name'] for a in st.get('after', [])] == ['bounces']
    and st['after'][0]['ok'] is True, st.get('after'))
rec('...after the verdict was written (it saw state=finished ok=True)',
    (d / 'bounces_saw.txt').read_text() == 'finished True', (d / 'bounces_saw.txt').read_text())
rec('...and is not one of the verdict steps', [s['name'] for s in st['steps']] == ['replies', 'optout_sync', 'ledger_sync'])
rec('...sync still exit 0 and the gate passes', rc == 0 and st['ok'] is True and G.verdict(path=str(d / 'sync_status.json'))['ok'])

d, rc, out, st = msync('raise SystemExit(2)')
rec('bounces.py fails: sync exit stays 0, ok stays True', rc == 0 and st['ok'] is True and st['state'] == 'finished', out[-300:])
rec('...the failure is recorded and logged, sends not held',
    st['after'][0]['ok'] is False and 'Logged only' in out and G.verdict(path=str(d / 'sync_status.json'))['ok'], st['after'])

d, rc, out, st = msync(None)
rec('bounces.py missing: sync still OK, recorded as not found',
    rc == 0 and st['ok'] is True and 'not found' in st['after'][0].get('why', ''), st.get('after'))

d, rc, out, st = msync(BOUNCES_OK, **{'optout_sync.py': 'raise SystemExit(2)'})
rec('a real sync failure still exits 1 and holds (bounces cannot mask it)',
    rc == 1 and st['ok'] is False and not G.verdict(path=str(d / 'sync_status.json'))['ok'], out[-200:])

d = pathlib.Path(tempfile.mkdtemp(prefix='msync_', dir=str(T)))
shutil.copy(HERE / 'morning_sync.py', d / 'morning_sync.py')
for n, body in FAKE_OK.items():
    (d / n).write_text(body, encoding='utf-8')
(d / 'bounces.py').write_text('import time; time.sleep(30)', encoding='utf-8')
M = load(d / 'morning_sync.py', 'morning_sync_ft')
M.AFTER = (('bounces', 'bounces.py', 1),)
t0 = time.time()
rc = M.main()
st = json.loads((d / 'sync_status.json').read_text(encoding='utf-8'))
rec('a hung bounce scan is killed at its timeout; sync result unchanged',
    rc == 0 and st['ok'] is True and st['after'][0]['rc'] == 'timeout' and time.time() - t0 < 25, st.get('after'))

ms = (HERE / 'morning_sync.py').read_text(encoding='utf-8')
rec('AFTER names bounces.py with a bounded timeout', "('bounces', 'bounces.py', 8 * 60)" in ms)
bat = (HERE / 'run-optout-sync.bat').read_text(encoding='utf-8')
rec('run-optout-sync.bat still runs morning_sync.py (which runs bounces.py)', 'python -u morning_sync.py' in bat)

shutil.rmtree(T, ignore_errors=True)
print('\n==== %d/%d first-touch gate checks passed ====' % (len(ok), len(ok) + len(bad)))
if bad:
    print('FAILED: %s' % bad)
sys.exit(1 if bad else 0)
