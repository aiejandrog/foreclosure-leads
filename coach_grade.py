#!/usr/bin/env python3
"""coach_grade.py -- replay grader for the live Call Coach's AI lines.

How grounded are the lines the coach puts on screen? A stronger judge model, reached through the same
Venice API and key the coach uses (Windows user variable VENICE_API_KEY), reads each line with the
playbook and the moment it was said in, and scores it pass/fail on five things plus an overall 0-10:

  grounded   every claim is backed by the playbook or the lead facts; no invented fact or promise
  never_say  no breach of the playbook's "Never say" list
  speaker    it answers what the HOMEOWNER said, not the caller
  on_step    it moves toward booking the senior-advisor call
  sayable    short and natural to say out loud

Two modes:
  --historical  grade every AI line that was actually shown on a saved call
  --replay      rebuild each fire's moment (the transcript lines up to it, the lines already given, the
                lead's lane, sale timing and equity flag), ask the CURRENT call_coach_server.ai_line() for
                a fresh line, and grade that. It imports the server, so when the coach code or the
                playbook improves, this score moves.

DATA. The saved calls (call_sessions/*/meta.json + transcript.json) are private homeowner data and are
gitignored; they are read in place and never copied into the repo. Per-line results and summary.json go
ONLY to DEALFLOW_DIR/coach-grades/<stamp>/ (outside the repo); the console shows counts, never words.

COST. The token estimate prints before anything is sent (--dry-run stops there). Judge calls and replay
calls are hard-capped (MAX_JUDGE, MAX_REPLAY); --limit picks a smaller seeded sample.

  python coach_grade.py --historical --sessions <dir>
  python coach_grade.py --replay --limit 100 --sessions <dir>
  COACH_JUDGE_MODEL / --judge-model picks the judge (list: GET https://api.venice.ai/api/v1/models).
"""
import argparse
import json
import os
import random
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import call_coach_server as S

try:
    from paths import DEALFLOW_DIR
except Exception:  # pragma: no cover - paths.py is in this repo; this is only a fallback
    DEALFLOW_DIR = os.environ.get("DEALFLOW_DIR") or os.path.join(os.path.expanduser("~"), "DEALFLOW")

REPO = os.path.dirname(os.path.abspath(__file__))
MODELS_URL = "https://api.venice.ai/api/v1/models?type=text"
# Opus 5.5: the strongest Claude Venice lists below the Fable tier (which costs twice as much), and a
# grade above the coach's own Sonnet 5.5 / DeepSeek flash. Override with COACH_JUDGE_MODEL.
JUDGE_MODEL = os.environ.get("COACH_JUDGE_MODEL", "claude-opus-5-5")
MAX_JUDGE = 400          # judge HTTP requests per run, retries included
MAX_REPLAY = 400         # ai_line() calls per run (each may make its own re-asks inside the coach)
JUDGE_TIMEOUT = 90.0
CRITERIA = ("grounded", "never_say", "speaker", "on_step", "sayable")
FAILURE_CLASSES = (
    "invented_fact",        # a fact about the case, the law, the company or the advisor that nothing supports
    "invented_promise",     # a promised outcome, date or result ("we stop the sale", "you'll keep the house")
    "money_or_figure",      # an amount, payoff, rate, fee or equity claim about this homeowner
    "equity_unverified",    # says or implies they have equity when the lead's equity is not verified
    "pay_disclosure",       # how the company gets paid / referral / commission
    "identity_claim",       # bank, court, lender, "my supervisor", "our attorneys", thirty years as ours
    "legal_advice",         # legal advice or a verdict on their attorney's case
    "shutdown_question",    # yes/no, "are you interested in selling", lowest price, either/or exit
    "wrong_lane",           # "foreclosure"/"the bank" on a tax sale, a sale date on a fresh filing, ...
    "answered_caller",      # responds to the caller's words (or its own last line) instead of the homeowner's
    "misread_homeowner",    # responds to the homeowner but gets what they said wrong
    "ignored_question",     # the homeowner asked something and the line does not answer it
    "no_progress",          # does not move toward the booking; circles, stalls
    "premature_close",      # jumps to the booking ask while a real question/objection is unanswered
    "repeat",               # repeats a line already given on this call
    "too_long",             # too many words to say in one breath
    "unnatural",            # stiff, written-sounding, jargon, two questions, awkward to say
    "weak_or_pushy",        # expects a no / begs, or argues and pressures
    "other",
)
_FC = set(FAILURE_CLASSES)

