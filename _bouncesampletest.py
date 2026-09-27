#!/usr/bin/env python
"""_bouncesampletest.py -- the bounce breaker must weigh how much evidence it has.

WHY (2026-09-18): one shared bounce list produced two irreconcilable readings on the same
morning, because mail_sent.json is per-machine and the raw ratio does not know how big its
own denominator is. The laptop, which had barely sent, read 38.1% (8 of 21) and refused to
mail. This desktop, which had sent everything, read 0.9% (2 of 222) and put out 182 messages
in sixteen minutes. Running bounces.py here revealed the desktop's true figure was 14.0%
(31 of 222) -- the 0.9% was four days of unrecorded bounces, not health.

The obvious fix -- a minimum sample floor -- is the wrong one, and this file exists mostly to
keep anyone from re-introducing it: a floor of 50 would have let the laptop's 8-of-21 through,
and 8 of 21 was the ONE reading that turned out to be real. The verdict is taken on the Wilson
lower bound instead, which asks "how bad is this at worst, given this much evidence" and so
needs no arbitrary cutoff.

Run:  python _bouncesampletest.py
"""
import json
import os
import re
import tempfile

import pipeline_alerts as PA
import send_server as S

# Cohort days have to fall on or after the #83 gate. On the gate's own calendar day,
# date.today()-1 is the day before FIRST_TOUCH_GATE_CUTOFF_DATE, so those rows are not
# in the post-cutoff sample and "yesterday" would not pause. When the local date has not
# passed the gate yet, move this process's clock to the next day. send_server reads the
# same dt, so the window and the row dates stay aligned in any timezone.
_gate_day = S.dt.date.fromisoformat(S.FIRST_TOUCH_GATE_CUTOFF_DATE)
_real_today = S.dt.date.today()
_clock_day = _gate_day + S.dt.timedelta(days=1) if _real_today <= _gate_day else _real_today
if _clock_day != _real_today:
    class _ClockDate(S.dt.date):
        @classmethod
        def today(cls):
            return _clock_day

    class _ClockDT:
        date = _ClockDate
        datetime = S.dt.datetime
        timedelta = S.dt.timedelta
        timezone = S.dt.timezone
        time = S.dt.time

    S.dt = _ClockDT()

HERE = os.path.dirname(os.path.abspath(__file__))
_ok = _n = 0


def check(label, cond, detail=''):
    global _ok, _n
    _n += 1
    if cond:
        _ok += 1
    print('  %s %s%s' % ('PASS' if cond else 'FAIL', label, (' | %s' % detail) if detail else ''))


def health_for(pairs, bounced):
    """Run the REAL _bounce_health() against a synthetic ledger + bounce list."""
    d = S.dt.date.today().isoformat()
    ledger = [{'ch': 'email', 'message_id': '<x%d>' % i, 'd': d, 'to': a}
              for i, a in enumerate(pairs)]
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, 'bounced_emails.json'), 'w', encoding='utf-8') as f:
        json.dump(sorted(bounced), f)
    old_here, old_ledger = S.HERE, S._load_ledger
    try:
        S.HERE = tmp
        S._load_ledger = lambda: ledger
        return S._bounce_health()
    finally:
        S.HERE, S._load_ledger = old_here, old_ledger


def sample(dead, mailed):
    """`dead` dead addresses and `mailed - dead` live ones, as (recipients, bouncelist)."""
    bad = ['dead%d@x.com' % i for i in range(dead)]
    return bad + ['live%d@x.com' % i for i in range(mailed - dead)], set(bad)


print('-- the lower bound itself --')
W = S._wilson_lower
check('no sends is not evidence of health', W(0, 0) == 0.0)
check('a perfect record still has a floor of zero', W(0, 100) == 0.0)
check('the bound never exceeds the observed ratio',
      all(W(d, m) <= d / m + 1e-12 for d, m in ((1, 5), (8, 21), (31, 222), (2, 3), (50, 100))))
check('more of the same evidence tightens the bound upward',
      W(8, 21) < W(80, 210) < W(800, 2100), '%.3f < %.3f < %.3f' % (W(8, 21), W(80, 210), W(800, 2100)))

