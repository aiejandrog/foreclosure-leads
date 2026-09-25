"""_verdicttest — case_verdict.assess against the five hand-written pilot verdicts and the edges.

The acceptance test is the first class: the five cases in MIAMI-AUTOMATION-STATUS.md carry verdicts
a person wrote by reading the code's output. The fixtures below reproduce the states that table
cites, so the module has to reach the same word the person did. If a rule here has to be loosened to
make a case pass, that is a policy change and it belongs in the status file, not in this test.
"""
import re
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
    if sale_date or kind == 'sale_scheduled':
        # _transition always writes the key for a 'sale_scheduled' status (:262), with None when it
        # could parse no date from the notice. A fixture that omits it is a shape build_timeline
        # cannot write - the same failure mode as read_attachment's missing `document` - and it is
        # how the unparsed-sale-date gap went untested for sixteen rounds (seventeenth review).
        status['sale_date'] = sale_date or None
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


def read_attachment(entry_id='232820355', document=None):
    # `document` is the source_ref string document_coverage :166 ALWAYS sets on a row built from a
    # fetched attachment. Omitting it made every coverage fixture a shape the producer cannot write,
    # which is how a check reading that key passed the whole suite while flipping a pilot case on
    # real evidence (thirteenth review).
    return {'entry_id': entry_id, 'kind': 'final_judgment', 'state': 'read', 'detail': [],
            'document': document or 'court:%s:1' % entry_id}


class PilotVerdictTests(unittest.TestCase):
    """The five cases from the status table's acceptance section."""

    def test_2024_014878_is_supported(self):
        # "$1,746,032.70 verifies on court:232820355:1 pp. 2-3. Controlling judgment #82.
        #  Sale 2026-09-28."
        r = CV.assess(pilot('2024-014878-CA-01', '232820355', kind='sale_scheduled',
                            sale_date='2026-09-28', checks=[ok_check()],
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
            # 1, not 0: one dossier has no timeline saved, so this run did not cover every case it
            # was given, and a scheduled caller must not read it as clean (eighth review).
            self.assertEqual(proc.returncode, 1, proc.stderr)
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
        # judgment_money raises this one string for several distinct facts, so the line must name
        # the ambiguity rather than assert the first of them (eighth review).
        self.assertTrue(any('either none of them matches it or more than one disagrees' in m
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
                               # Dated BEFORE the fixture's own as_of (2026-09-24): the producer
                               # decides nothing off a later entry, and neither does this module.
                               entries=[{'entry_id': '120', 'kind': 'notice_of_sale',
                                         'date': '2026-08-01'},
                                        {'entry_id': '150', 'kind': 'suggestion_of_bankruptcy',
                                         'date': '2026-08-10'}],
                               checks=[ok_check()], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any('entry 120' in c and 'notice_of_sale' in c for c in r['conflicts']),
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
            # 0: both cases produced a verdict. The type guards absorbed BAD's malformed blocks, so
            # nothing was skipped or lost - which is what separates this from the exit-1 cases.
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
                # Exit 1 because a case was lost, and the report is still written and still names
                # it: reporting the loss and exiting clean are two different things.
                self.assertEqual(CV.main(['--dossiers', str(folder)]), 1)
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

    def test_a_timeline_that_will_not_parse_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text('{ not json')
            proc = run_cli('--dossiers', folder, dealflow=folder)
            self.assertIn('UNREADABLE', proc.stdout)
            report = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertTrue(any('A-timeline.json' in n
                                for n in report['no_verdict']['unreadable']), report)

    def test_a_dossier_that_will_not_parse_keeps_the_verdict_and_names_the_loss(self):
        # It used to discard the whole case. The dossier contributes NOTES only - its open_gaps - and
        # the verdict rests entirely on the timeline, so throwing away a readable verdict over a
        # truncated file that changes nothing about it was the wrong trade (ninth review).
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text('{ not json')
            (folder / 'A-timeline.json').write_text(json.dumps(
                timeline('A', checks=[ok_check()], attachments=[read_attachment()])))
            proc = run_cli('--dossiers', folder, dealflow=folder)
            report = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertEqual([r['case'] for r in report['verdicts']], ['A'], proc.stdout)
            row = report['verdicts'][0]
            self.assertEqual(row['verdict'], 'incomplete', row)
            self.assertTrue(any('does not parse' in m for m in row['missing']), row['missing'])


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

    @staticmethod
    def calendared(rows, as_of='2026-09-23'):
        """Drive the producer with an eventType per entry: (id, description, date, eventType)."""
        import miami_case_timeline as T
        t = T.build_timeline('SYNTHETIC', {'entries': [
            {'source_id': str(n), 'expected_documents': 0,
             'metadata': {'eventID': n, 'eventDate': d, 'docketDescrition': text, 'eventType': ev}}
            for n, text, d, ev in rows], 'pagination_verified': True}, [], as_of)
        t['judgments'] = {'controlling_entry': '140', 'controlling_reason': 'one operative judgment',
                          'judgments': [judgment_row('140')], 'docket_duplicates_inferred': []}
        t['amount_vision'] = {'amount_checks': [ok_check('140', 'court:140:1', 500000.00)]}
        t['coverage'] = {'attachments': [read_attachment('140')], 'complete': False}
        return t

    def test_a_hearing_entry_the_classifier_never_labelled_is_still_a_gap(self):
        # The producer overwrites kind with 'hearing' for anything on a calendar eventType except a
        # notice of sale (:383), so 'hearing' has to stay in the unlabelled set. "Notice of
        # Rescheduled Foreclosure Sale" is one classify leaves as 'other', so on a hearing event both
        # of its labels are unrecognising ones and the answer is a gap.
        t = self.calendared([(100, 'Complaint', '01/05/2026', ''),
                             (140, 'Final Judgment of Foreclosure', '06/10/2026', ''),
                             (145, 'Notice of Rescheduled Foreclosure Sale on 12/28/2026',
                              '07/01/2026', 'Hearing'),
                             (150, 'Suggestion of Bankruptcy Chapter 13', '08/01/2026', '')])
        entry = next(e for e in t['entries'] if e['entry_id'] == '145')
        self.assertEqual((entry['kind'], entry['index_kind']), ('hearing', 'other'))
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['conflicts'], r['notes']))
        self.assertTrue(any('145' in m for m in r['missing']), r['missing'])


class EighthReviewTests(unittest.TestCase):
    """miami_case_timeline :383 overwrites `kind` with 'hearing' for any entry on a calendar
    eventType except a notice of sale, leaving the producer's real label only in `index_kind`. The
    seventh round's fix knew that and closed it in ONE of the three places it reaches. Every case
    here drives the producer with a real eventType.
    """
    calendared = staticmethod(SeventhReviewTests.__dict__['calendared'].__func__)
    OPEN = [(100, 'Complaint', '01/05/2026', ''),
            (140, 'Final Judgment of Foreclosure', '06/10/2026', '')]

    def test_a_bankruptcy_on_a_hearing_event_is_never_none_on_the_docket(self):
        # The worst verdict this module can produce: `supported`, no gaps, and "none on the docket"
        # printed in the stay column, over a live 11 USC 362 stay. All three stay-raising kinds were
        # reachable. The producer's OWN stay_history (:462) keys on the overwritten kind too, so the
        # backstop that exists to catch exactly this was defeated by the same docket.
        for text in ('Suggestion of Bankruptcy Chapter 13 case 26-12345',
                     'Order Staying Action Due To Bankruptcy 11 U.S.C. 362 debtor',
                     'Order Reinstating Automatic Stay chapter 13 debtor'):
            t = self.calendared(self.OPEN + [(150, text, '08/01/2026', 'Hearing')])
            entry = next(e for e in t['entries'] if e['entry_id'] == '150')
            self.assertEqual(entry['kind'], 'hearing', text)
            self.assertIn(entry['index_kind'], CV.BANKRUPTCY_KINDS, text)
            # The producer's own history is empty here - that is its bug, reported not fixed.
            self.assertEqual(t['stay_history'], [], text)
            r = CV.assess(t)
            self.assertNotEqual(r['verdict'], 'supported', (text, r['notes'], r['supported_by']))
            self.assertTrue(any('150' in m for m in r['missing']), (text, r['missing']))
            self.assertNotIn('none on the docket', CV.render_markdown([r]), text)

    def test_a_certificate_of_title_on_a_hearing_event_is_not_a_sale_going_ahead(self):
        # A certificate of title carries no "sale" word, so the unlabelled branch did not catch it
        # either: the sale read as still going ahead and a later petition made it `conflicted`, with
        # a reason saying there was no certificate over a docket whose certificate the producer
        # labelled. It is NOT `supported` either, which this test asserted for two rounds: a
        # calendar event can be a hearing ABOUT the certificate, and nothing saved says which. The
        # contradiction is gone and the case is held as a gap naming the entry.
        t = self.calendared(self.OPEN + [
            (145, 'Notice of Foreclosure Sale on 07/20/2026', '06/20/2026', ''),
            (170, 'Certificate of Title', '07/30/2026', 'Hearing'),
            (180, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '08/15/2026', '')])
        entry = next(e for e in t['entries'] if e['entry_id'] == '170')
        self.assertEqual((entry['kind'], entry['index_kind']), ('hearing', 'certificate_of_title'))
        r = CV.assess(t)
        self.assertFalse(any('sale going ahead' in c for c in r['conflicts']), r['conflicts'])
        self.assertEqual(r['verdict'], 'incomplete', (r['conflicts'], r['missing']))
        self.assertTrue(any('170' in m for m in r['missing']), r['missing'])

    def test_a_held_sale_is_read_even_when_the_status_never_carried_it(self):
        # build_timeline writes sale_outcome only inside `kind == 'sale_scheduled' and sale_date and
        # sale_date < today` (:509). A notice of sale with no parseable date leaves sale_date None, so
        # that branch never runs, and the top-level sale_held block was read only under a stay. The
        # clerk's money rows said the sale was HELD and the verdict vouched for "sale scheduled".
        t = self.calendared(self.OPEN + [
            (145, 'Notice of Foreclosure Sale', '06/20/2026', ''),
            (146, 'Bid Amount', '07/20/2026', ''),
            (147, 'Mortgage Foreclosure Deposit', '07/20/2026', '')])
        self.assertIsNone(t['status'].get('sale_date'))
        self.assertIsNone(t['status'].get('sale_outcome'))
        self.assertEqual((t['sale_held'] or {}).get('date'), '2026-07-20')
        self.assertIsNone((t['sale_held'] or {}).get('certificate'))
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['conflicts'], r['notes']))
        self.assertTrue(any('2026-07-20' in m and 'held' in m for m in r['missing']), r['missing'])

    def test_no_producer_label_is_matched_on_kind_alone(self):
        # The class of bug, not the instance: eight rounds on this module and the same override was
        # closed in one of three places. Every comparison against a producer label must go through
        # _producer_labels, which reads kind AND index_kind.
        import ast
        tree = ast.parse(MODULE.read_text())
        # _relabelled reads `kind` on purpose: it DETECTS the relabel, so 'hearing' is the thing it
        # is looking for, not a label it trusts.
        exempt = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name in ('_producer_labels', '_relabelled')]
        producer_labels = exempt[0]
        # `status` is the case posture, not a docket entry, and `g` is a gap row; neither goes
        # through the :383 override. Anything else reading 'kind' is reading an entry's label.
        # `status` is the case posture and `g` a gap row; `event` is one of reconcile_judgments'
        # own `unmatched` rows, whose 'kind' the producer wrote itself (:739) and which is only
        # quoted back - and which can only ever be 'satisfaction' or 'vacatur' (:722), never the
        # relabel. None of the three is a docket entry, so none goes through the :383 override.
        allowed = {'status', 'g', 'event'}
        inside = {id(n) for f in exempt for n in ast.walk(f)}
        bad = []
        for node in ast.walk(tree):
            if id(node) in inside:
                continue
            receiver = None
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'get' and len(node.args) == 1
                    and getattr(node.args[0], 'value', None) == 'kind'):
                receiver = node.func.value
            elif (isinstance(node, ast.Subscript) and getattr(node.slice, 'value', None) == 'kind'):
                receiver = node.value
            if receiver is None or (isinstance(receiver, ast.Name) and receiver.id in allowed):
                continue
            bad.append((getattr(node, 'lineno', '?'), ast.dump(receiver)[:60]))
        self.assertEqual(bad, [], 'a producer label is read off `kind` alone; miami_case_timeline '
                                  ':383 overwrites it with "hearing" on a calendar eventType, so '
                                  'this must go through _producer_labels')



