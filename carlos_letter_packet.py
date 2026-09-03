#!/usr/bin/env python
"""carlos_letter_packet.py — a write-and-mail book: one finished letter per lead.

WHO IS IN HERE
Only leads where MAIL IS THE ONLY CHANNEL LEFT: no traced phone, no deliverable email, not
opted out, not bankrupt-stayed, case still open. For everyone else there is a faster channel
and a letter is the wrong tool. That is why this list is short and worth the postage.

WHY EACH LETTER IS PRE-WRITTEN
Handing someone 38 addresses and "write them a letter" produces 38 different pitches, and the
one that gets us in trouble is whichever one promises to stop a foreclosure. Every letter here
is the same compliant copy with the owner's own facts merged in — name, property, case number,
who is suing, the date if there is one — so the only thing left to do is copy it out.

TWO TEMPLATES
  * PERSON  — handwritten, first name, plain language. Highest response rate we can get on paper.
  * ENTITY  — the parcel is owned by an LLC/corp/trust. A "we know you're struggling" note to a
              company reads wrong; this one is business-to-business and shorter.

Output: HTML + PDF (via _mkpdf.py).
"""
import datetime
import html as H
import json
import os
import re

import disclaimer as D

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, '_letter_rows.json')

# The company inbox comes from sender.json `client_email` -- it was this literal in THREE files,
# which is three chances to update two of them. `co` follows entity.py so this packet can never
# assert an LLC the register cannot substantiate.
def _sender_defaults():
    # The email keeps the retired name on purpose. That mailbox EXISTS and is read; a biscayne*
    # address does not, and a printed reply-to that bounces is worse than one that looks mismatched.
    # It is only on the ENTITY letter (the handwritten one carries a phone), so the audience for the
    # mismatch is a company, not a homeowner. Swap it the day the new mailbox receives, not before.
    d = {'name': 'Carlos Gonzalez', 'co': 'Biscayne Solutions Group',
         'phone': '(786) 631-1823', 'email': 'miamisolutionsgroup@gmail.com'}
    try:
        import entity
        s = entity.sender()
        d['co'] = entity.display_llc()[0] or d['co']
        if (s.get('phone') or '').strip():
            d['phone'] = s['phone'].strip()
        if (s.get('client_email') or '').strip():
            d['email'] = s['client_email'].strip()
    except Exception:
        pass
    return d


SENDER = _sender_defaults()

_ENTITY = re.compile(r'\b(LLC|L L C|INC|CORP|CORPORATION|HOLDINGS?|INVESTMENTS?|PROPERTIES|'
                     r'PARTNERS?|CAPITAL|VENTURES?|GROUP|BANK|MORTGAGE|LP|LLP|ASSOC|COMPANY|'
                     r'ENTERPRISES?|REALTY|HOMES|FUND|MANAGEMENT|PROJECT|TRUST)\b')


def is_entity(owner):
    return bool(_ENTITY.search(str(owner or '').upper()))


def person_first(owner):
    """County rolls are 'LAST, FIRST M' or all-caps 'FIRST LAST'. Get a usable first name."""
    o = re.sub(r'\s+', ' ', str(owner or '').strip())
    if ',' in o:
        parts = o.split(',')
        first = parts[1].strip().split(' ')[0] if len(parts) > 1 else ''
    else:
        first = o.split(' ')[0]
    first = re.sub(r'[^A-Za-z]', '', first)
    return first.title() if len(first) > 1 else ''


def street(addr):
    return str(addr or '').split(',')[0].strip()


def money(n):
    return '$%s' % format(int(n), ',d') if n else ''


