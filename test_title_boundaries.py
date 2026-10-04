"""Boundary checks on the judgment-title test, all fake data: where an affidavit heading sits, a judgment
with no readable total beside a balanced sibling, FINAL JUDGMENT quoted inside another paper, and a
decree whose opening sentence or heading is wrapped or split across pages."""
import unittest

import _moneytest as MT
import run_case_timeline as RCT
from test_text_amount_handoff import AFFIDAVIT_PAGE1, ENTRY, JUDGMENT_PAGE1, NO_AMOUNT_GAP, SiblingDocumentTests, CV

CAPTION = ('IN THE CIRCUIT COURT OF THE ELEVENTH JUDICIAL\nCIRCUIT IN AND FOR MIAMI-DADE COUNTY, FLORIDA\n'
           'CASE NO: 2099-000001-CA-01\n')
PARTIES = 'ALPHA LENDING TRUST\nPlaintiff(s)\nvs.\nGAMMA OWNER\nDefendant(s)\n____________________________/\n'
TITLE = 'FINAL JUDGMENT OF FORECLOSURE'
DECREE = MT.PAGE2 + '\nIt is ORDERED AND ADJUDGED that the clerk shall sell the property.'
OPENING = 'THIS ACTION was heard before the Court on June 16, 2026.'
FILLER = ['Filed on behalf of Plaintiff.', 'Reference 99-0001.', 'Counsel of record appears.', 'Division CA11.',
          'Hearing was noticed.', 'Parties were served.', 'Matters under submission.', 'No jury was demanded.']


def titled(page1, page2=DECREE):
    return RCT.judgment_titled(MT.text_reading(p1=page1, p2=page2))


def page(above=(), below=(), heading=None):
    """Caption + title with `above` lines between the parties and the title, `below` lines under it."""
    return CAPTION + PARTIES + ''.join(l + '\n' for l in above) + TITLE + '\n' + '\n'.join(below)


class HeadingDistanceTests(unittest.TestCase):
    def test_an_affidavit_heading_just_below_the_five_line_zone_still_rejects(self):
        for n in (4, 5, 6, 7, 10):                      # lines between the title and the heading
            for head in ('AFFIDAVIT OF INDEBTEDNESS', 'PAYOFF STATEMENT', 'DECLARATION OF AMOUNTS DUE'):
                with self.subTest(gap=n, heading=head):
                    below = ['Case No: 2099-000001-CA-01'] + FILLER[:n - 1] + [head, 'Principal $1.00']
                    self.assertFalse(titled(page(below=below)))

    def test_an_affidavit_heading_just_beyond_twelve_lines_above_still_rejects(self):
        for n in (11, 12, 13, 14):                      # lines between the heading and the title
            with self.subTest(gap=n):
                above = ['AFFIDAVIT OF INDEBTEDNESS'] + FILLER[:min(n, len(FILLER))] + FILLER[:max(0, n - len(FILLER))]
                self.assertFalse(titled('\n'.join(above) + '\n' + CAPTION + PARTIES + TITLE + '\n' + OPENING))

    def test_the_judgments_own_body_may_still_mention_affidavits_after_its_opening_sentence(self):
        body = [OPENING, 'The Court considered the affidavits filed in support and the Affidavit of Indebtedness.',
                'Amounts due are as set forth in the affidavits.']
        self.assertTrue(titled(page(below=body)))
        self.assertTrue(titled(page(below=['Docket Index Number: 21'] + body)))


