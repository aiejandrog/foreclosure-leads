#!/usr/bin/env python
"""send_server.py -- localhost SMTP bridge for the Morning Worker.

The browser can't call SMTP directly. This is a tiny HTTP server (127.0.0.1 only) that accepts
POST /send requests from the tracker tab, does the real SMTP send via gmail.key, and writes
mail_sent.json. So a click of the worker's Email button becomes a REAL send, not "open Gmail
then click Send again."

WHY 127.0.0.1 ONLY (not 0.0.0.0): the bind address is the only security boundary here. On
127.0.0.1 only processes on THIS machine can reach it; nothing on the LAN, nothing from the
internet, nothing from anyone else's browser. Malware already on the machine can read gmail.key
directly, so this endpoint doesn't widen the attack surface -- it just automates what a human
sitting at the keyboard could already do.

HOW TO RUN:
    python send_server.py                    # binds 127.0.0.1:8823 by default
    python send_server.py --port 8824        # different port
    python send_server.py --limit 100        # override 50/day cap for a batch night

Leave the terminal open. The tracker tab talks to it. Close the terminal and the worker
gracefully falls back to opening Gmail compose (the pre-existing behavior).

ENDPOINTS:
    GET  /health   -> {"ok": true, "user": "you@gmail.com", "sent_today": 3, "cap": 50}
    POST /send     -> {"to","subj","body","meta":{"c","owner","addr","lang","portfolio","test"}}
                      returns {"ok": true, "message_id": "..."} or {"ok": false, "err": "..."}
    POST /notes    -> the tracker's full localStorage state {_dealflow_notes, device, notes,
                      workerLog, sentArchive}. Written atomically to worker_notes.json plus a
                      daily snapshot in worker_notes_snapshots/. This is how call dispositions,
                      statuses and the activity feed reach DISK — before this, the entire call
                      history of the business lived only inside one Chrome profile.
"""
import argparse
import datetime as dt
import json
import mimetypes
import os
import re
import smtplib
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, 'gmail.key')
SENDER_FILE = os.path.join(HERE, 'sender.json')
SENT_LEDGER = os.path.join(HERE, 'mail_sent.json')
NOTES_FILE = os.path.join(HERE, 'worker_notes.json')
NOTES_SNAP_DIR = os.path.join(HERE, 'worker_notes_snapshots')

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_LEDGER_LOCK = threading.Lock()
_NOTES_LOCK = threading.Lock()


# ---------------------------------------------------------------- credential + sender loading
def _load_credentials():
    env_pw = (os.environ.get('GMAIL_APP_PASSWORD') or '').strip()
    if not os.path.exists(KEY_FILE):
        return None, env_pw or None
    raw = open(KEY_FILE, encoding='utf-8').read().strip()
    if ':' in raw:
        user, pw = raw.split(':', 1)
        user, pw = user.strip().lower(), pw.strip()
        if env_pw:
            pw = env_pw
        if _EMAIL_RE.match(user) and pw:
            return user, pw
    return None, None


def _load_sender():
    if not os.path.exists(SENDER_FILE):
        return {}
    try:
        return json.load(open(SENDER_FILE, encoding='utf-8'))
    except Exception:
        return {}


def _load_ledger():
    if not os.path.exists(SENT_LEDGER):
        return []
    try:
        data = json.load(open(SENT_LEDGER, encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _append_ledger(entry):
    """Atomic write. Lock protects against two concurrent /send requests corrupting the file.

    os.replace() on Windows can throw a transient PermissionError (WinError 5) when something else
    — antivirus, Windows Search indexing, OneDrive, a stray `open()` from an ad-hoc diagnostic
    script — has the destination file open for a few milliseconds. POSIX rename tolerates this;
    Windows does not. Caught live 2026-08-05: a real, already-delivered email hit this on its
    ledger write, AFTER the SMTP send had already succeeded — the write itself has no business
    logic to retry, it just needs to survive a momentary lock. 3 attempts with a short backoff
    clears the overwhelming majority of these without changing behavior on a real, persistent
    failure (permissions actually wrong, disk full, etc. still raise after the last attempt)."""
    with _LEDGER_LOCK:
        log = _load_ledger()
        log.append(entry)
        tmp = SENT_LEDGER + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2, ensure_ascii=False)
        for attempt in range(3):
            try:
                os.replace(tmp, SENT_LEDGER)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.15 * (attempt + 1))


def _richness(payload):
    """A rough 'how much real call history is in here' score. An empty / fresh browser still carries
    ~1 note (the opt-out ledger auto-bakes on every load), so counting notes alone isn't enough —
    weight actual activity (touches + the worker log + the sent archive)."""
    notes = (payload or {}).get('notes') or {}
    touches = 0
    for n in notes.values():
        if isinstance(n, dict):
            touches += len(n.get('touches') or [])
    return len(notes) + touches + len((payload or {}).get('workerLog') or []) \
        + len((payload or {}).get('sentArchive') or [])


