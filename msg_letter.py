#!/usr/bin/env python
"""msg_letter — the fill-in-the-blank door letter. Carlos's #1 field request (2026-08-14).

WHAT THIS IS AND WHY IT IS DIFFERENT FROM THE POSTCARD
The postcard (msg_flyer.py) is the loud thing that survives a drawer. This is the quiet thing that
goes in a sealed envelope when nobody answers, or into the owner's hand when they do. It is
pre-printed with BLANKS the field rep fills in by hand at the door:

    OWNER  ____________   PROPERTY  ____________   IMPORTANT DATE  ____________
    FROM   ____________   DIRECT    ____________

Handwriting the owner's name and their date is the entire point. A printed mail-merge reads as a
mass mailing and gets binned with the others; five handwritten fields read as a person who actually
stood at that address, looked something up, and came back. That is the difference between the tenth
identical letter that week and the one that gets kept.

THE ENVELOPE RULE IS NOT OPTIONAL — IT IS THE WHOLE DESIGN CONSTRAINT
This letter NEVER leaves the rep's hand loose. It goes in a blank envelope with the owner's FIRST
NAME and "PERSONAL" on the front, wedged in the door (never the mailbox — that is a federal
offence). Reason: the word "foreclosure" must never be visible to anyone not on title. A spouse who
is not on title, an adult son, a tenant, a neighbour reading over a shoulder — every one of those is
a third-party disclosure, and it is how you scare a family, spring a tenant into withholding rent,
and turn the owner against you in one move.

So the letter itself is written to be readable by the owner and uninteresting to anyone else. It
does not shout the situation on first glance; the specifics live in the blanks the rep fills in.

COMPLIANCE, BAKED IN (2026-08-12 legal pass, same rails as the postcard)
 * MARS/Reg O 12 CFR 1015.4(a) disclosures at body-copy size, both languages. Not a footnote.
 * No fee language anywhere except "you will never pay us anything".
 * "30+ years" attaches to the SENIOR ADVISOR, never to the company. The company is new.
 * No outcome promises. Options only. No guarantee, no "we will save your home".
 * "We are not a law firm and do not give legal advice."
 * Nothing gets signed in the field.

LAYOUT: half-letter (5.5 x 8.5), 2 per sheet, English page 1 / Spanish page 2. Duplex on the LONG
edge and cut once — each cut gives one bilingual letter.

Run:  python msg_letter.py                       # identity from sender.json
      python msg_letter.py --name "Carlos Gonzalez" --phone "(305) 555-0123"
"""
import argparse
import datetime
import html as H
import json
import os

import disclaimer as D
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))

# The registered name of the company. Until an entity is actually formed and the name is one we own,
# this must NOT print an "LLC" claim -- claiming a registered entity that does not exist is a
# separately actionable misrepresentation to a distressed homeowner (FDUTPA + MARS 1015.3).
# msg_letter refuses to print an unowned name; see _company().
UNOWNED = ('miami solutions group',)   # verified 2026-08-13: L22000200556, owned by other parties


def _sender():
    try:
        return json.load(open(os.path.join(HERE, 'sender.json'), encoding='utf-8'))
    except Exception:
        return {}


def _company(override=''):
    """(display_name, warning). Never returns an entity claim we cannot substantiate."""
    s = _sender()
    raw = (override or s.get('llc') or '').strip()
    bare = raw.lower().replace(',', '').replace('llc', '').strip()
    if not raw:
        return '', 'no company name set in sender.json'
    if bare in UNOWNED:
        # Strip the entity suffix and flag it. The letter still prints -- a field rep with no paper
        # is worse than a field rep with unbranded paper -- but it will not assert an LLC we do not
        # own, and the operator gets told loudly at build time.
        return raw.replace(' LLC', '').replace(', LLC', '').strip(), (
            'the configured name is registered to another Florida company (L22000200556). '
            'Printed WITHOUT an entity suffix. Do not run a large print job until the real name is set.')
    return raw, ''


BLANK = '<span class="bl"></span>'

