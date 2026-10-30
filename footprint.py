"""footprint -- keep the operators' personal identifiers out of everything that gets published.

WHY THIS EXISTS (2026-10-25)
The board (docs/index.html) and Call Mode (docs/call/) are mirrored to a PUBLIC repo. For weeks they
carried, in the clear and outside the encrypted payload: a home address (as a worked example in a
comment), a personal Gmail (inside a regex that healed a retired default), the advisor's Gmail (the
Brief Jesse button), a personal phone number (in a comment), and the operator's name, phone and
email (SENDER_DEFAULTS). Nothing flagged any of it, because the one guard that looked at emails
exempted "our own" addresses by name -- so the allow-list WAS the leak.

THE RULE
No real personal identifier is ever a literal in tracked code, in a template, or in a built page's
plaintext. Real values live in two LOCAL, UNTRACKED places:

  sender.json            the operator identity (name/phone/email/addr/title) -- already local
  footprint.local.json   everything else this module needs (gitignored; see below)

and reach the page only INSIDE the encrypted payload (side.sender / side.advisor / side.retired),
merged back by _applyPayload() on unlock. The local Desktop twin (never published) gets them written
straight in. What ships in the clear is blank.

footprint.local.json (gitignored, per machine -- the engine's laptop needs it):
  {
    "advisor_emails":       ["advisor@..."],      # who a Brief-Jesse / test send may reach
    "retired_emails":       ["old-default@..."],  # saved sender emails to heal away on devices
    "retired_addr_prefixes":["100 N Example Ave"],# saved sender addresses to heal away
    "warmup_peers":         ["peer@..."],         # extra warm-up recipients (warmup.py)
    "deny":                 ["any string"]        # must never appear in a published page
  }
Environment overrides (tests, CI): DEALFLOW_FOOTPRINT_JSON=<path>, DEALFLOW_ADVISOR_EMAILS=a,b.

THE GUARD
assert_clean(html, where) fails a build whose finished page carries (a) ANY consumer-mailbox address
(gmail/yahoo/...), (b) any advisor/retired/deny value from the local file, in plain OR regex-escaped
form, or (c) a retired address in any spacing. It is a tripwire: it stops the publish and names the
offender, it never rewrites. With no local file it still enforces (a).
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(HERE, 'footprint.local.json')

# Consumer mailboxes. A team inbox on the company domain is a different (and deliberate) thing; a
# personal mailbox on a published page never is. `\\?\.` also catches the dot escaped inside a JS
# regex literal -- the exact spelling that hid a personal Gmail in plain sight on the live board.
CONSUMER_EMAIL = re.compile(
    r'[A-Za-z0-9._%+\-]+@(?:gmail|googlemail|yahoo|ymail|hotmail|outlook|live|msn|icloud|me|aol|'
    r'bellsouth|comcast|att|verizon|proton|protonmail)\\?\.(?:com|net|me)\b', re.I)

# What a locked page carries instead of the real operator identity: blank, on purpose. Blank is what
# every "is the sender set up?" check in the board already treats as missing, so an engine with no
# sender.json produces a board that refuses to mail rather than one that signs "Your Name".
BLANK_IDENTITY = {'name': '', 'phone': '', 'email': '', 'addr': '', 'title': ''}


def _path():
    return os.environ.get('DEALFLOW_FOOTPRINT_JSON') or LOCAL


_CACHE = {}


def local():
    """footprint.local.json, cached by mtime: signature_email() runs once per lead per send."""
    p = _path()
    try:
        m = os.path.getmtime(p)
    except OSError:
        return {}
    hit = _CACHE.get(p)
    if hit and hit[0] == m:
        return hit[1]
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        d = d if isinstance(d, dict) else {}
    except Exception:
        d = {}
    _CACHE[p] = (m, d)
    return d


def _list(key):
    v = local().get(key) or []
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def advisor_emails():
    """Lowercased addresses a test send / Brief Jesse may reach. Empty = nobody (fail closed)."""
    out = {x.lower() for x in _list('advisor_emails')}
    out |= {x.strip().lower() for x in os.environ.get('DEALFLOW_ADVISOR_EMAILS', '').split(',')
            if x.strip()}
    return out


def advisor_email():
    s = sorted(advisor_emails())
    return s[0] if s else ''


def retired():
    return {'email': [x.lower() for x in _list('retired_emails')],
            'addr': _list('retired_addr_prefixes')}


def warmup_peers():
    return _list('warmup_peers')


def private_identity():
    """The real operator identity, from the untracked sender.json. {} when it is not there."""
    try:
        import entity
        s = entity.sender() or {}
    except Exception:
        s = {}
    return {k: str(s.get(k) or '').strip() for k in BLANK_IDENTITY}


def sealed_side():
    """What rides INSIDE the encrypted payload (payload.side). Only non-empty parts are included."""
    side = {}
    ident = {k: v for k, v in private_identity().items() if v}
    if ident:
        side['sender'] = ident
    adv = advisor_email()
    if adv:
        side['advisor'] = adv
    ret = retired()
    if ret['email'] or ret['addr']:
        side['retired'] = ret
    return side


def js(obj):
    """JSON as a JS literal that cannot close a <script> block."""
    return (json.dumps(obj, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('&', '\\u0026'))


PUBLIC_IDENTITY_JS = js(BLANK_IDENTITY)
PUBLIC_RETIRED_JS = js({'email': [], 'addr': []})
PUBLIC_ADVISOR_JS = '""'


# Where each placeholder sits in tracker_template.html. A bare '""' appears all over real JS, so the
# twin swap is anchored to the whole statement, never to the literal alone.
_TWIN_SITES = (
    ('}, ', PUBLIC_IDENTITY_JS, ');', lambda: js(private_identity())),     # SENDER_DEFAULTS = Object.assign({...}, <here>);
    ('let SENDER_RETIRED = ', PUBLIC_RETIRED_JS, ';', lambda: js(retired())),
    ('var _JESSE = ', PUBLIC_ADVISOR_JS, ';', lambda: js(advisor_email())),
)


def twin_identity(html):
    """The LOCAL plaintext twin keeps the real identity inline (it is never published and has no
    payload to unseal it from). Each placeholder statement must be there exactly once or the build
    stops: a silent no-op would mean the operator's own board prints a blank letterhead."""
    for pre, pub, post, real in _TWIN_SITES:
        site = pre + pub + post
        if html.count(site) != 1:
            raise SystemExit('footprint: expected exactly one %r placeholder in the template, found '
                             '%d -- refusing to write a twin with a blank identity.'
                             % ((pre + pub[:24]), html.count(site)))
        html = html.replace(site, pre + real() + post)
    return html


