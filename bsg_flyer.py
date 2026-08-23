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

COMPLIANCE, BAKED IN, NOT OPTIONAL (hardened by the 2026-08-12 legal adversarial pass)
 * "30+ years" is attributed to the SENIOR ADVISOR, never to the company — BSG is new, and a
   company-age claim on paper is FDUTPA bait.
 * MARS/Reg O ad disclosures in the microprint: not the government, not the lender, lender may
   not agree, you may stop at any time, free consultation. No fee language anywhere else.
 * "We are not a law firm and do not give legal advice."
 * The sale-date blank is filled ONLY when handing to the titled owner — never a third party
   (third-party foreclosure disclosure is the lawsuit; the playbook's card 8 owns this rule).

Run:  python bsg_flyer.py                        # safety-yellow, sender.json identity
      python bsg_flyer.py --color pink           # hot pink stock look
      python bsg_flyer.py --name X --phone Y     # someone else's paper
"""
import argparse
import datetime
import html as H
import json
import os

import disclaimer as D
import entity
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))

COLORS = {  # loud enough to find in a drawer, light enough that black text stays clean
    'yellow': '#FFE818',
    'pink':   '#FF5FA2',
    'green':  '#7CFC5A',
    'orange': '#FFA020',
}


# The entity gate lives in entity.py -- a Sunbiz-verified fact, not a hand-edited list. See that
# module for why. Fail-closed: the " LLC" suffix prints only when the register substantiates it.
UNOWNED = ()   # DEPRECATED shim; entity.DENY is the manual kill switch.


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
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">CALL OR TEXT &middot; 24 HOURS &middot; 7 DAYS</div></div>
<div class="fill">Important date: <span class="line"></span></div>
<div class="fine">""" + D.mars('%s', 'en') + """</div>
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
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">CALL OR TEXT &middot; 24 HOURS &middot; 7 DAYS</div></div>
<div class="fill">Important date: <span class="line"></span></div>
<div class="fine">""" + D.mars('%s', 'en') + """</div>
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
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">LLAME O ENV&Iacute;E TEXTO &middot; 24 HORAS &middot; 7 D&Iacute;AS</div></div>
<div class="fill">Fecha importante: <span class="line"></span></div>
<div class="fine">""" + D.mars('%s', 'es') + """</div>
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
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">LLAME O ENV&Iacute;E TEXTO &middot; 24 HORAS &middot; 7 D&Iacute;AS</div></div>
<div class="fill">Fecha importante: <span class="line"></span></div>
<div class="fine">""" + D.mars('%s', 'es') + """</div>
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

    dn, dp, llc = sender()
    name, phone = (a.name or dn), (a.phone or dp)
    if not phone:
        raise SystemExit('no phone: set sender.json or pass --phone')

    _en, _es = (card_missed_en, card_missed_es) if a.variant == 'missedyou' else (card_en, card_es)
    en = _en(name, phone) % (H.escape(name), H.escape(phone), H.escape(llc))
    es = _es(name, phone) % (H.escape(name), H.escape(phone), H.escape(llc))
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>BSG Leave-Behind</title>'
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
