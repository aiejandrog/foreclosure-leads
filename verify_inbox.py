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

THE LANE ALIASES ARE THE SAME QUESTION (2026-09-17). Since the 2026-09-08 sender migration cold
mail leaves as a LANE ALIAS from senders.json -- alejandro@biscaynesolutionsgroup.com (active,
balloon) and alejandro@bsgfl.com (early) -- and nothing sets Reply-To, so an owner who hits Reply
writes to the alias, not to the login. Whether that reply lands in the ONE mailbox replies.py
scans depends entirely on how those two domains are attached to Workspace: an alias DOMAIN
delivers into alejandro@bsgflorida.com, a secondary domain with its own user does not. Nothing in
this repo ever proved which. `--lanes` answers it by sending a nonce to every lane address and
then looking for it in the mailbox the scanner actually opens.

WHAT A PASS DOES AND DOES NOT PROVE. The probe leaves from our own Workspace login, so intra-org
delivery skips most of the filtering a stranger's mail meets. A pass proves ROUTING -- mail
addressed here lands in that mailbox. It does not prove a cold send reaches a homeowner's inbox.
Use --from-key with a consumer-account key file for a real external test.

CREDENTIALS
  bsg_gmail.key        'alejandro@bsgflorida.com:APP_PASSWORD'  -- preferred: the account that
                       actually sends the outreach, and the one replies.py scans
  gmail.key            'user@gmail.com:APP_PASSWORD'  -- the older personal account, fallback
  inbox.key            'user@gmail.com:APP_PASSWORD'  -- a THIRD-PARTY box being tested
All are 16-char Google App Passwords, not account passwords. Until 2026-09-17 the probe was sent
from gmail.key ONLY, so on a machine carrying just the company key this tool failed with
"gmail.key missing" and the check never ran.

RUN
    python verify_inbox.py                          # test sender.json's client_email
    python verify_inbox.py --lanes                  # test every senders.json lane alias
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


def _outbound_cred(key_file=''):
    """(user, password, filename) for the account that SENDS the probe.

    COMPANY FIRST, the same order replies.py's _cred_map uses. Outreach has left from
    bsg_gmail.key since 2026-09-08; probing from a different account than the one that mails
    homeowners would test a path nobody uses. key_file forces one file (--from-key).
    """
    names = [key_file] if key_file else ['bsg_gmail.key', 'gmail.key']
    for fn in names:
        u, p = _cred(fn)
        if u and p:
            return u, p, fn
    return '', '', ''


def _lane_addresses():
    """Every distinct From address in senders.json, in lane order. [] if the file is absent.

    Read straight from the file rather than through send_server._lane_from: the ramp fallback in
    that function can route a lane to the main domain today, and the address we need to test is
    the one the lane will use once its ramp opens, not the one it borrows while frozen.
    """
    try:
        import json as _j
        cfg = _j.load(open(os.path.join(HERE, 'senders.json'), encoding='utf-8'))
        lanes = cfg.get('lanes') or {}
    except Exception:
        return []
    out = []
    for lane in ('replied', 'urgent', 'active', 'early', 'balloon', 'default'):
        a = str(lanes.get(lane) or '').strip().lower()
        if a and '@' in a and a not in out:
            out.append(a)
    for a in sorted(str(v or '').strip().lower() for v in lanes.values()):
        if a and '@' in a and a not in out:
            out.append(a)
    return out


def send_probe(to_addr, nonce, key_file=''):
    """Send the probe from the normal outreach account. Returns (ok, detail)."""
    user, pw, src = _outbound_cred(key_file)
    if not user or not pw:
        return False, ('no sending credential: want bsg_gmail.key or gmail.key holding '
                       'user@domain:APP_PASSWORD')
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
        return True, 'probe sent from %s (%s)' % (user, src)
    except smtplib.SMTPAuthenticationError:
        return False, ('%s rejected by SMTP -- use a 16-char App Password, not the '
                       'account password' % src)
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


