"""The judgment-title check against the page-1 layouts of five saved real Miami-Dade judgments.

Shapes were read offline on 2026-10-03/04 and redacted (names, addresses, figures, case numbers replaced); the
fixtures below are those line-for-line layouts with invented names. Each must read as a judgment, and a
sibling document (affidavit, motion, proposed judgment) laid out the same way must not."""
import unittest

import _moneytest as MT
import run_case_timeline as RCT
from test_text_amount_handoff import AFFIDAVIT_PAGE1, ENTRY, NO_AMOUNT_GAP, SiblingDocumentTests, CV

CAPTION = ('IN THE CIRCUIT COURT OF THE ELEVENTH JUDICIAL\nCIRCUIT IN AND FOR MIAMI-DADE COUNTY, FLORIDA\n'
           'CASE NO: 2099-000001-CA-01\nSECTION: CA11\nJUDGE: JANE ROE\n')
RULE = '____________________________/\n'
DECREE = MT.PAGE2 + '\nIt is ORDERED AND ADJUDGED that the clerk shall sell the property.'
FOOT = 'Case No: 2099-000001-CA-01\nFiling # 99999999 E-Filed 08/19/2026 02:00:45 PM'

SHAPES = {
    'summary-judgment with affidavit recitals': CAPTION + (
        'ALPHA LENDING TRUST\nPlaintiff(s)\nvs.\n12 MAIN STREET HOLDINGS\nComp et al\nDefendant(s)\n') + RULE + (
        'FINAL JUDGMENT OF MORTGAGE FORECLOSURE AND FOR OTHER RELIEF\n'
        'THIS ACTION was heard before this Honorable Court on July 7, 2026, upon Plaintiff, ALPHA LENDING TRUST\n'
        'Foreclosure and Other Relief (the "Motion").  The Court, having reviewed the Motion, the affidavits filed in support of\n'
        'said Motion, the relevant case law, the pleadings, the Clerk\'s docket, and all other relevant filings, finding no affidavits\n'
        'c)         The Borrower defaulted on the Loan Documents by failing to pay the full amount\n'
        'due upon maturity of the Loan Documents on January 1, 2024; same is supported by Plaintiff\'s business records and its\n'
        'Affidavit of Indebtedness filed in support of the Motion.\n') + FOOT,
    'renewed motion recital': CAPTION + (
        'ALPHA LENDING TRUST\nASSOCIATION\nPlaintiff(s)\nvs.\nBETA OWNER\net al\nDefendant(s)\n') + RULE + (
        'FINAL JUDGMENT OF MORTGAGE FORECLOSURE\n'
        "THIS CAUSE came before the Court for hearing on June 11, 2026, on Plaintiff's, U.S.\n"
        'ALPHA LENDING TRUST, NOT IN ITS INDIVIDUAL CAPACITY BUT\n'
        'SOLELY AS OWNER TRUSTEE FOR BETA TRUST ("Plaintiff"), Renewed\n'
        'Motion for Summary Judgment (D.E. # 156). After hearing argument of the parties, and upon\n'
        'consideration of the Motion, the affidavits filed in support of the Motion, the record evidence,\n'
        "borrower's agreement to pay installment amounts due under the note and mortgage, a default by the\n"
        'borrower under the Note and Mortgage, and liability for, and calculation of, the amounts due as set\n'
        'forth in the affidavits, including attorney\'s fees and costs.\n') + FOOT,
    'stacked agreed titles, unnumbered award heading': CAPTION + (
        'ALPHA LENDING TRUST\nBETA TRUST\nPlaintiff(s)\nvs.\nGAMMA OWNER (DECEASED) et al\nDefendant(s)\n') + RULE + (
        'AGREED FINAL JUDGMENT\nCONSENT FINAL JUDGMENT OF FORECLOSURE\n(in rem relief)\n'
        'THIS ACTION was heard before the Court upon the consent of the parties on June 16,\n'
        '2026.  On the evidence presented, IT IS ORDERED AND ADJUDGED that FINAL\n'
        'JUDGMENT OF FORECLOSURE is GRANTED against all Defendants.\n'
        'Amounts Due and Owing.  Plaintiff is due:\n1.\n'
        'Principal due on the note secured by the mortgage foreclosed:   $1.00\n'
        'Costs per cost declaration   $2.00\nAttorneys\' Fees Total   $3.00\n') + FOOT,
    'ocr award heading': 'Filing # 99999999 E-Filed 07/10/2025 01 PM\nIN THE CIRCUIT COURT OF THE ELEVENTH JUDICIAL\n'
        'CIRCUIT IN AND FOR MIAMI-DADE COUNTY, FLORIDA\nCASE NO: 2099-000001-CA-01\nSECTION. CA31\nJUDGE: JANE ROE\n'
        'ALPHA LENDING TRUST\nPlaintiff(s)\nvs.\nGAMMA OWNER et al\nDefendant(s)\nFINAL JUDGMENT OF FORECLOSURE\n'
        'THIS ACTION was heard before the Court at Non-Jury Trial on May 20, 2025. On the evidence\npresented, it is hereby,\n'
        'ORDERED AND ADJUDGED as follows:\nFinal Judgment of Foreclosure is GRANTED in favor of the Plaintiff ALPHA\n'
        'l. Amounts Due and Owing. Plaintiff is due:\nPrincipal Balance\nInterest from 5/08/2022 to 5/16/2025\n',
    'county court default judgment with docket header fields': (
        'IN THE COUNTY COURT OF THE ELEVENTH JUDICIAL\nCIRCUIT IN AND FOR MIAMI-DADE COUNTY, FLORIDA\n'
        'CASE NO: 2099-000001-CC-26\nSECTION: CC04\nJUDGE: JANE ROE\nALPHA CLUB\nBETA ASSOCIATION\n'
        'Plaintiff(s) / Petitioner(s)\nvs.\nGAMMA de DELTA\nDefendant(s) / Respondent(s)\n') + RULE + (
        'DEFAULT FINAL JUDGMENT\nDocket Index Number: 21\nDate Filed: 6/24/2026\n'
        'Full Name of Motion:  Renewed Motion for Default Final Judgment\n'
        'THIS CAUSE came on to be heard on August 13, 2026 upon Plaintiff\'s, ALPHA CLUB\n'
        'ORDERED AND ADJUDGED as follows:\n1.        Plaintiff, ALPHA CLUB a\n'
        'Defendant, GAMMA DE DELTA the sum of:\na.         $1.00   Assessments;\n') + FOOT,
}


