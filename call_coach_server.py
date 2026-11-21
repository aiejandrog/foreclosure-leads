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
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
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
_meta_lock = threading.Lock()     # every read-modify-write of a session's meta.json (audio, checkpoint, end)
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
# TWO SPEEDS. Sonnet takes about 2.7 s a line; DeepSeek about 1.3 s. When the server already knows what
# this moment is (a booking, a named plan, busy, "what do I get", a lawyer, a loan, how we get paid, the
# opener) it hands the model the content, and the fast model only has to say it: tested 11-16, it does
# that as well as Sonnet. Sonnet is kept for the lines nobody scripted, where judgment is the job (a
# wife in surgery, an ex-wife on the deed). COACH_AI_FAST_MODEL="" turns the fast lane off.
AI_FAST_MODEL = os.environ.get("COACH_AI_FAST_MODEL", "deepseek-v4-1-flash")
AI_TIMEOUT = float(os.environ.get("COACH_AI_TIMEOUT", "9"))      # seconds per try; Sonnet's slow lines run 4 to 6
# ONE CLOCK FOR THE WHOLE LINE (forensics 11-18). Every try had its own 9 s and a line could be asked up to
# four times (the first ask, then too long / weak words / made-up pay answer / a goodbye), each with a retry:
# worst case well past half a minute, while the page holds its "asking" flag and asks nothing else. Shown
# lines came back in 1.0 s typical and 4.4 s worst, so anything still out at 8 s is a moment that has passed.
# Every try and every re-ask now draws on one budget; a re-ask that cannot fit is skipped and the line is
# fixed in code (trimmed) or dropped.
AI_BUDGET = float(os.environ.get("COACH_AI_BUDGET", "8"))
# REPLY SIZE (11-19). The reply is four short fields; the longest real line (the pay answer, about a hundred and
# ten words) is under two hundred tokens. 420 only let a runaway reply run on for seconds. A reply that does hit
# the cap is cut back to whole sentences (ask(), finish_reason "length").
AI_MAX_TOKENS = int(os.environ.get("COACH_AI_MAX_TOKENS", "260"))
# Venice's prompt_cache_key is a routing hint toward a server that already holds the cached prefix. Untested
# against the live API (it would cost money to test), so it is off unless COACH_AI_CACHE_KEY is set.
AI_CACHE_KEY = os.environ.get("COACH_AI_CACHE_KEY", "")
AI_REASK_MIN = 2.0          # seconds of budget a re-ask needs to be worth sending
AI_TRY_MIN = 0.8            # below this a try cannot come back in time
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
    "- time: step booked. If they gave a day but no hour ('call me tomorrow'), YOU name the hour, do not ask "
    "for one. Then tell them to save the number.\n"
    "CHECKMATE. Never put the ball in their court and let them decide things; they just don't answer. So:\n"
    "- YOU PICK THE TIME. Never 'what time are you done?', 'morning or after lunch?', 'what hour is best?', "
    "'tell me a time'. Name one specific hour and ask if it works: 'How does ten tomorrow morning sound?' "
    "'I've got him at four today. Does that work?' If they said tomorrow, it is ten tomorrow morning; if they "
    "said today or later, it is four this afternoon; if they said nothing, it is ten tomorrow morning. If "
    "they push back on the hour, name one other hour.\n"
    "- THE MOMENT THEY SAY WHAT THEY WANT AND IT IS SOMETHING THE ADVISOR HANDLES (an offer or a buyer, more time, a "
    "loan, a way to keep it, a way out with money), that is the close. No more questions about their "
    "situation, no explaining. One short sentence that it is exactly what he does, then your hour: 'An offer "
    "is exactly what he brings. How does ten tomorrow morning sound?'\n"
    "- A yes, an okay, a 'fine', a 'sure', a 'call me tomorrow' is a YES. Never answer a yes with a reason "
    "or an objection answer. Book it: the hour, then 'save my number and read it back to me'.\n"
    "- A closing line is twenty-five words or fewer.\n"
    "- stop: confirm they are off the list and say goodbye. This is the only way a call ends.\n"
    "NEVER FOLD. A person who is still on the phone is still deciding, and 'not interested' is a reflex, not a "
    "decision; a plain 'No' is not a request to be taken off the list. Unless TYPE is stop you never say goodbye, "
    "never offer to leave them be or let them go, never say 'take care', and never mention taking them off a list. "
    "The FIRST time they say they are not interested, the line is always the challenge, said calm and curious, as "
    "a real question: 'So you're not interested in saving your property, or the equity you've got in it?' After "
    "that, a new angle each time (the angles come with the line when it is that moment), then back to the ask.\n"
    "Stay warm and unbothered while you do it. Never argue, never guilt them, never raise the stakes with "
    "fear. Persistent and friendly, not pushy.\n"
    "- unclear: stay on the same step and ask its question again in fewer words.\n"
    "Pace: NO CIRCLES. People give a stranger about a minute. As soon as the homeowner has said what they want "
    "OR named the plan they are counting on, you have enough: the next line names the gap in it, and the line "
    "after that is the ask. Never two discovery questions in a row once you know their plan. By your third "
    "line at the latest the offer has been made. An objection gets the full objection answer, then the "
    "ask. Never use booked unless they named a time. Never go back to a finished step, and never ask again "
    "something they already answered.\n"
    "TALK LIKE A PERSON, WITH CONTROLLED URGENCY. How Alejandro actually talks on these calls, in his own words "
    "from 11-16, is the standard (one of his lines comes with the line when it is that moment).\n"
    "What makes his lines right, and what every line must do:\n"
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
    "TOO MANY WORDS. An objection is not handled by saying it is free and they have nothing to lose. "
    "It is handled by telling them the ONE thing about their situation they have not thought of. So:\n"
    "TWO KINDS OF LINE, AND BOTH ARE SHORT:\n"
    "1. A QUESTION LINE: react in their words, then one plain scenario question. Under thirty words.\n"
    "2. THE OBJECTION ANSWER: three beats, under forty-five words in all.\n"
    "   a. Agree in three to six words. Not an apology, not 'I hear you'.\n"
    "   b. THE REFRAME: one or two plain sentences that change how they see what they just said. This is the "
    "whole answer. The reframe that fits comes with the line when it is that moment.\n"
    "   c. The ask, in fresh words, with a time: 'What time are you done today?'\n"
    "   His longer answer (why wouldn't five minutes be worth it, every step laid out, shake hands and part "
    "friends, how does that sound) is for ONE moment per call, when they are close and wavering, and even "
    "then under fifty-five words.\n"
    "LENDERS, BUYERS AND THE OTHER CALLERS: when you mention a lender or a buyer, say it with a little dry wit, "
    "the joke on the investors, the banks or yourself, never on the homeowner. Never the flat 'I'm not your "
    "lender and I'm not trying to buy your house'.\n"
    "NO GRADING THEM. Never compliment or rate what they said ('that's smart', 'good question', 'fair enough', "
    "'that makes sense', 'I appreciate that'). Start with their words or with your point. The only 'good' "
    "allowed is 'good, keep him' about a lawyer, once.\n"
    "NEVER SAY, anywhere: 'worst case', 'you hang up', 'you've lost nothing', 'nothing to lose' more than "
    "once, 'no pressure', 'I'm not going to push you', 'I'm not picking on you', 'I'm not going to argue', "
    "'I've been talking too much', 'I'll stop talking', 'I'll leave you be', 'shortest call of your week'. "
    "Those are the words of someone who expects a no. You expect a yes.\n"
    "THE CALLERS TALKING TO EACH OTHER about the script ('too many words', 'what would you have said there') is "
    "not the homeowner. Never answer it: reply TYPE: unclear and repeat your last ask in five words.\n"
    "Other rules that still hold: never open with 'Fair question', 'That's fair', 'I understand', 'I hear "
    "you', 'It sounds like', 'No worries', 'Understood'. No dashes, no lists. Never a discovery question "
    "they already answered. The two-times question (later today or first thing tomorrow) once per call; "
    "after that ask what time they are done today and say the advisor calls them then.\n"
    "- Write every number as a word. Never a dollar amount, payoff, rate or figure about THIS homeowner's own "
    "loan, equity or any offer, and no promise of any outcome. The general figures in the playbook's section "
    "'The numbers' ARE allowed and should be used when they fit, in words, as what usually happens.\n"
    "Reply in exactly this form and nothing else:\n"
    "HEARD: <the homeowner's most recent words, copied from the transcript, twelve words at most>\n"
    "TYPE: <answer, question, objection, time, stop or unclear>\n"
    "STEP: <one step id from the path>\n"
    "SAY: <the line>")
# SITUATIONAL RULES (11-19). These rode in AI_SYSTEM on every line; they are only needed at certain moments, so
# situational() hands them over, in the user message, when the transcript says it is that moment.
AI_SIT_ANGLES = (
    "NEVER FOLD, THE ANGLES. After the first challenge, each time they repeat it use one not used yet on this call. Here are "
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
    "  7. Busy: then name the time, later today or first thing tomorrow, and it is five minutes.\n")
AI_SIT_REFRAMES = (
    "THE REFRAMES (beat b of the objection answer). Pick the one that fits:\n"
    "      A lawyer: 'What you're dealing with is a money problem, not a legal problem. Nobody's accusing you "
    "of a crime. Your lawyer can push the date, but every day it gets pushed, interest and fees get added, "
    "and that comes out of whatever you've got in the place.'\n"
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
    "avoid.'\n")
AI_SIT_WIT = (
    ""
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
    "the call.\n")
AI_SIT_SHAPE = (
    ""
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
    "right now, and read it back to me so I know you've got it.\n")
AI_SIT_PLANHOLE = (
    "HOW ALEJANDRO SAYS IT, on a plan with a hole in it:\n"
    "On a plan with a hole in it (they said a buyer was coming): 'Okay, so by the way, somebody has to be under "
    "contract and the deal has to be closed before that date. And if the sale is in about a week, what happens "
    "that same morning if it's not closed and they decide to back out of the deal? What are you going to do?'\n")
AI_SIT_BUSYVOICE = (
    "HOW ALEJANDRO SAYS IT, on a brush-off (ONE moment per call, when they are close and wavering):\n"
    "On 'I'm busy' or any brush-off: 'So why wouldn't five minutes of your time be worth it, to see what "
    "possible solutions you can find for your property, keep your equity, and do what it is that you want to "
    "do with your property, instead of just going in kind of blindly? In our consultation, all the steps are "
    "going to be laid out in front of you. You have nothing to lose. It's completely free. And if you decide "
    "you don't want to work with us, then we shake hands and we part friends. How does that sound?'\n")
_AI_BAD = re.compile(r"[\d$]")

# DIGITS THAT ARE NOT FIGURES. The no-number rule exists so the model never invents an amount, a rate or a
# date. It was enforced as "any digit kills the line", so "five minutes" written as "5 minutes", "24/7" or
# "1st" threw away an otherwise good answer and left the box on the old line. Those few forms are spelled
# out here before the check; anything else with a digit (an amount, a percent, a year) still never shows.
_SMALL = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")
_ORD = {"1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth", "5th": "fifth", "6th": "sixth",
        "7th": "seventh", "8th": "eighth", "9th": "ninth", "10th": "tenth"}


def spell_digits(line):
    """`line` with the harmless digit forms in words: 24/7, ordinals 1st-10th, and a lone 0-12 followed by a
    word ("5 minutes", "chapter 7 or"). Money, percents, decimals, times and longer numbers are left alone,
    so the digit check still catches them."""
    s = re.sub(r"(?<![\w$])24\s*/\s*7(?![\w%])", "around the clock", str(line or ""))
    s = re.sub(r"(?<![\w$.,])(10th|[1-9](?:st|nd|rd|th))\b", lambda m: _ORD.get(m.group(1).lower(), m.group(1)), s, flags=re.I)
    s = re.sub(r"(?<![\w$.,:/])(1[0-2]|[0-9])(?=(?:\s+[A-Za-z])|[,.?!]?$)(?!\s*(?:%|percent|k\b|grand|thousand|hundred|million|dollars?|bucks|am\b|pm\b|a\.m|p\.m|o.?clock))",
               lambda m: _SMALL[int(m.group(1))], s)
    return s


def _wordset(t):
    return set(w for w in _words(t) if len(w) > 2)


def near_dup(line, prior):
    """The earlier line `line` repeats, or ''. Same words in the same order, or most of the same words
    (forensics 11-18: 18 of 165 shown lines repeated an earlier line of the same call)."""
    a = " ".join(_words(line))
    ws = _wordset(line)
    for p in prior:
        if not p:
            continue
        if a and a == " ".join(_words(p)):
            return p
        pw = _wordset(p)
        if len(ws) >= 5 and len(pw) >= 5 and len(ws & pw) / float(len(ws | pw)) >= 0.6:
            return p
    return ""


