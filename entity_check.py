#!/usr/bin/env python
"""entity_check — verify the configured company against the Florida register, and write the verdict.

This is the only thing that may open the entity gate. `entity.py` reads what this writes and refuses
to print an " LLC" claim without it. See entity.py's docstring for why the gate stopped being a
hand-edited tuple.

Reuses `llc_officers` rather than re-implementing Sunbiz:
  * `_curl` shells out to the curl binary with a browser UA. Sunbiz returns 403 to a plain urllib or
    requests fetch -- that 403 is exactly why the 2026-08-23 verification was skipped and a false
    entity claim shipped. Do not "simplify" this back to urllib.
  * `_lookup` refuses fuzzy neighbours by design. That guard is load-bearing here: BISCAYNE
    SOLUTIONS INC. (ACTIVE, P23000083487) and BISCAYNE GROUP, LLC both sit next to the name we want,
    and matching either would authorise a claim on a stranger's company.

STRICTER THAN llc_officers' NORMAL USE. Lead enrichment tolerates a 0.92-similarity typo match
because county tax rolls carry misspellings. Verifying our OWN entity tolerates nothing: the answer
must be exact, untyped, and ACTIVE, or the gate stays shut.

RUN
    python entity_check.py                          # check sender.json's llc, write entity_status.json
    python entity_check.py --name "Some LLC"        # check an arbitrary name
    python entity_check.py --available "Foo Group LLC"   # name screening before paying to file
    python entity_check.py --quiet                  # for the daily pipeline

EXIT CODES
    0  verified ACTIVE  (the gate is open)
    1  not verified     (the gate stays shut -- this is SAFE, not an error to route around)
    2  the lookup itself failed (network/markup drift) -- treat as not verified
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import entity  # noqa: E402
import llc_officers as L  # noqa: E402

STATUS_FILE = os.path.join(HERE, 'entity_status.json')


def neighbours(name, limit=6):
    """The alphabetical window around `name`.

    Sunbiz's search lists alphabetically FROM the search term, so the window is what proves an
    absence rather than merely failing to find a hit -- on 2026-08-23 the contiguous run
    BISCAYNE SOLAR SYSTEMS -> BISCAYNE SOLUTIONS INC. -> BISCAYNE SOUTH CORP had no gap where
    BISCAYNE SOLUTIONS GROUP could have been hiding. It is also the FS 605.0112 distinguishability
    shortlist when screening a candidate name."""
    # Sunbiz's ordering is NOT a plain normalised-string comparison: searching the full
    # 'BISCAYNE SOLUTIONS GROUP' overshoots straight to BISCAYNE SOUTH CORP and hides the one
    # neighbour that actually matters, BISCAYNE SOLUTIONS INC. Probe successively shorter prefixes
    # and merge, so the window brackets the name instead of starting past it.
    import html as _html
    words = str(name or '').split()
    probes, seen_p = [], set()
    for cut in range(len(words), 0, -1):
        t = ' '.join(words[:cut]).strip()
        if len(t) >= 4 and t.upper() not in seen_p:
            seen_p.add(t.upper())
            probes.append(t)
        if len(probes) >= 3:
            break
    out, seen = [], set()
    for t in probes:
        try:
            h = L._curl(L.BASE + '/Inquiry/CorporationSearch/SearchResults?inquiryType=EntityName'
                        '&searchTerm=' + urllib.parse.quote(t))
            for raw in re.findall(r'href="/Inquiry/CorporationSearch/SearchResultDetail[^"]+"[^>]*>([^<]+)</a>', h):
                n = _html.unescape(raw).strip()
                if n and n.upper() not in seen:
                    seen.add(n.upper())
                    out.append(n)
        except Exception:
            continue
    # Rank by shared prefix with the query so the genuinely-confusable names float up.
    q = re.sub(r'[^a-z0-9]+', '', name.lower())
    def _shared(n):
        c = re.sub(r'[^a-z0-9]+', '', n.lower())
        i = 0
        while i < min(len(q), len(c)) and q[i] == c[i]:
            i += 1
        return -i
    return sorted(out, key=_shared)[:limit]


def check(name):
    """-> verdict dict. Never raises for a 'no such entity' answer; that is a valid result."""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    base = {'checked_utc': now, 'query': name, 'verified': False, 'status': '', 'doc': '',
            'filed': '', 'matched': '', 'ra': '', 'neighbours': [], 'error': ''}
    try:
        r = L._lookup(name)
    except Exception as e:
        base['error'] = '%s: %s' % (type(e).__name__, e)
        return base

    if r.get('not_found'):
        base['status'] = 'NOT_FOUND'
        # NOT _lookup's raw `near` -- that is the overshooting single-probe window. Use the
        # multi-probe one, which actually brackets the name.
        base['neighbours'] = neighbours(name)
        return base

    # STRICT: exact, and NOT via the typo tier. A near-miss here would authorise a claim on
    # somebody else's company, which is the exact failure this whole module exists to prevent.
    if not r.get('exact') or r.get('typo'):
        base['status'] = 'INEXACT'
        base['matched'] = r.get('matched', '')
        base['neighbours'] = neighbours(name)
        return base

    base.update({'status': r.get('status') or 'UNKNOWN', 'doc': r.get('doc', ''),
                 'filed': r.get('filed', ''), 'matched': r.get('matched', ''), 'ra': r.get('ra', '')})
    base['verified'] = (base['status'] == 'ACTIVE')
    return base


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', help='check this name instead of sender.json llc')
    ap.add_argument('--available', metavar='NAME',
                    help='name screening: is NAME free, and what sits next to it? Writes nothing.')
    ap.add_argument('--quiet', action='store_true', help='one line of output, for the daily pipeline')
    a = ap.parse_args()

    # ---- availability screening (does not touch entity_status.json) --------------------------
    if a.available:
        v = check(a.available)
        near = v['neighbours'] or neighbours(a.available)
        if v['status'] == 'NOT_FOUND':
            print('AVAILABLE (no exact match): %s' % a.available)
        else:
            print('TAKEN: %s -> %s  %s  doc=%s' % (a.available, v['matched'], v['status'], v['doc']))
        if near:
            print('\nNearest registered names -- FS 605.0112 requires a new name be DISTINGUISHABLE\n'
                  'from these. The Division makes that call, not this script:')
            for n in near:
                print('   %s' % n)
        return 0 if v['status'] == 'NOT_FOUND' else 1

    # ---- the real check -----------------------------------------------------------------------
    name = a.name or (entity.sender().get('llc') or '').strip()
    if not name:
        print('no company name set in sender.json -- nothing to verify')
        return 2

    v = check(name)
    if not a.name:                      # only persist a verdict about the CONFIGURED entity
        io.open(STATUS_FILE, 'w', encoding='utf-8', newline='').write(
            json.dumps(v, ensure_ascii=False, indent=2))

    if v['error']:
        print('entity: LOOKUP FAILED for %s -- %s (gate stays shut)' % (name, v['error']))
        return 2
    if v['verified']:
        print('entity: %s VERIFIED %s doc=%s filed=%s%s'
              % (v['matched'], v['status'], v['doc'], v['filed'],
                 '' if a.quiet else '  -- the LLC suffix now prints on every surface'))
        return 0

    if a.quiet:
        print('entity: %s NOT verified (%s) -- suffix withheld' % (name, v['status']))
    else:
        print('entity: %s NOT verified -- %s' % (name, v['status']))
        if v['matched']:
            print('  closest exact-ish match: %s' % v['matched'])
        if v['neighbours']:
            print('  alphabetical window (an absence here is the proof, not just a miss):')
            for n in v['neighbours']:
                print('     %s' % n)
        print('\n  The LLC suffix stays withheld on every surface. This is the guard working, not a\n'
              '  bug. If the filing is new, Sunbiz lags about a business day -- the next daily run\n'
              '  lifts it automatically. Do NOT hand-edit entity_status.json to force it.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
