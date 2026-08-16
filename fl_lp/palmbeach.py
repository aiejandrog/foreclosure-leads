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

# Landmark's `doctype` takes a NUMERIC document-type ID, not a label. Every text vocabulary
# ('LP', 'LIS PENDENS', 'L P', 'NOTICE OF LIS PENDENS') is silently ignored and the portal returns the
# whole unfiltered window — which is why PB LP sat at 0 rows for months while the parameter name was
# blamed. The name was right all along; the VALUE was wrong.
# Ground truth, captured 2026-08-15 from the live page by calling Landmark's own
# SetCriteria('DocumentTypeSearch','DocumentType','documentTypeSearchForm') and dumping the object:
#   doctype=20&beginDate=..&endDate=..&recordCount=200&exclude=false&ReturnIndexGroups=false
#   &townName=&mobileHomesOnly=false
# ID 20 = LIS PENDENS (from #dt-DocumentType-20 on erec.mypalmbeachclerk.com; 87 types are listed).
# Verified: doctype=20 -> 200 records, 399 docs, 399 of them LIS PENDENS.
LP_DOCTYPE_ID = '20'
LP_ENDPOINT = 'DocumentTypeSearch'


def _win(days):
    return ((datetime.date.today() - datetime.timedelta(days=days)).strftime('%m/%d/%Y'),
            datetime.date.today().strftime('%m/%d/%Y'))


def _gsr_rows(res):
    """Unwrap Landmark's DataTables envelope -> the actual row list.

    `P.get_search_results()` returns the WHOLE envelope ({draw, recordsTotal, data:[...]}), not the
    rows. Passing the envelope straight into gsr_rows_to_docs() iterates its KEYS ('draw',
    'recordsTotal', 'data') — all strings, all skipped by the isinstance(row, dict) guard — so it
    yields zero docs while the envelope itself stays truthy. That is why Palm Beach logged
    "doctype='LP' -> 0 homeowner LP" every night and PB LP coverage sat at exactly 0 rows while
    Miami-Dade and Broward worked: a parse bug wearing the costume of an empty county.
    broward_judgment_dates.py:262 and palmbeach_liens.py:281 already unwrap; this module did not.
    """
    if isinstance(res, dict):
        return res.get('data') or res.get('aaData') or []
    return res or []


def _doctype_payload(d_from, d_to, doctype_id=LP_DOCTYPE_ID, count='200'):
    """Byte-for-byte the object Landmark's own SetCriteria() builds for a Document Type search.
    Note there is no `name` field at all — this is a whole-window doctype sweep, not a name search."""
    return (LP_ENDPOINT, [('doctype', doctype_id),
                          ('beginDate', d_from), ('endDate', d_to), ('recordCount', count),
                          ('exclude', 'false'), ('ReturnIndexGroups', 'false'),
                          ('townName', ''), ('mobileHomesOnly', 'false')])


def _payload(name, doctype, d_from, d_to):
    """NameSearch — kept for the --deep plaintiff sweep, which searches BY NAME."""
    return ('NameSearch', [('searchLikeType', '0'), ('type', '0'), ('name', name),
                           ('doctype', doctype), ('bookType', '0'),
                           ('beginDate', d_from), ('endDate', d_to), ('recordCount', '250'),
                           ('exclude', 'false'), ('ReturnIndexGroups', 'false'), ('townName', ''),
                           ('selectedNamesIds', ''), ('includeNickNames', 'false'),
                           ('selectedNames', ''), ('mobileHomesOnly', 'false')])


def _norm_date(v):
    """Landmark ships `/Date(1785729600000)/`; Miami-Dade and Broward canonical rows carry
    `M/D/YYYY`. Leaving the raw form here would put a second date dialect into lis_pendens.json —
    `_lp_age_days()` parses only %m/%d/%Y and %Y-%m-%d, so every PB row would read as age-unknown
    and the LP freshness/ordering logic would silently mis-sort the freshest county."""
    s = str(v or '').strip()
    m = re.search(r'/Date\((-?\d+)', s)
    if m:
        try:
            return datetime.datetime.utcfromtimestamp(int(m.group(1)) / 1000).strftime('%m/%d/%Y')
        except (ValueError, OverflowError, OSError):
            return ''
    return s


