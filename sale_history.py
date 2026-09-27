#!/usr/bin/env python3
"""sale_history.py — the STALLER signal, from real court-docket sale events (Miami-Dade).

For each MD foreclosure lead, count how many times a foreclosure sale was SET and then CANCELLED /
RESET / RESCHEDULED before now. That "survived N sales" number is the SALAZAR intelligence — it
separates a first-timer (0 survivals, bows to a rescue) from a serial delayer (many survivals, will
almost certainly stall THIS sale too). Filing year alone was a proxy; this is the real thing.

Source: the Miami-Dade Clerk OCS API (fully public, no login) — the SAME endpoint the pipeline already
uses for plaintiff/defendants, so this just mines the docket array it returns:
  GET  /ocs/api/CaseInfo/encrypt/{CASE#}          -> {qs}
  POST /ocs/api/CaseInfo/GetSingleCaseResult?qs=  (body '""')  -> {dockets:[{docketDescrition,eventDate}]}

Cached in sale_history_cache.json with a 7-day TTL (a case's sale count CHANGES when a new sale is
set / cancelled, so unlike a lien chain it must re-check). Writes onto each lead:
  sale_survived  : times a scheduled sale was cancelled/reset (the staller count)
  sale_scheduled : distinct foreclosure-sale notices seen (context)

Miami-Dade only for now — Broward/Palm Beach dockets sit behind different (captcha-walled) clerk
portals, the same wall their lien tracers hit; they fall back to the filing-year FRESH/STALLER proxy.

Two Miami-Dade lanes are read (2026-09-26): the AUCTION list (leads_final.json) and the LIS PENDENS
lane (lp_leads.json, or lis_pendens.json when that is absent) — every LP row whose case is a
Miami-Dade civil number. An LP case lands in the cache under the same key and in the same format as
an auction case, which is what stay_gate.py reads; its board row gets the §362 fields the board
gates on (saleBkAct / saleBkD / saleLift). Order: near sales, then cases never read, then the rest
oldest-read first, so the --limit budget always reaches an unread case before a re-read.

Run:  python sale_history.py [--limit N] [--ttl-days 7] [--case CASE] [--no-lp]
"""
import argparse
import json
import os
import re
import time
from datetime import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'sale_history_cache.json')
CLERK = 'https://www2.miamidadeclerk.gov'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

# A docket line about a foreclosure sale. CANCEL wins over SCHED (a "reset sale" both cancels the old
# one and sets a new one — we count it as a survival, the meaningful signal).
_SALE = re.compile(r'sale|certificate of title', re.I)
_CANCEL = re.compile(r'cancel|reset|reschedul|vacat|continu', re.I)
_SCHED = re.compile(r'notice of\s+.*sale|foreclosure sale|judicial sale', re.I)
_DONE = re.compile(r'certificate of (?:sale|title)', re.I)
# A survival is a cancelled-sale EVENT, not a paper count. Live-docket evidence (2006-019959-CA-01):
#  - every real cancellation writes BOTH a "Motion to Cancel Sale" AND an "Order Cancelling..." line;
#    counting motions inflated ~2x (the v1 bug: 62 'survivals' of 29 scheduled sales);
#  - but orders-only (v2) went blind to clerk-entered "Mortgage Foreclosure Sale Cancelled" EVENT
#    lines - on the flagship, 3 of 4 carry 'CANCELLED PER BANKRUPTCY / COURT ORDER' comments on
#    dates with NO order line. Those are real dodges (bankruptcy = the heaviest staller signal).
# Rule: skip requests (motions) and denials; count granted orders AND clerk event lines, dedup'd
# by date so an order + clerk line for the SAME event counts once (flagship: 27 orders + 3 = 30).
_ORDER = re.compile(r'\border\b', re.I)
# Who moved to postpone (Jose's "bank stalling or owner fighting?" question). MD docket text is
# usually generic ("Motion to Cancel Sale"), so this only fills in when a line actually names the
# party — captured opportunistically, never guessed.
_PLTF = re.compile(r'plaintiff|mortgagee|\bbank\b', re.I)
_DEFT = re.compile(r'defendant|mortgagor|\bowner\b', re.I)

_MOTION = re.compile(r'\bmotion\b', re.I)
_DENY = re.compile(r'deny|denied|denial', re.I)
# 'bankrupt', or the chapter / petition words a bankruptcy order uses without it. 12-case
# verification 2026-09-24, defect 9: orders arrive on the state docket as "Notice of Filing: ..."
# and name the chapter ("Order Reinstating Chapter 13 Case") rather than the word bankruptcy.
_BANKR = re.compile(r'bankrupt|\bchapter\s*(?:7|11|12|13)\b|\bch\.?\s*(?:7|11|12|13)\b|voluntary petition', re.I)
# DISTINCT bankruptcy filings — Jose's strongest staller screen ("they've already done 3-4
# bankruptcies, they know the game"), and a signal the sale-cancel scan is structurally blind to:
# a Suggestion/Notice of Bankruptcy line never contains the word 'sale', and the automatic stay
# halts the sale WITHOUT any cancel order (live-verified: the flagship carries 18 BK docket lines
# across 7 distinct NUMBERED BK cases — kimi's recount is right, my first sample said 4; the 2009 +
# two 2014 numberless filings put the true total at 8-10, and only numbered lines count, so the
# undercount-never-overcount rule holds). Filings are deduped by their federal case number
# (e.g. 24-23467); numberless notices fall back to distinct dates.
_BKDOC = re.compile(r'suggestion of bankruptcy|notice of bankruptcy|bankruptcy stay', re.I)
_BKNUM = re.compile(r'\b(\d{2}-\d{4,6})\b')

