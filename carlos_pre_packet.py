#!/usr/bin/env python
"""carlos_pre_packet.py — the pre-foreclosure door book Carlos actually drives.

WHY SECTIONS AND NOT CITIES
"Miami, FL" covers Homestead to North Miami — 38 doors under one heading is not a route, it is
a list. Miami-Dade addresses are also unreliable as geography: half the county's houses say
"Miami" or "Unincorporated County" no matter which side of the county they sit on. So this
groups by REAL COORDINATES: doors within driving distance of each other become one section,
each section gets a human name from the zip codes inside it, and the stops inside a section are
ordered nearest-neighbor so he drives a line instead of criss-crossing.

Everything a door needs is on the card: owner, phone, value, beds/baths/sqft, when the case was
filed, which lender is suing, the case number, and a blank line to write what happened. The
script is printed on page one so he never knocks without it.

Output: an HTML book + the same book rendered to PDF (Playwright, the engine already vendored
for the UI tests).
"""
import datetime
import html as H
import json
import math
import os
import re
import sys
from urllib.parse import quote_plus

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, os.environ.get('CARLOS_SRC', '_carlos_pre_rows.json'))
MERGE_MI = 4.2          # two clusters merge when their centroids are this close
MAX_SECTION = 16        # a section bigger than this is a day's work by itself; split it
ORPHAN_MI = 9.0         # a lone door still joins its nearest section if it is within this far —
                        # a one-door "section" is not a route, it is a detour

# zip -> the name a Miami driver would actually use for that area
AREA = {
    '33030': 'Homestead', '33031': 'Homestead', '33032': 'Homestead', '33033': 'Homestead',
    '33034': 'Florida City', '33035': 'Florida City',
    '33157': 'Palmetto Bay / Cutler', '33158': 'Palmetto Bay', '33189': 'Cutler Bay',
    '33190': 'Cutler Bay', '33170': 'Goulds / Princeton', '33177': 'Richmond Heights',
    '33176': 'Kendall South', '33156': 'Pinecrest / Kendall', '33173': 'Kendall',
    '33183': 'West Kendall', '33193': 'West Kendall', '33186': 'West Kendall',
    '33196': 'West Kendall', '33185': 'Tamiami', '33194': 'Tamiami', '33184': 'Westchester',
    '33175': 'Westchester', '33165': 'Westchester', '33174': 'Fontainebleau',
    '33172': 'Doral / Fontainebleau', '33178': 'Doral', '33182': 'Doral',
    '33144': 'West Miami', '33155': 'Coral Terrace', '33134': 'Coral Gables',
    '33146': 'Coral Gables', '33143': 'South Miami', '33133': 'Coconut Grove',
    '33145': 'The Roads', '33125': 'Little Havana', '33135': 'Little Havana',
    '33126': 'Flagami', '33122': 'Airport West',
    '33010': 'Hialeah', '33012': 'Hialeah', '33013': 'Hialeah', '33014': 'Hialeah',
    '33015': 'Hialeah Gardens', '33016': 'Hialeah Gardens', '33018': 'Hialeah Gardens',
    '33166': 'Miami Springs', '33167': 'Liberty City', '33147': 'Brownsville',
    '33142': 'Allapattah', '33127': 'Little Haiti', '33150': 'Little River',
    '33137': 'Edgewater', '33138': 'Miami Shores', '33161': 'North Miami',
    '33168': 'North Miami', '33169': 'Norland', '33181': 'North Miami / Keystone',
    '33162': 'North Miami Beach', '33179': 'Ojus', '33160': 'Sunny Isles',
    '33180': 'Aventura', '33054': 'Opa-locka', '33055': 'Miami Gardens',
    '33056': 'Miami Gardens', '33139': 'Miami Beach', '33140': 'Miami Beach',
    '33141': 'North Beach', '33131': 'Brickell', '33132': 'Downtown', '33128': 'Downtown',
    '33129': 'Brickell', '33136': 'Overtown', '33149': 'Key Biscayne',
    '33154': 'Surfside / Bal Harbour', '33009': 'Hallandale', '33025': 'Miramar',
}