EN = """<div class="pg">
  <div class="hd"><div class="co">@@CO@@</div>
    <div class="sub">Miami-Dade &middot; Broward &middot; Palm Beach</div></div>

  <div class="fill">
    <div class="fr"><span class="lb">For</span>@@B@@</div>
    <div class="fr"><span class="lb">Property</span>@@B@@</div>
    <div class="fr"><span class="lb">Important date</span>@@B@@</div>
  </div>

  <p class="op">I stopped by today and you weren&rsquo;t home, so I&rsquo;m leaving this.</p>

  <p>My name is <b>@@NAME@@</b>. I work with homeowners here in South Florida whose property has a
  case at the county court. I looked up the public record on your address before I came. That is
  how I knew the date above.</p>

  <p>You may already have a plan. Most people I meet do. Keep working it.
  I&rsquo;m not here to talk you out of it, and I&rsquo;m not here to buy your house. I&rsquo;m here
  to be your parachute: a backup that costs nothing, in case the answer you are waiting on comes
  late. What if it comes a week after the date above?</p>

  <p><b>Here is the whole offer:</b> a free five-minute call with our senior advisor. He has spent
  over 30 years on situations exactly like this one. He goes through your case with you and lays
  out the options that actually exist for it, usually three or four. Most people are only ever
  shown one.</p>

  <p><b>You will never pay us anything.</b> Not now, not later, not at the end. There is nothing to
  sign. If nothing we say is useful, we shake hands and part friends.</p>

  <p class="cl">Sooner keeps more doors open. You should be the one who picks the door.</p>

  <p><b>P.S.</b> Save my number below in your phone right now. The day you want it, you will
  already have it.</p>

  <div class="sig">
    <div class="sr"><span class="lb">From</span>@@B@@</div>
    <div class="sr"><span class="lb">Call or text</span>@@B@@</div>
    <div class="any">Any hour, seven days</div>
  </div>

  <div class="fine">@@FINE_EN@@</div>
</div>"""

ES = """<div class="pg">
  <div class="hd"><div class="co">@@CO@@</div>
    <div class="sub">Miami-Dade &middot; Broward &middot; Palm Beach</div></div>

  <div class="fill">
    <div class="fr"><span class="lb">Para</span>@@B@@</div>
    <div class="fr"><span class="lb">Propiedad</span>@@B@@</div>
    <div class="fr"><span class="lb">Fecha importante</span>@@B@@</div>
  </div>

  <p class="op">Pas&eacute; por aqu&iacute; hoy y no lo encontr&eacute;, por eso le dejo esta nota.</p>

  <p>Mi nombre es <b>@@NAME@@</b>. Trabajo con due&ntilde;os de casa aqu&iacute; en el sur de la
  Florida cuya propiedad tiene un caso en la corte del condado. Revis&eacute; el registro
  p&uacute;blico de su direcci&oacute;n antes de venir. Por eso pude escribir la fecha de arriba.</p>

  <p>Puede que usted ya tenga un plan. Casi todos los que visito lo tienen. Siga con su plan. No vengo a quit&aacute;rselo, y no vengo a comprarle la casa. Vengo a ser su
  paraca&iacute;das: un respaldo que no cuesta nada, por si la respuesta que usted espera llega
  tarde. &iquest;Y si llega una semana despu&eacute;s de la fecha de arriba?</p>

  <p><b>Esta es toda la oferta:</b> una llamada gratis de cinco minutos con nuestro asesor
  principal. &Eacute;l lleva m&aacute;s de 30 a&ntilde;os en situaciones exactamente como la suya.
  Revisa su caso con usted y le explica las opciones que de verdad existen, normalmente tres o
  cuatro. A la mayor&iacute;a de la gente solo le ense&ntilde;an una.</p>

  <p><b>Usted nunca nos paga nada.</b> Ni ahora, ni despu&eacute;s, ni al final. No hay nada que
  firmar. Si nada de lo que decimos le sirve, nos damos la mano y quedamos como amigos.</p>

  <p class="cl">Mientras m&aacute;s pronto, m&aacute;s puertas abiertas. Y usted debe ser quien
  escoja la puerta.</p>

  <p><b>P.D.</b> Guarde mi n&uacute;mero de abajo en su tel&eacute;fono ahora mismo. El d&iacute;a
  que lo quiera usar, ya lo va a tener.</p>

  <div class="sig">
    <div class="sr"><span class="lb">De parte de</span>@@B@@</div>
    <div class="sr"><span class="lb">Llame o escriba</span>@@B@@</div>
    <div class="any">A cualquier hora, siete d&iacute;as</div>
  </div>

  <div class="fine">@@FINE_ES@@</div>
</div>"""

# The MARS/Reg O fine print now comes from disclaimer.py — the single source shared with the
# flyer, the Lob mail and the outreach email. It used to be hand-copied here, which is how the
# flyer ended up missing the "not a lender or mortgage broker" sentence this file has always
# carried. @@CO@@ is this module's company token; mars() just drops it in where the name goes.
FINE_EN = D.mars('@@CO@@', 'en')
FINE_ES = D.mars('@@CO@@', 'es')