def _write_notes(payload):
    """Atomic write of the tracker's localStorage state + one snapshot file per day.

    RICHEST-WINS, not last-write-wins. 127.0.0.1 keeps LAN/internet out, but it does NOT keep out a
    second browser profile, a fresh browser with empty localStorage, or an automated test tab on the
    SAME machine — any of those pushes near-empty state, and plain last-write-wins would let it CLOBBER
    a backup that holds the real call history (demonstrated 2026-08-03: Playwright test tabs overwrote
    it). So: overwrite the primary backup only when the incoming push is at least as rich as what's on
    disk, OR it comes from the same device (a device updating itself — even a legit deletion — wins),
    OR there is no backup yet. A poorer push from a different/blank device is ignored for the primary
    file (its real data is already safe) but still snapshotted for audit. Same guard on the day
    snapshot so a poor push can't clobber a rich same-day snapshot either. Mirrors the opt-out
    ledger's safety-first, never-lose-data posture.
    """
    with _NOTES_LOCK:
        os.makedirs(NOTES_SNAP_DIR, exist_ok=True)
        raw = json.dumps(payload, indent=1, ensure_ascii=False)
        inc_rich = _richness(payload)
        inc_dev = str((payload or {}).get('device') or '')
        # decide whether this push may replace the primary backup
        wins = True
        try:
            if os.path.exists(NOTES_FILE):
                cur = json.load(open(NOTES_FILE, encoding='utf-8'))
                cur_rich = _richness(cur)
                cur_dev = str((cur or {}).get('device') or '')
                same_device = bool(inc_dev) and inc_dev == cur_dev
                # same-device bypass removed 2026-08-10: a stale second tab on the SAME machine
                # was the normal clobber case (the Acosta Appointment wipe on 08-09) — richer
                # state must win regardless of device. Rejected pushes still land in
                # rejected_*.json for audit; a deliberate shrink can add an allow_shrink flag.
                wins = inc_rich >= cur_rich
        except Exception:
            wins = True   # unreadable/corrupt backup — a good push should be allowed to heal it
        if wins:
            tmp = NOTES_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(raw)
            os.replace(tmp, NOTES_FILE)
            snap = os.path.join(NOTES_SNAP_DIR, 'worker_notes_%s.json' % dt.date.today().isoformat())
            tmp2 = snap + '.tmp'
            with open(tmp2, 'w', encoding='utf-8') as f:
                f.write(raw)
            os.replace(tmp2, snap)
        else:
            # keep the richer primary + winning snapshot untouched; record the rejected push for audit
            rej = os.path.join(NOTES_SNAP_DIR, 'rejected_%s.json' % dt.date.today().isoformat())
            try:
                with open(rej, 'w', encoding='utf-8') as f:
                    f.write(raw)
            except Exception:
                pass
    return len(raw)


def _sent_today_count():
    today = dt.date.today().isoformat()
    return sum(1 for e in _load_ledger()
               if e.get('ch') == 'email' and str(e.get('d') or '') == today)


BOUNCE_WINDOW_DAYS = 7
BOUNCE_CEILING = 0.10     # refuse bulk outreach above this trailing hard-bounce rate


def _bounce_health():
    """Trailing hard-bounce rate, computed from the two files this box already owns.

    WHY (2026-08-08): outreach ran at a 28-33% recipient-level bounce rate for SEVEN STRAIGHT DAYS
    -- 478 of 1,712 addresses confirmed dead -- and nothing anywhere stopped it or even said so.
    The daily cap counts MESSAGES, which says nothing about whether they are landing. Providers
    tolerate ~2% and start blocking near 5%; past that Gmail suppresses the account and every lead
    on the board goes dark at once, live conversations included.

    This is deliberately measured on OBSERVED bounces only. It under-reports when bounces.py has
    not run recently, which is the safe direction for a kill switch: it can be too permissive from
    stale data, never too strict from invented data. The error text says so.
    """
    try:
        bounced = {str(k).lower() for k in json.load(open(
            os.path.join(HERE, 'bounced_emails.json'), encoding='utf-8'))}
    except Exception:
        return {'rate': 0.0, 'mailed': 0, 'dead': 0, 'known': 0, 'window': BOUNCE_WINDOW_DAYS}
    cutoff = (dt.date.today() - dt.timedelta(days=BOUNCE_WINDOW_DAYS)).isoformat()
    mailed = dead = 0
    for e in _load_ledger():
        if e.get('ch') != 'email' or not e.get('message_id'):
            continue
        if str(e.get('d') or '') < cutoff:
            continue
        for a in [e.get('to') or ''] + str(e.get('bcc') or '').split(','):
            a = a.strip().lower()
            if not a:
                continue
            mailed += 1
            if a in bounced:
                dead += 1
    return {'rate': (dead / mailed) if mailed else 0.0, 'mailed': mailed, 'dead': dead,
            'known': len(bounced), 'window': BOUNCE_WINDOW_DAYS}


PROVEN_MIN_AGE_DAYS = 2   # a hard bounce DSNs within minutes-to-hours; 48h of silence ≈ delivered
PROBE_DAILY_CAP = 10      # see _probe_quota_left — the free verification trickle


def _probe_quota_left():
    """PROBE LANE (2026-08-11): the only free way to verify the ~850 'unknown' addresses is to
    mail them and watch. Doing that in bulk is what poisoned the account in the first place, so
    this lane is a TRICKLE: at most PROBE_DAILY_CAP never-mailed, not-dead, not-risky recipients
    per day while the breaker is engaged. Worst case (33% of probes dead) adds ~3 bounces a day
    against ~150 clean proven sends — the trailing rate keeps FALLING even while probing. Every
    probe that stays quiet 48h graduates into the proven pool automatically via
    _proven_deliverable(), so the unknown pile self-verifies at ~70 addresses a week for $0."""
    today = dt.date.today().isoformat()
    used = sum(1 for e in _load_ledger()
               if e.get('ch') == 'email' and str(e.get('d') or '') == today
               and e.get('lane') == 'probe')
    return max(0, PROBE_DAILY_CAP - used)


