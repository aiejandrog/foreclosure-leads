#!/usr/bin/env python
"""Carlos's week of door routes — one city per day, houses only, 2-3 mile rings.

Spec set by Alejandro 2026-08-10: West Miami, Coral Gables, North Miami, Miami Shores,
Tamiami; one city a day; scrape up as many properties as possible per city; keep it
categorized and organized; stops within 2-3 miles of each other.

Rules carried over from the same morning's corrections (do not relax without him saying so):
  * NO CONDOS - _condo_ish + the county land-use description + the condo flag all gate it
  * NO REPEATS - every address from the 07-28 packet and both 08-10 single-day lists is out
  * live leads only - the _carlos_route gates (optout / stay / wrong-owner / auction-passed)

Assignment: a lead can sit inside two rings (North Miami and Miami Shores anchors are ~2 miles
apart) - each lead goes to its NEAREST city only, so no door ever appears on two days.

Output: Carlos_Week_Routes_YYYY-MM-DD.html (repo + Desktop\DEALFLOW) - five day sections,
each with its own stat line, cards (address / phone / sale date / judgment / homestead), and
a one-tap Google Maps multi-stop run for that day.
"""
import datetime
import html as H
import json
import os
import sys
from urllib.parse import quote_plus

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _carlos_route as CR  # noqa: E402  (helpers: _live_lead, _condo_ish, _hav_mi, loaders)
import paths as P

RADIUS = 3.0
CITIES = [   # weekday order = the order Alejandro listed them
    ('West Miami',   25.7634, -80.2963),
    ('Coral Gables', 25.7215, -80.2684),
    ('North Miami',  25.8901, -80.1867),
    ('Miami Shores', 25.8620, -80.1926),
    ('Tamiami',      25.7587, -80.4020),
]

# every address already routed to Carlos (07-28 packet + both 08-10 lists)
EXCLUDE = {'10445 NW 68', '10831 NW 7', '1101 SW 122', '12035 SW 14', '12608 NW 7',
           '13002 SW 48', '14932 SW 23', '2055 SW 122', '3330 SW 88', '6441 SW 116',
           '8055 NW 104', '8520 SW 27', '8811 SW 21', '9535 SW 46',
           '290 NW 125', '8930 W FLAGLER', '9941 SW 38', '5420 NW 104', '3100 SW 126',
           '10301 SW 58', '6320 SW 147', '15630 SW 58', '5600 SW 74', '7379 SW 162',
           '10601 SW 128', '6732 SW 166', '9603 SW 158'}


def _addr(r):
    return (r.get('addr') or r.get('Address') or '').strip()


def _excluded(a):
    a = ' '.join(str(a).upper().split())
    return any(a.startswith(e) for e in EXCLUDE)


# Cases in an active DEAL stage never go on a door route. Nistico (1212 NE 91 ST) is at
# 'Offer made' with opposing counsel engaged - a door knock mid-negotiation reads as pressure
# and can blow up the deal. The ledger is the source of truth for stage.
_WN = (CR._load('worker_notes.json') or {}).get('notes') or {}
_DEAL_STAGES = {'APPOINTMENT', 'OFFER MADE', 'UNDER CONTRACT', 'ASSIGNED', 'CLOSED'}


def _in_deal(case):
    st = str((_WN.get(case) or {}).get('status') or '').upper()
    return st in _DEAL_STAGES


def _house_only(r, skip, sibs, opt):
    if not CR._live_lead(r, skip, siblings=sibs, optouts=opt):
        return False
    if _in_deal(r.get('case') or r.get('Case #')):
        return False
    if CR._condo_ish(r) or r.get('condo'):
        return False
    if 'CONDO' in str(r.get('dor_desc', '')).upper():
        return False
    return not _excluded(_addr(r))


