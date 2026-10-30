"""followup_gaps.py - for every lead at "Called - talked" or later, is there a dated next step, and when
was the last touch? Read-only. Counts and case ids only (no names, phones, addresses).

    python reports/followup_gaps.py                 # DEALFLOW_DIR/worker_notes.json, else the repo copy
    python reports/followup_gaps.py --notes PATH    # explicit file
    python reports/followup_gaps.py --stale-days 7 --format json

Next step is read from BOTH shapes: today's n.next (a date string, also what the board's follow-up
and Callback chips write) and the after-yes panel's n.nextStep = {"date": "YYYY-MM-DD", "what": ...}.
Last touch = newest of n.touches[].d, n.dials[].d and n.stageAt values (morning-batch machine sends do
not count, same rule as deal_desk.py). Held out: opt-outs, wrong owner, and terminal/dead statuses.
"""
import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# Ladder order, "Called - talked" or later. Callback / Gatekeeper are the holding stages.
OPEN_STAGES = ['Called - talked', 'Door - spoke', 'Contacted', 'Callback', 'Gatekeeper', 'Appointment', 'Offer made',
               'Under contract', 'Assigned']
DONE_STAGES = ['CLOSED']          # reported, but a missing next step is not a gap
NEEDS_DATE = set(OPEN_STAGES)


def _day(s):
    s = str(s or '')[:10]
    for fmt in ('%Y-%m-%d', '%m/%d/%Y'):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def next_step(n):
    """Latest dated next step on a note, from n.nextStep.date or n.next. None if there is none."""
    ds = []
    ns = n.get('nextStep')
    if isinstance(ns, dict):
        ds.append(_day(ns.get('date')))
    ds.append(_day(n.get('next')))
    ds = [d for d in ds if d]
    return max(ds) if ds else None


def last_touch(n):
    best = None
    for t in (n.get('touches') or []):
        if not isinstance(t, dict) or 'morning batch' in str(t.get('out') or '').lower():
            continue
        d = _day(t.get('d') or str(t.get('ts') or '')[:10])
        if d and (best is None or d > best):
            best = d
    for t in (n.get('dials') or []):
        if not isinstance(t, dict) or t.get('oc') in ('pending', 'redial'):
            continue
        d = _day(t.get('d') or str(t.get('ts') or '')[:10])
        if d and (best is None or d > best):
            best = d
    sa = n.get('stageAt')
    if isinstance(sa, dict):
        for v in sa.values():
            d = _day(v)
            if d and (best is None or d > best):
                best = d
    return best


def analyse(notes, today=None, stale_days=7):
    today = today or dt.date.today()
    res = {'today': today.isoformat(), 'stale_days': stale_days, 'by_stage': {}, 'no_next_step': [],
           'overdue': [], 'scheduled': [], 'stale_no_next_step': [], 'never_touched': []}
    for case, n in (notes or {}).items():
        if case[:1] in ('@', '#') or not isinstance(n, dict):
            continue
        st = str(n.get('status') or '').strip()
        if st not in OPEN_STAGES and st not in DONE_STAGES:
            continue
        if n.get('optout') or n.get('wrongown'):
            continue
        b = res['by_stage'].setdefault(st, {'leads': 0, 'no_next_step': 0, 'overdue': 0, 'scheduled': 0})
        b['leads'] += 1
        if st in DONE_STAGES:
            continue
        nx, lt = next_step(n), last_touch(n)
        if lt is None:
            res['never_touched'].append(case)
        if nx is None:
            b['no_next_step'] += 1
            res['no_next_step'].append(case)
            if lt is None or (today - lt).days >= stale_days:
                res['stale_no_next_step'].append(case)
        elif nx < today:
            b['overdue'] += 1
            res['overdue'].append((case, (today - nx).days))
        else:
            b['scheduled'] += 1
            res['scheduled'].append(case)
    res['overdue'].sort(key=lambda x: -x[1])
    return res


def default_notes_path():
    try:
        import paths as P
        p = os.path.join(P.DEALFLOW_DIR, 'worker_notes.json')
        if os.path.exists(p):
            return p
    except Exception:
        pass
    return os.path.join(os.path.dirname(HERE), 'worker_notes.json')


def render(r):
    L = ['follow-up gaps as of %s (stale = no touch in %d+ days)' % (r['today'], r['stale_days'])]
    for st in OPEN_STAGES + DONE_STAGES:
        b = r['by_stage'].get(st)
        if b:
            L.append('  %-16s leads %-4d no next step %-4d overdue %-4d scheduled %d' % (
                st, b['leads'], b['no_next_step'], b['overdue'], b['scheduled']))
    L.append('open leads with NO dated next step: %d (of which stale or never touched: %d)' % (
        len(r['no_next_step']), len(r['stale_no_next_step'])))
    L.append('open leads with next step already overdue: %d' % len(r['overdue']))
    L.append('stale/never-touched, no next step (case ids): ' + ', '.join(r['stale_no_next_step']))
    L.append('overdue (case:days late): ' + ', '.join('%s:%d' % x for x in r['overdue']))
    return '\n'.join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--notes')
    ap.add_argument('--stale-days', type=int, default=7)
    ap.add_argument('--format', choices=('text', 'json'), default='text')
    a = ap.parse_args(argv)
    with open(a.notes or default_notes_path(), encoding='utf-8') as f:
        data = json.load(f)
    notes = data.get('notes', data) if isinstance(data, dict) else {}
    r = analyse(notes, stale_days=a.stale_days)
    print(json.dumps(r, indent=1) if a.format == 'json' else render(r))


if __name__ == '__main__':
    main()
