#!/usr/bin/env python
"""msg_card — the actual business card. 10-up on a letter sheet, bilingual, print-ready.

WHY THIS EXISTS (2026-08-27)
msg_flyer.py opens by calling itself "Jesse's replacement for the business card" — the 8/12
doctrine was that cards go in a drawer and die, so the leave-behind became a quarter-page
postcard. That is still true FOR A DOOR. It was never true for the rest of the job: a closing
table, an attorney's office, an HOA manager, a title rep, another investor. A postcard is the
wrong object to hand those people, and until now there was no card at all — nothing in the repo,
nothing in Drive, nothing in the mail. This is the missing artifact, not a replacement for the
flyer.

DESIGN DECISIONS THAT ARE LOAD-BEARING

 * WHITE GROUND, navy and gold as ink. The obvious card is a full-bleed navy rectangle with the
   gold shield reversed out of it. That card cannot be printed the way this shop prints: letter
   printers do not print borderless, so a full-bleed navy sheet returns with white strips on all
   four edges, and on a 10-up sheet where cards butt edge to edge (no gutter) EVERY card wears a
   white sliver on at least one side. Same lesson the flyer learned the hard way in --stock. On
   white, the un-printable margin is invisible, the card can be run on any printer or any shop,
   and card stock supplies the richness the ink would have.

 * THE NAME IS NOT IN THE ARTWORK. msg_brand's rule, verbatim: the emblem is a MARK only, and the
   company name is spelled out once, as footer text. That rule is followed here — shield top left,
   "Miami Solutions Group" once along the bottom rule, nowhere else.

 * NO " LLC", EVER. The TRUTH GATE from msg_flyer/msg_letter/msg-web: "Miami Solutions Group LLC"
   (L22000200556) is a filed Florida entity belonging to SOMEBODY ELSE. Printing that suffix on a
   card handed to a distressed homeowner is a MARS 1015.3 / FDUTPA misrepresentation, and a card
   is the worst place for it because it outlives every email. _display_llc() strips the suffix and
   is not optional. Delete the gate only when an entity we actually own is filed.

 * IDENTITY ONLY — NO OFFER. Deliberately there is no "free foreclosure consultation", no "we can
   stop the sale", no "3, 4, sometimes 5 options". Those lines are what make a piece a commercial
   communication OFFERING mortgage assistance relief, and the moment a card says them, the FTC
   MARS Rule (12 CFR 1015.4) wants its full disclosure block — five lines of microprint that do
   not fit on 3.5 x 2 inches and would swallow the card. A card that only says who you are and how
   to reach you is not a MARS advertisement and needs none of it. The pitch lives on the flyer,
   which has room for the disclosures the pitch requires. Keep it that way.

 * ALL TEN CARDS ARE IDENTICAL, which is what makes the duplex trivial: front sheet is 10 English
   cards, back sheet is 10 Spanish. Because every position holds the same card, it does not matter
   whether the printer flips on the long or the short edge — nothing can land misregistered onto
   the wrong card. Print duplex either way.

LAYOUT: Avery 5371 / 8371 geometry (2 columns x 5 rows, 3.5 x 2 in, 0.75 in side margins,
0.5 in top/bottom, no gutter — sums to exactly 8.5 x 11). So this prints on plain card stock and
cuts on a guillotine, or drops straight onto perforated Avery stock with no adjustment.

Run:  python msg_card.py                      # colour, sender.json identity
      python msg_card.py --mono               # one-ink: black/white emblem, for B&W printing
      python msg_card.py --name X --phone Y   # somebody else's card
"""
import argparse
import datetime
import html as H
import json
import os

import msg_brand

HERE = os.path.dirname(os.path.abspath(__file__))

NAVY = '#183450'
NAVY_DEEP = '#03182D'
GOLD = '#A87835'

# Same gate as msg_flyer._display_llc — see the docstring. Never print an entity we do not own.
UNOWNED = ('miami solutions group',)


def _display_llc(raw):
    raw = (raw or '').strip()
    bare = raw.lower().replace(',', '').replace('llc', '').strip()
    if bare in UNOWNED:
        return raw.replace(' LLC', '').replace(', LLC', '').strip()
    return raw


def sender():
    try:
        s = json.load(open(os.path.join(HERE, 'sender.json'), encoding='utf-8'))
        return (s.get('name') or 'Alejandro Gonzalez', s.get('phone') or '',
                s.get('email') or '', s.get('addr') or '',
                _display_llc(s.get('llc') or 'Miami Solutions Group'))
    except Exception:
        return ('Alejandro Gonzalez', '', '', '', 'Miami Solutions Group')


def card(mark, name, title, phone, email, addr, llc, always, area):
    return """<div class="card">
  <div class="top">
    <img class="mark" src="%s" alt="">
    <div class="who">
      <div class="nm">%s</div>
      <div class="ttl">%s</div>
    </div>
  </div>
  <div class="area">%s</div>
  <div class="tel">%s</div>
  <div class="always">%s</div>
  <div class="rule"></div>
  <div class="foot"><span class="co">%s</span>%s</div>
  <div class="em">%s</div>
</div>""" % (mark, H.escape(name), title, area, H.escape(phone), always, H.escape(llc),
             ('<span class="sep">&middot;</span>' + H.escape(addr)) if addr else '',
             H.escape(email))


