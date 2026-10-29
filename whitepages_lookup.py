"""Whitepages Pro API v2 — LAYERED enrichment.

Layer 1: /v2/property (address → all owners + residents + typed phones + emails + current city).
Layer 2: /v2/person   (owner name → aliases, address history, relatives, LinkedIn, is_dead flag,
                       and — most importantly — MORE phones that Property missed because Person
                       Search draws from a broader dataset). Fires automatically when Property
                       found no person owner (or only a different parcel's), on --relatives, or on
                       --deep. --upgrade adds it to cached leads with fewer than THIN_PHONES.

Rich sample (Velima 11410 NE 13 Ave): Property gave 12 phones on Jacob (absentee TX co-owner) +
2 on Velima. Person Search adds address history + relatives + LinkedIn, and typically 3-8 more
phones per name from records the property endpoint doesn't touch (workplace lines, historic
mobile, relatives-tagged numbers).

Parallelism: ThreadPoolExecutor(max_workers=CONCURRENCY, default 4). WP hasn't published a hard
per-second cap on Pro; 4 concurrent workers ≈ 10-15 lookups/sec while staying polite. Backs off
on 429 automatically. Cache writes serialized by a lock so parallel workers can't clobber.

Cache shape (backward-compatible with prior single-layer format):
  {case_id: {
    'result': {...property endpoint response...},        # Layer 1
    '_person': [{name, response},...],                    # Layer 2 (when run)
    '_http': 200 | 404,
    '_ts': <epoch>,
  }}

CLI:
  python whitepages_lookup.py --case 2025-016135-CA-01           # single lead, Property + auto Person
  python whitepages_lookup.py --all --limit 100                  # batch, Property + auto Person for thin
  python whitepages_lookup.py --all --limit 100 --deep           # Person Search on EVERY lead (2x cost)
  python whitepages_lookup.py --upgrade --limit 50               # re-scan cached leads with <5 phones, add Person layer
  python whitepages_lookup.py --gap --limit 50                   # person-owned: zero phones OR never WP-looked
  python whitepages_lookup.py --stats                            # cache size + phone-lift snapshot

Env:
  WP_MAX_CALLS_PER_RUN   hard cap per script invocation (default 200; script exits at cap)
  WP_KEY_FILE            key file path (default: whitepages.key)
  WP_CONCURRENCY         parallel workers (default 1 — trial keys 429 hard)
  WP_THROTTLE_S          delay between calls (default 0.6)
  WP_DAILY_CAP           dollars per calendar day across every run (default 5.00), Tracerfy-style
  WP_MAX_SPEND           dollars per run when --max-spend is not given (default 2.00)

Spend. Every request that reaches Whitepages is charged WP_EST_COST in bd_budget's shared ledger
under a 'wp' note, 404 misses included. Two ceilings, checked before each request: --max-spend for
this run, and WP_DAILY_CAP for the day. Whitepages used to share the BatchData cap (default $1.50,
about six lookups a day); it now has its own, the same way Tracerfy got one.

Who a number belongs to (owner / household / name-only / relative) is decided in wp_contacts.py.
"""
import argparse, glob as _glob, json, math, os, re, sys, time, threading, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE  = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
SKIPTRACE = os.path.join(HERE, 'skiptrace_results.json')   # BatchData/Tracerfy phones (gitignored)
CACHE = os.path.join(HERE, 'whitepages_lookup.json')
WP_IDS = os.path.join(HERE, 'wp_prop_ids.json')             # committed opaque /property/{id} map
KEY_F = os.path.join(HERE, os.environ.get('WP_KEY_FILE', 'whitepages.key'))
UA    = 'DealFlow/1.0 (+github.com/aiejandrog/foreclosure-leads)'
BASE  = 'https://api.whitepages.com/v2'
MAX_PER_RUN = int(os.environ.get('WP_MAX_CALLS_PER_RUN', '200'))
CONCURRENCY = max(1, int(os.environ.get('WP_CONCURRENCY', '1')))     # trial keys cap ~1 req/sec; verified 2026-07-24
THROTTLE_S  = float(os.environ.get('WP_THROTTLE_S', '0.6'))          # polite delay between calls (single-worker mode)
WP_DAILY_CAP = float(os.environ.get('WP_DAILY_CAP', '5') or 5)
WP_MAX_SPEND = float(os.environ.get('WP_MAX_SPEND', '2') or 2)
THIN_PHONES = 5                                     # Property returned <5 phones on top owner -> add Person
COMPANY_RE  = re.compile(r'\b(LLC|INC\b|CORP|COMPANY|CO\.|LTD|LP\b|LLP|ASSN|ASSOCIATION|CONDOMINIUM|CHURCH|TRUST|BANK|HOLDINGS)\b', re.I)
# wp_prop_ids.json stores hex-encoded ids; see normalize_prop_id() for why and for the decode rules.
_HEX_RE     = re.compile(r'^(?:[0-9a-fA-F]{2})+$')
_WP_ID_RE   = re.compile(r'^pro-[A-Za-z0-9_-]{4,}$')
# County → default city for Person Search when LP/upcoming has no property address yet
COUNTY_CITY = {
    'MIAMI-DADE': 'Miami', 'MIAMIDADE': 'Miami', 'MD': 'Miami',
    'BROWARD': 'Fort Lauderdale', 'BW': 'Fort Lauderdale',
    'PALM BEACH': 'West Palm Beach', 'PALM-BEACH': 'West Palm Beach',
    'PALMBEACH': 'West Palm Beach', 'PB': 'West Palm Beach',
}

_cache_lock = threading.Lock()
_progress_lock = threading.Lock()
_stats = {'ok': 0, 'miss': 0, 'err': 0, 'person': 0, 'person_miss': 0, 'phones_added': 0, 'rate_limited': 0}
_stop_run = threading.Event()   # set on sustained 429 wall OR budget exhaustion
_stop_why = {'why': ''}         # which one - the operator decision differs (top up WP vs raise the cap)
_spend_lock = threading.Lock()
_run = {'spent': 0.0, 'max': WP_MAX_SPEND, 'calls': 0, 'inflight': 0.0}


def wp_spent_today():
    """Dollars charged to Whitepages today in the shared ledger ('wp', 'wp-miss', ...)."""
    import bd_budget
    from datetime import date
    day = bd_budget._load().get(str(date.today()))
    if not isinstance(day, dict):
        return 0.0
    return sum(v for k, v in (day.get('by') or {}).items() if str(k).startswith('wp'))


