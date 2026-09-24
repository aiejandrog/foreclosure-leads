"""equity_state — ONE answer to "is this equity number a FACT or a GUESS?", for every lead.

WHY THIS EXISTS (2026-08-27, Alejandro: "i need the equity to be certain ... the whole reason
for the 5 am scraping ... giving me false hope")

The nightly DOES pull the dockets. Measured on the live board the same day:

    BROWARD      247 / 247 live cases had a recorded chain pulled.  ALL of them.
    MIAMI-DADE   351 / 368.
    PALM BEACH   145 / 313.

...and yet only 231 leads on a 1,940-lead board showed any chain at all. The work was done and
then thrown away at the merge, by one line in each of the two merge paths:

    if _h and _h.get('liens'):        # <-- only leads WITH surviving liens got a chain field

A chain that came back EMPTY is not "no data". It is one of two OPPOSITE facts:

    * we searched 30 recorded instruments under this owner and found NO surviving mortgage
      -> the equity is REAL and this is the single strongest lead on the board;   (conf 'ok')
    * we could not establish the chain at all
      -> the equity is a GUESS and must never be pitched.                        (conf 'none')

Both rendered as a blank cell, identical to a lead nobody ever checked. That is precisely the
false hope: 19 Broward leads were VERIFIED FREE AND CLEAR (conf 'ok', 5-30 records examined,
zero surviving mortgages) and sat invisible next to unverified guesses.

Palm Beach has a THIRD state that is structural, not a bug: the Landmark index publishes no
dollar amounts (no consideration column), so a PB chain can prove WHAT instruments exist and
which were released, but never a balance. 226 PB chains sit in exactly that state. Calling it
'resolved' would be a lie; calling it 'nothing' throws away the fact that we know a mortgage
survives. It is a CEILING, and it renders as one.

THE STATES (one field, `eqstate`, on every lead — never absent):

    clear      anchored search, nothing survives         -> equity is a FACT
    priced     every surviving instrument has an amount  -> equity is a FACT (net of `surv`)
    unpriced   instruments exist, total not established  -> equity is a CEILING
    none       chain attempted, could not establish      -> equity is a GUESS
    unchecked  no chain pulled yet                       -> equity is a GUESS

Only `clear` and `priced` may be spoken as fact to a homeowner or to Jose. Two states that used
to reach that pair no longer do -- a low-confidence empty search, and a part-priced lien list.
See `state_of` for why each one was manufacturing certainty it did not have.

`priced` is still RECORDED amounts, never a current payoff. Nothing in a county index knows what
a borrower owes today; interest, arrears and fees are not in the instrument.
"""

FACT = ('clear', 'priced')

LABEL = {
    'clear':     'VERIFIED CLEAR — chain traced, no surviving mortgage found',
    'priced':    'VERIFIED — surviving debt traced and priced',
    'unpriced':  'CEILING ONLY — mortgage(s) recorded, surviving total not established',
    'none':      'UNVERIFIED — the recorded chain could not be established',
    'unchecked': 'NOT CHECKED — no recorded chain pulled for this lead yet',
}
SHORT = {'clear': 'CLEAR', 'priced': 'VERIFIED', 'unpriced': 'CEILING',
         'none': 'UNVERIFIED', 'unchecked': 'NOT CHECKED'}


# A lender is foreclosing on this parcel: there IS a mortgage, whatever the recorded search found.
LENDER_CASE_TYPES = ('Bank/Mortgage',)
LENDER_OWN_CASE_WHY = ('UNVERIFIED — a lender is foreclosing on this parcel, but the recorded search '
                       'found no open mortgage; the chain missed it')


def lender_foreclosure(lead):
    """True when the lead's own case is a lender's mortgage foreclosure (foreclosure_leads.classify).

    Board rows carry the type as `ctype`, raw lead rows as `case_type`.
    """
    if not isinstance(lead, dict):
        return False
    return str(lead.get('ctype') or lead.get('case_type') or '') in LENDER_CASE_TYPES


