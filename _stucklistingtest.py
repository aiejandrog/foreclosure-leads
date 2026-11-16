"""_stucklistingtest -- Jesse's stuck-listing signal: days on Zillow today, price cut, fetch priority.

Run:  python _stucklistingtest.py   (exit 0 = safe; no network, invented cache entries only)

WHAT THIS PINS
  1. doz_now(): the days count keeps running after the fetch; only a LISTED entry has one.
  2. signal(): STUCK = LISTED, 90+ days today, from a reading no older than 21 days. A stale
     reading, a pending sale, an off-market parcel or a fresh listing is not stuck.
  3. Price cut: kept across refreshes (previous price), cleared on a raise or when the listing ends;
     reported only on a stuck listing.
  4. refresh_priority(): live listings first, then the soonest sale.
  5. value_join.attach stamps rows from the cache (lis pendens rows have no other route), never
     overrides a status a row already has, and never stamps from an old reading.
  6. The Call Mode row carries the days and the cut, the card draws the chip, and the nightly gives
     lis pendens rows their own fetch budget.
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import listing_signal as S  # noqa: E402
import value_join as VJ  # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra[:200]) if extra else ''))
    if not ok:
        fails.append(name)


NOW = time.time()
DAY = 86400


def ent(status='LISTED', d=60, ago=10, p=400000, pp=0):
    e = {'s': status, 'p': p, 'd': d, 't': NOW - ago * DAY}
    if pp:
        e['pp'] = pp
    return e


# ---- 1. doz_now ------------------------------------------------------------------------------------
rec('1a: days keep running after the fetch', S.doz_now(ent(d=60, ago=10), NOW) == 70)
rec('1b: only a LISTED entry has a count', S.doz_now(ent('OFF-MARKET', d=60), NOW) == 0 and S.doz_now(ent('PENDING', d=60), NOW) == 0 and S.doz_now(None, NOW) == 0)

# ---- 2. signal ----------------------------------------------------------------------------------------
rec('2a: listed 60 days at the fetch, read 30... too old', S.signal(ent(d=60, ago=30), NOW) == {})
rec('2b: 85 days at the fetch, read 6 days ago, is 91 today: stuck', S.signal(ent(d=85, ago=6), NOW) == {'zstuck': 91}, S.signal(ent(d=85, ago=6), NOW))
rec('2c: 89 days today is not yet stuck', S.signal(ent(d=83, ago=6), NOW) == {})
rec('2d: pending, off-market and sold are never stuck', all(S.signal(ent(s, d=200, ago=2), NOW) == {} for s in ('PENDING', 'OFF-MARKET', 'SOLD', 'RENTAL')))
rec('2e: a reading 22 days old is not trusted even at 300 days', S.signal(ent(d=300, ago=22), NOW) == {})
rec('2f: junk never raises', S.signal(None, NOW) == {} and S.signal({'s': 'LISTED'}, NOW) == {} and S.signal('x', NOW) == {})

# ---- 3. price cut ---------------------------------------------------------------------------------------
rec('3a: a cut is remembered', S.next_prev_price(ent(p=400000), 'LISTED', 380000) == 400000)
rec('3b: an unchanged price keeps the earlier cut', S.next_prev_price(ent(p=380000, pp=400000), 'LISTED', 380000) == 400000)
rec('3c: a raise clears it', S.next_prev_price(ent(p=380000, pp=400000), 'LISTED', 390000) == 0)
rec('3d: off the market clears it', S.next_prev_price(ent(p=380000, pp=400000), 'OFF-MARKET', 0) == 0)
rec('3e: a first reading has no previous price', S.next_prev_price(None, 'LISTED', 380000) == 0)
rec('3f: percent cut', S.cut_pct(400000, 380000) == 5 and S.cut_pct(0, 5) == 0 and S.cut_pct(100, 120) == 0 and S.cut_pct('x', None) == 0)
rec('3g: a cut is reported only with a stuck listing',
    S.signal(ent(d=95, ago=3, p=380000, pp=400000), NOW) == {'zstuck': 98, 'zcut': 5}
    and S.signal(ent(d=20, ago=3, p=380000, pp=400000), NOW) == {})

# ---- 4. priority --------------------------------------------------------------------------------------------
rows = [({'days': 5}, ent('OFF-MARKET')), ({'days': 90}, ent('LISTED')), ({'days': 30}, ent('LISTED')), ({'days': 9999}, None)]
order = sorted(rows, key=lambda x: S.refresh_priority(x[1], x[0]))
rec('4a: live listings first, then the soonest sale', [r[0]['days'] for r in order] == [30, 90, 5, 9999], [r[0]['days'] for r in order])

# ---- 5. attach --------------------------------------------------------------------------------------------------
cache = {'111': ent(d=85, ago=6, p=380000, pp=400000), '222': ent(d=200, ago=40), '333': ent('OFF-MARKET', d=0, ago=2)}
rows = [{'folio': '111'}, {'folio': '222'}, {'folio': '333'}, {'folio': '111', 'zstatus': 'PENDING', 'zdoz': 5}, {'folio': '999'}]
VJ.attach(rows, listing=cache)
rec('5a: a row with no status takes it from the cache, with today\'s days and the cut',
    rows[0].get('zstatus') == 'LISTED' and rows[0].get('zdoz') == 91 and rows[0].get('zstuck') == 91 and rows[0].get('zcut') == 5, rows[0])
rec('5b: an old reading is not stamped at all', 'zstatus' not in rows[1] and 'zstuck' not in rows[1], rows[1])
rec('5c: an off-market parcel gets no stuck flag', 'zstuck' not in rows[2])
rec('5d: a status a row already has is never overridden', rows[3]['zstatus'] == 'PENDING' and rows[3]['zdoz'] == 5)
rec('5e: no cache entry, nothing stamped', rows[4] == {'folio': '999'})
rows = [{'folio': '111', 'zstuck': 400, 'zcut': 50}]
VJ.attach(rows, listing={'111': ent('OFF-MARKET', ago=2)})
rec('5f: a stale flag from an earlier build is removed when the listing is gone', 'zstuck' not in rows[0] and 'zcut' not in rows[0], rows[0])

# ---- 6. Call Mode + nightly ----------------------------------------------------------------------------------------
cm = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
rec('6a: the row carries days and cut', "'zk': (d.get('zstuck')" in cm and "'zc': (d.get('zcut')" in cm)
rec('6b: the card draws a STUCK LISTING chip', 'STUCK LISTING &middot; ' in cm and 'if(r.zk) clock +=' in cm)
bat = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='cp1252', errors='replace').read()
rec('6c: the nightly gives lis pendens rows their own listing budget', '--lp-limit 80' in bat)
ls = open(os.path.join(HERE, 'listing_status.py'), encoding='utf-8').read()
rec('6d: the fetch loop is priority-sorted and records the previous price',
    'sig.refresh_priority(' in ls and 'sig.next_prev_price(' in ls and "enrich_file(os.path.join(base, 'lp_leads.json')" in ls)

print('\n%d checks, %d failed' % (len(checks), len(fails)))
print('ALL PASS' if not fails else 'FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)
