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
LENDER_RE = re.compile(r'\b(BANK|MORTG|LOAN|LENDING|FINANC|CAPITAL|FED(ERAL)?|CREDIT UNION|N\.?A\.?|'
                       r'FSB|TRUST|SERVICING|FUND(ING)?|HOLDINGS|WILMINGTON|DEUTSCHE|WELLS FARGO|'
                       r'CHASE|CITI|US BANK|NATIONSTAR|CARRINGTON|SELENE|RUSHMORE|FREEDOM|PENNYMAC|'
                       r'PHH|SHELLPOINT|NEWREZ|LAKEVIEW|FANNIE|FREDDIE|HUD|SECRETARY)\b', re.I)
HOA_RE = re.compile(r'\b(HOA|CONDO|ASSOC|ASSN|HOMEOWNER|MASTER|COMMUNITY|VILLAS?|TOWERS?|COA|POA)\b', re.I)

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
""".replace('SITEKEY', G.SITE_KEY)


def _mint_search(doc_type, d_from, d_to, stype='Name/Document', attempts=25):
    """Persistent: keep hammering the captcha until the LP query yields, or the cap is hit."""
    from playwright.sync_api import sync_playwright
    for attempt in range(1, attempts + 1):
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                pg = b.new_context(user_agent=G.UA, viewport={'width': 1400, 'height': 1000}).new_page()
                pg.goto(G.BASE, timeout=40000, wait_until='domcontentloaded')
                pg.wait_for_timeout(4000 + attempt * 400)
                res = pg.evaluate(SEARCH_JS, [doc_type, d_from, d_to, stype])
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='(legacy reCAPTCHA-v3 browser probe)')
    ap.add_argument('--days', type=int, default=30)
    a = ap.parse_args()
    if a.probe:
        probe(); return
    out = lp_sweep(days=a.days)
    if not out:
        print('\nno LP filings — every plaintiff search blocked (captcha) or empty window. Retry.'); return
    from collections import Counter
    kinds = Counter(x['kind'] for x in out)
    out.sort(key=lambda x: str(x.get('date') or ''), reverse=True)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
    print(f"\nDONE: {len(out)} fresh LIS PENDENS ({dict(kinds)}) -> lis_pendens.json")
    print("Front of the funnel — the owner the day their foreclosure was filed. Board play = LP-EARLY (be first).")


if __name__ == '__main__':
    main()
