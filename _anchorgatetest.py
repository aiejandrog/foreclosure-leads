#!/usr/bin/env python
"""_anchorgatetest — an UNANCHORED lead must never reach an outbound queue. Fixtures are fake.

Run:  python _anchorgatetest.py    (exit 0 = safe)

WHAT THIS PINS. A Broward lis pendens carries no legal description and no parcel, so
fl_lp/broward_resolve.py finds the property by the DEFENDANT'S NAME. When that name sits on
several parcels, nothing in the filing says which one the case is about. 196 Broward rows landed
there; broward_pin.py settled 123 off the foreclosed mortgage's PIN or the filing's plat book and
page, and the rest cannot be settled from any record we can read.

Before this suite, the only thing carrying that state was English prose in an advisory field
("ONE OF 8 PARCELS: ..."), which no gate parsed. 7 of 17 contacted rows were the wrong property.
So the rule is now a code — diligence_flags.PARCEL_UNANCHORED, in _HOLD_ALWAYS — and these checks
fail the moment an unanchored row can reach Call Mode's dial queue, the Morning Worker, the text
batch or the Closers cockpit.

The two directions matter equally. A gate that holds everything is a gate that gets switched off
inside a week (diligence_gate.py's own docstring, and it is right), so every fixture below has an
anchored twin that must still ship.
"""
import os
import sys

fails = []
ran = []


def chk(name, cond):
    ran.append(name)
    if not cond:
        fails.append(name)


import diligence_flags as DF      # noqa: E402
import diligence_gate as DG       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# A lis pendens row in the shape lp_leads.py emits: no judgment, no auction, no equity percentage.
def lp_row(**kw):
    r = {'case': 'CACE-26-000001', 'county': 'BROWARD', 'st': 'LP', 'stage': 'LP',
         'owners': 'ROE,MARY', 'oname': 'Mary Roe', 'addr': '', 'folio': '', 'mail': '',
         'value': 0, 'judg': 0, 'eq': None, 'eqfake': False, 'days': 9999, 'auction': '',
         'phones': ['9545550101'], 'phdnc': [False], 'phsrc': ['st'], 'phrank': [''], 'phbest': 0,
         'emails': [], 'vac': False, 'warn': '', 'ctype': 'Bank/Mortgage', 'ftype': 'MORTGAGE'}
    r.update(kw)
    return r


UNANCHORED = lp_row(case='CACE-26-000002', unanchored=True, anchorN=8,
                    addrGuess='ONE OF 8 PARCELS: 10 EAST ST · 20 WEST ST · 30 NORTH ST · 40 SOUTH AVE …',
                    anchorWhy='BCPA: 8 parcels match the full name — ambiguous, needs a human')
ANCHORED = lp_row(case='CACE-26-000003', addr='10 EAST ST, Sample City, FL 33301',
                  folio='555501210050')
# A row baked before lp_leads learned to stamp the state: prose in the advisory field and nothing else.
LEGACY = lp_row(case='CACE-26-000004',
                addrGuess='ONE OF 3 PARCELS: 10 EAST ST · 20 WEST ST · 30 NORTH ST',
                addrWhy='AMBIGUOUS — BCPA: 3 parcels match the full name')
# A single resolved parcel the cadastral could not corroborate. lp_leads puts the whole address in
# the advisory field. ONE parcel is not a coin flip, and holding these would empty the medium pool.
MEDIUM = lp_row(case='CACE-26-000005', addrGuess='20 WEST ST, Sample City, FL 33301',
                addrWhy='medium confidence — BCPA: exactly one parcel carries every token')

# --- 1. the predicate ----------------------------------------------------------------------------
chk('predicate: the explicit stamp is believed, with its count',
    DF.parcel_unanchored(UNANCHORED) == 8)
chk('predicate: an anchored row is 0', DF.parcel_unanchored(ANCHORED) == 0)
chk('predicate: a pre-stamp row is caught by its advisory prose',
    DF.parcel_unanchored(LEGACY) == 3)
chk('predicate: ONE resolved parcel is not ambiguous, whatever the advisory field says',
    DF.parcel_unanchored(MEDIUM) == 0)
chk('predicate: an explicit unanchored:False wins over stale prose',
    DF.parcel_unanchored(lp_row(unanchored=False, addrGuess='ONE OF 4 PARCELS: a · b')) == 0)
chk('predicate: a stamp with no usable count still reads as ambiguous',
    DF.parcel_unanchored(lp_row(unanchored=True)) >= 2)
chk('predicate: junk never raises', DF.parcel_unanchored(None) == 0
    and DF.parcel_unanchored('nope') == 0 and DF.parcel_unanchored({}) == 0)

# --- 2. the flag, and that it BLOCKS rather than merely annotating --------------------------------
chk('flag: PARCEL_UNANCHORED is in _HOLD_ALWAYS, so the gate can act on it',
    'PARCEL_UNANCHORED' in DF._HOLD_ALWAYS)
_f = [f for f in DF.risk_flags(UNANCHORED) if f['code'] == 'PARCEL_UNANCHORED']
chk('flag: risk_flags raises it on an unanchored row', len(_f) == 1)
chk('flag: it is critical', _f and _f[0]['sev'] == DF.SEV_CRITICAL)
chk('flag: the reason names how many parcels', _f and '8' in _f[0]['msg'])
chk('flag: the action says settle the parcel, not retry the guess',
    _f and 'broward_pin' in _f[0]['action'])
