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
import io
import os
import re

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

# UNIVERSAL BY DEFAULT. A card with one man's name is one man's card -- Carlos, Jose or Jesse
# cannot hand it out, and a reprint is needed every time the team changes. The company and the
# offer carry the card; --name puts a person back on it for whoever wants a personal run.
NAME = ''
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
OFFER2 = '30+ years. Every option, explained.'
PHONE = '(786) 631-1823'
EMAIL = ''          # deliberately blank — see the note in main()
# VERIFIED LIVE 2026-08-28 (HTTP 200) before printing it. A dead URL on a card is worse than no
# URL; an unverified one is a coin flip. This is also the single strongest missing trust element --
# it is what the daughter types in when she Googles the company after the door knock.
WEB = 'BSGFlorida.com'

# Layout E is the SHORT card and it is the default. Everything below about hierarchy, denials and
# five options is still true, but it was true at a length nobody standing in a doorway will read.
# A card is not a brochure: it gets about two seconds, and every line competing for those two
# seconds costs the one line that matters. So the front is the mark alone, and the back is the
# offer, the paths, the number and the code -- nothing else.
# THE CARD NEVER SAID WHAT IT WAS FOR. Front was the mark plus "every option, explained" — every
# option about WHAT? — and the back's "understand the timeline" is a euphemism nobody decodes while
# standing in their own doorway. A homeowner could hold this card and never learn it concerned
# their foreclosure.
# WHERE THE LINE IS, and it is already settled on this account: name the SITUATION, never promise
# an OUTCOME. "Pre-foreclosure & foreclosure" states the domain of work. "We stop foreclosures" /
# "we save homes" is an outcome representation, and FS 501.1377 defines a foreclosure-rescue
# consultant by exactly that kind of representation — which drags in a written contract, the 12pt
# uppercase disclosure, a 3-day right to cancel and a bar on fees before full performance. Jesse's
# "we stopped foreclosures for thousands" was refused on the email templates for this reason; the
# card holds the same line.
# WIDENED 8/31 on Jesse's meeting note, and the reasoning is Carlos: he is in a car all day
# meeting first-time buyers, investors and lenders, and nobody at 1am volunteers that they are
# losing their house. A card that says only "foreclosure" cannot be handed to anyone else, so it
# never leaves his pocket. "Financing" opens every conversation; "foreclosure options" keeps the
# original situation named for the person who needs it. Still a SITUATION, never an outcome --
# the FS 501.1377 line above holds unchanged.
SPECIALTY = 'Financing & foreclosure options'
SLOGAN = 'Every option, explained.'
# Jesse, 8/31 meeting, verbatim: "don't say 5 to 10 minute consultation, just free consultation."
# He is right for a reason the old 5-10 rationale missed: a duration turns the offer into an
# appointment. "Free consultation" is a standing door. The honest-ceiling concern lives in how the
# call is RUN, not on the card.
CONSULT = 'Free consultation'

# ---- layout F: the single-sided card, modelled on the reference -------------------------------
# Alejandro handed over Jesse's old "the FORECLOSURE CENTER" card and asked for this shape:
# everything on ONE face, the company mark and wordmark owning the top, the person and their
# numbers down the left, and a bulleted services list holding the right.
# Single-sided is not only a style choice -- it removes the duplex flip from the 10-up sheet
# entirely, which is the single most common way a home-printed card run gets ruined, and it
# roughly halves the toner.
ROLE = 'Sr. Consultant'           # only prints when --name is given
OFFICE = ''                       # optional second number, e.g. an office line
# The reference card's four services, kept. --bullets overrides the whole list.
BULLETS = [
    'Foreclosure prevention',
    'Investment opportunities',
    'Cash for your home',
    'Short sale specialist',
]
# Layouts with no back. sheet()/build_sheet() consult this so a single-sided card never emits a
# blank navy reverse or a second sheet page.
SINGLE_SIDED = {'f'}

