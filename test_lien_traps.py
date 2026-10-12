"""test_lien_traps -- the ways a Miami lien search can look complete and is not. Offline.

Run:  python test_lien_traps.py   (exit 0 = safe; no network, invented records only)

Every record here is made up: folios 0130000000001/2, a subdivision called TEST GARDENS SUB, owner
OWNER TESTA, lenders ALPHA / BETA / GAMMA. The fixture follows the design review Alex pasted on
2026-10-12: a capped surname search cannot prove a parcel has no other liens, subdivision is never
enough to place a loan, an assignment is not a new loan, a modification is not a second one, a
partial release for another parcel does not release this one, and an error-shaped empty answer is
not an empty search.

WHAT THIS PINS
  1. lien_coverage.coverage() never calls a chain fact_supported when any of those traps applies.
  2. The safe parts of records_liens.analyze(): a full page is marked capped, an empty answer is
     never a FACT, a search without the subject folio is not parcel-found, an assignment is not a
     loan, a satisfaction that names the loan's book/page releases it.

KNOWN analyze() BEHAVIOUR, printed as 'note' and not asserted (equity is frozen until the evidence
is reviewed with Alex, so these are reported, not changed):
  * a lender's partial release of ANOTHER parcel marks this parcel's loan SATISFIED ('lender chain')
  * a modification that carries an amount is counted as a second open loan
  * a namesake's mortgage in the same subdivision is counted as this owner's debt
  * a priced chain on a capped (500+) search is still 'priced'
"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import equity_state as ES  # noqa: E402
import lien_coverage as LC  # noqa: E402
import records_liens as R  # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    if len(extra) > 200:
        extra = extra[:200] + ' ...'
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra) if extra else ''))
    if not ok:
        fails.append(name)


def note(text):
    print('note ' + text)


FOL, OTHER, SD = '0130000000001', '0130000000002', 'TEST GARDENS SUB'


def r(doc, d, folio='', sd=SD, b='', p='', amt=0, p1='OWNER TESTA', p2='', ob='', op=''):
    return {'doC_TYPE': doc, 'reC_DATE': d, 'foliO_NUMBER': folio, 'subdiV_NAME': sd, 'reC_BOOK': b,
            'reC_PAGE': p, 'reC_BOOKPAGE': '%s/%s' % (b, p), 'consideratioN_1': amt, 'intangible': 0,
            'firsT_PARTY': p1, 'seconD_PARTY': p2, 'oriG_REC_BOOK': ob, 'oriG_REC_PAGE': op}


def deed():
    return r('DEED - DEE', '01/02/2015', FOL, b='29000', p='100', p2='OWNER TESTA')


def loan():
    return r('MORTGAGE - MOR', '01/02/2015', '', b='29000', p='101', amt=200000, p2='ALPHA BANK NA')


def run(models, judgment=150000):
    c = R.analyze(models, FOL, judgment)
    return c, LC.coverage(c), ES.state_of(c)


def mtg(c):
    return [(l['doc'], l['st'], l['amt'], l.get('sat_by')) for l in c['liens']]


def filler(n):
    # other people's recordings that a common surname returns, each on its own parcel
    return [r('DEED - DEE', '01/01/2010', '0130000009%03d' % i, sd='OTHER SUB %d' % i, b='1', p=str(i))
            for i in range(n)]


# ---------------------------------------------------------------- 0. the baseline
print('BASELINE')
c, cv, st = run([deed(), loan()])
rec('one deed + one loan is a complete, folio-anchored search', cv['search'] == 'complete'
    and cv['anchor'] == 'folio', cv)
rec('the loan row records how it was placed (subdivision: it carries no folio)',
    c['liens'][0].get('anchor') == 'subdivision', c['liens'][0])
rec('...so even the clean case is not fact_supported: subdivision is not parcel proof',
    not cv['fact_supported'] and 'subdivision_only_mortgage' in cv['gaps'], cv)
folio_loan = loan()
folio_loan['foliO_NUMBER'] = FOL
c, cv, st = run([deed(), folio_loan])
rec('a loan indexed to the subject folio is folio-anchored and fact_supported',
    c['liens'][0].get('anchor') == 'folio' and cv['fact_supported'] and st == 'priced', (cv, st))

# ---------------------------------------------------------------- 1. 501 records in one day
print('CAP')
c, cv, st = run([deed(), folio_loan] + filler(499))
rec('501 records is marked capped', c['capped'] is True and c['nrec'] == 501, c['nrec'])
rec('a capped search is never fact_supported, even folio-anchored', cv['search'] == 'capped'
    and not cv['fact_supported'], cv)
if st in ES.FACT:
    note('analyze()+state_of() still call a capped search %r (the 500-record page can hide a loan)' % st)
same_day = [r('DEED - DEE', '01/02/2015', '0130000008%03d' % i, sd='OTHER SUB', b='2', p=str(i))
            for i in range(500)] + [loan()]
c, cv, st = run(same_day)
rec('501 same-day records with the subject deed cut off: not parcel-found', c['parcel_found'] is False,
    c['parcel_found'])
rec('...the loan is not placed on a parcel the search never found', not c['liens'], mtg(c))
rec('...and nothing reads as a FACT', st not in ES.FACT and not cv['fact_supported'], (st, cv))

# ---------------------------------------------------------------- 2. namesake in the same subdivision
print('NAMESAKE')
namesake = r('MORTGAGE - MOR', '03/05/2019', '', b='31000', p='5', amt=90000, p2='BETA LENDING LLC')
c, cv, st = run([deed(), folio_loan, namesake])
rec('a same-subdivision mortgage with no folio is flagged subdivision_only_mortgage',
    'subdivision_only_mortgage' in cv['gaps'] and not cv['fact_supported'], cv)
if any(l['amt'] == 90000 and l['st'] == 'OPEN' for l in c['liens']):
    note('analyze() counts a same-subdivision namesake mortgage as open debt on this parcel')

# ---------------------------------------------------------------- 3. partial release for another parcel
print('PARTIAL RELEASE')
partial = r('PARTIAL RELEASE - PRL', '06/01/2016', OTHER, b='30000', p='7', p1='OWNER TESTA',
            p2='ALPHA BANK NA', ob='28000', op='9')
c, cv, st = run([deed(), folio_loan, partial])
rec('a release that names another book/page never counts as a book/page release',
    not any(l.get('sat_by') == 'book/page' for l in c['liens']), mtg(c))
rec('a loan released only by inference is flagged release_inferred',
    'release_inferred' in cv['gaps'] or all(l['st'] == 'OPEN' for l in c['liens']), cv)
rec('...and is never fact_supported', not cv['fact_supported'], cv)
if any(l['st'] == 'SATISFIED' for l in c['liens']):
    note("analyze() marks this parcel's loan SATISFIED from a lender's release of parcel %s "
         "(sat_by %r): debt understated" % (OTHER, c['liens'][0].get('sat_by')))
named = r('SATISFACTION - SAT', '06/01/2020', '', b='32000', p='1', p1='ALPHA BANK NA',
          p2='OWNER TESTA', ob='29000', op='101')
c, cv, st = run([deed(), folio_loan, named])
rec('a satisfaction that names the loan book/page releases it', mtg(c) == [
    ('MORTGAGE - MOR', 'SATISFIED', 200000, 'book/page')], mtg(c))
rec('...and is not an inferred release', 'release_inferred' not in cv['gaps'], cv)

# ---------------------------------------------------------------- 4. assignment and modification
print('ASSIGNMENT / MODIFICATION')
assign = r('ASSIGNMENT - ASG', '01/02/2017', '', b='30400', p='2', p1='ALPHA BANK NA', p2='GAMMA TRUST',
           amt=200000)
c, cv, st = run([deed(), folio_loan, assign])
rec('an assignment is not a second loan', [l['amt'] for l in c['liens']] == [200000], mtg(c))
rec('...and the chain stays fact_supported', cv['fact_supported'], cv)
modif = r('MORTGAGE MODIFICATION - MMO', '01/02/2018', FOL, b='30500', p='3', amt=180000, p2='ALPHA BANK NA')
c, cv, st = run([deed(), folio_loan, modif])
rec('an open modification row is flagged modification_as_loan', 'modification_as_loan' in cv['gaps']
    and not cv['fact_supported'], cv)
if sum(l['amt'] for l in c['liens'] if l['st'] == 'OPEN') > 200000:
    note('analyze() adds a priced modification as a second open loan: %s'
         % [l['amt'] for l in c['liens'] if l['st'] == 'OPEN'])
zero_mod = dict(modif, consideratioN_1=0)
c, cv, st = run([deed(), folio_loan, zero_mod])
rec('a $0 modification is not counted at all', [l['amt'] for l in c['liens']] == [200000]
    and not c['mtg_open_unpriced'], mtg(c))

# ---------------------------------------------------------------- 5. error-shaped empty answer
print('EMPTY')
c, cv, st = run([])
rec('an empty answer is search=empty', cv['search'] == 'empty', cv)
rec('...is never a FACT and never fact_supported', st not in ES.FACT and not cv['fact_supported'], (st, cv))
rec('...and is never clear even with a folio', st != 'clear', st)
for shape in (None, 'error', [], {'error': 'timeout'}, {'liens': []}):
    cv = LC.coverage(shape)
    rec('coverage(%r) is not fact_supported' % (shape,), not cv['fact_supported'], cv)
legacy = {'liens': [], 'conf': 'ok'}
rec('a cached chain without nrec / parcel_found reads search=unknown', LC.coverage(legacy)['search'] ==
    'unknown', LC.coverage(legacy))

# ---------------------------------------------------------------- 6. review 2026-10-12: more over-claims
print('OVER-CLAIMS')
c, cv, st = run([deed(), folio_loan] + filler(58))
rec('a 60-record search (conf low) is never fact_supported', c['conf'] == 'low' and not cv['fact_supported']
    and 'low_confidence' in cv['gaps'], cv)
c = run([deed(), folio_loan])[0]
for key in ('second_fc', 'second_fc_unsure'):
    cv = LC.coverage(dict(c, **{key: {'case': 'X'}}))
    rec('a separate lender foreclosure (%s) is never fact_supported' % key, not cv['fact_supported'], cv)
cv = LC.coverage(dict(c, other=[{'st': 'OPEN', 'amt': 5000, 'kind': 'code', 'doc': 'LIEN'}]))
rec('an open non-mortgage lien without a folio anchor is a gap', 'other_lien_unanchored' in cv['gaps']
    and not cv['fact_supported'], cv)
cv = LC.coverage(dict(c, other=[{'st': 'OPEN', 'own_case': True, 'kind': 'lis_pendens', 'doc': 'LIS PENDENS'}]))
rec("this case's own lis pendens is not a gap", cv['fact_supported'], cv)
subord = r('MORTGAGE SUBORDINATION AGREEMENT - SUB', '01/02/2019', FOL, b='30600', p='4', amt=50000,
           p2='ALPHA BANK NA')
c2, cv, st = run([deed(), folio_loan, subord])
rec('a priced subordination row is flagged, never a second loan proven', 'modification_as_loan' in cv['gaps']
    or len(c2['liens']) == 1, (mtg(c2), cv))
for bad in ({'liens': 5, 'nrec': 3, 'parcel_found': True}, {'nrec': 3, 'parcel_found': True,
            'mtg_open_unpriced': 'x', 'liens': []}, {'nrec': 'x', 'parcel_found': True}):
    try:
        cv = LC.coverage(bad)
        rec('coverage never raises on %r, and is not fact_supported' % (bad,), not cv['fact_supported'], cv)
    except Exception as e:  # noqa: BLE001
        rec('coverage never raises on %r' % (bad,), False, e)
s2 = LC.summarize({'A': c}, {'A': {}, 'B': {}})
rec('a lead with no chain is counted, never dropped', s2['no_chain'] == 1, s2)

# ---------------------------------------------------------------- 7. summary counts only
print('SUMMARY')
chains = {'A': run([deed(), folio_loan])[0], 'B': run([deed(), folio_loan] + filler(499))[0],
          'C': run([])[0]}
s = LC.summarize(chains)
rec('summarize counts board FACT vs supported', s['chains'] == 3 and s['board_fact'] == 2
    and s['fact_supported'] == 1, s)
rec('summarize carries no case numbers in its counts', 'A' not in str(s['gaps']) + str(s['search']), s)

print('\n%d checks, %d failed' % (len(checks), len(fails)))
if fails:
    for f in fails:
        print('  FAIL ' + f)
sys.exit(1 if fails else 0)
