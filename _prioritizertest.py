import unittest
from document_prioritizer import prioritize


CASE='2099-000001-CA-01'
def inventory(rows):
    return {'raw':{'caseNumber':CASE,'dockets':rows},
            'entries':[{'source_id':str(r['eventID']),'metadata':r,'expected_documents':1} for r in rows],
            'pagination_verified':False}
def row(i,text,date):
    return {'eventID':i,'docketDescrition':text,'eventDate':date,'comments':''}

class PrioritizerTests(unittest.TestCase):
    def test_service_of_judgment_is_not_itself_a_judgment(self):
        value=row(1,'Certificate of Service','01/01/2099')
        value['comments']='Final Judgment'
        plan=prioritize(CASE,inventory([value]),'2099-01-03')
        self.assertNotEqual(plan['documents'][0]['kind'],'final_judgment')

    def test_stay_before_judgments_and_recent_before_historical(self):
        value=prioritize(CASE,inventory([row(1,'Final Judgment','01/01/2098'),
            row(2,'Amended Final Judgment','01/01/2099'),row(3,'Suggestion of Bankruptcy','01/02/2099')]),'2099-01-03')
        self.assertEqual([x['entry_id'] for x in value['documents']],['3','2','1'])
        self.assertFalse(value['controlling_judgment_established'])
        self.assertFalse(value['coverage_complete'])

    def test_partial_party_list_does_not_exclude_judgment(self):
        source=inventory([row(1,'Final Judgment','01/01/2099')])
        source['raw']['parties']=[]
        self.assertEqual(prioritize(CASE,source,'2099-01-03')['documents'][0]['kind'],'final_judgment')

    def test_wrong_case_and_truncated_cache_rejected(self):
        source=inventory([row(1,'Final Judgment','01/01/2099')])
        with self.assertRaises(ValueError): prioritize('2099-000002-CA-01',source,'2099-01-03')
        source['entries']=[]
        with self.assertRaises(ValueError): prioritize(CASE,source,'2099-01-03')

    def test_future_unknown_dates_and_no_image_are_gaps_not_paid_targets(self):
        source=inventory([row(1,'Final Judgment','01/04/2099'),row(2,'Final Judgment','bad'),row(3,'Order Vacating Judgment','01/01/2099')])
        source['entries'][2]['expected_documents']=0
        value=prioritize(CASE,source,'2099-01-03')
        self.assertTrue(all(not x['eligible_for_acquisition'] for x in value['documents']))
        self.assertEqual(len(value['gaps']),4)

    def test_duplicate_ids_cannot_silently_overwrite_evidence(self):
        with self.assertRaises(ValueError): prioritize(CASE,inventory([row(1,'Order','01/01/2099'),row(1,'Other','01/02/2099')]),'2099-01-03')


# ---- paid-read order: selection BEFORE spending ------------------------------------------------
import document_prioritizer as DP
from unittest.mock import patch
from document_interpreter import BudgetExhausted

MIAMI = '2026-000123-CA-01'


def page(text):
    return {'pages': [{'page': 1, 'outcome': 'text', 'text': text}]}


def stored(ref, text, doc_type='JUDGMENT'):
    return {'source_ref': 'official_records/' + ref, 'status': 'stored', 'doc_type': doc_type,
            'reading': page(text), 'path': '/nonexistent/' + ref + '.pdf'}


def record(ref, when, doc_type='JUDGMENT'):
    book, pg = ref.split('-')
    return {'reC_BOOK': book, 'reC_PAGE': pg, 'reC_DATE': when, 'doC_TYPE': doc_type,
            'firsT_PARTY': 'OWNER', 'seconD_PARTY': 'BANK'}


def docket(case, rows):
    raw = {'caseNumber': case, 'dockets': rows, 'parties': []}
    return {'raw': raw, 'pagination_verified': False,
            'entries': [{'source_id': str(r['eventID']), 'source_ref': 'dockets/%d' % i,
                         'metadata': r, 'expected_documents': 1} for i, r in enumerate(rows)]}


