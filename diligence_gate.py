#!/usr/bin/env python
"""diligence_gate.py — the ONE place the diligence verdict becomes a contact decision.

diligence_flags.py answers "what is wrong with this lead". THIS file answers the only question a
door route, a dialer or a mail merge actually has: "does a human get put in front of this person
today, yes or no, and if no — what do I tell whoever asks why the queue got smaller?"

They are deliberately two files. diligence_flags is EVIDENCE and must stay pure; policy is a
business call that changes when Alejandro decides it changes, and it must be changeable in one
place without touching a single rule. Nothing here re-derives a rule, re-implements a threshold or
carries a second HOA regex — every fact comes from diligence_flags.contact_gate().

------------------------------------------------------------------------------------------------
THE POLICY, AND WHY IT IS NOT JUST `if is_hold(): continue`
------------------------------------------------------------------------------------------------
contact_gate() holds 987 of the 1,940 live rows (51%). Shipping that verbatim would have taken the
Palm Beach board from 314 contactable rows to 36 and emptied the entire lis-pendens pool, which is
the freshest inventory in the business. More to the point it would have held Kevin Markey — 8270
Phoenician Ct, Davie, $839,540 homestead against a $3,207 HOA lien, the clearest genuine-equity
file we have open — for the same reason it holds Sisavath. That is not a gate, that is a shutdown
with a green light on it, and a gate nobody can afford to leave on gets turned off inside a week.

So one code, and only one, gets a policy split. EQ_UNRELIABLE covers two data states that look
identical to the flag builder and are opposites in the field:

  * A DEBT FIGURE WAS CAPTURED AND THE BOARD SUBTRACTED IT.  ->  BLOCK.
    Sisavath: value $225,675 minus a $20,323 judgment that is the HOA'S, printed as 91% / $205,351,
    with a ~$298,000 first and a HUD partial claim nowhere on the row. The board performed
    arithmetic and the answer is WRONG. A closer read it out loud. 121 live rows are in this class.

  * NO DEBT WAS EVER CAPTURED (owner_owed_of(row) == 0).    ->  RELEASE, with the reason attached.
    Markey: judg 0, so "$839,540 equity" is not a computed number at all, it is the value with
    nothing subtracted because nothing has been found yet. A lis pendens IS the start of a case —
    no judgment has been entered, so there is nothing to have got wrong. 389 live rows, essentially
    the whole LP pool.

BE HONEST ABOUT WHAT THAT CARVE-OUT COSTS. Both classes print a pitchable dollar figure on the
card, and a closer who reads $839,540 off Markey's row is making the same mistake in kind as the
one who read 91% off Sisavath's. The difference is that on Markey the outreach itself is sound
("a lis pendens was filed against your property") and only the NUMBER is unsafe, while on Sisavath
there is no equity conversation to have at any volume. So the carve-out does not release the number
— it releases the CONTACT and marks the row `warn`, and every surface that shows a released-with-
warning row must show the reason with it. That is why gate() returns a `why` on a released row too,
and why the board bakes ddwarn/ddwarnwhy next to ddhold.

------------------------------------------------------------------------------------------------
FAIL OPEN, LOUDLY — and this is the ONE place that disagrees with diligence_flags
------------------------------------------------------------------------------------------------
contact_gate() fails CLOSED on purpose: an unreadable row returns hold=True, because "I could not
check this" must not read as "safe to call a distressed homeowner". That is right for a decision.
It is wrong for a BUILD. This gate runs inside make_tracker's bake loop, inside bsg_daily_routes at
06:30 unattended, inside the nightly CRM push. A board that serves nothing is worse than one bad
row, and a gate that can empty a queue on a parse error is a gate that will one day empty it.

So the split is by FAILURE CLASS, not by convenience:
  * A row the gate could EVALUATE and decided to hold  -> held. That is a verdict.
  * A row that could not be evaluated at all (non-dict, GATE_ERROR, an exception in this module,
    diligence_flags missing entirely) -> RELEASED, counted separately as `unchecked`, and printed
    on its own line so it never hides inside the hold count. A pile of these is a caller bug, not a
    pile of bad leads, and it must look like a bug.
Never a traceback, never a partial queue, never a silent zero.

------------------------------------------------------------------------------------------------
USAGE — three lines at any drop site
------------------------------------------------------------------------------------------------
    import diligence_gate as DG
    tally = DG.Tally()
    ...
    g = tally.check(r)                 # counts it, remembers the reason, never raises
    if g['hold']:
        continue
    ...
    tally.report('door routes')        # prints the held count + why, or one line saying zero

`tally.check()` is `gate()` plus the bookkeeping. Use it. A suppression that removes rows silently
is indistinguishable from a queue that was always this size — that sentence is already written in
call_mode.py:733 about the identity ledger and it is the whole reason this file prints.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Import is guarded. If diligence_flags is missing or broken at import time, every contact path in
# the repo still runs — it just runs UNGATED and says so on every report, which is the loudest
# possible version of "this protection is not on right now".
try:
    import diligence_flags as _DF
    _IMPORT_ERR = ''
except Exception as _e:                                        # pragma: no cover - import guard
    _DF = None
    _IMPORT_ERR = str(_e)[:160]


# --------------------------------------------------------------------------------------------
# POLICY
# --------------------------------------------------------------------------------------------
# WHICH CODES BLOCK IS NOT DECIDED HERE. These are READ from diligence_flags, not copied.
#
# _HOLD_ALWAYS fires on the data; _HOLD_ON_DIVE fires only on a lead that shows a real equity
# position, because on a lead with nothing to sell there is nothing to get wrong. Two earlier drafts
# of this file got that split wrong in opposite directions and both are worth remembering:
#   * copying the tuples and testing membership flat dropped the dive condition — HOA_CODEFENDANT
#     holds went 15 -> 77, RECENT_SALE 13 -> 29.
#   * "delete eqfake from a copy and re-ask contact_gate" looked elegant and was worse: eqfake is
#     what SUPPRESSES the dive, so removing it made 389 lis-pendens rows look like real equity leads
#     and they all came back held on HIGH_EQUITY_UNVERIFIED. A counterfactual row is not this row.
# Reading the real tuples means a code the rules owner adds over there is enforced here the same
# day, with no second list to remember. The literals are a fallback for an older diligence_flags
# only; if they are ever the thing in use, the module has changed shape and this needs a human.
# THE SPLIT IS AFFIRMATIVE-EVIDENCE vs ABSENCE-OF-EVIDENCE, and that is the whole policy.
#
# The previous version keyed the one carve-out on `owner_owed_of(row) <= 0` — it released a lead
# BECAUSE no debt figure had been captured. That is backwards in the most expensive way: the gate
# got STRICTER as the data got BETTER. Measured, not theorised: Markey passed only because his
# judgment field was empty, and putting his real $3,207 HOA lien on the row — which is exactly what
# the enrichment pipeline exists to do — flipped him to HELD. A gate that punishes enrichment is a
# gate that gets switched off the first week it blocks a live deal.
#
# So: hold on what we KNOW, warn on what we DON'T.
#
#   HOLD  — the row carries affirmative evidence the equity pitch is FALSE. Not a gap, a fact.
#           UNDERWATER        debt >= value, arithmetic on captured numbers
#           PURCHASE_ANCHOR   they paid >= today's value, with a date
#           SOLD_ABOVE_VALUE  last recorded sale >= today's value (Sisavath: $315,000 vs $225,675)
#           TITLE_TRANSFERRED they do not own it
#           SIBLING_CLAIMED   another case already took it
#
#   WARN  — we do not KNOW the equity. That makes quoting a NUMBER wrong; it does not make the
#           CALL wrong, and the call is how the number gets found. These ride out with the reason
#           attached and every surface must show it.
#           EQ_UNRELIABLE · HIGH_EQUITY_UNVERIFIED · PARTIES_UNAVAILABLE
#
# Sisavath still holds, on SOLD_ABOVE_VALUE — evidence, not a missing field, so it survives her row
# being enriched. Markey passes at EVERY data state including fully verified, which is the property
# the old carve-out could not offer at any data state.
_ALWAYS = tuple(getattr(_DF, '_HOLD_ALWAYS', ()) or
                ('TITLE_TRANSFERRED', 'SIBLING_CLAIMED', 'UNDERWATER', 'PURCHASE_ANCHOR',
                 'SOLD_ABOVE_VALUE')) if _DF else ()
# HOA_CODEFENDANT and RECENT_SALE stay dive-gated holds: a live association case behind our own,
# and a fresh sale on a distressed parcel, are both affirmative facts about the FILE, not gaps.
_ON_DIVE = tuple(getattr(_DF, '_HOLD_ON_DIVE', ()) or
                 ('HOA_CODEFENDANT', 'RECENT_SALE')) if _DF else ()

# Unknowns. Never hold on their own; always travel as a warning with the reason attached.
_WARN_ONLY = ('EQ_UNRELIABLE', 'HIGH_EQUITY_UNVERIFIED', 'PARTIES_UNAVAILABLE')

# Never block on these. They mean "the gate itself did not work", and per the fail-open rule above
# that is a caller bug to fix, not a lead to suppress.
UNCHECKED_CODES = ('GATE_NO_ROW', 'GATE_ERROR', 'GATE_UNAVAILABLE')

# The conditional one. See the policy essay in the module docstring.
CONDITIONAL_CODE = 'EQ_UNRELIABLE'

# Escape hatch, and it is deliberately awkward to reach. DEALFLOW_DILIGENCE_GATE=off releases
# everything but every report still prints, in full, what WOULD have been held — so turning the
# gate off cannot quietly become the same state we were in before it existed.
def _enabled():
    return str(os.environ.get('DEALFLOW_DILIGENCE_GATE', 'on')).strip().lower() not in ('off', '0', 'no')


# --------------------------------------------------------------------------------------------
# ownership.json — the single biggest lever on how much this gate holds
# --------------------------------------------------------------------------------------------
# HIGH_EQUITY_UNVERIFIED fires at SEV_HIGH purely because `title_status` is empty, and it is empty
# on every path that reads a raw *_leads.json instead of the board's `slim` (make_tracker is the
# ONLY place that stamps it, foreclosure_leads.py:2391-2397). Where ownership_scan has already run
# and come back 'clear', the flag does not fire at all — so merging this in is not leniency, it is
# refusing to hold a lead for a check that already happened. Loaded once, memoised, never mutated.
_OWN = None


def ownership():
    """{case: {title_status, title_flag, title_owner, title_evidence}} — {} if the file is absent."""
    global _OWN
    if _OWN is None:
        try:
            with open(os.path.join(HERE, 'ownership.json'), encoding='utf-8') as fh:
                d = json.load(fh)
            _OWN = d if isinstance(d, dict) else {}
        except Exception:
            _OWN = {}
    return _OWN


def _with_title(row):
    """Row as-is when it already carries a title stamp; otherwise a SHALLOW COPY carrying one.

    Copy, never mutate: several callers hand us a row they are about to serialise (sheets_crm's
    twin payload, outreach_mail's queue), and quietly growing four keys on somebody else's dict is
    how a field ends up in a published artifact nobody meant to publish.
    """
    if not isinstance(row, dict):
        return row
    if row.get('title_status'):
        return row
    try:
        og = ownership().get(_DF.case_of(row)) if _DF else None
    except Exception:
        og = None
    if not isinstance(og, dict) or not og.get('title_status'):
        return row
    r = dict(row)
    r['title_status'] = og.get('title_status', '')
    r['title_flag'] = og.get('title_flag', '')
    r['title_owner'] = og.get('title_owner', '')
    r['title_evidence'] = og.get('title_evidence', '')
    return r


# --------------------------------------------------------------------------------------------
# THE GATE
# --------------------------------------------------------------------------------------------
_UNCHECKED_ACTION = ('Fix the caller and re-run. The lead was RELEASED, not held — an unevaluable '
                     'row must never shrink a queue silently, and it must never look like a clean one.')


def _unchecked(why):
    # OUR verdict first, in our own words. contact_gate's message for these paths ends with
    # "Holding — an unreadable row must never come out the same door as a clean one", which is its
    # correct answer and the OPPOSITE of what we just did. Passing that through verbatim would put
    # a line in the build log that says "holding" next to a lead that shipped — a log that
    # contradicts the behaviour is worse than no log.
    return {'hold': False, 'warn': True, 'unchecked': True, 'code': 'GATE_UNAVAILABLE',
            'sev': '', 'codes': [],
            'why': 'NOT DILIGENCE-CHECKED, and released anyway so the build could finish — treat '
                   'this lead as unverified. ' + _clip(why, 200),
            'action': _UNCHECKED_ACTION}


def gate(row):
    """The contact decision for ONE lead. Never raises, never mutates, never returns None.

      {'hold':      bool   True = do NOT put a human on this today
       'warn':      bool   True = show the reason even though it is going out (or it is unchecked)
       'unchecked': bool   True = the gate could not evaluate it; released, count separately
       'code':      str    the code that decided it ('' when clean)
       'sev':       str    critical|high|med|low|''
       'codes':     [str]  every code on the row, most severe first
       'why':       str    plain English, print this at the drop site and on the card
       'action':    str    what to do about it}
    """
    if _DF is None:
        return _unchecked('diligence_flags could not be imported (%s), so NOTHING is being '
                          'diligence-checked on this run.' % (_IMPORT_ERR or 'unknown error'))
    try:
        r = _with_title(row)
        g = _DF.contact_gate(r)
        codes = [c for c in (g.get('codes') or []) if c]

        # The module could not read the row. Its verdict is hold; our build rule says release and
        # shout. Both are right for their own job — see the docstring.
        if any(c in UNCHECKED_CODES for c in codes):
            return _unchecked(g.get('why') or 'The diligence module could not evaluate this row.')

        # SUBTRACTIVE GUARANTEE. contact_gate's release is final: this file can only ever hold FEWER
        # leads than diligence_flags would, never more. Any future edit that makes this branch hold
        # is a bug, and the selftest pins it across every live row.
        if not g.get('hold'):
            # Clean, or flagged-but-released by the dive gate. One thing still has to travel: an
            # eqfake row that never reached a hold is still a row whose printed equity is a guess,
            # and a closer reading it off the card cannot tell.
            if CONDITIONAL_CODE in codes:
                m, a = _flag_msg(r, CONDITIONAL_CODE)
                return _v(False, True, CONDITIONAL_CODE, g, codes, m, a)
            return _v(False, False, '', g, codes, '', '')

        # Which of this row's codes are actually load-bearing, judged with the row's OWN dive state.
        dive = _dive(r)
        blocking = [c for c in codes if c in _ALWAYS or (dive and c in _ON_DIVE)]

        # ---- UNKNOWNS NEVER HOLD ALONE ----------------------------------------------------
        # _ALWAYS / _ON_DIVE above already exclude the warn-only codes, so `blocking` cannot contain
        # one. This strip is belt-and-braces against a future edit to those tuples, and it is where
        # the old data-absence carve-out used to live. Nothing here inspects whether a field is
        # missing — that was the bug.
        blocking = [c for c in blocking if c not in _WARN_ONLY]

        if not blocking:
            # Held only by unknowns. Release, and carry the reason: the most severe warn-only code
            # on the row is what the closer needs to see, not a generic "unverified".
            _w = next((c for c in codes if c in _WARN_ONLY), CONDITIONAL_CODE)
            m, a = _flag_msg(r, _w)
            return _v(False, True, _w, g, codes, m, a)

        code = blocking[0]                             # codes[] is already most-severe-first
        why, action = _flag_msg(r, code)
        if not why:                                    # fall back to whatever contact_gate said
            why, action = g.get('why', ''), g.get('action', '')

        if not _enabled():                             # gate switched off via env — say so, release
            return _v(False, True, code, g, codes,
                      'GATE DISABLED (DEALFLOW_DILIGENCE_GATE=off) — this lead would be held: ' + why,
                      action)
        return _v(True, True, code, g, codes, why, action)
    except Exception as e:
        return _unchecked('The diligence gate threw evaluating this lead (%s).' % str(e)[:140])


def _v(hold, warn, code, g, codes, why, action):
    return {'hold': bool(hold), 'warn': bool(warn), 'unchecked': False, 'code': code,
            'sev': g.get('sev', ''), 'codes': codes, 'why': why, 'action': action}


def _dive(row):
    """Does this lead show a real equity position? Gates the Milouse four. False on any error —
    which narrows what holds, consistent with the subtractive guarantee."""
    try:
        return bool(_DF.needs_deep_dive(row))
    except Exception:
        return False


def _owed(row):
    try:
        return float(_DF.owner_owed_of(row) or 0)
    except Exception:
        return 0.0


def _flag_msg(row, code):
    """(msg, action) for one code on this row. ('','') if it is not there — never raises."""
    try:
        for f in _DF.risk_flags(row):
            if f.get('code') == code:
                return (f.get('msg', ''), f.get('action', ''))
    except Exception:
        pass
    return ('', '')


def is_hold(row):
    """Bool convenience. Prefer gate() at a drop site — a caller that logs "diligence hold" with no
    code cannot tell a real hold from a broken row, which is exactly the confusion this file
    exists to prevent."""
    return bool(gate(row).get('hold'))


def board_fields(row):
    """The keys make_tracker bakes onto a board row so the BROWSER can read the same verdict.

    ddhold     bool  policy says hold. Every JS surface gates on this and ONLY this.
    ddwarn     bool  released, but there is something a human must be told first
    ddwhy2     str   the plain-English sentence, for the chip tooltip and the card
    ddcode     str   the code, for filtering/counting in the UI
    ddact      str   what to do about it

    Named ddwhy2/ddcode rather than reusing annotate()'s ddwhy/ddsev on purpose: ddwhy is the
    DEEP-DIVE reason ("why this lead earned the 3-5 minutes") and it is a different sentence from
    the HOLD reason. Collapsing them is how a card ends up explaining the wrong thing.
    """
    g = gate(row)
    if not (g['hold'] or g['warn']):
        return {}
    o = {'ddwhy2': g['why'], 'ddcode': g['code'], 'ddact': g['action']}
    if g['hold']:
        o['ddhold'] = True
    if g['warn'] and not g['hold']:
        o['ddwarn'] = True
    return o


# --------------------------------------------------------------------------------------------
# COUNT AND PRINT
# --------------------------------------------------------------------------------------------
class Tally(object):
    """Bookkeeping for one build. `check(row)` = gate(row) + remember it; `report(label)` prints.

    Prints even when the count is ZERO, because "0 held" and "the gate never ran" are different
    facts and only one of them is good news. Every code line carries a real example case and the
    first ~110 characters of the actual reason — a bare count tells the operator inventory left but
    not what to fix, and a reason nobody can read is a silent hold wearing a number.
    """

    def __init__(self):
        self.held = {}          # code -> count
        self.samples = {}       # code -> (case, why)
        self.warned = 0         # released-with-warning (EQ_UNRELIABLE carve-out, gate-off notices)
        self.unchecked = 0      # could not evaluate -> RELEASED
        self.unchecked_why = ''
        self.seen = 0

    def check(self, row):
        g = gate(row)
        self.seen += 1
        if g['unchecked']:
            self.unchecked += 1
            if not self.unchecked_why:
                self.unchecked_why = g['why']
        elif g['hold']:
            c = g['code'] or '?'
            self.held[c] = self.held.get(c, 0) + 1
            if c not in self.samples:
                self.samples[c] = (_case(row), g['why'])
        elif g['warn']:
            self.warned += 1
        return g

    @property
    def n_held(self):
        return sum(self.held.values())

    def report(self, label, indent='  '):
        """Print the block. Style matches the existing suppression counters in this repo."""
        try:
            n = self.n_held
            if not _enabled():
                print('%s%s: DILIGENCE GATE IS OFF (DEALFLOW_DILIGENCE_GATE=off) — nothing held.'
                      % (indent, label))
            if n:
                print('%s%s: %d lead(s) HELD by the diligence gate — not deleted, held. Each one '
                      'keeps its reason on the board.' % (indent, label, n))
                for c, k in sorted(self.held.items(), key=lambda kv: -kv[1]):
                    case, why = self.samples.get(c, ('', ''))
                    print('%s   %5d  %-24s e.g. %s — %s' % (indent, k, c, case or '?',
                                                            _clip(why, 110)))
            else:
                print('%s%s: 0 held by the diligence gate (checked %d).' % (indent, label, self.seen))
            # LOUD WHEN THE POOL COLLAPSES FOR A FIXABLE REASON — same idiom as the
            # COUNTY-VERIFIED POOL EMPTY warning in bsg_daily_routes.py.
            # HIGH_EQUITY_UNVERIFIED and PARTIES_UNAVAILABLE do not mean "this lead is bad". They
            # mean "nobody has run the check yet" and "the check could not run". Every other code
            # here is a defect computed from data already on the row; these two are a TODO, and the
            # TODO has a command. Measured 2026-08-27: ownership.json held 80 entries against 1,940
            # board rows (4%), and 220 of 583 holds would clear the moment the scanner covered them.
            # Without this line the operator sees a screen that emptied and no way to tell the
            # difference between "these leads are bad" and "run the scraper".
            _todo = self.held.get('HIGH_EQUITY_UNVERIFIED', 0) + self.held.get('PARTIES_UNAVAILABLE', 0)
            if _todo:
                print('%s   of those, %d are NOT a defect — they are an unrun check. Clear them with:'
                      '  python ownership_scan.py --days 45   (ownership.json currently covers %d '
                      'case(s))' % (indent, _todo, len(ownership())))
            if self.warned:
                print('%s%s: %d lead(s) RELEASED WITH A WARNING — the equity number on these is not '
                      'a number, do not quote it.' % (indent, label, self.warned))
            if self.unchecked:
                print('%s%s: !! %d lead(s) COULD NOT BE CHECKED and were RELEASED anyway (%s) — this '
                      'is a caller bug, not a pile of bad leads. Fix it.'
                      % (indent, label, self.unchecked, _clip(self.unchecked_why, 100)))
        except Exception as e:                                 # a REPORT must never kill a build
            print('%s%s: diligence tally report failed (%s)' % (indent, label, str(e)[:80]))


def _case(row):
    try:
        return _DF.case_of(row) if _DF else ''
    except Exception:
        return ''


def _clip(s, n):
    s = ' '.join(str(s or '').split())
    return s if len(s) <= n else s[:n - 1] + '…'


# --------------------------------------------------------------------------------------------
# CLI — `python diligence_gate.py` prints what the policy does to the live board, per file.
# --------------------------------------------------------------------------------------------
def _scan():
    files = ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json', 'lp_leads.json')
    grand = Tally()
    for fn in files:
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            print('%-24s (absent)' % fn)
            continue
        try:
            rows = json.load(open(p, encoding='utf-8'))
        except Exception as e:
            print('%-24s UNREADABLE %s' % (fn, str(e)[:60]))
            continue
        if isinstance(rows, dict):
            rows = list(rows.values())
        t = Tally()
        for r in rows:
            t.check(r)
            grand.check(r)
        print('\n%s  (%d rows)' % (fn, len(rows)))
        t.report('  ' + fn, indent='  ')
    print('\n===== ALL FILES =====')
    grand.report('board total', indent='  ')
    print('  contactable after the gate: %d of %d' % (grand.seen - grand.n_held, grand.seen))


if __name__ == '__main__':
    _scan()
