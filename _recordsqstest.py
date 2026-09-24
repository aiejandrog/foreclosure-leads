"""records_qs.json lives under DEALFLOW_DIR, and a legacy checkout copy is merged in, never lost.

Every key in that cache is a homeowner's name; it sat in the repo folder until 2026-09-24.
"""
import json
import os
import shutil
import tempfile
import unittest

import paths as P


class RecordsQsMoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='dealflow-qs-')
        self.repo = os.path.join(self.tmp, 'repo')
        self.out = os.path.join(self.tmp, 'DEALFLOW')
        os.makedirs(self.repo)
        self.saved = (P._REPO, P.RECORDS_QS, P.DEALFLOW_DIR, P._DEFAULT_DIR,
                      os.environ.pop('DEALFLOW_DIR', None))
        P._REPO, P.DEALFLOW_DIR, P._DEFAULT_DIR = self.repo, self.out, self.out
        P.RECORDS_QS = os.path.join(self.out, 'records_qs.json')
        self.legacy = os.path.join(self.repo, 'records_qs.json')

    def tearDown(self):
        P._REPO, P.RECORDS_QS, P.DEALFLOW_DIR, P._DEFAULT_DIR, env = self.saved
        if env is not None:
            os.environ['DEALFLOW_DIR'] = env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh)

    def _read(self, path):
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)

    def test_no_legacy_file_resolves_under_dealflow_dir_and_creates_it(self):
        self.assertEqual(P.records_qs(), P.RECORDS_QS)
        self.assertFalse(os.path.exists(P.RECORDS_QS))
        self.assertTrue(os.path.isdir(self.out), 'a writer must be able to save its first token')

    def test_dealflow_dir_set_explicitly_to_the_default_still_moves(self):
        self._write(self.legacy, {'OWNER A': 'qs-a'})
        os.environ['DEALFLOW_DIR'] = self.out + os.sep
        try:
            P.records_qs()
        finally:
            del os.environ['DEALFLOW_DIR']
        self.assertFalse(os.path.exists(self.legacy))
        self.assertEqual(self._read(P.RECORDS_QS), {'OWNER A': 'qs-a'})

    def test_an_unreadable_destination_does_not_hide_a_valid_legacy_cache(self):
        self._write(self.legacy, {'OWNER A': 'qs-a'})
        os.makedirs(self.out, exist_ok=True)
        with open(P.RECORDS_QS, 'w', encoding='utf-8') as fh:
            fh.write('{truncated')
        self.assertEqual(P.records_qs(), self.legacy)
        self.assertTrue(os.path.exists(self.legacy))

    def test_a_legacy_file_is_moved_out_of_the_checkout(self):
        self._write(self.legacy, {'OWNER A': 'qs-a'})
        self.assertEqual(P.records_qs(), P.RECORDS_QS)
        self.assertFalse(os.path.exists(self.legacy))
        self.assertEqual(self._read(P.RECORDS_QS), {'OWNER A': 'qs-a'})

    def test_both_copies_merge_and_the_newer_location_wins_a_clash(self):
        self._write(self.legacy, {'OWNER A': 'old-a', 'OWNER B': 'qs-b'})
        self._write(P.RECORDS_QS, {'OWNER A': 'new-a'})
        P.records_qs()
        self.assertEqual(self._read(P.RECORDS_QS), {'OWNER A': 'new-a', 'OWNER B': 'qs-b'})
        self.assertFalse(os.path.exists(self.legacy))

    def test_an_unreadable_legacy_file_is_left_in_place_but_not_used(self):
        # Readers json.load the path; handing them a corrupt file would crash the night. It stays
        # on disk for a person to recover, and the runs start a fresh cache in the new place.
        with open(self.legacy, 'w', encoding='utf-8') as fh:
            fh.write('{not json')
        self.assertEqual(P.records_qs(), P.RECORDS_QS)
        self.assertTrue(os.path.exists(self.legacy))

    def test_an_overridden_dealflow_dir_never_moves_the_real_file(self):
        self._write(self.legacy, {'OWNER A': 'qs-a'})
        P._DEFAULT_DIR = os.path.join(self.tmp, 'the-real-default')
        self.assertEqual(P.records_qs(), P.RECORDS_QS)
        self.assertTrue(os.path.exists(self.legacy))
        self.assertFalse(os.path.exists(P.RECORDS_QS))


if __name__ == '__main__':
    unittest.main()
