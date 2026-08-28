#!/usr/bin/env python
"""bsg_cards.py — print-ready business cards for Biscayne Solutions Group.

WHO THIS CARD IS HANDED TO
A homeowner who just got served, standing in their doorway, deciding in about two seconds whether
the person in front of them is a professional or a vulture. That is the whole design brief. It is
NOT a networking card — it is a credibility card, and the failure mode is looking like the four
other investors who knocked that week.

WHAT THAT MEANS CONCRETELY
  * No "WE BUY HOUSES CASH", no starbursts, no yellow. That styling is the tell.
  * The back carries what we do AND what we are not. On a flyer that is compliance; on a card it
    is the strongest trust signal available, because nobody predatory prints their own limits.
  * Deep navy + steel, generous white space, real type hierarchy. It should read closer to a
    title company than to a wholesaler.

THE " LLC" IS WITHHELD, DELIBERATELY
entity.display_llc() is the same truth-gate every other surface uses: Biscayne Solutions Group is
not in the Sunbiz index yet, so the suffix does not print. A business card is the LAST place to
assert a registration that does not exist -- it is the artifact a homeowner keeps, photographs,
and hands to their lawyer. The generator refuses to print a suffix the register cannot support.

PRINT SPEC (US standard)
  trim      3.5in x 2.0in
  bleed     0.125in on every side  -> 3.75in x 2.25in artboard
  safe zone 0.125in inside trim    -> nothing important within 0.25in of the artboard edge
  300 DPI, CMYK-safe colors (no neon, no pure RGB blue)

Usage:
  python bsg_cards.py                  # all layouts, front + back, review sheet + print PDFs
  python bsg_cards.py --layout b       # just one
  python bsg_cards.py --logo path.png  # swap the brand mark
"""
import argparse
import base64
import datetime as dt
import html as H
import os

import disclaimer as D

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- identity -------------------------------------------------------------------------------
CO = 'Biscayne Solutions Group'
try:
    import entity
    _n = entity.display_llc()[0]
    if _n:
        CO = _n
except Exception:
    pass

NAME = 'Alejandro Gonzalez'
# "ACQUISITIONS" split the reviewers. Compliance called it protective — it signals a
# principal buyer, which is the express FS 475 own-account exemption. The homeowner reviewer
# called it the worst word on the card: it is the vocabulary of the servicer and the plaintiff's
# law firm, aimed at a woman whose house is the thing being acquired.
# Both are right, so neither wins on its own terms. "I buy houses in Miami-Dade" does the SAME
# legal work — it states own-account purchase plainly — while reading like a person instead of a
# department. It also fixes a separate defect: the front never said what the business does, and
# half the time a card lands face-up on a counter.
# WRONG FOR TWO ROUNDS: this said "Acquisitions", then "I buy houses in Miami-Dade".
# BSG's own homepage copy says the opposite in as many words -- "BSG is not a one-size-fits-all
# home-buying company". It is a homeowner ADVISORY service: organise the options, explain the
# tradeoffs, refer out to licensed professionals. Selling for cash is ONE of five paths it
# presents, alongside refinance, listing with a realtor, home equity investment, and simply
# understanding the legal timeline.
# The pitch every other channel already uses, verbatim from call_mode's CIOC script:
#     "A free 5 minutes with our senior advisor, 30 plus years, gets you every option."
# Note the advisor is NOT Alejandro. He opens the door; the advisor takes the call. A card that
# says "I buy houses" both contradicts the brand and promises the wrong person.
TITLE = 'Homeowner advisory'
TITLE_ES = 'Asesoria para propietarios'
SPANISH_LINE = 'Hablo español'
OFFER = 'A free 5-minute call with our senior advisor'
OFFER2 = '30+ years. Every option on the table.'
PHONE = '(786) 631-1823'
EMAIL = ''          # deliberately blank — see the note in main()
# VERIFIED LIVE 2026-08-28 (HTTP 200) before printing it. A dead URL on a card is worse than no
# URL; an unverified one is a coin flip. This is also the single strongest missing trust element --
# it is what the daughter types in when she Googles the company after the door knock.
WEB = 'BSGFlorida.com'

