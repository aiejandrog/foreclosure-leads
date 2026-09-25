"""_verdicttest — case_verdict.assess against the five hand-written pilot verdicts and the edges.

The acceptance test is the first class: the five cases in MIAMI-AUTOMATION-STATUS.md carry verdicts
a person wrote by reading the code's output. The fixtures below reproduce the states that table
cites, so the module has to reach the same word the person did. If a rule here has to be loosened to
make a case pass, that is a policy change and it belongs in the status file, not in this test.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import case_verdict as CV

# The CLI is run as a subprocess, so the path must not depend on the working directory.
MODULE = Path(CV.__file__).resolve()


def judgment_row(entry_id='232820355', status='operative', satisfaction='no_satisfaction_found',
                 role='judgment', reason=''):
    # reconcile_judgments' real per-judgment shape (miami_case_timeline :677). `status` is not
    # always 'operative': that function's `operative` list also holds 'partially_vacated', and
    # `satisfaction` can be 'partially_satisfied'.
    return {'entry_id': entry_id, 'date': '2026-08-14', 'title': 'FINAL JUDGMENT OF FORECLOSURE',
            'role': role, 'status': status, 'by': [], 'satisfaction': satisfaction,
            'reason': reason}


def timeline(case, kind='judgment_entered', reason=None, controlling='232820355',
             controlling_reason='one operative judgment after amendments, vacaturs and satisfactions',
             stay=None, history=(), checks=(), attachments=(), sale_held=None, gaps=(),
             judgments=None, duplicates=(), sale_date=None, sale_outcome=None):
    status = {'kind': kind, 'evidence': [], 'reason': reason or ''}
    if sale_date:
        status['sale_date'] = sale_date
    if sale_outcome:
        status['sale_outcome'] = sale_outcome
    # The producer always writes a row per final judgment beside `controlling_entry`; a fixture with
    # an empty list is not a shape build_timeline can produce, and treating it as one is how this
    # suite came to pass while `status`/`satisfaction` on the controlling judgment went unread.
    rows = list(judgments) if judgments is not None else (
        [judgment_row(controlling)] if controlling else [])
    return {'case': case, 'county': 'MIAMI-DADE', 'as_of': '2026-09-24',
            'status': status,
            'judgments': {'controlling_entry': controlling, 'controlling_reason': controlling_reason,
                          'judgments': rows, 'unmatched': [],
                          'docket_duplicates_inferred': list(duplicates)},
            'stay_in_effect': stay, 'stay_history': list(history),
            'sale_held': sale_held,
            'amount_vision': {'amount_checks': list(checks)},
            'coverage': {'attachments': list(attachments), 'complete': False},
            'gaps': list(gaps), 'coverage_complete': not gaps}


def ok_check(entry_id='232820355', source_ref='court:232820355:1', amount=1746032.70):
    return {'entry_id': entry_id, 'source_ref': source_ref, 'amount': amount, 'ok': True,
            'reason': 'one run of printed rows ending at the total adds up to it exactly; '
                      'subtotals agree',
            'pages': [2, 3], 'run': 'continued_from_page_2', 'disagreeing_subtotals': []}


def failed_check(entry_id='232632335', source_ref='court:232632335:1', amount=785670.31,
                 subtotals=()):
    return {'entry_id': entry_id, 'source_ref': source_ref, 'amount': amount, 'ok': False,
            'reason': 'no contiguous run of rows ending at the total adds up to it to the cent '
                      '(tried up to 2 page(s) back)',
            'pages': [1, 2], 'run': None, 'disagreeing_subtotals': list(subtotals)}


def read_attachment(entry_id='232820355'):
    return {'entry_id': entry_id, 'kind': 'final_judgment', 'state': 'read', 'detail': []}


class PilotVerdictTests(unittest.TestCase):
    """The five cases from the status table's acceptance section."""

    def test_2024_014878_is_supported(self):
        # "$1,746,032.70 verifies on court:232820355:1 pp. 2-3. Controlling judgment #82.
        #  Sale 2026-09-28."
        r = CV.assess(timeline('2024-014878-CA-01', kind='sale_scheduled',
                               controlling='232820355', checks=[ok_check()],
                               attachments=[read_attachment('232820355')]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertEqual(r['conflicts'], [])
        self.assertEqual(r['missing'], [])
        self.assertTrue(any('1746032' in s or '232820355' in s for s in r['supported_by']))

    def test_2022_012065_is_supported(self):
        r = CV.assess(timeline('2022-012065-CA-01', controlling='231714504',
                               checks=[ok_check('231714504', 'court:231714504:1', 373482.79)],
                               attachments=[read_attachment('231714504')]))
        self.assertEqual(r['verdict'], 'supported')

    def test_2024_009959_is_supported_on_amount_and_posture(self):
        # The table's "supported (amount and posture)". Estate contact authority is NOT part of this
        # verdict: whether anyone may be called is miami_ranking.qualify's decision, not this one.
        r = CV.assess(timeline('2024-009959-CA-01', controlling='231457022',
                               checks=[ok_check('231457022', 'court:231457022:1', 555499.25)],
                               attachments=[read_attachment('231457022')]))
        self.assertEqual(r['verdict'], 'supported')

    def test_2023_020247_is_supported_with_the_stay_noted(self):
        # "Stay in effect: #125 reinstates it. $305,151.92 verifies. Controlling #91." A stay in
        # effect with no sale going ahead is a note, not a contradiction.
        r = CV.assess(timeline('2023-020247-CA-01', kind='stayed_by_bankruptcy',
                               controlling='224002597', stay=True,
                               history=[{'entry_id': '125', 'event': 'reinstated'}],
                               checks=[ok_check('224002597', 'court:224002597:1', 305151.92)],
                               attachments=[read_attachment('224002597')]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertEqual(r['conflicts'], [])
        self.assertTrue(any('stay is in effect' in n for n in r['notes']))

    def test_2018_026274_is_conflicted_and_the_amount_does_not_verify(self):
        # "conflicted; amount incomplete. Stay #93, no relief order found, vs amended judgment #140
        #  and sale notice #143 for 2026-09-28. $785,670.31 does not verify: the judgment's own
        #  printed interest subtotal $225,243.83 is $0.60 below its eight yearly rows."
        # The producer does NOT write kind='sale_scheduled' here. miami_case_timeline :494 forces
        # kind 'unclear' with this exact reason when an unresolved stay is followed by sale
        # activity, which is how this case's contradiction actually reaches the file. The first
        # version of this test asserted on a shape the pipeline cannot produce.
        r = CV.assess(timeline(
            '2018-026274-CA-01', kind='unclear', controlling='232632335', stay=True,
            reason='Later foreclosure activity conflicts with an unresolved bankruptcy stay; no '
                   'relief identified.',
            history=[{'entry_id': '93', 'event': 'stayed'}],
            # judgment_money's `difference` is signed: rows above minus the printed subtotal, so
            # this case's real output is -0.60, not 0.60 (_moneytest pins (30.0, 2, 30.6, -0.6)).
            checks=[failed_check(subtotals=[{'page': 2, 'label': 'INTEREST', 'amount': 225243.83,
                                             'rows_above': 8, 'rows_above_sum': 225244.43,
                                             'difference': -0.60}])],
            attachments=[read_attachment('232632335')]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any('Later foreclosure activity conflicts' in c
                            for c in r['conflicts']), r['conflicts'])
        self.assertTrue(any('$225,243.83'.replace(',', '') in c.replace(',', '')
                            for c in r['conflicts']), r['conflicts'])
        self.assertTrue(any('off by $0.60' in c for c in r['conflicts']), r['conflicts'])
        self.assertTrue(any('verifies to the cent' in m for m in r['missing']), r['missing'])


class PostureTests(unittest.TestCase):
    def test_conflicting_judgments_are_conflicted_not_incomplete(self):
        r = CV.assess(timeline('X', controlling=None,
                               controlling_reason=CV.CONFLICTING_JUDGMENTS,
                               checks=[ok_check()]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any(CV.CONFLICTING_JUDGMENTS in c for c in r['conflicts']))

    def test_no_operative_judgment_is_incomplete(self):
        r = CV.assess(timeline('X', controlling=None,
                               controlling_reason=CV.NO_OPERATIVE_JUDGMENT))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertEqual(r['conflicts'], [])

    def test_an_unclear_status_that_contradicts_itself_is_conflicted(self):
        for reason in CV.CONFLICT_REASONS:
            r = CV.assess(timeline('X', kind='unclear', reason=reason, checks=[ok_check()]))
            self.assertEqual(r['verdict'], 'conflicted', reason)

    def test_an_unclear_status_that_is_merely_missing_evidence_is_incomplete(self):
        # miami_case_timeline sets 'unclear' for eight reasons; these three are absent evidence.
        for reason in ('Undated dispositive entry prevents reliable chronology.',
                       'An order lifting a stay names no bankruptcy; it does not show the '
                       'bankruptcy stay ended.',
                       'Stay relief found without established pre-stay state.'):
            r = CV.assess(timeline('X', kind='unclear', reason=reason, checks=[ok_check()]))
            self.assertEqual(r['verdict'], 'incomplete', reason)
            self.assertEqual(r['conflicts'], [])

    def test_an_unrecognised_unclear_reason_counts_as_missing(self):
        r = CV.assess(timeline('X', kind='unclear', reason='something nobody has written yet',
                               checks=[ok_check()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertEqual(r['conflicts'], [])

    def test_no_judgment_yet_is_incomplete(self):
        r = CV.assess(timeline('X', kind='active_pre_judgment', controlling=None,
                               controlling_reason=CV.NO_OPERATIVE_JUDGMENT))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('no judgment entered yet' in m for m in r['missing']))

    def test_an_unknown_stay_is_incomplete(self):
        r = CV.assess(timeline('X', stay=None, history=[{'entry_id': '9', 'event': 'stayed'}],
                               checks=[ok_check()], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertIn('stay state unknown', r['missing'])

    def test_no_stay_history_at_all_does_not_hold_the_case(self):
        r = CV.assess(timeline('X', stay=None, history=[], checks=[ok_check()],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'supported')

    def test_a_stay_over_a_held_sale_is_conflicted(self):
        r = CV.assess(timeline('X', kind='judgment_entered', stay=True,
                               history=[{'entry_id': '9', 'event': 'stayed'}],
                               sale_held={'date': '2026-09-01', 'certificate': None},
                               checks=[ok_check()], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'conflicted')


class AmountTests(unittest.TestCase):
    def test_an_amount_verified_on_another_entry_is_not_this_judgment_s(self):
        # verify-12 defect D5: another filing's instrument cannot corroborate this judgment.
        r = CV.assess(timeline('X',
                               checks=[ok_check(entry_id='231457022',
                                                source_ref='court:231457022:1')],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('verifies to the cent' in m for m in r['missing']))

    def test_a_check_with_no_entry_id_does_not_count(self):
        # This test asserted the opposite until the first review of #66, on the premise that an
        # unattributed check is only legacy data. It is not: run_case_timeline.load_rows sets
        # entry_ref only for a court: source_ref, so every recorded-instrument row in the same
        # pipeline carries None, and accepting None let a recorded mortgage's principal corroborate
        # a judgment amount. That is verify-12 defect D5.
        r = CV.assess(timeline('X', checks=[ok_check(entry_id=None)],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('verifies to the cent' in m for m in r['missing']), r['missing'])

    def test_an_amount_read_off_a_recorded_copy_is_not_the_court_copy(self):
        # Same defect from the other side: the right entry_id, but the figure came off an Official
        # Records instrument rather than the court's own judgment.
        r = CV.assess(timeline('X', checks=[ok_check(source_ref='official_records/1-2')],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('not the court copy' in m for m in r['missing']), r['missing'])

    def test_a_truthy_ok_is_not_a_verification(self):
        # ok: 1 and ok: 'false' both read as verified while the test was a truthiness check.
        for value in (1, 'false', 'true', {}, []):
            r = CV.assess(timeline('X', checks=[dict(ok_check(), ok=value)],
                                   attachments=[read_attachment()]))
            self.assertEqual(r['verdict'], 'incomplete', value)

    def test_a_verified_check_with_no_amount_is_not_a_verification(self):
        r = CV.assess(timeline('X', checks=[dict(ok_check(), amount=None)],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')

    def test_one_total_verifying_does_not_excuse_another_that_does_not(self):
        # verify_document returns one check per grand total, so a judgment printing several totals
        # gets several checks. Reporting only the good one said "the amount verified" about a
        # document that disagrees with itself.
        r = CV.assess(timeline('X', checks=[ok_check(), failed_check(entry_id='232820355',
                                                                    source_ref='court:232820355:1')],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('do not verify' in m for m in r['missing']), r['missing'])

    def test_two_runs_reaching_the_total_is_conflicted(self):
        check = failed_check()
        check['reason'] = CV.AMBIGUOUS_RUN
        r = CV.assess(timeline('X', controlling='232632335', checks=[check],
                               attachments=[read_attachment('232632335')]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any('more than one reading' in c for c in r['conflicts']))

    def test_unread_amount_pages_are_missing_not_contradictory(self):
        check = failed_check()
        check['reason'] = 'amount pages have unresolved reading gaps'
        r = CV.assess(timeline('X', controlling='232632335', checks=[check],
                               attachments=[read_attachment('232632335')]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertEqual(r['conflicts'], [])

    def test_no_check_at_all_is_incomplete(self):
        r = CV.assess(timeline('X', checks=[], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')


class CoverageTests(unittest.TestCase):
    def test_a_login_walled_controlling_judgment_is_incomplete(self):
        for state in ('restricted', 'restricted_likely', 'login_required', 'login_required_likely'):
            r = CV.assess(timeline('X', checks=[ok_check()],
                                   attachments=[{'entry_id': '232820355', 'state': state}]))
            self.assertEqual(r['verdict'], 'incomplete', state)
            self.assertTrue(any("clerk's login" in m for m in r['missing']), state)

    def test_both_vocabularies_for_the_same_gap_are_accepted(self):
        # The same defect is 'login_required' in miami_case_timeline and 'restricted' in
        # document_coverage. Whichever word the saved evidence used, the verdict is the same.
        words = CV.NAMES['login_walled']
        self.assertTrue(set(words['miami_case_timeline']) <= set(CV.LOGIN_WALLED))
        self.assertTrue(set(words['document_coverage']) <= set(CV.LOGIN_WALLED))

    def test_gaps_elsewhere_on_the_docket_are_noted_and_never_block(self):
        # The scope decision in the module docstring. 2024-014878 is "supported" in the status table
        # with unread attachments elsewhere on its docket.
        r = CV.assess(timeline('X', checks=[ok_check()],
                               attachments=[read_attachment(),
                                            {'entry_id': '5', 'state': 'fetched_unread'},
                                            {'entry_id': '6', 'state': 'restricted'}]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertTrue(any('elsewhere on the docket' in n for n in r['notes']))
        self.assertTrue(any("behind the clerk's login" in n for n in r['notes']))

    def test_county_has_no_document_is_not_counted_as_unread_elsewhere(self):
        r = CV.assess(timeline('X', checks=[ok_check()],
                               attachments=[read_attachment(),
                                            {'entry_id': '5', 'state': 'county_no_document'}]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertFalse(any('elsewhere on the docket' in n for n in r['notes']))

    def test_a_missing_image_on_the_controlling_judgment_is_incomplete(self):
        for state in ('no_image_indexed', 'not_fetched', 'county_no_document', 'not_enumerated'):
            r = CV.assess(timeline('X', checks=[ok_check()],
                                   attachments=[{'entry_id': '232820355', 'state': state}]))
            self.assertEqual(r['verdict'], 'incomplete', state)


class AbsentEvidenceTests(unittest.TestCase):
    """Absence must never read as adequacy. Every shape here returned 'supported' in #66's first
    commit, which is the one direction this module is not allowed to fail in."""

    def test_no_coverage_block_at_all_is_incomplete(self):
        t = timeline('X', checks=[ok_check()])
        t.pop('coverage')
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('no coverage row' in m for m in r['missing']), r['missing'])

    def test_a_coverage_block_with_no_row_for_the_judgment_is_incomplete(self):
        r = CV.assess(timeline('X', checks=[ok_check()],
                               attachments=[{'entry_id': '5', 'state': 'read'}]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('no coverage row' in m for m in r['missing']), r['missing'])

    def test_a_row_with_no_state_recorded_is_incomplete(self):
        r = CV.assess(timeline('X', checks=[ok_check()],
                               attachments=[{'entry_id': '232820355'}]))
        self.assertEqual(r['verdict'], 'incomplete')

    def test_shapes_that_are_not_what_the_producer_writes_do_not_read_as_supported(self):
        for coverage in ({'attachments': {'232820355': {'state': 'restricted'}}},
                         {'attachments': None}, {'attachments': 'x'}, {'attachments': ['x']},
                         {}, None, [], 'oops'):
            t = timeline('X', checks=[ok_check()])
            t['coverage'] = coverage
            self.assertEqual(CV.assess(t)['verdict'], 'incomplete', coverage)

    def test_a_status_that_is_not_a_dict_does_not_read_as_supported(self):
        for status in ('oops', None, [], 42):
            t = timeline('X', checks=[ok_check()], attachments=[read_attachment()])
            t['status'] = status
            self.assertEqual(CV.assess(t)['verdict'], 'incomplete', status)

    def test_an_unrecognised_docket_kind_is_incomplete(self):
        # An allowlist, so a kind miami_case_timeline gains or renames holds the case.
        for kind in ('a-kind-nobody-has-written-yet', 'UNCLEAR', 'Unclear', '', 'judgment entered'):
            r = CV.assess(timeline('X', kind=kind, checks=[ok_check()],
                                   attachments=[read_attachment()]))
            self.assertEqual(r['verdict'], 'incomplete', kind)

    def test_every_kind_on_the_allowlist_is_one_miami_case_timeline_can_emit(self):
        # The allowlist is only safe while it matches the producer's vocabulary. The first version
        # of this test looked for MCT._TRANSITIONS, which does not exist (the mapping is a local
        # named `statuses`), so it always fell through to grepping the source for the quoted string:
        # a check that passes on an unrelated mention, depends on the working directory, and cannot
        # see a kind the producer GAINED. _transition() is the producer, so ask it directly.
        import miami_case_timeline as MCT
        docket_kinds = ('complaint', 'amended_complaint', 'final_judgment', 'notice_of_sale',
                        'order_resetting_sale', 'order_cancelling_sale', 'suggestion_of_bankruptcy',
                        'stay', 'notice_of_voluntary_dismissal', 'order_of_dismissal',
                        'satisfaction', 'certificate_of_sale', 'certificate_of_title',
                        'stay_reinstated', 'vacatur')
        emitted = set()
        for kind in docket_kinds:
            change = MCT._transition({'kind': kind, 'entry_id': '1', 'date': '2026-01-01',
                                      'operative_text': '', 'description': '', 'comments': '',
                                      'sale_passages': [], 'calendar_event': False})
            if change:
                emitted.add(change['kind'])
        self.assertIn('judgment_entered', emitted, 'this test no longer drives the producer')
        self.assertEqual(set(CV.SETTLED_KINDS + CV.SALE_KINDS) - emitted, set(),
                         'case_verdict vouches for a kind miami_case_timeline does not emit')
        self.assertEqual(emitted - set(CV.SETTLED_KINDS) - {'unclear', 'active_pre_judgment'}, set(),
                         'miami_case_timeline emits a kind case_verdict has never heard of')
        for kind in CV.SALE_KINDS:
            self.assertIn(kind, CV.SETTLED_KINDS)


class ReportTests(unittest.TestCase):
    def test_a_stay_in_effect_appears_on_the_report(self):
        # It did not, in #66's first commit: render_markdown printed only conflicts, missing and
        # supported_by, so a supported case under a stay rendered as a clean row.
        r = CV.assess(timeline('2023-020247-CA-01', kind='stayed_by_bankruptcy',
                               controlling='224002597', stay=True,
                               history=[{'entry_id': '125', 'event': 'reinstated'}],
                               checks=[ok_check('224002597', 'court:224002597:1', 305151.92)],
                               attachments=[read_attachment('224002597')]))
        self.assertEqual(r['verdict'], 'supported')
        md = CV.render_markdown([r])
        self.assertIn('IN EFFECT', md)
        self.assertIn('Bankruptcy stay', md)
        self.assertIn('nothing here clears anyone to be contacted', md)

    def test_an_unknown_stay_state_is_named_on_the_report(self):
        r = CV.assess(timeline('X', stay=None, history=[{'event': 'stayed'}], checks=[ok_check()],
                               attachments=[read_attachment()]))
        self.assertIn('unknown', CV.render_markdown([r]))

    def test_a_subtotal_with_no_additive_rows_above_it_does_not_print_none(self):
        text = CV._subtotal_gap({'disagreeing_subtotals': [
            {'page': 1, 'label': 'Costs total', 'amount': 40.0, 'rows_above': 0}]})
        self.assertNotIn('None', text)
        self.assertIn('no additive rows above it', text)

    def test_the_printed_gap_is_never_a_negative_amount(self):
        # judgment_money's `difference` is signed; 2018-026274's real value is -0.60.
        text = CV._subtotal_gap({'disagreeing_subtotals': [
            {'label': 'INTEREST', 'amount': 225243.83, 'rows_above_sum': 225244.43,
             'difference': -0.60}]})
        self.assertIn('off by $0.60', text)
        self.assertNotIn('$-', text)

    def test_the_controlling_judgment_is_not_also_counted_as_elsewhere(self):
        r = CV.assess(timeline('X', checks=[ok_check()],
                               attachments=[{'entry_id': '232820355', 'state': 'restricted'}]))
        self.assertTrue(any("clerk's login" in m for m in r['missing']))
        self.assertFalse(any('elsewhere on the docket' in n for n in r['notes']), r['notes'])


class ShapeTests(unittest.TestCase):
    def test_every_verdict_carries_the_completeness_caveat(self):
        r = CV.assess(timeline('X', checks=[ok_check()], attachments=[read_attachment()]))
        self.assertIn('pagination cursor', r['caveat'])
        self.assertIn('miami_ranking.qualify', r['qualification'])

    def test_the_verdict_is_one_of_three_words(self):
        r = CV.assess(timeline('X'))
        self.assertIn(r['verdict'], CV.VERDICTS)

    def test_a_timeline_missing_every_block_does_not_read_as_supported(self):
        r = CV.assess({'case': 'X'})
        self.assertEqual(r['verdict'], 'incomplete')

    def test_a_dossier_s_own_gaps_are_notes(self):
        r = CV.assess(timeline('X', checks=[ok_check()], attachments=[read_attachment()]),
                      {'open_gaps': ['the docket may be incomplete: no pagination cursor is '
                                     'published']})
        self.assertEqual(r['verdict'], 'supported')
        self.assertTrue(any('pagination cursor' in n for n in r['notes']))

    def test_markdown_counts_every_verdict(self):
        rows = [CV.assess(timeline('A', checks=[ok_check()], attachments=[read_attachment()])),
                CV.assess(timeline('B'))]
        md = CV.render_markdown(rows)
        self.assertIn('supported: 1', md)
        self.assertIn('incomplete: 1', md)
        self.assertIn('conflicted: 0', md)


class CliTests(unittest.TestCase):
    def test_it_reads_saved_dossiers_and_writes_a_report(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for case, kw in (('2024-014878-CA-01', {'checks': [ok_check()],
                                                    'attachments': [read_attachment()]}),
                             ('2018-026274-CA-01', {'kind': 'unclear', 'controlling': '140',
                                                    'stay': True,
                                                    'reason': 'Later foreclosure activity conflicts '
                                                              'with an unresolved bankruptcy stay; '
                                                              'no relief identified.',
                                                    'history': [{'entry_id': '93',
                                                                 'event': 'stayed'}],
                                                    'checks': [failed_check(entry_id='140')],
                                                    'attachments': [read_attachment('140')]})):
                (folder / (case + '.json')).write_text(
                    json.dumps({'case': case, 'complete': False, 'open_gaps': []}))
                (folder / (case + '-timeline.json')).write_text(
                    json.dumps(timeline(case, **kw)))
            # A dossier with no timeline saved yet is reported as skipped, never as a verdict.
            (folder / 'no-timeline-yet.json').write_text(json.dumps({'case': 'Z'}))

            proc = subprocess.run([sys.executable, '-u', str(MODULE), '--dossiers',
                                   str(folder)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('supported 1', proc.stdout)
            self.assertIn('conflicted 1', proc.stdout)
            self.assertIn('$0 spent, no requests', proc.stdout)
            self.assertIn('SKIPPED', proc.stdout)
            rows = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertEqual([r['verdict'] for r in rows], ['conflicted', 'supported'])
            self.assertIn('| Case | Verdict |', (folder / 'case-verdicts.md').read_text())

    def test_it_asks_for_an_input_rather_than_guessing(self):
        proc = subprocess.run([sys.executable, str(MODULE)], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('--dossiers', proc.stderr)


class SecondReviewTests(unittest.TestCase):
    """The independent review of #66's first commit. Every test here fails on that commit.

    The first round fixed absent STRUCTURE reading as agreement. These are absent SEMANTICS: data
    that is present, well-shaped, and says the evidence does not support the judgment - which the
    module was not reading at all.
    """

    def test_two_totals_that_both_verify_but_disagree_are_conflicted(self):
        # verify_document returns one check per stated grand total (judgment_money :457) and
        # document_vision appends one grand total PER PAGE (:263). A judgment printing a different
        # total on two pages therefore yields two checks that both verify, and that read as
        # "amount verified to the cent".
        r = CV.assess(timeline('X', checks=[ok_check(amount=1746032.70),
                                            ok_check(source_ref='court:232820355:1',
                                                     amount=955000.00)],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any('two different totals' in c for c in r['conflicts']), r['conflicts'])
        self.assertTrue(any('955,000.00' in c and '1,746,032.70' in c for c in r['conflicts']))

    def test_the_verified_amount_is_in_the_verdict_and_on_the_report(self):
        # The column this report replaces IS the amount. The first version recorded only that
        # something verified, and named the source_ref, so the figure never reached the page.
        r = CV.assess(timeline('X', checks=[ok_check()], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertEqual(r['judgment_amount'], 1746032.70)
        self.assertIn('$1,746,032.70', CV.render_markdown([r]))
        self.assertIn('| Amount |', CV.render_markdown([r]))

    def test_a_failed_check_saved_before_the_subtotal_key_existed_says_so(self):
        # run_case_timeline started saving disagreeing_subtotals in this PR, and
        # keep_cached_amounts copies an older check verbatim. On every timeline already on the
        # desktop, 2018-026274's $0.60 contradiction degraded silently to "not read".
        check = failed_check(entry_id='232820355')
        check.pop('disagreeing_subtotals')
        r = CV.assess(timeline('X', checks=[check], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('kept no subtotal detail' in m for m in r['missing']), r['missing'])
        self.assertTrue(any('re-run the timeline' in m for m in r['missing']))

    def test_a_total_with_no_matching_printed_total_row_is_named(self):
        check = failed_check(entry_id='232820355')
        check['reason'] = CV.TOTAL_ROW_DISAGREES
        r = CV.assess(timeline('X', checks=[check], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('no printed total row on that page matches it' in m
                            for m in r['missing']), r['missing'])

    def test_an_inferred_unread_duplicate_judgment_holds_the_case(self):
        # reconcile_judgments (:681) reaches ONE operative judgment by inferring that a
        # login-walled same-day entry is the same judgment listed twice - "(inferred, not read)".
        # The uniqueness of the controlling judgment is what "supported" is scoped to.
        r = CV.assess(timeline('X', duplicates=['79'], checks=[ok_check()],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('inferred to be a duplicate without being read' in m
                            for m in r['missing']), r['missing'])

    def test_a_passed_sale_date_with_no_certificate_holds_the_case(self):
        # miami_case_timeline (:508) keeps kind 'sale_scheduled' and records the outcome; its own
        # reason says "whether a sale occurred is unknown".
        for outcome in CV.UNSETTLED_SALE_OUTCOMES:
            r = CV.assess(timeline('X', kind='sale_scheduled', sale_date='2026-01-05',
                                   sale_outcome=outcome, checks=[ok_check()],
                                   attachments=[read_attachment()]))
            self.assertEqual(r['verdict'], 'incomplete', outcome)
            self.assertTrue(any(outcome in m for m in r['missing']), r['missing'])

    def test_a_partly_vacated_or_partly_satisfied_judgment_holds_the_case(self):
        # reconcile_judgments' `operative` list includes 'partially_vacated' (:753), and
        # satisfaction can be 'partially_satisfied'. Both were reported as a clean verified amount.
        for row in (judgment_row(status='partially_vacated',
                                 reason='vacatur 99 (cited date); limited to one defendant'),
                    judgment_row(satisfaction='partially_satisfied')):
            r = CV.assess(timeline('X', judgments=[row], checks=[ok_check()],
                                   attachments=[read_attachment()]))
            self.assertEqual(r['verdict'], 'incomplete', row)
        r = CV.assess(timeline('X', judgments=[judgment_row(status='partially_vacated')],
                               checks=[ok_check()], attachments=[read_attachment()]))
        self.assertTrue(any('partially_vacated' in m for m in r['missing']), r['missing'])

    def test_no_reconciliation_row_for_the_controlling_judgment_holds_the_case(self):
        r = CV.assess(timeline('X', judgments=[judgment_row('999')], checks=[ok_check()],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('no record for the controlling judgment' in m for m in r['missing']))

    def test_a_stay_over_a_sale_still_on_the_calendar_is_conflicted(self):
        # The kind-only rule was close to unreachable: when an unresolved stay is followed by sale
        # activity the producer rewrites kind to 'unclear' (:494), and a stay filed after the sale
        # notice leaves kind 'stayed_by_bankruptcy'. The sale_date survives both.
        r = CV.assess(timeline('X', kind='stayed_by_bankruptcy', stay=True,
                               sale_date='2026-09-28',
                               history=[{'entry_id': '93', 'event': 'stayed'}],
                               checks=[ok_check()], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any('2026-09-28' in c for c in r['conflicts']), r['conflicts'])

    def test_a_judgments_or_amount_vision_block_of_the_wrong_type_does_not_raise(self):
        for key in ('judgments', 'amount_vision'):
            for value in ('oops', [], 42, None, ['x']):
                t = timeline('X', checks=[ok_check()], attachments=[read_attachment()])
                t[key] = value
                self.assertEqual(CV.assess(t)['verdict'], 'incomplete', (key, value))

    def test_one_unreadable_saved_case_does_not_lose_the_others(self):
        # main() had no per-case guard, so a single malformed timeline raised and no report was
        # written at all - for a module whose whole purpose is an unattended report.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            good = timeline('GOOD', checks=[ok_check()], attachments=[read_attachment()])
            (folder / 'GOOD.json').write_text(json.dumps({'case': 'GOOD', 'open_gaps': []}))
            (folder / 'GOOD-timeline.json').write_text(json.dumps(good))
            bad = timeline('BAD', checks=[ok_check()], attachments=[read_attachment()])
            bad['coverage'] = {'attachments': [['not', 'a', 'dict']]}
            bad['judgments'] = {'controlling_entry': '1', 'judgments': 'not a list'}
            (folder / 'BAD.json').write_text(json.dumps({'case': 'BAD'}))
            (folder / 'BAD-timeline.json').write_text(json.dumps(bad))
            proc = subprocess.run([sys.executable, '-u', str(MODULE), '--dossiers', str(folder)],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            rows = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertIn('GOOD', [r['case'] for r in rows])

    def test_a_rerun_does_not_read_its_own_report_as_a_case(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text(json.dumps(
                timeline('A', checks=[ok_check()], attachments=[read_attachment()])))
            for _ in range(2):
                proc = subprocess.run([sys.executable, '-u', str(MODULE), '--dossiers',
                                       str(folder)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn('case-verdicts', proc.stdout.split('SKIPPED')[-1]
                             if 'SKIPPED' in proc.stdout else '')

    def test_out_goes_through_the_output_path_guard(self):
        # Every other writer in this chain routes through case_review.output_path, which refuses a
        # path outside paths.DEALFLOW_DIR and anything under OneDrive (CLAUDE.md's Known Folder
        # Move rule). --out was taking a raw path.
        import case_review
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text(json.dumps(
                timeline('A', checks=[ok_check()], attachments=[read_attachment()])))
            proc = subprocess.run([sys.executable, '-u', str(MODULE), '--dossiers', str(folder),
                                   '--out', str(folder / 'report.json')],
                                  capture_output=True, text=True)
            guarded = None
            try:
                guarded = case_review.output_path(str(folder / 'report.json'))
            except Exception:
                # The guard refuses the path; the CLI must refuse it too, not write it anyway.
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn('refused --out', proc.stdout)
                self.assertNotIn('Traceback', proc.stderr)
                self.assertFalse((folder / 'report.json').exists())
            if guarded is not None:
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(Path(guarded).exists())

    def test_more_than_twenty_dossier_gaps_says_how_many_were_dropped(self):
        r = CV.assess(timeline('X', checks=[ok_check()], attachments=[read_attachment()]),
                      {'open_gaps': ['gap %d' % i for i in range(25)]})
        self.assertTrue(any('5 more open gap(s)' in n for n in r['notes']), r['notes'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
