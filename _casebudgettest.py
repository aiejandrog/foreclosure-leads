"""Per-case vision shares: no case can spend what a pending case was given. No network, no spend.

    python -m unittest _casebudgettest
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import document_backfill as BF
import document_case_budget as CB
import document_vision as DV
from document_interpreter import BudgetExhausted

ROSTER = ['2026-00000%d-CA-01' % i for i in range(1, 6)]


class Client:
    """Counts requests. 1000 input tokens; 100 output tokens billed -> $0.0075 actual, and the
    worst case checked before the call (2000 output tokens) is $0.055."""

    def __init__(self):
        self.messages = self
        self.requests = 0

    def with_options(self, **kwargs):
        return self

    def count_tokens(self, **kwargs):
        return SimpleNamespace(input_tokens=1000)

    def create(self, **kwargs):
        self.requests += 1
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                               content=[SimpleNamespace(type='text',
                                                        text='{"rows":[],"grand_total":null}')],
                               stop_reason='end_turn')


def spend_until_refused(budget, reader, prefix):
    """Read distinct pages until the budget refuses. Returns how many were bought."""
    bought = 0
    for n in range(200):
        try:
            reader.read_page(('%s-page-%d' % (prefix, n)).encode(), budget)
        except BudgetExhausted:
            return bought
        bought += 1
    raise AssertionError('budget never refused')


class CaseShareTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'ledger.json'
        self.client = Client()
        self.reader = DV.VisionReader(client=self.client)

    def tearDown(self):
        self.folder.cleanup()

    def test_pilot_defect_reproduced_then_fixed(self):
        """The 2026-09-23 failure: case 1 walks its name-search candidates and a SHARED budget lets
        it take everything. With shares, case 5 still gets its full share."""
        with BF.State(self.path) as state:
            shared = BF.PersistentBudget(0.75, state)
            spend_until_refused(shared, self.reader, 'old-case1')
            with self.assertRaises(BudgetExhausted):
                shared.check(1000, 2000)             # case 5 would get nothing
        self.path.unlink()
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(0.75, state), ROSTER)
            first = spend_until_refused(alloc.for_case(ROSTER[0]), self.reader, 'case1')
            self.assertGreater(first, 0)
            self.assertLessEqual(alloc.for_case(ROSTER[0]).spent, 0.15 + 1e-9)
            for case in ROSTER[1:]:
                self.assertAlmostEqual(alloc.allowance(case), 0.15)
            self.assertGreater(spend_until_refused(alloc.for_case(ROSTER[4]), self.reader,
                                                   'case5'), 0)

    def test_finished_case_leftover_is_borrowable_pending_share_is_not(self):
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER)
            self.reader.read_page(b'a-1', alloc.for_case(ROSTER[0]))      # $0.0075 actual
            self.assertAlmostEqual(alloc.allowance(ROSTER[1]), 0.2)       # A still pending
            alloc.finish(ROSTER[0])
            self.assertAlmostEqual(alloc.allowance(ROSTER[1]), 0.2 + 0.2 - 0.0075)
            # C, D, E are pending: B can never reach their 0.6.
            spend_until_refused(alloc.for_case(ROSTER[1]), self.reader, 'b')
            self.assertLessEqual(alloc.for_case(ROSTER[1]).spent, 0.4 - 0.0075 + 1e-9)
            for case in ROSTER[2:]:
                # Each pending share is whole; what B could not use of A's leftover is extra.
                self.assertGreaterEqual(alloc.allowance(case), 0.2 - 1e-9)
            self.assertAlmostEqual(sum(max(0.0, 0.2 - alloc.for_case(c).spent)
                                       for c in ROSTER[2:]), 0.6)

    def test_refusal_happens_before_the_request(self):
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(0.10, state), ROSTER)   # $0.02 shares
            before = self.client.requests
            with self.assertRaises(BudgetExhausted):
                self.reader.read_page(b'x', alloc.for_case(ROSTER[0]))           # worst $0.055
            self.assertEqual(self.client.requests, before)
            self.assertEqual(state.data['reserved'], {})

    def test_uncertain_call_is_not_repeated_after_restart_and_stays_charged(self):
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER)
            budget = alloc.for_case(ROSTER[0])
            broken = Client()
            def die(**kwargs):
                raise KeyboardInterrupt()           # the request left; nothing came back
            broken.create = die
            with self.assertRaises(KeyboardInterrupt):
                DV.VisionReader(client=broken).read_page(b'page-7', budget)
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER)
            self.assertEqual(alloc.report()['uncertain_paid_calls'], 1)
            self.assertAlmostEqual(alloc.for_case(ROSTER[0]).spent, 0.055)
            with self.assertRaises(CB.UncertainPaidCall):
                self.reader.read_page(b'page-7', alloc.for_case(ROSTER[0]))
            self.assertEqual(self.client.requests, 0)
            # A different page is not blocked by it.
            self.reader.read_page(b'page-8', alloc.for_case(ROSTER[0]))
            self.assertEqual(self.client.requests, 1)

    def test_settled_page_is_free_on_restart(self):
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER)
            self.reader.read_page(b'p', alloc.for_case(ROSTER[0]))
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER)
            again = self.reader.read_page(b'p', alloc.for_case(ROSTER[0]))
        self.assertEqual(again['usd'], 0.0)
        self.assertEqual(self.client.requests, 1)

    def test_roster_change_keeps_charges_and_splits_what_is_left(self):
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER)
            self.reader.read_page(b'p', alloc.for_case(ROSTER[0]))
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER[:2])
            self.assertAlmostEqual(state.data['actual_usd'], 0.0075)
            self.assertAlmostEqual(alloc.batch['share'], (1.0 - 0.0075) / 2)
            with self.assertRaises(ValueError):
                alloc.for_case(ROSTER[4])           # not on this roster: no share at all

    def test_same_roster_resumes_the_same_batch(self):
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER)
            self.reader.read_page(b'p', alloc.for_case(ROSTER[0]))
            alloc.finish(ROSTER[0])
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(1.0, state), list(reversed(ROSTER)))
            self.assertTrue(alloc.batch['cases'][ROSTER[0]]['finished'])
            self.assertAlmostEqual(alloc.batch['share'], 0.2)

    def test_raising_the_cap_recomputes_the_split(self):
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(0.10, state), ROSTER)
            self.assertAlmostEqual(alloc.batch['share'], 0.02)
        with BF.State(self.path) as state:
            alloc = CB.CaseAllocator(BF.PersistentBudget(0.50, state), ROSTER)
            self.assertAlmostEqual(alloc.batch['share'], 0.10)

    def test_a_spent_global_cap_reads_as_exhausted_for_every_case(self):
        with BF.State(self.path) as state:
            state.data['actual_usd'] = 0.99
            budget = BF.PersistentBudget(1.0, state)
            alloc = CB.CaseAllocator(budget, ROSTER)
            budget.exhausted = True
            self.assertTrue(alloc.for_case(ROSTER[3]).exhausted)

    def test_global_cap_still_governs(self):
        with BF.State(self.path) as state:
            state.data['actual_usd'] = 0.9          # spent by an earlier batch
            alloc = CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER)
            self.assertAlmostEqual(alloc.batch['share'], 0.02)
            self.assertLessEqual(alloc.allowance(ROSTER[0]), 0.02 + 1e-9)

    def test_corrupt_allocation_fails_closed(self):
        with BF.State(self.path) as state:
            CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER)
        data = json.loads(self.path.read_text())
        data['allocation']['share'] = -1
        self.path.write_text(json.dumps(data))
        with BF.State(self.path) as state:
            with self.assertRaises(ValueError):
                CB.CaseAllocator(BF.PersistentBudget(1.0, state), ROSTER)

    def test_memory_state_for_the_nightly(self):
        alloc = CB.CaseAllocator(BF.PersistentBudget(0.2, CB.MemoryState()), ROSTER[:2])
        spend_until_refused(alloc.for_case(ROSTER[0]), self.reader, 'n')
        self.assertAlmostEqual(alloc.allowance(ROSTER[1]), 0.1)


class RunnerWiringTests(unittest.TestCase):
    """The backfill and the nightly hand run_case an allocator, not one shared pool."""

    def test_backfill_gives_each_case_a_share_and_one_spent_share_does_not_pause(self):
        from unittest.mock import patch
        import run_documents as RD
        rows = [{'Case #': c, 'county': 'MIAMI-DADE', 'owner_clean': 'TEST'} for c in ROSTER[:3]]
        seen = []
        def process(entry, qs, **kwargs):
            alloc = kwargs['vision_budget']
            self.assertIsInstance(alloc, CB.CaseAllocator)
            share = alloc.for_case(entry['case'])
            seen.append((entry['case'], round(alloc.allowance(entry['case']), 6)))
            # Spend this case's whole share, as the pilot's first case did.
            spend_until_refused(share, DV.VisionReader(client=Client()), entry['case'])
            alloc.finish(entry['case'])
            return {'complete': False, 'open_gaps': ['budget_exhausted']}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'leads.json'
            source.write_text(json.dumps(rows))
            with patch.object(RD, 'dossier_path',
                              side_effect=lambda county, name: Path(folder) / (name + '.json')), \
                 patch.object(RD, 'DocumentQueue', side_effect=lambda: __import__(
                     'document_queue').DocumentQueue(str(Path(folder) / 'queue.db'))), \
                 patch.object(DV.VisionReader, 'client', return_value=Client()), \
                 patch.object(RD, 'run_case', side_effect=process):
                code = RD.main(['--backfill', '--enable', '--vision', '--leads-file', str(source),
                                '--vision-max-spend', '0.30', '--no-ocr'])
        self.assertEqual(code, 0)                 # no pause: the cap itself was never spent
        self.assertEqual([c for c, _ in seen], ROSTER[:3])
        self.assertAlmostEqual(seen[0][1], 0.10)  # first case: its own third, nothing more


class ReplayTests(unittest.TestCase):
    """replay_paid_selection over synthetic saved evidence: no client, no queue, $0."""

    def test_replay_refuses_what_the_pilot_bought_on_history_and_reaches_the_judgment(self):
        from unittest.mock import patch
        import paths as P
        import document_store as DS
        import replay_paid_selection as RP
        case = '2026-000123-CA-01'
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(P, 'DEALFLOW_DIR', folder):
            index = {}
            docs = [('100-1', '02/01/2011', 'FINAL JUDGMENT Case No. 2010-044444-CA-01', .0675),
                    ('100-7', '08/25/2026', 'FINAL JUDGMENT Case No. %s TOTAL $1,000.00' % case, 0)]
            for n, (ref, when, text, usd) in enumerate(docs):
                key = ('%d' % n) * 64
                manifest = {'county': 'MIAMI-DADE', 'case': case, 'document_key': key,
                            'source_ref': 'official_records/' + ref, 'pages': 1,
                            'path': str(Path(folder) / (ref + '.pdf'))}
                cdir = DS.case_dir('MIAMI-DADE', case)
                cdir.mkdir(parents=True, exist_ok=True)
                (cdir / (key[:16] + '.json')).write_text(json.dumps(manifest))
                DS.save_page_text(manifest, {'pages': [{'page': 1, 'text': text,
                                                        'outcome': 'text'}],
                                             'read_status': 'read'})
                if usd:
                    (cdir / (key[:16] + '-text') / 'vision.json').write_text(json.dumps(
                        {'usd': usd, 'read_at': '2026-09-23T15:00:00+00:00', 'pages': {'1': {}}}))
                book, page = ref.split('-')
                index['%s/%s' % (book, page)] = {'reC_DATE': when, 'doC_TYPE': 'JUDGMENT'}
            index_path = Path(folder) / 'records_index.json'
            index_path.write_text(json.dumps(index))
            import requests
            with patch.object(DV.VisionReader, 'client', side_effect=AssertionError('no client')), \
                    patch.object(requests.Session, 'request',
                                 side_effect=AssertionError('no network')):
                RP.main(['--case', case, '--cap', '0.10', '--as-of', '2026-09-23',
                         '--index', str(index_path)])
            report = json.loads((Path(folder) / 'reports' /
                                 'paid-selection-replay-2026-09-23.json').read_text())
        row = report['cases'][0]
        self.assertEqual(row['would_read_within_share'][0]['source_ref'], 'official_records/100-7')
        self.assertEqual(row['refused'][0]['reason'], 'other_action')
        self.assertAlmostEqual(row['pilot_usd_now_refused'], .0675)
        self.assertTrue(row['reaches_tier0_or_tier1'])
        self.assertEqual(report['api_requests'], 0)


if __name__ == '__main__':
    unittest.main()
