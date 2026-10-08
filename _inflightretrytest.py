"""Fake tests for inflight-release-fix.patch: immediate retry and concurrent retry across the release.
python _inflightretrytest.py
Real send_server claim/ledger functions, ledger redirected to a temp file, fake address. No SMTP."""
import os, re, sys, tempfile, threading, datetime as dt
repo = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(repo))
import send_server as ss
ss.SENT_LEDGER = os.path.join(tempfile.mkdtemp(), 'mail_sent.json')
ADDR = 'homeowner@fake.invalid'

def row():
    return {'d': dt.date.today().isoformat(), 'ts_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'ch': 'email', 'to': ADDR, 'bcc': '', 'case': 'FAKE-2099-000001', 'message_id': '<fake>'}

def success_path(release_first=False):
    """What /send does after SMTP accepted the message, in the patched order (ledger, then release)."""
    assert ss._claim_recipients([ADDR])[0]
    if release_first:
        ss._release_recipients([ADDR]); ss._append_ledger(row())
    else:
        ss._append_ledger(row()); ss._release_recipients([ADDR])

def concurrent(release_first):
    wins, stop = [], threading.Event()
    def racer():
        while not stop.is_set():
            ok = ss._claim_recipients([ADDR])
            if ok[0]:
                wins.append(1); ss._release_recipients([ADDR])
    ss.SENT_LEDGER = os.path.join(tempfile.mkdtemp(), 'mail_sent.json'); ss._INFLIGHT.clear()
    orig = ss._append_ledger
    def slow_append(e):                      # widen the window between the two steps
        import time; time.sleep(0.05); return orig(e)
    ss._append_ledger = slow_append
    ts = [threading.Thread(target=racer) for _ in range(4)]
    try:
        assert ss._claim_recipients([ADDR])[0]
        for t in ts: t.start()
        if release_first:
            ss._release_recipients([ADDR]); ss._append_ledger(row())
        else:
            ss._append_ledger(row()); ss._release_recipients([ADDR])
        import time; time.sleep(0.2)
    finally:
        stop.set(); [t.join() for t in ts]; ss._append_ledger = orig
    return len(wins)

fails = 0
# 0) the patch puts the release AFTER the ledger write, and not in the except branch
src = open(os.path.join(repo, 'send_server.py'), encoding='utf-8').read()
body = re.search(r"The email is ALREADY SENT(.*?)return self\._json\(200, resp\)", src, re.S).group(1)
tb, eb = body.split('except Exception as e:', 1)
order_ok = '_release_recipients(_claimed)' in tb and tb.index('_append_ledger(') < tb.index('_release_recipients(_claimed)') and '_release_recipients' not in eb
print('release after the ledger write, kept on a failed write:', order_ok); fails += not order_ok
# 1) immediate retry after a recorded send: refused by the 24h ledger, with the real reason
ss._INFLIGHT.clear(); success_path()
r = ss._claim_recipients([ADDR])
ok1 = (not r[0]) and 'inside the last 24h' in r[2]
print('immediate retry:', r, '->', 'refused by ledger' if ok1 else 'NOT refused'); fails += not ok1
# 2) concurrent retries racing the release: none may win when the ledger is written first
w = concurrent(release_first=False)
print('concurrent retries that won (ledger then release):', w); fails += w != 0
# control: the test does catch the wrong order
wc = concurrent(release_first=True)
print('control, release then ledger (expected > 0):', wc); fails += wc == 0
print('PASS' if not fails else 'FAIL'); sys.exit(1 if fails else 0)
