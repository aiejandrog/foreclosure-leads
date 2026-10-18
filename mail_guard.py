#!/usr/bin/env python
"""mail_guard — the last check before an outreach email leaves the building.

WHY THIS EXISTS
Two messages that actually reached homeowners, found by reading the Sent folder on 2026-09-10:

  2026-09-07  to a real owner
      Subject: "Re: Regarding your property at"
      Body:    "Hi Figueroa, This is my last note about . If the case is already handled..."

  2026-08-01
      Body:    "Hi Milagros, My name is [YOUR NAME]. I am a local Miami home buyer..."

Neither is a delivery failure, a bounce, or anything the send path could notice. Both are a
SUCCESSFUL send of a broken message, and to the person receiving it the effect is worse than
silence: a foreclosure letter about no address at all, from nobody, is the exact shape of the scam
mail these owners are already drowning in. The whole outreach posture — "I am not your lender, not
the government, not a rescue company" — is credibility, and one message like this spends it.

THE TWO ROOT CAUSES, both of which are now fixed upstream as well:
  * `addr or 'your property'` runs BEFORE the address is cleaned, and the cleaner can return an
    empty string for a whitespace-or-comma-only address. The fallback then never fires. safe_addr()
    below closes that by falling back AFTER cleaning.
  * `snd.get('name') or '[YOUR NAME]'` is a reasonable default for a board PREVIEW and is never a
    reasonable thing to transmit.

Upstream fixes are the real repair; this module is the backstop that makes the class of failure
impossible to ship again, because it sits at the one place every path must pass through
(_smtp_send, in both outreach_email and send_server). It refuses the send rather than sanitising it:
a message with a hole in it is a message whose data is wrong, and quietly papering over that would
send a subtly wrong letter instead of an obviously broken one.
"""
import contextlib
import re
import threading

# Placeholders the templates fall back to when a field is missing. Fine on the board, never in a
# message. Also catches any other SHOUTED bracket token, which is how every one of these is written.
_PLACEHOLDER = re.compile(r'\[[A-Z][A-Z0-9 _/&.-]{2,}\]')
# Every other bracketed template token, in any case and in square or angle brackets: "[Your Name]",
# "[your name]", "[Agent's Name]", "[Your Phone #]", "<Your Name>". 2026-10-18: the Title Case
# pattern this replaced let "[Your name]" and "<Your Name>" through. A URL or an address in angle
# brackets ("<https://...>", "<me@x.com>") does not match: ':' and '@' are not in the token.
_ANY_PLACEHOLDER = re.compile(r"\[[^\W\d_][\w '\u2019#&/.\-]{1,40}\]|<[^\W\d_][\w '\u2019#&/.\-]{1,40}>")
# safe_street's fallback landing in the subject: "Regarding your property at your property".
_DEGENERATE_SUBJECT = re.compile(
    r'(?:property at|propiedad en)\s+(?:your property|su propiedad)\s*$', re.I)
# The same fallback used as the property itself. "about your property so I'll be brief"
# does not match: the next word has to be the end of the phrase.
_DEGENERATE_BODY = re.compile(
    r'\b(?:about|sobre)\s+(?:your property|su propiedad)\s*[.\n]'
    r'|\bagainst your property\b', re.I)
_BARE_GREETING = re.compile(r'^(?:Hi|Hola),\s*$', re.I)
_OUTREACH_SUBJECT = re.compile(
    r'Regarding your property|Referente a su propiedad|Sale date|Fecha de subasta', re.I)
