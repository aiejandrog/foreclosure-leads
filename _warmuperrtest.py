"""_warmuperrtest — guard for warmup.py's failure log. Runs on ANY checkout: no network, no keys.

BSG Warmup runs under pythonw, so a failed day printed its reason to nowhere: 09-17 and 09-19..21
sent nothing and nothing recorded why. warmup.py now appends every non-zero exit to
warmup_errors.log. This proves:

  1. an exception (here: no key file) is written to the log with its type
  2. creds()'s sys.exit('refusing: ...') is written too — it is SystemExit, not Exception
  3. a clean run (--dry-run) writes nothing, so an entry always means a failed day

SAFETY: warmup.py reads bsg_gmail.key next to itself, and on the laptop or desktop that file is a
live credential. So every case runs a COPY of warmup.py in a temp folder that holds no real key,
with DEALFLOW_DIR pointed at the temp folder, and a sitecustomize that makes smtplib.SMTP_SSL raise
before any socket opens. Nothing here can send.

Run:  python _warmuperrtest.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []

NO_SMTP = ("import smtplib\n"
           "def _refuse(*a, **k):\n"
           "    raise RuntimeError('SMTP blocked by _warmuperrtest')\n"
           "smtplib.SMTP_SSL = _refuse\n"
           "smtplib.SMTP = _refuse\n")


def check(name, got, want):
    ok = got == want
    print(f'  {"pass" if ok else "FAIL"}  {name}' + ('' if ok else f'   got {got!r}, want {want!r}'))
    if not ok:
        FAILS.append(name)


def run_case(key_text, *args):
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
        env = dict(os.environ, DEALFLOW_DIR=out, PYTHONPATH=tmp)
        p = subprocess.run([sys.executable, os.path.join(tmp, 'warmup.py'), *args],
                           cwd=tmp, env=env, capture_output=True, text=True, timeout=60)
        errlog = os.path.join(out, 'warmup_errors.log')
        text = open(errlog, encoding='utf-8').read() if os.path.exists(errlog) else None
        return p.returncode, text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


print('no key file -> exception is logged')
rc, log = run_case(None)
check('exit code 1', rc, 1)
check('one line written', log is not None and len(log.splitlines()), 1)
check('names the exception', log is not None and 'FileNotFoundError' in log, True)

print('non-company key -> creds() refusal is logged')
rc, log = run_case('someone@gmail.com:pw')
check('non-zero exit', rc != 0, True)
check('refusal written', log is not None and 'refusing: key is not a bsgflorida.com account' in log, True)

print('company key, SMTP blocked -> connection failure is logged, nothing sent')
rc, log = run_case('alejandro@bsgflorida.com:pw')
check('exit code 1', rc, 1)
check('block reason written', log is not None and 'SMTP blocked by _warmuperrtest' in log, True)

print('--dry-run -> no error log')
rc, log = run_case(None, '--dry-run')
check('exit code 0', rc, 0)
check('no log file', log, None)

print()
print('FAILED: %s' % ', '.join(FAILS) if FAILS else 'all passed')
sys.exit(1 if FAILS else 0)
