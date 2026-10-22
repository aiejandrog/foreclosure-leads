"""Matched Whitepages resident-owner numbers survive the phone cap without role upgrades."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest

import foreclosure_leads as F
from phone_src import fit_phones, WP_OVER_TRACE


class OwnerPhoneCap(unittest.TestCase):
    def bake(self, name):
        root = ast.parse(Path(F.__file__).read_text(encoding='utf-8'))
        fn = next(n for n in root.body if isinstance(n, ast.FunctionDef) and n.name == 'make_tracker')
        merge = next(n for n in fn.body if isinstance(n, ast.If)
                     and ast.unparse(n.test) == 'os.path.exists(_wpf) or _wp_ids')
        with tempfile.TemporaryDirectory() as tmp:
            record = {'result': {'apn': '0000000000001', 'ownership_info': {'person_owners': [
                {'name': 'Unrelated Fixture Resident', 'phones': []}]}, 'residents': [
                {'name': name, 'phones': [{'number': '3055550101', 'type': 'mobile'}]}]}}
            path = Path(tmp) / 'whitepages_lookup.json'
            path.write_text(json.dumps({'SYNTHETIC-CAP': record}), encoding='utf-8')
            lead = {'case': 'SYNTHETIC-CAP', 'owners': 'PERSON, SYNTHETIC ALPHA',
                    'addr': '1 Example Street, Example City, FL', 'folio': '0000000000001',
                    'phones': ['954555%04d' % (100+i) for i in range(10)],
                    'phsrc': ['st'] * 10, 'phtype': ['mobile'] * 10,
                    'phdnc': [False] * 10, 'emails': []}
            env = dict(vars(F), HERE=tmp, _wpf=str(path), _wp_ids={},
                       _wp_norm_id=lambda value: value, slim=[lead])
            exec(compile(ast.Module(body=[copy.deepcopy(merge)], type_ignores=[]),
                         '<production Whitepages merge>', 'exec'), env)
            return lead

    def test_named_owner_survives_ten_existing_trace_numbers(self):
        row = self.bake('Synthetic Alpha Person')
        self.assertIn('3055550101', row['phones'])
        self.assertEqual(len(row['phones']), 10)
        self.assertEqual(row['phsrc'][row['phones'].index('3055550101')], 'hh')
        self.assertEqual(row['emails'], [])

    def test_unrelated_resident_does_not_displace_owner_trace_numbers(self):
        self.assertNotIn('3055550101', self.bake('Different Beta Tenant')['phones'])

    def test_retention_marker_keeps_the_source_and_respects_dnc(self):
        trace = {'n': '9545550101', 'src': 'st', 'dnc': False}
        owner = {'n': '3055550101', 'src': 'hh', 'wp_owner': True, 'dnc': False}
        self.assertEqual(fit_phones([trace, owner], cap=1), [owner])
        self.assertEqual(owner['src'], 'hh')
        self.assertEqual(fit_phones([trace, dict(owner, dnc=True)], cap=1), [trace])

    def test_owner_retention_does_not_bypass_the_existing_wp_quota(self):
        trace = {'n': '9545550101', 'src': 'st', 'dnc': False}
        owners = [{'n': '305555%04d' % (100+i), 'src': 'hh', 'wp_owner': True,
                   'dnc': False} for i in range(WP_OVER_TRACE + 2)]
        out = fit_phones([trace] + owners, cap=WP_OVER_TRACE + 1)
        self.assertIn(trace, out)
        self.assertEqual(sum(x['src'] == 'hh' for x in out), WP_OVER_TRACE)


if __name__ == '__main__':
    unittest.main()
