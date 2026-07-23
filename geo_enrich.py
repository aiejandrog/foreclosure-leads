#!/usr/bin/env python3
"""geo_enrich.py — lat/lng for every lead, so the board can route/expand from an ORIGIN by real distance.

Addresses alone can't be distance-sorted; the origin-anchored door route (nearest-first, expanding
outward from home) needs coordinates. This geocodes every lead via the **US Census geocoder** — FREE,
no API key, batch (up to 10k/request). Cached in geocode_cache.json keyed by case; make_tracker bakes
lat/lng onto each lead. Re-runs only geocode NEW cases.

Run:  python geo_enrich.py            # geocode all un-cached leads -> geocode_cache.json
"""
import csv
import io
import json
import os
import time

import requests

import skiptrace as SK   # parse_addr / _propaddr / _mailaddr

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'geocode_cache.json')
BATCH = 'https://geocoding.geo.census.gov/geocoder/locations/addressbatch'
ONELINE = 'https://geocoding.geo.census.gov/geocoder/locations/onelineaddress'


def _leads():
    out = []
    for fn, ck in (('leads_final.json', 'Case #'), ('broward_leads.json', 'case'), ('palmbeach_leads.json', 'case')):
        p = os.path.join(HERE, fn)
        if os.path.exists(p):
            for r in json.load(open(p, encoding='utf-8')):
                out.append((str(r.get(ck) or ''), r))
    return out


def geocode_one(addr):
    try:
        r = requests.get(ONELINE, params={'address': addr, 'benchmark': 'Public_AR_Current', 'format': 'json'}, timeout=20).json()
        m = (r.get('result', {}).get('addressMatches') or [])
        if m:
            c = m[0]['coordinates']
            return round(c['y'], 6), round(c['x'], 6)
    except Exception:
        return None
    return None


def geocode_batch(rows):
    """rows = [(case, street, city, state, zip)]. Census addressbatch: POST a CSV file, get a CSV back."""
    buf = io.StringIO()
    w = csv.writer(buf)
    for case, st, city, state, zp in rows:
        w.writerow([case, st, city, state, zp])
    files = {'addressFile': ('addr.csv', buf.getvalue(), 'text/csv')}
    data = {'benchmark': 'Public_AR_Current'}
    out = {}
    try:
        resp = requests.post(BATCH, files=files, data=data, timeout=120)
        for row in csv.reader(io.StringIO(resp.text)):
            # id, input, match(Match/No_Match), matchtype, matched_addr, lon,lat, ...
            if len(row) >= 6 and row[2].strip() == 'Match':
                try:
                    lon, lat = row[5].split(',')
                    out[row[0]] = (round(float(lat), 6), round(float(lon), 6))
                except Exception:
                    pass
    except Exception as e:
        print('  batch error:', str(e)[:80])
    return out


def main():
    cache = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    todo = []
    for case, r in _leads():
        if not case or case in cache:
            continue
        a = SK.parse_addr(SK._propaddr(r)) or SK.parse_addr(SK._mailaddr(r))
        if not a:
            continue
        todo.append((case, a['street'], a['city'], a['state'], a['zip']))
    print(f'{len(todo)} leads to geocode ({len(cache)} cached)')
    if not todo:
        print('nothing to geocode.'); return
    got = 0
    for i in range(0, len(todo), 1000):
        chunk = todo[i:i + 1000]
        res = geocode_batch(chunk)
        for case, latlng in res.items():
            cache[case] = {'lat': latlng[0], 'lng': latlng[1]}
        got += len(res)
        print(f'  batch {i // 1000 + 1}: {len(res)}/{len(chunk)} matched (total {got})')
        json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=1)
        time.sleep(1)
    # one-line fallback for the batch misses (unit #s / odd formats) — cheap, only the stragglers
    miss = [(c, s, ci, st, z) for c, s, ci, st, z in todo if c not in cache]
    for case, st, city, state, zp in miss[:120]:
        ll = geocode_one(f'{st}, {city}, {state} {zp}')
        if ll:
            cache[case] = {'lat': ll[0], 'lng': ll[1]}; got += 1
        time.sleep(0.2)
    json.dump(cache, open(OUT, 'w', encoding='utf-8'), indent=1)
    print(f'DONE: {got} newly geocoded, {len(cache)} total -> geocode_cache.json')


if __name__ == '__main__':
    main()
