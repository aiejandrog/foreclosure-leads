#!/usr/bin/env python3
# pb_case_lookup.py — Palm Beach (15th Circuit) plaintiff + case-type resolver via eCaseView.
# HEADED Playwright is REQUIRED: reCAPTCHA v3 rejects headless browsers AND 2Captcha tokens; only a
# real headed browser's own (invisible) grecaptcha 'case_search' token passes. No human interaction.
#   python pb_case_lookup.py 502026CA000685XXXAMB
import re, sys, time
from playwright.sync_api import sync_playwright
BASE='https://appsgp.mypalmbeachclerk.com/eCaseView'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
UCN_RE=re.compile(r'\d{2}-\d{4}-[A-Z]{2}-\d{6}-[A-Z0-9]{3,4}-[A-Z]{2}')
STYLE=re.compile(r'\s+V\.?S?\.?\s+',re.I)
BANK=re.compile(r'\b(BANK|MORTGAGE|LOAN|FINANC|CAPITAL|FUNDING|LENDING|LENDER|N\.?A\.?|NATIONAL ASSOCIATION|TRUST|SERVICING|SAVINGS|FEDERAL|CREDIT UNION|WELLS FARGO|CHASE|CITI|ROCKET|PENNYMAC|NEWREZ|NATIONSTAR|LOANDEPOT|FANNIE|FREDDIE|US BANK|U\.?S\.? BANK|DEUTSCHE|BANKUNITED|TRUIST|PNC|FLAGSTAR|AMERANT)\b')
HOA=re.compile(r'\b(ASSOCIATION|ASSN|CONDOMINIUM|CONDO|HOMEOWNER|MASTER ASSOC|HOA|COA|TOWNHOM|VILLAS?|COMMUNITY|PROPERTY OWNERS)\b')
MTG=re.compile(r'MORTGAGE FORECLOS|RPMF|FORECLOSURE.*\$|NON HR FORECLOS|COMM FORECLOS|HOMESTEAD RES FORECLOS',re.I)
def core(c):
    c=re.sub(r'[^0-9A-Za-z]','',c or '').upper(); m=re.match(r'\d{2}(\d{4}[A-Z]{2}\d{6})',c); return m.group(1) if m else c
def classify(pl,ct,court):
    pl=(pl or '').upper(); ct=(ct or '').upper()
    if not pl: return False,'no plaintiff'
    plx=re.sub(r'\bNATIONAL ASSOCIATION\b',' ',pl)
    if HOA.search(plx) and not BANK.search(plx): return False,'HOA/condo plaintiff'
    if (court or '').upper().startswith('COUNTY'): return False,'County Civil (sub-$50k) junior/HOA'
    if BANK.search(pl) and (MTG.search(ct) or 'FORECLOS' in ct): return True,'institutional lender 1st mortgage'
    if BANK.search(pl): return False,f'bank plaintiff but case type "{ct}" not a mortgage foreclosure'
    return False,'non-bank (individual/LLC) plaintiff — senior mortgage may survive'
def _guest(pg):
    for _ in range(4):
        pg.goto(BASE+'/',wait_until='domcontentloaded',timeout=45000); pg.wait_for_timeout(2200)
        try: pg.click("button:has-text('Login as Guest User')",timeout=10000)
        except Exception: continue
        pg.wait_for_load_state('domcontentloaded'); pg.wait_for_timeout(1600)
        if 'GuestIn' in pg.url or pg.locator("a[href*='SignOut']").count()>0: return True
    return False
def _one(pg,lead):
    cc=core(lead)
    for _ in range(3):
        pg.goto(BASE+'/Search?handler=NewSearch',wait_until='domcontentloaded',timeout=45000)
        try: pg.wait_for_selector('#SearchRequest_CaseNumber',timeout=20000)
        except Exception: continue
        pg.fill('#SearchRequest_CaseNumber',cc); pg.wait_for_timeout(500); pg.click('#btnBeginSearch')
        try: pg.wait_for_url('**/SearchResults**',timeout=25000)
        except Exception: pg.wait_for_timeout(6000)
        txt=pg.evaluate("()=>{const c=document.querySelector('#caseinfo');return c?c.innerText:document.body.innerText;}")
        if 'Could not verify the ReCaptcha' in txt: time.sleep(2); continue
        if 'No cases found' in txt: return {'case':lead,'found':False}
        rows=pg.evaluate("""()=>{const rs=[];document.querySelectorAll('#searchResults tbody tr, table tbody tr').forEach(tr=>rs.push([...tr.querySelectorAll('td,th')].map(td=>(td.innerText||'').trim())));return rs;}""")
        for cells in rows:
            mu=UCN_RE.search(' '.join(cells))
            if not mu: continue
            style=next((c for c in cells if STYLE.search(c) and not UCN_RE.search(c)),'')
            court=cells[1] if len(cells)>1 else ''; ctype=cells[2] if len(cells)>2 else ''
            filed=next((c for c in cells if re.match(r'\d{1,2}/\d{1,2}/\d{4}$',c)),''); status=cells[-1]
            p=STYLE.split(style,1); plaintiff=p[0].strip() if style else ''; defendant=p[1].strip() if len(p)>1 else ''
            isbank,why=classify(plaintiff,ctype,court)
            return {'case':lead,'ucn':mu.group(0),'plaintiff':plaintiff,'defendant':defendant,'case_type':ctype,'court_type':court,'filed':filed,'status':status,'is_bank_foreclosing_first':isbank,'reason':why,'found':True}
        return {'case':lead,'found':False,'note':'no parsable row'}
    return {'case':lead,'found':False,'note':'reCAPTCHA blocked'}
def resolve_cases(cases,headless=False):
    out=[]
    with sync_playwright() as p:
        b=p.chromium.launch(headless=headless); pg=b.new_context(user_agent=UA,viewport={'width':1360,'height':950}).new_page()
        if not _guest(pg): b.close(); raise RuntimeError('guest login failed')
        for lc in cases:
            try: out.append(_one(pg,lc))
            except Exception as e: out.append({'case':lc,'found':False,'note':str(e)[:120]})
            time.sleep(0.8)
        b.close()
    return out
if __name__=='__main__':
    import json; print(json.dumps(resolve_cases(sys.argv[1:] or ['502026CA000685XXXAMB']),indent=1))