#!/usr/bin/env python
"""ownership_scan.py — nightly ownership-flip scan that feeds the board's dead-lead gate.

Reads the auction-window leads from all three counties, runs ownership_gate LIVE per folio, and writes
  ownership.json  {case: {title_status, title_owner, title_flag, title_evidence, ts}}
which make_tracker bakes into each board row (DATA[]). STAMP-ONLY by design: the board shows the flag
and isFlaggedDead reads 'transferred' only once someone flips OWNERSHIP_GATE_HOLD on — until then a
transferred/unverified lead is stamped for a human, never silently dropped.

Why per county the comparison target differs:
  * Miami-Dade leads carry a real foreclosure DEFENDANT (leads_final 'defendants'); compare the LIVE
    MD-PA owner to it — a mismatch is a transfer.
  * Broward / Palm Beach carry NO defendant, only the FDOR-cadastral owner ('oname'), which LAGS.
    Comparing that lagging owner to the LIVE appraiser owner is exactly what surfaces a transfer the
    cadastral has not caught yet (the Milouse case).

Bounded on purpose: only leads with an auction inside --days (default 45) are scanned, capped at
--max (default 80), soonest-auction first, so a night is dozens of free appraiser hits, not thousands.

Run:  python ownership_scan.py                 # scan + write ownership.json
      python ownership_scan.py --days 60 --max 120
      python ownership_scan.py --case CACE-24-006635   # one lead, print verdict, no write
"""
import argparse
import datetime
import json
import os

import ownership_gate as OG

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'ownership.json')

# (file, county, case_key, folio_key, target_owner_key, auction_key, days_key, filed_key)
#
# 🔴 WHY MIAMI-DADE COMPARES AGAINST 'owners' AND NOT 'defendants' (bug caught 2026-08-14 on the first
# clean full-board scan: 32 of 46 "transferred" flags were false). foreclosure_leads.enrich_clerk builds
# 'defendants' from the Clerk's party list while SKIPPING defs[0] — defs[0] is treated as the owner. So
# 'defendants' is the CO-defendants: condo associations, lenders, the USA, spouses. Comparing the live
# appraiser owner to that guarantees a mismatch on almost every MD lead (we were literally comparing the
# homeowner to their own HOA and calling it a sale — e.g. an owner's own LLC flagged as the buyer).
# 'owners' IS the right target: Miami-Dade already enriches from the live MD-PA at scrape time, so
# stored-vs-live catches a transfer that happened SINCE our last scrape, and the Certificate-of-Title-
# after-filing check catches the sold-via-a-separate-case pattern. Broward/PB stay on 'oname' because
# there the stored owner is the FDOR cadastral, which lags months — that lag is the whole Milouse trap.
SOURCES = [
    ('leads_final.json',    'MIAMI-DADE', 'Case #', 'Folio', 'owners', 'AuctionDate', 'days_to_auction', None),
    ('broward_leads.json',  'BROWARD',    'case',   'folio', 'oname',      'auction',     'days',            'filed'),
    ('palmbeach_leads.json','PALM BEACH', 'case',   'folio', 'oname',      'auction',     'days',            None),
]


def _load(f):
    try:
        d = json.load(open(os.path.join(HERE, f), encoding='utf-8'))
        return d if isinstance(d, list) else list(d.values())
    except Exception:
        return []


def _int(v):
    try:
        return int(float(str(v)))
    except Exception:
        return None


