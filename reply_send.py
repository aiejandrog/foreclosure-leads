#!/usr/bin/env python
"""reply_send.py -- the one way to email a homeowner who wrote to us, or any one-off email.

WHY THIS EXISTS (2026-10-12)
Four replies to homeowners were sent from the company login over SMTP by a script that called the
sender directly. The sender only ever ran mail_guard (is the message well-formed?). Everything that
decides whether we MAY write to this person lives in the callers -- send_server /send,
outreach_email.main, cadence -- so the script skipped the bankruptcy-stay check, the case and
same-owner do-not-contact checks, the bounce list and the dedupe, and it never wrote the ledger,
so the laptop (and cadence, and dedupe) never learned the mail existed. Three of those homeowners
had no passing stay check on file.

This module is the gated path for that case. It does, in this order, and refuses on the first
failure (every check fails CLOSED -- a gate that cannot be read is a gate that did not pass):

  1. the case number resolves (a one-off with no case is refused: no case, no stay verdict)
  2. the do-not-contact ledger parses, is fresh, and today's 07:15 sync is confirmed
  3. the case, the recipient address and every other case this owner is mailed under are not on it
  4. the recipient is not on the hard-bounce list
  5. stay_gate.check(case) is CLEAR (this also reads never_contact), and bk_lookup.send_hold()
     does not hold the case. Both are read-only: this path never spends on a lookup. A case that
     needs a paid search first is refused and the reason says so.
  6. the same recipient was not already sent this subject in the last 24 hours
  7. mail_guard (inside _smtp_send), under mail_guard.gated('reply_send')
  8. a row in mail_sent.json -- success or failure -- stamped with `source` and `machine`, so
     ledger_sync carries it to the other machine and cadence/dedupe see it (written through
     send_server's locked writer).

Also refused: a recipient that is not one bare address, an address we never mailed under (or heard
from about) this case, an unreadable mail_sent.json or bounce list, and a stay or federal hold on
ANY case this owner is mailed under, not just the one named. Known gap: no cross-process claim, so
two simultaneous runs to the same address can both pass the 24h duplicate check.

Dry run is the default. --send is required to transmit. Nothing here widens a cap or a spend.

    python reply_send.py --case 502018CA016108XXXXMB --to owner@example.com \\
        --subject "Re: your note" --body-file reply.txt            # gate verdict only
    python reply_send.py ... --send                                 # gate, then transmit and ledger
"""
import argparse
import datetime
import json
import os
import re
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = 'reply_send'
_EMAIL = re.compile(r'^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$')


def _refuse(code, why):
    return {'ok': False, 'code': code, 'why': why}


def _norm_case(c):
    return str(c or '').strip()


def machine():
    """Which computer this send came from, for the ledger row. Never raises."""
    try:
        return (os.environ.get('COMPUTERNAME') or socket.gethostname() or 'unknown')[:60]
    except Exception:
        return 'unknown'


def _owner_cases_for(addr, ledger):
    """Every case this address has been mailed under, lower-cased (same-owner sibling cases)."""
    out = set()
    for r in ledger:
        if not isinstance(r, dict) or r.get('test_mode'):
            continue
        allto = [str(r.get('to') or '')] + str(r.get('bcc') or '').split(',')
        if addr in {a.strip().lower() for a in allto if a.strip()}:
            c = str(r.get('case') or '').strip().lower()
            if c:
                out.add(c)
            for p in (r.get('portfolio') or []):
                if isinstance(p, str) and p.strip():
                    out.add(p.strip().lower())
    return out


def gate(case, to, subject='', also_cases=(), here=HERE, ss=None, sg=None, bk=None, now=None):
    """-> {'ok': bool, 'code': str, 'why': str}. Never raises; any surprise is a refusal.

    ss / sg / bk let a test hand in the send_server, stay_gate and bk_lookup modules."""
    try:
        return _gate(case, to, subject, also_cases, here, ss, sg, bk, now)
    except Exception as e:
        return _refuse('gate_error', 'the gate itself failed (%s: %s) -- nothing was sent'
                       % (type(e).__name__, str(e)[:120]))


