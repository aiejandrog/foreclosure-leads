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
# MODEL. deepseek-v4-1-flash ran the coach on 11-16 and was fast (1.2 s typical) but could not
# improvise: on six moments outside any script (ex-wife on the deed, a wife in surgery, already
# scammed once) it fell back to the same "has anybody shown you what that date costs" question.
# claude-sonnet-5-5 answered the actual thing every time, at 2.1 s typical and 6.0 s worst, and
# about a dollar a call instead of eleven cents. claude-opus-5-5 took over ten seconds and came
# back blank. Alejandro, 11-16: "switch to Sonnet". COACH_AI_MODEL overrides.
AI_MODEL = os.environ.get("COACH_AI_MODEL", "claude-sonnet-5-5")
AI_TIMEOUT = float(os.environ.get("COACH_AI_TIMEOUT", "9"))      # seconds per try; Sonnet's slow lines run 4 to 6
# The call has a path (CALL_SCRIPT_NEPQ.md, the same stages the page shows). The first version of
# this prompt only said "reflect and ask a question", and on test call 11-16 11:34 it circled:
# "sounds like you're feeling overwhelmed, what would a good outcome look like" three times and
# never asked for the appointment. The model now has to say where the call is on the path and
# move it ONE step toward the close on every line.
AI_STEPS = [
    ("opener", "Opener", "Who you are in one breath (Biscayne Solutions Group, and a witty way of saying you are not the bank and not a buyer) and why you called: the case, and how soon the date is."),
    ("situation", "Situation", "Find out what they want to happen with the house before the date."),
    ("problem", "Problem", "Find out whether anyone has shown them what the date costs them, or what is not covered."),
    ("solution", "What they've tried", "Find out the plan they are counting on and what part of it is actually in writing."),
    ("consequence", "Consequence", "Make them picture the morning after the date if the plan is late or not in writing."),
    ("bridge", "Bridge", "Name the gap in one sentence, in their words: what they want, and the piece of the plan that is not covered."),
    ("close", "Book the advisor", "Five minutes with our senior advisor closes that gap. Then the time: later today or first thing tomorrow, once; after that a specific hour."),
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
    "- objection: answer it with THE OBJECTION ANSWER (see the voice section below): a few words that show "
    "you heard them, the plain-words point that makes them think about what they are doing, and then the "
    "why-wouldn't-five-minutes close ending in 'How does that sound?'. Never tell them to 'keep it exactly as "
    "it is'. A lawyer, a plan, the bank helping: say that's good and they should keep doing it, then the "
    "point their plan does not cover, in plain words. Not interested, busy, or a flat no: work it, however "
    "many times they say it (see NEVER FOLD). A scam, who are you: public court record, not the lender, not "
    "buying, no fee. Giving up, 'let them take it': agree, then that the consultation also covers leaving on "
    "their own terms with something in their pocket.\n"
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
    "  4. The trade: five minutes on one side, the house on the other. Which one can they afford to skip.\n"
    "  5. What if: what if he points out one option nobody else has shown them.\n"
    "  6. Fairness: five minutes against the house is a trade worth making, right.\n"
    "  7. Busy: then name the time, later today or first thing tomorrow, and it is five minutes.\n"
    "Stay warm and unbothered while you do it. Never argue, never guilt them, never raise the stakes with "
    "fear. Persistent and friendly, not pushy.\n"
    "- unclear: stay on the same step and ask its question again in fewer words.\n"
    "Pace: NO CIRCLES. People give a stranger about a minute. As soon as the homeowner has said what they want "
    "OR named the plan they are counting on, you have enough: the next line names the gap in it, and the line "
    "after that is the ask. Never two discovery questions in a row once you know their plan. By your third "
    "line at the latest the offer has been made. An objection gets the full objection answer, then the "
    "ask. Never use booked unless they named a time. Never go back to a finished step, and never ask again "
    "something they already answered.\n"
    "TALK LIKE A PERSON, WITH CONTROLLED URGENCY. This is how Alejandro actually talks on these calls, in his "
    "own words from 11-16. These are the standard. Write like this.\n"
    "On a plan with a hole in it (they said a buyer was coming): 'Okay, so by the way, somebody has to be under "
    "contract and the deal has to be closed before that date. And if the sale is in about a week, what happens "
    "that same morning if it's not closed and they decide to back out of the deal? What are you going to do?'\n"
    "On 'I'm busy' or any brush-off: 'So why wouldn't five minutes of your time be worth it, to see what "
    "possible solutions you can find for your property, keep your equity, and do what it is that you want to "
    "do with your property, instead of just going in kind of blindly? In our consultation, all the steps are "
    "going to be laid out in front of you. You have nothing to lose. It's completely free. And if you decide "
    "you don't want to work with us, then we shake hands and we part friends. How does that sound?'\n"
    "What makes those right, and what every line must do:\n"
    "- PLAIN WORDS, DUMMY-PROOF. The person on the phone is stressed and half listening. Use words a "
    "twelve-year-old follows. Never jargon or shorthand: not 'the money side', 'the gap', 'locked in', "
    "'isolate', 'the clock', 'that's the gap', 'loss mitigation', 'reinstatement' without saying what it is. Say the actual "
    "thing: 'who is actually talking to the association about what you owe, so the sale doesn't happen?' If a "
    "line could make them say 'what do you mean?', rewrite it until it cannot.\n"
    "- SPOKEN, NOT CLIPPED. It should sound like a person thinking out loud to a neighbor, with the little "
    "connecting words people really use: 'Okay, so', 'by the way', 'and if', 'right?', 'what are you going "
    "to do?'. A sentence can run on a little. Short choppy fragments like 'Understood.' or 'That's the gap.' "
    "sound like a robot. Never 'keep it exactly as it is', never 'Good, keep him on that'.\n"
    "- MAKE THEM THINK. Controlled urgency is not pressure and not tricks. It is walking them, calmly, through "
    "one real thing that could go wrong with what they are counting on, as a concrete picture with their own "
    "details in it (the buyer backs out, the bank says no a week before, the check shows up late, the lawyer "
    "never called the association), and then asking them what they would do. Let them reach the answer. Never "
    "ask them to rate themselves ('is it really the time, or are you not sure it's worth it?').\n"
    "- CALM AND SURE. No pleading, no rushing, no stacking five reasons. One picture, one question, or one "
    "full objection answer. The calendar (how far off the sale is, in words) is the only pressure you use.\n"
    "TOO MANY WORDS. Call 11-16 18:57, said out loud on the call: 'this is too many words, and words that "
    "are not for me to say.' And: 'disgusting objection handling, like worst case you hang up and you've lost "
    "nothing.' Both are right. An objection is not handled by saying it is free and they have nothing to lose. "
    "It is handled by telling them the ONE thing about their situation they have not thought of. So:\n"
    "TWO KINDS OF LINE, AND BOTH ARE SHORT:\n"
    "1. A QUESTION LINE: react in their words, then one plain scenario question. Under thirty words.\n"
    "2. THE OBJECTION ANSWER: three beats, under forty-five words in all.\n"
    "   a. Agree in three to six words. Not an apology, not 'I hear you'.\n"
    "   b. THE REFRAME: one or two plain sentences that change how they see what they just said. This is the "
    "whole answer. Pick the one that fits:\n"
    "      A lawyer: 'What you're dealing with is a money problem, not a legal problem. Nobody's accusing you "
    "of a crime. Your lawyer can push the date, but every day it gets pushed, interest and fees get added, "
    "and that comes out of your equity.'\n"
    "      It's under control / handled: 'A week out, under control means it's in writing. Is it in writing?'\n"
    "      The bank is helping: 'The bank looking at it and the court selling it are two different offices, "
    "and they don't talk to each other. The sale doesn't wait for the bank.'\n"
    "      Money is coming: 'The court doesn't wait for a check. If it's a day late, the house is already "
    "sold.'\n"
    "      Busy: 'This takes five minutes. The sale takes the house. Which one do you have time for?'\n"
    "      Call me another day: 'Another day could be after the sale. It's in about a week.'\n"
    "      Other companies keep calling: 'They're calling to buy your house cheap. I'm the one call that isn't "
    "trying to take it.'\n"
    "      How do I know you're legit: 'You should check. Our senior advisor has done this over thirty years. "
    "Give him five minutes and judge him yourself.'\n"
    "      What can you even do: name the one thing the advisor does that fits what they want (more time from "
    "the court, a buyer who can close before the date, a new loan through a licensed lender, leaving on their "
    "date with money in hand).\n"
    "      I need to think about it: 'Think about what? You don't have the options yet. Get them first, then "
    "think.'\n"
    "      My wife / husband: 'Then bring her a list, not a guess. Five minutes gets you the list.'\n"
    "      Giving up: 'Leaving is fine. Leaving on a date somebody else picks, with nothing, is the part to "
    "avoid.'\n"
    "   c. The ask, in fresh words, with a time: 'What time are you done today?'\n"
    "   His longer answer (why wouldn't five minutes be worth it, every step laid out, shake hands and part "
    "friends, how does that sound) is for ONE moment per call, when they are close and wavering, and even "
    "then under fifty-five words.\n"
    "LENDERS, BUYERS AND THE OTHER CALLERS: SAY IT WITH A LITTLE WIT. Alejandro, 11-16: 'play around with "
    "the wording when you mention a lender or a buyer, say it witty.' The flat 'I'm not your lender and I'm "
    "not trying to buy your house' is what every caller says, so it lands as nothing. The style is Jon "
    "Lallande's: dry, low-key, a little amused, naming the thing they are already thinking before they say "
    "it. The joke is always on the investors, the banks, or on yourself as one more caller. NEVER on the "
    "homeowner or their situation. One light touch in a line, not a routine, and not in every line.\n"
    "  Saying you are not the bank and not a buyer (use a different one each time, never the flat version):\n"
    "    'I'm not the bank, and I'm not one of those guys trying to buy your house for half of nothing.'\n"
    "    'Before you ask, no, I don't want your house, and no, I'm not collecting for the bank.'\n"
    "    'I'm probably the only call you get this week that isn't trying to buy your place or collect on it.'\n"
    "    'I'm not the bank and I'm not a we-buy-houses guy. I don't even have a sign on a telephone pole.'\n"
    "    'Relax, I don't have a lowball offer in my pocket and I'm not the bank's collections department.'\n"
    "  Naming the other callers before they do:\n"
    "    'I'm guessing your phone's been blowing up with people who suddenly love your house.'\n"
    "    'You've probably had more investors call you this month than friends.'\n"
    "    'I know, everybody and their cousin wants to buy your house right now. I'm not in that line.'\n"
    "  What the senior advisor does with lenders and buyers:\n"
    "    'He knows which lenders actually say yes and which ones just like making you fill out paperwork.'\n"
    "    'If selling's the move, he brings buyers who show up with money, not the ones who show up with stories.'\n"
    "    'He deals with the banks all day so you don't have to sit on hold with them.'\n"
    "    'He can tell you in five minutes if a lender would even look at this, before you lose a month finding out.'\n"
    "  These are examples of the tone. Make your own in the same spirit, and do not reuse one already said on "
    "the call.\n"
    "NO GRADING THEM. Never compliment or rate what they said: no 'that's smart', 'that's good', 'good "
    "question', 'fair enough', 'that's good to hear', 'that makes sense', 'I appreciate that', 'love that'. "
    "A person on a cold call does not hand out grades. Start with their words or with your point. The only "
    "'good' allowed is 'good, keep him' about a lawyer, once.\n"
    "NEVER SAY, anywhere: 'worst case', 'you hang up', 'you've lost nothing', 'nothing to lose' more than "
    "once, 'no pressure', 'I'm not going to push you', 'I'm not picking on you', 'I'm not going to argue', "
    "'I've been talking too much', 'I'll stop talking', 'I'll leave you be', 'shortest call of your week'. "
    "Those are the words of someone who expects a no. You expect a yes.\n"
    "THE CALLERS TALKING TO EACH OTHER. Two people share the caller's side and they sometimes talk about the "
    "script out loud ('too many words', 'what would you have said there', 'let me see what it says', 'it "
    "keeps changing'). That is not the homeowner. Never answer it. If the newest words are that, reply with "
    "TYPE: unclear and repeat your last ask in five words.\n"
    "Other rules that still hold: never open with 'Fair question', 'That's fair', 'I understand', 'I hear "
    "you', 'It sounds like', 'No worries', 'Understood'. No dashes, no lists. Never a discovery question "
    "they already answered. The two-times question (later today or first thing tomorrow) once per call; "
    "after that ask what time they are done today and say the advisor calls them then.\n"
    "The call by section, in this voice (the shape, not lines to repeat):\n"
    "  Opener: Hey, it's Alejandro with Biscayne Solutions Group. Before you ask, no, I don't want your house, "
    "and no, I'm not collecting for the bank. I'm calling because there's a sale date set on your place in "
    "about a week. Did you know about that?\n"
    "  What they want: Okay, so with that date coming up, what is it you actually want to happen with the "
    "house?\n"
    "  The hole in the plan: Okay, so the bank's looking at it. And by the way, has anybody at the bank told "
    "you in writing that the sale is on hold while they look? Because if not, the sale still happens. What "
    "are you going to do if that date gets here first?\n"
    "  A lawyer: Okay, so your lawyer's handling the court part, and that's good, you should keep him. But "
    "who is actually talking to the association about what you owe, so the sale doesn't happen? Because if "
    "nobody is, what happens next week?\n"
    "  The offer: So here's what I'd want for you. Five minutes with our senior advisor, and he lays out "
    "every step you could take with your place. It's free and there's nothing to lose. How does that "
    "sound?\n"
    "  Booked: Okay, five o'clock, so he's going to call you right at five. Do me a favor and save my number "
    "right now, and read it back to me so I know you've got it.\n"
    "- Write every number as a word. Never a dollar amount, payoff, rate or figure about THIS homeowner's own "
    "loan, equity or any offer, and no promise of any outcome. The general figures in the playbook's section "
    "'The numbers' ARE allowed and should be used when they fit, in words, as what usually happens.\n"
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
                      r"take care(?! of)|good ?bye|have a (good|great|nice)|won[\u2019']?t (call|bother)|"
                      r"not (going to|gonna) (call|bother)|i[\u2019']?ll let you", re.I)


