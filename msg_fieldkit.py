#!/usr/bin/env python
"""msg_fieldkit — two field cards on one sheet: the no-soliciting rule card + the pack list.

Both are ⚡-this-week Field Kit items. They print two-up on one portrait letter sheet, cut once:

  TOP  — NO-SOLICITING RULE CARD. Laminate it, it lives on the clipboard. Carlos looks at it the
         moment he is unsure whether a sign means "skip this door." The behaviour is the whole point:
         a posted no-soliciting sign means DO NOT KNOCK, mark NS, move on. Florida added a do-not-knock
         rule in 2026 (referenced internally as FS 501.062) that makes knocking a posted door the
         violation, with a per-door penalty — and Carlos is the one standing at the door. $3 of
         lamination against that is not a decision.

  BOTTOM — FIELD KIT PACK LIST. Tape it inside the bag. One bag, packed the night before, same every
         time, so a 6am start never leaves the stapler or the ID on the counter.

No PII, no data source — it always builds.

Run:  python msg_fieldkit.py
"""
import datetime
import os

HERE = os.path.dirname(os.path.abspath(__file__))

CSS = """
@page{size:letter;margin:0}
*{box-sizing:border-box;margin:0;padding:0}
body{font:12px/1.4 "Segoe UI",Arial,sans-serif;color:#111827}
.card{width:8.5in;height:5.5in;padding:.4in .5in;position:relative;overflow:hidden}
.card.top{border-bottom:1.5pt dashed #9ca3af}
.cut{position:absolute;right:.2in;top:.14in;font:700 8px Arial;color:#9ca3af;letter-spacing:.1em}
h1{font-size:22px;color:#0B1730;margin-bottom:2px}
h1.warn{color:#7f1d1d}
.tag{font:700 10px Arial;letter-spacing:.12em;text-transform:uppercase;color:#6b7280;margin-bottom:12px}
.big{background:#7f1d1d;color:#fff;border-radius:9px;padding:11px 15px;font-size:15px;font-weight:700;margin:10px 0}
.big span{color:#fde68a}
.two{display:flex;gap:20px;margin-top:8px}
.col{flex:1}
.col h3{font-size:11px;color:#0B1730;text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px;border-bottom:1px solid #e5e7eb;padding-bottom:3px}
ul{list-style:none}
li{margin:5px 0;padding-left:20px;position:relative;font-size:12px;line-height:1.35}
li:before{content:"";position:absolute;left:0;top:2px;width:11px;height:11px;border:1.5px solid #0B1730;border-radius:2px}
li.dot:before{content:"\\2022";border:0;color:#7f1d1d;font-size:16px;top:-3px;left:2px}
.note{font-size:10px;color:#6b7280;margin-top:10px;line-height:1.4}
.k{color:#065f46;font-weight:700}
"""

NOSOLICIT = """
<div class="card top">
  <div class="cut">&#9986; LAMINATE &mdash; keep on the clipboard</div>
  <h1 class="warn">&#9940; No-Soliciting = Do Not Knock</h1>
  <div class="tag">Florida do-not-knock rule (2026) &middot; the door you skip can&rsquo;t complain</div>

  <div class="big">See a NO-SOLICITING / NO-TRESPASSING / NO-CANVASSING sign at the door or gate?
  <span>Do not knock.</span> Mark <span>NS</span> on the log. Next door.</div>

  <div class="two">
    <div class="col">
      <h3>What counts as a sign</h3>
      <ul>
        <li class="dot">Any posted sign at the entrance saying no soliciting, no trespassing, no canvassing, no peddlers &mdash; a sticker or a plaque counts</li>
        <li class="dot">A gated community or building posting it at the entrance = the WHOLE place is off unless the office says yes</li>
      </ul>
    </div>
    <div class="col">
      <h3>What to do</h3>
      <ul>
        <li class="dot">Skip it. Leave nothing &mdash; not even a postcard in the door</li>
        <li class="dot">Log it as NS so we never route it again</li>
        <li class="dot">Unsure if a sign counts? Treat it as a yes and skip</li>
      </ul>
    </div>
  </div>
  <div class="note">Knocking a posted door is the violation, and it carries a per-door penalty &mdash; you
  are the one at the door, so this rule protects you first. When in doubt, walk. &nbsp; <b>Alex (786) 631-1823.</b></div>
</div>"""


def _kit():
    carry = [
        'Door book (today&rsquo;s routes) + <b>3&ndash;4 door-log sheets</b> on the clipboard',
        'Postcards &mdash; a full stack',
        'Blank door letters &mdash; a stack, English/Spanish',
        'Blank envelopes (for the sealed &ldquo;[first name] / PERSONAL&rdquo; drop)',
        'Two pens + a mini stapler',
        'Laminated no-soliciting card (above) + this list',
        'Photo ID + an MSG card &mdash; show both, no argument, if asked who you are',
        'Phone charged + a battery pack',
    ]
    heat = [
        'Two bottles of water, frozen the night before',
        'Electrolytes',
        'Hat + sunscreen &mdash; it is August',
        'Comfortable walking shoes',
    ]
    prep = [
        '<b>Pre-staple 2&ndash;3 letter+postcard sets</b> for the doors that open',
        'Leave the rest of the letters LOOSE &mdash; blanks get filled at the door',
        'Pack it the night before, same bag every time',
        'First door no earlier than 9:00 AM. None after 7:00 PM. No Sundays',
    ]

    def col(title, items, boxed=True):
        lis = ''.join('<li%s>%s</li>' % ('' if boxed else ' class="dot"', x) for x in items)
        return '<div class="col"><h3>%s</h3><ul>%s</ul></div>' % (title, lis)

    return ("""
<div class="card">
  <div class="cut">&#9986; TAPE INSIDE THE BAG</div>
  <h1>&#127890; Field Kit &mdash; pack every time</h1>
  <div class="tag">One bag &middot; packed the night before &middot; same every time</div>
  <div class="two">%s%s</div>
  <div class="two" style="margin-top:12px">%s<div class="col"><h3>Before you leave</h3><ul>%s</ul></div></div>
</div>""" % (col('Carry', carry), col('Heat kit', heat),
            '', ''.join('<li class="dot">%s</li>' % x for x in prep)))


def main():
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>Field Kit</title>'
           '<style>%s</style></head><body>%s%s</body></html>' % (CSS, NOSOLICIT, _kit()))
    today = datetime.date.today().isoformat()
    hp = os.path.join(HERE, 'MSG_Field_Kit_%s.html' % today)
    open(hp, 'w', encoding='utf-8').write(doc)

    from playwright.sync_api import sync_playwright
    outs = [os.path.join(HERE, 'MSG_Field_Kit_%s.pdf' % today)]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        outs.append(os.path.expanduser(os.path.join(
            '~', 'OneDrive', 'Desktop', 'DEALFLOW', 'MSG_Field_Kit_%s.pdf' % today)))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto('file:///' + hp.replace(os.sep, '/'))
        pg.wait_for_timeout(300)
        pdf = pg.pdf(format='Letter', print_background=True, prefer_css_page_size=True,
                     margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    for o in outs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'wb').write(pdf)
        print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))
    print('Two cards, one sheet, cut once. Laminate the top (no-soliciting), tape the bottom in the bag.')


if __name__ == '__main__':
    main()
