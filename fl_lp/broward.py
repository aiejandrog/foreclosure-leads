#!/usr/bin/env python
"""fl_lp.broward — fresh LIS PENDENS from Broward Official Records (AcclaimWeb), by DOCUMENT
TYPE over a RECORD DATE RANGE. No names, no captcha — one session, one search, one grid pull.

Verified live 2026-08-11: doctype search form served after the disclaimer session; the Lis
Pendens code is DISCOVERED off the form's own checkboxes (title="LIS PENDENS (LP)") and cached
to fl_lp/broward_doctypes.json — never hardcoded, because Acclaim renumbering is exactly the
kind of silent breakage the MD sweeper just demonstrated for a month. Fallback = cached code =
158 (the value observed at discovery time).

Party semantics verified on live rows: DirectName = plaintiff (CITIBANK, WILMINGTON, FREEDOM
MORTGAGE, the HOA), IndirectName = defendant/owner. ParcelNumber and DocLegalDescription come
back EMPTY in the grid view, so Broward rows enter the pipeline address-unresolved — same
state as a Miami-Dade row before lp_resolve. Resolution is Phase 2 (BCPA adapter); the rows
are already actionable as owner-name + case + plaintiff for docket watch and skip-trace.

Transport: broward_liens._curl — native Windows System32 curl only (Cloudflare TLS wall),
coin-flip challenge retries built in. Sessions are cheap; cookies are never reused.
"""
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import broward_liens as B                      # noqa: E402  (_curl, start_session, BASE)

CODE_CACHE = os.path.join(HERE, 'broward_doctypes.json')
FALLBACK_LP_CODE = '158'                       # observed at discovery, 2026-08-11


def _lp_code(sess):
    """Discover the Lis Pendens doctype code off the live form; cache; fall back to cache."""
    try:
        form = B._curl(B.BASE + '/Search/SearchTypeDocType')
        m = re.search(r'name="DocTypeInfoCheckBox"\s+title="LIS PENDENS \(LP\)"[^>]*value="(\d+)"',
                      form)
        if m:
            code = m.group(1)
            json.dump({'lp': code, 'seen': datetime.date.today().isoformat()},
                      open(CODE_CACHE, 'w', encoding='utf-8'))
            return code
    except Exception:
        pass
    try:
        return json.load(open(CODE_CACHE, encoding='utf-8')).get('lp') or FALLBACK_LP_CODE
    except Exception:
        return FALLBACK_LP_CODE


def _date(js):
    m = re.search(r'/Date\((-?\d+)', js or '')
    if not m:
        return ''
    d = datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=int(m.group(1)))
    return '%d/%d/%d' % (d.month, d.day, d.year)


def sweep(days=30):
    """-> list of canonical LP rows (county='BROWARD'), or None when the portal blocked us.
    None vs [] matters: an empty week is data, a blocked session is not."""
    from lis_pendens import LENDER_RE, HOA_RE
    sess = B.start_session()
    if not sess:
        print('BROWARD: no session (Cloudflare) — sweep skipped, not empty', file=sys.stderr)
        return None
    code = _lp_code(sess)
    d_to = datetime.date.today().strftime('%m/%d/%Y')
    d_from = (datetime.date.today() - datetime.timedelta(days=days)).strftime('%m/%d/%Y')
    resp = B._curl(B.BASE + '/Search/SearchTypeDocType?Length=9', post=[
        ('DocTypes', code), ('DocTypesDisplay-input', 'LIS PENDENS (LP)'),
        ('DocTypesDisplay', 'LIS PENDENS (LP)'),
        ('BookTypes', sess['booktypes']), ('BookTypesDisplay', 'All'),
        ('RecordDateFrom', d_from), ('RecordDateTo', d_to), ('DateRangeList', ' ')])
    if 'ShowError' in resp:
        print('BROWARD: search rejected (ShowError) — doctype code may have moved', file=sys.stderr)
        return None
    out, page = [], 1
    while True:
        grid = B._curl(B.BASE + '/Search/GridResults', post=[
            ('page', str(page)), ('size', '400'), ('sort', ''), ('group', ''), ('filter', '')])
        try:
            j = json.loads(grid)
        except Exception:
            print('BROWARD: grid parse failed on page %d' % page, file=sys.stderr)
            return out if out else None
        rows = j.get('data') or []
        for r in rows:
            plaintiff = (r.get('DirectName') or '').strip()
            owner = (r.get('IndirectName') or '').strip()
            # the recorder occasionally swaps sides; trust the lender regex over position
            if owner and LENDER_RE.search(owner) and not LENDER_RE.search(plaintiff):
                plaintiff, owner = owner, plaintiff
            if not owner or LENDER_RE.search(owner):
                continue                              # lender-vs-lender = assignment noise
            kind = ('BANK-1st' if LENDER_RE.search(plaintiff)
                    else 'HOA/JUNIOR' if HOA_RE.search(plaintiff) else 'OTHER/PRIVATE')
            out.append({
                'date': _date(r.get('RecordDate')),
                'case': (r.get('CaseNumber') or '').strip(),
                'docType': (r.get('DocTypeDescription') or 'Lis Pendens').strip(),
                'bookpage': (r.get('BookPage') or '').strip(),
                'legal': (r.get('DocLegalDescription') or '').strip(),
                'parties': '%s; %s' % (plaintiff, owner),
                'plaintiff': plaintiff,
                'owner': owner,
                'kind': kind,
                'county': 'BROWARD',
                'instrument': str(r.get('InstrumentNumber') or '').strip(),
                'parcel': str(r.get('ParcelNumber') or '').strip(),
            })
        total = int(j.get('total') or 0)
        if page * 400 >= total or not rows:
            break
        page += 1
        time.sleep(0.6)
    print('BROWARD: %s..%s -> %d homeowner LP filings' % (d_from, d_to, len(out)))
    return out


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    a = ap.parse_args()
    rows = sweep(days=a.days)
    if rows is None:
        sys.exit(2)
    print(json.dumps(rows[:5], indent=1))
    print('total:', len(rows))
