# -*- coding: utf-8 -*-
"""Pull Broward property-tax status via Camoufox.

broward.county-taxes.com 403s every other route we have — plain requests, the native curl session,
and even the in-app browser. It is the last open item on the Markey file: are they current on taxes?
The condo already has two Tax Collector notices in its history (2021, 2024), so this is not idle.

WHY THIS READS AN IFRAME (fixed 2026-08-22)
The first version scraped `page.inner_text('body')` and regexed the result for /paid|unpaid|due/.
That never touched the tax data. The parcel URL 307s to county-taxes.net, whose top document is a
Grant Street shell — 395 characters of nav, a Specialty Plate banner and a footer. The bill renders
in a nested browsing context:

    county-taxes.net/iframe-taxsys/broward.county-taxes.com/govhub/property-tax/<base64 id>

inner_text('body') does not cross into a frame, so the scrape read the shell and the /due/ pattern
matched "**Due** to high demand" out of the license-plate notification. It printed `status Due` and
looked like it had worked. A wrong answer that reports itself as a hit is worse than a failure, so
this version reads the taxsys frame specifically and says NO-FRAME when it cannot find it.

The frame's own tables are not usable — row labels are <th>, and cells carry inlined SVG CSS — so
the fields below are anchored against the frame's flat text instead.

Usage:  python tax_status.py [folio ...]
        python tax_status.py --json 494111BC0250
"""
import json
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

URL = 'https://broward.county-taxes.com/public/real_estate/parcels/%s'

ACCOUNT = re.compile(r'Real Estate Account #([\w-]+)')
OWNER = re.compile(r'Owner:\s*(.+?)\s+Situs:')
SITUS = re.compile(r'Situs:\s*(.+?)\s+(?:Parcel details|GIS|Get Bills)')
TOTAL_DUE = re.compile(r'Total Amount Due:\s*\$([\d,]+\.\d{2})')
# "2025 Annual Bill $3,075.30 Unpaid" — the year, the amount and the word are one unit, which is
# what stops a stray "Due"/"Paid" elsewhere on the page from being read as a status.
BILL = re.compile(r'\b(20\d{2}) Annual Bill \$([\d,]+\.\d{2})\s+(Unpaid|Paid)\b')
CERT = re.compile(r'Certificate #(\d+)\s+Issued\s+(\d{2}/\d{2}/\d{4})')
TDA = re.compile(r'Tax Deed Application #(\d+)')


def money(s):
    return float(s.replace(',', ''))


def scrape(page, folio):
    """Return a dict for one folio. 'ok' is False for every path that did not yield real data."""
    r = page.goto(URL % folio, wait_until='domcontentloaded', timeout=60000)
    code = r.status if r else 0
    try:
        page.wait_for_load_state('networkidle', timeout=45000)
    except Exception:
        pass

    # The taxsys frame attaches late. Poll for it, then poll again for its content — the frame
    # exists well before the bills land in it.
    frame = None
    for _ in range(25):
        cand = [f for f in page.frames if 'iframe-taxsys' in f.url]
        if cand:
            try:
                if len(cand[0].locator('body').inner_text(timeout=3000)) > 600:
                    frame = cand[0]
                    break
            except Exception:
                pass
        page.wait_for_timeout(1200)

    if frame is None:
        return {'folio': folio, 'ok': False, 'http': code, 'why': 'NO-FRAME (blocked, or the '
                'taxsys iframe never rendered — do NOT read the outer shell, it has no tax data)'}

    text = re.sub(r'\s+', ' ', frame.locator('body').inner_text())

    bills = [{'year': int(y), 'amount': money(a), 'status': s} for y, a, s in BILL.findall(text)]
    unpaid = [b for b in bills if b['status'] == 'Unpaid']
    total = TOTAL_DUE.search(text)
    owner = OWNER.search(text)
    situs = SITUS.search(text)
    acct = ACCOUNT.search(text)

    if not bills and not total:
        return {'folio': folio, 'ok': False, 'http': code,
                'why': 'frame loaded but no bill rows matched — layout may have changed',
                'sample': text[:200]}

    return {
        'folio': folio, 'ok': True, 'http': code,
        'account': acct.group(1) if acct else '',
        'owner': owner.group(1).strip() if owner else '',
        'situs': situs.group(1).strip() if situs else '',
        'total_due': money(total.group(1)) if total else 0.0,
        'delinquent': bool(unpaid),
        'unpaid_years': [b['year'] for b in unpaid],
        'bills': bills,
        'certificates': [{'number': n, 'issued': d} for n, d in CERT.findall(text)],
        'tax_deed_applications': sorted(set(TDA.findall(text))),
    }


def main():
    args = [a for a in sys.argv[1:] if a != '--json']
    as_json = '--json' in sys.argv
    folios = args or ['494111BC0250', '504133320180']

    from camoufox.sync_api import Camoufox
    results = []
    with Camoufox(headless=True, humanize=True, geoip=True) as browser:
        for folio in folios:
            page = browser.new_page()
            try:
                results.append(scrape(page, folio))
            except Exception as e:
                results.append({'folio': folio, 'ok': False,
                                'why': '%s: %s' % (type(e).__name__, str(e)[:140])})
            finally:
                page.close()

    if as_json:
        print(json.dumps(results, indent=1))
        return 0

    for d in results:
        print('=== folio %s' % d['folio'])
        if not d.get('ok'):
            print('    FAILED  %s' % d.get('why'))
            print()
            continue
        print('    account      %s' % d['account'])
        print('    owner        %s' % d['owner'])
        print('    situs        %s' % d['situs'])
        print('    TOTAL DUE    $%s%s' % (format(d['total_due'], ',.2f'),
              '   *** DELINQUENT ***' if d['delinquent'] else ''))
        for b in d['bills'][:6]:
            print('      %s  $%-10s %s' % (b['year'], format(b['amount'], ',.2f'), b['status']))
        if d['certificates']:
            print('    certificates %s' % ', '.join('#%s (%s)' % (c['number'], c['issued'])
                                                    for c in d['certificates'][:4]))
        if d['tax_deed_applications']:
            print('    tax-deed app %s   <- prior TDAs on this parcel'
                  % ', '.join('#' + t for t in d['tax_deed_applications'][:6]))
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