_AI_NOTINT = re.compile(r"not (even |really |that |at all )*interested|no interest in|no me interesa|no estoy interesad", re.I)
_AI_CHALLENGED = re.compile(r"not interested in saving|no le interesa salvar", re.I)
AI_CHALLENGE = ("So you're not interested in saving your property, or the equity you've got in it?",
                "¿Entonces no le interesa salvar su propiedad, ni el patrimonio que tiene en ella?")


# THE PLAN FACT. The numbers and the attorney questions are in the playbook, and the model would not
# reach for them on its own (tested 11-16: five plans named, zero facts used). So when the homeowner's
# last words name a plan, the matching fact is handed to the model for that line, once per call each.
# (pattern in what they said, the fact or the question to use, a phrase that shows it was already used)
AI_PLAN_FACTS = [
    (r"not working|no job|unemploy|laid off|lost my job|out of work",
     "nine times out of ten the bank denies a modification when there is no full-time income", r"nine times out of ten the bank"),
    (r"modif|\bmod\b|remodif",
     "a modification can reset the loan to today's rate, and the payment can jump thirty to fifty percent", r"thirty to fifty"),
    (r"bankrupt|chapter (seven|thirteen|7|13)",
     "about seven in ten people who file end up right back in foreclosure, because it pauses the sale and does not fix what "
     "caused it, and the attorney wants two to four thousand up front", r"seven in ten"),
    (r"realtor|real estate agent|listed|listing it|list it|on the market",
     "most realtors are not trained for a foreclosure clock, and most have never asked the bank for more time", r"foreclosure clock|asked the bank for more time"),
    (r"(brother|sister|mom|mother|dad|father|son|daughter|cousin|uncle|aunt|family|friend).{0,40}(money|lend|loan|giv|help|pay)"
     r"|catch(ing)? (it )?up|reinstat|four ?oh ?one|401|retirement",
     "money promised by family falls through or shows up late nine times out of ten", r"falls through|shows up late"),
    (r"postpon|pushed back|push it back|rescheduled|cancel+ed the sale|sale (got|was) cancel",
     "a postponement only delays it, and the balance keeps growing while the situation stays the same", r"only delays"),
]
AI_ATTY_Q = (r"lawyer|attorney|abogad",
             "ask ONE of these, whichever has not been asked: is he a foreclosure specialist or a general attorney; what "
             "exact steps has he taken so far; has he been in touch with the lender to actually stop the sale",
             r"specialist|exact steps|in touch with the lender")


