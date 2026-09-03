#!/usr/bin/env python
"""diligence_flags.py — Jesse's 3-5 minute pre-contact diligence, expressed as DATA.

WHY THIS EXISTS (Jesse's critique, 2026-08-14 — it has now happened TWICE)
We routed a closer to pitch an equity position to somebody who no longer owned the house.
Milouse Joseph, 8208 NW 57 PL Tamarac. Our case was the MORTGAGE foreclosure CACE-24-006635
(value $325,610 - judgment $268,758 = ~$56.8k of owner equity, eqfake False, days 5, tier B).
Her HOA had already foreclosed in a SEPARATE case, CONO-23-008222, and a Certificate of Title
moved the house to Cynthia Sewell for $20,100 in May 2026. Our docket looked clean the whole time.

Jesse's manual method, and where each tell lives:
  #1  Only deep-dive a lead that shows a REAL equity position.  -> needs_deep_dive()
  #2  Look up OTHER cases under the owner's name.               -> checklist() step 1-2
                                                                   (live discovery = sibling_cases.py)
  #3  If the HOA / association / "maintenance corp" is a CO-DEFENDANT on our mortgage
      foreclosure, an association lien/case almost certainly exists — go find the
      association's OWN case. This is the cheapest early warning we have.  -> hoa_codefendant()
  #4  A recent sale on a distressed property is a RED FLAG, not a comp.   -> RECENT_SALE flag

WHY THIS EXISTS, PART TWO (Sisavath, 2026-08-26 — the OTHER way to burn a closer)
Milouse was "the owner is already gone". Sisavath is "the equity was never there".
Case 502024CA010265XXXAMB, 4118 41st Way, West Palm Beach. She paid $315,000 on 8/31/2023; the
house is worth roughly $273,683 today. Her board row carries value $225,675 and judg $20,323.36 —
and that judgment is the HOA'S, not the mortgage. The ~$298,000 first and the HUD partial claim
recorded 7/2/2024 appear in NO field of that row. So the board did the only arithmetic available to
it ($225,675 - $20,323) and printed 91% equity, about $205,351 of it. `eqfake` was ALREADY True:
this repo had already concluded the number was phantom, and the entire consequence of that flag was
a letter painted on a card. A closer pitched the 91% anyway.

Three more tells, each of which stops that call on its own:
  #5  Debt the row can actually see >= the value it prints.       -> UNDERWATER flag (critical)
      Not an equity deal. Nobody pitches one.
  #6  They paid at or above today's value, recently. Equity is    -> PURCHASE_ANCHOR flag (high)
      then arithmetically impossible whatever the equity field says.
  #7  eqfake means OUR OWN BOARD says the equity number is a      -> EQ_UNRELIABLE flag (high)
      gross upper bound. That has to stop a human, not decorate a card.

AND THE STRUCTURAL BUG THE THREE OF THEM EXPOSED
The old is_hold() asked needs_deep_dive() first and returned False when the answer was no. But
deep_dive_reason() returns '' on an eqfake row BY DESIGN (L~510) — so the exact rows this repo has
already judged untrustworthy were the rows the gate refused to look at. Hold/release now lives in
contact_gate(), which reads the DATA, not the dive. is_hold() is a thin wrapper over it so there is
one policy and it cannot drift.

SCOPE / CONTRACT
  * PURE DATA IN, DATA OUT. No network, no file writes, no imports beyond the stdlib.
    The live check already exists and lives in ownership_gate.py / ownership_scan.py.
  * NEVER RAISES. Every public function is wrapped; a malformed / None / non-dict row returns
    the empty answer, never a traceback. This code runs inside make_tracker's bake loop, and a
    single bad row must not take the whole board down.
  * ADDITIVE. Nothing here hides, drops or kills a lead. It flags, explains and holds.

ROW SHAPES ACCEPTED (all three real shapes in this repo, verified against the live JSON):
  * Miami-Dade fat  (leads_final.json):   'Case #', 'defendants' (str), 'named', 'plaintiff',
                                          'market_value', 'judgment', 'equity_pct', 'eq_fake',
                                          'last_sale_date', 'bought_year', 'filing_year', 'owners'
  * Board slim / county (broward_leads.json, palmbeach_leads.json, lp_leads.json, and the dicts
    make_tracker builds): 'case', 'defs', 'named', 'plaintiff', 'value', 'judg', 'eq', 'eqfake',
                          'bought', 'bprice', 'lsd', 'st', 'county', 'folio', 'owners'/'oname'
  * ownership_gate stamps (baked by make_tracker ~L1917): 'title_status', 'title_flag',
                                                          'title_owner', 'title_evidence'

KNOWN DATA CEILING — READ THIS BEFORE TRUSTING A CLEAN RESULT
  Tell #3 can only be evaluated where party data exists. Measured 2026-08-14:
      leads_final.json (Miami-Dade)  229/277 rows carry 'defendants'  -> 86 have an association co-def
      broward_leads.json             0/219   ('defs' is a hardcoded '' in county_leads.py:235)
      palmbeach_leads.json           0/210   (same)
      lp_leads.json                  0/724   (lp_leads.py:166, same)
  So on the exact county where Milouse happened we are BLIND to tell #3. That is not silence —
  this module emits PARTIES_UNAVAILABLE on any deep-dive lead with no party data, so the operator
  sees "this check could not run" instead of a clean row. Fixing the ceiling is a scraper job
  (broward_plaintiff.classify() already returns a 'defendant' key that county_plaintiffs.py:85-87
  throws away); this module is ready for it the day it lands.

CLI
  python diligence_flags.py --selftest          # name-zoo truth table + malformed-row fuzz, no I/O
  python diligence_flags.py --scan              # counts across every *_leads.json in this folder
  python diligence_flags.py --case CACE-24-006635   # print the printable checklist for one lead
"""
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------------------------
# TUNING — every threshold here is the repo's OWN existing number, not a new invention.
# --------------------------------------------------------------------------------------------
# Jesse's rule #1 gate. TWO doors, OR'd, and the dollar door is the important one:
#
#   DEEP_DIVE_EQ_PCT = 30  mirrors the board's `eq30Only` filter (tracker_template.html ~L14467:
#                          `pct >= 30 && !r.eqfake && !isFlaggedDead && !notForModel`), which is
#                          this repo's existing operational definition of "there is real equity
#                          here". Using the same number means the STOP and the filter can never
#                          disagree about which leads matter.
#
#   DEEP_DIVE_EQ_USD = 25000  exists because a percent gate ALONE WOULD HAVE MISSED MILOUSE.
#                          Her row shows eq 17% — but 17% of $325,610 is $56,853, which is a
#                          full deal. Equity is spent in dollars, not percent, and the whole
#                          reason this module exists is a lead that a 30% filter would have
#                          waved through unexamined. $25k is the floor at which an equity deal
#                          survives closing costs on a Florida house (~8-10% of a $300k value);
#                          it also sits just above the repo's own STRONG-verdict profit floor of
#                          $20,000 (setVerdict, tracker ~L2872).
DEEP_DIVE_EQ_PCT = 30
DEEP_DIVE_EQ_USD = 25000

# Tell #4. A sale this recent on a property now in foreclosure is not a comp — it is either an
# investor flip, a rescue-scam transfer, or the certificate of title from the case that already
# took the house. 2 years matches the repo's own `tiny_recent` suspicion window family
# (foreclosure_leads.py:594 uses 15y for a different question; 2y is the "did this just move" one).
RECENT_SALE_YEARS = 2

SEV_CRITICAL, SEV_HIGH, SEV_MED, SEV_LOW = 'critical', 'high', 'med', 'low'
_SEV_RANK = {SEV_CRITICAL: 4, SEV_HIGH: 3, SEV_MED: 2, SEV_LOW: 1, '': 0}

# --------------------------------------------------------------------------------------------
# THE NAMING ZOO
# --------------------------------------------------------------------------------------------
# Charter guard WINS FIRST, exactly like foreclosure_leads.py:742 — "CITIBANK, NATIONAL
# ASSOCIATION" and "U.S. BANK TRUST COMPANY, N.A." are banks whose charter name ends in the same
# word an HOA uses. Both appear as real co-defendants in leads_final.json today.
_CHARTER_BANK = re.compile(
    r'NATIONAL\s+ASS(?:N|OC(?:IATION)?)|\bN\.\s?A\.|\bN\.?A\.?$|\bFSB\b|\bFEDERAL\s+SAVINGS|'
    r'CREDIT\s+UNION|\bBANK\b|\bBANKERS?\b|\bSAVINGS\b|\bMORTGAGE\b|SERVICING|\bLOANS?\b|'
    r'\bLENDING\b|FINANCIAL|\bFUNDING\b|FANNIE\s?MAE|FREDDIE\s?MAC|\bFNMA\b|\bFHLMC\b|\bMERS\b|'
    r'MORTGAGE\s+ELECTRONIC', re.I)

# Government / utility co-defendants are their own species (foreclosure_leads.py's _GOV_RE family).
# They are never the association we are hunting.
_GOV_PARTY = re.compile(
    r'UNITED\s+STATES|\bU\.?S\.?A\.?\b|\bIRS\b|INTERNAL\s+REVENUE|TREASURY|SECRETARY\s+OF|'
    r'\bSTATE\s+OF\b|\bCITY\s+OF\b|\bTOWN\s+OF\b|\bVILLAGE\s+OF\b|\bCOUNTY\b|TAX\s+COLLECTOR|'
    r'CLERK\s+OF|DEPARTMENT|\bDEPT\b|SHERIFF|CODE\s+ENFORCEMENT|\bHUD\b|HOUSING\s+AUTHORITY',
    re.I)

# Placeholder parties every Florida foreclosure names. Not people, not associations.
_NOISE_PARTY = re.compile(
    r'UNKNOWN|JOHN\s+DOE|JANE\s+DOE|ANY\s+AND\s+ALL|TENANT|OCCUPANT|\bPARTIES?\s+IN\s+POSSESSION|'
    r'ALL\s+OTHERS?|IN\s+POSSESSION', re.I)

