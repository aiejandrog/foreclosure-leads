"""mail_followups.py - what mail is due, and the record of what actually went in the mail.

bsg_mail_variants.py builds the pieces and never records a mailing. This module is the other half:
a small log of the day each touch was mailed (mail_variants_sent.json, beside the ledger, case numbers
and dates only) and the schedule that follows from it:

    touch 1 mailed on day D  ->  SECOND NOTICE due D+7  ->  FINAL NOTICE due D+14  ->  file closes D+28

The D+28 date is the call-by date printed on the notices, so the log is also where that date comes from.

Run:
  python mail_followups.py                                   # what is due, counts only
  python mail_followups.py --mark-mailed --touch 1 [--date YYYY-MM-DD]
        every case the ledger marks in_print_file went out on that day (default: today)
  python mail_followups.py --mark-mailed --touch 2 [--date ...] [--from <notice .html>]
        the cases in that built notice file went out (default: the newest BSG_Mail_Notice2_*.html)

three_day.py and deal_desk.py print md_block() at the top of the daily sheets, so a due or overdue
mailing is on the page he already opens every morning.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

import paths as P

LEDGER = 'mail_variants.json'
SENT = 'mail_variants_sent.json'
STEP = {2: 7, 3: 14}                    # days after touch 1
CLOSE_DAYS = 28                         # touch 1 + this = the call-by date on the notices
NAME = {2: 'SECOND NOTICE', 3: 'FINAL NOTICE'}


def _day(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _read(name, default):
    try:
        with open(P.out(name), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def load_sent():
    d = _read(SENT, {})
    c = d.get('cases') if isinstance(d, dict) else None
    return c if isinstance(c, dict) else {}


def save_sent(cases):
    path = P.out(SENT)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump({'_about': 'the day each mail touch went out, by case. Written by mail_followups.py '
                             '--mark-mailed. No names, no addresses.',
                   'updated': dt.datetime.now().isoformat(timespec='seconds'), 'cases': cases}, fh, indent=1)
    os.replace(tmp, path)


def t1_date(case, sent=None):
    """The day touch 1 was mailed to this case, or None."""
    e = (load_sent() if sent is None else sent).get(case)
    return _day(e.get('t1')) if isinstance(e, dict) else None


def ledger_cases():
    d = _read(LEDGER, {})
    c = d.get('cases', d) if isinstance(d, dict) else {}
    return [k for k, v in c.items() if isinstance(v, dict) and v.get('in_print_file') is True]


def cases_in(html_path):
    with open(html_path, encoding='utf-8') as fh:
        return re.findall(r'<section class="pc pn" data-case="([^"]+)"', fh.read())


def newest_notice(touch):
    files = [f for f in glob.glob(P.out('BSG_Mail_Notice%d_*.html' % touch)) if 'SAMPLE' not in f]
    return max(files, key=os.path.getmtime) if files else ''


def mark_mailed(touch, day, src=''):
    """Record that a touch went out. Returns (cases marked, source)."""
    sent = load_sent()
    if touch == 1:
        cases, src = ledger_cases(), LEDGER
    else:
        src = src or newest_notice(touch)
        if not src or not os.path.isfile(src):
            raise SystemExit('REFUSED: no built notice file for touch %d. Build it, or pass --from.' % touch)
        cases = cases_in(src)
        missing = [c for c in cases if not t1_date(c, sent)]
        if missing:
            raise SystemExit('REFUSED: %d case(s) in that file have no touch 1 mail date. '
                             'Mark touch 1 first.' % len(missing))
    if not cases:
        raise SystemExit('REFUSED: nothing to mark (%s lists no cases).' % os.path.basename(src))
    for c in cases:
        e = sent.get(c) if isinstance(sent.get(c), dict) else {}
        e['t%d' % touch] = day.isoformat()
        sent[c] = e
    save_sent(sent)
    return len(cases), src


def schedule(today=None, sent=None):
    """One dict per touch-1 mail day: counts and the next dates. Counts only."""
    today = today or dt.date.today()
    sent = load_sent() if sent is None else sent
    by = {}
    for c, e in sent.items():
        d = t1_date(c, sent)
        if d:
            by.setdefault(d, []).append(e)
    out = []
    for d in sorted(by):
        es = by[d]
        b = {'t1': d, 'n': len(es), 'close': d + dt.timedelta(days=CLOSE_DAYS), 'steps': []}
        for t in (2, 3):
            due = d + dt.timedelta(days=STEP[t])
            done = sum(1 for e in es if _day(e.get('t%d' % t)))
            b['steps'].append({'touch': t, 'due': due, 'done': done, 'left': (due - today).days})
        b['open'] = today <= b['close'] + dt.timedelta(days=3)
        out.append(b)
    return out


def _when(n):
    return 'TODAY' if n == 0 else ('in %d day%s' % (n, '' if n == 1 else 's') if n > 0
                                   else 'OVERDUE by %d day%s' % (-n, '' if n == -1 else 's'))


def status_lines(today=None):
    today = today or dt.date.today()
    sch = schedule(today)
    if not sch:
        n = len(ledger_cases())
        return ['MAIL: %d piece(s) in the print ledger, no mail date recorded. After they go out: '
                'python mail_followups.py --mark-mailed --touch 1' % n] if n else []
    L = []
    for b in sch:
        if not b['open']:
            continue
        L.append('MAIL batch mailed %s: %d pieces. File closes %s (%s).'
                 % (b['t1'].strftime('%a %b %d'), b['n'], b['close'].strftime('%a %b %d'),
                    _when((b['close'] - today).days)))
        for s in b['steps']:
            if s['done']:
                L.append('  %s: mailed to %d.' % (NAME[s['touch']], s['done']))
            else:
                L.append('  %s: mail %s (%s). Build that morning: python bsg_mail_variants.py --touch %d '
                         '--from-ledger <ledger>, then --mark-mailed --touch %d.'
                         % (NAME[s['touch']], s['due'].strftime('%a %b %d'), _when(s['left']),
                            s['touch'], s['touch']))
    return L


def md_block(today=None):
    """Lines for the top of a daily sheet. [] when no batch is open. Never raises."""
    try:
        today = today or dt.date.today()
        hot = []
        for b in schedule(today):
            if not b['open']:
                continue
            for s in b['steps']:
                if not s['done'] and s['left'] <= 2:
                    hot.append('**MAIL %s: %s to the %s batch, %s.**'
                               % (_when(s['left']).upper() if s['left'] <= 0 else _when(s['left']),
                                  NAME[s['touch']], b['t1'].strftime('%b %d'),
                                  s['due'].strftime('%a %b %d')))
            left = (b['close'] - today).days
            if -3 <= left <= 2:
                hot.append('**MAIL: the %s batch file closes %s (%s). Tally who called.**'
                           % (b['t1'].strftime('%b %d'), b['close'].strftime('%a %b %d'), _when(left)))
        if hot:
            return hot + ['']
        plain = [x for x in status_lines(today) if x.startswith('MAIL')]
        return ([' '.join(plain[:1]) + ' Details: `python mail_followups.py`.', ''] if plain else [])
    except Exception:                       # a reminder must never break the sheet it rides on
        return []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mark-mailed', action='store_true')
    ap.add_argument('--touch', type=int, choices=(1, 2, 3), default=0)
    ap.add_argument('--date', default='', help='YYYY-MM-DD it went in the mail (default: today)')
    ap.add_argument('--from', dest='src', default='', help='the built notice .html that was mailed')
    a = ap.parse_args(argv)
    if a.mark_mailed:
        if not a.touch:
            print('REFUSED: --mark-mailed needs --touch 1, 2 or 3.')
            return 2
        day = _day(a.date) if a.date else dt.date.today()
        if not day or day > dt.date.today():
            print('REFUSED: --date must be today or earlier, YYYY-MM-DD.')
            return 2
        n, src = mark_mailed(a.touch, day, a.src)
        print('recorded touch %d as mailed %s for %d case(s) (from %s)'
              % (a.touch, day.isoformat(), n, os.path.basename(src)))
    lines = status_lines()
    print('\n'.join(lines) if lines else 'MAIL: nothing in the ledger.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
