"""judgment_interest.py — make the equity number tell the truth about TIME.

THE BUG THIS FIXES (found live on the Nistico file, 2026-08-03)
The board showed "$599,980 equity" on 1212 NE 91 St: value $1,765,483 minus the judgment
$1,165,503. But that judgment was ENTERED 12/04/2024 and carries 9.5% post-judgment interest.
By the 09/02/2026 sale the real payoff is ~$1.36M and the true equity is ~$400k. We were
overstating by $200k — the kind of number you say out loud to a homeowner and then have to
walk back. A judgment is a FROZEN snapshot; a payoff is a moving target.

HOW FLORIDA ACTUALLY ACCRUES IT (FS 55.03)
  - The rate is fixed at the moment judgment is entered (the FJ usually recites it).
  - It then RE-ADJUSTS every January 1 to the CFO's then-current rate, until paid (55.03(3)).
    So a 2018 judgment has walked through eight different rates by now — a single flat rate
    over the whole span is wrong, sometimes badly.
  - It is SIMPLE interest on the judgment amount, not compounded.
Rates below are the CFO's published January-1 rates (myfloridacfo.com, verified 2026-08-03).

WHERE THE JUDGMENT DATE COMES FROM
Nowhere in the lead data — the counties publish the AMOUNT, never the DATE. So we pull it from
the Miami-Dade clerk's docket (the same OCS API sibling_cases.py uses) and cache it in
judgment_dates.json. Broward and Palm Beach have no equivalent open API here, so those rows
stay UNACCRUED and are labelled as such. That asymmetry is deliberate:

  ** NEVER GUESS A JUDGMENT DATE. **
An invented date produces an invented payoff, which is the exact failure this module exists to
kill. No date -> no accrual -> the board says "as entered" instead of pretending.

Usage:
  python judgment_interest.py --case 2018-011148-CA-01   # one case, prove it
  python judgment_interest.py --all                       # every MD lead with a judgment
  python judgment_interest.py --all --refresh             # re-pull even if cached
  python judgment_interest.py --selftest                  # math checks, no network
"""
import argparse, datetime, json, os, re, sys, time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
CACHE = os.path.join(HERE, 'judgment_dates.json')     # case -> {d, rate, src, label, checked}
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
OCS = 'https://www2.miamidadeclerk.gov/ocs/'

# ---- FS 55.03 January-1 rates, published by the FL CFO -----------------------------------------
# Verified against myfloridacfo.com 2026-08-03. Add each new year every January.
JAN1_RATES = {
    2010: 6.00, 2011: 6.00, 2012: 4.75, 2013: 4.75, 2014: 4.75, 2015: 4.75, 2016: 4.75,
    2017: 4.97, 2018: 5.53, 2019: 6.33, 2020: 6.83, 2021: 4.81, 2022: 4.25, 2023: 5.52,
    2024: 9.09, 2025: 9.38, 2026: 8.44,
}
LATEST_RATE_YEAR = max(JAN1_RATES)

# Docket labels that mean "final judgment entered". Ordered: we take the EARLIEST match, because
# accrual runs from the original entry. An amended/reset judgment does not restart the clock.
FJ_PATTERNS = [
    r'summary final judgment',
    r'default final judgment',
    r'final judgment of foreclosure',
    r'^final judgment',
    r'\bfinal judgment\b',
]


def _parse_date(s):
    """Accept the shapes the clerk and our own JSON emit. None on anything unreadable."""
    s = str(s or '').strip()
    if not s:
        return None
    s = s.replace('T', ' ').split(' ')[0]
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m-%d-%Y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def accrue(principal, jdate, as_of, stated_rate=None):
    """Simple post-judgment interest across annual Jan-1 rate resets (FS 55.03(3)).

    principal    : judgment amount as entered
    jdate/as_of  : date objects (as_of is normally the auction date)
    stated_rate  : the rate the FJ itself recites (e.g. 9.50). Governs the FIRST partial year,
                   through Dec 31 of the entry year; Jan 1 resets take over after that.
    Returns {interest, payoff, days, segments:[...]} — segments make it auditable, which is the
    point: an operator can see WHY the number moved instead of trusting a black box.
    """
    principal = float(principal or 0)
    if principal <= 0 or not jdate or not as_of or as_of <= jdate:
        return {'interest': 0.0, 'payoff': round(principal, 2), 'days': 0, 'segments': []}
    segments = []
    total_days = (as_of - jdate).days
    cur = jdate
    while cur < as_of:
        year_end = datetime.date(cur.year, 12, 31)
        seg_end = min(as_of, year_end + datetime.timedelta(days=1))   # exclusive upper bound
        days = (seg_end - cur).days
        if days <= 0:
            break
        if cur.year == jdate.year and stated_rate:
            rate = float(stated_rate)          # the judgment's own recited rate
        else:
            rate = JAN1_RATES.get(cur.year, JAN1_RATES[LATEST_RATE_YEAR])
        # FL convention: simple interest, 365-day year, on the ORIGINAL judgment amount
        amt = principal * (rate / 100.0) * (days / 365.0)
        segments.append({'from': cur.isoformat(), 'to': (seg_end - datetime.timedelta(days=1)).isoformat(),
                         'days': days, 'rate': round(rate, 2), 'interest': round(amt, 2)})
        cur = seg_end
    interest = round(sum(s['interest'] for s in segments), 2)
    return {'interest': interest, 'payoff': round(principal + interest, 2),
            'days': total_days, 'segments': segments}