print()
print('-- the two readings of 2026-09-18 --')
LAPTOP, DESK_STALE, DESK_REAL = (8, 21), (2, 222), (31, 222)
check("the laptop's 8 of 21 still blocks -- weak sample, real signal",
      W(*LAPTOP) > S.BOUNCE_CEILING, 'lb=%.1f%%' % (W(*LAPTOP) * 100))
check("the desktop's stale 2 of 222 does not block",
      W(*DESK_STALE) <= S.BOUNCE_CEILING, 'lb=%.1f%%' % (W(*DESK_STALE) * 100))
check('the desktop 31 of 222, once bounces.py had run, blocks',
      W(*DESK_REAL) > S.BOUNCE_CEILING, 'lb=%.1f%%' % (W(*DESK_REAL) * 100))
check('and the two machines now agree that the list is unsafe',
      (W(*LAPTOP) > S.BOUNCE_CEILING) == (W(*DESK_REAL) > S.BOUNCE_CEILING))

print()
print('-- THE REGRESSION THIS FILE EXISTS FOR --')
floor50 = lambda d, m: (d / m if m else 0) > S.BOUNCE_CEILING and m >= 50
check('a flat floor of 50 would have UNBLOCKED the one true alarm',
      not floor50(*LAPTOP) and W(*LAPTOP) > S.BOUNCE_CEILING,
      'floor50=allow wilson=BLOCK -- do not reintroduce a sample floor')
check('a floor would also have cleared the stale desktop reading it could not see',
      not floor50(*DESK_STALE))

print()
print('-- samples too thin to convict --')
check('1 of 5 proves nothing', W(1, 5) <= S.BOUNCE_CEILING, 'lb=%.1f%%' % (W(1, 5) * 100))
check('a single bounce in 150 clean sends proves nothing', W(1, 150) <= S.BOUNCE_CEILING)
check('a box that has never sent is not blocked', W(0, 0) <= S.BOUNCE_CEILING)
check('but 2 of 3 dead is damning even at n=3', W(2, 3) > S.BOUNCE_CEILING, 'lb=%.1f%%' % (W(2, 3) * 100))

print()
print('-- _bounce_health() end to end --')
h = health_for(*sample(*LAPTOP))
check('it reports the raw ratio unchanged for the board', abs(h['rate'] - 8 / 21) < 1e-9)
check('it reports the bound beside it', abs(h['lb'] - W(*LAPTOP)) < 1e-9)
check('it carries its own verdict', h['blocked'] is True)
check('dead and mailed are counted per RECIPIENT', (h['dead'], h['mailed']) == (8, 21))
check('the ceiling travels with the reading', h['ceiling'] == S.BOUNCE_CEILING)
h2 = health_for(*sample(1, 5))
check('a thin sample reports its rate but does not block',
      h2['rate'] > S.BOUNCE_CEILING and h2['blocked'] is False, 'rate=20%% lb=%.1f%%' % (h2['lb'] * 100))
h3 = health_for(*sample(*DESK_REAL))
check('the desktop reading blocks end to end', h3['blocked'] is True)

print()
print('-- daily first-touch pause: more than 5% once 20 have been sent, or 2 hard bounces in a day of 10 --')


def health_rows(rows, bounced):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, 'bounced_emails.json'), 'w', encoding='utf-8') as f:
        json.dump(sorted(bounced), f)
    old_here, old_ledger = S.HERE, S._load_ledger
    try:
        S.HERE = tmp
        S._load_ledger = lambda: rows
        return S._bounce_health()
    finally:
        S.HERE, S._load_ledger = old_here, old_ledger


def ft_rows(dead, mailed, touch='first', day=None, **extra):
    d = day or S.dt.date.today().isoformat()
    bad = ['dead%d@x.com' % i for i in range(dead)]
    live = ['live%d@x.com' % i for i in range(max(0, mailed - dead))]
    rows = []
    for i, a in enumerate(bad + live):
        row = {'ch': 'email', 'message_id': '<ft%d>' % i, 'd': d, 'to': a, 'touch': touch}
        row.update(extra)
        rows.append(row)
    return rows, set(bad)


