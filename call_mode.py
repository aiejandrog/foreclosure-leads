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
  * The outcome vocabulary is COPIED from tracker_template.html's CALL_OUTCOMES (7 entries). A
    fourth vocabulary would be a regression; there are already three in this codebase.

NEVER let a failure here break the board. foreclosure_leads calls this inside a try/except: an
exception costs the phone page, not the thing the business runs on.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# Copied verbatim from tracker_template.html CALL_OUTCOMES. Keep byte-identical: k, label, cooldown
# hours, and whether it suppresses permanently. `appt` is the one a stale 6-entry copy drops.
CALL_OUTCOMES = [
    ('noanswer',  'No answer',             24, False),
    ('voicemail', 'Left voicemail',        24, False),
    ('talked',    'Talked',                72, False),
    ('appt',      'APPOINTMENT SET',       72, False),
    ('wrong',     'Wrong number',           0, True),
    # 720h = 30 DAYS, was 72h. Field report 9/2: "they're telling me no and I keep calling
    # them". A no-answer coming back tomorrow is cadence; a human who SAID NO coming back in
    # three days is harassment, and it was the outcome table doing it, not a bug downstream.
    # 30 days rather than forever on purpose: sale dates move, situations collapse, and a
    # September no can be an October "thank God you called" -- but nobody's October starts
    # three days after their no.
    ('notint',    'Not interested',       720, False),
    ('dnc',       'DNC — do not contact',   0, True),
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
            'say': say.group(1).strip().strip('"'),
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


_SYNC_START = "const _DNC = s => s === 'DO NOT CONTACT';"
_SYNC_END = 'function startTeamSync()'

# The OPT-OUT ENFORCEMENT helpers, which live ~10,500 lines above the sync block and which the sync
# block depends on. Extracted for the same reason and by the same mechanism: `_effOptout` reads the
# audit LEDGER rather than the mutable n.optout scalar, precisely so an opt-out survives a status
# change — reimplementing that from memory on the phone is how enforcement quietly diverges.
_OPT_START = 'function _optLog(n, act, src)'
_OPT_END = 'function _dneg(iso)'

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
    import diligence_gate as _DG
    _dg = _DG.Tally()
    _quo = _quo_latest()
    for d in slim:
        case = d.get('case') or ''
        if not case or case in optouts or case in deads:
            continue
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
        _dgv = _dg.check(d)
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
        pairs = []
        n_dnc = 0
        for oi, p in enumerate(phones):
            if phdnc[oi] if oi < len(phdnc) else False:
                n_dnc += 1
                continue                                   # DNC numbers are dropped, never flagged
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
        if not is_lp and (days < 0 or days > max_days):     # auction already passed, or too far out
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
            'o': (d.get('owners') or '').strip()[:70],          # FULL owners, co-owners included
            # GREETING NAME, separate from the display string above.
            # `owners` comes off the county roll as "LAST,FIRST" on 32% of leads (measured across
            # 4,009 real rows; Broward is almost entirely this shape). Deriving a first name from it
            # takes the token before the comma — the SURNAME — so the page opened with "Hi Gordon"
            # to Steve Gordon and "Hi Joseph" to Milouse Joseph, on the call AND in every text.
            # The pipeline already fixes this: owner_clean flips "Last, First" to "First Last"
            # (foreclosure_leads.py:536-539) and ships as `oname`. Ship it and use it. ~14 B/lead.
            'on': _greet_name(d),
            'a': (d.get('addr') or '')[:60],
            'x': d.get('auction') or d.get('filedDate') or d.get('filed') or '',
            'd': days,
            'lp': 1 if is_lp else 0,
            'p': [keep[k] for k in order],
            # rank is indexed by the ORIGINAL phones position, recovered via keep_oi.
            'r': [(rank[keep_oi[k]][:1].upper()
                   if keep_oi[k] < len(rank) and rank[keep_oi[k]] else '') for k in order],
            'k': withheld,
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
            # ---- last Quo call (transcript-backed). None on most rows; the null-strip removes it.
            'qc': (lambda q: ({'w': str(q.get('at') or '')[:16], 'du': q.get('dur') or 0,
                               's': ' '.join(q.get('summary') or [])[:180],
                               'fl': (q.get('flags') or [])[:4]} if q else None))(_quo.get(case)),
            # ---- who is foreclosing ----
            'pl': _s('plaintiff', 46), 'ft': _s('ftype', 10),
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
    if _ident_dropped:
        # Say it out loud. A suppression that removes people silently is indistinguishable from a
        # queue that was always this size, and this one drops leads that LOOK perfectly callable.
        print('call mode: %d lead(s) dropped — the person opted out by email/phone with no case '
              'attached (identity ledger)' % _ident_dropped)
    # Same rule, same reason — and the diligence holds are the ones that look MOST callable of all,
    # because a held lead's card still shows equity, a phone and an auction date.
    _dg.report('call mode', indent='')
    return out[:cap], len(out)


def build_html(rows, total, enc_payload, built, sig, board_sig, sync_js='', textperson=None):
    """The page. Deliberately one file, no framework, no external fetch."""
    # Every placeholder must occur EXACTLY once. str.replace substitutes ALL occurrences — a
    # placeholder token mentioned in a comment gets the full replacement value injected into the
    # middle of that comment (this happened: an 18KB sync block landed inside a /* */ about itself,
    # and the comment's closing */ turned the block's tail into live, unbalanced code).
    # __SIG__ is 2 BY DESIGN: the byte-42 head marker freshCheck range-reads, plus the JS var.
    # Both must receive the same value, so replace-all is correct there — the guard just pins the
    # exact expected count so a third copy (e.g. in a comment) still fails the build.
    for _ph, _want in (('__SYNCJS__', 1), ('__SCRIPT__', 1), ('__OUTCOMES__', 1), ('__PAYLOAD__', 1),
                       ('__BUILT__', 1), ('__SIG__', 2), ('__BSIG__', 1), ('__SHOWN__', 1),
                       ('__BOOKURL__', 1),
                       ('__TOTAL__', 1), ('__VMEN__', 1), ('__VMES__', 1), ('__TEXTPERSON__', 1)):
        _n_ph = _PAGE.count(_ph)
        if _n_ph != _want:
            raise CallModeError('call_mode: placeholder %s occurs %d times in _PAGE (expected %d — '
                                'str.replace hits every copy, including ones in comments)'
                                % (_ph, _n_ph, _want))
    oc = json.dumps([{'k': k, 't': t, 'h': h, 's': s} for k, t, h, s in CALL_OUTCOMES])
    script = {
        'op': {'en': PHONE_OPENER_EN, 'es': PHONE_OPENER_ES,
               'aen': PHONE_OPENER_ANON_EN, 'aes': PHONE_OPENER_ANON_ES},
        'q': [{'en': _q_en, 'es': _q_es} for _q_en, _q_es in zip(NEPQ_Q_EN, NEPQ_Q_ES)],
        'cioc': [{'k': k, 'w': w, 's': sx} for k, w, sx in CIOC],
        'f15': FIFTEEN_SEC,
        'mars': MARS_BLOCK,
        'never': NEVER_SAY,
        'obj': load_objections(),
        # Rendered in red under the opener ONLY when sender.json carries "quo_record": true.
        # Florida is ALL-PARTY consent (FS 934.03, a felony statute) -- if Quo auto-records, the
        # consent ask is not optional and it has to be ON the screen he reads from, not in a doc.
        # quo_sync's coach pass then verifies the word "record" actually occurs in the transcript.
        'rec': (_QREC_LINE if _quo_recording() else None),
    }
    return _PAGE.replace('__SYNCJS__', sync_js) \
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


def make_callmode(slim, codes, encrypt, built, board_sig, optouts=None, deads=None, guard=None,
                  textperson=None):
    """Write docs/call/index.html. `encrypt` is foreclosure_leads._encrypt_multi and `guard` is its
    _js_guard — both INJECTED rather than re-implemented, so the crypto and the parse check can
    never drift from the board's.

    The guard runs BEFORE the write. A page whose script fails to parse still loads — it just loads
    with every button dead, which is the precise fail-silent shape this whole effort exists to
    remove. Better to ship no Call Mode than a Call Mode that looks fine and logs nothing.
    """
    outdir = os.path.join(HERE, 'docs', 'call')
    os.makedirs(outdir, exist_ok=True)
    dest = os.path.join(outdir, 'index.html')
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
        print('call mode: NO site.codes — docs/call/ replaced with a stub. The phone page is DOWN '
              'until a build runs with access codes present.')
        return 0, 0
    rows, total = call_rows(slim, optouts, deads)
    payload = encrypt(json.dumps({'r': rows, 'x': phone_index(slim)}), codes)
    import hashlib
    sig = hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()[:12]
    _assert_page_provides(_PAGE)
    sync_js = extract_sync_js(open(os.path.join(HERE, 'tracker_template.html'),
                                   encoding='utf-8').read())
    _assert_no_dead_overrides(_PAGE, sync_js)
    html = build_html(rows, total, payload, built, sig, board_sig, sync_js, textperson)
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
.top{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px}
.lane{display:flex;gap:6px}
.lane button{flex:1;min-height:44px;border-radius:10px;border:1px solid #2a3f6b;background:#0f1d3a;
     color:var(--mut);font-size:14px;font-weight:600}
.lane button.on{background:var(--gold);color:#0b1730;border-color:var(--gold)}
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
var SHOWN=__SHOWN__, TOTAL=__TOTAL__, VMEN=__VMEN__, VMES=__VMES__;
var LS='fcLeadNotes', ROWS=[], PHIDX=null, lane='soon', i=0, cur=null, phIdx=0, notes={};
function $(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function fmt(d){d=String(d||'');return d.length===10?'('+d.slice(0,3)+') '+d.slice(3,6)+'-'+d.slice(6):d;}
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
   DEFAULT-APP SETTING before a dial session. Set 'gv' to route via Google Voice deep links. */
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
var SENDER = {name:'Alejandro Gonzalez', phone:'(786) 631-1823'};
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
    .split('{sender}').join(SENDER.name || '')
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
    .replace(/\s{2,}/g, ' ').replace(/\s+,/g, ',').trim();
}

function loadNotes(){try{notes=JSON.parse(localStorage.getItem(LS)||'{}');}catch(e){notes={};}}
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
function save(){ saveNotes(); }
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
  /* PAYLOAD SHAPE. It used to be a bare rows array; it is now {r:rows, x:phoneIndex} so the lookup
     can resolve numbers that are NOT among the dialable rows (67% of them). Accept BOTH shapes —
     an older decrypted copy can still be sitting in this device's cache. */
  var take=function(p){ if(!p) return null;
    if(Array.isArray(p)){ ROWS=p; PHIDX=null; return p; }
    ROWS=p.r||[]; PHIDX=p.x||null; return ROWS; };
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
function suppressed(r){
  var h = hardSuppressed(r);
  if(h) return h;
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
    var coolH = (typeof n.cooldownH === 'number' && n.cooldownH >= 0) ? n.cooldownH : 24;
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
      var sCool = (typeof sn.cooldownH === 'number' && sn.cooldownH >= 0) ? sn.cooldownH : 24;
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

/* pool() is the ONE place suppression is evaluated. Everything else reads what it left behind.

   Two reasons this is not just tidiness. First, `suppressed()` depends on the opt-out phone index,
   which must be rebuilt from live notes — a caller that skipped the rebuild would silently judge a
   lead against a stale index and keep dialing someone who just opted out. Second, head() used to run
   its own full pass over ROWS to count hidden leads, doubling the per-paint cost for a number pool()
   already knew. Both problems disappear if there is exactly one evaluator. */
var _SUPN = 0;
/* Case ids that got at least one logged outcome this session. Deduped, because a lead dialled on
   three numbers is one lead worked, not three. */
var _WORKED = [];
/* THE buy-box predicate — one function, used by both the lane and the button count. Those were
   two identical inline copies with a comment promising they could never disagree; a promise kept
   by hand is a promise that breaks the first time only one copy is edited, and adding the
   tax-deed test was exactly that edit. */
function isBuyBox(r){ return !!r.bb && r.bbs !== 'UNDERWATER' && !r.bbtd; }
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
function _seat(){ try{ var s=JSON.parse(localStorage.getItem('fcSeat')||'{}');
    return (s && s.n>1 && s.i>=0 && s.i<s.n) ? s : null; }catch(e){ return null; } }
function _seatSet(n, i, w){
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
      toast('&#9888; ' + esc(_by) + ' is dialing this same list &mdash; set your SEAT so you '
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
    var coolH=(typeof n.cooldownH==='number' && n.cooldownH>=0)?n.cooldownH:24;
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
  var base;
  if(lane==='wq'){ var s={}; workerQ().forEach(function(c){ s[c]=1; }); base = ROWS.filter(function(r){ return s[r.c]; }); }
  /* BUY-BOX LANE. Everything matching a standing acquisition criterion, in either board state —
     a 4-bedroom in Miami Gardens is worth the call whether its sale is next week or unscheduled,
     so this deliberately does NOT split on r.lp the way the other two lanes do.
     UNDERWATER rows are dropped from this lane specifically. They stay reachable everywhere else
     (an upside-down owner still deserves the advisor call, and short sales are real work) — but
     this lane answers "which of these could we ACQUIRE", and a house worth less than its liens is
     not a candidate. Showing it here is how a $222k-underwater lead got ranked #2 on a hand-built
     sheet under the heading "most runway". */
  else if(lane==='bb'){ base = ROWS.filter(isBuyBox); }
  else base = ROWS.filter(function(r){ return lane==='soon' ? !r.lp : !!r.lp; });
  var n = 0;
  var keep = base.filter(function(r){ if(suppressed(r)){ n++; return false; } return true; });
  _SUPN = n;
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
  var _wq = workerQ();
  if(_wq.length && ROWS.some(function(r){ return _wq.indexOf(r.c) >= 0; })) lane = 'wq';
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
    if(!_seat()){
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
function render(){
  if(SCREEN!=='lead'){ return; }   // never stomp an interactive screen — advance() repaints fresh
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
  /* Stake the claim on ARRIVAL at the lead, not on tapping dial. He reads the card, checks the
     file and rehearses the open before he dials — that whole stretch is exactly when the other
     phone must be told this one is taken. Claiming at dial-time would leave the most likely
     collision window completely unguarded. */
  _clmTake(cur.c);
  screenLead();
}
function head(){
  var wq = workerQ().length;
  /* Buy-box count. Same predicate as the lane filter, so the number on the button and the list
     behind it can never disagree — a count derived a second way is a count that drifts. */
  var bbn = ROWS.filter(isBuyBox).length;
  /* NO SILENT CAPS. Suppression is correct, but a list that quietly shrank looks identical to a list
     that was always that size — the exact confusion that hid 466 callable leads behind call_list's
     --max 30. Say the number out loud.
     Read from pool()'s last count rather than re-deriving it: this is THIS LANE's hidden count, and
     head() always renders downstream of a pool() call. */
  var sup = _SUPN;
  return '<div class="top"><div class="lane">'
    +(wq?('<button data-l="wq" class="'+(lane==='wq'?'on':'')+'">Worker &middot; '+wq+'</button>'):'')
    +'<button data-l="soon" class="'+(lane==='soon'?'on':'')+'">Sale soon</button>'
    +'<button data-l="lp" class="'+(lane==='lp'?'on':'')+'">Fresh filings</button>'
    /* Count in the label, same rule as the Worker lane: a lane whose size is invisible gets
       assumed to be whatever it was last time. Hidden only when the box matches nothing, so an
       empty buy-box never reads as a broken build. */
    +(bbn?('<button data-l="bb" class="'+(lane==='bb'?'on':'')+'">Buy-box &middot; '+bbn+'</button>'):'')
    +'</div>'
    +(sup?('<div class="supn">'+sup+' hidden &mdash; wrong number, opted out, dead, or <b>already called</b> '
          +'(by you or a teammate) &middot; <a href="#" id="reglink" style="color:var(--gold)">see the call log</a></div>'):'')
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
    +'</div>';
}
function seatChip(){
  var s=_seat();
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
        : 'https://www2.miamidadeclerk.gov/ocs/Search.aspx']);
  var nm = String(r.o || '').replace(/[,&]/g, ' ').replace(/\s+/g, ' ').trim();
  if(nm) L.push(['People search', 'https://www.truepeoplesearch.com/results?name='
        + encodeURIComponent(nm) + (r.z ? '&citystatezip=' + encodeURIComponent(r.z) : '')]);
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

  var who = '<div class="addr">'+esc(r.a||'(no address on file)')+'</div>'
          + '<div class="own">'+esc(r.o||'(owner unknown)')+'</div>';
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

  var clock = '<div class="when">'+when+'</div><div class="chips">';
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
    + '</div>';
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

  $('app').innerHTML = head()
    + '<div class="card">'
    +   ftsaBar
    +   mlBan
    +   band('WHO', who)
    +   band('THE CLOCK', clock)
    +   band('THE MONEY', mny)
    +   band('WHO IS FORECLOSING', whoFc + prop + histLine(r))
    +   fileBand(r)
    +   '<a class="dial" href="'+dialHref(d)+'"'+dialTarget()+' id="dial">'+fmt(d)+'</a>'
    +   '<div class="sub">number '+(phIdx+1)+' of '+r.p.length
    +     (rk?(' &middot; '+(rk==='C'?'call first':rk==='O'?'ok':'last resort')):'')
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
function textBody(r, stage){
  return fillScript(TEXT_T[stage] || TEXT_T.cold, r);
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
  var talk = (SCRIPT.rec ? '<div class="nc" style="border-color:#e07b6a;color:#f2b8ad">'+esc(SCRIPT.rec)+'</div>' : '')
    + '<div class="ltag" style="margin-top:12px">WHEN THEY PICK UP '+langChips()+'</div>'
    + say(named?SCRIPT.op.en:SCRIPT.op.aen, named?SCRIPT.op.es:SCRIPT.op.aes, r)
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
  addRef('Property', esc(r.a || ''));
  addRef('Owner', esc(r.o || ''));
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
    n.dials.push({d:today(), ts:nowTS(), tsu:Date.now(), ph4:String(d).slice(-4), oc:'redial'});
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
  document.getElementById('sheet').classList.add('hid');   // same tap-thief reasoning as screenOutcome
  var st = textStage(r), miss = (o.k==='noanswer'||o.k==='voicemail');
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
  var txt = '';
  if(hardSuppressed(r))     txt = '<div class="nc">This lead is suppressed ('+esc(hardSuppressed(r))+'). Do not text.</div>';
  else if(dnt)              txt = '<div class="nc">This number is on the do-not-text list. Call only.</div>';
  else if(st === 'retired') txt = '<div class="nc">Three messages already sent to this person. The ladder is closed — call only.</div>';
  else if(st === 'replied') txt = '<div class="nc">They have replied before. Do not send a cold-ladder text; talk to them.</div>';
  else if(!fl.ok)           txt = '<div class="nc">It is '+esc(fl.txt)+' in Florida. FTSA texting hours are 8:00 AM to 8:00 PM Eastern — this will be here in the morning.</div>';
  else {
    var lbl = st==='cold' ? 'Send 1st text' : st==='follow' ? 'Send follow-up (2 of 3)' : 'Send final text (3 of 3)';
    txt = '<button id="tx" class="'+(miss?'':'ghost')+'">'+lbl+'</button>';
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
    var bq = 'name=' + encodeURIComponent(firstName(r) || r.o || '')
           + '&attendeePhoneNumber=' + encodeURIComponent('+1' + String(num||'').replace(/\D/g,'').slice(-10))
           + '&notes=' + encodeURIComponent((r.a||'') + (r.c ? (' | case ' + r.c) : ''));
    book = '<div class="afterlab">Put it on the calendar</div>'
         + '<a class="btn" id="bk" href="' + BOOKURL + '?' + bq + '" target="_blank" rel="noopener">'
         + '&#128197; Book it now &mdash; they are still on the line</a>'
         + '<div class="mut" style="font-size:12px;margin-top:6px">Their name and this number are '
         + 'already filled in. Pick the slot, confirm, done.</div>';
  }
  $('app').innerHTML = '<div class="card">'
    + '<div class="addr" style="font-size:17px">Logged: '+esc(o.t)+'</div>'
    + '<div class="own">'+esc(firstName(r)||r.o||'')+' &middot; '+fmt(num)+'</div>'
    + book
    + '<div class="afterlab">Follow-up text</div>' + txt
    + '<div class="afterlab">Call them back</div>'
    + '<div class="cbrow">'
    +   '<button class="cb" data-h="3">In 3 hours</button>'
    +   '<button class="cb" data-h="20">Tomorrow</button>'
    +   '<button class="cb" data-h="72">In 3 days</button>'
    + '</div>'
    /* THE OTHER NUMBERS. no-answer/voicemail used to auto-jump here; now it is a deliberate tap,
       so the text offer above is never skipped past. Only shown when a number is actually left. */
    + ((miss && phIdx + 1 < (r.p||[]).length)
        ? '<button id="nph" class="ghost" style="margin-top:14px">&#128222; Try their next number ('
          + (phIdx + 2) + ' of ' + r.p.length + ')</button>'
        : '')
    + '<button id="nx" style="margin-top:14px">Next lead &rarr;</button>'
    + '</div><div class="sheetpad"></div>';
  var go = function(){ advance(r.c, nextC); };
  $('nx').onclick = go;
  if($('nph')) $('nph').onclick = function(){
    window._tapShieldUntil = Date.now() + 400;
    phIdx++; toast('Next number'); screenLead();
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
  if($('tx')) $('tx').onclick = function(){
    var body = textBody(r, st);
    /* Log the OPEN, not a send. Opening a composer is not a delivery — the board draws this exact
       line (the worker's textopen posts confirmed:false) and blurring it is how the ladder burns a
       touch on a message that was never sent. He confirms below once it is actually gone. */
    var n = notes[r.c] = notes[r.c] || {status:'',note:''};
    n.textopen = today(); saveNotes(); queueSync();
    var openComposer = function(){
      location.href = 'sms:' + r.p[phIdx] + (/iPhone|iPad|Mac/.test(navigator.userAgent) ? '&' : '?')
        + 'body=' + encodeURIComponent(body);
    };
    openComposer();
    /* THE PANEL. Before: "did it send?" with Yes / No — and No just toasted and DEAD-ENDED, so a
       message that failed to send had no way back except leaving the lead. Now it shows the exact
       text that went out (the output) and keeps a RESEND button alive on every path. */
    $('tx').outerHTML = '<div class="txconf" id="txconf0">Composer opened. Did it actually send?</div>'
      + '<div class="txbody" id="txbody"><span class="lbl">what was sent &middot; ' + esc(st) + '</span>'
      + esc(body) + '</div>'
      + '<button id="txy">&#10003; Yes, it sent</button>'
      + '<button id="txr" class="ghost">&#8635; Re-open composer (send again)</button>'
      + '<button id="txn" class="ghost">No, I did not send it</button>';
    $('txy').onclick = function(){
      var nn = notes[r.c] = notes[r.c] || {status:'',note:''};
      nn.touches = nn.touches || [];
      nn.touches.push({d:today(), ts:nowTS(), tsu:Date.now(), ch:'text', out:'Text sent — ' + st, by:caller()});
      saveNotes(); queueSync();
      if(!_ftsaCapToast(nn)) toast('Text logged');
      go();
    };
    /* RESEND. Same body, same number — re-fires the composer. Does NOT log anything: a second open
       is still not a delivery, and the Yes button remains the only thing that writes the touch. */
    $('txr').onclick = function(){
      openComposer();
      toast('Composer re-opened — press send in Messages');
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
  n.dials.push({d:today(),ts:nowTS(),tsu:Date.now(),ph4:String(digits).slice(-4),oc:o.k,by:caller()});
  n.cooldownH=o.h;
  if(o.k==='appt') n.status='Appointment';
  else if(o.k==='dnc'){ stopEverywhere(r, digits); }
  else if(o.k==='wrong'){ n.wrongown=n.wrongown||today(); n.status=n.status||'Wrong number'; }
  else if(o.k==='talked') n.status=n.status||'Contacted';
  else if(o.k==='notint') n.status=n.status||'Not interested';
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
  Array.prototype.forEach.call(document.querySelectorAll('.lane button'), function(b){
    b.onclick=function(){ lane=b.dataset.l; i=0; render();
      /* 2026-09-04: switching lanes also pulls fresh team state, so a category he opens does not show
         leads a teammate worked since the last 45s sync -- the "keeps bringing me back to people I've
         done" complaint on the team side. The immediate render() is instant; the pull corrects it. */
      if(localStorage.getItem('fcTeamKey')){ try{ syncPull().then(function(){ loadNotes();
        if(SCREEN==='lead'){ try{ render(); }catch(_e){} } }); }catch(e){} }
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

function renderSheet(r){
  // Named vs anonymous opener — see firstName(). No usable name means ask for the owner instead of
  // greeting a company, a placeholder, or a name the card just told him not to use.
  var named = !!firstName(r);
  var opEN = named ? SCRIPT.op.en : SCRIPT.op.aen;
  var opES = named ? SCRIPT.op.es : SCRIPT.op.aes;
  // PEEK — the first thing out of his mouth, plus the close cue, always one glance away.
  var op = fillScript(opEN, r);
  $('peek').innerHTML = '<b>'+esc(op.split('.')[0])+'.</b> '
    + '<span class="mut">&hellip; tap for the full script</span>'
    + '<div class="mut" style="margin-top:4px">Close with: <b>That&rsquo;s fair, right?</b></div>';

  var b = '<div class="ltag">THE OPENER '+langChips()+'</div>'
        + say(opEN, opES, r)
        + (named ? '' : '<div class="noes">No usable first name on this lead &mdash; ask for the owner rather than guessing at one.</div>');

  // NEPQ question stack -- only after the opener lands. Get them talking; the problem sells itself.
  if(SCRIPT.q && SCRIPT.q.length){
    b += '<div class="ltag">GET THEM TALKING</div>';
    SCRIPT.q.forEach(function(q){ b += say(q.en, q.es, r); });
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
     + say(VMEN, VMES, r);

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

  b += '<div class="ltag">MARS &mdash; say this at the TOP of any advisor consult</div>'
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
