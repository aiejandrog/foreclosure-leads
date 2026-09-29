"""Sunbiz challenge page must fail loudly, never read as NOT_FOUND. Synthetic; no network; $0.

2026-09-29: Sunbiz began answering curl with a Cloudflare "Just a moment..." page. _lookup found no
result rows in it and said not_found, entity_check exited 1 ("not verified", the guard working) on
an ACTIVE filing, and llc_officers cached every LLC it looked up as not registered."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import entity_check as EC       # noqa: E402
import llc_officers as LO       # noqa: E402
import sunbiz_entities as SE    # noqa: E402

CHALLENGE = ('<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>'
             '<noscript>Enable JavaScript and cookies to continue</noscript>'
             '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>'
             + ' ' * 6000 + '</body></html>')
MAINTENANCE = '<html><head><title>Division of Corporations</title></head><body>Try again later</body></html>'
SEARCH = ('<table><tr><td><a href="/Inquiry/CorporationSearch/SearchResultDetail?a=1">'
          'BISCAYNE SOLUTIONS GROUP LLC</a></td></tr><tr><td><a href="/Inquiry/CorporationSearch/'
          'SearchResultDetail?a=2">BISCAYNE SOLUTIONS INC.</a></td></tr></table>')
DETAIL = ('<div>Document Number <span>L26000444293</span> Date Filed <span>09/01/2026</span>'
          '<label>Status</label> <span>ACTIVE</span> Annual Reports</div>')


def _proc(out):
    return subprocess.CompletedProcess([], 0, stdout=out, stderr='')


class CurlTests(unittest.TestCase):
    def test_challenge_page_raises_after_retries(self):
        with mock.patch.object(LO.subprocess, 'run', return_value=_proc(CHALLENGE)) as run, \
                mock.patch.object(LO.time, 'sleep'):
            with self.assertRaises(LO.SunbizBlocked) as cm:
                LO._curl(LO.BASE + '/x')
        self.assertEqual(run.call_count, 3)
        self.assertIn('Cloudflare', str(cm.exception))

    def test_empty_body_raises(self):
        with mock.patch.object(LO.subprocess, 'run', return_value=_proc('')), \
                mock.patch.object(LO.time, 'sleep'):
            with self.assertRaises(LO.SunbizBlocked):
                LO._curl(LO.BASE + '/x')

    def test_challenge_that_clears_on_retry_returns_the_page(self):
        seq = [_proc(CHALLENGE), _proc(SEARCH)]
        with mock.patch.object(LO.subprocess, 'run', side_effect=lambda *a, **k: seq.pop(0)), \
                mock.patch.object(LO.time, 'sleep'):
            self.assertEqual(LO._curl(LO.BASE + '/x'), SEARCH)


class LookupTests(unittest.TestCase):
    def test_injected_fetch_returning_a_challenge_raises(self):
        with self.assertRaises(LO.SunbizBlocked):
            LO._lookup('BISCAYNE SOLUTIONS GROUP LLC', fetch=lambda u: CHALLENGE)

    def test_unrecognised_page_without_rows_raises_not_not_found(self):
        with self.assertRaises(LO.SunbizBlocked):
            LO._lookup('BISCAYNE SOLUTIONS GROUP LLC', fetch=lambda u: MAINTENANCE)

    def test_challenge_on_the_detail_page_raises(self):
        fetch = lambda u: CHALLENGE if 'SearchResultDetail' in u else SEARCH
        with self.assertRaises(LO.SunbizBlocked):
            LO._lookup('BISCAYNE SOLUTIONS GROUP LLC', fetch=fetch)

    def test_real_page_still_verifies(self):
        fetch = lambda u: DETAIL if 'SearchResultDetail' in u else SEARCH
        d = LO._lookup('BISCAYNE SOLUTIONS GROUP LLC', fetch=fetch)
        self.assertEqual((d['exact'], d['status'], d['doc']), (True, 'ACTIVE', 'L26000444293'))

    def test_strict_fetch_rejects_a_200_challenge(self):
        with mock.patch.object(SE.subprocess, 'run', return_value=_proc(CHALLENGE)):
            with self.assertRaises(SE.SunbizUnreachable):
                SE.strict_fetch(LO.BASE + '/x')


class EntityCheckTests(unittest.TestCase):
    def _run(self, argv, fetch_out):
        with mock.patch.object(LO.subprocess, 'run', return_value=_proc(fetch_out)), \
                mock.patch.object(LO.time, 'sleep'), \
                mock.patch.object(sys, 'argv', ['entity_check.py'] + argv), \
                redirect_stdout(io.StringIO()) as out:
            rc = EC.main()
        return rc, out.getvalue()

    def test_challenge_exits_2_not_1(self):
        rc, out = self._run(['--name', 'BISCAYNE SOLUTIONS GROUP LLC'], CHALLENGE)
        self.assertEqual(rc, 2)
        self.assertIn('LOOKUP FAILED', out)
        self.assertNotIn('next daily run', out)

    def test_configured_entity_writes_an_error_verdict_not_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            status = os.path.join(d, 'entity_status.json')
            with mock.patch.object(EC, 'STATUS_FILE', status), \
                    mock.patch.object(EC.entity, 'sender', return_value={'llc': 'Biscayne Solutions Group LLC'}):
                rc, _ = self._run([], CHALLENGE)
            v = json.load(open(status, encoding='utf-8'))
        self.assertEqual(rc, 2)
        self.assertFalse(v['verified'])
        self.assertNotEqual(v['status'], 'NOT_FOUND')
        self.assertIn('SunbizBlocked', v['error'])

    def test_available_screening_does_not_call_a_blocked_name_taken(self):
        rc, out = self._run(['--available', 'Foo Group LLC'], CHALLENGE)
        self.assertEqual(rc, 2)
        self.assertNotIn('TAKEN', out)
        self.assertNotIn('AVAILABLE', out)


class OfficerRunTests(unittest.TestCase):
    def test_blocked_run_caches_nothing_and_records_it(self):
        with tempfile.TemporaryDirectory() as d:
            json.dump([{'Case #': '2026-000001-CA-01', 'owners': 'EXAMPLE HOLDINGS LLC'},
                       {'Case #': '2026-000002-CA-01', 'owners': 'OTHER VENTURES LLC'}],
                      open(os.path.join(d, 'leads_final.json'), 'w'))
            with mock.patch.object(LO, 'HERE', d), \
                    mock.patch.object(LO, 'OUT', os.path.join(d, 'llc_officers.json')), \
                    mock.patch.object(LO, 'STATUS', os.path.join(d, 'llc_officers_status.json')), \
                    mock.patch.object(LO.subprocess, 'run', return_value=_proc(CHALLENGE)), \
                    mock.patch.object(LO.time, 'sleep'), \
                    mock.patch.object(sys, 'argv', ['llc_officers.py']), \
                    redirect_stdout(io.StringIO()) as out:
                rc = LO.main()
            cache = json.load(open(os.path.join(d, 'llc_officers.json')))
            st = json.load(open(os.path.join(d, 'llc_officers_status.json')))
        self.assertEqual(rc, 2)
        self.assertEqual(cache, {})                    # no false nf entry that later runs would skip
        self.assertTrue(st['blocked'])
        self.assertIn('SUNBIZ BLOCKED', out.getvalue())


if __name__ == '__main__':
    unittest.main()