def state_of(chain, lead=None):
    """chain = the per-case record from records_liens / broward_liens / palmbeach_liens
    (or None). Returns one of the five states above. Never raises, never guesses upward.

    TWO WAYS THIS USED TO GUESS UPWARD (audit 2026-09-21, defects list item 1):

    1. `conf='low'` + an empty lien list returned 'clear', i.e. VERIFIED CLEAR. But 'low' is set
       (records_liens.py) when the search could not be anchored to the parcel or matched more
       than 45 name models -- a search that unreliable cannot prove a NEGATIVE. An empty result
       from it is indistinguishable from having looked in the wrong place, which is the exact
       false hope this module was written to kill. 199 Miami-Dade and 16 Broward cached chains
       sat in that state. Low confidence now reaches 'none' (UNVERIFIED), never a FACT.

       Note the asymmetry, which is deliberate: low + liens FOUND stays priceable. A wrong
       anchor there overstates debt and costs us a lead, which is the safe direction to fail;
       `eqlow` still flags it. Understating debt is what reaches a homeowner.

    2. ONE lien carrying an amount returned 'priced' for the WHOLE list, before `conf='unpriced'`
       was even consulted. A list we cannot total is a CEILING, not a priced chain, so a partial
       or wholly amountless list is 'unpriced' now. `priced` means every surviving instrument is
       accounted for -- and it still means RECORDED amounts, never a current payoff balance.
    """
    if not chain or not isinstance(chain, dict):
        return 'unchecked'
    st = _state_of(chain, lead)
    # AN OPEN LIEN WITH NO PUBLISHED AMOUNT (12-case verification 2026-09-24, defect 1). A city,
    # county, association or tax lien the search found on the parcel, still open, whose amount the
    # index does not publish, means the surviving total is not established: a ceiling, never a
    # FACT, exactly like an unpriced mortgage.
    if st in FACT and (chain.get('other_open_unpriced') or 0) > 0:
        return 'unpriced'
    return st


def _state_of(chain, lead=None):
    conf = str(chain.get('conf') or '').strip().lower()
    liens = [l for l in (chain.get('liens') or []) if isinstance(l, dict)]
    # NO MORTGAGE FOUND IS NOT VERIFIED CLEAR (2026-09-23 accuracy audit). An empty list only proves
    # a negative when the chain says how it looked; one that does not is a GUESS. See
    # coverage_documented for the three things a clear has to show.
    if (conf == 'ok' and not liens and not (chain.get('mtg_open_unpriced') or 0)
            and not coverage_documented(chain)):
        return 'none'
    # The source publishes no amounts at all -> a ceiling, whatever any single row happens to carry.
    if conf == 'unpriced':
        return 'unpriced'
    if liens:
        # priced ONLY when every surviving instrument carries a figure. Any gap and the total is
        # unknowable, so the honest answer is the ceiling, not a number the operator would quote.
        # A mortgage the index lists with no amount (mtg_open_unpriced) is part of that list too.
        if all(l.get('amt') for l in liens) and not (chain.get('mtg_open_unpriced') or 0):
            return 'priced'
        return 'unpriced'
    # PB shape: instruments counted but never priced, even when conf says otherwise
    if (chain.get('mtg_open_unpriced') or 0) > 0:
        return 'unpriced'
    if conf == 'ok':
        # A BANK SUING TO FORECLOSE IS PROOF OF A MORTGAGE. Salkey (accuracy audit 2026-09-23)
        # rendered VERIFIED CLEAR beside a lender's foreclosure: the search missed the very mortgage
        # being foreclosed. An empty chain cannot prove a negative the lawsuit disproves. (A lender
        # suing in a SEPARATE case reaches the row later -- demote_for_bank_fc.)
        if lender_foreclosure(lead):
            return 'none'
        # searched the index against a real anchor, documented how, and found nothing surviving.
        return 'clear'
    if conf in ('low', 'none'):
        return 'none'
    return 'unchecked' if not conf else 'none'


def apply(lead, chain):
    """Stamp `eqstate` (+ label/short) onto a board lead. Call this for EVERY lead, including
    the ones whose chain came back empty — that emptiness is the finding."""
    st = state_of(chain, lead)
    lead['eqstate'] = st
    lead['eqstate_why'] = LABEL[st]
    if st == 'none' and lender_foreclosure(lead) and state_of(chain) == 'clear':
        lead['eqstate_why'] = LENDER_OWN_CASE_WHY
    if isinstance(chain, dict):
        # how hard did we look? an operator deserves to see 30-records-examined vs 0.
        if chain.get('nrec') is not None:
            lead['eqrecs'] = chain.get('nrec')
        if chain.get('traced'):
            lead['eqtraced'] = chain.get('traced')
        if chain.get('chain_note'):
            lead['eqnote'] = chain.get('chain_note')
        if st == 'unpriced':
            # how many instruments we know survive but cannot total. PB reports the count itself;
            # a part-priced list from any county has to be counted here, or the lead renders a
            # CEILING of 0 and reads like a clear one.
            _liens = [l for l in (chain.get('liens') or []) if isinstance(l, dict)]
            lead['eqopen'] = ((chain.get('mtg_open_unpriced') or 0) or len(_liens)) + (chain.get('other_open_unpriced') or 0)
            _gap = [l for l in _liens if not l.get('amt')]
            if _gap:
                lead['eqgap'] = len(_gap)   # instruments with no published figure
        if str(chain.get('conf') or '').lower() == 'low':
            lead['eqlow'] = True     # common-name search: the trace is less certain
    return st


