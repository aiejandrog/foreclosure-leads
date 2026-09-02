#!/usr/bin/env python
"""bsg_mail_campaign.py — a hand-write-and-mail bundle: fresh lis pendens, budgeted by stamps.

WHAT THIS IS FOR
Alejandro + Carlos splitting a real postage budget and writing letters by hand, because Jose's
read is that handwritten converts and he is right about why: the envelope is what decides whether
the thing gets opened at all, and a hand-addressed envelope with a real stamp does not look like
the four other foreclosure mailers that arrived the same week.

WHY THE LETTER IS SHORT
carlos_letter_packet's letter is six paragraphs. That is the right length for a lane of 30 where
mail is the ONLY channel left. It is the wrong length for 90, because nobody handwrites six
paragraphs ninety times -- and more to the point, nobody BELIEVES a six-paragraph handwritten
letter. A short note reads as a person; a long one reads as a copied script, which is what it
would be. Five sentences, ~90 words, about two minutes each including the envelope.

COMPLIANCE IS NOT OPTIONAL AND IS NOT COPIED
The two MARS/Reg O sentences come from disclaimer.mars_part(), the same source bsg_letter and
carlos_letter_packet use -- hand-copying compliance text is exactly how four surfaces drifted into
four different disclaimers before disclaimer.py existed. The government/lender denial, the
not-a-lawyer line and the opt-out sentence are in the body itself. Every one of them is load
bearing; the copy is short because the SALES part is short, not because anything was trimmed
out of the legal part.

SUPPRESSION
Runs the same opt-out ledger the other five channels use, case AND identity keys, and drops
entities, estates, life estates and anything already on the ledger. See _suppressiontest.py.

Usage:
  python bsg_mail_campaign.py                     # 14-day fresh pool, $80 budget
  python bsg_mail_campaign.py --days 30 --budget 120
  python bsg_mail_campaign.py --limit 40          # ignore budget, cap the count
"""
import argparse
import datetime as dt
import html as H
import json
import os
import re

import disclaimer as D
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))

# 2026-07-12 USPS increase: a First-Class Forever stamp is $0.82. Envelopes run ~$0.04 in a box of
# 500 and a sheet of paper ~$0.02. Verified against USPS pricing 2026-08-26 rather than assumed --
# the per-letter number is the whole budget, so a stale 78c would have overstated the count by 5%.
STAMP = 0.82
ENVELOPE = 0.04
PAPER = 0.02
PER_LETTER = STAMP + ENVELOPE + PAPER

# The signer and the phone must be the SAME person. Defaults to sender.json's
# identity; --signer-name/--signer-phone let Carlos put his own on his half of the
# stack. A letter signed by one brother carrying the other's number is the kind of
# detail that reads as a script rather than a neighbour.
SENDER = {'name': 'Alejandro Gonzalez', 'phone': '(786) 631-1823'}
try:
    import entity
    SENDER['co'] = entity.display_llc()[0] or 'Biscayne Solutions Group'
    _s = entity.sender()
    if (_s.get('phone') or '').strip():
        SENDER['phone'] = _s['phone'].strip()
    if (_s.get('name') or '').strip():
        SENDER['name'] = _s['name'].strip()
except Exception:
    SENDER['co'] = 'Biscayne Solutions Group'

# TRACKING NUMBER — the whole reason a mail test is worth paying for.
# Every other channel already uses the operator's main line, so a call from a letter is
# indistinguishable from a call from a door, a text or an email. Spend $264 on 300 letters
# without this and you still cannot answer "did mail work" — you have bought postage, not data.
# Set MAIL_TRACKING_PHONE to a number used on NOTHING ELSE (a free Google Voice line is enough)
# and every call to it is attributable to this campaign by construction.
# Falls back to the signer's phone with a loud warning rather than silently printing an
# untrackable letter.
MAIL_TRACKING_PHONE = os.environ.get('BSG_MAIL_PHONE', '').strip()


_ENTITY = re.compile(r'\b(LLC|L L C|INC|CORP|CORPORATION|TRUST|BANK|MORTGAGE|SERVICING|HOLDINGS?|'
                     r'PROPERTIES|PROPERTY|INVESTMENTS?|CAPITAL|FUND|ASSOC|ASSOCIATION|LP|LLP|'
                     r'COMPANY|REALTY|HOMES|GROUP|VENTURES?|PARTNERS?|MANAGEMENT|ENTERPRISES?|'
                     r'REO|N A|NA)\b', re.I)