def titled(page1, decree=DECREE):
    return RCT.judgment_titled(MT.text_reading(p1=page1, p2=decree))


class RealShapeTests(unittest.TestCase):
    def test_each_real_layout_reads_as_a_judgment(self):
        for name, page1 in SHAPES.items():
            with self.subTest(shape=name):
                self.assertTrue(titled(page1))

    def test_a_sibling_laid_out_like_each_judgment_does_not(self):
        for name, page1 in SHAPES.items():
            lines = page1.splitlines()
            at = next(i for i, ln in enumerate(lines) if RCT._JUDGMENT_TITLE_RE.match(ln))
            variants = {
                'sworn': lines[:at + 1] + ['Sworn to before me this day, NOTARY PUBLIC.'] + lines[at + 1:],
                'affiant': lines[:at + 1] + ['AFFIANT states the amounts due are as follows.'] + lines[at + 1:],
                'proposed': lines[:at] + ['[PROPOSED]'] + lines[at:],
                'affidavit title above': lines[:at] + ['AFFIDAVIT OF INDEBTEDNESS'] + lines[at:],
                'affidavit title below': lines[:at + 1] + ['AFFIDAVIT OF AMOUNTS DUE'] + lines[at + 1:],
                'motion title above': lines[:at] + ["PLAINTIFF'S MOTION FOR"] + lines[at:],
                'order granting above': lines[:at] + ['ORDER GRANTING MOTION FOR'] + lines[at:],
                'payoff schedule below': lines[:at + 1] + ['PAYOFF STATEMENT'] + lines[at + 1:],
                'motion heading under header fields': lines[:at + 1] + ['Docket Index Number: 21',
                                                                        'MOTION FOR SUMMARY JUDGMENT'] + lines[at + 1:],
            }
            for label, v in variants.items():
                with self.subTest(shape=name, variant=label):
                    self.assertFalse(titled('\n'.join(v)))

    def test_each_real_layout_keeps_its_amount_beside_a_sibling_and_the_sibling_is_a_note(self):
        for name, page1 in SHAPES.items():
            with self.subTest(shape=name):
                helper = SiblingDocumentTests('test_a_check_records_what_the_document_reads_as')
                helper.setUp()
                self.addCleanup(helper.tearDown)
                saved = helper.saved(helper.rows(MT.text_reading(p1=page1, p2=DECREE), MT.text_reading(p1=AFFIDAVIT_PAGE1)),
                                     ['court:%s:1' % ENTRY, 'court:%s:2' % ENTRY])
                result = CV.assess(saved)
                self.assertFalse([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])
                self.assertTrue([n for n in result['notes'] if 'not identified as the final judgment' in n],
                                result['notes'])


if __name__ == '__main__':
    unittest.main()
