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


def user_var(name):
    """A key from the process environment, else the Windows user variable of that name, else ''.
    Read on each request, so a key saved in Windows after this server started is still found (the
    user variable lives in the registry; a running process never sees it change)."""
    k = os.environ.get(name) or ""
    if not k and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
                k = winreg.QueryValueEx(h, name)[0] or ""
        except OSError:
            k = ""
    return str(k).strip()


def deepgram_key():
    """The Deepgram key from the DEEPGRAM_API_KEY variable, or ''. It goes only to the coach page
    over loopback (/api/dgkey). Never logged, never written to disk, never in /api/health."""
    return user_var("DEEPGRAM_API_KEY")


# ---------------------------------------------------------------- AI next line
# The cards match set phrases, so a homeowner who words it differently gets silence (test call
# 11-16 10:08: a minute of "we've got it figured out / we talked to the bank" and no card). This
# asks a language model for the one next line, from the transcript alone. The key is the
# VENICE_API_KEY variable and it never leaves this process: the page posts the transcript here
# and gets a sentence back. Only the spoken words go out; no case number, address or owner name
# from the lead file is ever put in the prompt.
AI_URL = os.environ.get("COACH_AI_URL", "https://api.venice.ai/api/v1/chat/completions")
AI_MODEL = os.environ.get("COACH_AI_MODEL", "deepseek-v4-flash")
AI_SYSTEM = (
    "You coach a caller live, during a phone call with a homeowner who has a foreclosure case filed against "
    "their property. The caller is Alejandro with Biscayne Solutions Group. He is not the lender, he is not "
    "buying the house, and there is no fee. The only thing he wants from this call is a free five-minute call "
    "between the homeowner and a senior advisor who lays out every option the owner has.\n"
    "You get the live transcript. Labels are CALLER, THEM or ?. They come from one microphone and are often "
    "wrong or missing, so work out who said each line from what it says.\n"
    "Write the ONE next thing the caller should say out loud, answering the last thing the homeowner said.\n"
    "Rules:\n"
    "- One or two short spoken sentences, plain words, the way a person talks on the phone.\n"
    "- First reflect what they just said in their own words, then end on ONE question.\n"
    "- The question is open and about their situation, or it assumes the next step. Never a yes/no question "
    "that hands them an exit, such as 'would you be open to' or 'do you have a minute'.\n"
    "- No digits, no dollar amounts, no dates, no promises of any outcome, no legal advice, nothing about "
    "what the bank or the court will do.\n"
    "- If they ask to stop being called, the line confirms they are off the list and says goodbye.\n"
    "- If the last line of the transcript is the caller speaking, reply with the single word SKIP.\n"
    "Reply with the line only. No quotes, no label, no explanation.")
_AI_BAD = re.compile(r"[\d$]")


def venice_key():
    return user_var("VENICE_API_KEY")


def ai_line(lines, lang=""):
    """The next line for the caller, or '' (no key, nothing to answer, a slow or failed call, or a
    reply that breaks the no-numbers rule). Never raises: a dead model must not touch the call."""
    key = venice_key()
    if not key:
        return ""
    rows = []
    for l in (lines or [])[-16:]:
        t = str((l or {}).get("text") or "").strip()
        if not t:
            continue
        spk = str((l or {}).get("spk") or "")
        rows.append(("CALLER" if spk.startswith("you") else "THEM" if spk.startswith("them") else "?") + ": " + t[:400])
    if not rows:
        return ""
    system = AI_SYSTEM + ("\nReply in Spanish." if str(lang).lower().startswith("es") else "")
    body = {"model": AI_MODEL, "max_tokens": 200, "temperature": 0.4, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(rows)}],
            "venice_parameters": {"include_venice_system_prompt": False, "disable_thinking": True,
                                  "strip_thinking_response": True}}
    try:
        import urllib.request
        req = urllib.request.Request(AI_URL, data=json.dumps(body).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as r:
            j = json.loads(r.read().decode("utf-8"))
        out = str(j["choices"][0]["message"].get("content") or "")
    except Exception:
        return ""
    out = " ".join(out.split()).strip().strip('"\u201c\u201d').strip()
    if not out or out.upper().rstrip(".") == "SKIP" or len(out) > 320 or _AI_BAD.search(out):
        return ""
    return out


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
        # What the coach may say about money. eqstate is stamped by equity_state.apply();
        # a lead without it reads as NOT verified on the page, and no number is offered.
        "eqstate": r.get("eqstate"),
        "judgment_unknown": bool(r.get("judgment_unknown")),
        "sale_bk_active": bool(r.get("sale_bk_active")),
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
    # one row per coach prompt: which intent, partial or final words, ms held, ms the words ran behind
    m["coach_fires"] = payload.get("coach_fires")
    m["consent_ack"] = payload.get("consent_ack")
    m["outcome"] = payload.get("outcome")
    _write_json(mp, m)
    # One append-only line per call so outcomes are greppable without opening each session
    # folder. Lives INSIDE call_sessions/ so the same .gitignore line covers it.
    try:
        with open(os.path.join(SESSIONS, "outcomes.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "sid": sid, "case": m.get("case"), "outcome": m.get("outcome"),
                "duration_s": m.get("duration_s"), "ended_utc": m.get("ended_utc"),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return m


# ------------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    server_version = "CallCoach/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[coach] %s\n" % (fmt % args))

    def _send(self, code, body, ctype="application/json", private=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not private:      # private = no CORS grant, so another site's script cannot read the reply
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
                "deepgram": bool(deepgram_key()),     # whether a key is set. Never the key.
                "ai": bool(venice_key()),
            })

        if p == "/api/dgkey":
            # The coach page's own request only: our Host (no DNS rebinding), our Origin when one
            # is sent, same-origin per the browser, and no CORS grant on the reply.
            own = ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)
            origin, site = self.headers.get("Origin"), self.headers.get("Sec-Fetch-Site")
            if (self.headers.get("Host") not in own or (origin and origin not in ["http://" + o for o in own])
                    or (site and site != "same-origin")):
                return self._send(403, {"error": "same-origin only"}, private=True)
            return self._send(200, {"key": deepgram_key()}, private=True)

        if p == "/api/lead":
            q = (qs.get("q") or [""])[0]
            return self._send(200, {"matches": search_leads(q)})

        if p == "/api/leads/recent":
            return self._send(200, {"leads": recent_leads()})

        if p == "/api/objections":
            # The 14 approved objection cards -- the SAME call_objections.json Call Mode
            # ships, so Jesse/Alejandro copy edits flow here without touching the page.
            try:
                with open(os.path.join(ROOT, "call_objections.json"), "r", encoding="utf-8") as f:
                    return self._send(200, {"cards": json.load(f)})
            except (OSError, ValueError):
                return self._send(200, {"cards": []})

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

        if p == "/api/suggest":
            # The coach page's own request only (same rule as /api/dgkey): this spends the key.
            own = ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)
            origin, site = self.headers.get("Origin"), self.headers.get("Sec-Fetch-Site")
            if (self.headers.get("Host") not in own or (origin and origin not in ["http://" + o for o in own])
                    or (site and site != "same-origin")):
                return self._send(403, {"error": "same-origin only"}, private=True)
            b = self._json_body()
            t0 = time.time()
            line = ai_line(b.get("lines"), b.get("lang") or "")
            return self._send(200, {"line": line, "ms": int((time.time() - t0) * 1000)}, private=True)

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