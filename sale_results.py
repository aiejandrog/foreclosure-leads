#!/usr/bin/env python3
"""sale_results.py — what happened to each Miami-Dade sale this week, from the court docket.

THE GAP (2026-09-24 verification, defect 10). The scraper reads only RealForeclose's waiting list
(#Area_W), and past results there are login-gated (AUCTION-RESULTS-SPEC.md §1). So the board could
not see any of these, all real that week:
  - 2025-023462 was SOLD on 09-23 to the plaintiff, with a Chapter 13 petition entered that morning
    before the sale. The board showed nothing; the case simply fell off the next scrape.
  - 2024-006803 had an emergency motion filed 09-24 to cancel its 09-28 sale.
  - 2025-013918 had an amended final judgment entered 09-24, four days before its sale.
And PR #51 has to treat every sale-day case as held at 9am because nothing says otherwise.

The Miami-Dade Clerk's OCS docket (public, no login, $0 — the same endpoint docket.py,
gen_dockets.py and sale_history.py use) records every one of those events. This module reads it
for the cases with a sale in the window and writes one verdict per case to sale_results.json
(gitignored), which make_tracker bakes onto the row as `sr`.

WHAT THIS DOES NOT DO — ownership lines, on purpose:
  - It sets no bankruptcy-stay flag and gates nothing. The §362 stay is sale_history.py's
    (sale_bk_active). A bankruptcy line near the sale is reported here only as a SALE fact
    ("filed before the sale, the sale may not stand"), and outreach gates keep reading the stay
    flag. If this sees a bankruptcy that sale_history missed, that is a finding for its owner.
  - It reads no document images and prices nothing. An amended judgment's amount is shown only when
    the docket text itself carries it; otherwise the board says "amount changed, read the order".
  - Metadata is not evidence (CASE-REVIEW-PROCEDURE.md): every verdict carries the docket lines it
    rests on (`ev`), so a person can check it in one look.

Verdicts (st), most decisive first:
  vacated    an order set aside the sale or its certificate after the sale date
  held       the sale went ahead (certificate of sale, bid amount, sale deposit, disbursement, title)
  cancelled  an order or clerk line cancelled the sale, with no new date seen
  reset      the sale was cancelled or moved and the docket names a later date
  at_risk    something pending could stop it: a motion to cancel/postpone with no order yet, or a
             bankruptcy notice filed in the run-up
  scheduled  nothing on the docket changes the sale
Flags beside the verdict: amj (amended judgment date) + ama (its amount when printed), bkb
(bankruptcy line dated on or before the sale), obj (objection to the sale filed after it).

Run:  python sale_results.py                      # window: sales in the last 7 and next 30 days
      python sale_results.py --case 2025-023462-CA-01 --show    # one case, print the lines used
      python sale_results.py --report             # print the current verdicts, fetch nothing
"""
import argparse
import datetime
import glob
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'sale_results.json')
LEADS = os.path.join(HERE, 'leads_final.json')
ARCHIVE = os.path.join(HERE, 'auction_archive.json')
MD_CASE = re.compile(r'^\d{4}-\d{6}-(CA|CC)-\d{2}$', re.I)
PAST_DAYS = 7              # held sales stay reported for a week
AHEAD_DAYS = 30            # upcoming sales watched this far out
LOOKBACK_DAYS = 45         # docket lines this far before the sale date can change it
THROTTLE_S = float(os.environ.get('DOCKET_THROTTLE', '2.2'))   # gen_dockets.py's measured pace
DEADLINE_S = int(os.environ.get('SALE_RESULTS_DEADLINE', '420'))
VER = 1

# ---- docket line patterns ------------------------------------------------------------------------
# The OCS docket mixes filed papers ("Motion to Cancel Sale") with clerk event lines ("Mortgage
# Foreclosure Sale Cancelled"). A request is never an outcome, and a denial undoes nothing.
_MOTION = re.compile(r'\bmotion\b|\brequest\b|\bpetition to\b', re.I)
_DENY = re.compile(r'\bden(?:y|ied|ying|ial)\b|\bstricken\b|\bwithdraw', re.I)
_ORDER = re.compile(r'\border\b', re.I)
_SALEWORD = re.compile(r'\bsale\b|\bauction\b', re.I)
_VACATE = re.compile(r'vacat|set(?:ting)?\s+aside', re.I)
_HELD = re.compile(r'certificate of sale|bid amount|\bhigh(?:est)? bid|winning bid|sale deposit|'
                   r'\bdeposit\b.*\bsale\b|certificate of disbursement|certificate of title|'
                   r'sale\s+(?:was\s+)?held|\bsold\b', re.I)
