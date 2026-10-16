#!/usr/bin/env python
"""The hold-funnel script prints counts only, including on a fake cache."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import hold_funnel_counts as H


NOW = time.time()
DAY = time.strftime('%Y-%m-%d', time.localtime(NOW))
BROWARD = 'CACE-99-100001'
PALM = '509999CA000001XXXAMB'
PLANTED = 'QUINCY TESTPERSON'


def _clear(now=NOW):
    import datetime as dt
    day = dt.date.fromtimestamp(now)
    return {
        'verdict': 'clear', 'a': False, 'env': 'prod', 't': now,
        'q': day.isoformat() + 'T12:00:00-04:00', 'region': 'national',
        'lookback_from': day.replace(year=day.year - 8).isoformat(),
        'src': 'pacer_pcl', 'why': 'no open bankruptcy', 'cases': [],
    }


class Funnel(unittest.TestCase):
    def _dirs(self):
        root = tempfile.mkdtemp()
        deal = os.path.join(root, 'deal')
        os.makedirs(deal)
        return root, deal

    def _write(self, deal, cl, pacer, status=None):
        with open(os.path.join(deal, 'bk_lead_cache.json'), 'w', encoding='utf-8') as f:
            json.dump(cl, f)
        pacer_path = os.path.join(deal, 'pacer_stay_cache.json')
        with open(pacer_path, 'w', encoding='utf-8') as f:
            json.dump(pacer, f)
        if status is not None:
            with open(os.path.join(deal, 'bk_lookup_status.json'), 'w', encoding='utf-8') as f:
                json.dump(status, f)
        return pacer_path

    def test_buckets_and_stdout_have_no_planted_text(self):
        _root, deal = self._dirs()
        cl = {
            BROWARD: {
                'verdict': 'clear_unconfirmed', 't': NOW, 'searched': True, 'ok_check': True,
                'err': '', 'cases': [], 'why': 'no open match',
            },
            PALM: {
                'verdict': 'unavailable', 't': NOW, 'searched': False, 'ok_check': False,
                'err': 'CourtListener party search truncated — lead stays held', 'cases': [],
                'why': 'CourtListener party search truncated — lead stays held ' + PLANTED,
            },
        }
        pacer = {
            BROWARD: _clear(),
            PALM: {
                'verdict': 'unverifiable', 'env': 'prod', 't': NOW, 'q': DAY + 'T12:00:00-04:00',
                'why': 'an open bankruptcy for the same name in nysbk (cannot tell if it is this owner) ' + PLANTED,
                'cases': [],
            },
        }
        # A second Broward key, trust-shaped why, so the debtor-name bucket is counted.
        other = 'CACE-99-100002'
        cl[other] = {'verdict': 'active', 'a': True, 't': NOW, 'searched': True, 'ok_check': True,
                     'cases': [{'no': '1:99-bk-10001', 'open': True, 'match': 'exact'}], 'why': PLANTED}
        pacer[other] = {'verdict': 'unverifiable', 'env': 'prod', 't': NOW, 'why': 'owner is a trust -- ' + PLANTED}
        pacer_path = self._write(deal, cl, pacer, {'pull_ok': True, 'filings': 3, 'holds': 1, 'errors': 0,
                                                   'truncated': 2, 'leads_checked': 4, 'reason': PLANTED})
        text = H.run(deal, pacer_path, now=NOW)
        self.assertNotIn(PLANTED, text)
        self.assertNotIn(BROWARD, text)
        self.assertNotIn(PALM, text)
        self.assertNotIn('1:99', text)
        self.assertIn('county broward leads=2', text)
        self.assertIn('exact_match=1', text)
        self.assertIn('clear_unconfirmed=1', text)
        self.assertIn('county palmbeach leads=1', text)
        self.assertIn('truncated=1', text)
        self.assertIn('out_of_state_namesake=1', text)
        self.assertIn('no_single_debtor_name=1', text)
        self.assertIn('clear_fresh=1', text)
        self.assertIn('release_today=1', text)
        self.assertIn('bk_status readable=1', text)
        for line in text.splitlines():
            self.assertRegex(line, H._COUNT_LINE)

    def test_unreadable_cache_blocks_the_release_count(self):
        _root, deal = self._dirs()
        with open(os.path.join(deal, 'bk_lead_cache.json'), 'w', encoding='utf-8') as f:
            f.write('{')
        pacer_path = os.path.join(deal, 'pacer_stay_cache.json')
        with open(pacer_path, 'w', encoding='utf-8') as f:
            json.dump({BROWARD: _clear()}, f)
        text = H.run(deal, pacer_path, now=NOW)
        self.assertIn('release_blocked=1', text)
        self.assertIn('release_today=0', text)
        self.assertNotIn(BROWARD, text)

    def test_cli_on_a_fake_folder(self):
        _root, deal = self._dirs()
        pacer_path = self._write(deal, {}, {PALM: {'verdict': 'unverifiable', 'env': 'prod', 't': NOW,
                                                   'why': 'PCL error: timeout ' + PLANTED}})
        rc = subprocess.run([sys.executable, 'hold_funnel_counts.py', '--dealflow', deal, '--pacer', pacer_path],
                            capture_output=True, text=True)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        self.assertIn('pcl_error=1', rc.stdout)
        self.assertNotIn(PLANTED, rc.stdout)
        self.assertNotIn(PALM, rc.stdout)


if __name__ == '__main__':
    unittest.main()
