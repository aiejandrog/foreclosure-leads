#!/usr/bin/env python
"""Fail-closed checks for the Broward / Palm Beach PACER name guard.

The ten cache keys below are fake stand-ins for the ten saved production clears
(7 Broward, 3 Palm Beach). They are not real cases. This branch's parser does not
read or write that cache, and it does not change pacer_confirmed.
"""
import datetime as dt
import inspect
import json
import os
import tempfile
import time
import unittest

import bk_lookup
import pacer_stay as PS
import stay_gate as SG


TODAY = dt.date(2026, 6, 1)
BROWARD = ['CACE-99-10000%d' % i for i in range(1, 8)]
PALM = ['509999CA00000%dXXXAMB' % i for i in range(1, 4)]
TEN = BROWARD + PALM


def _row(last, first, court):
    filed = '2024-01-15'
    return {
        'lastName': last, 'firstName': first, 'middleName': ' ', 'partyRole': 'db',
        'courtId': court, 'caseNumberFull': '1:99-bk-10001', 'dateFiled': filed,
        'jurisdictionType': 'Bankruptcy', 'caseType': 'bk', 'bankruptcyChapter': '13',
        'courtCase': {'courtId': court, 'caseType': 'bk', 'caseNumberFull': '1:99-bk-10001',
                      'dateFiled': filed, 'jurisdictionType': 'Bankruptcy'},
    }


def _fresh(now, **over):
    day = dt.date.fromtimestamp(now)
    ent = {
        'verdict': 'clear', 'a': False, 'env': 'prod', 't': now,
        'q': day.isoformat() + 'T12:00:00-04:00', 'region': 'national',
        'lookback_from': day.replace(year=day.year - 8).isoformat(),
        'src': 'pacer_pcl', 'why': 'no open bankruptcy', 'cases': [],
    }
    ent.update(over)
    return ent


class NameGuard(unittest.TestCase):
    def test_et_al_is_not_searched_and_the_named_person_alone_still_is(self):
        plain, plain_why = PS.parse_owner('TESTPERSON QUINCY', 'last_first')
        weaker, why = PS.parse_owner('TESTPERSON QUINCY ET AL', 'last_first')
        self.assertTrue(plain and plain_why == '')
        self.assertEqual(weaker, [])
        self.assertIn('et al', why)
        subjects, problem, n = PS.lead_subjects([('TESTPERSON QUINCY ET AL', 'last_first')])
        self.assertEqual(subjects, [])
        self.assertNotEqual(problem, '')
        self.assertEqual(n, 0)

    def test_bare_et_ux_stays_held_and_a_named_spouse_is_still_searched(self):
        bare, why = PS.parse_owner('TESTPERSON QUINCY ET UX', 'last_first')
        self.assertEqual(bare, [])
        self.assertIn('et ux', why)
        subjects, problem, n = PS.lead_subjects([('TESTPERSON QUINCY ET UX ROSALIND', 'last_first')])
        self.assertEqual(problem, '')
        queries = [q for s in subjects for q in s.queries()]
        self.assertIn({'last': 'TESTPERSON', 'first': 'QUINCY'}, queries)
        self.assertIn({'last': 'TESTPERSON', 'first': 'ROSALIND'}, queries)
        self.assertGreaterEqual(n, 2)

    def test_person_beside_a_trust_is_not_searched(self):
        # Searching the named person would be a new PCL page. This branch does not spend it.
        subjects, why = PS.parse_owner('TESTPERSON, QUINCY; TESTPERSON FAMILY TRUST', 'last_first')
        self.assertEqual(subjects, [])
        self.assertIn('trust', why)
        dotted, dotted_why = PS.parse_owner('ACME WIDGET L.L.C.', 'last_first')
        self.assertEqual(dotted, [])
        self.assertNotEqual(dotted_why, '')
        estate, estate_why = PS.parse_owner('ESTATE OF QUINCY TESTPERSON', 'last_first')
        self.assertEqual(estate, [])
        self.assertIn('estate', estate_why)

    def test_same_name_pcl_match_with_no_address_stays_unverifiable(self):
        person = PS.parse_owner('TESTPERSON, QUINCY', 'last_first')[0][0]
        row = _row('TESTPERSON', 'QUINCY', 'nysbk')
        self.assertNotIn('address', row)
        self.assertNotIn('city', row)
        verdict, why, _cases, _bd = PS.decide(
            [person], [(0, {'rows': [row], 'overflow': False, 'pages': 1})], TODAY, '2018-06-01')
        self.assertEqual(verdict, 'unverifiable')
        self.assertIn('same name', why)
        closed = dict(row, dateTermed='2020-01-01')
        closed['courtCase'] = dict(row['courtCase'], dateTermed='2020-01-01', effectiveDateClosed='2020-01-01')
        # An open same-name row is the hold. Overflow with no row is still a hold.
        overflow, owhy, _, _ = PS.decide(
            [person], [(0, {'rows': [], 'overflow': True, 'pages': 1})], TODAY, '2018-06-01')
        self.assertEqual(overflow, 'unverifiable')
        self.assertIn('page', owhy)
        self.assertNotEqual(closed, row)