chk('flag: an anchored row does not raise it',
    not [f for f in DF.risk_flags(ANCHORED) if f['code'] == 'PARCEL_UNANCHORED'])
chk('flag: a pre-stamp row raises it too',
    [f for f in DF.risk_flags(LEGACY) if f['code'] == 'PARCEL_UNANCHORED'])
chk('flag: one resolved parcel does not raise it',
    not [f for f in DF.risk_flags(MEDIUM) if f['code'] == 'PARCEL_UNANCHORED'])

chk('contact_gate: holds an unanchored row', DF.contact_gate(UNANCHORED)['hold'] is True)
chk('contact_gate: does not hold its anchored twin', DF.contact_gate(ANCHORED)['hold'] is False)

# --- 3. the policy gate every drop site actually calls --------------------------------------------
g_un, g_an, g_leg, g_med = (DG.gate(UNANCHORED), DG.gate(ANCHORED), DG.gate(LEGACY), DG.gate(MEDIUM))
chk('gate: unanchored -> hold', g_un['hold'] is True)
chk('gate: and the code says which rule did it', g_un['code'] == 'PARCEL_UNANCHORED')
chk('gate: the hold is a verdict, not an unevaluable row', g_un['unchecked'] is False)
chk('gate: anchored -> released', g_an['hold'] is False)
chk('gate: a pre-stamp row -> hold', g_leg['hold'] is True)
chk('gate: one resolved parcel -> released', g_med['hold'] is False)

bf = DG.board_fields(UNANCHORED)
chk('board: the browser is told (ddhold)', bf.get('ddhold') is True)
chk('board: with the code', bf.get('ddcode') == 'PARCEL_UNANCHORED')
chk('board: and a sentence a human can read', 'parcel' in (bf.get('ddwhy2') or '').lower())
chk('board: an anchored row carries no hold', not DG.board_fields(ANCHORED).get('ddhold'))

# --- 4. the escape hatch does not reach this one --------------------------------------------------
# DEALFLOW_DILIGENCE_GATE=off exists so a policy nobody can afford does not get deleted instead.
# "We do not know which house this is" is not a policy, so it is not switchable.
_prev = os.environ.get('DEALFLOW_DILIGENCE_GATE')
os.environ['DEALFLOW_DILIGENCE_GATE'] = 'off'
try:
    chk('gate off: PARCEL_UNANCHORED is still held', DG.gate(UNANCHORED)['hold'] is True)
    chk('gate off: and it says so rather than pretending the gate is on',
        'GATE IS OFF' in (DG.gate(UNANCHORED)['why'] or '').upper())
    chk('gate off: an ordinary hold IS released, so the hatch still works',
        DG.gate(lp_row(case='CACE-26-000006', title_status='transferred',
                       title_owner='SOMEONE ELSE LLC'))['hold'] is False)
    chk('gate off: PARCEL_UNANCHORED is the only code immune',
        DG.NEVER_RELEASED == ('PARCEL_UNANCHORED',))
finally:
    if _prev is None:
        os.environ.pop('DEALFLOW_DILIGENCE_GATE', None)
    else:
        os.environ['DEALFLOW_DILIGENCE_GATE'] = _prev

# --- 5. THE QUEUE ITSELF. Everything above is theory until the dialer refuses the row. ------------
import call_mode as CM           # noqa: E402

rows, _n = CM.call_rows([UNANCHORED, ANCHORED], max_days=60)
_cases = {r.get('c') or r.get('case') for r in rows}
chk('dial queue: the anchored row ships', 'CACE-26-000003' in _cases)
chk('dial queue: the unanchored row does NOT', 'CACE-26-000002' not in _cases)
rows2, _n2 = CM.call_rows([LEGACY], max_days=60)
chk('dial queue: a pre-stamp row does not either', not rows2)

# --- 6. the board's own surfaces, asserted against the template source ---------------------------
# The Morning Worker, the lane counts and the Closers cockpit all gate on r.ddhold and nothing
# else. That is the contract the Python hold relies on, so it is checked here rather than assumed.
_tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
chk('template: _workerEligible refuses a held row', 'if(r.ddhold) return false;' in _tpl)
chk('template: the Closers cockpit refuses a held row',
    _tpl.count('if(r.ddhold) return false;') >= 2)
chk('template: the lane counts mirror it, or a lane promises work the queue refuses',
    'if(r.ddhold){ s.suppressed++; return; }' in _tpl)
chk('template: the row says UNANCHORED in its own words, not just "diligence hold"',
    'UNANCHORED &mdash; PROPERTY NOT IDENTIFIED' in _tpl)
chk('template: the unanchored chip renders even when the Python bake did not run',
    'if(r.unanchored){' in _tpl)

# --- 7. lp_leads stamps it, so the state is produced and not only consumed ------------------------
_lp = open(os.path.join(HERE, 'lp_leads.py'), encoding='utf-8').read()
chk('lp_leads: stamps unanchored', "out[-1]['unanchored'] = True" in _lp)
chk('lp_leads: with the candidate count the chip and the flag print',
    "out[-1]['anchorN'] = len(_cands)" in _lp)
chk('lp_leads: the stamp needs 2+ candidates AND no resolved address',
    'unanchored = (not addr) and len(_cands) >= 2' in _lp)
chk('lp_leads: the build prints the count, zero included',
    'row(s) UNANCHORED' in _lp)

if fails:
    for f in fails:
        print('FAIL:', f)
    print('%d check(s) failed' % len(fails))
    sys.exit(1)
print('broward anchor gate: %d/%d checks pass' % (len(ran), len(ran)))
