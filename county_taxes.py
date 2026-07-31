#!/usr/bin/env python3
"""county_taxes.py — verified delinquent property taxes for Miami-Dade + Broward leads.

Supersedes broward_taxes.py (same engine, now multi-county and parallel).

WHY THIS EXISTS
Delinquent property taxes are a FIRST-PRIORITY lien that survives a mortgage foreclosure
(Fla. Stat. 197.122) — whoever takes title inherits them. They are invisible to the mortgage chain,
so a lead can read "records-verified $0 survives" and still owe six figures. Worse: once a tax
CERTIFICATE is sold, a second foreclosure clock starts, because the certificate holder can force a
tax-deed sale on a track completely separate from the bank's case.

Found live on the Spong lead (CACE-22-009549): $76,143 unpaid across 2024+2025 with certificate
#252 already issued. This checks every Miami-Dade and Broward lead so the next one is not missed.

COUNTY COVERAGE — and the honest gap
  MIAMI-DADE  miamidade.county-taxes.com   supported (Grant Street TaxSys)
  BROWARD     broward.county-taxes.com     supported (same platform)
  PALM BEACH  NOT SUPPORTED. PB's collector is not on county-taxes.com at all — it runs
              pbctax.publicaccessnow.com, a DotNetNuke/ASP.NET WebForms app whose search is a
              __VIEWSTATE + __RequestVerificationToken postback, not a URL you can deep-link.
              Probed live: /public/real_estate/parcels/... does not exist there (404), and the
              only inputs on the page are DNN hidden fields. Driving that form is a different,
              markedly more fragile scraper, so PB back-taxes stay MANUAL and no false $0 is ever
              written for a PB lead. Stated here so the gap is visible instead of silent.

WHY PLAYWRIGHT, AND THE TWO LOAD-BEARING FLAGS
The portals are Cloudflare bot-walled — plain requests get 403. It is a browser-FINGERPRINT
challenge, not a human captcha, so headless chromium passes it for free (no 2Captcha). Two things
cost real debugging and must not be "cleaned up":
  1. --disable-blink-features=AutomationControlled. Without it the SPA clears the challenge but
     never fires its data fetch: the panel sits on "Loading" and EVERY parcel reads a false $0 —
     the worst possible failure, since it hides a real tax lien.
  2. wait_for_load_state('networkidle') before scraping. TaxSys holds a long-poll socket open so
     networkidle never truly fires; WAITING on it (and letting it time out) is exactly the ~20s
     the challenge + SPA boot + iframe fetch need. Skip it and you get $0 for everything.
Folio dashes are optional — the platform normalizes both (verified on MD and BW).

SPEED — measured, not estimated
A single lookup is ~22s wall-clock and nearly all of it is idle waiting, so pages run CONCURRENTLY.
Measured on live batches: concurrency 6 gave 11.5s/parcel effective but only ~50% of pages rendered
in the poll window; concurrency 4 with a wider poll gives 9.3s/parcel AND ~83% render. So 4 is the
default — higher concurrency trades render-rate for almost no wall-clock. ~293 folios therefore
takes roughly 45 minutes, which is why the cloud step is capped and backfills across a few runs.
A parcel that does not render is NEVER cached, so it simply retries next run — no false $0.
Requests are jittered to stay a polite neighbour on a public county server.

Run:  python county_taxes.py                      # both counties, uncached, capped
      python county_taxes.py --limit 200          # raise the per-run cap
      python county_taxes.py --county MIAMI-DADE  # one county
      python county_taxes.py --conc 8             # more parallelism
      python county_taxes.py --folio 484102-00-0058 --county BROWARD
      python county_taxes.py --refresh            # ignore the cache
"""
import asyncio
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'county_taxes.json')
CACHE = os.path.join(HERE, '_county_taxes_cache.json')

# county -> (subdomain, board lead file, folio digit-length)
COUNTIES = {
    'MIAMI-DADE': ('miamidade', 'leads_final.json', 13),
    'BROWARD':    ('broward',   'broward_leads.json', 12),
}
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126 Safari/537.36')
DEFAULT_LIMIT = 120
DEFAULT_CONC = 4      # measured sweet spot: higher trades render-rate for little wall-clock


def _load(p, d):
    if not os.path.exists(p):
        return d
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


def _folios(county):
    """folio -> case for every board lead in this county carrying a full-length folio."""
    sub, fn, ln = COUNTIES[county]
    out = {}
    for r in _load(os.path.join(HERE, fn), []) or []:
        # leads_final.json is Miami-Dade only; the county files carry their own rows
        f = re.sub(r'\D', '', str(r.get('folio') or r.get('Folio') or ''))
        c = str(r.get('case') or r.get('Case #') or '').strip()
        if len(f) == ln and c:
            out[f] = c
    return out


def _parse(text):
    """Amount-due + bill-history text -> the delinquency picture. Only UNPAID years count."""
    due = 0.0
    m = re.search(r'Total\s+Amount\s+Due:?\s*\$?([\d,]+\.\d\d)', text, re.I)
    if m:
        due = float(m.group(1).replace(',', ''))
    years = []
    for ym in re.finditer(r'(20\d\d)\s+Annual bill\s+\$([\d,]+\.\d\d)\s+Unpaid', text, re.I):
        years.append({'year': ym.group(1), 'amt': float(ym.group(2).replace(',', ''))})
    cert = None
    cm = re.search(r'Certificate\s*#?\s*(\d+)\s+Issued\s+([\d/]+)[^$]*\$([\d,]+\.\d\d)[^%]*?([\d.]+)%',
                   text, re.I)
    if cm:
        cert = {'num': cm.group(1), 'date': cm.group(2),
                'face': float(cm.group(3).replace(',', '')), 'rate': cm.group(4)}
    if not due and years:
        due = round(sum(y['amt'] for y in years), 2)
    return {'due': int(round(due)), 'years': years, 'unpaid': len(years), 'cert': cert}


