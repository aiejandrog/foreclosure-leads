"""Document classification and the a/b/c/d case dossier.

SYNTHETIC FIXTURES ONLY. No homeowner data, no network. The point of most of these is negative:
what the pipeline must REFUSE to say. A document nobody read has no type; an index label is never
a substitute for reading; and reading a document never moves a lead into an equity FACT state.

Run:  python _dossiertest.py       (needs PyMuPDF)
"""
import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix='dealflow-dossiertest-')
os.environ['DEALFLOW_DIR'] = _TMP
os.environ.pop('ONEDRIVE', None)
os.environ.pop('OneDrive', None)

import case_dossier as CD               # noqa: E402
import document_classify as DC          # noqa: E402
import document_store as DS             # noqa: E402
import equity_state                     # noqa: E402
import run_documents as RD              # noqa: E402

CASE = '2026-000000-CA-01'


def reading_of(lines, source='embedded'):
    """A reading as document_store.read_pages would return it, one line per page."""
    return {'pages': [{'page': i + 1, 'text': line, 'chars': len(line),
                       'outcome': 'text' if source == 'embedded' else 'ocr_text',
                       'text_source': source}
                      for i, line in enumerate(lines)],
            'page_count': len(lines), 'pages_with_text': len(lines), 'pages_from_ocr': 0,
            'pages_unresolved': [], 'read_status': 'read', 'complete': True}


def unread(pages=5):
    return {'pages': [{'page': i + 1, 'text': '', 'outcome': 'needs_ocr',
                       'weak_reason': 'page is 91% raster image', 'text_source': None}
                      for i in range(pages)],
            'page_count': pages, 'pages_with_text': 0, 'pages_from_ocr': 0,
            'pages_unresolved': list(range(1, pages + 1)),
            'read_status': 'image_only', 'complete': False}


JUDGMENT = ['IN THE CIRCUIT COURT OF THE ELEVENTH JUDICIAL CIRCUIT IN AND FOR MIAMI-DADE',
            'SUMMARY FINAL JUDGMENT OF FORECLOSURE',
            'ORDERED AND ADJUDGED that there is due to plaintiff the total sum of $412,880.45',
            'for which let execution issue, and the clerk shall sell the property at public sale']

MORTGAGE = ['THIS MORTGAGE is made this 14th day of May between the Borrower and the Lender',
            'to secure the principal sum of $250,000.00 evidenced by a promissory note',
            'This Security Instrument covers the property described in Exhibit A']