_ESTATE = re.compile(r'\bEST(ATE)? OF\b|\bLIFE\s*EST\b|\bDECEASED\b|\bDEC\'?D\b', re.I)
_NAME_MARK = re.compile(r'\b(H/E|H&W|W/E|ET\s*AL|ETAL|ETUX|ETVIR|TRUSTEES?|TRS?|REV(OCABLE)?|'
                        r'LIV(ING)?|JT(RS)?|LE|REM)\b', re.I)


_DIR = {'NW', 'NE', 'SW', 'SE', 'N', 'S', 'E', 'W'}
_KEEP = {'ST', 'AVE', 'TER', 'CT', 'DR', 'RD', 'PL', 'LN', 'BLVD', 'CIR', 'WAY', 'PKWY', 'HWY'}


def street_case(s):
    """Title-case an address WITHOUT destroying directionals. '1700 SW 85 TER' must not become
    '1700 Sw 85 Ter' -- that is the first thing a Miami homeowner reads, and it looks like a
    machine wrote it, which is the exact impression a handwritten letter exists to avoid."""
    out = []
    for w in str(s or '').split():
        u = w.upper().strip('.,')
        if u in _DIR:
            out.append(u)
        elif u in _KEEP:
            out.append(u.title())
        elif any(ch.isdigit() for ch in u):
            out.append(u)                      # 85, 1700, 12A -- never re-case a number
        else:
            out.append(w.title())
    return ' '.join(out)


def _load(name, default=None):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default if default is not None else {}
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default if default is not None else {}


def _optout_keys():
    """Case keys AND raw identity keys from the server ledger. Fails OPEN on a missing file but
    says so -- a suppression that silently suppresses nothing is worse than no suppression."""
    raw = _load('optouts.json')
    notes = raw.get('notes') if isinstance(raw.get('notes'), dict) else raw
    if not isinstance(notes, dict) or not notes:
        print('WARNING: optouts.json missing or empty — building WITHOUT opt-out suppression')
        return set()
    return {str(k) for k, v in notes.items()
            if isinstance(v, dict)
            and (v.get('optout') or str(v.get('status') or '').upper() in
                 ('DO NOT CONTACT', 'OPTED OUT'))}


def person_name(owner):
    """'ABOUELELLA,MOHAMED S' -> 'Mohamed S Abouelella'.

    The county stores LAST,FIRST with no space after the comma. Printed straight onto an envelope
    that is the single clearest tell that a database addressed it, which defeats the entire point
    of hand-writing the thing."""
    raw = str(owner or '').split(';')[0].strip()
    if not raw:
        return ''
    raw = _NAME_MARK.sub(' ', raw)
    if ',' in raw:
        last, first = raw.split(',', 1)
        raw = '%s %s' % (first.strip(), last.strip())
    return ' '.join(w.title() if w.isalpha() else w for w in raw.split())


def _first_name(owner):
    """County data is 'LAST,FIRST' (often no space). Return a usable first name, or ''."""
    raw = str(owner or '').split(';')[0].strip()
    if not raw:
        return ''
    part = raw.split(',', 1)[1] if ',' in raw else raw
    part = _NAME_MARK.sub(' ', part)
    part = part.split('&')[0]
    for tok in part.split():
        tok = tok.strip(' .,')
        if len(tok) > 1 and tok.isalpha():
            return tok.title()
    return ''


def _filed_index():
    lp = _load('lis_pendens.json', [])
    rows = lp if isinstance(lp, list) else (lp.get('rows') or list(lp.values()))
    out = {}
    for x in rows:
        if not isinstance(x, dict):
            continue
        c, d = str(x.get('case') or ''), str(x.get('date') or '')
        if c and d:
            out[c] = d
    return out


def _age_days(datestr):
    for f in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return (dt.date.today() - dt.datetime.strptime(str(datestr).split()[0], f).date()).days
        except Exception:
            pass
    return None


