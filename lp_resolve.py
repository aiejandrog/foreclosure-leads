"""lp_resolve.py — turn a Miami-Dade lis pendens LEGAL DESCRIPTION into a real street address.

WHY THIS EXISTS
A lis pendens records a legal description ("AVENTURA ESTATES LOT 7 BLK 3 PB 142/390"), never a
folio and never a street address. skiptrace.py traces BY ADDRESS, so every one of the 125 LP
records in lis_pendens.json is unworkable: the board's EARLY outreach lane is starved, not broken.
This script closes that gap by matching the legal description against the county's own parcel roll
(MDC.PaGis, 943k rows, free + unauthenticated — the same service comps.py already uses).

THE SAFETY RULE THAT OUTRANKS COVERAGE
A wrong address sends someone to knock on an innocent stranger's door and puts foreclosure outreach
in front of a person who is not in foreclosure. A BLANK IS ALWAYS BETTER THAN A GUESS. So:
  - only "high" and "medium" rows carry a folio/address; "low" and "none" blank them and instead
    emit candidates[] for a human to pick from,
  - nothing resolves on a single signal that could be a coincidence,
  - a truncated result page is treated as UNRESOLVED, never as "the answer is in what came back".

THE LADDER (most precise first; stop at the first rung that lands on exactly one parcel)
  1 platbook   PB + LOT + BLK, all three in SQL. The plat citation is a composite parcel key and is
               immune to every spelling problem (PaGis abbreviates ADDITION->ADDN TO, SECTION->SEC,
               and outright misspells NELSEN->NELSON). Non-condo only.
  2 condo-or   Official-Records book of the condo declaration + UNIT. Condo only.
  3 name-full  Full subdivision name (verbatim, then abbreviation variants, then unordered) AND the
               LOT/BLK (or UNIT) geometry. Rescues leads whose plat page normalization is ambiguous.
  4 name-core  Generic plat words (ADDITION/SECTION/ordinals) dropped. RELAXED — information has
               been discarded, so this rung may only resolve when the plat book independently
               agrees, and never above "medium".
  5 owner      Tokenized owner name against TRUE_OWNER1/TRUE_OWNER2, then scored against the legal.
               Never resolves without independent legal corroboration (see OWNER_MIN_EVIDENCE).
  6 condo-weak First two words of the condo name + UNIT. ONE signal wearing two hats — it IS the
               legal, degraded. Diagnostic only: always "low", always blank, candidates for a human.

CONFIDENCE IS CROSS-CORROBORATION, NOT RUNG POSITION
  high   = exactly one parcel from a precise rung (1/2/3) AND a second independent signal agrees
           (owner name, or the plat book showing up in the parcel's own LEGAL)
  medium = exactly one parcel from a precise rung but the other signal is SILENT — including the
           post-filing-transfer case, where the parcel is right and the owner of record has changed
           (ownerMismatch=true; the LP defendant no longer owns it, so do not greet the record owner
           by name), or a relaxed rung that the plat book independently confirms
  low    = ambiguous (n>1), relaxed rung with nothing to confirm it, or signals that disagree
  none   = no candidate at all

Usage:
  python lp_resolve.py                 # resolve all 125, write lp_addresses.json
  python lp_resolve.py --limit 15      # first N records
  python lp_resolve.py --case 2026-014667-CA-01
  python lp_resolve.py --dry-run       # cache only, zero network calls; reports what is missing
  python lp_resolve.py --refresh       # ignore the cache and re-query

Every HTTP response is cached to _lp_pagis_cache.json keyed by the exact WHERE clause, so re-runs
are free and idempotent, and the `evidence` field of every emitted row contains the literal query
that produced the match — a human can paste it straight back at the endpoint to re-check us.
"""
import argparse, itertools, json, os, re, sys, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'lis_pendens.json')
OUT = os.path.join(HERE, 'lp_addresses.json')
CACHE = os.path.join(HERE, '_lp_pagis_cache.json')

LAYER = ('https://gisweb.miamidade.gov/arcgis/rest/services/MD_ComparableSales/'
         'MapServer/5/query')
UA = 'foreclosure-leads/1.0 (lp-address-resolver)'
FIELDS = ('FOLIO,TRUE_OWNER1,TRUE_OWNER2,TRUE_SITE_ADDR,TRUE_SITE_ADDR_NO_UNIT,'
          'TRUE_SITE_CITY,TRUE_SITE_ZIP_CODE,LEGAL,SUBDIVISION')
# The service truncates. Measured: `PB 174-097 AND LOT 1` returns 264 rows, so a 200-row page
# silently drops the correct parcel and whatever survives gets promoted to "exactly one" — a
# confident wrong answer. 1000 with a truncation guard, and every predicate in SQL (never a
# Python-side post-filter on a partial page).
CAP = 1000

_S = requests.Session(); _S.headers.update({'User-Agent': UA})
_cache, _net_calls, _dry = {}, [0], [False]


