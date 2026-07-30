#!/usr/bin/env python3
"""lp_status.py — is this "fresh filing" still a live case?

THE PROBLEM
A lis pendens is recorded the day a foreclosure is FILED. We treat those as the front of the
funnel and nurture them for months. But a case can END long before any auction — the owner
reinstates, refinances, sells, or the bank withdraws — and the lis pendens stays recorded in the
Official Records forever. Nothing in the LP feed tells us the case died. So a dismissed case keeps
sitting in the EARLY lane looking like a fresh opportunity, and somebody eventually calls a
homeowner who already fixed their problem to ask about a foreclosure that no longer exists.

Found live: 2026-012840-CA-01 (RILES ADRIENNE M / JPMorgan Chase) — caseStatus CLOSED, dockets
VOLD + VOLDCV "Voluntary Dismissal ... WITHOUT PREJUDICE". Still on the board as a workable lead.

WHY caseStatus AND NOT DOCKET TEXT — this is the whole design decision
The obvious implementation is to grep the docket for "Voluntary Dismissal". That is WRONG and I
verified it against live data before writing this: case 2026-012644-CA-01 carries docket code NVDU,
"Notice Of Voluntary Dismissal Of Unknown Party" — dropping the unnamed defendants, a routine step
in a case that is very much alive. Its caseStatus is OPEN. A text match on "voluntary dismissal"
would have killed a live lead. So the STATUS field decides; docket codes only corroborate.

WHAT "CLOSED" MEANS, AND WHERE IT DOES NOT
Measured on this board: all 220 auction leads carrying a status have a FUTURE sale date, and 180
of them read CLOSED or RECLOSED. In Florida the clerk closes a foreclosure when FINAL JUDGMENT is
entered; the sale is post-judgment activity. So CLOSED on a lead WITH a scheduled sale is normal
and means nothing is wrong. It is only meaningful on a lead with NO sale scheduled — a lis pendens
— where it says the case reached a terminal state without ever getting to auction.

That is why this script only runs against LP leads, and why the flag it sets is scoped to them.

Run:  python lp_status.py                # check every LP case, cached
      python lp_status.py --refresh      # ignore the cache
      python lp_status.py --case <case>  # one case, verbose
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sibling_cases as S    # reuse the working, un-captcha'd OCS request chain

HERE = os.path.dirname(os.path.abspath(__file__))
IN = os.path.join(HERE, 'lis_pendens.json')
CACHE = os.path.join(HERE, '_lp_status_cache.json')

# Terminal clerk statuses. On a lead with no sale scheduled these mean the case ended.
TERMINAL = {'CLOSED', 'RECLOSED', 'PJREPINACT', 'DISPOSED'}
# Docket codes that corroborate a true dismissal of the CASE. NVDU is deliberately excluded — it
# dismisses UNKNOWN PARTIES, not the action, and appears in healthy cases (see the docstring).
DISMISS_CODES = {'VOLD', 'VOLDCV', 'DISM', 'DISMCV', 'ORDISM'}


def _load(p, d):
    if not os.path.exists(p):
        return d
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


def check(case):
    """-> {status, dismissed, codes, checked} or {} when the clerk will not answer."""
    try:
        d = S._single(case)
    except Exception as e:
        print('  %s: lookup failed (%s)' % (case, str(e)[:70]))
        return {}
    status = str(d.get('caseStatus') or '').strip().upper()
    dk = d.get('dockets') or d.get('docketList') or []
    codes = sorted({str(x.get('docketCode') or x.get('code') or '').strip().upper()
                    for x in dk if (x.get('docketCode') or x.get('code'))})
    return {
        'status': status,
        # Terminal status is necessary. A dismissal code makes it definite; without one the case
        # may have closed by going to judgment, which is a different (still-dead-as-a-fresh-filing)
        # story — the board says which, rather than guessing.
        'dismissed': bool(status in TERMINAL and (DISMISS_CODES & set(codes))),
        'terminal': status in TERMINAL,
        'codes': [c for c in codes if c in DISMISS_CODES],
        'checked': time.strftime('%Y-%m-%d'),
    }


def main():
    args = sys.argv[1:]
    one = args[args.index('--case') + 1] if '--case' in args else None
    refresh = '--refresh' in args

    if one:
        print(json.dumps(check(one), indent=1))
        return

    feed = _load(IN, [])
    if not feed:
        print('no lis_pendens.json — nothing to check'); return
    cache = {} if refresh else _load(CACHE, {})

    cases = [str(r.get('case') or '').strip() for r in feed]
    cases = [c for c in cases if c and '-' in c]          # skip synthesized LP-XXXX keys
    todo = [c for c in cases if c not in cache]
    print('%d LP case(s) · %d to check (%d cached)' % (len(cases), len(todo), len(cases) - len(todo)))

    for i, c in enumerate(todo, 1):
        rec = check(c)
        if rec:
            cache[c] = rec
        if i % 10 == 0 or i == len(todo):
            print('  %d/%d' % (i, len(todo)))
            json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=0)   # checkpoint
        time.sleep(0.6)

    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=0)

    dismissed = [c for c, v in cache.items() if v.get('dismissed')]
    terminal = [c for c, v in cache.items() if v.get('terminal') and not v.get('dismissed')]
    live = [c for c, v in cache.items() if not v.get('terminal')]
    print('\nlive %d · closed-without-a-dismissal-docket %d · DISMISSED %d'
          % (len(live), len(terminal), len(dismissed)))
    for c in dismissed:
        print('  DISMISSED  %s  %s  %s' % (c, cache[c].get('status'), ','.join(cache[c].get('codes') or [])))
    for c in terminal:
        print('  closed     %s  %s  (no dismissal docket — may have gone to judgment)' % (c, cache[c].get('status')))
    if dismissed or terminal:
        print('\nRe-run lp_leads.py then rebuild to drop these out of the EARLY lane.')


if __name__ == '__main__':
    main()
