#!/usr/bin/env python3
"""call_coach_server.py -- local-only server for the BSG live call coach.

Serves call_coach_live.html, looks up leads from leads_final.json, and writes
every call session (audio + transcript + lead card) to
call_sessions/<stamp>_<case>/.

Binds 127.0.0.1 only. No auth, no outbound calls, no third-party deps.
Python 3.9+. Run:  python call_coach_server.py  (then open the printed URL)

Why a server at all: Chrome refuses mic access on file:// origins. Serving the
page from http://127.0.0.1 is a secure context, so getUserMedia works, and the
same origin can accept the audio POST. One process, two jobs.
"""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(ROOT, "leads_final.json")
SESSIONS = os.path.join(ROOT, "call_sessions")
PAGE = os.path.join(ROOT, "call_coach_live.html")
PORT = int(os.environ.get("COACH_PORT", "8777"))
HOST = "127.0.0.1"

_lock = threading.Lock()
_leads = None
_leads_mtime = 0.0


# --------------------------------------------------------------------- leads
def load_leads():
    """Cached read of leads_final.json, reloaded when the file changes."""
    global _leads, _leads_mtime
    try:
        m = os.path.getmtime(LEADS)
    except OSError:
        return []
    with _lock:
        if _leads is not None and m == _leads_mtime:
            return _leads
        try:
            with open(LEADS, "r", encoding="utf-8") as f:
                _leads = json.load(f)
        except (OSError, ValueError) as e:
            sys.stderr.write("[coach] leads read failed: %s\n" % e)
            _leads = _leads or []
        _leads_mtime = m
        return _leads


def _norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).strip()


def _days_to(auction):
    """Days from today to the auction date, or None if unparseable."""
    if not auction:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            d = datetime.strptime(str(auction).strip()[:10], fmt).date()
            return (d - datetime.now().date()).days
        except ValueError:
            continue
    return None


def lead_card(r):
    """The fields the coach actually shows on a call."""
    return {
        "case": r.get("Case #"),
        "address": r.get("Address"),
        "owners": r.get("owners"),
        "auction": r.get("AuctionDate"),
        "days": _days_to(r.get("AuctionDate")),
        "judgment": r.get("Final Judgment Amount"),
        "opening_bid": r.get("opening_bid"),
        "plaintiff": r.get("plaintiff"),
        "equity": r.get("equity"),
        "tier": r.get("tier"),
        "score": r.get("score"),
        "homestead": r.get("homestead"),
    }


def search_leads(q, limit=10):
    """All-token substring match across address / owner / case number."""
    nq = _norm(q)
    if not nq:
        return []
    toks = [t for t in nq.split() if len(t) > 1]
    if not toks:
        return []
    hits = []
    for r in load_leads():
        hay = _norm(" ".join(str(r.get(k) or "") for k in (
            "Address", "owners", "Case #", "people_name", "mailing_address")))
        if all(t in hay for t in toks):
            hits.append(r)
    return [lead_card(r) for r in hits[:limit]]


def recent_leads(limit=25):
    """Soonest auctions first -- the calls you actually make today."""
    rows = [r for r in load_leads() if _days_to(r.get("AuctionDate")) is not None]
    rows.sort(key=lambda r: _days_to(r.get("AuctionDate")))
    return [lead_card(r) for r in rows[:limit]]


# ------------------------------------------------------------------ sessions
def _slug(s, n=40):
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(s or "")).strip("-")
    return s[:n] or "unfiled"


