#!/usr/bin/env python3
"""balloon_refi.py — the SECOND deal engine (Layer 8): hard-money balloons about to mature.

Jose's play: investors who took a hard-money / bridge loan (10-14%, 1-2 year BALLOON) are sitting
ducks 60-90 days before the balloon comes due — the son refis them into a 6.75-7.75% DSCR/term loan
(min 4 points, 1 to our side), and each investor has 5-10 properties + refers friends. Recurring
revenue, separate from foreclosures.

How this finds them: Broward's Official Records (AcclaimWeb) name-search is FREE and no-captcha, and
you can search by the LENDER's name + doc type MORTGAGE + a recent date range. So for each known
hard-money lender, we pull every mortgage they recorded ~9-27 months ago (the window where a 12- or
24-month balloon is maturing now-ish), keep the investor-LLC borrowers, and rank by how close the
balloon is to due. Output = a maturity hit-list the son works down.

STARTER lender list = well-known national hard-money / DSCR / fix-&-flip lenders active in FL. Add
Jose's / the son's local private lenders to hm_lenders.txt (one name per line) — that's where the
richest, least-competed leads are. Broward first (the free feed); Miami-Dade OR is reCAPTCHA-gated
(reuse gen_records_qs machinery later); Palm Beach clerk is captcha-walled.

Run:  python balloon_refi.py [--months-min 9] [--months-max 27] [--min-amt 60000] [--limit-lenders N]
Writes balloon_refi.json + a readable report to the Desktop DEALFLOW folder.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import broward_liens as B   # reuse the AcclaimWeb session + curl + date parsing

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'balloon_refi.json')
LENDER_FILE = os.path.join(HERE, 'hm_lenders.txt')

# Known national hard-money / DSCR / bridge / fix-&-flip lenders that actively record in South FL.
# The son's LOCAL private lenders (added to hm_lenders.txt) are the real edge — these seed it.
HM_LENDERS = [
    'LIMA ONE CAPITAL', 'KIAVI', 'LENDINGHOME', 'RCN CAPITAL', 'ROC CAPITAL', 'ANCHOR LOANS',
    'COREVEST', 'TEMPLE VIEW CAPITAL', 'CONSTRUCTIVE LOANS', 'TOORAK CAPITAL', 'GENESIS CAPITAL',
    'CIVIC FINANCIAL', 'VISIO', 'RENOVO FINANCIAL', 'LENDINGONE', 'FINANCE OF AMERICA COMMERCIAL',
    'SHARESTATES', 'LONGHORN INVESTMENTS', 'BOOMERANG CAPITAL', 'CENTER STREET LENDING',
    'EXPRESS CAPITAL', 'ZEUS', 'BRIDGE LOAN', 'PRIVATE MONEY', 'HARD MONEY', 'CAPITAL FUND',
]

# balloon terms we assume when the doc doesn't state one (most bridge loans): 12 and 24 months.
ASSUMED_TERMS_MO = (12, 24)


def _load_lenders():
    names = list(HM_LENDERS)
    if os.path.exists(LENDER_FILE):
        for line in open(LENDER_FILE, encoding='utf-8'):
            n = line.strip().upper()
            if n and not n.startswith('#') and n not in names:
                names.append(n)
    return names


def _search_lender(sess, name, date_from):
    """All mortgage docs where `name` is a party, recorded on/after date_from. Reuses B._curl."""
    resp = B._curl(B.BASE + '/Search/SearchTypeName?Length=6', post=[
        ('PartyType', 'Both'), ('SearchOnName', name), ('IsParsedName', 'false'),
        ('AllowAutoCompleteCB', 'false'), ('DateRangeList', ' '),
        ('DocTypes', sess['doctypes']), ('DocTypesDisplay-input', 'All'), ('DocTypesDisplay', 'All'),
        ('BookTypes', sess['booktypes']), ('BookTypesDisplay', 'All'),
        ('RecordDateFrom', date_from), ('RecordDateTo', time.strftime('%m/%d/%Y')),
    ])
    if 'ShowError' in (resp or ''):
        return None
    grid = B._curl(B.BASE + '/Search/GridResults', post=[('page', '1'), ('size', '400'), ('sort', ''), ('group', ''), ('filter', '')])
    try:
        return json.loads(grid).get('data', [])
    except Exception:
        return None


def _is_investor(name):
    return bool(re.search(r'\bLLC\b|\bL\.?P\.?\b|\bINC\b|\bCORP\b|\bHOLDINGS?\b|\bPROPERT|\bINVEST|\bGROUP\b|\bHOMES?\b|\bREALTY\b|\bTRUST\b|\bVENTURES?\b|\bEQUITY\b', (name or '').upper()))


def _borrower(d, lender):
    """The BORROWER = the party on the mortgage that is NOT the lender we searched. AcclaimWeb indexes
    the matched party in `Name` and the other in `CrossPartyName`, but which is which varies, so pick
    whichever doesn't contain the lender's key tokens (the investor we actually want to reach)."""
    lk = re.sub(r'[^A-Z]', '', (lender or '').upper())[:8]
    n = (d.get('Name') or '').strip()
    c = (d.get('CrossPartyName') or '').strip()
    def is_lender(x): return bool(lk) and lk in re.sub(r'[^A-Z]', '', (x or '').upper())
    if is_lender(n) and not is_lender(c):
        return c
    if is_lender(c) and not is_lender(n):
        return n
    # neither/both look like the lender — prefer whichever reads like an investor entity
    return c if (_is_investor(c) and not _is_investor(n)) else n or c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months-min', type=int, default=9, help='youngest loan age to consider (mo)')
    ap.add_argument('--months-max', type=int, default=27, help='oldest loan age to consider (mo)')
    ap.add_argument('--min-amt', type=int, default=60000)
    ap.add_argument('--limit-lenders', type=int, default=0)
    a = ap.parse_args()

    lenders = _load_lenders()
    if a.limit_lenders:
        lenders = lenders[:a.limit_lenders]
    date_from = (datetime.now() - timedelta(days=int(a.months_max * 30.5))).strftime('%m/%d/%Y')
    print(f'Hunting {len(lenders)} hard-money lenders in Broward, mortgages since {date_from}...')

    sess = B.start_session()
    if not sess:
        print('ABORT: no AcclaimWeb session (Cloudflare block / site down). Try again later.')
        return

    today = datetime.now()
    hits = {}   # instrument -> record (dedupe across lenders)
    for i, lender in enumerate(lenders):
        docs = _search_lender(sess, lender, date_from)
        if docs is None:
            print(f'  {lender:34} (blocked)')
            continue
        found = 0
        for d in docs:
            if 'MORTGAGE' not in (d.get('DocTypeDescription') or '').upper():
                continue
            amt = B._num(d.get('Consideration'))
            if amt < a.min_amt:
                continue
            rec = B._jsdate(d.get('RecordDate'))       # YYYY-MM-DD
            try:
                recdt = datetime.strptime(rec, '%Y-%m-%d')
            except Exception:
                continue
            age_days = (today - recdt).days
            if not (a.months_min * 30.4 <= age_days <= a.months_max * 30.5):
                continue
            borrower = _borrower(d, lender)
            # the borrower must be a real, distinct investor entity — not the lender, not a person's
            # blank, not a govt/bank. This is who the son calls to refi.
            lk = re.sub(r'[^A-Z]', '', lender.upper())[:8]
            if not borrower or (lk and lk in re.sub(r'[^A-Z]', '', borrower.upper())):
                continue
            if not _is_investor(borrower):
                continue
            # nearest balloon maturity: for each assumed term, days until the balloon comes due
            mats = [(recdt + timedelta(days=int(t * 30.44)), t) for t in ASSUMED_TERMS_MO]
            # keep the maturity date closest to today (within a sane window: 120 days past .. 150 future)
            cand = [(m, t) for m, t in mats if -120 <= (m - today).days <= 150]
            if not cand:
                continue
            matdt, term = min(cand, key=lambda mt: abs((mt[0] - today).days))
            dtm = (matdt - today).days
            key = d.get('InstrumentNumber') or d.get('BookPage') or (borrower + rec)
            hits[key] = {
                'borrower': borrower, 'lender': lender, 'amount': round(amt),
                'recorded': rec, 'est_term_mo': term, 'est_maturity': matdt.strftime('%Y-%m-%d'),
                'days_to_maturity': dtm, 'parcel': (d.get('ParcelNumber') or '').strip(),
                'legal': (d.get('DocLegalDescription') or '')[:80], 'bookpage': d.get('BookPage', ''),
                'instrument': d.get('InstrumentNumber', ''),
            }
            found += 1
        print(f'  {lender:34} {found} maturing balloon(s)')
        time.sleep(0.5)

    recs = sorted(hits.values(), key=lambda r: abs(r['days_to_maturity']))
    json.dump(recs, open(OUT, 'w', encoding='utf-8'), indent=1)
    hot = [r for r in recs if -30 <= r['days_to_maturity'] <= 90]
    print(f'\nDONE: {len(recs)} maturing hard-money balloons found, {len(hot)} in the 90-day refi window.')
    for r in hot[:12]:
        when = f"{r['days_to_maturity']}d" if r['days_to_maturity'] >= 0 else f"{-r['days_to_maturity']}d PAST"
        print(f"  {r['est_maturity']} ({when:>9}) ${r['amount']:>9,}  {r['borrower'][:30]:30} <- {r['lender'][:20]}")

    # readable report to Desktop
    desk = os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop', 'DEALFLOW')
    if os.path.isdir(desk):
        try:
            with open(os.path.join(desk, 'Balloon-Refi Hit List.txt'), 'w', encoding='utf-8') as f:
                f.write(f'BALLOON-REFI HIT LIST — {today:%Y-%m-%d}  ({len(hot)} in the 90-day window)\n')
                f.write('The son refis these before the balloon comes due. Confirm the actual term + payoff.\n\n')
                for r in hot:
                    f.write(f"{r['est_maturity']}  {r['days_to_maturity']:>4}d  ${r['amount']:>9,}  "
                            f"{r['borrower'][:34]:34}  <- {r['lender']}  (parcel {r['parcel']}, {r['bookpage']})\n")
            print('  -> Desktop\\DEALFLOW\\Balloon-Refi Hit List.txt')
        except Exception:
            pass


if __name__ == '__main__':
    main()
