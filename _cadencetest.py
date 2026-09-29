"""_cadencetest -- cadence.py's 2026-09-25 hardening, without mail, IMAP, or data files.

Run: python _cadencetest.py
 - the run lock: a second cadence on the same box does not start; a stale lock is taken over
 - steps(): an empty owner no longer raises IndexError
 - imap_replies(): a reply that only QUOTES our "reply 'stop'" line is 'replied', not 'stopped';
   a fresh "Please stop" above the quote is 'stopped'
"""
import os
import sys
import tempfile
import time
import types

sys.argv = [sys.argv[0]]
import cadence as C

pass_n = fail_n = 0
def T(name, ok, got=None):
    global pass_n, fail_n
    if ok: pass_n += 1; print('  PASS', name)
    else: fail_n += 1; print('  FAIL', name, '' if got is None else '[got %r]' % (got,))

print('== run lock ==')
tmp = tempfile.mkdtemp(prefix='cadlock_')
C._lock_dir = lambda: os.path.join(tmp, 'cadence.lock')
with C._RunLock() as a:
    T('first run takes the lock', a.held is True)
    with C._RunLock() as b:
        T('second run is refused while the first holds it', b.held is False)
T('lock released on exit', not os.path.isdir(os.path.join(tmp, 'cadence.lock')))
os.mkdir(os.path.join(tmp, 'cadence.lock'))
old = time.time() - 7 * 3600
os.utime(os.path.join(tmp, 'cadence.lock'), (old, old))
with C._RunLock() as c:
    T('a 7h-old lock (killed run) is taken over', c.held is True)

print('== steps() on a bad owner ==')
sender = {'name': 'Alex', 'phone': '(786) 631-1823', 'email': 'a@example.com', 'llc': ''}
for owner in ('', ' , ', None):
    try:
        out = C.steps({'owner': owner, 'addr': '1 MAIN ST, MIAMI, FL- 33101', 'auction': '2026-10-15'}, sender)
        T('owner=%r renders 4 steps' % (owner,), len(out) == 4 and 'there' in out[0][1])
    except Exception as e:
        T('owner=%r renders 4 steps' % (owner,), False, repr(e))

print('== imap_replies scans only the fresh text ==')
QUOTE = ("\r\n\r\nOn Mon, Sep 22, 2026 at 2:48 PM Alejandro Gonzalez <a@example.com> wrote:\r\n"
         "> If you'd rather not hear from me, reply 'stop' and you won't hear from me again.")
def msg(body):
    return ("From: owner@example.com\r\nSubject: Re: Regarding your property at 1 MAIN ST\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n" + body).encode('utf-8')
INBOX = {'yes@example.com': msg('Yes, call me tomorrow after 5.' + QUOTE),
         'stop@example.com': msg('Please stop.' + QUOTE),
         'es@example.com': msg('¿Pueden detener la venta? Llámeme.' + QUOTE)}

class FakeIMAP:
    def __init__(self, *a, **k): self.cur = None
    def login(self, u, p): pass
    def select(self, box): pass
    def search(self, cs, crit):
        for em in INBOX:
            if em in crit:
                self.cur = em
                return 'OK', [b'1']
        return 'OK', [b'']
    def fetch(self, n, what):
        return 'OK', [(b'1 (RFC822 {n})', INBOX[self.cur])]
    def logout(self): pass

fake = types.ModuleType('imaplib'); fake.IMAP4_SSL = FakeIMAP
sys.modules['imaplib'] = fake
out = C.imap_replies(('u', 'p'), list(INBOX), dry=False, since={'yes@example.com': '2026-09-20'})
T('quoted stop line does NOT stop the sequence', out.get('yes@example.com') == 'replied', out)
T('a fresh "Please stop." above the quote stops it', out.get('stop@example.com') == 'stopped', out)
T('"detener la venta" is a reply, not a stop', out.get('es@example.com') == 'replied', out)

print('\n%d passed, %d failed' % (pass_n, fail_n))
sys.exit(1 if fail_n else 0)
