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
    ('notint',    'Not interested',        72, False),
    ('dnc',       'DNC — do not contact',   0, True),
]

# 15-second voicemail, Copy Pack §5. He READS it — no prerecorded or ringless drop, which would
# need prior express written consent under the TCPA and is the one part of "auto-dial everything"
# that stays off the table.
# 8/17 masterclass voice. {st1} not {street}: the full address read aloud sounds like a process
# server (Evernia St lesson, 2026-08-16). {phone} is the explicit callback slot — fillScript
# resolves it from SENDER.phone. Numbers written as words because he READS this live.
VOICEMAIL_EN = ("Hi {first}, this is {sender} with Miami Solutions Group, about {st1}. "
                "You may have a plan. Keep it. A plan can land a day late, and here a day is "
                "everything. Our senior advisor, thirty plus years, maps your free backup in five "
                "minutes. Call me any hour at {phone}. Thanks.")
VOICEMAIL_ES = ("Hola {first}, le habla {sender} de Miami Solutions Group, por {st1}. "
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
_OPEN_BODY_EN = ("My name is {sender}. I am not selling anything and I am not calling to buy your "
                 "house. There is a court case with your address on it, and I am probably the only "
                 "person calling you this week who is NOT trying to take the property. Two minutes, "
                 "then I will get out of your hair. Fair?")
# usted register throughout — the playbook grades Spanish drills on it and every Spanish asset in the
# repo (flyer, letter, voicemail) matches. Never tú.
_OPEN_BODY_ES = ("Mi nombre es {sender}. No le vengo a vender nada, ni a comprarle la casa. Hay un "
                 "caso en la corte con su dirección, y posiblemente soy el único que le llama esta "
                 "semana que NO anda detrás de la propiedad. Dos minutos y lo dejo tranquilo. "
                 "¿Le parece?")

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
     'interfere with your plan at all, is there any other reason not to spend ten minutes?'),
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