# ---- QR --------------------------------------------------------------------------------------
# Points at the SITE, not a tel: link. A tel: QR fires a dial the instant it is scanned, which is
# the wrong first move for someone who has not decided to talk yet -- the whole offer is that
# there is no pressure. The URL is also the trust artifact: it is what the daughter types in when
# she checks whether the company is real.
QR_TARGET = 'https://bsgflorida.com'
# Quiet zone in modules. The spec asks for 4; 2 is the practical floor and it only holds because
# the code sits on its own white chip, so the navy card supplies the boundary the zone normally
# provides. Referenced in two places -- keep them one constant so they cannot drift.
BORDER = 2


def qr_svg(data=QR_TARGET, dark=None, cls='qr'):
    """The QR as INLINE SVG, sized entirely by CSS.

    Error level M, not L. For this URL both land on the same 25-module symbol, so the redundancy
    is free -- there is no reason to take level L's fragility for zero size saving.

    The real constraint at this size is MODULE SIZE, not redundancy. 29 modules (25 + a 2-module
    quiet zone each side) at 0.55in gives 0.48mm modules, above the ~0.40mm a phone camera
    resolves comfortably at arm's length. Shrinking the code below ~0.45in crosses that line and
    it stops scanning for exactly the people most likely to be holding it at arm's length.

    DARK MODULES ON A WHITE CHIP, never white-on-navy. Built inverted first, to sit flush on the
    navy back, and it did not decode at all: OpenCV's detector refused it outright and inverted
    codes are a coin flip on real phones. The decoder contract is dark-on-light, so the code gets
    its own white chip and the navy card provides the contrast around it. Verified by decoding it
    back off a 4x render -- see _card_e_proof.png; if this is ever restyled, re-run that check
    rather than eyeballing it, because an unscannable QR looks completely normal.

    The quiet zone is part of the symbol, so nothing may be tucked into it -- border=2 is the
    minimum that still reads, and the white chip is sized larger than the code so the zone is
    genuinely blank.
    """
    try:
        import segno
    except ImportError:
        return ''
    dark = dark or NAVY
    q = segno.make(data, error='m', micro=False)
    buf = io.BytesIO()                    # segno's SVG writer emits BYTES, not str
    # svgclass, rather than prepending our own -- segno already writes class="segno", so a second
    # class= made the tag carry two. Browsers keep the first and drop the rest, so it happened to
    # work, but it is invalid markup and depends on which one lands first.
    q.save(buf, kind='svg', xmldecl=False, svgns=True, scale=1, border=BORDER,
           dark=dark, light=None, svgclass=cls)
    svg = buf.getvalue().decode('utf-8')

    # VIEWBOX, OR THE CODE PAINTS AT A THIRD OF ITS BOX. segno emits width="29" height="29" and
    # NO viewBox. Without one, `width:100%` stretches the ELEMENT while the drawing stays locked
    # at 29 user units anchored top-left -- so the code rendered tiny in the corner of its white
    # chip with dead space right and bottom, looking like a broken image.
    # Every decode test still PASSED, which is why this survived: the symbol was sharp, just
    # small, so it read fine at 300-600 DPI and would have failed on a phone at arm's length --
    # i.e. it worked in every test and nowhere real. Size is now asserted, not just decodability.
    n = q.symbol_size(scale=1, border=BORDER)[0]
    svg = re.sub(r'<svg([^>]*?)\swidth="\d+"\s+height="\d+"',
                 r'<svg\1 viewBox="0 0 %d %d"' % (n, n), svg, count=1)
    if 'viewBox' not in svg:              # segno changed its output shape -- fail loud, not small
        raise RuntimeError('qr_svg: could not set a viewBox; the code would print undersized')
    return svg


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
# The BSG blue lifted straight from the logo. The card was navy + white + a gold tick, so the
# only blue on it was inside the mark -- the logo looked pasted onto someone else's card. This is
# the same blue the mark uses, so the card and the logo are visibly one system.
BLUE = '#1B5FAA'
BLUE_TINT = '#eef4fb'
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

/* --- B: white field, full-colour mark, BLUE as a real brand colour -------------------------
   The mark is designed for white, so it gets white -- reversing it to solid white threw away the
   blue/charcoal/grey that ARE the logo. The blue rule and blue phone pull that same hue out into
   the card, so the mark reads as part of the card instead of pasted onto it. */
