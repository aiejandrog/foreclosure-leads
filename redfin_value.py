#!/usr/bin/env python
"""redfin_value.py -- Redfin Estimate (AVM) per lead, as an independent second opinion on value.

WHY: the board values a property from ONE source — comps ARV when it passes a sanity gate, else
county market value. There is no independent cross-check. This adds Redfin's own AVM
("Redfin Estimate") so the operator can see when two independent machines corroborate the number
the deal math already uses (trust it) or contradict it (pull comps before bidding). It is
ADVISORY ONLY — nothing here feeds the profit/equity math. See tracker_template.html r._valx.

PURE SIDECAR: like comps.py / county_taxes.py, this writes ONLY its own cache (redfin_cache.json).
It never edits the lead JSON files. make_tracker bakes the cache onto rows in a folio-keyed
post-pass, so no lead-file field can be lost.

TRANSPORT (probed live 2026-08-02, decided empirically):
  - Redfin's stingray JSON API is WAF-blocked (403) to plain `requests`, AND a real browser session
    cookie alone does not unlock it — the WAF needs the JS challenge actually executed.
  - Search-engine URL resolution (DuckDuckGo) rate-limits (202) within a handful of calls — too
    fragile for a full sweep.
  - What works cleanly: a headless browser that primes redfin.com once (passing the challenge),
    then calls the stingray API via IN-PAGE fetch (same-origin, WAF-approved). Two lightweight
    JSON calls per lead, no page navigation:
        /stingray/do/location-autocomplete?location=<addr>  -> propertyId + canonical /home/ url
        /stingray/api/home/details/avm?propertyId=<id>       -> predictedValue (the Redfin Estimate)
  This is the reliable LOCAL path (residential IP). In the GitHub Actions cloud run Redfin, like
  Zillow, will likely block the datacenter IP — that step is best-effort (continue-on-error) and
  the cache seeded by the nightly local run still bakes values.

  NOTE: the avm endpoint returns 200 for ANY propertyId (it will happily hand back a different
  home), so the autocomplete JOIN GUARD is the ONLY thing preventing a neighbor's value from
  polluting a row. The guard is enforced twice: on the autocomplete row, and again against the
  streetAddress echoed back in the avm payload.

CACHE  redfin_cache.json  {folio_digits: {v:int, url:str, conf:'ok'|'addr'|'nomatch', t:epoch, sqft?:int}}
  - TTL 21 days (the Estimate moves slowly).
  - Transport failures (block / timeout) are NEVER cached -> retried next run.
  - A clean "Redfin has no page / no estimate for this parcel" is cached {v:0, conf:'nomatch'} — a
    fact worth 21 days of not re-probing. NO 'BLOCKED' sentinel ever.

JOIN GUARD: the resolved property's street number must match the lead's, and the lead's 5-digit zip
(when present) must appear in the Redfin slug / streetAddress.
  both match -> conf 'ok';  street only -> conf 'addr';  neither -> no-match.

RUN
    python redfin_value.py --limit 100          # cap live lookups (default 0 = unlimited)
    python redfin_value.py --ttl-days 21
    python redfin_value.py --folio 3032050040150 # single-lead debug (prints each step)
    python redfin_value.py --addr "1212 NE 91st St, Miami, FL 33138"  # raw-address debug
"""
import argparse
import asyncio
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import property_photos as pp          # reuse _addr_of
from listing_status import _folio     # identical folio-key logic as the Zillow pass

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, 'redfin_cache.json')
LEAD_FILES = ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36')

# In-page fetch of a stingray endpoint. Redfin prefixes JSON with `{}&&` (anti-JSON-hijack) — strip
# it. Returns the parsed payload, or None on non-200 / parse failure (a transport signal).
_JS_FETCH = """async (u) => {
  try {
    const r = await fetch(u, {headers:{'Accept':'*/*'}, credentials:'include'});
    const t = await r.text();
    return {status: r.status, body: t};
  } catch (e) { return {status: 0, body: String(e)}; }
}"""

_HOME_URL = re.compile(r'^/[A-Z]{2}/[^/]+/[^/]+/home/(\d+)$')


def _load_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        return json.load(open(CACHE_PATH, encoding='utf-8'))
    except Exception:
        return {}


