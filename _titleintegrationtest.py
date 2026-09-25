"""Offline integration regression tests; synthetic parties, no county/API requests."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import document_queue as DQ
import document_walk as W
import miami_title_discovery as T


class Search:
    def __init__(self, results):
        self.results = results

    def search(self, name):
        return self.results.get(name, [])


class TitleIntegrationTest(unittest.TestCase):
    entry = {'case':'2099-000001-CA-01', 'owner':'SYNTHETIC OWNER', 'folio':'1234567890123'}

    def investigate(self, new_rows=None):
        with tempfile.TemporaryDirectory() as folder:
            queue = DQ.DocumentQueue(str(Path(folder) / 'queue.db'))
            index = W.RecordIndex(str(Path(folder) / 'index.json'))
            with patch('document_queue.DocumentQueue', return_value=queue), \
                 patch('document_walk.RecordIndex', return_value=index), \
                 patch('document_collectors.MiamiCollector.enumerate_documents',
                       return_value={'raw':{'parties':[]}, 'pagination_verified':False}), \
                 patch('miami_title_discovery.stored_evidence', return_value=([], [])), \
                 patch('document_walk.walk', return_value=(new_rows or [], {'unresolved':[]})):
                return T.investigate(self.entry, Search({}))

    def test_missing_deed_does_not_skip_owner_search_report(self):
        result = self.investigate()
        searched = [p['name'] for s in result['other_name_searches'] for p in s['searched']]
        self.assertIn('SYNTHETIC OWNER', searched)
        self.assertEqual(result['status'], 'unknown')

    def test_citation_discovered_deed_creates_named_pending_search(self):
        row = {'source_ref':'official_records/900-10', 'status':'stored',
               'doc_type':'WARRANTY DEED', 'stored':{'record_key':{
                   'book':'900','page':'10','cfn_master_id':'synthetic',
                   'doc_type':'WARRANTY DEED','rec_date':'01/01/2099'}},
               'reading':{'read_status':'complete','pages':[{
                   'page':1,'outcome':'text','text_source':'embedded',
                   'text':'Folio: 1234567890123\nGrantor: SYNTHETIC PRIOR\nGrantee: SYNTHETIC CITED OWNER'}]}}
        result = self.investigate([row])
        self.assertTrue(any('SYNTHETIC CITED OWNER' in gap and 'search' in gap
                            for gap in result['gaps']))

    def test_attachment_preserves_equity_section_and_original(self):
        dossier = {'d_picture':{'rests_on':['b'], 'verdict':'unknown'},
                   'complete':False,'open_gaps':['existing']}
        before = copy.deepcopy(dossier)
        result = T.attach_report(dossier, {'status':'unknown','gaps':['missing deed']})
        self.assertEqual(result['d_picture'], before['d_picture'])
        self.assertEqual(dossier, before)
        self.assertFalse(result['complete'])


if __name__ == '__main__':
    unittest.main()