# "BUSY" AND "WHAT DO I GET". Call 11-16 (association case, sale in eight days): after four pushbacks
# she said "I'm kinda busy" and the line was "Gotcha, you're slammed, later today or first thing
# tomorrow?", the same two-times question for the third time. Busy after pushback is a brush-off, not
# a calendar problem. And "you haven't even told me what you're offering" got "he lays the options
# out", which is no answer at all.
_AI_BUSY = re.compile(r"\bbusy\b|do ?n[o'’]?t have (the |any )?time|no time|bad time|in the middle of|not a good time|"
                      r"call (me )?(back|later)|another time|at work|driving|ocupad", re.I)
_AI_VALUE = re.compile(r"what (benefit|good|do you (even )?(do|offer)|are you (even )?offering|can you (even )?do|would i get|"
                       r"do i get|is in it for me|is the point)|have ?n[o'’]?t (even )?told me|how (can|could|would|do) you "
                       r"(even )?help|what (exactly )?is it you do|so what is it", re.I)
_AI_ASKED_TIME = re.compile(r"later today|first thing tomorrow|five minutes", re.I)


# The opener's "not the bank, not a buyer" line is picked here, at random, so two calls in a row do
# not open with the same joke (the model copied its one example word for word, three times of three).
AI_WIT_NOT = (
    "I'm not the bank, and I'm not one of those guys trying to buy your house for half of nothing.",
    "Before you ask, no, I don't want your house, and no, I'm not collecting for the bank.",
    "I'm probably the only call you get this week that isn't trying to buy your place or collect on it.",
    "I'm not the bank and I'm not a we-buy-houses guy. I don't even have a sign on a telephone pole.",
    "Relax, I don't have a lowball offer in my pocket and I'm not the bank's collections department.",
    "I'm not one of those wholesalers trying to buy your property for pennies on the dollar, and I'm not your lender either.",
)
_AI_LOANQ = re.compile(r"\b(loan|refinanc|refi|lender|borrow|financ|mortgage company)", re.I)
_AI_BUYQ = re.compile(r"\b(sell it|selling it|just sell|a buyer|buy it|cash offer|investor)", re.I)