_EMAIL_ADDR = re.compile(r'[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}', re.I)
# An unrendered format slot: "{addr}", "%s", "$addr". A literal brace is otherwise vanishingly rare
# in this copy, and a stray one is worth a human look either way.
_FORMAT_SLOT = re.compile(r'\{[a-z_][a-z0-9_]*\}|%[sd]\b|\$\{?[a-z_]+\}?', re.I)
# A VALUE THAT RENDERED EMPTY. The give-away is a preposition running straight into its own
# punctuation: "note about .", "property at ,", "sale on ." Deliberately narrow — it must not fire
# on ordinary prose, so it is anchored to the handful of words these templates put a value after.
_EMPTY_SLOT = re.compile(r'\b(?:about|at|on|for|regarding|of)\s+[.,;:!?]')
# Same hole, at the end of a line, which is how it shows up in a subject: "...your property at"
_EMPTY_TAIL = re.compile(r'\b(?:about|at|regarding)\s*$', re.I | re.M)
# "$." "$," "$" at the end — a money slot that rendered blank reads as an offer of nothing.
# The lookahead deliberately excludes whitespace: "US$ 100" is somebody typing, not a hole, and
# these rules also run over messages a human composed on the board.
_EMPTY_MONEY = re.compile(r'\$\s*(?=[.,;:!?)]|$)')


# An opt-out the recipient can actually see in the words of the message. CAN-SPAM wants the notice
# to be clear and conspicuous; a List-Unsubscribe header alone renders as the mail client's own
# button, which is good but is not under our control and does not always show.
# Both languages: the Spanish bodies carry their own sentence and an English-only pattern would
# read them as having none. Accented and unaccented both, because the bodies are written without
# accents and a human composing on the board will type them.
_OPTOUT_SENTENCE = re.compile(r'\bunsubscribe\b|\bopt[\s-]?out\b|\btake you off\b'
                              r'|\bremove you\b|\bstop receiving\b'
                              r'|\bquitar\b|\bno recibir m[aá]s\b'
                              r'|\bcancele su suscripci[oó]n\b|\bsaco de la lista\b'
                              # The approved line (2026-09-26): outreach_copy.OPTOUT_LINE_EN / _ES.
                              r"|\bwon['\u2019]?t reach out again\b|\bno lo vuelvo a contactar\b", re.I)


def unsubscribe_header(login, url=''):
    """The List-Unsubscribe value for a message, or '' when there is nothing to offer.

    Two arms, https first per RFC 8058 when a page exists. The mailto arm is the one that works
    today: replies.py scans that mailbox and is_stop_text() matches "unsubscribe" outright, so an
    unsubscribe reply reaches optouts.json the same night with no new infrastructure.

    `login`, not the lane alias: mail sent AS alejandro@bsgfl.com is a Send-mail-as alias, and
    whether that domain also RECEIVES into the scanned mailbox depends on Workspace domain-alias
    config this code cannot see. The login always receives.
    """
    arms = []
    if url:
        arms.append('<%s>' % str(url).strip())
    if login:
        arms.append('<mailto:%s?subject=unsubscribe>' % str(login).strip().lower())
    return ', '.join(arms)


def one_click_post(url=''):
    """RFC 8058's companion header, valid ONLY beside an https arm. A mailto-only List-Unsubscribe
    with this header set advertises a POST endpoint that does not exist."""
    return 'List-Unsubscribe=One-Click' if url else ''


def require_sender_address(snd, where='sender.json'):
    """Raise unless the sender config carries the physical mailing address CAN-SPAM requires.

    _sig() builds the signature by dropping every empty field, so a missing `addr` does not look
    like a hole to check() below -- the message reads as a perfectly ordinary letter that happens
    to omit the one line 15 U.S.C. 7704(a)(5) makes mandatory. Nothing else in the system would
    ever say so. An EMPTY config is left alone: that is the dry-run/preview path, and the
    placeholder name it produces is refused by check() anyway.
    """
    if not snd:
        return True
    if not str(snd.get('addr') or '').strip():
        raise UnsendableError(
            'refusing to send: %s has no "addr" field, so the message would go out without the '
            'physical mailing address CAN-SPAM requires on every commercial email. Add the '
            'business address to %s.' % (where, where))
    return True


class UnsendableError(Exception):
    """Raised instead of transmitting a message with a hole in it."""