CACHE_VER = 5   # v5 = v4 + active automatic-stay detection (sale_bk_active/date) — compliance gate.
                # v3 = event-level counting: granted orders + clerk-entered 'Sale Cancelled' event
                # lines (dedup'd by date), motions and DENIED orders excluded. v2 was orders-only —
                # blind to clerk cancellations (incl. 'CANCELLED PER BANKRUPTCY' lines).


def _bk_count(dockets):
    """Distinct bankruptcy filings on this docket. Case numbers win (one filing spawns many lines —
    notice + stay order + dismissal all cite the same 24-23467); numberless notices add dates.
    Undercounts when the clerk typed no number — never overcounts."""
    nums, dates = set(), set()
    for e in dockets or []:
        t = (e.get('docketDescrition') or e.get('docketDescription') or '')
        c = (e.get('comments') or '')
        if not (_BKDOC.search(t) or (_BANKR.search(c) and _BKDOC.search(t + ' ' + c))):
            continue
        found = _BKNUM.findall(t + ' ' + c)
        if found:
            nums.update(found)
        else:
            dates.add((e.get('eventDate') or '')[:10])
    return len(nums) if nums else min(len(dates), 9)


# --- ACTIVE automatic stay (kimi) ---------------------------------------------------------------
# Counting bankruptcies is a RANKING signal; whether one is OPEN right now is a COMPLIANCE signal.
# 11 U.S.C. §362 halts all collection activity the moment a petition lands — calls, letters,
# door-knocks, WhatsApp — until the case is dismissed/discharged or the stay is lifted. The
# flagship's 7th BK (26-19302-RAM) was filed 2026-07-16 with no closing line after it: the stay
# is LIVE today, and every outreach button on that lead is a federal violation waiting to happen.
_BKFILE = re.compile(r'suggestion of bankruptcy|notice of bankruptcy|order case pending bankruptcy stay', re.I)
_BKCLOSE = re.compile(r'dismiss|discharg|relief from (?:the )?(?:automatic )?stay|'
                      r'lift\w* (?:the )?(?:automatic )?stay|stay (?:is |was )?(?:lifted|terminated|annulled|vacated)|'
                      r'annul\w* (?:the )?stay', re.I)

def _iso_date(us):
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', (us or '').strip())
    return f'{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}' if m else ''

_BKSTART = re.compile(r'suggestion of bankruptcy|notice of bankruptcy', re.I)   # a new petition, not the stay acting
# Stay relief with no bankruptcy word: "relief from stay" and anything naming the AUTOMATIC stay.
# A bare "Order Lifting Stay" is left out: the state court stays its own case too (mediation,
# abatement), and lifting that says nothing about a bankruptcy.
_BKSTAYREL = re.compile(r'relief from (?:the )?(?:automatic )?stay|'
                        r'(?:lift|terminat|annul|vacat)\w* (?:of )?(?:the )?automatic stay|'
                        r'automatic stay (?:is |was )?(?:lifted|terminated|annulled|vacated)|annul\w* (?:the )?stay', re.I)
# A reinstatement, or an order vacating a dismissal: the case is back. Only ever read on a line
# already about a bankruptcy.
_BKREINSTATE = re.compile(r'reinstat|vacat\w*\s+(?:the\s+)?(?:order\s+(?:of\s+)?)?dismiss', re.I)
# A dismissal, stay relief or reinstatement counts only once it is ORDERED. A motion, request or
# hearing notice asking for one is not granted until an order says so, and an order DENYING one is
# the opposite. A line that also orders the thing itself ("Order Granting Motion to Dismiss Chapter
# 13 Case and Denying Motion for Rehearing", "Order Dismissing Chapter 13 Case on Trustee's Motion")
# still counts. The object of a grant or denial is read up to the first "and", so "Order Denying
# Confirmation and Dismissing Chapter 13 Case" denies confirmation and dismisses.
_BKASK = re.compile(r'\bmotion\b|\brequest\b|\bapplication\b|notice of hearing', re.I)
_BKDENIED = re.compile(r'\b(?:deny|denie[sd]|denying|denial of)\s+(?:(?!and\b)\w+\s+){0,3}?'
                       r'(?:motion|request|dismiss|relief|lift|terminat|annul|vacat|discharg|reinstat)', re.I)