class NoReadableTotalTests(SiblingDocumentTests):
    """A real judgment with no extractable total beside a sibling whose table balances."""

    def _check(self, order):
        judgment = MT.text_reading(p1=JUDGMENT_PAGE1, p2='The amounts due are as set out in the clerk record.',
                                   p3='Jurisdiction is retained.')
        sibling = MT.text_reading(p1=AFFIDAVIT_PAGE1)
        rows = self.rows(judgment, sibling)
        refs = ['court:%s:1' % ENTRY, 'court:%s:2' % ENTRY]
        if order == 'sibling first':
            rows = [dict(rows[1], source_ref=refs[0]), dict(rows[0], source_ref=refs[1])]
        saved = self.saved(rows, refs)
        self.assertTrue([c for c in saved['amount_checks'] if c['ok']], 'the sibling must verify for this test to mean anything')
        result = CV.assess(saved)
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])
        self.assertNotEqual(result['verdict'], 'supported')
        self.assertFalse([s for s in result['supported_by'] if 'verified to the cent' in s], result['supported_by'])

    def test_the_award_stays_unknown_with_the_judgment_first(self):
        self._check('judgment first')

    def test_the_award_stays_unknown_with_the_sibling_first(self):
        self._check('sibling first')


class QuotedTitleTests(unittest.TestCase):
    def test_final_judgment_inside_a_motion_name_header_is_not_the_title(self):
        for head in (['ORDER ON PLAINTIFF\'S MOTION', 'Full Name of Motion:'],
                     ['ORDER', 'Full Name of Motion: Renewed Motion for'],
                     ["Plaintiff's Motion for", ],
                     ['Motion Number: 21', 'Full Name of Motion:'],
                     ['EXHIBIT A']):
            with self.subTest(head=head):
                p1 = CAPTION + PARTIES + '\n'.join(head) + '\n' + TITLE + '\n' + OPENING + '\nORDERED AND ADJUDGED'
                self.assertFalse(titled(p1))

    def test_final_judgment_quoted_with_decree_wording_in_the_quotation_is_not_the_title(self):
        for quote in ('"%s' % TITLE, '“%s' % TITLE, "'%s" % TITLE, '> %s' % TITLE, '"%s"' % TITLE):
            with self.subTest(quote=quote):
                p1 = (CAPTION + PARTIES + 'The Plaintiff submitted the following:\n' + quote + '\n'
                      + OPENING + '\nIt is ORDERED AND ADJUDGED that the clerk shall sell the property."')
                self.assertFalse(titled(p1))
                # and with the caption straight above the quotation
                p1 = CAPTION + PARTIES + quote + '\n' + OPENING + '\nIt is ORDERED AND ADJUDGED that the clerk shall sell the property."'
                self.assertFalse(titled(p1))


class WrappedAndSplitTests(unittest.TestCase):
    def test_a_wrapped_opening_sentence_still_ends_the_title_zone(self):
        wrapped = ['THIS', 'ACTION was heard before the Court on June 16, 2026.',
                   'The Court considered the affidavits filed in support.']
        self.assertTrue(titled(page(below=wrapped)))
        self.assertTrue(titled(page(below=['THIS ACTION', 'was heard before the Court.'] + FILLER[:2] +
                                      ['The Affidavit of Indebtedness was filed.'])))

    def test_a_wrapped_opening_sentence_does_not_shelter_an_affidavit_heading_before_it(self):
        for heading in ('AFFIDAVIT OF INDEBTEDNESS', 'PAYOFF STATEMENT'):
            with self.subTest(heading=heading):
                below = ['Case No: 2099-000001-CA-01'] + FILLER[:5] + [heading, 'THIS', 'ACTION came on.']
                self.assertFalse(titled(page(below=below)))

    def test_an_opening_sentence_that_never_appears_does_not_hide_an_affidavit_heading(self):
        below = FILLER[:6] + ['AFFIDAVIT OF INDEBTEDNESS', 'Principal $1.00']
        self.assertFalse(titled(page(below=below)))

    def test_a_heading_split_across_pages_is_judged_on_the_first_page_only(self):
        # "ORDER GRANTING MOTION FOR" ends page 1, "FINAL JUDGMENT" opens page 2: page 1 has no title.
        self.assertFalse(titled(CAPTION + PARTIES + 'ORDER GRANTING MOTION FOR', 'FINAL JUDGMENT\n' + DECREE))
        # "AFFIDAVIT OF" ends page 1, "FINAL JUDGMENT" opens page 2.
        self.assertFalse(titled(CAPTION + PARTIES + 'AFFIDAVIT OF', 'FINAL JUDGMENT\n' + OPENING + '\n' + DECREE))
        # a title split in two on page 1 ("FINAL" / "JUDGMENT OF FORECLOSURE") is held, not accepted
        self.assertFalse(titled(CAPTION + PARTIES + 'FINAL\nJUDGMENT OF FORECLOSURE\n' + OPENING))

    def test_a_cover_page_title_with_the_sworn_text_on_page_two_is_not_a_judgment_with_that_text_accepted(self):
        p1 = CAPTION + PARTIES + TITLE
        p2 = 'AFFIDAVIT OF INDEBTEDNESS\nAFFIANT being duly sworn states the amounts due.\n' + DECREE
        self.assertFalse(titled(p1, p2))