def poll_many(mailbox, pw, nonces, seconds=POLL_SECONDS):
    """Look for several probe nonces in ONE mailbox. Returns (found_set, detail).

    One login and one polling window for the whole batch: --lanes sends three probes and waiting
    90 seconds each in turn would take five minutes to answer a question worth thirty seconds.
    """
    deadline = time.time() + seconds
    found, last = set(), ''
    while time.time() < deadline and len(found) < len(nonces):
        try:
            M = imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ssl.create_default_context())
            try:
                M.login(mailbox, pw)
            except imaplib.IMAP4.error as e:
                return found, ('IMAP login refused for %s (%s). If this alias is a SEPARATE '
                               'Workspace user rather than an alias domain, it has its own '
                               'mailbox and its own App Password.' % (mailbox, str(e)[:70]))
            try:
                # All Mail, not INBOX: a probe that landed but was filtered still proves routing,
                # and the distinction is the finding, not a failure.
                M.select('"[Gmail]/All Mail"' if b'All Mail' in b''.join(M.list()[1] or []) else 'INBOX')
                for n in nonces:
                    if n in found:
                        continue
                    typ, data = M.search(None, 'SUBJECT', '"%s"' % n)
                    if (data[0] or b'').split():
                        found.add(n)
            finally:
                try:
                    M.logout()
                except Exception:
                    pass
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, e)
        if len(found) < len(nonces):
            time.sleep(POLL_EVERY)
    return found, last


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--address', action='append', default=[],
                    help='mailbox to test (repeatable; default: sender.json client_email)')
    ap.add_argument('--lanes', action='store_true',
                    help='test every From address in senders.json -- the lane aliases homeowners '
                         'reply to')
    ap.add_argument('--into',
                    help='mailbox to look in (default: the sending login for --lanes, otherwise '
                         'the tested address itself)')
    ap.add_argument('--password', help='App Password for the mailbox being LOOKED IN (default: '
                                       'inbox.key, or the sending key when they are the same box)')
    ap.add_argument('--from-key', default='', dest='from_key',
                    help='key file to send the probe FROM (default: bsg_gmail.key, then '
                         'gmail.key). Point it at a consumer account for a real external test.')
    ap.add_argument('--send-only', action='store_true', help='send the probe, skip the IMAP check')
    ap.add_argument('--wait', type=int, default=POLL_SECONDS, help='seconds to poll (default 90)')
    a = ap.parse_args()

    targets = [x.strip().lower() for one in a.address for x in one.split(',') if x.strip()]
    if a.lanes:
        lanes = _lane_addresses()
        if not lanes:
            print('no senders.json lanes to test (file missing or malformed)')
            return 1
        targets = [t for t in lanes if t not in targets] + targets
    if not targets:
        try:
            import entity
            cfg = (entity.sender().get('client_email') or '').strip().lower()
        except Exception:
            cfg = ''
        if cfg:
            targets = [cfg]
    if not targets:
        print('no address: pass --address, --lanes, or set client_email in sender.json')
        return 1

    sender_user, sender_pw, sender_src = _outbound_cred(a.from_key)
    # WHERE TO LOOK. For a lane alias the question is not "does this address exist" but "does mail
    # to it land in the mailbox replies.py opens" -- so the default is the sending login, and a
    # miss there means the alias has its own mailbox nobody scans.
    look_in = (a.into or '').strip().lower()
    if not look_in:
        look_in = sender_user if a.lanes else (targets[0] if len(targets) == 1 else sender_user)
    if not look_in:
        look_in = targets[0]      # no credential at all: still name a box rather than print blank

    print('sending from   : %s (%s)' % (sender_user or '(none)', sender_src or '-'))
    print('looking in     : %s' % look_in)
    print('testing        : %s' % ', '.join(targets))

    nonces = {}
    failed_send = []
    for addr in targets:
        nonce = uuid.uuid4().hex[:12]
        ok, detail = send_probe(addr, nonce, a.from_key)
        print('  send -> %-42s %s -- %s' % (addr, 'OK' if ok else 'FAILED', detail))
        if ok:
            nonces[nonce] = addr
        else:
            failed_send.append(addr)
    if not nonces:
        print('\nNo probe went out, so RECEIPT IS UNPROVEN for every address above.')
        return 1

    if a.send_only:
        print('\n--send-only: open %s and look for subject "%s <nonce>":' % (look_in, SUBJECT_TAG))
        for n, addr in nonces.items():
            print('    %-42s %s %s' % (addr, SUBJECT_TAG, n))
        return 0

    pw = a.password or ''
    if not pw:
        _u, _p = _cred('inbox.key')
        pw = _p
    if not pw and look_in == sender_user:
        pw = sender_pw
    if not pw:
        print('  receive      : SKIPPED -- no App Password for %s (pass --password or create '
              'inbox.key).' % look_in)
        print('\nSending proved nothing about delivery. Re-run with --send-only and confirm by eye,')
        print('or supply the password for the mailbox you are looking in.')
        return 1

    print('  receive      : polling %s for up to %ds...' % (look_in, a.wait))
    found, detail = poll_many(look_in, pw, list(nonces), a.wait)
    if detail:
        print('  note         : %s' % detail)
    missing = []
    for n, addr in nonces.items():
        hit = n in found
        print('  recv <- %-42s %s' % (addr, 'OK' if hit else 'NOT FOUND'))
        if not hit:
            missing.append(addr)

    if not missing and not failed_send:
        print('\nEvery address above delivers into %s. A homeowner who hits Reply on a send from\n'
              'any of them writes into the mailbox replies.py scans.' % look_in)
        print('This proves ROUTING, not external deliverability -- the probe came from our own\n'
              'Workspace. Use --from-key with an outside account to test that.')
        return 0
    print('\nNOT DELIVERED into %s: %s' % (look_in, ', '.join(missing + failed_send)))
    print('A reply sent to any of those addresses does not reach the scanned mailbox. Either the\n'
          'domain is a SEPARATE Workspace account (give replies.py its own credential, or add a\n'
          'forward), or mail to it is not being delivered at all.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