def _gate(case, to, subject, also_cases, here, ss, sg, bk, now):
    case = _norm_case(case)
    to = str(to or '').strip().lower()
    from email.utils import parseaddr
    if not _EMAIL.match(to) or parseaddr(to)[1] != to or any(ch in to for ch in '<>"()'):
        return _refuse('bad_recipient', 'recipient %r is not a single bare email address' % to[:60])
    if ss is None:
        import send_server as ss
    if sg is None:
        import stay_gate as sg
    # 1. the case resolves ------------------------------------------------------------------
    if not case or not (sg.pacer_key(case) or sg.case_stem(case)):
        return _refuse('case_unresolvable',
                       'no usable case number (%r): without one there is no stay verdict. Find the '
                       'case first.' % case[:40])
    # 2. the do-not-contact ledger is readable and fresh ------------------------------------
    if not ss._optout_readable():
        return _refuse('optout_unreadable', 'the do-not-contact ledger is missing or will not parse')
    age = ss._optout_age_days()
    if age is None or age > ss.OPTOUT_MAX_AGE_DAYS:
        return _refuse('optout_stale', 'the do-not-contact ledger is %s days old (limit %s): run '
                       'run-optout-sync.bat first' % ('missing' if age is None else '%.1f' % age,
                                                      ss.OPTOUT_MAX_AGE_DAYS))
    sv = ss._sync_verdict()
    if not sv.get('ok'):
        return _refuse('optout_sync_unconfirmed', "today's 07:15 opt-out sync is not confirmed (%s)"
                       % str(sv.get('reason') or 'no verdict')[:120])
    if ss._ledger_unreadable():
        return _refuse('ledger_unreadable', 'mail_sent.json will not parse, so the sibling and duplicate '
                       'checks cannot be made')
    # 3. case / address / same-owner do-not-contact -----------------------------------------
    oo_cases, oo_emails = ss._optout_set()
    if to in oo_emails:
        return _refuse('optout_email', 'the recipient address is on the do-not-contact ledger')
    mine = {case.lower()} | {str(c).strip().lower() for c in (also_cases or ()) if str(c).strip()}
    sibs = _owner_cases_for(to, ss._load_ledger()) | mine
    hit = next((c for c in sorted(sibs) if c in oo_cases), None)
    if hit:
        return _refuse('optout_case', 'do-not-contact: %s' % (
            'this case' if hit == case.lower() else 'the same owner, case ' + hit))
    # 3b. a reply goes to someone we already know: an address we mailed, or one that wrote in under
    # this case. A cold address is not a reply and goes through send_server /send's first-touch gates.
    known = set()
    for r in ss._load_ledger():
        if isinstance(r, dict) and not r.get('test_mode') and r.get('message_id'):
            if str(r.get('case') or '').strip().lower() in mine:
                known |= {a.strip().lower() for a in [str(r.get('to') or '')] + str(r.get('bcc') or '').split(',') if a.strip()}
    try:
        rep = json.load(open(os.path.join(here, 'replies.json'), encoding='utf-8'))
    except Exception:
        rep = {}
    for rk, rec in (rep.items() if isinstance(rep, dict) else []):
        if (isinstance(rec, dict) and not str(rk).startswith('@') and str(rk).strip().lower() in mine
                and str(rec.get('email') or '').strip().lower()):
            known.add(str(rec['email']).strip().lower())
    if to not in known:
        return _refuse('unknown_recipient', 'this address was never mailed under, and never wrote in about, '
                       'this case. Cold email goes through send_server /send, not reply_send')
    # 4. hard bounces -----------------------------------------------------------------------
    bp = os.path.join(here, 'bounced_emails.json')
    if os.path.exists(bp):
        try:
            bounced = json.load(open(bp, encoding='utf-8'))
        except Exception:
            return _refuse('bounce_list_unreadable', 'bounced_emails.json will not parse')
        if not isinstance(bounced, (dict, list)):
            return _refuse('bounce_list_unreadable', 'bounced_emails.json is not a list or object')
        keys = {str(k).strip().lower() for k in bounced}
        if to in keys:
            return _refuse('bounced', 'the recipient hard-bounced before')
    # 5. bankruptcy stay, read-only ---------------------------------------------------------
    for c in sorted(sibs):
        v = sg.check(c, os.path.join(here, 'sale_history_cache.json'))
        if not v.get('ok'):
            return _refuse('stay_' + (v.get('code') or 'held'),
                           'bankruptcy-stay gate for %s: %s' % (c, str(v.get('why') or v.get('code'))[:200]))
    if bk is None:
        try:
            import bk_lookup as bk
        except Exception as e:
            return _refuse('bk_unavailable', 'the federal bankruptcy check could not load (%s)' % str(e)[:80])
    for c in sorted(sibs):
        held, why = bk.send_hold(c, here=here)
        if held:
            return _refuse('bk_hold', 'federal bankruptcy hold on %s: %s' % (c, str(why or 'held')[:160]))
    # 6. dedupe -----------------------------------------------------------------------------
    subj = str(subject or '').strip().lower()
    cutoff = (now or datetime.datetime.now(datetime.timezone.utc)) - datetime.timedelta(hours=24)
    for r in ss._load_ledger():
        if (isinstance(r, dict) and r.get('message_id') and not r.get('error') and not r.get('test_mode')
                and str(r.get('to') or '').strip().lower() == to
                and str(r.get('subj') or '').strip().lower() == subj):
            try:
                ts = datetime.datetime.fromisoformat(str(r.get('ts_utc') or '').replace('Z', '+00:00'))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=datetime.timezone.utc)
            except Exception:
                return _refuse('dedupe_unreadable', 'a ledger row to this address has no readable time')
            if ts >= cutoff:
                return _refuse('duplicate', 'this recipient was sent this subject in the last 24 hours')
    return {'ok': True, 'code': 'clear', 'why': ''}


