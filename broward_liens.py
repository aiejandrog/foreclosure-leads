"""Pillar 3 (Broward) — pull the recorded mortgage/lien chain for Broward leads. No captcha.

Broward's Official Records (AcclaimWeb, officialrecords.broward.org) is DISCLAIMER-gated but NOT
reCAPTCHA-gated — unlike Miami-Dade's wall that caps us at 62%. The catch: Cloudflare bot-management
blocks python-requests' TLS fingerprint AND headless browsers AND curl_cffi's chrome impersonation;
only the native Windows curl binary (Schannel TLS) passes. So this shells out to `curl`.

Flow (one session):  GET Disclaimer -> POST disclaimer=true -> per owner: POST name search
(all doc/book types) -> POST Search/GridResults (Telerik JSON). GridResults returns
  {data:[{Name,Party,CrossPartyName,RecordDate,BookPage,InstrumentNumber,Consideration,
          DocTypeDescription,DocLegalDescription,ParcelNumber,...}], total}.

analyze() mirrors records_liens.py — PRECISION OVER RECALL. Broward has no folio on the docs and
common names return decades of unrelated people, so we isolate by EXACT (last, first) name + the
borrower side, mark a mortgage OPEN unless a later same-institution satisfaction/release exists, and
apply hard confidence guards (common name / MERS ambiguity -> conf='low', no surviving-2nd number).
Output -> broward_liens.json (gitignored), keyed by Case #, SAME schema as records_liens.json so
make_tracker bakes orliens/orjunior/orconf for Broward leads exactly like Miami-Dade.

Usage:
  python broward_liens.py --case CACE-24-003040       # one lead (prove it)
  python broward_liens.py --tier A                     # a tier
  python broward_liens.py --all                        # every human-owner Broward lead not yet traced
  python broward_liens.py --all --limit 20             # cap the run
"""
import argparse, json, os, re, subprocess, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'broward_leads.json')
OUT = os.path.join(HERE, 'broward_liens.json')          # Case # -> lien result (gitignored)
BASE = 'https://officialrecords.broward.org/AcclaimWeb'
JAR = os.path.join(tempfile.gettempdir(), 'brw_liens_cookies.txt')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|USA|COUNTY|CITY OF|CHURCH|'
                        r'MINISTR|ESTATE OF|PROPERT|REALTY|CAPITAL|FUND|INVEST|HOMES|ENTERPRISE|PARTNERS|MGMT|'
                        r'MANAGEMENT|VENTURES|GROUP|EQUITIES|ACQUISITION|WORSHIP|TABERNACLE|TEMPLE|CONGREGATION)\b', re.I)
MERS_RE = re.compile(r'ELECTRONIC REGISTRATION|\bMERS\b|MORTGAGE ELECTRONIC', re.I)
# fixed "all document/book types" code lists (from the SearchTypeName form; stable). Fetched live at
# session start when possible, else these fallbacks keep the search valid.
DOCTYPES_FALLBACK = ('174,175,173,176,177,178,163,171,165,137,172,168,169,166,167,190,189,170,230,155,162,164,'
    '139,138,131,132,134,133,135,136,157,154,156,158,153,112,151,152,161,224,160,159,181,229,147,144,145,141,'
    '142,148,143,150,146,186,227,226,228,127,129,130,128,123,187,188,179,122,124,126,125,118,120,121,119,180,'
    '113,114,115,116')
BOOKTYPES_FALLBACK = '2,11,20,27,32,28,33'


# ---- curl transport (only fingerprint Cloudflare lets through here) ---------------------------
def _curl(url, post=None, timeout=45):
    cmd = ['curl', '-s', '-m', str(timeout), '-A', UA, '-c', JAR, '-b', JAR,
           '-H', 'Accept: text/html,application/json,*/*;q=0.8', '-H', 'Accept-Language: en-US,en;q=0.9']
    if post is not None:
        cmd += ['-X', 'POST', '-H', 'Content-Type: application/x-www-form-urlencoded',
                '-H', 'X-Requested-With: XMLHttpRequest', '-H', 'Referer: ' + BASE + '/Search/SearchTypeName']
        for k, v in post:
            cmd += ['--data-urlencode', f'{k}={v}']
    cmd += [url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout + 10)
        return r.stdout or ''
    except Exception:
        return ''


