#!/usr/bin/env python3
"""coverage_report.py — how much of each county's new filings the LIS PENDENS sweep actually holds.

READ ONLY. Reads lp_sweep_status.json (what the last sweep says it covered) and lis_pendens.json (the
filings on disk), prints counts, writes nothing, spends nothing, makes no request.

TWO QUESTIONS, KEPT APART (2026-10-22)
  1. Did the last sweep read its whole window?  From the coverage record lis_pendens.main writes:
     the window, the total the source declared, the rows pulled, the failures, and when the county
     last had a complete read. Broward and Palm Beach declare a total, so "complete" is provable
     there. Miami-Dade's plaintiff-name search declares none, so the best it can say is "every listed
     plaintiff searched, none at the record cap", which is complete over the list and silent about
     plaintiffs not on it.
  2. How does what we hold compare with the county's own count?  Only Miami-Dade publishes one we
     have found: the Clerk's monthly "Number of Filings" for mortgage foreclosures (BENCHMARKS below,
     with its source and the day it was read). Broward and Palm Beach totals have to be pulled by hand
     from the Florida Courts trial court statistics, or given with --bench.

WHAT THE RATIO CAN AND CANNOT SAY
  Our month is the RECORDING date of the lis pendens; the Clerk's month is the case FILING date, and
  the two can fall in different months. The Clerk counts cases, we count recorded instruments after
  dropping lender-vs-lender rows. So a ratio for one month is approximate in both directions; read a
  run of months, not one. A low ratio is a real gap. A ratio near 1 is consistent with complete
  coverage, not proof of it.

Run:  python coverage_report.py
      python coverage_report.py --bench "BROWARD:2026-07=410"   # a total you looked up yourself
      python coverage_report.py --json                           # the same numbers, machine-readable
"""
import argparse
import collections
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS = os.path.join(HERE, 'lp_sweep_status.json')
LP = os.path.join(HERE, 'lis_pendens.json')

STALE_DAYS = 2

# Miami-Dade Clerk, "Statistics for Mortgage Foreclosures", Number of Filings per month.
# https://www.miamidadeclerk.gov/clerk/mortgage-foreclosures.page, read 2026-10-22. Public aggregate
# counts, no names. Add a month when the Clerk posts it; never estimate one.
BENCHMARKS = {
    'MIAMI-DADE': {
        '_source': 'Miami-Dade Clerk mortgage foreclosure filings (read 2026-10-22)',
        '2025-07': 300, '2025-08': 283, '2025-09': 295, '2025-10': 318, '2025-11': 293,
        '2025-12': 334, '2026-01': 315, '2026-02': 328, '2026-03': 314, '2026-04': 350,
        '2026-05': 312, '2026-06': 359, '2026-07': 293,
    },
}


