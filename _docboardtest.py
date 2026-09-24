"""_docboardtest — the Miami document dossiers reach the board as a claim, never as equity.

Run: python -u _docboardtest.py
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import doc_board as DB

HERE = os.path.dirname(os.path.abspath(__file__))


def _template():
    with open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8') as fh:
        return fh.read()


def dossier(case='2024-009959-CA-01', judgment=None, read=1, fetched=2, gaps=('a', 'b')):
    return {'schema_version': 1, 'case': case, 'county': 'MIAMI-DADE',
            'built_at': '2026-09-24T17:00:00+00:00',
            'c_documents': {'fully_read': read, 'fetched': fetched,
                            'judgment': judgment if judgment is not None else
                            {'operative': None, 'candidates': [], 'certain': False}},
            'open_gaps': list(gaps)}


ONE = {'operative': 'ocs-event/231457022/1', 'candidates': ['ocs-event/231457022/1'],
       'certain': True, 'amount': 555499.25, 'satisfied': False, 'satisfied_by': []}


class Summarize(unittest.TestCase):
    def test_one_judgment_carries_its_amount_and_ref(self):
        s = DB.summarize(dossier(judgment=ONE))
        self.assertEqual(s['j'], 'one')
        self.assertEqual(s['amt'], 555499.25)
        self.assertEqual(s['ref'], 'ocs-event/231457022/1')
        self.assertEqual((s['n'], s['f'], s['g'], s['at']), (1, 2, 2, '2026-09-24'))

    def test_a_satisfied_judgment_ships_no_amount(self):
        sat = dict(ONE, amount=None, printed_amount=555499.25, satisfied=True,
                   satisfied_by=['ocs-event/9/1'])
        s = DB.summarize(dossier(judgment=sat))
        self.assertEqual(s['j'], 'sat')
        self.assertNotIn('amt', s)

    def test_several_candidates_ship_no_amount(self):
        sev = {'operative': None, 'candidates': ['x', 'y'], 'certain': False}
        s = DB.summarize(dossier(judgment=sev))
        self.assertEqual(s['j'], 'several')
        self.assertNotIn('amt', s)
        self.assertNotIn('ref', s)

    def test_partial_satisfaction_is_its_own_state(self):
        s = DB.summarize(dossier(judgment=dict(ONE, partially_satisfied_by=['p'])))
        self.assertEqual(s['j'], 'part')
        self.assertEqual(s['amt'], 555499.25)

    def test_gap_text_never_travels(self):
        s = DB.summarize(dossier(gaps=['MORTGAGE recorded 2019 against JOHN DOE sits on this parcel']))
        self.assertEqual(s['g'], 1)
        self.assertNotIn('DOE', json.dumps(s))

    def test_non_case_json_is_ignored(self):
        self.assertIsNone(DB.summarize({'cases': 25, 'read': 3}))
        self.assertIsNone(DB.summarize([]))


class LoadAndAttach(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, name, obj):
        with open(os.path.join(self.dir, name), 'w', encoding='utf-8') as fh:
            fh.write(obj if isinstance(obj, str) else json.dumps(obj))

    def test_load_skips_nightly_and_broken_files(self):
        self.write('2024-009959-CA-01.json', dossier(judgment=ONE))
        self.write('_nightly.json', {'case': 'not-a-case', 'cases': 25})
        self.write('broken.json', '{not json')
        got = DB.load(self.dir)
        self.assertEqual(list(got), ['2024009959CA01'])

    def test_missing_folder_is_empty(self):
        self.assertEqual(DB.load(os.path.join(self.dir, 'nope')), {})

    def test_attach_adds_docs_and_touches_nothing_else(self):
        self.write('2024-009959-CA-01.json', dossier(judgment=ONE))
        row = {'case': '2024-009959-CA-01', 'judg': 400000, 'payoff': 0, 'eq': 55, 'value': 900000}
        other = {'case': '2022-000001-CA-01', 'judg': 1}
        before = dict(row)
        self.assertEqual(DB.attach([row, other], DB.load(self.dir)), 1)
        self.assertEqual({k: v for k, v in row.items() if k != 'docs'}, before)
        self.assertEqual(row['docs']['amt'], 555499.25)
        self.assertNotIn('docs', other)

    def test_case_numbers_match_across_punctuation(self):
        self.assertEqual(DB.case_key('2024-009959-CA-01'), DB.case_key(' 2024 009959 ca 01 '))


def _chip_js():
    src = _template()
    m = re.search(r'function _docChip\(r\)\{.*?\n\}\n', src, re.S)
    assert m, '_docChip not found in tracker_template.html'
    return m.group(0)


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class Chip(unittest.TestCase):
    def render(self, row):
        prelude = (
            "const fmtK = n => '$' + Math.round((+n||0)/1e3) + 'k';\n"
            "const fmtMoney = n => '$' + Math.round(n).toLocaleString('en-US');\n"
            "const esc = s => String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
            ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');\n")
        js = prelude + _chip_js() + 'process.stdout.write(_docChip(%s));' % json.dumps(row)
        return subprocess.run(['node', '-e', js], capture_output=True, text=True,
                              check=True).stdout

    def test_no_dossier_renders_nothing(self):
        self.assertEqual(self.render({'judg': 5}), '')
        self.assertEqual(self.render({'docs': {'n': 0, 'f': 0}}), '')

    def test_amount_is_a_question_and_says_unverified(self):
        html = self.render({'judg': 555499.25, 'docs': DB.summarize(dossier(judgment=ONE))})
        self.assertIn('JUDG $555k?', html)
        self.assertIn('UNVERIFIED', html)
        self.assertIn('NOT in the equity number', html)
        self.assertNotIn('BOARD', html.split('>')[1])

    def test_disagreement_with_the_clerk_is_flagged(self):
        html = self.render({'judg': 400000, 'docs': DB.summarize(dossier(judgment=ONE))})
        self.assertIn('&ne; BOARD', html)
        self.assertIn('the two disagree', html)

    def test_paid_and_several(self):
        sat = DB.summarize(dossier(judgment=dict(ONE, amount=None, satisfied=True)))
        self.assertIn('JUDG PAID?', self.render({'docs': sat}))
        sev = DB.summarize(dossier(judgment={'candidates': ['x', 'y'], 'certain': False}))
        self.assertIn('2+ JUDGMENTS', self.render({'docs': sev}))

    def test_read_but_unread_states(self):
        self.assertIn('DOC UNREAD', self.render({'docs': {'n': 0, 'f': 3, 'j': 'none'}}))
        self.assertIn('NO JUDG', self.render({'docs': {'n': 2, 'f': 3, 'j': 'none'}}))

    def test_chip_sits_in_both_row_layouts(self):
        src = _template()
        self.assertEqual(src.count('_codeLienChip(r)+_docChip(r)'), 2)


if __name__ == '__main__':
    unittest.main(verbosity=1)
