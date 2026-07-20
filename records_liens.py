"""Pillar 1 — automate the recorded mortgage/lien pull so the equity number stops being a guess.

For each lead's owner, pull the Miami-Dade Official Records chain, match SATISFACTION docs to their
MORTGAGE, and surface the OPEN (unsatisfied) mortgages on the subject folio — i.e. the hidden 2nd that
made Hondroulis's "$655k equity" a fantasy. Output -> records_liens.json (gitignored), keyed by Case #.

Reliability trick: the reCAPTCHA-gated part is only the *search* (standardsearch POST). The RESULTS fetch
(getStandardRecords GET) is NOT gated. gen_records_qs.py already cached a valid search token (qs) per owner
in records_qs.json (158 owners). So for a cached owner we pull the chain with PLAIN REQUESTS — fast, no
bot-wall. Uncached owners fall back to a Playwright token-mint (best-effort; the county walls it ~half the
time headless).

Usage:
  python records_liens.py --case 2024-023366-CA-01     # one lead (prove it)
  python records_liens.py --tier A                      # a tier
  python records_liens.py --all --cached-only           # everyone we already have a token for (fast, no browser)
  python records_liens.py --all                         # everyone; mint tokens for the rest (slow, flaky)
"""
import argparse, json, os, re, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
QS_CACHE = os.path.join(HERE, 'records_qs.json')      # owner_clean -> search token (from gen_records_qs.py)
OUT = os.path.join(HERE, 'records_liens.json')         # Case # -> lien result  (gitignored)
OR_BASE = 'https://onlineservices.miamidadeclerk.gov/officialrecords/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|USA|UNITED STATES|COUNTY|CITY OF)\b', re.I)
SITE_KEY = '6LfI8ikaAAAAAH0qlQMApskMGd1U6EqDyniH5t0x'

S = requests.Session()
S.headers.update({'User-Agent': UA, 'Accept': 'application/json', 'Referer': OR_BASE})


def norm_folio(s):
    return re.sub(r'\D', '', str(s or '')).lstrip('0')

def num(x):
    try: return float(x or 0)
    except Exception: return 0

def split_owner(clean):
    toks = [t for t in (clean or '').split() if len(t.strip('.')) > 1]
    return (' '.join(toks[1:]), toks[0]) if len(toks) >= 2 else None   # (LAST..., FIRST)


# ---- fetch the owner's recorded documents -----------------------------------------------------
def records_by_qs(qs):
    try:
        r = S.get(OR_BASE + 'api/SearchResults/getStandardRecords?qs=' + qs, timeout=30)
        if r.status_code != 200:
            return None
        return (r.json() or {}).get('recordingModels') or []
    except Exception:
        return None

