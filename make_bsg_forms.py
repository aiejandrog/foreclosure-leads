#!/usr/bin/env python
"""Typeset the two client-facing BSG forms — Retainer Agreement and Third-Party Authorization.

Scope is deliberately narrow: **presentation and translation only.** The text of both documents is
reproduced as written. Nothing is added, removed, softened, or annotated — no review banners, no
internal guidance boxes, no "office only" markings. These are the copies a homeowner receives, so
they carry the emblem and the contact block and nothing that reads as an internal working file.

Retainer copy: Alejandro's `BSG RETAINER AGREEMENT.pdf`, verbatim.
TPA copy: the English template body in `02_Third-Party-Authorization-to-Release-Information-BSG.md`.
Spanish: a straight translation of those same documents. Same sections, same order, same fields —
no clause added, none dropped.

**No street address anywhere.** The only mailing address available is Alejandro's apartment, and
these go to homeowners, servicers and opposing counsel. Phone and email carry the contact. The TPA
keeps its "Mailing Address" line as an empty rule, because a servicer needs somewhere to send the
information it releases — that stays a fill-in, not a printed home address.

    python make_bsg_forms.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bsg_brand  # noqa: E402
import paths as P

OUT = P.out('BSG-Client-Forms')
MARK_H = 74
LLC = 'Biscayne Solutions Group, LLC'

# Email is PINNED per Jose's request (WhatsApp, 2026-08-08 10:58 PM): the company inbox goes on
# client paper, not the personal Gmail. Deliberately NOT overridden by sender.json — its email
# field still carries the personal address for the outreach pipeline's display name.
SENDER = {'phone': '(786) 631-1823', 'email': 'miamisolutionsgroup@gmail.com'}
try:
    _s = json.load(open(os.path.join(HERE, 'sender.json'), encoding='utf-8'))
    if (_s.get('phone') or '').strip():
        SENDER['phone'] = _s['phone'].strip()
except Exception:
    pass


def L(width=280, v=''):
    """A fill-in rule. Baseline-aligned so a filled line and an empty one sit at the same height.

    Pass `v` to print a merged value ON the rule. Merged fields stay visually identical to blank
    ones — same rule, same baseline — because a homeowner signing this should see one consistent
    form, not a mix of typed boxes and hand-written lines."""
    if v:
        return f'<span class="fill fv" style="min-width:{width}px">{v}</span>'
    return f'<span class="fill" style="min-width:{width}px"></span>'


# Case merge data. Empty = blank forms. Populated by --case on the command line.
C = {}


CSS = """
@page { size: Letter; margin: 0.62in 0.7in 0.55in 0.7in; }
* { box-sizing: border-box; }
body { margin:0; font:10.5pt/1.5 Georgia,'Times New Roman',serif; color:#14181F;
       -webkit-font-smoothing:antialiased; }
.sheet { max-width: 7.1in; margin: 0 auto; }

