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
TITLE = 'I buy houses in Miami-Dade'
TITLE_ES = 'Compro casas en Miami-Dade'
SPANISH_LINE = 'Hablo espanol'
PHONE = '(786) 631-1823'
EMAIL = ''          # deliberately blank — see the note in main()
WEB = ''            # bsgflorida.com is not live yet; a dead URL on a card is worse than none

# ---- brand ----------------------------------------------------------------------------------
NAVY = '#0B1730'
NAVY_SOFT = '#16294d'
STEEL = '#5b6b82'
GOLD = '#C9A227'
INK = '#1a2233'
PAPER = '#ffffff'


def logo_uri(path=''):
    """Data URI for the brand mark. Falls back to the mark already embedded in bsg_flyer."""
    if path and os.path.exists(path):
        ext = os.path.splitext(path)[1].lstrip('.').lower() or 'png'
        return 'data:image/%s;base64,%s' % (
            'jpeg' if ext in ('jpg', 'jpeg') else ext,
            base64.b64encode(open(path, 'rb').read()).decode('ascii'))
    try:
        import bsg_flyer
        return bsg_flyer.BSG_LOGO_B64
    except Exception:
        return ''


CSS = """
@page{size:3.75in 2.25in;margin:0}
*{box-sizing:border-box}
body{margin:0;font-family:'Segoe UI',-apple-system,Helvetica,Arial,sans-serif;color:%(ink)s}
/* The artboard IS the bleed area. Trim happens 0.125in inside it on every edge, so anything
   that must survive the cut lives inside .safe. */
.card{position:relative;width:3.75in;height:2.25in;overflow:hidden;background:%(paper)s;
      page-break-after:always}
.safe{position:absolute;left:.25in;top:.25in;right:.25in;bottom:.25in}
.bleedbg{position:absolute;inset:0}

/* --- shared type ------------------------------------------------------------------------- */
.nm{font-size:13.5pt;font-weight:700;letter-spacing:.01em;line-height:1.1;margin:0}
.ti{font-size:8pt;font-weight:600;letter-spacing:.02em;
    color:%(steel)s;margin:2pt 0 0}
.co{font-size:8.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:%(navy)s}
.ph{font-size:11pt;font-weight:700;letter-spacing:.01em;color:%(navy)s}
.sm{font-size:7pt;color:%(steel)s;letter-spacing:.02em}
.reachsm{font-size:6.5pt;font-weight:600;color:%(steel)s;letter-spacing:.05em}
.esline{font-size:7.5pt;font-weight:600;color:%(steel)s;margin-top:1pt}
.rule{height:2px;background:%(gold)s;width:34px;border-radius:2px}

/* --- A: centered classic ------------------------------------------------------------------ */
.a{text-align:center}
.a img{height:.72in;display:block;margin:0 auto .07in}
.a .nm{margin-top:.04in}
.a .rule{margin:.07in auto}

/* --- B: navy left panel ------------------------------------------------------------------- */
.b .panel{position:absolute;left:0;top:0;bottom:0;width:1.42in;background:%(navy)s}
/* The mark is a full-colour metallic asset. brightness(0) invert(1) turned it into an unreadable
   white blob -- verified in the first review render. A logo that cannot reverse gets a white chip
   to sit on; that is a normal print solution, not a compromise. */
.b .panel .chip{position:absolute;left:50%%;top:50%%;transform:translate(-50%%,-50%%);
                width:1.02in;height:1.02in;background:#fff;border-radius:.1in;
                display:flex;align-items:center;justify-content:center}
.b .panel .chip img{width:.82in;display:block}
.b .right{position:absolute;left:1.62in;right:.25in;top:.28in;bottom:.28in}

/* --- C: navy band across the top ---------------------------------------------------------- */
.c .band{position:absolute;left:0;right:0;top:0;height:.95in;background:#fff;
          border-bottom:3px solid %(navy)s}
.c .band img{position:absolute;left:.28in;top:50%%;transform:translateY(-50%%);height:.6in}
.c .bandco{position:absolute;right:.28in;top:50%%;transform:translateY(-50%%);color:%(navy)s;
           font-size:8pt;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
           text-align:right;max-width:1.5in;line-height:1.3}
.c .body{position:absolute;left:.28in;right:.28in;top:1.12in}

/* --- D: type-led, minimal ----------------------------------------------------------------- */
.d img{position:absolute;right:.25in;top:.25in;height:.5in;opacity:.95}
.d .stack{position:absolute;left:.25in;bottom:.25in;right:1in}
.d .nm{font-size:15pt}

/* --- BACK --------------------------------------------------------------------------------- */
.back{background:%(navy)s;color:#fff}
.back .safe{display:flex;flex-direction:column;justify-content:center}
.back h4{margin:0 0 .06in;font-size:7pt;letter-spacing:.16em;text-transform:uppercase;
         color:%(gold)s;font-weight:700}
.back ul{margin:0;padding-left:.14in;list-style:none}
.back li{font-size:8.5pt;line-height:1.5;margin:0 0 1pt;position:relative;padding-left:.11in}
.back li:before{content:'';position:absolute;left:0;top:.055in;width:4px;height:4px;
                border-radius:50%%;background:%(gold)s}
/* Same size as the body, not 6.6pt grey. Fine print reads as "someone made him print this";
   body text reads as "I want you to read this". The content was never the problem. */
.back .doline{font-size:8.5pt;line-height:1.45;margin:0 0 .05in}
.back .free{font-size:8pt;line-height:1.4;color:#dbe4f0;margin:0 0 .05in}
.back .not{font-size:7.6pt;line-height:1.4;color:#c3cfe0;margin:0 0 .05in}
.back .mine{font-size:7.6pt;line-height:1.4;color:#dbe4f0}
.back .cta{margin-top:.07in;font-size:11pt;font-weight:700;color:#fff;letter-spacing:.01em}
.back .reach{font-size:7pt;font-weight:600;color:#8fa1bb;letter-spacing:.06em}
""" % {'ink': INK, 'paper': PAPER, 'navy': NAVY, 'steel': STEEL, 'gold': GOLD}


