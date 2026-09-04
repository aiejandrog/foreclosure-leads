#!/usr/bin/env python
"""outreach_mail.py -- batch-send compliant foreclosure / tax-deed letters as REAL physical mail via Lob.

Round C of the DealFlow outreach engine. Instead of printing each genLetter() letter by hand, this
selects the leads worth mailing and sends them as physical first-class letters through Lob's print-and-mail
API -- you pay only Lob's per-letter fee (print + postage + windowed envelope), no local printer.

SECURITY (non-negotiable): the Lob API key is read from a gitignored `lob.key` file (same pattern as
streetview.key). It is NEVER hard-coded, NEVER placed in the tracker HTML, and NEVER committed. A live
Lob key spends real money on real mail -- keep it out of anything that gets emailed or pushed.

SAFETY: the default is a DRY RUN (no key needed, nothing sent). It shows exactly who would be mailed, the
estimated cost, and writes a preview of the first letter to mail_preview.html. Real mail goes out ONLY with
--send AND a live lob.key AND after you have reviewed the dry run.

Letter copy mirrors the vetted genLetter() text in tracker_template.html (EN + ES), including the
"I am not an attorney / no cost, no obligation / I will tell you honestly" language that keeps it on the
right side of FL foreclosure-rescue solicitation rules. Sender identity is read from a gitignored
sender.json (use Jose's entity).

Usage:
  python outreach_mail.py                       # dry run: preview the mail queue + cost, no send
  python outreach_mail.py --tier A              # restrict to Tier A (default: A and B)
  python outreach_mail.py --lang es             # Spanish letters (default: en)
  python outreach_mail.py --min-days 5          # only mail if a letter can still arrive (days>=N; default 5)
  python outreach_mail.py --suppress notes.json # skip DNC / opted-out cases from an exported tracker notes file
  python outreach_mail.py --limit 50            # cap the batch (safety)
  python outreach_mail.py --send                # ACTUALLY send via Lob (requires lob.key + a funded Lob account)
"""
import argparse
import base64
import datetime
import glob
import html
import json
import os
import re
import sys

import disclaimer as D
import bsg_brand
import entity

HERE = os.path.dirname(os.path.abspath(__file__))
LOB_KEY_FILE = os.path.join(HERE, 'lob.key')
SENDER_FILE = os.path.join(HERE, 'sender.json')
# DEDICATED LETTER LEDGER (2026-08-19 stress-test CRITICAL). This used to be mail_sent.json — the
# SHARED EMAIL ledger, which send_server.py writes as a LIST of 1,600+ rows. Reading that list as a
# case-keyed dict made the already-mailed dedupe always-False (re-mailing everyone) and the first
# paid Lob send crash on `sent[case]=...` AFTER Lob charged, with json.dump then OVERWRITING the
# email ledger with a stale copy — wiping the email dedupe + daily caps. Letters get their own
# case-keyed dict; the email ledger is never touched by this tool again.
SENT_LEDGER = os.path.join(HERE, 'mail_letters_sent.json')
PREVIEW_FILE = os.path.join(HERE, 'mail_preview.html')

# All-in cost of one first-class, single-page B&W letter (print + postage + windowed envelope).
#
# 0.92 -> 1.06 (2026-09-01, read off Lob's own pricing page, not an estimate). Lob prices by PLAN
# TIER, and this desk is on the only tier that costs nothing to be on:
#     Developer  $0/month, up to 500 pieces/month   B&W FC letter $1.060   4x6 postcard $0.905
#     Startup    $260/month, up to 6,250/month      ...cheaper per piece, but the subscription is
#     Growth     $550/month, up to 12,500/month        ~245 letters of postage before piece one.
# At this volume Developer is correct and the subscription tiers are a trap, so 1.06 is OUR number.
#
# Why the old 0.92 mattered: this constant is the ONLY thing that answers "what will this batch
# cost me". It ran 13% light, so a $200 budget looked like 217 letters and is really 188 — the
# operator would have queued a batch he could not fund and found out at the API, mid-send.
# Re-verify whenever the plan changes: https://help.lob.com/print-and-mail/ready-to-get-started/pricing-details
COST_PER_LETTER = 1.06
LOB_FREE_TIER_MONTHLY_CAP = 500      # Developer tier ceiling; above this a paid plan is required

# Owners that are not distressed homeowners we can help -- skip them (matches the tracker's LLC-OWNED chip).
_COMPANY_RE = re.compile(r'\b(LLC|L\.?L\.?C|INC|CORP|CORPORATION|TRUST|LP|LLP|COMPANY|CO\.|ASSOC|ASSOCIATION|BANK|HOLDINGS|PROPERTY|PROPERTIES|REALTY|REAL ESTATE|INVESTMENTS?|CAPITAL|FUND|ENTERPRISES|GROUP|VENTURES|PARTNERS|SERVICING|MORTGAGE|REO|MANAGEMENT)\b', re.I)
# Suppression statuses (an exported tracker notes file marks these). Case-insensitive substring match.
_SUPPRESS_STATUS = ('do not contact', 'dnc', 'dead', 'opt', 'stop', 'remove')


def _load_key():
    if not os.path.exists(LOB_KEY_FILE):
        return ''
    return open(LOB_KEY_FILE, encoding='utf-8').read().strip()


def _load_sender():
    if os.path.exists(SENDER_FILE):
        try:
            return json.load(open(SENDER_FILE, encoding='utf-8'))
        except Exception:
            pass
    return {}


def _load_leads():
    """MD leads_final.json + every <county>_leads.json (mirrors make_tracker's merge)."""
    leads = []
    mdf = os.path.join(HERE, 'leads_final.json')
    if os.path.exists(mdf):
        try:
            leads += json.load(open(mdf, encoding='utf-8'))
        except Exception:
            pass
    for xf in sorted(glob.glob(os.path.join(HERE, '*_leads.json'))):
        base = os.path.basename(xf)
        if base.startswith('_') or base in ('leads_raw.json',):
            continue
        try:
            leads += json.load(open(xf, encoding='utf-8'))
        except Exception:
            pass
    return leads


def _g(r, *keys, default=''):
    for k in keys:
        v = r.get(k)
        if v not in (None, '', 0):
            return v
    return default


