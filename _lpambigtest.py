#!/usr/bin/env python
"""_lpambigtest — the multi-parcel owner bug (2026-09-16). Fixture names, parcels and cases are fake.

Run:  python _lpambigtest.py    (exit 0 = safe)

WHAT BROKE. fl_lp/broward_resolve.py graded a defendant `low` on purpose: BCPA returned 3 parcels carrying
her full name, so the name could not pick one. lp_resolve2.py RULE 2 then re-read that same candidate
list — built BY her name — found the first candidate whose owner "agrees" with her name (all of them
do, by construction) and promoted it to `high`. The "unit/legal does not contradict" second signal is
vacuous when the LP has no legal (every Broward row). One signal counted twice = a coin flip marked
high. It picked her HOMESTEAD; the recorded mortgage the lis pendens
forecloses names the OTHER parcel — her rental. 121 of 126 pass2
rows carried the same flaw; 96 had been emailed about the picked address.

Also guards the two displays that dressed the gap up as data: the Sheets CRM prospect tab printed
"Market value $0 / Judgment $0 / AUCTION / Days out 9999" on a lis pendens, and its lien lines
dropped lender, date and OPEN/SATISFIED (so three satisfied mortgages read as live debt).
"""
import sys

fails = []


def chk(name, cond):
    if not cond:
        fails.append(name)


import lp_resolve2 as R2      # noqa: E402
import lp_leads as LL         # noqa: E402
import lp_values as LV        # noqa: E402
import sheets_crm as S        # noqa: E402

SAMPLE_CANDS = [
    {'folio': '555501210050', 'addr': '10 EAST ST', 'city': 'Sample City', 'owner': 'ROE,MARY'},
    {'folio': '555501210310', 'addr': '20 WEST ST', 'city': 'Sample City', 'owner': 'ROE,MARY'},
    {'folio': '555512050300', 'addr': '30 NORTH ST', 'city': 'Sample City', 'owner': 'POE,JOHN'},
]

# --- 1. RULE 2 must not promote a name-built candidate list ---------------------------------------
chk('R2: no legal (Broward, candidates came FROM the name) -> no promotion',
    R2.owner_rule('', 'ROE,MARY', SAMPLE_CANDS) is None)
one_bw = [SAMPLE_CANDS[0]]
chk('R2: no legal + a single agreeing candidate is still ONE signal -> no promotion',
    R2.owner_rule('', 'ROE,MARY', one_bw) is None)
md_two = [{'folio': '1', 'addr': '50 W 1 ST F-318', 'owner': 'ROE RICHARD'},
          {'folio': '2', 'addr': '50 W 1 ST F-102', 'owner': 'ROE RICHARD'}]
chk('R2: legal present but TWO agreeing, non-contradicting candidates -> ambiguous, no promotion',
    R2.owner_rule('SAMPLE VILLAS CONDO BLDG F', 'ROE RICHARD', md_two) is None)
md_one = [{'folio': '1', 'addr': '40 SOUTH AVE', 'owner': 'DOE JOHN'},
          {'folio': '2', 'addr': '44 SOUTH AVE', 'owner': 'SMITH JOHN'}]
hit = R2.owner_rule('SAMPLE VILLAGE HEIGHTS LOT 19 BLK 8 PB 11/111', 'DOE JOHN', md_one)
chk('R2 regression: legal-built list + exactly ONE agreeing candidate still promotes',
    bool(hit) and hit[0]['folio'] == '1')
md_units = [{'folio': '1', 'addr': 'X APT 101', 'owner': 'LEE ANN P', 'legal': 'SUNSET CONDO UNIT 101'},
            {'folio': '2', 'addr': 'X APT 205', 'owner': 'LEE ANN P', 'legal': 'SUNSET CONDO UNIT 205'}]
hit = R2.owner_rule('SUNSET CONDO UNIT 205', 'LEE ANN P', md_units)
chk('R2: the legal unit breaks a same-owner tie (two signals) -> promotes the matching unit',
    bool(hit) and hit[0]['folio'] == '2')

