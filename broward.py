"""Broward County lead pipeline — second county for DEALFLOW.

Reuses the Miami-Dade RealForeclose scraper (same RealAuction platform, just the broward subdomain) and
enriches with the statewide FL cadastral (fl_cadastral) instead of a Broward-specific Property Appraiser.
Outputs broward_leads.json in the tracker's slim shape, tagged county='BROWARD', so make_tracker merges it.

    python broward.py            # scrape all Broward auction dates -> enrich -> broward_leads.json
    python broward.py --dates 3  # first 3 dates only (quick test)
"""
import argparse, json, os, re, time, urllib.parse
from datetime import date, datetime
from playwright.sync_api import sync_playwright
import foreclosure_leads as F
import fl_cadastral

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://broward.realforeclose.com/index.cfm"
OUT = os.path.join(HERE, 'broward_leads.json')
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|USA|COUNTY|CITY OF)\b', re.I)


def _clean_owner(name):
    s = re.sub(r'\s*&\s*[WH]\b.*$', '', name or '', flags=re.I)
    s = re.sub(r'\b(ET\s?UX|ET\s?VIR|H/W|TRS|JR|SR|II|III|IV|ETAL|ET AL|LE|REM)\b', '', s, flags=re.I)
    if ',' in s:                                  # cadastral gives "LAST FIRST" or "LAST, FIRST"
        a, _, b = s.partition(','); s = (b + ' ' + a).strip()
    return re.sub(r'\s{2,}', ' ', s).strip()


def scrape_broward(max_dates=0):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_context(user_agent=F.UA, viewport={"width": 1400, "height": 1000}).new_page()
        dates = F.discover_dates(page, base=BASE)
        if max_dates: dates = dates[:max_dates]
        leads = []
        for d, st in dates:
            leads += F.scrape_date(page, d, st, base=BASE)
        b.close()
    # dedupe
    seen, out = set(), []
    for r in leads:
        k = (r.get('Case #') or r.get('Address', '')) + r.get('AuctionDate', '')
        if k and k not in seen:
            seen.add(k); out.append(r)
    return out


def to_slim(items):
    today = date.today()
    slim = []
    for r in items:
        folio = re.sub(r'\D', '', r.get('Folio', '') or '')
        judg = F.money(r.get('Final Judgment Amount', ''))
        addr = F._clean_addr(r.get('Address', ''))
        # enrich: owner + value from the statewide cadastral (by Broward folio)
        val = 0; owner = ''; hs = False; mail = ''; bprice = 0; bought = 0; condo = False; oname = ''
        info = None
        if folio:
            try: info = fl_cadastral.enrich(parcel_id=folio)
            except Exception: info = None
        if info:
            val = info['market_value']; owner = info['owner']; hs = info['homestead']
            mail = info['mail_addr']; bprice = info['last_sale_price']; bought = info['last_sale_year']
            condo = bool(re.search(r'CONDO', info.get('legal', ''), re.I)) or str(info.get('use_code', '')) in ('0400', '400', '04')
            oname = _clean_owner(owner)
        try:
            dt = datetime.strptime(r.get('AuctionDate', ''), '%m/%d/%Y').date()
            days = (dt - today).days
        except Exception:
            days = -1
        eqv = (val - judg) if val else 0
        eqp = round(eqv / val * 100) if val else 0
        is_co = bool(COMPANY_RE.search(owner))
        st = r.get('sale_type', 'FC')
        # simple qualify (the template recompute() does the real deal math from value+judg)
        score = 0
        if val: score = max(0, min(100, round(eqp) + (10 if hs else 0) + (10 if 0 <= days <= 30 else 0)))
        tier = 'A' if (val and eqp >= 40 and 0 <= days <= 45) else ('B' if val and eqp >= 15 else 'C')
        z = 'https://www.zillow.com/homes/' + urllib.parse.quote((addr or folio) + ' FL') + '_rb/'
        pa = ('https://web.bcpa.net/BcpaClient/#/Record-Search') if folio else ''
        tax = ('https://broward.county-taxes.com/public/real_estate/parcels/' + folio) if folio else ''
        auc = (BASE + '?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=' + r.get('AuctionDate', '') +
               ('#AITEM_' + r['AID'] if r.get('AID') else ''))
        pt = [t for t in re.sub(r'\s*&.*$', '', owner).split() if len(t.strip('.')) > 1]
        zip5 = (re.search(r'(\d{5})', addr) or [None, ''])[1] if addr else ''
        people = ('https://www.truepeoplesearch.com/results?name=' + urllib.parse.quote(pt[0] + ' ' + pt[-1]) +
                  ('&citystatezip=' + zip5 if zip5 else '')) if (len(pt) >= 2 and not is_co) else ''
        slim.append({
            'county': 'BROWARD', 'tier': tier, 'score': score,
            'auction': r.get('AuctionDate', ''), 'days': days, 'case': r.get('Case #', ''),
            'owners': owner or '(owner via title search)', 'oname': oname,
            'addr': addr, 'mail': mail, 'value': val, 'judg': judg,
            'eq': eqp, 'eqfake': False, 'hs': hs, 'condo': condo,
            'st': st, 'obid': 0, 'folio': folio,
            'zillow': z, 'pa': pa, 'tax': tax, 'auc': auc,
            'people': people, 'peopleaddr': '', 'ctype': 'Bank/Mortgage',
            'plaintiff': r.get('Plaintiff', ''), 'defs': '', 'named': [],
            'docket': 'https://www.browardclerk.org/Web2/CaseSearchECA/', 'cstatus': '',
            'mr': False, 'ip': False, 'ju': (judg <= 0), 'bought': bought, 'bprice': bprice,
            'filed': 0, 'etax': 0, 'warn': ('' if val else 'no cadastral match - verify parcel + value'),
            'recqs': '', 'ocsqs': '', 'cert': '',
        })
    return slim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dates', type=int, default=0, help='limit to first N auction dates (quick test)')
    a = ap.parse_args()
    print("scraping Broward auctions...")
    items = scrape_broward(a.dates)
    print(f"scraped {len(items)} Broward auctions; enriching via statewide cadastral...")
    slim = to_slim(items)
    got = sum(1 for s in slim if s['value'])
    json.dump(slim, open(OUT, 'w', encoding='utf-8'), indent=1)
    a_ = sum(1 for s in slim if s['tier'] == 'A')
    print(f"DONE: {len(slim)} Broward leads ({got} enriched with value, {a_} Tier A) -> broward_leads.json")


if __name__ == '__main__':
    main()
