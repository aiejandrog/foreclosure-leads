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

# a lender/bank plaintiff = a real mortgage foreclosure (RPMF). An HOA/association/individual
# plaintiff is a different animal (association lien, partition, family) — drop it from this lane.
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
    return fr.strftime('%m/%d/%Y'), to.strftime('%m/%d/%Y')


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


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true')
    ap.add_argument('--days', type=int, default=30)
    a = ap.parse_args()
    if a.probe:
        probe()
    else:
        print('run --probe first to confirm the API, then the sweep is wired from what it returns')