def letter_person(r):
    first = person_first(r['owner']) or 'there'
    st = street(r['addr'])
    case = r['c'] or ''
    when = (' The sale is currently set for %s.' % r['auction']) if r['auction'] else \
           (' There is no auction date set yet, which is why I am writing now.' if r['kind'] == 'PRE-FC' else '')
    whenES = (' La subasta está fijada para el %s.' % r['auction']) if r['auction'] else \
             (' Todavía no hay fecha de subasta, por eso le escribo ahora.' if r['kind'] == 'PRE-FC' else '')
    who = ('%s filed it' % r['pl']) if r['pl'] else 'a lender filed it'
    whoES = ('%s presentó el caso' % r['pl']) if r['pl'] else 'un prestamista presentó el caso'
    # COPY RULES (set after the 2026-08-10 compliance review, do not "improve" these):
    #   * the government/lender disclaimer leads. In WRITING it is not optional — a mailed pitch
    #     is a commercial communication and this is the sentence that keeps it clean.
    #   * we offer exactly TWO things, the two we can actually do: buy it, or help sell it before
    #     the auction. No loan modification, no short-sale negotiation, no bankruptcy. Offering
    #     those needs licenses BSG does not hold, and putting bankruptcy in a letter to a
    #     distressed homeowner is legal advice from a non-lawyer.
    #   * anything beyond that is pointed at their own attorney or a FREE HUD counselor.
    #   * the two MARS sentences come from disclaimer.mars_part(), not from a copy pasted here.
    #     Carlos writes these letters out by hand, so the whole mars() block is not an option — he
    #     would paraphrase it, and a paraphrased federal disclosure is worse than a short one. Two
    #     sentences he will actually copy is the trade. The entity letter below does NOT carry them:
    #     MARS runs to consumers, and an LLC-owned parcel is not one.
    en = (
        "%s,\n\n"
        "My name is %s. I work with %s, here in Miami-Dade. We are not associated with the "
        "government, and we are not approved by the government or your lender.\n\n"
        "I am writing because the public court records show a foreclosure case on %s — %s%s%s\n\n"
        "Here is exactly what we do, and nothing more: we buy houses in this situation, or we help "
        "people sell before the auction date. That is the whole offer. I am not a lender, I am not "
        "a lawyer, and there is nothing to sign and nothing to pay.\n\n"
        "If what you want is to keep the house or fight the case, that is a conversation for your "
        "own attorney, or for a HUD-approved housing counselor — that counseling is free, and you "
        "can find one at 1-800-569-4287 or hud.gov. I am not going to pretend to be either of "
        "those.\n\n"
        "What happened with the house? If selling is something you would even consider, call me and "
        "I will give you a straight answer about what it is worth and how fast it could be done. It "
        "costs nothing to talk and I am not going to pressure you.\n\n"
        "%s — %s\n\n"
        % (first if first != 'there' else 'Hello', SENDER['name'], SENDER['co'], st,
           who, (' (Case No. %s).' % case) if case else '.', when,
           SENDER['name'], SENDER['phone'])
    )
    es = (
        "%s,\n\n"
        "Mi nombre es %s. Trabajo con %s, aquí en Miami-Dade. No estamos asociados con el gobierno "
        "y no estamos aprobados por el gobierno ni por su prestamista.\n\n"
        "Le escribo porque los registros públicos de la corte muestran un caso de ejecución "
        "hipotecaria sobre %s — %s%s%s\n\n"
        "Esto es exactamente lo que hacemos, y nada más: compramos casas en esta situación, o "
        "ayudamos a venderla antes de la fecha de subasta. Esa es toda la oferta. No soy "
        "prestamista, no soy abogado, no hay nada que firmar y nada que pagar.\n\n"
        "Si lo que usted quiere es quedarse con la casa o pelear el caso, esa conversación es con su "
        "propio abogado, o con un consejero de vivienda aprobado por HUD — esa consejería es "
        "gratis, al 1-800-569-4287 o en hud.gov. No le voy a decir que soy ninguna de las dos "
        "cosas.\n\n"
        "¿Qué pasó con la casa? Si vender es algo que consideraría, llámeme y le doy una respuesta "
        "directa de cuánto vale y qué tan rápido se podría hacer. Hablar no cuesta nada y no le voy "
        "a presionar.\n\n"
        "%s — %s\n\n"
        % (first if first != 'there' else 'Hola', SENDER['name'], SENDER['co'], st,
           whoES, (' (Caso Núm. %s).' % case) if case else '.', whenES,
           SENDER['name'], SENDER['phone'])
    )
    return en, es