class ClassifierTests(unittest.TestCase):
    def test_a_document_nobody_read_has_no_type(self):
        verdict = DC.classify(unread(), index_label='JUDGMENT')
        self.assertEqual(verdict['kind'], 'unknown')
        self.assertEqual(verdict['basis'], 'not_read')
        self.assertEqual(verdict['confidence'], 'none')

    def test_the_index_label_is_never_a_fallback(self):
        # THE rule. The clerk saying JUDGMENT does not make an unread PDF a judgment, and the
        # pilot proved the label lies in the other direction too.
        verdict = DC.classify(unread(), index_label='FINAL JUDGMENT OF FORECLOSURE')
        self.assertEqual(verdict['kind'], 'unknown')
        self.assertEqual(verdict['index_label_kind'], 'final_judgment')

    def test_a_judgment_is_recognised_from_its_own_text(self):
        verdict = DC.classify(reading_of(JUDGMENT), index_label='DADE COURT PAPER - DCP')
        self.assertEqual(verdict['kind'], 'final_judgment')
        self.assertIn(verdict['confidence'], ('high', 'medium'))
        self.assertTrue(verdict['evidence'])

    def test_the_pilots_mislabelled_judgment_is_reported_as_a_disagreement(self):
        # Indexed "DADE COURT PAPER - DCP"; the text says final judgment. The disagreement is the
        # finding, not an error.
        verdict = DC.classify(reading_of(JUDGMENT), index_label='DADE COURT PAPER - DCP')
        self.assertIsNone(verdict['index_label_kind'])
        self.assertIsNone(verdict['index_agrees'])

    def test_agreement_is_reported_when_the_label_is_right(self):
        verdict = DC.classify(reading_of(MORTGAGE), index_label='MORTGAGE')
        self.assertEqual(verdict['kind'], 'mortgage')
        self.assertTrue(verdict['index_agrees'])

    def test_a_judgment_that_quotes_a_mortgage_is_still_a_judgment(self):
        mixed = JUDGMENT + ['the mortgage recorded in Official Records Book 29001 Page 12']
        self.assertEqual(DC.classify(reading_of(mixed))['kind'], 'final_judgment')

    def test_a_satisfaction_outranks_the_mortgage_it_names(self):
        # Every satisfaction, assignment and judgment quotes the mortgage it acts on, so the
        # generic kind must not win on that reference.
        doc = ['THIS MORTGAGE is made between the Borrower and the Lender, principal sum',
               'SATISFACTION OF MORTGAGE: the debt is paid in full and the holder hereby cancels']
        self.assertEqual(DC.classify(reading_of(doc))['kind'], 'satisfaction_of_mortgage')

    def test_a_judgment_outranks_a_generic_order(self):
        self.assertEqual(DC.classify(reading_of(JUDGMENT + [
            'ORDER GRANTING the motion, hearing was held']))['kind'], 'final_judgment')

    def test_a_genuine_tie_is_unknown_not_a_coin_flip(self):
        # Both titles on page ONE, so neither gets the first-page advantage over the other.
        tie = {'pages': [{'page': 1, 'outcome': 'text', 'text_source': 'embedded',
                          'text': 'WARRANTY DEED\nNOTICE OF LIS PENDENS'}]}
        verdict = DC.classify(tie)
        self.assertEqual(verdict['kind'], 'unknown')
        self.assertIn('stands out', verdict['why'])

    def test_unrecognised_text_is_unknown_not_forced(self):
        verdict = DC.classify(reading_of(['a shopping list', 'more of the same']))
        self.assertEqual(verdict['kind'], 'unknown')

    def test_ocr_sourced_classification_is_flagged(self):
        verdict = DC.classify(reading_of(JUDGMENT, source='ocr'))
        self.assertEqual(verdict['kind'], 'final_judgment')
        self.assertTrue(verdict['from_ocr'])

    def test_each_instrument_type_is_recognised(self):
        cases = {
            'lis_pendens': ['NOTICE OF LIS PENDENS', 'notice is hereby given that an action has '
                            'been commenced against the defendants'],
            'satisfaction_of_mortgage': ['SATISFACTION OF MORTGAGE', 'the debt is paid in full '
                                         'and the holder hereby cancels the said mortgage'],
            'certificate_of_title': ['CERTIFICATE OF TITLE', 'no objections to the sale having '
                                     'been filed, the clerk sold the property to the bidder'],
            'hoa_lien': ['CLAIM OF LIEN', 'for unpaid assessments due to the condominium '
                         'association and maintenance fees now owing'],
            'federal_tax_lien': ['NOTICE OF FEDERAL TAX LIEN', 'the Internal Revenue Service '
                                 'reports an unpaid balance of assessments'],
            'deed': ['WARRANTY DEED', 'the grantor, in consideration of ten dollars, conveys and '
                     'warrants to the grantee the following described land'],
        }
        for expected, lines in cases.items():
            self.assertEqual(DC.classify(reading_of(lines))['kind'], expected, expected)


# Shaped on 50-2026-CA-000685 (Palm Beach, 2026-09-22): the satisfaction names the judgment it
# discharges, so its text carries "FINAL JUDGMENT" too. Synthetic wording, no party names.
SATISFACTION = ['IN THE CIRCUIT COURT OF THE FIFTEENTH JUDICIAL CIRCUIT IN AND FOR PALM BEACH',
                'SATISFACTION OF FINAL JUDGMENT',
                'Plaintiff acknowledges full payment and satisfaction of the Final Judgment of '
                'Foreclosure entered May 4, recorded in Official Records Book 36502 Page 322',
                'and directs the Clerk to cancel the same of record']