class DesktopProbeTests(unittest.TestCase):
    """Holes found by an independent probe of the first #162 head (85d6e3b)."""

    def test_a_heading_after_the_opening_sentence_is_still_a_heading(self):
        for head in ('AFFIDAVIT OF AMOUNTS DUE', 'PAYOFF STATEMENT', 'Affidavit of Indebtedness',
                     'DECLARATION OF AMOUNTS DUE AND OWING'):
            for gap in (0, 1, 2, 3, 6, 9):
                with self.subTest(heading=head, gap=gap):
                    below = [OPENING] + FILLER[:gap] + [head, 'Principal $1.00']
                    self.assertFalse(titled(page(below=below)))

    def test_a_notice_that_quotes_the_judgment_after_a_neutral_lead_in_is_not_a_judgment(self):
        for lead in ('Plaintiff files the following:', 'The Court entered the following order:', 'Attached hereto:'):
            with self.subTest(lead=lead):
                p1 = (CAPTION + 'ALPHA LENDING TRUST\nPlaintiff(s)\nvs.\nGAMMA OWNER\nDefendant(s)\n'
                      'NOTICE OF FILING\n' + lead + '\n' + TITLE + '\n' + OPENING)
                self.assertFalse(titled(p1))

    def test_a_title_ending_page_one_with_an_affidavit_heading_opening_page_two_is_not_a_judgment(self):
        for head in ('AFFIDAVIT OF AMOUNTS DUE', 'PAYOFF STATEMENT', 'Affidavit of Indebtedness'):
            with self.subTest(heading=head):
                self.assertFalse(titled(CAPTION + PARTIES + TITLE, head + '\n' + DECREE))

    def test_a_first_readable_page_that_is_not_page_one_is_held(self):
        reading = MT.text_reading(p1=page(below=[OPENING]), p2=DECREE)
        self.assertTrue(RCT.judgment_titled(reading))
        reading['pages'][0]['outcome'] = 'image_only'
        self.assertFalse(RCT.judgment_titled(reading))

    def test_a_judgments_own_recital_lines_still_pass(self):
        recital = [OPENING, "the affidavits filed in support of the Motion, finding no genuine issue,",
                   'Affidavit of Indebtedness filed in support of the Motion.',
                   'Amounts Due and Owing.  Plaintiff is due:']
        self.assertTrue(titled(page(below=recital)))

    def test_party_names_that_open_with_an_instrument_word_do_not_hold_a_real_judgment(self):
        for plaintiff in ('GRANT PROPERTIES LLC,', 'SETTLEMENT SERVICES, INC.,', 'JOINT VENTURE LLC,'):
            with self.subTest(plaintiff=plaintiff):
                p1 = CAPTION + plaintiff + '\nPlaintiff(s)\nvs.\nGAMMA OWNER,\nDefendant(s)\n_____/\n' + TITLE + '\n' + OPENING
                self.assertTrue(titled(p1))


if __name__ == '__main__':
    unittest.main()
