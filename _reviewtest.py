"""The 12-case eyeball review: log, verdicts, the sheet, and what the review must NOT change.

SYNTHETIC FIXTURES ONLY. No homeowner data, no network.

Run:  python _reviewtest.py       (needs PyMuPDF)
"""
import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix='dealflow-reviewtest-')
os.environ['DEALFLOW_DIR'] = _TMP
os.environ.pop('ONEDRIVE', None)
os.environ.pop('OneDrive', None)

import document_store as DS             # noqa: E402
import judgment_review as JR            # noqa: E402
import miami_judgment as MJ             # noqa: E402

CASE = '2026-000000-CA-01'


def make_pdf(pages=3):
    fitz = DS._fitz()
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), 'page %d  GRAND TOTAL $1,000.00' % (i + 1))
    path = os.path.join(_TMP, 'fixture-%d.pdf' % pages)
    doc.save(path)
    doc.close()
    return path


def report(case=CASE, amount=1000.00, pdf=None, source='ocr', sum_check=True):
    candidate = {'amount': amount, 'page': 2, 'text_source': source, 'sum_check': sum_check,
                 'passage': 'GRAND TOTAL $1,000.00', 'sum_check_components': [900.0, 100.0],
                 'sum_check_reason': '900.00 + 100.00 = 1,000.00'}
    return {'case': case, 'county': 'MIAMI-DADE',
            'judgment_amount_agreed': amount if sum_check else None,
            'judgment_amount_corroborated': sum_check,
            'judgment_amount_candidates': [candidate],
            'documents': [{'source_ref': 'rec:1', 'path': pdf, 'images_dir': None,
                           'page_count_verified': True, 'read_status': 'read', 'case_tie': {'tier': 0},
                           'amount_candidates': [candidate]}]}


class LogTests(unittest.TestCase):
    def setUp(self):
        self.log = 'review-%s.json' % self.id().rsplit('.', 1)[-1]

    def entries(self):
        return JR.load(self.log)[0]

    def test_log_records_where_the_figure_is(self):
        pdf = make_pdf()
        MJ.log_corroboration(report(pdf=pdf), filename=self.log)
        figure = self.entries()[0]['figures'][0]
        self.assertEqual((figure['pdf'], figure['page'], figure['amount']), (pdf, 2, 1000.00))
        self.assertEqual(figure['components'], [900.0, 100.0])

    def test_uncorroborated_report_is_not_logged(self):
        self.assertEqual(MJ.log_corroboration(report(sum_check=False), filename=self.log),
                         (None, None))
        self.assertEqual(self.entries(), [])

    def test_rerun_keeps_the_verdict_for_the_same_figure(self):
        MJ.log_corroboration(report(), filename=self.log)
        entries, target = JR.load(self.log)
        JR.mark(entries, CASE, 'ok')
        JR.save(entries, target)
        MJ.log_corroboration(report(), filename=self.log)
        entry = self.entries()[0]
        self.assertTrue(entry['checked_against_image'])
        self.assertEqual(entry['review']['verdict'], 'ok')

    def test_rerun_with_a_different_figure_needs_a_new_look(self):
        MJ.log_corroboration(report(), filename=self.log)
        entries, target = JR.load(self.log)
        JR.mark(entries, CASE, 'ok')
        JR.save(entries, target)
        MJ.log_corroboration(report(amount=1100.00), filename=self.log)
        entry = self.entries()[0]
        self.assertFalse(entry['checked_against_image'])
        self.assertNotIn('review', entry)

    def test_log_stays_inside_the_dealflow_folder(self):
        _, target = MJ.log_corroboration(report(), filename=self.log)
        self.assertTrue(str(target).startswith(os.path.realpath(_TMP)))


