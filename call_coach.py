"""Live call coach: a local page that listens to the speakerphone, transcribes in the browser, and
shows the next line from the Call Mode script. $0 proof of concept (2026-11-04).

WHAT IT IS NOT. Nothing here runs by default, records audio, stores a transcript, or calls a paid
service. `python call_coach.py` writes ONE file, `coach.html`, into DEALFLOW_DIR (never `docs/`,
never the public mirror), and that page does nothing until the caller taps the consent button.

LEGAL GATE (Fla. Stat. 934.03, all-party consent). Live transcription is interception, with or
without an audio file, so the page refuses to open the microphone until the caller has read the
consent line out loud (call_mode.CONSENT_LINE_EN / _ES, the Stage 0 line in CALL_SCRIPT_NEPQ.md)
and tapped "They said yes". A no, or no tap, leaves the page as a script viewer and the mic closed.
Wiring this into the call opener is the attorney's call (Patrick list); this module does not
change Call Mode.

HOW THE AUDIO GETS IN. Neither phone OS hands call audio to an app, so the phone sits on speaker and
the laptop microphone hears both sides. Chrome's built-in SpeechRecognition does the transcription
(no key, no account; Chrome sends the audio to Google's recognizer, which is the whole reason the
consent gate is in front of it). A paid streaming provider (Deepgram, AssemblyAI) is a later
decision with a number from Alejandro; there is no hook for one here.

HOW THE COACH DECIDES. No model. The transcript is matched, as lowercase substrings, against a
keyword table built from the guided flow in call_mode._flow() (every branch label) and the
objection cards (every "say" line). The best hit moves the page to that branch's target step and
shows its line, EN and ES, with the tone and listen notes. The table is data in the page; the
matcher (`match`) is mirrored in JS and pinned by _callcoachtest.py.

DON'T-SAY ALERT. Fla. Stat. 501.1377 and the script's NEVER SAY list: if the transcript carries a
stop-the-sale promise, a guarantee, a deadline, or a fee, the page shows a red bar. It cannot tell
the two speakers apart, so it fires on either side; that is the point.

NOTHING LEAVES THE PAGE. No fetch, no XHR, no localStorage for the transcript, no file write. The
transcript lives in the tab and is gone when the tab closes.
"""
import html
import json
import os
import re
import sys

import call_mode as C
import paths as P

OUT_NAME = 'coach.html'

# Stage 0 line, verbatim from CALL_SCRIPT_NEPQ.md. Read out loud BEFORE the coach listens.
CONSENT_EN = ("Quick thing before we start. I record my calls so I keep your file straight. "
              "That okay with you?")
CONSENT_ES = ("Una cosa rápida antes de empezar. Yo grabo mis llamadas para tener su expediente en "
              "orden. ¿Le parece bien?")

# Phrases that must not be said on a BSG call (s.501.1377, MARS, NEVER SAY). Lowercase substrings.
# Not here on purpose: 'deadline' and 'up front'. The script says both the right way ("the court sets
# the deadline", "never a cent up front"); what 501.1377 bans is the FAKE deadline and the fee.
DONT_SAY = [
    'stop the sale', 'stop the foreclosure', 'stop your foreclosure', 'in 24 hours', 'within hours',
    'guarantee', 'guaranteed', 'save your home', "we'll save", 'we will save',
    'by friday', 'by tomorrow', 'today only', 'last chance', 'offer expires', 'expires today', 'act now',
    'retainer', 'our fee', 'a fee of', 'pay us', 'my supervisor', 'our attorneys',
    'my clients', 'we place loans', 'place the loan',
    'parar la venta', 'detener la venta', 'garantizado', 'garantizamos', 'última oportunidad', 'ultima oportunidad',
]