# The shape of the pilot failure: the owner-name search hands back history first.
NAME_SEARCH = [
    ('100-1', '02/01/2011', 'FINAL JUDGMENT  Case No. 2010-044444-CA-01'),      # other action
    ('100-2', '06/01/2015', 'JUDGMENT for costs, no case number printed'),      # pre-case-year
    ('100-3', '03/01/2019', 'SUMMARY FINAL JUDGMENT Case No. 2018-011111-CC-05'),  # other action
    ('100-4', '09/01/2026', 'Satisfaction of mortgage'),                        # satisfaction
    ('100-5', '04/10/2026', 'FINAL JUDGMENT, no case number on the page'),     # tier 2
    ('100-6', '08/20/2026', 'FINAL JUDGMENT, no case number on the page'),     # tier 1
    ('100-7', '08/25/2026', 'FINAL JUDGMENT OF FORECLOSURE Case No. %s' % MIAMI),  # tier 0
]
DOCKET = [row(1, 'Complaint', '02/01/2026'), row(2, 'Final Judgment of Foreclosure', '08/15/2026'),
          row(3, 'Order Vacating Final Judgment', '08/30/2026')]


def name_search_rows(case=MIAMI):
    rows, records = [], []
    for ref, when, text in NAME_SEARCH:
        doc_type = 'SATISFACTION' if 'Satisfaction' in text else 'JUDGMENT'
        rows.append(stored(ref, text.replace(MIAMI, case), doc_type))
        records.append(record(ref, when, doc_type))
    return rows, records


class PaidReadOrderTests(unittest.TestCase):
    def test_name_search_history_is_never_bought_and_current_judgment_is_first(self):
        rows, records = name_search_rows()
        plan = prioritize(MIAMI, docket(MIAMI, DOCKET), '2026-09-23')
        got = DP.recorded_read_order(MIAMI, rows, records, plan)
        self.assertEqual([r['source_ref'][-5:] for r in got['order']], ['100-7', '100-6', '100-5'])
        self.assertEqual([r['tier'] for r in got['order']], [0, 1, 2])
        reasons = {d['source_ref'][-5:]: d['reason'] for d in got['deferred']}
        self.assertEqual(reasons, {'100-1': 'other_action', '100-2': 'recorded_before_case_year',
                                   '100-3': 'other_action', '100-4': 'satisfaction_read_free_only'})
        # The vacatur dated after the docket judgment is named for review, not applied.
        self.assertEqual(got['order'][1]['later_orders'][0]['kind'], 'vacatur')
        self.assertEqual(got['docket_judgment_dates'], ['2026-08-15'])

    def test_each_purchase_carries_what_the_docket_says_became_of_its_judgment(self):
        # Priority 3: 6828's shape. The first judgment is vacated and a replacement entered; the
        # plan names the replacement as the one operative judgment and labels each recording.
        rows_docket = DOCKET + [row(4, 'Final Judgment of Foreclosure', '09/10/2026')]
        plan = prioritize(MIAMI, docket(MIAMI, rows_docket), '2026-09-23')
        self.assertEqual(plan['judgments']['controlling_entry'], '4')
        rows = [stored('100-7', 'FINAL JUDGMENT Case No. %s' % MIAMI),
                stored('100-8', 'FINAL JUDGMENT Case No. %s' % MIAMI)]
        records = [record('100-7', '08/17/2026'), record('100-8', '09/12/2026')]
        got = DP.recorded_read_order(MIAMI, rows, records, plan)
        status = {r['source_ref'][-5:]: r['docket_judgment_status'] for r in got['order']}
        self.assertEqual(status, {'100-7': 'vacated', '100-8': 'operative'})
        self.assertEqual(got['order'][0]['source_ref'][-5:], '100-8')

    def test_without_a_docket_plan_nothing_is_tier_one(self):
        rows, records = name_search_rows()
        got = DP.recorded_read_order(MIAMI, rows, records, None)
        self.assertEqual([r['tier'] for r in got['order']], [0, 2, 2])
        self.assertEqual(got['order'][1]['source_ref'][-5:], '100-6')   # newest first in a tier

    def test_unstored_row_is_deferred_not_priced(self):
        got = DP.recorded_read_order(MIAMI, [{'source_ref': 'official_records/1-1',
                                              'status': 'gap'}], [], None)
        self.assertEqual(got['deferred'][0]['reason'], 'no_stored_reading')
        self.assertEqual(got['order'], [])

    def test_timeline_order_follows_the_docket_plan(self):
        plan = prioritize(MIAMI, docket(MIAMI, DOCKET), '2026-09-23')
        rows = [{'source_ref': 'court:%d:0' % i, 'entry_ref': str(i)} for i in (1, 2, 3, 9)]
        got = DP.timeline_read_order(plan, rows)
        self.assertEqual([r['entry_ref'] for r in got['order']], ['3', '2', '1'])
        self.assertEqual(got['deferred'][0]['reason'], 'not_in_docket_plan')


