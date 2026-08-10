#!/usr/bin/env python
"""carlos_deep_packet.py — render deep_zone_doors.json into Carlos's door packet.

Separate from the search (carlos_deep_zones.py) so a verify pass can sit between them:
refuted_doors.json (a list of addresses) is subtracted before anything is rendered.

These are CODE-ENFORCEMENT doors, not mortgage-foreclosure doors — the packet says so on
every card and carries the talk track, because Carlos's usual script opens with the auction
date and these houses don't have one.
"""
import datetime
import html as H
import json
import os
from urllib.parse import quote_plus

HERE = os.path.dirname(os.path.abspath(__file__))
ORIGIN = {'Tamiami': 'Tamiami, FL', 'West Miami': 'West Miami, FL'}


def main():
    packet = json.load(open(os.path.join(HERE, 'deep_zone_doors.json'), encoding='utf-8'))
    refuted = set()
    rp = os.path.join(HERE, 'refuted_doors.json')
    if os.path.exists(rp):
        refuted = {a.upper() for a in json.load(open(rp, encoding='utf-8'))}

    out = ['<!doctype html><html><head><meta charset="utf-8"><title>Carlos — Deep Zone Doors (County-Lien Lane)</title><style>',
           'body{font:15px/1.5 "Segoe UI",Arial,sans-serif;color:#0e1b33;background:#f6f7fa;margin:0;padding:24px}',
           '.wrap{max-width:880px;margin:0 auto}',
           'h1{font-size:22px;margin:0 0 4px}',
           '.sub{color:#5b6472;font-size:13px;margin-bottom:14px}',
           '.talk{background:#fff8e6;border:1px solid #e8d48a;border-radius:10px;padding:12px 16px;margin-bottom:18px;font-size:13.5px}',
           '.zone{background:#fff;border-radius:12px;box-shadow:0 2px 10px rgba(11,23,48,.08);padding:18px 22px;margin-bottom:18px}',
           '.zone h2{font-size:17px;margin:0 0 2px;color:#0B1730}',
           '.zone .st{font:600 12px Arial;color:#5b6472;margin-bottom:10px}',
           '.run{display:inline-block;background:#0B1730;color:#F4E5A7;padding:8px 14px;border-radius:8px;',
           '     text-decoration:none;font:700 13px Arial;margin:2px 0 12px}',
           '.card{border-top:1px solid #eef0f5;padding:9px 0;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}',
           '.card .n{font-weight:800;min-width:22px;color:#b58a1f}',
           '.card .a{font-weight:700;flex:1 1 220px}',
           '.card .o{font:12.5px Arial;color:#12325e;min-width:170px}',
           '.lane9{background:#8a1c1c;color:#fff;font:700 10.5px Arial;padding:2px 7px;border-radius:9px}',
           '.lane4{background:#0B1730;color:#F4E5A7;font:700 10.5px Arial;padding:2px 7px;border-radius:9px}',
           '.oo{background:#eaf5ec;color:#1a5a2b;font:700 10.5px Arial;padding:2px 7px;border-radius:9px}',
           '.est{background:#efe6fa;color:#5b2d8f;font:700 10.5px Arial;padding:2px 7px;border-radius:9px}',
           '.card .d{font:12px Arial;color:#5b6472;flex-basis:100%}',
           '</style></head><body><div class="wrap">',
           '<h1>Carlos — Deep Zone Doors <span style="font-weight:400;font-size:15px;color:#5b6472">(county-lien lane)</span></h1>',
           f'<div class="sub">Built {datetime.date.today()} · these are NOT auction leads — different opener, below · '
           'houses only, individual owners only, nothing you have walked before</div>',
           '<div class="talk"><b>THE OPENER FOR THESE DOORS:</b> "Hi, I\'m with Miami Solutions Group — we help '
           'homeowners deal with county liens and code-enforcement fines. The county has '
           '<i>(a recorded lien on / an active case against)</i> this property and the fines grow until it\'s '
           'resolved. We can help you figure out what\'s owed and what your options are — no charge to talk." '
           '<b>RED-TAG doors: the COUNTY IS SUING to foreclose over the fines — lead with that, it\'s urgent '
           'and most owners don\'t know.</b> Do not quote fine amounts — we don\'t know them yet; the lien '
           'book/page is on the card so we can pull it if they engage.</div>']

    total = 0
    for zone, rows in packet.items():
        rows = [c for c in rows if c['addr'].upper() not in refuted]
        total += len(rows)
        oo = sum(1 for c in rows if c['owner_occupied'])
        n9 = sum(1 for c in rows if c['lane'] == 'COUNTY FORECLOSING')
        out.append('<div class="zone">')
        out.append(f'<h2>{H.escape(zone)}</h2>')
        out.append(f'<div class="st">{len(rows)} doors · {n9} county-foreclosing · {oo} owner-occupied</div>')
        if not rows:
            out.append('<div style="color:#8a1c1c;font-style:italic">Nothing survived verification.</div></div>')
            continue
        stops = ['%s, %s, FL %s' % (c['addr'], c.get('city') or 'MIAMI', c.get('zip') or '') for c in rows]
        url = ('https://www.google.com/maps/dir/?api=1&origin=' + quote_plus(ORIGIN.get(zone, zone + ', FL'))
               + '&destination=' + quote_plus(stops[-1])
               + '&waypoints=' + quote_plus('|'.join(stops[:-1]), safe='|'))
        out.append(f'<a class="run" href="{H.escape(url)}">&#128663; Route all {len(rows)} stops</a>')
        for n, c in enumerate(rows, 1):
            lane = ('<span class="lane9">COUNTY IS SUING</span>' if c['lane'] == 'COUNTY FORECLOSING'
                    else '<span class="lane4">RECORDED LIEN</span>')
            badges = lane
            if c['owner_occupied']:
                badges += ' <span class="oo">OWNER LIVES THERE</span>'
            if c.get('estate'):
                badges += ' <span class="est">ESTATE / PROBATE</span>'
            det = []
            if c.get('lien_bp'):
                det.append('lien recorded OR Bk/Pg %s' % c['lien_bp'])
            if c.get('latest_lien'):
                det.append('latest lien %s' % c['latest_lien'])
            det.append('%d open/lien case%s' % (c['n_cases'], 's' if c['n_cases'] != 1 else ''))
            det.append('%s mi' % c['dist'])
            probs = '; '.join(c.get('problems') or [])[:110]
            out.append(
                f'<div class="card"><span class="n">{n}</span>'
                f'<span class="a">{H.escape(c["addr"])}, {H.escape(c.get("city") or "")} {H.escape(c.get("zip") or "")}</span>'
                f'<span class="o">{H.escape(c["owner"])}</span>{badges}'
                f'<span class="d">{H.escape(" · ".join(det))} · {H.escape(probs)}</span></div>')
        out.append('</div>')

    out.append(f'<div class="sub">{total} doors total. Every card verified against the county\'s live '
               'code-enforcement file and the property roll before it reached this packet.</div></div></body></html>')
    html = '\n'.join(out)
    stamp = datetime.date.today().isoformat()
    for dest in (os.path.join(HERE, f'Carlos_Deep_Zone_Doors_{stamp}.html'),
                 os.path.expanduser(rf'~\OneDrive\Desktop\DEALFLOW\Carlos_Deep_Zone_Doors_{stamp}.html')):
        open(dest, 'w', encoding='utf-8').write(html)
        print('wrote', dest)
    # per-zone Maps links for WhatsApp
    for zone, rows in packet.items():
        rows = [c for c in rows if c['addr'].upper() not in refuted]
        if not rows:
            continue
        stops = ['%s, %s, FL %s' % (c['addr'], c.get('city') or 'MIAMI', c.get('zip') or '') for c in rows]
        url = ('https://www.google.com/maps/dir/?api=1&origin=' + quote_plus(ORIGIN.get(zone, zone + ', FL'))
               + '&destination=' + quote_plus(stops[-1])
               + '&waypoints=' + quote_plus('|'.join(stops[:-1]), safe='|'))
        print('\n[%s] %d doors\n%s' % (zone, len(rows), url))


if __name__ == '__main__':
    main()
