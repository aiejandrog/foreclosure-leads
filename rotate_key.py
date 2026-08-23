#!/usr/bin/env python
"""rotate_key — install a freshly-issued vendor key, but only after proving it works.

WHY (2026-08-22). DEALFLOW_TRANSFER_2026-08-20.zip was sitting on the OneDrive-synced Desktop with
a secrets/ folder inside: 2Captcha, Tracerfy x2, Whitepages, StreetView, ZeroBounce, Gmail and the
Sheets CRM webhook. All eight were still the LIVE keys — three days in consumer cloud storage. The
bundle was moved out; rotating what was exposed is the actual remediation.

The dangerous part of rotating by hand is the half-second where you have overwritten the working
key with a typo'd one and every scraper starts failing for a reason nobody connects to this. So:
verify the candidate against the vendor's own balance endpoint FIRST, back up the old key, write
only on success, and re-verify after the write.

    python rotate_key.py 2captcha  NEWKEY
    python rotate_key.py tracerfy  NEWKEY
    python rotate_key.py 2captcha  --check          # just test what is installed now

The key is passed as an argument, never printed. Backups land in .key-backups/ (gitignored).
"""
import argparse
import os
import shutil
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP = os.path.join(HERE, '.key-backups')

VENDORS = {
    '2captcha': {
        'file': 'captcha.key',
        'console': 'https://2captcha.com/setting',
        'note': 'Settings page -> API key -> the reset/regenerate control.',
    },
    'tracerfy': {
        'file': 'tracerfy.key',
        'console': 'https://tracerfy.com/',
        'note': 'Sign in -> account/API settings -> regenerate the API key.',
    },
}


def check_2captcha(key):
    """(ok, detail) from 2Captcha's own getbalance. A bad key answers ERROR_WRONG_USER_KEY."""
    import requests
    try:
        r = requests.get('https://2captcha.com/res.php',
                         params={'key': key, 'action': 'getbalance', 'json': 1}, timeout=30)
        j = r.json()
        if str(j.get('status')) == '1':
            return True, 'balance $%s' % j.get('request')
        return False, str(j.get('request'))[:70]
    except Exception as e:
        return False, 'network: %s' % str(e)[:60]


def check_tracerfy(key):
    """Reuse the module's own balance path so this can never drift from how the app authenticates."""
    old = os.environ.get('TRACERFY_API_KEY')
    os.environ['TRACERFY_API_KEY'] = key
    try:
        import importlib
        m = importlib.import_module('tracerfy_mcp')
        importlib.reload(m)
        for fn in ('balance', 'get_balance', 'check_balance'):
            f = getattr(m, fn, None)
            if callable(f):
                v = f()
                return (v is not None), 'credits: %s' % v
        return False, 'tracerfy_mcp exposes no balance function to verify against'
    except Exception as e:
        return False, str(e)[:70]
    finally:
        if old is None:
            os.environ.pop('TRACERFY_API_KEY', None)
        else:
            os.environ['TRACERFY_API_KEY'] = old


CHECKS = {'2captcha': check_2captcha, 'tracerfy': check_tracerfy}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('vendor', choices=sorted(VENDORS))
    ap.add_argument('newkey', nargs='?', default='')
    ap.add_argument('--check', action='store_true', help='test the installed key, change nothing')
    a = ap.parse_args()

    v = VENDORS[a.vendor]
    path = os.path.join(HERE, v['file'])
    check = CHECKS[a.vendor]

    if a.check or not a.newkey:
        if not os.path.exists(path):
            print('no %s on disk' % v['file'])
            return 1
        ok, detail = check(open(path, encoding='utf-8').read().strip())
        print('installed %-9s %s  (%s)' % (a.vendor, 'WORKS' if ok else 'FAILS', detail))
        if not a.newkey:
            print('\nto rotate: get a new key at %s\n  %s\n  then: python rotate_key.py %s NEWKEY'
                  % (v['console'], v['note'], a.vendor))
        return 0 if ok else 2

    new = a.newkey.strip()
    if len(new) < 16:
        print('that does not look like a key (%d chars) — refusing' % len(new))
        return 2

    print('1/4  testing the NEW key against %s ...' % a.vendor)
    ok, detail = check(new)
    if not ok:
        print('     REJECTED: %s' % detail)
        print('     nothing was changed — the working key is still in place.')
        return 2
    print('     accepted (%s)' % detail)

    os.makedirs(BACKUP, exist_ok=True)
    if os.path.exists(path):
        stamp = time.strftime('%Y%m%d-%H%M%S')
        bak = os.path.join(BACKUP, '%s.%s.bak' % (v['file'], stamp))
        shutil.copy2(path, bak)
        print('2/4  old key backed up -> .key-backups/%s' % os.path.basename(bak))
    else:
        print('2/4  no existing key to back up')

    open(path, 'w', encoding='utf-8').write(new + '\n')
    print('3/4  wrote %s' % v['file'])

    ok2, detail2 = check(open(path, encoding='utf-8').read().strip())
    print('4/4  re-verified from disk: %s (%s)' % ('WORKS' if ok2 else 'FAILS', detail2))
    if not ok2:
        print('     the file did not read back clean — restore from .key-backups/ if needed')
        return 2
    print('\nrotated. If TWOCAPTCHA_KEY / CAPTCHA_KEY / TRACERFY_API_KEY is set in your environment '
          'it OVERRIDES this file — clear it or update it too, or the old key keeps winning.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
