"""phone_src -- WHOSE number is on a lead, and how many numbers survive onto the board.

Split out of foreclosure_leads.py for the same reason phone_rank.py is a module of its own:
make_tracker cannot be imported without Playwright and a warm data directory, so logic that lives
inside it is only reachable by running a full build against real homeowner data. These two passes
decide who gets dialled and who gets the homeowner script, and that is not code to leave untested.

Guarded by _phonesrctest.py (no network, no browser, no board, no real data).
"""
import os
import re

# HOW MANY NUMBERS PER LEAD SURVIVE ONTO THE BOARD.
# skiptrace.py keeps EVERY number the provider returns (skiptrace.py:_collect dedupes and caps
# nothing), but the two merges below used to cut to the first FOUR and the Whitepages merge then
# capped the total at eight. So numbers that were paid for — a second person on the deed, the
# owner's older cell — were dropped at build time and never reached a dial. One constant, used by
# every merge, so the three caps cannot drift apart again the way phtype did.
MAX_PHONES = int(os.environ.get('DEALFLOW_MAX_PHONES', '10'))

# WHERE A NUMBER CAME FROM, one tag per entry of r.phones (parallel array r.phsrc).
#   st = skip trace, matched on the PROPERTY ADDRESS -> the owner, as good as this pipeline gets
#   wp = Whitepages, a person the deed names as an owner
#   hh = Whitepages HOUSEHOLD member at the address — a spouse, an adult child, a tenant. NOT the
#        owner. The play is "is <owner> home?", not "hi <owner>".
#   nm = Whitepages Person layer, matched on the owner's NAME only. A namesake in the same city
#        lands here looking exactly like the owner.
#   ag = the number also appears as this lead's LISTING AGENT (r.zagentphone). Never the owner.
#   xl = the same number sits on three or more DIFFERENT owners' leads. A homeowner's cell does
#        not do that; an office number does.
# Until this existed, all six were the same bare digit string in r.phones and no screen could
# tell them apart — which is how a Compass listing agent got dialled as the homeowner.
PHSRC_TRACE, PHSRC_WP, PHSRC_HOUSEHOLD, PHSRC_NAME = 'st', 'wp', 'hh', 'nm'
PHSRC_AGENT, PHSRC_SHARED = 'ag', 'xl'
# The tags that are NOT the homeowner. The dial queue (call_mode.call_rows) drops these; the board
# keeps them on the row, labelled, because they are still the right people to call — just never
# with the homeowner's opening line.
PHSRC_NOT_OWNER = (PHSRC_AGENT, PHSRC_SHARED)

# How many DIFFERENT owners have to share a number before it stops being a homeowner's.
SHARED_PHONE_MIN_OWNERS = int(os.environ.get('DEALFLOW_SHARED_PHONE_MIN', '3'))


def _last10(v):
    d = ''.join(c for c in str(v or '') if c.isdigit())
    return d[-10:] if len(d) >= 10 else ''


def _phsrc_arr(lead, n):
    """The lead's phsrc array, padded to n. A number with no tag came off the skip trace — every
    build before phsrc existed is that case, and defaulting it to 'unknown' would put the whole
    back catalogue behind the not-the-owner gates."""
    out = list(lead.get('phsrc') or [])
    while len(out) < n:
        out.append(PHSRC_TRACE)
    return out


