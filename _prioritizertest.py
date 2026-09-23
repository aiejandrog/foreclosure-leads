import unittest
from document_prioritizer import prioritize


CASE='2099-000001-CA-01'
def inventory(rows):
    return {'raw':{'caseNumber':CASE,'dockets':rows},
            'entries':[{'source_id':str(r['eventID']),'metadata':r,'expected_documents':1} for r in rows],
            'pagination_verified':False}
def row(i,text,date):
    return {'eventID':i,'docketDescrition':text,'eventDate':date,'comments':''}

class PrioritizerTests(unittest.TestCase):
    def test_service_of_judgment_is_not_itself_a_judgment(self):
        value=row(1,'Certificate of Service','01/01/2099')
        value['comments']='Final Judgment'
        plan=prioritize(CASE,inventory([value]),'2099-01-03')
        self.assertNotEqual(plan['documents'][0]['kind'],'final_judgment')

    def test_stay_before_judgments_and_recent_before_historical(self):
        value=prioritize(CASE,inventory([row(1,'Final Judgment','01/01/2098'),
            row(2,'Amended Final Judgment','01/01/2099'),row(3,'Suggestion of Bankruptcy','01/02/2099')]),'2099-01-03')
        self.assertEqual([x['entry_id'] for x in value['documents']],['3','2','1'])
        self.assertFalse(value['controlling_judgment_established'])
        self.assertFalse(value['coverage_complete'])

    def test_partial_party_list_does_not_exclude_judgment(self):
        source=inventory([row(1,'Final Judgment','01/01/2099')])
        source['raw']['parties']=[]
        self.assertEqual(prioritize(CASE,source,'2099-01-03')['documents'][0]['kind'],'final_judgment')

    def test_wrong_case_and_truncated_cache_rejected(self):
        source=inventory([row(1,'Final Judgment','01/01/2099')])
        with self.assertRaises(ValueError): prioritize('2099-000002-CA-01',source,'2099-01-03')
        source['entries']=[]
        with self.assertRaises(ValueError): prioritize(CASE,source,'2099-01-03')

    def test_future_unknown_dates_and_no_image_are_gaps_not_paid_targets(self):
        source=inventory([row(1,'Final Judgment','01/04/2099'),row(2,'Final Judgment','bad'),row(3,'Order Vacating Judgment','01/01/2099')])
        source['entries'][2]['expected_documents']=0
        value=prioritize(CASE,source,'2099-01-03')
        self.assertTrue(all(not x['eligible_for_acquisition'] for x in value['documents']))
        self.assertEqual(len(value['gaps']),4)

    def test_duplicate_ids_cannot_silently_overwrite_evidence(self):
        with self.assertRaises(ValueError): prioritize(CASE,inventory([row(1,'Order','01/01/2099'),row(1,'Other','01/02/2099')]),'2099-01-03')

if __name__=='__main__': unittest.main()
