"""Explicit CLI selection must reclaim done jobs; nightly selection must not."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import document_queue
import miami_judgment as MJ
import run_documents as RD


class ExplicitCaseRereadTests(unittest.TestCase):
    def test_explicit_case_reclaims_done_but_nightly_skips(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as folder:
                case = '2099-000001-CA-01'
                ref = 'official_records/1-2'
                queue = document_queue.DocumentQueue(str(Path(folder) / 'queue.db'))
                queue.add(RD.COUNTY, case, ref, 'recorded_instrument', {})
                job = queue.claim_ref('seed', RD.COUNTY, case, ref, 'recorded_instrument')
                queue.complete(job['id'], 'seed', reader_version=MJ.PIPELINE_VERSION,
                               read_status='read')
                reclaimed = []

                def reader(selected_case, records, **kwargs):
                    claimed = kwargs['queue'].claim_ref(
                        'test-reader', RD.COUNTY, selected_case, ref, 'recorded_instrument',
                        reader_version=MJ.PIPELINE_VERSION, force=not kwargs['resume'])
                    reclaimed.append(claimed is not None)
                    return {'documents': [], '_inventory': None}

                entry = {'case': case, 'owner': 'SYNTHETIC FIXTURE', 'chain': {}}
                with patch.object(RD, '_lead_rows', return_value=[{}]), \
                     patch.object(RD, 'pick_cases', return_value=[entry]), \
                     patch.object(RD, '_load', return_value={'SYNTHETIC FIXTURE': 'fake'}), \
                     patch.object(RD, 'DocumentQueue', return_value=queue), \
                     patch.object(RD, 'dossier_path', side_effect=lambda county, name: Path(folder) / (name + '.json')), \
                     patch('records_liens.records_by_qs', return_value=[]), \
                     patch.object(MJ, 'run', side_effect=reader), \
                     patch.object(MJ, 'log_corroboration'):
                    args = ['--enable', '--no-ocr']
                    if explicit:
                        args += ['--case', case]
                    self.assertEqual(RD.main(args), 0)
                self.assertEqual(reclaimed, [explicit])


if __name__ == '__main__':
    unittest.main()
