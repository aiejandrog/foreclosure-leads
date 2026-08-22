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
#   COMMODORE PLAZA CONDOMINIUM ASSOC INC   -> CONDOMINIUM
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


def is_hold(row):
    """TRUE = do not put a human on this until it is checked. The ONE predicate the door book and
    the call sheet should read. Deliberately NOT 'dead' — a hold is work, a kill is a delete."""
    try:
        if title_status_of(row) == 'transferred' or _d(row).get('sibclaimed'):
            return True
        if not needs_deep_dive(row):
            return False
        codes = {f['code'] for f in risk_flags(row)}
        return bool(codes & {'HOA_CODEFENDANT', 'RECENT_SALE', 'HIGH_EQUITY_UNVERIFIED',
                             'PARTIES_UNAVAILABLE'})
    except Exception:
        return False


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
            out['why'] = ('ALREADY PROVEN GONE — do not spend the 3-5 minutes, spend zero. '
                          'See the flags.' if dead else
                          'No real equity position (%s%% / $%s). Jesse rule #1: a plain foreclosure '
                          'we get paid to stop does not earn the deep dive.'
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
      ddhold  bool   do not put a human on this until it is checked
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
        fl = risk_flags(row)
        dd = needs_deep_dive(row)
        assn = hoa_codefendant(row)
        if not fl and not dd:
            return {}
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
        if is_hold(row):
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
    ('COMMODORE PLAZA CONDOMINIUM ASSOCIATION INC', True, 'textbook condo'),
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
