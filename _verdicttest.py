"""_verdicttest — case_verdict.assess against the five hand-written pilot verdicts and the edges.

The acceptance test is the first class: the five cases in MIAMI-AUTOMATION-STATUS.md carry verdicts
a person wrote by reading the code's output. The fixtures below reproduce the states that table
cites, so the module has to reach the same word the person did. If a rule here has to be loosened to
make a case pass, that is a policy change and it belongs in the status file, not in this test.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import case_verdict as CV

# The CLI is run as a subprocess, so the path must not depend on the working directory.
MODULE = Path(CV.__file__).resolve()


def run_cli(*args, dealflow=None):
    """The CLI, with DEALFLOW_DIR pointed where the report may legitimately land.

    case_review.output_path refuses anything outside paths.DEALFLOW_DIR, inside OneDrive or inside a
    Git repository, and BOTH of case_verdict's write paths go through it now. paths.py honours the
    DEALFLOW_DIR env override for exactly this reason (GitHub Actions uses it too).
    """
    env = dict(os.environ)
    if dealflow is not None:
        env['DEALFLOW_DIR'] = str(dealflow)
    return subprocess.run([sys.executable, '-u', str(MODULE)] + [str(a) for a in args],
                          capture_output=True, text=True, env=env)


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
             judgments=None, duplicates=(), sale_date=None, sale_outcome=None, entries=()):
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
    # A real saved timeline always has entries, and for a stayed case the sale state is read from
    # them, so a fixture with entries=() is a shape build_timeline cannot write - which is how the
    # pilot cases' sale rule went untested through three rewrites. The default is the smallest real
    # docket: a complaint and the controlling judgment, neither of which mentions a sale.
    rows_of_docket = list(entries) if entries else (
        [{'entry_id': '1', 'kind': 'complaint', 'date': '2026-01-05',
          'description': 'Complaint', 'operative_text': 'Complaint', 'comments': ''}]
        + ([{'entry_id': str(controlling), 'kind': 'final_judgment', 'date': '2026-06-10',
             'description': 'Final Judgment of Foreclosure',
             'operative_text': 'FINAL JUDGMENT OF FORECLOSURE', 'comments': ''}]
           if controlling else []))
    return {'case': case, 'county': 'MIAMI-DADE', 'as_of': '2026-09-24',
            'status': status, 'entries': rows_of_docket,
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
                 subtotals=(), reason=None):
    # run_case_timeline.py:195 writes exactly these keys, and 'disagreeing_subtotals' is always []
    # because verify_document cannot populate it (see the 2018-026274 test).
    return {'entry_id': entry_id, 'source_ref': source_ref, 'amount': amount, 'ok': False,
            'reason': reason or ('no contiguous run of rows ending at the total adds up to it to '
                                 'the cent (tried up to 2 page(s) back)'),
            'pages': [1, 2], 'run': None, 'disagreeing_subtotals': list(subtotals)}


# The twins reconcile_judgments' own comment (miami_case_timeline :664) names on the pilot cases:
# 2024-009959 #79/#80, 2023-020247 #91/#92, 2022-012065 #174/#177, and the status file says
# 2024-014878's #57 is a docket duplicate. So docket_duplicates_inferred is NOT empty on the four
# cases the table calls supported, and a fixture passing duplicates=() is not their real state.
# WHICH reason each twin carries - behind the county login, or no document indexed - is a fact only
# the desktop's saved evidence has, and it decides the verdict: see
# test_an_unread_duplicate_is_a_gap_and_a_no_document_one_is_a_note.
PILOT_TWINS = {'2024-014878-CA-01': '57', '2024-009959-CA-01': '79',
               '2023-020247-CA-01': '91', '2022-012065-CA-01': '174'}
NO_IMAGE_REASON = ('no document image; entry %s the same day with the same docket code has one, so '
                   'this is taken as the same judgment listed twice (inferred, not read)')
LOGIN_REASON = ('its document is behind the county login and was not read; entry %s the same day '
                'with the same docket code has a read copy, so this is taken as the same judgment '
                'listed twice (inferred, not read)')


def pilot(case, controlling, walled=False, **kw):
    """One pilot case with its real inferred duplicate, as the producer would save it."""
    twin = PILOT_TWINS[case]
    dup = judgment_row(twin, status='docket_duplicate_inferred', role='docket_duplicate',
                       reason=(LOGIN_REASON if walled else NO_IMAGE_REASON) % controlling)
    return timeline(case, controlling=controlling, duplicates=[twin],
                    judgments=[judgment_row(controlling), dup], **kw)


def read_attachment(entry_id='232820355'):
    return {'entry_id': entry_id, 'kind': 'final_judgment', 'state': 'read', 'detail': []}


class PilotVerdictTests(unittest.TestCase):
    """The five cases from the status table's acceptance section."""

    def test_2024_014878_is_supported(self):
        # "$1,746,032.70 verifies on court:232820355:1 pp. 2-3. Controlling judgment #82.
        #  Sale 2026-09-28."
        r = CV.assess(pilot('2024-014878-CA-01', '232820355', kind='sale_scheduled',
                            checks=[ok_check()],
                            attachments=[read_attachment('232820355')]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertEqual(r['conflicts'], [])
        self.assertEqual(r['missing'], [])
        self.assertTrue(any('1746032' in s or '232820355' in s for s in r['supported_by']))

    def test_2022_012065_is_supported(self):
        r = CV.assess(pilot('2022-012065-CA-01', '231714504',
                            checks=[ok_check('231714504', 'court:231714504:1', 373482.79)],
                            attachments=[read_attachment('231714504')]))
        self.assertEqual(r['verdict'], 'supported')

    def test_2024_009959_is_supported_on_amount_and_posture(self):
        # The table's "supported (amount and posture)". Estate contact authority is NOT part of this
        # verdict: whether anyone may be called is miami_ranking.qualify's decision, not this one.
        r = CV.assess(pilot('2024-009959-CA-01', '231457022',
                            checks=[ok_check('231457022', 'court:231457022:1', 555499.25)],
                            attachments=[read_attachment('231457022')]))
        self.assertEqual(r['verdict'], 'supported')

    def test_2023_020247_is_supported_with_the_stay_noted(self):
        # "Stay in effect: #125 reinstates it. $305,151.92 verifies. Controlling #91." A stay in
        # effect with no sale going ahead is a note, not a contradiction.
        r = CV.assess(pilot('2023-020247-CA-01', '224002597', kind='stayed_by_bankruptcy',
                            stay=True,
                            history=[{'entry_id': '125', 'event': 'reinstated'}],
                            checks=[ok_check('224002597', 'court:224002597:1', 305151.92)],
                            attachments=[read_attachment('224002597')]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertEqual(r['conflicts'], [])
        self.assertTrue(any('stay is in effect' in n for n in r['notes']))

    def test_a_login_walled_twin_turns_every_supported_pilot_case_incomplete(self):
        # The fork this PR cannot settle in a container. If a pilot case's unread twin is behind the
        # county login, a document exists that nobody read, and the uniqueness of the controlling
        # judgment - what "supported" is scoped to - rests on it. Then four of the five hand-written
        # verdicts become incomplete, and THAT is the finding for the desktop run to report, not a
        # rule to loosen. This test pins the consequence so it cannot arrive as a surprise.
        for case, controlling in (('2024-014878-CA-01', '232820355'),
                                  ('2022-012065-CA-01', '231714504'),
                                  ('2024-009959-CA-01', '231457022'),
                                  ('2023-020247-CA-01', '224002597')):
            r = CV.assess(pilot(case, controlling, walled=True,
                                checks=[ok_check(controlling, 'court:%s:1' % controlling, 1.0)],
                                attachments=[read_attachment(controlling)]))
            self.assertEqual(r['verdict'], 'incomplete', case)
            self.assertTrue(any('inferred to be a duplicate without being read' in m
                                for m in r['missing']), (case, r['missing']))

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
            # The shape verify_document REALLY writes for this case. It cannot emit
            # disagreeing_subtotals at all: vision_rows marks every row `explicit`, and for an
            # explicit row _resolve_subtotal either resolves or raises, so the notes that key is
            # collected from are never written. What reaches the file is this reason, and the
            # earlier fixture's non-empty subtotals with a 'no contiguous run' reason is a
            # combination the pipeline cannot produce.
            checks=[failed_check(reason=CV.SUBTOTAL_DISAGREES)],
            attachments=[read_attachment('232632335')]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any('Later foreclosure activity conflicts' in c
                            for c in r['conflicts']), r['conflicts'])
        # The amount is "incomplete" exactly as the status table says, and the reason is named.
        # The $0.60 breakdown itself is NOT in a saved timeline: judgment_money reports a subtotal
        # whose members could not be read and one whose members do not add up under one string.
        self.assertTrue(any(CV.SUBTOTAL_DISAGREES in m for m in r['missing']), r['missing'])
        self.assertTrue(any('does not say which' in m for m in r['missing']), r['missing'])
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

    def test_case_verdict_and_miami_case_timeline_agree_on_the_status_vocabulary(self):
        # Two earlier versions of this test could not see a kind the producer GAINED. The first
        # looked for MCT._TRANSITIONS, which does not exist, and fell through to grepping the source
        # for a quoted string. The second fed _transition a hand-copied tuple of docket kinds, so a
        # producer kind absent from that tuple never appeared in `emitted` - a reviewer added
        # 'order_confirming_sale' -> 'sale_confirmed' to the producer and this test still passed.
        # Read the producer's own mapping literal instead, so a new entry cannot hide.
        import ast
        import inspect
        import miami_case_timeline as MCT
        tree = ast.parse(inspect.getsource(MCT._transition))
        emitted = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict) and node.values and all(
                    isinstance(v, ast.Constant) and isinstance(v.value, str) for v in node.values):
                emitted |= {v.value for v in node.values}
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                continue
        # The vacatur branch returns 'unclear' outside the mapping; pick it up from the literal keys.
        emitted |= {'unclear'}
        self.assertIn('judgment_entered', emitted,
                      'this test no longer reads the producer mapping')
        self.assertEqual(set(CV.SETTLED_KINDS + CV.SALE_KINDS) - emitted, set(),
                         'case_verdict vouches for a kind miami_case_timeline does not emit')
        self.assertEqual(emitted - set(CV.SETTLED_KINDS) - {'unclear', 'active_pre_judgment'}, set(),
                         'miami_case_timeline emits a status kind case_verdict has never heard of; '
                         'decide whether it is a posture to vouch for before adding it')
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

            proc = run_cli('--dossiers', folder, dealflow=folder)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('supported 1', proc.stdout)
            self.assertIn('conflicted 1', proc.stdout)
            self.assertIn('$0 spent, no requests', proc.stdout)
            self.assertIn('SKIPPED', proc.stdout)
            report = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertEqual([r['verdict'] for r in report['verdicts']],
                             ['conflicted', 'supported'])
            # A dossier with no timeline is IN the file, not only on stdout.
            self.assertTrue(any('no-timeline-yet' in p
                                for p in report['no_verdict']['no_timeline_saved']))
            md = (folder / 'case-verdicts.md').read_text()
            self.assertIn('| Case | Verdict |', md)
            self.assertIn('## No verdict', md)
            self.assertIn('no verdict: 1', md)

    def test_it_asks_for_an_input_rather_than_guessing(self):
        proc = run_cli()
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

    def test_a_failed_check_saved_in_an_older_shape_says_so(self):
        # run_case_timeline started saving the key in this PR. An older saved check lacks it, and
        # re-running does not recover a subtotal breakdown either, so the line says only that.
        check = failed_check(entry_id='232820355')
        check.pop('disagreeing_subtotals')
        r = CV.assess(timeline('X', checks=[check], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('older shape than this run writes' in m for m in r['missing']),
                        r['missing'])

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
        # The kind-only rule was close to unreachable, and the status dict is NOT the source: the
        # producer's loop does `status = change` (:503) and _transition builds a fresh dict for a
        # bankruptcy entry with no sale_date, so a stay filed AFTER a notice of sale destroys the
        # scheduled date. The first version of this test passed kind='stayed_by_bankruptcy' WITH a
        # sale_date - a combination miami_case_timeline cannot write, which is the same defect it
        # was written to fix. The saved `entries` list survives, so that is what is read.
        r = CV.assess(timeline('X', kind='stayed_by_bankruptcy', stay=True,
                               history=[{'entry_id': '150', 'event': 'stayed'}],
                               entries=[{'entry_id': '120', 'kind': 'notice_of_sale',
                                         'date': '2026-11-01'},
                                        {'entry_id': '150', 'kind': 'suggestion_of_bankruptcy',
                                         'date': '2026-11-10'}],
                               checks=[ok_check()], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any('entry 120' in c and 'notice of sale' in c for c in r['conflicts']),
                        r['conflicts'])

    def test_a_cancelled_sale_under_a_stay_is_not_a_contradiction(self):
        r = CV.assess(timeline('X', kind='stayed_by_bankruptcy', stay=True,
                               history=[{'entry_id': '150', 'event': 'stayed'}],
                               entries=[{'entry_id': '120', 'kind': 'notice_of_sale',
                                         'date': '2026-11-01'},
                                        {'entry_id': '130', 'kind': 'order_cancelling_sale',
                                         'date': '2026-11-05'}],
                               checks=[ok_check()], attachments=[read_attachment()]))
        self.assertEqual(r['conflicts'], [])
        self.assertTrue(any('stay is in effect' in n for n in r['notes']))

    def test_the_real_producer_s_stay_after_a_notice_of_sale_is_conflicted(self):
        # Driving miami_case_timeline itself, so no fixture can paper over what it writes. This is
        # the case that returned 'supported' with a live 11 USC 362 stay over a pending sale.
        import miami_case_timeline as T

        def mct_entry(n, text, date):
            return {'source_id': str(n), 'expected_documents': 0,
                    'metadata': {'eventID': n, 'eventDate': date, 'docketDescrition': text}}

        t = T.build_timeline('SYNTHETIC', {'entries': [
            mct_entry(100, 'Complaint', '01/05/2026'),
            mct_entry(140, 'Final Judgment of Foreclosure', '06/10/2026'),
            mct_entry(145, 'Notice of Foreclosure Sale set for 12/28/2026', '07/01/2026'),
            mct_entry(150, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '08/01/2026'),
        ], 'pagination_verified': True}, [], '2026-09-23')
        # What the producer really writes: no sale_date anywhere on the status.
        self.assertEqual(t['status']['kind'], 'stayed_by_bankruptcy')
        self.assertNotIn('sale_date', t['status'])
        self.assertIs(t['stay_in_effect'], True)
        t['judgments'] = {'controlling_entry': '140', 'controlling_reason':
                          'one operative judgment after amendments, vacaturs and satisfactions',
                          'judgments': [judgment_row('140')], 'docket_duplicates_inferred': []}
        t['amount_vision'] = {'amount_checks': [ok_check('140', 'court:140:1')]}
        t['coverage'] = {'attachments': [read_attachment('140')], 'complete': False}
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'conflicted', r)
        self.assertTrue(any('stay is in effect while the docket shows a sale' in c
                            for c in r['conflicts']), r['conflicts'])

    def test_a_bankruptcy_on_the_sale_day_surfaces_even_after_relief(self):
        # sale_held records bankruptcy_order 'unresolved' and says whether the petition preceded the
        # sale decides whether the sale is void. Relief granted later makes stay_in_effect False, so
        # this reached the report through no path at all and every verdict was silent on it.
        r = CV.assess(timeline('X', kind='sold', stay=False,
                               history=[{'entry_id': '162', 'event': 'relief'}],
                               sale_held={'date': '2026-09-15', 'evidence': ['160', '161'],
                                          'certificate': '170',
                                          'bankruptcy_same_day': ['162'],
                                          'bankruptcy_order': 'unresolved'},
                               checks=[ok_check()], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any('landed on the day of the sale' in c for c in r['conflicts']),
                        r['conflicts'])

    def test_a_failed_check_s_reason_is_never_dropped(self):
        # run_case_timeline always writes disagreeing_subtotals now, so every failure reason that is
        # not one of the two matched strings fell through the if/elif chain and produced no line.
        for reason in ('no contiguous run of rows ending at the total adds up to it to the cent '
                       '(tried up to 2 page(s) back)',
                       'all labeled additive items do not equal the stated total to the cent',
                       'invalid or sub-cent monetary figure',
                       'amount pages have unresolved reading gaps'):
            check = failed_check(entry_id='232820355')
            check['reason'] = reason
            r = CV.assess(timeline('X', checks=[check], attachments=[read_attachment()]))
            self.assertEqual(r['verdict'], 'incomplete', reason)
            self.assertTrue(any(reason in m for m in r['missing']), (reason, r['missing']))

    def test_a_verified_check_whose_amount_is_not_a_number_does_not_read_as_supported(self):
        for amount in ('1746032.70', '', True, [1746032.70], {'v': 1}):
            r = CV.assess(timeline('X', checks=[ok_check(amount=amount)],
                                   attachments=[read_attachment()]))
            self.assertEqual(r['verdict'], 'incomplete', amount)
            self.assertTrue(any('is not a number' in m for m in r['missing']), r['missing'])

    def test_an_unread_duplicate_is_a_gap_and_a_no_document_one_is_a_note(self):
        walled = judgment_row('79', status='docket_duplicate_inferred', role='docket_duplicate',
                              reason='its document is behind the county login and was not read; '
                                     'entry 80 the same day with the same docket code has a read '
                                     'copy, so this is taken as the same judgment listed twice '
                                     '(inferred, not read)')
        no_image = judgment_row('79', status='docket_duplicate_inferred', role='docket_duplicate',
                                reason='no document image; entry 80 the same day with the same '
                                       'docket code has one, so this is taken as the same judgment '
                                       'listed twice (inferred, not read)')
        r = CV.assess(timeline('X', duplicates=['79'],
                               judgments=[judgment_row('232820355'), walled],
                               checks=[ok_check()], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        r = CV.assess(timeline('X', duplicates=['79'],
                               judgments=[judgment_row('232820355'), no_image],
                               checks=[ok_check()], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'supported', r['missing'])
        self.assertTrue(any('rests on the docket index' in n for n in r['notes']), r['notes'])

    def test_a_duplicate_with_no_saved_reason_holds_the_case(self):
        r = CV.assess(timeline('X', duplicates=['79'], checks=[ok_check()],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')

    def test_the_report_says_how_many_reasons_it_did_not_show(self):
        r = CV.assess(timeline('X', kind='unclear', reason='Undated dispositive entry prevents '
                                                           'reliable chronology.',
                               duplicates=['79'], checks=[failed_check(entry_id='232820355')],
                               attachments=[{'entry_id': '232820355', 'state': 'restricted'}]))
        md = CV.render_markdown([r])
        self.assertTrue(len(r['conflicts'] + r['missing'] + r['supported_by']) > 3)
        self.assertIn('more, in the JSON', md)

    def test_a_docket_with_no_bankruptcy_does_not_render_the_stay_as_unknown(self):
        clean = CV.assess(timeline('X', checks=[ok_check()], attachments=[read_attachment()]))
        self.assertIn('none on the docket', CV.render_markdown([clean]))
        unknown = CV.assess(timeline('Y', stay=None, history=[{'event': 'stayed'}],
                                    checks=[ok_check()], attachments=[read_attachment()]))
        self.assertIn('unknown', CV.render_markdown([unknown]))

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
            proc = run_cli('--dossiers', folder, dealflow=folder)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertIn('GOOD', [r['case'] for r in report['verdicts']])

    def test_a_rerun_does_not_read_its_own_report_as_a_case(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text(json.dumps(
                timeline('A', checks=[ok_check()], attachments=[read_attachment()])))
            for _ in range(2):
                proc = run_cli('--dossiers', folder, dealflow=folder)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn('case-verdicts', proc.stdout.split('SKIPPED')[-1]
                             if 'SKIPPED' in proc.stdout else '')

    def test_both_write_paths_go_through_the_output_path_guard(self):
        # Every other writer in this chain routes through case_review.output_path, which refuses a
        # path outside paths.DEALFLOW_DIR, inside OneDrive, or inside a Git repository - CLAUDE.md's
        # Known Folder Move rule. Routing only --out through it left the DEFAULT branch, the one
        # that actually runs, writing case numbers and judgment amounts wherever --dossiers pointed.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text(json.dumps(
                timeline('A', checks=[ok_check()], attachments=[read_attachment()])))
            with tempfile.TemporaryDirectory() as elsewhere:
                # The default path, with DEALFLOW_DIR somewhere else entirely: refused, and the
                # verdicts still print, so only the file is lost.
                proc = run_cli('--dossiers', folder, dealflow=elsewhere)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn('refused by the output guard', proc.stdout)
                self.assertIn('supported', proc.stdout)
                self.assertNotIn('Traceback', proc.stderr)
                self.assertFalse((folder / 'case-verdicts.json').exists())
                # --out, pointed outside DEALFLOW_DIR: the same refusal.
                proc = run_cli('--dossiers', folder, '--out', folder / 'report.json',
                               dealflow=elsewhere)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertFalse((folder / 'report.json').exists())

    def test_an_out_ending_in_md_does_not_eat_the_json(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text(json.dumps(
                timeline('A', checks=[ok_check()], attachments=[read_attachment()])))
            proc = run_cli('--dossiers', folder, '--out', folder / 'report.md', dealflow=folder)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads((folder / 'report.md').read_text())['verdicts'][0]['case'],
                             'A')
            self.assertIn('| Case | Verdict |', (folder / 'report-report.md').read_text())

    def test_more_than_twenty_dossier_gaps_says_how_many_were_dropped(self):
        r = CV.assess(timeline('X', checks=[ok_check()], attachments=[read_attachment()]),
                      {'open_gaps': ['gap %d' % i for i in range(25)]})
        self.assertTrue(any('5 more open gap(s)' in n for n in r['notes']), r['notes'])


class FourthReviewTests(unittest.TestCase):
    """Findings from the fourth independent review. Every test fails on 9caf846.

    Two of them drive miami_case_timeline.build_timeline, because both bugs were invisible to any
    hand-written fixture: the producer's real output was the evidence.
    """

    @staticmethod
    def built(entries, as_of='2026-09-23', controlling='140', amount=500000.00):
        import miami_case_timeline as T
        t = T.build_timeline('SYNTHETIC', {'entries': [
            {'source_id': str(n), 'expected_documents': 0,
             'metadata': {'eventID': n, 'eventDate': d, 'docketDescrition': text}}
            for n, text, d in entries], 'pagination_verified': True}, [], as_of)
        t['judgments'] = {'controlling_entry': controlling, 'controlling_reason':
                          'one operative judgment after amendments, vacaturs and satisfactions',
                          'judgments': [judgment_row(controlling)],
                          'docket_duplicates_inferred': []}
        t['amount_vision'] = {'amount_checks': [ok_check(controlling, 'court:%s:1' % controlling,
                                                        amount)]}
        t['coverage'] = {'attachments': [read_attachment(controlling)], 'complete': False}
        return t

    def test_a_sale_notice_the_classifier_labels_conflicts_with_a_stay(self):
        t = self.built([(100, 'Complaint', '01/05/2026'),
                        (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                        (145, 'Notice of Foreclosure Sale on 12/28/2026', '07/01/2026'),
                        (150, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '08/01/2026')])
        self.assertIs(t['stay_in_effect'], True)
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'conflicted', (r['missing'], r['notes']))
        self.assertTrue(any('sale going ahead' in c for c in r['conflicts']), r['conflicts'])

    def test_a_sale_phrasing_the_classifier_misses_is_a_gap_not_a_guess(self):
        # classify labels "Notice of Foreclosure Sale" but leaves "Notice of RESCHEDULED Foreclosure
        # Sale", "Amended Notice of Rescheduled Foreclosure Sale" and "Notice of Resetting
        # Foreclosure Sale" as kind 'other'. Two earlier rounds tried to classify those here - first
        # by kind (missed them, false supported), then by regex over the entry's words (matched
        # motions, objections and denials, false supported AND false conflicted). This module does
        # not classify dockets. An unlabelled sale-worded entry means the answer is not in the file.
        for phrasing in ('Notice of Rescheduled Foreclosure Sale on 12/28/2026',
                         'Amended Notice of Rescheduled Foreclosure Sale on 12/28/2026',
                         'Notice of Resetting Foreclosure Sale on 12/28/2026'):
            t = self.built([(100, 'Complaint', '01/05/2026'),
                            (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                            (145, phrasing, '07/01/2026'),
                            (150, 'Suggestion of Bankruptcy Chapter 13', '08/01/2026')])
            r = CV.assess(t)
            self.assertEqual(r['verdict'], 'incomplete', (phrasing, r))
            self.assertTrue(any('labelled neither a sale notice' in m for m in r['missing']),
                            (phrasing, r['missing']))
            self.assertEqual(r['conflicts'], [], phrasing)

    def test_a_bankruptcy_the_stay_history_never_saw_is_not_a_clean_docket(self):
        # build_timeline drops an entry with no date, or a date after as_of, before building
        # stay_history. Both left stay_history empty and stay_in_effect None, and the report then
        # printed "none on the docket" over a docket that has a bankruptcy on it - in the
        # after-as_of case with a verdict of 'supported' beside it.
        for date in ('', '12/01/2026'):
            t = self.built([(100, 'Complaint', '01/05/2026'),
                            (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                            (150, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', date)])
            self.assertEqual(t['stay_history'], [], date)
            r = CV.assess(t)
            self.assertEqual(r['verdict'], 'incomplete', (date, r))
            self.assertTrue(any('stay history never took in' in m for m in r['missing']),
                            (date, r['missing']))
            self.assertNotIn('none on the docket', CV.render_markdown([r]))

    def test_a_clean_docket_still_says_none_on_the_docket(self):
        t = self.built([(100, 'Complaint', '01/05/2026'),
                        (140, 'Final Judgment of Foreclosure', '06/10/2026')])
        r = CV.assess(t)
        self.assertIn('none on the docket', CV.render_markdown([r]))

    def test_the_subtotal_reason_the_pipeline_really_writes_is_named(self):
        # verify_document cannot emit disagreeing_subtotals: vision_rows marks every row `explicit`
        # and _resolve_subtotal never returns None for one, so the notes it is collected from are
        # never written. What a saved check carries is this reason, for both a subtotal whose
        # members could not be read and one whose members do not add up.
        r = CV.assess(timeline('X', checks=[failed_check(entry_id='232820355',
                                                         reason=CV.SUBTOTAL_DISAGREES)],
                               attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any(CV.SUBTOTAL_DISAGREES in m and 'does not say which' in m
                            for m in r['missing']), r['missing'])

    def test_verify_document_really_cannot_emit_disagreeing_subtotals(self):
        # The producer contract behind the test above, so a later change to judgment_money that
        # starts emitting the key makes this fail and the conflict branch gets re-examined.
        import judgment_money as JM
        figures = [{'id': 'i1', 'kind': 'charge', 'label': 'Principal', 'amount': '1000.00',
                    'page': 2, 'confident': True},
                   {'id': 'i2', 'kind': 'charge', 'label': '2019 Statutory Interest',
                    'amount': '10.30', 'page': 2, 'confident': True},
                   {'id': 'i3', 'kind': 'charge', 'label': '2020 Statutory Interest',
                    'amount': '20.30', 'page': 2, 'confident': True},
                   {'id': 's1', 'kind': 'subtotal', 'label': 'Interest Total', 'amount': '30.00',
                    'item_ids': ['i2', 'i3'], 'page': 2, 'confident': True},
                   {'id': 't1', 'kind': 'total', 'label': 'AMENDED TOTAL', 'amount': '1030.00',
                    'page': 2, 'confident': True}]
        [check] = JM.verify_document(figures, [{'amount': '1030.00', 'page': 2}], {2})
        self.assertFalse(check['ok'])
        self.assertFalse(check.get('disagreeing_subtotals'))
        self.assertEqual(check['reason'], CV.SUBTOTAL_DISAGREES)

    def test_the_gaps_note_names_kinds_not_sentences(self):
        r = CV.assess(timeline('X', checks=[ok_check()], attachments=[read_attachment()],
                               gaps=[{'kind': 'inventory_completeness_unknown',
                                      'reason': 'Docket pagination completeness is not verified.'},
                                     {'kind': 'page_unreadable', 'reason': 'No image indexed by '
                                                                          'county.'}]))
        note = next(n for n in r['notes'] if 'gaps of its own' in n)
        self.assertIn('inventory_completeness_unknown', note)
        self.assertNotIn('Docket pagination', note)

    def test_a_case_that_raises_is_named_in_the_report_not_swallowed(self):
        # The per-case guard could not be reached with any input I could build - the type guards
        # absorb malformed shapes - so it and its test were decorative. This drives main() with a
        # for_saved_case that raises, which is what the guard is for.
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text(json.dumps(
                timeline('A', checks=[ok_check()], attachments=[read_attachment()])))
            (folder / 'B.json').write_text(json.dumps({'case': 'B'}))
            (folder / 'B-timeline.json').write_text(json.dumps(timeline('B')))
            real = CV.for_saved_case

            def flaky(path):
                if Path(path).stem == 'B':
                    raise RuntimeError('saved evidence exploded')
                return real(path)

            # paths.py reads DEALFLOW_DIR at import, so the env alone would depend on whether
            # something already imported it in this process. Patch the attribute the guard reads.
            import paths
            with mock.patch.dict(os.environ, {'DEALFLOW_DIR': str(folder)}), \
                    mock.patch.object(paths, 'DEALFLOW_DIR', str(folder)), \
                    mock.patch.object(CV, 'for_saved_case', flaky):
                self.assertEqual(CV.main(['--dossiers', str(folder)]), 0)
            report = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertEqual([r['case'] for r in report['verdicts']], ['A'])
            self.assertTrue(any('exploded' in n for n in report['no_verdict']['unreadable']))
            self.assertIn('saved evidence does not load or does not parse',
                          (folder / 'case-verdicts.md').read_text())


class FifthReviewTests(unittest.TestCase):
    """Findings from the fifth independent review. Seven of these nine fail on 8d67734.

    (An earlier version of this docstring said "every test fails on 8d67734", and a reviewer checked:
    two of them pass on the parent. The claim is corrected rather than the tests dropped.)

    The theme: the round-4 fix over-corrected. Reading `sale_passages` turned "this text contains
    the word sale" into "a sale is scheduled", so every stayed case with a read judgment reported the
    same conflict - which buried the real contradiction AND made the status table's
    2023-020247 = supported unreachable.
    """

    built = staticmethod(FourthReviewTests.__dict__['built'].__func__)

    JUDGMENT_BODY = ('IT IS ORDERED that the Clerk shall sell the property at public sale to the '
                     'highest bidder on the date set by the Clerk.')

    @classmethod
    def with_judgment_body(cls, entries, as_of='2026-09-23', controlling='140'):
        """The producer's output with the judgment's real body text read, which is where
        sale_passages comes from."""
        import miami_case_timeline as T
        docs = [{'entry_ref': controlling, 'source_ref': 'court:%s:1' % controlling,
                 'reading': {'pages': [{'page': 1, 'text': cls.JUDGMENT_BODY, 'chars': 120,
                                        'outcome': 'text', 'text_source': 'embedded'}]},
                 'manifest': {'document_key': 'k'}}]
        t = T.build_timeline('SYNTHETIC', {'entries': [
            {'source_id': str(n), 'expected_documents': 0,
             'metadata': {'eventID': n, 'eventDate': d, 'docketDescrition': text}}
            for n, text, d in entries], 'pagination_verified': True}, docs, as_of)
        t['judgments'] = {'controlling_entry': controlling, 'controlling_reason':
                          'one operative judgment after amendments, vacaturs and satisfactions',
                          'judgments': [judgment_row(controlling)],
                          'docket_duplicates_inferred': []}
        t['amount_vision'] = {'amount_checks': [ok_check(controlling, 'court:%s:1' % controlling,
                                                        500000.00)]}
        t['coverage'] = {'attachments': [read_attachment(controlling)], 'complete': False}
        return t

    def test_the_judgment_ordering_a_sale_is_not_a_sale_on_the_calendar(self):
        # A Florida final judgment of foreclosure always orders the clerk to sell, and the producer
        # appends every body line matching sale|sell|auction|reset|reschedul to sale_passages,
        # ungated by kind. So the controlling judgment's own document made a stayed case conflicted
        # with no sale ever noticed.
        t = self.with_judgment_body([(100, 'Complaint', '01/05/2026'),
                                     (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                                     (150, 'Suggestion of Bankruptcy Chapter 13', '08/01/2026')])
        self.assertTrue(any(e.get('sale_passages') for e in t['entries']),
                        'the fixture no longer reproduces the condition')
        self.assertIs(t['stay_in_effect'], True)
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['conflicts'], r['missing']))
        self.assertTrue(any('stay is in effect' in n for n in r['notes']))

    def test_a_bankruptcy_petition_asking_to_stop_a_sale_is_not_a_sale_on_the_calendar(self):
        t = self.built([(100, 'Complaint', '01/05/2026'),
                        (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                        (150, 'Suggestion of Bankruptcy; debtor asks the Court to stop the '
                              'foreclosure sale', '08/01/2026')])
        r = CV.assess(t)
        self.assertEqual(r['conflicts'], [], r['conflicts'])

    def test_a_labelled_notice_of_sale_under_a_stay_is_still_conflicted(self):
        # The round-4 finding must stay fixed for the phrasing the producer does label.
        t = self.with_judgment_body([(100, 'Complaint', '01/05/2026'),
                                     (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                                     (145, 'Notice of Foreclosure Sale on 12/28/2026', '07/01/2026'),
                                     (150, 'Suggestion of Bankruptcy Chapter 13', '08/01/2026')])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'conflicted', (r['missing'], r['notes']))
        self.assertTrue(any('entry 145' in c for c in r['conflicts']), r['conflicts'])

    def test_a_cancellation_the_producer_calls_other_still_cancels(self):
        # "Notice of Cancellation of Foreclosure Sale" classifies as kind 'other'. Testing only
        # kind == order_cancelling_sale read it as the NEWEST sale notice, so a cancelled sale
        # reported as going ahead.
        t = self.with_judgment_body([(100, 'Complaint', '01/05/2026'),
                                     (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                                     (145, 'Notice of Foreclosure Sale on 12/28/2026', '07/01/2026'),
                                     (148, 'Notice of Cancellation of Foreclosure Sale',
                                      '07/10/2026'),
                                     (150, 'Suggestion of Bankruptcy Chapter 13', '08/01/2026')])
        r = CV.assess(t)
        self.assertEqual(r['conflicts'], [], r['conflicts'])

    def test_an_order_denying_a_motion_to_reschedule_is_not_a_sale(self):
        t = self.with_judgment_body([(100, 'Complaint', '01/05/2026'),
                                     (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                                     (145, 'Notice of Foreclosure Sale on 12/28/2026', '07/01/2026'),
                                     (147, 'Order Cancelling Foreclosure Sale', '07/20/2026'),
                                     (149, 'Order Denying Defendant Motion to Reschedule the Sale',
                                      '07/25/2026'),
                                     (150, 'Suggestion of Bankruptcy Chapter 13', '08/01/2026')])
        r = CV.assess(t)
        self.assertEqual(r['conflicts'], [], r['conflicts'])

    def test_a_second_petition_after_an_earlier_one_is_not_absorbed(self):
        # The guard asked whether stay_history was EMPTY, so a docket with an earlier bankruptcy
        # swallowed a petition dated after the run's as_of: verdict 'supported', stay column "no".
        t = self.built([(100, 'Complaint', '01/05/2026'),
                        (110, 'Suggestion of Bankruptcy Chapter 7', '02/01/2026'),
                        (120, 'Order Granting Relief from Bankruptcy Stay', '03/01/2026'),
                        (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                        (160, 'Suggestion of Bankruptcy Chapter 13 case 26-99999', '12/01/2026')])
        self.assertTrue(t['stay_history'], 'the fixture no longer reproduces the condition')
        self.assertIs(t['stay_in_effect'], False)
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', r)
        self.assertTrue(any('160' in m and 'never took in' in m for m in r['missing']), r['missing'])
        self.assertNotIn('| no |', CV.render_markdown([r]))

    def test_a_bankruptcy_the_history_did_take_in_is_not_reported_as_unseen(self):
        t = self.built([(100, 'Complaint', '01/05/2026'),
                        (110, 'Suggestion of Bankruptcy Chapter 7', '02/01/2026'),
                        (120, 'Order Granting Relief from Bankruptcy Stay', '03/01/2026'),
                        (140, 'Final Judgment of Foreclosure', '06/10/2026')])
        r = CV.assess(t)
        self.assertFalse(any('never took in' in m for m in r['missing']), r['missing'])
        self.assertEqual(r['verdict'], 'supported', (r['conflicts'], r['missing']))

    def test_a_timeline_that_will_not_parse_is_not_reported_as_never_run(self):
        # _load swallowed ValueError, so a partial write - the real failure this has to survive -
        # was filed as "no whole-case timeline saved", indistinguishable from a case never run.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text('{ not json')
            proc = run_cli('--dossiers', folder, dealflow=folder)
            self.assertIn('UNREADABLE', proc.stdout)
            self.assertNotIn('SKIPPED', proc.stdout)
            report = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertEqual(report['no_verdict']['no_timeline_saved'], [])
            self.assertTrue(any('A-timeline.json' in n for n in report['no_verdict']['unreadable']))

    def test_a_run_where_every_case_fails_still_writes_the_report(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A'}))
            proc = run_cli('--dossiers', folder, dealflow=folder)
            self.assertTrue((folder / 'case-verdicts.json').exists(), proc.stdout)
            report = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertEqual(report['verdicts'], [])
            self.assertTrue(report['no_verdict']['no_timeline_saved'])


class SixthReviewTests(unittest.TestCase):
    """Findings from the sixth independent review.

    Its two disqualifying findings were both in the sale rule, which had now been rewritten three
    times and broken in a different direction each time. The response was not a fourth regex: this
    module's contract is to RESTATE what other modules computed, and deciding whether a sale is
    scheduled is a classification job. `_sale_state` now answers live / unknown / none from the
    producer's own labels, and "unknown" is a gap - so neither a false conflict nor a false clean
    bill is reachable through a phrasing nobody anticipated.
    """

    built = staticmethod(FourthReviewTests.__dict__['built'].__func__)
    with_judgment_body = staticmethod(FifthReviewTests.__dict__['with_judgment_body'].__func__)

    BASE = [(100, 'Complaint', '01/05/2026'),
            (140, 'Final Judgment of Foreclosure', '06/10/2026'),
            (145, 'Notice of Foreclosure Sale on 12/28/2026', '07/01/2026')]
    STAY = (150, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '08/01/2026')

    def test_a_motion_or_denial_about_the_sale_never_clears_a_stay(self):
        # Matching 'sale' + 'cancel|vacate' on the entry's own words treated an order DENYING a
        # motion to cancel, an unruled motion, and an objection as cancellations - and because
        # cancellation was tested first, each printed 'supported' over a live 11 USC 362 stay with a
        # real notice of sale on the docket.
        for phrasing in ('Order Denying Defendants Motion to Cancel Foreclosure Sale',
                         'Motion to Cancel Foreclosure Sale',
                         'Defendants Objection to Foreclosure Sale and Motion to Vacate the Sale',
                         'Notice of Hearing on Motion to Cancel Foreclosure Sale'):
            t = self.built(self.BASE + [(147, phrasing, '07/10/2026'), self.STAY])
            r = CV.assess(t)
            self.assertNotEqual(r['verdict'], 'supported', (phrasing, r))
            self.assertFalse(any('nothing here clears anyone' in n and not r['conflicts']
                                 and not r['missing'] for n in r['notes']), phrasing)

    def test_an_order_cancelling_and_resetting_is_a_sale_not_a_cancellation(self):
        # The producer classifies this as order_resetting_sale and it carries a NEW sale date. The
        # cancel-first rule threw both away and printed 'supported'.
        t = self.built(self.BASE + [(147, 'Order Cancelling and Resetting Foreclosure Sale to '
                                          '03/15/2027', '07/10/2026'), self.STAY])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'conflicted', r)
        self.assertTrue(any('sale going ahead' in c for c in r['conflicts']), r['conflicts'])

    def test_a_stay_document_passage_is_not_a_sale_on_the_docket(self):
        # operative_text is BODY text on three producer paths - a stay-carrier entry takes it from
        # the bankruptcy order's own passage (:379), a judgment from its first-page title plus
        # uppercase continuation lines (:364), and a cancellation gets body reasons appended (:387).
        # Reading operative_text therefore reproduced the round-5 bug: the stay itself, and the
        # judgment itself, counted as "a sale on the docket". Only `description` is read now, and
        # only to notice an unlabelled entry.
        import miami_case_timeline as T
        docs = [{'entry_ref': '150', 'source_ref': 'court:150:1',
                 'reading': {'pages': [{'page': 1, 'outcome': 'text', 'text_source': 'embedded',
                                        'chars': 200,
                                        'text': 'ORDER REINSTATING CHAPTER 13 CASE\nThe automatic '
                                                'stay is hereby reinstated as to the foreclosure '
                                                'sale reset for December 28, 2026.'}]},
                 'manifest': {'document_key': 'k'}}]
        t = T.build_timeline('SYNTHETIC', {'entries': [
            {'source_id': str(n), 'expected_documents': 0,
             'metadata': {'eventID': n, 'eventDate': d, 'docketDescrition': text}}
            for n, text, d in [(100, 'Complaint', '01/05/2026'),
                               (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                               (145, 'Notice of Foreclosure Sale on 12/28/2026', '07/01/2026'),
                               (147, 'Order Cancelling Foreclosure Sale', '07/20/2026'),
                               (150, 'Suggestion of Bankruptcy Chapter 13', '08/01/2026')]],
            'pagination_verified': True}, docs, '2026-09-23')
        t['judgments'] = {'controlling_entry': '140', 'controlling_reason':
                          'one operative judgment after amendments, vacaturs and satisfactions',
                          'judgments': [judgment_row('140')], 'docket_duplicates_inferred': []}
        t['amount_vision'] = {'amount_checks': [ok_check('140', 'court:140:1', 500000.00)]}
        t['coverage'] = {'attachments': [read_attachment('140')], 'complete': False}
        r = CV.assess(t)
        # The sale was cancelled by entry 147, after the notice. The stay's own document must not
        # put it back.
        self.assertFalse(any('150' in c for c in r['conflicts']), r['conflicts'])

    def test_an_unruled_motion_cannot_resurrect_a_cancelled_sale(self):
        t = self.built(self.BASE + [(147, 'Order Cancelling Foreclosure Sale', '07/10/2026'),
                                    (149, 'Plaintiff Motion to Reschedule Foreclosure Sale',
                                     '07/20/2026'), self.STAY])
        r = CV.assess(t)
        self.assertFalse(any('no cancellation on or after it' in c for c in r['conflicts']),
                         r['conflicts'])

    def test_a_cancellation_on_the_same_day_as_the_notice_counts(self):
        t = self.built(self.BASE + [(146, 'Order Cancelling Foreclosure Sale', '07/01/2026'),
                                    self.STAY])
        r = CV.assess(t)
        self.assertFalse(any('no cancellation on or after it' in c for c in r['conflicts']),
                         r['conflicts'])

    def test_a_relief_order_after_the_cutoff_is_not_a_fresh_petition(self):
        # BANKRUPTCY_KINDS had included relief, dismissal and discharge - stay-ENDING events. One of
        # those dated after the run's as_of says nothing about the stay state at as_of, and it made a
        # clean case incomplete.
        t = self.built([(100, 'Complaint', '01/05/2026'),
                        (110, 'Suggestion of Bankruptcy Chapter 7', '02/01/2026'),
                        (120, 'Order Granting Relief from Bankruptcy Stay', '03/01/2026'),
                        (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                        (160, 'Order Granting Relief from Bankruptcy Stay', '12/01/2026')])
        r = CV.assess(t)
        self.assertFalse(any('never took in' in m for m in r['missing']), r['missing'])

    def test_a_petition_after_the_cutoff_is_still_a_gap(self):
        t = self.built([(100, 'Complaint', '01/05/2026'),
                        (110, 'Suggestion of Bankruptcy Chapter 7', '02/01/2026'),
                        (120, 'Order Granting Relief from Bankruptcy Stay', '03/01/2026'),
                        (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                        (160, 'Suggestion of Bankruptcy Chapter 13 case 26-99999', '12/01/2026')])
        r = CV.assess(t)
        self.assertTrue(any('160' in m and 'never took in' in m for m in r['missing']), r['missing'])

    def test_a_sale_with_a_certificate_of_title_is_not_going_ahead(self):
        # A petition filed after the sale completed and title issued is ordinary, not a
        # contradiction. This was pinned by a hand-made `sale_held` for one round: a sale_held block
        # is DERIVED from sale_bid / sale_deposit entries, so it cannot exist on a docket that has
        # none, and with the producible docket built the behaviour it claimed was reversed. Drive
        # the producer - the clerk's money rows and the certificate are docket entries like any other.
        t = self.built([(100, 'Complaint', '01/05/2026'),
                        (140, 'Final Judgment of Foreclosure', '06/10/2026'),
                        (145, 'Notice of Foreclosure Sale on 07/20/2026', '06/20/2026'),
                        (160, 'Bid Amount', '07/20/2026'),
                        (161, 'Mortgage Foreclosure Deposit', '07/20/2026'),
                        (170, 'Certificate of Title', '07/30/2026'),
                        (180, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '08/15/2026')])
        self.assertEqual((t['sale_held'] or {}).get('certificate'), '170')
        self.assertIs(t['stay_in_effect'], True)
        r = CV.assess(t)
        self.assertFalse(any('sale going ahead' in c for c in r['conflicts']), r['conflicts'])
        self.assertEqual(r['verdict'], 'supported', (r['conflicts'], r['missing']))

    def test_a_dossier_that_will_not_parse_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text('{ not json')
            (folder / 'A-timeline.json').write_text(json.dumps(
                timeline('A', checks=[ok_check()], attachments=[read_attachment()])))
            proc = run_cli('--dossiers', folder, dealflow=folder)
            self.assertIn('UNREADABLE', proc.stdout)
            report = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertTrue(any('A.json' in n for n in report['no_verdict']['unreadable']))


class SeventhReviewTests(unittest.TestCase):
    """The seventh independent review found four reachable wrong verdicts in the sale rule the sixth
    round redesigned - two of them the false `supported` over a live stay the whole redesign was for.
    Every case here drives miami_case_timeline.build_timeline: three of the previous four rounds'
    false claims came from a fixture carrying a shape the producer cannot write, and one of THIS
    round's findings was a test pinned by a hand-made `sale_held` that cannot exist.
    """
    @staticmethod
    def built(entries, as_of='2026-09-23', controlling='140', amount=500000.00):
        """entries are (id, description, comments, date) - the producer classifies on both."""
        import miami_case_timeline as T
        t = T.build_timeline('SYNTHETIC', {'entries': [
            {'source_id': str(n), 'expected_documents': 0,
             'metadata': {'eventID': n, 'eventDate': d, 'docketDescrition': text,
                          'comments': comments}}
            for n, text, comments, d in entries], 'pagination_verified': True}, [], as_of)
        t['judgments'] = {'controlling_entry': controlling, 'controlling_reason':
                          'one operative judgment after amendments, vacaturs and satisfactions',
                          'judgments': [judgment_row(controlling)],
                          'docket_duplicates_inferred': []}
        t['amount_vision'] = {'amount_checks': [ok_check(controlling, 'court:%s:1' % controlling,
                                                        amount)]}
        t['coverage'] = {'attachments': [read_attachment(controlling)], 'complete': False}
        return t

    OPEN = [(100, 'Complaint', '', '01/05/2026'),
            (140, 'Final Judgment of Foreclosure', '', '06/10/2026')]
    PETITION = (180, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '', '08/15/2026')

    def test_a_resale_noticed_after_a_certificate_is_not_a_clean_bill(self):
        # `sale_held` describes only the MOST RECENT held sale (sale_held: `day = max(...)`), so
        # returning 'none' on its certificate hid every sale noticed afterwards. A sale held in
        # March, a certificate, an order of resale, a new notice for December and then a Chapter 13
        # read `supported` with the judgment amount printed as verified.
        t = self.built(self.OPEN + [
            (145, 'Notice of Foreclosure Sale on 03/02/2026', '', '02/01/2026'),
            (160, 'Bid Amount', '', '03/02/2026'),
            (161, 'Mortgage Foreclosure Deposit', '', '03/02/2026'),
            (170, 'Certificate of Sale', '', '03/05/2026'),
            (175, 'Order Granting Plaintiff Motion for Resale', '', '04/01/2026'),
            (178, 'Notice of Foreclosure Sale on 12/28/2026', '', '05/01/2026'),
            self.PETITION])
        self.assertEqual((t['sale_held'] or {}).get('certificate'), '170')
        self.assertIs(t['stay_in_effect'], True)
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'conflicted', (r['missing'], r['notes']))
        self.assertTrue(any('178' in c for c in r['conflicts']), r['conflicts'])

    def test_a_sale_worded_only_in_the_clerks_comments_is_not_invisible(self):
        # build_timeline classifies on description + comments (:345). Reading `description` alone
        # meant an entry whose sale wording is in the comments was neither labelled a sale notice nor
        # flagged unlabelled - it fell out of the rule entirely, and a live sale under a stay read
        # `supported`. Both halves of this shape are in _casetimelinetest (:362, :461).
        t = self.built(self.OPEN + [
            (145, 'Notice:', 'OF FORECLOSURE SALE SET FOR 12/28/2026 AT 9:00 A.M.', '07/01/2026'),
            self.PETITION])
        self.assertIs(t['stay_in_effect'], True)
        r = CV.assess(t)
        self.assertNotEqual(r['verdict'], 'supported', (r['notes'], r['supported_by']))
        self.assertTrue(any('145' in m for m in r['missing']) or
                        any('145' in c for c in r['conflicts']), r)

    def test_a_certificate_of_title_ends_the_sale_without_the_clerks_money_rows(self):
        # sale_held is None whenever the clerk's `Bid Amount` / `Mortgage Foreclosure Deposit` rows
        # are absent, so the derived block was the ONLY thing stopping a certificate from reading as
        # a sale going ahead - and a notice plus a certificate of title plus a later petition came
        # back `conflicted` over a sale that is finished and titled.
        for certificate in ('Certificate of Title', 'Certificate of Sale'):
            t = self.built(self.OPEN + [
                (145, 'Notice of Foreclosure Sale on 07/20/2026', '', '06/20/2026'),
                (170, certificate, '', '07/30/2026'),
                self.PETITION])
            self.assertIsNone(t['sale_held'], certificate)
            self.assertIs(t['stay_in_effect'], True)
            r = CV.assess(t)
            self.assertEqual(r['verdict'], 'supported', (certificate, r['conflicts'], r['missing']))

    def test_a_classified_entry_is_never_reported_as_unclassified(self):
        # The `unknown` phrase said the later entries "mention a sale without being classified". A
        # certificate of sale IS classified, so that told the human the opposite of what the producer
        # saved - and every printed reason in this module is meant to be the producer's own state.
        src = MODULE.read_text()
        self.assertNotIn('without being classified', src)
        self.assertIn('labelled neither a sale notice nor ', src)

    def test_the_stays_own_words_are_not_an_unresolved_sale(self):
        # The unlabelled check ran on every kind but the three sale kinds, so a petition whose
        # docket description names the sale it stays counted as an unclassified sale entry: a docket
        # with no sale notice anywhere on it came back `incomplete`. Only the kinds classify uses
        # when it did not recognise the entry can be unlabelled.
        for text in ('Suggestion of Bankruptcy Chapter 13 case 26-12345; foreclosure sale stayed',
                     'Notice of Reinstatement of Automatic Stay; foreclosure sale stayed'):
            t = self.built(self.OPEN + [(150, text, '', '08/01/2026')])
            self.assertIs(t['stay_in_effect'], True)
            r = CV.assess(t)
            self.assertEqual(r['verdict'], 'supported', (text, r['missing'], r['conflicts']))

    def test_a_hearing_entry_that_mentions_a_sale_is_still_a_gap(self):
        # The producer overwrites kind with 'hearing' for anything on a calendar eventType except a
        # notice of sale (:383), so 'hearing' has to stay in the unlabelled set: gating the check on
        # 'other' alone would have dropped an order resetting a sale carried on a hearing event.
        import miami_case_timeline as T
        t = T.build_timeline('SYNTHETIC', {'entries': [
            {'source_id': str(n), 'expected_documents': 0,
             'metadata': dict({'eventID': n, 'eventDate': d, 'docketDescrition': text}, **extra)}
            for n, text, d, extra in [
                (100, 'Complaint', '01/05/2026', {}),
                (140, 'Final Judgment of Foreclosure', '06/10/2026', {}),
                (145, 'Order Resetting Foreclosure Sale', '07/01/2026', {'eventType': 'Hearing'}),
                (150, 'Suggestion of Bankruptcy Chapter 13', '08/01/2026', {})]],
            'pagination_verified': True}, [], '2026-09-23')
        self.assertEqual(next(e['kind'] for e in t['entries'] if e['entry_id'] == '145'), 'hearing')
        t['judgments'] = {'controlling_entry': '140', 'controlling_reason': 'one operative judgment',
                          'judgments': [judgment_row('140')], 'docket_duplicates_inferred': []}
        t['amount_vision'] = {'amount_checks': [ok_check('140', 'court:140:1', 500000.00)]}
        t['coverage'] = {'attachments': [read_attachment('140')], 'complete': False}
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['conflicts'], r['notes']))
        self.assertTrue(any('145' in m for m in r['missing']), r['missing'])



if __name__ == '__main__':
    unittest.main(verbosity=2)
