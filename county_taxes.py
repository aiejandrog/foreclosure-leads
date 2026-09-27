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

SUSTAINED-RUN THROTTLING — measured the hard way
Short batches and long batches behave DIFFERENTLY. A 24-parcel run at concurrency 4 rendered ~83%
at 9.3s/parcel. A 202-parcel run in the same session rendered only 49 (24%) at 41.7s/parcel — the
county server clearly degrades service under sustained load, and the later a request lands in a
long run the likelier it is to time out. Nothing is lost (an unrendered parcel is never cached, so
it simply retries), but a big --limit is FALSE ECONOMY: it burns wall-clock for a shrinking yield.
The right shape is what the cloud already does — a modest cap every morning, chipping away daily.
Hence DEFAULT_LIMIT is deliberately small. Raise it only if you are willing to watch it.

SPEED — measured, not estimated
A single lookup is ~22s wall-clock and nearly all of it is idle waiting, so pages run CONCURRENTLY.
Measured on live batches: concurrency 6 gave 11.5s/parcel effective but only ~50% of pages rendered
in the poll window; concurrency 4 with a wider poll gives 9.3s/parcel AND ~83% render. So 4 is the
default — higher concurrency trades render-rate for almost no wall-clock. ~293 folios therefore
takes roughly 45 minutes, which is why the cloud step is capped and backfills across a few runs.
A parcel that does not render is NEVER cached, so it simply retries next run — no false $0.
Requests are jittered to stay a polite neighbour on a public county server.

NEAR-SALE FIRST, AND READS GO STALE (Tax1, 2026-09-26)
Before this, a folio read once was cached FOREVER: a parcel checked in August at $0 stayed $0 on the
board through a September tax-certificate sale, and the per-run cap went to whichever folios the
shuffle happened to pick. Now each run picks by plan_targets():
  1. sale in the next 45 days, soonest first (the reads a deal decision actually waits on),
  2. parcels with a SOLD CERTIFICATE (the tax-deed clock; see cert_followup),
  3. never read,
  4. everything else whose read has gone stale, oldest first; past sales last.
"Stale" depends on how close the sale is (max_age_days): 3 days inside 14 days of sale, 7 days
inside 45 days or with a certificate, 30 days otherwise -- the same 30 days miami_ranking's
freshness gate uses. A parcel that fails to render keeps its previous read (never a false $0).
board_fields() is what the rebuild bakes: the amount, a taxStale age when the read is out of date
(including a stale "$0" -- which is the dangerous one), and the certificate follow-up.

CERTIFICATE FOLLOW-UP (FS 197.502(1), 197.482(1))
A certificate holder may apply for a tax deed once two years have passed since April 1 of the year
the certificate was issued, and a certificate with no application expires seven years after
issuance. cert_followup() dates both and re-reads certificate parcels weekly; a page that mentions a
tax-deed application or sale is flagged. Every certificate on the page is kept (certs), not only the
first.

Run:  python county_taxes.py                      # both counties, near-sale first, capped
      python county_taxes.py --limit 200          # raise the per-run cap
      python county_taxes.py --county MIAMI-DADE  # one county
      python county_taxes.py --conc 8             # more parallelism
      python county_taxes.py --folio 484102-00-0058 --county BROWARD
      python county_taxes.py --refresh            # ignore the cache