def pool(days, opt):
    """Fresh, high-confidence, real-person, not-suppressed LP rows with a mailable address."""
    filed = _filed_index()
    rows = _load('lp_addresses.json', [])
    rows = rows if isinstance(rows, list) else list(rows.values())
    drops = {'low-confidence': 0, 'no address': 0, 'entity-owned': 0, 'estate/life-estate': 0,
             'opted out': 0, 'stale filing': 0, 'no first name': 0}
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        # HIGH only. A medium row is a maybe-this-is-the-right-parcel, and a letter to the wrong
        # house about someone else's foreclosure is the single worst piece of mail we could send.
        if str(r.get('confidence')) != 'high':
            drops['low-confidence'] += 1
            continue
        addr, zipc = str(r.get('addr') or '').strip(), str(r.get('zip') or '').strip()
        if not addr or not zipc:
            drops['no address'] += 1
            continue
        case = str(r.get('case') or '')
        if case in opt:
            drops['opted out'] += 1
            continue
        owner = str(r.get('paOwner') or '')
        if not owner.strip() or _ENTITY.search(owner):
            drops['entity-owned'] += 1
            continue
        if _ESTATE.search(owner):
            drops['estate/life-estate'] += 1
            continue
        age = _age_days(filed.get(case, ''))
        if age is None or age > days:
            drops['stale filing'] += 1
            continue
        first = _first_name(owner)
        if not first:
            drops['no first name'] += 1
            continue
        out.append({'case': case, 'owner': owner, 'first': first, 'addr': addr,
                    'city': str(r.get('city') or '').strip(), 'zip': zipc.split('-')[0],
                    'age': age, 'plaintiff': str(r.get('plaintiff') or '').strip()})
    out.sort(key=lambda r: r['age'])          # freshest first — least competition, most urgency
    return out, drops


def contact_phone():
    """The number printed on the letter. The tracking line when one is set, otherwise the signer's
    with a warning -- never silently untrackable."""
    return MAIL_TRACKING_PHONE or SENDER['phone']


def letter(r):
    """~90 words. Short because it is HANDWRITTEN, not because anything legal was trimmed.

    Carries, in order: who I am, the four denials (government / lender / lawyer / no money), why I
    am writing, the offer, the two MARS sentences from disclaimer.mars_part(), and the opt-out.
    """
    street = street_case(r['addr'].split(',')[0])
    return (
        "%s,\n\n"
        "I'm %s with %s, here in South Florida. I'm not with the government or your lender, "
        "I'm not a lawyer, and I'm not asking you for any money.\n\n"
        "I saw the court filing on %s. If selling is something you'd consider, I buy houses in "
        "this situation, or I can help you sell before a sale date gets set.\n\n"
        "If you'd rather I didn't write again, call me and say stop.\n\n"
        "%s\n%s"
        % (r['first'], SENDER['name'], SENDER['co'], street,
           SENDER['name'], contact_phone())
    )


def letterhead():
    """Logo block for the printed variant. Reuses bsg_flyer's embedded PNG rather than a second
    copy of the asset — one logo, one place, same reason the disclaimer lives in disclaimer.py."""
    try:
        import bsg_flyer
        return ('<img class="logo" src="%s" alt="">' % bsg_flyer.BSG_LOGO_B64)
    except Exception:
        return '<div class="logoword">%s</div>' % H.escape(SENDER['co'])


def letter_printed(r):
    """Same words as the handwritten version. The ONLY differences are the letterhead and that a
    printed letter can carry a full address block, because nobody hand-copies one of those."""
    return letter(r)


