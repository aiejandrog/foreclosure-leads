"""call_stats.py - daily Call Mode stats from worker_notes.json, counts only (no names, phones, addresses).

    python call_stats.py                  # last 14 days, CSV, every seat
    python call_stats.py --days 1         # today only
    python call_stats.py --seat Alex      # one seat (rows with no seat stamp are kept)
    python call_stats.py --format text    # CSV under a one-paragraph definition note (paste into an AI)
    python call_stats.py --format json
    python call_stats.py --notes PATH --out stats.csv
    python call_stats.py --week           # this week and last week, Monday to Sunday (weekly scorecard)
    python call_stats.py --week --format csv

The twin of callStatsRows() in call_mode.py (the "call stats" screen). _callstatstest.py runs both on one
fixture. Definitions are in NOTE below. dial_to_log_min is the minutes from the dial tap to the outcome tap
on answered calls. Talk time (talk_min, talked_calls, avg_talk_min) comes only from the `talk` seconds the
page writes when "They picked up" was tapped during an answered call (2026-11-05).
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
        'no_outcome_tapped', 'other', 'dial_to_log_min', 'timed_calls', 'talk_min', 'talked_calls', 'avg_talk_min',
        'answer_rate', 'appt_rate']
NOTE = ('DealFlow Call Mode daily stats. total_dials = logged outcomes + dials with no outcome tapped. '
        'answered = a person picked up (talked, callback, appointment, soft no, hard no, not the owner). '
        'wrong_or_disconnected = wrong number + bad number. answer_rate and appt_rate = over logged dials '
        '(total_dials minus no_outcome_tapped). dial_to_log_min is the minutes from the dial tap to the outcome '
        'tap on answered calls (includes ringing), timed_calls says how many had it. Talk time is tracked only '
        'when \'They picked up\' is tapped during the call: talk_min is the sum of talk seconds on answered calls '
        'with a pickup tap, talked_calls is how many, avg_talk_min is its average.')


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
                              dial_to_log_min=0.0, timed_calls=0, talk_s=0, talked_calls=0))
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
                tk_raw = d.get('talk')
                try:
                    tk = float(tk_raw)
                except (TypeError, ValueError):
                    tk = 0
                if tk_raw is not None and 0 < tk <= 3600 and tk == int(tk):
                    r['talk_s'] += int(tk)
                    r['talked_calls'] += 1
            elif oc in NOANS:
                r['no_answer_vm'] += 1
            elif oc in BAD:
                r['wrong_or_disconnected'] += 1
            else:
                r['other'] += 1
            if oc == 'appt':
                r['appointments'] += 1
    return sorted(by.values(), key=lambda r: (-int(r['date'].replace('-', '') or 0), r['seat']))


# WEEKLY SCORECARD (2026-11-11). Twin of weekStats()/weekText() in call_mode.py ("call stats" screen,
# THIS WEEK band). _callstatstest.py runs both on one fixture. Counts only.
REACHED = {'talked', 'callback', 'appt'}
WEEK_TARGETS = {'dials': 625, 'doors': 10, 'letters': 10}
WEEK_COLS = ['dials', 'conversations', 'appointments', 'letters', 'doors', 'offers', 'contracts', 'closed']
WEEK_NOTE = ('DealFlow weekly scorecard, Monday to Sunday, counts only. dials = logged outcomes plus dials with no '
             'outcome tapped. conversations = talked, callback or appointment on the owner\'s line (not a gatekeeper\'s callback). letters / doors = '
             'letters marked mailed and doors knocked. offers / contracts / closed = leads that reached that stage '
             'this week (team total, no seat). Ratios are shown only when the lower number is at least 10.')


def week_stats(notes, today=None):
    t = today or dt.date.today()
    w0 = t - dt.timedelta(days=t.weekday())
    w1, w_end = w0 - dt.timedelta(days=7), w0 + dt.timedelta(days=7)
    w0s, w1s, wes = w0.isoformat(), w1.isoformat(), w_end.isoformat()

    def blank():
        return {f: 0 for f in WEEK_COLS}
    seats = {}

    def cell(seat, wk):
        return seats.setdefault(seat, {'cur': blank(), 'prev': blank()})[wk]

    def wk_of(d):
        if not isinstance(d, str) or not d or d < w1s or d >= wes:
            return ''
        return 'cur' if d >= w0s else 'prev'
    for k, n in (notes or {}).items():
        if k[:1] in ('@', '#') or not isinstance(n, dict):
            continue
        dl = n.get('dials') or []
        for i, d in enumerate(dl):
            if not isinstance(d, dict) or not d.get('d'):
                continue
            wk = wk_of(d['d'])
            if not wk:
                continue
            r, oc = cell(d.get('by') or '', wk), d.get('oc') or ''
            if oc in ('pending', 'redial'):
                if not any(isinstance(x, dict) and x.get('d') == d['d'] and x.get('oc') not in ('pending', 'redial')
                           for x in dl[i + 1:]):
                    r['dials'] += 1
                continue
            r['dials'] += 1
            if oc in REACHED and not d.get('gk') and not d.get('rel'):
                r['conversations'] += 1
            if oc == 'appt':
                r['appointments'] += 1
        for x in n.get('touches') or []:
            if not isinstance(x, dict) or x.get('ch') not in ('letter', 'door'):
                continue
            wk = wk_of(x.get('d'))
            if wk:
                cell(x.get('by') or '', wk)['letters' if x['ch'] == 'letter' else 'doors'] += 1
        sa = n.get('stageAt') or {}
        if isinstance(sa, dict):
            for st, f in (('Offer made', 'offers'), ('Under contract', 'contracts'), ('CLOSED', 'closed')):
                wk = wk_of(sa.get(st))
                if wk:
                    cell('', wk)[f] += 1
    tot = {'cur': blank(), 'prev': blank()}
    for s in seats.values():
        for wk in ('cur', 'prev'):
            for f in WEEK_COLS:
                tot[wk][f] += s[wk][f]
    return {'week': w0s, 'last': w1s, 'seats': seats, 'total': tot}


def week_text(W):
    def line(lab, s):
        return lab + ': ' + ', '.join('%s %d (last week %d)' % (f, s['cur'][f], s['prev'][f]) for f in WEEK_COLS)
    L = [WEEK_NOTE, '', 'Week of %s. Targets per seat: dials %d, doors %d, letters %d.' % (
        W['week'], WEEK_TARGETS['dials'], WEEK_TARGETS['doors'], WEEK_TARGETS['letters'])]
    for s in sorted(W['seats']):
        if s:
            L.append(line('Seat ' + s, W['seats'][s]))
    L.append(line('Team', W['total']))
    c, R = W['total']['cur'], []
    if c['conversations'] >= 10:
        R.append('dials per conversation %.1f' % (c['dials'] / c['conversations']))
    if c['appointments'] >= 10:
        R.append('conversations per appointment %.1f' % (c['conversations'] / c['appointments']))
    if c['contracts'] >= 10:
        R.append('appointments per contract %.1f' % (c['appointments'] / c['contracts']))
    if R:
        L.append('Ratios: ' + ', '.join(R))
    L += ['', 'Question: what is the one activity number we let slip, and what should we do about it next week? '
              'Say which of your points are facts from above and which are your guesses.']
    return '\n'.join(L)


def week_csv(W):
    out = ['week,seat,' + ','.join(WEEK_COLS)]
    for wk, lab in (('cur', W['week']), ('prev', W['last'])):
        for s in sorted(W['seats']):
            out.append(lab + ',' + s + ',' + ','.join(str(W['seats'][s][wk][f]) for f in WEEK_COLS))
        out.append(lab + ',TEAM,' + ','.join(str(W['total'][wk][f]) for f in WEEK_COLS))
    return '\n'.join(out)


def to_csv(rows):
    out = [','.join(COLS)]
    for r in rows:
        logged = r['dials'] - r['no_outcome']
        out.append(','.join(str(x) for x in [
            r['date'], r['seat'], r['dials'], r['answered'], r['no_answer_vm'], r['wrong_or_disconnected'],
            r['appointments'], r['no_outcome'], r['other'], '%.1f' % (int(r['dial_to_log_min'] * 10 + 0.5) / 10.0),
            r['timed_calls'], '%.1f' % (int(r['talk_s'] / 60.0 * 10 + 0.5) / 10.0), r['talked_calls'],
            '%.1f' % (int(r['talk_s'] / 60.0 / r['talked_calls'] * 10 + 0.5) / 10.0) if r['talked_calls'] else '',
            '%.3f' % (r['answered'] / logged) if logged else '',
            '%.3f' % (r['appointments'] / logged) if logged else '']))
    return '\n'.join(out)


def write_daily(notes_path=None, days=14):
    """Write call-stats/call-stats-<date>.csv and latest.csv (the text form, definitions on top) to
    DEALFLOW_DIR. Counts only. Returns the path. Called by the 21:00 evening task; fail-soft there."""
    import paths as P
    with open(notes_path or os.path.join(HERE, 'worker_notes.json'), encoding='utf-8') as f:
        wn = json.load(f)
    rows = rows_from_notes(wn.get('notes') if isinstance(wn, dict) and 'notes' in wn else wn, None, days)
    body = NOTE + '\n\n' + to_csv(rows) + '\n'
    day = dt.date.today().isoformat()
    d = os.path.dirname(P.out('call-stats', 'x'))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, 'call-stats-%s.csv' % day)
    for p in (path, os.path.join(d, 'latest.csv')):
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write(body)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--seat')
    ap.add_argument('--format', choices=('csv', 'text', 'json'), default=None, help='default csv; text with --week')
    ap.add_argument('--notes', default=os.path.join(HERE, 'worker_notes.json'))
    ap.add_argument('--out')
    ap.add_argument('--week', action='store_true', help='weekly scorecard: this week and last, Monday to Sunday')
    ap.add_argument('--write', action='store_true', help='write the dated file + latest.csv to DEALFLOW_DIR/call-stats')
    a = ap.parse_args(argv)
    if a.write:
        try:
            print(write_daily(a.notes, a.days))
            return 0
        except Exception as e:
            print('call_stats: write failed (%s)' % e.__class__.__name__, file=sys.stderr)
            return 2
    try:
        with open(a.notes, encoding='utf-8') as f:
            wn = json.load(f)
    except Exception as e:
        print('call_stats: cannot read %s (%s)' % (a.notes, e.__class__.__name__), file=sys.stderr)
        return 2
    nts = wn.get('notes') if isinstance(wn, dict) and 'notes' in wn else wn
    if a.week:
        W = week_stats(nts)
        body = json.dumps(W, indent=1) if a.format == 'json' else (week_csv(W) if a.format == 'csv' else week_text(W))
        if a.out:
            with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
                f.write(body + '\n')
        else:
            print(body)
        return 0
    rows = rows_from_notes(nts, a.seat, a.days)
    a.format = a.format or 'csv'
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
