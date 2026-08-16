"""Whitepages Pro API v2 — LAYERED enrichment.

Layer 1: /v2/property (address → all owners + residents + typed phones + emails + current city).
Layer 2: /v2/person   (owner name → aliases, address history, relatives, LinkedIn, is_dead flag,
                       and — most importantly — MORE phones that Property missed because Person
                       Search draws from a broader dataset). Fires automatically when Property
                       returned fewer than THIN_PHONES phones for the top owner, or on --deep.

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
"""
import argparse, glob as _glob, json, os, re, sys, time, threading, urllib.parse, urllib.request, urllib.error
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
THIN_PHONES = 5                                     # Property returned <5 phones on top owner -> add Person
COMPANY_RE  = re.compile(r'\b(LLC|INC\b|CORP|COMPANY|CO\.|LTD|LP\b|LLP|ASSN|ASSOCIATION|CONDOMINIUM|CHURCH|TRUST|BANK|HOLDINGS)\b', re.I)
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
_stop_run = threading.Event()   # set on sustained 429 wall so the batch exits cleanly


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
# raise this only against a plan we actually hold.
WP_EST_COST = 0.22


def _http_get(url, key, retries=3):
    """One HTTP GET with X-Api-Key header. Returns parsed JSON on 200, {'_http':N} on 404, None on
    other errors. Retries on 429 with exponential backoff (WP Pro trial keys rate-limit hard).
    Exits on 401/403 (bad key). After retries exhausted, sets _stop_run so the batch exits cleanly.

    Every call runs through bd_budget's SHARED daily-dollar ledger. Whitepages used to escape the
    ceiling entirely (per-run cap only) — which is the exact multi-spender hole the ledger was built
    to close. A request that REACHED the provider is charged (200 and 404 alike — a miss still
    bills); 429s and network errors never reached billing and are not charged."""
    import bd_budget
    for attempt in range(retries + 1):
        if _stop_run.is_set():
            return None
        try:
            bd_budget.require(WP_EST_COST, 'whitepages_lookup')
        except bd_budget.BudgetExhausted as e:
            _log(f'  BUDGET: {e}')
            _stop_run.set()
            return None
        req = urllib.request.Request(url, headers={'X-Api-Key': key, 'User-Agent': UA})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                out = json.loads(r.read().decode('utf-8'))
                bd_budget.charge(WP_EST_COST, 'wp')
                return out
        except urllib.error.HTTPError as e:
            body = ''
            try: body = e.read().decode('utf-8', 'replace')[:200]
            except Exception: pass
            if e.code == 404:
                bd_budget.charge(WP_EST_COST, 'wp-miss')
                return {'_http': 404, 'result': None}
            if e.code == 429:
                wait = min(120, 20 * (2 ** attempt))          # 20, 40, 80, 120
                _log(f'  RATE LIMIT (429) sleeping {wait}s (attempt {attempt + 1}/{retries + 1})')
                _stats['rate_limited'] += 1
                time.sleep(wait); continue
            if e.code in (401, 403):
                _log(f'  AUTH ERROR ({e.code}) — key rejected. Stopping. {body}')
                os._exit(2)
            _log(f'  HTTP {e.code}: {body}')
            return None
        except Exception as e:
            _log(f'  network error: {e}')
            return None
    _log('  RATE LIMIT: exhausted retries — pausing run (resume-safe; cache untouched for this lead)')
    _stats['rate_limited'] += 1
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
    with _cache_lock:
        json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=1)


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


def _stamp_prop_id(entry, case):
    """Preserve / stamp opaque property.whitepages.com /property/{id} from wp_prop_ids.json."""
    if entry.get('_prop_id'):
        return
    try:
        ids = json.load(open(WP_IDS, encoding='utf-8')) if os.path.exists(WP_IDS) else {}
        pid = (ids.get(case) or '').strip()
        if pid:
            entry['_prop_id'] = pid
    except Exception:
        pass


def _run_person_layer(lead, city, prop, entry, key, deep, force=False):
    """Layer 2 Person Search. Returns (person_records, phones_added, ran)."""
    ow_ct = len(((prop.get('result') or {}).get('ownership_info') or {}).get('person_owners') or [])
    should_run = force or deep or (ow_ct == 0)
    person_records = list(entry.get('_person') or [])
    person_added_phones = 0
    if not should_run:
        return person_records, 0, False
    names = _owner_names_from_property(prop)
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
    for name in names[:2]:
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


def enrich_one(lead, key, deep, cache):
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
            lead, city, prop, entry, key, deep, force=True)
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

    # Layer 2: Person (auto on Property MISS / --deep). Skip re-search when Property already
    # returned owners — same phones, wastes trial quota (verified 2026-07-24).
    person_records, person_added_phones, should_run_person = _run_person_layer(
        lead, city, prop, entry, key, deep, force=False)
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


def build_todo(leads, cache, args, skiptrace=None):
    """Return the (deduped, skip-LLC when applicable) work list, respecting --tier / --refresh / --upgrade."""
    skiptrace = skiptrace if skiptrace is not None else {}
    todo = []
    if args.case:
        by = {_lead_key(r): r for r in leads}
        if args.case not in by: sys.exit(f'case {args.case} not on the board')
        return [by[args.case]]
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
            if ph >= THIN_PHONES: continue                           # already good coverage
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', help='single lead by ID')
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
        entry = cache.get(case) or {}
        if entry.get('_prop_id') != pid:
            entry['_prop_id'] = pid
            cache[case] = entry
            stamped += 1
    if stamped:
        _save_cache(cache)
        print(f'stamped {stamped} wp_prop_id(s) into cache (Marisela preserved)', flush=True)
    if args.ensure_prop_ids and not (args.gap or args.all or args.upgrade or args.case or args.stats):
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

    key = _load_key()
    todo = build_todo(leads, cache, args, skiptrace=skiptrace)
    cap = min(args.limit, MAX_PER_RUN)
    todo = todo[:cap]
    if not todo: print('nothing to do.'); return

    est_calls = len(todo) * (2 if args.deep else 1)                  # rough (Person may not fire on some)
    print(f'{len(todo)} lookup(s) queued (concurrency={CONCURRENCY}, throttle={THROTTLE_S}s, deep={args.deep}, ~{est_calls} API calls, est ${est_calls*0.10:.2f})', flush=True)

    t0 = time.time()
    # Serial when CONCURRENCY=1 (default) — ThreadPool still works but sequential is clearer for 429s
    if CONCURRENCY <= 1:
        for lead in todo:
            if _stop_run.is_set():
                _log('  stopping early — 429 wall (rerun --gap to resume)')
                break
            enrich_one(lead, key, args.deep, cache)
    else:
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            futures = [ex.submit(enrich_one, lead, key, args.deep, cache) for lead in todo]
            for _ in as_completed(futures):
                if _stop_run.is_set():
                    break

    dt = int(time.time() - t0)
    print(f'\nDONE in {dt}s: {_stats["ok"]} hit / {_stats["miss"]} empty / {_stats["err"]} error · '
          f'429s={_stats["rate_limited"]} · '
          f'Person layer ran on {_stats["person"] + _stats["person_miss"]} (added {_stats["phones_added"]} phones)')
    print(f'cache -> whitepages_lookup.json ({len(cache)} entries)')
    if _stop_run.is_set():
        print('RESUME: python whitepages_lookup.py --gap --limit 25')
        print('        (or: python wp_gap_loop.py)')
        sys.exit(3)


if __name__ == '__main__':
    main()
