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
from email.utils import parseaddr
from datetime import datetime, timedelta
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = os.path.join(HERE, 'gmail.key')
OUT = os.path.join(HERE, 'replies.json')
# ALERTS carries ONLY what changed since the last run — a running log a human (or a Monitor-style
# watcher tailing this file) can act on same-day, instead of the only record being replies.json
# (which the daily .bat pipes straight into an unread %TEMP% file) or a weekly scorecard that finds
# a written reply days after it arrived. See run() for why "new" means n-count increased, not just
# "key wasn't there before" — days=30 means the SAME reply reappears in the search window on every
# run for a month, and re-alerting on it every single day would make the file worthless within a week.
ALERTS = P.out('NEW-REPLIES.txt')

# The subject our outreach always sends. Replies keep it as "Re: Regarding your property at ...",
# which is what makes PASS 2 (subject matching) possible — see main().
SUBJ_TAG = 'Regarding your property at'

# UNAMBIGUOUS opt-out phrasings — these mean "stop contacting me" in any context.
#
# The previous pattern was `\b(stop|unsubscribe|remove me|do not contact|dont contact|...)\b`,
# and it was wrong in BOTH directions.
#
# MISSED (of eight realistic opt-out sentences, only "unsubscribe" matched): "take me off your
# list", "opt me out", "remove my email address", "leave me alone", "do not email me again",
# "no longer wish to be contacted", "quit emailing me". Those people kept being contacted, which
# is the exposure the entire suppression chain exists to prevent.
OPTOUT_PHRASES = re.compile(
    r'\bunsubscribe\b'
    r'|\bopt[\s-]?out\b|\bopt me out\b'
    r'|\btake me off\b|\bremove me\b'
    r'|\bremove my\s+(?:e-?mail|address|number|phone|name|info)'
    r"|\bdo\s?n[o']?t\s+(?:contact|e-?mail|call|text|message|write|reach|send)"
    r'|\b(?:quit|stop|cease)\s+(?:contact|contacting|e-?mailing|calling|texting|messaging'
    r'|writing|sending|soliciting)'
    r'|\bleave me alone\b'
    r'|\bno longer\s+(?:wish|want|interested)'
    r'|\bno me contacte\b|\bno contacte\b|\bno me escriba\b|\bd[eé]jeme en paz\b'
    r'|\bquitar\b|\bdetener\b|\bparar\b', re.I)

# FALSE-FLAGGED, and this is the dangerous half. A bare "stop" is genuinely ambiguous IN THIS
# BUSINESS: "Please stop." is an opt-out, but "Can you stop the foreclosure?" is the most motivated
# reply a homeowner can send. A detected stop AUTO-SUPPRESSES on the board — optout +
# DO NOT CONTACT across the worker, call sheet, dial and cadence — so `\bstop\b` silently killed
# every one of these:
#     "Can you stop the foreclosure?"     "Is there any way to stop the sale?"
#     "I need to stop the auction"        "what can i do to stop this"
# Our own outreach copy is built around never promising to stop a foreclosure precisely because
# that is the thing these owners write in to ask for.
_BARE_STOP = re.compile(r'\bstop\b', re.I)
# What follows a "stop" that is aimed at the CASE rather than at us.
_STOP_OBJECT = re.compile(r'\s*(?:the|this|my|a|an)?\s*'
                          r'(?:foreclosure|sale|auction|case|process|hearing|judgment|eviction'
                          r'|it|this|that)\b', re.I)


