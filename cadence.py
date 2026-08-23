"""kimi: email cadence engine — the 4-touch owner sequence that auto-cancels on reply.

How it works end to end:
  1. In the tracker, enroll owners from any row: Links -> Cadence, then Cadence (header) ->
     Export queue. That produces cadence_queue.json next to this script.
  2. Run `python cadence.py` on this machine (double-click cadence-run.bat, or schedule it).
     It sends every step that's DUE today over Gmail SMTP (gmail.key, gitignored), schedules the
     next touch (day 0/2/4/7), and advances the state in cadence_state.json.
  3. Every run also polls the inbox (IMAP) for anything FROM an enrolled owner. The moment a
     reply lands, that sequence is CANCELLED for good — the lead goes warm and no more touches
     go out. Replies containing stop/unsubscribe/para/detener additionally write optouts.json in
     the tracker's notes-import format so the do-not-contact ledger picks them up on import.

Run `python cadence.py --dry-run` first: prints exactly what it would send, to whom, and what the
reply check would do — without touching the mail server. gmail.key = one line:  you@gmail.com:apppassword
"""
import argparse, json, os, re, smtplib, ssl, sys, time
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, 'cadence_queue.json')
STATE = os.path.join(HERE, 'cadence_state.json')
KEY = os.path.join(HERE, 'gmail.key')
OPTOUTS = os.path.join(HERE, 'optouts.json')

STOP_WORDS = re.compile(r'\b(stop|unsubscribe|remove me|do not contact|para|detener|dejen de escribir|no contactar)\b', re.I)

