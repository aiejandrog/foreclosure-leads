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


def _entity_name(owners):
    """The company token out of the owners field ('LORETA INVESTMENTS LLC; JOHN DOE' -> the LLC)."""
    for seg in re.split(r'[;&]| AND ', str(owners or '')):
        seg = seg.strip().rstrip(',')
        if seg and CO.search(seg):
            return re.sub(r'\s+', ' ', seg).upper()
    return ''


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


def _lookup(entity):
    """Search Sunbiz for the entity; return the parsed detail of the best (exact-name) match."""
    h = _curl(BASE + '/Inquiry/CorporationSearch/SearchResults?inquiryType=EntityName&searchTerm='
              + urllib.parse.quote(entity))
    links = re.findall(r'href="(/Inquiry/CorporationSearch/SearchResultDetail[^"]+)"[^>]*>([^<]+)</a>', h)
    if not links:
        return None
    norm = lambda s: re.sub(r'[^A-Z0-9]', '', s.upper())
    exact = [l for l in links if norm(l[1]) == norm(entity)]
    href = (exact or links)[0][0].replace('&amp;', '&')
    d = _parse_detail(_curl(BASE + href))
    d['matched'] = (exact or links)[0][1].strip()
    d['exact'] = bool(exact)
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
        if d and (d['officers'] or d['ra']):
            for o in d['officers']:
                o['p'] = _tps(o['n'])
            cache[case] = {'ent': ent, 'status': d['status'], 'exact': d['exact'],
                           'ra': d['ra'], 'ra_addr': d['ra_addr'], 'officers': d['officers'][:5],
                           't': time.strftime('%Y-%m-%d')}
            hits += 1
            if a.case:
                print(json.dumps(cache[case], indent=1))
        elif d is not None:
            cache[case] = {'ent': ent, 'status': d.get('status', ''), 'officers': [], 'ra': '',
                           'ra_addr': '', 'exact': d.get('exact', False), 't': time.strftime('%Y-%m-%d')}

    json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=1)
    withppl = sum(1 for v in cache.values() if v.get('officers') or v.get('ra'))
    print(f'llc_officers: {fetched} Sunbiz lookups this run, {hits} new hits. '
          f'{withppl}/{len(cache)} cached LLC leads carry humans/RA.')


if __name__ == '__main__':
    main()