def trim_line(line, limit=45):
    """A line cut to whole sentences within `limit` words: the opening sentences that fit, plus the closing
    question (the ask) when the line ends on one. Used when a too-long line cannot be asked again in time.
    Forensics 11-18: 23 of 165 shown lines ran past fifty-five words, the longest 114."""
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", str(line or "").strip()) if x.strip()]
    if len(" ".join(sents).split()) <= limit or len(sents) < 2:
        return line
    tail = sents[-1] if sents[-1].endswith("?") else ""
    body = sents[:-1] if tail else sents
    room = limit - len(tail.split())
    keep = []
    for s in body:
        if len(" ".join(keep + [s]).split()) > room:
            break
        keep.append(s)
    if not keep and not tail:
        return line
    if not keep:
        keep = [body[0]] if len((body[0] + " " + tail).split()) <= limit + 10 else []
    out = " ".join(keep + ([tail] if tail else []))
    return out or line


AI_SPLIT ="\n\n<<<THIS CALL>>>\n"       # everything before it is identical on every request (cacheable)

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


# THE PLAYBOOK, SPLIT (11-19). The whole note (about 8,000 tokens) used to ride on every line, on top of a
# 4,700-token rule block: about 13,000 tokens of system prompt per request. Now only a CORE is always sent (what
# the call sells, the never-say list, how the caller talks, the questions and the close), as one unchanging
# system prefix so the provider can cache it. The rest is SITUATIONAL: the sections, or the single bullets, that
# fit what the homeowner just said are picked by plain keyword matching on the last few transcript lines (no
# extra model call) and ride in the user message for that one line. A section he adds under a heading this code
# does not know goes in the core, so an edit in Obsidian is never silently left out.
PB_CORE = ("what this call sells", "never say", "how the caller talks", "questions that open them up")
SIT_BUDGET = int(os.environ.get("COACH_SIT_BUDGET", "2800"))      # characters of situational text per line, ~700 tokens
# Paragraphs inside a core section that are only for one moment, by their opening words, and the topics that
# bring them back for that line.
PB_CORE_PULL = (
    (r"who we are, if asked", ("scam", "early", "value", "lender")),
    (r"probes when", ("situation", "no")),
    (r"the magic button", ("situation",)),
    (r"after a yes", ("yes", "time")),
    (r"the advisor call goes on the calendar", ("yes", "time")),
)