def mint_and_fetch(owner_lf, budget=70):
    """Fallback: mint a fresh reCAPTCHA token in a browser, then fetch. Best-effort."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    src = open(os.path.join(HERE, 'gen_records_qs.py'), encoding='utf-8').read()
    js = re.search(r'JS = r"""(.*?)"""', src, re.S).group(1).replace('SITEKEY', SITE_KEY)
    t0 = time.time()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_context(user_agent=UA, viewport={'width': 1400, 'height': 1000}).new_page()
            res = None
            for _ in range(3):
                if time.time() - t0 > budget: break
                try:
                    pg.goto(OR_BASE, timeout=40000, wait_until='domcontentloaded'); pg.wait_for_timeout(3000)
                    res = pg.evaluate(js, list(owner_lf))
                    if res and res.get('success') and res.get('qs'): break
                except Exception:
                    pg.wait_for_timeout(2500)
            b.close()
        if res and res.get('success') and res.get('qs'):
            return records_by_qs(res['qs'])
    except Exception:
        pass
    return None


# ---- parse the chain: open vs satisfied, isolate the surviving junior --------------------------
def _fc_type(case):
    """HOA/county-court (whole 1st mortgage survives) vs circuit mortgage foreclosure. Miami-Dade case format
    uses -CA- (circuit) / -CC- (county); also handle the Broward-style CACE/COCE prefixes defensively."""
    c = (case or '').upper()
    if '-CA-' in c or c.startswith('CACE'): return 'MORTGAGE'
    if '-CC-' in c or c.startswith(('COCE', 'CONO', 'COWE', 'COSO')): return 'HOA'
    return ''


def analyze(models, folio, judgment, ftype=''):
    """Open-mortgage picture for the SUBJECT parcel only. Precision > recall: without a folio to isolate
    by, we return nothing rather than risk a namesake's mortgages polluting the number.
    ftype='HOA' means the whole first mortgage survives the sale (surface `surv`), not just a 2nd."""
    fol = norm_folio(folio)
    if not fol:
        return {'liens': [], 'open_count': 0, 'junior': 0, 'first_est': 0, 'surv': 0, 'surv_first': 0,
                'ftype': ftype, 'conf': 'none'}
    # ANCHOR the subject's subdivision from a record that DOES carry the subject folio (usually the deed).
    # folio is blank on most newer mortgages, but subdivision is consistent — so subdivision + owner-name
    # isolates the property, while folio alone would drop the very mortgages we need.
    subj_subdiv = ''
    for r in models:
        if norm_folio(r.get('foliO_NUMBER', '')) == fol:
            sd = (r.get('subdiV_NAME', '') or '').strip().upper()
            if sd: subj_subdiv = sd; break
    # a MORTGAGE is satisfied if a SATISFACTION points at its book/page
    satisfied = set()
    for r in models:
        if 'SATISFACTION' in (r.get('doC_TYPE', '') or '').upper():
            satisfied.add((str(r.get('oriG_REC_BOOK', '')).strip(), str(r.get('oriG_REC_PAGE', '')).strip()))
    def sortkey(r):
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', (r.get('reC_DATE', '') or '').strip())
        return (m.group(3), m.group(1).zfill(2), m.group(2).zfill(2)) if m else ('0000', '00', '00')
    liens, opens = [], []
    for r in sorted(models, key=sortkey):
        if not (r.get('doC_TYPE', '') or '').upper().startswith('MORTGAGE'):
            continue
        rf = norm_folio(r.get('foliO_NUMBER', ''))
        sd = (r.get('subdiV_NAME', '') or '').strip().upper()
        if not ((rf and rf == fol) or (subj_subdiv and sd == subj_subdiv)):
            continue                                   # subject parcel only (folio when present, else subdivision)
        it, cons = num(r.get('intangible')), num(r.get('consideratioN_1'))
        amt = round(it / 0.002) if it > 0 else round(cons)
        if amt <= 0:
            continue                                   # $0 doc = modification/piggyback placeholder, not a real balance
        bp = (str(r.get('reC_BOOK', '')).strip(), str(r.get('reC_PAGE', '')).strip())
        is_open = bp not in satisfied
        row = {'d': (r.get('reC_DATE', '') or '')[:10], 'amt': amt, 'party': (r.get('seconD_PARTY', '') or '')[:40],
               'bp': r.get('reC_BOOKPAGE', ''), 'st': 'OPEN' if is_open else 'SATISFIED'}
        liens.append(row)
        if is_open:
            opens.append(row)
    junior = first_amt = surv = surv_first = 0
    juniors_post = 0
    if opens:
        if ftype == 'HOA':                             # HOA sale: the WHOLE first mortgage survives
            surv = sum(o['amt'] for o in opens)
            surv_first = max(o['amt'] for o in opens)
        else:
            anchor = (lambda o: abs(o['amt'] - judgment)) if (judgment and judgment > 0) else (lambda o: -o['amt'])
            fore = min(opens, key=anchor)              # the foreclosing 1st (closest to judgment, else largest)
            first_amt = fore['amt']
            junior = surv = sum(o['amt'] for o in opens if o is not fore)
            juniors_post = sum(o['amt'] for o in opens if o is not fore and o['d'] >= fore['d'])
    # --- open non-mortgage liens (kimi: feeds the deal-modal HOA / code / IRS prefills) ------------
    # Lien/Judgment/Notice records, bucketed by holder. code+HOA require the same parcel isolation the
    # mortgages use (folio/subdivision); IRS + money judgments attach to the person and ride anyway.
    _IRS_RE = re.compile(r'INTERNAL\s+REV|UNITED\s+STATES|\bIRS\b', re.I)
    _CODE_RE = re.compile(r'\bCITY\s+OF\b|\bCOUNTY\b|CODE\s+ENFORCEMENT|MUNICIPAL|MIAMI-?DADE|STATE OF FLORIDA|PACE|CLEAN ENERGY', re.I)
    _HOA_DOC_RE = re.compile(r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|\bMASTER\b|\bVILLAS?\b|COMMUNITY|PROPERTY\s+OWNERS?|TOWNHO|MAINTENANCE', re.I)
    _ASSN_DOC_RE = re.compile(r'(?<!NATIONAL\s)\bASS(?:N|OC(?:IATION)?)\b', re.I)
    _LIEN_DOC_RE = re.compile(r'^(LIEN|JUDGMENT|NOTICE|CLAIM|CERT|FINANCING STATEMENT)', re.I)
    def _norm_party(s):
        s = (s or '').upper()
        s = re.sub(r'\b(NA|N A|INC|CORP|CO|LLC|LP|USA|TRUST|COMPANY|OF|THE|AND|ASSN|ASSOC|ASSOCIATION)\b', '', s)
        return re.sub(r'[^A-Z]', '', s)
    sats_parties = {_norm_party(r.get('seconD_PARTY')) for r in models if 'SATISFACTION' in (r.get('doC_TYPE', '') or '').upper()
                    or 'RELEASE' in (r.get('doC_TYPE', '') or '').upper()}
    hoa_open = code_open = irs_open = 0
    for r in models:
        if not _LIEN_DOC_RE.match((r.get('doC_TYPE', '') or '').upper().strip()):
            continue
        amt = num(r.get('consideratioN_1'))
        if amt <= 0:
            continue
        party = r.get('seconD_PARTY') or ''
        rf = norm_folio(r.get('foliO_NUMBER', ''))
        sd = (r.get('subdiV_NAME') or '').strip().upper()
        on_parcel = bool((rf and rf == fol) or (subj_subdiv and sd == subj_subdiv))
        holder = _norm_party(party)
        if holder and holder in sats_parties:
            continue                                          # released by a same-party satisfaction
        if _IRS_RE.search(party):
            irs_open += amt                                 # person-wide, attaches regardless
        elif _HOA_DOC_RE.search(party) or _ASSN_DOC_RE.search(party):
            if on_parcel: hoa_open += amt
        elif _CODE_RE.search(party):
            if on_parcel: code_open += amt
        elif 'JUDGMENT' in (r.get('doC_TYPE', '') or '').upper():
            code_open += amt                                # debt-buyer money judgments ride as surviving liens
    # confidence: we must have isolated by a real anchor, sane count, and not a common-name over-match
    conf = 'ok'
    if not subj_subdiv: conf = 'low'                   # couldn't anchor the property (no folio-carrying record)
    if len(opens) > 4: conf = 'low'                    # one parcel rarely has >4 live mortgages
    if len(models) > 45: conf = 'low'                  # busy/common name -> results unreliable
    return {'liens': liens, 'open_count': len(opens), 'junior': junior, 'first_est': first_amt,
            'surv': surv, 'surv_first': surv_first, 'juniors_post': juniors_post,
            'hoa_open': hoa_open, 'code_open': code_open, 'irs_open': irs_open,
            'ftype': ftype, 'conf': conf, 'subdiv': subj_subdiv}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', default='')
    ap.add_argument('--tier', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--cached-only', action='store_true', help="only owners with a cached search token (fast, no browser)")
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    leads = json.load(open(LEADS, encoding='utf-8'))
    qs_cache = json.load(open(QS_CACHE, encoding='utf-8')) if os.path.exists(QS_CACHE) else {}
    out = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}

    picked = []
    for r in leads:
        case = r.get('Case #', '') or ''
        if a.case and case != a.case: continue
        if a.tier and (r.get('tier', '') or '') != a.tier: continue
        oc = (r.get('owner_clean', '') or '').strip()
        if not oc or COMPANY_RE.search(oc): continue
        if a.cached_only and oc not in qs_cache: continue
        if case in out and not a.case: continue                      # already traced (unless targeting it)
        picked.append(r)
    if a.limit: picked = picked[:a.limit]

    cached = sum(1 for r in picked if (r.get('owner_clean','') or '').strip() in qs_cache)
    print(f"{len(picked)} lead(s) to pull ({cached} via cached token / requests, {len(picked)-cached} need a mint)")
    if a.dry_run or not picked:
        for r in picked[:20]:
            oc=(r.get('owner_clean','') or '').strip()
            print(f"  {r.get('Case #',''):22} {oc:26} {'cached' if oc in qs_cache else 'MINT'}")
        return

    done = hits = 0
    for r in picked:
        case = r.get('Case #', ''); oc = (r.get('owner_clean', '') or '').strip()
        folio = r.get('Folio', '') or r.get('year_folio', '')
        judg = num(r.get('judgment'))
        models = None
        if oc in qs_cache:
            models = records_by_qs(qs_cache[oc])
        if models is None and not a.cached_only:
            sp = split_owner(oc)
            if sp: models = mint_and_fetch(sp)
        if models is None:
            print(f"  --  {case:22} {oc:26} (no records / blocked)")
            continue
        res = analyze(models, folio, judg, ftype=_fc_type(case))
        res['traced'] = time.strftime('%Y-%m-%d'); res['folio'] = norm_folio(folio); res['owner'] = oc
        out[case] = res
        done += 1
        flag = ''
        if res['open_count'] >= 2:
            hits += 1; flag = f"  <-- OPEN 2ND ~${res['junior']:,} (of {res['open_count']} open mtgs)"
        print(f"  ok  {case:22} {oc:26} {res['open_count']} open mtg{flag}")
        json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
        time.sleep(0.4)
    print(f"\nDONE: {done} traced, {hits} with a surviving 2nd mortgage. -> records_liens.json")


if __name__ == '__main__':
    main()