def hav(a, b, c, d):
    R = 3958.8
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def cluster(rows):
    """Agglomerative: every door starts alone, closest pair of clusters merges, stop at MERGE_MI."""
    cl = [[r] for r in rows]

    def cen(g):
        return (sum(x['lat'] for x in g) / len(g), sum(x['lng'] for x in g) / len(g))

    while True:
        best = None
        for i in range(len(cl)):
            for j in range(i + 1, len(cl)):
                if len(cl[i]) + len(cl[j]) > MAX_SECTION:
                    continue
                a, b = cen(cl[i]), cen(cl[j])
                d = hav(a[0], a[1], b[0], b[1])
                if d <= MERGE_MI and (best is None or d < best[0]):
                    best = (d, i, j)
        if not best:
            break
        _, i, j = best
        cl[i] = cl[i] + cl[j]
        cl.pop(j)

    # Orphans: a section of 1-2 doors is a detour, not a run. Fold each into the nearest
    # real section when one is within ORPHAN_MI, even though that section is already "full" —
    # driving 4 extra minutes beats a separate trip across the county for one house.
    small = [g for g in cl if len(g) <= 2]
    big = [g for g in cl if len(g) > 2]
    if big:
        for g in small:
            a = cen(g)
            tgt = min(big, key=lambda h: hav(a[0], a[1], *cen(h)))
            b = cen(tgt)
            if hav(a[0], a[1], b[0], b[1]) <= ORPHAN_MI:
                tgt.extend(g)
            else:
                big.append(g)          # genuinely remote — keep it, labelled honestly
        cl = big
    return cl


def order(g):
    """Nearest-neighbour from the north-west corner — a drivable line, not a scatter."""
    left = list(g)
    cur = min(left, key=lambda r: (-r['lat'], r['lng']))
    out = [cur]
    left.remove(cur)
    while left:
        nxt = min(left, key=lambda r: hav(cur['lat'], cur['lng'], r['lat'], r['lng']))
        out.append(nxt)
        left.remove(nxt)
        cur = nxt
    return out


def name_of(g):
    names = {}
    for r in g:
        n = AREA.get(str(r.get('zip') or '')[:5])
        if n:
            names[n] = names.get(n, 0) + 1
    if not names:
        m = re.search(r',\s*([A-Za-z .]+),\s*FL', g[0]['addr'] or '')
        return (m.group(1).strip() if m else 'Miami-Dade')
    # dedupe the parts too: two zips can map to overlapping labels ("Doral" + "Doral /
    # Fontainebleau"), and "Doral / Doral / Fontainebleau" reads like a bug on the page.
    top = sorted(names.items(), key=lambda kv: -kv[1])
    parts = []
    for n, _ in top:
        for piece in [p.strip() for p in n.split('/')]:
            if piece and piece not in parts:
                parts.append(piece)
    return ' / '.join(parts[:2])


def _datebit(r):
    """The red slot on a door card. An auction lead gets a COUNTDOWN ('SALE 09/02'). A fresh lis
    pendens has no sale date at all, and printing nothing wasted the single most persuasive fact
    Carlos owns on that door: the case was filed days ago and NOBODY has contacted this owner yet.
    Same slot, opposite pitch."""
    if r.get('auction'):
        return '<b style="color:#991b1b">SALE %s</b> &middot; ' % H.escape(str(r['auction']))
    if (r.get('lane') or '') == 'FRESH FILING':
        age = _filed_age(r.get('filed'))
        if age is not None:
            return ('<b style="color:#0b5d1e">JUST FILED &mdash; %d day%s ago &middot; FIRST CONTACT</b> &middot; '
                    % (age, '' if age == 1 else 's'))
        return '<b style="color:#0b5d1e">JUST FILED &middot; FIRST CONTACT</b> &middot; '
    if r.get('lien'):
        return '<b>%s</b> &middot; ' % H.escape(r['lien'])
    return ''


def _filed_age(filed):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return (datetime.date.today() - datetime.datetime.strptime(str(filed).strip(), fmt).date()).days
        except Exception:
            pass
    return None


