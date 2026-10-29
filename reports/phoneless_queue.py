"""reports/phoneless_queue.py -- READ-ONLY. How many leads have no dialable phone, and in what
order a paid lookup should take them. It runs NO lookup, spends nothing, writes nothing.

"Dialable" is not redefined here. It is Call Mode's own definition: call_mode.call_rows(lookup=[])
puts a lead on that sink when it passes EVERY gate (opt-out, dead, stay, federal hold, diligence,
equity floor, sale window, vacant land ...) and has no number left after call_rows drops DNC,
agent/shared-office, dead-line and malformed numbers. A lead whose own numbers are all do-not-call is
NOT on the sink (finding another number for that person is not a way around the flag). So this
report counts exactly the "Find a number" list the phone shows. Leads that no gate lets through are
counted separately as `gated` and are never queued.

Counts and case ids only: no names, no addresses, no phone numbers.

    python reports/phoneless_queue.py                   # reads the board twin (paths.P.TWIN)
    python reports/phoneless_queue.py --input rows.json # a JSON list of board rows, or a twin .html
    python reports/phoneless_queue.py --top 100         # case ids printed in lookup order

Order a paid lookup should take (all key parts are listed in the output):
  1. days to sale: <=14, 15-30, 31-60, 61+ or no sale date (a lis pendens), soonest bucket first
  2. equity band: 50%+, 25-50%, 0-25%, unknown, negative (unknown still beats a known loss)
  3. then fewer days, then case id, so two runs on the same input print the same order
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import call_mode  # noqa: E402

DAY_BUCKETS = (('<=14d', 0, 14), ('15-30d', 15, 30), ('31-60d', 31, 60), ('61d+/none', 61, 10 ** 9))
EQ_BANDS = ('50%+', '25-50%', '0-25%', 'unknown', 'negative')


def read_rows(path):
    """A JSON list of board rows, or the plaintext twin (RAW = [...])."""
    text = open(path, encoding='utf-8').read()
    if path.lower().endswith('.html'):
        i = text.find('RAW = ')
        if i < 0:
            raise SystemExit('no RAW payload in %s' % path)
        rows, _ = json.JSONDecoder().raw_decode(text, i + len('RAW = '))
    else:
        rows = json.loads(text)
    if not isinstance(rows, list):
        raise SystemExit('input is not a list of rows')
    return rows


def day_bucket(days):
    for i, (_n, lo, hi) in enumerate(DAY_BUCKETS):
        if lo <= days <= hi:
            return i
    return len(DAY_BUCKETS) - 1


def eq_band(eq):
    if eq is None:
        return 'unknown'
    if eq < 0:
        return 'negative'
    if eq >= 50:
        return '50%+'
    if eq >= 25:
        return '25-50%'
    return '0-25%'


def _days(d):
    try:
        v = int(d.get('days'))
    except (TypeError, ValueError):
        return 9999
    return v if v >= 0 else 9999


def analyse(rows, optouts=None, deads=None):
    """-> dict of counts and the ordered case list. Pure: no I/O."""
    lk = []
    dial, _total = call_mode.call_rows(rows, optouts or {}, deads or {}, lookup=lk, cap=10 ** 6)
    by_case = {r.get('case'): r for r in rows}
    queue = []
    for x in lk:
        d = by_case.get(x['c']) or {}
        days = _days(d)
        band = eq_band(call_mode._call_equity(d))
        queue.append({'c': x['c'],
                      'county': (str(d.get('county') or 'MIAMI-DADE').strip().upper() or 'MIAMI-DADE'),
                      'type': ('LP' if d.get('st') == 'LP' else
                               ('BAL' if d.get('st') == 'BAL' else (str(d.get('ftype') or 'FC').upper() or 'FC'))),
                      'days': days, 'dbucket': day_bucket(days), 'band': band})
    queue.sort(key=lambda q: (q['dbucket'], EQ_BANDS.index(q['band']), q['days'], q['c']))
    n_dial = len(dial)
    return {'rows': len(rows), 'dialable': n_dial, 'phoneless': len(queue),
            'gated': len(rows) - n_dial - len(queue), 'queue': queue}


def _count(queue, key):
    out = {}
    for q in queue:
        out[q[key]] = out.get(q[key], 0) + 1
    return out


def render(res, top=50):
    q = res['queue']
    lines = ['phoneless queue (read-only; no lookup was run)',
             'rows=%d dialable=%d phoneless=%d gated-out=%d' % (
                 res['rows'], res['dialable'], res['phoneless'], res['gated'])]
    for title, key in (('by county', 'county'), ('by case type', 'type')):
        c = _count(q, key)
        lines.append('%s: %s' % (title, ', '.join('%s=%d' % (k, c[k]) for k in sorted(c)) or 'none'))
    b = _count([dict(x, bn=DAY_BUCKETS[x['dbucket']][0]) for x in q], 'bn')
    lines.append('by days to sale: ' + ', '.join('%s=%d' % (n, b.get(n, 0)) for n, _l, _h in DAY_BUCKETS))
    e = _count(q, 'band')
    lines.append('by equity band: ' + ', '.join('%s=%d' % (n, e.get(n, 0)) for n in EQ_BANDS))
    lines.append('lookup order (days bucket, then equity band, then days, then case id):')
    for n, q_ in enumerate(q[:top], 1):
        lines.append('%4d  %s  %s  %s  %sd  eq %s' % (
            n, q_['c'], q_['county'][:2], q_['type'], q_['days'] if q_['days'] < 9999 else '-', q_['band']))
    if len(q) > top:
        lines.append('... %d more (use --top)' % (len(q) - top))
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--input', help='JSON list of board rows, or the plaintext twin .html')
    ap.add_argument('--optouts', help='optouts.json (default: DEALFLOW_DIR/optouts.json if present)')
    ap.add_argument('--top', type=int, default=50)
    args = ap.parse_args(argv)
    import paths as P
    path = args.input or P.TWIN
    rows = read_rows(path)
    optouts = {}
    op = args.optouts or os.path.join(P.DEALFLOW_DIR, 'optouts.json')
    if os.path.exists(op):
        try:
            data = json.load(open(op, encoding='utf-8'))
            optouts = {k: v for k, v in data.items() if v} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            print('optouts.json unreadable: refusing to print counts that are not suppression-checked',
                  file=sys.stderr)
            return 2
    else:
        print('no optouts.json found: counts below are NOT suppression-checked', file=sys.stderr)
    print(render(analyse(rows, optouts), args.top))
    return 0


if __name__ == '__main__':
    sys.exit(main())