# --- 2. self-heal: rows the old rule promoted get revoked; human-verified rows are never touched ----
data = {
    'CACE-26-900100': {'case': 'CACE-26-900100', 'lpOwner': 'ROE,MARY', 'legal': '', 'county': 'BROWARD',
                       'candidates': SAMPLE_CANDS, 'folio': '555501210050', 'addr': '10 EAST ST',
                       'city': 'Sample City', 'zip': '', 'paOwner': 'ROE,MARY', 'confidence': 'high',
                       'rung': 'pass2', 'needsHuman': False,
                       'evidence': 'pass2: the parcel owner of record IS the LP defendant by full name '
                                   '(ROE,MARY), and the unit/legal does not contradict'},
    'CACE-26-000001': {'case': 'CACE-26-000001', 'lpOwner': 'ROE,MARY', 'legal': '', 'county': 'BROWARD',
                       'candidates': SAMPLE_CANDS, 'folio': '555501210310', 'addr': '20 WEST ST',
                       'confidence': 'high', 'rung': 'records-verified', 'needsHuman': False,
                       'evidence': 'RECORDS-VERIFIED: mortgage PIN'},
    '2026-000002-CA-01': {'case': '2026-000002-CA-01', 'lpOwner': 'DOE JOHN',
                          'legal': 'SAMPLE VILLAGE HEIGHTS LOT 19 BLK 8 PB 11/111', 'candidates': md_one,
                          'folio': '1', 'addr': '40 SOUTH AVE', 'confidence': 'high', 'rung': 'pass2',
                          'value': 300000,
                          'evidence': 'pass2: the parcel owner of record IS the LP defendant by full name '
                                      '(DOE JOHN), and the unit/legal does not contradict'},
}
revoked = R2.revoke_ambiguous(data)
m = data['CACE-26-900100']
chk('heal: the coin-flip is revoked', 'CACE-26-900100' in revoked)
chk('heal: revoked row is low + needsHuman', m['confidence'] == 'low' and m['needsHuman'] is True)
chk('heal: revoked row no longer carries the picked parcel', not m.get('addr') and not m.get('folio'))
chk('heal: revoked row keeps every candidate for the human', len(m.get('candidates') or []) == 3)
chk('heal: evidence names the ambiguity and the way out',
    'AMBIGUOUS' in m.get('evidence', '') and 'mortgage' in m.get('evidence', '').lower())
chk('heal: a human-verified row is never touched', data['CACE-26-000001']['confidence'] == 'high'
    and data['CACE-26-000001']['folio'] == '555501210310')
chk('heal: a legitimate RULE-2 row (legal-built, one agreeing) survives',
    data['2026-000002-CA-01']['confidence'] == 'high' and '2026-000002-CA-01' not in revoked)
chk('heal: idempotent', R2.revoke_ambiguous(data) == [])

# --- 3. the board keeps the candidates visible, never as a fact -------------------------------------
g, why = LL._candidates_guess(m)
chk('board: ambiguous row shows every owned parcel in the UNCONFIRMED chip',
    '10 EAST ST' in g and '20 WEST ST' in g and '3' in g)
chk('board: the guess explains itself', 'AMBIGUOUS' in why)
chk('board: a single-candidate or empty row gets no list', LL._candidates_guess({'candidates': []}) == ('', ''))

# --- 4. lp_values must not ask Miami-Dade to price a Broward folio ----------------------------------
addrs = {'CACE-26-900100': {'folio': '555501210310', 'confidence': 'high', 'county': 'BROWARD'},
         'CACE-26-900200': {'folio': '555511190423', 'confidence': 'high', 'county': 'BROWARD', 'value': 250000},
         '2026-1-CA-01': {'folio': '0131230040010', 'confidence': 'high'}}
cache = {'555501210310': {'value': 0}}       # the poisoned MD-proxy miss a Broward folio gets today
md, cad = LV._plan(addrs, cache, every=False)
chk('values: MD folio still goes to the MD proxy', md == {'2026-1-CA-01': '0131230040010'})
chk('values: a value-less Broward folio goes to the cadastral, not Miami-Dade',
    cad == {'CACE-26-900100': '555501210310'})
chk('values: an old MD-proxy $0 miss does not block the cadastral lookup', 'CACE-26-900100' in cad)

# Condo-format folios: priced from the exact roll parcel only, never the digits-only fallback. FAKE folios.
import fl_cadastral as _FC   # noqa: E402
_real_q, _real_enrich = _FC._q, _FC.enrich
_calls = {'q': [], 'enrich': 0}


def _fake_roll(rows):
    def _q(where, n=5, _tries=3):
        _calls['q'].append(where)
        return rows
    return _q


def _no_enrich(**kw):
    _calls['enrich'] += 1
    return {'market_value': 999999}