CSS = """
@page{size:Letter;margin:0}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI',Arial,sans-serif;color:%(navy)s}
/* Avery 5371/8371: 0.75in sides, 0.5in top/bottom, 2 x 5, no gutter. Sums to 8.5 x 11 exactly. */
.sheet{width:8.5in;height:11in;padding:.5in .75in;display:grid;
       grid-template-columns:3.5in 3.5in;grid-template-rows:repeat(5,2in);
       page-break-after:always}
.sheet:last-child{page-break-after:auto}
.card{width:3.5in;height:2in;background:#fff;padding:.17in .2in;position:relative;
      display:flex;flex-direction:column;
      outline:.5pt dashed rgba(0,0,0,.22);outline-offset:-.5pt}
.top{display:flex;align-items:center;gap:7pt}
.mark{height:40pt;width:auto;display:block}
.nm{font-size:11.6pt;font-weight:900;letter-spacing:.012em;line-height:1.1}
.ttl{font-size:6.8pt;font-weight:800;letter-spacing:.15em;color:%(gold)s;margin-top:1.5pt}
/* The service area is not decoration: it fills what was a dead 0.4in void between the name block
   and the phone, and it is the one thing a title rep or attorney actually wants off a card. It is
   a statement of WHERE we work, never an offer of what we will do — see the MARS note above. */
.area{font-size:7pt;font-weight:800;letter-spacing:.1em;color:%(navy)s;opacity:.62;
      margin-top:8pt;white-space:nowrap}
.tel{font-size:17.5pt;font-weight:900;letter-spacing:.005em;margin-top:auto;line-height:1}
.always{font-size:6.2pt;font-weight:800;letter-spacing:.11em;color:%(navy)s;opacity:.75;margin-top:2.5pt}
.rule{border-top:1.2pt solid %(gold)s;margin:5pt 0 4pt}
.foot{font-size:6.6pt;font-weight:700;letter-spacing:.02em}
.co{font-weight:900}
.sep{margin:0 3pt;color:%(gold)s}
.em{font-size:6.6pt;font-weight:600;margin-top:1.5pt;opacity:.8}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', default='')
    ap.add_argument('--phone', default='')
    ap.add_argument('--email', default='')
    ap.add_argument('--title', default='ACQUISITIONS')
    # One-ink output: the brand file ships a navy->black / gold->white emblem precisely because a
    # straight grayscale of navy-on-gold collapses into one mid-grey mass. Use it, never a filter.
    ap.add_argument('--mono', action='store_true')
    a = ap.parse_args()

    dn, dp, de, da, llc = sender()
    name = a.name or dn
    phone = a.phone or dp
    email = a.email or de or 'agonzalez0311707@gmail.com'
    addr = da or '231 NW 109th Ave, Miami, FL 33172'
    if not phone:
        raise SystemExit('no phone: set sender.json or pass --phone')

    mark = msg_brand.MONO_BW_B64 if a.mono else msg_brand.MONO_B64
    en = card(mark, name, H.escape(a.title), phone, email, addr, llc,
              'CALL OR TEXT &middot; 24 HOURS &middot; 7 DAYS',
              'MIAMI-DADE &middot; BROWARD &middot; PALM BEACH')
    es = card(mark, name, 'ADQUISICIONES', phone, email, addr, llc,
              'LLAME O ENV&Iacute;E TEXTO &middot; 24 HORAS &middot; 7 D&Iacute;AS',
              'MIAMI-DADE &middot; BROWARD &middot; PALM BEACH')
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>MSG Business Card</title>'
           '<style>%s</style></head><body>'
           '<div class="sheet">%s</div><div class="sheet">%s</div>'
           '</body></html>') % (CSS % {'navy': NAVY, 'gold': GOLD}, en * 10, es * 10)

    today = datetime.date.today().isoformat()
    stem = 'MSG_Business_Card_%s_%s' % ('mono' if a.mono else 'color', today)
    hp = os.path.join(HERE, stem + '.html')
    open(hp, 'w', encoding='utf-8').write(doc)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto('file:///' + hp.replace(os.sep, '/'))
        pg.wait_for_timeout(500)
        # Every card is identical, so nothing can misregister; but a card whose content overruns
        # 3.5 x 2 would bleed into its neighbour with no gutter to absorb it. Refuse that outright.
        bad = pg.evaluate("""() => Array.from(document.querySelectorAll('.card'))
            .map((c,i) => ({i, over: Math.round(c.scrollHeight - c.clientHeight)}))
            .filter(r => r.over > 1)""")
        if bad:
            b.close()
            raise SystemExit('card content overruns its 3.5x2in box on %d card(s) '
                             '(first by %dpx) — shorten the name/title, do not shrink the type.'
                             % (len(bad), bad[0]['over']))
        pdf = pg.pdf(format='Letter', print_background=True, prefer_css_page_size=True,
                     margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    pp = os.path.join(HERE, stem + '.pdf')
    open(pp, 'wb').write(pdf)
    print('wrote %s (%.0f KB) — 20 cards: 10 English (p1) + 10 Spanish (p2)'
          % (pp, len(pdf) / 1024))
    print('Print on white card stock (80-110 lb cover), duplex — flip either edge, all cards are '
          'identical — 100%% scale / Actual size, then cut on the guides.')


if __name__ == '__main__':
    main()