header.lh { display:flex; align-items:center; gap:18px; }
header.lh img { display:block; border:0; }
header.lh .meta { margin-left:auto; text-align:right;
  font:8.5pt/1.62 'Helvetica Neue',Helvetica,Arial,sans-serif; color:#1A1A1A; letter-spacing:.01em; }
header.lh .meta .co { font-weight:700; letter-spacing:.12em; text-transform:uppercase;
  font-size:9pt; color:#0B1730; }
header.lh .meta b { font-weight:700; letter-spacing:.05em; }
.rule { border-top:2.2px solid #14181F; margin:9px 0 20px; }

h1 { font:700 15.5pt/1.25 'Helvetica Neue',Helvetica,Arial,sans-serif; letter-spacing:.02em;
     margin:0 0 3px; color:#0B1730; text-transform:uppercase; }
.sub { font:9.5pt/1.45 'Helvetica Neue',Helvetica,Arial,sans-serif; color:#5A6472;
       letter-spacing:.05em; text-transform:uppercase; margin:0 0 18px; }
h2 { font:700 9.5pt/1.3 'Helvetica Neue',Helvetica,Arial,sans-serif; letter-spacing:.13em;
     text-transform:uppercase; color:#0B1730; margin:20px 0 9px;
     padding-bottom:5px; border-bottom:1px solid #D6DBE3; }
p { margin:0 0 8px; }
ol.terms { margin:0; padding-left:22px; }
ol.terms > li { margin:0 0 9px; padding-left:5px; }
ol.terms > li::marker { font-weight:700; color:#0B1730; }
.lead { font-weight:700; }

.fill { display:inline-block; border-bottom:1px solid #14181F; height:1.02em;
        vertical-align:-2px; margin:0 3px; }
.fill.fv { height:auto; min-height:1.02em; font-weight:600; padding:0 5px 1px;
           vertical-align:baseline; white-space:nowrap; }
.row { margin:0 0 9px; }
.row .lbl { font:700 9pt 'Helvetica Neue',Helvetica,Arial,sans-serif; letter-spacing:.05em;
            text-transform:uppercase; color:#3A4454; }
.grid2 { display:flex; gap:26px; }
.grid2 > * { flex:1; }
ul.opts { list-style:none; margin:2px 0 0; padding:0; }
ul.opts li { margin:0 0 6px; }
.box { border:1px solid #C9CDD4; padding:11px 14px; margin:10px 0 0; background:#FAFBFC; }
.sigline { margin-top:26px; }
.sigline .cap { font:8.5pt 'Helvetica Neue',Helvetica,Arial,sans-serif; color:#5A6472;
                letter-spacing:.05em; margin-top:4px; }
.mono { font:9.5pt/1.55 'Helvetica Neue',Helvetica,Arial,sans-serif; }
.note { font:9pt/1.5 'Helvetica Neue',Helvetica,Arial,sans-serif; color:#3A4454; }

footer.sig { margin-top:30px; padding-top:8px; border-top:1px solid #C9CDD4; text-align:center;
  font:7.5pt/1.65 'Helvetica Neue',Helvetica,Arial,sans-serif; color:#7C8492; letter-spacing:.05em; }
footer.sig .nm { display:block; font-weight:700; font-size:8.5pt; color:#5A6472;
  letter-spacing:.15em; text-transform:uppercase; margin-bottom:2px; }

.pgb { break-before:page; }
h2, .sigline, .box { break-inside:avoid; }
"""

# ------------------------------------------------------------------------------------------
# Strings. EN is the source wording; ES is a straight translation of that same wording.
# ------------------------------------------------------------------------------------------
T = {
 'en': {
  'disclaimer': 'Not a law firm. Not a HUD-approved housing counselor.',
  # --- retainer
  'r_title': 'Contract and Agreement for Services',
  'r_sub': 'Retainer Agreement &amp; Engagement Letter for Foreclosure Prevention Services',
  'date': 'Date', 'name': 'Name', 'ph': 'Ph', 'email': 'Email',
  'borrowers': 'Borrower(s) / Homeowner(s)',
  'prop_addr': 'Property Address', 'county': 'County', 'loan_no': 'Loan Number',
  'lender': 'Lender / Servicer', 'case_no': 'Foreclosure Case #',
  'sale_date': 'Foreclosure Sale Date', 'debt': 'Current Debt / Payoff',
  'principal': 'Principal Balance',
  'services': 'Services Retained',
  'svc': ['Loan Modification / Forbearance Plan',
          'Deed in Lieu of Foreclosure &amp; Cash for Keys',
          'BSG to Buy Subject Property',
          'Short Sale Negotiation',
          'Foreclosure Auction Prevention',
          'Other Services (please describe below)'],
  'desc': 'Description of Services &amp; Specific Terms',
  'fees': 'Fees &amp; Compensation', 'fee_struct': 'Fee Structure',
  'deposit': 'Initial Deposit', 'due': 'Payment Due Date', 'pay_method': 'Payment Method',
  'wire': 'Wire Transfer', 'zelle': 'Zelle', 'other': 'Other',
  'pay2': '2nd Payment', 'due2': 'Due Date',
  'terms': 'Terms &amp; Conditions',
  'tm': [('Engagement.', 'The Borrower(s) hereby retain Biscayne Solutions Group, LLC ("BSG") to '
          'provide the services checked above. This agreement constitutes the entire understanding '
          'between the parties.'),
         ('Authorization.', 'Borrower(s) acknowledge that they have executed a Third-Party '
          'Authorization allowing BSG to communicate directly with their lender/servicer and to act '
          'on their behalf regarding the above-referenced loan.'),
         ('Client Cooperation.', 'Borrower(s) agree to provide all required documentation, '
          'information, and cooperation in a timely manner to allow BSG to perform the retained '
          'services. Failure to cooperate may result in termination of services.'),
         ('No Guarantee of Result.', 'Borrower(s) understand that BSG provides professional '
          'negotiation and consulting services but cannot guarantee a specific outcome. All results '
          "depend on the lender/servicer's policies, investor guidelines, and Borrower(s)' "
          'cooperation and financial circumstances.'),
         ('Communication.', 'Borrower(s) designate BSG as their exclusive point of contact with '
          'their lender/servicer and agree to direct all lender communications through BSG.'),
         ('Confidentiality.', 'BSG agrees to maintain all client information confidential in '
          'accordance with applicable laws and professional standards.'),
         ('Termination.', 'Either party may terminate this agreement with written notice. '
          'Borrower(s) remain responsible for fees incurred up to the date of termination.'),
         ('Governing Law.', 'This agreement shall be governed by the laws of the State of '
          'Florida.')],
  'ack': 'Acknowledgment &amp; Agreement',
  'ack_p': 'I/We, the undersigned Borrower(s)/Homeowner(s), acknowledge that I/we have read, '
           'understood, and agree to the terms and conditions of this Retainer Agreement. I/we '
           'confirm that all information provided is true and accurate to the best of my/our '
           'knowledge.',
  'b_sigs': 'Borrower / Homeowner Signatures', 'signature': 'Signature',
  'accept': 'Biscayne Solutions Group, LLC — Acceptance',
  'accept_p': 'The undersigned representative of Biscayne Solutions Group, LLC accepts this engagement '
              'and agrees to perform the services checked above in accordance with the terms set '
              'forth herein.',
  'auth_sig': 'Authorized Signature (BSG, LLC Manager)', 'printed': 'Printed Name',
  # --- tpa
  't_title': 'Third-Party Authorization to Release Information',
  'folio': 'Folio / Parcel ID',
  't1': '1. Authorization',
  't1_p1': 'I, {O} ("Borrower"), the borrower (or one of the borrowers) on the loan secured by the '
           'Property described above, <b>DO HEREBY AUTHORIZE AND INSTRUCT ANY AND ALL</b> of the following '
           'parties — mortgagees, note holders, loan servicers, sub-servicers, lenders, lienholders, '
           "homeowners' or condominium associations, taxing authorities, code enforcement agencies, "
           'and their respective attorneys, trustees, assignees, employees, and agents (collectively, '
           '"Information Holders") — <b>TO RELEASE ANY AND ALL INFORMATION REGARDING THE PROPERTY '
           'AND ANY OBLIGATIONS SECURED BY OR ATTACHED TO IT</b>, including without limitation: '
           'current unpaid balance, payment and delinquency history, escrow and impound balances, '
           'payoff quotes, reinstatement quotes, per-diem interest, loss-mitigation status, '
           'foreclosure timeline, sale dates, foreclosure attorney contact and case number, '
           'HOA/condominium assessments and estoppel figures, code enforcement liens, delinquent ad '
           'valorem taxes, tax certificate status, and any other matter reasonably related to the '
           'Property or the debts secured by it.',
  't1_p2': "Information may be released, at the Borrower's direction, to and at the discretion of:",
  'mm': 'Managing Member', 'fl_llc': 'a Florida limited liability company',
  'mail_addr': 'Mailing Address', 'tel': 'Telephone',
  't1_p3': 'who has been duly authorized and instructed by the Borrower to receive such information '
           'for the purpose of assisting the Borrower in evaluating options relating to the Property.',
  't2': '2. Scope — Information Only',
  't2_p1': 'This authorization is <b>LIMITED</b> to the release, receipt, and review of information. '
           'It does <b>NOT</b> authorize the person or entity named above to:',
  't2_li': ["accept or receive any payment on the Borrower's behalf;",
            'sign, execute, or submit any loan modification, forbearance, short-sale package, '
            "deed-in-lieu, bankruptcy filing, or other legal instrument on the Borrower's behalf;",
            'transfer, convey, encumber, or record any interest in the Property; or',
            'provide legal advice to the Borrower.'],
  't2_p2': "The person named above is not the Borrower's attorney and does not represent the "
           'Borrower in any legal capacity. The Borrower remains solely responsible for all decisions '
           'concerning the Property.',
  't3': '3. Reference Information',
  't3_lbl': ['Loan Number', 'Last 4 of SSN', 'Lender', 'Servicer', 'HOA / Condo Assoc.',
             'Foreclosure Attorney', 'Attorney Phone'],
  't4': '4. Duration and Revocation',
  't4_p1': 'This authorization is effective as of the date signed and <b>REMAINS IN EFFECT UNTIL '
           'REVOKED IN WRITING</b> by the Borrower. Written revocation may be delivered to any '
           'Information Holder and to Biscayne Solutions Group, LLC at the email or telephone above. '
           'Absent written revocation, Information Holders may rely on this authorization for all '
           'requests made in its name.',
  't4_p2': 'A photocopy, scanned copy, facsimile copy, or emailed PDF copy of this authorization '
           "bearing the Borrower's signature shall have the same force and effect as the original.",
  't5': '5. Borrower Signature',
  't5_signed': 'Signed this {D} day of {M}, 20{Y}',
  'owner_borrower': 'Borrower', 'print_name': 'Print Name', 'dob': 'Date of Birth',
  't5_note': 'Some servicers require date of birth to release information.',
  't7': '6. BSG Acknowledgment', 't7_p': 'Received and acknowledged by:',
  't7_cap': 'Managing Member, Biscayne Solutions Group, LLC &mdash; authorized to act on behalf of the '
            'Company',
  't_instr_h': 'INSTRUCTIONS TO INFORMATION HOLDERS:',
  't_instr': "This authorization is provided pursuant to the Borrower's rights under 15 U.S.C. "
             '&sect; 6802 (Gramm-Leach-Bliley) and 12 C.F.R. &sect; 1024.36 (RESPA Regulation X '
             '&mdash; requests for information from a borrower&rsquo;s agent). Please direct all '
             'responses to the contact information listed in Section 1. Thank you.',
 },
 'es': {
  'disclaimer': 'No somos un bufete de abogados. No somos un asesor de vivienda aprobado por HUD.',
  'r_title': 'Contrato y Acuerdo de Servicios',
  'r_sub': 'Acuerdo de Retenci&oacute;n y Carta de Contrataci&oacute;n para Servicios de '
           'Prevenci&oacute;n de Ejecuci&oacute;n Hipotecaria',
  'date': 'Fecha', 'name': 'Nombre', 'ph': 'Tel', 'email': 'Correo',
  'borrowers': 'Prestatario(s) / Propietario(s)',
  'prop_addr': 'Direcci&oacute;n de la Propiedad', 'county': 'Condado',
  'loan_no': 'N&uacute;mero de Pr&eacute;stamo',
  'lender': 'Prestamista / Administrador', 'case_no': 'Caso de Ejecuci&oacute;n N.&ordm;',
  'sale_date': 'Fecha de Subasta', 'debt': 'Deuda Actual / Liquidaci&oacute;n',
  'principal': 'Saldo de Capital',
  'services': 'Servicios Contratados',
  'svc': ['Modificaci&oacute;n de Pr&eacute;stamo / Plan de Indulgencia',
          'Escritura en Lugar de Ejecuci&oacute;n y Efectivo por Llaves',
          'BSG Compra la Propiedad en Cuesti&oacute;n',
          'Negociaci&oacute;n de Venta Corta (Short Sale)',
          'Prevenci&oacute;n de la Subasta de Ejecuci&oacute;n',
          'Otros Servicios (describa a continuaci&oacute;n)'],
  'desc': 'Descripci&oacute;n de Servicios y T&eacute;rminos Espec&iacute;ficos',
  'fees': 'Honorarios y Compensaci&oacute;n', 'fee_struct': 'Estructura de Honorarios',
  'deposit': 'Dep&oacute;sito Inicial', 'due': 'Fecha de Vencimiento del Pago',
  'pay_method': 'M&eacute;todo de Pago', 'wire': 'Transferencia Bancaria', 'zelle': 'Zelle',
  'other': 'Otro', 'pay2': '2.&ordm; Pago', 'due2': 'Fecha de Vencimiento',
  'terms': 'T&eacute;rminos y Condiciones',
  'tm': [('Contrataci&oacute;n.', 'Por el presente, el/los Prestatario(s) contrata(n) a Biscayne '
          'Solutions Group, LLC ("BSG") para prestar los servicios marcados anteriormente. Este '
          'acuerdo constituye el entendimiento completo entre las partes.'),
         ('Autorizaci&oacute;n.', 'El/los Prestatario(s) reconoce(n) que ha(n) firmado una '
          'Autorizaci&oacute;n a Terceros que permite a BSG comunicarse directamente con su '
          'prestamista/administrador y actuar en su nombre con respecto al pr&eacute;stamo antes '
          'referido.'),
         ('Cooperaci&oacute;n del Cliente.', 'El/los Prestatario(s) acuerda(n) proporcionar toda la '
          'documentaci&oacute;n, informaci&oacute;n y cooperaci&oacute;n requeridas de manera '
          'oportuna para permitir que BSG preste los servicios contratados. La falta de '
          'cooperaci&oacute;n puede resultar en la terminaci&oacute;n de los servicios.'),
         ('Sin Garant&iacute;a de Resultado.', 'El/los Prestatario(s) entiende(n) que BSG presta '
          'servicios profesionales de negociaci&oacute;n y consultor&iacute;a pero no puede '
          'garantizar un resultado espec&iacute;fico. Todos los resultados dependen de las '
          'pol&iacute;ticas del prestamista/administrador, las pautas del inversionista y la '
          'cooperaci&oacute;n y las circunstancias financieras del/de los Prestatario(s).'),
         ('Comunicaci&oacute;n.', 'El/los Prestatario(s) designa(n) a BSG como su punto de contacto '
          'exclusivo con su prestamista/administrador y acuerda(n) dirigir todas las comunicaciones '
          'del prestamista a trav&eacute;s de BSG.'),
         ('Confidencialidad.', 'BSG acuerda mantener confidencial toda la informaci&oacute;n del '
          'cliente de conformidad con las leyes aplicables y las normas profesionales.'),
         ('Terminaci&oacute;n.', 'Cualquiera de las partes puede terminar este acuerdo mediante '
          'notificaci&oacute;n por escrito. El/los Prestatario(s) contin&uacute;a(n) siendo '
          'responsable(s) de los honorarios incurridos hasta la fecha de terminaci&oacute;n.'),
         ('Ley Aplicable.', 'Este acuerdo se regir&aacute; por las leyes del Estado de Florida.')],
  'ack': 'Reconocimiento y Acuerdo',
  'ack_p': 'Yo/Nosotros, el/los abajo firmante(s) Prestatario(s)/Propietario(s), reconozco/'
           'reconocemos que he/hemos le&iacute;do, entendido y que acepto/aceptamos los '
           't&eacute;rminos y condiciones de este Acuerdo de Retenci&oacute;n. Confirmo/Confirmamos '
           'que toda la informaci&oacute;n proporcionada es verdadera y precisa seg&uacute;n mi/'
           'nuestro leal saber y entender.',
  'b_sigs': 'Firmas del Prestatario / Propietario', 'signature': 'Firma',
  'accept': 'Biscayne Solutions Group, LLC — Aceptaci&oacute;n',
  'accept_p': 'El representante abajo firmante de Biscayne Solutions Group, LLC acepta esta '
              'contrataci&oacute;n y se compromete a prestar los servicios marcados anteriormente '
              'de conformidad con los t&eacute;rminos aqu&iacute; establecidos.',
  'auth_sig': 'Firma Autorizada (Gerente de BSG, LLC)', 'printed': 'Nombre en Letra de Molde',
  't_title': 'Autorizaci&oacute;n a Terceros para Divulgar Informaci&oacute;n',
  'folio': 'Folio / N.&ordm; de Parcela',
  't1': '1. Autorizaci&oacute;n',
  't1_p1': 'Yo, {O} ("Prestatario"), el prestatario (o uno de los prestatarios) del '
           'pr&eacute;stamo garantizado por la Propiedad descrita anteriormente, <b>POR EL PRESENTE AUTORIZO E '
           'INSTRUYO A TODAS Y CADA UNA</b> de las siguientes partes &mdash; acreedores '
           'hipotecarios, tenedores de pagar&eacute;s, administradores de pr&eacute;stamos, '
           'subadministradores, prestamistas, titulares de gravamen, asociaciones de propietarios o '
           'de condominio, autoridades fiscales, agencias de cumplimiento de c&oacute;digos, y sus '
           'respectivos abogados, fiduciarios, cesionarios, empleados y agentes (en conjunto, '
           '"Tenedores de Informaci&oacute;n") &mdash; <b>A DIVULGAR TODA Y CUALQUIER '
           'INFORMACI&Oacute;N RELATIVA A LA PROPIEDAD Y A CUALQUIER OBLIGACI&Oacute;N GARANTIZADA '
           'POR ELLA O VINCULADA A ELLA</b>, incluyendo sin limitaci&oacute;n: saldo pendiente '
           'actual, historial de pagos y de morosidad, saldos de plica y de retenci&oacute;n, '
           'cotizaciones de liquidaci&oacute;n, cotizaciones de reinstalaci&oacute;n, inter&eacute;s '
           'diario, estado de mitigaci&oacute;n de p&eacute;rdidas, cronograma de la '
           'ejecuci&oacute;n, fechas de subasta, contacto del abogado de la ejecuci&oacute;n y '
           'n&uacute;mero de caso, cuotas de asociaci&oacute;n y cifras de estoppel, gravámenes por '
           'cumplimiento de c&oacute;digos, impuestos ad valorem morosos, estado de certificados '
           'fiscales, y cualquier otro asunto razonablemente relacionado con la Propiedad o con las '
           'deudas garantizadas por ella.',
  't1_p2': 'La informaci&oacute;n podr&aacute; divulgarse, por instrucci&oacute;n del Prestatario, '
           'a y a discreci&oacute;n de:',
  'mm': 'Miembro Gerente', 'fl_llc': 'una compa&ntilde;&iacute;a de responsabilidad limitada de Florida',
  'mail_addr': 'Direcci&oacute;n Postal', 'tel': 'Tel&eacute;fono',
  't1_p3': 'quien ha sido debidamente autorizado e instruido por el Prestatario para recibir dicha '
           'informaci&oacute;n con el fin de asistir al Prestatario en la evaluaci&oacute;n de '
           'opciones relacionadas con la Propiedad.',
  't2': '2. Alcance &mdash; Solo Informaci&oacute;n',
  't2_p1': 'Esta autorizaci&oacute;n se <b>LIMITA</b> a la divulgaci&oacute;n, recepci&oacute;n y '
           'revisi&oacute;n de informaci&oacute;n. <b>NO</b> autoriza a la persona o entidad '
           'nombrada anteriormente a:',
  't2_li': ['aceptar o recibir pago alguno en nombre del Prestatario;',
            'firmar, ejecutar o presentar cualquier modificaci&oacute;n de pr&eacute;stamo, '
            'indulgencia, paquete de venta corta, escritura en lugar de ejecuci&oacute;n, '
            'solicitud de bancarrota u otro instrumento legal en nombre del Prestatario;',
            'transferir, ceder, gravar o registrar cualquier inter&eacute;s en la Propiedad; o',
            'brindar asesor&iacute;a legal al Prestatario.'],
  't2_p2': 'La persona nombrada anteriormente no es el abogado del Prestatario y no representa al '
           'Prestatario en ninguna capacidad legal. El Prestatario sigue siendo el &uacute;nico '
           'responsable de todas las decisiones relativas a la Propiedad.',
  't3': '3. Informaci&oacute;n de Referencia',
  't3_lbl': ['N&uacute;mero de Pr&eacute;stamo', '&Uacute;ltimos 4 del SSN', 'Prestamista',
             'Administrador', 'Asoc. de Propietarios / Condominio', 'Abogado de la Ejecuci&oacute;n',
             'Tel&eacute;fono del Abogado'],
  't4': '4. Vigencia y Revocaci&oacute;n',
  't4_p1': 'Esta autorizaci&oacute;n entra en vigor en la fecha de su firma y <b>PERMANECE VIGENTE '
           'HASTA SER REVOCADA POR ESCRITO</b> por el Prestatario. La revocaci&oacute;n escrita '
           'podr&aacute; entregarse a cualquier Tenedor de Informaci&oacute;n y a Biscayne Solutions '
           'Group, LLC al correo electr&oacute;nico o tel&eacute;fono indicados arriba. En ausencia '
           'de revocaci&oacute;n escrita, los Tenedores de Informaci&oacute;n podr&aacute;n confiar '
           'en esta autorizaci&oacute;n para todas las solicitudes hechas en su nombre.',
  't4_p2': 'Una fotocopia, copia escaneada, copia por facs&iacute;mil o copia en PDF enviada por '
           'correo electr&oacute;nico de esta autorizaci&oacute;n que lleve la firma del Prestatario '
           'tendr&aacute; la misma fuerza y efecto que el original.',
  't5': '5. Firma del Prestatario',
  't5_signed': 'Firmado este d&iacute;a {D} de {M} de 20{Y}',
  'owner_borrower': 'Prestatario', 'print_name': 'Nombre en Letra de Molde',
  'dob': 'Fecha de Nacimiento',
  't5_note': 'Algunos administradores exigen la fecha de nacimiento para divulgar '
             'informaci&oacute;n.',
  't7': '6. Reconocimiento de BSG', 't7_p': 'Recibido y reconocido por:',
  't7_cap': 'Miembro Gerente, Biscayne Solutions Group, LLC &mdash; autorizado para actuar en nombre de '
            'la Compa&ntilde;&iacute;a',
  't_instr_h': 'INSTRUCCIONES PARA LOS TENEDORES DE INFORMACI&Oacute;N:',
  't_instr': 'Esta autorizaci&oacute;n se otorga conforme a los derechos del Prestatario bajo 15 '
             'U.S.C. &sect; 6802 (Gramm-Leach-Bliley) y 12 C.F.R. &sect; 1024.36 '
             '(RESPA Reglamento X &mdash; solicitudes de informaci&oacute;n del agente de un '
             'prestatario). Por favor dirija todas las respuestas a la informaci&oacute;n de '
             'contacto indicada en la Secci&oacute;n 1. Gracias.',
 },
}


def head(t):
    w = bsg_brand.mark_size(MARK_H)
    return (f'<header class="lh">'
            f'<img src="{bsg_brand.MONO_B64}" width="{w}" height="{MARK_H}" alt="{LLC}">'
            f'<div class="meta"><span class="co">{LLC}</span><br>'
            f'<b>{t["ph"]}</b>&nbsp;&nbsp;{SENDER["phone"]}<br>'
            f'<b>{t["email"]}</b>&nbsp;&nbsp;{SENDER["email"]}</div></header><div class="rule"></div>')


def foot(t):
    return (f'<footer class="sig"><span class="nm">{LLC}</span>'
            f'{SENDER["phone"]} &middot; {SENDER["email"]}'
            f'<br>{t["disclaimer"]}</footer>')


def page(title, body, t):
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>'
            f'<style>{CSS}</style></head><body><div class="sheet">'
            f'{head(t)}{body}{foot(t)}</div></body></html>')


def retainer(lang):
    t = T[lang]
    b = [f'<h1>{t["r_title"]}</h1>', f'<div class="sub">{t["r_sub"]}</div>',
         f'<div class="row"><span class="lbl">{t["date"]}</span> {L(240)}</div>',
         f'<h2>{t["borrowers"]}</h2>']
    for who in (C.get('borrower1', ''), C.get('borrower2', '')):
        b.append(f'<div class="row"><span class="lbl">{t["name"]}</span> {L(215, who)} '
                 f'<span class="lbl">{t["ph"]}</span> {L(110)} '
                 f'<span class="lbl">{t["email"]}</span> {L(165)}</div>')
    b += [f'<div class="row"><span class="lbl">{t["prop_addr"]}</span> {L(410, C.get("prop",""))}</div>',
          f'<div class="grid2"><div class="row"><span class="lbl">{t["county"]}</span> {L(160, C.get("county",""))}</div>'
          f'<div class="row"><span class="lbl">{t["loan_no"]}</span> {L(160, C.get("loan_no",""))}</div></div>',
          f'<div class="row"><span class="lbl">{t["lender"]}</span> {L(400, C.get("lender",""))}</div>',
          f'<div class="grid2"><div class="row"><span class="lbl">{t["case_no"]}</span> {L(140, C.get("case",""))}</div>'
          f'<div class="row"><span class="lbl">{t["sale_date"]}</span> {L(140, C.get("sale",""))}</div></div>',
          f'<div class="grid2"><div class="row"><span class="lbl">{t["debt"]}</span> $ {L(92, C.get("debt",""))}</div>'
          f'<div class="row"><span class="lbl">{t["principal"]}</span> $ {L(105, C.get("principal",""))}</div></div>',
          f'<h2>{t["services"]}</h2>', '<ul class="opts">']
    b += [f'<li>&#9744;&nbsp;&nbsp;{s}</li>' for s in t['svc']]
    b += ['</ul>', f'<div class="row" style="margin-top:10px">{L(600)}</div>',
          f'<h2>{t["desc"]}</h2>']
    b += [f'<div class="row">{L(600)}</div>' for _ in range(4)]

    b += ['<div class="pgb"></div>', f'<h2>{t["fees"]}</h2>',
          f'<div class="row"><span class="lbl">{t["fee_struct"]}</span> {L(380)}</div>',
          f'<div class="row">{L(600)}</div>',
          f'<div class="grid2"><div class="row"><span class="lbl">{t["deposit"]}</span> $ {L(115)}</div>'
          f'<div class="row"><span class="lbl">{t["due"]}</span> {L(100)}</div></div>',
          f'<div class="row"><span class="lbl">{t["pay_method"]}</span> &nbsp;&#9744;&nbsp; '
          f'{t["wire"]} &nbsp;&nbsp;&#9744;&nbsp; {t["zelle"]} &nbsp;&nbsp;&#9744;&nbsp; '
          f'{t["other"]}: {L(160)}</div>',
          f'<div class="grid2"><div class="row"><span class="lbl">{t["pay2"]}</span> $ {L(140)}</div>'
          f'<div class="row"><span class="lbl">{t["due2"]}</span> {L(140)}</div></div>',
          f'<h2>{t["terms"]}</h2>', '<ol class="terms">']
    b += [f'<li><span class="lead">{h}</span> {x}</li>' for h, x in t['tm']]
    b += ['</ol>']

    b += ['<div class="pgb"></div>', f'<h2>{t["ack"]}</h2>', f'<p>{t["ack_p"]}</p>',
          f'<h2>{t["b_sigs"]}</h2>']
    for _ in range(2):
        b.append(f'<div class="sigline">{L(380)} <span class="lbl">{t["date"]}</span> {L(130)}'
                 f'<div class="cap">{t["signature"]}</div></div>')
    b += [f'<h2 style="margin-top:30px">{t["accept"]}</h2>', f'<p>{t["accept_p"]}</p>',
          f'<div class="sigline">{L(380)} <span class="lbl">{t["date"]}</span> {L(130)}'
          f'<div class="cap">{t["auth_sig"]}</div></div>',
          f'<div class="sigline">{L(400)}<div class="cap">{t["printed"]}</div></div>',
          '<div class="box" style="margin-top:22px"><div class="mono">'
          f'<b>{LLC.upper()}</b><br>'
          f'{t["ph"].upper()}: {SENDER["phone"]}&nbsp;&nbsp;&middot;&nbsp;&nbsp;'
          f'{t["email"].upper()}: {SENDER["email"]}</div></div>']
    return page(f'BSG Retainer Agreement ({lang.upper()})', ''.join(b), t)


def tpa(lang):
    t = T[lang]
    b = [f'<h1>{t["t_title"]}</h1>', f'<div class="sub">{LLC}</div>',
         f'<div class="row"><span class="lbl">{t["date"]}</span> {L(200)}</div>',
         f'<div class="row"><span class="lbl">{t["prop_addr"]}</span> {L(400, C.get("prop",""))}</div>',
         f'<div class="row"><span class="lbl">{t["folio"]}</span> {L(250, C.get("folio",""))}</div>',

         f'<h2>{t["t1"]}</h2>',
         f'<p>{t["t1_p1"].replace("{O}", L(280, C.get("borrower1","")))}</p>',
         f'<p>{t["t1_p2"]}</p>',
         '<div class="box"><div class="mono">'
         f'{L(280)}<br><span style="color:#5A6472;font-size:8.5pt">{t["mm"]}</span><br><br>'
         f'<b>{LLC.upper()}</b>, {t["fl_llc"]}<br>'
         f'{t["mail_addr"]}:&nbsp; {L(260)}<br>'
         f'{t["tel"]}:&nbsp; {SENDER["phone"]}<br>'
         f'{t["email"]}:&nbsp; {SENDER["email"]}</div></div>',
         f'<p style="margin-top:10px">{t["t1_p3"]}</p>',

         f'<h2>{t["t2"]}</h2>', f'<p>{t["t2_p1"]}</p>',
         '<ol class="terms" type="a">' + ''.join(f'<li>{x}</li>' for x in t['t2_li']) + '</ol>',
         f'<p>{t["t2_p2"]}</p>',

         f'<h2>{t["t3"]}</h2>']
    t3_keys = ('loan_no', 'ssn4', 'lender', 'servicer', 'hoa', 'atty', 'atty_phone')
    for lbl, key in zip(t['t3_lbl'], t3_keys):
        b.append(f'<div class="row"><span class="lbl" style="display:inline-block;min-width:172px">'
                 f'{lbl}</span> {L(300, C.get(key, ""))}</div>')

    b += ['<div class="pgb"></div>',
          f'<h2>{t["t4"]}</h2>', f'<p>{t["t4_p1"]}</p>', f'<p>{t["t4_p2"]}</p>',
          f'<h2>{t["t5"]}</h2>',
          f'<p>{t["t5_signed"].replace("{D}", L(55)).replace("{M}", L(190)).replace("{Y}", L(38))}</p>',
          f'<div class="sigline">{L(400)}<div class="cap">{t["owner_borrower"]}</div></div>',
          f'<div class="row" style="margin-top:18px"><span class="lbl">{t["print_name"]}</span> {L(340, C.get("borrower1",""))}</div>',
          f'<div class="row"><span class="lbl">{t["dob"]}</span> {L(58)} / {L(58)} / {L(85)}</div>',
          f'<p class="note">{t["t5_note"]}</p>',

          f'<h2>{t["t7"]}</h2>', f'<p>{t["t7_p"]}</p>',
          f'<div class="sigline">{L(380)} <span class="lbl">{t["date"]}</span> {L(130)}'
          f'<div class="cap">{t["t7_cap"]}</div></div>',
          f'<div class="box" style="margin-top:24px"><div class="note">'
          f'<b>{t["t_instr_h"]}</b> {t["t_instr"]}</div></div>']
    return page(f'BSG Third-Party Authorization ({lang.upper()})', ''.join(b), t)


CASES = {
    'acosta': {
        'label': 'Acosta',
        'borrower1': 'Luis M. Acosta',
        'prop': '16298 90th St N, Loxahatchee, FL 33470',
        'county': 'Palm Beach',
        'folio': '00-40-42-13-00-000-6120',
        'case': '50-2025-CA-008509-XXXA-MB',
        'sale': '08/26/2026',
        'debt': '582,791.10',
        'principal': '483,063.00',
        'lender': 'A&amp;D Mortgage LLC',
        # deliberately NOT filled - not in the record, and a wrong number on a servicer request
        # gets the whole authorization rejected: loan_no, ssn4, servicer, hoa, atty, atty_phone,
        # borrower2 (the roll shows "ACOSTA LUIS M &" so a co-borrower exists, unnamed).
    },
}


def main():
    global C
    case_key = None
    for a in sys.argv[1:]:
        if a.startswith('--case='):
            case_key = a.split('=', 1)[1].strip().lower()
    if case_key:
        if case_key not in CASES:
            print(f'unknown case {case_key!r}; have: {", ".join(CASES)}'); return 1
        C = dict(CASES[case_key])
    os.makedirs(OUT, exist_ok=True)
    from playwright.sync_api import sync_playwright
    tag = f'_{C["label"]}' if C.get('label') else ''
    docs = []
    for lang in ('en', 'es'):
        sfx = lang.upper()
        docs += [(f'BSG_Retainer_Agreement{tag}_{sfx}', retainer(lang)),
                 (f'BSG_Third_Party_Authorization{tag}_{sfx}', tpa(lang))]
    made = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, html in docs:
            open(os.path.join(OUT, name + '.html'), 'w', encoding='utf-8').write(html)
            pg = b.new_page()
            pg.set_content(html)
            pg.wait_for_timeout(700)
            path = os.path.join(OUT, name + '.pdf')
            pg.pdf(path=path, format='Letter', print_background=True)
            pg.close()
            made.append(path)
            print(f'  {name}.pdf')
        b.close()
    # the pre-bilingual filenames would otherwise sit next to these and get mailed by mistake
    for stale in ('BSG_Retainer_Agreement', 'BSG_Third_Party_Authorization'):
        for ext in ('.pdf', '.html'):
            p_ = os.path.join(OUT, stale + ext)
            if os.path.exists(p_):
                os.remove(p_)
                print(f'  removed stale {stale}{ext}')
    print('->', OUT)
    return made


if __name__ == '__main__':
    main()