# Hand-curated keywords for the branch labels the matcher cannot read from the label alone.
# Branch label (as in call_mode._flow) -> what the homeowner actually says. Lowercase substrings;
# multi-word phrases score higher than single words.
_LABEL_KW = {
    "Good / hey / that's me": ['yeah', 'yes', 'speaking', "that's me", 'this is he', 'this is she',
                               'go ahead', 'hello', 'hey', 'i can hear you'],
    "Who's this? / who's asking?": ["who's this", 'who is this', "who's asking", 'who is asking',
                                    "who's calling", 'who is calling'],
    "That's not me / who?": ["that's not me", 'not me', 'who?', 'wrong person'],
    'Wrong number': ['wrong number', 'wrong #', 'número equivocado', 'numero equivocado',
                     'no one by that name', 'nobody by that name'],
    'Not here / can I take a message?': ['not here', "isn't here", 'take a message', 'not available',
                                         "he's not", "she's not", 'at work'],
    "Their phone's AI answered (asks name + reason)": ['state your name', 'reason for your call',
                                                       'screening', 'who is calling and why'],
    'Straight to voicemail': ['leave a message', 'after the tone', 'voicemail', 'not available right now'],
    'Hung up on me': [],
    'Oh, that is me': ["that's me", 'this is him', 'this is her', 'speaking', 'yeah that is me'],
    "They're not here / I can take a message": ['not here', 'take a message', "isn't here"],
    "Who's asking? / what's it about?": ["who's asking", "what's it about", "what's this about",
                                         'what is this about', 'regarding'],
    'No one by that name / wrong number': ['wrong number', 'no one by that name', 'nobody by that name'],
    "Yes, that's me": ['yes', 'yeah', "that's me", 'speaking', 'this is him', 'this is her'],
    'No / not here': ['no', 'not here'],
    "What's it about?": ["what's it about", "what's this about", 'what is this about', 'regarding'],
    'Yes, I know': ['yes', 'yeah', 'i know', "i'm aware", 'i am aware', 'we know', 'i got the letter',
                    'of course'],
    "No, I didn't know": ["didn't know", 'did not know', 'no idea', 'what?', 'what do you mean',
                          'first i', "i wasn't aware", 'nobody told'],
    'What? / who is this?': ['who is this', "who's this", 'what?', 'excuse me'],
    'What do you do?': ['what do you do', 'what is it you do', 'what exactly', 'what company',
                        'what is biscayne'],
    'You buying my house?': ['buy my house', 'buying my house', 'buy the house', 'want to buy',
                             'trying to buy', 'investor', 'cash offer', 'wholesal'],
    'Are you an investor?': ['investor', 'are you an investor', 'you an investor', 'flipper', 'wholesal'],
    'Already selling it / finalized': ['already sold', 'already selling', 'under contract', 'we sold',
                                       'selling it', 'finalized', 'closing', 'listed it', 'on the market'],
    "I'm a realtor / selling it myself": ['realtor', 'real estate agent', "i'm an agent", 'selling it myself',
                                          'listed', 'broker'],
    'Not interested': ['not interested', 'no thanks', 'no thank you', "i'm good", 'im good', 'no me interesa',
                       'not for me', 'take me off'],
    'Still not interested': ['not interested', 'no thanks', "i'm good", 'still not', 'no me interesa'],
    "I'm busy, call me back": ['busy', 'call me back', 'call back', 'bad time', 'not a good time',
                               "can't talk", 'driving', 'at work', 'later'],
    "I've got a lawyer / the bank": ['lawyer', 'attorney', 'the bank', 'my bank', 'modification', 'loan mod',
                                     "it's handled", 'being handled', 'taken care of', 'abogado'],
    'We already reinstated': ['reinstated', 'reinstate', 'caught up', 'paid it', 'paid up', 'brought it current',
                              'current now', 'all paid'],
    "Don't call me again": ["don't call", 'do not call', 'stop calling', 'remove my number', 'take me off',
                            'lose my number', 'no me llame', 'no llame'],
    'Continue →': [],
    'Okay / sure': ['okay', 'ok', 'sure', 'go ahead', 'fine', 'alright', 'all right', 'yeah'],
    "What's this about? / what do you do?": ["what's this about", 'what do you do', 'what is this'],
    'Okay, go ahead': ['okay', 'go ahead', 'sure', 'fine', 'alright'],
    "I'm good, it's handled": ["i'm good", 'handled', 'taken care of', "it's fine", 'under control'],
    'Okay... so what is it?': ['so what is it', 'what is it then', 'okay', 'what do you want'],
    'Okay, what is it?': ['what is it', 'okay', 'go on'],
    '"Closed" in a month (tight)': ['closing in', 'close in', 'a month', 'thirty days', '30 days', 'next month'],
    '"Under contract", plenty of time': ['under contract', 'plenty of time', 'in escrow'],
    "It's handled, don't worry": ['handled', "don't worry", 'taken care of', 'under control'],
    'Signed / under contract / finalized': ['signed', 'under contract', 'finalized', 'contract'],
    'Not signed yet / still working it out': ['not signed', "haven't signed", 'working it out', 'not yet',
                                              'still talking', 'offer'],
    'Selling it myself / with a realtor': ['myself', 'realtor', 'agent', 'listed'],
    'Gave me the closing date / okay to check back': ['closing date', 'closes on', 'check back', 'call back then'],
    'Open to a second number': ['second number', 'another offer', 'sure', 'why not', "i'd listen", 'what number'],
    'Just send me something': ['send me', 'email me', 'mail me', 'in writing', 'send it', 'text me the info'],
    "No, I'm happy with it": ['happy with', "i'm fine", "i'm good", 'no thanks', "we're good"],
    'Closed / dismissed, all done': ['closed', 'dismissed', 'all done', 'over', 'done with it'],
    'Still open / not sure': ['still open', 'not sure', "don't know", 'no idea', 'i think so'],
    "Sure / that'd help": ['sure', 'that would help', "that'd help", 'okay', 'yes', 'go ahead', 'i guess'],
    'What kind of options? (do NOT present — ask)': ['what options', 'what kind of options', 'like what',
                                                      'what can you do', 'options?'],
    "I've got it handled": ['handled', 'got it', 'under control', 'taken care'],
    'Keep the house': ['keep the house', 'keep my house', 'keep it', 'stay', 'save the house', 'save my house',
                       'keep my home', 'not lose'],
    'Sell / get out / start fresh': ['sell', 'get out', 'start fresh', 'move on', 'walk away', 'be done',
                                     'get rid'],
    "Selling it myself / I'm a realtor": ['selling it myself', 'realtor', 'agent'],
    "I don't know": ["don't know", 'not sure', 'no idea', 'i guess'],
    "It's handled / bank / mod": ['handled', 'the bank', 'modification', 'loan mod', 'working with the bank'],
    'They opened up': [],
    "Still won't engage": [],
    'No, nobody explained it': ['no', 'nobody', 'no one', "didn't explain", 'not really', 'never explained'],
    "Yeah, they told me (ask: what'd they tell you?)": ['they told me', 'they said', 'yeah they', 'explained'],
    "It'll just get pushed again": ['pushed', 'postponed', 'push it', 'they keep moving', 'moved again',
                                    'delayed'],
    'Moving target / no idea': ['moving target', 'no idea', "don't know", 'not sure', 'keeps changing',
                                'keeps going up'],
    'I know the number': ['i know', 'the number is', 'about', 'thousand', 'owe'],
    "I'm fine, don't worry about it": ["i'm fine", "don't worry", "we're fine", 'all good'],
    'Bank mod': ['modification', 'loan mod', 'the bank', 'working with the bank', 'forbearance'],
    'Lawyer': ['lawyer', 'attorney', 'abogado', 'legal aid'],
    "Money's coming (check / family)": ['money coming', 'check coming', 'family', 'my brother', 'my son',
                                        'my daughter', 'inheritance', 'settlement', 'loan from'],
    'Already filed bankruptcy': ['filed bankruptcy', 'filed for bankruptcy', 'in bankruptcy', 'chapter 13',
                                 'chapter 7', 'bankruptcy attorney'],
    'Planning to file bankruptcy': ['going to file', 'planning to file', 'thinking about bankruptcy',
                                    'might file', 'file bankruptcy'],
    'They said the pain (kids / wreck me / no plan B)': ['kids', 'my children', 'wreck', 'nowhere to go',
                                                         'no plan', 'devastat', 'lose everything', 'homeless',
                                                         'my mother', 'my family'],
    '"I\'m fine, it\'ll work out" (flat)': ["it'll work out", "i'm fine", 'work itself out', "we'll see",
                                             "we'll figure"],
    'Too late, nothing I can do': ['too late', 'nothing i can do', "can't do anything", 'hopeless', 'given up'],
    'They felt it': [],
    'Still flat / not moving': [],
    'Continue to the ask →': [],
    'Later today': ['later today', 'this afternoon', 'tonight', 'this evening', 'today'],
    'Tomorrow morning': ['tomorrow', 'morning', 'first thing'],
    'Let me think about it': ['think about it', 'let me think', 'think it over', 'sleep on it'],
    "I'm busy right now": ['busy', "can't right now", 'not right now', 'later'],
    "No thanks, I'm good": ['no thanks', "i'm good", 'no thank you', 'pass'],
    'They told me what they want to see → back to the questions': ['i want to see', 'show me', 'what it costs',
                                                                    'how it works', 'the numbers'],
    'They named a concern → handle it, re-ask': ['scam', 'legit', 'trust', 'worried', 'concern', 'how do i know'],
    'Fine, just send it': ['just send it', 'send it', 'whatever'],
    'They named the concern → answer it, re-ask': ['scam', 'legit', 'trust', 'worried', 'my wife', 'my husband',
                                                   'talk to my'],
    'Still stalling': [],
    'Gave me a time': ['call me at', 'tomorrow at', 'around', "o'clock", 'after', 'pm', 'am'],
    'Booked a slot': [],
    'Brushed me off': ['no', 'whatever', 'bye'],
    'Okay, fine — five minutes': ['fine', 'okay', 'five minutes', 'go ahead', 'alright'],
    'No': ['no', 'nope', 'nah'],
    'Stop calling me': ['stop calling', "don't call", 'do not call', 'take me off'],
    'Read it back clean ✓': [],
    'Fumbled it / "pen ran out" (log it, count it soft)': ['pen', 'hold on', 'wait', 'say that again'],
    'Backed out': ['never mind', 'nevermind', 'forget it', 'changed my mind', 'actually no'],
    'Got a callback time': ['call back', 'try again', 'later', 'tomorrow', 'after'],
    'Left my number': [],
    'Hung up': [],
    'They picked up': ['hello', 'hi', 'yeah', 'speaking'],
    'It asked me to leave a message': ['leave a message', 'after the tone', 'voicemail'],
    'Declined / hung up': [],
    'This is my 2nd or 3rd try': [],
    'Left it': [],
}

