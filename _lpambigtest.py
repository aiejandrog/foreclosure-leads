#!/usr/bin/env python
"""_lpambigtest — the multi-parcel owner bug (2026-09-16, CACE-26-013184 Lynsia Montas).

Run:  python _lpambigtest.py    (exit 0 = safe)

WHAT BROKE. fl_lp/broward_resolve.py graded Montas `low` on purpose: BCPA returned 3 parcels carrying
her full name, so the name could not pick one. lp_resolve2.py RULE 2 then re-read that same candidate
list — built BY her name — found the first candidate whose owner "agrees" with her name (all of them
do, by construction) and promoted it to `high`. The "unit/legal does not contradict" second signal is
vacuous when the LP has no legal (every Broward row). One signal counted twice = a coin flip marked
high. It picked her HOMESTEAD (6280 BLVD OF CHAMPIONS); the recorded mortgage the lis pendens
forecloses (instr 120176310, PIN 494101-21-0310) is her rental at 6260 HIGHLAND CT. 121 of 126 pass2
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

MONTAS_CANDS = [
    {'folio': '494101210050', 'addr': '6280 BLVD OF CHAMPIONS', 'city': 'North Lauderdale', 'owner': 'MONTAS,LYNSIA'},
    {'folio': '494101210310', 'addr': '6260 HIGHLAND CT', 'city': 'North Lauderdale', 'owner': 'MONTAS,LYNSIA'},
    {'folio': '494112050300', 'addr': '6240 SW 18 CT', 'city': 'North Lauderdale', 'owner': 'MATHON,JACQUES'},
]

# --- 1. RULE 2 must not promote a name-built candidate list ---------------------------------------
chk('R2: no legal (Broward, candidates came FROM the name) -> no promotion',
    R2.owner_rule('', 'MONTAS,LYNSIA', MONTAS_CANDS) is None)
one_bw = [MONTAS_CANDS[0]]
chk('R2: no legal + a single agreeing candidate is still ONE signal -> no promotion',
    R2.owner_rule('', 'MONTAS,LYNSIA', one_bw) is None)
md_two = [{'folio': '1', 'addr': '1725 W 60 ST F-318', 'owner': 'GARCIA EDUARDO'},
          {'folio': '2', 'addr': '1725 W 60 ST F-102', 'owner': 'GARCIA EDUARDO'}]
chk('R2: legal present but TWO agreeing, non-contradicting candidates -> ambiguous, no promotion',
    R2.owner_rule('PALM SPRINGS VILLAS CONDO BLDG F', 'GARCIA EDUARDO', md_two) is None)
md_one = [{'folio': '1', 'addr': '13105 NW 12 AVE', 'owner': 'HARRELL TONYA'},
          {'folio': '2', 'addr': '13109 NW 12 AVE', 'owner': 'SMITH JOHN'}]
hit = R2.owner_rule('BISCAYNE VILLAGE HEIGHTS LOT 19 BLK 8 PB 46/570', 'HARRELL TONYA', md_one)
chk('R2 regression: legal-built list + exactly ONE agreeing candidate still promotes',
    bool(hit) and hit[0]['folio'] == '1')
md_units = [{'folio': '1', 'addr': 'X APT 101', 'owner': 'LIM EDDIE P', 'legal': 'SUNSET CONDO UNIT 101'},
            {'folio': '2', 'addr': 'X APT 205', 'owner': 'LIM EDDIE P', 'legal': 'SUNSET CONDO UNIT 205'}]
hit = R2.owner_rule('SUNSET CONDO UNIT 205', 'LIM EDDIE P', md_units)
chk('R2: the legal unit breaks a same-owner tie (two signals) -> promotes the matching unit',
    bool(hit) and hit[0]['folio'] == '2')

# --- 2. self-heal: rows the old rule promoted get revoked; human-verified rows are never touched ----
data = {
    'CACE-26-013184': {'case': 'CACE-26-013184', 'lpOwner': 'MONTAS,LYNSIA', 'legal': '', 'county': 'BROWARD',
                       'candidates': MONTAS_CANDS, 'folio': '494101210050', 'addr': '6280 BLVD OF CHAMPIONS',
                       'city': 'North Lauderdale', 'zip': '', 'paOwner': 'MONTAS,LYNSIA', 'confidence': 'high',
                       'rung': 'pass2', 'needsHuman': False,
                       'evidence': 'pass2: the parcel owner of record IS the LP defendant by full name '
                                   '(MONTAS,LYNSIA), and the unit/legal does not contradict'},
    'CACE-26-000001': {'case': 'CACE-26-000001', 'lpOwner': 'MONTAS,LYNSIA', 'legal': '', 'county': 'BROWARD',
                       'candidates': MONTAS_CANDS, 'folio': '494101210310', 'addr': '6260 HIGHLAND CT',
                       'confidence': 'high', 'rung': 'records-verified', 'needsHuman': False,
                       'evidence': 'RECORDS-VERIFIED: mortgage PIN'},
    '2026-000002-CA-01': {'case': '2026-000002-CA-01', 'lpOwner': 'HARRELL TONYA',
                          'legal': 'BISCAYNE VILLAGE HEIGHTS LOT 19 BLK 8 PB 46/570', 'candidates': md_one,
                          'folio': '1', 'addr': '13105 NW 12 AVE', 'confidence': 'high', 'rung': 'pass2',
                          'value': 371506,
                          'evidence': 'pass2: the parcel owner of record IS the LP defendant by full name '
                                      '(HARRELL TONYA), and the unit/legal does not contradict'},
}
revoked = R2.revoke_ambiguous(data)
m = data['CACE-26-013184']
chk('heal: the Montas coin-flip is revoked', 'CACE-26-013184' in revoked)
chk('heal: revoked row is low + needsHuman', m['confidence'] == 'low' and m['needsHuman'] is True)
chk('heal: revoked row no longer carries the picked parcel', not m.get('addr') and not m.get('folio'))
chk('heal: revoked row keeps every candidate for the human', len(m.get('candidates') or []) == 3)
chk('heal: evidence names the ambiguity and the way out',
    'AMBIGUOUS' in m.get('evidence', '') and 'mortgage' in m.get('evidence', '').lower())
chk('heal: a human-verified row is never touched', data['CACE-26-000001']['confidence'] == 'high'
    and data['CACE-26-000001']['folio'] == '494101210310')
chk('heal: a legitimate RULE-2 row (legal-built, one agreeing) survives',
    data['2026-000002-CA-01']['confidence'] == 'high' and '2026-000002-CA-01' not in revoked)
chk('heal: idempotent', R2.revoke_ambiguous(data) == [])

# --- 3. the board keeps the candidates visible, never as a fact -------------------------------------
g, why = LL._candidates_guess(m)
chk('board: ambiguous row shows every owned parcel in the UNCONFIRMED chip',
    '6280 BLVD OF CHAMPIONS' in g and '6260 HIGHLAND CT' in g and '3' in g)
chk('board: the guess explains itself', 'AMBIGUOUS' in why)
chk('board: a single-candidate or empty row gets no list', LL._candidates_guess({'candidates': []}) == ('', ''))

# --- 4. lp_values must not ask Miami-Dade to price a Broward folio ----------------------------------
addrs = {'CACE-26-013184': {'folio': '494101210310', 'confidence': 'high', 'county': 'BROWARD'},
         'CACE-26-011727': {'folio': '494111190423', 'confidence': 'high', 'county': 'BROWARD', 'value': 286650},
         '2026-1-CA-01': {'folio': '0131230040010', 'confidence': 'high'}}
cache = {'494101210310': {'value': 0}}       # the poisoned MD-proxy miss a Broward folio gets today
md, cad = LV._plan(addrs, cache, every=False)
chk('values: MD folio still goes to the MD proxy', md == {'2026-1-CA-01': '0131230040010'})
chk('values: a value-less Broward folio goes to the cadastral, not Miami-Dade',
    cad == {'CACE-26-013184': '494101210310'})
chk('values: an old MD-proxy $0 miss does not block the cadastral lookup', 'CACE-26-013184' in cad)

# --- 5. Sheets CRM: no fake zeros, no auction section, no 9999 on a lis pendens ---------------------
lp_row = {'case': 'CACE-26-013184', 'st': 'LP', 'stage': 'LP', 'addr': '6260 HIGHLAND CT, North Lauderdale, FL 33068',
          'owners': 'MONTAS,LYNSIA', 'county': 'BROWARD', 'value': 0, 'judg': 0, 'auction': '', 'days': 9999,
          'filed': '8/14/2026', 'plaintiff': 'US BANK TRUST NATIONAL ASSN'}
brl = {'CACE-26-013184': {'liens': [
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
rows = S.build_rows([lp_row], {'CACE-26-013184': {'status': 'Contacted'}}, {}, {})
crm = dict(zip(S.HEADERS, rows[0])) if rows else {}
chk('crm: Days column is blank for the no-sale sentinel', crm.get('Days') == '')

if fails:
    for f in fails:
        print('FAIL:', f)
    print('%d check(s) failed' % len(fails))
    sys.exit(1)
print('lp ambiguity + crm display: all checks pass')