def letter_entity(r):
    st = street(r['addr'])
    case = r['c'] or ''
    when = (' with a sale currently set for %s' % r['auction']) if r['auction'] else \
           (' with no sale date set yet' if r['kind'] == 'PRE-FC' else '')
    en = (
        "To the owner or manager of %s:\n\n"
        "My name is %s with %s, here in Miami-Dade.\n\n"
        "Public court records show a foreclosure filed against %s%s%s. I work with owners in "
        "exactly this position and I am reaching out directly rather than through a broker.\n\n"
        "What we do is straightforward: we buy properties in this situation, or we help owners "
        "sell before the auction date. We are not associated with the government and we are not "
        "approved by the government or your lender. We are not a lender or a law firm, there is "
        "nothing to sign, and there is no fee to have the conversation.\n\n"
        "If the property is something you would consider moving, or you just want a straight read "
        "on where the case stands, call me.\n\n"
        "%s — %s\n%s\n\n"
        % (st, SENDER['name'], SENDER['co'], st, when,
           (' (Case No. %s)' % case) if case else '',
           SENDER['name'], SENDER['phone'], SENDER['email'])
    )
    es = ''
    return en, es


CSS = """
@page{size:letter;margin:12mm 11mm}
body{font:11.5px/1.5 "Segoe UI",Arial,sans-serif;color:#111827;margin:0}
h1{font-size:22px;margin:0 0 2px}
.sub{color:#4b5563;font-size:11px;margin-bottom:12px}
.howto{background:#0B1730;color:#fff;border-radius:10px;padding:14px 16px;margin-bottom:14px;page-break-after:always}
.howto h2{color:#F4E5A7;font-size:14px;margin:12px 0 6px}
.howto h2:first-child{margin-top:0}
.howto ol,.howto ul{margin:4px 0 0 17px}
.howto li{margin:4px 0;font-size:11.5px}
.howto b{color:#F4E5A7}
.warn{background:#7f1d1d;border-radius:8px;padding:9px 12px;margin-top:10px;font-size:11.5px}
.lead{page-break-inside:avoid;border:1px solid #e5e7eb;border-radius:9px;margin-bottom:12px}
.lead h3{font-size:13.5px;margin:0;background:#0B1730;color:#F4E5A7;padding:7px 11px;border-radius:8px 8px 0 0}
.facts{font-size:10.5px;color:#374151;background:#f3f4f6;padding:6px 11px;border-bottom:1px solid #e5e7eb}
.env{font-size:11px;padding:7px 11px;background:#fffbeb;border-bottom:1px solid #fde68a}
.env b{color:#92400e}
.body{padding:9px 12px;white-space:pre-wrap;font:11.5px/1.55 Georgia,serif}
.es{border-top:1px dashed #d1d5db;background:#fafafa}
.tag{background:#dcfce7;color:#065f46;font:700 9px Arial;padding:1px 6px;border-radius:7px}
.tagb{background:#fee2e2;color:#991b1b;font:700 9px Arial;padding:1px 6px;border-radius:7px}
.tagc{background:#e0e7ff;color:#3730a3;font:700 9px Arial;padding:1px 6px;border-radius:7px}
"""

HOWTO = """
<div class="howto">
<h2>&#9993; HOW TO RUN THIS PACKET</h2>
<ol>
<li><b>These %d owners have no phone and no working email.</b> Paper is the only way to reach them,
which is exactly why it is worth doing. Nobody else on our side can touch these.</li>
<li><b>Handwrite the PERSON letters.</b> Blue or black pen, plain white paper or a note card. A
handwritten envelope gets opened; a printed one gets thrown out with the junk. Do not print these
on letterhead.</li>
<li><b>Hand-address the envelope too</b>, and use a real stamp, not a postage meter. Return address:
%s, %s.</li>
<li><b>Send the English one first.</b> If the name or the neighborhood tells you they are Spanish
dominant, send the Spanish version instead — it is printed right under each English letter.</li>
<li><b>ENTITY letters (marked in blue) can be printed</b> and mailed normally. A company does not
need a handwritten note, it needs a clear business offer.</li>
<li><b>Write the date you mailed it on the line</b> at the bottom of each letter and hand this book
back to Alex so it goes in the system. That is how we know when to follow up.</li>
<li><b>Follow-up:</b> if there is no answer in 10 days, that address gets a second, shorter note.
Alex will generate it — do not improvise a second letter.</li>
</ol>
<h2>&#9888; THE RULES THAT PROTECT US</h2>
<ul>
<li><b>Never</b> write or say "I can stop the foreclosure" or "we will save your house." That exact
promise is what Florida law punishes (FS 501.1377 / the federal MARS Rule).</li>
<li><b>Never</b> ask for money, a deposit, or a signature in a letter. We charge nothing up front.</li>
<li><b>Never</b> add a dollar figure that is not printed on the card — no "I'll pay you $X."</li>
<li><b>Do not change the copy.</b> It is written this way on purpose. If a letter needs something
different, call Alex and he will write it.</li>
<li>If someone tells us to stop — by mail, phone, or at a door — that address is done, permanently.
Tell Alex the same day so it gets recorded.</li>
</ul>
<div class="warn"><b>The opt-out line at the bottom of every letter stays in.</b> It is not optional
and it is not decoration — it is the line that keeps these letters legal.</div>
</div>
"""