# What a grant is OF: the words between "granting" and the thing, up to four, never across an "and"
# and never through a procedural step, so "Granting Motion for Continuance of Relief from Stay
# Hearing" grants a continuance, not relief. A reinstatement's object is the case or the dismissal
# itself: "Granting Motion to Vacate Hearing" vacates a hearing, not the dismissal.
_GRANTOF = r'\bgrant\w*\s+(?:(?!and\b|continu|postpon|reschedul|extend|extens|hearing|conference|shorten|expedit)\w+\s+){0,4}?'
_UNDISMISS = r'\s+(?:the\s+)?(?:order\s+(?:of\s+)?)?dismiss'    # after a form of "vacate"
_BKDONE = {   # what an order says when it does the thing: granted, or the verb in its done form
    'close': re.compile(_GRANTOF + r'(?:dismiss|relief|lift|terminat|annul|discharg)|'
                        r'\b(?:order|judgment)\b.*?\b(?:dismissing|dismissed|terminating|terminated|lifting|lifted|'
                        r'annulling|annulled|discharging|discharged)\b', re.I),
    'reinstate': re.compile(_GRANTOF + r'(?:reinstat|vacat\w*' + _UNDISMISS + r')|'
                            r'\b(?:order|judgment)\b.*?\b(?:reinstating|reinstated|vacat(?:ing|ed)' + _UNDISMISS + r')', re.I),
}


def _bk_not_ordered(tx, kind):
    """True when a closing (kind 'close') or reinstating ('reinstate') line only asks for it, or
    denies it, and does not itself order it."""
    if _BKDONE[kind].search(tx):
        return False
    return bool(_BKDENIED.search(tx) or _BKASK.search(tx))


def _bk_lines(dks):
    """(opens, closes) of the bankruptcy lines on a docket. opens are (ISO date, federal case numbers
    cited, starts-a-petition); closes are (ISO date, case numbers). Closing lines are checked FIRST
    ('Notice of Filing: ...ORDER OF DISMISSAL' contains 'filing' but closes)."""
    rows = []
    for e in dks or []:
        t = (e.get('docketDescrition') or e.get('docketDescription') or '')
        tx = t + ' ' + (e.get('comments') or '')
        iso = _iso_date(e.get('eventDate'))
        if iso:
            rows.append((iso, t, tx, frozenset(_BKNUM.findall(tx))))
    # A line is about a bankruptcy only when it says so: a filing line, the bankruptcy / chapter
    # words, stay relief, or a federal case number an earlier bankruptcy line already cited. A bare
    # "Order of Dismissal" or "Voluntary Dismissal" is about this lawsuit (a defendant, a motion)
    # and used to close a live stay (12-case verification 2026-09-24, defect 9).
    known = frozenset().union(*[n for _, t, tx, n in rows if _BKFILE.search(t) or _BANKR.search(tx)])
    opens, closes = [], []
    for iso, t, tx, nums in rows:
        if not (_BKFILE.search(t) or _BANKR.search(tx) or _BKSTAYREL.search(tx) or (nums & known)):
            continue
        kind = ('reinstate' if _BKREINSTATE.search(tx) else
                'close' if (_BKCLOSE.search(tx) or _BKSTAYREL.search(tx)) else '')
        if kind and _bk_not_ordered(tx, kind):
            continue                                   # asked for or denied: the case stands as it was
        if kind == 'reinstate':
            opens.append((iso, nums, False))           # the case is back: its stay is live again
        elif kind == 'close':
            closes.append((iso, nums))
        else:
            opens.append((iso, nums, bool(_BKSTART.search(t))))  # 'CANCELLED PER BANKRUPTCY' = the stay acting
    return opens, closes


def _bk_events(dks):
    """(opening ISO dates, closing ISO dates) of the bankruptcy lines on a docket."""
    opens, closes = _bk_lines(dks)
    return [o[0] for o in opens], [c[0] for c in closes]