CSS = """
@page{size:letter;margin:11mm 10mm}
body{font:12px/1.5 "Segoe UI",Arial,sans-serif;color:#111827;margin:0}
h1{font-size:21px;margin:0 0 2px}
.sub{color:#4b5563;font-size:11.5px;margin-bottom:12px}
.how{background:#0B1730;color:#fff;border-radius:10px;padding:14px 16px;margin-bottom:14px;page-break-after:always}
.how h2{color:#F4E5A7;font-size:14px;margin:12px 0 6px}
.how h2:first-child{margin-top:0}
.how li{margin:5px 0;font-size:12px}
.how b{color:#F4E5A7}
.warn{background:#7f1d1d;border-radius:8px;padding:9px 12px;margin-top:10px;font-size:12px}
.card{page-break-inside:avoid;border:1px solid #d1d5db;border-radius:9px;margin-bottom:11px}
.hd{font-size:13px;margin:0;background:#0B1730;color:#F4E5A7;padding:6px 11px;border-radius:8px 8px 0 0}
.env{font:13.5px/1.5 Georgia,serif;padding:9px 12px;background:#fffbeb;border-bottom:1px solid #fde68a}
.env b{display:block;font:700 10px Arial;color:#92400e;letter-spacing:.05em;margin-bottom:3px}
.body{padding:9px 12px;white-space:pre-wrap;font:13px/1.6 Georgia,serif}
.meta{font-size:10.5px;color:#4b5563;background:#f3f4f6;padding:5px 11px;border-top:1px solid #e5e7eb}
.done{font-size:11px;color:#374151;padding:5px 12px 9px}
/* ---- PRINTED VARIANT: one letter per sheet, logo at the top, address block under it. ---- */
.pl{page-break-after:always;padding:2mm 4mm 0}
.pl .logo{height:0.95in;display:block;margin:0 0 5mm}
.pl .logoword{font:700 20px "Segoe UI",Arial;color:#0B1730;margin:0 0 5mm}
.pl .to{font:13px/1.5 Georgia,serif;margin:0 0 7mm}
.pl .dt{font:12px Georgia,serif;color:#4b5563;margin:0 0 5mm}
.pl .bd{font:13.5px/1.72 Georgia,serif;white-space:pre-wrap}
.pl .ft{margin-top:8mm;padding-top:3mm;border-top:1px solid #d1d5db;font:10px/1.45 Arial;color:#6b7280}
"""


def build(rows, budget, days, drops, total_pool):
    today = dt.date.today().isoformat()
    cost = len(rows) * PER_LETTER
    half = cost / 2
    out = ['<html><head><meta charset="utf-8"><style>%s</style></head><body>' % CSS,
           '<h1>Handwrite &amp; Mail &mdash; %d letters</h1>' % len(rows),
           '<div class="sub">%s &middot; lis pendens filed in the last %d days &middot; '
           '$%.2f total postage ($%.2f each if you split it) &middot; freshest first</div>'
           % (today, days, cost, half),
           '<div class="how">'
           '<h2>&#9993; HOW TO RUN THIS</h2><ol>'
           '<li><b>Copy each letter by hand.</b> Blue or black pen, plain paper or a note card. '
           'It is five sentences on purpose &mdash; about two minutes each. Do NOT print it: a '
           'printed "handwritten" letter is worse than an honestly printed one.</li>'
           '<li><b>Hand-address the envelope too, and use a real stamp.</b> This is the part that '
           'decides whether it gets opened. A metered or labelled envelope reads as bulk mail and '
           'goes in the bin with the other four foreclosure mailers that week.</li>'
           '<li><b>No return-address label.</b> Write it. Same reason.</li>'
           '<li><b>Split the stack.</b> %d letters, $%.2f each. Take alternating pages so you both '
           'get the same mix of fresh and older filings.</li>'
           '<li><b>Mail them the same day you write them</b> &mdash; these are the freshest filings '
           'on the board and the whole advantage is being early.</li>'
           '<li><b>Tick the box</b> at the bottom of each card as you go, and tell Alex which ones '
           'went out so the system stops other channels double-touching them.</li>'
           '</ol>'
           '<h2>&#9888; THE RULES</h2><ul>'
           '<li><b>Do not change the wording.</b> Four of those sentences are there for legal '
           'reasons, not sales reasons. If a letter needs to say something different, call Alex.</li>'
           '<li><b>Never</b> write "I can stop the foreclosure" or "we can save your house." That '
           'exact promise is what Florida punishes (FS 501.1377 / the federal MARS Rule).</li>'
           '<li><b>Never</b> ask for money, a signature, or a deposit in a letter.</li>'
           '<li><b>If anyone says stop</b> &mdash; by mail, phone, text or at the door &mdash; that '
           'is permanent and it covers every channel. Tell Alex the same day.</li>'
           '</ul>'
           '<div class="warn"><b>The last line of every letter stays in.</b> It is not filler; it '
           'is the line that makes the rest of it legal to send.</div></div>'
           % (len(rows), half)]

    for n, r in enumerate(rows, 1):
        city = r['city'] if r['city'].lower() not in ('unincorporated county', '') else 'Miami'
        out.append('<div class="card"><h3 class="hd">%d. %s &nbsp;&middot;&nbsp; filed %d day%s ago</h3>'
                   % (n, H.escape(street_case(r['addr'])), r['age'], '' if r['age'] == 1 else 's'))
        out.append('<div class="env"><b>WRITE THIS ON THE ENVELOPE</b>%s<br>%s<br>%s, FL %s</div>'
                   % (H.escape(person_name(r['owner'])), H.escape(street_case(r['addr'])),
                      H.escape(city.title()), H.escape(r['zip'])))
        out.append('<div class="body">%s</div>' % H.escape(letter(r)))
        meta = 'case %s' % r['case'] + (' &middot; %s' % H.escape(r['plaintiff'][:44])
                                        if r['plaintiff'] else '')
        out.append('<div class="meta">%s</div>' % meta)
        out.append('<div class="done">&#9744; written &nbsp;&nbsp; &#9744; addressed &nbsp;&nbsp; '
                   '&#9744; stamped &nbsp;&nbsp; &#9744; mailed on ____ / ____</div></div>')

    kept = ', '.join('%s %d' % (k, v) for k, v in sorted(drops.items()) if v)
    out.append('<div class="sub" style="margin-top:10px">%d of %d eligible letters in this bundle. '
               'Dropped before selection: %s.</div></body></html>'
               % (len(rows), total_pool, kept or 'none'))
    return '\n'.join(out)


