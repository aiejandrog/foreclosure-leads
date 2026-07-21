#!/usr/bin/env python3
"""llc_officers.py — the HUMANS behind LLC-owned leads (Sunbiz, FL Division of Corporations).

An LLC-owned foreclosure has no skip-traceable owner — but every Florida entity must file its
managers/officers AND a registered agent on Sunbiz, and the county mailing address is usually the
manager's own house. This pulls, for every company-owned lead:
  - authorized persons / officers (title, name, address)  -> who to skip-trace and call
  - registered agent (name, address)                      -> formal/service-of-process contact
  - entity status (Active / INACT)                        -> a dissolved LLC still has its people

Sunbiz 403s python-requests but serves native Windows curl (Schannel TLS fingerprint — the same
trick broward_liens.py uses against Cloudflare). Results cached in llc_officers.json keyed by
case (entity-level dedupe inside); COMMITTED to the repo like sale_history_cache.json so the
cloud early-publish carries it. Re-check an entity with --refresh.

Run:  python llc_officers.py [--limit N] [--refresh] [--case CASE]
"""
import argparse
import difflib
import html as _html
import json
import os
import re
import subprocess
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'llc_officers.json')
CURL = r'C:\Windows\System32\curl.exe' if os.name == 'nt' else 'curl'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
CO = re.compile(r'\b(LLC|L\.L\.C|CORP|INC|TRUST|COMPANY|HOLDINGS|LP|LTD|PROPERT|INVEST|GROUP|REALTY|CAPITAL|VENTURES|EQUIT)\b', re.I)
BASE = 'https://search.sunbiz.org'


