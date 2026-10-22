#!/usr/bin/env python3
"""lis_pendens.py — THE FRONT OF THE FUNNEL.

Everyone buys the auction list (properties with a sale date already set) — that's the crowded tail
end, where ten investors dial the same dead phone the same week. The LIS PENDENS is recorded the day
the foreclosure is FILED, 8-14 months earlier, when nobody is calling yet. This sweeps newly-recorded
LIS PENDENS from the Miami-Dade Clerk Official Records so Jose can be the owner's FIRST contact.

Mechanics (verified against the live API): the same reCAPTCHA-gated standardsearch the owner-lien
tracer uses, but with partyName BLANK, documentType=LIS PENDENS, and a rolling recorded-date window.
The results fetch (getStandardRecords?qs=) is NOT gated. Then each hit is enriched via the statewide
cadastral (owner, market value, homestead, mailing address) exactly like the auction leads, and
RPMF-filtered (keep lender/bank plaintiffs; drop HOA, association, partition, divorce lis pendens).

Run:
  python lis_pendens.py --probe            # discovery: confirm the API + dump raw record shape
  python lis_pendens.py --days 30          # sweep the last 30 days, enrich, write lis_pendens.json

The Broward Official Records daily feed (broward_or_feed.py) is a separate lead
list. It runs from this collector only when DEALFLOW_BROWARD_OR_FEED=1, and it
is not merged into lis_pendens.json.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_records_qs as G   # reuse BASE, UA, SITE_KEY, the grecaptcha pattern

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'lis_pendens.json')
OR_API = 'https://onlineservices.miamidadeclerk.gov'

# Plaintiff type no longer DROPS a filing — every fresh LP is kept and TAGGED (Jose: no deal is dead).
# lender/bank = a 1st-mortgage foreclosure (buy/wholesale); HOA/association = a senior mortgage survives
# (short-sale/negotiate the survivor); individual/other = verify. All three are money, different plays.
# ⚠️ BOUNDARY BUG, fixed 2026-08-15. Both patterns used to end in `)\b`, a trailing word boundary on
# the WHOLE alternation — which silently defeated every token written as a PREFIX. `MORTG` could never
# match "MORTGAGE"; `ASSOC` could never match "ASSOCIATION"; `HOMEOWNER` could never match the plural
# "HOMEOWNERS"; `CONDO` could never match "CONDOMINIUM". Those are the most common real forms in
# Florida, so the classifier was blind to them. Measured over 955 lis-pendens rows, fixing it moves:
#   plaintiff  LENDER 506 -> 590 (+84)   HOA 198 -> 263 (+65)
#   owner      LENDER   0 ->  60 (+60)   HOA  15 -> 113 (+98)   <- these are FLIPPED-orientation rows
# The HOA half matters most: HOA-as-co-defendant is the tell that a SECOND case exists (the Milouse
# miss), and that screen was missing names like "FALLS OF INVERRARY CONDOMINIUMS INC".
# Tokens meant as prefixes now have NO trailing \b; genuinely-short/ambiguous ones keep it (COA would
# otherwise match "COAST", which is everywhere in FL names).
LENDER_RE = re.compile(r'\b(BANK|MORTG|LOAN|LENDING|FINANC|CAPITAL|FED(ERAL)?|CREDIT UNION|N\.?A\.?\b|'
                       r'FSB\b|TRUST|SERVICING|FUND|HOLDING|WILMINGTON|DEUTSCHE|WELLS FARGO|'
                       r'CHASE|CITI|US BANK|NATIONSTAR|CARRINGTON|SELENE|RUSHMORE|FREEDOM|PENNYMAC|'
                       r'PHH\b|SHELLPOINT|NEWREZ|LAKEVIEW|FANNIE|FREDDIE|HUD\b|SECRETARY)', re.I)
# "NATIONAL ASSOCIATION" is BANK nomenclature (the spelled-out N.A.), not a homeowners association —
# without this guard "US BANK TRUST NATIONAL ASSOCIATION" reads as an HOA. Callers classify LENDER
# first so order already saves the common case, but any standalone HOA test needs this.
_NOT_HOA_RE = re.compile(r'\bNATIONAL\s+ASSOC', re.I)
_HOA_CORE = re.compile(r'\b(HOA\b|COA\b|POA\b|CONDO|ASSOC|ASSN|HOMEOWNER|MASTER\b|COMMUNIT|'
                       r'VILLAS?\b|TOWERS?\b)', re.I)


class _HoaRe:
    """Drop-in for the old compiled HOA_RE (callers use .search) with the NATIONAL ASSOCIATION guard."""

    def search(self, s):
        s = s or ''
        if _NOT_HOA_RE.search(s):
            return None
        return _HOA_CORE.search(s)


HOA_RE = _HoaRe()

# the search JS: same mint, parameterized query. Left name blank + doc-type + date range.
SEARCH_JS = r"""
async (args) => {
  const KEY='SITEKEY';
  if(!window.grecaptcha || !window.grecaptcha.execute){
    await new Promise((res,rej)=>{ const s=document.createElement('script'); s.src='https://www.google.com/recaptcha/api.js?render='+KEY; s.onload=res; s.onerror=()=>rej(new Error('blocked')); document.head.appendChild(s); setTimeout(()=>rej(new Error('captcha load timeout')),25000); });
    await new Promise(r=>setTimeout(r,2000));
  }
  await new Promise(res=>grecaptcha.ready(res));
  const token=await grecaptcha.execute(KEY,{action:'standardsearch'});
  const [docType, dFrom, dTo, stype] = args;
  const url='/officialrecords/api/home/standardsearch?partyName=&dateRangeFrom='+encodeURIComponent(dFrom)
    +'&dateRangeTo='+encodeURIComponent(dTo)+'&documentType='+encodeURIComponent(docType)
    +'&searchT=&firstQuery=y&searchtype='+encodeURIComponent(stype);
  const r=await fetch(url,{method:'POST',headers:{'Accept':'application/json','x-recaptcha-token':token,'content-type':'application/json; charset=utf-8'},body:''});
  let j=null, raw=''; try{ raw=await r.text(); j=JSON.parse(raw); }catch(e){}
  if(!j || !j.qs) return {success:false, status:r.status, qs:null, raw:raw.slice(0,300)};
  const g=await fetch('/officialrecords/api/SearchResults/getStandardRecords?qs='+j.qs,{headers:{'Accept':'application/json'}});
  let gj=null; try{ gj=JSON.parse(await g.text()); }catch(e){}
  const arr=(gj && gj.recordingModels) || [];
  return {success:true, qs:j.qs, count:Array.isArray(arr)?arr.length:0, sample:arr.slice(0,60)};
}
"""
# NOTE (2026-08-11): this template is only consumed by the LEGACY reCAPTCHA-v3 browser probe.
# The substitution used to run at MODULE level against G.SITE_KEY — an attribute the rewritten
# gen_records_qs no longer defines — so `import lis_pendens` raised AttributeError and the LP
# sweep silently stopped at the Turnstile migration (newest filing sat at 7/9 for 33 days).
# The key is now resolved lazily inside _mint_search, and the LIVE path (lp_sweep) never
# touches it. A dead legacy helper must never be able to kill the import of the live one.


def _legacy_search_js():
    import records_liens as R
    key = getattr(G, 'SITE_KEY', '') or R.TS_SITE_KEY
    return SEARCH_JS.replace('SITEKEY', key)


def _mint_search(doc_type, d_from, d_to, stype='Name/Document', attempts=25):
    """Persistent: keep hammering the captcha until the LP query yields, or the cap is hit."""
    from playwright.sync_api import sync_playwright
    for attempt in range(1, attempts + 1):
        try:
            import records_liens as R
            _ua = getattr(G, 'UA', '') or getattr(R, 'UA', 'Mozilla/5.0')
            _base = getattr(G, 'BASE', '') or R.OR_BASE
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                pg = b.new_context(user_agent=_ua, viewport={'width': 1400, 'height': 1000}).new_page()
                pg.goto(_base, timeout=40000, wait_until='domcontentloaded')
                pg.wait_for_timeout(4000 + attempt * 400)
                res = pg.evaluate(_legacy_search_js(), [doc_type, d_from, d_to, stype])
                b.close()
            if res and res.get('success'):
                print(f'  mint OK on attempt {attempt}: {res.get("count")} records')
                return res
            print(f'  attempt {attempt}: no qs (status {res.get("status") if res else "?"}) {(res or {}).get("raw","")}')
        except Exception as e:
            print(f'  attempt {attempt} threw: {str(e)[:90]}')
        back = min(60, 10 * (1.4 ** min(attempt, 10)))
        print(f'  backing off {int(back)}s...')
        time.sleep(back)
    return None


def _win(days):
    to = datetime.date.today()
    fr = to - datetime.timedelta(days=days)
    return fr.strftime('%Y-%m-%d'), to.strftime('%Y-%m-%d')   # ISO — the ONLY date format the API accepts


def probe():
    d_from, d_to = _win(30)
    print(f'PROBE: LIS PENDENS, {d_from} .. {d_to}, blank name')
    # try the two most likely doc-type spellings + searchtype variants
    for dt in ('LIS PENDENS', 'LIS PENDENS - LIS', 'LIS'):
        for st in ('Name/Document', 'Document'):
            print(f'\n--- documentType={dt!r} searchtype={st!r} ---')
            res = _mint_search(dt, d_from, d_to, st, attempts=6)
            if res and res.get('count'):
                s = res['sample']
                print(f'  >>> {res["count"]} hits. Field keys on a record:')
                print('  ', sorted(s[0].keys()))
                print('  First 5 records:')
                for d in s[:5]:
                    print('    ', d.get('reC_DATE'), '|', d.get('doC_TYPE'), '|', d.get('reC_BOOKPAGE'),
                          '|', (d.get('parties') or '')[:60], '| folio', d.get('foliO_NUMBER'))
                json.dump(res, open(os.path.join(HERE, '_lp_probe.json'), 'w', encoding='utf-8'), indent=1, default=str)
                print('  saved -> _lp_probe.json')
                return res
            print('  (no hits with this combo)')
    print('\nPROBE FAILED — no combo returned LP records. Inspect the raw responses above.')
    return None


def _kind(parties):
    """Tag the filing so the play falls out: bank/lender plaintiff = mortgage FC (buy/wholesale),
    HOA/association = a senior mortgage survives (short-sale/negotiate), other = verify."""
    pu = (parties or '').upper()
    if HOA_RE.search(pu) and not LENDER_RE.search(pu):
        return 'HOA/JUNIOR'
    if LENDER_RE.search(pu):
        return 'BANK-1st'
    return 'OTHER/PRIVATE'


# The handful of plaintiffs that file the bulk of Miami-Dade MORTGAGE foreclosures. The blank-name
# docket sweep returns nothing through getStandardRecords, but a NAME search + ISO date window DOES
# (name searches aren't walled) — so we reconstruct the LP feed by sweeping these and unioning. HOA
# foreclosures are filed by thousands of individual associations and aren't reachable this way; this
# lane is the mortgage foreclosures (the deals with equity), which is what matters.
PLAINTIFFS = [
    'US BANK', 'BANK OF NEW YORK MELLON', 'WELLS FARGO', 'JPMORGAN CHASE', 'DEUTSCHE BANK NATIONAL',
    'WILMINGTON', 'NATIONSTAR', 'LAKEVIEW LOAN', 'PENNYMAC', 'FREEDOM MORTGAGE', 'CARRINGTON MORTGAGE',
    'SELENE FINANCE', 'NEWREZ', 'PHH MORTGAGE', 'RUSHMORE', 'SPECIALIZED LOAN', 'TOWD POINT', 'MTGLQ',
    'FEDERAL NATIONAL MORTGAGE', 'FEDERAL HOME LOAN MORTGAGE', 'SECRETARY OF HOUSING', 'LOANCARE',
    'SHELLPOINT', 'CITIBANK', 'CITIMORTGAGE', 'TRUIST', 'FLAGSTAR', 'MIDFIRST', 'PLANET HOME',
    'REVERSE MORTGAGE', 'ROCKET MORTGAGE', 'AJAX MORTGAGE', 'REGIONS BANK', 'BANK OF AMERICA',
    # Added 2026-09-28 off the bank-first plaintiffs on Broward and Palm Beach LP filings, whose sweeps
    # are whole-doctype and so show which lenders file in South Florida. The Miami list caught 341 of
    # those 615 filings. 'U S BANK' is the largest miss: the recorders index U.S. Bank with a space
    # (46 Broward filings as 'U S BANK ...'), and a 'US BANK' name search found 4 in Miami.
    'U S BANK', 'PNC BANK', 'M&T BANK', 'TD BANK', 'HSBC BANK', 'UMB BANK', 'FIRST CITIZENS BANK',
    'CITY NATIONAL BANK', 'SELECT PORTFOLIO', 'FAY SERVICING', 'ONITY MORTGAGE', 'COMPUTERSHARE',
    'FINANCE OF AMERICA', 'LONGBRIDGE FINANCIAL', 'AMERIHOME MORTGAGE', 'CROSSCOUNTRY MORTGAGE',
    'LOANDEPOT', 'UNITED WHOLESALE MORTGAGE', 'NEW AMERICAN FUNDING', 'PLAZA HOME MORTGAGE',
    '21ST MORTGAGE', 'ONSLOW BAY', 'LOAN FUNDER', 'NAVY FEDERAL CREDIT UNION', 'TROPICAL FINANCIAL',
]


def normalize(rec, lender=''):
    """The LP record carries the real court CASE NUMBER (casE_NUM) + the legal description; foliO_NUMBER
    is 0 on LP filings, so the case number is the key. Homeowner = the party that is NOT a lender (we
    searched by lender, so the lender is one party; the other is the defendant = who to contact)."""
    fp = str(rec.get('firsT_PARTY') or '').strip()
    sp = str(rec.get('seconD_PARTY') or '').strip()
    parties = str(rec.get('parties') or (fp + ' / ' + sp)).strip(' /')
    case = re.sub(r'\s+LISP\w*\s*$', '', str(rec.get('casE_NUM') or rec.get('misC_REF') or '').strip())
    cands = [p for p in (fp, sp) if p]
    owner = next((p for p in cands if not LENDER_RE.search(p.upper())), '')
    plaintiff = next((p for p in cands if LENDER_RE.search(p.upper())), (cands[0] if cands else ''))
    legal = ' '.join(x for x in [
        str(rec.get('subdiV_NAME') or '').strip(),
        str(rec.get('legaL_DESCRIPTION') or '').strip(),
        ('BLK ' + str(rec.get('blocK_NO'))) if rec.get('blocK_NO') else '',
        ('PB ' + str(rec.get('plaT_BOOKPAGE'))) if rec.get('plaT_BOOKPAGE') else ''] if x)
    return {
        'date': str(rec.get('reC_DATE') or '').split(' ')[0],           # '6/8/2026 12:00:00 AM' -> '6/8/2026'
        'case': case, 'docType': (rec.get('doC_TYPE') or 'LIS PENDENS - LIS').strip(),
        'bookpage': rec.get('reC_BOOKPAGE') or '', 'legal': legal,
        'parties': parties, 'plaintiff': plaintiff, 'owner': owner,
        'kind': _kind(parties),
    }


SWEEP_BLOCKED = []        # Miami-Dade plaintiff names whose fetch never got through this run
# Plaintiff names whose search came back with OR_RESULT_CAP or more records. The Miami-Dade Official
# Records search returns at most 500 records, the OLDEST first (records_liens.camoufox_qs, measured on
# owner searches). These name searches carry no document type, so a big lender's 30-day window holds its
# mortgages, assignments and satisfactions too; at the cap the newest filings in the window are the
# ones missing. Counted here so the sweep reports itself partial instead of complete.
SWEEP_CAPPED = []
OR_RESULT_CAP = 500
MD_LAST = {}              # what the last lp_sweep covered, for main()'s coverage record
# SPLITTING A CAPPED SEARCH (2026-10-22, Alejandro: "yes lets go"). The laptop log of 10-21 showed
# WELLS FARGO, JPMORGAN CHASE, CITIBANK and BANK OF AMERICA at exactly 500 records on both runs: the
# search kept their oldest 500 documents of the 30 days and dropped the rest, newest filings included.
# A capped search is now re-run over the two halves of its date window, and again, down to
# MD_MAX_SPLIT levels (30 days -> about two-day windows), the way fl_lp.palmbeach narrows Landmark.
# Each sub-search is one more paid solve under the same paid_reads cap, and a refused solve stops
# the split there: that lender is then reported partial, never complete.
def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MD_MAX_SPLIT = _env_int('DEALFLOW_LP_MAX_SPLIT', 4)
# THE NAME SEARCH TAKES A DOCUMENT TYPE (probe on the laptop, 2026-10-22): WELLS FARGO over 30 days
# with documentType='LIS PENDENS - LIS' returned 9 records, all 9 lis pendens, where the same search
# with no type hit the 500 cap on mortgages and satisfactions and kept 8. Asking for the type keeps a
# lender's window far under the cap, so the split above becomes the rare fallback, not the nightly
# path. DEALFLOW_LP_MD_DOCTYPE='' puts the untyped search back.
MD_LP_DOCTYPE = os.environ.get('DEALFLOW_LP_MD_DOCTYPE', 'LIS PENDENS - LIS').strip()
# A per-run ceiling on split searches, separate from the monthly paid-reads cap (which Alejandro
# lifted on 10-19, so it no longer bounds a night). 60 searches is about $0.20 at the ledger's price;
# a lender the ceiling stops is reported partial, like one the monthly cap stops.
MD_MAX_SPLIT_SEARCHES = _env_int('DEALFLOW_LP_MAX_SPLIT_SEARCHES', 60)


def _split_iso(d_from, d_to):
    """('YYYY-MM-DD', 'YYYY-MM-DD') -> its two halves, or None when it is a single day."""
    try:
        a = datetime.date.fromisoformat(d_from)
        b = datetime.date.fromisoformat(d_to)
    except (TypeError, ValueError):
        return None
    if a >= b:
        return None
    mid = a + datetime.timedelta(days=(b - a).days // 2)
    return ((a.isoformat(), mid.isoformat()),
            ((mid + datetime.timedelta(days=1)).isoformat(), b.isoformat()))


def _lp_rows(recs):
    return [r for r in recs if 'LIS' in (r.get('doC_TYPE') or '').upper()
            and 'CANCEL' not in (r.get('doC_TYPE') or '').upper()]


ATTEMPTS_KEPT = 6          # per lender, in the coverage record


def _or_attempt(R, solve, url):
    """One Official Records search try -> (records or None, what happened).

    What happened is labels and counts only, never a token, key or record: token yes/no, the HTTP
    status, whether the answer carried a result handle (qs), and the answer's keys plus any
    message/error text. It is what tells a lender that "blocks" every night apart: a solve the cap
    refused or 2Captcha failed (token no), a county refusal (http 4xx/5xx), a county answer with no
    result (qs no), or a results read that failed (qs yes, read failed).
    """
    info = {'token': False, 'http': None, 'qs': None, 'read': None, 'answer': ''}
    tok = solve(R.TS_SITE_KEY, R.OR_BASE)
    if not tok:
        info['answer'] = 'no token (paid cap refused or solver failed)'
        return None, info
    info['token'] = True
    try:
        resp = R.S.post(url, headers={'x-recaptcha-token': tok,
                                      'content-type': 'application/json; charset=utf-8'},
                        data='', timeout=35)
    except Exception as e:
        info['answer'] = 'request failed: %s' % type(e).__name__
        return None, info
    info['http'] = getattr(resp, 'status_code', None)
    try:
        j = resp.json()
    except Exception:
        info['answer'] = 'answer was not JSON'
        return None, info
    if not isinstance(j, dict):
        info['answer'] = 'answer was not an object'
        return None, info
    info['qs'] = bool(j.get('qs'))
    # A bool, number or null is shown as itself: isValidSearch true vs false is the whole question
    # for a lender that never gets a result handle. Anything else is reduced to its type name.
    info['answer'] = str({k: (str(v)[:60] if k.lower() in ('message', 'msg', 'error', 'errors')
                              else v if v is None or isinstance(v, (bool, int, float))
                              else type(v).__name__) for k, v in j.items()})[:160]
    if not info['qs']:
        return None, info
    recs = R.records_by_qs(j['qs'])
    info['read'] = recs is not None
    return recs, info


def lp_sweep(days=30, tries=3):
    """Fresh LIS PENDENS from Miami-Dade Official Records WITHOUT the walled docket sweep: name-search
    each major foreclosure plaintiff over an ISO date window and keep the LIS PENDENS docs, unioned +
    deduped. The front of the funnel — the owner the day their case is filed, months before the crowd."""
    import urllib.parse
    from captcha_solver import solve_turnstile as _solve
    import paid_reads
    import records_liens as R
    # Every solve here is a paid 2Captcha read: checked against the shared monthly cap and counted
    # before it is submitted (paid_reads.py). A refused solve returns None, like any failed one.
    solve_turnstile = paid_reads.guarded(_solve, 'lis_pendens')
    d_from, d_to = _win(days)
    print(f'LIS PENDENS lender-sweep: {d_from} .. {d_to} across {len(PLAINTIFFS)} plaintiffs')
    MD_LAST.clear()
    MD_LAST.update({'window': [d_from, d_to], 'searched': 0, 'retrieved': 0,
                    'split_searches': 0, 'no_qs': {}, 'attempts': {}, 'doctype': MD_LP_DOCTYPE})
    del SWEEP_CAPPED[:]

    def search(name, lo, hi):
        """One name search over [lo, hi] -> its records, or None when it never got through."""
        url = (R.OR_BASE + 'api/home/standardsearch?partyName=' + urllib.parse.quote(name)
               + '&dateRangeFrom=' + urllib.parse.quote(lo) + '&dateRangeTo=' + urllib.parse.quote(hi)
               + '&documentType=' + urllib.parse.quote(MD_LP_DOCTYPE)
               + '&searchT=&firstQuery=y&searchtype=' + urllib.parse.quote('Name/Document'))
        for _ in range(tries):
            recs, info = _or_attempt(R, solve_turnstile, url)
            tried = MD_LAST['attempts'].setdefault(name, [])
            if len(tried) < ATTEMPTS_KEPT:
                tried.append(info)
            if recs is not None:
                return recs
            if info['qs'] is False:
                MD_LAST['no_qs'].setdefault(name, info['answer'])
                time.sleep(1)
        return None

    def window(name, lo, hi, depth):
        """-> (records, complete). Splits a capped window; None when the top window never ran."""
        recs = search(name, lo, hi)
        if recs is None:
            return None, False
        if len(recs) < OR_RESULT_CAP:
            return recs, True
        halves = _split_iso(lo, hi) if depth < MD_MAX_SPLIT else None
        if not halves:
            return recs, False                     # capped, and cannot be narrowed further
        got, complete = list(recs), True           # the capped page is real rows: keep it too
        for a, b in halves:
            if (MD_LAST['split_searches'] >= MD_MAX_SPLIT_SEARCHES
                    or not paid_reads.allow(paid_reads.SOLVE_USD, 'lis_pendens')[0]):
                complete = False
                break
            MD_LAST['split_searches'] += 1
            part, ok = window(name, a, b, depth + 1)
            if part is None:
                complete = False
                continue
            got += part
            complete = complete and ok
        return got, complete

    out = {}
    for i, name in enumerate(PLAINTIFFS, 1):
        if not paid_reads.allow(paid_reads.SOLVE_USD, 'lis_pendens')[0]:
            # FAIL CLOSED, and say so: the names not searched count as blocked, so main() reports
            # Miami-Dade failed/partial instead of a clean sweep over a window it never looked at.
            SWEEP_BLOCKED.extend(PLAINTIFFS[i - 1:])
            print(f'  monthly paid-reads cap: {len(PLAINTIFFS) - i + 1} plaintiff name(s) not searched '
                  f'this run (Miami-Dade sweep stops here; the rest resume when the cap allows)')
            break
        before = MD_LAST['split_searches']
        recs, complete = window(name, d_from, d_to, 0)
        if recs is None:
            SWEEP_BLOCKED.append(name)
            print(f'  [{i}/{len(PLAINTIFFS)}] {name:26} (blocked)'); continue
        # the capped page and the narrower windows overlap: count each record once
        recs = list({json.dumps(r, sort_keys=True, default=str): r for r in recs}.values())
        MD_LAST['searched'] += 1
        MD_LAST['retrieved'] += len(recs)
        splits = MD_LAST['split_searches'] - before
        if not complete:
            SWEEP_CAPPED.append(name)
            print(f'  [{i}/{len(PLAINTIFFS)}] {name:26} NOT READ TO THE END: still at the '
                  f'{OR_RESULT_CAP}-record cap or a narrower window did not run '
                  f'({splits} split search(es)); the newest filings may be missing')
        kept = 0
        for r in _lp_rows(recs):
            n = normalize(r, name)
            if not n['owner']:                     # both parties lenders = assignment/subrogation noise
                continue
            out.setdefault(n['case'] or n['bookpage'] or n['parties'][:50], n)
            kept += 1
        print(f'  [{i}/{len(PLAINTIFFS)}] {name:26} {len(recs)} recs -> {kept} homeowner LP'
              + (f' ({splits} split search(es))' if splits else ''))
    return list(out.values())


def _yn(v):
    return '-' if v is None else ('yes' if v else 'no')


def probe_doctype(name, days=30, doc_types=('LIS PENDENS - LIS', 'LIS PENDENS')):
    """Does the name search honour a document type? (2026-10-22, decision card: "probe doctype too")

    If it does, a lender's search returns only its lis pendens and the 500-record cap stops mattering,
    with no split searches. One paid solve per spelling tried (two at most, three tries each), under
    the same paid_reads cap. Prints counts and document types only, never a party name.
    """
    import collections
    import urllib.parse
    from captcha_solver import solve_turnstile as _solve
    import paid_reads
    import records_liens as R
    solve = paid_reads.guarded(_solve, 'lis_pendens')
    d_from, d_to = _win(days)
    for dt in doc_types:
        url = (R.OR_BASE + 'api/home/standardsearch?partyName=' + urllib.parse.quote(name)
               + '&dateRangeFrom=' + urllib.parse.quote(d_from) + '&dateRangeTo=' + urllib.parse.quote(d_to)
               + '&documentType=' + urllib.parse.quote(dt)
               + '&searchT=&firstQuery=y&searchtype=' + urllib.parse.quote('Name/Document'))
        recs = None
        for _ in range(3):
            recs, info = _or_attempt(R, solve, url)
            print('  try: token %s, http %s, qs %s, read %s, answer %s'
                  % (_yn(info['token']), info['http'], _yn(info['qs']), _yn(info['read']), info['answer']))
            if recs is not None:
                break
        if recs is None:
            print(f'documentType={dt!r}: no result after 3 tries')
            continue
        kinds = collections.Counter((r.get('doC_TYPE') or '?').strip() for r in recs)
        lp = sum(v for k, v in kinds.items() if 'LIS' in k.upper())
        print(f'documentType={dt!r}: {len(recs)} records, {lp} lis pendens; types {dict(kinds.most_common(6))}')
        print('  FILTER WORKS' if recs and lp == len(recs) else
              '  filter ignored (other document types came back)' if recs else
              '  empty: inconclusive')
        if recs and lp == len(recs):
            return


# A PARTIAL sweep is not a failure of the run, but it is not a success either: some counties
# produced and some did not. lp_refresh treats 4 as benign-and-continue (its `ok` tuple) so the
# board still rebuilds, while run_report and the status file can say which lane is missing.
# 3 = nothing got through anywhere, which stops the chain.
EXIT_PARTIAL = 4


def sweep_verdict(status):
    """{county: 'ok'|'partial'|'failed'} -> (counties that ran, counties that failed).

    An empty list from a sweep that RAN is a verified-empty window and counts as ok; None from a
    blocked sweep is a failure. Collapsing the two is what let a fully blocked morning exit 0
    (audit 2026-09-21, defect 6). A 'partial' county ran and its rows merge, so it is in the first
    list; partial_counties() names it separately so it is never read as complete.
    """
    counties = {k: v for k, v in (status or {}).items() if not k.endswith('_blocked_plaintiffs')}
    return (sorted(k for k, v in counties.items() if v in ('ok', 'partial')),
            sorted(k for k, v in counties.items() if v == 'failed'))


def partial_counties(status):
    """Counties that ran but did not read their whole window."""
    return sorted(k for k, v in (status or {}).items()
                  if not k.endswith('_blocked_plaintiffs') and v == 'partial')


STATUS_FILE = os.path.join(HERE, 'lp_sweep_status.json')


def coverage_entry(status, info, prev=None, now=None):
    """One county's completeness record for lp_sweep_status.json (2026-10-22).

    "The sweep ran" and "the sweep read the whole window" are different facts, and only the second
    makes the rows a complete count. This keeps both: the window asked for, what the source said it
    matched (None when it gives no total, which is Miami-Dade's plaintiff search), what was pulled,
    what failed, and when the county last had a complete read. `last_complete` carries forward from
    the previous status file, so a run of partial nights shows how long it has been.
    """
    now = now or datetime.datetime.now().isoformat(timespec='seconds')
    info = info or {}
    e = {'status': status,
         'scope': info.get('scope', 'whole document type'),
         'window': list(info.get('window') or []),
         'reported_total': info.get('reported_total'),
         'retrieved': info.get('retrieved', 0),
         'kept': info.get('kept', 0),
         'pages': info.get('pages', 0),
         'failures': [str(f)[:120] for f in (info.get('failures') or [])][:20]}
    for k in ('searched', 'blocked', 'capped', 'split_searches', 'no_qs', 'attempts'):
        if k in info:
            e[k] = info[k]
    e['last_complete'] = now if status == 'ok' else (prev or {}).get('last_complete')
    return e


def _prev_coverage():
    try:
        with open(STATUS_FILE, encoding='utf-8') as f:
            c = json.load(f).get('coverage') or {}
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


# PALM BEACH CASE NUMBERS (2026-10-15). Landmark's grid sometimes returns the literal word "CASE" in
# CaseNumber; the real number is in the legal text ("Case Number: Case Number: 502026CA009528XXXAMB").
# 373 of 414 Palm Beach rows on file carried "CASE", and lp_leads dedupes on the case number, so
# all but ONE of them were dropped from the board, and lp_resolve/lp_status keyed them all to the
# same "CASE" entry. Only a circuit-civil (CA) or county-civil (CC) number is recovered: those are
# the foreclosure and HOA cases. A family, probate or truncated number stays unrecovered ('').
# Also '502026CA008606' with no suffix and '2026CA8204' with no 50 prefix or zero padding (review
# 2026-10-15): written back in the canonical 50YYYYTTnnnnnn form, suffix kept when there is one.
_PB_CASE_RE = re.compile(r'(?<![0-9A-Z])(?:50)?(\d{4})(CA|CC)(\d{4,6})([A-Z]{3,6})?(?![0-9])')


def pb_case(case, legal=''):
    """The Palm Beach case number for a row: `case` when it has a number in it, else the CA/CC
    number in the legal text, else ''."""
    c = str(case or '').strip()
    if len(re.findall(r'\d', c)) >= 5:
        return c
    m = _PB_CASE_RE.search(str(legal or '').upper())
    if not m:
        return ''
    year, kind, num, suffix = m.groups()
    if not ('19' <= year[:2] <= '20'):
        return ''
    if len(num) < 6 and num.startswith('0'):
        return ''                    # '502026CA0094' is a cut-off six-digit number, not case 94
    return '50%s%s%s%s' % (year, kind, num.zfill(6), suffix or '')


def _repair_pb_cases(rows):
    """Fix stored Palm Beach rows whose case is a label, in place. Returns how many changed."""
    n = 0
    for x in rows:
        if isinstance(x, dict) and x.get('county') == 'PALM BEACH':
            fixed = pb_case(x.get('case'), x.get('legal'))
            if fixed != str(x.get('case') or ''):
                x['case'] = fixed
                n += 1
    return n


def _maybe_broward_or_feed(env=None):
    """Build the free Official Records lead list when DEALFLOW_BROWARD_OR_FEED is on.

    Returns None on purpose. The list is written under DEALFLOW_DIR and must not
    be appended to the sweep output: Broward outreach stays on today's rules,
    and this file is unchanged when the flag is off (the default).
    """
    env = os.environ if env is None else env
    # Checked before the import. The default (off) path must not load the feed
    # module, so a problem in that file cannot stop the AcclaimWeb sweep.
    flag = str(env.get('DEALFLOW_BROWARD_OR_FEED') or '').strip().lower()
    if flag not in ('1', 'true', 'yes', 'on'):
        return None
    import broward_or_feed as feed
    feed.run_safe(env)
    return None


def _merge_key(x):
    """(county, instrument-or-case) — the cross-county dedupe key. MD rows predate the county
    field, so absence means MIAMI-DADE; MD rows also predate `instrument`, so case/bookpage
    stays their identity."""
    return ((x.get('county') or 'MIAMI-DADE'),
            x.get('instrument') or x.get('case') or x.get('bookpage') or str(x.get('parties'))[:50])


def write_status(a, status, info, new_rows, failed, partial, now=None):
    """lp_sweep_status.json: the run's verdict plus a per-county coverage record. Counts only."""
    now = now or datetime.datetime.now().isoformat(timespec='seconds')
    try:
        prev = _prev_coverage()
        coverage = dict(prev)             # a county not swept tonight keeps its last record
        for c, st in status.items():
            if not c.endswith('_blocked_plaintiffs'):
                old = prev.get(c)
                coverage[c] = coverage_entry(st, info.get(c), old if isinstance(old, dict) else None, now)
    except Exception as e:                # a status file must never stop the merge
        print(f'coverage record not built: {e}')
        coverage = {}
    try:
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'ran': now, 'days': a.days, 'requested': a.county, 'counties': status,
                       'new_rows': new_rows, 'failed': failed, 'partial': partial,
                       'coverage': coverage}, f, indent=1)
    except Exception as e:
        print(f'sweep status not written: {e}')
    for c in partial:
        cv = coverage.get(c)
        if not cv:
            continue
        tot = cv['reported_total'] if cv['reported_total'] is not None else '?'
        print(f'{c}: PARTIAL — {cv["retrieved"]} of {tot} source rows read; '
              + '; '.join(cv['failures'][:3]))
    return coverage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='(legacy reCAPTCHA-v3 browser probe)')
    ap.add_argument('--probe-types', metavar='A,B',
                    help='with --probe-doctype: document types to try, comma-separated; an empty item '
                         'is the untyped search (e.g. "LIS PENDENS - LIS,")')
    ap.add_argument('--probe-doctype', metavar='NAME',
                    help='one paid name search with documentType=LIS PENDENS; prints counts only')
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--county', default='all',
                    choices=['miami-dade', 'broward', 'palm-beach', 'all'],
                    help="which recorder(s) to sweep. Default 'all' ON PURPOSE: the nightly bat and "
                         "refresh.yml call this script bare, and a miami-dade default silently made "
                         "them single-county. Down/blocked counties skip themselves (sweep()->None).")
    a = ap.parse_args()
    if a.probe:
        probe(); return
    if a.probe_doctype:
        if a.probe_types is not None:
            probe_doctype(a.probe_doctype, a.days, tuple(t.strip() for t in a.probe_types.split(',')))
        else:
            probe_doctype(a.probe_doctype, a.days)
        return
    # PER-COUNTY VERDICT (audit 2026-09-21, defect 6). "Found nothing" and "never got through"
    # are opposite facts and this script used to return 0 for both, so a morning where every
    # captcha was refused reported a clean sweep and the wrapper carried on over yesterday's
    # data. A county is 'ok' only when its sweep actually ran -- an EMPTY list from a sweep that
    # ran is a verified-empty window and stays ok; None means blocked.
    out = []
    status = {}
    info = {}
    if a.county in ('miami-dade', 'all'):
        md = lp_sweep(days=a.days) or []
        out += md
        # MD has no single blocked signal: it sweeps a list of plaintiffs. All of them refused
        # and nothing found = we learned nothing, whatever the empty list looks like. Some refused,
        # or some at the record cap = it ran, but over less than the window (2026-10-22).
        if SWEEP_BLOCKED and not md:
            status['MIAMI-DADE'] = 'failed'
        elif SWEEP_BLOCKED or SWEEP_CAPPED:
            status['MIAMI-DADE'] = 'partial'
        else:
            status['MIAMI-DADE'] = 'ok'
        if SWEEP_BLOCKED:
            status['MIAMI-DADE_blocked_plaintiffs'] = len(SWEEP_BLOCKED)
        fails = []
        if SWEEP_BLOCKED:
            fails.append('%d plaintiff name(s) not searched' % len(SWEEP_BLOCKED))
        if SWEEP_CAPPED:
            fails.append('%d plaintiff search(es) not read to the end (%d-record cap)'
                         % (len(SWEEP_CAPPED), OR_RESULT_CAP))
        info['MIAMI-DADE'] = {
            'scope': 'listed plaintiffs only (%d names), document type %r'
                     % (len(PLAINTIFFS), MD_LP_DOCTYPE or 'any'),
            'window': list(MD_LAST.get('window') or _win(a.days)),
            'reported_total': None, 'retrieved': MD_LAST.get('retrieved', 0), 'kept': len(md),
            'pages': MD_LAST.get('searched', 0), 'searched': MD_LAST.get('searched', 0),
            'blocked': len(SWEEP_BLOCKED), 'capped': list(SWEEP_CAPPED), 'failures': fails,
            'split_searches': MD_LAST.get('split_searches', 0),
            'no_qs': dict(MD_LAST.get('no_qs') or {}),
            # per-try detail for the lenders that never got through: labels only, no token or record
            'attempts': {n: list((MD_LAST.get('attempts') or {}).get(n) or []) for n in SWEEP_BLOCKED}}
    if a.county in ('broward', 'all'):
        from fl_lp import broward as _bw
        bw = _bw.sweep(days=a.days)
        if bw is None:
            status['BROWARD'] = 'failed'
            print('BROWARD sweep blocked — other counties (if any) still merge.')
        else:
            status['BROWARD'] = _bw.LAST.get('status') or 'partial'
            out += bw
        info['BROWARD'] = dict(_bw.LAST, kept=len(bw or []))
        # Separate list. Off unless DEALFLOW_BROWARD_OR_FEED=1. Return value is
        # None, so these rows cannot join `out` or the published board.
        _maybe_broward_or_feed()
    if a.county in ('palm-beach', 'all'):
        from fl_lp import palmbeach as _pb
        pb = _pb.sweep(days=a.days)
        if pb is None:
            status['PALM BEACH'] = 'failed'
            print('PALM BEACH sweep blocked/portal down — other counties (if any) still merge.')
        else:
            status['PALM BEACH'] = _pb.LAST.get('status') or 'partial'
            out += pb
        info['PALM BEACH'] = dict(_pb.LAST, kept=len(pb or []))

    ran, failed = sweep_verdict(status)
    partial = partial_counties(status)
    write_status(a, status, info, len(out), failed, partial)

    if not ran:
        # nothing got through anywhere. The existing file is untouched and still valid data --
        # this is a FAILED REFRESH, not an empty county, and the chain must hear about it.
        print('\nno LP sweep completed — every source blocked. lis_pendens.json left as it was.')
        sys.exit(3)
    prev = []
    try:
        prev = json.load(open(OUT, encoding='utf-8'))
    except Exception:
        pass
    repaired = _repair_pb_cases(prev)
    if repaired:
        print(f'repaired {repaired} Palm Beach case number(s) read from the legal text')
    if not out:
        if repaired:
            json.dump(prev, open(OUT, 'w', encoding='utf-8'), indent=1)
        print('\nsweep ran, no new filings in the window.'
              + (f' FAILED: {", ".join(failed)}' if failed else '')
              + (f' PARTIAL: {", ".join(partial)}' if partial else ''))
        sys.exit(EXIT_PARTIAL if failed else 0)
    # MERGE, never overwrite (2026-08-11). The sweep only sees its own date window; the file
    # holds every filing being worked. Overwriting made a 30-day sweep silently delete every
    # older LP lead — the resolved, valued, on-the-board ones included. Union by the same
    # dedupe key the sweep uses; existing rows win so downstream enrichment is never clobbered.
    merged = {}
    for x in prev:
        merged[_merge_key(x)] = x
    fresh = 0
    for x in out:
        k = _merge_key(x)
        if k not in merged:
            merged[k] = x
            fresh += 1
    out = list(merged.values())
    from collections import Counter
    kinds = Counter(x['kind'] for x in out)
    out.sort(key=lambda x: str(x.get('date') or ''), reverse=True)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
    print(f"\nDONE: {fresh} NEW filing(s) this sweep, {len(out)} total ({dict(kinds)}) -> lis_pendens.json")
    print("Front of the funnel — the owner the day their foreclosure was filed. Board play = LP-EARLY (be first).")
    if failed:
        print(f'\nPARTIAL SWEEP: {", ".join(failed)} did not run. The rows above are from '
              f'{", ".join(ran)} only; the missing counties keep whatever was already on file.')
    if partial:
        print(f'\nINCOMPLETE WINDOW: {", ".join(partial)} ran but did not read the whole window. '
              f'Their rows merged; lp_sweep_status.json says what is missing.')
    # The exit code stays about counties that did not run. A partial county is named in the status
    # file, the coverage record and run_report, not here: Miami can be partial most nights (a big
    # lender at the record cap), and a standing exit 4 would set the refresh's RUNEXIT to 7 every
    # night, and an earlier code wins there, so it would hide a later 5 (mirror not published).
    if failed:
        sys.exit(EXIT_PARTIAL)


if __name__ == '__main__':
    main()