def build_printed(rows, days, drops, total_pool):
    """One letter per sheet, on letterhead, ready to fold into a #10 window-less envelope.

    The words are IDENTICAL to the handwritten version -- same offer, same four denials, same two
    MARS sentences, same opt-out. Only the presentation differs. Keeping one body means a copy fix
    lands on both variants; two bodies is how the flyer, letter and email drifted apart before.
    """
    today = dt.date.today()
    pretty = today.strftime('%B %-d, %Y') if os.name != 'nt' else today.strftime('%B %d, %Y').replace(' 0', ' ')
    out = ['<html><head><meta charset="utf-8"><style>%s</style></head><body>' % CSS]
    for r in rows:
        city = r['city'] if r['city'].lower() not in ('unincorporated county', '') else 'Miami'
        out.append('<div class="pl">%s' % letterhead())
        out.append('<div class="dt">%s</div>' % pretty)
        out.append('<div class="to">%s<br>%s<br>%s, FL %s</div>'
                   % (H.escape(person_name(r['owner'])), H.escape(street_case(r['addr'])),
                      H.escape(city.title()), H.escape(r['zip'])))
        out.append('<div class="bd">%s</div>' % H.escape(letter(r)))
        out.append('<div class="ft">%s &middot; %s &middot; case %s</div></div>'
                   % (H.escape(SENDER['co']), H.escape(contact_phone()), H.escape(r['case'])))
    out.append('</body></html>')
    return '\n'.join(out)