# What may sit BETWEEN the characters of a value without hiding it: punctuation, whitespace, and the
# regex escapes that a JS regex literal uses for them (`\\s`, `\\.`, `\\b` ...). "100 NW 50" must match
# `100\\s+N\\.?\\s*W\\.?\\s+50`, where even the two letters of NW are separated by escape characters.
_NOISE = r'(?:\\[sSdDwWbB]|[^A-Za-z0-9]){0,8}?'


def needle_regex(needle):
    """A matcher for a value in any spacing, punctuation or regex-escaped spelling.

    Only the alphanumerics of the value are significant; between any two of them the page may carry
    noise (see _NOISE). It is a PREFIX match on the right (no trailing boundary, so `50` finds
    `50th`) and anchored on the left (so `100` does not find `1100`)."""
    chars = re.sub(r'[^A-Za-z0-9]', '', needle or '')
    if len(chars) < 4:
        return None
    return re.compile(r'(?<![A-Za-z0-9])' + _NOISE.join(re.escape(c) for c in chars), re.I)


def _forbidden():
    out = []
    for v in list(advisor_emails()) + [x for x in _list('retired_emails')] + _list('deny'):
        out.append(('value', v))
        if '@' in v:
            out.append(('mailbox name', v.split('@', 1)[0]))
    for v in _list('retired_addr_prefixes'):
        out.append(('address', v))
    return out


def problems(html):
    found = []
    seen = set()
    for m in CONSUMER_EMAIL.finditer(html):
        e = m.group(0).lower().replace('\\', '')
        if e.endswith('@example.com') or e in seen:
            continue
        seen.add(e)
        found.append('consumer mailbox in plaintext: %s' % re.sub(r'^(.{2}).*(@.*)$', r'\1***\2', e))
    for kind, v in _forbidden():
        rx = needle_regex(v)
        if rx is not None and len(v) >= 4 and rx.search(html):
            found.append('%s from footprint.local.json is in the page (%s***)' % (kind, v[:2]))
    return found


def assert_clean(html, where, exc=SystemExit):
    p = problems(html)
    if p:
        raise exc('%s: refusing to publish -- personal identifier(s) in the page outside the encrypted '
                  'payload: %s. Move the value into sender.json / footprint.local.json (see '
                  'footprint.py) and rebuild.' % (where, '; '.join(p[:6])))