class NinthReviewTests(unittest.TestCase):
    """The eighth round's fix read `index_kind` as co-equal with `kind`. The ninth found that this
    breaks the other way wherever `kind` came from READING THE DOCUMENT - `kind = body_kind or ik`
    (:361) - and that it does not reach `sale_held` at all, because that summary is computed upstream
    from a bare `kind`. Every case here drives the producer, with eventType and document pages.
    """
    @staticmethod
    def built(rows, as_of='2026-11-20', pages=None):
        """rows are (id, description, date, eventType); pages maps entry id -> page text."""
        import miami_case_timeline as T
        docs = [{'source_ref': str(n), 'document_hash': 'h%s' % n, 'manifest': {'sha256': 'h%s' % n},
                 'reading': {'pages': [{'page': 1, 'outcome': 'text', 'text': text}]}}
                for n, text in (pages or {}).items()]
        t = T.build_timeline('SYNTHETIC', {'entries': [
            {'source_id': str(n), 'expected_documents': 1 if str(n) in (pages or {}) else 0,
             'source_ref': str(n),
             'metadata': {'eventID': n, 'eventDate': d, 'docketDescrition': text, 'eventType': ev}}
            for n, text, d, ev in rows], 'pagination_verified': True}, docs, as_of)
        t['judgments'] = {'controlling_entry': '140', 'controlling_reason': 'one operative judgment',
                          'judgments': [judgment_row('140')], 'docket_duplicates_inferred': []}
        t['amount_vision'] = {'amount_checks': [ok_check('140', 'court:140:1', 500000.00)]}
        t['coverage'] = {'attachments': [read_attachment('140')], 'complete': False}
        return t

    OPEN = [(100, 'Complaint', '01/05/2026', ''),
            (140, 'Final Judgment of Foreclosure', '06/10/2026', '')]

    def test_the_clerks_money_rows_on_a_hearing_event_are_not_lost(self):
        # sale_held is computed upstream from a bare e['kind'] (miami_case_timeline :543), so the :383
        # override empties it: bid and deposit rows on a calendar event made sale_held None and the
        # verdict `supported` over a sale the saved file says was held.
        t = self.built(self.OPEN + [
            (145, 'Notice of Foreclosure Sale on 06/01/2026', '05/01/2026', ''),
            (170, 'Bid Amount', '07/01/2026', 'Hearing'),
            (171, 'Mortgage Foreclosure Deposit', '07/01/2026', 'Hearing')])
        self.assertIsNone(t['sale_held'])
        self.assertEqual([e['index_kind'] for e in t['entries'] if e['entry_id'] in ('170', '171')],
                         ['sale_bid', 'sale_deposit'])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['conflicts'], r['notes']))
        self.assertTrue(any('170' in m and 'held' in m for m in r['missing']), r['missing'])

    def test_no_certificate_is_never_printed_over_a_labelled_certificate(self):
        # The summary's `certificate` is keyed on a bare kind too (:550), so a calendar-typed
        # certificate made the report say "no certificate of sale has followed" about a docket that
        # carries one - a sentence the producer's own saved entry refutes.
        t = self.built(self.OPEN + [
            (145, 'Notice of Foreclosure Sale on 06/01/2026', '05/01/2026', ''),
            (170, 'Bid Amount', '07/01/2026', ''),
            (171, 'Mortgage Foreclosure Deposit', '07/01/2026', ''),
            (175, 'Certificate of Sale', '07/10/2026', 'Hearing')])
        self.assertEqual((t['sale_held'] or {}).get('date'), '2026-07-01')
        self.assertIsNone((t['sale_held'] or {}).get('certificate'))
        r = CV.assess(t)
        self.assertFalse(any('no certificate of sale has followed' in m for m in r['missing']),
                         r['missing'])
        self.assertTrue(any('175' in m for m in r['missing']), r['missing'])

    def test_a_document_read_label_is_not_overruled_by_the_docket_index(self):
        # `kind = body_kind or ik`: where the producer opened the document, `kind` is what the
        # document says and `index_kind` is only the clerk's line. Unioning them let a docket line
        # reading "Certificate of Sale" close a sale whose own document is a notice of sale - and
        # under a live Chapter 13 stay that came back `supported` with the sale never named.
        t = self.built(self.OPEN + [
            (145, 'Certificate of Sale', '07/01/2026', ''),
            (150, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '11/10/2026', '')],
            pages={'145': 'NOTICE OF FORECLOSURE SALE\nthe clerk shall sell the property at public '
                          'sale on 12/28/2026'})
        entry = next(e for e in t['entries'] if e['entry_id'] == '145')
        self.assertEqual((entry['kind'], entry['index_kind'], entry['kind_source']),
                         ('notice_of_sale', 'certificate_of_sale', 'document'))
        self.assertIs(t['stay_in_effect'], True)
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'conflicted', (r['missing'], r['notes']))
        self.assertTrue(any('145' in c for c in r['conflicts']), r['conflicts'])

    def test_a_read_document_that_is_not_a_bankruptcy_filing_does_not_hold_the_case(self):
        # The same union in the conservative direction produced an `incomplete` no further reading
        # can ever clear: the producer opened the document, concluded it is an order on a motion, and
        # left stay_history empty on purpose.
        t = self.built(self.OPEN + [(150, 'Notice of Filing Bankruptcy Petition', '11/10/2026', '')],
                       pages={'150': 'ORDER DENYING MOTION TO COMPEL'})
        entry = next(e for e in t['entries'] if e['entry_id'] == '150')
        self.assertEqual((entry['kind'], entry['index_kind']),
                         ('order_on_motion', 'suggestion_of_bankruptcy'))
        self.assertEqual(t['stay_history'], [])
        r = CV.assess(t)
        self.assertFalse(any('150' in m and 'bankruptcy filing' in m for m in r['missing']),
                         r['missing'])

    def test_the_stay_backstop_still_catches_a_calendar_typed_petition(self):
        # The narrowing must not undo round 8: where the override DID fire, index_kind is still read.
        t = self.built(self.OPEN + [
            (150, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '11/10/2026', 'Hearing')])
        entry = next(e for e in t['entries'] if e['entry_id'] == '150')
        self.assertEqual((entry['kind'], entry['index_kind']),
                         ('hearing', 'suggestion_of_bankruptcy'))
        self.assertEqual(t['stay_history'], [])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['conflicts'], r['notes']))
        self.assertTrue(any('150' in m for m in r['missing']), r['missing'])
        self.assertNotIn('none on the docket', CV.render_markdown([r]))

    def test_a_run_does_not_read_its_own_named_report_as_a_dossier(self):
        # The self-exclusion was hardcoded to the default name, so `--out D/report.json` made every
        # later run read its own report, print SKIPPED and exit 1 for good.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text(json.dumps(
                timeline('A', checks=[ok_check()], attachments=[read_attachment()])))
            first = run_cli('--dossiers', folder, '--out', folder / 'report.json', dealflow=folder)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            second = run_cli('--dossiers', folder, '--out', folder / 'report.json', dealflow=folder)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertNotIn('SKIPPED', second.stdout)