hday = health_rows(*ft_rows(2, 20))
check('2 of 20 first touches is over 5% and pauses (Wilson alone would not)',
      hday['day_blocked'] is True and hday['blocked'] is True and hday['lb'] <= S.BOUNCE_CEILING,
      'day=%.1f%% lb=%.1f%%' % (hday['day_rate'] * 100, hday['lb'] * 100))
h5 = health_rows(*ft_rows(1, 20))
check('exactly 5% (1 of 20) does not pause',
      h5['day_blocked'] is False and h5['blocked'] is False, 'day=%.1f%%' % (h5['day_rate'] * 100))
h0 = health_rows(*ft_rows(0, 20))
check('0 of 20 first touches is clear', h0['day_blocked'] is False and h0['blocked'] is False)
hshort = health_rows(*ft_rows(2, 9))
check('2 of 9 is over 5% but under the minimum sample, and Wilson does not convict it either',
      hshort['day_blocked'] is False and hshort['blocked'] is False,
      'day=%.1f%% lb=%.1f%%' % (hshort['day_rate'] * 100, hshort['lb'] * 100))
hkeep = health_rows(*ft_rows(8, 21, touch=None))
check('the Wilson block still trips when the rows are not first touches',
      hkeep['day_blocked'] is False and hkeep['blocked'] is True)
htest = health_rows(*ft_rows(20, 20, test_mode=True))
check('test sends are not a first-touch sample', htest['day_sent'] == 0 and htest['day_blocked'] is False)
herr = health_rows(*ft_rows(20, 20, error=True))
check('error rows are not a first-touch sample', herr['day_sent'] == 0 and herr['day_blocked'] is False)
# The prior cohort day, not the calendar yesterday. On the gate day that yesterday is
# pre-cutoff; this is the day before the clock above, which is always post-cutoff.
yday = (S.dt.date.today() - S.dt.timedelta(days=1)).isoformat()
hy = health_rows(*ft_rows(2, 20, day=yday))
check("yesterday's 2 of 20, with nothing sent today, pauses on that cohort",
      hy['day_blocked'] is True and hy['blocked'] is True and hy['day_date'] == yday
      and (hy['day_sent'], hy['day_dead']) == (20, 2) and hy['lb'] <= S.BOUNCE_CEILING,
      'date=%s sent=%s dead=%s' % (hy['day_date'], hy['day_sent'], hy['day_dead']))
hy1 = health_rows(*ft_rows(1, 20, day=yday))
check("yesterday's 1 of 20 does not pause",
      hy1['day_blocked'] is False and hy1['blocked'] is False and hy1['day_date'] == yday
      and (hy1['day_sent'], hy1['day_dead']) == (20, 1),
      'day=%.1f%%' % (hy1['day_rate'] * 100))
stale = (S.dt.date.today() - S.dt.timedelta(days=S.BOUNCE_WINDOW_DAYS + 1)).isoformat()
hold = health_rows(*ft_rows(2, 20, day=stale))
check('a cohort older than the window does not pause',
      hold['day_blocked'] is False and hold['day_sent'] == 0 and hold['blocked'] is False)
rows_y, bad_y = ft_rows(2, 20, day=yday)
rows_t, bad_t = ft_rows(1, 20)
hmix = health_rows(rows_y + rows_t, bad_y | bad_t)
check("a clear today does not hide yesterday over the ceiling",
      hmix['day_blocked'] is True and hmix['blocked'] is True and hmix['day_date'] == yday
      and (hmix['day_sent'], hmix['day_dead']) == (20, 2))
os.environ['BOUNCE_DAY_MIN_SAMPLE'] = '5'
try:
    hmin = health_rows(*ft_rows(2, 9))
    check('BOUNCE_DAY_MIN_SAMPLE=5 lets 2 of 9 pause', hmin['day_blocked'] is True and hmin['blocked'] is True)
finally:
    os.environ.pop('BOUNCE_DAY_MIN_SAMPLE', None)