# What the homeowner may be talking about. t = matched on their recent words; h = matched on a playbook item's
# opening words (defaults to t). Plain regexes, lower-case text.
SIT_TOPICS = {
    "attorney": (r"lawyer|attorney|abogad|legal", None),
    "bank": (r"\bbank|lender|modif|loss mit|working with (me|us)|in review|forbearance|mortgage company", None),
    "handled": (r"handl|taken care|take care of it|under control|figured out|got a plan|have a plan|my plan|figure it out|"
                r"we'?re (good|fine|ok)|all set", r"handled|have a plan|figure it out"),
    "money": (r"money (is )?coming|refund|settlement|lend(ing)? (me|us|it)|brother|sister|retirement|401|check (is )?coming|"
              r"catch (it |them )?up|reinstat|get the money|pay it off", r"money is coming|refund|catching it up|reinstat|lending"),
    "notint": (r"not (even |really |that |at all )*interested|no interest|no me interesa|no estoy interesad", r"not interested"),
    "pay": (r"for free|get paid|out of it|pay you|no money|afford|your fee|cost me|charge|the catch", r"works for free|get out of it"),
    "busy": (r"\bbusy\b|call (me )?(back|later)|bad time|no time|in the middle of|another day|driving|at work|not a good time",
             r"busy|call me later|another day"),
    "think": (r"think about|thinking about|sleep on", r"think about"),
    "spouse": (r"\bwife|husband|\bmom\b|mother|\bdad\b|father|spouse|partner|esposa|esposo|my family", r"wife|husband|mother"),
    "scam": (r"scam|who (is|are) (this|you)|how did you get|legit|fraud|real company|trust you|who do you work", r"scam|who are you|legit"),
    "others": (r"(other|three|bunch of|lot of|so many) (of you|people|companies|investors|callers|calls)|keep calling|you people|"
               r"everybody (calls|is calling)", r"three of you|other callers|other companies"),
    "wronghouse": (r"wrong (house|person|number|address)|not in foreclosure|no foreclosure|we'?re not behind", r"wrong house"),
    "postponed": (r"postpon|reset|always gets|pushed back|resched|got cancel|got moved", r"postpone"),
    "bankruptcy": (r"bankrupt|chapter (7|13|seven|thirteen)", r"bankrupt"),
    "giveup": (r"too late|nothing (anybody|you|anyone) can|just take it|i'?m done|don'?t care|let it go|letting it go|walk away|"
               r"give up|giving up|relive|whole life", r"too late|take it|letting it go|whole life|i'?m done|giving up"),
    "notselling": (r"not selling|won'?t sell|never sell|staying no matter|not going anywhere", r"not selling|staying no matter"),
    "realtor": (r"realtor|listed|\bagent\b|on the market|listing", r"realtor|listed"),
    "offer": (r"\boffer|buyer|investor|cash|sell (it|the house|the place)|selling", r"offer|cash sale|buyer"),
    "business": (r"none of your business|your business|private matter", r"none of your business"),
    "fear": (r"medicaid|benefits|probate|\bheirs?\b|divorce|estate|social security|\bssi\b", r"medicaid|specific fear"),
    "decide": (r"no decisions|not making any decision|not deciding|staying no matter", r"not making any decisions"),
    "brushoff": (r"handl|taken care|under control|figure|none of your business|not interested|no thanks|we'?re good|all set|"
                 r"don'?t need", r"brush-off"),
    "no": (r"^\W*no\b|i said no|not right now|no thanks|nope|do ?n.?t think so|do not think so|not for me", None),
    "lender": (r"\b(loan|refinanc|refi|lender|borrow|financ)", None),
    "value": (r"what (can|do|would|could) you (even |guys )?(do|offer)|how (can|could|would) you help|what (benefit|good)|"
              r"what('?s| is) (this|it|that) (all )?about|what do you (guys )?do|what (are|would) my options|options", None),
    "notice": (r"letter|notice|lawsuit|served|papers|summons|lis pendens", None),
    "yes": (r"^\W*(yes|yeah|yep|yup|okay|ok|sure|fine|alright|all right|sounds good|that works)\b", None),
    "time": (r"\b(tomorrow|today|tonight|this (afternoon|evening|morning)|later|morning|afternoon|evening|o'?clock|"
             r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", None),
}
SIT_OBJ = ("attorney", "bank", "handled", "money", "notint", "pay", "busy", "think", "spouse", "scam", "others", "wronghouse",
           "postponed", "bankruptcy", "giveup", "notselling", "realtor", "business", "fear", "decide", "brushoff", "no")
_SIT_T = {k: re.compile(v[0], re.I | re.M) for k, v in SIT_TOPICS.items()}
_SIT_H = {k: re.compile(v[1] or v[0], re.I) for k, v in SIT_TOPICS.items()}

# How each known section is picked. mode: whole (all of it), items (only the bullets or paragraphs whose opening
# words match a live topic; a matching paragraph brings its bullets), groups (label paragraph plus its bullets,
# picked by the label). topics: the live topics that pull in a "whole" section.
PB_SIT = (
    ("objections: cioc", "whole", SIT_OBJ),
    ("the objections and the move for each", "items", ()),
    ("questions that shut them down", "whole", ("early", "offer")),
    ("what the advisor's options are", "whole", ("value", "offer", "giveup", "notselling")),
    ("what is true about a florida foreclosure", "whole", ("scam", "wronghouse", "postponed", "bank", "bankruptcy", "notice")),
    ("jesse's own lines", "groups", ()),
    ("more objections and moves", "items", ()),
    ("the numbers", "whole", ("bank", "money", "bankruptcy", "postponed", "realtor")),
    ("track record", "whole", ("notint", "handled", "giveup", "decide")),
    ("the buyer-side options", "whole", ("value", "offer", "notselling")),
    ("witty ways", "whole", ("early", "scam", "lender", "offer", "others")),
    ("sharp lines", "whole", ("brushed2",)),
    ("jesse closing a client", "groups", ()),
)
# Jesse's lines are grouped under short labels; which live topics pull each label in.
PB_GROUPS = (
    (r"^posture", ("notint", "brushoff", "giveup", "no", "business")),
    (r"^opening", ("early", "scam")),
    (r"finding out what they want", ("early", "situation")),
    (r"^an attorney", ("attorney",)),
    (r"bank or loss", ("bank",)),
    (r"^not interested", ("notint",)),
    (r"moving to the senior", SIT_OBJ + ("value", "yes", "offer")),
    (r"asking for the time", ("yes", "time", "busy")),
    (r"explains the options", ("value", "offer")),
    # the Giselle-call groups (2026-11-20): labels inside "Jesse closing a client"
    (r"^an attorney already answered", ("attorney",)),
    (r"paying a law firm", ("attorney", "pay", "money", "think")),
    (r"handing them the decision", ("think", "decide", "no", "notint", "spouse")),
    (r"a money problem", ("bankruptcy", "bank", "fear")),
    (r"^the protector", ("handled", "money", "spouse", "scam", "others", "notint")),
    (r"^after the yes", ("yes", "time")),
)
# The code-side rule blocks that are situational too, and the topics that bring each one.
AI_SIT_RULES = (
    ("angles", ("notint", "brushoff", "busy", "no")),
    ("reframes", SIT_OBJ),
    ("wit", ("early", "scam", "lender", "offer", "others")),
    ("shape", ("early",)),
    ("planhole", ("handled", "money", "bank", "realtor", "offer", "attorney")),
    ("busyvoice", ("busy", "think")),
)


def _pb_sections(text):
    """[(heading, whole text with its '## ' line)] in note order; text before the first heading has heading ''."""
    out = []
    for i, chunk in enumerate(re.split(r"(?m)^(?=## )", text or "")):
        if not chunk.strip():
            continue
        head = chunk.splitlines()[0][3:].strip() if chunk.startswith("## ") else ""
        out.append((head, chunk.strip()))
    return out


def _pb_spec(head):
    h = head.lower()
    for prefix, mode, topics in PB_SIT:
        if h.startswith(prefix):
            return mode, topics
    return None


def _pb_items(body):
    """A section body cut into items: [(kind, text, label_index)], kind 'para' or 'bullet'. Numbered lines and
    indented continuation lines stay with the item above them; a bullet carries the index of the paragraph it
    sits under (or -1)."""
    items, cur, label = [], None, -1
    for ln in body.splitlines()[1:]:
        if not ln.strip():
            if cur and cur[0] == "para":
                items.append(cur)
                cur = None
            continue
        if ln.startswith("- "):
            if cur:
                items.append(cur)
            if items and items[-1][0] == "para":
                label = len(items) - 1
            cur = ("bullet", ln, label)
        elif cur and (ln.startswith(" ") or re.match(r"\d+\.\s", ln) and cur[0] == "para"):
            cur = (cur[0], cur[1] + "\n" + ln, cur[2])
        elif cur and cur[0] == "para":
            cur = (cur[0], cur[1] + "\n" + ln, cur[2])
        else:
            if cur:
                items.append(cur)
            label = -1
            cur = ("para", ln, -1)
    if cur:
        items.append(cur)
    return items


_pb_cache = {"src": None}


def playbook_parts():
    """The playbook as {"core": str, "sit": [(head, mode, topics, body)], "never": [phrase], "nums": [(words, kind)]},
    rebuilt only when the note changes."""
    text = playbook_text()
    if _pb_cache["src"] == text:
        return _pb_cache
    core, sit, never, nums = [], [], [], []
    heads = [h.lower() for h, _ in _pb_sections(text) if h]
    for head, body in _pb_sections(text):
        spec = _pb_spec(head) if head else None
        if spec is None:
            # a few paragraphs of a core section are only for one moment (the probes, the magic button, what to
            # say after a yes); they go situational and the rest of the section stays in the core
            keep, pulled = [], []
            for para in re.split(r"\n\s*\n", body):
                hit = next((t for rx, t in PB_CORE_PULL if re.match(rx, para.strip(), re.I)), None)
                (pulled.append((para, hit)) if hit else keep.append(para))
            core.append("\n\n".join(keep))
            for para, topics in pulled:
                sit.append((head + " (more)", "whole", topics, para.strip()))
        else:
            sit.append((head, spec[0], spec[1], body))
        if head.lower().startswith("never say"):
            never = _never_phrases(body, heads)
        if head.lower().startswith("the numbers"):
            nums = _money_phrases(body)
    _pb_cache.update(src=text, core="\n\n".join(core), sit=sit, never=never, nums=nums)
    return _pb_cache


def live_topics(rows, prior):
    """The topics the last few homeowner lines touch, plus call-state topics: early (first lines of the call),
    situation (still finding out what they want), brushed2 (waved off twice already). Pure local regex."""
    them = [r.split(": ", 1)[1] for r in rows if not r.startswith("CALLER")]
    recent = "\n".join(them[-4:]).lower()
    last = them[-1].lower() if them else ""
    on = set(k for k, rx in _SIT_T.items() if k not in ("yes", "no", "time") and rx.search(recent))
    for k in ("yes", "no", "time"):
        if _SIT_T[k].search(last):
            on.add(k)
    if len(prior) <= 1:
        on.add("early")
    if len(prior) <= 3:
        on.add("situation")
    brush = _SIT_T["brushoff"]
    if sum(1 for t in them if brush.search(t) or _SIT_T["no"].search(t) or _SIT_T["busy"].search(t)) >= 2:
        on.add("brushed2")
    on.add("always")
    return on


def situational(rows, prior, pb=None):
    """The situational rules and playbook pieces for this line, as one text block ('' when nothing fits), and the
    list of names picked (for the log and the tests)."""
    on = live_topics(rows, prior)
    hot = [k for k in on if k in _SIT_H and k not in ("yes", "no", "time")]
    parts = pb if pb is not None else playbook_parts()
    code = {"angles": AI_SIT_ANGLES, "wit": AI_SIT_WIT, "shape": AI_SIT_SHAPE,
            "planhole": AI_SIT_PLANHOLE, "busyvoice": AI_SIT_BUSYVOICE}
    # Candidates in priority order: the code rules, then the single playbook items that match, then whole
    # sections. They are taken in that order until SIT_BUDGET characters; a piece that does not fit is skipped.
    first_pass, whole = [], []
    for name, topics in AI_SIT_RULES:
        if not on.intersection(topics) or name == "wit":
            continue
        if name == "reframes":
            # only the reframe lines whose label fits what they said, not all twelve
            ls = AI_SIT_REFRAMES.split("\n")
            pick, cur = [], None
            for ln in ls[1:]:
                if ln.startswith("      ") and not ln.startswith("       "):
                    cur = any(_SIT_H[k].search(ln[:60]) for k in hot) or ("value" in on and "even do" in ln[:60])
                if cur:
                    pick.append(ln)
            if pick:
                first_pass.append(("reframes", ls[0] + "\n" + "\n".join(pick)))
            continue
        first_pass.append((name, code[name].strip()))
    for head, mode, topics, body in parts["sit"]:
        if mode == "whole":
            if on.intersection(topics):
                whole.append((head.lower()[:24], body))
            continue
        items = _pb_items(body)
        keep = set()
        for i, (kind, text, label) in enumerate(items):
            q = re.match(r'^-\s*["“]([^"”]+)', text)
            first = q.group(1) if q else re.split(r"[:.]", text, 1)[0][:90]
            if mode == "groups":
                if kind == "para":
                    want = next((t for rx, t in PB_GROUPS if re.search(rx, first, re.I)), ())
                    if on.intersection(want):
                        keep.add(i)
                        keep.update(j for j, it in enumerate(items) if it[0] == "bullet" and it[2] == i)
                continue
            if any(_SIT_H[k].search(first) for k in hot):
                keep.add(i)
                if kind == "para":
                    keep.update(j for j, it in enumerate(items) if it[0] == "bullet" and it[2] == i)
                elif label >= 0:
                    keep.add(label)
        if keep:
            first_pass.append((head.lower()[:24], body.splitlines()[0] + "\n" + "\n".join(items[i][1] for i in sorted(keep))))
    has_wit = any(n.startswith("witty") for n, _ in whole)
    if not has_wit and on.intersection(dict(AI_SIT_RULES)["wit"]):
        whole.append(("wit", AI_SIT_WIT.strip()))          # the code's own wit lines when the note has none
    blocks, picked, room = [], [], SIT_BUDGET
    for name, text in first_pass + whole:
        if len(text) + 2 <= room:
            blocks.append(text)
            picked.append(name)
            room -= len(text) + 2
    return "\n\n".join(blocks), picked


# NEVER SAY, ENFORCED (11-19). The playbook's "Never say" list was an instruction only. A line that breaks it
# is now asked again once with the reason, and dropped if it still breaks it (the digit rule's pattern). Two
# sources: the quoted phrases under the note's "## Never say" heading, and fixed rules here for the worst
# cases. The rules are written to the note's own wording, so what it ALLOWS still passes: "ask the court for
# more time", "options to keep the house", "I'm not from the bank", "he can't guarantee anything", and the
# general figures in its "The numbers" section said in words.
_NEG = re.compile(r"\b(no|not|never|nobody|nothing|cannot|can'?t|won'?t|don'?t|doesn'?t|isn'?t|aren'?t)\b|n['’]t\b", re.I)
_US = r"(?:we|i|he|our (?:senior )?advisor|the (?:senior )?advisor|my advisor)"
_MODAL = r"(?:\s*['’]ll|\s+will|\s+can(?!['’]?t)|\s+could|\s+are going to|\s+is going to|\s+am going to|\s+gonna|" \
         r"\s+guarantee(?: to)?|\s+promise(?: to)?|\s+make sure(?: that)?)"
_ADV = r"(?:\s+(?:actually|definitely|absolutely|really|totally|for sure|just|easily|personally))?"
_OBJ = r"(?:your|the|this|that)\s+(?:home|house|place|property|sale|auction|foreclosure)"
NEVER_RULES = (
    ("promise", re.compile(r"\b" + _US + _MODAL + _ADV + r"\s+(?:you\s+)?(?:save|keep|stop|cancel|halt|end|kill|block|prevent)"
                           r"\s+(?:you\s+in\s+)?" + _OBJ + r"\b", re.I), False,
     "it promises an outcome (saving the home or stopping the sale). Offer options, never an outcome; more time "
     "is 'ask the court for more time'."),
    ("promise", re.compile(r"\b(?:we|he|our (?:senior )?advisor)\s+(?:stop|save|cancel|halt)s?\s+" + _OBJ + r"\b", re.I), False,
     "it promises an outcome (we stop the sale). Offer options, never an outcome."),
    ("guarantee", re.compile(r"\bguarantee", re.I), True, "it says guarantee. Nothing is guaranteed; offer options."),
    # "Do you need one? No." about his lawyer, ad-libbed on the 11-20 call. Keep-him is the ONLY stance.
    ("lawyer_needed", re.compile(r"(?:do(?:n'?t)?|not|no)\s+need\s+(?:a|an|your|the|one)?\s*(?:lawyer|attorney)\b"
                                 r"|(?:lawyer|attorney)\??\s*no\b|\bneed\s+(?:a|your)?\s*(?:lawyer|attorney)\s*\?\s*no\b"
                                 r"|do you need (?:one|him|her)\s*\?\s*no\b"
                                 r"|(?:fire|drop|dump|get rid of)\s+(?:your|the|that)\s+(?:lawyer|attorney)", re.I), False,
     "it questions whether they need their lawyer, or suggests dropping him. The only stance is KEEP HIM: "
     "the lawyer works the court side, the advisor works the money side."),
    # "nobody's accusing you of a crime" (live call 2026-11-19): the word plants the fear it denies. neg=False
    # on purpose: the reassuring, negated form is exactly the one to block.
    ("crime", re.compile(r"\b(crimes?|criminal|police|jail|prison|arrest(?:ed)?)\b", re.I), False,
     "it mentions crime, police, jail or arrest. Never bring these up, even to reassure: the word plants the "
     "fear it denies. This is a civil money matter; talk about the debt and the options."),
    ("authority", re.compile(r"\b(?:i['’]?m|i am|we['’]?re|we are|i['’]?m calling|we['’]?re calling|calling you)\s+"
                             r"(?:calling\s+)?(?:from|on behalf of|with|for)\s+(?:the|your)\s+(?:bank|lender|court|county|"
                             r"government|clerk|lender['’]?s (?:attorneys?|lawyers?)|(?:attorneys?|lawyers?) for)", re.I), True,
     "it says we are from the bank, the lender, the court or the government. We are an independent company."),
    ("authority", re.compile(r"\bwe (?:work for|represent) (?:the|your) (?:bank|lender|court|county)", re.I), True,
     "it says we work for the bank, the lender or the court. We are an independent company."),
    ("pay", re.compile(r"referral|commission|kickback|\b(?:lender|bank|attorney|they) pays? (?:us|me)\b|\bpays? us\b", re.I), False,
     "it says how we get paid (referral, commission, who pays us). Never say who pays us."),
    ("insider", re.compile(r"\bmy (?:supervisor|manager|boss|clients?)\b|\bour (?:own |in-house )?(?:attorneys?|lawyers?|legal team)\b"
                           r"|\b(?:we['’]?ve|we have|our company has|the company has) (?:got )?(?:over |more than )?thirty years", re.I), False,
     "it says my supervisor, my clients, our attorneys, or that the company has thirty years (the thirty years are "
     "the senior advisor's; attorneys are independent)."),
    ("we_lend", re.compile(r"\bwe (?:place|make|give|write|fund|offer) (?:you )?(?:a |the )?(?:loans?|mortgages?|refinanc)", re.I), True,
     "it says we place or make loans. Licensed lenders lend; we introduce."),
    ("legal_advice", re.compile(r"\byou (?:should|need to|have to|ought to|must|gotta|better)\s+(?:just\s+)?(?:file|sue|countersue|contest|"
                                r"appeal|answer the (?:complaint|summons|lawsuit)|respond to the (?:complaint|summons|lawsuit)|"
                                r"fight (?:the|this) (?:case|foreclosure|lawsuit))"
                                r"|\bi(?:['’]d| would)? (?:recommend|suggest|advise) (?:that )?(?:you )?(?:fil|su|contest|appeal|declar|go bankrupt)"
                                r"|\b(?:my|our) (?:legal )?advice\b|\blegally,? you\b|\byou have (?:the|a) (?:legal )?right to\b"
                                r"|\byou(?:['’]ve| have) (?:got )?a (?:strong|good|weak|solid) case\b"
                                r"|\b(?:your|the|his) (?:case|defen[cs]e) (?:will|would|is going to|can) (?:win|lose|hold up)"
                                r"|\byour (?:lawyer|attorney) is (?:wrong|bad|doing it wrong|useless)", re.I), False,
     "it gives legal advice or a verdict on their case. Never legal advice; the senior advisor brings a licensed "
     "professional where one is needed."),
    ("stop_paying", re.compile(r"\b(?:stop|quit) (?:paying|making (?:the|your) payments?)\b|\bignore (?:the|their|that|any|those) "
                               r"(?:mail|letters?|notices?|calls?)\b|\b(?:stop|quit) (?:talking|speaking) to (?:the|your) (?:bank|lender)", re.I), True,
     "it tells them to stop paying, stop talking to the lender, or ignore mail."),
)
_NUMW = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
         "eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred few couple several half").split()
_MONEY_UNIT = re.compile(r"\b(thousand|million|billion|grand|dollars?|bucks|percent|per cent)\b", re.I)


def _money_phrases(text):
    """[(frozenset of number words, 'pct' or 'usd')] for every figure written in words in `text`."""
    out = []
    for m in _MONEY_UNIT.finditer(text or ""):
        before = re.findall(r"[a-z\-]+", text[max(0, m.start() - 60):m.start()].lower())[-8:]
        nums = set()
        for w in reversed(before):
            parts = w.split("-")
            if all(p in _NUMW for p in parts):
                nums.update(parts)
            elif w in ("and", "to", "or", "a", "between", "about", "around", "of"):
                continue
            else:
                break
        if nums:
            out.append((frozenset(nums), "pct" if m.group(1).lower().startswith("per") else "usd"))
    return out


def _never_phrases(body, heads=()):
    """The quoted phrases in the playbook's Never-say section that are things NOT to say. A quote that names
    what to say instead ('it is "ask the court for more time"'), points at another section ('see "The
    numbers"', 'under "Nobody works for free"'), or is a single word ('"Options," never ...') is left out."""
    out = []
    for m in re.finditer(r"[\"“]([^\"”]{3,80})[\"”]", body):
        ph = m.group(1).strip().strip(",.;:").strip()
        pre = body[max(0, m.start() - 16):m.start()].lower()
        words = _words(ph)
        if len(words) < 2:
            continue
        if re.search(r"\b(it is|it's|it’s|instead|under|see|in|allowed|answer to|is)\s*$", pre.strip(), re.I):
            continue
        if any(h.startswith(ph.lower()) for h in heads):
            continue
        out.append(" ".join(words))
    return out


def _never_log(rule, outcome):
    """One log line per block: the rule and what happened (fixed by the re-ask, or dropped). Never the words."""
    try:
        sys.stderr.write("[coach] never-say rule=%s %s\n" % (rule, outcome))
    except Exception:
        pass


