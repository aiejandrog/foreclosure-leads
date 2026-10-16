"""enrich(): a failed Miami appraiser read carries the last good read of the same folio.

The 09-30 evening board shipped 194 of 194 Miami auction leads with a folio and no market value.
Fake folios and numbers only; no network (the session is replaced)."""
import json, os, sys, tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foreclosure_leads as FL
import publish_guard as PG

fails = 0
def check(name, ok, extra=''):
    global fails
    print(('PASS ' if ok else 'FAIL ') + name + ((' ' + str(extra)) if extra and not ok else ''))
    fails += 0 if ok else 1

F1, F2, F3 = '0100000000001', '0100000000002', '0100000000003'
today = date.today()

class _Resp:
    def __init__(self, j): self._j = j
    def json(self):
        if isinstance(self._j, Exception): raise self._j
        return self._j

class _Sess:
    def __init__(self, by_folio): self.by = by_folio; self.headers = {}
    def get(self, url, params=None, timeout=None):
        return _Resp(self.by.get(params['folioNumber'], ValueError('Expecting value: line 1')))

GOOD = {'PropertyInfo': {'DORDescription': 'SINGLE FAMILY', 'BedroomCount': 3, 'BathroomCount': 2,
                         'BuildingHeatedArea': 1500, 'FolioNumber': '01-0000-000-0002'},
        'OwnerInfos': [{'Name': 'OWNER A'}], 'MailingAddress': {'Address1': '1 X ST', 'City': 'MIAMI'},
        'Assessment': {'AssessmentInfos': [{'TotalValue': 400000}]}, 'SalesInfos': [],
        'SiteAddress': [{'Address': '1 X ST', 'City': 'Miami', 'Zip': '33100'}], 'Benefit': {}}

def run(rows, by, prior):
    real_sess, real_sleep = FL.requests.Session, FL.time.sleep
    FL.requests.Session = lambda: _Sess(by)
    FL.time.sleep = lambda s: None
    try:
        return FL.enrich(rows, prior=prior)
    finally:
        FL.requests.Session, FL.time.sleep = real_sess, real_sleep

# prior file: F1 good recent, F3 good but too old
with tempfile.TemporaryDirectory() as td:
    pth = os.path.join(td, 'leads_final.json')
    json.dump([
        {'Folio': F1, 'enriched': True, 'enriched_on': (today - timedelta(days=2)).isoformat(),
         'market_value': 300000, 'dor_desc': 'CONDO', 'beds': 2, 'owners': 'OWNER P', 'mailing_address': 'M'},
        {'Folio': F3, 'enriched': True, 'enriched_on': (today - timedelta(days=60)).isoformat(),
         'market_value': 250000, 'dor_desc': 'CONDO'},
        {'Folio': F2, 'enriched': False, 'market_value': 0},
    ], open(pth, 'w'))
    prior = FL._prior_enrichment(pth)
    check('prior keeps only enriched rows with a value', set(prior) == {F1, F3}, sorted(prior))

    # undated old file: rows dated by the file's mtime
    pth2 = os.path.join(td, 'old.json')
    json.dump([{'Folio': F1, 'enriched': True, 'market_value': 1}], open(pth2, 'w'))
    old_t = (date.today() - timedelta(days=40))
    ts = __import__('time').mktime(old_t.timetuple())
    os.utime(pth2, (ts, ts))
    p2 = FL._prior_enrichment(pth2)
    check('undated row takes the file date', p2[F1]['enriched_on'] == old_t.isoformat(), p2)

rows = [{'Folio': F1, 'Case #': 'A'}, {'Folio': F2, 'Case #': 'B'}, {'Folio': F3, 'Case #': 'C'}]
out = run(rows, {F2: GOOD}, prior)
a, b, c = out
check('failed read carries the recent prior value', a.get('market_value') == 300000 and a.get('enrich_carried'), a)
check('carried row keeps its original read date', a.get('enriched_on') == (today - timedelta(days=2)).isoformat())
check('carried row is marked enriched', a.get('enriched') is True)
check('live read sets value and today', b.get('market_value') == 400000 and b.get('enriched_on') == today.isoformat()
      and not b.get('enrich_carried'), b)
check('a read older than the carry limit is not carried', not c.get('market_value') and c.get('enriched') is False, c)

# an empty response (no property, no value) is a failure, not an enriched row with blanks
out = run([{'Folio': F1, 'Case #': 'A'}], {F1: {'Message': 'x'}}, prior)
check('empty appraiser answer counts as failed and carries', out[0].get('enrich_carried') and out[0]['market_value'] == 300000, out[0])
out = run([{'Folio': F2, 'Case #': 'B'}], {F2: {'Message': 'x'}}, {})
check('empty answer with nothing to carry stays unenriched', out[0].get('enriched') is False and not out[0].get('market_value'), out[0])

out = run([{'Folio': F1, 'Case #': 'A'}], {F1: {'PropertyInfo': {'FolioNumber': None}}}, prior)
check('a shell PropertyInfo with no folio and no value is a failed read', out[0].get('enrich_carried'), out[0])
land = dict(GOOD, Assessment={'AssessmentInfos': [{'TotalValue': 0}]})
out = run([{'Folio': F2, 'Case #': 'B'}], {F2: land}, {})
check('a real parcel with a zero value is still read, not failed', out[0].get('enriched') is True and not out[0].get('enrich_carried'), out[0])

# a future-dated prior read is not trusted
fut = {F1: dict(prior[F1], enriched_on=(today + timedelta(days=3)).isoformat())}
out = run([{'Folio': F1, 'Case #': 'A'}], {}, fut)
check('a future-dated prior read is not carried', not out[0].get('market_value'), out[0])

# publish guard knows the new census field and does not block a live board without it
check('publish_guard watches mdval', 'mdval' in PG.FIELDS)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'foreclosure_leads.py'), encoding='utf-8').read()
check('census carries mdval', "'mdval':" in src)

print('\n%d failure(s)' % fails)
sys.exit(1 if fails else 0)