def _ledger_stops(found):
    """Write every detected STOP straight into optouts.json. Returns (written, already_there).

    THE TOOL THAT DETECTS THE STOP IS THE ONE THAT SHOULD RECORD IT. This used to print
    "ACT ON THE STOP REPLIES FIRST: add them to optouts.json before any further contact" and leave
    it to a person. The ledger's own audit trail shows what that costs — gil_sosa@hotmail.com wrote
    "Please stop sending emails" on 2026-08-08, replies.py saw it, and the entry reads:

        "Detected in replies.json 2026-08-12 but NOT ledgered until 2026-08-13
         (5-day gap; see the reply->optout sync gap)"
        optlog: [... {"act": "ledgered", "src": "manual sync from replies.json"}]

    Five days of being contactable after asking us to stop, because the step was manual. The board
    auto-suppresses case-keyed stops the moment it loads, but that only helps devices that reload,
    and it cannot help the Python send/door/letter paths at all — those read optouts.json.

    SAFETY-ONE-WAY, matching the ledger's existing rule: this only ADDS. An entry that already
    exists is never rewritten, never downgraded, never cleared — so a hand-written note with more
    context than we have here always survives.
    """
    p = os.path.join(HERE, 'optouts.json')
    try:
        raw = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
    except Exception as e:
        print(f'  !! optouts.json unreadable ({e}) — STOP replies NOT ledgered, do it by hand')
        return 0, 0
    envelope = isinstance(raw, dict) and isinstance(raw.get('notes'), dict)
    notes = raw['notes'] if envelope else (raw if isinstance(raw, dict) else {})

    now = datetime.now()
    written = already = 0
    for key, rec in found.items():
        if not (isinstance(rec, dict) and rec.get('stop')):
            continue
        # keep BOTH shapes the ledger already uses: '@address' identity keys and bare case keys.
        if key in notes:
            already += 1
            continue
        notes[key] = {
            'status': 'DO NOT CONTACT',
            'optout': now.strftime('%Y-%m-%d'),
            'note': ('OPT-OUT %s - auto-ledgered by replies.py from an inbound reply: "%s"'
                     % (now.strftime('%Y-%m-%d %H:%M'), (rec.get('excerpt') or '')[:180])),
            'optlog': [{'ts': now.strftime('%Y-%m-%d %H:%M'), 'act': 'opted-out',
                        'src': 'email reply - STOP detected by replies.py'},
                       {'ts': now.strftime('%Y-%m-%d %H:%M'), 'act': 'ledgered',
                        'src': 'automatic (replies.py)'}],
        }
        written += 1
    if written:
        try:
            json.dump(raw if envelope else notes, open(p, 'w', encoding='utf-8'), indent=1)
        except Exception as e:
            print(f'  !! could not write optouts.json ({e}) — {written} STOP(s) NOT ledgered')
            return 0, already
    return written, already


def is_stop_text(text):
    """True when the writer is telling US to stop — not asking us to stop the foreclosure.

    Explicit phrasing wins outright. A bare "stop" counts UNLESS every occurrence is followed by a
    case-object, so "Please stop." and "stop sending emails" are opt-outs while "stop the sale" is
    not. Erring toward suppression stays the default wherever the two readings genuinely collide;
    this only separates the readings that do not.
    """
    t = str(text or '')
    if OPTOUT_PHRASES.search(t):
        return True
    for m in _BARE_STOP.finditer(t):
        if not _STOP_OBJECT.match(t[m.end():m.end() + 40]):
            return True          # a "stop" not aimed at the case is aimed at us
    return False


def _load_json(path, default):
    p = os.path.join(HERE, path)
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def _norm_addr(a):
    """'1533 W RIVER DR, MARGATE, 33063' -> '1533WRIVERDR'. Street part only, alnum, upper —
    so a subject line survives punctuation/casing/truncation and still joins to its lead."""
    s = str(a or '').split(',')[0]
    return re.sub(r'[^A-Z0-9]', '', s.upper())


def owner_addrs():
    """case-number -> normalized street address, for PASS-2 subject matching."""
    out = {}
    for fn in ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json'):
        d = _load_json(fn, [])
        rows = d if isinstance(d, list) else d.get('leads', d)
        if not isinstance(rows, list):
            continue
        for r in rows:
            case = r.get('case') or r.get('Case #')
            a = _norm_addr(r.get('addr') or r.get('Address') or '')
            if case and a:
                out[case] = a
    return out