# STRONG association tells — these words never appear in a bank or a person's name.
#   FAIRHAVEN 11 MAINTENANCE CORP           -> MAINTENANCE + corp suffix
#   SEAWIND CONDOMINIUM ASSOC INC   -> CONDOMINIUM
#   XYZ HOMEOWNERS ASSN                     -> HOMEOWNERS
#   MUTINY ON THE BAY CONDO ASSOC INC       -> CONDO ASSOC
#   Lago Mar Townhomes Association Inc      -> TOWNHOMES ASSOCIATION
#   SOMETHING MASTER ASSOCIATION            -> MASTER ASSOC
#   POA / HOA                               -> bare acronym, with or without periods
_ASSN_STRONG = re.compile(
    r'CONDOMINIUM|'
    r'\bCONDO\b(?=.{0,24}\bASS(?:N|OC))|'
    r'HOME\s?OWNERS?|'
    r'PROPERTY\s+OWNERS?|'
    r'\bH\.?\s?O\.?\s?A\.?\b|'
    r'\bP\.?\s?O\.?\s?A\.?\b|'
    r'MAINTENANCE(?=.{0,30}(?:\bCORP|\bCO\b|\bINC\b|\bASS(?:N|OC)|\bLLC\b|\bTRUST\b|$))|'
    r'MASTER\s+ASS(?:N|OC)|'
    r'TOWNHO(?:ME|USE)S?(?=.{0,24}\bASS(?:N|OC))|'
    r'\bVILLAS?\b(?=.{0,24}(?:\bASS(?:N|OC)|CONDOMINIUM))|'
    r'(?:COMMUNITY|RECREATION|NEIGHBORHOOD|CIVIC|HOMES?)\s+ASS(?:N|OC)|'
    r'\bCOOPERATIVE\b(?=.{0,24}(?:\bASS(?:N|OC)|\bINC\b))',
    re.I)

# WEAK tell — a bare "... ASSOCIATION INC" / "... ASSN" with no bank charter phrase. Reported with
# conf='assn' so the UI can say "probably the association" instead of asserting it.
# The lookbehind kills "NATIONAL ASSOCIATION" the way foreclosure_leads.py:749 does.
_ASSN_WEAK = re.compile(r'(?<!NATIONAL\s)\bASS(?:N|OC(?:IATION)?)S?\b', re.I)

_SPLIT = re.compile(r'\s*[;|]\s*|\s+AND\s+(?=[A-Z0-9])')

_COUNTY_LINKS = {
    'MIAMI-DADE': {
        'cases':   'https://www2.miamidadeclerk.gov/ocs/search.aspx',
        'records': 'https://onlineservices.miamidadeclerk.gov/officialrecords/StandardSearch.aspx',
        'pa':      'https://www.miamidade.gov/Apps/PA/propertysearch/',
        'clerk':   'Miami-Dade Clerk (OCS) party-name search',
    },
    'BROWARD': {
        'cases':   'https://www.browardclerk.org/Web2/CaseSearchECA/',
        'records': 'https://officialrecords.broward.org/AcclaimWeb/search/SearchTypeName',
        'pa':      'https://web.bcpa.net/BcpaClient/#/Record-Search',
        'clerk':   'Broward Clerk (Web2 CaseSearchECA) party-name search',
    },
    'PALM BEACH': {
        'cases':   'https://appsgp.mypalmbeachclerk.com/eCaseView/',
        'records': 'https://erec.mypalmbeachclerk.com/search/index?theme=.blue&section=searchCriteriaName&quickSearchSelection=',
        'pa':      'https://pbcpao.gov/Property/Search',
        'clerk':   'Palm Beach Clerk (eCaseView) party-name search',
    },
}
_DEFAULT_LINKS = {'cases': '', 'records': '', 'pa': '', 'clerk': 'the county clerk case search'}


# --------------------------------------------------------------------------------------------
# tiny safe helpers — none of these raise, ever
# --------------------------------------------------------------------------------------------
def _d(row):
    """Coerce anything into a dict we can .get() on."""
    return row if isinstance(row, dict) else {}


def _s(v):
    if v is None:
        return ''
    if isinstance(v, str):
        return v
    try:
        return str(v)
    except Exception:
        return ''


def _n(v):
    """Money/number out of anything: 0, None, '', '$225,577.00', '17%', 12.5 -> float."""
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        try:
            f = float(v)
            return f if f == f and abs(f) != float('inf') else 0.0   # NaN/inf guard
        except Exception:
            return 0.0
    t = re.sub(r'[^0-9.\-]', '', _s(v))
    if not t or t in ('-', '.', '-.'):
        return 0.0
    try:
        return float(t)
    except Exception:
        return 0.0


def _first(row, *keys):
    r = _d(row)
    for k in keys:
        v = r.get(k)
        if v not in (None, '', 0, [], {}):
            return v
    return ''


def case_of(row):
    return _s(_first(row, 'case', 'Case #', 'case_no', 'caseNumber')).strip()


def county_of(row):
    c = _s(_first(row, 'county')).upper().strip()
    if c:
        return 'PALM BEACH' if c.startswith('PALM') else c
    # infer from the case-number dialect when the row predates the county tag
    cs = case_of(row).upper()
    if re.match(r'^(CACE|CONO|COCE|COWE|COSO|CACO)[- ]', cs):
        return 'BROWARD'
    if re.match(r'^50\d{4}', cs):
        return 'PALM BEACH'
    if re.match(r'^\d{4}-\d{6}-(CA|CC)', cs):
        return 'MIAMI-DADE'
    return ''


def case_year(case):
    """Filing year out of ANY of the four case-number dialects in this repo.

    BUG THIS ALSO FIXES: sibling_cases.py:178 `_lead_year` does a bare `re.match(r'(\\d{4})', case)`
    and therefore returns 0 for every Broward ('CACE-24-006635') and Palm Beach
    ('502026CA000685XXXAMB') case number. `_apply_rules` (:199) then treats a SOLD sibling as
    non-recent and never sets `claimed` — the claim rule silently disables itself outside
    Miami-Dade. Import this instead: `from diligence_flags import case_year`.

      2026-002031-CA-01      -> 2026   (Miami-Dade)
      CACE-24-006635         -> 2024   (Broward, 2-digit)
      502026CA000685XXXAMB   -> 2026   (Palm Beach)
      2026-2833TD / 2026A00190 -> 2026 (tax deed / auction ids)
    """
    try:
        c = _s(case).upper().strip()
        if not c:
            return 0
        m = re.match(r'^(\d{4})', c)                       # MD, TD, A-ids
        if m and 1980 <= int(m.group(1)) <= 2100:
            return int(m.group(1))
        m = re.match(r'^[A-Z]{2,6}[- ](\d{2})[- ]', c)     # CACE-24-006635
        if m:
            return 2000 + int(m.group(1))
        # Palm Beach packs a 2-digit court code in front of the year: 502026CA000685XXXAMB.
        # This MUST be tried after (not before) the plain 4-digit read, and the plain read must
        # not short-circuit on an out-of-range value like '5020' — that is why the check above
        # tests the range inline instead of returning 0.
        m = re.match(r'^\d{2}(\d{4})', c)
        if m and 1980 <= int(m.group(1)) <= 2100:
            return int(m.group(1))
        m = re.search(r'(19|20)\d{2}', c)                  # last resort
        if m:
            return int(m.group(0))
        return 0
    except Exception:
        return 0


def _year_of(v):
    """Year out of '4/21/2016', '2016-04-21', '2016', 2016, '' -> int (0 when unknown)."""
    try:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            y = int(v)
            return y if 1900 <= y <= 2100 else 0
        t = _s(v).strip()
        if not t:
            return 0
        m = re.search(r'(19|20)\d{2}', t)
        return int(m.group(0)) if m else 0
    except Exception:
        return 0


def filing_year(row):
    """Best available filing year for the lead's OWN case."""
    r = _d(row)
    y = _year_of(_first(r, 'filing_year', 'filed', 'filedDate', 'filed_date'))
    return y or case_year(case_of(r))


def sale_year(row):
    """Year of the property's own most recent recorded sale."""
    r = _d(row)
    return _year_of(_first(r, 'last_sale_date', 'lsd', 'bought_year', 'bought', 'sale_date'))


# --------------------------------------------------------------------------------------------
# equity — mirrors the board's _basisOf / _ownerEqOf so this module and the UI agree
# --------------------------------------------------------------------------------------------
def basis_of(row):
    """'Worth today', same rule as tracker_template.html _basisOf() (L6203) + recompute()'s ARV gate:
    use the ARV only when conf=='ok' AND 0.7*county <= arv <= 2.5*county; otherwise county value."""
    r = _d(row)
    val = _n(_first(r, 'value', 'market_value', 'val'))
    arv = _n(r.get('arv'))
    conf = _s(_first(r, 'arvconf', 'arv_conf')).lower()
    if arv and val and conf == 'ok' and (0.7 * val) <= arv <= (2.5 * val):
        return arv
    return val


def owner_owed_of(row):
    """What the owner owes on the foreclosing instrument. Bake-time only — the board's runtime
    _payoffOf/_slien/_jlien operator overrides do not exist yet at this point in the pipeline, so
    this is deliberately the CONSERVATIVE (larger-equity) read. Any lien the chain later finds can
    only shrink equity, which can only ADD deep dives, never silently remove one."""
    r = _d(row)
    if _s(r.get('st')).upper() == 'TD':
        return _n(_first(r, 'obid', 'opening_bid', 'judg', 'judgment'))
    return _n(_first(r, 'payoff', 'judg', 'judgment', 'Final Judgment Amount'))


def owner_equity(row):
    """(dollars, percent) of owner equity. (0.0, 0.0) when there is no trustworthy basis."""
    try:
        b = basis_of(row)
        if b <= 0:
            return 0.0, 0.0
        eq = b - owner_owed_of(row)
        if eq < 0:
            eq = 0.0
        return round(eq, 2), round(eq / b * 100.0, 1)
    except Exception:
        return 0.0, 0.0


# --------------------------------------------------------------------------------------------
# SISAVATH TELL #5 — every dollar of debt THIS ROW can actually see
# --------------------------------------------------------------------------------------------
# The recorded-chain fields, baked onto slim by foreclosure_leads._fwd_flags (~L850) and the
# records_liens / batchdata_liens merge (~L1704, ~L1907). They are absent on the raw county files,
# which is exactly why Sisavath's $298k first was invisible: the ONLY debt figure on her row was an
# HOA judgment of $20,323, and 225,675 - 20,323 is a very convincing 91%.
_CHAIN_DEBT_KEYS = ('orsurvsen', 'orsurvfirst', 'orsurv', 'orjunior', 'orjuniors')