# ---- the 4-touch sequence (EN, with the ES block every owner expects from this operation) ------
def steps(lead, sender):
    first = (lead.get('owner') or '').split(',')[0].split()[0].title() or 'there'
    # The board stores addresses as "455 NE 210 TER, MIAMI, FL- 33179". That stray hyphen after the
    # state, and the shouted city, appear in every message and are the most machine-looking thing on
    # the page. Nobody writes their own address that way. Tidy it for display only.
    addr = lead.get('addr') or 'your property'
    if addr != 'your property':
        addr = re.sub(r',\s*([A-Z]{2})-\s*', r', \1 ', addr)
        parts = [p.strip() for p in addr.split(',')]
        if len(parts) >= 2 and parts[1].isupper():
            parts[1] = parts[1].title()
        addr = ', '.join(p for p in parts if p)
    auc = lead.get('auction') or 'the scheduled date'
    sn = sender.get('name') or ''
    sp = sender.get('phone') or ''
    se = sender.get('email') or ''
    sllc = sender.get('llc') or ''
    sig = f"\n\n{sn}" + (f"\n{sllc}" if sllc else '') + (f"\n{sp}" if sp else '') + (f"\n{se}" if se else '')
    # ADDED 2026-08-22. All four touches shipped with a STOP line and NOTHING else — no
    # not-an-attorney, no not-a-rescue-company, no fee statement. Every other outward channel
    # carries this: msg_letter and msg_flyer run the full MARS/Reg O block, outreach_mail says
    # "not a lender, not a foreclosure-rescue company, and not an attorney", and outreach_email
    # was given the same line earlier today. cadence.py builds its own bodies in this function,
    # so none of that reached it.
    #
    # It matters most HERE. Touch 2 tells an owner in foreclosure there may be "real money left
    # over ... that belongs to you" and offers a call about it. FS 501.1377 turns on holding
    # yourself out as offering a foreclosure-related service for a fee, so the denial and the
    # "no fee" statement are the two sentences that keep that pitch on the right side of it.
    # VOICE, rewritten 2026-08-22. Every touch used to lean on em dashes, five per message, which
    # is the single loudest tell that a machine wrote it. call_mode.py's TEXT_T templates already
    # solved this for SMS on 08-18 with the note "no em dashes (reads as AI in a text)", so the
    # house voice already existed and only the email side had missed it: short declaratives, commas
    # instead of dashes, a cushion before the ask, one ask per message. These now match.
    disc = ("\n\nI am not your lender, not the government, not a foreclosure-rescue company, and "
            "not an attorney. Nothing here is legal advice, and there is never a fee to talk to me.")
    unsub = ("\n\n(If you'd rather not hear from me, reply 'stop' and you won't hear from me again. "
             "No hard feelings.)")
    s0 = (f"Hi {first},\n\nMy name is {sn}. I work with a small local team that helps owners in "
          f"foreclosure. Your property at {addr} has an auction scheduled for {auc}.\n\n"
          f"I'm not calling to pressure you. I just want to make sure you've seen your options before "
          f"that date, because most of them close when the sale happens. If you already have a plan, "
          f"keep it. If you want a second look at the numbers, it costs you nothing."
          + sig + disc + unsub)
    s1 = (f"Hi {first},\n\nFollowing up on {addr}. Public records suggest there may be money left "
          f"over after the loan is paid off. If there is, it belongs to you and not the bank, but it "
          f"has to be handled before {auc}.\n\nTen minutes on the phone is usually enough to tell "
          f"whether your numbers work that way. It costs you nothing, and I'd rather you know than "
          f"guess." + sig + disc + unsub)
    s2 = (f"Hi {first},\n\nA few details before {auc}, in plain terms. Owners in your situation "
          f"usually have three real options.\n\n"
          f"  1) Stop the sale and buy time, usually 60 to 90 days, to regroup.\n"
          f"  2) Sell before the auction and keep the equity yourself.\n"
          f"  3) Borrow against the equity and stay in the home.\n\n"
          f"Which one fits comes down to your numbers. I can walk you through all three against your actual "
          f"property at {addr}, and it costs you nothing." + sig + disc + unsub)
    s3 = (f"Hi {first},\n\nThis is my last note about {addr}. The {auc} date is close, and once the "
          f"sale happens the options close with it.\n\nWhatever you decide, including deciding to let "
          f"it go, decide it with your own numbers in front of you instead of the bank's. If a 10 "
          f"minute call helps, I'm around." + sig + disc + unsub)
    subj = [f"About {addr.split(',')[0]} before the auction date",
            f"the part of {addr.split(',')[0]} that belongs to you",
            f"3 options before {auc} for {addr.split(',')[0]}",
            f"last note on {addr.split(',')[0]} before {auc}"]
    es = ("\n\n---\n\n(ES) Hablo español con gusto. Si le es más cómodo, respóndame en español "
          "y seguimos por escrito o por teléfono. No soy su prestamista, no soy del gobierno, "
          "no soy una empresa de rescate de ejecuciones, y no soy abogado; esto no es asesoría "
          "legal y nunca hay cargo por hablar conmigo.\n")
    bodies = [s0 + es, s1 + es, s2 + es, s3 + es]
    return list(zip(subj, bodies))


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def load_key():
    if not os.path.exists(KEY):
        return None
    line = open(KEY, encoding='utf-8').read().strip()
    if ':' not in line:
        return None
    user, pw = line.split(':', 1)
    return user.strip(), pw.strip()


