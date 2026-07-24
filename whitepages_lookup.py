"""Whitepages Pro API v2 -> per-lead full household graph.

WHY. Chrome-Claude's Velima analysis showed that a proper title read plus the household landline made
the deal actionable when TruePeopleSearch + CyberBG both came up dry. WP Pro's /v2/property endpoint
returns EVERY recorded owner of the folio, their current address (surfaces absentee owners — the
Velima case has Jacob Augustin living in TEXAS while Velima lives at the Miami property), and all
their phones (typed: Mobile / Landline / Unknown) + emails. One paid call replaces N free scrapes and
gives us data the free tier hides behind masking.

Endpoint (verified 2026-07-24 against the live docs at api.whitepages.com/docs):
    GET https://api.whitepages.com/v2/property?street=...&city=...&state_code=...
    header X-Api-Key: <key>   (case-sensitive header name)
Response shape (excerpt):
    {"result": {
        "apn": "30-2232-015-0450",
        "property_address": {"full_address": ..., "county": "Miami-Dade"},
        "ownership_info": {
            "owner_type": "Individual",
            "person_owners": [
                {"id": "...", "name": "Jacob J Augustin",
                 "current_addresses": [{"full_address": "...", "city": "Lewisville", "state": "TX"}],
                 "phones": [{"number": "13058935516", "type": "Landline"}, ...],
                 "emails": [{"email": "..."}]},
                ...],
            "business_owners": [...]
        }
    }}

CACHE. Results go to whitepages_lookup.json (gitignored), keyed by case #. Baked into every lead by
foreclosure_leads.make_tracker so the call sheet + row can render WP-verified numbers + absentee flags
without ever exposing the API key client-side. Never commit whitepages.key OR whitepages_lookup.json.

COST CONTROL. Per-call cost varies with the WP Pro tier; treat as $0.05-0.20/call until confirmed on
the dashboard. Defaults protect the balance: --limit caps a single run (default 20 leads), leads
already in the cache are skipped unless --refresh, and a hard $-budget can be enforced via env
WP_MAX_CALLS_PER_RUN. If the key is a trial, one run at default settings is the safe first move.

Usage:
    python whitepages_lookup.py --case 2025-016135-CA-01     # single lead (Velima test case)
    python whitepages_lookup.py --all --limit 20             # batch: 20 uncached leads
    python whitepages_lookup.py --all --refresh --limit 5    # re-fetch known leads (rare)
    python whitepages_lookup.py --stats                      # cache size + a coverage snapshot
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
CACHE = os.path.join(HERE, 'whitepages_lookup.json')
KEY_F = os.path.join(HERE, os.environ.get('WP_KEY_FILE', 'whitepages.key'))
API   = 'https://api.whitepages.com/v2/property'
UA    = 'DealFlow/1.0 (+github.com/aiejandrog/foreclosure-leads)'
MAX_PER_RUN = int(os.environ.get('WP_MAX_CALLS_PER_RUN', '40'))   # hard fleet-wide budget cap


def _load_key():
    if not os.path.exists(KEY_F):
        sys.exit(f'FATAL: no key at {KEY_F} (drop the WP Pro API key in that file, then rerun)')
    k = open(KEY_F).read().strip()
    if not k or len(k) < 20:
        sys.exit('FATAL: key file exists but looks empty/short')
    return k


def _split_addr(addr):
    """Split 'STREET, CITY, FL- ZIP' or 'STREET, CITY, FL ZIP' -> (street, city, 'FL')."""
    if not addr: return None
    parts = [p.strip() for p in addr.split(',')]
    if len(parts) < 2: return None
    street = parts[0]
    city = parts[1].strip()
    return street, city, 'FL'


def _lead_key(r):
    return (r.get('Case #') or r.get('case') or '').strip()


def _lead_addr(r):
    return r.get('Address') or r.get('addr') or ''


def lookup(street, city, state, key):
    """One property lookup. Returns dict on 200 (incl. 404-shaped empty), None on hard error."""
    q = urllib.parse.urlencode({'street': street, 'city': city, 'state_code': state})
    req = urllib.request.Request(API + '?' + q, headers={'X-Api-Key': key, 'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = ''
        try: body = e.read().decode('utf-8', 'replace')[:200]
        except Exception: pass
        if e.code == 404:                                                      # no record, not an error
            return {'result': None, '_http': 404}
        if e.code == 429:                                                      # rate limit -> back off
            print(f'  RATE LIMIT (429), sleeping 30s: {body}')
            time.sleep(30)
            return None
        if e.code in (401, 403):
            print(f'  AUTH ERROR ({e.code}) — key rejected. Stopping. body={body}')
            sys.exit(2)
        print(f'  HTTP {e.code}: {body}')
        return None
    except Exception as e:
        print(f'  network error: {e}')
        return None


def summarize(entry):
    """Compact one-line stats about a cached entry, for the run log."""
    r = (entry or {}).get('result') or {}
    ow = (r.get('ownership_info') or {}).get('person_owners') or []
    ph = sum(len(o.get('phones') or []) for o in ow)
    em = sum(len(o.get('emails') or []) for o in ow)
    return f'{len(ow)} owner(s), {ph} phone(s), {em} email(s)'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', help='Look up a single case by ID (e.g., 2025-016135-CA-01)')
    ap.add_argument('--all', action='store_true', help='Look up every uncached lead (respects --limit)')
    ap.add_argument('--limit', type=int, default=20, help='Per-run cap on lookups (default 20)')
    ap.add_argument('--refresh', action='store_true', help='Re-fetch even cached leads')
    ap.add_argument('--stats', action='store_true', help='Print cache stats and exit')
    ap.add_argument('--tier', default='', help='Only leads at this tier (A/B/C)')
    args = ap.parse_args()

    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    leads = json.load(open(LEADS, encoding='utf-8'))
    by_case = {_lead_key(r): r for r in leads if _lead_key(r)}

    if args.stats:
        # coverage
        covered = sum(1 for k in cache if k in by_case)
        empty   = sum(1 for k, v in cache.items() if (v.get('result') is None))
        print(f'cache: {len(cache)} entries · {covered} match live leads · {empty} were empty (no WP record)')
        # phone lift summary vs baseline (phones already on the lead)
        gained = 0
        for k, v in cache.items():
            r = by_case.get(k)
            if not r: continue
            base = set(str(p.get('number') if isinstance(p, dict) else p) for p in (r.get('phones') or []))
            wp   = set(o.get('number','') for own in ((v.get('result') or {}).get('ownership_info') or {}).get('person_owners') or [] for o in (own.get('phones') or []))
            gained += len(wp - base)
        print(f'phone lift across cache: +{gained} unique numbers not already on the lead')
        return

    key = _load_key()

    # build the work list
    todo = []
    if args.case:
        if args.case not in by_case: sys.exit(f'case {args.case} not on the current board')
        if args.case in cache and not args.refresh:
            print(f'{args.case} already cached: {summarize(cache[args.case])}'); return
        todo = [by_case[args.case]]
    elif args.all:
        for r in leads:
            k = _lead_key(r)
            if not k: continue
            if args.tier and (r.get('tier') or '') != args.tier: continue
            if k in cache and not args.refresh: continue
            todo.append(r)
    else:
        ap.print_help(); sys.exit(1)

    cap = min(args.limit, MAX_PER_RUN)
    todo = todo[:cap]
    if not todo:
        print('nothing to do (all cached; use --refresh to redo).'); return
    print(f'{len(todo)} lookup(s) queued (cap={cap}, budget-env WP_MAX_CALLS_PER_RUN={MAX_PER_RUN}, cost ~${len(todo)*0.10:.2f} at $0.10/call est.)')

    ok = miss = err = 0
    for r in todo:
        k = _lead_key(r)
        parts = _split_addr(_lead_addr(r))
        if not parts:
            print(f'  SKIP {k:24s} bad address'); err += 1; continue
        street, city, state = parts
        res = lookup(street, city, state, key)
        if res is None:
            err += 1; continue
        cache[k] = res
        json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=1)    # save-as-we-go
        if (res.get('result') or {}).get('ownership_info'):
            print(f'  ok   {k:24s} {street[:32]:32s} -> {summarize(res)}'); ok += 1
        else:
            print(f'  miss {k:24s} {street[:32]:32s} (no WP record)'); miss += 1
        time.sleep(0.4)                                                    # be polite

    print(f'\nDONE: {ok} hit / {miss} empty / {err} error. Cache -> whitepages_lookup.json')


if __name__ == '__main__':
    main()