class TenthReviewTests(unittest.TestCase):
    """Nine rounds fixed this one relabel at one site each. The tenth found three more summaries it
    reaches - a vacated judgment and a satisfied one reported `supported` with the amount "verified to
    the cent", and an order resetting a sale under a live stay reported `supported` with the sale
    never named - so the fix stopped being per-site: `_relabelled` holds the case wherever a
    posture-deciding label was lost. Every case drives the producer.
    """
    built = staticmethod(NinthReviewTests.__dict__['built'].__func__)
    OPEN = NinthReviewTests.OPEN

    def test_a_vacated_judgment_on_a_hearing_event_is_never_supported(self):
        # The worst output in the series: reconcile_judgments never saw the vacatur, so the judgment
        # stayed 'operative', status stayed 'judgment_entered' - an allowed settled kind - and the
        # report told a reader the amount was verified to the cent on a judgment the docket vacated.
        for text, index_kind in (('Order Vacating Final Judgment', 'vacatur'),
                                 ('Satisfaction of Judgment', 'satisfaction')):
            t = self.built(self.OPEN + [(150, text, '07/15/2026', 'Hearing')])
            entry = next(e for e in t['entries'] if e['entry_id'] == '150')
            self.assertEqual((entry['kind'], entry['index_kind']), ('hearing', index_kind), text)
            r = CV.assess(t)
            self.assertNotEqual(r['verdict'], 'supported', (text, r['supported_by'], r['notes']))
            self.assertTrue(any('150' in m and index_kind in m for m in r['missing']),
                            (text, r['missing']))

    def test_a_document_read_label_lost_to_the_relabel_holds_the_case(self):
        # :383 exempts only notice_of_sale, so an order resetting a sale is relabelled like anything
        # else - and where `kind` came from READING the document, what the relabel destroyed is saved
        # nowhere: index_kind is 'other' because the docket line is the bare word "Order". Not even
        # the unlabelled-sale gap fires, because that scans description and comments.
        t = self.built(self.OPEN + [
            (160, 'Order', '07/01/2026', 'Hearing'),
            (150, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '08/01/2026', '')],
            pages={'160': 'ORDER RESETTING FORECLOSURE SALE\nThe clerk shall sell the property on '
                          '12/28/2026.'})
        entry = next(e for e in t['entries'] if e['entry_id'] == '160')
        self.assertEqual((entry['kind'], entry['index_kind'], entry['kind_source']),
                         ('hearing', 'other', 'document'))
        self.assertIs(t['stay_in_effect'], True)
        r = CV.assess(t)
        self.assertNotEqual(r['verdict'], 'supported', (r['supported_by'], r['notes']))
        self.assertTrue(any('160' in m and 'read document' in m for m in r['missing']), r['missing'])

    def test_a_bankruptcy_on_the_sale_day_is_not_lost_to_the_relabel(self):
        # sale_held builds bankruptcy_same_day from the bare kind too (:552), so the one fact the
        # producer's own qualification says decides whether the sale stands went missing.
        t = self.built(self.OPEN + [
            (145, 'Notice of Foreclosure Sale on 09/10/2026', '08/01/2026', ''),
            (146, 'Bid Amount', '09/10/2026', ''),
            (147, 'Mortgage Foreclosure Deposit', '09/10/2026', ''),
            (150, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '09/10/2026', 'Hearing')])
        self.assertEqual((t['sale_held'] or {}).get('bankruptcy_same_day'), [])
        r = CV.assess(t)
        self.assertNotEqual(r['verdict'], 'supported', (r['supported_by'], r['notes']))
        # The relabel itself has to be named: the old head held the case for a different reason and
        # said nothing about the sale-day petition, which is the fact that decides the sale.
        self.assertTrue(any('150' in m and 'relabelled' in m for m in r['missing']), r['missing'])

    def test_the_stay_against_a_held_sale_survives_an_emptied_summary(self):
        # _sale_state still short-circuited on the summary, so with the money rows relabelled it read
        # "no sale was held" and the stay-against-sale contradiction became a note. The verdict was
        # held by a separate gap; the contradiction itself was lost.
        t = self.built(self.OPEN + [
            (146, 'Bid Amount', '09/10/2026', 'Hearing'),
            (147, 'Mortgage Foreclosure Deposit', '09/10/2026', 'Hearing'),
            (150, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '09/15/2026', '')])
        self.assertIsNone(t['sale_held'])
        self.assertIs(t['stay_in_effect'], True)
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['conflicts'], r['notes']))
        # Under the stay specifically: _sale_state must report the held sale, not "none".
        self.assertTrue(any('stay is in effect' in m and 'held' in m for m in r['missing']),
                        r['missing'])

    def test_a_certificate_from_an_earlier_sale_does_not_excuse_a_later_one(self):
        # sale_held counts a certificate only when it is dated ON OR AFTER the held sale (:550), so
        # `certificate: None` was CORRECT here. The re-read applied no date filter, so a certificate
        # from a sale two years earlier downgraded the stay-against-sale contradiction to a gap and
        # printed that the summary had not taken it in - which the summary had, and excluded.
        t = self.built(self.OPEN + [
            (145, 'Notice of Foreclosure Sale on 07/20/2024', '07/01/2024', ''),
            (150, 'Certificate of Title', '08/15/2024', ''),
            (160, 'Order Resetting Foreclosure Sale on 09/10/2026', '07/01/2026', ''),
            (170, 'Bid Amount', '09/10/2026', ''),
            (171, 'Mortgage Foreclosure Deposit', '09/10/2026', ''),
            (180, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '09/15/2026', '')])
        self.assertEqual((t['sale_held'] or {}).get('date'), '2026-09-10')
        self.assertIsNone((t['sale_held'] or {}).get('certificate'))
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'conflicted', (r['missing'], r['notes']))
        self.assertFalse(any('150' in m and 'certificate label' in m for m in r['missing']),
                         r['missing'])

    def test_an_undated_money_row_is_not_a_held_sale(self):
        # sale_held requires a date at or before the run's as_of (:544). Without that filter an
        # undated Bid Amount produced a gap that does not exist, with an inaccurate reason.
        t = self.built(self.OPEN + [(146, 'Bid Amount', '', '')])
        self.assertIsNone(t['sale_held'])
        r = CV.assess(t)
        self.assertFalse(any('sale-day bid or deposit label' in m for m in r['missing']),
                         r['missing'])



class EleventhReviewTests(unittest.TestCase):
    """Ten rounds concentrated on the calendar-eventType relabel. The eleventh found two false
    `supported` paths with nothing to do with it: two fields the producer writes and, until this
    commit, nothing in the repo read. Both drive the producer with real document pages.
    """
    @staticmethod
    def built(rows, as_of='2026-09-23', pages=None, controlling='2', amount=1746032.70):
        import miami_case_timeline as T
        docs = [{'source_ref': str(n), 'document_hash': 'h%s' % n,
                 'manifest': {'sha256': 'h%s' % n},
                 'reading': {'pages': [{'page': 1, 'outcome': 'text', 'text': text}]}}
                for n, text in (pages or {}).items()]
        t = T.build_timeline('SYNTHETIC', {'entries': [
            {'source_id': str(n), 'source_ref': str(n),
             'expected_documents': 1 if str(n) in (pages or {}) else 0,
             'metadata': {'eventID': n, 'eventDate': d, 'docketDescrition': text,
                          'comments': comments, 'eventType': ev}}
            for n, text, comments, d, ev in rows], 'pagination_verified': True}, docs, as_of)
        t['judgments'] = dict(t['judgments'] or {}, controlling_entry=controlling,
                              docket_duplicates_inferred=[])
        if not (t['judgments'].get('judgments') or []):
            t['judgments']['judgments'] = [judgment_row(controlling)]
        t['amount_vision'] = {'amount_checks': [ok_check(controlling, 'court:%s:1' % controlling,
                                                        amount)]}
        t['coverage'] = {'attachments': [read_attachment(controlling)], 'complete': False}
        return t

    OPEN = [(1, 'Complaint', '', '01/05/2026', ''),
            (2, 'Final Judgment of Foreclosure', '', '06/10/2026', '')]
    JUDGMENT_PAGE = 'FINAL JUDGMENT OF FORECLOSURE\nTotal $1,746,032.70'

    def test_a_dispositive_document_filed_under_a_covering_title_is_named(self):
        # _FILED_ABOUT_RE matches a bare `notice\b` (:274), so "Notice of Filing Satisfaction of
        # Judgment" moves the real label to attached_document_kind and leaves kind as the cover. The
        # producer's rule is right - a judgment on page 1 of a motion is an exhibit - but nothing read
        # the field, so the status stayed judgment_entered, the judgment stayed operative, and the
        # report vouched for the amount "to the cent" on a judgment the document says is paid.
        for title, body, attached in (
                ('Notice of Filing Satisfaction of Judgment',
                 'SATISFACTION OF FINAL JUDGMENT\nThe final judgment is fully satisfied and paid.',
                 'satisfaction'),
                ('Notice of Filing Order Vacating Final Judgment',
                 'ORDER VACATING FINAL JUDGMENT', 'vacatur'),
                ('Notice of Filing Certificate of Title', 'CERTIFICATE OF TITLE',
                 'certificate_of_title'),
                ('Notice of Filing Order of Dismissal', 'ORDER OF DISMISSAL',
                 'order_of_dismissal')):
            t = self.built(self.OPEN + [(5, title, '', '09/10/2026', '')],
                           pages={'2': self.JUDGMENT_PAGE, '5': body})
            entry = next(e for e in t['entries'] if e['entry_id'] == '5')
            self.assertEqual(entry.get('attached_document_kind'), attached, title)
            r = CV.assess(t)
            self.assertNotEqual(r['verdict'], 'supported', (title, r['supported_by'], r['notes']))
            self.assertTrue(any('5' in m and attached in m for m in r['missing']),
                            (title, r['missing']))

    def test_a_satisfaction_the_reconciliation_could_not_link_is_named(self):
        # reconcile_judgments keeps its own list of dispositive events it could not link to a
        # judgment (:756). _target returns none whenever the satisfaction cites a date that is not the
        # judgment's - the ordinary shape, since a satisfaction cites the mortgage's recording date -
        # so the judgment stayed operative / no_satisfaction_found and the list reached no reader.
        t = self.built(self.OPEN + [
            (7, 'Satisfaction of Final Judgment',
             'Satisfaction of the judgment on the mortgage recorded 03/14/2019 in OR Book 31234 '
             'Page 512', '09/12/2026', '')],
            pages={'2': self.JUDGMENT_PAGE,
                   '7': 'SATISFACTION OF FINAL JUDGMENT\nThe mortgage recorded 03/14/2019 is '
                        'satisfied in full.'})
        unmatched = (t['judgments'] or {}).get('unmatched') or []
        self.assertTrue(any(u.get('entry_id') == '7' for u in unmatched), unmatched)
        r = CV.assess(t)
        self.assertNotEqual(r['verdict'], 'supported', (r['supported_by'], r['notes']))
        self.assertTrue(any('7' in m and 'could not link' in m for m in r['missing']), r['missing'])

    def test_an_ordinary_document_on_a_calendar_event_does_not_hold_the_case(self):
        # The relabel sweep's document branch filtered on nothing, so ANY read document on a calendar
        # event held the case for good - a notice of appearance, an answer, even an order SETTING a
        # hearing, where the relabel was a no-op. The lost label is recoverable: :360 sets
        # operative_text to the very title line the producer classified, so its own parser gives it
        # back, and only a posture-deciding label is a gap.
        for body in ('NOTICE OF APPEARANCE', 'ORDER SETTING HEARING', 'MOTION TO COMPEL',
                     'ANSWER AND AFFIRMATIVE DEFENSES'):
            t = self.built(self.OPEN + [(9, 'Order', '', '07/01/2026', 'Hearing')],
                           pages={'2': self.JUDGMENT_PAGE, '9': body})
            entry = next(e for e in t['entries'] if e['entry_id'] == '9')
            self.assertEqual((entry['kind'], entry['kind_source']), ('hearing', 'document'), body)
            r = CV.assess(t)
            self.assertEqual(r['verdict'], 'supported', (body, r['missing'], r['conflicts']))

    def test_a_deciding_document_on_a_calendar_event_still_holds_the_case(self):
        # The same branch must keep firing where the lost label DOES decide something.
        t = self.built(self.OPEN + [(9, 'Order', '', '07/01/2026', 'Hearing')],
                       pages={'2': self.JUDGMENT_PAGE,
                              '9': 'ORDER VACATING FINAL JUDGMENT'})
        r = CV.assess(t)
        self.assertNotEqual(r['verdict'], 'supported', (r['supported_by'], r['notes']))
        self.assertTrue(any('9' in m and 'vacatur' in m for m in r['missing']), r['missing'])

    def test_a_stay_ending_label_lost_to_the_relabel_is_a_deciding_label(self):
        # DECIDING_KINDS listed relief_from_stay but not the two other ways a stay ends, both of which
        # the producer writes into stay_history (:462).
        for kind in ('bankruptcy_dismissed', 'bankruptcy_discharged'):
            self.assertIn(kind, CV.DECIDING_KINDS, kind)

    def test_a_certificate_after_the_cutoff_is_not_filtered_out(self):
        # The producer's certificate scan has no upper bound (:550); only its money-row scan does
        # (:544). Mirroring a bound the producer does not have drops evidence.
        t = self.built(self.OPEN + [
            (4, 'Bid Amount', '', '09/10/2026', ''),
            (5, 'Certificate of Title', '', '09/30/2026', ''),
            (6, 'Mortgage Foreclosure Deposit', '', '09/30/2026', '')], as_of='2026-09-23',
            pages={'2': self.JUDGMENT_PAGE})
        # The certificate is dated after the cutoff and must still be seen, because the producer sees
        # it; the money row dated after the cutoff must not be, because the producer does not.
        self.assertEqual([e['entry_id'] for e in CV._labelled(t, CV.CERTIFICATE_KINDS,
                                                              since='2026-09-10')], ['5'])
        self.assertEqual([e['entry_id'] for e in CV._labelled(t, CV.SALE_MONEY_KINDS)], ['4'])



