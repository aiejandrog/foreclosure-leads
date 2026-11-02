Warning: truncated output (original token count: 166682)
Total output lines: 10219

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

import footprint as _footprint

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

# VOICEMAIL (2026-10-12, Alejandro's own dictated script, cleaned for the copy law). He READS it --
# no prerecorded or ringless drop, which would need prior express written consent under the TCPA
# and is the one part of "auto-dial everything" that stays off the table.
# {sender}/{phone} resolve from the dialing phone's own identity (SENDER, fcSender), so Carlos's
# page says Carlos's name and number once his phone has them saved. {vmsale} is the whole
# sale-timing sentence, chosen per row by fillScript from VM_SALE below (live days to sale).
# Copy law: never "we stop the sale", no fee, no number, discovery framing (#57). The number is
# said twice because a voicemail is heard once, usually while driving.
VOICEMAIL_EN = ("Hi {first}, this is {sender} with Biscayne Solutions Group. {vmsale} "
                "We work with homeowners in this situation, and there may be more than one way to "
                "handle it, depending on what you want to do with the house. If you'd like to talk "
                "it through, call me at {phone}. Again, that's {phone}. If now's not a good time, "
                "just let me know and I won't reach out again. Thank you.")
VOICEMAIL_ES = ("Hola {first}, le habla {sender} de Biscayne Solutions Group. {vmsale_es} "
                "Trabajamos con propietarios en esta situación, y puede haber más de una manera de "
                "manejarlo, según lo que usted quiera hacer con la casa. Si desea conversarlo, "
                "llámeme al {phone}. Repito, {phone}. Si ahora no es buen momento, solo dígamelo y "
                "no lo vuelvo a contactar. Gracias.")

# The sale-timing sentence of the voicemail, by state. 'days' carries {n} (live days to sale, 2+);
# 'none' is a lead with no sale date (lis pendens), a passed sale, or a date the page cannot read.
# "court sale", not "foreclosure": a voicemail box is often shared, and the gatekeeper rule below
# (GATE step) is never to say "foreclosure" to anyone but the owner.
VM_SALE = {
    'en': {'days': "Public records show a court sale scheduled on your property at {st1} in about {n} days.",
           'tomorrow': "Public records show a court sale scheduled on your property at {st1} for tomorrow.",
           'today': "Public records show a court sale scheduled on your property at {st1} for today.",
           'none': "Public records show a court case filed on your property at {st1}."},
    'es': {'days': "Según los registros públicos, hay una venta judicial programada para su propiedad en {st1} en unos {n} días.",
           'tomorrow': "Según los registros públicos, hay una venta judicial programada para su propiedad en {st1} para mañana.",
           'today': "Según los registros públicos, hay una venta judicial programada para su propiedad en {st1} para hoy.",
           'none': "Según los registros públicos, hay un caso judicial presentado sobre su propiedad en {st1}."},
}

# HOW TO LEAVE IT — the tonality cue rendered right under the voicemail (2026-09-14, Alejandro). A
# voicemail lives or dies on delivery, not words: NEPQ leaves it low-status, unhurried and a little
# detached so it reads as a note, not a pitch — that softness is what earns the callback. Rendered as
# a muted coaching line above the script (like the NEPQ beat-purpose lines), NOT spoken aloud.
VM_TONE_EN = ("HOW TO SAY IT — low and unhurried, like a neighbor leaving a note, not a salesman. "
              "Pause at every period. Sound a little detached, almost unsure you've even got the right "
              "person — that softness is what earns the callback. Warm, never urgent, and slow down "
              "on the phone number so they can write it.")
