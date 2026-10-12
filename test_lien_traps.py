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

  3. The four analyze() defects found 2026-10-12, fixed after Alex's go the same day:
     * a lender's release of ANOTHER parcel or ANOTHER book/page never frees this parcel's loan
     * a priced modification amends the loan it modifies; it is a second loan only when no loan is found
     * a loan placed only by subdivision whose borrower is clearly a namesake is not this owner's debt
     * a priced chain on a capped (500+) search is UNVERIFIED ('none'), never 'priced'
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


def run(models, judgment=150000, owner=''):
    c = R.analyze(models, FOL, judgment, owner=owner)
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
rec('a priced chain on a capped search is not a FACT (the newest loans are past the cap)',
    st not in ES.FACT and st == 'none', st)
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
rec('with the owner unknown, a same-subdivision loan still counts (too much debt, never too little)',
    any(l['amt'] == 90000 and l['st'] == 'OPEN' for l in c['liens']), mtg(c))
OWNER = 'OWNER TESTA'          # surname TESTA, given OWNER
mario = dict(namesake, firsT_PARTY='TESTA MARIO')
c = R.analyze([deed(), folio_loan, mario], FOL, 150000, owner=OWNER)
rec("a subdivision-only loan to the owner's surname with another given name is a namesake's",
    not any(l['amt'] == 90000 for l in c['liens']) and c['mtg_namesake_skipped'] == 1, (mtg(c), c['mtg_namesake_skipped']))
for p1 in ('TESTA O', 'TESTA', 'TESTA OWNER M', 'OWNER TESTA & MARIO'):
    c = R.analyze([deed(), folio_loan, dict(namesake, firsT_PARTY=p1)], FOL, 150000, owner=OWNER)
    rec('a borrower that may be the owner (%s) still counts' % p1,
        any(l['amt'] == 90000 and l['st'] == 'OPEN' for l in c['liens']), mtg(c))
rec('a chain with a loan set aside as a namesake is never a FACT (the index abbreviates: SMITH WM)',
    ES.state_of(R.analyze([deed(), mario], FOL, 150000, owner=OWNER)) not in ES.FACT)
c = R.analyze([deed(), folio_loan, dict(namesake, firsT_PARTY='TESTA MARIO', foliO_NUMBER=FOL)], FOL, 150000,
              owner=OWNER)
rec('a namesake-looking loan indexed to the subject folio still counts',
    any(l['amt'] == 90000 for l in c['liens']), mtg(c))
spouse = r('DEED - DEE', '01/02/2015', FOL, b='29000', p='100', p2='TESTA OWNER & MARIA')
c = R.analyze([spouse, folio_loan, dict(namesake, firsT_PARTY='TESTA MARIA')], FOL, 150000, owner=OWNER)
rec("a spouse named on the parcel's deed is household, not a namesake",
    any(l['amt'] == 90000 for l in c['liens']), mtg(c))

# ---------------------------------------------------------------- 3. partial release for another parcel
print('PARTIAL RELEASE')
partial = r('PARTIAL RELEASE - PRL', '06/01/2016', OTHER, b='30000', p='7', p1='OWNER TESTA',
            p2='ALPHA BANK NA', ob='28000', op='9')
c, cv, st = run([deed(), folio_loan, partial])
rec('a release that names another book/page never counts as a book/page release',
    not any(l.get('sat_by') == 'book/page' for l in c['liens']), mtg(c))
rec("a lender's partial release of another parcel leaves this parcel's loan OPEN",
    all(l['st'] == 'OPEN' for l in c['liens']) and c['open_count'] == 1, mtg(c))
for name, rel in (('a full release indexed to another folio',
                   r('SATISFACTION - SAT', '06/01/2016', OTHER, b='30000', p='8', p2='ALPHA BANK NA')),
                  ('a release naming another book/page',
                   r('SATISFACTION - SAT', '06/01/2016', '', b='30000', p='9', p2='ALPHA BANK NA',
                     ob='27000', op='5')),
                  ('a partial release with no folio',
                   r('PARTIAL RELEASE - PRL', '06/01/2016', '', b='30000', p='10', p2='ALPHA BANK NA'))):
    c = run([deed(), folio_loan, rel])[0]
    rec('%s does not free the loan' % name, all(l['st'] == 'OPEN' for l in c['liens']), mtg(c))
c = run([deed(), folio_loan, r('SATISFACTION - SAT', '06/01/2016', '', b='30000', p='11', p2='ALPHA BANK NA')])[0]
rec('a lender release with no book/page and no folio still frees it by inference (unchanged)',
    c['liens'][0]['st'] == 'SATISFIED' and 'release_inferred' in LC.coverage(c)['gaps'], mtg(c))
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
rec('a priced modification amends its loan, never a second one', [l['amt'] for l in c['liens']] == [200000]
    and c['liens'][0].get('amended_by') == ['30500/3'], mtg(c))
