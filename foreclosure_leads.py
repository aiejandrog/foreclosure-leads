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
import json, re, time, csv, os, sys, shutil, hashlib, math, urllib.parse
from datetime import datetime, date, timedelta
import requests
from playwright.sync_api import sync_playwright
import paths as P
import equity_state as _es

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = P.DESKTOP
# Where the plaintext tracker + daily CSV land. MOVED OFF ONEDRIVE 2026-08-22 -- this file is the
# ungated board WITH phone numbers, and it was being replicated to consumer cloud storage on every
# refresh. paths.py owns the location now; GitHub Actions still overrides DEALFLOW_DIR to a
# throwaway tmp path (the Linux runner has no Desktop, and the img copy would otherwise create a
# literal 'C:\\Users\\...' directory in the workspace).
DEALFLOW_DIR = P.DEALFLOW_DIR
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

AUCTION_HORIZON_DAYS = int(os.environ.get('DEALFLOW_AUCTION_HORIZON_DAYS', '120'))


def _month_starts(start, horizon_days):
    """First-of-month for every calendar month overlapping [start, start+horizon_days]."""
    end = start + timedelta(days=horizon_days)
    out, cur = [], start.replace(day=1)
    while cur <= end:
        out.append(cur)
        cur = (cur + timedelta(days=32)).replace(day=1)
    return out


def discover_dates(page, base=BASE, horizon_days=None):
    """Auction dates from today out to a ROLLING horizon (default 120 days).

    WHY THIS IS NOT TWO CALENDAR MONTHS ANY MORE (fixed 2026-08-15). It used to fetch exactly
    [this month, next month], which is a SAWTOOTH, not a window: the front edge advances a day at a
    time while the back edge stays pinned to the last day of next month, then jumps a whole month on
    the 1st. Measured effect on the Miami-Dade board — 318 leads on 08/02 down to 270 on 08/15, an
    almost monotone decline, with 67% of the board being the same cases as eleven days earlier. That
    is the single biggest mechanical reason the operator kept seeing the same people: the pool was
    shrinking daily and only refilled once a month.

    A day-based horizon makes the back edge move every day like the front one does.
    Tune without a code change via DEALFLOW_AUCTION_HORIZON_DAYS.
    """
    today = date.today()
    horizon = int(horizon_days or AUCTION_HORIZON_DAYS)
    last = today + timedelta(days=horizon)
    dates, seen = [], set()
    for idx, cal in enumerate(_month_starts(today, horizon)):
        page.goto(f"{base}?zaction=USER&zmethod=CALENDAR&selCalDate={cal:%m/%d/%Y}", timeout=45000)
        try:
            page.wait_for_selector('.CALBOX', timeout=20000)
        except Exception:
            # Months beyond what the county has published render no calendar at all. That is the
            # normal end of the horizon, not a failure — the old two-month loop never reached far
            # enough to hit it, so an unguarded wait here would abort the whole nightly scrape.
            #
            # But the CURRENT month always exists. If even that has no calendar we are blocked,
            # pointed at a bad host, or the markup changed — and returning [] there would make the
            # caller scrape zero dates and publish an EMPTY board while reporting success. Fail loud
            # instead: county_leads.py catches this per-platform and logs "calendar failed", and a
            # single-county run aborts rather than silently shipping nothing.
            if idx == 0:
                raise RuntimeError(
                    f"no auction calendar at {base} for the CURRENT month ({cal:%Y-%m}) — "
                    f"blocked, wrong host, or .CALBOX markup changed")
            print(f"  no calendar published for {cal:%Y-%m} — stopping horizon scan")
            break
        for d in json.loads(page.evaluate(CAL_JS)):
            dt = datetime.strptime(d['date'], '%m/%d/%Y').date()
            if today <= dt <= last and d['remaining'] > 0 and d['date'] not in seen:
                seen.add(d['date'])
                dates.append((d['date'], d['saletype']))
    dates.sort(key=lambda x: datetime.strptime(x[0], '%m/%d/%Y').date())
    print(f"auction dates found ({horizon}d horizon, {len(dates)} dates): "
          f"{[f'{d} [{st}]' for d, st in dates]}")
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

def _has_widow(benefits):
    """Widow/widower exemption (FS 196.202). NOT an age determination — it is the only
    age-correlated signal the PA actually exposes today (verified 2026-08: the live Benefit array
    carries only Homestead / Second Homestead / Save Our Homes Cap / Widow — Florida's senior
    exemption FS 196.075 does NOT appear). Feeds the board's ELDER? chip, which is deliberately
    over-inclusive: a false positive costs a rep some extra courtesy, a false negative costs a
    first-degree felony under FS 825.103. See Playbook §0.5."""
    for b in (benefits or []):
        desc = (b.get('Description', '') or '').lower()
        typ = (b.get('Type', '') or '').strip().lower()
        if typ == 'exemption' and ('widow' in desc):
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
        if not folio:
            # STUB HOOK: a case the resolver has already tied to a parcel (stub_folios.json —
            # defendant-name -> appraiser roll, corroborated; see stub_resolve.py). The auction
            # published nothing for these, so without this line they re-enter every nightly as
            # value-less 'parcel not linked' rows forever.
            try:
                import stub_resolve
                _sf = _valid_folio(stub_resolve.folio_for(r.get('Case #', '')))
                if _sf:
                    r['Folio'] = _sf
                    folio = _sf
            except Exception:
                pass
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
                'widow': _has_widow(benefits),
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
    # Load comps once so the score can basis on ARV when a confident one exists — must match the
    # basis _basisOf() picks on the board, or ranking and money disagree by county-vs-ARV.
    try: comps = json.load(open(os.path.join(HERE, 'comps.json'), encoding='utf-8'))
    except Exception: comps = {}
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
        # Score against the SAME basis every money surface on the board uses. `mkt` is the county
        # roll; when comps produced a confident ARV inside the 0.7x-2.5x band, that ARV drives
        # _basisOf / _netEqOf and the row-level percentage. Scoring off the county roll while every
        # dollar downstream runs on ARV split ranking from money — the exact "Tier and Score baked
        # from assessed value while every money number runs on comps ARV" bug the audit flagged.
        _cp = comps.get(r.get('Case #', '')) if isinstance(comps, dict) else None
        _arv = int(_cp.get('arv') or 0) if _cp else 0
        _acf = (_cp.get('conf') if _cp else '') or ''
        _basis = _arv if (_acf == 'ok' and mkt and 0.7*mkt <= _arv <= 2.5*mkt) else mkt
        r['basis'] = _basis
        r['basis_src'] = 'arv' if _basis == _arv and _arv else 'county'
        r['equity'] = _basis - judg if _basis else 0
        r['equity_pct'] = round(r['equity']/_basis*100,1) if _basis else 0
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
        # `not td` for a DIFFERENT reason than `not is_hoa`, and the difference matters. An HOA
        # judgment is fake debt (the senior mortgage survives, unshown). A tax-deed spread is REAL
        # -- FS 197.552 extinguishes the mortgage -- but it is an AUCTION SPREAD, not owner equity:
        # nobody acquires at the opening bid, a competitive sale bids toward market, and IRS/
        # municipal liens can still survive. Ranking it on the distressed-homeowner ladder is what
        # made 71 of the board's 114 Tier-A leads tax deeds (62%), median spread 92%. The number is
        # kept and still displayed as `margin`; only its claim on this ladder is withdrawn.
        if mkt and not is_hoa and not td: score += min(42.0, max(0.0, ep) * 0.42)  # equity, 0-42
        score += min(18.0, max(0, days) * 1.0)                    # runway, 0-18
        # Homestead is a bonus only in the homeowner lane, where it means occupied and knockable.
        # On a tax deed FS 197.502(6)(c) puts HALF THE ASSESSED VALUE into the opening bid, so
        # homestead makes the deal WORSE. The board already says so out loud -- "opening bid already
        # carries 1/2 the assessed value ... expect thin margin" (tracker_template.html:3097) --
        # while this line paid it +12 for being owner-occupied. Display and score disagreed.
        score += 12 if (r.get('homestead') and not td) else 0     # owner-occupied
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
        # THE COMMA-FLIP IS FOR PEOPLE ONLY. 'SMITH, JOHN' -> 'JOHN SMITH' is right; but a company
        # carries its entity type after the SAME comma, so the identical flip produced names that
        # exist nowhere on earth: 'ALTUS IG REAL ESTATE, LLC' -> 'LLC ALTUS IG REAL ESTATE',
        # '524 NORTH LAKE, LLC' -> 'LLC 524 NORTH LAKE', 'AMERICAN REMODEL, INC.' -> 'INC. AMERICAN
        # REMODEL'. owner_clean is the string records_liens/gen_records_qs search the official
        # records index with, so every company-owned lead searched a nonexistent party and came back
        # '(no records / blocked)' — indistinguishable from a genuinely untraceable owner, and it
        # cached as conf 'none' FOREVER. Visible in the 2026-08-27 MD backlog run: entity-suffix
        # names dominated the failures. Flip only when the tail is NOT an entity suffix.
        if ',' in _oc:
            _last, _, _rest = _oc.partition(',')
            if re.match(r'^\s*(?:LLC|L\.L\.C\.?|INC\.?|CORP\.?|CO\.?|LP|LLP|LTD\.?|PA|PLLC|'
                        r'TRUST|TR|N\.?A\.?|FSB|ASSN|ASSOC(?:IATION)?)\s*\.?\s*$', _rest, re.I):
                _oc = re.sub(r',\s*', ' ', _oc).strip()      # keep entity order: 'X LLC'
            else:
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

def _esc_js(s):
    """Escape a plain string for embedding inside a JS TEMPLATE LITERAL (backticks). The identity
    disclosure is prose today, but a future edit adding an apostrophe-heavy clause, a backslash or
    a ${...} sequence would otherwise break the board's script — and this text ships to homeowners,
    so a silent breakage is not acceptable."""
    return str(s).replace(chr(92), chr(92)*2).replace('`', chr(92)+'`').replace('${', chr(92)+'${')


def _esc_json(obj):
    # Escape HTML-significant chars in embedded JSON so a county field containing "</script>"
    # can't break out of the inline <script> and inject/kill the page.
    return json.dumps(obj).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def _bake_alex_email(tpl, where='board'):
    """Bake Alejandro's cold-email copy into the board as ONE JS string literal.

    outreach_copy.email_body_template() renders his 2026-08-28 body with chr(1)-fenced tokens where
    the per-lead values go. outreach_email.py — the unattended nightly sender — renders that exact
    same call and fills the same tokens. Baking the identical string into genEmail() means the
    manual composer holds no second copy of the words at all, so the two surfaces cannot drift.
    They have drifted three times (08-22 identity gap, 08-28 a "byte-mirror" that was not one,
    08-29 senior-advisor framing) and each time the same homeowner could get two different emails
    depending on which surface reached them.

    DOES NOT RAISE. This runs inside the nightly and a copy module that fails to import must cost
    the new wording, never the board. On failure the placeholder is replaced with an empty JS
    string; genEmail then falls back to the 2026-08-29 body, which is itself compliant (identity
    disclosure, STOP line, CAN-SPAM signature). outreach_email.py degrades identically, so the two
    paths still agree with each other even in the failure case. The failure is printed, loudly —
    silent is how drift happens.
    """
    js = '""'
    try:
        import outreach_copy as _OC
        body = _OC.email_body_template()
        missing = _OC.missing_tokens(body)
        if missing:
            # A template with a token missing would ship a FIXED name/date/phone/company to every
            # homeowner on the board. Fall back rather than send that.
            raise ValueError('template lost token(s): %s' % ', '.join(missing))
        js = (json.dumps(body).replace('<', '\\u003c')
                              .replace('>', '\\u003e')
                              .replace('&', '\\u0026'))
    except Exception as _e:
        print('!! %s: outreach_copy bake FAILED (%s) -- genEmail falls back to the 2026-08-29 '
              'cold body. Fix outreach_copy.py and rebuild.' % (where, _e))
    return tpl.replace('"__ALEXMAIL_EN__"', js)

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

_UCN_RE = re.compile(r'^\d{2}\d{4}(CA|CC)\d')   # FL Uniform Case Number: county+year+court type
def _county_civil(case):
    """True when the case was filed in COUNTY civil court, across all three case-number dialects.
    County civil has a jurisdictional cap (~$50k), so it structurally cannot be a residential first-
    mortgage foreclosure — it is an association or junior action, and the first mortgage survives."""
    c = (case or '').upper()
    return bool(c.startswith(('COCE', 'CONO', 'COWE', 'COSO')) or '-CC-' in c
                or (_UCN_RE.match(c) and _UCN_RE.match(c).group(1) == 'CC'))
def _fc_type(case):
    """FALLBACK-ONLY classifier from the court case number, used when no plaintiff is available. The prefix
    is a POOR proxy — HOAs routinely foreclose in CIRCUIT court (a CACE number), so a CACE is NOT reliably a
    mortgage foreclosure. Prefer _fc_type_plaintiff() (the real signal) whenever a plaintiff name is known.
    Broward/PB: CACE=circuit, COCE/CONO/COWE/COSO=county. Miami-Dade: -CA-=circuit, -CC-=county."""
    c = (case or '').upper()
    if c.startswith('CACE') or '-CA-' in c: return 'MORTGAGE'
    if c.startswith(('COCE', 'CONO', 'COWE', 'COSO')) or '-CC-' in c: return 'HOA'
    # FLORIDA UNIFORM CASE NUMBER — {county:2}{year:4}{court:2}{seq:6}{div}. Palm Beach files this way
    # ("502025CC016197XXXAMB") and matched NONE of the patterns above, which only cover Broward's
    # CACE/COCE prefixes and Miami-Dade's -CA-/-CC- infixes. Measured: 182/182 Palm Beach leads
    # classified as '' and therefore defaulted to Bank/Mortgage, including 23 County Court cases.
    # That inverts the money model: an association lien is SUBORDINATE to a first mortgage (FS
    # 718.116), so the mortgage SURVIVES an association sale instead of being wiped by it.
    m = _UCN_RE.match(c)
    if m:
        # CC = County Civil. Its jurisdictional cap (~$50k) means a residential MORTGAGE foreclosure
        # essentially cannot be heard there — so CC is a STRONG association signal.
        if m.group(1) == 'CC': return 'HOA'
        # CA = Circuit Civil. Weak signal only, for exactly the reason named above: associations
        # foreclose in circuit court all the time. Same caveat as CACE.
        return 'MORTGAGE'
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


def _senior_surviving(h):
    """ONE meaning of "what survives the sale", fixed at the merge boundary.

    The three chain engines disagree about what `surv` contains, and the browser could not tell:
      records_liens.py:305 / broward_liens.py:325 -> surv = sum(all opens except the foreclosing one)
                                                          = seniors + juniors   (subtract juniors_post)
      batchdata_liens.py (seniors-only sum — match by content, its line drifts)
                                                  -> surv = sum(seniors)         (already seniors-only)

    The board applied the records-style subtraction to BOTH, so on every BatchData-sourced lead the
    junior balance came out of the SENIOR figure a second time — and Math.max(0, ...) then silently
    swallowed the remainder. On the live cache that erased an entire $811,577 first mortgage to $0
    (502024CA012300XXXAMB). Anything needing "the surviving senior" calls THIS; a fourth feed adds a
    branch here and nowhere else.
    """
    surv = float(h.get('surv') or 0)
    if (h.get('source') or '').lower() == 'batchdata':
        return int(round(surv))                                    # already seniors-only
    return int(round(max(0.0, surv - float(h.get('juniors_post') or 0))))


