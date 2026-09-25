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


def timeline(case, kind='judgment_entered', reason=None, controlling='82',
             controlling_reason='one operative judgment after amendments, vacaturs and satisfactions',
             stay=None, history=(), checks=(), attachments=(), sale_held=None, gaps=()):
    status = {'kind': kind, 'evidence': [], 'reason': reason or ''}
    return {'case': case, 'county': 'MIAMI-DADE', 'as_of': '2026-09-24',
            'status': status,
            'judgments': {'controlling_entry': controlling, 'controlling_reason': controlling_reason,
                          'judgments': [], 'unmatched': []},
            'stay_in_effect': stay, 'stay_history': list(history),
            'sale_held': sale_held,
            'amount_vision': {'amount_checks': list(checks)},
            'coverage': {'attachments': list(attachments), 'complete': False},
            'gaps': list(gaps), 'coverage_complete': not gaps}


def ok_check(entry_id='82', source_ref='court:232820355:1', amount=1746032.70):
    return {'entry_id': entry_id, 'source_ref': source_ref, 'amount': amount, 'ok': True,
            'reason': 'one run of printed rows ending at the total adds up to it exactly; '
                      'subtotals agree',
            'pages': [2, 3], 'run': 'continued_from_page_2', 'disagreeing_subtotals': []}


def failed_check(entry_id='140', source_ref='court:232632335:1', amount=785670.31, subtotals=()):
    return {'entry_id': entry_id, 'source_ref': source_ref, 'amount': amount, 'ok': False,
            'reason': 'no contiguous run of rows ending at the total adds up to it to the cent '
                      '(tried up to 3 page(s) back)',
            'pages': [1, 2], 'run': None, 'disagreeing_subtotals': list(subtotals)}


def read_attachment(entry_id='82'):
    return {'entry_id': entry_id, 'kind': 'final_judgment', 'state': 'read', 'detail': []}


