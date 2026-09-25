#!/usr/bin/env python
"""call_mode — the phone-first calling page. `docs/call/index.html`.

WHY THIS EXISTS
The board is 6.4 MB and the morning worker is a desktop Blob document with no viewport meta, so on
a phone it renders at ~40% scale with ~19px tap targets. Alejandro views leads on the laptop and
dials from his iPhone: he reads a number off the screen, writes it down, calls, and never walks back
to log the outcome. Measured result — the system recorded 6 dials while he had made hundreds. Every
funnel number computed off that was measuring LOGGING FRICTION, not work.

So this page has exactly one job: make logging a call cost ONE TAP, on the device already in his
hand. Tap the number (native iOS dialer), come back, tap an outcome, next lead.

DESIGN RULES, each one load-bearing:
  * SMALL. ~490 rows ship as ~100 KB of JSON vs the board's 6.4 MB — it has to open on cell data.
  * Same gate. Reuses foreclosure_leads._encrypt_multi with the same site.codes, so there is no
    second secret and no second thing to revoke.
  * Same origin (`/foreclosure-leads/call/`), so localStorage — fcPw, fcTeamKey, fcLeadNotes — is
    SHARED with the board. That is what makes zero-typing onboarding and write-back possible.
  * DNC numbers are never serialized. Not styled, not flagged — absent. A number that is not in the
    payload cannot be rendered, cannot be tel:-linked, and cannot be recovered from view-source.
  * The outcome vocabulary is COPIED from tracker_template.html's CALL_OUTCOMES (10 entries). A
    fourth vocabulary would be a regression; there are already three in this codebase.

NEVER let a failure here break the board. foreclosure_leads calls this inside a try/except: an
exception costs the phone page, not the thing the business runs on.
"""
import datetime as _dt
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# Copied verbatim from tracker_template.html CALL_OUTCOMES. Keep byte-identical: k, label, cooldown
# hours, and whether it suppresses permanently. `appt` is the one a stale 6-entry copy drops.
CALL_OUTCOMES = [
    ('noanswer',  'No answer',                24, False),
    ('voicemail', 'Left voicemail',           24, False),
    # NEW 2026-09-04: they answered and asked to be called back. 6h so it resurfaces the SAME day if
    # he does not pin a time; the after-call panel's callback chips set an exact n.next on top.
    ('callback',  'Callback requested',        6, False),
    ('talked',    'Talked',                   72, False),
    ('appt',      'APPOINTMENT SET',          72, False),
    # NEW: reached a gatekeeper / not the decision-maker. Lead stays live; try their other number.
    ('gate',      'Not the owner',            24, False),
    # NEW: this NUMBER is bad/disconnected (distinct from 'wrong', which is the wrong PERSON). The
    # person stays callable on their other numbers; the after-call panel offers the next one.
    ('badnum',    'Bad number',               24, False),
    # SOFT NO: 720h = 30 DAYS. Field report 9/2: "they're telling me no and I keep calling them".
    # A no-answer coming back tomorrow is cadence; a human who SAID NO coming back in three days is
    # harassment. 30 days rather than forever on purpose: sale dates move, and a September no can be
    # an October "thank God you called" -- but nobody's October starts three days after their no.
    ('notint',    'Soft no (call back later)', 720, False),
    ('wrong',     'Wrong number',              0, True),
    # HARD NO: permanent. "Stop calling me" is honoured immediately and forever (stopEverywhere).
    ('dnc',       'Hard no (do not call)',     0, True),
]

# 15-second voicemail, Copy Pack §5. He READS it — no prerecorded or ringless drop, which would
# need prior express written consent under the TCPA and is the one part of "auto-dial everything"
# that stays off the table.
# 8/17 masterclass voice. {st1} not {street}: the full address read aloud sounds like a process
# server (Evernia St lesson, 2026-08-16). {phone} is the explicit callback slot — fillScript
# resolves it from SENDER.phone. Numbers written as words because he READS this live.
VOICEMAIL_EN = ("Hi {first}, this is {sender} with Biscayne Solutions Group, about {st1}. "
                "You may have a plan. Keep it. A plan can land a day late, and here a day is "
                "everything. Our senior advisor, thirty plus years, maps your free backup in five "
                "minutes. Call me any hour at {phone}. Thanks.")
VOICEMAIL_ES = ("Hola {first}, le habla {sender} de Biscayne Solutions Group, por {st1}. "
                "Si tiene un plan, sígalo. Un plan puede llegar un día tarde, y aquí un día lo es "
                "todo. Nuestro asesor principal, más de treinta años, le arma su respaldo gratis "
                "en cinco minutos. Llámeme a cualquier hora al {phone}. Gracias.")

# HOW TO LEAVE IT — the tonality cue rendered right under the voicemail (2026-09-14, Alejandro). A
# voicemail lives or dies on delivery, not words: NEPQ leaves it low-status, unhurried and a little
# detached so it reads as a note, not a pitch — that softness is what earns the callback. Rendered as
# a muted coaching line above the script (like the NEPQ beat-purpose lines), NOT spoken aloud.
VM_TONE_EN = ("HOW TO SAY IT — low and unhurried, like a neighbor leaving a note, not a salesman. "
              "Pause on every '...'. Sound a little detached, almost unsure you've even got the right "
              "person — that softness is what earns the callback. Warm, never urgent, and slow down "
              "on the phone number so they can write it.")
VM_TONE_ES = ("CÓMO DECIRLO — bajo y sin prisa, como un vecino que deja un recado, no un vendedor. Haga "
              "pausa en cada '...'. Suene un poco desapegado, casi como si no estuviera seguro de tener "
              "a la persona correcta — esa suavidad es la que gana la llamada de vuelta. Cálido, nunca "
              "con urgencia, y despacio con el número de teléfono para que lo puedan anotar.")

# TONALITY — how to say ALL of it (2026-09-14, from a live debrief: the words landed, the delivery
# killed the call). Rendered as a COLLAPSED reference at the top of the sheet (one tap), so it trains
# without pushing the opener down. These are the NEPQ delivery laws, in the order that fixes the most:
# the #1 cause of "sounding robotic" is lifting your voice at the end of a question, and filler words
# ("um/like/so") come from rushing to fill space you should be leaving as silence. EN only — coaching,
# not spoken script; a Spanish caller reads these fine and they are universal.
TONALITY = [
    ('End questions going DOWN, not up',
     'Drop your pitch on the last word — "...isn\'t it." not "...okay?" Up = needy salesman. Down = calm '
     'authority. This one fix kills half of "sounding robotic."'),
    ('The "..." means STOP',
     'Pause a full second. It feels long to you, normal to them. Pauses REPLACE filler — you say '
     '"um/like/so" because you\'re rushing to fill space. Leave it silent instead.'),
    ('Curious — most of the call',
     'Sound genuinely puzzled and interested, like you\'re working it out WITH them, not reading a form.'),
    ('Concerned — on the pain',
     'Problem and consequence beats: softer, slower, caring. A doctor delivering news, not a closer.'),
    ('Never explain — ASK',
     'The second you\'re TELLING them the interest concept, you\'ve lost. Drop ONE fact, hand it back '
     'as a question, then STOP. THEY say the pain out loud, not you. (This is the beat you over-talked.)'),
    ('One tie-down, then shut up',
     '"That\'s fair, right?" — then SILENCE. Never stack "right?...right?...fair?" — that reads '
     'desperate. Ask once, stop, let them answer first.'),
    ('Cushion before you ever push',
     'When they say "I\'ve got it handled" — AGREE first ("Good, keep working that — puts you ahead of '
     'most people I talk to..."), then ONE question. Steamroll them and you earn the reflex "no."'),
    ('Match their energy, then lower it',
     'Start near their pace, then slow yours down. They follow you. If they\'re guarded, go slower, not '
     'louder.'),
]


# ── THE SCRIPT ───────────────────────────────────────────────────────────────────────────────────
# The Jesse System has NO canonical outbound phone opener — the playbook's opener is a DOOR opener
# ("the only one knocking this week"). This is that opener adapted to a call, preserving its proven
# shape exactly: name · two nots · the reason · a tiny ask · a fairness close. Nothing else before
# they respond.
# LANGUAGE LAW (playbook lines 13-18, binding): "our senior advisor" NEVER "my supervisor" · the one
# experience sentence is his, never the company's · never "my/our clients" · we INTRODUCE licensed
# lenders, never "place loans" · money side only on attorneys, never case merits.
# 2026-09-01 -- aligned to the email. Someone who got the email and then the call has to hear ONE
# company. The email leads with their sale date, names the four nots, and asks for one small thing;
# so does this now. What did NOT change is the shape or the length: a cold call has about fifteen
# seconds before a hang-up, so this stays name, nots, reason, tiny ask, fairness close. The email
# can afford four paragraphs. This cannot.
# NEPQ rewrite 2026-09-04 (Alejandro): opener + question stack (Jeremy Miner). Lower status, ask for
# help, then let the QUESTIONS do the work -- the homeowner talks themselves into the problem instead
# of being pitched. The OPENER stays ~15 seconds (a cold call has that long before a hang-up); the
# NEPQ_Q stack below only runs if they are still talking. "Five minutes" is the advisor consult
# (matches FIFTEEN_SEC, VOICEMAIL, outreach_copy); do not introduce a conflicting ten/fifteen here,
# cross_surface_check() greps this file for it.
_OPEN_BODY_EN = ("It is {sender} with Biscayne Solutions Group. This might sound a little random, but "
                 "I have a copy of the court paperwork on {st1}{date} right in front of me, and I was "
                 "hoping you could help me out for a second. I am not your lender and I am not calling "
                 "to buy the place. Can I ask you something real quick?")
# usted register throughout — the playbook grades Spanish drills on it and every Spanish asset in the
# repo (flyer, letter, voicemail) matches. Never tú.
_OPEN_BODY_ES = ("Le habla {sender}, de Biscayne Solutions Group. Puede sonar un poco raro, pero tengo "
                 "una copia del papeleo de la corte sobre {st1}{date} aquí enfrente, y esperaba que me "
                 "pudiera ayudar un momento. No soy su prestamista y no le vengo a comprar la casa. "
                 "¿Le puedo hacer una pregunta rápida?")

# THE QUESTION STACK -- NEPQ. Runs after the opener, only if they engage. Situation -> problem
# awareness -> consequence (money side only, no case merits, no promised outcome) -> what it has cost
# -> consequence (personal) -> solution-awareness + the five-minute advisor ask + fairness close.
# Rendered as its own "GET THEM TALKING" block; ES is the usted register to match every other Spanish
# asset in the repo.
#
# NO EITHER/OR THAT HANDS THEM AN EXIT (Jesse, 2026-09-04, on the "YOUR PITCH" email). Q3 shipped as
# "...would you want to look at them, OR ARE YOU PRETTY SET on how you are handling it?" -- that
# second half is a written invitation to hang up, and it was the single thing he called out. Ask
# ASSUMED, then tie down ("Right?" / "That is fair, right?"). The only either/or allowed anywhere in
# this call is two ways to say YES (a time, not a whether) and it lives in the CLOSE beat, not here.
# Same reason Q1/Q2 never ask "are you the right person" -- the OPENER already settled that. Assume.
#
# SIX BEATS, in order, and the order is the point: you do not get to beat 6 by talking, you get there
# by them answering 1 through 5. Beat 1 is the ISOLATE scalpel from CIOC ("what do you TRULY want")
# moved to the FRONT -- their answer tells you which program you are even pitching. Beats 2-3 are the
# money consequence, 4 is what it has already cost them, 5 is the personal one (the beat the 3-question
# stack was missing entirely, and the one Miner builds the whole call around), 6 asks for the advisor.
NEPQ_Q_EN = [
    # 1 · SITUATION. Open, never a binary -- "hold it or are you past it?" is exactly the this-or-that
    # Jesse killed. Let them say it in their own words; everything after is built on this answer.
    "Before I get into any of it, what is it you are actually hoping happens with {st1}?",
    # 2 · PROBLEM AWARENESS.
    "When the bank or the attorney talked to you about that sale date, did anybody actually explain "
    "what happens to your balance every time that date gets pushed back?",
    # 3 · CONSEQUENCE, money side only (never the case, never its merits).
    "That is what I hear from just about everyone. Every time it moves, the interest and the legal "
    "fees keep stacking on top of what you already owe. Have you had a chance to see where that "
    "number is really sitting today, or is it a bit of a moving target?",
    # 4 · WHAT IT HAS ALREADY COST. Time and effort, and it surfaces the plan they already have --
    # which CIOC says you INSURE, never fight (the parachute frame).
    "How long has this been hanging over you, and what have you already tried?",
    # 5 · CONSEQUENCE, personal. Ends on an assumed statement plus a tie-down, NOT a question that
    # offers them the option of not caring.
    "Let me ask you straight. If nothing changes and that date comes and goes, where does that leave "
    "you and your family? That is not something you are willing to just sit back and let happen. "
    "Right?",
    # 6 · SOLUTION + the advisor ask + fairness close, then the ONLY either/or in the whole call:
    # two ways to say yes. A time, never a whether.
    "Before that date hits, you would want to at least see your real options laid out. Right? Our "
    "senior advisor has over 30 years in mortgages and foreclosure workouts and he takes five minutes "
    "and lays them out. Worst case, you know more than you did this morning. That is fair, right? "
    "Let me get you on his calendar. Is later today better, or first thing tomorrow?",
]
NEPQ_Q_ES = [
    "Antes de meterme en nada, ¿qué es lo que usted de verdad quisiera que pasara con {st1}?",
    "Cuando el banco o el abogado le habló de esa fecha de subasta, ¿alguien de verdad le explicó qué "
    "le pasa a su saldo cada vez que esa fecha se pospone?",
    "Eso es lo que escucho de casi todos. Cada vez que se mueve, los intereses y los gastos legales "
    "se siguen sumando a lo que ya debe. ¿Ha tenido chance de ver en cuánto está ese número hoy, o es "
    "un poco un blanco móvil?",
    "¿Cuánto tiempo lleva cargando con esto, y qué ha intentado ya?",
    "Le pregunto de frente. Si nada cambia y esa fecha llega y pasa, ¿dónde lo deja a usted y a su "
    "familia? Eso no es algo que usted esté dispuesto a dejar pasar así nomás. ¿Verdad?",
    "Antes de que llegue esa fecha, usted querría por lo menos ver sus opciones reales sobre la mesa. "
    "¿Verdad? Nuestro asesor principal tiene más de 30 años en hipotecas y en resolver casos de "
    "ejecución, y en cinco minutos se las explica. En el peor de los casos, usted sabe más de lo que "
    "sabía esta mañana. ¿Le parece justo? Déjeme ponerlo en su calendario. ¿Le queda mejor hoy más "
    "tarde, o mañana temprano?",
]

# zip() TRUNCATES TO THE SHORTER LIST, silently. Add a beat to EN, forget the ES, and that beat just
# vanishes from the page -- no error, no log line, the stack is simply one question shorter and looks
# entirely normal. That is the same "succeeds while doing nothing" shape as the empty SCRIPT.q slot
# this whole block exists to fill, so it fails LOUD at import instead of on a live call. CallModeError
# is not defined until further down this file, hence the plain raise.
if len(NEPQ_Q_EN) != len(NEPQ_Q_ES):
    raise RuntimeError('call_mode: NEPQ stack is %d EN vs %d ES. zip() would silently drop the '
                       'extras -- every beat needs both languages.'
                       % (len(NEPQ_Q_EN), len(NEPQ_Q_ES)))

# BEAT LABELS + PURPOSE, rendered above each question (2026-09-05). Mid-call he does not have time
# to remember WHY beat 4 exists — the page says it, one line, the way CIOC's `w` column does. The
# purpose lines are the NEPQ mechanics: tone, silence, who says the pain out loud.
NEPQ_K = [
    ('WHAT DO THEY WANT',
     'Their answer picks the program. Open question, never a binary — everything after is built on this.'),
    ('PROBLEM AWARENESS',
     'Curious tone, slow down. When they say "no, nobody explained it" — let the silence sit.'),
    ('THE MONEY CONSEQUENCE',
     'Drop the fact, hand it straight back as a question, then STOP. Do NOT explain it — the moment you '
     'lecture the interest concept you sound robotic and they tune out. THEY say the number is a moving target.'),
    ('WHAT IT ALREADY COST',
     'Surfaces the plan they already have. CIOC rule: INSURE it, never fight it — you are the parachute.'),
    ('THE PERSONAL STAKES',
     'The beat Miner builds the whole call around. Assumed statement + tie-down. Do NOT fill the silence.'),
    ('THE COMMITMENT',
     'EARN IT FIRST — beats 1-5 must land and THEY must say the pain. Fire this into a cold "no" and it '
     'dies. Advisor ask + fairness close. The ONLY either/or allowed: two ways to say yes — a time, never a whether.'),
]
if len(NEPQ_K) != len(NEPQ_Q_EN):
    raise RuntimeError('call_mode: NEPQ_K has %d beat labels vs %d questions — zip() would silently '
                       'drop the unlabeled beats.' % (len(NEPQ_K), len(NEPQ_Q_EN)))

# Two variants, because greeting the wrong "name" is worse than not greeting one.
# Measured on the fixture before this existed: "Hi, is this ACME?" (a company), "Hi, is this
# UNKNOWN?" (a placeholder), and — worst — "Hi, is this OLD?" on the very lead whose card warns
# "do NOT open with this name" because the roll owner has changed. The script contradicted the card.
PHONE_OPENER_EN = "Hi, is this {first}? " + _OPEN_BODY_EN
PHONE_OPENER_ES = "Hola, ¿hablo con {first}? " + _OPEN_BODY_ES
# {st1} — the STREET LINE, not the full "…, WEST PALM BEACH, FL 33401". The full address got read
# aloud on a live call (2026-08-16, Evernia St) and sounds like a process server. Street only.
PHONE_OPENER_ANON_EN = "Hi, am I speaking with the owner of {st1}? " + _OPEN_BODY_EN
PHONE_OPENER_ANON_ES = "Hola, ¿hablo con el dueño de {st1}? " + _OPEN_BODY_ES

# WARM opener (2026-09-05, Alejandro's own field style): when he already KNOWS the person — a prior
# Talked / Appointment / Callback on this human — the cold disarm reads as amnesia. His open is just
# the name, energy up: "Hector!" The page auto-picks this variant off the lead's own touch history
# (isWarm() in the JS), so a familiar person is never greeted like a stranger.
PHONE_OPENER_WARM_EN = ("{first}! It is {sender}... from Biscayne Solutions Group. How have you "
                        "been? Listen, I was looking at your file on {st1} again and something "
                        "jumped out that I wanted to run by you. You got a quick second?")
PHONE_OPENER_WARM_ES = ("¡{first}! Le habla {sender}... de Biscayne Solutions Group. ¿Cómo ha "
                        "estado? Oiga, estaba revisando otra vez su expediente de {st1} y me saltó "
                        "algo que quería comentarle. ¿Tiene un segundito?")

CIOC = [
    # Round two (8/17 masterclass) folded into each beat. The structure never changes; the canon
    # under each beat grows.
    ('CUSHION', 'Agree, normalize, include them in the majority. Never argue — and never make them '
     'feel stupid for the plan they already have (the mod, the lawyer, the realtor, the check).',
     'I can understand that — the majority of people we speak to feel exactly the same way. '
     'Everybody hits that wall at some point, and that\'s fair.'),
    ('ISOLATE', 'Make the objection the ONLY thing in the way. Force open-or-closed. The 8/17 '
     'scalpel: ask what they TRULY want — the answer tells you which program to pitch.',
     'What is it that you truly want to do with this property?  ·  If talking to me couldn\'t '
     'interfere with your plan at all, is there any other reason not to spend five minutes?'),
    ('OVERCOME', 'One reframe + one analogy + one what-if. Not three. ONE. When they have a plan '
     'they believe in: INSURE the hope, never fight it — you are the parachute, not the enemy.',
     'I don\'t want to be your bank or your middleman — I want to be your parachute. You\'re '
     'betting your house on the timing; keep your plan, and let us work on postponing the sale in parallel. '
     'If it works, I did you a favor and you owe me one.'),
    ('CLOSE', 'Fairness micro-agreements, then MEASURE what they DID, not what they said: the '
     'paperwork trial close, the read-back test, the five-minute advisor handoff.',
     "That's fair, right?  ·  'Let me get the paperwork together — if we agree we shake hands, "
     "if not I wasted fifteen minutes and we part friends.'  ·  'Save my number right now… now "
     "read it all back to me.' Clean read-back = in; 'pen ran out of ink' = don't count the deal."),
]

# The default. The full skeleton is ONLY if they are still talking.
FIFTEEN_SEC = ("Totally understand. One thing and I am gone: our senior advisor — over 30 years in "
               "mortgages and foreclosure workouts — reviews your case free, five minutes, on the "
               "phone. If nothing fits, we part friends. Fair?")

MARS_BLOCK = ("Before we start, a few things I am required to tell you: Biscayne Solutions Group is not "
              "associated with the government, and our service is not approved by the government or "
              "by your lender. Even if you use our service, your lender may not agree to change your "
              "loan. You may stop doing business with us at any time. This consultation is free, and "
              "you will never be asked to pay us a fee before you get results.")

NEVER_SAY = [
    'Any promised outcome or date. "Options," never "we will save your home."',
    'Anything about the attorney\'s case or its merits. Money side only.',
    '"We place loans" or any rate as ours. Licensed lenders lend; we INTRODUCE.',
    '"My supervisor" · "our attorneys" · "my clients" · company + "30 years."',
    'Money up front. No fees, ever (MARS + FS 501.1377).',
    '"Foreclosure" to someone who is not the owner.',
    'An argument. Calls end warm.',
]

# ── NEPQ BLACK BOOK ADDITIONS (2026-09-13, Alejandro) ──────────────────────────────────────────────
# Mined from Jeremy Miner's NEPQ Black Book of Questions (7th Level, 2024). The 2026-09-04 pass built
# the opener + 6-beat stack + CIOC from working knowledge of Miner; these four pieces are the ones the
# actual book has that the page was missing. Each is additive and lane-guarded in renderSheet (the
# balloon lane has none of these keys, so `if(SCRIPT.frame)` etc. leave the investor call untouched).
# COPY LAW still binds every line: "options" not outcomes, INTRODUCE the advisor, five-minute consult
# (no new time figure — cross_surface_check greps for conflicting ten/fifteen), no either/or exit.
# Verbal fill-ins use [square brackets] on purpose: fillScript only resolves {curly} tokens, so a
# {free text} would render literally on screen. [brackets] pass through as coaching cues he fills live.

# STATUS FRAME — Connection stage, read straight after the opener lands and BEFORE any question. The
# single highest-impact NEPQ move the page lacked: it lowers the "he's about to pitch me" pressure and
# their "yes, that'd help" is the first micro-commitment. Neutral, expert, unhurried tonality.
STATUS_FRAME_EN = ("Honestly, this is pretty basic — I'm really just trying to understand where things "
                   "stand on {st1} right now... compared to where you'd want them to land... so we can "
                   "see what that gap looks like. And toward the end, if you feel it might be worth it, "
                   "we can talk about a couple of possible next steps. Would that help you?")
STATUS_FRAME_ES = ("La verdad, esto es bien sencillo — solo quiero entender cómo está la situación con "
                   "{st1} ahora mismo... comparado con dónde a usted le gustaría que quedara... para ver "
                   "cómo se ve esa diferencia. Y al final, si usted siente que pudiera valer la pena, "
                   "hablamos de un par de posibles pasos a seguir. ¿Le parece que le ayudaría?")

# PROBING RAIL — Miner's actual engine. When the homeowner goes vague, echo their last words back as a
# question and get specific. Short one-liners he drops in anywhere, not a fixed sequence. EN/ES paired
# by index; the length check below fails loud (same reason as the NEPQ_Q zip guard).
PROBE_EN = [
    'Not 100%?...  (echo whatever they just said)',
    'What do you mean by that?',
    "How long's that been going on?",
    'Has that had an impact on you?...  In what way, though?',
    "You don't sound so sure — what is it you don't like about how it's going?",
    'Can you give me a specific example of when that happened?',
]
PROBE_ES = [
    '¿No 100%?...  (repita lo que acaban de decir)',
    '¿Qué quiere decir con eso?',
    '¿Cuánto tiempo lleva pasando eso?',
    '¿Eso le ha afectado?...  ¿De qué manera?',
    'No lo escucho muy seguro — ¿qué es lo que no le gusta de cómo va la cosa?',
    '¿Me puede dar un ejemplo específico de cuándo pasó eso?',
]
if len(PROBE_EN) != len(PROBE_ES):
    raise RuntimeError('call_mode: PROBE has %d EN vs %d ES — zip/index pairing would drop the extras.'
                       % (len(PROBE_EN), len(PROBE_ES)))

# BRIDGE — the Transition Formula (their words + their emotion), read to tie the call together right
# before the advisor ask. [brackets] are live fill-ins he speaks, not tokens.
BRIDGE_EN = ("Okay... based on everything you just told me, this is exactly the kind of thing we can "
             "actually help with. Because you said you want [what they want]... and the way that date "
             "keeps moving has you feeling [stressed / worried / stuck] — I think you mentioned that a "
             "couple times. So here's the one thing I'd want for you before that date hits...")
BRIDGE_ES = ("Bueno... con todo lo que me acaba de decir, esto es justo lo que sí podemos ayudarle a "
             "resolver. Porque usted dijo que quiere [lo que quieren]... y la forma en que esa fecha se "
             "sigue moviendo lo tiene [estresado / preocupado / atascado] — creo que lo mencionó un par "
             "de veces. Así que esto es lo único que quisiera para usted antes de que llegue esa fecha...")

# BUSY / NEPQ CALENDAR COMMITMENT — the "I'm busy / call me back" objection, handled by flipping status:
# YOUR time is the scarce thing. Distinct from the CIOC/drill-pack cards on purpose so it is one glance
# away next to the objections without touching the vault parse. {phone} resolves to SENDER.phone.
BUSY_EN = ("No problem at all... here's what I'll do — I'll give you my number, and you'd have to call "
           "ME back later to catch me. What's your timeframe on getting back to me, just to see if I'd "
           "even be free?  ·  Or if you've got your calendar handy, I'll pull mine and we'll lock a "
           "specific time so neither of us is chasing the other. My number is {phone}.")
BUSY_ES = ("No hay problema... mire, esto es lo que hago — le doy mi número y usted tendría que "
           "llamarme A MÍ más tarde para alcanzarme. ¿Cuál es su horario para regresarme la llamada, "
           "nada más para ver si yo estaría libre?  ·  O si tiene su calendario a mano, saco el mío y "
           "fijamos una hora específica para que ninguno ande persiguiendo al otro. Mi número es {phone}.")

# Where the Core 10 lives. Read at BUILD time so the page stays in step with the canon instead of
# carrying a copy that silently rots.
#
# 2026-08-29: it HAD silently rotted. This path was renamed BSG/ with the 2026-08-23 company rename,
# but the vault folder and file were never renamed and are still MSG Sales/MSG Objection Drill Pack.
# So the canonical pack has been unreachable since that day, every local build has quietly fallen
# back to the vendored cache, and every edit made to the vault drill pack since the rename has had
# ZERO effect on the shipped page. It failed soft -- one printed line inside a long build log --
# which is exactly why it survived a week. Try both names rather than renaming the vault: the .md is
# the target of [[MSG Objection Drill Pack - Procrastinator Psychology]] wikilinks from the
# affirmation scripts and the playbook, and renaming the file breaks all of them.
_VAULT_5P = os.path.join(os.path.expanduser('~'), 'projects', 'obsidian-vault', '5-projects')
_DRILL_CANDIDATES = [
    os.path.join(_VAULT_5P, 'BSG Sales', 'BSG Objection Drill Pack - Procrastinator Psychology.md'),
    os.path.join(_VAULT_5P, 'MSG Sales', 'MSG Objection Drill Pack - Procrastinator Psychology.md'),
]
_DRILL = next((p for p in _DRILL_CANDIDATES if os.path.exists(p)), _DRILL_CANDIDATES[0])
# Vendored copy of the PARSED cards, committed to the repo. The vault only exists on this machine —
# CI has no drill pack, so before this cache a CI rebuild silently replaced Call Mode with a page
# that had zero objection cards, overwriting a full local build with a degraded one every night.
# The local build refreshes this file whenever the vault parses; CI just reads it. No new exposure:
# the same content already ships in PLAINTEXT inside docs/call/index.html's __SCRIPT__ payload.
_DRILL_CACHE = os.path.join(HERE, 'call_objections.json')

# ON-THE-SPOT BOOKING. Sourced from outreach_copy so the link on the phone page and the link in the
# email are one string. The whole point of booking DURING the call is that the moment they say yes
# is the only moment you have -- "I'll send you a link" loses the ones who meant it when they said
# it. Falls back to the bare page rather than raising: a broken import must cost the prefill, never
# the phone page.
try:
    from outreach_copy import BOOKING_URL as _BOOK
except Exception:
    _BOOK = 'cal.com/bsgflorida/free-records-review'
BOOKING_URL = 'https://' + _BOOK.replace('https://', '')


# The close script tells the homeowner the company name "is how it shows on your caller ID". That is
# a factual claim about the carrier's CNAM record, and it has been FALSE since the 2026-08-23 rename
# -- said out loud, to exactly the audience the MARS rules exist to protect. Stripped HERE rather
# than in call_objections.json because that file is only a CACHE of a pack authored in the Obsidian
# vault; editing the JSON would be undone by the next vault parse. Flip sender.json `cnam_verified`
# once the carrier record actually changes and the sentence comes back.
_CNAM_EN = re.compile(r"\s*[\u2014-]?\s*just put [A-Za-z]{2,4},?\s*that'?s how it shows on your caller ID\.?", re.I)
_CNAM_ES = re.compile(r"\s*[\u2014-]?\s*p[o\u00f3]ngale [A-Za-z]{2,4},?\s*as[i\u00ed] le sale en el identificador\.?", re.I)


_QREC_LINE = ('RECORDING IS ON. Before anything else: "Quick thing before we start, I record '
              'my calls so I have your file right. Is that okay with you?" A no means STOP '
              'RECORDING, not hang up.')


def _quo_recording():
    try:
        import entity
        return bool(entity.sender().get('quo_record'))
    except Exception:
        return False


def _balloon_signer():
    """sender.json balloon_signer -> the call page (SENDER.bsigner), so the investor lane's opener and
    voicemail introduce the ADVISOR — the same name genBalloonEmail signed and send_server put on the
    From line. Empty when unset: fillScript then falls back to the caller's own name."""
    try:
        import entity
        return str(entity.sender().get('balloon_signer') or '').strip()
    except Exception:
        return ''


def _cnam_ok():
    try:
        import entity
        return bool(entity.sender().get('cnam_verified'))
    except Exception:
        return False


def _strip_cnam(cards):
    """Drop the caller-ID claim from every card until sender.json says CNAM is verified."""
    if _cnam_ok():
        return cards

    def fix(v):
        if isinstance(v, str):
            return _CNAM_ES.sub('.', _CNAM_EN.sub('.', v))
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        return v
    return fix(cards)


def load_objections(path=None):
    """-> [{n, t, say, reb, one}] for the Core 10, or [] if neither vault nor cache is reachable.

    Returns [] rather than raising: a missing drill pack should cost the objection picker, not the
    whole calling page. The UI says so plainly when it is empty.
    ⚠️ Only the CORE 10. The Extended 20 contain ~7 real duplicates of these (bank-mod is #1, #11 and
    #25; the lawyer #2 and #16; "The Incoming Check" appears twice under that same title), so a flat
    30 reads as repetitive on a phone.
    """
    src = path or _DRILL
    try:
        s = open(src, encoding='utf-8').read()
    except Exception as e:
        # Broadened from OSError ON PURPOSE: a drill pack re-saved as UTF-16 or with an odd BOM
        # raises UnicodeDecodeError, which is a ValueError — it sailed past `except OSError` and out
        # of load_objections, and since make_callmode sits inside the caller's try/except that did
        # not just cost the objection picker, it silently cost the ENTIRE calling page.
        # Fall back to the vendored cache (the CI path) before giving up.
        try:
            cards = _strip_cnam(json.load(open(_DRILL_CACHE, encoding='utf-8')))
            if cards:
                print('call mode: vault drill pack unreachable — using vendored call_objections.json'
                      ' (%d cards). Fine on CI; on the LOCAL machine this means the vault moved.'
                      % len(cards))
                return cards
        except Exception:
            pass
        print('call mode: objection drill pack unreadable (%s: %s) AND no vendored cache — the page '
              'will ship with NO objection cards. Source: %s' % (type(e).__name__, str(e)[:80], src))
        return []
    core = s.split('# THE EXTENDED 20')[0]
    out = []
    for num, title, body in re.findall(r'^## (\d+)\.\s*(.+?)\n(.*?)(?=^## |\Z)', core, re.S | re.M):
        say = re.search(r'\*\*They say:\*\*\s*(.+?)\n', body)
        reb = re.search(r'\*\*The rebuttal.*?:\*\*\s*\n\n(.*?)(?=\n\*\*If you only)', body, re.S)
        one = re.search(r'\*\*If you only get one sentence:\*\*\s*\*?(.+?)\*?\s*$', body, re.M)
        if not (say and reb):
            continue
        card = {
            'n': int(num),
            't': title.strip(),
            # strip straight AND curly quotes: the vault authors “…”, and both renderers wrap the
            # value in &ldquo;&rdquo; again, which printed a doubled ““quote”” on every card.
            'say': say.group(1).strip().strip('"“”'),
            # the two paragraphs are CUSHION+ISOLATE then OVERCOME+CLOSE — keep the break
            'reb': [p.strip() for p in reb.group(1).strip().split('\n\n') if p.strip()],
            'one': (one.group(1).strip().strip('*') if one else ''),
        }
        # Spanish, if we have it. objections_es is NEW COPY, not canon — a faithful translation of
        # the English above, usted register, CIOC shape preserved. English stays source of truth;
        # a card with no Spanish says so on screen rather than rendering blank.
        try:
            import objections_es
            es = objections_es.ES.get(card['n'])
            if es:
                card['es'] = es
        except Exception:
            pass
        out.append(card)
    # Refresh the vendored cache so the next vault-less (CI) build ships these exact cards.
    # Best-effort: a read-only checkout must not fail the build over a cache write.
    if out:
        try:
            # The CACHE stays a FAITHFUL copy of the vault pack. Sanitising happens on both READ
            # paths instead, so flipping sender.json cnam_verified restores the caller-ID sentence
            # immediately rather than waiting for the next vault parse to rewrite this file.
            json.dump(out, open(_DRILL_CACHE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        except Exception:
            pass
    return _strip_cnam(out)


# "Not interested" — the reflex brush-off, and the single most common early hang-up. HARD-CODED here
# rather than parsed from the vault drill pack (2026-09-14) so it CANNOT be dropped by a build that
# reads a vault-less or stale vault: load_objections rewrites call_objections.json from whatever the
# local vault holds, so a runner whose vault lacks this card would silently strip it from the shipped
# cache. As a constant it is on every machine's build, vault or not. Same card shape load_objections
# emits (n, t, say, reb[], one, es{}), so renderSheet needs no change. No {tokens}: objection
# rebuttals render through esc() with no fillScript, so a token would print literally. Guardrails
# kept: "options" not outcomes, INTRODUCE the advisor, five-minute consult, not buying the house,
# no either/or exit, ends warm. ES is usted. NEPQ voice (disarm the reflex, curious reopen).
NOT_INTERESTED_CARD = {
    'n': 15,
    't': 'Not Interested (The Reflex Brush-Off)',
    'say': "I'm not interested.",
    'reb': [
        "That's not a problem at all — honestly, I wasn't expecting you to be. Almost everybody I "
        "reach about a place like yours says the exact same thing when I first call, so you're in "
        "good company. I'm not here to sign you up for anything, and I'm not calling to buy the "
        "house. Real quick though, just so I'm not the twelfth person wasting your afternoon — has "
        "anybody actually sat down and shown you what happens to your balance every time that sale "
        "date gets pushed... or is that still kind of a question mark for you?",
        "Because that's the only reason I called — not to pitch you a loan. Our senior advisor, "
        "30-plus years in mortgages and foreclosure workouts, just lays your real options out in "
        "about five minutes, free, and you decide what to do with them. Worst case, you know more "
        "than you did this morning, and we part friends. That's fair, right?",
    ],
    'one': "I'm not asking you to be interested — I'm asking if it's worth five free minutes to know "
           "your options before that date hits.",
    'es': {
        'say': 'No estoy interesado.',
        'reb': [
            'No hay ningún problema — la verdad, ni esperaba que lo estuviera. Casi todas las personas '
            'con las que hablo sobre una casa como la suya me dicen exactamente lo mismo cuando llamo la '
            'primera vez, así que está en buena compañía. No le vengo a inscribir en nada, y no le llamo '
            'para comprarle la casa. Rapidito, nada más para no ser la persona número doce que le quita '
            'la tarde: ¿alguien ya se sentó con usted y le mostró qué le pasa a su saldo cada vez que '
            'empujan esa fecha de la subasta... o eso todavía es una interrogante?',
            'Porque esa es la única razón por la que llamé — no para venderle un préstamo. Nuestro asesor '
            'principal, más de 30 años en hipotecas y en resolver casos de ejecución, le pone sus '
            'opciones reales sobre la mesa en unos cinco minutos, gratis, y usted decide qué hacer con '
            'ellas. En el peor de los casos, sabe más de lo que sabía esta mañana, y quedamos como '
            'amigos. ¿Verdad que sí?',
        ],
        'one': 'No le pido que esté interesado — le pregunto si vale cinco minutos gratis conocer sus '
               'opciones antes de que llegue esa fecha.',
    },
}


# "Wrong number" + instant hang-up (2026-09-14, field report). DISTINCT from card 8 "The Wrong House
# (Denial)": that one denies the FORECLOSURE and stays on the line to argue; this one denies the
# NUMBER and hangs up in ~2 seconds, and half the time it is a dodge by the actual owner. So the whole
# card is built for SPEED — a fast, apologetic, low-status catch delivered BEFORE the click, then one
# ownership question that reopens WITHOUT tripping the never-say rule ("foreclosure" to a non-owner):
# it says "county paperwork," never the word, and only after they engage as the owner. Honors a real
# wrong number — reb2 lets them go warm; a genuine no is logged 'wrong' and never argued (that is
# harassment and burns the number). Hard-coded for the same durability reason as NOT_INTERESTED_CARD.
WRONG_NUMBER_CARD = {
    'n': 16,
    't': 'Wrong Number (+ Instant Hang-Up)',
    'say': "You've got the wrong number.  [click]",
    'reb': [
        "Oh — my apologies, I may have dialed wrong. One quick second so I'm not bothering the wrong "
        "person: I'm just trying to reach the owner of the property on my paperwork here... that's "
        "not you, is it?",
        "If it's honestly not you — no problem at all, I'll let you go, sorry to have bothered you. "
        "But if it IS your place, this is worth thirty seconds: I've got county paperwork on it, and "
        "the people who look at it early are the ones who keep their choices. Five free minutes with "
        "an advisor who's done this thirty years — and if it turns out to be nothing, we part as "
        "friends. Fair enough?",
    ],
    'one': "Sorry — I think I may have dialed wrong. I'm just trying to reach the owner of the "
           "property on my paperwork... that's not you, is it?",
    'es': {
        'say': 'Tiene el número equivocado.  [cuelga]',
        'reb': [
            'Ay — disculpe, capaz marqué mal. Un segundito para no estar molestando a la persona '
            'equivocada: nada más estoy tratando de localizar al dueño de la propiedad que tengo aquí '
            'en mi papeleo... ¿ese no es usted, verdad?',
            'Si de verdad no es usted — no hay ningún problema, lo dejo ir, disculpe la molestia. Pero '
            'si SÍ es su propiedad, esto vale treinta segundos: tengo papeleo del condado sobre ella, y '
            'las personas que lo revisan temprano son las que se quedan con opciones. Cinco minutos '
            'gratis con un asesor que lleva treinta años en esto — y si resulta no ser nada, quedamos '
            'como amigos. ¿Le parece justo?',
        ],
        'one': 'Disculpe — creo que marqué mal. Nada más busco al dueño de la propiedad que tengo en mi '
               'papeleo... ¿ese no es usted, verdad?',
    },
}

# Cards that must ship on EVERY build regardless of the vault. Appended by objection_cards() below.
_HARDCODED_CARDS = [NOT_INTERESTED_CARD, WRONG_NUMBER_CARD]


def objection_cards():
    """load_objections() + the hard-coded cards (_HARDCODED_CARDS), each appended at most once.

    Dedup by number AND title so a vault copy of the same card (this desktop had 'Not interested' in
    the vault before the 2026-09-14 revert; a future one could reappear) never double-renders — the
    vault version wins if present, the constant fills in when it is not. Exactly one of each ships.
    """
    cards = load_objections()
    nums = {c.get('n') for c in cards}
    titles = {(c.get('t') or '').strip().lower() for c in cards}
    for hc in _HARDCODED_CARDS:
        if hc['n'] not in nums and hc['t'].strip().lower() not in titles:
            cards.append(hc)
            nums.add(hc['n'])
            titles.add(hc['t'].strip().lower())
    return cards


# ── GUIDED CALL FLOW (2026-09-15, Alejandro) ─────────────────────────────────────────────────────
# "Every line, one at a time: the line -> the tonality -> WAIT -> what they might say -> go that route,
# all the way to the end." A second RENDERER over the SAME payload, never a second vocabulary: every
# spoken line below is the existing constant (openers, the six NEPQ beats, frame, bridge, busy, f15,
# voicemail, objection cards). The only NEW copy is the handful of short lines that the 09-14 drills
# proved were missing (the "what do you do?" answer, the "you buying my house?" defuse, "send me
# something", "let me think", the booking read-back, the gatekeeper, a tougher consequence rail) —
# all additive, all inside the copy law. Rendered by renderGuided() as an additive GUIDED/FULL toggle;
# the FULL sheet is untouched and the balloon lane (no `flow`) never sees it.
#
# Why this shape fixes the leaks the drills exposed: LISTEN is a rendered step, so the pause is the
# move instead of a gap to fill with "um / real quick"; the branches are pre-loaded so he is never
# scrambling for words; every line carries its tone; and the advisor ask is UNREACHABLE except through
# beats 1-5 — the flow itself will not let him run to the close.
#
# No {tokens} in the new lines except {phone}/{st1}, which fillScript resolves. Verbal fill-ins are
# [square brackets] (rendered literally as coaching cues — fillScript only touches {curly}).
INTRO_30_EN = ("Fair question. Honestly, all I do is — I get a copy of the court file on a place, and before "
               "that date hits I help the owner see what options they've actually got. That's it. Most "
               "people don't know they have any... does that make sense?")
INTRO_30_ES = ("Buena pregunta. La verdad, lo único que hago es esto: consigo una copia del expediente de la "
               "corte sobre una propiedad, y antes de que llegue esa fecha ayudo al dueño a ver qué opciones "
               "tiene de verdad. Eso es todo. La mayoría no sabe que tiene alguna... ¿tiene sentido?")
DEFUSE_BUY_EN = "No — I'm not buying anything, I promise. That's not what this is."
DEFUSE_BUY_ES = "No — no le vengo a comprar nada, se lo prometo. Esto no es eso."
CONSEQ_EN = [
    "Do you want to have to go through all that... if you... if you didn't have to?",
    "Can you afford to take that risk?",
    "Why look at fixing this now? Why not just push it down the road like a lot of people do... who end "
    "up losing the house they could've kept?",
]
CONSEQ_ES = [
    "¿Usted quiere tener que pasar por todo eso... si... si no tuviera que hacerlo?",
    "¿Se puede dar el lujo de correr ese riesgo?",
    "¿Por qué ver cómo resolver esto ahora? ¿Por qué no dejarlo para después como hace mucha gente... que "
    "termina perdiendo la casa que pudo haber conservado?",
]
SENDINFO_EN = ("Yeah, I can do that for sure... now, just so I send you the right thing and not a stack of "
               "junk — what specifically would you want it to show you?")
SENDINFO_ES = ("Claro, con gusto... ahora, para mandarle lo correcto y no un montón de papeles: ¿qué "
               "específicamente quisiera que le mostrara?")
THINK_EN = ("That's fair — most people want to. Just so I make sure I actually answered it... what "
            "specifically do you want to think over?")
THINK_ES = ("Es justo — la mayoría quiere pensarlo. Nada más para asegurarme de haberle respondido bien... "
            "¿qué específicamente quiere pensar?")
BOOK_EN = ("Perfect — I've got you down for [the time they picked]. Do me one favor before we hang up: "
           "save my number right now... it's {phone}. ... Now read it back to me, so I know you've got it "
           "right.")
BOOK_ES = ("Perfecto — lo tengo anotado para [la hora que escogió]. Hágame un favor antes de colgar: "
           "guarde mi número ahora mismo... es el {phone}. ... Ahora léamelo de vuelta, para saber que lo "
           "tiene bien.")
# Gatekeeper / not the owner: "the property on {st1}", never the word foreclosure (NEVER_SAY).
GATE_EN = ("No problem at all — when's usually a good time to catch them? ... Or if it's easier, could I "
           "leave my number with you? It's {phone}, and it's about the property on {st1}.")
GATE_ES = ("No hay problema — ¿a qué hora suele estar? ... O si es más fácil, ¿le puedo dejar mi número? "
           "Es el {phone}, y es sobre la propiedad en {st1}.")


# "ARE YOU AN INVESTOR?" (2026-09-16 field debrief, 9107 NW 182 TER). Answered "we specialize in
# pre-foreclosures" -> a realtor-owner heard a wholesaler and hung up. The honest line is the script's
# own: "not calling to buy your house". NEVER claim BSG is not an investor (it does flip / cash-for-keys).
INVESTOR_EN = ("Fair question. No, I'm not calling to buy your house. What I do is help owners see every option "
               "they've got before that case moves, so whatever you decide, you decide it with all the facts. "
               "Can I ask you one thing?")
INVESTOR_ES = ("Buena pregunta. No, no le llamo para comprarle la casa. Lo que hago es ayudar a los dueños a ver "
               "todas las opciones que tienen antes de que ese caso avance, para que lo que decida, lo decida con "
               "toda la información. ¿Le puedo preguntar una cosa?")
# "I'M A REALTOR / SELLING IT MYSELF" — same call. Insure the plan (card 13, the Parachute), never
# compete with it, and never quote an agent their own home's value ("do your research"). The question
# lets THEM find the gap between "under contract" and "closed" while the case keeps moving.
REALTOR_EN = ("Honestly, selling it is a smart move, and you know that market, so I'm not going to throw a "
              "number at you. One question, though... when you say sell it soon, do you mean under contract, "
              "or closed?")
REALTOR_ES = ("La verdad, venderla es una buena jugada, y usted conoce ese mercado, así que no le voy a tirar un "
              "número. Una pregunta, eso sí... cuando dice venderla pronto, ¿quiere decir bajo contrato, o ya "
              "cerrada?")


def _beat(i):
    """The i-th NEPQ beat (label, purpose, EN, ES) — the SAME strings the full sheet renders."""
    k, w = NEPQ_K[i]
    return k, w, NEPQ_Q_EN[i], NEPQ_Q_ES[i]


def _flow():
    """The guided call as a list of steps. Each: id, k (label), en/es (the line) OR rail (a list of
    short lines), tone, listen, br (branches: [what they said, target]), ret (where a detour to an
    objection card returns to). Targets: a step id · 'obj:N' (objection card) · 'end:<outcome k>'."""
    q1, q2, q3, q4, q5, q6 = (_beat(i) for i in range(6))
    return [
        {'id': 'greet', 'k': 'THE OPENER',
         'en': 'Hi, is this {first}?', 'es': 'Hola, ¿hablo con {first}?',
         'aen': 'Hi, am I speaking with the owner of {st1}?', 'aes': 'Hola, ¿hablo con el dueño de {st1}?',
         'tone': 'Warm and casual, voice DOWN on the name — like you half-know them. Then STOP.',
         'listen': 'Wait. Let them answer. Do not fill the gap.',
         'br': [['Yes / that\'s me', 'disarm'], ['Who\'s this? / who\'s asking?', 'disarm'],
                ['Wrong number', 'obj:16'], ['Not here / can I take a message?', 'gate'],
                ['Straight to voicemail', 'vm'], ['Hung up on me', 'end:notint']],
         'ret': 'disarm'},
        {'id': 'disarm', 'k': 'THE DISARM',
         'en': _OPEN_BODY_EN, 'es': _OPEN_BODY_ES,
         'tone': 'Confused, low-status, slow. Say your FIRST name... pause... then full name + company, '
                 'like they should already know you. Slow on the two NOTS. Then STOP.',
         'listen': 'They will test you here. Whatever they say, do not get defensive.',
         'br': [['Okay / sure / what?', 'frame'], ['What\'s this about? / what do you do?', 'intro30'],
                ['You buying my house?', 'defusebuy'], ['Are you an investor?', 'investor'],
                ["I'm a realtor / selling it myself", 'realtor'], ['Not interested', 'obj:15'],
                ['I\'m busy, call me back', 'busy'], ['I\'ve got a lawyer / the bank', 'obj:1'],
                ['Don\'t call me again', 'end:dnc']],
         'ret': 'frame'},
        {'id': 'intro30', 'k': 'WHAT DO YOU DO? — one humble line',
         'en': INTRO_30_EN, 'es': INTRO_30_ES,
         'tone': 'Humble, one breath, zero pitch. Answer it and hand it back.',
         'listen': 'A skeptic asked. Answer, then stop.',
         'br': [['Okay, go ahead', 'frame'], ['Are you an investor?', 'investor'],
                ["I'm a realtor / selling it myself", 'realtor'], ['Not interested', 'obj:15'], ['I\'m good, it\'s handled', 'obj:1']],
         'ret': 'frame'},
        {'id': 'defusebuy', 'k': 'KILL THE BUYING FEAR',
         'en': DEFUSE_BUY_EN, 'es': DEFUSE_BUY_ES,
         'tone': 'Flat and fast, then a beat. Kill the fear — never argue it.',
         'listen': 'Let it land.',
         'br': [['Okay... so what is it?', 'frame'], ["I'm a realtor / selling it myself", 'realtor'],
                ['Still not interested', 'obj:15']],
         'ret': 'frame'},
        {'id': 'investor', 'k': '"ARE YOU AN INVESTOR?" — the honest answer',
         'en': INVESTOR_EN, 'es': INVESTOR_ES,
         'tone': "Calm and unbothered: it's a fair question, not an accusation. Say the true line, 'I'm not calling "
                 "to buy your house.' NEVER claim we're not investors, and never say 'we specialize in pre-foreclosures'.",
         'listen': 'If they relax, set the frame. If they keep probing, stay honest and short. Never dodge.',
         'br': [['Okay, what is it?', 'frame'], ["I'm a realtor / selling it myself", 'realtor'],
                ['Still not interested', 'obj:15'], ["Don't call me again", 'end:dnc']],
         'ret': 'frame'},
        {'id': 'realtor', 'k': "I'M A REALTOR / SELLING IT MYSELF — insure the plan",
         'en': REALTOR_EN, 'es': REALTOR_ES,
         'tone': "Back their plan: you're the parachute, not the competition. NEVER quote their home's value to an "
                 "agent; ask theirs. Curious on the question, then silence.",
         'listen': 'Let THEM notice the gap between under contract and closed. If they mention illness or family, '
                   'stop and be human first.',
         'br': [['"Closed" in a month (tight)', 'obj:13'], ['"Under contract", plenty of time', 'q2'],
                ["It's handled, don't worry", 'obj:13'], ['Not interested', 'obj:15']],
         'ret': 'q2'},
        {'id': 'frame', 'k': 'THE FRAME — set it before any question',
         'en': STATUS_FRAME_EN, 'es': STATUS_FRAME_ES,
         'tone': 'Neutral, expert, unhurried. You are setting the table, not pitching.',
         'listen': 'Their "yeah, that\'d help" is the first micro-yes.',
         'br': [['Sure / that\'d help', 'q1'], ['What kind of options? (do NOT present — ask)', 'q1'],
                ['I\'ve got it handled', 'obj:1'], ['Are you an investor?', 'investor'], ["I'm a realtor / selling it myself", 'realtor'],
                ['Not interested', 'obj:15']],
         'ret': 'q1'},
        {'id': 'q1', 'k': '1 · ' + q1[0], 'en': q1[2], 'es': q1[3],
         'tone': 'Curious. Open question. ' + q1[1],
         'listen': 'Their answer picks the program. Do not fill the silence.',
         'br': [['Keep the house', 'q2'], ['Sell / get out / start fresh', 'q2'], ["Selling it myself / I'm a realtor", 'realtor'], ['I don\'t know', 'probe'],
                ['It\'s handled / bank / mod', 'obj:1'], ['My lawyer\'s on it', 'obj:2']],
         'ret': 'q2'},
        {'id': 'probe', 'k': 'THEY WENT VAGUE — echo it back',
         'rail': {'en': PROBE_EN, 'es': PROBE_ES},
         'tone': 'Mirror their last words as a question. Get specific. One at a time.',
         'listen': 'Pick ONE. Then wait.',
         'br': [['They opened up', 'q2'], ['Still won\'t engage', 'f15']],
         'ret': 'q2'},
        {'id': 'q2', 'k': '2 · ' + q2[0], 'en': q2[2], 'es': q2[3],
         'tone': 'Curious, slow. When they say "no, nobody explained it" — LET THE SILENCE SIT. Do NOT explain.',
         'listen': 'Wait for the "no". It is your opening — do not fill it.',
         'br': [['No, nobody explained it', 'q3'], ['Yeah, they told me (ask: what\'d they tell you?)', 'q3'],
                ['It\'ll just get pushed again', 'obj:3']],
         'ret': 'q3'},
        {'id': 'q3', 'k': '3 · ' + q3[0], 'en': q3[2], 'es': q3[3],
         'tone': 'Concerned — a doctor, not a closer. Drop the fact, hand it back, STOP. You are not allowed to lecture here.',
         'listen': 'THEY say the number is a moving target. Not you.',
         'br': [['Moving target / no idea', 'q4'], ['I know the number', 'q4'],
                ['I\'m fine, don\'t worry about it', 'obj:13']],
         'ret': 'q4'},
        {'id': 'q4', 'k': '4 · ' + q4[0], 'en': q4[2], 'es': q4[3],
         'tone': 'Curious. Whatever plan they name — INSURE it, never fight it. You are the parachute.',
         'listen': 'This surfaces the plan they already have. Cushion it before anything else.',
         'br': [['Bank mod', 'obj:1'], ['Lawyer', 'obj:2'], ['Money\'s coming (check / family)', 'obj:4'],
                ['Bankruptcy', 'obj:6'], ["Listing it / I'm a realtor", 'realtor'], ['Nothing / haven\'t really tried', 'q5'], ['Tried a lot, nothing worked', 'q5']],
         'ret': 'q5'},
        {'id': 'q5', 'k': '5 · ' + q5[0], 'en': q5[2], 'es': q5[3],
         'tone': 'Concerned, low, slow. Assumed statement + ONE tie-down, voice DOWN. Then SILENCE — do not rescue it.',
         'listen': 'They must say the pain out loud. If they do, you have EARNED the close.',
         'br': [['They said the pain (kids / wreck me / no plan B)', 'bridge'],
                ['"I\'m fine, it\'ll work out" (flat)', 'conseq'], ['Too late, nothing I can do', 'obj:9']],
         'ret': 'bridge'},
        {'id': 'conseq', 'k': 'STILL FLAT — push, with care',
         'rail': {'en': CONSEQ_EN, 'es': CONSEQ_ES},
         'tone': 'Challenging but caring — lean in. Pick ONE, voice down, then silence.',
         'listen': 'You want them to defend why they need to act. Let them.',
         'br': [['They felt it', 'bridge'], ['Still flat / not moving', 'f15']],
         'ret': 'bridge'},
        {'id': 'bridge', 'k': 'THE BRIDGE — their words back to them',
         'en': BRIDGE_EN, 'es': BRIDGE_ES,
         'tone': 'Calm, voice DOWN. Repeat what THEY want and what THEY feel. Then straight into the ask.',
         'listen': 'No pause needed — flow into the commitment.',
         'br': [['Continue to the ask →', 'q6']],
         'ret': 'q6'},
        {'id': 'q6', 'k': '6 · ' + q6[0], 'en': q6[2], 'es': q6[3],
         'tone': 'EARNED IT? Then: calm, voice DOWN on "right". ONE tie-down, then the time. A when — never a whether. No promises.',
         'listen': 'Two ways to say yes. Wait for the time.',
         'br': [['Later today', 'book'], ['Tomorrow morning', 'book'], ['Just send me something', 'sendinfo'],
                ['Let me think about it', 'think'], ['I\'m busy right now', 'busy'], ['No thanks, I\'m good', 'f15']],
         'ret': 'q6'},
        {'id': 'sendinfo', 'k': '"JUST SEND ME SOMETHING"',
         'en': SENDINFO_EN, 'es': SENDINFO_ES,
         'tone': 'Agreeable, then ONE question. Whatever they say next is their real concern.',
         'listen': 'Never send blind — that is how you never hear back.',
         'br': [['They told me what they want to see → back to the questions', 'q1'],
                ['They named a concern → handle it, re-ask', 'q6'], ['Fine, just send it', 'end:talked']],
         'ret': 'q6'},
        {'id': 'think', 'k': '"LET ME THINK ABOUT IT"',
         'en': THINK_EN, 'es': THINK_ES,
         'tone': 'Unbothered, curious. Isolate the real concern — do not re-pitch.',
         'listen': 'They will name the thing. Then answer THAT and re-ask.',
         'br': [['They named the concern → answer it, re-ask', 'q6'], ['Still stalling', 'f15']],
         'ret': 'q6'},
        {'id': 'busy', 'k': '"I\'M BUSY / CALL ME BACK" — flip it',
         'en': BUSY_EN, 'es': BUSY_ES,
         'tone': 'Unbothered. YOUR time is the scarce one. Get a timeframe or a calendar slot — never "sure, whenever".',
         'listen': 'Wait for a time. If they give one, lock it.',
         'br': [['Gave me a time', 'end:callback'], ['Booked a slot', 'end:callback'], ['Brushed me off', 'end:notint']],
         'ret': 'q6'},
        {'id': 'f15', 'k': 'THE 15-SECOND OUT',
         'en': FIFTEEN_SEC, 'es': FIFTEEN_SEC,
         'tone': 'Totally relaxed. You are leaving, not chasing. One line, voice down, then let go.',
         'listen': 'Takeaway. Sometimes the door opens on the way out.',
         'br': [['Okay, fine — five minutes', 'book'], ['No', 'end:notint'], ['Stop calling me', 'end:dnc']],
         'ret': 'f15'},
        {'id': 'book', 'k': 'BOOK IT — read-back test',
         'en': BOOK_EN, 'es': BOOK_ES,
         'tone': 'Slow on the number. A clean read-back means it is real. "Pen ran out of ink" = do not count it. '
                 'Advisor consult: he reads the MARS block first.',
         'listen': 'Listen for the read-back.',
         'br': [['Read it back clean ✓', 'end:appt'], ['Fumbled it / "pen ran out" (log it, count it soft)', 'end:appt'],
                ['Backed out', 'f15']],
         'ret': 'book'},
        {'id': 'gate', 'k': 'NOT THE OWNER — gatekeeper',
         'en': GATE_EN, 'es': GATE_ES,
         'tone': 'Easy, friendly. Never say "foreclosure" to anyone but the owner — "the property" only.',
         'listen': 'Get a time or leave the number. Nothing else.',
         'br': [['Got a callback time', 'end:callback'], ['Left my number', 'end:gate'], ['Hung up', 'end:gate']],
         'ret': 'gate'},
        {'id': 'vm', 'k': 'VOICEMAIL — read it live',
         'en': VOICEMAIL_EN, 'es': VOICEMAIL_ES,
         'tone': VM_TONE_EN,
         'listen': 'Hang up after "Thanks." Do not ramble.',
         'br': [['Left it', 'end:voicemail']],
         'ret': 'vm'},
    ]


FLOW = _flow()

# FAIL LOUD AT IMPORT, like the NEPQ zip guards: a branch that points at a step that does not exist,
# an unknown outcome, or a non-numeric objection card would ship as a dead button on a live call.
_FLOW_IDS = {s['id'] for s in FLOW}
_OUT_KEYS = {k for k, _t, _h, _s in CALL_OUTCOMES}
for _s in FLOW:
    if len(_FLOW_IDS) != len(FLOW):
        raise RuntimeError('call_mode: duplicate step id in FLOW')
    if not (('en' in _s and 'es' in _s) or 'rail' in _s):
        raise RuntimeError('call_mode: FLOW step %r has no en/es line and no rail' % _s['id'])
    for _lbl, _tgt in _s['br']:
        if _tgt.startswith('obj:'):
            int(_tgt[4:])                      # ValueError = fail loud
        elif _tgt.startswith('end:'):
            if _tgt[4:] not in _OUT_KEYS:
                raise RuntimeError('call_mode: FLOW step %r branches to unknown outcome %r' % (_s['id'], _tgt))
        elif _tgt not in _FLOW_IDS:
            raise RuntimeError('call_mode: FLOW step %r branches to missing step %r' % (_s['id'], _tgt))
    if _s.get('ret') and _s['ret'] not in _FLOW_IDS:
        raise RuntimeError('call_mode: FLOW step %r returns to missing step %r' % (_s['id'], _s['ret']))


def _digits(v):
    return re.sub(r'\D', '', str(v or ''))


# Anchors for the sync/merge block lifted out of tracker_template.html. START is the first of the
# three merge helpers; END is the last line of the team-sync section. Everything between —
# _DNC/_DEAD/_lastTouchD, _mergeLead, mergeNotes, the Supabase push/pull — is contiguous.
class CallModeError(Exception):
    """A build-time defect in Call Mode.

    Deliberately a plain Exception, NOT SystemExit. make_callmode is called inside
    `except Exception` in foreclosure_leads.make_tracker, whose documented promise is that a failure
    building this page costs the phone page and never the board the business runs on. SystemExit
    derives from BaseException, so `except Exception` does not catch it — every guard in this file
    would have taken down the entire nightly refresh (board, publish, auction archive) instead of
    skipping one artifact. That risk grew with each guard added: the resolution checks fire whenever
    someone edits tracker_template.html and moves a line, which is a routine, innocuous change.
    """


_OUTCOMES_START = "var CALL_OUTCOMES=["
_SYNC_START = "const _DNC = s => s === 'DO NOT CONTACT';"
_SYNC_END = 'function startTeamSync()'

# The OPT-OUT ENFORCEMENT helpers, which live ~10,500 lines above the sync block and which the sync
# block depends on. Extracted for the same reason and by the same mechanism: `_effOptout` reads the
# audit LEDGER rather than the mutable n.optout scalar, precisely so an opt-out survives a status
# change — reimplementing that from memory on the phone is how enforcement quietly diverges.
_OPT_START = 'function _optLog(n, act, src)'
_OPT_END = 'function _dneg(iso)'

# ═══════════════ THE BOARD'S OWN FUNNEL CLASSIFIER (2026-09-17) ═══════════════
# Alejandro reads nine lane counts off the board — WARM / URGENT / CALL / WRITE / LETTER / DOOR /
# TRACE / WAITING / OUT — and could not find those lanes on the phone at all. Call Mode had nine
# lanes of its own (3-DAY, Emailed, Worker, Urgent 0-7, Sale soon, 46-60, Fresh filings, Balloon,
# Buy-box) over a DIFFERENT, much smaller population, so no lead could be followed from one surface
# to the other and no two numbers agreed.
#
# EXTRACTED, NOT COPIED, for exactly the reason extract_sync_js gives: a second copy of
# _funnelStage would be a fourth lane vocabulary in a codebase whose own CLAUDE.md records what the
# third one cost. The classifier is one function on the board; it stays one function.
#
# Four regions, because the pieces _funnelStage calls are scattered across 8,000 lines of the
# template. Each is anchored on a line that already existed (no marker comments to be helpfully
# tidied away) and each is asserted by name after extraction, so a move fails the build loudly
# instead of shipping a page whose lanes silently read zero.
_CLOCK_START = 'const NO_SALE = 9999;'          # NO_SALE + _hasClock + _aucPassed
_CLOCK_END = 'function _clockTxt(r){'
_SALEDAYS_START = 'function _saleDays(r){'      # live days-to-auction, recomputed not baked
_SALEDAYS_END = 'var WORKER_LANES = {'
_WRONGOWNER_START = 'function _isWrongOwner(n){'  # reads the audit ledger, not the n.wrongown scalar
_WRONGOWNER_END = 'function _clearWrongOwner(c){'
_FUNNEL_START = 'var MAIL_FLOOR_DAYS  = 7;'     # the thresholds + FUNNEL + FUNNEL_ORDER + _funnelStage
# Stops BEFORE _funnelRows on purpose: that one sorts by _netEqOf, which pulls the whole
# _basisOf/_payoffOf/_slien/_jlien enrichment chain the phone does not carry. Call Mode needs the
# CLASSIFIER (which lane is this lead in), never the board's ordering inside a lane.
_FUNNEL_END = 'function _funnelRows(stage){'

# THE TEXT BODIES (2026-09-18, Alejandro: "the script on the text batch i need that text template
# for the call mode py aswell"). The board's text batch bakes smsMsg() into every TEXTQ row; this
# page had a THIRD set of bodies of its own (TEXT_T / TEXT_T_ES, the after-call ladder), so the
# script he approves on the laptop was not the script leaving the phone. Same rule as the two
# extractions above: one source, lifted at build time, never copied.
#
# The region is the two PURE functions only — _textTone (which rung) and _textBodies (the words).
# Everything row-shaped is resolved by the caller, because the board row (r.owners/r.addr) and the
# phone row (r.on/r.a) are different shapes and always will be.
_TEXTTPL_START = 'function _textTone(stage, days, isLP){'
_TEXTTPL_END = 'function _smsHref(ph, msg){'

# Names the BOARD ITSELF calls defensively, so an absent definition cannot throw. Allow-listed
# individually rather than by loosening the resolution guard — each entry states why it is safe, and
# anything not listed still fails the build.
_GUARDED_OPTIONAL = {
    # `if(changed){ save(); try{ recompute(); }catch(e){} render(); }` — already inside try/catch.
    'recompute',
    # `if(typeof refreshDealModal==='function') refreshDealModal();` — typeof on an undeclared name
    # does not throw; the call is skipped. Board-only deal UI that the phone has no equivalent of.
    'refreshDealModal',
}


_JS_BUILTINS = set("""
Object Array String Number Boolean Math JSON Date RegExp Promise Map Set WeakMap WeakSet Error
TypeError RangeError SyntaxError Symbol Proxy Reflect BigInt Function parseInt parseFloat isNaN
isFinite encodeURIComponent decodeURIComponent encodeURI decodeURI escape unescape setTimeout
clearTimeout setInterval clearInterval queueMicrotask requestAnimationFrame fetch atob btoa console
localStorage sessionStorage document window navigator location history crypto performance
TextEncoder TextDecoder Uint8Array Uint16Array Uint32Array Int8Array Float32Array Float64Array
ArrayBuffer DataView Blob File FormData Headers Request Response URL URLSearchParams AbortController
Intl alert confirm prompt structuredClone
if for while switch catch return typeof function new delete void await async yield of in do else
""".split())


def _strip_js(js):
    """Remove comments and string literals so identifier scanning sees CODE, not prose."""
    js = re.sub(r'/\*.*?\*/', ' ', js, flags=re.S)
    js = re.sub(r'(?m)//.*$', ' ', js)
    js = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", js)
    js = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', js)
    js = re.sub(r'`(?:\\.|[^`\\])*`', '``', js)
    return js


def free_identifiers(js, provided):
    """Names this block CALLS but never defines, minus `provided`.

    🔴 THE GUARD THAT WAS MISSING. The old check only asserted five names were PRESENT in the
    extracted block — it never asked whether the block's own dependencies could resolve. They could
    not: `mergeNotes` calls `_dialedAfter` (defined ~10,500 lines above the extraction window) and
    `syncFreshness` (defined one line past its end), so EVERY mergeNotes call threw ReferenceError.
    syncPull swallows it per-row and still stamps a fresh fcLastPull, so both devices reported a
    healthy sync while the phone merged nothing — no teammate opt-out, no Dead, no wrong-number ever
    landed on the one device that places the calls.

    `node --check` cannot see this: an unresolved identifier is valid syntax. Only a resolution check
    catches it, and it must run at BUILD time, because at run time the symptom is a lead that stays
    dialable — indistinguishable from a lead nobody has opted out.
    """
    code = _strip_js(js)
    defined = set(re.findall(r'\bfunction\s+([A-Za-z_$][\w$]*)', code))
    defined |= set(re.findall(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)', code))
    called = set(re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(', code))
    return sorted(called - defined - _JS_BUILTINS - set(provided))


# Everything the generated page defines itself, or bridges, for the extracted board code.
# Anything the block calls that is NOT here and NOT defined inside it fails the build.
# ⚠️ This list is an ASSERTION about the page, and an assertion nobody checks is just a comment —
# the first draft of it silently claimed six names the page does not define, which would have moved
# the ReferenceError rather than fixing it. _assert_page_provides() below now verifies every entry.
_PAGE_PROVIDES = ('save', '_nowTS', '_today', 'render', 'esc', 'toast', '$',
                  'syncFreshness', 'syncStatus', 'loadNotes', 'saveNotes')

# Names the FUNNEL block needs that the page supplies from its own state rather than from a
# `function`/`var X =` declaration _assert_page_provides can see. `notes` rides a multi-declarator
# `var LS=..., notes={};` line, so it is listed here instead of there — the assertion regex would
# report it missing and the build would fail on a name that demonstrably exists.
_FUNNEL_PROVIDES = ('notes',)


def _assert_no_dead_overrides(page, block):
    """Any function declared in BOTH the page and the extracted block must be declared AFTER the
    __SYNCJS__ injection point in the page — otherwise the block's copy is the later declaration,
    it silently wins, and the page's version is dead code.

    Found the hard way: the page's syncStatus bridge (writes #sync) sat above the injection point
    while the extracted block shipped its own syncStatus (targets a #syncstat that does not exist
    here). The block's no-op won; every sync status message was silently swallowed. Same override
    class as _dialedAfter — invisible to node --check, only a position check catches it.
    """
    inj = page.find('__SYNCJS__')
    if inj < 0:
        raise CallModeError('call_mode: __SYNCJS__ placeholder missing from _PAGE')
    block_fns = set(re.findall(r'\bfunction\s+([A-Za-z_$][\w$]*)', _strip_js(block)))
    dead = []
    for name in block_fns:
        m = re.search(r'\bfunction\s+%s\b' % re.escape(name), page)
        if m and m.start() < inj:
            dead.append(name)
    if dead:
        raise CallModeError(
            'call_mode: %s declared in the page ABOVE __SYNCJS__ but also declared inside the '
            'extracted block — the block\'s copy wins and the page\'s is dead. Move the page\'s '
            'declaration below the injection point.' % ', '.join(sorted(dead)))


def _assert_page_provides(page):
    """Every name in _PAGE_PROVIDES must genuinely be defined in the page template."""
    # NB: \b does not work around `$` — it is a non-word char to Python's re but a legal JS
    # identifier. Use an explicit "not followed by an identifier char" lookahead instead.
    def _defined(n):
        e = re.escape(n)
        return re.search(r'(?:function\s+%s|(?:const|let|var)\s+%s)(?![A-Za-z0-9_$])' % (e, e), page)
    missing = [n for n in _PAGE_PROVIDES if not _defined(n)]
    if missing:
        raise CallModeError(
            'call_mode: _PAGE_PROVIDES claims the page defines %s, but it does not. '
            'That claim is what silences the resolution guard, so a false entry re-opens the exact '
            'ReferenceError it exists to catch.' % ', '.join(missing))


def board_call_outcomes(tracker_src):
    """The board's CALL_OUTCOMES, parsed out of the worker document it builds as a JS string."""
    i = tracker_src.find(_OUTCOMES_START)
    if i < 0:
        raise CallModeError('call_mode: CALL_OUTCOMES anchor not found in tracker_template.html — '
                            'the board\'s outcome list moved; update _OUTCOMES_START')
    j = tracker_src.find("'];'", i)
    if j < 0:
        raise CallModeError('call_mode: CALL_OUTCOMES list in tracker_template.html is unterminated '
                            "— expected a \"'];'\" line after the entries")
    block = tracker_src[i:j]
    out = []
    for m in re.finditer(r'\{k:"([A-Za-z0-9_]+)"\s*,\s*t:"([^"]*)"\s*,\s*hrs:(\d+)([^}]*)\}', block):
        out.append((m.group(1), m.group(2), int(m.group(3)), 'sup:true' in m.group(4)))
    if not out:
        raise CallModeError('call_mode: found the CALL_OUTCOMES anchor but parsed ZERO entries — '
                            'the entry shape changed; the comparison below would pass vacuously.')
    return out


def _assert_outcomes_match_board(tracker_src):
    """The phone and the board must speak the SAME outcome vocabulary.

    This file's own docstring has said since day one that CALL_OUTCOMES is COPIED from
    tracker_template.html and must be kept byte-identical, and that a fourth copy would be a
    regression. A docstring is not a guard. Two hand-maintained copies of the same list drift the
    moment one is edited alone, and the failure is silent in the worst way: the phone logs
    `badnum`, the board has never heard of `badnum`, and the outcome simply does not reconcile —
    no error, no missing page, just a touch that quietly means nothing on the other side.

    Extracting the list the way extract_sync_js extracts the merge block would be better still, but
    the two live in different languages (a Python tuple list here, a JS string assembled inside the
    board template there) and the board reads its own copy at runtime. So: keep both, and make
    drift impossible to ship unnoticed.

    SEVERITY IS SPLIT ON PURPOSE, because the two kinds of drift are not the same failure:
      * keys, cooldowns or the suppress flag differ -> RAISE. An outcome that cannot round-trip is
        a data-integrity break, and this file's standing rule is that shipping no Call Mode beats
        shipping one that looks fine and logs nothing.
      * only the human LABEL differs -> WARN loudly and build. A wording change is cosmetic; killing
        the page he dials from over a renamed button would be the worse outage of the two.
    """
    board = board_call_outcomes(tracker_src)
    mine = [(k, t, h, s) for k, t, h, s in CALL_OUTCOMES]

    bkey = [(k, h, s) for k, _t, h, s in board]
    mkey = [(k, h, s) for k, _t, h, s in mine]
    if bkey != mkey:
        raise CallModeError(
            'call_mode: the outcome vocabulary has DIVERGED from the board.\n'
            '  phone (call_mode.CALL_OUTCOMES): %r\n'
            '  board (tracker_template.html)  : %r\n'
            'Keys, cooldowns and the suppress flag must match exactly or an outcome logged on the '
            'phone cannot reconcile on the board. Fix both lists, then rebuild.' % (mkey, bkey))

    drifted = [(k, bt, mt) for (k, bt, _h, _s), (_k, mt, _h2, _s2) in zip(board, mine) if bt != mt]
    if drifted:
        print('call mode: WARNING — outcome LABELS differ between the phone and the board (keys and '
              'cooldowns match, so outcomes still reconcile; only the wording on screen differs):')
        for k, bt, mt in drifted:
            print('   %-10s board=%r  phone=%r' % (k, bt, mt))


def extract_sync_js(tracker_src):
    """Pull the merge + team-sync code VERBATIM out of the board template.

    Copying it would guarantee drift: the phone would keep merging by last year's rules while the
    board moved on, and the divergence would show up as outcomes that quietly fail to reconcile —
    the exact class of silent failure this page exists to remove. So it is extracted at build time,
    and the build FAILS LOUD if the anchors ever move rather than shipping a page with no sync.
    """
    i = tracker_src.find(_SYNC_START)
    if i < 0:
        raise CallModeError('call_mode: sync anchor START not found in tracker_template.html — '
                         'the merge block moved; update _SYNC_START')
    j = tracker_src.find(_SYNC_END, i)
    if j < 0:
        raise CallModeError('call_mode: sync anchor END not found after START — update _SYNC_END')
    j = tracker_src.find('\n', j)
    block = tracker_src[i:j]

    a = tracker_src.find(_OPT_START)
    if a < 0:
        raise CallModeError('call_mode: opt-out anchor START not found — update _OPT_START')
    b = tracker_src.find(_OPT_END, a)
    if b < 0:
        raise CallModeError('call_mode: opt-out anchor END not found after START — update _OPT_END')
    opt_block = tracker_src[a:b]
    for need in ('function _effOptout', 'function _dialedAfter', 'function _isOptedOut',
                 'function _optLog'):
        if need not in opt_block:
            raise CallModeError('call_mode: extracted opt-out block is missing %r' % need)
    for need in ('function _mergeLead', 'function mergeNotes', 'async function syncPush',
                 'async function syncPull', 'SB_TBL'):
        if need not in block:
            raise CallModeError('call_mode: extracted sync block is missing %r' % need)
    # Presence is not enough — see free_identifiers(). Every name the block CALLS must resolve.
    unresolved = [n for n in free_identifiers(opt_block + '\n' + block, _PAGE_PROVIDES)
                  if n not in _GUARDED_OPTIONAL]
    if unresolved:
        raise CallModeError(
            'call_mode: the extracted sync block calls %d name(s) that nothing defines: %s\n'
            '  Every one of these throws ReferenceError at run time and is INVISIBLE to node --check.\n'
            '  Either add it to the NAME BRIDGE in the page, extract the region that defines it, or\n'
            '  add it to _PAGE_PROVIDES if the page genuinely defines it.'
            % (len(unresolved), ', '.join(unresolved)))
    return opt_block + '\n' + block


def _region(src, start, end, what):
    """One anchored slice of the board template, or a loud build failure. Shared by every funnel
    region below so a moved anchor always reports WHICH region moved and what to update."""
    i = src.find(start)
    if i < 0:
        raise CallModeError('call_mode: %s anchor START not found in tracker_template.html — '
                            'the block moved; update the anchor (%r)' % (what, start))
    j = src.find(end, i)
    if j < 0:
        raise CallModeError('call_mode: %s anchor END not found after START — update the anchor '
                            '(%r)' % (what, end))
    return src[i:j]


def extract_funnel_js(tracker_src):
    """Pull the board's nine-lane funnel CLASSIFIER verbatim out of the template.

    Returns the concatenated regions, ready to inject into the Call Mode page. See the anchor
    constants above for why this is extracted rather than reimplemented.

    What comes across, and why each piece is needed by _funnelStage:
      NO_SALE / _hasClock / _aucPassed  the 9999 sentinel rule and "the auction already happened"
      _saleDays                          LIVE days-to-auction (r.days is frozen at build; a page
                                         left open overnight would keep yesterday's lane)
      _isWrongOwner                      reads the audit LEDGER, so it survives a status change
      MAIL_*/URGENT_*/FUNNEL/_funnelStage  the thresholds, the lane table and the classifier

    Deliberately NOT extracted: _funnelRows (sorts by _netEqOf — see _FUNNEL_END) and
    _funnelCounts (a forEach over the board's DATA global; the phone counts its own array).
    """
    clock = _region(tracker_src, _CLOCK_START, _CLOCK_END, 'clock')
    saled = _region(tracker_src, _SALEDAYS_START, _SALEDAYS_END, 'saleDays')
    wrong = _region(tracker_src, _WRONGOWNER_START, _WRONGOWNER_END, 'wrongOwner')
    funl = _region(tracker_src, _FUNNEL_START, _FUNNEL_END, 'funnel')
    block = '\n'.join((clock, saled, wrong, funl))
    # PRESENCE IS NOT ENOUGH, same lesson as extract_sync_js: an anchor can still match while the
    # thing it was pointing at has moved out of the slice. Name every definition we are relying on.
    for need in ('const NO_SALE', 'function _hasClock', 'function _aucPassed', 'function _heldToday',
                 'function _saleDays', 'function _isWrongOwner',
                 'var FUNNEL ', 'var FUNNEL_ORDER', 'function _fDays', 'function _funnelStage'):
        if need not in block:
            raise CallModeError(
                'call_mode: the extracted funnel block is missing %r. The lane counts on the phone '
                'come from this block; shipping it incomplete would put a lead in the wrong lane '
                'or in none, which is the exact drift the extraction exists to prevent.' % need)
    # The nine lane keys are the contract with Alejandro's board. If the board ever renames one,
    # fail here rather than on a phone at a door with a lane button that counts nothing.
    # A BOUNDARY, not a substring: `'trace:' in funl` is also satisfied by `skiptrace:`, so renaming
    # a lane to a name that merely ENDS in the old one would have sailed past this check — which is
    # the likeliest way a rename actually happens.
    for key in ('warm', 'urgent', 'call', 'write', 'letter', 'door', 'trace', 'wait', 'out'):
        if not re.search(r'(?<![A-Za-z0-9_$])%s\s*:' % key, funl):
            raise CallModeError('call_mode: FUNNEL lane %r is gone from the board template. Call '
                                'Mode mirrors the board\'s nine lanes; reconcile them deliberately '
                                'rather than letting the phone drop a lane.' % key)
    unresolved = [n for n in free_identifiers(block, _PAGE_PROVIDES + _FUNNEL_PROVIDES)
                  if n not in _GUARDED_OPTIONAL]
    if unresolved:
        raise CallModeError(
            'call_mode: the extracted funnel block calls %d name(s) that nothing defines: %s\n'
            '  Every one throws ReferenceError at run time and is INVISIBLE to node --check.'
            % (len(unresolved), ', '.join(unresolved)))
    return block


def extract_text_js(tracker_src):
    """Pull the board's TEXT BODIES verbatim out of the template.

    Two functions, both pure:
      _textTone(stage, days, isLP)   which rung of the ladder this lead is on
      _textBodies({first, sender, stEN, stES, td, lp, tone})   -> {en, es}

    Why extracted and not copied is the whole point of the change: the phone already carried its own
    after-call ladder (TEXT_T / TEXT_T_ES), so the board batch said "A case was just filed at the
    courthouse on <street>" while the phone said something else entirely to the same homeowner on the
    same case. A copy would have made that two copies to keep in step; this makes it one.

    Assertions below are PRESENCE BY NAME, same lesson as the other two extractors: an anchor can
    still match after the thing it pointed at has moved out of the slice, and the symptom of a
    half-extracted block here is a text that goes out with a missing branch rather than a crash.
    """
    block = _region(tracker_src, _TEXTTPL_START, _TEXTTPL_END, 'textTemplates')
    for need in ('function _textTone', 'function _textBodies', 'return {en: en, es: es};'):
        if need not in block:
            raise CallModeError(
                'call_mode: the extracted text-body block is missing %r. The phone composes its '
                'batch text from this block; shipping it incomplete would put a different script '
                'in front of the homeowner than the one the board sends.' % need)
    # Every tone the board can select must have a body in the slice, in BOTH languages. A missing
    # branch is silent: _textBodies would return {en: undefined} and the composer would open with
    # the literal word "undefined" in a message to a homeowner.
    for tone in ("'urgent'", "'t3'", "'followup'"):
        if tone not in block:
            raise CallModeError('call_mode: tone %s has no body in the extracted text block' % tone)
    for need in ('Hola', 'Hi', 'Biscayne Solutions Group'):
        if need not in block:
            raise CallModeError('call_mode: the extracted text block has no %r — the Spanish and '
                                'English bodies must both come across (Alejandro texts both).'
                                % need)
    unresolved = [n for n in free_identifiers(block, _PAGE_PROVIDES)
                  if n not in _GUARDED_OPTIONAL]
    if unresolved:
        raise CallModeError(
            'call_mode: the extracted text block calls %d name(s) that nothing defines: %s\n'
            '  These functions are meant to be PURE — if one now reads a board row, the extraction '
            'cannot work and the region has to be re-cut.' % (len(unresolved), ', '.join(unresolved)))
    return block


_COMPANY_RE = re.compile(r'\b(LLC|INC|CORP|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|EST)\b', re.I)


def _greet_name(d):
    """The name the page may greet with, in FIRST-name-first order — per COUNTY, measured not guessed.

    Three counties, three roll conventions (every count from real cached data):
      - Broward: "LAST,FIRST" with a comma (1,295/4,009) — owner_clean flips it. Fine.
      - Miami-Dade comma-less: already "FIRST LAST" ("PAUL GREEN", "EDDIE SQUIRE"). Fine.
      - Palm Beach: comma-less "LAST FIRST" — 340 of 340 sampled, zero commas. owner_clean has no
        comma to key on, so it ships unflipped and firstName() took token[0] — the SURNAME. The
        page asked "How did it go with White?" about SHUROD White, and would have texted "Hi White,".
        Caught from a live screenshot; only PB leads carry a (561) number.

    PB is identified by its case format (502025CA... — always '50', vs MD's '20XX-' and Broward's
    'CACE-'). Companies are left untouched: flipping "XPRESS ASSET MANAGEMENT LLC" helps nobody and
    firstName() ignores company rows anyway. Trailing '&' co-owner markers are already stripped by
    owner_clean upstream ("COTTO JOSE J &" -> "COTTO JOSE J" -> flips to "JOSE J COTTO").

    2026-08-27: THE CASE-NUMBER HEURISTIC WAS THE BUG. Keying the flip on "case starts with 50"
    assumed case FORMAT predicts roll CONVENTION. It does not, and two lanes proved it on the live
    payload: the Fresh-filings lane sets `oname` from _rec_name(), whose own docstring says it
    emits "Last, First" — the RECORDS-search order — so 353 of 400 shipped rows greeted people as
    "Hi, is this Hazan?" (Elizabeth Hazan) and "Hi, is this Amlong?" (an active deal file). PB tax
    deeds carry case '2026-2658TD', which also fails the '50' test, so "BROWN ROGER L" shipped
    unflipped too. Read the STRING, never the case number: a comma is an explicit LAST,FIRST
    marker in any county, and the PB comma-less shape stays county-gated as before.
    """
    on = (d.get('oname') or '').strip()
    case = str(d.get('case') or '')
    owners_first = (d.get('owners') or '').split(';')[0]
    if not on or _COMPANY_RE.search(on):
        return on[:40]
    # 1) EXPLICIT "LAST, FIRST" — self-identifying, no county guess required.
    if ',' in on:
        last, _, rest = on.partition(',')
        last, rest = last.strip(), rest.strip()
        if last and rest:
            return (rest + ' ' + last).strip()[:40]
    # 2) Palm Beach's comma-less "LAST FIRST" roll (340/340 sampled). Still county-gated, but now
    #    by the lead's own county field with the case prefix only as a fallback for older rows.
    _cty = str(d.get('county') or '').upper()
    if (('PALM' in _cty) or case.startswith('50')) and ',' not in owners_first:
        toks = on.split()
        if len(toks) >= 2:
            on = ' '.join(toks[1:] + [toks[0]])   # LAST FIRST [M] -> FIRST [M] LAST
    return on[:40]


def _quo_latest():
    """case -> the most recent analyzed Quo call, from quo_sync.py's local ledger.

    Local and gitignored (homeowner conversations; the repo is PUBLIC) -- it ships only inside the
    encrypted payload, exactly like every other lead field. Missing file = empty dict, zero cost:
    CI has no ledger and must not care."""
    try:
        led = json.load(open(os.path.join(HERE, 'quo_calls.json'), encoding='utf-8'))
    except Exception:
        return {}
    out = {}
    for rec in (led.get('calls') or {}).values():
        c = str(rec.get('case') or '').strip()
        if not c:
            continue
        if c not in out or str(rec.get('at') or '') > str(out[c].get('at') or ''):
            out[c] = rec
    return out


def call_rows(slim, optouts=None, deads=None, max_days=60, cap=400):
    """-> (rows, total_qualified). Selection mirrors call_list.collect + _workerEligible.

    Everything is decided HERE, at build time, so the phone does no filtering — it only renders.
    """
    optouts, deads = (optouts or {}), (deads or {})
    # THE PERSON'S FULL CASE LIST, from the build's own grouping — including cases that will NOT ship
    # (auction too far out, no phone, over the cap). The page's personCases() used to try to recover
    # these from notes[c].pkey, but NOTHING EVER WRITES pkey INTO A NOTE — the board keeps it on the
    # DATA rows only — so that branch could never fire and the 3-text cap saw only the cases on the
    # phone. (The test that "proved" it passed on a hand-fabricated note. Test the data the app
    # actually produces, not the mechanism you imagined.) Groups only, so the common singleton adds
    # zero bytes; ships INSIDE the encrypted payload because case lists are outreach intelligence.
    _groups = {}
    for d in slim:
        k = d.get('pkey')
        if k and d.get('case'):
            _groups.setdefault(k, []).append(d['case'])
    # IDENTITY-LEVEL OPT-OUTS — the ones with no case attached.
    # `optouts` is keyed by CASE, and ALSO by '@'+hash(email) / '#'+hash(digits) for a stop that
    # arrived without one (an email reply: they wrote "stop", they never quoted a case number).
    # `case in optouts` can never match those keys, so a person who said stop by email stayed
    # dialable HERE — on the one surface whose entire purpose is dialing them. The board has had
    # this via _isOptedOutPerson() for months; Call Mode never got it, and neither did the Morning
    # Worker until today. Same fix, third surface.
    _oo_ident = {str(k) for k in optouts if str(k)[:1] in ('@', '#')}
    _ak = None
    if _oo_ident:
        # function-level import on purpose: foreclosure_leads imports THIS module, so a top-level
        # one would be circular. Hash must be the same one the ledger was keyed with, never a copy.
        try:
            from foreclosure_leads import _addr_key as _ak
        except Exception:
            _ak = None

    def _identity_opted(lead):
        if not _oo_ident or not _ak:
            return False
        for _e in (lead.get('emails') or []):
            _e = str(_e or '').strip().lower()
            # raw key OR hashed key — optouts.json stores the address verbatim; only the
            # board bake hashes it. Hashed-only matched nothing against the real ledger.
            if _e and (('@' + _e) in _oo_ident or ('@' + _ak(_e)) in _oo_ident):
                return True
        for _p in (lead.get('phones') or []):
            # Digits only, NO country-code normalisation — deliberately matching what the ledger key
            # was hashed from and what the board's _isOptedOutPerson does. So a '+1' 11-digit number
            # would NOT match a 10-digit opt-out. Checked 2026-08-26: all 9,885 phones in the
            # skiptrace cache are 10 digits, so this is theoretical today. If 11-digit numbers ever
            # arrive, strip a leading '1' HERE, in the board, and in the bake — all three or none,
            # or the hashes stop agreeing and the suppression silently stops matching.
            _p = re.sub(r'\D', '', str(_p or ''))
            if _p and (('#' + _p) in _oo_ident or ('#' + _ak(_p)) in _oo_ident):
                return True
        return False

    out = []
    _ident_dropped = 0
    _notowner_dropped = 0        # numbers kept off the dial queue as not-the-owner (see below)
    import diligence_gate as _DG
    _dg = _DG.Tally()
    _quo = _quo_latest()
    for d in slim:
        case = d.get('case') or ''
        if not case or case in optouts or case in deads:
            continue
        # BALLOON rows (balloon_leads.py, st:'BAL'): an LLC investor with a maturing hard-money
        # note. Three homeowner gates below do not apply and would silently drop the whole lane:
        # the diligence gate (asks about a foreclosure that does not exist), the auction-window
        # gate (no auction — the clock is the note's est. maturity) and the sale-passed gate.
        is_bal = (d.get('st') == 'BAL')
        if _identity_opted(d):
            _ident_dropped += 1
            continue
        if d.get('sibclaimed') or d.get('saleBkAct') or d.get('lpDismissed'):
            continue
        if d.get('title_status') == 'transferred':          # ownership gate — they no longer own it
            continue
        # DILIGENCE GATE — same class of drop as the ownership gate directly above, and placed with
        # it on purpose: both are "this lead is not what the card says it is", both are decided at
        # build time, and neither is a preference. Sits BEFORE the equity floor and the phone-pair
        # work below so a held lead never pays for a rank translation it will not use.
        _dgv = _dg.check(d) if not is_bal else {'hold': False}
        if _dgv['hold']:
            continue
        # EQUITY FLOOR. A KNOWN, deeply underwater lead is a call with no possible win: no equity to
        # protect, no surplus at sale (hammer < judgment), no service to offer anyone. The sort puts
        # imminent auctions FIRST, so the 08-16 live session opened on a -71% corporate condo
        # ($698k judgment / $409k value, sale next day) — a lead no outcome could have salvaged.
        # KNOWN is the load-bearing word: eq=None means NOT CHECKED and always ships (the not-
        # checked-is-not-zero rule). -25 keeps thin-but-arguable deals; override via env.
        _eqf = d.get('eq')
        try:
            _eqf = float(_eqf)
        except (TypeError, ValueError):
            _eqf = None
        if _eqf is not None and _eqf <= float(os.environ.get('CALLMODE_EQ_FLOOR', '-25')):
            continue
        phones, phdnc = (d.get('phones') or []), (d.get('phdnc') or [])
        # DROP DNC NUMBERS FROM THE RECORD, do not flag them. `phbest is None` means every number
        # this lead has carries a DNC flag, and the correct number of dials on those is zero.
        # KEEP THE ORIGINAL INDEX WITH EACH NUMBER. `phrank` and `phbest` are positions in `phones`,
        # and every filter below shifts positions — so indexing rank by a position in the FILTERED
        # list mislabels numbers. Drop one DNC number and every rank label after it slides up one:
        # the page would print "CALL FIRST" over a number the ranker had put last, which is worse
        # than no label at all because he trusts it.
        # NOT-THE-OWNER NUMBERS DO NOT ENTER THE DIAL QUEUE. This page is one screen, one script,
        # one person: it opens "Hi <first name>" and talks about their foreclosure. A listing agent
        # or an office number that sits on three other owners' leads is worth a call, but not this
        # call, and there is nowhere on this page to say so. The board still shows them on the row,
        # tagged and dialable, which is where a call to the agent belongs.
        # `hh`/`nm` (household member, namesake) stay — reaching the owner through the household is
        # the reason those numbers were kept — and phone_rank has already scored them below the
        # owner's own, so they never lead the queue.
        phsrc = (d.get('phsrc') or [])
        pairs = []
        n_dnc = 0
        for oi, p in enumerate(phones):
            if phdnc[oi] if oi < len(phdnc) else False:
                n_dnc += 1
                continue                                   # DNC numbers are dropped, never flagged
            if (phsrc[oi] if oi < len(phsrc) else '') in ('ag', 'xl'):
                _notowner_dropped += 1
                continue
            dg = _digits(p)
            if len(dg) == 10:
                pairs.append((oi, dg))
        if not pairs:
            continue
        keep = [p for _, p in pairs]
        keep_oi = [oi for oi, _ in pairs]                  # keep[k] came from phones[keep_oi[k]]
        # COUNT ONLY THE DNC ONES. `len(phones) - len(keep)` also swept up numbers dropped for being
        # malformed, while the chip says "do-not-call flag on file" — so a lead carrying one junk
        # number told him a human had asked not to be called. That is the difference between a person
        # he may not contact and a person he simply has no good number for, and the label was picking
        # the wrong one. Same class as "0% equity" and "$0 owed": a number that is not its label.
        withheld = n_dnc
        days = d.get('days')
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 9999
        is_lp = (d.get('st') == 'LP')
        if is_bal:
            # the clock is the note's est. maturity (balloon_leads.py). Negative = past the 24-month
            # wall = HOTTEST, so it must not read as "auction already passed" below.
            _bd = (d.get('bal') or {}).get('days')
            days = int(_bd) if isinstance(_bd, (int, float)) else 9999
        if not is_lp and not is_bal and (days < 0 or days > max_days):     # auction already passed, or too far out
            continue
        # An LP row that GAINED a sale date which then passed is a sold property, not a fresh
        # filing — the board's EARLY lane closed this exact hole; mirror it here (audit 2026-08-18).
        if is_lp and d.get('auction') and days < 0:
            continue
        # Non-business classes never reach the dialer: vacant land and timeshares/unlinked parcels
        # are not rescuable homes, and neither warn nor vac used to ship to this page.
        if d.get('vac'):
            continue
        if re.search(r'timeshare|parcel not linked', str(d.get('warn') or ''), re.I):
            continue
        rank = d.get('phrank') or []
        best = d.get('phbest')
        # Put the ranked-best number first; the phone never re-ranks. `best` is a position in
        # `phones`, so translate it through keep_oi rather than using it as a keep index.
        order = list(range(len(keep)))
        if isinstance(best, int) and best in keep_oi:
            bk = keep_oi.index(best)
            order = [bk] + [k for k in order if k != bk]
        eq = d.get('eq')
        try:
            eq = float(eq)
        except (TypeError, ValueError):
            eq = None

        def _n(key):
            """Numeric or None. None means NOT CHECKED and must render as such — never as 0.
            Coverage is genuinely sparse (payoff on 183 of 400, orsurvsen on 13), and a $0 where the
            truth is 'nobody looked' is the same lie as the '0% equity' bug already fixed in
            call_list.py. The renderer keys off null, so the distinction has to survive here."""
            v = d.get(key)
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v if v else None

        def _s(key, cap=44):
            v = str(d.get(key) or '').strip()
            return v[:cap] if v else None

        # ABSENTEE as one byte, not the 23-byte mailing address. First line of mail vs addr is the
        # same test _callSheet uses; it decides "knock" vs "call, don't knock".
        _m1 = (d.get('mail') or '').split(',')[0].strip().upper()
        _a1 = (d.get('addr') or '').split(',')[0].strip().upper()
        row = {
            'c': case,
            'o': (d.get('owners') or d.get('oname') or '').strip(),
            # GREETING NAME, separate from the display string above.
            # `owners` comes off the county roll as "LAST,FIRST" on 32% of leads (measured across
            # 4,009 real rows; Broward is almost entirely this shape). Deriving a first name from it
            # takes the token before the comma — the SURNAME — so the page opened with "Hi Gordon"
            # to Steve Gordon and "Hi Joseph" to Milouse Joseph, on the call AND in every text.
            # The pipeline already fixes this: owner_clean flips "Last, First" to "First Last"
            # (foreclosure_leads.py:536-539) and ships as `oname`. Ship it and use it. ~14 B/lead.
            'on': _greet_name(d),
            'a': (d.get('addr') or '').strip(),
            # Advisory candidates stay separate from the address used by scripts and links.
            'ag': (d.get('addrGuess') or '').strip() or None,
            'aw': (d.get('addrWhy') or '').strip() or None,
            'x': d.get('auction') or d.get('filedDate') or d.get('filed') or '',
            'd': days,
            'lp': 1 if is_lp else 0,
            'p': [keep[k] for k in order],
            # rank is indexed by the ORIGINAL phones position, recovered via keep_oi.
            'r': [(rank[keep_oi[k]][:1].upper()
                   if keep_oi[k] < len(rank) and rank[keep_oi[k]] else '') for k in order],
            # WHOSE NUMBER (foreclosure_leads.PHSRC_*), same index as `p`. Only the two that change
            # what the caller SAYS ship: 'hh' (a household member at the address) and 'nm' (matched
            # on the owner's name only, so possibly a namesake). An owner number ships '' and costs
            # one byte. 'ag'/'xl' never appear — those numbers are filtered out of `keep` above.
            # Without this the page opens "Hi <first name>" on the owner's spouse or on a stranger
            # with the same name, and the caller has no way to know from the screen.
            'ps': ([(phsrc[keep_oi[k]] if keep_oi[k] < len(phsrc)
                     and phsrc[keep_oi[k]] in ('hh', 'nm') else '') for k in order]
                   if any(x in ('hh', 'nm') for x in phsrc) else None),
            'k': withheld,
            # EMAIL COUNT, never the addresses. The board's funnel splits WRITE from TRACE on
            # whether a lead is reachable by email at all, so the phone needs the fact and not the
            # data. Shipping the addresses would put 1,400 homeowner emails on a handset to answer
            # a yes/no question (and this page's whole DNC discipline is that what is not in the
            # payload cannot leak). None on the common no-email row; the null-strip drops the key.
            'ne': (len([e for e in (d.get('emails') or []) if str(e or '').strip()]) or None),
            'pk': d.get('pkey') or ('C' + case),
            # every case this person owns, when there is more than one — see _groups above.
            # None for singletons, and the null-strip below removes it: the common case costs 0 bytes.
            'pcs': (_groups.get(d.get('pkey') or '') if len(_groups.get(d.get('pkey') or '') or []) > 1 else None),
            # ---- money (null = not checked) ----
            'v': _n('value'), 'py': _n('payoff'), 'ja': _n('jaccr'), 'jg': _n('judg'),
            'jd': _s('jdate', 10), 'arv': _n('arv'), 'an': _n('arvn'),
            'e': eq if (eq is not None and (d.get('value') or 0)) else None,
            # EQUITY VERIFIED (2026-08-27). `e` alone cannot tell a caller whether the number is a
            # FACT or a GUESS, and the sort below ranked a guessed 90% above a traced-and-proven
            # 45% — so sessions opened on the least certain leads on the board. 1 = the recorded
            # chain was actually traced (equity_state clear/priced).
            # ⚠️ NAMED 'eqv', NOT 'v'. This shipped as 'v' for one build and Python kept the LAST
            # duplicate key — silently deleting `'v': _n('value')` six lines up, so all 400 rows
            # carried a 0/1 flag where six renderers read DOLLARS: the card printed "Property
            # value $0", and the outcome screen computed `r.v - owed` and told the caller
            # "UNDERWATER $480k" about a lead whose own card said "Equity 4% VERIFIED". A dict
            # literal will not warn you; only the payload does.
            'eqv': 1 if str(d.get('eqstate') or '') in ('clear', 'priced') else 0,
            'ss': (d.get('orsurvsen') if isinstance(d.get('orsurvsen'), (int, float)) else None),
            'oc': _s('orconf', 6), 'td': _n('taxDue'), 'et': _n('etax'),
            # ---- clock ----
            'sv': d.get('saleSurv'), 'sc': _n('saleSched'), 'sw': _s('saleWho', 6),
            'bk': _n('saleBK'), 'sl': _s('saleLift', 10), 'cs': _s('cstatus', 22),
            # docket sale result (sale_results.py -> row.sr): held / cancelled / moved / at risk /
            # amended judgment. None on almost every row; the null-strip drops it.
            'sr': (lambda v: ({k: v[k] for k in ('st', 'd', 'nd', 'was', 'why', 'amj', 'ama', 'bkb', 'obj', 'ev')
                               if v.get(k) not in (None, '')} if isinstance(v, dict) and v.get('st') else None))(d.get('sr')),
            # ---- last Quo call (transcript-backed). None on most rows; the null-strip removes it.
            'qc': (lambda q: ({'w': str(q.get('at') or '')[:16], 'du': q.get('dur') or 0,
                               's': ' '.join(q.get('summary') or [])[:180],
                               'fl': (q.get('flags') or [])[:4]} if q else None))(_quo.get(case)),
            # ---- who is foreclosing ----
            'pl': _s('plaintiff', 46), 'ft': _s('ftype', 10),
            # ---- live docket (gen_dockets.py -> slim row.dk). TRIMMED HARD for the handset: the
            # STATUS and the newest filings answer "what actually happened on this case?" on the
            # phone; shipping 199 entries per lead to a pocket device is payload for no decision.
            # Status is the one that ends arguments — a CLOSED/RECLOSED case means the owner really
            # did resolve it, which is the "ya no tiene problema" answer, verified instead of guessed.
            # None when not pulled; the null-strip drops the key entirely.
            'dk': (lambda k: ({'s': (k.get('status') or '')[:14], 't': (k.get('type') or '')[:28],
                               'f': k.get('filed') or '', 'n': k.get('n') or 0,
                               # attorney(s) of record for the DEFENDANT side — '' when none
                               'a': ', '.join(sorted({(p.get('a') or '').strip() for p in (k.get('parties') or [])
                                                      if str(p.get('t') or '').upper().startswith('DEFEND')
                                                      and (p.get('a') or '').strip()}))[:60],
                               'e': [{'d': e.get('d', ''), 'x': (e.get('x') or '')[:60]}
                                     for e in (k.get('ents') or [])[-12:]]} if k else None))(d.get('dk')),
            # ---- the person / property ----
            # booleans only when TRUE — an absent key reads as false in the renderer, so shipping
            # `"hs":0` on 1,300 rows is pure payload for no information
            'hs': 1 if d.get('hs') else None,
            'ab': 1 if (_m1 and _a1 and _m1 != _a1) else None,
            'dd': _s('dor_desc', 30), 'bd': _n('beds'), 'ba': _n('baths'), 'sf': _n('sqft'),
            'zs': _s('zstatus', 12),
            # BUY-BOX. A standing acquisition criterion (buybox.py) that make_tracker stamps onto
            # the row. Shipped as two short keys so the phone can lane on it without re-deriving
            # anything: 'bb' = which box, 'bbs' = CONFIRMED | UNKNOWN | UNDERWATER.
            # IT IS AN INTERNAL SORT, NOT A DIFFERENT PITCH. These are the same distressed owners
            # and they get the same advisor script — "I am not trying to buy your house" stays
            # true, because it is. Leading a preforeclosure homeowner with "I want to buy it" is
            # the exact predatory framing the language law exists to prevent. The box decides
            # WHICH doors get knocked first, never what is said at them.
            # 'bbtd' = this match is a TAX DEED sale. It stays in the buy-box JSON and the morning
            # digest, because it is a real way to acquire a house, but it is NOT a callable lead:
            # the record owner is usually an LLC, the money figure is an opening bid rather than a
            # payoff, and the advisor script below is written for a distressed homeowner. Dialing
            # one means reading foreclosure-rescue language to a holding company.
            'bb': _s('bb', 12), 'bbs': _s('bbstate', 12),
            'bbtd': 1 if d.get('bbtd') else None,
            # HIS FILE — the research links. Ship the two SEEDS (folio + county), not five long
            # URLs: 400 rows x ~450 chars of URL is ~180 KB on a page that has to open on a phone
            # at a door. The page rebuilds appraiser/tax/docket/people from these (see fileLinks).
            # ALPHANUMERIC, not digits-only: Broward folios carry letters (494213BA0140) and
            # stripping them produced a wrong parcel on 261 Broward leads (caught by diffing every
            # derived URL against the board's real ones before shipping).
            'fo': re.sub(r'[^0-9A-Za-z]', '', str(d.get('folio') or '')).upper()[:26] or None,
            'ct': (str(d.get('county') or 'MIAMI-DADE').strip().upper()[:2] or None),
            'z': (re.search(r'(\d{5})\s*$', (d.get('addr') or '')).group(1)
                  if re.search(r'(\d{5})\s*$', (d.get('addr') or '')) else None),
            'po': _s('paOwner', 34) if d.get('ownerMismatch') else None,
            # MISCALCULATED DEAL (8/17 masterclass): actively listed/pending with the sale ≤21 days
            # out — the realtor-flip lane. Ship the agent's contact so the CALL can go to the
            # gatekeeper (drill card 12) instead of dying at a shielded owner.
            'ml': (1 if (str(d.get('zstatus') or '').upper() in ('LISTED', 'PENDING')
                         and isinstance(d.get('days'), (int, float)) and 0 <= d['days'] <= 21) else None),
        }
        if row.get('ml'):
            row['zag'] = _s('zagent', 30) or None
            row['zap'] = _digits(d.get('zagentphone'))[:11] or None
        # `lp` and `k` follow the same rule; `d` (days) must NOT — 0 means the auction is TODAY.
        if not row['lp']:
            row.pop('lp')
        if not row['k']:
            row.pop('k')
        # ---- flags: one packed string instead of a dozen booleans ----
        if is_bal:
            # Ship the note on the row so the phone can lane on it and render THE NOTE band. `d` is
            # already the maturity countdown (above); `x` is the est. date the card prints.
            _b = d.get('bal') or {}
            row['st'] = 'BAL'
            row['lp'] = 0
            row['x'] = _b.get('maturity') or ''
            row['bal'] = {k: _b.get(k) for k in ('lender', 'amt', 'origin', 'age_mo', 'maturity', 'days',
                                                 'ltv', 'verdict', 'why', 'fits') if _b.get(k) is not None}
        fl = ''
        if d.get('eqfake'): fl += 'E'          # equity is a gross upper bound
        if d.get('ju'): fl += 'J'              # judgment not posted
        if d.get('mr'): fl += 'M'              # a first mortgage survives this sale
        if d.get('co'): fl += 'C'              # company / trust owner
        if d.get('condo'): fl += 'D'           # condo -> estoppel
        if d.get('wid'): fl += 'W'             # widow/widower exemption (elder proxy)
        if d.get('ip'): fl += 'I'              # individual plaintiff
        if d.get('taxCert'): fl += 'T'         # tax certificate sold -> second clock
        if d.get('etaxest'): fl += 'e'         # the tax figure is modeled, not billed
        if d.get('sib') or d.get('orsecond'): fl += 'S'   # SECOND CASE — Jesse's tell
        if d.get('orhoa'): fl += 'H'
        if d.get('codeConcern'): fl += 'X'
        if fl:
            row['f'] = fl
        # ---- SEAT BUCKET: how two phones stop dialing the same person ----------------------
        # A stable 0-11 bucket derived from the case number ALONE. Twelve because it divides
        # evenly by 2, 3, 4 and 6, so any realistic crew splits the list without remainder.
        #
        # This is the layer that actually prevents the collision, and it does it by making
        # coordination unnecessary rather than by coordinating: with two seats, Alejandro's queue
        # contains only even buckets and Carlos's only odd, so the same lead is never in both
        # queues at the same moment. No server call, no lock to acquire, no latency window, and it
        # works with the phone in airplane mode at a door with no signal.
        #
        # The live claim (see _clmOwner on the page) is the SECOND layer and covers only the
        # exception — someone deliberately working out of lane. It rides the 45s sync, so it can
        # never be the primary defence: two people opening the same lead inside the same 45
        # seconds would both see it unclaimed. The partition has no such window.
        #
        # Deterministic across builds: same case -> same bucket, every night, so a lead does not
        # hop between callers when the board refreshes mid-session. Changing the SEAT COUNT does
        # reshuffle assignments, which is correct and is why the page warns before doing it.
        _cs = str(d.get('case') or '')
        if _cs:
            _h = 2166136261
            for _ch in _cs:                       # FNV-1a, 32-bit
                _h = ((_h ^ ord(_ch)) * 16777619) & 0xFFFFFFFF
            row['sb'] = _h % 12
        out.append({k: v for k, v in row.items() if v is not None and v != '' and v != []})
    # HIGHEST-CONVERTING FIRST (2026-08-19, his words: "I don't want clients whose auction date is
    # today or two-three days out. I need the highest converting leads.") The old key was
    # soonest-sale-first, which opened every session on the exact leads nothing can save — a sale
    # tomorrow leaves no time to reinstate, list, or petition anything. Convertibility is equity
    # to protect TIMES runway to act:
    #   band 0: sale 7-45 days out — a real clock AND real time to work it (the prime window)
    #   band 1: 46-120 days out, or a fresh LP with no date — pure runway, first-mover ground
    #   band 2: 5-6 days — very tight; worth a dial only when the equity is real
    #   band 3: 0-4 days (or already passed) — too late to convert; surplus-only talk, parked LAST
    # Within a band: KNOWN equity high-to-low first, then sooner sale. .get() not [] — null/empty
    # fields are stripped above, so `e` is often absent; unknown equity sorts after known and must
    # never masquerade as 0 (the not-checked-is-not-zero rule).
    def _band(r):
        # BALLOON rows: the clock is a note maturity, not an auction, and "0-4 days" is the HOTTEST
        # state (the borrower must refinance or extend THIS week) — the opposite of a foreclosure at
        # 0-4 days. Prime band, soonest first via `d`. They never mix with homeowner rows on the
        # phone (own lane) and the sheet drops them; this only keeps the shared sort honest.
        if r.get('st') == 'BAL':
            return 0
        d = r.get('d', 9999)
        if d <= 4:
            return 3
        if d <= 6:
            return 2
        if d <= 45:
            return 0
        return 1
    # VERIFIED EQUITY OUTRANKS A BIGGER GUESS. Within a band the old key sorted purely on the
    # equity NUMBER, so a lead whose 90% was never traced opened the session ahead of one proven
    # at 45% by the recorded chain — the caller spent the freshest hour of the day on the leads
    # most likely to evaporate (the Acosta pattern, at the top of the queue). Traced first, then
    # the number. Untraced leads still ship; they just stop cutting the line.
    out.sort(key=lambda r: (_band(r), 0 if r.get('eqv') else 1,
                            0 if r.get('e') is not None else 1, -(r.get('e') or 0),
                            r.get('d', 9999)))
    # ---- ONE ROW PER CASE (2026-09-10) --------------------------------------------------------
    # A case listed on two auction dates is two rows, which put the same HUMAN on the phone twice
    # with two different sale dates. Rationale, evidence and the sooner-date rule live on
    # dedupe_calendar_rows. The merge in foreclosure_leads runs the same pass, so this is normally
    # a no-op — kept because call_rows is called directly (call_sheet, the tests, any future
    # caller) and the dial list is the surface where a double-serve is a double-dial.
    out, _collapsed, _sooner = dedupe_calendar_rows(out, key='c', days='d', date='x')
    if _collapsed:
        # Say it out loud, same rule as every other suppression here: a list that quietly shrank
        # looks exactly like a list that was always that size.
        print('call mode: %d duplicate calendar row(s) collapsed — same case listed on more than '
              'one auction date; kept the fullest row%s'
              % (_collapsed, (', %d of which now carry an EARLIER sale date to verify' % _sooner)
                 if _sooner else ''))
    if _notowner_dropped:
        # Out loud, like every other suppression on this page. These numbers look perfectly
        # dialable on the board and vanish here, so a silent drop is indistinguishable from a
        # skip trace that simply returned less.
        print('call mode: %d number(s) kept out of the dial queue — the lead\'s own listing agent, '
              'or a number sitting on three or more different owners' % _notowner_dropped)
    if _ident_dropped:
        # Say it out loud. A suppression that removes people silently is indistinguishable from a
        # queue that was always this size, and this one drops leads that LOOK perfectly callable.
        print('call mode: %d lead(s) dropped — the person opted out by email/phone with no case '
              'attached (identity ledger)' % _ident_dropped)
    # Same rule, same reason — and the diligence holds are the ones that look MOST callable of all,
    # because a held lead's card still shows equity, a phone and an auction date.
    _dg.report('call mode', indent='')
    # ---- THE CAP IS A WINDOW, NOT A CEILING (2026-09-19) --------------------------------------
    # `out[:cap]` shipped the top `cap` rows of a FULLY DETERMINISTIC sort (band, equity verified,
    # equity, days). Nothing in that key moves day to day except `days`, which only ever counts
    # down — so the same ~400 rows shipped every night and every lead ranked past the cap was
    # unreachable from the phone for as long as it stayed there. Alejandro, 2026-09-19: "why is it
    # the same people over and over again never new people to text?" The board's own worker queue
    # had already learned this lesson twice (the EARLY-lane _filedMs tie, the phone-only bucket
    # rotation); this is the same defect on the dial list.
    #
    # The head is PROTECTED because the head is the point: a sale 7-45 days out with proven equity
    # is what the session should open on, and rotating that away would trade one complaint for a
    # worse one. Only the REMAINDER of the cap rotates, by day of month, so the whole qualified
    # pool reaches the phone over a month instead of never. Deterministic within a day (same build,
    # same list) and needs no stored cursor.
    # Head defaults to HALF the cap (200 of the historical 400), so a caller that scales the cap
    # with the number of seats scales the protected head with it.
    head_n = max(0, min(cap, int(os.environ.get('CALLMODE_HEAD', str(cap // 2)))))
    total = len(out)
    if total > cap:
        head, tail = out[:head_n], out[head_n:]
        slots = cap - head_n
        n_fresh = 0
        if slots > 0 and tail:
            # ---- FRESH FILINGS ARE PINNED, NOT ROTATED (2026-09-21) ------------------------------
            # A brand-new LP has no sale date and no priced equity, so the rank puts it just PAST
            # the head (ranks 222-251 on the 09-21 book) — which is exactly the front of the tail.
            # The rotation then skipped that front two days in three: on 09-21, 30 of the 32 leads
            # filed in the last 7 days were on NEITHER phone. The first call on a fresh filing is
            # the whole first-mover edge; a lead that waits for its rotation day has lost it.
            # So tail leads filed within CALLMODE_FRESH_DAYS ride right behind the head, in rank
            # order, capped at half the slots so the rotation still walks the rest of the book.
            fresh_days = max(0, int(os.environ.get('CALLMODE_FRESH_DAYS', '14')))
            _age = {}
            if fresh_days:
                _today = _dt.date.today()
                for d in slim:
                    c = (d.get('case') or '').strip()
                    v = str(d.get('filedDate') or '').strip()
                    try:
                        _age[c] = (_today - _dt.datetime.strptime(v, '%m/%d/%Y').date()).days
                    except ValueError:
                        pass
            fresh = [r for r in tail if 0 <= _age.get(r.get('c'), -1) <= fresh_days][:slots // 2]
            _pinned = {id(r) for r in fresh}
            rest = [r for r in tail if id(r) not in _pinned]
            n_fresh = len(fresh)
            left = slots - n_fresh
            if rest and left > 0:
                # Stride by the window, not by a small constant: a stride smaller than the window
                # overlaps consecutive days almost completely, which is the bug it is meant to fix.
                rot = (_dt.date.today().day * left) % len(rest)
                rest = rest[rot:] + rest[:rot]
            out = head + fresh + rest[:max(0, left)]
        else:
            out = head[:cap]
        # NO SILENT CAPS. A list that quietly shrank looks exactly like a list that was always
        # this size — the same rule the dedupe and identity-drop notices above follow.
        print('call mode: %d qualified, %d shipped — top %d by rank always, %d fresh filing(s) '
              'pinned, the remaining %d slots rotate daily through the other %d so the tail '
              'reaches the phone'
              % (total, len(out), len(head), n_fresh, len(out) - len(head) - n_fresh,
                 max(0, total - len(head) - n_fresh)))
    return out[:cap], total


def coverage_rows(slim, dial_cases, optouts=None, deads=None):
    """-> (rows, n_suppressed) : one SLIM row for every lead the dial queue does not carry.

    WHY (2026-09-17, Alejandro): the board's nine funnel lanes cover all 2,297 leads; Call Mode
    carried ~400. TRACE alone is 1,068 leads — 46% of the book — and none of it was reachable from
    the phone, so "what still needs skip-tracing" was a question only the laptop could answer. Same
    for the LETTER and DOOR backlogs past the dial queue's 60-day window.

    These rows exist to be COUNTED, SORTED and HANDED OFF — never dialled. That is enforced by what
    is in them rather than by a flag the renderer has to remember to check:

      * NO PHONE NUMBERS. Not masked, not DNC-filtered — absent. `np` is a COUNT of usable numbers
        so the funnel can tell TRACE (nothing to call) from LETTER (a number exists, the auction is
        just too far out to make the dial queue). A count cannot be tel:-linked.
      * NO EMAIL ADDRESSES, same rule, same reason — `ne` is a count.
      * `oo:1` where the BUILD already holds a suppression verdict (the case is in optouts.json /
        bounced, or the person matched the identity ledger). The page routes those straight to OUT
        without asking the classifier, because a ledger opt-out outranks anything notes can say.

    Everything else is the shape the board's own _funnelStage reads, under the board's own key
    names, so the extracted classifier runs on these rows unmodified. See extract_funnel_js.
    """
    optouts, deads = (optouts or {}), (deads or {})
    dial = set(dial_cases or ())
    out, seen, suppressed = [], set(), 0
    # The identity-ledger check is call_rows' own, reused rather than re-derived: CLAUDE.md reserves
    # everything that PRODUCES a suppression verdict for the desktop session. This reads one.
    _id_opted = _identity_opted_fn(slim, optouts)
    for d in slim:
        case = (d.get('case') or '').strip()
        if not case or case in dial or case in seen:
            continue
        seen.add(case)
        oo = 1 if (case in optouts or case in deads or _id_opted(d)) else 0
        if oo:
            suppressed += 1
        # `np` COUNTS DNC-FLAGGED NUMBERS, deliberately, and this is the one place these rows do
        # not take the safer reading.
        #
        # The board's _funnelStage asks `(r.phones||[]).length > 0` and never looks at phdnc, so a
        # lead whose every number carries a do-not-call flag sits in the board's CALL lane as
        # reachable. Excluding them here read better and made the phone disagree with the board —
        # which is the one thing these rows exist to stop. The nine counts are Alejandro's, read off
        # the board; a lane that says 610 there has to say 610 here or the parity is decorative.
        #
        # ⚠️ THE BOARD-SIDE GAP IS REAL AND IS DELIBERATELY NOT FIXED HERE. CLAUDE.md reserves the
        # suppression surface for the DESKTOP-35NNMFL session and is explicit that a suppression bug
        # found from another session gets REPORTED, not repaired: two uncoordinated fixes on this
        # surface is the documented failure mode and the blast radius is dialing someone who said do
        # not call. Reported to Alejandro 2026-09-17.
        #
        # Nothing becomes dialable because of this. Coverage rows ship no numbers at all, and
        # call_rows still drops every DNC number from the dial queue. This count feeds a LANE LABEL.
        phdnc = d.get('phdnc') or []
        _valid = [i for i, ph in enumerate(d.get('phones') or []) if len(_digits(ph)) == 10]
        np = len(_valid)
        # ...and carry how many of those are do-not-call, so the card can say WHY a lead that has a
        # number on file is not in the dial queue. A count, never the numbers.
        kd = sum(1 for i in _valid if (phdnc[i] if i < len(phdnc) else False))
        days = d.get('days')
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = None
        # BUDGETED TO ~150 BYTES A ROW. This page's founding rule is that it opens on cell data at a
        # door — ~490 rows in ~100 KB against the board's 6.4 MB — and coverage adds ~1,900 rows to
        # it. Every field here is one a caller reads off the list or a skip-tracer needs to work the
        # lead. Keep the full owner/co-owner string when it differs from the greeting name;
        # drop only an exact duplicate. Greeting-name truncation must not erase identity context.
        _on = _greet_name(d)
        _o = (d.get('owners') or d.get('oname') or '').strip()
        _ne = len([e for e in (d.get('emails') or []) if str(e or '').strip()])
        row = {
            'c': case,
            'o': (_o if _o != _on else None),
            'on': _on,
            'a': (d.get('addr') or '').strip(),
            'ag': (d.get('addrGuess') or '').strip() or None,
            'aw': (d.get('addrWhy') or '').strip() or None,
            # BOARD KEY NAMES from here down — _funnelStage reads these verbatim.
            'auction': (d.get('auction') or ''),
            'days': days,
            # 0 ships as None so the null-strip drops the key: "no phones" is the common case and
            # `"np":0` on a thousand rows is payload carrying no information.
            'np': np or None,
            'kd': kd or None,
            'ne': _ne or None,
            'st': d.get('st') or '',
            'e': (lambda v: v if isinstance(v, (int, float)) else None)(d.get('eq')),
            'ct': (str(d.get('county') or 'MIAMI-DADE').strip().upper()[:2] or None),
            'fo': re.sub(r'[^0-9A-Za-z]', '', str(d.get('folio') or '')).upper()[:26] or None,
        }
        if oo:
            row['oo'] = 1
        for k in ('sibclaimed', 'saleBkAct', 'lpDismissed'):
            if d.get(k):
                row[k] = 1
        out.append({k: v for k, v in row.items() if v is not None and v != '' and v != []})
    return out, suppressed


def _identity_opted_fn(slim, optouts):
    """call_rows' identity-ledger test, lifted to a standalone closure so coverage_rows can reuse
    the SAME reader instead of growing a second one. Returns a predicate; a no-op when the ledger
    carries no identity keys."""
    _oo_ident = {str(k) for k in (optouts or {}) if str(k)[:1] in ('@', '#')}
    if not _oo_ident:
        return lambda lead: False
    try:
        from foreclosure_leads import _addr_key as _ak    # circular at module scope; see call_rows
    except Exception:
        return lambda lead: False

    def _test(lead):
        for _e in (lead.get('emails') or []):
            _e = str(_e or '').strip().lower()
            if _e and (('@' + _e) in _oo_ident or ('@' + _ak(_e)) in _oo_ident):
                return True
        for _p in (lead.get('phones') or []):
            _p = re.sub(r'\D', '', str(_p or ''))
            if _p and (('#' + _p) in _oo_ident or ('#' + _ak(_p)) in _oo_ident):
                return True
        return False
    return _test


def build_html(rows, total, enc_payload, built, sig, board_sig, sync_js='', textperson=None,
               seat=None, funnel_js='', text_js=''):
    """The page. Deliberately one file, no framework, no external fetch."""
    # Every placeholder must occur EXACTLY once. str.replace substitutes ALL occurrences — a
    # placeholder token mentioned in a comment gets the full replacement value injected into the
    # middle of that comment (this happened: an 18KB sync block landed inside a /* */ about itself,
    # and the comment's closing */ turned the block's tail into live, unbalanced code).
    # __SIG__ is 2 BY DESIGN: the byte-42 head marker freshCheck range-reads, plus the JS var.
    # Both must receive the same value, so replace-all is correct there — the guard just pins the
    # exact expected count so a third copy (e.g. in a comment) still fails the build.
    for _ph, _want in (('__SYNCJS__', 1), ('__FUNNELJS__', 1), ('__TEXTTPLJS__', 1),
                       ('__SCRIPT__', 1), ('__OUTCOMES__', 1), ('__PAYLOAD__', 1),
                       ('__BUILT__', 1), ('__SIG__', 2), ('__BSIG__', 1), ('__SHOWN__', 1),
                       ('__BOOKURL__', 1),
                       ('__TOTAL__', 1), ('__VMEN__', 1), ('__VMES__', 1), ('__TEXTPERSON__', 1),
                       ('__BSIGNER__', 1), ('__SEAT__', 1)):
        _n_ph = _PAGE.count(_ph)
        if _n_ph != _want:
            raise CallModeError('call_mode: placeholder %s occurs %d times in _PAGE (expected %d — '
                                'str.replace hits every copy, including ones in comments)'
                                % (_ph, _n_ph, _want))
    oc = json.dumps([{'k': k, 't': t, 'h': h, 's': s} for k, t, h, s in CALL_OUTCOMES])
    script = {
        'op': {'en': PHONE_OPENER_EN, 'es': PHONE_OPENER_ES,
               'aen': PHONE_OPENER_ANON_EN, 'aes': PHONE_OPENER_ANON_ES,
               'wen': PHONE_OPENER_WARM_EN, 'wes': PHONE_OPENER_WARM_ES},
        'q': [{'k': _k, 'w': _w, 'en': _q_en, 'es': _q_es}
              for (_k, _w), _q_en, _q_es in zip(NEPQ_K, NEPQ_Q_EN, NEPQ_Q_ES)],
        'cioc': [{'k': k, 'w': w, 's': sx} for k, w, sx in CIOC],
        'f15': FIFTEEN_SEC,
        'mars': MARS_BLOCK,
        'never': NEVER_SAY,
        # NEPQ Black Book additions (2026-09-13). Homeowner lane only — the balloon dict has none of
        # these, so renderSheet's `if(SCRIPT.frame)` guards leave the investor call exactly as it was.
        'frame': {'en': STATUS_FRAME_EN, 'es': STATUS_FRAME_ES},
        'probe': {'en': PROBE_EN, 'es': PROBE_ES},
        'bridge': {'en': BRIDGE_EN, 'es': BRIDGE_ES},
        'busy': {'en': BUSY_EN, 'es': BUSY_ES},
        'vmtone': {'en': VM_TONE_EN, 'es': VM_TONE_ES},   # delivery cue under the voicemail
        'tone': [{'k': k, 'v': v} for k, v in TONALITY],  # collapsed tonality reference at top of sheet
        'flow': FLOW,                                      # guided call flow (renderGuided) — homeowner only
        # load_objections() + the hard-coded 'Not interested' card, so a vault-less/stale-vault runner
        # can never drop it (see objection_cards / NOT_INTERESTED_CARD).
        'obj': objection_cards(),
        # BALLOON / REFI LANE — the investor script (vault: Refi Lane note, 2026-08-30). Read by
        # renderSheet for st:'BAL' rows only; same keys as the homeowner script so the page has ONE
        # renderer. `rec` copied from the main script: all-party consent applies to an investor too.
        'bal': dict(BALLOON_SCRIPT, rec=(_QREC_LINE if _quo_recording() else None)),
        # Rendered in red under the opener ONLY when sender.json carries "quo_record": true.
        # Florida is ALL-PARTY consent (FS 934.03, a felony statute) -- if Quo auto-records, the
        # consent ask is not optional and it has to be ON the screen he reads from, not in a doc.
        # quo_sync's coach pass then verifies the word "record" actually occurs in the transcript.
        'rec': (_QREC_LINE if _quo_recording() else None),
    }
    return _PAGE.replace('__SYNCJS__', sync_js) \
                .replace('__FUNNELJS__', funnel_js) \
                .replace('__TEXTTPLJS__', text_js) \
                .replace('__SCRIPT__', json.dumps(script, ensure_ascii=False)) \
                .replace('__OUTCOMES__', oc)                 .replace('__BOOKURL__', BOOKING_URL) \
                .replace('__PAYLOAD__', json.dumps(enc_payload)) \
                .replace('__BUILT__', built) \
                .replace('__SIG__', sig) \
                .replace('__BSIG__', board_sig) \
                .replace('__SHOWN__', str(len(rows))) \
                .replace('__TOTAL__', str(total)) \
                .replace('__VMEN__', json.dumps(VOICEMAIL_EN)) \
                .replace('__VMES__', json.dumps(VOICEMAIL_ES)) \
                .replace('__BSIGNER__', json.dumps(_balloon_signer())) \
                .replace('__SEAT__', json.dumps({'n': seat[0], 'i': seat[1], 'w': str(seat[2] or '')[:18]}
                                                if seat else None)) \
                .replace('__TEXTPERSON__', json.dumps(_tp_slim(textperson)))


# A REAL person hash: 'P' + 10 hex chars (foreclosure_leads._person_keys). Everything else that can
# appear as a pkey is the singleton fallback 'C' + <case number>.
_PKEY_HASH_RE = re.compile(r'^P[0-9a-f]{10}$')


def _tp_slim(tp):
    """PERSON-key -> send count, and nothing else.

    The board's ledger row carries {n, opens, last}; the phone only ever asks one question — has this
    human already had their three messages? Shipping `opens` and `last` would add bytes and answer
    nothing.

    🔒 HASHED KEYS ONLY. This table is baked as a PLAIN JS var, OUTSIDE the encrypted payload, and
    the repo is public. The docstring here used to claim "keys are already hashed pkeys" — untrue:
    singletons fall back to 'C' + <case number>, so the first ledger row for one of them would have
    published a real foreclosure case number bound to "texted N times" in cleartext. Outreach
    intelligence, on a public page. (No live leak occurred — the ledger is empty today — this closes
    the hole before the first row exists.)

    What the filter costs, stated honestly: a SINGLETON's ledger count no longer reaches a fresh
    device through this table. Bounded loss — any person spanning >1 case always carries a P-hash
    (that is what _person_keys assigns hashes FOR), so the dangerous case — six texts to one human
    across two properties — is fully covered. Singleton counts still travel in the touches that ride
    the encrypted team sync, which actually merges now.

    Ships ONLY people who have actually been texted. An absent key means zero, which is the same
    answer a zero would give at a fraction of the size.
    """
    out = {}
    for k, v in (tp or {}).items():
        n = (v or {}).get('n') or 0
        if n and _PKEY_HASH_RE.match(str(k)):
            out[k] = n
    return out


def phone_index(slim):
    """EVERY lead that has a phone -> the minimum needed to answer "who just texted me?".

    WHY THIS IS NOT JUST THE DIALABLE 400 (2026-08-19): a lead texted 305-801-1800 and there was no
    way to identify her. Measured on the live payload, only 1,202 of the 3,638 numbers we hold sit on
    the call page — so 67% of possible texters were unresolvable no matter how long he scrolled. The
    morning worker emails and texts far more people than the 400 it will dial, and any of them can
    reply. She turned out to be a lead we had emailed three times.

    Shape is deliberately two tables, not a fat map: several numbers share one lead, so
      t: [[owner, street, case, days, equity, folio, countyCode], ...]   (each lead once)
      d: {"3058011800": <index into t>, ...}
    Naive one-record-per-number was 253KB; this is materially smaller and the page has to open on a
    phone at a door. Rides INSIDE the encrypted payload — these are phone numbers and the repo is
    public.
    """
    def _num(v):                       # _n() is a closure inside call_rows, not module scope
        try:
            return float(str(v).replace(',', '').replace('$', ''))
        except Exception:
            return 0.0
    seen, table, digits = {}, [], {}
    for d in slim:
        case = (d.get('case') or '').strip()
        # 🔴 DNC NUMBERS MUST NOT BE SERIALIZED. This module's own docstring states the invariant:
        # "DNC numbers are never serialized... A number that is not in the payload cannot be
        # rendered, cannot be tel:-linked." call_rows() honours it; this index did not — it walked
        # `phones` with no `phdnc` read at all, so all 1,142 DNC-flagged numbers shipped, and
        # screenLookup paints "Call back" and "Text" anchors on whatever it resolves. Withholding
        # on the dial list while tel:-linking the same number on the lookup screen is not a
        # partial control, it is no control. Same zip-and-filter discipline as call_rows: pair
        # each number with its flag BEFORE filtering, so a short/missing phdnc can never shift the
        # flags onto the wrong numbers. Identification still works — a DNC lead keeps its row in
        # `t` via any non-DNC number; only the DNC digits are absent from `d`.
        _ph_raw = list(d.get('phones') or [])
        _dnc_raw = list(d.get('phdnc') or [])
        phones = []
        for _i, _p in enumerate(_ph_raw):
            if _i < len(_dnc_raw) and _dnc_raw[_i]:
                continue
            _pd = re.sub(r'\D', '', str(_p))[-10:]
            if len(_pd) == 10:
                phones.append(_pd)
        if not case or not phones:
            continue
        if case not in seen:
            val = _num(d.get('value'))
            judg = _num(d.get('judg'))
            seen[case] = len(table)
            table.append([
                (d.get('oname') or d.get('owners') or '')[:30],
                (d.get('addr') or '').split(',')[0][:32],
                case,
                d.get('days') if isinstance(d.get('days'), (int, float)) else None,
                int(val - judg) if (val and judg) else None,
                re.sub(r'[^0-9A-Za-z]', '', str(d.get('folio') or '')).upper()[:26] or None,
                (str(d.get('county') or 'MIAMI-DADE').strip().upper()[:2]),
            ])
        for p in phones:
            digits.setdefault(p, seen[case])       # first lead wins; a number is one person
    return {'t': table, 'd': digits}


# ---- BALLOON / REFI LANE SCRIPT ------------------------------------------------------------------
# Source: vault "Refi Lane — investor email + text (hard money to DSCR)", 2026-08-30, Jesse's 8/29
# structure. B2B, unregulated pitch: an investor paying 12% is making a business decision about a
# rate. Fifteen minutes on purpose, not five — "an investor discussing a $400k refinance expects a
# real conversation; five minutes reads as unserious." Tokens: {first} {sender} {st1} {phone} and
# {gap} (fillScript — THEIR monthly 12%-vs-7% gap off the recorded note, never the example figure).
BALLOON_SCRIPT = {
    'op': {
        'en': "Hi{first}, this is {sender} with Biscayne Solutions Group. I'm calling about the hard-money note on {st1}. Quick question: are you planning to extend it with the same lender, or refinance it?",
        'es': "Hola{first}, le habla {sender} de Biscayne Solutions Group. Le llamo por el préstamo privado sobre {st1}. Pregunta rápida: ¿piensa extenderlo con el mismo prestamista, o refinanciarlo?",
        'aen': "Hi, this is {sender} with Biscayne Solutions Group. I'm trying to reach whoever handles the financing on {st1}. Who would that be?",
        'aes': "Hola, le habla {sender} de Biscayne Solutions Group. Busco a la persona que maneja el financiamiento de {st1}. ¿Quién sería?",
        'wen': "Hi{first}, {sender} again from Biscayne Solutions Group. Did you get a minute to look at the note on {st1}?",
        'wes': "Hola{first}, {sender} otra vez de Biscayne Solutions Group. ¿Tuvo un momento para ver lo del préstamo sobre {st1}?",
    },
    'q': [
        {'k': 'THE RATE', 'w': 'Get the number out of their mouth. Twelve is the usual answer.',
         'en': "What are you paying on it right now, roughly?", 'es': "¿Más o menos qué tasa está pagando ahora?"},
        {'k': 'THE WALL', 'w': 'The maturity is the clock. Our 24-month date is a proxy; theirs is real.',
         'en': "And when does it come due?", 'es': "¿Y cuándo vence?"},
        {'k': 'THE PLAN', 'w': 'Extension at the same rate is the default. Name it so they hear it.',
         'en': "Were you just going to extend with them at the same rate?", 'es': "¿Iba a extenderlo con ellos a la misma tasa?"},
        {'k': 'THE MATH', 'w': 'Say THEIR number, from the card. Not the $400k example.',
         'en': "On that balance the gap between twelve and seven is about {gap} a month. That's what the extension costs you.",
         'es': "Con ese saldo, la diferencia entre doce y siete es como {gap} al mes. Eso es lo que le cuesta la extensión."},
        {'k': 'THE ASK', 'w': 'Fifteen minutes. Not five. An investor at $400k expects a real conversation.',
         'en': "Give me fifteen minutes to price it. If I can't beat what you've got, I'll tell you in the first five and you've lost nothing.",
         'es': "Deme quince minutos para cotizarlo. Si no puedo mejorar lo que tiene, se lo digo en los primeros cinco y no perdió nada."},
    ],
    'cioc': [
        ('DSCR', 'The product. Rent qualifies the loan, not their W-2.',
         "DSCR loans, six to eight percent, seventy to eighty LTV. No tax returns, no personal income docs. The property's rent qualifies it. About two weeks to close."),
        ('HARD EQUITY', 'When speed beats price.',
         "If you need it faster than that, hard equity at sixty to seventy LTV funds in five to ten business days. Three to five points, ten to twelve percent, but it closes."),
        ('WORST CASE', 'The close. Take the risk off the table.',
         "Worst case I confirm you already have the best deal available, and you extend with confidence. That's fair, right?"),
    ],
    'f15': "{sender}, Biscayne Solutions Group. You're paying twelve on {st1}; I can likely get you seven. Fifteen minutes to price it, no commitment. {phone}.",
    'mars': "B2B, unregulated pitch: a business talking to a business about a rate. No rescue framing, no homeowner disclaimers — but every number you quote is a program RANGE from the sheet, never a promise on THEIR file until it is priced.",
    'never': [
        'Never quote a rate as a promise. "Six to eight" is a program range; their number comes after pricing.',
        'Never state the maturity date as fact. Ours is origin + 24 months. Ask theirs.',
        'Never pitch the LLC. Pitch the human on the card; if that is not the decision-maker, get the name.',
        'Never run the foreclosure script. There is no sale, no plaintiff, no equity rescue here.',
    ],
    'obj': [
        {'t': "I'll just extend", 'w': 'The default. Price the cost of the default.',
         's': "Sure, and most people do. It costs about {gap} a month more than it has to. Let me at least put a number next to it before you sign the extension."},
        {'t': 'Rate is fine', 'w': 'Nobody thinks 12 is fine. They think the hassle is not worth it.',
         's': "Fair. Then this is a fifteen-minute call that ends with you knowing you have the best deal. That's worth fifteen minutes."},
        {'t': 'Send me something', 'w': 'Brush-off. Trade it for a time.',
         's': "Happy to. It'll be more useful once I know your balance and maturity. What's a good time tomorrow for ten minutes?"},
        {'t': 'Who are you', 'w': 'Identity, straight.',
         's': "{sender}, Biscayne Solutions Group, Miami. We place DSCR and hard-equity loans on investment property. Your note on {st1} is public record; that's how I found you."},
    ],
    # Voicemail for the investor lane. The homeowner one ("maps your free backup... a day is
    # everything") is a rescue message and reads absurd left for an LLC manager about a rate.
    'vm': {
        'en': "Hi{first}, this is {sender} with Biscayne Solutions Group, about the hard-money note on {st1}. If you're paying twelve on it, I can likely get you seven. Fifteen minutes to price it, no commitment, and if I can't beat it I'll say so. {phone}. Thanks.",
        'es': "Hola{first}, le habla {sender} de Biscayne Solutions Group, por el préstamo privado sobre {st1}. Si está pagando doce, probablemente le consigo siete. Quince minutos para cotizarlo, sin compromiso, y si no lo mejoro se lo digo. {phone}. Gracias.",
    },
    'rec': None,
}


# WHO IS ON THE PHONES, and therefore how the list is cut. (n, i, label) per seat; the label is
# also the URL segment, so seat 0 = docs/call/ and 'Carlos' = docs/call/carlos/.
# SEAT 0 MUST STAY FIRST AND MUST STAY subdir '' — that is the URL already bookmarked on
# Alejandro's phone and linked from the board's Call Mode button.
# To go back to one undivided list, set this to [None] — seat=None builds the whole list.
# Every n here must match, and every i must be distinct, or leads land on two phones or on none.
CALL_SEATS = [(2, 0, 'Alejandro'), (2, 1, 'Carlos')]


def dedupe_calendar_rows(rows, key='c', days='d', date='x'):
    """One row per case. Returns (rows, collapsed, sooner).

    THE DATA: the county calendars list a single case on MORE THAN ONE auction date, each as its
    own calendar line with its own AITEM/AID. Measured on the 2026-09-06 twin: 5 such cases. Where
    both copies are enriched enough to compare, the FOLIO matches — so this is one parcel
    calendared twice (a reset/rescheduled sale), not a case foreclosing on two properties. It is
    visible in the raw Miami-Dade scrape too: 2024-017395-CA-01 as AID 1512352 (09/08) and AID
    1512353 (09/28).

    WHY IT MATTERS ON EVERY SURFACE, not just the phone:
      * Call Mode served the same human twice, on two cards showing two different sale dates.
      * The board's worker lanes put ONE case in TWO lanes at once — _lanetest's mutual-exclusivity
        check fails on CACE-25-012839 (urgent via 09/17, active via 10/06) — so the Morning Worker
        can email the same owner from two lanes, and the FTSA/TCPA touch ladder counts per HUMAN.

    KEEP THE FULLEST ROW. The second posting is usually the county's newer one and is not enriched
    yet: no address, no folio. Keeping it would put a card with no property on the phone and a
    blank row on the board. Completeness is the count of populated fields.

    NEVER SILENTLY DROP A SOONER SALE. Completeness and earliest-date agree on all five real cases,
    but they need not in general, and telling a homeowner the sale is 39 days out when it is 17 is
    the one error on these calls that cannot be walked back. When a dropped copy is calendared
    EARLIER than the one kept, the kept row carries that date in `dupd` (and `dupn` counts what was
    collapsed) so the surface can say the sale may be sooner and to verify it with the clerk.
    """
    def _full(r):
        return sum(1 for v in r.values() if v not in (None, '', [], {}))

    def _d(r):
        v = r.get(days)
        return v if isinstance(v, (int, float)) else 9999

    groups, order = {}, []
    for r in rows:
        k = r.get(key)
        if not k:
            order.append([r])                      # caseless: never merged into anything
            continue
        if k not in groups:
            groups[k] = []
            order.append(groups[k])
        groups[k].append(r)
    out, collapsed, sooner = [], 0, 0
    for grp in order:
        if len(grp) == 1:
            out.append(grp[0])
            continue
        best = max(grp, key=lambda r: (_full(r), -_d(r)))
        others = [r for r in grp if r is not best]
        collapsed += len(others)
        best['dupn'] = len(others)
        early = [r for r in others if _d(r) < _d(best)]
        if early:
            best['dupd'] = min(early, key=_d).get(date) or ''
            sooner += 1
        out.append(best)
    return out, collapsed, sooner


def seat_rows(rows, n, i):
    """BUILD-TIME seat partition (2026-09-09). Rows whose stable bucket lands on seat i of n.

    Before this the split lived on the phone (fcSeat, three prompt() boxes) and depended on both
    callers typing matching numbers — nothing stopped two phones both choosing seat 1, and "show
    all" silently reverted on reload. Splitting HERE means each page's encrypted payload holds only
    its half: there is no setting to get wrong and no escape hatch to leak the other half.
    An unstamped row (no 'sb') goes to seat 0 so a lead is never absent from EVERY page — the same
    rule the page's _seatMine keeps. call_rows skips caseless rows, so in practice this is a guard.
    """
    return [r for r in rows if (r.get('sb', 0) % n) == i]


def make_callmode(slim, codes, encrypt, built, board_sig, optouts=None, deads=None, guard=None,
                  textperson=None, seat=None, subdir='', rows=None):
    """Write docs/call/<subdir>/index.html. `encrypt` is foreclosure_leads._encrypt_multi and
    `guard` is its _js_guard — both INJECTED rather than re-implemented, so the crypto and the
    parse check can never drift from the board's.

    seat   : None (whole list, legacy) or (n, i, label) — e.g. (2, 1, 'Carlos') bakes seat 2 of 2.
    subdir : '' -> docs/call/, 'carlos' -> docs/call/carlos/.
    rows   : optional (rows, total) from ONE shared call_rows() call, so two seat pages and the
             call sheet are cut from the identical list and can never disagree on a gate.

    The guard runs BEFORE the write. A page whose script fails to parse still loads — it just loads
    with every button dead, which is the precise fail-silent shape this whole effort exists to
    remove. Better to ship no Call Mode than a Call Mode that looks fine and logs nothing.
    """
    subdir = re.sub(r'[^a-z0-9-]', '', str(subdir or '').lower())
    outdir = os.path.join(HERE, 'docs', 'call', subdir) if subdir else os.path.join(HERE, 'docs', 'call')
    os.makedirs(outdir, exist_ok=True)
    dest = os.path.join(outdir, 'index.html')
    _where = 'docs/call/' + (subdir + '/' if subdir else '')
    if not codes:
        # No codes means no encryption. Call Mode has no meaningful degraded form: it exists to put
        # dialable numbers on a phone. Ship a stub rather than plaintext PII, and do NOT leave last
        # week's encrypted copy live against a codeless build.
        open(dest, 'w', encoding='utf-8').write(
            '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
            'initial-scale=1"><title>Call Mode</title><body style="font:16px system-ui;padding:24px">'
            '<h2>Call Mode needs site.codes</h2><p>The build ran without access codes, so this page '
            'cannot be encrypted and will not ship phone numbers. Rebuild with site.codes present.</p>')
        # SAY IT OUT LOUD. Replacing a working encrypted page with a stub is deliberate (see above),
        # but returning 0 made the caller's `if _cm_rows:` false, so the build log printed NOTHING —
        # the phone page silently became a dead-end and the only signal was discovering it mid-call.
        print('call mode: NO site.codes — %s replaced with a stub. The phone page is DOWN '
              'until a build runs with access codes present.' % _where)
        return 0, 0
    if rows is None:
        rows, total = call_rows(slim, optouts, deads)
    else:
        rows, total = rows
    if seat:
        _sn, _si, _sw = seat
        if not (_sn > 1 and 0 <= _si < _sn):
            raise CallModeError('call_mode: bad seat %r (want n>1, 0<=i<n)' % (seat,))
        rows = seat_rows(rows, _sn, _si)
    # ---- COVERAGE IS CUT AGAINST *THIS PAGE'S* DIAL LIST (2026-09-21) -------------------------
    # This was cut against the CREW-WIDE list, one line ABOVE the seat split, on the argument that
    # the nine board lane counts describe the business and halving them per phone would make two
    # callers read two different books. The argument is right; the code did the opposite of it.
    #
    # A seat page shipped `rows` (its own half) plus coverage for everything outside the CREW dial
    # list — so the OTHER seat's rows were in NEITHER list and were absent from the page entirely.
    # Every board lane on Alejandro's handset read short by the size of Carlos's queue, and the
    # leads that went missing were the most callable in the book: they had passed every gate and
    # made the cap, which is exactly why coverage skipped them. The guard below did not catch it
    # because it checked the CREW union rather than what this page actually ships.
    #
    # Scaling the cap with the crew (1863e22) doubled the hole rather than closing it: the crew
    # window went 400 -> 800, so ~400 leads now fall off each page instead of ~200.
    #
    # Cut against this page's own list and each page carries the WHOLE book: its own rows dialable,
    # every other lead as a countable, un-dialable coverage row. Both phones then total the same
    # 2,394 — which is what the crew-wide cut was trying to achieve and did not.
    _dial_all = list(rows)
    # total stays the CREW-WIDE qualifying count on purpose: "N qualifying" describes the funnel,
    # not this phone. SHOWN (len(rows)) is what this seat actually carries.
    # phone_index stays FULL on both seats: "Who texted me?" must resolve a number from either half.
    #
    # COVERAGE ROWS ride the SAME payload. They are the rest of the book — every lead THIS page's
    # dial queue does not carry (no traced number, auction past the 60-day window, over the cap, or
    # on the other caller's phone) as a countable, sortable, un-dialable row, so the nine board lane
    # counts on a handset describe the whole business. See coverage_rows for what is deliberately
    # absent from them, and the cut note above for why it is this page's list and not the crew's.
    _cov, _cov_sup = coverage_rows(slim, [r.get('c') for r in _dial_all], optouts, deads)
    # EVERY LEAD, OR SAY WHICH ONES ARE MISSING. The whole promise of the board lanes on the phone
    # is that they count the same book the board counts; a lead that falls out of BOTH the dial
    # queue and the coverage set makes a lane read low, and a lane reading low is indistinguishable
    # from a lane that is genuinely short. That is the silent-cap failure this file already carries
    # three comments about, so it is a build failure here rather than a surprise on a handset.
    _all_cases = {(d.get('case') or '').strip() for d in slim if (d.get('case') or '').strip()}
    _shipped = {r.get('c') for r in _dial_all} | {r.get('c') for r in _cov}
    _lost = sorted(_all_cases - _shipped)
    if _lost:
        raise CallModeError(
            'call_mode: %d lead(s) are in neither the dial queue nor the coverage set, so the board '
            'lanes on the phone would under-count the book: %s%s\n'
            '  A new gate in call_rows drops leads; coverage_rows has to pick them up.'
            % (len(_lost), ', '.join(_lost[:8]), ' ...' if len(_lost) > 8 else ''))
    _plain = json.dumps({'r': rows, 'b': _cov, 'x': phone_index(slim)})
    # SAY THE PAYLOAD SIZE OUT LOUD, every build. This page exists to open on cell data at a door
    # (~100 KB was the founding budget) and coverage roughly doubles it. A number in the log is what
    # makes the next person's addition to these rows a decision rather than an accident.
    print('call mode: %d dialable row(s) + %d coverage row(s) = %d leads on the phone, %.0f KB '
          'before encryption (board lanes now count the whole book, not just the dial queue)%s'
          % (len(rows), len(_cov), len(rows) + len(_cov), len(_plain) / 1024.0,
             ('; %d coverage row(s) carry a ledger opt-out and ship with no number at all'
              % _cov_sup) if _cov_sup else ''))
    payload = encrypt(_plain, codes)
    import hashlib
    sig = hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()[:12]
    _assert_page_provides(_PAGE)
    # Read the board template ONCE and hand the same text to both consumers. The outcome-vocabulary
    # guard runs BEFORE the extraction so a divergence is reported as a divergence, rather than
    # surfacing later as a confusing failure somewhere downstream of it.
    _tracker_src = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
    _assert_outcomes_match_board(_tracker_src)
    sync_js = extract_sync_js(_tracker_src)
    funnel_js = extract_funnel_js(_tracker_src)
    text_js = extract_text_js(_tracker_src)
    _assert_no_dead_overrides(_PAGE, sync_js)
    _assert_no_dead_overrides(_PAGE, funnel_js)
    _assert_no_dead_overrides(_PAGE, text_js)
    html = build_html(rows, total, payload, built, sig, board_sig, sync_js, textperson,
                      seat=seat, funnel_js=funnel_js, text_js=text_js)
    if guard:
        guard(html)          # raises on a parse error; the caller's try/except keeps the board safe
    # Assert the promise the page makes about itself: no dialable number outside the ciphertext.
    #
    # 🪤 The strip used to be re.sub(r'"(?:ct|k)":"[^"]+"', ...) — which matched NOTHING, because
    # json.dumps writes `"ct": "..."` WITH A SPACE after the colon. So the check scanned ~250 KB of
    # base64 ciphertext looking for phone numbers. It never produced a false alarm (10 consecutive
    # digits are rare in base64) and so nobody noticed it was not doing its job — and a run that did
    # hit 10 digits would have failed the build with a "plaintext leak" that was nothing of the kind.
    # Excise the payload EXACTLY instead of pattern-matching it: we know the precise string that was
    # inserted, so remove that, and verify the removal actually happened.
    _pay = json.dumps(payload)
    _body = html.replace(_pay, '')
    if len(_body) == len(html):
        raise CallModeError('call_mode: could not excise the encrypted payload before the PII scan — '
                            'the serialization changed, so this check would be scanning ciphertext '
                            'instead of the page. Fix the excision before trusting the result.')
    _leak = re.findall(r'(?<!\d)\d{10}(?!\d)', _body)
    if _leak:
        raise CallModeError('call_mode: %d plaintext 10-digit number(s) outside the encrypted payload'
                         % len(_leak))
    open(dest, 'w', encoding='utf-8').write(html)
    return len(rows), total


_PAGE = r"""<!doctype html><html lang="en"><head>
<!--SIG="__SIG__" — build signature, FIRST so the stale-cache check can find it in one small range
    request. It also lives in the script below; this copy exists because that one sits at byte
    ~252,000, after the encrypted payload on the same line, while freshCheck asks for bytes 0-1200.
    The check therefore matched nothing and silently never fired: a phone serving last week's list
    looked identical to one serving today's. Keep this marker above the payload, always. -->
<meta charset="utf-8">
<!-- THE viewport meta. Its absence is the single reason the morning worker is unusable on a phone. -->
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<title>Call Mode</title>
<style>
:root{--bg:#0b1730;--card:#132244;--ink:#f4f7ff;--mut:#94a7c8;--gold:#c6a14b;--ok:#2e7d32;--bad:#b3261e}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,system-ui,sans-serif;
     padding:0 0 env(safe-area-inset-bottom)}
.wrap{max-width:560px;margin:0 auto;padding:14px 14px 40px}
/* COLUMN, not a row (2026-09-09). .top holds the lane buttons AND the status lines under them
   (hidden count, seat chip, build stamp, session strip) — every one of which is written as a
   full-width centered .supn line. As a flex ROW with space-between that only looked right while
   there were two or three lane buttons: at eight the buttons ate the width and squeezed "Carlos —
   seat 2 of 2" into a 60px column of single words. */
.top{display:flex;flex-direction:column;align-items:stretch;gap:8px;margin-bottom:10px}
.lane{display:flex;flex-wrap:wrap;gap:6px}
/* flex-basis 0 + wrap = equal-width buttons that reflow onto as many rows as the labels need,
   never a horizontal scroll and never a 3-line-tall button. */
.lane button{flex:1 1 82px;min-width:82px;min-height:44px;border-radius:10px;border:1px solid #2a3f6b;
     background:#0f1d3a;padding:4px 6px;line-height:1.2;
     color:var(--mut);font-size:14px;font-weight:600}
.lane button.on{background:var(--gold);color:#0b1730;border-color:var(--gold)}
/* A lane with rows but nothing callable right now — every one suppressed. Shown, not hidden:
   hiding it would be the silent cap this page refuses to ship. Dimmed so it never competes with
   a lane that has work in it. */
.lane button.dim{opacity:.45}
.card{background:var(--card);border:1px solid #2a3f6b;border-radius:14px;padding:16px;margin-top:12px}
.addr{font-size:22px;font-weight:700;line-height:1.2}
.own{font-size:15px;color:var(--mut);margin-top:2px}
.when{margin-top:8px;font-size:15px;color:var(--gold);font-weight:600}
.facts{margin-top:10px;font-size:13px;color:var(--mut)}
a.dial,button.big{display:block;width:100%;min-height:58px;margin-top:12px;border-radius:12px;
     border:0;font-size:19px;font-weight:700;text-align:center;line-height:58px;text-decoration:none;
     touch-action:manipulation}
a.dial{background:var(--ok);color:#fff}
/* Redial: same tel: mechanics as the main dial button, styled as the secondary action it is —
   outlined, not filled, so the outcome buttons stay the visual priority on the screen. */
a.redial{display:block;width:100%;min-height:50px;line-height:50px;margin-top:10px;border-radius:12px;
     border:1px solid var(--ok);color:var(--ok);background:transparent;font-size:16px;font-weight:700;
     text-align:center;text-decoration:none;touch-action:manipulation}
button.big{background:#1d4ed8;color:#fff}
.oc button{display:block;width:100%;min-height:56px;margin-top:9px;border-radius:12px;border:1px solid #2a3f6b;
     background:#0f1d3a;color:var(--ink);font-size:17px;font-weight:600;touch-action:manipulation}
.oc button.warn{border-color:#7a4a12;color:#f0b357}
.oc button.dnc{border-color:var(--bad);color:#ff8a80}
/* After-call panel. Every control here is >=48px because it is tapped one-handed, often walking. */
.afterlab{font-size:11px;font-weight:800;letter-spacing:.08em;color:var(--gold);margin:16px 0 7px}
#tx,#txy,#txn,#nx,a#bk{display:block;width:100%;min-height:52px;border-radius:12px;border:1px solid #2a3f6b;
     background:#1d4ed8;color:#fff;font-size:17px;font-weight:700;margin-top:8px;touch-action:manipulation}
/* The book-it link is an <a>, not a <button>, because it opens Cal.com in a new tab while the call
   page keeps its state -- he is mid-call and losing the screen would lose the lead. Anchors do not
   inherit the button rules above, so it is named here and given the line-height/centering a button
   gets for free. GOLD, not blue: on APPOINTMENT SET this is the only thing on screen that matters. */
a#bk{background:#A8720C;border-color:#c69a3a;text-decoration:none;text-align:center;
     line-height:52px;padding:0 10px}
#tx.ghost,#txn.ghost{background:#0f1d3a;color:var(--ink)}
#nx{background:#0f1d3a;color:var(--ink)}
.cbrow{display:flex;gap:8px}
.cb{flex:1;min-height:50px;border-radius:12px;border:1px solid #2a3f6b;background:#0f1d3a;
     color:var(--ink);font-size:14px;font-weight:600;touch-action:manipulation}
.cb.on{border-color:var(--ok);color:var(--ok)}
.cb:disabled{opacity:.45}
/* WHO TEXTED ME — always reachable, thumb-sized, out of the way of the call buttons */
.lkfab{position:fixed;right:10px;bottom:12px;z-index:52;min-height:44px;padding:11px 15px;
  border-radius:999px;border:1px solid #2a3f6b;background:#12213f;color:var(--ink);
  font:700 13px "Segoe UI",Arial,sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.45);
  touch-action:manipulation;-webkit-tap-highlight-color:rgba(198,161,75,.25)}
.lkfab:active{background:#1b2c50}
/* THE FILE — the reference block on the outcome screen, up for the whole call */
.refbox{margin-top:12px;padding:11px 13px;border-radius:11px;background:#0f1d3a;border:1px solid #2a3f6b}
.reft{width:100%;border-collapse:collapse;font-size:13px;line-height:1.5}
.reft td{padding:2.5px 0;vertical-align:top;-webkit-user-select:text;user-select:text}
.reft td.rk{color:var(--mut);font:700 10.5px "Segoe UI",Arial,sans-serif;letter-spacing:.06em;
  text-transform:uppercase;white-space:nowrap;padding-right:10px;width:96px}
/* HIS FILE — research links, thumb-sized so they are usable one-handed on a call */
.flinks{display:flex;flex-wrap:wrap;gap:8px;margin-top:2px}
.flinks a{display:inline-flex;align-items:center;min-height:44px;padding:10px 14px;border-radius:10px;
  background:#12213f;border:1px solid #2a3f6b;color:var(--ink);font-size:13.5px;font-weight:600;
  text-decoration:none;touch-action:manipulation;-webkit-tap-highlight-color:rgba(198,161,75,.25)}
.flinks a:active{background:#1b2c50}
.fcase{margin-top:8px;color:var(--mut);font-size:11.5px;-webkit-user-select:text;user-select:text}
/* THE CALL LOG (registry) */
.regsum{font-size:15px;color:var(--ink)}
.regsum b{color:var(--gold);font-size:19px}
.reglist{margin-top:12px;max-height:62vh;overflow:auto}
.regrow{padding:11px 2px;border-bottom:1px solid #1c2b4d}
.regtop{display:flex;justify-content:space-between;gap:10px;align-items:baseline;font-size:15px}
.regago{color:var(--mut);font-size:12.5px;white-space:nowrap}
.regsub{color:var(--mut);font-size:12.5px;line-height:1.5;margin-top:2px}
.regby{display:inline-block;margin-left:8px;padding:1px 8px;border-radius:999px;
  background:#1c2b4d;color:var(--ink);font:700 10.5px "Segoe UI",Arial,sans-serif}
.txconf{font-size:14px;color:var(--gold);font-weight:600;margin-top:10px}
/* THE OUTPUT — the exact text that went to the composer, shown back so he can see what was sent
   (and read it aloud on the follow-up call). Selectable so he can copy it into another app. */
.txbody{margin-top:8px;padding:11px 13px;border-radius:10px;background:#0f1d3a;border:1px solid #2a3f6b;
  color:var(--ink);font-size:13.5px;line-height:1.55;white-space:pre-wrap;word-break:break-word;
  -webkit-user-select:text;user-select:text;max-height:220px;overflow:auto}
.txbody .lbl{display:block;font:800 10px "Segoe UI",Arial,sans-serif;letter-spacing:.1em;
  text-transform:uppercase;color:var(--mut);margin-bottom:5px}
#txr{background:#3d2c08;border-color:#A8720C;color:#F6E9C8}
.supn{font-size:11px;color:var(--mut);text-align:center;padding:5px 8px 0}
/* SESSION STRIP — today's work, on every screen. The counter moving on each logged outcome is the
   visible proof the logging is happening ("I want to see if it's logging what I am doing"). */
.sess{font-size:12.5px;color:var(--mut);background:#131f38;border:1px solid #2a3f6b;border-radius:9px;
  padding:7px 10px;margin:8px 0;text-align:center}
.sess b{color:#F4E5A7}
/* Situational-text chips on the after-call panel */
.txch{display:inline-block;margin:3px 5px 3px 0;padding:8px 12px;border:1px solid #2a3f6b;
  border-radius:16px;background:#0e1626;color:var(--ink);font-size:12.5px;min-height:34px}
.txch.on{border-color:var(--gold);color:var(--gold);font-weight:700}
/* language chips: small but still thumb-safe; .on = the active language */
.lchips{display:inline-flex;gap:4px;margin-left:8px;vertical-align:middle}
.lchip{min-width:44px;min-height:30px;padding:2px 8px;border-radius:8px;border:1px solid #2a3f6b;
     background:#0f1d3a;color:var(--mut);font-size:12px;font-weight:800;touch-action:manipulation}
.lchip.on{border-color:var(--gold);color:var(--gold)}
.errchip{color:#ff8a80;font-weight:700;cursor:pointer;text-decoration:underline}
.sub{font-size:12px;color:var(--mut);margin-top:6px;text-align:center}
.vm{background:#0f1d3a;border:1px solid #2a3f6b;border-radius:10px;padding:12px;margin-top:10px;font-size:17px;line-height:1.5}
.vmlang{font-size:11px;font-weight:800;letter-spacing:.08em;color:var(--gold);margin:12px 0 3px}
/* z-60 — above the sheet (40), the #sync chip (45) and the pill (50). With no z-index the sheet
   painted over every toast: the one piece of feedback confirming an outcome was logged was
   invisible on exactly the screens where he needed it.
   pointer-events:none — a bottom-fixed z-60 overlay that CATCHES taps steals the bottom strip of
   the next screen for 1.4s after every logged outcome. Feedback must never eat input. */
.toast{position:fixed;left:0;right:0;bottom:0;z-index:60;pointer-events:none;background:var(--ok);color:#fff;padding:16px;
     font-weight:700;text-align:center;transform:translateY(100%);transition:transform .18s}
.toast.on{transform:none}
.toast.bad{background:var(--bad)}
.gate{padding:28px 18px;max-width:420px;margin:0 auto}
.gate input{width:100%;min-height:52px;font-size:17px;padding:0 12px;border-radius:10px;border:1px solid #2a3f6b;
     background:#0f1d3a;color:var(--ink)}
/* z-index 50 — ABOVE the sheet's 40. Both are position:fixed at the bottom, and with no z-index at
   all the pill sat below the sheet in paint order, so the sheet covered it and swallowed the tap.
   The sheet is always in the DOM, so "a newer list is ready" was permanently unreachable: the one
   control whose entire job is to rescue him from a stale list could never be pressed.
   It sits above the safe-area inset too, so it clears the sheet's grip rather than hiding behind it. */
.pill{position:fixed;left:12px;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));z-index:50;
     background:var(--gold);color:#0b1730;border-radius:12px;box-shadow:0 4px 18px rgba(0,0,0,.5);
     min-height:52px;padding:14px;font-weight:700;text-align:center;display:none}
.mut{color:var(--mut)}
/* ---- the four bands. "Everything on the card" is a hierarchy problem, not an omission problem:
   at 390px the essentials must read without a scroll, so type size carries the ranking. ---- */
.band{border-top:1px solid #22355e;margin-top:12px;padding-top:10px}
.band:first-of-type{border-top:0;margin-top:0;padding-top:0}
.blab{font-size:10px;font-weight:800;letter-spacing:.09em;color:var(--mut);margin-bottom:5px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px}
.kv .k{font-size:11px;color:var(--mut);line-height:1.2}
.kv .v{font-size:17px;font-weight:700;line-height:1.25}
.kv .v.sm{font-size:14px;font-weight:600}
/* NOT CHECKED is a first-class state, never a blank and never a $0 — payoff is missing on 54% of
   leads and a zero there reads as "they owe nothing", which is the opposite of the truth. */
.nc{color:#6b7fa8;font-style:italic;font-weight:600;font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.chip{font-size:11px;font-weight:700;padding:4px 8px;border-radius:999px;border:1px solid #2a3f6b;color:var(--mut)}
.chip.hot{border-color:var(--gold);color:var(--gold)}
.chip.bad{border-color:var(--bad);color:#ff8a80}
.chip.ok{border-color:#2e7d32;color:#7fd18a}
.warnbar{background:#3a2a12;border:1px solid #7a4a12;color:#f0b357;border-radius:9px;padding:9px 11px;
  font-size:13px;font-weight:600;margin-top:10px;line-height:1.35}
.hist{font-size:12px;color:var(--mut);margin-top:8px;line-height:1.5}
/* ---- script bottom sheet ---- */
.sheet{position:fixed;left:0;right:0;bottom:0;background:#0f1d3a;border-top:1px solid var(--gold);
  border-radius:14px 14px 0 0;box-shadow:0 -6px 24px rgba(0,0,0,.45);z-index:40;
  transition:transform .2s ease;padding-bottom:env(safe-area-inset-bottom)}
.sheet .grip{height:46px;display:flex;align-items:center;justify-content:center;cursor:pointer;touch-action:manipulation}
.sheet .grip i{display:block;width:42px;height:4px;border-radius:99px;background:#3a5286}
.sheet .peek{padding:0 16px 12px;font-size:14px;line-height:1.4}
.sheet .body{display:none;padding:0 16px 18px;max-height:58vh;overflow-y:auto;-webkit-overflow-scrolling:touch}
.sheet.open .body{display:block}
/* SHORT VIEWPORTS — phone held sideways, or portrait with the keyboard up. At 375px tall a 58vh body
   plus grip plus peek measured 319 of 375px: the sheet stops being an overlay and becomes the whole
   screen, and the promise that the lead's numbers stay visible behind it quietly stops being true.
   Only 30px of card survived. Shrinking the body costs nothing — it already scrolls (997px of content
   in a 218px window), so this changes how much you see at once, never how much you can reach. */
@media (max-height:560px){
  .sheet .body{max-height:42vh}
  .sheet .peek{font-size:13px;padding-bottom:9px}
}
.say{background:#132244;border-left:3px solid var(--gold);border-radius:0 8px 8px 0;padding:11px 12px;
  margin:8px 0;font-size:16px;line-height:1.5}
.say.es{border-left-color:#3a5286;color:#cddcf5}
.ltag{font-size:10px;font-weight:800;letter-spacing:.08em;color:var(--gold);margin-top:10px}
.ltag.es{color:#8fa9d8}
.cioc{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin:10px 0}
.cioc button{min-height:44px;border-radius:8px;border:1px solid #2a3f6b;background:#132244;color:var(--mut);
  font-size:11px;font-weight:800;letter-spacing:.04em}
.cioc button.on{background:var(--gold);color:#0b1730;border-color:var(--gold)}
.objs{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.objs button{min-height:44px;padding:0 11px;border-radius:999px;border:1px solid #2a3f6b;background:#132244;
  color:var(--ink);font-size:13px;font-weight:600}
.objs button.on{background:var(--gold);color:#0b1730;border-color:var(--gold)}
.never{background:#3a1512;border:1px solid var(--bad);color:#ff9d94;border-radius:9px;padding:9px 11px;
  font-size:12px;line-height:1.45;margin-top:12px}
.noes{font-size:12px;color:#8fa9d8;font-style:italic;margin-top:6px}
/* Clearance for the fixed bottom sheet, sized to the MEASURED collapsed height, not the guessed
   one: 1px border + 46px grip + ~75px of wrapped peek + safe-area inset ≈ 122-156px. It was 76px —
   and only on the lead screen — so the last button of every other screen sat under the sheet and a
   tap opened the script instead. Every #app paint appends this now. */
.sheetpad{height:calc(130px + env(safe-area-inset-bottom))}
/* Logging screens hide the sheet entirely (screenOutcome / afterCall add .hid; screenLead removes
   it). The script belongs to the CALL; while logging, the sheet was only a tap-thief. */
.sheet.hid{display:none}
</style></head><body>
<div id="app" class="wrap"><div class="gate"><h2>Call Mode</h2>
<p class="mut" id="gmsg">Enter your access code.</p>
<input id="code" type="password" inputmode="text" autocomplete="one-time-code" placeholder="DEALFLOW-XXXXXXXX">
<button class="big" id="go">Unlock</button></div></div>
<!-- WHO TEXTED ME. Lives OUTSIDE #app for the same reason the sheet does: every screen replaces
     #app wholesale, and a lookup you can only reach from one screen is a lookup you will not reach
     when a text lands mid-call. Hidden until the payload is unlocked. -->
<button id="lkbtn" class="lkfab" style="display:none" title="Identify an inbound number">&#128269; Who texted me?</button>
<div class="toast" id="toast"></div>
<!-- z-index 45: above the sheet (40), below the pill (50). This line carries "logged to this phone
     only — team sync is off", which matters exactly when he is working the queue with the sheet up;
     in normal flow the open sheet covered it and the warning was unreadable at the moment it applied. -->
<div class="sub" id="sync" style="position:relative;z-index:45;padding:6px 14px;margin:0 10px 10px;
     background:var(--bg);border-radius:8px"></div>
<div class="pill" id="pill">A newer list is ready. Tap to load.</div>
<!-- Script sheet. Lives OUTSIDE #app so re-rendering a lead never tears it down mid-sentence —
     the operator can be reading the opener while the card behind it advances. -->
<div class="sheet" id="sheet"><div class="grip" id="grip"><i></i></div>
<div class="peek" id="peek"></div><div class="body" id="sbody"></div></div>
<script>
var ENC=__PAYLOAD__, OUTCOMES=__OUTCOMES__, BUILT="__BUILT__", SIG="__SIG__", BSIG="__BSIG__";
var BOOKURL="__BOOKURL__";
/* Person-keyed send counts from the server ledger. Authoritative across DEVICES and across cases
   that never shipped to this phone — without it a fresh phone reads every owner as never-texted and
   restarts the 3-touch ladder at touch 1, which is exactly the shape of the August email incident. */
var TEXTPERSON=__TEXTPERSON__;
/* BAKED SEAT (2026-09-09). null on a whole-list build; {n,i,w} on a seat page whose payload
   already holds ONLY that seat's rows (call_mode.seat_rows). When set, fcSeat is ignored, the
   seat prompts are inert and "show all" does not exist — the other half is not on this phone. */
var SEAT=__SEAT__;
var SHOWN=__SHOWN__, TOTAL=__TOTAL__, VMEN=__VMEN__, VMES=__VMES__;
var LS='fcLeadNotes', ROWS=[], PHIDX=null, lane='soon', i=0, cur=null, phIdx=0, notes={};
function $(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function fmt(d){d=String(d||'');return d.length===10?'('+d.slice(0,3)+') '+d.slice(3,6)+'-'+d.slice(6):d;}
/* WHOSE NUMBER r.p[i] IS, in the caller's words. r.ps is the parallel source array built in
   call_rows and only ever carries 'hh' or 'nm' — the listing-agent and shared-office numbers are
   filtered out of the queue upstream, so they can never reach this page. '' for an owner number,
   which is the overwhelming majority and the case that needs no words at all. */
function _phSrcNote(r, i){
  var s = (r.ps||[])[i] || '';
  if(s==='hh') return 'Not the owner — Whitepages places this person at the address (spouse, adult child, tenant). Ask if ' + (r.on||'the owner') + ' is home. Do not discuss the case with them.';
  if(s==='nm') return 'Matched on the NAME only, not the address — this may be a different ' + (r.on||'person') + '. Confirm the address before you mention the filing.';
  return '';
}
/* WHICH LINE THE CALL GOES OUT ON (2026-09-01). 'gv' routes every dial through the Google Voice
   app so the call originates from (786) 490-7825 — the DIALING line — instead of the phone's own
   carrier line, (786) 631-1823. That split is deliberate and load-bearing: 631-1823 is the number
   printed on the site, the cards and every letter, and 25-50 cold dials a day is exactly the
   traffic pattern that gets a number tagged "Spam Likely" by the carriers. If the GV line gets
   burned, Google swaps it for $10 and nothing printed changes; if the PUBLISHED line got burned,
   every card already handed out would be dialing a poisoned number. Set to 'tel' to fall back to
   the plain dialer (one character, next build).
   The GV deep link opens the Voice app on a phone that has it installed and signed into the
   agonzalez account; the number after nc, is the DESTINATION — Voice supplies the caller id.
   SWITCHED TO 'tel' 2026-09-01, SAME DAY, for Quo (786) 502-9550 — and the reasoning moved, not
   died. Quo (OpenPhone rebranded) registers as the PHONE'S default calling app, so a plain tel:
   link routes through Quo natively and the call originates from the Quo line. That beats a
   per-vendor deep link three ways: no URL scheme to break when a vendor renames itself (Quo just
   did), the outcome-logging page never navigates away (tel: opens the dialer OVER the page), and
   swapping dialing vendors becomes a SETTING ON THE PHONE instead of a build. The spam-split
   architecture is unchanged: published line (786) 631-1823 stays clean; the dialing line eats the
   volume risk. Requires: Quo app installed + set as the phone's default calling app — without
   that, tel: falls back to the carrier dialer and dials expose the published line, so CHECK THE
   DEFAULT-APP SETTING before a dial session. Set 'gv' to route via Google Voice deep links.
   SWITCHED BACK TO 'gv' 2026-09-06: Quo dropped (Alejandro: "no more quo", back to 631-1823 as the
   one published number). With no Quo default calling app a plain tel: link goes straight to the
   carrier dialer -- the PUBLISHED line -- which is the exact failure this split exists to prevent.
   'gv' keeps every cold dial on the Voice line: whichever GV number the signed-in Voice account
   holds ((305) 999-5960 BSG Main Line on the Workspace account, or the personal (786) 490-7825) --
   either way NOT the printed number. CHECK WHICH ACCOUNT THE VOICE APP IS SIGNED INTO before a dial
   session; /u/0/ in the deep link means "first signed-in account" in a browser. Letters reverted to
   631-1823 the same day (outreach_mail.py).
   BACK TO 'tel' 2026-09-07, Alejandro's call: "change it to phone link ill get another phone
   eventually when its right". tel: on the desktop routes to Windows Phone Link, which dials from
   his cell -- the PUBLISHED line (786) 631-1823. The spam-split warning above was stated and he
   accepted the risk until a business phone exists; do not flip this back without him. When that
   phone arrives, the clean move is: keep 'tel' and make the NEW phone the one Phone Link is
   paired with (or set 'gv' again), so the printed number never carries the cold-dial volume. */
var DIALER='tel';
function dialHref(d){return DIALER==='gv' ? 'https://voice.google.com/u/0/calls?a=nc,%2B1'+String(d) : 'tel:+1'+String(d);}
/* tel: opens the dialer OVER the page; an https link would navigate AWAY from it — and the whole
   outcome-logging flow (screenOutcome, the after-call bar) lives on this page. So GV dials open in
   a separate tab/app and this page stays put, same as tel: always behaved. */
function dialTarget(){return DIALER==='gv' ? ' target="_blank" rel="noopener"' : '';}
function today(){var d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function nowTS(){return new Date().toLocaleString();}
/* WHO IS SPEAKING. The board keeps identity in SENDER_DEFAULTS + localStorage.fcSender; this page is
   the SAME ORIGIN so it reads the same store rather than baking a second copy that could drift.
   Two heals copied from the board because both were real: a company name saved into `name` renders
   "this is Biscayne Solutions Group with Biscayne Solutions Group", and the legacy auto-injected "Jose"
   must not win over the real identity. Empty saved values must never clobber the default. */
var SENDER = {name:'Alejandro Gonzalez', phone:'(786) 631-1823', bsigner:__BSIGNER__};   /* bsigner: sender.json balloon_signer, investor lane only */
try{
  var _sv = JSON.parse(localStorage.getItem('fcSender')||'{}');
  if(_sv.name === 'Jose') delete _sv.name;
  if(typeof _sv.name === 'string' && /^\s*miami\s+solutions\s+group\b/i.test(_sv.name)) delete _sv.name;
  ['name','phone'].forEach(function(k){ if(_sv[k]) SENDER[k] = _sv[k]; });
}catch(e){}

/* Fill a script template. Global replace — String.replace with a string argument only swaps the
   FIRST match, which is how {sender} survived into a live read-aloud script. */
/* A USABLE HUMAN FIRST NAME, or ''. Mirrors the board's _smsFirst.
   Returns '' when the "owner" is a company, a placeholder, or a name we have already been told not
   to use — greeting someone by the wrong name is worse than not greeting them at all, and on a
   mismatch lead the card explicitly says do not open with it. */
var _NOTNAME = ['UNKNOWN','OWNER','TENANT','OCCUPANT','ESTATE','TRUST','TRUSTEE','HEIRS','TITLE',
                'SEARCH','VIA','THE','LLC','INC','CORP','COMPANY','PROPERTIES','HOLDINGS'];
function firstName(r){
  if(!r) return '';
  /* BALLOON rows: the owner IS a company (flag C) but `on` carries the Sunbiz officer / decision-
     maker, and that human is who answers. Greet them; the company rule below is for owners we
     could not put a person behind. */
  if(r.st==='BAL' && r.on){
    var _t = String(r.on).replace(/[^A-Za-z '-]/g,' ').trim().split(/\s+/)[0]||'';
    return _t ? _t.charAt(0).toUpperCase()+_t.slice(1).toLowerCase() : '';
  }
  if(has(r,'C')) return '';                 // company / trust owner
  if(r.po) return '';                       // roll owner changed — the card says do not use this name
  /* Prefer `on` (owner_clean), which the pipeline has ALREADY flipped from the county roll's
     "LAST,FIRST" to "First Last". Reading r.o directly took the token before the comma — the
     surname — on 32% of leads. Fall back to r.o only when `on` is absent, and strip anything before
     a comma first so the fallback cannot reintroduce the same bug. */
  var src = String(r.on || '').trim();
  if(!src){
    src = String(r.o||'').split(';')[0];
    if(src.indexOf(',') >= 0) src = src.split(',').slice(1).join(' ');   // "LAST,FIRST" -> "FIRST"
  }
  var raw = src.replace(/[^A-Za-z '-]/g,' ').trim();
  if(!raw) return '';
  var tok = raw.split(/\s+/)[0].toUpperCase();
  if(tok.length < 2 || _NOTNAME.indexOf(tok) >= 0) return '';
  return tok.charAt(0) + tok.slice(1).toLowerCase();
}

function fillScript(t, r){
  var first = firstName(r);
  return String(t||'')
    .split('{first}').join(first ? (' ' + first).trimEnd() : '')
    /* {sender} — balloon rows introduce the ADVISOR (sender.json balloon_signer, injected as
       SENDER.bsigner): the refi pitch's asset is his name, and the email the same row received was
       signed by him. Every other row introduces the caller. */
    .split('{sender}').join((r && r.st==='BAL' && SENDER.bsigner) ? SENDER.bsigner : (SENDER.name || ''))
    .split('{street}').join((r && r.a) || 'your property')
    /* Street line only. A text is 160-char segments and reads aloud in the recipient's head — the
       full "1240 NW 54 ST, MIAMI, FL 33142" costs a segment and sounds like a mail merge. Falls back
       to the whole string when there is no comma, and to 'your property' when there is nothing. */
    .split('{st1}').join(((r && r.a) || '').split(',')[0].trim() || 'your property')
    /* {date} — the email's strongest line is the owner's own sale date, so the call says it too.
       Renders the whole clause (with its leading space) or NOTHING: an LP lead has no auction date
       and 9999 is the no-date sentinel, so a bare "{date}" would read as "sale date of ." aloud. */
    .split('{date}').join(
      (r && r.x && r.d != null && r.d < 9000) ? (' with a sale date of ' + r.x) : '')
    .split('{phone}').join(SENDER.phone || '')
    /* {gap} — balloon lane only: THEIR monthly 12%-vs-7% gap off the recorded amount, or the $400k
       example when the amount is unknown. Rounded to $50 because their rate is an assumption. */
    .split('{gap}').join((r && r.bal && +r.bal.amt) ? ('$'+(Math.round((+r.bal.amt)*0.05/12/50)*50).toLocaleString()) : '$1,700')
    .replace(/\s{2,}/g, ' ').replace(/\s+,/g, ',').trim();
}

function loadNotes(){try{notes=JSON.parse(localStorage.getItem(LS)||'{}');}catch(e){notes={};}
  /* A teammate's merge lands here, not through save() — see _FCGEN. Without this bump the lane
     counts would keep reporting the state from before the pull. */
  if(typeof _FCGEN === 'number') _FCGEN++;}
function saveNotes(){try{localStorage.setItem(LS,JSON.stringify(notes));}catch(e){
  /* A swallowed setItem failure (private mode, quota) lost every log while the UI kept stamping
     green checks. Say it, loudly, and put it in the error chip. */
  logErr(e,'saveNotes');
  try{ toast('⚠ NOT SAVED — phone storage is full or blocked'); }catch(_e){}
}}
/* NAME BRIDGE for the extracted board code. The block below is lifted verbatim from
   tracker_template.html and calls the board's helper names — `save()`, `_nowTS()`, `_today()`. This
   page had its own `nowTS`/`today`, so without these aliases `_mergeLead` throws ReferenceError the
   first time a teammate's DO-NOT-CONTACT merges in: the merge dies, the opt-out never lands, and the
   only symptom is a lead that stays dialable. Caught by exercising _mergeLead in a browser — it is
   invisible to `node --check`, which is why the parse guard alone is not enough here. */
/* _FCGEN (declared with the cache it invalidates, down in the board-lane block) is bumped here
   because save() is the one place this page's own note writes land. loadNotes() bumps it too, for
   the writes that arrive as a teammate's merge. */
function save(){ _FCGEN++; saveNotes(); }
function _nowTS(){ return nowTS(); }
function _today(){ return today(); }
/* syncFreshness paints a staleness class onto the board's #syncbtn. The phone has no such button,
   and the board's own implementation returns early when it is absent — so a no-op here is not a stub
   that loses behaviour, it IS the behaviour. It must still EXIST: mergeNotes calls it unconditionally
   as its last statement, so an undefined name threw even when there was nothing to merge. */
function syncFreshness(){}
/* ══════ MERGE + TEAM SYNC — extracted VERBATIM from tracker_template.html at build time ══════
   Not copied. Copying would drift: the phone would merge by last year's rules while the board moved
   on, and the divergence would surface as outcomes that quietly fail to reconcile — precisely the
   silent failure this page exists to delete. The build fails loud if the anchors move.
   Storage key is the board's own `fcLeadNotes` on the same origin, so there is one store, not two. */
__SYNCJS__
/* ══════════════════════════════════════════════════════════════════════════════════════════ */
/* ══════ THE BOARD'S NINE FUNNEL LANES — also extracted VERBATIM, same rule ══════
   WARM / URGENT / CALL / WRITE / LETTER / DOOR / TRACE / WAITING / OUT. These are the counts
   Alejandro reads off the board, and until 2026-09-17 they did not exist on the phone at all:
   Call Mode had nine lanes of its OWN, over a different and much smaller population, so the two
   surfaces never agreed on a single number and no lead could be followed from one to the other.
   _funnelStage now runs HERE as the same bytes it runs as on the board. See extract_funnel_js. */
__FUNNELJS__
/* ══════════════════════════════════════════════════════════════════════════════════════════ */

/* COVERAGE ROWS — the rest of the book (coverage_rows in call_mode.py). Every lead the dial queue
   drops: no traced number, auction past the 60-day window, or over the cap. They carry NO phone
   numbers and NO email addresses, only counts, so nothing here can be dialled or written to by
   accident — see the docstring on the Python side for why that is structural and not a flag. */
var COV = [], BLANE = null;      /* BLANE: null = the dial queue; a FUNNEL key = the board-lane view */

/* THE ADAPTER, and the ONLY thing standing between the two row shapes. It maps FIELDS. It decides
   nothing: which lane a lead is in is _funnelStage's answer, on the board and here, from the same
   extracted bytes. Keep it that way — the moment a rule lives in this function the two surfaces
   have begun to drift again, which is the entire failure this file already carries three scars from. */
function _bView(r){
  if(r._bv) return r._bv;
  var isCov = (r.np !== undefined || r.days !== undefined);
  /* AUCTION IS BLANK FOR LP AND BALLOON ROWS, deliberately. On a dial row `x` is
     `auction or filedDate or filed` — so a fresh lis pendens carries its FILING date there and a
     balloon row carries the note's estimated maturity. Handing either to _saleDays as an auction
     date would invent a countdown the property does not have and drop the lead into URGENT.
     A coverage row already ships `auction` as the auction and nothing else. */
  var auc = isCov ? (r.auction || '') : ((r.lp || r.st === 'BAL') ? '' : (r.x || ''));
  var dys = isCov ? (typeof r.days === 'number' ? r.days : NO_SALE)
                  : (typeof r.d === 'number' ? r.d : NO_SALE);
  /* Arrays of the right LENGTH and nothing else: _funnelStage asks these two questions and only
     these two — "is there a number to dial" and "is there anywhere to write". Length answers both.
     Filling them with the actual numbers would put 1,148 dialable numbers on every coverage row. */
  var nph = isCov ? (r.np || 0) : ((r.p && r.p.length) || 0);
  var v = {
    case: r.c, auction: auc, days: dys,
    phones: new Array(nph), emails: new Array(r.ne || 0),
    sibclaimed: r.sibclaimed || 0, saleBkAct: r.saleBkAct || 0, lpDismissed: r.lpDismissed || 0
  };
  try { Object.defineProperty(r, '_bv', {value:v, enumerable:false}); } catch(e){ r._bv = v; }
  return v;
}

/* WHICH BOARD LANE IS THIS LEAD IN. One wrapper, one authority underneath it.
   `oo` short-circuits to OUT: it means the BUILD already held a suppression verdict for this
   person — the case is in optouts.json, it is on the bounced list, or the identity ledger matched
   an email/phone that said stop with no case attached. The board reaches the same verdict through
   its own baked notes; the phone cannot, because a ledger keyed by a hashed address is not
   something notes carry. Answering OUT without asking is therefore not a second classifier, it is
   the same verdict arriving by the only route this surface has — and it errs toward suppression,
   never away from it. */
function funnelOf(r){
  if(!r) return null;
  if(r.oo) return 'out';
  try { return _funnelStage(_bView(r)); } catch(e){ return null; }
}

/* THE WHOLE BOOK, dial queue first. Not de-duplicated here: coverage_rows is handed the crew-wide
   dial list and excludes every case already in it, so the two sets are disjoint by construction. */
function allLeads(){ return ROWS.concat(COV); }

/* MEMOISED, because head() runs it on EVERY paint and the whole book is now 2,297 leads, not the
   dial queue's 400. Measured at 6.6 ms a call on desktop node over a realistic 2,297-lead mix —
   several times that on the handset this page is written for, on every outcome tap. The board
   carries its own comment about a 36.8 ms-per-paint pass being worth deleting; this one is cheaper
   to keep correct than to re-earn.
   INVALIDATED ON TWO THINGS, and it has to be both:
     _FCGEN  — any note write, ours (save) or a teammate's (loadNotes). Lanes turn on touches.
     the DAY  — _saleDays recomputes days-to-auction live, so a tab left open past midnight must
                not keep yesterday's URGENT. Re-keying on the date costs one Date() per paint. */
/* DECLARED HERE, with the cache it guards, rather than beside save(). `var` hoists across the
   whole script so the bump in save() resolves either way — but keeping the counter next to the
   thing it invalidates is what makes the pair reviewable, and it keeps this block testable on
   its own (_funnelparitytest.py evaluates exactly this region). */
var _FCGEN = 0, _FCC = null, _FCCK = '';
function funnelCounts(){
  /* the sale hour is a second edge inside the day: at 9am every sale-day lead leaves its lane */
  var _nw = new Date();
  var key = _FCGEN + '|' + _nw.toDateString() + '|' + (typeof SALE_HOUR === 'number' && _nw.getHours() >= SALE_HOUR) + '|' + ROWS.length + '|' + COV.length;
  if(_FCC && _FCCK === key) return _FCC;
  var c = {}; FUNNEL_ORDER.forEach(function(k){ c[k] = 0; });
  allLeads().forEach(function(r){ var s = funnelOf(r); if(s && c[s] != null) c[s]++; });
  _FCC = c; _FCCK = key;
  return c;
}
function funnelRows(k){
  return allLeads().filter(function(r){ return funnelOf(r) === k; }).sort(function(a, b){
    var ea = (typeof a.e === 'number') ? a.e : -1e9, eb = (typeof b.e === 'number') ? b.e : -1e9;
    if(eb !== ea) return eb - ea;                        /* money first, same rule as the board */
    var da = _bView(a).days, db = _bView(b).days;
    return (da == null ? 99999 : da) - (db == null ? 99999 : db);
  });
}
/* Is this row actually dialable from this page? A coverage row is not, and that is the honest
   answer to give at the top of its card rather than a dead Call button. */
function _dialable(r){ return !!(r && r.p && r.p.length); }
/* syncStatus writes a one-line status into the page. The board targets its own element; here it goes
   to the same #sync line the gate and queueSync already use, so a sync failure is VISIBLE rather than
   swallowed. Guarded because syncPull calls it before #sync exists on the very first paint.
   🔴 DECLARED AFTER the extracted block ON PURPOSE. The block ships its OWN syncStatus (targeting a
   #syncstat element this page does not have), and with two same-name function declarations in one
   script the LATER one wins — this bridge sat above the injection point and was silently dead, the
   same override class as _dialedAfter. Moving it back above the injection point kills it again
   (and _assert_no_dead_overrides now fails the build if anyone tries).
   NB: never write the literal injection placeholder token inside a comment here — Python's
   str.replace substitutes EVERY occurrence, so the whole 18KB block got injected into the middle
   of this very comment once, shredding the page's syntax. */
function syncStatus(msg){ var el=$('sync'); if(el && msg) el.textContent=msg; }

/* ---- gate: same code, same envelope as the board. fcPw is SHARED (same origin) so a phone that
   already unlocked the board never sees this screen. ---- */
/* Envelope must match foreclosure_leads._encrypt_multi EXACTLY:
     {enc:2, it, iv, ct, keys:[{salt, iv, ct}]}
   One random master key encrypts the payload once; that master key is then wrapped under each
   person's code. The wrapped blob is NOT raw key bytes — it is JSON {mk:<base64>, name:<label>},
   so it must be parsed and the mk base64-decoded before it can be imported.
   ⚠️ Codes written in site.codes as `Label = CODE | PHRASE` wrap under CODE + \x1f + PHRASE. This
   single-field gate therefore opens individual codes only; a phrase-protected team code has to be
   entered on the board first (which shares fcPw with this page anyway). */
async function unwrap(code){
  var te=new TextEncoder();
  var km=await crypto.subtle.importKey('raw',te.encode(code),'PBKDF2',false,['deriveKey']);
  var keys=ENC.keys||[];
  for(var n=0;n<keys.length;n++){
    var w=keys[n];
    try{
      var wk=await crypto.subtle.deriveKey(
        {name:'PBKDF2',salt:b2u(w.salt),iterations:ENC.it||200000,hash:'SHA-256'},
        km,{name:'AES-GCM',length:256},false,['decrypt']);
      var blob=await crypto.subtle.decrypt({name:'AES-GCM',iv:b2u(w.iv)},wk,b2u(w.ct));
      var meta=JSON.parse(new TextDecoder().decode(blob));      // {mk, name}
      /* WHO IS CALLING. Each teammate has their OWN access code, and the code's blob carries their
         name — so the page already knows whether this is Alejandro, Jose or Carlos without asking.
         Stamped onto every call/text this device logs, which is what makes the shared registry
         able to say "Carlos called this one 2h ago" instead of an anonymous timestamp. */
      try{ if(meta.name) localStorage.setItem('fcCaller', meta.name); }catch(e){}
      var mk=await crypto.subtle.importKey('raw',b2u(meta.mk),{name:'AES-GCM'},false,['decrypt']);
      var pt=await crypto.subtle.decrypt({name:'AES-GCM',iv:b2u(ENC.iv)},mk,b2u(ENC.ct));
      return JSON.parse(new TextDecoder().decode(pt));
    }catch(e){}
  }
  return null;
}
function b2u(b64){var s=atob(b64),a=new Uint8Array(s.length);for(var i=0;i<s.length;i++)a[i]=s.charCodeAt(i);return a;}

async function boot(){
  loadNotes();
  /* Say whose page this is BEFORE the code prompt. Two seat pages look identical otherwise, and the
     one thing a caller must never be unsure about is which list is in front of him. */
  if(SEAT && SEAT.w){ try{ document.title='Call Mode · '+SEAT.w;
    var _gh=document.querySelector('.gate h2'); if(_gh) _gh.textContent='Call Mode · '+SEAT.w; }catch(e){} }
  /* PAYLOAD SHAPE. It used to be a bare rows array; it is now {r:rows, x:phoneIndex} so the lookup
     can resolve numbers that are NOT among the dialable rows (67% of them). Accept BOTH shapes —
     an older decrypted copy can still be sitting in this device's cache. */
  var take=function(p){ if(!p) return null;
    if(Array.isArray(p)){ ROWS=p; PHIDX=null; COV=[]; return p; }
    /* `b` is the coverage set (2026-09-17). Absent on an older decrypted copy sitting in this
       device's cache, in which case the board lanes count only the dial queue and say so, rather
       than quietly reporting a book two thirds the size of the one on the laptop. */
    ROWS=p.r||[]; PHIDX=p.x||null; COV=p.b||[]; return ROWS; };
  var saved=null; try{saved=localStorage.getItem('fcPw');}catch(e){}
  if(saved){ var r=await unwrap(saved); if(take(r)){ return start(); } }
  $('go').onclick=async function(){
    var c=$('code').value.trim(); if(!c) return;
    $('gmsg').textContent='Checking…';
    var r=await unwrap(c);
    if(!r){ $('gmsg').textContent='That code did not open this page. If it is new, the page may be cached — close the tab fully and reopen.'; return; }
    try{localStorage.setItem('fcPw',c);}catch(e){}   // unlocking either page unlocks both
    take(r); start();
  };
}
/* THE MORNING WORKER'S QUEUE. `fcCallQueue` is a durable localStorage ledger the worker fills when a
   lead is phone-only — same origin, so this page reads it directly with no new transport. Its own
   comment in the board records why it is durable: "before this, 192 queued calls evaporated with the
   tab and the week produced exactly one dial."
   Entries retire only on a logged call outcome, which is exactly what this page does.

   THIS WAS STILL DEVICE-LOCAL (found 2026-08-23). fcCallQueue lives in the browser that built it —
   the unattended Morning Worker tab on the laptop — and team sync only ever carried `notes`, never
   this array. 333 leads queued in 7 days, 1 dialled: the queue this function reads was empty on
   every phone, every time, because nothing had ever written to ITS localStorage. `.wq` on a case's
   own (synced) note is the fix — see _mergeLead in the extracted sync block below — so union both
   sources here: fcCallQueue for same-device continuity, `.wq` for whatever arrived from a teammate
   or the laptop. */
function workerQ(){
  var s = {};
  try{ (JSON.parse(localStorage.getItem('fcCallQueue')||'[]')||[]).forEach(function(x){ s[x.c]=1; }); }catch(e){}
  /* wqx = wq's retire TOMBSTONE (2026-08-26). The first fix DELETED .wq on retire, but _mergeLead
     is add-only for wq — the other device's surviving copy re-infected this lane on the next pull,
     so a lead someone had already worked came back as "queued" after its cooldown. A deletion
     cannot win an add-only merge; a newer tombstone can. Show a case only when its wq is strictly
     newer than any wqx: re-queueing later still works (new wq > old wqx), and a same-day tie goes
     to the retire — worked today is not offered again today. */
  for(var k in notes){
    var n = notes[k];
    if(k.charAt(0)!=='#' && n && n.wq && !(n.wqx && n.wqx >= n.wq)) s[k]=1;
  }
  return Object.keys(s);
}
function retireFromWorkerQ(caseId){
  try{
    var q = JSON.parse(localStorage.getItem('fcCallQueue')||'[]')||[];
    var n = q.filter(function(x){ return x.c !== caseId; });
    if(n.length !== q.length) localStorage.setItem('fcCallQueue', JSON.stringify(n));
  }catch(e){}
  var nn = notes[caseId];
  if(nn && nn.wq) nn.wqx = today();
}
/* CLIENT-SIDE SUPPRESSION — the read-back the first version was missing.
   Build-time filtering catches opt-outs and stays that existed when the page was BUILT. Everything
   decided since then lives only in notes: a wrong-number logged this morning, a DNC a teammate
   synced an hour ago, a person-level opt-out keyed to an email or phone. Without reading these back,
   the page happily re-serves a lead somebody already disproved — which is how a stranger gets called
   twice and how an opt-out gets violated by the device that recorded it. */
/* Digits of every phone recorded as opted out under a '#number' note key rather than a case.
   The board writes these (optout_sync.py, the STOP-reply loop) and they match no case on their own,
   so without this lookup they suppress nothing at all.

   Deliberately NOT memoized on the notes object's identity, the way the board memoizes
   _optedOutIdentities: mergeNotes assigns `notes[caseId] = merged` IN PLACE, so the object is
   identical after a teammate syncs an opt-out and an identity memo would never invalidate. A stale
   index here means the phone keeps dialing someone who just opted out — precisely what it exists to
   prevent. pool() rebuilds it on every call instead; that is O(notes) once, not per row. */
var _OPTPH = null;
function optPhones(force){
  if(_OPTPH && !force) return _OPTPH;
  var s = {};
  for(var k in notes){
    if(k.charAt(0) !== '#') continue;
    var nn = notes[k] || {};
    if(nn.optout || nn.status === 'DO NOT CONTACT') s[k.slice(1).replace(/\D/g,'')] = 1;
  }
  _OPTPH = s;
  return s;
}
/* Who is on this phone — set from the access-code blob at unlock (each teammate has their own
   code). Falls back to a neutral label so a touch is never stamped with the wrong person. */
function caller(){
  try{ return localStorage.getItem('fcCaller') || ''; }catch(e){ return ''; }
}
/* Names of teammates whose dials or texts have merged into this phone's notes store.
   PROOF the team key is right and someone else is actively working the same list. Empty means
   either we are truly solo, or the team key does not match — we cannot tell those two apart from
   this side (server holds ciphertext), but seeing NAMES here is the one signal that flips the
   silent-fail into a visible one.

   Bounded to 24h so a phone that once synced with someone months ago does not permanently claim
   "team of 2". */
function _teammatesSeen(){
  var me = caller(), out = {}, cutoff = Date.now() - 86400000;
  try{
    Object.keys(notes || {}).forEach(function(c){
      var n = notes[c] || {};
      (n.touches || []).forEach(function(t){
        var ts = +t.tsu || 0;
        if(ts >= cutoff && t.by && t.by !== me) out[t.by] = 1;
      });
      (n.dials || []).forEach(function(d){
        var ts = +d.tsu || 0;
        if(ts >= cutoff && d.by && d.by !== me) out[d.by] = 1;
      });
    });
  }catch(e){}
  return Object.keys(out);
}
/* THE SHARED REGISTRY, read side. Newest CALL touch on this lead from ANY device, with who made
   it. Team sync merges teammates' notes into the same store, so this sees Carlos's dials too. */
function lastCall(n){
  var best = null;
  ((n && n.touches) || []).forEach(function(t){
    if((t.ch||'') !== 'call') return;
    var ts = +t.tsu || +new Date(t.ts || t.d || 0) || 0;
    if(!ts) return;
    if(!best || ts > best.ts) best = {ts: ts, out: t.out || '', by: t.by || ''};
  });
  ((n && n.dials) || []).forEach(function(d){
    var ts = +d.tsu || 0;
    if(ts && (!best || ts > best.ts)) best = {ts: ts, out: d.oc || '', by: d.by || ''};
  });
  return best;
}
function agoTxt(ms){
  var m = Math.max(0, Math.round((Date.now() - ms) / 60000));
  if(m < 60) return m + 'm ago';
  var h = Math.round(m / 60);
  if(h < 36) return h + 'h ago';
  return Math.round(h / 24) + 'd ago';
}
/* HARD suppression — relationship-ending reasons ONLY (opt-out, DNC, wrong number, dead lead,
   opted-out phone). These block EVERYTHING, including the after-call text. Kept separate from
   the queue cooldown below because the two answer different questions: "may we contact this
   person at all?" versus "is this lead due to be dialled again?". */
function hardSuppressed(r){
  var n = notes[r.c] || {};
  if(n.wrongown) return 'wrong number reported';
  if(n.optout || n.status === 'DO NOT CONTACT') return 'opted out';
  if(n.status === 'Dead') return 'dead';
  var ph = optPhones(), p = r.p || [];
  for(var j=0;j<p.length;j++) if(ph[p[j]]) return 'this number opted out';
  return '';
}
/* ═══════════════════ WHY A LEAD IS NOT ON THE LIST — three tiers, 2026-09-10 ═══════════════════
   Returns {k:'hard'|'cool'|'soft'|'', t:<the sentence shown to the caller>}.

   THE TIERS ARE NOT ONE RULE, and collapsing them is what produced "why am I still getting leads
   I already contacted":
     hard — may we contact this person AT ALL? Opt-out, DNC, wrong number, dead, every number bad.
     cool — did we CALL them recently? Full outcome cooldown, in EVERY lane, email included. Once
            you have actually spoken to someone, no lane may re-serve them.
     soft — did we reach them on ANOTHER channel recently? Until today this tier did not exist:
            lastCall() drops every touch whose ch is not 'call', so an emailed lead was, to this
            page, a lead nobody had ever contacted. 684 of 1069 leads had outreach and no call.
            The board has always suppressed on any channel (_lastTouchMs); the phone did not, so
            two surfaces read one notes store and gave opposite answers.

   THE ONE EXEMPTION: soft does not apply in the lane whose own predicate IS that channel. The
   "Emailed / replied" lane exists precisely to CALL the people we emailed; a blanket "any touch
   hides it" would empty the one lane built for the job. A TEXT still hides a lead in the email
   lane — only a lane's own channel is exempt.

   laneK is a PARAMETER, never the module global: head() evaluates eight lanes in a single paint,
   which a global cannot express, and that is exactly how a button came to read 40 while the lane
   behind it held 3. */
function supReason(r, laneK){
  var h = hardSuppressed(r);
  if(h) return {k:'hard', t:h};
  if((r.p||[]).length && nextLivePh(r, 0) < 0) return {k:'hard', t:'every number marked bad'};
  var _n = notes[r.c] || {};
  var _open = _replyOpen(r);
  if(!_open){
    var _c = suppressed(r);
    if(_c) return {k:'cool', t:_c};
    /* TIER 3 — reached on another channel. Same 30-day floor a logged "no" gets, because a soft
       no is a soft no whichever way it arrived. */
    var laneCh = (laneDef(laneK || lane) || {}).ch || '';
    var lo = lastOutreach(r), soft = null;
    var softH = (_n.status === 'Not interested') ? 720 : COOL_DEFAULT_H;
    for(var chK in lo){
      if(chK === 'call' || chK === laneCh) continue;   // tier 2 owns 'call'; a lane owns its own
      if((Date.now() - lo[chK].ts) >= softH * 3600000) continue;
      if(!soft || lo[chK].ts > soft.ts) soft = {ch:chK, ts:lo[chK].ts, by:lo[chK].by};
    }
    if(soft) return {k:'soft', t:'we already sent a ' + soft.ch + ' ' + agoTxt(soft.ts)
                                 + (soft.by ? ' — ' + soft.by : '')};
  }
  return {k:'', t:''};
}
/* TIER 2 only — a call or a dial. Kept as its own function because afterCall(), _teammateCall()
   and the registry all ask specifically "have we CALLED this person", which is a different
   question from "have we contacted them". */
function suppressed(r){
  var h = hardSuppressed(r);
  if(h) return h;
  /* Every number this lead has is marked dead — there is nothing left to dial, so it must leave the
     queue rather than serve a card whose only buttons are dead numbers. Counted in the visible
     hidden tally like every other suppression (NO SILENT CAPS), and reversible: badnum_audit.py
     restores the mark and the lead comes straight back. */
  if((r.p||[]).length && nextLivePh(r, 0) < 0) return 'every number marked bad';
  var n = notes[r.c] || {};
  /* ALREADY WORKED — by me OR by a teammate. Without this, Carlos logs a call, the note syncs to
     this phone, and the lead still sits in the queue waiting to be dialled a second time by the
     other guy. The cooldown is the SAME outcome-aware one the board honours (logOutcome writes
     n.cooldownH: no-answer comes back tomorrow, a real conversation waits longer), so the two
     surfaces can never disagree about whether a lead is due.
     QUEUE-ONLY. This branch must never gate the after-call text: the call HE JUST LOGGED is
     inside its own cooldown by definition, so using suppressed() there read back "called 0m ago
     by Alejandro · Do not text" — his own dial blocking the missed-call text, the single best
     moment to send one (2026-08-19 field report). afterCall gates on hardSuppressed(). */
  var lc = lastCall(n);
  if(lc){
    /* A 'pending' dial is one we started and never tagged. Short hold, read from here rather than
       from n.cooldownH, so an abandoned dial is retryable today and nothing syncs. */
    var coolH = (lc.out === 'pending') ? PENDING_HOLD_H
              : ((typeof n.cooldownH === 'number' && n.cooldownH >= 0) ? n.cooldownH : COOL_DEFAULT_H);
    /* NO MEANS NO, retroactively. Every notint logged BEFORE 2026-09-02 carries the old 72h in
       its stored cooldownH, so raising the outcome table alone would have left every already-
       collected "no" cycling back every three days until someone re-logged it. The STATUS is the
       durable fact; read it directly and hold the same 30 days the new logs get. */
    if(n.status === 'Not interested' && coolH < 720) coolH = 720;
    if((Date.now() - lc.ts) < coolH * 3600000){
      return 'called ' + agoTxt(lc.ts) + (lc.by ? ' by ' + lc.by : '')
           + (lc.out ? ' · ' + lc.out : '');
    }
  }
  /* ONE HUMAN, MANY CASE ROWS. Suppression was case-keyed, and the feeds routinely carry the
     same person twice -- an HOA case and a bank case on one condo, or two properties in the LP
     lane. Calling row A logged the outcome on A while row B kept serving the identical human as
     a fresh lead ("why am I still getting the same people"). r.pcs is the person's OTHER case
     list, stamped at build for exactly this; read it. Sibling notes arrive over the same sync,
     so this also covers Carlos having called the person on the sibling case. */
  if(r.pcs && r.pcs.length){
    for(var si=0; si<r.pcs.length; si++){
      var sc = r.pcs[si];
      if(!sc || sc === r.c) continue;
      var sn = notes[sc] || {};
      var slc = lastCall(sn);
      if(!slc) continue;
      var sCool = (typeof sn.cooldownH === 'number' && sn.cooldownH >= 0) ? sn.cooldownH : COOL_DEFAULT_H;
      if(sn.status === 'Not interested' && sCool < 720) sCool = 720;
      if((Date.now() - slc.ts) < sCool * 3600000){
        return 'same person called ' + agoTxt(slc.ts) + (slc.by ? ' by ' + slc.by : '')
             + (slc.out ? ' · ' + slc.out : '') + ' (their case ' + sc + ')';
      }
    }
  }
  return '';
}
/* Do-Not-Text is text-only and must NOT hide a lead from CALLING — but a number on it should not be
   the one we put first. Kept separate from suppressed() on purpose. */
function dntSet(){
  try{ return new Set(JSON.parse(localStorage.getItem('fcDNT')||'[]')); }catch(e){ return new Set(); }
}
/* ── DEAD NUMBERS — `badph`, the field the MORNING WORKER already writes ─────────────────────────
   The worker's "bad number" control appends the digits to notes[case].badph and its card filter
   drops them (_workerCard). Call Mode never read it, so a number retired on the laptop was still
   dialled from the phone — and now that this page has its own "Bad number" outcome, both surfaces
   must mean the same thing. Same field, one vocabulary.

   WHY NOT FILTER r.p INTO A NEW ARRAY: phIdx indexes r.p AND the parallel rank array r.r, and
   render() re-reads cur from pool() on every paint. Handing back a filtered CLONE would give `cur`
   a new identity each render and silently drop cur._rcStay — the "Stay on this lead" override —
   putting the already-called takeover into a loop. So the arrays stay intact and we simply never
   LAND on a dead index. Same result, none of the index drift.

   ⚠️ This mark is destructive and effectively permanent (badnum_audit.py exists because the worker's
   version fired 61 times in one day, 20 inside a single minute, and killed a live number belonging
   to an owner who texted in that evening asking to sell). Hence the confirm in screenOutcome and the
   fact that nothing here ever DELETES a number — it is skipped, and badnum_audit.py can restore it. */
function badSet(r){
  var out = {};
  try{
    ((notes[r.c]||{}).badph||[]).forEach(function(b){
      var d = String(b).replace(/\D/g,''); if(d) out[d] = 1;
    });
  }catch(e){}
  return out;
}
function isBadPh(r, ix){
  var p = (r.p||[])[ix]; if(!p) return false;
  return !!badSet(r)[String(p).replace(/\D/g,'')];
}
/* First dialable index at or after `from`, or -1 when every remaining number is dead. */
function nextLivePh(r, from){
  var p = r.p||[], bad = badSet(r);
  for(var k = Math.max(0, from|0); k < p.length; k++){
    if(!bad[String(p[k]).replace(/\D/g,'')]) return k;
  }
  return -1;
}

/* pool() is the ONE place suppression is evaluated. Everything else reads what it left behind.

   Two reasons this is not just tidiness. First, `suppressed()` depends on the opt-out phone index,
   which must be rebuilt from live notes — a caller that skipped the rebuild would silently judge a
   lead against a stale index and keep dialing someone who just opted out. Second, head() used to run
   its own full pass over ROWS to count hidden leads, doubling the per-paint cost for a number pool()
   already knew. Both problems disappear if there is exactly one evaluator. */
var _SUPN = 0;
/* TIER 3 gets its OWN counter. "Suppressed for a compliance reason or already called" and "we
   emailed them on Tuesday" are different facts and must never share a number — the same rule that
   already keeps _SEATN and _CLMN separate. */
var _SUPT = 0;
/* Per-lane {raw, net} counts, filled by pool() in one pass so head()'s buttons and the list behind
   them are the same number. */
var _LANEN = {};
/* ONE NUMBER, BOTH SURFACES. The board has defaulted to 72h since it shipped (_workerEligible,
   _laneStats); this page defaulted to 24 in three places. 433 of 1069 leads carry no stored
   cooldownH at all — every lead nobody has logged an outcome on — so they came back on the phone
   two days before the board agreed they were due. This is the FALLBACK only: the per-outcome table
   (CALL_OUTCOMES) is unchanged and a no-answer still returns tomorrow, which is deliberate. */
var COOL_DEFAULT_H = 72;
/* A dial with no logged outcome is an ATTEMPT, not a contact. It must hold the lead briefly (a
   double-tap, or the other caller dialing the same second) and it must be retryable the same day.
   It must NOT be written to n.cooldownH: that field is the CONTACT cooldown, it syncs to the board
   on a bare-date tie, and the board's clock never reads dials — so a 6 written there suppressed
   nothing there, it just sat on the note and rescaled the NEXT real outcome. Read-side only now. */
var PENDING_HOLD_H = 6;
/* Case ids that got at least one logged outcome this session. Deduped, because a lead dialled on
   three numbers is one lead worked, not three. */
var _WORKED = [];
/* THE buy-box predicate — one function, used by both the lane and the button count. Those were
   two identical inline copies with a comment promising they could never disagree; a promise kept
   by hand is a promise that breaks the first time only one copy is edited, and adding the
   tax-deed test was exactly that edit. */
function isBuyBox(r){ return !!r.bb && r.bbs !== 'UNDERWATER' && !r.bbtd; }
/* BALLOON LANE (2026-09-08) — hardmoney_balloon.py rows (st:'BAL'): an LLC investor whose hard-money
   note is at/near its est. 24-month wall. Different desk, different script (SCRIPT.bal), different
   card (_balCard). Never in soon/lp: those are homeowner lanes and would read them the rescue script. */
function isBalloon(r){ return r.st==='BAL'; }
/* ═══════════════ LANES (2026-09-09) — one table, one predicate per button ═══════════════
   r.d is FROZEN at build (the "9999 days out" bug the board already fixed with _saleDays); a page
   left open past midnight would keep a passed sale in Urgent. Recompute from the baked date string
   r.x with the board's own regex. LP rows have no sale date and BAL rows count down to maturity on
   r.d — neither goes through liveDays. */
/* The board's sale-hour rule, reached through a typeof guard: _heldToday arrives with the extracted
   funnel block, and a page built without it (the suites' stub pages) must still paint its lanes. */
function _heldNow(x){ return typeof _heldToday === 'function' && _heldToday(x); }
function liveDays(r){
  if(r.lp || isBalloon(r)) return null;
  var m = String(r.x||'').match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
  if(!m) return (typeof r.d==='number' && r.d<9000) ? r.d : null;
  var t=new Date(+m[3],+m[1]-1,+m[2]), td=new Date(); td.setHours(0,0,0,0);
  var d=Math.round((t-td)/864e5);
  /* Sale morning past the sale hour reads as held (-1), the board's own rule: _heldToday is lifted
     from the template with the clock block, so the phone and the board flip at the same minute. */
  return (d===0 && _heldNow(r.x)) ? -1 : d;
}
/* ═════════════ WHAT COUNTS AS "WE ALREADY CONTACTED THIS PERSON" (2026-09-10) ═════════════
   Byte-for-byte the board's own vocabulary, tracker_template.html:3780. `worker` is deliberately
   ABSENT: a morning-batch worked/skipped breadcrumb is TRIAGE, not a message to a human — the
   board even gives a skip its own separate 24h. Counting it would suppress leads nobody wrote to. */
var OUTREACH_CH = {call:1, text:1, email:1, letter:1, door:1};
/* INBOUND is THEIR contact with US, not ours with them. _markReplied writes {ch:'email',
   out:'OWNER REPLIED'} and the phone's own reply button writes a 'text' touch reading
   "THEY REPLIED (inbound)". Treating either as outreach buries every lead who wrote back, for
   three days, on the strength of their own reply — the exact inversion the board's _replyWaiting
   exists to prevent. Same /replied|inbound/ convention isEmailFU already uses. */
function _inbound(t){ return /replied|inbound/i.test(String((t && t.out) || '')); }
/* Newest OUTBOUND touch per channel, across this case AND every sibling case of the same person
   (r.pcs — one human, many case rows, same reason the call cooldown already walks it).
   Returns {ch: {ts, by, out, c}}. One pass, because this now runs per row per lane per paint. */
function lastOutreach(r){
  var best = {}, cs = [r.c].concat(r.pcs || []);
  for(var k=0;k<cs.length;k++){
    var T = (notes[cs[k]] || {}).touches || [];
    for(var j=0;j<T.length;j++){
      var t = T[j];
      if(!OUTREACH_CH[t.ch] || _inbound(t)) continue;
      var ts = +t.tsu || +new Date(t.ts || t.d || 0) || 0;
      if(!ts) continue;
      var b = best[t.ch];
      if(!b || ts > b.ts) best[t.ch] = {ts:ts, by:t.by||'', out:t.out||'', c:cs[k]};
    }
  }
  return best;
}
/* THEY ANSWERED US. A reply inverts the whole rationale of a cooldown: it exists so we do not
   pester someone we just contacted, and a person who wrote back is asking to be called. Open until
   we send something outbound after their reply. Mirrors the board's _replyWaiting without its
   MAILLOG dependency, which this page does not have. Bypasses the cooldowns ONLY — an opt-out,
   a DNC or a wrong number still hides the lead, because those are not about timing. */
function _replyOpen(r){
  var cs = [r.c].concat(r.pcs || []);
  for(var k=0;k<cs.length;k++){
    var n = notes[cs[k]] || {};
    if(!n.replied) continue;
    var w = +new Date(n.replied) || 0;
    if(!w) return true;
    var answered = (n.touches||[]).some(function(t){
      if(!OUTREACH_CH[t.ch] || _inbound(t)) return false;
      return (+t.tsu || +new Date(t.ts || t.d || 0) || 0) > w;
    });
    if(!answered) return true;
  }
  return false;
}
/* Any touch on THIS case or a sibling case (r.pcs) inside `days`, matching pred(touch, note). */
function _touchesWithin(r, days, pred){
  var cut=Date.now()-days*86400000, cases=[r.c].concat(r.pcs||[]);
  for(var k=0;k<cases.length;k++){ var n=notes[cases[k]]||{}, T=n.touches||[];
    for(var j=0;j<T.length;j++){ var ts=+T[j].tsu||+new Date(T[j].ts||T[j].d||0)||0;
      if(ts>=cut && pred(T[j],n)) return true; } }
  return false;
}
/* WORKER lane: this morning's queue, plus anything the worker actually touched in the last 7 days.
   workerQ() is the ONE authority on "queued" — it already unions this device's fcCallQueue with the
   synced .wq flag and honours the .wqx retire tombstone, so re-deriving that here would be a second
   copy of a rule this codebase has already broken twice. Cached per pool() pass: it parses
   localStorage, and this predicate runs once per row per lane count. */
var _WQSET = null;
function wqSet(){ if(_WQSET) return _WQSET;
  var s={}; try{ workerQ().forEach(function(c){ s[c]=1; }); }catch(e){}
  _WQSET=s; return s; }
function isWorker(r){
  if(wqSet()[r.c]) return true;
  return _touchesWithin(r, 7, function(t){ return t.ch==='worker'; });
}
/* EMAIL lane: warm follow-up — the owner replied, or an outreach email went out in the last 30 days. */
function isEmailFU(r){
  var n=notes[r.c]||{};
  if(n.replied) return true;
  return _touchesWithin(r, 30, function(t){ return t.ch==='email' || /replied|inbound/i.test(t.out||''); });
}
function _dayLane(r, lo, hi){ if(r.lp || isBalloon(r)) return false;
  var d=liveDays(r); return d!==null && d>=lo && d<=hi; }
/* `ch` marks the two lanes whose predicate IS a channel. supReason exempts a lane from the soft
   (other-channel) tier for its OWN channel only — the Emailed lane exists to call the people we
   emailed, so suppressing them there would empty the one lane built for the job. A TEXT still
   hides a lead in the email lane. Every other lane has no `ch` and is exempt from nothing. */
/* 3-DAY (Jesse, 2026-09-16): sale within 3 BUSINESS days, equity at face value (county value above
   the debt as posted), case filed 2024+. Python twin: three_day.is_three_day() — keep in step. */
function _bizDays(r){
  var m = String(r.x||'').match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/); if(!m) return null;
  var t=new Date(+m[3],+m[1]-1,+m[2]), cur=new Date(); cur.setHours(0,0,0,0);
  if(t<cur || _heldNow(r.x)) return -1;
  var n=0; while(cur<t){ cur.setDate(cur.getDate()+1); if(cur.getDay()>0 && cur.getDay()<6) n++; }
  return n;
}
function _caseYear(c){ c=String(c||'').toUpperCase(); var m;
  if((m=c.match(/^(\d{4})-\d/))) return +m[1];
  if((m=c.match(/^[A-Z]{4}-(\d{2})-/))) return 2000+(+m[1]);
  if((m=c.match(/^50(\d{4})[A-Z]{2}/))) return +m[1];
  return 0; }
function isThreeDay(r){
  if(r.lp || isBalloon(r)) return false;
  var b=_bizDays(r); if(b===null || b<0 || b>3) return false;
  var owed=(+r.py||0)||(+r.jg||0); if(!(+r.v>0) || !(owed>0) || +r.v<=owed) return false;
  return _caseYear(r.c) >= 2024;
}
var LANES = [
  {k:'d3',     lbl:'3-DAY',             pred:isThreeDay, hide0:true},
  {k:'email',  lbl:'Emailed / replied', pred:isEmailFU,  hide0:true, ch:'email'},
  {k:'worker', lbl:'Worker',            pred:isWorker,   hide0:true, ch:'worker'},
  {k:'urgent', lbl:'Urgent 0-7',        pred:function(r){ return _dayLane(r,0,7); },   hide0:true},
  {k:'soon',   lbl:'Sale soon 8-45',    pred:function(r){ return _dayLane(r,8,45); },  hide0:false},
  {k:'late',   lbl:'46-60',             pred:function(r){ return _dayLane(r,46,60); }, hide0:true},
  {k:'lp',     lbl:'Fresh filings',     pred:function(r){ return !!r.lp && !isBalloon(r); }, hide0:false},
  {k:'bal',    lbl:'Balloon',           pred:isBalloon,  hide0:true},
  {k:'bb',     lbl:'Buy-box',           pred:isBuyBox,   hide0:true}
];
function laneDef(k){ for(var q=0;q<LANES.length;q++) if(LANES[q].k===k) return LANES[q]; return laneDef('soon'); }
/* ══════════════════ TEAM SEATS — two phones, one list, nobody dialled twice ══════════════════
   fcSeat = {n: <how many callers>, i: <which one am I, 0-based>, w: <my name>}. Absent or n<=1
   means solo and every filter below is a no-op, so a lone caller sees exactly what he saw before.

   THE PARTITION IS THE DEFENCE. r.sb is a stable 0-11 bucket of the case number stamped at build
   (see call_rows). With n seats I take the buckets where sb % n === i, so with two phones the
   same lead is NEVER in both queues at once. Nothing is negotiated, so there is nothing to race
   and nothing to be offline for.

   THE CLAIM IS THE EXCEPTION HANDLER. It rides the existing encrypted note sync, which pulls
   every 45s, so it CANNOT be the primary defence — two people opening the same lead inside the
   same 45 seconds both see it unclaimed. It exists for deliberate out-of-lane work and for
   "he's already on the phone with this one", and it is advisory: it greys and warns, it does not
   lock anyone out.

   CLAIMS EXPIRE. A claim with no TTL turns one abandoned tap into a lead nobody may ever call
   again — the silent-suppression failure this codebase keeps re-learning. 90 minutes, and an
   outcome clears it outright. */
var SEAT_TTL_MS = 90*60*1000, SEAT_ALL = false;
/* Baked seat wins outright. fcSeat is shared per-origin with the other seat's page, so a value
   left there by an old test could otherwise hide EVERYTHING on a page that already holds one half. */
function _seat(){ if(SEAT) return SEAT;
    try{ var s=JSON.parse(localStorage.getItem('fcSeat')||'{}');
    return (s && s.n>1 && s.i>=0 && s.i<s.n) ? s : null; }catch(e){ return null; } }
function _seatSet(n, i, w){
  if(SEAT) return;                                   // fixed at build; nothing on the phone may move it
  if(!n || n<=1){ localStorage.removeItem('fcSeat'); }
  else localStorage.setItem('fcSeat', JSON.stringify({n:n, i:i, w:(w||'').slice(0,18)}));
  i=0; try{ render(); }catch(e){}
}
function _seatMine(r){ var s=_seat(); if(!s || SEAT_ALL) return true;
  if(typeof r.sb !== 'number') return true;    // unstamped row (older build) -> visible to all,
  return (r.sb % s.n) === s.i; }               // never silently dropped from every queue at once
/* Who holds a live claim on this case, or '' — MY OWN claim never counts against me. */
function _clmOwner(c){
  var nt = notes[c]; if(!nt || !nt.clm) return '';
  var k = nt.clm; if(!k.d || k.d === (typeof _deviceId==='function' ? _deviceId() : '')) return '';
  /* AN OUTCOME ENDS THE CLAIM. Checked against the fields this page actually writes — n.status
     and a logged call via lastCall() — not against invented ones. Written as nt.out/nt.outcome
     first, which no code path ever sets, so the branch could never fire and the claim would have
     hung on for the full TTL after the lead was already worked. suppressed() would have hidden
     the row anyway, so nothing would have LOOKED wrong; it would just have quietly held a lead
     out of the other caller's "show all" for 90 minutes. */
  if(nt.status) return '';
  try{ if(typeof lastCall==='function' && lastCall(nt)) return ''; }catch(e){}
  var age = Date.now() - (+k.t || 0);
  if(age < 0 || age > SEAT_TTL_MS) return '';               // stale, or a skewed clock
  return k.w || 'a teammate';
}
/* Stake a claim the moment a lead is opened, and let the normal debounced push carry it. */
function _clmTake(c){
  if(!c || !_seat()) return;
  var s=_seat(); notes[c] = notes[c] || {};
  var mine = notes[c].clm;
  if(mine && mine.d === _deviceId() && (Date.now()-(+mine.t||0)) < 60000) return;  // already fresh
  if(_clmOwner(c)) return;                                  // do not steal a live claim
  notes[c].clm = {d:_deviceId(), t:Date.now(), w:(s.w||'seat '+(s.i+1))};
  try{ saveNotes(); }catch(e){}
  try{ if(typeof syncPushSoon==='function') syncPushSoon(); }catch(e){}
}
/* ═══════════════ TEAM WATCH — say it out loud when a teammate works a lead ═══════════════
   Everything needed to KNOW a lead was already called was already here: every dial and text
   writes `by: caller()`, suppressed() computes "called 3m ago by Carlos · No answer", and
   screenRegistry() lists the whole log split mine-vs-team.

   All of it was PULL. Nothing ever told him. syncPull merges a teammate's calls every 45s and
   says nothing — only an opt-out violation toasts — so the only way to learn Carlos had worked
   twenty leads was to go looking for a screen that says so. A recognition system nobody is
   notified by is a filing cabinet.

   Two things get announced, and the second is the one that matters:
     1. A quiet running tally: "Carlos logged 3 calls".
     2. THE LEAD ON HIS SCREEN RIGHT NOW. If a merge lands an outcome on the exact card he is
        reading, he is about to dial someone his cousin just hung up with. That is the whole
        double-dial problem, in the one moment where a count in a header is useless.

   Dedup is by case+timestamp so a lead re-merged on every 45s tick announces ONCE. The very
   first load seeds the seen-set silently — otherwise opening the page after a day off would
   announce two hundred calls as if they had just happened. */
var _TW_SEEN = 'fcTeamSeen', _TW_INIT = 'fcTeamSeenInit';
function _twSeen(){ try{ return new Set(JSON.parse(localStorage.getItem(_TW_SEEN)||'[]')); }
  catch(e){ return new Set(); } }
function _twSave(s){ try{ var a=Array.from(s); localStorage.setItem(_TW_SEEN,
  JSON.stringify(a.slice(-800))); }catch(e){}   // bounded: this grows forever otherwise
}
function teamWatch(){
  var me = caller(), seen = _twSeen(), first = !localStorage.getItem(_TW_INIT), fresh = [];
  Object.keys(notes || {}).forEach(function(c){
    var lc = null; try{ lc = lastCall(notes[c]); }catch(e){}
    if(!lc || !lc.by) return;
    if(me && lc.by === me) return;                        // my own work is not news
    var k = c + '|' + lc.ts;
    if(seen.has(k)) return;
    seen.add(k);
    if(first) return;                                     // seed silently on the first load
    if(Date.now() - lc.ts > 6*3600000) return;            // absorb anything genuinely old
    fresh.push({c:c, by:lc.by, out:lc.out, ts:lc.ts});
  });
  _twSave(seen);
  if(first){ localStorage.setItem(_TW_INIT, '1'); return 0; }
  if(!fresh.length) return 0;

  /* THE ONCE-PER-SESSION SEAT NAG. If a teammate's dial just merged in and we have NO seat,
     both phones are reading the same queue — the exact bug the seat split exists to prevent.
     Fire ONCE per session (not per tick) so it prompts but does not become the nag-of-the-day.
     Guarded on presence of a team key so a lone-caller on an old note dump never sees this. */
  try{
    var _nag = 'fcSeatNagShown';
    if(!_seat() && !sessionStorage.getItem(_nag) && localStorage.getItem('fcTeamKey')){
      sessionStorage.setItem(_nag, '1');
      var _by = fresh[0].by || 'A teammate';
      /* PLAIN TEXT — toast() sets textContent, so this line used to render the literal characters
         "&#9888;" and "&mdash;" on the phone instead of the warning sign and the dash. */
      toast('⚠ ' + _by + ' is dialing this same list — set your SEAT so you '
          + 'never double-dial. Tap the top bar to split.', {bad:true, ms:12000});
    }
  }catch(e){}

  /* THE CARD HE IS LOOKING AT is teamRecheck()'s job, not this one's — it already owns that
     moment with a full takeover: "ALREADY CALLED", who, when, the outcome, a 3s countdown and a
     STAY button. Re-toasting over it would be two warnings for one event.
     What teamRecheck could not do is FIRE ON ITS OWN. It was wired only to visibilitychange, so
     it caught the return from the dialer and nothing else: sit reading a card for two minutes
     while Carlos works that same lead, and the 45s pull lands his outcome in notes with the
     screen unchanged. Handing it the background tick is the whole fix. */
  var here = cur && fresh.filter(function(f){ return f.c === cur.c; })[0];
  if(here){ try{ teamRecheck(); }catch(e){} }
  var others = fresh.filter(function(f){ return !here || f.c !== here.c; });
  if(others.length){
    var names = {}; others.forEach(function(f){ names[f.by] = (names[f.by]||0)+1; });
    var bits = Object.keys(names).map(function(w){
      return esc(w) + ' ' + names[w] + ' call' + (names[w]===1?'':'s'); });
    try{ toast('&#128100; ' + bits.join(' &middot; ') + ' &mdash; already handled, pulled from your queue',
        {ms:6000}); }catch(e){}
  }
  return fresh.length;
}
/* ════════ ALREADY-CALLED RECHECK — the one gap in the team defence (2026-09-02) ════════
   The seat partition stops the same lead being in two queues; the claim greys a lead a teammate
   just opened; suppressed() drops worked leads from the NEXT pool() pass. What nothing covered:
   the lead ALREADY PAINTED on this screen when the teammate's outcome arrives. visibilitychange
   ran syncPull -> loadNotes() and then went silent, so Alejandro comes back from one call, the
   merge quietly writes "Carlos called this lead 3 minutes ago" into notes, and the person still
   filling his screen gets dialled a second time. Field report 9/1: "we clash on repetitive
   clients... it should say this person has already been called today, and move on."

   teamRecheck() runs after every merge AND at screenLead paint. A teammate call inside the
   cooldown paints an amber takeover with WHO and WHEN and auto-advances after 3.5s — tap STAY to
   keep the lead (deliberate revisits are legitimate; silent auto-anything is how leads vanish).

   MY OWN dials never trigger it: logging outcome then "Try their next number" returns to
   screenLead on the SAME lead, now inside its own cooldown — auto-skipping there would rip a
   multi-number sequence away mid-lead. Skip only when the last call's `by` is a DIFFERENT name
   (or blank-but-not-me is impossible: `by` is stamped from caller() on every dial). */
var _rcTimer=null;
function _teammateCall(r){
  /* Checks the lead AND every sibling case of the same person -- the takeover exists to stop a
     human being double-dialled, and the human does not care which of their case numbers the
     first call was logged under. */
  var cases=[r.c].concat(r.pcs||[]);
  for(var ci=0; ci<cases.length; ci++){
    var n=notes[cases[ci]]||{}, lc=(typeof lastCall==='function')?lastCall(n):null;
    if(!lc || !lc.by || lc.by===caller()) continue;
    var coolH=(typeof n.cooldownH==='number' && n.cooldownH>=0)?n.cooldownH:COOL_DEFAULT_H;
    if(n.status==='Not interested' && coolH<720) coolH=720;
    if((Date.now()-lc.ts) < coolH*3600000) return lc;
  }
  return null;
}
function teamRecheck(){
  if(SCREEN!=='lead' || !cur) return;
  var lc=_teammateCall(cur);
  if(!lc) return;
  if(_rcTimer) return;                       // takeover already up
  var app=$('app');
  var msg='<div class="card" style="border:2px solid #c69a3a">'
    + '<div class="ltag" style="color:#c69a3a">ALREADY CALLED</div>'
    + '<div class="addr" style="font-size:17px">'+esc(cur.o||cur.a||'')+'</div>'
    + '<div style="font-size:16px;margin-top:6px"><b>'+esc(lc.by)+'</b> called '
    + esc(agoTxt(lc.ts)) + (lc.out?' &middot; '+esc(lc.out):'') + '</div>'
    + '<div class="mut" style="margin-top:6px">Moving to the next lead so you two never double-dial. '
    + 'Tap STAY if you are picking this one up on purpose.</div>'
    + '<button id="rcgo" style="margin-top:14px">Next lead &rarr; <span id="rcn">3</span></button>'
    + '<button id="rcstay" class="ghost" style="margin-top:8px">Stay on this lead</button>'
    + '</div><div class="sheetpad"></div>';
  app.innerHTML=msg;
  var left=3;
  var go=function(){ clearInterval(_rcTimer); _rcTimer=null;
    var P=pool(), k; for(k=0;k<P.length;k++) if(P[k].c===cur.c){ i=k; break; }
    /* pool() has already dropped this lead (it is suppressed now), so position i holds its
       successor — render() paints them. When it was the LAST lead, render()'s own bounds
       handling shows the done screen. */
    render();
  };
  _rcTimer=setInterval(function(){ left--; var el=$('rcn');
    if(el) el.textContent=String(left);
    if(left<=0) go(); }, 1170);
  $('rcgo').onclick=function(){ go(); };
  $('rcstay').onclick=function(){ clearInterval(_rcTimer); _rcTimer=null;
    /* Staying is a deliberate override — remember it for THIS lead so the recheck does not
       re-takeover on the next repaint of the same screen. Cleared on advance. */
    cur._rcStay=1; screenLead(); };
}
function pool(){
  /* Rebuilt here rather than in render() so EVERY caller gets a fresh index — advance() and
     screenOutcome() both call pool() outside a render, and a teammate's opt-out landing between
     paints would otherwise be missed. 0.05ms for 900 note keys; the O(rows x notes) version this
     replaced measured 36.8ms per pass at 400 rows, twice per paint. */
  optPhones(true);
  _WQSET = null;                                       // fresh queue read once per pass, not per row
  /* ONE predicate per lane, shared with head()'s button counts so the number on the button and the
     list behind it can never disagree. Notes on the individual lanes:
     BUY-BOX — everything matching a standing acquisition criterion, in either board state, so it
       deliberately does NOT split on r.lp. UNDERWATER rows are dropped from this lane only (an
       upside-down owner still deserves the advisor call, but this lane answers "could we ACQUIRE").
     Date lanes / lp both exclude balloon rows: a balloon row has no lis pendens (lp=0) and would
       otherwise surface under "Sale soon" — an investor pitched the foreclosure script.
     email / worker overlap the date lanes on purpose: a follow-up is a follow-up regardless of the
       clock, and it is still one lead with one cooldown in suppressed(). */
  if(lane==='wq') lane='worker';                       // legacy key from an older stored state
  var base = ROWS.filter(laneDef(lane).pred);
  var n = 0, s = 0;
  var keep = base.filter(function(r){
    var sr = supReason(r, lane);
    if(sr.k === 'soft'){ s++; return false; }          // reached on another channel — its own count
    if(sr.k){ n++; return false; }                     // compliance, or already called
    return true; });
  _SUPN = n; _SUPT = s;
  /* ONE PASS, EIGHT LANES. head() reads this instead of re-deriving its button numbers: a count
     derived a second way is a count that drifts, and this one drifted badly — the buttons used a
     bare ROWS.filter(pred) with NO suppression, so "Emailed / replied · 40" could sit above a lane
     holding three. Every row is evaluated per lane because the soft tier is lane-dependent by
     design; hard and cool short-circuit before any per-lane work. */
  _LANEN = {};
  LANES.forEach(function(L){
    var raw = 0, net = 0;
    ROWS.forEach(function(r){
      if(!L.pred(r)) return;
      raw++;
      if(!supReason(r, L.k).k) net++;
    });
    _LANEN[L.k] = {raw:raw, net:net};
  });
  /* SEAT FILTER LAST, so _SUPN keeps meaning "suppressed for a compliance reason" and does not
     silently absorb "belongs to the other caller" — two very different facts that must never
     share a counter. Counted separately and shown separately. */
  _SEATN = 0; _CLMN = 0;
  keep = keep.filter(function(r){
    /* SEAT filter is conditional — it is MY setting, so turning it off must hand me the list back.
       CLAIM filter is UNCONDITIONAL, and that difference is the point: a claim means a teammate is
       on the phone with that person RIGHT NOW, which is true whether or not I have split my own
       list. Gating it on _seat() meant switching myself to solo silently un-hid every lead Carlos
       was actively working — the exact double-dial this feature exists to prevent, reachable by
       tapping the one control that sounds harmless. Nothing writes a claim unless a seat is set,
       so on a genuinely solo crew this costs nothing. */
    if(_seat() && !SEAT_ALL && !_seatMine(r)){ _SEATN++; return false; }
    if(_clmOwner(r.c)){ _CLMN++; return false; }
    return true; });
  return keep;
}
var _SEATN = 0, _CLMN = 0;
function start(){
  /* The worker's queue is the DEFAULT when it has anything in it. Those leads were triaged this
     morning and are phone-only — the worker could not reach them any other way, so they are the
     highest-intent list on the device. Sale-soon and Fresh-filings stay one tap away. */
  /* NEVER OPEN ON 'email'. It shipped as the default on 2026-09-09 and that was wrong: it is a
     follow-up list — by definition people we have already written to — so opening on it put him
     straight onto leads he had already contacted, which is the complaint that produced this whole
     change. The lane stays, one tap away. Chosen on the NET count so we also never open on a lane
     whose every row is suppressed and land on "Nothing in this lane". */
  lane = lane || 'soon';
  pool();                                              // fills _LANEN for the choice below
  var _net = function(k){ return (_LANEN[k] || {}).net || 0; };
  lane = _net('worker') ? 'worker' : 'soon';
  if(SEAT){
    try{ localStorage.removeItem('fcSeat'); }catch(e){}          // legacy per-phone seat is dead here
    try{ var _cw = caller();
      /* PLAIN TEXT. toast() sets textContent, so markup and HTML entities appear literally —
         tags here rendered as "<b>Carlos</b>" on the phone. */
      if(_cw && SEAT.w && _cw.toLowerCase() !== String(SEAT.w).toLowerCase())
        toast('This page is built for ' + SEAT.w + ' but you unlocked as ' + _cw
            + '. Calls will log under ' + _cw + '.', {bad:true, ms:9000}); }catch(e){}
  }
  // the lookup only exists once the payload is open — show it here, not in the gate
  try{
    var _lk = $('lkbtn');
    if(_lk){ _lk.style.display = 'block'; _lk.onclick = function(){ screenLookup(); }; }
  }catch(e){}
  i=0; render(); freshCheck();
  paintSync();
}
/* THE SYNC LINE, and it is now a BUTTON when sync is off.
   It used to read "Turn it on in the board" — technically true (same origin, so the board's
   fcTeamKey is this page's fcTeamKey) and useless in practice: the board is 6.4 MB, this page
   exists precisely because that does not open on cell data, and the moment someone needs team
   sync is the moment they are standing at a door holding the phone. Sending them to the heavy
   surface to type eight characters is how a feature ends up switched off forever.
   Everything needed was already here — startTeamSync, syncPush, syncPull, _deviceId. Only the
   input was missing. */
function paintSync(){
  var k=null; try{k=localStorage.getItem('fcTeamKey');}catch(e){}
  var el=$('sync'); if(!el) return;
  /* Seat shown inline, from _seat() itself — I reached for a _seatTxt() helper that does not
     exist, which would have thrown a ReferenceError on every paint of the one line that tells him
     sync is working. {n,i,w}: w is the optional name, i is 0-based and reads 1-based. */
  var _s=_seat(), seatTxt='';
  if(_s && !SEAT_ALL) seatTxt=' · '+esc(_s.w||('Seat '+(_s.i+1)))+' of '+_s.n;
  else if(_s && SEAT_ALL) seatTxt=' · ALL leads';
  if(k){ el.textContent='Team sync ON'+seatTxt;
    el.onclick=function(){ screenTeamKey(); };
    try{ syncPull().then(function(){ loadNotes();
      /* THE WRONG-KEY TELL. A mistyped team code is a perfectly valid team of one; the server holds
         ciphertext and can never detect it, so this line said "Team sync ON" forever while nothing
         arrived. What IS detectable: a full list of leads with almost no history behind it. Real
         state carries a touch on most leads (1050 of 1069 at the last good backup), so a near-empty
         notes store against a full ROWS means the history did not land — wrong code, cleared
         storage, or a genuine first run. Checked AFTER the pull so a cold start does not flash it. */
      try{
        var _nk = 0; try{ _nk = Object.keys(notes||{}).length; }catch(_e){}
        if(ROWS.length > 50 && _nk < Math.max(10, ROWS.length * 0.05)){
          el.innerHTML = '&#9888; <b style="color:#e2645f">NO LEAD HISTORY LOADED</b> &mdash; '+_nk
            + ' notes for '+ROWS.length+' leads. Every lead will look fresh and nothing will be '
            + 'hidden. Check the team code &mdash; one wrong character makes a silent team of one. '
            + '<b style="color:var(--gold)">Tap to check it</b>';
          el.onclick=function(){ screenTeamKey(); };
        }
      }catch(_e){}
      /* 2026-09-04: RE-POOL after the pull. The initial paint (and a lane switch) built the queue
         from pre-sync notes, so the first leads shown were ones a teammate or the other device had
         ALREADY worked -- "as soon as I land it puts me on people I already called". render() only
         repaints on the lead screen, so an in-progress call is never stomped. */
      if(SCREEN==='lead'){ try{ render(); }catch(_e){} }
    }); }catch(e){}
    return; }
  el.innerHTML='Team sync is OFF — outcomes log to this phone only. <b style="color:var(--gold)">Tap to turn it on</b>';
  el.onclick=function(){ screenTeamKey(); };
}
function screenTeamKey(){
  SCREEN='tkey';
  var cur_k=''; try{ cur_k=localStorage.getItem('fcTeamKey')||''; }catch(e){}
  $('app').innerHTML='<div class="card"><div class="ltag">TEAM SYNC</div>'
    + '<div class="mut" style="margin:6px 0 10px">Everyone dialing types the SAME code. It is the '
    + 'encryption key for your notes, not an account — nobody can read them without it, and there '
    + 'is nothing to recover if it is lost.</div>'
    /* THE FAILURE MODE, SAID OUT LOUD. A mistyped key is not an error — it is a valid team of one.
       Both phones say "sync ON" and neither ever sees the other, because the server only ever
       holds ciphertext it cannot compare. Nothing can detect this for them, so warn instead. */
    + '<div class="mut" style="margin-bottom:10px"><b>Paste it, do not retype it.</b> A single wrong '
    + 'character makes a second team that silently never syncs — both phones will still say ON.</div>'
    + '<input id="tkin" type="text" autocapitalize="off" autocorrect="off" spellcheck="false" '
    + 'placeholder="team code (8+ characters)" value="'+esc(cur_k)+'" '
    + 'style="width:100%;font-size:17px;padding:12px;border-radius:10px;border:1px solid #2c3a52;'
    + 'background:#0e1626;color:#fff">'
    + '<div id="tkerr" class="mut" style="color:#e0655f;margin-top:6px;display:none"></div>'
    + '<button id="tkgo" style="margin-top:12px">Turn team sync on</button>'
    + (cur_k?'<button id="tkoff" class="ghost" style="margin-top:8px">Turn sync OFF on this phone</button>':'')
    + '<button class="ghost" style="margin-top:8px" onclick="SCREEN=\'lead\';render();paintSync()">Back</button>'
    + '</div><div class="sheetpad"></div>';
  var go=function(){
    var v=($('tkin').value||'').trim();
    /* 8 is the board's minimum and they MUST agree — a key this page accepts and the board
       rejects would sync from the phone and silently never from the laptop. */
    if(v.length<8){ var e=$('tkerr'); e.style.display='block';
      e.textContent='Needs at least 8 characters — this is the encryption key.'; return; }
    try{ localStorage.setItem('fcTeamKey', v); if(!localStorage.getItem('fcDevice')) _deviceId();
      startTeamSync(); }catch(e){}
    try{ toast('Team sync ON — logging as ' + esc(caller() || 'this phone'), {ms:5000}); }catch(e){}
    /* FORCE the seat picker if none is set. Not a nag — a required next step. A team key without
       seats is the exact bug this whole feature exists to prevent: both phones read the same
       queue and the split does nothing. The wizard now ends with the caller SET, not just with
       sync ON. If they already have a seat (returning after turning sync off + back on), skip
       straight to the queue — no reason to re-ask. */
    if(!_seat() && !SEAT){
      SCREEN='lead'; render(); paintSync();
      setTimeout(function(){ try{ seatMenu(); }catch(e){} }, 250);
      return;
    }
    SCREEN='lead'; render(); paintSync();
  };
  $('tkgo').onclick=go;
  $('tkin').onkeydown=function(ev){ if(ev.key==='Enter') go(); };
  if($('tkoff')) $('tkoff').onclick=function(){
    try{ localStorage.removeItem('fcTeamKey'); }catch(e){}
    SCREEN='lead'; render(); paintSync();
  };
}

/* ADVANCE BY IDENTITY, NEVER BY `i++`.

   `i` indexes into pool(), and pool() is recomputed from live notes on every render. Logging an
   outcome can REMOVE the current lead from it — do-not-contact, wrong number and not-interested all
   become suppressed the moment they are written, and a worker-queue entry is retired on any outcome.
   When that happens the successor slides down into slot `i`, so `i++` lands one past them and a
   real person is silently skipped. (Suppression is new; this is a regression it introduced, and the
   worker lane had the same hole before it.)

   So: remember who was next BEFORE mutating, and go find them afterwards. Falls back to the worked
   lead's own position when the intended successor is also gone, and holds position when both
   vanished — because then everything at `i` has already shifted down. */
function advance(workedC, nextC){
  if(cur) delete cur._rcStay;       // the stay override is per-visit, never per-lead-forever
  SCREEN='lead';                    // leaving the interactive screen ON PURPOSE — render may paint
  var P = pool(), k;
  if(nextC) for(k=0;k<P.length;k++) if(P[k].c===nextC){ i=k; return render(); }
  for(k=0;k<P.length;k++) if(P[k].c===workedC){ i=k+1; return render(); }
  render();
}
/* WHICH SCREEN IS UP. The board's extracted mergeNotes ends with `render()` — harmless on the
   board, where render repaints a static list, and CATASTROPHIC here, where the page is a wizard.
   Returning from the tel: dialer or sms: composer fires visibilitychange -> syncPull -> mergeNotes
   -> render(), which replaced the outcome screen / after-call panel / "did it send?" confirm 1-2s
   after he got back, and reset phIdx to 0. That is why the FIRST call of a session worked (nothing
   to merge yet) and everything after it "went faulty": his own first push guaranteed every later
   return had a change to merge. The data must land; the REPAINT must wait. */
var SCREEN='lead';
/* Last afterCall args, so a language toggle can repaint that panel in place (it is built from
   closure args, not from `cur` alone the way the other screens are). */
var _lastAfter=null;
function render(){
  if(SCREEN!=='lead'){ return; }   // never stomp an interactive screen — advance() repaints fresh
  /* BOARD-LANE VIEW. Sits in front of the dial queue rather than filtering it: these lanes cover
     leads with no phone at all, which pool() cannot express and must not be taught to. */
  if(BLANE){ $('app').innerHTML = boardList(); wire(); return; }
  var P=pool();
  /* An emptied lane and a never-populated lane are NOT the same event, and until now they printed the
     same words. Work every lead and the pool drains to zero, so a finished session was reporting
     "Nothing in this lane" — indistinguishable from a broken build or the wrong lane. Same rule as
     the fail-silent one on the auction horizon: an empty result must never look like missing data. */
  if(!P.length){
    var done = _WORKED.length
      ? '<b>Lane cleared.</b><div class="sub">'+_WORKED.length+' lead'+(_WORKED.length===1?'':'s')+' worked. Switch lanes above, or reopen tomorrow.</div>'
      : '<b>Nothing in this lane.</b><div class="sub">Switch lanes above.</div>';
    $('app').innerHTML=head()+'<div class="card">'+done+'</div><div class="sheetpad"></div>'; wire(); return;
  }
  /* Count what was ACTUALLY worked, not what is left in the pool. `P.length` was standing in for it,
     but the two diverge the moment an outcome removes a lead: log 5 do-not-contacts and the pool is
     empty, so it reported "0 worked" for a full session. A number on screen that is not the thing it
     is labelled is the same defect class as the "0% equity" and "$0 owed" bugs. */
  if(i>=P.length){ $('app').innerHTML=head()+'<div class="card"><b>Queue clear.</b><div class="sub">'
      +_WORKED.length+' lead'+(_WORKED.length===1?'':'s')+' worked this session. Reopen tomorrow.</div></div>'
      +'<div class="sheetpad"></div>'; wire(); return; }
  /* Keep the NUMBER position when the lead is unchanged. A legitimate lead-screen render (sync merge
     landing while he reads the card on number 2) must not snap him back to number 1. */
  var pc=cur&&cur.c, pp=phIdx;
  cur=P[i]; phIdx=(cur&&cur.c===pc&&pp<cur.p.length)?pp:0;
  /* NEVER LAND ON A DEAD NUMBER. A lead can carry a number marked bad here or on the laptop's
     worker card; without this the card offers it as the one to dial. pool() has already dropped
     leads whose numbers are ALL bad, so this normally finds one — the second call is belt and
     braces for a mark that landed between the pool pass and this paint. */
  var _lv=nextLivePh(cur, phIdx); if(_lv<0) _lv=nextLivePh(cur, 0); if(_lv>=0) phIdx=_lv;
  /* Stake the claim on ARRIVAL at the lead, not on tapping dial. He reads the card, checks the
     file and rehearses the open before he dials — that whole stretch is exactly when the other
     phone must be told this one is taken. Claiming at dial-time would leave the most likely
     collision window completely unguarded. */
  _clmTake(cur.c);
  screenLead();
}
function head(){
  /* Every button counts with the SAME predicate pool() filters with — a count derived a second way
     is a count that drifts. Zero-count lanes are hidden (except Sale soon / Fresh filings, which
     always show so an empty build never reads as a broken build). */
  /* Read pool()'s per-lane pass rather than re-deriving. The old line here was
     `ROWS.filter(L.pred).length` — the same predicate but a DIFFERENT pipeline, with no
     suppression in it, so a button could read 40 above a lane that held 3.
     Hidden only at raw 0 (an empty build must never read as a broken build); a lane whose rows are
     all suppressed shows a dimmed 0, because hiding it would be exactly the silent cap the line
     below forbids. */
  var laneBtns = LANES.map(function(L){
    var C = _LANEN[L.k] || {raw:0, net:0};
    if(!C.raw && L.hide0) return '';
    return '<button data-l="'+L.k+'" class="'+(lane===L.k?'on':'')+(C.raw && !C.net?' dim':'')+'">'
         + L.lbl + (C.raw ? ' &middot; '+C.net : '')+'</button>';
  }).join('');
  /* NO SILENT CAPS. Suppression is correct, but a list that quietly shrank looks identical to a list
     that was always that size — the exact confusion that hid 466 callable leads behind call_list's
     --max 30. Say the number out loud.
     Read from pool()'s last count rather than re-deriving it: this is THIS LANE's hidden count, and
     head() always renders downstream of a pool() call. */
  var sup = _SUPN;
  return '<div class="top"><div class="lane">'+laneBtns+'</div>'
    + boardBar()
    +(sup?('<div class="supn">'+sup+' hidden &mdash; wrong number, opted out, dead, or <b>already called</b> '
          +'(by you or a teammate) &middot; <a href="#" id="reglink" style="color:var(--gold)">see the call log</a></div>'):'')
    /* TIER 3, on its own line and never folded into the number above it. "Opted out" and "we
       emailed them Tuesday" are different facts; a caller who cannot tell them apart cannot tell
       whether the list is short because it is clean or short because it is stale. */
    +(_SUPT?('<div class="supn">'+_SUPT+' more hidden &mdash; <b>we already reached them another way</b> '
          +'(email, text, letter or door) within '+COOL_DEFAULT_H+'h</div>'):'')
    /* SEAT CHIP. Always rendered, even solo, because "am I splitting the list right now" is a
       question the caller must be able to answer without opening a menu — a partition you cannot
       see is one you assume is on when it is off, and that is the whole bug he asked to fix.
       The two hidden counts are printed SEPARATELY and never folded into `sup`: "carlos has it"
       and "opted out" are different facts and a caller must be able to tell them apart. */
    +seatChip()
    /* The build stamp, in the TOP bar on purpose. BUILT was baked into the page and rendered
       nowhere, so a phone serving last week's list had no visible tell — the operator's only signal
       was leads that felt stale. The bottom of the screen belongs to the sheet (fixed, z-40), which
       covers anything placed there; the top bar is the one strip nothing ever overlays. */
    +'<div class="supn">list built '+esc(BUILT.replace('T',' '))+errChip()+'</div>'
    +sessStrip()
    +'</div>';
}
/* ══════════════ THE BOARD'S NINE LANES, ON THE PHONE (2026-09-17) ══════════════
   A second strip under the dial lanes, not a replacement for them. The lanes above answer "who do I
   dial next"; these answer "where does the whole book stand", which is the question the laptop was
   the only place to ask. Same nine keys, same classifier, same counts — so a lane that reads 129 on
   the board reads 129 here, and tapping it lists the same 129 leads.

   COLLAPSED BY DEFAULT to one summary line. Nine more buttons permanently above the card would push
   the opener down the screen, and this page's whole reason for existing is that the thing he needs
   is reachable in one tap on a handset. */
var BBAR_OPEN = false;
function boardBar(){
  var c = funnelCounts(), tot = 0;
  FUNNEL_ORDER.forEach(function(k){ tot += c[k]; });
  if(!tot) return '';
  if(!BBAR_OPEN){
    /* The summary line names the two lanes that are ACTIONABLE-BUT-NOT-DIALABLE, because those are
       the ones that were invisible here before and they are the bulk of the book: TRACE is work for
       the skip-tracer, LETTER is work for the mail run. */
    var bits = [];
    if(c.trace)  bits.push(c.trace + ' to skip-trace');
    if(c.letter) bits.push(c.letter + ' to mail');
    if(c.door)   bits.push(c.door + ' to knock');
    return '<div class="supn"><a href="#" onclick="BBAR_OPEN=true;render();return false" '
         + 'style="color:var(--gold)">Board lanes &middot; ' + tot + ' leads</a>'
         + (bits.length ? ' &mdash; ' + bits.join(' &middot; ') : '') + '</div>';
  }
  var btns = FUNNEL_ORDER.map(function(k){
    var F = FUNNEL[k];
    return '<button data-b="' + k + '" class="' + (BLANE === k ? 'on' : '')
         + (c[k] ? '' : ' dim') + '">' + F.ic + ' ' + esc(F.t) + ' &middot; ' + c[k] + '</button>';
  }).join('');
  return '<div class="lane" style="margin-top:2px">' + btns + '</div>'
       + '<div class="supn">Every lead on the board, in the board\'s own lanes &middot; '
       + '<a href="#" onclick="BBAR_OPEN=false;BLANE=null;i=0;render();return false" '
       + 'style="color:var(--gold)">hide</a></div>';
}

/* The list behind a board-lane button. Read-only on purpose for the rows that are not dialable:
   the point of putting TRACE on the phone is to be able to SEE and HAND OFF the 1,068 leads nobody
   can call yet, not to invent a way to call them. */
function propertyLabel(r){
  if(r.a) return r.a;
  if(r.ag) return 'Possible property: ' + r.ag + ' — unverified';
  return 'Property address unresolved — check the case record';
}
function ownerLabel(r){
  return r.o || r.on || 'Owner name unresolved — check the case record';
}
function boardList(){
  var k = BLANE, F = FUNNEL[k] || {t:k, ic:'', d:''}, rows = funnelRows(k);
  var head_ = '<div class="card"><b>' + F.ic + ' ' + esc(F.t) + ' &middot; ' + rows.length + '</b>'
            + '<div class="sub">' + esc(F.d) + '</div></div>';
  if(!rows.length){
    return head() + head_ + '<div class="card">Nothing in this lane.</div><div class="sheetpad"></div>';
  }
  /* CAPPED AT 200 ROWS PER PAINT, and it says so. 1,068 rows of DOM on a handset is a locked tab,
     and a list that silently stopped at 200 is the same silent cap this file already carries two
     comments about. The COUNT above is always the true one. */
  var CAP = 200, shown = rows.slice(0, CAP);
  var body = shown.map(function(r){
    var dial = _dialable(r), bv = _bView(r);
    var clock = (!bv.auction) ? 'no sale date'
              : (bv.days == null || bv.days >= NO_SALE) ? esc(bv.auction)
              : (bv.days < 0 ? 'sale passed' : 'sale in ' + bv.days + 'd');
    var eq = (typeof r.e === 'number') ? (r.e + '% equity') : 'equity not checked';
    /* WHAT IS ACTUALLY BLOCKING THIS LEAD, named. "Not callable" is not an answer a caller can act
       on; "no traced number" sends it to the skip-tracer and "sale is 94 days out" does not. */
    /* NAME THE BLOCKER. "Not callable" is not something a caller can act on; "the only number on
       file is do-not-call" and "the sale is 94 days out" send the lead to two different places. */
    var why = dial ? ''
      : (r.kd && r.kd >= bv.phones.length) ? 'do-not-call on every number — mail or door only'
      : bv.phones.length ? 'not in today\'s dial queue'
      : bv.emails.length ? 'no phone — email only'
      : 'no phone, no email — skip-trace first';
    return '<div class="card"' + (dial ? ' data-open="' + esc(r.c) + '" style="cursor:pointer"' : '')
         + '><b>' + esc(ownerLabel(r)) + '</b>'
         + '<div class="sub">' + esc(propertyLabel(r)) + '</div>'
         + '<div class="sub">' + clock + ' &middot; ' + eq
         + (why ? ' &middot; ' + why : ' &middot; <b style="color:var(--gold)">tap to call</b>') + '</div></div>';
  }).join('');
  var more = rows.length > CAP
    ? '<div class="card"><b>' + (rows.length - CAP) + ' more in this lane.</b><div class="sub">'
      + 'Showing the first ' + CAP + ' by equity. The count above is the whole lane — the rest are '
      + 'on the board.</div></div>' : '';
  return head() + head_ + body + more + '<div class="sheetpad"></div>';
}
function seatChip(){
  var s=_seat();
  if(SEAT){
    /* Baked page: no "change", no "show all" — the other seat's rows are not in this payload. */
    var bb2 = []; if(_CLMN) bb2.push(_CLMN+' being worked now');
    return '<div class="supn"><b>'+esc(SEAT.w||('Seat '+(SEAT.i+1)))+'</b> &mdash; seat '+(SEAT.i+1)
      +' of '+SEAT.n+' (built in)'+(bb2.length?' &middot; '+bb2.join(' &middot; '):'')+'</div>';
  }
  /* THE BUG STATE ALEJANDRO FLAGGED: a team key is on and no seat is set. Both phones read
     the same queue and the whole partition is a decoration. The old chip said "Solo — whole
     list", which reads as a valid choice, not a warning. Called out in red now, with the
     evidence — how many teammates we've actually seen dialing — so it cannot be dismissed
     as a cosmetic default. */
  if(!s){
    var hasKey = false; try{ hasKey = !!localStorage.getItem('fcTeamKey'); }catch(e){}
    if(hasKey){
      var mates = _teammatesSeen();
      var who = mates.length
        ? '<b>' + mates.slice(0,3).map(esc).join(', ') + '</b>'
        : '';
      return '<div class="supn" style="color:#e2645f;font-weight:600">'
        + '&#9888; Team sync ON but no seat set &mdash; you and '
        + (who || 'your teammate') + ' see the SAME leads &middot; '
        + '<a href="#" onclick="seatMenu();return false" style="color:var(--gold)">split now</a>'
        + '</div>';
    }
    return '<div class="supn">Solo &mdash; whole list &middot; '
    + '<a href="#" onclick="seatMenu();return false" style="color:var(--gold)">split with a teammate</a></div>';
  }
  var bits = [];
  if(_SEATN) bits.push(_SEATN+' on the other phone'+(s.n>2?'s':''));
  if(_CLMN)  bits.push(_CLMN+' being worked now');
  return '<div class="supn">'
    + (SEAT_ALL ? '<b style="color:var(--gold)">Showing EVERYONE\'S leads</b>'
                : '<b>'+esc(s.w||('Seat '+(s.i+1)))+'</b> &mdash; seat '+(s.i+1)+' of '+s.n)
    + (bits.length ? ' &middot; '+bits.join(' &middot; ') : '')
    + ' &middot; <a href="#" onclick="seatMenu();return false" style="color:var(--gold)">change</a>'
    + (s ? ' &middot; <a href="#" onclick="SEAT_ALL=!SEAT_ALL;i=0;render();return false" style="color:var(--gold)">'
           + (SEAT_ALL?'back to mine':'show all')+'</a>' : '')
    + '</div>';
}
/* Deliberately a prompt() and not a styled modal: this is set once per phone and then never
   touched, and a wizard for it would be more code to break than the feature itself. */
function seatMenu(){
  if(SEAT){ alert('This page is built for '+(SEAT.w||'seat '+(SEAT.i+1))+' (seat '+(SEAT.i+1)+' of '+SEAT.n
      +').\n\nThe split is fixed at build time — the other half lives on the other page.'); return; }
  var s=_seat()||{n:1,i:0,w:''};
  var n = parseInt(prompt('How many people are calling this list?\n\n1 = solo (you get everything)\n2 = you and one teammate\n3 or 4 also work.\n\nEVERY caller must enter the SAME number.', String(s.n)), 10);
  if(!n || n<1){ return; }
  if(n===1){ _seatSet(1); alert('Solo. You now get the whole list.'); return; }
  var idx = parseInt(prompt('Which seat is THIS phone?\n\nEnter 1 for the first caller, 2 for the second, and so on.\nEvery phone must pick a DIFFERENT number.', String((s.i||0)+1)), 10);
  if(!idx || idx<1 || idx>n){ alert('Seat must be between 1 and '+n+'.'); return; }
  var w = (prompt('Your first name (shows on your teammate\'s phone when you are working a lead):', s.w||'')||'').trim();
  _seatSet(n, idx-1, w);
  alert('Seat '+idx+' of '+n+'.\n\nYou now see about 1/'+n+' of the list and your teammate sees the rest — '
      + 'the same lead is never on both phones at once.\n\nUse "show all" if you finish early and want to work outside your lane.');
}
function errChip(){
  var n=0; try{ n=(JSON.parse(localStorage.getItem('fcErrLog')||'[]')).length; }catch(e){}
  return n ? (' &middot; <span class="errchip" onclick="showErrs()">'+n+' error'+(n===1?'':'s')+' logged &mdash; tap</span>') : '';
}
function showErrs(){
  var log=[]; try{ log=JSON.parse(localStorage.getItem('fcErrLog')||'[]'); }catch(e){}
  // alert() so it can be screenshotted whole, then offer to clear
  alert(log.map(function(e){ return e.t+' ['+e.w+'] '+e.m+'\n'+e.s; }).join('\n\n') || 'empty');
  if(confirm('Clear the error log?')){ try{ localStorage.removeItem('fcErrLog'); }catch(e){} render(); }
}
/* FTSA calling window, 8am-8pm EASTERN. WARN, never block — same call the board makes: a hard block
   would stop him documenting a callback the homeowner themselves asked for, and the statute governs
   solicitation hours, not every dial. The banner has to state the time THERE, not here. */
function flClock(){
  try{
    var p = new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'numeric',minute:'2-digit',hour12:true}).format(new Date());
    var h = +new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'numeric',hour12:false}).format(new Date());
    return {txt:p, ok:(h>=8 && h<20)};
  }catch(e){ return {txt:'', ok:true}; }
}
/* NOT CHECKED vs ZERO. Payoff is absent on 54% of leads, back taxes on 98%. A blank reads as
   "it is fine" and a $0 reads as "they owe nothing" - both are lies about data nobody gathered. */
function money(n){ return (n==null) ? '<span class="nc">not checked</span>' : '$'+Math.round(n).toLocaleString(); }
function kv(k,v,sm){ return '<div class="kv"><div class="k">'+k+'</div><div class="v'+(sm?' sm':'')+'">'+v+'</div></div>'; }
function has(r,ch){ return (r.f||'').indexOf(ch)>=0; }
function band(lbl,inner){ return '<div class="band"><div class="blab">'+lbl+'</div>'+inner+'</div>'; }

/* What we have already said to this person, from the SAME notes store the board writes.
   n.dials is the only thing separating "3 dials, no answer" from "never tried". */
/* "YOU HAVE ALREADY REACHED OUT TO THIS PERSON" — the thing a cooldown expiry must never hide.
   histLine below answers a different question (how many, what status) and lives in the FOURTH band,
   below the fold on a phone. This one is the FIRST thing in the card, above even the FTSA bar,
   because it changes the opening SENTENCE of the call, not merely the decision to make it.
   Deliberately NOT gated on the cooldown: the whole point is the lead that came back legitimately
   after its cooldown expired and now looks brand new. */
function priorBar(r){
  var lo = lastOutreach(r), best = null;
  for(var k in lo){ if(!best || lo[k].ts > best.ts) best = {ch:k, ts:lo[k].ts, by:lo[k].by}; }
  if(!best) return '';
  var chips = Object.keys(lo).sort(function(a,b){ return lo[b].ts - lo[a].ts; })
    .map(function(k){ return '<span class="chip bad">'+esc(k)+' &middot; '+esc(agoTxt(lo[k].ts))
        + (lo[k].by ? ' &middot; '+esc(lo[k].by) : '')+'</span>'; }).join('');
  return '<div class="warnbar">&#9888; <b>ALREADY CONTACTED</b> &mdash; '+esc(best.ch)+' '
       + esc(agoTxt(best.ts)) + (best.by ? ' by <b>'+esc(best.by)+'</b>' : '')
       + '. Not a fresh lead &mdash; open like someone you have already reached out to.'
       + '<div class="chips">'+chips+'</div></div>';
}
function histLine(r){
  var n=notes[r.c]||{}, ts=n.touches||[], dl=n.dials||[], out=[], byCh={};
  ts.forEach(function(t){ byCh[t.ch]=(byCh[t.ch]||0)+1; });
  ['call','text','email','letter','door'].forEach(function(ch){ if(byCh[ch]) out.push(byCh[ch]+'x '+ch); });
  if(dl.length) out.push(dl.length+' dial'+(dl.length>1?'s':''));
  if(n.status) out.push('status: '+esc(n.status));
  var last = ts.length ? ts[ts.length-1] : null;
  return '<div class="hist">'+(out.length ? ('Already: '+out.join(' &middot; ')+(last?(' &middot; last '+esc(last.d)):''))
                                          : 'No contact logged yet.')+'</div>';
}
/* WARM or COLD. A person with a prior real conversation — Talked, Appointment, Callback — on ANY of
   their cases gets the familiar opener ("Hector!"), not the stranger disarm. Reading the cold script
   to someone he spoke with yesterday reads as amnesia and burns the rapport the last call built. */
function isWarm(r){
  var warm = false;
  var chk = function(c){
    var n = notes[c]||{};
    if(/^(Contacted|Appointment|Callback)$/.test(n.status||'')) warm = true;
    (n.touches||[]).forEach(function(t){
      if(t.ch==='call' && /talked|appointment|callback/i.test(t.out||'')) warm = true;
    });
  };
  chk(r.c);
  (r.pcs||[]).forEach(chk);
  return warm;
}
/* THE SESSION STRIP — on every screen. "How many calls have I made, how many people have I been
   through" answered where he is looking, and the number MOVING on each logged outcome is the live
   proof the logging works. Today = from notes (survives reloads, includes what synced in);
   session = _WORKED (this open page). Person keys ('@'/'#') are ledger rows, not leads — skipped. */
function sessStrip(){
  var me = caller(), dials = 0, cases = {}, appts = 0, td = today();
  Object.keys(notes||{}).forEach(function(c){
    if(c.charAt(0)==='@' || c.charAt(0)==='#') return;
    var n = notes[c]||{}, hit = false;
    (n.dials||[]).forEach(function(x){
      if(x.d===td && (!me || !x.by || x.by===me)){ dials++; hit = true; }
    });
    (n.touches||[]).forEach(function(t){
      if(t.d===td && t.ch==='call' && (!me || !t.by || t.by===me)){
        hit = true;
        if(/APPOINTMENT/i.test(t.out||'')) appts++;
      }
    });
    if(hit) cases[c] = 1;
  });
  var ppl = Object.keys(cases).length;
  return '<div class="sess">TODAY: <b>'+dials+'</b> dial'+(dials===1?'':'s')+' &middot; <b>'+ppl+'</b> '
    + (ppl===1?'person':'people') + (appts?(' &middot; <b>'+appts+'</b> appt'+(appts===1?'':'s')):'')
    + ' &middot; this session: <b>'+_WORKED.length+'</b> worked</div>';
}

/* ===== THE CALL REGISTRY =========================================================================
   Every call this team has logged, newest first, across every device — because the touches live in
   notes and team sync merges teammates' notes into the same store. Durable by construction: it is
   the same data the queue reads to decide what to skip, so the log and the skipping can never
   disagree. Reachable from the hidden-count line in the top bar. */
function screenRegistry(){
  SCREEN='reg';
  var rows = [];
  Object.keys(notes || {}).forEach(function(c){
    var n = notes[c] || {};
    var lc = lastCall(n);
    if(!lc) return;
    var row = ROWS.filter(function(x){ return x.c === c; })[0] || null;
    rows.push({
      c: c, ts: lc.ts, by: lc.by, out: lc.out,
      who: (row && (row.o || row.f)) || (n.owner || ''),
      addr: (row && row.a) || '',
      n: (((n.dials||[]).length) || ((n.touches||[]).filter(function(t){return t.ch==='call';}).length) || 1),
      status: n.status || ''
    });
  });
  rows.sort(function(a,b){ return b.ts - a.ts; });
  var mine = 0, team = 0, me = caller();
  rows.forEach(function(x){ if(x.by && me && x.by !== me) team++; else mine++; });
  var list = rows.length ? rows.slice(0, 300).map(function(x){
    return '<div class="regrow">'
      + '<div class="regtop"><b>' + esc(x.who || x.c) + '</b>'
      + '<span class="regago">' + agoTxt(x.ts) + '</span></div>'
      + (x.addr ? '<div class="regsub">' + esc(x.addr) + '</div>' : '')
      + '<div class="regsub">' + esc(x.out || 'called')
      + (x.n > 1 ? ' &middot; ' + x.n + ' dials' : '')
      + (x.status ? ' &middot; ' + esc(x.status) : '')
      + '<span class="regby">' + esc(x.by || 'this phone') + '</span></div>'
      + '</div>';
  }).join('') : '<div class="regsub" style="padding:14px 2px">No calls logged yet. Every outcome you '
      + 'tap lands here, and so does every call your teammates log on their phones.</div>';
  $('app').innerHTML = head()
    + '<div class="card">'
    +   '<div class="band"><div class="bl">THE CALL LOG</div>'
    +     '<div class="regsum"><b>' + rows.length + '</b> people called &middot; '
    +       mine + ' by you &middot; ' + team + ' by teammates</div>'
    +     '<div class="regsub" style="margin-top:6px">Anyone on this list inside their cooldown is '
    +       'skipped in the queue &mdash; that is how two people never dial the same owner.</div>'
    +   '</div>'
    +   '<div class="reglist">' + list + '</div>'
    +   '<button class="big" id="regback" style="margin-top:14px">&larr; Back to the queue</button>'
    + '</div><div class="sheetpad"></div>';
  $('regback').onclick = function(){ SCREEN='lead'; render(); };
}
/* ===== WHO TEXTED ME? ===========================================================================
   An inbound text is the highest-value event in this business — a reply outranks every score and
   tier — and it arrives as an anonymous number. Before this, the only way to identify it was to
   scroll the 400-lead queue hoping to spot it, which could not work: only 1,202 of our 3,638
   numbers are on this page at all. PHIDX carries every lead that has a phone (inside the encrypted
   payload — these are phone numbers and the repo is public).                                   */
function digitsOf(s){ return String(s == null ? '' : s).replace(/\D/g, ''); }
function phLookup(q){
  var d = digitsOf(q);
  if(!PHIDX || !PHIDX.d || d.length < 4) return [];
  var t = PHIDX.t || [], map = PHIDX.d, hits = [], seenIdx = {};
  var push = function(num, idx){
    if(idx == null || seenIdx[idx + '|' + num]) return;
    seenIdx[idx + '|' + num] = 1;
    var row = t[idx] || [];
    hits.push({num:num, owner:row[0]||'', street:row[1]||'', c:row[2]||'',
               d:row[3], eq:row[4], fo:row[5], ct:row[6]});
  };
  var ten = d.slice(-10);
  if(d.length >= 10 && map[ten] != null){ push(ten, map[ten]); return hits; }
  // 4-9 digits: suffix match, because he often only has the tail of a number in front of him
  for(var k in map){ if(k.slice(-d.length) === d) push(k, map[k]); if(hits.length >= 12) break; }
  return hits;
}
/* What we already sent this person. He should never call back blind — three emails and a text in
   the last two weeks changes the opening line completely. Reads the same notes the board writes. */
function contactHistory(caseId){
  var n = notes[caseId] || {}, out = [];
  (n.doclog || []).forEach(function(e){
    out.push({t:e.ts || '', w:({'worker-email':'email sent','worker-text':'text sent',
      'worker-callq':'queued to call','worker-letter':'letter'}[e.key] || e.key || 'contact')});
  });
  (n.touches || []).forEach(function(e){
    out.push({t:e.ts || e.d || '', w:(e.ch || '') + (e.out ? ' — ' + e.out : '')});
  });
  out.sort(function(a,b){ return String(b.t).localeCompare(String(a.t)); });
  return out.slice(0, 6);
}
function screenLookup(prefill){
  SCREEN = 'lookup';
  try{ document.getElementById('sheet').classList.add('hid'); }catch(e){}
  $('app').innerHTML = '<div class="card">'
    + '<div class="addr" style="font-size:18px">&#128269; Who texted me?</div>'
    + '<div class="mut" style="font-size:12.5px;margin-top:4px">Paste the number, or just the last 4 digits.</div>'
    + '<input id="lkq" inputmode="tel" autocomplete="off" placeholder="305-801-1800  or  1800" '
    +   'style="width:100%;margin-top:12px;padding:14px;border-radius:12px;border:1px solid #2a3f6b;'
    +   'background:#0f1d3a;color:var(--ink);font-size:17px;-webkit-user-select:text;user-select:text">'
    + '<div id="lkout" style="margin-top:14px"></div>'
    + '<button id="lkback" class="ghost" style="margin-top:14px">&larr; Back to the queue</button>'
    + '</div><div class="sheetpad"></div>';
  $('lkback').onclick = function(){ SCREEN='lead'; render(); };
  var run = function(){
    var q = $('lkq').value, hits = phLookup(q), box = $('lkout');
    if(digitsOf(q).length < 4){ box.innerHTML = ''; return; }
    if(!hits.length){
      box.innerHTML = '<div class="nc">No lead in the database has this number.<br>'
        + 'It may be a wrong number, a new person, or someone we have not traced yet.</div>'
        + '<a class="flinks" style="display:block;margin-top:10px" href="https://www.truepeoplesearch.com/resultphone?phoneno='
        + esc(digitsOf(q).slice(-10)) + '" target="_blank" rel="noopener">'
        + '<span style="display:inline-flex;min-height:44px;align-items:center;padding:10px 14px;'
        + 'border-radius:10px;background:#12213f;border:1px solid #2a3f6b;color:var(--ink);font-weight:600">'
        + 'Reverse-search this number &rarr;</span></a>';
      return;
    }
    box.innerHTML = hits.map(function(h){
      var r = null;
      for(var j=0;j<ROWS.length;j++){ if(ROWS[j].c === h.c){ r = ROWS[j]; break; } }
      var hist = contactHistory(h.c);
      var money = (h.eq != null)
        ? ('equity <b style="color:' + (h.eq > 0 ? '#7ad48f' : '#ff8a80') + '">$'
           + Math.abs(Math.round(h.eq/1000)) + 'k' + (h.eq < 0 ? ' UNDERWATER' : '') + '</b>')
        : '';
      var clock = (h.d != null && h.d < 9000)
        ? (h.d < 0 ? '<b style="color:#ff8a80">sale PASSED</b>'
                   : '<b style="color:' + (h.d <= 7 ? '#ff8a80' : '#F4E5A7') + '">sale in ' + h.d + ' day' + (h.d===1?'':'s') + '</b>')
        : '<span class="mut">no auction date yet</span>';
      return '<div class="refbox" style="margin-top:10px">'
        + '<div class="ltag" style="margin:0 0 6px">' + fmt(h.num) + '</div>'
        + '<div style="font:800 16px \'Segoe UI\',Arial;color:var(--ink)">' + esc(h.owner || 'name unknown') + '</div>'
        + '<div style="margin-top:3px">' + esc(h.street) + '</div>'
        + '<div style="margin-top:6px">' + clock + (money ? ' &middot; ' + money : '') + '</div>'
        + (hist.length
            ? '<div class="ltag" style="margin:10px 0 4px">WHAT WE ALREADY SENT THEM</div>'
              + hist.map(function(e){ return '<div class="mut" style="font-size:12px">'
                  + esc(String(e.t).slice(0,16)) + ' &middot; ' + esc(e.w) + '</div>'; }).join('')
            : '<div class="mut" style="margin-top:8px;font-size:12px">no contact logged yet</div>')
        + '<div class="flinks" style="margin-top:10px">'
        +   '<a href="'+dialHref(esc(h.num))+'"'+dialTarget()+'>&#128222; Call back</a>'
        +   '<a href="sms:' + esc(h.num) + '">&#128172; Text</a>'
        +   fileLinks({fo:h.fo, ct:h.ct, o:h.owner, a:h.street, c:h.c}).map(function(x){
              return '<a href="' + esc(x[1]) + '" target="_blank" rel="noopener">' + esc(x[0]) + '</a>'; }).join('')
        + '</div>'
        + '<div class="brhost" data-c="' + esc(h.c) + '"></div>'
        + '<button class="lkrep" data-c="' + esc(h.c) + '" style="margin-top:10px;background:#286c34;'
        +   'border-color:#286c34;color:#fff">&#10003; They REPLIED — flag it</button>'
        + (r ? '<button class="lkgo" data-c="' + esc(h.c) + '" class="ghost" style="margin-top:8px">Open the full card &rarr;</button>' : '')
        + '<div class="fcase">case ' + esc(h.c) + '</div></div>';
    }).join('');
    /* Brief buttons per hit — use the FULL row when the lead is on this page (money, flags,
       plaintiff all present); fall back to the index's slice, whose brief states its unknowns. */
    Array.prototype.forEach.call(box.querySelectorAll('.brhost'), function(host){
      var c = host.dataset.c, full = null;
      for(var j=0;j<ROWS.length;j++){ if(ROWS[j].c === c){ full = ROWS[j]; break; } }
      var hh = null;
      hits.forEach(function(x){ if(x.c === c) hh = x; });
      var rr = full || {a: (hh && hh.street) || '', o: (hh && hh.owner) || '', c: c,
                        d: hh && hh.d, fo: hh && hh.fo, ct: hh && hh.ct,
                        p: hh ? [hh.num] : []};
      host.innerHTML = briefButtons(rr);
      wireBrief(host, rr);
    });
    /* DELIBERATE TAP, never automatic on lookup: a reply retires the cold ladder and changes the
       cadence, so merely looking someone up must not do it. */
    Array.prototype.forEach.call(box.querySelectorAll('.lkrep'), function(b){
      b.onclick = function(){
        var c = b.dataset.c, n = notes[c] = notes[c] || {status:'',note:''};
        n.replied = today(); n.status = n.status || 'Contacted';
        n.touches = n.touches || [];
        n.touches.push({d:today(), ts:nowTS(), tsu:Date.now(), ch:'text', out:'THEY REPLIED (inbound)'});
        touched = true; saveNotes(); queueSync();
        b.textContent = '✓ flagged as replied'; b.disabled = true;
        toast('Flagged — they jump the queue now');
      };
    });
    Array.prototype.forEach.call(box.querySelectorAll('.lkgo'), function(b){
      b.onclick = function(){
        var c = b.dataset.c;
        for(var j=0;j<ROWS.length;j++){ if(ROWS[j].c === c){ cur = ROWS[j]; phIdx = 0; SCREEN='lead'; return screenLead(); } }
        toast('That lead is not in this page’s call list');
      };
    });
  };
  $('lkq').oninput = run;
  if(prefill){ $('lkq').value = prefill; run(); }
  try{ $('lkq').focus(); }catch(e){}
}
/* ===== HIS FILE — the research links, built on the page ==========================================
   Call Mode shipped with ZERO links to a lead's records: standing on a call you had to leave the
   page and hunt the county sites by hand (2026-08-18 field report: "I have to struggle to find
   this guy"). Every URL below is DERIVED from two seed fields (folio + county) instead of shipping
   five long strings per lead — 400 rows x ~450 chars of URL is ~180 KB on a page that has to open
   on a phone at a door. Deep docket links can't be derived (the MD clerk uses an opaque token), so
   those go to the county's case-search page, which is one paste away. */
function fileLinks(r){
  // alphanumeric — Broward parcel ids contain letters (494213BA0140)
  var fo = String(r.fo || '').replace(/[^0-9A-Za-z]/g, '').toUpperCase();
  var ct = String(r.ct || 'MI').toUpperCase().slice(0, 2);
  var L = [];
  var dash = function(s, groups){                    // 40434504140060130 -> 40-43-45-04-14-006-0130
    var out = [], p = 0;
    for(var i = 0; i < groups.length && p < s.length; i++){ out.push(s.substr(p, groups[i])); p += groups[i]; }
    if(p < s.length) out.push(s.substr(p));
    return out.join('-');
  };
  if(fo){
    if(ct === 'PA'){                                  // PALM BEACH
      L.push(['Appraiser', 'https://pbcpao.gov/Property/Details?parcelId=' + fo]);
      L.push(['Taxes', 'https://pbctax.publicaccessnow.com/PropertyTax.aspx?s=ParcelID%3A'
        + encodeURIComponent(dash(fo, [2,2,2,2,2,3,4])) + '&pg=1&g=-1&moduleId=449']);
    } else if(ct === 'BR'){                           // BROWARD
      L.push(['Appraiser', 'https://bcpa.net/RecInfo.asp?URL_Folio=' + fo]);
      L.push(['Taxes', 'https://broward.county-taxes.com/public/real_estate/parcels/'
        + dash(fo, [6,2,4]) + '/bills']);
    } else {                                          // MIAMI-DADE
      L.push(['Appraiser', 'https://apps.miamidadepa.gov/PropertySearch/#/?folio=' + fo]);
      L.push(['Taxes', 'https://miamidade.county-taxes.com/public/real_estate/parcels/' + fo]);
    }
  } else {
    /* NO FOLIO RESOLVED. These two links used to simply VANISH — so on a folio-less lead (the
       'no cadastral match' class) he had no way to check taxes at all, mid-call, with nothing on
       screen explaining why. Fall back to the county's OWN address search, pre-filled where the
       site accepts it. Never a web search: the board's old Google punt is exactly the complaint
       that started this (2026-08-22). Street number + street only — a unit designator is the very
       thing the county roll disagrees with (court '#107' vs county '#L7'), so including one turns
       a working search into zero results. */
    var st = String(r.a || '').split(',')[0]
               .replace(/\s+(?:APT|UNIT|STE|#)\s*[\w-]+\s*$/i, '').trim();
    var qs = encodeURIComponent(st);
    if(ct === 'PA'){
      L.push(['Appraiser (search)', 'https://pbcpao.gov/']);
      L.push(['Taxes (search)', 'https://pbctax.publicaccessnow.com/PropertyTax.aspx?s=' + qs]);
    } else if(ct === 'BR'){
      L.push(['Appraiser (search)', 'https://web.bcpa.net/BcpaClient/#/Record-Search']);
      L.push(['Taxes (search)', 'https://broward.county-taxes.com/public/search?search_query=' + qs]);
    } else {
      L.push(['Appraiser (search)', 'https://apps.miamidadepa.gov/propertysearch/#/?address=' + qs]);
      L.push(['Taxes (search)', 'https://miamidade.county-taxes.com/public/search?search_query=' + qs]);
    }
  }
  L.push(['Court docket', ct === 'PA' ? 'https://appsgp.mypalmbeachclerk.com/eCaseView/'
        : ct === 'BR' ? 'https://www.browardclerk.org/Web2/CaseSearchECA/'
        : 'https://www2.miamidadeclerk.gov/ocs/']);
  var nm = String(r.o || '').replace(/[,&]/g, ' ').replace(/\s+/g, ' ').trim();
  if(nm) L.push(['People search', 'https://www.truepeoplesearch.com/results?name='
        + encodeURIComponent(nm) + (r.z ? '&citystatezip=' + encodeURIComponent(r.z) : '')]);
  /* WHITEPAGES. Call Mode shipped with none — the board has three Whitepages links on every row
     and the phone had zero, so standing on a doorstep the reverse-ADDRESS page (the one whose FREE
     tier prints the household's shared landline unmasked, plus residents and relatives by name and
     age) was unreachable. Same two URL shapes the board builds in _wpSlug/_wpAddrUrl/_wpNameUrl,
     derived here from the seed fields the row already carries (r.a, r.o) rather than shipping two
     more long strings per lead. The /property/{id} deep-link is deliberately NOT here: the id is
     per-lead, so it would be exactly the per-row string this band exists to avoid. */
  var wpSlug = function(s){ return String(s || '').replace(/[^A-Za-z0-9 ]/g, ' ').trim().replace(/\s+/g, '-'); };
  var ap = String(r.a || '').split(',');
  var wpCity = ap.length >= 2 ? wpSlug(ap[1]) : '';
  if(ap[0] && wpCity)
    L.push(['Whitepages addr', 'https://www.whitepages.com/address/' + wpSlug(ap[0]) + '/' + wpCity + '-FL']);
  if(nm && wpSlug(nm))
    L.push(['Whitepages', 'https://www.whitepages.com/name/' + wpSlug(nm) + '/' + (wpCity ? wpCity + '-FL' : 'FL')]);
  if(r.a) L.push(['Map', 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(r.a)]);
  return L;
}
function fileBand(r){
  var L = fileLinks(r);
  if(!L.length) return '';
  return '<div class="band"><div class="bl">HIS FILE</div><div class="flinks">'
    + L.map(function(x){
        return '<a href="' + esc(x[1]) + '" target="_blank" rel="noopener">' + esc(x[0]) + '</a>';
      }).join('')
    + '</div>'
    + briefButtons(r)
    + '<div class="fcase">case ' + esc(r.c || '') + (r.fo ? ' &middot; folio ' + esc(r.fo) : '')
    + '</div></div>';
}
/* ===== BRIEF JESSE — one tap mid-call ============================================================
   The live-call flow: owner on the line, Jesse joining, and he fires his standard questions —
   plaintiff? bank or HOA? what's owed? sale when? listed? — while Alejandro scrambles. Every answer
   is ALREADY on the row; this assembles them IN JESSE'S ORDER and hands them off. mailto: keeps a
   human on the send button (a page can't verify delivery, so it must not pretend to send).
   Recipient is HARDCODED — this can never mail an owner. */
var JESSE = 'celusa13@gmail.com';
function advisorBrief(r){
  var L = [];
  var money = function(v){ v = +v || 0;
    return v >= 1e6 ? '$' + (v/1e6).toFixed(2) + 'M' : '$' + Math.round(v).toLocaleString(); };
  var fl = String(r.f || '');
  L.push('LIVE CALL BRIEF — ' + (r.a || 'address unknown'));
  L.push('Owner: ' + (r.o || 'unknown')
    + (r.hs ? ' — lives there (homestead)' : (r.ab ? ' — ABSENTEE (mails elsewhere)' : ''))
    + (fl.indexOf('C') >= 0 ? ' [company/trust]' : ''));
  var hasSale = (r.d != null && r.d < 9000);
  L.push('Sale: ' + (!hasSale
      ? ((r.x || 'n/a') + ' filed — NO auction date yet (months of runway)')
      : (r.d < 0 ? (r.x + ' — PASSED ' + (-r.d) + 'd ago (surplus talk only)')
                 : (r.x + ' — in ' + r.d + ' day' + (r.d === 1 ? '' : 's'))))
    + (r.sv ? ' | survived ' + r.sv + ' prior sale date' + (r.sv === 1 ? '' : 's') + ' (staller)' : ''));
  /* ja = TOTAL interest accrued (FS 55.03), not per-day — and a payoff already CONTAINS it, so
     it only prints beside a bare judgment (first draft said "+$X/day": a 384%/yr absurdity). */
  var owed = (r.py || r.jg);
  L.push('Money: value ' + (r.v ? money(r.v) + ' (county)' : 'UNKNOWN')
    + ' | owed ' + (owed ? money(owed)
                            + (r.py ? ' payoff (incl. interest' + (r.jd ? ', as of ' + r.jd : '') + ')'
                                    : ' judgment' + (r.jd ? ' (' + r.jd + ')' : '')
                                      + (r.ja ? ' + ' + money(r.ja) + ' interest accrued' : ''))
                  : (fl.indexOf('J') >= 0 ? 'NOT POSTED yet' : 'unknown')));
  if(r.v && owed){
    var eq = r.v - owed;
    L.push('  -> equity ~' + money(Math.abs(eq)) + (eq < 0 ? ' UNDERWATER' : '')
      + (fl.indexOf('E') >= 0 ? ' (gross upper bound — liens unverified)' : ''));
  }
  L.push('Foreclosing: ' + (r.pl || 'plaintiff not on file') + (r.ft ? ' [' + r.ft + ']' : ''));
  if(fl.indexOf('H') >= 0) L.push('HOA co-defendant: YES — check for a second case');
  if(fl.indexOf('S') >= 0) L.push('SECOND CASE EXISTS on this person — pull it before advising');
  if(fl.indexOf('M') >= 0) L.push('A FIRST MORTGAGE SURVIVES this sale');
  if(r.ss) L.push('Surviving senior lien: ' + money(r.ss));
  if(fl.indexOf('T') >= 0) L.push('Tax certificate sold — second clock running');
  if(r.td) L.push('Back taxes due: ' + money(r.td));
  L.push(r.zs ? ('Listing: ' + r.zs + (r.zap ? ' — agent ' + (r.zag || '') + ' ' + fmt(r.zap) : (r.zag ? ' — agent ' + r.zag : '')))
              : 'Listing: not listed');
  if(fl.indexOf('D') >= 0) L.push('Condo — estoppel letter needed');
  L.push('Case ' + (r.c || '?') + (r.ct ? ' (' + (r.ct === 'BR' ? 'BROWARD' : r.ct === 'PA' ? 'PALM BEACH' : 'MIAMI-DADE') + ')' : '')
    + (r.fo ? ' | folio ' + r.fo : ''));
  fileLinks(r).forEach(function(x){ if(x[0] !== 'Map' && x[0] !== 'People search') L.push(x[0] + ': ' + x[1]); });
  if(r.p && r.p.length) L.push('Phones: ' + r.p.map(fmt).join(' | '));
  return L.join('\n').slice(0, 1500);
}
function briefButtons(r){
  return '<div class="flinks" style="margin-top:10px">'
    + '<a href="#" class="briefjesse" style="background:#3d2c08;border-color:#A8720C;color:#F6E9C8;font-weight:800">&#9889; Brief Jesse</a>'
    + '<a href="#" class="briefcopy">&#128203; Copy brief</a></div>';
}
function wireBrief(root, r){
  var mark = function(){
    var n = notes[r.c] = notes[r.c] || {status:'',note:''};
    n.doclog = n.doclog || [];
    n.doclog.push({ts: nowTS(), tsu: Date.now(), key: 'advisor-brief', src: 'call-mode'});
    touched = true; saveNotes(); queueSync();
  };
  Array.prototype.forEach.call(root.querySelectorAll('.briefjesse'), function(b){
    b.onclick = function(ev){
      ev.preventDefault();
      var body = advisorBrief(r);
      var url = 'mailto:' + JESSE + '?subject=' + encodeURIComponent((r.a || r.c || 'lead') + ' — live call brief')
              + '&body=' + encodeURIComponent(body);
      var a = document.createElement('a');
      a.href = url; a.style.display = 'none';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      mark(); toast('Gmail opened — hit Send and keep talking');
    };
  });
  Array.prototype.forEach.call(root.querySelectorAll('.briefcopy'), function(b){
    b.onclick = function(ev){
      ev.preventDefault();
      var body = advisorBrief(r);
      var done = function(){ mark(); toast('Brief copied — paste to Jose'); };
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(body).then(done, function(){ prompt('Copy this:', body); done(); });
      } else { prompt('Copy this:', body); done(); }
    };
  });
}
/* BALLOON CARD — the same four bands as a homeowner card, different facts: the clock is the note's
   est. maturity, the money is the loan + LTV verdict, "who is foreclosing" becomes THE NOTE. Kept in
   its own builder so the homeowner branches in screenLead stay untouched. */
function _balCard(r){
  var B = r.bal||{};
  var mny_ = function(n){ return (n==null||n==='') ? '<span class="nc">not known</span>' : ('$'+Math.round(+n).toLocaleString()); };
  var dd = (B.days==null) ? null : +B.days;
  var when = ((dd==null) ? 'note maturity not estimated'
           : (dd<0 ? ('note PAST its 24-month wall by '+(-dd)+' days') : (dd===0 ? 'note matures TODAY' : ('note matures in '+dd+' days'))))
           + (B.maturity ? (' &middot; est. '+esc(B.maturity)) : '');
  var who = '<div class="addr">'+esc(r.a||'(property address not resolved)')+'</div>'
          + '<div class="own">'+esc(r.on||'(no officer on file — ask for whoever handles the financing)')
          + (r.o ? (' <span class="mut">&middot; '+esc(r.o)+'</span>') : '')+'</div>'
          + '<div class="chips"><span class="chip">investor &middot; LLC borrower</span>'
          + (B.fits ? ('<span class="chip ok">FITS &middot; '+esc(B.verdict||'DSCR')+'</span>') : (B.verdict ? ('<span class="chip">'+esc(B.verdict)+'</span>') : ''))
          + '</div>';
  var clock = '<div class="when">'+when+'</div><div class="chips">'
            + (B.age_mo!=null ? ('<span class="chip">'+B.age_mo+' months since origin'+(B.origin?(' &middot; '+esc(B.origin)):'')+'</span>') : '')
            + '<span class="chip">24-mo balloon is a PROXY &mdash; ask the real maturity</span>'
            + ((dd!=null && dd<=30) ? '<span class="chip hot">extend-or-refi decision is NOW</span>' : '')
            + '</div>';
  var gap = B.amt ? Math.round((+B.amt)*(0.12-0.07)/12/50)*50 : 0;
  var mny = '<div class="grid">'
    + kv('Recorded note', mny_(B.amt))
    + kv('Lender', B.lender ? esc(B.lender) : '<span class="nc">unknown</span>', 1)
    + kv('Property value', mny_(r.v))
    + kv('LTV', B.ltv ? (Math.round(B.ltv)+'%') : '<span class="nc">not priced</span>')
    + kv('12% vs 7%, per month', gap ? ('~$'+gap.toLocaleString()) : '<span class="nc">n/a</span>')
    + '</div>'
    + (B.why ? ('<div class="chips"><span class="chip">'+esc(B.why)+'</span></div>') : '');
  var whoFc = '<div class="grid">'
    + kv('Program', 'DSCR 6&ndash;8% &middot; 70&ndash;80 LTV &middot; ~2 wk close', 1)
    + kv('Or', 'Hard equity 60&ndash;70 LTV &middot; 5&ndash;10 business days', 1)
    + '</div>';
  return {when:when, who:who, clock:clock, mny:mny, whoFc:whoFc};
}
function screenLead(){
  SCREEN='lead';
  document.getElementById('sheet').classList.remove('hid');   // the script belongs to the call screen
  var r=cur, d=r.p[phIdx], rk=r.r[phIdx]||'';
  /* Teammate already called this lead and the operator has not chosen to stay: take over the
     paint entirely. Covers every arrival path — queue advance on a stale pool, the lookup jump,
     the back path from a text panel — because they all end here. */
  if(!r._rcStay && _teammateCall(r)){ setTimeout(teamRecheck, 0); }
  var when = r.lp ? ('lis pendens filed '+esc(r.x||''))
                  : ((r.d===0?'auction TODAY':(r.d===1?'auction TOMORROW':'auction in '+r.d+' days'))+(r.x?' &middot; '+esc(r.x):''));

  var who = '<div class="addr">'+esc(propertyLabel(r))+'</div>'
          + '<div class="own">'+esc(ownerLabel(r))+'</div>';
  if(!r.a && r.ag) who += '<div class="warnbar">Verify this candidate against the case record before quoting it.'
    + (r.aw ? ' ' + esc(r.aw) : '') + '</div>';
  /* LAST QUO CALL -- what happened last time, in front of him BEFORE he redials. Summary from
     Quo's AI, flags from quo_sync's coach pass. Flags render red because every one of them is a
     sentence that must not be said again on the call he is about to make. */
  if(r.qc){
    who += '<div style="margin-top:8px;padding:8px 10px;border:1px solid #2a3f6b;border-radius:10px;background:#0f1d3a">'
        +  '<div class="ltag">LAST CALL &middot; '+esc(r.qc.w||'')+' &middot; '+(r.qc.du||0)+'s</div>'
        +  (r.qc.s ? '<div class="mut" style="font-size:13px;margin-top:3px">'+esc(r.qc.s)+'</div>' : '')
        +  ((r.qc.fl||[]).map(function(f){
             return '<div style="color:#e07b6a;font-size:12.5px;margin-top:3px">&#9873; '+esc(f)+'</div>';
           }).join(''))
        +  '</div>';
  }
  var wc='';
  if(r.ab) wc += '<span class="chip">absentee &middot; call, do not knock</span>';
  if(r.hs) wc += '<span class="chip ok">homestead &middot; they live there</span>';
  if(has(r,'C')) wc += '<span class="chip">company owner &middot; ask for the manager</span>';
  if(has(r,'W')) wc += '<span class="chip">elder signal</span>';
  if(wc) who += '<div class="chips">'+wc+'</div>';
  if(r.po) who += '<div class="warnbar">Roll owner is now <b>'+esc(r.po)+'</b> &mdash; do NOT open with the name above. Ask who you are speaking with.</div>';
  /* DIALED TODAY, NO OUTCOME. The one hole cooldown suppression cannot cover: a dial with no logged
     outcome starts no cooldown, so the lead resurfaces everywhere — which is exactly "why am I
     calling the same people". Say it on the card instead of letting it happen silently. */
  var _n0 = notes[r.c]||{};
  var _dT = (_n0.dials||[]).filter(function(x){ return x.d===today(); }).length;
  var _oT = (_n0.touches||[]).some(function(t){ return t.d===today() && t.ch==='call'; });
  if(_dT && !_oT) who += '<div class="warnbar">You DIALED this lead today ('+_dT+'x) but no outcome '
    + 'was logged &mdash; log one or this lead keeps coming back in every lane.</div>';

  var clock = '<div class="when">'+when+'</div><div class="chips">';
  /* THE SAME CASE IS ON THE CALENDAR TWICE, and the copy we dropped sells SOONER than the one on
     this card. call_rows keeps the fullest row (the newer posting is usually un-enriched: no
     address, no folio) and stamps the earlier date here rather than discarding it. FIRST chip,
     red, because "your sale is 39 days out" when it is really 17 is the one thing said on these
     calls that a homeowner cannot recover from. Verify against the clerk before quoting a date. */
  if(r.dupd) clock += '<span class="chip bad">ALSO CALENDARED ' + esc(r.dupd)
    + ' &mdash; SOONER than above. Verify the sale date before you quote it.</span>';
  if(r.sv!=null && r.sv>=2) clock += '<span class="chip bad">STALLER &middot; dodged '+r.sv+' sales</span>';
  else if(r.sv===0)         clock += '<span class="chip ok">FRESH &middot; first sale</span>';
  /* Sales SCHEDULED, the counterpart to sales survived. `sc` shipped on every lead and nothing read
     it. Only shown when it exceeds `sv`, because then it means a sale is on the calendar that has
     not happened yet — which is the difference between "they have dodged three" and "they have
     dodged three and a fourth is booked". Equal values would just restate the chip above. */
  if(r.sc!=null && r.sv!=null && r.sc>r.sv)
                     clock += '<span class="chip">'+r.sc+' sales scheduled, '+(r.sc-r.sv)+' still ahead</span>';
  if(r.sw==='bank')  clock += '<span class="chip">bank keeps postponing</span>';
  if(r.sw==='owner') clock += '<span class="chip">owner fights</span>';
  if(r.bk)           clock += '<span class="chip bad">'+r.bk+' bankruptcy filing'+(r.bk>1?'s':'')+'</span>';
  if(r.sl)           clock += '<span class="chip hot">stay LIFTED '+esc(r.sl)+'</span>';
  if(r.cs)           clock += '<span class="chip">case '+esc(r.cs)+'</span>';
  if(r.sr){ var _sm=function(i){var m=String(i||'').match(/^\d{4}-(\d{2})-(\d{2})$/);return m?m[1]+'/'+m[2]:'';};
    var _sl={held:'SOLD '+_sm(r.sr.d), cancelled:'sale CANCELLED '+_sm(r.sr.d), reset:'sale MOVED to '+_sm(r.sr.nd)+(r.sr.was?' (was '+_sm(r.sr.was)+')':''),
             vacated:'sale SET ASIDE', redeemed:'REDEEMED after sale', at_risk:'sale AT RISK', unknown:'result not on docket yet'}[r.sr.st]||'';
    if(_sl) clock += '<span class="chip '+((r.sr.st==='cancelled'||r.sr.st==='reset')?'hot':(r.sr.st==='held'?'bad':''))+'" title="'+esc((r.sr.why||'')+((r.sr.ev&&r.sr.ev.length)?' | docket: '+r.sr.ev.map(function(e){return _sm(e.d)+' '+e.x;}).join(' | '):''))+'">'+esc(_sl)+'</span>';
    if(r.sr.bkb && r.sr.st==='held') clock += '<span class="chip bad">BK filed '+esc(_sm(r.sr.bkb))+', sale may not stand</span>';
    if(r.sr.amj) clock += '<span class="chip">amended judgment '+esc(_sm(r.sr.amj))+(r.sr.ama?' $'+Math.round(r.sr.ama).toLocaleString():'')+'</span>'; }
  clock += '</div>';

  var mny = '<div class="grid">'
    + kv('They owe (with interest)', money(r.py))
    + kv('Property value', money(r.v))
    + kv('Equity', r.e==null ? '<span class="nc">not known</span>' : (Math.round(r.e)+'%'+(has(r,'E')?' <span class="nc">gross</span>':'')
         // Say which kind of number this is, on the surface where it gets spoken out loud. A
         // traced chain is the difference between "you have equity" and "you might".
         + (r.eqv ? ' <span class="ok">VERIFIED</span>' : ' <span class="nc">unverified — chain not traced</span>')))
    + kv('Surviving 1st', r.ss==null ? '<span class="nc">not checked</span>' : (r.ss===0?'none':money(r.ss)), 1)
    + '</div>';
  var mc='';
  if(r.py!=null && r.ja) mc += '<span class="chip">includes $'+Math.round(r.ja).toLocaleString()+' interest'+(r.jd?(' since '+esc(r.jd)):'')+'</span>';
  if(r.py==null && r.jg!=null) mc += '<span class="chip">judgment as entered $'+Math.round(r.jg).toLocaleString()+'</span>';
  if(has(r,'J')) mc += '<span class="chip bad">judgment not posted</span>';
  if(has(r,'M')) mc += '<span class="chip bad">a 1st mortgage SURVIVES this sale</span>';
  if(r.td)       mc += '<span class="chip bad">back taxes $'+Math.round(r.td).toLocaleString()+'</span>';
  /* The ESTIMATE, only when the real delinquent balance is unknown — which is 98% of leads, where
     the taxes line was simply blank. `et` was serialized on every lead and read by nothing.
     Labelled "est. annual" and never "owed": it is a millage estimate of the yearly bill, not a
     delinquent balance, and presenting it as debt would be the same lie money() exists to prevent. */
  else if(r.et)  mc += '<span class="chip">est. annual tax $'+Math.round(r.et).toLocaleString()
                     + (has(r,'e')?' (estimate)':'')+'</span>';
  if(has(r,'T')) mc += '<span class="chip bad">tax certificate sold</span>';
  if(r.arv)      mc += '<span class="chip">ARV $'+Math.round(r.arv).toLocaleString()+(r.an?(' &middot; '+r.an+' comps'):'')+'</span>';
  /* orconf is the CONFIDENCE of the lien-chain SEARCH, not a statement about liens. Rendering it raw
     printed "lien chain: none" — which mid-call reads as "there are no liens", the opposite of what
     it means ("the chain was never resolved"), on the screen he uses to decide what to tell someone
     about their equity. 'bd' rendered as "lien chain: bd", pure jargon. Same rule as money(): a
     not-checked state must announce itself, never pose as a clean result. */
  var _OCONF = {ok:  ['',    'lien chain verified'],
                low: ['bad', 'lien chain LOW confidence &mdash; common name, may include a stranger'],
                bd:  ['',    'lien chain from property data, not the records'],
                none:['bad', 'lien chain NOT RESOLVED &mdash; do not treat it as clear']};
  var _oc = r.oc ? (_OCONF[r.oc] || ['', 'lien chain: '+esc(r.oc)]) : null;
  if(_oc)        mc += '<span class="chip '+_oc[0]+'">'+_oc[1]+'</span>';
  if(mc) mny += '<div class="chips">'+mc+'</div>';

  var whoFc = '<div class="grid">'
    + kv('Plaintiff', r.pl ? esc(r.pl) : '<span class="nc">not resolved</span>', 1)
    + kv('Type', r.ft ? esc(r.ft) : '<span class="nc">unknown</span>', 1)
    + (r.dk ? kv('Court status', '<b>' + esc(r.dk.s || '?') + '</b>'
        + (r.dk.t ? ' &middot; ' + esc(r.dk.t) : '')
        + (r.dk.f ? ' &middot; filed ' + esc(r.dk.f) : ''), 1) : '')
    /* Jesse's first question on a 3-day lead: does the owner have a lawyer? The docket parties say. */
    + (r.dk ? kv('Owner’s attorney', r.dk.a ? '<b>'+esc(r.dk.a)+'</b>' : '<span class="nc">none of record</span>', 1) : '')
    + '</div>';
  /* LIVE DOCKET on the handset (gen_dockets.py -> row.dk). The clerk's site cannot be deep-linked
     to a case from a URL, so the filings RIDE WITH THE ROW instead of being a link the caller has
     to go retype a case number into mid-dial. Newest first: the last thing that happened is what
     decides the call — a stay motion, a certificate of title, a dismissal. Collapsed by default so
     it never pushes the dial buttons off the first screen. */
  if(r.dk && (r.dk.e || []).length){
    var _de = r.dk.e.slice().reverse();
    whoFc += '<details style="margin-top:6px"><summary style="cursor:pointer;font-size:12px;opacity:.85">'
      + 'Docket &mdash; newest ' + _de.length + ' of ' + esc(String(r.dk.n || _de.length)) + ' filings</summary>'
      + '<div style="margin-top:4px;max-height:220px;overflow:auto">'
      + _de.map(function(e){
          return '<div style="font-size:11px;padding:3px 0;border-top:1px solid rgba(255,255,255,.08)">'
               + '<b>' + esc(e.d || '') + '</b> ' + esc(e.x || '') + '</div>';
        }).join('')
      + '</div></details>';
  }
  var fc='';
  if(has(r,'S')) fc += '<span class="chip bad">SECOND CASE on this property</span>';
  if(has(r,'H')) fc += '<span class="chip">open HOA lien</span>';
  if(has(r,'X')) fc += '<span class="chip">code enforcement</span>';
  if(has(r,'I')) fc += '<span class="chip">individual plaintiff</span>';
  if(has(r,'D')) fc += '<span class="chip">condo &middot; estoppel</span>';
  if(fc) whoFc += '<div class="chips">'+fc+'</div>';

  /* MISCALCULATED DEAL banner (8/17 masterclass): actively listed with the sale weeks out — the
     agent is burning the client's clock protecting a fantasy price. This call may be a GATEKEEPER
     call: drill card 12 (same property pays the agent twice). Agent's number dials from here. */
  var mlBan = '';
  if(r.ml){
    mlBan = '<div style="margin:8px 0;padding:9px 12px;border-radius:8px;background:#3d2c08;border:1px solid #A8720C;color:#F6E9C8;font:700 12.5px/1.5 -apple-system,Segoe UI,Arial">'
      + '🏷 LISTED with the sale '+(r.d!=null?r.d+'d':'weeks')+' out — MISCALCULATED DEAL. '
      + 'Gatekeeper play: <b>card 12</b> — full commission on the buy + the re-listing. Same property pays the agent twice.'
      + (r.zag ? '<br>Agent: <b>'+esc(r.zag)+'</b>' : '')
      + (r.zap ? ' &middot; <a style="color:#F4E5A7;font-weight:800" href="'+dialHref(String(r.zap).replace(/^1/,''))+'"'+dialTarget()+'>&#128222; call the agent</a>' : '')
      + '</div>';
  }

  var prop = (r.dd||r.bd||r.sf||r.zs) ? ('<div class="hist">'+(r.dd?esc(r.dd):'')
      + (r.bd?(' &middot; '+r.bd+'bd'):'') + (r.ba?('/'+r.ba+'ba'):'')
      + (r.sf?(' &middot; '+Number(r.sf).toLocaleString()+' sqft'):'')
      + (r.zs?(' &middot; listed '+esc(r.zs)):'')+'</div>') : '';

  var fl = flClock();
  var ftsaBar = fl.ok ? '' :
    '<div class="warnbar">FL FTSA &mdash; solicitation calling hours are 8:00 AM to 8:00 PM Eastern. '
    + 'It is ' + esc(fl.txt) + ' there. Dialing anyway is your call; a callback they asked for is different from a cold dial.</div>';

  /* Balloon rows swap the four bands' CONTENT (see _balCard); layout, dial link, file band and the
     outcome flow are shared. Done last so nothing above has to know the lane exists. */
  var _isBal = (r.st==='BAL');
  if(_isBal){ var _B=_balCard(r); who=_B.who; clock=_B.clock; mny=_B.mny; whoFc=_B.whoFc; }
  $('app').innerHTML = head()
    + '<div class="card">'
    +   priorBar(r)
    +   ftsaBar
    +   mlBan
    +   band('WHO', who)
    +   band('THE CLOCK', clock)
    +   band('THE MONEY', mny)
    +   band(_isBal ? 'THE NOTE' : 'WHO IS FORECLOSING', whoFc + prop + histLine(r))
    +   fileBand(r)
    +   '<a class="dial" href="'+dialHref(d)+'"'+dialTarget()+' id="dial">'+fmt(d)+'</a>'
    +   '<div class="sub">number '+(phIdx+1)+' of '+r.p.length
    /* 'N' (not the owner) is new; an unmapped letter used to fall through to "last resort", which
       reads as a bad number rather than a different person. */
    +     (rk?(' &middot; '+(rk==='C'?'call first':rk==='O'?'ok':rk==='N'?'not the owner':'last resort')):'')
    /* WHOSE NUMBER THIS IS, above the fold and next to the dial button, because it changes the
       first sentence out of your mouth. The script on this page greets the owner by first name;
       on these two it is "is <owner> home?" instead. */
    +     (_phSrcNote(r, phIdx) ? '<br><b>'+_phSrcNote(r, phIdx)+'</b>' : '')
    +     (r.k?(' &middot; '+r.k+' withheld, do-not-call flag on file'):'')+'</div>'
    +   '<button class="big" id="skip" style="background:#2a3f6b">Skip</button>'
    + '</div>'
    + '<div class="sub">'+(i+1)+' of '+pool().length+' &middot; showing '+SHOWN+' of '+TOTAL+' that qualify</div>'
    + '<div class="sheetpad"></div>';
  // Render the outcome screen SYNCHRONOUSLY on tap and let the tel: navigation proceed. iOS
  // backgrounds the tab the instant the dialer opens; painting after would never happen.
  $('dial').addEventListener('click', function(){
    // A dial IS work in progress. `touched` used to be set only on the first LOGGED outcome, so a
    // deploy landing during the first call of a session made freshCheck location.reload() the page
    // he was mid-call on. Three deploys shipped today while he was dialing.
    touched = true;
    /* RECORD THE ATTEMPT AT DIAL TIME, not just on the tapped outcome (2026-09-08 field report:
       "me and my cousin keep getting people we already called"). A call he never tags an outcome
       for -- distracted, or iOS backgrounds the tab the instant the dialer opens on a 100-dial
       day -- used to write NOTHING: no touch, no dial row, no cooldown, no sync. So the lead stayed
       in pool() (suppressed() saw no lastCall) AND nothing pushed to the teammate, and the same
       person resurfaced on BOTH phones -- the double-dial this whole team layer exists to stop.
       A 'pending' dial gives lastCall()/suppressed()/teamRecheck() something to hold on (a short 6h
       cooldown, set ONLY when no real outcome cooldown already exists) and queueSync() pushes it to
       the cousin immediately. logOutcome() replaces this trailing 'pending' when the real outcome is
       tapped, so the dial-through count stays one row per attempt, never doubled. */
    try{
      var n = notes[r.c] = notes[r.c] || {status:'',note:''};
      n.dials = n.dials || [];
      n.dials.push({d:today(), ts:nowTS(), tsu:Date.now(), ph4:String(d).slice(-4), oc:'pending', by:caller()});
      /* 2026-09-10: this used to write n.cooldownH = 6. That field is the CONTACT cooldown and it
         SYNCS — it reached the board on a bare-date tie, where the clock never reads dials, so the
         6 suppressed nothing there and instead sat on the note permanently, rescaling the next
         real outcome. The hold is read-side now: suppressed() sees oc:'pending' and holds
         PENDING_HOLD_H. Same protection, nothing written, nothing propagated. */
      saveNotes(); queueSync();
    }catch(e){ try{ logErr(e,'dial-prelog'); }catch(_e){} }
    setTimeout(screenOutcome,0);
  });
  /* Skip was the last raw i++ in the file — the same bug class already fixed for advance(), and
     reachable without any teammate involvement: in the worker lane retireFromWorkerQ() shrinks the
     pool on the first logged number, so i++ from there lands one past the next person. Skipping is
     also the one action that must NOT count as work, so it does not touch _WORKED. */
  $('skip').onclick=function(){ advance(cur.c, null); };
  wireBrief(document, cur);
  wire();
  // Refresh the sheet for THIS lead. It is not re-created — it lives outside #app — so its
  // open/closed state and the chosen CIOC beat survive advancing to the next person.
  renderSheet(r);
}
/* ---- text ladder, person-keyed --------------------------------------------------------------
   Mirrors the board's _textStage. The cap is per HUMAN, not per case: three messages about property
   A plus three about property B is six messages to one phone. WITHIN a case we take max(local,
   ledger) because a send made in this browser is also in the next bake and summing would retire
   someone a stage early; ACROSS cases we sum, because those really are separate messages. */
var TEXT_MAX_TOTAL = 3;
/* Every case belonging to this person that this device can see.
   🔴 ROWS ALONE IS NOT ENOUGH. ROWS is the 400 DIALABLE leads of 783 qualifying, out of ~1,700 in
   the pipeline — so a person's other property, or a case whose auction already passed, is simply not
   in it. Counting texts over that subset lets a 4th message go to someone the ladder thinks is on
   their 2nd, which is the exact FTSA exposure the cap exists to prevent.
   `r.pcs` is the person's FULL case list from the build's own grouping, shipped inside the encrypted
   payload for exactly this lookup. (A previous version scanned notes for a `pkey` field instead —
   dead code: nothing ever writes pkey into a note, and the test that "proved" it passed on a
   hand-fabricated one. notes carries the TOUCHES for these cases; pcs carries WHICH cases.)
   TEXTPERSON (the server ledger) remains the authoritative backstop in textStage for cases that have
   left the pipeline entirely. */
function personCases(r){
  var k = r.pk, seen = {}, out = [];
  function add(c){ if(c && !seen[c]){ seen[c]=1; out.push(c); } }
  ROWS.forEach(function(x){ if(x.pk === k) add(x.c); });
  (r.pcs||[]).forEach(add);
  add(r.c);
  return out;
}
function textStage(r){
  var sends = 0, replied = false;
  personCases(r).forEach(function(c){
    var n = notes[c] || {}, local = 0;
    (n.touches||[]).forEach(function(t){
      if((t.ch||'') !== 'text') return;
      if(/replied|inbound|answered|said stop/i.test(t.out||'')){ replied = true; return; }
      local++;
    });
    sends += local;
  });
  var P = TEXTPERSON[r.pk] || 0;
  if(P > sends) sends = P;           // authoritative — includes cases that never shipped here
  if(replied) return 'replied';
  if(sends >= TEXT_MAX_TOTAL) return 'retired';
  return ['cold','follow','final'][sends] || 'retired';
}
/* ══════ THE TEXT BODIES — extracted VERBATIM too, same rule again ══════
   _textTone (which rung) + _textBodies (the words, EN and ES). This page used to hold a THIRD set
   of bodies: the board sent "A case was just filed at the courthouse on <street>" from the text
   batch while the phone sent its own after-call ladder to the same homeowner about the same case.
   Both scripts are still here on purpose — the after-call ladder opens with "I just tried calling",
   which is true only on this screen — but the BATCH script is now the board's own bytes rather than
   a copy of them. See call_mode.extract_text_js. */
__TEXTTPLJS__
/* ══════════════════════════════════════════════════════════════════════════════════════════ */

/* Bodies are the board's t1/t2/t3 shapes. Compliance rides along: identify, one ask, opt-out on
   every message (the FTSA 15-day cure safe harbor is worthless if the STOP line is missing).

   Written as TEMPLATES and run through fillScript rather than interpolated here. SENDER is an
   OBJECT — a raw `+ SENDER +` renders "this is [object Object] with Biscayne Solutions Group", which is
   the same failure as the {sender} bug that already reached a live read-aloud script. Going through
   fillScript also inherits the Jose heal and the company-name heal for free. */
/* 2026-09-04 (Alejandro): the ladder is 1:1 human texts now. No confirm-CTA and no opt-out line in
   the body -- his call as operator of record. Jeremy Miner / NEPQ voice: curiosity,
   self-qualify, ONE soft question that earns a reply on its own. Still identifies the sender and
   names the street; no em dashes (reads as AI in a text); 'keep my number' is drill card 14's
   read-back close at SMS size. Run through fillScript -- SENDER is an OBJECT, a raw `+ SENDER +`
   renders "[object Object]". Each body stays under two GSM segments. ES pending a language path. */
var TEXT_T = {
  cold:   'Hi{first}, it is {sender} with Biscayne Solutions Group. I just tried calling about {st1}. '
        + 'This might be off base so tell me if it is. I work with a few owners going through the same '
        + 'court filing you have, and there is one part of it almost nobody gets told. Can I ask you '
        + 'something about it?',
  follow: 'Hi{first}, {sender} again about {st1}. Not trying to crowd you. If you already have a plan '
        + 'you trust, honestly ignore me and I hope it works out. If you are not fully sure it will, '
        + 'that is the part I would want a second set of eyes on for you. Worth a quick look?',
  final:  'Hi{first}, last one from me, {sender} with Biscayne Solutions Group about {st1}. I really do '
        + 'hope your plan lands on time. If anything slips before the sale date, one quick call with '
        + 'our senior advisor lays out what still works. Either way, keep my number in your phone.'
};
/* ES ladder (2026-09-05) — his first client was a Spanish speaker; Miami runs bilingual. Usted
   register throughout, same NEPQ voice, no dashes, no confirm-CTA. Picked by the fcLang toggle
   (the same EN|ES chips as the call script). */
var TEXT_T_ES = {
  cold:   'Hola{first}, le habla {sender} de Biscayne Solutions Group. Acabo de intentar llamarle por '
        + '{st1}. Puede que esté equivocado, y si es así dígame. Trabajo con varios dueños que van por '
        + 'el mismo proceso en la corte, y hay una parte que a casi nadie le explican. ¿Le puedo hacer '
        + 'una pregunta sobre eso?',
  follow: 'Hola{first}, {sender} otra vez por {st1}. No quiero presionarlo. Si ya tiene un plan en el '
        + 'que confía, ignóreme y ojalá le funcione. Si no está cien por ciento seguro de que llega a '
        + 'tiempo, esa es la parte que yo quisiera revisarle. ¿Vale la pena una mirada rápida?',
  final:  'Hola{first}, último mensaje mío, {sender} de Biscayne Solutions Group por {st1}. De verdad '
        + 'espero que su plan salga a tiempo. Si algo se atrasa antes de la fecha de subasta, una '
        + 'llamada rápida con nuestro asesor principal le muestra lo que todavía funciona. De '
        + 'cualquier forma, guarde mi número.'
};
function textBody(r, stage){
  var T = (lang()==='es') ? TEXT_T_ES : TEXT_T;
  return fillScript(T[stage] || T.cold, r);
}
/* ── SITUATIONAL TEXTS (2026-09-05) — sequences beyond the cold ladder ──────────────────────────
   Appointment confirm/remind/no-show, post-conversation, callback-promised, and the self-serve
   booking link. These are RESPONSIVE messages to a person who just engaged on a call — they are
   offered by outcome on the after-call panel and still pass every hard gate (opt-out, DNT, FTSA
   hours). Voice rules unchanged: identify, one ask, no dashes, name situations never outcomes,
   "five minutes" is the advisor consult. {book} is the Cal.com link with their info prefilled. */
var TEXT_SIT = {
  apptconfirm:{ t:'Confirm appt',
    en:'Hi{first}, it is {sender} with Biscayne Solutions Group. Good talking with you. You are set, '
      +'our senior advisor will call you at this number. If your mortgage paperwork is nearby when he '
      +'calls, even better. Anything changes, text me here.',
    es:'Hola{first}, le habla {sender} de Biscayne Solutions Group. Un gusto hablar con usted. Ya '
      +'quedó, nuestro asesor principal lo llamará a este número. Si tiene su papeleo de la hipoteca '
      +'a la mano cuando llame, mejor. Cualquier cambio, escríbame aquí.'},
  booklink:{ t:'Send booking link',
    en:'Hi{first}, {sender} here. Easiest way, pick the time yourself and our senior advisor calls '
      +'you right at it: {book} Takes 20 seconds and your info is already filled in.',
    es:'Hola{first}, soy {sender}. Lo más fácil, escoja usted la hora y nuestro asesor principal lo '
      +'llama justo a esa hora: {book} Toma 20 segundos y sus datos ya van puestos.'},
  apptremind:{ t:'Appt reminder',
    en:'Hi{first}, {sender} here. Quick reminder on your call with our senior advisor. He set that '
      +'time aside just for your case on {st1}. If the time stopped working, tell me and we move it, '
      +'no problem.',
    es:'Hola{first}, soy {sender}. Un recordatorio de su llamada con nuestro asesor principal. Apartó '
      +'ese tiempo solo para su caso de {st1}. Si la hora ya no le sirve, dígame y la movemos sin '
      +'problema.'},
  noshow:{ t:'Missed appt',
    en:'Hi{first}, {sender} with Biscayne Solutions Group. We missed you for the advisor call. No '
      +'worries at all, life happens. He has openings today and tomorrow. Which works better for you?',
    es:'Hola{first}, {sender} de Biscayne Solutions Group. No pudimos conectarlo con el asesor. No se '
      +'preocupe, pasa. Tiene espacio hoy y mañana. ¿Cuál le queda mejor?'},
  aftertalk:{ t:'After the talk',
    en:'Hi{first}, {sender} here. Appreciated you being straight with me today. One thing to keep in '
      +'mind, the balance keeps growing every time that date moves. Whenever you want the real '
      +'numbers, five minutes with our senior advisor gets them. I am here.',
    es:'Hola{first}, soy {sender}. Le agradezco lo directo que fue hoy. Solo tenga presente que el '
      +'saldo sigue creciendo cada vez que esa fecha se mueve. Cuando quiera los números reales, '
      +'cinco minutos con nuestro asesor principal y los tiene. Aquí estoy.'},
  cbtext:{ t:'Callback promised',
    en:'Hi{first}, {sender} with Biscayne Solutions Group. You asked me to reach back out about '
      +'{st1}, so this is me keeping my word. What time works for you today?',
    es:'Hola{first}, {sender} de Biscayne Solutions Group. Me pidió que lo contactara de nuevo sobre '
      +'{st1}, y aquí estoy cumpliendo. ¿A qué hora le queda bien hoy?'}
};
/* The prefilled Cal.com link, SMS-sized: name + the number he actually dialled. The full notes
   payload (address, case, sale date) stays on HIS book-it button — a homeowner does not need their
   own case number in a link, and a shorter URL survives SMS truncation. */
function bookUrl(r){
  return BOOKURL + '?name=' + encodeURIComponent(String(r.on||firstName(r)||'').trim())
       + '&attendeePhoneNumber=' + encodeURIComponent('+1' + String(r.p[phIdx]||'').replace(/\D/g,'').slice(-10));
}
function sitBody(r, key){
  var s = TEXT_SIT[key]; if(!s) return '';
  var raw = (lang()==='es' && s.es) ? s.es : s.en;
  return fillScript(raw.split('{book}').join(bookUrl(r)), r);
}
/* ── THE BOARD'S BATCH SCRIPT, on the phone (2026-09-18, Alejandro) ─────────────────────────────
   Same words the Morning Worker's text batch sends, because they are the same bytes: _textBodies is
   extracted from tracker_template.html at build time (call_mode.extract_text_js), not copied. This
   function is only the ADAPTER — it resolves what the board resolves from a board row (r.owners,
   r.addr, r.days) out of a phone row (r.on, r.a, r.d) and hands it over.

   Differences from the board's own call, both deliberate:
     - ONE LANGUAGE, not both. The batch bakes EN + ES together because it cannot know which the
       owner reads. On the phone he has just spoken to them, and the EN|ES chips above this button
       already say which — sending both after a conversation reads like a blast.
     - The street falls back INSIDE _textBodies, which picks the fallback in the right language
       ('su propiedad', not 'your property' in a Spanish body). So pass '' and let it choose. */
function boardTextBody(r){
  var st1 = String((r && r.a) || '').split(',')[0].trim();
  var B = _textBodies({
    first:  firstName(r),
    sender: SENDER.name || '',
    stEN:   st1, stES: st1,
    td:     (r && r.st === 'TD'),
    lp:     (r && r.st === 'LP'),
    /* The rung comes from THIS device's stage count fed through the board's own rule, so touch 2
       gets the follow-up body and touch 3 gets the one that promises it is the last. */
    tone:   _textTone(textStage(r), (r && r.d), (r && r.st === 'LP'))
  });
  return (lang() === 'es') ? B.es : B.en;
}
/* The label on the send button, and the label written into the touch log. Each was built inline in
   two places (the initial render and the chip handler), and adding a third script to choose from is
   exactly when those two drift apart. One function, called from both. */
function txLabel(k, st){
  if(k === 'ladder') return st === 'cold' ? 'Send 1st text'
                          : st === 'follow' ? 'Send follow-up (2 of 3)' : 'Send final text (3 of 3)';
  if(k === 'batch')  return 'Send the board script';
  return 'Send: ' + ((TEXT_SIT[k] || {}).t || '');
}
function txKind(k, st){
  return k === 'ladder' ? st : k === 'batch' ? ('board script, ' + st) : k;
}
/* Callback. Writes n.next, the same field the board reads to re-surface a lead — so a promise made
   on the phone shows up on the laptop instead of living in his head. */
function setCallback(r, hours, label){
  var n = notes[r.c] = notes[r.c] || {status:'',note:''};
  var t = new Date(Date.now() + hours*3600*1000);
  /* LOCAL date parts, mirroring the board's _setNext. toISOString() is UTC, and Miami is UTC-4 —
     so any callback set between 8pm and midnight lands on TOMORROW's date, which is precisely the
     window he works. The board renders n.next as "due today"/"overdue Nd" against a local today(),
     so a UTC string is an off-by-one-day that only shows up in the evening. */
  n.next = t.getFullYear() + '-' + String(t.getMonth()+1).padStart(2,'0') + '-' + String(t.getDate()).padStart(2,'0');
  n.nextTs = t.getTime();
  /* NO SYNTHETIC TOUCH. A touch is a CONTACT record; scheduling a callback is not a contact, and the
     board has no 'note' channel anyway (call/door/email/letter/worker/text/surplus). Inventing one
     would inflate the very counts this system exists to make trustworthy — and `_lastTouchD` uses
     the last touch to break merge ties, so a fake one would let a reminder outrank a real outcome.
     The board itself sets follow-ups this way: _setNext writes n.next and pushes nothing.
     The human-readable trace goes in n.note, which is free text and is what n.note is for. */
  var line = today() + ' callback set ' + label;
  n.note = n.note ? (n.note.indexOf(line) >= 0 ? n.note : (n.note + '\n' + line)) : line;
  saveNotes(); queueSync();
  toast('Callback ' + label);
}

function screenOutcome(){
  SCREEN='outcome';
  /* THE SHEET STAYS. This screen is not "the logging screen" — it paints the instant he taps dial
     and is what he looks at for the WHOLE call, so hiding the script here stripped the dialogue
     from the exact minutes it exists for ("you forgot to put the whole dialogue up on that screen
     when I'm dialing" — correct, I had misread the workflow). The burial bug the hiding was meant
     to fix is already solved by the full-size sheetpad on every screen; collapsed, the sheet no
     longer steals taps. afterCall still hides it — there the call is genuinely over. */
  document.getElementById('sheet').classList.remove('hid');
  var r=cur, d=r.p[phIdx];
  // Captured BEFORE any outcome is written — see advance(). Once logOutcome runs, pool() may no
  // longer contain either this lead or the same neighbours, so there is nothing left to read it from.
  var nextC = (function(){ var P=pool(); for(var k=0;k<P.length;k++) if(P[k].c===r.c) return (P[k+1]||{}).c; return null; })();
  var btns=OUTCOMES.map(function(o){
    var cls=o.k==='dnc'?'dnc':(o.k==='wrong'||o.k==='notint')?'warn':'';
    return '<button class="'+cls+'" data-oc="'+o.k+'">'+esc(o.t)+'</button>';
  }).join('');
  // Use the resolved first name, not the raw roll string — `(r.o).split(' ')[0]` on the county's
  // "GORDON,STEVE" has no space to split on, so the question read "How did it go with GORDON,STEVE?"
  /* THE DIALOGUE, ON the screen he stares at for the whole call — not behind a tap. Same named/
     anonymous opener choice as the sheet (greeting a company or a name the card said not to use is
     worse than asking for the owner). EN with ES stacked, the board's proven pattern: in South
     Florida you do not know which language you need until they pick up. The full apparatus — CIOC,
     objections, MARS — stays one tap away in the sheet below. */
  var named=!!firstName(r);
  /* WARM vs COLD, picked off the lead's own history. His field style: a person he has talked to
     gets their NAME and energy ("Hector!"), never the stranger script. */
  var warm = named && isWarm(r);
  var _opE = warm ? SCRIPT.op.wen : (named ? SCRIPT.op.en : SCRIPT.op.aen);
  var _opS = warm ? SCRIPT.op.wes : (named ? SCRIPT.op.es : SCRIPT.op.aes);
  var talk = (SCRIPT.rec ? '<div class="nc" style="border-color:#e07b6a;color:#f2b8ad">'+esc(SCRIPT.rec)+'</div>' : '')
    + '<div class="ltag" style="margin-top:12px">WHEN THEY PICK UP '
    + (warm ? '<span style="color:#7ad48f">&middot; WARM &mdash; you two have talked</span> ' : '')
    + langChips()+'</div>'
    + say(_opE, _opS, r)
    + '<div class="mut" style="font-size:12px;margin-top:4px">Close with: <b>'
    + (lang()==='es' ? '&iquest;Verdad que s&iacute;?' : 'That&rsquo;s fair, right?')
    + '</b> &middot; CIOC + objections in the script drawer below.</div>';
  /* THE REFERENCE BLOCK. This screen is up for the WHOLE call and carried only a first name and
     the number he dialled — so mid-conversation he could not see the address, the owner, what is
     owed, or when the sale is, and had to leave the page to look it up (2026-08-18: "add all of
     the information about the address etc so i can have the reference"). Everything below is
     already on the row; none of it costs a byte more of payload. */
  var _money = function(v){
    v = +v || 0;
    return v >= 1e6 ? '$' + (v/1e6).toFixed(2) + 'M' : v ? '$' + Math.round(v/1000) + 'k' : '';
  };
  var refRows = '';
  var addRef = function(k, v){ if(v) refRows += '<tr><td class="rk">'+k+'</td><td>'+v+'</td></tr>'; };
  addRef('Property', esc(propertyLabel(r)));
  addRef('Owner', esc(ownerLabel(r)));
  if(r.v || r.jg || r.py){
    var eqv = (r.v && (r.py || r.jg)) ? (+r.v - (+r.py || +r.jg)) : 0;
    /* UNDERWATER MUST SHOW. Showing equity only when positive quietly hid the single fact that
       changes the whole call: BARBOSA BRADS is owed $537k on a $473k house with the sale TODAY —
       there is no equity to protect, so cash-for-keys and surplus are off the table and the
       honest conversation is a short sale or the bank's own workout. Silence there reads as
       "fine". */
    addRef('Money', (r.v ? 'worth <b>' + _money(r.v) + '</b>' : '')
      + ((r.py || r.jg) ? ' &middot; owed ' + _money(r.py || r.jg) : ' &middot; owed <b>not posted</b>')
      + (eqv > 0 ? ' &middot; equity <b style="color:#7ad48f">' + _money(eqv) + '</b>'
         : (eqv < 0 ? ' &middot; <b style="color:#ff8a80">UNDERWATER ' + _money(-eqv)
                      + '</b> <span class="mut">(no equity &mdash; short sale / lender workout, not cash-for-keys)</span>'
            : '')));
  }
  /* THE DATE LINE — three honest states, because r.x and r.d don't mean one thing. r.x is
     auction OR filed date; r.d uses 9999 as the NO-AUCTION sentinel. The first version tested
     only d>=0 and rendered "SALE 8/7/2026 in 9999 days" on AMLONG's LP — the FILED date wearing
     a SALE label with the sentinel as a countdown, live, mid-call (2026-08-19). Same class as
     the worker's old 9999 burn: a sentinel must never cross a rendering boundary unlabeled. */
  if(r.x){
    var hasSale = (r.d != null && r.d < 9000);   // 9999 = no-auction sentinel, never a countdown
    if(!hasSale){
      addRef('Filed', esc(r.x) + ' <span class="mut">&middot; no auction date yet &mdash; months of runway</span>');
    } else if(r.d < 0){
      addRef('SALE', esc(r.x) + ' <b style="color:#ff8a80">PASSED ' + (-r.d) + ' day' + (r.d===-1?'':'s')
        + ' ago</b> <span class="mut">(surplus talk only)</span>');
    } else {
      addRef('SALE', esc(r.x) + ' <b style="color:' + (r.d <= 7 ? '#ff8a80' : '#F4E5A7') + '">in '
        + r.d + ' day' + (r.d===1?'':'s') + '</b>');
    }
  }
  if(r.dd || r.bd || r.sf) addRef('Property type', esc([r.dd || '', (r.bd ? r.bd + 'BR' : ''),
      (r.ba ? r.ba + 'BA' : ''), (r.sf ? r.sf + ' sf' : '')].filter(Boolean).join(' &middot; ')));
  if(r.pl) addRef('Foreclosing', esc(r.pl));
  addRef('Case', esc(r.c || '') + (r.fo ? ' &middot; folio ' + esc(r.fo) : ''));
  if(r.p && r.p.length > 1) addRef('Their numbers',
    r.p.map(function(x, i){ return (i === phIdx ? '<b>' + fmt(x) + ' (dialing)</b>' : fmt(x)); }).join(' &middot; '));
  var refBlock = refRows
    ? '<div class="refbox"><div class="ltag" style="margin:0 0 6px">THE FILE &mdash; for reference on this call</div>'
      + '<table class="reft">' + refRows + '</table>'
      + '<div class="flinks" style="margin-top:9px">'
      + fileLinks(r).map(function(x){
          return '<a href="' + esc(x[1]) + '" target="_blank" rel="noopener">' + esc(x[0]) + '</a>'; }).join('')
      + '</div>'
      + briefButtons(r)
      + '</div>'
    : '';
  $('app').innerHTML='<div class="card"><div class="addr" style="font-size:18px">How did it go with '+esc(firstName(r)||'them')+'?</div>'
    +'<div class="own">'+fmt(d)+'</div>'
    +sessStrip()
    + refBlock
    /* REDIAL — same number, no outcome logged, place kept. For the dropped call, the accidental
       hang-up, the straight-to-voicemail retry. An ANCHOR, not a JS navigation: tel: via href is
       the proven path (the main dial button), and returning from the dialer lands right back on
       this screen because the SCREEN guard defers any sync repaint. */
    +'<a class="redial" href="'+dialHref(d)+'"'+dialTarget()+' id="redial">&#8635;&nbsp; Redial '+fmt(d)+'</a>'
    + talk
    +'<div class="oc" style="margin-top:10px">'+btns+'</div>'
    +'</div><div class="sheetpad"></div>';
  /* A redial IS a dial — record it, or the dial-through count undercounts the actual work (the
     exact logging gap this page exists to close). oc:'redial' marks it as outcome-pending; the
     outcome he eventually taps logs its own entry for that attempt. */
  wireBrief(document, r);
  $('redial').addEventListener('click', function(){
    var n=notes[r.c]=notes[r.c]||{status:'',note:''};
    n.dials=n.dials||[];
    n.dials.push({d:today(), ts:nowTS(), tsu:Date.now(), ph4:String(d).slice(-4), oc:'redial', by:caller()});
    touched=true; saveNotes(); queueSync();
  });
  Array.prototype.forEach.call(document.querySelectorAll('.oc button'), function(b){
    b.onclick=function(){
      Array.prototype.forEach.call(document.querySelectorAll('.oc button'),function(x){x.disabled=true;});
      /* EVERYTHING inside try/catch, and the catch RE-ENABLES the buttons. The handler's first act
         is disabling all seven buttons; any throw after that left a screen of dead buttons with no
         message — "sometimes those buttons do not work at all", reported from the field. A dead
         screen is now impossible: on any error the buttons come back, the error is toasted, and
         the details land in the error log (top bar) for a screenshot. */
      try{
      var o=OUTCOMES.filter(function(z){return z.k===b.dataset.oc;})[0];
      /* 2026-09-04: the voicemail script moved to the sheet ("IF NO ANSWER", renderSheet) so it is on
         screen DURING the call, when he reads it into the machine, not gated behind a post-call
         "Done reading" wall. "Left voicemail" now logs and advances exactly like No answer: no wall,
         no limbo of disabled buttons waiting on a second tap. */
      var go;
      if(o.k==='dnc' && !confirm('They asked to stop. This closes every channel, permanently, on every device. Continue?')){
        Array.prototype.forEach.call(document.querySelectorAll('.oc button'),function(x){x.disabled=false;}); return;
      }
      /* CONFIRM, BECAUSE THIS IS DESTRUCTIVE AND SITS IN THE TAP PATH. The worker's version of this
         control had no confirmation and fired 61 times in one day -- 20 inside a single minute --
         killing a live number whose owner texted in that evening asking to sell (badnum_audit.py).
         Nobody establishes a number is dead in three seconds, so make the tap deliberate. */
      if(o.k==='badnum' && !confirm('Mark ' + fmt(d) + ' as a dead number?\n\nIt stops being dialled on this phone and on the board. Their other numbers are unaffected.')){
        Array.prototype.forEach.call(document.querySelectorAll('.oc button'),function(x){x.disabled=false;}); return;
      }
      var _fresh = logOutcome(r,o,d);
      var _okmsg = _fresh ? ('✓ '+o.t+' — logged') : ('✓ dial counted — '+o.t+' already logged today');
      /* The shield arms INSIDE go() — at the actual screen swap — not at outcome-tap time. On the
         voicemail path the swap happens seconds later (the Done-reading button), and arming early
         both missed that swap and ate legitimate taps on the just-painted voicemail block. */
      var _shield = function(){ window._tapShieldUntil = Date.now() + 400; };
      if(o.k==='dnc'||o.k==='wrong'||o.k==='notint'){
        /* An outcome that ENDS the relationship gets no follow-up offer — showing a Text button
           after someone says do-not-contact is how a compliance breach happens by muscle memory. */
        go = function(){ _shield(); toast(_okmsg); advance(r.c,nextC); };
      } else {
        /* EVERY other outcome — including NO ANSWER and VOICEMAIL — lands on the after-call panel,
           which is where the follow-up text lives.
           THE BUG THIS FIXES (2026-08-18, reported from the field): no-answer/voicemail used to
           jump straight to the lead's NEXT number and skip afterCall entirely. Skiptrace returns
           3-4 numbers on most leads and no-answer is far and away the most common outcome, so the
           follow-up text was effectively unreachable — the one moment a text matters most (they
           just saw a missed call from you) was the one moment the button never appeared.
           Cycling numbers is not lost: afterCall now carries a "try the next number" button. */
        go = function(){ _shield(); toast(_okmsg); afterCall(r,o,nextC); };
      }
      // 2026-09-04: voicemail no longer gates behind a post-call "Done reading" wall. The script now
      // lives in the sheet ("IF NO ANSWER"), on screen DURING the call, so "Left voicemail" advances
      // straight to the after-call panel like No answer -- same follow-up-text moment, no extra tap.
      go();
      /* go() runs IMMEDIATELY now. The 650ms hold showed a screen of disabled buttons between every
         outcome and the next action — pure dead time, times a hundred dials a day. The toast (now
         z-60, above the sheet) is the confirmation, and it overlaps the next screen harmlessly. */
      }catch(err){
        Array.prototype.forEach.call(document.querySelectorAll('.oc button'),function(x){x.disabled=false;});
        logErr(err, 'outcome:'+(b.dataset.oc||''));
        toast('Error logging that — buttons re-enabled, try again');
      }
    };
  });
  wireLang($('app'));
}
/* MAKE THE CONFIRM TRUE. The do-not-contact dialog promises "this closes every channel, permanently,
   on every device" — and the code behind it wrote optout+status to ONE case and nothing else.
   The person said stop about THEMSELVES: their second property stayed dialable, their number stayed
   textable, and nothing suppressed a case keyed differently. A promise the code does not keep is
   worse than no promise, because he stops checking.
   Mirrors the board's _stopEverywhere (tracker_template.html:5666-5677): every case for the person,
   a '#digits' person-level key that optPhones()/suppressed() actually read back, and the number on
   the do-not-text list. Never gated on hours, cadence or retire state — "they told me to stop"
   outranks every other condition. */
function stopEverywhere(r, digits){
  var stamp = today(), cases = personCases(r);
  cases.forEach(function(c){
    var n = notes[c] = notes[c] || {status:'',note:''};
    n.optout = n.optout || stamp;
    n.status = 'DO NOT CONTACT';
    n.optlog = n.optlog || [];
    n.optlog.push({ts:nowTS(), tsu:Date.now(), act:'set-local', src:'call-mode'});
    // n.dntph is the field _mergeLead carries laptop->phone precisely so a do-not-text survives the
    // device. Union only — a suppression list may only ever grow.
    var d = n.dntph = n.dntph || [];
    (r.p||[]).forEach(function(p){ if(d.indexOf(p) < 0) d.push(p); });
    if(digits && d.indexOf(String(digits)) < 0) d.push(String(digits));
  });
  // Person-level keys, the shape optout_sync.py and the STOP-reply loop write. Without these the
  // opt-out suppresses only cases we happen to know about today.
  (r.p||[]).forEach(function(p){
    var k = '#' + p, n = notes[k] = notes[k] || {status:'',note:''};
    n.optout = n.optout || stamp;
    n.status = 'DO NOT CONTACT';
    n.optlog = n.optlog || [];
    n.optlog.push({ts:nowTS(), tsu:Date.now(), act:'set-local', src:'call-mode'});
  });
  // Mirror onto the device Do-Not-Text set too, so the board sees it without waiting for a sync.
  try{
    var s = JSON.parse(localStorage.getItem('fcDNT')||'[]')||[];
    (r.p||[]).forEach(function(p){ if(s.indexOf(p) < 0) s.push(p); });
    localStorage.setItem('fcDNT', JSON.stringify(s));
  }catch(e){}
  return cases.length;
}

/* The 20 seconds after a call is where the follow-up is either captured or lost forever. This is the
   screen that exists because he dials from the phone, writes the number on paper, and never comes
   back to the laptop to log it. Everything here writes to the SAME notes the board reads. */
function afterCall(r, o, nextC){
  SCREEN='after';
  _lastAfter=[r, o, nextC];
  document.getElementById('sheet').classList.add('hid');   // same tap-thief reasoning as screenOutcome
  // 'miss' == show the "try their next number" button. badnum (this line is dead) and gate (reached
  // the wrong person on this line) both want the NEXT number; callback/talked/appt do not.
  var st = textStage(r), miss = (o.k==='noanswer'||o.k==='voicemail'||o.k==='badnum'||o.k==='gate');
  // The next LIVE number, computed after logOutcome has recorded any badnum mark — so the number he
  // just declared dead is never the one offered next. -1 = nothing dialable left on this lead.
  var _nextPh = nextLivePh(r, phIdx + 1);
  var num = r.p[phIdx];
  /* THREE GATES, all of which were missing. The text button shipped with only the ladder check.
     - Do-Not-Text: dntSet() was written and then never called anywhere, and n.dntph — the field
       _mergeLead carries laptop->phone for exactly this — was never read. So a number marked
       do-not-text on the board was still one tap from an SMS on the device that sends them.
     - FTSA hours: flClock() was consumed in exactly one place, screenLead's banner, which afterCall
       destroys when it replaces #app. Florida's FTSA covers "telephonic sales calls" and the statute
       defines those to include text messages, so the window applies to this button too.
     - Opt-out: belt and braces. suppressed() should already have removed the lead, but this button
       can be reached from a card rendered before a sync landed. */
  var dnt = false;
  try{ dnt = (JSON.parse(localStorage.getItem('fcDNT')||'[]')||[]).indexOf(num) >= 0; }catch(e){}
  if(!dnt) dnt = ((notes[r.c]||{}).dntph||[]).indexOf(num) >= 0;
  var fl = flClock();
  /* SITUATIONAL TEXTS BY OUTCOME (2026-09-05). A person who just booked, asked for a callback, or
     had a real conversation gets a RESPONSIVE message — confirm, booking link, recap — not another
     rung of the cold ladder. The cold ladder stays exactly what it was, for the outcomes where the
     person never engaged (no answer, voicemail, bad number, gatekeeper). Situational texts remain
     available even when the ladder is retired/replied — "they replied, talk to them" is precisely
     what an appointment-confirm text is. Hard gates (opt-out, DNT, FTSA hours) block EVERYTHING. */
  var sitMap = { appt:['apptconfirm','booklink','apptremind','noshow'],
                 callback:['cbtext'],
                 talked:['aftertalk','booklink'],
                 notint:['aftertalk'] };
  var sitKeys = sitMap[o.k] || [];
  var _txk = sitKeys.length ? sitKeys[0] : 'ladder';
  var _chips = '';
  var txt = '';
  if(hardSuppressed(r))     txt = '<div class="nc">This lead is suppressed ('+esc(hardSuppressed(r))+'). Do not text.</div>';
  else if(o.k==='badnum')   txt = '<div class="nc">Bad number &mdash; nothing to text here. Try their next number below.</div>';
  else if(dnt)              txt = '<div class="nc">This number is on the do-not-text list. Call only.</div>';
  else if(!fl.ok)           txt = '<div class="nc">It is '+esc(fl.txt)+' in Florida. FTSA texting hours are 8:00 AM to 8:00 PM Eastern — this will be here in the morning.</div>';
  else if(sitKeys.length){
    _chips = '<div>' + sitKeys.map(function(k, ix){
      return '<button class="txch'+(ix===0?' on':'')+'" data-sit="'+k+'">'+esc(TEXT_SIT[k].t)+'</button>';
    }).join('') + '</div>';
    txt = '<button id="tx">'+esc(txLabel(_txk, st))+'</button>';
  }
  else if(st === 'retired') txt = '<div class="nc">Three messages already sent to this person. The ladder is closed — call only.</div>';
  else if(st === 'replied') txt = '<div class="nc">They have replied before. Do not send a cold-ladder text; talk to them.</div>';
  else {
    /* TWO COLD SCRIPTS, ONE RUNG. "After the call" is this page's own ladder and opens with "I just
       tried calling", which is true only here. "Board script" is the text batch's body for this
       lead — the just-filed one for an LP, the tax-deed one for a TD, the sale-date one otherwise.
       Both spend the SAME touch: whichever he sends writes one ch:'text' touch, so textStage counts
       it and the 3-message lifetime ladder cannot be walked around by switching chips.
       BAL rows get no chip: the batch bodies are homeowner-foreclosure copy and the balloon lane is
       an investor with no sale date, so the board would have nothing to say to them here. */
    var lbl = txLabel('ladder', st);
    if(!(r && r.st === 'BAL')){
      _chips = '<div><button class="txch on" data-sit="ladder">After the call</button>'
             + '<button class="txch" data-sit="batch">Board script</button></div>';
    }
    txt = '<button id="tx" class="'+(miss?'':'ghost')+'">'+esc(lbl)+'</button>';
  }
  /* NAME THE NUMBER. The text button targets r.p[phIdx] — whichever number he actually dialled —
     but the panel never said which, so on a 3-number lead he was approving a message to an unnamed
     recipient. If it is going to someone's phone, he gets to see whose. */
  /* BOOK IT WHILE THEY ARE STILL ON THE LINE. Only on APPOINTMENT SET, and first on the screen,
     because this is the one action with an expiry: the moment they agree is the moment to put it
     on a calendar. "I'll email you a link" is where booked calls go to die.
     Prefilled with the name and THE NUMBER HE ACTUALLY DIALLED (r.p[phIdx], not phones[0]) so he is
     not retyping a phone number while someone waits on the other end. attendeePhoneNumber is the
     field slug Cal.com uses for the "Attendee phone number" location, which is how both event types
     are configured -- the owner never joins a video call, we ring them. */
  var book = '';
  if(o.k === 'appt'){
    /* FULL name (r.on is the county roll flipped to FIRST LAST), the number he actually dialled,
       and notes that give the advisor the whole picture in one glance: address, case, sale date. */
    var bq = 'name=' + encodeURIComponent(String(r.on || firstName(r) || r.o || '').trim())
           + '&attendeePhoneNumber=' + encodeURIComponent('+1' + String(num||'').replace(/\D/g,'').slice(-10))
           + '&notes=' + encodeURIComponent((r.a||'') + (r.c ? (' | case ' + r.c) : '')
               + (r.x && r.d != null && r.d < 9000 ? (' | sale ' + r.x) : ''));
    book = '<div class="afterlab">Put it on the calendar</div>'
         + '<a class="btn" id="bk" href="' + BOOKURL + '?' + bq + '" target="_blank" rel="noopener">'
         + '&#128197; Book it now &mdash; they are still on the line</a>'
         + '<div class="mut" style="font-size:12px;margin-top:6px">Their name and this number are '
         + 'already filled in. Pick the slot, confirm, done.</div>';
  }
  $('app').innerHTML = '<div class="card">'
    + '<div class="addr" style="font-size:17px">Logged: '+esc(o.t)+'</div>'
    + '<div class="own">'+esc(firstName(r)||r.o||'')+' &middot; '+fmt(num)+'</div>'
    + sessStrip()
    + book
    + '<div class="afterlab">Follow-up text '+langChips()+'</div>' + _chips + txt
    + '<div class="afterlab">Call them back</div>'
    + '<div class="cbrow">'
    +   '<button class="cb" data-h="3">In 3 hours</button>'
    +   '<button class="cb" data-h="20">Tomorrow</button>'
    +   '<button class="cb" data-h="72">In 3 days</button>'
    + '</div>'
    /* THE OTHER NUMBERS. no-answer/voicemail used to auto-jump here; now it is a deliberate tap,
       so the text offer above is never skipped past. Only shown when a number is actually left. */
    /* Skips numbers marked bad (here or on the worker card) rather than counting blindly upward —
       offering a dead line as "their next number" is the whole reason badnum exists. */
    + ((miss && _nextPh >= 0)
        ? '<button id="nph" class="ghost" style="margin-top:14px">&#128222; Try their next number ('
          + (_nextPh + 1) + ' of ' + r.p.length + ')</button>'
        : '')
    + '<button id="nx" style="margin-top:14px">Next lead &rarr;</button>'
    + '</div><div class="sheetpad"></div>';
  var go = function(){ advance(r.c, nextC); };
  $('nx').onclick = go;
  if($('nph')) $('nph').onclick = function(){
    window._tapShieldUntil = Date.now() + 400;
    phIdx = _nextPh; toast('Next number'); screenLead();
  };
  Array.prototype.forEach.call(document.querySelectorAll('.cb'), function(b){
    b.onclick = function(){
      setCallback(r, +b.dataset.h, b.textContent.toLowerCase());
      Array.prototype.forEach.call(document.querySelectorAll('.cb'),function(x){x.disabled=true;});
      b.classList.add('on');
      /* Setting a callback IS choosing what happens next — making him also tap "Next lead" was one
         more mandatory tap in a flow he runs a hundred times a day. Brief hold so the chip's
         confirmation state is visible, then advance. EXCEPT while the "did it actually send?"
         confirm is up — auto-advancing there would destroy the unanswered confirm and the text
         send would never be logged. He answers that first; Next lead is still one tap away. */
      if(!$('txy')) setTimeout(go, 450);
    };
  });
  /* Chip taps swap which template the Send button fires. The button is re-labelled in place; the
     body itself is computed at SEND time so a language toggle between tap and send is honoured. */
  Array.prototype.forEach.call(document.querySelectorAll('.txch'), function(b){
    b.onclick = function(){
      _txk = b.dataset.sit;
      Array.prototype.forEach.call(document.querySelectorAll('.txch'), function(x){ x.classList.remove('on'); });
      b.classList.add('on');
      var t = $('tx'); if(t) t.textContent = txLabel(_txk, st);
    };
  });
  wireLang($('app'));
  if($('tx')) $('tx').onclick = function(){
    var body = (_txk==='ladder') ? textBody(r, st)
             : (_txk==='batch')  ? boardTextBody(r)
             : sitBody(r, _txk);
    /* Log the OPEN, not a send. Opening a composer is not a delivery — the board draws this exact
       line (the worker's textopen posts confirmed:false) and blurring it is how the ladder burns a
       touch on a message that was never sent. He confirms below once it is actually gone. */
    var n = notes[r.c] = notes[r.c] || {status:'',note:''};
    n.textopen = today(); saveNotes(); queueSync();
    var openComposer = function(){
      /* CLIPBOARD FIRST, ALWAYS. On the desktop this sms: link lands in Phone Link, and Phone Link
         honours an activation only while it has NO window open: with its window up on the Messages
         list it swallows the link outright. That is the whole "it locks and Re-open composer does
         nothing" failure -- reproduced on DESKTOP-35NNMFL 2026-09-05 with Start-Process, OUTSIDE the
         browser, so it is an OS-level behaviour this page cannot navigate around. What the page CAN
         do is guarantee the words are never trapped in it: the body goes to the clipboard on every
         open, so a composer that never appears (or appears blank) costs a paste instead of a lost
         message. Fire-and-forget on purpose -- writeText rejects on a denied permission and the
         send must not depend on it. */
      try{
        if(navigator.clipboard && navigator.clipboard.writeText){
          navigator.clipboard.writeText(body).catch(function(){});
        }
      }catch(e){}
      location.href = 'sms:' + r.p[phIdx] + (/iPhone|iPad|Mac/.test(navigator.userAgent) ? '&' : '?')
        + 'body=' + encodeURIComponent(body);
    };
    openComposer();
    /* THE PANEL. Before: "did it send?" with Yes / No — and No just toasted and DEAD-ENDED, so a
       message that failed to send had no way back except leaving the lead. Now it shows the exact
       text that went out (the output) and keeps a RESEND button alive on every path. */
    $('tx').outerHTML = '<div class="txconf" id="txconf0">Composer opened. Did it actually send?</div>'
      + '<div class="txbody" id="txbody"><span class="lbl">what was sent &middot; ' + esc(txKind(_txk, st)) + '</span>'
      + esc(body) + '</div>'
      + '<button id="txy">&#10003; Yes, it sent</button>'
      + '<button id="txr" class="ghost">&#8635; Re-open composer (send again)</button>'
      + '<button id="txn" class="ghost">No, I did not send it</button>'
      + '<div class="mut" style="font-size:12px;margin-top:8px">Text is on the clipboard. If the '
      + 'composer stayed shut or came up blank, <b>close the Phone Link window</b> and tap Re-open '
      + '&mdash; or open the thread and paste.</div>';
    $('txy').onclick = function(){
      var nn = notes[r.c] = notes[r.c] || {status:'',note:''};
      nn.touches = nn.touches || [];
      nn.touches.push({d:today(), ts:nowTS(), tsu:Date.now(), ch:'text',
                       out:'Text sent — ' + txKind(_txk, st), by:caller()});
      saveNotes(); queueSync();
      if(!_ftsaCapToast(nn)) toast('Text logged');
      go();
    };
    /* RESEND. Same body, same number — re-fires the composer. Does NOT log anything: a second open
       is still not a delivery, and the Yes button remains the only thing that writes the touch. */
    $('txr').onclick = function(){
      openComposer();
      toast('Re-opened — text is on the clipboard');
      var c = $('txconf0'); if(c) c.textContent = 'Re-opened. Did it send this time?';
    };
    $('txn').onclick = function(){
      toast('Not logged as sent');
      var c = $('txconf0');
      if(c) c.innerHTML = 'Not logged. Tap <b>Re-open composer</b> to try again, or move on '
        + '&mdash; this lead stays in the queue.';
    };
  };
}
/* FTSA caps telephonic (call/text) contact at 3 per lead per 24h. Before this, the only place that
   counted same-day touches was analyst.py's WEEKLY scan reading ALL-TIME touches -- a 4th touch
   surfaced days later, by which point a 5th and 6th could already be sitting in the ledger too. The
   dial or text has already happened by the time either caller below runs, so this cannot un-ring the
   phone; it exists to make the cap-crossing impossible to miss RIGHT NOW and to mark the exact touch
   an audit would need to find, instead of leaving that to a future re-scan of timestamps. */
function _telephonicToday(n){
  return (n.touches||[]).filter(function(x){ return x.d===today() && (x.ch==='call'||x.ch==='text'); }).length;
}
function _ftsaCapToast(n, extra){
  var cnt = _telephonicToday(n);
  if(cnt <= 3) return false;
  n.touches[n.touches.length-1].capExceeded = true;
  toast('⚠ FTSA cap: touch #' + cnt + ' today for this lead (max 3/24h).' + (extra ? ' ' + extra : '') +
        ' Stop contacting them until tomorrow.', {bad:true, ms:8000});
  return true;
}
/* Mirrors tracker_template.html's `callout` dispatcher so the phone and the laptop write the SAME
   shape. n.dials is additive and never deduped — it is the dial-through count the whole logging
   problem exists to recover; the touch itself stays deduped for cooldown purposes. */
function logOutcome(r,o,digits){
  var n=notes[r.c]=notes[r.c]||{status:'',note:''};
  n.touches=n.touches||[];
  var last=n.touches[n.touches.length-1];
  /* Returns whether a NEW touch was written. The same-day dedupe is correct (cooldown math), but
     silently collapsing the second identical tap while stamping "✓ logged" read as a broken
     button — the caller now tells the truth: "dial counted, already logged today". */
  var fresh = !(last && last.d===today() && last.ch==='call' && last.out===o.t);
  // `by` = who made this call (from their access code). It is what lets the other phone say
  // "called 2h ago by Carlos" instead of skipping a lead for no visible reason.
  if(fresh){
    n.touches.push({d:today(),ts:nowTS(),tsu:Date.now(),ch:'call',out:o.t,by:caller()});
    _ftsaCapToast(n);
  }
  n.dials=n.dials||[];
  /* If this attempt was pre-logged as 'pending' at dial time (screenLead's dial handler), REPLACE
     that marker instead of stacking a second dials row for one call -- keeps the dial count = one
     row per attempt, not doubled. Matched on same number + same day so it never eats a genuinely
     separate earlier attempt. */
  var _lastD = n.dials[n.dials.length-1];
  if(_lastD && _lastD.oc==='pending' && _lastD.d===today() && _lastD.ph4===String(digits).slice(-4)){
    n.dials.pop();
  }
  n.dials.push({d:today(),ts:nowTS(),tsu:Date.now(),ph4:String(digits).slice(-4),oc:o.k,by:caller()});
  n.cooldownH=o.h;
  if(o.k==='appt') n.status='Appointment';
  else if(o.k==='dnc'){ stopEverywhere(r, digits); }
  else if(o.k==='wrong'){ n.wrongown=n.wrongown||today(); n.status=n.status||'Wrong number'; }
  else if(o.k==='talked') n.status=n.status||'Contacted';
  else if(o.k==='notint') n.status=n.status||'Not interested';
  else if(o.k==='callback') n.status=n.status||'Callback';
  else if(o.k==='gate') n.status=n.status||'Gatekeeper';
  else if(o.k==='badnum'){
    /* THE NUMBER is dead, not the person — no status, no opt-out, and the lead stays callable on
       every other number it has. Written to `badph`, the SAME field the morning worker's bad-number
       control uses, so one mark is honoured by both surfaces and badnum_audit.py can already read,
       quarantine and restore it. Digits only (the worker stores and compares digits) and union only:
       a suppression list may grow, never shrink. */
    var _bd = String(digits||'').replace(/\D/g,'');
    n.badph = n.badph || [];
    if(_bd && n.badph.indexOf(_bd) < 0) n.badph.push(_bd);
  }
  // Retire it from the worker's queue on ANY logged outcome — same rule as the board's `callout`
  // dispatcher. Without this a lead worked here reappears in tomorrow's worker queue.
  retireFromWorkerQ(r.c);
  if(_WORKED.indexOf(r.c) < 0) _WORKED.push(r.c);
  /* THE FLAG freshCheck READS. It was declared `var touched=false`, read as `if(!touched) reload()`,
     and never assigned anywhere — so the guard its own comment promises ("never auto-reload once an
     outcome has been logged, that would lose his position mid-sequence") did the exact opposite.
     iOS returning from the dialer fires the freshness check; if a new build had landed, the page
     reloaded and dropped him back to lead 1 mid-sequence. Set it the moment work exists to lose. */
  touched = true;
  saveNotes();
  queueSync();
  return fresh;
}
/* Write-back rides the board's existing Supabase team sync.
   PUSH IMMEDIATELY — do NOT use the board's 1.5s syncPushSoon debounce. iOS backgrounds this tab the
   instant the dialer opens, and a debounced push scheduled at tap time simply never fires. The cost
   of pushing on every outcome is one small request; the cost of missing one is the logged call.
   If fcTeamKey is absent we still log LOCALLY and say so — never block dialing on sync setup. */
var _pushRetryT=null, _pushRetryN=0;
function queueSync(){
  var k=null; try{k=localStorage.getItem('fcTeamKey');}catch(e){}
  if(!k){ $('sync').textContent='Logged to this phone only — team sync is off'; return; }
  /* RETRY. The push fired right before tel:/sms: navigation dies when iOS backgrounds the tab,
     and a failed push used to be simply gone — the outcome lived on this phone only.
     syncPush() NEVER rejects (it swallows failures internally and stamps fcLastPush only on a
     real 2xx), so a .catch-based retry is dead code — success is detected by the stamp moving. */
  var _before=null; try{ _before=localStorage.getItem('fcLastPush'); }catch(e){}
  var _resched=function(){
    if(_pushRetryN>=4) return;
    clearTimeout(_pushRetryT); _pushRetryN++;
    _pushRetryT=setTimeout(queueSync, 4000*_pushRetryN);
  };
  try{
    syncPush().then(function(){
      var _after=null; try{ _after=localStorage.getItem('fcLastPush'); }catch(e){}
      if(_after && _after!==_before){ clearTimeout(_pushRetryT); _pushRetryT=null; _pushRetryN=0; return; }
      _resched();
    }).catch(_resched);
  }catch(e){ _resched(); }
}
/* One shared dismissal timer, cleared on every show. Without this, back-to-back toasts (the norm
   now that the 650ms screen-holds are gone) let the FIRST toast's 1400ms timer strip the class off
   the SECOND — the confirmation he most needs flashes for a few ms and dies. */
var _toastT=null;
function toast(t,opts){
  var el=$('toast'); el.textContent=t; el.classList.add('on');
  el.classList.toggle('bad', !!(opts && opts.bad));
  clearTimeout(_toastT);
  _toastT=setTimeout(function(){el.classList.remove('on');},(opts && opts.ms) || 1400);
}

/* ON-DEVICE ERROR LOG. I cannot see his phone; "sometimes the buttons do not work" is a symptom
   with no traceback. Every caught error and every uncaught one lands in a small ring buffer
   (fcErrLog, last 20), and when any exist the top bar shows a red chip — tapping it shows the log
   for a screenshot. Field reports become tracebacks. */
function logErr(err, where){
  try{
    var log = JSON.parse(localStorage.getItem('fcErrLog')||'[]');
    log.push({t: nowTS(), w: where||'', m: String(err && err.message || err).slice(0,200),
              s: String(err && err.stack || '').slice(0,300)});
    localStorage.setItem('fcErrLog', JSON.stringify(log.slice(-20)));
  }catch(e){}
}
window.addEventListener('error', function(ev){ logErr(ev.error||ev.message, 'window'); });
/* Tap OUTSIDE an open sheet closes it. An open drawer covers most of the card; taps on covered
   buttons hit the drawer body and did nothing visible — one of the shapes behind "sometimes the
   buttons do not work at all". Tap-outside-to-close is the behaviour every sheet UI trains. */
/* Post-swap tap shield (see the outcome handler): a click arriving <400ms after a screen swap is
   the tail of a double-tap aimed at the OLD screen — swallow it before any handler sees it. */
document.addEventListener('click', function(ev){
  if(window._tapShieldUntil && Date.now() < window._tapShieldUntil){
    /* stopImmediatePropagation, not stopPropagation: the sheet-close listener below is on the
       SAME node (document, capture) and would otherwise still run — a shielded ghost tap could
       close the script sheet mid-call. */
    ev.stopImmediatePropagation(); ev.preventDefault();
  }
}, true);
document.addEventListener('click', function(ev){
  var sh = document.getElementById('sheet');
  if(sh && sh.classList.contains('open') && !sh.contains(ev.target)) sh.classList.remove('open');
}, true);
window.addEventListener('unhandledrejection', function(ev){ logErr(ev.reason, 'promise'); });
function wire(){
  /* [data-l] NOT '.lane button'. The board-lane strip reuses the .lane class for its layout and its
     buttons carry data-b, so the bare selector matched them too and set `lane=undefined` — which
     laneDef() silently resolves to 'soon'. Tapping "TRACE" would have quietly switched the DIAL
     queue to Sale-soon and looked like nothing happened. Select on the attribute that means it. */
  Array.prototype.forEach.call(document.querySelectorAll('.lane button[data-l]'), function(b){
    b.onclick=function(){ lane=b.dataset.l; BLANE=null; i=0; render();
      /* 2026-09-04: switching lanes also pulls fresh team state, so a category he opens does not show
         leads a teammate worked since the last 45s sync -- the "keeps bringing me back to people I've
         done" complaint on the team side. The immediate render() is instant; the pull corrects it. */
      if(localStorage.getItem('fcTeamKey')){ try{ syncPull().then(function(){ loadNotes();
        if(SCREEN==='lead'){ try{ render(); }catch(_e){} } }); }catch(e){} }
    };
  });
  /* BOARD LANES. Tapping the lane you are already in closes the view and hands the dial queue
     back — otherwise the only way out is the "hide" link, which is one more thing to find. */
  Array.prototype.forEach.call(document.querySelectorAll('.lane button[data-b]'), function(b){
    b.onclick=function(){ BLANE = (BLANE === b.dataset.b) ? null : b.dataset.b; i=0; render(); };
  });
  /* A DIALABLE row inside a board lane opens the normal card, in the dial lane that actually holds
     it. Jumping straight to the lead without moving `lane` would leave advance() walking a pool
     this lead is not in, so "next" would land somewhere unrelated. Find its lane first, then seek.
     Rows with no phone carry no data-open at all, so there is nothing here to tap. */
  Array.prototype.forEach.call(document.querySelectorAll('[data-open]'), function(el){
    el.onclick=function(){
      var c = el.getAttribute('data-open');
      for(var q=0;q<LANES.length;q++){
        var L=LANES[q]; var hit=-1;
        if(!ROWS.some(function(r){ return r.c===c && L.pred(r); })) continue;
        lane=L.k; BLANE=null;
        var P=pool();
        for(var j=0;j<P.length;j++) if(P[j].c===c){ hit=j; break; }
        if(hit>=0){ i=hit; render(); return; }
      }
      /* In no dial lane we can open — suppressed, claimed by the other phone, or on the other
         seat. Say which rather than doing nothing when tapped. */
      toast(_clmOwner(c) ? 'A teammate is on this lead right now'
                         : 'Not in a dial lane right now \u2014 suppressed, or on the other phone');
    };
  });
  // "see the call log" on the hidden-count line — the registry of who has been called, by whom
  var rl = $('reglink');
  if(rl) rl.onclick = function(e){ e.preventDefault(); screenRegistry(); };
}
/* Stale-cache heal. iPhone Safari is the named worst offender and a phone quietly serving last
   week's list is the failure most likely to go unnoticed. Never auto-reload once an outcome has
   been logged — that would lose his position mid-sequence. Offer the pill instead. */
var touched=false;
async function freshCheck(){
  try{
    /* BOTH URL FORMS. This assumed the path was always a DIRECTORY (/call/ -> /call/index.html),
       so opening the page at its explicit file URL — a bookmark, an iOS Add-to-Home-Screen, a
       pasted link — built /call/index.html/index.html, 404'd, and the catch below swallowed it in
       silence. The whole job of this function is to notice a stale cache ("a phone quietly serving
       last week's list is the failure most likely to go unnoticed", per the comment above it), and
       for anyone on the file URL it had been doing nothing at all. Caught by the first runtime test
       of this page, 2026-08-26. */
    var _p=location.pathname;
    var u=/\.html?$/i.test(_p) ? _p : _p.replace(/\/$/,'')+'/index.html';
    var res=await fetch(u+'?_='+Date.now(),{headers:{'Range':'bytes=0-1200'}});
    var t=await res.text();
    /* [A-Za-z0-9]+, not [a-f0-9]+. The signature is a sha256 hex prefix today, so hex-only works —
       until the day it does not, and then this check silently goes back to never firing. The whole
       job of this function is to notice staleness; it must not be one format change from dead. */
    var m=t.match(/SIG="([A-Za-z0-9]+)"/);
    if(m && m[1]!==SIG){ if(!touched) location.reload(); else $('pill').style.display='block'; }
  }catch(e){}
}
/* ══════════════════════ SCRIPT SHEET ══════════════════════
   Collapsed by default: one line of peek. Tap the grip (or the peek) to expand, tap again to close.
   The lead's numbers stay visible behind it — that is the whole point of a sheet rather than an
   overlay. It lives outside #app so advancing a lead never destroys it mid-sentence. */
var SCRIPT=__SCRIPT__, ciocIdx=-1, objIdx=-1;
var SCRIPT_ALL=SCRIPT;   /* the full payload; renderSheet shadows SCRIPT per lane (homeowner vs SCRIPT_ALL.bal) */
function sheetToggle(){ $('sheet').classList.toggle('open'); }

/* ONE LANGUAGE AT A TIME. Every script block used to render EN and ES stacked — on the call screen
   that stacked the opener twice, the close cue, the drawer peek and (on voicemail) two more blocks:
   his words, "too many transcripts, keep one on the screen." The board's both-languages instinct
   was right for a LIST you scan; on a phone mid-call it is double the reading at the worst moment.
   The chosen language persists (fcLang); the other is ONE tap away on the EN|ES chips. */
function lang(){ try{ return localStorage.getItem('fcLang')==='es' ? 'es' : 'en'; }catch(e){ return 'en'; } }
function setLang(v){
  try{ localStorage.setItem('fcLang', v); }catch(e){}
  // repaint whichever script surfaces are up, without touching flow state.
  if(SCREEN==='outcome' && cur){ screenOutcome(); }
  else if(SCREEN==='lead' && cur){ screenLead(); }
  /* The after-call panel too — its text templates are bilingual now, and a language toggle that
     left the old body on screen would send the wrong language. Rebuilt from its own saved args;
     repainting resets the chip selection to the default, which is the safe direction. */
  else if(SCREEN==='after' && _lastAfter){ afterCall(_lastAfter[0], _lastAfter[1], _lastAfter[2]); }
  if(cur) renderSheet(cur);
}
function langChips(){
  var L=lang();
  return '<span class="lchips"><button class="lchip'+(L==='en'?' on':'')+'" data-lang="en">EN</button>'
       + '<button class="lchip'+(L==='es'?' on':'')+'" data-lang="es">ES</button></span>';
}
function wireLang(root){
  Array.prototype.forEach.call((root||document).querySelectorAll('.lchip'), function(b){
    b.onclick=function(ev){ ev.stopPropagation(); setLang(b.dataset.lang); };
  });
}
function say(en, es, r){
  if(lang()==='es' && es) return '<div class="say es">'+esc(fillScript(es, r))+'</div>';
  return '<div class="say">'+esc(fillScript(en, r))+'</div>'
       + (lang()==='es' && !es ? '<div class="noes">No Spanish version of this line yet.</div>' : '');
}

/* ══════════════ GUIDED CALL FLOW (2026-09-15) ══════════════
   One line at a time: the line -> TONE -> LISTEN -> tap what they said -> it routes you, opener to
   booked. A second renderer over the SAME payload (SCRIPT.flow references the constants the full
   sheet renders), never a second vocabulary. Additive GUIDED/FULL toggle; objection cards are detours
   with "continue where I was"; end states press the SAME .oc outcome button the card uses. Any throw
   falls back to the full sheet (logErr). Homeowner lane only (the balloon dict carries no `flow`). */
/* GUIDED is THE view (2026-09-15, Alejandro: "make it the main view"). Default ON; only an explicit
   tap on "view full script" (stored '0') turns it off, and that sticks per device. */
var _g = {on:true, step:'greet', stack:[], ret:null, kase:null};
try{ if(localStorage.getItem('fcGuided')==='0') _g.on=false; }catch(e){}
function _gSave(){ try{ localStorage.setItem('fcGuided', _g.on?'1':'0'); }catch(e){} }
function secondName(r){
  /* (p1b) the co-owner on the card. r.o is "OWNER1; OWNER2"; the county roll may be "LAST,FIRST". */
  try{
    var parts=String((r&&r.o)||'').split(';'); if(parts.length<2) return '';
    var s=parts[1]; if(s.indexOf(',')>=0) s=s.split(',').slice(1).join(' ');
    var tok=s.replace(/[^A-Za-z '-]/g,' ').trim().split(/\s+/)[0]||'';
    if(tok.length<2 || _NOTNAME.indexOf(tok.toUpperCase())>=0) return '';
    return tok.charAt(0).toUpperCase()+tok.slice(1).toLowerCase();
  }catch(e){ return ''; }
}
function _gStep(S,id){ for(var k=0;k<S.flow.length;k++){ if(S.flow[k].id===id) return S.flow[k]; } return null; }
function _gObj(S,n){ for(var k=0;k<S.obj.length;k++){ if(S.obj[k].n===n) return S.obj[k]; } return null; }
function _gBtn(label,to){ return '<button data-gto="'+esc(to)+'">'+esc(label)+'</button>'; }
function _gTone(t){ return t ? '<div class="mut" style="font-size:12px;margin-top:6px"><b style="color:#e7c98a">TONE</b> &middot; '+esc(t)+'</div>' : ''; }
function _gListen(t){
  return '<div style="margin:10px 0 4px;padding:8px 10px;border:1px dashed var(--gold);border-radius:8px;color:var(--gold);font-weight:800;font-size:12px;letter-spacing:.06em">&#9679; LISTEN'
       + (t ? ' &mdash; <span style="font-weight:400;letter-spacing:0;color:#e7c98a">'+esc(t)+'</span>' : '') + '</div>';
}
function renderGuided(r, S, named, warm){
  var b='', st=_g.step, es=(lang()==='es');
  b+='<div class="ltag">GUIDED '+langChips()+' <span class="mut" style="font-weight:400;letter-spacing:0">&middot; step '+(_g.stack.length+1)+'</span></div>';
  /* ---- objection detour */
  if(st.indexOf('obj:')===0){
    var n=+st.slice(4), o=_gObj(S,n), oe=(es&&o&&o.es)?o.es:o;
    b+='<div class="ltag">THEY PUSHED BACK &mdash; '+(o?esc(o.t):'card '+n)+'</div>';
    if(!o){ b+='<div class="noes">This objection card is not in this build. Cushion, isolate, ONE reframe, then continue.</div>'; }
    else{
      b+='<div class="mut" style="font-size:12px;margin-top:6px">They say: &ldquo;'+esc(oe.say)+'&rdquo;</div>';
      b+=_gTone('Cushion FIRST — agree, normalize ("most people I talk to say the same"). Then ONE reframe. Never argue. Voice down.');
      oe.reb.forEach(function(p){ b+='<div class="say'+(es&&o.es?' es':'')+'">'+esc(p)+'</div>'; });
      if(oe.one) b+='<div class="ltag">IF YOU ONLY GET ONE SENTENCE</div><div class="say'+(es&&o.es?' es':'')+'">'+esc(oe.one)+'</div>';
    }
    b+=_gListen('Let it land. Do not stack a second reframe.');
    b+='<div class="ltag">THEN</div><div class="objs">'+_gBtn('↩ Continue where I was','ret')+_gBtn('Still pushing back → 15-second out','f15')+_gBtn('Stop calling me','end:dnc')+'</div>';
    b+='<div class="objs" style="margin-top:4px">'+_gBtn('◀ Back','back')+'</div>';
    return b;
  }
  /* ---- end state: log it with the SAME button the card uses */
  if(st.indexOf('end:')===0){
    var k=st.slice(4), oc=null; for(var j=0;j<OUTCOMES.length;j++){ if(OUTCOMES[j].k===k) oc=OUTCOMES[j]; }
    b+='<div class="ltag">CALL OVER &mdash; LOG IT</div><div class="say">'+esc(oc?oc.t:k)+'</div>';
    b+='<div class="mut" style="font-size:12px;margin:6px 0">One tap logs it (the same as the outcome button on the card) and moves you to the next lead.</div>';
    b+='<div class="objs">'+_gBtn('✓ Log: '+(oc?oc.t:k),'log:'+k)+_gBtn('◀ Back','back')+'</div>';
    return b;
  }
  var s=_gStep(S,st); if(!s){ _g.step='greet'; s=_gStep(S,'greet'); }
  if(!s) throw new Error('guided: flow has no greet step');
  var en=s.en, esl=s.es, br=s.br, tone=s.tone, listen=s.listen;
  if(s.id==='greet'){
    if(warm){ en=S.op.wen; esl=S.op.wes; tone='Energy UP on the name — you KNOW this person. Then hand it to them and wait.';
              br=[['Glad to hear from me','frame'],['Not a good time','busy'],['Voicemail','vm']]; }
    else if(!named){ en=s.aen||en; esl=s.aes||esl; }
  }
  b+='<div class="ltag">'+esc(s.k||s.id)+'</div>';
  if(s.id==='greet' && named && !warm){ var sn=secondName(r); if(sn) b+='<div class="noes">Co-owner on the card: <b>'+esc(sn)+'</b> &mdash; if that is who picked up, use their name.</div>'; }
  if(s.rail){
    var rl=(es&&s.rail.es)?s.rail.es:s.rail.en;
    rl.forEach(function(p){ b+='<div class="say'+(es&&s.rail.es?' es':'')+'">'+esc(fillScript(p,r))+'</div>'; });
  } else { b+=say(en, esl, r); }
  b+=_gTone(tone)+_gListen(listen);
  b+='<div class="ltag">THEY SAID&hellip; tap it</div><div class="objs">';
  br.forEach(function(x){ b+=_gBtn(x[0],x[1]); });
  b+='</div><div class="objs" style="margin-top:4px">'+(_g.stack.length?_gBtn('◀ Back','back'):'')+_gBtn('↻ Restart','restart')+'</div>';
  return b;
}
function _gGo(to){
  var S=(cur && cur.st==='BAL' && SCRIPT_ALL.bal)?SCRIPT_ALL.bal:SCRIPT_ALL;
  if(to==='back'){ _g.step=_g.stack.pop()||'greet'; }
  else if(to==='restart'){ _g.step='greet'; _g.stack=[]; _g.ret=null; }
  else if(to==='ret'){ _g.stack.push(_g.step); _g.step=_g.ret||'greet'; _g.ret=null; }
  else if(to.indexOf('log:')===0){
    var k=to.slice(4), btn=document.querySelector('.oc button[data-oc="'+k+'"]');
    if(btn){ btn.click(); } else { toast('Tap the phone number to start the call first — outcomes log from the call screen.'); }
    return;
  } else {
    var here=_gStep(S,_g.step);
    _g.stack.push(_g.step);
    if(to.indexOf('obj:')===0) _g.ret=(here&&here.ret)||_g.step;
    _g.step=to;
  }
  renderSheet(cur);
}
function _gWire(){
  Array.prototype.forEach.call($('sbody').querySelectorAll('[data-gto]'), function(el){ el.onclick=function(){ _gGo(el.dataset.gto); }; });
  Array.prototype.forEach.call($('sbody').querySelectorAll('[data-gmode]'), function(el){ el.onclick=function(){ _g.on=(el.dataset.gmode==='1'); _gSave(); renderSheet(cur); }; });
  wireLang($('sbody'));
}

function renderSheet(r){
  /* BALLOON rows read the investor script (SCRIPT_ALL.bal, from the vault Refi Lane note). Shadowing
     the global with a local of the same name keeps every SCRIPT.* line below untouched — one switch,
     not fourteen edits, and the homeowner script cannot leak onto an investor call. */
  var SCRIPT = (r && r.st==='BAL' && SCRIPT_ALL.bal) ? SCRIPT_ALL.bal : SCRIPT_ALL;
  // Named vs anonymous opener — see firstName(). No usable name means ask for the owner instead of
  // greeting a company, a placeholder, or a name the card just told him not to use.
  var named = !!firstName(r);
  var warm = named && isWarm(r);
  var opEN = warm ? SCRIPT.op.wen : (named ? SCRIPT.op.en : SCRIPT.op.aen);
  var opES = warm ? SCRIPT.op.wes : (named ? SCRIPT.op.es : SCRIPT.op.aes);
  // PEEK — the first thing out of his mouth, plus the close cue, always one glance away.
  var op = fillScript(opEN, r);
  // The peek is the doorway. In GUIDED mode it says so in gold — the first line of the opener plus
  // "tap to start" — so the guided call is impossible to miss (the toggle used to hide in the drawer).
  var _guidedOn = !!(SCRIPT.flow && SCRIPT.flow.length && _g.on);
  $('peek').innerHTML = '<b>'+esc(op.split('.')[0])+'.</b> '
    + (_guidedOn
        ? '<span class="mut">&hellip;</span> <b style="color:var(--gold)">&#9654; TAP TO START THE CALL</b>'
          + '<span class="mut"> &middot; guided, one line at a time</span>'
        : '<span class="mut">&hellip; tap for the full script</span>')
    + '<div class="mut" style="margin-top:4px">Close with: <b>That&rsquo;s fair, right?</b></div>';

  var b = '';
  // GUIDED / FULL toggle + the guided renderer. Homeowner lane only (the balloon dict has no `flow`).
  // Reset to the opener whenever the LEAD changes — never mid-call.
  if(SCRIPT.flow && SCRIPT.flow.length){
    if(r && r.c!==_g.kase){ _g.kase=r.c; _g.step='greet'; _g.stack=[]; _g.ret=null; }
    // GUIDED is the main view: a gold header + a small "view full script" escape hatch. In FULL mode
    // the way back is one big gold button — the full sheet is the secondary view now, not a peer.
    if(_g.on){
      b += '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin:0 0 6px">'
         + '<b style="color:var(--gold);font-size:13px;letter-spacing:.06em">&#9654; GUIDED CALL</b>'
         + '<button data-gmode="0" style="min-height:36px;padding:0 12px;border-radius:999px;border:1px solid #2a3f6b;background:#0f1d3a;color:var(--mut);font-size:12px;font-weight:700;touch-action:manipulation">view full script</button>'
         + '</div>';
    } else {
      b += '<div class="cioc" style="grid-template-columns:1fr;margin:0 0 8px">'
         + '<button data-gmode="1" class="on">&#9664; BACK TO GUIDED CALL &mdash; one line at a time</button></div>';
    }
    if(_g.on){
      try{
        b += renderGuided(r, SCRIPT, named, warm);
        $('sbody').innerHTML = b;
        _gWire();
        return;
      }catch(e){
        logErr(e,'guided');
        b += '<div class="noes">Guided mode hit an error and fell back to the full script (see the error chip).</div>';
      }
    }
  }
  // TONALITY reference — collapsed by default (one tap), so it trains without pushing the opener down.
  // Homeowner lane only (the balloon dict has no `tone`). Native <details>, no JS state needed.
  if(SCRIPT.tone && SCRIPT.tone.length){
    b += '<details style="margin:0 0 8px;border:1px solid #2a3f6b;border-radius:8px;background:#0f1c38;padding:6px 10px">'
       + '<summary style="cursor:pointer;color:var(--gold);font-weight:700;font-size:12px;letter-spacing:.05em">TONALITY &mdash; how to say all of this (tap)</summary>';
    SCRIPT.tone.forEach(function(t){
      b += '<div style="margin:8px 0;font-size:13px;line-height:1.4"><b style="color:#e7c98a">'
         + esc(t.k) + '</b><br><span class="mut">' + esc(t.v) + '</span></div>';
    });
    b += '</details>';
  }
  b += '<div class="ltag">THE OPENER '+langChips()+'</div>'
        + say(opEN, opES, r)
        + (named ? '' : '<div class="noes">No usable first name on this lead &mdash; ask for the owner rather than guessing at one.</div>');

  // STATUS FRAME (NEPQ) — read straight after the opener, before any question. Lowers the "he's about
  // to pitch me" pressure; their "yes, that'd help" is the first micro-commitment. Homeowner lane only.
  if(SCRIPT.frame){
    b += '<div class="ltag">THE FRAME &mdash; set this BEFORE any question</div>'
       + say(SCRIPT.frame.en, SCRIPT.frame.es, r);
  }

  // NEPQ question stack -- only after the opener lands. Get them talking; the problem sells itself.
  if(SCRIPT.q && SCRIPT.q.length){
    b += '<div class="ltag">GET THEM TALKING &mdash; '+SCRIPT.q.length+' BEATS, IN ORDER</div>';
    SCRIPT.q.forEach(function(q, ix){
      b += '<div class="ltag" style="margin-top:9px">'+(ix+1)+' &middot; '+esc(q.k||'')+'</div>'
        + (q.w ? '<div class="mut" style="font-size:12px">'+esc(q.w)+'</div>' : '')
        + say(q.en, q.es, r);
    });
  }

  // PROBING RAIL (NEPQ) — echo their last words back as a question whenever they go vague. Not a
  // sequence; one-liners he drops in. Homeowner lane only.
  if(SCRIPT.probe && SCRIPT.probe.en && SCRIPT.probe.en.length){
    b += '<div class="ltag">WHEN THEY GET VAGUE &mdash; echo it back, get specific</div>';
    SCRIPT.probe.en.forEach(function(p, ix){
      b += say(p, (SCRIPT.probe.es && SCRIPT.probe.es[ix]) || p, r);
    });
  }

  // BRIDGE (NEPQ Transition Formula) — tie their words + emotion together right before the advisor ask.
  if(SCRIPT.bridge){
    b += '<div class="ltag">THE BRIDGE &mdash; then hand off to the advisor</div>'
       + say(SCRIPT.bridge.en, SCRIPT.bridge.es, r);
  }

  // CIOC as nav — tap a beat, get its words.
  b += '<div class="cioc">';
  SCRIPT.cioc.forEach(function(c,ix){ b += '<button data-cioc="'+ix+'"'+(ciocIdx===ix?' class="on"':'')+'>'+esc(c.k)+'</button>'; });
  b += '</div>';
  if(ciocIdx>=0){
    var c=SCRIPT.cioc[ciocIdx];
    b += '<div class="mut" style="font-size:12px">'+esc(c.w)+'</div><div class="say">'+esc(c.s)+'</div>';
  }

  b += '<div class="ltag">IF YOU ONLY GET 15 SECONDS</div><div class="say">'+esc(fillScript(SCRIPT.f15, r))+'</div>';

  /* VOICEMAIL script, in the sheet so it is one glance away DURING the call (the sheet stays open on
     the outcome screen). Was gated behind a post-call "Done reading" wall; "Left voicemail" now just
     logs and advances. Read live, never a recording (prerecorded/ringless drops need prior express
     written consent under the TCPA). */
  b += '<div class="ltag">IF NO ANSWER &mdash; LEAVE THIS (read it live, no recording)</div>'
     /* Delivery cue (NEPQ tonality) above the words — a voicemail lives on HOW it's said. Muted,
        not spoken; tracks the EN/ES toggle. Homeowner lane only (balloon dict has no vmtone). */
     + (SCRIPT.vmtone ? '<div class="mut" style="font-size:12px;margin:2px 0 7px">'
                        + esc(lang()==='es' && SCRIPT.vmtone.es ? SCRIPT.vmtone.es : SCRIPT.vmtone.en)
                        + '</div>' : '')
     /* the investor lane carries its own voicemail (SCRIPT.vm); the homeowner rescue message is
        the page-level VMEN/VMES and stays exactly what it was for every other row */
     + (SCRIPT.vm ? say(SCRIPT.vm.en, SCRIPT.vm.es, r) : say(VMEN, VMES, r));

  // BUSY / CALL ME BACK (NEPQ Calendar Commitment) — the most common early bail. Flip status: YOUR
  // time is the scarce thing. Its own block so it's one glance away, not buried in the drill pack.
  if(SCRIPT.busy){
    b += '<div class="ltag">&ldquo;I&rsquo;M BUSY / CALL ME BACK&rdquo; &mdash; flip it, don&rsquo;t chase</div>'
       + say(SCRIPT.busy.en, SCRIPT.busy.es, r);
  }

  // OBJECTIONS — tap what you are hearing.
  b += '<div class="ltag">THEY PUSHED BACK &mdash; tap what you heard</div>';
  if(!SCRIPT.obj.length){
    b += '<div class="noes">Objection cards unavailable in this build (drill pack not readable at build time).</div>';
  } else {
    b += '<div class="objs">';
    SCRIPT.obj.forEach(function(o,ix){ b += '<button data-obj="'+ix+'"'+(objIdx===ix?' class="on"':'')+'>'+esc(o.t)+'</button>'; });
    b += '</div>';
    if(objIdx>=0){
      var o=SCRIPT.obj[objIdx];
      b += '<div class="mut" style="font-size:12px;margin-top:6px">They say: &ldquo;'+esc(o.say)+'&rdquo;</div>';
      b += '<div class="ltag">EN</div>';
      o.reb.forEach(function(p){ b += '<div class="say">'+esc(p)+'</div>'; });
      if(o.one) b += '<div class="ltag">IF YOU ONLY GET ONE SENTENCE</div><div class="say">'+esc(o.one)+'</div>';
      if(o.es){
        b += '<div class="mut" style="font-size:12px;margin-top:12px">Dicen: &ldquo;'+esc(o.es.say)+'&rdquo;</div>';
        b += '<div class="ltag es">ES</div>';
        o.es.reb.forEach(function(p){ b += '<div class="say es">'+esc(p)+'</div>'; });
        if(o.es.one) b += '<div class="ltag es">SI SOLO LE DA TIEMPO A UNA FRASE</div><div class="say es">'+esc(o.es.one)+'</div>';
      } else {
        // never a blank card — say plainly that the Spanish does not exist for this one
        b += '<div class="noes">No Spanish version of this card yet.</div>';
      }
    }
  }

  b += '<div class="ltag">'+(SCRIPT===SCRIPT_ALL ? 'MARS &mdash; say this at the TOP of any advisor consult'
                                                 : 'THE FRAME &mdash; a business talking to a business about a rate')+'</div>'
     + '<div class="say">'+esc(SCRIPT.mars)+'</div>';

  b += '<div class="never"><b>NEVER SAY</b><br>';
  SCRIPT.never.forEach(function(n){ b += '&bull; '+esc(n)+'<br>'; });
  b += '</div>';

  $('sbody').innerHTML = b;
  // delegated so the handlers survive every re-render of the body
  Array.prototype.forEach.call($('sbody').querySelectorAll('[data-cioc]'), function(el){
    el.onclick=function(){ ciocIdx = (ciocIdx===+el.dataset.cioc) ? -1 : +el.dataset.cioc; renderSheet(cur); };
  });
  Array.prototype.forEach.call($('sbody').querySelectorAll('[data-obj]'), function(el){
    el.onclick=function(){ objIdx = (objIdx===+el.dataset.obj) ? -1 : +el.dataset.obj; renderSheet(cur); };
  });
  // GUIDED / FULL toggle is rendered in the full sheet too — wire it here as well.
  Array.prototype.forEach.call($('sbody').querySelectorAll('[data-gmode]'), function(el){
    el.onclick=function(){ _g.on=(el.dataset.gmode==='1'); _gSave(); renderSheet(cur); };
  });
  wireLang($('sbody'));
}
$('grip').onclick=sheetToggle;
$('peek').onclick=sheetToggle;

$('pill').onclick=function(){
  /* The pill sits above the sheet grip and used to reload INSTANTLY — mid-call, unconfirmed.
     Off the lead screen (call in progress), reloading needs a deliberate yes. */
  if(SCREEN!=='lead' && !confirm('Load the newer list now? Your logs are saved, but the screen resets to the top of the queue.')) return;
  location.reload();
};
/* Returning to the page means he just finished a call. Pull then (teammate opt-outs matter before
   the next dial) and re-check freshness. Do NOT poll on a 45s timer here — 200k-iteration PBKDF2
   every 45s on a backgrounded phone is pure battery burn for a page used in bursts. */
document.addEventListener('visibilitychange',function(){
  if(document.hidden) return;
  freshCheck();
  /* Pull first (teammate opt-outs before the next dial), then PUSH: the push fired just before
     the dialer opened may have died when the tab backgrounded — returning is the retry moment. */
  try{ if(localStorage.getItem('fcTeamKey')){ syncPull().then(function(){ loadNotes();
    /* THE POINT OF THE PULL. The merge just wrote whatever the other phone did while this one
       was in the dialer — re-check the lead on screen before he dials it. Without this line the
       fresh teammate outcome sits in notes and changes nothing until the next full repaint. */
    try{ teamRecheck(); }catch(e){}
    try{ teamWatch(); }catch(e){}
    if(touched) return syncPush(); }).catch(function(){}); } }catch(e){}
});
/* THE BACKGROUND TICK. startTeamSync() already pulls every 45s, but nothing consumed the result
   while the page stayed in the foreground — teamRecheck was wired to visibilitychange alone, so
   a teammate's outcome only ever surfaced when he left the app and came back. Reading a card for
   two minutes while Carlos worked that same lead showed him nothing.
   This polls the merged notes instead of the network: no fetch, no extra sync traffic, just a
   diff of what syncPull already wrote. 20s so a fresh outcome lands inside the window between
   reading a card and tapping dial, and teamWatch's case+timestamp dedup means a tick with
   nothing new is silent. */
setInterval(function(){ try{ if(localStorage.getItem('fcTeamKey')) teamWatch(); }catch(e){} }, 20000);
boot();
</script></body></html>
"""
