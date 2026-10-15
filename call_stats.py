"""call_stats.py - daily Call Mode stats from worker_notes.json, counts only (no names, phones, addresses).

    python call_stats.py                  # last 14 days, CSV, every seat
    python call_stats.py --days 1         # today only
    python call_stats.py --seat Alex      # one seat (rows with no seat stamp are kept)
    python call_stats.py --format text    # CSV under a one-paragraph definition note (paste into an AI)
    python call_stats.py --format json
    python call_stats.py --notes PATH --out stats.csv

The twin of callStatsRows() in call_mode.py (the "call stats" screen). _callstatstest.py runs both on one
fixture. Definitions are in NOTE below. Talk time is not tracked; dial_to_log_min is the minutes from the
dial tap to the outcome tap on answered calls.
"""
import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ANSWERED = {'talked', 'callback', 'appt', 'notint', 'dnc', 'gate'}
NOANS = {'noanswer', 'voicemail'}
BAD = {'badnum', 'wrong'}
COLS = ['date', 'seat', 'total_dials', 'answered', 'no_answer_vm', 'wrong_or_disconnected', 'appointments',
        'no_outcome_tapped', 'other', 'dial_to_log_min', 'timed_calls', 'answer_rate', 'appt_rate']
NOTE = ('DealFlow Call Mode daily stats. total_dials = logged outcomes + dials with no outcome tapped. '
        'answered = a person picked up (talked, callback, appointment, soft no, hard no, not the owner). '
        'wrong_or_disconnected = wrong number + bad number. answer_rate and appt_rate = over logged dials '
        '(total_dials minus no_outcome_tapped). Talk time is not tracked: dial_to_log_min is the minutes from '
        'the dial tap to the outcome tap on answered calls (includes ringing), timed_calls says how many had it.')


def rows_from_notes(notes, who=None, days=None, today=None):
    cutoff = ''
    if days:
        cutoff = ((today or dt.date.today()) - dt.timedelta(days=days - 1)).isoformat()
    by = {}
    for k, n in (notes or {}).items():
        if k[:1] in ('@', '#') or not isinstance(n, dict):
            continue
        dl = n.get('dials') or []
        for i, d in enumerate(dl):
            if not isinstance(d, dict) or not d.get('d'):
                continue
            day, seat, oc = d['d'], d.get('by') or '', d.get('oc') or ''
            if cutoff and day < cutoff:
                continue
            if who and seat and seat != who:
                continue
            r = by.setdefault((day, seat), dict(date=day, seat=seat, dials=0, answered=0, no_answer_vm=0,
                              wrong_or_disconnected=0, appointments=0, no_outcome=0, other=0,
                              dial_to_log_min=0.0, timed_calls=0))
            if oc in ('pending', 'redial'):
                if not any(isinstance(x, dict) and x.get('d') == day and x.get('oc') not in ('pending', 'redial')
                           for x in dl[i + 1:]):
                    r['dials'] += 1
                    r['no_outcome'] += 1
                continue
            r['dials'] += 1
            if oc in ANSWERED:
                r['answered'] += 1
                try:
                    g = (float(d.get('tsu')) - float(d.get('tsd'))) / 1000.0
                except (TypeError, ValueError):
                    g = 0
                if d.get('tsd') and 0 < g <= 3600:
                    r['dial_to_log_min'] += g / 60.0
                    r['timed_calls'] += 1
            elif oc in NOANS:
                r['no_answer_vm'] += 1
            elif oc in BAD:
                r['wrong_or_disconnected'] += 1
            else:
                r['other'] += 1
            if oc == 'appt':
                r['appointments'] += 1
    return sorted(by.values(), key=lambda r: (-int(r['date'].replace('-', '') or 0), r['seat']))


def to_csv(rows):
    out = [','.join(COLS)]
    for r in rows:
        logged = r['dials'] - r['no_outcome']
        out.append(','.join(str(x) for x in [
            r['date'], r['seat'], r['dials'], r['answered'], r['no_answer_vm'], r['wrong_or_disconnected'],
            r['appointments'], r['no_outcome'], r['other'], '%.1f' % (int(r['dial_to_log_min'] * 10 + 0.5) / 10.0),
            r['timed_calls'], '%.3f' % (r['answered'] / logged) if logged else '',
            '%.3f' % (r['appointments'] / logged) if logged else '']))
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--seat')
    ap.add_argument('--format', choices=('csv', 'text', 'json'), default='csv')
    ap.add_argument('--notes', default=os.path.join(HERE, 'worker_notes.json'))
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    try:
        with open(a.notes, encoding='utf-8') as f:
            wn = json.load(f)
    except Exception as e:
        print('call_stats: cannot read %s (%s)' % (a.notes, e.__class__.__name__), file=sys.stderr)
        return 2
    rows = rows_from_notes(wn.get('notes') if isinstance(wn, dict) and 'notes' in wn else wn, a.seat, a.days)
    body = json.dumps(rows, indent=1) if a.format == 'json' else (
        NOTE + '\n\n' + to_csv(rows) if a.format == 'text' else to_csv(rows))
    if a.out:
        with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(body + '\n')
    else:
        print(body)
    return 0


if __name__ == '__main__':
    sys.exit(main())