def _days_out(d):
    """Days until the auction. First-class mail is 2-4 days inside Miami-Dade, and the owner
    needs a day to react — a letter that lands after the gavel is wasted postage and, worse,
    it reaches someone who just lost the house."""
    for f in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return (datetime.datetime.strptime(str(d).strip(), f).date()
                    - datetime.date.today()).days
        except Exception:
            pass
    return None


def _optout_cases():
    """Cases on the server opt-out ledger. Empty set if the ledger is missing — fail OPEN on the
    file, never on the check: a missing optouts.json must not silently disable suppression.

    The rules card in this packet promises "If someone tells us to stop, that address is done,
    permanently" — and nothing here enforced it. The rows come from a hand-built _letter_rows.json,
    so a regenerated list could put an opted-out owner back in the book with the promise still
    printed on the facing page. Checked 2026-08-26: the current 38 rows contain none, so this is a
    guard against the next regeneration, not a retraction of letters already written.

    Identity ('@'/'#') keys are deliberately NOT consulted: this lane is defined as leads with no
    traced phone and no deliverable email — all 38 rows carry zero contacts — so there is nothing
    to match an identity against. Case is the only key that can apply here.
    """
    p = os.path.join(HERE, 'optouts.json')
    if not os.path.exists(p):
        print('WARNING: optouts.json not found — letters generated WITHOUT opt-out suppression')
        return set()
    try:
        raw = json.load(open(p, encoding='utf-8')) or {}
        notes = raw.get('notes') if isinstance(raw, dict) else None
        if not isinstance(notes, dict):
            notes = raw if isinstance(raw, dict) else {}
        return {str(k) for k, v in notes.items()
                if not str(k).startswith(('@', '#'))
                and isinstance(v, dict)
                and (v.get('optout') or str(v.get('status') or '').upper() in ('DO NOT CONTACT', 'OPTED OUT'))}
    except Exception as e:
        print('WARNING: optouts.json unreadable (%s) — letters generated WITHOUT suppression' % e)
        return set()


