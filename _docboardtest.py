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

    def test_timeline_file_rides_with_its_dossier_and_never_replaces_it(self):
        # run_case_timeline.write_timeline puts <case>-timeline.json BESIDE the dossier. It has a
        # 'case' key too, and it sorts after the dossier, so reading it as a dossier would
        # overwrite the real summary with an empty one.
        self.write('2024-009959-CA-01.json', dossier(judgment=ONE))
        self.write('2024-009959-CA-01-timeline.json',
                   {'case': '2024-009959-CA-01', 'stay_in_effect': True, 'entries': []})
        got = DB.load(self.dir)
        self.assertEqual(list(got), ['2024009959CA01'])
        self.assertEqual(got['2024009959CA01']['amt'], 555499.25)
        self.assertIs(got['2024009959CA01']['stay'], True)

    def test_no_timeline_or_unknown_stay_ships_no_stay_key(self):
        self.write('2024-009959-CA-01.json', dossier(judgment=ONE))
        self.write('2022-012065-CA-01.json', dossier(case='2022-012065-CA-01'))
        self.write('2022-012065-CA-01-timeline.json', {'case': '2022-012065-CA-01', 'stay_in_effect': None})
        got = DB.load(self.dir)
        self.assertNotIn('stay', got['2024009959CA01'])
        self.assertNotIn('stay', got['2022012065CA01'])

    def test_a_timeline_stub_dossier_is_not_a_document_summary(self):
        # write_timeline creates {'case','county','complete'} when no dossier existed yet.
        self.assertIsNone(DB.summarize({'case': 'x', 'county': 'MIAMI-DADE', 'complete': False}))

    def test_folder_matches_run_documents(self):
        import document_store as DS
        self.assertEqual(DB.COUNTY_DIR, DS._slug('MIAMI-DADE'))

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
    m = re.search(r'function _docChip\(r\)\{.*?\nfunction _docJudgChip\(r, d, tail\)\{.*?\n\}\n', src, re.S)
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

    def test_a_stay_leads_the_chip(self):
        s = DB.summarize(dossier(judgment=ONE), {'stay_in_effect': True})
        html = self.render({'judg': 555499.25, 'docs': s})
        self.assertIn('STAY?', html)
        self.assertLess(html.index('STAY?'), html.index('JUDG'))
        s = DB.summarize(dossier(judgment=ONE), {'stay_in_effect': False})
        self.assertNotIn('STAY', self.render({'judg': 555499.25, 'docs': s}))

    def test_a_partial_reading_still_shows_its_judgment(self):
        d = dossier(judgment=ONE, read=0, fetched=1)
        d['c_documents']['documents'] = [{'read_status': 'partial', 'is': 'final_judgment'}]
        s = DB.summarize(d)
        self.assertEqual((s['n'], s['p'], s['j']), (0, 1, 'one'))
        html = self.render({'judg': 555499.25, 'docs': s})
        self.assertIn('JUDG $555k?', html)
        self.assertNotIn('UNREAD', html)
        self.assertIn('1 partly read', html)
        self.assertIn('PART READ', self.render({'docs': {'n': 0, 'p': 2, 'f': 2, 'j': 'none'}}))

    def test_stay_from_the_timeline_status_on_main(self):
        # The timeline on main has no stay_in_effect; the answer is in status.kind.
        self.assertIs(DB._stay({'status': {'kind': 'stayed_by_bankruptcy'}}), True)
        self.assertEqual(DB._stay({'status': {'kind': 'unclear', 'reason': 'Later foreclosure '
                                   'activity conflicts with an unresolved bankruptcy stay'}}), 'unclear')
        self.assertIsNone(DB._stay({'status': {'kind': 'unclear', 'reason': 'No dated dispositive entry.'}}))
        self.assertIsNone(DB._stay({'status': {'kind': 'sale_scheduled'}}))
        # #53's explicit answer wins over the status.
        self.assertIs(DB._stay({'stay_in_effect': False, 'status': {'kind': 'stayed_by_bankruptcy'}}), False)
        # Relief granted with no earlier status to restore: the CASE status is unclear, the stay is not.
        self.assertIs(DB._stay({'status': {'kind': 'unclear', 'reason': 'Stay relief found without '
                               'established pre-stay state.'}}), False)
        self.assertEqual(DB._stay({'status': {'kind': 'unclear', 'reason': 'Partial or limited stay '
                                   'relief does not establish that all foreclosure restrictions ended.'}}), 'unclear')
        s = DB.summarize(dossier(judgment=ONE), {'status': {'kind': 'unclear', 'reason':
                         'Later foreclosure activity conflicts with an unresolved bankruptcy stay'}})
        html = self.render({'judg': 555499.25, 'docs': s})
        self.assertIn('STAY UNCLEAR', html)
        self.assertIn('JUDG $555k?', html)      # the stay never hides the judgment

    def test_read_but_unread_states(self):
        self.assertIn('DOC UNREAD', self.render({'docs': {'n': 0, 'f': 3, 'j': 'none'}}))
        self.assertIn('NO JUDG', self.render({'docs': {'n': 2, 'f': 3, 'j': 'none'}}))

    def test_chip_sits_in_both_row_layouts(self):
        src = _template()
        self.assertEqual(src.count('_codeLienChip(r)+_docChip(r)'), 2)


if __name__ == '__main__':
    unittest.main(verbosity=1)
