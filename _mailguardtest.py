"""No outreach email leaves with a hole in it.

WHY THIS EXISTS
Two messages that actually reached homeowners, found by reading the Sent folder on 2026-09-10:

  2026-09-07   Subject: "Re: Regarding your property at"
               Body:    "Hi Figueroa, This is my last note about . If the case is already handled..."
  2026-08-01   Body:    "Hi Milagros, My name is [YOUR NAME]. I am a local Miami home buyer..."

Neither is a bounce or a delivery failure. Both are a SUCCESSFUL send of a broken message, so every
existing check — SMTP result, the ledger, the daily cap, the bounce list — reported success. The
only place the defect existed was in the words the owner read.

That matters more here than in most products. These people are in foreclosure and are already being
buried in rescue-scam mail. A letter about no address at all, signed by nobody, is that silhouette
exactly, and the entire outreach posture ("I am not your lender, not the government, not a rescue
company") is credibility we spend one message at a time.

THE TWO ROOT CAUSES, fixed upstream and re-asserted here:
  1. `addr or 'your property'` ran BEFORE the address was cleaned. A whitespace- or comma-only
     address is TRUTHY, so the fallback never fired, and the cleaner's own `if p` filter then
     dropped every part and returned ''. safe_addr() decides AFTER cleaning.
  2. `snd.get('name') or '[YOUR NAME]'` is a fine default for a board preview and is never a
     thing to transmit.

AND THE THIRD BUG FOUND WITH THEM: cadence.py advanced each lead's step in MEMORY and wrote
cadence_state.json once, after the loop. A run that died, was killed, or hit an SMTP error threw
away the record of every mail it had already sent, and the next run re-sent them. One owner got the
identical step-2 follow-up on 2026-08-25 16:14 and again on 2026-08-26 12:39. State is written after
every send now, atomically.

Run: python _mailguardtest.py
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mail_guard as G  # noqa: E402

FAIL, PASS = [], []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok   ' if ok else '  FAIL ') + name + (('  — ' + str(detail)[:130]) if detail else ''))


def _raises(fn):
    """True when fn() refuses. A guard that fails open is the bug this whole file exists about."""
    try:
        fn()
        return False
    except G.UnsendableError:
        return True



# ---- 1. the two messages that actually shipped -------------------------------------------------
print('\nTHE REAL ONES')
real_bad = [
    ('the 09-07 blank address',
     'Re: Regarding your property at',
     'Hi Figueroa, This is my last note about . If the case is already handled, ignore this and '
     'I truly wish you well. If it is not, the door stays open.'),
    ('the 08-01 unfilled name',
     'Regarding your property at 1101 SW 122 AVE 307',
     'Hi Milagros, My name is [YOUR NAME]. I am a local Miami home buyer -- I buy houses.'),
]
for name, subj, body in real_bad:
    bad = G.check(subj, body, 'owner@example.com')
    rec('REFUSED: ' + name, bool(bad), (bad or ['NOT CAUGHT'])[0])

# ---- 2. real messages that must still go out ---------------------------------------------------
print('\nTHE GOOD ONES (verbatim from the Sent folder)')
real_ok = [
    ('Re: Regarding your property at 13950 SW 276 ST',
     'Hi Gamboa, This is my last note about 13950 SW 276 ST. If the case is already handled, '
     'ignore this and I truly wish you well. If it is not, the door stays open. Our senior '
     'advisor will still go over your options.'),
    ('Sale date 09/22/2026 - Regarding your property at 12535 SW 33 ST',
     'Hi Lim, Alejandro Gonzalez with Biscayne Solutions Group LLC again. I am not your lender, '
     'not the government, not a foreclosure-rescue company, and not an attorney. I wrote you a '
     'few days back about 7921 EAST DR 6.'),
    ('3 options before 09/22/2026 for 2081 UTOPIA DR',
     'Hi Manuel, Which one fits comes down to your numbers. I can walk you through all three '
     'against your actual property at 2081 UTOPIA DR, and it costs you nothing.'),
    # a hand-typed reply from the board — these go through the same choke point and must not trip
    ('Re: Regarding your property at 1533 W RIVER DR',
     'Hey Toni, no worries at all, hope you are doing ok. Sounds like today was a lot. I do not '
     'want to pile on but your sale date is Wednesday so we are on the clock either way. '
     'The payoff came to US$ 240,000 and the offer is $175,000. Are you free Thursday?'),
]
for subj, body in real_ok:
    bad = G.check(subj, body, 'owner@example.com')
    rec('SENDS: ' + subj[:52], not bad, bad)

# ---- 3. the classes, exhaustively --------------------------------------------------------------
print('\nEVERY HOLE')
holes = [
    ('unfilled name', 'Subject here', 'Hi, My name is [YOUR NAME].'),
    ('unfilled company', 'Subject here', 'I am with [YOUR COMPANY] and I buy houses.'),
    ('unfilled phone', 'Subject here', 'Call me at [YOUR PHONE] any time.'),
    ('unrendered slot', 'About {addr} before the auction', 'Following up on {addr}.'),
    ('printf slot', 'Subject here', 'Public records show %s is in foreclosure.'),
    ('empty value mid-sentence', 'Subject here', 'This is my last note about . The date is close.'),
    ('empty value after "at"', 'Subject here', 'your actual property at , and it costs nothing.'),
    ('subject ends on a preposition', 'Regarding your property at', 'A perfectly fine body here.'),
    ('empty money', 'Subject here', 'We can offer $. for the property.'),
    ('empty subject', '', 'A perfectly fine body here.'),
    ('empty body', 'A fine subject', '   '),
]
for name, subj, body in holes:
    rec(name, bool(G.check(subj, body, 'o@e.com')), (G.check(subj, body, 'o@e.com') or ['NOT CAUGHT'])[0])
rec('a bad recipient is refused', bool(G.check('s', 'b', 'not-an-address')))

# ---- 4. safe_addr never returns empty ----------------------------------------------------------
print('\nsafe_addr / safe_street')
for raw in ('', '   ', ',', ' , , ', ' - ', None, 0, [], '  ,  ,  '):
    a, st = G.safe_addr(raw), G.safe_street(raw)
    rec('degenerate %r -> a real string' % (raw,), a == 'your property' and st == 'your property',
        '%r / %r' % (a, st))
rec('the board format is tidied, not mangled',
    G.safe_addr('455 NE 210 TER, MIAMI, FL- 33179') == '455 NE 210 TER, Miami, FL 33179',
    G.safe_addr('455 NE 210 TER, MIAMI, FL- 33179'))
rec('street is the first line only', G.safe_street('455 NE 210 TER, MIAMI, FL- 33179') == '455 NE 210 TER',
    G.safe_street('455 NE 210 TER, MIAMI, FL- 33179'))
rec('a one-line address survives whole', G.safe_addr('13950 SW 276 ST') == '13950 SW 276 ST')
# the exact shape that produced the 09-07 send
rec('THE 09-07 INPUT: a rendered-empty address can no longer reach a template',
    G.safe_addr(' , ') == 'your property' and G.safe_street(' , ') == 'your property')

# ---- 5. the guard is actually wired into every send path ---------------------------------------
print('\nWIRING')
for mod in ('outreach_email.py', 'send_server.py'):
    src = io.open(os.path.join(HERE, mod), encoding='utf-8').read()
    body = src.split('def _smtp_send(', 1)[1] if 'def _smtp_send(' in src else ''
    head = body.split('sender = (from_addr or user)', 1)[0]
    rec('%s: _smtp_send refuses before it transmits' % mod, '_MG.assert_sendable(' in head)
src_c = io.open(os.path.join(HERE, 'cadence.py'), encoding='utf-8').read()
src_o = io.open(os.path.join(HERE, 'outreach_email.py'), encoding='utf-8').read()
rec('cadence builds its address through safe_addr', '_MG.safe_addr(lead.get(\'addr\'))' in src_c)
rec('cadence builds its subject through safe_street', '_MG.safe_street(' in src_c)
rec('outreach_email builds both through the shared helpers',
    '_MG.safe_addr(' in src_o and '_MG.safe_street(' in src_o)
rec('the old fallback-before-clean is gone from cadence',
    "addr = lead.get('addr') or 'your property'" not in src_c)

# ---- 6. cadence remembers a send the moment it happens -----------------------------------------
print('\nDUPLICATE SENDS')
rec('state is written after every send, not once at the end',
    src_c.count('_save_state(state)') >= 2, src_c.count('_save_state(state)'))
_loop = src_c.split('sent += 1', 1)[1].split('for a, n in sorted(capped', 1)[0] if 'sent += 1' in src_c else ''
rec('...and that write is INSIDE the send loop', '_save_state(state)' in _loop)
rec('the write is atomic (tmp + replace)',
    'os.replace(tmp, STATE)' in src_c and "STATE + '.tmp'" in src_c)
rec('a failed state write is announced, not swallowed',
    'WAS EMAILED but cadence_state.json could not be written' in src_c)
rec('the old single end-of-run dump is gone',
    "json.dump(state, open(STATE, 'w', encoding='utf-8'), indent=1)" not in src_c)

# BOTH cadence send paths offer a way out, now that no body carries a sentence (2026-09-22).
# The lane path inherits the header from send_server._smtp_send. The legacy fallback -- reached
# only when `import send_server` failed -- builds its message by hand and used to set nothing,
# which made it the one path that could mail a homeowner with no opt-out at all.
# Sliced to the LAST line of the branch, not to `sent += 1` further down: prose in this branch's
# own comments names that token, so slicing on it cut the region short and silently emptied the
# assertions below. End on the statement the branch actually ends with.
_fallback = src_c.split("msg = MIMEText(body, 'plain', 'utf-8')", 1)[-1]
_fallback = _fallback.split('smtp.send_message(msg)', 1)[0] + 'smtp.send_message(msg)'
rec('cadence\'s legacy fallback sets List-Unsubscribe',
    "msg['List-Unsubscribe'] = _unsub_hdr" in _fallback
    and '_MG.unsubscribe_header(cred[0])' in _fallback)
rec('...off the LOGIN, so the mailto arm lands where replies.py scans',
    '_MG.unsubscribe_header(cred[0])' in _fallback
    and '_MG.unsubscribe_header(alias' not in _fallback)
rec('...and the lane path still goes through send_server, which sets it too',
    '_ss._smtp_send(' in src_c
    and "msg['List-Unsubscribe'] = unsub" in io.open(
        os.path.join(HERE, 'send_server.py'), encoding='utf-8').read())

# ...and BOTH now run the pre-send guard. The fallback had no check of any kind: an unfilled
# placeholder or an empty-rendered value could leave on it, and both have reached real homeowners
# before ("My name is [YOUR NAME]", "my last note about ."), succeeding at the SMTP layer so
# nothing downstream could notice.
rec('cadence\'s legacy fallback runs the guard before handing the message to smtplib',
    '_MG.check(subj, body, ' in _fallback
    and _fallback.index('_MG.check(subj, body, ') < _fallback.index('smtp.send_message(msg)'))
# SKIP, not raise. Nothing in the send loop catches an exception, so a raise would abort the run
# and every other owner due today would go unmailed over one bad row. `continue` also delivers
# mail_guard's own promise literally -- "the message was NOT sent and the step was NOT consumed" --
# because it skips both `sent += 1` and the step advance, leaving the touch due for the next run.
_loop_body = src_c.split('    for c, s in active.items():', 1)[1].split('    if smtp:', 1)[0]
# Split defensively: if the guard is ever removed, the assertion above already fails, and this
# one must report a FAIL of its own rather than crashing the suite on an empty slice. A traceback
# reads as a broken test; the whole point here is that it reads as a broken send path.
_after_guard = _fallback.split('_MG.check(subj, body, ', 1)[1] if '_MG.check(subj, body, ' in _fallback else ''
rec('...and SKIPS the lead rather than raising, so one bad row cannot abort the batch',
    'continue' in _after_guard and '_MG.assert_sendable(' not in _fallback)
rec('...which is required, because no send in that loop is wrapped in try/except',
    not re.search(r'try:\s*\n\s*(mid = _ss\._smtp_send|smtp\.send_message)', _loop_body))
# The skip has to land BEFORE the counter and the step advance, or a refused touch would be
# recorded as delivered and never retried. Both live after the branch in the loop body.
rec('a refused step is NOT consumed (the guard precedes the counter and the step advance)',
    _loop_body.index('_MG.check(subj, body, ') < _loop_body.index('sent += 1')
    < _loop_body.index("s['step'] = step + 1"))

# ---- 7. every commercial message offers a way out ----------------------------------------------
# CAN-SPAM 15 U.S.C. 7704(a)(3). The guard itself is UNCHANGED and still refuses a send that has
# neither a header nor a body sentence -- that rule is what makes the 2026-09-22 wording removal
# safe to make at all. What changed is which of the two arms our own bodies use: the sentence is
# gone from every body by Alejandro's direction, so the List-Unsubscribe header is now the only
# thing satisfying this on a real send. These cases pin the guard, not the copy.
print('\nOPT-OUT')
rec('a send with neither header nor body line is refused',
    bool(G.check('Subject here', 'A perfectly ordinary sales letter.', 'a@b.com', unsub='')))
rec('...the List-Unsubscribe header alone satisfies it',
    not G.check('Subject here', 'A perfectly ordinary sales letter.', 'a@b.com',
                unsub='<mailto:x@y.com?subject=unsubscribe>'))
rec('...and a sentence in the body alone satisfies it',
    not G.check('Subject here', 'Reply unsubscribe and I will take you off the list.',
                'a@b.com', unsub=''))
# The reason the rule is opt-in rather than always-on: a hand-typed reply mid-conversation goes
# through the same choke point, and an unsubscribe footer does not belong on one.
rec('a caller that passes no unsub at all is not judged on it',
    not G.check('Re: your property', 'Hey Toni, are you free Thursday?', 'a@b.com'))

rec('the header carries the mailto arm that today actually suppresses',
    G.unsubscribe_header('alejandro@bsgflorida.com')
    == '<mailto:alejandro@bsgflorida.com?subject=unsubscribe>')
rec('...https first when a page exists (RFC 8058 order)',
    G.unsubscribe_header('a@b.com', 'https://bsgflorida.com/u')
    == '<https://bsgflorida.com/u>, <mailto:a@b.com?subject=unsubscribe>')
rec('no login, no header -- refused rather than silently unsubscribable',
    G.unsubscribe_header('') == '')
# One-click POSTs to the https arm. Advertising it beside a mailto-only header points Gmail at an
# endpoint that does not exist, which is worse than not advertising it.
rec('One-Click is advertised only beside an https arm', G.one_click_post('') == ''
    and G.one_click_post('https://bsgflorida.com/u') == 'List-Unsubscribe=One-Click')

# The word the mailto subject uses has to be one the existing detector already matches, or the
# unsubscribe lands in the inbox and nothing happens. This is the whole reason the arm is mailto.
import replies as _R
rec('"unsubscribe" is already an opt-out to replies.is_stop_text()', _R.is_stop_text('unsubscribe'))

# ---- 8. the physical mailing address ------------------------------------------------------------
# 15 U.S.C. 7704(a)(5). _sig() builds the signature by dropping empty fields, so a missing addr
# leaves a letter that reads completely normal and is missing the one line the statute requires.
print('\nPOSTAL ADDRESS')
rec('a populated sender.json without addr is refused',
    _raises(lambda: G.require_sender_address({'name': 'Alex', 'phone': '(786) 631-1823'})))
rec('...and with one is fine',
    G.require_sender_address({'name': 'Alex', 'addr': '1 Main St, Miami, FL 33101'}))
rec('an EMPTY config is left alone (preview/dry-run path)', G.require_sender_address({}))

# ---- 9. wiring: the headers reach the wire ------------------------------------------------------
print('\nOPT-OUT WIRING')
for mod in ('outreach_email.py', 'send_server.py'):
    src = io.open(os.path.join(HERE, mod), encoding='utf-8').read()
    fn = src.split('def _smtp_send(', 1)[1].split('\ndef ', 1)[0] if 'def _smtp_send(' in src else ''
    rec('%s: sets List-Unsubscribe' % mod, "msg['List-Unsubscribe'] = unsub" in fn)
    rec('%s: passes the header into the guard' % mod, 'unsub=unsub' in fn)
    rec('%s: One-Click only via one_click_post()' % mod,
        "msg['List-Unsubscribe-Post']" in fn and '_MG.one_click_post(' in fn)
src_o = io.open(os.path.join(HERE, 'outreach_email.py'), encoding='utf-8').read()
rec('outreach_email checks the postal address before a real send',
    '_MG.require_sender_address(snd)' in src_o)

# ---- 10. the copy itself carries the line -------------------------------------------------------
print('\nCOPY')
import outreach_copy as _OC
for _name in ('email_body', 'email_body_short'):
    _b = getattr(_OC, _name)(first='Maria', sale_date='2026-09-30')
    rec('%s() carries no opt-out sentence' % _name, not G._OPTOUT_SENTENCE.search(_b))
    rec('...and %s() leaves no run of blank lines behind' % _name,
        _b.endswith('\n') and not _b.endswith('\n\n'))
rec('the baked template carries none either (the board renders from this exact string)',
    not G._OPTOUT_SENTENCE.search(_OC.email_body_template()))
rec('...and still holds every required token', _OC.missing_tokens(_OC.email_body_template()) == ())
# UNSUB_URL is the one-line switch from the reply-based opt-out to the hosted page. Empty today
# because nothing in this system serves HTTP; a link that records nothing is a broken promise.
# 2026-09-22: _unsub() is neutralized for every language and every URL, the way _mars() already
# was. Pinned empty on purpose -- a sentence reappearing in body copy is a regression against an
# explicit instruction, not a nice surprise, and this is the assertion that would catch it.
rec('_unsub() renders nothing, in both languages, URL or not',
    _OC._unsub('') == '' and _OC._unsub(lang='es') == ''
    and _OC._unsub('https://bsgflorida.com/u') == '')


# ---- 11. every body, both languages, no opt-out sentence and no hole where it was ---------------
# outreach_email.py carries eight inline bodies of its own (early / follow-final / portfolio, EN and
# ES) that outreach_copy never touches, and two of them are the only Spanish a homeowner ever gets.
# Rendering is the only honest check here: the sentence was concatenated onto the signature inline
# (`sig + '\n\n' + _OC_unsub(lang)`), so removing it leaves a signature followed by a blank line
# unless _sig_unsub() collapses it. That is invisible in the source and obvious in a built body.
print('\nEVERY BODY')
import outreach_email as _OE
_SND = {'name': 'Alex Gonzalez', 'title': 'Acquisitions', 'llc': 'Biscayne Solutions Group LLC',
        'phone': '(786) 631-1823', 'email': 'alejandro@bsgflorida.com',
        'addr': '1 SE 2nd Ave Ste 2000, Miami, FL 33131', 'web': 'BSGflorida.com'}
_LEADS = {
    'early': {'case': 'CACE-25-001', 'addr': '123 MAIN ST, MIAMI, FL- 33101', 'owners': 'Maria Lopez'},
    'cold': {'case': 'CACE-25-002', 'addr': '456 OAK AVE, MIAMI, FL- 33102', 'owners': 'Jose Ruiz',
             'saleDate': '2026-09-30'},
    'tax deed': {'case': 'TD-25-003', 'addr': '789 PINE RD, MIAMI, FL- 33103', 'owners': 'Ana Diaz',
                 'saleDate': '2026-09-30', 'type': 'TD'},
}
for _label, _r in _LEADS.items():
    for _lang in ('en', 'es'):
        _body = _OE._compose_single(_r, _SND, lang=_lang)['body']
        rec('%s/%s carries no opt-out sentence' % (_label, _lang),
            not G._OPTOUT_SENTENCE.search(_body))
        # The bodies already ended on one blank line before the sentence was removed; what must
        # NOT appear is a RUN of them where the sentence used to sit.
        rec('%s/%s leaves no run of blank lines behind' % (_label, _lang),
            bool(_body.strip()) and not _body.endswith('\n\n\n'))
for _lang in ('en', 'es'):
    _body = _OE._compose_portfolio(_LEADS['cold'], [_LEADS['early']], _SND, lang=_lang)['body']
    rec('portfolio/%s carries no opt-out sentence' % _lang,
        not G._OPTOUT_SENTENCE.search(_body))
    rec('portfolio/%s leaves no run of blank lines behind' % _lang,
        bool(_body.strip()) and not _body.endswith('\n\n\n'))

# The bodies no longer name a keyword, but the DETECTOR is untouched and must stay that way: an
# owner who replies STOP or unsubscribe on their own is still honored, in both languages. That is
# the whole basis on which the sentence could be dropped, so it is pinned here.
rec('replies.is_stop_text() still honors an unprompted English opt-out',
    _R.is_stop_text('unsubscribe') and _R.is_stop_text('take me off your list')
    and _R.is_stop_text('please stop emailing me'))
rec('...and an unprompted Spanish one',
    _R.is_stop_text('QUITAR') and _R.is_stop_text('quitar') and _R.is_stop_text('no me escriba'))
rec('...and still does not read "stop the foreclosure" as an opt-out',
    not _R.is_stop_text('Can you stop the foreclosure?'))
# The header is now the ONLY recipient-facing opt-out on an email, so it is load-bearing.
rec('the List-Unsubscribe header is still built for a real login',
    G.unsubscribe_header('alejandro@bsgflorida.com').startswith('<mailto:'))
rec('...and a send carrying it passes the guard with no sentence in the body',
    not G.check('Foreclosure sale Sept 30 - your home', _OE._compose_single(
        _LEADS['cold'], _SND, lang='en')['body'], 'a@b.com',
        unsub=G.unsubscribe_header('alejandro@bsgflorida.com')))


print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAILED: ' + f)
sys.exit(1 if FAIL else 0)
