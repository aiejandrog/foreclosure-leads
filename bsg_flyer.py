#!/usr/bin/env python
"""bsg_flyer — the leave-behind. Jesse's replacement for the business card (2026-08-12 call).

THE DOCTRINE, VERBATIM FROM THE CALL
Business cards go in a drawer and die. The replacement is a QUARTER of a letter page — cut a
sheet twice and you get 4 postcards — on a color so loud "they'll remember where they put it and
find it easy in a drawer... like a turd in a punch bowl." Loud paper, CLEAN layout: name and
phone huge, 24-hour line, the "clients who thought it was handled" hook, a blank line for the
sale date (owner's hand only), Spanish on the reverse. Jesse's standing order: no more home
visits until this exists.

WHAT THIS RENDERS
Page 1: 4-up ENGLISH cards on one letter sheet.   Page 2: 4-up SPANISH cards, mirrored layout.
Print duplex (flip on LONG edge), cut twice -> 4 bilingual postcards per sheet. Faint cut guides.

IDENTITY comes from sender.json (same contract as the letter system — the name MUST be a person;
see bsg_brand.py's sender notes). Override per-batch with --name/--phone for Carlos's paper:
    python bsg_flyer.py --name "Carlos Gonzalez" --phone "(305) 555-0123"

COMPLIANCE
 * "30+ years" is attributed to the SENIOR ADVISOR, never to the company — MSG is new, and a
   company-age claim on paper is FDUTPA bait.
 * The sale-date blank is filled ONLY when handing to the titled owner — never a third party
   (third-party foreclosure disclosure is the lawsuit; the playbook's card 8 owns this rule).
 * REMOVED 2026-08-23 AT ALEJANDRO'S DIRECTION, KNOWINGLY: the MARS/Reg O 12 CFR 1015.4(a)
   microprint block and the FS 501.1377 "not a foreclosure-rescue company" line. This is the one
   surface disclaimer.py's own docstring names as required ("Goes on anything printed and handed
   or mailed to a homeowner: letters, flyers, door hangers") and it is why this file does not
   `import disclaimer` — do not re-add it without asking first, the omission is deliberate, not a
   bug. No attorney has signed off on this removal.

Run:  python bsg_flyer.py                        # safety-yellow, sender.json identity
      python bsg_flyer.py --color pink           # hot pink stock look
      python bsg_flyer.py --name X --phone Y     # someone else's paper
"""
import argparse
import datetime
import html as H
import json
import os

import entity          # _display_llc() delegates to it; removed by accident in 35bc4d1 alongside
                       # `import disclaimer`, which broke every flyer run with a NameError. The
                       # disclaimer import stays OUT (see the note above) — this one is the gate
                       # that decides whether the " LLC" suffix may be printed at all.
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))

COLORS = {  # loud enough to find in a drawer, light enough that black text stays clean
    'yellow': '#FFE818',
    'pink':   '#FF5FA2',
    'green':  '#7CFC5A',
    'orange': '#FFA020',
}


# TRUTH GATE (same rule as msg_letter._company + msg-web): never print an " LLC" entity claim
# until an entity we own is filed. "Miami Solutions Group LLC" (L22000200556) is registered to
# another Florida company, so asserting it to a distressed homeowner is a MARS 1015.3 / FDUTPA
# misrepresentation. Strip the suffix at display; the canonical entity string stays in sender.json
# for the legal pack, which is attorney-gated and used only once the entity actually exists.
# ---------------------------------------------------------------------------------------------
# BSG FLORIDA brand mark. Added 2026-08-23 at Alejandro's direction ("a name out there when we
# are putting those flyers out"). Deliberately ICON ONLY here, not the full BSG/FLORIDA wordmark
# lockup -- the card is 4.25x5.5in and the wordmark's chrome 3D lettering does not survive being
# scaled down to badge size, it just turns into a smudge. Full lockup lives untouched at
# design-system/assets/bsg_logo.png for a surface with room for it (the letter, the site).
# Source: design-system/assets/bsg_icon.png, trimmed to its own bounding box then resized to 420px
# wide. Embedded as base64 (same convention as msg_brand.py's MSG shield) so the generated HTML/PDF
# never depends on a relative file path surviving a copy or a move.
# THE MARK LOADS FROM brand/bsg-logo-master.png, IT IS NOT PASTED HERE.
# 2026-09-02: this constant held a base64 blob of the WRONG logo and every letter, flyer and
# letterhead in the business rendered it, because the bytes were frozen in source where nobody
# could see what they were. A logo you cannot look at is a logo you cannot check. The master file
# is now the single source; drop a new PNG there and every surface changes at once.
def _load_master_logo():
    import base64 as _b64, os as _os
    _p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'brand', 'bsg-logo-master.png')
    with open(_p, 'rb') as _f:
        return 'data:image/png;base64,' + _b64.b64encode(_f.read()).decode('ascii')