def _proven_deliverable():
    """Addresses with DIRECT evidence of deliverability: mailed >= 48h ago, never observed
    bouncing. This is the positive class the verifier files cannot supply without a paid API
    key (verified_emails.json currently holds ZERO 'ok' rows — only dead/risky/unknown).

    WHY (2026-08-10): the bounce breaker rightly blocks bulk sends while the 7-day rate sits
    near 28%, but it also gags follow-ups to the ~1,200 addresses that ACCEPTED mail during
    those same sends — and re-engaging exactly those addresses is both safe (acceptance is the
    strongest free evidence there is; it beats the domain-share 'risky' heuristic, which is
    about UNOBSERVED addresses) and the fastest way to dilute the trailing rate back under the
    ceiling. Dead stays dead. Never-mailed stays blocked until the rate clears or an API key
    exists — those are the addresses that poisoned the list in the first place."""
    try:
        bad = {str(k).lower() for k in json.load(open(
            os.path.join(HERE, 'bounced_emails.json'), encoding='utf-8'))}
    except Exception:
        bad = set()
    ver = {}
    try:
        ver = json.load(open(os.path.join(HERE, 'verified_emails.json'), encoding='utf-8'))
    except Exception:
        pass
    for a, x in ver.items():
        if (x or {}).get('v') == 'dead':
            bad.add(str(a).lower())
    cutoff = (dt.date.today() - dt.timedelta(days=PROVEN_MIN_AGE_DAYS)).isoformat()
    ok = set()
    for e in _load_ledger():
        if e.get('ch') != 'email' or not e.get('message_id'):
            continue
        if str(e.get('d') or '') > cutoff:      # too recent for silence to mean anything
            continue
        for a in [e.get('to') or ''] + str(e.get('bcc') or '').split(','):
            a = a.strip().lower()
            if a and a not in bad:
                ok.add(a)
    for a, x in ver.items():                     # API-verified 'ok' counts too, once a key exists
        if (x or {}).get('v') == 'ok' and str(a).lower() not in bad:
            ok.add(str(a).lower())
    return ok


def _recipients_today():
    """Gmail meters RECIPIENTS, not messages — and BCC fans one send out to ~2.7 of them.

    At a 150-message cap that is ~400 recipients on an average day, but a day weighted toward
    six-address leads crosses Gmail's 500/day ceiling. Crossing it does not fail politely: the
    server starts rejecting mid-run and can temporarily lock the account, which takes EVERY lead
    on the board dark, not just the ones left in the queue. So the cap is enforced on both axes
    and whichever binds first wins."""
    today = dt.date.today().isoformat()
    n = 0
    for e in _load_ledger():
        if e.get('ch') != 'email' or str(e.get('d') or '') != today:
            continue
        n += 1 + len([a for a in str(e.get('bcc') or '').split(',') if a.strip()])
    return n


def _recently_emailed_to(addr, hours=24):
    """True if this address was on ANY email in the window — as `to` OR inside `bcc`.

    Checking only `to` (the original behaviour) missed every BCC'd address, and the board fans one
    send out to ~2.7 recipients via BCC. So a person who is the `to` of one lead and a BCC of
    another could legitimately be mailed twice in a minute and the dedupe would never see it.
    """
    if not addr:
        return False
    addr = addr.strip().lower()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    for e in _load_ledger():
        if e.get('ch') != 'email':
            continue
        every = [str(e.get('to') or '')] + str(e.get('bcc') or '').split(',')
        if addr not in {a.strip().lower() for a in every if a.strip()}:
            continue
        try:
            ts = dt.datetime.fromisoformat(e.get('ts_utc') or '')
        except Exception:
            continue
        if ts >= cutoff:
            return True
    return False


# ---- ATOMIC RECIPIENT CLAIM -----------------------------------------------------------------
# WHY (2026-08-13): the 24h dedupe was a bare check followed later by an unlocked SMTP send and a
# locked ledger write. Two concurrent /send requests therefore both read "not recently emailed",
# both sent, and only then did either one appear in the ledger — a textbook check-then-act race.
# It fired for real: the morning worker's auto-run was started twice, and 146 sends reached only
# 200 unique recipients because 51 people got the SAME email twice, several inside the same second
# (message ids ...1323213 and ...1049364 for one lead at 12:07:45).
#
# The client now refuses a second auto-run, but the client is not the choke point — a second TAB,
# a stale tab, or a script would still race. This is: reserve every recipient (to + bcc) under one
# lock, and hold the reservation until the send resolves. A racing request sees the reservation and
# gets the same 409 'skip' it would get from the ledger, so the caller's existing skip path handles
# it with no new client code.
_INFLIGHT = set()
_INFLIGHT_LOCK = threading.Lock()


def _norm_addrs(*groups):
    out = []
    for g in groups:
        if not g:
            continue
        for a in (g.split(',') if isinstance(g, str) else g):
            a = (a or '').strip().lower()
            if a:
                out.append(a)
    return out


def _claim_recipients(addrs, hours=24):
    """Reserve every address for this send. Returns (ok, blocking_address, reason)."""
    with _INFLIGHT_LOCK:
        for a in addrs:
            if a in _INFLIGHT:
                return False, a, 'a send to this address is already in flight'
        for a in addrs:
            if _recently_emailed_to(a, hours=hours):
                return False, a, f'was emailed inside the last {hours}h'
        _INFLIGHT.update(addrs)
    return True, '', ''