def known_debt_of(row):
    """Total debt this row can see, WITH provenance. Never raises.

      {'total': float, 'parts': [{'usd','kind','label'}], 'indicative': bool, 'how': str}
      kind: 'judgment' (court figure) | 'principal' (recorded, original) | 'balance' (estimated)

    WHY IT TAKES max() OVER THE CHAIN FIELDS AND NOT sum()
    foreclosure_leads._senior_surviving (L817) documents that the three chain engines disagree about
    what `surv` holds — records_liens/broward_liens put seniors+juniors in it, BatchData puts seniors
    only — and that the board double-subtracted one against the other and erased an entire $811,577
    first mortgage to $0 on 502024CA012300XXXAMB. `orjunior` and `orsurv` are literally assigned the
    same sum in records_liens.py:494. Adding these fields together INVENTS debt; taking the largest
    single figure under-counts it. UNDERWATER is critical and it kills a pitch, so it takes the
    under-count: it only fires when even the smallest defensible read of the debt swallows the value.

    WHY 'indicative' EXISTS — the repo's hard rule: A RECORDED PRINCIPAL IS NOT A PAYOFF
    Official Records gives us the number written on the mortgage the day it was signed
    (records_liens.py:410 reads 'amt' straight off the instrument). Years of payments have moved it
    and nothing in this file knows by how much. BatchData ships a current estimated BALANCE instead
    (batchdata_liens.py:108 'bal', stamped orconf='bd'). A final judgment is a court figure as of the
    judgment date and only grows from there under FS 55.03. So this total is a DIRECTION, not a
    quote, and any flag built on it has to say which figure it used and stop short of asserting a
    payoff. That is the difference between "this is not an equity deal" (true, sayable) and
    "you owe $298,000" (a number we do not have).

    Deliberately NOT counted: orhoa / orcode. When our case IS the association's or the city's, the
    judgment already IS that lien and counting both would double it — the same class of mistake as
    summing the chain fields.
    """
    try:
        r = _d(row)
        parts = []
        # A judgment we cannot read is not a debt figure, it is a blank. `ju` / judgment_unknown is
        # the repo's own marker for that, and asserting UNDERWATER off a blank would be a fresh lie.
        unknown = bool(r.get('ju') or r.get('judgment_unknown'))
        owed = owner_owed_of(r)
        if owed > 0 and not unknown:
            td = _s(r.get('st')).upper() == 'TD'
            parts.append({
                'usd': owed, 'kind': 'judgment',
                'label': (('the $%s opening bid' if td else
                           'the $%s judgment/payoff on the foreclosing case')
                          % format(int(owed), ','))})
        chain, conf = 0.0, _s(r.get('orconf')).lower()
        for k in _CHAIN_DEBT_KEYS:
            v = _n(r.get(k))
            if v > chain:
                chain = v
        if chain > 0:
            bal = (conf == 'bd')
            parts.append({
                'usd': chain, 'kind': 'balance' if bal else 'principal',
                'label': (('an estimated $%s still open on the recorded mortgage chain' if bal else
                           'a $%s mortgage on the recorded chain (ORIGINAL PRINCIPAL off the '
                           'instrument — not a payoff)')
                          % format(int(chain), ','))})
        total = round(sum(p['usd'] for p in parts), 2)
        return {'total': total, 'parts': parts,
                'indicative': any(p['kind'] == 'principal' for p in parts),
                'how': ' + '.join(p['label'] for p in parts)}
    except Exception:
        return {'total': 0.0, 'parts': [], 'indicative': False, 'how': ''}


# --------------------------------------------------------------------------------------------
# (1) TELL #3 — the HOA / association co-defendant
# --------------------------------------------------------------------------------------------
def _party_names(row):
    """Every co-party string on the case, EXCLUDING the plaintiff and the owner/defendant[0].

    Reads all the shapes this repo produces:
      'defendants' (MD fat, ';'-joined, defendant[0] already dropped, capped at 6 by
                    foreclosure_leads.py:357), 'defs' (board slim, same string),
      'named' ([{name,url}] or [str]), 'opart', 'parties', 'defendant' (broward_plaintiff shape).
    """
    r = _d(row)
    out, seen = [], set()

    def add(v):
        for part in _SPLIT.split(_s(v)):
            t = re.sub(r'\s+', ' ', part).strip(' ,.-\t')
            if len(t) < 3:
                continue
            k = t.upper()
            if k in seen:
                continue
            seen.add(k)
            out.append(t)

    for key in ('defendants', 'defs', 'opart', 'defendant', 'parties', 'co_defendants'):
        v = r.get(key)
        if isinstance(v, (list, tuple)):
            for it in v:
                add(it.get('name') if isinstance(it, dict) else it)
        else:
            add(v)

    nm = r.get('named')
    if isinstance(nm, (list, tuple)):
        for it in nm:
            add(it.get('name') if isinstance(it, dict) else it)

    pl = _s(_first(r, 'plaintiff', 'Plaintiff', 'pl')).upper().strip()
    own = _s(_first(r, 'owner_clean', 'oname', 'owners', 'rname')).upper()
    keep = []
    for t in out:
        u = t.upper()
        if pl and (u == pl or u in pl or pl in u):
            continue                     # the plaintiff is not a CO-defendant
        if own and u and (u in own):
            continue                     # the owner themselves
        keep.append(t)
    return keep


def _assn_match(name):
    """('' | matched-name, 'strong'|'assn') for ONE party string."""
    t = re.sub(r'\s+', ' ', _s(name)).strip()
    if len(t) < 3:
        return '', ''
    u = t.upper()
    if _NOISE_PARTY.search(u) or _GOV_PARTY.search(u):
        return '', ''
    strong = bool(_ASSN_STRONG.search(u))
    if not strong and _CHARTER_BANK.search(u):
        return '', ''                    # bank charter wins when there is no strong HOA word
    if strong:
        # even with a strong word, an explicit bank charter phrase beats it
        if re.search(r'NATIONAL\s+ASS(?:N|OC(?:IATION)?)|\bN\.\s?A\.|\bFSB\b|CREDIT\s+UNION|\bBANK\b', u):
            return '', ''
        return t, 'strong'
    if _ASSN_WEAK.search(u):
        return t, 'assn'
    return '', ''


def hoa_parties(row):
    """All association-looking co-parties: [{'name':..,'conf':'strong'|'assn'}]. Never raises."""
    try:
        seen, out = set(), []
        for p in _party_names(row):
            nm, conf = _assn_match(p)
            if nm and nm.upper() not in seen:
                seen.add(nm.upper())
                out.append({'name': nm, 'conf': conf})
        out.sort(key=lambda x: 0 if x['conf'] == 'strong' else 1)
        return out
    except Exception:
        return []


def hoa_codefendant(row):
    """JESSE'S TELL #3. Return the name of the HOA / condo association / homeowners association /
    maintenance corp / POA / master association named as a CO-DEFENDANT on this case, or ''.

    An association sitting as a co-defendant on a bank's mortgage foreclosure means the bank had to
    name it — which in practice means it holds a recorded lien, which in practice means it may have
    its own case. That is where the sale that already happened hides (Milouse: CONO-23-008222).
    Strongest match wins. Returns the name verbatim so a human can paste it into a clerk search.
    """
    try:
        hits = hoa_parties(row)
        return hits[0]['name'] if hits else ''
    except Exception:
        return ''


def plaintiff_is_assn(row):
    """True when OUR OWN case is the association's foreclosure (ftype/ctype 'HOA', or the plaintiff
    name itself is an association). Tell #3 is the OPPOSITE shape and must not fire on this."""
    try:
        r = _d(row)
        for k in ('ftype', 'ctype', 'case_type', 'clerk_case_type'):
            if _s(r.get(k)).upper().startswith('HOA'):
                return True
        if '-CC-' in case_of(r).upper():
            return True
        nm, _c = _assn_match(_first(r, 'plaintiff', 'Plaintiff'))
        return bool(nm)
    except Exception:
        return False


def title_status_of(row):
    """'clear' | 'transferred' | 'unverified' | '' (never checked). From ownership_gate stamps."""
    return _s(_d(row).get('title_status')).strip().lower()


# --------------------------------------------------------------------------------------------
# (3) JESSE'S RULE #1 — who earns the 3-5 minutes
# --------------------------------------------------------------------------------------------
def deep_dive_reason(row):
    """('' when no dive needed) else the one-line reason, so the UI never shows a bare boolean."""
    try:
        r = _d(row)
        # Already proven gone / already claimed: there is nothing left to verify, only a warning
        # to print. TITLE_TRANSFERRED / SIBLING_CLAIMED carry those rows.
        if title_status_of(r) == 'transferred':
            return ''
        if r.get('sibclaimed'):
            return ''
        # "a plain foreclosure we get paid to stop does not need it" — a judgment we cannot read is
        # not a real equity position, it is an unknown one.
        if r.get('ju') or r.get('judgment_unknown'):
            return ''
        # eqfake = this repo already KNOWS the headline equity is phantom (HOA case with a surviving
        # first, junior foreclosure, individual plaintiff, tiny-judgment ratio). Those never reach a
        # closer (_isCloser L2546) and are not a real equity position by our own definition.
        if r.get('eqfake') or r.get('eq_fake'):
            return ''
        if r.get('vac'):
            return ''                     # vacant land: notForModel, no homeowner to protect
        usd, pct = owner_equity(r)
        if usd <= 0:
            return ''
        if pct >= DEEP_DIVE_EQ_PCT:
            return '%d%% owner equity (>= %d%%) — Jesse rule #1: verify before anyone is contacted' % (
                round(pct), DEEP_DIVE_EQ_PCT)
        if usd >= DEEP_DIVE_EQ_USD:
            return '$%s of owner equity (>= $%s) — a real deal at %d%%; the Milouse row was 17%%' % (
                format(int(usd), ','), format(DEEP_DIVE_EQ_USD, ','), round(pct))
        return ''
    except Exception:
        return ''


def needs_deep_dive(row):
    """JESSE'S RULE #1. True when this lead shows a REAL equity position and therefore earns the
    mandatory 3-5 minute check BEFORE a closer dials or a rep drives.

    THRESHOLD AND WHY (see DEEP_DIVE_EQ_* above):
      owner equity >= 30% of basis   (the board's own eq30Only definition of real equity)
        OR  owner equity >= $25,000  (because Milouse was 17% / $56,853 — a percent-only gate
                                      would have waved through the exact lead that burned us)
      AND not already-known-phantom (eqfake), not judgment-unknown, not vacant land,
      AND not already proven dead (title transferred / sibling claimed — those get a warning,
          not a dive; there is nothing left to verify).
    """
    return bool(deep_dive_reason(row))


# --------------------------------------------------------------------------------------------
# (2) THE FLAGS
# --------------------------------------------------------------------------------------------
def _flag(code, sev, msg, action='', party=''):
    return {'code': code, 'sev': sev, 'msg': msg, 'action': action, 'party': party}


