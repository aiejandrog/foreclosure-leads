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
import argparse, datetime, html, json, os, re, subprocess, tempfile, time
from records_liens import untraceable_owner   # shared junk-owner rule

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'broward_leads.json')
OUT = os.path.join(HERE, 'broward_liens.json')          # Case # -> lien result (gitignored)
LINKS = os.path.join(HERE, '_acclaim_links.json')       # instrument -> parsed details page (gitignored)
PAIRING_VERSION = 2      # 2 = releases paired by the recorder's own DocLink / Doc Extension (2026-09-16)
LINK_TTL_DAYS = 14       # an unreleased mortgage can gain its satisfaction later: re-read it after this
LINK_BUDGET = 60         # details pages one chain may fetch per run; past it the chain stays queued
LINK_RUN_BUDGET = 600    # details pages per whole run (~13 min at ~1.3 s/page). The nightly --all can hold
                         # hundreds of untraced leads; past this they trace name-only and queue for relink
LINK_RUN_SECONDS = 900   # ...and wall time spent reading them: a slow Cloudflare hour stretches a page to
                         # its 45 s curl timeout, and a page count alone would not bound the nightly
LINK_TRIP = 6            # consecutive unreadable details pages -> stop asking for the rest of the run
_CONF_RANK = {'none': 0, 'low': 1, 'ok': 2}
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
# kimi: pin the NATIVE Windows curl (Schannel). Bare 'curl' resolves through PATH, and when this
# script is launched from Git Bash the mingw64 curl shadows System32's — Cloudflare blocks that
# fingerprint, which is why sessions aborted even while System32 curl sailed through (2026-07-20).
CURL = r'C:\Windows\System32\curl.exe' if os.name == 'nt' and os.path.exists(r'C:\Windows\System32\curl.exe') else 'curl'

def _curl(url, post=None, timeout=45):
    cmd = [CURL, '-s', '-m', str(timeout), '-A', UA, '-c', JAR, '-b', JAR,
           '-H', 'Accept: text/html,application/json,*/*;q=0.8', '-H', 'Accept-Language: en-US,en;q=0.9']
    if post is not None:
        cmd += ['-X', 'POST', '-H', 'Content-Type: application/x-www-form-urlencoded',
                '-H', 'X-Requested-With: XMLHttpRequest', '-H', 'Referer: ' + BASE + '/Search/SearchTypeName']
        for k, v in post:
            cmd += ['--data-urlencode', f'{k}={v}']
    cmd += [url]
    out = ''
    # kimi: when Cloudflare trips, it challenges per-request at a coin-flip rate (verified 2026-07-20:
    # the SAME request flips 403<->200 within seconds, headers/jar irrelevant). A challenge page says
    # "Just a moment" — retry the identical request a few times and it rides through.
    for _ in range(5):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout + 10)
            out = r.stdout or ''
        except Exception:
            out = ''
        if 'Just a moment' not in out and 'cf-chl' not in out:
            break
        time.sleep(3)
    return out


def start_session():
    """Accept the disclaimer and read the all-types code lists off the search form."""
    try: os.remove(JAR)          # kimi: never reuse a jar — a blocked run leaves poisoned cookies
    except OSError: pass
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


# ---- the recorder's own links: which release satisfies which mortgage (2026-09-16) ----------------
# The name index cannot say which release goes with which mortgage. Broward indexes most satisfactions
# "To MORTGAGE ELECTRONIC REGISTRATION SYSTEMS INC", so every MERS release matched every MERS mortgage by
# lender name and analyze()'s rules paired them oldest-first. On a live five-mortgage lis pendens chain that
# left a $152,000 loan OPEN nine months after its release was recorded, and marked a $319,750 loan
# SATISFIED although it was assigned in 2026. The details page carries the truth: a mortgage lists its
# children under "Doc Extension", and a release names its parent under "DocLink". Linking is present on
# most records from ~1993; older and private-party pairs can be unlinked, so the lender-name rules stay as
# the FALLBACK — but never with a release that is linked to something else.
_REL_RE = re.compile(r'SATISF|RELEASE|REVOKE|TERMINAT', re.I)
_LENDER_RE = re.compile(r'BANK|MORTGAGE|MTGE|LOAN|FINANC|SAVING|CREDIT|FUNDING|SERVICING|FEDERAL|NATIONAL', re.I)
_ROW_SPLIT_RE = re.compile(r'class="[^"]*\bdocDetailRow\b[^"]*"')
_ROW_RE = re.compile(r'class="detailLabel">(.*?)</div>\s*<div class="(?:listDocDetails|formInput)"[^>]*>'
                     r'(.*?)(?:<div class="expandDown"|<hr)', re.S)