VM_TONE_ES = ("CÓMO DECIRLO — bajo y sin prisa, como un vecino que deja un recado, no un vendedor. Haga "
              "pausa en cada punto. Suene un poco desapegado, casi como si no estuviera seguro de tener "
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
# 2026-10-15 (Alejandro): "i dont like how robotic the nepq one is". His own phone opener leads:
# "Do you know your property is in pre foreclosure?" It is a real question, so the page STOPS after
# it and lets them answer; the two nots and the permission ask come second, in plain words. The old
# "this might sound a little random... help me out" disarm is retired. Accuracy: {date} renders
# " with a sale date of <date>" only when an auction is actually set, and nothing otherwise, so an LP
# lead hears "pre-foreclosure" and a set sale hears its date. "Foreclosure" is said only after the
# greeting has settled that this is the owner (NEVER_SAY): the full sheet carries a [Wait for yes.]
# cue after the greeting, and the guided "who's asking?" answer re-confirms the owner first.
# 2026-10-26 (Alejandro): "always assume". The greeting now names us and assumes it is them, so the
# question no longer re-introduces the caller: _OPEN_ASK_* is the question alone.
_OPEN_ASK_EN = "Do you know your property on {st1} is in pre-foreclosure{date}?"
_OPEN_NOTS_EN = ("I am not your lender and I am not calling to buy your house. I help owners see what options "
                 "they have before this goes any further. Can I ask you a couple of quick questions?")
# usted register throughout — the playbook grades Spanish drills on it and every Spanish asset in the
# repo (flyer, letter, voicemail) matches. Never tú.
_OPEN_ASK_ES = "¿Usted sabe que su propiedad en {st1} está en proceso de foreclosure{date_es}?"
_OPEN_NOTS_ES = ("No soy su prestamista y no le llamo para comprarle la casa. Ayudo a los dueños a ver "
                 "qué opciones tienen antes de que esto avance más. ¿Le puedo hacer un par de preguntas "
                 "rápidas?")
# If they did not know: they just heard it from a stranger. Be human first, then the nots.
_OPEN_NEWS_EN = ("I'm sorry to be the one telling you. It's in the public court records, and that's "
                 "the only reason I called.")
_OPEN_NEWS_ES = ("Siento ser yo quien se lo diga. Está en los registros públicos de la corte, y es la "
                 "única razón por la que le llamé.")
# The full sheet reads as one block, so the pause is written in as a [bracket] cue (rendered
# literally; fillScript only touches {curly}).
_OPEN_BODY_EN = _OPEN_ASK_EN + " [Stop. Let them answer.] " + _OPEN_NOTS_EN
_OPEN_BODY_ES = _OPEN_ASK_ES + " [Pare. Deje que conteste.] " + _OPEN_NOTS_ES

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

# ASSUME IT IS THEM (2026-10-26, Alejandro: "hi, is this Lewis? ... we don't know if that's Lewis. We
# want to be familiar ... always assume"). The greeting says their name, says ours, and asks how they
# are, like a call to someone we know. No "is this ...?": the question is what made it sound like a
# stranger reading a list. The wait after "how you doing?" is the identity check: someone who is not
# them says so there ("this isn't Lewis", "he's not here"), and the guided call has a branch for it,
# before anything about the property is said (NEVER_SAY: "foreclosure" only once it is the owner).
# Two variants, because greeting the wrong "name" is worse than not greeting one.
# Measured on the fixture before this existed: "Hi, is this ACME?" (a company), "Hi, is this
# UNKNOWN?" (a placeholder), and — worst — "Hi, is this OLD?" on the very lead whose card warns
# "do NOT open with this name" because the roll owner has changed. The script contradicted the card.
# 2026-10-26 evening, Alejandro's own words: "Hi, Karl? Can you hear me?" Their name and a line
# check, nothing else; who we are comes after they answer (INTRO_*).
GREET_EN = "Hi, {first}? Can you hear me?"
GREET_ES = "Hola, ¿{first}? ¿Me escucha?"
INTRO_EN = "It's {sender} with Biscayne Solutions Group."
INTRO_ES = "Le habla {sender}, de Biscayne Solutions Group."
# No usable name: still assume. Say who we are, then assume they own the place and let them correct it.
GREET_ANON_EN = "Hey, it's {sender} with Biscayne Solutions Group. You're the owner over on {st1}, right?"
GREET_ANON_ES = "Hola, le habla {sender}, de Biscayne Solutions Group. Usted es el dueño de {st1}, ¿verdad?"
# The cue is the identity check: only the person answering to the name goes on to the question.
WAIT_EN = "[Wait. If it's not them, they'll say so.]"
WAIT_ES = "[Espere. Si no es la persona, se lo va a decir.]"
PHONE_OPENER_EN = GREET_EN + " " + WAIT_EN + " " + INTRO_EN + " " + _OPEN_BODY_EN
PHONE_OPENER_ES = GREET_ES + " " + WAIT_ES + " " + INTRO_ES + " " + _OPEN_BODY_ES
# {st1} — the STREET LINE, not the full "…, WEST PALM BEACH, FL 33401". The full address got read
# aloud on a live call (2026-08-16, Evernia St) and sounds like a process server. Street only.
PHONE_OPENER_ANON_EN = GREET_ANON_EN + " " + WAIT_EN + " " + _OPEN_BODY_EN
PHONE_OPENER_ANON_ES = GREET_ANON_ES + " " + WAIT_ES + " " + _OPEN_BODY_ES

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

# ONE SOURCE (2026-09-25). This block was a hand-typed copy that had already drifted from
# disclaimer.mars() -- it dropped "not a law firm", "not a lender or mortgage broker" and "not a
# foreclosure-rescue company" and added a fee sentence of its own. CLAUDE.md: never re-type this
# text. The federally-scripted sentences come from disclaimer.py; the one house sentence about fees
# is appended separately so it is visibly not part of the MARS text.
import disclaimer as _D
_MARS_HOUSE_EN = "This consultation is free, and you will never be asked to pay us a fee before you get results."
_MARS_HOUSE_ES = "Esta consulta es gratis, y nunca se le pedirá pagarnos una tarifa antes de obtener resultados."
MARS_BLOCK = ("Before we start, a few things I am required to tell you: "
              + _D.mars('Biscayne Solutions Group', as_html=False) + " " + _MARS_HOUSE_EN)
MARS_BLOCK_ES = ("Antes de empezar, unas cosas que estoy obligado a decirle: "
                 + _D.mars('Biscayne Solutions Group', lang='es', as_html=False) + " " + _MARS_HOUSE_ES)

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
B…150682 tokens truncated…ventListener('click', function(ev){
  var b = ev.target && ev.target.closest ? ev.target.closest('[data-cb]') : null;
  if(!b || !cur) return;
  var a = b.getAttribute('data-cb');
  if(a === 'save') cbSave(cur, false); else if(a === 'resched') cbSave(cur, true);
  else if(a === 'done') cbDone(cur); else if(a === 'cancel') cbCancel(cur);
  ev.preventDefault();
}, false);

/* ══════════════════════ SCRIPT SHEET ══════════════════════
   Collapsed by default: one line of peek. Tap the grip (or the peek) to expand, tap again to close.
   The lead's numbers stay visible behind it — that is the whole point of a sheet rather than an
   overlay. It lives outside #app so advancing a lead never destroys it mid-sentence. */
var SCRIPT=__SCRIPT__, ciocIdx=-1, objIdx=-1;
var SCRIPT_ALL=SCRIPT;   /* the full payload; renderSheet shadows SCRIPT per lane (homeowner vs SCRIPT_ALL.bal) */
function sheetToggle(){ $('sheet').classList.toggle('open'); }
/* --sheeth = the COLLAPSED sheet's height (grip + peek + border + safe-area), 0 while it is hidden.
   Body padding, the Who-texted-me button and the new-list pill all stand on it, so nothing fixed
   lands on the opener line at any width or zoom. body.shopen hides the button over an open sheet. */
function _sheetPad(){
  try{
    var sh = document.getElementById('sheet'), h = 0;
    if(sh && getComputedStyle(sh).display !== 'none'){
      var g = document.getElementById('grip'), pk = document.getElementById('peek');
      h = Math.ceil((g ? g.offsetHeight : 0) + (pk ? pk.offsetHeight : 0) + 1
                    + (parseFloat(getComputedStyle(sh).paddingBottom) || 0));
    }
    var root = document.documentElement;
    if(root.style.getPropertyValue('--sheeth') !== h + 'px') root.style.setProperty('--sheeth', h + 'px');
    var open = !!(h && sh.classList.contains('open'));
    document.body.classList.toggle('shopen', open);
    /* The team-sync line rides INSIDE the open sheet, under the grip. At z-45 in page flow it
       painted across the open script; below the sheet it would be hidden exactly while he works
       with the script up. Collapsed, it goes back above the pill in normal flow. */
    var sy = document.getElementById('sync');
    if(sy && open && sy.parentNode !== sh) sh.insertBefore(sy, document.getElementById('peek'));
    else if(sy && !open && sy.parentNode === sh) document.body.insertBefore(sy, document.getElementById('pill'));
  }catch(e){}
}
(function(){
  try{
    var sh = document.getElementById('sheet');
    if(window.ResizeObserver){ var ro = new ResizeObserver(_sheetPad); ro.observe(sh); ro.observe(document.getElementById('peek')); }
    if(window.MutationObserver) new MutationObserver(_sheetPad).observe(sh, {attributes:true, attributeFilter:['class']});
    window.addEventListener('resize', _sheetPad);
    _sheetPad();
  }catch(e){}
})();

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
  else if(SCREEN==='mail' && _MAIL){ screenMail(_MAIL[0], _MAIL[1], _MAIL[2]); }
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
/* FULL-SCREEN VOICEMAIL. The same words as the sheet's "IF NO ANSWER" block (VMEN/VMES, or the
   investor lane's SCRIPT.vm), big, alone on the screen. Logging goes through the SAME .oc outcome
   button the card uses, so a voicemail logged here is exactly a "Left voicemail" tap: a miss, 24h. */
function _vmTexts(r){
  var S = (r && r.st==='BAL' && SCRIPT_ALL.bal) ? SCRIPT_ALL.bal : SCRIPT_ALL;
  return S.vm ? {en:S.vm.en, es:S.vm.es, tone:null} : {en:VMEN, es:VMES, tone:S.vmtone||null};
}
function vmClose(){ var o=document.getElementById('vmfull'); if(o && o.parentNode) o.parentNode.removeChild(o); }
function vmOpen(r){
  vmClose();
  var t=_vmTexts(r), es=(lang()==='es' && t.es);
  var o=document.createElement('div'); o.id='vmfull'; o.className='vmfull';
  o.innerHTML='<div class="vmin"><div class="vmtop"><span>VOICEMAIL &middot; READ IT LIVE</span>'+langChips()+'</div>'
    + _vmWarnHtml()
    + (t.tone ? '<div class="mut" style="font-size:12px;margin-top:8px">'+esc(es && t.tone.es ? t.tone.es : t.tone.en)+'</div>' : '')
    /* the callback number never breaks across lines: he reads it aloud, slowly, twice */
    + '<div class="vmsay">'+esc(fillScript(es ? t.es : t.en, r))
        .split(esc(SENDER.phone||'\u0000')).join('<span style="white-space:nowrap">'+esc(SENDER.phone||'')+'</span>')+'</div>'
    + '<button class="vmlog" id="vmlog">&#10003;&nbsp; Left voicemail &mdash; log it</button>'
    + '<button class="vmx" id="vmx">Close</button></div>';
  document.body.appendChild(o);
  Array.prototype.forEach.call(o.querySelectorAll('.lchip'), function(b){
    b.onclick=function(ev){ ev.stopPropagation(); setLang(b.dataset.lang); vmOpen(r); };
  });
  o.querySelector('#vmx').onclick=function(){ vmClose(); };
  o.querySelector('#vmlog').onclick=function(){
    vmClose();
    var b=document.querySelector('.oc button[data-oc="voicemail"]');
    if(b && !b.disabled) b.click(); else toast('Tap "Left voicemail" below to log it');
  };
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
              br=[['Glad to hear from me','frame'],['Not a good time','busy'],['Their phone\'s AI answered','screen2'],['Voicemail','vm']]; }
    else if(!named){ en=s.aen||en; esl=s.aes||esl; }
  }
  /* Any other step with an anonymous variant (the screener lines): no name on the row, no {first}. */
  else if(!named && s.aen){ en=s.aen; esl=s.aes||esl; }
  b+='<div class="ltag">'+esc(s.k||s.id)+'</div>';
  if(s.id==='vm') b+=_vmWarnHtml();
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
    /* "We already reinstated" is not an outcome of its own (CALL_OUTCOMES is the board's vocabulary
       and drives cooldown and the no policy). It rides the outcome he logs as a line in n.note, the
       free-text field, exactly like setCallback's trace. No status, touch, or suppression field. */
    if(btn && cur && cur.c && _g.stack.indexOf('reinstated')>=0){
      var rn=notes[cur.c]=notes[cur.c]||{status:'',note:''}, rl=today()+' owner says loan reinstated';
      rn.note = rn.note ? (rn.note.indexOf(rl)>=0 ? rn.note : (rn.note+'\n'+rl)) : rl;
    }
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
  // First sentence, before any [cue]; keep its own ? or ! ("Hi, Karl? Can you hear me?").
  var _pk = op.split('[')[0].trim().split('. ')[0]; if(!/[.?!]$/.test(_pk)) _pk += '.';
  $('peek').innerHTML = '<b>'+esc(_pk)+'</b> '
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
     /* WRONG VOICE GUARD (2026-10-12): an unset phone falls back to Alejandro's name and number, so
        on Carlos's page the voicemail would leave Alejandro's callback. Say so above the words. */
     + _vmWarnHtml()
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
     + '<div class="say">'+esc((lang()==='es' && SCRIPT.mars_es) ? SCRIPT.mars_es : SCRIPT.mars)+'</div>';

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
  if(SCREEN!=='lead' && !confirm('Load the newer list now? Log this call first. Your logs are saved, and you come back to your place in the list.')) return;
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
    try{ workerWatch(); }catch(e){}
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
/* MORNING WORKER HAND-OFFS ARRIVE LIVE (2026-10-26). The worker's "Send to Call Mode" button (and its
   auto-run) stamps the synced .wq flag; startTeamSync pulls it within 45s. This tick says so: a banner
   with how many of MY leads just arrived, and one tap to the Worker lane where they sort first. Only
   leads on this page's own dial list that are not hard-suppressed count, so the number is what this
   phone can actually call. Each case announces once per hand-off (keyed by its wq date). */
