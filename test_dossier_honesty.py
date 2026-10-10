"""Two ways a dossier said more than it knew (acceptance review 2026-10-08). Fake data only.

1. "VERIFIED" in d beside "no document read" in c: the state is the recorded index's, and the label
   now says so. The state itself, rests_on and speakable_as_fact are unchanged.
2. A section c built weeks before the timeline read more of the case: the timeline writer marks it
   stale and names the rebuild, and clears the mark once c catches up."""
import unittest

import case_dossier as CD
import equity_state
import run_case_timeline as RCT

CASE = '2099-000001-CA-01'
# A traced, priced chain (the same shape _dossiertest uses; not imported, so its data-folder setup
# does not depend on import order).
CHAIN = {'conf': 'ok', 'nrec': 30, 'liens': [{'d': '2005-01-01', 'amt': 250000, 'party': 'A BANK',
                                              'bp': '29001-1234', 'st': 'OPEN'}],
         'open_count': 1, 'surv': 250000, 'first_est': 250000, 'subdiv': 'SYNTHETIC ESTATES'}
READ_DOC = {'source_ref': 'official_records/1-2', 'status': 'stored', 'read_status': 'read',
            'page_count_verified': True, 'pages': 1, 'classification': {'kind': 'final_judgment'}}


class IndexOnlyLabelTests(unittest.TestCase):
    def test_a_priced_state_with_no_document_read_says_index_only(self):
        d = CD.build(CASE, 'MIAMI-DADE', chain=CHAIN)['d_picture']
        self.assertEqual(d['eqstate'], 'priced')
        self.assertEqual(d['short'], 'VERIFIED (index only)')
        self.assertIn('no document has been read', d['verdict'])
        self.assertEqual(d['rests_on'], ['b'])
        self.assertEqual(d['speakable_as_fact'], 'priced' in equity_state.FACT)

    def test_with_a_document_read_the_label_is_unchanged(self):
        d = CD.build(CASE, 'MIAMI-DADE', chain=CHAIN, documents=[READ_DOC])['d_picture']
        self.assertEqual(d['short'], equity_state.SHORT['priced'])
        self.assertEqual(d['verdict'], equity_state.LABEL['priced'])

    def test_a_state_that_is_not_a_fact_is_not_relabelled(self):
        d = CD.build(CASE, 'MIAMI-DADE', chain=None)['d_picture']
        self.assertEqual(d['short'], equity_state.SHORT[d['eqstate']])


class StaleSectionCTests(unittest.TestCase):
    def dossier(self, fetched):
        return {'case': CASE, 'built_at': '2026-09-23T00:00:00+00:00', 'complete': True,
                'c_documents': {'fetched': fetched}, 'open_gaps': ['an older gap']}

    def test_a_timeline_with_more_documents_marks_c_stale(self):
        d = self.dossier(24)
        RCT.mark_stale_documents(d, {'counts': {'documents': 103}})
        self.assertEqual(d['c_documents_stale']['timeline_documents'], 103)
        self.assertIn(CASE, d['c_documents_stale']['rebuild'])
        self.assertFalse(d['complete'])
        self.assertEqual(sum(g.startswith(RCT._STALE_GAP) for g in d['open_gaps']), 1)
        self.assertIn('an older gap', d['open_gaps'])

    def test_running_twice_does_not_stack_the_gap(self):
        d = self.dossier(24)
        for _ in range(2):
            RCT.mark_stale_documents(d, {'counts': {'documents': 103}})
        self.assertEqual(sum(g.startswith(RCT._STALE_GAP) for g in d['open_gaps']), 1)

    def test_a_current_c_is_left_alone_and_an_old_mark_cleared(self):
        d = self.dossier(24)
        RCT.mark_stale_documents(d, {'counts': {'documents': 103}})
        d['c_documents']['fetched'] = 103
        RCT.mark_stale_documents(d, {'counts': {'documents': 103}})
        self.assertNotIn('c_documents_stale', d)
        self.assertEqual(d['open_gaps'], ['an older gap'])

    def test_a_dossier_without_c_is_untouched(self):
        d = {'case': CASE, 'complete': False}
        RCT.mark_stale_documents(d, {'counts': {'documents': 5}})
        self.assertEqual(d, {'case': CASE, 'complete': False})


if __name__ == '__main__':
    unittest.main()
