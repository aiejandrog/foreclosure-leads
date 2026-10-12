"""other_liens — the non-mortgage liens on a Broward or Palm Beach owner, ROW BY ROW, priced or not.

WHY (2026-10-12, Alex: "find any hidden liens on the case or tied to the property that might
interfere with equity and make a false equity property a more realistic property evaluation").

Miami-Dade's chain (records_liens.analyze) keeps every federal tax lien, city or code lien,
association claim and money judgment it finds as a row in `other`, and counts the open ones with no
published amount in `other_open_unpriced`, which keeps a VERIFIED verdict from being a FACT
(equity_state.state_of). Broward and Palm Beach share broward_liens.analyze, and that analyzer only
ever SUMMED the non-mortgage liens that carried a dollar figure:

    amt = _num(d.get('Consideration'))
    if amt <= 0:
        continue

Broward's index lists most city and county liens with no consideration (the real chain behind
_brlinktest.py has two city liens at $0), and Palm Beach's index publishes no amounts at all. So an
open IRS lien, a city lien or a PACE assessment on a Broward or Palm Beach owner vanished, and a
chain with nothing else on it could read VERIFIED CLEAR. A mechanic's lien or a private money
judgment vanished even when priced, because it matched none of the three buckets.

`rows(...)` reads the SAME index rows the analyzer already has (no new search, no PDF, no OCR) and
returns:

    other                 one row per instrument: date, document, kind, holder, book/page, amount
                          (None when the index has none), OPEN / RELEASED, and how it was released
    other_open_unpriced   open claims with no published amount: the total is not established
    add                   {'hoa_open', 'code_open', 'irs_open'} amounts to ADD to the analyzer's
                          sums: priced open claims the old loop never counted
    hoa_own_in            the plaintiff association's own claim is still inside hoa_open (the old
                          loop counted it), so the board must keep netting the judgment

Only ever adds debt. It never removes a lien the old loop counted and never lowers a sum.

Kinds use Miami's vocabulary (records_liens, lien_census): irs, state_tax, association, code,
judgment, other, lis_pendens. A PACE assessment is `code` with `pace: True` (it is collected on the
tax bill, like a municipal lien); a construction (mechanic's) lien is `other` with `mechanic: True`.

RELEASES. A satisfaction that quotes the lien's book/page frees that lien. In Broward a release
also frees one lien of its own holder recorded on or before it, oldest lien first. In Palm Beach
only the book/page quote counts (Landmark rows carry no link to pair by). A release that cannot be tied to a lien this way frees nothing: an unpaired lien stays
OPEN. Partial releases never free a lien.

DIRECTION. The two indexes put the owner on opposite sides of a lien (Broward 'To', Palm Beach
'FROM'), so a lien the owner holds against someone else cannot be told apart reliably. Every claim
on the owner's own rows is counted: too much debt costs a lead, too little reaches a homeowner.

NAMESAKES. The rows are the analyzer's exact-name rows (broward_liens._exact_docs): Broward's search
is by owner name only, so a person-wide lien (a tax lien, a money judgment) under the same exact
name can belong to someone else. `anchor` says what tied each row to the owner ('person' = name
only, 'parcel_index' = the county index listed a parcel on the row).
"""
import re

# a claim against the owner: a lien, a judgment, a tax warrant, a special assessment
CLAIM_DOC_RE = re.compile(r'\bLIEN\b|\bJUDG|\bWARRANTS?\b|\bASSESSMENT\b', re.I)
# documents that carry those words but are not a claim: releases, waivers, the suit's own notice,
# a deed ('WARRANTY'), an assignment of a lien to a new holder, a notice of commencement
NOT_CLAIM_RE = re.compile(r'RELEASE|SATISF|PARTIAL|WAIV|CANCEL|DISCHARG|TERMINAT|REVOK|LIS\s*PEND|'
                          r'COMMENCEMENT|SUBORD|AFFIDAVIT|WARRANTY|ASSIGN|MODIF|CONTEST|WITHDRAW|AMEND|EXTEN|CONTINU|DISMISS|ASSESSMENT\s+MORT|'
                          r'VACAT|FINANCING\s+STATEMENT|CERTIFICATE\s+OF\s+TITLE|DEED', re.I)
REL_DOC_RE = re.compile(r'SATISF|RELEASE|REVOKE|TERMINAT|CANCELLATION|DISCHARGE', re.I)

