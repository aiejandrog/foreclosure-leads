"""Sunbiz challenge page must fail loudly, never read as NOT_FOUND. Synthetic; no network; $0.

2026-09-29: Sunbiz began answering curl with a Cloudflare "Just a moment..." page. _lookup found no
result rows in it and said not_found, entity_check exited 1 ("not verified", the guard working) on
an ACTIVE filing, and llc_officers cached every LLC it looked up as not registered."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, redirect_stdout
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


def _answers(x):
    """A fetch tier stub: a string (every call), a list (one per call), or a callable(url)."""
    if callable(x):
        return mock.MagicMock(side_effect=x)
    if isinstance(x, list):
        seq = list(x)
        return mock.MagicMock(side_effect=lambda *a, **k: seq.pop(0))
    return mock.MagicMock(return_value=x)


@contextmanager
def tiers(cffi=None, camoufox=None, native=None):
    """Stub every network tier of LO._curl. Nothing here touches the network or launches a browser:
    camoufox=None means "not installed", and a laptop that has Camoufox must not open it in a test."""
    with ExitStack() as st:
        st.enter_context(mock.patch.object(LO, 'FETCH_LOG', []))
        st.enter_context(mock.patch.object(LO.time, 'sleep'))
        c = st.enter_context(mock.patch.object(LO, '_cffi_get', _answers(cffi)))
        f = st.enter_context(mock.patch.object(LO, 'camoufox_fetch', _answers(camoufox)))
        n = st.enter_context(mock.patch.object(LO, '_native_curl_get', _answers(native if native is not None else '')))
        yield c, f, n


class CurlTests(unittest.TestCase):
    def test_challenge_page_raises_after_retries(self):
        with tiers(cffi=CHALLENGE) as (c, f, n):
            with self.assertRaises(LO.SunbizBlocked) as cm:
                LO._curl(LO.BASE + '/x')
        self.assertEqual(c.call_count, 3)
        self.assertEqual(f.call_count, 1)               # camoufox asked once, reported not installed
        self.assertIn('Cloudflare', str(cm.exception))
        self.assertIn('curl_cffi', str(cm.exception))

    def test_empty_body_raises(self):
        with tiers(cffi=''):
            with self.assertRaises(LO.SunbizBlocked):
                LO._curl(LO.BASE + '/x')

    def test_challenge_that_clears_on_retry_returns_the_page(self):
        with tiers(cffi=[CHALLENGE, SEARCH]) as (c, f, n):
            self.assertEqual(LO._curl(LO.BASE + '/x'), SEARCH)
        f.assert_not_called()

    def test_camoufox_fallback_returns_the_page(self):
        with tiers(cffi=CHALLENGE, camoufox=SEARCH) as (c, f, n):
            self.assertEqual(LO._curl(LO.BASE + '/x'), SEARCH)
            self.assertEqual(c.call_count, 3)
            # once the browser got through, the next page goes to it first: no wasted retries
            self.assertEqual(LO._curl(LO.BASE + '/y'), SEARCH)
            self.assertEqual(c.call_count, 3)
            self.assertEqual(f.call_count, 2)

    def test_camoufox_also_challenged_raises(self):
        with tiers(cffi=CHALLENGE, camoufox=CHALLENGE):
            with self.assertRaises(LO.SunbizBlocked) as cm:
                LO._curl(LO.BASE + '/x')
        self.assertIn('and camoufox', str(cm.exception))

    def test_camoufox_first_falls_back_to_cffi_when_the_browser_stops_answering(self):
        with tiers(cffi=SEARCH, camoufox=[SEARCH, '']) as (c, f, n):
            LO.FETCH_LOG.append(('camoufox', 'ok'))
            self.assertEqual(LO._curl(LO.BASE + '/x'), SEARCH)   # browser answers
            self.assertEqual(LO._curl(LO.BASE + '/y'), SEARCH)   # browser empty -> curl_cffi
            self.assertEqual(c.call_count, 1)

    def test_native_curl_used_only_when_curl_cffi_is_missing(self):
        with tiers(cffi=None, native=SEARCH) as (c, f, n):
            # _cffi_get returning None is "curl_cffi not installed"
            self.assertEqual(LO._curl(LO.BASE + '/x'), SEARCH)
        self.assertEqual(n.call_count, 1)
        with tiers(cffi=SEARCH, native=SEARCH) as (c, f, n):
            LO._curl(LO.BASE + '/x')
        n.assert_not_called()

    def test_cffi_get_impersonates_without_a_hand_set_user_agent(self):
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            self.skipTest('curl_cffi not installed')
        resp = mock.MagicMock(text=SEARCH)
        with mock.patch('curl_cffi.requests.get', return_value=resp) as g:
            self.assertEqual(LO._cffi_get(LO.BASE + '/x'), SEARCH)
        kw = g.call_args.kwargs
        self.assertEqual(kw['impersonate'], LO.IMPERSONATE)
        self.assertNotIn('headers', kw)

    def test_cffi_get_network_error_is_a_miss_not_a_crash(self):
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            self.skipTest('curl_cffi not installed')
        with mock.patch('curl_cffi.requests.get', side_effect=OSError('reset')):
            self.assertEqual(LO._cffi_get(LO.BASE + '/x'), '')

    def test_camoufox_missing_is_none_not_an_error(self):
        with mock.patch.dict(LO._CAMOUFOX, {'cm': None, 'browser': None, 'down': ''}), \
                mock.patch.dict(sys.modules, {'camoufox': None, 'camoufox.sync_api': None}):
            self.assertIsNone(LO.camoufox_fetch(LO.BASE + '/x'))
            self.assertIn('not installed', LO._CAMOUFOX['down'])


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
        with tiers(cffi=CHALLENGE):
            with self.assertRaises(SE.SunbizUnreachable):
                SE.strict_fetch(LO.BASE + '/x')

    def test_strict_fetch_returns_a_real_page(self):
        with tiers(cffi=SEARCH):
            self.assertEqual(SE.strict_fetch(LO.BASE + '/x'), SEARCH)


class EntityCheckTests(unittest.TestCase):
    def _run(self, argv, fetch_out):
        with tiers(cffi=fetch_out), \
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
                    tiers(cffi=CHALLENGE), \
                    mock.patch.object(sys, 'argv', ['llc_officers.py']), \
                    redirect_stdout(io.StringIO()) as out:
                rc = LO.main()
            cache = json.load(open(os.path.join(d, 'llc_officers.json')))
            st = json.load(open(os.path.join(d, 'llc_officers_status.json')))
        self.assertEqual(rc, 2)
        self.assertEqual(cache, {})                    # no false nf entry that later runs would skip
        self.assertTrue(st['blocked'])
        self.assertEqual(st['tiers'], {'curl_cffi_blocked': 1})   # counts only, no URL or name
        self.assertIn('SUNBIZ BLOCKED', out.getvalue())


if __name__ == '__main__':
    unittest.main()