def _require_wp(cost):
    """Reserve `cost` for one request, or raise BudgetExhausted when it would cross the run cap or
    the day cap. Requests already in flight count, so parallel workers cannot overshoot together.
    Call with _spend_lock held; every reservation ends in _charge_wp() or _release_wp()."""
    import bd_budget
    if _run['spent'] + _run['inflight'] + cost > _run['max'] + 1e-9:
        raise bd_budget.BudgetExhausted(
            f"run cap ${_run['max']:.2f} reached (${_run['spent']:.2f} spent this run). "
            f"Raise with --max-spend.")
    s = wp_spent_today() + _run['inflight']
    if s + cost > WP_DAILY_CAP + 1e-9:
        raise bd_budget.BudgetExhausted(
            f"Whitepages daily cap ${WP_DAILY_CAP:.2f} reached (${s:.2f} spent today). "
            f"Raise with env WP_DAILY_CAP.")
    _run['inflight'] = round(_run['inflight'] + cost, 4)


def _release_wp():
    """The request never reached billing (429, network error): give the reservation back."""
    with _spend_lock:
        _run['inflight'] = max(0.0, round(_run['inflight'] - WP_EST_COST, 4))


def _charge_wp(note):
    import bd_budget
    with _spend_lock:
        _run['inflight'] = max(0.0, round(_run['inflight'] - WP_EST_COST, 4))
        _run['spent'] = round(_run['spent'] + WP_EST_COST, 4)
        _run['calls'] += 1
        bd_budget.charge(WP_EST_COST, note)


def _load_key():
    if not os.path.exists(KEY_F):
        sys.exit(f'FATAL: no key at {KEY_F} (drop the WP Pro API key in that file, then rerun)')
    k = open(KEY_F).read().strip()
    if not k or len(k) < 20: sys.exit('FATAL: key file exists but looks empty/short')
    return k


def _lead_key(r):  return (r.get('Case #') or r.get('case') or '').strip()
def _lead_addr(r):
    """Property address, then mailing — LP nurture rows often only have mail later."""
    return (r.get('Address') or r.get('addr') or r.get('mail') or r.get('mailing_address') or '').strip()
def _lead_owners(r): return r.get('owners') or r.get('owner') or r.get('oname') or ''
def _lead_county(r): return (r.get('county') or r.get('County') or '').strip().upper()
def _lead_folio(r):  return (r.get('folio') or r.get('Folio') or '').strip()


def _parcel_verdict(lead, prop):
    """wp_contacts.wp_parcel_verdict for a /property response against this lead's folio."""
    import wp_contacts
    return wp_contacts.wp_parcel_verdict(((prop or {}).get('result') or {}).get('apn'), _lead_folio(lead))


def _county_city(r):
    c = _lead_county(r).replace('_', ' ')
    if c in COUNTY_CITY: return COUNTY_CITY[c]
    c2 = c.replace(' ', '').replace('-', '')
    return COUNTY_CITY.get(c2, 'Miami')


def load_all_leads():
    """Full Dealflow universe: MD board + Broward/PB + LP/upcoming (*_leads.json).

    Dedupes by case id — first win (leads_final, then alpha county files, then lp_leads).
    """
    seen, leads = set(), []
    def _add(rows, src):
        n = 0
        for r in rows:
            k = _lead_key(r)
            if not k or k in seen: continue
            seen.add(k); leads.append(r); n += 1
        return n
    if os.path.exists(LEADS):
        try: _add(json.load(open(LEADS, encoding='utf-8')), 'leads_final.json')
        except Exception as e: print(f'  skip leads_final.json: {e}', flush=True)
    for f in sorted(_glob.glob(os.path.join(HERE, '*_leads.json'))):
        bn = os.path.basename(f)
        if bn in ('leads_final.json', 'leads_raw.json') or bn.startswith('_'):
            continue
        try:
            _add(json.load(open(f, encoding='utf-8')), bn)
        except Exception as e:
            print(f'  skip {bn}: {e}', flush=True)
    return leads


def load_skiptrace():
    """BatchData/Tracerfy phone cache keyed by case #. Empty dict if missing."""
    if not os.path.exists(SKIPTRACE):
        return {}
    try:
        return json.load(open(SKIPTRACE, encoding='utf-8')) or {}
    except Exception:
        return {}


def _split_addr(addr):
    if not addr: return None
    parts = [p.strip() for p in addr.split(',')]
    if len(parts) < 2: return None
    return parts[0], parts[1], 'FL'


def _first_owner_name(owners_str):
    """Take the first name off the semicolon-list, skip company entries. Return None for pure LLCs."""
    for chunk in (owners_str or '').split(';'):
        chunk = chunk.strip()
        if not chunk: continue
        if COMPANY_RE.search(chunk):     # LLC / INC / CORP — Person Search won't help
            continue
        # 'LAST, FIRST M' or 'FIRST LAST'
        if ',' in chunk:
            l, _, f = chunk.partition(',')
            return (f.strip() + ' ' + l.strip()).strip()
        return chunk
    return None


# $/call. NOT an estimate: whitepages.com/pro-api/pricing lists Essentials at $0.22/query
# ($220/mo for 1,000, overages at $0.22). Verified 2026-08-15. The old 0.10 was copied from a .bat
# comment and understated the true cost by 2.2x, so bd_budget's shared daily ceiling was letting
# Whitepages spend roughly double its charged amount. Higher-volume tiers reach $0.15 at 2,500+/mo —
# raise this only against a plan we actually hold. WP_PRICE overrides it for a plan bought at another
# price (2026-10-29: a $100 / 500-search pack is 0.20), so --max-spend and the ledger read in real dollars.
WP_EST_COST = float(os.environ.get('WP_PRICE', '0.22') or 0.22)