up = dict(modif, consideratioN_1=230000)
c = run([deed(), folio_loan, up])[0]
rec('...and a larger modification raises that loan to its figure', [l['amt'] for l in c['liens']] == [230000], mtg(c))
named_mod = dict(modif, seconD_PARTY='OMEGA SERVICING', oriG_REC_BOOK='29000', oriG_REC_PAGE='0101')
c = run([deed(), folio_loan, named_mod])[0]
rec('a modification naming the loan book/page attaches whoever files it', [l['amt'] for l in c['liens']] == [200000], mtg(c))
paid = r('SATISFACTION - SAT', '06/01/2017', '', b='30450', p='1', p2='ALPHA BANK NA', ob='29000', op='101')
c = run([deed(), folio_loan, paid, modif])[0]
rec('a later modification from the lender of a paid-off loan is not folded into it', len(c['liens']) == 2
    and [l['st'] for l in c['liens']] == ['SATISFIED', 'OPEN'], mtg(c))
# review 2026-10-12: an inferred release must not take a folded-in modification's debt with it
l05 = r('MORTGAGE - MOR', '01/02/2005', FOL, b='24000', p='1', amt=150000, p2='ALPHA BANK NA')
l10 = r('MORTGAGE - MOR', '01/02/2010', FOL, b='27000', p='1', amt=200000, p2='ALPHA BANK NA')
s10 = r('SATISFACTION - SAT', '06/01/2010', '', b='27100', p='1', p2='ALPHA BANK NA')
m18 = r('MORTGAGE MODIFICATION - MMO', '01/02/2018', FOL, b='31000', p='1', amt=250000, p2='ALPHA BANK NA')
c, cv, st = run([deed(), l05, l10, s10, m18])
rec('a modification survives an inferred release of the loans before it', any(
    l['st'] == 'OPEN' and l['amt'] >= 250000 for l in c['liens']) and c['surv'] + c['first_est'] > 0, (mtg(c), st))
heloc = r('MORTGAGE - MOR', '01/02/2012', FOL, b='26000', p='3', amt=50000, p2='ALPHA BANK NA')
m_other = r('MORTGAGE MODIFICATION - MMO', '01/02/2019', FOL, b='31200', p='1', amt=320000, p2='ALPHA BANK NA',
            ob='19000', op='44')
c = run([deed(), heloc, m_other])[0]
rec('a modification naming a loan the search did not return stays its own row',
    sorted(l['amt'] for l in c['liens'] if l['st'] == 'OPEN') == [50000, 320000], mtg(c))
unp = dict(heloc, consideratioN_1=0)
c, cv, st = run([deed(), unp, dict(m_other, oriG_REC_BOOK='26000', oriG_REC_PAGE='3')])
rec('a priced modification prices the unpriced loan it names', [l['amt'] for l in c['liens']] == [320000]
    and not c['mtg_open_unpriced'], (mtg(c), c['mtg_open_unpriced']))
c, cv, st = run([deed(), unp, dict(m_other, oriG_REC_BOOK='', oriG_REC_PAGE='')])
rec('a modification only GUESSED to amend an unpriced loan does not price it', st not in ES.FACT
    and c['mtg_open_unpriced'] == 1, (mtg(c), st))
lone = dict(modif, seconD_PARTY='OMEGA SERVICING')
c, cv, st = run([deed(), folio_loan, lone])
rec('a modification with no loan to attach to stays its own row and is flagged', len(c['liens']) == 2
    and 'modification_as_loan' in cv['gaps'] and not cv['fact_supported'], (mtg(c), cv))
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
zero = r('MORTGAGE - MOR', '01/02/2016', FOL, b='29500', p='10', amt=0, p2='DELTA BANK NA')
zrel = r('SATISFACTION - SAT', '06/01/2018', '', b='31500', p='2', p1='OWNER TESTA', p2='DELTA BANK NA')
c3, cv, st = run([deed(), zero, zrel])
rec('an unpriced loan released by inference still reads release_inferred (Codex review)',
    c3.get('mtg_unpriced_release_inferred') == 1 and 'release_inferred' in cv['gaps']
    and not cv['fact_supported'], (c3.get('mtg_unpriced_release_inferred'), cv))
s2 = LC.summarize({'A': c}, {'A': {}, 'B': {}})
rec('a lead with no chain is counted, never dropped', s2['no_chain'] == 1, s2)

# ---------------------------------------------------------------- 7. deed book/page anchoring
print('DEED ANCHOR')
lead = {'sale_refs': [{'b': '29000', 'p': '0100', 'd': '01/02/2015', 'i': 'DEE'}]}
OWN = 'OWNER TESTA'
c = run([deed(), loan()], owner=OWN)[0]
rec('the loan row records its borrower match and the deed before it',
    c['liens'][0].get('by_owner') is True and c['liens'][0].get('deed_bp') == '29000/100', c['liens'][0])
cv = LC.coverage(c, lead)
rec('a loan recorded right behind the appraiser-listed deed is tied to the parcel',
    cv['deed_anchored'] == 1 and 'subdivision_only_mortgage' not in cv['gaps'] and cv['fact_supported'], cv)