# ---- endpoint --------------------------------------------------------------------------------
def q(where):
    """Cached PaGis query. Returns (rows, truncated). A response without a 'features' key is an
    ERROR, not an empty result — returning [] there would read as 'no such parcel' and blank a
    lead that actually exists."""
    if where in _cache:
        rows = _cache[where]
        return rows, len(rows) >= CAP
    if _dry[0]:
        return None, False                       # dry-run: cache miss is reported, never fetched
    p = {'where': where, 'outFields': FIELDS, 'returnGeometry': 'false',
         'f': 'json', 'resultRecordCount': CAP}
    for i in range(3):
        try:
            _net_calls[0] += 1
            j = _S.get(LAYER, params=p, timeout=60).json()
            if 'features' in j:
                rows = [f['attributes'] for f in j['features']]
                _cache[where] = rows
                return rows, len(rows) >= CAP
            print('   ! api error:', str(j)[:160])
        except Exception as e:
            print('   ! http error:', str(e)[:120])
        time.sleep(1.5)
    return None, False                            # exhausted retries -> unknown, NOT empty


def esc(s):
    return str(s).replace("'", "''")


# ---- text normalization ----------------------------------------------------------------------
def norm(s):
    """Uppercase, strip punctuation INCLUDING the plat hyphen. Anything compared against a norm()'d
    string must therefore use the space form ('PB 142 39'), never the raw hyphen form."""
    s = (s or '').upper().replace('&W', ' ').replace('&H', ' ').replace('&', ' ')
    s = re.sub(r'[^A-Z0-9 ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def nb(s, tok):
    """Token present with no digit glued to either side. Without this, 'PB 107-4' matches
    'PB 107-41' and 'LOT 1' matches 'LOT 10' — both are real parcels a mile apart."""
    return re.search(r'(?<![0-9])' + re.escape(tok) + r'(?![0-9])', s) is not None


# Abbreviation families, measured live against the 943k-row LEGAL field: BOTH long and short forms
# occur in volume (ADDITION 2,226 vs ADDN 50,536; SECTION 7,957 vs SEC 118,478), so a name match
# has to try every member — you cannot simply substitute one for the other.
FAMILIES = [
    ['ADDN', 'ADDITION', 'ADD', 'ADDNS'], ['SEC', 'SECTION', 'SECT'], ['SUB', 'SUBDIVISION'],
    ['RESUB', 'RESUBDIVISION'], ['1ST', 'FIRST'], ['2ND', 'SECOND'], ['3RD', 'THIRD'],
    ['4TH', 'FOURTH'], ['5TH', 'FIFTH'], ['6TH', 'SIXTH'], ['7TH', 'SEVENTH'], ['8TH', 'EIGHTH'],
    ['9TH', 'NINTH'], ['10TH', 'TENTH'],
    ['ONE', '1'], ['TWO', '2'], ['THREE', '3'], ['FOUR', '4'], ['FIVE', '5'], ['SIX', '6'],
    ['GDNS', 'GARDENS', 'GDN'], ['ESTS', 'ESTATES', 'EST'], ['HTS', 'HEIGHTS'], ['MNR', 'MANOR'],
    ['TERR', 'TERRACE'], ['VLG', 'VILLAGE'], ['PK', 'PARK'], ['AMD', 'AMENDED'],
    ['REV', 'REVISED'], ['CORR', 'CORRECTED'], ['CONDO', 'CONDOMINIUM'], ['PL', 'PLAT', 'PLACE'],
    ['REPL', 'REPLAT'], ['TWNHS', 'TOWNHOMES'], ['BLVD', 'BOULEVARD'], ['PT', 'POINTE', 'POINT'],
    ['LK', 'LAKE'], ['LKS', 'LAKES'], ['IND', 'INDUSTRIAL'], ['AC', 'ACRES'], ['HWY', 'HIGHWAY'],
]
VAR = {}
for _fam in FAMILIES:
    for _t in _fam:
        VAR.setdefault(_t, set()).update(_fam)

# Generic plat vocabulary. Dropping these is what makes rung 4 work at all — and it is also exactly
# why rung 4 cannot resolve alone: with them gone, "PARK VIEW ISLAND" and "PARK VIEW TERRACE RESUB"
# both read as "PARK VIEW SUBDIVISION".
MODIFIER = {'ADDN', 'ADDITION', 'ADD', 'ADDNS', 'SEC', 'SECTION', 'SECT', 'SUB', 'SUBDIVISION',
            'RESUB', 'RESUBDIVISION', '1ST', 'FIRST', '2ND', 'SECOND', '3RD', 'THIRD', '4TH',
            'FOURTH', '5TH', 'FIFTH', '6TH', 'SIXTH', '7TH', 'SEVENTH', '8TH', 'EIGHTH', 'ONE',
            'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'AMD', 'AMENDED', 'REV', 'REVISED', 'CORR',
            'CORRECTED', 'PL', 'PLAT', 'REPL', 'REPLAT', 'THE', 'OF', 'TO', 'A', 'AN', 'AND',
            'NO', 'UNIT', 'INC', 'LLC', 'ASSN', 'ASSOCIATION', 'CORP', 'CO', 'LTD', 'PH',
            'PHASE', 'TR', 'TRACT'}
STOP = {'THE', 'OF', 'TO', 'A', 'AN', 'AND', 'INC', 'LLC', 'ASSN', 'ASSOCIATION', 'CORP', 'CO', 'LTD'}


def name_tokens(name):
    t = [x for x in norm(name).split() if x]
    while t and t[-1] in STOP:
        t.pop()
    while t and t[0] in STOP:
        t.pop(0)
    return t


def core_tokens(toks):
    return [t for t in toks if t not in MODIFIER]


# ---- LP legal parsing ------------------------------------------------------------------------
PB_RE = re.compile(r'\bPB\s+([0-9]+)\s*/\s*([0-9]+)')
LOT_RE = re.compile(r'\bLOTS?\s+([0-9]+(?:\s*(?:&|,|AND|THRU|TO)\s*[0-9]+)*)')
BLK_RE = re.compile(r'\bBLK\s+([0-9]+)')
UNIT_RE = re.compile(r'\bUNITS?\s*(?:NO\s+)?([0-9A-Z]+)(?:[- ]([0-9A-Z]{1,4}))?')
# Words that can legally follow a unit number and are NOT part of it. Without this, "UNIT 201 BLDG
# III" parses as unit "201 BLDG".
_UNIT_STOPWORD = {'BLDG', 'BLD', 'BUILDING', 'PHASE', 'PH', 'PB', 'UNDIV', 'INT', 'IN', 'COMMON',
                  'ELEMENTS', 'OF', 'THE', 'AND', 'TOGETHER', 'WITH', 'AS', 'DESC', 'SEE', 'DOC',
                  'PARCEL', 'PARCELS', 'TR', 'TRACT', 'LOT', 'LOTS', 'BLK', 'FL', 'FLOOR'}
LOTALL_RE = re.compile(r'\bLOTS?\s+([0-9]+(?:\s*(?:&|,|AND|THRU|TO)\s*[0-9]+)*)')
HEAD_RE = re.compile(r'\b(LOTS?|UNITS?|TRACTS?|BLDG|PARCELS?|PB)\b')


def _unit_forms(m):
    """Condo unit numbers as PaGis actually writes them. The LP renders PaGis's hyphen as a SPACE,
    so 'UNIT NO 8 11' is parcel 'UNIT 8-11', 'UNIT BL 26' is 'UNIT BL-26', 'UNIT NO 10 M' is '10M'.
    Verified live on all three. Most-specific form first; the bare first part stays last so a
    two-part unit can still fall back, and any resulting ambiguity is caught by the n>1 rule."""
    if not m:
        return []
    a, b = m.group(1), m.group(2)
    if not b or b in _UNIT_STOPWORD:
        return [a]
    return ['%s-%s' % (a, b), '%s%s' % (a, b), a]


def parse_lp(legal):
    L = (legal or '').upper()
    pb, lot, blk, unit = PB_RE.search(L), LOT_RE.search(L), BLK_RE.search(L), UNIT_RE.search(L)
    h = HEAD_RE.search(L)
    p = {'book': int(pb.group(1)) if pb else None,
         'page': pb.group(2) if pb else None,
         'lots': re.findall(r'[0-9]+', lot.group(1)) if lot else [],
         'blk': blk.group(1) if blk else None,
         'unit': unit.group(1) if unit else None,
         'units': _unit_forms(unit),
         'sub': (L[:h.start()] if h else L).strip(),
         'placeholder': L.startswith('SEE DOC') or not pb or pb.group(1) == '0'}
    # A book >= 1000 is not a plat book at all — it is an Official Records book, i.e. the recorded
    # condo declaration. PaGis parcel legals carry plat books, so the strongest signal is simply
    # unavailable for these and the whole record has to go down the condo branch.
    p['condo'] = bool((p['book'] or 0) >= 1000 or (p['unit'] and not p['lots']))
    return p


def pb_variants(book, page):
    """LP page carries one extra trailing digit vs PaGis, which also uses a hyphen and sometimes
    zero-pads: 142/390 -> 'PB 142-39' | 50/190 -> 'PB 50-19' | 174/970 -> 'PB 174-097'.
    103 of the 123 usable LP pages end in 0, so the /10 rule is the norm; the raw page is kept as
    a last variant for the rest."""
    if not book or book >= 1000:
        return []
    s = str(int(page))
    base = s[:-1] if (s.endswith('0') and len(s) > 1) else s
    out = []
    for v in (base.lstrip('0') or '0', base.zfill(2), base.zfill(3), s):
        c = 'PB %d-%s' % (book, v)
        if c not in out:
            out.append(c)
    return out


def or_variants(book, page):
    """Condo declaration book/page as PaGis writes it inside a LEGAL: '22644-0643' or '22644-643'."""
    if not book or book < 1000:
        return []
    out = []
    for v in (str(int(page)).zfill(4), str(int(page)), str(page)):
        c = '%d-%s' % (book, v)
        if c not in out:
            out.append(c)
    return out


# ---- parcel-side adjudication ----------------------------------------------------------------
def legal_lots(L):
    s = set()
    for m in LOTALL_RE.finditer(L):
        s.update(re.findall(r'[0-9]+', m.group(1)))
    return s


def legal_units(L):
    """PaGis labels a condo unit UNIT on most declarations and APT on others (SKY LAKE GARDENS NO 3
    CONDO writes 'APT 115'), so both have to count or those buildings look like they don't exist."""
    return set(re.findall(r'\b(?:UNIT|APT)\s+([0-9A-Z\-]+)', L))


def geom_ok(p, L):
    """Exact re-check of the geometry the SQL LIKE only coarsely prefiltered."""
    if p['condo'] or (p['unit'] and not p['lots']):
        return bool(set(p['units']) & legal_units(L))
    if p['lots'] and not all(x in legal_lots(L) for x in p['lots']):
        return False
    # nb() deliberately accepts "BLK 2-S" for an LP "BLK 2" — PaGis splits some blocks N/S and the
    # LP never carries the suffix. Flagged in the evidence when it fires (see _blk_suffix).
    if p['blk'] and not nb(L, 'BLK ' + p['blk']):
        return False
    return True


def name_ok(L, toks):
    """Scoped name check: verify ONLY the tokens the rung actually queried. Full-name verification
    rejects correct parcels — LP 'HIALEAH STUDIO ADDITION TO THE TOWN OF SECOND' is PaGis
    'HIALEAH 2ND STUDIO ADD', which has no TOWN in it at all."""
    Lz = ' ' + re.sub(r'\s+', ' ', re.sub(r'[^A-Z0-9 ]', ' ', L)) + ' '
    for t in toks:
        if not any((' ' + f + ' ') in Lz for f in VAR.get(t, {t})):
            return False
    return True


def pb_ok(p, L):
    """Does the parcel's own LEGAL carry the LP plat book? Independent of every spelling issue."""
    return any(nb(L, v) for v in pb_variants(p['book'], p['page'])) or \
           any(v in L for v in or_variants(p['book'], p['page']))


def _blk_suffix(p, L):
    if not p['blk']:
        return False
    m = re.search(r'\bBLK\s+' + re.escape(p['blk']) + r'-([A-Z0-9]+)', L)
    return bool(m)


# ---- owner-side adjudication -----------------------------------------------------------------
# 23 of 125 LP "owner" fields are companies, and they are overwhelmingly the PLAINTIFF or the HOA
# mis-captured into the owner column: ROCKET MORTGAGE LLC appears as owner on 8 different legals,
# CITIBANK N A on 5. Querying those by name returns whatever the lender happens to own and is a
# guaranteed wrong-door generator, so the owner SIGNAL is suppressed for them entirely (not scored
# as a miss — suppressed, so a company-owned condo still resolves on its legal alone).
COMPANY_TOK = {'INC', 'LLC', 'CORP', 'CORPORATION', 'CO', 'ASSN', 'ASSOC', 'ASSOCIATION',
               'CONDOMINIUM', 'CONDO', 'TRUST', 'LP', 'LLP', 'LTD', 'BANK', 'MORTGAGE', 'NA',
               'COMPANY', 'PARTNERSHIP', 'HOLDINGS', 'GROUP', 'SERVICES', 'FUND', 'PA', 'PLLC',
               'INVESTMENTS', 'CAPITAL', 'PROPERTIES', 'REALTY', 'HOMES', 'USA', 'FINANCIAL',
               'LENDING', 'FC'}
PERSON_SUFFIX = {'JR', 'SR', 'II', 'III', 'IV', 'V', 'TRS', 'TRUSTEE', 'TRUSTEES', 'TR', 'EST',
                 'ESTATE', 'ESTATES', 'OF', 'ETAL', 'ET', 'AL', 'LE', 'REM', 'LIFE', 'REMAINDER',
                 'DECD', 'DECEASED', 'W', 'H'}


def is_company(owner):
    return bool(set(norm(owner).split()) & COMPANY_TOK)


def owner_tokens(owner):
    """Significant tokens of a LAST-FIRST-MIDDLE name. Middle initials are dropped: PaGis stores
    the full middle name, so 'RIVERA LUIS M' must become [RIVERA, LUIS] — LIKE '%M%' is worse than
    useless. Token-ANDing is order-free, which is what makes LP LAST-FIRST match PaGis FIRST-LAST."""
    out, seen = [], set()
    for t in norm(owner).split():
        if t in PERSON_SUFFIX or t in STOP or len(t) < 3 or t.isdigit() or t in seen:
            continue
        seen.add(t); out.append(t)
    return out


def owner_agrees(row, lp_owner):
    """(hits, total_tokens, matched_field). SQL LIKE is substring-only, so this word-boundary
    re-check is mandatory — it is what stops GIL matching GILBERT."""
    toks = owner_tokens(lp_owner)
    if not toks or is_company(lp_owner):
        return 0, 0, ''
    for fld in ('TRUE_OWNER1', 'TRUE_OWNER2'):
        w = set(norm(row.get(fld)).split())
        if w and all(t in w for t in toks):
            return len(toks), len(toks), fld
    blob = set(norm((row.get('TRUE_OWNER1') or '') + ' ' + (row.get('TRUE_OWNER2') or '')).split())
    return sum(1 for t in toks if t in blob), len(toks), ''


def owner_strong(row, lp_owner):
    """Two-token agreement (or full agreement on a one-token name) = the owner signal fires."""
    h, t, _ = owner_agrees(row, lp_owner)
    return t > 0 and (h >= 2 or (h == t and h >= 1 and t == 1))


# ---- SQL builders ----------------------------------------------------------------------------
def lot_clause(p):
    """'%LOT%29%' not '%LOT 29%'. PaGis writes multi-lot parcels as "LOTS 29 & 30" — the literal
    'LOT 29' never occurs there, and the narrower form silently returns zero rows and reads exactly
    like "this lead has no parcel". Two of fifteen holdout leads were invisibly lost this way."""
    return '(' + ' OR '.join("LEGAL LIKE '%%LOT%%%s%%'" % esc(l) for l in p['lots']) + ')'


def unit_clause(p):
    """Every unit spelling x both keywords, OR-ed. The post-filter is exact, so breadth here is free."""
    return '(' + ' OR '.join("LEGAL LIKE '%%%s %s%%'" % (kw, esc(u))
                             for u in p['units'] for kw in ('UNIT', 'APT')) + ')'


def geom_clause(p):
    if p['condo'] or (p['unit'] and not p['lots']):
        return unit_clause(p) if p['units'] else None
    if p['lots'] and p['blk']:
        return lot_clause(p) + " AND LEGAL LIKE '%%BLK %s%%'" % esc(p['blk'])
    if p['lots']:
        return lot_clause(p)
    return None


def tok_clause(t):
    """The name may start the LEGAL or follow a 'SS TT RR' section-township-range prefix — never
    anchor it at position 0."""
    e = esc(t)
    return "(LEGAL LIKE '%% %s %%' OR LEGAL LIKE '%s %%')" % (e, e)


# ---- the ladder ------------------------------------------------------------------------------
def _run(where, p, tokens_to_verify=None, pb_variant=None):
    """Query + strict client-side adjudication. Returns (kept_rows, raw_count, truncated)."""
    rows, trunc = q(where)
    if rows is None:
        return None, 0, False                      # error or dry-run miss — unknown, not empty
    if trunc:
        return [], len(rows), True                 # partial page: refuse to pick from it
    keep = []
    for r in rows:
        L = (r.get('LEGAL') or '').upper()
        if pb_variant and not nb(L, pb_variant):
            continue
        if not geom_ok(p, L):
            continue
        if tokens_to_verify and not name_ok(L, tokens_to_verify):
            continue
        keep.append(r)
    return keep, len(rows), False


def rung_platbook(p, rec):
    """PB + LOT + BLK, every predicate in SQL. BLK is heavily load-bearing — measured candidate
    reductions after adding it: 26->1, 17->1, 6->1, 5->1, 3->1."""
    if p['condo'] or not p['lots']:
        return None
    for v in pb_variants(p['book'], p['page']):
        w = "LEGAL LIKE '%%%s%%' AND " % esc(v) + lot_clause(p)
        if p['blk']:
            w += " AND LEGAL LIKE '%%BLK %s%%'" % esc(p['blk'])
        keep, raw, trunc = _run(w, p, pb_variant=v)
        if keep is None or trunc:
            continue
        if keep:
            return {'rung': 'platbook', 'where': w, 'rows': keep, 'raw': raw}
    return None


def rung_condo_or(p, rec):
    """Official-Records book of the declaration + UNIT."""
    if not p['condo'] or not p['units']:
        return None
    for v in or_variants(p['book'], p['page']):
        w = "LEGAL LIKE '%%%s%%' AND " % esc(v) + unit_clause(p)
        keep, raw, trunc = _run(w, p)
        if keep is None or trunc:
            continue
        if keep:
            return {'rung': 'condo-or', 'where': w, 'rows': keep, 'raw': raw}
    return None


def rung_name(p, rec, relaxed):
    """Subdivision name AND the geometry. relaxed=False walks verbatim -> variant-ordered ->
    unordered on the FULL name; relaxed=True drops generic plat words (see MODIFIER)."""
    g = geom_clause(p)
    toks = name_tokens(p['sub'])
    if not g or not toks:
        return None
    cr = core_tokens(toks)
    tries = []
    if not relaxed:
        tries.append(("LEGAL LIKE '%" + esc(' '.join(toks)) + "%'", toks))
        choices = [sorted(VAR.get(t, {t})) for t in toks]
        for c in itertools.islice(itertools.product(*choices), 8):
            tries.append(("LEGAL LIKE '%" + '%'.join(esc(x) for x in c) + "%'", toks))
        if len(toks) <= 7:
            tries.append((' AND '.join(tok_clause(t) for t in toks), toks))
    else:
        if cr:
            tries.append((' AND '.join(tok_clause(t) for t in cr), cr))
        if len(cr) >= 2:
            tries.append((' AND '.join(tok_clause(t) for t in cr[:2]), cr[:2]))
    pool, used, rawn = {}, None, 0
    for clause, verify in tries:
        w = clause + ' AND ' + g
        keep, raw, trunc = _run(w, p, tokens_to_verify=verify)
        if keep is None or trunc:
            continue
        rawn += raw
        if keep:
            for r in keep:
                pool[r['FOLIO']] = r
            used = w
            break
    if not pool:
        return None
    return {'rung': 'name-core' if relaxed else 'name-full', 'where': used,
            'rows': list(pool.values()), 'raw': rawn}


# The owner path may never resolve on a bare LOT or BLK number: that is a 1-in-a-few-hundred
# coincidence, not evidence. It needs the plat book, the unit, or >=2 subdivision words — the
# signals that actually identify a PROPERTY rather than a position within some plat.
def legal_score(p, L):
    RL = norm(L)
    score, ev = 0, []
    for v in pb_variants(p['book'], p['page']) + or_variants(p['book'], p['page']):
        if norm(v) in RL:                          # norm() ate the hyphen on BOTH sides
            score += 3; ev.append('PB=' + v); break
    sw = [w for w in norm(p['sub']).split() if w not in STOP and len(w) > 2]
    hit = [w for w in sw if w in RL.split()]
    if sw:
        frac = len(hit) / float(len(sw))
        if frac >= 0.6 and len(hit) >= 2:
            score += 2; ev.append('SUB %d/%d' % (len(hit), len(sw)))
        elif len(hit) >= 1 and frac >= 0.5:
            score += 1; ev.append('SUBpartial %d/%d' % (len(hit), len(sw)))
    if p['lots'] and legal_lots(L) & set(p['lots']):
        score += 1; ev.append('LOT')
    if p['blk'] and nb(L.upper(), 'BLK ' + p['blk']):
        score += 1; ev.append('BLK')
    if p['units'] and (set(p['units']) & legal_units(L.upper())):
        score += 2; ev.append('UNIT')
    strong = any(e.startswith('PB=') or e == 'UNIT' or e.startswith('SUB ') for e in ev)
    return score, ev, strong


def rung_owner(p, rec):
    """Owner tokens AND-ed inside ONE field. TRUE_OWNER2 is not optional — 3 of 11 holdout owner
    resolutions matched only there (OWNER1 was the spouse). Company owners are refused outright."""
    o = rec.get('owner') or ''
    if is_company(o):
        return None
    toks = owner_tokens(o)
    if len(toks) < 2:
        return None                                # single-token names are unqueryable
    a = ' AND '.join("TRUE_OWNER1 LIKE '%%%s%%'" % esc(t) for t in toks)
    b = ' AND '.join("TRUE_OWNER2 LIKE '%%%s%%'" % esc(t) for t in toks)
    w = '(%s) OR (%s)' % (a, b)
    rows, trunc = q(w)
    if rows is None or trunc:
        return None
    scored = []
    for r in rows:
        h, t, fld = owner_agrees(r, o)
        if not fld:                                # must sit wholly in one field
            continue
        s, ev, strong = legal_score(p, (r.get('LEGAL') or '').upper())
        if not strong:
            continue                               # LOT-only / BLK-only is not corroboration
        scored.append((s, ev, r))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0]
    ties = [x for x in scored if x[0] == top]
    # Refuse when the top two are within 2 points — with 107 person-name leads, two same-named
    # people owning in the same subdivision is a matter of time, and the margin is the only guard.
    if len(ties) > 1 or (len(scored) > 1 and top - scored[len(ties)][0] < 2):
        return {'rung': 'owner', 'where': w, 'rows': [x[2] for x in ties] or [scored[0][2]],
                'raw': len(rows), 'score': top, 'sev': scored[0][1]}
    return {'rung': 'owner', 'where': w, 'rows': [scored[0][2]], 'raw': len(rows),
            'score': top, 'sev': scored[0][1]}


