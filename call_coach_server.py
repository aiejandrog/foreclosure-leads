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
# Scored 2026-11-16 on twelve moments from the test calls, twice each: deepseek-v4-flash 17/24 at
# 1.5 s median (6.1 s worst), deepseek-v4-1-flash 22/24 at 1.0 s (2.7 s worst). The newer one is
# both the faster and the one that answers "how can you help me" with the offer.
AI_MODEL = os.environ.get("COACH_AI_MODEL", "deepseek-v4-1-flash")
# The call has a path (CALL_SCRIPT_NEPQ.md, the same stages the page shows). The first version of
# this prompt only said "reflect and ask a question", and on test call 11-16 11:34 it circled:
# "sounds like you're feeling overwhelmed, what would a good outcome look like" three times and
# never asked for the appointment. The model now has to say where the call is on the path and
# move it ONE step toward the close on every line.
AI_STEPS = [
    ("opener", "Opener", "Say who you are: with Biscayne Solutions Group, not the lender, not calling to buy the house."),
    ("situation", "Situation", "Before I get into any of it, what is it you're actually hoping happens with the house?"),
    ("problem", "Problem", "Has anybody actually explained what happens to your balance every time that sale date gets pushed back?"),
    ("solution", "What they've tried", "Before today, were you out there looking at options so this didn't go to sale, or what were you doing about it?"),
    ("consequence", "Consequence", "Let me ask you straight: if nothing changes and that date comes and goes, where does that leave you and your family?"),
    ("bridge", "Bridge", "Okay, based on what you just told me, this is exactly the kind of thing we can help with. You said [what they want], and it has you feeling [their word for it]."),
    ("close", "Book the advisor", "So here's the one thing I'd want for you before that date hits: our senior advisor takes about five minutes and lays your real options out. Worst case, you know more than you did this morning. Let me get you on his calendar. Is later today better, or first thing tomorrow?"),
    ("booked", "Lock the time", "Repeat the day and time back, ask them to save your number, and end the call."),
]
AI_STEP_IDS = [x[0] for x in AI_STEPS]
AI_SYSTEM = (
    "You coach a caller live, during a phone call with a homeowner who has a foreclosure case filed against "
    "their property. The caller is Alejandro with Biscayne Solutions Group. He is not the lender, he is not "
    "buying the house, and there is no fee. The ONE goal of the call is to book a free five-minute call between "
    "the homeowner and the senior advisor, at a set time. Every line you write moves toward that booking.\n"
    "You get the live transcript. Labels are CALLER, THEM or ?. They come from one microphone and are often "
    "wrong or missing, so work out who said each line from what it says.\n"
    "The call follows this path. Next to each step is what that step is for, in the script's words. It is "
    "there so you know the point of the step. It is NOT a line to say:\n"
    + "".join("%d. %s (%s): %s\n" % (n + 1, sid, name, line) for n, (sid, name, line) in enumerate(AI_STEPS)) +
    "LISTEN FIRST. Before you write anything, find the homeowner's most recent words in the transcript "
    "(not the caller's, and not the caller reading one of your lines) and decide what kind of thing it was:\n"
    "  answer    - they answered the caller's last question\n"
    "  question  - they asked something (who is this, what is this about, how did you get this, what sale)\n"
    "  objection - not interested, busy, already has a lawyer or attorney, already has a plan, the bank is "
    "helping, is this a scam, giving up on the house\n"
    "  time      - they named a day or time for the advisor call\n"
    "  stop      - they said, in so many words, stop calling, do not call me, take me off your list, remove "
    "my number. ONLY that. A plain 'no', 'not interested', 'I'm busy' or 'I said no' is NEVER stop; it is an "
    "objection.\n"
    "  unclear   - a word or two, small talk, or you cannot tell\n"
    "Your line answers THAT. The path only says where to go once you have answered it.\n"
    "What to say for each kind:\n"
    "- answer: open with a few of their own words (what they want, who they named: the house, the wife, the "
    "kids), then ask what the NEXT step is trying to find out. Move one step at a time. Never jump two steps.\n"
    "- question: answer it plainly in one short sentence first, then ask the current step's question. If "
    "they ask what the call is about or how you can help, the answer is the offer itself: five minutes with "
    "our senior advisor, who lays out the options nobody has put on their table, free.\n"
    "- objection: one short sentence that agrees with them, then ONE question about what they just said. "
    "A lawyer or attorney: keep the attorney, and ask what the attorney has told them about the sale date. "
    "A plan or the bank helping: keep it exactly as it is, and ask what happens if it is not approved before "
    "the sale date. Not interested, busy, or a flat no: agree in a few words, then work it, however many "
    "times they say it (see NEVER FOLD). A scam, who are you: public court record, not the lender, not buying, no fee, "
    "then the situation question. Giving up, 'let them take it': agree, then the close, because the advisor "
    "also covers leaving on their own terms. An objection is NEVER answered with the close or with booked, "
    "except giving up.\n"
    "- time: step booked. Repeat the day and time back and ask them to save the number.\n"
    "- stop: confirm they are off the list and say goodbye. This is the only way a call ends.\n"
    "NEVER FOLD. Test call 11-16 17:40: the homeowner said 'really not interested, man' once and you wrote 'No "
    "problem, I'll leave you be', then read a plain 'No' as a request to be taken off the list. He never said "
    "stop. A person who is still on the phone is still deciding, and 'not interested' is a reflex, not a "
    "decision. Unless TYPE is stop you never say goodbye, never offer to leave them be or let them go, never "
    "say 'take care', and never mention taking them off a list. You work the objection, and each time they "
    "repeat it you use an angle you have not used yet on this call, then go back to the ask. The FIRST time "
    "they say they are not interested, the line is always the challenge, said calm and curious, as a real "
    "question: 'So you're not interested in saving your property, or the equity you've got in it?' After that, "
    "the angles:\n"
    "  1. It is information, not interest: you have not told them anything yet, and there is a case with their "
    "address on it.\n"
    "  2. Ask what exactly they are not interested in: keeping the home, or protecting what they have put into "
    "it.\n"
    "  3. Isolate: if it cost nothing and could not touch the plan they already have, is there any other reason "
    "not to take five minutes.\n"
    "  4. Worst case: five minutes, and if nothing fits you shake hands and part friends, and they lost "
    "nothing.\n"
    "  5. What if: what if he points out one option nobody else has shown them.\n"
    "  6. Fairness: five minutes against the house is a trade worth making, right.\n"
    "  7. Busy: then name the time, later today or first thing tomorrow, and it is five minutes.\n"
    "Stay warm and unbothered while you do it. Never argue, never guilt them, never raise the stakes with "
    "fear. Persistent and friendly, not pushy.\n"
    "- unclear: stay on the same step and ask its question again in fewer words.\n"
    "Pace: do not use bridge or close until the homeowner has said what they want AND has answered at least "
    "one of the problem, solution or consequence questions. Never use booked unless they named a time. Never "
    "go back to a finished step, and never ask again something they already answered.\n"
    "TALK LIKE A PERSON. Test call 11-16 17:03 read like a script: three-sentence lines, the same stock "
    "openers every time, script sentences said word for word, the same question asked three times. The caller "
    "has to say your line out loud to a stranger. Write what a calm, friendly person would actually say on the "
    "phone:\n"
    "- SHORT. One quick reaction and one question. Aim for under twenty-five words, never more than two "
    "sentences plus the question. One thought only: never a reason, another reason, and then a question.\n"
    "- React to what they JUST said, in their own words, the way you would if you were really listening: "
    "'Okay, so the realtor's on it.' 'Gotcha, you're at work.' 'Yeah, that's a lot.' 'Oh, so the lawyer's "
    "filing next week.' Different every time. Never open with: Fair question, That's fair, Good, I understand, "
    "I hear you, It sounds like, Let me ask you straight.\n"
    "- Then ask what the step needs, in your own everyday words. NEVER say a script or playbook sentence word "
    "for word, and never use a sentence or a question that is in the list of lines you already gave. If a "
    "question was already asked, find a new angle or move on.\n"
    "- Sound spoken, not written: contractions, plain small words, the occasional 'yeah', 'look', 'honestly', "
    "'I mean'. No dashes, no semicolons, no colons, no lists. If they are casual, be casual back.\n"
    "- If they already told you what a step asks, that step is done. Use what they said in the next question.\n"
    "- Examples of the same moment, wrong and right.\n"
    "  Wrong: Good, a realtor's a real start, and you've got equity on your side. Let me ask you straight, "
    "though: if nothing changes and that sale date comes and goes before the sale closes, where does that leave "
    "you and your family?\n"
    "  Right: Okay, so the realtor's working on it. What happens if it doesn't close before the sale date?\n"
    "  Wrong: Fair question, five minutes with our senior advisor, free, and he lays out the options nobody's "
    "put on your table. So what is it you're hoping happens with the house?\n"
    "  Right: Honestly, it's just five minutes with our senior advisor, no charge. He shows you what your "
    "options are. What are you hoping to do with the place?\n"
    "  Wrong: That's fair, and I'm not selling anything. There's a case filed under your address by the "
    "association, and that's the only reason I have your number. What were you hoping would happen with the "
    "house?\n"
    "  Right: No worries, I'm not selling you anything. I just saw the association filed a case on your place. "
    "Did you know about that?\n"
    "- The close is the one place with fixed parts: five minutes with our senior advisor, and the two times "
    "(later today or first thing tomorrow). Say even that in a natural sentence, and say it differently if you "
    "already asked once. That is the only either/or allowed; never ask whether they want the call.\n"
    "- Write every number as a word. No dollar amounts, no promises of any outcome, no legal advice, nothing "
    "about what the bank or the court will do.\n"
    "Reply in exactly this form and nothing else:\n"
    "HEARD: <the homeowner's most recent words, copied from the transcript>\n"
    "TYPE: <answer, question, objection, time, stop or unclear>\n"
    "STEP: <one step id from the path>\n"
    "SAY: <the line>")