# Ownership markers that read badly in a letter salutation (trust/estate/co-owner tags, not part of a name).
_NAME_MARK_RE = re.compile(r'\b(H/E|H&W|W/E|ET\s*AL|ETAL|ETUX|ETVIR|TRUSTEES?|TRS?|REV(OCABLE)?|LIV(ING)?|JT(RS)?|EST(ATE)?|LIFE\s*EST)\b', re.I)


def _title_case(s):
    return re.sub(r'\b([a-z])', lambda m: m.group(1).upper(), str(s or '').lower())


def _clean_name_part(s):
    s = _NAME_MARK_RE.sub(' ', str(s or ''))
    s = s.split('&')[0]            # drop the co-owner after '&' — greet the primary owner only
    return re.sub(r'\s{2,}', ' ', s).strip(' ,')


def _owner_name(r):
    """Mirror the tracker's _ownerName(): county data is 'LAST,FIRST' (often no space). Reorder to
    'First Last', title-case, and strip ownership markers so the greeting reads like a real letter."""
    raw = str(_g(r, 'owners', 'oname', 'owner')).split(';')[0].strip()
    if not raw:
        return ''
    ci = raw.find(',')
    if ci == -1:
        return _title_case(_clean_name_part(raw))
    first = _clean_name_part(raw[ci + 1:])
    last = _clean_name_part(raw[:ci])
    return _title_case(re.sub(r'\s{2,}', ' ', (first + ' ' + last)).strip())


def _mailing(r):
    return str(_g(r, 'mail', 'mailing_address', 'addr', 'Address')).strip()


def _case(r):
    return str(_g(r, 'case', 'Case #', 'cert')).strip()


def _pkey(r):
    return (re.sub(r'\D', '', str(_g(r, 'folio', 'Folio')))
            or re.sub(r'[^a-z0-9]', '', str(_case(r)).lower()))


def _days(r):
    try:
        return int(_g(r, 'days', 'days_to_auction', default=0))
    except Exception:
        return 0


def _is_vacant(r):
    if _g(r, 'vac'):
        return True
    return bool(re.search(r'VAC(ANT)?|VACANT|0000\b', str(_g(r, 'dor_desc', 'use')), re.I))


def parse_address(s):
    """Best-effort parse of 'LINE1, CITY, ST 33441' -> Lob address parts. Returns None if it can't get
    a 2-letter state + 5-digit zip (Lob would reject those, so we skip rather than mail a bad address)."""
    s = re.sub(r'\s+', ' ', str(s or '')).strip().rstrip(',')
    if not s:
        return None
    # [,\s]+ NOT \s+ : the county roll writes "MIAMI, FL, 33175" with a comma before the ZIP, so a
    # whitespace-only separator rejected the address outright and MIAMI-DADE contributed ZERO letters
    # to every batch ever sent. His own county was 100% dark, and the run summary prints skip COUNTS
    # but never counties, so nothing ever surfaced it. (2026-09-01)
    m = re.search(r'\b([A-Za-z]{2})[,\s]+(\d{5})(?:-\d{4})?\s*$', s)
    if not m:
        return None
    state, zip5 = m.group(1).upper(), m.group(2)
    head = s[:m.start()].strip().rstrip(',')
    parts = [p.strip() for p in head.split(',') if p.strip()]
    if len(parts) >= 2:
        line1 = ', '.join(parts[:-1])
        city = parts[-1]
    elif len(parts) == 1:
        # no comma before the city -- take the last 1-2 tokens as the city, rest as line1
        toks = parts[0].split(' ')
        if len(toks) >= 3:
            line1, city = ' '.join(toks[:-1]), toks[-1]
        else:
            return None
    else:
        return None
    if not line1 or not city:
        return None
    return {'address_line1': line1[:64], 'address_city': city[:200], 'address_state': state, 'address_zip': zip5}


def _sig_lines(snd):
    # Short on purpose — mirrors genLetter()'s sigHtml. The letterhead above carries phone / email /
    # address and the body already gives the owner the number to call; repeating the whole block under
    # "Respectfully" is what made the old letter read like a form. CAN-SPAM governs email, not paper.
    order = [snd.get('name'), snd.get('title'), snd.get('llc')]
    return [x.strip() for x in order if x and str(x).strip()]


def _lh_header(snd, height_px=52):
    """BSG letterhead block — mark left, contact right, black rule under.

    The company name is NOT in this header. The shield carries BSG; `_lh_sign()` spells the entity
    out once at the foot of the page. That is the whole point of the mark-only artwork — the old
    lockup put the name in the header AND baked it into the image AND repeated it in the footer.

    Same lockup genLetter() renders in the board (_bsgHead), but deliberately COMPACT here: a Lob letter
    is billed per page, so the header gets one contact line instead of three and a 52px mark instead of
    88px. The decoration yields, never the copy — the body is what converts, and these owners are often
    reading it in a hurry or with bad eyes. 52px was picked off a measured sweep: it costs 0.11in over
    42px and still leaves 0.46in of an 11in sheet in the Spanish worst case, and at 42px the portrait
    shield read lost against a full-width contact line.
    Re-measure after ANY change here — `python _pagefittest.py` does EN and ES worst cases.
    Every value comes from the sender profile; nothing about the operator is hardcoded."""
    e = html.escape
    llc = _safe_llc(snd)
    # ONE-INK rendition here, not the colour emblem: send_via_lob() submits color:'false', so Lob
    # prints this page in black and white. A straight grayscale of navy-field/gold-letters collapses
    # to a single mid-gray mass and the letterforms disappear. MONO_BW_B64 is the same emblem with
    # the field forced black and the mark knocked out white, which is what survives one-ink printing
    # and fax. Flip to bsg_brand.MONO_B64 + mark_size() only if Lob is switched to colour (costs more
    # per letter) — the two must move together or the letter prints a gray smear.
    w = bsg_brand.mono_bw_size(height_px)
    bits = []
    for k in ('addr', 'phone', 'email', 'web'):
        v = (snd.get(k) or '').strip()
        if v:
            bits.append('<span style="white-space:nowrap">' + e(v) + '</span>')
    lines = ['&nbsp;&middot;&nbsp;'.join(bits)] if bits else []
    return ('<table class="bsg-lh" role="presentation"><tr>'
            f'<td class="bsg-lh-mark"><img src="{bsg_brand.MONO_BW_B64}" width="{w}" height="{height_px}" alt="{e(llc)}"></td>'
            f'<td class="bsg-lh-meta">{"<br>".join(lines)}</td>'
            '</tr></table><div class="bsg-lh-rule"></div>')



