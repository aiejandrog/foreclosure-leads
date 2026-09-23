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
    conf = str(chain.get('conf') or '').strip().lower()
    liens = [l for l in (chain.get('liens') or []) if isinstance(l, dict)]
    # The source publishes no amounts at all -> a ceiling, whatever any single row happens to carry.
    if conf == 'unpriced':
        return 'unpriced'
    if liens:
        # priced ONLY when every surviving instrument carries a figure. Any gap and the total is
        # unknowable, so the honest answer is the ceiling, not a number the operator would quote.
        if all(l.get('amt') for l in liens):
            return 'priced'
        return 'unpriced'
    # PB shape: instruments counted but never priced, even when conf says otherwise
    if (chain.get('mtg_open_unpriced') or 0) > 0:
        return 'unpriced'
    if conf == 'ok':
        # A BANK SUING TO FORECLOSE IS PROOF OF A MORTGAGE. Salkey (accuracy audit 2026-09-23)
        # rendered VERIFIED CLEAR while a lender's foreclosure sat on the same parcel: the search
        # missed the very mortgage being foreclosed, and "no surviving mortgage" was the one thing
        # the case file itself contradicts. An empty chain cannot prove a negative the lawsuit
        # disproves, so it falls to UNVERIFIED, never to a FACT.
        if lender_foreclosure(lead):
            return 'none'
        # searched the index against a real anchor and found nothing surviving. THAT is the fact.
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
    if (st == 'none' and isinstance(chain, dict) and str(chain.get('conf') or '').lower() == 'ok'
            and lender_foreclosure(lead)):
        lead['eqstate_why'] = ('UNVERIFIED — a lender is foreclosing on this parcel, but the '
                               'recorded search found no open mortgage; the chain missed it')
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
            lead['eqopen'] = (chain.get('mtg_open_unpriced') or 0) or len(_liens)
            _gap = [l for l in _liens if not l.get('amt')]
            if _gap:
                lead['eqgap'] = len(_gap)   # instruments with no published figure
        if str(chain.get('conf') or '').lower() == 'low':
            lead['eqlow'] = True     # common-name search: the trace is less certain
    return st