def stop(r):
    a = r['addr'].split(',')[0].strip()
    z = str(r.get('zip') or '')[:5]
    return '%s, FL %s' % (a, z) if z else '%s, Miami-Dade, FL' % a


def maps(chunk):
    return ('https://www.google.com/maps/dir/?api=1&origin=' + quote_plus(chunk[0])
            + '&destination=' + quote_plus(chunk[-1])
            + ('&waypoints=' + quote_plus('|'.join(chunk[1:-1]), safe='|') if len(chunk) > 2 else ''))


def fmt_ph(p):
    d = re.sub(r'\D', '', str(p or ''))
    return '(%s) %s-%s' % (d[:3], d[3:6], d[6:]) if len(d) == 10 else (p or '')


CSS = """
@page{size:letter;margin:12mm 10mm}
body{font:11.5px/1.45 "Segoe UI",Arial,sans-serif;color:#111827;margin:0}
h1{font-size:22px;margin:0 0 2px}
.sub{color:#4b5563;font-size:11px;margin-bottom:12px}
.script{background:#0B1730;color:#fff;border-radius:10px;padding:14px 16px;margin-bottom:14px;page-break-after:always}
.script h2{color:#F4E5A7;font-size:14px;margin:0 0 8px}
.must{background:#7f1d1d;border-radius:8px;padding:10px 13px;margin:0 0 10px;font-size:12.5px;line-height:1.5}
.say{background:rgba(255,255,255,.09);border-left:3px solid #F4E5A7;padding:8px 11px;margin:7px 0;font-size:12px}
.script b{color:#F4E5A7}
.script ul{margin:6px 0 0 16px}.script li{margin:4px 0;font-size:11.5px}
.sec{page-break-inside:avoid;margin-bottom:14px}
.sec h2{font-size:15px;margin:0 0 2px;background:#0B1730;color:#F4E5A7;padding:7px 11px;border-radius:7px 7px 0 0}
.secmeta{font:600 10.5px Arial;color:#4b5563;background:#f3f4f6;padding:5px 11px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb}
.run{display:block;background:#e8f0fe;color:#12325e;padding:6px 11px;font:700 10.5px Arial;text-decoration:none;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;word-break:break-all}
table{width:100%;border-collapse:collapse;border:1px solid #e5e7eb}
td{padding:6px 8px;border-top:1px solid #eef0f5;vertical-align:top}
.n{font-weight:800;color:#b58a1f;width:22px}
.a{font-weight:700}
.who{font-size:11px;color:#12325e}
.ph{font:11.5px monospace;color:#065f46;white-space:nowrap}
.meta{font-size:10px;color:#6b7280}
.hs{background:#dcfce7;color:#065f46;font:700 9px Arial;padding:1px 5px;border-radius:7px}
.ln{background:#0B1730;color:#F4E5A7;font:700 9px Arial;padding:1px 6px;border-radius:7px}
.fl{background:#fee2e2;color:#991b1b;font:700 9px Arial;padding:1px 5px;border-radius:7px}
.cd{background:#fef3c7;color:#92400e;font:700 9px Arial;padding:1px 5px;border-radius:7px}
.note{border-bottom:1px dotted #9ca3af;height:13px;margin-top:4px}
.toc td{font-size:11.5px}
/* check-off: a big empty box under each door number Carlos ticks when the door is done */
.done{width:30px;text-align:center;vertical-align:top;padding-top:7px}
.box{display:inline-block;width:17px;height:17px;border:2px solid #0B1730;border-radius:4px;margin-top:5px}
/* the result strip — tick what happened, no writing needed for the common cases */
.tick{margin-top:6px;font:700 10px Arial;color:#374151}
.tick .opt{display:inline-block;margin:0 9px 3px 0;white-space:nowrap}
.tick .opt i{display:inline-block;width:11px;height:11px;border:1.5px solid #6b7280;border-radius:2px;margin-right:4px;vertical-align:-1px}
.tick .win i{border-color:#065f46;border-width:2px}
.tick .win{color:#065f46}
.tick .dead i{border-color:#991b1b}
.tick .dead{color:#991b1b}
/* one write line for the phone/answer, and the buying data demoted to a dim footnote */
.wl{border-bottom:1px solid #cbd5e1;height:15px;margin-top:5px}
.wl.lbl{border:0;height:auto;font:600 9.5px Arial;color:#6b7280;margin-top:6px}
.dim{font-size:9.5px;color:#9aa3af;margin-top:4px}
"""

