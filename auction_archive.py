#!/usr/bin/env python3
"""auction_archive.py — remember every sale we ever watched, so the buyer list can build itself.

THE GAP THIS CLOSES
The nightly pipeline scrapes the auction calendar, publishes it, and then FORGETS it. A property
that sells drops off the next scrape, taking its case number with it. So on 2026-08-04, ninety days
of Miami-Dade auctions had left exactly 15 cases still visible — and cash_buyers.py could only check
those 15. Every other sale we watched all summer was unrecoverable.

A certificate of title names the party that actually won and paid cash. That is the only prospect
list in this business that cannot be bought, and it is worthless without the case numbers. So:
append-only, every night, forever. Cheap (a few hundred KB/yr) and it compounds.

TIMING MATTERS — LEARNED FROM A REAL FILE
Martin condo (2026-020206-CC-25): sale 05/27/2026, certificate of title 06/16/2026 — TWENTY DAYS
later. Check a case the morning after its sale and you will correctly find nothing and cache that
nothing. Hence `--mature-days` (default 21): a sale is only handed to the buyer-checker once its
certificate has had time to issue.

  python auction_archive.py                 # snapshot today's board into the archive
  python auction_archive.py --stats         # what we are holding
  python auction_archive.py --due           # sales now mature enough to check for a buyer
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(HERE, 'auction_archive.json')
DESKTOP_BOARD = os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop', 'DEALFLOW',
                             'Foreclosure Lead Tracker.html')
DOCS_BOARD = os.path.join(HERE, 'docs', 'index.html')
# Source lead files — used on CI, where no Desktop board exists.
LEAD_FILES = ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json')


def _d(s):
    for f in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(str(s).strip(), f).date()
        except (ValueError, TypeError):
            continue
    return None


def _rows_from_board():
    """Prefer the built board (fully enriched). Falls back to the raw lead files on CI."""
    for p in (DESKTOP_BOARD, DOCS_BOARD):
        if not os.path.exists(p):
            continue
        try:
            txt = open(p, encoding='utf-8', errors='ignore').read()
            m = re.search(r'const RAW\s*=\s*(\[.*?\]);\s*\n', txt, re.S)
            if m:
                rows = json.loads(m.group(1))
                if rows:
                    return rows, os.path.basename(p)
        except Exception:
            pass
    out = []
    for fn in LEAD_FILES:
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding='utf-8'))
            rows = d if isinstance(d, list) else (d.get('leads') or [])
            for r in rows:
                out.append({'case': r.get('case') or r.get('Case #'),
                            'auction': r.get('auction') or r.get('AuctionDate'),
                            'addr': r.get('addr') or r.get('Address'),
                            'value': r.get('value'), 'judg': r.get('judgment') or r.get('judg'),
                            'county': r.get('county')})
        except Exception:
            pass
    return out, 'lead files'


def load():
    if os.path.exists(ARCHIVE):
        try:
            return json.load(open(ARCHIVE, encoding='utf-8')) or {}
        except Exception:
            return {}
    return {}


def save(d):
    tmp = ARCHIVE + '.tmp'
    json.dump(d, open(tmp, 'w', encoding='utf-8'), indent=1, sort_keys=True)
    os.replace(tmp, ARCHIVE)


def snapshot():
    """APPEND-ONLY. A case already in the archive keeps its ORIGINAL sale date unless the new one is
    later — a reset sale legitimately moves the date forward, but a scrape glitch must never erase
    a date we already recorded, because that is the only key the buyer lookup has."""
    rows, src = _rows_from_board()
    arc = load()
    today = datetime.date.today().isoformat()
    added = moved = 0
    for r in rows:
        case = str(r.get('case') or '').strip()
        auc = _d(r.get('auction'))
        if not case or not auc:
            continue
        cur = arc.get(case)
        if not cur:
            arc[case] = {'auction': auc.isoformat(), 'addr': r.get('addr') or '',
                         'county': r.get('county') or '', 'value': r.get('value') or 0,
                         'judg': r.get('judg') or 0, 'first_seen': today, 'last_seen': today}
            added += 1
        else:
            cur['last_seen'] = today
            prev = _d(cur.get('auction'))
            if prev and auc > prev:          # sale reset to a later date
                cur['auction'] = auc.isoformat()
                cur['reset_from'] = prev.isoformat()
                moved += 1
            # keep the richest address/value we have ever seen
            if not cur.get('addr') and r.get('addr'):
                cur['addr'] = r['addr']
    save(arc)
    print(f'auction archive: {len(arc)} sale(s) remembered  (+{added} new, {moved} reset) '
          f'[source: {src}]')
    return arc


def due(arc, mature_days=21, checked=None):
    """Sales old enough that a certificate of title should exist, and not yet checked."""
    checked = checked or {}
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=mature_days)
    out = []
    for case, v in arc.items():
        a = _d(v.get('auction'))
        if not a or a > cutoff:
            continue
        if case in checked:
            continue
        out.append((case, v))
    out.sort(key=lambda kv: kv[1].get('auction') or '')
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--due', action='store_true')
    ap.add_argument('--mature-days', type=int, default=21)
    a = ap.parse_args()

    if a.stats or a.due:
        arc = load()
        if not arc:
            print('archive is empty — run `python auction_archive.py` once to seed it.')
            return 0
        if a.stats:
            today = datetime.date.today()
            past = [v for v in arc.values() if (_d(v.get('auction')) or today) < today]
            fut = len(arc) - len(past)
            dates = sorted(v['auction'] for v in arc.values() if v.get('auction'))
            print(f'{len(arc)} sales remembered · {len(past)} past · {fut} upcoming')
            print(f'range {dates[0]} .. {dates[-1]}' if dates else '')
        if a.due:
            try:
                import cash_buyers
                checked = cash_buyers.load_cache()
            except Exception:
                checked = {}
            d = due(arc, a.mature_days, checked)
            print(f'{len(d)} sale(s) mature (>{a.mature_days}d) and unchecked:')
            for case, v in d[:40]:
                print(f"  {v.get('auction')}  {case:24} {str(v.get('addr'))[:40]}")
        return 0

    snapshot()
    return 0


if __name__ == '__main__':
    sys.exit(main())
