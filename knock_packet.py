#!/usr/bin/env python
"""knock_packet.py — the Miami-Dade door-knocking day, on paper, in three lanes.

WHY THIS EXISTS (2026-08-22)
pre_foreclosure_doors.json is the EARLY universe (fresh LPs, no sale date, months of runway) and
auction_archive.json quietly holds the URGENT one — Miami-Dade cases with a real auction date and
real equity, which is exactly who needs a knock THIS WEEK, not a letter. Nothing merged them and
nothing routed them, so door days were driven off a flat city list. This renders one packet:

  LANE 1  URGENT   auction date inside 30 days AND equity >= $100k or 30% of value. Human owners
                   only — LLC/INC doors don't answer and the pitch is homeowner-facing. Estates
                   and personal trusts STAY (heirs open doors) and are flagged.
  LANE 2  EARLY    every door in pre_foreclosure_doors.json, aged by LP filing date. The oldest
                   LPs float up: service + the 20-day answer clock make them first to turn urgent.
  ROUTES  NEAR     both lanes are chained nearest-neighbor inside ~5mi zones (geocode_cache is
                   keyed by CASE, which is why joins here are by case, never by address) and
                   chunked into 6-stop Google Maps links — 9 waypoints is the URL cap and 6 is a
                   real morning.

HONEST GAPS, stated so nobody trusts this past what it is:
 * Equity = county value minus recorded judgment. Not a payoff, not surplus math.
 * Urgent-lane owners come from lis_pendens.json/ownership.json where they exist; a blank owner
   means knock as "the occupant" and verify at the door.
 * No DNC scrub here (dnc_scrub.json is per-machine). The phone column is for dialing AT the
   door, standing in front of the house, not for a call session.

DOOR RULES ride on the cover page and are not optional (same rails as msg_flyer/PLAYBOOK):
third-party disclosure is the lawsuit — the foreclosure is named to the TITLED OWNER only; the
leave-behind is the printed card, never this sheet; the sale-date blank is filled in the owner's
presence only.

Run:  python knock_packet.py                # writes MSG_Knock_Packet_<date>.pdf (gitignored)
"""
import datetime
import html as H
import json
import math
import os
import re
from urllib.parse import quote_plus

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date.today()

ENTITY = re.compile(r'\b(LLC|L\.L\.C|INC|CORP|CORPORATION|COMPANY|HOLDINGS|PROPERTIES|CAPITAL|GROUP)\b', re.I)
TRUSTY = re.compile(r'\b(TRS|TRUST|TRUSTEE|EST OF|ESTATE)\b', re.I)
UNIT = re.compile(r'\b(?:APT|UNIT|#)\s*\S+|\b\d+[A-Z]?-\d+\b|\b(AVE|ST|DR|BLVD|CT|TER|PL|WAY|LN|RD|CIR|PATH|TRCE)\b\s+[A-Z]?-?\d+[A-Z]?\b', re.I)


def _load(name):
    return json.load(open(os.path.join(HERE, name), encoding='utf-8'))


def _d(us):                     # '7/2/2026' -> date
    m, d, y = [int(x) for x in str(us).split('/')]
    return datetime.date(y, m, d)


