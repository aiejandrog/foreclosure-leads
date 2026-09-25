"""Deed-to-parcel matching by the clerk index's own legal description. Synthetic names; $0.

A Miami deed indexed without a folio used to wait for a person to compare its legal description
with the parcel's. The index row already carries that legal in fields (subdivision, the free legal
field, block, plat book/page), and the records filed under the parcel's folio carry the parcel's.
These tests pin that only an exact lot/block/plat or unit/building/phase agreement places a deed,
that only a positive disagreement rules one out, and that everything else still goes to a person.
"""
import unittest

import miami_present_title as MPT
import miami_title_parties as T

FOLIO = '3012345678901'


def rec(book, date, grantor, grantee, folio='', doc_type='WARRANTY DEED', sub='SAMPLE GROVE',
        legal='LOT 14', block='12', plat='53/900'):
    return dict(reC_BOOK=book, reC_PAGE='1', reC_DATE=date, doC_TYPE=doc_type, firsT_PARTY=grantor,
                seconD_PARTY=grantee, foliO_NUMBER=folio, subdiV_NAME=sub, legaL_DESCRIPTION=legal,
                blocK_NO=block, plaT_BOOKPAGE=plat)


def mortgage(folio=FOLIO, **kw):
    return rec('5', '3/1/2019', 'OWNER PERSON', 'SAMPLE BANK', folio, 'MORTGAGE', **kw)


def title(rows):
    return T.build_title_parties(rows, [], {'parties': [
        {'partyName': 'OWNER PERSON', 'partyTypeDesc': 'DEFENDANT'}]}, FOLIO)


class IndexLegalParseTests(unittest.TestCase):
    def test_shapes_seen_in_the_miami_index(self):
        L = T.index_legal
        self.assertEqual(L(rec('1', '', '', '', legal='LOTS 38 THRU 40 & LOTS 1 THRU 3'))['lots'],
                         {'38', '39', '40', '1', '2', '3'})
        self.assertEqual(L(rec('1', '', '', '', legal='LOT 24 A SEE DOC'))['see_document'], True)
        self.assertEqual(L(rec('1', '', '', '', legal='LOT 3A', block='22A'))['block'], '22A')
        self.assertEqual(L(rec('1', '', '', '', legal='LOTS 14 & 15'))['lots'], {'14', '15'})
        unit = L(rec('1', '', '', '', legal='CONDO UNIT NO 104 BLDG 7', block='', plat='11542/2022'))
        self.assertEqual((unit['unit'], unit['building'], unit['lots']), ('104', '7', None))
        self.assertEqual(L(rec('1', '', '', '', legal='CONDO UNIT M 322 PH II', block=''))['phase'], 'II')
        self.assertEqual(L(rec('1', '', '', '', legal='CONDO UNIT PH 07', block=''))['unit'], 'PH 07')
        self.assertEqual(L(rec('1', '', '', '', legal='CONDOMINIUM PARCEL NO 3101 PHASE 1', block=''))['unit'], '3101')
        self.assertEqual(L(rec('1', '', '', '', legal='TR 6', block=''))['tract'], '6')
        self.assertIsNone(L(rec('1', '', '', '', legal='W2 OF W281FT', block='', plat='0/0'))['plat'])
        self.assertTrue(L(rec('1', '', '', '', legal='W2 OF W281FT', block='', plat='0/0'))['unparsed'])
        self.assertIsNone(L(rec('1', '', '', '', sub='', legal='', block='', plat='')))

    def test_a_subdivision_named_unit_is_not_a_condo_unit(self):
        got = T.index_legal(rec('1', '', '', '', legal='UNIT THREE LOT 9', block='21'))
        self.assertEqual((got['lots'], got['unit']), ({'9'}, None))

    def test_a_part_of_a_lot_is_never_read_as_the_lot(self):
        for legal in ('LOT 9 LESS W 10FT', 'LOT 24 A'):
            self.assertTrue(T.index_legal(rec('1', '', '', '', legal=legal))['unparsed'], legal)
        self.assertEqual(T.index_legal(rec('1', '', '', '', legal='LOT B'))['lots'], {'B'})
        self.assertEqual(T.index_legal(rec('1', '', '', '', legal='LOT 3A'))['lots'], {'3A'})