# ---- brand ----------------------------------------------------------------------------------
# #0B1730 measured L* ~8.1 -- BELOW the CMYK gamut floor. CMYK cannot hold a chromatic dark that
# deep, so it converts to a near-neutral rich black and the navy disappears entirely. Worse, a
# naive sRGB->SWOP conversion lands near 287% total area coverage on a solid that covers 100% of
# every card back -- over the 240-260% limit for card stock, which means set-off and mottle.
# #16294d is L* ~16.9, holds its hue in CMYK, and builds around 220% TAC. It was already defined
# in this file and never used.
NAVY = '#16294d'
NAVY_DEEP = '#0B1730'   # screen/board use only -- do not send to a press
# STEEL was #5b6b82 -- a four-plate build. At 6.5-8pt a 4-plate colour shows a registration
# fringe on every letterform. This is a near-neutral that presses as mostly K.
STEEL = '#63656b'
GOLD = '#C9A227'
INK = '#1a2233'
PAPER = '#ffffff'


def find_logo():
    """ONE known path, not a filename guess.

    Fuzzy discovery matched 'logo*' across Downloads/Pictures and picked up an unrelated "City
    Gear" logo -- guessing at filenames is worse than not guessing, because it silently prints the
    wrong brand. So: exactly one drop point, any common extension.

        brand/bsg-logo.(svg|png|jpg)

    This exists because an image attached in chat arrives as pixels in the conversation, NOT as a
    file on disk, and nothing can write those pixels out. One save into this folder is the whole
    handoff; every generator that takes --logo can then point at the same file.
    """
    for ext in ('svg', 'png', 'jpg', 'jpeg', 'webp'):
        p = os.path.join(HERE, 'brand', 'bsg-logo.' + ext)
        if os.path.exists(p):
            return p
    return ''


def logo_uri(path='', mono=''):
    """Data URI for the brand mark.

    Order: an explicit --logo file, then the VECTOR mark in bsg_mark, then the old raster.
    The vector is the default because the raster fails at card sizes -- "FLORIDA" prints at
    1.5-2.5pt inside the bitmap, and its alpha edges are matted toward black so it haloes on
    white stock. `mono` collapses the mark to one colour for reversing out of the navy panel;
    the old asset had no working single-colour version at all.
    """
    if path and os.path.exists(path):
        ext = os.path.splitext(path)[1].lstrip('.').lower() or 'png'
        if ext == 'svg':
            import base64 as _b
            return 'data:image/svg+xml;base64,' + _b.b64encode(
                open(path, 'rb').read()).decode('ascii')
        return 'data:image/%s;base64,%s' % (
            'jpeg' if ext in ('jpg', 'jpeg') else ext,
            base64.b64encode(open(path, 'rb').read()).decode('ascii'))
    try:
        import bsg_mark
        return bsg_mark.data_uri(mono)
    except Exception:
        pass
    try:
        import bsg_flyer
        return bsg_flyer.BSG_LOGO_B64
    except Exception:
        return ''