def never_say(line, pb=None):
    """('rule', 'reason') when `line` says something the playbook forbids, else ('', '')."""
    s = str(line or "")
    low = s.lower()
    for name, rx, neg, why in NEVER_RULES:
        for m in rx.finditer(s):
            if neg and _NEG.search(low[max(0, m.start() - 28):m.start()]):
                continue
            return name, why
    parts = pb if pb is not None else playbook_parts()
    ok = list(parts.get("nums") or []) + _money_phrases(" ".join(x[1] for x in AI_PLAN_FACTS))
    for nums, kind in _money_phrases(s):
        if not any(kind == k2 and nums == n2 for n2, k2 in ok):     # the same figure, not one end of a range
            return "figure", ("it says a dollar amount or a percent in words. Only the general figures in the playbook's "
                              "numbers section may be said; never a figure about this homeowner.")
    said = " " + " ".join(_words(s)) + " "
    for ph in parts.get("never") or []:
        if " " + ph + " " in said:
            return "playbook", "it says \"" + ph + "\", which the playbook's Never say list forbids."
    return "", ""


# FACT CHECK (11-19, replay grader): the lines the judge failed most were facts the coach was never given.
# never_say() knows only the playbook; these rules also need THIS call: is a sale date known, is equity
# verified, what did the homeowner actually say. A hit is asked again once with the reason, then dropped.
_HOURW = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|noon)"
_TIME_RX = re.compile(r"\b(?:at\s+" + _HOURW + r"(?:\s+(?:o['’]?clock|thirty|fifteen|forty[- ]five))?"
                      r"|" + _HOURW + r"\s+o['’]?clock|" + _HOURW + r"\s+(?:thirty|fifteen|forty[- ]five)\b"
                      r"|" + _HOURW + r"\s+(?:tomorrow|today|tonight|in the (?:morning|afternoon|evening)|this (?:morning|afternoon|evening)|a\.?m\b|p\.?m\b))", re.I)
FACT_RULES = (
    ("availability", re.compile(r"\b(?:he|the advisor|our (?:senior )?advisor)(?:['’]s| is| has)(?: got)?\s+(?:an?\s+)?"
                                r"(?:open(?:ing)?|free(?! of)|available|a slot|a spot|time)\b"
                                r"|\b(?:i['’]ve|i have|we['’]ve|we have) got him (?:free|open|available)\b"
                                r"|\bhe(?:['’]s| has) got [a-z' ]{0,30}\b(?:open|available|free(?! of))\b(?! of charge)", re.I),
     "it claims the senior advisor's availability, which you do not know. Offer 'later today or first thing tomorrow' "
     "and let them pick."),
    ("advisor_acts", re.compile(r"\b(?:he|the advisor|our (?:senior )?advisor)(?:['’]ll| will| can| is going to| gonna)?\s+"
                                r"(?:ask|go to|petition|file (?:with|in)|talk to|call|get)\s+(?:the|your)\s+(?:court|judge|clerk)\b", re.I),
     "it says the advisor himself goes to the court. He reviews the case and lays out the options; licensed "
     "professionals carry them out."),
    ("promise", re.compile(r"\bkeep (?:a|the) roof over|\byou(?:['’]ll| will)? (?:get to )?keep (?:the|your) (?:house|home|place)\b"
                           r"|\bmake[s]? sure (?:the|your) (?:sale|closing|deal) (?:goes through|closes|happens)"
                           r"|\b(?:he|we)(?:['’]ll| will) (?:get|make) (?:it|this|everything) (?:handled|taken care of|go away|right)"
                           r"|\b(?:has|have|there['’]?s) a fix\b|\bstop (?:that|the) bleeding\b"
                           r"|\b(?:he|we|the advisor)(?:['’]ll| will| can)? (?:get|have) (?:the|that|your) (?:date|sale) (?:pushed|moved|postponed)"
                           r"|\b(?:he|we|the advisor)(?:['’]ll| will| can)? line up (?:a|the) (?:cash )?buyer", re.I),
     "it promises an outcome. Offer options, never an outcome."),
)


def fact_check(line, days=None, lane="", eq="", convo=""):
    """('rule', 'reason') when `line` states a fact this call never gave the coach, else ('', '')."""
    s = str(line or "")
    said = str(convo or "").lower()
    # Deepgram writes times as digits ("3 pm", "3:30"): read them as the hour words the rule compares.
    _HW = "zero one two three four five six seven eight nine ten eleven twelve".split()
    said = re.sub(r"\b(1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?=a\.?m|p\.?m|o['’]?clock|\b)",
                  lambda m: " " + _HW[int(m.group(1))] + " ", said)
    for name, rx, why in FACT_RULES:
        if rx.search(s):
            return name, why
    for m in _TIME_RX.finditer(s):
        hour = re.search(_HOURW, m.group(0), re.I).group(0).lower()
        if not re.search(r"\b" + hour + r"\b", said):
            return "time", ("it names a clock time nobody on the call said. Offer 'later today or first thing tomorrow', "
                            "or repeat the time they gave.")
    try:
        known = int(days) >= 0
    except (TypeError, ValueError):
        known = False
    # Only a STATEMENT that a sale is set counts; asking about the sale date is how he finds out.
    _stmt = " ".join(x for x in re.split(r"(?<=[.!?])\s+", s) if not x.rstrip().endswith("?"))
    if (not known or str(lane or "").lower() == "filing") and re.search(
            r"\b(?:before (?:the|that|your) (?:sale|auction)(?: date)?|(?:the|that|your) (?:sale|auction)(?: date)? (?:is|comes|was|gets?) "
            r"(?:set|scheduled|coming|next|in|on|this|still|soon|close)|(?:sale|auction) date (?:is|comes)|still comes in"
            r"|(?:the|that|your) (?:sale|auction) (?:will|is going to|still) (?:happen|go|come)|(?:after|when|once) (?:the|that|your) (?:sale|auction)"
            r"|a (?:week|month|few days) (?:out|before|away))\b", _stmt, re.I) \
            and not re.search(r"\b(?:sale|auction|date)\b", said):
        return "sale_date", ("it mentions a sale date or an auction, but no sale date is known for this call and the "
                             "homeowner has not mentioned one. Talk about the filing and the options instead.")
    if str(eq or "").lower() != "verified" and re.search(
            r"\b(?:your|the|that|all that|plenty of|lot of|some) equity\b|\bmoney (?:in|left in) (?:it|the house|the place|your house)\b"
            r"|\bmoney in your pocket\b|\b(?:money|cash) out of (?:it|the house|the place|your house)\b"
            r"|\bwalk away with (?:money|cash|something|a check)\b", s, re.I) \
            and not re.search(r"not interested in saving your property, or the equity you['’]ve got in it", s, re.I):
        return "equity", ("it says or implies they have equity, which nobody has verified. Say 'whatever you've got in the "
                          "place' or 'if there's equity in it, he'll tell you'.")
    return "", ""


_CLOSE_RX = re.compile(r"(later today|first thing tomorrow|what time (tomorrow |today )?works?( best)?|"
                       r"time (are you|you'?re) available|works best for you)", re.I)
_YES_RX = re.compile(r"\b(yes|yeah|yep|sure|okay|ok|fine|alright|sounds good|that works|tomorrow|today|tonight|"
                     r"morning|afternoon|evening|o'?clock|\d{1,2}\s*(am|pm))\b", re.I)


def close_cap(line, asked, convo):
    """The 11-20 call: the card ended in the same would-later-today-or-tomorrow close TEN times in six
    minutes, and the man said "I would like to hang up" in the middle of them. Two asks is the number:
    after the close has been offered twice with no yes and no time from them, it is RETIRED for the
    call - the sentence carrying it is cut, and what remains (the answer part) is shown alone. A line
    that was ONLY the close becomes the walk-away, which leaves the door open instead of pushing."""
    if not line or not _CLOSE_RX.search(line):
        return line
    prior = sum(1 for a in (asked or []) if _CLOSE_RX.search(str(a)))
    if prior < 2:
        return line
    them = [c.split(": ", 1)[1] for c in (convo or []) if str(c).startswith("THEM")]
    if them and _YES_RX.search(them[-1]):
        return line                                     # they finally gave a yes or a time: close away
    kept = [t for t in re.split(r"(?<=[.?!])\s+", line.strip()) if t and not _CLOSE_RX.search(t)]
    out = " ".join(kept).strip()
    return out or ("I'll leave you alone. Save my number, and the day the plan slips even one day, "
                   "that's the day you call me. Fair enough?")


def one_question(line):
    """A line with two or more questions keeps its statements and its LAST question (the ask). 11-19: the
    judge's 'unnatural' class was mostly two or three questions stacked in one line."""
    parts = [x for x in re.split(r"(?<=[.!?])\s+", str(line or "").strip()) if x]
    qs = [i for i, x in enumerate(parts) if x.rstrip().endswith("?")]
    if len(qs) < 2:
        return line
    keep = [x for i, x in enumerate(parts) if i not in qs[:-1]]
    return " ".join(keep)


_TIME_ASK = re.compile(r"later today|first thing tomorrow|today or tomorrow|what time|tomorrow morning", re.I)


_ASK_EN = "Would later today or first thing tomorrow work better?"
_ASK_ES = "¿Le queda mejor hoy más tarde o mañana a primera hora?"


def repair_time(line, convo="", lang=""):
    """REPAIR, NOT DROP (11-19, replay grader: a quarter of the lines came back blank because the model kept
    proposing a clock time or the advisor's availability, twice). A sentence that names a time nobody said, or
    claims the advisor is free, is swapped for the standing ask when it is the ask, and cut when it is a
    statement. The rest of the line stays. Returns the line unchanged when there is nothing to repair."""
    s = str(line or "").strip()
    if not s:
        return s
    parts = [x for x in re.split(r"(?<=[.!?])\s+", s) if x]
    avail = FACT_RULES[0][1]
    out, swapped = [], False
    for x in parts:
        bad = bool(avail.search(x)) or fact_check(x, 0, "", "verified", convo)[0] == "time"
        if not bad:
            out.append(x)
        elif not swapped:
            swapped = True               # one ask replaces every invented-time sentence
    if not swapped:
        return s
    es = str(lang or "").lower().startswith("es")
    ask = _ASK_ES if es else _ASK_EN
    # THEY ALREADY PICKED THE DAY: re-offering "today or tomorrow" undoes it. Ask only for the hour.
    low = str(convo or "").lower()
    i_tom = max(low.rfind("tomorrow"), low.rfind("mañana"))
    i_tod = max(low.rfind("today"), low.rfind("tonight"), low.rfind("after work"), low.rfind("this afternoon"),
                low.rfind("this evening"), low.rfind("hoy"))
    if i_tom >= 0 and i_tom > i_tod:
        ask = "¿A qué hora mañana le queda mejor?" if es else "What time tomorrow works best for you?"
    elif i_tod >= 0:
        ask = "¿A qué hora hoy le queda mejor?" if es else "What time today works best for you?"
    out = [x for x in out if not x.rstrip().endswith("?")] + [ask]
    return " ".join(out)


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


_AI_DAYWORD = re.compile(r"\b(tomorrow|today|tonight|this (afternoon|evening|morning)|later|in the (morning|afternoon|evening)|"
                         r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|after work|when i get (out|off)|"
                         r"manana|hoy|esta tarde)\b", re.I)
_AI_YESWORD = re.compile(r"^\W*(yes|yeah|yep|yup|okay|ok|sure|fine|alright|all right|sounds good|that works|let'?s do it|"
                         r"go ahead|i guess( so)?|si|claro|esta bien)\b", re.I)
_AI_ASKEDFOR = re.compile(r"five minutes|senior advis|later today|first thing tomorrow|ten tomorrow|four (today|this afternoon)|"
                          r"what time|does that work|sound\?", re.I)
# What the CALLER says when he is closing. Call 11-17 09:36: his own "Ten tomorrow morning or later this
# evening, how does that sound?" was heard as the homeowner giving a day, and the box jumped to "Okay,
# ten tomorrow morning, save my number" while she was asking what we sell.
_AI_CALLERSAID = re.compile(r"how does that sound|does that work|save my number|read it back|senior advis|he calls you", re.I)
# The hour already agreed, read from the booking lines given so far ("he calls you right at six").
_AI_BOOKED = re.compile(r"calls? you (?:right )?at ([a-z]+)", re.I)
_AI_WANT = re.compile(r"\b(an offer|your (offer|price)|what'?s your price|how much (would|will|can) you|a buyer|sell it|"
                      r"more time|buy (me )?(some )?time|a loan|refinanc|keep (it|my house|the house)|stay in (it|my house))\b", re.I)


