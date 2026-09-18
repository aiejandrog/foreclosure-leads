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

import send_server as S

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

print()
print('-- one verdict, computed in one place --')
src = open(os.path.join(HERE, 'send_server.py'), encoding='utf-8').read()
check('nothing compares the raw rate to the ceiling any more',
      not re.search(r"\[.rate.\]\s*>\s*BOUNCE_CEILING", src),
      'the 09-18 bug was this comparison living in three places')
check('the /send breaker reads the verdict', "if _hb['blocked']:" in src)
check('/health publishes the same verdict it enforces', "'bounce_blocked': _bh['blocked']," in src)
check('the breaker message tells the operator both numbers',
      "_hb['lb']" in src and "_hb['rate']" in src)

print()
print('%d/%d passed' % (_ok, _n))
raise SystemExit(0 if _ok == _n else 1)