try:
    _FC.enrich = _no_enrich
    _FC._q = _fake_roll([{'PARCEL_ID': '555501AB0170', 'JV': 321000, 'JV_HMSTD': 50000, 'OWN_NAME': 'ROE MARY'}])
    _got = LV._fetch_cad('555501ab0170')
    chk('values: a condo folio is priced from the exact roll parcel',
        _got.get('value') == 321000 and _got.get('hs') is True)
    chk('values: the condo lookup asks for the letters, not the digits',
        bool(_calls['q']) and "PARCEL_ID='555501AB0170'" in _calls['q'][-1])
    _FC._q = _fake_roll([{'PARCEL_ID': '5555010170', 'JV': 111000}])
    chk('values: a roll row with a different parcel id is refused',
        LV._fetch_cad('555501AB0170') == {'miss': 'cadastral-exact'})
    _FC._q = _fake_roll([])
    chk('values: no exact parcel on the roll is a recorded miss',
        LV._fetch_cad('555501AB0170') == {'miss': 'cadastral-exact'})
    chk('values: condo folios never use the digits-only fallback', _calls['enrich'] == 0)
finally:
    _FC._q, _FC.enrich = _real_q, _real_enrich
chk('values: an old refusal {} on a condo folio is asked again',
    LV._cad_needs('555501AB0170', {'cad:555501AB0170': {}}))
chk('values: a recorded condo miss is not asked again',
    not LV._cad_needs('555501AB0170', {'cad:555501AB0170': {'miss': 'cadastral-exact'}}))
chk('values: a cached numeric miss keeps the old rule',
    not LV._cad_needs('555501210310', {'cad:555501210310': {}}))

# --- 5. Sheets CRM: no fake zeros, no auction section, no 9999 on a lis pendens ---------------------
lp_row = {'case': 'CACE-26-900100', 'st': 'LP', 'stage': 'LP', 'addr': '20 WEST ST, Sample City, FL 33068',
          'owners': 'ROE,MARY', 'county': 'BROWARD', 'value': 0, 'judg': 0, 'auction': '', 'days': 9999,
          'filed': '8/14/2026', 'plaintiff': 'US BANK TRUST NATIONAL ASSN'}
brl = {'CACE-26-900100': {'liens': [
    {'d': '2014-05-02', 'amt': 85500, 'party': 'MORTGAGE ELECTRONIC REGISTRATION SYSTEMS', 'st': 'SATISFIED'},
    {'d': '2025-04-22', 'amt': 217000, 'party': 'HOMEXPRESS MORTGAGE CORP', 'st': 'OPEN'}]}}
b = S._prospect_block(lp_row, {'status': 'Contacted'}, {}, {}, brl, {})
flat = [' '.join(str(c) for c in r) for r in b['rows']]
txt = '\n'.join(flat)
chk('crm: no "$0" anywhere on a lis pendens tab', '$0' not in txt)
chk('crm: no 9999 sentinel', '9999' not in txt)
chk('crm: a lis pendens is not filed under AUCTION', '— AUCTION —' not in txt)
chk('crm: says plainly there is no judgment / sale yet', 'LIS PENDENS' in txt.upper())
chk('crm: lien line carries the lender', any('HOMEXPRESS' in f for f in flat))
chk('crm: lien line carries the date', any('2025-04-22' in f for f in flat))
chk('crm: a satisfied mortgage says SATISFIED', any('SATISFIED' in f and '85,500' in f for f in flat))
chk('crm: a priced lis pendens is not called a JUNIOR-LIEN case',
    'JUNIOR' not in S._chain_note(dict(lp_row, value=321130, eqfake=True), {}).upper())
rows = S.build_rows([lp_row], {'CACE-26-900100': {'status': 'Contacted'}}, {}, {})
crm = dict(zip(S.HEADERS, rows[0])) if rows else {}
chk('crm: Days column is blank for the no-sale sentinel', crm.get('Days') == '')

# --- 6. fl_lp.broward_pin: the foreclosed mortgage's PIN settles an ambiguous row ----------------------
# Fixtures are OCR-shaped text with FAKE parcels and addresses (the repo is public). The shapes are real:
# a Form 3010 page 1 carries the MERS MIN, a "PIN:" header and the BORROWER's residence ("currently
# residing at"); page 3 carries the COLLATERAL ("which currently has the address of").
import json as _json        # noqa: E402
import tempfile as _tf      # noqa: E402
from fl_lp import broward_pin as BP   # noqa: E402