class TwelfthReviewTests(unittest.TestCase):
    """The twelfth review caught a REGRESSION this branch introduced one commit earlier, plus two
    false `supported` paths on axes eleven rounds had not touched - the amount attribution and a
    third unread producer field - and a new gap of my own that would have held routine dockets.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    OPEN = EleventhReviewTests.OPEN
    JUDGMENT_PAGE = EleventhReviewTests.JUDGMENT_PAGE

    def test_the_docket_index_never_outranks_a_read_document(self):
        # THE REGRESSION. _relabelled was narrowed to hold only when the lost DOCUMENT label decides
        # something, but _producer_labels still fell back to index_kind - so where the document's
        # label is harmless and the index's is deciding, nothing held the case and the weaker label
        # was used. An order DENYING a motion to cancel a sale, indexed "Order Cancelling Foreclosure
        # Sale", closed a live sale under a Chapter 13 stay: `supported`, amount vouched to the cent.
        t = self.built(self.OPEN + [
            (3, 'Notice of Foreclosure Sale', 'sale set for 09/28/2026', '07/01/2026', ''),
            (4, 'Suggestion of Bankruptcy', 'Chapter 13 case no. 26-11111', '07/15/2026', ''),
            (5, 'Order Cancelling Foreclosure Sale', '', '08/01/2026', 'Hearing')],
            pages={'2': self.JUDGMENT_PAGE,
                   '5': 'ORDER DENYING MOTION TO CANCEL FORECLOSURE SALE'})
        entry = next(e for e in t['entries'] if e['entry_id'] == '5')
        self.assertEqual((entry['kind'], entry['kind_source'], entry['index_kind']),
                         ('hearing', 'document', 'order_cancelling_sale'))
        self.assertEqual(CV._producer_labels(entry), ('order_on_motion',))
        self.assertIs(t['stay_in_effect'], True)
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'conflicted', (r['missing'], r['notes']))
        self.assertTrue(any('3' in c for c in r['conflicts']), r['conflicts'])

    def test_a_reason_never_calls_a_denied_motion_a_notice_of_sale(self):
        # The mirror: index 'Notice of Foreclosure Sale' over a document that is an order denying an
        # unrelated motion printed "which the docket classifies as notice_of_sale", which the
        # producer contradicts - it classified the entry off the document.
        t = self.built(self.OPEN + [
            (4, 'Suggestion of Bankruptcy', 'Chapter 13 case no. 26-11111', '07/15/2026', ''),
            (5, 'Notice of Foreclosure Sale', '', '08/01/2026', 'Hearing')],
            pages={'2': self.JUDGMENT_PAGE,
                   '5': 'ORDER DENYING MOTION FOR PROTECTIVE ORDER'})
        r = CV.assess(t)
        self.assertFalse(any('notice_of_sale' in c for c in r['conflicts']), r['conflicts'])

    def test_the_amount_names_the_document_it_was_read_off(self):
        # A check's entry_id comes from the MIDDLE segment of court:<entry>:<document>
        # (run_case_timeline :53), so an Affidavit of Indebtedness filed as a second attachment on
        # the judgment entry carries the judgment's entry_id and its own total.
        #
        # The twelfth round answered that with a per-case gap on any entry with two read documents.
        # That was wrong twice over: a judgment filed with its legal-description exhibit is the
        # ORDINARY shape, so it held routine dockets for good and flipped a pilot case, and the
        # reason it printed ("a check names the entry rather than the document") was refuted by the
        # source_ref in the same sentence. The ambiguity is uniform across every multi-document
        # entry and nothing saved resolves it, so it belongs in the report's standing qualification -
        # which every report carries - and the line names the copy the figure verified on.
        t = self.built(self.OPEN, pages={'2': self.JUDGMENT_PAGE})
        t['amount_vision'] = {'amount_checks': [ok_check('2', 'court:2:2', 412880.00)]}
        t['coverage'] = {'attachments': [read_attachment('2', 'court:2:1'),
                                         read_attachment('2', 'court:2:2')],
                         'complete': False}
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))
        self.assertTrue(any('court:2:2' in line for line in r['supported_by']), r['supported_by'])
        self.assertIn('which attachment on a docket entry IS the judgment', CV.QUALIFICATION)

    def test_one_read_document_on_the_entry_leaves_the_amount_alone(self):
        # The ordinary case must not gap: one document, no ambiguity about what the figure came off.
        t = self.built(self.OPEN, pages={'2': self.JUDGMENT_PAGE})
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))

    def test_a_dismissal_the_run_read_as_limited_is_named(self):
        # limited_scope is written on every entry (:367) and _transition declines to move the status
        # for a limited-scope dismissal (:234). A limited-scope SATISFACTION is caught, because
        # reconcile_judgments records partially_satisfied; a dismissal has no such backstop, so a
        # whole action voluntarily dismissed read `supported` with status judgment_entered.
        for title, body in (
                ('Notice of Voluntary Dismissal',
                 'NOTICE OF VOLUNTARY DISMISSAL\nPlaintiff hereby dismisses this action, reserving '
                 'only its right to refile.'),
                ('Order of Dismissal',
                 'ORDER OF DISMISSAL\nThis cause is dismissed as to Defendants JOHN SMITH and '
                 'UNKNOWN TENANT #1 only.')):
            t = self.built(self.OPEN + [(6, title, '', '08/01/2026', '')],
                           pages={'2': self.JUDGMENT_PAGE, '6': body})
            entry = next(e for e in t['entries'] if e['entry_id'] == '6')
            self.assertIs(entry['limited_scope'], True, title)
            r = CV.assess(t)
            self.assertNotEqual(r['verdict'], 'supported', (title, r['supported_by'], r['notes']))
            self.assertTrue(any('6' in m and 'dismissal' in m for m in r['missing']),
                            (title, r['missing']))

    def test_a_judgment_copy_under_a_covering_title_does_not_hold_the_case(self):
        # final_judgment is in _DISPOSITIVE_BODIES, so a motion, a proposed judgment, a memorandum
        # and a status report carrying a judgment copy all set attached_document_kind - and the
        # eleventh round's reader held every one of them forever. The producer's own rule is that
        # such a copy is an exhibit; it can only mean "a judgment exists", which is already read.
        for title in ('Motion for Summary Judgment', 'Notice of Filing Proposed Final Judgment',
                      'Request for Judicial Notice', 'Memorandum of Law', 'Status Report'):
            t = self.built([(1, 'Complaint', '', '01/05/2026', ''),
                            (3, title, '', '03/01/2026', ''),
                            (2, 'Final Judgment of Foreclosure', '', '06/10/2026', '')],
                           pages={'2': self.JUDGMENT_PAGE,
                                  '3': 'FINAL JUDGMENT OF FORECLOSURE\nIT IS ADJUDGED that '
                                       'plaintiff recover $1,746,032.70'})
            entry = next(e for e in t['entries'] if e['entry_id'] == '3')
            self.assertEqual(entry.get('attached_document_kind'), 'final_judgment', title)
            r = CV.assess(t)
            self.assertEqual(r['verdict'], 'supported', (title, r['missing'], r['conflicts']))

    def test_a_document_the_run_never_reached_is_not_no_document(self):
        # document_coverage :20 defines not_enumerated as the attachment list itself not being
        # obtained, and :169 emits it saying how many documents the docket claims. Printing "has no
        # document to read" told the reader the opposite of what the producer saved.
        self.assertNotIn('not_enumerated', CV.NO_IMAGE)
        self.assertIn('not_enumerated', CV.NOT_REACHED)
        r = CV.assess(timeline('X', checks=[ok_check()],
                               attachments=[dict(read_attachment(), state='not_enumerated')]))
        self.assertFalse(any('no document to read' in m for m in r['missing']), r['missing'])
        self.assertTrue(any('never reached' in m for m in r['missing']), r['missing'])



class ThirteenthReviewTests(unittest.TestCase):
    """The thirteenth review found the two failures the brief asked it to weigh against each other:
    a false `supported` over a live stay, and one of my own gap checks holding ordinary dockets. It
    also found the inverse of this suite's standing lesson - a fixture MISSING a key the producer
    always writes, which let a bad check pass all 134 tests.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    JUDGMENT_PAGE = EleventhReviewTests.JUDGMENT_PAGE

    def test_an_entry_after_the_cutoff_decides_nothing(self):
        # The producer keeps post-as_of entries in `entries` and skips them everywhere it decides
        # anything (:442, reconcile_judgments :671, sale_held :544). _sale_state's scan did not, so on
        # an acceptance replay with a cutoff behind the collection date a certificate dated AFTER the
        # cutoff closed a sale that was live at it: `supported`, amount vouched to the cent.
        docket = [(1, 'Complaint', '', '01/05/2025', ''),
                  (2, 'Final Judgment of Foreclosure', '', '04/10/2025', ''),
                  (3, 'Notice of Foreclosure Sale on 12/10/2025', '', '05/01/2025', ''),
                  (4, 'Suggestion of Bankruptcy Chapter 13 case 25-11111', '', '06/01/2025', '')]
        before = self.built(docket, as_of='2025-12-31', pages={'2': self.JUDGMENT_PAGE})
        self.assertEqual(CV.assess(before)['verdict'], 'conflicted')
        after = self.built(docket + [(120, 'Certificate of Title', '', '03/01/2026', '')],
                           as_of='2025-12-31', pages={'2': self.JUDGMENT_PAGE})
        self.assertEqual(next(e['entry_id'] for e in after['entries'] if e['entry_id'] == '120'),
                         '120', 'the producer keeps the later entry in the list')
        r = CV.assess(after)
        self.assertEqual(r['verdict'], 'conflicted', (r['missing'], r['notes']))

    def test_a_sale_noticed_after_the_cutoff_is_not_a_sale_at_it(self):
        # The mirror: a later notice must not make a docket with no sale at the cutoff conflicted.
        t = self.built([(1, 'Complaint', '', '01/05/2025', ''),
                        (2, 'Final Judgment of Foreclosure', '', '04/10/2025', ''),
                        (4, 'Suggestion of Bankruptcy Chapter 13 case 25-11111', '', '06/01/2025', ''),
                        (5, 'Notice of Foreclosure Sale on 06/01/2026', '', '03/01/2026', '')],
                       as_of='2025-12-31', pages={'2': self.JUDGMENT_PAGE})
        r = CV.assess(t)
        self.assertEqual(r['conflicts'], [], r['conflicts'])

    def test_a_judgment_filed_with_its_exhibit_still_reads_supported(self):
        # The over-broad check the last round added: a final judgment plus its legal-description
        # exhibit, both read, one verified total, is the ORDINARY shape - document_collectors :374
        # builds court:<entry>:<documentID> per attachment - and every such entry read incomplete for
        # good. The status table's 2023-020247 verifies "on both copies", so it flipped a pilot too.
        t = self.built([(1, 'Complaint', '', '01/05/2026', ''),
                        (2, 'Final Judgment of Foreclosure', '', '06/10/2026', '')],
                       pages={'2': self.JUDGMENT_PAGE})
        t['coverage'] = {'attachments': [read_attachment('2', 'court:2:1'),
                                         read_attachment('2', 'court:2:2')], 'complete': False}
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))

    def test_the_coverage_fixture_carries_the_key_the_producer_always_writes(self):
        # The inverse of this suite's standing lesson. document_coverage :166 always sets `document`
        # on a row built from a fetched attachment; read_attachment omitted it, so a check reading
        # that key could never fire in any test and passed the whole suite while being wrong on real
        # evidence. Assert the fixture against the producer, not against itself.
        import document_coverage as COV
        import inspect
        src = inspect.getsource(COV.coverage)
        self.assertIn("document=str(row.get('source_ref'))", src,
                      'document_coverage no longer names the row key this fixture mirrors')
        self.assertTrue(str(read_attachment('9')['document']).startswith('court:9:'),
                        read_attachment('9'))

    def test_a_held_sale_a_certificate_closes_is_not_reported_as_open(self):
        # assess's held-sale gap printed "whether a sale was held is not settled" with no regard for
        # a certificate closing the day, while the sibling branch in _sale_state checks exactly that.
        t = self.built([(1, 'Complaint', '', '01/05/2026', ''),
                        (2, 'Final Judgment of Foreclosure', '', '06/10/2026', ''),
                        (4, 'Bid Amount', '', '07/01/2026', 'Hearing'),
                        (5, 'Certificate of Title', '', '07/10/2026', '')],
                       pages={'2': self.JUDGMENT_PAGE})
        self.assertIsNone(t['sale_held'])
        r = CV.assess(t)
        self.assertFalse(any('whether a sale was held' in m for m in r['missing']), r['missing'])

    def test_a_timeline_that_parses_to_a_non_object_is_unreadable(self):
        # Parsing is not the only way a file can be wrong. `null` parses fine and is not a timeline;
        # it was filed under "no whole-case timeline saved" - the bucket _load exists to keep corrupt
        # files out of, where it is indistinguishable from a case nobody has run.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text('null')
            proc = run_cli('--dossiers', folder, dealflow=folder)
            self.assertIn('UNREADABLE', proc.stdout)
            report = json.loads((folder / 'case-verdicts.json').read_text())
            self.assertEqual(report['no_verdict']['no_timeline_saved'], [], report['no_verdict'])



