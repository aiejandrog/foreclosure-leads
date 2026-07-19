"""Deep per-parcel tax-account links for Broward leads (county-taxes.net).

Why: the tracker linked the tax PORTAL landing page (county-taxes.net/broward/property-tax) because
the parcel deep-link was believed Cloudflare-walled. The portal is a Grant Street 'payhub' SPA whose
search runs on a PUBLIC Algolia index — the search-only API key ships to every browser by design.
The site's own deep-link grammar (proven from the app bundle + live browser capture):
    token = base64( objectID.split('/')[4] )
    link  = https://county-taxes.net/broward/property-tax/<token>
Querying the index by FOLIO returns the parcel's account record deterministically (verified:
folio 514008146850 -> exactly the URL a real user lands on after searching 'SALAZAR GLADYS').

Reads broward_leads.json, writes tax_links.json {case: deeplink} (gitignored — same posture as
cases_qs.json / records_qs.json). make_tracker then overrides the landing link per lead.
New-only by default (cached cases kept); --all re-mints; --limit N caps new lookups per run.
"""
import base64, json, os, re, sys, time
import requests

HERE  = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'broward_leads.json')
OUT   = os.path.join(HERE, 'tax_links.json')

# Public search-only Algolia credentials lifted from the site's own public config (they are
# shipped to every visitor's browser — not secrets). Index: Broward property-tax accounts.
APPID, KEY, IDX = '0LWZO52LS2', 'c0745578b56854a1b90ed57b63fbf0ba', 'fl-broward.property_tax'
API  = f'https://{APPID}-dsn.algolia.net/1/indexes/*/queries'
HDR  = {'x-algolia-application-id': APPID, 'x-algolia-api-key': KEY}
BASE = 'https://county-taxes.net/broward/property-tax/'


def _folio_of(r):
    f = re.sub(r'\D', '', r.get('folio', '') or '')
    if not f:
        m = re.search(r'URL_Folio=(\d+)', r.get('pa', '') or '')
        f = m.group(1) if m else ''
    return f


def _token(oid):
    seg = oid.split('/')[4] if oid.count('/') >= 4 else ''
    return base64.b64encode(seg.encode()).decode() if seg else ''


def main():
    args = sys.argv[1:]
    do_all = '--all' in args
    limit = 0
    if '--limit' in args:
        i = args.index('--limit')
        if i + 1 < len(args):
            try: limit = int(args[i + 1])
            except Exception: limit = 0

    leads = json.load(open(LEADS, encoding='utf-8')) if os.path.exists(LEADS) else []
    cache = {}
    if os.path.exists(OUT):
        try: cache = json.load(open(OUT, encoding='utf-8'))
        except Exception: cache = {}

    todo = []
    for r in leads:
        case = r.get('case', '')
        if not case or (not do_all and cache.get(case)):
            continue
        f = _folio_of(r)
        if f:
            todo.append((case, f))
    if limit:
        todo = todo[:limit]
    print(f'{len(todo)} folios to resolve ({len(cache)} cached)')
    if not todo:
        json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=0)
        return

    ok = fail = 0
    for i in range(0, len(todo), 20):            # Algolia multi-query: 20 folios per POST
        chunk = todo[i:i + 20]
        body = {'requests': [{'indexName': IDX,
                              'params': 'query=%s&hitsPerPage=2&clickAnalytics=false' % f}
                             for _, f in chunk]}
        try:
            r = requests.post(API, headers=HDR, json=body, timeout=30)
            results = r.json().get('results', [])
        except Exception as e:
            print('batch error:', str(e)[:80])
            fail += len(chunk)
            continue
        for (case, folio), res in zip(chunk, results):
            got = ''
            for h in res.get('hits', []):
                oid = h.get('objectID', '')
                # Trust a hit only when the parcel's own id tokens contain our folio — never
                # link a fuzzy-matched wrong account.
                if folio in json.dumps(h.get('child_groups', '')) or folio in oid:
                    tok = _token(oid)
                    if tok:
                        got = BASE + tok
                        break
            if got:
                cache[case] = got; ok += 1
            else:
                fail += 1
        print(f'  {ok} linked / {fail} unresolved...')
        time.sleep(0.25)   # polite — Algolia is built for search traffic, but stay boring

    json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=0)
    print(f'tax_links.json: {len(cache)} total ({ok} new, {fail} unresolved)')


if __name__ == '__main__':
    main()
