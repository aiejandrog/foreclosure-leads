#!/usr/bin/env python3
"""Fake-data tests for the Broward Official Records daily feed.

No real names, numbers, or feed rows. Case numbers are the fake CACE-26-000000 shape.
"""
import csv
import json
import os
import shutil
import tempfile
import unittest

import broward_or_feed as F
import fl_cadastral
import lis_pendens as LP

HERE = os.path.dirname(os.path.abspath(__file__))


def doc(inst, ymd, typ, case='', legal=''):
    fields = [''] * 20
    fields[0] = inst
    fields[1] = ymd
    fields[2] = '%s/%s/%s' % (ymd[4:6], ymd[6:8], ymd[0:4])
    fields[4] = typ
    fields[5] = '0.00'
    fields[8] = 'O'
    fields[9] = legal
    fields[15] = 'V'
    fields[18] = case
    return '|'.join(fields)


def nme(inst, name, side, seq):
    return '|'.join([inst, name, side, str(seq), ''])


def lnk(inst, typ, ref, ref_type='LP'):
    fields = [''] * 12
    fields[0] = inst
    fields[3] = 'O'
    fields[4] = typ
    fields[5] = ref
    fields[8] = 'O'
    fields[9] = ref_type
    if ref:
        fields[10] = '%s [%s]' % (ref, ref_type)
    else:
        fields[10] = '-NOT SHOWN'
    return '|'.join(fields)


def write_text(folder, filename, lines):
    os.makedirs(folder, exist_ok=True)
    text = '\r\n'.join(lines) + '\r\n'
    with open(os.path.join(folder, filename), 'wb') as fh:
        fh.write(text.encode('latin-1'))