CANDS = [{'folio': '123401210050', 'addr': '100 FIRST ST', 'city': 'Sample City', 'owner': 'DOE,JANE'},
         {'folio': '123401210310', 'addr': '200 SECOND AVE', 'city': 'Sample City', 'owner': 'DOE,JANE'}]
P1 = ('Loan 2000000001\nMIN: 100000000000000018\nMERS Phone: 1-888-679-6377\nPIN: 123401-21-0310\nDEFINITIONS\n'
      '(A) "Borrower" is JANE DOE, AN UNMARRIED PERSON, currently residing at 100 FIRST ST,\nSAMPLE CITY, FL 33000')
P3 = ('property located in the COUNTY of BROWARD:\nSEE EXHIBIT A ATTACHED\nwhich currently has the address of 200 '
      'SECOND AVE, SAMPLE CITY, Florida 33000 ("Property')


def _rd(*pages, mtg='900000002'):
    return [{'mtg': mtg, 'pages': {i + 1: t for i, t in enumerate(pages)}}]


chk('pin: "PIN: 123401-21-0310"', BP.labeled_pins('PIN: 123401-21-0310')[0][0] == '123401210310')
chk('pin: "A.P.N.: 1234 01 21 0310"', BP.labeled_pins('A.P.N.: 1234 01 21 0310')[0][0] == '123401210310')
chk('pin: "Parcel ID Number: 1234-01-21-0310 and"', BP.labeled_pins('Parcel ID Number: 1234-01-21-0310 and')[0][0] == '123401210310')
chk('pin: condo "Folio No. 1234 34 CB 0170 DEFINITIONS" keeps its letters',
    BP.labeled_pins('Folio No. 1234 34 CB 0170 DEFINITIONS')[0][0] == '123434CB0170')
chk('pin: the MERS MIN is not a parcel number', BP.labeled_pins('MIN: 100000000000000018') == [])
chk('pin: a 9-digit tax id is not a parcel number', BP.labeled_pins('Tax ID: 12-3456789') == [])
chk('lp text: the mortgage instrument a filing names, OCR-split digits rejoined',
    BP.instruments_named('seeking to foreclose a note and mortgage ... at Instrument Number 9 00000123.') == ['900000123'])
chk('lp text: "Official Record Instrument No. 900000456, encumbering"',
    BP.instruments_named('in Official Record Instrument\nNo. 900000456, encumbering the real property') == ['900000456'])
chk('lp text: the filing\'s own "Instr#" and "Filing #" are not a named mortgage',
    BP.instruments_named('Instr# 900000789 Page 1 of 2 Filing # 200000001 E-Filed') == [])
chk('lp text: its own instrument is excluded', BP.instruments_named('Instrument Number 900000789', exclude={'900000789'}) == [])
chk('pin: a printed folio is found as a whole token', BP.folio_printed('123401210310', 'Parcel 1234-01-21-0310, Broward'))
chk('pin: never inside a longer number', not BP.folio_printed('000000000001', 'MIN: 100000000000000018'))
chk('pin: collateral address comes from "currently has the address of", not the borrower residence',
    BP.property_address(P1) == '' and BP.property_address(P3).startswith('200 SECOND AVE'))

d = BP.decide(_rd(P1, '', P3), CANDS)
chk('decide: labeled PIN + page-3 address on the same parcel -> promoted, corroborated',
    d['status'] == 'promoted' and d['cand']['folio'] == '123401210310' and d['corroborated'] and d['page'] == 1)
chk('decide: the borrower residence on page 1 (another candidate) is not a conflict', d['status'] == 'promoted')
d = BP.decide(_rd(P1, '', P3.replace('200 SECOND AVE', '100 FIRST ST')), CANDS)
chk('decide: PIN names one parcel, property address another -> conflict (stays low)', d['status'] == 'conflict')
chk('decide: a PIN that is none of the candidates -> no-match',
    BP.decide(_rd('PIN: 777701-21-0310'), CANDS)['status'] == 'no-match')
chk('decide: both candidates printed -> ambiguous',
    BP.decide(_rd('PIN: 123401-21-0310\nExhibit A parcel 123401210050'), CANDS)['status'] == 'ambiguous')
chk('decide: nothing readable -> no-pin', BP.decide(_rd('MORTGAGE\nDEFINITIONS'), CANDS)['status'] == 'no-pin')
d = BP.decide(_rd('Exhibit A: Lot 4 of the plat, 1234-01-21-0310, Broward County'), CANDS)
chk('decide: an unlabeled printed folio still promotes (how=printed)', d['status'] == 'promoted' and d['how'] == 'printed')
chk('decide: the same PIN read twice, once garbled, does not block',
    BP.decide(_rd('PIN: 123401-21-0310', 'PIN: 123401-21-031O'), CANDS)['status'] == 'promoted')