def mate_hint(last_said, given):
    """The close, when they just handed it over: a day with no hour, a plain yes after the ask, or
    naming a want that is exactly what the advisor does. '' otherwise."""
    used = " ".join(given)
    asked_already = bool(_AI_ASKEDFOR.search(used))
    if _AI_NOTINT.search(last_said) or _AI_BUSY.search(last_said) and not _AI_DAYWORD.search(last_said):
        return ""
    if _AI_CALLERSAID.search(last_said):
        return ""                       # the caller reading his own close is never the homeowner booking
    _bk = _AI_BOOKED.findall(used)
    if _bk and _AI_YESWORD.search(last_said) and len(last_said.split()) <= 8 and not _AI_DAYWORD.search(last_said):
        # Same call, 04:29: booked at six, she said "Okay. Sounds good. Thank you." and the box offered ten tomorrow.
        return ("\n\nIT IS ALREADY BOOKED for " + _bk[-1].lower() + " and they just confirmed it. TYPE: time, STEP: booked. Your "
                "SAY, fifteen words or fewer: they are set for that hour, pick up when he calls, and thank them. Do NOT "
                "name any other hour and do NOT ask them to save or read back a number again.")
    hour = "four this afternoon" if re.search(r"\b(today|tonight|later|this (afternoon|evening)|after work|hoy|esta tarde)\b", last_said, re.I) else "ten tomorrow morning"
    if asked_already and _AI_DAYWORD.search(last_said) and not re.search(r"\b(not|can'?t|cannot|won'?t|never|don'?t)\b", last_said, re.I):
        return ("\n\nTHEY GAVE YOU A DAY. That is a yes. TYPE: time, STEP: booked. Your SAY, twenty-five words or fewer: "
                "name the hour yourself (" + hour + ", unless they named an hour, then use theirs), say the senior "
                "advisor calls them right then, and tell them to save your number and read it back. Do NOT ask what "
                "hour, do NOT give a reason, do NOT say the sale could come first.")
    if asked_already and _AI_YESWORD.search(last_said) and len(last_said.split()) <= 8:
        return ("\n\nTHEY SAID YES. TYPE: time, STEP: booked. Your SAY, twenty-five words or fewer: name the hour yourself ("
                + hour + "), say the senior advisor calls them then, and tell them to save your number and read it "
                "back. No reason, no question except 'that work?'.")
    if _AI_WANT.search(last_said) and "exactly what he" not in used.lower() and not (_AI_LOANQ.search(last_said) and "?" in last_said):
        return ("\n\nCHECKMATE. They just told you what they want, and it is what the senior advisor does. Do not ask "
                "another question about their situation and do not explain. Your SAY, thirty words or fewer: pick up "
                "their want in their words, one flat sentence that it is exactly what he does (he brings the offer, "
                "he asks the court for more time, he works with the lenders), then name the hour: 'How does "
                + hour + " sound?' STEP: close.")
    return ""


# SAVE THE NUMBER: ONCE. Call 11-17 09:36: the box said "save my number and read it back to me" three
# times while she kept answering "I don't know what your number is" and "I can't read it".
_AI_NUMASKED = re.compile(r"save my number|read it back", re.I)
_AI_NONUM = re.compile(r"\b(don'?t|do not|can'?t|cannot|didn'?t) (see|know|have|read|get)\b.{0,40}\b(number|it)\b|what('?s| is) (your|the) number|"
                       r"different number|which number|no number|(blocked|private|unknown|restricted) (number|caller)|no caller id", re.I)


def num_note(last_said, given):
    """(note, replaces) for the save-the-number step. They say they cannot see the number: give it to them
    out loud instead, and that is the whole line. Already asked once this call: never ask again."""
    used = " ".join(given)
    if _AI_NUMASKED.search(used) and _AI_NONUM.search(last_said):
        return ("\n\nTHEY CANNOT SEE YOUR NUMBER. They just told you so. Do NOT say 'save my number' or 'read it back' "
                "again. TYPE: question. Your SAY, twenty words or fewer, no digits: no problem, grab a pen, you will "
                "give it to them right now, slowly. Nothing else: the caller reads his number out himself. If an hour "
                "is still open, do not settle it in this line.", True)
    if _AI_NUMASKED.search(used):
        return ("\n\nYOU ALREADY ASKED THEM TO SAVE THE NUMBER ON THIS CALL. Whatever any instruction above says, do "
                "NOT say 'save my number', 'save the number' or 'read it back' again. Confirm the hour and that he "
                "calls them then, and stop.", False)
    return ("", False)


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
    (r"what time are you done", "what time are you done today (never ask this: name the hour yourself)", 0),
    (r"morning or (after|afternoon)|what hour|tell me a time|give me a time", "asking them to pick the hour (name it yourself)", 0),
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
    "How does ten tomorrow morning sound?",
    "I've got him at four this afternoon. Does that work?",
    "He's open at ten tomorrow. I'll put you down for ten.",
    "Let's do four today, right after work. Good?",
    "Ten tomorrow morning, he calls you. That work?",
    "I can have him call you within the hour. Does that work?",
    "Five minutes against the house, that's a fair trade. Ten tomorrow morning?",
    "Let's get it on his calendar so it's done. Four today?",
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


