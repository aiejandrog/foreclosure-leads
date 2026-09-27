"""Placing an owner search on the parcel by the Property Appraiser's deed book/page (2026-09-27).

Every folio, book/page and name below is invented. The shape is the one measured on 28 real
Miami-Dade owner searches that night: the owner's own deed and mortgage come back with a blank
folio field, so nothing reached the parcel and the chain was worthless, although the appraiser
lists that very deed's book/page for the folio.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tempfile as _tf
os.environ['DEALFLOW_PAID_LEDGER'] = os.path.join(_tf.mkdtemp(prefix='paidreads_'), 'paid_reads_ledger.json')

import equity_state as ES
import records_liens as RL

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


FOLIO = '0100000000077'
PLAINTIFF = 'SYNTHETIC SAVINGS BANK NA'


def rec(doc, date, bk, pg, amt=0, second='OWNER TESTER', first='OWNER TESTER', folio='', sub='TEST GARDENS', **kw):
    r = {'doC_TYPE': doc, 'reC_DATE': date, 'reC_BOOK': bk, 'reC_PAGE': pg,
         'reC_BOOKPAGE': '%s/%s' % (bk, pg), 'consideratioN_1': amt, 'intangible': 0,
         'seconD_PARTY': second, 'firsT_PARTY': first, 'foliO_NUMBER': folio, 'subdiV_NAME': sub}
    r.update(kw)
    return r


deed = rec('DEED', '2/1/2008', '26100', '0010', 0, 'OWNER TESTER', first='PRIOR SELLER')
mtg = rec('MORTGAGE', '2/1/2008', '26100', '11', 0, PLAINTIFF, intangible=700)       # $350,000 face
mtg2 = rec('MORTGAGE', '3/3/2015', '29500', '40', 0, 'OTHER LENDER', intangible=100)
elsewhere_mtg = rec('MORTGAGE', '4/4/2016', '30000', '5', 0, 'THIRD LENDER', intangible=200, sub='ELSEWHERE ESTATES')
models = [deed, mtg, mtg2, elsewhere_mtg]
DEEDS = {('26100', '10')}                      # the appraiser's book/page, leading zeros stripped

before = RL.analyze(models, FOLIO, 300000, ftype='MORTGAGE', plaintiff=PLAINTIFF)
check('without the appraiser: never reaches the parcel', before['parcel_found'] is False)
check('without the appraiser: no mortgage counted', before['open_count'] == 0, before['open_count'])
check('without the appraiser: never a documented clear', not ES.coverage_documented(before))

after = RL.analyze(models, FOLIO, 300000, ftype='MORTGAGE', plaintiff=PLAINTIFF, deed_bps=DEEDS)
check('the deed at the appraiser book/page places the search', after['parcel_found'] is True)
check('placement is labelled', after['placed_by'].startswith('appraiser deed'), after.get('placed_by'))
check('its subdivision anchors the parcel', after['subdiv'] == 'TEST GARDENS', after['subdiv'])
check('both mortgages in that subdivision are counted', after['open_count'] == 2, after['open_count'])
check('a mortgage in another subdivision is not', all('THIRD' not in (l.get('party') or '') for l in after['liens']))
check('the caller\'s rows are not mutated', deed['foliO_NUMBER'] == '')

# a row the index files under a DIFFERENT folio is another parcel, even at a listed book/page
other = rec('DEED', '2/1/2008', '26100', '10', 0, folio='0100000000999')
r2 = RL.analyze([other, mtg], FOLIO, 300000, ftype='MORTGAGE', deed_bps=DEEDS)
check('a row carrying another folio is never placed', r2['parcel_found'] is False and not r2['placed_by'])

# a placed deed with no subdivision ties nothing else: not placed, so an empty chain is never CLEAR
bare = rec('DEED', '2/1/2008', '26100', '10', 0, sub='')
r3 = RL.analyze([bare, rec('MORTGAGE', '2/1/2008', '26100', '11', 0, PLAINTIFF, intangible=700, sub='')],
                FOLIO, 300000, ftype='MORTGAGE', deed_bps=DEEDS)
check('a deed with no subdivision does not place the search', r3['parcel_found'] is False and not r3['placed_by'])
check('...and cannot read as a documented clear', not ES.coverage_documented(r3))

# no appraiser deed on the search: nothing changes
r4 = RL.analyze(models, FOLIO, 300000, ftype='MORTGAGE', deed_bps={('99999', '1')})
check('an appraiser book/page not on the search places nothing', r4['parcel_found'] is False and r4['open_count'] == 0)

# a search that already carries the folio is analysed exactly as before
withf = [dict(deed, foliO_NUMBER=FOLIO), mtg, mtg2]
a = RL.analyze(withf, FOLIO, 300000, ftype='MORTGAGE')
b = RL.analyze(withf, FOLIO, 300000, ftype='MORTGAGE', deed_bps=DEEDS)
check('a folio-carrying search is unchanged by the appraiser', a == b)

check('_bp_key strips leading zeros', RL._bp_key('026100', '0010') == ('26100', '10'))
check('_bp_key refuses a half', RL._bp_key('26100', '') is None)

# pa_deed_bookpages reads OfficialRecordBook/Page, not EncodedRecordBookAndPage, and fails empty
class _R:
    def __init__(self, j): self._j = j
    def json(self): return self._j
_orig = RL.requests.get
RL._PA_DEEDS.clear()
RL.requests.get = lambda *a, **k: _R({'SalesInfos': [
    {'EncodedRecordBookAndPage': 'X1ABCDEF%2FGHIJ', 'OfficialRecordBook': '26100', 'OfficialRecordPage': '0010'},
    {'OfficialRecordBook': '', 'OfficialRecordPage': ''}]})
check('pa_deed_bookpages reads the official record fields', RL.pa_deed_bookpages('01-0000-000-0077') == {('26100', '10')})
def _boom(*a, **k): raise RuntimeError('offline')
RL._PA_DEEDS.clear(); RL.requests.get = _boom
check('an unreadable appraiser is an empty set', RL.pa_deed_bookpages(FOLIO) == set())
RL.requests.get = _orig

print('\n%d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
