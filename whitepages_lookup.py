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
  python whitepages_lookup.py --gap --limit 50                   # person-owned leads with ZERO phones (BatchData miss)
  python whitepages_lookup.py --stats                            # cache size + phone-lift snapshot

Env:
  WP_MAX_CALLS_PER_RUN   hard cap per script invocation (default 200; script exits at cap)
  WP_KEY_FILE            key file path (default: whitepages.key)
  WP_CONCURRENCY         parallel workers (default 4)
"""
import argparse, json, os, re, sys, time, threading, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE  = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
CACHE = os.path.join(HERE, 'whitepages_lookup.json')
KEY_F = os.path.join(HERE, os.environ.get('WP_KEY_FILE', 'whitepages.key'))
UA    = 'DealFlow/1.0 (+github.com/aiejandrog/foreclosure-leads)'
BASE  = 'https://api.whitepages.com/v2'
MAX_PER_RUN = int(os.environ.get('WP_MAX_CALLS_PER_RUN', '200'))
CONCURRENCY = max(1, int(os.environ.get('WP_CONCURRENCY', '1')))     # trial keys cap ~1 req/sec; verified 2026-07-24
THROTTLE_S  = float(os.environ.get('WP_THROTTLE_S', '0.6'))          # polite delay between calls (single-worker mode)
THIN_PHONES = 5                                     # Property returned <5 phones on top owner -> add Person
COMPANY_RE  = re.compile(r'\b(LLC|INC\b|CORP|COMPANY|CO\.|LTD|LP\b|LLP|ASSN|ASSOCIATION|CONDOMINIUM|CHURCH)\b', re.I)

_cache_lock = threading.Lock()
_progress_lock = threading.Lock()
_stats = {'ok': 0, 'miss': 0, 'err': 0, 'person': 0, 'person_miss': 0, 'phones_added': 0}


def _load_key():
    if not os.path.exists(KEY_F):
        sys.exit(f'FATAL: no key at {KEY_F} (drop the WP Pro API key in that file, then rerun)')
    k = open(KEY_F).read().strip()
    if not k or len(k) < 20: sys.exit('FATAL: key file exists but looks empty/short')
    return k


def _lead_key(r):  return (r.get('Case #') or r.get('case') or '').strip()
def _lead_addr(r): return r.get('Address') or r.get('addr') or ''


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


def _http_get(url, key, retries=2):
    """One HTTP GET with X-Api-Key header. Returns parsed JSON on 200, {'_http':N} on 404, None on
    other errors. Retries on 429 with backoff. Exits on 401/403 (bad key)."""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={'X-Api-Key': key, 'User-Agent': UA})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = ''
            try: body = e.read().decode('utf-8', 'replace')[:200]
            except Exception: pass
            if e.code == 404: return {'_http': 404, 'result': None}
            if e.code == 429:
                wait = 15 * (attempt + 1)
                _log(f'  RATE LIMIT (429) sleeping {wait}s')
                time.sleep(wait); continue
            if e.code in (401, 403):
                _log(f'  AUTH ERROR ({e.code}) — key rejected. Stopping. {body}')
                os._exit(2)
            _log(f'  HTTP {e.code}: {body}')
            return None
        except Exception as e:
            _log(f'  network error: {e}')
            return None
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


def enrich_one(lead, key, deep, cache):
    """One lead: Property + (auto-fallback) Person layer. Writes to cache incrementally."""
    case = _lead_key(lead)
    parts = _split_addr(_lead_addr(lead))
    if not parts:
        _log(f'  SKIP {case:24s} bad address')
        _stats['err'] += 1
        return
    street, city, state = parts
    if THROTTLE_S > 0: time.sleep(THROTTLE_S)                            # polite; trial cap ~1 req/sec

    # Layer 1: Property
    entry = cache.get(case) or {}
    prop = entry.get('result') and {'result': entry.get('result'), '_http': entry.get('_http')} \
        or property_lookup(street, city, state, key)
    if prop is None:
        _stats['err'] += 1; return

    ph = _count_phones_on_property(prop)
    ow_ct = len(((prop.get('result') or {}).get('ownership_info') or {}).get('person_owners') or [])
    entry['result'] = prop.get('result')
    entry['_http']  = prop.get('_http', 200)
    entry['_ts']    = int(time.time())

    # Layer 2: Person (auto only on Property MISS — where Person can actually add new phones — or in
    # --deep mode). When Property returned owners, re-searching those owners by name just returns the
    # same phones (verified 2026-07-24: 0 new phones on every non-miss upgrade attempt). Trial keys
    # rate-limit hard, so we don't waste calls on the no-value path.
    should_run_person = deep or (ow_ct == 0)
    person_records = entry.get('_person') or []
    person_added_phones = 0
    if should_run_person:
        # candidate names: property owners first, then the lead's raw `owners` list as fallback
        names = _owner_names_from_property(prop)
        if not names:
            n = _first_owner_name(lead.get('owners') or '')
            if n: names.append(n)
        seen_names = {p.get('name', '').upper() for p in person_records}
        for name in names[:2]:                                       # cap per lead: 2 name lookups
            if name.upper() in seen_names: continue
            pr = person_lookup(name, city, 'FL', key)
            if pr is None: continue
            recs = pr if isinstance(pr, list) else []
            # count new phone digits
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
    entry['_person'] = person_records
    _stats['phones_added'] += person_added_phones

    with _cache_lock:
        cache[case] = entry
    _save_cache(cache)

    total_phones = ph + person_added_phones
    if ow_ct > 0 or person_records:
        person_note = f' + Person: +{person_added_phones} more' if person_added_phones else \
                      (f' + Person: 0 new' if should_run_person else '')
        _log(f'  ok   {case:24s} {street[:32]:32s} -> Property: {ow_ct} owner(s), {ph} phones{person_note}')
        _stats['ok'] += 1
    else:
        _log(f'  miss {case:24s} {street[:32]:32s} (no WP record on address or name)')
        _stats['miss'] += 1


def _lead_phone_count(r):
    """How many dialable phones the lead already carries (skiptrace / prior bake)."""
    n = 0
    for p in (r.get('phones') or []):
        raw = p.get('number') if isinstance(p, dict) else str(p)
        d = ''.join(c for c in str(raw or '') if c.isdigit())
        if len(d) == 11 and d.startswith('1'): d = d[1:]
        if len(d) == 10: n += 1
    return n


def build_todo(leads, cache, args):
    """Return the (deduped, skip-LLC when applicable) work list, respecting --tier / --refresh / --upgrade."""
    todo = []
    if args.case:
        by = {_lead_key(r): r for r in leads}
        if args.case not in by: sys.exit(f'case {args.case} not on the board')
        return [by[args.case]]
    if getattr(args, 'gap', False):
        # Systemic gap-fill: BatchData (or prior bake) returned ZERO phones on a person-owned lead.
        # These are the ones that look "dead" on Dealflow while Property Intel still has household data.
        skipped_co = skipped_has = 0
        for r in leads:
            k = _lead_key(r)
            if not k: continue
            if args.tier and (r.get('tier') or '') != args.tier: continue
            if k in cache and not args.refresh: continue
            owner = (r.get('owners') or '').upper()
            if any(t in owner for t in [' LLC', ' INC', ' CORP', ' CO.', ' LTD', ' LLP',
                                        ' ASSN', ' ASSOCIATION', ' CONDOMINIUM', ' CHURCH']):
                skipped_co += 1; continue
            if _lead_phone_count(r) > 0:
                skipped_has += 1; continue
            todo.append(r)
        _log(f'  --gap: {len(todo)} zero-phone person leads'
             + (f' (skipped {skipped_co} companies, {skipped_has} already have phones)' if (skipped_co or skipped_has) else ''))
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
            if k in cache and not args.refresh: continue
            owner = (r.get('owners') or '').upper()
            if any(t in owner for t in [' LLC', ' INC', ' CORP', ' CO.', ' LTD', ' LLP',
                                        ' ASSN', ' ASSOCIATION', ' CONDOMINIUM', ' CHURCH']):
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
    ap.add_argument('--gap', action='store_true', help='only person-owned leads with ZERO phones (BatchData miss gap-fill)')
    ap.add_argument('--deep', action='store_true', help='force Person Search on every lead (2x cost)')
    ap.add_argument('--limit', type=int, default=50, help='per-run cap (default 50, env WP_MAX_CALLS_PER_RUN overrides)')
    ap.add_argument('--refresh', action='store_true', help='re-fetch even cached leads')
    ap.add_argument('--stats', action='store_true', help='cache stats + phone lift snapshot')
    ap.add_argument('--tier', default='', help='only leads at this tier (A/B/C)')
    args = ap.parse_args()

    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    leads = json.load(open(LEADS, encoding='utf-8'))

    if args.stats:
        by = {_lead_key(r): r for r in leads}
        covered = sum(1 for k in cache if k in by)
        with_person = sum(1 for v in cache.values() if v.get('_person'))
        gained = 0
        for k, v in cache.items():
            r = by.get(k)
            if not r: continue
            base = {str(p.get('number') if isinstance(p, dict) else p) for p in (r.get('phones') or [])}
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
        print(f'cache: {len(cache)} entries · {covered} match live leads · {with_person} with Person layer')
        print(f'total unique WP phones across cache: {gained}')
        return

    key = _load_key()
    todo = build_todo(leads, cache, args)
    cap = min(args.limit, MAX_PER_RUN)
    todo = todo[:cap]
    if not todo: print('nothing to do.'); return

    est_calls = len(todo) * (2 if args.deep else 1)                  # rough (Person may not fire on some)
    print(f'{len(todo)} lookup(s) queued (concurrency={CONCURRENCY}, deep={args.deep}, ~{est_calls} API calls, est ${est_calls*0.10:.2f})', flush=True)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futures = [ex.submit(enrich_one, lead, key, args.deep, cache) for lead in todo]
        for _ in as_completed(futures): pass                         # progress printed inside enrich_one

    dt = int(time.time() - t0)
    print(f'\nDONE in {dt}s: {_stats["ok"]} hit / {_stats["miss"]} empty / {_stats["err"]} error · '
          f'Person layer ran on {_stats["person"] + _stats["person_miss"]} (added {_stats["phones_added"]} phones)')
    print(f'cache -> whitepages_lookup.json ({len(cache)} entries)')


if __name__ == '__main__':
    main()