def rung_condo_weak(p, rec):
    """First two words of the condo name + UNIT. NOT an independent signal — it is the legal,
    degraded. 'CRICKET CLUB' prefixes both CRICKET CLUB CONDO and CRICKET CLUBHOUSE CONDO. Always
    low, always blanked; it exists only to put candidates in front of a human."""
    if not p['condo'] or not p['units']:
        return None
    sub2 = ' '.join(norm(p['sub']).split()[:2])
    if not sub2:
        return None
    w = "LEGAL LIKE '%%%s%%' AND " % esc(sub2) + unit_clause(p)
    keep, raw, trunc = _run(w, p)
    if not keep or trunc:
        return None
    return {'rung': 'condo-weak', 'where': w, 'rows': keep, 'raw': raw}


LADDER = [rung_platbook, rung_condo_or,
          lambda p, r: rung_name(p, r, False),
          lambda p, r: rung_name(p, r, True),
          rung_owner, rung_condo_weak]


def resolve(rec):
    p = parse_lp(rec.get('legal') or '')
    row = {'case': rec.get('case', ''), 'folio': '', 'addr': '', 'city': '', 'zip': '',
           'paOwner': '', 'confidence': 'none', 'rung': '', 'evidence': '',
           'ownerMismatch': False, 'needsHuman': True, 'candidates': [],
           'lpOwner': rec.get('owner', ''), 'legal': rec.get('legal', '')}
    if p['placeholder']:
        row['evidence'] = 'LP record carries no usable legal description ("%s") — nothing to match' \
                          % (rec.get('legal') or '')[:60]
        return row

    best = None
    for fn in LADDER:
        got = fn(p, rec)
        if not got:
            continue
        rows = got['rows']
        if len(rows) == 1:
            best = got
            break
        # Ambiguous. An independent owner-name hit can break the tie — the LP defendant owning
        # exactly one of the N parcels that satisfy the same legal key is a genuine second signal.
        # Capped at "medium" and flagged, because it is one coincidence away from a wrong door.
        picks = [r for r in rows if owner_strong(r, rec.get('owner', ''))]
        if len(picks) == 1:
            best = dict(got, rows=picks, tiebreak=len(rows))
            break
        if best is None or len(rows) < len(best['rows']):
            best = got                             # keep the tightest ambiguous set for a human
    if best is None:
        row['evidence'] = 'no parcel matched any rung (plat book, condo book, subdivision name, owner)'
        return row

    rows, rung = best['rows'], best['rung']
    row['rung'] = rung
    cands = [{'folio': r.get('FOLIO'), 'addr': (r.get('TRUE_SITE_ADDR') or '').strip(),
              'city': (r.get('TRUE_SITE_CITY') or '').strip(),
              'owner': (r.get('TRUE_OWNER1') or '').strip(),
              'legal': (r.get('LEGAL') or '').strip()} for r in rows[:8]]

    if len(rows) != 1:
        row['confidence'] = 'low'
        row['candidates'] = cands
        row['evidence'] = ('%d parcels share this legal key and no independent signal separates '
                           'them — a human must pick. query: %s' % (len(rows), best['where']))
        return row

    r = rows[0]
    L = (r.get('LEGAL') or '').upper()
    h, t, fld = owner_agrees(r, rec.get('owner', ''))
    o_strong = owner_strong(r, rec.get('owner', ''))
    o_suppressed = is_company(rec.get('owner', '')) or t == 0
    pb = pb_ok(p, L)
    sig = []
    if rung in ('platbook', 'condo-or'):
        sig.append('legal key (%s)' % rung)
    elif pb:
        sig.append('plat book in parcel legal')
    if rung in ('name-full', 'name-core'):
        sig.append('subdivision name')
    if o_strong:
        sig.append('owner name %d/%d on %s' % (h, t, fld or 'either field'))

    tie = best.get('tiebreak')
    if tie:
        conf = 'medium'
        why = ('%d parcels matched the %s key; the LP defendant owns exactly one of them '
               '(owner %d/%d tokens) — tie broken by an independent signal, verify before knocking'
               % (tie, rung, h, t))
    elif rung == 'condo-weak':
        conf = 'low'
        why = ('condo matched only on a 2-word name prefix + unit number — ONE signal, and the LP '
               '"owner" on HOA cases is the association, so no owner corroboration is possible')
    elif rung == 'name-core':
        # Generic plat words were discarded to get here, so the plat book has to vouch for it.
        if pb:
            conf = 'high' if o_strong else 'medium'
            why = 'relaxed subdivision name, but the parcel legal carries the LP plat book'
        elif o_strong:
            # Two independent signals (degraded name + owner of record IS the LP defendant), but
            # the name signal had information removed to get here, so it stops at medium.
            conf = 'medium'
            why = ('relaxed subdivision name with no plat book available (condo declarations are '
                   'often absent from PaGis), but the owner of record is the LP defendant')
        else:
            conf = 'low'
            why = ('resolved only by a relaxed subdivision-name match with no plat-book agreement '
                   '— information was discarded to get here, so this is a hint, not an address')
    elif rung == 'owner':
        conf = 'high' if best.get('score', 0) >= 3 else 'medium'
        why = 'owner name matched a single parcel, corroborated by the legal [%s]' % \
              ', '.join(best.get('sev') or [])
    elif o_strong:
        conf = 'high'
        why = 'exactly one parcel on the %s key AND the owner of record is the LP defendant' % rung
    elif o_suppressed:
        conf = 'medium'
        why = ('exactly one parcel on the %s key; the owner signal is unavailable (the LP "owner" '
               'is a company/HOA, i.e. usually the plaintiff) so this rests on the legal alone' % rung)
    else:
        conf = 'medium'
        why = ('exactly one parcel on the %s key, but the owner of record is NOT the LP defendant '
               '— post-filing transfer / trust / LLC / REO. The parcel is right; the person is not.'
               % rung)

    row['confidence'] = conf
    row['rung'] = rung
    row['paOwner'] = '; '.join(x for x in [(r.get('TRUE_OWNER1') or '').strip(),
                                           (r.get('TRUE_OWNER2') or '').strip()] if x)
    row['ownerMismatch'] = bool(not o_strong and not o_suppressed)
    row['needsHuman'] = conf != 'high'
    ev = [why, 'signals: ' + (', '.join(sig) or 'one only'), 'query: ' + best['where']]
    if _blk_suffix(p, L):
        ev.append('NOTE: LP says BLK %s, parcel legal says BLK %s-<suffix> — this plat splits the '
                  'block; confirm the half.' % (p['blk'], p['blk']))
    row['evidence'] = ' | '.join(ev)
    if conf in ('high', 'medium'):
        row['folio'] = r.get('FOLIO') or ''
        row['addr'] = (r.get('TRUE_SITE_ADDR') or '').strip()
        row['city'] = (r.get('TRUE_SITE_CITY') or '').strip()
        row['zip'] = str(r.get('TRUE_SITE_ZIP_CODE') or '').strip()
    else:
        row['candidates'] = cands
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--case', default='')
    ap.add_argument('--dry-run', action='store_true', help='cache only, zero network calls')
    ap.add_argument('--refresh', action='store_true', help='ignore the cache and re-query')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    global _cache
    if os.path.exists(CACHE) and not args.refresh:
        try: _cache = json.load(open(CACHE, encoding='utf-8'))
        except Exception: _cache = {}
    _dry[0] = args.dry_run

    recs = json.load(open(SRC, encoding='utf-8'))
    if args.case:
        recs = [r for r in recs if r.get('case') == args.case]
        if not recs:
            sys.exit('no LP record with case %s' % args.case)
    if args.limit:
        recs = recs[:args.limit]

    out = {}
    for i, rec in enumerate(recs, 1):
        try:
            row = resolve(rec)
        except Exception as e:
            print('  %s: ERROR %s' % (rec.get('case'), str(e)[:140]))
            continue
        out[row['case']] = row
        if not args.quiet:
            print('[%3d/%d] %-20s %-9s %-10s %-26s %s'
                  % (i, len(recs), row['case'], row['confidence'], row['rung'] or '-',
                     (row['addr'] + ' ' + row['city'])[:26] or '(blank)',
                     'MISMATCH' if row['ownerMismatch'] else ''))
        if i % 10 == 0 and not args.dry_run:
            json.dump(_cache, open(CACHE, 'w', encoding='utf-8'))   # a timeout can't lose the run

    if not args.dry_run:
        json.dump(_cache, open(CACHE, 'w', encoding='utf-8'))
        # MERGE, never replace: a --case / --limit run must not blow away the other 124 rows.
        prev = {}
        if os.path.exists(OUT):
            try: prev = json.load(open(OUT, encoding='utf-8'))
            except Exception: prev = {}
        prev.update(out)
        json.dump(prev, open(OUT, 'w', encoding='utf-8'), indent=1)

    n = len(out)
    c = lambda k: sum(1 for r in out.values() if r['confidence'] == k)
    print('\n%d records | high %d  medium %d  low %d  none %d | %d HTTP calls'
          % (n, c('high'), c('medium'), c('low'), c('none'), _net_calls[0]))
    print('with an address: %d   owner-mismatch flagged: %d   needs a human: %d'
          % (sum(1 for r in out.values() if r['addr']),
             sum(1 for r in out.values() if r['ownerMismatch']),
             sum(1 for r in out.values() if r['needsHuman'])))
    by_rung = {}
    for r in out.values():
        by_rung[r['rung'] or '(none)'] = by_rung.get(r['rung'] or '(none)', 0) + 1
    print('by rung:', ', '.join('%s=%d' % kv for kv in sorted(by_rung.items())))
    if args.dry_run:
        print('(dry run — cache only, nothing written)')


if __name__ == '__main__':
    main()