def build_envelopes(rows):
    """Address sheet for the printed run: every recipient, in mailing order, to write or label."""
    out = ['<html><head><meta charset="utf-8"><style>'
           '@page{size:letter;margin:12mm}'
           'body{font:12px/1.45 "Segoe UI",Arial;margin:0}'
           'h1{font-size:18px;margin:0 0 3px}.s{color:#4b5563;font-size:11px;margin-bottom:10px}'
           'table{width:100%;border-collapse:collapse}'
           'td{border-bottom:1px solid #e5e7eb;padding:6px 5px;vertical-align:top;font:12px Georgia,serif}'
           'td.n{width:26px;color:#6b7280;font:11px Arial}'
           '</style></head><body>',
           '<h1>Envelopes &mdash; %d</h1>' % len(rows),
           '<div class="s">Hand-address these in the same order as the letters. '
           'Return address: %s.</div><table>' % H.escape(SENDER['co'])]
    for n, r in enumerate(rows, 1):
        city = r['city'] if r['city'].lower() not in ('unincorporated county', '') else 'Miami'
        out.append('<tr><td class="n">%d</td><td><b>%s</b><br>%s<br>%s, FL %s</td></tr>'
                   % (n, H.escape(person_name(r['owner'])), H.escape(street_case(r['addr'])),
                      H.escape(city.title()), H.escape(r['zip'])))
    out.append('</table></body></html>')
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=14, help='max age of the LP filing (default 14)')
    ap.add_argument('--budget', type=float, default=80.0, help='postage budget in dollars')
    ap.add_argument('--limit', type=int, default=0, help='hard cap on letters (overrides budget)')
    ap.add_argument('--signer-name', default='', help="who signs (default: sender.json's name)")
    ap.add_argument('--signer-phone', default='', help='phone printed under the signature')
    ap.add_argument('--printed', action='store_true',
                    help='letterhead + logo, one letter per sheet (instead of the copy-by-hand book)')
    ap.add_argument('--tracking-phone', default='',
                    help='number used ONLY on this campaign, so replies are attributable')
    a = ap.parse_args()

    if a.signer_name:
        SENDER['name'] = a.signer_name
    if a.signer_phone:
        SENDER['phone'] = a.signer_phone
    global MAIL_TRACKING_PHONE
    if a.tracking_phone:
        MAIL_TRACKING_PHONE = a.tracking_phone.strip()
    if not MAIL_TRACKING_PHONE:
        print('WARNING: no tracking number (--tracking-phone or BSG_MAIL_PHONE).')
        print('         Letters will carry %s, the same line every other channel uses, so a call'
              % SENDER['phone'])
        print('         CANNOT be attributed to this mailing. You would be buying postage, not data.')
    opt = _optout_keys()
    rows, drops = pool(a.days, opt)
    total = len(rows)
    n = a.limit if a.limit else int(a.budget // PER_LETTER)
    rows = rows[:n]
    if not rows:
        print('no eligible letters — widen --days')
        return

    tag = 'Printed' if a.printed else 'Handwrite'
    html = build_printed(rows, a.days, drops, total) if a.printed \
        else build(rows, a.budget, a.days, drops, total)
    p_html = os.path.join(HERE, 'BSG_Mail_%s_%s.html' % (tag, dt.date.today().isoformat()))
    open(p_html, 'w', encoding='utf-8').write(html)
    if a.printed:
        # The printed run needs an address list too — the letters go in envelopes somebody still
        # has to address, and doing that off 300 separate sheets is how one gets misfiled.
        p_env = os.path.join(HERE, 'BSG_Mail_Envelopes_%s.html' % dt.date.today().isoformat())
        open(p_env, 'w', encoding='utf-8').write(build_envelopes(rows))
        print('wrote', p_env)
    print('%d letters (of %d eligible, filed within %dd) [%s]'
          % (len(rows), total, a.days, 'PRINTED + logo' if a.printed else 'handwritten'))
    print('  contact number on the letter: %s%s'
          % (contact_phone(), '  <-- TRACKING' if MAIL_TRACKING_PHONE else '  (NOT trackable)'))
    print('  postage: $%.2f at $%.2f/letter - $%.2f each split two ways'
          % (len(rows) * PER_LETTER, PER_LETTER, len(rows) * PER_LETTER / 2))
    print('  dropped:', ', '.join('%s %d' % (k, v) for k, v in sorted(drops.items()) if v) or 'none')
    print('wrote', p_html)
    # _mkpdf.py is a SCRIPT (argv-driven), not a module with to_pdf() — importing and calling it
    # made it re-render its own default filename. Inline the same Playwright render bsg_daily_routes
    # uses, which is the working pattern in this repo.
    try:
        from playwright.sync_api import sync_playwright
        base = os.path.splitext(os.path.basename(p_html))[0]
        outs = [os.path.join(HERE, base + '.pdf')]
        try:
            outs.append(P.out(base + '.pdf'))
        except Exception:
            pass
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page()
            pg.goto('file:///' + p_html.replace(os.sep, '/'))
            pg.wait_for_timeout(900)
            pdf = pg.pdf(format='Letter', print_background=True,
                         margin={'top': '11mm', 'bottom': '11mm',
                                 'left': '10mm', 'right': '10mm'})
            b.close()
        for o in outs:
            os.makedirs(os.path.dirname(o), exist_ok=True)
            open(o, 'wb').write(pdf)
            print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))
    except Exception as e:
        print('(PDF step failed: %s — open the HTML and print from the browser)' % e)


if __name__ == "__main__":
    main()