class SavedClears(unittest.TestCase):
    def _write(self, folder, cache, hits=None):
        with open(os.path.join(folder, 'pacer_stay_cache.json'), 'w', encoding='utf-8') as f:
            json.dump(cache, f)
        if hits is not None:
            with open(os.path.join(folder, SG.HITS_NAME), 'w', encoding='utf-8') as f:
                json.dump(hits, f)

    def _confirmed(self, folder, key, now):
        prev = bk_lookup.PACER_ROOT
        bk_lookup.PACER_ROOT = folder
        try:
            return bk_lookup.pacer_confirmed(key, now)
        finally:
            bk_lookup.PACER_ROOT = prev

    def test_ten_defective_clears_stay_held(self):
        now = time.time()
        day = dt.date.fromtimestamp(now)
        defects = [
            {'t': now - 40 * 86400, 'q': (day - dt.timedelta(days=40)).isoformat() + 'T12:00:00-04:00'},
            {'region': 'fl'},
            {'lookback_from': day.replace(year=day.year - 2).isoformat()},
            {'env': 'qa'},
            {'verdict': 'unverifiable', 'why': 'more than 1 page(s) of results for one owner name (a common name)'},
            {'t': None, 'q': ''},
            {'verdict': 'active', 'a': True},
            {'region': ''},
            {'lookback_from': ''},
            {},  # blocked by a newer new-filer hit, filled below
        ]
        cache = {key: _fresh(now, **defects[i]) for i, key in enumerate(TEN)}
        hits = {TEN[-1]: {'env': 'prod', 'cases': [{'no': '1:99-bk-10001', 'court': 'flsbk'}],
                          'seen_t': now + 10, 't': now + 10}}
        self.assertEqual(len(BROWARD), 7)
        self.assertEqual(len(PALM), 3)
        with tempfile.TemporaryDirectory() as folder:
            self._write(folder, cache, hits)
            for key in TEN:
                self.assertIs(self._confirmed(folder, key, now), False, key)

    def test_parser_does_not_change_whether_ten_fresh_clears_release(self):
        now = time.time()
        cache = {key: _fresh(now) for key in TEN}
        src = inspect.getsource(bk_lookup.pacer_confirmed)
        self.assertNotIn('parse_owner', src)
        self.assertNotIn('_ET_AL_RE', src)
        with tempfile.TemporaryDirectory() as folder:
            self._write(folder, cache)
            before = [self._confirmed(folder, key, now) for key in TEN]
            PS.parse_owner('TESTPERSON QUINCY ET AL', 'last_first')
            PS.parse_owner('TESTPERSON, QUINCY; TESTPERSON FAMILY TRUST', 'last_first')
            after = [self._confirmed(folder, key, now) for key in TEN]
            self.assertEqual(before, after)
            self.assertTrue(all(before))
            with open(os.path.join(folder, 'pacer_stay_cache.json'), encoding='utf-8') as fh:
                again = json.load(fh)
            self.assertEqual(set(again), set(TEN))
            for ent in again.values():
                self.assertEqual(ent.get('verdict'), 'clear')


if __name__ == '__main__':
    unittest.main()