CSS = """
@page{size:Letter;margin:0}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',-apple-system,Arial,sans-serif;color:#111}
.sheet{width:8.5in;height:11in;display:grid;grid-template-rows:1fr 1fr;page-break-after:always}
.sheet:last-child{page-break-after:auto}
.pg{width:8.5in;height:5.5in;padding:.32in .45in .22in;display:flex;flex-direction:column;
    outline:.5pt dashed rgba(0,0,0,.25);outline-offset:-.5pt}
.hd{border-bottom:1.8pt solid #0B1730;padding-bottom:4pt;margin-bottom:8pt}
.co{font-size:14pt;font-weight:800;letter-spacing:.02em;color:#0B1730}
.sub{font-size:7.4pt;letter-spacing:.13em;text-transform:uppercase;color:#5a6577;margin-top:1pt}
.fill{background:#F4F7FC;border:.8pt solid #d8e0ee;border-radius:4pt;padding:6pt 9pt;margin-bottom:8pt}
.fr{display:flex;align-items:baseline;margin:3pt 0}
.lb{font-size:7.4pt;letter-spacing:.09em;text-transform:uppercase;color:#5a6577;
    width:1.15in;flex:none;font-weight:700}
.bl{flex:1;border-bottom:.9pt solid #6b7a90;height:12pt}
p{font-size:9.1pt;line-height:1.42;margin-bottom:5pt}
p.op{font-size:9.6pt;font-weight:600}
p.cl{font-weight:700;margin-top:2pt}
.sig{margin-top:auto;padding-top:6pt;border-top:.8pt solid #d8e0ee}
.sr{display:flex;align-items:baseline;margin:3pt 0}
.any{font-size:7.4pt;letter-spacing:.11em;text-transform:uppercase;color:#5a6577;
     font-weight:700;margin-top:2pt}
.fine{font-size:6.6pt;line-height:1.25;color:#333;margin-top:5pt}
"""


def build(name, phone, company):
    def page(tpl, fine):
        t = tpl.replace('@@B@@', BLANK).replace('@@NAME@@', H.escape(name))
        t = t.replace('@@FINE_EN@@', fine).replace('@@FINE_ES@@', fine)
        return t.replace('@@CO@@', H.escape(company) if company else 'Our office')

    en = page(EN, FINE_EN.replace('@@CO@@', H.escape(company) if company else 'Our office'))
    es = page(ES, FINE_ES.replace('@@CO@@', H.escape(company) if company else 'Nuestra oficina'))
    return ('<!doctype html><html><head><meta charset="utf-8"><title>Door Letter</title>'
            '<style>%s</style></head><body>'
            '<div class="sheet">%s%s</div><div class="sheet">%s%s</div>'
            '</body></html>') % (CSS, en, en, es, es)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', default='', help='the rep whose name is printed (blank = sender.json)')
    ap.add_argument('--phone', default='')
    ap.add_argument('--company', default='', help='override the company name')
    a = ap.parse_args()

    s = _sender()
    name = a.name or s.get('name') or ''
    phone = a.phone or s.get('phone') or ''
    company, warn = _company(a.company)
    if not name:
        raise SystemExit('no rep name: set sender.json or pass --name')

    doc = build(name, phone, company)
    today = datetime.date.today().isoformat()
    html_out = os.path.join(HERE, 'MSG_Door_Letter_%s.html' % today)
    open(html_out, 'w', encoding='utf-8').write(doc)

    from playwright.sync_api import sync_playwright
    outs = [os.path.join(HERE, 'MSG_Door_Letter_%s.pdf' % today)]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        outs.append(P.out('MSG_Door_Letter_%s.pdf' % today))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto('file:///' + html_out.replace(os.sep, '/'))
        pg.wait_for_timeout(400)
        pdf = pg.pdf(format='Letter', print_background=True, prefer_css_page_size=True,
                     margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    for o in outs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'wb').write(pdf)
        print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))

    if warn:
        print('\n!! COMPANY NAME: %s' % warn)
    print('\nDuplex on the LONG edge, cut once -> 2 bilingual letters per sheet.')
    print('Fill in by hand at the door: owner, property, important date, your name, your number.')
    print('ALWAYS in a sealed blank envelope marked [First name] / PERSONAL. Door handle, never the '
          'mailbox.')


if __name__ == '__main__':
    main()
