"""entity — the ONLY authority on whether we may print an " LLC" entity claim.

WHY THIS EXISTS
Twice now the company name has been asserted to distressed homeowners without anyone checking the
register. 'Miami Solutions Group LLC' turned out to belong to another Florida company. Its
replacement, 'Biscayne Solutions Group', was un-gated on 2026-08-23 on a verbal confirmation that it
had been filed -- and a Sunbiz lookup the same day found no such entity. Both times the guard was a
hardcoded tuple that a human edited, which means it encoded a BELIEF, not a fact.

This module replaces the belief with evidence. `display_llc()` returns the full entity name only
when `entity_status.json` holds a strict, exact, ACTIVE Sunbiz match for that precise string.
Otherwise it strips the suffix and hands back a warning. **Fail-closed: no evidence, no claim.**
Nobody's say-so -- Alejandro's, Claude's, a future operator's -- can open the gate. Only the
register can, and `entity_check.py` is what reads it.

Claiming a registered entity that does not exist, to a homeowner in foreclosure, is a separately
actionable misrepresentation (FDUTPA + MARS 1015.3). That is the whole stake.

USAGE
    import entity
    name, doc, warn = entity.display_llc()
    if warn:
        print('WARNING: ' + warn)

`sender.json.llc` stays the CANONICAL entity string -- the legal pack needs it, and the attorney
instruments are drafted against it. Stripping happens at DISPLAY only. Do not collapse the two.
"""
import io
import json
import os
import re
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(HERE, 'entity_status.json')
SENDER_FILE = os.path.join(HERE, 'sender.json')

# A verification does not stay true forever. If entity_check.py stops running -- broken cron, dead
# scraper, moved machine -- the claim must decay rather than coast indefinitely on a stale success.
# 30 days is long enough that a transient outage never yanks the suffix mid print-run, and short
# enough that a silently dead checker cannot keep authorising the claim for a whole quarter.
MAX_AGE_DAYS = 30

# MANUAL DENY LIST. Belt to entity_check's braces: a name here is never printed with its suffix even
# if Sunbiz says ACTIVE. Use it to stop a name we own but have decided not to trade under. It cannot
# do the reverse -- there is deliberately no manual ALLOW, because a manual allow is exactly the
# mechanism that failed twice.
DENY = ()


def _norm(s):
    return re.sub(r'[^a-z0-9]+', '', str(s or '').lower())


def _strip_suffix(raw):
    return re.sub(r'\s*,?\s*(?:L\.?L\.?C\.?|INC\.?|CORP\.?)\s*$', '', str(raw or '').strip()).strip()


def sender():
    try:
        return json.load(io.open(SENDER_FILE, encoding='utf-8'))
    except Exception:
        return {}


def status():
    """The last verdict entity_check.py wrote, or {} if it has never run here."""
    try:
        return json.load(io.open(STATUS_FILE, encoding='utf-8'))
    except Exception:
        return {}


def _age_days(st):
    try:
        t = datetime.strptime(st.get('checked_utc', ''), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
    except Exception:
        return 1e9


def verified(raw=''):
    """True only for a strict, exact, ACTIVE, fresh match on THIS exact name."""
    raw = (raw or sender().get('llc') or '').strip()
    if not raw or _norm(_strip_suffix(raw)) in [_norm(d) for d in DENY]:
        return False
    st = status()
    return bool(st.get('verified')
                and st.get('status') == 'ACTIVE'
                and _norm(st.get('query')) == _norm(raw)
                and _age_days(st) <= MAX_AGE_DAYS)


def display_llc(override=''):
    """(display_name, doc_number, warning). Never returns a claim we cannot substantiate."""
    raw = (override or sender().get('llc') or '').strip()
    if not raw:
        return '', '', 'no company name set in sender.json'
    st = status()
    if verified(raw):
        return raw, st.get('doc', ''), ''

    bare = _strip_suffix(raw)
    if _norm(bare) in [_norm(d) for d in DENY]:
        return bare, '', ('%s is on the manual DENY list in entity.py. Printed WITHOUT an entity '
                          'suffix.' % bare)
    if not st:
        return bare, '', ('the entity has never been checked against Sunbiz on this machine. '
                          'Printed WITHOUT the LLC suffix. Run: python entity_check.py')
    if _norm(st.get('query')) != _norm(raw):
        return bare, '', ('sender.json says %r but the last Sunbiz check was for %r. Printed '
                          'WITHOUT the LLC suffix. Re-run: python entity_check.py'
                          % (raw, st.get('query')))
    if _age_days(st) > MAX_AGE_DAYS:
        return bare, '', ('the last Sunbiz check is %d days old (limit %d) -- the checker may have '
                          'stopped running. Printed WITHOUT the LLC suffix. Run: python '
                          'entity_check.py' % (_age_days(st), MAX_AGE_DAYS))
    # NOT_FOUND / INEXACT are absences, not statuses -- they get the fuller message below.
    if st.get('status') not in ('', 'ACTIVE', 'NOT_FOUND', 'INEXACT'):
        return bare, '', ('Sunbiz lists %s as %s, not ACTIVE. Printed WITHOUT the LLC suffix.'
                          % (st.get('matched') or raw, st.get('status')))
    near = ', '.join(st.get('neighbours') or [])[:120]
    return bare, '', ('%s is NOT in the Sunbiz index%s. It cannot be substantiated to a homeowner, '
                      'so it printed WITHOUT the LLC suffix. If the filing is new, the index lags '
                      'about a business day and the suffix returns by itself on the next refresh.'
                      % (raw, (' (nearest: %s)' % near) if near else ''))


if __name__ == '__main__':
    name, doc, warn = display_llc()
    print('sender.json llc : %r' % (sender().get('llc') or ''))
    print('display         : %r' % name)
    print('document number : %s' % (doc or '-'))
    print('verified        : %s' % verified())
    print('warning         : %s' % (warn or '(none)'))