def safe_addr(raw, fallback='your property'):
    """A property address fit to put in front of an owner, or the fallback. NEVER empty.

    The board stores "455 NE 210 TER, MIAMI, FL- 33179": a stray hyphen after the state and a
    shouted city, which is the most machine-looking thing in the message. Tidy that, then decide
    whether anything survived — in that ORDER. Doing it the other way round is what shipped
    "my last note about ." to a homeowner: `raw or fallback` passed a whitespace-only address
    through as truthy, and the cleaner's own `if p` filter then dropped every part of it.
    """
    s = str(raw or '').strip()
    if s:
        s = re.sub(r',\s*([A-Z]{2})-\s*', r', \1 ', s)
        parts = [p.strip() for p in s.split(',')]
        if len(parts) >= 2 and parts[1].isupper():
            parts[1] = parts[1].title()
        s = ', '.join(p for p in parts if p)
    # strip anything that is punctuation-only, e.g. a lone "," or "-"
    if not re.search(r'[A-Za-z0-9]', s):
        return fallback
    return s


def safe_street(raw, fallback='your property'):
    """Just the street line, for a subject. Never empty, never a bare comma."""
    s = safe_addr(raw, fallback)
    if s == fallback:
        return fallback
    head = (s.split(',')[0] or '').strip()
    return head if re.search(r'[A-Za-z0-9]', head) else s


def merge_issues(subj='', body='', to=''):
    """Holes check() did not name: a blank street that fell back to "your property", a
    bare "Hi,", a placeholder in [ ] or < > in any case, or more than one homeowner on To.

    Empty means sendable. Legitimate prose ("about your property so I'll be brief") and a
    bare greeting on a note whose subject is not an outreach subject are not holes.
    """
    bad = []
    subj_s = str(subj or '')
    body_s = str(body or '')
    for label, text in (('subject', subj_s), ('body', body_s)):
        for m in sorted(set(_ANY_PLACEHOLDER.findall(text))):
            bad.append('the %s still contains the placeholder %s' % (label, m))
    if _DEGENERATE_SUBJECT.search(subj_s.strip()):
        bad.append('the subject names the fallback "your property" instead of a street')
    m = _DEGENERATE_BODY.search(body_s)
    if m:
        bad.append('the body names the fallback "your property" instead of a street near %r'
                   % m.group(0).strip())
    first = (body_s.splitlines() or [''])[0].strip()
    if _BARE_GREETING.match(first) and _OUTREACH_SUBJECT.search(subj_s):
        bad.append('the greeting is only %r on an outreach subject' % first)
    addrs = _EMAIL_ADDR.findall(str(to or ''))
    if len(addrs) > 1:
        bad.append('the To line has %d recipients; one homeowner per message' % len(addrs))
    return bad


def check(subj='', body='', to='', unsub=None):
    """Every reason this message must not be sent, as a list of human sentences. Empty == sendable.

    `unsub` is the List-Unsubscribe value the caller is ABOUT TO ATTACH, and passing it is what
    opts a caller into the opt-out rule. None means "I am not transmitting, do not judge this" —
    previews, the board's composer, anything inspecting a body. The send paths always pass it, so
    the rule is enforced exactly where a message actually leaves.

    That distinction is not pedantry. A hand-typed reply in an ongoing conversation ("no worries at
    all, hope you are doing ok... are you free Thursday?") goes through this same choke point, and
    an unsubscribe footer does not belong on one. The header, which _smtp_send sets on every
    message, covers those without putting sales-letter furniture in a human's reply.

    A message satisfies the requirement through EITHER the header or a sentence in the body, and
    fails only when it carries neither — the state every message was in before 2026-09-17.
    """
    bad = merge_issues(subj, body, to)
    for label, text in (('subject', str(subj or '')), ('body', str(body or ''))):
        if not text.strip():
            bad.append('the %s is empty' % label)
            continue
        for m in set(_PLACEHOLDER.findall(text)):
            why = 'the %s still contains the placeholder %s' % (label, m)
            if why not in bad:      # merge_issues already named it
                bad.append(why)
        for m in set(_FORMAT_SLOT.findall(text)):
            bad.append('the %s still contains the unrendered slot %s' % (label, m))
        m = _EMPTY_SLOT.search(text)
        if m:
            bad.append('the %s has a value that rendered EMPTY near %r' % (label, m.group(0).strip()))
        m = _EMPTY_TAIL.search(text.strip())
        if m and label == 'subject':
            bad.append('the subject ends on %r with nothing after it' % m.group(0).strip())
        if _EMPTY_MONEY.search(text):
            bad.append('the %s has a money figure that rendered empty' % label)
    if to and '@' not in str(to):
        bad.append('the recipient %r is not an email address' % to)
    if unsub is not None and not str(unsub).strip() and not _OPTOUT_SENTENCE.search(str(body or '')):
        bad.append('the message offers no way to opt out (no List-Unsubscribe header and no '
                   'unsubscribe sentence in the body)')
    return bad