chk('decide: a far-off second labeled number -> conflict',
    BP.decide(_rd('PIN: 123401-21-0310', 'Parcel ID: 555566-77-8899'), CANDS)['status'] == 'conflict')
chk('decide: condo folio compares exactly with its letters',
    BP.decide(_rd('Parcel ID: 1234 34 CB 0170'), [{'folio': '123434CB0170', 'addr': '9 A ST'},
                                                   {'folio': '123434CB0180', 'addr': '9 B ST'}])['cand']['folio'] == '123434CB0170')

ELIG = {'county': 'BROWARD', 'confidence': 'low', 'rung': 'pass2-revoked', 'candidates': CANDS, 'case': 'CACE-26-900001'}
chk('eligible: pass2-revoked Broward row', BP.eligible(ELIG))
chk('eligible: broward_resolve name-ladder low',
    BP.eligible(dict(ELIG, rung=None, evidence='BCPA: 2 parcels match the full name — ambiguous, needs a human')))
chk('eligible: NEVER a records-verified row', not BP.eligible(dict(ELIG, rung='records-verified', confidence='high')))
chk('eligible: never a row already promoted', not BP.eligible(dict(ELIG, rung='mortgage-pin', confidence='high')))
chk('eligible: never another rung\'s low', not BP.eligible(dict(ELIG, rung='platbook')))
chk('eligible: never outside Broward', not BP.eligible(dict(ELIG, county='MIAMI-DADE')))
chk('eligible: never a single-candidate row', not BP.eligible(dict(ELIG, candidates=CANDS[:1])))

hit = BP.decide(_rd(P1, '', P3), CANDS)
tmpd = _tf.mkdtemp()
path = tmpd + '/lp_addresses.json'
book = {'CACE-26-900001': dict(ELIG),
        'CACE-26-900002': dict(ELIG, case='CACE-26-900002', rung='records-verified', confidence='high',
                               folio='123401210050', addr='100 FIRST ST'),
        'CACE-26-900003': dict(ELIG, case='CACE-26-900003', candidates=[CANDS[0], dict(CANDS[0], folio='123401219999')])}
_json.dump(book, open(path, 'w', encoding='utf-8'))
done = BP.apply_promotions(path, {'CACE-26-900001': (hit, ['900000001'], '33000'),
                                  'CACE-26-900002': (hit, ['900000009'], ''),
                                  'CACE-26-900003': (hit, ['900000010'], '')}, '2026-09-16')
after = _json.load(open(path, encoding='utf-8'))
r1 = after['CACE-26-900001']
chk('apply: the eligible row is promoted to the named parcel',
    done == ['CACE-26-900001'] and r1['folio'] == '123401210310' and r1['addr'] == '200 SECOND AVE'
    and r1['confidence'] == 'high' and r1['rung'] == 'mortgage-pin' and r1['needsHuman'] is False and r1['zip'] == '33000')
chk('apply: evidence cites the lis pendens and the mortgage instrument',
    '900000001' in r1['evidence'] and '900000002' in r1['evidence'] and 'MORTGAGE-PIN' in r1['evidence'])
chk('apply: every candidate is kept for audit', len(r1['candidates']) == 2)
chk('evidence: a mortgage found by the filing\'s text says so',
    'names in its text' in BP.evidence('2026-09-16', ['900000001'], dict(hit, via='text'), 2)
    and 'links (DocLink)' in BP.evidence('2026-09-16', ['900000001'], dict(hit, via='link'), 2))
chk('apply: a records-verified row is untouched even if handed a hit', after['CACE-26-900002'] == book['CACE-26-900002'])
chk('apply: a folio that is not among the row\'s candidates is never written', after['CACE-26-900003'] == book['CACE-26-900003'])
cached = {'mtg': '900000002', 'page': 1, 'how': 'labeled', 'pin': '123401-21-0310', 'corroborated': True,
          'via': 'link', 'folio': '123401210310'}
rebuilt = BP._hit_from_cache(ELIG, cached)
chk('cache: an interrupted run\'s promotion is rebuilt against the row\'s own candidate',
    rebuilt and rebuilt['cand']['addr'] == '200 SECOND AVE' and 'MORTGAGE-PIN' in BP.evidence('2026-09-16', ['1'], rebuilt, 2))