def is_tax_deed_row(row):
    """True when this lead is a TAX DEED sale rather than a mortgage foreclosure.

    Reads the several field names the counties and scrapers actually use, because there is no one
    canonical key: Miami-Dade's auction feed writes 'Auction Type': 'TAXDEED' and 'sale_type': 'TD',
    while the enricher writes case_type 'Tax Deed'. buybox.is_tax_deed() is the same predicate for
    the acquisition side; both are small and neither imports the other, so this file keeps its "no
    cross-module import in the flag path" property.
    """
    try:
        # 'st' AND 'ctype' ARE LOAD-BEARING, and leaving them out made this whole flag dead code.
        # make_tracker bakes the diligence gate onto the SLIM board row (foreclosure_leads.py:2513),
        # and slim renames sale_type -> 'st' and case_type -> 'ctype'. Measured on the 2,050-row
        # board twin: 174 rows carry st == 'TD', and the tuple below without 'st'/'ctype' fired on
        # exactly 0 of them. The flag was shipped, committed, and protected nothing on any surface a
        # human sees. 'st' is also the ONLY key that works for Broward/Palm Beach, whose county rows
        # hardcode ctype 'Bank/Mortgage' on tax deeds (140 of the 174).
        u = ' '.join(_s(_d(row).get(k)) for k in
                     ('sale_type', 'st', 'case_type', 'ctype', 'Auction Type', 'auction_type',
                      'clerk_case_type')).upper()
        return 'TAX DEED' in u or 'TAXDEED' in u.replace(' ', '') or bool(re.search(r'\bTD\b', u))
    except Exception:
        return False


def risk_flags(row):
    """Structured risk flags, most severe first. Never raises; returns [] on a malformed row.

    Each flag: {'code','sev','msg','action','party'}
      code    stable machine name
      sev     'critical' | 'high' | 'med' | 'low'
      msg     one plain-English sentence a closer can read out loud
      action  what to actually DO about it (the 3-5 minute step)
      party   the association / owner / third-party name involved, when there is one
    """
    try:
        r = _d(row)
        out = []
        dive = needs_deep_dive(r)
        ts = title_status_of(r)
        cy = filing_year(r)
        sy = sale_year(r)
        county = county_of(r)
        links = _COUNTY_LINKS.get(county, _DEFAULT_LINKS)

        # ---- TITLE_TRANSFERRED — the ownership_gate already proved it. Highest severity there is.
        if ts == 'transferred':
            who = _s(_first(r, 'title_owner')) or 'someone else'
            out.append(_flag(
                'TITLE_TRANSFERRED', SEV_CRITICAL,
                'The county appraiser shows the current owner of record is %s, NOT the person this '
                'case is against. The house is already gone — do not call, do not knock.' % who,
                'Stop. Mark it dead. If you want the story, find the OTHER case that issued the '
                'certificate of title (%s).' % (links['clerk']),
                who))

        # ---- TAX_DEED_SALE — a different animal wearing a foreclosure lead's clothes.
        # Nothing in the contact path tested for this until 2026-08-31: 34 rows on the board, 15 of
        # them ranked TIER A, sitting in the dial pool. Three things are wrong with calling one:
        #   1. The money figure is an OPENING BID (tax certificate + fees), not a payoff. It lands
        #      in the same 'judgment' field a court-ordered number does, so value-minus-debt printed
        #      up to 97% "equity" — $22.8M of room board-wide that does not exist. The senior
        #      mortgage is not on the row at all.
        #   2. The record owner is usually an LLC or an investor, not a distressed homeowner. The
        #      advisor script — "I am not trying to buy your house", the MARS disclosure, the whole
        #      language law — is written for a person about to lose the place they live in.
        #   3. It is not a doorstep deal in the first place. It is a competitive cash auction that
        #      bids toward market, and the winner still needs quiet title to resell.
        # Every one of these rows already carried a `warning` field saying exactly this. The warning
        # was written, displayed, and read by nothing that decides who gets called.
        if is_tax_deed_row(r):
            out.append(_flag(
                'TAX_DEED_SALE', SEV_HIGH,
                'This is a TAX DEED sale, not a mortgage foreclosure. The dollar figure on it is '
                'the opening bid, not a payoff, so any equity number shown for it is meaningless — '
                'the mortgage is not on this row. The owner of record is usually an LLC, not a '
                'family losing their home, and the advisor script does not fit.',
                'Do not dial it as a homeowner lead. If you want it, work it as an auction: pull '
                'the surviving liens (IRS 120-day right of redemption, municipal, HOA) and price '
                'the quiet title (%s).' % (links['clerk'])))

        # ---- SIBLING_CLAIMED — sibling_cases.py already found the case that took it (MD only today)
        if r.get('sibclaimed'):
            sib = ''
            try:
                sib = _s((r.get('sib') or [{}])[0].get('case'))
            except Exception:
                sib = ''
            out.append(_flag(
                'SIBLING_CLAIMED', SEV_CRITICAL,
                'Another foreclosure case on this same owner already sold and issued a certificate '
                'of title%s. This property belongs to a third party.' % ((' (%s)' % sib) if sib else ''),
                'Stop. Nothing to sell, nobody to help.', sib))

        # ---- UNDERWATER — Sisavath tell #5. The debt the row can SEE already eats the value.
        # Fires with no reference to `dive`, on purpose: the dive gate is the thing that waved
        # Sisavath through. A lead whose known debt exceeds its value is not a deep dive we owe, it
        # is a pitch that must not happen, and that is true whether or not anyone calls it "equity".
        basis = basis_of(r)
        debt = known_debt_of(r)
        if basis > 0 and debt['total'] > 0 and debt['total'] >= basis:
            out.append(_flag(
                'UNDERWATER', SEV_CRITICAL,
                'Known debt of $%s is at or above the $%s this row calls the value — the owner is '
                'underwater by about $%s. There is no equity position here to buy and none to pitch. '
                'Counted: %s.%s'
                % (format(int(debt['total']), ','), format(int(basis), ','),
                   format(int(max(0.0, debt['total'] - basis)), ','), debt['how'],
                   (' This comparison is INDICATIVE, not a payoff — a recorded principal is the '
                    'amount written on the instrument the day it was signed, and nothing here knows '
                    'what has been paid down since. It is enough to stop an equity pitch; it is not '
                    'enough to quote.' if debt['indicative'] else
                    ' The judgment figure grows with statutory interest (FS 55.03), so the real debt '
                    'is this or larger, never smaller.')),
                'Do not say an equity number out loud. Order the real payoff / reinstatement figure '
                'in writing first. If the debt holds up, the conversation is a short sale or a '
                'modification — Sisavath was pitched an equity deal on a house whose $20,323 '
                'judgment was the HOA\'s, with a ~$298,000 first and a HUD partial claim behind it.'))

        # ---- HOA_CODEFENDANT — Jesse's tell #3, the cheap early warning
        assns = hoa_parties(r)
        if assns and not plaintiff_is_assn(r):
            nm = assns[0]['name']
            weak = assns[0]['conf'] != 'strong'
            sev = SEV_HIGH if dive else SEV_MED
            out.append(_flag(
                'HOA_CODEFENDANT', sev,
                '%s is a CO-DEFENDANT on our foreclosure%s. A bank only has to name an association '
                'when the association holds a recorded lien — which usually means it has, or can '
                'open, its OWN foreclosure case. That is where an already-completed sale hides '
                '(Milouse: our case was clean, the HOA case CONO-23-008222 had already taken the '
                'house).' % (nm, ' (name match is weak — confirm it is the association)' if weak else ''),
                'Search "%s" as PLAINTIFF at %s and look for a case against this owner. If one '
                'exists, check it for a Certificate of Title BEFORE anyone is contacted.'
                % (nm, links['clerk']),
                nm))
            for extra in assns[1:3]:
                out.append(_flag(
                    'HOA_CODEFENDANT_ALSO', SEV_LOW,
                    'A second association is also named on this case: %s.' % extra['name'],
                    'Search it as plaintiff too — a condo can sit under a master association AND a '
                    'sub-association, and either one can foreclose.',
                    extra['name']))

        # ---- PARTIES_UNAVAILABLE — the honest hole. Broward/PB/LP carry no defendant list at all.
        if dive and not assns and not _party_names(r):
            out.append(_flag(
                'PARTIES_UNAVAILABLE', SEV_MED,
                'We have NO co-defendant list for this lead, so the HOA-co-defendant check could '
                'not run. This is the county and the exact blind spot that produced the Milouse '
                'miss — treat "no association flagged" here as "unknown", not as "clean".',
                'Open the case at %s and read the style/parties yourself; a condo or HOA in the '
                'caption is the trigger to go look for its own case.' % links['clerk']))

        # ---- RECENT_SALE — Jesse's tell #4
        if sy and cy:
            if sy >= cy:
                out.append(_flag(
                    'RECENT_SALE', SEV_HIGH,
                    'The property records a sale in %d, on or after this case was filed (%d). On a '
                    'distressed property that is usually a certificate of title, a tax deed, or a '
                    'rescue-scam transfer — not an arms-length sale.' % (sy, cy),
                    'Pull the deed at %s and read WHO it conveyed to and under WHAT instrument '
                    'before treating this owner as the owner.' % (links['records'] or 'the county official records'),
                    _s(_first(r, 'title_owner'))))
            elif (cy - sy) <= RECENT_SALE_YEARS:
                px = _n(_first(r, 'bprice', 'last_sale_price'))
                out.append(_flag(
                    'RECENT_SALE', SEV_MED,
                    'The owner bought in %d, only %d year(s) before the foreclosure was filed'
                    '%s. A fresh purchase that is already in foreclosure means little paid-down '
                    'principal, and the sale price is a red flag, not a comp.'
                    % (sy, max(0, cy - sy), (' for $%s' % format(int(px), ',')) if px else ''),
                    'Confirm the equity is real against the recorded mortgage amount, not against '
                    'the county roll value.'))

        # ---- PURCHASE_ANCHOR — Sisavath tell #6. What they PAID beats what we ESTIMATE.
        # RECENT_SALE (above) says "a fresh sale is suspicious". This says something harder and
        # arithmetic: they paid at or above what this row thinks the house is worth, so there is no
        # room for equity no matter what the equity field prints. Sisavath: $315,000 in Aug 2023,
        # worth about $273,683 — and the board still showed 91%.
        # WINDOW: reuses RECENT_SALE_YEARS deliberately rather than inventing a number. The purchase
        # price only anchors while little principal has been paid down; past that the two facts drift
        # apart and this stops being evidence. An older overpay is a real hole this does not cover —
        # say that out loud rather than pretending the window is a conclusion.
        # STRICTLY BEFORE THE FILING (sy < cy), and that is not fussiness. A sale recorded in or
        # after the filing year is at least as likely to be a certificate of title, a tax deed or a
        # rescue-scam transfer as a purchase — measured on the live board, the only such row is
        # 2026A00257 at "$4,200,000" against a $2,205,000 value. Calling that "the owner paid" would
        # print a fabrication in a closer's voice, which is the exact failure this file exists to
        # stop. RECENT_SALE already carries that case at SEV_HIGH and says the true thing about it.
        px = _n(_first(r, 'bprice', 'last_sale_price', 'sale_price'))
        if px > 0 and basis > 0 and sy and cy and sy < cy and (cy - sy) <= RECENT_SALE_YEARS \
                and px >= basis:
            out.append(_flag(
                'PURCHASE_ANCHOR', SEV_HIGH,
                'The owner paid $%s for this in %d — at or above the $%s this row calls today\'s '
                'value. You cannot buy at or above market, hold it %d year(s), and have meaningful '
                'equity. Whatever number the equity field shows was computed against the county roll '
                'and one judgment, not against what this owner actually owes.'
                % (format(int(px), ','), sy, format(int(basis), ','), max(0, cy - sy)),
                'Treat the equity on this row as fiction until the recorded mortgage says otherwise. '
                'Pull the deed AND the mortgage at %s — the purchase price tells you roughly what '
                'they borrowed; the roll value tells you nothing about it.'
                % (links['records'] or 'the county official records')))

        # ---- SOLD_ABOVE_VALUE — the same arithmetic as PURCHASE_ANCHOR, off the one field that
        # actually survives on the rows that need it. PURCHASE_ANCHOR needs a sale YEAR (bought /
        # last_sale_date), and the county that publishes no dollar amounts also publishes no sale
        # date onto our rows: Sisavath's live Palm Beach row carries bought=0, bprice=0, lsd=None —
        # and zstatus='SOLD' with zprice=315000 against a $225,675 value. The $315,000 she paid was
        # sitting on the row, two keys away, while three rules written for her all failed to see it.
        #
        # NO DATE, AND THAT IS SURVIVABLE HERE. listing_status.py's own docstring says zprice is
        # "asking price when LISTED/PENDING (0 otherwise)" — but fetch_status keeps it for SOLD
        # (`price = price if status == 'SOLD' else 0`), so on a SOLD row this is a CLOSED price.
        # The undated comparison still means something because the failure mode is one-directional:
        # a distress sale, a certificate of title or a tax deed comes in BELOW market, so it cannot
        # manufacture this flag. Only a real sale at or above today's value trips it.
        # It says "the last recorded sale", never "the owner paid" — we do not know the date, and
        # inventing one in a closer's voice is the exact failure this file exists to stop.
        _zsold = str(_first(r, 'zstatus')).strip().upper() == 'SOLD'
        _zpx = _n(_first(r, 'zprice'))
        if _zsold and _zpx > 0 and basis > 0 and _zpx >= basis:
            out.append(_flag(
                'SOLD_ABOVE_VALUE', SEV_HIGH,
                'The last recorded sale of this property was $%s — $%s ABOVE the $%s this row '
                'calls today\'s value. Whatever the equity field prints, the market has already '
                'said this house is worth less than someone paid for it. (Sale date is not on '
                'this row, so this is the last sale, not necessarily this owner\'s purchase.)'
                % (format(int(_zpx), ','), format(int(_zpx - basis), ','), format(int(basis), ',')),
                'Do not quote an equity number. Pull the deed and the mortgage before any '
                'conversation that depends on there being room in this property.'))

        # ---- EQ_UNRELIABLE — Sisavath tell #7. Our own board already called this number a lie.
        # eqfake is set upstream when the equity read is a gross upper bound: an HOA/junior case with
        # a first mortgage still standing, an individual plaintiff, a judgment too small to be the
        # real debt. Until now it did two things: skip the deep dive (deep_dive_reason L~510) and
        # paint a letter on a card. Skipping the dive is correct — there is nothing to verify about a
        # number we already know is wrong — but it silently doubled as PERMISSION TO CALL, because
        # the old is_hold() short-circuited on the dive. Sisavath's row carried eqfake AND 91%.
        # GUARD: only fires where the row is actually SHOWING an equity number a human could pitch.
        # An eqfake row with no value and no judgment (most of lp_leads.json) claims nothing, so
        # there is nothing to be wrong about and nothing to hold.
        if r.get('eqfake') or r.get('eq_fake'):
            hd_pct = _n(_first(r, 'eq', 'equity_pct'))
            hd_usd = _n(_first(r, 'equity'))
            c_usd, c_pct = owner_equity(r)
            if hd_pct > 0 or hd_usd > 0 or c_usd > 0:
                # 'eq' is OVERLOADED: a percent on the county rows (Sisavath carries eq 91) and raw
                # DOLLARS on lp_leads (2026-015820-CA-01 carries eq 344139 against judg 0). Equity
                # cannot exceed 100% of the basis, so a headline over 100 is a dollar figure wearing
                # a percent's key name. Render from owner_equity() wherever it can be computed so a
                # closer never reads "344139%" off this sentence.
                if c_usd > 0:
                    shown = '$%s (%d%%)' % (format(int(c_usd), ','), round(c_pct))
                elif 0 < hd_pct <= 100:
                    shown = '%d%%' % round(hd_pct)
                else:
                    shown = '$%s' % format(int(hd_usd or hd_pct), ',')
                out.append(_flag(
                    'EQ_UNRELIABLE', SEV_HIGH,
                    'This row is flagged eqfake — WE have already concluded its equity number is a '
                    'gross upper bound — and it is still showing %s.%s Sisavath\'s row said eqfake '
                    'and 91%% at the same time; the 91%% got pitched, and the first mortgage behind '
                    'it was about $298,000 on a house worth roughly $273,683.'
                    % (shown,
                       (' Note the shape here: the judgment on this row is $0, so that figure is not '
                        'equity computed against the wrong debt — it is the whole value with NO debt '
                        'subtracted at all, because none has been captured yet.'
                        if owner_owed_of(r) <= 0 else
                        ' The board already knows the debt figure on this lead is not the real debt.')),
                    'Do not quote equity, a spread, or a payoff on this lead. Find the senior debt '
                    'first: the recorded mortgage chain at %s, then the payoff in writing. If the '
                    'equity survives that, it becomes a real lead — before that it is a guess with a '
                    'dollar sign on it.' % (links['records'] or 'the county official records')))

        # ---- HIGH_EQUITY_UNVERIFIED — big money, no live title read
        if dive and ts != 'clear' and ts != 'transferred':
            usd, pct = owner_equity(r)
            out.append(_flag(
                'HIGH_EQUITY_UNVERIFIED',
                SEV_HIGH if ts == '' else SEV_MED,
                'This lead shows $%s (%d%%) of owner equity and %s. Every dollar of that equity is '
                'an assumption until somebody confirms the defendant still owns the house.'
                % (format(int(usd), ','), round(pct),
                   'has never had a live ownership check' if ts == ''
                   else 'the live ownership check came back UNVERIFIED'),
                'Run: python ownership_gate.py --folio %s --owner "%s" --county %s%s  — or do the '
                '3-5 minute manual checklist below.'
                % (_s(_first(r, 'folio', 'Folio')) or '<folio>',
                   _s(_first(r, 'owner_clean', 'oname', 'owners')) or '<owner>',
                   county or '<county>',
                   (' --filed %d' % cy) if cy else '')))

        out.sort(key=lambda f: -_SEV_RANK.get(f.get('sev', ''), 0))
        return out
    except Exception:
        return []


