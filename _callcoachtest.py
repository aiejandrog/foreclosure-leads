"""Live call coach (call_coach.py): container-safe, no browser, no board.

Pins: the page is written OUTSIDE the repo; nothing listens before the consent tap; no network and
no storage in the page; the script lines are call_mode's own; the matcher routes the common replies
to the right branch or card; the don't-say list catches the s.501.1377 phrases; and no line the
coach would put on screen contains one of them.
"""
import os
import re
import tempfile

import call_coach as K
import call_mode as C

bad = []


def chk(c, m):
    print(('ok   ' if c else 'FAIL ') + m)
    if not c:
        bad.append(m)


T = K.coach_table()
steps = {s['id']: s for s in T['steps']}

# --- the table is the flow -------------------------------------------------------------------
chk(set(steps) == {s['id'] for s in C._flow()}, 'every flow step is in the coach table')
chk(T['start'] == 'greet', 'starts at the opener')
chk(steps['greet']['en'] == C.GREET_EN and 'is this' not in steps['greet']['en'].lower(), 'opener is the assumed greeting')
chk({c['n'] for c in T['cards']} >= {15, 16, 17}, 'hard-coded objection cards present')
chk(T['never'] == list(C.NEVER_SAY), 'NEVER SAY list is call_mode\'s')
for s in T['steps']:
    for b in s['br']:
        chk(b['to'].startswith(('obj:', 'end:')) or b['to'] in steps, '%s -> %s resolves' % (s['id'], b['to']))
homeowner_br = [b for s in T['steps'] for b in s['br']
                if not re.search(r'hung up|left (it|my number)|booked|continue|felt it|opened up|read it back|'
                                 r'2nd or 3rd|declined|still (flat|stalling|won)|→', b['label'], re.I)]
empty = [b['label'] for b in homeowner_br if not b['kw']]
chk(not empty, 'every homeowner-side branch has keywords: %s' % empty[:5])

# --- the matcher -------------------------------------------------------------------------------
def m(step, text):
    return K.match(T, step, text)[1]

chk(m('greet', "who's this?") == 'who', "greet: who's this -> who")
chk(m('greet', 'you have the wrong number') == 'obj:16', 'greet: wrong number -> card 16')
chk(m('greet', "yeah that's me, I can hear you") == 'disarm', 'greet: yes -> disarm')
chk(m('disarm', "I'm not interested") == 'obj:15', 'disarm: not interested -> card 15')
chk(m('disarm', "I didn't know anything about that") == 'news', "disarm: didn't know -> news")
chk(m('disarm', 'I already have a lawyer handling it') == 'obj:1', 'disarm: lawyer -> card 1')
chk(m('disarm', 'are you trying to buy my house?') == 'defusebuy', 'disarm: buying -> defusebuy')
chk(m('disarm', "don't call me again") == 'end:dnc', 'disarm: do not call -> dnc')
chk(m('disarm', 'we already reinstated the loan') == 'reinstated', 'disarm: reinstated')
chk(m('nots', "I'm busy, can you call me back later") == 'busy', 'nots: busy -> busy')
chk(m('q1', 'we want to keep the house') == 'q2', 'q1: keep -> q2')
chk(m('q4', 'I already filed for bankruptcy', ) is not None, 'q4: bankruptcy routes')
chk(m('q6', 'just send me something in writing') == 'sendinfo', 'q6: send me something')
chk(m('q6', 'let me think about it') == 'think', 'q6: think about it')
chk(K.match(T, 'q3', 'the weather is nice')[0] is None, 'no hit on noise')

# --- don't-say -------------------------------------------------------------------------------
chk(K.dont_say_hits('we can stop the sale in 24 hours') == ['stop the sale', 'in 24 hours'], 'stop-the-sale promise caught')
chk(K.dont_say_hits('I guarantee it') == ['guarantee'], 'guarantee caught')
chk(not K.dont_say_hits('our senior advisor lays your options out, free'), 'clean line passes')
said = []
for s in T['steps']:
    said += [s.get('en', ''), s.get('es', ''), s.get('aen', ''), s.get('aes', '')]
    if s.get('rail'):
        said += s['rail'].get('en', []) + s['rail'].get('es', [])
for c in T['cards']:
    said += c['reb'] + [c['one']] + (c['es'].get('reb') or []) + [c['es'].get('one', '')]
dirty = [(l[:50], K.dont_say_hits(l)) for l in said if K.dont_say_hits(l)]
chk(not dirty, 'no scripted line trips the don\'t-say list: %s' % dirty[:3])

# --- the page ----------------------------------------------------------------------------------
page = K.render(T, sender='')
chk('__TABLE__' not in page and '__SENDER__' not in page, 'tokens substituted')
chk(K.CONSENT_EN in page and K.CONSENT_ES in page and '934.03' in page, 'consent line and statute on the gate')
js = page[page.index('<script>'):]
chk('fetch(' not in js and 'XMLHttpRequest' not in js and 'WebSocket' not in js and 'navigator.sendBeacon' not in js,
    'no network in the page')
chk('localStorage' not in js and 'sessionStorage' not in js and 'indexedDB' not in js, 'no storage in the page')
chk('MediaRecorder' not in js and 'getUserMedia' not in js, 'no audio recording API in the page')
chk("$('yes').onclick" in js and 'consent = true' in js and 'startMic()' in js, 'mic starts only from the yes tap')
chk(js.index('consent = true') < js.index('startMic(); }') and js.count('startMic();') == 1, 'startMic has one caller: the yes button')
chk('new SR()' in js and js.index('function startMic') < js.index('new SR()'), 'recognizer is built inside startMic')
chk('record_calls' not in js, 'page does not read the call-recording flag; consent is per call, by tap')

with tempfile.TemporaryDirectory() as d:
    p = K.build(os.path.join(d, 'coach.html'))
    chk(os.path.getsize(p) > 20000, 'build writes the page')
default = K.P.out(K.OUT_NAME)
repo = os.path.dirname(os.path.abspath(K.__file__))
chk(not os.path.abspath(default).startswith(repo + os.sep), 'default output is outside the repo: %s' % default)
chk(os.sep + 'docs' + os.sep not in default, 'default output is not under docs/')

print()
print('FAIL %d' % len(bad) if bad else 'ALL OK')
raise SystemExit(1 if bad else 0)
