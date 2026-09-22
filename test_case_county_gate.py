import os
import unittest

import diligence_flags as DF
import diligence_gate as DG


class CaseCountyGateTests(unittest.TestCase):
    def setUp(self):
        self.old_gate = os.environ.get('DEALFLOW_DILIGENCE_GATE')

    def tearDown(self):
        if self.old_gate is None:
            os.environ.pop('DEALFLOW_DILIGENCE_GATE', None)
        else:
            os.environ['DEALFLOW_DILIGENCE_GATE'] = self.old_gate

    def test_case_dialect_recognizes_punctuated_palm_beach(self):
        self.assertEqual(DF.case_county('50-2026-CA-009372'), 'PALM BEACH')

    def test_broward_tag_with_palm_case_is_critical_hold(self):
        row = {'case': '50-2026-CA-009372', 'county': 'BROWARD', 'oname': 'WPT LAND 2 LP'}
        flag = next(f for f in DF.risk_flags(row) if f['code'] == 'CASE_COUNTY_MISMATCH')
        self.assertEqual(flag['sev'], 'critical')
        self.assertTrue(DG.gate(row)['hold'])

    def test_matching_counties_do_not_flag(self):
        rows = [
            {'case': 'CACE-26-009372', 'county': 'BROWARD'},
            {'case': '502026CA009372XXXAMB', 'county': 'PALM BEACH'},
            {'case': '2026-009372-CA-01', 'county': 'MIAMI-DADE'},
        ]
        for row in rows:
            self.assertNotIn('CASE_COUNTY_MISMATCH', {f['code'] for f in DF.risk_flags(row)})

    def test_gate_off_cannot_release_mismatch(self):
        os.environ['DEALFLOW_DILIGENCE_GATE'] = 'off'
        row = {'case': '50-2026-CA-009372', 'county': 'BROWARD'}
        self.assertTrue(DG.gate(row)['hold'])
        self.assertIn('CASE_COUNTY_MISMATCH', DG.NEVER_RELEASED)


if __name__ == '__main__':
    unittest.main()