chk('cache: a cached folio no longer among the candidates rebuilds nothing',
    BP._hit_from_cache(dict(ELIG, candidates=CANDS[:1]), cached) is None)
chk('cache: a settled no is not re-read for 30 days', not BP._due({'status': 'no-pin', 'd': '2026-09-10'}, '2026-09-16', 30))
chk('cache: a settled no is re-read after 30 days', BP._due({'status': 'no-pin', 'd': '2026-08-01'}, '2026-09-16', 30))
chk('cache: a transient failure is re-read next run', BP._due({'status': 'unreadable', 'd': '2026-09-16'}, '2026-09-16', 30))

# --- 7. fl_lp.broward_pin: a lis pendens that names no mortgage settles by its LEGAL DESCRIPTION -----------
# FAKE developments, folios and addresses. The shapes are real: the filing prints "Lot / Block / <name>,
# according to the plat ... Plat Book N, Page M" or "Unit N, <name>, a Condominium"; the FDOR roll legal
# names the development ("<NAME> 990-98 B") and never the lot or unit.
LC = [{'folio': '123401210050', 'addr': '100 FIRST ST'}, {'folio': '123434CB0170', 'addr': '9 SAMPLE CT'}]
LP_PLAT = ('... seeking to foreclose a lien encumbering LOT 91, BLOCK 97, SAMPLE HEIGHTS HOMES, ACCORDING TO THE MAP '
           'OR PLAT THEREOF, AS RECORDED IN PLAT BOOK 990, PAGE 98, OF THE PUBLIC RECORDS OF BROWARD COUNTY, FLORIDA')
LP_CONDO = ('THAT CERTAIN CONDOMINIUM PARCEL, COMPOSED OF UNIT 902, THE CYPRESS AT SAMPLEWOOD 111, A CONDOMINIUM AND '
            'AN UNDIVIDED SHARE IN THE COMMON ELEMENTS')
ROLL = {'123401210050': 'SAMPLE HEIGHTS HOMES 990-98 B', '123434CB0170': 'OTHER PLACE CONDO'}

d = BP.decide_legal(LP_PLAT, LC, ROLL)
chk('legal: same plat book/page on exactly one candidate -> promoted by plat',
    d['status'] == 'promoted' and d['cand']['folio'] == '123401210050' and 'plat book 990 page 98' in d['by'])
d = BP.decide_legal(LP_CONDO, LC, {'123401210050': 'SAMPLE HEIGHTS HOMES 990-98 B', '123434CB0170': 'CYPRESS AT SAMPLEWOOD III'})
chk('legal: condo name matches through OCR roman numerals ("111" = III) -> promoted by name',
    d['status'] == 'promoted' and d['cand']['folio'] == '123434CB0170')
chk('legal: "SECTION THREE" in the filing equals "SEC 3" on the roll',
    BP.decide_legal('LOT 94, BLOCK 947, SAMPLE PARK SECTION THREE, ACCORDING TO THE PLAT THEREOF', LC,
                    {'123401210050': 'SAMPLE PARK SEC 3', '123434CB0170': 'OTHER PLACE CONDO'})['status'] == 'promoted')
chk('legal: two candidates in the same development -> ambiguous (the roll legal has no lot or unit)',
    BP.decide_legal(LP_PLAT, LC, {'123401210050': 'SAMPLE HEIGHTS HOMES 990-98 B',
                                  '123434CB0170': 'SAMPLE HEIGHTS HOMES 990-98 B'})['status'] == 'ambiguous')
chk('legal: a disagreeing plat rules a candidate out even when the name agrees',
    BP.decide_legal(LP_PLAT, LC, {'123401210050': 'SAMPLE HEIGHTS HOMES 991-9 B',
                                  '123434CB0170': 'OTHER PLACE CONDO'})['status'] == 'no-match')
chk('legal: a bare city name in the filing never names a parcel',
    BP.decide_legal('LOT 3, BLOCK 2, ACCORDING TO THE PLAT THEREOF, PEMBROKE PINES, FLORIDA', LC,
                    {'123401210050': 'PEMBROKE PINES', '123434CB0170': 'OTHER PLACE CONDO'})['status'] == 'no-match')
chk('legal: a one-word development name without a plat is not enough',
    BP.decide_legal('UNIT 5, SAMPLEWOOD, A CONDOMINIUM', LC,
                    {'123401210050': 'SAMPLEWOOD', '123434CB0170': 'OTHER PLACE CONDO'})['status'] == 'no-match')