def imap_replies(cred, emails, dry):
    """Return {email: 'replied'|'stopped'} for anything found in the inbox from these addresses."""
    if not emails:
        return {}
    if dry:
        print(f'  [dry-run] would IMAP-search {len(emails)} owner addresses for replies/STOP words')
        return {}
    import imaplib
    out = {}
    user, pw = cred
    try:
        M = imaplib.IMAP4_SSL('imap.gmail.com')
        M.login(user, pw)
        M.select('INBOX')
        for em in emails:
            typ, data = M.search(None, f'(FROM "{em}")')
            if typ != 'OK' or not data or not data[0].split():
                continue
            out[em] = 'replied'
            # read the latest one for STOP words
            latest = data[0].split()[-1]
            typ, body = M.fetch(latest, '(RFC822.TEXT)')
            if typ == 'OK' and body and body[0] and isinstance(body[0], tuple) and STOP_WORDS.search(body[0][1].decode('utf-8', 'ignore')):
                out[em] = 'stopped'
        M.logout()
    except Exception as e:
        print('  IMAP check failed (skipping this pass):', str(e)[:90])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='render and report only — no mail sent')
    args = ap.parse_args()
    today = date.today()

    payload = load_json(QUEUE, None)
    if not payload or 'queue' not in payload:
        print('no cadence_queue.json — export it from the tracker (Cadence button) first.')
        return 1
    sender = payload.get('sender') or {}
    queue = payload['queue']
    state = load_json(STATE, {})
    cred = load_key()
    if not cred and not args.dry_run:
        print('gmail.key missing (you@gmail.com:apppassword) — running as dry-run.')

    # enroll fresh queue entries into state (idempotent — an already-known case keeps its progress)
    for lead in queue:
        c = lead.get('case')
        if not c:
            continue
        if c not in state:
            state[c] = {'step': lead.get('step', 0), 'next': lead.get('next') or str(today),
                        'status': 'active', 'owner': lead.get('owner'), 'addr': lead.get('addr'),
                        'email': lead.get('email'), 'auction': lead.get('auction'), 'log': []}
        elif state[c].get('status') in ('stopped', 'replied', 'cancelled', 'completed'):
            state[c] = state[c]   # never resurrect a finished sequence by re-exporting
        else:
            state[c]['owner'] = lead.get('owner'); state[c]['addr'] = lead.get('addr')
            state[c]['email'] = lead.get('email'); state[c]['auction'] = lead.get('auction')

    active = {c: s for c, s in state.items() if s.get('status') == 'active' and s.get('email')}

    # 1) reply check FIRST — never send another touch to someone who already wrote back
    replies = imap_replies(cred, [s['email'] for s in active.values()], args.dry_run or not cred)
    for c, s in active.items():
        got = replies.get(s['email'])
        if got == 'replied':
            s['status'] = 'replied'; s['log'].append({'d': str(today), 'ev': 'reply — sequence auto-cancelled'})
        elif got == 'stopped':
            s['status'] = 'stopped'; s['log'].append({'d': str(today), 'ev': 'STOP word — opt-out'})
    if any(r == 'replied' for r in replies.values()):
        print(f"  auto-cancelled on reply: {sum(1 for r in replies.values() if r=='replied')}")

    # write opt-outs in the tracker's notes-import shape (merge via Sync/team -> Import)
    stopped = {c: s for c, s in state.items() if s.get('status') == 'stopped'}
    if stopped:
        notes = {c: {'status': 'DO NOT CONTACT', 'optout': str(today),
                     'optlog': [{'ts': datetime.now().isoformat(timespec='seconds'), 'act': 'set-local', 'src': 'cadence stop-word'}]}
                 for c in stopped}
        json.dump({'notes': notes}, open(OPTOUTS, 'w', encoding='utf-8'), indent=1)
        print(f'  optouts.json updated for {len(stopped)} owner(s) — import it in the tracker (Sync/team)')

    # 2) send due steps
    sent = 0
    ctx = ssl.create_default_context()
    smtp = None
    if cred and not args.dry_run:
        smtp = smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx)
        smtp.login(*cred)
    for c, s in active.items():
        due = s.get('next') or str(today)
        if due > str(today):
            continue
        step = int(s.get('step', 0))
        if step >= 4:
            s['status'] = 'completed'
            continue
        subj, body = steps(s, sender)[step]
        if args.dry_run or not cred:
            print(f"  [dry-run] {s['email']}  step {step+1}/4  '{subj}'")
            print('    ' + body.replace('\n', ' ')[:130] + '…')
            continue
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subj
        msg['From'] = formataddr((sender.get('name') or cred[0], cred[0]))
        msg['To'] = s['email']
        smtp.send_message(msg)
        sent += 1
        print(f"  sent step {step+1}/4 -> {s['email']}  ({s['owner']})")
        s['log'].append({'d': str(today), 'ev': f'sent step {step+1}'})
        gaps = [0, 2, 2, 3]
        s['step'] = step + 1
        if s['step'] >= 4:
            s['status'] = 'completed'
        else:
            s['next'] = str(today + timedelta(days=gaps[s['step']]))
    if smtp:
        smtp.quit()

    json.dump(state, open(STATE, 'w', encoding='utf-8'), indent=1)
    print(f'done. {sent} sent, {sum(1 for s in state.values() if s.get("status")=="active")} active, '
          f'{sum(1 for s in state.values() if s.get("status")=="replied")} replied-cancelled.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