def _save_cache(cache):
    tmp = CACHE_PATH + '.tmp'
    json.dump(cache, open(tmp, 'w', encoding='utf-8'), indent=0)
    os.replace(tmp, CACHE_PATH)


def _street_num(addr):
    m = re.match(r'\s*(\d+)', str(addr or ''))
    return m.group(1) if m else ''


def _zip5(addr):
    m = re.search(r'\b(\d{5})\b', str(addr or ''))
    return m.group(1) if m else ''


def _clean_addr(addr):
    # board addresses look like "1212 NE 91 ST, MIAMI, FL- 33138" — the "FL-" dash trips nothing in
    # autocomplete but normalize it anyway; autocomplete is fuzzy on "91 ST" vs "91st St".
    return re.sub(r'\bFL-\b', 'FL', str(addr or '')).strip()


def _strip_prefix(body):
    if body.startswith('{}&&'):
        body = body[4:]
    return body


async def _api(page, path):
    """One in-page stingray call -> parsed payload dict, or None on any failure."""
    res = await page.evaluate(_JS_FETCH, path)
    if res.get('status') != 200:
        return None
    try:
        j = json.loads(_strip_prefix(res.get('body') or ''))
    except Exception:
        return None
    return j.get('payload') if isinstance(j, dict) else None


async def _resolve(page, addr, debug=False):
    """autocomplete(addr) -> (propertyId, url, conf) or (None, None, 'nomatch').
    Raises RuntimeError on transport failure so the caller does NOT cache it."""
    import urllib.parse
    payload = await _api(page,
        '/stingray/do/location-autocomplete?location=' + urllib.parse.quote(_clean_addr(addr)) + '&v=2')
    if payload is None:
        raise RuntimeError('autocomplete transport failure')
    rows = []
    for sec in (payload.get('sections') or []):
        rows.extend(sec.get('rows') or [])
    em = payload.get('exactMatch')
    if em:
        rows.insert(0, em)
    if debug:
        print('  rows:', [(r.get('name'), r.get('url')) for r in rows[:4]])
    want_num, want_zip = _street_num(addr), _zip5(addr)
    best = None  # (conf_rank, pid, url, conf)
    for row in rows:
        url = row.get('url') or ''
        m = _HOME_URL.match(url)
        if not m:
            continue                              # a region/agent row, not a property
        pid = m.group(1)
        row_num = _street_num(row.get('name') or '')
        num_ok = want_num and row_num == want_num
        zip_ok = want_zip and (want_zip in url)
        if num_ok and (zip_ok or not want_zip):
            return pid, url, 'ok'
        if num_ok and best is None:
            best = (pid, url, 'addr')
    if best:
        return best
    return None, None, 'nomatch'


async def _avm(page, pid, addr, debug=False):
    """avm(propertyId) -> (value:int, sqft:int, conf_ok:bool). Raises on transport failure.
    conf_ok re-checks the property's own streetAddress against the lead (defense in depth: the avm
    endpoint returns SOME home for any id)."""
    payload = await _api(page,
        '/stingray/api/home/details/avm?propertyId=' + pid + '&accessLevel=1')
    if payload is None:
        raise RuntimeError('avm transport failure')
    pv = payload.get('predictedValue')
    if not pv:
        return 0, 0, False
    sa = payload.get('streetAddress') or {}
    sa_num = str(sa.get('streetNumber') or '')
    sa_zip = str(sa.get('zip') or '')
    want_num, want_zip = _street_num(addr), _zip5(addr)
    # Only DOWNGRADE on an affirmative contradiction. The avm payload often omits zip, so a missing
    # sa_zip must not count against a match the autocomplete already confirmed by street# + zip slug.
    conf_ok = bool(want_num) and sa_num == want_num and (not want_zip or not sa_zip or sa_zip == want_zip)
    sqft = 0
    sq = payload.get('sqFt') or {}
    if isinstance(sq, dict):
        sqft = int(sq.get('value') or 0)
    if debug:
        print('  avm predictedValue:', pv, '| streetAddr', sa_num, sa_zip, '| conf_ok', conf_ok)
    return int(round(float(pv))), sqft, conf_ok