def main():
    leads = CR._load('leads_final.json') or []
    geo = CR._load('geocode_cache.json') or {}
    skip = CR._load('skiptrace_results.json') or {}
    sibs = CR._load('sibling_cases.json') or {}
    _oo = CR._load('optouts.json') or {}
    opt = set((_oo.get('notes') or _oo or {}).keys()) if isinstance(_oo, dict) else set()

    # nearest-city assignment inside the ring
    days = {name: [] for name, _, _ in CITIES}
    seen_addr = set()
    for r in leads:
        c = r.get('case') or r.get('Case #')
        g = geo.get(c) or {}
        if not (g.get('lat') and g.get('lng')):
            continue
        if not _house_only(r, skip, sibs, opt):
            continue
        a = _addr(r).lower()
        if not a or a in seen_addr:
            continue
        best, bd = None, None
        for name, la, lo in CITIES:
            d = CR._hav_mi(la, lo, g['lat'], g['lng'])
            if d <= RADIUS and (bd is None or d < bd):
                best, bd = name, d
        if best:
            seen_addr.add(a)
            r['_dist'] = bd
            r['_case'] = c
            r['_phones'] = CR._phones_from_skip(skip.get(c))
            days[best].append(r)

    for name in days:
        days[name].sort(key=lambda r: r['_dist'])

    monday = datetime.date(2026, 8, 11)
    out = ['<!doctype html><html><head><meta charset="utf-8"><title>Carlos — Week of Door Routes</title><style>',
           'body{font:15px/1.5 "Segoe UI",Arial,sans-serif;color:#0e1b33;background:#f6f7fa;margin:0;padding:24px}',
           '.wrap{max-width:860px;margin:0 auto}',
           'h1{font-size:22px;margin:0 0 4px}',
           '.sub{color:#5b6472;font-size:13px;margin-bottom:18px}',
           '.day{background:#fff;border-radius:12px;box-shadow:0 2px 10px rgba(11,23,48,.08);padding:18px 22px;margin-bottom:18px}',
           '.day h2{font-size:17px;margin:0 0 2px;color:#0B1730}',
           '.day .st{font:600 12px Arial;color:#5b6472;margin-bottom:10px}',
           '.run{display:inline-block;background:#0B1730;color:#F4E5A7;padding:8px 14px;border-radius:8px;',
           '     text-decoration:none;font:700 13px Arial;margin:2px 0 12px}',
           '.card{border-top:1px solid #eef0f5;padding:9px 0;display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}',
           '.card .n{font-weight:800;min-width:22px;color:#b58a1f}',
           '.card .a{font-weight:700;flex:1 1 240px}',
           '.card .m{font:12.5px monospace;color:#1a5a2b}',
           '.card .d{font:12px Arial;color:#5b6472}',
           '.hs{background:#eaf5ec;color:#1a5a2b;font:700 10.5px Arial;padding:2px 7px;border-radius:9px}',
           '.emptyday{color:#8a1c1c;font-style:italic}',
           '</style></head><body><div class="wrap">',
           '<h1>Carlos — Week of Door Routes</h1>',
           f'<div class="sub">Built {datetime.date.today()} · one city a day · houses only, NO condos · '
           f'nothing repeated from earlier routes · every stop within {RADIUS:.0f} miles of the day\'s city</div>']

    total = 0
    for i, (name, la, lo) in enumerate(CITIES):
        rows = days[name]
        total += len(rows)
        day = monday + datetime.timedelta(days=i)
        out.append('<div class="day">')
        out.append(f'<h2>Day {i+1} — {H.escape(name)} <span style="font-weight:400;color:#5b6472">'
                   f'({day.strftime("%A %m/%d")})</span></h2>')
        hs = sum(1 for r in rows if r.get('homestead') or r.get('hs'))
        ph = sum(1 for r in rows if r['_phones'])
        out.append(f'<div class="st">{len(rows)} doors · {hs} homestead · {ph} with a phone number</div>')
        if not rows:
            out.append('<div class="emptyday">No qualifying houses inside the ring on today\'s board — '
                       'skip or swap this day.</div></div>')
            continue
        stops = [f"{_addr(r)}, Miami-Dade, FL" for r in rows]
        url = ('https://www.google.com/maps/dir/?api=1&origin=' + quote_plus(f'{name}, FL')
               + '&destination=' + quote_plus(stops[-1])
               + '&waypoints=' + quote_plus('|'.join(stops[:-1]), safe='|'))
        out.append(f'<a class="run" href="{H.escape(url)}">&#128663; Route all {len(rows)} stops</a>')
        for n, r in enumerate(rows, 1):
            def _fmt(p):
                n = str((p.get('n') if isinstance(p, dict) else p) or '')
                return f'({n[:3]}) {n[3:6]}-{n[6:]}' if len(n) == 10 else n
            phones = ' · '.join(_fmt(p) for p in r['_phones'][:2]) if r['_phones'] else 'no number — knock'
            sale = r.get('auction') or r.get('AuctionDate') or 'no sale date'
            judg = r.get('judgment') or r.get('judg') or 0
            out.append(
                f'<div class="card"><span class="n">{n}</span>'
                f'<span class="a">{H.escape(_addr(r))}</span>'
                + (f'<span class="hs">HOMESTEAD</span>' if (r.get("homestead") or r.get("hs")) else '')
                + f'<span class="m">{H.escape(phones)}</span>'
                f'<span class="d">sale {H.escape(str(sale))} · judgment ${int(judg):,} · {r["_dist"]:.1f} mi</span></div>')
        out.append('</div>')

    out.append(f'<div class="sub">{total} doors across the week.</div></div></body></html>')
    html = '\n'.join(out)
    stamp = datetime.date.today().isoformat()
    for dest in (os.path.join(HERE, f'Carlos_Week_Routes_{stamp}.html'),
                 P.out(f'Carlos_Week_Routes_{stamp}.html')):
        open(dest, 'w', encoding='utf-8').write(html)
        print('wrote', dest)
    for name, _, _ in CITIES:
        print(f'  {name:14} {len(days[name])} doors')
    print('total:', total)


if __name__ == '__main__':
    main()
