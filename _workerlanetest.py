"""_workerlanetest -- every outreach-eligible sale date has a Morning Worker lane.

Run:  python _workerlanetest.py   (exit 0 = safe; node, no network, no browser, no board, no real data)

WHAT BROKE (found 2026-10-22, Alejandro: "make sure new leads in main board is being thrown thru
call mode as well and morning worker right?"). WORKER_LANES.active tested d>=8 && d<=45, and EARLY
takes only LP rows and rows with no sale date. So an auction-calendar lead whose sale was 46-120
days out (the calendars are scraped 120 days ahead) matched no lane at all: _laneStats did not
count it, _workerQueue never served it, and the 8am auto-run never emailed it until the sale aged
into 45 days. Call Mode dialled the same lead from day one (its '46+ days' lane, MAX_DAYS 120), and
cadence._cadence_lane / outreach_email._lane_of have called it 'active' since 2026-09-08.

HOW THIS TESTS. WORKER_LANES is extracted by name from tracker_template.html and its test functions
run under node, so the assertion is on the code that ships. Each sale-days value is checked against
the lane outreach_email._lane_of gives it (the Python sender's mirror of this table).
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def rec(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else '  -> %s' % (detail,)))
    if not ok:
        FAILS.append(name)


def extract_obj(src, anchor):
    at = src.index(anchor)
    j = src.index('{', at)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                return src[at:k + 1] + ';'
    raise SystemExit('unbalanced braces extracting ' + anchor)


src = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
lanes_js = extract_obj(src, 'var WORKER_LANES = {')

# (label, sale days or None, row)
CASES = [
    ('sale today', 0, {'st': 'MF'}),
    ('sale in 7 days', 7, {'st': 'MF'}),
    ('sale in 8 days', 8, {'st': 'MF'}),
    ('sale in 45 days', 45, {'st': 'MF'}),
    ('sale in 46 days', 46, {'st': 'MF'}),
    ('sale in 90 days', 90, {'st': 'MF'}),
    ('sale in 120 days', 120, {'st': 'MF'}),
    ('LP, no sale date', None, {'st': 'LP'}),
    ('no sale date', None, {'st': 'MF'}),
    ('sale passed', -1, {'st': 'MF'}),
]
js = ('function _replyWaiting(r){ return !!r.rw; }\n' + lanes_js + '\n'
      'var CASES = ' + json.dumps([[d, r] for _, d, r in CASES]) + ';\n'
      'var out = CASES.map(function(c){ var hit = [];\n'
      '  Object.keys(WORKER_LANES).forEach(function(k){ if(WORKER_LANES[k].test(c[0], c[1])) hit.push(k); });\n'
      '  return hit; });\n'
      'console.log(JSON.stringify(out));\n')
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
    f.write(js)
    path = f.name
try:
    r = subprocess.run(['node', path], capture_output=True, text=True, timeout=60,
                       encoding='utf-8', errors='replace')
finally:
    os.unlink(path)
if r.returncode != 0:
    raise SystemExit('node failed:\n' + (r.stderr or '')[:3000])
hits = json.loads(r.stdout)

WANT = {0: ['urgent'], 7: ['urgent'], 8: ['active'], 45: ['active'], 46: ['active'],
        90: ['active'], 120: ['active']}
for (label, d, row), got in zip(CASES, hits):
    if d is None:
        want = ['early']
    elif d < 0:
        want = []
    else:
        want = WANT[d]
    rec('%s -> %s' % (label, want or 'no lane'), got == want, got)

# the Python sender's lane table says the same thing for every dated sale in the worker's range
sys.path.insert(0, HERE)
try:
    import outreach_email as OE
except Exception as e:                                  # pragma: no cover - import env only
    OE = None
    print('SKIP outreach_email agreement (import failed: %s)' % e)
if OE is not None:
    import datetime as _dt
    for (label, d, row), got in zip(CASES, hits):
        if d is None or d < 0:
            continue
        day = _dt.date.today() + _dt.timedelta(days=d)
        lead = {'auction': '%d/%d/%d' % (day.month, day.day, day.year), 'days': d}
        rec('outreach_email agrees: %s' % label, [OE._lane_of(lead)] == got,
            (OE._lane_of(lead), got))

# a replied owner stays out of the day lanes at any distance (REPLIED is lane zero)
js2 = ('function _replyWaiting(r){ return !!r.rw; }\n' + lanes_js + '\n'
       'console.log(JSON.stringify([WORKER_LANES.active.test(90, {rw:1}), WORKER_LANES.replied.test(90, {rw:1})]));\n')
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
    f.write(js2)
    path = f.name
try:
    r2 = subprocess.run(['node', path], capture_output=True, text=True, timeout=60,
                        encoding='utf-8', errors='replace')
finally:
    os.unlink(path)
rec('replied owner 90 days out is REPLIED, not ACTIVE', json.loads(r2.stdout) == [False, True], r2.stdout)

print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