CSS = """
@page{size:3.75in 2.25in;margin:0}
/* WITHOUT THIS THE BACKS PRINT AS BLANK PAPER. Browsers strip background colours from print by
   default, and the navy back and B's panel are both CSS BACKGROUNDS with white text on them --
   so a browser print produces white-on-white. The PDF path is immune (Playwright is told
   print_background=True, verified: #0B1730 is present in the emitted content stream), but the
   generator also hands out HTML and somebody will print that. */
*{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;font-family:'Segoe UI',-apple-system,Helvetica,Arial,sans-serif;color:%(ink)s}
/* The artboard IS the bleed area. Trim happens 0.125in inside it on every edge, so anything
   that must survive the cut lives inside .safe. */
.card{position:relative;width:3.75in;height:2.25in;overflow:hidden;background:%(paper)s;
      page-break-after:always}
.safe{position:absolute;left:.25in;top:.25in;right:.25in;bottom:.25in}
.bleedbg{position:absolute;inset:0}

/* --- shared type ------------------------------------------------------------------------- */
.nm{font-size:13.5pt;font-weight:700;letter-spacing:.01em;line-height:1.1;margin:0}
.ti{font-size:8.5pt;font-weight:500;letter-spacing:.005em;
    color:%(steel)s;margin:3pt 0 0}
.co{font-size:8.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:%(navy)s}
.ph{font-size:11pt;font-weight:700;letter-spacing:.01em;color:%(navy)s}
.sm{font-size:7pt;color:%(steel)s;letter-spacing:.02em}
.reachsm{font-size:7pt;font-weight:600;color:%(steel)s;letter-spacing:.05em}
.esline{font-size:7.5pt;font-weight:600;color:%(steel)s;margin-top:1pt}
.weburl{font-size:8pt;font-weight:700;color:%(navy)s;letter-spacing:.02em;margin-top:6pt}
/* flex-basis + flex-shrink:0 because this is an EMPTY box inside a flex column: min-height:auto
   resolved to 0 and layout A (the only overflowing column) silently shrank it to 1px of
   antialiasing. A divider that disappears under pressure is not a system element. */
.rule{height:2px;flex:0 0 2px;background:%(gold)s;width:34px;border-radius:2px}

/* --- A: centered classic ------------------------------------------------------------------ */
.a{text-align:center}
.a .brandmark{height:.62in;width:auto;display:block;margin:0 auto .07in}
.a .nm{margin-top:.04in}
.a .rule{margin:.07in auto}

/* --- B: white field, logo in FULL COLOUR ---------------------------------------------------
   The navy panel forced the mark to reverse to solid white, which throws away the blue, charcoal
   and grey that ARE the logo. The mark is designed for white; give it white. Navy moves to the
   back, where it still does the brand work and has no logo to destroy. */
.b .lockup{position:absolute;left:.3in;top:50%%;transform:translateY(-50%%);width:1.2in}
.b .divider{position:absolute;left:1.66in;top:.36in;bottom:.36in;width:1px;background:#dbe0e8}
.b .right{position:absolute;left:1.86in;right:.25in;top:.3in;bottom:.3in}
.b .nm{font-size:12pt}
/* --- C: navy band across the top ---------------------------------------------------------- */
.c .band{position:absolute;left:0;right:0;top:0;height:.95in;background:#fff;
          border-bottom:3px solid %(navy)s}
.c .band img{position:absolute;left:.28in;top:50%%;transform:translateY(-50%%);height:.6in}
.c .bandco{position:absolute;right:.28in;top:50%%;transform:translateY(-50%%);color:%(navy)s;
           font-size:8pt;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
           text-align:right;max-width:1.5in;line-height:1.3}
.c .body{position:absolute;left:.28in;right:.28in;top:1.12in}

/* --- D: type-led, minimal ----------------------------------------------------------------- */
.d .brandmark{position:absolute;right:.3125in;top:.3125in;height:.46in;width:auto}
.d .stack{position:absolute;left:.25in;bottom:.25in;right:1in}
.d .nm{font-size:15pt}

/* --- BACK --------------------------------------------------------------------------------- */
.back{background:%(navy)s;color:#fff}
.back .safe{display:flex;flex-direction:column;justify-content:center}
.back h4{margin:0 0 .06in;font-size:8pt;letter-spacing:.16em;text-transform:uppercase;
         color:%(gold)s;font-weight:700}
.back ul{margin:0;padding-left:.14in;list-style:none}
.back li{font-size:8.5pt;line-height:1.5;margin:0 0 1pt;position:relative;padding-left:.11in}
.back li:before{content:'';position:absolute;left:0;top:.055in;width:4px;height:4px;
                border-radius:50%%;background:%(gold)s}
/* Same size as the body, not 6.6pt grey. Fine print reads as "someone made him print this";
   body text reads as "I want you to read this". The content was never the problem. */
.back .doline{font-size:8.5pt;line-height:1.45;margin:0 0 .05in}
.back .free{font-size:8pt;line-height:1.4;color:#eef3fa;margin:0 0 .05in}
.back .not{font-size:7.4pt;line-height:1.4;color:#c9d5e6;margin:.05in 0 .06in}
.back .opts{font-size:7.8pt;line-height:1.45;color:#f2f6fb;margin:.045in 0 0}
.back .mine{font-size:8pt;line-height:1.4;color:#e8eef7}
.back .cta{margin-top:.07in;font-size:11pt;font-weight:700;color:#fff;letter-spacing:.01em}
.back .reach{font-size:7.5pt;font-weight:600;color:#8fa1bb;letter-spacing:.06em}
""" % {'ink': INK, 'paper': PAPER, 'navy': NAVY, 'steel': STEEL, 'gold': GOLD}


def _contact_lines():
    # "call or text" is not decoration: an ashamed person texts before they call, and nothing
    # on the card otherwise grants permission to.
    out = ['<div class="ph">%s</div>' % H.escape(PHONE),
           '<div class="reachsm">call or text</div>']
    if EMAIL:
        out.append('<div class="sm" style="margin-top:2pt">%s</div>' % H.escape(EMAIL))
    # WEB is deliberately NOT emitted here: layout B renders it in its own .weburl block, and
    # having both printed BSGFlorida.com twice on the same card.
    return ''.join(out)


_LOGO_PATH = ''

