"""LP sweep completeness record (2026-10-22): synthetic data only, no network, no captcha.

A county is 'ok' only when it read its whole window, 'partial' when it ran over less, 'failed' when
nothing came back. Partial rows still merge; the status and the coverage record say what is missing.
"""
import datetime
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import coverage_report as CR
import lis_pendens as LP
import run_report as RR
from fl_lp import palmbeach as PB


def _env(rows, total):
    return {'draw': 1, 'recordsTotal': total,
            'data': [{'id': i} for i in range(rows)]}


def pb_sweep(windows, fail=()):
    """windows: {(from, to): (rows, total)} keyed on the m/d/Y strings the sweep asks for.
    Any window not listed answers with the default; any in `fail` is rejected every time."""
    state = {}

    def search(payload, token=''):
        crit = dict(payload[1])
        state['w'] = (crit['beginDate'], crit['endDate'])
        return None if state['w'] in fail else 'ok'

    def get(length, start):
        rows, total = windows.get(state['w'], windows.get('default', (0, 0)))
        take = max(0, min(length, rows - start))
        return _env(take, total)

    fake = SimpleNamespace(start_session=lambda: True, solve_token_2captcha=lambda: 'tok',
                           search=search, get_search_results=get,
                           gsr_rows_to_docs=lambda rows: [])
    with mock.patch.object(PB, 'P', fake), mock.patch.object(PB.time, 'sleep', lambda _: None):
        return PB.sweep(days=30)


class PalmBeach(unittest.TestCase):
    def test_whole_window_read_is_ok(self):
        rows = pb_sweep({'default': (150, 150)})
        self.assertEqual(rows, [])                      # the doc parser is stubbed out
        self.assertEqual(PB.LAST['status'], 'ok')
        self.assertEqual(PB.LAST['reported_total'], 150)
        self.assertEqual(PB.LAST['retrieved'], 150)

    def test_window_still_over_the_cap_at_full_depth_is_partial(self):
        pb_sweep({'default': (200, 900)})
        self.assertEqual(PB.LAST['status'], 'partial')
        self.assertTrue(any('truncated' in f for f in PB.LAST['failures']))

    def test_half_that_never_ran_makes_the_window_partial(self):
        d_from, d_to = PB._win(30)
        lo, hi = PB._split_window(d_from, d_to)
        rows = pb_sweep({'default': (150, 150), (d_from, d_to): (200, 300)}, fail={hi})
        self.assertIsNotNone(rows)
        self.assertEqual(PB.LAST['status'], 'partial')
        self.assertTrue(any('did not run' in f for f in PB.LAST['failures']))

    def test_no_session_is_failed(self):
        fake = SimpleNamespace(start_session=lambda: False)
        with mock.patch.object(PB, 'P', fake), mock.patch.object(PB.time, 'sleep', lambda _: None):
            self.assertIsNone(PB.sweep(days=30))
        self.assertEqual(PB.LAST['status'], 'failed')


class StatusFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'lp_sweep_status.json')
        self.p = mock.patch.object(LP, 'STATUS_FILE', self.path)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def write(self, status, info, now):
        a = SimpleNamespace(days=30, county='all')
        failed = LP.sweep_verdict(status)[1]
        return LP.write_status(a, status, info, 0, failed, LP.partial_counties(status), now=now)

    def test_partial_ran_but_is_named(self):
        st = {'MIAMI-DADE': 'ok', 'BROWARD': 'partial', 'PALM BEACH': 'failed'}
        self.assertEqual(LP.sweep_verdict(st), (['BROWARD', 'MIAMI-DADE'], ['PALM BEACH']))
        self.assertEqual(LP.partial_counties(st), ['BROWARD'])

    def test_last_complete_carries_through_partial_nights(self):
        self.write({'BROWARD': 'ok'}, {'BROWARD': {'reported_total': 10, 'retrieved': 10}}, '2026-10-20T05:30:00')
        cov = self.write({'BROWARD': 'partial'},
                         {'BROWARD': {'reported_total': 900, 'retrieved': 400,
                                      'failures': ['grid parse failed on page 2']}}, '2026-10-21T05:30:00')
        self.assertEqual(cov['BROWARD']['last_complete'], '2026-10-20T05:30:00')
        self.assertEqual(cov['BROWARD']['status'], 'partial')
        with open(self.path, encoding='utf-8') as f:
            d = json.load(f)
        self.assertEqual(d['partial'], ['BROWARD'])
        self.assertEqual(d['coverage']['BROWARD']['retrieved'], 400)

    def test_a_county_not_swept_keeps_its_record(self):
        self.write({'PALM BEACH': 'ok'}, {}, '2026-10-20T05:30:00')
        cov = self.write({'BROWARD': 'ok'}, {}, '2026-10-21T05:30:00')
        self.assertEqual(cov['PALM BEACH']['last_complete'], '2026-10-20T05:30:00')

    def test_never_complete_says_so(self):
        cov = self.write({'MIAMI-DADE': 'partial'}, {'MIAMI-DADE': {'capped': ['US BANK']}}, '2026-10-21T05:30:00')
        self.assertIsNone(cov['MIAMI-DADE']['last_complete'])
        self.assertEqual(cov['MIAMI-DADE']['capped'], ['US BANK'])

    def test_a_damaged_prior_record_does_not_stop_the_sweep(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'coverage': {'BROWARD': 'not a dict'}}, f)
        cov = self.write({'BROWARD': 'ok'}, {}, '2026-10-21T05:30:00')
        self.assertEqual(cov['BROWARD']['status'], 'ok')

    def test_run_report_reads_partial_as_not_healthy(self):
        self.write({'BROWARD': 'partial', 'MIAMI-DADE': 'ok'}, {}, '2026-10-21T05:30:00')
        with mock.patch.object(RR, 'HERE', self.tmp):
            ran, failed = RR._lp_sweep()
        self.assertEqual(failed, ['BROWARD (partial)'])
        state, why = RR.verdict_of(1000, 'HEALTHY', ran, failed)
        self.assertEqual(state, 'DEGRADED')


