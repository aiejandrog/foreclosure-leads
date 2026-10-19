"""Has the hard-bounce list caught up with an address's last send? Follow-ups ask before they go.

WHY (2026-10-19). Follow-ups reached two recipients whose earlier emails had already hard-bounced,
and both bounced again. Every follow-up path (/send, cadence, outreach_email) does check
bounced_emails.json, but that file is only as current as the last bounces.py run, and nothing
compared the two: a follow-up went out whenever the address was not on the list, including when
the list had simply never seen the notice for the first email. bounces.py runs at 05:30 and in
the 07:15 sync; a day it failed (no network, standby, an IMAP error) or a mailbox it never opened
left every bounce since then invisible, and a follow-up read that silence as "delivered".

THE RULE. A follow-up to an address may go only when a bounce scan that finished without error
STARTED at least SETTLE_HOURS after this system last mailed that address. Otherwise the step is
held, not consumed, and goes out after the next clean scan. An address never mailed (a first
touch) is not this module's business; the first-touch pause is separate and unchanged.

    bounce_scan_status.json   written by bounces.py, gitignored; counts and mailbox names only
    DEALFLOW_BOUNCE_SETTLE_H  hours a notice is given to arrive (default 2)
    DEALFLOW_BOUNCE_SCAN_GATE=0  turns the hold off (the bounce list itself still applies)

A missing or unreadable status file holds every follow-up: no record of a clean scan is not
evidence that nothing bounced.
"""
import datetime as dt
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS = 'bounce_scan_status.json'
SETTLE_HOURS = 2.0


def _settle():
    try:
        return max(0.0, float(os.environ.get('DEALFLOW_BOUNCE_SETTLE_H', SETTLE_HOURS)))
    except ValueError:
        return SETTLE_HOURS


def gate_on():
    return os.environ.get('DEALFLOW_BOUNCE_SCAN_GATE', '1').strip() != '0'


def _parse(s):
    """Aware UTC datetime from an ISO string, or None."""
    try:
        t = dt.datetime.fromisoformat(str(s).strip())
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc)


def write_status(here, rec):
    """Atomic write. rec: started_utc, finished_utc, ok, mailboxes, failed, notices, hard, new, days."""
    path = os.path.join(here or HERE, STATUS)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def last_clean_scan(here=None):
    """(started_utc, '') for the last scan that finished without error, or (None, why)."""
    path = os.path.join(here or HERE, STATUS)
    if not os.path.exists(path):
        return None, 'no bounce scan on record (%s missing)' % STATUS
    try:
        with open(path, encoding='utf-8') as fh:
            st = json.load(fh)
    except Exception as e:
        return None, '%s is unreadable (%s)' % (STATUS, type(e).__name__)
    if not isinstance(st, dict):
        return None, '%s is not an object' % STATUS
    t = _parse(st.get('last_ok_started_utc'))
    if t is None:
        return None, 'no bounce scan has finished cleanly yet'
    return t, ''


def last_mailed_utc(rows, addr):
    """When this ledger last mailed addr (To or Bcc), as aware UTC, or None if never.

    A row with no ts_utc counts as the END of its day, so an old row can only hold, never pass."""
    a = (addr or '').strip().lower()
    if not a:
        return None
    best = None
    for e in rows or ():
        if not isinstance(e, dict) or e.get('ch') != 'email' or not e.get('message_id'):
            continue
        got = [x.strip().lower() for x in [e.get('to') or ''] + str(e.get('bcc') or '').split(',')]
        if a not in got:
            continue
        t = _parse(e.get('ts_utc')) if e.get('ts_utc') else None
        if t is None:
            try:
                d = dt.date.fromisoformat(str(e.get('d') or '')[:10])
            except ValueError:
                continue
            t = dt.datetime.combine(d + dt.timedelta(days=1), dt.time(), dt.timezone.utc)
        if best is None or t > best:
            best = t
    return best


def followup_hold(addr, rows, here=None):
    """'' when a follow-up to addr may rely on the bounce list, else the reason it is held."""
    if not gate_on():
        return ''
    sent = last_mailed_utc(rows, addr)
    if sent is None:
        return ''
    scan, why = last_clean_scan(here)
    if scan is None:
        return 'follow-up held: %s, so a bounce of the last email (%s UTC) may not be recorded' % (
            why, sent.strftime('%Y-%m-%d %H:%M'))
    need = sent + dt.timedelta(hours=_settle())
    if scan < need:
        return ('follow-up held: the last clean bounce scan (%s UTC) has not covered the last email '
                'to this address (%s UTC, plus %gh for a bounce to arrive). It goes after the next '
                'clean `python bounces.py`.' % (scan.strftime('%Y-%m-%d %H:%M'),
                                                 sent.strftime('%Y-%m-%d %H:%M'), _settle()))
    return ''