JUDGE_SYSTEM = """You grade one line written by a live sales-call coach. The coach listens to a phone call and puts ONE \
line on the caller's screen for him to say next. The caller is Alejandro with Biscayne Solutions Group; the other person \
is a homeowner with a foreclosure (or tax-sale) case. He is not the lender, not buying the house, no fee. The ONE goal of \
the call is to book a free five-minute call between the homeowner and the company's senior advisor at a set time.

You get: the lead facts the coach was allowed to use, the lines the coach already gave on this call, the transcript up to \
the moment (labels CALLER / THEM / ? come from one microphone and are often wrong or missing: work out who said what from \
the content; a line that matches one the coach already gave is the caller reading it out), and the coach's output \
(HEARD = what it thinks the homeowner said, TYPE, STEP, SAY = the line). Grade the SAY line.

Score each criterion pass (true) or fail (false). Be strict; when in real doubt, fail.
- grounded: every factual claim and every promise in SAY is supported by the PLAYBOOK below or by the lead facts given. \
Anything about the law, the case, the company, the advisor, timelines or outcomes that neither supports is a fail. \
Ordinary conversational moves (empathy, a question, asking for a time) need no support. Saying or implying they have \
equity when the lead facts say equity is NOT verified is a fail.
- never_say: SAY breaks nothing on the playbook's "Never say" list (and nothing in the lane rules in the lead facts, \
e.g. "the bank" on a tax sale, a sale date on a filing with no sale).
- speaker: SAY responds to the homeowner's most recent meaningful words, not to the caller's words and not to the coach's \
own last line read aloud. If the homeowner has not spoken yet (the opener), pass when SAY is a proper next caller line.
- on_step: SAY moves the call at least one step toward booking the advisor call (answer and then advance, or ask for \
the time), and does not skip past an unanswered real question or objection.
- sayable: short enough to say in one breath or two (roughly under forty-five words), one point and at most one \
question, natural spoken English (or Spanish), nothing that reads like writing.

score: overall quality of SAY as the next thing to say, an integer 0-10 (10 = exactly what a top closer would say here).
failure_classes: for every failed criterion, one or more labels from this fixed list (no others): """ + ", ".join(FAILURE_CLASSES) + """. \
Empty list when everything passes.

Reply with ONE JSON object and nothing else, no code fence:
{"grounded": {"pass": true, "why": "..."}, "never_say": {"pass": true, "why": "..."}, "speaker": {"pass": true, "why": "..."}, \
"on_step": {"pass": true, "why": "..."}, "sayable": {"pass": true, "why": "..."}, "score": 7, "failure_classes": []}
Each "why" is one short sentence.

PLAYBOOK (the caller's own sales system: the facts, the moves, and the Never say list):
"""


# ------------------------------------------------------------------ small pieces
class Cap:
    """A thread-safe hard limit: take() is True at most n times."""

    def __init__(self, n):
        self.n, self.used, self._lock = int(n), 0, threading.Lock()

    def take(self):
        with self._lock:
            if self.used >= self.n:
                return False
            self.used += 1
            return True


class Tally:
    def __init__(self):
        self._lock = threading.Lock()
        self.c = Counter()

    def add(self, **kw):
        with self._lock:
            self.c.update({k: v for k, v in kw.items() if v})