_link_fails = [0]        # consecutive unreadable details pages this run (the Cloudflare breaker)
_link_spent = [0]        # details pages requested this run (LINK_RUN_BUDGET)
_link_clock = [0.0]      # seconds spent reading them this run (LINK_RUN_SECONDS)


def _flat(fragment):
    """HTML fragment -> one line of text (&nbsp; included)."""
    return re.sub(r'[\s\xa0]+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))).strip()


def _norm_bp(bp):
    """'50746/663' -> '50746/663'; '0/0', '/', '' -> '' (no book/page on record)."""
    m = re.match(r'\s*(\d+)\s*/\s*(\d+)\s*$', str(bp or ''))
    return '%d/%d' % (int(m.group(1)), int(m.group(2))) if (m and int(m.group(1))) else ''


def _anchor_ref(text):
    """One DocLink / Doc Extension anchor -> {'k': ['BP:<book/page>', 'I:<instrument>'], 't': type code}, or
    None. Shapes seen live: '115415213 [M]', 'O 112261923', '113005367', 'O 32505/1578 101552188',
    'O 20924/869', 'O 0/0 113151309', '42049/905'."""
    t = ' '.join(str(text or '').upper().split())
    keys = ['BP:' + b for b in (_norm_bp(x) for x in re.findall(r'\d+\s*/\s*\d+', t)) if b]
    keys += ['I:' + i for i in re.findall(r'\b\d{6,}\b', re.sub(r'\d+\s*/\s*\d+', ' ', t))]
    code = re.search(r'\[([A-Z]+)\]', t)
    return {'k': keys, 't': code.group(1) if code else ''} if keys else None


def parse_details(page):
    """AcclaimWeb details page -> {'i': instrument, 't': doc type, 'p': [DocLink refs], 'c': [Doc Extension
    refs]}. None when the body is not a details page (Cloudflare interstitial, block, error page): an
    unreadable page is UNKNOWN, never 'this document has no links'."""
    if 'docDetailRow' not in (page or ''):
        return None
    rows = {}
    for chunk in _ROW_SPLIT_RE.split(page)[1:]:
        m = _ROW_RE.search(chunk)
        if m:
            rows[_flat(m.group(1)).rstrip(':').strip()] = m.group(2)

    def refs(label):
        out = []
        for a in re.findall(r'<a\b[^>]*>(.*?)</a>', rows.get(label, ''), re.S):
            r = _anchor_ref(_flat(a))
            if r and r not in out:
                out.append(r)
        return out
    inst = re.sub(r'\D', '', _flat(rows.get('Instrument Number', '')))
    dtype = _flat(rows.get('Doc Type', ''))
    if not inst or not dtype:
        return None
    return {'i': inst, 't': dtype, 'p': refs('DocLink'), 'c': refs('Doc Extension')}


def fetch_details(inst, tries=3):
    """Read one details page. _curl rides through 'Just a moment', but the 'Enable JavaScript and cookies'
    interstitial slips past that check — so refuse any body that is not the details page for THIS
    instrument and retry a few times, spaced. None = unknown."""
    for attempt in range(tries):
        got = parse_details(_curl(BASE + '/details/JumpToInstrumentNumber/27/%s' % inst))
        if got is not None and got['i'] == str(inst):
            _link_fails[0] = 0
            return got
        if attempt < tries - 1:
            time.sleep(2 + 2 * attempt)
    _link_fails[0] += 1
    return None


def _load_links():
    try:
        with open(LINKS, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_links(cache):
    tmp = LINKS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, separators=(',', ':'))
    os.replace(tmp, LINKS)


def _is_mort(d): return (d.get('DocTypeDescription') or '').upper().startswith('MORTGAGE')
def _is_sat(d): return bool(_REL_RE.search(d.get('DocTypeDescription') or ''))
def _is_borrower(d): return (d.get('Party') or '').strip().upper() == 'FROM'   # owner is mortgagor/grantor, not the lender


def _full_release(doctype):
    """A satisfaction that ends the lien. A PARTIAL release frees part of the land and leaves the loan."""
    t = (doctype or '').upper()
    return bool(_REL_RE.search(t)) and 'PARTIAL' not in t


def _doc_keys(d):
    inst = str(d.get('InstrumentNumber') or '').strip()
    bp = _norm_bp(d.get('BookPage'))
    return (['I:' + inst] if inst.strip('0') else []) + (['BP:' + bp] if bp else [])


def _dedupe(docs):
    """One row per (instrument, party side). The name index returns an instrument once per NAME VARIANT
    ('JOSE L' and 'JOSE LUIS' both carry 117161162, and _lf folds both into the owner), so a duplicated
    mortgage read as a second open lien — and once releases pair by link, the copy the link does not
    reach would be left behind as a phantom OPEN."""
    seen, out = set(), []
    for d in docs:
        inst = str(d.get('InstrumentNumber') or '').strip()
        if inst.strip('0'):
            k = (inst, (d.get('Party') or '').strip().upper())
            if k in seen:
                continue
            seen.add(k)
        out.append(d)
    return out


def _exact_docs(docs, owner):
    """The owner's own rows: exact (last, first) for a person — excludes namesakes."""
    key = _lf(owner)
    if not key[0]:
        return []
    # kimi: companies — _lf collapses 'OCEAN BREEZE 777 LLC' and sister 'OCEAN BREEZE 888 LLC' to the
    # same ('OCEAN','BREEZE') key, which would blend two entities' chains into one fantasy. Match the
    # FULL alpha-digit name instead (punctuation/comma variants normalize away).
    if COMPANY_RE.search(owner or ''):
        _ck = re.sub(r'[^A-Z0-9]', '', (owner or '').upper())
        return [d for d in docs if re.sub(r'[^A-Z0-9]', '', (d.get('Name') or '').upper()) == _ck]
    return [d for d in docs if _lf(d.get('Name')) == key]


def link_chain(docs, owner, lead_case, cache, fetch=None, today=None, budget=LINK_BUDGET, pause=0.4,
               run_budget=None, run_seconds=None):
    """-> (links, complete). links = instrument -> parsed details page for every document whose links
    decide this chain: each priced mortgage the owner owes, each release, THIS case's lis pendens, and any
    child of a still-unreleased mortgage that the name index does not carry (its type is unknown until
    read). `cache` is reused and filled in place: a release's DocLink never changes, so it is read once; a
    mortgage with no known release is re-read after LINK_TTL_DAYS, because its satisfaction may not be
    recorded yet. complete = every wanted page is in hand (nothing failed, nothing cut by the budget or
    the breaker)."""
    fetch = fetch or fetch_details
    today = today or time.strftime('%Y-%m-%d')
    run_budget = LINK_RUN_BUDGET if run_budget is None else run_budget
    run_seconds = LINK_RUN_SECONDS if run_seconds is None else run_seconds
    pool = _dedupe(_exact_docs(docs, owner))
    lc = (lead_case or '').upper()
    in_chain = {}
    for d in pool:
        for k in _doc_keys(d):
            in_chain.setdefault(k, d)
    lenders = set()
    for d in pool:
        if _is_mort(d) and _is_borrower(d) and _num(d.get('Consideration')) > 0:
            lenders.add(_inst(d.get('CrossPartyName')))
        elif 'ASSIGNMENT' in (d.get('DocTypeDescription') or '').upper():
            lenders.update((_inst(d.get('Name')), _inst(d.get('CrossPartyName'))))
    lenders.discard('')

    def pairable(s):
        # Only a release that could ever pair with this owner's loans is worth a page: one from a lender or
        # assignee on the chain, MERS, or a lender-shaped name. An auto insurer's or a city's release never
        # can (a loan's own page still reaches any child directly), and a judgment-heavy chain carries dozens.
        cp = s.get('CrossPartyName') or ''
        return _inst(cp) in lenders or bool(MERS_RE.search(cp)) or bool(_LENDER_RE.search(cp))
    links, state = {}, {'complete': True, 'spent': 0}

    def get(inst, fresh_enough=True):
        rec = cache.get(inst)
        if rec is not None and fresh_enough:
            links[inst] = rec
            return rec
        if (state['spent'] >= budget or _link_fails[0] >= LINK_TRIP or _link_spent[0] >= run_budget
                or _link_clock[0] >= run_seconds):
            state['complete'] = False
            if rec is not None:
                links[inst] = rec              # an older read beats none; the chain stays queued
            return rec
        state['spent'] += 1
        _link_spent[0] += 1
        t0 = time.time()
        got = fetch(inst)
        if pause:
            time.sleep(pause)
        _link_clock[0] += time.time() - t0
        if got is None:
            state['complete'] = False
            if rec is not None:
                links[inst] = rec
            return rec
        got = dict(got, f=today)
        cache[inst] = got
        links[inst] = got
        return got

    def child_release(ref):
        d = next((in_chain[k] for k in ref['k'] if k in in_chain), None)
        if d is not None:
            return _is_sat(d) and _full_release(d.get('DocTypeDescription'))
        ci = next((k[2:] for k in ref['k'] if k.startswith('I:')), '')
        return bool(ci) and _full_release((cache.get(ci) or {}).get('t'))

    def stale(rec):
        try:
            age = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(rec.get('f') or '')).days
        except ValueError:
            return True
        return age > LINK_TTL_DAYS

    for d in pool:
        inst = str(d.get('InstrumentNumber') or '').strip()
        is_lp = bool(lc) and (d.get('CaseNumber') or '').upper() == lc and \
            'LIS PEND' in (d.get('DocTypeDescription') or '').upper()
        if inst.strip('0') and ((_is_sat(d) and pairable(d)) or is_lp):
            get(inst)
    for d in pool:
        inst = str(d.get('InstrumentNumber') or '').strip()
        if not (inst.strip('0') and _is_mort(d) and _is_borrower(d) and _num(d.get('Consideration')) > 0):
            continue
        rec = cache.get(inst)
        rec = get(inst, fresh_enough=rec is not None and (any(child_release(r) for r in rec.get('c') or [])
                                                          or not stale(rec)))
        if rec is None:
            continue
        released = any(child_release(r) for r in rec.get('c') or [] if any(k in in_chain for k in r['k']))
        for ref in rec.get('c') or []:              # children the name index does not carry: read their type
            if any(k in in_chain for k in ref['k']):
                continue
            ci = next((k[2:] for k in ref['k'] if k.startswith('I:')), '')
            if ci and (ci in cache or not released):
                get(ci)
    return links, state['complete']