def wit_hint(last_said, given):
    """Which witty wording to use on this line: the opener's not-the-bank line, or the lender / buyer
    line when they just asked about a loan or about selling."""
    used = " ".join(given).lower()
    if not given:
        import random
        return ("\n\nOPENER WORDING FOR THIS CALL. Say who you are, then say you are not the bank and not a buyer in "
                "these words or very close to them: \"" + random.choice(AI_WIT_NOT) + "\" Then why you called and one "
                "question.")
    if _AI_LOANQ.search(last_said) and "?" in last_said and "lenders actually say yes" not in used:
        return ("\n\nTHEY ASKED ABOUT A LOAN OR A LENDER. Answer it first, plainly: we do not lend money ourselves, our "
                "senior advisor works with licensed lenders. Then one dry line about lenders in the witty style (he "
                "knows which ones actually say yes and which ones just like paperwork; he can tell them in five "
                "minutes if a lender would even look at this). Then the ask. Under forty-five words.")
    if _AI_BUYQ.search(last_said) and "show up with" not in used:
        return ("\n\nTHEY BROUGHT UP SELLING OR A BUYER. Pick it up in their words, then one dry line about buyers in the "
                "witty style (if selling is the move, the advisor brings buyers who show up with money, not the ones "
                "who show up with stories), then one plain question or the ask. Under forty-five words.")
    return ""