class FakeCollector:
    def __init__(self, case):
        self.case = case

    def enumerate_documents(self, case):
        return docket(case, [dict(r) for r in DOCKET])


class NoIndex:
    def __init__(self, *a, **k):
        pass

    def add_models(self, models):
        return 0


def fake_vision(bought):
    """Stands in for miami_judgment.vision_candidates: one page, priced like a real page, through
    the budget it is given. Records which document the money went to."""
    def read(path, reading, budget, reader=None, out_dir=None):
        try:
            budget.check(1000, 2000)
        except BudgetExhausted as stop:
            return [], {'figures': [], 'grand_totals': [], 'pages': {}, 'usd': 0.0,
                        'errors': {1: 'budget: %s' % stop}}
        budget.record(1000, 100)
        bought.append(path)
        return [], {'figures': [], 'grand_totals': [], 'pages': {1: {'usd': .0075}},
                    'usd': .0075, 'errors': {}}
    return read


class FiveCaseReplayTests(unittest.TestCase):
    """The acceptance shape of priority 1, on synthetic evidence shaped like the pilot: five cases,
    each owner-name search returning history ahead of the current judgment, one shared $0.20 cap.
    The real replay over the saved pilot evidence is replay_paid_selection.py on the desktop."""

    CASES = ['2026-00012%d-CA-01' % i for i in range(1, 6)]

    def run_cases(self, budget_for, finish=lambda case: None):
        import miami_judgment as MJ
        bought = []
        with patch('document_walk.RecordIndex', NoIndex), \
             patch.object(MJ, 'vision_candidates', side_effect=fake_vision(bought)):
            for case in self.CASES:
                rows, records = name_search_rows(case)
                with patch.object(MJ, 'collect_recorded', return_value=rows):
                    report = MJ.run(case, records, collector=FakeCollector(case),
                                    vision_budget=budget_for(case), as_of='2026-09-23')
                finish(case)
                yield case, report, list(bought)
                bought.clear()

    def test_old_shape_first_case_takes_everything(self):
        """What the pilot did: no selection, one shared budget. Reproduced by walking the rows in
        search order through a plain Budget."""
        from document_interpreter import Budget
        shared = Budget(0.20)
        order = []
        stopped = False
        for case in self.CASES:
            rows, _ = name_search_rows(case)
            for r in rows:
                for _page in range(3):          # the pilot's judgment read was three pages
                    try:
                        shared.check(1000, 2000)
                    except BudgetExhausted:
                        stopped = True
                        break
                    shared.record(1000, 100)
                    order.append((case, r['source_ref'][-5:]))
                if stopped:
                    break
            if stopped:
                break
        self.assertTrue(stopped)
        self.assertTrue(all(case == self.CASES[0] for case, _ in order))     # four cases got $0
        self.assertNotIn('100-7', [ref for _, ref in order[:18]])       # history bought first

    def test_every_case_reads_its_current_judgment_first_within_its_share(self):
        import document_backfill as BF
        import document_case_budget as CB
        # $0.30 over five cases: a $0.06 share, and each page is checked at $0.055 worst case.
        alloc = CB.CaseAllocator(BF.PersistentBudget(0.30, CB.MemoryState()), self.CASES)
        for case, report, bought in self.run_cases(alloc.for_case, alloc.finish):
            self.assertTrue(bought, case)
            self.assertTrue(bought[0].endswith('100-7.pdf'), (case, bought))
            self.assertFalse(any(b.endswith(('100-1.pdf', '100-2.pdf', '100-3.pdf', '100-4.pdf'))
                                 for b in bought), (case, bought))
            vision = report['vision']
            self.assertEqual(len(vision['selection']['deferred']), 4)
            if case == self.CASES[0]:
                # First case, nothing to borrow yet: one page, then its share is spent and the
                # rest of its selection is a named gap instead of another case's money.
                self.assertEqual(len(bought), 1)
                self.assertEqual(vision['not_read_budget'], ['official_records/100-5'])
                row = [r for r in report['documents'] if r['source_ref'].endswith('100-5')][0]
                self.assertIn('budget_exhausted', row['vision_errors']['*'])
            else:
                # Later cases may borrow what FINISHED cases left, so they reach further.
                self.assertEqual(len(bought), 3)
        self.assertLessEqual(alloc.budget.spent, 0.30 + 1e-9)


if __name__=='__main__': unittest.main()