def _fwd_flags(d, h, ftype):
    """Bake the deal-killer flags from a lien result (records_liens/broward_liens) onto a slim lead. Missing
    keys (e.g. Miami-Dade records that predate the flag fields) are simply skipped."""
    d['orftype'] = h.get('ftype') or ftype
    d['orsrc'] = (h.get('source') or 'records')      # PROVENANCE. orconf is CONFIDENCE — stop conflating.
    # Emitted UNCONDITIONALLY, a real 0 included. The `if h.get('surv')` guard below omits the field on
    # a chain that proved nothing survives, and the board's `+r.orsurv || +r.orjunior || ...` fallback
    # then reads the JUNIOR as if it were a surviving senior. Measured: 7 leads, $458,777 of invented
    # first mortgage (worst 502025CA008013XXXAMB, $195,871 on a $537,320 property). Today's
    # double-subtraction happens to net those to zero by accident — fixing one without the other
    # would expose the phantom.
    d['orsurvsen'] = _senior_surviving(h)
    if h.get('surv'): d['orsurv'] = h.get('surv', 0)                 # total open mortgage that survives an HOA sale
    if h.get('surv_first'): d['orsurvfirst'] = h.get('surv_first', 0)  # the first mortgage (headline number)
    if h.get('deeded'):                                             # already deeded to another investor
        d['ordeeded'] = h['deeded']; d['ordeedconf'] = h.get('deed_conf', '')
    if h.get('second_fc'): d['orsecond'] = h['second_fc']           # a separate CACE mortgage foreclosure


# ---------------------------------------------------------------------------------------------
# MIAMI-DADE bounding box. A coordinate outside this is a provably bad geocode for an MD lead.
# Measured hit: case 2026A00186 ("MIAMI GARDENS, FL- 33056", no street number) geocoded to
# 26.1425,-80.1472 — that is in BROWARD, north of the county line.
MD_BBOX = (25.13, 25.99, -80.88, -80.11)   # lat_min, lat_max, lng_min, lng_max

def _has_street(addr):
    """True when the address starts with a street number.

    WHY THIS MATTERS MORE THAN THE BOUNDING BOX: a street-less address ("HOMESTEAD, FL- 33034")
    gives the geocoder nothing to work with, so it falls back to a city/county centroid. Case
    2026A00080 is a Homestead property whose coordinate landed 0.9 miles from DOWNTOWN MIAMI --
    27 miles from the actual property. The bbox check cannot catch that, because downtown Miami
    is legitimately inside Miami-Dade. Only the street-number test catches it. Without this, a
    radius filter ranks that lead "near me" and sends Carlos on a 54-mile round trip to nothing.
    """
    return bool(re.match(r'^\s*\d+\s+\S', str(addr or '')))

def _routable_py(d):
    """Server-side twin of the browser's _routable(). Keep the two in sync."""
    la, lo = d.get('lat'), d.get('lng')
    if not (la and lo):
        return False
    if not (MD_BBOX[0] <= la <= MD_BBOX[1] and MD_BBOX[2] <= lo <= MD_BBOX[3]):
        return False
    return _has_street(d.get('addr'))

def _js_guard(tpl):
    """Parse every inline <script> block with Node and abort the build on a syntax error.

    WHY THIS EXISTS: the page generates whole documents (door hangers, doc room, dial-ready,
    the morning worker) as JS strings concatenated inside other JS strings. A single dropped
    backslash produces valid-looking HTML whose script dies at parse time, so the feature is
    simply dead on the live site while every Python test still passes -- Python never parses
    that JavaScript. Two shipped that way on 2026-07-30 before this guard existed.

    Node is optional: if it is not installed we warn and continue rather than blocking a build
    on a machine that cannot run the check.
    """
    import subprocess, tempfile

    # STATIC LINT FIRST — catches the bug node --check CANNOT. This template builds documents as
    # JS strings with a leading-`+` continuation style ("...' \n + 'more'"). A continuation line
    # that reads "+ +'text'" or "+ + (expr)" is a UNARY PLUS on a string, which is valid JS that
    # evaluates to NaN at runtime -- so node --check passes and the page ships "JoseNaNNaN" or
    # "call these firstNaN" into a printed field sheet. This exact typo shipped three times
    # (2026-07-30/31). The concat continuation is always "+ <value>", never "+ +<value>", so a
    # doubled leading plus is never intentional here.
    for m in re.finditer(r'\n[ \t]*\+[ \t]+\+[ \t]*[\'"(]', tpl):
        ctx = tpl[max(0, m.start()):m.start() + 70].replace('\n', ' ').strip()
        raise SystemExit('BUILD ABORTED: unary-plus concat bug ("+ +" continuation -> NaN at '
                         'runtime, invisible to node --check):\n   ...' + ctx + '\n'
                         'Delete the second "+". See _js_guard for why.')

    # HTML comments are not executable, so strip them BEFORE hunting for <script> blocks. A comment
    # that merely MENTIONS "<script>" in prose otherwise opens a phantom block running to the next
    # REAL </script>, and node --check ends up linting English. The Motion.js note does exactly
    # that ("a network <script> fails offline"), which aborted every build on 2026-08-09 — the
    # early publish still landed so the site looked fresh, while the enriched rebuild never shipped.
    scan = re.sub(r'<!--.*?-->', '', tpl, flags=re.S)
    blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', scan, re.S | re.I)
    if not blocks:
        return
    try:
        subprocess.run(['node', '--version'], capture_output=True, timeout=10, check=True)
    except Exception:
        print('WARN: node not available - skipping inline-JS syntax guard')
        return
    bad = 0
    for n, src in enumerate(blocks, 1):
        if not src.strip():
            continue
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
            fh.write(src)
            path = fh.name
        try:
            p = subprocess.run(['node', '--check', path], capture_output=True, text=True, timeout=30)
            if p.returncode != 0:
                bad += 1
                first = (p.stderr or '').strip().split('\n')
                print(f'JS SYNTAX ERROR in inline <script> #{n}:')
                for line in first[:6]:
                    print('   ' + line)
        finally:
            try: os.unlink(path)
            except Exception: pass
    if bad:
        raise SystemExit(f'BUILD ABORTED: {bad} inline <script> block(s) failed node --check. '
                         'Fix the escaping before publishing - the page would load but the '
                         'feature would be dead on the live site.')
    print(f'js-guard: {len(blocks)} inline script block(s) parsed clean')

def _text_ledger():
    """text_sent.json -> (case -> {n, opens, last}, pkey -> {n, opens, last}).

    The SMS mirror of _mail_ledger, and it exists for the same measured reason: before it, text
    history lived ONLY in one browser's localStorage, so a new profile / second device / cleared
    cache read every owner as never-texted and restarted the 3-touch ladder at touch 1. That is a
    strictly worse version of the 2026-08-08 email incident (33 owners got 4+ emails).

    ONLY `confirmed` rows count as sends — the exact analogue of requiring message_id above. The
    worker's `textopen` branch posts confirmed:false because opening a composer is not a delivery;
    those are surfaced separately as `opens` so an opened-but-never-sent lead is visible without
    being treated as contacted.

    The PERSON table is the one that matters. Cases fall off the board when their auction passes,
    but the human keeps their phone — a per-case count silently forgets the two messages already
    sent about a property that sold, and the ladder restarts on the next property they own.
    """
    log, per = {}, {}
    p = os.path.join(HERE, 'text_sent.json')
    if not os.path.exists(p):
        return log, per
    try:
        rows = json.load(open(p, encoding='utf-8')) or []
    except Exception as e:
        print(f'text ledger: text_sent.json unreadable ({e}) - cadence falls back to localStorage only')
        return log, per
    for e in rows:
        if not isinstance(e, dict) or e.get('ch') != 'text':
            continue
        try:
            ms = int(datetime.fromisoformat(e['ts_utc']).timestamp() * 1000)
        except Exception:
            ms = 0
        ok = bool(e.get('confirmed'))
        for key, table in ((str(e.get('case') or '').strip(), log),
                           (str(e.get('pkey') or '').strip(), per)):
            if not key:
                continue
            d = table.setdefault(key, {'n': 0, 'opens': 0, 'last': 0})
            if ok:
                d['n'] += 1
            else:
                d['opens'] += 1
            d['last'] = max(d['last'], ms)
    return log, per


def _mail_ledger(recent_days=14):
    """mail_sent.json -> (case -> {n, last}, recipient -> last_ms). Both in epoch MILLIseconds,
    because every consumer in the page compares them against Date.now().

    ONLY rows carrying a message_id count. The same ledger also records FAILURES -- send_server
    writes an {'error': ...} row on an SMTP exception, and that row shares ch='email'. Counting a
    failure as contact would put a lead into cooldown, and eventually into the 'done, never email
    again' bucket, without a single message ever having reached them.

    The recipient table is trimmed to `recent_days` because its only consumer is a 24h dedupe.
    Baking all ~800 historical rows into every board would be dead weight with no behavioural gain.
    BCC addresses are included: they are the same owner's other traced mailboxes, and the guard
    exists to protect a human's inbox, not a particular string.
    """
    log, tos = {}, {}
    p = os.path.join(HERE, 'mail_sent.json')
    if not os.path.exists(p):
        return log, tos
    try:
        rows = json.load(open(p, encoding='utf-8')) or []
    except Exception as e:
        print(f'send ledger: mail_sent.json unreadable ({e}) - board falls back to localStorage only')
        return log, tos
    cutoff = (datetime.now() - timedelta(days=recent_days)).timestamp() * 1000
    for e in rows:
        if e.get('ch') != 'email' or not e.get('message_id'):
            continue
        try:
            ms = int(datetime.fromisoformat(e['ts_utc']).timestamp() * 1000)
        except Exception:
            continue
        case = (e.get('case') or '').strip()
        if case:
            d = log.setdefault(case, {'n': 0, 'last': 0})
            d['n'] += 1
            d['last'] = max(d['last'], ms)
        if ms >= cutoff:
            for a in [e.get('to') or ''] + str(e.get('bcc') or '').split(','):
                a = a.strip().lower()
                if a:
                    tos[a] = max(tos.get(a, 0), ms)
    return log, tos


def _zip_centroids(slim):
    """ZIP -> {lat, lng, n, city?, county?, src} centroid table.

    Two source layers, board-anchored first:

    1. `src:'board'` — centroid computed from our own ROUTABLE Miami-Dade leads. Accuracy on the
       live board: median max-spread 1.39 mi across the 28 ZIPs holding 3+ geocoded leads --
       good enough to anchor a 5-mile radius. 26 of 64 ZIPs hold exactly ONE geocoded lead, so
       their "centroid" is just that lead's position; `n` is returned so the UI can downgrade
       confidence and refuse a precision it cannot support.

       OUTLIER REJECT: any contributing point more than 4 miles from the provisional centroid is
       dropped and the centroid recomputed. This is the guard that surfaced the 33056 bug, where
       one street-less lead stretched the raw spread to 12.9 mi inside a ZIP only ~3 mi across.

    2. `src:'fl_bundle'` — statewide FL fallback loaded from fl_zip_centroids.json (1,464 active
       FL ZIPs). Fills any ZIP not present in the board layer, so the operator can type ANY
       Florida ZIP and get a real centroid instead of a silent fallback to Carlos's house. Marked
       `n:0` so the UI clamps radius to minCap=5 (single-point origins cannot support tighter).

    Board data wins on collision — a ZIP with 3+ real leads geocodes tighter than the USPS ZIP
    centroid, which is a population-weighted point that can sit half a mile from every lead.
    """
    import collections, pathlib
    buckets = collections.defaultdict(list)
    for d in slim:
        if (d.get('county') or 'MIAMI-DADE') != 'MIAMI-DADE':
            continue
        if not _routable_py(d):
            continue
        m = re.search(r'\b(3\d{4})\b', str(d.get('addr') or ''))
        if m:
            buckets[m.group(1)].append((d['lat'], d['lng']))
    out = {}
    for z, pts in buckets.items():
        for _ in range(2):                     # provisional centroid, reject outliers, recompute
            clat = sum(p[0] for p in pts) / len(pts)
            clng = sum(p[1] for p in pts) / len(pts)
            keep = [p for p in pts if _haversine_mi(clat, clng, p[0], p[1]) <= 4.0]
            if len(keep) == len(pts) or not keep:
                break
            pts = keep
        clat = sum(p[0] for p in pts) / len(pts)
        clng = sum(p[1] for p in pts) / len(pts)
        out[z] = {'lat': round(clat, 6), 'lng': round(clng, 6), 'n': len(pts), 'src': 'board'}

    # Layer 2: FL statewide fallback. Missing file is not fatal — degrades to board-only.
    fl_path = pathlib.Path(__file__).parent / 'fl_zip_centroids.json'
    if fl_path.exists():
        try:
            fl = __import__('json').loads(fl_path.read_text(encoding='utf-8'))
            fallback_added = 0
            for z, rec in fl.items():
                if z in out:
                    continue                                # board layer wins on collision
                out[z] = {'lat': rec['lat'], 'lng': rec['lng'], 'n': 0,
                          'city': rec.get('city', ''), 'county': rec.get('county', ''),
                          'src': 'fl_bundle'}
                fallback_added += 1
            print(f'zip centroids: {len(buckets)} board + {fallback_added} FL bundle '
                  f'= {len(out)} total')
        except Exception as e:
            print(f'zip centroids: FL bundle load failed ({e}); board-only')
    else:
        print(f'zip centroids: board-only ({len(out)} ZIPs) — fl_zip_centroids.json not found')
    return out