def plan_hint(last_said, given, when=""):
    """The instruction for this one line when the homeowner just named a plan, pushed back with
    "busy", or asked what they get, or ''."""
    used = " ".join(given).lower()
    clock = (" The sale is set " + when + ": say so, in those words, as the reason it cannot wait.") if when else ""
    if _AI_VALUE.search(last_said) and not re.search(r"ask the court for more time|close in cash|licensed lender", used, re.I):
        return ("\n\nTHEY ASKED WHAT THEY GET. 'He lays out your options' is not an answer. Your SAY names TWO concrete "
                "things the senior advisor does, picked to match what THIS homeowner said they want, from: asking the "
                "court for more time; bringing a buyer who can close in cash before the sale date; replacing the loan "
                "through a licensed lender; getting a modification resubmitted the right way; leaving on a date they "
                "pick with money in hand. Say them plainly, as what he does, not as a promise. Then ask what time "
                "today works. Under forty-five words." + clock +
                " This overrides the one-thought rule for this line.")
    if _AI_VALUE.search(last_said):
        return ("\n\nTHEY ASKED AGAIN WHAT THEY GET. Never say you do not offer anything. Your SAY: the ONE thing the "
                "senior advisor does that fits what they said they want, in one plain sentence, in different words "
                "than before; then that it is five minutes and free; then ask what exact time today they are free." + clock)
    if False:
        return ("\n\nBUSY / NO TIME, and you have already given the full objection answer twice. Do not give it "
                "again. Your SAY, in fresh words: pick up that they are busy in a few words, say why it cannot wait "
                "in one plain sentence, then take the scheduling off them by asking what time they are done today so "
                "the advisor calls them right then." + clock)
    if _AI_BUSY.search(last_said):
        return ("\n\nBUSY / NO TIME. This is pushback, not a calendar problem. Your SAY is the short objection answer: "
                "agree in a few words; then the reframe in one plain sentence (this takes five minutes, the sale "
                "takes the house, or another day could be after the sale); then ask what time they are done today so "
                "the advisor calls them right then. Under forty-five words." + clock + " Never ask whether it is "
                "really the time, and never say they have nothing to lose.")
    for pat, fact, key in AI_PLAN_FACTS:
        if re.search(pat, last_said, re.I) and not re.search(key, used, re.I):
            return ("\n\nPLAN FACT FOR THIS LINE. The homeowner just named a plan. Your SAY, in the spoken 'Okay, so by "
                    "the way' voice: pick up their plan in their words, then this fact as something you see happen "
                    "(numbers kept as words), then a plain 'so what are you going to do if that happens?' kind of "
                    "question with their details in it. The fact: " + fact + ". Say it as what usually happens, not as a promise. This "
                    "overrides the one-thought rule for this line.")
    pat, q, key = AI_ATTY_Q
    if re.search(pat, last_said, re.I) and not re.search(r"money problem|not a legal", used, re.I):
        return ("\n\nATTORNEY, FIRST TIME. Your SAY is these four beats and nothing else, forty words at most, no "
                "compliment and no 'that's good': (1) 'Keep him.' (2) 'But this is a money problem, not a legal "
                "problem. Nobody's accusing you of a crime.' (3) 'Every day he pushes the date, interest and fees "
                "come out of your equity.' (4) ONE question, in words anybody follows: who is actually talking to "
                "the association or the bank about what they owe. Do not add the sale timing, a second question, or "
                "anything after the question. Never say 'the money side'.")
    if re.search(pat, last_said, re.I) and not re.search(key, used, re.I):
        return ("\n\nATTORNEY QUESTION FOR THIS LINE. The homeowner brought up their attorney. Your SAY, in plain words "
                "anybody follows: that's good and they should keep him, he handles the court part; then " + q +
                ". Ask it as a real plain question and say why it matters (if nobody is, the sale still happens). "
                "One question only.")
    return ""