_AI_BAD = re.compile(r"[\d$]")

# THE PLAYBOOK. The prompt above knows the path of the call; it does not know the sales system.
# That lives in one note the caller can edit (Jesse's CIOC, the objection moves, the questions, what
# is true about a Florida foreclosure, the never-say list). It is kept OUT of this public repo: the
# default is the note in the private vault, COACH_PLAYBOOK points somewhere else. Everything after
# the last line that is only "---" is sent to the model. Re-read when the file changes, so an edit
# is live on the next line with no restart. No file: the coach runs on the prompt alone.
PLAYBOOK = os.environ.get("COACH_PLAYBOOK") or os.path.join(
    os.path.expanduser("~"), "projects", "obsidian-vault", "5-projects", "MSG Sales", "Call Coach AI Playbook.md")
_playbook = {"mtime": None, "text": ""}


def playbook_text():
    try:
        mt = os.path.getmtime(PLAYBOOK)
    except OSError:
        return ""
    if mt != _playbook["mtime"]:
        try:
            with open(PLAYBOOK, encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            return ""
        body = re.split(r"(?m)^---\s*$", raw)[-1]
        _playbook.update(mtime=mt, text=re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", body).strip()[:40000])
    return _playbook["text"]


def sale_in_words(days):
    """How far off the court sale is, in words (the line may not carry a digit). '' when unknown."""
    try:
        d = int(days)
    except (TypeError, ValueError):
        return ""
    if d < 0:
        return ""
    if d <= 1:
        return "today or tomorrow"
    if d <= 6:
        return "in a few days"
    if d <= 10:
        return "in about a week"
    if d <= 17:
        return "in about two weeks"
    if d <= 24:
        return "in about three weeks"
    if d <= 38:
        return "in about a month"
    if d <= 70:
        return "in about two months"
    return "more than two months out"


# What a real stop request sounds like, and what folding sounds like.
_AI_STOP = re.compile(r"stop calling|quit calling|do ?n[o'\u2019]?t (ever )?call|never call|take me off|remove (me|my)|"
                      r"off (your|the|that) list|do not contact|no me llame|no llame|quiteme de", re.I)
_AI_FOLD = re.compile(r"leave you be|let you go|leave it there|leave you alone|take you off|off (the|my|our) list|"
                      r"take care|good ?bye|have a (good|great|nice)|won[\u2019']?t (call|bother)|"
                      r"not (going to|gonna) (call|bother)|i[\u2019']?ll let you", re.I)


_AI_NOTINT = re.compile(r"not (even |really |that |at all )*interested|no interest in|no me interesa|no estoy interesad", re.I)
_AI_CHALLENGED = re.compile(r"not interested in saving|no le interesa salvar", re.I)
AI_CHALLENGE = ("So you're not interested in saving your property, or the equity you've got in it?",
                "¿Entonces no le interesa salvar su propiedad, ni el patrimonio que tiene en ella?")


def folds(res):
    """True when a line gives up on the call: a goodbye, or reading their words as a stop request."""
    return res.get("type") == "stop" or (res.get("type") != "time" and bool(_AI_FOLD.search(res.get("line", ""))))


def venice_key():
    return user_var("VENICE_API_KEY")


def _words(t):
    return re.sub(r"[^a-z ]+", " ", str(t or "").lower().replace("'", "").replace("\u2019", "")).split()


def _read_back(text, given):
    """True when `text` is the caller reading out one of the lines the coach gave: most of that
    line's words are in it and little else is."""
    said = _words(text)
    for g in given:
        gw = _words(g)
        if len(gw) < 4:
            continue
        hit = sum(1 for w in gw if w in said)
        extra = sum(1 for w in said if w not in gw)
        if hit / len(gw) >= 0.6 and extra <= max(4, len(gw) // 2):
            return True
    return False


# WHICH CONVERSATION THIS IS. One script for every lead was wrong for half of them: a fresh filing has
# no sale date to talk about, a tax sale has no bank, and an investor has no "wife and kids".
AI_LANES = {
    "auction": "A foreclosure sale for this property is on the court calendar. Everything in the path applies as written.",
    "hoa": "This case was filed by the homeowners or condo association, not the mortgage lender. Say 'the association's "
           "case', not 'the bank'. What an association is owed is usually small next to what the home is worth; that gap is "
           "why this is fixable and why it is dangerous to leave alone. Never state either amount.",
    "taxdeed": "This is a county tax sale for unpaid property taxes, not a mortgage foreclosure. Never say 'foreclosure', "
               "'the bank' or 'your lender' on this call; say 'the tax sale on the property' and 'the county'. The goal "
               "is the same: five minutes with the senior advisor.",
    "filing": "A lawsuit was filed recently and NO sale date has been set. Never mention a sale date or 'before the sale'; "
              "say 'the case that was filed' and 'before this goes any further'. This owner is earlier than most people "
              "we reach and has the most options of anyone: say that as good news. The consequence question is 'if "
              "nothing changes and this keeps moving through the court, where does that leave you?'",
    "investor": "The owner is most likely an investor or a company, and the paper is short-term money that came due. Talk "
                "business, not family: no 'wife and kids', no feelings, unless they bring it up. No rates, terms or "
                "numbers. The offer is five minutes with the senior advisor, who works with licensed lenders on replacing "
                "that paper with long-term money. Ask what else they hold, because the same thing may help those too.",
}


def ai_line(lines, lang="", shown="", asked=None, days=None, lane=""):
    """The next line for the caller as {"line", "step", "n", "of", "name"}, or {} (no key, nothing
    to answer, a slow or failed call, or a reply that breaks the no-numbers rule). Never raises: a
    dead model must not touch the call."""
    key = venice_key()
    if not key:
        return {}
    rows = []
    for l in (lines or [])[-24:]:
        t = str((l or {}).get("text") or "").strip()
        if not t:
            continue
        spk = str((l or {}).get("spk") or "")
        who = "CALLER" if spk == "you" else "THEM" if spk == "them" else "?"
        if who == "?" and _read_back(t, list(asked or []) + [shown]):
            who = "CALLER"            # one microphone: your line, read out, is the caller for certain
        rows.append(who + ": " + t[:400])
    if not rows:
        return {}
    system = AI_SYSTEM + ("\nWrite the SAY line in Spanish." if str(lang).lower().startswith("es") else "")
    pb = playbook_text()
    if pb:
        system += ("\n\nPLAYBOOK. This is the caller's own sales system. Take the MOVE and the facts from it: what "
                   "to do when they say this, which analogy fits, what is true. Do not take the wording. Its "
                   "sentences are examples of the idea; say the idea your own way, short, as in TALK LIKE A PERSON. "
                   "Its 'Never say' list overrides everything else.\n" + pb)
    lane = str(lane or "").lower()
    if lane in AI_LANES:
        system += "\n\nTHIS LEAD: " + AI_LANES[lane]
    when = sale_in_words(days) if lane != "filing" else ""
    if when:
        system += ("\n\nCourt calendar for this homeowner: the sale is set " + when + ". You may say that in those "
                   "words when it helps the line. Never say a date.")
    shown = " ".join(str(shown or "").split())[:320]
    prior = [" ".join(str(x or "").split())[:320] for x in (asked or [])[-5:] if str(x or "").strip()]
    if prior:
        system += ("\nLines you already gave on this call, oldest first:\n" + "\n".join("- " + x for x in prior)
                   + "\nNever repeat one of them or ask the same thing in other words. The new line is a later step.")
    if shown:
        system += ("\nThe caller's screen currently shows: \"" + shown + "\". The caller may have just read it "
                   "out. Write the line that comes after the homeowner's answer to it.")
    said = " ".join(r.split(": ", 1)[1] for r in rows[-6:])

    def ask(sys_text):
        body = {"model": AI_MODEL, "max_tokens": 300, "temperature": 0.6, "stream": False,
                "messages": [{"role": "system", "content": sys_text}, {"role": "user", "content": "\n".join(rows)}],
                "venice_parameters": {"include_venice_system_prompt": False, "disable_thinking": True,
                                      "strip_thinking_response": True}}
        import urllib.request
        out = ""
        for _try in range(2):            # one retry: a dropped request must not leave the box blank
            try:
                req = urllib.request.Request(AI_URL, data=json.dumps(body).encode("utf-8"),
                                             headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    j = json.loads(r.read().decode("utf-8"))
                out = str(j["choices"][0]["message"].get("content") or "")
            except Exception:
                out = ""
            if out.strip():
                break
        if not out.strip():
            return {}

        def field(name):
            m = re.search(r"^\s*%s:\s*(.*)$" % name, out, re.I | re.M)
            return " ".join(m.group(1).split()).strip().strip('"\u201c\u201d').strip() if m else ""
        step, kind, heard, line = field("STEP").lower(), field("TYPE").lower(), field("HEARD"), field("SAY")
        if not re.search(r"^\s*SAY:", out, re.I | re.M) and not re.search(r"^\s*(STEP|TYPE|HEARD):", out, re.I | re.M):
            line = " ".join(out.split()).strip().strip('"\u201c\u201d').strip()      # a bare line, no form
        step = step if step in AI_STEP_IDS else ""
        kind = kind if kind in ("answer", "question", "objection", "time", "stop", "unclear") else ""
        if step == "booked" and kind != "time":
            step = "close"
        # No figure may come from the model. The one exception is the clock time the homeowner just
        # gave ("ten fifteen tomorrow"): repeating it back is the booking, and it is never a dollar sign.
        bad = ("$" in line) if kind == "time" else bool(_AI_BAD.search(line))
        line = re.sub(r"\s*[\u2014\u2013]\s*", ", ", line)        # a dash is written, not spoken
        if not line or line.upper().rstrip(".") == "SKIP" or len(line) > 420 or bad:
            return {}
        res = {"line": line, "step": step, "of": len(AI_STEPS), "type": kind, "heard": heard[:200]}
        if step:
            res["n"] = AI_STEP_IDS.index(step) + 1
            res["name"] = AI_STEPS[res["n"] - 1][1]
        return res

    res = ask(system)
    # THE CHALLENGE, guaranteed (Alex, 11-16: "why didn't you ask, so you're not interested in saving
    # your property, your equity?"). The first "not interested" of a call gets his question, word for
    # word, whatever the model wrote. Later ones get the model's other angles. A stop request is
    # never challenged.
    last_said = rows[-1].split(": ", 1)[1] if rows else ""
    if (_AI_NOTINT.search(last_said) and not _AI_STOP.search(said)
            and not any(_AI_CHALLENGED.search(x or "") for x in prior + [shown])):
        es = str(lang).lower().startswith("es")
        res = {"line": AI_CHALLENGE[1 if es else 0], "step": (res or {}).get("step") or "close", "of": len(AI_STEPS),
               "type": "objection", "heard": last_said[:200]}
        res["n"] = AI_STEP_IDS.index(res["step"]) + 1
        res["name"] = AI_STEPS[res["n"] - 1][1]
        return res
    # NEVER FOLD, enforced. If the line ends the call, or calls their words a stop request, and nobody
    # in the last few lines actually asked to stop being called, that line is not shown: the model is
    # asked again and told so. A second fold shows nothing rather than a goodbye.
    if res and folds(res) and not _AI_STOP.search(said + " " + res.get("heard", "")):
        res = ask(system + "\n\nIMPORTANT: the homeowner has NOT asked to stop being called. Your last answer ended "
                  "the call or treated their words as a stop request. That is wrong. Their last words are an "
                  "objection: TYPE is objection. Answer it with a NEVER FOLD angle you have not used and go back "
                  "to the ask. No goodbye, no 'take care', nothing about a list.")
        if res and folds(res):
            return {}
        if res and res.get("type") == "stop":
            res["type"] = "objection"
    return res


# THE CALL WRAP-UP. When the call ends the same model reads the whole transcript and drafts the log
# entry: what happened, what they want, what they pushed back with, the time they agreed to, and a
# note. It is a DRAFT: the caller sees it on the outcome screen, fixes it, and picks the outcome
# himself; only then does it go to Call Mode. One microphone still mislabels who said what, and one
# wrong "appointment" logged by a machine would corrupt the cadence and the stats.
WRAP_OUTCOMES = ("appointment", "callback", "talked", "not_interested", "voicemail", "bad_number")
WRAP_SYSTEM = (
    "You write the log entry for a phone call that just ended. The caller is Alejandro with Biscayne Solutions "
    "Group; the other person is a property owner with a court case or tax sale on the property. Labels in the "
    "transcript (CALLER, THEM, ?) come from one microphone and are often wrong or missing; work out who said what "
    "from the content. Report only what was actually said. If something was not said, write none.\n"
    "Reply in exactly this form and nothing else:\n"
    "OUTCOME: <one of: appointment, callback, talked, not_interested, voicemail, bad_number>\n"
    "WANT: <keep, sell, undecided or unknown: what the owner wants to do with the property>\n"
    "OBJECTION: <their main pushback in a few words, or none>\n"
    "WHEN: <the day and time the owner agreed to for the next call, in their words, or none>\n"
    "NOTE: <two short sentences a teammate could act on: their situation and plan in their own words, and what "
    "was agreed. No advice, no opinions.>\n"
    "appointment = they agreed to a set day or time for the advisor call. callback = they asked to be called "
    "another time without agreeing to the advisor call. talked = a real conversation with neither. voicemail = "
    "nobody answered or a machine did. bad_number = wrong person or a dead line.")


def ai_wrapup(lines, lane=""):
    """{"outcome","want","objection","when","note"} drafted from the transcript, or {} (no key, an
    empty call, or a failed request). Never raises."""
    key = venice_key()
    rows = []
    for l in (lines or [])[-120:]:
        t = str((l or {}).get("text") or "").strip()
        if t:
            spk = str((l or {}).get("spk") or "")
            rows.append(("CALLER" if spk == "you" else "THEM" if spk == "them" else "?") + ": " + t[:300])
    if not key or not rows:
        return {}
    system = WRAP_SYSTEM + ("\nThis lead: " + AI_LANES[lane] if lane in AI_LANES else "")
    body = {"model": AI_MODEL, "max_tokens": 260, "temperature": 0.1, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(rows)}],
            "venice_parameters": {"include_venice_system_prompt": False, "disable_thinking": True,
                                  "strip_thinking_response": True}}
    import urllib.request
    out = ""
    for _try in range(2):
        try:
            req = urllib.request.Request(AI_URL, data=json.dumps(body).encode("utf-8"),
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                out = str(json.loads(r.read().decode("utf-8"))["choices"][0]["message"].get("content") or "")
        except Exception:
            out = ""
        if out.strip():
            break

    def field(name):
        m = re.search(r"^\s*%s:\s*(.*)$" % name, out, re.I | re.M)
        return " ".join(m.group(1).split()).strip().strip('"\u201c\u201d').strip() if m else ""
    oc = field("OUTCOME").lower().replace(" ", "_")
    if oc not in WRAP_OUTCOMES:
        return {}
    want = field("WANT").lower()
    none = lambda v: "" if v.lower().rstrip(".") in ("none", "n/a", "unknown", "") else v
    return {"outcome": oc, "want": want if want in ("keep", "sell", "undecided") else "",
            "objection": none(field("OBJECTION"))[:80], "when": none(field("WHEN"))[:60], "note": none(field("NOTE"))[:320]}


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
        "county": "MIAMI-DADE",
        "lane": lane_of("TD" if "tax" in str(r.get("Auction Type") or "").lower() else "FC", r.get("case_type")),
        "src": "leads_final.json",
    }


# EVERY LANE (2026-11-16, Alex: "add Coach onto every section, all lanes, morning worker"). The coach
# read leads_final.json only: the Miami-Dade auction file. A Broward or Palm Beach auction, a fresh
# filing or an investor balloon opened the coach with no name, no sale date and no lane, so it looked
# like the coach only worked in one corner of Call Mode. These are the board's other lead files, in the
# slim shape the board itself bakes. First file that has a case wins.
SLIM_FILES = ("broward_leads.json", "palmbeach_leads.json", "balloon_leads.json", "lp_leads.json")
_slim = {"sig": None, "rows": []}


def lane_of(st, ctype):
    """Which conversation this is: auction (court sale on the calendar), hoa (the association filed),
    taxdeed (county sale for unpaid taxes), filing (lawsuit filed, no sale date yet), investor
    (short-term paper that came due)."""
    st, ct = str(st or "").upper(), str(ctype or "").lower()
    if st == "BAL" or "balloon" in ct:
        return "investor"
    if st == "TD" or "tax" in ct:
        return "taxdeed"
    if st == "LP":
        return "filing"
    if "hoa" in ct or "condo" in ct:
        return "hoa"
    return "auction"


def slim_card(r, src):
    days = r.get("days")
    days = days if isinstance(days, int) and 0 <= days < 9000 else None
    return {
        "case": r.get("case"),
        "address": r.get("addr"),
        "owners": r.get("oname") or r.get("owners"),
        "auction": r.get("auction") or None,
        "days": days,
        "judgment": None, "opening_bid": None,          # the slim files carry estimates, never quoted here
        "plaintiff": r.get("plaintiff"),
        "equity": None, "eqstate": None,                # the board's verdict arrives in the coach link, or not at all
        "tier": r.get("tier"), "score": r.get("score"), "homestead": r.get("hs"),
        "judgment_unknown": True, "sale_bk_active": False,
        "county": r.get("county"), "lane": lane_of(r.get("st"), r.get("ctype")), "src": src,
    }


def all_cards():
    """Every lead the coach can open: leads_final first, then the slim files. Cached on file times."""
    sig = []
    for f in ("leads_final.json",) + SLIM_FILES:
        try:
            sig.append(os.path.getmtime(os.path.join(ROOT, f)))
        except OSError:
            sig.append(0)
    with _lock:
        if _slim["sig"] == sig:
            return _slim["rows"]
    rows, seen = [], set()
    for r in load_leads():
        c = lead_card(r)
        key = _norm(c.get("case"))
        if key and key not in seen:
            seen.add(key)
            rows.append((_norm(" ".join(str(r.get(k) or "") for k in (
                "Address", "owners", "Case #", "people_name", "mailing_address"))), c))
    for f in SLIM_FILES:
        try:
            with open(os.path.join(ROOT, f), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        for r in data if isinstance(data, list) else []:
            if not isinstance(r, dict):
                continue
            c = slim_card(r, f)
            key = _norm(c.get("case"))
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append((_norm(" ".join(str(x or "") for x in (
                c.get("address"), c.get("owners"), c.get("case"), r.get("mail")))), c))
    with _lock:
        _slim.update(sig=sig, rows=rows)
    return rows


def search_leads(q, limit=10):
    """All-token substring match across address / owner / case number, in every lead file. An exact
    case number is found first, so the coach link from any lane opens the right lead."""
    nq = _norm(q)
    if not nq:
        return []
    toks = [t for t in nq.split() if len(t) > 1]
    if not toks:
        return []
    cards = all_cards()
    exact = [c for _, c in cards if _norm(c.get("case")) == nq]
    if exact:
        return exact[:limit]
    hits = []
    for hay, c in cards:
        if all(t in hay for t in toks):
            hits.append(c)
            if len(hits) >= limit:
                break
    return hits


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
    m["wrapup"] = payload.get("wrapup")        # the AI draft and what the caller kept of it
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
                "leads": len(all_cards()),               # every lane the coach can open
                "sessions_dir": SESSIONS,
                "port": PORT,
                "deepgram": bool(deepgram_key()),     # whether a key is set. Never the key.
                "ai": bool(venice_key()),
                "playbook": len(playbook_text()),     # characters of playbook the AI reads; 0 = none found
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
            res = ai_line(b.get("lines"), b.get("lang") or "", b.get("shown") or "", b.get("asked") or [], b.get("days"), b.get("lane") or "")
            res = dict(res or {"line": ""}, ms=int((time.time() - t0) * 1000))
            return self._send(200, res, private=True)

        if p == "/api/wrapup":
            own = ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)
            origin, site = self.headers.get("Origin"), self.headers.get("Sec-Fetch-Site")
            if (self.headers.get("Host") not in own or (origin and origin not in ["http://" + o for o in own])
                    or (site and site != "same-origin")):
                return self._send(403, {"error": "same-origin only"}, private=True)
            b = self._json_body()
            return self._send(200, ai_wrapup(b.get("lines"), str(b.get("lane") or "").lower()) or {"outcome": ""}, private=True)

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