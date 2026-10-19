"""Has the hard-bounce list caught up with an address's last send? Follow-ups ask before they go.

WHY (2026-10-19). Follow-ups reached two recipients whose earlier emails had already hard-bounced,
and both bounced again. Every follow-up path (/send, cadence, outreach_email) does check
bounced_emails.json, but that file is only as current as the last bounces.py run, and nothing
compared the two: a follow-up went out whenever the address was not on the list, including when
the list had simply never seen the notice for the first email.

THE RULE. A follow-up to an address may go only when, for EVERY mailbox a bounce of an earlier email
to it in the last LOOKBACK_DAYS could land in, plus the mailbox of its newest email (the ledger
row's `login`, else its `from`; a row with neither needs every mailbox on record), that mailbox's
own record shows:
  - a clean scan that STARTED at least SETTLE hours after the last email from that mailbox,
  - whose IMAP SINCE date is at least a day before that email (it searched the right days),
  - no failed run of that mailbox after it, and
  - that clean scan is no older than MAX_AGE hours (late bounces keep arriving);
and the bounce list the send just read still contains every address that scan wrote (by hash), so
an older copy of bounced_emails.json can never pair with a newer "scan complete".
Otherwise the step is held, not consumed. An address never mailed (a first touch) is not this
module's business; the first-touch pause is separate and unchanged.

    bounce_scan_status.json      written by bounces.py, gitignored; mailbox logins, dates, counts
                                 and SHA-256 hashes of the listed addresses (never an address)
    DEALFLOW_BOUNCE_SETTLE_H     hours a notice is given to arrive (default 2)
    DEALFLOW_BOUNCE_SCAN_MAX_AGE_H  how old the covering clean scan may be (default 36)
    DEALFLOW_BOUNCE_SCAN_GATE=0  turns the hold off (the bounce list itself still applies)

A missing, unreadable or older-format status file holds every follow-up: no record of a clean
scan is not evidence that nothing bounced.
"""
import datetime as dt
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS = 'bounce_scan_status.json'
VERSION = 2
SETTLE_HOURS = 2.0
MAX_AGE_HOURS = 36.0
LOOKBACK_DAYS = 30


def _env_hours(name, default):
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except ValueError:
        return default


def _settle():
    return _env_hours('DEALFLOW_BOUNCE_SETTLE_H', SETTLE_HOURS)


def _max_age():
    return _env_hours('DEALFLOW_BOUNCE_SCAN_MAX_AGE_H', MAX_AGE_HOURS)


def gate_on():
    return os.environ.get('DEALFLOW_BOUNCE_SCAN_GATE', '1').strip() != '0'


def addr_hash(a):
    return hashlib.sha256(str(a).strip().lower().encode('utf-8')).hexdigest()


def _parse(s):
    """Aware UTC datetime from an ISO string, or None."""
    try:
        t = dt.datetime.fromisoformat(str(s).strip())
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc)


def _norm(a):
    return str(a or '').strip().lower()


def write_status(here, rec):
    """Atomic write."""
    path = os.path.join(here or HERE, STATUS)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_status(here=None):
    """(status dict, '') or (None, why). Anything but a version-2 object is unusable."""
    path = os.path.join(here or HERE, STATUS)
    if not os.path.exists(path):
        return None, 'no bounce scan on record (%s missing)' % STATUS
    try:
        with open(path, encoding='utf-8') as fh:
            st = json.load(fh)
    except Exception as e:
        return None, '%s is unreadable (%s)' % (STATUS, type(e).__name__)
    if not isinstance(st, dict) or st.get('version') != VERSION or not isinstance(st.get('mailboxes'), dict):
        return None, '%s is not a version-%d record' % (STATUS, VERSION)
    return st, ''


def last_clean_scan(here=None):
    """(oldest per-mailbox clean start, '') across every mailbox on record, or (None, why). /health."""
    st, why = read_status(here)
    if st is None:
        return None, why
    ts = [_parse((m or {}).get('last_ok_started_utc')) for m in st['mailboxes'].values()]
    if not ts or any(t is None for t in ts):
        return None, 'a mailbox on record has never scanned cleanly'
    return min(ts), ''