def _http_get(url, key, retries=3):
    """One HTTP GET with X-Api-Key header. Returns parsed JSON on 200, {'_http':N} on 404, None on
    other errors. Retries on 429 with exponential backoff (WP Pro trial keys rate-limit hard).
    Exits on 401/403 (bad key). After retries exhausted, sets _stop_run so the batch exits cleanly.

    Every call is recorded in bd_budget's SHARED daily-dollar ledger (note 'wp' / 'wp-miss') and
    gated by Whitepages' own ceilings: --max-spend for the run and WP_DAILY_CAP for the day (see
    _require_wp). A request that REACHED the provider is charged (200 and 404 alike — a miss still
    bills); 429s and network errors never reached billing and are not charged. Other HTTP errors
    are treated as not billed, which is an assumption, not a documented Whitepages rule."""
    import bd_budget
    for attempt in range(retries + 1):
        if _stop_run.is_set():
            return None
        try:
            with _spend_lock:
                _require_wp(WP_EST_COST)
        except bd_budget.BudgetExhausted as e:
            _log(f'  BUDGET: {e}')
            _stop_why['why'] = _stop_why['why'] or 'spend cap reached'
            _stop_run.set()
            return None
        req = urllib.request.Request(url, headers={'X-Api-Key': key, 'User-Agent': UA})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            body = ''
            try: body = e.read().decode('utf-8', 'replace')[:200]
            except Exception: pass
            if e.code == 404:
                _charge_wp('wp-miss')
                return {'_http': 404, 'result': None}
            if e.code == 429:
                wait = min(120, 20 * (2 ** attempt))          # 20, 40, 80, 120
                # LOG THE BODY. It was read and then discarded on this one branch — nine straight
                # nights of first-call 429s produced zero evidence of whether WP meant "throttled"
                # (wait it out) or "quota exceeded" (dead plan, stop paying the retry tax). That
                # distinction is the entire top-up decision, and it was in the discarded bytes.
                _log(f'  RATE LIMIT (429) sleeping {wait}s (attempt {attempt + 1}/{retries + 1})'
                     + (f'  provider said: {body}' if body else ''))
                _stats['rate_limited'] += 1
                _release_wp()
                time.sleep(wait); continue
            _release_wp()
            if e.code in (401, 403):
                _log(f'  AUTH ERROR ({e.code}) — key rejected. Stopping. {body}')
                os._exit(2)
            _log(f'  HTTP {e.code}: {body}')
            return None
        except Exception as e:
            _release_wp()
            _log(f'  network error: {e}')
            return None
        _charge_wp('wp')                            # a 200 reached billing, parseable or not
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception as e:
            _log(f'  unparseable 200 from Whitepages (charged): {e}')
            return None
    _log('  RATE LIMIT: exhausted retries — pausing run (resume-safe; cache untouched for this lead)')
    _stats['rate_limited'] += 1
    _stop_why['why'] = _stop_why['why'] or '429 wall'
    _stop_run.set()
    return None


def property_lookup(street, city, state, key):
    q = urllib.parse.urlencode({'street': street, 'city': city, 'state_code': state})
    return _http_get(f'{BASE}/property?{q}', key)


def person_lookup(name, city, state, key):
    """Fuzzy + historical addresses ON to maximize recall. Returns the raw list response (may be empty)."""
    q = urllib.parse.urlencode({
        'name': name, 'city': city, 'state_code': state,
        'include_fuzzy_matching': 'true', 'include_historical_locations': 'true',
    })
    return _http_get(f'{BASE}/person?{q}', key)


def _log(msg):
    with _progress_lock:
        print(msg, flush=True)


def _save_cache(cache):
    # Write-then-rename. The nightly bake reads this file while a manual run may be writing it; a
    # half-written file made the bake print "WhitepagesPro merge skipped" and ship a board with no
    # Whitepages numbers at all.
    with _cache_lock:
        tmp = CACHE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=1)
        os.replace(tmp, CACHE)


def _count_phones_on_property(prop_response):
    r = (prop_response or {}).get('result') or {}
    ow = (r.get('ownership_info') or {}).get('person_owners') or []
    return sum(len(o.get('phones') or []) for o in ow)


def _owner_names_from_property(prop_response):
    r = (prop_response or {}).get('result') or {}
    ow = (r.get('ownership_info') or {}).get('person_owners') or []
    names = []
    for o in ow:
        n = o.get('name')
        if n and not COMPANY_RE.search(n): names.append(n)
    return names


def normalize_prop_id(pid):
    """wp_prop_ids.json -> the id property.whitepages.com actually serves.

    THE BUG THIS EXISTS FOR (2026-09-17). Every value in wp_prop_ids.json is stored as HEX-ENCODED
    ASCII, not as the id itself: "70726f2d52564d4b45706135587a4b" is the hex of "pro-RVMKEpa5XzK".
    Nothing in the repo ever decoded it, so make_tracker baked the hex blob onto the lead as
    wpPropId and the board's _wpPropUrl() shipped

        https://property.whitepages.com/property/70726f2d52564d4b45706135587a4b#owner

    on all 60 leads that have an id -- exactly the leads where the "direct" deep-link was supposed
    to beat the ?address= search fallback. The page does not exist, so the one link on the board
    advertised as landing ON the owner's Property Intel page landed nowhere.

    Decode is deliberately conservative: hex only, even length, and only when the decoded bytes are
    printable ASCII that looks like a Whitepages id ("pro-..."). A plain id is returned untouched,
    so this is safe to run over an already-correct map and safe to run twice (idempotent)."""
    pid = (pid or '').strip()
    if not pid or not _HEX_RE.match(pid):
        return pid
    try:
        dec = bytes.fromhex(pid).decode('ascii')
    except Exception:
        return pid
    return dec if _WP_ID_RE.match(dec) else pid


def _stamp_prop_id(entry, case):
    """Preserve / stamp opaque property.whitepages.com /property/{id} from wp_prop_ids.json."""
    if entry.get('_prop_id'):
        return
    try:
        ids = json.load(open(WP_IDS, encoding='utf-8')) if os.path.exists(WP_IDS) else {}
        pid = normalize_prop_id(ids.get(case) or '')
        if pid:
            entry['_prop_id'] = pid
    except Exception:
        pass