var _WK_SEEN = 'fcWkSeen:' + location.pathname, _WKT = null;   // per seat page: both share one origin
function workerWatch(){
  /* nothing decrypted yet (both lists empty): writing `keep` now would forget every hand-off seen */
  if(!ROWS || (!ROWS.length && !(COV && COV.length))) return 0;
  _WQSET = null;
  var q = wqSet(), seen = {};
  try{ seen = JSON.parse(localStorage.getItem(_WK_SEEN)||'{}') || {}; }catch(e){}
  /* the same test the Worker lane applies (supReason + teammate claim), so the number on the banner
     is the number in the lane it opens */
  var mine = ROWS.filter(function(r){
    if(!q[r.c] || !_seatMine(r)) return false;
    try{ return !supReason(r, 'worker').k && !_clmOwner(r.c); }catch(e){ return false; } });
  /* no-number leads the worker sent over wait on the Find a number list (lkRows: this seat, unclaimed,
     not hard-suppressed) */
  var lks = [];
  try{ lks = lkRows().filter(function(r){ return q[r.c]; }); }catch(e){}
  var keep = {}, fresh = 0;
  mine.concat(lks).forEach(function(r){ var w = (notes[r.c]||{}).wq || 'q'; keep[r.c] = w; if(seen[r.c] !== w) fresh++; });
  try{ localStorage.setItem(_WK_SEEN, JSON.stringify(keep)); }catch(e){}
  if(!fresh) return 0;
  var b = $('wkban'); if(!b) return fresh;
  b.innerHTML = '<span class="wkn">&#128229;</span><span>' + fresh + ' new from Morning Worker'
    + '<br><span style="font-weight:500;opacity:.85;font-size:12px">' + mine.length + ' to call'
    + (lks.length ? ' &middot; ' + lks.length + ' to look up' : '') + ' on this phone</span></span>'
    + '<button id="wkgo">Open</button><button id="wkx" aria-label="Close" style="margin-left:0;background:transparent;color:inherit;padding:9px 6px">&#10005;</button>';
  b.classList.remove('on'); void b.offsetWidth; b.classList.add('on');
  $('wkx').onclick = function(){ b.classList.remove('on'); };
  $('wkgo').onclick = function(){
    b.classList.remove('on');
    if(SCREEN !== 'lead'){ toast('Log this call first, then open the Worker lane', {ms:3000}); return; }
    if(!mine.length && lks.length){ BLANE = '__lk'; i = 0; render(); return; }
    lane = 'worker'; QVIEW = null; BLANE = null; i = 0; render();
  };
  clearTimeout(_WKT); _WKT = setTimeout(function(){ b.classList.remove('on'); }, 20000);
  return fresh;
}
setInterval(function(){ try{ workerWatch(); }catch(e){ logErr(e, 'workerWatch'); } }, 20000);
setTimeout(function(){ try{ workerWatch(); }catch(e){} }, 3000);
boot();
</script></body></html>
"""


def _print_ledger_audit(audit):
    """One line per category, then the leftover rows. No address, name, number or subject."""
    print('ledger audit: %d case-less send(s) tied to a lead by recipient or property address, %d advisor/test send(s) '
          'reaching no lead, %d marked not on board, %d still unattributed'
          % (audit.get('matched', 0), audit.get('test', 0), audit.get('offboard', 0), audit.get('unattributed', 0)))
    for r in audit.get('rows') or []:
        print('  unattributed: %s %-5s to-domain=%s lane=%s test=%s owner-named=%s addr-named=%s'
              % (r['d'] or '?', r['ch'], r['dom'], r['lane'] or '-', r['test'], r['owner'], r.get('addr', False)))


if __name__ == '__main__' and '--ledger-audit' in __import__('sys').argv[1:]:
    # Laptop diagnostic: reads mail_sent.json / text_sent.json beside this file. Without the build's
    # lead list it cannot tie a case-less send by recipient, so "still unattributed" here can be
    # higher than the nightly build's; the build prints the real figure.
    _a = ledger_audit()
    _print_ledger_audit(_a)
    print(history_coverage(0, 0, _a))
