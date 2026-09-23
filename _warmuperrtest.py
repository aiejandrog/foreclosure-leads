"""_warmuperrtest — guard for warmup.py's failure log. Runs on ANY checkout: no network, no keys.

BSG Warmup runs under pythonw, so a failed day printed its reason to nowhere: 09-17 and 09-19..21
sent nothing and nothing recorded why. warmup.py now retries twice and appends every failed attempt
(and every recovery) to warmup_errors.log. This proves:

  1. an exception (here: no key file) is written with its type, on each of the three attempts
  2. creds()'s sys.exit('refusing: ...') is written too — it is SystemExit, not Exception — and is
     not retried, since a retry cannot fix a wrong key
  3. a first attempt that fails and a second that connects sends the full day and logs 'recovered'
  4. a batch dropped mid-send is finished by the retry without resending what already went, and a
     second run the same day sends nothing more
  5. a clean run (--dry-run) writes nothing

SAFETY: warmup.py reads bsg_gmail.key next to itself, and on the laptop or desktop that file is a
live credential. So every case runs a COPY of warmup.py in a temp folder that holds no real key,
with DEALFLOW_DIR pointed at the temp folder, and a sitecustomize that replaces smtplib.SMTP_SSL
before any socket opens (it raises, or returns a fake that only counts). Nothing here can send.

Run:  python _warmuperrtest.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []

# Loaded into the warmup.py subprocess. Every mode blocks the real network; time.sleep is a no-op so
# the retry waits cost nothing. FLAKY fails the first connection and then accepts every later one
# on a fake server that only counts messages. MIDSEND drops the first connection after 7 messages.
NO_SMTP = ("import os, smtplib, time\n"
           "time.sleep = lambda s: None\n"
           "_calls = [0]\n"
           "class _Fake:\n"
           "    def __enter__(self): return self\n"
           "    def __exit__(self, *a): return False\n"
           "    def login(self, u, p): pass\n"
           "    def __init__(self, die_after=None): self.n, self.die = 0, die_after\n"
           "    def sendmail(self, f, t, m):\n"
           "        self.n += 1\n"
           "        if self.die is not None and self.n > self.die:\n"
           "            raise ConnectionResetError('dropped mid-batch by _warmuperrtest')\n"
           "def _smtp(*a, **k):\n"
           "    _calls[0] += 1\n"
           "    mode = os.environ.get('WARMUPTEST_MODE')\n"
           "    if mode == 'flaky' and _calls[0] > 1:\n"
           "        return _Fake()\n"
           "    if mode == 'midsend':\n"
           "        return _Fake(7 if _calls[0] == 1 else None)\n"
           "    raise RuntimeError('SMTP blocked by _warmuperrtest')\n"
           "smtplib.SMTP_SSL = _smtp\n"
           "smtplib.SMTP = _smtp\n")


def check(name, got, want):
    ok = got == want
    print(f'  {"pass" if ok else "FAIL"}  {name}' + ('' if ok else f'   got {got!r}, want {want!r}'))
    if not ok:
        FAILS.append(name)


def run_case(key_text, *args, mode='', runs=1):
    tmp = tempfile.mkdtemp(prefix='warmuperr_')
    try:
        for f in ('warmup.py', 'paths.py'):
            shutil.copy(os.path.join(HERE, f), tmp)
        with open(os.path.join(tmp, 'sitecustomize.py'), 'w') as f:
            f.write(NO_SMTP)
        if key_text is not None:
            with open(os.path.join(tmp, 'bsg_gmail.key'), 'w') as f:
                f.write(key_text)
        out = os.path.join(tmp, 'out')
        env = dict(os.environ, DEALFLOW_DIR=out, PYTHONPATH=tmp, WARMUPTEST_MODE=mode)
        for _ in range(runs):
            p = subprocess.run([sys.executable, os.path.join(tmp, 'warmup.py'), *args],
                               cwd=tmp, env=env, capture_output=True, text=True, timeout=60)
        errlog = os.path.join(out, 'warmup_errors.log')
        text = open(errlog, encoding='utf-8').read() if os.path.exists(errlog) else None
        sent = None
        wlog = os.path.join(out, 'warmup_log.json')
        if os.path.exists(wlog):
            days = json.load(open(wlog, encoding='utf-8'))['days']
            sent = sum(len(v) for d in days.values() for v in d.values())
        return p.returncode, text, sent
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


print('no key file -> every attempt is logged, then it gives up')
rc, log, sent = run_case(None)
check('exit code 1', rc, 1)
check('three attempts written', log is not None and len(log.splitlines()), 3)
check('names the exception', log is not None and 'FileNotFoundError' in log, True)
check('last line is attempt 3 of 3', log is not None and 'attempt 3 of 3' in log.splitlines()[-1], True)
check('nothing sent', sent, None)

print('non-company key -> creds() refusal is logged once, no retry')
rc, log, sent = run_case('someone@gmail.com:pw')
check('non-zero exit', rc != 0, True)
check('refusal written', log is not None and 'refusing: key is not a bsgflorida.com account' in log, True)
check('one line only', log is not None and len(log.splitlines()), 1)

print('company key, SMTP blocked -> three failed attempts, nothing sent')
rc, log, sent = run_case('alejandro@bsgflorida.com:pw')
check('exit code 1', rc, 1)
check('block reason written 3x', log is not None and log.count('SMTP blocked by _warmuperrtest'), 3)
check('nothing sent', sent, None)

print('first attempt fails, second connects -> the day is recovered and says so')
rc, log, sent = run_case('alejandro@bsgflorida.com:pw', mode='flaky')
check('exit code 0', rc, 0)
check('attempt 1 failure written', log is not None and 'attempt 1 of 3' in log, True)
check('recovery written last', log is not None and 'recovered on attempt 2 of 3' in log.splitlines()[-1], True)
check('full day sent (2 aliases x 15)', sent, 30)

print('run twice the same day -> the second run sends nothing more')
rc, log, sent = run_case('alejandro@bsgflorida.com:pw', mode='flaky', runs=2)
check('exit code 0', rc, 0)
check('still 30, no double send', sent, 30)

print('connection drops after 7 sends -> the retry sends only the missing 23')
rc, log, sent = run_case('alejandro@bsgflorida.com:pw', mode='midsend')
check('exit code 0', rc, 0)
check('drop written', log is not None and 'dropped mid-batch' in log, True)
check('exactly 30 in the log, none twice', sent, 30)

print('--dry-run -> no error log, nothing sent')
rc, log, sent = run_case(None, '--dry-run')
check('exit code 0', rc, 0)
check('no log file', log, None)
check('nothing sent', sent, None)

print()
print('FAILED: %s' % ', '.join(FAILS) if FAILS else 'all passed')
sys.exit(1 if FAILS else 0)
