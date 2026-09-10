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
import re

# Placeholders the templates fall back to when a field is missing. Fine on the board, never in a
# message. Also catches any other SHOUTED bracket token, which is how every one of these is written.
_PLACEHOLDER = re.compile(r'\[[A-Z][A-Z0-9 _/&.-]{2,}\]')
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


def check(subj='', body='', to=''):
    """Every reason this message must not be sent, as a list of human sentences. Empty == sendable."""
    bad = []
    for label, text in (('subject', str(subj or '')), ('body', str(body or ''))):
        if not text.strip():
            bad.append('the %s is empty' % label)
            continue
        for m in set(_PLACEHOLDER.findall(text)):
            bad.append('the %s still contains the placeholder %s' % (label, m))
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
    return bad


def assert_sendable(subj='', body='', to=''):
    """Raise rather than transmit. Called from _smtp_send, which every send path goes through."""
    bad = check(subj, body, to)
    if bad:
        raise UnsendableError(
            'refusing to send to %s — %s. Fix the lead data or the template; the message was NOT '
            'sent and the step was NOT consumed.' % (to or '(unknown)', '; '.join(bad)))
    return True
