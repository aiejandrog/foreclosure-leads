import unittest
from unittest.mock import patch
import miami_timeline_amounts as A


class AmountPageTest(unittest.TestCase):
    def test_only_pages_with_monetary_evidence_are_selected(self):
        reading = {'pages': [
            {'page':1,'text':'Hearing on 10/5/2026 at 9:00.'},
            {'page':2,'text':'Total $1,234.56'},
            {'page':3,'text':'Exhibit B'},
            {'page':4,'text':'','provisional_ocr_text':'Amount USD 99.00'},
            {'page':5,'text':'','outcome':'unreadable_source'}]}
        self.assertEqual(A.amount_page_numbers(reading), [2,4])

    def test_no_amounts_never_calls_the_paid_reader(self):
        with patch('document_vision.read_document', side_effect=AssertionError('Paid call forbidden')):
            result = A.assess_amount_pages({'path':'unused','reading':{'pages':[]}}, object())
        self.assertEqual(result['pages'], {})

    def test_supplemental_ocr_amount_selects_page(self):
        self.assertEqual(A.amount_page_numbers({'pages':[
            {'page':2,'text':'Embedded header','supplemental_ocr':{'text':'Amount $45.00'}}]}), [2])

    def test_reader_does_not_assume_all_documents_are_judgments(self):
        with patch('document_vision.VisionReader.read_page', return_value={}) as read:
            A.TimelineAmountReader().read_page(b'image', object())
        prompt = read.call_args.kwargs['instruction']
        self.assertIn('untrusted evidence', prompt)
        self.assertNotIn('awarded additive', prompt)
        self.assertIn('not necessarily a judgment', prompt)

    def test_all_pages_not_read_after_cap_have_named_gaps(self):
        row = {'path':'synthetic.pdf','source_ref':'court:1:1','reading':{'pages':[
            {'page':1,'text':'Amount $10.00'}, {'page':2,'text':'Costs $5.00'},
            {'page':3,'text':'Total $15.00'}]}}
        with patch('document_vision.read_document', return_value={
            'pages':{1:{'rows':[]}},'errors':{2:'budget exhausted'},'figures':[],
            'grand_totals':[], 'usd':0}) as external:
            result = A.assess_amount_pages(row, object())
        self.assertEqual([g['page'] for g in result['gaps']], [2,3])
        self.assertEqual(external.call_args.args[1], [1,2,3])


if __name__ == '__main__':
    unittest.main()