_STOP = {'the', 'a', 'an', 'me', 'my', 'it', 'i', 'to', 'of', 'in', 'on', 'is', "i'm", 'im', 'you', 'and', 'or',
         'so', 'do', 'not', 'what', 'who', 'that', 'this', 'with', 'for', 'be', 'got', 'okay', 'ok'}


def label_keywords(label):
    """Keywords for a branch label: the curated list, else the label's own words (no stop words,
    4+ letters), lowercase. Branches that describe the CALLER's own action (hung up, left it) get
    none on purpose: nothing the homeowner says should trigger them."""
    if label in _LABEL_KW:
        return list(_LABEL_KW[label])
    words = re.findall(r"[a-záéíóúñ']+", label.lower())
    return [w for w in words if w not in _STOP and len(w) >= 4]


def _say_keywords(card):
    """Keywords for an objection card: its "say" line, split on / and punctuation, plus the ES one."""
    out = []
    for s in (card.get('say', ''), (card.get('es') or {}).get('say', '')):
        s = re.sub(r'\[.*?\]', ' ', s or '').lower()
        for part in re.split(r'[/.,;!?]+', s):
            part = part.strip(" '\"")
            if len(part) >= 4:
                out.append(part)
    return out


def coach_table():
    """The data the page runs on. Script lines are the SAME strings Call Mode renders (call_mode is
    the only source); this adds keywords, the consent line and the don't-say list."""
    steps = []
    for s in C._flow():
        st = {'id': s['id'], 'k': s['k'], 'tone': s.get('tone', ''), 'listen': s.get('listen', ''),
              'ret': s.get('ret', '')}
        if 'rail' in s:
            st['rail'] = s['rail']
        else:
            st['en'], st['es'] = s['en'], s['es']
            if 'aen' in s:
                st['aen'], st['aes'] = s['aen'], s['aes']
        st['br'] = [{'label': lbl, 'to': tgt, 'kw': label_keywords(lbl)} for lbl, tgt in s.get('br', [])]
        steps.append(st)
    cards = []
    for c in C.objection_cards():
        cards.append({'n': c['n'], 't': c['t'], 'say': c.get('say', ''), 'reb': c.get('reb', []),
                      'one': c.get('one', ''), 'es': c.get('es') or {}, 'kw': _say_keywords(c)})
    return {'steps': steps, 'cards': cards, 'never': list(C.NEVER_SAY), 'dont': list(DONT_SAY),
            'consent': {'en': CONSENT_EN, 'es': CONSENT_ES}, 'start': 'greet'}