def _release_recipients(addrs):
    with _INFLIGHT_LOCK:
        _INFLIGHT.difference_update(addrs)


# ---------------------------------------------------------------- SMTP
def _smtp_send(user, pw, from_display, to_addr, subj, body, bcc='', attach=None):
    """bcc carries the owner's OTHER traced addresses.

    Skiptrace returns several addresses per lead and only one is usually live, so reaching all of
    them materially raises the odds the owner ever sees the letter. They must be BCC, never a
    shared To:. Case 2025-007384-CA-01 is owned by DEBORAH C DAVIS SLADE while its six traced
    addresses include shamikamiley@, wanda.smiley@ and talldadman@ — household members, not her.
    A visible To: would publish six strangers' addresses to each other and disclose Deborah's
    foreclosure to five people who are not her.
    smtplib's send_message() delivers to Bcc recipients and does NOT transmit the header.

    attach: list of LOCAL file paths (added 2026-08-06). Case workups — dockets, judgments, deed
    copies — are the one thing this bridge is asked to send that is not a plain-text owner letter,
    and mailing a partner a link to a file on Alejandro's own Desktop is useless to them. Paths are
    resolved and read here; a missing/unreadable file raises so the caller sees a real 502 rather
    than silently mailing a workup with nothing attached."""
    msg = EmailMessage()
    msg['From'] = f'{from_display} <{user}>' if from_display else user
    msg['To'] = to_addr
    if bcc:
        msg['Bcc'] = bcc
    msg['Subject'] = subj
    msg['Message-ID'] = make_msgid(domain=user.split('@', 1)[-1])
    msg['Date'] = formatdate(localtime=True)
    msg.set_content(body)
    for path in (attach or []):
        p = os.path.abspath(os.path.expanduser(str(path)))
        if not os.path.isfile(p):
            raise FileNotFoundError(f'attachment not found: {p}')
        with open(p, 'rb') as f:
            data = f.read()
        ctype, _ = mimetypes.guess_type(p)
        maintype, _, subtype = (ctype or 'application/octet-stream').partition('/')
        msg.add_attachment(data, maintype=maintype, subtype=subtype or 'octet-stream',
                           filename=os.path.basename(p))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)
    return msg['Message-ID']


