"""options_strategy — which path fits THIS homeowner: keep, sell, refinance, or bankruptcy.

WHY THIS EXISTS (2026-09-24, Alejandro): "build a strategy way to skin the cat on the property
according to what the client wants to do with it, either keep it or sell it or refinance or
bankruptcy, whichever is best for their case."

It reads the facts the engine already has on a board row, plus the answers only a conversation
produces (what the owner wants, whether they can carry a payment), and returns ONE recommended path,
the alternatives, who actually does each one, and what the operator must find out before saying
anything. Pure function, no I/O, no network. HOMEOWNER-OPTIONS.md is the human version.

NOT WIRED IN. Nothing on the board, in Call Mode or in any outbound surface calls this yet. It gets
applied to real cases only after the Miami read verification (judgments, documents, mortgages)
reports, because every branch below leans on `eqstate` and the lien chain being right.

FOUR RULES THE CODE ENFORCES, not just states:

1. Only the SELL paths earn us anything, and only the wholesale assignment pays us (by the buyer, at
   closing, through title). Keep, refinance and bankruptcy are hand-offs we are never paid for.
   FS 501.1377(3)(b) forbids collecting anything for "stopping, avoiding, or delaying foreclosure"
   before every promised service is performed; a referral fee on a homeowner's mortgage loan is a
   RESPA section 8 problem; a fee split with a lawyer is a Bar problem. `we_earn` is False on each.
2. "Keep the house by selling it to us and renting or buying it back" is never offered. Under
   FS 501.1377(5)-(6) that is a foreclosure-rescue transaction: statutory contract, 3-business-day
   cancel, a repurchase price over 17%/yr presumed unconscionable, and a lease-option presumed to
   be a MORTGAGE. There is no path id for it on purpose.
3. No equity number drives a SELL recommendation unless `eqstate` is a FACT (clear / priced). A
   ceiling or a guess yields `needs_facts` and the payoff question, never a verdict.
4. A bankruptcy stay, a transferred title or a dismissed case stops the analysis. The stay is a
   verdict produced elsewhere (sale_history / the §362 flags); this module only reads it.

Thresholds (LIST_MIN_DAYS etc.) are operating defaults, not law. Change them here, in one place.
"""

from equity_state import FACT

GOALS = ('keep', 'sell', 'undecided')

# --- operating defaults (judgment calls, not statute) -------------------------------------------
# Fewer calendar days than this to the sale and a clean purchase cannot close in time: the seller's
# 3-business-day cancel window (FS 501.1377 practice, AFTER-THE-YES stage 04) plus closing at least
# 3 business days before the sale plus title/payoff turnaround.
MIN_DAYS_TO_CLOSE = 12
# A retail listing needs roughly 30-45 days to a contract and 30-45 to close. Below this, a listing
# is a bet against the sale date.
LIST_MIN_DAYS = 75
# Equity bands, on the board's equity percentage, used ONLY when eqstate is a FACT.
THIN_PCT = 15      # under this, selling costs or a wholesale discount eat what is left
STRONG_PCT = 30    # Jesse's qualify line (30% indicated equity)
# Inside this many days, a keep-the-house owner needs a lawyer before anything else: the only
# things that move a sale date that close are court filings.
COURT_TIME_DAYS = 30


# --- the paths ---------------------------------------------------------------------------------
# who: 'us' = Biscayne Solutions Group; everything else is a hand-off.
PATHS = {
    'wholesale': {
        'label': 'Sell to a cash buyer (assignable contract)',
        'who': 'us', 'we_earn': True,
        'note': 'Our lane. Fee paid by the end buyer at closing through title, never by the seller.',
    },
    'list': {
        'label': 'List it with a licensed agent',
        'who': 'licensed agent', 'we_earn': False,
        'note': 'Usually nets the owner more when time allows. We take no referral fee: paying one '
                'to an unlicensed person is believed barred by FS ch. 475 (attorney to confirm).',
    },
    'short_sale': {
        'label': 'Short sale or deed in lieu (lender approval)',
        'who': 'servicer + licensed agent or attorney', 'we_earn': False,
        'note': 'Underwater. Only the lender can accept less than the payoff.',
    },
    'reinstate': {
        'label': 'Pay the association judgment or reinstate the loan',
        'who': "owner, with the plaintiff's attorney payoff letter", 'we_earn': False,
        'note': 'Ask the plaintiff attorney for a written reinstatement or payoff figure.',
    },
    'loss_mit': {
        'label': 'Lender workout: repayment plan, forbearance or modification',
        'who': 'servicer + HUD-approved housing counselor (free)', 'we_earn': False,
        'note': 'The counselor is free and exempt under FS 501.1377; send them there.',
    },
    'refinance': {
        'label': 'Refinance or equity loan',
        'who': 'licensed loan originator', 'we_earn': False,
        'note': 'Hand-off only. No points or referral fee to us on a homeowner loan (RESPA sec. 8; '
                'FS ch. 494 licensing believed to apply, attorney to confirm).',
    },
    'court_time': {
        'label': 'Ask the court for time (motion to cancel or reset the sale)',
        'who': 'attorney', 'we_earn': False,
        'note': 'Never promise the sale will stop. Say a lawyer can ask the court for more time.',
    },
    'bankruptcy': {
        'label': 'Bankruptcy consult',
        'who': 'bankruptcy attorney', 'we_earn': False,
        'note': 'We never recommend filing. We say a bankruptcy attorney can tell them whether it '
                'is an option. Free referral, no fee either way.',
    },
    'post_sale': {
        'label': 'After the sale: surplus funds or redemption',
        'who': 'attorney', 'we_earn': False,
        'note': 'Surplus belongs to the former owner (FS 45.032); any cut is capped and lawyer-gated.',
    },
}

