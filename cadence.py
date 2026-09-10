"""kimi: email cadence engine — the 4-touch owner sequence that auto-cancels on reply.

How it works end to end:
  1. In the tracker, enroll owners from any row: Links -> Cadence, then Cadence (header) ->
     Export queue. That produces cadence_queue.json next to this script.
  2. Run `python cadence.py` on this machine (double-click cadence-run.bat, or schedule it).
     It sends every step that's DUE today over Gmail SMTP (gmail.key, gitignored), schedules the
     next touch (day 0/2/4/7), and advances the state in cadence_state.json.
  3. Every run also polls the inbox (IMAP) for anything FROM an enrolled owner. The moment a
     reply lands, that sequence is CANCELLED for good — the lead goes warm and no more touches
     go out. Replies containing stop/unsubscribe/para/detener are additionally ledgered straight
     into optouts.json (add-only, both the case key and the '@email' key) and onto the
     bounced_emails.json hard-suppression list the send bridge enforces.
  4. Before any of that, every run re-reads the opt-out ledger and drops anyone suppressed since
     the queue was exported. The queue is a snapshot; the ledger is the truth.

The company name in the signature comes from entity.display_llc() — the " LLC" suffix prints only
on a strict ACTIVE Sunbiz match. Never sign with a raw sender.llc string; see safe_llc() below.

Run `python cadence.py --dry-run` first: prints exactly what it would send, to whom, and what the
reply check would do — without touching the mail server. gmail.key = one line:  you@gmail.com:apppassword
"""
import argparse, json, os, re, smtplib, ssl, sys, time
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

import entity
import outreach_email as _oe
import mail_guard as _MG

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, 'cadence_queue.json')
STATE = os.path.join(HERE, 'cadence_state.json')
KEY = os.path.join(HERE, 'gmail.key')
OPTOUTS = os.path.join(HERE, 'optouts.json')

# ── LANE MAP (wired 2026-09-07) ────────────────────────────────────────────────────────────────
# Until today this module sent every follow-up as whatever `gmail.key` held, which for a month was
# the PERSONAL Gmail — so the same owner got a cold email from a BSG domain and its follow-up from
# a personal address. Now it uses senders.json exactly like the bridge does.
#
# 🔴 THE REASON THIS IMPORTS INSTEAD OF COPYING. The per-alias warm-up cap is metered off
# mail_sent.json by _alias_sent_today(): it counts rows whose `from` is the alias. cadence has never
# written to that ledger at all, so a private copy of the lane logic would send from bsgfl.com while
# the bridge still reported 0/5 — two independent senders on one warming alias, and the ramp becomes
# a number nobody is enforcing. Sharing the FUNCTIONS is not enough; the ledger WRITE below is what
# makes the cap real. Same reason the mail/text ledgers exist at all.
#
# send_server only binds a port under `if __name__ == '__main__'`, so importing it is side-effect
# free. If the import ever fails this module falls back to its original behaviour rather than
# breaking a scheduled job — announced loudly, because silence would look like the lanes are on.
try:
    import send_server as _ss
    _LANES_OK = True
except Exception as _e:                                    # pragma: no cover - defensive
    _ss, _LANES_OK = None, False
    print('cadence: send_server import failed (%s) — sending as the login, NO lane map, NO ledger '
          'row, NO warm-up cap.' % (str(_e)[:80],))


def _save_state(state):
    """Atomic write of cadence_state.json. tmp + os.replace, the same shape the opt-out ledger
    uses: this file is now written after EVERY send, so a crash mid-write must never be able to
    leave a truncated state behind — that would lose the whole sequence, not one step."""
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE)


