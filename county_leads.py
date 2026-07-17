"""Generic Florida county lead pipeline — one module, any county.

Every FL county's foreclosure auctions live on the same RealAuction platform (countyname.realforeclose.com),
and the statewide FDOR cadastral (fl_cadastral) enriches owner+value for all of them. So a new county is
just a config row. Outputs <county>_leads.json in the tracker's slim shape, county-tagged, for make_tracker.

    python county_leads.py --county broward
    python county_leads.py --county "palm beach" --dates 3
"""
import argparse, json, os, re, urllib.parse
from datetime import date, datetime
from playwright.sync_api import sync_playwright
import foreclosure_leads as F
import fl_cadastral

HERE = os.path.dirname(os.path.abspath(__file__))
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|USA|COUNTY|CITY OF|CHURCH|MINISTR)\b', re.I)

# One row per county. subdomain -> RealForeclose; co_no -> FDOR cadastral; links -> county PA/tax/clerk.
# Per-county DIRECT deep-links, verified against real parcels (2026-07-16). PA links open the exact
# parcel by folio; tax/records/cases are the correct county portals (some are search-only — see notes).
COUNTIES = {
    'BROWARD': {
        'sub': 'broward', 'co_no': 16,
        # BCPA's Angular SPA can't deep-link; the legacy RecInfo.asp endpoint opens the parcel directly (12-digit folio, no dashes).
        'pa': lambda f: 'https://bcpa.net/RecInfo.asp?URL_Folio=' + f,
        # The county-taxes.com/.../parcels/{folio} deep-link is Cloudflare-walled on its redirect and never
        # resolves for a normal click (verified) — link the tax portal LANDING, which loads + lets you search.
        'tax': lambda f: 'https://county-taxes.net/broward',
        'records': 'https://officialrecords.broward.org/AcclaimWeb/search/SearchTypeName',   # lands on the NAME search form (via disclaimer), not the generic portal
        'cases': 'https://www.browardclerk.org/Web2/CaseSearchECA/',    # court case search
    },
    'PALM BEACH': {
        'sub': 'palmbeach', 'co_no': 60,
        'pa': lambda f: 'https://pbcpao.gov/Property/Details?parcelId=' + f,   # direct parcel (17-digit PCN, no dashes)
        # manatron.com is retired; the PublicAccessNow portal's true deep-link needs a non-derivable Aumentum
        # account id, so link the portal (user searches by PCN/address there).
        'tax': lambda f: 'https://pbctax.publicaccessnow.com/',
        'records': 'https://erec.mypalmbeachclerk.com/search/index?theme=.blue&section=searchCriteriaName&quickSearchSelection=',   # Landmark NAME search
        'cases': 'https://appsgp.mypalmbeachclerk.com/eCaseView/',       # court case search
    },
}


def _rec_name(owner):
    """'Last, First' for the Official-Records / court-case NAME search (AcclaimWeb + Landmark want last-first)
    — the OPPOSITE of the TruePeopleSearch order. Copied to the clipboard when the Records/Cases button is clicked."""
    s = re.sub(r'\s*&.*$', '', owner or '')
    s = re.sub(r'\b(H/[EW]|ET\s?UX|ET\s?AL|TRS?|JR|SR|II+|III|IV|LE|REM)\b', '', s, flags=re.I).strip()
    if ',' in s:
        last, _, first = s.partition(',')
    else:
        toks = s.split()
        if len(toks) < 2:
            return s.strip()
        last, first = toks[0], ' '.join(toks[1:])
    return (last.strip() + ', ' + first.strip()).strip(', ')


def _people_name(owner):
    """'First Last' for a TruePeopleSearch NAME query from an FDOR owner name (stored as LAST FIRST[,] MIDDLE)."""
    s = re.sub(r'\s*&.*$', '', owner or '')
    s = re.sub(r'\b(H/[EW]|ET\s?UX|ET\s?AL|TRS?|JR|SR|II+|III|IV|LE|REM|EST|ESTATE)\b', '', s, flags=re.I)
    if ',' in s:
        last, _, first = s.partition(',')
    else:
        toks = [t for t in s.split() if len(t.strip('.')) > 1]
        if len(toks) < 2:
            return ''
        last, first = toks[0], toks[1]
    last = last.strip('. '); first = (first.split() or [''])[0].strip('. ')
    return (first + ' ' + last).strip() if (first and last) else ''


