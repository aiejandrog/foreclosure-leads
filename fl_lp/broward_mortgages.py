#!/usr/bin/env python
"""fl_lp.broward_mortgages — countywide sweep of RECORDED MORTGAGES for the hard-money balloon play.

This is the volume unlock behind hardmoney_balloon.py. That tool proved the concept on mortgages
attached to properties ALREADY in foreclosure — i.e. balloons that already popped. The money is
catching the loan 8-24 months in, BEFORE the balloon, and those loans are not in the foreclosure
file. They are in the recorder's mortgage index, which this sweeps directly.

Same AcclaimWeb rails as fl_lp/broward.py (doctype + record-date range, no captcha, native curl
through the Cloudflare TLS wall). What differs from the lis-pendens sweep:
  * DOCTYPE = MORTGAGE, code discovered off the live form (title contains "MORTGAGE / MODIFICATIONS")
    and cached — never hardcoded, Acclaim renumbers. Fallback = 159 (observed 2026-08-12).
  * PARTY MAPPING IS REVERSED. On a mortgage, DirectName = BORROWER (mortgagor) and IndirectName =
    LENDER (mortgagee) — verified live: SANDOVAL,CARLOS -> NAVY FEDERAL; FERNANDEZ,JAIME -> JETSET
    VOYAGES LLC. (On a lis pendens, Direct = plaintiff.) We check both orientations per row anyway,
    because the recorder occasionally swaps sides, and keep whichever yields LLC-borrower + private-
    lender.
  * `Consideration` carries the LOAN AMOUNT (real number, e.g. 300000.0).

THE FILTER (Jesse's criteria): borrower is an LLC/entity, lender name is a hard-money signature
(STRONG tokens only — CAPITAL/LENDING/FUND/PRIVATE/BRIDGE/etc., excludes every institutional
lender + credit unions), loan amount > 0, recorded inside the balloon window.

Run:  python fl_lp/broward_mortgages.py --months 18        # sweep last 18 months, write the file
      python fl_lp/broward_mortgages.py --months 18 --chunk 30
Writes broward_mortgages.json (list of hard-money-to-LLC rows). hardmoney_balloon.py reads it.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import broward_liens as B                        # noqa: E402

CODE_CACHE = os.path.join(HERE, 'broward_doctypes.json')
OUT = os.path.join(REPO, 'broward_mortgages.json')
FALLBACK_MTG_CODE = '159'                         # "MORTGAGE / MODIFICATIONS & ASSUMPTIONS", 2026-08-12

# STRONG hard-money signature. Against a 40k-row countywide index the loose tokens (GROUP/HOLDINGS/
# VENTURES) over-match legitimate companies, so the sweep requires a token that actually names a
# lending business. hardmoney_balloon.py keeps the looser set for the small foreclosure-scoped list.
HM_STRONG = re.compile(r'\b(CAPITAL|LENDING|LENDERS?|FUND(?:ING|S)?\b|PRIVATE\s*(?:MONEY|LEND)|'
                       r'BRIDGE\s*(?:LOAN|LEND|CAP)|HARD\s*MONEY|MORTGAGE\s*FUND|TRUST\s*DEED|'
                       r'REI\s*CAPITAL|EQUITY\s*(?:FUND|LEND|CAP)|LOAN\s*FUND|FINANCE\s*OF\s*AMERICA|'
                       r'RCN|KIAVI|LIMA\s*ONE|ANCHOR\s*LOAN|CONSTRUCTIVE|GENESIS\s*CAP|TOORAK|'
                       r'ROC\s*CAP|CENTER\s*STREET|BENWORTH|HOUSEMAX|LENDZ|CROSBY\s*LEND)\b', re.I)
BIG_BANK = re.compile(r'\b(WELLS\s*FARGO|BANK\s*OF\s*AMERICA|\bCHASE\b|JPMORGAN|CITI|U\.?S\.?\s*BANK|'
                      r'PNC|TRUIST|SUNTRUST|REGIONS|FIFTH\s*THIRD|\bMERS\b|MORTGAGE\s*ELECTRONIC|'
                      r'FREDDIE|FANNIE|QUICKEN|ROCKET|LOANDEPOT|PENNYMAC|HSBC|TD\s*BANK|NATIONSTAR|'
                      r'MR\.?\s*COOPER|FLAGSTAR|USAA|NAVY\s*FED|CREDIT\s*UNION|\bFHA\b|\bHUD\b|\bVA\b|'
                      r'CALIBER|FREEDOM\s*MORTGAGE|CARRINGTON|NEWREZ|CROSSCOUNTRY|GUARANTEED\s*RATE|'
                      r'HOMEPOINT|AMERIHOME|MUTUAL\s*OF\s*OMAHA|GUILD\s*MORTGAGE|CMG\s*MORTGAGE|'
                      r'PARAMOUNT\s*RESIDENTIAL|UNITED\s*WHOLESALE|BROKER\s*SOLUTIONS)\b', re.I)
COMPANY = re.compile(r'\b(LLC|L\.L\.C|\bINC\b|CORP|\bCO\b|\bLP\b|LLP|LTD|TRUST|HOLDINGS|PROPERTIES|'
                     r'GROUP|ENTERPRISES?|INVESTMENTS?|VENTURES?|REALTY|CAPITAL|PARTNERS|'
                     r'DEVELOPMENT|ACQUISITIONS?|HOMES?)\b', re.I)


def _mtg_code():
    sess_form = None
    try:
        sess_form = B._curl(B.BASE + '/Search/SearchTypeDocType')
        m = re.search(r'title="MORTGAGE\s*/\s*MODIFICATIONS[^"]*"[^>]*value="(\d+)"', sess_form)
        if not m:
            m = re.search(r'value="(\d+)"[^>]*title="MORTGAGE\s*/\s*MODIFICATIONS', sess_form)
        if m:
            code = m.group(1)
            cache = {}
            try:
                cache = json.load(open(CODE_CACHE, encoding='utf-8'))
            except Exception:
                pass
            cache['mtg'] = code
            cache['mtg_seen'] = datetime.date.today().isoformat()
            json.dump(cache, open(CODE_CACHE, 'w', encoding='utf-8'))
            return code
    except Exception:
        pass
    try:
        return json.load(open(CODE_CACHE, encoding='utf-8')).get('mtg') or FALLBACK_MTG_CODE
    except Exception:
        return FALLBACK_MTG_CODE


def _date(js):
    m = re.search(r'/Date\((-?\d+)', js or '')
    if not m:
        return ''
    d = datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=int(m.group(1)))
    return '%04d-%02d-%02d' % (d.year, d.month, d.day)


def _classify(a, b):
    """(borrower, lender) if one side is a private lender and the other an entity, else None.
    a=DirectName (usually borrower), b=IndirectName (usually lender). Tries the natural mapping
    first, then the swap, because the recorder is not perfectly consistent."""
    for borrower, lender in ((a, b), (b, a)):
        if HM_STRONG.search(lender) and not BIG_BANK.search(lender) and COMPANY.search(borrower) \
           and not HM_STRONG.search(borrower):
            return borrower.strip(), lender.strip()
    return None


def _sweep_window(sess, code, d_from, d_to):
    resp = B._curl(B.BASE + '/Search/SearchTypeDocType?Length=9', post=[
        ('DocTypes', code), ('DocTypesDisplay-input', 'MORTGAGE'), ('DocTypesDisplay', 'MORTGAGE'),
        ('BookTypes', sess['booktypes']), ('BookTypesDisplay', 'All'),
        ('RecordDateFrom', d_from), ('RecordDateTo', d_to), ('DateRangeList', ' ')])
    if 'ShowError' in resp:
        print('BROWARD MTG: search rejected (%s..%s) — doctype code may have moved'
              % (d_from, d_to), file=sys.stderr)
        return None
    out, page, scanned = [], 1, 0
    while True:
        grid = B._curl(B.BASE + '/Search/GridResults', post=[
            ('page', str(page)), ('size', '400'), ('sort', ''), ('group', ''), ('filter', '')])
        try:
            j = json.loads(grid)
        except Exception:
            print('BROWARD MTG: grid parse failed on page %d' % page, file=sys.stderr)
            break
        rows = j.get('data') or []
        for r in rows:
            scanned += 1
            amt = float(r.get('Consideration') or 0)
            if amt <= 0:
                continue
            hit = _classify((r.get('DirectName') or '').strip(), (r.get('IndirectName') or '').strip())
            if not hit:
                continue
            borrower, lender = hit
            out.append({
                'origin': _date(r.get('RecordDate')),
                'borrower': borrower, 'lender': lender, 'amt': int(round(amt)),
                'instrument': str(r.get('InstrumentNumber') or '').strip(),
                'bookpage': (r.get('BookPage') or '').strip(),
                'parcel': str(r.get('ParcelNumber') or '').strip(),
                'legal': (r.get('DocLegalDescription') or '').strip(),
                'county': 'BROWARD',
            })
        total = int(j.get('total') or 0)
        if page * 400 >= total or not rows:
            break
        page += 1
        time.sleep(0.5)
    return out, scanned


def sweep(months=18, chunk_days=30, skip_recent=0):
    """-> list of hard-money-to-LLC mortgage rows, or None when the portal blocked us."""
    sess = B.start_session()
    if not sess:
        print('BROWARD MTG: no session (Cloudflare) — sweep skipped, not empty', file=sys.stderr)
        return None
    code = _mtg_code()
    today = datetime.date.today()
    end = today - datetime.timedelta(days=int(skip_recent * 30.4))
    start = today - datetime.timedelta(days=int(months * 30.4))
    all_hits, total_scanned = [], 0
    # Sweep in date CHUNKS. AcclaimWeb caps a single grid at what it will page, and a year+ of
    # mortgages is ~50k rows; monthly chunks keep each search returnable and let a mid-sweep block
    # keep the chunks already collected.
    cur = start
    while cur < end:
        nxt = min(cur + datetime.timedelta(days=chunk_days), end)
        r = _sweep_window(sess, code, cur.strftime('%m/%d/%Y'), nxt.strftime('%m/%d/%Y'))
        if r is None:
            break
        hits, scanned = r
        all_hits.extend(hits)
        total_scanned += scanned
        print('  %s..%s : %d mortgages scanned, %d hard-money-to-LLC'
              % (cur.isoformat(), nxt.isoformat(), scanned, len(hits)), flush=True)
        cur = nxt
        time.sleep(0.6)
    # dedupe by instrument (a modification re-records the same loan)
    seen, uniq = set(), []
    for h in sorted(all_hits, key=lambda x: x['origin'], reverse=True):
        k = h['instrument'] or (h['borrower'], h['lender'], h['origin'])
        if k not in seen:
            seen.add(k)
            uniq.append(h)
    print('BROWARD MTG: %d mortgages scanned over %d months -> %d hard-money-to-LLC (deduped)'
          % (total_scanned, months, len(uniq)))
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=18)
    # SWEEP AN OLDER SLICE without re-scanning what's already collected. An 18-month sweep leaves
    # the 2-YEAR balloons (18-30mo old) — the ones coming due RIGHT NOW — completely missing from
    # the file, which is exactly the tier the play targets. `--skip-recent 18 --months 30` sweeps
    # only the 18-to-30-month band and merges into the existing rows.
    ap.add_argument('--skip-recent', type=int, default=0, metavar='MONTHS',
                    help='do not sweep the most recent N months (merge an older band in)')
    ap.add_argument('--chunk', type=int, default=30, help='date chunk size in days')
    ap.add_argument('--dry', action='store_true', help='sweep + print, do not write the file')
    a = ap.parse_args()
    rows = sweep(months=a.months, chunk_days=a.chunk, skip_recent=a.skip_recent)
    if rows is None:
        sys.exit(2)
    # MERGE with what's already on disk (an older-band sweep must not delete the recent band)
    try:
        prior = json.load(open(OUT, encoding='utf-8'))
    except Exception:
        prior = []
    if prior:
        seen = {(r.get('instrument') or (r.get('borrower'), r.get('lender'), r.get('origin')))
                for r in rows}
        merged = rows + [p for p in prior
                         if (p.get('instrument') or (p.get('borrower'), p.get('lender'), p.get('origin'))) not in seen]
        print('merged with %d existing rows -> %d total' % (len(prior), len(merged)))
        rows = sorted(merged, key=lambda r: str(r.get('origin') or ''), reverse=True)
    for h in rows[:25]:
        print('  %s | $%9s | %-30s <- %s' % (h['origin'], format(h['amt'], ','),
                                             h['borrower'][:30], h['lender'][:34]))
    if not a.dry:
        tmp = OUT + '.tmp'
        json.dump(rows, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        os.replace(tmp, OUT)
        print('-> %s (%d rows)' % (OUT, len(rows)))


if __name__ == '__main__':
    main()