def severity_of(row):
    """Worst severity across all flags, '' when clean. Cheap enough to call per row."""
    try:
        fl = risk_flags(row)
        return fl[0]['sev'] if fl else ''
    except Exception:
        return ''


# --------------------------------------------------------------------------------------------
# THE HOLD / RELEASE DECISION — one policy, one call, no drift
# --------------------------------------------------------------------------------------------
# TWO CLASSES, and the split IS the lesson from the two incidents.
#
# _HOLD_ALWAYS — these never ask whether the lead "earned" a deep dive, because the deep-dive gate
#   is precisely what let Sisavath through. deep_dive_reason() returns '' on an eqfake row by design,
#   so needs_deep_dive() was False, so the old is_hold() returned False on its second line and never
#   read a single flag. Everything in this tuple fires on the DATA.
# _HOLD_ON_DIVE — unchanged from the Milouse build. These are "verify before you contact" questions
#   about a lead that DOES show a real equity position; on a lead with no equity position there is
#   nothing being sold and nothing to get wrong, so they stay dive-gated exactly as before.
#
# Nothing was removed from either list. A code that held before still holds.
# SOLD_ABOVE_VALUE belongs here, not only in diligence_gate's fallback tuple. The gate keeps a
# SUBTRACTIVE guarantee — it may hold fewer leads than this module, never more — so a code the gate
# is meant to block on has to be blocking HERE first, or the gate silently cannot act on it. That
# is exactly what happened when it was added to the gate alone: the flag fired, contact_gate said
# hold, and the released row still went out because the code was never in this tuple.
# TAX_DEED_SALE fires on the DATA (the auction type is on the row), so it belongs in this tuple and
# not in _HOLD_ON_DIVE: a tax deed with no computed equity position is still the wrong pitch to the
# wrong party, and dive-gating it would repeat the Sisavath mistake of asking whether a lead
# "earned" a check before checking it.
_HOLD_ALWAYS = ('TITLE_TRANSFERRED', 'SIBLING_CLAIMED', 'UNDERWATER', 'PURCHASE_ANCHOR',
                'SOLD_ABOVE_VALUE', 'EQ_UNRELIABLE', 'TAX_DEED_SALE')
_HOLD_ON_DIVE = ('HOA_CODEFENDANT', 'RECENT_SALE', 'HIGH_EQUITY_UNVERIFIED', 'PARTIES_UNAVAILABLE')