def _run_person_layer(lead, city, prop, entry, key, deep, force=False, relatives=False):
    """Layer 2 Person Search. Returns (person_records, phones_added, ran).

    relatives=True runs it even when Property found owners, because relatives only come back on a
    Person record. It searches ONE name, the property owner who clears wp_contacts' owner bar
    against our lead (else the lead's own first owner), so the extra cost is one call per lead."""
    ow_ct = len(((prop.get('result') or {}).get('ownership_info') or {}).get('person_owners') or [])
    # A /property record for a DIFFERENT parcel (APN != our folio; the lookup address was garbled)
    # names strangers. Searching their names paid for a stranger's phones and relatives, and its
    # "found owners" also suppressed the Person fallback that runs when Property finds nobody. So a
    # mismatched record counts as no owners, and the search uses our lead's own owner name.
    wrong_parcel = _parcel_verdict(lead, prop) == 'mismatch'
    if wrong_parcel:
        ow_ct = 0
    should_run = force or deep or relatives or (ow_ct == 0)
    person_records = list(entry.get('_person') or [])
    person_added_phones = 0
    if not should_run:
        return person_records, 0, False
    names = [] if wrong_parcel else _owner_names_from_property(prop)
    if relatives and not deep:
        import wp_contacts
        names = [n for n in names if wp_contacts.name_grade(_lead_owners(lead), n) == 'owner'][:1]
    if not names:
        n = _first_owner_name(_lead_owners(lead))
        if n: names.append(n)
    # FL tax-roll often stores LAST FIRST — flip for Person Search recall
    flipped = []
    for n in names:
        parts = n.split()
        if ',' not in n and len(parts) >= 2 and parts[0].isupper() and parts[1].isupper():
            flipped.append(' '.join(parts[1:] + [parts[0]]))
        else:
            flipped.append(n)
    names = flipped
    seen_names = {p.get('name', '').upper() for p in person_records}
    for name in names[:(1 if relatives and not deep else 2)]:
        if name.upper() in seen_names: continue
        if THROTTLE_S > 0: time.sleep(THROTTLE_S)
        pr = person_lookup(name, city, 'FL', key)
        if pr is None: continue
        recs = pr if isinstance(pr, list) else []
        existing_digits = set()
        for o in ((prop.get('result') or {}).get('ownership_info') or {}).get('person_owners') or []:
            for p in (o.get('phones') or []):
                d = ''.join(c for c in (p.get('number') or '') if c.isdigit())
                if len(d) >= 10: existing_digits.add(d[-10:])
        for p in person_records:
            for phone in (p.get('response') or [{}])[0].get('phones') or []:
                d = ''.join(c for c in (phone.get('number') or '') if c.isdigit())
                if len(d) >= 10: existing_digits.add(d[-10:])
        for rec in recs:
            for phone in (rec.get('phones') or []):
                d = ''.join(c for c in (phone.get('number') or '') if c.isdigit())
                if len(d) >= 10 and d[-10:] not in existing_digits:
                    person_added_phones += 1
                    existing_digits.add(d[-10:])
        person_records.append({'name': name, 'response': recs, '_ts': int(time.time())})
        if recs:
            _stats['person'] += 1
        else:
            _stats['person_miss'] += 1
    return person_records, person_added_phones, True


def enrich_one(lead, key, deep, cache, relatives=False):
    """One lead: Property + (auto-fallback) Person layer. Writes to cache incrementally.

    Address-less LP/upcoming rows still get Person Search using owner name + county city —
    nurture lane needs numbers before a sale date or folio lands.
    """
    if _stop_run.is_set():
        return
    case = _lead_key(lead)
    parts = _split_addr(_lead_addr(lead))
    entry = cache.get(case) or {}

    if not parts:
        # No street/city — Person-only path (common for fresh Lis Pendens)
        name = _first_owner_name(_lead_owners(lead))
        if not name:
            _log(f'  SKIP {case:24s} no address + no person owner')
            _stats['err'] += 1
            return
        city = _county_city(lead)
        person_only = True
        street = f'(person-only/{city})'
        prop = {'result': entry.get('result'), '_http': entry.get('_http') or 0}
        if THROTTLE_S > 0: time.sleep(THROTTLE_S)
        person_records, person_added_phones, _ran = _run_person_layer(
            lead, city, prop, entry, key, deep, force=True, relatives=relatives)
        entry['_person'] = person_records
        entry['_http'] = entry.get('_http') or 0
        entry['_ts'] = int(time.time())
        entry['_person_only'] = True
        _stamp_prop_id(entry, case)
        _stats['phones_added'] += person_added_phones
        with _cache_lock:
            cache[case] = entry
        _save_cache(cache)
        if person_records and any(p.get('response') for p in person_records):
            _log(f'  ok   {case:24s} {street:32s} -> Person-only: +{person_added_phones} phones')
            _stats['ok'] += 1
        else:
            _log(f'  miss {case:24s} {street:32s} (no WP person record)')
            _stats['miss'] += 1
        return

    street, city, state = parts
    if THROTTLE_S > 0: time.sleep(THROTTLE_S)

    # Layer 1: Property
    prop = entry.get('result') and {'result': entry.get('result'), '_http': entry.get('_http')} \
        or property_lookup(street, city, state, key)
    if prop is None:
        _stats['err'] += 1; return

    ph = _count_phones_on_property(prop)
    ow_ct = len(((prop.get('result') or {}).get('ownership_info') or {}).get('person_owners') or [])
    entry['result'] = prop.get('result')
    entry['_http']  = prop.get('_http', 200)
    entry['_ts']    = int(time.time())
    if _parcel_verdict(lead, prop) == 'mismatch':
        # Kept in the cache (it was paid for, and the bake refuses it by APN). Counts only.
        _log(f'  note {case:24s} Property record is a different parcel (APN != folio); '
             f'its owners are ignored, Person search uses our owner name')
        ow_ct = 0

    # Layer 2: Person (auto on Property MISS / --deep). Skip re-search when Property already
    # returned owners — same phones, wastes trial quota (verified 2026-07-24).
    person_records, person_added_phones, should_run_person = _run_person_layer(
        lead, city, prop, entry, key, deep, force=False, relatives=relatives)
    entry['_person'] = person_records
    _stamp_prop_id(entry, case)
    _stats['phones_added'] += person_added_phones

    with _cache_lock:
        cache[case] = entry
    _save_cache(cache)

    if ow_ct > 0 or person_records:
        person_note = f' + Person: +{person_added_phones} more' if person_added_phones else \
                      (f' + Person: 0 new' if should_run_person else '')
        _log(f'  ok   {case:24s} {street[:32]:32s} -> Property: {ow_ct} owner(s), {ph} phones{person_note}')
        _stats['ok'] += 1
    else:
        _log(f'  miss {case:24s} {street[:32]:32s} (no WP record on address or name)')
        _stats['miss'] += 1


def _phone_list_count(phones):
    """Count dialable 10-digit numbers in a phones list (dicts or bare strings)."""
    n = 0
    for p in phones or []:
        raw = p.get('number') if isinstance(p, dict) else str(p)
        d = ''.join(c for c in str(raw or '') if c.isdigit())
        if len(d) == 11 and d.startswith('1'): d = d[1:]
        if len(d) == 10: n += 1
    return n


def _lead_phone_count(r, skiptrace=None):
    """Dialable phones already known for this lead.

    Source of truth for the live board is skiptrace_results.json (merged at bake), NOT leads_final.json
    which never carries a phones field. County *_leads.json may already have phones baked in — check
    those as a fallback after skiptrace.
    """
    k = _lead_key(r)
    if skiptrace is not None and k:
        hit = skiptrace.get(k) or {}
        n = _phone_list_count(hit.get('phones'))
        if n > 0:
            return n
    return _phone_list_count(r.get('phones'))