def _contact_lines():
    # "call or text" is not decoration: an ashamed person texts before they call, and nothing
    # on the card otherwise grants permission to.
    out = ['<div class="ph">%s <span class="reachsm">call or text</span></div>' % H.escape(PHONE)]
    if EMAIL:
        out.append('<div class="sm" style="margin-top:2pt">%s</div>' % H.escape(EMAIL))
    if WEB:
        out.append('<div class="sm">%s</div>' % H.escape(WEB))
    return ''.join(out)


def front(layout, logo):
    img = '<img src="%s" alt="">' % logo if logo else ''
    if layout == 'a':
        return ('<div class="card a"><div class="safe" style="display:flex;flex-direction:column;'
                'align-items:center;justify-content:center">%s'
                '<div class="co">%s</div><div class="rule"></div>'
                '<div class="nm">%s</div><div class="ti">%s</div>'
                '<div class="esline">Hablo espa&ntilde;ol</div>'
                '<div style="margin-top:.09in">%s</div></div></div>'
                % (img, H.escape(CO), H.escape(NAME), H.escape(TITLE), _contact_lines()))
    if layout == 'b':
        return ('<div class="card b"><div class="panel"><div class="chip">%s</div></div>'
                '<div class="right" style="display:flex;flex-direction:column;justify-content:center">'
                '<div class="co">%s</div><div class="rule" style="margin:.06in 0"></div>'
                '<div class="nm">%s</div><div class="ti">%s</div>'
                '<div class="esline">Hablo espa&ntilde;ol</div>'
                '<div style="margin-top:.1in">%s</div></div></div>'
                % (img, H.escape(CO), H.escape(NAME), H.escape(TITLE), _contact_lines()))
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
    """The back is the trust side. What I do, then my limits, in first person.

    THE DENIALS ARE SOURCED, NOT TYPED. The first version of this function hardcoded its own
    denial string — the exact drift disclaimer.py exists to stop, committed on the one artifact a
    homeowner keeps, photographs and hands to a lawyer. sig_tag() is 87 characters, fits, and
    restores the two denials the hand-typed line had silently dropped: the foreclosure-rescue
    denial (which appears on EVERY other surface) and the HUD-counselor denial.

    THE SELL-SIDE BULLET IS GONE. "Or help you sell it before the sale date" was one sentence
    carrying three separate exposures: it framed the service against the foreclosure sale date
    (FS 501.1377), it straddled the MARS short-sale line (a plain third-party sale is carved out,
    a short sale is expressly IN), and helping another person sell their property for compensation
    is unlicensed brokerage under FS 475. Deleting it leaves a pure principal-purchase card, which
    is both the compliant version and the stronger pitch. Buying for your own account is the
    express FS 475 exemption — so saying plainly that I BUY is protective, not just clearer.

    "We charge no fees" is gone too. disclaimer.py sources that claim narrowly, as
    "Consultations are always free." The card had escalated it into a categorical claim about the
    whole relationship — an unauthorized expansion, and the sentence most likely to be literally
    false the moment any assignment fee exists.
    """
    es = (lang == 'es')
    do = ('Puedo comprar su casa como está y cerrar en la fecha que usted elija.'
          if es else "I can buy your house as-is and close on the date you pick.")
    free = ('La consulta siempre es gratis. No hay nada que firmar para hablar. '
            'Sin obligación de vender.' if es else
            'Consultations are always free. Nothing to sign to talk. No obligation to sell.')
    mine = ('Este número es mi celular. Habla conmigo, no con un centro de llamadas.' if es else
            'This number rings my cell. You get me, not a call center.')
    head = 'LO QUE HAGO' if es else 'WHAT I DO'
    reach = 'llame o escriba' if es else 'call or text'
    try:
        tag = D.sig_tag('es' if es else 'en', as_html=False)
    except Exception:
        tag = ''
    return ('<div class="card back"><div class="safe">'
            '<h4>%s</h4>'
            '<div class="doline">%s</div>'
            '<div class="free">%s</div>'
            '<div class="not">%s</div>'
            '<div class="mine">%s</div>'
            '<div class="cta">%s &nbsp;<span class="reach">%s</span></div>'
            '<div class="sm" style="color:#8fa1bb;margin-top:1pt">%s &middot; %s</div>'
            '</div></div>'
            % (H.escape(head), H.escape(do), H.escape(free), H.escape(tag), H.escape(mine),
               H.escape(PHONE), H.escape(reach), H.escape(NAME), H.escape(CO)))


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
    return rp, outs


if __name__ == '__main__':
    main()
