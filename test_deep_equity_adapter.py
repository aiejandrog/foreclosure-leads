"""Synthetic producer-to-board contract. No real homeowner identifiers."""
import copy
import datetime as dt
import json
import pathlib
import tempfile
import unittest

import deep_equity_adapter as A
import diligence_flags as D
import equity_state as E


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.now().replace(microsecond=0)
        stamp = self.now.isoformat()
        self.lead = {'case': 'CACE-26-900001', 'county': 'BROWARD',
                     'ctype': 'Bank/Mortgage',
                     'folio': '000000-00-0001', 'value': 332360, 'judg': 267151.56,
                     'eqstate': 'unpriced', 'orconf': 'low', 'orjunior': 14685}
        self.result = {
            'case_key': 'CACE26900001', 'case_number': 'CACE26900001',
            'parcel': '000000000001', 'verdict': 'VERIFIED', 'case_status': 'active',
            'case_plaintiff': 'EXAMPLE BANK',
            'last_checked': stamp, 'source_at': {k: stamp for k in A.REQUIRED},
            'source_status': {k: 'OK' for k in A.REQUIRED}, 'unknowns': [],
            'debt_first': 267151.56, 'debt_first_inst': '900000002',
            'debt_first_src': 'principal claimed in the case',
            'debt_subordinate': [
                {'inst': str(900000004 + i), 'kind': 'hud_partial_claim',
                 'amount': n, 'fha_partial_claim': True, 'recorded': '2025-01-15'}
                for i, n in enumerate([73424.17, 9237.88, 5446.95])],
            'debt_subordinate_total': 88109.0, 'debt_liens': [], 'debt_liens_total': 0,
            'debt_total': 355260.56, 'value_low': 332360, 'value_high': None,
            'equity_low': -22900.56, 'equity_high': None,
            'fha_partial_claims': ['900000004', '900000005', '900000006'],
            'debt_note': 'Recorded and claimed debt. Interest and fees are additional.'}

    def bake(self, result=None, **entry):
        ent = {'verified': self.result if result is None else result,
               'last_attempt': self.result if result is None else result}
        ent.update(entry)
        row = copy.deepcopy(self.lead)
        A.attach([row], {'CACE26900001': ent}, now=self.now)
        row['eqc'] = E.card(row)
        return row

    def test_all_hud_claims_count_once(self):
        row = self.bake()
        self.assertEqual(row['eqstate'], 'priced')
        self.assertEqual(D.known_debt_of(row)['total'], 355260.56)
        self.assertEqual(row['eqc']['net'], -22900.56)
        self.assertTrue(row['eqc']['may_quote'])
        self.assertEqual(len(row['eqc']['liens']), 4)
        self.assertIn('interest', row['eqc']['why'].lower())

    def test_judgment_and_accrual_are_not_added_twice(self):
        self.lead.update(judg=280000, payoff=285000)
        row = self.bake()
        self.assertEqual(D.known_debt_of(row)['total'], 373109)
        self.assertEqual(row['orjuniors'], 88109)

    def test_missing_judgment_stays_do_not_quote(self):
        self.lead.update(judg=0, ju=True)
        row = self.bake()
        self.assertFalse(row['eqc']['may_quote'])
        self.assertIsNone(row['eqc']['net'])

    def test_verified_county_basis_and_handset_contract(self):
        import call_mode
        self.lead.update(value=500000, arv=550000, arvconf='ok')
        row = self.bake()
        self.assertEqual(row['eqc']['basis'], 332360)
        card = call_mode._eqc_slim(row['eqc'])
        self.assertEqual(card['n'], -22900.56)
        self.assertEqual(card['q'], 1)
        self.assertEqual(len(card['li']), 4)

    def test_nonmortgage_debt_is_counted_once(self):
        result = copy.deepcopy(self.result)
        result['debt_liens'] = [{'inst': 'tax-collector', 'kind': 'delinquent_taxes',
                                 'amount': 3200.5}]
        result.update(debt_liens_total=3200.5, debt_total=358461.06,
                      equity_low=-26101.06)
        row = self.bake(result)
        self.assertEqual(D.known_debt_of(row)['total'], 358461.06)
        self.assertEqual(row['orjuniors'] + row['orsurvsen'] + row['judg'], 358461.06)
        self.assertEqual(row['orhoa'] + row['orcode'] + row['orirs'], 0)

    def test_rejects_bad_evidence_even_when_legacy_was_priced(self):
        self.lead['eqstate'] = 'priced'
        changes = [
            {'case_key': 'CACE26999999'}, {'case_number': 'CACE26999999'},
            {'parcel': '000000000002'}, {'verdict': 'UNVERIFIED'},
            {'unknowns': ['unread lien']}, {'case_status': 'closed'},
            {'case_plaintiff': 'EXAMPLE HOMEOWNERS ASSOCIATION'},
            {'debt_total': 267151.56}, {'debt_first': None},
            {'debt_liens': [{'inst': '900000020', 'amount': None}]},
            {'debt_first': True}, {'debt_total': float('nan')},
            {'equity_low': float('nan')}, {'equity_low': None},
            {'last_checked': 'torn'},
            {'last_checked': (self.now - dt.timedelta(days=15)).isoformat()},
            {'last_checked': (self.now + dt.timedelta(days=1)).isoformat()},
            {'source_status': {'case': 'OK'}}, {'source_at': {}},
            {'source_at': {k: '2000-01-01' for k in A.REQUIRED}},
            {'fha_partial_claims': []},
        ]
        for change in changes:
            with self.subTest(change=change):
                result = dict(self.result, **change)
                row = self.bake(result)
                self.assertEqual(row['eqstate'], 'none')
                self.assertFalse(row['eqc']['may_quote'])

    def test_later_failure_is_not_hidden_by_saved_verified_result(self):
        row = self.bake(last_attempt=dict(self.result, verdict='UNVERIFIED'))
        self.assertFalse(row['eqc']['may_quote'])

    def test_unsupported_case_or_county_cannot_promote(self):
        original = copy.deepcopy(self.lead)
        for change in ({'ctype': 'HOA'}, {'county': 'MIAMI-DADE'}, {'st': 'TD'},
                       {'ctype': ''}, {'folio': ''}):
            with self.subTest(change=change):
                self.lead = dict(original)
                self.lead.update(change)
                self.assertFalse(self.bake()['eqc']['may_quote'])

    def test_missing_entry_leaves_legacy_row_unchanged(self):
        row = copy.deepcopy(self.lead)
        A.attach([row], {}, now=self.now)
        self.assertEqual(row, self.lead)

    def test_loader_missing_is_optional_but_torn_present_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / 'latest.json'
            self.assertEqual(A.load(p), {})
            p.write_text('{torn')
            with self.assertRaises(ValueError):
                A.load(p)
            p.write_text(json.dumps({'CACE26900001': {'verified': self.result,
                                                    'last_attempt': self.result}}))
            self.assertEqual(len(A.load(p)), 1)

    def test_local_cache_to_board_hook(self):
        import paths
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            cache = pathlib.Path(tmp) / 'deep_equity'
            cache.mkdir()
            (cache / 'latest.json').write_text(json.dumps({
                'CACE26900001': {'verified': self.result, 'last_attempt': self.result}}))
            rows = [copy.deepcopy(self.lead)]
            with patch.object(paths, 'DEALFLOW_DIR', tmp):
                A.attach(rows, A.load(), now=self.now)
            self.assertEqual(rows[0]['eqc']['net'], -22900.56)

    def test_actual_producer_record_load_and_handset(self):
        import deep_equity as producer
        import call_mode
        owner = 'EXAMPLE, PERSON'
        lead = dict(self.lead, owner=owner)
        legal = 'EXAMPLE ESTATES 1-2 B LOT 7 BLK 4'

        def instrument(inst, kind, holder, amount, party='FROM'):
            return {'Name': owner, 'Party': party, 'CrossPartyName': holder,
                    'RecordDate': '/Date(1609459200000)/', 'InstrumentNumber': inst,
                    'Consideration': amount, 'DocTypeDescription': kind,
                    'DocLegalDescription': legal, 'ParcelNumber': '000000000001'}

        records = [instrument('900000001', 'DEED', 'EXAMPLE SELLER LLC', 300000, 'TO'),
                   instrument('900000002', 'MORTGAGE', 'EXAMPLE BANK', 267151.56)]
        records += [instrument(i['inst'], 'MORTGAGE',
                    'SECRETARY OF HOUSING AND URBAN DEVELOPMENT', i['amount'])
                    for i in self.result['debt_subordinate']]
        sources = {
            'appraiser': producer.src('OK', {'market': 332360, 'legal': legal}),
            'case': producer.src('OK', {'case_number': lead['case'], 'status': 'active',
                                       'principal': 267151.56, 'plaintiff': 'EXAMPLE BANK'}),
            'records': producer.src('OK'), 'tax': producer.src('OK', {'due': 0}),
            'avm': producer.src('OK', {'value': 379400, 'src': 'synthetic estimate'})}
        result = producer.compute(lead, sources, {producer.search_name(owner): records})
        self.assertEqual(result['verdict'], 'VERIFIED')
        self.assertEqual(result['debt_total'], 355260.56)
        with tempfile.TemporaryDirectory() as tmp:
            producer.record(result, tmp)
            rows = [copy.deepcopy(self.lead)]
            A.attach(rows, A.load(pathlib.Path(tmp) / 'latest.json'))
        card = call_mode._eqc_slim(rows[0]['eqc'])
        self.assertEqual(card['n'], -22900.56)
        self.assertEqual(rows[0]['deep_equity']['equity_high'], 24139.44)

    def test_bake_wiring_precedes_diligence_and_publishing(self):
        src = pathlib.Path('foreclosure_leads.py').read_text()
        self.assertIn('_dea.attach(slim, _dea.load())', src)
        self.assertLess(src.index('_dea.attach(slim, _dea.load())'),
                        src.index('_DF_BAKE.annotate(_d)'))


if __name__ == '__main__':
    unittest.main()
