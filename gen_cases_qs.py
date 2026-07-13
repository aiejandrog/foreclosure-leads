"""Generate direct-to-results Miami-Dade OCS 'Cases' links per owner.

The county court search is reCaptcha-gated, so we mint the token in a real browser (Playwright),
run the party-name search, and keep the returned qs ONLY if it actually returns cases. The qs is a
stable encryption of the name search, so the cache (cases_qs.json) is permanent per owner — re-runs
only cost NEW owners. make_tracker bakes the qs into the site so the Cases link jumps straight to
the owner's cases; owners with no qs fall back to the open-search link (never a broken link).

Usage:
  python gen_cases_qs.py            # generate for all human-owner leads not already cached
  python gen_cases_qs.py --tier A   # only Tier A
  python gen_cases_qs.py --limit 20 # cap this run
"""
import json, os, re, time, argparse
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
CACHE = os.path.join(HERE, 'cases_qs.json')
SITE_KEY = '6Le7np8qAAAAAAEMezDvhuXyKV4EA6BWZTvdK_E6'
BASE = 'https://www2.miamidadeclerk.gov/ocs/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|TR|EST|ESTATE|MTGE|SVCS|'
                        r'UNITED STATES|STATE OF|COUNTY|CITY OF|DEPARTMENT|SECRETARY|\bUSA\b)\b', re.I)
MAX_HITS = 100   # a real individual owner rarely has >100 filings; more = common-name over-match -> skip (fallback)
DEADLINE_SEC = int(os.environ.get('GEN_DEADLINE','480'))   # never let generation eat the scheduled task's 30-min kill: stop after 8 min, resume next run

# mints a reCaptcha token, runs the party-name search, verifies it returns cases. Returns {success,qs,count}.
JS = r"""
async (lf) => {
  const KEY='SITEKEY';
  if(!window.grecaptcha || !window.grecaptcha.execute){
    await new Promise((res,rej)=>{ const s=document.createElement('script'); s.src='https://www.google.com/recaptcha/api.js?render='+KEY; s.onload=res; s.onerror=()=>rej(new Error('blocked')); document.head.appendChild(s); setTimeout(()=>rej(new Error('captcha load timeout')),25000); });
    await new Promise(r=>setTimeout(r,2000));
  }
  await new Promise(res=>grecaptcha.ready(res));
  const token=await grecaptcha.execute(KEY,{action:'partysearch'});
  const crit={searchBy:'personaName',compareBy:'secondPartyPersonaName',partyFirstName:lf[1],partyLastName:lf[0],businessNameName:'',partyType:0,partyFirstName2:'',partyLastName2:'',caseType:0,filingDateFrom:'0001-01-01T00:00:00Z',filingDateTo:'0001-01-01T00:00:00Z',secondPartyBusinessName2:'',section:0};
  const r=await fetch('/ocs/api/CaseInfo/PostSearchByPartyName',{method:'POST',headers:{'Content-Type':'application/json','Captcha-Token':token},body:JSON.stringify(crit)});
  const t=await r.text(); let j=null; try{ j=JSON.parse(t); }catch(e){}
  if(!j || !j.success || !j.qs) return {success:false, qs:null, count:0};
  const g=await fetch('/ocs/api/CaseInfo/GetMultipleCaseResult?qs='+j.qs,{headers:{'Accept':'application/json'}});
  let gj=null; try{ gj=JSON.parse(await g.text()); }catch(e){}
  const arr=(gj && gj.caseListResult) || [];
  return {success:true, qs:j.qs, count:Array.isArray(arr)?arr.length:0};
}
""".replace('SITEKEY', SITE_KEY)


def split_name(clean):
    """PA owner names here read First [Middle] Last; drop single-letter middle initials.
    Returns (lastName, firstName) or None."""
    toks = [t for t in (clean or '').split() if len(t) > 1]
    if len(toks) < 2:
        return None
    return (' '.join(toks[1:]), toks[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tier', default='')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    leads = json.load(open(LEADS, encoding='utf-8'))
    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}

    todo = {}
    for r in leads:
        if args.tier and (r.get('tier', '') or '') != args.tier:
            continue
        oc = (r.get('owner_clean') or '').strip()
        if not oc or COMPANY_RE.search(oc) or oc in cache:
            continue
        sp = split_name(oc)
        if sp:
            todo[oc] = sp
    items = list(todo.items())
    if args.limit:
        items = items[:args.limit]
    print(f"{len(items)} owner(s) to generate ({len(cache)} already cached, of {len(leads)} leads)")
    if not items:
        return

    ok = 0
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_context(user_agent=UA, viewport={'width': 1400, 'height': 1000}).new_page()
        for a in range(4):
            try:
                pg.goto(BASE, timeout=40000, wait_until='domcontentloaded'); break
            except Exception as e:
                print(f"  goto {a+1} failed: {str(e)[:50]}")
                if a == 3: raise
                pg.wait_for_timeout(4000)
        pg.wait_for_timeout(2500)
        _start = time.time()
        for oc, lf in items:
            if time.time() - _start > DEADLINE_SEC:
                print(f"  .. 8-min budget hit; stopping (rest resume next run)"); break
            try:
                res = None
                for attempt in range(2):
                    try:
                        res = pg.evaluate(JS, list(lf)); break
                    except Exception:
                        if attempt == 1: raise
                        pg.wait_for_timeout(2500)
                _n = res.get('count', 0) if res else 0
                if res and res.get('success') and res.get('qs') and 0 < _n <= MAX_HITS:
                    cache[oc] = res['qs']; ok += 1
                    print(f"  ok  {oc:32} {_n} case(s)")
                elif _n > MAX_HITS:
                    print(f"  ~~  {oc:32} too common ({_n}), skip -> fallback")
                else:
                    print(f"  --  {oc:32} no cases")
                json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=1)  # save as we go
                time.sleep(1.6)  # gentle on reCaptcha
            except Exception as e:
                print(f"  ERR {oc:32} {str(e)[:50]}")
        b.close()
    print(f"\nDONE: {ok}/{len(items)} owners now have a direct Cases link. Cache -> cases_qs.json")


if __name__ == '__main__':
    main()