MARS_BLOCK = ("Before we start, a few things I am required to tell you: Miami Solutions Group is not "
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
_DRILL = os.path.join(os.path.expanduser('~'), 'projects', 'obsidian-vault', '5-projects',
                      'MSG Sales', 'MSG Objection Drill Pack - Procrastinator Psychology.md')
# Vendored copy of the PARSED cards, committed to the repo. The vault only exists on this machine —
# CI has no drill pack, so before this cache a CI rebuild silently replaced Call Mode with a page
# that had zero objection cards, overwriting a full local build with a degraded one every night.
# The local build refreshes this file whenever the vault parses; CI just reads it. No new exposure:
# the same content already ships in PLAINTEXT inside docs/call/index.html's __SCRIPT__ payload.
_DRILL_CACHE = os.path.join(HERE, 'call_objections.json')


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
            cards = json.load(open(_DRILL_CACHE, encoding='utf-8'))
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
            json.dump(out, open(_DRILL_CACHE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        except Exception:
            pass
    return out


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
    """
    on = (d.get('oname') or '').strip()
    case = str(d.get('case') or '')
    owners_first = (d.get('owners') or '').split(';')[0]
    if (case.startswith('50') and on and ',' not in owners_first
            and not _COMPANY_RE.search(on)):
        toks = on.split()
        if len(toks) >= 2:
            on = ' '.join(toks[1:] + [toks[0]])   # LAST FIRST [M] -> FIRST [M] LAST
    return on[:40]


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
    out = []
    for d in slim:
        case = d.get('case') or ''
        if not case or case in optouts or case in deads:
            continue
        if d.get('sibclaimed') or d.get('saleBkAct') or d.get('lpDismissed'):
            continue
        if d.get('title_status') == 'transferred':          # ownership gate — they no longer own it
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
            'ss': (d.get('orsurvsen') if isinstance(d.get('orsurvsen'), (int, float)) else None),
            'oc': _s('orconf', 6), 'td': _n('taxDue'), 'et': _n('etax'),
            # ---- clock ----
            'sv': d.get('saleSurv'), 'sc': _n('saleSched'), 'sw': _s('saleWho', 6),
            'bk': _n('saleBK'), 'sl': _s('saleLift', 10), 'cs': _s('cstatus', 22),
            # ---- who is foreclosing ----
            'pl': _s('plaintiff', 46), 'ft': _s('ftype', 10),
            # ---- the person / property ----
            # booleans only when TRUE — an absent key reads as false in the renderer, so shipping
            # `"hs":0` on 1,300 rows is pure payload for no information
            'hs': 1 if d.get('hs') else None,
            'ab': 1 if (_m1 and _a1 and _m1 != _a1) else None,
            'dd': _s('dor_desc', 30), 'bd': _n('beds'), 'ba': _n('baths'), 'sf': _n('sqft'),
            'zs': _s('zstatus', 12),
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
        out.append({k: v for k, v in row.items() if v is not None and v != '' and v != []})
    # soonest sale first, then known equity high-to-low, then unknowns — call_list.py's key
    # .get() not [] — null/empty fields are stripped from the row above, so `e` is often absent.
    # Known-equity still sorts ahead of unknown; unknown must never masquerade as 0.
    out.sort(key=lambda r: (r.get('d', 9999), 0 if r.get('e') is not None else 1, -(r.get('e') or 0)))
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
        'cioc': [{'k': k, 'w': w, 's': sx} for k, w, sx in CIOC],
        'f15': FIFTEEN_SEC,
        'mars': MARS_BLOCK,
        'never': NEVER_SAY,
        'obj': load_objections(),
    }
    return _PAGE.replace('__SYNCJS__', sync_js) \
                .replace('__SCRIPT__', json.dumps(script, ensure_ascii=False)) \
                .replace('__OUTCOMES__', oc) \
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
    payload = encrypt(json.dumps(rows), codes)
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
#tx,#txy,#txn,#nx,#vmdone{display:block;width:100%;min-height:52px;border-radius:12px;border:1px solid #2a3f6b;
     background:#1d4ed8;color:#fff;font-size:17px;font-weight:700;margin-top:8px;touch-action:manipulation}
#tx.ghost,#txn.ghost{background:#0f1d3a;color:var(--ink)}
#nx{background:#0f1d3a;color:var(--ink)}
.cbrow{display:flex;gap:8px}
.cb{flex:1;min-height:50px;border-radius:12px;border:1px solid #2a3f6b;background:#0f1d3a;
     color:var(--ink);font-size:14px;font-weight:600;touch-action:manipulation}
.cb.on{border-color:var(--ok);color:var(--ok)}
.cb:disabled{opacity:.45}
.txconf{font-size:14px;color:var(--gold);font-weight:600;margin-top:10px}
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
/* Person-keyed send counts from the server ledger. Authoritative across DEVICES and across cases
   that never shipped to this phone — without it a fresh phone reads every owner as never-texted and
   restarts the 3-touch ladder at touch 1, which is exactly the shape of the August email incident. */
var TEXTPERSON=__TEXTPERSON__;
var SHOWN=__SHOWN__, TOTAL=__TOTAL__, VMEN=__VMEN__, VMES=__VMES__;
var LS='fcLeadNotes', ROWS=[], lane='soon', i=0, cur=null, phIdx=0, notes={};
function $(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function fmt(d){d=String(d||'');return d.length===10?'('+d.slice(0,3)+') '+d.slice(3,6)+'-'+d.slice(6):d;}
function today(){var d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function nowTS(){return new Date().toLocaleString();}
/* WHO IS SPEAKING. The board keeps identity in SENDER_DEFAULTS + localStorage.fcSender; this page is
   the SAME ORIGIN so it reads the same store rather than baking a second copy that could drift.
   Two heals copied from the board because both were real: a company name saved into `name` renders
   "this is Miami Solutions Group with Miami Solutions Group", and the legacy auto-injected "Jose"
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
  var saved=null; try{saved=localStorage.getItem('fcPw');}catch(e){}
  if(saved){ var r=await unwrap(saved); if(r){ ROWS=r; return start(); } }
  $('go').onclick=async function(){
    var c=$('code').value.trim(); if(!c) return;
    $('gmsg').textContent='Checking…';
    var r=await unwrap(c);
    if(!r){ $('gmsg').textContent='That code did not open this page. If it is new, the page may be cached — close the tab fully and reopen.'; return; }
    try{localStorage.setItem('fcPw',c);}catch(e){}   // unlocking either page unlocks both
    ROWS=r; start();
  };
}
/* THE MORNING WORKER'S QUEUE. `fcCallQueue` is a durable localStorage ledger the worker fills when a
   lead is phone-only — same origin, so this page reads it directly with no new transport. Its own
   comment in the board records why it is durable: "before this, 192 queued calls evaporated with the
   tab and the week produced exactly one dial."
   Entries retire only on a logged call outcome, which is exactly what this page does. */
function workerQ(){
  try{ return (JSON.parse(localStorage.getItem('fcCallQueue')||'[]')||[]).map(function(x){ return x.c; }); }
  catch(e){ return []; }
}
function retireFromWorkerQ(caseId){
  try{
    var q = JSON.parse(localStorage.getItem('fcCallQueue')||'[]')||[];
    var n = q.filter(function(x){ return x.c !== caseId; });
    if(n.length !== q.length) localStorage.setItem('fcCallQueue', JSON.stringify(n));
  }catch(e){}
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
function suppressed(r){
  var n = notes[r.c] || {};
  if(n.wrongown) return 'wrong number reported';
  if(n.optout || n.status === 'DO NOT CONTACT') return 'opted out';
  if(n.status === 'Dead') return 'dead';
  var ph = optPhones(), p = r.p || [];
  for(var j=0;j<p.length;j++) if(ph[p[j]]) return 'this number opted out';
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
function pool(){
  /* Rebuilt here rather than in render() so EVERY caller gets a fresh index — advance() and
     screenOutcome() both call pool() outside a render, and a teammate's opt-out landing between
     paints would otherwise be missed. 0.05ms for 900 note keys; the O(rows x notes) version this
     replaced measured 36.8ms per pass at 400 rows, twice per paint. */
  optPhones(true);
  var base;
  if(lane==='wq'){ var s={}; workerQ().forEach(function(c){ s[c]=1; }); base = ROWS.filter(function(r){ return s[r.c]; }); }
  else base = ROWS.filter(function(r){ return lane==='soon' ? !r.lp : !!r.lp; });
  var n = 0;
  var keep = base.filter(function(r){ if(suppressed(r)){ n++; return false; } return true; });
  _SUPN = n;
  return keep;
}
function start(){
  /* The worker's queue is the DEFAULT when it has anything in it. Those leads were triaged this
     morning and are phone-only — the worker could not reach them any other way, so they are the
     highest-intent list on the device. Sale-soon and Fresh-filings stay one tap away. */
  var _wq = workerQ();
  if(_wq.length && ROWS.some(function(r){ return _wq.indexOf(r.c) >= 0; })) lane = 'wq';
  i=0; render(); freshCheck();
  var k=null; try{k=localStorage.getItem('fcTeamKey');}catch(e){}
  if(!k){ $('sync').textContent='Team sync is off — outcomes log to this phone only. Turn it on in the board to reach the laptop.'; }
  else { $('sync').textContent='Team sync on'; try{ syncPull().then(function(){ loadNotes(); }); }catch(e){} }
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
  screenLead();
}
function head(){
  var wq = workerQ().length;
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
    +'</div>'
    +(sup?('<div class="supn">'+sup+' hidden &mdash; wrong number, opted out, or dead</div>'):'')
    /* The build stamp, in the TOP bar on purpose. BUILT was baked into the page and rendered
       nowhere, so a phone serving last week's list had no visible tell — the operator's only signal
       was leads that felt stale. The bottom of the screen belongs to the sheet (fixed, z-40), which
       covers anything placed there; the top bar is the one strip nothing ever overlays. */
    +'<div class="supn">list built '+esc(BUILT.replace('T',' '))+errChip()+'</div>'
    +'</div>';
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

function screenLead(){
  SCREEN='lead';
  document.getElementById('sheet').classList.remove('hid');   // the script belongs to the call screen
  var r=cur, d=r.p[phIdx], rk=r.r[phIdx]||'';
  var when = r.lp ? ('lis pendens filed '+esc(r.x||''))
                  : ((r.d===0?'auction TODAY':(r.d===1?'auction TOMORROW':'auction in '+r.d+' days'))+(r.x?' &middot; '+esc(r.x):''));

  var who = '<div class="addr">'+esc(r.a||'(no address on file)')+'</div>'
          + '<div class="own">'+esc(r.o||'(owner unknown)')+'</div>';
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
    + kv('Equity', r.e==null ? '<span class="nc">not known</span>' : (Math.round(r.e)+'%'+(has(r,'E')?' <span class="nc">gross</span>':'')))
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
      + (r.zap ? ' &middot; <a style="color:#F4E5A7;font-weight:800" href="tel:+1'+String(r.zap).replace(/^1/,'')+'">&#128222; call the agent</a>' : '')
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
    +   '<a class="dial" href="tel:+1'+d+'" id="dial">'+fmt(d)+'</a>'
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
   OBJECT — a raw `+ SENDER +` renders "this is [object Object] with Miami Solutions Group", which is
   the same failure as the {sender} bug that already reached a live read-aloud script. Going through
   fillScript also inherits the Jose heal and the company-name heal for free. */
/* 8/17 masterclass voice (overnight 2026-08-18): cushion + parachute, one ask, no em dashes
   (reads as AI in a text), 'save this number' = drill card 14's read-back close at SMS size.
   ES drafts exist (workflow wf_50663fa7) but TEXT_T is EN-only until a language path lands. */
var TEXT_T = {
  cold:   'Hi{first}, this is {sender} with Miami Solutions Group. I just tried calling about {st1}. '
        + 'I am not selling anything and not trying to buy the house. If you have a plan, keep it. '
        + 'A free 5 minutes with our senior advisor, 30 plus years, gets you every option. '
        + 'Reply YES, or STOP to opt out.',
  follow: 'Hi{first}, {sender} with Miami Solutions Group again about {st1}. If your plan is moving, '
        + 'good, keep it. One question. Do you have it in writing yet? If not, our senior advisor '
        + 'can be the backup, free, 5 minutes. Reply YES, or STOP to opt out.',
  final:  'Hi{first}, last text from me, {sender} with Miami Solutions Group about {st1}. I hope your '
        + 'plan lands on time. If anything slips, one free call with our senior advisor maps what '
        + 'still works. Save this number even if you delete this text. Reply YES, or STOP to opt out.'
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
  var talk = '<div class="ltag" style="margin-top:12px">WHEN THEY PICK UP '+langChips()+'</div>'
    + say(named?SCRIPT.op.en:SCRIPT.op.aen, named?SCRIPT.op.es:SCRIPT.op.aes, r)
    + '<div class="mut" style="font-size:12px;margin-top:4px">Close with: <b>'
    + (lang()==='es' ? '&iquest;Verdad que s&iacute;?' : 'That&rsquo;s fair, right?')
    + '</b> &middot; CIOC + objections in the script drawer below.</div>';
  $('app').innerHTML='<div class="card"><div class="addr" style="font-size:18px">How did it go with '+esc(firstName(r)||'them')+'?</div>'
    +'<div class="own">'+fmt(d)+'</div>'
    /* REDIAL — same number, no outcome logged, place kept. For the dropped call, the accidental
       hang-up, the straight-to-voicemail retry. An ANCHOR, not a JS navigation: tel: via href is
       the proven path (the main dial button), and returning from the dialer lands right back on
       this screen because the SCREEN guard defers any sync repaint. */
    +'<a class="redial" href="tel:+1'+d+'" id="redial">&#8635;&nbsp; Redial '+fmt(d)+'</a>'
    + talk
    +'<div class="oc" style="margin-top:10px">'+btns+'</div>'
    +'<div class="vm" id="vm" style="display:none"></div></div><div class="sheetpad"></div>';
  /* A redial IS a dial — record it, or the dial-through count undercounts the actual work (the
     exact logging gap this page exists to close). oc:'redial' marks it as outcome-pending; the
     outcome he eventually taps logs its own entry for that attempt. */
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
      /* The voicemail script must SURVIVE for him to read it. It renders into #vm, which lives
         inside #app — and on a single-phone lead the flow fell straight through to afterCall(),
         whose first statement replaces #app.innerHTML. The script he was told to read aloud was
         destroyed in the same synchronous handler that created it, before the browser ever painted.
         So: show it, and wait for him to say he is done. ONE language at a time (fcLang) — the
         stacked EN+ES block was part of the "too many transcripts" pile-up. */
      var go;
      /* The vm block repaints ITSELF on a language toggle — going through setLang() would rebuild
         the whole outcome screen and wipe the script he is mid-way through reading aloud. */
      function paintVM(){
        $('vm').style.display='block';
        $('vm').innerHTML='<b>Read this. Do not use a recording. '+langChips()+'</b>'
          + say(VMEN, VMES, r)
          + (go ? '<button id="vmdone" style="margin-top:14px">Done reading &rarr;</button>' : '');
        if(go && $('vmdone')) $('vmdone').onclick = go;
        Array.prototype.forEach.call($('vm').querySelectorAll('.lchip'), function(c){
          c.onclick=function(ev){ ev.stopPropagation();
            try{ localStorage.setItem('fcLang', c.dataset.lang); }catch(e){}
            paintVM(); };
        });
      }
      if(o.k==='voicemail') paintVM();
      if(o.k==='dnc' && !confirm('They asked to stop. This closes every channel, permanently, on every device. Continue?')){
        Array.prototype.forEach.call(document.querySelectorAll('.oc button'),function(x){x.disabled=false;}); return;
      }
      var _fresh = logOutcome(r,o,d);
      var _okmsg = _fresh ? ('✓ '+o.t+' — logged') : ('✓ dial counted — '+o.t+' already logged today');
      /* The shield arms INSIDE go() — at the actual screen swap — not at outcome-tap time. On the
         voicemail path the swap happens seconds later (the Done-reading button), and arming early
         both missed that swap and ate legitimate taps on the just-painted voicemail block. */
      var _shield = function(){ window._tapShieldUntil = Date.now() + 400; };
      if((o.k==='noanswer'||o.k==='voicemail') && phIdx+1 < r.p.length){
        go = function(){ _shield(); phIdx++; toast(_fresh?'Logged · next number':'Dial counted · next number'); screenLead(); };
      } else if(o.k==='dnc'||o.k==='wrong'||o.k==='notint'){
        /* An outcome that ENDS the relationship gets no follow-up offer — showing a Text button
           after someone says do-not-contact is how a compliance breach happens by muscle memory. */
        go = function(){ _shield(); toast(_okmsg); advance(r.c,nextC); };
      } else {
        go = function(){ _shield(); toast(_okmsg); afterCall(r,o,nextC); };
      }
      // A voicemail script he has not finished reading must not be replaced out from under him.
      // paintVM re-runs now that `go` exists, adding the Done button (and keeping it across
      // language toggles).
      if(o.k==='voicemail'){ paintVM(); } else go();
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
  if(suppressed(r))         txt = '<div class="nc">This lead is suppressed ('+esc(suppressed(r))+'). Do not text.</div>';
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
  $('app').innerHTML = '<div class="card">'
    + '<div class="addr" style="font-size:17px">Logged: '+esc(o.t)+'</div>'
    + '<div class="own">'+esc(firstName(r)||r.o||'')+' &middot; '+fmt(num)+'</div>'
    + '<div class="afterlab">Follow-up text</div>' + txt
    + '<div class="afterlab">Call them back</div>'
    + '<div class="cbrow">'
    +   '<button class="cb" data-h="3">In 3 hours</button>'
    +   '<button class="cb" data-h="20">Tomorrow</button>'
    +   '<button class="cb" data-h="72">In 3 days</button>'
    + '</div>'
    + '<button id="nx" style="margin-top:14px">Next lead &rarr;</button>'
    + '</div><div class="sheetpad"></div>';
  var go = function(){ advance(r.c, nextC); };
  $('nx').onclick = go;
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
    location.href = 'sms:' + r.p[phIdx] + (/iPhone|iPad|Mac/.test(navigator.userAgent) ? '&' : '?')
      + 'body=' + encodeURIComponent(body);
    $('tx').outerHTML = '<div class="txconf">Composer opened. Did it actually send?</div>'
      + '<button id="txy">Yes, it sent</button>'
      + '<button id="txn" class="ghost">No, I did not send it</button>';
    $('txy').onclick = function(){
      var nn = notes[r.c] = notes[r.c] || {status:'',note:''};
      nn.touches = nn.touches || [];
      nn.touches.push({d:today(), ts:nowTS(), tsu:Date.now(), ch:'text', out:'Text sent — ' + st});
      saveNotes(); queueSync(); toast('Text logged'); go();
    };
    $('txn').onclick = function(){ toast('Not logged as sent'); };
  };
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
  if(fresh)
    n.touches.push({d:today(),ts:nowTS(),tsu:Date.now(),ch:'call',out:o.t});
  n.dials=n.dials||[];
  n.dials.push({d:today(),ts:nowTS(),tsu:Date.now(),ph4:String(digits).slice(-4),oc:o.k});
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
function toast(t){
  var el=$('toast'); el.textContent=t; el.classList.add('on');
  clearTimeout(_toastT);
  _toastT=setTimeout(function(){el.classList.remove('on');},1400);
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
    b.onclick=function(){ lane=b.dataset.l; i=0; render(); };
  });
}
/* Stale-cache heal. iPhone Safari is the named worst offender and a phone quietly serving last
   week's list is the failure most likely to go unnoticed. Never auto-reload once an outcome has
   been logged — that would lose his position mid-sequence. Offer the pill instead. */
var touched=false;
async function freshCheck(){
  try{
    var u=location.pathname.replace(/\/$/,'')+'/index.html';
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
  // NOT while a voicemail script is visible: that block repaints itself, and a full rebuild here
  // would wipe the script mid-read and re-enable buttons for an outcome already logged.
  var vmUp = $('vm') && $('vm').style.display === 'block';
  if(SCREEN==='outcome' && cur && !vmUp){ screenOutcome(); }
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

  // CIOC as nav — tap a beat, get its words.
  b += '<div class="cioc">';
  SCRIPT.cioc.forEach(function(c,ix){ b += '<button data-cioc="'+ix+'"'+(ciocIdx===ix?' class="on"':'')+'>'+esc(c.k)+'</button>'; });
  b += '</div>';
  if(ciocIdx>=0){
    var c=SCRIPT.cioc[ciocIdx];
    b += '<div class="mut" style="font-size:12px">'+esc(c.w)+'</div><div class="say">'+esc(c.s)+'</div>';
  }

  b += '<div class="ltag">IF YOU ONLY GET 15 SECONDS</div><div class="say">'+esc(fillScript(SCRIPT.f15, r))+'</div>';

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
  try{ if(localStorage.getItem('fcTeamKey')){ syncPull().then(function(){ loadNotes(); if(touched) return syncPush(); }).catch(function(){}); } }catch(e){}
});
boot();
</script></body></html>
"""