# SAYING THE SAME THING AGAIN. Call 11-16 18:41, his words: "it starts repeating the same shit, use
# different words to get the appointment booked". Counted in that call's AI lines: "why wouldn't
# five minutes be worth it" five times, "later today or first thing tomorrow, which is easier" six,
# "by the way" in nearly every line. The model is told what it has already used on THIS call and may
# not use it again; the objection answer in his full words is good twice, then it has to find new ones.
AI_STOCK = [
    (r"why wouldn.?t five minutes", "why wouldn't five minutes be worth it", 1),
    (r"going in (kind of )?blind", "going in blind", 2),
    (r"shake hands", "shake hands and part friends", 1),
    (r"how does that sound", "How does that sound?", 2),
    (r"nothing to lose", "nothing to lose", 1),
    (r"completely free|it.?s free|\bfree\b", "it's free", 2),
    (r"laid out in front of you", "every step laid out in front of you", 2),
    (r"later today or first thing tomorrow", "later today or first thing tomorrow", 1),
    (r"which (one )?is easier", "which is easier for you", 1),
    (r"not your lender", "the flat 'I'm not your lender and I'm not trying to buy your house'", 1),
    (r"half of nothing", "half of nothing", 1), (r"collecting for the bank", "collecting for the bank", 1),
    (r"blowing up", "your phone's been blowing up", 1), (r"telephone pole", "sign on a telephone pole", 1),
    (r"show up with (money|stories)", "buyers who show up with money, not stories", 1),
    (r"fill out paperwork|filling out paperwork", "lenders who like paperwork", 1),
    (r"what time are you done", "what time are you done today", 1),
    (r"call(s)? you right then|call you then", "so he can call you right then", 1),
    (r"what time (today )?works", "what time works", 1),
    (r"what are you going to do", "what are you going to do", 2),
    (r"by the way", "by the way", 2),
    (r"sitting there", "still sitting there a week out", 1),
    (r"that morning", "that morning", 2),
    (r"here.?s the thing", "here's the thing", 1),
    (r"let me be straight|i.?ll be straight", "let me be straight with you", 1),
    (r"that.?s good,? (you should )?keep (him|them|it)", "that's good, keep him", 1),
    (r"what happens if", "what happens if", 2),
    (r"let me ask you this", "let me ask you this", 1),
    (r"so here.?s what i.?d want for you", "so here's what I'd want for you", 1),
]
AI_CLOSES = (
    "He has a few minutes open this afternoon. What time are you done today?",
    "Tell me a time that works for you and I'll make him fit it.",
    "I can have him call you within the hour. Does that work?",
    "You said you need [what they said they want]. That's the first thing he looks at. When can he call you?",
    "What if he shows you one thing nobody has told you yet? When's a good time for him to call?",
    "Worst case, you hang up knowing more than you do right now. So when's good?",
    "Five minutes against the house. That's a fair trade, right? Give me a time.",
    "Let's just get it on the calendar so it's done. Morning or afternoon?",
    "Is this the best number for him to reach you on? Then give me a time today.",
)


def variety_note(prior):
    """Tells the model which of its own phrases are used up on this call, which opener it just used,
    and which ways of asking for the appointment are still fresh."""
    text = " ".join(prior).lower()
    spent = [label for pat, label, limit in AI_STOCK if len(re.findall(pat, text)) >= limit]
    out = ""
    if spent:
        out += ("\nUSED UP ON THIS CALL. You have already said these, so they may NOT appear in this line, in these "
                "words or close to them: " + "; ".join(spent) + ". Find different words for the same idea.")
    last = prior[-1].lower() if prior else ""
    m = re.match(r"\s*(okay,? so|so|okay|gotcha|right|yeah|i get it|of course)\b", last)
    if m:
        out += "\nYour last line opened with '" + m.group(1) + "'. Open this one differently."
    if sum(1 for x in prior if re.match(r"\s*okay,? so\b", x, re.I)) >= 3:
        out += "\nYou have opened three lines with 'Okay, so'. Do not open with it again on this call."
    if len(re.findall(r"why wouldn.?t five minutes", text)) >= 1:
        out += ("\nYou have given the long 'why wouldn't five minutes' answer already. Not again on this call. Answer what they "
                "just said in ONE or two fresh sentences, then ask for the appointment.")
    asks = len(re.findall(r"later today|first thing tomorrow|what time|when can he|give me a time|on his calendar|on the calendar", text))
    if asks >= 1:
        fresh = [c for c in AI_CLOSES if c.lower()[:22] not in text]
        out += ("\nYou have already asked for the appointment " + str(asks) + " time(s). Ask a NEW way, never the same "
                "words twice. Ways not used yet on this call (adapt one, do not copy it): " + " | ".join(fresh[:5]))
    return out


