"""listing_signal.py -- the STUCK LISTING signal, from the Zillow listing cache. Pure functions.

Jesse (9/21 meeting): "The tell: Zillow listings over 90 days with a price drop = a bigger dip is
forming." A property that has sat on the market for months at a price nobody pays is an owner who
needs to sell and has an agent who is out of ideas: the warmest door in the book. The data was
already collected (listing_status_cache.json: status, price, days on Zillow) and used for one
thing, a 'listed in the next 21 days of a sale' flag. Three gaps this closes:

  * days on Zillow was stored as of the FETCH, and the cache is weeks old (918 of 3,001 entries
    older than 90 days on 2026-11-16), so a listing fetched at 40 days still read 40;
  * a price cut was never kept (the cache held only the latest price);
  * lis pendens rows, the bulk of new filings, were never looked up at all.

STUCK = LISTED, days on Zillow >= 90 as of today, from a reading no older than STUCK_MAX_AGE_DAYS.
A listing we have not looked at for three weeks might have sold; it is not called stuck on a guess.
"""
import time

STUCK_DAYS = 90
STUCK_MAX_AGE_DAYS = 21
LIVE = ('LISTED', 'PENDING')


def age_days(ent, now=None):
    t = (ent or {}).get('t') or 0
    return max(0, int(((now if now is not None else time.time()) - t) // 86400)) if t else None


def doz_now(ent, now=None):
    """Days on Zillow as of today for a LISTED entry: the fetched count plus the days since."""
    if not isinstance(ent, dict) or ent.get('s') != 'LISTED':
        return 0
    a = age_days(ent, now)
    d = int(ent.get('d') or 0)
    return d + (a or 0)


def cut_pct(prev, price):
    """Percent the asking price came down, or 0."""
    try:
        prev, price = int(prev or 0), int(price or 0)
    except (TypeError, ValueError):
        return 0
    return int(round(100.0 * (prev - price) / prev)) if prev > 0 and 0 < price < prev else 0


def next_prev_price(old, status, price):
    """What to store as the PREVIOUS price after a fresh reading, so the cut survives refreshes.
    A cut sets it to the old price; an unchanged price keeps what was there; a raise clears it;
    a listing that went off the market clears it."""
    old = old if isinstance(old, dict) else {}
    if status not in LIVE or not price:
        return 0
    old_price = int(old.get('p') or 0)
    if old.get('s') in LIVE and old_price > price:
        return old_price
    if old_price == price:
        return int(old.get('pp') or 0)
    return 0


def signal(ent, now=None):
    """-> {'zstuck': days, 'zcut': pct} for a cache entry that is a stuck listing today, else {}.
    zcut rides along only on a listing that is also stuck: a cut on a fresh listing is routine."""
    if not isinstance(ent, dict) or ent.get('s') != 'LISTED':
        return {}
    a = age_days(ent, now)
    if a is None or a > STUCK_MAX_AGE_DAYS:
        return {}
    d = doz_now(ent, now)
    if d < STUCK_DAYS:
        return {}
    out = {'zstuck': d}
    c = cut_pct(ent.get('pp'), ent.get('p'))
    if c:
        out['zcut'] = c
    return out


def refresh_priority(ent, row=None):
    """Sort key for the nightly fetch budget: live listings first (their days and price move, and a
    stuck one is the signal), then rows with a sale date soonest, then everything else."""
    live = 0 if isinstance(ent, dict) and ent.get('s') in LIVE else 1
    days = (row or {}).get('days')
    days = days if isinstance(days, (int, float)) and days < 9000 else 9999
    return (live, days)
