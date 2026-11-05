"""deep_equity.py -- per-lead debt and equity with every lien summed, and a status per source.

WHY THIS EXISTS (2026-11-05, Alex, the CACE-26 "HUD partial claims" lead)
The 5:30 board showed $65k-$112k equity on a Broward house whose real equity was about -$23k to
+$24k. The numbers on the board were the county value and an online estimate minus the principal
the bank claims in its complaint. Three FHA partial-claim mortgages to the Secretary of Housing
and Urban Development, $88,109 together, were never subtracted. Four things let that happen:

  1. a parcel search of Broward Official Records returns deeds only (mortgages and liens carry no
     parcel id in the index), so anything parcel-keyed cannot see them;
  2. "the mortgage" was treated as one number, while the debt was one first plus three juniors;
  3. broward_liens.analyze() sets conf='low' when a parcel has more than 3 open mortgages, which
     is exactly the shape of an FHA loan modified three times (one partial claim per modification),
     and a low chain is a CEILING, never subtracted as fact;
  4. a source that returned nothing still left a clean-looking equity number on the row.

WHAT THIS DOES
For one lead (Tier 3, before a call) or a short list (Tier 2, nightly), it reads five sources and
gives each a status, OK / BLOCKED / EMPTY / ERROR:

  appraiser  county property appraiser (pa_values.lookup): market, assessed, owner, homestead
  case       the foreclosure case: number, plaintiff, filed, status, principal claimed, sale date
  records    Broward Official Records, NAME search for the owner AND every co-borrower, through
             the same AcclaimWeb session broward_liens.py uses
  tax        the tax collector read county_taxes.py keeps (Tier 3 re-reads it fresh)
  avm        an online estimate: the Redfin Estimate redfin_value.py caches, or a Zestimate typed
             in by hand. Never scraped from Zillow (its terms forbid it).

Every instrument found under those names is classified: mortgage, FHA partial claim (a mortgage to
HUD), modification, assignment, HOA / code / tax / IRS / judgment lien, lis pendens, release.
Releases are paired to what they release. Rows are kept to the subject property by the legal
description of the deeds that carry its parcel id; a row recorded against another property is
listed and not counted; an open row with no legal description at all is an UNKNOWN.

    debt_total = first mortgage (the principal the case claims, else the last modified balance,
                 else the recorded original) + every open subordinate mortgage + every open lien
                 + delinquent taxes from the collector
    equity_low  = county market value - debt_total
    equity_high = online estimate     - debt_total

THE RULES THAT MAKE IT SAFE
  * If case, records or tax is not OK the lead is UNVERIFIED and BOTH equity fields are blank.
  * If any debt component is unknown (an open instrument with no amount, a search cut short, an
    open row with no legal description, a namesake risk), equity is blank, never a number.
  * No stealth, no human-check solving, no paid records. A source that shows a "verify you are
    human" page is BLOCKED; three blocks in a row stop that source for the rest of the run.
  * About one request per 2 s per site with random jitter.
  * Output is an append-only log. A run that is not VERIFIED never replaces a VERIFIED row in the
    latest view; it is kept beside it as `last_attempt`.
  * debt_total is RECORDED / CLAIMED debt. Interest, escrow advances and fees since the complaint
    are not in it, so a real payoff is higher and equity is a ceiling. The row says so.

THE CLERK CASE SEARCH IS NOT AUTOMATED. browardclerk.org Web2 case search sits behind a Cloudflare
Turnstile human check (broward_plaintiff.py, 2026-08-15; re-probed 2026-10-16). This module does
not solve or evade it. Without a case-facts entry the case source is BLOCKED and the lead is
UNVERIFIED -- which is the honest answer. Tier 3 fills it: read the docket in a browser and save
the facts with

    python deep_equity.py --case CACE-26-000000 --set-case plaintiff="..." filed=2026-07-22 \
        status=active principal=267151.56 sale=none

Those entries are stamped with the day they were typed and go stale after CASE_MANUAL_TTL_DAYS.

Files (all under DEALFLOW_DIR, never the repo; they carry owner names):
    deep_equity/runs.jsonl          append-only, one JSON row per lead per run
    deep_equity/latest.json         case -> {verified, last_attempt}
    deep_equity/latest.csv          the spec's column list, one row per lead (latest view)
    deep_equity/run-<stamp>.log     every source failure with a timestamp
    deep_equity/manual/<case>.json  hand-entered case facts / co-borrowers / Zestimate
    deep_equity/raw/<case>/...      the raw responses this run read (--record), for --replay

Usage:
    python deep_equity.py --case CACE-26-000000            # Tier 3: fresh, everything, one lead
    python deep_equity.py --case CACE-26-000000 --record   # ...and keep the raw responses
    python deep_equity.py --replay CACE-26-000000          # re-parse the recorded responses only
    python deep_equity.py --tier 2 --limit 25              # nightly shortlist
    python deep_equity.py --tier 1                         # census, no network beyond caches
    python deep_equity.py --accept fixture.json            # acceptance: parse a fixture, compare
"""
import argparse
import csv
import datetime
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# where the gitignored lead files and caches live: the main checkout. A test worktree points this at it.
DATA = os.environ.get('DEEP_EQUITY_DATA_DIR') or HERE

OK, BLOCKED, EMPTY, ERROR = 'OK', 'BLOCKED', 'EMPTY', 'ERROR'
SOURCES = ('appraiser', 'case', 'records', 'tax', 'avm')
REQUIRED = ('case', 'records', 'tax')        # spec item 6: any of these not OK -> UNVERIFIED
VERIFIED, UNVERIFIED = 'VERIFIED', 'UNVERIFIED'

CASE_MANUAL_TTL_DAYS = 14
TAX_MAX_AGE_DAYS = 30
TIER2_DAYS = 30
TIER2_RECHECK_DAYS = 3
MIN_GAP_S = 2.0
JITTER_S = 1.5
BLOCK_TRIP = 3
DETAILS_PER_LEAD = 15         # details pages (release links) per lead; ~2-3.5 s each
TIER2_MAX_RUNTIME_S = 900     # the 5:30 run is not held up longer than other optional steps
NAMESAKE_RECORDS = 35          # same guard as broward_liens.analyze: past this, a name blends people

PAYOFF_NOTE = ('recorded and claimed debt only: interest, escrow advances and fees since the '
               'complaint are not included, so the real payoff is higher and equity is a ceiling')

HUD_RE = re.compile(r'SECRETARY\s+OF\s+(THE\s+)?(DEPT\.?\s+OF\s+|DEPARTMENT\s+OF\s+)?HOUSING|'
                    r'HOUSING\s+(AND|&)\s+URBAN\s+DEV|\bHUD\b', re.I)
