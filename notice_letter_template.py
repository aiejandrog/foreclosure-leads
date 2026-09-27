"""notice_letter_template.py -- PLACEHOLDER layout + wording for `outreach_mail.py --variant notice`.

    ###########################################################################################
    #  PLACEHOLDER -- TO BE REPLACED.  A new design is coming.  Nothing in this file is final:  #
    #  not the wording, not the layout, not the CSS.  Swap this whole file for the new design; #
    #  notice_letter.py (the logic, gates and config) should not need to change.               #
    ###########################################################################################

This is the ONE file that holds the notice letter's look and words. Everything that decides WHO gets
a letter, WHAT data goes in it, and WHETHER it may be built at all lives in notice_letter.py.

CONTRACT the replacement must keep (notice_letter.py and _noticelettertest.py rely on it):

  STRINGS      {'en': {...}, 'es': {...}} -- every word on the page, per language.
  CSS          one stylesheet string shared by both pages.
  PAGE         one HTML string for ONE page, filled with str.format(**fields). It is rendered twice
               per lead: English (front of the sheet) and Spanish (back of the sheet). Literal CSS/
               HTML braces must be doubled ({{ }}) because of str.format.
  C2M_LAYOUT   the Click2Mail job layout this design is built for. 'Address on First Page' means
               Click2Mail prints the recipient block in the top third of the FRONT page, so a real
               design must leave that zone clear (this placeholder does NOT -- it is not final).

Fields available to PAGE (all HTML-escaped by notice_letter.py before formatting, except the
already-built `ret_html` and `*_html` strings):
  {lang}           'en' or 'es'
  {owner}          owner name, 'First Last'
  {prop_addr}      property street address
  {prop_city_zip}  property city + ZIP ('Miami, FL 33101'), may be ''
  {prop_city_zip_sep}  ', ' when prop_city_zip is non-empty, else ''
  {mail_line1}     recipient mailing street (from the lead's mailing address)
  {mail_city_zip}  recipient mailing city/state/ZIP
  {plaintiff}      lender / plaintiff on the case
  {case}           case number
  {sale}           sale date (formatted per language when parseable)
  {date}           mail date (today, formatted per language)
  {ref}            reference, 'BSG-<case>'
  {phone}          the configured MAIL-ONLY phone number   (BSG_NOTICE_PHONE)
  {hours}          the configured call hours               (BSG_NOTICE_CALL_HOURS)
  {ret_html}       the configured return address / PMB, lines joined with <br> (BSG_NOTICE_RETURN_ADDRESS)
  {t_<key>}        STRINGS[lang][key] with the per-lead fields already substituted in

Wording rules that are NOT design and must survive any redesign: no government look-alike seal or
form number, no "FINAL NOTICE", the private-company / not-the-court / not-a-lawyer / advertisement
disclosure, and the opt-out sentence. _noticelettertest.py checks for the disclosure and opt-out keys.

Marketing copy, not a secret: this file may be committed. Never put lead data, names, addresses,
phone numbers, keys or tokens in it -- those come from the lead record and the gitignored config.
"""

IS_PLACEHOLDER = True

C2M_LAYOUT = 'Address on First Page'

# Per-lead placeholders inside STRINGS use {ADDR} {PLAINTIFF} {CASE} {SALE} {HOURS}; notice_letter.py
# fills them (escaped) before the page is formatted.
STRINGS = {
    'en': dict(
        date="Notice Date", case="Case No.", court="Court", sale="Sale Date", ref="Reference",
        courtv="Miami-Dade Circuit Court",
        head="TIME-SENSITIVE: YOUR FORECLOSURE SALE DATE",
        sub="Information from the public court record. Please read and call before your sale date.",
        att="ATTENTION", prop="PROPERTY",
        p1="Public court records show a foreclosure case on your property at <b>{ADDR}</b>, filed by <b>{PLAINTIFF}</b> in the Miami-Dade Circuit Court (Case No. <b>{CASE}</b>). The sale is currently scheduled for <b>{SALE}</b>.",
        p2="My name is Alejandro Gonzalez, with Biscayne Solutions Group, a private company here in South Florida. I am not your lender, I am not the court or any government office, and I am not a lawyer. I am writing because I read your case, not because you are on a mailing list.",
        p3="Owners in this situation usually still have choices before the sale date, and the sooner you look at them, the more of them are still open. I will go over your case with you in a free 15-minute call, with no cost and no obligation. If selling for cash before the sale is the right move, we can buy the house as-is. If another path is better for you, I will tell you so.",
        cta="Call or text before {SALE}:",
        hours="{HOURS} &nbsp;|&nbsp; Please have your case number ready: {CASE}",
        opt="If you would rather not hear from us, call or text the number above and say so, and we will not contact you again.",
        sign="Alejandro Gonzalez, Biscayne Solutions Group",
        fine="Biscayne Solutions Group is a private company. We are not affiliated with {PLAINTIFF}, your loan servicer, the Miami-Dade Clerk of Court, or any government agency. We are not a law firm and do not give legal advice. You are not required to use our services. This is an advertisement.",
    ),
    'es': dict(
        date="Fecha", case="Caso Núm.", court="Corte", sale="Fecha de subasta", ref="Referencia",
        courtv="Corte de Circuito de Miami-Dade",
        head="URGENTE: LA FECHA DE SUBASTA DE SU CASA",
        sub="Información del registro público de la corte. Por favor lea y llame antes de la fecha de subasta.",
        att="ATENCIÓN", prop="PROPIEDAD",
        p1="Los registros públicos de la corte muestran un caso de ejecución hipotecaria sobre su propiedad en <b>{ADDR}</b>, presentado por <b>{PLAINTIFF}</b> en la Corte de Circuito de Miami-Dade (Caso Núm. <b>{CASE}</b>). La subasta está programada para el <b>{SALE}</b>.",
        p2="Mi nombre es Alejandro Gonzalez, de Biscayne Solutions Group, una compañía privada aquí en el sur de la Florida. No soy su prestamista, no soy la corte ni ninguna oficina del gobierno, y no soy abogado. Le escribo porque leí su caso, no porque usted esté en una lista de correo.",
        p3="Los dueños en esta situación normalmente todavía tienen opciones antes de la fecha de subasta, y mientras más pronto las revise, más opciones quedan abiertas. Con gusto repaso su caso con usted en una llamada gratis de 15 minutos, sin costo ni compromiso. Si vender en efectivo antes de la subasta es lo correcto, podemos comprar la casa tal como está. Si otro camino le conviene más, se lo diré.",
        cta="Llame o escriba antes del {SALE}:",
        hours="{HOURS} &nbsp;|&nbsp; Tenga a mano su número de caso: {CASE}",
        opt="Si prefiere que no le contactemos, llame o escriba al número de arriba y díganoslo, y no le volveremos a contactar.",
        sign="Alejandro Gonzalez, Biscayne Solutions Group",
        fine="Biscayne Solutions Group es una compañía privada. No estamos afiliados con {PLAINTIFF}, su administrador de préstamo, la Secretaría de la Corte de Miami-Dade ni ninguna agencia del gobierno. No somos un bufete de abogados y no damos asesoría legal. Usted no está obligado a usar nuestros servicios. Esto es un anuncio.",
    ),
}

