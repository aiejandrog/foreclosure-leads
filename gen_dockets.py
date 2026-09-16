#!/usr/bin/env python
"""gen_dockets.py -- cache Miami-Dade DOCKETS so the board shows the filings INLINE.

WHY (2026-09-15). The Docket link could only ever open the clerk's SEARCH page: Miami-Dade
retired /ocs/Search.aspx, and their SPA deliberately refuses to render a case from a URL (the
case-number token mints but GetMultipleCaseResult/GetSingleCaseResult come back empty for it --
verified). Broward/Palm Beach offer no case deep-link at all. So a "click Docket, land on the
docket" LINK cannot be built on any of the three.

But the OCS JSON API answers plain requests even though the web view blocks automation -- the
encrypt -> GetSingleCaseResult dance docket.py already proved. So instead of linking out, we PULL
the docket and bake it into the board: parties, case type, status, filing date and the filing
history, shown in a modal without leaving DealFlow. That is the "direct" the link never could be.

MIAMI-DADE ONLY -- Broward/PB clerks are different systems (docket.py's own limit). Those leads
keep the search-page link.

Cache: dockets.json (gitignored -- same posture as cases_qs.json / *_liens.json; the board ships
it inside the ENCRYPTED payload, not in the clear).

Usage:
  python gen_dockets.py                    # new MD cases only, default cap
  python gen_dockets.py --limit 200        # bigger batch
  python gen_dockets.py --stale 14         # also re-pull cached dockets older than 14 days
  python gen_dockets.py --case 2026-017502-CA-01   # one case, ignore the cache
"""
import argparse
import glob
import json
import os
import random
import re
import sys
import time

import docket as D          # reuse the audited encrypt -> GetSingleCaseResult pull()

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
CACHE = os.path.join(HERE, 'dockets.json')
MAX_ENTRIES = 40            # newest N filings; bounds the baked payload (a long case runs 100+)
THROTTLE_S = float(os.environ.get('DOCKET_THROTTLE', '2.2'))   # one polite request pair per case
RETRIES = 3                 # per case, with backoff — the clerk rate-limits a sustained burst
COOL_AFTER = 5              # consecutive failures before a long cool-off
COOL_S = 90
DEADLINE_S = int(os.environ.get('DOCKET_DEADLINE', '600'))   # never eat the nightly's 30-min kill
# Miami-Dade civil/county case formats: 2026-017502-CA-01, 2024-024069-CC-05 ...
MD_CASE = re.compile(r'^\d{4}-\d{6}-(CA|CC)-\d{2}$', re.I)


def _case(r):
    return (r.get('Case #', '') or r.get('case', '') or '').strip()


def _county(r):
    return str(r.get('county', '') or '').upper()


def load_leads():
    """leads_final.json (Miami-Dade) + every county <name>_leads.json, same set the board bakes."""
    out = []
    try:
        out.extend(json.load(open(LEADS, encoding='utf-8')))
    except Exception as e:
        print(f'  leads_final.json: {e}')
    for f in sorted(glob.glob(os.path.join(HERE, '*_leads.json'))):
        bn = os.path.basename(f)
        if bn in ('leads_final.json', 'leads_raw.json') or bn.startswith('_'):
            continue
        try:
            out.extend(json.load(open(f, encoding='utf-8')))
        except Exception as e:
            print(f'  skip {bn}: {e}')
    return out


def compact(j):
    """Trim the API record to what the board actually renders. The API misspells
    docketDescrition -- docket.py documents it; keep reading the misspelled key."""
    parties = [{'t': (p.get('partyTypeDesc') or '').strip(),
                'n': (p.get('partyName') or '').strip(),
                'a': (p.get('leadAttName') or '').strip()}
               for p in (j.get('parties') or [])][:14]
    dk = sorted((j.get('dockets') or []),
                key=lambda x: str(x.get('eventDate') or x.get('oDate') or ''))
    ents = []
    for x in dk[-MAX_ENTRIES:]:
        d = str(x.get('eventDate') or x.get('oDate') or '')[:10]
        desc = (x.get('docketDescrition') or '').strip()
        cmt = str(x.get('comments') or '').strip().replace('\n', ' / ')[:160]
        e = {'d': d, 'x': desc}
        if cmt and cmt.lower() != desc.lower():
            e['c'] = cmt
        ents.append(e)
    return {
        'style': (j.get('caseStyle') or '').strip(),
        'type': (j.get('caseType') or '').strip(),
        'status': (j.get('caseStatus') or '').strip(),
        'filed': str(j.get('filingDate') or '')[:10],
        'n': len(dk),                       # TRUE total, even though we ship only the newest MAX_ENTRIES
        'parties': parties,
        'ents': ents,
        'ts': time.strftime('%Y-%m-%d'),
    }


