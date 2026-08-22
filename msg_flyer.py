#!/usr/bin/env python
"""msg_flyer — the leave-behind. Jesse's replacement for the business card (2026-08-12 call).

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
see msg_brand.py's sender notes). Override per-batch with --name/--phone for Carlos's paper:
    python msg_flyer.py --name "Carlos Gonzalez" --phone "(305) 555-0123"

COMPLIANCE, BAKED IN, NOT OPTIONAL (hardened by the 2026-08-12 legal adversarial pass)
 * "30+ years" is attributed to the SENIOR ADVISOR, never to the company — MSG is new, and a
   company-age claim on paper is FDUTPA bait.
 * MARS/Reg O ad disclosures in the microprint: not the government, not the lender, lender may
   not agree, you may stop at any time, free consultation. No fee language anywhere else.
 * "We are not a law firm and do not give legal advice."
 * The sale-date blank is filled ONLY when handing to the titled owner — never a third party
   (third-party foreclosure disclosure is the lawsuit; the playbook's card 8 owns this rule).

TWO PRINT PATHS — WHY --stock EXISTS (2026-08-21, the print-counter problem)
The colored PDF paints #FFE818 edge-to-edge, and that file cannot be printed as designed:
letter printers (home and FedEx/Office Depot alike) do not print borderless, so the sheet
comes back with white strips on all four edges — after the two cuts, EVERY card wears white
stripes on its outer edges, on a card whose whole doctrine is a solid loud color. It also
bills as full-coverage color duplex (~5x the B&W meter) to lay down toner-yellow, which is
the wrong yellow: the doctrine is loud PAPER ("like a turd in a punch bowl"), not loud ink.
`--stock` renders the artwork the way a print shop actually does this: BLACK INK ONLY on a
white background, printed B&W duplex onto loud colored stock (Astrobrights "Solar Yellow"
65 lb cover or similar). Unprinted area IS the paper, so the color runs truly edge-to-edge
and the un-printable margin becomes invisible. The black tag box keeps its knockout text by
setting the letters WHITE — white is simply not printed, so the letters show the paper.
The colored PDF stays for screens: it is the swatch you match the paper against, never the
file you hand the counter.

Run:  python msg_flyer.py                        # safety-yellow, sender.json identity
      python msg_flyer.py --color pink           # hot pink stock look
      python msg_flyer.py --name X --phone Y     # someone else's paper
      python msg_flyer.py --stock                # ink-only file: print B&W on colored stock
"""
import argparse
import datetime
import html as H
import json
import os

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
        return s.get('name') or 'Alejandro Gonzalez', s.get('phone') or '', \
            _display_llc(s.get('llc') or 'Miami Solutions Group')
    except Exception:
        return 'Alejandro Gonzalez', '', 'Miami Solutions Group'


def card_en(name, phone):
    return """<div class="card">
<div class="tag">FREE FORECLOSURE CONSULTATION &middot; 5 MINUTES &middot; NO FEE &middot; NO COMMITMENT</div>
<div class="hook">Think it&rsquo;s handled?<br>Keep this anyway.</div>
<div class="body">Many homeowners think it&rsquo;s handled, right up until the week of the
sale. Keep this number, just in case. One free call and our senior advisor, with <b>over 30 years
in mortgages and foreclosure workouts</b>, personally reviews your case and lays out
<b>3, 4, sometimes 5 options</b>. Most people are only ever shown one.</div>
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">CALL OR TEXT &middot; 24 HOURS &middot; 7 DAYS</div></div>
<div class="fill">Important date: <span class="line"></span></div>
<div class="fine">%s. We are not a law firm and do not give legal advice. We are not
associated with the government, and our service is not approved by the government or your lender.
Even if you use our service, your lender may not agree to change your loan. You may stop doing
business with us at any time. Consultations are always free.</div>
</div>"""


def card_missed_en(name, phone):
    return """<div class="card">
<div class="tag">FREE FORECLOSURE CONSULTATION &middot; 5 MINUTES &middot; NO FEE &middot; NO COMMITMENT</div>
<div class="hook">Sorry I missed you today.</div>
<div class="body">I came by to visit with you about a <b>time-sensitive matter concerning this
property</b>. When an owner does nothing, the bank wins and the owner loses. There are usually
<b>3, 4, sometimes 5 options</b> nobody has shown you. One free call and our senior advisor, with
<b>over 30 years in mortgages and foreclosure workouts</b>, walks you through every one of
them.</div>
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">CALL OR TEXT &middot; 24 HOURS &middot; 7 DAYS</div></div>
<div class="fill">Important date: <span class="line"></span></div>
<div class="fine">%s. We are not a law firm and do not give legal advice. We are not
associated with the government, and our service is not approved by the government or your lender.
Even if you use our service, your lender may not agree to change your loan. You may stop doing
business with us at any time. Consultations are always free.</div>
</div>"""