def sends_by_mailbox(rows, addr):
    """{mailbox or '*': last send to addr from it, as aware UTC}. '*' = a row naming no mailbox.

    A row with no ts_utc counts as the END of its day, so an old row can only hold, never pass."""
    a = _norm(addr)
    out = {}
    if not a:
        return out
    for e in rows or ():
        if not isinstance(e, dict) or e.get('ch') != 'email' or not e.get('message_id'):
            continue
        got = [_norm(x) for x in [e.get('to') or ''] + str(e.get('bcc') or '').split(',')]
        if a not in got:
            continue
        t = _parse(e.get('ts_utc')) if e.get('ts_utc') else None
        if t is None:
            try:
                d = dt.date.fromisoformat(str(e.get('d') or '')[:10])
            except ValueError:
                continue
            t = dt.datetime.combine(d + dt.timedelta(days=1), dt.time(), dt.timezone.utc)
        mb = _norm(e.get('login') or e.get('from')) or '*'
        if mb not in out or t > out[mb]:
            out[mb] = t
    return out


def last_mailed_utc(rows, addr):
    s = sends_by_mailbox(rows, addr)
    return max(s.values()) if s else None


def _mailbox_hold(name, rec, sent, now):
    rec = rec or {}
    ok_t = _parse(rec.get('last_ok_started_utc'))
    stamp = sent.strftime('%Y-%m-%d %H:%M')
    if ok_t is None:
        return 'mailbox %s has no clean bounce scan on record' % name
    if rec.get('last_run_ok') is not True:
        return 'the last bounce scan of %s failed after its clean one' % name
    if ok_t < sent + dt.timedelta(hours=_settle()):
        return ('the last clean bounce scan of %s (%s UTC) has not covered the email of %s UTC plus %gh'
                % (name, ok_t.strftime('%Y-%m-%d %H:%M'), stamp, _settle()))
    try:
        since = dt.date.fromisoformat(str(rec.get('last_ok_since') or '')[:10])
    except ValueError:
        return 'the clean scan of %s records no search start date' % name
    # IMAP SINCE is a whole date in the server's zone; a day of margin keeps a UTC-evening send
    # from falling just outside a search that looks as if it starts the same day.
    if since > sent.date() - dt.timedelta(days=1):
        return ('the clean scan of %s searched from %s, too late to cover the email of %s UTC'
                % (name, since.isoformat(), stamp))
    if now - ok_t > dt.timedelta(hours=_max_age()):
        return ('the last clean bounce scan of %s (%s UTC) is older than %gh'
                % (name, ok_t.strftime('%Y-%m-%d %H:%M'), _max_age()))
    return ''


def followup_hold(addr, rows, here=None, bounced=None, now=None):
    """'' when a follow-up to addr may rely on the bounce list, else the reason it is held.

    bounced: the bounce-list address set THIS send already read for its verdict. The scan's
    harvested hashes must all be in it; otherwise the list predates the status and nothing is
    trusted. None = read bounced_emails.json here."""
    if not gate_on():
        return ''
    sends = sends_by_mailbox(rows, addr)
    if not sends:
        return ''
    last = max(sends.values()).strftime('%Y-%m-%d %H:%M')
    st, why = read_status(here)
    if st is None:
        return ('follow-up held: %s, so a bounce of the last email (%s UTC) may not be recorded'
                % (why, last))
    if bounced is None:
        bounced = _load_bounced(here)
        if bounced is None:
            return 'follow-up held: bounced_emails.json is unreadable'
    have = {addr_hash(x) for x in bounced}
    want = st.get('list_hashes')
    if not isinstance(want, list) or any(h not in have for h in want):
        return ('follow-up held: the bounce list this send read is older than the last scan record '
                '(%s lists addresses it does not have). Re-run `python bounces.py`.' % STATUS)
    now = now or dt.datetime.now(dt.timezone.utc)
    boxes = {_norm(k): v for k, v in st['mailboxes'].items()}
    # Every mailbox that mailed this address inside the lookback, and always the one that mailed
    # it last. A bounce of an email older than the lookback has long since arrived and been
    # harvested or not; an address that accepted a later, covered email is live.
    newest = max(sends.values())
    floor = now - dt.timedelta(days=LOOKBACK_DAYS)
    for mb, sent in sorted(sends.items()):
        if sent < floor and sent != newest:
            continue
        need = sorted(boxes) if mb == '*' else [mb]
        if not need:
            return 'follow-up held: no mailbox has ever been scanned for bounces'
        for name in need:
            why = _mailbox_hold(name, boxes.get(name), sent, now)
            if why:
                return 'follow-up held: %s. It goes after the next clean `python bounces.py`.' % why
    return ''


def _load_bounced(here):
    path = os.path.join(here or HERE, 'bounced_emails.json')
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding='utf-8') as fh:
            d = json.load(fh)
    except Exception:
        return None
    if isinstance(d, dict):
        return {_norm(k) for k in d}
    if isinstance(d, list):
        return {_norm(x) for x in d}
    return None