class FourteenthReviewTests(unittest.TestCase):
    """The fourteenth review found a regression of mine: the as_of bound the thirteenth round added to
    _sale_state also dropped UNDATED entries, which silently emptied the unlabelled-sale gap. It also
    found the held-sale money rows closed by anything sale-shaped rather than by a certificate.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    JUDGMENT_PAGE = EleventhReviewTests.JUDGMENT_PAGE

    def test_an_undated_sale_notice_under_a_live_stay_is_never_supported(self):
        # The blocker. miami_case_timeline's classifier does not label "Notice of Rescheduled
        # Foreclosure Sale", so the only thing standing between this docket and `supported` is the
        # unlabelled-sale gap - and a cutoff filter that drops undated entries deletes exactly that.
        # An entry with no date is more unknown, not less.
        t = self.built([(1, 'Complaint', '', '01/05/2026', ''),
                        (2, 'Final Judgment of Foreclosure', '', '06/10/2026', ''),
                        (4, 'Suggestion of Bankruptcy Chapter 13 case 26-11111', '', '07/01/2026', ''),
                        (9, 'Notice of Rescheduled Foreclosure Sale', 'sale set for 11/02/2026',
                         '', '')],
                       pages={'2': self.JUDGMENT_PAGE})
        undated = [e for e in t['entries'] if e['entry_id'] == '9']
        self.assertEqual([e.get('date') for e in undated], [None],
                         'the fixture must carry the undated entry the producer keeps')
        r = CV.assess(t)
        self.assertNotEqual(r['verdict'], 'supported', (r['missing'], r['conflicts'], r['notes']))
        self.assertTrue(any('sale' in m.lower() for m in r['missing'] + r['conflicts']),
                        (r['missing'], r['conflicts']))

    def test_a_dated_entry_after_the_cutoff_is_still_dropped(self):
        # The bound itself is right for DATED entries and the thirteenth round's case must stay fixed.
        docket = [(1, 'Complaint', '', '01/05/2025', ''),
                  (2, 'Final Judgment of Foreclosure', '', '04/10/2025', ''),
                  (3, 'Notice of Foreclosure Sale on 12/10/2025', '', '05/01/2025', ''),
                  (4, 'Suggestion of Bankruptcy Chapter 13 case 25-11111', '', '06/01/2025', ''),
                  (120, 'Certificate of Title', '', '03/01/2026', '')]
        r = CV.assess(self.built(docket, as_of='2025-12-31', pages={'2': self.JUDGMENT_PAGE}))
        self.assertEqual(r['verdict'], 'conflicted', (r['missing'], r['notes']))

    def test_only_a_certificate_closes_the_clerks_money_rows(self):
        # The held-sale gap read `closing_for_money` off SALE_CLOSING_KINDS while its own reason says
        # a certificate. An order CANCELLING a sale dated after money changed hands does not settle
        # what happened at it; saying a certificate closed the day when none exists is the same
        # false-agreement failure in the reason text rather than the verdict.
        t = self.built([(1, 'Complaint', '', '01/05/2026', ''),
                        (2, 'Final Judgment of Foreclosure', '', '06/10/2026', ''),
                        (4, 'Bid Amount', '', '07/01/2026', 'Hearing'),
                        (5, 'Order Cancelling Foreclosure Sale', '', '07/10/2026', '')],
                       pages={'2': self.JUDGMENT_PAGE})
        r = CV.assess(t)
        self.assertTrue(any('whether a sale was held' in m for m in r['missing']), r['missing'])

    def test_two_read_documents_on_the_judgment_entry_are_named_in_the_notes(self):
        # Nothing saved says WHICH attachment on an entry is the judgment, so a verified figure may be
        # a sibling document's total. Blocking on it flipped a pilot (thirteenth review), so it is a
        # per-case note - the standing qualification cannot say which cases it bites on.
        t = self.built([(1, 'Complaint', '', '01/05/2026', ''),
                        (2, 'Final Judgment of Foreclosure', '', '06/10/2026', '')],
                       pages={'2': self.JUDGMENT_PAGE})
        t['coverage'] = {'attachments': [read_attachment('2', 'court:2:1'),
                                         read_attachment('2', 'court:2:2')], 'complete': False}
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))
        note = [n for n in r['notes'] if 'read documents' in n]
        self.assertTrue(note, r['notes'])
        self.assertIn('court:2:2', note[0])

    def test_one_read_document_on_the_judgment_entry_says_nothing(self):
        t = self.built([(1, 'Complaint', '', '01/05/2026', ''),
                        (2, 'Final Judgment of Foreclosure', '', '06/10/2026', '')],
                       pages={'2': self.JUDGMENT_PAGE})
        r = CV.assess(t)
        self.assertFalse([n for n in r['notes'] if 'read documents' in n], r['notes'])

    def test_an_unreadable_timeline_file_is_not_reported_as_a_parsed_shape(self):
        # `null` and a file that will not parse reach the same branch; reporting the second as
        # "parsed to NoneType" describes a parse that never happened.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'A.json').write_text(json.dumps({'case': 'A', 'open_gaps': []}))
            (folder / 'A-timeline.json').write_text('{"case": "A", ')
            proc = run_cli('--dossiers', folder, dealflow=folder)
            self.assertIn('UNREADABLE', proc.stdout)
            self.assertNotIn('NoneType', proc.stdout)



class FifteenthReviewTests(unittest.TestCase):
    """Two false `supported` paths, both where a producer bound this module had already noticed on the
    gap-RAISING side was still missing on the gap-SUPPRESSING side, or where the producer's own net for
    undated entries has a hole this module did not cover.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    JUDGMENT_PAGE = EleventhReviewTests.JUDGMENT_PAGE

    HELD = [(1, 'Complaint', '', '2026-01-05', ''),
            (140, 'Final Judgment of Foreclosure', '', '06/10/2026', ''),
            (170, 'Bid Amount', '', '08/10/2026', ''),
            (171, 'Mortgage Foreclosure Deposit', '', '08/10/2026', '')]

    def held(self, extra=(), as_of='2026-09-01'):
        return self.built(list(self.HELD) + list(extra), as_of=as_of, controlling='140',
                          pages={'140': self.JUDGMENT_PAGE})

    def test_a_certificate_after_the_cutoff_does_not_close_a_sale_held_before_it(self):
        # sale_held bounds its money-row scan by as_of (:544) and its certificate scan (:550) does
        # NOT, so a certificate dated after the run's own cutoff sets sale_held['certificate'].
        # Reading that bare field as "the sale closed" suppressed the held-sale gap entirely.
        t = self.held([(300, 'Certificate of Title', '', '10/05/2026', '')])
        self.assertEqual((t['sale_held'] or {}).get('certificate'), '300',
                         'the producer must still record the later certificate')
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))
        line = [m for m in r['missing'] if 'sale was held' in m]
        self.assertTrue(line, r['missing'])
        self.assertIn('300', line[0])
        self.assertIn('as_of', line[0])
        self.assertFalse(any('did not take in' in m for m in r['missing']),
                         'the summary DID take that certificate in')

    def test_the_same_sale_under_a_live_stay_conflicts(self):
        # The worst variant: the stay-against-sale contradiction was downgraded all the way to
        # `supported` because _sale_state read the same bare certificate field.
        t = self.held([(200, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '', '08/20/2026', ''),
                       (300, 'Certificate of Title', '', '10/05/2026', '')])
        self.assertTrue(t['stay_in_effect'])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'conflicted', (r['conflicts'], r['missing']))
        self.assertTrue(any('08-10' in c or '2026-08-10' in c for c in r['conflicts']), r['conflicts'])

    def test_a_certificate_at_the_cutoff_still_closes_the_sale(self):
        # The mirror. A certificate dated within the window closes the day as it always did.
        t = self.held([(300, 'Certificate of Title', '', '08/20/2026', '')])
        r = CV.assess(t)
        self.assertFalse(any('sale was held' in m for m in r['missing']), r['missing'])

    def test_an_undated_money_row_over_a_read_judgment_is_not_supported(self):
        # _transition has no entry for sale_bid or sale_deposit (:250), so build_timeline's undated
        # net (:505) does not cover them, and _labelled drops undated rows because every producer
        # summary it mirrors does. Nothing was left to raise this.
        t = self.built([(1, 'Complaint', '', '01/05/2026', ''),
                        (140, 'Final Judgment of Foreclosure', '', '06/10/2026', ''),
                        (146, 'Bid Amount', '', '', '')],
                       controlling='140', pages={'140': self.JUDGMENT_PAGE})
        self.assertEqual(t['status']['kind'], 'judgment_entered')
        self.assertIsNone(t['sale_held'])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['notes']))
        line = [m for m in r['missing'] if 'could not date it' in m]
        self.assertTrue(line, r['missing'])
        self.assertIn('146', line[0])

    def test_an_undated_limited_scope_satisfaction_is_not_supported(self):
        # _transition returns None for a limited-scope satisfaction (:248) and reconcile_judgments
        # skips undated entries (:673), so the judgment stayed operative / no_satisfaction_found and
        # the status never moved: `supported` over a partial satisfaction of the controlling judgment.
        t = self.built([(1, 'Complaint', '', '01/05/2026', ''),
                        (140, 'Final Judgment of Foreclosure', '', '06/10/2026', ''),
                        (150, 'Partial Satisfaction of Judgment as to Defendant JOHN DOE', '', '', '')],
                       controlling='140', pages={'140': self.JUDGMENT_PAGE})
        row = next(e for e in t['entries'] if e['entry_id'] == '150')
        self.assertEqual((row['kind'], row['limited_scope'], row['date']), ('satisfaction', True, None))
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['notes']))
        self.assertTrue([m for m in r['missing'] if 'could not date it' in m and '150' in m],
                        r['missing'])

    def test_a_dated_ordinary_docket_is_untouched_by_the_undated_sweep(self):
        # Contract 5: the sweep must add nothing to a docket whose entries all carry dates.
        t = self.built([(1, 'Complaint', '', '01/05/2026', ''),
                        (140, 'Final Judgment of Foreclosure', '', '06/10/2026', '')],
                       controlling='140', pages={'140': self.JUDGMENT_PAGE})
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))



