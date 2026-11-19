"""_coachfactstest -- Call Coach fact_check: facts the coach was never given (replay grader, 2026-11-19).

Run:  python _coachfactstest.py   (offline; no model, no network)

The replay grader's top failure classes: a sale date when none is known, the advisor's availability, a clock
time nobody said, equity that is not verified, soft outcome promises, the advisor going to court himself.
Pins each rule, its allowed twin, and that no quoted line in the playbook trips fact_check on a lead with
a known sale and verified equity.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_coach_server as S  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


CASES = [
    ("He's open at ten tomorrow, does that work?", None, "", "", "", "availability"),
    ("Later today or first thing tomorrow, which works better?", None, "", "", "", ""),
    ("Perfect, four o'clock it is.", None, "", "", "can you do four", ""),
    ("Perfect, four o'clock it is.", None, "", "", "call me later", "time"),
    ("Perfect, three o'clock it is.", None, "", "", "call me at 3 pm", ""),
    ("Great, three thirty then.", None, "", "", "how about 3:30", ""),
    ("Great, at ten then.", None, "", "", "my case number is 2026", "time"),
    ("Before that sale date comes, let's get you five minutes with him.", None, "", "", "", "sale_date"),
    ("Before that sale date comes, let's get you five minutes with him.", 12, "fc", "", "", ""),
    ("Before the auction, let's talk.", None, "", "", "they said the auction is soon", ""),
    ("Before the auction, let's talk.", 12, "filing", "", "", "sale_date"),
    ("With all that equity, he'll show you options.", 12, "fc", "", "", "equity"),
    ("With all that equity, he'll show you options.", 12, "fc", "verified", "", ""),
    ("So you're not interested in saving your property, or the equity you've got in it?", 12, "fc", "", "", ""),
    ("Whatever you've got in the place, he'll go over it.", 12, "fc", "", "", ""),
    ("He'll ask the court for more time for you.", 12, "fc", "", "", "advisor_acts"),
    ("One option is to ask the court for more time, he'll walk you through it.", 12, "fc", "", "", ""),
    ("He makes sure the sale closes and you walk away clean.", 12, "fc", "", "", "promise"),
    ("You'll keep the house, don't worry.", 12, "fc", "", "", "promise"),
    ("Options to keep the house are what he lays out.", 12, "fc", "", "", ""),
    ("He'll get this handled for you.", 12, "fc", "", "", "promise"),
    ("He's got ten tomorrow morning open.", 12, "fc", "", "", "availability"),
    ("I've got him free later today.", 12, "fc", "", "", "availability"),
    ("He's got more than thirty years doing this, and he's free of charge.", 12, "fc", "", "", ""),
    ("He calls you ten tomorrow.", 12, "fc", "", "", "time"),
    ("There's a way out with money in your pocket.", 12, "fc", "", "", "equity"),
]
for line, d, lane, eq, conv, want in CASES:
    got = S.fact_check(line, d, lane, eq, conv)[0]
    rec('%-14s %s' % (want or 'allowed', line[:70]), got == want, got)

rec('two questions keep the statement and the last question',
    S.one_question("Got it. What's the plan? And how much of it is in writing?") == "Got it. And how much of it is in writing?")
rec('one question is left alone', S.one_question("That makes sense. Later today or first thing tomorrow?") == "That makes sense. Later today or first thing tomorrow?")
pb = S.playbook_text() or ''
quoted = [q for q in re.findall(r'"([^"]{12,300})"', pb)]
hits = [(q, S.fact_check(q, 12, 'fc', 'verified', q)) for q in quoted]
hits = [(q, h) for q, h in hits if h[0]]
rec('no quoted playbook line trips fact_check on a dated, verified lead (%d checked)' % len(quoted), not hits,
    [(q[:60], h[0]) for q, h in hits[:5]])
src = open(os.path.join(HERE, 'call_coach_server.py'), encoding='utf-8').read()
rec('ai_line checks every line with never_say AND fact_check', 'return r if r[0] else fact_check(line, days, lane, eq, _convo)' in src
    and src.count('check(res["line"])') >= 2)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)

# STILL READING: the caller is reading the line on his screen, so no model call and the screen stays.
_calls = []
_orig = S._ai_post
S._ai_post = lambda *a, **k: _calls.append(1) or (_ for _ in ()).throw(RuntimeError('model called'))
_shown = "Makes sense. Would later today or first thing tomorrow work better for the five minutes with our senior advisor?"
try:
    _r = S.ai_line([{'spk': 'them', 'text': 'I guess so.'},
                    {'spk': 'me', 'text': 'Makes sense would later today or first thing tomorrow work better for the five minutes with our senior advisor'}],
                   shown=_shown, asked=[_shown], stat={})
except Exception as _e:
    _r = {'err': str(_e)}
S._ai_post = _orig
print(('ok   ' if (_r == {} and not _calls) else 'FAIL ') + 'caller still reading the coach line: nothing new, no model call')
if not (_r == {} and not _calls):
    sys.exit(1)