def _bk_cases(dks):
    """The bankruptcy CASES on a docket, oldest first: [(start ISO, case numbers, close ISO or '')].
    A petition line (suggestion / notice of bankruptcy) starts a new case unless it cites only
    numbers one case already has, or is a second numberless petition on the same day. Any
    other bankruptcy line (a stay order, a sale cancelled per bankruptcy, a reinstatement) is the
    current case acting; a line citing a different federal case number is a different case, unless
    that number belongs to an earlier case, in which case it is that earlier case acting.
    A case is closed by a closing line dated on or after its LAST line (a reinstatement reopens it):
    one citing its number counts whatever its date; one citing no number counts only before the
    next case starts, and never for cases filed on the same day as another; a numbered one on a
    numberless case counts in that window when the number belongs to no other case."""
    opens, closes = _bk_lines(dks)
    cases = []                                         # [start, numbers, last line, line dates]
    for d, n, st in sorted(opens, key=lambda o: o[0]):
        cur = cases[-1] if cases else None
        own = [c for c in cases if n and n & c[1]]
        if own and (not st or n <= own[-1][1]):
            cur = own[-1]                              # a line for a case we already have
        elif (cur is None or (n and cur[1] and not (n & cur[1]))
                or (st and not (n and n <= cur[1]) and not (not n and d == cur[0]))):
            cases.append([d, set(n), d, {d}])
            continue
        cur[1] |= n; cur[2] = d; cur[3].add(d)
    out = []
    for i, (st, nums, last, dates) in enumerate(cases):
        end = min([c[0] for c in cases if c[0] > st] or ['9999-99-99'])
        shared = sum(1 for c in cases if c[0] == st) > 1
        others = set().union(*[c[1] for j, c in enumerate(cases) if j != i]) - nums
        hits = [d for d, n in closes if d >= last and (
            (n & nums) if n and nums else
            (d < end and not (n & others)) if n else
            (d < end and not shared))]
        out.append((st, frozenset(nums), max(hits or ['']), frozenset(dates)))
    return out


def _stay_end(cases, since):
    """Close date of the stay made of the case holding a line dated `since` and every case started
    after it ('' while any of them is open, or when no line on this docket is dated `since`: a
    stay ends only on a read that still shows the line that opened it)."""
    held = [c[0] for c in cases if since in c[3]]
    if not held:
        return ''
    ends = [c[2] for c in cases if c[0] >= min(held) or since in c[3]]
    return max(ends) if all(ends) else ''


def _bk_stay(dks):
    """(active, latest_filing_iso, lifted_iso). A bankruptcy filing line (or a sale cancelled PER
    the stay) opens; a dismissal / discharge / stay-relief line closes. Active = a case started on
    the newest petition date has no closing line of its own (_bk_cases), so an OLDER case's late
    dismissal never ends a newer one.
    lifted_iso = the court date the LAST stay closed (when none is active) — the door signal: the
    owner's shield just dropped, the sale is about to be reset, and contact is legal again. The
    freshest-dismissed leads are the most rescuable calls on the board."""
    opens, _ = _bk_events(dks)
    if not opens:
        return False, '', ''
    latest = max(opens)
    lifted = _stay_end(_bk_cases(dks), latest)
    return not lifted, latest, lifted


def _bk_active(dks):
    """kimi's original contract, preserved for its callers/tests."""
    active, latest, _ = _bk_stay(dks)
    return active, latest


def _count(dockets):
    """(survived, scheduled, completed, who) from a docket array. `who` = 'bank'/'owner'/'' —
    which side's postponements dominate, when the docket text names the movant at all."""
    sched = done = pl = df = 0
    cancels = []                                     # (date, is_order, party text) survival candidates
    for e in dockets or []:
        d = (e.get('docketDescrition') or e.get('docketDescription') or '')
        if not _SALE.search(d):
            continue
        if _DONE.search(d):
            done += 1
        elif _CANCEL.search(d):
            if _MOTION.search(d) or _DENY.search(d):
                continue                             # a request or a denial is not a cancelled sale
            ptxt = d + ' ' + (e.get('comments') or '')
            cancels.append((e.get('eventDate', ''), bool(_ORDER.search(d)), ptxt))
        elif _SCHED.search(d):
            sched += 1
    order_dates = {dt for dt, iso, _ in cancels if iso}
    surv = 0
    for dt, iso, ptxt in cancels:
        if iso or dt not in order_dates:             # orders always count; a clerk line counts only
            surv += 1                                # when no same-date order already covers it
            if _BANKR.search(ptxt):
                df += 1                              # bankruptcy is filed BY the owner, always
            elif _PLTF.search(ptxt):
                pl += 1
            elif _DEFT.search(ptxt):
                df += 1
    who = 'bank' if pl > df else ('owner' if df > pl else '')
    return surv, sched, done, who


def _fetch(session, case):
    """OCS docket array for a MD case, or None on any failure."""
    try:
        qs = session.get(f'{CLERK}/ocs/api/CaseInfo/encrypt/{case}', timeout=20).json().get('qs')
        if not qs:
            return None
        d = session.post(f'{CLERK}/ocs/api/CaseInfo/GetSingleCaseResult?qs={qs}',
                         headers={'Content-Type': 'application/json'}, data='""', timeout=20).json()
        if not d or d.get('caseID', -1) == -1:
            return None
        return d.get('dockets') or []
    except Exception:
        return None


def _load_cache():
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE, encoding='utf-8'))
        except Exception:
            return {}
    return {}