def _is_company_owner(owners_str):
    """True only when every owner chunk is a company (no person name extractable)."""
    return _first_owner_name(owners_str) is None


def _cache_looked(entry):
    """True when we actually called WP (property result and/or person layer), not a bare prop-id stamp."""
    if not entry: return False
    if entry.get('result') is not None or entry.get('_http') in (200, 404):
        return True
    if entry.get('_person'):
        return True
    return False


def _is_miami(r):
    c = re.sub(r'[^A-Z]', '', _lead_county(r))
    if c:
        return c.startswith('MIAMI') or c == 'MD'
    return bool(re.match(r'^\d{4}-\d{6}-CA-\d{2}$', _lead_key(r)))   # Miami-Dade civil case form


def contact_hold(r):
    """Why no money should be spent on this lead's contacts, or ''.

    Paying for numbers we are not allowed to use is waste, so the same holds the board and the send
    bridge apply are read here before a request goes out: a never-contact case (stay_gate), a
    bankruptcy stay flag on the lead, a dismissed or closed case, an owner mismatch. This only
    READS those verdicts; it does not decide any of them. An unreadable never-contact list holds."""
    if r.get('sale_bk_active') or r.get('saleBkAct') or r.get('bkWhy'):
        return 'bankruptcy stay flag'
    if r.get('lpDismissed') or r.get('lpClosed') or r.get('ownerMismatch'):
        return 'dismissed/closed/owner mismatch'
    try:
        import stay_gate
        if stay_gate.never_contact(_lead_key(r)):
            return 'never-contact list'
    except ImportError:
        return 'stay_gate not importable'
    except Exception as e:
        return 'never-contact list unreadable (%s)' % str(e)[:60]
    return ''


def _days_to_sale(r):
    try:
        return int(r.get('days_to_auction') if r.get('days_to_auction') is not None else r.get('days'))
    except (TypeError, ValueError):
        return 9999


def pick_miami(leads, cache, n):
    """The n Miami-Dade leads this test should spend on: person-owned, with a property address,
    no contact hold, not already Person-searched, soonest sale first."""
    out, held = [], 0
    for r in leads:
        if not _is_miami(r) or not _split_addr(_lead_addr(r)):
            continue
        if _is_company_owner(_lead_owners(r)):
            continue
        if (cache.get(_lead_key(r)) or {}).get('_person'):
            continue
        if contact_hold(r):
            held += 1
            continue
        out.append(r)
    out.sort(key=lambda r: (_days_to_sale(r), _lead_key(r)))
    _log(f'  --pick-miami: {len(out)} eligible, {held} skipped for a contact hold; taking {min(n, len(out))}')
    return out[:n]


PICK_MAX_DAYS = int(os.environ.get('WP_PICK_MAX_DAYS', '60'))


def _gated(leads, cache, max_days, tag):
    """Leads a paid deep search may spend on, any county: person-owned, a property address, no
    contact hold, never Person-searched, a sale inside max_days (or a lis pendens with no sale yet).
    Then the gates skiptrace applies before it spends (10-18): a §362 stay from the docket cache or
    PACER, an outreach hold, a closed case, and Do Not Contact / opt-out from the ledger and Call
    Mode notes. Read only. If they cannot be read, nothing is bought: an unreadable gate is not an
    open one. -> (eligible, held_count)"""
    out, held = [], 0
    for r in leads:
        k = _lead_key(r)
        if not k or not _split_addr(_lead_addr(r)) or _is_company_owner(_lead_owners(r)):
            continue
        if (cache.get(k) or {}).get('_person'):
            continue
        # a one-parcel LP row's addr is a name-matched parcel (lp_leads addrConf=medium): a paid
        # search there buys the parcel's residents, not the owner's file
        if r.get('addrConf') == 'medium':
            continue
        d = _days_to_sale(r)
        if d < 0 or (d != 9999 and d > max_days):
            continue
        if contact_hold(r):
            held += 1
            continue
        out.append(r)
    if out:
        try:
            import skiptrace as _ST
            _idx = _ST.exclusion_index(out, leads)
            kept = [r for r in out if not _ST.exclusion_reasons(r, _idx)]
        except Exception as e:
            _log(f'  --{tag}: contact gates unreadable ({str(e)[:80]}); buying nothing')
            kept = []
        held += len(out) - len(kept)
        out = kept
    return out, held


def pick_callable(leads, cache, n, skiptrace=None, max_days=None):
    """The n leads the nightly deep search should spend on (see _gated for who qualifies). Fewest
    known phones first inside the same week of sale, then soonest sale: the lead with two dead
    numbers gains more from a deep file than the one with ten. Jesse's method (owner, the
    household, relatives) on the leads that are about to be called, inside --max-spend and
    WP_DAILY_CAP."""
    max_days = PICK_MAX_DAYS if max_days is None else max_days
    skiptrace = skiptrace if skiptrace is not None else {}
    out, held = _gated(leads, cache, max_days, 'pick-callable')
    out.sort(key=lambda r: (_days_to_sale(r) // 7, min(_lead_phone_count(r, skiptrace), 5),
                            _days_to_sale(r), _lead_key(r)))
    _log(f'  --pick-callable: {len(out)} eligible (sale within {max_days}d or undated), '
         f'{held} skipped for a contact hold; taking {min(n, len(out))}')
    return out[:n]


PICK_BEST_MAX_DAYS = int(os.environ.get('WP_PICK_BEST_MAX_DAYS', '365'))
EQ_FACT = ('clear', 'priced')           # equity_state: the only states that are a fact


def _skip_miss(hit):
    """The skip trace ran on this lead and came back with no phone (not an error, not a skip)."""
    if not isinstance(hit, dict) or hit.get('outcome'):
        return False
    return _phone_list_count(hit.get('phones')) == 0


def _board_equity(path=None):
    """case -> True when the board twin says the equity is a fact (clear/priced and above 0).
    {} when the twin cannot be read: then equity just does not reorder anything."""
    try:
        import paths as _P
        from foreclosure_report import load_board
        out = {}
        for r in load_board(path or _P.TWIN):
            try:
                out[r.get('case')] = r.get('eqstate') in EQ_FACT and float(r.get('eq') or 0) > 0
            except (TypeError, ValueError):
                pass
        return out
    except Exception as e:
        _log(f'  --pick-best: board twin unreadable ({e.__class__.__name__}); equity not used to order')
        return {}