def new_session(meta):
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    sid = stamp + "_" + _slug(meta.get("case") or meta.get("address") or "call")
    d = os.path.join(SESSIONS, sid)
    os.makedirs(d, exist_ok=True)
    meta = dict(meta)
    meta["session_id"] = sid
    meta["started_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta["audio_bytes"] = 0
    _write_json(os.path.join(d, "meta.json"), meta)
    return sid


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _sdir(sid):
    """Session dir, refusing any sid that could escape SESSIONS."""
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        return None
    d = os.path.join(SESSIONS, sid)
    return d if os.path.isdir(d) else None


def append_audio(sid, blob):
    d = _sdir(sid)
    if not d:
        return None
    path = os.path.join(d, "audio.webm")
    with open(path, "ab") as f:
        f.write(blob)
    n = os.path.getsize(path)
    mp = os.path.join(d, "meta.json")
    try:
        with open(mp, "r", encoding="utf-8") as f:
            m = json.load(f)
        m["audio_bytes"] = n
        _write_json(mp, m)
    except (OSError, ValueError):
        pass
    return n


def save_transcript(sid, payload):
    d = _sdir(sid)
    if not d:
        return None
    lines = payload.get("lines") or []
    _write_json(os.path.join(d, "transcript.json"), {
        "session_id": sid,
        "saved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lines": lines,
    })
    with open(os.path.join(d, "transcript.txt"), "w", encoding="utf-8") as f:
        for ln in lines:
            f.write("[%s] %s\n" % (ln.get("t", ""), ln.get("text", "")))
    return len(lines)


def end_session(sid, payload):
    d = _sdir(sid)
    if not d:
        return None
    mp = os.path.join(d, "meta.json")
    try:
        with open(mp, "r", encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        m = {"session_id": sid}
    m["ended_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    m["duration_s"] = payload.get("duration_s")
    m["stages_visited"] = payload.get("stages_visited")
    m["objections_fired"] = payload.get("objections_fired")
    m["redflags_fired"] = payload.get("redflags_fired")
    m["consent_ack"] = payload.get("consent_ack")
    _write_json(mp, m)
    return m


# ------------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    server_version = "CallCoach/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[coach] %s\n" % (fmt % args))

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        return self.rfile.read(n) if n > 0 else b""

    def _json_body(self):
        raw = self._body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self):
        self._send(204, b"")

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        qs = parse_qs(u.query)

        if p in ("/", "/index.html"):
            try:
                with open(PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                return self._send(500, {"error": "call_coach_live.html not found next to this script"})

        if p == "/api/health":
            return self._send(200, {
                "ok": True,
                "leads": len(load_leads()),
                "sessions_dir": SESSIONS,
                "port": PORT,
            })

        if p == "/api/lead":
            q = (qs.get("q") or [""])[0]
            return self._send(200, {"matches": search_leads(q)})

        if p == "/api/leads/recent":
            return self._send(200, {"leads": recent_leads()})

        if p == "/api/sessions":
            try:
                names = sorted(os.listdir(SESSIONS), reverse=True)[:50]
            except OSError:
                names = []
            return self._send(200, {"sessions": names})

        return self._send(404, {"error": "not found", "path": p})

    # --------------------------------------------------------------- POST
    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        parts = [unquote(x) for x in p.strip("/").split("/")]

        # /api/session/start
        if p == "/api/session/start":
            meta = self._json_body()
            sid = new_session(meta)
            return self._send(200, {"session_id": sid, "dir": os.path.join(SESSIONS, sid)})

        # /api/session/<sid>/<verb>
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "session":
            sid, verb = parts[2], parts[3]
            if verb == "audio":
                n = append_audio(sid, self._body())
                if n is None:
                    return self._send(404, {"error": "unknown session"})
                return self._send(200, {"ok": True, "bytes": n})
            if verb == "transcript":
                n = save_transcript(sid, self._json_body())
                if n is None:
                    return self._send(404, {"error": "unknown session"})
                return self._send(200, {"ok": True, "lines": n})
            if verb == "end":
                m = end_session(sid, self._json_body())
                if m is None:
                    return self._send(404, {"error": "unknown session"})
                return self._send(200, {"ok": True, "meta": m})

        return self._send(404, {"error": "not found", "path": p})


def main():
    os.makedirs(SESSIONS, exist_ok=True)
    n = len(load_leads())
    url = "http://%s:%d/" % (HOST, PORT)
    print("=" * 62)
    print(" BSG Call Coach")
    print("=" * 62)
    print(" leads loaded : %d" % n)
    print(" sessions     : %s" % SESSIONS)
    print(" open         : %s" % url)
    print(" stop         : Ctrl+C")
    print("=" * 62)
    if n == 0:
        print(" WARNING: leads_final.json missing or empty -- lead lookup is off.")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[coach] stopped")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()