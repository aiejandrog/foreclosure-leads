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
]
for line, d, lane, eq, conv, want in CASES:
    got = S.fact_check(line, d, lane, eq, conv)[0]
    rec('%-14s %s' % (want or 'allowed', line[:70]), got == want, got)

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