def _score(text, kws):
    """Sum of matched keyword lengths: a longer phrase beats a stray word, two hits beat one."""
    return sum(len(k) for k in kws if k and k in text)


def match(table, step_id, text):
    """Reference matcher, mirrored in the page's JS (`coachMatch`). Returns (kind, target, score)
    where kind is 'br' (a branch of the current step), 'obj' (an objection card from anywhere),
    or None. The current step's branches win ties; a card needs a clearly stronger hit."""
    text = ' ' + re.sub(r'\s+', ' ', (text or '').lower()).strip() + ' '
    best = (None, None, 0)
    step = next((s for s in table['steps'] if s['id'] == step_id), None)
    if step:
        for b in step['br']:
            sc = _score(text, b['kw'])
            if sc > best[2]:
                best = ('br', b['to'], sc)
    for c in table['cards']:
        sc = _score(text, c['kw'])
        if sc > best[2] + 3:
            best = ('obj', 'obj:%d' % c['n'], sc)
    return best


def dont_say_hits(text):
    t = (text or '').lower()
    return [p for p in DONT_SAY if p in t]


def _sender_name():
    """Operator first name from the untracked sender.json, for the {sender} token. Blank if absent;
    the page then shows an input. This file never leaves DEALFLOW_DIR, so a real name here is fine."""
    try:
        import entity
        return (entity.sender().get('name') or '').split(' ')[0]
    except Exception:
        return ''


