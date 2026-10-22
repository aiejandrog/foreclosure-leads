"""Execute the actual WP bake with invented records for every supported county."""
import ast
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import os

import phone_src as PS
from foreclosure_leads import wp_parcel_verdict


def bake(extra=None):
    source = Path(__file__).with_name('foreclosure_leads.py').read_text(encoding='utf-8')
    block = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Try) and n.body
                 and isinstance(n.body[0], ast.Assign)
                 and isinstance(n.body[0].targets[0], ast.Name)
                 and n.body[0].targets[0].id == '_wp')
    rows, cache = [], {}
    for i, (county, folio) in enumerate([('MIAMI-DADE', '9999999999991'),
                                       ('BROWARD', '999999999992'),
                                       ('PALM BEACH', '99999999999999993')]):
        key = 'SYNTHETIC-' + str(i)
        rows.append({'case': key, 'county': county, 'folio': folio,
                     'owners': 'GARCIA, JOSE', 'addr': '123 Example St, Example City, FL',
                     'phones': [], 'phdnc': [], 'phtype': [], 'phsrc': []})
        phones = [None, 'not a phone', '3055550101'] if i == 0 else [{'number': '3055550102'}]
        cache[key] = {'result': {'apn': folio, 'ownership_info': {'person_owners': [
            {'name': 'Jose Garcia', 'phones': phones}]}}}
    cache['SYNTHETIC-2']['_person'] = [{'name': 'Jose Garcia', 'response': [
        {'name': 'Jose Garcia', 'phones': ['3055550103', 3055550104, {'number': None}],
         'emails': ['jg@example.invalid', None, {'address': 'jg2@example.invalid'}]}]}]
    for key, entry in (extra or {}).items():
        cache[key] = entry
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'whitepages_lookup.json'
        path.write_text(json.dumps(cache))
        env = dict(os=os, json=json, HERE=temp, _wpf=str(path), _wp_ids={},
                   _wp_norm_id=lambda x: x, wp_parcel_verdict=wp_parcel_verdict, slim=rows,
                   fit_phones=PS.fit_phones,
                   MAX_PHONES=PS.MAX_PHONES, PHSRC_TRACE=PS.PHSRC_TRACE,
                   PHSRC_HOUSEHOLD=PS.PHSRC_HOUSEHOLD, PHSRC_NAME=PS.PHSRC_NAME,
                   PHSRC_WP=PS.PHSRC_WP)
        with contextlib.redirect_stdout(io.StringIO()) as log:
            exec(compile(ast.Module(body=[block], type_ignores=[]), 'wp-bake', 'exec'), env)
        return rows, log.getvalue()


class CacheShapeTests(unittest.TestCase):
    def test_a_null_owner_is_not_a_verified_owner(self):
        rows, log = bake({'SYNTHETIC-0': {'result': {'apn': '9999999999991',
                                                     'ownership_info': {'person_owners': [None]}}}})
        self.assertNotIn('merge skipped', log)
        self.assertNotEqual(rows[0].get('wpKey'), 'ok')
        self.assertFalse(rows[0].get('wpOwners'))
        self.assertEqual(rows[1]['phones'], ['3055550102'])

    def test_a_record_that_still_throws_skips_only_its_own_lead(self):
        # ownership_info as a string raises inside the merge; before the per-lead guard that
        # one record dropped Whitepages from every lead after it.
        rows, log = bake({'SYNTHETIC-0': {'_prop_id': 'x', 'result': {'apn': '9999999999991',
                                                                     'ownership_info': 'junk'}}})
        self.assertNotIn('merge skipped', log)
        self.assertIn('one lead skipped', log)
        self.assertNotIn('GARCIA', log)
        self.assertEqual(rows[0]['phones'], [])
        self.assertNotIn('wpPropId', rows[0])
        self.assertNotIn('wpKey', rows[0])
        self.assertEqual(rows[1]['phones'], ['3055550102'])
        self.assertEqual(rows[2]['phones'][0], '3055550102')

    def test_property_phone_shapes_do_not_drop_later_counties(self):
        rows, log = bake()
        self.assertNotIn('merge skipped', log)
        self.assertEqual(rows[0]['phones'], ['3055550101'])
        self.assertEqual(rows[1]['phones'], ['3055550102'])

    def test_person_phone_strings_reach_palm_beach_and_metadata_aligns(self):
        rows, _ = bake()
        self.assertEqual(rows[2]['phones'], ['3055550102', '3055550103', '3055550104'])
        self.assertEqual(rows[2]['phsrc'], ['wp', 'nm', 'nm'])
        self.assertEqual(rows[2]['emails'], ['jg@example.invalid', 'jg2@example.invalid'])
        for row in rows:
            for field in ('phtype', 'phdnc', 'phsrc'):
                self.assertEqual(len(row[field]), len(row['phones']))


if __name__ == '__main__':
    unittest.main()
