"""Miami-Dade pending-foreclosure lead pipeline — one-command rerun.

Usage:  python foreclosure_leads.py
Output: Desktop CSV "Miami-Dade Foreclosure Leads - <today>.csv" + leads_final.json here.

Phase 1: auto-discover auction dates (current + next month) from the RealForeclose calendar,
         scrape every "Auctions Waiting" case (#Area_W) with pagination.
Phase 2: enrich each parcel via the Miami-Dade Property Appraiser public API
         (owner, mailing address, market value, homestead, beds/baths, last sale).
Phase 3: qualify + score (equity/lead-time/homestead/value), write CSV sorted best-first.
"""
import json, re, time, csv, os, urllib.parse
from datetime import datetime, date, timedelta
import requests
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = r"C:\Users\olqbb\OneDrive\Desktop"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
BASE = "https://miamidade.realforeclose.com/index.cfm"

# NOTE (2026-07-09): auction detail rows use <td> label cells, NOT <th>. Waiting list = #Area_W only.
EXTRACT_JS = """
() => {
  const out = [];
  document.querySelectorAll('#Area_W .AUCTION_ITEM').forEach(item => {
    const rec = {}; const addr = [];
    item.querySelectorAll('.AUCTION_DETAILS tr').forEach(tr => {
      const cells = tr.querySelectorAll('td,th');
      const label = cells.length ? (cells[0].innerText || cells[0].textContent || '').trim().replace(/:$/,'') : '';
      const val = cells.length > 1 ? (cells[1].innerText || cells[1].textContent || '').trim() : '';
      if (label === 'Property Address') addr.push(val);
      else if (!label && val && addr.length) addr.push(val);
      else if (label) rec[label] = val;
    });
    rec.Address = addr.join(', ');
    const a = item.querySelector('a[href*="folio="]');
    rec.Folio = a ? (a.href.split('folio=')[1] || '') : '';
    out.push(rec);
  });
  const max = document.getElementById('maxWA');
  return JSON.stringify({items: out, maxPages: max ? (max.textContent || '').trim() : '1'});
}
"""

CAL_JS = """
() => {
  const days = [];
  document.querySelectorAll('.CALBOX').forEach(box => {
    const dayid = box.getAttribute('dayid');
    const txt = box.innerText.replace(/\\s+/g, ' ').trim();
    const m = txt.match(/Foreclosure\\s+(\\d+)\\s*\\/\\s*(\\d+)/);
    if (dayid && m) days.push({date: dayid, remaining: parseInt(m[1])});
  });
  return JSON.stringify(days);
}
"""

def discover_dates(page):
    today = date.today()
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    dates = []
    for cal in [today, next_month]:
        page.goto(f"{BASE}?zaction=USER&zmethod=CALENDAR&selCalDate={cal:%m/%d/%Y}", timeout=45000)
        page.wait_for_selector('.CALBOX', timeout=20000)
        for d in json.loads(page.evaluate(CAL_JS)):
            dt = datetime.strptime(d['date'], '%m/%d/%Y').date()
            if dt >= today and d['remaining'] > 0 and d['date'] not in [x[0] for x in dates]:
                dates.append((d['date'], d['remaining']))
    print(f"auction dates found: {[f'{d} ({n})' for d, n in dates]}")
    return [d for d, _ in dates]

def scrape_date(page, d, attempt=1):
    page.goto(f"{BASE}?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE={d}", timeout=45000)
    try:
        page.wait_for_selector('#Area_W .AUCTION_DETAILS tr', timeout=25000, state='attached')
    except Exception:
        if attempt == 1:
            return scrape_date(page, d, attempt=2)
        print(f"{d}: no waiting auctions rendered"); return []
    data = json.loads(page.evaluate(EXTRACT_JS))
    items = list(data['items'])
    # pager text is unreliable headless — click Next until the first case stops changing
    seen_firsts = {items[0].get('Case #','') if items else ''}
    pages = 1
    for _ in range(25):
        cur_first = data['items'][0].get('Case #', '') if data['items'] else ''
        clicked = page.evaluate("() => { const b = document.querySelector('.Head_W .PageRight'); if (!b) return false; b.click(); return true; }")
        if not clicked: break
        advanced = False
        for _ in range(16):
            time.sleep(0.5)
            data = json.loads(page.evaluate(EXTRACT_JS))
            first = data['items'][0].get('Case #', '') if data['items'] else ''
            if first and first != cur_first and first not in seen_firsts:
                seen_firsts.add(first)
                items += data['items']; pages += 1; advanced = True
                break
        if not advanced: break
    for rec in items: rec['AuctionDate'] = d
    print(f"{d}: {len(items)} pending (pages={pages})")
    return items

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'browser-profile')

