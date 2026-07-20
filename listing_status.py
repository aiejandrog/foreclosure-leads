"""Zillow listing status for every lead: is the property actually ON the retail market?

Answers Jose's question "is it off-market or listed?" per row. This matters for the play:
  * LISTED (For Sale by Agent/Owner) -> an agent is involved, the owner already has retail hope
    and a price anchor. Letter play is weaker; the listing price IS the negotiation ceiling.
  * PENDING -> under contract. Lead is mostly dead unless the contract falls through.
  * SOLD (recently) -> possibly already flipped/short-sold; verify before spending anything.
  * FOR RENT -> owner is landlording it; different pitch (tired-landlord angle).
  * OFF-MARKET -> the good hunting ground. Nobody else is marketing it.

CRITICAL Zillow gotcha this script handles: Zillow marks its own pre-foreclosure/auction data
pages homeStatus=FOR_SALE even though the property is NOT listed by anyone. The REAL signal is
listingTypeDimension: 'For Sale by Agent' / 'For Sale by Owner' / 'New Construction' / 'Coming
Soon' are true listings; 'Pre-Foreclosure' / 'Foreclosure' / 'Unknown Listed By' are not.
(Verified live 2026-07-19: 525 W 79 PL Hialeah = FOR_SALE + Pre-Foreclosure = NOT listed;
888 Brickell Key Dr 807 = FOR_SALE + For Sale by Agent + $1.1M = genuinely listed.)

Fetch shape mirrors property_photos.zillow_photos (search page -> homedetails with referer chain;
direct homedetails hits 403). Cached per folio in listing_status_cache.json with a 7-day TTL —
listing status CHANGES (a lead can get listed mid-pipeline), so unlike property types this cache
expires. Fail-soft everywhere: any error leaves the lead's zstatus untouched.

Writes onto each lead:
  zstatus : one of LISTED | PENDING | SOLD | RENTAL | OFF-MARKET  ('' = never checked)
  zprice  : asking price in dollars when LISTED/PENDING (0 otherwise)
  zdoz    : days on Zillow when LISTED (0 otherwise)

Run:  python listing_status.py [--limit N] [--ttl-days 7]
"""

import argparse
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import property_photos as pp  # reuse _ZHDRS fingerprint + _addr_of

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'listing_status_cache.json')

# Escape-depth-agnostic: Zillow double-escapes the JSON inside __NEXT_DATA__, so quotes appear
# as " or \" or \\" depending on nesting level. \\* matches any number of backslashes.
PAT_HS = re.compile(r'homeStatus\\*"\s*:\s*\\*"([A-Z_]+)')
PAT_LTD = re.compile(r'listingTypeDimension\\*"\s*:\s*\\*"([^"\\]+)')
PAT_PRC = re.compile(r'[{,]\\*"price\\*"\s*:\s*(\d+)')
PAT_DOZ = re.compile(r'daysOnZillow\\*"\s*:\s*(-?\d+)')

TRUE_LISTING_TYPES = ('for sale by agent', 'for sale by owner', 'new construction', 'coming soon')


def _folio(r):
    return (re.sub(r'\D', '', str(r.get('folio') or r.get('Folio') or ''))
            or re.sub(r'[^a-z0-9]', '', str(r.get('case') or r.get('Case #') or '').lower()))


def classify(home_status, listing_type, price, doz):
    """Map raw Zillow fields to our zstatus label. Empty string = signal too thin to trust."""
    lt = (listing_type or '').strip().lower()
    hs = (home_status or '').strip().upper()
    if hs == 'PENDING':
        return 'PENDING'
    if hs in ('RECENTLY_SOLD', 'SOLD'):
        return 'SOLD'
    if hs == 'FOR_RENT':
        return 'RENTAL'
    if hs == 'FOR_SALE':
        # Zillow's own pre-foreclosure/auction data pages are FOR_SALE too — only a real
        # by-agent/by-owner listing counts as LISTED.
        return 'LISTED' if lt in TRUE_LISTING_TYPES else 'OFF-MARKET'
    if hs in ('OTHER', 'OFF_MARKET'):
        return 'OFF-MARKET'
    return ''


def _blocked(text):
    """True when Zillow served a bot-wall instead of a real page (HTTP 200 CAPTCHA). A blocked
    response must NOT be classified — it says nothing about the property."""
    t = text[:4000].lower()
    return 'captcha' in t or 'px-captcha' in t or 'denied' in t or len(text) < 15000


