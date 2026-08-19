# -*- coding: utf-8 -*-
"""Pull case -> Parcel ID off RealForeclose auction-date pages (the court's own posting).

WHY: 'no cadastral match' leads have a court address whose UNIT label does not exist on the county
roll (1400 Saint Charles Pl '#107' vs county 'L1..L8'). Guessing units is how you end up hunting a
non-owner; the auction item itself publishes the county Parcel ID, so read THAT.

Usage: python rf_parcel.py <county> <MM/DD/YYYY> [more dates...]
Prints JSON {case: {parcel, address, judgment, status}} per date and merges into rf_parcels.json.
"""
import json, os, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

HERE = r'C:\Users\olqbb\projects\foreclosure-leads'
OUT = os.path.join(HERE, 'rf_parcels.json')
COUNTY = {'BROWARD': 'broward', 'PALM BEACH': 'palmbeach', 'MIAMI-DADE': 'miamidade'}

def scrape(pg, county_host, date):
    url = 'https://%s.realforeclose.com/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=%s' % (county_host, date)
    pg.goto(url, timeout=60000, wait_until='domcontentloaded')
    pg.wait_for_timeout(3500)
    items = pg.evaluate("""() => {
      const out = [];
      document.querySelectorAll('div.AUCTION_ITEM').forEach(el => {
        const t = (el.innerText || '');
        const g = (re) => { const m = t.match(re); return m ? m[1].trim() : null; };
        const flat = t.replace(/\\s+/g, ' ');
        out.push({
          case:   g(/Case #:\\s*([^\\n]+)/),
          parcel: g(/Parcel ID:\\s*([^\\n]+)/),
          addr:   g(/Property Address:\\s*([^\\n]+)/),
          judg:   g(/Final Judgment Amount:\\s*\\$?([\\d,\\.]+)/),
          status: /Auction Sold/.test(flat) ? 'SOLD' : /reschedul/i.test(flat) ? 'RESCHEDULED'
                  : /cancel/i.test(flat) ? 'CANCELED' : 'SCHEDULED'
        });
      });
      return out;
    }""")
    return {i['case']: i for i in items if i.get('case')}

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    county = sys.argv[1].upper()
    dates = sys.argv[2:]
    host = COUNTY.get(county)
    if not host:
        sys.exit('unknown county %r' % county)
    try:
        book = json.load(open(OUT, encoding='utf-8'))
    except Exception:
        book = {}
    with sync_playwright() as p:
        b = p.chromium.launch(args=['--disable-blink-features=AutomationControlled'])
        pg = b.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                   '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
        for date in dates:
            got = scrape(pg, host, date)
            print('%s %s: %d item(s)' % (county, date, len(got)))
            for c, it in got.items():
                book[c] = {'parcel': it.get('parcel'), 'addr': it.get('addr'),
                           'judg': it.get('judg'), 'status': it.get('status'),
                           'county': county, 'date': date}
                if it.get('parcel'):
                    print('   %-22s parcel=%-18s %s' % (c[:22], it['parcel'], (it.get('addr') or '')[:40]))
        b.close()
    json.dump(book, open(OUT, 'w', encoding='utf-8'), indent=1)
    print('saved -> rf_parcels.json (%d cases total)' % len(book))

if __name__ == '__main__':
    main()