class PilotVerdictTests(unittest.TestCase):
    """The five cases from the status table's acceptance section."""

    def test_2024_014878_is_supported(self):
        # "$1,746,032.70 verifies on court:232820355:1 pp. 2-3. Controlling judgment #82.
        #  Sale 2026-09-28."
        r = CV.assess(timeline('2024-014878-CA-01', kind='sale_scheduled', controlling='82',
                               checks=[ok_check()], attachments=[read_attachment('82')]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertEqual(r['conflicts'], [])
        self.assertEqual(r['missing'], [])
        self.assertTrue(any('1746032' in s or '232820355' in s for s in r['supported_by']))

    def test_2022_012065_is_supported(self):
        r = CV.assess(timeline('2022-012065-CA-01', controlling='174',
                               checks=[ok_check('174', 'court:231714504:1', 373482.79)],
                               attachments=[read_attachment('174')]))
        self.assertEqual(r['verdict'], 'supported')

    def test_2024_009959_is_supported_on_amount_and_posture(self):
        # The table's "supported (amount and posture)". Estate contact authority is NOT part of this
        # verdict: whether anyone may be called is miami_ranking.qualify's decision, not this one.
        r = CV.assess(timeline('2024-009959-CA-01', controlling='79',
                               checks=[ok_check('79', 'court:231457022:1', 555499.25)],
                               attachments=[read_attachment('79')]))
        self.assertEqual(r['verdict'], 'supported')

    def test_2023_020247_is_supported_with_the_stay_noted(self):
        # "Stay in effect: #125 reinstates it. $305,151.92 verifies. Controlling #91." A stay in
        # effect with no sale going ahead is a note, not a contradiction.
        r = CV.assess(timeline('2023-020247-CA-01', controlling='91', stay=True,
                               history=[{'entry_id': '125', 'event': 'reinstated'}],
                               checks=[ok_check('91', 'court:224002597:1', 305151.92)],
                               attachments=[read_attachment('91')]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertEqual(r['conflicts'], [])
        self.assertTrue(any('stay is in effect' in n for n in r['notes']))

    def test_2018_026274_is_conflicted_and_the_amount_does_not_verify(self):
        # "conflicted; amount incomplete. Stay #93, no relief order found, vs amended judgment #140
        #  and sale notice #143 for 2026-09-28. $785,670.31 does not verify: the judgment's own
        #  printed interest subtotal $225,243.83 is $0.60 below its eight yearly rows."
        r = CV.assess(timeline(
            '2018-026274-CA-01', kind='sale_scheduled', controlling='140', stay=True,
            history=[{'entry_id': '93', 'event': 'stayed'}],
            checks=[failed_check(subtotals=[{'page': 2, 'label': 'INTEREST', 'amount': 225243.83,
                                             'rows_above_sum': 225244.43, 'difference': 0.60}])],
            attachments=[read_attachment('140')]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any('stay is in effect while the docket shows a sale' in c
                            for c in r['conflicts']), r['conflicts'])
        self.assertTrue(any('$225,243.83'.replace(',', '') in c.replace(',', '')
                            for c in r['conflicts']), r['conflicts'])
        self.assertTrue(any('off by $0.60' in c for c in r['conflicts']), r['conflicts'])
        self.assertTrue(any('verifies to the cent' in m for m in r['missing']), r['missing'])


class PostureTests(unittest.TestCase):
    def test_conflicting_judgments_are_conflicted_not_incomplete(self):
        r = CV.assess(timeline('X', controlling=None,
                               controlling_reason=CV.CONFLICTING_JUDGMENTS,
                               checks=[ok_check(None)]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertEqual(r['missing'], [])

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
        r = CV.assess(timeline('X', controlling='82',
                               checks=[ok_check(entry_id='57', source_ref='official:99-1')],
                               attachments=[read_attachment('82')]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('verifies to the cent' in m for m in r['missing']))

    def test_a_check_with_no_entry_id_still_counts(self):
        # Older saved evidence recorded no entry_id; it is not evidence for a different judgment.
        r = CV.assess(timeline('X', controlling='82', checks=[ok_check(entry_id=None)],
                               attachments=[read_attachment('82')]))
        self.assertEqual(r['verdict'], 'supported')

    def test_two_runs_reaching_the_total_is_conflicted(self):
        check = failed_check()
        check['reason'] = CV.AMBIGUOUS_RUN
        r = CV.assess(timeline('X', controlling='140', checks=[check],
                               attachments=[read_attachment('140')]))
        self.assertEqual(r['verdict'], 'conflicted')
        self.assertTrue(any('more than one reading' in c for c in r['conflicts']))

    def test_unread_amount_pages_are_missing_not_contradictory(self):
        check = failed_check()
        check['reason'] = 'amount pages have unresolved reading gaps'
        r = CV.assess(timeline('X', controlling='140', checks=[check],
                               attachments=[read_attachment('140')]))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertEqual(r['conflicts'], [])

    def test_no_check_at_all_is_incomplete(self):
        r = CV.assess(timeline('X', checks=[], attachments=[read_attachment()]))
        self.assertEqual(r['verdict'], 'incomplete')


class CoverageTests(unittest.TestCase):
    def test_a_login_walled_controlling_judgment_is_incomplete(self):
        for state in ('restricted', 'restricted_likely', 'login_required', 'login_required_likely'):
            r = CV.assess(timeline('X', controlling='82', checks=[ok_check()],
                                   attachments=[{'entry_id': '82', 'state': state}]))
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
        r = CV.assess(timeline('X', controlling='82', checks=[ok_check()],
                               attachments=[read_attachment('82'),
                                            {'entry_id': '5', 'state': 'fetched_unread'},
                                            {'entry_id': '6', 'state': 'restricted'}]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertTrue(any('elsewhere on the docket' in n for n in r['notes']))
        self.assertTrue(any("behind the clerk's login" in n for n in r['notes']))

    def test_county_has_no_document_is_not_counted_as_unread_elsewhere(self):
        r = CV.assess(timeline('X', controlling='82', checks=[ok_check()],
                               attachments=[read_attachment('82'),
                                            {'entry_id': '5', 'state': 'county_no_document'}]))
        self.assertEqual(r['verdict'], 'supported')
        self.assertFalse(any('elsewhere on the docket' in n for n in r['notes']))

    def test_a_missing_image_on_the_controlling_judgment_is_incomplete(self):
        for state in ('no_image_indexed', 'not_fetched', 'county_no_document', 'not_enumerated'):
            r = CV.assess(timeline('X', controlling='82', checks=[ok_check()],
                                   attachments=[{'entry_id': '82', 'state': state}]))
            self.assertEqual(r['verdict'], 'incomplete', state)


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
                             ('2018-026274-CA-01', {'kind': 'sale_scheduled', 'controlling': '140',
                                                    'stay': True,
                                                    'history': [{'entry_id': '93',
                                                                 'event': 'stayed'}],
                                                    'checks': [failed_check()],
                                                    'attachments': [read_attachment('140')]})):
                (folder / (case + '.json')).write_text(
                    json.dumps({'case': case, 'complete': False, 'open_gaps': []}))
                (folder / (case + '-timeline.json')).write_text(
                    json.dumps(timeline(case, **kw)))
            # A dossier with no timeline saved yet is reported as skipped, never as a verdict.
            (folder / 'no-timeline-yet.json').write_text(json.dumps({'case': 'Z'}))

            proc = subprocess.run([sys.executable, '-u', 'case_verdict.py', '--dossiers',
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
        proc = subprocess.run([sys.executable, 'case_verdict.py'], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('--dossiers', proc.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