class SixteenthReviewTests(unittest.TestCase):
    """_sale_state answered the most decision-relevant question on the page - is a sale still running -
    and `assess` consulted it only when a bankruptcy stay happened to be in effect. On every other
    docket the answer was computed and thrown away.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    JUDGMENT_PAGE = EleventhReviewTests.JUDGMENT_PAGE
    NOTICED = [(1, 'Complaint', '', '01/05/2026', ''),
               (145, 'Notice of Foreclosure Sale on 04/01/2026', '', '03/01/2026', ''),
               (150, 'Final Judgment of Foreclosure', '', '05/01/2026', '')]

    def case(self, rows):
        return self.built(rows, controlling='150', pages={'150': self.JUDGMENT_PAGE})

    def test_a_live_sale_with_no_stay_is_not_supported(self):
        # The producer's status loop takes the LATEST transition, so a judgment entered after the sale
        # notice leaves kind 'judgment_entered' - a SETTLED_KIND - and sale_outcome is written only
        # while the final status is 'sale_scheduled' with a parsed date (miami_case_timeline :508), so
        # nothing else in the module covered it. No clerk money rows either: the sale has not been held.
        t = self.case(self.NOTICED)
        self.assertEqual((t['status']['kind'], t['status'].get('sale_outcome'), t['sale_held']),
                         ('judgment_entered', None, None))
        self.assertEqual(CV._sale_state(t, t['status'], t['status']['kind'])[0], 'live')
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))
        self.assertTrue([m for m in r['missing'] if '145' in m and 'notice_of_sale' in m], r['missing'])

    def test_a_sale_the_classifier_could_not_label_is_named_without_a_stay(self):
        # The `unknown` half. With a Suggestion of Bankruptcy on the same docket this exact sentence
        # already reached `missing`; without one the identical evidence reached nobody.
        t = self.case([(1, 'Complaint', '', '01/05/2026', ''),
                       (145, 'Notice of Rescheduled Foreclosure Sale on 12/28/2026', '',
                        '03/01/2026', ''),
                       (150, 'Final Judgment of Foreclosure', '', '05/01/2026', '')])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))
        self.assertTrue([m for m in r['missing'] if 'whether a sale is pending' in m], r['missing'])

    def test_a_judgment_with_no_sale_entries_is_untouched(self):
        r = CV.assess(self.case([(1, 'Complaint', '', '01/05/2026', ''),
                                 (150, 'Final Judgment of Foreclosure', '', '05/01/2026', '')]))
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))

    def test_the_postures_that_end_a_case_are_not_held_by_an_older_sale_notice(self):
        # Contract 5, and the reason the gate is scoped to 'judgment_entered'. _sale_state reads a
        # newest notice against a newest cancellation or certificate and knows nothing of dismissals
        # or satisfactions, so it still calls all of these 'live'. The producer's status loop takes
        # the latest transition, so the thing that ended the case is newer than the sale entries.
        for title, kind in (('Order of Dismissal', 'dismissed'),
                            ('Certificate of Title', 'sold'),
                            ('Order Cancelling Foreclosure Sale', 'sale_cancelled')):
            t = self.case(self.NOTICED + [(160, title, '', '06/01/2026', '')])
            self.assertEqual(t['status']['kind'], kind, title)
            r = CV.assess(t)
            self.assertFalse([m for m in r['missing'] if 'the docket status is' in m],
                             (title, r['missing']))

    def test_a_live_sale_under_an_unknown_stay_state_is_named_too(self):
        # `elif stay is None and history` skipped _sale_state as well, so a live sale under an unknown
        # stay state said only "stay state unknown" and never named the sale. Already incomplete, so
        # this is a reporting loss rather than a false clean bill - and it is the same gate.
        t = self.case(self.NOTICED)
        t['stay_in_effect'] = None
        t['stay_history'] = [{'entry_id': '99', 'kind': 'suggestion_of_bankruptcy'}]
        r = CV.assess(t)
        self.assertIn('stay state unknown', r['missing'])
        self.assertTrue([m for m in r['missing'] if '145' in m], r['missing'])

    def test_a_live_sale_under_a_stay_is_still_one_contradiction(self):
        # The stay branch must not double-report: a live sale with a stay in effect is a conflict, not
        # a conflict plus a gap saying the same thing.
        t = self.case(self.NOTICED + [(160, 'Suggestion of Bankruptcy Chapter 13 case 26-12345', '',
                                       '06/01/2026', '')])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'conflicted', (r['conflicts'], r['missing']))
        self.assertFalse([m for m in r['missing'] if 'the docket status is' in m], r['missing'])



class SeventeenthReviewTests(unittest.TestCase):
    """Both halves of the gate the sixteenth round added were wrong against the producer, in two
    different ways - and the pilot fixture that should have caught the first was itself a shape
    build_timeline cannot write.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    JUDGMENT_PAGE = EleventhReviewTests.JUDGMENT_PAGE
    JUDGED = [(1, 'Complaint', '', '01/05/2026', ''),
              (140, 'Final Judgment of Foreclosure', '', '02/10/2026', '')]

    def case(self, extra):
        return self.built(self.JUDGED + list(extra), controlling='140',
                          pages={'140': self.JUDGMENT_PAGE})

    def test_a_noticed_sale_with_no_parseable_date_is_not_supported(self):
        # _transition takes the sale date from sale_passages - the docket line plus the body lines of
        # READ pages - and falls back to the entry's own date only for a calendar event (:264). A
        # notice of sale whose description carries no date and whose document is behind the county
        # login leaves sale_date None, so build_timeline's past-sale check (:508, written
        # `and status.get('sale_date') and ... < today`) never runs and no sale_outcome is saved.
        t = self.case([(145, 'Notice of Foreclosure Sale', '', '07/01/2026', '')])
        self.assertEqual((t['status']['kind'], t['status']['sale_date'],
                          t['status'].get('sale_outcome')), ('sale_scheduled', None, None))
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))
        self.assertTrue([m for m in r['missing'] if 'parsed no sale date' in m], r['missing'])

    def test_the_same_notice_with_a_date_is_the_case_that_already_worked(self):
        # The inversion that proves it was a defect and not a policy: the docket where LESS is known
        # was the one reading supported.
        t = self.case([(145, 'Notice of Foreclosure Sale on 03/02/2026', '', '02/20/2026', '')])
        self.assertEqual(t['status'].get('sale_outcome'), 'unknown_no_certificate')
        self.assertEqual(CV.assess(t)['verdict'], 'incomplete')

    def test_a_future_sale_date_on_the_calendar_is_still_supported(self):
        # Contract 5: a sale_scheduled status with a parsed date the cutoff has not passed is the
        # ordinary live-lead shape, and 2024-014878 in the acceptance table is exactly it.
        t = self.case([(145, 'Notice of Foreclosure Sale on 12/28/2026', '', '08/01/2026', '')])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))

    def test_the_sale_scheduled_fixture_carries_the_key_the_producer_always_writes(self):
        # The read_attachment lesson again: _transition always writes sale_date for a sale_scheduled
        # status, so a fixture omitting it could never reach the check above.
        self.assertIn('sale_date', timeline('X', kind='sale_scheduled')['status'])
        self.assertIsNone(timeline('X', kind='sale_scheduled')['status']['sale_date'])
        self.assertNotIn('sale_date', timeline('X')['status'])

    def test_an_unlabelled_sale_entry_after_a_cancellation_is_named(self):
        # The gate reported `unknown` only on judgment_entered, on the argument that a settled kind
        # means the settling entry is newer than the sale entries. That cannot reach the entries
        # `unknown` is built from: classify leaves "Notice of Rescheduled Foreclosure Sale" as
        # 'other' and _transition has no entry for 'other', so they produce no transition and are
        # invisible to the status loop. A cancellation order in between flipped identical evidence
        # from incomplete to supported.
        t = self.case([(145, 'Notice of Foreclosure Sale on 07/20/2026', '', '06/20/2026', ''),
                       (150, 'Order Cancelling Foreclosure Sale', '', '07/10/2026', ''),
                       (155, 'Notice of Rescheduled Foreclosure Sale on 12/28/2026', '',
                        '08/01/2026', '')])
        self.assertEqual(t['status']['kind'], 'sale_cancelled')
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))
        self.assertTrue([m for m in r['missing'] if '155' in m], r['missing'])

    def test_a_completed_sale_is_not_reopened_by_the_clerks_proceeds_entries(self):
        # Contract 5, and why `unknown` is not simply reported on every kind. "Disbursement of Sale
        # Proceeds" and "Surplus Funds from Sale" both carry the word and both classify as 'other',
        # so every completed sale would read incomplete for good. The producer's own certificate
        # label is what separates the two: a cancellation leaves room for a later notice, a
        # certificate does not.
        t = self.case([(145, 'Notice of Foreclosure Sale on 07/20/2026', '', '06/20/2026', ''),
                       (150, 'Certificate of Sale', '', '07/21/2026', ''),
                       (151, 'Certificate of Title', '', '08/05/2026', ''),
                       (160, 'Disbursement of Sale Proceeds', '', '08/10/2026', ''),
                       (161, 'Surplus Funds from Sale', '', '08/12/2026', '')])
        self.assertEqual(t['status']['kind'], 'sold')
        self.assertEqual(CV._sale_state(t, t['status'], 'sold'), ('none', None))
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))