def mark_inline(mono='', cls='mark'):
    """The mark, inlined.

    An SVG file is READ AND INLINED, never wrapped in <img src="data:image/svg+xml;base64,...">.
    Chromium refuses to paint that and draws a broken-image glyph -- it did it twice here, once
    for the generated mark and again the moment a real file appeared. Inline is also better for
    print: the RIP sees geometry instead of a base64 blob.

    Raster files still go through <img>, which is correct for them.
    `mono` asks for a single-colour build, used to reverse out of the navy panel.
    """
    src = _LOGO_PATH
    if src and os.path.exists(src) and src.lower().endswith('.svg'):
        try:
            svg = open(src, encoding='utf-8').read()
            svg = svg[svg.index('<svg'):]                       # drop any XML prolog/comments
            if mono:
                # reversing a supplied file: recolour every explicit fill/stroke to the mono value
                import re as _re
                svg = _re.sub(r'fill="(?!none)[^"]*"', 'fill="%s"' % mono, svg)
                svg = _re.sub(r'stroke="(?!none)[^"]*"', 'stroke="%s"' % mono, svg)
            return svg.replace('<svg ', '<svg class="%s" ' % cls, 1)
        except Exception:
            pass
    if src and os.path.exists(src):
        u = logo_uri(src)
        return '<img class="%s" src="%s" alt="">' % (cls, u) if u else ''
    try:
        import bsg_mark
        return bsg_mark.svg(mono).replace('<svg ', '<svg class="%s" ' % cls, 1)
    except Exception:
        return ''


def front(layout, logo):
    img = mark_inline('', 'brandmark')
    if layout == 'a':
        return ('<div class="card a"><div class="safe" style="display:flex;flex-direction:column;'
                'align-items:center;justify-content:center">%s'
                '<div class="co">%s</div><div class="rule"></div>'
                '<div class="nm">%s</div><div class="ti">%s</div>'
                '<div class="esline">Hablo espa&ntilde;ol</div>'
                '<div style="margin-top:.09in">%s</div></div></div>'
                % (img, H.escape(CO), H.escape(NAME), H.escape(TITLE), _contact_lines()))
    if layout == 'b':
        # mono='' -> the logo keeps its own colours
        return ('<div class="card b">%s<div class="divider"></div>'
                '<div class="right" style="display:flex;flex-direction:column;justify-content:center">'
                '<div class="nm">%s</div><div class="ti">%s</div>'
                '<div class="esline">%s</div>'
                '<div style="margin-top:.11in">%s</div>'
                '<div class="weburl">%s</div></div></div>'
                % (mark_inline('', 'lockup'), H.escape(NAME), H.escape(TITLE),
                   H.escape(SPANISH_LINE), _contact_lines(), H.escape(WEB)))
    if layout == 'c':
        return ('<div class="card c"><div class="band">%s<div class="bandco">%s</div></div>'
                '<div class="body"><div class="nm">%s</div><div class="ti">%s</div>'
                '<div class="rule" style="margin:.07in 0"></div>%s</div></div>'
                % (img, H.escape(CO), H.escape(NAME), H.escape(TITLE), _contact_lines()))
    return ('<div class="card d">%s<div class="stack">'
            '<div class="co" style="margin-bottom:.05in">%s</div>'
            '<div class="nm">%s</div><div class="ti">%s</div>'
                '<div class="esline">Hablo espa&ntilde;ol</div>'
            '<div class="rule" style="margin:.07in 0"></div>%s</div></div>'
            % (img, H.escape(CO), H.escape(NAME), H.escape(TITLE), _contact_lines()))


def back(lang='en'):
    """The back sells the CALL, not a purchase.

    Rebuilt from bsgflorida.com's own copy. The five paths are the site's five options in its own
    order -- "sell for cash" is ONE of them, not the business. The offer line is verbatim from the
    CIOC door script every other channel already uses, so a homeowner hears the same sentence at
    the door, on the phone, and off this card.

    The denials stay, and the advisory framing makes them true rather than defensive: referring out
    to licensed professionals is what the site actually promises.
    """
    return ('<div class="card back"><div class="safe">'
            '<h4>%s</h4>'
            '<div class="free">%s</div>'
            '<div class="opts">Sell for cash &middot; Refinance &middot; List with a realtor'
            '<br>Home equity &middot; Understand the legal timeline</div>'
            '<div class="not">Not a lender, not a law firm, not a foreclosure-rescue company. '
            'We help you compare options and refer you to licensed professionals. '
            'No fee. Nothing to sign. No obligation to sell.</div>'
            '<div class="reach">%s &nbsp;<span class="reachsm">call or text</span></div>'
            '<div class="mine">%s &middot; %s</div>'
            '</div></div>'
            % (H.escape(OFFER), H.escape(OFFER2), H.escape(PHONE), H.escape(WEB), H.escape(CO)))