# ONLY THE NEW TEXT COUNTS, and getting this wrong is expensive in BOTH directions.
# Our own template ends with "Reply STOP or ask me not to contact you again", and every reply
# quotes that template underneath. If the quoted block reaches the scanner, EVERY reply reads as
# an opt-out -- including Deondre, who wrote back with his phone number asking for a call.
# Suppressing a hot lead as a false opt-out is worse than the miss this guard was written for.
# The first cut required the quote marker to start a line; real Gmail replies inline it
# ("...call you On Mon, Jul 27, 2026 at 2:48 PM Alejandro Gonzalez <...> wrote:"), so the quote
# leaked through. Match the marker ANYWHERE, and cut at the earliest hit.
_QUOTE = [
    r'On .{0,120}?wrote:',            # Gmail / Apple Mail
    r'-{2,}\s*Original Message',      # Outlook
    r'_{5,}',                          # Outlook divider rule
    r'From:\s*\S+@',                  # forwarded header
    r'Sent from my ',                 # signature that precedes a quote
    r'\n\s*>',                        # classic quote marker
]


def _read_msg(raw):
    """(subject, date, fresh_body, is_stop, sender) from one raw RFC822 message.

    SHARED BY BOTH SCAN PASSES ON PURPOSE. The opt-out decision is safety-critical (a missed STOP
    is an FTSA/TCPA problem), so it must live in exactly one place -- two copies would drift.

    FETCH THE WHOLE MESSAGE, NOT JUST THE HEADER. Caught on the first live run (2026-07-30):
    Norma Hendy replied "Please stop" and the scan scored it stop=False, because her subject was
    the untouched "Re: Regarding your property at ..." and only the BODY carried the opt-out.
    """
    subj, when, body, sender = '', '', '', ''
    try:
        msg = email.message_from_bytes(raw)
        subj = _decode(msg.get('Subject'))
        when = _decode(msg.get('Date'))[:31]
        sender = (parseaddr(msg.get('From') or '')[1] or '').strip().lower()
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    try:
                        body += part.get_payload(decode=True).decode(
                            part.get_content_charset() or 'utf-8', 'replace')
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode(
                    msg.get_content_charset() or 'utf-8', 'replace')
            except Exception:
                body = str(msg.get_payload())[:4000]
    except Exception:
        pass
    cut = len(body)
    for pat in _QUOTE:
        m = re.search(pat, body, re.I | re.S)
        if m and m.start() < cut:
            cut = m.start()
    fresh = body[:cut][:2000]
    is_stop = bool(is_stop_text(subj) or is_stop_text(fresh))
    return subj, when, fresh, is_stop, sender


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