class EighteenthReviewTests(unittest.TestCase):
    """Both holes were in the gate the round before added, on the one posture that gate could not
    reach: `unknown` was reported on every kind, but it was never COMPUTED for a live sale.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    JUDGMENT_PAGE = EleventhReviewTests.JUDGMENT_PAGE
    JUDGED = [(1, 'Complaint', '', '01/05/2026', ''),
              (140, 'Final Judgment of Foreclosure', '', '02/10/2026', '')]
    NOTICE = (145, 'Notice of Foreclosure Sale on 12/28/2026', '', '06/20/2026', '')

    def case(self, extra):
        return self.built(self.JUDGED + list(extra), controlling='140',
                          pages={'140': self.JUDGMENT_PAGE})

    def test_an_unlabelled_cancellation_of_a_live_sale_is_named(self):
        # _sale_state returned 'live' off the bare status kind before any path that computes
        # 'unknown'. classify leaves "Notice of Cancellation of Foreclosure Sale" as 'other' and
        # _transition has no entry for 'other', so the entry produces no transition and the status is
        # no evidence about it - the same argument the previous round used for reporting `unknown`.
        t = self.case([self.NOTICE,
                       (151, 'Notice of Cancellation of Foreclosure Sale', '', '07/11/2026', '')])
        self.assertEqual(t['status']['kind'], 'sale_scheduled')
        self.assertEqual(CV._sale_state(t, t['status'], 'sale_scheduled')[0], 'unknown')
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))
        self.assertTrue([m for m in r['missing'] if '151' in m], r['missing'])

    def test_an_unlabelled_reschedule_of_a_live_sale_is_named(self):
        # The same hole with the sale moved rather than cancelled: the report vouched for a posture
        # whose sale date the docket's own later entry contradicts.
        t = self.case([self.NOTICE,
                       (155, 'Notice of Rescheduled Foreclosure Sale on 10/05/2026', '',
                        '08/01/2026', '')])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))

    def test_knowing_less_is_never_the_supported_case(self):
        # The inversion. With a LABELLED cancellation between the notice and the unlabelled
        # rescheduling the case was already incomplete; removing it - knowing strictly less - made the
        # same entry invisible.
        labelled = self.case([self.NOTICE, (150, 'Order Cancelling Foreclosure Sale', '',
                                            '07/10/2026', ''),
                              (155, 'Notice of Rescheduled Foreclosure Sale on 10/05/2026', '',
                               '08/01/2026', '')])
        bare = self.case([self.NOTICE, (155, 'Notice of Rescheduled Foreclosure Sale on 10/05/2026',
                                        '', '08/01/2026', '')])
        self.assertEqual(CV.assess(labelled)['verdict'], CV.assess(bare)['verdict'])

    def test_a_resale_noticed_after_a_certificate_is_still_not_a_clean_bill(self):
        # The previous round's `completed` exclusion discarded EVERY later unlabelled sale-worded
        # entry, which is wider than its own justification (the clerk's proceeds handling) and
        # reopened the shape the seventh review fixed for the phrasing classify does label.
        t = self.case([(145, 'Notice of Foreclosure Sale on 07/20/2026', '', '06/20/2026', ''),
                       (146, 'Bid Amount', '', '07/20/2026', ''),
                       (150, 'Certificate of Sale', '', '07/21/2026', ''),
                       (155, 'Notice of Rescheduled Foreclosure Sale for 12/28/2026', '',
                        '08/01/2026', '')])
        self.assertEqual(t['status']['kind'], 'sold')
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))
        self.assertTrue([m for m in r['missing'] if '155' in m], r['missing'])

    def test_the_clerks_proceeds_entries_still_close_a_completed_sale(self):
        # Contract 5, and what separates the two: the producer's own date parser. A proceeds entry
        # prints no sale date after the certificate; a rescheduled-sale notice does.
        t = self.case([(145, 'Notice of Foreclosure Sale on 07/20/2026', '', '06/20/2026', ''),
                       (146, 'Bid Amount', '', '07/20/2026', ''),
                       (150, 'Certificate of Sale', '', '07/21/2026', ''),
                       (151, 'Certificate of Title', '', '08/05/2026', ''),
                       (160, 'Disbursement of Sale Proceeds', '', '08/10/2026', ''),
                       (161, 'Surplus Funds from Sale', '', '08/12/2026', '')])
        self.assertEqual(CV._sale_state(t, t['status'], 'sold'), ('none', None))
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))

    def test_a_live_sale_with_nothing_unlabelled_after_it_is_still_supported(self):
        t = self.case([self.NOTICE])
        self.assertEqual(CV._sale_state(t, t['status'], 'sale_scheduled')[0], 'live')
        self.assertEqual(CV.assess(t)['verdict'], 'supported')



class NineteenthReviewTests(unittest.TestCase):
    """One false `incomplete` on the highest-value posture in the pipeline, and one false `supported`
    from feeding the producer's parser less than the producer gave it.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    JUDGMENT_PAGE = EleventhReviewTests.JUDGMENT_PAGE
    JUDGED = [(1, 'Complaint', '', '01/05/2026', ''),
              (140, 'Final Judgment of Foreclosure', '', '02/10/2026', '')]
    LIVE = (160, 'Notice of Foreclosure Sale on 12/28/2026', '', '08/01/2026', '')

    def case(self, extra, pages=None):
        pg = {'140': self.JUDGMENT_PAGE}
        pg.update(pages or {})
        return self.built(self.JUDGED + list(extra), controlling='140', pages=pg)

    def test_a_sale_worded_entry_older_than_the_notice_does_not_unsettle_it(self):
        # The live-sale gate asks whether an unlabelled entry might be the cancellation or the
        # rescheduling of the sale now on the calendar, so an entry dated BEFORE the notice that put
        # it there cannot be one. It was floored at closing_date, which is '' when nothing closes a
        # sale, so a routine docket line - classify leaves "Order Setting Foreclosure Sale" 'other' -
        # held the ordinary live-lead shape incomplete for good.
        t = self.case([(142, 'Order Setting Foreclosure Sale', '', '02/15/2026', ''), self.LIVE])
        self.assertEqual(t['status']['kind'], 'sale_scheduled')
        self.assertEqual(CV._sale_state(t, t['status'], 'sale_scheduled')[0], 'live')
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))

    def test_an_entry_between_a_cancellation_and_a_fresh_notice_does_not_unsettle_it(self):
        # The commoner shape: the floor was the CANCELLATION, so an entry the producer labelled a
        # notice of sale after it did not lift the floor past it.
        t = self.case([(145, 'Notice of Foreclosure Sale on 04/01/2026', '', '03/01/2026', ''),
                       (150, 'Order Cancelling Foreclosure Sale', '', '03/20/2026', ''),
                       (152, "Plaintiff's Bid at Sale", '', '04/10/2026', ''), self.LIVE])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))

    def test_an_unlabelled_entry_after_the_notice_is_still_caught(self):
        # The eighteenth round's fix must survive the new floor.
        t = self.case([self.LIVE,
                       (165, 'Notice of Cancellation of Foreclosure Sale', '', '08/20/2026', '')])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))
        self.assertTrue([m for m in r['missing'] if '165' in m], r['missing'])

    def test_a_resale_dated_only_inside_the_document_is_not_supported(self):
        # sale_passages is what build_timeline saved for the entry: the docket line plus every body
        # line of a READ page matching its sale vocabulary, and _transition takes the status's own
        # sale_date from it. _sale_dates_of read only the docket words, so the producer's parser got a
        # narrower input than the producer gave it and the completed-sale filter discarded a resale
        # noticed after the certificate. The field was saved by the producer and read by nothing.
        t = self.case([(145, 'Notice of Foreclosure Sale on 03/02/2026', '', '02/20/2026', ''),
                       (151, 'Certificate of Title', '', '04/15/2026', ''),
                       (160, 'Sale Package Filed by Plaintiff', '', '05/01/2026', '')],
                      pages={'160': 'SALE PACKAGE\nTHE CLERK SHALL SELL THE PROPERTY AT PUBLIC SALE '
                                    'ON JUNE 20, 2026 at 9am'})
        entry = next(e for e in t['entries'] if e['entry_id'] == '160')
        self.assertIn('THE CLERK SHALL SELL', ' '.join(entry['sale_passages']),
                      'the producer must have saved the document line in sale_passages')
        self.assertEqual(CV._sale_dates_of(entry), ['2026-06-20'])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))
        self.assertTrue([m for m in r['missing'] if '160' in m], r['missing'])

    def test_a_rescheduled_sale_with_no_parseable_date_is_not_supported(self):
        # The same inversion as the seventeenth round: the docket whose date nobody could parse - the
        # ordinary case for a notice whose document is behind the county login - was the one reading
        # `supported`. The producer's own reset vocabulary answers it without a date.
        t = self.case([(145, 'Notice of Foreclosure Sale on 03/02/2026', '', '02/20/2026', ''),
                       (151, 'Certificate of Title', '', '04/15/2026', ''),
                       (160, 'Notice of Rescheduled Foreclosure Sale', '', '05/01/2026', '')])
        self.assertEqual(CV._sale_dates_of(
            next(e for e in t['entries'] if e['entry_id'] == '160')), [])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['conflicts']))

    def test_the_clerks_proceeds_entries_still_close_a_completed_sale(self):
        # Contract 5 again: neither of the two discriminators may fire on proceeds handling.
        t = self.case([(145, 'Notice of Foreclosure Sale on 07/20/2026', '', '06/20/2026', ''),
                       (146, 'Bid Amount', '', '07/20/2026', ''),
                       (150, 'Certificate of Sale', '', '07/21/2026', ''),
                       (151, 'Certificate of Title', '', '08/05/2026', ''),
                       (160, 'Disbursement of Sale Proceeds', '', '08/10/2026', ''),
                       (161, 'Surplus Funds from Sale', '', '08/12/2026', '')])
        self.assertEqual(CV._sale_state(t, t['status'], 'sold'), ('none', None))
        self.assertEqual(CV.assess(t)['verdict'], 'supported')