_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BSG Call Coach (local)</title>
<style>
:root{--bg:#0f1419;--card:#1a2129;--ink:#e8edf2;--dim:#8a97a6;--ok:#2ecc71;--warn:#f39c12;--bad:#e74c3c;--acc:#3b82f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:10px 16px;border-bottom:1px solid #2a3340;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header b{font-size:18px}.pill{padding:3px 10px;border-radius:999px;font-size:13px;background:#2a3340}
.pill.on{background:var(--ok);color:#000}.pill.off{background:#3a4553}
main{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px}
@media(max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border-radius:10px;padding:14px}
h2{margin:0 0 8px;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
#gate{grid-column:1/-1;border:2px solid var(--warn)}
#gate p.line{font-size:20px;margin:8px 0}
button{font:inherit;padding:10px 16px;border-radius:8px;border:0;cursor:pointer;background:#2a3340;color:var(--ink)}
button.yes{background:var(--ok);color:#000;font-weight:600}button.no{background:var(--bad)}
button.br{display:block;width:100%;text-align:left;margin:4px 0;padding:8px 10px;font-size:14px}
button.br.hit{outline:2px solid var(--acc);background:#1e3a5f}
#say{font-size:24px;line-height:1.35;margin:6px 0 10px}#sayes{color:#cbd5e1;font-size:17px}
.tone{color:var(--dim);font-size:14px}.listen{color:var(--warn);font-size:14px}
#alert{display:none;grid-column:1/-1;background:var(--bad);color:#fff;font-size:20px;font-weight:700;padding:12px}
#tx{height:38vh;overflow:auto;font-size:15px;white-space:pre-wrap;color:#cbd5e1}#tx i{color:var(--dim)}
input{font:inherit;padding:6px 8px;border-radius:6px;border:1px solid #3a4553;background:#0f1419;color:var(--ink);width:150px}
.never li{color:#f5b7b1;font-size:13px}.ret{font-size:13px;color:var(--dim)}
footer{padding:8px 16px;color:var(--dim);font-size:12px}
</style></head><body>
<header><b>BSG Call Coach</b> <span class="pill" id="mode">script only</span>
<label>First <input id="first" placeholder="first name" autocomplete="off"></label>
<label>Street <input id="st1" placeholder="street" autocomplete="off"></label>
<label>Me <input id="sender" value="__SENDER__" autocomplete="off"></label>
<label>Lang <select id="lang"><option value="en-US">EN</option><option value="es-US">ES</option></select></label>
<button id="reset">New call</button></header>
<main>
<section class="card" id="gate">
<h2>Before the coach listens: all-party consent (Fla. Stat. 934.03)</h2>
<p class="line" id="cen"></p><p class="line" id="ces" style="color:#cbd5e1;font-size:17px"></p>
<p>Read it out loud after "Can you hear me?". Nothing is captured until you tap yes. A no means the coach stays off and you work from Call Mode as usual.</p>
<button class="yes" id="yes">They said yes. Start listening</button>
<button class="no" id="no">No. Script only, mic stays closed</button>
<p class="tone" id="micst"></p>
</section>
<div id="alert"></div>
<section class="card">
<h2 id="stepk">Step</h2>
<div id="say"></div><div id="sayes"></div>
<p class="tone" id="tone"></p><p class="listen" id="listen"></p>
<div id="rail"></div>
<h2 style="margin-top:12px">What they said → tap it (or let the coach pick)</h2>
<div id="brs"></div><p class="ret" id="ret"></p>
</section>
<section class="card">
<h2>Live transcript (this tab only, nothing saved)</h2>
<div id="tx"><i>Mic is closed.</i></div>
<h2 style="margin-top:12px">Never say</h2><ul class="never" id="never"></ul>
</section>
</main>
<footer>Local file. No network, no storage, no recording. Phone on speaker, laptop mic. Transcription by the browser (Chrome). Close the tab and the transcript is gone.</footer>
<script>
const T = __TABLE__;
const $ = id => document.getElementById(id);
let cur = T.start, consent = false, rec = null, lastFinal = '', history = [];
$('cen').textContent = T.consent.en; $('ces').textContent = T.consent.es;
T.never.forEach(n => { const li = document.createElement('li'); li.textContent = n; $('never').appendChild(li); });

function fill(s) {
  const first = $('first').value.trim(), st1 = $('st1').value.trim() || 'the property', me = $('sender').value.trim() || 'me';
  return (s || '').replace(/\{first\}/g, first || 'there').replace(/\{st1\}/g, st1).replace(/\{sender\}/g, me)
    .replace(/\{date\}/g, '').replace(/\{date_es\}/g, '').replace(/\{phone\}/g, 'my number');
}
function step(id) { return T.steps.find(s => s.id === id); }
function card(n) { return T.cards.find(c => c.n === n); }

function show(target) {
  $('rail').innerHTML = ''; $('brs').innerHTML = ''; $('ret').textContent = '';
  if (target.startsWith('obj:')) {
    const c = card(+target.slice(4)); if (!c) return;
    $('stepk').textContent = 'OBJECTION ' + c.n + ' · ' + c.t;
    $('say').textContent = fill(c.reb[0] || c.one); $('sayes').textContent = fill((c.es.reb || [])[0] || c.es.one || '');
    $('tone').textContent = c.reb[1] ? 'Then: ' + fill(c.reb[1]) : ''; $('listen').textContent = 'One line: ' + fill(c.one);
    const s = step(cur); const back = (s && s.ret) || cur;
    const b = document.createElement('button'); b.className = 'br'; b.textContent = '← back to the script (' + back + ')';
    b.onclick = () => go(back); $('brs').appendChild(b);
    return;
  }
  if (target.startsWith('end:')) { $('stepk').textContent = 'END · ' + target.slice(4) + ' (log it in Call Mode)'; $('say').textContent = ''; $('sayes').textContent = ''; $('tone').textContent = ''; $('listen').textContent = ''; return; }
  const s = step(target); if (!s) return; cur = s.id;
  const anon = !$('first').value.trim();
  $('stepk').textContent = s.k;
  if (s.rail) { $('say').textContent = ''; $('sayes').textContent = '';
    s.rail.en.forEach((l, i) => { const d = document.createElement('div'); d.id = 'say'; d.style.fontSize = '19px'; d.textContent = fill(l); const e = document.createElement('div'); e.className = 'tone'; e.textContent = fill((s.rail.es || [])[i] || ''); $('rail').appendChild(d); $('rail').appendChild(e); });
  } else { $('say').textContent = fill(anon && s.aen ? s.aen : s.en); $('sayes').textContent = fill(anon && s.aes ? s.aes : s.es); }
  $('tone').textContent = s.tone; $('listen').textContent = s.listen;
  s.br.forEach(b => { const el = document.createElement('button'); el.className = 'br'; el.dataset.to = b.to; el.textContent = b.label + '  →  ' + b.to; el.onclick = () => go(b.to); $('brs').appendChild(el); });
  if (s.ret) $('ret').textContent = 'After an objection card, come back to: ' + s.ret;
}
function go(target) { history.push(cur); show(target); }

function coachMatch(stepId, text) {
  text = ' ' + text.toLowerCase().replace(/\s+/g, ' ').trim() + ' ';
  const score = kws => kws.reduce((a, k) => a + (k && text.includes(k) ? k.length : 0), 0);
  let best = [null, null, 0];
  const s = step(stepId);
  if (s) s.br.forEach(b => { const sc = score(b.kw); if (sc > best[2]) best = ['br', b.to, sc]; });
  T.cards.forEach(c => { const sc = score(c.kw); if (sc > best[2] + 3) best = ['obj', 'obj:' + c.n, sc]; });
  return best;
}
function dontSay(text) { const t = text.toLowerCase(); return T.dont.filter(p => t.includes(p)); }

function onFinal(text) {
  const tx = $('tx'); tx.innerHTML += (tx.innerHTML.startsWith('<i>') ? '' : '\n') + text; tx.scrollTop = tx.scrollHeight;
  const hits = dontSay(text);
  if (hits.length) { $('alert').style.display = 'block'; $('alert').textContent = 'DON\'T SAY: "' + hits.join('", "') + '". No promises, no deadlines, no fee. (s.501.1377)'; }
  const m = coachMatch(cur, text);
  if (m[0]) { document.querySelectorAll('#brs .br').forEach(b => b.classList.toggle('hit', b.dataset.to === m[1])); if (m[2] >= 6) go(m[1]); }
}

function startMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { $('micst').textContent = 'This browser has no built-in speech recognition. Use Chrome.'; return; }
  rec = new SR(); rec.continuous = true; rec.interimResults = true; rec.lang = $('lang').value;
  rec.onresult = ev => { for (let i = ev.resultIndex; i < ev.results.length; i++) { const r = ev.results[i]; if (r.isFinal) { const t = r[0].transcript.trim(); if (t && t !== lastFinal) { lastFinal = t; onFinal(t); } } } };
  rec.onerror = ev => { $('micst').textContent = 'Mic: ' + ev.error; };
  rec.onend = () => { if (consent) { try { rec.start(); } catch (e) {} } };
  try { rec.start(); $('mode').textContent = 'LISTENING (consent given)'; $('mode').className = 'pill on'; $('tx').innerHTML = ''; $('micst').textContent = 'Listening. Phone on speaker.'; }
  catch (e) { $('micst').textContent = 'Mic: ' + e; }
}
function stopMic() { consent = false; if (rec) { try { rec.onend = null; rec.stop(); } catch (e) {} rec = null; } $('mode').textContent = 'script only'; $('mode').className = 'pill off'; }

$('yes').onclick = () => { consent = true; $('gate').style.borderColor = '#2ecc71'; startMic(); };
$('no').onclick = () => { stopMic(); $('gate').style.borderColor = '#e74c3c'; $('micst').textContent = 'No consent. Mic stays closed; tap the branches by hand.'; };
$('reset').onclick = () => { stopMic(); cur = T.start; history = []; lastFinal = ''; $('tx').innerHTML = '<i>Mic is closed.</i>'; $('alert').style.display = 'none'; $('gate').style.borderColor = '#f39c12'; $('micst').textContent = ''; $('first').value = ''; $('st1').value = ''; show(cur); };
['first', 'st1', 'sender'].forEach(id => $(id).oninput = () => show(cur));
window.addEventListener('beforeunload', stopMic);
show(cur);
</script></body></html>
"""


def render(table=None, sender=''):
    table = table or coach_table()
    data = json.dumps(table, ensure_ascii=False).replace('</', '<\\/')
    return _PAGE.replace('__TABLE__', data).replace('__SENDER__', html.escape(sender, quote=True))


def build(path=None):
    """Write the page to DEALFLOW_DIR/coach.html (never the repo, never docs/)."""
    path = path or P.out(OUT_NAME)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(render(sender=_sender_name()))
    return path


if __name__ == '__main__':
    p = build(sys.argv[1] if len(sys.argv) > 1 else None)
    print('call coach written: %s' % p)
    print('open it in Chrome, put the phone on speaker, read the consent line, then tap yes.')