os.environ['BOUNCE_DAY_CEILING'] = '1'
try:
    hoff = health_rows(*ft_rows(2, 20))
    check('BOUNCE_DAY_CEILING=1 does not pause 2 of 20, and does not clear a Wilson block that is not there',
          hoff['day_blocked'] is False and hoff['blocked'] is False)
finally:
    os.environ.pop('BOUNCE_DAY_CEILING', None)

print()
print('-- post-#83 cohort: the 09-19 batch does not feed the first-touch block --')
check('the cutoff is the #83 merge instant',
      S.FIRST_TOUCH_GATE_CUTOFF == '2026-09-26T18:21:42+00:00')
check('a 2026-09-19 timestamp is before the gate',
      not S._post_gate_send({'d': '2026-09-19', 'ts_utc': '2026-09-19T15:00:00+00:00'}))
check('a bare date on the gate day counts (no ts_utc stored)',
      S._post_gate_send({'d': '2026-09-26'}))
check('a timestamp before the merge, even on the gate day, does not count',
      not S._post_gate_send({'d': '2026-09-26', 'ts_utc': '2026-09-26T18:21:41+00:00'}))
check('the merge instant itself counts',
      S._post_gate_send({'ts_utc': '2026-09-26T18:21:42+00:00', 'd': '2026-09-26'}))


def bind(rows, bounced, window=None):
    """Point _bounce_health and the first-touch picker at one fixture. Caller restores."""
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, 'bounced_emails.json'), 'w', encoding='utf-8') as f:
        json.dump(sorted(bounced), f)
    return tmp, (S.HERE, S._load_ledger, S.BOUNCE_WINDOW_DAYS, dict(S._FT_SLOTS))


def use(rows, bounced, window=400):
    tmp, saved = bind(rows, bounced)
    S.HERE = tmp
    S._load_ledger = lambda: rows
    S.BOUNCE_WINDOW_DAYS = window
    S._FT_SLOTS.clear()
    return tmp, saved


def restore(saved):
    S.HERE, S._load_ledger, S.BOUNCE_WINDOW_DAYS, slots = saved
    S._FT_SLOTS.clear()
    S._FT_SLOTS.update(slots)


def old_batch(touch=None):
    """31 of 85 on 2026-09-19, the shape the Wilson reading is stuck on. No gate marker."""
    rows = []
    bad = []
    for i in range(85):
        addr = 'old%d@example.com' % i
        row = {'ch': 'email', 'message_id': '<old%d>' % i, 'd': '2026-09-19',
               'ts_utc': '2026-09-19T16:04:00+00:00', 'to': addr}
        if touch:
            row['touch'] = touch
        if i < 31:
            bad.append(addr)
        rows.append(row)
    return rows, set(bad)


def today_from(addr, n, dead=0, domain_tag=''):
    d = S.dt.date.today().isoformat()
    rows = []
    bad = []
    for i in range(n):
        a = 'new%s%d@example.com' % (domain_tag, i)
        rows.append({'ch': 'email', 'message_id': '<n%s%d>' % (domain_tag, i), 'd': d,
                     'ts_utc': d + 'T15:00:00+00:00', 'to': a, 'from': addr, 'touch': 'first'})
        if i < dead:
            bad.append(a)
    return rows, set(bad)


HI_RAMP = {
    'main_domain': 'bsgflorida.com', 'main_domain_cap': 40,
    'ramp_start': '2020-01-01', 'ramp': [{'through_day': 9999, 'per_day': 100}],
    'lanes': {'default': 'ops@bsgflorida.com'},
    'first_touch': {'from': ['a@one.example', 'b@one.example', 'c@two.example'], 'per_day': 100},
}