# ---- Miami-Dade docket pull ---------------------------------------------------------------------
def _ocs_case(session, case):
    """Full OCS record for a case, or None. Same two-step the rest of the repo uses:
    GET encrypt/{case} -> qs, then POST GetSingleCaseResult?qs=."""
    h = {'User-Agent': UA, 'Referer': OCS}
    try:
        r = session.get(f'{OCS}api/CaseInfo/encrypt/{case}', headers=h, timeout=30)
        qs = (r.json() or {}).get('qs')
        if not qs:
            return None
        time.sleep(0.35)
        r2 = session.post(f'{OCS}api/CaseInfo/GetSingleCaseResult?qs={qs}',
                          headers={**h, 'Content-Type': 'application/json'}, data='""', timeout=30)
        return r2.json()
    except Exception:
        return None


def fj_from_docket(rec):
    """(date, label) of the EARLIEST final-judgment docket entry, or (None, '')."""
    if not isinstance(rec, dict):
        return None, ''
    rows = rec.get('dockets') or []
    best, best_label = None, ''
    for pat in FJ_PATTERNS:
        for x in rows:
            desc = str(x.get('docketDescrition') or '').strip()
            if not re.search(pat, desc, re.I):
                continue
            d = _parse_date(x.get('eventDate') or x.get('oDate'))
            if d and (best is None or d < best):
                best, best_label = d, desc
        if best:
            break          # earliest match on the most specific pattern wins
    return best, best_label


def load_cache():
    if not os.path.exists(CACHE):
        return {}
    try:
        return json.load(open(CACHE, encoding='utf-8')) or {}
    except Exception:
        return {}


def save_cache(d):
    tmp = CACHE + '.tmp'
    json.dump(d, open(tmp, 'w', encoding='utf-8'), indent=1, sort_keys=True)
    os.replace(tmp, CACHE)


def _selftest():
    """Math only — no network. Anchored on the Nistico judgment, which we read from the PDF."""
    ok = True
    # Nistico: $1,165,503.22 entered 12/04/2024 @ 9.50% stated, sale 09/02/2026.
    r = accrue(1165503.22, datetime.date(2024, 12, 4), datetime.date(2026, 9, 2), stated_rate=9.50)
    # 28 days @9.50 (12/04->12/31/2024) + 365 @9.38 (2025) + 244 @8.44 (01/01->09/02/2026) = 637,
    # which is exactly (sale - entry).days — the segments must tile the span with no gap/overlap.
    exp = (1165503.22 * .095 * 28 / 365) + (1165503.22 * .0938 * 365 / 365) + (1165503.22 * .0844 * 244 / 365)
    if r['days'] != 637:
        print(f'  FAIL nistico: span {r["days"]} days, expected 637'); ok = False
    if sum(s['days'] for s in r['segments']) != r['days']:
        print('  FAIL: segments do not tile the full span'); ok = False
    if abs(r['interest'] - round(exp, 2)) > 1.0:
        print(f'  FAIL nistico: got {r["interest"]}, expected ~{exp:,.2f}'); ok = False
    else:
        print(f'  PASS nistico accrual: +${r["interest"]:,.2f} -> payoff ${r["payoff"]:,.2f} '
              f'({len(r["segments"])} rate segments)')
    # no date -> no accrual, ever
    if accrue(500000, None, datetime.date(2026, 9, 1))['interest'] != 0:
        print('  FAIL: accrued without a judgment date'); ok = False
    else:
        print('  PASS: missing date accrues nothing')
    # as_of before judgment -> no negative interest
    if accrue(500000, datetime.date(2026, 9, 1), datetime.date(2025, 1, 1))['interest'] != 0:
        print('  FAIL: negative span produced interest'); ok = False
    else:
        print('  PASS: past-dated as_of accrues nothing')
    # rate resets actually change the number
    a = accrue(1000000, datetime.date(2022, 1, 1), datetime.date(2023, 1, 1))   # 4.25%
    b = accrue(1000000, datetime.date(2024, 1, 1), datetime.date(2025, 1, 1))   # 9.09%
    if not (b['interest'] > a['interest'] * 1.9):
        print('  FAIL: annual rate reset not applied'); ok = False
    else:
        print(f'  PASS: rate resets applied (2022 ${a["interest"]:,.0f} vs 2024 ${b["interest"]:,.0f})')
    print('SELFTEST', 'OK' if ok else 'FAILED')
    return 0 if ok else 1