def urgent_lane(geo):
    arch, own = _load('auction_archive.json'), _load('ownership.json')
    lp = _load('lis_pendens.json')
    lpo = {it['case']: it.get('owner', '') for it in (lp if isinstance(lp, list) else [])
           if isinstance(it, dict) and it.get('case')}
    out = []
    for c, a in arch.items():
        if not isinstance(a, dict) or not a.get('auction'):
            continue
        if 'DADE' not in str(a.get('county', '')).upper() and not re.match(r'^\d{4}-\d{6}-C', c):
            continue
        try:
            sale = datetime.date(*[int(x) for x in a['auction'].split('-')])
        except Exception:
            continue
        v, j = a.get('value') or 0, a.get('judg') or 0
        eq = v - j
        # A sale dated TODAY runs at 9am — past the point of a door. Those owners become
        # surplus-recovery conversations (post-sale), which is a different packet, not this one.
        if not (TODAY + datetime.timedelta(days=1) <= sale <= TODAY + datetime.timedelta(days=30)):
            continue
        if not v or not j or not (eq >= 100000 or eq / v >= 0.30):
            continue
        owner = lpo.get(c) or (own.get(c, {}) or {}).get('title_owner', '') or ''
        if ENTITY.search(owner):
            continue                       # entities don't answer doors; mail lane, not this one
        g = geo.get(c) or {}
        out.append({'c': c, 'sale': sale, 'addr': a.get('addr', ''), 'val': v, 'judg': j,
                    'eq': int(eq), 'owner': owner,
                    'trust': bool(TRUSTY.search(owner)),
                    'unit': bool(UNIT.search(a.get('addr', ''))),
                    'lat': g.get('lat'), 'lng': g.get('lng')})
    out.sort(key=lambda x: (x['sale'], -x['eq']))
    return out


def early_lane(geo):
    doors = [d for _, ds in _load('pre_foreclosure_doors.json').items() for d in ds]
    for d in doors:
        d['age'] = (TODAY - _d(d['filed'])).days
        g = geo.get(d['c']) or {}
        d['lat'], d['lng'] = g.get('lat'), g.get('lng')
    return doors


# --- NEAR: zone -> nearest-neighbor chain -> 6-stop maps chunks -------------------------------
def _zone(d):
    if d['lat'] is None:
        return ('zz', str(d.get('city') or 'MIAMI'))     # un-geocoded: fall back to city buckets
    return (round(d['lat'] / 0.07), round(d['lng'] / 0.07))


def _chain(ds):
    """Greedy nearest-neighbor from the northernmost door. O(n^2), n is tiny."""
    left = sorted(ds, key=lambda d: -(d['lat'] or 0))
    if not left:
        return []
    path = [left.pop(0)]
    while left:
        a = path[-1]
        left.sort(key=lambda b: math.hypot((a['lat'] or 0) - (b['lat'] or 0),
                                           (a['lng'] or 0) - (b['lng'] or 0)))
        path.append(left.pop(0))
    return path


def routes(ds, target=9):
    """Zone, then MERGE: a 2-door "route" defeats the point of routing. Adjacent zones are
    rolled together N->S until a run holds ~`target` doors, then the whole run is re-chained.
    Un-geocoded doors keep their city buckets at the end - never silently dropped."""
    zones = {}
    for d in ds:
        zones.setdefault(_zone(d), []).append(d)
    geo_z = [z for k, z in sorted(zones.items(), key=lambda kv: -max((d['lat'] or 0) for d in kv[1]))
             if z[0]['lat'] is not None]
    city_z = [z for z in zones.values() if z[0]['lat'] is None]
    merged, run = [], []
    for z in geo_z:
        run += z
        if len(run) >= target:
            merged.append(_chain(run)); run = []
    if run:
        if merged and len(run) < 4:
            merged[-1] = _chain(merged[-1] + run)   # tail-end orphans join the last real run
        else:
            merged.append(_chain(run))
    return merged + city_z


def maps_links(route):
    """9-waypoint URL cap, 6-stop runs (same rule as carlos_deep_packet)."""
    stops = ['%s, FL' % r['addr'] for r in route]
    links = []
    for i in range(0, len(stops), 6):
        ch = stops[i:i + 6]
        links.append('https://www.google.com/maps/dir/?api=1&origin=' + quote_plus(ch[0])
                     + '&destination=' + quote_plus(ch[-1])
                     + ('&waypoints=' + quote_plus('|'.join(ch[1:-1]), safe='|') if len(ch) > 2 else ''))
    return links