def scrape():
    with sync_playwright() as p:
        # if the user has logged into realforeclose via login-setup.bat, reuse that profile so
        # any logged-in-only fields (case detail, judgment docs) flow into the generic extractor
        if os.path.isdir(PROFILE_DIR):
            ctx = p.chromium.launch_persistent_context(PROFILE_DIR, headless=True,
                user_agent=UA, viewport={"width":1400,"height":1000})
            browser = ctx
            page = ctx.new_page()
        else:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(user_agent=UA, viewport={"width":1400,"height":1000}).new_page()
        leads = []
        for d in discover_dates(page):
            leads += scrape_date(page, d)
        browser.close()
    seen, out = set(), []
    for r in leads:
        k = (r.get('Case #') or r.get('Address','')) + r.get('AuctionDate','')
        if not k or k in seen: continue
        seen.add(k); out.append(r)
    return out

def money(s):
    try: return float(re.sub(r'[^\d.]','', s or '') or 0)
    except: return 0.0

def enrich(leads):
    s = requests.Session(); s.headers['User-Agent'] = UA
    for i, r in enumerate(leads):
        folio = re.sub(r'\D','', r.get('Folio',''))
        r['enriched'] = False
        if not folio: continue
        try:
            d = s.get("https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx",
                params={"Operation":"GetPropertySearchByFolio","clientAppName":"PropertySearch","folioNumber":folio},
                timeout=20).json()
        except Exception as e:
            print("PA fail", folio, e); time.sleep(1); continue
        pi = d.get('PropertyInfo') or {}
        owners = [o.get('Name','') for o in (d.get('OwnerInfos') or []) if o.get('Name')]
        ma = d.get('MailingAddress') or {}
        mkt = next((a['TotalValue'] for a in (d.get('Assessment') or {}).get('AssessmentInfos') or [] if a.get('TotalValue')), 0)
        benefits = (d.get('Benefit') or {}).get('BenefitInfos') or []
        sales = d.get('SalesInfos') or []
        last_sale = sales[0] if sales else {}
        r.update({
            'enriched': True, 'owners': '; '.join(owners),
            'mailing_address': ', '.join(x for x in [ma.get('Address1',''), ma.get('Address2',''), ma.get('City',''), ma.get('State',''), ma.get('ZipCode','')] if x),
            'market_value': mkt, 'dor_desc': pi.get('DORDescription',''),
            'beds': pi.get('BedroomCount',0), 'baths': pi.get('BathroomCount',0),
            'living_area': pi.get('BuildingHeatedArea',0), 'year_folio': pi.get('FolioNumber',''),
            'homestead': any('homestead' in (b.get('Description','') or '').lower() for b in benefits),
            'last_sale_price': last_sale.get('SalePrice',0), 'last_sale_date': last_sale.get('DateOfSale',''),
        })
        if (i+1) % 20 == 0: print(f"enriched {i+1}/{len(leads)}")
        time.sleep(0.35)
    return leads

CLERK = "https://www2.miamidadeclerk.gov"

def classify(case_type, plaintiff):
    ct = (case_type or '').upper()
    pl = (plaintiff or '').upper()
    if re.search(r'\b(ASSOCIATION|ASSN|CONDO|HOMEOWNER|MASTER ASSOC|HOA|TOWNHOM|VILLAS?|COMMUNITY)\b', pl):
        return 'HOA/Condo'
    if 'RPMF' in ct or re.search(r'\b(BANK|MORTGAGE|LOAN|FINANCIAL|CAPITAL|FUNDING|LENDING|N\.?A\.?|TRUST|SERVICING|WELLS FARGO|CHASE|CITI|ROCKET|CROSSCOUNTRY|FREEDOM|LAKEVIEW|PENNYMAC|NEWREZ|CARRINGTON)\b', pl):
        return 'Bank/Mortgage'
    if re.search(r'\b(CITY OF|COUNTY|STATE OF|MIAMI-DADE|CODE ENF)\b', pl):
        return 'Govt/Code'
    if 'RPMF' in ct or 'FORECLOS' in ct:
        return 'Mortgage/Other'
    return 'Other'

