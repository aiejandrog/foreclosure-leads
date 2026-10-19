"""Harvest hard bounces out of the sending inbox and blacklist those addresses forever.

WHY THIS EXISTS (2026-08-02). The first real Morning Worker run sent 41 cold emails. Nineteen of
them bounced within four minutes:

    554 30 Sorry, your message to jtarara@aol.com cannot be delivered.
    This mailbox is disabled (554.30).

That is a ~46% hard-bounce rate. Two consequences, and the second is the one that ends the channel:

  1. Half the outreach never reached a human, so "no replies" was never a copy problem.
  2. Mailbox providers read bounce rate as the primary signal of a sender working a purchased or
     stale list. The tolerated ceiling is ~2%; sustained double-digit bounce rates get a sender
     throttled, then spam-foldered, then blocked -- and this is the operator's PERSONAL Gmail, the
     inbox his business actually runs on. Every additional send to a dead address makes the good
     addresses less likely to land.

The addresses come from BatchData skip-trace, which returns whatever it has on file including
decade-old AOL accounts. There is no way to tell a live address from a dead one before sending, so
the only defence is to never send twice: bounce once, blacklisted permanently.

    python bounces.py                 # scan the last 14 days, update bounced_emails.json
    python bounces.py --days 60       # wider sweep
    python bounces.py --dry-run       # report only, write nothing

The board reads bounced_emails.json at build time (make_tracker) and strips those addresses, so a
bounced lead falls back to phone/door instead of burning another send.
"""
import argparse, email, imaplib, json, os, re, sys
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'bounced_emails.json')     # gitignored: real homeowner addresses
sys.path.insert(0, HERE)
import replies as R                                  # reuse the audited gmail.key loader

# A hard bounce means the mailbox does not exist / is disabled -- permanent, never retry.
# A soft bounce (full mailbox, greylisting, temporary defer) is NOT included: those recover, and
# blacklisting a live owner over a transient 4xx would throw away a real lead.
HARD = re.compile(
    r'\b(550|551|553|554|5\.1\.1|5\.1\.10|5\.4\.1|554\.30)\b'
    r'|address (?:not found|couldn.t be found)'
    r'|mailbox (?:is )?(?:disabled|unavailable|not found)'
    r'|no such (?:user|mailbox|address)'
    r'|user (?:unknown|doesn.t exist)'
    r'|recipient (?:address )?rejected', re.I)
SOFT = re.compile(r'\b(4\d\d|4\.\d\.\d)\b|over quota|mailbox full|try again later|temporarily', re.I)

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')


def _body_text(msg):
    out = []
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
        try:
            payload = part.get_payload(decode=True) or b''
            out.append(payload.decode(part.get_content_charset() or 'utf-8', errors='replace'))
        except Exception:
            continue
    return '\n'.join(out)


def _failed_recipients(msg, text, sender_addr):
    """Prefer the RFC 3464 machine-readable part; fall back to scraping the human text."""
    hits = set()
    for part in msg.walk():
        if part.get_content_type() == 'message/delivery-status':
            for sub in (part.get_payload() or []):
                try:
                    fa = sub.get('Final-Recipient') or sub.get('Original-Recipient') or ''
                    action = (sub.get('Action') or '').lower()
                    if fa and action in ('', 'failed'):
                        m = EMAIL_RE.search(fa)
                        if m:
                            hits.add(m.group(0).lower())
                except Exception:
                    continue
    if not hits:
        for m in EMAIL_RE.finditer(text or ''):
            a = m.group(0).lower()
            # never blacklist ourselves or the postmaster reporting the failure
            if a == (sender_addr or '').lower():   continue
            if a.startswith(('mailer-daemon@', 'postmaster@')): continue
            # @mail.gmail.com is the MESSAGE-ID domain, not a recipient. The quoted original is
            # included in every bounce, so its Message-ID looks exactly like an address to a plain
            # regex — blacklisting one would be harmless but it inflates the "dead addresses"
            # count, which is the number this whole file exists to report honestly.
            if a.endswith(('googlemail.com', 'google.com', 'mail.gmail.com')): continue
            hits.add(a)
    return hits


def _mailboxes(account):
    """Every (user, password) to scan. --account or an env credential = that one mailbox.

    2026-10-19: this used to open only the FIRST mailbox in replies._cred_map(). A box holding both
    bsg_gmail.key and a gmail.key for a different login never read the second inbox's notices, so
    a bounce that landed there never reached bounced_emails.json and a follow-up went out to it."""
    if account or (os.environ.get('GMAIL_APP_PASSWORD') or '').strip():
        c = R.load_key(account or None)
        return [c] if c and c[0] and c[1] else []
    return [(u, pw) for u, pw in R._cred_map().items() if u and pw]