def collect(days_window):
    rows = []
    for f, county, ck, fk, ok, ak, dk, filedk in SOURCES:
        for r in _load(f):
            if not isinstance(r, dict):
                continue
            folio = r.get(fk)
            case = r.get(ck)
            owner = r.get(ok) or r.get('owners') or r.get('oname')
            if not folio or not case or not owner:
                continue
            d = _int(r.get(dk))
            if d is None or d < 0 or d > days_window:
                continue                      # only the auction window that reaches outreach
            filed = r.get(filedk) if filedk else None
            if str(filed) in ('0', '0.0', 'None'):
                filed = None
            rows.append({'case': str(case), 'folio': str(folio), 'owner': str(owner),
                         'county': county, 'filed': filed, 'days': d})
    rows.sort(key=lambda r: r['days'])        # soonest auctions first
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=45, help='only auctions within N days')
    ap.add_argument('--max', type=int, default=80, help='cap leads scanned per run')
    ap.add_argument('--case', default='', help='scan one case (by number), print, no write')
    ap.add_argument('--budget', type=int, default=180,
                    help='wall-clock seconds before remaining leads bail to unverified')
    a = ap.parse_args()

    rows = collect(a.days)
    if a.case:
        rows = [r for r in rows if r['case'].upper() == a.case.upper()]
        if not rows:
            raise SystemExit('case %s not in the %dd auction window of any county file' % (a.case, a.days))
        r = rows[0]
        res = OG.check_lead(r['folio'], r['owner'], filed=r['filed'], county=r['county'], force=True)
        print(json.dumps({**res, 'case': r['case'], 'county': r['county']}, indent=1, ensure_ascii=False))
        return 0

    if len(rows) > a.max:
        print('scan: %d leads in window, capping to the %d soonest (raise --max to cover more)'
              % (len(rows), a.max))
        rows = rows[:a.max]
    print('ownership scan: %d leads (<= %dd auction) across MD/Broward/PB' % (len(rows), a.days))

    kept, held = OG.gate_rows(rows, folio_key='folio', owner_key='owner', filed_key='filed',
                              county_key='county', budget_s=a.budget)
    # MERGE, NEVER OVERWRITE (fixed 2026-08-27).
    # This used to be `out = {}` then a full-file json.dump — so every run REPLACED ownership.json
    # with only the cases IT had just scanned. `--max` defaults well below the board size, so a
    # routine `--days 45 --max 40` silently erased the other 40 verdicts, INCLUDING both recorded
    # TRANSFERRED flips. That is the one stamp in this file that kills a lead outright: it is what
    # stops a closer pitching equity to somebody who no longer owns the house (Milouse), and losing
    # it turns a proven-gone property back into a callable lead with no trace in any log.
    # Caught while running this script as the documented remedy for a diligence hold — the remedy
    # was destroying the evidence the gate reads.
    # Same one-way posture as optout_sync.py and cadence.py's ledger write: a fresh scan of a case
    # supersedes the old verdict for THAT case, and a case this run did not look at keeps whatever
    # it had. Re-running is idempotent.
    prev = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding='utf-8') as _fh:
                _p = json.load(_fh)
            if isinstance(_p, dict):
                prev = _p
        except Exception as _pe:
            # An unreadable existing file must not be silently replaced with a partial scan.
            print('ownership.json unreadable (%s) — NOT overwriting it. Fix or move it, then '
                  're-run.' % str(_pe)[:100])
            return 1
    out = dict(prev)
    for r in rows:
        out[r['case']] = {'title_status': r.get('title_status', 'unverified'),
                          'title_owner': r.get('title_owner', ''),
                          'title_flag': r.get('title_flag', ''),
                          'title_evidence': r.get('title_evidence', ''),
                          'ts': datetime.date.today().isoformat()}
    tmp = OUT + '.tmp'
    json.dump(out, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    os.replace(tmp, OUT)
    if prev:
        _scanned = {r['case'] for r in rows}
        _carried = len([c for c in prev if c not in _scanned])
        print('ownership scan: %d case(s) scanned this run (%d re-scanned), %d carried forward '
              'untouched, %d total in ownership.json'
              % (len(_scanned), len([c for c in _scanned if c in prev]), _carried, len(out)))

    flips = [c for c, v in out.items() if v['title_status'] == 'transferred']
    print('wrote ownership.json — %d cases (%d TRANSFERRED, %d unverified)'
          % (len(out), len(flips), sum(1 for v in out.values() if v['title_status'] == 'unverified')))
    for c in flips[:25]:
        print('  TRANSFERRED  %-24s %s' % (c, out[c]['title_owner']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