rows_old, bad_old = old_batch()
tmp, saved = use(rows_old, bad_old)
try:
    hold = S._bounce_health()
    check('the 09-19 batch stays in the trailing reading',
          (hold['dead'], hold['mailed']) == (31, 85) and hold['lb'] > S.BOUNCE_CEILING
          and hold['blocked'] is True
          and (hold['pre_dead'], hold['pre_mailed']) == (31, 85) and hold['post_mailed'] == 0,
          'lb=%.1f%%' % (hold['lb'] * 100))
    check('that batch does not feed the first-touch block',
          hold['ft_sent'] == 0 and hold['ft_dead'] == 0 and hold['day_blocked'] is False
          and hold['ft_rule'] == 'slow_restart' and hold['hist_sent'] == 0
          and S._first_touch_bounce_paused(hold) is False, hold['ft_rule'])
    check('slow restart ceiling is 10 and does not pause the picker',
          S._ft_restart_ceiling(hold) == 10 and S._pick_first_touch_from(HI_RAMP, ceiling=10)[0] != '')
    alert = PA.bounce_alert(PA.bounce_signal(hold), '2026-09-27T21:00:00-04:00')
    check('the old batch does not fire the first-touch alert', alert is None, alert)
finally:
    restore(saved)

rows_marked, bad_marked = old_batch(touch='first')
tmp, saved = use(rows_marked, bad_marked)
try:
    marked = S._bounce_health()
    check('pre-cutoff rows marked touch=first are history, not the cohort',
          marked['hist_sent'] == 85 and marked['hist_dead'] == 31
          and marked['ft_sent'] == 0 and marked['ft_rule'] == 'slow_restart'
          and marked['day_blocked'] is False)
finally:
    restore(saved)

rows_cap, bad_cap = today_from('a@one.example', 10, domain_tag='a')
tmp, saved = use(rows_old + rows_cap, bad_old | bad_cap)
try:
    small = S._bounce_health()
    picked, st = S._pick_first_touch_from(HI_RAMP)
    caps = {x['addr']: x['first_touch_cap'] for x in st}
    check('a small post-cutoff sample still allows a send, at 10 per domain',
          small['ft_rule'] == 'slow_restart' and small['ft_sent'] == 10 and small['ft_dead'] == 0
          and picked == 'c@two.example' and caps['a@one.example'] == 10
          and S._first_touch_bounce_paused(small) is False,
          '%s %s %s' % (picked, caps, small['ft_rule']))
    low = dict(HI_RAMP, ramp=[{'through_day': 9999, 'per_day': 4}])
    low_caps = {x['addr']: x['first_touch_cap'] for x in S._first_touch_status(low, ceiling=10)}
    check('the restart cap does not raise a lower warm-up ramp',
          low_caps['a@one.example'] == 4 and low_caps['c@two.example'] == 4, low_caps)
finally:
    restore(saved)

rows_b, bad_b = today_from('c@two.example', 9, domain_tag='c')
tmp, saved = use(rows_old + rows_cap + rows_b, bad_old | bad_cap | bad_b)
try:
    mid = S._bounce_health()
    nxt = S._pick_first_touch_from(HI_RAMP)[0]
    check('domain one is full at 10 and the next slow-restart send uses the other domain',
          mid['ft_rule'] == 'slow_restart' and mid['ft_sent'] == 19 and nxt == 'c@two.example',
          '%s %s' % (mid['ft_rule'], nxt))
finally:
    restore(saved)
rows_full, bad_full = today_from('c@two.example', 10, domain_tag='c')
tmp, saved = use(rows_cap + rows_full, bad_cap | bad_full)
try:
    shut = S._pick_first_touch_from(HI_RAMP, ceiling=10)[0]
    check('both domains at the restart cap: no further first touch on either', shut == '', shut)
finally:
    restore(saved)

one_dead, one_bad = today_from('a@one.example', 10, dead=1, domain_tag='d')
tmp, saved = use(one_dead, one_bad)
try:
    one = S._bounce_health()
    check('1 of 10 is over 5% but the sample is still small, so sends stay allowed',
          one['ft_rule'] == 'slow_restart' and one['day_blocked'] is False
          and S._first_touch_bounce_paused(one) is False
          and S._pick_first_touch_from(HI_RAMP)[0] != '')
finally:
    restore(saved)