def _clean_owner(name):
    s = re.sub(r'\s*&\s*[WH]\b.*$', '', name or '', flags=re.I)
    s = re.sub(r'\b(ET\s?UX|ET\s?VIR|H/W|TRS|JR|SR|II|III|IV|ETAL|ET AL|LE|REM)\b', '', s, flags=re.I)
    if ',' in s:
        a, _, b = s.partition(','); s = (b + ' ' + a).strip()
    return re.sub(r'\s{2,}', ' ', s).strip()


def scrape_county(cfg, max_dates=0):
    base = f"https://{cfg['sub']}.realforeclose.com/index.cfm"
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_context(user_agent=F.UA, viewport={"width": 1400, "height": 1000}).new_page()
        dates = F.discover_dates(page, base=base)
        if max_dates: dates = dates[:max_dates]
        leads = []
        for d, st in dates:
            leads += F.scrape_date(page, d, st, base=base)
        b.close()
    seen, out = set(), []
    for r in leads:
        k = (r.get('Case #') or r.get('Address', '')) + r.get('AuctionDate', '')
        if k and k not in seen:
            seen.add(k); out.append(r)
    return out, base


def to_slim(county, cfg, base, items):
    today = date.today()
    slim = []
    for r in items:
        folio = re.sub(r'\D', '', r.get('Folio', '') or '')
        judg = F.money(r.get('Final Judgment Amount', ''))
        # TRUE type from the auction plaintiff (bank-charter guard wins first); the case-number prefix is
        # only the fallback when the auction gives no plaintiff. The recorded-chain plaintiff later overrides
        # this in make_tracker() for any lead we actually trace (broward_liens).
        ftype = F._fc_type_plaintiff(r.get('Plaintiff', '')) or F._fc_type(r.get('Case #', ''))
        addr = F._clean_addr(r.get('Address', ''))
        val = 0; owner = ''; hs = False; mail = ''; bprice = 0; bought = 0; condo = False; oname = ''
        if folio:
            try: info = fl_cadastral.enrich(parcel_id=folio)
            except Exception: info = None
            if info:
                val, owner, hs = info['market_value'], info['owner'], info['homestead']
                mail, bprice, bought = info['mail_addr'], info['last_sale_price'], info['last_sale_year']
                condo = bool(re.search(r'CONDO', info.get('legal', ''), re.I)) or str(info.get('use_code', '')) in ('0400', '400', '04')
                oname = _clean_owner(owner)
        try:
            days = (datetime.strptime(r.get('AuctionDate', ''), '%m/%d/%Y').date() - today).days
        except Exception:
            days = -1
        eqp = round((val - judg) / val * 100) if val else 0
        is_co = bool(COMPANY_RE.search(owner))
        st = r.get('sale_type', 'FC')
        score = max(0, min(100, round(eqp) + (10 if hs else 0) + (10 if 0 <= days <= 30 else 0))) if val else 0
        tier = 'A' if (val and eqp >= 40 and 0 <= days <= 45) else ('B' if val and eqp >= 15 else 'C')
        z = 'https://www.zillow.com/homes/' + urllib.parse.quote((addr or folio) + ' FL') + '_rb/'
        auc = base + '?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=' + r.get('AuctionDate', '') + ('#AITEM_' + r['AID'] if r.get('AID') else '')
        # People NAME search — TruePeopleSearch wants "First Last". FDOR owner names are "LAST FIRST[,] MIDDLE",
        # so build the query with _people_name() (handles both comma + space forms). zip is at the END of the
        # address (not the street number). Skip companies/trusts and address-named entities ("...LAND TR").
        zip5 = (re.search(r'(\d{5})(?:-\d{4})?\s*$', addr) or [None, ''])[1] if addr else ''
        _nm = _people_name(owner)
        _ent = bool(re.search(r'\b(TR|TRS|EST|ESTATE|FUND|PROPERT|REALTY|HOMES|GROUP|INVEST|ENTERPRISE|LAND|ASSN|ASSOC)\b', owner, re.I))
        if _nm and not is_co and not _ent and not re.match(r'^\s*\d', owner or ''):
            people = 'https://www.truepeoplesearch.com/results?name=' + urllib.parse.quote(_nm) + ('&citystatezip=' + zip5 if zip5 else '')
        else:
            people = ''
        # People-by-ADDRESS (pinpoints the owner among same-name strangers) — reuse the Miami-Dade builder.
        peopleaddr = F.people_addr_url(mail, addr, is_co or _ent)
        # CyberBackgroundChecks NAME search (free detail page: phones w/ last-reported date, emails,
        # relatives+associates — verified 2026-07-17 to out-return BatchData on both a Broward and a
        # Sunrise lead). Same gate as the TPS name search: skip companies/trusts/address-named entities.
        cyberbg = F.cyberbg_url(_nm, addr) if (_nm and not is_co and not _ent) else ''
        cyberbgaddr = F.cyberbg_addr_url(mail, addr, is_co or _ent)
        slim.append({
            'county': county, 'tier': tier, 'score': score, 'auction': r.get('AuctionDate', ''), 'days': days,
            'case': r.get('Case #', ''), 'owners': owner or '(owner via title search)', 'oname': oname, 'rname': _rec_name(owner),
            'addr': addr, 'mail': mail, 'value': val, 'judg': judg, 'eq': eqp, 'eqfake': False, 'hs': hs, 'condo': condo,
            'st': st, 'obid': 0, 'folio': folio, 'zillow': z, 'pa': cfg['pa'](folio) if folio else '',
            'tax': cfg['tax'](folio) if folio else '', 'auc': auc, 'people': people, 'peopleaddr': peopleaddr, 'cyberbg': cyberbg, 'cyberbgaddr': cyberbgaddr,
            'ctype': ('HOA' if ftype == 'HOA' else 'Bank/Mortgage'), 'ftype': ftype, 'plaintiff': r.get('Plaintiff', ''), 'defs': '', 'named': [],
            # county leads have no per-case docket token (no clerk enrichment) -> no Docket button; the
            # Records/Cases buttons point to THIS county's official-records + court-case search portals.
            'docket': '', 'records': cfg['records'], 'cases': cfg['cases'],
            'cstatus': '', 'mr': (ftype == 'HOA'), 'ip': False, 'ju': (judg <= 0),
            'bought': bought, 'bprice': bprice, 'filed': 0, 'etax': 0,
            'warn': ('' if val else 'no cadastral match - verify parcel + value'), 'recqs': '', 'ocsqs': '', 'cert': '',
        })
    return slim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--county', required=True)
    ap.add_argument('--dates', type=int, default=0)
    a = ap.parse_args()
    key = a.county.upper().strip()
    if key not in COUNTIES:
        raise SystemExit(f"unknown county '{a.county}'. Have: {', '.join(COUNTIES)}")
    cfg = COUNTIES[key]
    print(f"scraping {key} auctions ({cfg['sub']}.realforeclose.com)...")
    items, base = scrape_county(cfg, a.dates)
    print(f"scraped {len(items)}; enriching via statewide cadastral (CO_NO={cfg['co_no']})...")
    slim = to_slim(key, cfg, base, items)
    got = sum(1 for s in slim if s['value']); aN = sum(1 for s in slim if s['tier'] == 'A')
    out = os.path.join(HERE, key.lower().replace(' ', '') + '_leads.json')
    # Safety guard (matches foreclosure_leads.py): a blocked/thin scrape must NEVER overwrite a good
    # file with an empty one. Bail and leave the last good snapshot in place so the site stays populated.
    MIN = 10
    if len(slim) < MIN:
        prev = 0
        if os.path.exists(out):
            try: prev = len(json.load(open(out, encoding='utf-8')))
            except Exception: prev = 0
        print(f"ABORT: only {len(slim)} {key} leads scraped (< {MIN}). Keeping the existing {prev}-lead file.")
        raise SystemExit(1)
    json.dump(slim, open(out, 'w', encoding='utf-8'), indent=1)
    print(f"DONE: {len(slim)} {key} leads ({got} enriched, {aN} Tier A) -> {os.path.basename(out)}")


if __name__ == '__main__':
    main()