# PLACEHOLDER CSS (from the first draft, minus the preview tag and the blue per-lead styling).
CSS = """@page{size:Letter;margin:0}*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'DejaVu Sans',Arial,sans-serif;color:#111;font-size:10.2pt;line-height:1.38}
.page{width:8.5in;height:11in;padding:.55in .7in .45in;position:relative;page-break-after:always;background:#fff;overflow:hidden}
.page:last-child{page-break-after:auto}
.top{display:flex;justify-content:space-between;align-items:flex-start}
.ret{font-size:9pt;line-height:1.35}.ret b{font-size:11pt;letter-spacing:.02em}
.box{border:1.6pt solid #111;padding:6pt 9pt;font-size:9pt;line-height:1.45;min-width:2.9in}
.box b{display:inline-block;width:1.05in}
.addr{margin:.45in 0 .25in .1in;font-size:10.5pt;line-height:1.35}
h1{text-align:center;font-size:15pt;letter-spacing:.02em;margin:4pt 0 2pt}
.sub{text-align:center;font-size:9.2pt;color:#333;margin-bottom:10pt}
.att{border-top:1pt solid #111;border-bottom:1pt solid #111;padding:5pt 0;margin-bottom:10pt;font-size:10pt}
.att b{display:inline-block;width:.95in}
p{margin:0 0 8pt}
.cta{border:1.6pt solid #111;text-align:center;padding:8pt;margin:10pt 0}
.cta .l{font-size:10.5pt;font-weight:bold}.cta .n{font-size:20pt;font-weight:bold;letter-spacing:.03em;margin:2pt 0}
.cta .h{font-size:9pt}
.sig{margin-top:6pt}
.fine{position:absolute;left:.7in;right:.7in;bottom:.45in;font-size:7.6pt;color:#333;border-top:.8pt solid #999;padding-top:5pt;font-style:italic}"""

# PLACEHOLDER layout for ONE page. Rendered once per language (EN front, ES back).
PAGE = """<div class="page" lang="{lang}">
<div class="top"><div class="ret"><b>BISCAYNE SOLUTIONS GROUP</b><br>{ret_html}</div>
<div class="box"><b>{t_date}:</b> {date}<br><b>{t_case}:</b> {case}<br><b>{t_court}:</b> {t_courtv}<br><b>{t_sale}:</b> {sale}<br><b>{t_ref}:</b> {ref}</div></div>
<div class="addr">{owner}<br>{mail_line1}<br>{mail_city_zip}</div>
<h1>{t_head}</h1><div class="sub">{t_sub}</div>
<div class="att"><b>{t_att}:</b> {owner}<br><b>{t_prop}:</b> {prop_addr}{prop_city_zip_sep}{prop_city_zip}</div>
<p>{t_p1}</p><p>{t_p2}</p><p>{t_p3}</p>
<div class="cta"><div class="l">{t_cta}</div><div class="n">{phone}</div><div class="h">{t_hours}</div></div>
<p>{t_opt}</p><div class="sig">{t_sign}</div>
<div class="fine">{t_fine}</div></div>"""

DOCUMENT = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title><style>{css}</style></head><body>
{pages}
</body></html>"""