# --- LIS PENDENS coverage (2026-09-26) ------------------------------------------------------------
# Until 2026-09-26 this file read leads_final.json and nothing else, i.e. the Miami AUCTION list. The
# ~350 Miami-Dade lis pendens leads on the board's Fresh-filings lane carry a real Miami-Dade civil
# case number (2025-012345-CA-01) the same OCS endpoint answers, and not one of them was ever read.
# So they had no stay verdict anywhere: the board showed every one of them workable (saleBkAct never
# set), and the send bridge's fail-closed gate (stay_gate.py, #72) can only refuse them forever as
# stay_unverified. They are read here now, from the same lane the board merges:
#   lp_leads.json      the board's LP rows, built by lp_leads.py from lis_pendens.json (already
#                      deduped, dismissed/closed flags applied). Preferred, and the only file this
#                      script writes LP stay flags back into.
#   lis_pendens.json   the raw feed, used only when lp_leads.json is absent, with lp_leads.build's
#                      own dedupe (owner required; key = case, else owner+date). Read-only.
# Only rows whose county is Miami-Dade and whose case holds a Miami-Dade civil number are read;
# Broward / Palm Beach LP numbers sit behind other clerks (see the module docstring).
# File NAMES, joined to HERE at call time (not import time), so anything that points HERE somewhere
# else -- the suites do -- moves the LP files with it and can never stamp the real lp_leads.json.
LP_LEADS = 'lp_leads.json'
LP_FEED = 'lis_pendens.json'
_MD_CIVIL = re.compile(r'\b(\d{4}-\d{6}-(?:CA|CC)-\d{2})\b')


def md_civil_case(raw):
    """'2025-012345-CA-01' out of whatever the row carries ('CASE NO 2025-012345-CA-01' included);
    '' when there is no Miami-Dade civil number in it."""
    m = _MD_CIVIL.search(str(raw or '').upper())
    return m.group(1) if m else ''


def _lp_filed_key(r):
    """Sortable filing date from an LP row ('6/8/2026' or '2026-06-08'); '' when unreadable."""
    d = str(r.get('filedDate') or r.get('filed') or r.get('date') or '').strip().split(' ')[0]
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})$', d)
    if m:
        return '%s-%02d-%02d' % (m.group(3), int(m.group(1)), int(m.group(2)))
    return d[:10] if re.match(r'\d{4}-\d{2}-\d{2}', d) else ''


def load_lp_rows(lp_leads=None, lp_feed=None):
    """(rows, whole_file, source) — the Miami-Dade LP rows to read, with their clean case numbers.

    rows = [(case, row)]. whole_file is the full lp_leads.json list (every county, same objects as
    in rows) when the rows came from there, else None: lp_leads.json is the one file whose rows reach
    the board and so the one this script stamps stay flags into. The raw feed is never written."""
    lp_leads = lp_leads or os.path.join(HERE, LP_LEADS)
    lp_feed = lp_feed or os.path.join(HERE, LP_FEED)
    src, rows, writable = '', None, False
    if os.path.exists(lp_leads):
        try:
            rows = json.load(open(lp_leads, encoding='utf-8'))
            src, writable = os.path.basename(lp_leads), True
        except Exception as e:
            print(f'sale_history: {os.path.basename(lp_leads)} unreadable ({str(e)[:80]}) - falling back to the raw feed')
            rows = None
    if not isinstance(rows, list):
        rows, writable = None, False
        if os.path.exists(lp_feed):
            try:
                feed = json.load(open(lp_feed, encoding='utf-8'))
            except Exception:
                feed = []
            rows, seen = [], set()
            for lp in feed if isinstance(feed, list) else []:
                # lp_leads.build's dedupe, verbatim: no owner -> not a lead; key = case or owner+date
                case = str(lp.get('case') or '').strip()
                owner = str(lp.get('owner') or '').strip()
                if not owner:
                    continue
                key = case or (owner + str(lp.get('date', '')))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(lp)
            src = os.path.basename(lp_feed)
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if str(r.get('county') or 'MIAMI-DADE').upper() != 'MIAMI-DADE':
            continue
        case = md_civil_case(r.get('case'))
        if case:
            out.append((case, r))
    return out, (rows if writable else None), src


def _apply_cached(kind, r, ent):
    """Stamp a cache entry onto a row. Never CLEARS a flag: only a live read may (see below)."""
    if kind == 'lp':
        # LP rows are board-slim rows. Only the §362 stay fields are stamped, under the names the board
        # already gates on (tracker_template reads r.saleBkAct / saleBkD / saleLift). The staller
        # counts (saleSurv / saleBK) are deliberately NOT stamped: they feed ranking, and moving the
        # Fresh-filings lane's order is a different change from making its stay status known.
        if ent.get('a'):
            r['saleBkAct'] = True; r['saleBkD'] = ent.get('bd', '')
        if ent.get('sl'):
            r['saleLift'] = ent['sl']
        return
    r['sale_survived'] = ent['s']; r['sale_scheduled'] = ent.get('n', 0)
    if ent.get('w'): r['sale_who'] = ent['w']
    if ent.get('b'): r['sale_bk'] = ent['b']
    if ent.get('a'): r['sale_bk_active'] = True; r['sale_bk_date'] = ent.get('bd', '')
    if ent.get('sl'): r['sale_stay_lifted'] = ent['sl']