# ---- COUNTY DETECTION -----------------------------------------------------------------------
# This puller reaches the MIAMI-DADE clerk's OCS docket and NOTHING ELSE. That was survivable while
# --all filtered to '-(CA|CC)-', but --case never filtered: handing it a Broward or Palm Beach case
# queried OCS, got nothing back (correctly — wrong county), and then CACHED A MISS. A cached miss is
# indistinguishable from "this docket genuinely has no final judgment", and since the next run skips
# anything already cached, the false negative is PERMANENT.
#
# Measured 2026-08-13: `--case CACE-24-006635` printed "no final-judgment entry found" for a BROWARD
# case five days from its foreclosure sale. It never looked. Across the board that is ~452 of 725
# tracked cases (Broward + Palm Beach) carrying no accrued interest with no signal as to why.
# NO DATE -> NO ACCRUAL is the correct rail; silently manufacturing the date's absence is not.
MD_CASE = re.compile(r'-(CA|CC)-', re.I)                            # 2024-000848-CA-01
BROWARD_CASE = re.compile(r'^(CACE|COCE|COWE|CONO|COSO)-', re.I)    # CACE-24-006635
PB_CASE = re.compile(r'^50\d{4}(CA|CC)', re.I)                      # 502025CA007842XXXAMB


def county_of(case):
    c = str(case or '').strip().upper()
    if BROWARD_CASE.search(c):
        return 'BROWARD'
    if PB_CASE.search(c):
        return 'PALM BEACH'
    if MD_CASE.search(c):
        return 'MIAMI-DADE'
    return 'UNKNOWN'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--case', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()

    if a.selftest:
        return _selftest()

    cache = load_cache()
    leads = json.load(open(LEADS, encoding='utf-8')) if os.path.exists(LEADS) else []
    by_case = {}
    for r in leads:
        c = str(r.get('case') or r.get('Case #') or '').strip()
        if c:
            by_case[c] = r

    if a.case:
        # REFUSE rather than cache a false negative. See county_of().
        cty = county_of(a.case)
        if cty != 'MIAMI-DADE':
            print('%s is a %s case. This puller only reads the MIAMI-DADE clerk docket (OCS),'
                  % (a.case, cty))
            print('so it cannot see this case and will NOT record a miss for it.')
            print('')
            print('Get the entry date from one of these instead:')
            if cty == 'BROWARD':
                print('  * the homeowner\'s own final judgment paperwork — fastest, and it also')
                print('    carries the rate the judgment recites, which OCS never gives us')
                print('  * browardclerk.org/Web2/CaseSearchECA (reCAPTCHA-gated; broward_plaintiff.py')
                print('    already solves that flow and could be extended to read the docket)')
            elif cty == 'PALM BEACH':
                print('  * the homeowner\'s own final judgment paperwork')
                print('  * appsgp.mypalmbeachclerk.com case search')
            else:
                print('  * unrecognised case-number format — check it before assuming a county')
            return 2
        targets = [a.case]
    elif a.all:
        # Miami-Dade circuit/county cases only — the OCS API is county-specific.
        targets = [c for c, r in by_case.items()
                   if re.search(r'-(CA|CC)-', c) and float(r.get('judgment') or 0) > 0]
    else:
        print('nothing to do — pass --case X or --all (or --selftest)')
        return 0

    if not a.refresh:
        targets = [c for c in targets if c not in cache]
    if a.limit:
        targets = targets[:a.limit]

    # NAME THE BLIND SPOT OUT LOUD. --all silently drops every non-Miami-Dade case, which reads as
    # "nothing left to do" when it actually means "most of the board is unreachable by this tool".
    if a.all:
        skipped = {}
        for c, r in by_case.items():
            if float(r.get('judgment') or 0) <= 0:
                continue
            cty = county_of(c)
            if cty != 'MIAMI-DADE':
                skipped[cty] = skipped.get(cty, 0) + 1
        if skipped:
            tot = sum(skipped.values())
            print('NOT COVERED: %d case(s) with a judgment are outside Miami-Dade and this puller '
                  'cannot reach them (%s).'
                  % (tot, ', '.join('%s %d' % kv for kv in sorted(skipped.items()))))
            print('             They have no accrued-interest number. That is a data gap, not a '
                  'finding of "no judgment".')

    print(f'{len(targets)} case(s) to pull')

    s = requests.Session()
    hit = miss = 0
    for i, c in enumerate(targets, 1):
        rec = _ocs_case(s, c)
        d, label = fj_from_docket(rec)
        if d:
            cache[c] = {'d': d.isoformat(), 'rate': None, 'src': 'mdc-ocs-docket',
                        'label': label[:60], 'checked': datetime.date.today().isoformat()}
            hit += 1
            print(f'  [{i}/{len(targets)}] {c}  FJ {d.isoformat()}  ({label[:40]})')
        else:
            # cache the MISS too, so a re-run does not re-hammer the clerk for a case whose
            # docket genuinely has no FJ entry (dismissed, pre-judgment, LP-only).
            cache[c] = {'d': '', 'rate': None, 'src': 'mdc-ocs-docket', 'label': '',
                        'checked': datetime.date.today().isoformat()}
            miss += 1
            print(f'  [{i}/{len(targets)}] {c}  no final-judgment entry found')
        if i % 10 == 0:
            save_cache(cache)
        time.sleep(0.5)
    save_cache(cache)
    print(f'\nDONE: {hit} judgment date(s) found, {miss} without one -> {os.path.basename(CACHE)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
