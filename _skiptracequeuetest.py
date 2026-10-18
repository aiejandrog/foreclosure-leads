"""Nightly skiptrace spends credits only on leads that can still be contacted.

Synthetic case numbers only. No names, street addresses, phone numbers, or emails.
The provider is never called by --report, and the trace checks below use an empty
200 so the fixture itself carries no contact data.

A typical pre-cap queue in this file is 17 leads:

  bankruptcy stay          3   §362 flag, sale_bk_active, sale-history cache
  outreach hold            3   broward_or_feed outreach/source, Palm Beach / #206 hold
  closed / sold / passed   6   dismissed, disposed, lpClosed, sale passed, sibling sold
  do not contact           1   opt-out / Call Mode DNC key
  kept                     4   lifted stay, CLOSED with a future sale, two open sales

dropped 13, and the 40/run cap then sees only the 4 that can be contacted, soonest
sale first.
"""
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import skiptrace as ST
import bd_budget


def lead(case, **kw):
    row = {'Case #': case, 'county': kw.pop('county', 'MIAMI-DADE')}
    row.update(kw)
    return row


def typical_leads():
    """The queue a nightly --all would be about to cap, before exclusions."""
    return [
        lead('2099-000301-CA-01', saleBkAct=True, days_to_auction=5),
        lead('2099-000302-CA-01', sale_bk_active=True, days_to_auction=6),
        lead('2099-000303-CA-01', saleBkAct=True, saleLift='2026-01-02', days_to_auction=7),
        lead('CACE-26-000304', outreach='held', county='BROWARD', days_to_auction=8),
        lead('CACE-26-000305', source='broward-or-feed', county='BROWARD', days_to_auction=9),
        lead('502026CA000306XXXAMB', county='PALM BEACH', days_to_auction=4),
        lead('2099-000307-CA-01', lpDismissed=True, days_to_auction=3),
        lead('2099-000308-CA-01', cstatus='DISPOSED', days_to_auction=11),
        lead('2099-000309-CA-01', cstatus='CLOSED', days_to_auction=12),
        lead('2099-000310-CA-01', lpClosed=True, days=9999),
        lead('2099-000311-CA-01', days_to_auction=-2),
        lead('2099-000312-CA-01', sibclaimed=True, days_to_auction=13),
        lead('2099-000313-CA-01', days_to_auction=1),
        lead('2099-000314-CA-01', days_to_auction=2),
        lead('2099-000315-CA-01', days_to_auction=40),
        lead('2099-000316-CA-01', case_status='DISMISSED', days_to_auction=14),
        lead('2099-000317-CA-01', days_to_auction=15),
    ]


def typical_index():
    """Cache / ledger hits the row itself does not carry.

    306 is a Palm Beach outreach hold (#206 unverifiable or send_hold).
    313 is on the opt-out ledger or a Call Mode DNC mark.
    317 is an active §362 entry in sale_history_cache (no flag on the row yet).
    """
    idx = ST._empty_index()
    idx['outreach_cases'].add('502026CA000306XXXAMB')
    idx['dnc_cases'].add('2099-000313-ca-01')
    idx['stay_cases'].add('2099-000317-CA-01')
    return idx


