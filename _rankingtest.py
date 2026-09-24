"""Fresh facts and the evidence-qualified Miami list. Synthetic; no network; $0."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import miami_ranking as R
import paths as P

TODAY = date(2026, 9, 24)
CASE = '2025-000001-CA-01'


def timeline(kind='sale_scheduled', controlling='e7', stay=None, history=()):
    return {'status': {'kind': kind}, 'stay_in_effect': stay, 'stay_history': list(history),
            'judgments': {'controlling_entry': controlling}, 'gaps': []}


def present(persons=('JANE OWNER',), entities=(), status='candidate'):
    return {'ownership': {'status': status, 'grantees': list(persons) + list(entities)},
            'owner_persons': list(persons), 'owner_entities': [{'name': e} for e in entities],
            'held_because': []}


def facts(sale='2026-10-01', last_seen='2026-09-24', calendar='2026-09-24', tl=None, owner='JANE OWNER',
          owner_ts='2026-09-23', tax_checked='2026-09-20', trace_name='JANE OWNER', phones=('3055550100',),
          pres=None):
    tl = timeline() if tl is None else tl
    pres = present() if pres is None else pres
    grantees = pres['ownership']['grantees']
    return {'auction': R.auction_fact({'AuctionDate': sale}, {'last_seen': last_seen}, calendar, tl, TODAY),
            'appraiser': R.appraiser_fact({'title_status': 'clear', 'current_owner': owner, 'ts': owner_ts},
                                          grantees, TODAY),
            'tax': R.tax_fact({'due': 0, 'years': [], 'cert': None, 'checked': tax_checked}, TODAY),
            'contact': R.contact_fact({'name': trace_name, 'phones': list(phones), 'traced': '2026-09-01'},
                                      pres, TODAY)}


class AuctionTests(unittest.TestCase):
    def test_past_sale_date_is_never_a_sale(self):
        got = R.auction_fact({'AuctionDate': '09/10/2026'}, {'last_seen': '2026-09-09'}, '2026-09-24',
                             timeline(), TODAY)
        self.assertEqual(got['state'], 'past_date_outcome_unknown')

    def test_certificate_on_the_docket_is_the_only_sold(self):
        got = R.auction_fact({'AuctionDate': '09/10/2026'}, {}, '2026-09-24', timeline('sold'), TODAY)
        self.assertEqual(got['state'], 'sold_per_docket')

    def test_future_date_on_newest_calendar_is_scheduled(self):
        got = R.auction_fact({'AuctionDate': '10/01/2026'}, {'last_seen': '2026-09-24'}, '2026-09-24',
                             timeline(), TODAY)
        self.assertEqual((got['state'], got['days_to_sale']), ('scheduled', 7))

    def test_future_date_missing_from_calendar_is_unknown_not_cancelled(self):
        got = R.auction_fact({'AuctionDate': '10/01/2026'}, {'last_seen': '2026-09-20'}, '2026-09-24',
                             timeline(), TODAY)
        self.assertEqual(got['state'], 'dropped_from_calendar_unknown')

    def test_stale_calendar_and_stay(self):
        self.assertEqual(R.auction_fact({'AuctionDate': '10/01/2026'}, {'last_seen': '2026-09-20'},
                                        '2026-09-20', timeline(), TODAY)['state'], 'stale_calendar')
        self.assertEqual(R.auction_fact({'AuctionDate': '10/01/2026'}, {}, '2026-09-24',
                                        timeline(stay=True), TODAY)['state'], 'stayed')


class FactTests(unittest.TestCase):
    def test_appraiser_owner_compared_with_deed_grantee(self):
        same = R.appraiser_fact({'title_status': 'clear', 'current_owner': 'OWNER, JANE', 'ts': '2026-09-23'},
                                ['JANE OWNER'], TODAY)
        self.assertEqual((same['agrees_with_deed_grantee'], same['fresh']), ('agrees', True))
        other = R.appraiser_fact({'title_status': 'transferred', 'current_owner': 'NEW BUYER LLC',
                                  'ts': '2026-09-10'}, ['JANE OWNER'], TODAY)
        self.assertEqual((other['agrees_with_deed_grantee'], other['fresh']), ('differs', False))
        self.assertEqual(R.appraiser_fact(None, [], TODAY)['state'], 'not_checked')

    def test_tax_facts_carry_no_priority_conclusion(self):
        got = R.tax_fact({'due': 7614, 'years': [{'year': '2025'}], 'checked': '2026-09-01',
                          'cert': {'num': '252'}}, TODAY)
        self.assertEqual((got['state'], got['unpaid_years'], got['fresh']), ('certificate_sold', ['2025'], True))
        self.assertNotIn('survive', json.dumps(got).replace('No priority or survival', ''))
        self.assertFalse(R.tax_fact({'due': 0, 'checked': '2026-07-01'}, TODAY)['fresh'])

    def test_entity_only_owner_is_never_call_ready(self):
        got = R.contact_fact({'name': 'RICHARD ROE', 'phones': ['1'], 'traced': '2026-09-01',
                              'entity': 'EXAMPLE LLC'}, present((), ('EXAMPLE LLC',)), TODAY)
        self.assertEqual((got['state'], got['call_ready']), ('entity_only', False))

    def test_phone_must_belong_to_a_person_on_the_deed(self):
        ok = R.contact_fact({'name': 'OWNER, JANE', 'phones': ['1'], 'traced': '2026-09-01'}, present(), TODAY)
        self.assertEqual((ok['state'], ok['call_ready']), ('phone_for_person_on_deed', True))
        other = R.contact_fact({'name': 'SOMEONE ELSE', 'phones': ['1'], 'traced': '2026-09-01'}, present(), TODAY)
        self.assertEqual((other['state'], other['call_ready']), ('phone_for_name_not_on_deed', False))
        officer = R.contact_fact({'name': 'JANE OWNER', 'phones': ['1'], 'traced': '2026-09-01',
                                  'entity': 'X LLC'}, present(), TODAY)
        self.assertEqual(officer['state'], 'traced_entity_officer')
        stale = R.contact_fact({'name': 'JANE OWNER', 'phones': ['1'], 'traced': '2025-01-01'}, present(), TODAY)
        self.assertFalse(stale['call_ready'])


class RankingTests(unittest.TestCase):
    def item(self, case, **kw):
        tl = kw.pop('tl', timeline())
        pres = kw.pop('pres', present())
        return {'case': case, 'facts': facts(tl=tl, pres=pres, **kw), 'timeline': tl, 'present': pres}

    def test_fully_evidenced_cases_rank_by_sale_date_and_the_rest_are_held(self):
        rows = R.rank([self.item('C-late', sale='2026-10-20'), self.item('C-soon', sale='2026-09-30'),
                       self.item('C-past', sale='2026-09-01'),
                       self.item('C-entity', pres=present((), ('EXAMPLE LLC',)))], TODAY)
        self.assertEqual([(r['case'], r['rank']) for r in rows[:2]], [('C-soon', 1), ('C-late', 2)])
        held = {r['case']: r['held_because'] for r in rows if not r['qualified']}
        self.assertIn('auction: past date outcome unknown', held['C-past'])
        self.assertIn('contact: entity only', held['C-entity'])
        self.assertTrue(all(r['rank'] is None for r in rows if not r['qualified']))

    def test_each_missing_or_stale_fact_holds_the_case(self):
        rows = R.rank([self.item('A', owner_ts='2026-09-01'), self.item('B', tax_checked='2026-06-01'),
                       self.item('C', tl=timeline(controlling=None)),
                       self.item('D', pres=present(status='possibly_conveyed_later')),
                       self.item('E', owner='NEW BUYER')], TODAY)
        held = {r['case']: r['held_because'] for r in rows}
        self.assertEqual(held['A'], ['appraiser owner not read in the last 7 days'])
        self.assertEqual(held['B'], ['taxes not read in the last 30 days'])
        self.assertEqual(held['C'], ['no single controlling judgment'])
        self.assertEqual(held['D'], ['ownership: possibly conveyed later'])
        self.assertEqual(held['E'], ['appraiser owner vs deed grantee: differs'])

    def test_no_timeline_or_title_summary_holds(self):
        row = R.rank([{'case': 'X', 'facts': facts(), 'timeline': None, 'present': None}], TODAY)[0]
        self.assertIn('no whole-case timeline saved', row['held_because'])
        self.assertIn('no present-title summary (run title discovery)', row['held_because'])

    def test_changes_name_every_moved_fact(self):
        first = {'cases': R.rank([self.item('A', sale='2026-10-01'), self.item('B')], TODAY)}
        second = {'cases': R.rank([self.item('A', sale='2026-10-15'), self.item('C')], TODAY)}
        got = {(c['case'], c['field']) for c in R.changes(first, second)}
        self.assertIn(('A', 'sale_date'), got)
        self.assertIn(('C', 'case'), got)
        self.assertIn(('B', 'case'), got)


class MainTests(unittest.TestCase):
    def test_main_reads_caches_only_and_writes_a_report_with_changes(self):
        import document_store as DS
        import run_documents as RD
        with tempfile.TemporaryDirectory() as folder, patch.object(P, 'DEALFLOW_DIR', folder):
            dossier = Path(RD.dossier_path('MIAMI-DADE', CASE))
            dossier.parent.mkdir(parents=True, exist_ok=True)
            DS.pipeline_write(dossier, {'case': CASE})
            DS.pipeline_write(dossier.with_name(dossier.stem + '-timeline.json'), timeline())
            title = Path(folder) / 'title_discovery'
            title.mkdir()
            (title / (CASE + '.json')).write_text(json.dumps({'owner': 'JANE OWNER', 'present_title': present()}))
            reports = Path(folder) / 'reports'
            reports.mkdir()
            (reports / 'miami-ranking-2026-09-20.json').write_text(json.dumps({'cases': []}))
            sources = {'archive': {CASE: {'last_seen': str(date.today())}}, 'calendar_day': str(date.today()),
                       'ownership': {}, 'taxes': {}, 'traces': {}}
            lead = {'Case #': CASE, 'owner_clean': 'JANE OWNER', 'Folio': '3012345678901',
                    'AuctionDate': '12/31/2099'}
            with patch.object(R, 'load_sources', return_value=sources), \
                    patch.object(RD, '_lead_rows', return_value=[lead]), \
                    patch('ownership_gate.check_lead', side_effect=AssertionError('network')):
                self.assertEqual(R.main(['--all']), 0)
            out = json.loads((reports / ('miami-ranking-%s.json' % date.today())).read_text())
            self.assertEqual(out['summary'], {'cases': 1, 'qualified': 0, 'held': 1})
            self.assertEqual(out['previous'], '2026-09-20')
            self.assertEqual(out['changes'], [{'case': CASE, 'field': 'case', 'was': None, 'now': 'first seen'}])
            self.assertEqual(out['cases'][0]['facts']['auction']['state'], 'scheduled')
            self.assertIn('appraiser owner not read in the last 7 days', out['cases'][0]['held_because'])
            self.assertTrue((reports / ('miami-ranking-%s.md' % date.today())).is_file())


if __name__ == '__main__':
    unittest.main()