def start_session():
    """Accept the disclaimer and read the all-types code lists off the search form."""
    _curl(BASE + '/Search/Disclaimer')
    _curl(BASE + '/Search/Disclaimer', post=[('disclaimer', 'true')])
    form = _curl(BASE + '/Search/SearchTypeName')
    if 'SearchOnName' not in form:
        return None                                    # blocked / no session
    dts = ','.join(dict.fromkeys(re.findall(r'name="DocTypeInfoCheckBox"[^>]*value="(\d+)"', form))) or DOCTYPES_FALLBACK
    bts = ','.join(dict.fromkeys(re.findall(r'name="BookTypeInfoCheckBox"[^>]*value="(\d+)"', form))) or BOOKTYPES_FALLBACK
    return {'doctypes': dts, 'booktypes': bts}


def search_docs(sess, search_name):
    """Run a name search and return the full document list (list of dicts) or None if blocked."""
    resp = _curl(BASE + '/Search/SearchTypeName?Length=6', post=[
        ('PartyType', 'Both'), ('SearchOnName', search_name), ('IsParsedName', 'false'),
        ('AllowAutoCompleteCB', 'false'), ('DateRangeList', ' '),
        ('DocTypes', sess['doctypes']), ('DocTypesDisplay-input', 'All'), ('DocTypesDisplay', 'All'),
        ('BookTypes', sess['booktypes']), ('BookTypesDisplay', 'All'),
        ('RecordDateFrom', '1/1/1985'), ('RecordDateTo', time.strftime('%-m/%-d/%Y') if os.name != 'nt' else time.strftime('%m/%d/%Y')),
    ])
    if 'ShowError' in resp:                             # invalid criteria (shouldn't happen with full code lists)
        return None
    grid = _curl(BASE + '/Search/GridResults', post=[('page', '1'), ('size', '400'), ('sort', ''), ('group', ''), ('filter', '')])
    try:
        j = json.loads(grid)
    except Exception:
        return None
    return j.get('data', [])


# ---- parse the chain: open vs satisfied, isolate the surviving junior --------------------------
def _num(x):
    try: return float(x or 0)
    except Exception: return 0

def _jsdate(s):
    m = re.search(r'/Date\((-?\d+)', s or '')
    if not m: return '0000-00-00'
    ms = int(m.group(1))
    # avoid tz libs: derive Y-M-D from the epoch ms directly (UTC)
    import datetime
    return (datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=ms)).strftime('%Y-%m-%d')

def _lf(name):
    """('LAST','FIRST') alpha-upper from an official-records / lead owner string. Middle names dropped."""
    s = re.sub(r'\s*&\s*[WH].*$', '', (name or '').upper())          # drop '&W HELEN', '& H ...'
    s = re.sub(r'\bH/[EW]\b|\bET\s?UX\b|\bET\s?AL\b|\bTRS?\b|\bJR\b|\bSR\b|\bII+\b', '', s)
    if ',' in s:
        last, _, rest = s.partition(',')
    else:
        toks = s.split(); last, rest = (toks[0], ' '.join(toks[1:])) if toks else ('', '')
    la = re.sub(r'[^A-Z]', '', last)
    ft = re.sub(r'[^A-Z]', '', (rest.split() or [''])[0])
    return (la, ft)

def _inst(s):
    """Normalize a lender/institution name for satisfaction<->mortgage matching."""
    s = (s or '').upper()
    s = re.sub(r'\b(NA|N A|NATIONAL ASSN|NATIONAL ASSOCIATION|FSB|FA|INC|CORP|CO|LLC|LP|USA|'
               r'TRUST COMPANY|MTGE|MORTGAGE|GROUP|GRP|SVGS|SAVINGS|HOME LOANS?|FINANCIAL|SERVICES?|BANK)\b', '', s)
    return re.sub(r'[^A-Z]', '', s)


