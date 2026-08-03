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
import os
import re
import smtplib
import ssl
import sys
import threading
import time
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
    """Atomic write. Lock protects against two concurrent /send requests corrupting the file."""
    with _LEDGER_LOCK:
        log = _load_ledger()
        log.append(entry)
        tmp = SENT_LEDGER + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SENT_LEDGER)


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
                wins = same_device or inc_rich >= cur_rich
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


def _recently_emailed_to(addr, hours=24):
    if not addr:
        return False
    addr = addr.strip().lower()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    for e in _load_ledger():
        if e.get('ch') != 'email' or str(e.get('to') or '').lower().strip() != addr:
            continue
        try:
            ts = dt.datetime.fromisoformat(e.get('ts_utc') or '')
        except Exception:
            continue
        if ts >= cutoff:
            return True
    return False


# ---------------------------------------------------------------- SMTP
def _smtp_send(user, pw, from_display, to_addr, subj, body):
    msg = EmailMessage()
    msg['From'] = f'{from_display} <{user}>' if from_display else user
    msg['To'] = to_addr
    msg['Subject'] = subj
    msg['Message-ID'] = make_msgid(domain=user.split('@', 1)[-1])
    msg['Date'] = formatdate(localtime=True)
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx, timeout=30) as s:
        s.login(user, pw)
        s.send_message(msg)
    return msg['Message-ID']


# ---------------------------------------------------------------- HTTP handler
class Handler(BaseHTTPRequestHandler):
    server_version = 'DealFlowSend/1.0'
    daily_cap = 50

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
        if self.path.startswith('/health'):
            user, pw = _load_credentials()
            self._json(200, {
                'ok': True,
                'user': user or '(not configured)',
                'has_password': bool(pw),
                'sent_today': _sent_today_count(),
                'cap': self.daily_cap,
                'ready': bool(user and pw),
            })
        else:
            self._json(404, {'ok': False, 'err': 'unknown path'})

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
        subj = str(payload.get('subj') or '').strip()
        body = str(payload.get('body') or '')
        meta = payload.get('meta') or {}

        if not _EMAIL_RE.match(to):
            return self._json(400, {'ok': False, 'err': 'invalid to address'})
        if not subj or not body:
            return self._json(400, {'ok': False, 'err': 'subj and body required'})

        # ---- daily cap ----
        n = _sent_today_count()
        if n >= self.daily_cap:
            return self._json(429, {
                'ok': False, 'err': f'daily cap reached ({n}/{self.daily_cap})',
                'sent_today': n, 'cap': self.daily_cap,
            })

        # ---- 24h per-recipient dedupe (unless test flag set) ----
        if not meta.get('test') and _recently_emailed_to(to, hours=24):
            return self._json(409, {
                'ok': False, 'err': f'{to} was emailed inside the last 24h',
                'skip': True,
            })

        # ---- credentials ----
        user, pw = _load_credentials()
        if not (user and pw):
            return self._json(500, {'ok': False, 'err': 'no gmail.key credentials'})
        snd = _load_sender()
        from_display = (snd.get('name') or '').strip()

        # ---- send ----
        try:
            mid = _smtp_send(user, pw, from_display, to, subj, body)
        except smtplib.SMTPAuthenticationError as e:
            return self._json(401, {'ok': False, 'err': f'SMTP AUTH failed: {e}'})
        except Exception as e:
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

        _append_ledger({
            'd': dt.date.today().isoformat(),
            'ts_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'ch': 'email',
            'from': user, 'to': to,
            'owner': meta.get('owner') or '',
            'case': meta.get('c') or '',
            'addr': meta.get('addr') or '',
            'lang': meta.get('lang') or 'en',
            'portfolio': meta.get('portfolio') or [],
            'subj': subj, 'body_len': len(body),
            'message_id': mid,
            'test_mode': bool(meta.get('test')),
        })

        return self._json(200, {
            'ok': True, 'message_id': mid,
            'sent_today': _sent_today_count(),
            'cap': self.daily_cap,
        })


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--host', default='127.0.0.1',
                    help='bind address (default 127.0.0.1 — DO NOT change unless you know why)')
    ap.add_argument('--port', type=int, default=8823)
    ap.add_argument('--limit', type=int, default=50,
                    help='daily send cap (default 50; Gmail cold-mail practical ceiling)')
    args = ap.parse_args()

    Handler.daily_cap = args.limit

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
