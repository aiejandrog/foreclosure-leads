import unittest
import miami_case_timeline as T


def entry(n, text, date=None, **meta):
    return {'source_id': str(n), 'expected_documents': 0, 'metadata': dict(
        eventID=n, eventDate=date or '09/%02d/2026' % n,
        docketDescrition=text, **meta)}


def run(entries, docs=()):
    return T.build_timeline('SYNTHETIC', {'entries': entries, 'pagination_verified': True}, docs, '2026-09-23')


class TimelineTests(unittest.TestCase):
    def test_dismissal_after_judgment(self):
        self.assertEqual(run([entry(1, 'Final Judgment'), entry(2, 'Order of dismissal')])['status']['kind'], 'dismissed')

    def test_stay_then_relief_restores_previous_status(self):
        r = run([entry(1, 'Final Judgment'), entry(2, 'Suggestion of Bankruptcy'), entry(3, 'Order granting relief from bankruptcy stay')])
        self.assertEqual(r['status']['kind'], 'judgment_entered')
        self.assertEqual(r['status']['evidence'], ['1', '3'])

    def test_cancellation_then_reset(self):
        r = run([entry(1, 'Notice of sale on 09/20/2026'), entry(2, 'Order cancelling sale'), entry(3, 'Order resetting sale to 10/15/2026')])
        self.assertEqual(r['status']['kind'], 'sale_scheduled')
        self.assertEqual(r['status']['sale_date'], '2026-10-15')

    def test_satisfaction_after_judgment(self):
        self.assertEqual(run([entry(1, 'Final Judgment'), entry(2, 'Satisfaction of Judgment')])['status']['kind'], 'satisfied_redeemed')

    def test_same_day_conflict_no_invented_order(self):
        r = run([entry(1, 'Order of dismissal', '09/01/2026'), entry(2, 'Final Judgment', '09/01/2026')])
        self.assertEqual(r['status']['kind'], 'unclear')
        self.assertEqual(set(r['status']['evidence']), {'1', '2'})

    def test_motion_is_not_disposition(self):
        r = run([entry(1, 'Final Judgment'), entry(2, 'Motion to dismiss')])
        self.assertEqual(r['status']['kind'], 'judgment_entered')
        self.assertEqual(r['pending'][0]['entry_id'], '2')

    def test_body_overrides_index(self):
        r = run([entry(1, 'Final Judgment')], [{'entry_ref': '1', 'reading': {'pages': [{'page': 1, 'text': 'NOTICE OF VOLUNTARY DISMISSAL\nPlaintiff dismisses this action.', 'outcome': 'ocr_text'}]}}])
        self.assertEqual(r['entries'][0]['kind'], 'notice_of_voluntary_dismissal')
        self.assertFalse(r['entries'][0]['index_agrees'])

    def test_order_denies_dismissal_does_not_dismiss(self):
        r = run([entry(1, 'Complaint'), entry(2, 'Motion to dismiss'), entry(3, 'Order denying motion to dismiss')])
        self.assertEqual(r['status']['kind'], 'active_pre_judgment')
        self.assertFalse([p for p in r['pending'] if p['type'] == 'motion'])

    def test_future_judgment_does_not_decide_today(self):
        self.assertEqual(run([entry(1, 'Complaint'), entry(2, 'Final Judgment', '10/01/2026')])['status']['kind'], 'active_pre_judgment')

    def test_unknown_dates_and_missing_images_named(self):
        e = entry(1, 'Order of dismissal', 'bad-date'); e['expected_documents'] = 1
        r = run([e])
        self.assertEqual(r['status']['kind'], 'unclear')
        self.assertTrue(any(g.get('entry_id') == '1' for g in r['gaps']))

    def test_no_image_and_filer_remain_explicit(self):
        r = run([entry(1, 'Complaint', partiesName='Synthetic Owner')])
        self.assertEqual(r['entries'][0]['filed_by'], 'unknown')
        self.assertEqual(r['entries'][0]['image_status'], 'no_image_indexed')
        self.assertIn('Complaint', T.render_markdown(r))

    def test_amounts_are_unverified_not_equity(self):
        r = run([entry(1, 'Final Judgment')], [{'entry_ref': '1', 'reading': {'pages': [{'page': 1, 'text': 'FINAL JUDGMENT\nTotal $1,234.50', 'outcome': 'ocr_text'}]}}])
        self.assertEqual(r['amounts'][0]['amount'], '1234.50')
        self.assertEqual(r['amounts'][0]['verification'], 'unverified_extraction')

    def test_partial_dispositions_do_not_resolve_case(self):
        for text in ('Satisfaction of mortgage', 'Notice of voluntary dismissal as to Defendant A only', 'Order dismissing one defendant'):
            with self.subTest(text=text):
                self.assertEqual(run([entry(1, 'Final Judgment'), entry(2, text)])['status']['kind'], 'judgment_entered')

    def test_denied_stay_relief_preserves_stay(self):
        r = run([entry(1, 'Suggestion of Bankruptcy'), entry(2, 'Order denying relief from bankruptcy stay')])
        self.assertEqual(r['status']['kind'], 'stayed_by_bankruptcy')

    def test_unrelated_order_does_not_close_motion(self):
        r = run([entry(1, 'Motion to dismiss'), entry(2, 'Order setting hearing on motion to dismiss')])
        self.assertEqual(len([p for p in r['pending'] if p['type'] == 'motion']), 1)

    def test_court_source_ref_and_multiline_heading(self):
        r = run([entry(1, 'Final Judgment')], [{'source_ref': 'court:1:9', 'reading': {'pages': [{'page': 1, 'text': 'MOTION TO\nCANCEL SALE\nExhibit FINAL JUDGMENT', 'outcome': 'ocr_text'}]}}])
        self.assertEqual(r['entries'][0]['kind'], 'motion')

    def test_missing_second_attachment_prevents_read_status(self):
        e = entry(1, 'Complaint'); e['expected_documents'] = 2
        r = run([e], [{'entry_ref': '1', 'reading': {'pages': [{'page': 1, 'text': 'COMPLAINT', 'outcome': 'ocr_text'}]}}])
        self.assertNotEqual(r['entries'][0]['image_status'], 'read')

    def test_index_partial_scope_not_lost_by_document_title(self):
        r = run([entry(1, 'Final Judgment'), entry(2, 'Notice of voluntary dismissal as to Defendant A only')], [{'entry_ref': '2', 'reading': {'pages': [{'page': 1, 'text': 'NOTICE OF VOLUNTARY DISMISSAL\nAs to Defendant A only.', 'outcome': 'ocr_text'}]}}])
        self.assertEqual(r['status']['kind'], 'judgment_entered')

    def test_same_document_multiple_aliases_not_counted_twice(self):
        d = {'entry_ref': '1', 'event_id': '1', 'source_ref': 'court:1:9', 'reading': {'pages': [{'page': 1, 'text': 'FINAL JUDGMENT\nTotal $1.00', 'outcome': 'ocr_text'}]}}
        self.assertEqual(len(run([entry(1, 'Final Judgment')], [d])['amounts']), 1)

    def test_body_title_keeps_index_sale_date(self):
        d = {'entry_ref': '1', 'reading': {'pages': [{'page': 1, 'text': 'NOTICE OF SALE\nSigned 09/01/2026', 'outcome': 'ocr_text'}]}}
        self.assertEqual(run([entry(1, 'Notice of Sale', comments='SALE OF 10/5/2026')], [d])['status']['sale_date'], '2026-10-05')

    def test_unrelated_complaint_does_not_reset_judgment(self):
        self.assertEqual(run([entry(1, 'Final Judgment'), entry(2, 'Amended complaint')])['status']['kind'], 'judgment_entered')

    def test_sale_notice_cannot_silently_lift_stay(self):
        r = run([entry(1, 'Suggestion of bankruptcy'), entry(2, 'Notice of sale on 10/05/2026')])
        self.assertEqual(r['status']['kind'], 'unclear')
        self.assertEqual(r['status']['evidence'], ['1', '2'])

    def test_cancelling_order_closes_cancel_motion(self):
        r = run([entry(1, 'Motion to cancel sale'), entry(2, 'Order cancelling sale')])
        self.assertFalse([x for x in r['pending'] if x['type'] == 'motion'])

    def test_provenance_and_actual_acquisition_gap_preserved(self):
        d = {'entry_ref': '1', 'source_ref': 'court:1:9', 'manifest': {'source_sha256': 'abc'}, 'reading': {'pages': [{'page': 1, 'text': 'FINAL JUDGMENT\n$1.00', 'outcome': 'ocr_text'}]}}
        r = run([entry(1, 'Final Judgment')], [d])
        self.assertEqual(r['amounts'][0]['source_ref'], 'court:1:9')
        self.assertEqual(r['amounts'][0]['document_hash'], 'abc')
        r = run([entry(1, 'Complaint')], [{'entry_ref': '1', 'acquisition_gap': 'HTTP 403 county restriction'}])
        self.assertTrue(any('HTTP 403' in g['reason'] for g in r['gaps']))

    def test_written_month_reset_uses_new_not_cancelled_date(self):
        r = run([entry(1, 'Order cancelling and resetting sale', comments='SALE DATE AUGUST 24, 2026 CANCELLED; RESET FOR SEPTEMBER 23, 2026')])
        self.assertEqual(r['status']['sale_date'], '2026-09-23')

    def test_unassessed_pages_are_named_even_if_present_pages_read(self):
        d = {'entry_ref': '1', 'manifest': {'pages': 3}, 'reading': {'pages': [{'page': 1, 'text': 'COMPLAINT', 'outcome': 'ocr_text'}]}}
        r = run([entry(1, 'Complaint')], [d])
        self.assertTrue(any(g['kind'] == 'unassessed_pages' and g['pages'] == [2, 3] for g in r['gaps']))
        self.assertNotEqual(r['entries'][0]['image_status'], 'read')

    def test_inventory_pagination_unknown_remains_gap(self):
        r = T.build_timeline('SYNTHETIC', {'entries': [entry(1, 'Complaint')], 'pagination_verified': False}, [], '2026-09-23')
        self.assertTrue(any(g['kind'] == 'inventory_completeness_unknown' for g in r['gaps']))

    def test_notice_body_prose_does_not_become_judgment(self):
        d = {'entry_ref': '1', 'reading': {'pages': [{'page': 1, 'text': 'NOTICE IS HEREBY GIVEN pursuant to Final Judgment entered previously.', 'outcome': 'ocr_text'}]}}
        self.assertEqual(run([entry(1, 'Notice of Sale')], [d])['entries'][0]['kind'], 'notice_of_sale')

    def test_multiline_reset_heading(self):
        d = {'entry_ref': '1', 'reading': {'pages': [{'page': 1, 'text': 'ORDER GRANTING MOTION TO CANCEL AND RESCHEDULE\nFORECLOSURE SALE\nThe sale is reset for September 23, 2026.', 'outcome': 'ocr_text'}]}}
        self.assertEqual(run([entry(1, 'Order')], [d])['entries'][0]['kind'], 'order_resetting_sale')

    def test_calendar_and_response_not_pending_motion(self):
        r = run([entry(1, 'Motion to dismiss', eventType='Hearing'), entry(2, 'Response in opposition to motion to dismiss')])
        self.assertFalse([p for p in r['pending'] if p['type'] == 'motion'])

    def test_fee_order_closes_fee_motion(self):
        r = run([entry(1, 'Motion for attorney fees'), entry(2, 'Order awarding attorney fees')])
        self.assertFalse([p for p in r['pending'] if p['type'] == 'motion'])

    def test_final_judgment_closes_only_explicitly_granted_motion(self):
        d = {'entry_ref': '3', 'reading': {'pages': [{'page': 1, 'text': 'FINAL JUDGMENT\nPlaintiff motion for summary judgment is granted.', 'outcome': 'ocr_text'}]}}
        r = run([entry(1, 'Motion for summary judgment'), entry(2, 'Motion for default'), entry(3, 'Final Judgment')], [d])
        self.assertEqual([p['entry_id'] for p in r['pending'] if p['type'] == 'motion'], ['2'])

    def test_notice_hearing_with_motion_topic_not_motion(self):
        self.assertEqual(T.classify('Notice of Hearing on Motion for Summary Judgment'), 'hearing')
        self.assertEqual(T.classify("NOTICE OF SPECIAL SET HEARING MOTION FOR ATTORNEY'S FEES AND COSTS"), 'hearing')
        self.assertEqual(T.classify('Request for Hearing on Motion to dismiss'), 'hearing')

    def test_notice_judicial_sale_and_short_year(self):
        r = run([entry(1, 'Notice of Judicial Sale by the Clerk', comments='SALE OF 9/23/26')])
        self.assertEqual(r['status']['kind'], 'sale_scheduled')
        self.assertEqual(r['status']['sale_date'], '2026-09-23')
        self.assertIn('Sale date: 2026-09-23', T.render_markdown(r))

    def test_plural_attorneys_fees_resolves(self):
        r = run([entry(1, 'Motion for Attorneys Fees'), entry(2, 'Order Awarding Attorney Fees')])
        self.assertFalse([p for p in r['pending'] if p['type'] == 'motion'])

    def test_mentions_do_not_create_disposition(self):
        for text in ('Objection to Certificate of Sale', 'Appeal of Final Judgment', 'Response concerning Certificate of Title'):
            with self.subTest(text=text):
                self.assertEqual(run([entry(1, 'Final Judgment'), entry(2, text)])['status']['kind'], 'judgment_entered')

    def test_vacatur_not_judgment_entered(self):
        self.assertEqual(run([entry(1, 'Final Judgment'), entry(2, 'Order vacating final judgment')])['status']['kind'], 'unclear')

    def test_discovery_stay_not_bankruptcy(self):
        self.assertEqual(run([entry(1, 'Complaint'), entry(2, 'Order staying discovery')])['status']['kind'], 'active_pre_judgment')

    def test_dismissal_count_not_whole_case(self):
        self.assertEqual(run([entry(1, 'Final Judgment'), entry(2, 'Notice of voluntary dismissal of Count II')])['status']['kind'], 'judgment_entered')

    def test_partial_stay_relief_does_not_clear_stays(self):
        r = run([entry(1, 'Final Judgment'), entry(2, 'Suggestion of bankruptcy'), entry(3, 'Order granting partial relief from bankruptcy stay')])
        self.assertEqual(r['status']['kind'], 'unclear')

    def test_ambiguous_multiple_motions_not_all_closed(self):
        r = run([entry(1, 'Motion to dismiss by Defendant A'), entry(2, 'Motion to dismiss by Defendant B'), entry(3, 'Order denying motion to dismiss by Defendant A')])
        self.assertEqual([p['entry_id'] for p in r['pending'] if p['type'] == 'motion'], ['1', '2'])

    def test_unreadable_page_gap_preserves_evidence_reason(self):
        d = {'entry_ref': '1', 'source_ref': 'court:1:9', 'manifest': {'source_sha256': 'abc'}, 'reading': {'pages': [{'page': 1, 'outcome': 'unreadable_source', 'assessment': {'reason': 'County image blacked out'}}]}}
        r = run([entry(1, 'Final Judgment')], [d])
        gap = next(g for g in r['gaps'] if g.get('page') == 1)
        self.assertEqual(gap['reason'], 'County image blacked out')
        self.assertEqual(gap['document_hash'], 'abc')
        self.assertEqual(gap['source_ref'], 'court:1:9')
        self.assertEqual(gap['outcome'], 'unreadable_source')

    def test_denied_vacatur_leaves_judgment(self):
        self.assertEqual(run([entry(1, 'Final Judgment'), entry(2, 'Order denying motion to vacate final judgment')])['status']['kind'], 'judgment_entered')

    def test_motion_requesting_order_is_not_order(self):
        for text in ('Motion for order of dismissal', 'Emergency motion for order of dismissal', 'Amended motion for order of dismissal', 'Renewed motion for order of dismissal'):
            with self.subTest(text=text):
                self.assertEqual(run([entry(1, 'Final Judgment'), entry(2, text)])['status']['kind'], 'judgment_entered')


