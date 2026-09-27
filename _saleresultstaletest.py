#!/usr/bin/env python3
"""_saleresultstaletest.py — AR1: sale_results.load_for_board() flags stale results instead of
hiding them.

Before AR1 every verdict older than MAX_AGE_DAYS vanished from the board, so a sale the docket had
already recorded as held or cancelled showed nothing the night the read failed, and a passed sale
never read at all looked exactly like "nothing happened". Now a decisive verdict up to
STALE_SHOW_DAYS old ships with `stale` (its age in days), a passed Miami-Dade sale with no current
verdict ships st='unread', and volatile verdicts (at risk / unknown) still hide when old.
All case numbers and dates are invented.

Run: python _saleresultstaletest.py
"""
import datetime
import json
import os
import re
import sys
import tempfile

import sale_results as S

D = datetime.date
FAIL = []


def check(name, cond, got=None):
    if not cond:
        FAIL.append(name)
        print('FAIL  %s  -> %r' % (name, got))
    else:
        print('ok    %s' % name)


TODAY = D(2026, 9, 25)
RES = {
    # decisive, read 6 days ago -> ships, stale = 6
    '2025-000101-CA-01': {'st': 'held', 'sale': '2026-09-18', 'd': '2026-09-18', 'ts': '2026-09-19',
                          'bid': 250100.0, 'pl': False, 'sale_outcome': 'sold', 'why': 'certificate of sale'},
    # decisive, read today -> ships, no stale key
    '2025-000102-CA-01': {'st': 'cancelled', 'sale': '2026-09-24', 'd': '2026-09-23', 'ts': '2026-09-25'},
    # decisive but 20 days old -> past STALE_SHOW_DAYS; sale passed -> unread
    '2025-000103-CA-01': {'st': 'held', 'sale': '2026-09-20', 'ts': '2026-09-05'},
    # volatile, read 5 days ago, sale still ahead -> hidden (unchanged behaviour)
    '2025-000104-CA-01': {'st': 'at_risk', 'sale': '2026-09-29', 'ts': '2026-09-20'},
    # volatile, read 5 days ago, sale passed -> unread, stale = 5
    '2025-000105-CA-01': {'st': 'at_risk', 'sale': '2026-09-22', 'ts': '2026-09-20'},
    # verdict about a DIFFERENT sale date -> ignored; this sale passed -> unread 'never'
    '2025-000106-CA-01': {'st': 'held', 'sale': '2026-08-01', 'ts': '2026-08-02'},
    # decisive reset, 5 days old, moved later -> still moves the clock, flagged stale
    '2025-000107-CA-01': {'st': 'reset', 'sale': '2026-09-21', 'nd': '2026-11-02', 'ts': '2026-09-20'},
    # non-Miami-Dade case never gets an 'unread' claim
    'CACE-25-000108': {'st': 'at_risk', 'sale': '2026-09-22', 'ts': '2026-09-10'},
}
ROWS = [
    {'case': '2025-000101-CA-01', 'auction': '09/18/2026'},
    {'case': '2025-000102-CA-01', 'auction': '09/24/2026'},
    {'case': '2025-000103-CA-01', 'auction': '09/20/2026'},
    {'case': '2025-000104-CA-01', 'auction': '09/29/2026'},
    {'case': '2025-000105-CA-01', 'auction': '09/22/2026'},
    {'case': '2025-000106-CA-01', 'auction': '09/23/2026'},
    {'case': '2025-000107-CA-01', 'auction': '09/21/2026'},
    {'case': 'CACE-25-000108', 'auction': '09/22/2026'},
    {'case': '2025-000109-CA-01', 'auction': '09/24/2026'},    # passed, never read at all
    {'case': '2025-000110-CA-01', 'auction': '10/14/2026'},    # ahead, never read -> nothing
    {'case': '2025-000111-CA-01', 'auction': '09/01/2026'},    # passed, but outside PAST_DAYS
    {'case': '2025-000112-CA-01', 'auction': ''},              # no date at all
]