class MiamiCap(unittest.TestCase):
    def test_a_search_at_the_record_cap_is_counted(self):
        recs = [{'doC_TYPE': 'MORTGAGE'}] * LP.OR_RESULT_CAP
        R = SimpleNamespace(OR_BASE='https://example.invalid/', TS_SITE_KEY='k',
                            S=SimpleNamespace(post=lambda *a, **k: SimpleNamespace(json=lambda: {'qs': 'q'})),
                            records_by_qs=lambda qs: recs)
        PR = SimpleNamespace(guarded=lambda f, name: (lambda *a, **k: 'tok'),
                             allow=lambda *a: (True, ''), SOLVE_USD=0.0033)
        CS = SimpleNamespace(solve_turnstile=lambda *a: 'tok')
        with mock.patch.dict('sys.modules', {'records_liens': R, 'paid_reads': PR, 'captcha_solver': CS}), \
                mock.patch.object(LP, 'PLAINTIFFS', ['BIG LENDER', 'SMALL LENDER']):
            LP.SWEEP_BLOCKED[:] = []
            LP.lp_sweep(days=30)
        self.assertEqual(LP.SWEEP_CAPPED, ['BIG LENDER', 'SMALL LENDER'])
        self.assertEqual(LP.MD_LAST['searched'], 2)
        self.assertEqual(LP.MD_LAST['retrieved'], 2 * LP.OR_RESULT_CAP)


class Report(unittest.TestCase):
    def test_monthly_counts_by_county_and_recording_month(self):
        rows = [{'date': '7/3/2026'}, {'date': '7/30/2026', 'county': 'BROWARD'},
                {'date': '2026-07-09'}, {'date': 'junk'}]
        m = CR.monthly(rows)
        self.assertEqual(m['MIAMI-DADE']['2026-07'], 2)
        self.assertEqual(m['BROWARD']['2026-07'], 1)

    def test_bench_parses_and_compares(self):
        b = CR.parse_bench(['broward:2026-07=400'])
        self.assertEqual(b, {'BROWARD': {'2026-07': 400}})
        rows = CR.bench_rows({'BROWARD': {'2026-07': 100}}, b)
        self.assertEqual(rows, [('BROWARD', '2026-07', 100, 400, 0.25)])
        with self.assertRaises(ValueError):
            CR.parse_bench(['BROWARD:July=400'])

    def test_stale_last_complete_is_flagged(self):
        now = datetime.datetime(2026, 10, 22, 6, 0)
        lines = CR.sweep_lines({'coverage': {'BROWARD': {
            'status': 'partial', 'window': ['2026-09-22', '2026-10-22'], 'reported_total': 900,
            'retrieved': 400, 'last_complete': '2026-10-15T05:30:00'}}}, now)
        self.assertTrue(any('STALE' in x for x in lines))
        lines = CR.sweep_lines({'coverage': {'MIAMI-DADE': {'status': 'partial'}}}, now)
        self.assertTrue(any('NONE ON RECORD' in x for x in lines))

    def test_report_runs_read_only_on_this_checkout(self):
        with mock.patch('builtins.print'):
            self.assertEqual(CR.main([]), 0)
            self.assertEqual(CR.main(['--json']), 0)


if __name__ == '__main__':
    unittest.main()