class VacaturTests(unittest.TestCase):
    def test_an_order_vacating_a_certificate_is_not_a_sale(self):
        # Greptile on #50, 2026-09-23.
        for title in ('Order vacating certificate of title', 'ORDER VACATING CERTIFICATE OF SALE',
                      'Order setting aside foreclosure sale'):
            self.assertEqual(T.classify(title), 'vacatur', title)
        self.assertEqual(T.classify('Certificate of title'), 'certificate_of_title')



# ---- priority 3: amendment, vacatur, satisfaction and stay reconciliation (2026-09-24) -----------
def run_with_parties(entries, defendants, docs=()):
    parties = [{'partyName': 'BANK OF EXAMPLE NA', 'partyTypeDesc': 'PLAINTIFF'}] + [
        {'partyName': d, 'partyTypeDesc': 'DEFENDANT'} for d in defendants]
    return T.build_timeline('SYNTHETIC', {'entries': entries, 'pagination_verified': True,
                                          'raw': {'parties': parties}}, docs, '2026-09-23')


class ReconciliationTests(unittest.TestCase):
    """Shapes of the pilot's 6828 (replacement judgment) and McCray (reinstated stay) cases, and
    defendant-specific dismissals. Synthetic; no homeowner data."""

    def test_an_amended_judgment_replaces_the_one_it_amends(self):
        r = run([entry(1, 'Final Judgment of Foreclosure'),
                 entry(2, 'Amended Final Judgment of Foreclosure')])
        j = {x['entry_id']: x for x in r['judgments']['judgments']}
        self.assertEqual(j['1']['status'], 'superseded')
        self.assertEqual(j['2']['replaces'], '1')
        self.assertEqual(r['judgments']['controlling_entry'], '2')

    def test_a_vacated_judgment_and_its_replacement(self):
        r = run([entry(1, 'Final Judgment of Foreclosure', '03/01/2026'),
                 entry(2, 'Order vacating final judgment entered 03/01/2026', '05/01/2026'),
                 entry(3, 'Final Judgment of Foreclosure', '07/01/2026')])
        j = {x['entry_id']: x for x in r['judgments']['judgments']}
        self.assertEqual(j['1']['status'], 'vacated')
        self.assertIn('03/01', j['1']['reason'].replace('2026-03-01', '03/01'))
        self.assertEqual(j['3']['status'], 'operative')
        self.assertEqual(r['judgments']['controlling_entry'], '3')
        self.assertEqual(r['status']['kind'], 'judgment_entered')

    def test_newest_is_not_controlling_when_nothing_links_two_judgments(self):
        r = run([entry(1, 'Final Judgment of Foreclosure'), entry(2, 'Final Judgment')])
        self.assertIsNone(r['judgments']['controlling_entry'])
        self.assertEqual({x['status'] for x in r['judgments']['judgments']}, {'unclear'})

    def test_a_vacatur_that_names_no_judgment_decides_nothing_when_there_are_two(self):
        r = run([entry(1, 'Final Judgment', '03/01/2026'),
                 entry(2, 'Amended Final Judgment', '04/01/2026'),
                 entry(3, 'Final Judgment', '05/01/2026'),
                 entry(4, 'Order vacating judgment', '06/01/2026')])
        self.assertIsNone(r['judgments']['controlling_entry'])

    def test_a_supplemental_fee_judgment_adds_and_replaces_nothing(self):
        r = run([entry(1, 'Final Judgment of Foreclosure'),
                 entry(2, "Supplemental Final Judgment for Attorney's Fees and Costs")])
        j = {x['entry_id']: x for x in r['judgments']['judgments']}
        self.assertEqual(j['1']['status'], 'operative')
        self.assertEqual(j['2']['adds_to'], '1')
        self.assertEqual(r['judgments']['controlling_entry'], '1')

    def test_no_satisfaction_found_is_not_an_open_balance(self):
        r = run([entry(1, 'Final Judgment')])
        [j] = r['judgments']['judgments']
        self.assertEqual(j['satisfaction'], 'no_satisfaction_found')
        self.assertIn('not proof of an open balance', r['judgments']['qualification'])

    def test_a_satisfaction_marks_the_judgment(self):
        r = run([entry(1, 'Final Judgment'), entry(2, 'Satisfaction of Judgment')])
        [j] = r['judgments']['judgments']
        self.assertEqual((j['status'], j['satisfaction']), ('satisfied', 'satisfied'))

    def test_a_reinstated_stay_is_a_stay_again(self):
        # McCray's shape: stayed, relief granted, then the stay reinstated. Reading the
        # reinstatement as relief restored the judgment as if nothing stood in the way.
        r = run([entry(1, 'Final Judgment'), entry(2, 'Suggestion of Bankruptcy'),
                 entry(3, 'Order granting relief from bankruptcy stay'),
                 entry(4, 'Order vacating order granting relief from stay')])
        self.assertEqual(r['entries'][3]['kind'], 'stay_reinstated')
        self.assertEqual(r['status']['kind'], 'stayed_by_bankruptcy')
        self.assertTrue(r['stay_in_effect'])
        self.assertEqual([h['event'] for h in r['stay_history']], ['stayed', 'relief', 'reinstated'])

    def test_a_reinstatement_inside_a_bankruptcy_filing_body_is_read(self):
        # McCray as it really is (desktop replay, 2026-09-24): the docket title says "Suggestion
        # of Bankruptcy"; the bankruptcy court's order reinstating the stay is on pages 3-4.
        body = {'source_ref': 'court:3:1', 'entry_ref': '3', 'reading': {'pages': [
            {'page': 1, 'outcome': 'text', 'text': 'SUGGESTION OF BANKRUPTCY'},
            {'page': 3, 'outcome': 'text', 'text': 'ORDER GRANTING MOTION TO REINSTATE AUTOMATIC STAY\n'
                                                   'ORDERED that the automatic stay under 11 U.S.C. 362(a)\n'
                                                   'is hereby REINSTATED as to the Debtor.'}]}}
        r = run([entry(1, 'Final Judgment'), entry(2, 'Suggestion of Bankruptcy'),
                 entry(3, 'Suggestion of Bankruptcy', expected=1)], [body])
        e3 = r['entries'][2]
        self.assertEqual((e3['kind'], e3['kind_source']), ('stay_reinstated', 'document_passage'))
        self.assertEqual(e3['stay_passages'][0]['page'], 3)
        self.assertIn('REINSTATED', e3['stay_passages'][0]['passage'])
        self.assertEqual([h['event'] for h in r['stay_history']][-1], 'reinstated')
        self.assertTrue(r['stay_in_effect'])

    def test_a_motion_asking_for_reinstatement_is_not_one(self):
        body = {'source_ref': 'court:2:1', 'entry_ref': '2', 'reading': {'pages': [
            {'page': 2, 'outcome': 'text', 'text': 'Debtor moves to reinstate the automatic stay.\n'
                                                   'The Debtor filed a Motion to reinstate the stay.\n'
                                                   'If the stay is reinstated, the sale is cancelled.'}]}}
        r = run([entry(1, 'Final Judgment'), entry(2, 'Suggestion of Bankruptcy')], [body])
        self.assertEqual(r['entries'][1]['kind'], 'suggestion_of_bankruptcy')

    def test_a_vacatur_that_names_its_judgment_only_in_the_body_is_linked(self):
        # 6828's shape: two judgments, and a docket title that says only "Order vacating
        # final judgment". The order's own text names the judgment by a long-form date.
        body = {'source_ref': 'court:3:1', 'entry_ref': '3', 'reading': {'pages': [
            {'page': 1, 'outcome': 'text', 'text': 'ORDER VACATING FINAL JUDGMENT\nThe Final Judgment of '
             'Foreclosure entered on the 1st day of April, 2026 is hereby vacated. Hearing held May 20, 2026.'}]}}
        r = run([entry(1, 'Final Judgment of Foreclosure', '03/01/2026'),
                 entry(2, 'Final Judgment of Foreclosure', '04/01/2026'),
                 entry(3, 'Order vacating final judgment', '06/01/2026', expected=1),
                 entry(4, 'Amended Final Judgment of Foreclosure', '08/01/2026')], [body])
        j = {x['entry_id']: x for x in r['judgments']['judgments']}
        self.assertEqual(j['2']['status'], 'vacated')
        self.assertIn('document body', j['2']['reason'])
        self.assertNotIn('_body', r['entries'][2])

    def test_6828_as_the_docket_really_reads(self):
        # Desktop replay, 2026-09-24: an affidavit, a certificate of service and a motion each had
        # a judgment on page 1; two judgment entries share one day; the agreed vacatur names that
        # day and is limited "as to" two defendants; an amended judgment follows.
        def doc(n, text):
            return {'source_ref': 'court:%d:1' % n, 'entry_ref': str(n), 'reading': {'pages': [
                {'page': 1, 'outcome': 'text', 'text': text}]}}
        judgment = 'FINAL JUDGMENT OF FORECLOSURE\nIT IS ORDERED AND ADJUDGED'
        docs = [doc(49, judgment), doc(56, judgment), doc(58, judgment), doc(65, judgment),
                doc(67, 'AGREED ORDER VACATING FINAL JUDGMENT OF FORECLOSURE AND\n'
                        'CANCELLING FORECLOSURE SALE\n1. The Final Judgment of Foreclosure entered on '
                        'September 8, 2025, and recorded in Official Records Book 1, Page 2, is hereby '
                        'VACATED as to Defendants EXAMPLE HOLDINGS LLC and JOHN DOE.\n'
                        '2. The foreclosure sale currently scheduled for October 20, 2025, is CANCELLED.'),
                doc(82, 'AMENDED FINAL JUDGMENT OF FORECLOSURE')]
        r = run_with_parties([
            entry(49, 'Affidavit of Indebtedness', '07/01/2025', expected=1),
            entry(56, 'Final Judgment by Judge', '09/08/2025', expected=1),
            entry(57, 'Final Judgment by Judge', '09/08/2025'),
            entry(58, 'Certificate of Service', '09/09/2025', expected=1,
                  comments='OF SERVING FINAL JUDGMENT OF MORTGAGE FORECLOSURE'),
            entry(65, 'Motion to Cancel Sale', '10/13/2025', expected=1),
            entry(67, 'Order to Vacate Judgment', '10/16/2025', expected=1,
                  comments='(AGREED)AND CANCEL FORECLOSURE SALE SET FOR OCTOBER 20, 2025 AT 9:00 A.M.'),
            entry(82, 'Amended Final Judgment', '08/19/2026', expected=1)],
            ['EXAMPLE HOLDINGS LLC', 'JOHN DOE', 'JANE DOE'], docs)
        kinds = {e['entry_id']: e['kind'] for e in r['entries']}
        self.assertEqual([kinds[n] for n in ('49', '58', '65')],
                         ['affidavit', 'certificate_of_service', 'motion'])
        self.assertEqual(r['entries'][0]['attached_document_kind'], 'final_judgment')
        j = {x['entry_id']: x for x in r['judgments']['judgments']}
        self.assertEqual(sorted(j), ['56', '57', '82'])
        # #57 has no image on a day #56 has one: the docket listing one judgment twice.
        self.assertEqual((j['57']['role'], j['57']['status']), ('docket_duplicate', 'docket_duplicate_inferred'))
        self.assertEqual(j['56']['status'], 'superseded')
        self.assertEqual(j['56']['by'], ['67', '82'])
        self.assertEqual(j['82']['replaces'], '56')
        self.assertEqual(r['judgments']['controlling_entry'], '82')
        self.assertEqual(r['judgments']['docket_duplicates_inferred'], ['57'])

    def test_an_image_less_same_day_judgment_entry_is_a_docket_duplicate(self):
        # Walker #79/#80, McCray #91/#92, Blue Water #174/#177: the imaged entry controls,
        # whichever of the two the docket lists first.
        doc = {'source_ref': 'court:2:1', 'entry_ref': '2', 'reading': {'pages': [
            {'page': 1, 'outcome': 'text', 'text': 'FINAL JUDGMENT OF FORECLOSURE'}]}}
        r = run([entry(1, 'Final Judgment', '06/16/2026'),
                 entry(2, 'Final Judgment', '06/16/2026', expected=1)], [doc])
        self.assertEqual(r['judgments']['controlling_entry'], '2')
        self.assertIn('listed twice', r['judgments']['controlling_reason'])

    def test_two_imaged_same_day_judgments_stay_unclear(self):
        docs = [{'source_ref': 'court:%d:1' % n, 'entry_ref': str(n), 'reading': {'pages': [
            {'page': 1, 'outcome': 'text', 'text': 'FINAL JUDGMENT OF FORECLOSURE'}]}} for n in (1, 2)]
        r = run([entry(1, 'Final Judgment', '06/16/2026', expected=1),
                 entry(2, 'Final Judgment', '06/16/2026', expected=1)], docs)
        self.assertIsNone(r['judgments']['controlling_entry'])

    def test_filings_that_carry_a_judgment_copy_are_not_judgments(self):
        judgment = {'page': 1, 'outcome': 'text', 'text': 'FINAL JUDGMENT OF FORECLOSURE'}
        docs = [{'source_ref': 'court:%d:1' % n, 'entry_ref': str(n), 'reading': {'pages': [judgment]}}
                for n in (2, 3, 4, 5)]
        r = run([entry(1, 'Final Judgment', '01/10/2026'),
                 entry(2, 'Memorandum', '02/01/2026', expected=1),
                 entry(3, 'Notice', '02/02/2026', expected=1, comments='STATUS REPORT'),
                 entry(4, 'Request: FOR JUDICIAL NOTICE', '02/03/2026', expected=1),
                 entry(5, 'Final Judgment', '03/01/2026', expected=1)], docs)
        self.assertEqual([e['kind'] for e in r['entries'][1:4]], ['other', 'other', 'other'])
        self.assertEqual([j['entry_id'] for j in r['judgments']['judgments']], ['1', '5'])

    def test_the_partial_vacatur_alone_leaves_the_same_day_pair_partially_vacated(self):
        body = {'source_ref': 'court:3:1', 'entry_ref': '3', 'reading': {'pages': [
            {'page': 1, 'outcome': 'text', 'text': 'AGREED ORDER VACATING FINAL JUDGMENT\nThe Final '
             'Judgment entered on September 8, 2025 is hereby VACATED as to Defendant JOHN DOE.'}]}}
        r = run_with_parties([entry(1, 'Final Judgment by Judge', '09/08/2025'),
                              entry(2, 'Final Judgment by Judge', '09/08/2025'),
                              entry(3, 'Order to Vacate Judgment', '10/16/2025', expected=1)],
                             ['JOHN DOE', 'JANE DOE'], [body])
        j = {x['entry_id']: x for x in r['judgments']['judgments']}
        self.assertEqual([j['1']['status'], j['2']['status']], ['partially_vacated'] * 2)
        self.assertIn('shared by 2 judgment entries that day', j['1']['reason'])
        self.assertIn('limited to', j['1']['reason'])

    def test_two_same_day_judgments_nothing_names_stay_unclear(self):
        r = run([entry(1, 'Final Judgment by Judge', '09/08/2025'),
                 entry(2, 'Final Judgment by Judge', '09/08/2025')])
        j = r['judgments']['judgments']
        self.assertEqual({x['status'] for x in j}, {'unclear'})
        self.assertIn('same day', j[0]['reason'])
        self.assertIsNone(r['judgments']['controlling_entry'])

    def test_a_reinstated_chapter_13_case_puts_the_stay_back_in_effect(self):
        # McCray filing 125's attached order: the case is reinstated and "the automatic stay under
        # 11 U.S.C. 362(a) is once again in effect".
        body = {'source_ref': 'court:3:1', 'entry_ref': '3', 'reading': {'pages': [
            {'page': 3, 'outcome': 'text', 'text': 'ORDER AND REINSTATING CHAPTER 13 CASE\n'
                                                   'This case is REINSTATED effective upon entry of this order.'},
            {'page': 4, 'outcome': 'text', 'text': 'until entry of this order. Immediately upon entry of this '
                                                   'order, the automatic stay\nunder 11 U.S.C. \u00a7 362(a) is '
                                                   'once again in effect.'}]}}
        r = run([entry(1, 'Final Judgment'), entry(2, 'Suggestion of Bankruptcy'),
                 entry(3, 'Suggestion of Bankruptcy', expected=1, comments='BKV: 26-00001-XYZ')], [body])
        e3 = r['entries'][2]
        self.assertEqual((e3['kind'], e3['stay_passages'][0]['page']), ('stay_reinstated', 4))
        self.assertIn('once again in effect', e3['stay_passages'][0]['passage'])
        self.assertTrue(r['stay_in_effect'])

    def test_a_stay_that_would_be_in_effect_again_only_if_asked_is_not_one(self):
        body = {'source_ref': 'court:2:1', 'entry_ref': '2', 'reading': {'pages': [
            {'page': 2, 'outcome': 'text', 'text': 'If the case is reinstated, the stay is once again in effect.'}]}}
        r = run([entry(1, 'Final Judgment'), entry(2, 'Suggestion of Bankruptcy', expected=1)], [body])
        self.assertEqual(r['entries'][1]['kind'], 'suggestion_of_bankruptcy')

    def test_render_survives_page_numbers_in_coverage_detail(self):
        r = run([entry(1, 'Final Judgment')])
        r['coverage'] = {'read': 0, 'expected': 1, 'counts': {'unread': 1}, 'attachments': [
            {'entry_id': '1', 'description': 'Final Judgment', 'state': 'unread', 'detail': [4, 'p5']}]}
        self.assertIn('(4; p5)', T.render_markdown(r))

    def test_long_form_dates_are_read(self):
        self.assertEqual(T._dates_in('entered October 14, 2025 and the 3rd day of Nov., 2025'),
                         {'2025-10-14', '2025-11-03'})

    def test_a_notice_of_serving_a_judgment_is_not_a_judgment(self):
        # 6828 (desktop replay): "Notice of serving final judgment" was a sixth judgment.
        r = run([entry(1, 'Final Judgment of Foreclosure'), entry(2, 'Notice of Serving Final Judgment'),
                 entry(3, 'Notice of Filing Proposed Final Judgment')])
        self.assertEqual([e['kind'] for e in r['entries']],
                         ['final_judgment', 'notice_of_filing', 'notice_of_filing'])
        self.assertEqual(r['judgments']['controlling_entry'], '1')

    def test_a_sale_after_a_reinstated_stay_is_a_conflict(self):
        r = run([entry(1, 'Final Judgment'), entry(2, 'Suggestion of Bankruptcy'),
                 entry(3, 'Order granting relief from bankruptcy stay'),
                 entry(4, 'Order reinstating automatic stay'),
                 entry(5, 'Notice of sale on 10/20/2026')])
        self.assertEqual(r['status']['kind'], 'unclear')
        self.assertIn('4', r['status']['evidence'])

    def test_relief_after_the_reinstatement_ends_it_again(self):
        r = run([entry(1, 'Final Judgment'), entry(2, 'Suggestion of Bankruptcy'),
                 entry(3, 'Order granting relief from bankruptcy stay'),
                 entry(4, 'Order reinstating automatic stay'),
                 entry(5, 'Order granting relief from stay')])
        self.assertEqual(r['status']['kind'], 'judgment_entered')
        self.assertFalse(r['stay_in_effect'])

    def test_a_dismissed_bankruptcy_is_not_a_dismissed_foreclosure(self):
        r = run([entry(1, 'Final Judgment'), entry(2, 'Suggestion of Bankruptcy'),
                 entry(3, 'Notice of dismissal of bankruptcy')])
        self.assertEqual(r['entries'][2]['kind'], 'bankruptcy_dismissed')
        self.assertEqual(r['status']['kind'], 'judgment_entered')
        self.assertFalse(r['stay_in_effect'])

    def test_dismissing_one_named_defendant_does_not_dismiss_the_case(self):
        r = run_with_parties([entry(1, 'Complaint'), entry(2, 'Final Judgment'),
                              entry(3, 'Order dismissing defendant JANE Q DOE')],
                             ['JOHN DOE', 'JANE Q DOE', 'UNKNOWN TENANT'])
        self.assertTrue(r['entries'][2]['limited_scope'])
        self.assertEqual(r['entries'][2]['dismissed_parties'], ['JANE Q DOE'])
        self.assertEqual(r['status']['kind'], 'judgment_entered')

    def test_dropping_unknown_tenants_does_not_dismiss_the_case(self):
        r = run_with_parties([entry(1, 'Complaint'),
                              entry(2, 'Notice of voluntary dismissal of Unknown Tenant in possession')],
                             ['JOHN DOE', 'UNKNOWN TENANT'])
        self.assertTrue(r['entries'][1]['limited_scope'])
        self.assertEqual(r['status']['kind'], 'active_pre_judgment')

    def test_dismissing_the_action_still_dismisses(self):
        r = run_with_parties([entry(1, 'Complaint'),
                              entry(2, 'Order of dismissal: this action is dismissed as to all defendants')],
                             ['JOHN DOE', 'UNKNOWN TENANT'])
        self.assertFalse(r['entries'][1]['limited_scope'])
        self.assertEqual(r['status']['kind'], 'dismissed')

    def test_a_past_sale_date_is_not_a_sale(self):
        r = run([entry(1, 'Final Judgment', '06/01/2026'),
                 entry(2, 'Notice of sale on 08/20/2026', '07/01/2026')])
        self.assertEqual(r['status']['kind'], 'sale_scheduled')
        self.assertEqual(r['status']['sale_outcome'], 'unknown_no_certificate')


if __name__ == '__main__':
    unittest.main()