def _apply_live(kind, r, surv, sched, who, bk, bkact, bkd, lifted):
    """Stamp a successful live read onto a row. A live read that says no stay is active CLEARS one."""
    if kind == 'lp':
        if bkact:
            r['saleBkAct'] = True; r['saleBkD'] = bkd
            r.pop('saleLift', None)
        else:
            r.pop('saleBkAct', None); r.pop('saleBkD', None)
        if lifted: r['saleLift'] = lifted
        return
    r['sale_survived'] = surv; r['sale_scheduled'] = sched
    if who: r['sale_who'] = who
    if bk: r['sale_bk'] = bk
    if bkact:
        r['sale_bk_active'] = True; r['sale_bk_date'] = bkd
        r.pop('sale_stay_lifted', None)      # gates read a lift date as "contact is legal"
    else:
        # A LIVE READ saying no stay is active overrides a flag already on the row. Since
        # 2026-09-24 foreclosure_leads.main() writes the cached stays into leads_final.json
        # before this step runs, so a stay the docket now shows lifted would otherwise stay
        # on the row (and gate the lead) until the next scrape. Only a successful fetch
        # clears; a failed one (dks None) and a cache hit never do.
        r.pop('sale_bk_active', None); r.pop('sale_bk_date', None)
    if lifted: r['sale_stay_lifted'] = lifted


def _row_prev_stay(kind, r):
    """(active, filing date) a row already carries, in its own field names."""
    if kind == 'lp':
        return bool(r.get('saleBkAct')), r.get('saleBkD') or ''
    return bool(r.get('sale_bk_active')), r.get('sale_bk_date') or ''


def never_read(ent):
    """No cache entry, or one from before the stay fields existed (v<4, no 'a') — the two states
    stay_gate.py refuses as stay_unverified. These are read before any re-read of a known case."""
    return not isinstance(ent, dict) or 'a' not in ent


def plan(leads, lp_rows, cache, near):
    """The fetch order: [(case, [(kind, row), ...], is_near)].

    One entry per case, so a case on both the auction list and the LP lane is fetched once and
    stamped onto both rows. Order:
      0. an auction within --near-days (the short-TTL re-read; a petition is likeliest right before
         a sale — unchanged from before)
      1. NEVER READ: auction rows first (they have a sale date), then LP rows newest filing first
      2. everything else, oldest cache read first
    Fresh entries cost no fetch, so where they sort only matters for which stale ones the --limit
    budget reaches, and those go oldest-first."""
    groups, order = {}, []
    for r in leads:
        case = (r.get('Case #') or '').strip()
        # civil MD foreclosure cases only (tax-deed & non-CA cases aren't in OCS)
        if r.get('sale_type') == 'TD' or not re.match(r'\d{4}-\d+-\w+-\d+', case):
            continue
        if case not in groups:
            groups[case] = {'rows': [], 'near': False, 'fc': False, 'filed': ''}
            order.append(case)
        g = groups[case]
        g['rows'].append(('fc', r)); g['fc'] = True
        g['near'] = g['near'] or near(r)
    for case, r in lp_rows:
        if case not in groups:
            groups[case] = {'rows': [], 'near': False, 'fc': False, 'filed': ''}
            order.append(case)
        g = groups[case]
        g['rows'].append(('lp', r))
        g['filed'] = max(g['filed'], _lp_filed_key(r))
    pos = {c: i for i, c in enumerate(order)}

    def key(c):
        g, ent = groups[c], cache.get(c)
        if g['near']:
            return (0, 0, '', pos[c])
        if never_read(ent):
            # auction rows keep their file order; LP rows go newest filing first
            return (1, 0 if g['fc'] else 1, '' if g['fc'] else _inv(g['filed']), pos[c])
        return (2, 0, '%020.3f' % float(ent.get('t', 0) or 0), pos[c])
    return [(c, groups[c]['rows'], groups[c]['near']) for c in sorted(order, key=key)]


def _inv(iso):
    """Sort key that puts a LATER 'YYYY-MM-DD' first; an unknown date sorts last."""
    if not iso:
        return '~'
    return ''.join(chr(ord('9') - int(ch) + ord('0')) if ch.isdigit() else ch for ch in iso)


