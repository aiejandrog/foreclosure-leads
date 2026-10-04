"""Boundary checks on the judgment-title test, all fake data: where an affidavit heading sits, a judgment
with no readable total beside a balanced sibling, FINAL JUDGMENT quoted inside another paper, and a
decree whose opening sentence or heading is wrapped or split across pages."""
import hashlib
import unittest
from pathlib import Path

import _moneytest as MT
import run_case_timeline as RCT
from test_text_amount_handoff import AFFIDAVIT_PAGE1, ENTRY, JUDGMENT_PAGE1, NO_AMOUNT_GAP, SiblingDocumentTests, CV, row_for


def row_for_ref(tmp, ref, reading, content, name):
    return row_for(tmp, ref=ref, reading=reading, content=content, name=name)

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


def _padded(heading, at, below=None):
    """Page 1 with `heading` as the `at`-th nonblank line (1-based), neutral filler before it."""
    lines = [l for l in page(below=below or [OPENING]).splitlines() if l.strip()]
    k = 0
    while len(lines) < at - 1:
        k += 1
        lines.append('Paragraph %d of the findings is incorporated by reference.' % k)
    return '\n'.join(lines[:at - 1] + [heading] + lines[at - 1:])


def _page2(heading, at):
    lines = ['Paragraph %d of the findings is incorporated by reference.' % k for k in range(1, at)]
    return '\n'.join(lines + [heading]) + '\n' + DECREE


class HeadingScanWindowTests(unittest.TestCase):
    """The title search reads 25 lines; the contradicting-heading scan must not stop there."""
    HEADINGS = ('PAYOFF STATEMENT', 'AFFIDAVIT OF INDEBTEDNESS', 'Payoff Statement', 'DECLARATION OF AMOUNTS DUE')

    def test_a_heading_anywhere_on_page_one_rejects(self):
        for h in self.HEADINGS:
            for at in (14, 24, 25, 26, 27, 40, 80):
                with self.subTest(heading=h, line=at):
                    self.assertFalse(titled(_padded(h, at)))

    def test_a_heading_anywhere_on_page_two_rejects(self):
        for h in self.HEADINGS:
            for at in (1, 4, 5, 6, 7, 25, 26, 60):
                with self.subTest(heading=h, line=at):
                    self.assertFalse(titled(page(below=[OPENING]), _page2(h, at)))

    def test_the_control_without_a_heading_is_still_a_judgment(self):
        self.assertTrue(titled(_padded('Costs are taxed as set out below.', 30)))
        self.assertTrue(titled(page(below=[OPENING]), _page2('Costs are taxed as set out below.', 30)))


class HeadingPunctuationTests(unittest.TestCase):
    def test_closing_punctuation_does_not_exempt_a_heading(self):
        for h in ('PAYOFF STATEMENT', 'PAYOFF STATEMENT:', 'PAYOFF STATEMENT.', 'PAYOFF STATEMENT;', 'PAYOFF STATEMENT,',
                  'AFFIDAVIT OF INDEBTEDNESS', 'AFFIDAVIT OF INDEBTEDNESS.', 'Affidavit of Indebtedness:',
                  'DECLARATION OF COSTS:'):
            with self.subTest(heading=h):
                body = [OPENING, 'The Court has considered the file.', 'The Court finds service was proper.', h,
                        'Principal $1.00']
                self.assertFalse(titled(page(below=body)))

    def test_recital_sentences_that_refer_to_affidavits_still_pass(self):
        for s in ('Affidavit of Indebtedness filed in support of the Motion.',
                  'Affidavit of Indebtedness filed by Plaintiff and',
                  'the affidavits filed in support of the Motion, finding no genuine issue,',
                  'Plaintiff relied on its Affidavit of Indebtedness dated June 1, 2026.',
                  'Costs per cost declaration $2.00'):
            with self.subTest(sentence=s):
                body = [OPENING, 'The Court has considered the file.', s, 'Amounts Due and Owing.  Plaintiff is due:']
                self.assertTrue(titled(page(below=body)))


class DraftAndCertificateTests(unittest.TestCase):
    def test_draft_and_signature_wording_rejects(self):
        for w in ('DRAFT', 'Draft - submitted for signature', 'For the Court\'s signature', 'SUBMITTED FOR SIGNATURE'):
            with self.subTest(wording=w):
                self.assertFalse(titled(page(below=[OPENING, w])))