class SatisfactionOfJudgmentTests(unittest.TestCase):
    def test_a_satisfaction_that_quotes_its_judgment_is_a_satisfaction(self):
        self.assertEqual(DC.classify(reading_of(SATISFACTION))['kind'],
                         'satisfaction_of_judgment')

    def test_a_judgment_alone_is_still_a_judgment(self):
        self.assertEqual(DC.classify(reading_of(JUDGMENT))['kind'], 'final_judgment')

    def test_the_index_label_maps_to_the_satisfaction_not_the_judgment(self):
        self.assertEqual(DC.index_kind('SATISFACTION OF JUDGMENT'), 'satisfaction_of_judgment')
        self.assertEqual(DC.index_kind('FINAL JUDGMENT'), 'final_judgment')

    def _rows(self, with_satisfaction):
        rows = [{'source_ref': 'court:30', 'is': 'final_judgment',
                 'amounts': [{'amount': 993885.33}]}]
        if with_satisfaction:
            rows.append({'source_ref': 'court:40', 'is': 'satisfaction_of_judgment', 'amounts': []})
        return rows

    def test_a_partial_satisfaction_is_its_own_kind(self):
        # Greptile on #50: a partial release acknowledges a payment, not a discharge.
        lines = [l.replace('SATISFACTION OF FINAL', 'PARTIAL SATISFACTION OF FINAL')
                 .replace('full payment and satisfaction', 'partial payment') for l in SATISFACTION]
        self.assertEqual(DC.classify(reading_of(lines))['kind'], 'partial_satisfaction_of_judgment')

    def test_a_partial_satisfaction_keeps_the_amount_and_says_so(self):
        rows = self._rows(False) + [{'source_ref': 'court:41',
                                     'is': 'partial_satisfaction_of_judgment', 'amounts': []}]
        j = CD._operative_judgment(rows)
        self.assertEqual(j['amount'], 993885.33)
        self.assertFalse(j['satisfied'])
        self.assertEqual(j['partially_satisfied_by'], ['court:41'])


    def test_a_satisfied_judgment_carries_no_outstanding_amount(self):
        j = CD._operative_judgment(self._rows(True))
        self.assertIsNone(j['amount'])
        self.assertEqual(j['printed_amount'], 993885.33)
        self.assertTrue(j['satisfied'])
        self.assertEqual(j['satisfied_by'], ['court:40'])

    def test_an_unsatisfied_judgment_keeps_its_amount(self):
        j = CD._operative_judgment(self._rows(False))
        self.assertEqual(j['amount'], 993885.33)
        self.assertFalse(j['satisfied'])

    def test_a_satisfaction_without_its_judgment_is_still_reported(self):
        j = CD._operative_judgment(self._rows(True)[1:])
        self.assertIsNone(j['operative'])
        self.assertEqual(j['satisfied_by'], ['court:40'])
        self.assertIn('satisfaction of judgment WAS read', j['why'])


class CitedInstrumentTests(unittest.TestCase):
    """Chain-following starts here: an owner-name search never sees a lien recorded against a
    prior owner, but the document that references it does."""

    def test_book_and_page_references_are_collected(self):
        lines = ['that certain mortgage recorded in Official Records Book 29001, Page 1234',
                 'and the assignment recorded at O.R.B. 30111 PG 42 of the public records']
        cited = DC.cited_instruments(reading_of(lines))
        self.assertEqual([(c['book'], c['page_no']) for c in cited],
                         [('29001', '1234'), ('30111', '42')])
        self.assertTrue(all(c['fetched'] is False for c in cited))
        self.assertTrue(all(c['passage'] for c in cited))

    def test_the_same_reference_twice_is_one_candidate(self):
        lines = ['recorded in Book 29001 Page 1234', 'see Book 29001, Page 1234 again']
        self.assertEqual(len(DC.cited_instruments(reading_of(lines))), 1)

    def test_an_unread_document_cites_nothing(self):
        self.assertEqual(DC.cited_instruments(unread()), [])


