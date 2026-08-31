# -*- coding: utf-8 -*-
"""outreach_copy.py — ONE source of words for email, text and letter.

THE COPY IS ALEJANDRO'S. He wrote it 2026-08-28 and it ships as written. Subject line, tone,
length, the 30-years line, the CTA — all of it is his call and none of it is edited here. If the
words below look wrong to you, that is a conversation with him, not a code change.

THE ONE THING THIS FILE ADDS: the MARS/Reg O disclosure paragraph at the end of each body.
That is not a style opinion. 16 CFR 1015.4(a) requires it in a commercial communication offering
mortgage assistance relief, and a free consultation to review a homeowner's foreclosure options is
squarely that. It comes from disclaimer.py so all four surfaces say the same sentences — they
drifted into four different disclaimers once before, which is why disclaimer.py exists at all.

Channel differences are LENGTH ONLY, never claims:
  email  — full copy + full MARS block
  sms    — the same offer compressed, with the statutory core + STOP (a carrier-truncated
           disclosure is no disclosure, so the long block cannot go in a text)
  letter — handwritten length + the three federally mandated sentences
"""
import datetime as dt

import disclaimer as D

COMPANY = 'Biscayne Solutions Group'
SIGNER = 'Alex Gonzalez'
PHONE = '(786) 631-1823'


def _first_of(signer):
    """'Alejandro Gonzalez' -> 'Alejandro'. The bodies used to hardcode 'Alex' while the
    signature came from sender.json, so one email carried two different names."""
    return (str(signer or '').strip().split() or ['there'])[0]


def _fmt_date(d):
    """'Wednesday, September 16' from a date or ISO/US string. '' when unknown."""
    if not d:
        return ''
    if isinstance(d, str):
        s = d.strip()
        for f in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y'):
            try:
                d = dt.datetime.strptime(s, f).date()
                break
            except ValueError:
                continue
        else:
            return s
    return d.strftime('%A, %B ') + str(d.day)


def _short_date(d):
    """'Sept 16' for the short subject line."""
    if not d:
        return ''
    if isinstance(d, str):
        return d
    return d.strftime('%b ').replace('Sep ', 'Sept ') + str(d.day)


def _mars(company=COMPANY):
    return D.mars(company, 'en', as_html=False)


def _ident(identity, lang='en'):
    """None -> the one sourced sentence from disclaimer.py. '' -> caller wants it omitted."""
    return D.identity(lang, as_html=False) if identity is None else str(identity)


def _sig_lines(sig_block, signer, company, phone):
    """His three-line sign-off, unless a caller hands over a full CAN-SPAM signature block.

    PLUMBING, NOT COPY. The sentences above are Alejandro's and are not touched here. But a real
    send has to carry the sender's physical mailing address (CAN-SPAM 15 U.S.C. 7704(a)(5)) and the
    board already builds exactly that block for every other body; passing it in beats printing the
    name/company/phone twice.
    """
    if sig_block:
        return [l for l in str(sig_block).split('\n')]
    return [signer, company, phone]


# ---------------------------------------------------------------------------------------------
# EMAIL — Alejandro's long form, verbatim
# ---------------------------------------------------------------------------------------------
def email_subject(sale_date=None, short=False):
    ds = _short_date(sale_date) if short else _fmt_date(sale_date)
    # SWAP 3 (approved 2026-08-30). URGENT dropped. Bounce rate was pulled from 28-33% to 16.9%
    # while the measured subject was shipping, URGENT is a known spam-filter trigger, and it is
    # the rescue-scam silhouette. The sale date carries the urgency on its own.
    if not ds:
        return 'Foreclosure Sale of Your Home'
    if short:
        return 'Foreclosure sale %s - your home' % ds
    return 'Foreclosure sale %s - your home' % ds