class TwentiethReviewTests(unittest.TestCase):
    """A judgment superseded by one filed under a covering title, the report dropping the one field
    that says the foreclosure is over, and the live-sale gate still holding routine sale paperwork.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    JUDGED = [(1, 'Complaint', '', '01/05/2026', ''),
              (82, 'Final Judgment of Foreclosure', '', '02/10/2026', '')]
    PAGE = 'FINAL JUDGMENT OF FORECLOSURE\nTotal $105,000.00'
    AMENDED = 'AMENDED FINAL JUDGMENT OF FORECLOSURE\nTOTAL $225,000.00'
    NOTICE = (140, 'Notice of Foreclosure Sale set for 10/28/2026', '', '06/01/2026', '')

    def case(self, extra, pages=None):
        pg = {'82': self.PAGE}
        pg.update(pages or {})
        return self.built(self.JUDGED + list(extra), controlling='82', amount=105000.00, pages=pg)

    def test_an_amended_judgment_under_a_covering_title_is_named(self):
        # _FILED_ABOUT_RE's bare `notice\b` matches "Notice of Filing ...", so the producer moves the
        # real label to attached_document_kind and leaves kind 'notice_of_filing'. _transition has no
        # entry for that kind and reconcile_judgments keys on kind == 'final_judgment' (:672), so the
        # superseded judgment stays operative and controlling - and the verdict vouched for the OLD
        # figure to the cent with the amendment named nowhere on the page.
        t = self.case([(140, 'Notice of Filing Amended Final Judgment of Foreclosure', '',
                        '06/01/2026', '')], pages={'140': self.AMENDED})
        entry = next(e for e in t['entries'] if e['entry_id'] == '140')
        self.assertEqual((entry['kind'], entry['attached_document_kind']),
                         ('notice_of_filing', 'final_judgment'))
        self.assertEqual([(r['entry_id'], r['status']) for r in t['judgments']['judgments']],
                         [('82', 'operative')])
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'incomplete', (r['missing'], r['notes']))
        self.assertTrue([m for m in r['missing'] if '140' in m], r['missing'])

    def test_a_judgment_copy_filed_as_an_exhibit_still_reads_supported(self):
        # The twelfth review's calibration, which is why final_judgment is excluded at all: a judgment
        # body on page 1 of a motion, memorandum, status report or proposed order is an exhibit, and
        # holding those made routine dockets incomplete for good. None of these carries a _REPLACES
        # word, which is the producer's own test for a replacement.
        for title in ('Notice of Filing Proposed Final Judgment', 'Memorandum of Law',
                      'Status Report', 'Request for Judicial Notice', 'Motion for Summary Judgment',
                      'Affidavit of Indebtedness', 'Notice of Filing Final Judgment'):
            t = self.case([(140, title, '', '06/01/2026', '')], pages={'140': self.AMENDED})
            r = CV.assess(t)
            self.assertEqual(r['verdict'], 'supported', (title, r['missing']))

    def test_routine_sale_paperwork_after_the_notice_does_not_hold_the_docket(self):
        # The previous round floored these branches at the notice, but the routine paperwork of a
        # noticed sale is filed AFTER it - a statement of amounts due in the run-up, a bid at the sale
        # itself - so the floor never reached them and the ordinary live-lead shape stayed incomplete.
        # The branches ask whether an entry could BE the cancellation or the rescheduling, so they now
        # require the words that would say so.
        t = self.case([self.NOTICE,
                       (141, 'Statement of Amounts Due at Sale', '', '06/05/2026', ''),
                       (142, "Plaintiff's Bid at Sale", '', '06/06/2026', '')])
        self.assertEqual(t['status']['kind'], 'sale_scheduled')
        r = CV.assess(t)
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))

    def test_a_cancellation_or_rescheduling_after_the_notice_is_still_caught(self):
        # The seventeenth and eighteenth rounds' cases, which the new filter must not lose.
        for title in ('Notice of Cancellation of Foreclosure Sale',
                      'Notice of Rescheduled Foreclosure Sale on 12/28/2026'):
            r = CV.assess(self.case([self.NOTICE, (165, title, '', '06/20/2026', '')]))
            self.assertEqual(r['verdict'], 'incomplete', (title, r['missing']))
            self.assertTrue([m for m in r['missing'] if '165' in m], (title, r['missing']))

    def test_the_report_prints_the_posture_and_the_cutoff(self):
        # dismissed, sold and sale_cancelled are all SETTLED_KINDS, and the producer folds none of
        # them into the judgment row or the amount, so all three are `supported` under this module's
        # scope - and all three printed as a clean row with a judgment amount beside them and nothing
        # on the page saying the foreclosure was over. A `sold` case means a third party holds the
        # certificate of title. Same defect the stay column exists to fix.
        rows = []
        for extra, kind in (([(160, 'Order of Dismissal', '', '06/01/2026', '')], 'dismissed'),
                            ([(160, 'Notice of Foreclosure Sale on 04/01/2026', '', '03/01/2026', ''),
                              (161, 'Certificate of Title', '', '04/10/2026', '')], 'sold')):
            t = self.case(extra)
            self.assertEqual(t['status']['kind'], kind)
            row = CV.assess(t)
            self.assertEqual(row['verdict'], 'supported', (kind, row['missing']))
            rows.append(dict(row, case=kind))
        report = CV.render_markdown(rows)
        self.assertIn('| Case | Verdict | Posture |', report)
        for kind in ('dismissed', 'sold'):
            line = next(l for l in report.split('\n') if l.startswith('| %s |' % kind))
            self.assertIn('| %s |' % kind, line.split('supported')[1], line)
        self.assertIn('Evidence as of 2026-09-23.', report)



class TwentyFirstReviewTests(unittest.TestCase):
    """The UNREAD half of the covering-title defect. The twentieth round fixed it for a filing whose
    document the run opened; when nobody opened it the producer writes a COVER label instead of
    attached_document_kind, and that reached no check in this module at all.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    PAGE = EleventhReviewTests.JUDGMENT_PAGE
    JUDGED = [(1, 'Complaint', '', '01/05/2026', ''),
              (2, 'Final Judgment of Foreclosure', '', '06/10/2026', '')]

    def case(self, extra):
        return self.built(self.JUDGED + list(extra), controlling='2', pages={'2': self.PAGE})

    def test_a_dispositive_filing_under_an_unread_cover_is_named(self):
        # attached_document_kind exists only when the producer OPENED the document (:353). Otherwise
        # classify falls back to a cover label - notice_of_filing, certificate_of_service, affidavit -
        # which names the envelope, and _transition has no entry for any of them, reconcile_judgments
        # keys on kind == 'final_judgment' (:672), and none is in DECIDING_KINDS or was in
        # UNLABELLED_KINDS. So every one of these read `supported` with the amount vouched to the cent.
        for title in ('Notice of Filing Satisfaction of Judgment',
                      'Notice of Filing Order Vacating Final Judgment of Foreclosure',
                      'Notice of Filing Order of Dismissal',
                      'Notice of Filing Certificate of Title',
                      'Certificate of Service of Satisfaction of Judgment',
                      'Affidavit of Satisfaction of Judgment'):
            t = self.case([(3, title, '', '07/01/2026', '')])
            entry = next(e for e in t['entries'] if e['entry_id'] == '3')
            self.assertIsNone(entry.get('attached_document_kind'),
                              'the fixture must be the UNREAD shape: %s' % title)
            self.assertIn(entry['kind'], CV.COVER_KINDS, title)
            r = CV.assess(t)
            self.assertEqual(r['verdict'], 'incomplete', (title, r['missing']))
            self.assertTrue([m for m in r['missing'] if '3' in m], (title, r['missing']))

    def test_a_sale_under_an_unread_cover_reaches_the_sale_reader(self):
        # The sale-calendar subjects route through _sale_state's unlabelled scan rather than an
        # unconditional gap, so they keep the live_floor and closing-word guards three rounds were
        # spent calibrating. Either way the case must not read supported.
        for title in ('Notice of Filing Notice of Foreclosure Sale',
                      'Notice of Filing Order Rescheduling Foreclosure Sale'):
            r = CV.assess(self.case([(3, title, '', '07/01/2026', '')]))
            self.assertEqual(r['verdict'], 'incomplete', (title, r['missing']))

    def test_the_docket_where_less_is_known_is_not_the_supported_one(self):
        # The recurring inversion, stated as a test: the same entry with its document READ was already
        # incomplete, so the unread version must not be the clean bill.
        unread = self.case([(3, 'Notice of Filing Satisfaction of Judgment', '', '07/01/2026', '')])
        read = self.built(self.JUDGED + [(3, 'Notice of Filing Satisfaction of Judgment', '',
                                          '07/01/2026', '')], controlling='2',
                          pages={'2': self.PAGE,
                                 '3': 'SATISFACTION OF JUDGMENT\nThe judgment entered 06/10/2026 is '
                                      'satisfied.'})
        self.assertEqual(next(e for e in read['entries']
                              if e['entry_id'] == '3')['attached_document_kind'], 'satisfaction')
        self.assertEqual(CV.assess(unread)['verdict'], CV.assess(read)['verdict'])

    def test_routine_cover_titled_filings_still_read_supported(self):
        # Contract 5. classify is the discriminator, and on these it lands on proposed_order,
        # affidavit, summons_service, notice_of_filing or other - none of them deciding.
        for title in ('Notice of Filing Proposed Final Judgment',
                      'Notice of Filing Affidavit of Diligent Search', 'Notice of Appearance',
                      'Notice of Dropping Party', 'Certificate of Service',
                      'Notice of Filing Return of Service', 'Affidavit of Attorney Fees'):
            r = CV.assess(self.case([(3, title, '', '07/01/2026', '')]))
            self.assertEqual(r['verdict'], 'supported', (title, r['missing']))

    def test_a_live_sale_beside_its_publication_affidavit_still_reads_supported(self):
        # The shape that decided sale subjects go through _sale_state rather than a bare gap: an
        # "Affidavit of Publication of Notice of Foreclosure Sale" is the routine paperwork of a
        # noticed sale, and an unconditional gap on a cover-titled sale subject held it.
        r = CV.assess(self.case([
            (4, 'Notice of Foreclosure Sale on 12/28/2026', '', '07/01/2026', ''),
            (5, 'Affidavit of Publication of Notice of Foreclosure Sale', '', '07/10/2026', '')]))
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))



class TwentySecondReviewTests(unittest.TestCase):
    """The producer's own two cover regexes disagree on one head, and the sentence the unread half
    printed claimed a document had been read.
    """
    built = staticmethod(EleventhReviewTests.__dict__['built'].__func__)
    PAGE = EleventhReviewTests.JUDGMENT_PAGE
    JUDGED = [(1, 'Complaint', '', '01/05/2026', ''),
              (2, 'Final Judgment of Foreclosure', '', '06/10/2026', '')]

    def case(self, title, pages=None):
        pg = {'2': self.PAGE}
        pg.update(pages or {})
        return self.built(self.JUDGED + [(3, title, '', '07/01/2026', '')], controlling='2', pages=pg)

    def test_the_producers_two_cover_regexes_still_disagree(self):
        # The fix exists because classify (:158) lists `certificate of filing` as a cover head and
        # _FILED_ABOUT_RE (:275) does not. If the producer ever aligns them this test says so, and the
        # local fallback can go.
        import miami_case_timeline as T
        line = 'Certificate of Filing Satisfaction of Judgment'
        self.assertIn(T.classify(line), CV.COVER_KINDS)
        self.assertIsNone(T._FILED_ABOUT_RE.match(line),
                          'the producer now matches this head; drop _CERT_FILING_RE')

    def test_a_dispositive_filing_under_a_certificate_of_filing_is_named(self):
        # classify gave these a COVER label, so the unread-cover branch ran - and _cover_subject could
        # not strip the head, so no gap was raised at all. The bankruptcy row is the worst: the report
        # printed "none on the docket" over a live Chapter 13.
        for title in ('Certificate of Filing Satisfaction of Judgment',
                      'Certificate of Filing Order Vacating Final Judgment',
                      'Certificate of Filing Order of Dismissal',
                      'Certificate of Filing Certificate of Title',
                      'Certificate of Filing Suggestion of Bankruptcy Chapter 13 Case No. '
                      '26-11111-LMI',
                      'Certificate of Filing Notice of Voluntary Dismissal',
                      'Amended Certificate of Filing Satisfaction of Judgment'):
            t = self.case(title)
            self.assertIn(next(e for e in t['entries'] if e['entry_id'] == '3')['kind'],
                          CV.COVER_KINDS, title)
            r = CV.assess(t)
            self.assertEqual(r['verdict'], 'incomplete', (title, r['missing']))
            self.assertTrue([m for m in r['missing'] if 'entry 3' in m], (title, r['missing']))

    def test_routine_certificate_of_filing_entries_still_read_supported(self):
        for title in ('Certificate of Filing Proposed Final Judgment',
                      'Certificate of Filing Affidavit of Diligent Search',
                      'Certificate of Filing Return of Service', 'Certificate of Filing',
                      'Certificate of Service'):
            r = CV.assess(self.case(title))
            self.assertEqual(r['verdict'], 'supported', (title, r['missing']))

    def test_the_unread_half_never_says_a_document_was_read(self):
        # Nobody opened it - the county may index no image at all - and the label came from the docket
        # TITLE through the producer's classifier. CLAUDE.md's own rule: document metadata and keyword
        # signals must never be represented as documents read.
        r = CV.assess(self.case('Notice of Filing Satisfaction of Judgment'))
        line = next(m for m in r['missing'] if 'entry 3' in m)
        self.assertIn('own docket title names', line)
        self.assertIn('nobody opened it', line)
        self.assertNotIn('the document under it reads', line)

    def test_the_read_half_still_says_the_document_reads_as(self):
        # And where a document WAS opened, the sentence stays the producer's own claim.
        t = self.case('Notice of Filing Satisfaction of Judgment',
                      pages={'3': 'SATISFACTION OF JUDGMENT\nThe judgment entered 06/10/2026 is '
                                  'satisfied.'})
        self.assertEqual(next(e for e in t['entries']
                              if e['entry_id'] == '3')['attached_document_kind'], 'satisfaction')
        r = CV.assess(t)
        self.assertTrue([m for m in r['missing'] if 'the document under it reads as satisfaction' in m],
                        r['missing'])



if __name__ == '__main__':
    unittest.main(verbosity=2)