chk('legal: the name must sit inside the legal description, not elsewhere in the filing',
    BP.decide_legal('PLAINTIFF SAMPLE HEIGHTS LENDING LLC ' + 'X ' * 150 + 'UNIT 5 OF A CONDOMINIUM', LC,
                    {'123401210050': 'SAMPLE HEIGHTS', '123434CB0170': 'OTHER PLACE CONDO'})['status'] == 'no-match')
chk('legal: a candidate the roll has no legal for blocks promotion (it cannot be ruled out)',
    BP.decide_legal(LP_PLAT, LC, {'123401210050': 'SAMPLE HEIGHTS HOMES 990-98 B', '123434CB0170': ''})['status'] == 'no-roll-legal')
chk('legal: an a/k/a address naming ANOTHER candidate -> conflict',
    BP.decide_legal(LP_PLAT + ' A/K/A 9 SAMPLE CT, SAMPLE CITY', LC, ROLL)['status'] == 'conflict')
d = BP.decide_legal(LP_PLAT + ' A/K/A 100 FIRST STREET, SAMPLE CITY', LC, ROLL)
chk('legal: an a/k/a address naming the same candidate corroborates', d['status'] == 'promoted' and d['corroborated'])
chk('legal: a filing that prints no legal description -> no-legal',
    BP.decide_legal('NOTICE OF LIS PENDENS. PLAINTIFF V. DEFENDANT. CASE NO. 000', LC, ROLL)['status'] == 'no-legal')
chk('legal: roll legal key splits the plat off the development name',
    BP.roll_legal_key('SAMPLE HEIGHTS SEC 3 945-98 B') == ({(945, 98)}, ['SAMPLE', 'HEIGHTS', 'SEC', '3']))
chk('legal: "Plat Book 952, Page(s) 98" is read', (952, 98) in BP.lp_legal('LOT K-95, SAMPLE LAKES, PLAT BOOK 952, PAGE(S) 98, OF')['plats'])

hit = BP.decide_legal(LP_PLAT, LC, ROLL)
tmpd = _tf.mkdtemp()
path = tmpd + '/lp_addresses.json'
_json.dump({'CACE-26-900011': dict(ELIG, case='CACE-26-900011', candidates=LC)}, open(path, 'w', encoding='utf-8'))
done = BP.apply_promotions(path, {'CACE-26-900011': (hit, ['900000011'], '')}, '2026-09-16')
r = _json.load(open(path, encoding='utf-8'))['CACE-26-900011']
chk('legal apply: promoted with rung lp-legal to the matched parcel',
    done == ['CACE-26-900011'] and r['rung'] == 'lp-legal' and r['confidence'] == 'high' and r['folio'] == '123401210050')
chk('legal apply: evidence says LP-LEGAL, quotes the roll legal, and admits the unit is unproven',
    'LP-LEGAL' in r['evidence'] and 'SAMPLE HEIGHTS HOMES 990-98 B' in r['evidence'] and 'not the unit' in r['evidence'])
cached_legal = {'how': 'legal', 'by': 'plat book 990 page 98', 'roll_legal': 'SAMPLE HEIGHTS HOMES 990-98 B',
                'corroborated': False, 'via': 'legal', 'mtg': None, 'page': None, 'pin': 'plat book 990 page 98',
                'folio': '123401210050'}
rb = BP._hit_from_cache(dict(ELIG, candidates=LC), cached_legal)
chk('legal cache: an interrupted run\'s legal promotion rebuilds with legal evidence',
    rb and 'LP-LEGAL' in BP.evidence_legal('2026-09-16', ['1'], rb, 2))
chk('legal cache: a no-link cached BEFORE the matcher existed is read once more',
    BP._due({'status': 'no-link', 'd': '2026-09-15'}, '2026-09-16', 30))
chk('legal cache: a no-link the matcher already read stays settled',
    not BP._due({'status': 'no-link', 'd': '2026-09-15', 'legal': 'none-found'}, '2026-09-16', 30))
chk('legal cache: a candidate without a roll legal is a settled answer',
    not BP._due({'status': 'no-roll-legal', 'd': '2026-09-15', 'legal': 'no-roll-legal'}, '2026-09-16', 30))