def _scan_mailbox(user, pw, since, found):
    """Add this mailbox's hard bounces to found. Returns (notices, soft_only). Raises on IMAP failure."""
    M = imaplib.IMAP4_SSL('imap.gmail.com')
    soft_only = 0
    try:
        M.login(user, pw)
        M.select('"[Gmail]/All Mail"', readonly=True)
        # Gmail and most providers sign notices as mailer-daemon; Microsoft (outlook/hotmail/live)
        # and some hosts send them as postmaster, which this search used to skip.
        typ, data = M.search(None, f'(SINCE {since} OR FROM "mailer-daemon" FROM "postmaster")')
        if typ != 'OK':
            raise imaplib.IMAP4.error('search returned %s' % typ)
        ids = (data[0].split() if data and data[0] else [])
        print(f'scanning {len(ids)} delivery notice(s) since {since} as {user}')
        for i in ids:
            typ, raw = M.fetch(i, '(RFC822)')
            if typ != 'OK' or not raw or not raw[0]:
                raise imaplib.IMAP4.error('fetch returned %s' % typ)
            msg = email.message_from_bytes(raw[0][1])
            text = _body_text(msg)
            if not HARD.search(text):
                if SOFT.search(text):
                    soft_only += 1          # transient — the address may still be good
                continue
            for addr in _failed_recipients(msg, text, user):
                if addr not in found:
                    found[addr] = {
                        'first_seen': str(date.today()),
                        'reason': (HARD.search(text).group(0) if HARD.search(text) else 'hard bounce'),
                    }
        return len(ids), soft_only
    finally:
        try: M.logout()
        except Exception: pass


def _record_scan(started, ok, boxes, failed, notices, hard, new, days):
    """bounce_scan_status.json: what the follow-up gate (bounce_scan.py) trusts. Counts only.
    A failed run keeps the last clean scan's time, so it never moves the gate forward."""
    import bounce_scan as BS
    prev = {}
    try:
        prev = json.load(open(os.path.join(HERE, BS.STATUS), encoding='utf-8'))
        prev = prev if isinstance(prev, dict) else {}
    except Exception:
        prev = {}
    rec = {'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(),
           'ok': bool(ok), 'mailboxes': boxes, 'failed': failed, 'notices': notices,
           'hard': hard, 'new': new, 'days': days,
           'last_ok_started_utc': started if ok else prev.get('last_ok_started_utc')}
    try:
        BS.write_status(HERE, rec)
    except Exception as e:
        print('!! could not write %s (%s); follow-ups stay held until a scan records'
              % (BS.STATUS, type(e).__name__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--account', default='')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    started = datetime.now(timezone.utc).isoformat()
    creds = _mailboxes(a.account)
    if not creds:
        print('NO GMAIL CREDENTIAL. Put user:app_password in gmail.key or set GMAIL_APP_PASSWORD.')
        if not a.dry_run:
            _record_scan(started, False, [], ['no credential'], 0, 0, 0, a.days)
        sys.exit(1)

    since = (date.today() - timedelta(days=a.days)).strftime('%d-%b-%Y')
    found, soft_only, notices, boxes, failed = {}, 0, 0, [], []
    for user, pw in creds:
        try:
            n, soft = _scan_mailbox(user, pw, since, found)
            notices += n
            soft_only += soft
            boxes.append(user)
        except Exception as e:
            # Keep going: the other mailboxes' bounces are still real and still get written. The
            # run is not clean, so the follow-up gate does not move past it.
            print(f'IMAP failed for {user}: {type(e).__name__}: {str(e)[:120]}')
            failed.append(user)

    try:
        prior = load_bounce_list(OUT)
    except Exception as e:
        # A torn list must not become {}. Replacing it would drop every harvested bounce and
        # the send-time block that reads this file. The backup sits beside the original.
        print('!! bounced_emails.json is unreadable (%s). Original left in place. Nothing written.'
              % type(e).__name__)
        if not a.dry_run:
            _record_scan(started, False, boxes, failed + ['bounce list unreadable'], notices,
                         len(found), 0, a.days)
        return 1

    new = {k: v for k, v in found.items() if k not in prior}
    merged = dict(prior); merged.update(found)

    print(f'\nhard bounces: {len(found)} found, {len(new)} new '
          f'({len(merged)} blacklisted total). soft/transient ignored: {soft_only}')
    for addr in list(new)[:20]:
        print(f'  NEW  {addr}   [{new[addr]["reason"]}]')

    if a.dry_run:
        print('(dry run — bounced_emails.json not written)')
        return 0

    save_bounce_list(OUT, merged)
    print(f'-> {OUT} (gitignored)')
    # Recorded only AFTER the list is on disk: a clean status must never precede the bounces it covers.
    _record_scan(started, not failed, boxes, failed, notices, len(found), len(new), a.days)
    if failed:
        print('!! not a clean scan (%s); follow-ups to addresses mailed since the last clean scan '
              'stay held.' % ', '.join(failed))
    if new:
        print('Rebuild the board so these stop being queued: '
              'python -c "import json,foreclosure_leads as F; '
              'F.make_tracker(json.load(open(\'leads_final.json\',encoding=\'utf-8\')))"')
    return 2 if failed else 0


def load_bounce_list(path=None):
    """The current list, or {} when the file does not exist yet.

    Exists-but-unreadable raises after a timestamped backup. The caller must not replace it."""
    path = path or OUT
    if not os.path.exists(path):
        return {}
    from optout_sync import _read_ledger_for_write, _refuse_unreadable_ledger
    d = _read_ledger_for_write(path)
    if d is None:
        return {}
    if isinstance(d, list):
        return {str(x).strip().lower(): {'type': 'legacy'} for x in d if x}
    if not isinstance(d, dict):
        _refuse_unreadable_ledger(path, 'parsed but is not an object or a list (%s)' % type(d).__name__)
    return d


def save_bounce_list(path, data):
    """Atomic replace. A crash mid-write leaves the previous file in place."""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


if __name__ == '__main__':
    raise SystemExit(main() or 0)
