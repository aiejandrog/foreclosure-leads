"""_closecaptest -- the time-close is asked twice then retired, and the lawyer is never questioned.

Run:  python _closecaptest.py   (exit 0 = safe; no network, no model)

WHY (2026-11-20 call, 403s): the card ended in the same would-later-today close TEN times in six
minutes while the man said "I would like to hang up"; and "Do you need one? No." about his lawyer
was ad-libbed on the same call.
"""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import call_coach_server as S
FAILS = []
def rec(n, ok, x=''):
    print(('ok   ' if ok else 'FAIL ') + n + ((' | ' + str(x)[:220]) if x and not ok else ''))
    if not ok: FAILS.append(n)

A2 = ["Selling is what he handles. Would later today or first thing tomorrow work better?",
      "He lays out options. Would later today or first thing tomorrow work better?"]
NO = ["THEM: I'm not available to speak on this."]
L = "I get it, you're not up for a long talk. What time tomorrow works best for you?"
rec('first and second asks pass untouched', S.close_cap(L, [], NO) == L and S.close_cap(L, A2[:1], NO) == L)
rec('the third ask is cut, the answer half stays', S.close_cap(L, A2, NO) == "I get it, you're not up for a long talk.")
rec('a pure-close line becomes the walk-away', 'Save my number' in S.close_cap("Would later today or first thing tomorrow work better?", A2, NO))
rec('a yes or a time from them un-retires the close', S.close_cap(L, A2, ["THEM: okay tomorrow works"]) == L)
rec('a line with no close is never touched', S.close_cap("Keep him. Who is talking to the bank?", A2, NO) == "Keep him. Who is talking to the bank?")
rec('"what time are you available tomorrow" counts as the same close',
    S.close_cap("Fine. What time are you available tomorrow?", A2, NO) != "Fine. What time are you available tomorrow?")
for bad in ("He's there to file motions. Do you need one? No.", "You don't need a lawyer for this.",
            "Honestly, you should get rid of that attorney."):
    rec('never: %r' % bad[:40], S.never_say(bad)[0] == 'lawyer_needed', S.never_say(bad))
for good in ("Keep your lawyer, he handles the court side.", "Does your attorney know about the sale date?",
             "You need your lawyer on the court side."):
    rec('clean: %r' % good[:40], S.never_say(good)[0] in ('', 'playbook') or True if False else S.never_say(good)[0] != 'lawyer_needed', S.never_say(good))
page = open(os.path.join(HERE, 'call_coach_live.html'), encoding='utf-8').read()
rec('the red-flag bar fires when HE says it', 'NEVER question their lawyer' in page and re.search(r'do you need \(one\|him\|her\)', page) is not None)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
