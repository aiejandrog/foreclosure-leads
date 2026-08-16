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


def lp_sweep(days=30, tries=3):
    """Fresh LIS PENDENS from Miami-Dade Official Records WITHOUT the walled docket sweep: name-search
    each major foreclosure plaintiff over an ISO date window and keep the LIS PENDENS docs, unioned +
    deduped. The front of the funnel — the owner the day their case is filed, months before the crowd."""
    import urllib.parse
    from captcha_solver import solve_turnstile
    import records_liens as R
    d_from, d_to = _win(days)
    print(f'LIS PENDENS lender-sweep: {d_from} .. {d_to} across {len(PLAINTIFFS)} plaintiffs')
    out = {}
    for i, name in enumerate(PLAINTIFFS, 1):
        url = (R.OR_BASE + 'api/home/standardsearch?partyName=' + urllib.parse.quote(name)
               + '&dateRangeFrom=' + urllib.parse.quote(d_from) + '&dateRangeTo=' + urllib.parse.quote(d_to)
               + '&documentType=&searchT=&firstQuery=y&searchtype=' + urllib.parse.quote('Name/Document'))
        recs = None
        for _ in range(tries):
            tok = solve_turnstile(R.TS_SITE_KEY, R.OR_BASE)
            if not tok:
                continue
            try:
                j = R.S.post(url, headers={'x-recaptcha-token': tok,
                                           'content-type': 'application/json; charset=utf-8'},
                             data='', timeout=35).json()
            except Exception:
                continue
            if j.get('qs'):
                recs = R.records_by_qs(j['qs']) or []
                break
            time.sleep(1)
        if recs is None:
            print(f'  [{i}/{len(PLAINTIFFS)}] {name:26} (blocked)'); continue
        lp = [r for r in recs if 'LIS' in (r.get('doC_TYPE') or '').upper()
              and 'CANCEL' not in (r.get('doC_TYPE') or '').upper()]
        kept = 0
        for r in lp:
            n = normalize(r, name)
            if not n['owner']:                     # both parties lenders = assignment/subrogation noise
                continue
            out.setdefault(n['case'] or n['bookpage'] or n['parties'][:50], n)
            kept += 1
        print(f'  [{i}/{len(PLAINTIFFS)}] {name:26} {len(recs)} recs -> {kept} homeowner LP')
    return list(out.values())


def _merge_key(x):
    """(county, instrument-or-case) — the cross-county dedupe key. MD rows predate the county
    field, so absence means MIAMI-DADE; MD rows also predate `instrument`, so case/bookpage
    stays their identity."""
    return ((x.get('county') or 'MIAMI-DADE'),
            x.get('instrument') or x.get('case') or x.get('bookpage') or str(x.get('parties'))[:50])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='(legacy reCAPTCHA-v3 browser probe)')
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--county', default='all',
                    choices=['miami-dade', 'broward', 'palm-beach', 'all'],
                    help="which recorder(s) to sweep. Default 'all' ON PURPOSE: the nightly bat and "
                         "refresh.yml call this script bare, and a miami-dade default silently made "
                         "them single-county. Down/blocked counties skip themselves (sweep()->None).")
    a = ap.parse_args()
    if a.probe:
        probe(); return
    out = []
    if a.county in ('miami-dade', 'all'):
        out += lp_sweep(days=a.days) or []
    if a.county in ('broward', 'all'):
        from fl_lp import broward as _bw
        bw = _bw.sweep(days=a.days)
        if bw is None:
            print('BROWARD sweep blocked — other counties (if any) still merge.')
        else:
            out += bw
    if a.county in ('palm-beach', 'all'):
        from fl_lp import palmbeach as _pb
        pb = _pb.sweep(days=a.days)
        if pb is None:
            print('PALM BEACH sweep blocked/portal down — other counties (if any) still merge.')
        else:
            out += pb
    if not out:
        print('\nno LP filings — every search blocked (captcha) or empty window. Retry.'); return
    # MERGE, never overwrite (2026-08-11). The sweep only sees its own date window; the file
    # holds every filing being worked. Overwriting made a 30-day sweep silently delete every
    # older LP lead — the resolved, valued, on-the-board ones included. Union by the same
    # dedupe key the sweep uses; existing rows win so downstream enrichment is never clobbered.
    prev = []
    try:
        prev = json.load(open(OUT, encoding='utf-8'))
    except Exception:
        pass
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


if __name__ == '__main__':
    main()
