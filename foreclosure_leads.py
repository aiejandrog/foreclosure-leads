"""Miami-Dade pending-foreclosure lead pipeline — one-command rerun.

Usage:  python foreclosure_leads.py
Output: Desktop\DEALFLOW\ CSV "Miami-Dade Foreclosure Leads - <today>.csv" + tracker HTML;
        leads_final.json stays here in the project.

Phase 1: auto-discover auction dates (current + next month) from the RealForeclose calendar,
         scrape every "Auctions Waiting" case (#Area_W) with pagination.
Phase 2: enrich each parcel via the Miami-Dade Property Appraiser public API
         (owner, mailing address, market value, homestead, beds/baths, last sale).
Phase 3: qualify + score (equity/lead-time/homestead/value), write CSV sorted best-first.
"""
import json, re, time, csv, os, sys, shutil, urllib.parse
from datetime import datetime, date, timedelta
import requests
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = r"C:\Users\olqbb\OneDrive\Desktop"
# Where the plaintext Desktop tracker + daily CSV land. Local runs write to Alejandro's Desktop;
# GitHub Actions overrides DEALFLOW_DIR to a throwaway tmp path (the Linux runner has no Desktop,
# and the img copy would otherwise create a literal 'C:\\Users\\...' directory in the workspace).
DEALFLOW_DIR = os.environ.get('DEALFLOW_DIR') or os.path.join(DESKTOP, "DEALFLOW")
RESULTS_FILE = os.path.join(HERE, 'skiptrace_results.json')   # local phone cache (gitignored)
PASS_FILE = os.path.join(HERE, 'site.pass')                    # shared-site password (gitignored)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
BASE = "https://miamidade.realforeclose.com/index.cfm"

# non-human parties named on a foreclosure case (bank/HOA/county/tenant/gov) — never a person to call.
# Company words are matched mostly as SUBSTRINGS (they never occur inside real person names), so glued
# forms like CITIFINANCIAL / CORPORATION / SERVICING are caught; a few short ones keep word boundaries.
_PARTY_JUNK = re.compile(
    r'(LLC|L\.L\.C|\bCORP|INCORPORAT|\bINC\b|\bCO\b|\bLP\b|\bLTD\b|PLLC|'
    r'BANK|TRUST|MORTGAGE|SERVICING|SERVICE|FINANCIAL|FINANCE|FUNDING|\bFUND\b|CAPITAL|CREDIT|LENDING|\bLOAN|'
    r'ASSOCIAT|\bASSN\b|CONDOMINIUM|\bCONDO\b|HOMEOWNER|\bHOA\b|FANNIE|FREDDIE|FEDERAL|\bNA\b|N\.A\.|'
    r'COUNTY|CITY OF|STATE OF|UNITED STATES|DEPARTMENT|\bDEPT\b|SECRETARY|\bUSA\b|\bIRS\b|TREASURY|REVENUE|TAX COLLECTOR|'
    r'ELEVATOR|UTILIT|ELECTRIC|\bWATER\b|\bSEWER\b|\bGROUP\b|PARTNER|HOLDING|INVESTMENT|PROPERT|REALTY|ENTERPRISE|SOLUTION|SYSTEM|MANAGEMENT|DEVELOPMENT|BUILDER|CONSTRUCTION|'
    r'UNKNOWN|TENANT|OCCUPANT|JOHN DOE|JANE DOE|ANY AND ALL|\bCLERK\b|ESTATE OF|LIENOR)', re.I)

def _clean_party(raw):
    """Clean one named party to 'First [Middle] Last': strip suffixes/spouse markers, flip 'Last, First'."""
    s = (raw or '').strip()
    s = re.sub(r'\b(ET\s?UX|ET\s?VIR|H/W|W/H|LE|REM|TRS|JR|SR|II|III|IV|ETAL|ET AL)\b', '', s, flags=re.I)
    s = re.sub(r'\s*&\s*[WH]\b.*$', '', s, flags=re.I)   # drop "&W SPOUSE" tail
    s = re.sub(r'\s*&\s*$', '', s).strip()
    if ',' in s:
        _a, _, _b = s.partition(','); s = (_b.strip() + ' ' + _a.strip()).strip()
    return re.sub(r'\s{2,}', ' ', s).strip()


def _slug(s):
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', (s or '').lower())).strip('-')

def cyberbg_url(name, address):
    """CyberBackgroundChecks NAME search — verified 2026-07-17 against live leads (Shembel, Lawrence):
    free (no paywall/captcha) detail page shows 5-10 phones w/ carrier + last-reported date, up to 7
    emails, prior addresses, RELATIVES (separate from associates — good for the tenant/relative bypass),
    and property basics. Consistently surfaced MORE phones than BatchData on both test leads. URL pattern
    /people/{first}-{last}/fl/{city-slug} (falls back to state-only if city can't be parsed); one click
    from there to VIEW DETAILS for the free full page. `name` is a 'First Last' string (reuse the TPS name)."""
    toks = [t for t in (name or '').split() if t]
    if len(toks) < 2:
        return ''
    slug = _slug(' '.join(toks))
    if not slug:
        return ''
    m = re.search(r',\s*([^,]+?)\s*,\s*[A-Z]{2}\s*\d{5}', address or '')
    city = _slug(m.group(1)) if m else ''
    return 'https://www.cyberbackgroundchecks.com/people/' + slug + '/fl' + ('/' + city if city else '')

def cyberbg_addr_url(mailing, address, is_company):
    """CyberBackgroundChecks ADDRESS search — verified 2026-07-17: often surfaces things the NAME search
    misses. Live test (Bazile-Medley, 6816 SW 5th St, Pembroke Pines): revealed she's ABSENTEE — this is
    her CURRENT address, distinct from the foreclosure property's mailing address on file with the county
    (meaning official notices may not be reaching her) — plus a phone BatchData never returned, and
    confirmed a family relation matching a decades-old deed. URL pattern /address/{street-slug}/{city-slug}/fl
    (no dashes needed in the street — slugified same as the name search). Prefer mailing over property
    address for the same absentee-owner reason as people_addr_url; skip PO boxes/companies."""
    src = (mailing or '').strip()
    if not src or re.search(r'\bP\.?\s*O\.?\s*BOX\b', src, re.I):
        src = (address or '').strip()
    if not src or is_company or re.search(r'\bP\.?\s*O\.?\s*BOX\b', src, re.I):
        return ''
    parts = [p.strip() for p in src.split(',') if p.strip()]
    if len(parts) < 2:
        return ''
    street = _slug(parts[0])
    rest = ' '.join(parts[1:])
    mz = re.search(r'(\d{5})(?:-\d{4})?\s*$', rest)
    rn = (rest[:mz.start()] if mz else rest).strip()
    sm = re.search(r'\b([A-Za-z]{2})\s*$', rn)
    city = _slug(rn[:sm.start()] if sm else rn)
    if not street or not city:
        return ''
    return 'https://www.cyberbackgroundchecks.com/address/' + street + '/' + city + '/fl'