def _cadence_lane(entry, today):
    """Sale-date proximity -> Morning Worker lane, so a follow-up leaves from the SAME domain the
    lead's cold email did. Mirrors WORKER_LANES: urgent <=7d, active 8d and beyond, early = no date.

    'replied' is deliberately unreachable here: a reply CANCELS the sequence (status 'replied'), so
    by construction nothing cadence sends is going to someone who wrote back. No auction date, an
    unparseable one, or one already passed falls to `early` — the coldest lane and the slowest ramp,
    the conservative direction for a lead whose timeline we cannot read.

    2026-09-08: a sale beyond 45 days is ACTIVE, not early. It is still a dated, cold sale-date lead;
    `early` is for fresh filings with no date at all. outreach_email._lane_of makes the same call, so
    a lead's cold email and its follow-ups leave from the same alias whichever path sends them."""
    raw = str(entry.get('auction') or '').strip()
    if not raw:
        return 'early'
    d = None
    for f in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y'):
        try:
            d = datetime.strptime(raw, f).date()
            break
        except ValueError:
            continue
    if not d:
        return 'early'
    days = (d - today).days
    if days < 0:
        return 'early'
    if days <= 7:
        return 'urgent'
    return 'active'          # 8 days and beyond, 45 included — see docstring

# STOP DETECTION IS NOT LOCAL ANY MORE. This module used to own the regex below, and earlier today
# (d53955d) I made its verdict permanent: a detected stop now writes optouts.json and the
# bounced_emails.json hard-suppression list, which every channel reads. That change is only as good
# as the detector behind it, and hours later replies.py proved this exact pattern wrong in BOTH
# directions (f80f5e6): `\bstop\b` matched "Can you stop the foreclosure?" and "Is there any way to
# stop the sale?" -- the most motivated reply a homeowner in foreclosure can send, and the central
# phrase of the business -- while missing "take me off your list", "opt me out" and "leave me
# alone" entirely. So the naive version was auto-suppressing the hottest leads across every channel
# and letting real opt-outs through, and my commit is what promoted that mistake from a local status
# flag to a permanent cross-channel ledger entry.
#
# replies.is_stop_text() separates the two readings (explicit opt-out phrasing wins; a bare "stop"
# counts unless every occurrence is followed by a case-object) and is covered by _suppressiontest.
# Call it. Do not re-implement it here -- a second detector is how the two drift apart, and the half
# that drifts is the half nobody is testing.
def _is_stop(text):
    try:
        from replies import is_stop_text
    except Exception:
        # Fail CLOSED toward suppression: if the shared detector cannot be imported, treat an
        # explicit opt-out word as a stop rather than silently contacting someone who asked to end.
        return bool(re.search(r'\b(unsubscribe|remove me|do not contact|no contactar)\b', text or '', re.I))
    return bool(is_stop_text(text))


def safe_llc(raw):
    """The company name as it may legally be signed, per the Sunbiz gate in entity.py.

    ADDED 2026-08-26. cadence.py was the ONE owner-facing channel that never consulted the gate.
    Every other surface does -- bsg_letter, bsg_flyer, outreach_mail, call_mode,
    carlos_letter_packet, foreclosure_leads -- but cadence builds its own signature block from
    cadence_queue.json's sender dict, and that queue was exported 2026-08-23 10:43, hours BEFORE
    the gate landed. So it carried "Biscayne Solutions Group LLC" while entity_status.json said
    NOT_FOUND, and signed 59 emails with it on 08-24 and 08-25.

    A queue is a frozen snapshot of a sender profile, so it can also still hold the retired
    'Miami Solutions Group' -- which belongs to a DIFFERENT Florida company. display_llc() maps
    retired names forward itself (entity.RETIRED) before deciding about the suffix, so a stale
    queue is covered by the same gate as a stale sender.json.
    Fail-closed: on any doubt this returns the bare name, never the entity claim."""
    name, _doc, warn = entity.display_llc((raw or '').strip())
    return name, warn