def _secs(t):
    """'mm:ss' or 'h:mm:ss' -> seconds, or None."""
    try:
        parts = [int(x) for x in str(t).strip().split(":")]
    except (TypeError, ValueError):
        return None
    if not parts or len(parts) > 3:
        return None
    s = 0
    for p in parts:
        s = s * 60 + p
    return s


def _date(s):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(str(s).strip()[:10], fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def judge_key():
    return S.venice_key()


# ------------------------------------------------------------------ the saved calls
def load_cards():
    """case -> lead card, from the same lead files the coach opens. {} when they are not there."""
    try:
        return {S._norm(c.get("case")): c for _, c in S.all_cards() if c.get("case")}
    except Exception:
        return {}


def lead_facts(case, auction, started, cards):
    """lane, days to the sale (as of the call), and the equity flag the page would have sent."""
    card = cards.get(S._norm(case)) if case else None
    a, d0 = _date(auction or (card or {}).get("auction")), _date(started)
    days = (a - d0).days if (a and d0) else None
    lane = (card or {}).get("lane") or ("auction" if days is not None else "")
    eq = ""
    if card and card.get("eqstate") in ("clear", "priced"):
        try:
            eq = "verified" if float(card.get("equity")) > 0 else ""
        except (TypeError, ValueError):
            eq = ""
    return {"lane": lane, "days": days, "eq": eq}


def load_fires(sessions_dir, cards=None):
    """Every shown AI line on every saved call that has a transcript, with its rebuilt moment."""
    cards = cards if cards is not None else {}
    out = []
    for name in sorted(os.listdir(sessions_dir)):
        d = os.path.join(sessions_dir, name)
        mp, tp = os.path.join(d, "meta.json"), os.path.join(d, "transcript.json")
        if not (os.path.isfile(mp) and os.path.isfile(tp)):
            continue
        try:
            with open(mp, encoding="utf-8") as f:
                meta = json.load(f)
            with open(tp, encoding="utf-8") as f:
                tr = json.load(f)
        except (OSError, ValueError):
            continue
        tl = [(l, _secs(l.get("t"))) for l in (tr.get("lines") or []) if isinstance(l, dict)]
        facts = lead_facts(meta.get("case"), meta.get("auction"), meta.get("started_utc"), cards)
        prior = []
        for i, f in enumerate(meta.get("coach_fires") or []):
            line = str((f or {}).get("line") or "").strip()
            if (f or {}).get("id") != "ai" or not line:
                continue
            at = _secs(f.get("at"))
            if at is None:
                continue
            ctx = [{"spk": str(l.get("spk") or ""), "text": str(l.get("text") or "")}
                   for l, s in tl if s is not None and s <= at and str(l.get("text") or "").strip()]
            out.append({"session": name, "fire": i, "at": at, "lines": ctx[-24:],
                        "line": line, "heard": str(f.get("heard") or ""), "type": str(f.get("type") or ""),
                        "step": str(f.get("step") or ""), "asked": list(prior), "shown": prior[-1] if prior else "",
                        "lang": str(meta.get("lang") or ""), **facts})
            prior.append(line)
    return out


# ------------------------------------------------------------------ the judge
def _rows(lines, given):
    rows = []
    for l in lines:
        t = str(l.get("text") or "").strip()
        spk = str(l.get("spk") or "")
        who = "CALLER" if spk == "you" else "THEM" if spk == "them" else "?"
        if who == "?" and S._read_back(t, given):
            who = "CALLER"
        rows.append(who + ": " + t[:400])
    return rows


def judge_user(fire, cand):
    lane = fire.get("lane") or ""
    facts = ["Lane: " + (lane or "unknown")]
    if lane in S.AI_LANES:
        facts.append("Lane rules: " + S.AI_LANES[lane])
    when = S.sale_in_words(fire.get("days")) if lane != "filing" else ""
    facts.append("Court sale: " + (("set " + when + " (the coach may say that, never a date)") if when else "no sale timing given to the coach"))
    facts.append("Equity: " + ("VERIFIED on this lead" if fire.get("eq") == "verified"
                               else "NOT verified (the coach must not say or imply they have equity)"))
    given = [x for x in fire.get("asked") or [] if x]
    parts = ["LEAD FACTS THE COACH HAD:\n" + "\n".join(facts)]
    if given:
        parts.append("LINES THE COACH ALREADY GAVE ON THIS CALL, oldest first:\n" + "\n".join("- " + x for x in given[-6:]))
    parts.append("TRANSCRIPT UP TO THIS MOMENT:\n" + ("\n".join(_rows(fire.get("lines") or [], given)) or "(nothing yet)"))
    parts.append("COACH OUTPUT:\nHEARD: %s\nTYPE: %s\nSTEP: %s\nSAY: %s" % (
        cand.get("heard") or "", cand.get("type") or "", cand.get("step") or "", cand.get("line") or ""))
    parts.append("Grade the SAY line. JSON only.")
    return "\n\n".join(parts)


def judge_system():
    return JUDGE_SYSTEM + (S.playbook_text() or "(no playbook file found: grade on the rules above)")


def venice_chat(model, system, user, key, max_tokens=900, timeout=JUDGE_TIMEOUT):
    """One chat completion. (text, usage dict). Raises on HTTP / network errors."""
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "venice_parameters": {"include_venice_system_prompt": False, "disable_thinking": True,
                                  "strip_thinking_response": True}}
    req = urllib.request.Request(S.AI_URL, data=json.dumps(body).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8"))
    return str(j["choices"][0]["message"].get("content") or ""), (j.get("usage") or {})


def _as_pass(v):
    if isinstance(v, dict):
        v = v.get("pass")
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "pass", "yes"):
        return True
    if isinstance(v, str) and v.strip().lower() in ("false", "fail", "no"):
        return False
    raise ValueError("criterion is not pass/fail")