rec('...without the deed reference it stays subdivision-only', not LC.coverage(c)['fact_supported'])
for name, ref in (('a different book', {'b': '29001', 'p': '100', 'd': '01/02/2015'}),
                  ('a page before the deed', {'b': '29000', 'p': '102', 'd': '01/02/2015'}),
                  ('a page far after the deed', {'b': '29000', 'p': '20', 'd': '01/02/2015'}),
                  ('a sale a month apart', {'b': '29000', 'p': '100', 'd': '02/02/2015'}),
                  ('an unreadable date', {'b': '29000', 'p': '100', 'd': 'soon'}),
                  ('no page', {'b': '29000', 'p': '', 'd': '01/02/2015'})):
    cv = LC.coverage(c, {'sale_refs': [ref]})
    rec('%s does not tie the loan' % name, cv['deed_anchored'] == 0 and not cv['fact_supported'], cv)
rec('an ISO sale date reads too', LC.coverage(c, {'sale_refs': [{'b': '29000', 'p': '100',
    'd': '2015-01-02'}]})['deed_anchored'] == 1)
c = run([deed(), loan(), namesake], owner=OWN)[0]
cv = LC.coverage(c, lead)
rec('a later namesake mortgage in the subdivision is still a gap', cv['deed_anchored'] == 1
    and 'subdivision_only_mortgage' in cv['gaps'] and not cv['fact_supported'], cv)
# review 2026-10-12: an investor buys the subject and a neighbouring unit the same day
two = [deed(), loan(), r('DEED - DEE', '01/02/2015', OTHER, b='29000', p='106', p2='OWNER TESTA'),
       r('MORTGAGE - MOR', '01/02/2015', '', b='29000', p='107', amt=250000, p2='BETA BANK NA')]
c = run(two, owner=OWN)[0]
cv = LC.coverage(c, lead)
rec("the neighbouring unit's loan, behind its own deed, is not tied to this parcel",
    cv['deed_anchored'] == 1 and 'subdivision_only_mortgage' in cv['gaps'] and not cv['fact_supported'], cv)
# a cash purchase, and a namesake's loan recorded a few pages later that day
cash = [deed(), r('MORTGAGE - MOR', '01/02/2015', '', b='29000', p='104', amt=300000, p1='OWNER TESTB',
                  p2='GAMMA BANK NA')]
cv = LC.coverage(run(cash, owner=OWN)[0], lead)
rec("a namesake's loan right behind the deed is not tied (borrower is not the owner)",
    cv['deed_anchored'] == 0 and not cv['fact_supported'], cv)
far = [deed(), dict(loan(), reC_PAGE='115', reC_BOOKPAGE='29000/115')]
cv = LC.coverage(run(far, owner=OWN)[0], lead)
rec('a loan 15 pages behind the deed is not tied', cv['deed_anchored'] == 0, cv)
old_rows = [{k: v for k, v in l.items() if k not in ('by_owner', 'deed_bp')} for l in
            run([deed(), loan()], owner=OWN)[0]['liens']]
cv = LC.coverage(dict(run([deed(), loan()], owner=OWN)[0], liens=old_rows), lead)
rec('a cached row without the evidence fields is never tied', cv['deed_anchored'] == 0, cv)
rec('garbage sale_refs never raise', LC.coverage(c, {'sale_refs': 'x'})['deed_anchored'] == 0
    and LC.coverage(c, {'sale_refs': [None, 5]})['deed_anchored'] == 0)
s3 = LC.summarize({'A': run([deed(), loan()], owner=OWN)[0]}, {'A': lead})
rec('summarize counts deed-anchored mortgages', s3['deed_anchored_mortgages'] == 1 and s3['fact_supported'] == 1, s3)
import foreclosure_leads as FL  # noqa: E402
refs = FL._sale_refs([{'OfficialRecordBook': '12945', 'OfficialRecordPage': '0623', 'DateOfSale': '3/4/1986',
                       'SaleInstrument': '', 'GranteeName1': 'NOBODY', 'SalePrice': 9},
                      {'OfficialRecordBook': '', 'OfficialRecordPage': '1'}, 'junk'])
rec('enrich keeps book, page (leading zero), date and instrument, and drops names and prices',
    refs == [{'b': '12945', 'p': '0623', 'd': '3/4/1986', 'i': ''}], refs)

# ---------------------------------------------------------------- 8. summary counts only
print('SUMMARY')
chains = {'A': run([deed(), folio_loan])[0], 'B': run([deed(), folio_loan] + filler(499))[0],
          'C': run([])[0]}
s = LC.summarize(chains)
rec('summarize counts board FACT vs supported', s['chains'] == 3 and s['board_fact'] == 1
    and s['fact_supported'] == 1, s)
rec('summarize carries no case numbers in its counts', 'A' not in str(s['gaps']) + str(s['search']), s)

print('\n%d checks, %d failed' % (len(checks), len(fails)))
if fails:
    for f in fails:
        print('  FAIL ' + f)
sys.exit(1 if fails else 0)