# recall rules, each with the refusal that keeps it honest (FAKE data)
chk('legal: a trailing letter is part of the development name ("SAMPLEMONT CONDOMINIUM B")',
    BP.decide_legal('CONDOMINIUM UNIT NO. 901 OF SAMPLEMONT CONDOMINIUM B, A CONDOMINIUM', LC,
                    {'123401210050': 'OTHER PLACE CONDO', '123434CB0170': 'SAMPLEMONT CONDOMINIUM B'})['status'] == 'promoted')
TWIN = [{'folio': '123401210180', 'addr': '9526 NW 96 CT'}, {'folio': '123401210160', 'addr': '9534 NW 96 CT'}]
TWIN_ROLL = {'123401210180': 'SAMPLE PINE ESTATES 969-96 B', '123401210160': 'SAMPLE PINE ESTATES 969-96 B'}
LP_TWIN = ('PROPERTY DESCRIBED BELOW: 9526 NW 96TH COURT SAMPLE CITY FLORIDA 33000 LOT 98, OF SAMPLE PINE ESTATES, '
           'ACCORDING TO THE PLAT THEREOF, AS RECORDED IN PLAT BOOK 969, PAGE 96')
d = BP.decide_legal(LP_TWIN, TWIN, TWIN_ROLL)
chk('legal: two parcels on one plat - the street address printed with the legal picks one ("96TH" = "96")',
    d['status'] == 'promoted' and d['cand']['folio'] == '123401210180' and d['corroborated'])
FAR = ('TO: JANE DOE, 9534 NW 96 CT, SAMPLE CITY FL 33000. ' + 'YOU ARE NOTIFIED THAT AN ACTION HAS BEEN FILED. ' * 12
       + 'LOT 98, OF SAMPLE PINE ESTATES, ACCORDING TO THE PLAT THEREOF, AS RECORDED IN PLAT BOOK 969, PAGE 96')
chk('legal: a defendant\'s home address far up in the caption never breaks the tie',
    BP.decide_legal(FAR, TWIN, TWIN_ROLL)['status'] == 'ambiguous')
UNITS = [{'folio': '123434CB0906', 'addr': '1 SAMPLE WAY #906'}, {'folio': '123434CB0910', 'addr': '1 SAMPLE WAY #910'}]
d = BP.decide_legal('UNIT 906, BUILDING 92 OF SAMPLE TOWERS, A CONDOMINIUM', UNITS,
                    {'123434CB0906': 'SAMPLE TOWERS CONDO', '123434CB0910': 'SAMPLE TOWERS CONDO'})
chk('legal: two units in one condo - the unit number printed with the legal picks one',
    d['status'] == 'promoted' and d['cand']['folio'] == '123434CB0906')
chk('legal: two units in one condo and no unit number -> still ambiguous',
    BP.decide_legal('THAT CONDOMINIUM PARCEL IN SAMPLE TOWERS, A CONDOMINIUM', UNITS,
                    {'123434CB0906': 'SAMPLE TOWERS CONDO', '123434CB0910': 'SAMPLE TOWERS CONDO'})['status'] == 'ambiguous')
VIS = [{'folio': '123401DD0410', 'addr': '9774 SAMPLEWOOD BLVD #901P'}, {'folio': '123401210999', 'addr': '9 OTHER ST'}]
VIS_ROLL = {'123401DD0410': 'SAMPLE VISTAS X-Y IN SAMPLEWOOD', '123401210999': 'OTHER PLACE 12-3 B'}
LP_VIS = 'THE CONDOMINIUM PARCEL KNOWN AS APARTMENT X-901, IN CONDOMINIUM X-Y IN SAMPLE VISTAS IN SAMPLEWOOD, THE CONDOMINIUM'
chk('legal: development words in another order are NOT enough on their own',
    BP.decide_legal(LP_VIS, VIS, VIS_ROLL)['status'] == 'no-match')
chk('legal: ...the same words plus the parcel\'s own street address are',
    BP.decide_legal(LP_VIS + ' PROPERTY ADDRESS: 9774 NW SAMPLEWOOD BLVD', VIS, VIS_ROLL)['status'] == 'promoted')
chk('legal: the legal fits one parcel but ANOTHER candidate\'s address is printed with it -> conflict',
    BP.decide_legal(LP_PLAT + ', 9 SAMPLE CT', LC, ROLL)['status'] == 'conflict')

if fails:
    for f in fails:
        print('FAIL:', f)
    print('%d check(s) failed' % len(fails))
    sys.exit(1)
print('lp ambiguity + crm display: all checks pass')