def _pair_by_links(pool, morts, sats, links, lc):
    """-> (linked_rel, claimed, fore_link).
    linked_rel: id(mortgage) -> instrument of the full release linked to it (the mortgage's own Doc
                Extension names the release, or the release's DocLink names the mortgage).
    claimed:    id(release) whose DocLink names a parent — in this chain, or a recorded instrument outside
                it. That release belongs to its parent and is never handed to the lender-name rules. A
                book/page-only link that matches nothing here stays unclaimed: the recorder types those by
                hand (41644/1728 for a mortgage recorded at 40644/1728 was seen live).
    fore_link:  the mortgage THIS case's lis pendens names — the loan being foreclosed, so it is open."""
    idx = {}
    for d in pool:
        for k in _doc_keys(d):
            idx.setdefault(k, d)
    mort_ids = {id(m) for m in morts}

    def resolve(ref):
        return next((idx[k] for k in ref.get('k') or [] if k in idx), None)

    def ref_inst(ref):
        return next((k[2:] for k in ref.get('k') or [] if k.startswith('I:')), '')
    linked_rel, claimed = {}, set()
    for s in sats:
        rec = links.get(str(s.get('InstrumentNumber') or '').strip())
        if not rec or not rec.get('p'):
            continue
        parents = [resolve(r) for r in rec['p']]
        if any(p is not None for p in parents):
            claimed.add(id(s))
            if _full_release(s.get('DocTypeDescription')):
                for p in parents:
                    if p is not None and id(p) in mort_ids:
                        linked_rel.setdefault(id(p), str(s.get('InstrumentNumber')).strip())
        elif any(ref_inst(r) for r in rec['p']):
            claimed.add(id(s))
    for m in morts:
        rec = links.get(str(m.get('InstrumentNumber') or '').strip())
        for ref in (rec or {}).get('c') or []:
            child = resolve(ref)
            if child is not None:
                if _is_sat(child):
                    claimed.add(id(child))
                    if _full_release(child.get('DocTypeDescription')):
                        linked_rel.setdefault(id(m), str(child.get('InstrumentNumber')).strip())
                continue
            ci = ref_inst(ref)
            if ci and _full_release((links.get(ci) or {}).get('t')):
                linked_rel.setdefault(id(m), ci)
    fore_link = None
    for d in pool:
        if not lc or (d.get('CaseNumber') or '').upper() != lc or \
                'LIS PEND' not in (d.get('DocTypeDescription') or '').upper():
            continue
        for ref in (links.get(str(d.get('InstrumentNumber') or '').strip()) or {}).get('p') or []:
            p = resolve(ref)
            if p is not None and id(p) in mort_ids and id(p) not in linked_rel:
                fore_link = p
                break
        if fore_link is not None:
            break
    return linked_rel, claimed, fore_link