def enrich_clerk(leads):
    """Miami-Dade Clerk OCS API: plaintiff, defendants, case type + a deep-link that lands
    directly on the case page (parties, dockets, final judgment). Fully public, no login."""
    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Referer': CLERK + '/ocs/'})
    ok = 0
    for i, r in enumerate(leads):
        case = (r.get('Case #') or '').strip()
        r['plaintiff'] = r['defendants'] = r['docket_url'] = ''
        if not re.match(r'\d{4}-\d+-\w+-\d+', case):
            continue
        try:
            enc = s.get(f"{CLERK}/ocs/api/CaseInfo/encrypt/{case}", timeout=20).json()
            qs = enc.get('qs')
            if not qs: continue
            d = s.post(f"{CLERK}/ocs/api/CaseInfo/GetSingleCaseResult?qs={qs}",
                       headers={'Content-Type': 'application/json'}, data='""', timeout=20).json()
            if not d or d.get('caseID', -1) == -1:
                continue
            parties = d.get('parties', []) or []
            plaintiffs = [p.get('partyName','').strip() for p in parties if 'PLAINTIFF' in (p.get('partyTypeDesc','') or '').upper()]
            defs = [p.get('partyName','').strip() for p in parties if 'DEFENDANT' in (p.get('partyTypeDesc','') or '').upper()]
            r['plaintiff'] = plaintiffs[0] if plaintiffs else ''
            # skip the first defendant (that's the owner, already shown) -> "also named"
            extra = [x for x in defs[1:] if x][:6]
            r['defendants'] = '; '.join(extra)
            r['clerk_case_type'] = d.get('caseType','')
            r['case_status'] = d.get('caseStatus','')
            r['docket_url'] = f"{CLERK}/ocs/searchResults?qs={qs}"
            r['case_type'] = classify(d.get('caseType',''), r['plaintiff'])
            ok += 1
        except Exception:
            pass
        if (i+1) % 40 == 0: print(f"clerk {i+1}/{len(leads)} ({ok} matched)")
        time.sleep(0.25)
    print(f"clerk enrichment: {ok}/{len(leads)} cases resolved")
    return leads

def qualify(leads):
    today = datetime.now()
    for r in leads:
        judg = money(r.get('Final Judgment Amount',''))
        mkt = r.get('market_value',0) or 0
        r['judgment'] = judg
        r['equity'] = mkt - judg if mkt else 0
        r['equity_pct'] = round(r['equity']/mkt*100,1) if mkt else 0
        try: days = (datetime.strptime(r['AuctionDate'],'%m/%d/%Y') - today).days
        except: days = 0
        r['days_to_auction'] = days
        case = r.get('Case #','')
        fy = re.match(r'(\d{4})-', case)
        r['filing_year'] = int(fy.group(1)) if fy else 0
        # CC = county-court case, almost always HOA/condo assoc foreclosure: equity is real but a
        # senior mortgage may exist that the judgment amount doesn't show
        is_hoa = bool(re.search(r'-CC-', case))
        r['warning'] = 'HOA/assoc case - verify senior mortgage on docket' if is_hoa else ''
        ep = r['equity_pct']
        # granular 0-100 so leads rank instead of clustering
        score = 0.0
        if mkt: score += min(42.0, max(0.0, ep) * 0.42)          # equity, 0-42
        score += min(18.0, max(0, days) * 1.0)                    # runway, 0-18
        score += 12 if r.get('homestead') else 0                  # owner-occupied
        if 200000 <= mkt <= 1000000: score += 14                  # value band
        elif mkt > 1000000: score += 9
        elif mkt >= 150000: score += 6
        if r.get('enriched') and r.get('owners'): score += 8      # contactable
        elif r.get('enriched'): score += 4
        if is_hoa: score -= 6                                     # payoff uncertainty
        dq = []
        if mkt and mkt < 100000: dq.append('low value')
        if mkt and ep < 15: dq.append('thin/negative equity')
        if not r.get('Address','').strip(): dq.append('no address')
        r['score'] = round(score) if not dq else min(round(score), 40)
        r['disqualifiers'] = '; '.join(dq)
        r['tier'] = 'A' if r['score']>=70 and not dq else ('B' if r['score']>=50 and not dq else 'C')
        addr = r.get('Address','').replace(',',' ')
        r['zillow_url'] = 'https://www.zillow.com/homes/' + urllib.parse.quote(addr) + '_rb/' if addr.strip() else ''
        folio = re.sub(r'\D','', r.get('Folio',''))
        r['pa_url'] = 'https://www.miamidade.gov/Apps/PA/propertysearch/#/?folio=' + folio if folio else ''
        r['auction_url'] = f"{BASE}?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE={r.get('AuctionDate','')}"
        # owner purchase year (from PA sales history)
        sd = re.search(r'(\d{4})$', (r.get('last_sale_date','') or '').strip())
        r['bought_year'] = int(sd.group(1)) if sd else 0
        # TruePeopleSearch prefill for human owners (companies get Sunbiz instead)
        first_owner = (r.get('owners','') or '').split(';')[0].strip()
        is_company = bool(re.search(r'\b(LLC|CORP|INC|TRUST|TRS|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|LE|REM)\b', first_owner, re.I))
        zm = re.search(r'(\d{5})\s*$', r.get('Address','') or '')
        if first_owner and not is_company:
            q = urllib.parse.quote(first_owner)
            z = ('&citystatezip=' + zm.group(1)) if zm else ''
            r['people_url'] = f"https://www.truepeoplesearch.com/results?name={q}{z}"
        else:
            r['people_url'] = ''
        # case_type comes from the Clerk API (enrich_clerk); fall back to a heuristic if unresolved
        if not r.get('case_type'):
            r['case_type'] = 'HOA/Condo' if re.search(r'-CC-', r.get('Case #','')) else 'Mortgage/Other'
        # tax-collector lookup link by folio (delinquent taxes/certificates; Cloudflare-walled to scrape,
        # so this is a reliable one-click lookup instead)
        r['tax_url'] = ('https://miamidade.county-taxes.com/public/search?search_query=' + folio) if folio else ''
        # mortgage-risk: an HOA/condo judgment often hides a senior mortgage. If a lender is a
        # co-defendant, the true payoff is higher than the association judgment shown -> flag it.
        defs = (r.get('defendants','') or '').upper()
        r['mortgage_risk'] = bool(r.get('case_type','').startswith('HOA') and re.search(
            r'BANK|MORTGAGE|LOAN|FINANCIAL|CAPITAL|FUNDING|LENDING|SERVICING|FEDERAL CREDIT|'
            r'FANNIE|FREDDIE|HOUSING AND URBAN|SECRETARY OF HOUSING|BANC|LENDER|\bN\.?A\.?\b|'
            r'CITIMORTGAGE|WELLS FARGO|CHASE|NATIONSTAR|PENNYMAC|NEWREZ|CARRINGTON|LAKEVIEW', defs))
    return leads