class VerdictTests(unittest.TestCase):
    def test_unknown_case_is_refused(self):
        with self.assertRaises(KeyError):
            JR.mark([{'case': 'x'}], 'y', 'ok')

    def test_verdict_must_be_ok_or_wrong(self):
        with self.assertRaises(ValueError):
            JR.mark([{'case': 'x'}], 'x', 'maybe')

    def test_count_below_threshold(self):
        entries = [{'case': str(i), 'review': {'verdict': 'ok'}} for i in range(3)]
        entries.append({'case': 'p'})
        s = JR.status(entries)
        self.assertEqual((s['ok'], s['pending'], s['wrong']), (3, 1, 0))
        self.assertIn('3 of %d' % MJ.REVIEW_THRESHOLD, s['line'])

    def test_threshold_met_is_reported_not_acted_on(self):
        entries = [{'case': str(i), 'review': {'verdict': 'ok'}}
                   for i in range(MJ.REVIEW_THRESHOLD)]
        self.assertIn('can now be revisited', JR.status(entries)['line'])

    def test_one_wrong_figure_outweighs_the_count(self):
        entries = [{'case': str(i), 'review': {'verdict': 'ok'}}
                   for i in range(MJ.REVIEW_THRESHOLD)]
        entries.append({'case': 'w', 'review': {'verdict': 'wrong'}})
        line = JR.status(entries)['line']
        self.assertIn('WRONG', line)
        self.assertNotIn('can now be revisited', line)


class EquityUntouchedTests(unittest.TestCase):
    def test_scan_figures_stay_refused_after_a_full_review(self):
        # The review is evidence for a later decision. It must not itself open the gate.
        log = 'review-equity.json'
        for i in range(MJ.REVIEW_THRESHOLD):
            MJ.log_corroboration(report(case='2026-%06d-CA-01' % i), filename=log)
        entries, target = JR.load(log)
        for entry in entries:
            JR.mark(entries, entry['case'], 'ok')
        JR.save(entries, target)
        for source in MJ.SCAN_SOURCES:
            self.assertIsNone(MJ.judgment_for_analyze(report(source=source)))


class SheetTests(unittest.TestCase):
    def test_sheet_puts_the_page_image_beside_the_figure(self):
        pdf = make_pdf()
        entries = [MJ.log_corroboration(report(pdf=pdf), filename='review-sheet.json') and
                   JR.load('review-sheet.json')[0][0]]
        out = os.path.join(_TMP, 'sheet')
        index = JR.build_sheet(entries, out)
        page = open(index, encoding='utf-8').read()
        self.assertIn('$1,000.00', page)
        self.assertIn('--mark %s --ok' % CASE, page)
        pngs = [n for n in os.listdir(out) if n.endswith('.png')]
        self.assertEqual(len(pngs), 1)
        self.assertIn('-p2.png', pngs[0])
        self.assertIn('src="%s"' % pngs[0], page)

    def test_missing_pdf_is_said_not_hidden(self):
        entries = [{'case': CASE, 'amount': 1000.0,
                    'figures': [dict(report()['documents'][0]['amount_candidates'][0],
                                     pdf=os.path.join(_TMP, 'gone.pdf'), images_dir=None)]}]
        page = open(JR.build_sheet(entries, os.path.join(_TMP, 'sheet2')), encoding='utf-8').read()
        self.assertIn('stored PDF is missing', page)

    def test_old_entry_without_figures_says_rerun(self):
        page = open(JR.build_sheet([{'case': CASE, 'amount': 5.0}],
                                   os.path.join(_TMP, 'sheet3')), encoding='utf-8').read()
        self.assertIn('Re-run the case', page)

    def test_page_text_is_escaped(self):
        entries = [{'case': CASE, 'amount': 1.0,
                    'figures': [{'amount': 1.0, 'page': 1, 'passage': '<script>x</script>'}]}]
        page = open(JR.build_sheet(entries, os.path.join(_TMP, 'sheet4')), encoding='utf-8').read()
        self.assertNotIn('<script>x', page)


class NightlyLogsTests(unittest.TestCase):
    def test_run_documents_logs_corroborations(self):
        # Before 2026-09-23 only the pilot CLI logged, so the nightly could never move the count.
        source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   'run_documents.py'), encoding='utf-8').read()
        self.assertIn('MJ.log_corroboration(report)', source)


if __name__ == '__main__':
    unittest.main(verbosity=1 if len(sys.argv) == 1 else 2)
