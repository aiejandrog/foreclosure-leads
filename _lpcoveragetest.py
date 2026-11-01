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
    def test_repeated_pages_cannot_claim_complete(self):
        def repeated(length, start):
            return {'recordsTotal': 4, 'data': [{'id': 1}, {'id': 2}]}
        rows, complete = PB._page_all(repeated, page=2, limit=4)
        self.assertEqual([r['id'] for r in rows], [1, 2])
        self.assertFalse(complete)

    def test_declared_empty_window_is_verified_empty(self):
        rows = pb_sweep({'default': (0, 0)})
        self.assertEqual(rows, [])
        self.assertEqual(PB.LAST['status'], 'ok')
        self.assertEqual(PB.LAST['retrieved'], 0)

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


class FakeClerk:
    """A Miami-Dade OR search over synthetic lenders: `per_day[name]` records a day, one in ten a
    lis pendens, returned oldest first and cut at the 500-record cap like the real search."""

    def __init__(self, per_day, no_qs=(), broken_reads=False, invalid=()):
        import urllib.parse as U
        self.U, self.per_day, self.no_qs, self.posts = U, per_day, set(no_qs), []
        self.broken_reads = broken_reads
        self.invalid = set(invalid)

    def post(self, url, **k):
        q = self.U.parse_qs(self.U.urlsplit(url).query)
        name, lo, hi = q['partyName'][0], q['dateRangeFrom'][0], q['dateRangeTo'][0]
        self.posts.append((name, lo, hi))
        self.doctypes = getattr(self, 'doctypes', []) + [q.get('documentType', [''])[0]]
        body = {'message': 'No records'} if name in self.no_qs else {'qs': '|'.join((name, lo, hi))}
        if name in self.invalid:
            body = {'isValidSearch': False, 'qs': None}
        return SimpleNamespace(json=lambda: body, status_code=200)

    def records_by_qs(self, qs):
        name, lo, hi = qs.split('|')
        if self.broken_reads and (lo, hi) != LP._win(30):
            return None                        # the GET for a narrower window failed
        d, end, out = datetime.date.fromisoformat(lo), datetime.date.fromisoformat(hi), []
        while d <= end:
            for n in range(self.per_day.get(name, 0)):
                lp = n % 10 == 0
                out.append({'doC_TYPE': 'LIS PENDENS - LIS' if lp else 'MORTGAGE',
                            'casE_NUM': '%s-%s-%d' % (name[:3], d.isoformat(), n),
                            'firsT_PARTY': name + ' BANK', 'seconD_PARTY': 'SYNTHETIC OWNER %d' % n,
                            'reC_DATE': d.strftime('%m/%d/%Y')})
            d += datetime.timedelta(days=1)
        return out[:LP.OR_RESULT_CAP]