# A LENDER'S FORECLOSURE THE CHAIN NEVER SAW (2026-09-23 accuracy audit, Salkey).
# apply() reads only the recorded chain. Evidence that a lender is foreclosing the SAME property as a
# SEPARATE case reaches the row later in the merge (the chain's `second_fc`, surfaced as `orsecond`;
# an open circuit-court sibling case), after the label is already stamped. A bank suing proves an
# open mortgage, so a CLEAR label beside it is the one claim the court file contradicts. Only ever
# moves CLEAR down, to the same UNVERIFIED a lender foreclosure on the lead's own case gets.
import re

BANK_FC_SEPARATE_WHY = ('UNVERIFIED — a lender is foreclosing this property in a separate case '
                        '(%s); the recorded search found no open mortgage, so it missed one')


def bank_fc_evidence(lead):
    """The separate lender foreclosure on a board row, as display text, or ''.

    orsecond: the lien chain's own parcel-anchored find (records_liens / broward_liens second_fc).
    sib: sibling_cases.py's open circuit-court case against the same defendants. That one is matched
    on the OWNERS, not the parcel, so it counts only on an association case with a med/high match --
    the condo-with-two-cases shape it was built for -- and never when the sibling already sold
    (that row is CLAIMED and leaves every lane anyway).
    """
    if not isinstance(lead, dict):
        return ''
    sec = lead.get('orsecond')
    if isinstance(sec, dict) and (sec.get('case') or sec.get('party')):
        return ' '.join(str(x) for x in (sec.get('case'), sec.get('party')) if x)
    case = str(lead.get('case') or '').upper()
    if '-CC-' in case or case.startswith(('COCE', 'CONO', 'COWE', 'COSO')) or re.match(r'^50\d{4}CC', case):
        for s in (lead.get('sib') or []):
            sc = str((s or {}).get('case') or '').upper()
            if (('-CA-' in sc or sc.startswith('CACE')) and not s.get('sold')
                    and str(s.get('conf') or '') in ('high', 'med')):
                return ' '.join(x for x in (sc, str(s.get('pl') or '')[:40]) if x)
    return ''


def demote_for_bank_fc(lead):
    """Call AFTER the merge has attached orsecond / sib. Returns True when it demoted."""
    if not isinstance(lead, dict) or lead.get('eqstate') != 'clear':
        return False
    what = bank_fc_evidence(lead)
    if not what:
        return False
    lead['eqstate'] = 'none'
    lead['eqstate_why'] = BANK_FC_SEPARATE_WHY % what
    lead['eqbankfc'] = True
    return True


def coverage_documented(chain):
    """May an EMPTY chain be read as clear? Only when it records how it searched.

      * `nrec` -- how many records the search returned, present and above zero. A chain that does
        not say is a cache entry from before anyone wrote it down.
      * not capped / truncated -- a search that stopped early cannot prove nothing is left.
      * `second_fc` present and empty -- the chain asked whether a LENDER is foreclosing the same
        property and found no one. A bank's foreclosure proves an open mortgage (Salkey); the key
        being absent means the question was never asked. A lender filing on one of the owner's
        units in the same building whose unit could not be pinned (`second_fc_unsure`) is not a no.
    Missing amounts are handled before this is reached: they are a ceiling, never zero debt.
    """
    if not isinstance(chain, dict):
        return False
    try:
        n = int(chain.get('nrec') or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0 or chain.get('capped') or chain.get('truncated') or chain.get('parcel_found') is False:
        return False
    return ('second_fc' in chain and not chain.get('second_fc')
            and not chain.get('second_fc_unsure'))
