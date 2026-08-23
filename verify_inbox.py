#!/usr/bin/env python
"""verify_inbox — prove a mailbox actually RECEIVES before it goes on client paper.

WHY THIS EXISTS
`client_email` in sender.json is printed on every letter a homeowner, servicer or opposing counsel
receives -- the letterhead, the retainer, the third-party authorization. If it points at an address
that does not exist or does not deliver, replies from people in foreclosure vanish silently. Nobody
finds out from the sending side, because nothing bounces to us: THEY get the bounce, we get nothing.
That is strictly worse than a stale-but-working inbox, which is why the rename deliberately left
`miamisolutionsgroup@gmail.com` in place rather than guessing a biscayne* address.

SENDING IS NOT RECEIVING. `rotate_key.check_gmail()` already proves a credential can log into SMTP
and send. That says nothing about whether mail addressed TO this box lands in it -- a forwarding
rule, a suspended account, a full quota or a typo all pass an SMTP-auth check and still black-hole
every reply. So this sends a nonce and then goes and LOOKS for it over IMAP.

CREDENTIALS
  gmail.key            'user@gmail.com:APP_PASSWORD'  -- the account that SENDS the probe
  inbox.key            'user@gmail.com:APP_PASSWORD'  -- the account being TESTED (gitignored)
Both are 16-char Google App Passwords, not account passwords. If inbox.key is absent, pass
--password, or run with --send-only to get a probe you confirm by eye.

RUN
    python verify_inbox.py                          # test sender.json's client_email
    python verify_inbox.py --address new@gmail.com  # test a candidate before switching
    python verify_inbox.py --send-only              # send the probe, check the box by hand

EXIT CODES
    0  the mailbox received the probe -- safe to put on client paper
    1  it did not (or could not be checked) -- do NOT switch client_email
"""
import argparse
import email
import imaplib
import os
import smtplib
import ssl
import sys
import time
import uuid
from email.mime.text import MIMEText

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SUBJECT_TAG = 'DEALFLOW-INBOX-PROBE'
POLL_SECONDS = 90
POLL_EVERY = 5


def _cred(fname, override=''):
    """-> (user, password) from a 'user:password' key file, or ('','')."""
    if override and ':' in override:
        u, p = override.split(':', 1)
        return u.strip(), p.strip()
    try:
        raw = open(os.path.join(HERE, fname), encoding='utf-8').read().strip()
    except Exception:
        return '', ''
    if ':' not in raw:
        return '', raw.strip()
    u, p = raw.split(':', 1)
    return u.strip(), p.strip()


def send_probe(to_addr, nonce):
    """Send the probe from the normal outreach account. Returns (ok, detail)."""
    user, pw = _cred('gmail.key')
    if not user or not pw:
        return False, 'gmail.key missing or malformed (want user@gmail.com:APP_PASSWORD)'
    msg = MIMEText(
        'Inbox reachability probe for DealFlow.\n\n'
        'This confirms mail addressed to %s is actually delivered. Nonce: %s\n\n'
        'Nothing to do -- you can delete this.\n' % (to_addr, nonce), 'plain', 'utf-8')
    msg['Subject'] = '%s %s' % (SUBJECT_TAG, nonce)
    msg['From'] = user
    msg['To'] = to_addr
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context(),
                              timeout=25) as sv:
            sv.login(user, pw)
            sv.sendmail(user, [to_addr], msg.as_string())
        return True, 'probe sent from %s' % user
    except smtplib.SMTPAuthenticationError:
        return False, 'gmail.key rejected by SMTP -- use a 16-char App Password, not the account password'
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, e)


def poll_inbox(addr, pw, nonce, seconds=POLL_SECONDS):
    """Look for the nonce over IMAP. Returns (found, detail)."""
    deadline = time.time() + seconds
    last = ''
    while time.time() < deadline:
        try:
            M = imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ssl.create_default_context())
            try:
                M.login(addr, pw)
            except imaplib.IMAP4.error as e:
                return False, ('IMAP login refused (%s). Enable IMAP in Gmail settings and use a '
                               '16-char App Password.' % str(e)[:80])
            try:
                # Search ALL, not just INBOX-unread: a filter may have already moved or read it.
                M.select('"[Gmail]/All Mail"' if b'All Mail' in b''.join(M.list()[1] or []) else 'INBOX')
                typ, data = M.search(None, 'SUBJECT', '"%s"' % nonce)
                ids = (data[0] or b'').split()
                if ids:
                    typ, d = M.fetch(ids[-1], '(RFC822.HEADER)')
                    hdr = email.message_from_bytes(d[0][1]) if d and d[0] else None
                    return True, 'delivered (subject %r)' % (hdr.get('Subject') if hdr else nonce)
            finally:
                try:
                    M.logout()
                except Exception:
                    pass
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, e)
        time.sleep(POLL_EVERY)
    return False, last or 'not delivered within %ds' % seconds


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--address', help='mailbox to test (default: sender.json client_email)')
    ap.add_argument('--password', help='App Password for the tested mailbox (default: inbox.key)')
    ap.add_argument('--send-only', action='store_true', help='send the probe, skip the IMAP check')
    ap.add_argument('--wait', type=int, default=POLL_SECONDS, help='seconds to poll (default 90)')
    a = ap.parse_args()

    try:
        import entity
        cfg = (entity.sender().get('client_email') or '').strip()
    except Exception:
        cfg = ''
    addr = (a.address or cfg).strip()
    if not addr:
        print('no address: pass --address or set client_email in sender.json')
        return 1

    nonce = uuid.uuid4().hex[:12]
    print('testing mailbox : %s' % addr)
    ok, detail = send_probe(addr, nonce)
    print('  send          : %s -- %s' % ('OK' if ok else 'FAILED', detail))
    if not ok:
        print('\nCould not send the probe, so RECEIPT IS UNPROVEN. Do not put this address on '
              'client paper.')
        return 1

    if a.send_only:
        print('\n--send-only: open %s and look for subject "%s %s".\n'
              'If it is there, the mailbox receives and you can set client_email in sender.json.'
              % (addr, SUBJECT_TAG, nonce))
        return 0

    _u, pw = _cred('inbox.key', a.password or '')
    pw = a.password or pw
    if not pw:
        print('  receive       : SKIPPED -- no inbox.key and no --password.')
        print('\nSending proved nothing about delivery. Either create inbox.key as\n'
              '  %s:<16-char App Password>\n'
              'or re-run with --send-only and confirm by eye. Until receipt is proven, leave\n'
              'client_email alone.' % addr)
        return 1

    print('  receive       : polling IMAP for up to %ds...' % a.wait)
    found, detail = poll_inbox(addr, pw, nonce, a.wait)
    print('  receive       : %s -- %s' % ('OK' if found else 'FAILED', detail))
    if found:
        print('\n%s SENDS and RECEIVES. Safe to set as client_email in sender.json --\n'
              'that one value feeds the board, carlos_letter_packet.py and make_bsg_forms.py.' % addr)
        return 0
    print('\nThe probe never arrived. Replies from homeowners, servicers and opposing counsel would\n'
          'disappear the same way. Do NOT set this as client_email.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
