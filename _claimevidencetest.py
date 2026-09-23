import copy
import unittest
import miami_claim_evidence as E


class ClaimEvidenceTest(unittest.TestCase):
    def fixture(self, classification=None):
        reports = [{'searched':[{'name':'SYNTHETIC TITLE PARTY'}],
                    'potential_title_party_claims':[], 'gaps':[]}]
        raw = {'SYNTHETIC TITLE PARTY':[{'reC_BOOK':'900','reC_PAGE':'10',
                'doC_TYPE':'DADE COURT PAPER - DCP','reC_DATE':'01/01/2099'}]}
        docs = [{'source_ref':'recorded:synthetic',
                 'stored':{'record_key':{'book':'900','page':'10'},'source_sha256':'synthetic-hash'},
                 'classification':classification or {'kind':'final_judgment'},
                 'reading':{'pages':[{'page':2,'text':'TOTAL $100.00','outcome':'ocr_text'}]},
                 'amount_candidates':[]}]
        return reports, raw, docs

    def test_dcp_body_judgment_retained_with_unknown_amount(self):
        reports, raw, docs = self.fixture()
        before = copy.deepcopy(reports)
        claim = E.enrich_claims(reports, raw, docs)[0]['potential_title_party_claims'][0]
        self.assertEqual(claim['book'], '900')
        self.assertIsNone(claim['amount'])
        self.assertEqual(claim['attachment_status'], 'unknown')
        self.assertEqual(claim['document_hash'], 'synthetic-hash')
        self.assertEqual(reports, before)

    def test_other_action_is_retained_as_potential_only(self):
        args = self.fixture({'kind':'other_action','text_kind':'final_judgment'})
        self.assertEqual(len(E.enrich_claims(*args)[0]['potential_title_party_claims']), 1)

    def test_missing_body_is_named_gap_not_judgment(self):
        reports, raw, _ = self.fixture()
        out = E.enrich_claims(reports, raw, [])[0]
        self.assertEqual(out['potential_title_party_claims'], [])
        self.assertTrue(any('900/10' in g['reason'] for g in out['gaps']))

    def test_satisfaction_is_not_a_new_judgment(self):
        args = self.fixture({'kind':'satisfaction_of_judgment'})
        self.assertEqual(E.enrich_claims(*args)[0]['potential_title_party_claims'], [])

    def test_repeated_enrichment_does_not_duplicate_claim(self):
        reports, raw, docs = self.fixture()
        first = E.enrich_claims(reports, raw, docs)
        self.assertEqual(len(E.enrich_claims(first, raw, docs)[0]['potential_title_party_claims']), 1)

    def test_saved_unknown_amount_is_replaced_by_new_corroborated_evidence(self):
        reports, raw, docs = self.fixture()
        first = E.enrich_claims(reports, raw, docs)
        docs[0]['amount_candidates'] = [{'amount':100,'sum_check':True,'page':2}]
        result = E.enrich_claims(first, raw, docs)[0]['potential_title_party_claims']
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['amount'], 100)

    def test_only_corroborated_amount_is_kept_with_page_evidence(self):
        reports, raw, docs = self.fixture()
        docs[0]['amount_candidates'] = [
            {'amount':900,'sum_check':False,'page':1},
            {'amount':100,'sum_check':True,'page':2,'line':'TOTAL $100.00'}]
        claim = E.enrich_claims(reports, raw, docs)[0]['potential_title_party_claims'][0]
        self.assertEqual(claim['amount'], 100)
        self.assertEqual(claim['amount_evidence']['page'], 2)
        self.assertEqual(claim['amount_evidence']['line'], 'TOTAL $100.00')


class SavedVisionTest(unittest.TestCase):
    def detail(self):
        return {'source_ref':'recorded:synthetic','errors':[], 'pages':{'1':{'unreadable':False}},
                'figures':[{'page':1,'id':i,'kind':k,'amount':a,'confident':True,'label':i}
                           for i,k,a in [('a','charge',70),('b','charge',30),('r','rate',5),('t','total',100)]],
                'grand_totals':[{'page':1,'amount':100}]}

    def test_saved_typed_charges_are_rechecked_free(self):
        result = E.saved_vision_candidates(self.detail(), 'recorded:synthetic')
        self.assertEqual(result[0]['amount'], 100)
        self.assertEqual(result[0]['sum_check_components'], [70,30])
        self.assertFalse(result[0]['verified'])

    def test_wrong_source_rejected(self):
        self.assertEqual(E.saved_vision_candidates(self.detail(), 'recorded:other'), [])

    def test_bad_subtotal_on_other_page_rejects_total(self):
        data = self.detail()
        data['figures'].append({'page':2,'id':'s','kind':'subtotal','amount':12,
                                'confident':True,'item_ids':['missing']})
        self.assertEqual(E.saved_vision_candidates(data, 'recorded:synthetic'), [])

    def test_unreadable_and_errors_reject(self):
        for field, value in [('errors',['failed']), ('pages',{'1':{'unreadable':True}})]:
            data = self.detail()
            data[field] = value
            self.assertEqual(E.saved_vision_candidates(data, 'recorded:synthetic'), [])


if __name__ == '__main__':
    unittest.main()