# HOW WE GET PAID is never made up. On that same call the homeowner said "nobody works for free, we
# have no money to pay you", and the model invented an answer three times ("the licensed lender or
# attorney who does the work pays us a referral"). Nobody told it that. What it may say is what
# Alejandro said on the call himself.
_AI_PAYQ = re.compile(r"work(s|ing)? for free|for free|get paid|what do you (guys )?get|what.?s in it for you|the catch|"
                      r"can.?t (afford|pay)|(no|any|the) (funds|money)|pay (you|your|for)|how much (do|does|is)|your fee|"
                      r"cost me|charge", re.I)
_AI_PAYLIE = re.compile(r"referral|pays? us|paid by|commission|kickback|we (only )?get paid|gets? paid (when|if|unless|by)|"
                        r"doesn.?t get paid|don.?t get paid|our cut|we make (our )?money|lender pays|attorney pays", re.I)
AI_PAY_ANSWER = (
    "\n\nTHEY ASKED HOW WE GET PAID, OR SAID THEY CANNOT PAY. Answer it straight, once, in plain words, with ONLY "
    "these facts and nothing added: you're right that nobody works for free; the five-minute consultation is free and "
    "they never pay anything up front; our senior advisor specializes in cases where people have no money available; "
    "there are programs where both sides come out winning and they do not come out of pocket; how any of that would "
    "work for THEIR case is exactly what he lays out on the call, and they decide. NEVER say who pays us, never say "
    "referral, commission, or that a lender or attorney pays. Then ask for the appointment in fresh words.")


# The words of someone who expects a no (his note, 11-16: "disgusting objection handling"). A line
# that leans on them is asked again; twice, nothing is shown.
_AI_WEAK = re.compile(r"worst case|you hang up|lost nothing|lose nothing|no pressure|not (going to|gonna) push|not picking on|"
                      r"not (going to|gonna) argue|talking too much|stop talking|saying a lot|throwing too much|"
                      r"shortest call", re.I)
# The two callers talking to each other about the script, picked up by the one microphone.
_AI_META = re.compile(r"too many (fucking |damn )?words|what would you have said|what (does|did) (it|that) say|see what (it|that) says|"
                      r"it keeps changing|line for line|words that are not|the (script|coach|computer)\b|"
                      r"answer (like|as if) you.?re answering|something along th(e|ose) lines", re.I)


# PRAISE FILLER. His note, 11-16: "stop throwing in phrases like 'that's smart', that sounds AI as
# fuck." A person on a cold call does not grade the other person's remarks. These are cut out of the
# line itself, so they can never reach the screen whatever the model writes.
_PRAISE_CORE = (
    r"(?:and |but |so |okay,? |yeah,? |hey,? )?"
    r"(?:(?:that(?:'|’)?s|that is|it(?:'|’)?s|you(?:'|’)?re|that was) (?:a |really |actually |totally |very |so )*"
    r"(?:smart|a smart move|a smart thing|good to hear|great to hear|good|nice|great|awesome|perfect|wonderful|a great question|"
    r"a good question|a fair question|a great point|a good point|a fair point|fair|fair enough|real|understandable|"
    r"totally understandable|the right move|the right call|the right thing|a good start|a real start|good thinking)"
    r"(?: to (?:ask|hear|know|do))?(?: of you)?"
    r"|(?:great|good|fair|smart|excellent|awesome|love that|nice|perfect)(?: question| point| call| move| enough)?)")
_AI_PRAISE = re.compile(r"(^|[.!?,])\s*" + _PRAISE_CORE + r"\s*([.!,])\s*", re.I)


