#!/usr/bin/env python
"""reports/funnel_truth.py - per day: dials, conversations, appointments, offers, contracts. Read-only, counts only.

The board's Worked tile (_funnelStats) answers "how many leads are at or past each rung TODAY". It cannot
say what happened on a given day, so it cannot say whether last week's dials turned into anything. This
does, from the same notes the board pushes to worker_notes.json:

  dials          outcome rows in notes[case].dials[] on that day, plus a dial with no outcome tapped
                 (same rule as call_stats.py: a pending/redial marker with no later outcome that day)
  conversations  dial outcomes talked / callback / appt (the CONVO set outcome_learn.py uses)
  appointments   distinct leads with an appt dial that day, or stageAt['Appointment'] == that day
  offers         stageAt['Offer made'] == that day; legacy lead (no stageAt): status 'Offer made', outcomeAt
  contracts      stageAt['Under contract'] == that day; legacy lead: status Under contract / Assigned /
                 CLOSED, dated by outcomeAt (the FIRST terminal stamp, so it can be the offer day;
                 the footer says how many leads rest on it)

Counts only: no case numbers, names, phone numbers or addresses are put in the output. Writes nothing.

    python reports/funnel_truth.py                  # last 14 days, table
    python reports/funnel_truth.py --days 30 --json
    python reports/funnel_truth.py --notes PATH     # a copy of worker_notes.json

Notes are found, in order: --notes, $DEALFLOW_NOTES, worker_notes.json in paths.DEALFLOW_DIR, then the
repo root (where send_server writes it).
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

CONVO = {'talked', 'callback', 'appt'}
MARKERS = {'pending', 'redial'}
OFFER_ST = {'Offer made'}
CONTRACT_ST = {'Under contract', 'Assigned', 'CLOSED'}
COLS = ['date', 'dials', 'conversations', 'appointments', 'offers', 'contracts']


def find_notes(path=None):
    cands = [path, os.environ.get('DEALFLOW_NOTES')]
    try:
        import paths as P
        cands.append(os.path.join(P.DEALFLOW_DIR, 'worker_notes.json'))
    except Exception:
        pass
    cands.append(os.path.join(REPO, 'worker_notes.json'))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _day(s):
    s = str(s or '')[:10]
    try:
        dt.date.fromisoformat(s)
        return s
    except ValueError:
        return ''


def compute(notes, days=14, today=None):
    """Per-day rows (dicts keyed by COLS), newest first. `notes` is worker_notes['notes']."""
    today = today or dt.date.today()
    cut = (today - dt.timedelta(days=days - 1)).isoformat() if days else ''
    by = collections.defaultdict(lambda: dict.fromkeys(COLS[1:], 0))
    seen = set()                                    # (column, lead index, day): one lead counts once a day

    def hit(day, col, who):
        if not day or (cut and day < cut) or day > today.isoformat():
            return
        k = (col, who, day)
        if k in seen:
            return
        seen.add(k)
        by[day][col] += 1

    for who, (case, n) in enumerate((notes or {}).items()):   # `who` is an index, never the case number
        if str(case)[:1] in ('@', '#') or not isinstance(n, dict):
            continue
        dl = [d for d in (n.get('dials') or []) if isinstance(d, dict) and _day(d.get('d'))]
        for i, d in enumerate(dl):
            day, oc = _day(d['d']), d.get('oc') or ''
            if cut and day < cut:
                continue
            if oc in MARKERS:
                if any(x.get('d') == d['d'] and x.get('oc') not in MARKERS for x in dl[i + 1:]):
                    continue                        # its outcome row is the dial
                by[day]['dials'] += 1
                continue
            by[day]['dials'] += 1
            if oc in CONVO:
                by[day]['conversations'] += 1
            if oc == 'appt':
                hit(day, 'appointments', who)
        sa = n.get('stageAt') if isinstance(n.get('stageAt'), dict) else {}
        st = str(n.get('status') or '')
        if sa.get('Appointment'):
            hit(_day(sa['Appointment']), 'appointments', who)
        if sa.get('Offer made'):
            hit(_day(sa['Offer made']), 'offers', who)
        elif st in OFFER_ST and n.get('outcomeAt'):
            hit(_day(n['outcomeAt']), 'offers', who)
        if sa.get('Under contract'):
            hit(_day(sa['Under contract']), 'contracts', who)
        elif st in CONTRACT_ST and n.get('outcomeAt'):
            hit(_day(n['outcomeAt']), 'contracts', who)
    return [dict(date=d, **by[d]) for d in sorted(by, reverse=True)]


def legacy_count(notes):
    """Leads whose offer/contract date rests on outcomeAt (no stageAt) - the caveat on those columns."""
    c = 0
    for case, n in (notes or {}).items():
        if str(case)[:1] in ('@', '#') or not isinstance(n, dict):
            continue
        sa = n.get('stageAt') if isinstance(n.get('stageAt'), dict) else {}
        st = str(n.get('status') or '')
        if n.get('outcomeAt') and st in (OFFER_ST | CONTRACT_ST):
            if not sa.get('Offer made' if st in OFFER_ST else 'Under contract'):
                c += 1
    return c


def render(rows, legacy=0):
    out = ['%-10s %6s %13s %12s %7s %9s' % tuple(COLS)]
    for r in rows:
        out.append('%-10s %6d %13d %12d %7d %9d' % tuple(r[c] for c in COLS))
    if not rows:
        out.append('(no dated activity in this window)')
    if legacy:
        out.append('%d lead(s) are dated by outcomeAt (first terminal stamp), not by stageAt.' % legacy)
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--notes', default=None)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    path = find_notes(a.notes)
    if not path:
        print('no worker_notes.json found (looked at --notes, $DEALFLOW_NOTES, the repo root, DEALFLOW_DIR)')
        return 1
    try:
        with open(path, encoding='utf-8') as f:
            wn = json.load(f)
    except Exception as e:
        print('worker_notes.json unreadable (%s)' % e.__class__.__name__)
        return 1
    notes = wn.get('notes') if isinstance(wn, dict) and 'notes' in wn else wn
    rows = compute(notes, a.days)
    leg = legacy_count(notes)
    if a.json:
        print(json.dumps({'days': a.days, 'rows': rows, 'legacy_dated': leg}, indent=1))
    else:
        print('FUNNEL TRUTH  last %d days  (worker_notes received %s)'
              % (a.days, wn.get('received_utc') if isinstance(wn, dict) else '?'))
        print(render(rows, leg))
    return 0


if __name__ == '__main__':
    sys.exit(main())
