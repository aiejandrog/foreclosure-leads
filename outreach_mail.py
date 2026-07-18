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

HERE = os.path.dirname(os.path.abspath(__file__))
LOB_KEY_FILE = os.path.join(HERE, 'lob.key')
SENDER_FILE = os.path.join(HERE, 'sender.json')
SENT_LEDGER = os.path.join(HERE, 'mail_sent.json')
PREVIEW_FILE = os.path.join(HERE, 'mail_preview.html')

# Approximate all-in cost of one first-class, single-page B&W letter (print + postage + windowed envelope).
# VERIFY the live number for your volume at https://help.lob.com/print-and-mail/ready-to-get-started/pricing-details
# (July 2026: buyers typically see $0.70-$1.10/piece; a small desk without a volume plan trends to the high end).
COST_PER_LETTER = 0.92

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
    m = re.search(r'\b([A-Za-z]{2})\s+(\d{5})(?:-\d{4})?\s*$', s)
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
    order = [snd.get('name'), snd.get('title'), snd.get('llc'),
             ('Phone: ' + snd['phone']) if snd.get('phone') else '',
             ('Email: ' + snd['email']) if snd.get('email') else '',
             snd.get('addr'), snd.get('web')]
    return [x.strip() for x in order if x and str(x).strip()]


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
    case_en = (f" (Certificate/Case No. {case_no})" if td else f" (Case No. {case_no})") if case_no else ''
    case_es = (f" (Núm. de certificado/caso {case_no})" if td else f" (Caso Núm. {case_no})") if case_no else ''
    sig = '<br>'.join(e(x) for x in _sig_lines(snd)) or sN

    if lang == 'es':
        if td:
            body = f"""<p>Estimado/a {e(first)},</p>
<p>Espero que esta carta le encuentre bien. Mi nombre es {sN} y soy inversionista de bienes raíces aquí en Miami. Le escribo respecto a su propiedad en <b>{addr}</b>, la cual según los registros del condado tiene una <b>subasta de tax deed programada para el {dt}</b>{case_es} por impuestos sin pagar.</p>
<p>Quise comunicarme por si le sirve conocer sus opciones antes de esa fecha:</p>
<ul><li>Todavía puede conservar la propiedad pagando los impuestos atrasados en cualquier momento antes de la subasta.</li>
<li>Si conservarla no es realista, vender antes de la subasta puede dejarle dinero en el bolsillo en lugar de perderla ante el condado.</li>
<li>Si se vende por más de lo que se debe en impuestos, cualquier excedente podría corresponderle a usted, aunque hay que reclamarlo.</li></ul>
<p>No soy abogado y nada de esto es asesoría legal. Compro propiedades directamente, en efectivo, y puedo cerrar antes de la fecha límite. Si vender no es lo mejor para usted, se lo diré con honestidad.</p>
<p>No hay costo ni compromiso por conversar. Puede comunicarse conmigo al <b>{sP}</b>. Aunque la subasta esté cerca, todavía puede haber tiempo.</p>
<p>Gracias por su tiempo.</p>
<p>Respetuosamente,<br><br>{sig}</p>"""
        else:
            byp = f" por parte de {plaintiff}" if plaintiff else ''
            body = f"""<p>Estimado/a {e(first)},</p>
<p>Espero que esta carta le encuentre bien. Mi nombre es {sN} y soy inversionista de bienes raíces aquí en Miami. Le escribo respecto a su propiedad en <b>{addr}</b>, la cual según los registros públicos se encuentra actualmente en proceso de ejecución hipotecaria{byp}{case_es}, con una subasta programada para el <b>{dt}</b>.</p>
<p>Entiendo que puede ser una situación estresante, y quise comunicarme por si le sirve conocer sus opciones antes de esa fecha:</p>
<ul><li>Si la propiedad se vende por más de lo que usted debe, cualquier excedente le pertenece a usted, no al prestamista.</li>
<li>Vender antes de la subasta suele ser mejor para usted que perderla en el juzgado, y puede dejarle dinero en el bolsillo.</li>
<li>El monto que consta en el expediente no siempre está actualizado, y conviene confirmarlo antes de la venta.</li></ul>
<p>No soy abogado y nada de esto es asesoría legal. Compro propiedades directamente, en efectivo, y puedo cerrar antes de la fecha límite. Si comprar no es lo adecuado para usted, con gusto le oriento hacia una mejor opción, aunque no me incluya.</p>
<p>No hay costo ni compromiso por conversar. Puede comunicarse conmigo al <b>{sP}</b>. Aunque la subasta esté cerca, todavía puede haber tiempo.</p>
<p>Gracias por su tiempo.</p>
<p>Respetuosamente,<br><br>{sig}</p>"""
    else:
        if td:
            body = f"""<p>Dear {e(owner)},</p>
<p>I hope this letter finds you well. My name is {sN}, and I am a local real estate investor here in Miami. I am writing regarding your property at <b>{addr}</b>, which county records show is scheduled for a <b>tax deed sale on {dt}</b>{case_en} due to unpaid property taxes.</p>
<p>I wanted to reach out in case it helps to know your options before that date:</p>
<ul><li>You can still keep the property by paying the back taxes any time before the sale.</li>
<li>If keeping it is not realistic, selling before the sale can put cash in your pocket rather than losing it to the county.</li>
<li>If it sells for more than the taxes owed, any surplus may belong to you, though it has to be claimed.</li></ul>
<p>I am not an attorney, and nothing here is legal advice. I purchase properties directly, with cash, and can close before the deadline. If selling is not your best move, I will tell you honestly.</p>
<p>There is no cost and no obligation to talk. You can reach me at <b>{sP}</b>. Even if the sale is close, there may still be time.</p>
<p>Thank you for your time.</p>
<p>Respectfully,<br><br>{sig}</p>"""
        else:
            byp = f" by {plaintiff}" if plaintiff else ''
            body = f"""<p>Dear {e(owner)},</p>
<p>I hope this letter finds you well. My name is {sN}, and I am a local real estate investor here in Miami. I am writing regarding your property at <b>{addr}</b>, which public records show is currently in foreclosure{byp}{case_en}, with a sale scheduled for <b>{dt}</b>.</p>
<p>I understand this can be a stressful situation, and I wanted to reach out in case it helps to know your options before that date:</p>
<ul><li>If the property sells for more than you owe, any surplus belongs to you, not the lender.</li>
<li>Selling before the auction is often better for you than losing it at the courthouse, and it can put cash in your pocket.</li>
<li>The amount on file is not always current, and it is worth confirming before the sale.</li></ul>
<p>I am not an attorney, and nothing here is legal advice. I purchase properties directly, with cash, and can close before the deadline. If buying is not the right fit for you, I am glad to point you in a better direction, even if it does not involve me.</p>
<p>There is no cost and no obligation to talk. You can reach me at <b>{sP}</b>. Even if the sale is close, there may still be time.</p>
<p>Thank you for your time.</p>
<p>Respectfully,<br><br>{sig}</p>"""

    today = datetime.date.today().strftime('%B %d, %Y')
    ret = '<br>'.join(e(x) for x in [snd.get('name'), snd.get('llc'), snd.get('addr')] if x and str(x).strip())
    # 2.6in top pad reserves the #10 window zone for Lob's stamped recipient address (address_placement).
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page{{margin:0}}
html,body{{margin:0;padding:0}}
body{{font-family:Georgia,'Times New Roman',serif;color:#111;line-height:1.5;font-size:12pt}}
.page{{width:8.5in;height:11in;box-sizing:border-box;padding:2.6in 0.9in 0.9in;position:relative}}
.ret{{position:absolute;top:0.55in;left:0.9in;font-size:10pt;color:#333;line-height:1.35}}
.date{{margin:0 0 18px;color:#333}}
ul{{margin:10px 0}}
p{{margin:0 0 12px}}
</style></head><body><div class="page">
<div class="ret">{ret}</div>
<div class="date">{today}</div>
{body}
</div></body></html>"""


def build_selection(leads, tiers, min_days, suppress, sent, remail, limit, trust_selection=False):
    """Apply every filter and return (queue, skip_reasons_counter).

    trust_selection=True (queue mode): the human already picked these leads in the tracker, so skip the
    JUDGMENT filters (tier / company / vacant / days-out) and keep only the HARD safety backstops that a
    letter physically requires or that law requires: a real owner name + deliverable address, opt-out
    suppression, and no double-mailing."""
    from collections import Counter
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
    return queue, skips


def load_suppress(path):
    """Build a set of suppressed case numbers from an exported tracker notes/sync JSON.
    Accepts a few shapes: {case: {status: 'DO NOT CONTACT', optout: true, ...}} or a list of such."""
    out = set()
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
    ap.add_argument('--min-days', type=int, default=5, help='only mail if the auction is >= N days out (letter must arrive)')
    ap.add_argument('--suppress', default='', help='exported tracker notes JSON: skip DNC/opted-out cases')
    ap.add_argument('--limit', type=int, default=0, help='cap the batch size (0 = no cap)')
    ap.add_argument('--remail', action='store_true', help='include cases already in mail_sent.json')
    ap.add_argument('--queue', default='', help="a mail-queue JSON from the tracker's 'Mail batch' button (pre-selected, opt-out-filtered)")
    ap.add_argument('--send', action='store_true', help='ACTUALLY send via Lob (needs lob.key + funded account)')
    a = ap.parse_args()

    tiers = None if a.tier.lower() == 'all' else set(t.strip().upper() for t in a.tier.split(',') if t.strip())
    snd = _load_sender()
    try:
        sent = json.load(open(SENT_LEDGER, encoding='utf-8')) if os.path.exists(SENT_LEDGER) else {}
    except Exception:
        sent = {}
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

    # write a preview of the first letter so the copy/address can be eyeballed with no key/send
    if queue:
        with open(PREVIEW_FILE, 'w', encoding='utf-8') as f:
            f.write(build_letter_html(queue[0][0], snd, a.lang))
        print(f"preview of letter #1 -> {os.path.relpath(PREVIEW_FILE, HERE)} (open in a browser)")

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
    from_addr = dict(from_parsed, name=(snd.get('llc') or snd.get('name') or 'Return')[:40])
    live = key.startswith('live_')
    print(f"\nSending {len(queue)} letters via Lob ({'LIVE — real mail + real charges' if live else 'TEST key — no real mail'})...")

    ok_n = 0
    for r, addr in queue:
        to_addr = dict(addr, name=(_owner_name(r) or 'Current Resident')[:40])
        letter = build_letter_html(r, snd, a.lang)
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