two_dead, two_bad = today_from('a@one.example', 10, dead=2, domain_tag='h')
tmp, saved = use(two_dead, two_bad)
try:
    hard = S._bounce_health()
    sig = PA.bounce_signal(hard)
    fired = PA.bounce_alert(sig, '2026-09-27T21:00:00-04:00')
    check('2 hard bounces in a day of 10 re-blocks',
          hard['ft_rule'] == 'paused' and hard['ft_why'] == 'hard_bounces'
          and hard['day_blocked'] is True and S._first_touch_bounce_paused(hard) is True
          and S._ft_restart_ceiling(hard) == 0 and S._pick_first_touch_from(HI_RAMP)[0] == '')
    check('that pause fires the alert, counts only',
          fired and fired['severity'] == 'fail' and '2 of 10' in fired['text']
          and 'Rule: paused' in fired['text'] and 'example.com' not in fired['text']
          and '@' not in fired['text'], fired['text'] if fired else hard)
finally:
    restore(saved)

rows20, bad20 = ft_rows(2, 20)
for r in rows20:
    r['ts_utc'] = r['d'] + 'T15:00:00+00:00'
tmp, saved = use(rows20, bad20)
try:
    over = S._bounce_health()
    sig = PA.bounce_signal(over)
    fired = PA.bounce_alert(sig, '2026-09-27T21:00:00-04:00')
    check('2 of 20 post-cutoff is over 5% and re-blocks',
          over['ft_rule'] == 'paused' and over['day_blocked'] is True
          and (over['ft_sent'], over['ft_dead']) == (20, 2)
          and S._pick_first_touch_from(HI_RAMP)[0] == '')
    check('the 5% breach fires the alert with the post-cutoff counts and the rule',
          fired and '2 of 20' in fired['text'] and 'Rule: paused' in fired['text']
          and 'Post-cutoff' in fired['text'] and 'example.com' not in fired['text'],
          fired['text'] if fired else over)
finally:
    restore(saved)

clean20, _none = ft_rows(0, 20)
tmp, saved = use(clean20, set())
try:
    healthy = S._bounce_health()
    check('20 clean post-cutoff sends clear the restart cap',
          healthy['ft_rule'] == 'clear' and S._ft_restart_ceiling(healthy) is None
          and S._first_touch_status(HI_RAMP)[0]['first_touch_cap'] == 100)
finally:
    restore(saved)

print()
print('-- a missing bounce list must not read as health --')
old_here = S.HERE
try:
    S.HERE = tempfile.mkdtemp()      # no bounced_emails.json in it
    hm = S._bounce_health()
finally:
    S.HERE = old_here
check('it degrades to a complete payload, not a KeyError downstream',
      {'rate', 'lb', 'mailed', 'dead', 'known', 'window', 'ceiling', 'blocked'} <= set(hm))
check('and does not claim a verdict it cannot support', hm['blocked'] is False)
check('a missing list is unmeasured and still caps first touches at 10 per domain',
      hm['ft_measured'] is False and hm['ft_rule'] == 'unmeasured'
      and S._ft_restart_ceiling(hm) == S.FIRST_TOUCH_RESTART_CAP == 10)

print()
print('-- one verdict, computed in one place --')
src = open(os.path.join(HERE, 'send_server.py'), encoding='utf-8').read()
check('nothing compares the raw rate to the ceiling any more',
      not re.search(r"\[.rate.\]\s*>\s*BOUNCE_CEILING", src),
      'the 09-18 bug was this comparison living in three places')
check('the /send breaker reads the verdict', "if _hb['blocked'] and not _ft_exempt:" in src)
check('a first touch is exempt from the trailing block only via a warm-up sender',
      "_ft_exempt = bool(_ft_warmup) and _hb.get('ft_rule') in ('slow_restart', 'clear', 'unmeasured')" in src)
check('/health publishes the same verdict it enforces', "'bounce_blocked': _bh['blocked']," in src)
check('the breaker message tells the operator both numbers',
      "_hb['lb']" in src and "_hb['rate']" in src)

print()
print('%d/%d passed' % (_ok, _n))
raise SystemExit(0 if _ok == _n else 1)