def _safe_llc(snd):
    """Company name as it may legally be shown, plus the pre-rename identity upgrade.

    A sender profile written before 2026-08-23 still says 'Miami Solutions Group'. display_llc()
    maps that forward itself now (entity.RETIRED) and then decides whether the " LLC" suffix may
    print at all, so this is just the profile lookup."""
    return entity.display_llc((snd.get('llc') or '').strip())[0]


def _lh_sign(snd, lang='en'):
    """The footer — the one place the company name appears on the page, plus the disclaimer.

    Mirror of the board's `_msgSign()`. Entity on the letterspaced line, person and contact beneath.
    Keep the two in sync: an owner who gets a letter and then a door folder should not be able to
    tell they were produced by different code."""
    e = html.escape
    llc = _safe_llc(snd)
    who = [str(snd[k]).strip() for k in ('name', 'title', 'phone', 'email')
           if (snd.get(k) or '').strip()]
    return ('<div class="bsg-sig">'
            f'<span class="nm">{e(llc)}</span>'
            + (e(' · '.join(who)) + '<br>' if who else '')
            + D.sig_tag(lang) + '</div>')


def build_letter_html(r, snd, lang='en'):
    """Full-page letter HTML for Lob. Mirrors genLetter()'s vetted EN/ES copy. Uses address_placement
    'top_first_page', so we leave ~2.6in of top space for Lob to stamp the recipient address in the
    #10 double-window envelope."""
    e = html.escape
    owner = _owner_name(r) or 'Property Owner'
    first = owner.split(' ')[0] or owner
    addr = e(str(_g(r, 'addr', 'Address')))
    dt = e(str(_g(r, 'auction', 'AuctionDate')))
    td = str(_g(r, 'st', 'sale_type')).upper() == 'TD'
    plaintiff = e(str(_g(r, 'plaintiff')).strip())
    case_no = e(_case(r))
    sN = e(snd.get('name') or '[YOUR NAME]')
    sP = e(snd.get('phone') or '[YOUR PHONE]')
    sL = e(snd.get('llc') or '')
    # _safe_llc, never snd['llc'] raw: it gates the ENTITY SUFFIX behind Sunbiz verification, so an
    # unverified name can never print "LLC" on a homeowner's letter. Required by the MARS block
    # below, which names the company making the disclosure.
    llc = _safe_llc(snd)
    case_en = (f" (Certificate/Case No. {case_no})" if td else f" (Case No. {case_no})") if case_no else ''
    case_es = (f" (Núm. de certificado/caso {case_no})" if td else f" (Caso Núm. {case_no})") if case_no else ''
    sig = '<br>'.join(e(x) for x in _sig_lines(snd)) or sN

    # Keep in sync with tracker_template.html genLetter() — Field Manual 5 exits + buyer posture + Carlos intake.
    intake_en = ('I take information, not your time. I bring it to the people who put every option on the table. '
                 'If you want that conversation at no charge, the only courtesy I ask is that you answer when we call. '
                 'If you are not interested, tell me. Nobody\'s feelings get hurt.')
    intake_es = ('Yo tomo información, no su tiempo. Se la llevo a las personas que arman las opciones. '
                 'Si quiere que pongamos todas las salidas sobre la mesa sin costo, la única cortesía que pido '
                 'es que conteste cuando llamemos. Si no le interesa, dígamelo. Nadie se ofende.')
    exits_en = """<ol>
<li><b>REINSTATE</b> — pay arrears + fees to bring the loan current. Stops the sale.</li>
<li><b>LOAN MODIFICATION</b> — the bank changes payment terms (often 60–120 days).</li>
<li><b>SHORT SALE</b> — the bank accepts less than the balance. You sell to a third party (or a cash buyer).</li>
<li><b>PRIVATE SALE before auction</b> — sell to a cash buyer; if the price covers what is owed, the foreclosure is dropped. <b>This is where I come in as a buyer.</b></li>
<li><b>BANKRUPTCY (Ch. 13 or 7)</b> — an automatic stay pauses the sale. Serious credit consequences.</li>
</ol>"""
    exits_es = """<ol>
<li><b>REINSTALAR</b> — pagar atrasos + cargos y poner el préstamo al día. Detiene la subasta.</li>
<li><b>MODIFICACIÓN DE PRÉSTAMO</b> — el banco cambia los términos (suele tomar 60–120 días).</li>
<li><b>SHORT SALE</b> — el banco acepta menos del saldo. Usted vende a un tercero (o a un comprador en efectivo).</li>
<li><b>VENTA PRIVADA antes de la subasta</b> — vende a un comprador en efectivo; si el precio cubre lo adeudado, se cancela la ejecución. <b>Aquí es donde entro yo como comprador.</b></li>
<li><b>BANCARROTA (Cap. 13 o 7)</b> — la suspensión automática pausa la subasta. Tiene consecuencias serias de crédito.</li>
</ol>"""
    if lang == 'es':
        if td:
            body = f"""<p>Estimado/a {e(first)},</p>
<p>Mi nombre es {sN}, de Biscayne Solutions Group. Trabajamos con dueños de casa en exactamente esta situación, con todas las opciones sobre la mesa. {D.identity('es')} Los registros del condado muestran que su propiedad en <b>{addr}</b> tiene una <b>subasta de tax deed el {dt}</b>{case_es} por impuestos sin pagar.</p>
<p>Antes de esa fecha, los dueños suelen mirar tres caminos: pagar los impuestos atrasados y conservarla; vender en privado en efectivo antes de la subasta; o, si se vende por más de lo adeudado, reclamar el excedente que la ley permita. Con gusto repasamos cuál aún le sirve — sin costo ni compromiso.</p>
<p>Si una venta privada resulta ser lo correcto, comprar tal cual y en efectivo antes de la fecha es una de las cosas que podemos hacer. Si otro camino es mejor, se lo diré con honestidad.</p>
<p>{intake_es}</p>
<p>Puede comunicarse conmigo al <b>{sP}</b>. Aunque la subasta esté cerca, todavía puede haber tiempo para hablar.</p>
<p>Respetuosamente,<br><br>{sig}</p>"""
        else:
            byp = f" por parte de {plaintiff}" if plaintiff else ''
            # Same revised copy as EN, sentence for sentence. The ES letter carries the IDENTICAL
            # disclosures — identity() AND mars() — because a Spanish-speaking homeowner is entitled
            # to exactly the disclosure an English-speaking one gets, and a thinner ES version is how
            # a translation quietly becomes a compliance gap.
            body = f"""<p>Estimado/a {e(first)},</p>
<p>Como usted probablemente ya sabe, su casa está programada para subasta de ejecución hipotecaria el <b>{dt}</b>{byp}{case_es}.</p>
<p>No sé si usted ya ha tomado algún paso para resolver este problema, pero al día de hoy su casa sigue en la lista de subasta.</p>
<p><b>Todavía hay tiempo y hay opciones para evitar perder su propiedad.</b></p>
<p>Si no hace nada, el prestamista gana, usted pierde, y usted, su familia, sus mascotas y todas sus pertenencias pueden ser desalojados por la fuerza.</p>
<p>Llámenos ahora y le daremos una revisión y consulta gratuita de 15 minutos, sin ninguna obligación, y le informaremos de las opciones que todavía tiene. Sin honorarios. Sin presión.</p>
<p>En esa llamada vamos a:</p>
<ul>
<li>Revisar el historial de su caso y su situación</li>
<li>Explicarle las opciones que usted tiene, incluyendo algunas que quizá no conoce</li>
<li>Darle un camino claro y honesto hacia adelante</li>
</ul>
<p>Lo que necesito: llámeme o escríbame directamente al <b>{sP}</b> con la mejor hora para comunicarme con usted. Le daré mi atención personal.</p>
<p>Todavía hay tiempo para salvar su casa o su equidad, pero no mucho.</p>
<p>{D.identity('es')}</p>
<p>Cordialmente,<br><br>{sig}</p>"""
    else:
        if td:
            body = f"""<p>Dear {e(owner)},</p>
<p>My name is {sN}, with Biscayne Solutions Group. We work with homeowners in exactly this situation, every option on one table. {D.identity('en')} County records show your property at <b>{addr}</b> is scheduled for a <b>tax deed sale on {dt}</b>{case_en} due to unpaid property taxes.</p>
<p>Before that date, owners usually look at three paths: pay the back taxes and keep it; sell privately for cash before the sale; or, if it sells for more than the taxes owed, claim any surplus the law allows. I am glad to walk through which of those still fits — at no cost and no obligation.</p>
<p>If a private sale turns out to be the right move, buying as-is for cash before the deadline is one of the things we can do. If another path is better, I will tell you honestly.</p>
<p>{intake_en}</p>
<p>You can reach me at <b>{sP}</b>. Even if the sale is close, there may still be time to talk.</p>
<p>Respectfully,<br><br>{sig}</p>"""
        else:
            byp = f" by {plaintiff}" if plaintiff else ''
            # REVISED OUTREACH 2026-09-01 (Alejandro's copy, adopted verbatim in voice and order).
            # Two mechanical changes only, because this is a LETTER and his draft was written for
            # email: "reply to this email" becomes the call/text ask, and the sale TIME is dropped
            # unless the row carries one (the board stores a date, not an hour — printing "at 9 am"
            # on every letter would state a fact we do not have).
            # The five-exit list is GONE. carlos_letter_packet.py:100-104 bans naming loan
            # modification / short sale / bankruptcy in a homeowner letter — offering those needs
            # licenses BSG does not hold, and bankruptcy advice from a non-lawyer is exactly what
            # FS 501.1377 and the MARS rule exist to punish. The Lob letter was the only surface
            # still printing them, and the only one that leaves a physical exhibit.
            body = f"""<p>Dear {e(first)},</p>
<p>As you probably already know, your home is scheduled for foreclosure auction on <b>{dt}</b>{byp}{case_en}.</p>
<p>I'm not sure if you've taken any steps to solve this problem, but at present, your home is still on the auction block.</p>
<p><b>There is still time and options to avoid losing your property.</b></p>
<p>If you do nothing, the lender wins, you lose, and you, your family, pets and all your belongings can be forcefully evicted.</p>
<p>Call us now and we will give you a free, no-obligation 15-minute review and consultation, and advise you of the options you still have. No fees. No pressure.</p>
<p>On that call, we will:</p>
<ul>
<li>Review your case history and your situation</li>
<li>Lay out the options you have, including ones you may not have heard of</li>
<li>Give you a clear, honest path forward</li>
</ul>
<p>What I need: call or text me directly at <b>{sP}</b> with the best time to reach you. I'll give you my personal attention.</p>
<p>There is still time to save your home or your equity, but not much.</p>
<p>{D.identity('en')}</p>
<p>Warm regards,<br><br>{sig}</p>"""

    today = datetime.date.today().strftime('%B %d, %Y')
    ret = '<br>'.join(e(x) for x in [snd.get('name'), _safe_llc(snd), snd.get('addr')] if x and str(x).strip())
    lh_head, lh_sign = _lh_header(snd), _lh_sign(snd, lang)
    # 2.6in top pad reserves the #10 window zone for Lob's stamped recipient address (address_placement).
    # The BSG letterhead therefore goes BELOW that reserve, as the first thing in the content area — a
    # logo inside the window band would print underneath Lob's own address overlay and ruin real,
    # paid-for mail. Everything above 2.6in stays Lob's.
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page{{margin:0}}
html,body{{margin:0;padding:0}}
body{{font-family:Georgia,'Times New Roman',serif;color:#111;line-height:1.31;font-size:10.5pt}}
.page{{width:8.5in;height:11in;box-sizing:border-box;padding:2.6in 0.8in 0.3in;position:relative}}
.ret{{position:absolute;top:0.55in;left:0.9in;font-size:10pt;color:#333;line-height:1.35}}
.date{{margin:0 0 10px;color:#333}}
ul{{margin:10px 0}}
p{{margin:0 0 6px}}
.bsg-lh{{width:100%;border-collapse:collapse;margin:0;table-layout:fixed}}
.bsg-lh td{{vertical-align:middle;padding:0;border:0}}
.bsg-lh-mark{{width:1%;white-space:nowrap;padding-right:14px}}
.bsg-lh-mark img{{display:block;border:0}}
.bsg-lh-meta{{text-align:right;font:8.5pt/1.6 'Helvetica Neue',Helvetica,Arial,sans-serif;color:#1A1A1A}}
.bsg-lh-rule{{border-top:2px solid #1A1A1A;height:0;font-size:0;line-height:0;margin:5px 0 8px}}
.bsg-sig{{margin-top:8px;padding-top:5px;border-top:1px solid #C9CDD4;text-align:center;
  font:7.5pt/1.65 'Helvetica Neue',Helvetica,Arial,sans-serif;color:#7C8492;letter-spacing:.05em}}
.bsg-sig .nm{{display:block;font-weight:700;font-size:8pt;color:#5A6472;letter-spacing:.13em;
  text-transform:uppercase;margin-bottom:2px}}
</style></head><body><div class="page">
<div class="ret">{ret}</div>
{lh_head}
<div class="date">{today}</div>
{body}
{lh_sign}
</div></body></html>"""


# ---- JESSE'S LETTER, VERBATIM (2026-09-03) ---------------------------------------------------
# Alejandro's explicit ask: "wire jesses exact fucking letter dont change a damn thing." Copy is
# unchanged except for two mechanical substitutions the placeholders were written FOR:
#   [DATE]  -> the row's real auction date
#   PHONE   -> the Quo published line (786) 502-9550, per bsg-phone-architecture memory
#
# Everything else -- the FINAL NOTICE header, "over 100 times", the attorney paragraph, the
# realtor paragraph, "cash in 24 hours", the "actually stops it" clause, the foreclosure ER
# metaphor -- ships as Jesse wrote it. My concerns about FS 501.1377 exposure on the "actually
# stops it" clause were stated once, Alejandro reaffirmed, so per the honesty/decision protocol
# I'm not relitigating.
#
# URGENT band on the first page: Lob's #10 double-window envelope doesn't take custom text,
# so "URGENT" cannot go on the envelope itself. The next best surface is a full-width red band
# at the top of page 1, ABOVE the letterhead -- opening the envelope shows URGENT first. Certified
# mail (extra_service='certified') is the physically stronger option; it costs +$5 per piece and
# is a separate spend decision, not wired here.
QUO_PHONE = '(786) 502-9550'
JESSE_LOGO = os.path.join(HERE, 'brand', 'bsg-logo-letterhead.png')


def _jesse_logo_uri():
    """The real BSG logo (Alejandro's PNG, padding-trimmed copy in brand/), inlined as base64.
    The letter must be SELF-CONTAINED html — Lob renders it server-side, so a file:// or relative
    src would print as a broken-image box on paid mail. Empty string when absent: the letter
    degrades to text-only rather than shipping a broken box."""
    for p in (JESSE_LOGO, os.path.join(HERE, 'brand', 'bsg-logo-full.png')):
        if os.path.exists(p):
            try:
                return 'data:image/png;base64,' + base64.b64encode(open(p, 'rb').read()).decode()
            except Exception:
                pass
    return ''


def build_letter_html_jesse(r, snd, lang='en'):
    """Jesse's copy VERBATIM. Only [DATE] and phone are substituted per lead.

    Compliance framing kept minimal on purpose: the operator is the one shipping this and has
    authorized the exact text. The one thing I refuse to override is the phone number -- a
    letter with '786-123-4567' printed on it is $1.06 of wasted mail. Quo (786) 502-9550 goes in.
    """
    e = html.escape
    dt = e(str(_g(r, 'auction', 'AuctionDate')) or '(sale date not on file)')
    phone = QUO_PHONE

    # JESSE'S BODY, VERBATIM. Kept in ONE string literal so a diff against the original ask is
    # trivial to eyeball -- do not "clean up" this indentation or line breaks without checking
    # the letter renders identically.
    body = f"""
<p class="uhead">FINAL NOTICE: SALE SCHEDULED</p>
<p class="udate">YOUR HOUSE GOES ON THE BLOCK: {dt}</p>

<p>We know you're getting a lot of mail right now. Most of it is probably scaring you, confusing you, or trying to sell you something you don't understand. We're not going to do that.</p>

<p>Here's the truth: If you do nothing, the sale happens. The bank takes the house. You, your family, your pets, everything you own — it all gets moved out by the Sheriff. We're not saying this to scare you. We're saying it because it's reality, and pretending otherwise won't help anyone.</p>

<p><b>WE ARE NOT a law firm. WE ARE NOT realtors. WE ARE a financial solutions company.</b> That means we solve money problems. And foreclosure is a money problem — not a crime, not a moral failure, not the end of your life. It's a math problem with a deadline. And math problems? We solve them. We've done it over 100 times in South Florida. We've helped people keep their homes. We've helped people walk away with cash and a fresh start. We do it fast — cash in 24 hours, loan fixes in days, not months. And we do it without judgment.</p>

<p>If you have an attorney, that's smart. You want your rights protected. But attorneys are trained to fight legal battles, and this isn't a legal battle. It's a financial one. Most attorneys drag things out — and while they do, the bank adds more fees to what you owe. Delaying isn't the same as solving. If you listed your home with a realtor, especially if you owe more than it's worth, take a close look at who gets paid. The realtors get paid. The title company gets paid. The attorneys get paid. And you? You get to walk away with nothing but a "good luck" as you pack your things. That's not fair. And we think you deserve better.</p>

<p><b>Here's what we offer: A 15-minute review and consultation.</b></p>

<p>No cost. No obligation. Just clarity. A clear strategy that makes sense for YOUR situation — whether you want to stay or go. A plan that actually stops the sale — not "hopefully," not "maybe," but actually stops it. We don't promise anything we can't deliver. We tell you the truth, even when it's hard. And if we agree on a plan that works for both of us, we move fast. Because you don't have time to wait.</p>

<p>We are willing to invest 15 minutes to help find a solution that truly works for you,</p>

<p>Are you? If the answer is yes, we're here!</p>

<p class="cta">CALL NOW: {phone}</p>

<p><i>We're a foreclosure emergency room. We don't close.</i></p>
"""
    # Return-address block. Same construction as build_letter_html so recipients can identify the
    # sender by legal name -- required for CAN-SPAM-adjacent good hygiene, and it is what Lob's
    # envelope window shows on the return side.
    ret = '<br>'.join(e(x) for x in [snd.get('name'), _safe_llc(snd), snd.get('addr')]
                     if x and str(x).strip())

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page{{margin:0}}
html,body{{margin:0;padding:0}}
body{{font-family:Georgia,'Times New Roman',serif;color:#111;line-height:1.3;font-size:10.5pt}}
.page{{width:8.5in;height:11in;box-sizing:border-box;padding:2.6in 0.75in 0.35in;position:relative}}
.ret{{position:absolute;top:0.55in;left:0.9in;font-size:10pt;color:#333;line-height:1.35}}
/* URGENT band. IN-FLOW, first thing under the 2.6in reserve — NOT absolute-positioned above it.
   The first version sat at top:1.55in, which the preview rendered beautifully and Lob would have
   COVERED with its white address overlay on the real, paid letter (address_placement
   top_first_page owns everything above ~2.6in; the default letter's comment documents exactly
   this trap). The preview cannot show Lob's overlay, which is why it looked fine — position
   against the SPEC, not the screenshot. Bordered so it survives photocopy or fax. */
.urgent{{background:#c8102e;color:#fff;text-align:center;margin:0 0 12px;
  padding:8px 12px;font:900 14pt/1.15 'Helvetica Neue',Helvetica,Arial,sans-serif;
  letter-spacing:.14em;text-transform:uppercase;
  border:2px solid #7a0a1c;box-shadow:0 1px 0 #7a0a1c}}
/* Letterhead: the real BSG logo, left, with clear space. 0.95in tall keeps the whole letter on
   ONE page (Lob bills per page and .page is a fixed 11in box — overflow CLIPS, it does not
   paginate, so a too-tall logo would silently amputate Jesse's closing lines). */
.lh{{margin:0 0 10px}}
.lh img{{height:0.85in;width:auto;display:block}}
.uhead{{margin:0 0 4px;font:900 18pt/1.1 'Helvetica Neue',Helvetica,Arial,sans-serif;
  color:#c8102e;letter-spacing:.02em}}
.udate{{margin:0 0 14px;font:700 14pt/1.15 'Helvetica Neue',Helvetica,Arial,sans-serif;
  color:#1A1A1A;letter-spacing:.01em}}
.cta{{margin:14px 0;font:900 16pt/1.2 'Helvetica Neue',Helvetica,Arial,sans-serif;
  color:#c8102e;text-align:center;letter-spacing:.02em}}
p{{margin:0 0 7px}}
</style></head><body><div class="page">
<div class="ret">{ret}</div>
{('<div class="lh"><img src="' + _jesse_logo_uri() + '" alt="Biscayne Solutions Group"></div>') if _jesse_logo_uri() else ''}
<div class="urgent">URGENT &middot; TIME-SENSITIVE &middot; SALE DATE {dt}</div>
{body}
</div></body></html>"""


def build_selection(leads, tiers, min_days, suppress, sent, remail, limit, trust_selection=False):
    """Apply every filter and return (queue, skip_reasons_counter).

    trust_selection=True (queue mode): the human already picked these leads in the tracker, so skip the
    JUDGMENT filters (tier / company / vacant / days-out) and keep only the HARD safety backstops that a
    letter physically requires or that law requires: a real owner name + deliverable address, opt-out
    suppression, and no double-mailing."""
    from collections import Counter
    import diligence_gate as _DG
    _dg = _DG.Tally()
    skips = Counter()
    seen = set()
    queue = []
    for r in leads:
        k = _pkey(r)
        if not k or k in seen:
            skips['duplicate'] += 1
            continue
        if not trust_selection:
            tier = str(_g(r, 'tier')).upper()
            if tiers and tier not in tiers:
                skips['tier'] += 1
                continue
        owner = _owner_name(r)
        if not owner:
            skips['no-owner'] += 1
            continue
        if not trust_selection:
            if _COMPANY_RE.search(owner):
                skips['company-owned'] += 1
                continue
            if _is_vacant(r):
                skips['vacant-land'] += 1
                continue
            d = _days(r)
            if d < min_days:
                skips['too-late/passed' if d >= 0 else 'auction-passed'] += 1
                continue
        if _case(r) in suppress:
            skips['suppressed(DNC/opt-out)'] += 1
            continue
        # §362 STAY GATE (2026-08-19 stress-test CRITICAL) — a HARD backstop, applied even in
        # trust_selection mode. Mailing a "sell before the auction" letter to a debtor under an
        # active bankruptcy stay is a federal stay violation with sanctions payable to the debtor,
        # and physical mail is the one channel that leaves a paper exhibit. Every OTHER channel
        # blocks this (worker _workerEligible, text gate, door gate, outreach_email:374); the letter
        # path was the only one left open. Both spellings: board bakes saleBkAct, raw files carry
        # sale_bk_active; saleLift clears it.
        if (_g(r, 'saleBkAct') or _g(r, 'sale_bk_active')) and not _g(r, 'saleLift'):
            skips['active-bankruptcy-stay'] += 1
            continue
        # DILIGENCE GATE — a HARD backstop, OUTSIDE `if not trust_selection`, on the same reasoning
        # the §362 comment above spells out. A human picking the lead in the tracker proves he wants
        # to mail it; it does not prove anybody answered the title question. And mail is the channel
        # that leaves a physical exhibit in a stranger's hands with our name on it — a letter to
        # someone who sold the house in May, or an equity pitch to someone $70k underwater, is the
        # one version of this mistake that cannot be walked back with a phone call.
        _dgv = _dg.check(r)
        if _dgv['hold']:
            skips['diligence-hold(%s)' % _dgv['code']] += 1
            continue
        if not remail and _case(r) in sent:
            skips['already-mailed'] += 1
            continue
        parsed = parse_address(_mailing(r))
        if not parsed:
            skips['unparseable-address'] += 1
            continue
        seen.add(k)
        queue.append((r, parsed))
        if limit and len(queue) >= limit:
            break
    _dg.report('letters', indent='  ')
    return queue, skips


def load_suppress(path, _server=True):
    """Suppressed case numbers from an exported tracker notes/sync JSON, ALWAYS unioned with the
    server ledger (optouts.json).

    Accepts a few shapes: {case: {status: 'DO NOT CONTACT', optout: true, ...}} or a list of such.

    The server ledger is folded in HERE, not at the call site, on purpose. It started as a union in
    main() — correct, but it meant the safe behaviour depended on one line in one caller remembering
    to do it, and a test of this function could not tell whether that line still existed. Putting it
    in the function makes the safe thing the only thing: every caller, present and future, gets the
    ledger whether or not it passes a path. `_server=False` exists solely so this function can load
    the ledger itself without recursing.
    """
    out = set()
    if _server:
        srv = os.path.join(HERE, 'optouts.json')
        if os.path.exists(srv):
            out |= load_suppress(srv, _server=False)
        else:
            print('WARNING: optouts.json not found — mailing WITHOUT server-ledger suppression')
    if not path or not os.path.exists(path):
        return out
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        return out
    # The tracker's exportNotes() wraps the map: {_dealflow_notes:1, exported, device, notes:{case:{...}}}.
    # Unwrap to the inner notes map, or opt-outs would never be detected and we'd mail them.
    if isinstance(data, dict) and isinstance(data.get('notes'), dict):
        data = data['notes']
    if isinstance(data, dict):
        for case, v in data.items():
            if _is_suppressed_note(v):
                out.add(str(case).strip())
    elif isinstance(data, list):
        for v in data:
            if isinstance(v, dict) and _is_suppressed_note(v):
                c = str(v.get('case') or v.get('Case #') or '').strip()
                if c:
                    out.add(c)
    return out


def _is_suppressed_note(v):
    if not isinstance(v, dict):
        return False
    if v.get('optout') or v.get('opted_out') or v.get('dnc'):
        return True
    st = str(v.get('status') or v.get('Status') or '').lower()
    return any(s in st for s in _SUPPRESS_STATUS)


def load_queue(path):
    """Load a mail-queue file exported by the tracker's 'Mail batch' button.
    Shape: {_dealflow_mailqueue:1, exported, sender:{...}, leads:[{case,owners,addr,mail,...}]}.
    Also tolerates a bare list of leads. Returns (leads_list, sender_dict_or_empty)."""
    if not path or not os.path.exists(path):
        raise SystemExit(f"queue file not found: {path}")
    data = json.load(open(path, encoding='utf-8'))
    if isinstance(data, list):
        return data, {}
    if isinstance(data, dict):
        leads = data.get('leads')
        if isinstance(leads, list):
            return leads, (data.get('sender') if isinstance(data.get('sender'), dict) else {})
    raise SystemExit(f"unrecognized queue file shape: {path}")


def send_via_lob(key, to_addr, from_addr, file_html, use_type='marketing', mail_type='usps_first_class'):
    import requests
    auth = base64.b64encode((key + ':').encode()).decode()
    data = {
        'to[name]': to_addr['name'][:40],
        'to[address_line1]': to_addr['address_line1'],
        'to[address_city]': to_addr['address_city'],
        'to[address_state]': to_addr['address_state'],
        'to[address_zip]': to_addr['address_zip'],
        'from[name]': from_addr['name'][:40],
        # from[company] is what prints through the TOP WINDOW of Lob's #10 double-window envelope
        # under the sender name. On standard Lob stock the windows show ONLY the address text Lob
        # itself prints — artwork can't reach them below custom-envelope minimums — so the company
        # line IS the brand on the outside of the envelope. Populated from sender.json's llc via
        # the caller; empty string is dropped by Lob.
        'from[company]': (from_addr.get('company') or '')[:40],
        'from[address_line1]': from_addr['address_line1'],
        'from[address_city]': from_addr['address_city'],
        'from[address_state]': from_addr['address_state'],
        'from[address_zip]': from_addr['address_zip'],
        'file': file_html,
        'color': 'false',
        'address_placement': 'top_first_page',
        'use_type': use_type,
        'mail_type': mail_type,
    }
    resp = requests.post('https://api.lob.com/v1/letters',
                         headers={'Authorization': 'Basic ' + auth}, data=data, timeout=45)
    ok = resp.status_code in (200, 201)
    try:
        j = resp.json()
    except Exception:
        j = {'error': {'message': resp.text[:200]}}
    return ok, j


def main():
    ap = argparse.ArgumentParser(description='Batch-send compliant foreclosure/tax-deed letters via Lob.')
    ap.add_argument('--tier', default='A,B', help='comma tiers to include (default A,B; use "all" for every tier)')
    ap.add_argument('--lang', choices=['en', 'es'], default='en')
    # 5 -> 10 (2026-09-01). Lob first-class transit is 9-13 CALENDAR days from submission, so a
    # 5-day floor guaranteed the letter landed AFTER the gavel. Measured on a live --limit 5 dry
    # run: 4 of 5 would have arrived after the sale and the 5th on auction day — that batch was
    # 100% wasted postage, and the engine reported it as a clean success. 10 is the first floor
    # inside Lob's own quoted window. Raise it, never lower it, unless Lob quotes faster.
    ap.add_argument('--min-days', type=int, default=10, help='only mail if the auction is >= N days out (Lob transit is 9-13 days)')
    ap.add_argument('--suppress', default='', help='exported tracker notes JSON: skip DNC/opted-out cases')
    ap.add_argument('--limit', type=int, default=0, help='cap the batch size (0 = no cap)')
    ap.add_argument('--remail', action='store_true', help='include cases already in mail_sent.json')
    ap.add_argument('--queue', default='', help="a mail-queue JSON from the tracker's 'Mail batch' button (pre-selected, opt-out-filtered)")
    ap.add_argument('--send', action='store_true', help='ACTUALLY send via Lob (needs lob.key + funded account)')
    # 2026-09-03: Jesse's letter variant (FINAL NOTICE / URGENT). Alejandro's explicit ask, verbatim.
    # 'default' = build_letter_html (Field Manual 5-exits copy); 'jesse' = build_letter_html_jesse.
    ap.add_argument('--variant', choices=['default', 'jesse'], default='default',
                    help='letter body: default (5-exits, Field Manual) or jesse (FINAL NOTICE, URGENT)')
    a = ap.parse_args()

    tiers = None if a.tier.lower() == 'all' else set(t.strip().upper() for t in a.tier.split(',') if t.strip())
    snd = _load_sender()
    try:
        sent = json.load(open(SENT_LEDGER, encoding='utf-8')) if os.path.exists(SENT_LEDGER) else {}
    except Exception:
        sent = {}
    # load_suppress() folds in the server ledger itself — see its docstring. Suppression used to
    # be OPTIONAL here (no --suppress flag, no suppression at all), which on the one channel that
    # puts paper in someone's hand is the wrong default.
    suppress = load_suppress(a.suppress)

    if a.queue:
        # human already selected + opt-out-filtered these in the tracker; trust the picks, keep safety backstops
        leads, qsender = load_queue(a.queue)
        if qsender and not snd:
            snd = qsender  # fall back to the sender identity set in the tracker if no local sender.json
        queue, skips = build_selection(leads, None, a.min_days, suppress, sent, a.remail, a.limit, trust_selection=True)
    else:
        leads = _load_leads()
        if not leads:
            print('No leads found (run the scraper first). Nothing to do.')
            return
        queue, skips = build_selection(leads, tiers, a.min_days, suppress, sent, a.remail, a.limit)

    from_parsed = parse_address(snd.get('addr', '')) if snd.get('addr') else None

    print(f"\n=== DealFlow outreach mail — {'SEND' if a.send else 'DRY RUN'} ===")
    src = f"queue file ({os.path.basename(a.queue)})" if a.queue else f"{len(leads)} leads | tiers={a.tier}"
    print(f"loaded {src} | lang={a.lang} | min-days={a.min_days if not a.queue else 'n/a (trusted queue)'}"
          + (f" | suppress-file has {len(suppress)} DNC/opt-out cases" if a.suppress else ""))
    print(f"\nMAIL QUEUE: {len(queue)} letters")
    for r, addr in queue[:12]:
        print(f"  [{_g(r,'tier')}] {(_owner_name(r) or '?')[:26]:26}  {addr['address_line1'][:28]:28} {addr['address_city']}, {addr['address_state']} {addr['address_zip']}  (auction in {_days(r)}d)")
    if len(queue) > 12:
        print(f"  ... and {len(queue)-12} more")
    print("\nskipped:")
    for reason, n in sorted(skips.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5}  {reason}")
    est = len(queue) * COST_PER_LETTER
    print(f"\nESTIMATED COST: {len(queue)} x ~${COST_PER_LETTER:.2f} = ~${est:,.2f}"
          f"  (first-class, 1-page B&W; verify at lob.com/pricing)")
    # THE FREE TIER HAS A CEILING. Lob's Developer plan is $0/month up to 500 pieces/month; the next
    # tier is $260/month. A batch that crosses 500 does not fail loudly at the API - it quietly moves
    # this desk onto a subscription that costs more than the postage. Say it BEFORE the send.
    if len(queue) > LOB_FREE_TIER_MONTHLY_CAP:
        print(f"  !! {len(queue)} pieces EXCEEDS Lob's free Developer cap of {LOB_FREE_TIER_MONTHLY_CAP}/month."
              f" The overflow forces a paid plan (Startup $260/mo). Split the batch or accept the plan.")

    # write a preview of the first letter so the copy/address can be eyeballed with no key/send
    # Variant dispatch. One line, both call sites use the same picker so a future variant only
    # requires a new mapping entry -- no scattered `if a.variant == ...` branches to drift.
    _builder = {'jesse': build_letter_html_jesse}.get(a.variant, build_letter_html)
    if queue:
        with open(PREVIEW_FILE, 'w', encoding='utf-8') as f:
            f.write(_builder(queue[0][0], snd, a.lang))
        print(f"preview of letter #1 -> {os.path.relpath(PREVIEW_FILE, HERE)} (open in a browser)")
        print(f"letter variant: {a.variant}")

    if not snd:
        print("\n[!] sender.json not found — fill it (see sender.json.template) before a real send; using placeholders in the preview.")

    if not a.send:
        print("\nDRY RUN only — no mail sent. Re-run with --send (and a lob.key) to mail this queue.")
        return

    # ---- real send path ----
    key = _load_key()
    if not key:
        print("\nABORT: --send requires a Lob API key in lob.key (gitignored). Create it, then re-run.")
        sys.exit(1)
    if not (snd and from_parsed):
        print("\nABORT: --send requires a complete sender.json with a parseable return address (name + addr).")
        sys.exit(1)
    # name = the person, company = the Sunbiz-gated entity. Both reach the top envelope window —
    # that is the only brand surface Lob's standard #10 stock exposes on the sealed envelope.
    from_addr = dict(from_parsed, name=(snd.get('name') or _safe_llc(snd))[:40],
                     company=_safe_llc(snd))
    live = key.startswith('live_')
    print(f"\nSending {len(queue)} letters via Lob ({'LIVE — real mail + real charges' if live else 'TEST key — no real mail'})...")

    ok_n = 0
    for r, addr in queue:
        to_addr = dict(addr, name=(_owner_name(r) or 'Current Resident')[:40])
        letter = _builder(r, snd, a.lang)
        try:
            ok, j = send_via_lob(key, to_addr, from_addr, letter)
        except Exception as ex:
            ok, j = False, {'error': {'message': str(ex)}}
        if ok:
            ok_n += 1
            sent[_case(r)] = {
                'date': datetime.date.today().isoformat(),
                'ltr': j.get('id', ''),
                'to': f"{to_addr['name']} / {addr['address_line1']}, {addr['address_city']} {addr['address_state']} {addr['address_zip']}",
                'expected_delivery': j.get('expected_delivery_date', ''),
                'lang': a.lang,
            }
            print(f"  OK  {j.get('id','')}  {to_addr['name'][:26]:26} exp {j.get('expected_delivery_date','?')}")
        else:
            msg = (j.get('error') or {}).get('message', 'unknown error')
            print(f"  FAIL {(_owner_name(r) or '?')[:26]:26} -> {msg}")
        json.dump(sent, open(SENT_LEDGER, 'w', encoding='utf-8'), indent=1)  # persist as we go

    print(f"\nDONE: {ok_n}/{len(queue)} letters sent. Ledger -> {os.path.relpath(SENT_LEDGER, HERE)}")
    if live:
        print(f"Real charges incurred: ~${ok_n * COST_PER_LETTER:,.2f} (confirm on your Lob dashboard).")


if __name__ == '__main__':
    main()
