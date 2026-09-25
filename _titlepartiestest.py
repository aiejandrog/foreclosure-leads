"""Synthetic title-party evidence tests; no network or private data."""
import unittest
import miami_title_parties as T


def deed(book, date, grantor, grantee, folio='1234567890123'):
    return dict(reC_BOOK=book, reC_PAGE='1', reC_DATE=date, doC_TYPE='WARRANTY DEED',
                firsT_PARTY=grantor, seconD_PARTY=grantee, foliO_NUMBER=folio,
                subdiV_NAME='TEST CONDOMINIUM')


class TitlePartiesTests(unittest.TestCase):
    def test_inverted_person_name_matches_docket_without_changing_display(self):
        got = T.build_title_parties([deed('3','1/1/2024','OLD LLC','Example Middle Person')], [],
            {'parties':[{'partyName':'PERSON, EXAMPLE MIDDLE','partyTypeDesc':'DEFENDANT'}]}, '1234567890123')
        self.assertEqual(got['owners_not_named'], [])
        self.assertEqual(got['defendants_not_on_title'], [])
        self.assertEqual(got['title_parties'][1]['name'], 'Example Middle Person')

    def test_entity_commas_not_reordered_and_suffix_preserved(self):
        self.assertNotEqual(T.name_key('ALPHA, BETA LLC'), T.name_key('BETA LLC ALPHA'))
        self.assertNotEqual(T.name_key('ALPHA, BETA TRUST'), T.name_key('BETA TRUST ALPHA'))
        self.assertEqual(T.name_key('PERSON, EXAMPLE JR'), T.name_key('EXAMPLE PERSON JR'))
        self.assertNotEqual(T.name_key('PERSON, EXAMPLE JR'), T.name_key('EXAMPLE PERSON SR'))
        self.assertEqual(T.name_key('EXAMPLE PERSON, JR.'), T.name_key('EXAMPLE PERSON JR'))
    def test_statutory_deed_identification_and_qualified_parties(self):
        row = deed('3','1/1/2024 12:00:00 AM','','','0')
        doc = {'source_ref':'official_records/3-1','stored':{'source_sha256':'abc'},'reading':{'pages':[
            {'page':1,'outcome':'ocr_text','text':
             'Parcel Identification No 12-3456-789-0123\nThis indenture between Example Services Corp Inc., a Florida Corporation, '
             'whose post office address is 123 Example Street, of the County of Florida Grantor,\n'
             'to Example James Person, a single man, post office address is 456 Sample Road, of the County of Miami-Dade, Florida, Grantee:'}]}}
        got = T.build_title_parties([row], [doc], {}, '1234567890123')
        self.assertEqual([p['name'] for p in got['title_parties']], ['Example Services Corp Inc.', 'Example James Person'])
        self.assertEqual(got['current_deed_candidate']['book_page'], '3/1')

    def test_property_estate_boilerplate_is_not_death_flag(self):
        doc = {'source_ref':'x','reading':{'pages':[{'page':1,'text':
            'The claim or estate of Defendant is inferior to Plaintiff.\nEstate of Example Person, deceased.'}]}}
        got = T.build_title_parties([], [doc], {}, '1234567890123')
        self.assertEqual(len(got['identity_flags']), 1)
        self.assertEqual(got['identity_flags'][0]['passage'], 'Estate of Example Person, deceased.')
    def test_ordinary_between_clause_names_have_explicit_role_evidence(self):
        row = deed('3','1/1/2024','','')
        doc = {'source_ref':'official_records/3-1','stored':{'source_sha256':'abc'},'reading':{'pages':[
            {'page':1,'outcome':'ocr_text','text':'This Warranty Deed between OLD TRUST, hereinafter called the grantor, and NEW LLC, hereinafter called the grantee.'}]}}
        got = T.build_title_parties([row], [doc], {}, '1234567890123')
        self.assertEqual([p['name'] for p in got['title_parties']], ['OLD TRUST','NEW LLC'])
    def test_recorded_cfn_reference_matches_actual_stored_document(self):
        row = deed('3','1/1/2024','','')
        row['cfN_MASTER_ID'] = '12345'
        doc = {'source_ref':'recorded:12345','stored':{'source_sha256':'hash'},'reading':{'pages':[
            {'page':1,'outcome':'ocr_text','text':'Grantor: OLD LLC\nGrantee: NEW LLC'}]}}
        got = T.build_title_parties([row], [doc], {}, '1234567890123')
        self.assertEqual([p['name'] for p in got['title_parties']], ['OLD LLC','NEW LLC'])
    def test_current_and_preceding_deed_keep_entity_names_and_roles(self):
        rows = [deed('1','1/1/2010','OLD TRUST','BEFORE LLC'),
                deed('2','1/1/2020','BEFORE LLC','CURRENT TRUST'),
                deed('3','1/1/2024','CURRENT TRUST','NEW LLC')]
        got = T.build_title_parties(rows, [], {'parties': [
            {'partyName':'OTHER PERSON','partyTypeDesc':'DEFENDANT'},
            {'partyName':'NEW LLC','partyTypeDesc':'PLAINTIFF'}]}, '1234567890123')
        self.assertEqual(got['current_deed_candidate']['book_page'], '3/1')
        self.assertEqual(got['previous_deed_candidate']['book_page'], '2/1')
        self.assertEqual({p['name'] for p in got['title_parties']}, {'BEFORE LLC','CURRENT TRUST','NEW LLC'})
        self.assertEqual([p['name'] for p in got['owners_not_named']], ['NEW LLC'])
        self.assertEqual([p['name'] for p in got['defendants_not_on_title']], ['OTHER PERSON'])
        self.assertEqual(got['status'], 'unknown')

    def test_subdivision_alone_never_anchors_neighbor_deed(self):
        got = T.build_title_parties([deed('1','1/1/2020','A PERSON','B PERSON','')], [], {}, '1234567890123')
        self.assertIsNone(got['current_deed_candidate'])
        self.assertEqual(got['title_parties'], [])
        self.assertTrue(got['gaps'])

    def test_body_explicit_roles_have_page_hash_and_conditional_death_not_fact(self):
        doc = {'source_ref':'official_records/3-1', 'stored':{'source_sha256':'abc'},
               'reading':{'complete':True,'pages':[{'page':1,'outcome':'ocr_text',
               'text':'WARRANTY DEED\nFolio: 1234567890123\nGrantor: FIRST PERSON\nGrantee: NEW LLC; SECOND PERSON\nIf deceased, heirs of FIRST PERSON'}]}}
        got = T.build_title_parties([deed('3','1/1/2024','','')], [doc], {}, '1234567890123')
        self.assertEqual({p['name'] for p in got['title_parties']}, {'FIRST PERSON','NEW LLC','SECOND PERSON'})
        owner = next(p for p in got['title_parties'] if p['name']=='SECOND PERSON')
        self.assertEqual(owner['evidence']['page'], 1)
        self.assertEqual(owner['evidence']['document_hash'], 'abc')
        self.assertTrue(got['identity_flags'][0]['conditional'])
        self.assertFalse(got['identity_flags'][0]['death_established'])

    def test_missing_date_prevents_false_current_owner_comparison(self):
        got = T.build_title_parties([deed('3','','A PERSON','B PERSON')], [], {}, '1234567890123')
        self.assertIsNone(got['current_deed_candidate'])
        self.assertEqual(got['owners_not_named'], [])
        self.assertIn('B PERSON', [p['name'] for p in got['search_names']])

    def test_explicit_wrong_folio_not_overridden_by_body_reference(self):
        row = deed('3','1/1/2024','A PERSON','B PERSON','9999999999999')
        doc = {'source_ref':'official_records/3-1','reading':{'pages':[
            {'page':1,'text':'Folio: 1234567890123','outcome':'text'}]}}
        got = T.build_title_parties([row], [doc], {}, '1234567890123')
        self.assertEqual(got['title_parties'], [])


if __name__ == '__main__':
    unittest.main()