def people_addr_url(mailing, address, is_company):
    """TruePeopleSearch ADDRESS search: returns only the people who actually live at an address, so the
    owner can be told apart from same-name strangers (BatchData gives no age/DOB to disambiguate). Prefer
    the mailing address (where the owner actually lives — matters for absentee owners), fall back to the
    property address, skip PO boxes (address search is useless on a box). Returns '' when not resolvable."""
    src = (mailing or '').strip()
    if not src or re.search(r'\bP\.?\s*O\.?\s*BOX\b', src, re.I):
        src = (address or '').strip()
    if not src or is_company or re.search(r'\bP\.?\s*O\.?\s*BOX\b', src, re.I):
        return ''
    parts = [p.strip() for p in src.split(',') if p.strip()]
    if len(parts) < 2:
        return ''
    street = parts[0]
    rest = ' '.join(parts[1:])                                  # "MIAMI FL 33184-2809"
    mz = re.search(r'(\d{5})(?:-\d{4})?\s*$', rest)             # 5-digit zip (drop +4)
    zp = mz.group(1) if mz else ''
    rn = (rest[:mz.start()] if mz else rest).strip()           # "MIAMI FL"
    sm = re.search(r'\b([A-Za-z]{2})\s*$', rn)                  # trailing state
    st = sm.group(1).upper() if sm else 'FL'
    city = (rn[:sm.start()] if sm else rn).strip()
    csz = (city + ', ' + st + (' ' + zp if zp else '')).strip(' ,')
    return ("https://www.truepeoplesearch.com/resultaddress?streetaddress="
            + urllib.parse.quote(street) + "&citystatezip=" + urllib.parse.quote(csz))

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
    // tax-deed items show the folio as plain text in the Parcel ID cell (no link)
    if (!rec.Folio && rec['Parcel ID'] && /\\d/.test(rec['Parcel ID'])) rec.Folio = rec['Parcel ID'].replace(/\\D/g,'');
    // RealForeclose gives each auction item a stable id (aid="1506095"); #AITEM_<aid> scrolls the
    // day's auction page straight to THIS case, so the Auction link lands on the exact parcel.
    rec.AID = item.getAttribute('aid') || '';
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
    const m = txt.match(/(Foreclosure|Tax Deed)\\s+(\\d+)\\s*\\/\\s*(\\d+)/);
    if (dayid && m) days.push({date: dayid, remaining: parseInt(m[2]), saletype: m[1] === 'Tax Deed' ? 'TD' : 'FC'});
  });
  return JSON.stringify(days);
}
"""

def discover_dates(page, base=BASE):
    today = date.today()
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    dates = []
    for cal in [today, next_month]:
        page.goto(f"{base}?zaction=USER&zmethod=CALENDAR&selCalDate={cal:%m/%d/%Y}", timeout=45000)
        page.wait_for_selector('.CALBOX', timeout=20000)
        for d in json.loads(page.evaluate(CAL_JS)):
            dt = datetime.strptime(d['date'], '%m/%d/%Y').date()
            if dt >= today and d['remaining'] > 0 and d['date'] not in [x[0] for x in dates]:
                dates.append((d['date'], d['saletype']))
    print(f"auction dates found: {[f'{d} [{st}]' for d, st in dates]}")
    return dates   # list of (date, saletype)

def scrape_date(page, d, saletype='FC', attempt=1, base=BASE):
    page.goto(f"{base}?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE={d}", timeout=45000)
    # tax-deed lists render slower; give them a longer settle window
    tmo = 40000 if saletype == 'TD' else 25000
    try:
        page.wait_for_selector('#Area_W .AUCTION_DETAILS tr', timeout=tmo, state='attached')
    except Exception:
        if attempt == 1:
            return scrape_date(page, d, saletype, attempt=2, base=base)
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
    for rec in items:
        rec['AuctionDate'] = d
        rec['sale_type'] = saletype
    print(f"{d} [{saletype}]: {len(items)} pending (pages={pages})")
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
        for d, saletype in discover_dates(page):
            leads += scrape_date(page, d, saletype)
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

def _has_homestead(benefits):
    """True only for the real Homestead EXEMPTION. The PA 'Benefit' array also carries a
    'Non-Homestead Cap' assessment reduction (the cap for NON-homesteaded parcels) whose
    description literally contains the substring 'homestead' — so a naive `'homestead' in desc`
    falsely flags LLCs, rentals and second homes as owner-occupied. Require Type == Exemption
    and exclude the non-homestead cap. (Genuine homestead = Type 'Exemption', Desc 'Homestead'.)"""
    for b in (benefits or []):
        desc = (b.get('Description', '') or '').lower()
        typ = (b.get('Type', '') or '').strip().lower()
        if 'non-homestead' in desc or 'non homestead' in desc:
            continue
        if typ == 'exemption' and 'homestead' in desc:
            return True
    return False

def _valid_folio(s):
    """A real Miami-Dade folio is exactly 13 digits. Multi-parcel or blank entries (e.g. the county's
    'MULTIPLE PARCELS' placeholder) strip down to junk like '20' — reject those so we never fire a
    doomed PA lookup or build a broken Appraiser/Tax deep-link. Returns the 13-digit folio or ''."""
    f = re.sub(r'\D', '', s or '')
    return f if len(f) == 13 else ''

def enrich(leads):
    s = requests.Session(); s.headers['User-Agent'] = UA
    for i, r in enumerate(leads):
        folio = _valid_folio(r.get('Folio',''))
        r['enriched'] = False
        if not folio: continue   # skip non-parcel / multi-parcel rows (no real folio to look up)
        # ONE try/except that guards BOTH the fetch AND the parse — a single malformed PA response
        # (e.g. Assessment/AssessmentInfos returned as a string, PropertyInfo shape change) used to
        # AttributeError out here and kill the WHOLE enrich pass; now it just skips that one lead.
        try:
            d = s.get("https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx",
                params={"Operation":"GetPropertySearchByFolio","clientAppName":"PropertySearch","folioNumber":folio},
                timeout=20).json()
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
                'homestead': _has_homestead(benefits),
                'last_sale_price': last_sale.get('SalePrice',0), 'last_sale_date': last_sale.get('DateOfSale',''),
            })
        except Exception as e:
            print("PA fail", folio, e); time.sleep(1); continue
        if (i+1) % 20 == 0: print(f"enriched {i+1}/{len(leads)}")
        time.sleep(0.35)
    return leads

CLERK = "https://www2.miamidadeclerk.gov"

def classify(case_type, plaintiff):
    ct = (case_type or '').upper()
    pl = (plaintiff or '').upper()
    # A bank named "... National Association" would falsely match the HOA regex on "ASSOCIATION".
    # Strip that lender suffix before the HOA test; real HOAs are never "National Association".
    pl_h = re.sub(r'\bNATIONAL\s+ASSOCIATION\b', ' ', pl)
    if re.search(r'\b(ASSOCIATION|ASSN|CONDO|HOMEOWNER|MASTER ASSOC|HOA|TOWNHOM|VILLAS?|COMMUNITY)\b', pl_h):
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
        # tax-deed cases (e.g. 2026A00097) aren't in the civil OCS system - skip
        if r.get('sale_type') == 'TD' or not re.match(r'\d{4}-\d+-\w+-\d+', case):
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
            # PA owner needs a folio; a folio-less case still names the owner as the 1st defendant,
            # so recover it instead of showing a blank owner on an otherwise real, workable lead.
            if not (r.get('owners') or '').strip() and defs and defs[0]:
                r['owners'] = defs[0]
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
    # date-only "today" so a SAME-day auction shows 'in 0d' instead of '-1d'
    # (datetime.now() at 3pm minus AuctionDate parsed at 00:00 gives -0.6 days, .days floors to -1)
    today = datetime.combine(date.today(), datetime.min.time())
    for r in leads:
        td = (r.get('sale_type') == 'TD')
        mkt = r.get('market_value',0) or 0
        # TAX DEED: the money you pay is the Opening Bid, not a judgment. Title is unclean (needs quiet
        # title) and some liens survive - but for scoring, the spread is value - opening bid.
        if td:
            judg = money(r.get('Opening Bid',''))
            r['opening_bid'] = judg
            r['judgment'] = judg          # reuse the money plumbing (the tracker branches on sale_type)
            r['case_type'] = 'Tax Deed'
            r['judgment_unknown'] = False
            is_hoa = False
        else:
            judg = money(r.get('Final Judgment Amount',''))
            r['judgment'] = judg
            r['opening_bid'] = 0
            r['judgment_unknown'] = (judg == 0)
            case0 = r.get('Case #','')
            # HOA/junior signal: the case-number format (-CC-) OR the plaintiff-derived case_type.
            # Many HOA foreclosures are classified by plaintiff (case_type "HOA/Condo") without a
            # -CC- number, so keying only on the number missed them and left their fake equity scored.
            is_hoa = bool(re.search(r'-CC-', case0)) or (r.get('case_type','') or '').upper().startswith('HOA')
        r['equity'] = mkt - judg if mkt else 0
        r['equity_pct'] = round(r['equity']/mkt*100,1) if mkt else 0
        try: days = (datetime.strptime(r['AuctionDate'],'%m/%d/%Y') - today).days
        except: days = 0
        r['days_to_auction'] = days
        case = r.get('Case #','')
        fy = re.match(r'(\d{4})', case)
        r['filing_year'] = int(fy.group(1)) if fy else 0
        # a blank/$0 judgment = the debt isn't posted yet, NOT $0 owed. Don't credit full equity.
        if r['judgment_unknown']:
            r['equity'] = 0; r['equity_pct'] = 0
        r['warning'] = ('tax-deed: verify surviving liens (IRS 120d / municipal / HOA) + quiet title to resell' if td
                        else 'judgment not posted - debt unknown' if r['judgment_unknown']
                        else 'HOA/assoc case - verify senior mortgage on docket' if is_hoa else '')
        ep = r['equity_pct']
        # granular 0-100 so leads rank instead of clustering
        score = 0.0
        # Equity only counts when the judgment reflects the TRUE debt. For an HOA/junior (-CC-)
        # foreclosure the judgment is the tiny association lien, not the surviving 1st mortgage, so
        # "equity" is fake-high (a $13k lien on a $348k condo reads as 96%). Don't credit it there —
        # otherwise these unverifiable leads wrongly rank Tier A. (judgment_unknown already zeroed ep.)
        if mkt and not is_hoa: score += min(42.0, max(0.0, ep) * 0.42)   # equity, 0-42
        score += min(18.0, max(0, days) * 1.0)                    # runway, 0-18
        score += 12 if r.get('homestead') else 0                  # owner-occupied
        if 200000 <= mkt <= 1000000: score += 14                  # value band
        elif mkt > 1000000: score += 9
        elif mkt >= 150000: score += 6
        if r.get('enriched') and r.get('owners'): score += 8      # contactable
        elif r.get('enriched'): score += 4
        if is_hoa: score -= 6                                     # payoff uncertainty
        dq = []
        # for tax deeds the cheap parcels ARE the play (small opening bid vs value), so no low-value cut
        if not td and mkt and mkt < 100000: dq.append('low value')
        if mkt and ep < 15: dq.append('thin margin' if td else 'thin/negative equity')
        if not r.get('Address','').strip(): dq.append('no address')
        # city-only address ("HOMESTEAD, FL 33034") — can't be mailed, driven, or door-knocked, and
        # blank-street + huge-equity is a classic lien-trap profile. Never let it headline Tier A.
        elif not re.match(r'^\s*(?:\d[\d-]*|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)\s+\S',
                          r.get('Address',''), re.I): dq.append('no street address - verify parcel first')
        if not mkt: dq.append('no value data')
        if r['judgment_unknown']: dq.append('judgment not posted')
        r['score'] = round(score) if not dq else min(round(score), 40)
        r['disqualifiers'] = '; '.join(dq)
        r['tier'] = 'A' if r['score']>=70 and not dq else ('B' if r['score']>=50 and not dq else 'C')
        addr = r.get('Address','').replace(',',' ')
        r['zillow_url'] = 'https://www.zillow.com/homes/' + urllib.parse.quote(addr) + '_rb/' if addr.strip() else ''
        folio = _valid_folio(r.get('Folio',''))
        r['pa_url'] = ('https://apps.miamidadepa.gov/PropertySearch/#/?folio=' + folio) if folio else ''
        # No valid folio -> no Property Appraiser data (value/homestead/links). Two DIFFERENT honest
        # cases; don't lump them, and never show a broken folio link (pa_url/tax_url already blanked):
        if not folio:
            _pf = (str(r.get('Folio','')) + ' ' + str(r.get('Parcel ID',''))).upper()
            if 'MULTIPLE' in _pf:
                r['warning'] = 'multiple parcels - open the case / auction to view all properties'
            elif not r['warning']:
                # a real case whose parcel just wasn't linked: owner/value come from the docket, not PA
                r['warning'] = 'parcel not linked - verify property & value via the docket'
        _aid = str(r.get('AID', '') or '').strip()
        # #AITEM_<aid> deep-links to THIS case on the day's auction page; without an aid, still open the
        # correct day's list (never a broken link).
        r['auction_url'] = (f"{BASE}?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE={r.get('AuctionDate','')}"
                            + (f"#AITEM_{_aid}" if _aid else ""))
        # owner purchase year (from PA sales history)
        sd = re.search(r'(\d{4})$', (r.get('last_sale_date','') or '').strip())
        r['bought_year'] = int(sd.group(1)) if sd else 0
        # owner_clean = a clean "First [Middle] Last" name for the People/Records/Cases party searches.
        # Strip spouse markers ("&W HELEN"/"ET UX"), legal suffixes, and a dangling "&"; and normalize
        # the Clerk "Last, First M" format (folio-less leads recover the owner as defendant[0]) to
        # First-Last so the name searches don't come back reversed.
        _oc = (r.get('owners', '') or '').split(';')[0].strip()
        _oc = re.sub(r'\s*&\s*[WH]\b.*$', '', _oc, flags=re.I)
        _oc = re.sub(r'\b(ET\s?UX|ET\s?VIR|H/W|W/H|LE|REM|TRS|JR|SR|II|III|IV|ETAL|ET AL)\b', '', _oc, flags=re.I)
        _oc = re.sub(r'\s*&\s*$', '', _oc).strip()
        if ',' in _oc:
            _last, _, _rest = _oc.partition(',')
            _oc = (_rest.strip() + ' ' + _last.strip()).strip()
        r['owner_clean'] = re.sub(r'\s{2,}', ' ', _oc).strip()
        # Estimated ANNUAL property tax (the delinquent balance is Cloudflare-walled, not scrapable).
        # Miami-Dade aggregate millage ~2% of taxable value; homestead runs lower (exemptions + SOH cap).
        # Rough, clearly labeled in the UI as an estimate to verify via the Taxes link.
        _mv = r.get('market_value', 0) or 0
        r['est_annual_tax'] = round(_mv * (0.013 if r.get('homestead') else 0.021)) if _mv else 0
        # TruePeopleSearch prefill (companies get no People link). DataDome walls bots, so this only
        # pre-fills the search for ONE human click. Build the name from owner_clean — which already
        # strips the spouse ("&W HELEN"), suffixes, and flips the Clerk "Last, First" order — so we
        # search the actual OWNER, never a welded owner-first + spouse-first name ("JAMES HELEN") or a
        # reversed Last/First ("VASQUEZ A.").  (Bug reported by Jose 2026-07-14.)
        is_company = bool(re.search(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD)\b', r['owner_clean'], re.I))
        _pt = [t.strip('.') for t in r['owner_clean'].split()]
        _pt = [t for t in _pt if len(t) > 1]
        zm = re.search(r'(\d{5})\s*$', r.get('Address','') or '')
        if len(_pt) >= 2 and not is_company:
            name = _pt[0] + ' ' + _pt[-1]                   # First + last surname, from the CLEAN owner
            z = ('&citystatezip=' + zm.group(1)) if zm else ''
            r['people_url'] = "https://www.truepeoplesearch.com/results?name=" + urllib.parse.quote(name) + z
            r['people_name'] = name
            r['cyberbg_url'] = cyberbg_url(name, r.get('Address', ''))
        else:
            r['people_url'] = ''; r['people_name'] = ''; r['cyberbg_url'] = ''
        r['cyberbg_addr_url'] = cyberbg_addr_url(r.get('mailing_address', ''), r.get('Address', ''), is_company)
        # ADDRESS-based People search. A name search on TPS returns many same-name people and there is
        # no way to tell which is the owner (BatchData returns NO age/DOB — confirmed against the live
        # API). Searching by the ADDRESS instead returns the 1-3 people who actually live there, which
        # pinpoints the owner. Prefer the mailing address (where the owner actually lives, which matters
        # for absentee owners); fall back to the property address; skip PO boxes (address search is
        # useless on a box). TPS /resultaddress route, same domain as the name search.
        r['people_addr_url'] = people_addr_url(r.get('mailing_address', ''), r.get('Address', ''), is_company)
        # CO-PARTIES: every OTHER party named on the case, cleaned + deduped. Each human (co-owner,
        # spouse, relative living with the owner) gets its own People-search URL so you can reach them;
        # companies (bank/HOA/county/tenant) carry no URL and render muted for context. BatchData/TPS
        # can't auto-list relatives, but the case already names them — this surfaces + links them.
        _named, _seen = [], set()
        _octoks = [t for t in r['owner_clean'].split() if len(t) > 1]
        _primary_key = (_octoks[0].lower(), _octoks[-1].lower()) if len(_octoks) >= 2 else None
        for _raw in re.split(r'\s*;\s*', (r.get('defendants', '') or '')):
            _raw = _raw.strip()
            if not _raw:
                continue
            if _PARTY_JUNK.search(_raw):                        # bank/HOA/county/tenant -> show as-is, no link
                nm = re.sub(r'\s{2,}', ' ', _raw).strip(); key = ('co', nm.lower()); _url = ''
            else:                                               # person -> "First Last" + a People-search link
                nm = _clean_party(_raw)
                ptoks = [t.strip('.') for t in nm.split() if len(t.strip('.')) > 1]
                if len(ptoks) < 2:
                    continue
                key = (ptoks[0].lower(), ptoks[-1].lower())
                if key == _primary_key:
                    continue
                _sn = ptoks[0] + ' ' + ptoks[-1]
                _z = ('&citystatezip=' + zm.group(1)) if zm else ''
                _url = "https://www.truepeoplesearch.com/results?name=" + urllib.parse.quote(_sn) + _z
            if key in _seen:
                continue
            _seen.add(key)
            _named.append({'name': nm, 'url': _url})
        r['named'] = _named[:10]
        # case_type comes from the Clerk API (enrich_clerk); fall back to a heuristic if unresolved
        if not r.get('case_type'):
            r['case_type'] = 'HOA/Condo' if re.search(r'-CC-', r.get('Case #','')) else 'Mortgage/Other'
        # tax-collector DIRECT parcel page by folio (delinquent taxes/certs/full bill history).
        # Cloudflare-walled to scrape, so this is a reliable one-click deep-link straight to the parcel.
        r['tax_url'] = ('https://miamidade.county-taxes.com/public/real_estate/parcels/' + folio) if folio else ''
        # mortgage-risk: the judgment shown may be only ONE debt. Two ways a senior mortgage hides
        # behind apparent equity -> both force an Official Records lien check before trusting it:
        #  (a) HOA/condo judgment with a lender co-defendant (the tiny assoc lien, 1st mtg survives).
        #  (b) an INDIVIDUAL plaintiff (not a bank/servicer) on a mortgage foreclosure - almost always
        #      a private or 2nd-position note, so a bank 1st mortgage very likely survives unshown.
        defs = (r.get('defendants','') or '').upper()
        hoa_hidden_mtg = bool(r.get('case_type','').startswith('HOA') and re.search(
            r'BANK|MORTGAGE|LOAN|FINANCIAL|CAPITAL|FUNDING|LENDING|SERVICING|FEDERAL CREDIT|'
            r'FANNIE|FREDDIE|HOUSING AND URBAN|SECRETARY OF HOUSING|BANC|LENDER|\bN\.?A\.?\b|'
            r'CITIMORTGAGE|WELLS FARGO|CHASE|NATIONSTAR|PENNYMAC|NEWREZ|CARRINGTON|LAKEVIEW', defs))
        pl = (r.get('plaintiff', '') or '')
        _ent = re.search(r'\b(LLC|CORP|INC|MORTGAGE|LOAN|FINANC|CAPITAL|FUNDING|LENDING|SERVICING|'
                         r'TRUST|ASSOC|ASSN|FUND|HOLDINGS|LP|LTD|COMPANY|CREDIT UNION|FEDERAL|FANNIE|'
                         r'FREDDIE|HUD|SECRETARY|BANC|NATIONSTAR|PENNYMAC|NEWREZ|CARRINGTON|LAKEVIEW|'
                         r'SERIES|PARTNERS|GROUP|INVESTMENT|ENTERPRISE)\b', pl, re.I) \
               or re.search(r'BANK|\bSB\b|\bFSB\b|\bBK\b|\bN\.?A\.?\b', pl, re.I)   # compound bank names (Servbank, USBank)
        indiv_plaintiff = bool(pl) and not _ent and bool(re.search(r'[A-Za-z]{2},\s*[A-Za-z]{2}', pl))
        # "bank-like" plaintiff = an institutional lender whose judgment IS the senior debt (no hidden 1st).
        bank_like = bool(re.search(
            r'BANK|MORTGAGE|LENDING|SERVICING|FINANCIAL|SAVINGS|FEDERAL|FANNIE|FREDDIE|HUD|SECRETARY|'
            r'\bN\.?A\.?\b|\bFSB\b|\bSB\b|BANC|CREDIT UNION|NATIONSTAR|PENNYMAC|NEWREZ|CARRINGTON|LAKEVIEW|'
            r'WELLS FARGO|CHASE|CITI|ROCKET|FREEDOM|SELENE|SHELLPOINT|RUSHMORE|SPECIALIZED|MR COOPER|'
            r'CROSSCOUNTRY|LOANDEPOT|FLAGSTAR|\bLOAN\b', pl, re.I))
        # (c) any NON-bank plaintiff (individual OR private LLC/fund/trust) on an FC with real apparent
        #     equity -> the shown judgment is likely a private/junior note and a senior 1st mortgage
        #     probably survives unshown. Bias toward "verify via Official Records" (now one click).
        suspect_equity = (not td) and bool(pl) and (not bank_like) and (r.get('equity_pct', 0) or 0) >= 40
        # (d) TINY judgment relative to value on a RECENTLY-bought property — even from a name-brand
        #     bank. Nobody holds 80% equity a year after purchase, so a small bank judgment there is a
        #     junior/partial position (HELOC/2nd) with the senior 1st likely surviving unshown
        #     (e.g. $68k Nationstar judgment on a $356k house bought last year). LONG tenure is the
        #     honest exception: 15+ years explains a tiny judgment as a paid-down senior (a 1983 condo
        #     with an $18k Chase balance is plausibly REAL 90% equity), so those stay unflagged.
        _mv = r.get('market_value', 0) or 0
        _jd = r.get('judgment', 0) or 0
        _by = r.get('bought_year') or 0
        tiny_recent = (not td) and _mv > 0 and 0 < _jd and (_jd / _mv) < 0.20 \
                      and (r.get('equity_pct', 0) or 0) >= 40 and (not _by or _by >= today.year - 15)
        r['indiv_plaintiff'] = indiv_plaintiff
        r['mortgage_risk'] = bool(hoa_hidden_mtg or (not td and (indiv_plaintiff or suspect_equity or tiny_recent)))
        # eq_fake: the shown equity_pct is gross/unverified (HOA-junior lien or a hidden senior mortgage),
        # so the UI mutes it and sinks it on the Equity sort instead of ranking a $9k-lien lead as 98% equity.
        r['eq_fake'] = bool(is_hoa or r['mortgage_risk'])
        # MIXED-SIGNAL FIX ("why is it ranked A if you're telling me to verify it"): a lead whose
        # equity we just flagged as unverified must not headline Tier A on that same equity. Strip the
        # equity points scoring credited and re-tier — mirrors the county scrapers, which zero eff_eq
        # for eqfake before scoring. (is_hoa leads never received equity points, nothing to strip.)
        # (disqualified leads are already capped at 40/C — subtracting from the capped score would
        # over-penalize them, and there is no A/B mixed signal to fix there anyway)
        if r['eq_fake'] and not is_hoa and not td and not r.get('disqualifiers'):
            _pts = min(42.0, max(0.0, (r.get('equity_pct', 0) or 0)) * 0.42)
            r['score'] = max(0, round((r.get('score') or 0) - _pts))
            r['tier'] = 'A' if r['score'] >= 70 else ('B' if r['score'] >= 50 else 'C')
    return leads

def _clean_addr(s):
    # County/PA data formats the state as "FL- 33184" or "FL, 33184"; normalize to "FL 33184"
    # so addresses read cleanly everywhere (table, cards, copy, CSV, and the mailed letters).
    s = re.sub(r'\bFL[-,]\s*', 'FL ', s or '')
    return re.sub(r'\s{2,}', ' ', s).strip()

def _esc_json(obj):
    # Escape HTML-significant chars in embedded JSON so a county field containing "</script>"
    # can't break out of the inline <script> and inject/kill the page.
    return json.dumps(obj).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')

def _encrypt_payload(plaintext, password):
    """AES-GCM-256 with a PBKDF2-SHA256 key. Round-trips with the template's Web Crypto decrypt.
    Output is a small JSON object of base64 strings (no HTML-special chars)."""
    import base64
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = os.urandom(16), os.urandom(12)
    iters = 200000
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iters).derive(password.encode('utf-8'))
    ct = AESGCM(key).encrypt(iv, plaintext.encode('utf-8'), None)   # ciphertext has the 16-byte tag appended
    b64 = lambda x: base64.b64encode(x).decode()
    return {'enc': 1, 'it': iters, 'salt': b64(salt), 'iv': b64(iv), 'ct': b64(ct)}

def _decrypt_payload(env, password):
    """Inverse of _encrypt_payload (single-code PBKDF2 + AES-GCM envelope)."""
    import base64
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(env['salt']),
                     iterations=env['it']).derive(password.encode('utf-8'))
    return AESGCM(key).decrypt(base64.b64decode(env['iv']), base64.b64decode(env['ct']), None).decode('utf-8')

def _encrypt_multi(plaintext, codes):
    """Envelope encryption for PER-PERSON access codes, no backend needed. One random master key
    encrypts the payload once; that master key is then wrapped separately under EACH person's code
    (PBKDF2-SHA256 -> AES-GCM). Any valid code unwraps the master key and decrypts the same data.
    Revoke one person by dropping their line from site.codes + rebuilding. Labels are NOT emitted
    (the public file never reveals who has access). codes: list of (label, code)."""
    import base64
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    b64 = lambda x: base64.b64encode(x).decode()
    iters = 200000
    mk = os.urandom(32)                                   # random 256-bit master data key
    iv = os.urandom(12)
    ct = AESGCM(mk).encrypt(iv, plaintext.encode('utf-8'), None)
    keys = []
    for label, code in codes:
        salt, kiv = os.urandom(16), os.urandom(12)
        wk = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iters).derive(code.encode('utf-8'))
        # wrap {master key + this person's NAME} under their code. The name rides ENCRYPTED, so the
        # public file never reveals it, but their own code decrypts it -> personalized "welcome".
        blob = json.dumps({'mk': b64(mk), 'name': label}).encode('utf-8')
        wct = AESGCM(wk).encrypt(kiv, blob, None)
        keys.append({'salt': b64(salt), 'iv': b64(kiv), 'ct': b64(wct)})
    return {'enc': 2, 'it': iters, 'iv': b64(iv), 'ct': b64(ct), 'keys': keys}

def _load_codes():
    """Access entries for the shared site (site.codes, gitignored). Each line is either
        Label = CODE                 -> an individual code
        Label = CODE | PHRASE        -> a shared/team code that ALSO requires a secret phrase
    The wrap secret is CODE, or CODE + <unit-sep> + PHRASE when a phrase is set (both halves
    needed to unlock). Falls back to a single shared site.pass. Returns a list of (label, secret)."""
    SEP = '\x1f'
    codes_file = os.path.join(HERE, 'site.codes')
    if os.path.exists(codes_file):
        out = []
        for line in open(codes_file, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            label, rest = line.split('=', 1)
            label = label.strip()
            if '|' in rest:
                code, phrase = (x.strip() for x in rest.split('|', 1))
                secret = (code + SEP + phrase) if phrase else code
            else:
                secret = rest.strip()
            if secret:
                out.append((label or 'user', secret))
        if out:
            return out
    if os.path.exists(PASS_FILE):
        pw = open(PASS_FILE, encoding='utf-8').read().strip()
        if pw:
            return [('shared', pw)]
    return []

def _fc_type(case):
    """FALLBACK-ONLY classifier from the court case number, used when no plaintiff is available. The prefix
    is a POOR proxy — HOAs routinely foreclose in CIRCUIT court (a CACE number), so a CACE is NOT reliably a
    mortgage foreclosure. Prefer _fc_type_plaintiff() (the real signal) whenever a plaintiff name is known.
    Broward/PB: CACE=circuit, COCE/CONO/COWE/COSO=county. Miami-Dade: -CA-=circuit, -CC-=county."""
    c = (case or '').upper()
    if c.startswith('CACE') or '-CA-' in c: return 'MORTGAGE'
    if c.startswith(('COCE', 'CONO', 'COWE', 'COSO')) or '-CC-' in c: return 'HOA'
    return ''


# --- TRUE foreclosure type from the PLAINTIFF name (mirrors broward_liens._fc_type_plaintiff) ------
# Who is foreclosing decides the type, not the case-number prefix: HOAs sue in circuit court constantly.
# BANK-CHARTER GUARD WINS FIRST so a national-bank trustee ("U S BANK TRUST COMPANY NATIONAL ASSN") is
# never misread as an HOA just because its charter name ends in "ASSN".
_BANK_RE = re.compile(
    r'\bBANK\b|\bN\.?\s?A\.?\b|NATIONAL\s+ASS(?:N|OC(?:IATION)?)|\bTRUST(?!EES?\s+OF)|\bSAVINGS\b|'
    r'\bMORTGAGE\b|\bLOANS?\b|\bFINANCIAL\b|\bFUNDING\b|\bSERVICING\b|\bFEDERAL\b|CREDIT\s+UNION|'
    r'\bFANNIE\b|\bFREDDIE\b|\bFNMA\b|\bFHLMC\b', re.I)
_HOA_RE = re.compile(
    r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|\bMASTER\b|\bVILLAS?\b|COMMUNITY|PROPERTY\s+OWNERS?|'
    r'TOWNHO|MAINTENANCE', re.I)
_ASSN_RE = re.compile(r'(?<!NATIONAL\s)\bASS(?:N|OC(?:IATION)?)\b', re.I)
_LENDER_CORP_RE = re.compile(r'\bLLC\b|\bL\.?\s?P\.?\b|\bLLP\b', re.I)


def _fc_type_plaintiff(plaintiff):
    """'MORTGAGE' | 'HOA' | '' from a foreclosure plaintiff name. Bank-charter guard wins first."""
    p = (plaintiff or '').upper()
    if not p.strip():
        return ''
    if _BANK_RE.search(p):
        return 'MORTGAGE'
    if _HOA_RE.search(p) or _ASSN_RE.search(p):
        return 'HOA'
    if _LENDER_CORP_RE.search(p):
        return 'MORTGAGE'
    return ''


def _fwd_flags(d, h, ftype):
    """Bake the deal-killer flags from a lien result (records_liens/broward_liens) onto a slim lead. Missing
    keys (e.g. Miami-Dade records that predate the flag fields) are simply skipped."""
    d['orftype'] = h.get('ftype') or ftype
    if h.get('surv'): d['orsurv'] = h.get('surv', 0)                 # total open mortgage that survives an HOA sale
    if h.get('surv_first'): d['orsurvfirst'] = h.get('surv_first', 0)  # the first mortgage (headline number)
    if h.get('deeded'):                                             # already deeded to another investor
        d['ordeeded'] = h['deeded']; d['ordeedconf'] = h.get('deed_conf', '')
    if h.get('second_fc'): d['orsecond'] = h['second_fc']           # a separate CACE mortgage foreclosure


def make_tracker(leads):
    # merge locally skip-traced phones/emails (never fetched here; produced by skiptrace.py, gitignored)
    st = {}
    if os.path.exists(RESULTS_FILE):
        try: st = json.load(open(RESULTS_FILE, encoding='utf-8'))
        except Exception: st = {}
    # direct-to-results OCS "Cases" tokens per owner (produced by gen_cases_qs.py, gitignored)
    cq = {}
    _cqf = os.path.join(HERE, 'cases_qs.json')
    if os.path.exists(_cqf):
        try: cq = json.load(open(_cqf, encoding='utf-8'))
        except Exception: cq = {}
    # direct-to-results Official Records tokens per owner (produced by gen_records_qs.py, gitignored)
    rq = {}
    _rqf = os.path.join(HERE, 'records_qs.json')
    if os.path.exists(_rqf):
        try: rq = json.load(open(_rqf, encoding='utf-8'))
        except Exception: rq = {}
    # recorded open-mortgage chain per lead (produced by records_liens.py, gitignored) — turns the equity
    # number from a guess into fact by surfacing the real surviving 2nd mortgage.
    rl = {}
    _rlf = os.path.join(HERE, 'records_liens.json')
    if os.path.exists(_rlf):
        try: rl = json.load(open(_rlf, encoding='utf-8'))
        except Exception: rl = {}
    # per-parcel deep tax-account links (produced by gen_tax_links.py, gitignored) — replaces the
    # generic county-taxes landing page with the parcel's own account URL, keyed by case #.
    taxlinks = {}
    _tlf = os.path.join(HERE, 'tax_links.json')
    if os.path.exists(_tlf):
        try: taxlinks = json.load(open(_tlf, encoding='utf-8'))
        except Exception: taxlinks = {}
    # radius comparable-sales per lead (produced by comps.py, gitignored) — ARV from median comp
    # $/sqft x subject sqft with the 3 nearest sales for the deal modal + dispo pack.
    comps = {}
    _cf = os.path.join(HERE, 'comps.json')
    if os.path.exists(_cf):
        try: comps = json.load(open(_cf, encoding='utf-8'))
        except Exception: comps = {}
    slim = []
    for r in leads:
        _ft = _fc_type(r.get('Case #', ''))          # HOA (whole 1st mortgage survives) vs MORTGAGE foreclosure
        d = {
            'tier': r.get('tier',''), 'score': r.get('score',0),
            'auction': r.get('AuctionDate',''), 'days': r.get('days_to_auction',0),
            'case': r.get('Case #',''), 'owners': r.get('owners',''),
            'addr': _clean_addr(r.get('Address','')), 'mail': _clean_addr(r.get('mailing_address','')),
            'value': r.get('market_value',0) or 0, 'judg': r.get('judgment',0) or 0,
            'eq': r.get('equity_pct',0), 'eqfake': bool(r.get('eq_fake')), 'hs': bool(r.get('homestead')),
            # condo -> the displayed equity is a GROSS upper bound: a special assessment (40-yr recert) or a
            # 2nd mortgage can erase it and neither is in public data. Drives the "verify equity" caveat + a
            # MARGINAL cap until the association estoppel is entered. (Lesson from the Hondroulis condo deal.)
            # Full dor_desc string carried through so the row's property-type chip (_ptype in the
            # template) can render "Single Family" / "Townhouse" / etc. on Miami-Dade rows. Without
            # this pass-through the JS side sees no dor_desc and renders no chip for MD leads
            # (BW/PB rows already come through the slim.extend path with dor_desc intact).
            'dor_desc': r.get('dor_desc',''),
            # Zillow listing status (listing_status.py): LISTED/PENDING/SOLD/RENTAL/OFF-MARKET +
            # asking price + days-on-Zillow. County rows pass through via slim.extend; MD needs
            # the explicit copy just like dor_desc/photos.
            'zstatus': r.get('zstatus',''), 'zprice': r.get('zprice',0) or 0, 'zdoz': r.get('zdoz',0) or 0,
            'condo': bool(re.search(r'CONDO', str(r.get('dor_desc','') or ''), re.I)),
            # VACANT LAND (no homeowner + speculative land value) and COMPANY-OWNED — systematic
            # false-positives for the homeowner-rescue model; badged in the UI so a big-equity vacant
            # lot / LLC (e.g. Ocean Breeze 777 LLC's $2.1M raw lots) can't masquerade as a live lead.
            'vac': bool(re.search(r'VACANT', str(r.get('dor_desc','') or ''), re.I)),
            'co': bool(re.search(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|PROPERT|REALTY|CAPITAL|GROUP|INVEST|EQUIT)\b', str(r.get('owners','') or ''), re.I)),
            'zillow': r.get('zillow_url',''), 'pa': r.get('pa_url',''),
            # property photos (from property_photos.py). County leads pass through via slim.extend, but the
            # Miami-Dade dict is rebuilt with explicit keys, so photos MUST be copied here or every MD lead
            # loses its image.
            'photos': r.get('photos', []) or [], 'zlisting': r.get('zlisting',''), 'photo_kind': r.get('photo_kind',''),
            'aurl': r.get('aurl',''),   # absolute Esri fallback so a bare emailed HTML still shows photos
            'auc': r.get('auction_url',''), 'warn': r.get('warning',''),
            'filed': r.get('filing_year',0),
            # sale-history survival count (sale_history.py) — the REAL staller signal from the docket:
            # how many scheduled foreclosure sales this owner has already dodged (cancelled/reset).
            'saleSurv': r.get('sale_survived', None), 'saleSched': r.get('sale_scheduled', 0),
            # who keeps postponing, when the docket names the movant (usually it doesn't):
            # 'bank' = plaintiff loss-mit churn (owner may still be rescuable), 'owner' = fights.
            'saleWho': r.get('sale_who', ''),
            # DISTINCT bankruptcy filings on the docket — Jose's heaviest staller screen ("3-4
            # bankruptcies = they know the game"); the automatic stay halts sales with no order.
            'saleBK': r.get('sale_bk', 0),
            # ACTIVE automatic stay (11 U.S.C. §362) — a bankruptcy filing with no dismissal /
            # discharge / stay-relief after it. Collection contact right now is a federal
            # violation: the site hard-gates outreach on this (sale_history.py _bk_active).
            'saleBkAct': bool(r.get('sale_bk_active')), 'saleBkD': r.get('sale_bk_date', ''),
            'bought': r.get('bought_year',0), 'bprice': r.get('last_sale_price',0) or 0,
            'people': r.get('people_url',''), 'peopleaddr': r.get('people_addr_url',''), 'cyberbg': r.get('cyberbg_url',''), 'cyberbgaddr': r.get('cyberbg_addr_url',''), 'ctype': r.get('case_type',''),
            'plaintiff': r.get('plaintiff',''), 'defs': r.get('defendants',''),
            'named': r.get('named', []),   # [{name,url}] co-parties: humans get a People-search URL, companies ''
            'docket': r.get('docket_url',''), 'tax': r.get('tax_url',''),
            'cstatus': r.get('case_status',''), 'mr': bool(r.get('mortgage_risk')) or _ft == 'HOA', 'ftype': _ft,
            'ip': bool(r.get('indiv_plaintiff')), 'oname': r.get('owner_clean',''),
            'ocsqs': cq.get(r.get('owner_clean',''), ''), 'recqs': rq.get(r.get('owner_clean',''), ''),
            'etax': r.get('est_annual_tax',0),
            'ju': bool(r.get('judgment_unknown')),
            'st': r.get('sale_type','FC'), 'obid': r.get('opening_bid',0) or 0,
            'cert': r.get('Certificate #',''),
            'folio': _valid_folio(r.get('Folio','')),   # lets the in-site property lookup cross-check any parcel against this auction list
        }
        rlh = rl.get(r.get('Case #',''))
        if rlh and rlh.get('liens'):
            d['orliens'] = rlh.get('liens', [])          # the recorded mortgage chain (open/satisfied + amounts)
            d['orjunior'] = rlh.get('junior', 0)         # suggested surviving 2nd (open mtgs beyond the foreclosing 1st)
            d['orconf'] = rlh.get('conf', '')            # 'ok' = isolated + sane; 'low' = common name / verify
            # kimi: non-mortgage open liens + junior-payoff split for the deal-modal prefills
            d['orhoa'] = rlh.get('hoa_open', 0); d['orcode'] = rlh.get('code_open', 0)
            d['orirs'] = rlh.get('irs_open', 0); d['orjuniors'] = rlh.get('juniors_post', 0)
        if rlh:
            _fwd_flags(d, rlh, _ft)                       # surviving-1st / TAKEN / 2nd-foreclosure flags
            # JUNIOR-FORECLOSURE GUARD (the Echeverri lesson, MD side): the traced chain shows an
            # OPEN mortgage beyond the foreclosing one -> the headline equity_pct is GROSS (that
            # other note survives the sale or must be paid off at purchase). Flag it so the row
            # renders "~88% eq" with the verify tooltip instead of stating fantasy as fact. The
            # county merge path has had this guard since the Hondroulis condo; MD never did —
            # which is how an 88%-equity headline sat on a $142.5k-senior junior foreclosure.
            if (rlh.get('junior') or rlh.get('surv')) and not d.get('eqfake'):
                d['eqfake'] = True
        hit = st.get(r.get('Case #',''))
        if hit and hit.get('phones'):
            d['phones'] = [p.get('number') for p in hit['phones'] if p.get('number')][:4]
            d['phdnc'] = [bool(p.get('dnc')) for p in hit['phones']][:4]
            d['emails'] = (hit.get('emails') or [])[:3]
        # Radius comps (comps.py MD path via the county's own MD_ComparableSales layer) — same
        # merge the BW/PB loop below does; without this, MD rows never showed an ARV.
        _cp = comps.get(r.get('Case #',''))
        if _cp:
            d['arv'] = _cp.get('arv', 0); d['arvconf'] = _cp.get('conf', '')
            d['arvpsf'] = _cp.get('psf', 0); d['arvn'] = _cp.get('n', 0)
            d['comps'] = _cp.get('comps', [])
        d['county'] = 'MIAMI-DADE'
        slim.append(d)

    # Merge other counties: any <county>_leads.json (produced by county_leads.py — already slim + county-tagged).
    import glob as _glob
    for _xf in sorted(_glob.glob(os.path.join(HERE, '*_leads.json'))):
        _bn = os.path.basename(_xf)
        # skip the MD files and any scratch/backup (_-prefixed) file so a stray _bak_*_leads.json can't
        # get double-merged into the site.
        if _bn in ('leads_final.json', 'leads_raw.json') or _bn.startswith('_'):
            continue
        try:
            xl = json.load(open(_xf, encoding='utf-8'))
            # Bake the recorded lien chain for this county if a sibling <county>_liens.json exists
            # (broward_liens.py etc.) — same schema/fields as Miami-Dade's records_liens merge above.
            _lf = _xf[:-len('_leads.json')] + '_liens.json'
            xrl = {}
            if os.path.exists(_lf):
                try: xrl = json.load(open(_lf, encoding='utf-8'))
                except Exception: xrl = {}
            for _d in xl:
                _h = xrl.get(_d.get('case', ''))
                # Tax link priority (2026-07-20): a real per-parcel /parcels/.../bills deep-link the
                # county appraiser's own Tax Collector button opens (now set in county_leads.py from the
                # folio) is the BEST link — it lands on the actual bill. The old gen_tax_links.py Algolia
                # token (county-taxes.net/.../{base64 :parents: uuid}) only reaches a disambiguation page
                # (verified: HTTP 200 but no bill), so it must NOT override the appraiser deep-link. Only
                # use the token link when the lead has no proper per-parcel URL yet.
                _tx = taxlinks.get(_d.get('case', ''))
                _cur = _d.get('tax', '') or ''
                if _tx and '/parcels/' not in _cur:
                    _d['tax'] = _tx
                # Radius comps (comps.py): ARV + nearest sales for the modal/pack.
                _cp = comps.get(_d.get('case', ''))
                if _cp:
                    _d['arv'] = _cp.get('arv', 0); _d['arvconf'] = _cp.get('conf', '')
                    _d['arvpsf'] = _cp.get('psf', 0); _d['arvn'] = _cp.get('n', 0)
                    _d['comps'] = _cp.get('comps', [])
                # TRUE type: the recorded-chain plaintiff (broward_liens.analyze -> _h['ftype']) is
                # authoritative and OVERRIDES the case-number prefix, which mislabels HOA-in-circuit-court
                # cases (CACE) as MORTGAGE. The slim lead's own plaintiff-or-prefix guess is the next
                # fallback (when no chain was traced), then the bare prefix as a last resort.
                _cft = (_h.get('ftype') if _h else '') or _d.get('ftype') or _fc_type(_d.get('case', ''))
                if _cft == 'HOA':
                    _d['ftype'] = 'HOA'; _d['ctype'] = 'HOA'; _d['mr'] = True   # whole 1st mortgage survives an HOA sale
                    # ...and the shown equity is fantasy (the 1st survives), so zero it for score/tier —
                    # a chain-CONFIRMED HOA in the 20-60% judgment band would otherwise still headline
                    # Tier A on the equity sort (mirrors county to_slim + the MD path).
                    _d['eqfake'] = True
                    _db = 10 if (isinstance(_d.get('days'), int) and 0 <= _d['days'] <= 30) else 0
                    _d['score'] = (max(0, min(100, (10 if _d.get('hs') else 0) + _db)) if _d.get('value') else 0)
                    _d['tier'] = 'C'
                elif _cft == 'MORTGAGE':
                    _d['ftype'] = 'MORTGAGE'
                    # Clear mortgage-risk ONLY when the RECORDED CHAIN (_h) verified a bank foreclosure
                    # AND the judgment is a plausible SENIOR amount. Two guards, both required:
                    #  1. a bare case-prefix 'MORTGAGE' guess (CACE) must not clear the flag — HOAs
                    #     foreclose in circuit court constantly, so an untraced CACE stays flagged.
                    #  2. even a chain-confirmed bank plaintiff foreclosing a TINY judgment (<20% of value
                    #     with 40%+ apparent equity) is almost always a junior/partial position (HELOC/2nd)
                    #     with the 1st surviving — keep it flagged until Official Records prove otherwise.
                    #     ($29k judgment on a $1.2M house is fantasy equity even if a bank filed it.)
                    _v, _j, _e = _d.get('value', 0) or 0, _d.get('judg', 0) or 0, _d.get('eq', 0) or 0
                    _suspect_ratio = bool(_v) and _j > 0 and (_j / _v) < 0.20 and _e >= 40
                    if _h and _h.get('ftype') == 'MORTGAGE' and not _h.get('surv') and not _suspect_ratio:
                        _d['mr'] = False; _d['eqfake'] = False                  # verified real senior equity
                        if (_d.get('ctype') or '').upper().startswith('HOA'): _d['ctype'] = 'Bank/Mortgage'
                        # Mirror the HOA-side downward re-tier — but UPWARD: the pipeline had ZEROED
                        # this lead's equity points on the fantasy-equity flag, so a chain-verified
                        # real-equity mortgage stays stuck at Tier C until we credit them back in.
                        # Rebuild using the same formula as MD qualify() so cross-county tiers align.
                        _v = _d.get('value') or 0
                        _e = _d.get('eq') or 0     # true equity_pct (already computed for the county lead)
                        if _v:
                            _s = min(42.0, max(0.0, _e) * 0.42)                 # equity, 0-42
                            _dd = _d.get('days', -1)
                            _s += min(18.0, max(0, _dd) * 1.0) if isinstance(_dd, int) else 0
                            _s += 12 if _d.get('hs') else 0
                            _s += 14 if 200000 <= _v <= 1000000 else (9 if _v > 1000000 else (6 if _v >= 150000 else 0))
                            # 'enriched'/'owners' equivalent: county leads always come pre-enriched
                            _s += 8 if _d.get('oname') else 4
                            _d['score'] = round(_s)
                            _d['tier'] = 'A' if _d['score'] >= 70 else ('B' if _d['score'] >= 50 else 'C')
                if _h and _h.get('liens'):
                    _d['orliens'] = _h.get('liens', [])
                    _d['orjunior'] = _h.get('junior', 0)
                    _d['orconf'] = _h.get('conf', '')
                    # kimi: non-mortgage open liens + junior-payoff split for the deal-modal prefills
                    _d['orhoa'] = _h.get('hoa_open', 0); _d['orcode'] = _h.get('code_open', 0)
                    _d['orirs'] = _h.get('irs_open', 0); _d['orjuniors'] = _h.get('juniors_post', 0)
                if _h:
                    _fwd_flags(_d, _h, _cft)                          # surviving-1st / TAKEN / 2nd-foreclosure flags
                # skip-traced phones/emails for this county lead (skiptrace.py now covers all counties)
                _ph = st.get(_d.get('case', ''))
                if _ph and _ph.get('phones'):
                    _d['phones'] = [p.get('number') for p in _ph['phones'] if p.get('number')][:4]
                    _d['phdnc'] = [bool(p.get('dnc')) for p in _ph['phones']][:4]
                    _d['emails'] = (_ph.get('emails') or [])[:3]
            slim.extend(xl)
            _nl = sum(1 for _d in xl if _d.get('orliens'))
            _np = sum(1 for _d in xl if _d.get('phones'))
            print(f"merged {len(xl)} leads from {os.path.basename(_xf)}" +
                  (f" ({_nl} with lien chains)" if _nl else "") + (f" ({_np} with phones)" if _np else ""))
        except Exception as e:
            print(f"skip {_xf}: {e}")

    tpl = open(os.path.join(HERE,'tracker_template.html'), encoding='utf-8').read().replace('__UPDATED__', f"{datetime.now():%Y-%m-%d %H:%M}")
    os.makedirs(os.path.join(HERE,'docs'), exist_ok=True)
    docs = os.path.join(HERE,'docs','index.html')

    # Desktop copy: always PLAINTEXT with phones (local machine, Alejandro's own use).
    # Skipped in CI (DEALFLOW_NO_DESKTOP=1): the OneDrive path is meaningless on a runner and would
    # just pollute the checkout with a junk "C:\Users\..." directory + duplicate photo copies.
    if os.environ.get('DEALFLOW_NO_DESKTOP') != '1':
        os.makedirs(DEALFLOW_DIR, exist_ok=True)
        desktop = os.path.join(DEALFLOW_DIR,'Foreclosure Lead Tracker.html')

        # Guarded: if OneDrive has the Desktop HTML open/locked, don't let a PermissionError abort the whole
        # build (which would also skip the docs/index.html publish below). Warn and keep going.
        try:
            open(desktop,'w',encoding='utf-8').write(tpl.replace('__DATA__', _esc_json(slim)))
        except Exception as e:
            print(f"WARN: could not write Desktop copy ({e}) - is it open? continuing to publish docs/index.html")

        # P0: the template references photos as relative 'img/<name>.jpg', which only resolves next to
        # docs/index.html (docs/img/). Ship the referenced files beside the Desktop copy too, or every
        # image in the investor-facing file is a broken grey box. Idempotent (size-compare) + fail-soft
        # per file so a locked OneDrive handle can never kill the build.
        try:
            srcdir = os.path.join(HERE, 'docs', 'img')
            dstdir = os.path.join(DEALFLOW_DIR, 'img')
            os.makedirs(dstdir, exist_ok=True)
            names = {p.split('/', 1)[1] for d in slim for p in (d.get('photos') or [])
                     if isinstance(p, str) and p.startswith('img/')}
            n_copied = 0
            for name in names:
                s, t = os.path.join(srcdir, name), os.path.join(dstdir, name)
                try:
                    if os.path.exists(s) and (not os.path.exists(t) or os.path.getsize(t) != os.path.getsize(s)):
                        shutil.copy2(s, t); n_copied += 1
                except Exception:
                    pass
            if n_copied: print(f"copied {n_copied} photos -> DEALFLOW\\img")
        except Exception as e:
            print('photo copy to DEALFLOW skipped:', e)

    # Shared docs/index.html: ENCRYPTED (with phones) when a site.pass exists, else PLAINTEXT with
    # phones STRIPPED. This guarantees personal phone numbers never hit the public web unencrypted.
    codes = _load_codes()
    _dst = '' if os.environ.get('DEALFLOW_NO_DESKTOP') == '1' else ' + Desktop (plaintext)'
    if codes:
        enc = _encrypt_multi(json.dumps(slim), codes)
        open(docs,'w',encoding='utf-8').write(tpl.replace('__DATA__', json.dumps(enc)))
        print(f'tracker written: docs/index.html (ENCRYPTED · {len(codes)} access code(s)){_dst}')
    else:
        nophone = [{k: v for k, v in d.items() if k not in ('phones','phdnc','emails')} for d in slim]
        open(docs,'w',encoding='utf-8').write(tpl.replace('__DATA__', _esc_json(nophone)))
        print('tracker written: docs/index.html (public, phone-free)' + ('' if os.environ.get('DEALFLOW_NO_DESKTOP') == '1' else ' + Desktop'))

def main():
    leads = scrape()
    print(f"scraped {len(leads)} pending auctions")
    # Guard the live site: a broken/blocked scrape must never overwrite a good tracker with an
    # empty one. Bail before regenerating anything (leads_*.json are gitignored, so nothing commits).
    if len(leads) < 20:
        print(f"ABORT: only {len(leads)} leads scraped (expected 100+). Not regenerating the site.")
        sys.exit(1)
    # Defensive dedupe: the calendar can list the same auction item twice. Collapse exact repeats
    # (same case + folio + auction date) so a duplicate never becomes two rows in the tracker.
    seen, deduped = set(), []
    for r in leads:
        key = (r.get('Case #','').strip(), r.get('Folio','').strip(), r.get('AuctionDate','').strip())
        if key in seen: continue
        seen.add(key); deduped.append(r)
    if len(deduped) < len(leads):
        print(f"deduped {len(leads) - len(deduped)} exact-duplicate row(s)")
    leads = deduped
    json.dump(leads, open(os.path.join(HERE,'leads_raw.json'),'w'), indent=1)
    leads = enrich(leads)
    leads = enrich_clerk(leads)
    leads = qualify(leads)
    leads.sort(key=lambda r: -r['score'])
    # Preserve photos across the refresh: this fresh scrape has no photo fields, so without this
    # every returning property would revert to a placeholder until the (slow, sometimes-killed)
    # photo pass finishes. Carry them from the previous snapshot BEFORE we overwrite it.
    from photo_carry import carry_photos
    _carried = carry_photos(leads, os.path.join(HERE,'leads_final.json'))
    if _carried: print(f"carried photos forward for {_carried} returning leads")
    # Zillow seed (photo_seed.enc, committed ciphertext): listing photos harvested locally over a
    # residential connection — Zillow blocks the GHA datacenter IP, so without this floor every CI
    # build silently downgrades returning leads from listing photos to Street View/aerials (seen
    # 2026-07-19: 120 leads lost their Zillow sets in one run). Decrypt with the first site code
    # (present locally AND via the SITE_CODES secret in CI) and carry with PREFERENCE — a listing
    # photo outranks a Street View / aerial fallback even when the lead already has one.
    try:
        _seed_f = os.path.join(HERE, 'photo_seed.enc')
        if os.path.exists(_seed_f) and _load_codes():
            _code = _load_codes()[0][1].split('\x1f')[0]
            _tmp = os.path.join(HERE, '_photo_seed.json')          # gitignored working copy
            json.dump(json.loads(_decrypt_payload(json.load(open(_seed_f, encoding='utf-8')), _code)),
                      open(_tmp, 'w', encoding='utf-8'))
            _zc = carry_photos(leads, _tmp, prefer=('zillow',))
            if _zc: print(f"zillow seed: listing photos restored for {_zc} leads")
    except Exception as _e:
        print('zillow seed skipped:', _e)
    json.dump(leads, open(os.path.join(HERE,'leads_final.json'),'w'), indent=1)
    make_tracker(leads)
    cols = ['tier','score','sale_type','AuctionDate','days_to_auction','Case #','opening_bid','filing_year','owners','Address','mailing_address',
            'market_value','judgment','equity','equity_pct','homestead','case_type','warning','dor_desc','beds','baths',
            'living_area','last_sale_price','last_sale_date','year_folio','zillow_url','pa_url','disqualifiers']
    # Skip the daily CSV on GHA — same reason as the Desktop tracker copy above:
    # DEALFLOW_DIR resolves to a Windows path that would create a literal 'C:\\Users\\...'
    # directory in the runner workspace. Local runs still get the CSV as before.
    if os.environ.get('DEALFLOW_NO_DESKTOP') != '1':
        os.makedirs(DEALFLOW_DIR, exist_ok=True)
        out_csv = os.path.join(DEALFLOW_DIR, f"Miami-Dade Foreclosure Leads - {date.today():%Y-%m-%d}.csv")
        with open(out_csv,'w',newline='',encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
            w.writeheader()
            for r in leads: w.writerow(r)
    else:
        out_csv = '(skipped in CI)'
    a = sum(1 for r in leads if r['tier']=='A'); b = sum(1 for r in leads if r['tier']=='B')
    fc = sum(1 for r in leads if r.get('sale_type')!='TD'); td = sum(1 for r in leads if r.get('sale_type')=='TD')
    print(f"DONE: {len(leads)} leads ({fc} foreclosure, {td} tax deed) | Tier A: {a} | Tier B: {b}")
    print(f"CSV: {out_csv}")

if __name__ == '__main__':
    main()