def fetch_status(addr):
    """(zstatus, zprice, zdoz) for one address. '' status = TRANSIENT failure (blocked/timeout),
    retried next run. A clean search with no property match is NOT transient — Zillow indexes
    essentially every parcel, so no-match means no listing exists: classified OFF-MARKET so
    every reachable property ends up with a badge instead of a permanent hole."""
    try:
        sess = requests.Session()
        sess.headers.update(pp._ZHDRS)
        url = 'https://www.zillow.com/homes/' + requests.utils.quote(addr) + '_rb/'
        r = sess.get(url, timeout=20)
        if r.status_code != 200 or _blocked(r.text):
            return '', 0, 0
        lm = re.search(r'https://www\.zillow\.com/homedetails/[^"\'<>\s]+/(\d+)_zpid/', r.text)
        if not lm:
            return 'OFF-MARKET', 0, 0
        h2 = dict(pp._ZHDRS)
        h2['Referer'] = url
        h2['Sec-Fetch-Site'] = 'same-origin'
        r2 = sess.get(lm.group(0), headers=h2, timeout=20)
        if r2.status_code != 200 or _blocked(r2.text):
            return '', 0, 0
        hs = PAT_HS.search(r2.text)
        ltd = PAT_LTD.search(r2.text)
        prc = PAT_PRC.search(r2.text)
        doz = PAT_DOZ.search(r2.text)
        status = classify(hs.group(1) if hs else '', ltd.group(1) if ltd else '',
                          int(prc.group(1)) if prc else 0, int(doz.group(1)) if doz else 0)
        # Page fetched cleanly but no recognizable homeStatus: that's Zillow's bare Zestimate
        # page shape for never-listed parcels — off-market, not unknown.
        if not status:
            status = 'OFF-MARKET'
        price = int(prc.group(1)) if prc else 0
        days = max(0, int(doz.group(1))) if doz else 0
        # Price/days only meaningful for live retail states
        if status not in ('LISTED', 'PENDING'):
            price = price if status == 'SOLD' else 0
            days = 0
        return status, price, days
    except Exception:
        return '', 0, 0


def _load_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        return json.load(open(CACHE_PATH, encoding='utf-8'))
    except Exception:
        return {}


def enrich_file(path, cache, ttl_s, limit_state):
    if not os.path.exists(path):
        return 0
    leads = json.load(open(path, encoding='utf-8'))
    if not isinstance(leads, list):
        return 0
    now = time.time()
    changed = fetched = 0
    for r in leads:
        if limit_state['n'] <= 0:
            break
        k = _folio(r)
        if not k:
            continue
        ent = cache.get(k)
        if ent and (now - ent.get('t', 0)) < ttl_s:
            if r.get('zstatus') != ent['s'] or r.get('zprice') != ent.get('p', 0):
                r['zstatus'], r['zprice'], r['zdoz'] = ent['s'], ent.get('p', 0), ent.get('d', 0)
                changed += 1
            continue
        addr = pp._addr_of(r)
        if not addr:
            continue
        status, price, days = fetch_status(addr)
        fetched += 1
        limit_state['n'] -= 1
        if status:
            r['zstatus'], r['zprice'], r['zdoz'] = status, price, days
            cache[k] = {'s': status, 'p': price, 'd': days, 't': now}
            changed += 1
        # A failed fetch is NOT cached — retried next run.
        time.sleep(1.2)  # same pacing the photo pass uses; Zillow tolerates it
        if fetched % 25 == 0:
            json.dump(cache, open(CACHE_PATH, 'w', encoding='utf-8'))
            # Flush the lead file too — a killed/timed-out run keeps everything fetched so far,
            # and a rebuild mid-backfill publishes partial statuses instead of none.
            json.dump(leads, open(path, 'w', encoding='utf-8'), indent=1)
            print(f'  ... {fetched} fetched in {os.path.basename(path)}')
    if changed:
        json.dump(leads, open(path, 'w', encoding='utf-8'), indent=1)
    print(f'{os.path.basename(path)}: {changed} updated, {fetched} fetched live')
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='max live fetches this run (0 = unlimited)')
    ap.add_argument('--ttl-days', type=float, default=7.0)
    a = ap.parse_args()
    base = os.path.dirname(os.path.abspath(__file__))
    cache = _load_cache()
    limit_state = {'n': a.limit if a.limit > 0 else 10 ** 9}
    for fn in ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json'):
        enrich_file(os.path.join(base, fn), cache, a.ttl_days * 86400, limit_state)
    json.dump(cache, open(CACHE_PATH, 'w', encoding='utf-8'))


if __name__ == '__main__':
    main()
