"""Execute the production Whitepages bake on resident-only synthetic cache records, offline."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest

import foreclosure_leads as F


class ResidentOnlyBake(unittest.TestCase):
    def bake(self, apn='00-0000-000-0001', residents=True):
        tree = ast.parse(Path(F.__file__).read_text(encoding='utf-8'))
        build = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'make_tracker')
        # Execute the real merge statement, not a second implementation; do not run the board's
        # other enrichment passes, private inputs, or network operations.
        merge = next(n for n in build.body if isinstance(n, ast.If)
                     and ast.unparse(n.test) == 'os.path.exists(_wpf) or _wp_ids')
        with tempfile.TemporaryDirectory() as tmp:
            cached = {'result': {'apn': apn, 'ownership_info': {'person_owners': []},
                      'residents': [{'name': 'Synthetic Alpha Person',
                                     'phones': [{'number': '3055550101', 'type': 'mobile'}],
                                     'emails': ['private-fixture@example.com']}] if residents else []}}
            path = Path(tmp) / 'whitepages_lookup.json'
            path.write_text(json.dumps({'SYNTHETIC-RESIDENT': cached}), encoding='utf-8')
            lead = {'case': 'SYNTHETIC-RESIDENT', 'owners': 'PERSON, SYNTHETIC ALPHA',
                    'addr': '1 Example Street, Example City, FL', 'folio': '0000000000001',
                    'phones': ['3055550102'], 'phtype': ['mobile'], 'phsrc': ['st'],
                    'phdnc': [True], 'emails': ['existing-fixture@example.com']}
            env = dict(vars(F), HERE=tmp, _wpf=str(path), _wp_ids={},
                       _wp_norm_id=lambda value: value, slim=[lead])
            exec(compile(ast.Module(body=[copy.deepcopy(merge)], type_ignores=[]),
                         '<production Whitepages merge>', 'exec'), env)
            return lead

    def test_resident_only_contacts_reach_the_baked_phone_evidence(self):
        row = self.bake()
        self.assertEqual(row.get('wpKey'), 'ok')
        self.assertEqual([p['n'] for p in row.get('wpAllPhones', [])], ['3055550101'])
        self.assertIn('3055550101', row['phones'])
        i = row['phones'].index('3055550101')
        self.assertEqual(row['phsrc'][i], 'hh', 'no messaging role upgrade')
        self.assertTrue(row['phdnc'][row['phones'].index('3055550102')])
        self.assertEqual(row['emails'], ['existing-fixture@example.com'], 'no new email recipients')

    def test_wrong_parcel_residents_stay_excluded(self):
        row = self.bake(apn='00-0000-000-0002')
        self.assertEqual(row.get('wpKey'), 'mismatch')
        self.assertNotIn('3055550101', row['phones'])

    def test_empty_record_stays_a_miss(self):
        row = self.bake(residents=False)
        self.assertEqual(row.get('wpKey'), 'none')
        self.assertEqual(row['phones'], ['3055550102'])


if __name__ == '__main__':
    unittest.main()