def _haversine_mi(la1, lo1, la2, lo2):
    R = 3958.8
    t = math.pi / 180
    dla = (la2 - la1) * t
    dlo = (lo2 - lo1) * t
    a = math.sin(dla/2)**2 + math.cos(la1*t) * math.cos(la2*t) * math.sin(dlo/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def _money(v):
    """'$225,577.00' | 225577 | None -> int. The county scrapes hand back formatted currency
    strings; every downstream consumer wants a number, and a string silently fails every
    comparison it is used in rather than raising."""
    if v is None or v == '':
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(float(re.sub(r'[^0-9.\-]', '', str(v)) or 0))
    except Exception:
        return 0


def _person_keys(rows):
    """Group cases that belong to the SAME HUMAN, so a contact budget can be shared across them.

    Everything in this system is case-keyed. One owner with three properties therefore gets three
    independent text budgets and receives three times the messages — while experiencing one phone
    buzzing. This is the grouping that fixes that.

    EDGES (an edge unions two cases; a single uncorroborated signal never does):
      * shared primary EMAIL — strong on its own. Mirrors the `byEmail` grouping the worker queue
        already uses to consolidate portfolio email, so there is one grouping idiom, not two.
      * shared 10-digit PHONE **and** a shared name token of >= 3 chars.
    Name alone NEVER unions: "MARIA GARCIA" in Miami-Dade is a demographic, not a person.

    🔴 THE INSTITUTIONAL GUARD IS THE LOAD-BEARING PART. A lender's, law firm's, or HOA management
    company's number is attached to hundreds of leads. Union on it unguarded and every one of those
    homeowners collapses into a single "person" with a single 3-text budget — the whole board would
    go silent after three messages. Any phone or email shared by more than _MAX_SHARED cases is not
    a person; it is an institution, and it is skipped entirely.

    BIAS, deliberately toward UNDER-contacting: a wrongly MERGED person costs a lead; a wrongly
    SPLIT person costs an extra unwanted message, which is the FTSA exposure. When in doubt, merge.

    Returns {case: (pkey, group_size)}. Singletons get 'C'+case — i.e. exactly today's per-case
    behaviour — so a lead with no phone and no email degrades to current semantics rather than into
    a shared bucket with strangers.
    ⚠️ pkey is a HASH, never the phone itself: the public build strips phones/emails from the payload
    (see `nophone`), and a raw-phone key would put homeowner numbers straight back into it.
    """
    import hashlib
    _MAX_SHARED = 8
    NOISE = {'THE', 'AND', 'LLC', 'INC', 'CORP', 'TRUST', 'ESTATE', 'ETAL', 'JR', 'SR', 'III',
             'LIVING', 'FAMILY', 'REVOCABLE', 'TRUSTEE', 'HEIRS', 'UNKNOWN', 'TENANT', 'OWNER',
             'HUSBAND', 'WIFE', 'AKA', 'FKA', 'DECEASED'}

    def _toks(s):
        return {t for t in re.split(r'[^A-Za-z]+', str(s or '').upper())
                if len(t) >= 3 and t not in NOISE}

    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_email, by_phone, tok_of = {}, {}, {}
    for r in rows:
        c = r.get('case') or ''
        if not c:
            continue
        parent.setdefault(c, c)
        tok_of[c] = _toks(r.get('owners'))
        for e in (r.get('emails') or []):
            e = str(e or '').strip().lower()
            if e:
                by_email.setdefault(e, set()).add(c)
        for p in (r.get('phones') or []):
            d = re.sub(r'\D', '', str(p or ''))
            if len(d) == 10:
                by_phone.setdefault(d, set()).add(c)

    skipped = 0
    for cs in by_email.values():
        cs = sorted(cs)
        if len(cs) > _MAX_SHARED:
            skipped += 1
            continue
        for c in cs[1:]:
            union(cs[0], c)
    for cs in by_phone.values():
        cs = sorted(cs)
        if len(cs) > _MAX_SHARED:
            skipped += 1
            continue
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                if tok_of.get(cs[i], set()) & tok_of.get(cs[j], set()):
                    union(cs[i], cs[j])

    groups = {}
    for c in list(parent):
        groups.setdefault(find(c), []).append(c)
    out = {}
    for members in groups.values():
        if len(members) == 1:
            out[members[0]] = ('C' + members[0], 1)
            continue
        h = 'P' + hashlib.sha1('|'.join(sorted(members)).encode('utf-8')).hexdigest()[:10]
        for c in members:
            out[c] = (h, len(members))
    return out, skipped


def _pa_url_from_folio(r):
    """Property-appraiser DEEP link from folio, or '' when there is no folio to deep-link with.
    Same reason as _tax_url_from_folio: a resolved parcel deserves the record, not a search box."""
    folio = re.sub(r'[^0-9A-Za-z]', '', str(r.get('folio') or '')).upper()
    if not folio:
        return ''
    ct = str(r.get('county') or 'MIAMI-DADE').strip().upper()
    if ct.startswith('PALM'):
        return 'https://pbcpao.gov/Property/Details?parcelId=' + folio
    if ct.startswith('BROW'):
        return 'https://bcpa.net/RecInfo.asp?URL_Folio=' + folio
    return 'https://apps.miamidadepa.gov/PropertySearch/#/?folio=' + folio


def _tax_url_from_folio(r):
    """Build the county tax-bill URL from the folio when the enricher never set tax_url.

    WHY (2026-08-22, reported from the field as "the tax link only takes me to a Google search"):
    483 of 1,887 leads carried a REAL folio and still had no tax link, so the board fell through to
    its search-engine fallback on parcels it could identify perfectly well. The URL is a pure
    function of folio + county — the same three formulas Call Mode uses, diffed against every one of
    the 1,035 folio-bearing leads at the time they were written (Palm Beach needs 2-2-2-2-2-3-4
    dashing, Broward 6-2-4, Miami-Dade plain digits) — so there was never a reason to leave it empty.
    Broward folios can contain LETTERS (494213BA0140); strip punctuation, not alphanumerics.
    """
    folio = re.sub(r'[^0-9A-Za-z]', '', str(r.get('folio') or '')).upper()
    if not folio:
        return ''
    ct = str(r.get('county') or 'MIAMI-DADE').strip().upper()

    def dash(s, groups):
        out, p = [], 0
        for g in groups:
            if p >= len(s):
                break
            out.append(s[p:p + g])
            p += g
        if p < len(s):
            out.append(s[p:])
        return '-'.join(out)

    if ct.startswith('PALM'):
        return ('https://pbctax.publicaccessnow.com/PropertyTax.aspx?s=ParcelID%3A'
                + urllib.parse.quote(dash(folio, [2, 2, 2, 2, 2, 3, 4]), safe='')
                + '&pg=1&g=-1&moduleId=449')
    if ct.startswith('BROW'):
        return ('https://broward.county-taxes.com/public/real_estate/parcels/'
                + dash(folio, [6, 2, 4]) + '/bills')
    return 'https://miamidade.county-taxes.com/public/real_estate/parcels/' + folio



def _js(v):
    """Escape a Python string for a single-quoted JavaScript literal.

    Every injected value lands inside `llc:'...'` / `= '...'` in the board. A bare apostrophe would
    close the literal and break the whole page -- and the board is one self-contained encrypted
    file, so a syntax error there is a blank site, not a degraded one.
    """
    return (str(v or '').replace('\\', '\\\\').replace("'", "\\'")
            .replace('\r', '').replace('\n', ' '))


_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
# Ours, and Jesse's. These are on the page deliberately (sender identity, the Brief Jesse button).
_OWN_EMAILS = ('agonzalez0311707@gmail.com', 'celusa13@gmail.com', 'miamisolutionsgroup@gmail.com')


def assert_no_bulk_emails(html, where, limit=6):
    """Refuse to publish a page carrying a harvestable list of homeowner email addresses.

    The lead payload is encrypted, so for a long time it was assumed the page held no addresses.
    It held 1,438 of them (found 2026-08-26): MAILTO keyed its recipient ledger BY ADDRESS, in
    plaintext, outside the payload, in a PUBLIC repo -- people in foreclosure, ready to scrape.
    Three more were in SERVER_OPTOUTS, more in REPLIES, and four were sitting in developer comments
    in tracker_template.html that name real owners as worked examples.

    Every one of those was invisible to review because nothing counted. This counts. It is a
    tripwire, not a scrubber: it fails the build so a person decides what to do, rather than
    quietly stripping something a feature depended on.
    """
    found = sorted({e.lower() for e in _EMAIL_RE.findall(html)}
                   - {e.lower() for e in _OWN_EMAILS})
    if len(found) > limit:
        raise SystemExit('%s: %d third-party email address(es) would be published in plaintext '
                         '(limit %d). First few: %s' % (where, len(found), limit, ', '.join(found[:5])))


def _addr_key(addr):
    """FNV-1a/base36 of a lowercased email — the key the board's recipient guard looks up.

    MAILTO used to ship the addresses themselves as object keys: 1,438 people in foreclosure, in
    plaintext, outside the encrypted lead payload, in a PUBLIC repo. The guard only ever asks "when
    did we last mail this inbox?", so it never needed the address, only a stable identity.

    This is obfuscation, not encryption, and the difference matters: it removes a list anyone could
    scrape or index, and it does NOT stop someone checking whether one address they already hold is
    in it. The real fix is moving this const inside the encrypted payload with the lead data.
    Must stay byte-identical to _addrKey() in tracker_template.html.
    """
    h = 0x811c9dc5
    for ch in str(addr or '').lower().strip():
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    # base36 — same alphabet and direction as JS Number.prototype.toString(36)
    if not h:
        return '0'
    digits, out = '0123456789abcdefghijklmnopqrstuvwxyz', ''
    while h:
        out = digits[h % 36] + out
        h //= 36
    return out


_PLACEHOLDER = re.compile(r'__[A-Z][A-Z0-9_]{2,}__')


def assert_no_placeholders(html, where):
    """Refuse to write a page that still carries an unsubstituted __TOKEN__.

    Every placeholder in tracker_template.html sits in JS as a bare identifier, so one that survives
    the build is not a cosmetic blemish -- it is a ReferenceError at top level, and it aborts the
    rest of the script. Everything declared after it never initialises and the page is dead.

    design-preview.html shipped that way with eleven of them (found 2026-08-26): build_preview.py
    called subst_build_facts() under a comment claiming it was 'the same substitution the real build
    uses', which was true of six tokens and false of the other eleven that make_tracker does inline.
    _calctest, _scripttest and _redesign_verify had all been pointed at that dead page. Nothing
    failed loudly, because a page that renders nothing still serves a 200.
    """
    left = sorted(set(_PLACEHOLDER.findall(html)))
    if left:
        raise SystemExit('%s: %d unsubstituted placeholder(s) -- the page would abort on load: %s'
                         % (where, len(left), ', '.join(left)))


def _motion_js():
    """vendor/motion.min.js, inlined. Empty (not fatal) if absent -- _fxEnter() no-ops and the
    board still renders. Shared so the preview build cannot animate differently from the real one."""
    try:
        js = open(os.path.join(HERE, 'vendor', 'motion.min.js'), encoding='utf-8').read()
        if '</script' in js:          # would terminate the inline block early and shred the page
            raise ValueError('vendor/motion.min.js contains a </script> token — refusing to inline')
        return js
    except Exception as e:
        print(f'motion.js not inlined ({e}) — board renders without entrance animation')
        return ''


def subst_build_facts(tpl, updated):
    """Fill the board's build-time placeholders. BOTH template readers must go through this.

    The board is JavaScript in a self-contained encrypted file -- it cannot call Python, so any fact
    that Python owns has to be injected here or the board silently drifts from every other surface.

      __ENTITY_LLC__   the company name AS IT MAY LEGALLY BE SHOWN. entity.py decides whether the
                       " LLC" suffix is substantiated; hardcoding it in the template is what let a
                       false entity claim reach the public site on 2026-08-23.
      __CLIENT_EMAIL__ the company inbox printed on client paper. One value in sender.json instead
                       of the same literal in three files.
      __EMBLEM_W/H__   the emblem's real pixel aspect, from the payload actually embedded.
                       Hardcoding it meant a logo swap stretched the mark on every letterhead.
    """
    import entity as _entity
    import bsg_brand as _brand
    name, _doc, _warn = _entity.display_llc()
    snd = _entity.sender()
    return (tpl.replace('__UPDATED__', updated)
               .replace('__ENTITY_VERIFIED__', 'true' if _entity.verified() else 'false')
               .replace('__ENTITY_LLC__', _js(name))
               .replace('__CLIENT_EMAIL__', _js(snd.get('client_email')))
               .replace('__EMBLEM_W__', str(_brand.NATIVE_W))
               .replace('__EMBLEM_H__', str(_brand.NATIVE_H)))


def make_tracker(leads):
    # merge locally skip-traced phones/emails (never fetched here; produced by skiptrace.py, gitignored)
    st = {}
    if os.path.exists(RESULTS_FILE):
        try: st = json.load(open(RESULTS_FILE, encoding='utf-8'))
        except Exception: st = {}
    # REGISTRY DNC OVERRIDE (tracerfy_mcp.py dnc lane, gitignored sidecar). The provider's dnc flag
    # is a MODELED estimate; dnc_scrub.json holds actual federal/state registry verdicts. A registry
    # hit flips the flag to True at THIS single seam — every bake path downstream (MD + county merge,
    # call sheet, text gate) inherits it, so the compliance gate enforces the registry, not a model.
    # One-way on purpose: a registry MISS never un-flags a provider True (belt stays on braces).
    _dncreg = {}
    try:
        _dncreg = json.load(open(os.path.join(HERE, 'dnc_scrub.json'), encoding='utf-8'))
    except Exception:
        pass
    if _dncreg:
        _n = 0
        for _e in st.values():
            for _p in (_e.get('phones') or []):
                _v = _dncreg.get(str(_p.get('number') or ''))
                if _v and (_v.get('national_dnc') or _v.get('state_dnc')) and not _p.get('dnc'):
                    _p['dnc'] = True; _n += 1
        if _n:
            print(f"DNC registry override: {_n} phone(s) the modeled flag called safe are registry-listed -> flagged")
    # HARD-BOUNCED ADDRESSES (produced by bounces.py, gitignored). The first real outreach run on
    # 2026-08-02 sent 41 emails and 13 came back "mailbox disabled" — a 32% hard-bounce rate,
    # because skip-trace returns whatever address it holds including decade-dead AOL / EarthLink /
    # Netscape accounts. Providers treat a sustained bounce rate as proof of a purchased list (the
    # tolerated ceiling is ~2%) and respond by throttling, then spam-foldering, then blocking — and
    # this is the operator's PERSONAL Gmail, the inbox the business runs on. Strip them here so a
    # dead address can never be queued a second time.
    _bounced = set()
    _bf = os.path.join(HERE, 'bounced_emails.json')
    if os.path.exists(_bf):
        try: _bounced = {str(k).lower() for k in json.load(open(_bf, encoding='utf-8'))}
        except Exception: _bounced = set()
    # PRE-SEND VERIFICATION (verify_emails.py, gitignored). The bounce list above is REACTIVE -- it
    # only knows an address is dead after we have already burned sender reputation proving it. The
    # verifier is the same guard run FORWARD: role mailboxes, malformed addresses, and domains our
    # own ledger shows are effectively purged (netscape.net, cs.com, juno.com, netzero.net and the
    # rest of the dead-ISP tier, all 100% dead across every send). Merged into the same strip so
    # there is exactly ONE place an address gets removed from the queue.
    _vf = os.path.join(HERE, 'verified_emails.json')
    _vdead = 0
    if os.path.exists(_vf):
        try:
            for _a, _rec in (json.load(open(_vf, encoding='utf-8')) or {}).items():
                if (_rec or {}).get('v') == 'dead' and str(_a).lower() not in _bounced:
                    _bounced.add(str(_a).lower()); _vdead += 1
        except Exception:
            pass
    if _vdead:
        print(f"verifier: +{_vdead} address(es) blocked BEFORE a first send (run verify_emails.py)")
    if _bounced:
        _stripped = 0
        for _v in st.values():
            _em = _v.get('emails') or []
            _keep = [e for e in _em if str(e).lower() not in _bounced]
            if len(_keep) != len(_em):
                _stripped += len(_em) - len(_keep)
                _v['emails'] = _keep
        print(f"bounce guard: {len(_bounced)} dead address(es) known — {_stripped} stripped from the queue")
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
    # BatchData property source (produced by batchdata_liens.py) — the SECOND lien feed, covering the
    # counties/leads the captcha-walled Official Records scrape can't (Palm Beach especially) and
    # carrying a current-estimated balance + AVM value. Used as a FALLBACK: only where the recorded
    # chain is missing or empty, so the richer Official Records data always wins when we have it.
    _bdf = os.path.join(HERE, 'batchdata_liens.json')
    if os.path.exists(_bdf):
        try: _bdl = json.load(open(_bdf, encoding='utf-8'))
        except Exception: _bdl = {}
        _bd_used = 0
        for _c, _v in (_bdl or {}).items():
            _cur = rl.get(_c)
            if (not _cur or not _cur.get('liens')) and _v and _v.get('liens'):
                rl[_c] = _v; _bd_used += 1
        if _bd_used:
            print(f"filled {_bd_used} lien chains from BatchData (fallback where Official Records had none)")
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
    # Deep Diligence briefs (diligence.py) — Capri-H quality card on the Call Sheet.
    # Prefer diligence_cache.json; fall back to per-case files under diligence/*.json.
    diligence = {}
    _dcf = os.path.join(HERE, 'diligence_cache.json')
    if os.path.exists(_dcf):
        try: diligence = json.load(open(_dcf, encoding='utf-8')) or {}
        except Exception: diligence = {}
    if not diligence:
        _ddir = os.path.join(HERE, 'diligence')
        if os.path.isdir(_ddir):
            for _fn in os.listdir(_ddir):
                if not _fn.endswith('.json'): continue
                try:
                    _dj = json.load(open(os.path.join(_ddir, _fn), encoding='utf-8'))
                    _dc = (_dj.get('case') or '').strip()
                    if _dc: diligence[_dc] = _dj
                except Exception:
                    pass
    # SIBLING CASES (sibling_cases.py, committed — public court data). The Martin lesson
    # (2026-07-28): a condo can carry TWO foreclosure cases, and the one we DON'T track can
    # auction first. A sibling with a Certificate of Title = the property is already someone
    # else's; the board must say CLAIMED, not route Carlos to a competitor's doorstep.
    siblings = {}
    _sbf = os.path.join(HERE, 'sibling_cases.json')
    if os.path.exists(_sbf):
        try: siblings = json.load(open(_sbf, encoding='utf-8'))
        except Exception: siblings = {}
    # the HUMANS behind LLC-owned leads (llc_officers.py, Sunbiz; committed like the stay cache) —
    # managers/officers + registered agent so a company-owned deal is still a person you can call.
    llcs = {}
    _lof = os.path.join(HERE, 'llc_officers.json')
    if os.path.exists(_lof):
        try: llcs = json.load(open(_lof, encoding='utf-8'))
        except Exception: llcs = {}
    # OWNERSHIP-FLIP GATE (ownership_scan.py -> ownership.json): for auction-window leads, a LIVE
    # county-appraiser check of "does the defendant still own this house?" (the Milouse miss —
    # foreclosed out by a SEPARATE HOA case, Certificate of Title to a third party, still scored
    # ~$56k equity). STAMP-ONLY for now: the row carries the verdict; isFlaggedDead does NOT yet
    # auto-drop on it, so a flipped lead is surfaced for a human, never silently dropped.
    ownership = {}
    _ogf = os.path.join(HERE, 'ownership.json')
    if os.path.exists(_ogf):
        try: ownership = json.load(open(_ogf, encoding='utf-8'))
        except Exception: ownership = {}

    # §362 STAY FLAGS MUST NOT DEPEND ON THE PIPELINE REACHING STEP [3e/5].
    # sale_history.py stamps sale_bk_active onto the LEAD rows, but the nightly scrape REWRITES
    # leads_final.json every morning — so the stamp only exists if sale_history.py runs again after
    # it. It sits late in the bat, which is exactly where the 2h Task Scheduler kill was landing, so
    # the flags silently vanished: cache held 97 active stays while the published board carried 0.
    # healthcheck caught the identical hole on 2026-07-21 (67 stays) — it is a RECURRING regression,
    # and the site hard-gates outreach on this flag, so a miss means soliciting someone under a
    # federal automatic stay. sale_history_cache.json is DURABLE, so read it here as the floor.
    # Field mapping is sale_history.py:246-250 verbatim — do not re-derive it.
    _shc = {}
    _shf = os.path.join(HERE, 'sale_history_cache.json')
    if os.path.exists(_shf):
        try: _shc = json.load(open(_shf, encoding='utf-8'))
        except Exception: _shc = {}
    if _shc:
        _restored = 0
        for r in leads:
            ent = _shc.get(r.get('Case #') or '')
            if not isinstance(ent, dict):
                continue
            if r.get('sale_survived') is None and ent.get('s') is not None:
                r['sale_survived'] = ent['s']; r['sale_scheduled'] = ent.get('n', 0)
            if ent.get('w') and not r.get('sale_who'):     r['sale_who'] = ent['w']
            if ent.get('b') and not r.get('sale_bk'):      r['sale_bk'] = ent['b']
            if ent.get('sl') and not r.get('sale_stay_lifted'): r['sale_stay_lifted'] = ent['sl']
            if ent.get('a') and not r.get('sale_bk_active'):
                r['sale_bk_active'] = True
                r['sale_bk_date'] = ent.get('bd', '')
                _restored += 1
        if _restored:
            print('sale-history cache: restored %d ACTIVE 362 stay flag(s) the scrape had wiped '
                  '-> outreach stays gated' % _restored)
    slim = []
    for r in leads:
        _ft = _fc_type(r.get('Case #', ''))          # HOA (whole 1st mortgage survives) vs MORTGAGE foreclosure
        d = {
            'tier': r.get('tier',''), 'score': r.get('score',0),
            'auction': r.get('AuctionDate',''), 'days': r.get('days_to_auction',0),
            'case': r.get('Case #',''), 'owners': r.get('owners',''),
            'addr': _clean_addr(r.get('Address','')), 'mail': _clean_addr(r.get('mailing_address','')),
            # assessed_value: the Miami-Dade scrape writes the TITLE-CASE key 'Assessed Value' as a
            # formatted string ("$225,577.00"); nothing on this path has ever set the lowercase key
            # this line used to read, so all 350 MD rows shipped assessed_value=0 while 201 of them
            # carried a real number in leads_final.json. Broward/Palm Beach were unaffected
            # (county_leads.py sets it properly), which is why the gap looked like a county quirk
            # rather than a key typo. Accept either key and either shape.
            'value': r.get('market_value',0) or 0,
            'assessed_value': _money(r.get('assessed_value') or r.get('Assessed Value')),
            'judg': r.get('judgment',0) or 0,
            'eq': r.get('equity_pct',0), 'eqfake': bool(r.get('eq_fake')), 'hs': bool(r.get('homestead')),
            # --- FS 825.103 elder guardrail (Playbook §0.5) -------------------------------------
            # ownerAge is the ONLY authoritative field and is empty today: skip trace returns no
            # age/DOB (BatchData confirmed; see notes at L99/L502). When a provider that DOES carry
            # DOB lands (PropStream migration), populate 'ownerAge' here and the board's chip
            # upgrades itself from ELDER? to ELDER-65 with no template change.
            # Until then the chip runs on two weak, clearly-labelled proxies: the widow exemption
            # and ownership tenure (lsd). Over-inclusive on purpose — see _has_widow().
            'ownerAge': r.get('owner_age') or 0,
            'wid': bool(r.get('widow')),
            'lsd': (r.get('last_sale_date') or '')[:10],
            # condo -> the displayed equity is a GROSS upper bound: a special assessment (40-yr recert) or a
            # 2nd mortgage can erase it and neither is in public data. Drives the "verify equity" caveat + a
            # MARGINAL cap until the association estoppel is entered. (Lesson from the Hondroulis condo deal.)
            # Full dor_desc string carried through so the row's property-type chip (_ptype in the
            # template) can render "Single Family" / "Townhouse" / etc. on Miami-Dade rows. Without
            # this pass-through the JS side sees no dor_desc and renders no chip for MD leads
            # (BW/PB rows already come through the slim.extend path with dor_desc intact).
            'dor_desc': r.get('dor_desc',''),
            # the physical facts Jose asks for on a live call ("how big is it? how many beds?") —
            # they were sitting in leads_final.json and never reaching the site.
            'beds': r.get('beds',0) or 0, 'baths': r.get('baths',0) or 0, 'sqft': r.get('living_area',0) or 0,
            # Zillow listing status (listing_status.py): LISTED/PENDING/SOLD/RENTAL/OFF-MARKET +
            # asking price + days-on-Zillow. County rows pass through via slim.extend; MD needs
            # the explicit copy just like dor_desc/photos.
            'zstatus': r.get('zstatus',''), 'zprice': r.get('zprice',0) or 0, 'zdoz': r.get('zdoz',0) or 0,
            # Zillow Zestimate (AVM, listing_status.py) — advisory value cross-check, never drives
            # the math. Same MD-explicit-copy rule as zstatus; county rows re-stamp from cache.
            'zest': r.get('zest',0) or 0,
            # Listing-agent contact (listing_status.py, added 2026-08-05) — only meaningful when
            # zstatus is LISTED/PENDING; empty on every other status by construction upstream.
            # Same explicit-copy rule as the rest of this block.
            'zagent': r.get('zagent','') or '', 'zagentphone': r.get('zagentphone','') or '',
            'zagentemail': r.get('zagentemail','') or '', 'zbroker': r.get('zbroker','') or '',
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
            # the DOOR: when the last stay CLOSED (dismissal/discharge/relief court date). A
            # fresh-dismissed owner just lost the shield — sale resets, contact legal, max urgency.
            'saleLift': r.get('sale_stay_lifted', ''),
            # Sunbiz humans behind a company owner (llc_officers.py): managers/officers with
            # people-search links + the registered agent — a company deal is still a person.
            'llcppl': (llcs.get(r.get('Case #', '')) or {}).get('officers', []),
            'llcra': (llcs.get(r.get('Case #', '')) or {}).get('ra', ''),
            'llcraaddr': (llcs.get(r.get('Case #', '')) or {}).get('ra_addr', ''),
            'llcstat': (llcs.get(r.get('Case #', '')) or {}).get('status', ''),
            # Sunbiz has NO entity under this name (foreign LLC / tax-roll misspelling): the site
            # routes to the DEED instead of showing an empty block or a fuzzy stranger's name.
            'llcnf': bool((llcs.get(r.get('Case #', '')) or {}).get('nf')),
            # the entity Sunbiz actually matched — shown in the UI so a wrong pairing is obvious
            'llcmatch': (llcs.get(r.get('Case #', '')) or {}).get('matched', ''),
            # LEGAL DESCRIPTION — the only parcel identifier a lis pendens carries. LP filings arrive
            # with no folio and no street address (all 125 of them), so the row had nothing to show
            # and rendered as a $0 property. The legal is what he pastes into the appraiser search to
            # pull the folio; once that lands, value/photo/comps follow. Cheap to bake, useless to omit.
            'legal': r.get('legal','') or r.get('legal_desc','') or '',
            'bookpage': r.get('bookpage','') or '',
            'bought': r.get('bought_year',0), 'bprice': r.get('last_sale_price',0) or 0,
            'people': r.get('people_url',''), 'peopleaddr': r.get('people_addr_url',''), 'cyberbg': r.get('cyberbg_url',''), 'cyberbgaddr': r.get('cyberbg_addr_url',''), 'ctype': r.get('case_type',''),
            'plaintiff': r.get('plaintiff',''), 'defs': r.get('defendants',''),
            'named': r.get('named', []),   # [{name,url}] co-parties: humans get a People-search URL, companies ''
            'docket': r.get('docket_url',''), 'tax': r.get('tax_url','') or _tax_url_from_folio(r),
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
        # EQUITY STATE on EVERY lead, chain or no chain (see equity_state.py). An empty chain is
        # a FINDING, not an absence: 'searched 30 instruments, nothing survives' and 'never
        # checked' are opposite facts that both used to render as a blank cell — which is how a
        # verified-clear lead sat invisible next to a guess. Stamped before the liens gate below
        # so it is set even when that gate skips.
        _es.apply(d, rlh)
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
            # MOBILE-FIRST + non-DNC-first ordering so the row leads with a number that actually gets
            # answered/texted. Landlines to distressed owners are near-dead; the UI mutes them + drops
            # WhatsApp on them. phtype rides along so the call sheet can label/style each line.
            def _pk(p):
                mob = (p.get('type') or '').lower().startswith('mob')
                return (1 if p.get('dnc') else 0, 0 if mob else 1)
            _phs = sorted([p for p in hit['phones'] if p.get('number')], key=_pk)[:4]
            d['phones'] = [p.get('number') for p in _phs]
            d['phdnc'] = [bool(p.get('dnc')) for p in _phs]
            d['phtype'] = ['mobile' if (p.get('type') or '').lower().startswith('mob') else 'landline' for p in _phs]
            # WHICH number to dial first. Every signal is already here (type/carrier/dnc) — it was just
            # never ranked, so the first dial was a coin flip between the owner's cell, a dead landline
            # and a number the registry says never to call. phrank[i] labels each number; phbest is the
            # index of the one to try first (None when every number is DNC-flagged — 67 leads are in
            # exactly that state, and on those the correct number of dials is ZERO).
            try:
                import phone_rank as _PR
                _ranked, _blocked = _PR.rank(_phs)
                _lbl = {}
                for _r in _ranked + _blocked:
                    _lbl[str(_r.get('number'))] = _r.get('label')
                d['phrank'] = [_lbl.get(str(p.get('number')), '') for p in _phs]
                _top = _ranked[0]['number'] if _ranked else None
                d['phbest'] = next((i for i, p in enumerate(_phs)
                                    if str(p.get('number')) == str(_top)), None) if _top else None
            except Exception:
                d['phrank'] = []
                d['phbest'] = None
            d['emails'] = (hit.get('emails') or [])[:3]
        # Radius comps (comps.py MD path via the county's own MD_ComparableSales layer) — same
        # merge the BW/PB loop below does; without this, MD rows never showed an ARV.
        _cp = comps.get(r.get('Case #',''))
        if _cp:
            d['arv'] = _cp.get('arv', 0); d['arvconf'] = _cp.get('conf', '')
            d['arvpsf'] = _cp.get('psf', 0); d['arvn'] = _cp.get('n', 0)
            d['comps'] = _cp.get('comps', [])
        _dd = diligence.get(r.get('Case #', ''))
        if _dd:
            d['diligence'] = _dd
        # sibling foreclosure cases on the same owner (sibling_cases.py) — slimmed to what the
        # UI needs to say "this deal is already gone" and prove it.
        _sb = siblings.get(r.get('Case #', ''))
        if _sb and _sb.get('sibs'):
            d['sib'] = [{'case': s.get('case',''), 'sold': bool(s.get('sold')),
                         'tpb': s.get('tpb',''), 'title': s.get('cert_title',''),
                         'sale': s.get('cert_sale',''), 'pl': s.get('plaintiff',''),
                         'conf': s.get('conf','')} for s in _sb['sibs']]
            d['sibclaimed'] = bool(_sb.get('claimed'))
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
                # BatchData fallback for the counties whose Official Records we can't scrape (Palm
                # Beach has no *_liens.json at all): use the property-API chain where the county
                # scrape gave us nothing. rl already has BatchData merged in above, so reuse it.
                if (not _h or not _h.get('liens')) and rl.get(_d.get('case', '')):
                    _bh = rl[_d.get('case', '')]
                    if _bh.get('liens'):
                        _h = _bh
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
                # Deep Diligence (diligence.py) — Capri-H brief on Call Sheet
                _dd = diligence.get(_d.get('case', ''))
                if _dd:
                    _d['diligence'] = _dd
                # Sunbiz humans behind a company owner (llc_officers.py)
                _lo = llcs.get(_d.get('case', ''))
                if _lo and (_lo.get('officers') or _lo.get('ra')):
                    _d['llcppl'] = _lo.get('officers', []); _d['llcra'] = _lo.get('ra', '')
                    _d['llcraaddr'] = _lo.get('ra_addr', ''); _d['llcstat'] = _lo.get('status', '')
                    _d['llcmatch'] = _lo.get('matched', '')
                elif _lo and _lo.get('nf'):
                    _d['llcnf'] = True
                # ANNUAL TAX for the statewide counties. Miami-Dade bakes est_annual_tax at line 467
                # (1.3% of value on homestead, 2.1% otherwise); Broward and Palm Beach shipped NOTHING
                # — 0 of 189 and 0 of 182 — so every diligence brief on those counties showed no tax
                # line at all, and the money math treated the unknown as $0. Same roll-value model,
                # same rates, verified against the 198 Miami-Dade leads that carry both figures
                # (implied effective rate: p25 1.30%, median/p75 2.10%). Clearly an ESTIMATE off the
                # roll — the per-parcel tax link on the row is still the number to trust before wiring.
                if not (_d.get('etax') or 0):
                    _tv = float(_d.get('value') or 0) or 0
                    if _tv:
                        _d['etax'] = round(_tv * (0.013 if _d.get('hs') else 0.021))
                        _d['etaxest'] = True          # flag it so the UI can never pass it off as billed
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
                    #  3. COUNTY CIVIL IS A HARD GATE, not a guess. Court jurisdiction is a matter of
                    #     law: county civil is capped (~$50k), so it cannot hear a residential FIRST
                    #     mortgage foreclosure — those are association/junior cases where the 1st
                    #     SURVIVES. A chain that names a bank plaintiff on a CC case is matching the
                    #     wrong instrument, not proving senior equity. Measured on the live board: of
                    #     23 Palm Beach CC cases, 10 were overridden to MORTGAGE by the chain and 3
                    #     had the risk flag cleared outright — including $18,416 owed on a $1,147,680
                    #     Wellington house, which headlined as ~98% equity. Being wrong toward
                    #     "verify the mortgage" costs one lookup; being wrong the other way costs the
                    #     whole house.
                    if _county_civil(_d.get('case', '')):
                        _d['mr'] = True; _d['eqfake'] = True
                        # Flagging the equity as fantasy is only half the job — the SCORE was already
                        # computed from that fantasy, so without re-tiering the lead still headlines
                        # Tier A on the equity sort and lands in the Closers cockpit. Mirror the
                        # HOA-branch downgrade exactly. NOTE: ctype stays 'Bank/Mortgage' on purpose —
                        # a county-civil case with a bank plaintiff is usually a JUNIOR/HELOC action,
                        # not an association one, and calling it "HOA" would trade one false label for
                        # another. What matters is the shared truth: the first mortgage survives.
                        _db = 10 if (isinstance(_d.get('days'), int) and 0 <= _d['days'] <= 30) else 0
                        _d['score'] = (max(0, min(100, (10 if _d.get('hs') else 0) + _db)) if _d.get('value') else 0)
                        _d['tier'] = 'C'
                    elif _h and _h.get('ftype') == 'MORTGAGE' and not _h.get('surv') and not _suspect_ratio:
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
                # EQUITY STATE for the county lanes too — same rule, same module, no drift.
                # This is the path that was hiding 19 VERIFIED-CLEAR Broward leads and 226
                # Palm Beach ceiling-only chains behind an empty cell.
                _es.apply(_d, _h)
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
                    # ONE filtered sequence, three arrays. `phones` used to filter `if p.get('number')`
                    # while `phdnc`/`phtype` did not — so a single empty-number row in the skiptrace
                    # output shifted every flag after it onto the WRONG number: a DNC-flagged phone
                    # read as clean and dialable, a clean one as DNC. Parallel arrays must be drawn
                    # from the same list or they are not parallel.
                    _sph = [p for p in _ph['phones'] if p.get('number')][:4]
                    _d['phones'] = [p.get('number') for p in _sph]
                    _d['phdnc'] = [bool(p.get('dnc')) for p in _sph]
                    # phtype was dropped here while the Miami-Dade merge above (line ~1110) kept it.
                    # skiptrace.py stores a per-phone type and 447 of the Broward/Palm Beach numbers
                    # come back 'Land Line' — but with the array missing, both consumers
                    # (textablePhones and the row's text button) DEFAULT TO MOBILE, so the board
                    # offered a Text button on hundreds of landlines. Those texts go nowhere and
                    # the operator reads the silence as "they ignored me" instead of "that was a
                    # landline". Same expression as the MD path so the two cannot drift again.
                    _d['phtype'] = ['mobile' if (p.get('type') or '').lower().startswith('mob') else 'landline'
                                    for p in _sph]
                    _d['emails'] = (_ph.get('emails') or [])[:3]
            slim.extend(xl)
            _nl = sum(1 for _d in xl if _d.get('orliens'))
            _np = sum(1 for _d in xl if _d.get('phones'))
            print(f"merged {len(xl)} leads from {os.path.basename(_xf)}" +
                  (f" ({_nl} with lien chains)" if _nl else "") + (f" ({_np} with phones)" if _np else ""))
        except Exception as e:
            print(f"skip {_xf}: {e}")

    # bake code-enforcement liens (code_liens.py, free Miami-Dade CCVIOL ArcGIS, folio-keyed). A code
    # lien is a JUNIOR lien that never shows in the mortgage chain, so a lead reading "90% equity" can
    # be quietly underwater once the county's accrued fines attach. codeliens = [{case,st,stLabel,
    # problem,lien,lienRef}]; codeConcern = the worst status on the parcel, for a one-glance chip.
    _clf = os.path.join(HERE, 'code_liens.json')
    if os.path.exists(_clf):
        try:
            _cl = json.load(open(_clf, encoding='utf-8')); _cn = 0
            for _r in slim:
                _f = str(_r.get('folio') or '').strip().replace('-', '')
                _hits = _cl.get(_f)
                if _hits:
                    _r['codeliens'] = _hits[:6]
                    # worst-first: county-foreclosing > recorded-lien > open/referred
                    if any(h.get('st') == '9' for h in _hits):   _r['codeConcern'] = 'foreclosing'
                    elif any(h.get('lien') for h in _hits):       _r['codeConcern'] = 'lien'
                    else:                                          _r['codeConcern'] = 'open'
                    _cn += 1
            if _cn:
                print(f"code liens: flagged {_cn} lead(s) with an open case or recorded code lien")
        except Exception as e:
            print(f"code_liens.json skipped ({e})")

    # bake the PropStream overlay (propstream_import.py, CSV bridge — PropStream has no API).
    # Advisory context on leads we already have: their AVM vs ours, open-loan balance, distress
    # flags we cannot scrape (divorce, bankruptcy, tax-delinquent). psPhones/psEmails stay
    # QUARANTINED — no DNC scrub to vouch for, so they never enter r.phones (FTSA).
    _psf = os.path.join(HERE, 'propstream_overlay.json')
    if os.path.exists(_psf):
        try:
            _ps = json.load(open(_psf, encoding='utf-8')); _pn = 0
            for _r in slim:
                _h = _ps.get(_r.get('case', ''))
                if _h:
                    for _k in ('psValue', 'psEquity', 'psOpenLoans', 'psDistress', 'psPhones', 'psEmails', 'psJoin'):
                        if _h.get(_k) not in (None, '', [], 0):
                            _r[_k] = _h[_k]
                    _pn += 1
            if _pn:
                print(f"propstream: overlay merged onto {_pn} lead(s)")
        except Exception as e:
            print(f"propstream_overlay.json skipped ({e})")

    # bake verified delinquent property taxes (county_taxes.py — Miami-Dade + Broward, Playwright
    # past the Cloudflare wall). Back taxes are a FIRST-PRIORITY lien that survives foreclosure
    # (FS 197.122) and are invisible to the mortgage chain, so a "records-verified $0 survives" lead
    # can still owe six figures. taxDue feeds the deal math as the DEFAULT back-tax unless the
    # operator typed their own override; taxCert flags that a certificate was SOLD, which means a
    # second, separate tax-deed foreclosure clock is already running on the parcel.
    # PALM BEACH IS DELIBERATELY ABSENT: its collector is not on the county-taxes.com platform
    # (DNN/__VIEWSTATE postback), so no PB lead ever gets a taxDue and none gets a false $0 either.
    _ctf = os.path.join(HERE, 'county_taxes.json')
    if os.path.exists(_ctf):
        try:
            _ct = json.load(open(_ctf, encoding='utf-8')); _ctn = 0
            for _r in slim:
                _cty = str(_r.get('county') or 'MIAMI-DADE')
                if _cty not in ('MIAMI-DADE', 'BROWARD'):
                    continue
                _f = re.sub(r'\D', '', str(_r.get('folio') or ''))
                _h = _ct.get(_f)
                # guard the join: only apply a record scraped for THIS lead's county
                if _h and _h.get('due') and str(_h.get('county') or _cty) == _cty:
                    _r['taxDue'] = int(_h['due'])
                    _r['taxYears'] = [y.get('year') for y in (_h.get('years') or []) if y.get('year')]
                    _r['taxCert'] = bool(_h.get('cert'))
                    _r['taxChecked'] = _h.get('checked', '')
                    _ctn += 1
            if _ctn:
                print(f"county taxes: {_ctn} lead(s) carry verified delinquent taxes")
        except Exception as e:
            print(f"county_taxes.json skipped ({e})")

    # ---- RE-CLOCK days AT BAKE TIME ------------------------------------------------------------
    # `days` was a snapshot frozen at SCRAPE time and copied forever. The county lead files are
    # rewritten in place by half a dozen enrichment passes, none of which recompute it — so when a
    # county's real scrape stalls (Broward, routinely), every one of its rows drifts a day further
    # off per rebuild. Caught live 2026-08-03: all 159 Broward rows were +3 days wrong, and
    # COCE-25-088956 sat in the URGENT lane reading "closing today" for an auction held 3 days
    # earlier. The auction DATE is the durable fact; the countdown belongs to the day the page is
    # built. Rows without a parseable date keep their value (preserves the LP 9999 no-sale sentinel
    # and the county -1 parse-failure sentinel). Negative = auction passed; the board retires those
    # from outreach and shows the surplus play.
    _today_rc = datetime.now().date()
    _rcn = _rcchg = 0
    for _r in slim:
        _auc = str(_r.get('auction') or '').strip()
        if not _auc:
            continue
        try:
            _d_new = (datetime.strptime(_auc, '%m/%d/%Y').date() - _today_rc).days
        except Exception:
            continue
        _rcn += 1
        if _r.get('days') != _d_new:
            _r['days'] = _d_new
            _rcchg += 1
    print(f"days re-clocked: {_rcn} dated rows, {_rcchg} corrected")

    # ---- ACCRUE POST-JUDGMENT INTEREST ---------------------------------------------------------
    # Same class of bug as the stale `days` above, but it costs REAL MONEY instead of a wrong
    # countdown. The county publishes the judgment AMOUNT, which is frozen at entry; what a payoff
    # actually costs on sale day is that amount plus post-judgment interest (FS 55.03), which runs
    # at the rate the FJ recites and RE-ADJUSTS every January 1. On 1212 NE 91 ST the board read
    # "$599,980 equity" from a judgment entered 12/04/2024 — by the 09/02/2026 sale the true payoff
    # is ~$1.35M and the real equity ~$416k. A $184k overstatement, on a number an operator says
    # out loud to a homeowner.
    # judgment_interest.py pulls the entry DATE off the Miami-Dade docket (the counties never
    # publish it) into judgment_dates.json. NO DATE -> NO ACCRUAL, EVER: the row keeps its
    # as-entered judgment and carries jaccrued=False so the board can label it honestly rather
    # than invent a payoff. Broward/PB have no open docket API here, so they stay unaccrued.
    try:
        import judgment_interest as _JI
        _jdates = _JI.load_cache()
        _jn = _jskip = 0
        _jsum = 0.0
        for _r in slim:
            _c = str(_r.get('case') or '').strip()
            _amt = float(_r.get('judg') or 0)
            _ent = (_jdates.get(_c) or {}).get('d') or ''
            _jd = _JI._parse_date(_ent) if _ent else None
            if _amt <= 0 or not _jd:
                _r['jaccrued'] = False
                if _amt > 0:
                    _jskip += 1
                continue
            # accrue to the auction date; if the sale has no date yet, accrue to today (what a
            # payoff would cost right now — the honest "as of" for an LP/undated lead).
            _asof = None
            _aucs = str(_r.get('auction') or '').strip()
            if _aucs:
                try:
                    _asof = datetime.strptime(_aucs, '%m/%d/%Y').date()
                except Exception:
                    _asof = None
            _asof = _asof or _today_rc
            _acc = _JI.accrue(_amt, _jd, _asof, stated_rate=(_jdates.get(_c) or {}).get('rate'))
            if _acc['interest'] <= 0:
                _r['jaccrued'] = False
                continue
            _r['payoff'] = _acc['payoff']         # what it costs to satisfy on `asof`
            _r['jaccr'] = _acc['interest']        # the interest alone (shown as a delta)
            _r['jdate'] = _jd.isoformat()         # entry date, so the board can cite it
            _r['jasof'] = _asof.isoformat()
            _r['jaccrued'] = True
            _jn += 1
            _jsum += _acc['interest']
        print(f"post-judgment interest: {_jn} row(s) accrued (+${_jsum:,.0f} total unseen debt), "
              f"{_jskip} judgment(s) left as-entered (no verified entry date)")
    except Exception as _e:
        print(f"post-judgment interest: SKIPPED ({_e}) — judgments shown as entered")

    # Redfin Estimate (redfin_value.py) — advisory second-opinion value, folio-keyed post-pass.
    # A PURE sidecar merge: rfval/rfurl/rfchk never touch the lead files, so they can't be lost by
    # the MD rebuild or the county slim.extend. Only apply a real, address-matched estimate
    # (v>0 and conf in ok/addr); nomatch/zero entries are skipped so the row stays clean. Advisory
    # only — nothing here feeds val/_valsrc/_profit (see tracker_template.html r._valx).
    _rff = os.path.join(HERE, 'redfin_cache.json')
    if os.path.exists(_rff):
        try:
            _rf = json.load(open(_rff, encoding='utf-8')); _rfn = 0
            for _r in slim:
                _f = re.sub(r'\D', '', str(_r.get('folio') or ''))
                _h = _rf.get(_f)
                if _h and int(_h.get('v') or 0) > 0 and _h.get('conf') in ('ok', 'addr'):
                    _r['rfval'] = int(_h['v'])
                    _r['rfurl'] = _h.get('url', '')
                    _r['rfchk'] = time.strftime('%Y-%m-%d', time.localtime(_h.get('t') or 0))
                    _rfn += 1
            if _rfn:
                print(f"redfin: {_rfn} lead(s) carry a Redfin Estimate")
        except Exception as e:
            print(f"redfin_cache.json skipped ({e})")

    # bake lat/lng from geocode_cache.json (geo_enrich.py, keyless US Census) so the board's origin-
    # anchored door route can sort deals by REAL distance and expand outward from home.
    _gcf = os.path.join(HERE, 'geocode_cache.json')
    if os.path.exists(_gcf):
        try:
            _gc = json.load(open(_gcf, encoding='utf-8')); _gn = 0
            for _r in slim:
                _g = _gc.get(_r.get('case', ''))
                if _g and _g.get('lat'):
                    _r['lat'] = _g['lat']; _r['lng'] = _g['lng']; _gn += 1
            if _gn:
                print(f"baked lat/lng into {_gn} leads (origin-anchored route)")
        except Exception:
            pass

    # bake Whitepages Pro property-endpoint results (whitepages_lookup.py, gitignored). One paid call
    # per property returned every deed-holder + resident with full unmasked phones (typed mobile/
    # landline) + emails + current mailing address. What Chrome-Claude found on Velima the hard way,
    # now available on every cached lead. Key surfaces:
    #   wpOwners   -> [{name, phones:[{n,type,rank}], emails:[str], city, state, absentee: bool}]
    #                 absentee=true when the owner's current mailing address is NOT in the property
    #                 city (e.g. Velima's co-owner Jacob lives in Lewisville TX -> call, don't knock)
    #   wpAllPhones-> deduped ordered union of every owner's phones (mobile-first, non-DNC first)
    #                 injected into the lead's `phones` array so the existing UI + copy paths pick them
    #   wpAllEmails-> deduped union of emails, injected into `emails`
    #   wpKey      -> 'ok' when we got a real record, 'none' when we tried and got 404, missing if we
    #                 haven't looked yet (the call sheet uses this to distinguish gaps from misses)
    # Opaque property.whitepages.com /property/{id} deep-links (manual or cookie-resolved).
    # Safe to commit the id map — no phones/PII, just public property page slugs.
    _wp_ids = {}
    _wp_id_path = os.path.join(HERE, 'wp_prop_ids.json')
    if os.path.exists(_wp_id_path):
        try:
            _wp_ids = json.load(open(_wp_id_path, encoding='utf-8')) or {}
        except Exception:
            _wp_ids = {}

    _wpf = os.path.join(HERE, 'whitepages_lookup.json')
    if os.path.exists(_wpf) or _wp_ids:
        try:
            _wp = json.load(open(_wpf, encoding='utf-8')) if os.path.exists(_wpf) else {}
            _wn = _wpn = _wpe = _wid = 0
            def _prop_city_state(r):
                a = r.get('addr') or r.get('Address') or ''
                p = [s.strip() for s in a.split(',')]
                return (p[1] if len(p) > 1 else '').upper(), 'FL'
            def _wp_own(o, prop_city):
                addrs = o.get('current_addresses') or []
                city = state = ''
                if addrs:
                    city = (addrs[0].get('city') or '').upper()
                    state = (addrs[0].get('state') or addrs[0].get('state_code') or '').upper()
                absentee = bool(state and state != 'FL') or bool(city and prop_city and city != prop_city)
                phs = []
                for p in (o.get('phones') or []):
                    n = ''.join(c for c in (p.get('number') or '') if c.isdigit())
                    if len(n) == 11 and n.startswith('1'): n = n[1:]
                    if len(n) != 10: continue
                    phs.append({'n': n, 'type': (p.get('type') or '').lower()})
                emails = [e.get('email') for e in (o.get('emails') or []) if e.get('email')]
                return {'name': o.get('name',''), 'phones': phs, 'emails': emails,
                        'city': city, 'state': state, 'absentee': absentee}
            def _rank(p):                          # mobile-first, then landline, then unknown/other
                t = p['type']
                return 0 if 'mob' in t else (1 if 'land' in t else 2)
            for _r in slim:
                _case = _r.get('case', '')
                # Deep-link id: cache _prop_id wins, then committed wp_prop_ids.json
                _pid = ((_wp.get(_case) or {}).get('_prop_id') or _wp_ids.get(_case) or '').strip()
                if _pid:
                    _r['wpPropId'] = _pid
                    _wid += 1
                _hit = _wp.get(_case)
                if not _hit: continue
                _res = (_hit.get('result') or {})
                _oi = (_res.get('ownership_info') or {})
                _po = _oi.get('person_owners') or []
                _pc, _ = _prop_city_state(_r)
                _owns = [_wp_own(o, _pc) for o in _po]
                _person_recs = _hit.get('_person') or []
                # Person-only cache rows (LP/upcoming with no address) have no property owners —
                # still bake phones from the Person layer. Bare _prop_id stamps without a lookup
                # are not a miss; skip quietly so we don't mark Marisela-style id-only rows 'none'.
                if not _owns and not any((p.get('response') or []) for p in _person_recs):
                    if _hit.get('result') is not None or _hit.get('_http') == 404:
                        _r['wpKey'] = 'none'
                    continue
                # dedup phones + emails across owners; mobile first, absentee-owner phones tagged
                _seen_ph, _all_ph = set(), []
                for _o in _owns:
                    for _p in sorted(_o['phones'], key=_rank):
                        if _p['n'] in _seen_ph: continue
                        _seen_ph.add(_p['n'])
                        _all_ph.append({'n': _p['n'], 'type': _p['type'], 'owner': _o['name'], 'absentee': _o['absentee']})
                _seen_em, _all_em = set(), []
                for _o in _owns:
                    for _e in _o['emails']:
                        if _e.lower() in _seen_em: continue
                        _seen_em.add(_e.lower()); _all_em.append(_e)
                # RESIDENTS — the other people Whitepages places at this address (spouse, adult kids,
                # relatives). Same shape as person_owners. These were already PAID FOR on every
                # property call and sat unread in the cache: 41 of 48 cached leads carry them, worth
                # hundreds of dialable numbers. They are NOT the deed owner, so they are tagged
                # resident:true — the play is "is <owner> home?", not "hi <owner>". Ranked after the
                # owners so an owner's own mobile always leads.
                _residents = []
                for _rz in (_res.get('residents') or []):
                    _rr = _wp_own(_rz, _pc)
                    if not (_rr['phones'] or _rr['emails']): continue
                    _rr['resident'] = True
                    _residents.append(_rr)
                    for _p in sorted(_rr['phones'], key=_rank):
                        if _p['n'] in _seen_ph: continue
                        _seen_ph.add(_p['n'])
                        _all_ph.append({'n': _p['n'], 'type': _p['type'], 'owner': _rr['name'],
                                        'absentee': _rr['absentee'], 'resident': True})
                    for _e in _rr['emails']:
                        if _e.lower() in _seen_em: continue
                        _seen_em.add(_e.lower()); _all_em.append(_e)
                # Person Search layer (whitepages_lookup.py --deep / auto-fallback when Property was thin):
                # extra phones + emails sourced from the owner NAME rather than the property address.
                # Includes aliases + address history + relatives-tagged numbers Property doesn't touch.
                # Merged into wpAllPhones with source='person' so the UI can tag them.
                _person_phones = []
                _person_emails = []
                for _pr in _person_recs:
                    for _rec in (_pr.get('response') or []):
                        for _p in (_rec.get('phones') or []):
                            _n = ''.join(c for c in (_p.get('number') or '') if c.isdigit())
                            if len(_n) == 11 and _n.startswith('1'): _n = _n[1:]
                            if len(_n) != 10: continue
                            if _n in _seen_ph: continue
                            _seen_ph.add(_n)
                            _pt = (_p.get('type') or '').lower()
                            _person_phones.append({'n': _n, 'type': _pt, 'owner': _pr.get('name',''), 'absentee': False, 'source': 'person'})
                        for _e in (_rec.get('emails') or []):
                            _ea = _e.get('address') or _e.get('email') or ''
                            if _ea and _ea.lower() not in _seen_em:
                                _seen_em.add(_ea.lower()); _person_emails.append(_ea)
                # tag property-sourced numbers explicitly and merge person after them
                for _p in _all_ph: _p.setdefault('source', 'property')
                _all_ph.extend(sorted(_person_phones, key=_rank))
                _all_em.extend(_person_emails)
                _r['wpOwners'] = _owns
                if _residents: _r['wpResidents'] = _residents
                _r['wpAllPhones'] = _all_ph
                _r['wpAllEmails'] = _all_em
                _r['wpPersonRecs'] = len(_person_recs)
                _r['wpKey'] = 'ok' if (_owns or _all_ph) else 'none'
                # merge WP phones into the lead's existing `phones` array — dedupe against skiptrace
                # numbers so we don't double-list, cap at 8 total to keep the row readable
                _cur = set()
                for _p in (_r.get('phones') or []):
                    _pn = _p.get('number') if isinstance(_p, dict) else str(_p)
                    _cur.add(''.join(c for c in _pn if c.isdigit())[-10:])
                # SHAPE MUST MATCH THE SKIPTRACE PATH. r.phones is a list of bare digit STRINGS with
                # metadata carried in the PARALLEL arrays r.phtype / r.phdnc (see the skiptrace merge
                # above). This block used to append DICTS instead, so the UI's String(ph) turned each
                # WP number into "[object Object]" -> 0 digits -> _contactLineHtml bailed and the line
                # rendered EMPTY. Result: 116 numbers across 30 leads that were paid for and then
                # silently dropped from Call, Text, WhatsApp and the CSV export.
                _new = [p for p in _all_ph if p['n'] not in _cur]
                if _new:
                    _ph  = list(_r.get('phones') or [])
                    _pt  = list(_r.get('phtype') or [])
                    _pd  = list(_r.get('phdnc') or [])
                    # keep the parallel arrays aligned with any pre-existing phones before extending
                    while len(_pt) < len(_ph): _pt.append('')
                    while len(_pd) < len(_ph): _pd.append(False)
                    for p in _new:
                        _ph.append(p['n'])
                        _pt.append('mobile' if 'mob' in p['type'] else ('landline' if 'land' in p['type'] else ''))
                        # Whitepages returns no DNC/TCPA flag — record unknown (False = "not flagged"),
                        # never a positive claim that the number is scrubbed.
                        _pd.append(False)
                    _r['phones'] = _ph[:8]
                    _r['phtype'] = _pt[:8]
                    _r['phdnc']  = _pd[:8]
                    _wpn += len(_new)
                if _all_em:
                    _cur_em = set((e or '').lower() for e in (_r.get('emails') or []))
                    _add_em = [e for e in _all_em if e.lower() not in _cur_em]
                    if _add_em:
                        _r['emails'] = (_r.get('emails') or []) + _add_em
                        _r['emails'] = _r['emails'][:6]
                        _wpe += len(_add_em)
                _wn += 1
            if _wn:
                print(f"WhitepagesPro: enriched {_wn} leads (+{_wpn} phones, +{_wpe} emails, absentee-owner flags set)")
            if _wid:
                print(f"Whitepages property deep-links: {_wid} leads with wpPropId")
        except Exception as _e:
            print('WhitepagesPro merge skipped:', _e)

    # ---- PHONE RANKING, after every phone mutation is done ---------------------------------------
    # "Put the ranked-best number first" was a silent no-op for most of the board. Only the
    # Miami-Dade enrichment path set phrank/phbest (line ~1560); the Broward / Palm Beach county
    # merge sets phones/phdnc/phtype and never ranks, so `phbest` was None and the first dial on
    # those leads was whatever order the county happened to return — a coin flip between a cell, a
    # dead landline, and a number the registry says never to call. The Whitepages merge above then
    # APPENDS numbers without extending phrank, leaving even MD rows with labels shorter than their
    # phone list.
    #
    # Runs here because this is the first point at which no code will touch `phones` again. It only
    # FILLS IN what is missing or stale — never overwrites a rank that already covers every number —
    # so it cannot disturb a lead that was already ranked correctly. Per-lead try/except: a ranking
    # failure must cost the ordering on one lead, never the build.
    try:
        import phone_rank as _PR
        _rk = _fx = 0
        for _r in slim:
            _ph = [str(p) for p in (_r.get('phones') or []) if p]
            if not _ph:
                continue
            _rank, _best = _r.get('phrank') or [], _r.get('phbest')
            if len(_rank) >= len(_ph) and _best is not None:
                continue                      # already ranked, and the labels cover every number
            _fx += 1
            try:
                _pd = list(_r.get('phdnc') or [])
                _pt = list(_r.get('phtype') or [])
                _objs = [{'number': n,
                          'dnc': bool(_pd[i]) if i < len(_pd) else False,
                          'type': _pt[i] if i < len(_pt) else ''} for i, n in enumerate(_ph)]
                _ranked, _blocked = _PR.rank(_objs)
                _lbl = {}
                for _x in _ranked + _blocked:
                    _lbl[str(_x.get('number'))] = _x.get('label')
                _r['phrank'] = [_lbl.get(n, '') for n in _ph]
                _top = _ranked[0]['number'] if _ranked else None
                # None when every number is DNC-flagged. On those leads the correct number of
                # dials is ZERO, and Call Mode drops them entirely.
                _r['phbest'] = next((i for i, n in enumerate(_ph) if n == str(_top)), None) if _top else None
                _rk += 1
            except Exception:
                _r.setdefault('phrank', [])
                _r.setdefault('phbest', None)
        # ALWAYS print. The first version of this pass iterated `leads` — but phones live on
        # the SLIM dicts (the WP merge iterates `for _r in slim:`), so it scanned dicts with no
        # phones key, found zero work, and printed nothing. A pass that only speaks when it finds
        # work makes "broken" and "nothing to do" the same silence — the exact defect it was
        # built to fix. Now the zero case says so out loud, with the denominator.
        _withph = sum(1 for _r in slim if _r.get('phones'))
        print('phone rank: %d of %d phone-bearing lead(s) needed ranking, %d ranked'
              % (_fx, _withph, _rk))
    except Exception as _e:
        print('phone rank: SKIPPED (%s) — first-number order falls back to county order' % str(_e)[:80])

    tpl = subst_build_facts(open(os.path.join(HERE,'tracker_template.html'), encoding='utf-8').read(),
                            f"{datetime.now():%Y-%m-%d %H:%M}")
    # Motion v13 (UMD) inlined, not CDN-linked: the Desktop twin is opened over file://, where an ESM
    # import is CORS-blocked and a network <script> dies offline. If the vendored file ever goes
    # missing the placeholder collapses to empty and _fxEnter() no-ops — the board still renders.
    tpl = tpl.replace('__MOTIONJS__', _motion_js())
    os.makedirs(os.path.join(HERE,'docs'), exist_ok=True)
    docs = os.path.join(HERE,'docs','index.html')

    # SYNTAX-GUARD every inline <script> the template emits. These blocks are built by string
    # concatenation inside JS strings, which means one dropped backslash silently ships a page that
    # parses as HTML but dies at runtime — twice in one session: `<\\/script>` (never closed the
    # tag) and `"\\""` as an object key (Unexpected string). Neither showed up in the Python tests
    # because Python never parses that JS. Node does, in ~200ms, so do it here and fail the build.
    _js_guard(tpl)

    # Substitute the build clock + ZIP centroid table BEFORE the Desktop copy is written.
    # ORDERING BUG THIS FIXES (caught in the browser 2026-07-29): the Desktop write below runs
    # ~55 lines before the __BUILT__ substitution, so any placeholder added next to BUILT was
    # left raw in the Desktop copy. __BUILT__ survived that because it sits inside quotes and the
    # gate explicitly tolerates an unsubstituted value; `const ZIP_CENT = __ZIPCENT__` is a BARE
    # identifier, so the Desktop copy threw ReferenceError at parse time and the whole board
    # failed to boot. Both new placeholders are resolved here, before either copy is written.
    # Bake the ownership-flip verdict onto every row by case (stamp-only — additive fields; if
    # ownership.json is absent this no-ops and the board is unchanged).
    for _d in slim:
        _og = ownership.get(_d.get('case', ''))
        if _og:
            _d['title_status'] = _og.get('title_status', '')
            _d['title_flag'] = _og.get('title_flag', '')
            _d['title_owner'] = _og.get('title_owner', '')
            _d['title_evidence'] = _og.get('title_evidence', '')

    # ---- DILIGENCE BAKE (Jesse's pre-contact check, as data) -----------------------------------
    # MUST run AFTER the ownership stamp directly above and BEFORE anything reads `slim`, because
    # diligence_flags.title_status_of() reads the key written on the line above this comment. A row
    # with no stamp trips HIGH_EQUITY_UNVERIFIED at HIGH instead of MED, so the order is the
    # difference between "nobody has checked this" and "we checked and it was fine".
    #
    # This is the ONLY place the Python verdict can reach the browser. The board, the Morning
    # Worker, the Closers cockpit and the printed call sheet all gate on `r.ddhold`, and an
    # UNDEFINED field in JS is falsy — so a bake that silently does not run fails OPEN with nothing
    # in the console anywhere. That is why the whole loop is wrapped (a throw here would abort
    # make_tracker and freeze the live board at the last good build, the 08-14/08-16 stale-site
    # failure class) AND why it counts what it stamped and says so out loud. "0 rows carry ddhold"
    # must look different from "the gate ran and held nobody".
    #
    # ddhold from annotate() is diligence_flags' RAW verdict; board_fields() is the CONTACT POLICY
    # (diligence_gate). They differ on ~404 rows — the lis-pendens pool, where eqfake means "no debt
    # captured yet", not "we did the arithmetic wrong". The policy one wins, so it is written last
    # and the raw one is removed when the policy releases. One field, one meaning.
    _dg_tally = None
    try:
        import diligence_flags as _DF_BAKE
        import diligence_gate as _DG_BAKE
        # Buy-box is OPTIONAL by construction: if buybox.py is missing or broken the board still
        # builds and every lane except the buy-box lane is untouched. A standing preference must
        # never be able to take down the pipeline that finds the leads in the first place.
        try:
            import buybox as _BB_BAKE
        except Exception as _bbe:
            _BB_BAKE = None
            print('buybox: SKIPPED (%s) - no buy-box lane this build' % str(_bbe)[:80])
        _dg_tally = _DG_BAKE.Tally()
        _dg_baked = 0
        for _d in slim:
            try:
                _d.update(_DF_BAKE.annotate(_d))              # flags/severity/dive, for display
                _d.update(_BB_BAKE.annotate(_d) if _BB_BAKE else {})   # standing buy-box tag
                _g = _dg_tally.check(_d)                      # the contact decision + bookkeeping
                _d.update(_DG_BAKE.board_fields(_d))
                if not _g['hold']:
                    _d.pop('ddhold', None)                    # annotate's raw verdict must not win
                if _d.get('ddhold') or _d.get('ddwarn'):
                    _dg_baked += 1
            except Exception as _de:
                # ONE bad row is one bad row. It ships ungated (the board serving nothing is worse)
                # and it is counted as unchecked, which prints on its own line below.
                _dg_tally.unchecked += 1
                if not _dg_tally.unchecked_why:
                    _dg_tally.unchecked_why = str(_de)[:120]
        print('diligence: %d of %d row(s) carry a gate verdict (ddhold/ddwarn)' % (_dg_baked, len(slim)))
        if _BB_BAKE:
            # Print the split, not just the total. "12 in the buy-box" hides that 3 of them are
            # underwater and will never appear in the lane — and a lane smaller than its headline
            # number is how people stop trusting the headline number.
            _bb_rows = [_d for _d in slim if _d.get('bb')]
            _bb_uw = sum(1 for _d in _bb_rows if _d.get('bbstate') == 'UNDERWATER')
            _bb_ok = sum(1 for _d in _bb_rows if _d.get('bbstate') == 'CONFIRMED')
            # ASCII ONLY IN THIS PRINT, and that is not a style note. This block runs inside the
            # bake's try/except, stdout on the nightly is a Windows cp1252 console, and the em-dash
            # this line originally carried raised UnicodeEncodeError -> the whole buy-box report
            # AND the diligence tally below it vanished silently while the build still exited 0.
            # A status line that can kill the status lines after it is worse than no status line.
            print('buybox: %d row(s) match a standing box - %d CONFIRMED room, %d unknown, '
                  '%d UNDERWATER (excluded from the call lane)'
                  % (len(_bb_rows), _bb_ok, len(_bb_rows) - _bb_ok - _bb_uw, _bb_uw))
        _dg_tally.report('diligence', indent='  ')
        if _dg_tally.n_held and not any(d.get('ddhold') for d in slim):
            # The tally says it held leads and not one row carries the field the browser reads.
            # That is the exact silent-failure this bake exists to make impossible — say it.
            print('  !! diligence: %d hold(s) computed but ZERO rows carry ddhold — the browser '
                  'gates will pass everything. Investigate before trusting this build.'
                  % _dg_tally.n_held)
    except Exception as _dge:
        print('diligence: SKIPPED (%s) — every contact surface on this build is UNGATED.' % str(_dge)[:120])

    # ---- PERSON KEYS (cross-case contact identity) ---------------------------------------------
    # Computed at BUILD time, not in the browser: a client-side pass would be O(n^2) inside render
    # loops over ~1,500 leads, and it would only ever see the cases in THIS build — a person whose
    # first case already went to auction and dropped off the board still spent that budget.
    _pk, _pk_skipped = _person_keys(slim)
    for _d in slim:
        _kp = _pk.get(_d.get('case', ''))
        if _kp:
            _d['pkey'], _d['pkn'] = _kp
    _multi = {v[0]: v[1] for v in _pk.values() if v[1] > 1}
    if _multi:
        print('person keys: %d owner(s) span %d cases (largest %d)%s'
              % (len(_multi), sum(_multi.values()), max(_multi.values()),
                 (' · %d shared phone/email(s) skipped as institutional' % _pk_skipped) if _pk_skipped else ''))
    # A raw phone number must never become the key — the public build strips phones/emails below and
    # a digit key would smuggle them back in. Assert it rather than trust the comment.
    _leak = [d['pkey'] for d in slim if re.fullmatch(r'\d{10}', str(d.get('pkey') or ''))]
    if _leak:
        raise SystemExit('person keys: %d raw-phone pkey(s) — would leak PII into the public build' % len(_leak))

    tpl = tpl.replace('__BUILTAT__', datetime.now().strftime('%Y-%m-%dT%H:%M'))
    tpl = tpl.replace('__ZIPCENT__', _esc_json(_zip_centroids(slim)))
    # IDENTITY DISCLOSURE — ONE SOURCE, BAKED. disclaimer.identity() is what outreach_email.py
    # already sends on every AUTOMATED send; genEmail (the manual copy-and-send path an operator
    # actually uses) had its own hand-typed variant that had drifted: it said "not a lawyer" and
    # omitted the foreclosure-rescue-company denial entirely. Same channel, same homeowner, two
    # different disclosures depending on which button was clicked. Baking it means the manual path
    # cannot drift from the automated one again — change the sentence in disclaimer.py and every
    # surface moves together.
    try:
        import disclaimer as _D
        tpl = tpl.replace('__IDENT_EN__', _esc_js(_D.identity('en', as_html=False)))
        tpl = tpl.replace('__IDENT_ES__', _esc_js(_D.identity('es', as_html=False)))
    except Exception as _e:
        # NEVER ship the raw placeholder into homeowner-facing copy. Fail the build instead.
        raise SystemExit('identity disclosure bake FAILED (%s) — refusing to build a board whose '
                         'emails would carry a literal __IDENT_EN__ placeholder.' % _e)
    # ALEJANDRO'S COLD-EMAIL COPY -- one string, baked from outreach_copy.py and rendered by BOTH
    # genEmail() and outreach_email.py, so the manual composer and the unattended nightly send
    # cannot drift. Never raises; see _bake_alex_email().
    tpl = _bake_alex_email(tpl, 'board (pre desktop twin)')
    # Owner replies detected by replies.py (gitignored replies.json, written from IMAP). Absent
    # until the operator adds gmail.key -- and an empty table is the honest state: the Proof Sheet
    # then reads "awaiting reply" for every send instead of implying nobody wrote back.
    # replies.json was dumped VERBATIM into this page until 2026-08-26 -- whole file, every field,
    # plaintext, OUTSIDE the encrypted __DATA__ payload, in a PUBLIC repo. It is keyed by case AND
    # by '@'+the owner's email, so three homeowners' personal addresses were published in the clear
    # alongside the text of what they wrote to us. The opt-out bake fifty lines down already refused
    # to publish owner text for precisely this reason; this one never got the same treatment.
    #
    # Two changes. The '@email' keys resolve to their case here (same resolver as the opt-outs), so
    # no address ships and _replyFor still finds the reply by case. And only the fields the board
    # actually reads survive -- 'email' was never one of them, it was pure exhaust.
    _replies_raw = {}
    _rf = os.path.join(HERE, 'replies.json')
    if os.path.exists(_rf):
        try: _replies_raw = json.load(open(_rf, encoding='utf-8')) or {}
        except Exception: _replies_raw = {}
    _RP_KEEP = ('stop', 'when', 'n', 'src', 'subject', 'excerpt', 'note', 'checked', 'via')
    _rp_by_email = {}
    for _sr in slim:
        for _se in (_sr.get('emails') or []):
            _rp_by_email.setdefault(str(_se).strip().lower(), []).append(str(_sr.get('case') or ''))
    _replies, _rp_unresolved = {}, 0
    for _k, _v in (_replies_raw.items() if isinstance(_replies_raw, dict) else []):
        if not isinstance(_v, dict):
            continue
        # An excerpt is the owner's own words, and a reply chain quotes its own headers, so the
        # body carries addresses even after the 'email' field is dropped. Redact them: the operator
        # needs to read what the person SAID, never their address, and this text ships plaintext.
        _slim_v = {f: (_EMAIL_RE.sub('[email]', str(_v[f])) if isinstance(_v[f], str) else _v[f])
                   for f in _RP_KEEP if f in _v}
        if str(_k).startswith('@'):
            _hit = _rp_by_email.get(str(_k)[1:].strip().lower()) or []
            if not _hit:
                _rp_unresolved += 1
                continue
            for _hc in _hit:
                if _hc and _hc not in _replies:
                    _replies[_hc] = dict(_slim_v)
        else:
            _replies[_k] = _slim_v
    _rp_leaked = [k for k in _replies if '@' in str(k)]
    if _rp_leaked:
        raise SystemExit('docs/index.html: %d reply key(s) still carry an email address and would be '
                         'published in plaintext: %s' % (len(_rp_leaked), ', '.join(_rp_leaked)[:200]))
    tpl = tpl.replace('__REPLIES__', _esc_json(_replies))
    if _replies:
        print(f'replies: {len(_replies)} owner reply/replies merged')
    if _rp_unresolved:
        print(f'replies: {_rp_unresolved} email-keyed reply/replies match no lead on the board — '
              f'not baked (their addresses are never published).')

    # ---- SERVER SEND LEDGER -------------------------------------------------------------------
    # mail_sent.json is the bridge's record of confirmed SMTP deliveries. Until now the BOARD never
    # opened it, so _mailHist / the 72h cooldown / the 24h recipient guard all ran on this browser's
    # localStorage alone -- and a profile that had not personally sent an email read the lead as
    # never-contacted and re-sent the COLD template. Measured 2026-08-08: 33 cases had received 4+
    # emails despite the FINAL variant promising the third is the last. See MAILLOG in the template.
    _mlog, _mto = _mail_ledger()
    # See _addr_key: the recipient guard matches identities, so the page gets hashes instead of the
    # 1,438 homeowner addresses it used to publish in the clear.
    _mto = {_addr_key(_a): _t for _a, _t in (_mto or {}).items()}
    tpl = tpl.replace('__MAILLOG__', _esc_json(_mlog))
    tpl = tpl.replace('__MAILTO__', _esc_json(_mto))
    # SMS counterpart. __TEXTPERSON__ is the authoritative one — it survives a case dropping off the
    # board after its auction, which a per-case count does not (the human keeps their phone).
    _tlog, _tper = _text_ledger()
    tpl = tpl.replace('__TEXTLOG__', _esc_json(_tlog))
    tpl = tpl.replace('__TEXTPERSON__', _esc_json(_tper))
    if _tlog or _tper:
        _ret = sum(1 for v in _tper.values() if v.get('n', 0) >= 3)
        print(f'text ledger: {len(_tlog)} case(s) / {len(_tper)} person(s) baked '
              f'({_ret} already at 3 sends -> retired from the cadence)')
    if _mlog:
        _staged = sum(1 for v in _mlog.values() if v['n'] >= 3)
        print(f'send ledger: {len(_mlog)} cases baked '
              f'({_staged} already at 3+ sends -> no further email), {len(_mto)} recent recipients')

    # ---- SERVER-SIDE OPT-OUT LEDGER -----------------------------------------------------------
    # optouts.json is the WRITTEN record of who told us to stop. cadence.py writes it, replies.py
    # tells the operator to act on it, and _carlos_route.py refuses to route a door to anyone in
    # it -- but until now make_tracker never opened the file, so the BOARD never saw it. The whole
    # opt-out suppression machinery in the page (logTouch's preventive guard, the Doc Room gate,
    # the Morning Worker's eligibility test, Closers, the daily plan) was fed exclusively by
    # localStorage `notes`, which lives on ONE browser profile on ONE device.
    # Concretely, today: Norma Hendy (CACE-25-005200) replied "Please stop" in writing on
    # 2026-07-28. She is suppressed only where that JSON was hand-imported. Any other device, any
    # teammate, any reinstall -- she is a callable lead again, and every contact after a written
    # opt-out is an FTSA exposure (~$500-1,500 per message) plus the thing itself: a person who
    # asked to be left alone, not being left alone.
    # Baked in here so suppression travels with the page. Merge is SAFETY-ONE-WAY on the client:
    # the server can add an opt-out, never clear one.
    _optouts = {}
    _oo_unresolved = 0          # counted inside the try; read after it, including on the error path
    _of = os.path.join(HERE, 'optouts.json')
    if os.path.exists(_of):
        try:
            _raw_oo = json.load(open(_of, encoding='utf-8')) or {}
            # the file is a notes-export envelope {_dealflow_notes, exported, device, notes:{...}}
            _oo_notes = _raw_oo.get('notes') if isinstance(_raw_oo, dict) else None
            if not isinstance(_oo_notes, dict):
                _oo_notes = _raw_oo if isinstance(_raw_oo, dict) else {}
            # AN EMAIL-KEYED OPT-OUT RESOLVES TO ITS CASE, AND NEVER SHIPS AS AN EMAIL.
            # A written STOP that arrives by reply has no case attached, so the ledger keys it
            # '@someone@gmail.com'. Baking that key verbatim did two bad things at once:
            #   1. It PUBLISHED three real homeowners' personal email addresses, in plaintext, in
            #      docs/index.html, in a PUBLIC repo. People who asked to be left alone, made
            #      searchable. The note text was already withheld for this exact reason -- but the
            #      KEY is the identifier, and it was going out in full.
            #   2. It suppressed NOBODY. The board only ever reads notes by case id; the one
            #      '@'+email lookup in the page is _replyFor (replies), not opt-outs. So the entry
            #      created an orphan note under a key nothing matches, and the cross-device
            #      suppression this whole block exists to provide silently did not cover the
            #      email-only opt-outs -- the ones most likely to be a written STOP.
            # Resolving to the case fixes both: no address leaves the machine, and the right lead
            # is actually suppressed on every device. An address that matches no lead is dropped
            # loudly -- there is nothing on the board to suppress, and outreach_email._eligible()
            # still refuses it from optouts.json, which is gitignored and stays local.
            _by_email = {}
            for _sr in slim:
                for _se in (_sr.get('emails') or []):
                    _by_email.setdefault(str(_se).strip().lower(), []).append(str(_sr.get('case') or ''))
            for _c, _n in _oo_notes.items():
                if not isinstance(_n, dict):
                    continue
                _st = str(_n.get('status') or '').upper()
                if not (_n.get('optout') or _st in ('DO NOT CONTACT', 'OPTED OUT')):
                    continue
                # carry only what suppression needs — never the free-text note (it can quote
                # the owner verbatim and this file ships inside a page that leaves the machine)
                _entry = {'optout': _n.get('optout') or '', 'status': _n.get('status') or 'DO NOT CONTACT'}
                if str(_c).startswith('@') or str(_c).startswith('#'):
                    # PERSON-LEVEL SUPPRESSION SURVIVES, HASHED.
                    # The board builds _optedOutIdentities() from notes keys starting '@'/'#' and
                    # _isOptedOutPerson() blocks EVERY case carrying that email or phone -- an owner
                    # with six concurrent sales who says stop once is suppressed on all six. That is
                    # the strongest opt-out path in the system and it is fed from here.
                    #
                    # The first cut of this fix resolved these to a case and dropped the identity
                    # key, which closed the plaintext leak and silently killed cross-case
                    # suppression with it. Emit BOTH: the case (so the right lead is suppressed even
                    # on a device that has never seen this person) and an OPAQUE identity key, so
                    # _isOptedOutPerson still works without publishing the address.
                    _raw = str(_c)[1:].strip().lower()
                    if str(_c).startswith('#'):
                        _raw = re.sub(r'\D', '', _raw)
                    _optouts[str(_c)[0] + _addr_key(_raw)] = dict(_entry)
                    for _hc in (_by_email.get(_raw) or []):
                        if _hc:
                            _optouts[_hc] = dict(_entry)
                    if not (_by_email.get(_raw) or []):
                        _oo_unresolved += 1
                else:
                    _optouts[_c] = _entry
        except Exception as e:
            print(f'optouts.json unreadable ({e}) — board falls back to device-local suppression only')
    # Belt to the resolver's braces. If an email ever reaches this dict again -- a new key shape, a
    # hand-edited ledger -- it must not be the published page that finds out.
    # '@<hash>' / '#<hash>' identity keys are intended (see above) -- reject only a real ADDRESS or
    # a real phone number. Matching on a bare '@' would reject the hashed keys the person-level
    # suppression depends on, which is how that suppression got dropped the first time.
    _leaked = [k for k in _optouts
               if _EMAIL_RE.search(str(k)) or re.fullmatch(r'#\d{7,}', str(k))]
    if _leaked:
        raise SystemExit('docs/index.html: %d opt-out key(s) still carry a real email address or '
                         'phone number and would be published in plaintext: %s'
                         % (len(_leaked), ', '.join(_leaked)[:200]))
    tpl = tpl.replace('__OPTOUTS__', _esc_json(_optouts))
    if _optouts:
        print(f'opt-outs: {len(_optouts)} owner(s) baked in from the server ledger (suppressed on EVERY device)')
    if _oo_unresolved:
        print(f'opt-outs: {_oo_unresolved} email-only opt-out(s) match no lead on the board — not baked. '
              f'The send path still refuses them from optouts.json.')

    # ---- SERVER-SIDE DEAD LEDGER --------------------------------------------------------------
    # Same disease as opt-outs, different symptom: "this deal no longer exists" lived only in one
    # browser's localStorage. Concretely: MILAGROS MARTIN (2026-003288-CA-01) was DISMISSED on
    # 07/31 (redeemed by a third-party LLC) yet stayed a Tier-A lead on the board through 08/02 —
    # she only vanished because the county happened to pull the 08/03 calendar and a full scrape
    # ran. Had the scrape been blocked (the standing Broward failure mode), the worker would still
    # be emailing a house that is out of foreclosure. deads.json is the durable record; the merge
    # is SAFETY-ONE-WAY on the client (server can mark Dead, never resurrect).
    # Shape: {case: {status:'Dead', d:'YYYY-MM-DD', why:'...', folio:'digits', cases:[siblings]}}
    # folio + cases are optional: when present, the LOOKUP page can recognize a retired case on a
    # folio the board no longer carries (the Martin condo went "all clear" 4 days after DEALFLOW
    # itself retired it — the dig page had no memory) and link every related docket.
    _deads = {}
    _df = os.path.join(HERE, 'deads.json')
    if os.path.exists(_df):
        try:
            _raw_dd = json.load(open(_df, encoding='utf-8')) or {}
            for _c, _n in _raw_dd.items():
                if not isinstance(_n, dict):
                    continue
                if str(_n.get('status') or '').upper() in ('DEAD', 'CLOSED', 'LOST - SOLD AT AUCTION'):
                    _deads[_c] = {'status': 'Dead', 'd': _n.get('d') or '', 'why': str(_n.get('why') or '')[:160],
                                  'folio': re.sub(r'\D', '', str(_n.get('folio') or '')),
                                  'cases': [str(x) for x in (_n.get('cases') or []) if x][:6]}
        except Exception as e:
            print(f'deads.json unreadable ({e}) — board falls back to device-local dead marks only')
    tpl = tpl.replace('__DEADS__', _esc_json(_deads))
    if _deads:
        print(f'dead ledger: {len(_deads)} case(s) baked in (retired on EVERY device)')

    # FINAL BOUNCE SWEEP — must run before ANY output is written, twin included.
    # The strip at the skiptrace merge is correct but not sufficient: emails are re-merged from more
    # than one source afterwards (the person/portfolio union among them), so a blacklisted address
    # can reappear on the finished card even though the send queue never had it. Measured
    # 2026-08-22: 11 known-dead addresses on 8 cards. That is exactly how a dead address gets
    # emailed BY HAND — wamlong@gmail.com sat on the Amlong card, got typed into a send, and
    # hard-bounced 550. Placed here rather than beside the payload because the DESKTOP TWIN is
    # written first; running it later cleaned the live board and left the local copy dirty.
    if _bounced:
        _late = 0
        for _d in slim:
            _em = _d.get('emails') or []
            if not _em:
                continue
            _keep = [e for e in _em if str(e).lower().strip() not in _bounced]
            if len(_keep) != len(_em):
                _late += len(_em) - len(_keep)
                _d['emails'] = _keep
        if _late:
            print(f"bounce guard (final sweep): {_late} dead address(es) removed from finished cards "
                  f"— they had been re-merged after the queue strip")

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
    # COVERAGE MARKER — a plaintext, greppable census of how enriched this build actually is, so the
    # publish guard can compare a new build against the one already live WITHOUT decrypting either.
    # It carries only counts, never a name, number or address.
    _cov = {
        'leads':  len(slim),
        'phones': sum(1 for d in slim if d.get('phones')),
        'liens':  sum(1 for d in slim if d.get('orconf') and d.get('orconf') != 'none'),
        'wp':     sum(1 for d in slim if d.get('wpKey') == 'ok'),
        'arv':    sum(1 for d in slim if d.get('arv')),
        'rfval':  sum(1 for d in slim if d.get('rfval')),
        'zest':   sum(1 for d in slim if d.get('zest')),
        # §362 STAYS ON THE ACTUAL BUILD. healthcheck's "RULE: §362 stay flags reach the build" is
        # one of only three FAILs that block a publish, and it counted the INPUT LEAD FILES — it
        # never looked at the build it is named for. The 2026-07-21 hole was cache-had-67,
        # published-build-had-ZERO; if that stripping happens inside make_tracker rather than
        # upstream, the rule passes while the board ships unprotected. Publishing the count here
        # lets the rule read the artifact instead of its ingredients.
        'bkstay': sum(1 for d in slim if d.get('saleBkAct') or d.get('sale_bk_active')),
        'built':  datetime.now().strftime('%Y-%m-%dT%H:%M'),
    }
    # (the final bounce sweep runs ABOVE, before the Desktop twin is written — one sweep, not two)
    if codes:
        _payload = json.dumps(_encrypt_multi(json.dumps(slim), codes))
    else:
        nophone = [{k: v for k, v in d.items() if k not in ('phones','phdnc','emails')} for d in slim]
        _payload = _esc_json(nophone)
    # BUILD SIGNATURE — identifies this build by its CONTENT, not by the clock. 'built' is
    # minute-resolution, so two builds inside the same minute (a code added just as the nightly
    # refresh runs) share a stamp, and the gate's stale-copy check would conclude "same build" —
    # exactly the false negative it exists to prevent. The signature moves whenever the payload or
    # the access-code set changes, which is precisely when a cached page has gone stale.
    _cov['sig'] = hashlib.sha256(_payload.encode('utf-8')).hexdigest()[:12]
    _marker = '<!-- DEALFLOW-COVERAGE ' + json.dumps(_cov, separators=(',', ':')) + ' -->\n'
    print('coverage: ' + json.dumps(_cov, separators=(',', ':')))
    # The page must know its own signature so the gate can tell a wrong code apart from a stale
    # cached copy — a newly added access code cannot unlock a page built before it existed.
    tpl = tpl.replace('__BUILT__', _cov['sig'])
    # BOARD-DATA DATE (distinct from the content signature above). The Carlos packet used to stamp
    # only the PRINT date, so paper printed today off a week-old board looked identical to fresh
    # paper — and the daily refresh task has failed silently before (the ExecutionTimeLimit bug).
    # Carlos cannot tell stale paper from fresh, and he pays for the gas. Bake the build clock so
    # every printed artifact can show where its data actually came from.
    # (already substituted above, before the Desktop write — this is a no-op safety net)
    tpl = tpl.replace('__BUILTAT__', _cov['built'])
    # ZIP -> CENTROID table for the Near-Me run, computed from OUR OWN geocoded leads. Zero runtime
    # API, no CORS, no key, works offline in the encrypted file. Only leads that are genuinely
    # ROUTABLE contribute: a street-less address geocodes to a city centroid (a Homestead lead was
    # carrying a coordinate 0.9mi from downtown Miami, 27mi from the property), so including it
    # would poison the very centroid meant to locate it.
    tpl = tpl.replace('__ZIPCENT__', _esc_json(_zip_centroids(slim)))
    # IDENTITY DISCLOSURE — ONE SOURCE, BAKED. disclaimer.identity() is what outreach_email.py
    # already sends on every AUTOMATED send; genEmail (the manual copy-and-send path an operator
    # actually uses) had its own hand-typed variant that had drifted: it said "not a lawyer" and
    # omitted the foreclosure-rescue-company denial entirely. Same channel, same homeowner, two
    # different disclosures depending on which button was clicked. Baking it means the manual path
    # cannot drift from the automated one again — change the sentence in disclaimer.py and every
    # surface moves together.
    try:
        import disclaimer as _D
        tpl = tpl.replace('__IDENT_EN__', _esc_js(_D.identity('en', as_html=False)))
        tpl = tpl.replace('__IDENT_ES__', _esc_js(_D.identity('es', as_html=False)))
    except Exception as _e:
        # NEVER ship the raw placeholder into homeowner-facing copy. Fail the build instead.
        raise SystemExit('identity disclosure bake FAILED (%s) — refusing to build a board whose '
                         'emails would carry a literal __IDENT_EN__ placeholder.' % _e)
    # ALEJANDRO'S COLD-EMAIL COPY -- see _bake_alex_email(). Never raises.
    tpl = _bake_alex_email(tpl, 'docs/index.html')
    _page = _marker + tpl.replace('__DATA__', _payload)
    # Last gate before the board the business runs on goes to disk. A surviving __TOKEN__ is a
    # top-level ReferenceError that kills every declaration after it, and the page still serves a
    # 200 while doing nothing -- which is exactly how design-preview.html stayed dead unnoticed.
    assert_no_placeholders(_page, 'docs/index.html')
    assert_no_bulk_emails(_page, 'docs/index.html')
    open(docs,'w',encoding='utf-8').write(_page)
    # ---- CALL MODE (docs/call/) ----------------------------------------------------------------
    # The phone-first calling page. Wrapped in try/except ON PURPOSE: this is an additive artifact,
    # and a failure building it must cost the phone page, never the board the business runs on.
    # ⚠️ call_mode raises CallModeError (a plain Exception) for exactly this reason. It used to raise
    # SystemExit, which derives from BaseException and sails straight through this handler — so any
    # one of its nine build guards would have killed the whole refresh. If you add a guard there,
    # raise CallModeError, never SystemExit.
    try:
        import call_mode
        _cm_rows, _cm_total = call_mode.make_callmode(
            slim, codes, _encrypt_multi,
            datetime.now().strftime('%Y-%m-%dT%H:%M'), _cov.get('sig', ''),
            optouts=_optouts, deads=_deads, guard=_js_guard, textperson=_tper)
        if _cm_rows:
            print('call mode: %d dialable lead(s) of %d qualifying -> docs/call/  (%s)'
                  % (_cm_rows, _cm_total, 'encrypted' if codes else 'STUB — no site.codes'))
        # DESKTOP CALL SHEET. Both the board and Call Mode require a decision to OPEN them, and
        # on 2026-08-27 that was the whole funnel: 133 leads with verified equity, a phone and a
        # clean gate — zero touched, one text sent in nine days. A plain file that is simply
        # THERE at 7 AM, best call at the top, removes the last step between the data and a dial.
        # Same rows Call Mode just built, so the sheet and the phone can never disagree.
        try:
            import call_sheet
            _cs_rows, _cs_total = call_mode.call_rows(slim, optouts=_optouts, deads=_deads)
            call_sheet.write(_cs_rows, _cs_total)
        except Exception as _cse:
            print('call sheet: SKIPPED (%s) — board is unaffected' % str(_cse)[:100])
    except Exception as _cme:
        print('call mode: SKIPPED (%s) — board is unaffected' % str(_cme)[:120])

    if codes:
        print(f'tracker written: docs/index.html (ENCRYPTED · {len(codes)} access code(s)){_dst}')
    else:
        print('tracker written: docs/index.html (public, phone-free)' + ('' if os.environ.get('DEALFLOW_NO_DESKTOP') == '1' else ' + Desktop'))

def main():
    leads = scrape()
    print(f"scraped {len(leads)} pending auctions")
    # Guard the live site: a broken/blocked scrape must never overwrite a good tracker with an
    # empty one. Bail before regenerating anything (leads_*.json are gitignored, so nothing commits).
    # RATIO against the file already on disk, not a fixed 20 — that floor was set when the board
    # was small and never moved, so Miami-Dade could collapse 370 -> 21 and still regenerate the
    # whole site. A partial scrape is a failure, not a smaller day. (scrape_guard.py)
    import scrape_guard
    _ok, _msg = scrape_guard.check('MIAMI-DADE', len(leads),
                                   os.path.join(HERE, 'leads_final.json'), min_abs=20,
                                   force=os.environ.get('DEALFLOW_FORCE') == '1')
    print(_msg)
    if not _ok:
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