def tag_shared_numbers(leads, min_owners=None):
    """Tag every number that sits on `min_owners`+ DIFFERENT owners as PHSRC_SHARED.

    -> (leads tagged, distinct numbers tagged)

    A homeowner's cell belongs to that homeowner. A number that turns up under three different
    owners is an office: a realtor, a property manager, a foreclosure-defense firm, a relative who
    handles everybody's paperwork. Nothing in the pipeline looked across leads, so each of those
    rows showed the number as its own owner's and each got the homeowner script.

    THE TEST IS DISTINCT OWNERS, NOT DISTINCT LEADS. One person foreclosing on four rentals is four
    leads and one phone, and that is right — counting leads would tag the numbers of the
    multi-property owners who are the best calls on the board. Owner names normalise crudely
    (letters and digits only), so one person spelled two ways counts twice; at a threshold of three
    that costs precision, not safety. The precise version wants `pkey`, which make_tracker does not
    build until after this runs."""
    if min_owners is None:
        min_owners = SHARED_PHONE_MIN_OWNERS
    owners_by_num = {}
    for r in leads:
        own = re.sub(r'[^A-Z0-9]', '', str(r.get('owners') or '').upper())
        if not own:
            continue
        for p in (r.get('phones') or []):
            n10 = _last10(p)
            if n10:
                owners_by_num.setdefault(n10, set()).add(own)
    shared = {n for n, owners in owners_by_num.items() if len(owners) >= min_owners}
    hits = 0
    for r in leads:
        ph = [str(p) for p in (r.get('phones') or []) if p]
        if not ph:
            continue
        src = _phsrc_arr(r, len(ph))
        touched = False
        for i, n in enumerate(ph):
            if _last10(n) in shared:
                src[i] = PHSRC_SHARED
                touched = True
        if touched:
            r['phsrc'] = src[:MAX_PHONES]
            hits += 1
    return hits, len(shared)


def tag_listing_agents(leads):
    """Tag any dial-list number that is also this lead's listing agent (`zagentphone`). -> n leads.

    listing_status.py already scrapes the agent's number and the board shows it in its own Listing
    panel, so the fact was on the row — it was just never compared against the dial list. On
    2026-09-19 a 561 number on the owner queue was dialled and answered by a Compass listing agent.

    DELIBERATELY A TAG, NOT A DROP. An agent on the listing is a real party to the deal and worth a
    call — just never with the homeowner's opening line, and never first. Dropping the number here
    would also hide it from the opt-out matching downstream, which is the wrong direction on a
    suppression-adjacent array."""
    hits = 0
    for r in leads:
        ap = _last10(r.get('zagentphone'))
        if not ap:
            continue
        ph = [str(p) for p in (r.get('phones') or []) if p]
        if not ph:
            continue
        src = _phsrc_arr(r, len(ph))
        touched = False
        for i, n in enumerate(ph):
            if _last10(n) == ap:
                src[i] = PHSRC_AGENT
                touched = True
        if touched:
            r['phsrc'] = src[:MAX_PHONES]
            hits += 1
    return hits


def propagate_dnc(leads, extra=()):
    """A number DNC-flagged on ANY lead is flagged on EVERY lead that carries it. -> (leads, numbers)

    `phdnc` is per record: the registry flag comes with whichever trace returned the number, and a
    Whitepages copy of the same number is appended as False ("not flagged", never "scrubbed"). So
    one number could be do-not-call on one lead and clean on another, and every surface that reads
    phdnc row by row (board, Morning Worker, Call Mode's queue and its lookup) offered it through
    the clean copy. Flags are only ever ADDED here.

    A lead whose flags changed loses its phone ranking (phrank / phbest) so the ranking pass that
    runs after this rebuilds it: phbest could point at the number that just became DNC.

    `extra`: numbers known to be DNC from outside the rows (every flagged skip-trace number,
    including ones the MAX_PHONES cut dropped, and registry-listed numbers from dnc_scrub.json).
    The merges sort DNC numbers last before cutting, so the flag is the first thing a long list
    loses; without this, another lead's clean copy of a cut number stayed dialable."""
    flagged = {n for n in (_last10(x) for x in (extra or ())) if n}
    for r in leads:
        dnc = r.get('phdnc') or []
        for i, p in enumerate(r.get('phones') or []):
            if i < len(dnc) and dnc[i]:
                n10 = _last10(p)
                if n10:
                    flagged.add(n10)
    if not flagged:
        return 0, 0
    hits, nums = 0, set()
    for r in leads:
        ph = list(r.get('phones') or [])
        if not ph:
            continue
        dnc = list(r.get('phdnc') or [])
        while len(dnc) < len(ph):
            dnc.append(False)
        touched = False
        for i, p in enumerate(ph):
            n10 = _last10(p)
            if n10 in flagged and not dnc[i]:
                dnc[i] = True
                nums.add(n10)
                touched = True
        if touched:
            r['phdnc'] = dnc
            r['phrank'] = []
            r['phbest'] = None
            hits += 1
    return hits, len(nums)