def email_body(first='', sale_date=None, signer=SIGNER, phone=PHONE, company=COMPANY,
               identity=None, sig_block=None):
    ds = _fmt_date(sale_date) or '[DATE]'
    ident = _ident(identity)
    return '\n'.join([
        'Hi %s,' % (first or '[First Name]'),
        '',
        "I know this is a stressful time, and you're likely receiving a lot of mail and calls "
        "about your property so I'll be brief and direct",
        '',
        "Your home is scheduled for foreclosure auction on %s." % ds,
        # SWAP 2 (approved 2026-08-30). Was: "...you will lose any equity you have and could be
        # forcefully evicted shortly after." That is not a scare tactic, it is FALSE: surplus
        # above the judgment belongs to the former owner under FS 45.032, and the drill pack
        # already teaches reps to tell them that. Telling a consumer the opposite, in writing,
        # about their own money, is the claim that turns an aggressive email into a deceptive
        # one. Same dread, all of it true.
        "If nothing changes before that date, the house is sold to the highest bidder and "
        "every choice you have right now goes with it.",
        "But this does not have to happen.",
        # Was the literal "Alex". The function already takes `signer`, so the body ignored it and no
        # sender but Alex could ever introduce themselves correctly -- and because the SIGNATURE is
        # built from sender.json, the shipped email said "My name is Alex" over "Alejandro Gonzalez".
        # Two names, one email, on a message whose whole job is proving we are not a mass mailer.
        ("My name is %s with the team at %s." % (_first_of(signer), company))
        + ((' ' + ident) if ident else ''),
        # SWAP 1 (approved 2026-08-30). Was: "For over 30 years, our team has helped thousands
        # of Florida homeowners just like you stop foreclosures..." Three problems in one
        # sentence: "stop foreclosures" is near-verbatim the FS 501.1377 trigger ("stop, prevent
        # or reverse"); "thousands" is a quantified performance claim with nothing behind it
        # (FDUTPA); and "our team, 30 years" put the experience on a company filed 8/24/2026.
        # Same weight, said about the person who actually earned it.
        "Our senior advisor has spent over 30 years in mortgages and foreclosure workouts, and "
        "he personally reviews your case. He has sat across from people in exactly your "
        "position and walked them out of it with their equity intact.",
        '',
        "We specialize in Urgent Foreclosure Cases that are facing an auction in just a few days.",
        '',
        "I don't know if you already have a plan, but I want to offer you something completely free "
        "and without any obligation: a 15-minute private phone consultation.",
        '',
        "During that call, we will:",
        '',
        "  • Review your entire case and situation.",
        "  • Explain every option available to you (many of which you may not know exist)",
        "  • Give you a clear, honest path forward.",
        '',
        "There are no tricks, no hidden fees, and no pressure.",
        '',
        "Here's what I need from you:",
        '',
        "Simply reply to this email with the best phone number and times to call you, or call me "
        "directly at %s and I'll personally take your call and give you my 100%% personal "
        "attention. I promise to be objective and help you resolve your situation in the best and "
        "fastest way possible." % phone,
        '',
        "There is still time to protect and save your home or equity, but the clock is ticking.",
        "Please don't wait.",
        '',
        "Reply now or call me today.",
        '',
        "Warm regards,",
        '',
    ] + _sig_lines(sig_block, signer, company, phone) + [
        '',
        _mars(company),
        '',
        "If you would rather not hear from us again, reply STOP and we will not contact you again "
        "on any channel.",
    ])


def email_body_short(first='', sale_date=None, signer=SIGNER, phone=PHONE, company=COMPANY,
                     identity=None, sig_block=None):
    ds = _short_date(sale_date) or '[DATE]'
    ident = _ident(identity)
    return '\n'.join([
        'Hi %s,' % (first or '[First Name]'),
        '',
        "Your home is scheduled for foreclosure auction on %s." % ds,
        # SWAP 2, short body. Same false equity claim, compressed.
        "If nothing changes before that date, the house is sold to the highest bidder and "
        "every choice you have right now goes with it.",
        "But this doesn't have to happen.",
        '',
        ("I'm %s with %s." % (_first_of(signer), company)) + ((' ' + ident) if ident else '') +
        # SWAP 1, short body. Same three defects, plus an em dash.
        " Our senior advisor has over 30 years in mortgages and foreclosure workouts and he "
        "personally reviews your case, including cases only days from auction.",
        '',
        "I don't know if you already have a plan, but I'm offering you a free, no-obligation "
        "15-minute phone consultation. No tricks. No fees. No pressure.",
        '',
        "On that call, I'll:",
        '',
        "  • Review your entire situation",
        "  • Lay out every option you have (including ones you haven't heard of)",
        "  • Give you a clear, honest path forward",
        '',
        "Here's all I need from you:",
        "Reply with your phone number and best time to call, or call me directly at %s. I'll "
        "give you my personal, undivided attention and help you find the fastest solution." % phone,
        '',
        "There is still time to save your home or your equity, but not much.",
        "Reply now or call today.",
        '',
        "Warm regards,",
    ] + _sig_lines(sig_block, signer, company, phone) + [
        '',
        _mars(company),
        '',
        "Reply STOP and we stop, on every channel.",
    ])