def parse_verdict(text):
    """The judge's reply -> {criterion: bool, ..., "score": int, "why": {...}, "failure_classes": [...]}.
    Tolerates a code fence or words around the object; raises ValueError on anything it cannot trust."""
    t = re.sub(r"^\s*```(?:json)?|```\s*$", "", str(text or "").strip(), flags=re.I).strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object")
    j = json.loads(t[a:b + 1])
    if not isinstance(j, dict):
        raise ValueError("not an object")
    v = {"why": {}}
    for c in CRITERIA:
        if c not in j:
            raise ValueError("missing " + c)
        v[c] = _as_pass(j[c])
        if isinstance(j[c], dict):
            v["why"][c] = str(j[c].get("why") or "")[:400]
    sc = j.get("score")
    if isinstance(sc, bool) or not isinstance(sc, (int, float)):
        try:
            sc = float(str(sc).split("/")[0])
        except (TypeError, ValueError):
            raise ValueError("score is not a number")
    v["score"] = int(round(max(0.0, min(10.0, float(sc)))))
    fc = j.get("failure_classes") or []
    fc = [fc] if isinstance(fc, str) else fc if isinstance(fc, list) else []
    fc = [str(x).strip().lower().replace(" ", "_").replace("-", "_") for x in fc if str(x).strip()]
    v["failure_classes"] = sorted({x if x in _FC else "other" for x in fc})
    if not all(v[c] for c in CRITERIA) and not v["failure_classes"]:
        v["failure_classes"] = ["unclassified"]
    if all(v[c] for c in CRITERIA):
        v["failure_classes"] = []
    return v