def make_tracker(leads):
    slim = [{
        'tier': r.get('tier',''), 'score': r.get('score',0),
        'auction': r.get('AuctionDate',''), 'days': r.get('days_to_auction',0),
        'case': r.get('Case #',''), 'owners': r.get('owners',''),
        'addr': r.get('Address',''), 'mail': r.get('mailing_address',''),
        'value': r.get('market_value',0) or 0, 'judg': r.get('judgment',0) or 0,
        'eq': r.get('equity_pct',0), 'hs': bool(r.get('homestead')),
        'zillow': r.get('zillow_url',''), 'pa': r.get('pa_url',''),
        'auc': r.get('auction_url',''), 'warn': r.get('warning',''),
        'filed': r.get('filing_year',0),
        'bought': r.get('bought_year',0), 'bprice': r.get('last_sale_price',0) or 0,
        'people': r.get('people_url',''), 'ctype': r.get('case_type',''),
        'plaintiff': r.get('plaintiff',''), 'defs': r.get('defendants',''),
        'docket': r.get('docket_url',''), 'tax': r.get('tax_url',''),
        'cstatus': r.get('case_status',''), 'mr': bool(r.get('mortgage_risk')),
    } for r in leads]
    tpl = open(os.path.join(HERE,'tracker_template.html'), encoding='utf-8').read()
    html = tpl.replace('__DATA__', json.dumps(slim)).replace('__UPDATED__', f"{date.today():%Y-%m-%d}")
    os.makedirs(os.path.join(HERE,'docs'), exist_ok=True)
    for out in [os.path.join(HERE,'docs','index.html'),
                os.path.join(DESKTOP,'Foreclosure Lead Tracker.html')]:
        open(out,'w',encoding='utf-8').write(html)
    print('tracker written: docs/index.html + Desktop')

def main():
    leads = scrape()
    print(f"scraped {len(leads)} pending auctions")
    json.dump(leads, open(os.path.join(HERE,'leads_raw.json'),'w'), indent=1)
    leads = enrich(leads)
    leads = enrich_clerk(leads)
    leads = qualify(leads)
    leads.sort(key=lambda r: -r['score'])
    json.dump(leads, open(os.path.join(HERE,'leads_final.json'),'w'), indent=1)
    make_tracker(leads)
    cols = ['tier','score','AuctionDate','days_to_auction','Case #','filing_year','owners','Address','mailing_address',
            'market_value','judgment','equity','equity_pct','homestead','warning','dor_desc','beds','baths',
            'living_area','last_sale_price','last_sale_date','year_folio','zillow_url','pa_url','disqualifiers']
    out_csv = os.path.join(DESKTOP, f"Miami-Dade Foreclosure Leads - {date.today():%Y-%m-%d}.csv")
    with open(out_csv,'w',newline='',encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for r in leads: w.writerow(r)
    a = sum(1 for r in leads if r['tier']=='A'); b = sum(1 for r in leads if r['tier']=='B')
    print(f"DONE: {len(leads)} leads | Tier A: {a} | Tier B: {b}")
    print(f"CSV: {out_csv}")

if __name__ == '__main__':
    main()
