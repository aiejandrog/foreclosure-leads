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


if __name__ == '__main__':
    unittest.main()