class DeedPlacementTests(unittest.TestCase):
    def test_later_deed_on_the_same_lot_block_plat_becomes_the_current_deed(self):
        rows = [mortgage(), rec('2', '1/1/2018', 'SELLER', 'OWNER PERSON', FOLIO),
                rec('7', '6/1/2023', 'OWNER PERSON', 'BUYER LLC')]
        got = title(rows)
        self.assertEqual(got['current_deed_candidate']['book_page'], '7/1')
        self.assertEqual(got['current_deed_candidate']['anchored_by'], 'legal_description')
        self.assertEqual(got['current_deed_candidate']['legal_match']['reference_record'], '5/1')
        self.assertEqual(got['current_deed_status'], 'candidate')
        self.assertEqual(got['legal_matched_deeds'], ['7/1'])
        self.assertEqual(got['unanchored_deeds'], [])
        self.assertEqual(got['chain_of_title'][-1]['link'], 'continuous')
        self.assertTrue(any('not by folio' in g for g in got['gaps']))

    def test_the_owners_deed_of_another_lot_is_kept_as_another_parcel(self):
        rows = [mortgage(), rec('2', '1/1/2018', 'SELLER', 'OWNER PERSON', FOLIO),
                rec('7', '6/1/2023', 'OWNER PERSON', 'BUYER LLC', legal='LOT 15')]
        got = title(rows)
        kept = got['unanchored_deeds'][0]
        self.assertEqual(kept['status'], 'legal_description_differs')
        self.assertEqual(got['current_deed_candidate']['book_page'], '2/1')
        self.assertEqual(got['possible_later_conveyances'], [])

    def test_another_block_or_plat_differs(self):
        for kw in ({'block': '13'}, {'plat': '53/910'}):
            got = title([mortgage(), rec('7', '6/1/2023', 'A', 'B', **kw)])
            self.assertEqual(got['unanchored_deeds'][0]['status'], 'legal_description_differs', kw)

    def test_see_doc_overlap_and_one_sided_block_stay_with_a_person(self):
        cases = ({'legal': 'LOT 14 SEE DOC'}, {'legal': 'LOTS 14 & 15'}, {'block': ''})
        for kw in cases:
            got = title([mortgage(), rec('2', '1/1/2018', 'SELLER', 'OWNER PERSON', FOLIO),
                         rec('7', '6/1/2023', 'OWNER PERSON', 'BUYER LLC', **kw)])
            kept = got['unanchored_deeds'][0]
            self.assertEqual(kept['status'], 'legal_description_match_required', kw)
            self.assertEqual(kept['legal_match']['verdict'], 'needs_person', kw)
            self.assertEqual(got['possible_later_conveyances'], ['7/1'], kw)

    def test_no_plat_falls_back_to_the_exact_subdivision_name_only(self):
        same = title([mortgage(plat=''), rec('7', '6/1/2023', 'A', 'B', plat='')])
        self.assertEqual(same['legal_matched_deeds'], ['7/1'])
        other = title([mortgage(plat=''), rec('7', '6/1/2023', 'A', 'B', plat='', sub='SAMPLE GROVE 2ND ADDN')])
        self.assertEqual(other['unanchored_deeds'][0]['status'], 'legal_description_match_required')

    def test_a_plat_on_only_one_side_is_never_settled_by_the_subdivision_name(self):
        for ref_plat, deed_plat in (('', '99/1'), ('53/900', '')):
            got = title([mortgage(plat=ref_plat), rec('7', '6/1/2023', 'A', 'B', plat=deed_plat)])
            self.assertEqual(got['legal_matched_deeds'], [], (ref_plat, deed_plat))
            self.assertEqual(got['unanchored_deeds'][0]['legal_match']['reason'],
                             'only one side names a plat book/page')

    def test_leading_zeros_are_not_a_difference(self):
        rows = [mortgage(), rec('2', '1/1/2018', 'SELLER', 'OWNER PERSON', FOLIO),
                rec('7', '6/1/2023', 'OWNER PERSON', 'BUYER LLC', block='012', plat='053/0900')]
        self.assertEqual(title(rows)['legal_matched_deeds'], ['7/1'])

    def test_block_and_lot_spellings_the_index_also_uses(self):
        got = T.index_legal(rec('1', '', '', '', legal='LOTS 1 AND 2 BLOCK 12', block=''))
        self.assertEqual((got['lots'], got['block']), ({'1', '2'}, '12'))

    def test_condo_unit_building_and_phase(self):
        unit = dict(sub='SAMPLE TOWERS CONDO', legal='CONDO UNIT NO 104 BLDG 7', block='', plat='11542/2022')
        self.assertEqual(title([mortgage(**unit), rec('7', '6/1/2023', 'A', 'B', **unit)])['legal_matched_deeds'], ['7/1'])
        for legal, status in (('CONDO UNIT NO 105 BLDG 7', 'legal_description_differs'),
                              ('CONDO UNIT NO 104 BLDG 8', 'legal_description_differs'),
                              ('CONDO UNIT NO 104', 'legal_description_match_required'),
                              ('CONDO UNIT NO 10 4 BLDG 7', 'legal_description_match_required')):
            got = title([mortgage(**unit), rec('7', '6/1/2023', 'A', 'B', **dict(unit, legal=legal))])
            self.assertEqual(got['unanchored_deeds'][0]['status'], status, legal)

    def test_disagreeing_folio_records_give_no_yardstick(self):
        rows = [mortgage(), rec('6', '1/1/2020', 'X', 'Y', FOLIO, 'MORTGAGE', legal='LOT 15'),
                rec('7', '6/1/2023', 'A', 'B')]
        kept = title(rows)['unanchored_deeds'][0]
        self.assertEqual(kept['status'], 'legal_description_match_required')
        self.assertIn('different index legal descriptions', kept['legal_match']['reason'])

    def test_another_parcels_folio_is_never_overridden_by_a_matching_legal(self):
        got = title([mortgage(), rec('7', '6/1/2023', 'A', 'B', '9999999999999')])
        self.assertEqual(got['unanchored_deeds'][0]['status'], 'folio_conflict')
        self.assertEqual(got['legal_matched_deeds'], [])

    def test_records_under_other_folios_never_become_the_yardstick(self):
        rows = [mortgage(folio='9999999999999'), rec('7', '6/1/2023', 'A', 'B')]
        kept = title(rows)['unanchored_deeds'][0]
        self.assertEqual(kept['legal_match']['verdict'], 'needs_person')


class PresentTitleTests(unittest.TestCase):
    def test_present_title_names_how_the_deed_was_placed(self):
        rows = [mortgage(), rec('2', '1/1/2018', 'SELLER', 'OWNER PERSON', FOLIO),
                rec('7', '6/1/2023', 'OWNER PERSON', 'BUYER LLC'),
                rec('8', '6/1/2024', 'OTHER', 'SOMEONE', legal='LOT 2')]
        got = MPT.present_title({'title_parties': title(rows), 'owner': 'OWNER PERSON'})
        own = got['ownership']
        self.assertEqual((own['deed'], own['anchored_by']), ('7/1', 'legal_description'))
        self.assertEqual(own['legal_description_matched'], ['7/1'])
        self.assertEqual(own['legal_description_differs'], ['8/1'])
        self.assertEqual(own['legal_description_match_required'], [])
        self.assertFalse(any('legal-description matching' in h for h in got['held_because']))
        self.assertIn('clerk index legal description', own['basis'])
        self.assertTrue(any('every deed party was recovered' in g and 'official_records/7-1' in g
                            for g in title(rows)['gaps']))


if __name__ == '__main__':
    unittest.main()