class DossierTests(unittest.TestCase):
    CHAIN = {'conf': 'ok', 'nrec': 30, 'liens': [{'d': '2005-01-01', 'amt': 250000,
                                                  'party': 'A BANK', 'bp': '29001-1234',
                                                  'st': 'OPEN'}],
             'open_count': 1, 'surv': 250000, 'first_est': 250000, 'subdiv': 'SYNTHETIC ESTATES'}

    def docs(self, reading=None, **kw):
        row = {'source_ref': 'official_records/35287-4642', 'status': 'stored',
               'sha256': 'abc123', 'doc_type': 'DADE COURT PAPER - DCP', 'pages': 5,
               'page_count_verified': True, 'read_status': 'read', 'pages_unresolved': [],
               'amount_candidates': [{'amount': 412880.45, 'page': 3, 'passage': 'total sum',
                                      'text_source': 'embedded'}],
               'reading': reading if reading is not None else reading_of(JUDGMENT)}
        row.update(kw)
        return [row]

    def test_sections_come_out_in_order(self):
        d = CD.build(CASE, 'MIAMI-DADE', chain=self.CHAIN)
        self.assertEqual([k for k in d if k in CD.SECTIONS], list(CD.SECTIONS))

    def test_an_empty_c_says_so_and_d_is_the_index_verdict(self):
        d = CD.build(CASE, 'MIAMI-DADE', chain=self.CHAIN)
        self.assertEqual(d['c_documents']['status'], 'empty')
        self.assertEqual(d['d_picture']['rests_on'], ['b'])
        self.assertIn('index-based', d['d_picture']['documents_note'])
        self.assertIn('no document has been read', d['open_gaps'])

    def test_reading_a_document_does_not_move_the_equity_verdict(self):
        """The line this whole branch must not cross."""
        without = CD.build(CASE, 'MIAMI-DADE', chain=self.CHAIN)
        rows = CD.classify_documents(self.docs())
        with_docs = CD.build(CASE, 'MIAMI-DADE', chain=self.CHAIN, documents=rows)
        self.assertEqual(with_docs['d_picture']['eqstate'], without['d_picture']['eqstate'])
        self.assertEqual(with_docs['d_picture']['rests_on'], ['b'])
        self.assertIn('NOT folded into this verdict', with_docs['d_picture']['documents_note'])

    def test_document_evidence_alone_never_reaches_a_fact_state(self):
        rows = CD.classify_documents(self.docs())
        d = CD.build(CASE, 'MIAMI-DADE', chain=None, documents=rows)
        self.assertNotIn(d['d_picture']['eqstate'], equity_state.FACT)
        self.assertFalse(d['d_picture']['speakable_as_fact'])

    def test_c_carries_the_type_read_from_the_document_not_the_label(self):
        rows = CD.classify_documents(self.docs())
        row = CD.build(CASE, 'MIAMI-DADE', documents=rows)['c_documents']['documents'][0]
        self.assertEqual(row['is'], 'final_judgment')
        self.assertEqual(row['index_label'], 'DADE COURT PAPER - DCP')
        self.assertEqual(row['classified_from'], 'document_text')

    def test_an_unread_document_is_fetched_but_untyped(self):
        rows = CD.classify_documents(self.docs(reading=unread(), read_status='image_only',
                                               pages_unresolved=[1, 2, 3, 4, 5],
                                               amount_candidates=[]))
        d = CD.build(CASE, 'MIAMI-DADE', chain=self.CHAIN, documents=rows)
        self.assertEqual(d['c_documents']['status'], 'fetched_unread')
        self.assertEqual(d['c_documents']['documents'][0]['is'], 'unknown')
        self.assertEqual(d['c_documents']['fully_read'], 0)

    def test_gaps_name_an_unverified_page_count(self):
        rows = CD.classify_documents(self.docs(page_count_verified=False))
        d = CD.build(CASE, 'MIAMI-DADE', chain=self.CHAIN, documents=rows)
        self.assertTrue(any('page count never verified' in g for g in d['open_gaps']))
        self.assertFalse(d['complete'])

    def test_cited_instruments_surface_as_an_open_gap(self):
        lines = JUDGMENT + ['the mortgage recorded in Official Records Book 29001 Page 1234']
        rows = CD.classify_documents(self.docs(reading=reading_of(lines)))
        d = CD.build(CASE, 'MIAMI-DADE', chain=self.CHAIN, documents=rows)
        self.assertEqual(len(d['c_documents']['cited_but_not_fetched']), 1)
        self.assertTrue(any('cited by a read document' in g for g in d['open_gaps']))

    def test_the_conclusion_names_what_it_rests_on(self):
        rows = CD.classify_documents(self.docs())
        d = CD.build(CASE, 'MIAMI-DADE', chain=self.CHAIN, documents=rows)
        self.assertIn('final_judgment', d['conclusion'])
        self.assertIn('rests on b', d['conclusion'])

    def test_pagination_is_never_claimed_verified(self):
        d = CD.build(CASE, 'MIAMI-DADE',
                     inventory={'entries': [{'expected_documents': 1}] * 88,
                                'pagination_verified': False, 'judgment_candidates': []})
        self.assertEqual(d['a_filed']['docket_entries'], 88)
        self.assertFalse(d['a_filed']['pagination_verified'])
        self.assertTrue(any('may be incomplete' in g for g in d['open_gaps']))

    def test_nothing_established_is_said_plainly(self):
        d = CD.build(CASE, 'MIAMI-DADE')
        self.assertIn('nothing has been established', d['conclusion'])