_CANCEL = re.compile(r'cancel|postpon|continu|reschedul|reset', re.I)
_RESET = re.compile(r'reschedul|reset', re.I)
_NOTICE_SALE = re.compile(r'notice of (?:(?:foreclosure|judicial|mortgage foreclosure) )?sale|'
                          r're-?notice of sale|notice of (?:rescheduled|reset) sale', re.I)
_STOP_ASK = re.compile(r'cancel|postpon|continu|stay|stop|vacate', re.I)
_BK = re.compile(r'bankrupt|chapter\s*(?:7|11|13)\b|\bch\.?\s*13\b', re.I)
_BKCLOSE = re.compile(r'dismiss|discharg|relief from', re.I)
_OBJECT = re.compile(r'objection', re.I)
_AMENDED_FJ = re.compile(r'amended\s+(?:(?:uniform|consent|agreed|in rem|summary)\s+)*'
                         r'(?:final\s+)?(?:summary\s+)?judgment', re.I)
_MONEY = re.compile(r'\$\s*([\d]{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2})')
_DATE = re.compile(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b')
_ISO = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')


def _to_date(s):
    """A date from 'MM/DD/YYYY', 'YYYY-MM-DD' or an ISO timestamp; None when unreadable."""
    s = str(s or '').strip()
    m = _ISO.match(s)
    try:
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _DATE.match(s)
        if m:
            y = int(m.group(3))
            return datetime.date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    return None


def _dates_in(text):
    out = []
    for m in _DATE.finditer(text or ''):
        d = _to_date(m.group(0))
        if d:
            out.append(d)
    return out


def _money(text):
    m = _MONEY.search(text or '')
    if not m:
        return None
    try:
        return float(m.group(1).replace(',', ''))
    except ValueError:
        return None


def _entries(dockets):
    """Normalize raw OCS dockets or gen_dockets compact `ents` into [(date, text)], oldest first.
    `text` is description + comments: the clerk often puts the substance (a bid amount, a new sale
    date, 'CANCELLED PER BANKRUPTCY') in the comment, not the title."""
    out = []
    for e in dockets or []:
        if not isinstance(e, dict):
            continue
        d = _to_date(e.get('eventDate') or e.get('oDate') or e.get('d'))
        desc = str(e.get('docketDescrition') or e.get('docketDescription') or e.get('x') or '').strip()
        cmt = str(e.get('comments') or e.get('c') or '').strip()
        if not d or not (desc or cmt):
            continue
        out.append((d, desc, cmt))
    out.sort(key=lambda t: t[0])
    return out


def classify(dockets, sale, today=None, listed=None):
    """The verdict for ONE scheduled sale. `sale` is the sale date (date or string), `dockets` the
    OCS docket (raw or compact). Pure: no I/O, so the suite can pin every rule.

    `listed` is the last day RealForeclose still showed this sale as waiting (the archive's
    last_seen). A cancellation dated BEFORE that day belongs to an earlier sale date: the listing
    proves this one was still on after it. Without this, the 08-19 cancellation that led to a
    09-28 reset reads as the 09-28 sale being cancelled."""
    today = today or datetime.date.today()
    sale = sale if isinstance(sale, datetime.date) else _to_date(sale)
    listed = listed if isinstance(listed, datetime.date) or listed is None else _to_date(listed)
    res = {'st': 'scheduled', 'sale': sale.isoformat() if sale else '', 'ev': []}
    if not sale:
        res['st'] = 'unknown'
        res['why'] = 'no readable sale date'
        return res
    lo = sale - datetime.timedelta(days=LOOKBACK_DAYS)
    ents = [t for t in _entries(dockets) if t[0] >= lo]

    held = cancel = vacated = None
    new_date = None
    pending = []                       # (date, text) motions to stop the sale with no order yet
    bk_before = None
    obj = None
    amj = None

    def ev(d, desc, cmt):
        line = desc + ((' :: ' + cmt[:120]) if cmt and cmt.lower() != desc.lower() else '')
        item = {'d': d.isoformat(), 'x': line[:180]}
        if item not in res['ev']:
            res['ev'].append(item)

    for d, desc, cmt in ents:
        t = desc + ' ' + cmt
        is_motion = bool(_MOTION.search(desc)) and not _ORDER.search(desc)
        denied = bool(_DENY.search(t))

        # amended judgment — the amount moves before the sale (2025-013918, 09-24)
        if _AMENDED_FJ.search(desc) and not is_motion and not denied:
            amj = {'d': d.isoformat(), 'amt': _money(t)}
            ev(d, desc, cmt)
            continue

        # bankruptcy near the sale: a SALE fact here, never a stay flag (sale_history owns that).
        # A dismissal/discharge/relief line after it closes it for this purpose.
        if _BK.search(t):
            if _BKCLOSE.search(t):
                if bk_before and d >= bk_before and d <= sale:
                    bk_before = None
            elif d <= sale and bk_before is None:
                bk_before = d
                ev(d, desc, cmt)
            # 'SALE CANCELLED PER BANKRUPTCY' is also a cancellation; fall through for that

        if not (_SALEWORD.search(t) or _HELD.search(t)):
            continue

        # a line that SETS this very sale date (the reset or notice that created it) is history:
        # anything cancelled before it was an earlier sale
        if sale in _dates_in(t) and (_RESET.search(t) or _NOTICE_SALE.search(desc)) and d < sale:
            cancel, new_date, pending = None, None, []
            continue

        # an order undoing the sale or its certificate, on or after the sale
        if _VACATE.search(desc) and _ORDER.search(desc) and d >= sale and not denied:
            vacated = (d, desc, cmt)
            ev(d, desc, cmt)
            continue

        if _OBJECT.search(desc) and d >= sale:
            obj = obj or d
            ev(d, desc, cmt)
            continue

        if is_motion:
            if _STOP_ASK.search(t) and d <= sale:
                pending.append((d, desc, cmt))
            continue

        if denied:
            # an order denying a motion to cancel resolves the pending ask the other way
            if _ORDER.search(desc) and pending:
                pending = [p for p in pending if p[0] > d]
            continue

        if _HELD.search(t) and d >= sale and not _CANCEL.search(desc):
            if held is None:
                held = (d, desc, cmt)
                bid = _money(t) if re.search(r'bid|deposit|certificate of sale', t, re.I) else None
                if bid:
                    res['bid'] = bid
            ev(d, desc, cmt)
            continue

        if _CANCEL.search(t) and d <= sale + datetime.timedelta(days=1):
            if listed and d < listed:
                continue                 # predates a listing that still showed this sale on
            cancel = (d, desc, cmt)
            pending = []                 # the ask was granted (or the clerk pulled the sale anyway)
            later = [x for x in _dates_in(t) if x > sale]
            if later:
                new_date = max(later)
            ev(d, desc, cmt)
            continue

        # a fresh notice of sale naming a later date after a cancellation = the sale was moved
        if _NOTICE_SALE.search(desc) and cancel:
            later = [x for x in _dates_in(t) if x > sale]
            if later:
                new_date = max(later)
                ev(d, desc, cmt)

    if amj:
        res['amj'] = amj['d']
        if amj['amt']:
            res['ama'] = amj['amt']
    if bk_before:
        res['bkb'] = bk_before.isoformat()
    if obj:
        res['obj'] = obj.isoformat()

    if vacated:
        res['st'], res['d'] = 'vacated', vacated[0].isoformat()
        res['why'] = 'the court set the sale aside'
    elif held:
        res['st'], res['d'] = 'held', held[0].isoformat()
        res['why'] = 'sale went ahead' + (' (bid $%s)' % format(int(res['bid']), ',') if res.get('bid') else '')
        if bk_before:
            res['why'] += '; a bankruptcy was filed on or before the sale date, so the sale may not stand'
        if obj:
            res['why'] += '; an objection to the sale was filed'
    elif cancel and new_date:
        res['st'], res['d'], res['nd'] = 'reset', cancel[0].isoformat(), new_date.isoformat()
        res['why'] = 'sale moved to ' + new_date.strftime('%m/%d/%Y')
    elif cancel:
        res['st'], res['d'] = 'cancelled', cancel[0].isoformat()
        res['why'] = 'sale cancelled' + (' (bankruptcy)' if _BK.search(cancel[1] + ' ' + cancel[2]) else '')
    elif pending:
        p = pending[-1]
        res['st'], res['d'] = 'at_risk', p[0].isoformat()
        res['why'] = ('emergency ' if re.search(r'emergenc', p[1], re.I) else '') + \
            'motion to stop the sale filed, no ruling yet'
        for q in pending:
            ev(*q)
    elif bk_before and bk_before >= lo:
        res['st'], res['d'] = 'at_risk', bk_before.isoformat()
        res['why'] = 'bankruptcy filed before the sale'
    elif sale < today:
        # the date passed and the docket shows neither a result nor a cancellation yet: the
        # certificate of sale can lag a day or two. Say so instead of guessing either way.
        res['st'] = 'unknown'
        res['why'] = 'sale date passed; no result on the docket yet'
    res['ev'] = sorted(res['ev'], key=lambda e: e['d'])[-6:]
    return res


# ---- which cases, and I/O -------------------------------------------------------------------------
def window_cases(today=None, leads=None, archive=None, leads_day=None):
    """{case: (sale_date, last_listed)} for Miami-Dade foreclosure cases with a sale in [today-7, today+30].
    From today's lead file (the board) and the auction archive (cases the scrape already dropped:
    a held or cancelled sale leaves the waiting list the next morning, which is exactly when its
    result matters)."""
    today = today or datetime.date.today()
    lo, hi = today - datetime.timedelta(days=PAST_DAYS), today + datetime.timedelta(days=AHEAD_DAYS)
    out = {}

    def add(case, when, county='', seen=None):
        case = str(case or '').strip().upper()
        d = _to_date(when)
        if not case or not d or not MD_CASE.match(case):
            return
        if county and 'MIAMI' not in str(county).upper():
            return
        if not (lo <= d <= hi):
            return
        cur = out.get(case)
        if cur is None or d > cur[0]:
            out[case] = (d, seen)
        elif d == cur[0] and seen and (cur[1] is None or seen > cur[1]):
            out[case] = (d, seen)

    for r in leads or []:
        if r.get('sale_type') == 'TD':
            continue
        # on today's lead file = on the waiting list at the last scrape
        add(r.get('Case #') or r.get('case'), r.get('AuctionDate') or r.get('auction'),
            r.get('county'), leads_day)
    for case, v in (archive or {}).items():
        if isinstance(v, dict):
            add(case, v.get('auction'), v.get('county'), _to_date(v.get('last_seen')))
    return out


def _load(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def _save(data):
    tmp = OUT + '.tmp'
    json.dump(data, open(tmp, 'w', encoding='utf-8'), indent=1, sort_keys=True)
    os.replace(tmp, OUT)


BOARD_KEYS = ('st', 'd', 'why', 'nd', 'amj', 'ama', 'bkb', 'obj', 'bid', 'sale', 'ts')


def load_for_board(rows, path=OUT):
    """Stamp `sr` onto board rows whose case AND sale date match a verdict. Returns the count.
    'scheduled' with no flag carries nothing worth a chip, so it is not shipped."""
    res = _load(path, {})
    if not res:
        return 0
    n = 0
    for r in rows:
        v = res.get(str(r.get('case') or '').strip().upper())
        if not isinstance(v, dict):
            continue
        rd = _to_date(r.get('auction'))
        if not rd or rd.isoformat() != v.get('sale'):
            continue
        if v.get('st') == 'scheduled' and not (v.get('amj') or v.get('bkb')):
            continue
        r['sr'] = {k: v[k] for k in BOARD_KEYS if v.get(k) not in (None, '')}
        n += 1
    return n


def _pull(case):
    import docket as D            # the audited encrypt -> GetSingleCaseResult pair
    last = None
    for a in range(3):
        try:
            return D.pull(case)
        except Exception as e:
            last = e
            time.sleep(2.5 * (a + 1) + random.uniform(0, 1.5))
    raise last


def report(res, today=None):
    today = today or datetime.date.today()
    order = {'vacated': 0, 'held': 1, 'cancelled': 2, 'reset': 3, 'at_risk': 4, 'unknown': 5, 'scheduled': 6}
    rows = sorted(res.items(), key=lambda kv: (order.get(kv[1].get('st'), 9), kv[1].get('sale', '')))
    counts = {}
    for _, v in rows:
        counts[v.get('st')] = counts.get(v.get('st'), 0) + 1
    print('sale results: ' + ', '.join('%s %d' % (k, counts[k]) for k in order if counts.get(k)))
    for case, v in rows:
        if v.get('st') == 'scheduled' and not (v.get('amj') or v.get('bkb')):
            continue
        extra = ''
        if v.get('amj'):
            extra += ' | AMENDED JUDGMENT %s%s' % (v['amj'], (' $%s' % format(v['ama'], ',.2f')) if v.get('ama') else '')
        if v.get('bkb'):
            extra += ' | bankruptcy line %s' % v['bkb']
        print('  %-18s sale %s  %-9s %s%s' % (case, v.get('sale', ''), v.get('st', '').upper(),
                                            v.get('why', ''), extra))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--case', default='', help='one case (uses its lead/archive sale date, or --sale)')
    ap.add_argument('--sale', default='', help='sale date for --case, MM/DD/YYYY')
    ap.add_argument('--show', action='store_true', help='print the docket lines each verdict rests on')
    ap.add_argument('--report', action='store_true', help='print saved verdicts, fetch nothing')
    ap.add_argument('--limit', type=int, default=250)
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    today = datetime.date.today()
    res = _load(OUT, {})
    if a.report:
        report(res, today)
        return 0

    leads = _load(LEADS, [])
    for f in sorted(glob.glob(os.path.join(HERE, '*_leads.json'))):
        if os.path.basename(f) not in ('leads_final.json', 'leads_raw.json'):
            leads += [r for r in _load(f, []) if isinstance(r, dict)]
    try:
        leads_day = datetime.date.fromtimestamp(os.path.getmtime(LEADS))
    except OSError:
        leads_day = None
    win = window_cases(today, leads, _load(ARCHIVE, {}), leads_day)
    if a.case:
        c = a.case.strip().upper()
        sd = _to_date(a.sale) or (win.get(c) or (None, None))[0] or _to_date((res.get(c) or {}).get('sale'))
        if not sd:
            print('%s: no sale date known; pass --sale MM/DD/YYYY' % c)
            return 2
        todo = [(c, sd, (win.get(c) or (None, None))[1])]
    else:
        # nearest-to-now first: yesterday's and today's sales, then this week, then the rest
        todo = sorted(((c, d, seen) for c, (d, seen) in win.items()), key=lambda t: abs((t[1] - today).days))
        stamp = today.isoformat()
        todo = [t for t in todo
                if (res.get(t[0]) or {}).get('ts') != stamp or (res.get(t[0]) or {}).get('sale') != t[1].isoformat()]
        todo = todo[:a.limit]
    print('sale results: %d case(s) in the window, %d to read' % (len(win), len(todo)))

    start, ok, fail = time.time(), 0, 0
    for i, (c, sd, seen) in enumerate(todo, 1):
        if time.time() - start > DEADLINE_S:
            print('  .. %ds budget hit; the rest are read next run' % DEADLINE_S)
            break
        try:
            j = _pull(c)
            dks = (j or {}).get('dockets') or []
            if not dks:
                print('  [%3d] --   %s  no docket' % (i, c))
                fail += 1
            else:
                v = classify(dks, sd, today, seen)
                v.update({'ts': today.isoformat(), 'v': VER, 'status': str(j.get('caseStatus') or '')[:24]})
                res[c] = v
                ok += 1
                print('  [%3d] %-9s %s  sale %s  %s' % (i, v['st'].upper(), c, v['sale'], v.get('why', '')))
                if a.show:
                    for e in v['ev']:
                        print('         %s  %s' % (e['d'], e['x']))
                _save(res)
        except Exception as e:
            fail += 1
            print('  [%3d] ERR  %s  %s' % (i, c, str(e)[:80]))
        if i < len(todo):
            time.sleep(THROTTLE_S + random.uniform(0, 0.6))

    # forget verdicts for sales that left the window; the board only shows the window
    lo = today - datetime.timedelta(days=PAST_DAYS)
    for c in [c for c, v in res.items() if (_to_date(v.get('sale')) or today) < lo]:
        del res[c]
    _save(res)
    print('sale results: %d read, %d failed, %d on file -> sale_results.json' % (ok, fail, len(res)))
    report(res, today)
    return 0


if __name__ == '__main__':
    sys.exit(main())