NEVER_SAY = (
    'that we can stop the sale',
    'an equity or offer number while eqstate is not verified',
    'that they should file bankruptcy',
    'buy-it-back, rent-back or lease-option as a way to keep the house',
    'that we charge anything, now or later, for keeping the house',
)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _days(lead):
    d = lead.get('days')
    if d is None:
        d = lead.get('days_to_auction')
    if isinstance(d, bool):
        return None
    try:
        return int(d)
    except (TypeError, ValueError):
        return None


def _is_hoa(lead):
    t = str(lead.get('ftype') or lead.get('ctype') or lead.get('case_type') or '').upper()
    return t.startswith('HOA')


def equity_band(lead):
    """'unknown' unless eqstate is a FACT. Then underwater / thin / moderate / strong."""
    if str(lead.get('eqstate') or '') not in FACT:
        return 'unknown'
    if lead.get('eqfake'):
        return 'unknown'
    pct = _num(lead.get('eq'))
    if pct is None:
        pct = _num(lead.get('equity_pct'))
    if pct is None:
        return 'unknown'
    if pct <= 0:
        return 'underwater'
    if pct < THIN_PCT:
        return 'thin'
    if pct < STRONG_PCT:
        return 'moderate'
    return 'strong'


def _blocks(lead):
    out = []
    if lead.get('saleBkAct') or lead.get('sale_bk_active'):
        out.append('Bankruptcy stay is active: no contact of any kind until it lifts.')
    if lead.get('sibclaimed'):
        out.append('Title already transferred through a sibling case: nothing left to decide.')
    if lead.get('lpDismissed'):
        out.append('The foreclosure case was dismissed: there is no sale to plan around.')
    return out


def _pick(primary, alts):
    seen, clean = set(), []
    for p in [primary] + list(alts):
        if p and p not in seen:
            seen.add(p)
            clean.append(p)
    return (clean[0] if clean else None), clean[1:]


def _keep(lead, facts, days, band, notes, ask):
    hoa = _is_hoa(lead)
    can_pay = facts.get('can_pay')
    alts = []
    if days is not None and days < COURT_TIME_DAYS:
        notes.append('Sale is %d days out. Only a court filing moves it that close; lawyer first.' % days)
        primary = 'court_time'
        alts += ['reinstate' if hoa else 'loss_mit', 'bankruptcy']
    elif can_pay is None:
        ask.append('Going forward, can you afford the regular payment if the back amount were handled?')
        primary = 'reinstate' if hoa else 'loss_mit'
        alts += ['bankruptcy']
    elif can_pay:
        primary = 'reinstate' if hoa else 'loss_mit'
        alts += ['court_time', 'bankruptcy']
    else:
        notes.append('On what they told us, keeping it has no payment behind it. Say so gently, '
                     'offer the free counselor, and ask whether selling before the sale is worth '
                     'looking at. Do not push.')
        primary = 'loss_mit'
        alts += ['bankruptcy']
    if hoa:
        notes.append('Association case: the first mortgage, if any, survives this sale. Keeping '
                     'means paying the association, not the bank.')
    if band == 'strong' and can_pay and facts.get('occupied', True):
        alts.append('refinance')
    if facts.get('prior_bk_recent'):
        notes.append('A bankruptcy in the past year limits the stay (11 USC 362(c)(3)). The '
                     'attorney has to know this up front.')
    return _pick(primary, alts)