def assert_sendable(subj='', body='', to='', unsub=None):
    """Raise rather than transmit. Called from _smtp_send, which every send path goes through."""
    bad = check(subj, body, to, unsub)
    if bad:
        raise UnsendableError(
            'refusing to send to %s — %s. Fix the lead data or the template; the message was NOT '
            'sent and the step was NOT consumed.' % (to or '(unknown)', '; '.join(bad)))
    return True


# ---------------------------------------------------------------------------------------------
# THE GATE TOKEN (2026-10-12)
#
# mail_guard sits in _smtp_send, "the one place every path must pass through" -- and that is exactly
# why it was the one check that ran when four homeowner replies were sent over SMTP by a script that
# called outreach_email._smtp_send directly. The stay gate, the case and same-owner opt-out checks,
# the bounce breaker, the caps and the dedupe all live in the CALLERS (send_server /send,
# outreach_email.main, cadence, reply_send), so a script that reached past them to the sender
# skipped every one, and the send was not in the ledger either: three homeowners with no stay check,
# on a laptop whose ledger never saw them.
#
# _smtp_send now refuses unless the caller says which gate it went through. A gated caller wraps
# its send in `with gated('send_server'):`. The one exception is mail to ourselves (the advisor or
# the sending login: a deal-desk digest, a smoke test), which has no homeowner to protect.
# This cannot stop a script that opens its own smtplib connection. It stops the one entry point
# this repo owns, and makes the bypass an explicit act instead of a default.
_TL = threading.local()
_OPERATOR_FALLBACK = frozenset({'celusa13@gmail.com'})


@contextlib.contextmanager
def gated(source):
    """Mark the current thread as sending through a gate named `source` (a non-empty label)."""
    label = str(source or '').strip()
    if not label:
        raise UnsendableError('gated() needs the name of the gate the send went through')
    prev = getattr(_TL, 'src', '')
    _TL.src = label
    try:
        yield label
    finally:
        _TL.src = prev


def current_gate():
    return getattr(_TL, 'src', '')


def _operator_addrs(login=''):
    own = set(_OPERATOR_FALLBACK)
    try:
        import send_server as _SS
        own |= {str(a).strip().lower() for a in getattr(_SS, 'ADVISOR_EMAILS', ())}
    except Exception:
        pass
    if login:
        own.add(str(login).strip().lower())
    return own


def require_gated(to='', bcc='', login=''):
    """Raise UnsendableError unless this send came through a gate, or goes only to us."""
    if current_gate():
        return True
    rc = [a.strip().lower() for a in re.split(r'[,;]', '%s,%s' % (to or '', bcc or '')) if a.strip()]
    if rc and all(a in _operator_addrs(login) for a in rc):
        return True
    raise UnsendableError(
        'refusing to send to %s -- _smtp_send was called outside a gate. Replies and one-off mail '
        'go through reply_send.py (stay check, do-not-contact check, ledger row); batches go through '
        'send_server /send or outreach_email.py. The message was NOT sent.' % (to or '(unknown)'))