def card_missed_es(name, phone):
    return """<div class="card">
<div class="tag">CONSULTA GRATIS SOBRE SU CASO DE FORECLOSURE &middot; 5 MINUTOS &middot; SIN COSTO</div>
<div class="hook">Lamento no haberlo encontrado hoy.</div>
<div class="body">Pas&eacute; a visitarlo por un <b>asunto urgente relacionado con esta
propiedad</b>. Cuando un due&ntilde;o no hace nada, el banco gana y el due&ntilde;o pierde. Casi
siempre hay <b>3, 4, hasta 5 opciones</b> que nadie le ha mostrado. Una llamada gratis y nuestro
asesor principal, <b>con m&aacute;s de 30 a&ntilde;os en hipotecas y soluciones de
foreclosure</b>, le explica cada una.</div>
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">LLAME O ENV&Iacute;E TEXTO &middot; 24 HORAS &middot; 7 D&Iacute;AS</div></div>
<div class="fill">Fecha importante: <span class="line"></span></div>
<div class="fine">%s. No somos un bufete de abogados y no damos consejos legales. No
estamos asociados con el gobierno, y nuestro servicio no est&aacute; aprobado por el gobierno ni
por su banco. Aunque use nuestro servicio, es posible que su banco no acepte modificar su
pr&eacute;stamo. Puede dejar de trabajar con nosotros en cualquier momento. Las consultas siempre
son gratis.</div>
</div>"""


def card_es(name, phone):
    return """<div class="card">
<div class="tag">CONSULTA GRATIS SOBRE SU CASO DE FORECLOSURE &middot; 5 MINUTOS &middot; SIN COSTO</div>
<div class="hook">&iquest;Cree que ya lo tiene resuelto?<br>Guarde esto de todos modos.</div>
<div class="body">Muchos due&ntilde;os creen que ya est&aacute; resuelto, hasta la semana de la
subasta. Guarde este n&uacute;mero, por si acaso. Una llamada gratis y nuestro asesor principal,
<b>con m&aacute;s de 30 a&ntilde;os en hipotecas y soluciones de foreclosure</b>, revisa su caso
personalmente y le presenta <b>3, 4, hasta 5 opciones</b>. A la mayor&iacute;a solo le muestran
una.</div>
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">LLAME O ENV&Iacute;E TEXTO &middot; 24 HORAS &middot; 7 D&Iacute;AS</div></div>
<div class="fill">Fecha importante: <span class="line"></span></div>
<div class="fine">%s. No somos un bufete de abogados y no damos consejos legales. No
estamos asociados con el gobierno, y nuestro servicio no est&aacute; aprobado por el gobierno ni
por su banco. Aunque use nuestro servicio, es posible que su banco no acepte modificar su
pr&eacute;stamo. Puede dejar de trabajar con nosotros en cualquier momento. Las consultas siempre
son gratis.</div>
</div>"""


CSS = """
@page{size:Letter;margin:0}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI',Arial,sans-serif;color:#111}
.sheet{width:8.5in;height:11in;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;
       page-break-after:always;position:relative}
.sheet:last-child{page-break-after:auto}
/* TYPE SCALE — two things were wrong on the 8/21 proof and they were the same bug.
   `.fine` was 8.2pt, the SAME size as `.body`, so the MARS/Reg O disclosure rendered as five
   lines of full-size body copy and read as if the disclaimer were the message — on a card whose
   entire doctrine is "name and phone huge". Fine print that is not fine is not fine print.
   And because every block was undersized for a 4.25x5.5in card, `margin-bottom:auto` dumped ALL
   the leftover space into one ~1.2in dead void in the middle, which reads as a broken template
   rather than as breathing room.
   Fix: the disclosure drops to true microprint and the pitch/contact type grows to fill the card,
   with `justify-content:space-between` distributing what slack remains as several small gaps
   instead of one hole.
   DO NOT take `.fine` below 6pt. MARS (12 CFR 1015) requires these disclosures be clear and
   prominent — noticeable and readable to an ordinary consumer. 6pt black on loud stock, held in
   the hand, clears that; 5pt starts arguing the other side of it for us. Shrink the copy, never
   the point size. */
/* NOTE: deliberately NOT `overflow:hidden`. Clipping would silently truncate the MARS disclosure
   on the longer Spanish side and ship a card that looks fine and is not. Overflow stays visible so
   a human proofing the PDF sees the bleed, and _assert_fits() below fails the build outright. */
.card{width:4.25in;height:5.5in;background:%(bg)s;padding:.28in .3in;position:relative;
      display:flex;flex-direction:column;justify-content:space-between;
      outline:.5pt dashed rgba(0,0,0,.28);outline-offset:-.5pt}
/* text-wrap:balance - the tag is too long for one line in both languages, and left to itself it
   orphans the last word ("...NO FEE - NO / COMMITMENT"). Balanced, it breaks into two even lines. */
.tag{font-size:7pt;font-weight:800;letter-spacing:.04em;background:#111;color:%(tagink)s;
     padding:3pt 6pt;border-radius:3pt;align-self:flex-start;text-wrap:balance}
.hook{font-size:19pt;line-height:1.06;font-weight:900}
.body{font-size:9pt;line-height:1.34}
.who{border-top:1.6pt solid #111;padding-top:6pt}
.nm{font-size:14pt;font-weight:900;letter-spacing:.01em}
.tel{font-size:21pt;font-weight:900;letter-spacing:.01em;margin:1pt 0}
.always{font-size:7.6pt;font-weight:800;letter-spacing:.1em}
.fill{font-size:8.6pt;font-weight:700}
.line{display:inline-block;width:1.7in;border-bottom:1pt solid #111;height:9pt;vertical-align:bottom}
.fine{font-size:6pt;line-height:1.28;color:#111}
"""


