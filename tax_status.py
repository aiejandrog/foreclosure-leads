# -*- coding: utf-8 -*-
"""Pull Broward property-tax status via Camoufox.

broward.county-taxes.com 403s every other route we have — plain requests, the native curl session,
and even the in-app browser. It is the last open item on the Markey file: are they current on taxes?
The condo already has two Tax Collector notices in its history (2021, 2024), so this is not idle.

Usage:  python tax_status.py [folio ...]
"""
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

FOLIOS = sys.argv[1:] or ['494111BC0250', '504133320180']
URL = 'https://broward.county-taxes.com/public/real_estate/parcels/%s'

# what we actually want off the page, in priority order
WANTED = [
    (r'(?i)\b(paid|unpaid|delinquent|due)\b[^.\n]{0,80}', 'status'),
    (r'\$[\d,]+\.\d{2}', 'amount'),
    (r'(?i)tax\s*year[^\n]{0,40}', 'year'),
]


def main():
    from camoufox.sync_api import Camoufox
    out = {}
    with Camoufox(headless=True, humanize=True, geoip=True) as browser:
        for folio in FOLIOS:
            page = browser.new_page()
            try:
                # Grant Street Group serves an SPA that renders "Loading" first. domcontentloaded
                # returns a 114-char shell and a naive scrape reads it as $0 owed — the exact false
                # negative recorded in the back-taxes note. Wait for networkidle, then poll until the
                # loading text is actually gone.
                r = page.goto(URL % folio, wait_until='domcontentloaded', timeout=60000)
                try:
                    page.wait_for_load_state('networkidle', timeout=45000)
                except Exception:
                    pass
                for _ in range(20):
                    body = page.inner_text('body')
                    if len(body) > 400 and 'Loading' not in body[:200]:
                        break
                    page.wait_for_timeout(1500)
                code = r.status if r else 0
                text = re.sub(r'\s+', ' ', page.inner_text('body'))
                out[folio] = (code, text)
                print('=== folio %s — HTTP %s, %d chars of text' % (folio, code, len(text)))
                if code == 403 or 'Access denied' in text or len(text) < 200:
                    print('    STILL BLOCKED (or empty)')
                    print('    sample: %s' % text[:200])
                else:
                    for pat, label in WANTED:
                        hits = list(dict.fromkeys(re.findall(pat, text)))[:4]
                        if hits:
                            print('    %-7s %s' % (label, ' | '.join(str(h)[:70] for h in hits)))
                    print('    head: %s' % text[:260])
            except Exception as e:
                print('=== folio %s — ERROR %s' % (folio, str(e)[:140]))
            finally:
                page.close()
            print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