BSG_LOGO_B64 = _load_master_logo()
BSG_LOGO_W, BSG_LOGO_H = 440, 457


UNOWNED = ('miami solutions group',)


def _display_llc(raw):
    """Company name as it may legally be shown. Delegates to entity.display_llc()."""
    return entity.display_llc(raw)[0]


def sender():
    try:
        s = json.load(open(os.path.join(HERE, 'sender.json'), encoding='utf-8'))
        return s.get('name') or 'Alejandro Gonzalez', s.get('phone') or '', \
            _display_llc(s.get('llc') or '')
    except Exception:
        return 'Alejandro Gonzalez', '', _display_llc('')


def card_en(name, phone):
    return """<div class="card">
<div class="tag">FREE FORECLOSURE CONSULTATION &middot; 5 MINUTES &middot; NO FEE &middot; NO COMMITMENT</div>
<div class="hook">Think it&rsquo;s handled?<br>Keep this anyway.</div>
<div class="body">Many homeowners think it&rsquo;s handled &mdash; until the week of the sale.
Keep this number, just in case. One free call and our senior advisor &mdash; <b>over 30 years in
mortgages and foreclosure workouts</b> &mdash; personally reviews your case and lays out
<b>3, 4, sometimes 5 options</b>. Most people are only ever shown one.</div>
<div class="brandwrap"><img class="brand" src='""" + BSG_LOGO_B64 + """'></div>
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">CALL OR TEXT &middot; 24 HOURS &middot; 7 DAYS</div></div>
<div class="fill">Important date: <span class="line"></span></div>
</div>"""


def card_missed_en(name, phone):
    # THE BANNER LEAK (fixed 2026-08-23). This variant is left at a door when nobody answers, so
    # whoever picks it up reads it: a neighbour, a tenant, an adult child, whoever. The BODY was
    # written carefully for exactly that reason and says only "time-sensitive matter concerning this
    # property" -- and then the loudest line on the card announced FREE FORECLOSURE CONSULTATION to
    # that same audience. Discretion in 9pt body copy is worth nothing under a banner. The door card
    # now names no situation anywhere on its face. The 'keep' card is handed to the owner in person,
    # so that one may still say the word.
    #
    # WHY IT READ SOFT (Jose, same day): the old hook, "Sorry I missed you today", spends the
    # biggest type on the card apologising. The first line has one job, which is to make a man who is
    # being buried in mail stop and read the second line. "Doing nothing is a decision" states the
    # one thing that is true of every owner in this position and names no situation at all.
    # "He does not want your house" is the differentiator: every other card in that stack does.
    #
    # VOICE: no em dashes (2026-08-22 house standard, adopted everywhere else and missed here --
    # four of them on a card this small is the loudest tell a machine wrote it).
    return """<div class="card">
<div class="tag">TIME-SENSITIVE &middot; ABOUT THIS PROPERTY &middot; NO FEE &middot; NO OBLIGATION</div>
<div class="hook">Doing nothing<br>is a decision.</div>
<div class="body">I came by today about a <b>time-sensitive matter concerning this property</b>.
Most owners in this position are shown exactly <b>one</b> option, usually by the people they owe.
There are normally <b>three, four, sometimes five</b>. Our senior advisor has spent <b>over 30
years in mortgages and workouts</b> and will walk you through all of them on one call. He does not
charge for it, and <b>he does not want your house</b>.</div>
<div class="brandwrap"><img class="brand" src='""" + BSG_LOGO_B64 + """'></div>
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">CALL OR TEXT &middot; 24 HOURS &middot; 7 DAYS</div></div>
<div class="fill">Important date: <span class="line"></span></div>
</div>"""


