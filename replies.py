"""replies.py — did any owner actually write back?

Alejandro (2026-07-30): "I wanted to track whether it gets any outputs from any clients that
reply back."

WHY THIS IS A PYTHON JOB AND NOT A BROWSER JOB
The board is a static encrypted HTML file with no server. It cannot poll a mailbox: no
credentials, no CORS, nothing to run when the tab is closed. So the reply check runs here, on
the machine, and writes replies.json. make_tracker bakes that file into the page, and the Proof
Sheet flips every matching send from "awaiting reply" to a green REPLIED badge.

WHAT IT DOES
  1. Reads the owner email addresses we actually contacted (from skiptrace_results.json, plus
     leads_final.json / the county lead files as a fallback).
  2. IMAP-searches the Gmail account that sent the outreach for mail FROM any of those addresses.
  3. Writes replies.json keyed BOTH by case number and by '@<lowercased email>' so the page can
     match a reply even when the send predates the current lead set.

CREDENTIALS — read this before running
Needs gmail.key in this folder, gitignored, ONE line:
      you@gmail.com:xxxxxxxxxxxxxxxx
The second half is a Google APP PASSWORD (myaccount.google.com -> Security -> App passwords),
NOT your account password. Create it yourself; this script only reads the file. Never commit it.
Without the file the script exits cleanly and the board keeps saying "awaiting reply", which is
the honest state -- it never fabricates a reply it has not seen.

Run:  python replies.py            # check the last 30 days
      python replies.py --days 90
      python replies.py --dry-run  # show what it would search, touch nothing
"""
import os, sys, json, re, imaplib, email
from email.header import decode_header, make_header
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = os.path.join(HERE, 'gmail.key')
OUT = os.path.join(HERE, 'replies.json')

# Words that mean "stop contacting me". A reply carrying one of these is flagged so the operator
# can push it into the opt-out ledger immediately -- an opt-out is more urgent than a warm lead.
STOP_WORDS = re.compile(r'\b(stop|unsubscribe|remove me|do not contact|dont contact|'
                        r'no me contacte|no contacte|detener|parar|quitar)\b', re.I)


def _load_json(path, default):
    p = os.path.join(HERE, path)
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def owner_emails():
    """case-number -> [emails] for every lead we have an address for."""
    out = {}
    st = _load_json('skiptrace_results.json', {}) or {}
    for case, hit in st.items():
        ems = [e for e in (hit.get('emails') or []) if e and '@' in str(e)]
        if ems:
            out.setdefault(case, [])
            out[case].extend(ems)
    for fn in ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json'):
        d = _load_json(fn, [])
        rows = d if isinstance(d, list) else d.get('leads', d)
        if not isinstance(rows, list):
            continue
        for r in rows:
            case = r.get('case') or r.get('Case #')
            ems = [e for e in (r.get('emails') or []) if e and '@' in str(e)]
            if case and ems:
                out.setdefault(case, [])
                out[case].extend(ems)
    # dedupe, lowercase
    for c in list(out):
        seen, uniq = set(), []
        for e in out[c]:
            e = str(e).strip().lower()
            if e and e not in seen:
                seen.add(e)
                uniq.append(e)
        out[c] = uniq
    return out


def load_key():
    if not os.path.exists(KEY):
        return None
    raw = open(KEY, encoding='utf-8').read().strip()
    if ':' not in raw:
        return None
    user, _, pw = raw.partition(':')
    return user.strip(), pw.strip()


def _decode(s):
    try:
        return str(make_header(decode_header(s or '')))
    except Exception:
        return s or ''


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    days = 30
    if '--days' in args:
        try: days = int(args[args.index('--days') + 1])
        except Exception: pass

    by_case = owner_emails()
    all_emails = sorted({e for ems in by_case.values() for e in ems})
    print(f'{len(by_case)} lead(s) carry an email address · {len(all_emails)} distinct addresses')

    if not all_emails:
        print('No owner emails on file yet — run skiptrace.py first. Nothing to check.')
        return

    cred = load_key()
    if not cred:
        print('\ngmail.key MISSING — cannot check replies.')
        print('  Create it in this folder, one line:   you@gmail.com:APP_PASSWORD')
        print('  APP_PASSWORD = myaccount.google.com -> Security -> App passwords (16 chars).')
        print('  It is gitignored. The board will keep showing "awaiting reply" until it exists,')
        print('  which is correct — nothing is checking the inbox right now.')
        return

    if dry:
        print(f'[dry-run] would IMAP-search the last {days} days for mail FROM {len(all_emails)} addresses')
        for e in all_emails[:12]:
            print('   ', e)
        if len(all_emails) > 12:
            print(f'    ... and {len(all_emails)-12} more')
        return

    user, pw = cred
    since = (datetime.now() - timedelta(days=days)).strftime('%d-%b-%Y')
    found = {}
    try:
        M = imaplib.IMAP4_SSL('imap.gmail.com')
        M.login(user, pw)
        M.select('INBOX')
        for addr in all_emails:
            typ, data = M.search(None, f'(SINCE {since} FROM "{addr}")')
            if typ != 'OK' or not data or not data[0]:
                continue
            ids = data[0].split()
            # newest message from this sender
            typ, msg_data = M.fetch(ids[-1], '(RFC822.HEADER)')
            subj, when = '', ''
            if typ == 'OK' and msg_data and msg_data[0]:
                try:
                    hdr = email.message_from_bytes(msg_data[0][1])
                    subj = _decode(hdr.get('Subject'))
                    when = _decode(hdr.get('Date'))[:31]
                except Exception:
                    pass
            rec = {'email': addr, 'n': len(ids), 'subject': subj, 'when': when,
                   'stop': bool(STOP_WORDS.search(subj or '')),
                   'checked': datetime.now().isoformat(timespec='minutes')}
            found['@' + addr] = rec
            for case, ems in by_case.items():
                if addr in ems:
                    found[case] = rec
            flag = '  [STOP WORD IN SUBJECT]' if rec['stop'] else ''
            print(f'  REPLY from {addr} — {len(ids)} msg(s) · {subj[:52]}{flag}')
        M.logout()
    except Exception as e:
        print('IMAP check failed:', str(e)[:140])
        print('  (app password wrong, IMAP disabled in Gmail settings, or network.)')
        return

    json.dump(found, open(OUT, 'w', encoding='utf-8'), indent=1)
    replies = len([k for k in found if k.startswith('@')])
    stops = len([v for k, v in found.items() if k.startswith('@') and v.get('stop')])
    print(f'\nreplies.json written — {replies} address(es) replied'
          + (f', {stops} contain a STOP word' if stops else ''))
    if stops:
        print('  ACT ON THE STOP REPLIES FIRST: add them to optouts.json before any further contact.')
    print('Rebuild the board to surface them:  python -c "import json,foreclosure_leads as F;'
          ' F.make_tracker(json.load(open(\'leads_final.json\',encoding=\'utf-8\')))"')


if __name__ == '__main__':
    main()