def _sell(lead, facts, days, band, notes, ask):
    if band == 'unknown':
        ask.append('Who holds the first mortgage, roughly what is owed, and is it current?')
        notes.append('Equity is not verified (%s). No number, no offer, no path until the payoff '
                     'is known.' % (lead.get('eqstate') or 'unchecked'))
        return None, []
    if days is not None and days < MIN_DAYS_TO_CLOSE:
        notes.append('Too close to the sale for any purchase to close (need %d+ days). A lawyer '
                     'asking the court for time is the only way a sale can still happen.'
                     % MIN_DAYS_TO_CLOSE)
        return _pick('court_time', ['wholesale'] if band in ('moderate', 'strong') else ['short_sale'])
    if band == 'underwater':
        return _pick('short_sale', [])
    poor = str(facts.get('condition') or '').lower() == 'poor'
    long_runway = days is None or days >= LIST_MIN_DAYS
    if band == 'thin':
        notes.append('Thin equity: a cash discount leaves the owner little. Tell them so.')
        return _pick('list' if long_runway and not poor else 'wholesale', ['short_sale'])
    # moderate or strong
    if long_runway and not poor:
        notes.append('Enough time to list. A listing likely nets them more; say that, then offer '
                     'speed and certainty as the reason to take cash.')
        return _pick('list', ['wholesale'])
    return _pick('wholesale', ['list'] if long_runway else [])


def assess(lead, goal=None, facts=None):
    """lead = a board row (or raw lead). goal = 'keep' | 'sell' | 'undecided' | None, taken from
    the owner's own words. facts = what the conversation produced:

        can_pay          True/False/None  can carry the regular payment going forward
        occupied         True/False       owner lives there (default True)
        condition        'poor' | 'ok'    rough condition
        prior_bk_recent  bool             a bankruptcy case in the last 12 months
        has_attorney     bool             already represented in the foreclosure

    Returns a dict: status ('blocked' | 'needs_goal' | 'needs_facts' | 'ready'), primary,
    alternatives, paths (details for each named path), we_earn, notes, ask, never_say.
    Never raises on a malformed row; unknown fields read as unknown.
    """
    lead = lead if isinstance(lead, dict) else {}
    facts = facts if isinstance(facts, dict) else {}
    goal = str(goal or '').strip().lower() or None
    if goal not in GOALS:
        goal = None
    days = _days(lead)
    band = equity_band(lead)
    notes, ask = [], []
    out = {'goal': goal, 'days': days, 'equity': band, 'eqstate': lead.get('eqstate') or 'unchecked',
           'primary': None, 'alternatives': [], 'notes': notes, 'ask': ask,
           'never_say': list(NEVER_SAY)}

    blocks = _blocks(lead)
    if blocks:
        out.update(status='blocked', notes=blocks)
        return _finish(out)
    if days is not None and days < 0:
        out.update(status='ready', primary='post_sale')
        notes.append('The sale date has passed. Only surplus or redemption questions remain, and '
                     'those go to a lawyer before a word goes out.')
        return _finish(out)
    if facts.get('has_attorney'):
        notes.append('Owner already has a lawyer in the case. Jesse hears first; nothing is '
                     'proposed around their counsel.')
    if goal is None:
        ask.append('Do you want to keep the house, or sell it?')
        out['status'] = 'needs_goal'
        return _finish(out)

    if goal == 'keep':
        primary, alts = _keep(lead, facts, days, band, notes, ask)
    elif goal == 'sell':
        primary, alts = _sell(lead, facts, days, band, notes, ask)
    else:  # undecided: lay both out side by side, recommend neither
        k, ka = _keep(lead, facts, days, band, notes, ask)
        s, sa = _sell(lead, facts, days, band, notes, ask)
        out['keep_side'] = [p for p in [k] + ka if p]
        out['sell_side'] = [p for p in [s] + sa if p]
        primary, alts = None, []
        for p in out['keep_side'] + out['sell_side']:
            if p not in alts:
                alts.append(p)
        notes.append('Undecided: show both sides and let them choose. Do not pick for them.')

    out['primary'], out['alternatives'] = primary, alts
    out['status'] = 'needs_facts' if (primary is None and goal != 'undecided') or ask else 'ready'
    return _finish(out)


def _finish(out):
    named = [p for p in [out.get('primary')] + list(out.get('alternatives') or []) if p]
    for side in ('keep_side', 'sell_side'):
        named += [p for p in out.get(side, []) if p not in named]
    out['paths'] = {p: dict(PATHS[p]) for p in named}
    out['we_earn'] = bool(out.get('primary')) and PATHS[out['primary']]['we_earn']
    return out