def _pull_retry(case, tries=RETRIES):
    """Pull with backoff. MEASURED 2026-09-15: a 527-case run at 1.1s went 23 ok / 504 failed, every
    failure 'Max retries exceeded' — and a single pull seconds later answered in 0.18s. So the clerk
    rate-limits a sustained burst rather than blocking; the cases were fine. Retrying with a widening,
    jittered gap turns those 504 write-offs back into records instead of permanently-missing dockets."""
    last = None
    for a in range(tries):
        try:
            return D.pull(case)
        except Exception as e:
            last = e
            time.sleep(2.5 * (a + 1) + random.uniform(0, 1.5))
    raise last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=120)
    ap.add_argument('--stale', type=int, default=0, help='also re-pull cached dockets older than N days')
    ap.add_argument('--case', default='', help='one case number, bypasses the cache')
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE, encoding='utf-8'))
        except Exception:
            cache = {}

    if a.case:
        todo = [a.case.strip()]
    else:
        seen, todo = set(), []
        for r in load_leads():
            c = _case(r)
            if not c or c in seen or not MD_CASE.match(c):
                continue
            cty = _county(r)
            if cty and 'MIAMI' not in cty:      # blank county == Miami-Dade (leads_final)
                continue
            seen.add(c)
            hit = cache.get(c)
            if hit:
                if not a.stale:
                    continue
                try:
                    age = (time.time() - time.mktime(time.strptime(hit.get('ts', '1970-01-01'), '%Y-%m-%d'))) / 86400
                except Exception:
                    age = 9999
                if age < a.stale:
                    continue
            todo.append(c)
        todo = todo[:a.limit]

    print(f'{len(todo)} Miami-Dade case(s) to pull ({len(cache)} already cached)')
    if not todo:
        return 0

    ok = fail = consec = 0
    start = time.time()
    for i, c in enumerate(todo, 1):
        if time.time() - start > DEADLINE_S:
            print(f'  .. {DEADLINE_S}s budget hit; stopping (rest resume next run)')
            break
        # A wall of consecutive failures is the clerk throttling us, not 500 bad cases. Sit out a
        # cool-off instead of burning through the queue writing every remaining case off as failed.
        if consec >= COOL_AFTER:
            print(f'  .. {consec} consecutive failures — cooling off {COOL_S}s (clerk is throttling)')
            time.sleep(COOL_S)
            consec = 0
        try:
            j = _pull_retry(c)
            if not j or (j.get('caseID', -1) == -1 and not (j.get('dockets') or [])):
                print(f'  [{i:3}/{len(todo)}] --   {c}  no record')
                fail += 1
            else:
                rec = compact(j)
                cache[c] = rec
                ok += 1
                print(f'  [{i:3}/{len(todo)}] ok   {c}  {rec["n"]} entr(ies) · {rec["status"]} · {rec["type"][:28]}')
                json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=1)   # save as we go
                consec = 0                      # a success clears the throttle streak
        except Exception as e:
            fail += 1
            consec += 1
            print(f'  [{i:3}/{len(todo)}] ERR  {c}  {str(e)[:70]}')
        time.sleep(THROTTLE_S + random.uniform(0, 0.6))   # jitter — a metronome reads as a bot

    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=1)
    print(f'\nDONE: {ok} pulled, {fail} failed -> dockets.json ({len(cache)} cached). '
          f'make_tracker bakes these onto the board as row.dk.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
