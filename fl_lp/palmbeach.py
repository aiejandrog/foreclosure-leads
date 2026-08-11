#!/usr/bin/env python
"""fl_lp.palmbeach — fresh LIS PENDENS from Palm Beach Official Records (Landmark).

STATUS AT BIRTH (2026-08-11): the county's Landmark portal is serving HTTP 500 on its own
ROOT page — down for everyone, verified with a plain browser fetch, not a block aimed at us.
This module is built and wired so the first nightly run after the portal recovers lands data
with zero further work. Until then sweep() returns None (blocked/down), never [] (empty week),
and the caller treats those differently.

DESIGN (cheapest captcha spend first):
  1. ONE-TOKEN sweep: Landmark's NameSearch payload carries `doctype` + a date range. Try an
     empty/wildcard name with doctype='LP' over the window — if the portal accepts it, a whole
     sweep costs a single reCAPTCHA solve (1-3 min via 2Captcha).
  2. PLAINTIFF-SWEEP fallback: the Miami-Dade pattern — name-search each major foreclosure
     plaintiff with the doctype filter. Costs one token per plaintiff (slow: ~30-50 min for the
     full list at v2 solve speeds), so it only runs with --deep.
Both emit the canonical lis_pendens row shape (county='PALM BEACH'). Landmark result rows carry
a LEGAL description (unlike Broward's grid), so Phase-2 resolution has something to work with.
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
import palmbeach_liens as P                    # noqa: E402  (_curl, session, captcha, gsr parsing)

LP_DOCTYPES = ('LP', 'LIS PENDENS', 'L P')     # vocabulary candidates; first accepted wins


def _win(days):
    return ((datetime.date.today() - datetime.timedelta(days=days)).strftime('%m/%d/%Y'),
            datetime.date.today().strftime('%m/%d/%Y'))


def _payload(name, doctype, d_from, d_to):
    return ('NameSearch', [('searchLikeType', '0'), ('type', '0'), ('name', name),
                           ('doctype', doctype), ('bookType', '0'),
                           ('beginDate', d_from), ('endDate', d_to), ('recordCount', '250'),
                           ('exclude', 'false'), ('ReturnIndexGroups', 'false'), ('townName', ''),
                           ('selectedNamesIds', ''), ('includeNickNames', 'false'),
                           ('selectedNames', ''), ('mobileHomesOnly', 'false')])


def _rows_to_canonical(docs):
    from lis_pendens import LENDER_RE, HOA_RE
    out = []
    for d in docs:
        if 'LIS' not in (d.get('DocTypeDescription') or '').upper():
            continue
        direct = (d.get('Name') or d.get('Direct') or '').strip()
        reverse = (d.get('Reverse') or '').strip()
        plaintiff, owner = direct, reverse
        if owner and LENDER_RE.search(owner) and not LENDER_RE.search(plaintiff):
            plaintiff, owner = owner, plaintiff
        if not owner or LENDER_RE.search(owner):
            continue
        kind = ('BANK-1st' if LENDER_RE.search(plaintiff)
                else 'HOA/JUNIOR' if HOA_RE.search(plaintiff) else 'OTHER/PRIVATE')
        out.append({'date': (d.get('RecordDate') or '').strip(),
                    'case': (d.get('CaseNumber') or '').strip(),
                    'docType': (d.get('DocTypeDescription') or 'LIS PENDENS').strip(),
                    'bookpage': (d.get('BookPage') or '').strip(),
                    'legal': (d.get('DocLegalDescription') or d.get('Legal') or '').strip(),
                    'parties': '%s; %s' % (plaintiff, owner),
                    'plaintiff': plaintiff, 'owner': owner, 'kind': kind,
                    'county': 'PALM BEACH',
                    'instrument': str(d.get('InstrumentNumber') or '').strip()})
    return out


def sweep(days=30, deep=False):
    """-> canonical rows, or None when the portal is down/blocked (None != empty)."""
    ok = False
    for i in range(4):
        ok = P.start_session()
        if ok:
            break
        time.sleep(10 + i * 10)
    if not ok:
        print('PALM BEACH: no Landmark session (portal 500/blocked) — sweep skipped',
              file=sys.stderr)
        return None
    d_from, d_to = _win(days)

    # ---- one-token attempt: empty name + doctype filter --------------------------------------
    for dt in LP_DOCTYPES:
        tok = P.solve_token_2captcha()
        if not tok:
            return None
        body = P.search(_payload('', dt, d_from, d_to), token=tok)
        if body is None or len(body or '') < 200:
            continue                            # rejected — try next vocabulary
        rows = P.get_search_results(250, 0)
        if rows:
            docs = P.gsr_rows_to_docs(rows)
            out = _rows_to_canonical(docs)
            print('PALM BEACH: %s..%s doctype=%r -> %d homeowner LP' % (d_from, d_to, dt, len(out)))
            return out
    if not deep:
        print('PALM BEACH: one-token sweep rejected; rerun with --deep for the plaintiff sweep '
              '(~1 captcha per plaintiff, slow)', file=sys.stderr)
        return None

    # ---- plaintiff sweep (expensive) ---------------------------------------------------------
    from lis_pendens import PLAINTIFFS
    seen, out = set(), []
    for name in PLAINTIFFS:
        tok = P.solve_token_2captcha()
        if not tok:
            break
        body = P.search(_payload(name, 'LP', d_from, d_to), token=tok)
        if body is None:
            continue
        rows = P.get_search_results(250, 0) or []
        for r in _rows_to_canonical(P.gsr_rows_to_docs(rows)):
            k = r['instrument'] or r['case'] or r['parties'][:50]
            if k not in seen:
                seen.add(k)
                out.append(r)
        time.sleep(0.6)
    print('PALM BEACH deep sweep: %d homeowner LP' % len(out))
    return out


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--deep', action='store_true')
    a = ap.parse_args()
    rows = sweep(days=a.days, deep=a.deep)
    if rows is None:
        sys.exit(2)
    print(json.dumps(rows[:4], indent=1))
    print('total:', len(rows))