def analyze(docs, owner, judgment, ftype='', lead_case='', links=None, _legacy=False):
    """Open-mortgage picture + deal-killer flags for the subject owner. Precision > recall: guards force
    conf='low' on common names / MERS ambiguity so we never assert a fantasy (the template shows low-conf
    flags as 'possible - verify', never solid red).
    links: instrument -> parse_details() record (link_chain). The recorder's links decide which release
    satisfies which mortgage; the lender-name rules below are the fallback for everything unlinked, and
    conf may never rise above the pre-link algorithm's on a chain the fallback touched. _legacy=True runs
    that pre-link algorithm exactly (no links, no de-duplication)."""
    lc = (lead_case or '').upper()
    ftype_in = ftype
    # TRUE type from the plaintiff on THIS case's rows overrides the case-number prefix guess passed in as
    # `ftype` (which mislabels HOA-in-circuit-court cases as MORTGAGE). Prefix stays as the fallback.
    ftype = _plaintiff_ftype(docs, lc) or ftype
    key = _lf(owner)
    base = {'liens': [], 'open_count': 0, 'junior': 0, 'first_est': 0, 'surv': 0, 'surv_first': 0,
            'ftype': ftype, 'deeded': None, 'deed_conf': '', 'second_fc': None, 'conf': 'none', 'nrec': 0}
    if not key[0]:
        return base
    exact = _exact_docs(docs, owner)
    is_m, is_s, borrower = _is_mort, _is_sat, _is_borrower
    # `exact` stays as indexed for the record-count guards (namesake noise); the pairing reads one row per
    # instrument (_dedupe). _legacy reproduces the pre-link algorithm exactly — the conf ceiling uses it.
    pool = exact if _legacy else _dedupe(exact)
    morts = [d for d in pool if is_m(d) and borrower(d) and _num(d.get('Consideration')) > 0]   # priced mortgages the owner OWES
    sats = [d for d in pool if is_s(d)]
    for d in morts + sats: d['_dt'] = _jsdate(d.get('RecordDate'))
    used = set(); liens = []; opens = []; fore_row = None
    newest = max([_jsdate(d.get('RecordDate')) for d in docs] or ['0000-00-00'])
    links = {} if _legacy else (links or {})
    linked_rel, claimed, fore_link = _pair_by_links(pool, morts, sats, links, lc)
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
    def _months(a, b):                                   # months from date a to date b ('YYYY-MM-DD')
        try: return (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:7]) - int(a[5:7]))
        except Exception: return 99
    for m in sorted(morts, key=lambda x: x['_dt']):
        lend = _inst(m.get('CrossPartyName'))
        mers = bool(MERS_RE.search(m.get('CrossPartyName') or ''))
        chain = chain_of(lend) if lend else {lend}
        amt = _num(m.get('Consideration'))
        inst = str(m.get('InstrumentNumber') or '').strip()
        rel = linked_rel.get(id(m), '')
        # The recorder's link decides when it exists. The rules are the FALLBACK: they never touch the loan
        # THIS case's lis pendens names, and a release linked to anything (claimed) is not theirs to pair.
        # Where the loan's own page WAS read and names no release, only rule 1 may still satisfy it — an
        # unlinked release from the same institution (private and pre-link pairs exist). Rules 2-4 pair by
        # weaker evidence (any lender's release; a refi or deed with no release at all) and run only where
        # no page could answer: on read pages rule 2 handed a Wells Fargo release to a MERS loan, and rule 4
        # fired on the owner's own purchase deed (it checks neither direction nor day) and killed a loan
        # that was assigned in 2026.
        is_open = not rel
        fallback = is_open and m is not fore_link
        inferred = fallback and inst not in links
        for i, s in enumerate(sats if fallback else ()):                    # rule 1: release in the chain
            if i in used or id(s) in claimed or not s['_dt'] or s['_dt'] < m['_dt']: continue
            if _inst(s.get('CrossPartyName')) in chain:
                used.add(i); is_open = False; break
        if inferred and is_open:                                             # rule 2: lender release kills the NEWEST PRIOR within 3-24mo
            for i, s in enumerate(sats):
                if i in used or id(s) in claimed or not s['_dt']: continue
                if 3 <= _months(m['_dt'], s['_dt']) <= 24 and _LENDER_RE.search(s.get('CrossPartyName') or ''):
                    newer = [x for x in opens if x['d'] > m['_dt'] and _months(m['_dt'], x['d']) <= 24]
                    if not newer:
                        used.add(i); is_open = False; break
        if inferred and is_open:                                             # rule 3: refi-kill ONLY in true-refi shape (>=90%, 24mo)
            for m2 in morts:
                if m2 is m: continue
                if (0 < _months(m['_dt'], m2['_dt']) <= 24 and _num(m2.get('Consideration')) >= amt * 0.9
                        and _inst(m2.get('CrossPartyName')) != lend and _inst(m2.get('CrossPartyName')) not in chain):
                    is_open = False; break
        if inferred and is_open:                                             # rule 4: sale-kill
            for d in exact:
                if not (d.get('DocTypeDescription') or '').upper().startswith('DEED'): continue
                dd = _jsdate(d.get('RecordDate'))
                if dd and dd >= m['_dt'] and _num(d.get('Consideration')) >= amt * 0.7:
                    is_open = False; break
        if not _legacy and fallback and is_open and _months(m['_dt'], newest) >= 360:
            # matured: 30+ years before the newest record on file, no release in this owner's index. The
            # pre-link rules only ever killed these by accident (rule 4 on the purchase deed); gating those
            # rules off read pages would otherwise resurrect them as phantom juniors (a 1993 loan, live).
            is_open = False
        row = {'d': m['_dt'], 'amt': round(amt),
               'party': (m.get('CrossPartyName') or '')[:40], 'bp': m.get('BookPage', ''),
               'st': 'OPEN' if is_open else 'SATISFIED', 'mers': mers}
        if not _legacy:
            # provenance: 'link' = the recorder links it (its release, or THIS case's lis pendens);
            # 'read' = its page was read and names no release; 'heur' = the lender-name rules decided
            row['inst'] = inst
            row['how'] = ('link' if (rel or m is fore_link)
                          else 'heur' if (not is_open or inst not in links) else 'read')
            if rel:
                row['rel'] = rel
        if m is fore_link:
            fore_row = row
        liens.append(row)
        if is_open: opens.append(row)
    conf = 'ok'
    if not exact: conf = 'none'
    if len(exact) > 35: conf = 'low'                                 # common name -> many people/properties
    if len(morts) > 5: conf = 'low'
    if len(opens) > 3: conf = 'low'                                  # one residential parcel rarely has >3 truly-open mtgs
    if sum(1 for o in opens if o['mers']) and len(opens) > 1: conf = 'low'   # MERS can't be uniquely paired
    # The fallback never earns confidence: when any status came from the lender-name rules (or a page
    # could not be read), conf may not exceed what the pre-link algorithm computes on the same records.
    if not _legacy and any(r.get('how') == 'heur' for r in liens):
        _old = analyze(docs, owner, judgment, ftype=ftype_in, lead_case=lead_case, _legacy=True)['conf']
        if _CONF_RANK.get(_old, 0) < _CONF_RANK.get(conf, 0):
            conf = _old

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
            if fore_row is not None and any(o is fore_row for o in opens):
                fore = fore_row                                     # THIS case's lis pendens names the loan: no guess
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
    # kimi: companies have no LAST,FIRST structure — querying 'OCEAN, BREEZE' finds nothing (verified
    # 2026-07-20: OCEAN BREEZE 777 LLC returned 0 recs). The full name IS the indexed name.
    if COMPANY_RE.search(raw):
        return raw
    return f"{toks[0]}, {toks[1]}" if len(toks) >= 2 else raw


