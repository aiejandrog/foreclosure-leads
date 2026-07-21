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

Run:  python sale_history.py [--limit N] [--ttl-days 7] [--case CASE]
"""
import argparse
import json
import os
import re
import time

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
_BANKR = re.compile(r'bankrupt', re.I)
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

def _bk_stay(dks):
    """(active, latest_filing_iso, lifted_iso). A bankruptcy filing line (or a sale cancelled PER
    the stay) opens; a dismissal / discharge / stay-relief line closes. Active = the newest opening
    has no closing on/after it. Closing lines are checked FIRST ('Notice of Filing: ...ORDER OF
    DISMISSAL' contains 'filing' but closes).
    lifted_iso = the court date the LAST stay closed (when none is active) — the door signal: the
    owner's shield just dropped, the sale is about to be reset, and contact is legal again. The
    freshest-dismissed leads are the most rescuable calls on the board."""
    opens, closes = [], []
    for e in dks or []:
        t = (e.get('docketDescrition') or e.get('docketDescription') or '')
        tx = t + ' ' + (e.get('comments') or '')
        iso = _iso_date(e.get('eventDate'))
        if not iso:
            continue
        if _BKCLOSE.search(tx):
            closes.append(iso)
        elif _BKFILE.search(t) or _BANKR.search(tx):
            opens.append(iso)                          # 'CANCELLED PER BANKRUPTCY' = the stay acting
    if not opens:
        return False, '', ''
    latest = max(opens)
    active = not closes or max(closes) < latest
    lifted = '' if active else max(c for c in closes if c >= latest)
    return active, latest, lifted


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='max live fetches this run (0 = unlimited)')
    ap.add_argument('--ttl-days', type=float, default=7.0)
    ap.add_argument('--case', default='')
    ap.add_argument('--cache-only', action='store_true',
                    help='apply cached entries regardless of TTL, fetch NOTHING. For the early-publish '
                         'path: yesterday\'s stay flags beat shipping the board with the compliance '
                         'layer stripped (the 2026-07-21 hole: [1b/5] published 67 stay-active leads '
                         'with live outreach buttons).')
    ap.add_argument('--refresh-bk', action='store_true',
                    help='force-refetch every BK-relevant entry (active stay or any BK count) ignoring TTL')
    a = ap.parse_args()

    path = os.path.join(HERE, 'leads_final.json')
    if not os.path.exists(path):
        print('leads_final.json missing'); return
    leads = json.load(open(path, encoding='utf-8'))
    cache = _load_cache()
    now = time.time()
    ttl = a.ttl_days * 86400
    session = requests.Session()
    session.headers.update({'User-Agent': UA, 'Referer': CLERK + '/ocs/'})

    budget = a.limit if a.limit > 0 else 10 ** 9
    changed = fetched = 0
    for r in leads:
        case = (r.get('Case #') or '').strip()
        if a.case and case != a.case:
            continue
        # civil MD foreclosure cases only (tax-deed & non-CA cases aren't in OCS)
        if r.get('sale_type') == 'TD' or not re.match(r'\d{4}-\d+-\w+-\d+', case):
            continue
        ent = cache.get(case)
        _fresh = ent and ent.get('v') == CACHE_VER and (now - ent.get('t', 0)) < ttl
        _bkforce = a.refresh_bk and ent and (ent.get('a') or ent.get('b'))
        # cache-only: TRUST any structurally-compatible entry (v4+ carries the BK fields) — the
        # point is a fetch-free apply so the early-publish never ships without the compliance layer.
        if a.cache_only:
            if ent and ent.get('v', 0) >= 4:
                r['sale_survived'] = ent['s']; r['sale_scheduled'] = ent.get('n', 0)
                if ent.get('w'): r['sale_who'] = ent['w']
                if ent.get('b'): r['sale_bk'] = ent['b']
                if ent.get('a'): r['sale_bk_active'] = True; r['sale_bk_date'] = ent.get('bd', '')
                if ent.get('sl'): r['sale_stay_lifted'] = ent['sl']
                changed += 1
            continue
        if _fresh and not a.case and not _bkforce:
            r['sale_survived'] = ent['s']; r['sale_scheduled'] = ent.get('n', 0)
            if ent.get('w'): r['sale_who'] = ent['w']
            if ent.get('b'): r['sale_bk'] = ent['b']
            if ent.get('a'): r['sale_bk_active'] = True; r['sale_bk_date'] = ent.get('bd', '')
            if ent.get('sl'): r['sale_stay_lifted'] = ent['sl']
            continue
        if budget <= 0:
            break
        dks = _fetch(session, case)
        fetched += 1; budget -= 1
        if dks is not None:
            surv, sched, done, who = _count(dks)
            bk = _bk_count(dks)
            bkact, bkd, lifted = _bk_stay(dks)
            # a standalone bankruptcy filing IS the owner's move — attribute when cancels didn't
            if bk and not who:
                who = 'owner'
            r['sale_survived'] = surv; r['sale_scheduled'] = sched
            if who: r['sale_who'] = who
            if bk: r['sale_bk'] = bk
            if bkact: r['sale_bk_active'] = True; r['sale_bk_date'] = bkd
            if lifted: r['sale_stay_lifted'] = lifted
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
            json.dump(leads, open(path, 'w', encoding='utf-8'))
            print(f'  ... {fetched} fetched, {changed} updated')

    json.dump(cache, open(CACHE, 'w', encoding='utf-8'))
    if changed:
        json.dump(leads, open(path, 'w', encoding='utf-8'))
    stallers = sum(1 for r in leads if (r.get('sale_survived') or 0) >= 2)
    print(f'sale_history: {changed} updated, {fetched} fetched live. '
          f'{stallers} serial stallers (survived >=2 sales) flagged.')


if __name__ == '__main__':
    main()