def card_missed_es(name, phone):
    # Mirrors card_missed_en exactly: banner names no situation (this card is read by whoever finds
    # it at the door), no em dashes, and the hook stops spending the largest type on an apology.
    # "No quiere su casa" is the line that separates this from every other card in the stack.
    return """<div class="card">
<div class="tag">URGENTE &middot; SOBRE ESTA PROPIEDAD &middot; SIN COSTO &middot; SIN COMPROMISO</div>
<div class="hook">No hacer nada<br>tambi&eacute;n es decidir.</div>
<div class="body">Pas&eacute; hoy por un <b>asunto urgente relacionado con esta propiedad</b>. A la
mayor&iacute;a de los due&ntilde;os en esta situaci&oacute;n les muestran <b>una sola</b>
opci&oacute;n, casi siempre la gente a quien le deben. Normalmente hay <b>tres, cuatro, hasta
cinco</b>. Nuestro asesor principal tiene <b>m&aacute;s de 30 a&ntilde;os en hipotecas y
soluciones</b> y se las explica todas en una llamada. No cobra por eso, y
<b>no quiere su casa</b>.</div>
<div class="brandwrap"><img class="brand" src='""" + BSG_LOGO_B64 + """'></div>
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">LLAME O ENV&Iacute;E TEXTO &middot; 24 HORAS &middot; 7 D&Iacute;AS</div></div>
<div class="fill">Fecha importante: <span class="line"></span></div>
</div>"""


def card_es(name, phone):
    return """<div class="card">
<div class="tag">CONSULTA GRATIS SOBRE SU CASO DE FORECLOSURE &middot; 5 MINUTOS &middot; SIN COSTO</div>
<div class="hook">&iquest;Cree que ya lo tiene resuelto?<br>Guarde esto de todos modos.</div>
<div class="body">Muchos due&ntilde;os creen que ya est&aacute; resuelto &mdash; hasta la semana
de la subasta. Guarde este n&uacute;mero, por si acaso. Una llamada gratis y nuestro asesor
principal &mdash; <b>con m&aacute;s de 30 a&ntilde;os en hipotecas y soluciones de foreclosure</b>
&mdash; revisa su caso personalmente y le presenta <b>3, 4, hasta 5 opciones</b>. A la
mayor&iacute;a solo le muestran una.</div>
<div class="brandwrap"><img class="brand" src='""" + BSG_LOGO_B64 + """'></div>
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">LLAME O ENV&Iacute;E TEXTO &middot; 24 HORAS &middot; 7 D&Iacute;AS</div></div>
<div class="fill">Fecha importante: <span class="line"></span></div>
</div>"""