REL_RE = re.compile(r'SATISF|RELEASE|REVOK|TERMINAT|DISCHARG|CANCEL', re.I)
PARTIAL_RE = re.compile(r'PARTIAL', re.I)
MOD_RE = re.compile(r'MODIF', re.I)
ASSIGN_RE = re.compile(r'ASSIGN', re.I)
LP_RE = re.compile(r'LIS\s*PEND', re.I)
DEED_RE = re.compile(r'^DEED|WARRANTY|QUIT\s*CLAIM|CERTIFICATE\s+OF\s+TITLE', re.I)
LIEN_RE = re.compile(r'^(LIEN|CLAIM\s+OF\s+LIEN|JUDGMENT|NOTICE\s+OF\s+LIEN|CERT|TAX)|LIEN', re.I)
IRS_RE = re.compile(r'INTERNAL\s+REV|UNITED\s+STATES|\bIRS\b', re.I)
TAX_RE = re.compile(r'TAX\s+COLLECTOR|DEPARTMENT\s+OF\s+REVENUE|\bTAX\b', re.I)
CODE_RE = re.compile(r'\bCITY\s+OF\b|\bTOWN\s+OF\b|\bVILLAGE\s+OF\b|\bCOUNTY\b|CODE\s+ENF|MUNICIPAL|'
                     r'STATE\s+OF\s+FLORIDA|\bPACE\b|CLEAN\s+ENERGY', re.I)
HOA_RE = re.compile(r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|\bMASTER\b|COMMUNITY|PROPERTY\s+OWNERS?|'
                    r'TOWNHO|MAINTENANCE|(?<!NATIONAL\s)\bASS(N|OC(IATION)?)\b|\bPOA\b|\bHOA\b', re.I)
CHALLENGE_RE = re.compile(r'Just a moment|cf-chl|challenges\.cloudflare\.com|turnstile|'
                          r'verify you are human|Enable JavaScript and cookies', re.I)


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def _now():
    return datetime.datetime.now().replace(microsecond=0)


def _iso(dt=None):
    return (dt or _now()).isoformat()


def _num(x):
    try:
        if isinstance(x, str):
            x = re.sub(r'[^\d.\-]', '', x)
        f = float(x or 0)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f else 0.0


def _money(x):
    return None if x is None else round(float(x), 2)


