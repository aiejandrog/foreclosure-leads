"""One row per case on the dial list, and never a silently-hidden sooner sale date.

WHY THIS EXISTS
The county calendars list one case on MORE THAN ONE auction date — each posting is its own
calendar line with its own AITEM/AID id. Measured on the 2026-09-06 twin: 5 such cases, 3 of them
dialable. The caller was served the same human twice, and the two cards showed DIFFERENT sale
dates:

    502025CC010521XXXAMB   10/26/2026  and  10/27/2026   (same folio, same address)
    CACE-25-016452         09/23/2026  and  10/15/2026   (second copy un-enriched: no addr, no folio)
    CACE-25-012839         09/17/2026  and  10/06/2026   (same)
    2025-002125-CA-01      09/14/2026  and  09/22/2026   (same folio, both fully enriched)
    502026CC004037XXXAMB   10/14/2026  and  10/26/2026

Same folio on every pair we can check, so this is ONE parcel calendared twice (a reset sale), not
two properties. It is also visible in the raw Miami-Dade scrape: leads_final.json carries
2024-017395-CA-01 twice, AID 1512352 (09/08) and AID 1512353 (09/28).

THE TWO THINGS THAT MUST HOLD, and both are silent when they break:

  1. ONE ROW PER CASE on the phone. A dial list is a list of PEOPLE. Even a case that genuinely
     forecloses on several parcels is one owner and one conversation. (The BOARD keeps every
     calendar line on purpose — that is a per-property surface — so this is deduped in call_rows,
     not in the merge.)

  2. NEVER HIDE A SOONER SALE. We keep the fullest row, because the un-enriched copy has no
     address and would put an empty card on the phone. Completeness and earliest-date happen to
     agree on all five real cases, but they need not in general, and telling a homeowner the sale
     is 39 days out when it is 17 is the one error here that cannot be walked back. When a dropped
     copy is calendared earlier, the kept row must carry that date (dupd) so the card can say so.

Run: python _dupetest.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import call_mode  # noqa: E402

FAIL, PASS = [], []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok   ' if ok else '  FAIL ') + name + (('  — ' + str(detail)[:140]) if detail else ''))


def lead(case, auction, days, **extra):
    """A slim lead shaped like the merged feed call_rows consumes."""
    d = {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': auction, 'days': days,
         'tier': 'B', 'score': 50, 'phones': [{'number': '9545550101'}]}
    d.update(extra)
    return d


# ---- 1. the invariant, on synthetic pairs -------------------------------------------------------
print('\nONE ROW PER CASE')

# the real shape: the enriched copy is the EARLIER posting, the bare copy is the newer one
rows, _ = call_mode.call_rows([
    lead('CASE-A', '09/17/2026', 11, addr='1410 NW 48 PL, DEERFIELD BEACH, 33064',
         folio='484215052740', beds=3, baths=2, assessed_value=210000),
    lead('CASE-A', '10/06/2026', 30),
    lead('CASE-B', '09/20/2026', 14, addr='9 Only Ave', folio='111'),
], None, None)
by = {r['c']: r for r in rows}
rec('a case listed twice yields ONE row', len(rows) == 2 and len(by) == 2,
    '%d rows / %d cases' % (len(rows), len(by)))
rec('the kept row is the enriched one (a card with no address is useless)',
    by['CASE-A'].get('a', '').startswith('1410 NW 48 PL'), by['CASE-A'].get('a'))
rec('the kept row keeps its own sale date', by['CASE-A'].get('d') == 11, by['CASE-A'].get('d'))
rec('the collapse is recorded on the row', by['CASE-A'].get('dupn') == 1, by['CASE-A'].get('dupn'))
rec('no sooner-date warning when the kept row IS the soonest',
    by['CASE-A'].get('dupd') in (None, ''), by['CASE-A'].get('dupd'))
rec('an unduplicated case is untouched',
    'dupn' not in by['CASE-B'] and 'dupd' not in by['CASE-B'], sorted(by['CASE-B'])[:6])

# ---- 2. the dangerous direction: fullest row is the LATER posting -------------------------------
print('\nA SOONER SALE IS NEVER HIDDEN')
rows2, _ = call_mode.call_rows([
    lead('CASE-C', '10/15/2026', 39, addr='4851 NW 21 ST, LAUDERHILL, 33313',
         folio='494125DH0080', beds=4, baths=2, assessed_value=305000),
    lead('CASE-C', '09/23/2026', 17),                      # bare, but sells 22 days SOONER
], None, None)
c = {r['c']: r for r in rows2}['CASE-C']
rec('still one row', len(rows2) == 1, len(rows2))
rec('kept the row that actually has a property on it', str(c.get('a', '')).startswith('4851'),
    c.get('a'))
rec('and carries the EARLIER calendared date forward', c.get('dupd') == '09/23/2026', c.get('dupd'))
rec('the earlier date is not silently dropped', bool(c.get('dupd')), c.get('dupd'))

# ---- 3. three copies, and the caseless row is never merged --------------------------------------
print('\nEDGE CASES')
rows3, _ = call_mode.call_rows([
    lead('CASE-D', '10/20/2026', 44, addr='1 Full St', folio='9', beds=3, assessed_value=1),
    lead('CASE-D', '10/01/2026', 25),
    lead('CASE-D', '09/25/2026', 19),
], None, None)
d = rows3[0]
rec('three postings collapse to one', len(rows3) == 1, len(rows3))
rec('and the count says two were dropped', d.get('dupn') == 2, d.get('dupn'))
rec('the SOONEST of the dropped dates is the one carried', d.get('dupd') == '09/25/2026',
    d.get('dupd'))

same, _ = call_mode.call_rows([
    lead('CASE-E', '10/26/2026', 50, addr='2812 S GARDEN DR 305', folio='00434417430003050'),
    lead('CASE-E', '10/27/2026', 51, addr='2812 S GARDEN DR 305', folio='00434417430003050'),
], None, None)
rec('equally-complete copies collapse to the earlier date (the real 502025CC010521 case)',
    len(same) == 1 and same[0].get('d') == 50, '%d rows, d=%s' % (len(same), same[0].get('d')))

# ---- 4. the cap counts PEOPLE, not calendar lines -----------------------------------------------
print('\nTHE CAP')
many = []
for i in range(30):
    many.append(lead('BULK-%03d' % i, '10/20/2026', 44, addr='%d St' % i, folio=str(i)))
many.append(lead('BULK-000', '11/20/2026', 75))          # a duplicate of the first
capped, total = call_mode.call_rows(many, None, None, cap=30)
rec('a duplicate does not eat a slot under the cap',
    len({r['c'] for r in capped}) == len(capped) == 30, '%d rows / %d cases'
    % (len(capped), len({r['c'] for r in capped})))
rec('the qualifying total counts people, not postings', total == 30, total)

# ---- 5. and it holds on the real feed ------------------------------------------------------------
print('\nREAL FEED')
try:
    import collections
    import paths
    src = open(paths.TWIN, encoding='utf-8', errors='replace').read()
    m = (re.search(r'(?:const|let|var)\s+RAW\s*=\s*(\[.*?\]);\n', src, re.S)
         or re.search(r'(?:const|let|var)\s+DATA\s*=\s*(\[.*?\]);\n', src, re.S))
    if not m:
        rec('twin readable', True, 'RAW is encrypted on this board — synthetic checks stand alone')
    else:
        raw = json.loads(m.group(1))
        rrows, _t = call_mode.call_rows(raw, None, None)
        cnt = collections.Counter(r['c'] for r in rrows)
        worst = {k: v for k, v in cnt.items() if v > 1}
        rec('no case is dialable twice on the live feed', not worst, worst)
        src_dupes = len([k for k, v in collections.Counter(
            r.get('case') for r in raw if r.get('case')).items() if v > 1])
        rec('the feed itself still carries the duplicates (we fix them, we do not hide them)',
            src_dupes > 0, '%d duplicated case(s) upstream' % src_dupes)
except Exception as e:
    rec('real feed check', False, repr(e))

# ---- 6. the board side: one case must not sit in two worker lanes ------------------------------
# This is the failure the phone-only fix would have left behind. The Morning Worker mails per
# lane, and the FTSA/TCPA touch ladder counts per HUMAN — so a case in both `urgent` and `active`
# is a licence to mail the same owner twice in a morning. _lanetest asserts lane exclusivity
# against the BUILT board, so it can only go green after a rebuild; this asserts the same property
# against the merge pass that feeds it, which is testable right here.
print('\nBOARD LANES')
try:
    import datetime

    def _live_days(r):
        m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', str(r.get('auction') or ''))
        if not m:
            return None
        return (datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
                - datetime.date.today()).days

    def _lane(r):
        if r.get('st') == 'BAL':
            return 'balloon'
        d = _live_days(r)
        if d is None or r.get('st') == 'LP':
            return 'early'
        if 0 <= d <= 7:
            return 'urgent'
        if 8 <= d <= 45:
            return 'active'
        return 'other'

    def _multi_lane(rows):
        seen = {}
        for r in rows:
            c = r.get('case')
            if c:
                seen.setdefault(c, set()).add(_lane(r))
        real = ('urgent', 'active', 'early', 'balloon')
        return {c: sorted(v) for c, v in seen.items() if len({x for x in v if x in real}) > 1}

    # a case posted on two dates that straddle the urgent/active boundary — the real shape of
    # CACE-25-012839 (09/17 -> urgent, 10/06 -> active on 2026-09-10)
    soon = datetime.date.today() + datetime.timedelta(days=5)
    late = datetime.date.today() + datetime.timedelta(days=26)
    fmt = lambda d: '%d/%d/%d' % (d.month, d.day, d.year)          # noqa: E731
    board = [
        {'case': 'SPLIT', 'st': 'FC', 'auction': fmt(soon), 'days': 5,
         'addr': '1410 NW 48 PL', 'folio': '484215052740', 'beds': 3},
        {'case': 'SPLIT', 'st': 'FC', 'auction': fmt(late), 'days': 26, 'addr': '', 'folio': ''},
        {'case': 'SOLO', 'st': 'FC', 'auction': fmt(late), 'days': 26, 'addr': '9 St', 'folio': '1'},
    ]
    rec('the bug: one case lands in two lanes before dedupe',
        list(_multi_lane(board)) == ['SPLIT'], _multi_lane(board))
    dd, ncol, nsoon = call_mode.dedupe_calendar_rows(board, key='case', days='days',
                                                     date='auction')
    rec('after the merge pass, no case is in two lanes', not _multi_lane(dd), _multi_lane(dd))
    rec('and the enriched copy is the one that survived',
        {r['case'] for r in dd} == {'SPLIT', 'SOLO'}
        and next(r for r in dd if r['case'] == 'SPLIT').get('folio') == '484215052740')
    rec('nothing is lost — every case still present', len(dd) == 2 and ncol == 1, '%d rows' % len(dd))

    # and on the real feed
    import paths
    src2 = open(paths.TWIN, encoding='utf-8', errors='replace').read()
    m2 = re.search(r'(?:const|let|var)\s+RAW\s*=\s*(\[.*?\]);\n', src2, re.S)
    if m2:
        raw2 = json.loads(m2.group(1))
        before = _multi_lane(raw2)
        after_rows, ncol2, _ = call_mode.dedupe_calendar_rows(list(raw2), key='case',
                                                              days='days', date='auction')
        after = _multi_lane(after_rows)
        rec('live feed: multi-lane cases go to zero', not after,
            'before=%d (%s) after=%d' % (len(before), list(before)[:3], len(after)))
        rec('live feed: no case disappears',
            {r.get('case') for r in raw2 if r.get('case')}
            == {r.get('case') for r in after_rows if r.get('case')},
            '%d rows -> %d, %d collapsed' % (len(raw2), len(after_rows), ncol2))
except Exception as e:
    rec('board lane check', False, repr(e))

print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAILED: ' + f)
sys.exit(1 if FAIL else 0)