# an investor grantee on a recent deed = the deal was already worked (land trust / LLC / holding co).
INVESTOR_RE = re.compile(r'\bLAND\s*TR(?:UST)?\b|\bLLC\b|\bINC\b|\bCORP\b|\bL\.?P\.?\b|\bLTD\b|\bGROUP\b|\bHOMES?\b|'
                         r'PROPERT|REALTY|CAPITAL|\bFUND\b|INVEST|HOLDINGS|VENTURES|EQUITIES|ACQUISITION|PARTNERS', re.I)


def _fc_type(case):
    """FALLBACK-ONLY classifier: guess from the court case number when we have no plaintiff to read.
    The prefix is a POOR proxy — HOAs routinely foreclose in CIRCUIT court (a CACE number), so a CACE is
    NOT reliably a mortgage foreclosure. Prefer _plaintiff_ftype() (below) whenever the chain is available."""
    c = (case or '').upper()
    if c.startswith('CACE'):                       # Broward/PB circuit civil
        return 'MORTGAGE'
    if c.startswith(('COCE', 'CONO', 'COWE', 'COSO')):   # Broward/PB county court (HOA / code)
        return 'HOA'
    if '-CA-' in c:                                # Miami-Dade circuit
        return 'MORTGAGE'
    if '-CC-' in c:                               # Miami-Dade county court
        return 'HOA'
    return ''


# --- TRUE foreclosure type from the PLAINTIFF name -----------------------------------------------
# The real signal isn't the case-number prefix, it's who is foreclosing. HOAs sue in circuit court all
# the time (CACE-26-000767 = SANDPIPER COVE HOMEOWNERS ASSN), so the plaintiff, not the prefix, decides.
# BANK-CHARTER GUARD WINS FIRST so a national-bank trustee ("U S BANK TRUST COMPANY NATIONAL ASSN") is
# never misread as an HOA just because its charter name ends in "ASSN".
_BANK_RE = re.compile(
    r'\bBANK\b|\bN\.?\s?A\.?\b|NATIONAL\s+ASS(?:N|OC(?:IATION)?)|\bTRUST(?!EES?\s+OF)|\bSAVINGS\b|'
    r'\bMORTGAGE\b|\bLOANS?\b|\bFINANCIAL\b|\bFUNDING\b|\bSERVICING\b|\bFEDERAL\b|CREDIT\s+UNION|'
    r'\bFANNIE\b|\bFREDDIE\b|\bFNMA\b|\bFHLMC\b', re.I)
_HOA_RE = re.compile(
    r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|\bMASTER\b|\bVILLAS?\b|COMMUNITY|PROPERTY\s+OWNERS?|'
    r'TOWNHO|MAINTENANCE', re.I)
# a bare ASSN/ASSOC(IATION) counts as HOA only when NOT preceded by NATIONAL (that's a bank charter, above)
_ASSN_RE = re.compile(r'(?<!NATIONAL\s)\bASS(?:N|OC(?:IATION)?)\b', re.I)
# a bare corporate note-holder (LLC/LP) with no association term is a lender/note-buyer, not an HOA
_LENDER_CORP_RE = re.compile(r'\bLLC\b|\bL\.?\s?P\.?\b|\bLLP\b', re.I)


def _fc_type_plaintiff(plaintiff):
    """'MORTGAGE' | 'HOA' | '' from a foreclosure plaintiff name. Bank-charter guard wins first."""
    p = (plaintiff or '').upper()
    if not p.strip():
        return ''
    if _BANK_RE.search(p):
        return 'MORTGAGE'
    if _HOA_RE.search(p) or _ASSN_RE.search(p):
        return 'HOA'
    if _LENDER_CORP_RE.search(p):                   # "... LLC/LP" as a lender, no association terms
        return 'MORTGAGE'
    return ''