# --- render -----------------------------------------------------------------------------------
CSS = """
@page{size:Letter;margin:.45in .5in}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI',Arial,sans-serif;color:#111;font-size:8.6pt}
h1{font-size:20pt;font-weight:900}
h2{font-size:12.5pt;font-weight:900;border-bottom:2pt solid #111;padding-bottom:2pt;margin:10pt 0 5pt}
h3{font-size:9.5pt;font-weight:800;margin:7pt 0 3pt}
.small{font-size:7.4pt;color:#333}
.box{border:1.4pt solid #111;padding:6pt 8pt;margin:7pt 0}
.rules li{margin:2.5pt 0 2.5pt 12pt;font-size:8.4pt}
table{width:100%;border-collapse:collapse;margin:2pt 0 6pt}
th{font-size:6.8pt;letter-spacing:.05em;text-align:left;border-bottom:1.2pt solid #111;padding:2pt 3pt}
td{border-bottom:.5pt solid #999;padding:3.2pt 3pt;vertical-align:top}
tr{page-break-inside:avoid}
.eq{font-weight:900;font-size:10pt;white-space:nowrap}
.own{font-weight:800}
.addr{font-weight:700}
.mono{font-size:7.2pt;color:#333;white-space:nowrap}
.flag{font-size:6.6pt;font-weight:800;border:1pt solid #111;padding:0 2.5pt;border-radius:2pt;white-space:nowrap}
.chk{font-size:7pt;white-space:nowrap;color:#333}
.route{background:#eee;font-weight:800;font-size:8pt;padding:3pt 4pt}
.mlink{font-size:6.6pt;color:#333;word-break:break-all}
.pb{page-break-before:always}
.salehdr{background:#111;color:#fff;font-weight:900;padding:3pt 6pt;font-size:10pt;margin:8pt 0 3pt}
.note{border-bottom:.5pt solid #777;display:inline-block;width:1.5in;height:8pt}
"""

CHK = '<span class="chk">&#9634;ans &#9634;no-ans &#9634;card &#9634;appt &#9634;DNC</span>'


def _flags(u):
    f = []
    if u.get('trust'):
        f.append('<span class="flag">EST/TRUST</span>')
    if u.get('unit'):
        f.append('<span class="flag">UNIT/GATED</span>')
    return ' '.join(f)