def _needs_relink(h):
    """A cached chain whose statuses predate link pairing (no pv stamp) or whose link read was incomplete."""
    h = h or {}
    return bool(h.get('liens')) and int(h.get('pv') or 1) < PAIRING_VERSION


def _relink_rank(h):
    """Worst first. A mortgage foreclosure whose chain shows NO open loan: a live loan was paired away, so
    the board overstates equity (the dangerous direction). Then chains carrying several MERS loans, where
    every MERS release matched every MERS loan. Then longer chains. A common-name chain (the >35-record
    guard) goes LAST whatever else it shows: it blends several people, stays 'low' however it pairs, and
    one live chain carried 59 mortgages + 84 releases to read."""
    h = h or {}
    liens = h.get('liens') or []
    common = int(h.get('nrec') or 0) > 35
    none_open = h.get('ftype') == 'MORTGAGE' and not int(h.get('open_count') or 0)
    mers = sum(1 for l in liens if MERS_RE.search(l.get('party') or ''))
    return (1 if common else 0, 0 if none_open else 1, 0 if mers >= 2 else 1, -len(liens))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', default='')
    ap.add_argument('--tier', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--refresh', action='store_true', help='re-trace even already-cached cases')
    ap.add_argument('--relink-cap', type=int, default=25,
                    help='per run, re-trace this many cached chains that predate link pairing (worst first)')
    ap.add_argument('--no-links', action='store_true',
                    help='skip details pages: lender-name rules only (chains stay queued for relink)')
    ap.add_argument('--link-pages', type=int, default=LINK_RUN_BUDGET,
                    help='details pages this run may request in total (past it: name-only, queued for relink)')
    ap.add_argument('--link-seconds', type=int, default=LINK_RUN_SECONDS,
                    help='wall seconds this run may spend reading details pages (same fallback past it)')
    a = ap.parse_args()

    leads = json.load(open(LEADS, encoding='utf-8'))
    # LIS PENDENS leads live in their own file and were NEVER traced (2026-08-22). Broward fresh
    # filings — the first-mover front of the funnel, months before anyone else calls — therefore
    # had no lien chain at all, which is why a $1.59M Fort Lauderdale house (CACE-26-004416,
    # Amlong) sat on the board as "owed $0" and ranked as junk. The same blind spot existed in
    # diligence.py's find_lead. Dedupe by case: a lead promoted from LP into the county file must
    # not be traced twice.
    _lp = os.path.join(HERE, 'lp_leads.json')
    if os.path.exists(_lp):
        try:
            _seen = {(r.get('case') or '') for r in leads}
            for _r in json.load(open(_lp, encoding='utf-8')):
                if (str(_r.get('county') or '').strip().upper().startswith('BROW')
                        and (_r.get('case') or '') not in _seen):
                    leads.append(_r)
        except Exception:
            pass
    out = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}

    picked, retries, relinks, skipped = [], [], [], {}
    for r in leads:
        case = r.get('case', '') or ''
        if a.case and case != a.case: continue
        if a.tier and (r.get('tier', '') or '') != a.tier: continue
        owner = r.get('owners', '') or ''
        if not owner: continue
        # AcclaimWeb is searched by NAME only, so a government body or a bare street address has
        # nothing to search on. Shared with records_liens so the rule lives in one place; it is
        # deliberately narrow and never fires on a company suffix (see its docstring — most
        # address-shaped owners here are real LLCs named after their address). Zero of Broward's
        # 164 distinct owners match today; this is insurance for what the scrape brings in next.
        # --case overrides.
        if not a.case:
            why = untraceable_owner(owner)
            if why:
                skipped.setdefault(why, []).append(owner)
                continue
        # kimi: companies ARE traced now (Alejandro 2026-07-20 — OCEAN BREEZE 777 LLC showed
        # manual-only fields because the old COMPANY_RE skip left every LLC/Corp lead with no
        # chain at all). Exact-name isolation works BETTER on companies than on people, and the
        # conf guards (common-name over-match, open-count sanity) catch multi-property blenders.
        if case in out and not (a.refresh or a.case):
            # A FAILED search (conf 'none') is not a result — it used to be cached forever, which
            # is how the tracer reported '0 to trace' while coverage sat at 20% (266 of 406 cached
            # entries were failures, 2026-08-18 audit). Retry a capped batch of them each run;
            # AcclaimWeb is captcha-free, so retries cost only seconds.
            if (out[case] or {}).get('conf') == 'none':
                retries.append(r)
            elif not a.no_links and _needs_relink(out[case]):
                relinks.append(r)
            continue
        picked.append(r)
    n_fresh = len(picked)
    # MIGRATION (2026-09-16): every chain traced before link pairing carries lender-name guesses (a paid-off
    # loan left OPEN, a live one marked SATISFIED). Re-trace a capped batch per run, worst first, so the
    # nightly converges without one multi-hour AcclaimWeb session. They go FIRST, ahead of new leads, for
    # the run's link-page budget: a wrong chain shows invented equity, an untraced lead shows none.
    relinks.sort(key=lambda r: _relink_rank(out.get(r.get('case', '') or '')))
    relinks = relinks[:max(0, a.relink_cap)]
    picked = relinks + picked + retries[:40]
    if a.limit: picked = picked[:a.limit]
    if len(picked) > n_fresh:
        print(f"(+{len(picked) - n_fresh} beyond new leads: {len(relinks)} chain(s) re-paired from before link "
              f"pairing, capped {max(0, a.relink_cap)}/run; {len(retries)} previously-failed search(es), capped 40/run)")

    print(f"{len(picked)} Broward lead(s) to trace via AcclaimWeb (no captcha, curl session)")
    for why, names in sorted(skipped.items()):
        uniq = sorted(set(names))
        print(f"  skipped {len(names)} lead(s) — {why}: "
              + ', '.join(n[:34] for n in uniq[:3]) + (' ...' if len(uniq) > 3 else ''))
    if not picked:
        return
    # kimi: Cloudflare's block here is probabilistic and sticky for minutes once tripped — a single
    # attempt aborts the whole run (and the daily chain) for no reason. Retry with backoff ~6 min.
    sess = None
    for attempt in range(6):
        sess = start_session()
        if sess:
            break
        wait = 20 + attempt * 20
        print(f"  session attempt {attempt + 1}/6 blocked; retrying in {wait}s...")
        time.sleep(wait)
    if not sess:
        print("ABORT: could not establish an AcclaimWeb session (Cloudflare block / site down). Try again later.")
        # EXIT NON-ZERO (2026-08-26). This used to `return`, so the process exited 0 after six
        # backoff retries (20+40+60+80+100 = 300s) having traced NOTHING. A caller cannot tell that
        # apart from a clean run with no new leads, and one caller in particular has been fooled by
        # it for a long time: the GitHub Actions runner. CURL above pins System32's Schannel curl on
        # Windows and falls back to bare `curl` everywhere else, and this module's own note says a
        # non-Schannel fingerprint is exactly what Cloudflare blocks -- so on the Ubuntu runner this
        # almost certainly aborts every night, burns five minutes doing it, and reports success.
        # Measured corroboration: the cloud's [2b/5] step ran 362s on run #45, which is ~300s of
        # these retries plus MD's cached-only pass.
        # 2, not 1, to distinguish "no session" from an ordinary error.
        return 2
    print(f"session up (doctypes {sess['doctypes'].count(',')+1}, booktypes {sess['booktypes'].count(',')+1})")

    link_cache = {} if a.no_links else _load_links()
    done = hits = blocked = 0
    for r in picked:
        _t_case = time.time()
        case = r.get('case', ''); owner = r.get('owners', ''); judg = _num(r.get('judg'))
        docs = search_docs(sess, _search_name(r))
        if docs is None:
            blocked += 1
            print(f"  --  {case:18} {owner[:26]:26} (blocked / no data)")
            if blocked >= 5 and blocked == done + blocked:          # session died early -> re-establish once
                sess = start_session() or sess
            time.sleep(0.8); continue
        ftype = _fc_type(case)
        links, complete = (({}, False) if a.no_links
                           else link_chain(docs, owner, case, link_cache, run_budget=a.link_pages,
                                           run_seconds=a.link_seconds))
        res = analyze(docs, owner, judg, ftype=ftype, lead_case=case, links=links)
        res['traced'] = time.strftime('%Y-%m-%d'); res['owner'] = owner
        res['pv'] = PAIRING_VERSION if complete else 1      # an incomplete link read stays queued for relink
        out[case] = res
        done += 1
        flags = []
        if res['deeded']: flags.append(f"TAKEN->{res['deeded']['grantee']}")
        if res['second_fc']: flags.append(f"2ND-FC {res['second_fc']['case']}")
        if ftype == 'HOA' and res['surv_first']: flags.append(f"surv 1st ~${res['surv_first']:,}")
        elif res['open_count'] >= 2 and res['junior']: flags.append(f"2nd ~${res['junior']:,}")
        if flags: hits += 1
        flag = ('  <-- ' + ' | '.join(flags) + f" (conf {res['conf']})") if flags else ''
        nlink = sum(1 for l in res['liens'] if l.get('how') == 'link')
        print(f"  ok  {case:18} {owner[:26]:26} {ftype or '?':8} {res['nrec']:>3} recs  "
              f"linked {nlink}/{len(res['liens'])}{'' if (complete or a.no_links) else ' (partial)'} "
              f"{time.time() - _t_case:.0f}s{flag}")
        json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
        if not a.no_links:
            _save_links(link_cache)
        time.sleep(0.5)
    if _link_fails[0] >= LINK_TRIP:
        print(f"  NOTE: {LINK_TRIP} details pages in a row were unreadable (Cloudflare) - link pairing paused for "
              f"the rest of this run; those chains keep their queue place for the next one.")
    if not a.no_links and (_link_spent[0] >= a.link_pages or _link_clock[0] >= a.link_seconds):
        print(f"  NOTE: link budget spent ({_link_spent[0]} pages, {_link_clock[0]:.0f}s) - later chains this run "
              f"traced name-only and are queued for relink.")
    elif not a.no_links:
        print(f"  links: {_link_spent[0]} details page(s) read in {_link_clock[0]:.0f}s")
    print(f"\nDONE: {done} traced ({hits} confident surviving-2nd, {blocked} blocked). -> broward_liens.json")


if __name__ == '__main__':
    # main() returns 2 when no AcclaimWeb session could be established; propagate it so a
    # blocked run is visible to the caller instead of looking like a clean no-op.
    raise SystemExit(main() or 0)