class StageTests(unittest.TestCase):
    def test_the_stage_is_off_by_default_and_exits_clean(self):
        # A nightly line calling this must be a no-op, not a stage failure that reds the run.
        os.environ.pop('DEALFLOW_DOCS', None)
        self.assertEqual(RD.main([]), 0)

    def test_interpretation_without_a_cap_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            RD.main(['--enable', '--interpret'])
        self.assertEqual(caught.exception.code, 2)

    def test_a_token_budget_without_a_captcha_key_stops_before_any_case(self):
        import captcha_solver
        real = captcha_solver.has_key
        captcha_solver.has_key = lambda: False
        try:
            with self.assertRaises(SystemExit) as caught:
                RD.main(['--enable', '--token-budget', '5'])
            self.assertEqual(caught.exception.code, 2)
        finally:
            captcha_solver.has_key = real

    def test_dossiers_land_outside_the_repo_and_outside_onedrive(self):
        path = str(RD.dossier_path('MIAMI-DADE', CASE))
        self.assertTrue(path.startswith(_TMP))
        self.assertNotIn('OneDrive', path)
        self.assertFalse(path.startswith(os.path.dirname(os.path.abspath(__file__))))

    def test_a_hostile_case_number_cannot_escape_the_dossier_folder(self):
        path = str(RD.dossier_path('MIAMI-DADE', '../../../etc/passwd'))
        self.assertTrue(path.startswith(_TMP))
        self.assertNotIn('..', path)

    def test_only_miami_cases_are_picked(self):
        leads = [{'Case #': 'A', 'owner_clean': 'X Y', 'county': 'BROWARD'},
                 {'Case #': 'B', 'owner_clean': 'X Y', 'county': 'MIAMI-DADE'},
                 {'Case #': 'C', 'owner_clean': 'X Y'}]
        picked = [p['case'] for p in RD.pick_cases(leads, {}, 0)]
        self.assertEqual(sorted(picked), ['B', 'C'])

    def test_the_oldest_dossier_is_revisited_before_a_fresh_one(self):
        # Without this the same file-order head is re-read nightly and a satisfaction recorded
        # after the judgment is never seen on a case lower in the file.
        import time
        leads = [{'Case #': c, 'owner_clean': 'X Y', 'county': 'MIAMI-DADE'}
                 for c in ('OLD-1', 'NEW-2', 'NONE-3')]
        for case, age in (('OLD-1', 3600), ('NEW-2', 0)):
            path = RD.dossier_path('MIAMI-DADE', case)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
            stamp = time.time() - age
            os.utime(path, (stamp, stamp))
        self.assertEqual([p['case'] for p in RD.pick_cases(leads, {}, 0)],
                         ['NONE-3', 'OLD-1', 'NEW-2'])

    def test_the_nightly_summary_counts_satisfied_judgments_and_skips(self):
        read = {'case': 'A', 'open_gaps': [],
                'c_documents': {'fully_read': 2, 'documents': [],
                                'judgment': {'operative': 'x', 'candidates': ['x'],
                                             'satisfied_by': ['y']}}}
        skipped = {'case': 'B', 'open_gaps': ['owner_search: no cached search token'],
                   'c_documents': {'fully_read': 0, 'judgment': {},
                                   'documents': [{'status': 'skipped', 'reason': 'no token'}]}}
        s = RD.summarize([read, skipped])
        self.assertEqual((s['cases'], s['skipped_no_token'], s['judgment_found'],
                          s['judgment_satisfied'], s['complete']), (2, 1, 1, 1, 1))

    def test_a_lead_without_an_owner_is_skipped(self):
        leads = [{'Case #': 'A'}, {'owner_clean': 'X Y'}, {'Case #': 'B', 'owner_clean': 'X Y'}]
        self.assertEqual([p['case'] for p in RD.pick_cases(leads, {}, 0)], ['B'])

    def test_a_case_with_no_cached_token_is_skipped_not_paid_for(self):
        # --token-budget can now mint one on request. With no budget passed, the mint must not
        # be reached at all: this is the stage's only uninvited spend and a nightly runs it
        # over the whole backlog.
        import gen_records_qs as G
        real = G.mint_qs

        def never(lf):
            raise AssertionError('mint_qs reached with no --token-budget')
        G.mint_qs = never
        try:
            entry = {'case': CASE, 'owner': 'NOBODY CACHED', 'chain': None}
            dossier = RD.run_case(entry, {})
        finally:
            G.mint_qs = real
        row = dossier['c_documents']['documents'][0]
        self.assertEqual(row['source_ref'], 'owner_search')
        self.assertEqual(row['status'], 'skipped')
        self.assertIn('--token-budget is 0', row['reason'])
        # and the skip is visible as an open gap, not swallowed
        self.assertTrue(any('--token-budget is 0' in g for g in dossier['open_gaps']))


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    unittest.main(verbosity=2)