def pick_best(leads, cache, n, skiptrace=None, max_days=None, equity=None):
    """The n leads a one-off bought block of searches should go to (2026-10-29, Alex's $100 / 500).
    Same eligibility and gates as the nightly pick (_gated), any sale inside max_days (default a year).
    Order: the skip trace ran and found nothing, then leads with no number at all, then everyone
    else by fewest numbers; inside each, verified equity first, then soonest sale."""
    max_days = PICK_BEST_MAX_DAYS if max_days is None else max_days
    skiptrace = skiptrace if skiptrace is not None else {}
    equity = _board_equity() if equity is None else equity
    out, held = _gated(leads, cache, max_days, 'pick-best')

    def tier(r):
        if _skip_miss(skiptrace.get(_lead_key(r))):
            return 0
        return 1 if _lead_phone_count(r, skiptrace) == 0 else 2
    out.sort(key=lambda r: (tier(r), min(_lead_phone_count(r, skiptrace), 5),
                            0 if equity.get(_lead_key(r)) else 1, _days_to_sale(r), _lead_key(r)))
    tiers = [tier(r) for r in out[:n]]
    _log(f'  --pick-best: {len(out)} eligible (sale within {max_days}d or undated), {held} skipped for '
         f'a contact hold; taking {min(n, len(out))}: {tiers.count(0)} skip-trace miss, '
         f'{tiers.count(1)} no number, {tiers.count(2)} with numbers; '
         f'{sum(1 for r in out[:n] if equity.get(_lead_key(r)))} with verified equity')
    return out[:n]


def build_todo(leads, cache, args, skiptrace=None):
    """Return the (deduped, skip-LLC when applicable) work list, respecting --tier / --refresh / --upgrade."""
    skiptrace = skiptrace if skiptrace is not None else {}
    todo = []
    if getattr(args, 'pick_miami', 0):
        return pick_miami(leads, cache, args.pick_miami)
    if getattr(args, 'pick_callable', 0):
        return pick_callable(leads, cache, args.pick_callable, skiptrace)
    if getattr(args, 'pick_best', 0):
        return pick_best(leads, cache, args.pick_best, skiptrace)
    if args.case:
        by = {_lead_key(r): r for r in leads}
        out = []
        for c in args.case:
            if c not in by: sys.exit(f'case {c} not on the board')
            why = contact_hold(by[c])
            if why:
                _log(f'  SKIP {c}: {why} — not spending on it')
                continue
            out.append(by[c])
        return out
    if getattr(args, 'gap', False):
        # (a) person-owned + zero phones (skiptrace/county), OR (b) person-owned + never WP-looked.
        # Zero-phone first (highest need), then never-looked that already have some BatchData phones.
        # Includes LP/upcoming with no sale date — nurture lane. Address-less rows still queue
        # (Person-only path in enrich_one).
        skipped_co = skipped_ok = skipped_cached = 0
        zero, never = [], []
        for r in leads:
            k = _lead_key(r)
            if not k: continue
            if args.tier and (r.get('tier') or '') != args.tier: continue
            if _cache_looked(cache.get(k)) and not args.refresh:
                skipped_cached += 1; continue
            if _is_company_owner(_lead_owners(r)):
                skipped_co += 1; continue
            ph = _lead_phone_count(r, skiptrace)
            if ph == 0:
                zero.append(r)
            else:
                # never-looked (no real WP result) even if skiptrace has phones
                never.append(r)
        # Prefer actionable Property lookups (have street+city) ahead of person-only
        def _sort_key(r):
            return (0 if _split_addr(_lead_addr(r)) else 1, _lead_key(r))
        zero.sort(key=_sort_key)
        never.sort(key=_sort_key)
        todo = zero + never
        _log(f'  --gap: {len(todo)} person-owned to enrich '
             f'({len(zero)} zero-phone, {len(never)} never-looked w/ phones)'
             + f' — skipped {skipped_co} companies'
             + (f', {skipped_cached} already in WP cache' if skipped_cached else ''))
        return todo
    if args.upgrade:
        # re-scan cached leads with thin Property AND no Person layer yet
        by = {_lead_key(r): r for r in leads}
        for case, entry in cache.items():
            if case not in by: continue
            if entry.get('_person'): continue                        # already has Person layer
            ph = _count_phones_on_property(entry)
            wrong = _parcel_verdict(by[case], entry) == 'mismatch'   # its phones are a stranger's
            if ph >= THIN_PHONES and not wrong: continue             # already good coverage
            todo.append(by[case])
        return todo
    if args.all:
        skipped_co = 0
        for r in leads:
            k = _lead_key(r)
            if not k: continue
            if args.tier and (r.get('tier') or '') != args.tier: continue
            if _cache_looked(cache.get(k)) and not args.refresh: continue
            if _is_company_owner(_lead_owners(r)):
                skipped_co += 1; continue
            todo.append(r)
        if skipped_co: _log(f'  (skipped {skipped_co} pure-company leads — WP Property is human-owner-only)')
        return todo
    return []


def lead_contacts(lead, entry):
    """Grade one cache entry against one lead -> {owner, household, name_only, dropped, relatives}.
    The same rules make_tracker bakes with (wp_contacts), so this report is what the board shows."""
    import wp_contacts as W
    owners, addr = _lead_owners(lead), _lead_addr(lead)
    res = (entry or {}).get('result') or {}
    out = {'owner': set(), 'household': set(), 'name_only': set(), 'dropped': 0,
           'relatives': 0, 'relative_phones': 0}
    for o in ((res.get('ownership_info') or {}).get('person_owners') or []):
        tag = W.grade_property_owner(owners, o.get('name'))
        for p in W._phones_of(o):
            (out['owner'] if tag == 'wp' else out['household']).add(p['n'])
    for o in (res.get('residents') or []):
        for p in W._phones_of(o):
            out['household'].add(p['n'])
    for pr in ((entry or {}).get('_person') or []):
        for rec in (pr.get('response') or []):
            g = W.grade_person_record(owners, addr, rec)
            phs = [p['n'] for p in W._phones_of(rec)]
            if g is None:
                out['dropped'] += len(phs)
            else:
                (out['owner'] if g == 'wp' else out['name_only']).update(phs)
    out['household'] -= out['owner']
    out['name_only'] -= out['owner'] | out['household']
    rels = W.extract_relatives(owners, addr, (entry or {}).get('_person') or [],
                               out['owner'] | out['household'] | out['name_only'])
    out['relatives'] = len(rels)
    out['relative_phones'] = sum(len(r['phones']) for r in rels)
    return out