SCRIPT = """
<div class="script">
<div class="must"><b>SAY THIS FIRST, WORD FOR WORD, AT EVERY DOOR:</b><br>
&ldquo;Biscayne Solutions Group is not associated with the government, and we are not approved by the
government or your lender.&rdquo;<br>
<span style="font-size:10.5px;opacity:.9">This sentence is not optional and it is not small talk. It
is the line that keeps this legal. Say it before anything else.</span></div>

<h2>&#128172; THE SCRIPT &mdash; say it like this, every door</h2>
<div class="say"><b>KNOCK:</b> "Good morning, are you [owner name]? My name is Carlos, I'm with Miami
Solutions Group. We are not associated with the government and we are not approved by the government
or your lender. I'm not selling you anything."</div>
<div class="say"><b>WHY YOU'RE THERE (read the plaintiff name OFF THE CARD, don't say "the bank"):</b>
"There's a foreclosure case filed on this address by [name printed on the card], as of the date on my
sheet. I came now because of where the case is, not because anything is due today."</div>
<div class="say"><b>THE ONE QUESTION:</b> "What happened with the house?" &mdash; then STOP TALKING.
Let them answer. That answer is the whole visit.</div>
<div class="say"><b>WHAT WE ACTUALLY DO (this is the whole offer &mdash; do not add to it):</b>
"We buy houses in this situation, or we help people sell before the auction date. That's it. If you
want to keep the house or fight the case, that's your own attorney's call, not ours &mdash; and there
are free HUD-approved housing counselors for that, I can write the number down for you."</div>
<div class="say"><b>CLOSE:</b> "What's the best number for Jose to call you?" &mdash; GET THE NUMBER.
That is the win. You are not closing anything at the door.</div>
<div class="say"><b>IF THEY PUSH FOR MORE:</b> "I don't want to guess and give you wrong information.
Jose knows the details and it costs nothing to talk to him."</div>

<h2 style="margin-top:14px">&#9940; NEVER SAY &mdash; these are the ones that create real liability</h2>
<ul>
<li><b>Never</b> "I can stop the foreclosure" / "we can save your house." That promise is exactly what
Florida law punishes (FS 501.1377) &mdash; and it is the fastest way to turn a knock into a lawsuit.</li>
<li><b>Never</b> offer or discuss a <b>loan modification</b>, a <b>short sale</b> as our service, or
<b>bankruptcy</b>. Negotiating those for a fee needs licenses we don't have, and telling someone to
consider bankruptcy is legal advice from a non-lawyer. Say "that's for your attorney or a HUD
counselor" and move on.</li>
<li><b>Never</b> ask for money, a deposit, or a signature. We charge nothing up front, ever.</li>
<li><b>Never</b> say we're the bank, a lawyer, the county, or a "rescue" company.</li>
<li><b>Never</b> quote a dollar figure that isn't printed on the card.</li>
<li><b>Never</b> say "there's no auction date." Say <i>"as of when we checked, no sale date was set."</i>
The case can change after this book is printed.</li>
<li>They say <b>stop / not interested / don't come back</b> &rarr; you leave, and you text Alex the
same day. That address is done permanently.</li>
</ul>

<h2 style="margin-top:14px">&#128100; WHO ANSWERS THE DOOR</h2>
<ul>
<li><b>The owner on the card</b> &rarr; run the script.</li>
<li><b>Someone else (tenant, family, roommate)</b> &rarr; do NOT mention foreclosure, a case, a bank,
or money. Say only: "I'm looking for [name] about the property. Is there a good time or a good
number?" Leave a card. Walk away. Saying why to the wrong person is a privacy problem.</li>
<li><b>They say the owner died</b> &rarr; "I'm sorry, I didn't know." Do not discuss the case. Ask
only who is handling the estate and how to reach them. Text Alex. Several cards in this book are
marked ESTATE for exactly this reason.</li>
<li><b>Nobody home</b> &rarr; leave the card, mark NH on the line, keep moving. Never look in windows,
never go into a back yard.</li>
</ul>

<h2 style="margin-top:14px">&#9989; WRITE THIS DOWN AT EVERY DOOR</h2>
<ul>
<li>Best phone + best time to call &nbsp;|&nbsp; what happened with the house</li>
<li>Are they still living there? Anyone else on the deed? Listed with a realtor?</li>
</ul>
<div style="margin-top:10px;font-size:11px;color:#F4E5A7"><b>PHONE ON THE CARD?</b> Call it on the way
&mdash; "I'm around the corner, I was going to stop by" beats a cold knock.</div>
</div>

<div class="script" style="page-break-after:always">
<h2>&#128737; SAFETY, HOURS AND AUTHORITY &mdash; read before the first door</h2>
<div class="must"><b>JOSE:</b> ask Alex for the cell &nbsp;&nbsp;|&nbsp;&nbsp; <b>ALEX:</b> (786) 631-1823
&nbsp;&nbsp;|&nbsp;&nbsp; <b>Emergency: 911 first, then Alex.</b></div>
<ul>
<li><b>Hours:</b> 9:00 AM to 7:00 PM only. No Sundays. Never after dark.</li>
<li><b>Carry photo ID and an BSG card.</b> If anyone asks who you are, show both, no argument.</li>
<li><b>NO SOLICITING sign</b> &rarr; do not knock. Mark NS and move on. It is not worth the complaint.</li>
<li><b>Never</b> open a gate, enter a yard past a closed gate, pass a dog, or knock on a door that is
standing open. Never step inside a house, even if invited &mdash; talk on the porch.</li>
<li><b>Hostile person:</b> "Sorry to bother you, have a good day." Turn, walk to the truck, leave. Do
not explain, do not argue, do not go back. Mark DNC and text Alex.</li>
<li><b>Police show up:</b> be polite, hands visible. "I'm with a private company doing door-to-door
contact from public court records. Here's my ID and my list." If they ask you to leave, leave
immediately. Call Alex right after.</li>
<li><b>PERMITS:</b> several cities in this book (Homestead, Florida City, Hialeah, Hialeah Gardens,
Miami Beach, Sunny Isles, Aventura, North Bay Village, Miami Gardens) can require a solicitor or
canvasser permit. <b>Ask Alex before working a section in those cities.</b> A permit question at the
door is answered with "let me check with my office" &mdash; then leave.</li>
<li><b>Gated buildings / condos:</b> do not talk your way past a front desk or follow someone in.
Leave a card with the desk if they take it, otherwise skip it and mark GATED.</li>
</ul>
</div>
"""