class MiamiCap(unittest.TestCase):
    def run_sweep(self, per_day, budget=10 ** 6, no_qs=(), broken_reads=False, invalid=()):
        clerk = FakeClerk(per_day, no_qs, broken_reads, invalid)
        R = SimpleNamespace(OR_BASE='https://example.invalid/', TS_SITE_KEY='k',
                            S=SimpleNamespace(post=clerk.post), records_by_qs=clerk.records_by_qs)
        left = {'n': budget}

        def allow(*a):
            return (left['n'] > 0, '')

        def guarded(f, name):
            def solve(*a, **k):
                if left['n'] <= 0:
                    return None
                left['n'] -= 1
                return 'tok'
            return solve
        PR = SimpleNamespace(guarded=guarded, allow=allow, SOLVE_USD=0.0033)
        CS = SimpleNamespace(solve_turnstile=lambda *a: 'tok')
        with mock.patch.dict('sys.modules', {'records_liens': R, 'paid_reads': PR, 'captcha_solver': CS}), \
                mock.patch.object(LP, 'PLAINTIFFS', sorted(per_day)), mock.patch.object(LP.time, 'sleep', lambda _: None):
            LP.SWEEP_BLOCKED[:] = []
            rows = LP.lp_sweep(days=30)
        return rows, clerk

    def test_a_capped_lender_is_split_until_every_window_is_whole(self):
        rows, clerk = self.run_sweep({'BIG': 40, 'SMALL': 2})
        self.assertEqual(LP.SWEEP_CAPPED, [])
        self.assertEqual(LP.SWEEP_BLOCKED, [])
        big = [r for r in rows if r['case'].startswith('BIG')]
        self.assertEqual(len(big), 31 * 4, 'every lis pendens in the 31 days, newest included')
        self.assertGreater(LP.MD_LAST['split_searches'], 0)
        self.assertEqual(LP.MD_LAST['retrieved'], 31 * 40 + 31 * 2, 'overlapping windows counted once')
        self.assertEqual(sum(1 for p in clerk.posts if p[0] == 'SMALL'), 1, 'an uncapped lender is searched once')

    def test_the_search_asks_for_lis_pendens_by_default(self):
        _, clerk = self.run_sweep({'SMALL': 2})
        self.assertEqual(clerk.doctypes, ['LIS PENDENS - LIS'])
        with mock.patch.object(LP, 'MD_LP_DOCTYPE', ''):
            _, clerk = self.run_sweep({'SMALL': 2})
        self.assertEqual(clerk.doctypes, [''])

    def test_still_capped_at_full_depth_is_reported(self):
        self.run_sweep({'HUGE': 400})
        self.assertEqual(LP.SWEEP_CAPPED, ['HUGE'])

    def test_a_split_stopped_by_the_paid_cap_is_partial(self):
        rows, _ = self.run_sweep({'BIG': 40}, budget=2)
        self.assertEqual(LP.SWEEP_CAPPED, ['BIG'])
        self.assertTrue(rows, 'the rows that were read are kept')

    def test_a_failed_results_read_is_not_an_empty_window(self):
        rows, _ = self.run_sweep({'BIG': 40}, broken_reads=True)
        self.assertEqual(LP.SWEEP_CAPPED, ['BIG'], 'halves that were never read cannot be complete')
        self.assertTrue(rows, 'the capped page is still kept')

    def test_the_per_run_split_ceiling_stops_and_reports(self):
        with mock.patch.object(LP, 'MD_MAX_SPLIT_SEARCHES', 1):
            self.run_sweep({'BIG': 40})
        self.assertEqual(LP.SWEEP_CAPPED, ['BIG'])
        self.assertLessEqual(LP.MD_LAST['split_searches'], 1)

    def test_a_no_result_answer_is_recorded(self):
        self.run_sweep({'GONE': 0}, no_qs={'GONE'})
        self.assertEqual(LP.SWEEP_BLOCKED, ['GONE'])
        self.assertIn('No records', LP.MD_LAST['no_qs']['GONE'])

    def test_each_try_for_a_blocked_lender_is_described_without_a_token(self):
        self.run_sweep({'GONE': 0}, no_qs={'GONE'})
        tries = LP.MD_LAST['attempts']['GONE']
        self.assertEqual(len(tries), 3)
        self.assertEqual(tries[0]['token'], True)
        self.assertEqual(tries[0]['http'], 200)
        self.assertEqual(tries[0]['qs'], False)
        self.assertIn('No records', tries[0]['answer'])
        import json as _json
        dumped = _json.dumps(LP.coverage_entry('partial', {'attempts': LP.MD_LAST['attempts']}, None))
        self.assertNotIn('"tok"', dumped, 'the captcha token never reaches the status record')
        self.assertIn('attempts', dumped)

    def test_a_failed_results_read_is_described(self):
        self.run_sweep({'BIG': 40}, broken_reads=True)
        reads = [t['read'] for tries in LP.MD_LAST['attempts'].values() for t in tries]
        self.assertIn(False, reads)

    def test_an_exception_is_recorded_by_type_only(self):
        def boom(*a, **k):
            raise RuntimeError('secret-token SOMEONE PRIVATE')
        R = SimpleNamespace(OR_BASE='x', TS_SITE_KEY='k', S=SimpleNamespace(post=boom))
        _, t = LP._or_attempt(R, lambda *a: 'tok', 'https://example.invalid/')
        self.assertEqual(t['answer'], 'request failed: RuntimeError')
        R.S = SimpleNamespace(post=lambda *a, **k: SimpleNamespace(status_code=503, json=lambda: 1 / 0))
        _, t = LP._or_attempt(R, lambda *a: 'tok', 'https://example.invalid/')
        self.assertEqual((t['http'], t['answer']), (503, 'answer was not JSON'))
        R.S = SimpleNamespace(post=lambda *a, **k: SimpleNamespace(
            status_code=200, json=lambda: {'isValidSearch': False, 'qs': None, 'rows': ['x']}))
        _, t = LP._or_attempt(R, lambda *a: 'tok', 'https://example.invalid/')
        self.assertIn("'isValidSearch': False", t['answer'], 'a flag is shown as its value')
        self.assertIn("'rows': 'list'", t['answer'], 'anything else stays a type name')

    def test_a_refused_solve_reads_as_no_token(self):
        self.run_sweep({'SMALL': 2}, budget=0)
        self.assertEqual(LP.SWEEP_BLOCKED, ['SMALL'])
        self.assertNotIn('SMALL', LP.MD_LAST['attempts'], 'stopped before trying: the report says never tried')
        R = SimpleNamespace(OR_BASE='x', TS_SITE_KEY='k', S=None)
        recs, t = LP._or_attempt(R, lambda *a: None, 'https://example.invalid/')
        self.assertIsNone(recs)
        self.assertEqual((t['token'], t['http'], t['qs']), (False, None, None))

    def test_report_prints_the_tries(self):
        now = datetime.datetime(2026, 10, 22, 8, 0)
        lines = CR.sweep_lines({'coverage': {'MIAMI-DADE': {'status': 'partial', 'attempts': {
            'GONE': [{'token': True, 'http': 200, 'qs': False, 'read': None, 'answer': "{'message': 'No records'}"}],
            'LATE': []}}}}, now)
        text = '\n'.join(lines)
        self.assertIn('GONE: token yes http 200 qs no read -', text)
        self.assertIn('LATE: never tried', text)

    def test_a_lender_the_clerk_calls_not_valid_on_every_try_is_no_match_not_blocked(self):
        rows, clerk = self.run_sweep({'QUIET': 0, 'SMALL': 2}, invalid={'QUIET'})
        self.assertEqual(LP.SWEEP_BLOCKED, [])
        self.assertEqual(LP.MD_LAST['no_match'], ['QUIET'])
        self.assertEqual(sum(1 for p in clerk.posts if p[0] == 'QUIET'), 3, 'all three tries were made')
        self.assertTrue(rows or LP.MD_LAST['retrieved'] >= 0)
        cov = LP.coverage_entry('ok', {'no_match': LP.MD_LAST['no_match']}, None)
        self.assertEqual(cov['no_match'], ['QUIET'])

    def test_one_not_valid_answer_among_good_tries_is_still_blocked(self):
        # a bad solve gives the same False; only every try agreeing counts as no match
        clerk = FakeClerk({'FLAKY': 0}, invalid={'FLAKY'})
        answers = iter([{'isValidSearch': False, 'qs': None}, {'qs': None}, {'isValidSearch': False, 'qs': None}])
        clerk_post = lambda url, **k: SimpleNamespace(json=lambda: next(answers), status_code=200)
        R = SimpleNamespace(OR_BASE='https://example.invalid/', TS_SITE_KEY='k',
                            S=SimpleNamespace(post=clerk_post), records_by_qs=clerk.records_by_qs)
        PR = SimpleNamespace(guarded=lambda f, n: (lambda *a, **k: 'tok'), allow=lambda *a: (True, ''), SOLVE_USD=0.0033)
        CS = SimpleNamespace(solve_turnstile=lambda *a: 'tok')
        with mock.patch.dict('sys.modules', {'records_liens': R, 'paid_reads': PR, 'captcha_solver': CS}), \
                mock.patch.object(LP, 'PLAINTIFFS', ['FLAKY']), mock.patch.object(LP.time, 'sleep', lambda _: None):
            LP.SWEEP_BLOCKED[:] = []
            LP.lp_sweep(days=30)
        self.assertEqual(LP.SWEEP_BLOCKED, ['FLAKY'])
        self.assertEqual(LP.MD_LAST['no_match'], [])

    def test_split_halves_cover_the_window_exactly(self):
        lo, hi = LP._split_iso('2026-09-22', '2026-10-22')
        self.assertEqual(lo[0], '2026-09-22')
        self.assertEqual(hi[1], '2026-10-22')
        self.assertEqual(datetime.date.fromisoformat(hi[0]) - datetime.date.fromisoformat(lo[1]),
                         datetime.timedelta(days=1))
        self.assertIsNone(LP._split_iso('2026-10-22', '2026-10-22'))


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

    def test_audit_all_lp_counts_missing_by_kind_and_writes_nothing(self):
        def rec(case, p1, p2='JANE DOE', typ='LIS PENDENS - LIS'):
            return {'casE_NUM': case, 'firsT_PARTY': p1, 'seconD_PARTY': p2, 'doC_TYPE': typ,
                    'reC_DATE': '10/1/2026 12:00:00 AM', 'reC_BOOKPAGE': 'B' + case}
        day = [rec('2026-1', 'WELLS FARGO BANK NA'), rec('2026-1', 'WELLS FARGO BANK NA'),   # per-party dup
               rec('2026-2', 'LAKEVIEW LOAN SERVICING'), rec('2026-3', 'SUNSET HOMEOWNERS ASSOCIATION'),
               rec('2026-4', 'WELLS FARGO BANK NA', typ='CANCEL LIS PENDENS')]
        calls = []
        def fake(lo, hi):
            calls.append((lo, hi))
            return day if len(calls) == 1 else (None if len(calls) == 2 else [])
        with mock.patch('builtins.print'):
            out = LP.audit_all_lp(days=3, held_rows=[{'case': '2026-1'}], search_fn=fake)
        self.assertEqual(len(calls), 3)
        self.assertEqual(out['unique'], 3)
        self.assertEqual(out['held'], 1)
        self.assertEqual(out['missing'], 2)
        self.assertEqual(out['missing_by_kind'], {'BANK-1st': 1, 'HOA/JUNIOR': 1})
        self.assertEqual(len(out['failed']), 1)
        self.assertEqual(out['capped'], [])

    def test_audit_all_lp_flags_a_capped_day(self):
        recs = [{'casE_NUM': '2026-%d' % i, 'firsT_PARTY': 'A BANK', 'seconD_PARTY': 'X', 'doC_TYPE': 'LIS PENDENS - LIS'}
                for i in range(LP.OR_RESULT_CAP)]
        with mock.patch('builtins.print'):
            out = LP.audit_all_lp(days=1, held_rows=[], search_fn=lambda lo, hi: recs)
        self.assertEqual(len(out['capped']), 1)

    def test_whole_miami_sweep_is_off_by_default_and_cuts_to_kinds(self):
        self.assertFalse(LP.MD_WHOLE, 'a recurring paid read stays off until Alex enables it')
        def rec(case, p1, p2='JANE DOE'):
            return {'casE_NUM': case, 'firsT_PARTY': p1, 'seconD_PARTY': p2, 'doC_TYPE': 'LIS PENDENS - LIS'}
        day = [rec('2026-1', 'WELLS FARGO BANK NA'), rec('2026-1', 'WELLS FARGO BANK NA'),
               rec('2026-2', 'SUNSET HOMEOWNERS ASSOCIATION'), rec('2026-3', 'WELLS FARGO BANK NA', 'CHASE BANK')]
        calls = []
        def fake(lo, hi):
            calls.append((lo, hi))
            return None if len(calls) == 2 else day
        with mock.patch('builtins.print'):
            rows, info = LP.whole_miami_sweep(days=3, kinds='BANK-1st', search_fn=fake)
            allrows, _ = LP.whole_miami_sweep(days=1, kinds='all', search_fn=lambda lo, hi: day)
        self.assertEqual([r['case'] for r in rows], ['2026-1'])      # HOA cut, both-lender row dropped, dup once
        self.assertEqual(info['windows'], 3)
        self.assertEqual(len(info['failed']), 1)
        self.assertTrue(all(lo == hi for lo, hi in calls))
        self.assertEqual(sorted(r['case'] for r in allrows), ['2026-1', '2026-2'])

    def test_report_runs_read_only_on_this_checkout(self):
        with mock.patch('builtins.print'):
            self.assertEqual(CR.main([]), 0)
            self.assertEqual(CR.main(['--json']), 0)


if __name__ == '__main__':
    unittest.main()
