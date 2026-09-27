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

# review 2026-09-27, finding 1: the owner's mortgage with neither folio nor subdivision cannot be tied
# to this parcel or ruled out, so placement would read an incomplete chain as CLEAR
loose = rec('MORTGAGE', '2/1/2008', '26100', '11', 0, PLAINTIFF, first='OWNER TESTER', intangible=700, sub='')
r5 = RL.analyze([deed, loose], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
check('an unanchorable owner mortgage refuses placement', r5['parcel_found'] is False and not r5['placed_by'],
      r5.get('placement_refused'))
check('...and says why', 'no folio and no subdivision' in r5['placement_refused'])
check('...and is never a documented clear', not ES.coverage_documented(r5))
stranger = rec('MORTGAGE', '2/1/2008', '26200', '11', 0, PLAINTIFF, first='SOMEONE ELSE', intangible=700, sub='')
r5b = RL.analyze([deed, mtg, stranger], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
check('a loose mortgage naming someone else does not block placement', r5b['parcel_found'] is True)

# finding 2: an instrument also indexed under another folio covers another parcel
twin_other = rec('DEED', '2/1/2008', '26100', '10', 0, folio='0100000000999', sub='OTHER PLAT')
twin_blank = rec('DEED', '2/1/2008', '26100', '10', 0, sub='OTHER PLAT')
nm = rec('MORTGAGE', '3/3/2015', '29500', '41', 0, 'NAMESAKE BANK', intangible=100, sub='OTHER PLAT')
r6 = RL.analyze([twin_other, twin_blank, nm], FOLIO, 300000, ftype='MORTGAGE', deed_bps=DEEDS)
check('a book/page indexed under another folio is not placed', r6['parcel_found'] is False and r6['open_count'] == 0,
      (r6['parcel_found'], r6['open_count'], r6.get('placement_refused')))
# ...and placed rows that disagree on subdivision anchor nothing
wrong = rec('DEED', '2/1/2008', '26100', '10', 0, sub='WRONG PLAT')
r7 = RL.analyze([wrong, deed, mtg], FOLIO, 300000, ftype='MORTGAGE', deed_bps=DEEDS)
check('deed rows naming two subdivisions are not placed', r7['parcel_found'] is False and not r7['placed_by'],
      r7.get('placement_refused'))

# finding 3: the owner's other unit in the same condominium is not this parcel's
unit = rec('DEED', '2/1/2008', '26100', '10', 0, 'OWNER TESTER', first='PRIOR SELLER', sub='BAY CONDO')
unit2 = rec('DEED', '6/6/2012', '28000', '3', 0, 'OWNER TESTER', first='ANOTHER SELLER', sub='BAY CONDO')
unit2_mtg = rec('MORTGAGE', '6/6/2012', '28000', '4', 0, 'UNIT TWO BANK', intangible=300, sub='BAY CONDO')
r8 = RL.analyze([unit, unit2, unit2_mtg], FOLIO, 300000, ftype='HOA', owner='OWNER TESTER', deed_bps=DEEDS)
check('another owner deed in the subdivision refuses placement', r8['parcel_found'] is False and not r8['placed_by'],
      r8.get('placement_refused'))

# second review, finding A: the owner's other unit counts even when its deed carries its own folio,
# or came by certificate of title
unit2f = rec('DEED', '6/6/2012', '28000', '3', 0, 'OWNER TESTER', first='ANOTHER SELLER', sub='BAY CONDO',
             folio='0100000000555')
r9 = RL.analyze([unit, unit2f, unit2_mtg], FOLIO, 300000, ftype='HOA', owner='OWNER TESTER', deed_bps=DEEDS)
check('another unit deed carrying its own folio refuses placement', r9['parcel_found'] is False and r9['open_count'] == 0,
      (r9['open_count'], r9.get('placement_refused')))
unit2c = rec('CERTIFICATE OF TITLE', '6/6/2012', '28000', '3', 0, 'OWNER TESTER', first='CLERK OF COURT', sub='BAY CONDO')
r10 = RL.analyze([unit, unit2c, unit2_mtg], FOLIO, 300000, ftype='HOA', owner='OWNER TESTER', deed_bps=DEEDS)
check('another unit bought by certificate of title refuses placement', r10['parcel_found'] is False,
      r10.get('placement_refused'))

# finding B: a co-owner's loose mortgage is the household's, as analyze() reads co_owners
spouse = rec('MORTGAGE', '2/1/2010', '27000', '5', 0, 'SPOUSE BANK', first='TESTER MARIA', intangible=400, sub='')
r11 = RL.analyze([deed, spouse], FOLIO, 300000, ftype='MORTGAGE', owner='JOSE TESTER',
                 co_owners=(('TESTER', 'MARIA'),), deed_bps=DEEDS)
check('a co-owner loose mortgage refuses placement', r11['parcel_found'] is False and not ES.coverage_documented(r11),
      r11.get('placement_refused'))
check('_parcel_in reads co-owners too',
      RL._parcel_in([deed, spouse], FOLIO, DEEDS, 'JOSE TESTER', (('TESTER', 'MARIA'),)) is False)

# finding C: a loose association or code lien naming the owner would be dropped as another property
for doc, lienor in (('CLAIM OF LIEN', 'BAY CONDOMINIUM ASSOCIATION INC'), ('LIEN', 'CITY OF MIAMI')):
    ln = rec(doc, '5/5/2020', '31000', '7', 1200, 'OWNER TESTER', first=lienor, sub='')
    rl = RL.analyze([deed, ln], FOLIO, 300000, ftype='HOA', owner='OWNER TESTER', deed_bps=DEEDS)
    check('a loose %s naming the owner refuses placement' % doc.lower(),
          rl['parcel_found'] is False and not ES.coverage_documented(rl), rl.get('placement_refused'))
rel = rec('RELEASE OF LIEN', '5/5/2021', '31500', '8', 0, 'OWNER TESTER', first='CITY OF MIAMI', sub='')
rr = RL.analyze([deed, mtg, rel], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
check('a loose release does not refuse placement', rr['parcel_found'] is True, rr.get('placement_refused'))

# the repull gate asks the appraiser only when no row carries the folio
_asked = []
check('_parcel_in does not call the appraiser for a folio-carrying search',
      RL._parcel_in([dict(deed, foliO_NUMBER=FOLIO)], FOLIO, lambda: _asked.append(1) or DEEDS) is True and not _asked)
check('_parcel_in calls it lazily otherwise', RL._parcel_in(models, FOLIO, lambda: _asked.append(1) or DEEDS) is True
      and len(_asked) == 1)

# finding 5: --repull's gate knows placement, so it does not pay for a search placement answers
check('_parcel_in sees a placeable search', RL._parcel_in(models, FOLIO, DEEDS) is True)
check('_parcel_in without the appraiser is unchanged', RL._parcel_in(models, FOLIO) is False)
check('_parcel_in refuses what placement refuses', RL._parcel_in([deed, loose], FOLIO, DEEDS, 'OWNER TESTER') is False)

# finding 7: a float book/page is the same number
check('_bp_key reads a whole float as an integer', RL._bp_key(26100.0, 10.0) == ('26100', '10'))

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
check('...and is not cached as "no deeds"', FOLIO.lstrip('0') not in RL._PA_DEEDS and FOLIO not in RL._PA_DEEDS)
# finding 4: three failures in a row stop asking for the rest of the run
_calls = []
def _count(*a, **k):
    _calls.append(1); raise RuntimeError('offline')
RL.requests.get = _count; RL._PA_FAILS.update(n=0, off=False)
for i in range(6):
    RL.pa_deed_bookpages('01000000001%02d' % i)
check('a dead appraiser is asked three times, not once per lead', len(_calls) == 3, len(_calls))
RL._PA_FAILS.update(n=0, off=False)
RL.requests.get = _orig

print('\n%d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