PACE_RE = re.compile(r'\bPACE\b|GREEN\s+FINANCE|CLEAN\s+ENERGY|YGRENE|RENEW\s+FINANCIAL|RENOVATE\s+AMERICA|'
                     r'ENERGY\s+IMPROVEMENT|FLORIDA\s+RESILIENCY|ENERGY\s+FINANCE', re.I)
IRS_RE = re.compile(r'INTERNAL\s+REV|UNITED\s+STATES|\bIRS\b|\bU\s?S\s+(?:DEPT|DEPARTMENT)', re.I)
STATE_TAX_RE = re.compile(r'DEP(?:AR)?T(?:MENT)?\.?\s+OF\s+REVENUE|\bDOR\b|REEMPLOYMENT', re.I)
CODE_RE = re.compile(r'\bCITY\s+OF\b|\bCOUNTY\b|\bTOWN\s+OF\b|\bVILLAGE\s+OF\b|CODE\s+ENF|MUNICIPAL|'
                     r'UTILIT|\bWATER\b|\bSEWER|SPECIAL\s+MAGISTRATE|SCHOOL\s+BOARD|STATE\s+OF\s+FLORIDA|'
                     r'\bBROWARD\b|PALM\s+BEACH', re.I)
ASSOC_STRONG_RE = re.compile(r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|PROPERTY\s+OWNERS?|TOWNHO', re.I)
BANKISH_RE = re.compile(r'\bBANK\b|CREDIT\s+UNION|\bMORTGAGE\b|\bLOANS?\b|\bFINANCIAL\b|\bFUNDING\b|'
                        r'\bSAVINGS\b|\bFEDERAL\b|NATIONAL\s+ASS', re.I)
MECHANIC_DOC_RE = re.compile(r'CONSTRUCTION|MECHANIC', re.I)
MECHANIC_HOLDER_RE = re.compile(r'ROOF|PLUMB|ELECTRIC|\bPOOLS?\b|AIR\s+COND|\bA/?C\b|HVAC|CONTRACT|'
                                r'CONSTRUCT|BUILDERS?|PAINT|FENC|WINDOWS?|SOLAR|IMPACT|REMODEL', re.I)


def _kind(doc, holder):
    """(kind, extra flags) from the document type and the holder's name."""
    import broward_liens as BL
    if 'LIS PEND' in doc:
        return 'lis_pendens', {}
    if PACE_RE.search(holder):
        return 'code', {'pace': True}
    if IRS_RE.search(holder):
        return 'irs', {}
    if STATE_TAX_RE.search(holder) or re.search(r'\bWARRANTS?\b', doc):
        return 'state_tax', {}
    if (ASSOC_STRONG_RE.search(holder)
            or ((BL._HOA_RE.search(holder) or BL._ASSN_RE.search(holder)) and not BANKISH_RE.search(holder))):
        return 'association', {}
    if CODE_RE.search(holder):
        return 'code', {}
    if MECHANIC_DOC_RE.search(doc) or (re.search(r'\bLIEN\b', doc) and MECHANIC_HOLDER_RE.search(holder)):
        return 'other', {'mechanic': True}
    if 'JUDG' in doc:
        return 'judgment', {}
    return 'other', {}


def is_claim(doc, holder=''):
    """A document that puts a debt on the owner. A PACE notice is one (it is how PACE is recorded)."""
    doc = (doc or '').upper()
    if NOT_CLAIM_RE.search(doc):
        return False
    return bool(CLAIM_DOC_RE.search(doc)) or bool(PACE_RE.search(holder or '') and 'NOTICE' in doc)


def _digits(s):
    return re.sub(r'\D', '', str(s or ''))


def _nums(s):
    """The numbers in a text, leading zeros dropped ('ORB 32001 PG 0101' -> [32001, 101])."""
    return [int(x) for x in re.findall(r'\d+', str(s or ''))]


def rows(pool, lead_case='', legacy_counted=(), legacy_released=(), holder_release=True, judgment=0):
    """pool: the analyzer's de-duplicated exact-name rows (broward_liens._dedupe(_exact_docs(...))).
    legacy_counted / legacy_released: id()s of rows the old summing loop counted, or skipped as
    released, so nothing it counted is counted twice and nothing it counted is dropped.
    holder_release=False (Palm Beach): only a release that quotes the lien's book/page frees it."""
    import broward_liens as BL
    lc = (lead_case or '').strip().upper()
    legacy_counted, legacy_released = set(legacy_counted), set(legacy_released)
    # who sues in THIS case: the holder on its own lis pendens / judgment rows
    plaintiffs = set()
    for d in pool:
        if lc and (d.get('CaseNumber') or '').strip().upper() == lc:
            h = BL._inst(d.get('CrossPartyName'))
            if h:
                plaintiffs.add(h)
    lenders = {BL._inst(d.get('CrossPartyName')) for d in pool
               if (d.get('DocTypeDescription') or '').upper().startswith('MORTGAGE')} - {''}
    rels = []
    for d in pool:
        doc = (d.get('DocTypeDescription') or '').upper()
        if REL_DOC_RE.search(doc) and 'PARTIAL' not in doc:
            rels.append({'d': BL._jsdate(d.get('RecordDate')), 'h': BL._inst(d.get('CrossPartyName')),
                         'nums': _nums(' '.join(str(d.get(k) or '') for k in
                                                ('DocLegalDescription', 'CaseNumber', 'CrossPartyName'))),
                         'used': False})
    out, live = [], []
    for d in pool:
        doc = (d.get('DocTypeDescription') or '').upper().strip()
        holder = (d.get('CrossPartyName') or '').strip()
        if not (is_claim(doc, holder) or 'LIS PEND' in doc):
            continue
        kind, extra = _kind(doc, holder)
        amt = BL._num(d.get('Consideration'))
        case = (d.get('CaseNumber') or '').strip().upper()
        h = BL._inst(holder)
        own = bool(lc) and (case == lc or (h in plaintiffs and 'JUDG' not in doc and kind not in ('irs', 'state_tax')))
        dt = BL._jsdate(d.get('RecordDate'))
        dt = '' if dt.startswith('0000') else dt
        row = {'d': dt, 'doc': doc[:40], 'kind': kind, 'party': holder[:40],
               'bp': d.get('BookPage', '') or '', 'amt': round(amt) if amt > 0 else None, 'st': 'OPEN',
               'anchor': 'parcel_index' if len(_digits(d.get('ParcelNumber'))) >= 8 else 'person'}
        row.update(extra)
        # a lender's judgment is usually the foreclosure itself (this case, an earlier refiled one,
        # or a servicer's name): already in `liens`, so adding its amount would count the first twice
        lender_suit = (kind == 'judgment' and not own and BANKISH_RE.search(holder)
                       and (h in plaintiffs or h in lenders
                            or (judgment and amt > 0 and abs(amt - judgment) <= 0.02 * judgment)))
        if lender_suit:
            row['lender_suit'] = True
        if own:
            row['own_case'] = True
        if id(d) in legacy_counted:
            row['counted'] = 'legacy'
        elif id(d) in legacy_released:
            row['legacy_released'] = True
        out.append(row)
        if kind != 'lis_pendens' and not own and not lender_suit:
            live.append((row, h))
    # Palm Beach: a satisfaction that quotes the lien's book/page frees exactly that lien
    for row, _h in live:
        bp = _nums(row['bp'])
        if len(bp) != 2 or bp[0] < 1000:
            continue
        for r in rels:
            if (not r['used'] and bp[0] in r['nums'] and bp[1] in r['nums']
                    and (not row['d'] or not r['d'] or r['d'] >= row['d'])):
                r['used'] = True
                row['st'] = 'RELEASED'
                row['released_by'] = 'book/page'
                break
    # then: a release by the same holder, on or after the lien, one release per lien, oldest first
    for row, h in sorted(live, key=lambda x: x[0]['d'] or '') if holder_release else ():
        if row['st'] != 'OPEN' or not h or not row['d']:
            continue
        for r in sorted(rels, key=lambda x: x['d'] or ''):
            if not r['used'] and r['h'] == h and r['d'] and r['d'] >= row['d']:
                r['used'] = True
                row['st'] = 'RELEASED'
                row['released_by'] = 'holder release'
                break
    add = {'hoa_open': 0, 'code_open': 0, 'irs_open': 0}
    unpriced = 0
    hoa_own_in = False
    for row in out:
        if row.get('own_case') and row['kind'] == 'association' and row.get('counted') == 'legacy':
            hoa_own_in = True
        if row['kind'] == 'lis_pendens' or row.get('own_case') or row.get('lender_suit') or row['st'] != 'OPEN' \
                or row.get('counted') == 'legacy':
            continue
        if not row['amt']:
            unpriced += 1
            row['counted'] = 'unpriced'
            continue
        key = ('irs_open' if row['kind'] in ('irs', 'state_tax')
               else 'hoa_open' if row['kind'] == 'association' else 'code_open')
        add[key] += row['amt']
        row['counted'] = key
    return {'other': out, 'other_open_unpriced': unpriced, 'add': add, 'hoa_own_in': hoa_own_in}
