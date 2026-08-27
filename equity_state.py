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

    clear      chain traced, nothing survives            -> equity is a FACT
    priced     chain traced, surviving debt with amounts -> equity is a FACT (net of `surv`)
    unpriced   instruments exist, amounts unpublished    -> equity is a CEILING (PB)
    none       chain attempted, could not establish      -> equity is a GUESS
    unchecked  no chain pulled yet                       -> equity is a GUESS

Only `clear` and `priced` may be spoken as fact to a homeowner or to Jose.
"""

FACT = ('clear', 'priced')

LABEL = {
    'clear':     'VERIFIED CLEAR — chain traced, no surviving mortgage found',
    'priced':    'VERIFIED — surviving debt traced and priced',
    'unpriced':  'CEILING ONLY — mortgage(s) recorded but Palm Beach publishes no amounts',
    'none':      'UNVERIFIED — the recorded chain could not be established',
    'unchecked': 'NOT CHECKED — no recorded chain pulled for this lead yet',
}
SHORT = {'clear': 'CLEAR', 'priced': 'VERIFIED', 'unpriced': 'CEILING',
         'none': 'UNVERIFIED', 'unchecked': 'NOT CHECKED'}


def state_of(chain):
    """chain = the per-case record from records_liens / broward_liens / palmbeach_liens
    (or None). Returns one of the five states above. Never raises, never guesses upward."""
    if not chain or not isinstance(chain, dict):
        return 'unchecked'
    conf = str(chain.get('conf') or '').strip().lower()
    liens = chain.get('liens') or []
    # priced: we hold actual dollar figures for surviving debt
    if liens and any((l or {}).get('amt') for l in liens if isinstance(l, dict)):
        return 'priced'
    if conf == 'unpriced':
        return 'unpriced'
    # PB shape: instruments counted but never priced, even when conf says otherwise
    if not liens and (chain.get('mtg_open_unpriced') or 0) > 0:
        return 'unpriced'
    if conf in ('ok', 'low') and not liens:
        # searched the index and found nothing surviving. 'low' = common owner name, so the
        # search itself is less certain -> still a fact, but the operator sees the caveat.
        return 'clear'
    if liens:
        return 'priced'
    if conf == 'none':
        return 'none'
    return 'unchecked' if not conf else 'none'


def apply(lead, chain):
    """Stamp `eqstate` (+ label/short) onto a board lead. Call this for EVERY lead, including
    the ones whose chain came back empty — that emptiness is the finding."""
    st = state_of(chain)
    lead['eqstate'] = st
    lead['eqstate_why'] = LABEL[st]
    if isinstance(chain, dict):
        # how hard did we look? an operator deserves to see 30-records-examined vs 0.
        if chain.get('nrec') is not None:
            lead['eqrecs'] = chain.get('nrec')
        if chain.get('traced'):
            lead['eqtraced'] = chain.get('traced')
        if chain.get('chain_note'):
            lead['eqnote'] = chain.get('chain_note')
        if st == 'unpriced':
            lead['eqopen'] = chain.get('mtg_open_unpriced') or 0
        if str(chain.get('conf') or '').lower() == 'low':
            lead['eqlow'] = True     # common-name search: the trace is less certain
    return st