def _load(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def _month(d):
    """'M/D/YYYY' or 'YYYY-MM-DD' -> 'YYYY-MM', else ''."""
    s = str(d or '').strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(s.split(' ')[0], fmt).strftime('%Y-%m')
        except ValueError:
            pass
    return ''


def monthly(rows):
    """{county: {'YYYY-MM': count}} over the filings on disk."""
    out = collections.defaultdict(collections.Counter)
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        m = _month(r.get('date'))
        if m:
            out[r.get('county') or 'MIAMI-DADE'][m] += 1
    return out


def parse_bench(items):
    """['BROWARD:2026-07=410', ...] -> {county: {month: n}}. Bad items raise ValueError."""
    out = collections.defaultdict(dict)
    for it in items or []:
        county, rest = it.split(':', 1)
        month, n = rest.split('=', 1)
        datetime.datetime.strptime(month.strip(), '%Y-%m')
        out[county.strip().upper()][month.strip()] = int(n)
    return out


def age_days(ts, now):
    try:
        return (now - datetime.datetime.fromisoformat(str(ts))).total_seconds() / 86400.0
    except Exception:
        return None


def _try_text(t):
    if not isinstance(t, dict):
        return '?'
    yn = lambda v: '-' if v is None else ('yes' if v else 'no')
    return 'token %s http %s qs %s read %s (%s)' % (yn(t.get('token')), t.get('http'), yn(t.get('qs')),
                                                    yn(t.get('read')), str(t.get('answer') or '')[:80])


def sweep_lines(status, now):
    """Question 1, one block per county."""
    cov = (status or {}).get('coverage') or {}
    if not cov:
        return ['  no coverage record yet: lp_sweep_status.json predates it or the sweep has not run.']
    lines = []
    for c in sorted(cov):
        e = cov[c] or {}
        tot = e.get('reported_total')
        win = e.get('window') or []
        lc = e.get('last_complete')
        age = age_days(lc, now) if lc else None
        lines.append(f"  {c:11} {str(e.get('status', '?')).upper():8} window {' .. '.join(win) or '?'}")
        lines.append(f"              source said {tot if tot is not None else 'no total'}; "
                     f"read {e.get('retrieved', 0)} in {e.get('pages', 0)} page(s); kept {e.get('kept', 0)}")
        if 'searched' in e:
            lines.append(f"              plaintiffs searched {e.get('searched')}, not searched {e.get('blocked', 0)}, "
                         f"at the record cap {len(e.get('capped') or [])}")
        lines.append(f"              scope: {e.get('scope', '?')}")
        for name, tries in sorted((e.get('attempts') or {}).items()):
            lines.append(f"              not searched: {name}: "
                         + ('; '.join(_try_text(t) for t in tries) if tries else 'never tried (paid cap or run stopped)'))
        for f in (e.get('failures') or [])[:5]:
            lines.append(f"              failure: {f}")
        if lc is None:
            lines.append('              last complete read: NONE ON RECORD')
        else:
            lines.append(f"              last complete read: {lc}"
                         + (f'  (STALE, {age:.1f} days)' if age is not None and age > STALE_DAYS else ''))
    return lines


def bench_rows(counts, bench):
    """Question 2: [(county, month, ours, theirs, ratio)] for every month that has a benchmark."""
    out = []
    for c in sorted(bench):
        for m in sorted(k for k in bench[c] if not k.startswith('_')):
            ours = counts.get(c, {}).get(m, 0)
            theirs = bench[c][m]
            out.append((c, m, ours, theirs, (ours / theirs) if theirs else None))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--bench', action='append', default=[],
                    help='COUNTY:YYYY-MM=N, an official monthly total you looked up (repeatable)')
    ap.add_argument('--months', type=int, default=6, help='how many recent months of our own counts to show')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    now = datetime.datetime.now()
    status = _load(STATUS, {})
    counts = monthly(_load(LP, []))
    bench = {c: dict(v) for c, v in BENCHMARKS.items()}
    for c, v in parse_bench(a.bench).items():
        bench.setdefault(c, {}).update(v)
    rows = bench_rows(counts, bench)
    if a.json:
        print(json.dumps({'coverage': status.get('coverage') or {}, 'ran': status.get('ran'),
                          'monthly': {c: dict(v) for c, v in counts.items()},
                          'benchmark': [dict(zip(('county', 'month', 'ours', 'official', 'ratio'), r))
                                        for r in rows]}, indent=1))
        return 0
    print(f"LP SWEEP COVERAGE — last sweep {status.get('ran') or 'never'}")
    print('\n1. Did the last sweep read its whole window?')
    for line in sweep_lines(status, now):
        print(line)
    print(f'\n2. Filings on disk by recording month (last {a.months})')
    for c in sorted(counts):
        ms = sorted(counts[c])[-a.months:]
        print(f"  {c:11} " + '  '.join(f'{m}:{counts[c][m]}' for m in ms))
    print('\n3. Against an official monthly count (our recording month vs their filing month)')
    if not rows:
        print('  no benchmark months')
    for c, m, ours, theirs, r in rows:
        first = min(counts.get(c) or {'9999': 0})
        if m < first:
            continue                      # before our file starts: not a gap, just no data
        print(f"  {c:11} {m}  ours {ours:>4}  official {theirs:>4}  "
              + (f'{r:.0%}' if r is not None else '—')
              + ('   (our file starts this month: partial by construction)' if m == first else ''))
    print('\n  Broward and Palm Beach: no public monthly total wired in. Look one up in the Florida Courts')
    print('  trial court statistics (circuit civil, real property / mortgage foreclosure, by county) and')
    print('  pass it with --bench. A ratio is approximate; read several months, not one.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