def sheet(layouts, logo, review=False):
    body = []
    for L in layouts:
        if review:
            body.append('<div class="lbl">Layout %s &mdash; front</div>' % L.upper())
        body.append(front(L, logo))
        if review:
            body.append('<div class="lbl">Layout %s &mdash; back</div>' % L.upper())
        body.append(back())
    extra = ('' if not review else
             'body{background:#e9edf2;padding:16px}'
             '.card{margin:0 auto 6px;box-shadow:0 2px 10px rgba(11,23,48,.18)}'
             '.lbl{max-width:3.75in;margin:14px auto 4px;font:700 11px Arial;color:#44546b;'
             'letter-spacing:.08em;text-transform:uppercase}'
             '/* trim + safe guides, review only */'
             '.card:after{content:"";position:absolute;inset:.125in;outline:1px dashed rgba(200,40,40,.55);'
             'pointer-events:none}'
             '.card:before{content:"";position:absolute;inset:.25in;outline:1px dashed rgba(40,120,220,.45);'
             'pointer-events:none;z-index:5}')
    return ('<html><head><meta charset="utf-8"><style>%s%s</style></head><body>%s</body></html>'
            % (CSS, extra, '\n'.join(body)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--layout', default='', help='a|b|c|d (default: all four)')
    ap.add_argument('--logo', default='', help='path to the brand mark PNG')
    a = ap.parse_args()

    global _LOGO_PATH
    _LOGO_PATH = a.logo or find_logo()
    if _LOGO_PATH:
        print('logo: %s' % _LOGO_PATH)
    else:
        print('logo: none found — using the drawn vector mark.')
        print('      Save your logo to ~/Downloads as bsg.png (any of bsg*/biscayne*/logo*)')
        print('      and re-run; it will be picked up automatically.')
    logo = logo_uri(a.logo)
    layouts = [a.layout.lower()] if a.layout else ['a', 'b', 'c', 'd']
    today = dt.date.today().isoformat()

    if not EMAIL:
        print('NOTE: no email on the card. miamisolutionsgroup@gmail.com is the only live mailbox')
        print('      and it carries the RETIRED brand — on a Biscayne card that reads as a')
        print('      mismatch at the exact moment the card exists to build trust. A phone-only')
        print('      card is clean; a wrong-brand address is not. Set EMAIL once a BSG mailbox is live.')
    if a.logo and not os.path.exists(a.logo):
        print('WARNING: --logo %s not found, using the embedded mark' % a.logo)

    rp = os.path.join(HERE, 'BSG_Cards_REVIEW_%s.html' % today)
    open(rp, 'w', encoding='utf-8').write(sheet(layouts, logo, review=True))
    print('wrote', rp)

    outs = []
    for L in layouts:
        p = os.path.join(HERE, 'BSG_Card_%s_%s.html' % (L.upper(), today))
        open(p, 'w', encoding='utf-8').write(sheet([L], logo, review=False))
        outs.append(p)
        print('wrote', p)
    # THE DOCSTRING PROMISED PDFs AND main() ONLY EVER WROTE HTML. That matters more than a
    # missing feature: HTML has no embedded fonts, so a printer opening it substitutes Segoe UI ->
    # Arial, and "BISCAYNE SOLUTIONS GROUP" measures 139.6pt in Arial against a 135.4pt column --
    # it wraps, and B's vertical centring breaks. Never hand a printer live HTML.
    # print_background=True is load-bearing: without it the navy backs come out blank.
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page()
            for src in outs:
                pg.goto('file:///' + src.replace(os.sep, '/'))
                pg.wait_for_timeout(700)
                pdf = pg.pdf(width='3.75in', height='2.25in', print_background=True,
                             margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
                dst = os.path.splitext(src)[0] + '.pdf'
                open(dst, 'wb').write(pdf)
                print('wrote %s (%.0f KB)' % (dst, len(pdf) / 1024))
            b.close()
    except Exception as e:
        print('(PDF step failed: %s)' % e)
    print()
    print('SEND THE PDF, NOT THE HTML. Artboard 3.75x2.25in = 3.5x2.0in trim + 0.125in bleed.')
    print('Tell the printer: bleed 0.125in, safe zone 0.125in inside trim, no crop marks included.')
    return rp, outs


if __name__ == '__main__':
    main()