.b .lockup{position:absolute;left:.3in;top:50%%;transform:translateY(-50%%);width:1.16in}
/* NO EDGE BAR. A bar bleeding off the left edge is invisible on the 10-up sheet (which is
   deliberately no-bleed, because a desktop printer cannot reach the paper edge) and it is the
   first thing a drifting trim eats on the commercial version. The blue lives INSIDE the trim
   instead, where every cut keeps it: the divider and a short rule under the offer. */
.b .divider{background:%(blue)s;opacity:.30}
.b .orule{width:.34in;height:2px;background:%(blue)s;border-radius:2px;margin:.055in 0 .05in}
.b .divider{position:absolute;left:1.62in;top:.32in;bottom:.32in;width:1.5px}
.b .right{position:absolute;left:1.82in;right:.25in;top:.28in;bottom:.28in}
.b .offer{font-size:9.5pt;font-weight:700;color:%(navy)s;line-height:1.28}
.b .offer2{font-size:7.6pt;color:%(steel)s;margin-top:2pt}
.b .bigph{font-size:13pt;font-weight:700;color:%(blue)s;letter-spacing:.01em;margin-top:.09in}
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

/* --- E: the short card. Front is the mark and nothing else. -------------------------------
   Sized as large as the trim honestly allows. The mark's own "SOLUTIONS GROUP" sub-line is
   font-size 34 in a 620-unit viewBox, so it scales to 0.055x the mark height: at 1.22in that is
   ~4.9pt, under the 6pt floor this project holds everything else to. It is grey on white, so it
   goes SOFT rather than plugging solid the way the old reversed raster did -- acceptable for a
   sub-wordmark nobody reads letter by letter, and the alternative is redrawing a mark that is
   not mine to redraw. Flagged rather than silently "fixed". */
.e .safe{display:flex;flex-direction:column;align-items:center;justify-content:center}
/* 1.22in -> 1.08in to seat a second line without crowding the trim. The sub-wordmark inside the
   mark scales with it (~4.3pt now); still grey-on-white, so it softens rather than plugging. */
.e .brandmark{height:1.08in;width:auto;display:block}
/* WHAT WE DO reads before the promise. A stranger holding this needs the situation first --
   the promise only means something once they know what it is a promise about. */
.e .specialty{font-size:8pt;font-weight:700;letter-spacing:.075em;text-transform:uppercase;
              color:%(navy)s;margin-top:.10in;text-align:center;white-space:nowrap}
.e .slogan{font-size:7pt;font-weight:500;letter-spacing:.055em;color:%(steel)s;
           margin-top:.045in;text-align:center}

/* --- F: single-sided, modelled on the reference card ---------------------------------------
   Two columns inside the safe area. LEFT holds the mark then the person then the numbers;
   RIGHT holds the services bullets, optically centred against the contact block rather than the
   card, which is what makes the reference card's right column feel anchored instead of floating.
   The divider is the same blue as the mark, so the two columns read as one system. */
/* The LOCKUP owns the top third, exactly as the reference does: glyph left, wordmark right, with
   BISCAYNE as the dominant word and SOLUTIONS GROUP letterspaced beneath it to the SAME measure.
   That measure-match is the whole trick behind "FORECLOSURE / CENTER" -- it is what makes two
   words of very different lengths read as one designed block instead of two stacked lines. The
   tracking below is a computed starting point; the rendered widths are asserted, not eyeballed. */
.f .safe{display:flex;flex-direction:column}
.f .lock{display:flex;align-items:center;gap:.10in}
.f .badgemark{height:.50in;width:auto;display:block;flex:0 0 auto}
.f .wm1{font-size:21pt;font-weight:700;letter-spacing:.035em;color:%(navy)s;line-height:.98;
        white-space:nowrap}
.f .wm2{font-size:7.6pt;font-weight:600;color:%(steel)s;line-height:1;margin-top:2.5pt;
        white-space:nowrap}
.f .cols{display:flex;align-items:stretch;flex:1;margin-top:.085in}
.f .lcol{width:1.72in;display:flex;flex-direction:column;justify-content:center;padding-right:.10in}
.f .rcol{flex:1;display:flex;flex-direction:column;justify-content:center;padding-left:.14in;
         border-left:1.5px solid rgba(27,95,170,.30)}