# ---------------------------------------------------------------- HTTP handler
class Handler(BaseHTTPRequestHandler):
    server_version = 'DealFlowSend/1.0'
    daily_cap = 300          # messages/day (operator-set 2026-08-12, was 150 — the 08-12 run hit it)
    # RECIPIENTS, NOT MESSAGES, ARE THE REAL CEILING. Gmail's free-tier SMTP limit is ~500
    # RECIPIENTS/day, and this board averages 1.92 recipients per message (each send BCCs the
    # owner's alternate addresses). So 300 messages would be ~576 recipients — over the line, and
    # blowing Gmail's limit locks the account the whole business runs on. Keep this at 450: it binds
    # first, at ~234 messages/day, which is still a 56% lift over the old 150 cap. Do NOT raise it
    # toward 500 to chase the message number; cut BCCs instead if more volume is needed.
    recipient_cap = 450      # addresses/day — safety margin under Gmail's ~500

    def log_message(self, fmt, *args):
        """Terse one-line per request in the terminal, no HTTP boilerplate.

        Under pythonw.exe (the silent autostart launcher) sys.stderr is None — an unguarded
        write raised AttributeError INSIDE request handling, killing every response with an
        empty reply. The autostart bridge was dead on arrival because of this line; only a
        console-started `python send_server.py` ever worked.
        """
        try:
            out = sys.stderr or sys.stdout
            if out:
                out.write(f'  {dt.datetime.now().strftime("%H:%M:%S")}  {fmt % args}\n')
        except Exception:
            pass

    def _cors(self):
        """Same-origin doesn't apply from file:// to http://127.0.0.1, so we explicitly allow it.
        The bind address (127.0.0.1) is the actual security boundary; CORS is UX plumbing."""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json(self, status, obj):
        raw = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/marks'):
            return self._handle_marks_get()
        if self.path.startswith('/health'):
            user, pw = _load_credentials()
            _bh = _bounce_health()
            self._json(200, {
                'ok': True,
                'user': user or '(not configured)',
                'has_password': bool(pw),
                'sent_today': _sent_today_count(),
                'cap': self.daily_cap,
                'recipients_today': _recipients_today(),
                'recipient_cap': self.recipient_cap,
                'ready': bool(user and pw),
                # Surfaced so the board can show list health next to the cap. `blocked` is the
                # same verdict /send enforces, so the worker can say WHY before it starts a run
                # instead of discovering it 403 by 403.
                'bounce': _bh, 'bounce_ceiling': BOUNCE_CEILING,
                'bounce_blocked': _bh['rate'] > BOUNCE_CEILING,
                # verified-only mode: while blocked, sends to proven-deliverable addresses
                # (accepted mail >= 48h ago, never bounced) still go through. The worker
                # banner uses this to say "N addresses still sendable" instead of "all stop".
                'proven_pool': len(_proven_deliverable()) if _bh['rate'] > BOUNCE_CEILING else None,
                'probe_quota_left': _probe_quota_left() if _bh['rate'] > BOUNCE_CEILING else None,
            })
        else:
            self._json(404, {'ok': False, 'err': 'unknown path'})

    def _handle_marks_get(self):
        """GET /marks — the board's boot-time replay feed.

        worker_marks.jsonl was a write-only black box: /mark appended, nothing ever read it, so a
        mark that survived a closed worker tab was safe on disk and useless forever. The board now
        fetches this at boot, re-injects each mark through its own workerAct listener (identical
        semantics, zero duplicated logic), then POSTs /marks/consumed with the offset it processed.
        Offset-based so marks appended DURING the replay are never lost or double-consumed."""
        p = os.path.join(HERE, 'worker_marks.jsonl')
        if not os.path.exists(p):
            return self._json(200, {'ok': True, 'marks': [], 'offset': 0})
        try:
            raw = open(p, 'rb').read()
            marks = []
            for line in raw.decode('utf-8', 'replace').splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    marks.append(json.loads(line))
                except Exception:
                    pass                                   # a torn line is audit noise, not a reason to 500
            return self._json(200, {'ok': True, 'marks': marks, 'offset': len(raw)})
        except Exception as e:
            return self._json(500, {'ok': False, 'err': str(e)[:120]})

    def _handle_marks_consumed(self):
        """POST /marks/consumed {offset} — move the replayed prefix to the applied archive."""
        ln = int(self.headers.get('Content-Length') or 0)
        try:
            off = int((json.loads(self.rfile.read(ln).decode('utf-8')) or {}).get('offset') or 0)
        except Exception:
            return self._json(400, {'ok': False, 'err': 'bad json'})
        p = os.path.join(HERE, 'worker_marks.jsonl')
        if off <= 0 or not os.path.exists(p):
            return self._json(200, {'ok': True, 'consumed': 0})
        try:
            # LOCKED: this is a threaded server, and read->truncate->replace with a concurrent
            # /mark append in the gap OBLITERATES that mark — the flagship race is a worker tab's
            # pagehide beacon landing while a freshly reloaded board consumes. A destroyed mark
            # can be a DNC.
            with _NOTES_LOCK:
                raw = open(p, 'rb').read()
                off = min(off, len(raw))
                with open(os.path.join(HERE, 'worker_marks.applied.jsonl'), 'ab') as f:
                    f.write(raw[:off])
                tmp = p + '.tmp'
                with open(tmp, 'wb') as f:
                    f.write(raw[off:])
                os.replace(tmp, p)
            return self._json(200, {'ok': True, 'consumed': off})
        except Exception as e:
            return self._json(500, {'ok': False, 'err': str(e)[:120]})

    def _handle_mark(self):
        """POST /mark — black-box recorder for worker marks that could not reach the board tab.

        The worker buffers and retries the real postMessage path; this file is the crash-proof
        audit copy so a DNC callout can never be silently lost to a closed board tab (an owner
        who said STOP staying dialable is FTSA exposure, not a UI bug)."""
        ln = int(self.headers.get('Content-Length') or 0)
        if ln <= 0 or ln > 200_000:
            return self._json(400, {'ok': False, 'err': 'missing or oversized body'})
        try:
            d = json.loads(self.rfile.read(ln).decode('utf-8'))
        except Exception:
            return self._json(400, {'ok': False, 'err': 'bad json'})
        rec = {'ts_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'c': str(d.get('c') or ''), 'k': str(d.get('k') or ''), 'extra': d.get('extra')}
        try:
            with _NOTES_LOCK:      # serialized against /marks/consumed's read->truncate->replace
                with open(os.path.join(HERE, 'worker_marks.jsonl'), 'a', encoding='utf-8') as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        except Exception as e:
            return self._json(500, {'ok': False, 'err': str(e)[:120]})
        return self._json(200, {'ok': True})

    def _handle_text(self):
        """POST /text — the SMS counterpart of mail_sent.json.

        WHY THIS EXISTS. Email history has been server-side since the 2026-08-08 incident (33 owners
        received 4+ emails because _mailHist read only this browser's localStorage). Text history had
        no such ledger at all, so the 3-touch cadence was correct only WITHIN one browser profile: a
        new profile, a second device, or a cleared cache read every owner as never-texted and
        restarted the ladder at touch 1. This closes that.

        `confirmed` is the exact analogue of mail_sent.json's message_id — ONLY confirmed rows count
        as sends. The worker's `textopen` branch deliberately logs no touch (opening a composer is
        not a delivery), so it posts confirmed:false: the attempt stops being invisible without
        becoming a contact.

        Append is read-modify-write under the shared lock, writing the same LIST shape as
        mail_sent.json so _text_ledger() can mirror _mail_ledger() instead of inventing a parser."""
        ln = int(self.headers.get('Content-Length') or 0)
        if ln <= 0 or ln > 200_000:
            return self._json(400, {'ok': False, 'err': 'missing or oversized body'})
        try:
            d = json.loads(self.rfile.read(ln).decode('utf-8'))
        except Exception:
            return self._json(400, {'ok': False, 'err': 'bad json'})
        case = str(d.get('case') or '').strip()
        if not case:
            return self._json(400, {'ok': False, 'err': 'no case'})
        rec = {'d': dt.date.today().isoformat(),
               'ts_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'ch': 'text', 'case': case,
               'pkey': str(d.get('pkey') or ''),
               'to': re.sub(r'\D', '', str(d.get('to') or '')),
               'owner': str(d.get('owner') or '')[:60],
               'stage': str(d.get('stage') or ''), 'tone': str(d.get('tone') or ''),
               'lang': str(d.get('lang') or ''),
               'body_len': int(d.get('body_len') or 0),
               'confirmed': bool(d.get('confirmed')),
               'device': str(d.get('device') or '')[:40]}
        path = os.path.join(HERE, 'text_sent.json')
        try:
            with _NOTES_LOCK:
                rows = []
                if os.path.exists(path):
                    try:
                        rows = json.load(open(path, encoding='utf-8')) or []
                    except Exception:
                        rows = []          # a corrupt ledger must never block recording a send
                if not isinstance(rows, list):
                    rows = []
                rows.append(rec)
                tmp = path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(rows, f, ensure_ascii=False, indent=1)
                os.replace(tmp, path)      # atomic — a half-written ledger is worse than none
        except Exception as e:
            return self._json(500, {'ok': False, 'err': str(e)[:120]})
        return self._json(200, {'ok': True, 'n': len(rows)})

    def _handle_retrace(self):
        """POST /retrace {c, ph} — the worker's bad-number button parks a case here after a
        number proved wrong/dead. The nightly skiptrace consumes retrace_queue.json and
        re-traces those cases even though they are cached; when a fresh number lands, the
        next build re-queues the lead for a second message."""
        ln = int(self.headers.get('Content-Length') or 0)
        if ln <= 0 or ln > 10_000:
            return self._json(400, {'ok': False, 'err': 'missing or oversized body'})
        try:
            d = json.loads(self.rfile.read(ln).decode('utf-8'))
        except Exception:
            return self._json(400, {'ok': False, 'err': 'bad json'})
        case = str(d.get('c') or '').strip()
        if not case:
            return self._json(400, {'ok': False, 'err': 'no case'})
        qp = os.path.join(HERE, 'retrace_queue.json')
        try:
            q = json.load(open(qp, encoding='utf-8')) if os.path.exists(qp) else []
        except Exception:
            q = []
        if not any(str(x.get('c')) == case for x in q if isinstance(x, dict)):
            q.append({'c': case, 'ph': str(d.get('ph') or ''),
                      'd': dt.date.today().isoformat()})
            tmp = qp + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(q, f, indent=1)
            os.replace(tmp, qp)
        return self._json(200, {'ok': True, 'queued': len(q)})

    def _handle_notes(self):
        """POST /notes — persist the tracker's full localStorage state to disk.

        Body cap is 25 MB: fcSentArchive keeps up to 300 full email bodies and fcLeadNotes grows
        with every touch, so the 200 KB /send cap would reject a healthy payload within weeks.
        The `_dealflow_notes` flag is required so a stray POST can never overwrite the ledger
        with junk.
        """
        length = int(self.headers.get('Content-Length', 0) or 0)
        if length <= 0 or length > 25_000_000:
            return self._json(400, {'ok': False, 'err': 'missing or oversized body'})
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception as e:
            return self._json(400, {'ok': False, 'err': f'bad json: {e}'})
        if not (isinstance(payload, dict) and payload.get('_dealflow_notes')):
            return self._json(400, {'ok': False, 'err': 'not a dealflow notes payload'})
        payload['received_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            size = _write_notes(payload)
        except Exception as e:
            return self._json(500, {'ok': False, 'err': f'write failed: {e}'})
        n_notes = len(payload.get('notes') or {})
        self.log_message('notes push: %d leads, %d log entries, %d KB',
                         n_notes, len(payload.get('workerLog') or []), size // 1024)
        return self._json(200, {'ok': True, 'saved': True, 'notes_count': n_notes, 'bytes': size})

    def do_POST(self):
        if self.path.startswith('/notes'):
            return self._handle_notes()
        if self.path.startswith('/marks/consumed'):   # BEFORE /mark — startswith('/mark') shadows it
            return self._handle_marks_consumed()
        if self.path.startswith('/mark'):
            return self._handle_mark()
        if self.path.startswith('/retrace'):
            return self._handle_retrace()
        if self.path.startswith('/text'):
            return self._handle_text()
        if not self.path.startswith('/send'):
            return self._json(404, {'ok': False, 'err': 'unknown path'})

        # ---- parse body ----
        length = int(self.headers.get('Content-Length', 0) or 0)
        if length <= 0 or length > 200_000:
            return self._json(400, {'ok': False, 'err': 'missing or oversized body'})
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception as e:
            return self._json(400, {'ok': False, 'err': f'bad json: {e}'})

        to = str(payload.get('to') or '').strip().lower()
        # extra traced addresses for the same owner — validated individually so one
        # malformed skiptrace result cannot poison the whole send.
        bcc = ', '.join(a for a in (x.strip().lower() for x in
                        str(payload.get('bcc') or '').split(',')) if _EMAIL_RE.match(a))
        subj = str(payload.get('subj') or '').strip()
        body = str(payload.get('body') or '')
        meta = payload.get('meta') or {}
        # Local file paths to attach (case workups: docket PDFs, judgments, deeds). List of strings.
        attach = payload.get('attach') or []
        if isinstance(attach, str):
            attach = [attach]
        if not isinstance(attach, list) or any(not isinstance(x, str) for x in attach):
            return self._json(400, {'ok': False, 'err': 'attach must be a string or list of file paths'})

        if not _EMAIL_RE.match(to):
            return self._json(400, {'ok': False, 'err': 'invalid to address'})
        if not subj or not body:
            return self._json(400, {'ok': False, 'err': 'subj and body required'})

        # ---- daily cap ----
        rcpt = _recipients_today()
        if rcpt >= self.recipient_cap:
            return self._json(429, {
                'ok': False,
                'err': (f'daily RECIPIENT cap reached ({rcpt}/{self.recipient_cap}) — '
                        f'BCC fan-out counts toward the 500/day Gmail limit'),
                'sent_today': _sent_today_count(), 'cap': self.daily_cap,
                'recipients_today': rcpt, 'recipient_cap': self.recipient_cap})
        n = _sent_today_count()
        if n >= self.daily_cap:
            return self._json(429, {
                'ok': False, 'err': f'daily cap reached ({n}/{self.daily_cap})',
                'sent_today': n, 'cap': self.daily_cap,
            })

        # ---- BOUNCE CIRCUIT BREAKER ----
        # Hard stop on BULK outreach when the trailing bounce rate is unsafe. 1:1 replies to a
        # human who wrote to us are exempt (meta.test) -- gagging a live conversation to protect
        # deliverability would be the wrong trade, and one message cannot move the rate.
        if not meta.get('test'):
            _hb = _bounce_health()
            if _hb['rate'] > BOUNCE_CEILING:
                # PROVEN-DELIVERABLE LANE: while the trailing rate is over the ceiling, a send
                # may still go out if EVERY recipient has direct acceptance evidence (see
                # _proven_deliverable). One unproven recipient blocks the whole send — bcc
                # fan-out must not smuggle unverified addresses through the breaker.
                _rcpts = [a.strip().lower()
                          for a in [to] + str(bcc or '').split(',') if a.strip()]
                _proven = _proven_deliverable()
                _unproven = sorted(a for a in _rcpts if a not in _proven)
                # PROBE LANE: unproven recipients may still go if every one of them is merely
                # UNKNOWN (never observed bouncing, not flagged risky) and today's probe quota
                # has room. Risky/dead never probe — those classes already have evidence.
                if _unproven:
                    _bad = set()
                    try:
                        _bad = {str(k).lower() for k in json.load(open(
                            os.path.join(HERE, 'bounced_emails.json'), encoding='utf-8'))}
                    except Exception:
                        pass
                    _ver = {}
                    try:
                        _ver = json.load(open(os.path.join(HERE, 'verified_emails.json'),
                                              encoding='utf-8'))
                    except Exception:
                        pass
                    def _cls(a):
                        v = (_ver.get(a) or {}).get('v')
                        if a in _bad or v == 'dead':
                            return 'dead'
                        return v or 'unknown'
                    if (all(_cls(a) == 'unknown' for a in _unproven)
                            and len(_unproven) <= _probe_quota_left()):
                        meta['lane'] = 'probe'
                        _unproven = []
                if _unproven:
                    # skip:True is the machine-readable "advance, don't halt" signal — the worker
                    # must treat a held recipient like the 24h-dedupe skip, not like an outage.
                    # An old worker doc ignores it and halts (safe); a new worker against an old
                    # server sees no skip field and also halts (safe).
                    return self._json(403, {
                        'ok': False, 'blocked': 'bounce_rate', 'skip': True,
                        'proven_pool': len(_proven),
                        'unproven': _unproven[:8],
                        'err': (f"BLOCKED: {_hb['dead']} of {_hb['mailed']} addresses mailed in the last "
                                f"{_hb['window']} days are confirmed dead ({_hb['rate']*100:.0f}%). The safe "
                                f"ceiling is {BOUNCE_CEILING*100:.0f}% and providers start blocking near 5%. "
                                f"Verified-only mode is ON: follow-ups to addresses that already accepted "
                                f"mail still send; this one has {len(_unproven)} recipient(s) with no "
                                f"acceptance evidence ({', '.join(_unproven[:3])}...). They unblock when "
                                f"the rate clears or after API verification: "
                                f"python bounces.py && python verify_emails.py && rebuild."),
                        'bounce': _hb, 'sent_today': n, 'cap': self.daily_cap})
                meta['lane'] = 'proven'

        # ---- 24h per-recipient dedupe + in-flight claim (unless test flag set) ----
        # Covers `to` AND every BCC, and reserves them atomically so a concurrent request cannot
        # slip between the check and the ledger write. See _claim_recipients.
        _claimed = []
        if not meta.get('test'):
            _want = _norm_addrs(to, bcc)
            ok_claim, blocker, why = _claim_recipients(_want, hours=24)
            if not ok_claim:
                return self._json(409, {
                    'ok': False, 'err': f'{blocker} {why}',
                    'skip': True,
                })
            _claimed = _want

        # ---- credentials ----
        user, pw = _load_credentials()
        if not (user and pw):
            _release_recipients(_claimed)
            return self._json(500, {'ok': False, 'err': 'no gmail.key credentials'})
        snd = _load_sender()
        from_display = (snd.get('name') or '').strip()

        # ---- send ----
        try:
            mid = _smtp_send(user, pw, from_display, to, subj, body, bcc, attach)
        except smtplib.SMTPAuthenticationError as e:
            _release_recipients(_claimed)
            return self._json(401, {'ok': False, 'err': f'SMTP AUTH failed: {e}'})
        except Exception as e:
            # NOTE: the claim is deliberately NOT released on a generic send failure — the SMTP
            # exception can arrive after Gmail already accepted the message, and re-sending on a
            # maybe-delivered address is exactly the duplicate this guard exists to prevent. The
            # 24h ledger entry written just below keeps it blocked; a real retry is tomorrow.
            _append_ledger({
                'd': dt.date.today().isoformat(),
                'ts_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'ch': 'email', 'from': user, 'to': to,
                'owner': meta.get('owner') or '',
                'case': meta.get('c') or '',
                'error': str(e)[:200],
                'test_mode': bool(meta.get('test')),
            })
            return self._json(502, {'ok': False, 'err': f'send failed: {e}'})

        # The email is ALREADY SENT at this point (mid came back from a real SMTP transaction).
        # Everything below is bookkeeping. If it throws — even after _append_ledger's own retries
        # — that must never surface to the client as a send failure: the worker's catch() branch
        # treats a broken /send response as "the bridge is down" and (correctly, for an ACTUAL
        # outage) pauses the whole auto-run. A logging hiccup is not an outage, and pausing a
        # healthy unattended run on one is a worse bug than the log gap it would be protecting
        # against. Caught live 2026-08-05: exactly this ordering turned one transient ledger-write
        # error into "the morning worker looks like it's failing" — the send had gone through fine.
        ledger_err = ''
        try:
            _append_ledger({
                'd': dt.date.today().isoformat(),
                'ts_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'ch': 'email',
                # bcc is recorded because _recipients_today() meters Gmail's real limit off it.
                # Without it every multi-address send would count as one recipient and the ceiling
                # would never bind — the exact failure the ceiling exists to prevent.
                'from': user, 'to': to, 'bcc': bcc,
                'owner': meta.get('owner') or '',
                'case': meta.get('c') or '',
                'addr': meta.get('addr') or '',
                'lang': meta.get('lang') or 'en',
                'portfolio': meta.get('portfolio') or [],
                'subj': subj, 'body_len': len(body),
                'message_id': mid,
                'test_mode': bool(meta.get('test')),
                # 'proven' = passed the breaker on acceptance evidence; 'probe' = one of
                # today's quota of unknown-address verification sends. Absent on normal sends.
                'lane': meta.get('lane') or '',
            })
        except Exception as e:
            # Loud on the server side (operator can grep the log) — this is the one real gap the
            # skipped write leaves: this send won't count toward today's cap and its 24h dedupe
            # record is missing, so the same lead COULD be re-queued and re-emailed by a later run
            # before the day rolls over. Worth a manual mail_sent.json check if this ever fires
            # more than once in a row (would mean the retries above are landing on a persistent
            # lock, not a transient one).
            ledger_err = str(e)[:200]
            print(f'WARNING: {to} was emailed successfully (mid={mid}) but the ledger write '
                  f'failed — this send is UNRECORDED: {ledger_err}', file=sys.stderr)

        resp = {'ok': True, 'message_id': mid, 'sent_today': _sent_today_count(), 'cap': self.daily_cap}
        if ledger_err:
            resp['ledger_warn'] = ledger_err
        return self._json(200, resp)


# ---------------------------------------------------------------- main
def _already_running(host, port, timeout=2.5):
    """True when a HEALTHY DealFlow bridge already owns this port.

    Windows lets a SECOND process bind a port that is already in use when SO_REUSEADDR is set —
    and http.server sets it via allow_reuse_address. Verified 2026-08-07: two sockets bound
    127.0.0.1:9977 simultaneously with no error. Two live bridges would each keep their own view of
    mail_sent.json's daily count, so the 150/day cap would be enforced twice at half strength and a
    lead could be mailed by both.

    Autostart now fires from TWO places (the Startup folder at logon, and a scheduled task at logon
    plus again before the 8am worker), so a duplicate launch is the EXPECTED case, not an edge one.
    Probe /health and step aside instead of racing for the socket."""
    try:
        with urllib.request.urlopen(f'http://{host}:{port}/health', timeout=timeout) as r:
            return bool(json.loads(r.read().decode()).get('ok'))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--host', default='127.0.0.1',
                    help='bind address (default 127.0.0.1 — DO NOT change unless you know why)')
    ap.add_argument('--port', type=int, default=8823)
    # NOTE: this default SILENTLY OVERRODE Handler.daily_cap — raising the class attribute alone
    # did nothing because main() always reassigns from args. Keep the two in sync.
    ap.add_argument('--limit', type=int, default=Handler.daily_cap,
                    help=f'daily MESSAGE cap (default {Handler.daily_cap}). A separate '
                         f'{Handler.recipient_cap}-RECIPIENT ceiling also applies, because BCC '
                         f'fan-out is what Gmail actually meters.')
    args = ap.parse_args()

    Handler.daily_cap = args.limit

    # Idempotent start — see _already_running(). Exit 0, not an error: a duplicate launch means the
    # bridge is up, which is the desired end state.
    if _already_running(args.host, args.port):
        print(f'  a healthy bridge already owns {args.host}:{args.port} — leaving it alone.')
        return

    user, pw = _load_credentials()
    print()
    print('=' * 62)
    print(f'  DealFlow send bridge — http://{args.host}:{args.port}')
    print('=' * 62)
    print(f'  account:      {user or "(gmail.key missing)"}')
    print(f'  credential:   {"OK" if pw else "MISSING"}')
    print(f'  sent today:   {_sent_today_count()}')
    print(f'  daily cap:    {args.limit}')
    print(f'  ledger:       {SENT_LEDGER}')
    print()
    print('  health:  curl http://127.0.0.1:{}/health'.format(args.port))
    print('  stop:    Ctrl+C')
    print()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nstopping...')
        srv.shutdown()


if __name__ == '__main__':
    main()