def _date(s):
    """'YYYY-MM-DD' from a /Date(ms)/ grid value, an ISO string, or M/D/YYYY. '' when unreadable."""
    s = str(s or '').strip()
    m = re.search(r'/Date\((-?\d+)', s)
    if m:
        return (datetime.datetime(1970, 1, 1)
                + datetime.timedelta(milliseconds=int(m.group(1)))).strftime('%Y-%m-%d')
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.datetime.strptime(s[:10], fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ''


def _digits(s):
    return re.sub(r'\D', '', str(s or ''))


def _inst_name(s):
    """Holder name normalized for release pairing (same idea as broward_liens._inst)."""
    s = (s or '').upper()
    s = re.sub(r'\b(NA|N A|NATIONAL ASSN|NATIONAL ASSOCIATION|FSB|FA|INC|CORP|CO|LLC|LP|USA|THE|'
               r'TRUST COMPANY|MTGE|MORTGAGE|GROUP|GRP|SVGS|SAVINGS|HOME LOANS?|FINANCIAL|'
               r'SERVICES?|BANK|OF)\b', '', s)
    return re.sub(r'[^A-Z]', '', s)


def name_key(name):
    """('LAST', 'FIRST') from 'LAST, FIRST MIDDLE' or 'LAST FIRST MIDDLE' (index order)."""
    s = re.sub(r'\s*&\s*[WH]\b.*$', '', (name or '').upper())
    s = re.sub(r'\bH/[EW]\b|\bET\s?UX\b|\bET\s?AL\b|\bTRS?\b|\bJR\b|\bSR\b|\bII+\b', '', s)
    if ',' in s:
        last, _, rest = s.partition(',')
    else:
        toks = s.split()
        last, rest = (toks[0], ' '.join(toks[1:])) if toks else ('', '')
    return (re.sub(r'[^A-Z]', '', last), re.sub(r'[^A-Z]', '', (rest.split() or [''])[0]))


def search_name(name):
    """'LAST, FIRST' query string for AcclaimWeb (middle names dropped: the index narrows on them)."""
    la, fi = name_key(name)
    return '%s, %s' % (la, fi) if fi else la


def legal_key(s):
    """A legal description reduced to comparable tokens. '' when there is nothing to compare."""
    s = (s or '').upper()
    s = re.sub(r'\bBLK\b', 'BLOCK', s)
    s = re.sub(r'\bLT\b', 'LOT', s)
    s = re.sub(r'\bSUBD?\b|\bSUBDIVISION\b|\bPLAT\b|\bBOOK\b|\bPAGE\b|\bPB\b|\bPG\b|\bAKA\b', ' ', s)
    toks = re.findall(r'[A-Z0-9]+', s)
    return ' '.join(toks)


def legal_match(a, b):
    """Same parcel by legal description: the lot/block/unit numbers agree and the words overlap."""
    ka, kb = legal_key(a), legal_key(b)
    if not ka or not kb:
        return False
    if ka == kb or ka in kb or kb in ka:
        return True
    ta, tb = set(ka.split()), set(kb.split())
    na = {t for t in ta if t.isdigit()}
    nb = {t for t in tb if t.isdigit()}
    if na != nb:
        return False
    wa, wb = ta - na, tb - nb
    if not wa or not wb:
        return False
    return len(wa & wb) / float(len(wa | wb)) >= 0.6


# ---------------------------------------------------------------------------------------------
# classification: AcclaimWeb grid rows -> the subject property's debt picture (pure, testable)
# ---------------------------------------------------------------------------------------------
def _kind(doc, holder):
    d = (doc or '').upper()
    if LP_RE.search(d):
        return 'lis_pendens'
    if REL_RE.search(d):
        return 'partial_release' if PARTIAL_RE.search(d) else 'release'
    if MOD_RE.search(d):
        return 'modification'
    if ASSIGN_RE.search(d):
        return 'assignment'
    if d.startswith('MORTGAGE') or d == 'MTG':
        return 'hud_partial_claim' if HUD_RE.search(holder or '') else 'mortgage'
    if DEED_RE.search(d):
        return 'deed'
    if LIEN_RE.search(d):
        h = holder or ''
        if 'JUDG' in d:
            return 'judgment'
        if IRS_RE.search(h):
            return 'irs_lien'
        if HOA_RE.search(h):
            return 'hoa_lien'
        if TAX_RE.search(h) or 'TAX' in d:
            return 'tax_lien'
        if CODE_RE.search(h):
            return 'code_lien'
        return 'other_lien'
    return 'other'


DEBT_KINDS = ('mortgage', 'hud_partial_claim', 'hoa_lien', 'code_lien', 'tax_lien', 'irs_lien',
              'judgment', 'other_lien')
LIEN_KINDS = ('hoa_lien', 'code_lien', 'tax_lien', 'irs_lien', 'judgment', 'other_lien')


def merge_rows(searches):
    """searches: {searched_name: [grid rows]} -> one record per instrument, with every name it was
    found under and the side each name was on."""
    out = {}
    for searched, rows in (searches or {}).items():
        for r in rows or []:
            inst = str(r.get('InstrumentNumber') or '').strip()
            if not inst.strip('0'):
                inst = 'BP:' + str(r.get('BookPage') or '') + ':' + str(r.get('DocTypeDescription') or '')
            rec = out.get(inst)
            holder = (r.get('CrossPartyName') or '').strip()
            if rec is None:
                rec = out[inst] = {
                    'inst': inst, 'doc': (r.get('DocTypeDescription') or '').strip(),
                    'd': _date(r.get('RecordDate')), 'amt': _num(r.get('Consideration')),
                    'holder': holder, 'legal': (r.get('DocLegalDescription') or '').strip(),
                    'parcel': _digits(r.get('ParcelNumber')), 'case': (r.get('CaseNumber') or '').strip().upper(),
                    'bp': str(r.get('BookPage') or ''), 'names': [], 'sides': [],
                    'refs': list(r.get('_refs') or []),
                }
            nm = (r.get('Name') or '').strip()
            if nm and nm not in rec['names']:
                rec['names'].append(nm)
            side = (r.get('Party') or '').strip().upper()
            if side and side not in rec['sides']:
                rec['sides'].append(side)
            if holder and not rec['holder']:
                rec['holder'] = holder
            for k in ('legal', 'parcel', 'case'):
                v = {'legal': (r.get('DocLegalDescription') or '').strip(),
                     'parcel': _digits(r.get('ParcelNumber')),
                     'case': (r.get('CaseNumber') or '').strip().upper()}[k]
                if v and not rec[k]:
                    rec[k] = v
            if not rec['amt']:
                rec['amt'] = _num(r.get('Consideration'))
            for ref in r.get('_refs') or []:
                if ref not in rec['refs']:
                    rec['refs'].append(ref)
    for rec in out.values():
        rec['kind'] = _kind(rec['doc'], rec['holder'])
    return out


def _borrower_side(rec, people):
    """Did one of our people sign this as borrower / grantor / debtor? (Party FROM in the index.)"""
    keys = {name_key(p) for p in people}
    return 'FROM' in rec['sides'] and any(name_key(n) in keys for n in rec['names'])


def classify(searches, owner, coborrowers=(), folio='', subject_legal='', links=None, lead_case=''):
    """The debt picture for the subject property.

    searches     {searched name: AcclaimWeb grid rows}
    owner        owner of record ('LAST, FIRST ...')
    coborrowers  people on the loans who are not owners of record
    folio        subject parcel id (its digits are matched against ParcelNumber on deeds)
    subject_legal  the appraiser's legal description, when known
    links        instrument -> broward_liens.parse_details() record ({'p': [...], 'c': [...]})

    Returns a dict: instruments (all, classified), open_debts, released, other_property, unknowns,
    anchors, lis_pendens, modifications, assignments."""
    links = links or {}
    people = [owner] + [c for c in (coborrowers or []) if c]
    recs = merge_rows(searches)
    fd = _digits(folio)
    anchors = []
    if subject_legal:
        anchors.append(subject_legal)
    for r in recs.values():
        if fd and r['parcel'] and (r['parcel'] == fd or r['parcel'].endswith(fd) or fd.endswith(r['parcel'])):
            if r['legal'] and not any(legal_key(r['legal']) == legal_key(a) for a in anchors):
                anchors.append(r['legal'])
    # place every instrument: on the subject, on another property, or unplaced
    for r in recs.values():
        if fd and r['parcel'] and (r['parcel'] == fd or r['parcel'].endswith(fd) or fd.endswith(r['parcel'])):
            r['place'] = 'subject'
        elif r['legal'] and anchors:
            r['place'] = 'subject' if any(legal_match(r['legal'], a) for a in anchors) else 'other'
        elif lead_case and r['case'] == lead_case.upper():
            r['place'] = 'subject'
        else:
            r['place'] = 'unplaced'

    # release pairing: recorder link first, then a reference in the release's own text, then the
    # same holder recording a release on or after the debt (each release used once)
    releases = [r for r in recs.values() if r['kind'] == 'release']
    debts = [r for r in recs.values() if r['kind'] in DEBT_KINDS]
    used = set()

    def linked(rel, debt):
        lk = links.get(rel['inst']) or {}
        refs = set(lk.get('p') or []) | set(lk.get('c') or []) | set(rel.get('refs') or [])
        dl = links.get(debt['inst']) or {}
        back = set(dl.get('p') or []) | set(dl.get('c') or [])
        return (debt['inst'] in refs or (debt['bp'] and debt['bp'] in refs) or rel['inst'] in back)

    for debt in sorted(debts, key=lambda x: x['d']):
        for rel in releases:
            if rel['inst'] in used:
                continue
            if linked(rel, debt):
                debt['released_by'] = rel['inst']; debt['released_on'] = rel['d']; used.add(rel['inst'])
                break
    for debt in sorted(debts, key=lambda x: x['d']):
        if debt.get('released_by'):
            continue
        dn = re.sub(r'\D', '', debt['inst'])
        for rel in releases:
            if rel['inst'] in used:
                continue
            txt = ' '.join([rel['legal'], rel['holder'], ' '.join(rel.get('refs') or [])])
            if dn and len(dn) >= 7 and dn in re.sub(r'[^\d ]', ' ', txt).split():
                debt['released_by'] = rel['inst']; debt['released_on'] = rel['d']; used.add(rel['inst'])
                break
    for debt in sorted(debts, key=lambda x: x['d']):
        if debt.get('released_by') or not debt['d']:
            continue
        h = _inst_name(debt['holder'])
        if not h:
            continue
        for rel in sorted(releases, key=lambda x: x['d']):
            if rel['inst'] in used or not rel['d'] or rel['d'] < debt['d']:
                continue
            if _inst_name(rel['holder']) == h:
                debt['released_by'] = rel['inst']; debt['released_on'] = rel['d']; used.add(rel['inst'])
                debt['release_how'] = 'same holder, later date'
                break

    open_debts, released, other_property, unknowns = [], [], [], []
    for r in sorted(debts, key=lambda x: x['d']):
        if r['kind'] in ('mortgage', 'hud_partial_claim') and not _borrower_side(r, people):
            continue          # a mortgage one of our people HOLDS (lender side), not one they owe
        if r.get('released_by'):
            released.append(r)
            continue
        if r['place'] == 'other':
            other_property.append(r)
            continue
        if r['place'] == 'unplaced':
            unknowns.append({'what': 'open %s %s recorded %s has no legal description or parcel to tie '
                                     'it to this property' % (r['kind'], r['inst'], r['d']), 'inst': r['inst']})
            continue
        if r['amt'] <= 0:
            unknowns.append({'what': 'open %s %s recorded %s carries no amount in the index'
                                     % (r['kind'], r['inst'], r['d']), 'inst': r['inst']})
        open_debts.append(r)

    mods = sorted([r for r in recs.values() if r['kind'] == 'modification' and r['place'] != 'other'],
                  key=lambda x: x['d'])
    assigns = sorted([r for r in recs.values() if r['kind'] == 'assignment' and r['place'] != 'other'],
                     key=lambda x: x['d'])
    lps = sorted([r for r in recs.values() if r['kind'] == 'lis_pendens' and r['place'] != 'other'],
                 key=lambda x: x['d'])
    return {'instruments': recs, 'open_debts': open_debts, 'released': released,
            'other_property': other_property, 'unknowns': unknowns, 'anchors': anchors,
            'modifications': mods, 'assignments': assigns, 'lis_pendens': lps}


def _item(r, **extra):
    out = {'inst': r['inst'], 'kind': r['kind'], 'recorded': r['d'], 'holder': r['holder'][:60],
           'amount': _money(r['amt']) if r['amt'] > 0 else None}
    if r.get('released_by'):
        out['released_by'] = r['released_by']
        out['released_on'] = r.get('released_on', '')
    out.update(extra)
    return out


def pick_first(open_debts, case_facts=None, assignments=()):
    """The mortgage being foreclosed: an open non-HUD mortgage. The plaintiff (or an assignee that
    names it) decides between several; else the largest."""
    cands = [r for r in open_debts if r['kind'] == 'mortgage']
    if not cands:
        return None
    pl = _inst_name((case_facts or {}).get('plaintiff'))
    if pl and len(cands) > 1:
        for r in cands:
            if pl in _inst_name(r['holder']):
                return r
    return max(cands, key=lambda r: r['amt'])


# ---------------------------------------------------------------------------------------------
# the row: sources -> debt -> equity, with the fail-loud rules
# ---------------------------------------------------------------------------------------------
def src(status, data=None, why='', at=None, origin=''):
    return {'status': status, 'data': data if data is not None else {}, 'why': why,
            'at': at or _iso(), 'origin': origin}


def compute(lead, sources, records_searches=None, coborrowers=(), links=None, truncated=False,
            nrec=None):
    """Build the output row. `sources` maps each of SOURCES to a src() dict. `records_searches` is
    the raw grid rows per searched name (the records source's payload)."""
    case = str(lead.get('case') or '').upper()
    owner = lead.get('owner') or lead.get('owners') or ''
    folio = lead.get('folio') or lead.get('parcel') or ''
    srcs = {k: sources.get(k) or src(EMPTY, why='not run') for k in SOURCES}
    unknowns = []
    appr = srcs['appraiser']['data'] if srcs['appraiser']['status'] == OK else {}
    cf = srcs['case']['data'] if srcs['case']['status'] == OK else {}
    pic = None
    if srcs['records']['status'] == OK:
        pic = classify(records_searches or {}, owner, coborrowers, folio=folio,
                       subject_legal=appr.get('legal', ''), links=links, lead_case=case)
        unknowns += pic['unknowns']
        if truncated:
            unknowns.append({'what': 'an Official Records name search returned a full page, so later '
                                     'records may be missing'})
        if nrec is not None and nrec > NAMESAKE_RECORDS and not pic['anchors']:
            unknowns.append({'what': 'common name (%d records) and no deed carrying the parcel id to '
                                     'anchor them: a namesake\'s loans may be mixed in' % nrec})

    first = None
    debt_first = None
    debt_first_src = ''
    subordinate, liens = [], []
    if pic is not None:
        first = pick_first(pic['open_debts'], cf, pic['assignments'])
        mods_on_first = [m for m in pic['modifications'] if m['amt'] > 0]
        if first is None:
            if cf.get('status', '').lower() in ('active', 'open', 'pending') and not cf.get('hoa_case'):
                unknowns.append({'what': 'a lender is foreclosing but no open first mortgage was found '
                                         'under these names: the search missed it'})
        else:
            principal = _num(cf.get('principal'))
            if principal > 0:
                debt_first, debt_first_src = principal, 'principal claimed in the case'
            elif mods_on_first:
                debt_first, debt_first_src = mods_on_first[-1]['amt'], 'last recorded modification balance'
            elif first['amt'] > 0:
                debt_first, debt_first_src = first['amt'], 'recorded original amount'
        for r in pic['open_debts']:
            if r is first:
                continue
            if r['kind'] in ('mortgage', 'hud_partial_claim'):
                subordinate.append(_item(r, fha_partial_claim=(r['kind'] == 'hud_partial_claim')))
            else:
                liens.append(_item(r))
    # the tax collector's delinquent amount is a lien the index may not carry
    tx = srcs['tax']['data'] if srcs['tax']['status'] == OK else {}
    if _num(tx.get('due')) > 0:
        liens.append({'inst': 'tax-collector', 'kind': 'delinquent_taxes', 'recorded': tx.get('checked', ''),
                      'holder': 'Broward County Tax Collector', 'amount': _money(_num(tx['due'])),
                      'certificate': bool(tx.get('cert'))})

    debt_sub = round(sum(l['amount'] or 0 for l in subordinate), 2)
    debt_liens = round(sum(l['amount'] or 0 for l in liens), 2)
    not_ok = [k for k in REQUIRED if srcs[k]['status'] != OK]
    verdict = UNVERIFIED if (not_ok or unknowns or debt_first is None) else VERIFIED
    debt_total = None
    if debt_first is not None and not unknowns and pic is not None:
        debt_total = round(debt_first + debt_sub + debt_liens, 2)

    value_low = _num(appr.get('market')) or None
    av = srcs['avm']['data'] if srcs['avm']['status'] == OK else {}
    value_high = _num(av.get('value')) or None
    eq_low = eq_high = None
    why = []
    if not_ok:
        why.append('not verified: %s' % ', '.join('%s %s' % (k, srcs[k]['status']) for k in not_ok))
    if unknowns:
        why.append('a debt component is unknown')
    if debt_first is None and not not_ok:
        why.append('no first mortgage amount')
    if verdict == VERIFIED:
        if value_low:
            eq_low = round(value_low - debt_total, 2)
        else:
            why.append('no county value, so no equity_low')
        if value_high:
            eq_high = round(value_high - debt_total, 2)
        else:
            why.append('no online estimate (%s), so no equity_high' % srcs['avm']['status'])

    owners = [o.strip() for o in re.split(r'\s*&\s*|\s*;\s*', owner) if o.strip()] if owner else []
    row = {
        'case_key': case,
        'parcel': folio,
        'address': lead.get('addr') or lead.get('address') or '',
        'owners': owners,
        'coborrowers': [c for c in coborrowers if c],
        'value_low': _money(value_low), 'value_low_src': 'county market value' if value_low else '',
        'value_high': _money(value_high), 'value_high_src': av.get('src', '') if value_high else '',
        'debt_first': _money(debt_first), 'debt_first_src': debt_first_src,
        'debt_first_inst': first['inst'] if first else '',
        'debt_subordinate': subordinate,
        'debt_subordinate_total': debt_sub,
        'debt_liens': liens,
        'debt_liens_total': debt_liens,
        'debt_total': _money(debt_total),
        'debt_note': PAYOFF_NOTE,
        'equity_low': _money(eq_low), 'equity_high': _money(eq_high),
        'equity_why': '; '.join(why),
        'fha_partial_claims': [l['inst'] for l in subordinate if l.get('fha_partial_claim')],
        'released': [_item(r) for r in (pic['released'] if pic else [])],
        'other_property': [_item(r) for r in (pic['other_property'] if pic else [])],
        'modifications': [_item(r) for r in (pic['modifications'] if pic else [])],
        'assignments': [_item(r) for r in (pic['assignments'] if pic else [])],
        'lis_pendens': [dict(_item(r), case=r['case']) for r in (pic['lis_pendens'] if pic else [])],
        'unknowns': [u['what'] for u in unknowns],
        'case_number': cf.get('case_number') or case,
        'case_plaintiff': cf.get('plaintiff', ''),
        'case_filed': cf.get('filed', ''),
        'case_status': cf.get('status', ''),
        'case_principal': _money(_num(cf.get('principal'))) if cf.get('principal') else None,
        'sale_date': cf.get('sale', ''),
        'other_cases': cf.get('other_cases') or [],
        'tax_status': (('delinquent $%.2f' % _num(tx['due']) + (' (certificate sold)' if tx.get('cert') else ''))
                       if _num(tx.get('due')) > 0 else ('paid' if srcs['tax']['status'] == OK else '')),
        'homestead': appr.get('homestead'),
        'source_status': {k: srcs[k]['status'] for k in SOURCES},
        'source_why': {k: srcs[k]['why'] for k in SOURCES if srcs[k]['why']},
        'source_at': {k: srcs[k]['at'] for k in SOURCES},
        'verdict': verdict,
        'last_checked': _iso(),
    }
    return row


# ---------------------------------------------------------------------------------------------
# storage: append-only log, latest view that never lets an unverified run replace a verified row
# ---------------------------------------------------------------------------------------------
def _dir():
    import paths as P
    d = P.out('deep_equity')
    os.makedirs(d, exist_ok=True)
    return d


def record(row, base=None):
    """Append the row to runs.jsonl, then update latest.json. Returns the latest entry."""
    base = base or _dir()
    with open(os.path.join(base, 'runs.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, sort_keys=True) + '\n')
    lp = os.path.join(base, 'latest.json')
    try:
        latest = json.load(open(lp, encoding='utf-8'))
        if not isinstance(latest, dict):
            raise ValueError('latest.json is not a map')
    except FileNotFoundError:
        latest = {}
    except Exception as e:
        # an unreadable latest view is never silently replaced: keep it, write beside it
        os.replace(lp, lp + '.unreadable-' + _now().strftime('%Y%m%d%H%M%S'))
        print('deep_equity: latest.json unreadable (%s); kept a copy, rebuilding from this run' % e)
        latest = {}
    key = row['case_key']
    ent = latest.get(key) or {}
    if row['verdict'] == VERIFIED:
        ent['verified'] = row
    ent['last_attempt'] = row
    latest[key] = ent
    tmp = lp + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(latest, f, indent=1, sort_keys=True)
    os.replace(tmp, lp)
    _write_csv(latest, os.path.join(base, 'latest.csv'))
    return ent


CSV_COLS = ('parcel', 'address', 'owners', 'coborrowers', 'value_low', 'value_high', 'debt_first',
            'debt_subordinate', 'debt_liens', 'debt_total', 'equity_low', 'equity_high', 'case_number',
            'case_status', 'sale_date', 'tax_status', 'source_status', 'verdict', 'last_checked',
            'shown_from')


def _write_csv(latest, path):
    rows = []
    for key in sorted(latest):
        ent = latest[key]
        r = ent.get('verified') or ent.get('last_attempt')
        if not r:
            continue
        shown = 'verified' if ent.get('verified') else 'last attempt (unverified)'
        if ent.get('verified') and ent.get('last_attempt', {}).get('verdict') != VERIFIED:
            shown = 'verified %s; a later run was %s' % (r['last_checked'][:10],
                                                         ent['last_attempt']['verdict'])
        rows.append({
            'parcel': r['parcel'], 'address': r['address'], 'owners': ' & '.join(r['owners']),
            'coborrowers': ' & '.join(r['coborrowers']), 'value_low': r['value_low'],
            'value_high': r['value_high'], 'debt_first': r['debt_first'],
            'debt_subordinate': '; '.join('%s %s %s' % (l['inst'], l['holder'][:30], l['amount'])
                                          for l in r['debt_subordinate']),
            'debt_liens': '; '.join('%s %s %s' % (l['inst'], l['kind'], l['amount']) for l in r['debt_liens']),
            'debt_total': r['debt_total'], 'equity_low': r['equity_low'], 'equity_high': r['equity_high'],
            'case_number': r['case_number'], 'case_status': r['case_status'], 'sale_date': r['sale_date'],
            'tax_status': r['tax_status'],
            'source_status': ' '.join('%s=%s' % kv for kv in r['source_status'].items()),
            'verdict': r['verdict'], 'last_checked': r['last_checked'], 'shown_from': shown})
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


class RunLog:
    """Every source failure, timestamped, in deep_equity/run-<stamp>.log."""

    def __init__(self, base=None):
        self.path = os.path.join(base or _dir(), 'run-%s.log' % _now().strftime('%Y%m%d-%H%M%S'))

    def __call__(self, msg):
        line = '%s %s' % (_iso(), msg)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


# ---------------------------------------------------------------------------------------------
# live sources (laptop). Each returns src(); none raises.
# ---------------------------------------------------------------------------------------------
class Site:
    """Polite pacing per site and the 3-blocks-in-a-row stop."""

    def __init__(self, name, sleeper=time.sleep, clock=time.time, rand=random.random):
        self.name, self.sleep, self.clock, self.rand = name, sleeper, clock, rand
        self.last = 0.0
        self.blocks = 0
        self.stopped = False

    def wait(self):
        gap = MIN_GAP_S + self.rand() * JITTER_S
        dt = self.clock() - self.last
        if dt < gap:
            self.sleep(gap - dt)
        self.last = self.clock()

    def saw(self, body):
        """Feed every response. True when it was a human check / block page."""
        if body is None or CHALLENGE_RE.search(body or ''):
            self.blocks += 1
            if self.blocks >= BLOCK_TRIP:
                self.stopped = True
            return True
        self.blocks = 0
        return False


def manual_path(case, base=None):
    return os.path.join(base or _dir(), 'manual', re.sub(r'[^A-Z0-9-]', '_', case.upper()) + '.json')


def load_manual(case, base=None):
    try:
        return json.load(open(manual_path(case, base), encoding='utf-8'))
    except Exception:
        return {}


def save_manual(case, updates, base=None):
    p = manual_path(case, base)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cur = load_manual(case, base)
    cur.update(updates)
    cur['entered_at'] = _iso()
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cur, f, indent=1, sort_keys=True)
    return cur


def case_source(case, manual, lp_rows=(), today=None):
    """The clerk's case search is behind a human check, so the case facts come from a hand entry.
    A Lis Pendens in Official Records corroborates the number and plaintiff but cannot say the
    status or the amount claimed, so on its own it is not OK."""
    today = today or datetime.date.today()
    m = (manual or {}).get('case') or {}
    corr = [{'case': r.get('case'), 'holder': r.get('holder'), 'recorded': r.get('recorded')}
            for r in lp_rows or []]
    if m:
        at = str((manual or {}).get('entered_at') or '')[:10]
        try:
            age = (today - datetime.date.fromisoformat(at)).days
        except ValueError:
            age = 10 ** 6
        need = [k for k in ('status', 'principal', 'filed', 'plaintiff') if not str(m.get(k) or '').strip()]
        if need:
            return src(ERROR, dict(m, lis_pendens=corr), 'hand entry is missing ' + ', '.join(need),
                       origin='manual')
        if age > CASE_MANUAL_TTL_DAYS:
            return src(BLOCKED, dict(m, lis_pendens=corr),
                       'hand entry is %d days old (max %d); re-read the docket' % (age, CASE_MANUAL_TTL_DAYS),
                       origin='manual')
        d = dict(m, lis_pendens=corr)
        d.setdefault('case_number', case)
        return src(OK, d, '', at=(manual or {}).get('entered_at'), origin='manual (docket read by hand)')
    return src(BLOCKED, {'lis_pendens': corr},
               'clerk case search is behind a Cloudflare human check and is not automated; read the '
               'docket and save it with --set-case', origin='browardclerk.org')


def appraiser_source(lead):
    try:
        import pa_values
        info = pa_values.lookup('BROWARD', lead.get('folio') or '', lead.get('addr') or '')
    except Exception as e:
        return src(ERROR, why='appraiser lookup raised %s' % type(e).__name__, origin='bcpa')
    if not info:
        return src(EMPTY, why='no appraiser record for this folio / address', origin='bcpa')
    mv = _num(info.get('market_value'))
    d = {'market': mv, 'assessed': _num(info.get('assessed_value')), 'owner': info.get('owner', ''),
         'homestead': bool(info.get('homestead')), 'legal': info.get('legal', ''),
         'parcel': info.get('parcel_id', '')}
    if mv <= 0:
        return src(EMPTY, d, 'appraiser record has no market value', origin=info.get('src', 'bcpa'))
    return src(OK, d, origin=info.get('src', 'bcpa'))


def tax_source(lead, fresh=False, today=None, raw=None):
    """county_taxes' cached read, or a fresh one (Tier 3). A read older than TAX_MAX_AGE_DAYS is not OK."""
    today = today or datetime.date.today()
    f = _digits(lead.get('folio'))
    if len(f) != 12:
        return src(EMPTY, why='no 12-digit Broward folio to look up', origin='broward.county-taxes.com')
    rec = None
    if fresh:
        try:
            import asyncio
            import county_taxes
            got = asyncio.run(county_taxes._run([('BROWARD', f)], 1))
            rec = (got or {}).get(f)
            if rec is not None:
                rec = dict(rec, checked=today.isoformat())
            else:
                return src(BLOCKED, why='tax collector page did not render (Cloudflare or timeout)',
                           origin='broward.county-taxes.com')
        except Exception as e:
            return src(ERROR, why='fresh tax read raised %s' % type(e).__name__,
                       origin='broward.county-taxes.com')
    else:
        try:
            cache = json.load(open(os.path.join(DATA, '_county_taxes_cache.json'), encoding='utf-8'))
        except Exception:
            cache = {}
        rec = cache.get(f)
    if raw is not None:
        raw['tax'] = rec
    if rec is None:
        return src(EMPTY, why='tax collector never read for this folio', origin='broward.county-taxes.com')
    try:
        age = (today - datetime.date.fromisoformat(str(rec.get('checked'))[:10])).days
    except ValueError:
        age = 10 ** 6
    if age > TAX_MAX_AGE_DAYS:
        return src(BLOCKED, rec, 'tax read is %d days old (max %d)' % (age, TAX_MAX_AGE_DAYS),
                   origin='broward.county-taxes.com')
    return src(OK, rec, origin='broward.county-taxes.com')


def avm_source(lead, manual):
    z = _num((manual or {}).get('zestimate'))
    if z > 0:
        return src(OK, {'value': z, 'src': 'Zestimate typed in by hand %s' % str(manual.get('entered_at', ''))[:10]},
                   origin='manual')
    try:
        cache = json.load(open(os.path.join(DATA, 'redfin_cache.json'), encoding='utf-8'))
    except Exception:
        cache = {}
    e = cache.get(_digits(lead.get('folio')))
    if not e:
        return src(EMPTY, why='no Redfin Estimate cached and no Zestimate entered (Zillow is not scraped)',
                   origin='redfin')
    if e.get('conf') == 'nomatch' or not _num(e.get('v')):
        return src(EMPTY, why='Redfin has no estimate for this parcel', origin='redfin')
    return src(OK, {'value': _num(e['v']), 'src': 'Redfin Estimate'}, origin='redfin')


def records_source(people, site, log, raw=None, fetch_links=True):
    """Name search for each person through broward_liens' AcclaimWeb session (native curl on the
    laptop). Returns (src, searches, links, truncated, nrec)."""
    import broward_liens as BL
    searches, links, truncated, nrec = {}, {}, False, 0
    sess = getattr(site, 'sess', None)          # one AcclaimWeb session for the whole run
    for attempt in range(0 if sess else BLOCK_TRIP):
        site.wait()
        try:
            sess = BL.start_session()
        except Exception as e:
            log('records: session raised %s' % type(e).__name__)
            sess = None
        if sess:
            site.saw('')
            site.sess = sess
            break
        site.saw(None)
        log('records: session attempt %d blocked' % (attempt + 1))
    if not sess:
        return src(BLOCKED, why='AcclaimWeb session blocked %d times; stopped for this run' % BLOCK_TRIP,
                   origin='officialrecords.broward.org'), {}, {}, False, 0
    for p in people:
        q = search_name(p)
        if not q or site.stopped:
            continue
        site.wait()
        try:
            rows = BL.search_docs(sess, q)
        except Exception as e:
            log('records: search raised %s' % type(e).__name__)
            rows = None
        if rows is None:
            site.saw(None)
            log('records: search for one name returned no grid (block or error)')
            return (src(BLOCKED if site.stopped else ERROR,
                        why='name search did not return a result grid', origin='officialrecords.broward.org'),
                    searches, links, truncated, nrec)
        site.saw('')
        truncated = truncated or bool(getattr(BL.search_docs, 'last_truncated', False))
        key = name_key(p)
        exact = [r for r in rows if name_key(r.get('Name')) == key]
        nrec += len(exact)
        searches[q] = exact
    if raw is not None:
        raw['records'] = searches
    if fetch_links:
        want = set()
        for rows in searches.values():
            for r in rows:
                k = _kind(r.get('DocTypeDescription'), r.get('CrossPartyName'))
                if k in DEBT_KINDS or k == 'release':
                    want.add(str(r.get('InstrumentNumber') or '').strip())
        for inst in sorted(i for i in want if i.strip('0'))[:DETAILS_PER_LEAD]:
            if site.stopped:
                log('records: details pages stopped after %d blocks' % BLOCK_TRIP)
                break
            site.wait()
            try:
                got = BL.fetch_details(inst, tries=1)
            except Exception:
                got = None
            site.saw('' if got else None)
            if got:
                links[inst] = got
        if raw is not None:
            raw['links'] = links
    if not any(searches.values()):
        return src(EMPTY, why='no recorded instruments under these names', origin='officialrecords.broward.org'), \
            searches, links, truncated, nrec
    return src(OK, {'names': list(searches), 'records': nrec, 'details_read': len(links)},
               origin='officialrecords.broward.org'), searches, links, truncated, nrec


# ---------------------------------------------------------------------------------------------
# leads and tiers
# ---------------------------------------------------------------------------------------------
def _load(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def broward_leads():
    rows = _load(os.path.join(DATA, 'broward_leads.json'), [])
    seen = {str(r.get('case') or '') for r in rows}
    for r in _load(os.path.join(DATA, 'lp_leads.json'), []):
        if str(r.get('county') or '').upper().startswith('BROW') and str(r.get('case') or '') not in seen:
            rows.append(r)
    return [r for r in rows if isinstance(r, dict) and r.get('case')]


def case_norm(c):
    return re.sub(r'[^A-Z0-9]', '', str(c or '').upper())


def find_lead(case, leads=None):
    want = case_norm(case)
    for r in leads if leads is not None else broward_leads():
        if case_norm(r.get('case')) == want:
            return r
    return None


def has_phone(lead, skip=None):
    if lead.get('phones') or lead.get('ph'):
        return True
    e = (skip or {}).get(lead.get('case') or '') or {}
    return any(p.get('number') for p in (e.get('phones') or []) if isinstance(p, dict))


def days_to_sale(lead, today=None):
    today = today or datetime.date.today()
    d = _date(lead.get('auction') or lead.get('AuctionDate') or '')
    if not d:
        return None
    return (datetime.date.fromisoformat(d) - today).days


def tier2(leads, skip, latest=None, today=None, limit=25):
    """auction within 30 days + has a phone + a case on the row; soonest first; leads verified in
    the last TIER2_RECHECK_DAYS are skipped."""
    today = today or datetime.date.today()
    latest = latest or {}
    out = []
    for r in leads:
        dts = days_to_sale(r, today)
        if dts is None or not (0 <= dts <= TIER2_DAYS):
            continue
        if not has_phone(r, skip) or not str(r.get('case') or '').strip():
            continue
        v = (latest.get(str(r.get('case')).upper()) or {}).get('verified')
        if v:
            try:
                if (today - datetime.date.fromisoformat(v['last_checked'][:10])).days < TIER2_RECHECK_DAYS:
                    continue
            except ValueError:
                pass
        out.append((dts, r))
    out.sort(key=lambda x: x[0])
    return [r for _, r in out[:limit]]


def coborrowers_for(lead, manual):
    owner = lead.get('owners') or ''
    parts = [p.strip() for p in re.split(r'\s*&\s*|\s*;\s*', owner) if p.strip()]
    co = list((manual or {}).get('coborrowers') or [])
    for p in parts[1:]:
        if p not in co and not re.match(r'^[WH]\b', p):
            co.append(p)
    return parts[0] if parts else owner, co


def run_one(lead, fresh=False, log=None, sites=None, record_raw=False, base=None):
    base = base or _dir()
    log = log or RunLog(base)
    sites = sites if sites is not None else {}
    case = str(lead.get('case') or '').upper()
    manual = load_manual(case, base)
    owner, co = coborrowers_for(lead, manual)
    raw = {} if record_raw else None
    sources = {'appraiser': appraiser_source(lead)}
    site = sites.setdefault('records', Site('records'))
    if site.stopped:
        rs, searches, links, trunc, nrec = (src(BLOCKED, why='stopped earlier this run after %d blocks' % BLOCK_TRIP,
                                                origin='officialrecords.broward.org'), {}, {}, False, 0)
    else:
        rs, searches, links, trunc, nrec = records_source([owner] + co, site, log, raw)
    sources['records'] = rs
    lps = []
    if searches:
        pic = classify(searches, owner, co, folio=lead.get('folio', ''), lead_case=case)
        lps = [{'case': r['case'], 'holder': r['holder'], 'recorded': r['d']} for r in pic['lis_pendens']]
    sources['case'] = case_source(case, manual, lps)
    sources['tax'] = tax_source(lead, fresh=fresh, raw=raw)
    sources['avm'] = avm_source(lead, manual)
    for k, s in sources.items():
        if s['status'] != OK:
            log('%s %s %s: %s' % (case, k, s['status'], s['why']))
    row = compute(dict(lead, owner=owner), sources, searches, co, links, trunc, nrec)
    if record_raw:
        rd = os.path.join(base, 'raw', re.sub(r'[^A-Z0-9-]', '_', case))
        os.makedirs(rd, exist_ok=True)
        with open(os.path.join(rd, _now().strftime('%Y%m%d-%H%M%S') + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'lead': lead, 'owner': owner, 'coborrowers': co, 'sources': sources,
                       'raw': raw, 'truncated': trunc, 'nrec': nrec}, f, indent=1, sort_keys=True)
    record(row, base)
    return row


def replay(case, base=None):
    """Re-parse the newest recorded responses for a case. No network."""
    base = base or _dir()
    rd = os.path.join(base, 'raw', re.sub(r'[^A-Z0-9-]', '_', case.upper()))
    files = sorted(f for f in os.listdir(rd) if f.endswith('.json')) if os.path.isdir(rd) else []
    if not files:
        raise SystemExit('no recorded responses for %s under %s (run --case %s --record first)' % (case, rd, case))
    j = json.load(open(os.path.join(rd, files[-1]), encoding='utf-8'))
    sources = j['sources']
    # the case facts may have been typed in after the recording
    manual = load_manual(case, base)
    if manual.get('case'):
        sources['case'] = case_source(case, manual, (sources.get('case') or {}).get('data', {}).get('lis_pendens'))
    if manual.get('zestimate'):
        sources['avm'] = avm_source(j['lead'], manual)
    return compute(dict(j['lead'], owner=j['owner']), sources, (j.get('raw') or {}).get('records') or {},
                   j.get('coborrowers') or [], (j.get('raw') or {}).get('links') or {},
                   j.get('truncated'), j.get('nrec'))


# ---------------------------------------------------------------------------------------------
# acceptance: a fixture with inputs and the expected answer
# ---------------------------------------------------------------------------------------------
def accept(fx):
    """fx = {'lead', 'coborrowers', 'searches', 'links', 'sources', 'expect'}, or {'replay': case,
    'expect'} to check the newest recorded live responses for that case. Returns (ok, lines, row)."""
    if fx.get('replay'):
        row = replay(fx['replay'])          # the laptop's recorded live responses, re-parsed
    else:
        sources = {k: src(**v) if isinstance(v, dict) and 'status' in v else v
                   for k, v in fx['sources'].items()}
        row = compute(fx['lead'], sources, fx.get('searches') or {}, fx.get('coborrowers') or [],
                      fx.get('links') or {}, fx.get('truncated', False), fx.get('nrec'))
    exp = fx['expect']
    lines, ok = [], True

    def check(label, got, want, tol=0.01):
        nonlocal ok
        good = (abs((got or 0) - want) <= tol) if isinstance(want, (int, float)) and got is not None else got == want
        ok = ok and good
        lines.append('%s %-34s got %-22s want %s' % ('PASS' if good else 'FAIL', label, got, want))

    for k in ('debt_first', 'debt_subordinate_total', 'debt_liens_total', 'debt_total', 'equity_low',
              'equity_high', 'value_low', 'value_high', 'verdict', 'case_status', 'sale_date',
              'case_principal', 'debt_first_inst'):
        if k in exp:
            check(k, row.get(k), exp[k])
    if 'subordinate' in exp:
        got = sorted((l['inst'], l['amount'], bool(l.get('fha_partial_claim'))) for l in row['debt_subordinate'])
        want = sorted((s['inst'], s['amount'], True) for s in exp['subordinate'])
        check('subordinate mortgages (FHA)', got, want)
    if 'released' in exp:
        got = sorted((r['inst'], r.get('released_by')) for r in row['released'])
        check('released', got, sorted(tuple(x) for x in exp['released']))
    if 'modifications' in exp:
        check('modifications', len(row['modifications']), exp['modifications'])
    if 'assignments' in exp:
        check('assignment to', [a['holder'] for a in row['assignments']], exp['assignments'])
    if 'unknowns' in exp:
        check('unknowns', row['unknowns'], exp['unknowns'])
    return ok, lines, row


# ---------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--case', default='')
    ap.add_argument('--tier', type=int, default=0)
    ap.add_argument('--limit', type=int, default=25)
    ap.add_argument('--max-runtime', type=int, default=TIER2_MAX_RUNTIME_S)
    ap.add_argument('--record', action='store_true', help='keep the raw responses for --replay')
    ap.add_argument('--replay', default='', help='re-parse the recorded responses for this case')
    ap.add_argument('--accept', default='', help='acceptance fixture JSON')
    ap.add_argument('--set-case', nargs='*', default=None,
                    help='hand-entered docket facts: plaintiff= filed= status= principal= sale=')
    ap.add_argument('--set-coborrower', action='append', default=[])
    ap.add_argument('--set-zestimate', type=float, default=0)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--folio', default='', help='with --case, when the case is not on a lead row')
    ap.add_argument('--owner', default='', help="with --case: 'LAST, FIRST'")
    ap.add_argument('--addr', default='')
    a = ap.parse_args(argv)

    if a.accept:
        ok, lines, row = accept(json.load(open(a.accept, encoding='utf-8')))
        print('\n'.join(lines))
        if a.json:
            print(json.dumps(row, indent=1))
        print('ACCEPTANCE', 'PASS' if ok else 'FAIL')
        return 0 if ok else 1

    if a.set_case is not None or a.set_coborrower or a.set_zestimate:
        if not a.case:
            raise SystemExit('--set-* needs --case')
        upd = {}
        if a.set_case is not None:
            facts = {}
            for kv in a.set_case:
                k, _, v = kv.partition('=')
                facts[k.strip().lower()] = v.strip()
            if 'principal' in facts:
                facts['principal'] = _num(facts['principal'])
            upd['case'] = facts
        if a.set_coborrower:
            upd['coborrowers'] = a.set_coborrower
        if a.set_zestimate:
            upd['zestimate'] = a.set_zestimate
        cur = save_manual(a.case, upd)
        print('saved hand entry for %s: %s' % (a.case, ', '.join(sorted(k for k in cur if k != 'entered_at'))))
        return 0

    if a.replay:
        row = replay(a.replay)
        print(json.dumps(row, indent=1))
        return 0

    if a.case:
        lead = find_lead(a.case)
        if lead is None and a.folio and a.owner:
            lead = {'case': a.case, 'folio': a.folio, 'owners': a.owner, 'addr': a.addr}
        if lead is None:
            print('no Broward lead for %s (pass --folio and --owner to check it anyway)' % a.case)
            return 1
        row = run_one(lead, fresh=True, record_raw=a.record)
        print(json.dumps(row, indent=1) if a.json else _summary(row))
        return 0

    if a.tier == 1:
        leads = broward_leads()
        n = len(leads)
        val = sum(1 for r in leads if _num(r.get('value')) > 0)
        cs = sum(1 for r in leads if str(r.get('case') or '').strip())
        snap = {'at': _iso(), 'leads': n, 'appraiser_value': val, 'case_number': cs,
                'note': 'Tier 1 reads the nightly appraiser value and case number already on the lead '
                        'rows; the clerk case search itself is not automated (human check).'}
        with open(os.path.join(_dir(), 'tier1.json'), 'w', encoding='utf-8') as f:
            json.dump(snap, f, indent=1)
        print('deep_equity tier 1: %d Broward leads, %d with a county value, %d with a case number'
              % (n, val, cs))
        return 0

    if a.tier == 2:
        base = _dir()
        latest = _load(os.path.join(base, 'latest.json'), {})
        skip = _load(os.path.join(DATA, 'skiptrace_results.json'), {})
        picks = tier2(broward_leads(), skip, latest, limit=a.limit)
        log = RunLog(base)
        sites = {}
        counts = {VERIFIED: 0, UNVERIFIED: 0}
        stat = {k: {} for k in SOURCES}
        t0 = time.time()
        done = 0
        for r in picks:
            if time.time() - t0 > a.max_runtime:
                print('  time budget %ds reached after %d lead(s); the rest go first next run' % (a.max_runtime, done))
                break
            row = run_one(r, fresh=False, log=log, sites=sites, base=base)
            done += 1
            counts[row['verdict']] += 1
            for k, v in row['source_status'].items():
                stat[k][v] = stat[k].get(v, 0) + 1
        # counts only on the console: the log file under DEALFLOW_DIR carries the detail
        print('deep_equity tier 2: %d shortlisted (sale <= %d days, phone, case); %d VERIFIED, %d UNVERIFIED'
              % (len(picks), TIER2_DAYS, counts[VERIFIED], counts[UNVERIFIED]))
        for k in SOURCES:
            print('  %-9s %s' % (k, ' '.join('%s=%d' % kv for kv in sorted(stat[k].items())) or '-'))
        if sites.get('records') and sites['records'].stopped:
            print('  FLAG: Official Records blocked %d times in a row; stopped for this run' % BLOCK_TRIP)
        _status(base, picks, counts, stat, sites)
        return 0

    ap.print_help()
    return 2


def _status(base, picks, counts, stat, sites):
    st = {'at': _iso(), 'shortlisted': len(picks), 'verified': counts[VERIFIED],
          'unverified': counts[UNVERIFIED], 'sources': stat,
          'records_stopped': bool(sites.get('records') and sites['records'].stopped)}
    with open(os.path.join(base, 'status.json'), 'w', encoding='utf-8') as f:
        json.dump(st, f, indent=1)


def _summary(r):
    L = ['%s  %s  %s' % (r['case_key'], r['verdict'], r['address']),
         '  sources: ' + ' '.join('%s=%s' % kv for kv in r['source_status'].items())]
    for k, w in r['source_why'].items():
        L.append('    %s: %s' % (k, w))
    L.append('  first mortgage  %s  (%s, %s)' % (r['debt_first'], r['debt_first_src'], r['debt_first_inst']))
    for l in r['debt_subordinate']:
        L.append('  subordinate     %s  %s %s%s' % (l['amount'], l['inst'], l['holder'][:40],
                                                   '  FHA PARTIAL CLAIM' if l.get('fha_partial_claim') else ''))
    for l in r['debt_liens']:
        L.append('  lien            %s  %s %s' % (l['amount'], l['inst'], l['kind']))
    for l in r['released']:
        L.append('  released        %s %s by %s on %s' % (l['inst'], l['kind'], l.get('released_by'), l.get('released_on')))
    for u in r['unknowns']:
        L.append('  UNKNOWN: ' + u)
    L.append('  debt_total %s   equity_low %s   equity_high %s' % (r['debt_total'], r['equity_low'], r['equity_high']))
    if r['equity_why']:
        L.append('  ' + r['equity_why'])
    L.append('  ' + r['debt_note'])
    return '\n'.join(L)


if __name__ == '__main__':
    sys.exit(main())