def report(leads, cache, cases, skiptrace=None, lines=True):
    """One line per lead (lines=True), then totals: counts only. Safe to paste into chat. A property
    record on a different parcel (APN gate) counts for its Person half only, as the bake does."""
    by = {_lead_key(r): r for r in leads}
    skiptrace = skiptrace if skiptrace is not None else {}
    if lines:
        print('\nper-lead result (counts only):')
        print('  case                      owner  household  name-only  dropped-namesake  relatives(with #)')
    t = {'looked': 0, 'any': 0, 'owner_leads': 0, 'owner_new': 0, 'new_owner_leads': 0, 'household': 0,
         'name_only': 0, 'had_none_now_owner': 0}
    for c in cases:
        r, e = by.get(c), cache.get(c)
        if not r or not e:
            if lines: print(f'  {c:24s}  not looked up')
            continue
        if _parcel_verdict(r, e) == 'mismatch':
            e = {k: v for k, v in e.items() if k != 'result'}
        k = lead_contacts(r, e)
        if lines:
            print(f"  {c:24s}  {len(k['owner']):5d}  {len(k['household']):9d}  {len(k['name_only']):9d}  "
                  f"{k['dropped']:16d}  {k['relatives']:3d} ({k['relative_phones']} #)")
        known = set()
        for src in (r.get('phones'), (skiptrace.get(c) or {}).get('phones')):
            for p in src or []:
                d = ''.join(ch for ch in str(p.get('number') if isinstance(p, dict) else p) if ch.isdigit())
                if len(d) >= 10: known.add(d[-10:])
        new_owner = k['owner'] - known
        t['looked'] += 1
        t['any'] += bool(k['owner'] or k['household'] or k['name_only'])
        t['owner_leads'] += bool(k['owner'])
        t['owner_new'] += len(new_owner)
        t['new_owner_leads'] += bool(new_owner)
        t['household'] += len(k['household'])
        t['name_only'] += len(k['name_only'])
        t['had_none_now_owner'] += bool(not known and k['owner'])
    print(f"\nTOTALS: {t['looked']} lead(s) looked up · {t['any']} got any number · "
          f"{t['owner_leads']} got an owner-graded number")
    print(f"        {t['owner_new']} owner number(s) we did not have, on {t['new_owner_leads']} lead(s) · "
          f"{t['had_none_now_owner']} lead(s) went from no number to an owner number")
    print(f"        {t['household']} household number(s) · {t['name_only']} name-only (unconfirmed) number(s)")