class FlyerError(Exception):
    pass


# Measured in the live page, before the PDF is written. A card whose content exceeds its 4.25x5.5in
# box bleeds into the neighbouring card and across the cut line, and the block that overruns is
# always the LAST one - the MARS/Reg O disclosure. That is the one element on this card that may
# never be quietly lost, and the Spanish copy runs ~15% longer than the English, so the side that
# breaks first is the side nobody proofreads. Refuse to write the PDF instead.
_FIT_JS = """() => Array.from(document.querySelectorAll('.card')).map((c, i) => {
  const last = c.querySelector('.fine');
  return {i, over: Math.round(c.scrollHeight - c.clientHeight),
          fineBottom: Math.round(last.getBoundingClientRect().bottom
                                 - c.getBoundingClientRect().bottom)};
}).filter(r => r.over > 1 || r.fineBottom > -1)"""


def _assert_fits(pg):
    bad = pg.evaluate(_FIT_JS)
    if bad:
        w = '; '.join('card %d overflows by %dpx (fine print %dpx past the cut)'
                      % (r['i'], r['over'], r['fineBottom']) for r in bad[:4])
        raise FlyerError(
            'card content does not fit its 4.25x5.5in box - %d card(s): %s. The disclosure is the '
            'block that runs over, so this PDF would ship a truncated MARS notice. Shorten the copy '
            'or reduce .hook/.body - do NOT reduce .fine below 6pt.' % (len(bad), w))


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
    # Ink-only artwork for printing on loud colored paper stock (see the docstring's print-path
    # section). White background = unprinted = the paper's own color; the tag letters go white
    # for the same reason - a B&W printer leaves them as paper showing through the black box.
    ap.add_argument('--stock', action='store_true')
    a = ap.parse_args()

    dn, dp, llc = sender()
    name, phone = (a.name or dn), (a.phone or dp)
    if not phone:
        raise SystemExit('no phone: set sender.json or pass --phone')

    _en, _es = (card_missed_en, card_missed_es) if a.variant == 'missedyou' else (card_en, card_es)
    en = _en(name, phone) % (H.escape(name), H.escape(phone), H.escape(llc))
    es = _es(name, phone) % (H.escape(name), H.escape(phone), H.escape(llc))
    palette = ({'bg': '#ffffff', 'tagink': '#ffffff'} if a.stock
               else {'bg': COLORS[a.color], 'tagink': COLORS[a.color]})
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>MSG Leave-Behind</title>'
           '<style>%s</style></head><body>'
           '<div class="sheet">%s</div><div class="sheet">%s</div>'
           '</body></html>') % (CSS % palette, en * 4, es * 4)

    today = datetime.date.today().isoformat()
    colorname = 'stock' if a.stock else a.color
    html_out = os.path.join(HERE, 'MSG_Flyer_%s_%s_%s.html' % (a.variant, colorname, today))
    open(html_out, 'w', encoding='utf-8').write(doc)

    from playwright.sync_api import sync_playwright
    pdfs = [os.path.join(HERE, 'MSG_Flyer_%s_%s_%s.pdf' % (a.variant, colorname, today))]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        pdfs.append(os.path.expanduser(os.path.join(
            '~', 'OneDrive', 'Desktop', 'DEALFLOW', 'MSG_Flyer_%s_%s_%s.pdf' % (a.variant, colorname, today))))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto('file:///' + html_out.replace(os.sep, '/'))
        pg.wait_for_timeout(400)
        try:
            _assert_fits(pg)          # never write a PDF with a clipped disclosure
            pdf = pg.pdf(format='Letter', print_background=True, prefer_css_page_size=True,
                         margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        finally:
            b.close()
    for o in pdfs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'wb').write(pdf)
        print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))
    if a.stock:
        print('B&W duplex (flip on LONG edge) on LOUD COLORED STOCK (Astrobrights 65lb cover), '
              'cut twice -> 4 bilingual cards/sheet. The colored PDF is the paper swatch, not a print file.')
    else:
        print('Duplex print (flip on LONG edge), cut twice -> 4 bilingual cards/sheet.')


if __name__ == '__main__':
    main()