def read_json(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def read_text(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def write_day(folder, stamp, docs, names, links=None):
    write_text(folder, stamp + 'doc-ver.txt', docs)
    write_text(folder, stamp + 'nme-ver.txt', names)
    if links is not None:
        write_text(folder, stamp + 'lnk-ver.txt', links)


def roll_of(rows):
    return F.StateRoll(rows)


class NormalizeTests(unittest.TestCase):
    def test_shapes(self):
        pairs = (
            ('CACE-26-000001', 'CACE-26-000001'),
            ('CACE- 26-000001', 'CACE-26-000001'),
            ('cace-26-1', 'CACE-26-000001'),
            ('FMCE26-000123', 'FMCE-26-000123'),
            ('COCE 26-000014', 'COCE-26-000014'),
            ('COCE 26 000014', 'COCE-26-000014'),
            ('COCE26000014', 'COCE-26-000014'),
            ('CACE-2026-000123', 'CACE-26-000123'),
            ('CACE2026000123', 'CACE-26-000123'),
            ('CACE/26/000009', 'CACE-26-000009'),
            ('CONO-26-12', 'CONO-26-000012'),
            ('-NOT SHOWN', ''),
            ('NOT SHOWN', ''),
            ('OTHER', ''),
            ('UNKNOWN', ''),
            ('', ''),
            ('CACE-26-000001 (CLOSED)', ''),
            ('CACE-26-0000001', ''),
        )
        for raw, want in pairs:
            self.assertEqual(F.normalize_case(raw), want, raw)

    def test_spaced_and_compact_are_the_same_case(self):
        self.assertEqual(F.normalize_case('CACE- 26-000007'), F.normalize_case('CACE26-7'))


class PartyTests(unittest.TestCase):
    def test_direct_is_plaintiff_and_sequence_order_wins(self):
        joined = F.join_parties([
            {'name': 'DOE, JOHN B', 'side': 'R', 'seq': 2, 'ord': 0},
            {'name': 'EXAMPLE MORTGAGE LLC', 'side': 'D', 'seq': 1, 'ord': 1},
            {'name': 'DOE, JANE A', 'side': 'R', 'seq': 1, 'ord': 2},
            {'name': 'SAMPLE HOMEOWNERS ASSOCIATION INC', 'side': 'R', 'seq': 3, 'ord': 3},
        ])
        self.assertFalse(joined['flipped'])
        self.assertEqual(joined['plaintiffs'], ['EXAMPLE MORTGAGE LLC'])
        self.assertEqual(joined['defendants'], [
            'DOE, JANE A', 'DOE, JOHN B', 'SAMPLE HOMEOWNERS ASSOCIATION INC'])

    def test_institution_on_the_reverse_side_only_is_flipped(self):
        joined = F.join_parties([
            {'name': 'DOE, JANE A', 'side': 'D', 'seq': 1, 'ord': 0},
            {'name': 'SAMPLE HOMEOWNERS ASSOCIATION INC', 'side': 'R', 'seq': 1, 'ord': 1},
        ])
        self.assertTrue(joined['flipped'])
        self.assertEqual(joined['plaintiffs'], ['SAMPLE HOMEOWNERS ASSOCIATION INC'])
        self.assertEqual(joined['defendants'], ['DOE, JANE A'])

    def test_association_as_a_defendant_is_not_a_flip(self):
        joined = F.join_parties([
            {'name': 'EXAMPLE MORTGAGE LLC', 'side': 'D', 'seq': 1, 'ord': 0},
            {'name': 'DOE, JANE A', 'side': 'R', 'seq': 1, 'ord': 1},
            {'name': 'SAMPLE HOMEOWNERS ASSOCIATION INC', 'side': 'R', 'seq': 2, 'ord': 2},
        ])
        self.assertFalse(joined['flipped'])
        self.assertEqual(joined['plaintiffs'], ['EXAMPLE MORTGAGE LLC'])

    def test_person_plaintiff_with_a_person_and_an_association_is_not_flipped(self):
        joined = F.join_parties([
            {'name': 'DOE, JANE A', 'side': 'D', 'seq': 1, 'ord': 0},
            {'name': 'ROE, RICHARD C', 'side': 'R', 'seq': 1, 'ord': 1},
            {'name': 'SAMPLE HOMEOWNERS ASSOCIATION INC', 'side': 'R', 'seq': 2, 'ord': 2},
        ])
        self.assertFalse(joined['flipped'])
        self.assertEqual(joined['defendants'][0], 'ROE, RICHARD C')


class ClassifyTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(F.classify('CACE-26-000001', ['EXAMPLE MORTGAGE LLC']), 'lender-foreclosure')
        self.assertEqual(F.classify('CACE-26-000002', ['SAMPLE CREDIT UNION']), 'lender-foreclosure')
        self.assertEqual(F.classify('CACE-26-000003', ['EXAMPLE 2021-1 TRUST']), 'lender-foreclosure')
        self.assertEqual(F.classify('COCE-26-000004', ['SAMPLE HOMEOWNERS ASSOCIATION INC']), 'hoa-condo')
        self.assertEqual(F.classify('CONO-26-000005', ['SAMPLE CONDOMINIUM ASSOCIATION INC']), 'hoa-condo')
        self.assertEqual(F.classify('FMCE-26-000006', ['EXAMPLE MORTGAGE LLC']), 'family')
        self.assertEqual(F.classify('FMCE-26-000007', ['DOE, JANE A']), 'family')
        self.assertEqual(F.classify('CACE-26-000008', ['BROWARD COUNTY']), 'government')
        self.assertEqual(F.classify('CACE-26-000009', ['CITY OF SAMPLE']), 'government')
        self.assertEqual(F.classify('CACE-26-000010', ['DOE, JOHN B']), 'other')
        self.assertEqual(F.classify('CACE-26-000011', ['DOE FAMILY TRUST']), 'other')
        self.assertEqual(F.classify('CACE-26-000012', ['COASTAL BUILDERS LLC']), 'other')
        self.assertEqual(F.classify('CACE-26-000013', ['US BANK NATIONAL ASSOCIATION']), 'lender-foreclosure')


class MatchTests(unittest.TestCase):
    def test_clean_unique_parcel_keeps_letters(self):
        roll = roll_of([{'parcel': '4942-00-AB-0010', 'owner': 'DOE JANE',
                         'address': '100 EXAMPLE ST', 'city': 'SAMPLE', 'zip': '33000'}])
        got = F.match_names(['DOE, JANE A'], roll)
        self.assertEqual(got['match'], 'clean')
        self.assertEqual(got['parcel'], '494200AB0010')
        self.assertEqual(got['address'], '100 EXAMPLE ST')
        self.assertEqual(got['candidates'], [])

    def test_two_parcels_are_not_a_guess(self):
        roll = roll_of([
            {'parcel': '514299010010', 'owner': 'DOE JANE', 'address': '100 EXAMPLE ST',
             'city': 'SAMPLE', 'zip': '33000'},
            {'parcel': '514299010020', 'owner': 'DOE JANE A', 'address': '200 EXAMPLE ST',
             'city': 'SAMPLE', 'zip': '33000'},
        ])
        got = F.match_names(['DOE, JANE A'], roll)
        self.assertEqual(got['match'], 'needs-review')
        self.assertEqual(got['match_note'], 'several-parcels')
        self.assertEqual(got['parcel'], '')
        self.assertEqual(got['address'], '')
        self.assertEqual(got['roll_owner'], '')
        self.assertEqual(got['candidate_count'], 2)
        self.assertEqual({c['parcel'] for c in got['candidates']}, {'514299010010', '514299010020'})

    def test_different_given_name_is_not_a_candidate(self):
        roll = roll_of([{'parcel': '514299010010', 'owner': 'DOE ROBERT',
                         'address': '100 EXAMPLE ST', 'city': 'SAMPLE', 'zip': '33000'}])
        got = F.match_names(['DOE, JANE A'], roll)
        self.assertEqual(got['match'], 'none')
        self.assertEqual(got['match_note'], 'no-name-hit')
        self.assertEqual(got['candidates'], [])

    def test_one_middle_name_is_clean_and_two_differences_are_not(self):
        one = roll_of([{'parcel': '514299010010', 'owner': 'DOE JANE ANN',
                        'address': '100 EXAMPLE ST', 'city': 'SAMPLE', 'zip': '33000'}])
        self.assertEqual(F.match_names(['DOE, JANE A'], one)['match'], 'clean')
        clash = roll_of([{'parcel': '514299010010', 'owner': 'DOE JANE MARIE',
                          'address': '100 EXAMPLE ST', 'city': 'SAMPLE', 'zip': '33000'}])
        got = F.match_names(['DOE, JANE ANN'], clash)
        self.assertEqual(got['match'], 'needs-review')
        self.assertEqual(got['match_note'], 'weak-name')
        self.assertEqual(got['parcel'], '')

    def test_two_extra_roll_tokens_are_not_assigned(self):
        roll = roll_of([{'parcel': '514299010010', 'owner': 'DOE JANE ANN MARIE',
                         'address': '100 EXAMPLE ST', 'city': 'SAMPLE', 'zip': '33000'}])
        got = F.match_names(['DOE, JANE A'], roll)
        self.assertEqual(got['match'], 'needs-review')
        self.assertEqual(got['parcel'], '')
        self.assertEqual(got['address'], '')

    def test_couple_on_one_deed_is_one_parcel(self):
        roll = roll_of([{'parcel': '514299010010', 'owner': 'DOE JANE & DOE JOHN',
                         'address': '100 EXAMPLE ST', 'city': 'SAMPLE', 'zip': '33000'}])
        got = F.match_names(['DOE, JANE A', 'DOE, JOHN B'], roll)
        self.assertEqual(got['match'], 'clean')
        self.assertEqual(got['parcel'], '514299010010')

    def test_two_defendants_on_two_parcels_are_not_a_guess(self):
        roll = roll_of([
            {'parcel': '514299010010', 'owner': 'DOE JANE', 'address': '100 EXAMPLE ST',
             'city': 'SAMPLE', 'zip': '33000'},
            {'parcel': '514299010020', 'owner': 'DOE JOHN', 'address': '200 EXAMPLE ST',
             'city': 'SAMPLE', 'zip': '33000'},
        ])
        got = F.match_names(['DOE, JANE A', 'DOE, JOHN B'], roll)
        self.assertEqual(got['match'], 'needs-review')
        self.assertEqual(got['parcel'], '')
        self.assertEqual(got['address'], '')

    def test_suffix_and_no_roll_and_no_person(self):
        roll = roll_of([{'parcel': '514299010010', 'owner': 'DOE JANE',
                         'address': '100 EXAMPLE ST', 'city': 'SAMPLE', 'zip': '33000'}])
        self.assertEqual(F.match_names(['DOE, JANE TR'], roll)['match'], 'clean')
        self.assertEqual(F.match_names(['DOE, JANE LIFE ESTATE'], roll)['match'], 'clean')
        self.assertEqual(F.match_names(['DOE, JANE A'], None)['match_note'], 'no-roll')
        self.assertEqual(F.match_names(['SAMPLE HOMEOWNERS ASSOCIATION INC'], roll)['match_note'], 'no-person')
        self.assertEqual(F.match_names(['JANE DOE'], roll)['match_note'], 'no-person')

    def test_candidate_list_is_capped_and_still_not_chosen(self):
        rows = [{'parcel': '51429901%04d' % i, 'owner': 'DOE JANE', 'address': '100 EXAMPLE ST',
                 'city': 'SAMPLE', 'zip': '33000'} for i in range(9)]
        got = F.match_names(['DOE, JANE A'], roll_of(rows))
        self.assertEqual(got['match'], 'needs-review')
        self.assertEqual(got['parcel'], '')
        self.assertEqual(got['candidate_count'], 9)
        self.assertEqual(len(got['candidates']), 8)


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='broward-or-')
        self.feed = os.path.join(self.tmp, 'feed')
        self.out = os.path.join(self.tmp, 'out')
        os.makedirs(self.feed)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _roll_csv(self, rows):
        path = os.path.join(self.tmp, 'roll.csv')
        with open(path, 'w', encoding='latin-1', newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=[
                'PARCEL_ID', 'OWN_NAME', 'PHY_ADDR1', 'PHY_ADDR2', 'PHY_CITY', 'PHY_ZIPCD', 'S_LEGAL'])
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return path

    def test_latin1_and_ignored_files_do_not_shift_columns(self):
        legal = 'LOT 1 ' + '\u00f8'
        write_day(self.feed, '10-02-2026', [
            doc('120000001', '20261002', 'LP', 'CACE-26-000001', legal),
            'TOO|SHORT',
            doc('120000002', '20261002', 'D', 'CACE-26-000099', legal),
        ], [
            nme('120000001', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000001', 'DOE, JANE A', 'R', 1),
        ])
        write_text(self.feed, '10-02-2026doc-ver-rng.txt', ['not a document'])
        write_text(self.feed, '10-02-2026lgl-ver.txt', ['120000002|' + legal + '|514299010010|'])
        rows = F.read_pipe(os.path.join(self.feed, '10-02-2026doc-ver.txt'))
        self.assertIn('\u00f8', rows[0][9])
        self.assertEqual(rows[0][18], 'CACE-26-000001')
        leads, stats = F.build(self.feed, None)
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]['case'], 'CACE-26-000001')
        self.assertEqual(leads[0]['kind'], 'lender-foreclosure')
        self.assertGreaterEqual(stats['bad_rows'], 1)
        self.assertEqual(stats['files'], 2)

    def test_out_of_order_days_use_the_record_date_and_still_close(self):
        # 10-14 is written first and carries the earlier lis pendens. 10-01 carries the later judgment.
        write_day(self.feed, '10-14-2026', [
            doc('120000010', '20261002', 'LP', 'CACE- 26-000010'),
            doc('120000011', '20261003', 'LP', 'CACE-26-000011'),
        ], [
            nme('120000010', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000010', 'DOE, JANE A', 'R', 1),
            nme('120000011', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000011', 'ROE, RICHARD C', 'R', 1),
        ])
        write_day(self.feed, '10-01-2026', [
            doc('120000090', '20261009', 'FJ', 'CACE26-10'),
            doc('120000091', '20261009', 'D', ''),
        ], [
            nme('120000090', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000090', 'DOE, JANE A', 'R', 1),
        ])
        leads, stats = F.build(self.feed, None)
        cases = {lead['case'] for lead in leads}
        self.assertNotIn('CACE-26-000010', cases)
        self.assertIn('CACE-26-000011', cases)
        kept = leads[0]
        self.assertEqual(kept['recorded'], '2026-10-03')
        self.assertEqual(stats['dropped_judgment'], 1)
        self.assertEqual(stats['recording_dates'], ['2026-10-02', '2026-10-03', '2026-10-09'])

    def test_release_by_case_number_and_by_instrument_and_not_by_display_text(self):
        write_day(self.feed, '10-06-2026', [
            doc('120000020', '20261006', 'LP', 'CACE-26-000020'),
            doc('120000021', '20261006', 'LP', 'CACE-26-000021'),
            doc('120000022', '20261006', 'LP', 'CACE-26-000022'),
            doc('120000023', '20261006', 'LP', '-NOT SHOWN'),
            doc('120000080', '20261006', 'RST', 'cace-26-20'),
            doc('120000081', '20261006', 'RST', '-NOT SHOWN'),
            doc('120000082', '20261006', 'RST', ''),
            doc('120000083', '20261006', 'FJX', 'CACE-26-000022'),
            doc('120000084', '20261006', 'CFJ', 'CACE-26-000030'),
            doc('120000030', '20261006', 'LP', 'CACE-26-000030'),
        ], [
            nme('120000020', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000020', 'DOE, JANE A', 'R', 1),
            nme('120000021', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000021', 'DOE, JOHN B', 'R', 1),
            nme('120000022', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000022', 'DOE, JANE A', 'R', 1),
            nme('120000023', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000023', 'DOE, JANE A', 'R', 1),
            nme('120000030', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000030', 'DOE, JANE A', 'R', 1),
        ], [
            lnk('120000080', 'RST', '', 'LP'),
            lnk('120000081', 'RST', '120000021', 'LP'),
            lnk('120000082', 'RST', '119000099', 'M'),
            '|'.join(['120000082', '', '', 'O', 'RST', '', '', '', 'O', 'LP', '120000022 [LP]', '']),
        ])
        leads, stats = F.build(self.feed, None)
        cases = {lead['case']: lead for lead in leads}
        self.assertNotIn('CACE-26-000020', cases)
        self.assertNotIn('CACE-26-000021', cases)
        self.assertNotIn('CACE-26-000030', cases)
        self.assertIn('CACE-26-000022', cases)
        self.assertEqual(cases['CACE-26-000022']['instrument'], '120000022')
        bare = [lead for lead in leads if lead['instrument'] == '120000023']
        self.assertEqual(len(bare), 1)
        self.assertEqual(bare[0]['case'], '')
        self.assertEqual(stats['dropped_judgment'], 1)
        self.assertEqual(stats['dropped_release'], 2)

    def test_amended_lis_pendens_keeps_the_later_instrument(self):
        write_day(self.feed, '10-01-2026', [
            doc('120000040', '20261001', 'LP', 'CACE-26-000040'),
        ], [
            nme('120000040', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000040', 'DOE, JANE A', 'R', 1),
        ])
        write_day(self.feed, '10-08-2026', [
            doc('120000041', '20261008', 'LP', 'CACE-26-000040'),
        ], [
            nme('120000041', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000041', 'DOE, JANE A', 'R', 2),
            nme('120000041', 'DOE, JOHN B', 'R', 1),
        ])
        leads, _stats = F.build(self.feed, None)
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]['instrument'], '120000041')
        self.assertEqual(leads[0]['instruments'], ['120000041', '120000040'])
        self.assertEqual(leads[0]['defendants'], ['DOE, JOHN B', 'DOE, JANE A'])

    def test_releasing_one_instrument_keeps_the_other(self):
        write_day(self.feed, '10-07-2026', [
            doc('120000050', '20261001', 'LP', 'CACE-26-000050'),
            doc('120000051', '20261007', 'LP', 'CACE-26-000050'),
            doc('120000052', '20261007', 'RST', '-NOT SHOWN'),
        ], [
            nme('120000050', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000050', 'DOE, JANE A', 'R', 1),
            nme('120000051', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000051', 'DOE, JANE A', 'R', 1),
        ], [
            lnk('120000052', 'RST', '120000050', 'LP'),
        ])
        leads, stats = F.build(self.feed, None)
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]['instruments'], ['120000051'])
        self.assertEqual(stats['dropped_release'], 0)

    def test_full_build_matches_the_roll_and_does_not_use_the_plaintiff(self):
        write_day(self.feed, '10-05-2026', [
            doc('120000060', '20261005', 'LP', 'CACE-26-000060'),
            doc('120000061', '20261005', 'LP', 'CACE-26-000061'),
            doc('120000062', '20261005', 'LP', 'FMCE-26-000062'),
            doc('120000063', '20261005', 'LP', 'CACE-26-000063'),
        ], [
            nme('120000060', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000060', 'DOE, JANE A', 'r', 1),
            nme('120000061', 'DOE, JANE A', 'D', 1),
            nme('120000061', 'SAMPLE HOMEOWNERS ASSOCIATION INC', 'R', 1),
            nme('120000062', 'DOE, JANE A', 'D', 1),
            nme('120000062', 'DOE, JOHN B', 'R', 1),
            nme('120000063', 'DOE, JANE A', 'D', 1),
            nme('120000063', 'ROE, RICHARD C', 'R', 1),
        ])
        path = self._roll_csv([
            {'PARCEL_ID': '514299010010', 'OWN_NAME': 'DOE JANE A',
             'PHY_ADDR1': '100 EXAMPLE ST, UNIT 2', 'PHY_ADDR2': '',
             'PHY_CITY': 'SAMPLE', 'PHY_ZIPCD': '33000', 'S_LEGAL': 'LOT ' + '\u00f8'},
            {'PARCEL_ID': '514299010020', 'OWN_NAME': 'DOE JANE A',
             'PHY_ADDR1': '200 EXAMPLE ST', 'PHY_ADDR2': '',
             'PHY_CITY': 'SAMPLE', 'PHY_ZIPCD': '33000', 'S_LEGAL': ''},
            {'PARCEL_ID': '514299010030', 'OWN_NAME': 'ROE RICHARD C',
             'PHY_ADDR1': '300 EXAMPLE ST', 'PHY_ADDR2': '',
             'PHY_CITY': 'SAMPLE', 'PHY_ZIPCD': '33000', 'S_LEGAL': ''},
            {'PARCEL_ID': '514299019999', 'OWN_NAME': 'MUN' + '\u00f8' + 'Z, CARL',
             'PHY_ADDR1': '9 NOWHERE', 'PHY_ADDR2': '',
             'PHY_CITY': 'SAMPLE', 'PHY_ZIPCD': '33000', 'S_LEGAL': ''},
        ])
        roll = F.load_state_roll(path)
        self.assertEqual(len(roll.rows), 4)
        self.assertEqual(roll.rows[0]['address'], '100 EXAMPLE ST, UNIT 2')
        leads, stats = F.build(self.feed, roll)
        by_case = {lead['case']: lead for lead in leads}
        jane = by_case['CACE-26-000060']
        self.assertEqual(jane['kind'], 'lender-foreclosure')
        self.assertFalse(jane['sides_flipped'])
        self.assertEqual(jane['match'], 'needs-review')
        self.assertEqual(jane['parcel'], '')
        self.assertEqual(jane['address'], '')
        flipped = by_case['CACE-26-000061']
        self.assertTrue(flipped['sides_flipped'])
        self.assertEqual(flipped['kind'], 'hoa-condo')
        self.assertEqual(flipped['defendants'], ['DOE, JANE A'])
        self.assertEqual(by_case['FMCE-26-000062']['kind'], 'family')
        quiet = by_case['CACE-26-000063']
        self.assertEqual(quiet['kind'], 'other')
        self.assertEqual(quiet['match'], 'clean')
        self.assertEqual(quiet['parcel'], '514299010030')
        self.assertNotEqual(quiet['parcel'], '514299010010')
        self.assertEqual(stats['outreach'], 'held')
        self.assertNotIn('DOE', json.dumps(stats))
        self.assertNotIn('EXAMPLE ST', json.dumps(stats))
        for lead in leads:
            self.assertEqual(lead['outreach'], 'held')
            self.assertNotIn('phone', lead)
            self.assertNotIn('email', lead)
            self.assertNotIn('amount', lead)

    def test_run_writes_outside_and_refuses_the_repo(self):
        write_day(self.feed, '10-05-2026', [
            doc('120000070', '20261005', 'LP', 'CACE-26-000070'),
        ], [
            nme('120000070', 'EXAMPLE MORTGAGE LLC', 'D', 1),
            nme('120000070', 'DOE, JANE A', 'R', 1),
        ])
        stats = F.run(directory=self.feed, out_dir=self.out)
        self.assertTrue(stats['ok'])
        self.assertTrue(os.path.isfile(os.path.join(self.out, 'feed.json')))
        self.assertTrue(os.path.isfile(os.path.join(self.out, 'status.json')))
        self.assertFalse(os.path.isfile(os.path.join(self.out, 'broward_or_leads.json')))
        saved = read_json(os.path.join(self.out, 'feed.json'))
        self.assertEqual(saved[0]['case'], 'CACE-26-000070')
        self.assertEqual(saved[0]['match'], 'none')
        with self.assertRaises(ValueError):
            F.run(directory=self.feed, out_dir=HERE)

    def test_missing_directory_and_unreadable_roll_do_not_guess(self):
        stats = F.run(directory=os.path.join(self.tmp, 'nope'), out_dir=self.out)
        self.assertFalse(stats['ok'])
        self.assertFalse(os.path.exists(os.path.join(self.out, 'feed.json')))
        write_day(self.feed, '10-05-2026', [
            doc('120000071', '20261005', 'LP', 'CACE-26-000071'),
        ], [nme('120000071', 'DOE, JANE A', 'R', 1)])
        bad_roll = os.path.join(self.tmp, 'bad.csv')
        with open(bad_roll, 'w', encoding='latin-1') as fh:
            fh.write('FOO,BAR\n1,2\n')
        stats = F.run(directory=self.feed, roll_path=bad_roll, out_dir=self.out)
        self.assertEqual(stats['roll'], 'unreadable')
        saved = read_json(os.path.join(self.out, 'feed.json'))
        self.assertEqual(saved[0]['match'], 'none')
        self.assertEqual(saved[0]['match_note'], 'no-roll')
        self.assertEqual(saved[0]['parcel'], '')

    def test_config_supplies_the_folder_when_the_env_dir_is_empty(self):
        write_day(self.feed, '10-05-2026', [
            doc('120000072', '20261005', 'LP', 'COCE-26-000072'),
        ], [
            nme('120000072', 'SAMPLE HOMEOWNERS ASSOCIATION INC', 'D', 1),
            nme('120000072', 'DOE, JANE A', 'R', 1),
        ])
        cfg = os.path.join(self.tmp, 'feed-config.json')
        with open(cfg, 'w', encoding='utf-8') as fh:
            json.dump({'dir': self.feed}, fh)
        stats = F.run(env={F.ENV_CONFIG: cfg, 'DEALFLOW_DIR': self.tmp}, out_dir=self.out)
        self.assertEqual(stats['open_leads'], 1)
        saved = read_json(os.path.join(self.out, 'feed.json'))
        self.assertEqual(saved[0]['kind'], 'hoa-condo')


class CollectorTests(unittest.TestCase):
    def test_flag_defaults_off(self):
        for value in ('', '0', 'off', 'false', 'no', 'nope'):
            self.assertFalse(F.enabled({F.ENV_FLAG: value}))
        for value in ('1', 'true', 'yes', 'on', ' YES '):
            self.assertTrue(F.enabled({F.ENV_FLAG: value}))

    def test_hook_writes_a_list_and_returns_nothing_to_merge(self):
        tmp = tempfile.mkdtemp(prefix='broward-hook-')
        try:
            folder = os.path.join(tmp, 'days')
            os.makedirs(folder)
            write_day(folder, '10-05-2026', [
                doc('120000073', '20261005', 'LP', 'CACE-26-000073'),
            ], [
                nme('120000073', 'EXAMPLE MORTGAGE LLC', 'D', 1),
                nme('120000073', 'DOE, JANE A', 'R', 1),
            ])
            off = {
                F.ENV_FLAG: '0',
                F.ENV_DIR: folder,
                'DEALFLOW_DIR': os.path.join(tmp, 'off'),
            }
            self.assertIsNone(LP._maybe_broward_or_feed(off))
            self.assertFalse(os.path.exists(os.path.join(tmp, 'off', 'broward_or_feed', 'feed.json')))
            on = {
                F.ENV_FLAG: '1',
                F.ENV_DIR: folder,
                'DEALFLOW_DIR': os.path.join(tmp, 'on'),
            }
            self.assertIsNone(LP._maybe_broward_or_feed(on))
            saved = read_json(os.path.join(tmp, 'on', 'broward_or_feed', 'feed.json'))
            self.assertEqual(saved[0]['case'], 'CACE-26-000073')
            self.assertEqual(saved[0]['outreach'], 'held')
            status = read_json(os.path.join(tmp, 'on', 'broward_or_feed', 'status.json'))
            self.assertEqual(status['outreach'], 'held')
            self.assertNotIn('DOE', json.dumps(status))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_collector_gate_uses_the_same_on_words(self):
        src = read_text(os.path.join(HERE, 'lis_pendens.py'))
        gate = src.split('def _maybe_broward_or_feed', 1)[1].split('import broward_or_feed', 1)[0]
        for word in F._ON:
            self.assertIn("'%s'" % word, gate)

    def test_name_tokens_follow_the_resolve_rule(self):
        import lp_resolve2 as R2
        for name in ('DOE, JANE A', 'DOE JANE TR', 'THE DOE FAMILY'):
            self.assertEqual(set(F.ordered_tokens(name)), R2.toks(name), name)

    def test_roll_columns_are_the_cadastral_clients(self):
        for col in F.ROLL_COLUMNS:
            self.assertIn(col, fl_cadastral.FLDS)

    def test_module_does_not_touch_outreach_gates(self):
        src = read_text(os.path.join(HERE, 'broward_or_feed.py'))
        for word in ('stay_gate', 'optout', 'equity_state', 'publish_guard', 'make_tracker('):
            self.assertNotIn(word, src)
        hook = read_text(os.path.join(HERE, 'lis_pendens.py'))
        self.assertIn('return None', hook[hook.index('def _maybe_broward_or_feed'):
                                           hook.index('def _merge_key')])


if __name__ == '__main__':
    unittest.main()
