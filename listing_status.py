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
  zest    : the Zillow Zestimate (AVM) in dollars, valid for ANY status incl. off-market (0 if
            the page carried no Zestimate). Free to grab — it rides the same homedetails HTML we
            already download. Feeds the board's advisory value cross-check alongside the Redfin
            Estimate; it never drives the deal math.

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
# The [{,] prefix anchor is load-bearing: the page also carries "rentZestimate":<num>, and an
# unanchored zestimate pattern's tail would match it. Same guard PAT_PRC uses.
PAT_ZEST = re.compile(r'[{,]\\*"zestimate\\*"\s*:\s*(\d+)')
# Listing-agent contact, off the SAME page already being fetched for zstatus — Zillow's
# attributionInfo block. Verified live 2026-08-05 against a real LISTED lead: agentName,
# agentPhoneNumber, brokerName all present; agentEmail present on some listings, null on others
# (agents commonly omit it so leads route through Zillow's own contact form instead).
PAT_AGENT = re.compile(r'agentName\\*"\s*:\s*\\*"([^"\\]+)')
PAT_AGENT_PHONE = re.compile(r'agentPhoneNumber\\*"\s*:\s*\\*"([^"\\]+)')
PAT_AGENT_EMAIL = re.compile(r'agentEmail\\*"\s*:\s*\\*"([^"\\]+)')
PAT_BROKER = re.compile(r'brokerName\\*"\s*:\s*\\*"([^"\\]+)')
# Real-listing proof + the authoritative pre-foreclosure flag. A genuine MLS listing carries an
# mlsid; Zillow's own pre-foreclosure/auction data pages do not, and Zillow states that fact
# directly rather than making us infer it from listingTypeDimension. See classify().
PAT_MLSID = re.compile(r'mlsid\\*"\s*:\s*\\*"([A-Za-z0-9\-]+)', re.I)
PAT_PREFC = re.compile(r'isPreforeclosureAuction\\*"\s*:\s*(true|false)', re.I)

TRUE_LISTING_TYPES = ('for sale by agent', 'for sale by owner', 'new construction', 'coming soon')


def _folio(r):
    return (re.sub(r'\D', '', str(r.get('folio') or r.get('Folio') or ''))
            or re.sub(r'[^a-z0-9]', '', str(r.get('case') or r.get('Case #') or '').lower()))


def _addr_from_folio_md(folio):
    """Resolve a property address from a Miami-Dade folio via the PA JSON API. Some auction rows
    ship with an empty Address ('MULTIPLE PARCELS' etc.) but a real folio — the PA knows the situs
    address for every folio, which unlocks the Zillow check for those leads. Returns '' on any
    failure. Same endpoint foreclosure_leads.py already uses for enrichment."""
    digits = re.sub(r'\D', '', str(folio or ''))
    if len(digits) < 10:
        return ''
    try:
        d = requests.get(
            'https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx',
            params={'Operation': 'GetPropertySearchByFolio', 'clientAppName': 'PropertySearch',
                    'folioNumber': digits},
            timeout=20).json()
        pi = d.get('PropertyInfo') or {}
        site = (d.get('SiteAddress') or [{}])
        site = site[0] if isinstance(site, list) and site else {}
        street = (site.get('Address') or pi.get('SiteAddress') or '').strip()
        city = (site.get('City') or '').strip()
        zipc = str(site.get('Zip') or '').strip()
        if not street:
            return ''
        return ', '.join(x for x in [street, city, 'FL'] if x) + (' ' + zipc if zipc else '')
    except Exception:
        return ''


