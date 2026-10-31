"""Offline Person-record attribution: a building match is not a unit match."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import wp_contacts as W


def baked_unit_leads():
    """Run the production cache merge, never make_tracker or a production lookup."""
    import phone_src as PS
    source = Path(__file__).with_name('foreclosure_leads.py').read_text(encoding='utf-8')
    block = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Try) and n.body
                 and isinstance(n.body[0], ast.Assign)
                 and isinstance(n.body[0].targets[0], ast.Name)
                 and n.body[0].targets[0].id == '_wp')
    rows, cache = [], {}
    for county, area in [('MIAMI-DADE', '305'), ('BROWARD', '954'), ('PALM BEACH', '561')]:
        for i in range(12):
            case = f'SYNTHETIC-UNIT-{area}-{i:02}'
            nums = [area + str(5550100 + i * 4 + j) for j in range(4)]
            street = f'{100 + i} Example Street'
            rows.append(dict(case=case, county=county, st='LP', days=9999,
                             owners='EXAMPLE, TAYLOR', oname='Taylor Example',
                             addr=street + ' Apt 201, Example City, FL',
                             phones=nums[:2], phdnc=[False, True],
                             phsrc=['st', 'st'], phtype=['mobile', 'mobile']))
            cache[case] = {'_person': [{'name': 'Taylor Example', 'response': [
                {'name': 'Taylor Example', 'addresses': [street + ' Unit 202'],
                 'phones': [{'number': nums[2], 'type': 'mobile'}]},
                {'name': 'Taylor Example', 'addresses': [street + ' Unit 201'],
                 'phones': [{'number': nums[3], 'type': 'mobile'}]}]}]}
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'whitepages_lookup.json'
        path.write_text(json.dumps(cache), encoding='utf-8')
        env = dict(os=os, json=json, HERE=temp, _wpf=str(path), _wp_ids={},
                   _wp_norm_id=lambda x: x, wp_parcel_verdict=W.wp_parcel_verdict,
                   slim=rows, fit_phones=PS.fit_phones, MAX_PHONES=PS.MAX_PHONES,
                   PHSRC_TRACE=PS.PHSRC_TRACE, PHSRC_HOUSEHOLD=PS.PHSRC_HOUSEHOLD,
                   PHSRC_NAME=PS.PHSRC_NAME, PHSRC_WP=PS.PHSRC_WP)
        with contextlib.redirect_stdout(io.StringIO()) as log:
            exec(compile(ast.Module(body=[block], type_ignores=[]), 'wp-unit-bake', 'exec'), env)
        if 'skipped' in log.getvalue():
            raise AssertionError(log.getvalue())
    return rows


class PersonUnitTests(unittest.TestCase):
    owner = 'EXAMPLE, TAYLOR'
    street = '100 Example Street'

    def grade(self, address, lead=None):
        return W.grade_person_record(self.owner, lead or self.street + ' Apt 201',
                                     {'name': 'Taylor Example', 'addresses': [address]})

    def test_different_unit_is_name_only_in_every_county(self):
        for city in ('Miami', 'Fort Lauderdale', 'West Palm Beach'):
            with self.subTest(city=city):
                lead = self.street + ' Apt 201, ' + city + ', FL'
                self.assertEqual(self.grade(self.street + ' Apt 202, ' + city + ', FL', lead),
                                 'nm', 'another unit must not confirm the owner at this property')

    def test_unit_after_comma_is_not_discarded(self):
        self.assertEqual(self.grade(self.street + ', Unit 202, Example City, FL'), 'nm')
        self.assertEqual(self.grade(self.street + ', Unit 201, Example City, FL'), 'wp')
        self.assertEqual(self.grade(self.street + ' Unit 201',
                                   self.street + ', Apt 201, Example City, FL'), 'wp')

    def test_missing_unit_stays_name_only(self):
        self.assertEqual(self.grade(self.street), 'nm')
        self.assertEqual(self.grade(self.street + ' Unit 201', self.street), 'nm')

    def test_matching_unit_spellings_confirm(self):
        for marker in ('Apt', 'Apartment', 'Unit', '#', 'Suite', 'Ste.'):
            with self.subTest(marker=marker):
                self.assertEqual(self.grade(self.street + ' ' + marker + ' 201'), 'wp')
        self.assertEqual(self.grade(self.street + ' #2a', self.street + ' Unit 2A'), 'wp')
        self.assertEqual(self.grade(self.street + ' Unit 2B', self.street + ' Unit 2A'), 'nm')

    def test_marker_like_street_names_do_not_hide_the_actual_unit(self):
        for street in ('100 United Avenue', '100 Apartment Road', '100 Steadfast Road'):
            with self.subTest(street=street):
                self.assertEqual(W._unit_key(street), '')
                self.assertEqual(W._unit_key(street + ' Apt 201'), '201')
                self.assertEqual(self.grade(street + ' Apt 202', street + ' Apt 201'), 'nm')
                self.assertEqual(self.grade(street + ' Apt 201', street + ' Apt 201'), 'wp')
                self.assertEqual(self.grade(street, street), 'wp')
        for marker, unit in (('Unit201', '201'), ('Apt201', '201'),
                             ('Unit2A', '2A'), ('#2a', '2A'), ('UnitA', 'A')):
            with self.subTest(compact_marker=marker):
                self.assertEqual(self.grade(self.street + ' ' + marker,
                                           self.street + ' Unit ' + unit), 'wp')

    def test_unit_before_comma_less_city_state_is_not_discarded(self):
        lead = self.street + ' Apt 201 Example City FL'
        for address in (self.street + ' Apt 202 Example City FL',
                        {'full_address': self.street + ' Apt 202 Example City FL'}):
            with self.subTest(address_kind=type(address).__name__):
                self.assertEqual(self.grade(address, lead), 'nm')
        self.assertEqual(self.grade(self.street + ' Apt 201 Example City FL', lead), 'wp')
        self.assertEqual(self.grade({'full_address': self.street + ' Apt 201 Example City FL'}, lead), 'wp')
        self.assertEqual(self.grade(self.street + ' Example City FL', lead), 'nm')

    def test_structured_unit_cannot_be_bypassed_by_bare_line(self):
        for key in ('unit', 'unit_number', 'street_line_2', 'line2'):
            with self.subTest(key=key):
                address = {'line1': self.street, key: 'Unit 202'}
                self.assertEqual(self.grade(address), 'nm')
                address[key] = '201' if key in ('unit', 'unit_number') else 'Apt 201'
                self.assertEqual(self.grade(address), 'wp')
        self.assertEqual(self.grade({'line1': self.street,
                                     'full_address': self.street + ' Unit 202'}), 'nm')

    def test_conflicting_address_fields_cannot_confirm(self):
        self.assertEqual(self.grade({'line1': self.street + ' Apt 201',
                                     'full_address': self.street + ' Apt 202'}), 'nm')

    def test_single_family_and_street_mismatch_keep_existing_rules(self):
        self.assertEqual(self.grade(self.street, self.street), 'wp')
        self.assertEqual(self.grade('102 Example Street Apt 201'), 'nm')
        self.assertEqual(W.grade_person_record(self.owner, self.street,
                         {'name': 'Morgan Other', 'addresses': [self.street]}), None)
        # same_street deliberately compares buildings; only Person attribution is tightened.
        self.assertTrue(W.same_street(self.street + ' Unit 201', self.street + ' Unit 202'))

    def test_production_bake_to_call_payload_for_36_leads(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'DEALFLOW_DIR': temp}):
            import call_mode as C
            leads = baked_unit_leads()
            rows, total = C.call_rows(leads)
        self.assertEqual((len(rows), total), (36, 36))
        by_case = {r['c']: r for r in rows}
        for lead in leads:
            with self.subTest(case=lead['case']):
                self.assertEqual(lead['phsrc'], ['st', 'st', 'nm', 'wp'])
                wrong, right = lead['phones'][2:]
                row = by_case[lead['case']]
                self.assertEqual(row['p'][0], right)
                self.assertEqual(row['pw'][row['p'].index(wrong)], 'n')
                self.assertEqual(row['ps'][row['p'].index(wrong)], 'nm')
                self.assertEqual(row['pw'][row['p'].index(right)], 'o')
                self.assertNotIn(lead['phones'][1], row['p'])
                self.assertEqual(row['dn'][0][0], lead['phones'][1])


if __name__ == '__main__':
    unittest.main()
