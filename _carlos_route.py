"""Carlos's door-knock packet — Miami-Dade foreclosures nearest to his house.

CARLOS'S HOUSE = 10839 NW 7th St, Miami, FL 33172 (lat 25.77557, lng -80.373158).
This is the "Near home" anchor already baked into the tracker (ROUTE_ORIGIN).
He complains about gas, so this packet is aggressively local: 5-mile ring first,
grown to 8 mi only if the 5-mile list is too thin.

WHAT WE FILTER OUT:
  * leads without lat/lng we can measure (can't route to it)
  * DO NOT CONTACT / opted-out (foreclosed by owner request)
  * active bankruptcy stay (federal auto-stay, no contact allowed)
  * wrong-owner leads (someone confirmed the phones don't reach the owner)
  * PALM BEACH / BROWARD -- gas is the whole point; MD-only

WHAT WE RANK BY:
  ascending distance from Carlos, straight-line miles. Houses ahead of condos
  when equal distance (door-knock is a lot easier on a single-family lot).

OUTPUT:
  Carlos_Door_Route_YYYY-MM-DD.html  --  printable packet, one card per lead,
    with tel: number, Google Maps address deep-link, sale date, judgment, and
    the Alejandro-known status bits (homestead / occupied / mortgage risk).
  Plus a single "Route the top 10" Google Maps deep-link at the top so he can
  drop-and-drive from his phone.
"""
import json, os, math, html, datetime, sys
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))

CARLOS = {'addr': '10839 NW 7th St, Miami, FL 33172', 'lat': 25.77557, 'lng': -80.373158}

# thresholds
RADIUS_TIGHT = 5.0      # miles -- try this first
RADIUS_LOOSE = 8.0      # fall back if the tight ring has < TARGET_MIN
TARGET_MIN   = 12       # minimum leads in the packet before we widen

def _load(name, default=None):
    p = os.path.join(HERE, name)
    if not os.path.exists(p): return default if default is not None else {}
    return json.load(open(p, encoding='utf-8'))

def _hav_mi(lat1, lng1, lat2, lng2):
    R = 3958.7613
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dLat = math.radians(lat2 - lat1)
    dLng = math.radians(lng2 - lng1)
    a = math.sin(dLat/2)**2 + math.cos(r1)*math.cos(r2)*math.sin(dLng/2)**2
    return 2*R*math.asin(math.sqrt(a))

def _fm(n):
    try: n = float(n)
    except Exception: return ''
    if abs(n) >= 1_000_000: return f"${n/1_000_000:.2f}M"
    if abs(n) >= 1000: return f"${n/1000:.0f}k"
    return f"${n:,.0f}"

def _phones_from_skip(entry):
    if not entry: return []
    ph = entry.get('phones') or []
    out = []
    for p in ph:
        if isinstance(p, dict):
            n = p.get('number') or p.get('phone') or ''
            dnc = bool(p.get('dnc'))
            typ = p.get('type') or ''
        else:
            n, dnc, typ = str(p), False, ''
        n = ''.join(c for c in n if c.isdigit())
        if len(n) == 11 and n.startswith('1'): n = n[1:]
        if len(n) == 10 and not dnc:
            out.append({'n': n, 'typ': typ})
    return out[:4]

def _fmt_phone(n):
    return f"({n[0:3]}) {n[3:6]}-{n[6:10]}"

def _addr_of(r):
    return (r.get('Address') or r.get('address') or '').strip()

def _condo_ish(r):
    dor = (r.get('dor_desc') or '').lower()
    if 'condo' in dor: return True
    addr = _addr_of(r).lower()
    return ' apt ' in addr or ' #' in addr or ' unit ' in addr

def _sale_date(r):
    s = r.get('AuctionDate') or r.get('auction_date') or ''
    return s

def _days_to(sale):
    if not sale: return None
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            d = datetime.datetime.strptime(sale.split()[0], fmt).date()
            return (d - datetime.date.today()).days
        except Exception: pass
    return None

def _live_lead(r, skip, siblings=None, optouts=None):
    """DROP-OR-KEEP filter -- the compliance + gas gate before ranking."""
    case = r.get('case') or r.get('Case #')
    if r.get('sale_bk_active') and not r.get('sale_stay_lifted'): return False   # BK stay
    # CLAIMED (sibling_cases.py): a sibling case already sold + title transferred.
    # The Martin lesson -- Carlos knocked on a unit R&V Investors already owned. Never again.
    if siblings and (siblings.get(case) or {}).get('claimed'): return False
    # server-side opt-out ledger (optouts.json) -- an owner who said stop gets NO door either
    if optouts and case in optouts: return False
    # already-sold / cancelled won't have a future date -- days<0 = past
    d = _days_to(_sale_date(r))
    if d is not None and d < 0: return False
    # tax-deed sales are a different lane -- Carlos door-knocks foreclosure-rescue leads
    if (r.get('sale_type') or '').upper() == 'TD': return False
    # Wrong-owner is stored in per-device notes[] client-side; we can't see those from a
    # server-side script. But the address + owner + phone tuple is still walkable --
    # the compliance is on CONTACT, not on visiting.
    return True