def _plaintiff_ftype(docs, lc):
    """TRUE type from the plaintiff (CrossPartyName) on the chain rows whose CaseNumber == the lead case.
    A bank-charter plaintiff on ANY such row is decisive (returns MORTGAGE); else an association plaintiff
    yields HOA; else '' (unknown -> caller falls back to the case-number prefix)."""
    if not lc:
        return ''
    result = ''
    for d in docs or []:
        if (d.get('CaseNumber') or '').upper() != lc:
            continue
        t = _fc_type_plaintiff(d.get('CrossPartyName'))
        if t == 'MORTGAGE':
            return 'MORTGAGE'                       # bank/lender plaintiff — decisive
        if t == 'HOA':
            result = 'HOA'
    return result


def analyze(docs, owner, judgment, ftype='', lead_case=''):
    """Open-mortgage picture + deal-killer flags for the subject owner. Precision > recall: guards force
    conf='low' on common names / MERS ambiguity so we never assert a fantasy (the template shows low-conf
    flags as 'possible - verify', never solid red)."""
    lc = (lead_case or '').upper()
    # TRUE type from the plaintiff on THIS case's rows overrides the case-number prefix guess passed in as
    # `ftype` (which mislabels HOA-in-circuit-court cases as MORTGAGE). Prefix stays as the fallback.
    ftype = _plaintiff_ftype(docs, lc) or ftype
    key = _lf(owner)
    base = {'liens': [], 'open_count': 0, 'junior': 0, 'first_est': 0, 'surv': 0, 'surv_first': 0,
            'ftype': ftype, 'deeded': None, 'deed_conf': '', 'second_fc': None, 'conf': 'none', 'nrec': 0}
    if not key[0]:
        return base
    exact = [d for d in docs if _lf(d.get('Name')) == key]           # exact (last, first) — excludes namesakes
    def is_m(d): return (d.get('DocTypeDescription') or '').upper().startswith('MORTGAGE')
    def is_s(d): return bool(re.search(r'SATISF|RELEASE|REVOKE|TERMINAT', (d.get('DocTypeDescription') or '').upper()))
    def borrower(d): return (d.get('Party') or '').strip().upper() == 'FROM'   # owner is mortgagor/grantor, not the lender
    morts = [d for d in exact if is_m(d) and borrower(d) and _num(d.get('Consideration')) > 0]   # priced mortgages the owner OWES
    sats = [d for d in exact if is_s(d)]
    for d in morts + sats: d['_dt'] = _jsdate(d.get('RecordDate'))
    used = set(); liens = []; opens = []
    # --- kimi: layered satisfaction matching (the Salazar fix) -------------------------------------
    # AcclaimWeb's raw OPEN flag lies: old mortgages whose satisfactions were recorded under an ASSIGNEE
    # (Chase->WaMu->HSBC) or a successor servicer stay 'open' forever, and phantom stacks like Salazar's
    # $358,950 'surviving senior' get prefilled as fact. Kill rules, applied in order per mortgage:
    #  1) a release by the lender or ANY assignee downstream in the assignment chain
    #  2) a LENDER-party release within 24 months after the mortgage (successor servicer, unrecorded assignment)
    #  3) refi-kill: a newer different-lender mortgage >=70% of the balance within 36 months
    #  4) sale-kill: an arm's-length deed transfer (consideration >=70% of the balance) after the mortgage
    assigns = {}
    for d in exact:
        if 'ASSIGNMENT' in (d.get('DocTypeDescription') or '').upper():
            fr = _inst(d.get('Name')); to = _inst(d.get('CrossPartyName'))
            if fr and to and fr != to:
                assigns.setdefault(fr, set()).add(to)
    def chain_of(lend):
        out, frontier, seen = {lend}, [lend], set()
        while frontier:
            cur = frontier.pop()
            if cur in seen: continue
            seen.add(cur)
            for nxt in assigns.get(cur, ()):
                if nxt not in out:
                    out.add(nxt); frontier.append(nxt)
        return out
    _LENDER_RE = re.compile(r'BANK|MORTGAGE|MTGE|LOAN|FINANC|SAVING|CREDIT|FUNDING|SERVICING|FEDERAL|NATIONAL', re.I)
    def _months(a, b):                                   # months from date a to date b ('YYYY-MM-DD')
        try: return (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:7]) - int(a[5:7]))
        except Exception: return 99
    for m in sorted(morts, key=lambda x: x['_dt']):
        lend = _inst(m.get('CrossPartyName'))
        mers = bool(MERS_RE.search(m.get('CrossPartyName') or ''))
        chain = chain_of(lend) if lend else {lend}
        amt = _num(m.get('Consideration'))
        is_open = True
        for i, s in enumerate(sats):                                        # rule 1: release in the chain
            if i in used or not s['_dt'] or s['_dt'] < m['_dt']: continue
            if _inst(s.get('CrossPartyName')) in chain:
                used.add(i); is_open = False; break
        if is_open:                                                          # rule 2: lender release kills the NEWEST PRIOR within 3-24mo
            for i, s in enumerate(sats):
                if i in used or not s['_dt']: continue
                if 3 <= _months(m['_dt'], s['_dt']) <= 24 and _LENDER_RE.search(s.get('CrossPartyName') or ''):
                    newer = [x for x in opens if x['d'] > m['_dt'] and _months(m['_dt'], x['d']) <= 24]
                    if not newer:
                        used.add(i); is_open = False; break
        if is_open:                                                          # rule 3: refi-kill ONLY in true-refi shape (>=90%, 24mo)
            for m2 in morts:
                if m2 is m: continue
                if (0 < _months(m['_dt'], m2['_dt']) <= 24 and _num(m2.get('Consideration')) >= amt * 0.9
                        and _inst(m2.get('CrossPartyName')) != lend and _inst(m2.get('CrossPartyName')) not in chain):
                    is_open = False; break
        if is_open:                                                          # rule 4: sale-kill
            for d in exact:
                if not (d.get('DocTypeDescription') or '').upper().startswith('DEED'): continue
                dd = _jsdate(d.get('RecordDate'))
                if dd and dd >= m['_dt'] and _num(d.get('Consideration')) >= amt * 0.7:
                    is_open = False; break
        row = {'d': m['_dt'], 'amt': round(amt),
               'party': (m.get('CrossPartyName') or '')[:40], 'bp': m.get('BookPage', ''),
               'st': 'OPEN' if is_open else 'SATISFIED', 'mers': mers}
        liens.append(row)
        if is_open: opens.append(row)
    conf = 'ok'
    if not exact: conf = 'none'
    if len(exact) > 35: conf = 'low'                                 # common name -> many people/properties
    if len(morts) > 5: conf = 'low'
    if len(opens) > 3: conf = 'low'                                  # one residential parcel rarely has >3 truly-open mtgs
    if sum(1 for o in opens if o['mers']) and len(opens) > 1: conf = 'low'   # MERS can't be uniquely paired

    # --- surviving mortgage ---------------------------------------------------------------------
    # HOA sale: the WHOLE first mortgage survives, so DON'T anchor to the tiny HOA judgment. Otherwise
    # (a mortgage foreclosure) the foreclosing 1st is wiped and only a real 2nd survives.
    surv = surv_first = junior = first = 0
    juniors_post = 0                                          # kimi: opens recorded AFTER the foreclosing one (payoff on owner purchase)
    if opens:
        if ftype == 'HOA':
            surv = sum(o['amt'] for o in opens)                     # total open loan stack that survives
            surv_first = max(o['amt'] for o in opens)               # the first mortgage (headline number)
        else:
            anchor = (lambda o: abs(o['amt'] - judgment)) if (judgment and judgment > 0) else (lambda o: -o['amt'])
            fore = min(opens, key=anchor)                           # the foreclosing 1st (nearest judgment, else largest)
            first = fore['amt']
            junior = surv = sum(o['amt'] for o in opens if o is not fore)   # the surviving 2nd
            juniors_post = sum(o['amt'] for o in opens if o is not fore and o['d'] >= fore['d'])  # juniors recorded after it

    # --- already deeded to another investor? (the McNulty / "you're too late" signal) -----------
    # "Recent" is anchored to THIS foreclosure's lis-pendens (a deed only counts if it post-dates the filing),
    # else ~1 year before the newest record on file — never the earliest old lien in a decades-long chain.
    latest = max([_jsdate(d.get('RecordDate')) for d in docs] or ['2026-01-01'])
    floor = str(int(latest[:4]) - 1) + latest[4:]
    lp = [_jsdate(d.get('RecordDate')) for d in exact
          if (d.get('CaseNumber') or '').upper() == lc and 'LIS PEND' in (d.get('DocTypeDescription') or '').upper()]
    anchor_dt = min(lp) if lp else floor
    deeded = None
    deeds = sorted([d for d in exact if (d.get('DocTypeDescription') or '').upper().startswith('DEED')
                    and borrower(d)], key=lambda x: _jsdate(x.get('RecordDate')), reverse=True)
    for d in deeds:
        dd = _jsdate(d.get('RecordDate')); grantee = (d.get('CrossPartyName') or '').strip()
        if not grantee or dd < anchor_dt:
            continue
        g = re.sub(r'[^A-Z]', '', grantee.upper())
        investor = bool(INVESTOR_RE.search(grantee)) and key[0] not in g   # a company/trust, not a same-surname family deed
        if investor:
            deeded = {'d': dd, 'grantee': grantee[:40]}
            break
    # the deed flag has its OWN confidence — an exact-name, post-filing deed to a clearly-named company is
    # reliable even when the mortgage chain is noisy; only a very common name (huge record set) downgrades it.
    deed_conf = ('ok' if (deeded and len(exact) <= 45) else ('low' if deeded else ''))

    # --- a SECOND, hidden foreclosure? (the Bloom / Tucker signal). Only meaningful when THIS lead is the
    # small HOA case: a CACE mortgage foreclosure running underneath it. For a lead that's already a mortgage
    # foreclosure, another CACE is just namesake noise, so we don't flag it. ---
    second_fc = None
    if ftype == 'HOA':
        for d in sorted(exact, key=lambda x: _jsdate(x.get('RecordDate')), reverse=True):
            cn = (d.get('CaseNumber') or '').strip().upper()
            if not cn or cn == lc:
                continue
            if not re.search(r'LIS PEND|FINAL JUDG|CERT', (d.get('DocTypeDescription') or '').upper()):
                continue
            if cn.startswith('CACE') or '-CA-' in cn:               # a circuit = mortgage foreclosure
                second_fc = {'case': cn, 'party': (d.get('CrossPartyName') or '')[:40]}
                break

    # --- open non-mortgage liens (kimi: feeds the deal-modal HOA / code / IRS prefills) -------------
    # Lien + Judgment-type records on the owner, bucketed by who holds them: HOA/association (estoppel
    # estimate), IRS/federal, code/municipal. A lien dies when a Release/Satisfy record names the same
    # institution (same _inst normalization the mortgage chain uses).
    _IRS_RE = re.compile(r'INTERNAL\s+REV|UNITED\s+STATES|\bIRS\b', re.I)
    _CODE_RE = re.compile(r'\bCITY\s+OF\b|\bCOUNTY\b|CODE\s+ENFORCEMENT|MUNICIPAL|\bBROWARD\b|STATE OF FLORIDA|\bPACE\b|CLEAN ENERGY', re.I)
    _LIEN_DOC_RE = re.compile(r'^(LIEN|JUDGMENT|NOTICE|CLAIM|CERT)', re.I)
    def _released(party_inst):
        for s in sats:
            if s.get('_dt') and _inst(s.get('CrossPartyName')) == party_inst:
                return True
        return False
    hoa_open = code_open = irs_open = 0
    for d in exact:
        if not _LIEN_DOC_RE.match((d.get('DocTypeDescription') or '').upper().strip()):
            continue
        amt = _num(d.get('Consideration'))
        if amt <= 0:
            continue
        party = d.get('CrossPartyName') or ''
        inst = _inst(party)
        if not inst or _released(inst):
            continue
        if _IRS_RE.search(party):
            irs_open += amt
        elif _HOA_RE.search(party) or _ASSN_RE.search(party):
            hoa_open += amt
        elif _CODE_RE.search(party):
            code_open += amt

    return {'liens': [{k: v for k, v in r.items() if k != 'mers'} for r in liens],
            'open_count': len(opens), 'junior': junior, 'first_est': first,
            'surv': surv, 'surv_first': surv_first, 'juniors_post': juniors_post,
            'hoa_open': hoa_open, 'code_open': code_open, 'irs_open': irs_open, 'ftype': ftype,
            'deeded': deeded, 'deed_conf': deed_conf, 'second_fc': second_fc, 'conf': conf, 'nrec': len(exact)}


