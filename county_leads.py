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
COUNTIES = {
    'BROWARD': {
        'sub': 'broward', 'co_no': 16,
        'pa': lambda f: 'https://web.bcpa.net/BcpaClient/#/Record-Search',
        'tax': lambda f: 'https://broward.county-taxes.com/public/real_estate/parcels/' + f,
        'clerk': 'https://www.browardclerk.org/Web2/CaseSearchECA/',
    },
    'PALM BEACH': {
        'sub': 'palmbeach', 'co_no': 60,
        'pa': lambda f: 'https://pbcpao.gov/Property/Details?parcelId=' + f,
        'tax': lambda f: 'https://pbctax.manatron.com/Accounts/AccountDetails.aspx?p=' + f,
        'clerk': 'https://appsgp.mypalmbeachclerk.com/eCaseView/',
    },
}


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
        pt = [t for t in re.sub(r'\s*&.*$', '', owner).split() if len(t.strip('.')) > 1]
        zip5 = (re.search(r'(\d{5})', addr) or [None, ''])[1] if addr else ''
        people = ('https://www.truepeoplesearch.com/results?name=' + urllib.parse.quote(pt[0] + ' ' + pt[-1]) + ('&citystatezip=' + zip5 if zip5 else '')) if (len(pt) >= 2 and not is_co) else ''
        slim.append({
            'county': county, 'tier': tier, 'score': score, 'auction': r.get('AuctionDate', ''), 'days': days,
            'case': r.get('Case #', ''), 'owners': owner or '(owner via title search)', 'oname': oname,
            'addr': addr, 'mail': mail, 'value': val, 'judg': judg, 'eq': eqp, 'eqfake': False, 'hs': hs, 'condo': condo,
            'st': st, 'obid': 0, 'folio': folio, 'zillow': z, 'pa': cfg['pa'](folio) if folio else '',
            'tax': cfg['tax'](folio) if folio else '', 'auc': auc, 'people': people, 'peopleaddr': '',
            'ctype': 'Bank/Mortgage', 'plaintiff': r.get('Plaintiff', ''), 'defs': '', 'named': [],
            'docket': cfg['clerk'], 'cstatus': '', 'mr': False, 'ip': False, 'ju': (judg <= 0),
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
    json.dump(slim, open(out, 'w', encoding='utf-8'), indent=1)
    print(f"DONE: {len(slim)} {key} leads ({got} enriched, {aN} Tier A) -> {os.path.basename(out)}")


if __name__ == '__main__':
    main()