def build():
    leads = _load('leads_final.json') or []
    geo   = _load('geocode_cache.json') or {}
    skip  = _load('skiptrace_results.json') or {}
    sibs  = _load('sibling_cases.json') or {}
    _oo   = _load('optouts.json') or {}
    optouts = set((_oo.get('notes') or _oo or {}).keys()) if isinstance(_oo, dict) else set()
    if not isinstance(leads, list):
        leads = leads.get('leads', [])

    # 1) attach lat/lng + distance
    ranked = []
    for r in leads:
        c = r.get('case') or r.get('Case #')
        g = geo.get(c) or {}
        lat, lng = g.get('lat'), g.get('lng')
        if not (lat and lng): continue
        if not _live_lead(r, skip, siblings=sibs, optouts=optouts): continue
        d = _hav_mi(CARLOS['lat'], CARLOS['lng'], lat, lng)
        r['_dist'] = d
        r['_lat'] = lat; r['_lng'] = lng
        r['_case'] = c
        r['_phones'] = _phones_from_skip(skip.get(c))
        ranked.append(r)

    # 2) tight ring first; widen if too thin
    tight = [r for r in ranked if r['_dist'] <= RADIUS_TIGHT]
    if len(tight) >= TARGET_MIN:
        picked, radius_used = tight, RADIUS_TIGHT
    else:
        picked = [r for r in ranked if r['_dist'] <= RADIUS_LOOSE]
        radius_used = RADIUS_LOOSE

    # 3) DEDUPE by address -- same house, two cases (2nd mortgage / HOA + bank) reads as one door.
    #    Keep the one with the sooner auction (Carlos knocks once, on the case that runs out first).
    by_addr = {}
    for r in picked:
        key = _addr_of(r).lower().strip()
        if not key: continue
        d0 = _days_to(_sale_date(r))
        prev = by_addr.get(key)
        if prev is None:
            by_addr[key] = r; r['_alt_cases'] = []
        else:
            dP = _days_to(_sale_date(prev))
            # sooner sale wins the card slot; the other becomes an alt-case chip on it
            keep, other = (r, prev) if (d0 is not None and (dP is None or d0 < dP)) else (prev, r)
            keep['_alt_cases'] = (keep.get('_alt_cases') or []) + (other.get('_alt_cases') or []) + [{
                'case': other.get('_case'), 'sale': _sale_date(other), 'judg': other.get('judgment') or 0}]
            by_addr[key] = keep
    picked = list(by_addr.values())

    # 4) sort by distance; houses tie-break ahead of condos
    picked.sort(key=lambda r: (round(r['_dist'], 2), 1 if _condo_ish(r) else 0))

    # 5) top-10 Google Maps multi-stop route
    top = picked[:10]
    def _gmap_addr(a):
        import urllib.parse as u
        return u.quote_plus(a)
    origin = _gmap_addr(CARLOS['addr'])
    if top:
        stops = [_gmap_addr(_addr_of(r) + ', Miami-Dade, FL') for r in top]
        # https://www.google.com/maps/dir/?api=1&origin=A&destination=Z&waypoints=B|C|D
        route_url = ('https://www.google.com/maps/dir/?api=1'
                     f'&origin={origin}'
                     f'&destination={stops[-1]}'
                     + (('&waypoints=' + '|'.join(stops[:-1])) if len(stops) > 1 else ''))
    else:
        route_url = ''

    return picked, radius_used, route_url

