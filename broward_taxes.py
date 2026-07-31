#!/usr/bin/env python3
"""broward_taxes.py — verified delinquent property taxes for Broward leads.

WHY THIS EXISTS
Delinquent property taxes are a FIRST-PRIORITY lien that survives a mortgage foreclosure
(Fla. Stat. 197.122) — a buyer who takes title inherits them. They are invisible to the mortgage
chain, so a lead can read "90% equity, records-verified $0 survives" and still owe six figures in
back taxes. Worse: once a tax certificate is sold, a SECOND foreclosure clock starts — the
certificate holder can force a tax-deed sale on a completely separate track from the bank's case.

Found live on the Spong lead (CACE-22-009549, 6466 NW 80 TER): $76,143 unpaid (2024 + 2025) with
tax certificate #252 already issued. The board's equity number was right only because someone had
manually entered it. This automates that check for EVERY Broward lead so the next one is not missed.

WHY PLAYWRIGHT AND NOT requests
Broward's tax portal (broward.county-taxes.com, Grant Street TaxSys behind county-taxes.net) is
Cloudflare bot-walled — plain requests get 403 (records_liens.py's comments already note this).
It is NOT a human captcha, just a browser-fingerprint challenge, so a REAL headless browser passes
it for free (no 2Captcha). The parcel-bills deep link resolves inside the SPA and renders the
amount-due panel in an iframe; we goto it, wait, and read the iframe text. Verified working: the
deep link for folio 484102-00-0058 renders "Total Amount Due: $76,143.48", two Unpaid years, and
"Certificate #252 Issued 05/28/2025 Face $36,276.70".

WHAT IT WRITES
broward_taxes.json, keyed by folio: {due, years:[...], unpaid, cert:{num,face,date,rate}|null,
checked}. foreclosure_leads.py bakes `taxDue`/`taxYears`/`taxCert` onto the Broward lead; the deal
math folds a verified taxDue into the equity automatically UNLESS the operator typed their own
Back-property-taxes override (manual research always wins, same precedence as the lien chain).

Run:  python broward_taxes.py               # check uncached Broward folios (capped)
      python broward_taxes.py --limit 60     # raise the per-run cap
      python broward_taxes.py --folio 484102-00-0058   # one parcel, verbose
      python broward_taxes.py --refresh      # ignore the cache
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'broward_taxes.json')
CACHE = os.path.join(HERE, '_broward_taxes_cache.json')
DEFAULT_LIMIT = 40


def _load(p, d):
    if not os.path.exists(p):
        return d
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


def _dash(folio):
    """Board folios are 12 undashed digits (484102000058); the portal URL wants 484102-00-0058."""
    f = re.sub(r'\D', '', str(folio or ''))
    return (f[:6] + '-' + f[6:8] + '-' + f[8:12]) if len(f) == 12 else str(folio or '')


def _broward_folios():
    """folio -> case for every Broward board lead that carries a 12-digit folio."""
    out = {}
    for r in _load(os.path.join(HERE, 'broward_leads.json'), []) or []:
        f = re.sub(r'\D', '', str(r.get('folio') or r.get('Folio') or ''))
        c = str(r.get('case') or r.get('Case #') or '').strip()
        if len(f) == 12 and c:
            out[f] = c
    return out


def _parse(text):
    """Amount-due + bill-history text -> the delinquency picture. Only UNPAID years count toward due."""
    due = 0.0
    m = re.search(r'Total\s+Amount\s+Due:?\s*\$?([\d,]+\.\d\d)', text, re.I)
    if m:
        due = float(m.group(1).replace(',', ''))
    # unpaid years: "2025 Annual bill $38,046.69 Unpaid"
    years = []
    for ym in re.finditer(r'(20\d\d)\s+Annual bill\s+\$([\d,]+\.\d\d)\s+Unpaid', text, re.I):
        years.append({'year': ym.group(1), 'amt': float(ym.group(2).replace(',', ''))})
    # a sold tax certificate = a second foreclosure track. Capture it.
    cert = None
    cm = re.search(r'Certificate\s*#?\s*(\d+)\s+Issued\s+([\d/]+)[^$]*\$([\d,]+\.\d\d)[^%]*?([\d.]+)%',
                   text, re.I)
    if cm:
        cert = {'num': cm.group(1), 'date': cm.group(2),
                'face': float(cm.group(3).replace(',', '')), 'rate': cm.group(4)}
    # if the total-due line was missing but unpaid years exist, sum them (belt and suspenders)
    if not due and years:
        due = round(sum(y['amt'] for y in years), 2)
    return {'due': int(round(due)), 'years': years, 'unpaid': len(years), 'cert': cert}


def check(page, folio):
    """Drive the parcel-bills deep link and read the rendered amount. Returns the parsed dict or {}."""
    url = 'https://broward.county-taxes.com/public/real_estate/parcels/%s/bills' % _dash(folio)
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
    except Exception as e:
        print('  %s: goto failed (%s)' % (folio, str(e)[:60]))
        return {}
    # LOAD-BEARING WAIT. The TaxSys SPA keeps a long-poll socket open, so networkidle never truly
    # fires — but WAITING on it (and letting it time out) is exactly the ~20s the Cloudflare
    # challenge + SPA boot + iframe data-fetch need. Without this the poll below reads the shell and
    # returns $0 for every parcel (a false "clear" that would hide a real tax lien). Verified: with
    # this wait, Spong's $76,143 renders; without it, $0.
    try:
        page.wait_for_load_state('networkidle', timeout=22000)
    except Exception:
        pass
    # the amount renders in an iframe after the Cloudflare + SPA settle; poll for it
    text = ''
    for _ in range(30):
        page.wait_for_timeout(1000)
        try:
            text = page.evaluate("""() => {
              let s = document.body.innerText || '';
              document.querySelectorAll('iframe').forEach(f => {
                try { s += ' ' + (f.contentDocument.body.innerText||''); } catch(e){}
              });
              return s;
            }""")
        except Exception:
            text = ''
        if re.search(r'Amount\s+Due', text, re.I) or re.search(r'No\s+bills', text, re.I):
            break
    if re.search(r'No\s+bills|not\s+found', text, re.I) and 'Amount Due' not in text:
        return {'due': 0, 'years': [], 'unpaid': 0, 'cert': None}   # checked, nothing owed
    if 'Amount Due' not in text:
        return {}                                                    # never rendered — treat as unchecked
    return _parse(text)


def main():
    args = sys.argv[1:]
    refresh = '--refresh' in args
    limit = DEFAULT_LIMIT
    if '--limit' in args:
        try: limit = int(args[args.index('--limit') + 1])
        except Exception: pass
    one = args[args.index('--folio') + 1] if '--folio' in args else None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('playwright not installed — pip install playwright && playwright install chromium')
        return

    with sync_playwright() as p:
        # --disable-blink-features=AutomationControlled is LOAD-BEARING. Without it the Cloudflare/
        # TaxSys SPA passes the challenge but never fires its data fetch — the panel sits on
        # "Loading" forever and every parcel reads $0. With it, the amount renders in ~1s. A real
        # viewport matters too (a 0x0 headless viewport reads as a bot).
        browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(
            user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/126 Safari/537.36'),
            viewport={'width': 1280, 'height': 900})
        page = ctx.new_page()

        if one:
            print(json.dumps(check(page, one), indent=1))
            browser.close(); return

        folios = _broward_folios()
        cache = {} if refresh else _load(CACHE, {})
        todo = [f for f in folios if f not in cache][:limit]
        print('%d Broward folio(s) · %d uncached · checking %d this run (cap %d)'
              % (len(folios), len([f for f in folios if f not in cache]), len(todo), limit))

        for i, f in enumerate(todo, 1):
            rec = check(page, f)
            if rec:                              # {} = never rendered; leave uncached to retry next run
                rec['checked'] = __import__('time').strftime('%Y-%m-%d')
                cache[f] = rec
                tag = ('$%s owed' % f"{rec['due']:,}") if rec['due'] else 'clear'
                cert = ' + CERT #%s' % rec['cert']['num'] if rec.get('cert') else ''
                print('  [%d/%d] %s -> %s%s' % (i, len(todo), f, tag, cert))
            if i % 5 == 0:
                json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=0)
            page.wait_for_timeout(400)

        json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=0)
        browser.close()

    # emit only folios that actually owe, keyed for the board join
    owed = {f: v for f, v in cache.items() if v.get('due')}
    json.dump(owed, open(OUT, 'w', encoding='utf-8'), indent=1)
    certs = sum(1 for v in owed.values() if v.get('cert'))
    total = sum(v['due'] for v in owed.values())
    print('\nbroward_taxes.json — %d parcel(s) owe back taxes (%d with a sold certificate) · $%s total'
          % (len(owed), certs, f'{total:,}'))
    if owed:
        print('Rebuild to fold verified taxes into equity + surface the chip.')


if __name__ == '__main__':
    main()
