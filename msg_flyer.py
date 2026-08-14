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

Run:  python msg_flyer.py                        # safety-yellow, sender.json identity
      python msg_flyer.py --color pink           # hot pink stock look
      python msg_flyer.py --name X --phone Y     # someone else's paper
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
<div class="body">Many homeowners think it&rsquo;s handled &mdash; until the week of the sale.
Keep this number, just in case. One free call and our senior advisor &mdash; <b>over 30 years in
mortgages and foreclosure workouts</b> &mdash; personally reviews your case and lays out
<b>3, 4, sometimes 5 options</b>. Most people are only ever shown one.</div>
<div class="who"><div class="nm">%s</div>
<div class="tel">%s</div>
<div class="always">CALL OR TEXT &middot; 24 HOURS &middot; 7 DAYS</div></div>
<div class="fill">Important date: <span class="line"></span></div>
<div class="fine">%s &middot; We are not a law firm and do not give legal advice. We are not
associated with the government, and our service is not approved by the government or your lender.
Even if you use our service, your lender may not agree to change your loan. You may stop doing
business with us at any time. Consultations are always free.</div>
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
<div class="fine">%s &middot; No somos un bufete de abogados y no damos consejos legales. No
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
    a = ap.parse_args()

    dn, dp, llc = sender()
    name, phone = (a.name or dn), (a.phone or dp)
    if not phone:
        raise SystemExit('no phone: set sender.json or pass --phone')

    en = card_en(name, phone) % (H.escape(name), H.escape(phone), H.escape(llc))
    es = card_es(name, phone) % (H.escape(name), H.escape(phone), H.escape(llc))
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>MSG Leave-Behind</title>'
           '<style>%s</style></head><body>'
           '<div class="sheet">%s</div><div class="sheet">%s</div>'
           '</body></html>') % (CSS % {'bg': COLORS[a.color]}, en * 4, es * 4)

    today = datetime.date.today().isoformat()
    html_out = os.path.join(HERE, 'MSG_Flyer_%s_%s.html' % (a.color, today))
    open(html_out, 'w', encoding='utf-8').write(doc)

    from playwright.sync_api import sync_playwright
    pdfs = [os.path.join(HERE, 'MSG_Flyer_%s_%s.pdf' % (a.color, today))]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        pdfs.append(os.path.expanduser(os.path.join(
            '~', 'OneDrive', 'Desktop', 'DEALFLOW', 'MSG_Flyer_%s_%s.pdf' % (a.color, today))))
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