fd, fp = tempfile.mkstemp(suffix='.json'); os.close(fd)
json.dump(RES, open(fp, 'w'))
n = S.load_for_board(ROWS, fp, today=TODAY)
by = {r['case']: r for r in ROWS}

# decisive + stale
sr = by['2025-000101-CA-01'].get('sr') or {}
check('decisive verdict 6 days old still ships', sr.get('st') == 'held', sr)
check('...and carries stale = 6', sr.get('stale') == 6, sr)
check('...bid / plaintiff flag / outcome ride along', sr.get('bid') == 250100.0 and sr.get('pl') is False
      and sr.get('sale_outcome') == 'sold', sr)
sr = by['2025-000102-CA-01'].get('sr') or {}
check('fresh decisive verdict has no stale key', sr.get('st') == 'cancelled' and 'stale' not in sr, sr)
sr = by['2025-000103-CA-01'].get('sr') or {}
check('decisive verdict past STALE_SHOW_DAYS is not shown as a result', sr.get('st') == 'unread', sr)
check('...the unread flag says how old the last read is', sr.get('stale') == 20, sr)

# volatile
check('volatile at-risk 5 days old, sale ahead: hidden (as before)', 'sr' not in by['2025-000104-CA-01'],
      by['2025-000104-CA-01'])
sr = by['2025-000105-CA-01'].get('sr') or {}
check('volatile at-risk 5 days old, sale passed: unread, not "at risk"', sr.get('st') == 'unread'
      and sr.get('stale') == 5, sr)
check('...why names the last read date', '2026-09-20' in (sr.get('why') or ''), sr)

# other sale date / never read
sr = by['2025-000106-CA-01'].get('sr') or {}
check('verdict for another sale date never describes this one', sr.get('st') == 'unread'
      and sr.get('stale') == 'never', sr)
sr = by['2025-000109-CA-01'].get('sr') or {}
check('passed sale never read: unread / never', sr.get('st') == 'unread' and sr.get('stale') == 'never'
      and sr.get('sale') == '2026-09-24', sr)
check('upcoming sale never read: no claim', 'sr' not in by['2025-000110-CA-01'], by['2025-000110-CA-01'])
check('passed sale outside the PAST_DAYS window: no claim', 'sr' not in by['2025-000111-CA-01'],
      by['2025-000111-CA-01'])
check('row with no auction date: untouched', 'sr' not in by['2025-000112-CA-01'], by['2025-000112-CA-01'])
check('non-Miami-Dade case never gets an unread claim', 'sr' not in by['CACE-25-000108'], by['CACE-25-000108'])

# reset + stale
r7 = by['2025-000107-CA-01']
check('stale decisive reset still moves the clock', r7['auction'] == '11/02/2026'
      and (r7.get('sr') or {}).get('was') == '2026-09-21', r7)
check('...and says the read is 5 days old', (r7.get('sr') or {}).get('stale') == 5, r7)

check('count = 7 rows carry sr', n == 7, n)

# no results file at all: no claims (the reader never ran on this machine)
rows2 = [{'case': '2025-000109-CA-01', 'auction': '09/24/2026'}]
os.remove(fp)
check('missing results file: no unread claims', S.load_for_board(rows2, fp, today=TODAY) == 0
      and 'sr' not in rows2[0], rows2)

# board + phone surface the new state and the age
here = os.path.dirname(os.path.abspath(__file__))
tpl = open(os.path.join(here, 'tracker_template.html'), encoding='utf-8').read()
cm = open(os.path.join(here, 'call_mode.py'), encoding='utf-8').read()
check('board label knows st=unread', "unread:'RESULT UNREAD" in tpl)
check('board label appends the read age for stale decisive verdicts', "' · read '+sr.stale+'d ago'" in tpl)
check('board tooltip explains STALE', "'. STALE: '" in tpl)
check('phone keeps stale + sale when it slims sr', re.search(r"'ev', 'stale', 'sale'\)", cm) is not None)
check('phone label knows st=unread', "unread:'result UNREAD, sale passed'" in cm)

print()
if FAIL:
    print('%d FAILED: %s' % (len(FAIL), ', '.join(FAIL)))
    sys.exit(1)
print('all passed')