def _curl(u):
    r = subprocess.run([CURL, '-s', '-L', '--max-time', '25', '-A', UA,
                        '-H', 'Accept: text/html,application/xhtml+xml', u],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.stdout or ''


SUFFIX_ONLY = re.compile(r'^(?:LLC|L\.?L\.?C\.?|INC|CORP|LP|LTD|TRUST|CO)\.?,?$', re.I)


def _entity_name(owners):
    """The company token out of the owners field ('LORETA INVESTMENTS LLC; JOHN DOE' -> the LLC).

    Splitting on '&' shreds names that CONTAIN it ('SMITH & JONES, LLC' -> a bare 'LLC' segment,
    which then fuzzy-matched five random officers on two live leads). So: try the whole string
    first, and never return a bare corporate suffix."""
    o = re.sub(r'\s+', ' ', str(owners or '')).strip().rstrip(',')
    if not o:
        return ''
    # a single entity with no co-owner list: use it whole (keeps '&' inside the name intact)
    if ';' not in o and CO.search(o) and not re.search(r'\bAND\b', o):
        return o.upper()
    best = ''
    for seg in re.split(r'[;]| AND ', o):
        seg = seg.strip().rstrip(',')
        if not seg or SUFFIX_ONLY.match(seg) or not CO.search(seg):
            continue
        # keep the LONGEST company-looking segment — the most complete name
        if len(seg) > len(best):
            best = seg
    return best.upper() if len(best) > 3 else ''


def _flat(html):
    return [p.strip() for p in re.sub(r'<[^>]+>', '|', re.sub(r'\s+', ' ', html)).split('|') if p.strip()]


def _parse_detail(html):
    t = re.sub(r'\s+', ' ', html)
    out = {'status': '', 'ra': '', 'ra_addr': '', 'officers': []}
    m = re.search(r'>\s*Status\s*<[^>]*>\s*<span[^>]*>\s*([A-Za-z]+)', t) or re.search(r'Status</label>\s*<span[^>]*>([A-Za-z]+)', t)
    if m:
        out['status'] = m.group(1).upper()
    # registered agent — loose section grab, tolerant of markup drift
    m = re.search(r'Registered Agent Name(.*?)(?:Officer/Director Detail|Authorized Person|Annual Reports|Document Images)', t)
    if m:
        parts = [p for p in _flat(m.group(1)) if p not in ('&amp; Address', '& Address', 'Name & Address')]
        if parts:
            out['ra'] = parts[0][:60]
            out['ra_addr'] = ', '.join(parts[1:3])[:90]
    # officers / authorized persons: chunks split on Title markers
    m = re.search(r'(?:Authorized Person\(s\) Detail|Officer/Director Detail)(.*?)(?:Annual Reports|Document Images|$)', t)
    if m:
        for chunk in re.split(r'Title(?:&nbsp;|\s)+', m.group(1))[1:]:
            parts = _flat(chunk)
            if not parts:
                continue
            title = parts[0][:10]
            rest = [p for p in parts[1:] if p and p != 'Name & Address']
            if not rest:
                continue
            name = rest[0][:60]
            addr = ', '.join(rest[1:3])[:90]
            if re.search(r'[A-Za-z]{2}', name):
                out['officers'].append({'t': title, 'n': name, 'a': addr})
    return out


def _norm(s):
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def _strip_suffix(s):
    """Name without its corporate suffix — 'MIZNER AND MIZNER LLC' -> 'MIZNERANDMIZNER'."""
    return re.sub(r'(LLC|LLLP|LLP|LP|INC|CORP|CORPORATION|COMPANY|CO|LTD|TRUST|PA|PLLC)$', '', _norm(s))


_SERIES = re.compile(r'\b(?:[IVX]{1,4}|\d+)\b')


def _series(s):
    """Numbering tokens that distinguish SIBLING entities: 'GO FUND PROP I LLC' and 'GO FUND PROP
    II LLC' are one character apart but are different companies with different managers. Any
    near-name match must carry identical numbering or it is rejected."""
    core = re.sub(r'\b(LLC|LLLP|LLP|LP|INC|CORP|CORPORATION|COMPANY|CO|LTD|TRUST|PA|PLLC)\b\.?', '',
                  str(s or '').upper())
    return tuple(_SERIES.findall(core))


def _lookup(entity):
    """Search Sunbiz and return the detail ONLY for a genuine name match.

    NEVER returns a fuzzy neighbour: Sunbiz's search is prefix-ish, so 'BEETA BRIDGES LLC' (not
    registered in FL) returned 'BEETAILS LLC' — a stranger's company with a stranger's CEO. That
    is worse than no data: it puts the operator on the phone with the wrong person. A match must
    be identical, or identical once the corporate suffix is dropped (LLC vs L.L.C. vs INC drift).
    Anything else -> not_found, and the UI says so."""
    h = _curl(BASE + '/Inquiry/CorporationSearch/SearchResults?inquiryType=EntityName&searchTerm='
              + urllib.parse.quote(entity))
    links = re.findall(r'href="(/Inquiry/CorporationSearch/SearchResultDetail[^"]+)"[^>]*>([^<]+)</a>', h)
    # Sunbiz returns HTML-escaped names ("ANGEL&#39;S NEWS LLC"). Unescape BEFORE any comparison:
    # the raw entity's digits ('39') otherwise read as a sibling-numbering token and blocked a
    # legitimate apostrophe match.
    links = [(href, _html.unescape(txt)) for href, txt in links]
    if not links:
        return {'not_found': True, 'officers': [], 'ra': '', 'ra_addr': '', 'status': '', 'exact': False}
    tgt, tgt_ns = _norm(entity), _strip_suffix(entity)
    hit = ([l for l in links if _norm(l[1]) == tgt]
           or [l for l in links if tgt_ns and _strip_suffix(l[1]) == tgt_ns])
    typo = False
    if not hit:
        # TYPO TIER: county tax rolls carry misspellings ('LORETA INVETMENTS LLC' is really
        # INVESTMENTS). A very tight similarity (>=0.92) recovers a dropped/extra letter while
        # still refusing a different company — BEETA BRIDGES vs BEETAILS scores ~0.6. The matched
        # name is always surfaced in the UI so a wrong pairing is obvious at a glance.
        _ser = _series(entity)
        scored = sorted(((difflib.SequenceMatcher(None, tgt, _norm(l[1])).ratio(), l) for l in links
                         if _series(l[1]) == _ser),          # sibling-entity guard (PROP I vs PROP II)
                        key=lambda x: -x[0])
        if scored and scored[0][0] >= 0.92:
            hit = [scored[0][1]]
            typo = True
    if not hit:
        return {'not_found': True, 'officers': [], 'ra': '', 'ra_addr': '', 'status': '',
                'exact': False, 'near': [l[1].strip() for l in links[:3]]}
    d = _parse_detail(_curl(BASE + hit[0][0].replace('&amp;', '&')))
    d['matched'] = hit[0][1].strip()
    d['exact'] = True
    d['typo'] = typo
    d['not_found'] = False
    return d


def _tps(name):
    """TruePeopleSearch link for a Sunbiz 'LAST, FIRST M' name."""
    m = re.match(r'([^,]+),\s*(.+)', name)
    person = (m.group(2) + ' ' + m.group(1)).strip() if m else name
    if CO.search(person):
        return ''
    return 'https://www.truepeoplesearch.com/results?name=' + urllib.parse.quote(person)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='max live Sunbiz lookups (0 = unlimited)')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--case', default='')
    a = ap.parse_args()

    leads = []
    for fn, ck in (('leads_final.json', 'Case #'), ('broward_leads.json', 'case'), ('palmbeach_leads.json', 'case')):
        p = os.path.join(HERE, fn)
        if os.path.exists(p):
            for r in json.load(open(p, encoding='utf-8')):
                leads.append((str(r.get(ck) or ''), str(r.get('owners') or '')))

    cache = {}
    if os.path.exists(OUT) and not a.refresh:
        try:
            cache = json.load(open(OUT, encoding='utf-8'))
        except Exception:
            cache = {}
    by_entity = {}                       # dedupe: one Sunbiz pull per entity name per run
    budget = a.limit if a.limit > 0 else 10 ** 9
    fetched = hits = 0
    for case, owners in leads:
        if a.case and case != a.case:
            continue
        ent = _entity_name(owners)
        if not case or not ent:
            continue
        if case in cache and not a.case:
            continue
        if ent in by_entity:
            d = by_entity[ent]
        else:
            if budget <= 0:
                break
            d = _lookup(ent)
            fetched += 1; budget -= 1
            by_entity[ent] = d
            time.sleep(1.2)
        if d and d.get('exact') and (d['officers'] or d['ra']):
            for o in d['officers']:
                o['p'] = _tps(o['n'])
            cache[case] = {'ent': ent, 'status': d['status'], 'exact': True,
                           'matched': d.get('matched', ''), 'typo': bool(d.get('typo')),
                           'ra': d['ra'], 'ra_addr': d['ra_addr'], 'officers': d['officers'][:5],
                           't': time.strftime('%Y-%m-%d')}
            hits += 1
            if a.case:
                print(json.dumps(cache[case], indent=1))
        elif d is not None:
            # NOT registered in FL under this name (or no people filed). Record it as such so the
            # site can route the operator to the deed instead of showing an empty/wrong block.
            cache[case] = {'ent': ent, 'status': d.get('status', ''), 'officers': [], 'ra': '',
                           'ra_addr': '', 'exact': False, 'nf': bool(d.get('not_found')),
                           'near': d.get('near', [])[:3], 't': time.strftime('%Y-%m-%d')}
            if a.case:
                print(json.dumps(cache[case], indent=1))

    json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=1)
    withppl = sum(1 for v in cache.values() if v.get('officers') or v.get('ra'))
    print(f'llc_officers: {fetched} Sunbiz lookups this run, {hits} new hits. '
          f'{withppl}/{len(cache)} cached LLC leads carry humans/RA.')


if __name__ == '__main__':
    main()
