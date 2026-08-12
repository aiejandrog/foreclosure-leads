#!/usr/bin/env python
"""hardmoney_enrich — turn swept hard-money mortgages into CALLABLE people + real addresses.

The countywide sweep (fl_lp/broward_mortgages.py) gives borrower LLC, lender, amount, origin date.
That is a loan, not a lead. Two free public-record joins finish it:

  1. WHO   — Sunbiz (llc_officers._lookup): the LLC's officers + registered agent, i.e. the human
             who signs and answers the phone. Reuses that module's STRICT match guard verbatim: an
             exact name match (or exact-after-suffix-drop, or >=0.92 typo tier), never a fuzzy
             neighbour. A wrong officer means calling a stranger about someone else's loan.
  2. WHERE — BCPA name search (fl_lp/broward_resolve.bcpa_name): the LLC is the property OWNER (it
             just mortgaged the place), so an owner search returns folio + site address. The
             AcclaimWeb mortgage grid ships ParcelNumber EMPTY on every row (verified: 0 of 1,931),
             so this name join is the only address path — same ladder that resolved Broward LP.
             ONE parcel only: an LLC holding 5 properties can't be pinned to the mortgaged one from
             the name alone, so multi-hit LLCs are recorded as ambiguous, never guessed.

Both sources are FREE (no API keys, no credits). Nothing here skip-traces — that costs money and is
the operator's call; this produces the officer NAME + ADDRESS that a later skiptrace run turns into
a phone.

Run:  python hardmoney_enrich.py                 # balloon zone (8-24mo), biggest loans first
      python hardmoney_enrich.py --limit 300
      python hardmoney_enrich.py --all           # every swept LLC, not just the balloon zone
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llc_officers as LO                                   # noqa: E402  Sunbiz + strict matcher
from fl_lp.broward_resolve import bcpa_name, _split_site    # noqa: E402  BCPA owner search

SRC = os.path.join(HERE, 'broward_mortgages.json')
OUT = os.path.join(HERE, 'hardmoney_enriched.json')


def _age_mo(iso):
    try:
        return round((datetime.date.today() - datetime.date.fromisoformat(str(iso)[:10])).days / 30.4, 1)
    except Exception:
        return 999.0


def _norm_ent(s):
    """Normalize an entity name for BCPA comparison: drop punctuation + corporate suffix noise."""
    s = re.sub(r'[^A-Z0-9 ]', ' ', str(s or '').upper())
    s = re.sub(r'\b(LLC|L L C|INC|CORP|CORPORATION|CO|LP|LLP|LTD|COMPANY)\b', ' ', s)
    return ' '.join(s.split())


def bcpa_owner(llc):
    """-> {'folio','addr','city','zip'} when EXACTLY ONE Broward parcel is owned by this LLC.
    Multiple parcels -> ambiguous (an LLC with 5 houses can't be pinned from the name alone)."""
    hits = bcpa_name(llc)
    if hits is None:
        return {'err': 'bcpa unreachable'}
    want = _norm_ent(llc)
    if not want:
        return {'err': 'unusable name'}
    matches = []
    seen = set()
    for h in hits:
        for nm in (h.get('ownerName1'), h.get('ownerName2')):
            if _norm_ent(nm) == want:
                f = str(h.get('folioNumber') or '')
                if f and f not in seen:
                    seen.add(f)
                    matches.append(h)
                break
    if not matches:
        return {'err': 'no BCPA parcel under that exact name'}
    if len(matches) > 1:
        return {'err': 'owns %d parcels — cannot pin the mortgaged one by name' % len(matches),
                'parcels': len(matches),
                'candidates': [{'folio': m.get('folioNumber'), 'addr': m.get('siteAddress1')}
                               for m in matches[:6]]}
    m = matches[0]
    a1, city, zc = _split_site(m)
    return {'folio': str(m.get('folioNumber') or '').strip(), 'addr': a1, 'city': city, 'zip': zc,
            'paOwner': (m.get('ownerName1') or '').strip()}


def sunbiz_human(llc):
    """-> {'officer','title','officer_addr','agent','agent_addr','status','matched'} or {'err':...}.
    Strict match only — LO._lookup refuses fuzzy neighbours by design."""
    try:
        d = LO._lookup(llc)
    except Exception as e:
        return {'err': 'sunbiz error: %s' % str(e)[:60]}
    if d.get('not_found'):
        return {'err': 'no exact Sunbiz match', 'near': d.get('near') or []}
    off = next((p for p in (d.get('officers') or []) if p.get('n')), {})
    return {'officer': off.get('n', ''), 'title': off.get('t', ''), 'officer_addr': off.get('a', ''),
            'agent': d.get('ra', ''), 'agent_addr': d.get('ra_addr', ''),
            'status': d.get('status', ''), 'matched': d.get('matched', ''),
            'typo': bool(d.get('typo'))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=250)
    # LOAN-SIZE BAND. Sorting purely by size surfaces $100M-$1.4B institutional commercial paper
    # (InTown Suites, Bradford Allen, LSREF7) whose "officer" is another holding LLC in Greenwich or
    # Chicago — those are private-equity funds with their own capital markets desk, not a borrower
    # anyone is refinancing through a local broker. Jesse's client is the investor carrying a
    # few hundred k to a few million on a flip or a rental. Band it.
    ap.add_argument('--min-amt', type=int, default=75000)
    ap.add_argument('--max-amt', type=int, default=5000000)
    ap.add_argument('--all', action='store_true', help='every swept LLC, not just the balloon zone')
    ap.add_argument('--refresh', action='store_true', help='re-do LLCs already enriched')
    a = ap.parse_args()

    rows = json.load(open(SRC, encoding='utf-8'))
    if not a.all:
        # 8-30mo: a 1yr balloon blows at 12, a 2yr at 24. The 20-30mo tier is the most
        # urgent of all and an 8-24 window silently excluded it (fixed 2026-08-12).
        rows = [r for r in rows if 8 <= _age_mo(r.get('origin')) <= 30]
    rows = [r for r in rows if a.min_amt <= (r.get('amt') or 0) <= a.max_amt]
    # biggest loan first = biggest refi commission first
    rows.sort(key=lambda r: r.get('amt') or 0, reverse=True)

    cache = {}
    if os.path.exists(OUT) and not a.refresh:
        try:
            cache = json.load(open(OUT, encoding='utf-8'))
        except Exception:
            cache = {}

    todo, seen = [], set()
    for r in rows:
        b = (r.get('borrower') or '').strip()
        if not b or b in seen or (b in cache and not a.refresh):
            continue
        seen.add(b)
        todo.append((b, r))
        if len(todo) >= a.limit:
            break

    print('%d LLC borrower(s) to enrich (%d already cached)' % (len(todo), len(cache)))
    got_human = got_addr = 0
    for i, (llc, r) in enumerate(todo, 1):
        rec = {'borrower': llc, 'amt': r.get('amt'), 'lender': r.get('lender'),
               'origin': r.get('origin'), 'age_mo': _age_mo(r.get('origin')),
               'enriched': datetime.date.today().isoformat()}
        rec['sunbiz'] = sunbiz_human(llc)
        time.sleep(0.35)
        rec['bcpa'] = bcpa_owner(llc)
        time.sleep(0.35)
        if rec['sunbiz'].get('officer'):
            got_human += 1
        if rec['bcpa'].get('addr'):
            got_addr += 1
        cache[llc] = rec
        if i % 10 == 0 or i == len(todo):
            tmp = OUT + '.tmp'
            json.dump(cache, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
            os.replace(tmp, OUT)
            print('  [%d/%d] %d with a human, %d with an address' % (i, len(todo), got_human, got_addr),
                  flush=True)
    tmp = OUT + '.tmp'
    json.dump(cache, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    os.replace(tmp, OUT)
    print('DONE: %d enriched this run (%d human, %d address) -> %s'
          % (len(todo), got_human, got_addr, OUT))


if __name__ == '__main__':
    main()
