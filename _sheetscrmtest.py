#!/usr/bin/env python
"""_sheetscrmtest — sheets_crm's exit code when the Google Sheets push fails. No network.

Run:  python _sheetscrmtest.py    (exit 0 = safe)

2026-09-29: the team sheet's Apps Script webhook answered 404 and the "DealFlow Sheets CRM" task
still read Last Result 0, so nothing but the log said Carlos's tab had stopped updating. A
configured push that fails now exits PUSH_FAILED_RC; no webhook at all stays 0 (CSV-only is a
choice), and the CSV is written either way.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_crm as S        # noqa: E402

FAILS = []
TMP = tempfile.mkdtemp(prefix='sheetscrm_')


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)[:300]) if not cond else ''))
    if not cond:
        FAILS.append(name)


S.DESK = TMP
S.WEBHOOK_F = os.path.join(TMP, 'sheets_crm_webhook.url')
S._leads = lambda: []
S._load = lambda _p, default: default
S.build_rows = lambda *_a: [['Working', '1 Example St', 'Example Owner']]
S.build_prospects = lambda *_a: []
_real_urlopen = urllib.request.urlopen


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self):
        return b'{"ok":true}'


def run(argv, urlopen=None, url=None):
    if url is None:
        if os.path.exists(S.WEBHOOK_F):
            os.remove(S.WEBHOOK_F)
    else:
        with open(S.WEBHOOK_F, 'w', encoding='utf-8') as f:
            f.write(url)
    csv_path = os.path.join(TMP, 'BSG_CRM.csv')
    if os.path.exists(csv_path):
        os.remove(csv_path)
    urllib.request.urlopen = urlopen or _real_urlopen
    old_argv = sys.argv
    sys.argv = ['sheets_crm.py'] + list(argv)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = S.main()
    finally:
        sys.argv = old_argv
        urllib.request.urlopen = _real_urlopen
    return rc, buf.getvalue(), os.path.exists(csv_path)


def gone(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, None)


def down(req, timeout=None):
    raise urllib.error.URLError('no route')


APPS = 'https://script.google.com/macros/s/EXAMPLE/exec'

rc, out, csv_ok = run([], urlopen=gone, url=APPS)
check('a 404 from the webhook exits PUSH_FAILED_RC', rc == S.PUSH_FAILED_RC and rc != 0, (rc, out))
check('a 404 still writes the CSV', csv_ok, out)
check('a 404 says the deployment URL is gone', 'no longer answers' in out, out)

rc, out, csv_ok = run([], urlopen=down, url=APPS)
check('a network failure exits PUSH_FAILED_RC', rc == S.PUSH_FAILED_RC, (rc, out))
check('a network failure does not claim the URL is gone', 'no longer answers' not in out, out)

rc, out, csv_ok = run([], urlopen=lambda req, timeout=None: _Resp(), url=APPS)
check('a push that lands exits 0', rc == 0 and 'pushed 1 row' in out, (rc, out))

rc, out, csv_ok = run([], urlopen=gone, url=None)
check('no webhook configured is CSV-only and exits 0', rc == 0 and csv_ok, (rc, out))

rc, out, csv_ok = run([], urlopen=gone, url='https://example.invalid/hook')
check('a webhook that is not an Apps Script URL exits PUSH_FAILED_RC', rc == S.PUSH_FAILED_RC, (rc, out))

rc, out, csv_ok = run(['--csv-only'], urlopen=gone, url=APPS)
check('--csv-only never pushes and exits 0', rc == 0 and csv_ok and 'push' not in out, (rc, out))

print()
print('==== %d FAIL(S) ====' % len(FAILS) if FAILS else '==== all sheets CRM exit-code checks passed ====')
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
