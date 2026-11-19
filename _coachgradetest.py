"""coach_grade.py offline: no network, no real call data.

Pins: the judge's JSON is parsed strictly (fences tolerated, junk refused, unknown failure labels
folded to "other"); aggregation counts per criterion, overall and by failure class; the judge and
replay caps are hard (HTTP requests never pass the cap, retries included); a moment is rebuilt from
only the transcript lines up to the fire; and results can never be written inside the repo.
"""
import io
import json
import os
import tempfile
import urllib.error

import call_coach_server as S
import coach_grade as G

bad = []


def chk(c, m):
    print(('ok   ' if c else 'FAIL ') + m)
    if not c:
        bad.append(m)


def verdict_json(ok=True, score=8, fc=None):
    v = {c: {"pass": ok, "why": "x"} for c in G.CRITERIA}
    v["score"] = score
    v["failure_classes"] = fc if fc is not None else ([] if ok else ["invented_fact"])
    return json.dumps(v)


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def api_reply(text, pin=1000, pout=100):
    return FakeResp(json.dumps({"choices": [{"message": {"content": text}}],
                                "usage": {"prompt_tokens": pin, "completion_tokens": pout}}).encode())


calls = []


def install(replies):
    """Replace urlopen in the grader with a queue of canned replies (a reply may be an exception)."""
    q = list(replies)

    def fake(req, timeout=None):
        calls.append(req.full_url)
        r = q.pop(0) if q else api_reply(verdict_json())
        if isinstance(r, Exception):
            raise r
        return r
    G.urllib.request.urlopen = fake


G.time.sleep = lambda s: None          # retries must not slow the test
S.playbook_text = lambda: "## Never say\n- a promised outcome"

# --- parsing ----------------------------------------------------------------------------------
v = G.parse_verdict(verdict_json(True, 9))
chk(all(v[c] for c in G.CRITERIA) and v["score"] == 9 and v["failure_classes"] == [], 'clean pass parses')
v = G.parse_verdict("```json\n" + verdict_json(False, 3, ["Invented Fact", "made_up_label"]) + "\n```")
chk(not v["grounded"] and v["score"] == 3, 'fenced reply parses')
chk(v["failure_classes"] == ["invented_fact", "other"], 'labels normalised; unknown label -> other')
v = G.parse_verdict('Here you go: {"grounded": true, "never_say": "pass", "speaker": {"pass": "false"}, '
                    '"on_step": true, "sayable": true, "score": "7/10", "failure_classes": []} thanks')
chk(v["never_say"] is True and v["speaker"] is False and v["score"] == 7, 'string pass/fail and "7/10" coerced')
chk(v["failure_classes"] == ["unclassified"], 'a failure with no label is counted as unclassified')
v = G.parse_verdict(verdict_json(True, 14))
chk(v["score"] == 10, 'score clamped to 10')
for junk, why in (("no json here", "no object"), ('{"grounded": true}', "missing criteria"),
                  (verdict_json().replace('"score": 8', '"score": "high"'), "non-numeric score"),
                  (verdict_json().replace('"pass": true', '"pass": "maybe"', 1), "non pass/fail value")):
    try:
        G.parse_verdict(junk)
        chk(False, 'refuses ' + why)
    except ValueError:
        chk(True, 'refuses ' + why)

# --- aggregation ------------------------------------------------------------------------------
def rec(i, ok, score, fc=(), crit_fail=()):
    vd = {c: (c not in crit_fail) for c in G.CRITERIA}
    vd.update(score=score, failure_classes=list(fc), why={})
    return {"session": "s", "fire": i, "orig": {"step": "close"}, "verdict": vd}


res = [rec(0, True, 9), rec(1, False, 4, ["invented_fact"], ["grounded"]),
       rec(2, False, 2, ["invented_fact", "too_long"], ["grounded", "sayable"]),
       {"session": "s", "fire": 3, "error": "badjson"}, {"session": "s", "fire": 4, "blank": "skip"}]
A = G.aggregate(res)
chk(A["fires"] == 5 and A["graded"] == 3, 'graded count excludes errors and blanks')
chk(A["criteria"]["grounded"] == {"pass": 1, "fail": 2, "pass_rate": 0.333}, 'per-criterion pass rate')
chk(A["criteria"]["speaker"]["pass_rate"] == 1.0, 'untouched criterion stays at 100%')
chk(A["overall"]["mean_score"] == 5.0 and A["overall"]["all_pass"] == 1, 'overall mean and all-pass')
chk(A["failure_classes"][0] == {"class": "invented_fact", "count": 2}, 'worst failure class first, with count')
chk(A["judge_errors"] == {"badjson": 1} and A["replay_blank"] == {"skip": 1}, 'errors and blanks are counted')
chk(G.aggregate([])["overall"]["mean_score"] is None, 'empty run does not crash')

# --- caps -------------------------------------------------------------------------------------
cap = G.Cap(3)
chk([cap.take() for _ in range(5)] == [True, True, True, False, False] and cap.used == 3, 'Cap takes exactly n')

fires = [{"session": "s%02d" % i, "fire": i, "at": 10, "lines": [{"spk": "", "text": "who is this"}],
          "line": "It's Alejandro.", "heard": "who is this", "type": "question", "step": "opener",
          "asked": [], "shown": "", "lane": "", "days": None, "eq": ""} for i in range(10)]
calls.clear()
install([])
out = G.run(fires, "historical", "judge-x", "k", concurrency=3, judge_cap=G.Cap(4))
chk(len(calls) == 4, 'judge cap: exactly 4 HTTP requests for 10 lines (got %d)' % len(calls))
chk(sum(1 for r in out if r.get("verdict")) == 4 and sum(1 for r in out if r.get("error") == "cap") == 6,
    'lines past the cap are marked cap, not graded')