# ---------------------------------------------------------------------------------------------
# THE MIRROR — one baked template, two renderers
#
# tracker_template.html's genEmail() (manual composer + morning worker) and outreach_email.py (the
# unattended nightly send) have drifted from each other three times: the identity gap on 08-22, the
# "byte-mirror" that was not one on 08-28, and the senior-advisor framing on 08-29. Every time, the
# same homeowner could get two different emails depending on which surface reached them.
#
# Comparing two hand-maintained copies is what failed. So the JS does not get a copy at all: the
# body below is rendered ONCE in Python with sentinel tokens where the per-lead values go, baked
# into the board at build time (foreclosure_leads.py, beside the __IDENT_EN__ substitution), and
# both sides then run the same trivial token->value replacement. Drift is not caught, it is
# structurally impossible: there is one string, and it comes from this file.
#
# chr(1) is the token fence: it cannot occur in prose, survives str.strip(), it
# JSON-encodes to a plain backslash-u0001 escape that is legal in JSON and in a JS string
# literal, and _fmt_date() passes an unparseable date string straight through - which is what
# lets the DATE token survive into the template instead of being formatted away at bake time.
# ---------------------------------------------------------------------------------------------
TOK = {
    'first':   '\x01FIRST\x01',
    'date':    '\x01DATE\x01',
    'signer':  '\x01SIGNER\x01',
    'phone':   '\x01PHONE\x01',
    'company': '\x01COMPANY\x01',
    'sig':     '\x01SIG\x01',
    'ident':   '\x01IDENT\x01',
}

# Tokens that MUST survive into the template. A template missing one of these would ship a fixed
# name, date, phone or company to every homeowner on the board, which is the exact class of silent
# failure this whole mirror exists to prevent - so both renderers assert it before using the string.
# 'signer' is deliberately NOT in this list: email_body_template() passes a sig_block, and a
# sig_block REPLACES the signer/company/phone sign-off triple, so the signer token has nothing to
# mark. Caught the first time this baked, by the assertion below.
TOK_REQUIRED = ('first', 'date', 'company', 'phone', 'sig', 'ident')


def missing_tokens(tpl):
    """Which required tokens are absent from `tpl`. Empty tuple means the template is usable."""
    s = str(tpl or '')
    return tuple(k for k in TOK_REQUIRED if TOK[k] not in s)


def email_body_template(identity=None):
    """email_body() with every per-lead value replaced by a sentinel. Feed to fill()."""
    return email_body(first=TOK['first'], sale_date=TOK['date'], signer=TOK['signer'],
                      phone=TOK['phone'], company=TOK['company'],
                      identity=TOK['ident'] if identity is None else identity,
                      sig_block=TOK['sig'])


def fill(tpl, first='', date='', signer='', phone='', company='', sig='', ident=''):
    """Render a template from email_body_template(). Mirrored verbatim by genEmail()'s _alexFill().

    An empty first name collapses the greeting to a bare "Hi," rather than shipping a visible
    mail-merge hole; genEmail has always done that and a homeowner reading "Hi [First Name],"
    stops reading.
    """
    tpl = str(tpl or '')
    if not first:
        tpl = tpl.replace('Hi %s,' % TOK['first'], 'Hi,')
    for k, v in (('first', first), ('date', date), ('signer', signer), ('phone', phone),
                 ('company', company), ('sig', sig), ('ident', ident)):
        tpl = tpl.replace(TOK[k], str(v or ''))
    return tpl


# ---------------------------------------------------------------------------------------------
# SUBJECT — Alejandro's URGENT framing as a PREFIX on the tail replies.py needs.
#
# The tail is not decoration. replies.py PASS-2 recovers which lead a reply belongs to with
# re.search(r'property at\s+(.+)$', subj) — ANCHORED TO END OF STRING — and finds candidates over
# IMAP with the substring SUBJ_TAG = 'Regarding your property at'. A prefix survives both. Anything
# appended AFTER the street becomes part of the captured address, matches no lead, and every reply
# from an address not already on file silently stops being attributed.
# ---------------------------------------------------------------------------------------------
SUBJ_TAG_EN = 'Regarding your property at'
SUBJ_TAG_ES = 'Referente a su propiedad en'


