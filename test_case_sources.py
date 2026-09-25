import unittest
from case_sources import SOURCES, source_tasks
from case_review import build_review


class SourceRoutingTests(unittest.TestCase):
    def test_all_counties_get_four_independent_pending_tasks(self):
        for county in SOURCES:
            manifest = build_review({'dockets': [], 'parties': []}, 'TEST', county)
            self.assertEqual(len(manifest['source_tasks']), 4)
            self.assertFalse(manifest['verification_policy']['human_verification_required'])
            self.assertEqual(manifest['status'], 'incomplete')
            self.assertTrue(all(t['status'] == 'pending' for t in manifest['source_tasks']))
        tasks = source_tasks('BROWARD')
        tasks[0]['required_evidence'].clear()
        self.assertTrue(source_tasks('BROWARD')[0]['required_evidence'])

    def test_no_default_example_parcel_or_silent_county(self):
        self.assertNotIn('folio=', SOURCES['MIAMI-DADE']['appraiser'])
        self.assertEqual(build_review({'dockets': [], 'parties': []}, 'TEST')['source_routing_status'], 'county_required')
        with self.assertRaises(ValueError):
            source_tasks('UNKNOWN')


if __name__ == '__main__':
    unittest.main()