"""
import asyncio
import datetime
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
DEFAULT_LIMIT = 60       # see SUSTAINED-RUN THROTTLING: bigger runs yield less, not more
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
    return {f: v['case'] for f, v in _folio_rows(county).items()}


def _sale_date(row):
    """The lead's auction date (MM/DD/YYYY on the board rows, ISO elsewhere), or None."""
    s = str((row or {}).get('auction') or (row or {}).get('AuctionDate') or (row or {}).get('sale') or '').strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _folio_rows(county, rows=None):
    """folio -> {'case', 'sale'} for every board lead in this county carrying a full-length folio."""
    sub, fn, ln = COUNTIES[county]
    out = {}
    rows = _load(os.path.join(HERE, fn), []) if rows is None else rows
    for r in rows or []:
        # leads_final.json is Miami-Dade only; the county files carry their own rows
        f = re.sub(r'\D', '', str(r.get('folio') or r.get('Folio') or ''))
        c = str(r.get('case') or r.get('Case #') or '').strip()
        if len(f) == ln and c:
            sale = _sale_date(r)
            prev = out.get(f)
            # two cases on one folio: the SOONER upcoming sale decides how fresh the read must be
            if prev and prev['sale'] and (not sale or prev['sale'] <= sale):
                continue
            out[f] = {'case': c, 'sale': sale}
    return out


# ---- Tax1: how fresh a read must be, and which parcels to read first ---------------------------
FRESH_NEAR_DAYS, FRESH_NEAR_AGE = 14, 3     # sale inside 14 days: re-read after 3 days
FRESH_SOON_DAYS, FRESH_SOON_AGE = 45, 7     # inside 45 days (or a sold certificate): after 7
FRESH_CERT_AGE = 7
FRESH_DEFAULT_AGE = 30                      # = miami_ranking.TAX_MAX_AGE


def _as_date(s):
    try:
        return datetime.date.fromisoformat(str(s or '')[:10])
    except ValueError:
        return None


def days_to_sale(sale, today):
    return (sale - today).days if sale else None


def max_age_days(dts, has_cert=False):
    """How old a read may be before it is stale, given days to the sale (None = no date)."""
    if dts is not None and 0 <= dts <= FRESH_NEAR_DAYS:
        return FRESH_NEAR_AGE
    if (dts is not None and 0 <= dts <= FRESH_SOON_DAYS) or has_cert:
        return min(FRESH_SOON_AGE, FRESH_CERT_AGE)
    return FRESH_DEFAULT_AGE


def read_age(rec, today):
    d = _as_date((rec or {}).get('checked'))
    return (today - d).days if d else None


def is_stale(rec, dts, today):
    """True when there is no dated read, or the read is older than max_age_days allows."""
    age = read_age(rec, today)
    if age is None:
        return True
    return age > max_age_days(dts, bool((rec or {}).get('cert')))


def plan_targets(folio_rows, cache, today, limit):
    """[(county, folio)] to read this run, most decision-relevant first. See the docstring.

    folio_rows: {county: {folio: {'case', 'sale'}}}. Only stale or never-read parcels are picked."""
    cand = []
    for county, rows in folio_rows.items():
        for f, v in rows.items():
            rec = cache.get(f)
            dts = days_to_sale(v.get('sale'), today)
            if not is_stale(rec, dts, today):
                continue
            age = read_age(rec, today)
            if dts is not None and 0 <= dts <= FRESH_SOON_DAYS:
                key = (0, dts, 0)
            elif rec and rec.get('cert'):
                key = (1, -(age or 0), 0)
            elif rec is None or age is None:
                key = (2, dts if (dts is not None and dts >= 0) else 99999, 0)
            elif dts is not None and dts < 0:
                key = (4, -(age or 0), 0)
            else:
                key = (3, -(age or 0), 0)
            cand.append((key, county, f))
    cand.sort(key=lambda x: (x[0], x[2]))
    return [(c, f) for _, c, f in cand[:max(0, limit)]]


def cert_followup(cert, today):
    """Dates for a sold certificate: when a tax-deed application becomes possible (2 years after
    April 1 of the issuance year, FS 197.502(1)) and when an unapplied certificate expires (7 years
    after issuance, FS 197.482(1)). None when the issue date cannot be read."""
    d = None
    s = str((cert or {}).get('date') or '').strip()
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d'):
        try:
            d = datetime.datetime.strptime(s, fmt).date()
            break
        except ValueError:
            continue
    if not d:
        return None
    tda_from = datetime.date(d.year + 2, 4, 1)
    try:
        expires = d.replace(year=d.year + 7)
    except ValueError:                       # Feb 29
        expires = d.replace(year=d.year + 7, day=28)
    state = ('expired' if today >= expires else
             'tax_deed_application_possible' if today >= tda_from else 'holding')
    return {'issued': d.isoformat(), 'tda_possible_from': tda_from.isoformat(),
            'expires': expires.isoformat(), 'state': state,
            'days_to_tda_window': max(0, (tda_from - today).days)}


