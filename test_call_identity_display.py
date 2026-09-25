"""Offline regressions for names and property context in Call Mode."""
import json
import re
import subprocess
import unittest
from unittest.mock import patch

import call_mode as cm


class CallIdentityDisplayTests(unittest.TestCase):
    def lead(self, **extra):
        row = dict(case='SYNTHETIC-CASE', county='BROWARD', st='LP',
                   owners='', oname='EXAMPLE, TAYLOR', addr='',
                   phones=['9545550101'], days=9999)
        row.update(extra)
        return row

    def rows(self, lead):
        with patch.object(cm, '_quo_latest', return_value={}):
            return cm.call_rows([lead])[0]

    def test_owner_name_survives_when_only_oname_exists(self):
        row = self.rows(self.lead())[0]
        self.assertEqual(row['o'], 'EXAMPLE, TAYLOR')
        self.assertEqual(row['on'], 'TAYLOR EXAMPLE')

    def test_candidate_address_never_becomes_script_address(self):
        lead = self.lead(addrGuess='123 Example Street', addrWhy='Multiple parcels')
        for row in [self.rows(lead)[0], cm.coverage_rows([lead], [])[0][0]]:
            self.assertFalse(row.get('a'))
            self.assertEqual(row['ag'], '123 Example Street')
            self.assertEqual(row['aw'], 'Multiple parcels')

    def test_full_address_and_coowners_are_not_truncated(self):
        address = '123 Example Boulevard, Building A, Apartment 12345, Example City, FL 33333'
        owners = 'EXAMPLE, TAYLOR; EXAMPLE, MORGAN; EXAMPLE, CASEY; EXAMPLE, ALEXANDER; EXAMPLE, ROBIN'
        lead = self.lead(addr=address, owners=owners)
        for row in [self.rows(lead)[0], cm.coverage_rows([lead], [])[0][0]]:
            self.assertEqual(row['a'], address)
            self.assertEqual(row['o'], owners)

    def test_unknowns_stay_unknown_and_coverage_has_no_numbers(self):
        lead = self.lead(oname='')
        row = cm.coverage_rows([lead], [])[0][0]
        self.assertFalse(row.get('o'))
        self.assertFalse(row.get('on'))
        self.assertFalse(row.get('a'))
        self.assertNotIn('p', row)

    def test_browser_labels_handle_old_and_new_payloads(self):
        funcs = '\n'.join(re.search(r'function ' + name + r'\(r\)\{.*?\n\}',
                                    cm._PAGE, re.S).group(0)
                          for name in ('ownerLabel', 'propertyLabel'))
        script = funcs + '\nconsole.log(JSON.stringify([' + ','.join([
            "ownerLabel({on:'Taylor Example'})",
            "ownerLabel({o:'Taylor; Morgan',on:'Taylor'})",
            "propertyLabel({a:'Verified Street',ag:'Other Street'})",
            "propertyLabel({ag:'Candidate Street'})",
            "ownerLabel({})", "propertyLabel({})"
        ]) + ']));'
        result = subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
        labels = json.loads(result.stdout)
        self.assertEqual(labels[:3], ['Taylor Example', 'Taylor; Morgan', 'Verified Street'])
        self.assertIn('unverified', labels[3])
        self.assertIn('unresolved', labels[4])
        self.assertIn('unresolved', labels[5])


if __name__ == '__main__':
    unittest.main()