def contact_gate(row):
    """HOLD or RELEASE for ONE lead, with the reason. This is THE call for every contact path —
    door route, call sheet, call mode, cold email, cadence enrolment, letters, the team sheet.

    Returns (never None, never raises):
      {'hold':   bool,   True = do not put a human on this until somebody checks it
       'sev':    str,    worst severity across ALL flags: critical|high|med|low|'' (clean)
       'codes':  [str],  every flag code on the row, most severe first
       'why':    str,    the ONE sentence to print/log — the flag that caused the hold
       'action': str}    what to actually do about that flag

    Usage is meant to be one line at a drop site, WITH the reason, because a suppression that
    removes rows silently is indistinguishable from a queue that was always this size:

        g = diligence_flags.contact_gate(r)
        if g['hold']:
            drop['diligence hold: ' + (g['codes'][0] if g['codes'] else '?')] += 1
            continue

    IT FAILS CLOSED, AND IT IS THE ONLY THING IN THIS FILE THAT DOES.
    The module contract above is "never raises, return the empty answer" — and for annotate() /
    risk_flags() that is right, because the empty answer there means "no flags to show". Here the
    empty answer would mean "safe to call a distressed homeowner", which is the worst possible
    default and is exactly how a data regression upstream would read as "nothing to hold today". So
    an unreadable row, a non-dict, or an internal error returns hold=True with an explicit
    GATE_NO_ROW / GATE_ERROR code. Count those separately from real holds: a sudden pile of them is
    a bug in the caller, not a pile of bad leads.
    """
    try:
        if not isinstance(row, dict):
            return {'hold': True, 'sev': SEV_CRITICAL, 'codes': ['GATE_NO_ROW'],
                    'why': 'No readable lead row reached the diligence gate, so nothing about this '
                           'lead has been checked. Holding — an unreadable row must never come out '
                           'the same door as a clean one.',
                    'action': 'Fix the caller: it passed %s instead of a lead dict.'
                              % type(row).__name__}
        fl = risk_flags(row)
        codes = [_s(f.get('code')) for f in fl]
        dive = needs_deep_dive(row)
        hit = None
        for f in fl:                                  # fl is already sorted most-severe-first
            c = _s(f.get('code'))
            if c in _HOLD_ALWAYS or (dive and c in _HOLD_ON_DIVE):
                hit = f
                break
        if hit is None and (title_status_of(row) == 'transferred' or row.get('sibclaimed')):
            # The row is stamped proven-gone but the flag builder came back without the matching
            # flag — i.e. risk_flags() swallowed something. The Milouse build checked these two
            # stamps before it looked at any flag; keep that belt, and fail closed rather than
            # release a house that is already sold.
            return {'hold': True, 'sev': SEV_CRITICAL, 'codes': (codes or ['GATE_ERROR']),
                    'why': 'This lead is stamped as already transferred / already claimed by another '
                           'case, and the flag builder returned nothing for it. Holding on the stamp.',
                    'action': 'Re-run ownership_gate.py on this case and check why risk_flags() came '
                              'back empty.'}
        return {'hold': hit is not None,
                'sev': (_s(fl[0].get('sev')) if fl else ''),
                'codes': codes,
                'why': (_s(hit.get('msg')) if hit else ''),
                'action': (_s(hit.get('action')) if hit else '')}
    except Exception as e:
        return {'hold': True, 'sev': SEV_CRITICAL, 'codes': ['GATE_ERROR'],
                'why': 'The diligence gate could not evaluate this lead (%s), so it is unchecked, '
                       'not clean. Holding.' % (_s(e)[:120] or 'unknown error'),
                'action': 'Fix the error, then re-run. Do not release the lead by turning the gate '
                          'off — that is the state we were already in when Sisavath got pitched.'}


def is_hold(row):
    """TRUE = do not put a human on this until it is checked. The ONE predicate the door book and
    the call sheet should read. Deliberately NOT 'dead' — a hold is work, a kill is a delete.

    Thin wrapper over contact_gate() so there is exactly one hold policy in the repo. Prefer
    contact_gate() at a drop site: this bool has no reason channel, and a caller that logs
    "diligence hold" with no code cannot tell a real hold from a broken row.

    BEHAVIOUR CHANGE 2026-08-27, and it is intentional: this used to return False when it could not
    evaluate a row. It now returns True (see contact_gate's fail-closed note). Nothing that held
    before releases now.
    """
    return bool(contact_gate(row).get('hold'))


# --------------------------------------------------------------------------------------------
# (4) THE PRINTABLE 3-5 MINUTE CHECKLIST
# --------------------------------------------------------------------------------------------
def checklist(row):
    """Jesse's 3-5 minute deep dive, tailored to THIS row.

    Returns a dict (never None, never raises):
      {'case','owner','addr','county','budget','why','steps':[{'n','do','why','url'}],
       'stop_if':[str], 'text': '<the whole thing as plain text>'}
    Empty 'steps' means no dive is warranted (Jesse rule #1) — the dict still explains why.
    """
    try:
        r = _d(row)
        case = case_of(r)
        county = county_of(r)
        links = _COUNTY_LINKS.get(county, _DEFAULT_LINKS)
        owner = _s(_first(r, 'owner_clean', 'oname', 'owners', 'rname')) or '(owner via title search)'
        addr = _s(_first(r, 'addr', 'Address'))
        folio = _s(_first(r, 'folio', 'Folio'))
        cy = filing_year(r)
        usd, pct = owner_equity(r)
        assns = hoa_parties(r)
        assn = assns[0]['name'] if assns else ''
        reason = deep_dive_reason(r)
        flags = risk_flags(r)

        out = {'case': case, 'owner': owner, 'addr': addr, 'county': county or '(unknown county)',
               'folio': folio, 'budget': '3-5 minutes', 'why': reason,
               'equity': {'usd': usd, 'pct': pct}, 'association': assn,
               'flags': flags, 'steps': [], 'stop_if': [], 'text': ''}

        if not reason:
            dead = title_status_of(r) == 'transferred' or r.get('sibclaimed')
            gate = contact_gate(r)
            if dead:
                out['why'] = ('ALREADY PROVEN GONE — do not spend the 3-5 minutes, spend zero. '
                              'See the flags.')
            elif gate.get('hold'):
                # "No deep dive owed" and "safe to contact" were the same sentence in this printout
                # until Sisavath. They are not the same sentence. An eqfake / underwater row earns
                # no dive precisely because the number is already known to be wrong — which is a
                # reason to stop, not a reason to dial.
                out['why'] = 'NO DEEP DIVE OWED, BUT HOLD — %s' % gate.get('why', '')
            else:
                out['why'] = ('No real equity position (%s%% / $%s). Jesse rule #1: a plain '
                              'foreclosure we get paid to stop does not earn the deep dive.'
                              % (round(pct), format(int(usd), ',')))
            out['text'] = _checklist_text(out)
            return out

        steps = []
        # 1 — the association's own case. THE tell, and the cheapest one.
        if assn:
            steps.append({
                'n': 1,
                'do': 'Search "%s" as PLAINTIFF at %s.' % (assn, links['clerk']),
                'why': 'It is a co-defendant on OUR case, which means it holds a lien. An '
                       'association that holds a lien forecloses on it. That case is where an '
                       'already-completed sale hides — it never touches our docket.',
                'url': links['cases']})
        else:
            steps.append({
                'n': 1,
                'do': 'Read the case style/parties at %s and note EVERY association, condo, '
                      '"maintenance corp", POA or master association in the caption.' % links['clerk'],
                'why': 'We have no co-defendant data on this lead, so nothing has checked this. '
                       'An association in the caption is the trigger for step 2.',
                'url': links['cases']})
            steps.append({
                'n': 2,
                'do': 'If you find one, search that association as PLAINTIFF against this owner.',
                'why': 'Same reason as above — its case can already have taken the house.',
                'url': links['cases']})

        n = len(steps) + 1
        # 2 — other cases under the owner's name (Jesse tell #2)
        steps.append({
            'n': n,
            'do': 'Search the OWNER "%s" as a PARTY at %s and list every case, not just ours.'
                  % (owner, links['clerk']),
            'why': 'Our case number tells you nothing about a second one. A second foreclosure '
                   '(HOA, condo, code lien, second mortgage) can be months ahead of ours.',
            'url': links['cases']})
        n += 1
        # 3 — official records: the certificate of title itself
        steps.append({
            'n': n,
            'do': 'Name-search "%s" in Official Records%s and look for CERTIFICATE OF TITLE, TAX '
                  'DEED, or any deed dated after %s.'
                  % (owner, (' (%s)' % links['records']) if links['records'] else '',
                     cy if cy else 'the case was filed'),
            'why': 'The certificate of title is the document that ends the story. If one exists '
                   'dated after our filing, this owner sold or was sold out and owns nothing.',
            'url': links['records']})
        n += 1
        # 4 — the property's own sale history (Jesse tell #4)
        steps.append({
            'n': n,
            'do': 'Open the property appraiser record%s and read the SALES HISTORY.'
                  % ((' for folio %s' % folio) if folio else ''),
            'why': 'A sale on a distressed property is a red flag, not a comp. On Broward look for '
                   'the CET (certificate of title) code; any sale dated after %s means the house '
                   'moved while the case was open.' % (cy if cy else 'the filing'),
            'url': links['pa']})
        n += 1
        # 5 — confirm the human
        steps.append({
            'n': n,
            'do': 'Confirm the CURRENT owner name on the appraiser page is still "%s".' % owner,
            'why': 'This is the whole question. If the name changed, everything above is academic '
                   'and the equity on the board is phantom.',
            'url': links['pa']})

        out['steps'] = steps
        out['stop_if'] = [
            'A certificate of title exists dated AFTER our case was filed — the house is gone.',
            'The current owner of record is not our defendant — the house is gone.',
            'The association has its own foreclosure with a sale date already past — assume gone '
            'until you can prove otherwise.',
            'You have spent 5 minutes and cannot answer "does this person still own it" — mark it '
            'UNVERIFIED and hold it. Do not let a closer dial it on a maybe.']
        out['text'] = _checklist_text(out)
        return out
    except Exception:
        return {'case': case_of(row), 'owner': '', 'addr': '', 'county': '', 'folio': '',
                'budget': '3-5 minutes', 'why': '', 'equity': {'usd': 0.0, 'pct': 0.0},
                'association': '', 'flags': [], 'steps': [], 'stop_if': [], 'text': ''}


def _checklist_text(c):
    try:
        L = []
        L.append('PRE-CONTACT DEEP DIVE  (%s)' % c.get('budget', ''))
        L.append('%s  |  %s  |  %s' % (c.get('case', ''), c.get('addr', ''), c.get('county', '')))
        L.append('Owner: %s' % c.get('owner', ''))
        eq = c.get('equity') or {}
        L.append('Equity on the board: $%s (%s%%)  -- assumption until step %d says otherwise'
                 % (format(int(eq.get('usd') or 0), ','), round(eq.get('pct') or 0),
                    len(c.get('steps') or []) or 1))
        if c.get('why'):
            L.append('WHY THIS ONE: %s' % c['why'])
        for f in (c.get('flags') or []):
            L.append('  [%s] %s' % (_s(f.get('sev')).upper(), _s(f.get('msg'))))
        if c.get('steps'):
            L.append('')
            for s in c['steps']:
                L.append('%d) %s' % (s.get('n', 0), s.get('do', '')))
                L.append('     why: %s' % s.get('why', ''))
                if s.get('url'):
                    L.append('     %s' % s['url'])
        if c.get('stop_if'):
            L.append('')
            L.append('STOP AND HOLD IF:')
            for s in c['stop_if']:
                L.append('  - %s' % s)
        return '\n'.join(L)
    except Exception:
        return ''