# ---- the 4-touch sequence (EN, with the ES block every owner expects from this operation) ------
def steps(lead, sender):
    first = (lead.get('owner') or '').split(',')[0].split()[0].title() or 'there'
    # The board stores addresses as "455 NE 210 TER, MIAMI, FL- 33179". That stray hyphen after the
    # state, and the shouted city, appear in every message and are the most machine-looking thing on
    # the page. Nobody writes their own address that way. Tidy it for display only.
    # FALLBACK AFTER THE CLEAN, NOT BEFORE. This block used to open `addr = lead.get('addr') or
    # 'your property'` and then tidy it — but a whitespace- or comma-only address is TRUTHY, so the
    # fallback never fired, and the tidy's own `if p` filter then dropped every part and left an
    # empty string. That is what sent a homeowner "This is my last note about ." on 2026-09-07.
    # safe_addr does the same tidying and decides afterwards whether anything survived.
    addr = _MG.safe_addr(lead.get('addr'))
    auc = lead.get('auction') or 'the scheduled date'
    sn = sender.get('name') or ''
    sp = sender.get('phone') or ''
    se = sender.get('email') or ''
    sllc = safe_llc(sender.get('llc'))[0]
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
          f"has to be handled before {auc}.\n\nFive minutes on the phone is usually enough to tell "
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
    _street = _MG.safe_street(lead.get('addr'))
    subj = [f"About {_street} before the auction date",
            f"the part of {_street} that belongs to you",
            f"3 options before {auc} for {_street}",
            f"last note on {_street} before {auc}"]
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
            if typ == 'OK' and body and body[0] and isinstance(body[0], tuple) and \
                    _is_stop(body[0][1].decode('utf-8', 'ignore')):
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
    # Surface the entity gate's verdict in the run log. This is a scheduled job -- if the warning
    # is only a return value nobody reads it, and "why is my company name missing its LLC" becomes
    # a mystery instead of a one-line answer sitting in cadence-run.log.
    _llc, _llcwarn = safe_llc(sender.get('llc'))
    if _llcwarn:
        print('  ENTITY GATE: signing as %r — %s' % (_llc, _llcwarn))
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
        elif state[c].get('status') in ('stopped', 'suppressed', 'replied', 'cancelled', 'completed'):
            state[c] = state[c]   # never resurrect a finished sequence by re-exporting
        else:
            state[c]['owner'] = lead.get('owner'); state[c]['addr'] = lead.get('addr')
            state[c]['email'] = lead.get('email'); state[c]['auction'] = lead.get('auction')

    active = {c: s for c, s in state.items() if s.get('status') == 'active' and s.get('email')}

    # 0) SUPPRESSION SWEEP, at send time.
    #
    # ADDED 2026-08-26. Until now cadence checked opt-outs exactly once: at queue-EXPORT time, on
    # the board, on whichever machine built cadence_queue.json. Everything after that ran blind.
    # An owner who told Carlos to stop at the door, said stop on the phone, or whose STOP reply was
    # ledgered by optout_sync overnight, stayed enrolled and kept receiving touches -- and this
    # engine is scheduled, so nobody is watching when it sends. The queue in use here was exported
    # 2026-08-23 and drives sends for a week or more; a one-time filter cannot hold that long.
    #
    # Reuses outreach_email._load_optouts(), which unwraps the {_dealflow_notes, exported, device,
    # notes:{...}} envelope correctly and returns lowercased keys with the '@' stripped, so a case
    # number and an email address both match. That function carries its own war story: it was dead
    # code until 2026-08-19 and a written STOP was followed by a fresh cold email. Call it, do not
    # re-implement it.
    _oo = _oe._load_optouts()
    _sup = load_json(os.path.join(HERE, 'bounced_emails.json'), {})
    _sup = {str(k).strip().lower() for k in _sup} if isinstance(_sup, dict) else set()
    for c, s in list(active.items()):
        em = (s.get('email') or '').strip().lower()
        why = ('opt-out ledger' if (c.strip().lower() in _oo or em in _oo)
               else 'hard-suppression list' if em in _sup else '')
        if why:
            # NOT 'stopped' -- that status means "this engine heard a stop word" and feeds the
            # ledger write below. These are already suppressed elsewhere; re-ledgering them under
            # cadence's provenance would forge the record of how we learned.
            s['status'] = 'suppressed'
            s['log'].append({'d': str(today), 'ev': f'suppressed before send ({why})'})
            active.pop(c)
            print(f'  SUPPRESSED (not sent) -> {em or c}  ({s.get("owner")}) — {why}')

    # 0b) DILIGENCE SWEEP, also at send time, and for the same reason as the sweep above.
    #
    # build_cadence_queue gates enrolment, which is correct and is not sufficient. A cadence_queue
    # entry is {case, owner, addr, email, auction, step, next} — no value, no judgment, no parties,
    # no sale year — so nothing in it can be diligence-checked on its own, and cadence_state.json
    # keeps a sequence ALIVE across rebuilds by design (an already-known case keeps its progress).
    # An owner enrolled before the gate existed therefore keeps receiving touches 2, 3 and 4 forever
    # no matter what the gate later concludes, and this engine has no cap, no throttle and no
    # --limit. So look the case back up in the lead files and re-ask, every run.
    #
    # 'held' is its own status, NOT 'suppressed': suppressed means a human said stop and it is
    # permanent. A diligence hold is WORK — clear the reason, rebuild the queue, and the sequence
    # resumes. Conflating the two would quietly convert a verification task into a dead lead.
    try:
        import diligence_gate as _dg
        _rows = {}
        for _r in (_oe._load_leads() or []):
            _c = _oe._case(_r)
            if _c and _c not in _rows:
                _rows[_c] = _r
        _held = 0
        _nolookup = 0
        for c, s in list(active.items()):
            _row = _rows.get(c)
            if _row is None:
                _nolookup += 1          # off the board (auction passed, file rotated) — not a hold
                continue
            _g = _dg.gate(_row)
            if _g['hold']:
                _held += 1
                s['status'] = 'held'
                s['log'].append({'d': str(today),
                                 'ev': 'held before send — diligence %s: %s' % (_g['code'],
                                                                               _dg._clip(_g['why'], 160))})
                active.pop(c)
                print('  HELD (not sent) -> %s  (%s) — diligence %s: %s'
                      % (s.get('email') or c, s.get('owner'), _g['code'], _dg._clip(_g['why'], 120)))
        if _held or _nolookup:
            print('  diligence sweep: %d sequence(s) held, %d case(s) not found in the lead files '
                  '(off-board, left running)' % (_held, _nolookup))
    except Exception as _dge:
        # A cadence run that cannot diligence-check must still deliver its opt-out sweep and its
        # reply check. Say the protection is off; do not take the engine down with it.
        print('  !! diligence sweep SKIPPED (%s) — this run is sending UNGATED.' % str(_dge)[:120])

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

    # write opt-outs into the SERVER LEDGER, add-only (the board, call_list, carlos_* routes,
    # morning_planner and outreach_email all read this file).
    #
    # REWRITTEN 2026-08-26. This used to be `json.dump({'notes': notes}, open(OPTOUTS, 'w'))` --
    # a full-file OVERWRITE with only cadence's own stop-word set. It would have dropped the
    # {_dealflow_notes, exported, device} envelope every other consumer unwraps, and erased every
    # opt-out this engine did not personally detect: Norma Hendy, gil_sosa (a wrong-person hit),
    # and the rest. It had not fired yet only because no enrolled owner had replied STOP. Same
    # one-way posture as optout_sync.py: ADD only, never clear, never downgrade, never rewrite an
    # entry a human left. Re-running is a no-op.
    #
    # BOTH KEYS, same reason optout_sync writes both: the ledger is case-keyed but replies are
    # email-keyed, and suppressing one key and not the other is exactly how a handled opt-out
    # comes back. The raw address also goes on the hard-suppression list the send bridge enforces.
    stopped = {c: s for c, s in state.items() if s.get('status') == 'stopped'}
    if stopped and args.dry_run:
        print(f'  [dry-run] would ledger {len(stopped)} opt-out(s): {", ".join(stopped)}')
    elif stopped:
        opt = load_json(OPTOUTS, {}) or {}
        if not isinstance(opt, dict):
            opt = {}
        opt.setdefault('_dealflow_notes', 1)
        opt.setdefault('device', 'server-ledger')
        opt['exported'] = str(today)
        notes = opt.setdefault('notes', {})
        sup = load_json(os.path.join(HERE, 'bounced_emails.json'), {})
        if not isinstance(sup, dict):
            sup = {}
        now = datetime.now().isoformat(timespec='seconds')
        added, sup_new = [], []
        for c, s in stopped.items():
            em = (s.get('email') or '').strip().lower()
            entry = {'status': 'DO NOT CONTACT', 'optout': str(today),
                     'note': ('AUTO-LEDGERED by cadence.py: the owner replied with a stop word to '
                              'the 4-touch sequence. Covers ALL channels: no email, no call, no '
                              'text, no door. Owner %s, %s.'
                              % (s.get('owner') or '?', s.get('addr') or '?')),
                     'optlog': [{'ts': now, 'act': 'opted-out', 'src': 'cadence stop-word (IMAP reply)'}]}
            for key in (c, ('@' + em) if em else ''):
                if key and key not in notes:
                    notes[key] = dict(entry)
                    added.append(key)
            if em and em not in sup:
                sup[em] = {'type': 'optout', 'when': str(today), 'why': 'cadence stop-word reply'}
                sup_new.append(em)
        if added:
            tmp = OPTOUTS + '.tmp'
            json.dump(opt, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
            os.replace(tmp, OPTOUTS)
        if sup_new:
            _b = os.path.join(HERE, 'bounced_emails.json')
            tmp = _b + '.tmp'
            json.dump(sup, open(tmp, 'w', encoding='utf-8'), indent=0, ensure_ascii=False)
            os.replace(tmp, _b)
        print(f'  optouts.json: {len(added)} key(s) ledgered for {len(stopped)} owner(s), '
              f'{len(sup_new)} address(es) hard-suppressed')

    # 2) send due steps
    sent = 0
    capped = {}
    ctx = ssl.create_default_context()
    smtp = None
    # The legacy connection is only built when the lane path is unavailable — _smtp_send opens its
    # own per message (what the bridge does), and the ramp caps bound the daily volume anyway.
    if cred and not args.dry_run and not _LANES_OK:
        smtp = smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx)
        smtp.login(*cred)
    _cfg = _ss._load_senders() if _LANES_OK else {}
    # The map only applies when the LOGIN can send as the aliases (bsg_gmail.key). On the old
    # personal gmail.key Google rewrites From, so senders_active is False and this stays as it was.
    _map_on = bool(_LANES_OK and cred and _cfg and _ss._senders_active(cred[0], _cfg))
    if _LANES_OK and cred and not _map_on:
        print(f'  lane map OFF — login {cred[0]} cannot send as the aliases; '
              f'everything leaves as the login (copy bsg_gmail.key to enable).')
    for c, s in active.items():
        due = s.get('next') or str(today)
        if due > str(today):
            continue
        step = int(s.get('step', 0))
        if step >= 4:
            s['status'] = 'completed'
            continue
        subj, body = steps(s, sender)[step]
        lane = _cadence_lane(s, today)
        alias = _ss._lane_from(_cfg, lane) if _map_on else ''
        if args.dry_run or not cred:
            _as = f'  [{lane} -> {alias}]' if alias else f'  [{lane} -> login]'
            print(f"  [dry-run] {s['email']}  step {step+1}/4  '{subj}'{_as}")
            print('    ' + body.replace('\n', ' ')[:130] + '…')
            # The guard lives in _smtp_send, which a dry run never reaches — so without this the
            # one command whose entire job is "show me what would go out" was the one command that
            # could not see a hole in it. Report here, do not raise: a dry run should list every
            # problem in the batch, not stop at the first.
            for _why in _MG.check(subj, body, s['email']):
                print(f'    !! WOULD BE REFUSED — {_why}')
            continue
        # WARM-UP CAP, metered off the SHARED ledger so cadence and the bridge draw on one budget.
        # Skipped WITHOUT advancing the step: the touch stays due and goes out tomorrow rather than
        # being silently consumed. That is the whole difference between a paced ramp and a lost step.
        if alias:
            used, cap = _ss._alias_sent_today(alias), _ss._ramp_cap(_cfg, alias)
            if used >= cap:
                capped[alias] = capped.get(alias, 0) + 1
                continue
        mid = ''
        if _LANES_OK:
            # Reuses the bridge's own sender: alias in From AND the envelope, Message-ID stamped
            # with the alias domain. from_addr=None reproduces the old login-From behaviour exactly.
            mid = _ss._smtp_send(cred[0], cred[1], sender.get('name') or '', s['email'],
                                 subj, body, from_addr=alias or None)
        else:
            msg = MIMEText(body, 'plain', 'utf-8')
            msg['Subject'] = subj
            msg['From'] = formataddr((sender.get('name') or cred[0], cred[0]))
            msg['To'] = s['email']
            smtp.send_message(msg)
        sent += 1
        # THE LEDGER ROW — same shape the bridge writes, and the reason the cap above can work at
        # all. `from` is what _alias_sent_today counts; `message_id` is what makes the row count as
        # a real send (_mail_ledger ignores rows without one). Never fatal: the mail is already
        # delivered by this point, so a ledger failure is reported, not raised.
        if _LANES_OK:
            try:
                _ss._append_ledger({
                    'd': str(today),
                    'ts_utc': datetime.now(timezone.utc).isoformat(),
                    'ch': 'email',
                    'from': alias or cred[0], 'to': s['email'], 'bcc': '',
                    'login': cred[0],
                    'owner': s.get('owner') or '', 'case': c, 'addr': s.get('addr') or '',
                    'lang': 'en', 'portfolio': [],
                    'subj': subj, 'body_len': len(body),
                    'message_id': mid,
                    'test_mode': False,
                    'lane': 'cadence', 'wl': lane,
                })
            except Exception as e:
                print(f'  WARNING: {s["email"]} was emailed (mid={mid}) but the ledger write '
                      f'failed ({str(e)[:90]}) — this send will not count toward the alias cap.')
        print(f"  sent step {step+1}/4 -> {s['email']}  ({s['owner']})"
              + (f'  as {alias} [{lane}]' if alias else ''))
        s['log'].append({'d': str(today), 'ev': f'sent step {step+1}'})
        gaps = [0, 2, 2, 3]
        s['step'] = step + 1
        if s['step'] >= 4:
            s['status'] = 'completed'
        else:
            s['next'] = str(today + timedelta(days=gaps[s['step']]))
        # PERSIST AFTER EVERY SEND, not once at the end of the run.
        # The step advance above was in MEMORY only, and the single json.dump lived past the end of
        # this loop — so a run that died, was killed, hit an SMTP error or was closed mid-batch
        # threw away the record of every mail it had already delivered, and the next run re-sent
        # them. That is not hypothetical: one owner received the identical step-2 follow-up on
        # 2026-08-25 16:14 and again on 2026-08-26 12:39. The mail is already gone by this line, so
        # the only question is whether we remember it, and a write failure here must be LOUD rather
        # than leave a delivered mail unrecorded.
        try:
            _save_state(state)
        except Exception as e:
            print(f'  !! {s["email"]} WAS EMAILED but cadence_state.json could not be written '
                  f'({str(e)[:80]}) — this step may be RE-SENT on the next run. Fix the file now.')
    if smtp:
        smtp.quit()
    for a, n in sorted(capped.items()):
        print(f'  warm-up cap reached on {a} — {n} step(s) held for tomorrow (not consumed).')

    _save_state(state)
    print(f'done. {sent} sent, {sum(1 for s in state.values() if s.get("status")=="active")} active, '
          f'{sum(1 for s in state.values() if s.get("status")=="replied")} replied-cancelled, '
          f'{sum(1 for s in state.values() if s.get("status")=="suppressed")} suppressed, '
          f'{sum(1 for s in state.values() if s.get("status")=="stopped")} opted-out.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
