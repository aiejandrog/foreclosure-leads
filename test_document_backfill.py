"""Synthetic fixtures only; backfill must never spend during a preview."""
import contextlib
import io
import json
import tempfile
import unittest
import os
import threading
from types import SimpleNamespace
from datetime import date
from pathlib import Path
from unittest.mock import patch

import run_documents as RD


class BackfillTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows replacement sharing semantics')
    def test_checkpoint_survives_reader_temporarily_blocking_replace(self):
        import document_backfill as BF
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with BF.State(path) as state:
                state.save()
                reader = open(path, 'rb')
                release = threading.Timer(.15, reader.close)
                release.start()
                try:
                    state.data['actual_usd'] = .25
                    state.save()
                finally:
                    release.join()
                    reader.close()
            self.assertEqual(BF.snapshot(path)['actual_usd'], .25)

    def test_auction_order_prioritizes_next_45_days_then_future_past_unknown(self):
        import document_backfill as BF
        rows = [{'Case #': name, 'county': 'MIAMI-DADE', 'AuctionDate': when}
                for name, when in [('past','09/22/2026'), ('later','12/01/2026'),
                    ('edge','11/07/2026'), ('unknown','bad'), ('next','09/24/2026'),
                    ('today','2026-09-23'), ('outside','11/08/2026')]]
        picked = BF.select_cases(rows, {}, today=date(2026, 9, 23))
        self.assertEqual([r['case'] for r in picked],
                         ['today', 'next', 'edge', 'outside', 'later', 'past', 'unknown'])

    def test_progress_separates_finished_attempts_from_complete_cases(self):
        import document_backfill as BF
        entries = [{'case': n} for n in ('done', 'gaps', 'paused', 'new')]
        data = {'cases': {e['case']: {'fingerprint': BF.fingerprint(e), 'status': status}
                         for e, status in zip(entries, ('complete','assessed_with_gaps','budget_paused'))}}
        result = BF.progress(entries, data)
        self.assertEqual(result['cases_done'], 2)
        self.assertEqual(result['cases_complete'], 1)
        self.assertEqual(result['cases_left'], 2)

    def test_dry_run_includes_ownerless_dedupes_and_has_no_clients(self):
        rows = [
            {'Case #': '2099-000001-CA-01', 'county': 'MIAMI-DADE', 'owner_clean': 'TEST'},
            {'Case #': '2099-000001-CA-01', 'county': 'MIAMI-DADE', 'owner_clean': 'TEST'},
            {'Case #': '2099-000002-CC-01', 'county': 'MIAMI-DADE'},
            {'Case #': '2099-000003-CA-01', 'county': 'BROWARD'},
        ]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'leads.json'
            source.write_text(json.dumps(rows))
            output = io.StringIO()
            with contextlib.redirect_stdout(output), \
                 patch.object(RD, 'dossier_path', side_effect=lambda county, name: Path(folder) / (name + '.json')), \
                 patch('document_vision.VisionReader.client', side_effect=AssertionError('client on dry-run')), \
                 patch.object(RD, 'DocumentQueue', side_effect=AssertionError('queue on dry-run')):
                result = RD.main(['--backfill', '--leads-file', str(source), '--vision',
                                  '--vision-max-spend', '1.00', '--dry-run'])
            self.assertEqual(result, 0)
            self.assertIn('2 unique Miami cases', output.getvalue())
            self.assertIn('$1.00', output.getvalue())
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ['leads.json'])

    def test_backfill_requires_explicit_finite_cap(self):
        for extra in ([], ['--vision-max-spend', 'nan'], ['--vision-max-spend', 'inf'],
                      ['--vision-max-spend', '-1']):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as raised:
                RD.main(['--backfill', '--dry-run'] + extra)
            self.assertEqual(raised.exception.code, 2)

    def test_backfill_rejects_other_paid_paths(self):
        for extra in (['--token-budget', '1'], ['--name-budget', '1'], ['--interpret']):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as raised:
                RD.main(['--backfill', '--vision-max-spend', '1', '--dry-run'] + extra)
            self.assertEqual(raised.exception.code, 2)

    def test_resume_skips_finished_attempt_not_interrupted_or_changed(self):
        import document_backfill as BF
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            entry = {'case': '2099-000001-CA-01', 'owner': 'TEST', 'folio': '1', 'chain': {}}
            with BF.State(path) as state:
                state.start(entry)
            with BF.State(path) as state:
                self.assertTrue(state.pending(entry))
                state.finish(entry, {'complete': False, 'open_gaps': ['unknown']})
            with BF.State(path) as state:
                self.assertFalse(state.pending(entry))
                self.assertTrue(state.pending(entry, retry_gaps=True))
                self.assertTrue(state.pending(dict(entry, folio='2')))
                self.assertEqual(state.data['cases'][entry['case']]['status'], 'assessed_with_gaps')

    def test_budget_reserves_before_request_and_survives_crash(self):
        import document_backfill as BF
        from document_interpreter import BudgetExhausted
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with BF.State(path) as state:
                budget = BF.PersistentBudget(0.10, state)
                budget.check(1000, 2000)  # $0.055, unknown outcome on interruption
            with BF.State(path) as state:
                budget = BF.PersistentBudget(0.10, state)
                self.assertAlmostEqual(budget.spent, 0.055)
                with self.assertRaises(BudgetExhausted):
                    budget.check(1000, 2000)

    def test_record_refunds_unused_reservation_but_retains_actual_spend(self):
        import document_backfill as BF
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with BF.State(path) as state:
                budget = BF.PersistentBudget(1, state)
                budget.check(1000, 2000)
                budget.record(1000, 1000)  # $0.030
            with BF.State(path) as state:
                budget = BF.PersistentBudget(1, state)
                self.assertAlmostEqual(budget.spent, 0.03)
                self.assertAlmostEqual(state.data['actual_usd'], 0.03)

    def test_concurrent_runner_cannot_open_same_state(self):
        import document_backfill as BF
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with BF.State(path):
                with self.assertRaises(RuntimeError):
                    with BF.State(path):
                        self.fail('second worker acquired state')

    def test_paid_page_survives_restart_without_second_api_call(self):
        import document_backfill as BF
        import document_vision as DV
        class Client:
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
                    content=[SimpleNamespace(type='text', text='{"rows":[],"grand_total":null}')],
                    stop_reason='end_turn')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            client = Client()
            reader = DV.VisionReader(client=client)
            with BF.State(path) as state:
                budget = BF.PersistentBudget(1, state)
                budget.check(1000, 2000)  # Prior transport failure; reservation stays.
                first = reader.read_page(b'synthetic image', budget)
                self.assertAlmostEqual(first['usd'], .0075)
            with BF.State(path) as state:
                second = reader.read_page(b'synthetic image', BF.PersistentBudget(1, state))
                self.assertEqual(second['usd'], 0)
            self.assertEqual(client.requests, 1)

    def test_runner_resumes_interrupted_case_and_skips_saved_dossier(self):
        rows = [{'Case #': '2099-000001-CA-01', 'county': 'MIAMI-DADE', 'owner_clean': 'TEST'},
                {'Case #': '2099-000002-CA-01', 'county': 'MIAMI-DADE', 'owner_clean': 'TEST'}]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'leads.json'
            source.write_text(json.dumps(rows))
            calls = []
            def process(entry, *args, **kwargs):
                self.assertTrue(kwargs['resume'])
                calls.append(entry['case'])
                if len(calls) == 2:
                    raise KeyboardInterrupt()
                return {'complete': False, 'open_gaps': ['unavailable']}
            with patch.object(RD, 'dossier_path', side_effect=lambda county, name: Path(folder) / (name + '.json')), \
                 patch.object(RD, 'DocumentQueue', side_effect=lambda: __import__('document_queue').DocumentQueue(str(Path(folder) / 'queue.db'))), \
                 patch.object(RD, 'run_case', side_effect=process):
                argv = ['--backfill', '--enable', '--leads-file', str(source),
                        '--vision-max-spend', '1', '--no-ocr']
                with self.assertRaises(KeyboardInterrupt):
                    RD.main(argv)
                self.assertEqual(RD.main(argv), 0)
            self.assertEqual(calls, ['2099-000001-CA-01', '2099-000002-CA-01', '2099-000002-CA-01'])

    def test_corrupt_ledger_does_not_reset_spend(self):
        import document_backfill as BF
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text('{broken')
            with self.assertRaises(ValueError):
                with BF.State(path):
                    self.fail('corrupt ledger opened')

    def test_backfill_ignores_environment_enable_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'leads.json'
            source.write_text('[]')
            with patch.dict('os.environ', {'DEALFLOW_DOCS': '1'}), \
                 patch.object(RD, 'dossier_path', side_effect=lambda county, name: Path(folder) / (name + '.json')):
                self.assertEqual(RD.main(['--backfill', '--leads-file', str(source),
                                          '--vision-max-spend', '1']), 0)
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ['leads.json'])

    def test_settlement_and_cached_response_are_one_durable_write(self):
        import document_backfill as BF
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with BF.State(path) as state:
                budget = BF.PersistentBudget(1, state)
                budget.check(1000, 2000)
                original_save = state.save
                def crash_after_save():
                    original_save()
                    raise KeyboardInterrupt()
                with patch.object(state, 'save', side_effect=crash_after_save):
                    with self.assertRaises(KeyboardInterrupt):
                        budget.record_read(1000, 1000, 'fixture', {'rows': [], 'usd': .03})
            with BF.State(path) as state:
                budget = BF.PersistentBudget(1, state)
                self.assertAlmostEqual(state.data['actual_usd'], .03)
                self.assertEqual(budget.cached_read('fixture')['usd'], 0)

    def test_done_ocr_document_can_reach_vision_without_downloading(self):
        import fitz
        import document_store as DS
        import document_queue as DQ
        import miami_judgment as MJ
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            key = 'a' * 64
            pdf = root / (key[:16] + '.pdf')
            with fitz.open() as doc:
                doc.new_page().insert_text((72,72), 'TOTAL $12.34')
                doc.save(pdf)
            manifest = {'county': RD.COUNTY, 'case': '2099-000001-CA-01',
                        'path': str(pdf), 'meta_path': str(root / (key[:16] + '.json')),
                        'document_key': key, 'pages': 1, 'source_ref': 'official_records/1-2',
                        'page_count_verified': True, 'pages_expected': 1}
            reading = DS.read_pages(str(pdf))
            reading['pages'][0].update(outcome='ocr_text', text_source='ocr')
            DS.pipeline_write(manifest['meta_path'], manifest)
            with patch.object(DS, 'case_dir', return_value=root):
                DS.save_page_text(manifest, reading)
                with DQ.DocumentQueue(str(root / 'queue.db')) as queue:
                    queue.add(RD.COUNTY, manifest['case'], manifest['source_ref'], 'recorded_instrument', {})
                    job = queue.claim_ref('seed', RD.COUNTY, manifest['case'], manifest['source_ref'], 'recorded_instrument')
                    queue.complete(job['id'], 'seed', sha256=key, reader_version=MJ.PIPELINE_VERSION, read_status='read')
                    class NoDownload:
                        def retrieve_document(self, record):
                            raise AssertionError('completed document downloaded twice')
                    rows = MJ.collect_recorded(manifest['case'], [{'reC_BOOK':'1', 'reC_PAGE':'2', 'doC_TYPE':'JUDGMENT'}],
                        queue=queue, collector=NoDownload(), resume=True, reuse_done=True)
                    self.assertEqual(rows[0]['status'], 'stored')
                    self.assertIn('TOTAL', rows[0]['reading']['pages'][0]['text'])
                    detail = {'figures': [], 'grand_totals': [], 'pages': {}, 'errors': {}}
                    with patch.object(MJ.DV, 'read_document', return_value=detail) as vision:
                        MJ.vision_candidates(rows[0]['path'], rows[0]['reading'], None)
                    self.assertEqual(vision.call_args.args[1], [1])


if __name__ == '__main__':
    unittest.main()