def _reading_line(text, given):
    """True when `text` is a FRAGMENT of a recent coach line read aloud: at least five words, and nearly all of
    them (four in five, at least five in a row) appear in that line in the same order. One microphone splits his
    reading into pieces, and _read_back needs most of the whole line, so a half-line piece used to go out as the
    homeowner (or kept a wrong THEM label). A homeowner echoing five of his words in a row, in order, is rare;
    two or three ('five minutes', 'the senior advisor') are not enough."""
    tw = _words(text)
    if len(tw) < 5:
        return False
    need = max(5, -(-len(tw) * 4 // 5))
    for g in given:
        gw = _words(g)
        if len(gw) < need:
            continue
        best = 0
        prev = [0] * (len(gw) + 1)
        for a in tw:
            cur = [0] * (len(gw) + 1)
            for j, b in enumerate(gw, 1):
                if a == b:
                    cur[j] = prev[j - 1] + 1
                    if cur[j] > best:
                        best = cur[j]
            prev = cur
        if best >= need:
            return True
    return False


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


AI_ROUND3 = (
    "\n\nONE QUESTION. A line has at most one question, and it is the last sentence. Under forty words."
    "\n\nAFTER A TIME IS AGREED, never ask for the time or repeat it again. The next lines, in order: save my number "
    "and read it back; who else should be on the call; have the latest letters from the lender or the court nearby; "
    "then the goodbye."
    "\n\nA bare 'okay' or 'yes' that is not about a time is NOT a booking: say what the five minutes with the senior "
    "advisor is, then offer the two times. Never ask for a time before the advisor and the five minutes were said on "
    "this call."
    "\n\nNO FACTS OF YOUR OWN about the law, the courts, lenders, associations or what anyone else on their case is "
    "doing. If it is not in the playbook or in what they said, ask a question instead of stating it."
)


def ai_line(lines, lang="", shown="", asked=None, days=None, lane="", eq="", stat=None):
    """The next line for the caller as {"line", "step", "n", "of", "name"}, or {} (no key, nothing
    to answer, a slow or failed call, or a reply that breaks the no-numbers rule). Never raises: a
    dead model must not touch the call."""
    st = stat if stat is not None else {}
    st.update(deadline=time.monotonic() + AI_BUDGET, tries=0, err="", why="")
    key = venice_key()
    if not key:
        st["why"] = "no_key"
        return {}
    rows = []
    for l in (lines or [])[-18:]:        # 11-19: was 24; the older lines are in the "lines you already gave" list
        t = str((l or {}).get("text") or "").strip()
        if not t:
            continue
        spk = str((l or {}).get("spk") or "")
        who = "CALLER" if spk == "you" else "THEM" if spk == "them" else "?"
        if who == "?" and _read_back(t, list(asked or []) + [shown]):
            who = "CALLER"            # one microphone: your line, read out, is the caller for certain
        elif who != "CALLER" and _reading_line(t, list(asked or [])[-3:] + [shown]):
            who = "CALLER"            # a piece of a recent coach line, word for word: him reading it, whatever the label
        rows.append(who + ": " + t[:400])
    if not rows:
        st["why"] = "no_words"
        return {}
    # COST. Venice bills Claude with prompt caching: text it has seen in the last five minutes costs a
    # twentieth of new text. The rules and the playbook (about 13,000 of 16,000 tokens) are the same
    # on every line, but they used to be ONE block with this call's details mixed in, so every request
    # was billed as all-new: call 11-16 19:47 cost $2.26, of which $1.86 was re-sending the same
    # playbook 45 times. Now the unchanging part is its own system message, and everything about this
    # call and this line rides in the user message after it.
    # SIZE (11-19): only the playbook's CORE rides in that system message; the pieces that fit this moment come
    # in the user message (see situational()). The system message stays byte-for-byte the same on every line.
    system = AI_SYSTEM + AI_ROUND3
    pbp = playbook_parts()
    if pbp["core"]:
        system += ("\n\nPLAYBOOK. This is the caller's own sales system. Take the MOVE and the facts from it: what "
                   "to do when they say this, which analogy fits, what is true. Do not take the wording. Its "
                   "sentences are examples of the idea; say the idea your own way, short, as in TALK LIKE A PERSON. "
                   "Its 'Never say' list overrides everything else, and where the playbook allows something this "
                   "prompt forbids (its numbers, its options, its attorney questions), the playbook wins. More of "
                   "it comes with a line, under SITUATIONAL, when it is that moment.\n" + pbp["core"])
    system += AI_SPLIT
    _prior0 = [x for x in (asked or [])[-14:] if str(x or "").strip()]
    _sit, st["sit"] = situational(rows, _prior0, pbp)
    if _sit:
        system += ("\n\nSITUATIONAL: the rules and playbook pieces for what they just said. Same force as the rest.\n"
                   + _sit + "\n")
    if str(lang).lower().startswith("es"):
        system += "\nWrite the SAY line in Spanish."
    # EQUITY IS A FACT OR IT IS NOT SAID. Call 11-16 19:47: the line read "a house with equity in it" on a
    # lead whose equity nobody has verified. The page says whether the board has it as a fact.
    if str(eq or "").lower() != "verified":
        system += ("\n\nEQUITY IS NOT VERIFIED ON THIS LEAD. Never say or imply that they have equity, that the house "
                   "has equity in it, or that there is money in it for them. Say 'whatever you've got in the place' or "
                   "'what you've put into it', or 'if there's equity in it, he'll tell you'. The one fixed line "
                   "'So you're not interested in saving your property, or the equity you've got in it?' is allowed.")
    system += ("\n\nNEVER PUT WORDS IN THEIR MOUTH. Do not say 'so you know about the case' or 'so you already "
               "heard' unless they said exactly that. If they said they did not know, your line starts from that.")
    lane = str(lane or "").lower()
    if lane in AI_LANES:
        system += "\n\nTHIS LEAD: " + AI_LANES[lane]
    when = sale_in_words(days) if lane != "filing" else ""
    if not when:
        system += ("\n\nNO SALE DATE IS KNOWN FOR THIS CALL. Never mention a sale date, an auction or 'that date' "
                   "unless the homeowner says one first. Talk about the filing and the options.")
    system += ("\n\nNEVER INVENT: the advisor's availability, a clock time nobody said, or what the advisor does at "
               "the court. Offer 'later today or first thing tomorrow'.")
    if when:
        system += ("\n\nCourt calendar for this homeowner: the sale is set " + when + ". You may say that in those "
                   "words when it helps the line. Never say a date.")
    shown = " ".join(str(shown or "").split())[:320]
    prior = [" ".join(str(x or "").split())[:320] for x in (asked or [])[-14:] if str(x or "").strip()]
    if prior:
        system += ("\nLines you already gave on this call, oldest first:\n" + "\n".join("- " + x[:260] for x in prior[-4:])
                   + "\nNever repeat one of them or ask the same thing in other words. The new line is a later step.")
        system += variety_note(prior)
    if shown:
        system += ("\nThe caller's screen currently shows: \"" + shown + "\". The caller may have just read it "
                   "out. Write the line that comes after the homeowner's answer to it.")
    said = " ".join(r.split(": ", 1)[1] for r in rows[-6:])

    use = {"model": AI_MODEL}
    st["model"] = use["model"]

    def left():
        return st["deadline"] - time.monotonic()

    def ask(sys_text):
        static, _, call = sys_text.partition(AI_SPLIT)
        user = ("FOR THIS CALL AND THIS LINE (these are instructions, with the same force as your rules):" + call
                + "\n\nTRANSCRIPT SO FAR:\n" + "\n".join(rows)) if call.strip() else "\n".join(rows)
        body = {"model": use["model"], "max_tokens": AI_MAX_TOKENS, "temperature": 0.5, "stream": False,
                "messages": [{"role": "system", "content": static}, {"role": "user", "content": user}],
                "venice_parameters": {"include_venice_system_prompt": False, "disable_thinking": True,
                                      "strip_thinking_response": True}}
        if AI_CACHE_KEY:
            body["prompt_cache_key"] = AI_CACHE_KEY
        st["ptok_est"] = (len(static) + len(user)) // 4
        out = _ai_post(body, key, st)
        if not out.strip():
            st["why"] = "model:" + (st["err"] or "empty")
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
        if kind != "time":
            line = spell_digits(line)
        bad = ("$" in line) if kind == "time" else bool(_AI_BAD.search(line))
        line = re.sub(r"\s*[\u2014\u2013]\s*", ", ", line)        # a dash is written, not spoken
        if st.get("finish") == "length" and line and not re.search(r"[.?!]['\"\u201d]?$", line):
            cut = re.match(r"^(.*[.?!])\s", line + " ")             # hit max_tokens mid-sentence: keep whole sentences
            if not cut:
                st["why"] = "cut"
                return {}
            line = cut.group(1)
        if not line or line.upper().rstrip(".") == "SKIP":
            st["why"] = "skip"
            return {}
        if len(line) > 640:
            st["why"] = "too_long_640"
            return {}
        if bad:
            st["why"] = "digits"
            return {}
        line = no_praise(line)
        st["why"] = ""
        res = {"line": line, "step": step, "of": len(AI_STEPS), "type": kind, "heard": heard[:200], "model": use["model"]}
        if step:
            res["n"] = AI_STEP_IDS.index(step) + 1
            res["name"] = AI_STEPS[res["n"] - 1][1]
        return res

    def reask(extra):
        """Ask again with `extra` added, or None when the budget has no room for another try."""
        if left() < AI_REASK_MIN:
            return None
        return ask(system + extra)

    def done(res):
        # The last gate: a line that reached here through a later re-ask (too long, weak, repeat, fold) is
        # checked against the never-say rules too. No budget for another ask at this point: it is dropped.
        if res and res.get("line"):
            rule = check(res["line"])[0]
            if rule:
                _never_log(rule, "dropped")
                st["why"] = "never:" + rule
                res = {}
        st["why"] = "" if (res and res.get("line")) else (st["why"] or "empty")
        st["model"] = use["model"]
        return res

    _convo = " ".join(r.split(": ", 1)[-1] for r in rows if not r.startswith("CALLER"))

    def check(line):
        r = never_say(line, pbp)
        return r if r[0] else fact_check(line, days, lane, eq, _convo)

    last_said = rows[-1].split(": ", 1)[1] if rows else ""
    # STILL READING (11-19). The last words are the caller reading the line on his screen: the homeowner has not
    # answered yet, so a new line now would answer the coach's own words. Keep what is on screen.
    _recent = [x for x in (prior[-3:] + [shown]) if x]
    # One microphone: the label on those words is often "?" or wrong, so the words decide, not the label.
    if rows and _recent:
        # Only the LAST words count: a short answer after his read ("No, nobody did.") is the homeowner.
        if _read_back(last_said, _recent) or _reading_line(last_said, _recent):
            st["why"] = "wait"
            return done({})
    if _AI_META.search(last_said):
        st["why"] = "meta"
        return done({})                # the callers are talking about the script: keep what is on screen
    if not rows[-1].startswith("CALLER"):
        _given = [x for x in prior + [shown] if x]
        _ph = mate_hint(last_said, _given) or plan_hint(last_said, prior + [shown], when)
        _hint = _ph or wit_hint(last_said, _given)
        _nn, _only = num_note(last_said, _given)
        _hint = _nn if _only else _hint + _nn
        system += _hint
        if _AI_PAYQ.search(last_said):
            system += AI_PAY_ANSWER
        if AI_FAST_MODEL and (_hint or _AI_PAYQ.search(last_said) or _AI_NOTINT.search(last_said)):
            use["model"] = AI_FAST_MODEL            # the content is handed over; the fast model says it
    res = ask(system)
    # A FIGURE IN DIGITS. Asked once more with the rule spelled out, instead of leaving the box empty.
    if not res and st["why"] == "digits":
        r2 = reask("\n\nYour last answer had a number written in digits or a dollar sign, so it could not be shown. "
                   "Write the line again with every number in words. If it is an amount, a rate or a date you were "
                   "not given in these instructions, leave it out.")
        if r2 is not None:
            res = r2
    # An invented clock time or advisor availability is repaired in code first (repair_time), so it never
    # costs a re-ask or blanks the box.
    if res and res.get("line"):
        _fixed = repair_time(res["line"], _convo, lang)
        if _fixed != res["line"]:
            res["line"] = _fixed
            st["repaired"] = 1
    # NEVER SAY. A line that breaks the playbook's never-say list is asked once more with the reason, and
    # dropped if the second one breaks it too. How-we-get-paid words have their own re-ask below, with the facts.
    if res and res.get("line"):
        rule, why = check(res["line"])
        if rule and rule != "pay":
            r2 = reask("\n\nYour last answer could not be shown because " + why + " Write the line again without "
                       "that, same point, same ask.")
            if r2 and r2.get("line") and not check(r2["line"])[0]:
                _never_log(rule, "fixed")
                res = r2
            else:
                _never_log(rule, "dropped")
                st["why"] = "never:" + rule
                return done({})
    if res and res.get("line"):
        _fixed = repair_time(res["line"], _convo, lang)
        if _fixed != res["line"]:
            res["line"] = _fixed
            st["repaired"] = 1
    # ONE QUESTION (11-19). The time-before-the-advisor rule is in the prompt only: a re-ask here cost a model call.
    if res and res.get("line"):
        res["line"] = one_question(res["line"])
    # THE CLOSE IS ASKED TWICE, THEN RETIRED (11-20). Code, not prompt: the model kept appending it.
    if res and res.get("line"):
        _capped = close_cap(res["line"], asked, _convo)
        if _capped != res["line"]:
            res["line"] = _capped
            st["closecap"] = 1
    # TOO LONG. His target is forty-five words for an objection answer. One that runs past fifty-five is
    # asked for again, shorter; the shorter of the two is shown. The pay answer is long on purpose. With no
    # time left to ask again, the line is cut to whole sentences in code.
    # LATENCY (11-19): when cutting it to whole sentences in code already gives a line of forty-five words or
    # fewer that still ends on the ask, that is used and the re-ask (a whole second model call) is skipped.
    if (res and res.get("type") in ("objection", "question", "answer", "unclear") and len(res.get("line", "").split()) > 55
            and not _AI_PAYQ.search(last_said)):
        _t = trim_line(res["line"], 45)
        if _t != res["line"] and len(_t.split()) <= 45 and _t.rstrip().endswith("?") and len(re.findall(r"[.?!]", _t)) >= 2:
            res["line"] = _t
            st["trimmed"] = 1
    if (res and res.get("type") in ("objection", "question", "answer", "unclear") and len(res.get("line", "").split()) > 55
            and not _AI_PAYQ.search(last_said)):
        n_words = len(res["line"].split())
        short = reask("\n\nYour last answer was " + str(n_words) + " words: \"" + res["line"] + "\" That is too "
                      "long to say out loud. Say the same point in under forty words: keep the one sentence that matters "
                      "and the one question, cut everything else. No second question, no extra reason.")
        if short and short.get("line") and len(short["line"].split()) < n_words and not folds(short):
            res = short
        if len(res["line"].split()) > 55:
            res["line"] = trim_line(res["line"], 45)
    # OVER FORTY-FIVE, UNDER FIFTY-SIX (11-19): cut to whole sentences in code when that keeps the ask. No model call.
    if (res and res.get("line") and 45 < len(res["line"].split()) <= 55 and not _AI_PAYQ.search(last_said)):
        _t = trim_line(res["line"], 40)
        if _t != res["line"] and len(_t.split()) >= 8 and (_t.rstrip().endswith("?") or not res["line"].rstrip().endswith("?")):
            res["line"] = _t
            st["trimmed"] = 1
    if res and _AI_WEAK.search(res.get("line", "")) and res.get("type") != "stop":
        res = reask("\n\nYour last answer used the words of someone who expects a no (worst case, hang up, "
                    "lost nothing, no pressure, not going to push, talking too much). Never. Give the short objection "
                    "answer: agree in a few words, ONE reframe about their situation, then the ask with a time.")
        if not res or _AI_WEAK.search(res.get("line", "")):
            st["why"] = st["why"] or "weak"
            return done({})
    # A made-up answer about how we get paid is never shown: asked again with the facts, then dropped.
    if res and _AI_PAYLIE.search(res.get("line", "")):
        res = reask(AI_PAY_ANSWER + " Your last answer invented how the company gets paid. That is forbidden.")
        if not res or _AI_PAYLIE.search(res.get("line", "")):
            st["why"] = st["why"] or "paylie"
            return done({})
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
        return done(res)
    # NEVER FOLD, enforced. If the line ends the call, or calls their words a stop request, and nobody
    # in the last few lines actually asked to stop being called, that line is not shown: the model is
    # asked again and told so. A second fold shows nothing rather than a goodbye.
    if res and folds(res) and not _AI_STOP.search(said + " " + res.get("heard", "")):
        res = reask("\n\nIMPORTANT: the homeowner has NOT asked to stop being called. Your last answer ended "
                    "the call or treated their words as a stop request. That is wrong. Their last words are an "
                    "objection: TYPE is objection. Answer it with a NEVER FOLD angle you have not used and go back "
                    "to the ask. No goodbye, no 'take care', nothing about a list.")
        if not res or folds(res):
            st["why"] = st["why"] or "fold"
            return done({})
        if res.get("type") == "stop":
            res["type"] = "objection"
    # SAID ALREADY. The model is told every earlier line and still repeated one 18 times in 165 (forensics
    # 11-18). A repeat is asked for once more in new words; a second repeat leaves the screen as it is.
    if res and res.get("line") and res.get("type") != "stop":
        dup = near_dup(res["line"], prior + [shown])
        if dup:
            r2 = reask("\n\nYour last answer repeated a line you already gave on this call: \"" + dup[:240] + "\". The "
                       "homeowner has heard it. Answer their last words with something they have not heard yet, in new "
                       "words, and keep moving toward the booking.")
            if r2 and r2.get("line") and not near_dup(r2["line"], prior + [shown]) and not folds(r2):
                res = r2
            else:
                st["why"] = "repeat"
                return done({})
    return done(res)


def _ai_post(body, key, st):
    """POST one request to the model; the reply text, or '' after at most two tries inside st's deadline.
    A failure is recorded in st["err"] (timeout, http429, conn, badjson, ...) instead of vanishing: before
    11-18 every exception became a silent blank line and the log could not tell a dead key from a slow model."""
    data = json.dumps(body).encode("utf-8")
    for attempt in range(2):
        rem = st["deadline"] - time.monotonic()
        if rem < AI_TRY_MIN:
            st["err"] = st["err"] or "budget"
            break
        st["tries"] += 1
        try:
            req = urllib.request.Request(AI_URL, data=data,
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=min(AI_TIMEOUT, rem)) as r:
                st["ttfb"] = int((time.monotonic() - t0) * 1000)      # headers back: the model has finished (no stream)
                j = json.loads(r.read().decode("utf-8"))
            st["total"] = int((time.monotonic() - t0) * 1000)
            u = j.get("usage") if isinstance(j, dict) else None
            if isinstance(u, dict):
                d = u.get("prompt_tokens_details") if isinstance(u.get("prompt_tokens_details"), dict) else {}
                st["ptok"], st["ctok"] = u.get("prompt_tokens"), u.get("completion_tokens")
                st["cached"] = d.get("cached_tokens", u.get("cache_read_input_tokens"))
                st["cwrite"] = d.get("cache_creation_input_tokens", u.get("cache_creation_input_tokens"))
            st["finish"] = str(j["choices"][0].get("finish_reason") or "")
            out = str(j["choices"][0]["message"].get("content") or "")
            if out.strip():
                st["err"] = ""
                return out
            st["err"] = "empty"
        except urllib.error.HTTPError as e:
            st["err"] = "http%d" % e.code
            if e.code in (400, 401, 402, 403, 404):
                break                    # a refused request comes back refused; retrying only burns the clock
        except urllib.error.URLError as e:
            st["err"] = "timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "conn"
        except (socket.timeout, TimeoutError):
            st["err"] = "timeout"
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            st["err"] = "badjson"
        except Exception as e:           # never let the model touch the call
            st["err"] = type(e).__name__
        if attempt == 0 and st["err"] not in ("timeout", "empty"):
            # a dropped connection or a 429/5xx: a short pause, never longer than the clock allows
            time.sleep(max(0.0, min(0.3, st["deadline"] - time.monotonic() - AI_TRY_MIN)))
    return ""


# THE CALL WRAP-UP. When the call ends the same model reads the whole transcript and drafts the log
# entry: what happened, what they want, what they pushed back with, the time they agreed to, and a
# note. It is a DRAFT: the caller sees it on the outcome screen, fixes it, and picks the outcome
# himself; only then does it go to Call Mode. One microphone still mislabels who said what, and one
# wrong "appointment" logged by a machine would corrupt the cadence and the stats.
WRAP_OUTCOMES = ("appointment", "callback", "talked", "not_interested", "voicemail", "bad_number")
# ONE VOCABULARY (11-18). The model keeps answering in the words above (they score well and it was
# never re-tested on codes), but everything the coach stores or hands on is Call Mode's own outcome
# code (call_mode.CALL_OUTCOMES; _coachhandofftest.py pins the two lists equal). The old names map
# here so a meta.json from before this change and one from after mean the same thing.
CALL_MODE_CODES = ("noanswer", "voicemail", "callback", "talked", "appt", "gate", "badnum", "notint", "wrong", "dnc")
OLD_OUTCOME_NAMES = {"appointment": "appt", "not_interested": "notint", "bad_number": "badnum"}


def outcome_code(v):
    """A Call Mode outcome code, "auto_silence" (the 90-second stop), or None for anything else."""
    v = str(v or "").strip().lower()
    v = OLD_OUTCOME_NAMES.get(v, v)
    return v if v in CALL_MODE_CODES or v == "auto_silence" else None

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
    return {"outcome": outcome_code(oc), "want": want if want in ("keep", "sell", "undecided") else "",
            "objection": none(field("OBJECTION"))[:80], "when": none(field("WHEN"))[:60], "note": none(field("NOTE"))[:320]}


_ENTITY_RE = re.compile(
    r"\b(LLC|L L C|INC|INCORPORATED|CORP|CORPORATION|COMPANY|LTD|LIMITED|LLP|LLLP|PLLC|TRUST|TRUSTEE|TRUSTEES|"
    r"REVOCABLE|IRREVOCABLE|ESTATE|EST|HEIRS|DEVISEES|BENEFICIARIES|BANK|BANCORP|ASSOCIATION|ASSOC|ASSN|"
    r"CONDOMINIUM|CONDO|HOMEOWNERS|HOA|PROPERTIES|PROPERTY|HOLDINGS|INVESTMENTS|INVESTMENT|INVESTORS|GROUP|"
    r"PARTNERS|PARTNERSHIP|FUND|FUNDING|CAPITAL|REALTY|ENTERPRISES|VENTURES|MORTGAGE|SERVICING|LENDING|"
    r"FINANCIAL|FINANCE|CREDIT UNION|FEDERAL|NATIONAL|FOUNDATION|MINISTRIES|MINISTRY|SERVICES|SOLUTIONS|"
    r"MANAGEMENT|DEVELOPMENT|DEVELOPERS|CONSTRUCTION|BUILDERS|HOMES|HOUSES|APARTMENTS|VILLAS|TOWNHOMES|"
    r"COMMUNITY|CLUB|SOCIETY|AUTHORITY|COUNTY|UNITED STATES|SECRETARY|DEPARTMENT|FSB|UNKNOWN|TENANT|OCCUPANT)\b")
_NAME_FRAGMENTS = set(
    "A AN THE WE US OUR I ME MY YOU YOUR HE HIM HIS SHE HER IT ITS THEY THEM THEIR THIS THAT WHO ALL ANY NONE NO NOT "
    "AND OR OF FOR TO AT IN ON BY AS IS FROM WITH MR MRS MS MISS DR SR JR II III IV AKA FKA NKA DBA ETAL ET AL ETUX "
    "ETVIR UNK NA DE DEL DELA LA LAS LOS EL LE DA DI DOS DU VAN VON DER DEN ST SAN SANTA MC MAC NW NE SW SE "
    "OWNER VIA TITLE SEARCH".split())


def greet_first(oname, owners, county="", case="", officer=False):
    """The first name to say on the phone, or '' when there is none worth saying.

    "Hi, Lopez?" (2026-11-19): the coach page took the first word of the owner string, which is the
    SURNAME wherever the roll writes the last name first. Same reading as call_mode._greet_name and the
    page's firstName(): "LAST, FIRST" is marked by its comma in any county; Palm Beach's roll writes
    "LAST FIRST" with no comma; everything else arrives "First Last". A company, trust or estate has
    no first name, and neither does a pronoun, a title or a surname particle. officer=True is the
    investor lane: the owner is a company and `oname` is the person behind it, who is who answers."""
    own1 = str(owners or "").split(";")[0].strip()
    src = str(oname or "").strip() or own1
    if not src:
        return ""
    flat = lambda v: re.sub(r"\s+", " ", re.sub(r"[.,]", " ", str(v).upper())).strip()
    if _ENTITY_RE.search(flat(src)) or (not officer and _ENTITY_RE.search(flat(own1))) or re.search(r"\d", src):
        return ""
    if "," in own1 and "," not in src and not officer:
        # the roll says LAST,FIRST outright and the cleaned name still leads with that surname
        _l, _s = re.sub(r"[^A-Z ]", " ", own1.split(",")[0].upper()).split(), src.upper().split()
        if _l and len(_s) >= 2 and _s[0] == _l[0] and own1.split(",", 1)[1].strip():
            src = own1.split(",", 1)[1]
    src = re.split(r"&| and ", src, flags=re.I)[0].strip() or src
    if "," in src:
        last, _, rest = src.partition(",")
        src = rest.strip() or last.strip()
    elif ("PALM" in str(county or "").upper() or str(case or "").startswith("50")) and "," not in own1:
        toks = src.split()
        if len(toks) >= 2:
            src = " ".join(toks[1:])
    toks = re.sub(r"[^A-Za-z '\-]", " ", src).split()
    if not toks:
        return ""
    tok = toks[0].strip("'-").upper()
    if len(tok) < 2 or tok in _NAME_FRAGMENTS or not re.search(r"[AEIOUY]", tok):
        return ""
    return tok[0] + tok[1:].lower()


def _caller_name(v):
    """The first name typed in "Your name" on the coach page, or '' when it is empty or not a name."""
    t = re.sub(r"[^A-Za-z '\-]", " ", str(v or "")).split()
    return (t[0][:20].capitalize()) if t else ""


def with_caller(res, caller):
    """The prompts are written in Alejandro's voice. When someone else is on the call (Carlos, 11-19: the
    card told him to say "It's Alejandro with Biscayne Solutions Group"), the line says the caller's name."""
    c = _caller_name(caller)
    if not c or c == "Alejandro" or not isinstance(res, dict):
        return res
    out = dict(res)
    for k in ("line", "note"):
        if isinstance(out.get(k), str):
            out[k] = re.sub(r"\bAlejandro\b", c, out[k])
    return out


def lead_card(r):
    """The fields the coach actually shows on a call."""
    return {
        "case": r.get("Case #"),
        "address": re.sub(r"\bFL-\s*", "FL ", str(r.get("Address") or "")) or None,
        "owners": r.get("owners"),
        "first": greet_first(r.get("owner_clean"), r.get("owners"), "MIAMI-DADE", r.get("Case #")),
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
        "first": greet_first(r.get("oname"), r.get("owners"), r.get("county"), r.get("case"),
                             officer=str(r.get("st") or "").upper() == "BAL"),
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


CALLS_PAGE_CSS = (
    "body{margin:0;background:#0e1216;color:#e6edf3;font:14px/1.45 system-ui,Segoe UI,sans-serif}"
    "header{position:sticky;top:0;background:#101a24;border-bottom:1px solid #1d4468;padding:12px 18px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}"
    "h1{font-size:18px;margin:0 10px 0 0;color:#ffd479}"
    "button,a.b{font:inherit;background:#132244;color:#e6edf3;border:1px solid #2a3f6b;border-radius:8px;padding:7px 12px;cursor:pointer;text-decoration:none}"
    "button.big{background:#c6a14b;color:#14203a;border-color:#c6a14b;font-weight:700}"
    "input{font:inherit;background:#0b1520;color:#e6edf3;border:1px solid #2a3f6b;border-radius:8px;padding:7px 10px;min-width:240px}"
    ".path{color:#8b98a6;font-size:12px}"
    "table{border-collapse:collapse;width:100%}th,td{padding:8px 10px;border-bottom:1px solid #1b2733;vertical-align:top;text-align:left}"
    "th{color:#8b98a6;font-weight:600;font-size:12px;position:sticky;top:58px;background:#0e1216}"
    "tr:hover td{background:#111c27}.oc{font-weight:700}.oc.appointment{color:#5fd38d}.oc.talked{color:#ffd479}"
    ".note{color:#bcdcff;font-size:12.5px;min-width:240px;max-width:520px}.dim{color:#5e6b78}"
    "td.when,td.len{white-space:nowrap}td.who{min-width:150px;overflow-wrap:anywhere}td.files{min-width:260px}"
    "td.files audio{height:32px;width:min(240px,100%);vertical-align:middle}table{table-layout:auto}"
    "pre{white-space:pre-wrap;background:#0b1520;border:1px solid #1d4468;border-radius:8px;padding:12px;font:13px/1.5 ui-monospace,Consolas,monospace}"
)


def _local_stamp(started, sid):
    """(sort_key, 'Nov 20 8:05 PM') in the LOCAL timezone. The folder stamp is local time and the
    meta stamp is UTC: showing one and sorting by the other put 23:56 under 01:05 (stress test #4)."""
    try:
        t = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        t = t.astimezone()
        return t.timestamp(), t.strftime("%b %d, %I:%M %p").replace(" 0", " ")
    except (ValueError, TypeError):
        try:
            t = datetime.strptime(sid[:15], "%Y-%m-%d_%H%M%S")
            return t.timestamp(), t.strftime("%b %d, %I:%M %p").replace(" 0", " ")
        except ValueError:
            return 0.0, ""


def _dur_text(m, d):
    """Length of a call: the saved figure, else ended-minus-started, else the transcript's last stamp."""
    v = m.get("duration_s")
    if not isinstance(v, (int, float)):
        try:
            a = datetime.fromisoformat(str(m.get("started_utc")).replace("Z", "+00:00"))
            b = datetime.fromisoformat(str(m.get("ended_utc") or m.get("checkpoint_utc")).replace("Z", "+00:00"))
            v = (b - a).total_seconds()
        except (ValueError, TypeError):
            v = None
    if not isinstance(v, (int, float)):
        try:
            with open(os.path.join(d, "transcript.txt"), "r", encoding="utf-8") as f:
                last = [ln for ln in f if ln.startswith("[")][-1]
            mm = re.match(r"\[(\d+):(\d+)\]", last)
            v = int(mm.group(1)) * 60 + int(mm.group(2)) if mm else None
        except (OSError, IndexError):
            v = None
    return ("%d:%02d" % divmod(int(v), 60)) if isinstance(v, (int, float)) and v >= 0 else ""


def calls_index(limit=300, show_all=False):
    """Every recorded call, newest first: when, how long, which lead, what happened, and what is on disk."""
    out = []
    try:
        names = sorted((n for n in os.listdir(SESSIONS) if os.path.isdir(os.path.join(SESSIONS, n))), reverse=True)
    except OSError:
        names = []
    for sid in names[:limit]:
        d = os.path.join(SESSIONS, sid)
        try:
            with open(os.path.join(d, "meta.json"), "r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            m = {}
        w = m.get("wrapup") or {}
        ai = w.get("ai") if isinstance(w, dict) else {}
        if not show_all and re.match(r"(?i)test[-_]", str(m.get("case") or "") or sid.split("_", 2)[-1]):
            continue                      # leftover test rows are not calls (stress test #17)
        _key, _when = _local_stamp(m.get("started_utc"), sid)
        out.append({
            "sid": sid, "started": m.get("started_utc") or "", "dur": m.get("duration_s"),
            "key": _key, "when": _when, "length": _dur_text(m, d),
            "case": m.get("case") or "", "address": m.get("address") or "", "owners": m.get("owners") or "",
            "caller": m.get("caller") or "",
            "outcome": m.get("outcome") or (w.get("picked") if isinstance(w, dict) else "") or "",
            "note": (w.get("note") if isinstance(w, dict) else "") or (ai or {}).get("note") or "",
            "objection": (ai or {}).get("objection") or "",
            "audio": os.path.isfile(os.path.join(d, "audio.webm")),
            "transcript": os.path.isfile(os.path.join(d, "transcript.txt")),
            "coach_lines": len(m.get("coach_fires") or []),
        })
    out.sort(key=lambda c: (c["key"], c["sid"]), reverse=True)
    return out


def calls_page():
    """The Call recordings page: one row per call, open the transcript or play the audio here, or open
    the folder in Explorer. Local only (127.0.0.1), the same machine the recordings are on."""
    import html as _h
    e = lambda v: _h.escape(str(v or ""), quote=True)
    rows, empty = [], 0
    for c in calls_index():
        if not c["audio"] and not c["transcript"]:
            empty += 1
            hide = " class='emptyrow' style='display:none'"
        else:
            hide = ""
        lead = (e(c["case"]) + ("<br><span class='dim'>" + e(c["address"]) + "</span>" if c["address"] else "")) or "<span class='dim'>no prospect</span>"
        tr = ("<a class='b' target='_blank' href='/calls/%s/transcript'>Transcript</a>" % e(c["sid"])) if c["transcript"] else ""
        au = ("<audio preload='metadata' controls src='/calls/%s/audio'></audio> <a class='b' download='%s.webm' "
              "href='/calls/%s/audio' title='Saves as a .webm file any player opens'>&#8595;</a>"
              % (e(c["sid"]), e(c["sid"]), e(c["sid"]))) if c["audio"] else ""
        files = (tr + ("<div style='margin-top:12px;clear:both'>" + au + "</div>" if au else "")) or "<span class='dim'>nothing recorded</span>"
        rows.append("<tr%s data-q='%s'><td class='when'>%s</td><td class='len'>%s</td><td class='who'>%s</td><td class='oc %s'>%s</td>"
                    "<td class='note'>%s%s</td><td class='files'>%s</td></tr>" % (
                        hide, e((c["case"] + " " + c["address"] + " " + c["owners"] + " " + c["note"] + " " + c["sid"]).lower()),
                        e(c["when"]), e(c["length"]), lead, e(c["outcome"]), e(c["outcome"] or "-"),
                        e(c["note"][:400]), ("<br><span class='dim'>Objection: " + e(c["objection"]) + "</span>") if c["objection"] else "",
                        files))
    shown = len(rows) - empty
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Call recordings</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'><style>__CSS__</style></head><body>"
            "<header><h1>&#128193; Call recordings &middot; __N__</h1>"
            "<button class='big' onclick=\"fetch('/api/open_sessions',{method:'POST'}).then(r=>r.json()).then(j=>{if(!j.ok)alert(j.error||'Could not open the folder')})\">Open the folder</button>"
            "<input id='f' placeholder='Filter: case, street, name, outcome'>"
            "<a class='b' href='/'>&larr; Back to the coach</a>__EMPTYBTN__<span class='path' title='__DIR__'>call_sessions folder on this laptop</span></header>"
            "<table><thead><tr><th>Date</th><th>Length</th><th>Prospect</th><th>Outcome</th><th>Note</th><th>Files</th></tr></thead><tbody>__ROWS__</tbody></table>"
            "<script>(function(){var f=document.getElementById('f');"
            "function apply(){var q=(f.value||'').toLowerCase(),all=document.getElementById('showempty')&&document.getElementById('showempty').dataset.on==='1';"
            "document.querySelectorAll('tbody tr').forEach(function(r){var em=r.classList.contains('emptyrow');"
            "r.style.display=((em&&!all)||(q&&r.dataset.q.indexOf(q)<0))?'none':'';});}"
            "f.addEventListener('input',apply);window.addEventListener('pageshow',apply);apply();"
            "var se=document.getElementById('showempty');if(se)se.onclick=function(){this.dataset.on=this.dataset.on==='1'?'0':'1';"
            "this.textContent=this.dataset.on==='1'?'Hide empty':'Show empty (__EMPTYN__)';apply();};"
            "document.querySelectorAll('audio').forEach(function(a){a.addEventListener('loadedmetadata',function(){"
            "if(a.duration===Infinity||isNaN(a.duration)){a.currentTime=1e101;a.addEventListener('timeupdate',function fix(){"
            "a.removeEventListener('timeupdate',fix);a.currentTime=0;});}});});})();</script>"
            "</body></html>").replace("__CSS__", CALLS_PAGE_CSS).replace("__N__", str(shown)).replace("__DIR__", e(SESSIONS)).replace(
                "__EMPTYBTN__", ("<button id='showempty' data-on='0' style='margin-left:6px'>Show empty (%d)</button>" % empty) if empty else "").replace(
                "__EMPTYN__", str(empty)).replace(
                "__ROWS__", "".join(rows) or "<tr><td colspan=6 class='dim'>No calls recorded yet.</td></tr>")


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
    with _meta_lock:
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
    with _meta_lock:
        return _end_session(d, sid, payload)


def _end_session(d, sid, payload):
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
    m["outcome"] = outcome_code(payload.get("outcome"))
    m["wrapup"] = payload.get("wrapup")        # the AI draft and what the caller kept of it
    if payload.get("ai_stats") is not None:
        m["ai_stats"] = payload.get("ai_stats")  # asks, lines shown, and why the others were not
    m.pop("checkpoint_utc", None)
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


def checkpoint_session(sid, payload):
    """Save the call-so-far into meta.json without ending it. Forensics 11-18: 28 of 64 sessions were never
    ended (tab reloaded or closed, next lead opened), and those kept no transcript and no coach_fires, so
    ~60% of all AI asks left no trace. The page now checkpoints every minute and on pagehide. A checkpoint
    never touches a session that has ended and never shortens what is already saved. {} when skipped."""
    d = _sdir(sid)
    if not d:
        return None
    mp = os.path.join(d, "meta.json")
    with _meta_lock:
        try:
            with open(mp, "r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            m = {"session_id": sid}
        if m.get("ended_utc"):
            return {}
        fires = payload.get("coach_fires")
        if not isinstance(fires, list) or len(fires) < len(m.get("coach_fires") or []):
            return {}
        m["checkpoint_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for k in ("duration_s", "stages_visited", "objections_fired", "redflags_fired", "ai_stats"):
            if payload.get(k) is not None:
                m[k] = payload.get(k)
        m["coach_fires"] = fires
        _write_json(mp, m)
    lines = payload.get("lines")
    if isinstance(lines, list) and lines:
        tp = os.path.join(d, "transcript.json")
        try:
            with open(tp, "r", encoding="utf-8") as f:
                have = len(json.load(f).get("lines") or [])
        except (OSError, ValueError, AttributeError):
            have = 0
        if len(lines) >= have:
            save_transcript(sid, {"lines": lines})
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
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                # The page went away (reload, next call) while its answer was being written: 10 tracebacks
                # in coach_server.log by 11-18. Nothing to do but note it in one line.
                sys.stderr.write("[coach] client gone before the reply to %s\n" % (self.path.split("?")[0],))
                self.close_connection = True

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

        if p == "/calls":
            return self._send(200, calls_page(), "text/html; charset=utf-8", private=True)
        if p == "/api/calls":
            return self._send(200, {"dir": SESSIONS, "calls": calls_index()}, private=True)
        if p.startswith("/calls/"):
            _seg = [unquote(x) for x in p.strip("/").split("/")]
            _d = _sdir(_seg[1]) if len(_seg) == 3 else None
            if not _d:
                return self._send(404, {"error": "no such call"}, private=True)
            if _seg[2] == "transcript":
                try:
                    _tags = {"you": "YOU", "you?": "YOU?", "them": "THEM", "them?": "THEM?"}
                    _txt = None
                    try:
                        with open(os.path.join(_d, "transcript.json"), "r", encoding="utf-8") as jf:
                            _lines = (json.load(jf) or {}).get("lines") or []
                        if _lines:
                            _txt = "\n".join("[%s] %s%s" % (l.get("t", ""), (_tags.get(str(l.get("spk") or ""), "") + ": ") if _tags.get(str(l.get("spk") or "")) else "", l.get("text", "")) for l in _lines)
                    except (OSError, ValueError, AttributeError):
                        _txt = None
                    with open(os.path.join(_d, "transcript.txt"), "r", encoding="utf-8") as f:
                        _plain = f.read()
                        import html as _h
                        return self._send(200, "<!doctype html><meta charset='utf-8'><title>%s</title><style>%s</style><body style='padding:18px'>"
                                          "<a class='b' href='/calls'>&larr; All calls</a><h1 style='margin:14px 0'>%s</h1><pre>%s</pre></body>" % (
                                              _h.escape(_seg[1]), CALLS_PAGE_CSS, _h.escape(_seg[1]), _h.escape(_txt if _txt else _plain)),
                                          "text/html; charset=utf-8", private=True)
                except OSError:
                    return self._send(404, {"error": "no transcript"}, private=True)
            if _seg[2] == "audio":
                try:
                    with open(os.path.join(_d, "audio.webm"), "rb") as f:
                        body = f.read()
                except OSError:
                    return self._send(404, {"error": "no audio"}, private=True)
                # A download from the player saved as "audio.weba", which Windows cannot open
                # (2026-11-20). Name the file after the call and keep the extension players know.
                self.send_response(200)
                self.send_header("Content-Type", "audio/webm")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", 'inline; filename="%s.webm"' % _seg[1])
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    try:
                        self.wfile.write(body)
                    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                        self.close_connection = True
                return
            return self._send(404, {"error": "not found"}, private=True)

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

        if p == "/api/open_sessions":
            own = ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)
            origin, site = self.headers.get("Origin"), self.headers.get("Sec-Fetch-Site")
            if (self.headers.get("Host") not in own or (origin and origin not in ["http://" + o for o in own])
                    or (site and site != "same-origin")):
                return self._send(403, {"error": "same-origin only"}, private=True)
            try:
                os.makedirs(SESSIONS, exist_ok=True)
                if hasattr(os, "startfile"):
                    os.startfile(SESSIONS)          # Explorer, on the folder only: no path from the request is used
                    return self._send(200, {"ok": True, "dir": SESSIONS}, private=True)
                return self._send(200, {"ok": False, "error": "Open it by hand: " + SESSIONS}, private=True)
            except OSError as ex:
                return self._send(200, {"ok": False, "error": "Could not open %s (%s)" % (SESSIONS, type(ex).__name__)}, private=True)

        if p == "/api/suggest":
            # The coach page's own request only (same rule as /api/dgkey): this spends the key.
            own = ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)
            origin, site = self.headers.get("Origin"), self.headers.get("Sec-Fetch-Site")
            if (self.headers.get("Host") not in own or (origin and origin not in ["http://" + o for o in own])
                    or (site and site != "same-origin")):
                return self._send(403, {"error": "same-origin only"}, private=True)
            b = self._json_body()
            t0 = time.time()
            stat = {}
            try:
                res = ai_line(b.get("lines"), b.get("lang") or "", b.get("shown") or "", b.get("asked") or [], b.get("days"),
                              b.get("lane") or "", b.get("eq") or "", stat=stat)
                res = with_caller(res, b.get("caller"))
            except Exception as e:            # a bug in the line rules must not 500 the page mid-call
                res, stat["why"] = {}, "error:" + type(e).__name__
            ms = int((time.time() - t0) * 1000)
            res = dict(res or {"line": ""}, ms=ms)
            if not res.get("line"):
                res["why"] = stat.get("why") or "empty"
            # One line per ask, so a blank box can be traced afterwards. Codes and timings only, never words.
            # 11-19: plus where the time and the tokens go: ttfb/total of the last try, prompt tokens (the
            # estimate, and Venice's own count with how much of it came from the cache), and which situational
            # pieces rode along. Numbers and section names only.
            _n = lambda k: "-" if stat.get(k) is None else str(stat.get(k))
            sys.stderr.write("[coach] suggest %s ms=%d tries=%s model=%s%s ttfb=%s total=%s est=%s pt=%s cached=%s ct=%s sit=%s%s\n" % (
                "shown" if res.get("line") else "blank why=" + res["why"], ms, stat.get("tries", 0),
                stat.get("model", "-"), (" err=" + stat["err"]) if stat.get("err") else "",
                _n("ttfb"), _n("total"), _n("ptok_est"), _n("ptok"), _n("cached"), _n("ctok"),
                len(stat.get("sit") or []), " trimmed" if stat.get("trimmed") else ""))
            return self._send(200, res, private=True)

        if p == "/api/wrapup":
            own = ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)
            origin, site = self.headers.get("Origin"), self.headers.get("Sec-Fetch-Site")
            if (self.headers.get("Host") not in own or (origin and origin not in ["http://" + o for o in own])
                    or (site and site != "same-origin")):
                return self._send(403, {"error": "same-origin only"}, private=True)
            b = self._json_body()
            return self._send(200, with_caller(ai_wrapup(b.get("lines"), str(b.get("lane") or "").lower()) or {"outcome": ""}, b.get("caller")), private=True)

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
            if verb == "checkpoint":
                m = checkpoint_session(sid, self._json_body())
                if m is None:
                    return self._send(404, {"error": "unknown session"})
                return self._send(200, {"ok": True, "saved": bool(m)})
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