CSS = """
@page{size:Letter;margin:0}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI',Arial,sans-serif;color:#111}
.sheet{width:8.5in;height:11in;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;
       page-break-after:always;position:relative}
.sheet:last-child{page-break-after:auto}
.card{width:4.25in;height:5.5in;background:%(bg)s;padding:.28in .3in;position:relative;
      display:flex;flex-direction:column;outline:.5pt dashed rgba(0,0,0,.28);outline-offset:-.5pt}
.tag{font-size:7.2pt;font-weight:800;letter-spacing:.06em;background:#111;color:%(bg)s;
     padding:3pt 6pt;border-radius:3pt;align-self:flex-start;margin-bottom:8pt}
.hook{font-size:16.5pt;line-height:1.06;font-weight:900;margin-bottom:6pt}
.body{font-size:8.2pt;line-height:1.32;margin-bottom:auto}
.who{margin:8pt 0 6pt;border-top:1.6pt solid #111;padding-top:6pt}
.nm{font-size:13pt;font-weight:900;letter-spacing:.01em}
.tel{font-size:19pt;font-weight:900;letter-spacing:.01em;margin:1pt 0}
.always{font-size:7.6pt;font-weight:800;letter-spacing:.1em}
.fill{font-size:8.6pt;font-weight:700;margin:5pt 0 6pt}
.line{display:inline-block;width:1.7in;border-bottom:1pt solid #111;height:9pt;vertical-align:bottom}
.fine{font-size:8.2pt;line-height:1.22;color:#111}
/* BRAND BLOCK. NOT absolutely positioned in a corner (tried that 2026-08-23, it landed on top of
   the .tag banner: .tag wraps to two lines on both variants and spans nearly the full width, so the
   top-right corner is not empty space, it just looked empty in the one mockup I checked).
   It sits in the flex flow instead, between .body and .who, and `margin:auto 0` centres it in
   whatever vertical space is left over -- which is the dead zone .body's own margin-bottom:auto
   was already creating. Fills the void instead of fighting for a corner, and the lockup gets to be
   big enough that the FLORIDA wordmark is actually legible. */
/* 1.4in is sized against the TIGHTEST of the four faces, not the roomiest: the missedyou cards run
   a longer body than the keep cards and leave 1.77in between the copy and the divider (measured, in
   the browser, on all four). At 1.4in wide the lockup is 1.45in tall and still clears both with
   ~.13in of air top and bottom. Going bigger makes FLORIDA marginally more legible and makes the
   pink door card look crammed -- if this ever needs to grow, re-measure the missedyou/es face first,
   that is the one that runs out of room. */
.brandwrap{margin:auto 0;text-align:center;padding:2pt 0}
.brand{width:1.4in;height:auto;display:block;margin:0 auto}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--color', default='yellow', choices=sorted(COLORS))
    ap.add_argument('--name', default='')
    ap.add_argument('--phone', default='')
    # 'keep' = Jesse's keep-this-anyway card (2026-08-12). 'missedyou' = Jose's door leave-behind
    # (2026-08-16 meeting): Carlos drops it when nobody answers. Jose's spoken line named the
    # recipient's foreclosure sale outright; ON PAPER that is a third-party disclosure (anyone at
    # the door reads it), the exact exposure the 8/12 legal pass closed - so the printed card says
    # "time-sensitive matter concerning this property" and keeps his bank-wins-you-lose frame as a
    # general statement. The sale-date line stays hand-filled, OWNER'S PRESENCE ONLY.
    ap.add_argument('--variant', default='keep', choices=('keep', 'missedyou'))
    a = ap.parse_args()

    dn, dp, _ = sender()
    name, phone = (a.name or dn), (a.phone or dp)
    if not phone:
        raise SystemExit('no phone: set sender.json or pass --phone')

    _en, _es = (card_missed_en, card_missed_es) if a.variant == 'missedyou' else (card_en, card_es)
    en = _en(name, phone) % (H.escape(name), H.escape(phone))
    es = _es(name, phone) % (H.escape(name), H.escape(phone))
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>BSG Florida Leave-Behind</title>'
           '<style>%s</style></head><body>'
           '<div class="sheet">%s</div><div class="sheet">%s</div>'
           '</body></html>') % (CSS % {'bg': COLORS[a.color]}, en * 4, es * 4)

    today = datetime.date.today().isoformat()
    html_out = os.path.join(HERE, 'BSG_Flyer_%s_%s_%s.html' % (a.variant, a.color, today))
    open(html_out, 'w', encoding='utf-8').write(doc)

    from playwright.sync_api import sync_playwright
    pdfs = [os.path.join(HERE, 'BSG_Flyer_%s_%s_%s.pdf' % (a.variant, a.color, today))]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        pdfs.append(P.out('BSG_Flyer_%s_%s_%s.pdf' % (a.variant, a.color, today)))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto('file:///' + html_out.replace(os.sep, '/'))
        pg.wait_for_timeout(400)
        pdf = pg.pdf(format='Letter', print_background=True, prefer_css_page_size=True,
                     margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    for o in pdfs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'wb').write(pdf)
        print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))
    print('Duplex print (flip on LONG edge), cut twice -> 4 bilingual cards/sheet.')


if __name__ == '__main__':
    main()