TYPICAL_LINE = ('skiptrace exclude: bankruptcy_stay=3 outreach_held=3 closed=6 '
                'do_not_contact=1 kept=4 dropped=13')


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class QueueTest(unittest.TestCase):
    def setUp(self):
        self._env = {}
        for name in ('SKIPTRACE_RUN_CAP', 'SKIPTRACE_DAY_CAP', 'SKIPTRACE_DEDUPE_DAYS'):
            self._env[name] = os.environ.get(name)
            os.environ.pop(name, None)
        self._saved = (
            ST._EXCLUSION_OVERRIDE, ST.load_all_leads, ST.select, ST.load_key,
            ST.RESULTS, bd_budget.LEDGER, ST.requests.Session, ST.time.sleep,
            ST.SKIPTRACE_RUN_CAP, ST.SKIPTRACE_DAY_CAP,
        )

    def tearDown(self):
        (ST._EXCLUSION_OVERRIDE, ST.load_all_leads, ST.select, ST.load_key,
         ST.RESULTS, bd_budget.LEDGER, ST.requests.Session, ST.time.sleep,
         ST.SKIPTRACE_RUN_CAP, ST.SKIPTRACE_DAY_CAP) = self._saved
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_typical_queue_counts(self):
        counts = ST.exclusion_summary(typical_leads(), typical_index())
        self.assertEqual(ST.exclusion_line(counts), TYPICAL_LINE)
        self.assertEqual(counts['queued'], 17)
        self.assertEqual(counts['dropped'] + counts['kept'], 17)
        by_case = {ST._case(r): ST.exclusion_reasons(r, typical_index()) for r in typical_leads()}
        self.assertEqual(by_case['2099-000303-CA-01'], [])          # stay lifted
        self.assertEqual(by_case['2099-000309-CA-01'], [])          # CLOSED, sale still ahead
        self.assertEqual(by_case['2099-000314-CA-01'], [])
        self.assertEqual(by_case['2099-000301-CA-01'], ['bankruptcy_stay'])
        self.assertEqual(by_case['2099-000302-CA-01'], ['bankruptcy_stay'])
        self.assertEqual(by_case['2099-000317-CA-01'], ['bankruptcy_stay'])
        self.assertEqual(by_case['CACE-26-000304'], ['outreach_held'])
        self.assertEqual(by_case['CACE-26-000305'], ['outreach_held'])
        self.assertEqual(by_case['502026CA000306XXXAMB'], ['outreach_held'])
        self.assertEqual(by_case['2099-000307-CA-01'], ['closed'])
        self.assertEqual(by_case['2099-000308-CA-01'], ['closed'])
        self.assertEqual(by_case['2099-000310-CA-01'], ['closed'])
        self.assertEqual(by_case['2099-000311-CA-01'], ['closed'])
        self.assertEqual(by_case['2099-000312-CA-01'], ['closed'])
        self.assertEqual(by_case['2099-000316-CA-01'], ['closed'])
        self.assertEqual(by_case['2099-000313-CA-01'], ['do_not_contact'])

    def test_one_lead_can_match_two_groups_and_drops_once(self):
        row = lead('2099-000401-CA-01', saleBkAct=True, days_to_auction=2)
        idx = ST._empty_index()
        idx['dnc_cases'].add('2099-000401-ca-01')
        counts = ST.exclusion_summary([row], idx)
        self.assertEqual(counts['bankruptcy_stay'], 1)
        self.assertEqual(counts['do_not_contact'], 1)
        self.assertEqual(counts['dropped'], 1)
        self.assertEqual(counts['kept'], 0)

    def test_sale_date_beats_a_stale_day_count(self):
        passed = lead('2099-000411-CA-01', AuctionDate='01/01/2000', days_to_auction=30)
        ahead = lead('2099-000412-CA-01', auction='01/01/2099', days_to_auction=-4)
        self.assertEqual(ST.exclusion_reasons(passed, ST._empty_index()), ['closed'])
        self.assertEqual(ST.exclusion_reasons(ahead, ST._empty_index()), [])

    def test_sold_flags_match_the_board(self):
        idx = ST._empty_index()
        for row in (
            lead('2099-000421-CA-01', cert='2099-1', days_to_auction=9),
            lead('2099-000422-CA-01', title_status='transferred', days_to_auction=9),
            lead('2099-000423-CA-01', sr={'st': 'sold'}, days_to_auction=9),
            lead('2099-000424-CA-01', rf={'st': 'sold'}, days_to_auction=9),
        ):
            self.assertEqual(ST.exclusion_reasons(row, idx), ['closed'], ST._case(row))

    def test_cache_helpers_match_stay_gate(self):
        import stay_gate
        active, lifted = ST._stay_sets_from_cache({
            '2099-000431-CA-01': {'a': True},
            '2099-000432-CA-01': {'a': True, 'sl': '2026-02-02'},
            '2099-000433-CA-01': {'a': False},
        })
        self.assertIn('2099-000431-CA-01', active)
        self.assertNotIn('2099-000432-CA-01', active)
        self.assertIn('2099-000432-CA-01', lifted)
        self.assertTrue(stay_gate.entry_stay_active({'a': True}))
        self.assertFalse(stay_gate.entry_stay_active({'a': True, 'sl': '2026-02-02'}))
        stay, outreach = ST._pacer_sets_from_index({
            'CACE-26-000434': {'verdict': 'active', 'env': 'prod', 'a': True, 'cases': []},
            'CACE-26-000435': {'verdict': 'unverifiable', 'env': 'prod', 'why': 'co-owner marker'},
            'CACE-26-000436': {'verdict': 'unverifiable', 'env': 'qa', 'why': 'test'},
        })
        self.assertIn('CACE-26-000434', stay)
        self.assertIn('CACE-26-000435', outreach)
        self.assertNotIn('CACE-26-000436', outreach)
        self.assertNotIn('CACE-26-000436', stay)

    def test_index_reads_send_hold_notes_and_pacer(self):
        import stay_gate
        import bk_lookup
        import optout_sync
        saved = (stay_gate.never_contact, stay_gate._load_pacer,
                 bk_lookup.send_hold, optout_sync.notes_dnc_keys)
        def never(case, path=None):
            return case == '2099-000502-CA-01'
        def load_pacer(path):
            ent = {'verdict': 'unverifiable', 'env': 'prod', 'why': 'co-owner marker'}
            return ({'CACE-26-000503': ent}, {}), '', True
        def send_hold(case, here=None):
            if case == 'CACE-26-000501':
                return True, 'federal bankruptcy check has not run for this lead'
            return False, ''
        def notes(path=None):
            return {'2099-000504-ca-01'}
        stay_gate.never_contact = never
        stay_gate._load_pacer = load_pacer
        bk_lookup.send_hold = send_hold
        optout_sync.notes_dnc_keys = notes
        try:
            rows = [
                lead('CACE-26-000501', county='BROWARD'),
                lead('2099-000502-CA-01'),
                lead('CACE-26-000503', county='BROWARD'),
                lead('2099-000504-CA-01'),
            ]
            idx = ST._build_exclusion_index(rows, rows)
        finally:
            (stay_gate.never_contact, stay_gate._load_pacer,
             bk_lookup.send_hold, optout_sync.notes_dnc_keys) = saved
        self.assertFalse(idx['dnc_unreadable'])
        self.assertIn('CACE-26-000501', idx['outreach_cases'])
        self.assertIn('CACE-26-000503', idx['outreach_cases'])
        self.assertIn('2099-000502-ca-01', idx['dnc_cases'])
        self.assertIn('2099-000504-ca-01', idx['dnc_cases'])
        self.assertIn('do_not_contact', ST.exclusion_reasons(rows[1], idx))
        self.assertIn('outreach_held', ST.exclusion_reasons(rows[0], idx))
        self.assertIn('outreach_held', ST.exclusion_reasons(rows[2], idx))

    def test_unreadable_ledger_holds_the_queue(self):
        import optout_sync
        folder = tempfile.mkdtemp(prefix='skq_')
        bad = os.path.join(folder, 'optouts.json')
        with open(bad, 'w', encoding='utf-8') as fh:
            fh.write('{')
        saved = optout_sync.OPTOUTS
        optout_sync.OPTOUTS = bad
        try:
            idx = ST._build_exclusion_index([lead('2099-000441-CA-01', days_to_auction=3)], [])
        finally:
            optout_sync.OPTOUTS = saved
        self.assertTrue(idx['dnc_unreadable'])
        self.assertIn('do_not_contact',
                      ST.exclusion_reasons(lead('2099-000441-CA-01', days_to_auction=3), idx))

    def test_call_mode_dnc_and_ledger_use_the_existing_readers(self):
        """A hand-set Call Mode status and a ledger key, through notes_dnc_keys and the envelope.
        No monkeypatch of those readers."""
        import optout_sync
        folder = tempfile.mkdtemp(prefix='skq_')
        notes_path = os.path.join(folder, 'worker_notes.json')
        ledger_path = os.path.join(folder, 'optouts.json')
        with open(notes_path, 'w', encoding='utf-8') as fh:
            json.dump({'notes': {
                '2099-000801-CA-01': {'status': 'Do Not Contact'},
                '2099-000802-CA-01': {'status': 'opted out'},
            }}, fh)
        with open(ledger_path, 'w', encoding='utf-8') as fh:
            json.dump({'_dealflow_notes': 'shape', 'exported': '2026-10-18',
                       'notes': {'2099-000803-ca-01': {'why': 'stop'}}}, fh)
        saved = (optout_sync.NOTES, optout_sync.OPTOUTS)
        optout_sync.NOTES = notes_path
        optout_sync.OPTOUTS = ledger_path
        rows = [
            lead('2099-000801-CA-01', days_to_auction=3),
            lead('2099-000802-CA-01', days_to_auction=4),
            lead('2099-000803-CA-01', days_to_auction=5),
            lead('2099-000804-CA-01', days_to_auction=6),
        ]
        try:
            idx = ST._empty_index()
            ST._fill_dnc(idx, rows)
        finally:
            optout_sync.NOTES, optout_sync.OPTOUTS = saved
        self.assertFalse(idx['dnc_unreadable'])
        self.assertIn('2099-000801-ca-01', idx['dnc_cases'])
        self.assertIn('2099-000802-ca-01', idx['dnc_cases'])
        self.assertIn('2099-000803-ca-01', idx['dnc_cases'])
        self.assertEqual(ST.exclusion_reasons(rows[0], idx), ['do_not_contact'])
        self.assertEqual(ST.exclusion_reasons(rows[1], idx), ['do_not_contact'])
        self.assertEqual(ST.exclusion_reasons(rows[2], idx), ['do_not_contact'])
        self.assertEqual(ST.exclusion_reasons(rows[3], idx), [])

    def _run(self, leads, argv, index):
        folder = tempfile.mkdtemp(prefix='skq_')
        results = os.path.join(folder, 'skiptrace_results.json')
        ST.RESULTS = results
        bd_budget.LEDGER = os.path.join(folder, 'spend.json')
        ST._EXCLUSION_OVERRIDE = index
        ST.load_all_leads = lambda: [dict(r) for r in leads]
        ST.select = lambda rows, args, llcs=None: [dict(r) for r in rows]
        ST.load_key = lambda prov: 'FAKE'
        ST.time.sleep = lambda *a, **k: None
        calls = {'n': 0}

        class Sess:
            def post(self, url, json=None, timeout=None, headers=None):
                calls['n'] += 1
                return FakeResp(200, {'results': {'persons': []}})

        ST.requests.Session = lambda: Sess()
        old_argv = sys.argv
        sys.argv = ['skiptrace.py'] + argv
        buf = io.StringIO()
        old_out = sys.stdout
        sys.stdout = buf
        code = 0
        try:
            ST.main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        finally:
            sys.stdout = old_out
            sys.argv = old_argv
        got = {}
        if os.path.exists(results):
            got = json.loads(open(results, encoding='utf-8').read())
        return code, got, calls['n'], buf.getvalue(), results

    def test_report_prints_the_typical_line_and_does_not_call(self):
        code, got, calls, out, results = self._run(
            typical_leads(), ['--all', '--report', '--provider', 'batchdata'], typical_index())
        self.assertEqual(code, 0)
        self.assertEqual(calls, 0)
        self.assertIn(TYPICAL_LINE, out)
        self.assertIn('skiptrace report: no provider call', out)
        self.assertNotIn('skiptrace cap:', out)
        self.assertFalse(os.path.exists(results))
        self.assertEqual(got, {})

    def test_cap_is_applied_after_exclusions_soonest_sale_first(self):
        ST.SKIPTRACE_RUN_CAP = 1
        code, got, calls, out, _results = self._run(
            typical_leads(), ['--all', '--provider', 'batchdata', '--max-spend', '70'],
            typical_index())
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, 1)
        self.assertEqual(list(got), ['2099-000314-CA-01'])
        self.assertIn(TYPICAL_LINE, out)
        self.assertIn('trimming 4 queued', out)

    def test_limit_does_not_fill_up_on_excluded_leads(self):
        rows = [
            lead('2099-000701-CA-01', saleBkAct=True, days_to_auction=1),
            lead('2099-000702-CA-01', lpDismissed=True, days_to_auction=2),
            lead('2099-000703-CA-01', days_to_auction=5),
            lead('2099-000704-CA-01', days_to_auction=6),
        ]
        code, got, calls, out, _results = self._run(
            rows, ['--all', '--limit', '2', '--provider', 'batchdata'], ST._empty_index())
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, 2)
        self.assertEqual(set(got), {'2099-000703-CA-01', '2099-000704-CA-01'})

    def test_county_first_still_prefers_miami_among_leads_that_remain(self):
        rows = [
            lead('2099-000601-CA-01', county='MIAMI-DADE', days_to_auction=30),
            lead('CACE-26-000602', county='BROWARD', days_to_auction=1),
            lead('2099-000603-CA-01', county='MIAMI-DADE', saleBkAct=True, days_to_auction=2),
        ]
        code, got, calls, out, _results = self._run(
            rows, ['--all', '--limit', '1', '--county-first', 'MIAMI-DADE', '--provider', 'batchdata'],
            ST._empty_index())
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, 1)
        self.assertEqual(list(got), ['2099-000601-CA-01'])
        code, got, calls, out, _results = self._run(
            rows, ['--all', '--limit', '1', '--provider', 'batchdata'], ST._empty_index())
        self.assertEqual(list(got), ['CACE-26-000602'])


if __name__ == '__main__':
    unittest.main()