def no_praise(line):
    """The line with compliment filler removed. 'check us out, that's smart. Our' becomes 'check us
    out. Our'; a sentence that was only a compliment is dropped."""
    def cut(m):
        before, after = m.group(1), m.group(2)
        if not before:
            return ""                                    # the line opened with it
        if before == "," and after in ".!":
            return ". "                                  # the clause it hung on now ends the sentence
        return before + " "
    out = line
    for _ in range(4):
        new = _AI_PRAISE.sub(cut, out)
        if new == out:
            break
        out = new
    out = " ".join(out.split()).strip(" ,")
    out = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return (out[:1].upper() + out[1:]) if out else line


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
                   "Its 'Never say' list overrides everything else, and where the playbook allows something this "
                   "prompt forbids (its numbers, its options, its attorney questions), the playbook wins.\n" + pb)
    lane = str(lane or "").lower()
    if lane in AI_LANES:
        system += "\n\nTHIS LEAD: " + AI_LANES[lane]
    when = sale_in_words(days) if lane != "filing" else ""
    if when:
        system += ("\n\nCourt calendar for this homeowner: the sale is set " + when + ". You may say that in those "
                   "words when it helps the line. Never say a date.")
    shown = " ".join(str(shown or "").split())[:320]
    prior = [" ".join(str(x or "").split())[:320] for x in (asked or [])[-14:] if str(x or "").strip()]
    if prior:
        system += ("\nLines you already gave on this call, oldest first:\n" + "\n".join("- " + x for x in prior[-6:])
                   + "\nNever repeat one of them or ask the same thing in other words. The new line is a later step.")
        system += variety_note(prior)
    if shown:
        system += ("\nThe caller's screen currently shows: \"" + shown + "\". The caller may have just read it "
                   "out. Write the line that comes after the homeowner's answer to it.")
    said = " ".join(r.split(": ", 1)[1] for r in rows[-6:])

    def ask(sys_text):
        body = {"model": AI_MODEL, "max_tokens": 420, "temperature": 0.5, "stream": False,
                "messages": [{"role": "system", "content": sys_text}, {"role": "user", "content": "\n".join(rows)}],
                "venice_parameters": {"include_venice_system_prompt": False, "disable_thinking": True,
                                      "strip_thinking_response": True}}
        import urllib.request
        out = ""
        for _try in range(2):            # one retry: a dropped request must not leave the box blank
            try:
                req = urllib.request.Request(AI_URL, data=json.dumps(body).encode("utf-8"),
                                             headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as r:
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
        if not line or line.upper().rstrip(".") == "SKIP" or len(line) > 640 or bad:
            return {}
        line = no_praise(line)
        res = {"line": line, "step": step, "of": len(AI_STEPS), "type": kind, "heard": heard[:200]}
        if step:
            res["n"] = AI_STEP_IDS.index(step) + 1
            res["name"] = AI_STEPS[res["n"] - 1][1]
        return res

    last_said = rows[-1].split(": ", 1)[1] if rows else ""
    if _AI_META.search(last_said):
        return {}                      # the callers are talking about the script: keep what is on screen
    if not rows[-1].startswith("CALLER"):
        _ph = plan_hint(last_said, prior + [shown], when)
        system += _ph or wit_hint(last_said, [x for x in prior + [shown] if x])
        if _AI_PAYQ.search(last_said):
            system += AI_PAY_ANSWER
    res = ask(system)
    # TOO LONG. His target is forty-five words for an objection answer. One that runs past fifty-five is
    # asked for again, shorter; the shorter of the two is shown. The pay answer is long on purpose.
    if (res and res.get("type") in ("objection", "question", "answer") and len(res.get("line", "").split()) > 55
            and not _AI_PAYQ.search(last_said)):
        n_words = len(res["line"].split())
        short = ask(system + "\n\nYour last answer was " + str(n_words) + " words: \"" + res["line"] + "\" That is too "
                    "long to say out loud. Say the same point in under forty words: keep the one sentence that matters "
                    "and the one question, cut everything else. No second question, no extra reason.")
        if short and short.get("line") and len(short["line"].split()) < n_words and not folds(short):
            res = short
    if res and _AI_WEAK.search(res.get("line", "")) and res.get("type") != "stop":
        res = ask(system + "\n\nYour last answer used the words of someone who expects a no (worst case, hang up, "
                  "lost nothing, no pressure, not going to push, talking too much). Never. Give the short objection "
                  "answer: agree in a few words, ONE reframe about their situation, then the ask with a time.")
        if res and _AI_WEAK.search(res.get("line", "")):
            return {}
    # A made-up answer about how we get paid is never shown: asked again with the facts, then dropped.
    if res and _AI_PAYLIE.search(res.get("line", "")):
        res = ask(system + AI_PAY_ANSWER + " Your last answer invented how the company gets paid. That is forbidden.")
        if res and _AI_PAYLIE.search(res.get("line", "")):
            return {}
    # THE CHALLENGE, guaranteed (Alex, 11-16: "why didn't you ask, so you're not interested in saving
    # your property, your equity?"). The first "not interested" of a call gets his question, word for
    # word, whatever the model wrote. Later ones get the model's other angles. A stop request is
    # never challenged.
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