"""Call Mode opener (2026-10-15, Alejandro's own line). Container-safe: no browser, no board.

Pins the wording he chose and its one accuracy rule: "pre-foreclosure" always, plus the sale date
only when an auction is actually set. Runs the real fillScript from call_mode._PAGE under node.
Also pins the guided order: the question, then STOP, then (if they did not know) the human line,
then the two nots.
"""
import json
import subprocess
import sys

import call_mode as C

P = C._PAGE
bad = []


def chk(c, m):
    print(('ok   ' if c else 'FAIL ') + m)
    if not c:
        bad.append(m)


def fn(name):
    # fillScript's strings hold '{date}' etc., so braces cannot be counted; it ends at a bare '}' line.
    i = P.index('function ' + name + '(')
    return P[i:P.index('\n}\n', i) + 2]


chk('Do you know your property on {st1} is in pre-foreclosure{date}?' in C._OPEN_ASK_EN, 'EN opener is his line')
chk('está en proceso de foreclosure{date_es}?' in C._OPEN_ASK_ES, 'ES opener carries the Spanish date token')
chk('{date}' not in C._OPEN_ASK_ES, 'ES opener never renders the English date clause')
chk('random' not in C._OPEN_BODY_EN and 'help me out' not in C._OPEN_BODY_EN, 'old disarm retired')
chk(C.PHONE_OPENER_EN.startswith("Hi, {first}? Can you hear me?"), "named opener is his line (2026-10-26)")
for k in ('PHONE_OPENER_EN', 'PHONE_OPENER_ES', 'PHONE_OPENER_ANON_EN', 'PHONE_OPENER_ANON_ES', 'GREET_EN', 'GREET_ES'):
    v = getattr(C, k).lower()
    chk('is this' not in v and 'hablo con' not in v and 'am i speaking' not in v, k + ': no "is this ...?" greeting')
chk(C._OPEN_BODY_EN.count('{sender}') == 0 and C.PHONE_OPENER_EN.count('{sender}') == 1, 'caller named once, in the greeting')
chk('not your lender' in C._OPEN_NOTS_EN and 'not calling to buy' in C._OPEN_NOTS_EN, 'two nots kept')
for w in ('stop the sale', 'save your home', 'fee', 'offer'):
    chk(w not in (C._OPEN_BODY_EN + C._OPEN_NEWS_EN).lower(), 'no "%s" in the opener' % w)

F = {s['id']: s for s in C.FLOW}
greet = dict((b[0], b[1]) for b in F['greet']['br'])
chk(greet.get("Good / hey / that's me") == 'disarm', 'greet -> his question')
chk(greet.get("That's not me / who?") == 'notme', 'wrong person -> fallback step')
nm = dict((b[0], b[1]) for b in F['notme']['br'])
chk('foreclosure' not in (F['notme']['en'] + F['notme']['es'] + F['notme']['aen'] + F['notme']['aes']).lower(), 'fallback never says foreclosure')
chk(nm.get('Oh, that is me') == 'disarm' and 'gate' in nm.values() and 'obj:16' in nm.values(), 'fallback routes: me / message / wrong number')
chk(F['greet']['aen'] == C.GREET_ANON_EN and '{first}' not in C.GREET_ANON_EN, 'no-name greeting never says {first}')
dis = dict((b[0], b[1]) for b in F['disarm']['br'])
chk(F['disarm']['en'] == C.INTRO_EN + ' ' + C._OPEN_ASK_EN and F['disarm']['aen'] == C._OPEN_ASK_EN, 'guided disarm: intro + question (named), question alone (no name)')
chk(dis.get("No, I didn't know") == 'news' and dis.get('Yes, I know') == 'nots', 'yes -> nots, no -> news')
chk(dict((b[0], b[1]) for b in F['news']['br']).get('Continue →') == 'nots', 'news -> nots')
chk(dict((b[0], b[1]) for b in F['nots']['br']).get('Okay / sure') == 'frame', 'nots -> frame')

for k in ('PHONE_OPENER_EN', 'PHONE_OPENER_ES', 'PHONE_OPENER_ANON_EN', 'PHONE_OPENER_ANON_ES'):
    v = getattr(C, k)
    cue = C.WAIT_EN if k.endswith('EN') else C.WAIT_ES
    chk(cue in v and v.index(cue) < v.lower().index('foreclosure'), k + ': stop for the owner before "foreclosure"')
chk(greet.get("Who's this? / who's asking?") == 'who', "who's asking -> confirm the owner, not the question")
chk('foreclosure' not in (F['who']['en'] + F['who']['es']).lower(), 'who step never says foreclosure')
chk(dict((b[0], b[1]) for b in F['who']['br']).get("Yes, that's me") == 'disarm', 'who -> yes -> question')

JS = (r"""
var SENDER={name:'Alejandro',phone:'(786) 631-1823'};
function firstName(r){return r.fn||'';} function _vmSale(){return '';}
""" + fn('fillScript') + r"""
var dated={a:'1240 NW 54 ST, MIAMI, FL 33142',x:'11/05/2026',d:21,fn:'Maria'};
var lp={a:'1240 NW 54 ST, MIAMI, FL 33142',x:'',d:9999,fn:'Maria'};
var T=__T__;
console.log(JSON.stringify([fillScript(T.en,dated),fillScript(T.en,lp),fillScript(T.es,dated),fillScript(T.es,lp)]));
""").replace('__T__', json.dumps({'en': C._OPEN_ASK_EN, 'es': C._OPEN_ASK_ES}))
out = json.loads(subprocess.run(['node', '-e', JS], capture_output=True, text=True, check=True).stdout)
for o in out:
    print('     ' + o)
chk(out[0].endswith('Do you know your property on 1240 NW 54 ST is in pre-foreclosure with a sale date of 11/05/2026?'), 'dated EN says the sale date')
chk(out[1].endswith('is in pre-foreclosure?'), 'LP EN: no date clause')
chk(out[2].endswith('está en proceso de foreclosure con fecha de subasta el 5 de noviembre?'), 'dated ES in Spanish, month spelled out')
chk(out[3].endswith('está en proceso de foreclosure?'), 'LP ES: no date clause')

print('\n%s -- %d failure(s)' % ('PASSED' if not bad else 'FAILED', len(bad)))
sys.exit(1 if bad else 0)