def render(picked, radius, route_url):
    esc = lambda s: html.escape(str(s or ''), quote=True)
    today = datetime.date.today().isoformat()
    css = """
      *{box-sizing:border-box}
      body{margin:0;padding:24px;font:15px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Arial,sans-serif;
           color:#0e1b33;background:#fbfaf6}
      h1{margin:0 0 4px;font-size:22px;letter-spacing:.01em}
      .sub{color:#5b6472;margin:0 0 14px;font-size:13px}
      .banner{background:linear-gradient(90deg,#fff8e1,#f9edd2);border:1px solid #C6A14B;border-radius:10px;
              padding:14px 16px;margin:12px 0 22px}
      .banner a{color:#0e1b33;font-weight:800;text-decoration:none;border-bottom:1px dashed #8a6d1f}
      .banner b{color:#8a6d1f}
      .card{border:1px solid #e2e6ee;border-left:4px solid #C6A14B;border-radius:10px;padding:14px 16px;
            margin:0 0 14px;background:#fff;page-break-inside:avoid;position:relative}
      .card.condo{border-left-color:#8899b3}
      .card.urgent{border-left-color:#8a1c1c;background:#fff8f7}
      .urgtag{position:absolute;top:-9px;right:12px;background:#8a1c1c;color:#fff;padding:3px 10px;
              border-radius:5px;font-size:11px;font-weight:800;letter-spacing:.07em;text-transform:uppercase;
              box-shadow:0 2px 6px rgba(138,28,28,.35)}
      .altcases{margin-top:6px;font-size:11.5px;color:#5b6472;line-height:1.35}
      .altcases b{color:#0e1b33}
      .card .h{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
      .card .addr{font-size:16px;font-weight:800;letter-spacing:.01em;flex:1;min-width:220px}
      .card .addr a{color:#0e1b33;text-decoration:none}
      .card .addr a:hover{text-decoration:underline}
      .card .dist{font-size:13px;color:#5b6472;white-space:nowrap;padding-top:3px}
      .card .dist b{color:#8a6d1f;font-size:15px}
      .row{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;font-size:13.5px;color:#334155}
      .row .lbl{color:#8a94a8;font-size:11px;text-transform:uppercase;letter-spacing:.07em;margin-right:3px}
      .row b{color:#0e1b33}
      .row .warn{color:#a33}
      .row .hs{color:#286c34}
      .phones{margin-top:8px;display:flex;flex-wrap:wrap;gap:8px}
      .phones a{display:inline-block;padding:6px 12px;background:#eaf5ec;color:#1a5a2b;border-radius:6px;
                text-decoration:none;font-weight:700;font-size:13px;border:1px solid #cfe6d4}
      .phones a.dnc{background:#f9e5e5;color:#8a1c1c;border-color:#e8c9c9}
      .phones .nop{color:#8a94a8;font-size:12.5px;font-style:italic}
      .notes{margin-top:8px;font-size:12.5px;color:#5b6472;border-top:1px dashed #e2e6ee;padding-top:8px}
      .tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:.06em;
           text-transform:uppercase;margin-right:5px}
      .tag.hs{background:#eaf5ec;color:#286c34}
      .tag.oo{background:#ecebfa;color:#463ea1}
      .tag.cd{background:#eef3fa;color:#3c5b8a}
      .tag.mr{background:#f9e5e5;color:#8a1c1c}
      .tag.sn{background:#fff2d7;color:#7a5a10}
      @media print { body{background:#fff} .card{page-break-inside:avoid;box-shadow:none} .banner{page-break-after:avoid} }
    """
    def _card(r):
        addr = _addr_of(r)
        gmap = f"https://www.google.com/maps/search/?api=1&query={html.escape(addr + ' Miami FL', quote=True).replace(' ', '+')}"
        dist = f"{r['_dist']:.1f} mi"
        sale = _sale_date(r)
        days = _days_to(sale)
        days_txt = f" ({days}d)" if days is not None and 0 <= days <= 60 else ''
        judg = _fm(r.get('judgment') or r.get('Final Judgment Amount') or 0)
        val  = _fm(r.get('market_value') or r.get('Assessed Value') or r.get('value') or 0)
        owners = (r.get('owner_clean') or r.get('owners') or r.get('sale_who') or '').strip()
        homestead = bool(r.get('homestead'))
        indiv = bool(r.get('indiv_plaintiff'))
        mrisk = bool(r.get('mortgage_risk'))
        cond = _condo_ish(r)
        urgent = (days is not None and 0 <= days <= 7)
        card_cls = 'card' + (' condo' if cond else '') + (' urgent' if urgent else '')
        urg_html = f'<div class="urgtag">SALE IN {days}D</div>' if urgent else ''
        # alt-case chip (same house, another case with a later sale)
        alt = r.get('_alt_cases') or []
        alt_html = ''
        if alt:
            alt_html = '<div class="altcases">Also on this address: ' + ', '.join(
                f'<b>{esc(a["case"])}</b> (sale {esc(a["sale"])})' for a in alt) + '</div>'
        tags = []
        if homestead: tags.append('<span class="tag hs">Homestead</span>')
        if cond:      tags.append('<span class="tag cd">Condo</span>')
        if indiv:     tags.append('<span class="tag oo">Private plaintiff</span>')
        if mrisk:     tags.append('<span class="tag mr">Verify 1st mortgage</span>')
        if r.get('sale_bk'):
            tags.append('<span class="tag sn">BK filed</span>')
        # phones
        ph = r.get('_phones', [])
        if ph:
            ptxt = ''.join(
                f'<a href="tel:+1{p["n"]}"'
                + (' class="dnc" title="Marked DO-NOT-CALL by carrier"' if False else '')
                + f'>&#128222; {_fmt_phone(p["n"])}</a>'
                for p in ph)
            ph_html = f'<div class="phones">{ptxt}</div>'
        else:
            # give Carlos a manual-lookup fallback so no dead-end
            plink = r.get('people_addr_url') or r.get('people_url') or ''
            ph_html = ('<div class="phones"><span class="nop">No phone traced yet — '
                       + (f'<a href="{esc(plink)}" target="_blank" style="background:#fff2d7;color:#7a5a10;border:1px solid #eadb9f">Look up on TruePeopleSearch</a>' if plink else 'no lookup link available')
                       + '</span></div>')
        # bilingual door hint -- the 4-second frame (identity first, bounded time, no scary
        # words in sentence one; "foreclosure"/"auction" only after they engage)
        script_hint = ('Say: "Hi, sorry to knock cold -- are you the owner here? [WAIT] '
                       'My name is Carlos, I am local. Your case came up on the county records with a date of '
                       + (sale if sale else '[check the sheet]')
                       + '. Not selling anything -- I just want to make sure you know your options before that date. '
                       'Got a couple minutes, or does tomorrow work better?"')
        return (
            f'<div class="{card_cls}">'
            f'  {urg_html}'
            f'  <div class="h">'
            f'    <div class="addr"><a href="{esc(gmap)}" target="_blank">{esc(addr)}</a></div>'
            f'    <div class="dist"><b>{dist}</b> from home</div>'
            f'  </div>'
            f'  <div style="margin-top:4px">{"".join(tags)}</div>'
            f'  <div class="row">'
            f'    <span><span class="lbl">Sale</span> <b>{esc(sale)}{days_txt}</b></span>'
            f'    <span><span class="lbl">Judgment</span> <b>{judg}</b></span>'
            f'    <span><span class="lbl">Value</span> <b>{val}</b></span>'
            f'    <span><span class="lbl">Owner</span> <b>{esc(owners) or "—"}</b></span>'
            f'  </div>'
            f'  {ph_html}'
            f'  {alt_html}'
            f'  <div class="notes">{esc(script_hint)}</div>'
            f'</div>')

    cards_html = '\n'.join(_card(r) for r in picked)

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Carlos Door Route — {today}</title>
<style>{css}</style></head><body>
<h1>Carlos&rsquo;s door route &mdash; {len(picked)} nearby foreclosures</h1>
<div class="sub">
  Anchor: <b>{esc(CARLOS['addr'])}</b> &middot; Radius: <b>{radius:.0f} mi</b> &middot;
  Miami&#8209;Dade only (no gas to Broward or Palm Beach) &middot;
  Built {today}