def send_reply(case, to, subject, body, also_cases=(), send=False, here=HERE, smtp=None, ledger=None,
               **gate_kw):
    """Gate, then (only with send=True) transmit and ledger. -> dict with ok/code/why/message_id.

    smtp(user, pw, display, to, subj, body) and ledger(row) can be handed in by a test."""
    case, to = _norm_case(case), str(to or '').strip().lower()
    g = gate(case, to, subject, also_cases, here=here, **gate_kw)
    if not g['ok'] or not send:
        g['sent'] = False
        return g
    import mail_guard as MG
    import outreach_email as OE
    user, pw = OE._load_credentials()
    if not (user and pw):
        return dict(_refuse('no_credentials', 'no gmail.key credentials on this machine'), sent=False)
    display = str((OE._load_sender() or {}).get('name') or '')
    row = {'d': datetime.date.today().isoformat(),
           'ts_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'ch': 'email', 'from': user, 'to': to, 'owner': '', 'case': case, 'subj': subject,
           'kind': 'reply', 'source': SOURCE, 'machine': machine(), 'body_len': len(body or '')}
    import send_server as SS
    put = ledger or SS._append_ledger
    try:
        with MG.gated(SOURCE):
            mid, resp = (smtp or OE._smtp_send)(user, pw, display, to, subject, body)
    except Exception as e:
        put(dict(row, error=str(e)[:200]))
        return dict(_refuse('send_failed', str(e)[:200]), sent=False)
    put(dict(row, message_id=mid, smtp_resp=str(resp)))
    return {'ok': True, 'code': 'sent', 'why': '', 'sent': True, 'message_id': mid}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--case', required=True)
    ap.add_argument('--to', required=True)
    ap.add_argument('--subject', required=True)
    ap.add_argument('--body-file', required=True)
    ap.add_argument('--also-case', action='append', default=[], help="the owner's other case numbers")
    ap.add_argument('--send', action='store_true', help='transmit (default is the gate verdict only)')
    a = ap.parse_args(argv)
    body = open(a.body_file, encoding='utf-8').read()
    r = send_reply(a.case, a.to, a.subject, body, also_cases=a.also_case, send=a.send)
    print('%s: %s%s' % ('SENT' if r.get('sent') else ('CLEAR (dry run, nothing sent)' if r['ok'] else 'REFUSED'),
                        r['code'], (' -- ' + r['why']) if r['why'] else ''))
    return 0 if r['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
