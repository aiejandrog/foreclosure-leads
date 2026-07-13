"""Generate direct-to-results Miami-Dade Official Records ('Records') links per owner — the owner's
MORTGAGES, liens and judgments (this is how you find the hidden 1st/2nd mortgage Jose asks about).

Same approach as gen_cases_qs.py: mint the reCaptcha token in a real browser (Playwright), run the
Name/Document search with the EXACT params (firstQuery=y, searchtype=Name/Document), keep the qs
only if it returns records. Cache (records_qs.json) is permanent per owner. make_tracker bakes the
qs so the Records link opens the owner's recorded documents directly; misses fall back to open+copy.

Usage: python gen_records_qs.py [--tier A] [--limit N]
"""
import json, os, re, time, argparse
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
CACHE = os.path.join(HERE, 'records_qs.json')
SITE_KEY = '6LfI8ikaAAAAAH0qlQMApskMGd1U6EqDyniH5t0x'
BASE = 'https://onlineservices.miamidadeclerk.gov/officialrecords/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|TR|EST|ESTATE|MTGE|SVCS)\b', re.I)

JS = r"""
async (lf) => {
  const KEY='SITEKEY';
  if(!window.grecaptcha || !window.grecaptcha.execute){
    await new Promise((res,rej)=>{ const s=document.createElement('script'); s.src='https://www.google.com/recaptcha/api.js?render='+KEY; s.onload=res; s.onerror=()=>rej(new Error('blocked')); document.head.appendChild(s); setTimeout(()=>rej(new Error('captcha load timeout')),25000); });
    await new Promise(r=>setTimeout(r,2000));
  }
  await new Promise(res=>grecaptcha.ready(res));
  const token=await grecaptcha.execute(KEY,{action:'standardsearch'});
  const partyName = (lf[0]+' '+lf[1]).trim();   // LAST FIRST
  const url='/officialrecords/api/home/standardsearch?partyName='+encodeURIComponent(partyName)+'&dateRangeFrom=&dateRangeTo=&documentType=&searchT=&firstQuery=y&searchtype='+encodeURIComponent('Name/Document');
  const r=await fetch(url,{method:'POST',headers:{'Accept':'application/json','x-recaptcha-token':token,'content-type':'application/json; charset=utf-8'},body:''});
  let j=null; try{ j=await r.json(); }catch(e){}
  if(!j || !j.qs) return {success:false, qs:null, count:0};
  const g=await fetch('/officialrecords/api/SearchResults/getStandardRecords?qs='+j.qs,{headers:{'Accept':'application/json'}});
  let gj=null; try{ gj=JSON.parse(await g.text()); }catch(e){}
  const arr=(gj && gj.recordingModels) || [];
  return {success:true, qs:j.qs, count:Array.isArray(arr)?arr.length:0};
}
""".replace('SITEKEY', SITE_KEY)


def split_name(clean):
    toks = [t for t in (clean or '').split() if len(t) > 1]
    if len(toks) < 2:
        return None
    return (' '.join(toks[1:]), toks[0])  # (last, first)


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
    print(f"{len(items)} owner(s) to generate ({len(cache)} cached, of {len(leads)} leads)")
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
        for oc, lf in items:
            try:
                res = None
                for attempt in range(2):
                    try:
                        res = pg.evaluate(JS, list(lf)); break
                    except Exception:
                        if attempt == 1: raise
                        pg.wait_for_timeout(2500)
                if res and res.get('success') and res.get('qs') and res.get('count', 0) > 0:
                    cache[oc] = res['qs']; ok += 1
                    print(f"  ok  {oc:32} {res['count']} record(s)")
                else:
                    print(f"  --  {oc:32} no records")
                json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=1)
                time.sleep(1.6)
            except Exception as e:
                print(f"  ERR {oc:32} {str(e)[:50]}")
        b.close()
    print(f"\nDONE: {ok}/{len(items)} owners now have a direct Records link. Cache -> records_qs.json")


if __name__ == '__main__':
    main()