def board_fields(rec, sale, today):
    """What the rebuild bakes for one lead from its cached read. {} when nothing to say."""
    out = {}
    dts = days_to_sale(sale, today)
    age = read_age(rec, today)
    if rec and rec.get('due'):
        out['taxDue'] = int(rec['due'])
        out['taxYears'] = [y.get('year') for y in (rec.get('years') or []) if y.get('year')]
        out['taxCert'] = bool(rec.get('cert'))
        out['taxChecked'] = rec.get('checked', '')
    # STALE-READ GATE: a read older than the sale-aware window is flagged, $0 reads included. A
    # stale $0 is the dangerous case: no chip at all used to read as "no back taxes".
    if not rec or age is None:
        if dts is not None and dts >= 0:
            out['taxStale'] = 'never'
    elif age > max_age_days(dts, bool(rec.get('cert'))):
        out['taxStale'] = age
        out['taxChecked'] = rec.get('checked', '')
    certs = (rec or {}).get('certs') or ([rec['cert']] if (rec or {}).get('cert') else [])
    certs = [c for c in certs if isinstance(c, dict)]      # an old-shape `cert: true` has no date
    fu = [c for c in (cert_followup(c, today) for c in certs) if c]
    if fu:
        out['taxCertFollow'] = fu[0] if len(fu) == 1 else sorted(fu, key=lambda x: x['tda_possible_from'])[0]
        out['taxCertN'] = len(certs)
    if (rec or {}).get('tax_deed_signal'):
        out['taxDeedSignal'] = rec['tax_deed_signal']
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
    certs = []
    for cm in re.finditer(r'Certificate\s*#?\s*(\d+)\s+Issued\s+([\d/]+)[^$]*?\$([\d,]+\.\d\d)[^%]*?([\d.]+)%',
                          text, re.I):
        c = {'num': cm.group(1), 'date': cm.group(2),
             'face': float(cm.group(3).replace(',', '')), 'rate': cm.group(4)}
        if all(c['num'] != x['num'] for x in certs):
            certs.append(c)
    cert = certs[0] if certs else None
    if not due and years:
        due = round(sum(y['amt'] for y in years), 2)
    out = {'due': int(round(due)), 'years': years, 'unpaid': len(years), 'cert': cert}
    if len(certs) > 1:
        out['certs'] = certs
    sig = re.search(r'.{0,40}tax\s+deed\s+(?:application|applied|sale|auction|file)[^.\n]{0,60}', text, re.I)
    if sig:
        out['tax_deed_signal'] = re.sub(r'\s+', ' ', sig.group(0)).strip()[:120]
    return out


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
    today = datetime.date.today()
    rows = {c: _folio_rows(c) for c in COUNTIES if not only or c == only}
    pool = sum(len(v) for v in rows.values())
    uncached = sum(1 for v in rows.values() for f in v if f not in cache)
    # Tax1: near-sale first, then certificates, then never-read, then stale -- see plan_targets.
    targets = plan_targets(rows, cache, today, limit)
    stale_n = len(plan_targets(rows, cache, today, 10 ** 9))
    near = sum(1 for v in rows.values() for x in v.values()
               if x['sale'] and 0 <= (x['sale'] - today).days <= FRESH_SOON_DAYS)
    print('%d folio(s) in scope · %d never read · %d due for a (re)read · %d with a sale in %d days '
          '· checking %d this run (cap %d, concurrency %d)'
          % (pool, uncached, stale_n, near, FRESH_SOON_DAYS, len(targets), limit, conc))
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