def load_key(account_override=None):
    """gmail.key holds 'user:app_password'. --account swaps the mailbox without editing the file.

    WHY THE OVERRIDE EXISTS (found the hard way 2026-07-30): an App Password is minted against
    whichever Google account you were signed into, and with several accounts open it is easy to
    create one under the wrong mailbox. The key looked textbook-correct -- 16 lowercase chars, no
    spaces -- and Gmail still returned AUTHENTICATIONFAILED, because the password belonged to the
    personal Gmail rather than the Workspace address in the same file. Rather than rewrite the
    credential file to chase that, point the run at the mailbox the password actually opens.
    Also the more correct default: replies land in whichever inbox the outreach was SENT from.
    """
    # ENV FIRST, FILE SECOND — the same contract every other scraper here uses (batchdata.key,
    # tracerfy.key, captcha.key all check env then fall back to a gitignored file). On GitHub
    # Actions only env exists; on Alejandro's machine only the file does. Without this branch
    # replies.py is the one enrichment step that can never run in the cloud, which is exactly
    # why reply detection was still a manual chore after everything else automated.
    env = (os.environ.get('GMAIL_APP_PASSWORD') or '').strip()
    if env:
        if ':' in env:
            user, _, pw = env.partition(':')
        else:
            # password-only secret — the mailbox must then come from --account or gmail.key
            user, pw = '', env
        user = (account_override or user or os.environ.get('GMAIL_ACCOUNT', '')).strip()
        if user and pw:
            return user, pw.strip()
    if not os.path.exists(KEY):
        return None
    raw = open(KEY, encoding='utf-8').read().strip()
    if ':' not in raw:
        return None
    user, _, pw = raw.partition(':')
    return (account_override or user).strip(), pw.strip()


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
    account = None
    if '--account' in args:
        try: account = args[args.index('--account') + 1]
        except Exception: pass

    by_case = owner_emails()
    all_emails = sorted({e for ems in by_case.values() for e in ems})
    print(f'{len(by_case)} lead(s) carry an email address · {len(all_emails)} distinct addresses')

    if not all_emails:
        print('No owner emails on file yet — run skiptrace.py first. Nothing to check.')
        return

    cred = load_key(account)
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
    print(f'checking mailbox: {user}')
    since = (datetime.now() - timedelta(days=days)).strftime('%d-%b-%Y')
    prior = _load_json('replies.json', {})
    found = {}
    new_alerts = []
    try:
        M = imaplib.IMAP4_SSL('imap.gmail.com')
        M.login(user, pw)
        M.select('INBOX')
        # ---- PASS 1: FROM a known owner address -------------------------------------------------
        for addr in all_emails:
            typ, data = M.search(None, f'(SINCE {since} FROM "{addr}")')
            if typ != 'OK' or not data or not data[0]:
                continue
            ids = data[0].split()
            typ, msg_data = M.fetch(ids[-1], '(RFC822)')
            raw = msg_data[0][1] if (typ == 'OK' and msg_data and msg_data[0]) else b''
            subj, when, fresh, is_stop, _snd = _read_msg(raw)
            rec = {'email': addr, 'n': len(ids), 'subject': subj, 'when': when,
                   'stop': is_stop,
                   'excerpt': ' '.join(fresh.split())[:220],
                   'checked': datetime.now().isoformat(timespec='minutes')}
            # NEW means the message count for this address grew since the last run, not just "the
            # key is new" -- with days=30 the same reply reappears in this search every day for a
            # month, and a returning replier's SECOND message should still alert even though the
            # address itself was already known.
            is_new = rec['n'] > ((prior.get('@' + addr) or {}).get('n') or 0)
            found['@' + addr] = rec
            case_hit = None
            for case, ems in by_case.items():
                if addr in ems:
                    found[case] = rec
                    case_hit = case
            flag = '  [STOP WORD IN SUBJECT]' if rec['stop'] else ''
            print(f'  REPLY from {addr} — {len(ids)} msg(s) · {subj[:52]}{flag}')
            if is_new:
                new_alerts.append({'case': case_hit, 'email': addr, 'when': when,
                                    'stop': is_stop, 'excerpt': rec['excerpt']})
                print(f'  NEW SELLER REPLY | {case_hit or "no case on file"} | {addr} | '
                      f'{rec["excerpt"][:90]}')

        # ---- PASS 2: SUBJECT match — a reply from an address we DON'T have on file ---------------
        # PASS 1 only searches FROM the addresses skiptrace found. An owner who replies from ANY
        # other address is invisible to it. That is not hypothetical: 2026-08-03, Toni Labriola
        # (1533 W River Dr, auction 3 days out) replied "call me tomorrow" from an address not in
        # her file, and the scan reported zero new replies -- Alejandro only caught it by reading
        # his own inbox. Same failure class as the Capri lead: the warmest signal the system can
        # produce, sitting invisible.
        # Our subject is always "Regarding your property at <ADDRESS>", and replies keep it as
        # "Re: ...", so the SUBJECT itself identifies the lead even when the sender doesn't.
        # Skip anything PASS 1 already has, and skip our own address (a reply we sent).
        seen = set(all_emails)
        typ, data = M.search(None, f'(SINCE {since} SUBJECT "{SUBJ_TAG}")')
        if typ == 'OK' and data and data[0]:
            addr_map = owner_addrs()
            # HEADER-FIRST TRIAGE (2026-08-26). This loop full-fetched RFC822 for EVERY message in
            # the 30-day subject window just to read who sent it, then discarded nearly all of them
            # -- and the window only ever grows, so the run got slower every day until the 06:45
            # task blew through its PT30M ExecutionTimeLimit mid-scan (killed = replies.json never
            # written = that morning's detected replies thrown away). Sender/self/bounce/subject-
            # frag are all HEADER questions; fetch headers only (cheap, no body), and pay for the
            # full message -- excerpt + STOP scan need the body -- only for the handful that survive.
            for mid in data[0].split():
                typh, hd = M.fetch(mid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])')
                if typh != 'OK' or not hd or not hd[0] or not isinstance(hd[0], tuple):
                    continue
                hmsg = email.message_from_bytes(hd[0][1])
                sender = (parseaddr(str(hmsg.get('From') or ''))[1] or '').lower().strip()
                hsubj = str(make_header(decode_header(hmsg.get('Subject') or '')))
                if not sender or sender == str(user).lower() or sender in seen:
                    continue
                # a bounce/auto-notice is not an owner writing back
                if re.search(r'(mailer-daemon|postmaster|no-?reply|do-?not-?reply)', sender, re.I):
                    continue
                mfrag = re.search(r'property at\s+(.+)$', hsubj or '', re.I)
                if not mfrag or not _norm_addr(mfrag.group(1)):
                    continue
                typ2, md = M.fetch(mid, '(RFC822)')
                if typ2 != 'OK' or not md or not md[0]:
                    continue
                subj, when, fresh, is_stop, sender = _read_msg(md[0][1])
                if not sender or sender == str(user).lower() or sender in seen:
                    continue
                m = re.search(r'property at\s+(.+)$', subj or '', re.I)
                frag = _norm_addr(m.group(1)) if m else ''
                if not frag:
                    continue
                case = None
                for c, a in addr_map.items():
                    if a and (a.startswith(frag) or frag.startswith(a)):
                        case = c
                        break
                rec = {'email': sender, 'n': 1, 'subject': subj, 'when': when,
                       'stop': is_stop,
                       'excerpt': ' '.join(fresh.split())[:220],
                       'via': 'subject-match', 'new_address': True,
                       'checked': datetime.now().isoformat(timespec='minutes')}
                is_new = ('@' + sender) not in prior
                found['@' + sender] = rec
                if case:
                    found[case] = rec
                seen.add(sender)
                flag = '  [STOP WORD]' if is_stop else ''
                print(f'  REPLY from {sender} (NEW ADDRESS, matched by subject) — '
                      f'{subj[:46]}{flag}')
                print(f'      -> lead: {case or "no case matched — check the address"}'
                      f'   ** add {sender} to this owner\'s file **')
                if is_new:
                    new_alerts.append({'case': case, 'email': sender, 'when': when,
                                        'stop': is_stop, 'excerpt': rec['excerpt']})
                    print(f'  NEW SELLER REPLY | {case or "no case on file"} | {sender} | '
                          f'{rec["excerpt"][:90]}')
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
        _ledgered, _already = _ledger_stops(found)
        if _ledgered:
            print(f'  {_ledgered} STOP repl(ies) written to optouts.json automatically'
                  + (f' ({_already} already there)' if _already else ''))
        else:
            print(f'  {_already} STOP repl(ies) already on the opt-out ledger.')
        print('  Review them, but they are already suppressed — no manual step is required.')
    print('Rebuild the board to surface them:  python -c "import json,foreclosure_leads as F;'
          ' F.make_tracker(json.load(open(\'leads_final.json\',encoding=\'utf-8\')))"')

    # run-replies-daily.bat pipes ALL of this stdout into an unread %TEMP% file, and replies.json
    # only feeds pull-based consumers (the board, morning_planner, the weekly scorecard) -- nothing
    # ever told a human THE DAY a reply landed. This file exists to be checked/tailed daily; it holds
    # only what changed since the last run, and disappears when there is nothing new so it can never
    # be mistaken for a fresh alert days later.
    if new_alerts:
        lines = [f'NEW SELLER REPLIES — as of {datetime.now().strftime("%Y-%m-%d %H:%M")}', '']
        for a in new_alerts:
            tag = '  [STOP WORD — opt-out, do not text/call further]' if a['stop'] else ''
            lines.append(f'{a["case"] or "(no case matched — check the address)"} | {a["email"]} | {a["when"]}{tag}')
            lines.append(f'    {a["excerpt"]}')
            lines.append('')
        open(ALERTS, 'w', encoding='utf-8').write('\n'.join(lines))
        print(f'\n{len(new_alerts)} NEW reply(ies) since the last check -> {ALERTS}')
    elif os.path.exists(ALERTS):
        os.remove(ALERTS)


if __name__ == '__main__':
    main()