OTHER_P2 = MT.PAGE2.replace('$150,000.00', '$140,000.00')
OTHER_P3 = MT.PAGE3.replace('$178,172.62', '$168,172.62')
TOTAL_A, TOTAL_B = 178172.62, 168172.62
JBYTES, SBYTES = b'judgment bytes: signed', b'sibling bytes: other paper'


class AmountAttributionTests(SiblingDocumentTests):
    """The exact award and the document it came from, with a second balanced total beside it."""

    def build(self, judgment, sibling, sibling_first=False):
        jref, sref = ('court:%s:2' % ENTRY, 'court:%s:1' % ENTRY) if sibling_first else \
            ('court:%s:1' % ENTRY, 'court:%s:2' % ENTRY)
        rows = [row_for_ref(self.tmp, jref, judgment, JBYTES, 'j.pdf'),
                row_for_ref(self.tmp, sref, sibling, SBYTES, 's.pdf')]
        saved = self.saved(rows, ['court:%s:1' % ENTRY, 'court:%s:2' % ENTRY])
        return saved, CV.assess(saved), jref, sref

    def check_award(self, sibling_first):
        judgment = MT.text_reading(p1=JUDGMENT_PAGE1)                               # total A
        sibling = MT.text_reading(p1=AFFIDAVIT_PAGE1, p2=OTHER_P2, p3=OTHER_P3)     # total B, balanced
        saved, result, jref, sref = self.build(judgment, sibling, sibling_first)
        ok = {c['source_ref']: c for c in saved['amount_checks'] if c['ok']}
        self.assertIn(sref, ok, 'the sibling must verify for this test to mean anything')
        self.assertEqual(result['judgment_amount'], TOTAL_A)
        self.assertNotEqual(result['judgment_amount'], TOTAL_B)
        self.assertEqual(ok[jref]['document_hash'], hashlib.sha256(JBYTES).hexdigest())
        self.assertTrue([s for s in result['supported_by'] if jref in s and 'verified to the cent' in s], result['supported_by'])
        self.assertFalse([s for s in result['supported_by'] if sref in s and 'verified to the cent' in s])

    def test_the_award_is_the_judgments_total_and_comes_from_its_document_judgment_first(self):
        self.check_award(False)

    def test_the_award_is_the_judgments_total_and_comes_from_its_document_sibling_first(self):
        self.check_award(True)

    def test_no_total_in_the_judgment_means_no_amount_in_either_order(self):
        for first in (False, True):
            with self.subTest(sibling_first=first):
                judgment = MT.text_reading(p1=JUDGMENT_PAGE1, p2='The amounts due are as set out in the clerk record.',
                                           p3='Jurisdiction is retained.')
                sibling = MT.text_reading(p1=AFFIDAVIT_PAGE1, p2=OTHER_P2, p3=OTHER_P3)
                saved, result, jref, sref = self.build(judgment, sibling, first)
                self.assertTrue([c for c in saved['amount_checks'] if c['ok'] and c['source_ref'] == sref])
                self.assertIsNone(result['judgment_amount'])
                self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])

    def test_a_second_paper_laid_out_exactly_like_the_judgment_is_never_silently_the_award(self):
        # Nothing in the text separates an unsigned copy from the signed judgment (no marker, same layout,
        # a certificate of service on both). The case verdict must then not present either total alone.
        twin = MT.text_reading(p1=JUDGMENT_PAGE1 + '\nI HEREBY CERTIFY that a copy was furnished to counsel of record.',
                               p2=OTHER_P2, p3=OTHER_P3)
        judgment = MT.text_reading(p1=JUDGMENT_PAGE1 + '\nI HEREBY CERTIFY that a copy was furnished to counsel of record.')
        for first in (False, True):
            with self.subTest(sibling_first=first):
                saved, result, jref, sref = self.build(judgment, twin, first)
                self.assertEqual(result['verdict'], 'conflicted', result)
                self.assertEqual(result['judgment_amount'], [TOTAL_B, TOTAL_A])
                self.assertNotEqual(result['judgment_amount'], TOTAL_A)
                self.assertNotEqual(result['judgment_amount'], TOTAL_B)


if __name__ == '__main__':
    unittest.main()
