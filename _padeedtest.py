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
# the owner's other house: its own deed is on the search, so its loan is plainly not this parcel's
elsewhere_deed = rec('DEED', '4/4/2016', '30000', '4', 0, 'OWNER TESTER', first='A SELLER', sub='ELSEWHERE ESTATES')
models = [deed, mtg, mtg2, elsewhere_deed, elsewhere_mtg]
DEEDS = {('26100', '10')}                      # the appraiser's book/page, leading zeros stripped

before = RL.analyze(models, FOLIO, 300000, ftype='MORTGAGE', plaintiff=PLAINTIFF)
check('without the appraiser: never reaches the parcel', before['parcel_found'] is False)
check('without the appraiser: no mortgage counted', before['open_count'] == 0, before['open_count'])
check('without the appraiser: never a documented clear', not ES.coverage_documented(before))

after = RL.analyze(models, FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', plaintiff=PLAINTIFF, deed_bps=DEEDS)
check('the deed at the appraiser book/page places the search', after['parcel_found'] is True)
check('placement is labelled', after['placed_by'].startswith('appraiser deed'), after.get('placed_by'))
check('its subdivision anchors the parcel', after['subdiv'] == 'TEST GARDENS', after['subdiv'])
check('both mortgages in that subdivision are counted', after['open_count'] == 2, after['open_count'])
check('a mortgage in another subdivision is not', all('THIRD' not in (l.get('party') or '') for l in after['liens']))

# third review, finding 4: an owner loan in a subdivision the owner holds no deed in may be this
# parcel's under a variant subdivision name ('TEST GARDENS SEC 1'), so it cannot be ruled out
variant = rec('MORTGAGE', '4/4/2016', '30000', '5', 0, 'THIRD LENDER', intangible=200, sub='TEST GARDENS SEC 1')
r12 = RL.analyze([deed, mtg, variant], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
check('an owner loan in a subdivision with no owner deed refuses placement', r12['parcel_found'] is False,
      r12.get('placement_refused'))
# finding 3: a same-surname stranger's loan in the subdivision would be counted as this parcel's
namesake = rec('MORTGAGE', '5/5/2017', '30500', '9', 0, 'NAMESAKE BANK', first='TESTER MARIA', intangible=200)
r13 = RL.analyze([deed, mtg, namesake], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
check('a namesake loan in the subdivision refuses placement', r13['parcel_found'] is False and r13['open_count'] == 0,
      (r13['open_count'], r13.get('placement_refused')))
# finding 1: the city files code liens as certified orders and notices
for doc in ('CERTIFIED COPY OF ORDER', 'NOTICE - NOT'):
    cl = rec(doc, '5/5/2020', '31000', '7', 1200, 'OWNER TESTER', first='CITY OF MIAMI', sub='')
    rc = RL.analyze([deed, cl], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
    check('a loose %s naming the owner refuses placement' % doc.lower(),
          rc['parcel_found'] is False and not ES.coverage_documented(rc), rc.get('placement_refused'))
# finding 2: a spouse named only on the deed's grantee side is household, as analyze() reads it
hdeed = rec('DEED', '2/1/2008', '26100', '10', 0, 'TESTER OWNER & HELEN', first='PRIOR SELLER')
hmtg = rec('MORTGAGE', '2/1/2012', '27500', '2', 0, 'HELEN BANK', first='TESTER HELEN', intangible=300, sub='')
r14 = RL.analyze([hdeed, hmtg], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
check('a deed-named spouse loose mortgage refuses placement', r14['parcel_found'] is False and not ES.coverage_documented(r14),
      r14.get('placement_refused'))
check('the caller\'s rows are not mutated', deed['foliO_NUMBER'] == '')

# a row the index files under a DIFFERENT folio is another parcel, even at a listed book/page
other = rec('DEED', '2/1/2008', '26100', '10', 0, folio='0100000000999')
r2 = RL.analyze([other, mtg], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
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
r6 = RL.analyze([twin_other, twin_blank, nm], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
check('a book/page indexed under another folio is not placed', r6['parcel_found'] is False and r6['open_count'] == 0,
      (r6['parcel_found'], r6['open_count'], r6.get('placement_refused')))
# ...and placed rows that disagree on subdivision anchor nothing
wrong = rec('DEED', '2/1/2008', '26100', '10', 0, sub='WRONG PLAT')
r7 = RL.analyze([wrong, deed, mtg], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
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

# round 4, finding 1: this parcel's quit-claim (owner to owner and spouse) and its refi, both indexed under a
# variant subdivision, must not make that subdivision "another property" and drop the refi
pm = rec('MORTGAGE', '2/1/2008', '26100', '11', 0, 'FIRST BANK NA', intangible=700)
sat = rec('SATISFACTION', '5/5/2016', '30010', '3', 0, 'FIRST BANK NA', oriG_REC_BOOK='26100', oriG_REC_PAGE='11')
qcd = rec('QUIT CLAIM DEED', '4/4/2016', '30000', '19', 0, 'OWNER TESTER & HELEN', first='OWNER TESTER', sub='TEST GARDENS SEC 1')
vref = rec('MORTGAGE', '4/4/2016', '30000', '20', 0, 'REFI LENDER LLC', intangible=500, sub='TEST GARDENS SEC 1')
rq = RL.analyze([deed, pm, sat, qcd, vref], FOLIO, 20000, ftype='HOA', owner='OWNER TESTER', deed_bps=DEEDS)
check('an owner-to-owner quit-claim does not make a variant subdivision another property',
      rq['parcel_found'] is False and not ES.coverage_documented(rq), rq.get('placement_refused'))
# round 4, finding 2: a same-surname stranger's claim of lien or city lien in the subdivision
for doc, lienor, first in (('CLAIM OF LIEN', 'OWNER TESTER', 'TEST GARDENS HOMEOWNERS ASSOCIATION INC'),
                           ('LIEN', 'CITY OF MIAMI', 'TESTER MARIA')):
    nl = rec(doc, '5/5/2020', '32000', '7', 9000, 'TESTER MARIA' if lienor == 'OWNER TESTER' else lienor,
             first=first if lienor != 'OWNER TESTER' else first)
    nl['seconD_PARTY'], nl['firsT_PARTY'] = (first, 'TESTER MARIA') if doc == 'CLAIM OF LIEN' else ('CITY OF MIAMI', 'TESTER MARIA')
    rn = RL.analyze([deed, pm, sat, nl], FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
    check('a namesake %s in the subdivision refuses placement' % doc.lower(),
          rn['parcel_found'] is False and not rn.get('hoa_open') and not rn.get('code_open'), rn.get('placement_refused'))
# round 4, finding 4: no usable owner name means no namesake check, so no placement
nm = rec('MORTGAGE', '5/5/2017', '30500', '9', 0, 'NAMESAKE BANK', first='TESTER MARIA', intangible=200)
r0 = RL.analyze([deed, pm, sat, nm], FOLIO, 300000, ftype='MORTGAGE', owner='', deed_bps=DEEDS)
check('no owner name refuses placement', r0['parcel_found'] is False, r0.get('placement_refused'))
# the ordinary household still places: deed, paid-off purchase loan, a refi, and another house elsewhere
refi = rec('MORTGAGE', '4/4/2016', '30000', '20', 0, 'REFI LENDER LLC', intangible=500)
for ft in ('MORTGAGE', 'HOA'):
    rp = RL.analyze([deed, pm, sat, refi, elsewhere_deed, elsewhere_mtg], FOLIO, 300000, ftype=ft,
                    owner='OWNER TESTER', deed_bps=DEEDS)
    check('an ordinary household still places (%s lead)' % ft, rp['parcel_found'] is True and rp['open_count'] == 1,
          (rp.get('placement_refused'), rp.get('open_count')))

# round 4, finding 3: an HOA lead placed by the deed, with a lender's lis pendens naming the owner that
# carries no folio (no subdivision, or a variant one), is never a documented clear
import datetime as _dt
_lpd = (_dt.date.today() - _dt.timedelta(days=200)).strftime('%m/%d/%Y')
for _sub in ('', 'TEST GARDENS SEC 1'):
    lp = rec('LIS PENDENS', _lpd, '34000', '5', 0, 'OWNER TESTER', first='BIG BANK NA', sub=_sub)
    rh = RL.analyze([deed, pm, sat, lp], FOLIO, 20000, ftype='HOA', owner='OWNER TESTER', deed_bps=DEEDS)
    check('HOA lead: a loose lender lis pendens (sub %r) blocks a documented clear' % _sub,
          not ES.coverage_documented(rh) and rh.get('second_fc_unsure'), (rh.get('parcel_found'), rh.get('second_fc_unsure')))
lpr = rec('LIS PENDENS RELEASE', _lpd, '34100', '1', 0, 'OWNER TESTER', first='BIG BANK NA', sub='',
          oriG_REC_BOOK='34000', oriG_REC_PAGE='5')
rh = RL.analyze([deed, pm, sat, rec('LIS PENDENS', _lpd, '34000', '5', 0, 'OWNER TESTER', first='BIG BANK NA', sub=''), lpr],
                FOLIO, 20000, ftype='HOA', owner='OWNER TESTER', deed_bps=DEEDS)
check('HOA lead: a released loose lis pendens does not block', not rh.get('second_fc_unsure'), rh.get('second_fc_unsure'))

# round 5: a double surname on the deed ('GARCIA LOPEZ JOSE') is not a household given name, so a stranger
# ANA GARCIA LOPEZ's loose mortgage in the subdivision still refuses placement
gd = rec('DEED', '2/1/2008', '26100', '0010', 0, 'GARCIA LOPEZ JOSE', first='PRIOR SELLER')
gm = rec('MORTGAGE', '2/1/2008', '26100', '11', 0, 'LENDER BANK', first='GARCIA LOPEZ JOSE', intangible=700)
ana = rec('MORTGAGE', '6/6/2019', '31500', '3', 0, 'OTHER BANK', first='GARCIA LOPEZ ANA', intangible=300)
rg = RL.analyze([gd, gm, ana], FOLIO, 300000, ftype='MORTGAGE', owner='JOSE GARCIA', deed_bps=DEEDS)
check('a double-surname stranger\'s mortgage in the subdivision refuses placement', rg['parcel_found'] is False,
      (rg.get('placement_refused'), rg.get('open_count')))
# round 5: a namesake's lender lis pendens in the subdivision refuses placement (it would flag 2ND FORECLOSURE)
nlp = rec('LIS PENDENS', _lpd, '34000', '9', 0, 'GARCIA ROBERTO', first='BIG BANK NA')
rg = RL.analyze([gd, gm, nlp], FOLIO, 20000, ftype='HOA', owner='JOSE GARCIA', deed_bps=DEEDS)
check('a namesake lender lis pendens in the subdivision refuses placement', rg['parcel_found'] is False and not rg.get('second_fc'),
      (rg.get('placement_refused'), rg.get('second_fc')))
# ... but a namesake's lis pendens elsewhere in the county does not block a placed HOA lead's clear
nlp2 = dict(nlp, subdiV_NAME='')
rg = RL.analyze([gd, gm, nlp2], FOLIO, 20000, ftype='HOA', owner='JOSE GARCIA', deed_bps=DEEDS)
check('a namesake lis pendens with no subdivision does not mark an HOA lead unsure', rg['parcel_found'] is True
      and not rg.get('second_fc_unsure'), (rg.get('placement_refused'), rg.get('second_fc_unsure')))
# round 5: an owner who sold an earlier home to an outsider still places (that subdivision is another property)
sold = rec('WARRANTY DEED', '5/5/2006', '24000', '1', 0, 'BUYER PERSON', first='GARCIA JOSE', sub='OLD PLACE')
oldm = rec('MORTGAGE', '5/5/2003', '21000', '2', 0, 'OLD BANK', first='GARCIA JOSE', intangible=200, sub='OLD PLACE')
rg = RL.analyze([gd, gm, sold, oldm], FOLIO, 300000, ftype='MORTGAGE', owner='JOSE GARCIA', deed_bps=DEEDS)
check('an owner who sold an earlier home to an outsider still places', rg['parcel_found'] is True, rg.get('placement_refused'))

# the repull gate asks the appraiser only when no row carries the folio
_asked = []
check('_parcel_in does not call the appraiser for a folio-carrying search',
      RL._parcel_in([dict(deed, foliO_NUMBER=FOLIO)], FOLIO, lambda: _asked.append(1) or DEEDS) is True and not _asked)
check('_parcel_in calls it lazily otherwise', RL._parcel_in(models, FOLIO, lambda: _asked.append(1) or DEEDS, 'OWNER TESTER') is True
      and len(_asked) == 1)

# finding 5: --repull's gate knows placement, so it does not pay for a search placement answers
check('_parcel_in sees a placeable search', RL._parcel_in(models, FOLIO, DEEDS, 'OWNER TESTER') is True)
check('_parcel_in without the appraiser is unchanged', RL._parcel_in(models, FOLIO) is False)
check('_parcel_in refuses what placement refuses', RL._parcel_in([deed, loose], FOLIO, DEEDS, 'OWNER TESTER') is False)

# finding 7: a float book/page is the same number
check('_bp_key reads a whole float as an integer', RL._bp_key(26100.0, 10.0) == ('26100', '10'))

# no appraiser deed on the search: nothing changes
r4 = RL.analyze(models, FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps={('99999', '1')})
check('an appraiser book/page not on the search places nothing', r4['parcel_found'] is False and r4['open_count'] == 0)

# a search that already carries the folio is analysed exactly as before
withf = [dict(deed, foliO_NUMBER=FOLIO), mtg, mtg2]
a = RL.analyze(withf, FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER')
b = RL.analyze(withf, FOLIO, 300000, ftype='MORTGAGE', owner='OWNER TESTER', deed_bps=DEEDS)
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