</div>
<div class="banner">
  &#128663; <a href="{esc(route_url)}" target="_blank">One&#8209;tap driving route through the top 10 nearest</a>
  &middot; opens in Google Maps.
  Cards below are sorted <b>nearest first</b>; each address links straight to Maps, each phone is a tap&#8209;to&#8209;call.
  Green tags are good signals (Homestead = family home, softer approach). Red = verify before making promises.
</div>
{cards_html}
</body></html>"""

def main():
    picked, radius, route_url = build()
    doc = render(picked, radius, route_url)
    today = datetime.date.today().isoformat()
    out_repo = os.path.join(HERE, f'Carlos_Door_Route_{today}.html')
    with open(out_repo, 'w', encoding='utf-8') as f: f.write(doc)
    # also drop on the Desktop for quick print
    desk = P.out(f'Carlos_Door_Route_{today}.html')
    try:
        os.makedirs(os.path.dirname(desk), exist_ok=True)
        with open(desk, 'w', encoding='utf-8') as f: f.write(doc)
    except Exception as e:
        desk = f'(desktop write failed: {e})'
    print(f'radius: {radius:.0f} mi  |  leads: {len(picked)}')
    print(f'repo:    {out_repo}')
    print(f'desktop: {desk}')
    # quick stats
    ph = sum(1 for r in picked if r.get('_phones'))
    hs = sum(1 for r in picked if r.get('homestead'))
    cd = sum(1 for r in picked if _condo_ish(r))
    within2 = sum(1 for r in picked if r['_dist'] <= 2)
    print(f'stats:   {ph} have a phone  |  {hs} homestead  |  {cd} condo  |  {within2} within 2 mi')

if __name__ == '__main__':
    main()