def _dump(obj, path, indent=None):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=indent)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='max live fetches this run (0 = unlimited)')
    ap.add_argument('--ttl-days', type=float, default=7.0)
    ap.add_argument('--case', default='')
    ap.add_argument('--cache-only', action='store_true',
                    help='apply cached entries regardless of TTL, fetch NOTHING. For the early-publish '
                         'path: yesterday\'s stay flags beat shipping the board with the compliance '
                         'layer stripped (the 2026-07-21 hole: [1b/5] published 67 stay-active leads '
                         'with live outreach buttons).')
    ap.add_argument('--near-days', type=float, default=14.0,
                    help='a lead whose auction is this many days away or fewer is re-read on the short TTL')
    ap.add_argument('--near-ttl-hours', type=float, default=20.0,
                    help='TTL for near-sale leads: a bankruptcy filed days before the sale is the most '
                         'common one, and a 7-day-old read cannot see it')
    ap.add_argument('--refresh-bk', action='store_true',
                    help='force-refetch every BK-relevant entry (active stay or any BK count) ignoring TTL')
    ap.add_argument('--no-lp', action='store_true',
                    help='auction list only, the pre-2026-09-26 scope: skip the Miami-Dade lis pendens lane')
    a = ap.parse_args(argv)

    path = os.path.join(HERE, 'leads_final.json')
    leads = []
    if os.path.exists(path):
        leads = json.load(open(path, encoding='utf-8'))
    else:
        print('leads_final.json missing - reading the lis pendens lane only')
    lp_rows, lp_all, lp_src = ([], None, '') if a.no_lp else load_lp_rows()
    lp_writable = lp_all is not None
    if not leads and not lp_rows:
        print('sale_history: no leads_final.json and no Miami-Dade lis pendens rows - nothing to read'); return
    cache = _load_cache()
    now = time.time()
    ttl = a.ttl_days * 86400
    near_ttl = a.near_ttl_hours * 3600

    def _days_to_sale(r):
        try:
            t = datetime.strptime((r.get('AuctionDate') or '').strip()[:10], '%m/%d/%Y').timestamp()
        except ValueError:
            return None
        return (t - now) / 86400.0

    def _near(r):
        d = _days_to_sale(r)
        return d is not None and -1 <= d <= a.near_days
    session = requests.Session()
    session.headers.update({'User-Agent': UA, 'Referer': CLERK + '/ocs/'})

    budget = a.limit if a.limit > 0 else 10 ** 9
    changed = fetched = 0
    # Rows served from a FRESH cache entry. Tracked separately from `changed` because they are the
    # normal steady state: once the cache is warm, a run legitimately fetches nothing. They still
    # have to be written to disk — see the write guard at the bottom.
    applied = 0
    lp_dirty = False
    nlp_cases = len({c for c, _ in lp_rows})
    lp_new = lp_read = 0
    capped = 0
    if lp_rows:
        print(f'sale_history: {len(lp_rows)} Miami-Dade lis pendens row(s) ({nlp_cases} case(s)) from {lp_src}, '
              f'{sum(1 for c in {c for c, _ in lp_rows} if never_read(cache.get(c)))} never read')
    # NEAR SALES FIRST, ON A SHORT TTL (sweep of all Miami leads, 2026-09-24): three 09-28 sales had
    # a suggestion of bankruptcy filed 09-22 to 09-24, and a 7-day TTL let a read from the week before
    # stand until the sale. A petition is most likely in the days before an auction, so those leads
    # are re-read every run and fetched before the --limit budget runs out on distant ones. Then
    # every case never read at all (plan()), then the rest oldest-read first.
    for case, rows, is_near in plan(leads, lp_rows, cache, _near):
        if a.case and case != a.case and md_civil_case(a.case) != case:
            continue
        ent = cache.get(case)
        _fresh = ent and ent.get('v') == CACHE_VER and (now - ent.get('t', 0)) < (near_ttl if is_near else ttl)
        _bkforce = a.refresh_bk and ent and (ent.get('a') or ent.get('b'))
        # cache-only: TRUST any structurally-compatible entry (v4+ carries the BK fields) — the
        # point is a fetch-free apply so the early-publish never ships without the compliance layer.
        if a.cache_only:
            if ent and ent.get('v', 0) >= 4:
                for kind, r in rows:
                    _apply_cached(kind, r, ent)
                    changed += 1
                    lp_dirty = lp_dirty or kind == 'lp'
            continue
        if _fresh and not a.case and not _bkforce:
            for kind, r in rows:
                _apply_cached(kind, r, ent)
                applied += 1
                lp_dirty = lp_dirty or kind == 'lp'
            continue
        if budget <= 0:
            # `continue`, not `break`: a fresh entry later in the order still has to be applied.
            capped += 1
            continue
        dks = _fetch(session, case)
        fetched += 1; budget -= 1
        if any(k == 'lp' for k, _ in rows):
            lp_read += 1
            lp_new += int(never_read(ent))
        if dks is not None:
            surv, sched, done, who = _count(dks)
            bk = _bk_count(dks)
            bkact, bkd, lifted = _bk_stay(dks)
            # A STAY WE ALREADY HOLD ENDS ONLY ON THE SAME EVIDENCE THAT OPENED IT. The prior stay is
            # the cached one or, when the cache entry is gone, the flag on the row itself. It is cleared
            # only when this read shows a bankruptcy filing on or after the prior filing date AND a
            # closing line of its own for every case from that filing on (_bk_cases). An empty or short answer, a
            # closing line with no filing line, an older stay's closure, or a prior date we cannot
            # read all keep the stay: a wrongly kept stay costs a call, a wrongly cleared one is a
            # §362 contact.
            _prev = ent if isinstance(ent, dict) else {}
            _rowprev = [_row_prev_stay(k, r) for k, r in rows]
            _row_act = any(act for act, _ in _rowprev)
            _row_bd = next((d for act, d in _rowprev if act and d), '')
            if not bkact and (_prev.get('a') or _row_act):
                _pbd = _prev.get('bd') or _row_bd or ''
                _piso = _pbd if re.match(r'\d{4}-\d{2}-\d{2}$', _pbd) else _iso_date(_pbd)
                # _bk_stay answers for the NEWEST case only; a newer case closing says nothing
                # about ours. The stay ends only when ours and every case filed after it each show
                # their own closing line (_bk_cases), and the lift date is the last of those.
                _end = _stay_end(_bk_cases(dks), _piso) if _piso else ''
                if _piso and bkd and bkd >= _piso and lifted and _end:
                    lifted = max(_end, lifted)
                else:
                    # still active: drop any lift date from an OLDER closed stay in this read, since
                    # the board's gates read a lift date as "contact is legal again"
                    bkact, bkd, lifted = True, _pbd or bkd, ''
            # a standalone bankruptcy filing IS the owner's move — attribute when cancels didn't
            if bk and not who:
                who = 'owner'
            for kind, r in rows:
                _apply_live(kind, r, surv, sched, who, bk, bkact, bkd, lifted)
                lp_dirty = lp_dirty or kind == 'lp'
            # CACHE FORMAT UNCHANGED: stay_gate.py (#72), outreach_email, healthcheck and
            # foreclosure_leads.restore_stays_from_cache all read these keys. An LP case lands under
            # its clean Miami-Dade number exactly like an auction case.
            cache[case] = {'s': surv, 'n': sched, 'd': done, 'w': who, 'b': bk,
                           'a': bkact, 'bd': bkd, 'sl': lifted, 't': now, 'v': CACHE_VER}
            changed += 1
            if a.case:
                print(f'{case}: survived {surv} sale(s), {sched} scheduled, {done} completed'
                      + (f', mostly {who}-moved' if who else '') + (f', {bk} distinct bankruptcies' if bk else '')
                      + (f', STAY ACTIVE since {bkd}' if bkact else '')
                      + (f', stay LIFTED {lifted}' if lifted else ''))
        time.sleep(0.25)
        if fetched % 25 == 0:
            json.dump(cache, open(CACHE, 'w', encoding='utf-8'))
            if leads:
                json.dump(leads, open(path, 'w', encoding='utf-8'))
            if lp_dirty and lp_writable:
                _dump(lp_all, os.path.join(HERE, LP_LEADS), indent=1)
            print(f'  ... {fetched} fetched, {changed} updated')

    json.dump(cache, open(CACHE, 'w', encoding='utf-8'))
    # WRITE WHENEVER ANY VALUE WAS APPLIED, not only when something was fetched live. Guarding this
    # on `changed` alone silently discarded the whole in-memory merge on any day the cache was fully
    # warm (2026-08-09: 0 fetched -> file never written -> sale_survived and sale_bk_active vanished
    # from every row, sale-history coverage 100% -> 0%, and 93 active §362 stays stopped reaching the
    # board). The staller count below reads the in-memory list, so the old log line reported healthy
    # numbers for data that was never persisted — the failure was invisible in the log.
    if leads and (changed or applied):
        json.dump(leads, open(path, 'w', encoding='utf-8'))
    # Same rule for the LP lane, and the same file shape lp_leads.py writes (indent=1). Written only
    # when a row actually took a value: an untouched file keeps its mtime.
    if lp_dirty and lp_writable:
        _dump(lp_all, os.path.join(HERE, LP_LEADS), indent=1)
    stallers = sum(1 for r in leads if (r.get('sale_survived') or 0) >= 2)
    print(f'sale_history: {changed} updated, {applied} applied from cache, {fetched} fetched live. '
          f'{stallers} serial stallers (survived >=2 sales) flagged.')
    if lp_rows:
        _unread = sum(1 for c in {c for c, _ in lp_rows} if never_read(cache.get(c)))
        _act = sum(1 for c in {c for c, _ in lp_rows} if isinstance(cache.get(c), dict) and cache[c].get('a') and not cache[c].get('sl'))
        print(f'  lis pendens: {lp_read} case(s) read live ({lp_new} for the first time); '
              f'{nlp_cases - _unread} of {nlp_cases} Miami-Dade LP case(s) now carry a stay read, '
              f'{_unread} still unread, {_act} with an ACTIVE stay'
              + ('' if lp_writable else f' (from {lp_src}: read-only, no board rows stamped)'))
    if capped:
        print(f'  --limit reached: {capped} stale or unread case(s) left for the next run')


if __name__ == '__main__':
    main()
