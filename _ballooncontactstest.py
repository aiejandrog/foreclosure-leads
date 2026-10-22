"""balloon_contacts: the writer of balloon_contacts.json. Fake records only, no provider call."""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

import balloon_leads as BL
import balloon_contacts as BC
import skiptrace as ST


def hit(owner, **kw):
    origin = (datetime.date.today() - datetime.timedelta(days=int(14 * 30.4))).isoformat()
    h = {'case': '', 'county': 'BROWARD', 'owner': owner, 'lender': 'SYNTHETIC CAPITAL', 'amt': 250000,
         'origin': origin, 'age_mo': 14.0, 'agent': 'DOE, JANE', 'agent_title': 'MGR',
         'agent_addr': '1 Test St, Fort Lauderdale, FL 33301', 'agent_ph': ''}
    h.update(kw)
    return h


class Select(unittest.TestCase):
    def test_countywide_officer_row_is_picked_with_officer_address(self):
        picked, _ = BC.select([hit('ALPHA SYN LLC')], {}, ST.parse_addr)
        self.assertEqual(len(picked), 1)
        p = picked[0]
        self.assertEqual(p['case'], BL._case_id(hit('ALPHA SYN LLC')))
        self.assertEqual(p['_trace_name'], 'JANE DOE')
        self.assertEqual(p['_trace_addr']['zip'], '33301')

    def test_foreclosure_case_rows_are_never_picked(self):
        # held by stay / opt-out gates keyed on the foreclosure case, which a BAL id would bypass
        picked, why = BC.select([hit('BETA SYN LLC', case='CACE-25-000001')], {}, ST.parse_addr)
        self.assertEqual(picked, [])
        self.assertEqual(why['foreclosure_case'], 1)

    def test_reg_agent_missing_address_and_out_of_zone_are_skipped(self):
        hits = [hit('C1 LLC', agent='LAW FIRM PA (reg. agent)'), hit('C2 LLC', agent_addr='nowhere'),
                hit('C3 LLC', age_mo=3.0), hit('C4 LLC', agent='')]
        picked, why = BC.select(hits, {}, ST.parse_addr)
        self.assertEqual(picked, [])
        self.assertEqual((why['reg_agent'], why['no_addr'], why['out_of_zone'], why['no_officer']), (1, 1, 1, 1))

    def test_a_recent_miss_is_not_paid_for_again_but_an_old_one_is(self):
        h = hit('DELTA SYN LLC')
        bal = BL._case_id(h)
        today = datetime.date.today()
        recent = {bal: {'phones': [], 'traced': today.isoformat()}}
        old = {bal: {'phones': [], 'traced': (today - datetime.timedelta(days=200)).isoformat()}}
        self.assertEqual(BC.select([h], recent, ST.parse_addr)[0], [])
        self.assertEqual(len(BC.select([h], old, ST.parse_addr)[0]), 1)
        have = {bal: {'phones': [{'number': '3055550100'}]}}
        self.assertEqual(BC.select([h], have, ST.parse_addr)[0], [])


class EndToEnd(unittest.TestCase):
    def test_traced_phone_reaches_the_balloon_row_and_no_email_is_written(self):
        h = hit('EPSILON SYN LLC')
        picked, _ = BC.select([h], {}, ST.parse_addr)
        e = BC.entry(picked[0], [{'number': '3055550199', 'type': 'Mobile', 'dnc': False}], 'tracerfy')
        self.assertEqual(e['emails'], [])
        row = BL.rows(hits=[h], contacts={picked[0]['case']: e})[0]
        self.assertEqual(row['phones'], ['3055550199'])
        self.assertEqual(row['emails'], [])
        self.assertEqual(row['bal']['contact_src'], BC.SRC)
        # before: with no contacts file the lane ships the row with no number
        self.assertEqual(BL.rows(hits=[h], contacts={})[0]['phones'], [])

    def test_dry_run_calls_no_provider_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'balloon_contacts.json')
            fake_hb = mock.Mock(candidates=mock.Mock(return_value=[hit('ZETA SYN LLC')]))
            with mock.patch.object(BC, 'OUT', out), \
                    mock.patch.dict('sys.modules', {'hardmoney_balloon': fake_hb}), \
                    mock.patch.object(ST, 'trace_one', side_effect=AssertionError('called')), \
                    mock.patch('sys.argv', ['balloon_contacts.py']):
                BC.main()
            self.assertFalse(os.path.exists(out))

    def test_trace_writes_the_entry_and_records_a_miss(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'balloon_contacts.json')
            hits = [hit('ETA SYN LLC'), hit('THETA SYN LLC', agent='ROE, RICHARD')]
            fake_hb = mock.Mock(candidates=mock.Mock(return_value=hits))
            answers = iter([([{'number': '3055550111', 'type': 'Mobile', 'dnc': False}], ['x@y.com']), ([], [])])
            with mock.patch.object(BC, 'OUT', out), \
                    mock.patch.dict('sys.modules', {'hardmoney_balloon': fake_hb}), \
                    mock.patch.object(ST, 'load_key', return_value='k'), \
                    mock.patch.object(ST, 'pick_provider', return_value='tracerfy'), \
                    mock.patch.object(ST, 'trace_one', side_effect=lambda *a, **k: next(answers)), \
                    mock.patch('sys.argv', ['balloon_contacts.py', '--trace']):
                BC.main()
            got = json.load(open(out, encoding='utf-8'))
            self.assertEqual(len(got), 2)
            phones = sorted(len(v['phones']) for v in got.values())
            self.assertEqual(phones, [0, 1])
            self.assertTrue(all(v['emails'] == [] for v in got.values()))

    def test_max_spend_aborts_before_any_call(self):
        fake_hb = mock.Mock(candidates=mock.Mock(return_value=[hit('IOTA%d SYN LLC' % i) for i in range(5)]))
        with mock.patch.dict('sys.modules', {'hardmoney_balloon': fake_hb}), \
                mock.patch.object(ST, 'pick_provider', return_value='tracerfy'), \
                mock.patch.object(ST, 'trace_one', side_effect=AssertionError('called')), \
                mock.patch('sys.argv', ['balloon_contacts.py', '--trace', '--max-spend', '0.2']):
            with self.assertRaises(SystemExit) as cm:
                BC.main()
        self.assertEqual(cm.exception.code, 4)


if __name__ == '__main__':
    unittest.main()