def outreach_subject(street, sale_date='', td=False, lang='en', style='measured'):
    """'URGENT: Foreclosure sale 09/22/2026 - Regarding your property at 12535 SW 33 ST'.

    style='measured' drops the URGENT prefix and restores the pre-2026-08-30 date tag. See the
    SUBJECT_STYLE comment in outreach_email.py for the bounce-rate numbers behind that choice.
    The date is passed through AS THE BOARD HOLDS IT — no reformatting — because the subject is
    also what mail_sent.json, the Proof Sheet and replies.py read back.
    """
    d = str(sale_date or '').strip()
    urgent = (str(style or 'urgent').lower() != 'measured')
    if lang == 'es':
        tag = ((('Subasta de tax deed ' if td else 'Fecha de subasta ') + d + ' - ') if d else '')
        return ('URGENTE: ' if urgent else '') + tag + SUBJ_TAG_ES + ' ' + str(street or '')
    if urgent:
        tag = ((('Tax deed sale ' if td else 'Foreclosure sale ') + d + ' - ') if d else '')
        return 'URGENT: ' + tag + SUBJ_TAG_EN + ' ' + str(street or '')
    tag = ((('Tax deed sale ' if td else 'Sale date ') + d + ' - ') if d else '')
    return tag + SUBJ_TAG_EN + ' ' + str(street or '')


# ---------------------------------------------------------------------------------------------
# SMS — same offer, compressed. Statutory core + STOP.
# ---------------------------------------------------------------------------------------------
def sms(first='', sale_date=None, signer=SIGNER, phone=PHONE, company=COMPANY):
    ds = _short_date(sale_date)
    who = ('%s, ' % first) if first else ''
    when = ('Your home is scheduled for foreclosure auction %s' % ds if ds
            else 'Your home is scheduled for foreclosure auction')
    return ("%sI'm %s with %s. %s. Free 15-min call, no obligation - I'll lay out every option you "
            "have. Call/text %s. We're not the government or your lender; your lender may not "
            "agree to change your loan. Reply STOP to opt out."
            % (who, signer, company, when, phone))


# ---------------------------------------------------------------------------------------------
# LETTER — handwritten length, same offer
# ---------------------------------------------------------------------------------------------
def letter(first='', sale_date=None, signer=SIGNER, phone=PHONE, company=COMPANY):
    ds = _fmt_date(sale_date)
    when = ("Your home is scheduled for foreclosure auction on %s." % ds if ds
            else "I saw the foreclosure filing on your home.")
    return '\n'.join([
        '%s,' % (first or '[First Name]'),
        '',
        "I know this is a stressful time and you're getting a lot of mail, so I'll be brief.",
        '',
        # SWAP 2, letter. "any equity goes with it" is the same false statement as the email.
        when + " If nothing changes it can be sold to the highest bidder, and the choices you "
        "have right now go with it. It doesn't have to go that way.",
        '',
        "I'm %s with %s. I'm offering you a free 15-minute call, no obligation - I'll review your "
        "situation and lay out every option you have. No tricks, no fees, no pressure." % (signer, company),
        '',
        "Call or text me at %s." % phone,
        '',
        D.mars_part('govt', 'may_agree', 'may_stop', lang='en', company=company, as_html=False),
        '',
        "If you'd rather I didn't write again, call and say stop.",
        '',
        signer,
        phone,
    ])


def selftest():
    """The disclosure must be present on every channel, and SMS must fit two segments."""
    d = dt.date(2026, 9, 16)
    fails = []
    for name, body, need in (
        ('email_long', email_body('Maria', d), ('lender may not agree', 'stop doing business')),
        ('email_short', email_body_short('Maria', d), ('lender may not agree', 'stop doing business')),
        ('letter', letter('Maria', d), ('lender may not agree', 'stop doing business')),
        ('sms', sms('Maria', d), ('lender may not agree', 'STOP')),
    ):
        for m in need:
            if m not in body:
                fails.append('%s: missing %r' % (name, m))
    s = sms('Maria', d)
    if len(s) > 320:
        fails.append('sms %d chars (>320 = 3 segments)' % len(s))
    print('outreach_copy selftest: %s' % ('OK' if not fails else 'FAILED'))
    for f in fails:
        print('   !', f)
    return not fails


if __name__ == '__main__':
    import sys
    if '--selftest' in sys.argv:
        sys.exit(0 if selftest() else 1)
    d = dt.date(2026, 9, 16)
    print('SUBJECT (long):', email_subject(d))
    print('SUBJECT (short):', email_subject(d, short=True))
    print('\n' + '=' * 88 + '\n' + email_body_short('Ann', d))
    print('\n' + '=' * 88 + '\nSMS (%d chars)\n' % len(sms('Ann', d)) + sms('Ann', d))
    print('\n' + '=' * 88 + '\n' + letter('Ann', d))