calls.clear()
install([api_reply("not json"), urllib.error.URLError("down"), api_reply(verdict_json(False, 5))])
t = G.Tally()
r = G.judge(fires[0], fires[0], "judge-x", "k", G.Cap(10), t)
chk(r.get("verdict") and r["verdict"]["score"] == 5 and len(calls) == 3, 'bad JSON and a network error are retried')
calls.clear()
install([api_reply("not json")] * 5)
r = G.judge(fires[0], fires[0], "judge-x", "k", G.Cap(2), G.Tally())
chk(len(calls) == 2 and r.get("error") == "badjson", 'retries draw on the cap too')
calls.clear()
install([urllib.error.HTTPError("u", 401, "no", {}, None)])
r = G.judge(fires[0], fires[0], "judge-x", "k", G.Cap(10), G.Tally())
chk(len(calls) == 1 and r.get("error") == "http401", 'a refused key is not retried')

# replay: the current ai_line is called, capped, and its line (not the old one) is graded
seen = []
real_ai_line = S.ai_line


def fake_ai_line(lines, lang="", shown="", asked=None, days=None, lane="", eq="", stat=None):
    seen.append((len(lines), shown, list(asked or []), days, lane, eq))
    if len(seen) == 2:
        stat["why"] = "skip"
        return {}
    return {"line": "Fresh line.", "heard": "h", "type": "question", "step": "situation"}


S.ai_line = fake_ai_line
calls.clear()
install([])
t = G.Tally()
out = G.run(fires[:5], "replay", "judge-x", "k", concurrency=1, replay_cap=G.Cap(3), tally=t)
S.ai_line = real_ai_line
chk(len(seen) == 3 and t.c["replay_calls"] == 3, 'replay cap: ai_line called 3 times for 5 fires')
chk(sum(1 for r in out if r.get("skipped") == "replay_cap") == 2, 'fires past the replay cap are skipped')
chk(sum(1 for r in out if r.get("blank") == "skip") == 1, 'a blank fresh line is recorded with its reason')
chk(all(r["cand"]["line"] == "Fresh line." for r in out if r.get("verdict")) and len(calls) == 2,
    'the fresh line is what gets judged')
chk("SAY: Fresh line." in G.judge_user(fires[0], {"line": "Fresh line.", "heard": "", "type": "", "step": ""}),
    'judge prompt carries the candidate line')

# --- rebuilding the moment from a saved call (synthetic, in a temp folder) ---------------------
with tempfile.TemporaryDirectory() as td:
    d = os.path.join(td, "20260101_call")
    os.makedirs(d)
    json.dump({"case": "", "auction": None, "started_utc": "2026-01-01T10:00:00+00:00", "coach_fires": [
        {"id": "obj:busy", "at": "00:05"},
        {"id": "ai", "at": "00:20", "line": "First line.", "heard": "a", "type": "answer", "step": "situation"},
        {"id": "ai", "at": "00:40", "line": "", "type": "answer"},
        {"id": "ai", "at": "01:05", "line": "Second line.", "heard": "b", "type": "question", "step": "close"},
    ]}, open(os.path.join(d, "meta.json"), "w"))
    json.dump({"lines": [{"t": "00:02", "text": "hello", "spk": ""}, {"t": "00:19", "text": "one", "spk": "them?"},
                         {"t": "00:30", "text": "two", "spk": ""}, {"t": "01:10", "text": "later", "spk": ""}]},
              open(os.path.join(d, "transcript.json"), "w"))
    os.makedirs(os.path.join(td, "no_transcript"))
    F = G.load_fires(td, {})
    chk(len(F) == 2, 'only shown AI lines become fires (cards and blanks skipped)')
    chk([l["text"] for l in F[0]["lines"]] == ["hello", "one"], 'context stops at the fire time')
    chk([l["text"] for l in F[1]["lines"]] == ["hello", "one", "two"], 'later words never leak into the moment')
    chk(F[1]["asked"] == ["First line."] and F[1]["shown"] == "First line.", 'earlier lines ride as asked/shown')
    chk(F[0]["lines"][1]["spk"] == "them?", 'speaker tags pass through as the page sent them')

chk(G._secs("01:05") == 65 and G._secs("1:00:01") == 3601 and G._secs("x") is None, 'clock parsing')
lf = G.lead_facts("C1", "2026-01-11", "2026-01-01T10:00:00+00:00",
                  {S._norm("C1"): {"case": "C1", "lane": "hoa", "eqstate": "clear", "equity": 5}})
chk(lf == {"lane": "hoa", "days": 10, "eq": "verified"}, 'lead facts: lane, days as of the call, equity flag')
chk(G.lead_facts("", None, "", {}) == {"lane": "", "days": None, "eq": ""}, 'no lead -> nothing assumed')

# --- output never lands in the repo -----------------------------------------------------------
try:
    G.safe_out_dir(os.path.join(G.REPO, "coach-grades"))
    chk(False, 'refuses an output folder inside the repo')
except SystemExit:
    chk(True, 'refuses an output folder inside the repo')
chk(G.safe_out_dir(os.path.join(tempfile.gettempdir(), "cg")).endswith("cg"), 'accepts a folder outside the repo')
chk(G.MAX_JUDGE <= 400 and G.MAX_REPLAY <= 400, 'hard caps are at most 400')

print('\n%d failed' % len(bad) if bad else '\nall passed')
raise SystemExit(1 if bad else 0)