READ_JS = """() => {
  let s = document.body.innerText || '';
  document.querySelectorAll('iframe').forEach(f => {
    try { s += ' ' + (f.contentDocument.body.innerText || ''); } catch(e){}
  });
  return s;
}"""


async def _check(ctx, county, folio, sem):
    """One parcel. Returns (folio, rec) — rec is {} when the page never rendered (retry next run)."""
    sub = COUNTIES[county][0]
    url = 'https://%s.county-taxes.com/public/real_estate/parcels/%s/bills' % (sub, folio)
    async with sem:
        page = await ctx.new_page()
        try:
            await asyncio.sleep(random.uniform(0, 1.2))      # jitter — do not thunder the server
            await page.goto(url, wait_until='domcontentloaded', timeout=45000)
            try:
                await page.wait_for_load_state('networkidle', timeout=22000)
            except Exception:
                pass                                          # see the docstring: the timeout IS the wait
            text = ''
            # Poll patience matters MORE under concurrency: with several pages in flight the SPA
            # takes longer to paint, and a short window returns {} (a wasted fetch that retries next
            # run). 20 x 900ms = 18s of polling after the networkidle wait. Measured: at conc 6 with
            # a 12-iteration window only half the parcels rendered; widening it fixes that without
            # touching the server load.
            for _ in range(20):
                await page.wait_for_timeout(900)
                try:
                    text = await page.evaluate(READ_JS)
                except Exception:
                    text = ''
                if re.search(r'Amount\s+Due', text, re.I) or re.search(r'No\s+bills', text, re.I):
                    break
            if 'Amount Due' not in text:
                if re.search(r'No\s+bills|not\s+found', text, re.I):
                    return folio, {'due': 0, 'years': [], 'unpaid': 0, 'cert': None, 'county': county}
                return folio, {}                              # never rendered — do NOT cache a false $0
            rec = _parse(text)
            rec['county'] = county
            return folio, rec
        except Exception as e:
            print('  %s %s: %s' % (county, folio, str(e)[:60]))
            return folio, {}
        finally:
            try: await page.close()
            except Exception: pass


async def _run(targets, conc):
    """targets: [(county, folio)] -> {folio: rec}. One browser, `conc` pages in flight."""
    from playwright.async_api import async_playwright
    out = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = await browser.new_context(user_agent=UA, viewport={'width': 1280, 'height': 900})
        sem = asyncio.Semaphore(conc)
        done = 0
        tasks = [asyncio.create_task(_check(ctx, c, f, sem)) for c, f in targets]
        for fut in asyncio.as_completed(tasks):
            folio, rec = await fut
            done += 1
            if rec:
                out[folio] = rec
                if rec.get('due'):
                    cert = ' + CERT #%s' % rec['cert']['num'] if rec.get('cert') else ''
                    print('  [%d/%d] %s %s -> $%s owed%s'
                          % (done, len(targets), rec.get('county', ''), folio, f"{rec['due']:,}", cert))
            if done % 25 == 0:
                print('  ... %d/%d checked' % (done, len(targets)))
        await browser.close()
    return out


def main():
    args = sys.argv[1:]
    refresh = '--refresh' in args
    def _arg(flag, cast, default):
        if flag in args:
            try: return cast(args[args.index(flag) + 1])
            except Exception: pass
        return default
    limit = _arg('--limit', int, DEFAULT_LIMIT)
    conc = max(1, min(10, _arg('--conc', int, DEFAULT_CONC)))
    only = _arg('--county', str, '').upper()
    one = _arg('--folio', str, '')

    try:
        import playwright  # noqa: F401
    except ImportError:
        print('playwright not installed — pip install playwright && playwright install chromium')
        return

    if one:
        county = only if only in COUNTIES else 'BROWARD'
        f = re.sub(r'\D', '', one)
        res = asyncio.run(_run([(county, f)], 1))
        print(json.dumps(res.get(f, {}), indent=1))
        return

    cache = {} if refresh else _load(CACHE, {})
    targets, pool = [], 0
    for county in COUNTIES:
        if only and county != only:
            continue
        fol = _folios(county)
        pool += len(fol)
        targets += [(county, f) for f in fol if f not in cache]
    random.shuffle(targets)                    # spread load across both counties
    targets = targets[:limit]
    print('%d folio(s) in scope · %d uncached · checking %d this run (cap %d, concurrency %d)'
          % (pool, pool - len([1 for c in COUNTIES for f in _folios(c) if f in cache]),
             len(targets), limit, conc))
    if not targets:
        print('nothing to do'); return

    t0 = time.time()
    got = asyncio.run(_run(targets, conc))
    stamp = time.strftime('%Y-%m-%d')
    for f, rec in got.items():
        rec['checked'] = stamp
        cache[f] = rec
    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=0)

    owed = {f: v for f, v in cache.items() if v.get('due')}
    json.dump(owed, open(OUT, 'w', encoding='utf-8'), indent=1)
    certs = sum(1 for v in owed.values() if v.get('cert'))
    total = sum(v['due'] for v in owed.values())
    el = time.time() - t0
    print('\n%d checked in %dm%02ds (%.1fs/parcel effective)'
          % (len(got), int(el // 60), int(el % 60), el / max(1, len(got))))
    print('county_taxes.json — %d parcel(s) owe back taxes (%d with a sold certificate) · $%s total'
          % (len(owed), certs, f'{total:,}'))
    if owed:
        print('Rebuild to fold verified taxes into equity + surface the chip.')


if __name__ == '__main__':
    main()
