"""stub_resolve: two defendants in separate owner slots of one parcel resolve a common-name stub.
Fake names and folios; the appraiser search is replaced, no network."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stub_resolve as SR

fails = 0
def check(name, ok, extra=''):
    global fails
    print(('PASS ' if ok else 'FAIL ') + name + ((' ' + str(extra)) if extra and not ok else ''))
    fails += 0 if ok else 1

def C(folio, *slots, legal='', capped=False):
    return {'folio': folio, 'owner': ' '.join(slots), 'slots': list(slots), 'capped': capped,
            'addr': 'x', 'legal': legal}

def run(cands_by_last, defs, plaintiff='BIG BANK NA', legal_of=None):
    real_md, real_legal = SR._md_candidates, SR._legal_of
    SR._md_candidates = lambda last, first: [dict(c) for c in
                                             cands_by_last.get((last, first), cands_by_last.get(last, []))]
    SR._legal_of = legal_of or (lambda county, folio: ('', {'market_value': 1}))
    try:
        return SR.resolve_case('MIAMI-DADE', 'X', plaintiff, defs)
    finally:
        SR._md_candidates, SR._legal_of = real_md, real_legal

many = [C('1', 'GARCIA JOSE'), C('2', 'GARCIA JOSE', 'LOPEZ MARIA'), C('3', 'GARCIA JOSE L')]
r = run({'GARCIA': many}, 'Garcia, Jose; Lopez, Maria; Unknown Spouse of Jose Garcia')
check('the one parcel with both defendants in separate slots resolves',
      r and r['folio'] == '2' and r['how'] == 'owner-pair', r)

r = run({'GARCIA': many}, 'Garcia, Jose')
check('a lone common-name defendant stays unresolved', r is None, r)

# A: one woman named Maria Jose Garcia is not Jose and Maria Garcia
a = [C('1', 'GARCIA JOSE'), C('2', 'GARCIA JOSE A'), C('3', 'GARCIA MARIA JOSE')]
# (the search for the second defendant returns her own common-name namesakes too)
r = run({('GARCIA', 'JOSE'): a, ('GARCIA', 'MARIA'): [a[2], C('6', 'GARCIA MARIA'), C('7', 'GARCIA MARIA E')]},
        'Garcia, Jose; Garcia, Maria')
check('both names inside ONE owner slot is not a pair', r is None, r)

# B: one person under two surnames is not a co-defendant
b = [C('4', 'GARCIA JOSE'), C('5', 'GARCIA LOPEZ JOSE')]
r = run({'GARCIA': b}, 'Garcia, Jose; Garcia Lopez, Jose')
check('the same person listed twice does not pair with themself', r is None, r)

# C: association case, the pair parcel sits in another complex
c = [C('1', 'PEREZ ANA', legal='SUNSET ESTATES'), C('2', 'PEREZ ANA', 'DIAZ LUIS', legal='CORAL HOMES')]
r = run({'PEREZ': c}, 'Perez, Ana; Diaz, Luis', plaintiff='PALM LAKES CONDOMINIUM ASSOCIATION INC')
check('an association case rejects a pair parcel outside its complex', r is None, r)
c2 = [C('1', 'PEREZ ANA', legal='SUNSET ESTATES'), C('2', 'PEREZ ANA', 'DIAZ LUIS', legal='PALM LAKES CONDO UNIT 4')]
r = run({'PEREZ': c2}, 'Perez, Ana; Diaz, Luis', plaintiff='PALM LAKES CONDOMINIUM ASSOCIATION INC')
check('an association case takes a pair parcel inside its complex', r and r['folio'] == '2', r)

# a namesake household: Jose Maria Garcia Sr and Jose Garcia Jr on one deed is not Jose and Maria
sr = [C('1', 'GARCIA JOSE'), C('8', 'GARCIA JOSE MARIA', 'GARCIA JOSE')]
r = run({('GARCIA', 'JOSE'): sr, ('GARCIA', 'MARIA'): [C('6', 'GARCIA MARIA'), C('7', 'GARCIA MARIA E')]},
        'Garcia, Jose; Garcia, Maria')
check('a slot that also names this defendant cannot be the other one', r is None, r)

# cap: a cut search can hide their second parcel
capd = [C('1', 'GARCIA JOSE', capped=True), C('2', 'GARCIA JOSE', 'LOPEZ MARIA', capped=True)]
r = run({'GARCIA': capd}, 'Garcia, Jose; Lopez, Maria')
check('a search at its page cap never pairs', r is None, r)

two = many + [C('4', 'LOPEZ MARIA', 'GARCIA JOSE')]
r = run({'GARCIA': two}, 'Garcia, Jose; Lopez, Maria')
check('two parcels with both defendants stay unresolved', r is None, r)

r = run({'GARCIA': many}, 'Garcia, Jose; Lopez, Ana')
check('a second defendant on no parcel does not resolve', r is None, r)

r = run({'PEREZ': [C('9', 'PEREZ ANA')]}, 'Perez, Ana; Diaz, Luis')
check('the single-parcel rule still wins first', r and r['folio'] == '9' and r['how'] == 'owner-unique', r)

print('\n%d failure(s)' % fails)
sys.exit(1 if fails else 0)