def judge(fire, cand, model, key, cap, tally, system=None):
    """Grade one candidate line. {"verdict": {...}} or {"error": code}. Every HTTP request draws on cap."""
    system = system or judge_system()
    user = judge_user(fire, cand)
    err = "cap"
    for attempt in range(3):
        if not cap.take():
            return {"error": err if attempt else "cap"}
        try:
            text, usage = venice_chat(model, system, user, key)
            tally.add(judge_calls=1, judge_in=int(usage.get("prompt_tokens") or 0),
                      judge_out=int(usage.get("completion_tokens") or 0),
                      judge_cached=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
                      if isinstance(usage.get("prompt_tokens_details"), dict) else 0)
            return {"verdict": parse_verdict(text)}
        except urllib.error.HTTPError as e:
            tally.add(judge_calls=1)
            err = "http%d" % e.code
            if e.code in (400, 401, 402, 403, 404):
                return {"error": err}
        except (urllib.error.URLError, socket.timeout, TimeoutError):
            tally.add(judge_calls=1)
            err = "net"
        except (ValueError, KeyError, IndexError, TypeError):
            err = "badjson"
        time.sleep(min(8.0, 1.5 * (attempt + 1)))
    return {"error": err}


# ------------------------------------------------------------------ the run
def _count_ai_post(tally):
    """Measure (never change) what the coach sends: wrap call_coach_server._ai_post to tally requests and
    approximate tokens (characters / 4; the coach does not keep the API's usage numbers)."""
    orig = S._ai_post
    if getattr(orig, "_coach_grade_counted", False):
        return

    def counted(body, key, st):
        out = orig(body, key, st)
        chars = sum(len(str(m.get("content") or "")) for m in body.get("messages") or [])
        tally.add(coach_requests=1, coach_in_est=chars // 4, coach_out_est=len(out) // 4)
        return out
    counted._coach_grade_counted = True
    S._ai_post = counted


def grade_one(fire, mode, model, key, judge_cap, replay_cap, tally, system):
    rec = {"session": fire["session"], "fire": fire["fire"], "at": fire["at"], "lane": fire.get("lane"),
           "days": fire.get("days"), "eq": fire.get("eq"), "orig": {k: fire[k] for k in ("line", "heard", "type", "step")}}
    if mode == "replay":
        if not replay_cap.take():
            rec["skipped"] = "replay_cap"
            return rec
        stat = {}
        t0 = time.time()
        try:
            res = S.ai_line(fire["lines"], fire.get("lang") or "", fire.get("shown") or "", fire.get("asked") or [],
                            fire.get("days"), fire.get("lane") or "", fire.get("eq") or "", stat=stat)
        except Exception as e:  # a bug in the coach is a finding, not a crash of the grader
            res, stat["why"] = {}, "error:" + type(e).__name__
        tally.add(replay_calls=1)
        rec["replay_ms"] = int((time.time() - t0) * 1000)
        rec["replay_model"] = stat.get("model")
        if not (res or {}).get("line"):
            rec["blank"] = stat.get("why") or "empty"
            return rec
        cand = {k: res.get(k) or "" for k in ("line", "heard", "type", "step")}
        rec["cand"] = cand
    else:
        cand = rec["orig"]
    rec.update(judge(fire, cand, model, key, judge_cap, tally, system))
    return rec


def run(fires, mode, model, key, concurrency=4, judge_cap=None, replay_cap=None, tally=None, on_result=None):
    judge_cap = judge_cap or Cap(MAX_JUDGE)
    replay_cap = replay_cap or Cap(MAX_REPLAY)
    tally = tally or Tally()
    system = judge_system()
    if mode == "replay":
        _count_ai_post(tally)
    out = []
    lock = threading.Lock()

    def work(f):
        r = grade_one(f, mode, model, key, judge_cap, replay_cap, tally, system)
        with lock:
            out.append(r)
            if on_result:
                on_result(r)
        return r
    with ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as ex:
        list(ex.map(work, fires))
    out.sort(key=lambda r: (r["session"], r["fire"]))
    return out


def aggregate(results):
    graded = [r for r in results if r.get("verdict")]
    n = len(graded)
    agg = {"fires": len(results), "graded": n,
           "judge_errors": dict(Counter(r["error"] for r in results if r.get("error"))),
           "replay_blank": dict(Counter(r["blank"] for r in results if r.get("blank"))),
           "skipped": dict(Counter(r["skipped"] for r in results if r.get("skipped")))}
    crit = {}
    for c in CRITERIA:
        p = sum(1 for r in graded if r["verdict"][c])
        crit[c] = {"pass": p, "fail": n - p, "pass_rate": round(p / n, 3) if n else None}
    agg["criteria"] = crit
    scores = [r["verdict"]["score"] for r in graded]
    agg["overall"] = {
        "mean_score": round(statistics.mean(scores), 2) if scores else None,
        "median_score": statistics.median(scores) if scores else None,
        "all_pass": sum(1 for r in graded if all(r["verdict"][c] for c in CRITERIA)),
        "all_pass_rate": round(sum(1 for r in graded if all(r["verdict"][c] for c in CRITERIA)) / n, 3) if n else None,
        "score_hist": {str(k): v for k, v in sorted(Counter(scores).items())},
    }
    fc = Counter(x for r in graded for x in r["verdict"]["failure_classes"])
    agg["failure_classes"] = [{"class": k, "count": v} for k, v in fc.most_common()]
    by_step = {}
    for r in graded:
        st = (r.get("cand") or r.get("orig") or {}).get("step") or "-"
        by_step.setdefault(st, []).append(r["verdict"]["score"])
    agg["by_step"] = {k: {"n": len(v), "mean": round(statistics.mean(v), 2)} for k, v in sorted(by_step.items())}
    return agg


# ------------------------------------------------------------------ cost estimate
def model_prices(key):
    """{model_id: (usd per M in, usd per M out)} from Venice, or {} if it cannot be read."""
    try:
        req = urllib.request.Request(MODELS_URL, headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        out = {}
        for m in j.get("data") or []:
            p = (m.get("model_spec") or {}).get("pricing") or {}
            try:
                out[m["id"]] = (float(p["input"]["usd"]), float(p["output"]["usd"]))
            except (KeyError, TypeError, ValueError):
                pass
        return out
    except Exception:
        return {}


def estimate(fires, mode, model, prices=None):
    """Upper-bound token estimate (no prompt-cache discount)."""
    sysj = len(judge_system()) // 4
    users = [len(judge_user(f, f)) // 4 for f in fires] or [0]
    n = len(fires)
    est = {"judge_calls": n, "judge_in": n * sysj + sum(users), "judge_out": n * 350}
    if mode == "replay":
        coach_sys = (len(S.AI_SYSTEM) + len(S.playbook_text())) // 4 + 600
        per = coach_sys + sum(users) // max(1, n)
        est.update(replay_calls=n, coach_in=int(n * per * 1.3), coach_out=int(n * 120 * 1.3))
    usd = 0.0
    prices = prices or {}
    if model in prices:
        pi, po = prices[model]
        usd += est["judge_in"] / 1e6 * pi + est["judge_out"] / 1e6 * po
    if mode == "replay" and S.AI_MODEL in prices:
        pi, po = prices[S.AI_MODEL]
        usd += est["coach_in"] / 1e6 * pi + est["coach_out"] / 1e6 * po
    est["usd_upper_bound"] = round(usd, 2) if prices else None
    return est


# ------------------------------------------------------------------ main
def safe_out_dir(path):
    """Results hold homeowner words: refuse any folder inside this (public) repo."""
    p = os.path.abspath(path)
    repo = os.path.abspath(REPO)
    if os.path.normcase(p) == os.path.normcase(repo) or os.path.normcase(p).startswith(os.path.normcase(repo) + os.sep):
        raise SystemExit("refusing to write grades inside the repo: " + p)
    return p


def _git_head():
    try:
        return subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--historical", action="store_true", help="grade every AI line that was shown")
    g.add_argument("--replay", action="store_true", help="re-ask the current ai_line() at each fire and grade that")
    ap.add_argument("--limit", type=int, default=0, help="grade a seeded random sample of this many fires")
    ap.add_argument("--seed", type=int, default=1119)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--judge-model", default=JUDGE_MODEL)
    ap.add_argument("--sessions", default=os.environ.get("COACH_SESSIONS") or S.SESSIONS)
    ap.add_argument("--leads-root", default="", help="folder with leads_final.json (default: the sessions folder's parent)")
    ap.add_argument("--out", default=os.path.join(DEALFLOW_DIR, "coach-grades"))
    ap.add_argument("--dry-run", action="store_true", help="print the estimate and stop")
    a = ap.parse_args(argv)
    mode = "replay" if a.replay else "historical"

    if not os.path.isdir(a.sessions):
        raise SystemExit("no sessions folder: " + a.sessions)
    root = a.leads_root or os.path.dirname(os.path.abspath(a.sessions))
    S.ROOT, S.LEADS = root, os.path.join(root, "leads_final.json")
    out_root = safe_out_dir(a.out)
    key = judge_key()
    if not key:
        raise SystemExit("VENICE_API_KEY is not set")

    fires = load_fires(a.sessions, load_cards())
    total = len(fires)
    cap_n = MAX_REPLAY if mode == "replay" else MAX_JUDGE
    n = min(a.limit or total, total, cap_n)
    if n < total:
        keep = set(random.Random(a.seed).sample(range(total), n))
        fires = [f for i, f in enumerate(fires) if i in keep]
    prices = model_prices(key)
    est = estimate(fires, mode, a.judge_model, prices)
    print("[grade] mode=%s fires=%d of %d judge=%s coach=%s fast=%s" % (mode, len(fires), total, a.judge_model,
                                                                      S.AI_MODEL, S.AI_FAST_MODEL or "-"))
    print("[grade] estimate: " + json.dumps(est))
    if a.dry_run:
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + mode
    od = os.path.join(out_root, stamp)
    os.makedirs(od, exist_ok=True)
    tally = Tally()
    judge_cap, replay_cap = Cap(MAX_JUDGE), Cap(MAX_REPLAY)
    fh = open(os.path.join(od, "results.jsonl"), "w", encoding="utf-8")
    done = [0]

    def on_result(r):
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.flush()
        done[0] += 1
        if done[0] % 20 == 0:
            print("[grade] %d/%d" % (done[0], len(fires)), flush=True)
    t0 = time.time()
    try:
        results = run(fires, mode, a.judge_model, key, a.concurrency, judge_cap, replay_cap, tally, on_result)
    finally:
        fh.close()
    summary = {"mode": mode, "stamp": stamp, "code": _git_head(), "judge_model": a.judge_model,
               "coach_model": S.AI_MODEL, "coach_fast_model": S.AI_FAST_MODEL, "seed": a.seed,
               "sessions_fires_total": total, "sampled": len(fires), "seconds": int(time.time() - t0),
               "estimate": est, "used": dict(tally.c), "caps": {"judge": [judge_cap.used, judge_cap.n],
                                                              "replay": [replay_cap.used, replay_cap.n]},
               **aggregate(results)}
    with open(os.path.join(od, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    # Counts only on the console: no transcript or line text.
    print("[grade] graded=%d mean=%s all_pass=%s" % (summary["graded"], summary["overall"]["mean_score"],
                                                    summary["overall"]["all_pass_rate"]))
    for c in CRITERIA:
        print("[grade]   %-10s %s" % (c, summary["criteria"][c]["pass_rate"]))
    print("[grade] failure classes: " + ", ".join("%s=%d" % (x["class"], x["count"]) for x in summary["failure_classes"][:8]))
    if summary["replay_blank"] or summary["judge_errors"]:
        print("[grade] blank=%s errors=%s" % (summary["replay_blank"], summary["judge_errors"]))
    print("[grade] used: " + json.dumps(summary["used"]))
    print("[grade] wrote " + od)
    return 0


if __name__ == "__main__":
    sys.exit(main())