# --------------------------------------------------------------------------------------------
# THE ONE CALL make_tracker / the door book / the call sheet should use
# --------------------------------------------------------------------------------------------
def annotate(row, checklist_mode='none'):
    """Compute every flag for one row and return the SHORT board keys to merge onto it.

    Returns {} for a clean row (nothing baked, board unchanged). Never raises, never mutates.

      dd      bool   needs the mandatory 3-5 minute dive (Jesse rule #1)
      ddwhy   str    one line: why this lead earned the dive
      ddsev   str    worst severity across the flags: critical|high|med|low
      ddhold  bool   contact_gate() says hold. THE field every JS surface must read; it is the
                     only way the Python decision reaches the browser, and an absent key there is
                     falsy, so a skipped bake fails OPEN with no error anywhere. Assert on the
                     count of rows carrying it at build time — do not assume the loop ran.
      ddassn  str    the association co-defendant name (Jesse tell #3), '' when none
      ddflags list   [{code,sev,msg,action,party}]
      ddchk   str|dict  the printable checklist, ONLY when dd is True

    checklist_mode: 'none' (DEFAULT — bake no checklist onto the row), 'text' (the printable
                    checklist as plain text, render it in a <pre>) or 'full' (the structured dict).
                    PAYLOAD NOTE, measured on the live board: 312 rows carry flags today, so
                    'text' adds ~800KB and 'full' ~1.4MB to a docs/index.html that is already
                    6.4MB and boots in ~16s. The door book, the call sheet and the CLI all run in
                    Python and can just call checklist(row) on the handful of rows they print —
                    do not pay for 312 copies of it in the page.
    """
    try:
        # The bake loop's contract is unchanged and stays absolute: a malformed row adds NOTHING to
        # the board. contact_gate() below fails closed, but that belongs at a contact decision, not
        # here — make_tracker does `_d.update(annotate(_d))`, which cannot even reach a non-dict.
        if not isinstance(row, dict):
            return {}
        fl = risk_flags(row)
        dd = needs_deep_dive(row)
        gate = contact_gate(row)
        assn = hoa_codefendant(row)
        # A HOLD MUST NEVER FALL OUT OF THE BAKE. The browser reads r.ddhold, and an undefined field
        # there is falsy — it fails OPEN, silently, with nothing in the console. So if the gate says
        # hold, this row is baked even when risk_flags() came back empty (the fail-closed paths), and
        # it is baked WITH its reason attached rather than as a bare true nobody can explain.
        if not fl and not dd and not gate.get('hold'):
            return {}
        if gate.get('hold') and not fl:
            fl = [_flag(((gate.get('codes') or ['GATE_ERROR'])[0]), gate.get('sev') or SEV_CRITICAL,
                        gate.get('why', ''), gate.get('action', ''))]
        o = {}
        if dd:
            o['dd'] = True
            o['ddwhy'] = deep_dive_reason(row)
            if checklist_mode == 'full':
                o['ddchk'] = checklist(row)
            elif checklist_mode == 'text':
                o['ddchk'] = checklist(row).get('text', '')
        if fl:
            o['ddflags'] = fl
            o['ddsev'] = fl[0]['sev']
        if assn:
            o['ddassn'] = assn
        if gate.get('hold'):
            o['ddhold'] = True
        return o
    except Exception:
        return {}


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------
_ZOO = [
    # (party string, expect_match, note)
    ('FAIRHAVEN 11 MAINTENANCE CORP', True, 'maintenance corp, no ASSN word'),
    ('SEAWIND CONDOMINIUM ASSOCIATION INC', True, 'textbook condo'),
    ('XYZ HOMEOWNERS ASSN', True, 'homeowners assn'),
    ('SUNRISE POA', True, 'bare POA'),
    ('OAK RUN MASTER ASSOCIATION', True, 'master association'),
    ('MUTINY ON THE BAY CONDO ASSOC INC (THE)', True, 'condo assoc'),
    ('Lago Mar Townhomes Association Inc', True, 'townhomes association'),
    ('London Tower Condominium Inc', True, 'condominium, no assn word'),
    ('Biscayne Gardens Civic Association Inc', True, 'civic association (weak match, still report)'),
    ('THE VILLAS AT BAY COLONY ASSOCIATION, INC.', True, 'villas + association'),
    ('PALM COVE PROPERTY OWNERS ASSOCIATION', True, 'property owners'),
    ('CITIBANK, NATIONAL ASSOCIATION', False, 'bank charter — the classic false positive'),
    ('U.S. BANK TRUST COMPANY, NATIONAL ASSOCIATION', False, 'bank charter'),
    ('WELLS FARGO BANK, N.A.', False, 'bank'),
    ('HSBC FINANCE CORPORATION', False, 'lender'),
    ('MORTGAGE ELECTRONIC REGISTRATION SYSTEMS INC', False, 'MERS'),
    ('DADE COUNTY FEDERAL CREDIT UNION', False, 'credit union'),
    ('UNITED STATES OF AMERICA DEPT OF THE TREASURY IRS', False, 'gov'),
    ('UNKNOWN TENANT #1', False, 'noise party'),
    ('Martinez, Ernesto', False, 'a human'),
    ('CJ Capital Group Inc a New York Corporation', False, 'random corp'),
    ('', False, 'empty'),
]