def classify(home_status, listing_type, price, doz, mls_id='', agent='', prefc_auction=False):
    """Map raw Zillow fields to our zstatus label. Empty string = signal too thin to trust.

    mls_id / agent / prefc_auction added 2026-08-06 to fix a false OFF-MARKET (see below). All
    three default to empty/False so any older call site keeps its previous behavior."""
    lt = (listing_type or '').strip().lower()
    hs = (home_status or '').strip().upper()
    if hs == 'PENDING':
        return 'PENDING'
    if hs in ('RECENTLY_SOLD', 'SOLD'):
        return 'SOLD'
    if hs == 'FOR_RENT':
        return 'RENTAL'
    if hs == 'FOR_SALE':
        # Zillow's own pre-foreclosure/auction data pages carry homeStatus FOR_SALE as well, and
        # those are NOT real listings — that is what this branch guards against. It used to infer
        # that from listingTypeDimension being one of TRUE_LISTING_TYPES, which produced a silent
        # false negative: Zillow frequently emits "Unknown Listed By" on perfectly genuine MLS
        # listings. Caught live 2026-08-06 on 16298 90TH ST N, Loxahatchee — MLS# B26047763,
        # BeachesMLS, real listing agent, $699,999, 35 days on market — classified OFF-MARKET, which
        # also meant the Agent Outreach feature could never fire on it.
        # Zillow states the pre-foreclosure fact outright (isPreforeclosureAuction), so key off that
        # instead of guessing, and treat a real MLS id or a named listing agent as proof of a real
        # listing. Unknown-and-unattributed still falls through to OFF-MARKET, so the original
        # conservative default is preserved.
        if prefc_auction:
            return 'OFF-MARKET'
        if lt in TRUE_LISTING_TYPES:
            return 'LISTED'
        if (mls_id or '').strip() or (agent or '').strip():
            return 'LISTED'
        return 'OFF-MARKET'
    if hs in ('OTHER', 'OFF_MARKET'):
        return 'OFF-MARKET'
    return ''


def _blocked(text):
    """True when Zillow served a bot-wall instead of a real page (HTTP 200 CAPTCHA). A blocked
    response must NOT be classified — it says nothing about the property."""
    t = text[:4000].lower()
    return 'captcha' in t or 'px-captcha' in t or 'denied' in t or len(text) < 15000


