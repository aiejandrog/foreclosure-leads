"""Call Mode opener. Container-safe: no browser, no board.

2026-11-05 (Alejandro): the line after the opener ASKS instead of TELLS. No street, no sale date, no
"foreclosure": "I work with homeowners in {county} who have a case filed against the house. Is that
something you've already got handled, or worth two minutes?" {county} comes off the lead (r.ct), and
an unknown county says South Florida. "I have a lawyer" is a one-line exit to Do Not Contact.
Runs the real fillScript from call_mode._PAGE under node. Also pins the guided order.
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


chk(C._OPEN_ASK_EN == ("I work with homeowners in {county} who have a case filed against the house. Is that "
                       "something you've already got handled, or worth two minutes?"), 'EN ask is the 11-05 line')
chk('{county}' in C._OPEN_ASK_ES and 'dos minutos' in C._OPEN_ASK_ES, 'ES ask mirrors it')
for k in ('_OPEN_ASK_EN', '_OPEN_ASK_ES'):
    v = getattr(C, k)
    chk('{st1}' not in v and '{street}' not in v and '{date' not in v and 'foreclosure' not in v.lower(),
        k + ': no address, no sale date, no "foreclosure" (it read as "I know your business")')
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
chk(dis.get('Worth two minutes / what is it?') == 'nots' and dis.get("What case? / I didn't know") == 'news', 'worth it -> nots, what case -> news')
chk(dis.get('I have a lawyer handling it') == 'lawyer' and greet.get('I have a lawyer') == 'lawyer', 'lawyer at greet or ask -> exit')
chk(dict((b[0], b[1]) for b in F['nots']['br']).get('I have a lawyer handling it') == 'lawyer', 'lawyer at the nots -> exit')
chk(dict((b[0], b[1]) for b in F['news']['br']).get('I have a lawyer handling it') == 'lawyer', 'lawyer after news -> exit')
chk([b[1] for b in F['lawyer']['br']] == ['end:dnc'], 'lawyer exit logs Do Not Contact, nothing else')
chk("won't call you again" in C.LAWYER_EN and 'No le vuelvo a llamar' in C.LAWYER_ES, 'lawyer line says we stop')
for w in ('who is', 'which lawyer', 'what is the plan', 'but '):
    chk(w not in C.LAWYER_EN.lower(), 'lawyer line has no "%s"' % w)
chk(dict((b[0], b[1]) for b in F['news']['br']).get('Continue →') == 'nots', 'news -> nots')
chk(dict((b[0], b[1]) for b in F['nots']['br']).get('Okay / sure') == 'frame', 'nots -> frame')

for k in ('PHONE_OPENER_EN', 'PHONE_OPENER_ES', 'PHONE_OPENER_ANON_EN', 'PHONE_OPENER_ANON_ES'):
    v = getattr(C, k)
    cue = C.WAIT_EN if k.endswith('EN') else C.WAIT_ES
    chk(cue in v and v.index(cue) < v.index('{county}'), k + ': stop for the owner before the case line')
    chk('foreclosure' not in v.lower(), k + ': opener never says foreclosure')
chk(greet.get("Who's this? / who's asking?") == 'who', "who's asking -> confirm the owner, not the question")
chk('foreclosure' not in (F['who']['en'] + F['who']['es']).lower(), 'who step never says foreclosure')
chk(dict((b[0], b[1]) for b in F['who']['br']).get("Yes, that's me") == 'disarm', 'who -> yes -> question')

JS = (r"""
var SENDER={name:'Alejandro',phone:'(305) 555-0100'};
function firstName(r){return r.fn||'';} function _vmSale(){return '';}
""" + fn('fillScript') + r"""
var br={a:'99 SAMPLE DR, FORT LAUDERDALE, FL 33301',x:'11/05/2026',d:21,fn:'Maria',ct:'BR'};
var mi={a:'1240 NW 54 ST, MIAMI, FL 33142',x:'',d:9999,fn:'Maria',ct:'MI'};
var pb={a:'1 MAIN ST',ct:'PA'}; var no={a:'1 MAIN ST'};
var T=__T__;
console.log(JSON.stringify([fillScript(T.en,br),fillScript(T.en,mi),fillScript(T.en,pb),fillScript(T.en,no),fillScript(T.es,br)]));
""").replace('__T__', json.dumps({'en': C._OPEN_ASK_EN, 'es': C._OPEN_ASK_ES}))
out = json.loads(subprocess.run(['node', '-e', JS], capture_output=True, text=True, check=True).stdout)
for o in out:
    print('     ' + o)
chk(out[0].startswith('I work with homeowners in Broward who have a case filed'), 'Broward lead says Broward')
chk('in Miami-Dade who' in out[1] and 'in Palm Beach who' in out[2], 'Miami-Dade and Palm Beach named')
chk('in South Florida who' in out[3], 'unknown county: South Florida, never a wrong county')
chk('en Broward que' in out[4], 'ES says the county')
chk(all('SAMPLE' not in o and '11/05' not in o for o in out), 'rendered ask never says the street or the date')

print('\n%s -- %d failure(s)' % ('PASSED' if not bad else 'FAILED', len(bad)))
sys.exit(1 if bad else 0)