def _selftest():
    ok = fail = 0

    def chk(cond, label):
        nonlocal ok, fail
        if cond:
            ok += 1
            print('  PASS  %s' % label)
        else:
            fail += 1
            print('  FAIL  %s' % label)

    print('--- association name zoo ---')
    for name, want, note in _ZOO:
        got = bool(_assn_match(name)[0])
        chk(got == want, '%-52s -> %-5s (%s)' % (name[:52], got, note))

    print('--- co-defendant extraction (real MD shape) ---')
    md = {'Case #': '2026-002031-CA-01', 'plaintiff': 'REGIONS BANK',
          'defendants': 'TRABIN, SARA; MUTINY CONDOMINIUM ASSOCIATION INC (THE); '
                        'MUTINY ON THE BAY CONDO ASSOC INC (THE)',
          'market_value': 400000, 'judgment': 100000, 'county': 'MIAMI-DADE'}
    chk('MUTINY' in hoa_codefendant(md).upper(), 'HOA co-defendant found on a bank foreclosure')
    chk(needs_deep_dive(md) is True, 'high equity -> deep dive required')
    chk(any(f['code'] == 'HOA_CODEFENDANT' for f in risk_flags(md)), 'HOA_CODEFENDANT flag emitted')

    print('--- the case that started this (Broward, Milouse shape) ---')
    mil = {'case': 'CACE-24-006635', 'county': 'BROWARD', 'owners': 'JOSEPH,MILOUSE H/E',
           'addr': '8208 NW 57 PL, TAMARAC, 33321', 'value': 325610, 'judg': 268757.94,
           'eq': 17, 'eqfake': False, 'st': 'FC', 'days': 5, 'folio': '494109022120',
           'defs': '', 'plaintiff': '', 'named': []}
    usd, pct = owner_equity(mil)
    chk(round(usd) == 56852, 'owner equity $%s' % format(int(usd), ','))
    chk(pct < DEEP_DIVE_EQ_PCT, 'and only %.1f%% — a percent-only gate would MISS it' % pct)
    chk(needs_deep_dive(mil) is True, 'dollar gate catches it anyway (this is the whole point)')
    codes = {f['code'] for f in risk_flags(mil)}
    chk('PARTIES_UNAVAILABLE' in codes, 'no party data -> honest PARTIES_UNAVAILABLE, not silence')
    chk('HIGH_EQUITY_UNVERIFIED' in codes, 'no live title read -> HIGH_EQUITY_UNVERIFIED')
    chk(is_hold(mil) is True, 'HOLD before a closer dials')
    mil2 = dict(mil, title_status='transferred', title_owner='SEWELL, CYNTHIA')
    chk(any(f['code'] == 'TITLE_TRANSFERRED' and f['sev'] == SEV_CRITICAL
            for f in risk_flags(mil2)), 'ownership_gate stamp -> CRITICAL')
    chk(needs_deep_dive(mil2) is False, 'proven gone -> no dive, just the warning')
    chk(checklist(mil)['steps'] and checklist(mil2)['steps'] == [], 'checklist follows the dive gate')

    print('--- the SECOND incident (Sisavath) — each new rule must catch it ALONE ---')
    # Her real palmbeach_leads.json row, field for field. No sale year, no purchase price, no
    # mortgage: the ONLY thing on it that knows the truth is eqfake.
    sis = {'case': '502024CA010265XXXAMB', 'county': 'PALM BEACH', 'st': 'FC', 'ftype': 'MORTGAGE',
           'owners': 'SISAVATH VANHNALY K &', 'addr': '4118 41ST WAY, WEST PALM BEACH, FL 33407',
           'value': 225675, 'judg': 20323.36, 'eq': 91, 'eqfake': True,
           'bought': 0, 'bprice': 0, 'filed': 0, 'defs': '', 'named': [], 'plaintiff': ''}
    chk(needs_deep_dive(sis) is False, 'eqfake still skips the DIVE — rule #1 unchanged')
    chk({f['code'] for f in risk_flags(sis)} == {'EQ_UNRELIABLE'},
        'EQ_UNRELIABLE fires ALONE on the untouched live row (got %s)'
        % sorted({f['code'] for f in risk_flags(sis)}))
    chk(is_hold(sis) is True, 'and she is HELD — the dive gate no longer decides contact')
    g = contact_gate(sis)
    chk(g['hold'] is True and g['sev'] == SEV_HIGH and g['codes'] == ['EQ_UNRELIABLE'] and g['why'],
        'contact_gate: hold + severity + codes + a printable reason')
    chk('NO DEEP DIVE OWED, BUT HOLD' in checklist(sis)['why'],
        'the printout no longer reads "no dive" as "safe to call"')

    # #5 UNDERWATER, isolated: title already read clear (kills HIGH_EQUITY_UNVERIFIED), a party
    # present (kills PARTIES_UNAVAILABLE), no eqfake, no sale data. Only the debt can hold it.
    sis_u = {'case': '502024CA010265XXXAMB', 'county': 'PALM BEACH', 'value': 273683,
             'judg': 20323.36, 'orsurvfirst': 298000, 'orconf': 'ok', 'title_status': 'clear',
             'defs': 'UNKNOWN TENANT #1', 'filed': 2024}
    uf = [f for f in risk_flags(sis_u) if f['code'] == 'UNDERWATER']
    chk({f['code'] for f in risk_flags(sis_u)} == {'UNDERWATER'},
        'UNDERWATER fires alone (got %s)' % sorted({f['code'] for f in risk_flags(sis_u)}))
    chk(bool(uf) and uf[0]['sev'] == SEV_CRITICAL, 'UNDERWATER is critical')
    chk(bool(uf) and 'INDICATIVE' in uf[0]['msg'] and 'ORIGINAL PRINCIPAL' in uf[0]['msg'],
        'says the comparison is indicative AND names the recorded-principal figure it used')
    chk(is_hold(sis_u) is True, 'UNDERWATER holds on its own')
    chk(round(known_debt_of(sis_u)['total']) == 318323, 'debt = judgment + the ONE largest chain '
        'figure, never the sum of the chain fields (got $%s)'
        % format(int(known_debt_of(sis_u)['total']), ','))
    chk(known_debt_of(dict(sis_u, orsurv=298000, orjunior=298000))['total']
        == known_debt_of(sis_u)['total'], 'duplicate chain fields cannot inflate the debt')
    chk(not any(f['code'] == 'UNDERWATER' for f in risk_flags(dict(sis_u, ju=True, orsurvfirst=0))),
        'a judgment we cannot read is a blank, not a debt — no UNDERWATER off it')

    # #6 PURCHASE_ANCHOR, isolated from the dive: ju=True kills the dive AND the judgment figure, so
    # the only flag that can hold this row is the price she paid. This is the shape that proves the
    # rule does not depend on needs_deep_dive() — the exact dependency that burned us.
    sis_p = {'case': '502024CA010265XXXAMB', 'county': 'PALM BEACH', 'value': 273683,
             'judg': 20323.36, 'ju': True, 'bprice': 315000, 'bought': 2023, 'filed': 2024,
             'defs': 'UNKNOWN TENANT #1'}
    pcodes = {f['code'] for f in risk_flags(sis_p)}
    chk('PURCHASE_ANCHOR' in pcodes, 'PURCHASE_ANCHOR: paid $315,000 in 2023 on a $273,683 value')
    chk(needs_deep_dive(sis_p) is False, 'no dive owed on this row at all')
    chk(contact_gate(sis_p)['hold'] is True and contact_gate(sis_p)['codes'][0] == 'PURCHASE_ANCHOR',
        'it holds anyway, and it is the reason printed')
    chk(not any(f['code'] == 'PURCHASE_ANCHOR'
                for f in risk_flags(dict(sis_p, bprice=200000))),
        'bought BELOW value -> no anchor flag (the rule is an inequality, not a vibe)')
    chk(not any(f['code'] == 'PURCHASE_ANCHOR' for f in risk_flags(dict(sis_p, bought=2009))),
        'an old purchase is not an anchor — principal has been paid down since')

    # #7 EQ_UNRELIABLE must NOT fire where the row claims nothing. lp_leads.json rows carry
    # eqfake with value 0 and judg 0: no equity number exists, so there is nothing to be wrong
    # about and nothing to hold. Without this guard the gate eats the whole LP pool.
    lp = {'case': 'COWE-26-055152', 'county': 'BROWARD', 'value': 0, 'judg': 0, 'eqfake': True,
          'eq': 0, 'defs': '', 'named': []}
    chk(risk_flags(lp) == [] and is_hold(lp) is False,
        'eqfake with no equity number anywhere -> no flag, no hold')
    mk = dict(lp, value=839540, plaintiff='PHOENICIAN COVE HOMEOWNERS ASSN INC')
    chk(any(f['code'] == 'EQ_UNRELIABLE' for f in risk_flags(mk)),
        'eqfake on a row printing 100% of $839,540 off a $0 judgment -> HELD (Markey, 8270 '
        'Phoenician Ct — this one used to walk through because nothing could see it)')

    print('--- the gate fails CLOSED, unlike everything else in this file ---')
    for bad in (None, [], 'nonsense', 42):
        chk(contact_gate(bad)['hold'] is True and contact_gate(bad)['codes'] == ['GATE_NO_ROW'],
            'unreadable row %r -> HOLD, not release' % (str(bad)[:20],))
    chk(is_hold(mil) is True and is_hold(mil2) is True, 'Milouse still holds both ways (no regression)')
    chk(all(annotate(b) == {} for b in (None, [], 'nonsense', 42)),
        'annotate() still bakes NOTHING onto a malformed row — the gate fails closed, the bake does not')
    chk(annotate(sis).get('ddhold') is True and annotate(sis).get('ddflags'),
        'a held row bakes ddhold WITH its reason (the browser reads ddhold and an absent key '
        'fails open)')
    chk(contact_gate({'case': 'X', 'value': 300000, 'judg': 290000, 'title_status': 'clear',
                      'defs': 'UNKNOWN TENANT #1'})['hold'] is False,
        'a thin-equity, title-clear lead is still RELEASED — this is a gate, not a wall')

    print('--- case_year across all four dialects (sibling_cases._lead_year bug) ---')
    for c, y in (('2026-002031-CA-01', 2026), ('CACE-24-006635', 2024), ('CONO-23-008222', 2023),
                 ('502026CA000685XXXAMB', 2026), ('2026-2833TD', 2026), ('2026A00190', 2026),
                 ('', 0), (None, 0), (12345, 0)):
        chk(case_year(c) == y, 'case_year(%r) == %r (got %r)' % (c, y, case_year(c)))

    print('--- recent sale (tell #4) ---')
    rs = {'case': '2025-001111-CA-01', 'filing_year': 2025, 'last_sale_date': '6/2/2026',
          'market_value': 500000, 'judgment': 100000}
    chk(any(f['code'] == 'RECENT_SALE' and f['sev'] == SEV_HIGH for f in risk_flags(rs)),
        'sale AFTER filing -> HIGH')
    rs2 = dict(rs, last_sale_date='6/2/2024')
    chk(any(f['code'] == 'RECENT_SALE' and f['sev'] == SEV_MED for f in risk_flags(rs2)),
        'sale 1y before filing -> MED')
    rs3 = dict(rs, last_sale_date='6/2/2009')
    chk(not any(f['code'] == 'RECENT_SALE' for f in risk_flags(rs3)), 'old sale -> no flag')

    print('--- malformed rows must never raise ---')
    for bad in (None, {}, [], 'nonsense', 42, {'defs': None, 'named': None, 'value': 'abc'},
                {'named': [None, 5, {'nope': 1}], 'defs': ['a', {'name': 'X CONDO ASSOC'}]},
                {'value': float('nan'), 'judg': float('inf')},
                {'defendants': 'A' * 20000}, {'case': None, 'county': None},
                {'named': {'name': 'weird dict'}}, {'value': '$1,234.00', 'judg': '$1'}):
        try:
            risk_flags(bad); needs_deep_dive(bad); hoa_codefendant(bad); checklist(bad); annotate(bad)
            severity_of(bad); is_hold(bad); owner_equity(bad); basis_of(bad); filing_year(bad)
            chk(True, 'survived %r' % (str(bad)[:40],))
        except Exception as e:
            chk(False, 'RAISED on %r -> %s' % (str(bad)[:40], e))

    print('--- our own HOA case must NOT fire tell #3 (opposite shape) ---')
    own = {'case': '2025-074993-CC-05', 'ftype': 'HOA', 'ctype': 'HOA',
           'plaintiff': 'PALM LAKE CONDOMINIUM ASSOCIATION INC',
           'defs': 'SMITH, JOHN; WELLS FARGO BANK NA', 'value': 300000, 'judg': 8000}
    chk(not any(f['code'] == 'HOA_CODEFENDANT' for f in risk_flags(own)),
        'HOA plaintiff on our own case is not a co-defendant tell')

    print('\n%d passed, %d failed' % (ok, fail))
    return 0 if not fail else 1


def _load(fn):
    try:
        with open(os.path.join(HERE, fn), encoding='utf-8') as fh:
            d = json.load(fh)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _scan():
    import glob
    tot = {}
    files = ['leads_final.json'] + [os.path.basename(f) for f in
                                    sorted(glob.glob(os.path.join(HERE, '*_leads.json')))]
    print('%-26s %6s %6s %6s %6s %6s %6s %6s'
          % ('file', 'rows', 'dive', 'now', 'hoa', 'hold', 'crit', 'high'))
    for bn in files:
        if bn.startswith('_') or bn in ('leads_raw.json',) or bn in tot:
            continue
        rows = _load(bn)
        if not rows:
            continue
        dive = now = hoa = hold = crit = high = 0
        for r in rows:
            d = needs_deep_dive(r)
            if d:
                dive += 1
                # "now" = the dive the operator actually owes TODAY: the contact window every
                # human surface already uses (_isCloser L2543 / _carlos_md_rows: 2 < days <= 45).
                try:
                    days = _n(_d(r).get('days') or _d(r).get('days_to_auction'))
                    if 2 < days <= 45:
                        now += 1
                except Exception:
                    pass
            if hoa_codefendant(r):
                hoa += 1
            if is_hold(r):
                hold += 1
            s = severity_of(r)
            if s == SEV_CRITICAL:
                crit += 1
            elif s == SEV_HIGH:
                high += 1
        tot[bn] = (len(rows), dive, now, hoa, hold, crit, high)
        print('%-26s %6d %6d %6d %6d %6d %6d %6d' % (bn, *tot[bn]))
    if tot:
        agg = [sum(v[i] for v in tot.values()) for i in range(7)]
        print('%-26s %6d %6d %6d %6d %6d %6d %6d' % ('TOTAL', *agg))
        print('\n%d lead(s) earn the dive; %d of them are inside the 2-45 day contact window '
              '= about %d-%d operator minutes of real work today.'
              % (agg[1], agg[2], agg[2] * 3, agg[2] * 5))
    return 0


def _print(s):
    """Console-safe print. The flag sentences carry em dashes; a legacy cp1252 Windows console
    raises UnicodeEncodeError on those and would kill a CLI run over punctuation."""
    t = _s(s)
    try:
        print(t)
    except Exception:
        sys.stdout.write(t.encode('ascii', 'replace').decode('ascii') + '\n')


def _one(case):
    import glob
    for f in ['leads_final.json'] + sorted(glob.glob(os.path.join(HERE, '*_leads.json'))):
        for r in _load(os.path.basename(f)):
            if case_of(r).upper() == _s(case).upper():
                _print(checklist(r)['text'])
                return 0
    print('case not found in any *_leads.json: %s' % case)
    return 1


def main(argv):
    if '--selftest' in argv:
        return _selftest()
    if '--scan' in argv:
        return _scan()
    if '--case' in argv:
        i = argv.index('--case')
        if i + 1 < len(argv):
            return _one(argv[i + 1])
    print(__doc__.strip().splitlines()[0])
    print('usage: diligence_flags.py [--selftest | --scan | --case <CASE NUMBER>]')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