def fetch_status(addr):
    """(zstatus, zprice, zdoz, zest, agent, agent_phone, agent_email, broker) for one address.
    '' status = TRANSIENT failure (blocked/timeout), retried next run. A clean search with no
    property match is NOT transient — Zillow indexes essentially every parcel, so no-match means
    no listing exists: classified OFF-MARKET so every reachable property ends up with a badge
    instead of a permanent hole. zest is the Zestimate off the same homedetails page (0 when the
    page carries none / on the no-match path). Agent fields are '' unless status is LISTED or
    PENDING — an off-market parcel has no active listing agent to report, and Zillow's own
    attribution block for it is usually just the last transaction's agent, which would be stale
    and misleading if surfaced here."""
    try:
        sess = requests.Session()
        sess.headers.update(pp._ZHDRS)
        url = 'https://www.zillow.com/homes/' + requests.utils.quote(addr) + '_rb/'
        r = sess.get(url, timeout=20)
        if r.status_code != 200 or _blocked(r.text):
            return '', 0, 0, 0, '', '', '', ''
        lm = re.search(r'https://www\.zillow\.com/homedetails/[^"\'<>\s]+/(\d+)_zpid/', r.text)
        if not lm:
            return 'OFF-MARKET', 0, 0, 0, '', '', '', ''
        h2 = dict(pp._ZHDRS)
        h2['Referer'] = url
        h2['Sec-Fetch-Site'] = 'same-origin'
        r2 = sess.get(lm.group(0), headers=h2, timeout=20)
        if r2.status_code != 200 or _blocked(r2.text):
            return '', 0, 0, 0, '', '', '', ''
        hs = PAT_HS.search(r2.text)
        ltd = PAT_LTD.search(r2.text)
        prc = PAT_PRC.search(r2.text)
        doz = PAT_DOZ.search(r2.text)
        zst = PAT_ZEST.search(r2.text)
        # Agent/MLS are parsed BEFORE classification now: they are inputs to it, not just outputs.
        # (Extracting them only after a LISTED verdict was circular — a genuine listing that
        # classify rejected could never produce the very agent data that proves it is genuine.)
        am = PAT_AGENT.search(r2.text)
        apm = PAT_AGENT_PHONE.search(r2.text)
        aem = PAT_AGENT_EMAIL.search(r2.text)
        bm = PAT_BROKER.search(r2.text)
        mlsm = PAT_MLSID.search(r2.text)
        pfm = PAT_PREFC.search(r2.text)
        agent = (am.group(1).strip() if am else '')[:80]
        agent_phone = (apm.group(1).strip() if apm else '')[:20]
        agent_email = (aem.group(1).strip() if aem else '')[:120]
        broker = (bm.group(1).strip() if bm else '')[:120]
        mls_id = (mlsm.group(1).strip() if mlsm else '')[:40]
        prefc_auction = bool(pfm and pfm.group(1).lower() == 'true')
        status = classify(hs.group(1) if hs else '', ltd.group(1) if ltd else '',
                          int(prc.group(1)) if prc else 0, int(doz.group(1)) if doz else 0,
                          mls_id=mls_id, agent=agent, prefc_auction=prefc_auction)
        # Page fetched cleanly but no recognizable homeStatus: that's Zillow's bare Zestimate
        # page shape for never-listed parcels — off-market, not unknown.
        if not status:
            status = 'OFF-MARKET'
        price = int(prc.group(1)) if prc else 0
        days = max(0, int(doz.group(1))) if doz else 0
        zest = int(zst.group(1)) if zst else 0
        # Agent contact only means something on a live listing. On anything else Zillow's
        # attribution block is usually the LAST sale's agent, which would be stale and misleading.
        if status not in ('LISTED', 'PENDING'):
            agent = agent_phone = agent_email = broker = ''
        # Price/days only meaningful for live retail states
        if status not in ('LISTED', 'PENDING'):
            price = price if status == 'SOLD' else 0
            days = 0
        return status, price, days, zest, agent, agent_phone, agent_email, broker
    except Exception:
        return '', 0, 0, 0, '', '', '', ''


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
            # Re-apply cached values in place (this is ALSO how county rows get zstatus/zest without
            # a county_leads.py change — the county scrape rebuilds the file, this pass re-stamps it).
            # ent.get('z',0) / ent.get('ag','') etc.: older cache entries predate these fields;
            # default to empty, no forced refetch — the 7-day TTL rolls them over naturally.
            if (r.get('zstatus') != ent['s'] or r.get('zprice') != ent.get('p', 0)
                    or r.get('zest', 0) != ent.get('z', 0)
                    or r.get('zagent', '') != ent.get('ag', '')):
                r['zstatus'], r['zprice'], r['zdoz'] = ent['s'], ent.get('p', 0), ent.get('d', 0)
                r['zest'] = ent.get('z', 0)
                r['zagent'], r['zagentphone'] = ent.get('ag', ''), ent.get('ap', '')
                r['zagentemail'], r['zbroker'] = ent.get('ae', ''), ent.get('br', '')
                changed += 1
            continue
        addr = pp._addr_of(r)
        if not addr:
            # Address missing on the auction row but a real folio exists -> ask the Miami-Dade
            # PA for the situs address (only meaningful for MD leads; BW/PB scrapers always
            # carry an address).
            addr = _addr_from_folio_md(r.get('Folio') or r.get('folio'))
            if not addr:
                # Verified unresolvable: no address on the auction row AND the county PA has no
                # situs address for the folio (raw land / MULTIPLE PARCELS bundles). Zillow can't
                # be checked without an address — mark honestly instead of leaving a hole that
                # reads as a glitch. Cached so the row doesn't re-probe the PA daily; the 7-day
                # TTL re-checks in case a later scrape run starts carrying the address.
                r['zstatus'], r['zprice'], r['zdoz'], r['zest'] = 'NO-ADDR', 0, 0, 0
                r['zagent'] = r['zagentphone'] = r['zagentemail'] = r['zbroker'] = ''
                cache[k] = {'s': 'NO-ADDR', 'p': 0, 'd': 0, 'z': 0, 't': now}
                changed += 1
                continue
        status, price, days, zest, agent, agent_phone, agent_email, broker = fetch_status(addr)
        fetched += 1
        limit_state['n'] -= 1
        if status:
            r['zstatus'], r['zprice'], r['zdoz'], r['zest'] = status, price, days, zest
            r['zagent'], r['zagentphone'] = agent, agent_phone
            r['zagentemail'], r['zbroker'] = agent_email, broker
            cache[k] = {'s': status, 'p': price, 'd': days, 'z': zest, 't': now,
                        'ag': agent, 'ap': agent_phone, 'ae': agent_email, 'br': broker}
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
