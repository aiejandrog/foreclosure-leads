#!/usr/bin/env python3
"""pb_court_filings.py -- new Palm Beach foreclosure FILINGS from the court's own case search. $0.

WHY (measured live 2026-11-16). Palm Beach's lis pendens intake has been dead since the recorder
sweep stopped (newest filing on file 2026-08-26): the recorder's search (Landmark) answers
'Invalid Captcha' to every query without a paid solve. The COURT publishes the same cases for free.
eCaseView's search takes Court Type + Case Type + a filing-date window, and its case types are the
foreclosure categories themselves ("HR FORECLOSURE >$50K, <$250K", "ASSN LIEN FORECLOSURE ..."). One
search for one category over Nov 1-15 returned 16 cases filed through 11/13.

The court site only admits a person's own Chrome (pb_court_inbox.py explains), so the searches run
there and the result rows are posted to the inbox, or saved as a filings_*.json file in it. This
module turns those rows into lis_pendens.json rows and merges them. Existing rows always win.

    python pb_court_filings.py            merge every filings_*.json in the inbox
    python pb_court_filings.py --dry-run  counts only, write nothing

A court row: {"case": "50-2026-CA-012740-XXXA-MB", "type": "HR FORECLOSURE > $50K, < $250K",
              "filed": "11/13/2026", "style": "LENDER V OWNER", "status": "Open"}
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIS = os.path.join(HERE, 'lis_pendens.json')
FORECLOSURE_TYPE_RE = re.compile(r'FORECLOSURE', re.I)
TIMESHARE_RE = re.compile(r'TIMESHARE', re.I)
CLOSED_RE = re.compile(r'closed|disposed|dismiss', re.I)
# the case types to search, as the court spells them in the Case Type list (Court Type = Circuit Civil)
CASE_TYPES = [
    'HR FORECLOSURE =< $50K', 'HR FORECLOSURE >$50K, <$250K', 'HR FORECLOSURE => $250K',
    'NON HR FORECLOSURE =< $50K', 'NON HR FORECLOSURE>$50K,<$250K', 'NON HR FORECLOSURE => $250K',
    'FORECLOSURE =< $50K', 'FORECLOSURE >$50K, <$250K', 'FORECLOSURE => $250K',
    'ASSN LIEN FORECLOSURE =< $50K', 'ASSN LN FORECLOSURE>$50,<$250K', 'ASSN LIEN FORECLOSURE => $250K',
    'REAL PROPERTY/FORECLOSURE',
]


def ucn(case):
    """'50-2026-CA-012740-XXXA-MB' -> '502026CA012740XXXAMB' (the shape lis_pendens.json uses)."""
    v = re.sub(r'[^A-Z0-9]', '', str(case or '').upper())
    return v if re.fullmatch(r'50\d{4}(?:CA|CC)\d{6}[A-Z0-9]{6}', v) else ''


def key14(case):
    v = re.sub(r'[^A-Z0-9]', '', str(case or '').upper())
    return v[:14] if re.match(r'50\d{4}(?:CA|CC)\d{6}', v) else ''


def split_style(style):
    """'LENDER V OWNER, FIRST' -> ('LENDER', 'OWNER, FIRST'). ('', '') when there is no ' V '."""
    s = ' '.join(str(style or '').split())
    m = re.match(r'(.+?)\s+VS?\.?\s+(.+)$', s, re.I)
    return (m.group(1).strip(' ,'), m.group(2).strip(' ,')) if m else ('', '')


def _date(value):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            d = datetime.datetime.strptime(str(value or '').strip(), fmt).date()
            return '%02d/%02d/%04d' % (d.month, d.day, d.year)
        except ValueError:
            continue
    return ''


def to_row(court, kind_of=None):
    """One court search row -> a lis_pendens.json row, or None when it is not a usable filing."""
    if not isinstance(court, dict):
        return None
    case = ucn(court.get('case'))
    ctype = str(court.get('type') or '')
    if not case or not FORECLOSURE_TYPE_RE.search(ctype) or TIMESHARE_RE.search(ctype):
        return None
    if CLOSED_RE.search(str(court.get('status') or '')):
        return None
    plaintiff, owner = split_style(court.get('style'))
    date = _date(court.get('filed'))
    if not plaintiff or not owner or not date:
        return None
    parties = '%s; %s' % (plaintiff, owner)
    if kind_of is None:
        import lis_pendens as LP
        kind_of = LP._kind
    kind = 'HOA/JUNIOR' if re.search(r'ASSN', ctype, re.I) else kind_of(parties)
    return {'date': date, 'case': case, 'docType': 'Lis Pendens', 'bookpage': '', 'legal': '',
            'parties': parties, 'plaintiff': plaintiff, 'owner': owner, 'kind': kind,
            'county': 'PALM BEACH', 'instrument': '', 'parcel': '', 'src': 'court',
            'courtType': ctype[:40]}


def merge(existing, court_rows, kind_of=None):
    """-> (merged list, added, skipped_known, skipped_unusable). Existing rows are never changed."""
    known = {key14(x.get('case')) for x in existing
             if isinstance(x, dict) and (x.get('county') or '') == 'PALM BEACH'} - {''}
    out, added, dup, bad = list(existing), 0, 0, 0
    for c in court_rows or []:
        row = to_row(c, kind_of)
        if row is None:
            bad += 1
            continue
        k = key14(row['case'])
        if k in known:
            dup += 1
            continue
        known.add(k)
        out.append(row)
        added += 1
    return out, added, dup, bad


def inbox_rows(folder=None):
    if folder is None:
        import pb_court_inbox
        folder = pb_court_inbox.inbox_dir()
    rows, files = [], 0
    for path in sorted(glob.glob(os.path.join(folder, 'filings_*.json'))):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        files += 1
        rows += [r for r in (data.get('rows') if isinstance(data, dict) else data) or [] if isinstance(r, dict)]
    return rows, files


def main(argv=None):
    ap = argparse.ArgumentParser(description='Merge Palm Beach court filings into lis_pendens.json.')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    rows, files = inbox_rows()
    try:
        with open(LIS, encoding='utf-8') as f:
            existing = json.load(f)
    except (OSError, ValueError):
        print('pb_court_filings: lis_pendens.json not readable; nothing written')
        return 1
    merged, added, dup, bad = merge(existing, rows)
    print('pb_court_filings: %d file(s), %d court row(s): %d new filing(s), %d already on file, %d not usable'
          % (files, len(rows), added, dup, bad))
    if added and not a.dry_run:
        merged.sort(key=lambda x: datetime.datetime.strptime(x['date'], '%m/%d/%Y').toordinal()
                    if re.fullmatch(r'\d\d/\d\d/\d{4}', str(x.get('date') or '')) else 0, reverse=True)
        tmp = LIS + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(merged, f, indent=1)
        os.replace(tmp, LIS)
        print('pb_court_filings: lis_pendens.json now holds %d row(s)' % len(merged))
    return 0


if __name__ == '__main__':
    sys.exit(main())