def schema(cache):
    """Which keys Whitepages actually returns, so the relatives key is confirmed, not guessed."""
    from collections import Counter
    top, rec_keys, rel_keys = Counter(), Counter(), Counter()
    for e in cache.values():
        top.update((e.get('result') or {}).keys())
        for pr in e.get('_person') or []:
            for rec in pr.get('response') or []:
                if isinstance(rec, dict):
                    rec_keys.update(rec.keys())
                    for k in ('relatives', 'associated_people', 'related_people', 'relations'):
                        for rel in rec.get(k) or []:
                            if isinstance(rel, dict):
                                rel_keys.update(k + '.' + kk for kk in rel.keys())
    print('property result keys:', dict(top.most_common()))
    print('person record keys:  ', dict(rec_keys.most_common()))
    print('relative entry keys: ', dict(rel_keys.most_common()) or 'none cached yet')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', action='append', help='lead by ID (repeatable)')
    ap.add_argument('--pick-miami', type=int, default=0, metavar='N',
                    help='the N soonest-sale Miami-Dade person-owned leads with no contact hold')
    ap.add_argument('--pick-callable', type=int, default=0, metavar='N',
                    help='the N leads about to be called (any county, sale within WP_PICK_MAX_DAYS, '
                         'fewest phones first) with no contact hold and no Person search yet')
    ap.add_argument('--pick-best', type=int, default=0, metavar='N',
                    help='a bought block of searches: skip-trace misses, then no-number leads, then the '
                         'rest, verified equity and soonest sale first (sale within WP_PICK_BEST_MAX_DAYS)')
    ap.add_argument('--estimate', action='store_true',
                    help='print what a full --pick-callable --relatives sweep would cost, $0')
    ap.add_argument('--relatives', action='store_true',
                    help="also run Person Search on the matched owner to get relatives (+1 call/lead)")
    ap.add_argument('--max-spend', type=float, default=None, metavar='USD',
                    help=f'dollar cap for this run (default env WP_MAX_SPEND = {WP_MAX_SPEND:.2f})')
    ap.add_argument('--dry-run', action='store_true', help='show what would be looked up and the cost')
    ap.add_argument('--report', action='store_true', help='per-lead counts for --case, no names/numbers')
    ap.add_argument('--schema', action='store_true', help='key names in cached WP records, no values')
    ap.add_argument('--all', action='store_true', help='every uncached lead (respects --limit)')
    ap.add_argument('--upgrade', action='store_true', help='re-scan cached leads with <5 phones + no Person layer yet')
    ap.add_argument('--gap', action='store_true',
                    help='person-owned: zero phones (skiptrace) OR never WP-looked; includes LP/upcoming')
    ap.add_argument('--deep', action='store_true', help='force Person Search on every lead (2x cost)')
    ap.add_argument('--limit', type=int, default=50, help='per-run cap (default 50, env WP_MAX_CALLS_PER_RUN overrides)')
    ap.add_argument('--refresh', action='store_true', help='re-fetch even cached leads')
    ap.add_argument('--stats', action='store_true', help='cache stats + phone lift snapshot')
    ap.add_argument('--tier', default='', help='only leads at this tier (A/B/C)')
    ap.add_argument('--ensure-prop-ids', action='store_true',
                    help='stamp wp_prop_ids.json into cache (Marisela etc.) then exit')
    args = ap.parse_args()

    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    leads = load_all_leads()
    skiptrace = load_skiptrace()
    print(f'universe: {len(leads)} unique leads · skiptrace: {len(skiptrace)} · wp cache: {len(cache)}', flush=True)

    # Always re-stamp committed prop ids (Marisela) so a later cache write can't drop them.
    try:
        ids = json.load(open(WP_IDS, encoding='utf-8')) if os.path.exists(WP_IDS) else {}
    except Exception:
        ids = {}
    stamped = 0
    for case, pid in (ids or {}).items():
        if not case or case.startswith('addr:') or not pid: continue
        pid = normalize_prop_id(pid)            # hex map value -> the id WP actually serves
        entry = cache.get(case) or {}
        if entry.get('_prop_id') != pid:
            entry['_prop_id'] = pid
            cache[case] = entry
            stamped += 1
    if stamped:
        _save_cache(cache)
        print(f'stamped {stamped} wp_prop_id(s) into cache (Marisela preserved)', flush=True)
    if args.ensure_prop_ids and not (args.gap or args.all or args.upgrade or args.case or args.stats
                                     or args.pick_miami or args.pick_callable or args.pick_best
                                     or args.estimate
                                     or args.report or args.schema):
        return

    if args.stats:
        by = {_lead_key(r): r for r in leads}
        covered = sum(1 for k in cache if k in by)
        with_person = sum(1 for v in cache.values() if v.get('_person'))
        wp_ok = sum(1 for v in cache.values()
                    if ((v.get('result') or {}).get('ownership_info') or {}).get('person_owners')
                    or any((p.get('response') or []) for p in (v.get('_person') or [])))
        gained = 0
        for k, v in cache.items():
            r = by.get(k)
            if not r: continue
            wp = set()
            for own in ((v.get('result') or {}).get('ownership_info') or {}).get('person_owners') or []:
                for p in (own.get('phones') or []):
                    d = ''.join(c for c in (p.get('number') or '') if c.isdigit())
                    if len(d) >= 10: wp.add(d[-10:])
            for pr in (v.get('_person') or []):
                for rec in (pr.get('response') or []):
                    for p in (rec.get('phones') or []):
                        d = ''.join(c for c in (p.get('number') or '') if c.isdigit())
                        if len(d) >= 10: wp.add(d[-10:])
            gained += len(wp)
        # Same rules as --gap: zero-phone OR never-looked, person-owned
        gap_zero = gap_never = 0
        for r in leads:
            k = _lead_key(r)
            if not k or _is_company_owner(_lead_owners(r)): continue
            if _cache_looked(cache.get(k)): continue
            if _lead_phone_count(r, skiptrace) == 0: gap_zero += 1
            else: gap_never += 1
        print(f'cache: {len(cache)} entries · {covered} match live leads · {wp_ok} wpKey=ok · {with_person} with Person layer')
        print(f'total unique WP phones across cache: {gained}')
        print(f'--gap remaining: {gap_zero + gap_never} ({gap_zero} zero-phone, {gap_never} never-looked w/ phones)')
        return

    if args.estimate:
        elig = pick_callable(leads, cache, 10 ** 9, skiptrace)
        uncached = sum(1 for r in elig if not (cache.get(_lead_key(r)) or {}).get('result'))
        calls = len(elig) + uncached                    # one Person call each + Property where not cached
        print(f'full deep sweep: {len(elig)} lead(s), {uncached} need a Property call too -> up to '
              f'{calls} calls = ${calls * WP_EST_COST:.2f} at ${WP_EST_COST:.2f}/query '
              f'(price from 2026-08-15, check it against the plan). At the ${WP_DAILY_CAP:.2f} day cap: '
              f'{math.ceil(calls * WP_EST_COST / WP_DAILY_CAP) if WP_DAILY_CAP > 0 else "n/a"} day(s).')
        return
    if args.report:
        report(leads, cache, args.case or [])
        return
    if args.schema:
        schema(cache)
        return

    if args.max_spend is not None:
        _run['max'] = float(args.max_spend)
    todo = build_todo(leads, cache, args, skiptrace=skiptrace)
    if not args.case and not args.pick_miami and not args.pick_callable and not args.pick_best:
        _before = len(todo)
        todo = [r for r in todo if not contact_hold(r)]
        if len(todo) != _before:
            _log(f'  skipped {_before - len(todo)} lead(s) with a contact hold (stay / never-contact / closed)')
    cap = min(args.limit, MAX_PER_RUN)
    todo = todo[:cap]
    if not todo: print('nothing to do.'); return

    per_lead = 1 if not (args.deep or args.relatives) else (2 if args.relatives and not args.deep else 3)
    est_calls = len(todo) * per_lead                                 # worst case; cached layers cost 0
    # est at WP_EST_COST, not the retired $0.10 - the banner understated real exposure 2.2x
    print(f'{len(todo)} lookup(s) queued (concurrency={CONCURRENCY}, throttle={THROTTLE_S}s, deep={args.deep}, '
          f'relatives={args.relatives}, up to {est_calls} API calls = ${est_calls*WP_EST_COST:.2f} at '
          f'${WP_EST_COST:.2f}/query)', flush=True)
    print(f'caps: this run ${_run["max"]:.2f} (--max-spend) · today ${wp_spent_today():.2f} spent of '
          f'${WP_DAILY_CAP:.2f} (WP_DAILY_CAP)', flush=True)
    if args.dry_run:
        for r in todo:
            print(f'  would look up: {_lead_key(r)}  (sale in {_days_to_sale(r)}d)')
        print('(dry run — no API calls made)')
        return
    key = _load_key()

    t0 = time.time()
    # Serial when CONCURRENCY=1 (default) — ThreadPool still works but sequential is clearer for 429s
    if CONCURRENCY <= 1:
        for lead in todo:
            if _stop_run.is_set():
                _log(f"  stopping early — {_stop_why['why'] or 'stop requested'} (rerun --gap to resume)")
                break
            enrich_one(lead, key, args.deep, cache, args.relatives)
    else:
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            futures = [ex.submit(enrich_one, lead, key, args.deep, cache, args.relatives) for lead in todo]
            for _ in as_completed(futures):
                if _stop_run.is_set():
                    break

    dt = int(time.time() - t0)
    print(f'\nDONE in {dt}s: {_stats["ok"]} hit / {_stats["miss"]} empty / {_stats["err"]} error · '
          f'429s={_stats["rate_limited"]} · '
          f'Person layer ran on {_stats["person"] + _stats["person_miss"]} (added {_stats["phones_added"]} phones)')
    print(f'cache -> whitepages_lookup.json ({len(cache)} entries)')
    print(f'spend: {_run["calls"]} billed request(s) = ${_run["spent"]:.2f} this run · '
          f'${wp_spent_today():.2f} today')
    if args.relatives or args.case or args.pick_miami or args.pick_callable or args.pick_best:
        report(leads, cache, [_lead_key(r) for r in todo], skiptrace, lines=not args.pick_best)
    if _stop_run.is_set():
        print('RESUME: python whitepages_lookup.py --gap --limit 25')
        print('        (or: python wp_gap_loop.py)')
        sys.exit(3)


if __name__ == '__main__':
    main()