async def lookup(page, addr, debug=False):
    """Full pipeline for one address. Returns a cache-entry dict, or None on transport failure."""
    try:
        pid, url, conf = await _resolve(page, addr, debug)
    except Exception as e:
        if debug:
            print('  resolve FAILED (not cached):', e)
        return None
    if not pid:
        return {'v': 0, 'conf': 'nomatch'}
    await asyncio.sleep(0.6 + random.random() * 0.5)
    try:
        val, sqft, avm_ok = await _avm(page, pid, addr, debug)
    except Exception as e:
        if debug:
            print('  avm FAILED (not cached):', e)
        return None
    if not val:
        return {'v': 0, 'conf': 'nomatch'}
    # downgrade confidence if the avm's own address disagrees with the autocomplete match
    final_conf = conf if avm_ok else 'addr'
    abs_url = url if url.startswith('http') else 'https://www.redfin.com' + url
    ent = {'v': val, 'url': abs_url, 'conf': final_conf}
    if sqft:
        ent['sqft'] = sqft
    return ent


def _worklist():
    """(folio, addr) across the three lead files, folio-deduped, addr-resolved."""
    seen, out = set(), []
    for fn in LEAD_FILES:
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            continue
        try:
            leads = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        for r in leads if isinstance(leads, list) else []:
            k = _folio(r)
            if not k or k in seen:
                continue
            addr = pp._addr_of(r)
            if not addr:
                continue
            seen.add(k)
            out.append((k, addr))
    return out


async def _prime(page):
    """Load redfin.com once so the WAF challenge is solved and the session cookie is set; every
    subsequent in-page stingray fetch then rides that same-origin session."""
    await page.goto('https://www.redfin.com/', wait_until='domcontentloaded', timeout=45000)
    await page.wait_for_timeout(2500)


async def _run_async(work, limit, ttl_s, debug=False):
    from playwright.async_api import async_playwright
    cache = _load_cache()
    now = time.time()
    budget = limit if limit > 0 else 10 ** 9
    fetched = hits = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = await browser.new_context(user_agent=UA, viewport={'width': 1280, 'height': 900})
        page = await ctx.new_page()
        try:
            await _prime(page)
        except Exception as e:
            print(f'redfin_value: could not reach redfin.com ({e}) — nothing fetched')
            await browser.close()
            return
        for k, addr in work:
            if budget <= 0:
                break
            ent = cache.get(k)
            if ent and (now - ent.get('t', 0)) < ttl_s:
                continue                          # TTL governs refresh, not display
            res = await lookup(page, addr, debug)
            budget -= 1
            fetched += 1
            if res is not None:                   # None = transport failure, never cached
                res['t'] = now
                cache[k] = res
                if res.get('v'):
                    hits += 1
            await asyncio.sleep(1.2 + random.random() * 0.8)
            if fetched % 25 == 0:
                _save_cache(cache)
                print(f'  ... {fetched} looked up, {hits} with an estimate')
        await browser.close()
    _save_cache(cache)
    have = sum(1 for e in cache.values() if e.get('v'))
    print(f'redfin_value: {fetched} looked up this run, {hits} new estimates; '
          f'{have} of {len(cache)} cached folios carry a value')


async def _debug_one(addr):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = await browser.new_context(user_agent=UA, viewport={'width': 1280, 'height': 900})
        page = await ctx.new_page()
        await _prime(page)
        print('addr:', addr)
        print('result:', await lookup(page, addr, debug=True))
        await browser.close()


def main():
    ap = argparse.ArgumentParser(description='Redfin Estimate sidecar for the DealFlow board.')
    ap.add_argument('--limit', type=int, default=0, help='max live lookups this run (0 = unlimited)')
    ap.add_argument('--ttl-days', type=float, default=21.0)
    ap.add_argument('--folio', help='debug: look up the single lead with this folio')
    ap.add_argument('--addr', help='debug: look up a raw address (bypasses the lead files)')
    a = ap.parse_args()

    if a.addr:
        asyncio.run(_debug_one(a.addr))
        return
    if a.folio:
        target = re.sub(r'\D', '', a.folio)
        for k, addr in _worklist():
            if k == target:
                print(f'folio {k} -> {addr}')
                asyncio.run(_debug_one(addr))
                return
        print('folio not found in lead files')
        return
    asyncio.run(_run_async(_worklist(), a.limit, a.ttl_days * 86400))


if __name__ == '__main__':
    main()