def main():
    rows = json.load(open(SRC, encoding='utf-8'))
    _opt = _optout_cases()
    _n0 = len(rows)
    rows = [r for r in rows if str(r.get('c') or '') not in _opt]
    if len(rows) != _n0:
        print('opt-out: %d letter(s) dropped — the owner asked us to stop' % (_n0 - len(rows)))
    # DILIGENCE GATE. _letter_rows.json is a hand-built scratch file whose case key is 'c', not
    # 'case' — diligence_flags.case_of() does not read 'c', so gating the row as-is would have
    # produced a clean verdict it never computed (the "succeeds while doing nothing" class). Look the
    # REAL board row up by case and gate THAT; a case with no board row is reported, not silently
    # passed, because a letter is the channel that leaves paper in someone's hands.
    try:
        import diligence_gate as _DG
        import outreach_email as _OE
        _by_case = {}
        for _lr in (_OE._load_leads() or []):
            _lc = _OE._case(_lr)
            if _lc and _lc not in _by_case:
                _by_case[_lc] = _lr
        _dg, _keep, _missing = _DG.Tally(), [], 0
        for r in rows:
            _src = _by_case.get(str(r.get('c') or ''))
            if _src is None:
                _missing += 1
                _keep.append(r)
                continue
            if _dg.check(_src)['hold']:
                continue
            _keep.append(r)
        rows = _keep
        _dg.report('carlos letters', indent='')
        if _missing:
            print('carlos letters: %d row(s) had no matching board lead and were NOT '
                  'diligence-checked — verify those by hand before mailing.' % _missing)
    except Exception as _dge:
        print('carlos letters: diligence gate SKIPPED (%s) — this packet is UNGATED.' % str(_dge)[:120])
    dropped = []
    keep = []
    for r in rows:
        n = _days_out(r['auction']) if r['auction'] else None
        if n is not None and n < 6:
            r['_why'] = ('auction already passed' if n < 0 else 'auction in %d day(s)' % n)
            dropped.append(r)
        else:
            keep.append(r)
    rows = keep
    rows.sort(key=lambda r: (-(r['val'] or 0)))
    today = datetime.date.today().isoformat()
    people = [r for r in rows if not is_entity(r['owner'])]
    ents = [r for r in rows if is_entity(r['owner'])]

    out = ['<html><head><meta charset="utf-8"><style>%s</style></head><body>' % CSS,
           '<h1>Carlos &mdash; Letters To Write</h1>',
           '<div class="sub">%s &middot; %d letters &middot; %d handwritten (person) &middot; %d printed '
           '(company-owned) &middot; every one of these owners has NO phone and NO working email on file, '
           'so mail is the only way we can reach them</div>' % (today, len(rows), len(people), len(ents)),
           HOWTO % (len(rows), SENDER['co'], SENDER['phone'])]

    for group, label in ((people, 'HANDWRITE THESE'), (ents, 'PRINT THESE (company-owned)')):
        if not group:
            continue
        out.append('<h2 style="font-size:15px;margin:14px 0 8px">%s &mdash; %d</h2>' % (label, len(group)))
        for n, r in enumerate(group, 1):
            ent = is_entity(r['owner'])
            en, es = (letter_entity(r) if ent else letter_person(r))
            tags = ('<span class="tagc">COMPANY</span> ' if ent else '<span class="tag">HANDWRITE</span> ')
            if r['auction']:
                tags += '<span class="tagb">SALE %s</span>' % H.escape(str(r['auction']))
            facts = ' &middot; '.join(x for x in [
                'value ' + money(r['val']) if r['val'] else '',
                'judgment ' + money(r['judg']) if r['judg'] else '',
                ('filed ' + str(r['filed'])) if r['filed'] else '',
                ('plaintiff: ' + r['pl']) if r['pl'] else '',
                'case ' + str(r['c'] or ''),
                'PRE-FORECLOSURE, no sale date' if r['kind'] == 'PRE-FC' else 'auction case',
            ] if x)
            out.append('<div class="lead"><h3>%d. %s &nbsp; %s</h3>' % (n, H.escape(r['addr']), tags))
            out.append('<div class="facts">%s</div>' % H.escape(facts).replace('&amp;middot;', '&middot;'))
            out.append('<div class="env"><b>ENVELOPE:</b> %s &nbsp;|&nbsp; %s &nbsp; '
                       '<i>(mail to the property — no separate mailing address on file)</i></div>'
                       % (H.escape(r['owner'] or 'Current Owner'), H.escape(r['addr'])))
            out.append('<div class="body">%s\n\n_______  mailed on: ____ / ____ / 2026</div>'
                       % H.escape(en))
            if es:
                out.append('<div class="body es"><b>SI PREFIERE ESPA&Ntilde;OL &mdash; use esta versi&oacute;n en '
                           'lugar de la de arriba:</b>\n\n%s</div>' % H.escape(es))
            out.append('</div>')

    if dropped:
        out.append('<h2 style="font-size:15px;margin:14px 0 6px">DO NOT MAIL &mdash; %d</h2>'
                   '<div class="sub">These are in the letter lane too, but the auction lands before '
                   'the mail could. A letter arriving after the sale reaches someone who just lost the '
                   'house. If any of these matter, they need a phone call or a door today, not paper.</div>'
                   '<table style="width:100%%;border-collapse:collapse;font-size:11px">' % len(dropped))
        for r in dropped:
            out.append('<tr><td style="border-top:1px solid #eee;padding:5px 7px"><b>%s</b></td>'
                       '<td style="border-top:1px solid #eee;padding:5px 7px">%s</td>'
                       '<td style="border-top:1px solid #eee;padding:5px 7px;color:#991b1b">%s</td></tr>'
                       % (H.escape(r['addr']), H.escape(r['owner'][:30]), H.escape(r['_why'])))
        out.append('</table>')
    out.append('<div class="sub" style="margin-top:10px">%d letters. Mark the mail date on each one and '
               'give the book back to Alex.</div></body></html>' % len(rows))
    p = os.path.join(HERE, 'Carlos_Letters_%s.html' % today)
    open(p, 'w', encoding='utf-8').write('\n'.join(out))
    print('letters: %d (%d handwrite, %d print)' % (len(rows), len(people), len(ents)))
    print('wrote', p)


if __name__ == '__main__':
    main()