def _dedupe(rows):
    """One row per recorded instrument.

    gsr_rows_to_docs deliberately emits a TO-side MIRROR of every record, so a single filing arrives
    twice with plaintiff and owner swapped. Measured: 357 canonical rows for 179 real instruments —
    a 2x phantom inflation of the freshest lead lane if it reached the board. The lender/HOA swap
    heuristic above cannot always tell the mirrors apart (neither side matches LENDER_RE on an
    HOA-vs-owner filing), so dedupe explicitly and PREFER the orientation whose plaintiff is
    institutional — that is the one whose `owner` is the actual homeowner."""
    best = {}
    for r in rows:
        k = r.get('instrument') or r.get('case') or r.get('parties', '')[:60]
        cur = best.get(k)
        if cur is None or (r.get('kind') != 'OTHER/PRIVATE' and cur.get('kind') == 'OTHER/PRIVATE'):
            best[k] = r
    return list(best.values())


def _rows_to_canonical(docs):
    from lis_pendens import LENDER_RE, HOA_RE
    out = []
    for d in docs:
        if 'LIS' not in (d.get('DocTypeDescription') or '').upper():
            continue
        direct = (d.get('Name') or d.get('Direct') or '').strip()
        # gsr_rows_to_docs emits 'CrossPartyName' (the AcclaimWeb schema), NOT 'Reverse'. Reading
        # 'Reverse' left `owner` empty on every row, so the `if not owner: continue` below dropped
        # 100% of them — invisible until doctype=20 finally let real LIS PENDENS through (399 docs
        # in, 0 canonical out). Keep 'Reverse' as a fallback for any other caller's schema.
        reverse = (d.get('CrossPartyName') or d.get('Reverse') or '').strip()
        plaintiff, owner = direct, reverse
        if owner and LENDER_RE.search(owner) and not LENDER_RE.search(plaintiff):
            plaintiff, owner = owner, plaintiff
        if not owner or LENDER_RE.search(owner):
            continue
        kind = ('BANK-1st' if LENDER_RE.search(plaintiff)
                else 'HOA/JUNIOR' if HOA_RE.search(plaintiff) else 'OTHER/PRIVATE')
        out.append({'date': _norm_date(d.get('RecordDate')),
                    'case': (d.get('CaseNumber') or '').strip(),
                    'docType': (d.get('DocTypeDescription') or 'LIS PENDENS').strip(),
                    'bookpage': (d.get('BookPage') or '').strip(),
                    'legal': (d.get('DocLegalDescription') or d.get('Legal') or '').strip(),
                    'parties': '%s; %s' % (plaintiff, owner),
                    'plaintiff': plaintiff, 'owner': owner, 'kind': kind,
                    'county': 'PALM BEACH',
                    'instrument': str(d.get('InstrumentNumber') or '').strip()})
    return _dedupe(out)


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

    # ---- one-token DOCTYPE sweep -------------------------------------------------------------
    # Retry the SAME search rather than cycling vocabularies: the first search after start_session()
    # frequently returns Landmark's error page, and P.search() now detects that and returns None.
    # The old loop treated a transient rejection as "wrong vocabulary" and moved on, burning a
    # captcha per attempt on values that were never going to work anyway.
    for attempt in range(3):
        tok = P.solve_token_2captcha()
        if not tok:
            return None
        body = P.search(_doctype_payload(d_from, d_to), token=tok)
        if body is None:
            continue                            # transient rejection — same query, fresh token
        rows = _gsr_rows(P.get_search_results(250, 0))
        if rows:
            docs = P.gsr_rows_to_docs(rows)
            out = _rows_to_canonical(docs)
            # Report all three counts. "0 homeowner LP" alone cannot distinguish a quiet window from
            # a broken parse or an over-tight filter — and that ambiguity is exactly what hid the
            # envelope bug. raw>0 with out==0 means the FILTER dropped them, not the portal.
            print('PALM BEACH: %s..%s doctype=%s -> %d raw / %d parsed / %d homeowner LP'
                  % (d_from, d_to, LP_DOCTYPE_ID, len(rows), len(docs), len(out)))
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
        rows = _gsr_rows(P.get_search_results(250, 0))
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