def render(urg, early):
    b = ['<h1>MSG Door Day &middot; Miami-Dade</h1>',
         '<div class="small">Built %s &middot; URGENT %d doors (sale &le;30 days, equity-positive, human owners) '
         '&middot; EARLY %d doors (fresh LP, no sale date) &middot; routed nearest-neighbor in ~5mi zones. '
         'Sales dated today are excluded &mdash; past the point of a door.</div>'
         % (TODAY.isoformat(), len(urg), len(early)),
         '<div class="box"><b>DOOR RULES — same rails as the card, non-negotiable.</b><ul class="rules">'
         '<li>The foreclosure is named to the <b>TITLED OWNER ONLY</b>. To anyone else at the door it is '
         '&ldquo;a time-sensitive matter concerning this property.&rdquo; Third-party disclosure is the lawsuit.</li>'
         '<li>This sheet is INTERNAL. The only paper that leaves your hand is the printed card. '
         'The sale-date blank on the card is filled in the owner&rsquo;s presence only.</li>'
         '<li>No fee talk at the door. Consultations are free. If they say stop &rarr; mark DNC and it is over.</li>'
         '<li>URGENT lane: sale dates are DAYS away — lead with the free consult, never with &ldquo;we can stop it.&rdquo; '
         'EARLY lane: nothing is scheduled — soft open, the keep-this-anyway posture.</li>'
         '<li>Phone column = dial standing at the door (no answer &rarr; call, then card). Not a call-session list.</li>'
         '</ul></div>']

    # URGENT, grouped by sale date, routed within group
    b.append('<h2>LANE 1 — URGENT: sale inside 30 days, equity on the table</h2>')
    bydate = {}
    for u in urg:
        bydate.setdefault(u['sale'], []).append(u)
    for sale in sorted(bydate):
        rows = [r for rt in routes(bydate[sale]) for r in rt]
        dd = (sale - TODAY).days
        b.append('<div class="salehdr">SALE %s — %d day%s out — %d doors</div>'
                 % (sale.strftime('%a %m/%d'), dd, 's' if dd != 1 else '', len(rows)))
        b.append('<table><tr><th>EQUITY</th><th>OWNER / ADDRESS</th><th>NUMBERS</th><th>VISIT</th></tr>')
        for u in rows:
            b.append('<tr><td class="eq">$%dk</td>'
                     '<td><span class="own">%s</span> %s<br><span class="addr">%s</span><br>'
                     '<span class="mono">%s</span></td>'
                     '<td class="mono">val $%s<br>judg $%s</td>'
                     '<td>%s<br><span class="note"></span></td></tr>'
                     % (u['eq'] // 1000, H.escape(u['owner'] or '(owner unverified — ask at door)'),
                        _flags(u), H.escape(u['addr']), u['c'],
                        format(int(u['val']), ','), format(int(u['judg']), ','), CHK))
        b.append('</table>')
        for i, ml in enumerate(maps_links(rows), 1):
            b.append('<div class="mlink">&#128663; run %d: %s</div>' % (i, ml))

    # EARLY, routed
    b.append('<h2 class="pb">LANE 2 — EARLY: LP filed, no sale date. Months of runway. Soft open.</h2>')
    n = 0
    for rt in routes(early):
        n += 1
        b.append('<div class="route">ROUTE %d — %d doors — %s</div>' % (n, len(rt), H.escape(rt[0].get('city') or '')))
        b.append('<table><tr><th>OWNER / ADDRESS</th><th>LP AGE</th><th>VALUE</th><th>HS</th><th>PLAINTIFF</th><th>PHONE</th><th>VISIT</th></tr>')
        for d in rt:
            b.append('<tr><td><span class="own">%s</span><br><span class="addr">%s</span>, %s<br><span class="mono">%s &middot; %s/%s</span></td>'
                     '<td class="mono">%dd<br>%s</td><td class="mono">$%s</td><td>%s</td>'
                     '<td class="mono">%s</td><td class="mono">%s</td><td>%s</td></tr>'
                     % (H.escape(d.get('owner') or ''), H.escape(d['addr'].split(',')[0]), H.escape(d.get('city') or ''),
                        d['c'], d.get('beds') or '?', d.get('baths') or '?',
                        d['age'], d['filed'],
                        format(int(d.get('val') or 0), ','), 'Y' if d.get('hs') else '&mdash;',
                        H.escape((d.get('pl') or '')[:24]),
                        d.get('ph') or '&mdash;', CHK))
        b.append('</table>')
        for i, ml in enumerate(maps_links(rt), 1):
            b.append('<div class="mlink">&#128663; run %d: %s</div>' % (i, ml))

    return ('<!doctype html><html><head><meta charset="utf-8"><title>MSG Door Day</title>'
            '<style>%s</style></head><body>%s</body></html>') % (CSS, ''.join(b))


def main():
    geo = _load('geocode_cache.json')
    urg, early = urgent_lane(geo), early_lane(geo)
    html = render(urg, early)
    stem = 'MSG_Knock_Packet_%s' % TODAY.isoformat()
    hp = os.path.join(HERE, stem + '.html')
    open(hp, 'w', encoding='utf-8').write(html)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page()
        pg.goto('file:///' + hp.replace(os.sep, '/'))
        pg.wait_for_timeout(400)
        pdf = pg.pdf(format='Letter', print_background=True)
        br.close()
    pp = os.path.join(HERE, stem + '.pdf')
    open(pp, 'wb').write(pdf)
    print('URGENT %d | EARLY %d' % (len(urg), len(early)))
    print('wrote %s (%.0f KB)' % (pp, len(pdf) / 1024))


if __name__ == '__main__':
    main()