def _search_name(lead):
    """Build a 'LAST, FIRST' AcclaimWeb query from the lead's raw owner string. Use the FIRST given name only
    (drop the middle initial): AcclaimWeb narrows on the middle initial and misses records indexed without it
    (e.g. a 2015 mortgage under 'MCNULTY, CHRISTINE' vs the lead's 'MCNULTY, CHRISTINE A'). _lf() re-isolates
    the exact (last, first) afterward, so broadening the query only helps recall."""
    raw = (lead.get('owners', '') or '').upper()
    raw = re.sub(r'\s*&\s*[WH].*$', '', raw); raw = re.sub(r'\bH/[EW]\b', '', raw).strip()
    if ',' in raw:
        last, _, rest = raw.partition(',')
        first = (rest.strip().split() or [''])[0]
        return f"{last.strip()}, {first}" if first else last.strip()
    toks = raw.split()
    return f"{toks[0]}, {toks[1]}" if len(toks) >= 2 else raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', default='')
    ap.add_argument('--tier', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--refresh', action='store_true', help='re-trace even already-cached cases')
    a = ap.parse_args()

    leads = json.load(open(LEADS, encoding='utf-8'))
    out = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}

    picked = []
    for r in leads:
        case = r.get('case', '') or ''
        if a.case and case != a.case: continue
        if a.tier and (r.get('tier', '') or '') != a.tier: continue
        owner = r.get('owners', '') or ''
        if not owner or COMPANY_RE.search(owner): continue          # companies: no personal chain to isolate
        if case in out and not (a.refresh or a.case): continue
        picked.append(r)
    if a.limit: picked = picked[:a.limit]

    print(f"{len(picked)} Broward lead(s) to trace via AcclaimWeb (no captcha, curl session)")
    if not picked:
        return
    sess = start_session()
    if not sess:
        print("ABORT: could not establish an AcclaimWeb session (Cloudflare block / site down). Try again later.")
        return
    print(f"session up (doctypes {sess['doctypes'].count(',')+1}, booktypes {sess['booktypes'].count(',')+1})")

    done = hits = blocked = 0
    for r in picked:
        case = r.get('case', ''); owner = r.get('owners', ''); judg = _num(r.get('judg'))
        docs = search_docs(sess, _search_name(r))
        if docs is None:
            blocked += 1
            print(f"  --  {case:18} {owner[:26]:26} (blocked / no data)")
            if blocked >= 5 and blocked == done + blocked:          # session died early -> re-establish once
                sess = start_session() or sess
            time.sleep(0.8); continue
        ftype = _fc_type(case)
        res = analyze(docs, owner, judg, ftype=ftype, lead_case=case)
        res['traced'] = time.strftime('%Y-%m-%d'); res['owner'] = owner
        out[case] = res
        done += 1
        flags = []
        if res['deeded']: flags.append(f"TAKEN->{res['deeded']['grantee']}")
        if res['second_fc']: flags.append(f"2ND-FC {res['second_fc']['case']}")
        if ftype == 'HOA' and res['surv_first']: flags.append(f"surv 1st ~${res['surv_first']:,}")
        elif res['open_count'] >= 2 and res['junior']: flags.append(f"2nd ~${res['junior']:,}")
        if flags: hits += 1
        flag = ('  <-- ' + ' | '.join(flags) + f" (conf {res['conf']})") if flags else ''
        print(f"  ok  {case:18} {owner[:26]:26} {ftype or '?':8} {res['nrec']:>3} recs{flag}")
        json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
        time.sleep(0.5)
    print(f"\nDONE: {done} traced ({hits} confident surviving-2nd, {blocked} blocked). -> broward_liens.json")


if __name__ == '__main__':
    main()