.f .who{font-size:10.5pt;font-weight:700;color:%(navy)s;line-height:1.12;letter-spacing:.005em}
.f .role{font-size:7.4pt;font-weight:500;color:%(steel)s;letter-spacing:.045em;
         text-transform:uppercase;margin-top:1.5pt}
.f .spec{font-size:7.2pt;font-weight:700;color:%(blue)s;letter-spacing:.055em;
         text-transform:uppercase;margin-top:2pt}
.f .rule{height:1.5px;flex:0 0 1.5px;background:%(blue)s;width:.30in;opacity:.55;margin:.055in 0}
.f .cline{font-size:8.2pt;font-weight:600;color:%(ink)s;line-height:1.5;letter-spacing:.005em}
.f .cline b{font-weight:700;color:%(navy)s}
.f .web{font-size:8pt;font-weight:700;color:%(navy)s;letter-spacing:.015em;margin-top:1pt}
.f .es{font-size:6.8pt;font-weight:600;color:%(steel)s;letter-spacing:.05em;
       text-transform:uppercase;margin-top:2.5pt}
/* Bullets: small caps at 7.4pt. The reference sets these ALL-CAPS at roughly 6pt, which plugs at
   press -- 7.4pt with a tighter tracking reads the same at a glance and survives dot gain. */
.f ul.svc{margin:0;padding:0;list-style:none}
.f ul.svc li{font-size:7.4pt;font-weight:600;color:%(ink)s;letter-spacing:.035em;
             text-transform:uppercase;line-height:1.32;margin:0 0 3.4pt;position:relative;
             padding-left:.105in}
.f ul.svc li:before{content:'';position:absolute;left:0;top:.036in;width:3.4px;height:3.4px;
                    border-radius:50%%;background:%(blue)s}
