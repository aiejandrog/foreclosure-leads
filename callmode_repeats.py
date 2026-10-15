"""callmode_repeats.py — read-only census of REPEATS in Call Mode. Counts only, no names, no numbers.

Alejandro, 2026-10-15: "i still feel like im getting some repeats altho they are split with carlos".
Three things read as a repeat on the phone, and this separates them:

  A. One household on BOTH seats. The seat bucket used to be a hash of the case number alone, so a
     person with two cases, two owners of one house under different person keys, or two rows with
     the same phone could be split across Alejandro and Carlos. `call_mode._seat_by_household` now
     groups those; this prints the count under the old case-only split and under the current one.
  B. One household twice on ONE seat (extra rows for the same person / street line / phone).
  C. Leads already worked: a call in worker_notes.json, or a confirmed email or text in the ledgers.

Plus where the list comes from (county, auction vs lis pendens, how recently filed), so "there
should be way more leads" can be read against what actually flows in.

Reads P.TWIN (plaintext board), optouts.json, deads.json, worker_notes.json, mail_sent.json,
text_sent.json. Writes nothing. No network. Run:  python callmode_repeats.py
"""
import collections
import datetime as dt
import json
import os
import re
import sys

import call_mode as CM
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, default):
    try:
        with open(os.path.join(HERE, name), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def board_rows(path=None):
    h = open(path or P.TWIN, encoding='utf-8').read()
    i = h.find('RAW = ')
    if i < 0:
        raise SystemExit('callmode_repeats: no RAW array in the board twin')
    rows, _ = json.JSONDecoder().raw_decode(h, i + len('RAW = '))
    return [r for r in rows if isinstance(r, dict)]


def _optouts():
    raw = _load('optouts.json', {}) or {}
    notes = raw.get('notes') if isinstance(raw, dict) and isinstance(raw.get('notes'), dict) else raw
    return {k: v for k, v in (notes or {}).items() if isinstance(v, dict)}


def _deads():
    out = {}
    for c, n in (_load('deads.json', {}) or {}).items():
        if isinstance(n, dict) and str(n.get('status') or '').upper() in ('DEAD', 'CLOSED', 'LOST - SOLD AT AUCTION'):
            out[c] = {'status': 'Dead'}
    return out


def _groups(rows, keyf):
    g = collections.defaultdict(list)
    for r in rows:
        for k in keyf(r):
            g[k].append(r)
    return {k: v for k, v in g.items() if len({x['c'] for x in v}) > 1}


def household_census(rows, seats=2):
    """-> dict of counts for one row set whose 'sb' is already stamped."""
    seat = lambda r: r.get('sb', 0) % seats
    out = {}
    for label, prefix in (('person', 'k:'), ('address', 'a:'), ('phone', 'p:')):
        multi = _groups(rows, lambda r, p=prefix: [k for k in CM._seat_links(r) if k.startswith(p)])
        out[label] = {
            'groups': len(multi),
            'extra_rows': sum(len({x['c'] for x in v}) - 1 for v in multi.values()),
            'on_both_seats': sum(1 for v in multi.values() if len({seat(x) for x in v}) > 1),
            'different_person_key': sum(1 for v in multi.values() if len({x.get('pk') for x in v}) > 1),
        }
    return out


def _last_call_ms(n):
    best = 0
    for t in (n or {}).get('touches') or []:
        if isinstance(t, dict) and (t.get('ch') or '') == 'call':
            try:
                best = max(best, int(t.get('tsu') or 0))
            except (TypeError, ValueError):
                pass
    for d in (n or {}).get('dials') or []:
        if isinstance(d, dict):
            try:
                best = max(best, int(d.get('tsu') or 0))
            except (TypeError, ValueError):
                pass
    return best


def _mail_last():
    out = {}
    for r in _load('mail_sent.json', []) or []:
        if not isinstance(r, dict) or r.get('ch') != 'email' or not r.get('message_id') or not r.get('case'):
            continue
        out[r['case']] = True
    return out


def _text_cases():
    rows = _load('text_sent.json', []) or []
    out = set()
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get('ch') == 'text' and r.get('case') and r.get('confirmed'):
            out.add(str(r['case']).strip())
    return out


def _filed_age(d, today):
    s = str(d.get('filedDate') or '').strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return (today - dt.datetime.strptime(s, fmt).date()).days
        except ValueError:
            pass
    return None


def main():
    today = dt.date.today()
    slim = board_rows()
    rows, total = CM.call_rows(slim, optouts=_optouts(), deads=_deads(), cap=10 ** 6)
    print('CALL MODE REPEATS  %s  (board twin %s)' % (today, dt.datetime.fromtimestamp(os.path.getmtime(P.TWIN)).strftime('%Y-%m-%d %H:%M')))
    print('board rows %d   Call Mode rows %d (qualifying %d)' % (len(slim), len(rows), total))
    m = re.search(r'TOTAL=(\d+)', open(os.path.join(HERE, 'docs', 'call', 'index.html'), encoding='utf-8', errors='ignore').read()) \
        if os.path.exists(os.path.join(HERE, 'docs', 'call', 'index.html')) else None
    print('live page TOTAL %s (should be close; this run uses case-keyed opt-outs only)' % (m.group(1) if m else '?'))

    seats = len([s for s in CM.CALL_SEATS if s]) or 1
    new_sb = {id(r): r.get('sb', 0) for r in rows}
    for r in rows:                                   # the split as it was: case number alone
        r['sb'] = CM._seat_bucket(r['c'])
    old = household_census(rows, seats)
    old_seat = collections.Counter(r['sb'] % seats for r in rows)
    for r in rows:
        r['sb'] = new_sb[id(r)]
    new = household_census(rows, seats)
    new_seat = collections.Counter(r['sb'] % seats for r in rows)
    print('\nA/B. SAME HOUSEHOLD, MORE THAN ONE ROW  (case-only split -> household split)')
    for k in ('person', 'address', 'phone'):
        o, n = old[k], new[k]
        print('   %-8s groups %4d  extra rows %4d  on BOTH callers %4d -> %d   (groups with 2+ person keys %d)'
              % (k, o['groups'], o['extra_rows'], o['on_both_seats'], n['on_both_seats'], o['different_person_key']))
    print('   rows per caller: before %s   after %s' % (dict(sorted(old_seat.items())), dict(sorted(new_seat.items()))))

    notes = (_load('worker_notes.json', {}) or {}).get('notes') or {}
    mail, texts = _mail_last(), _text_cases()
    now_ms = dt.datetime.now().timestamp() * 1000
    c = collections.Counter()
    for r in rows:
        cs = [r['c']] + list(r.get('pcs') or [])
        lc = max([_last_call_ms(notes.get(x)) for x in cs] or [0])
        called = lc > 0
        c['called ever'] += called
        if called and now_ms - lc < 3 * 86400000:
            c['called in last 3 days'] += 1
        if called and now_ms - lc < 14 * 86400000:
            c['called in last 14 days'] += 1
        c['emailed (confirmed)'] += any(x in mail for x in cs)
        c['texted (confirmed)'] += any(x in texts for x in cs)
        c['never touched by call, email or text'] += not (called or any(x in mail or x in texts for x in cs))
    print('\nC. ALREADY WORKED  (of %d Call Mode rows; worker_notes %s)' % (
        len(rows), 'present' if notes else 'MISSING - call counts are 0'))
    for k in ('called ever', 'called in last 3 days', 'called in last 14 days', 'emailed (confirmed)',
              'texted (confirmed)', 'never touched by call, email or text'):
        print('   %-40s %5d' % (k, c[k]))

    by_case = {d.get('case'): d for d in slim if d.get('case')}
    lane = collections.Counter()
    age = collections.Counter()
    for r in rows:
        d = by_case.get(r['c']) or {}
        co = str(d.get('county') or '?').title()
        lane[(co, 'lis pendens' if d.get('st') == 'LP' else ('tax deed' if d.get('st') == 'TD' else 'auction'))] += 1
        if d.get('st') == 'LP':
            a = _filed_age(d, today)
            age[('<=7d' if a is not None and a <= 7 else '8-30d' if a is not None and a <= 30 else
                 '31-90d' if a is not None and a <= 90 else '>90d' if a is not None else 'no date')] += 1
    print('\nD. WHERE THE CALL MODE ROWS COME FROM')
    for (co, ln), n in sorted(lane.items()):
        print('   %-12s %-12s %5d' % (co, ln, n))
    print('   lis pendens rows by filing age: %s' % ', '.join('%s %d' % (k, age[k]) for k in ('<=7d', '8-30d', '31-90d', '>90d', 'no date')))

    def has_phone(d):
        return any(re.sub(r'\D', '', str(p)) for p in d.get('phones') or [])
    inmode = {r['c'] for r in rows}
    county = collections.defaultdict(collections.Counter)
    for d in slim:
        co = str(d.get('county') or '?').title()
        county[co]['leads'] += 1
        county[co]['with a phone'] += has_phone(d)
        county[co]['in Call Mode'] += d.get('case') in inmode
        if has_phone(d) and d.get('case') not in inmode:
            county[co]['phone but not in Call Mode'] += 1
    print('\nE. BOARD -> CALL MODE BY COUNTY')
    for co in sorted(county):
        k = county[co]
        print('   %-12s leads %5d  with a phone %5d  in Call Mode %5d  phone but held/cut %5d'
              % (co, k['leads'], k['with a phone'], k['in Call Mode'], k['phone but not in Call Mode']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