def main():
    rows = json.load(open(SRC, encoding='utf-8'))
    rows = [r for r in rows if r.get('lat')]
    groups = [order(g) for g in cluster(rows)]
    groups.sort(key=lambda g: (-len(g), name_of(g)))
    today = datetime.date.today().isoformat()

    stale = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    doors = sum(len(g) for g in groups)
    out = ['<html><head><meta charset="utf-8"><style>%s</style></head><body>' % CSS,
           '<h1>Carlos &mdash; Pre-Foreclosure Door Book</h1>',
           '<div class="sub"><b>Case status verified %s.</b> DO NOT USE THIS BOOK AFTER %s &mdash; ask Alex '
           'to reprint. Cases get dismissed, reinstated and sale-dated every week, and the card in your hand '
           'is a snapshot, not live. %d doors &middot; %d sections &middot; dismissed and closed cases were '
           'removed at print time.</div>' % (today, stale, doors, len(groups)),
           '<table class="toc"><tr><td colspan="4"><b>THE ROUTE BOOK</b> &mdash; one section is roughly one run</td></tr>']
    for i, g in enumerate(groups, 1):
        out.append('<tr><td class="n">%d</td><td><b>%s</b></td><td>%d doors</td>'
                   '<td class="meta">%d with a phone &middot; %d owner-occupied</td></tr>'
                   % (i, H.escape(name_of(g)), len(g), sum(1 for r in g if r['ph']),
                      sum(1 for r in g if r['hs'])))
    out.append('</table>')
    out.append(SCRIPT)

    for i, g in enumerate(groups, 1):
        stops = [stop(r) for r in g]
        out.append('<div class="sec"><h2>%d. %s &nbsp;<span style="font-weight:400">(%d doors)</span></h2>'
                   % (i, H.escape(name_of(g)), len(g)))
        out.append('<div class="secmeta">%d with a phone number &middot; %d owner-occupied &middot; '
                   'tightest driving order below, top to bottom</div>'
                   % (sum(1 for r in g if r['ph']), sum(1 for r in g if r['hs'])))
        for k in range(0, len(stops), 8):
            ch = stops[k:k + 8]
            if len(ch) > 1:
                out.append('<a class="run" href="%s">&#128663; Google Maps route%s: %s</a>'
                           % (H.escape(maps(ch)), (' part %d' % (k // 8 + 1)) if len(stops) > 8 else '',
                              ' &rarr; '.join(H.escape(s.split(',')[0]) for s in ch)))
        out.append('<table>')
        for n, r in enumerate(g, 1):
            ph = ' &middot; '.join(fmt_ph(p) for p in r['ph'][:2]) if r['ph'] else 'no number &mdash; knock'
            # ESTATE / ABSENTEE / MULTI-UNIT come from the live-parcel verification pass. They
            # change how the door is worked (see WHO ANSWERS THE DOOR), so they print in red.
            tags = ('<span class="hs">LIVES THERE</span> ' if r['hs'] else '') + \
                   ('<span class="cd">CONDO</span> ' if r['condo'] else '') + \
                   ''.join('<span class="fl">%s</span> ' % H.escape(f) for f in (r.get('flags') or [])) + \
                   ('<span class="ln">%s</span> ' % H.escape(r.get('lane', '')) if r.get('lane') else '')
            out.append(
                '<tr><td class="done"><div class="n">%d</div><div class="box"></div></td>'
                '<td><div class="a">%s</div>'
                '<div class="who">%s %s</div>'
                '<div class="meta">%sPLAINTIFF: %s &middot; case %s</div>'
                '<div class="tick">'
                '<span class="opt"><i></i>Nobody home</span>'
                '<span class="opt"><i></i>Talked</span>'
                '<span class="opt"><i></i>Left letter</span>'
                '<span class="opt win"><i></i>GOT NUMBER</span>'
                '<span class="opt dead"><i></i>Not interested</span>'
                '</div>'
                '<div class="wl lbl">Best # &amp; time to call, or what they said:</div>'
                '<div class="wl"></div>'
                '<div class="dim">$%s value &middot; %s bd / %s ba &middot; %s sqft &middot; filed %s</div></td>'
                '<td class="ph">%s</td></tr>'
                % (n, H.escape(r['addr']), H.escape(r['owner'][:34]), tags,
                   _datebit(r), H.escape(r['pl']), H.escape(str(r['c'])),
                   format(r['val'], ',d') if r['val'] else '?', r['beds'] or '?', r['baths'] or '?',
                   format(int(r['sqft']), ',d') if r.get('sqft') else '?',
                   H.escape(str(r['filed'])), ph))
        out.append('</table></div>')

    out.append('<div class="sub" style="margin-top:10px">%d doors total. Anything you learn goes on '
               'the line under the address &mdash; hand it back to Alex and it goes into the system.</div>'
               '</body></html>' % doors)
    html = '\n'.join(out)
    hp = os.path.join(HERE, 'Carlos_PreForeclosure_Book_%s.html' % today)
    open(hp, 'w', encoding='utf-8').write(html)
    print('sections:', len(groups), '| doors:', doors)
    for i, g in enumerate(groups, 1):
        print('  %d. %-28s %2d doors' % (i, name_of(g), len(g)))
    return hp


if __name__ == '__main__':
    main()