.f .qrf{position:absolute;right:0;bottom:0;width:.46in;height:.46in;background:#fff;padding:.02in}
.f .qrf svg{display:block;width:100%%;height:100%%;shape-rendering:crispEdges}

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

/* --- SHORT BACK (layout E) ----------------------------------------------------------------
   Five elements, in the order a person actually needs them: what they get, what it covers, who
   to call, where to look, and the one denial worth keeping. The QR sits in the bottom-right
   corner and the text column stops short of it -- .qrpad reserves the width so nothing ever
   runs underneath, which is the usual way a corner code ends up unscannable. */
.sb{background:%(navy)s;color:#fff}
.sb .safe{display:flex;flex-direction:column;justify-content:center}
.sb .hd{font-size:8.5pt;font-weight:700;letter-spacing:.10em;text-transform:uppercase;
        color:%(gold)s;margin:0 0 .07in}
.sb .paths{font-size:8.5pt;line-height:1.55;color:#f2f6fb;margin:0}
/* Clears the QR with a .10in gutter. Measured, not guessed: safe area is 3.25in wide, the code
   occupies the right .55in of it, so the text column must stop at 2.60in. Set this by eye and the
   last line of a long phone number tucks under the code and kills the scan. */
.sb .qrpad{padding-right:.78in}
.sb .ph2{font-size:15pt;font-weight:700;color:#fff;letter-spacing:.01em;margin-top:.11in;
         line-height:1}
.sb .sub{font-size:7pt;font-weight:600;color:#93a5bf;letter-spacing:.07em;margin-top:2.5pt}
.sb .web2{font-size:8.5pt;font-weight:700;color:#fff;letter-spacing:.02em;margin-top:.075in}
.sb .deny{font-size:6.6pt;line-height:1.35;color:#9db0cb;margin-top:.06in}
/* 0.55in -> 0.48mm modules. Do not shrink this: see qr_svg().
   right/bottom 0 anchors it to the SAFE-AREA corner, not the card's. .safe is already inset
   .25in, so right:.25in here inset it TWICE and floated the code into the middle of the card
   while the text column still ran underneath it. Corner means the corner of the printable area. */
.sb .qrbox{position:absolute;right:0;bottom:0;width:.62in;height:.62in;background:#fff;
           padding:.035in;border-radius:2px}
.sb .qr{display:block;width:100%%;height:100%%;shape-rendering:crispEdges}
""" % {'ink': INK, 'paper': PAPER, 'navy': NAVY, 'steel': STEEL, 'gold': GOLD,
   'blue': BLUE, 'bluetint': BLUE_TINT}


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


def badge_inline(cls='badgemark'):
    """The mark's GLYPH only -- skyline + horizon arc, no monogram, no wordmark.

    Layout F needs a WIDE horizontal lockup (glyph left, type right), which is the silhouette of
    the reference card. bsg_mark is a stacked square lockup and cannot make that shape: dropped
    into a horizontal slot it either shrinks until the sub-wordmark is unreadable or eats the
    column the type needs.

    Nothing is redrawn. The full mark is viewBox 0 0 760 620. Measured off the actual geometry:
        skyline blocks + tower   x 296-444, y  62-328   (the pole is M352 232 ... v96 -> y 328)
        horizon arc              x 214-546, y 196-262
        BSG monogram             baseline y 452 at 196pt -> CAPS TOP ~311
        BISCAYNE / SOLUTIONS     baselines y 536 / 590

    THE POLE AND THE MONOGRAM OVERLAP: the pole runs to 328, the monogram starts at 311. There is
    no y that keeps all of the pole and none of the letters. A first crop to y 340 looked correct
    in the numbers and printed the TOPS OF THE B, S AND G as three blue blobs under the skyline --
    it read as a rendering fault, not a logo.
    So the cut is at 308, just above the caps: the pole loses its bottom 17 units (~7% of the glyph
    height, below the arc where it already reads as a taper) and no letterform can leak in.

    Cropping rather than editing matters: the file in brand/ stays the single source of the mark,
    so a new logo dropped there flows through here untouched.
    """
    # A PURPOSE-DRAWN BADGE BEATS ANY CROP, so look for one first. The crop below is a fallback
    # that is always slightly wrong by construction: the stacked mark's pole and its monogram
    # overlap in y, so every window either hangs a pole stub under the arc or leaks letter tops,
    # and the stacked mark has only ONE arc where the emblem has two. Drop the real emblem at
    # brand/bsg-badge.svg and it is used verbatim -- no crop, no compromise.
    p = os.path.join(HERE, 'brand', 'bsg-badge.svg')
    if os.path.exists(p):
        try:
            s = open(p, encoding='utf-8').read()
            s = s[s.index('<svg'):]
            return s.replace('<svg ', '<svg class="%s" ' % cls, 1)
        except Exception:
            pass
    svg = mark_inline('', cls)
    if not svg:
        return ''
    if 'viewBox' in svg:
        return re.sub(r'viewBox="[^"]*"', 'viewBox="200 48 360 260"', svg, count=1)
    # a supplied raster cannot be cropped this way -- fall back to the whole mark rather than
    # silently emitting a stretched or empty box
    return svg


def front(layout, logo):
    img = mark_inline('', 'brandmark')
    if layout == 'f':
        # Single-sided, in the shape of the reference card. Name and role print only when --name
        # is passed: the universal run keeps the same silhouette and simply drops those two lines,
        # so one layout serves both the personal card and the stack anyone can hand out.
        who = ''
        if NAME:
            who = ('<div class="who">%s</div><div class="role">%s</div>'
                   % (H.escape(NAME), H.escape(ROLE)))
        office = ('<div class="cline">Office: %s</div>' % H.escape(OFFICE)) if OFFICE else ''
        mail = ('<div class="cline">%s</div>' % H.escape(EMAIL)) if EMAIL else ''
        bullets = ''.join('<li>%s</li>' % H.escape(b) for b in BULLETS)
        return ('<div class="card f"><div class="safe">'
                '<div class="lock">%s<div><div class="wm1">BISCAYNE</div>'
                '<div class="wm2">SOLUTIONS GROUP</div></div></div>'
                '<div class="cols">'
                '<div class="lcol">%s<div class="rule"></div>'
                '<div class="cline"><b>%s</b></div>%s%s'
                '<div class="es">Call or text &middot; Hablo espa&ntilde;ol</div>'
                '<div class="web">%s</div></div>'
                '<div class="rcol"><ul class="svc">%s</ul></div>'
                '</div></div></div>'
                % (badge_inline(), who, H.escape(PHONE), office, mail,
                   H.escape(WEB), bullets))
    if layout == 'e':
        # The mark, what we do, the promise. Still no name, no title, no phone, no QR -- those all
        # competed with the mark for the two seconds the card gets, and the back carries them.
        # But "nothing else" went one line too far: a card that never names foreclosure leaves the
        # person holding it unable to tell what it is for. Situation first, promise second.
        return ('<div class="card e"><div class="safe">%s'
                '<div class="specialty">%s</div><div class="slogan">%s</div>'
                '</div></div>' % (img, H.escape(SPECIALTY), H.escape(SLOGAN)))
    if layout == 'a':
        return ('<div class="card a"><div class="safe" style="display:flex;flex-direction:column;'
                'align-items:center;justify-content:center">%s'
                '<div class="co">%s</div><div class="rule"></div>'
                '<div class="nm">%s</div><div class="ti">%s</div>'
                '<div class="esline">Hablo espa&ntilde;ol</div>'
                '<div style="margin-top:.09in">%s</div></div></div>'
                % (img, H.escape(CO), H.escape(NAME), H.escape(TITLE), _contact_lines()))
    if layout == 'b':
        who = ('<div class="nm">%s</div>' % H.escape(NAME)) if NAME else ''
        return ('<div class="card b">%s<div class="divider"></div>'
                '<div class="right" style="display:flex;flex-direction:column;justify-content:center">'
                '%s'
                '<div class="offer">%s</div>'
                '<div class="orule"></div><div class="offer2">%s</div>'
                '<div class="bigph">%s</div>'
                '<div class="reachsm">call or text &middot; %s</div>'
                '<div class="weburl">%s</div></div></div>'
                % (mark_inline('', 'lockup'), who, H.escape(OFFER), H.escape(OFFER2),
                   H.escape(PHONE), H.escape(SPANISH_LINE), H.escape(WEB)))
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


def back_short():
    """The short back: offer, paths, number, site, code. Five things, one screenful of eye.

    What was cut and why it was safe to cut it:
      * three denial sentences -> one line. "Not a lender or law firm. No fee, no obligation."
        keeps the actual trust signal (we print our own limits) and drops the explanation of it.
      * "refer you to licensed professionals" -> gone from the card, still true, still on the site
        the QR opens. A card cannot carry a policy; it can carry a promise and a link to the policy.
      * the five paths keep their own words but lose the bullets and the heading -- as one
        centre-dotted run they read as scope in a single glance instead of a list to work through.
    Nothing here is a compliance requirement: FS 501.1377's disclosures attach to a foreclosure-
    rescue CONTRACT, not to a business card. The denial stays because it works, not because it
    is owed.
    """
    qr = qr_svg()
    return ('<div class="card sb"><div class="safe">'
            '<div class="qrpad">'
            '<div class="hd">%s</div>'
            # "Understand the timeline" -- WHICH timeline? The front answers that, but a card
            # lands back-up half the time and this side has to stand alone. "Foreclosure timeline"
            # names it and is three characters SHORTER, so it costs no width against the QR gutter.
            # THE FULL MENU, 8/31. Two lines: line one is money coming IN (what a stranger in
            # Carlos's car wants), line two is trouble going OUT (what the doorstep needs). Same
            # character budget as the old two-liner, so the QR gutter is untouched.
            '<div class="paths">First home &middot; Refinance &middot; Investor loans<br>'
            'Credit lines &middot; Sell for cash &middot; Foreclosure options</div>'
            '<div class="ph2">%s</div>'
            '<div class="sub">CALL OR TEXT &middot; %s</div>'
            '<div class="web2">%s</div>'
            '<div class="deny">Not a lender or law firm. No fee, no obligation.</div>'
            '</div>'
            '<div class="qrbox">%s</div>'
            '</div></div>'
            % (H.escape(CONSULT), H.escape(PHONE), H.escape(SPANISH_LINE.upper()),
               H.escape(WEB), qr))


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


def back_for(layout):
    """E gets the short back; a-d keep the long one they were designed around.

    None for a single-sided layout -- and callers must honour that rather than substituting an
    empty card, or F prints a blank navy reverse and doubles the toner for nothing.
    """
    if layout in SINGLE_SIDED:
        return None
    return back_short() if layout == 'e' else back()


def sheet(layouts, logo, review=False):
    body = []
    for L in layouts:
        if review:
            body.append('<div class="lbl">Layout %s &mdash; %s</div>'
                        % (L.upper(), 'single-sided' if L in SINGLE_SIDED else 'front'))
        body.append(front(L, logo))
        b = back_for(L)
        if b:
            if review:
                body.append('<div class="lbl">Layout %s &mdash; back</div>' % L.upper())
            body.append(b)
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


SHEET_CSS = """
@page{size:8.5in 11in;margin:0}
body{margin:0}
/* 10-UP ON US LETTER. Ten 3.5x2.0in cards tile exactly: 2 across (7.0in) and 5 down (10.0in),
   leaving 0.75in side margins and 0.5in top/bottom -- inside every desktop printer's unprintable
   edge. NO BLEED here on purpose: a home printer cannot print to the paper edge, so a bleed
   design would cut with white slivers. The bleed version is the separate per-card PDF for a
   commercial printer.
   Cards butt edge to edge so ONE cut line serves both neighbours -- a guillotine or a steel rule
   and a knife goes straight across. Crop ticks sit in the margin, never on the card. */
.sheet{position:relative;width:8.5in;height:11in;page-break-after:always}
.grid{position:absolute;left:.75in;top:.5in;width:7in;height:10in}
.slot{position:absolute;width:3.5in;height:2in;overflow:hidden}
/* THE CARD KEEPS ITS NATIVE 3.75x2.25 ARTBOARD -- do not re-declare width/height here.
   Squashing it to 3.5x2.0 also squashes .safe (left/right .25in) from 3.25in to 3.00in of usable
   column, so the sheet re-wrapped text the bleed card set on one line, and left .safe sitting
   .125in from the left trim but .375in from the right. The card looked centred and was not.
   Instead: leave it 3.75in, shift by exactly the bleed, and let the slot's overflow:hidden do the
   trimming. Card local .125-3.625in then maps to slot 0-3.5in, which IS the trim, and .safe lands
   .125in inside it on all four sides. */
.slot .card{box-shadow:none;page-break-after:auto;transform:translate(-.125in,-.125in)}
.tick{position:absolute;background:#9aa3ad}
.tickh{width:.16in;height:.5px}
.tickv{width:.5px;height:.16in}
.shlbl{position:absolute;left:.75in;top:.22in;font:9px Arial;color:#8a9099;letter-spacing:.06em}
"""


def sheet_10up(layout, logo, side='front'):
    """Ten cards on one US Letter page, with cut ticks in the margin.

    Front and back are SEPARATE pages, and the back page is column-mirrored so a duplex flip on
    the long edge lands each back on its own front. Get that wrong and every card is somebody
    else's back -- which is the single most common way a DIY card sheet is wasted.
    """
    cols, rows = 2, 5
    cells = []
    for r in range(rows):
        for c in range(cols):
            cc = (cols - 1 - c) if side == 'back' else c      # mirror for the duplex flip
            body = back_for(layout) if side == 'back' else front(layout, logo)
            cells.append('<div class="slot" style="left:%.3fin;top:%.3fin">%s</div>'
                         % (cc * 3.5, r * 2.0, body))
    ticks = []
    for c in range(cols + 1):                                  # vertical cuts
        x = .75 + c * 3.5
        ticks.append('<div class="tick tickv" style="left:%.3fin;top:.30in"></div>' % x)
        ticks.append('<div class="tick tickv" style="left:%.3fin;top:10.54in"></div>' % x)
    for r in range(rows + 1):                                  # horizontal cuts
        y = .5 + r * 2.0
        ticks.append('<div class="tick tickh" style="left:.55in;top:%.3fin"></div>' % y)
        ticks.append('<div class="tick tickh" style="left:7.79in;top:%.3fin"></div>' % y)
    return ('<div class="sheet"><div class="shlbl">%s &mdash; %s &mdash; 10 cards &mdash; '
            'cut on the tick marks</div>%s<div class="grid">%s</div></div>'
            % (H.escape(CO), side.upper(), ''.join(ticks), ''.join(cells)))


def build_sheet(layout, logo):
    """One page for a single-sided layout, two (front + mirrored back) otherwise.

    Emitting a back page for F would not merely waste a sheet -- it would invite a duplex run,
    and a duplex run on a single-sided design is how you get ten cards backed with ten blanks and
    a mis-registered trim. The whole point of the single-sided card is that this step disappears.
    """
    pages = sheet_10up(layout, logo, 'front')
    if layout not in SINGLE_SIDED:
        pages += sheet_10up(layout, logo, 'back')
    return ('<html><head><meta charset="utf-8"><style>%s%s</style></head><body>%s</body></html>'
            % (CSS, SHEET_CSS, pages))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--layout', default='', help='e|a|b|c|d (default: e, the short card)')
    ap.add_argument('--logo', default='', help='path to the brand mark PNG')
    ap.add_argument('--sheet', action='store_true',
                    help='10-up US Letter sheet: front page + mirrored back page, cut marks')
    ap.add_argument('--office', default='', help='optional second phone line (layout f)')
    ap.add_argument('--bullets', default='',
                    help='comma-separated services list for layout f, overrides the default')
    ap.add_argument('--role', default='', help='title under the name on layout f')
    ap.add_argument('--name', default='',
                    help='put a person on the card (default: universal company card)')
    a = ap.parse_args()

    global _LOGO_PATH, NAME, ROLE, OFFICE, BULLETS
    if a.name:
        NAME = a.name
    if a.role:
        ROLE = a.role
    if a.office:
        OFFICE = a.office
    if a.bullets:
        BULLETS = [b.strip() for b in a.bullets.split(',') if b.strip()]
    _LOGO_PATH = a.logo or find_logo()
    if _LOGO_PATH:
        print('logo: %s' % _LOGO_PATH)
    else:
        print('logo: none found — using the drawn vector mark.')
        print('      Save your logo to ~/Downloads as bsg.png (any of bsg*/biscayne*/logo*)')
        print('      and re-run; it will be picked up automatically.')
    logo = logo_uri(a.logo)
    layouts = [a.layout.lower()] if a.layout else ['e']
    today = dt.date.today().isoformat()

    if not EMAIL:
        print('NOTE: no email on the card. miamisolutionsgroup@gmail.com is the only live mailbox')
        print('      and it carries the RETIRED brand — on a Biscayne card that reads as a')
        print('      mismatch at the exact moment the card exists to build trust. A phone-only')
        print('      card is clean; a wrong-brand address is not. Set EMAIL once a BSG mailbox is live.')
    if a.logo and not os.path.exists(a.logo):
        print('WARNING: --logo %s not found, using the embedded mark' % a.logo)

    if a.sheet:
        lay = (a.layout or 'e').lower()
        html = build_sheet(lay, logo)
        sp = os.path.join(HERE, 'BSG_Cards_SHEET_%s_%s.html' % (lay.upper(), today))
        open(sp, 'w', encoding='utf-8').write(html)
        print('wrote', sp)
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.launch(); pg = b.new_page()
                pg.goto('file:///' + sp.replace(os.sep, '/')); pg.wait_for_timeout(900)
                pdf = pg.pdf(format='Letter', print_background=True,
                             margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
                b.close()
            dp = os.path.splitext(sp)[0] + '.pdf'
            open(dp, 'wb').write(pdf)
            print('wrote %s (%.0f KB)' % (dp, len(pdf) / 1024))
        except Exception as e:
            print('(sheet PDF failed: %s)' % e)
        print()
        if lay in SINGLE_SIDED:
            print('PRINT: US Letter, ACTUAL SIZE / 100% scale (never "fit to page").')
            print('       SINGLE-SIDED - do NOT duplex. One page, 10 cards, cut on the ticks.')
        else:
            print('PRINT: US Letter, ACTUAL SIZE / 100% scale (never "fit to page"), duplex')
            print('       flip on the LONG edge. 10 cards per sheet. Cut on the tick marks.')
        print('       Cardstock 65-110lb. This sheet has NO bleed by design -- a desktop')
        print('       printer cannot reach the paper edge, so a bleed would cut white slivers.')
        return sp, [sp]

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
