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
  redeemed   the clerk records the property redeemed after the sale (the debt was paid)
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
AMENDED_DAYS = 60          # an amended judgment this far back still changed THIS sale's amount
                           # (2018-026274: amended 08-11 together with the reset to 09-28)
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
# Real Miami-Dade wording (2025-023462, sold 09-23-2026): 'Bid Amount :: PL/53643' (BIDSCV) and
# 'Mortgage Foreclosure Deposit :: 24-PL/53643/DOC STAMPS' (MFDPCV) on the sale day; the certificate
# of sale follows a day or more later. 'Mortgage Foreclosure Sale' (SALE, a Hearing) is only the
# calendar slot and appears before every sale, held or not, so it is NOT evidence of anything.
_HELD = re.compile(r'certificate of sale|bid amount|\bhigh(?:est)? bid|winning bid|sale deposit|'
                   r'foreclosure deposit|'
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
# Orders spell the dates out: 'Sale Date: AUGUST 24, 2026 AND RESET FOR SEPTEMBER 23, 2026 AT 9:00'
_MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
           'october', 'november', 'december')
_WDATE = re.compile(r'\b(' + '|'.join(_MONTHS) + r')\s+(\d{1,2}),?\s+(\d{4})\b', re.I)
_HELD_CODES = {'BIDSCV', 'MFDPCV'}
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


def _date_spans(text):
    """[(start, end, date)] for every date written on the line, in reading order."""
    text = text or ''
    out = []
    for m in _DATE.finditer(text):
        d = _to_date(m.group(0))
        if d:
            out.append((m.start(), m.end(), d))
    for m in _WDATE.finditer(text):
        try:
            out.append((m.start(), m.end(),
                        datetime.date(int(m.group(3)), _MONTHS.index(m.group(1).lower()) + 1, int(m.group(2)))))
        except ValueError:
            pass
    return sorted(out, key=lambda t: t[0])


# A docket line can name dates that are not the sale: a notice of sale reciting the surplus-claim
# deadline ('... SURPLUS CLAIMS MUST BE FILED BY 12/01/2026'), an objection deadline, a hearing
# set or continued. Taking the latest date on the line as the sale date pushed a sale days away
# months out, which dropped it off the urgent lane and out of Call Mode.
#
# The rule is deliberately narrow, so everything else reads exactly as before: a date is dropped
# only when the words before it (back to the previous date, within its own part of the line) hold
# a claim / surplus / objection / hearing / deadline word and no sale-SETTING phrase, in either
# order ('RESET PER HEARING TO 10/26' and 'HEARING HELD; SALE RESET FOR 10/26' are both a sale).
# A setting phrase is the shape a new sale date is written in ('Sale Date:', 'SALE OF', 'RESET
# ... TO', 'NEW SALE', 'RESALE'), never a bare 'the sale': the surplus sentence talks about the
# sale ('... WITHIN 60 DAYS AFTER THE SALE, 11/30/2026') without setting one. Known ambiguity, left
# on the old side: 'HEARING RESET TO 11/05' keeps its date, because 'CANCELLED PER HEARING, RESET TO
# 11/09' is written the same way and is a sale.
_DEADLINE_WORD = re.compile(r'\bclaim(?:s|ed|ing|ant|ants)?\b|\bsurplus\b|\bdeadlines?\b|'
                            r'\bhearings?\b|\bobjections?\b', re.I)
_SETS_SALE = re.compile(r'\bsale\s+(?:date|of|on|set|scheduled|for|to)\b|\bnew\s+sale\b|\bre-?sale\b|'
                        r'\breset\b|\breschedul|\bauction\s+(?:date|of|on|for)\b', re.I)


def _line_dates(*parts):
    """(sale dates, every date) written in these parts of a docket line (description, comment), in
    reading order. Each part is read on its own, so a docket title says nothing about a date in the
    comment. See _DEADLINE_WORD."""
    keep, every = [], []
    for text in parts:
        text = text or ''
        prev_end = 0
        for start, end, d in _date_spans(text):
            ctx = text[prev_end:start]
            prev_end = end
            every.append(d)
            if _DEADLINE_WORD.search(ctx) and not _SETS_SALE.search(ctx):
                continue
            keep.append(d)
    return keep, every


def _sale_dates_in(*parts):
    """The dates on a docket line that name a SALE (see _line_dates)."""
    return _line_dates(*parts)[0]


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
        out.append((d, desc, cmt, str(e.get('docketCode') or e.get('k') or '').strip().upper()))
    out.sort(key=lambda t: t[0])
    return out


def classify(dockets, sale, today=None, listed=None):
    """The verdict for ONE scheduled sale. `sale` is the sale date (date or string), `dockets` the
    OCS docket (raw or compact). Pure: no I/O, so the suite can pin every rule.

    Which sale a cancellation belongs to is read from its own text, not from when RealForeclose
    last listed the case: the waiting list can keep showing a cancelled item (the 2026-09-22 list
    carried six sales the docket shows no bid for). A cancellation naming only other, earlier dates
    was an earlier sale's; one naming this date, or no date, is this one's. `listed` is accepted
    and ignored, kept so callers need not change."""
    today = today or datetime.date.today()
    sale = sale if isinstance(sale, datetime.date) else _to_date(sale)
    listed = listed if isinstance(listed, datetime.date) or listed is None else _to_date(listed)
    res = {'st': 'scheduled', 'sale': sale.isoformat() if sale else '', 'ev': []}
    if not sale:
        res['st'] = 'unknown'
        res['why'] = 'no readable sale date'
        return res
    lo = sale - datetime.timedelta(days=LOOKBACK_DAYS)
    ents = [t for t in _entries(dockets) if t[0] >= sale - datetime.timedelta(days=AMENDED_DAYS)]

    held = cancel = vacated = redeemed = None
    challenged = None                  # a motion to set the sale aside, filed on/after the sale
    held_lines, cert = [], None        # for sale_held, named as #53's miami_case_timeline names it
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

    for d, desc, cmt, code in ents:
        t = desc + ' ' + cmt
        is_motion = bool(_MOTION.search(desc)) and not _ORDER.search(desc)
        denied = bool(_DENY.search(t))

        # amended judgment — the amount moves before the sale (2025-013918, 09-24)
        if _AMENDED_FJ.search(desc) and not is_motion and not denied:
            amj = {'d': d.isoformat(), 'amt': _money(t)}
            ev(d, desc, cmt)
            continue
        if d < lo:
            continue                     # older lines only count for the amended judgment above

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

        if not (_SALEWORD.search(t) or _HELD.search(t) or code in _HELD_CODES):
            continue

        # a line that SETS this very sale date (the reset or notice that created it) is history:
        # anything cancelled before it was an earlier sale
        # ('Order Cancelling Foreclosure Sale :: Sale Date: AUGUST 17, 2026 AND RESET FOR SEPTEMBER
        # 28, 2026' SET the 09-28 sale; a line naming this date AND a later one cancelled it.)
        _ds, _all = _line_dates(desc, cmt)
        if (sale in _ds and not any(x > sale for x in _ds) and d < sale and not is_motion
                and (_RESET.search(t) or _NOTICE_SALE.search(desc))):
            cancel, new_date, pending = None, None, []
            continue

        # an order undoing the sale or its certificate, on or after the sale
        if _VACATE.search(desc) and _ORDER.search(desc) and d >= sale and not denied:
            vacated = (d, desc, cmt)
            ev(d, desc, cmt)
            continue

        # 'Mortgage Foreclosure Deposit :: PROPERTY REDEEMED AFTER SALE BY: ...' (2026-007129): the
        # deposit line that would read as held says the owner's side paid it off instead.
        if re.search(r'redeem|redemption', t, re.I) and d >= sale and not is_motion and not denied:
            redeemed = redeemed or (d, desc, cmt)
            ev(d, desc, cmt)
            continue

        if is_motion and _VACATE.search(t) and d >= sale:
            challenged = challenged or d   # 2025-000483: 'Motion to Set Aside/Vacate' on the sale day
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

        if (_HELD.search(t) or code in _HELD_CODES) and d >= sale and not _CANCEL.search(desc):
            held_lines.append(desc + ((' :: ' + cmt[:80]) if cmt else ''))
            if re.search(r'certificate of (?:sale|title)', desc, re.I):
                cert = cert or d
            if held is None:
                held = (d, desc, cmt)
                bid = _money(t) if re.search(r'bid|deposit|certificate of sale', t, re.I) else None
                if bid:
                    res['bid'] = bid
            # 'Bid Amount :: PL/53643' = the plaintiff took it back (no third-party buyer)
            if re.search(r'bid|deposit', desc, re.I) and re.search(r'(?:^|[\s/-])PL/', cmt):
                res['pl'] = 1
            ev(d, desc, cmt)
            continue

        if _CANCEL.search(t) and d <= sale + datetime.timedelta(days=1):
            # a line whose only dates are a hearing or a deadline still says which sale it was
            # about when those dates are all earlier than this one, as it did before the filter
            _cd = _ds or _all
            if _cd and sale not in _cd and not any(x > sale for x in _cd):
                continue                 # it names an earlier sale date, not this one
            cancel = (d, desc, cmt)
            pending = []                 # the ask was granted (or the clerk pulled the sale anyway)
            later = [x for x in _ds if x > sale]
            if later:
                new_date = max(later)
            ev(d, desc, cmt)
            continue

        # a fresh notice of sale naming a later date after a cancellation = the sale was moved
        if _NOTICE_SALE.search(desc) and cancel:
            later = [x for x in _ds if x > sale]
            if later:
                new_date = max(later)
                ev(d, desc, cmt)

    # THE BOARD'S DATE CAN BE STALE (sweep 09-24: 2009-074573 and 2024-000195 listed 09-28, the
    # docket says 2027-01-04 and 2026-11-09). If the newest line that names any sale date names a
    # LATER one — a notice of sale, or a reset order — the sale was moved, however long ago.
    if not (held or vacated or cancel):
        # A sale-setting line whose only dates are a deadline or a hearing is still the newest
        # word on the sale: it stays in, naming no later date (None), so an older reset cannot
        # jump over it.
        _setting = []
        for d, desc, cmt, code in _entries(dockets):
            if (_MOTION.search(desc) and not _ORDER.search(desc)) or not (
                    _NOTICE_SALE.search(desc) or code == 'NOTSCV'
                    or (_ORDER.search(desc) and _SALEWORD.search(desc) and _CANCEL.search(desc + ' ' + cmt))):
                continue
            _ds, _all = _line_dates(desc, cmt)
            if _all:
                _setting.append((d, max(_ds) if _ds else None))
        if _setting:
            _last = max(_setting, key=lambda x: x[0])
            if _last[1] and _last[1] > sale:
                cancel, new_date = (_last[0], 'docket sets a later sale date', ''), _last[1]
                res['ev'].append({'d': _last[0].isoformat(), 'x': 'newest sale-setting line names %s' % _last[1].strftime('%m/%d/%Y')})

    if amj:
        res['amj'] = amj['d']
        if amj['amt']:
            res['ama'] = amj['amt']
    if bk_before:
        res['bkb'] = bk_before.isoformat()
    if obj:
        res['obj'] = obj.isoformat()

    if redeemed:
        res['st'], res['d'] = 'redeemed', redeemed[0].isoformat()
        res['why'] = 'property redeemed after the sale: the debt was paid, there is no sale to work'
    elif vacated:
        res['st'], res['d'] = 'vacated', vacated[0].isoformat()
        res['why'] = 'the court set the sale aside'
    elif held:
        res['st'], res['d'] = 'held', held[0].isoformat()
        # Same vocabulary as miami_case_timeline.sale_held() / status.sale_outcome (PR #53), so the
        # two readers can be folded into one: bid (sale_bid, BIDSCV) and deposit (sale_deposit,
        # MFDPCV) entries say the sale was HELD; only a certificate says it went through.
        res['sale_outcome'] = 'sold' if cert else 'held_no_certificate_yet'
        res['sale_held'] = {'date': held[0].isoformat(), 'evidence': held_lines[:4],
                            'certificate': cert.isoformat() if cert else None,
                            'bankruptcy_same_day': bool(bk_before and bk_before == sale)}
        res['why'] = ('sold back to the plaintiff' if res.get('pl') else 'sale went ahead') + \
            (' (bid $%s)' % format(int(res['bid']), ',') if res.get('bid') else '')
        if bk_before and bk_before == sale:
            res['why'] += ('; a bankruptcy was filed on the sale day: if it was entered before the '
                           'sale started, the sale may not stand')
        elif bk_before:
            res['why'] += '; a bankruptcy was filed before the sale date, so the sale may not stand'
        if obj:
            res['why'] += '; an objection to the sale was filed'
    elif cancel and new_date:
        res['st'], res['d'], res['nd'] = 'reset', cancel[0].isoformat(), new_date.isoformat()
        res['why'] = 'sale moved to ' + new_date.strftime('%m/%d/%Y')
    elif cancel:
        res['st'], res['d'] = 'cancelled', cancel[0].isoformat()
        res['why'] = 'sale cancelled' + (' (bankruptcy)' if _BK.search(cancel[1] + ' ' + cancel[2]) else '')
    elif sale < today and not (cancel and new_date):
        # the date passed and the docket shows neither a result nor a cancellation yet: the bid
        # and certificate can lag a day or two. Say so, with what IS on the docket, rather than
        # guessing either way. (A pending motion or a bankruptcy line on a PASSED sale is no
        # longer a risk to it; it is a reason the result may never come.)
        res['st'] = 'unknown'
        res['sale_outcome'] = 'unknown_no_certificate'
        res['why'] = 'sale date passed; no result on the docket yet'
        if bk_before:
            res['why'] += '; a bankruptcy was filed %s, which usually stops the sale' % (
                'on the sale day' if bk_before == sale else 'before it')
        if challenged:
            res['why'] += '; a motion to set the sale aside was filed'
        elif pending:
            res['why'] += '; a motion to stop the sale was pending'
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


BOARD_KEYS = ('st', 'd', 'why', 'nd', 'amj', 'ama', 'bkb', 'obj', 'bid', 'pl', 'sale', 'ts')
# A verdict older than this is not shown: a docket read that keeps failing must not leave last
# week's "SALE AT RISK" on the board after the docket has moved on. The nightly reads every case
# in the window, so two days allows one missed night.
# Three since read_order(): a sale more than HOT_AHEAD days out is re-read on a rotation that takes
# about two nights for a 200-case window. Sales near today are re-read every night regardless.
MAX_AGE_DAYS = 3
EV_SHIP = 3            # newest docket lines behind the verdict, shipped so the chip can cite them


def load_for_board(rows, path=OUT, today=None):
    """Stamp `sr` onto board rows whose case AND sale date match a fresh verdict. Returns the count.
    'scheduled' with no flag carries nothing worth a chip, so it is not shipped.

    A MOVED sale also moves the row's clock: when the docket's newest sale-setting line names a
    later date, `auction` becomes that date (the listed one is kept in sr.was), so the countdown,
    the payoff accrual and Call Mode's "sale already passed" cut follow the date the court set,
    not the stale calendar listing."""
    res = _load(path, {})
    if not res:
        return 0
    today = today or datetime.date.today()
    n = 0
    for r in rows:
        v = res.get(str(r.get('case') or '').strip().upper())
        if not isinstance(v, dict):
            continue
        rd = _to_date(r.get('auction'))
        if not rd or rd.isoformat() != v.get('sale'):
            continue
        ts = _to_date(v.get('ts'))
        if not ts or (today - ts).days > MAX_AGE_DAYS:
            continue
        if v.get('st') == 'scheduled' and not (v.get('amj') or v.get('bkb')):
            continue
        sr = {k: v[k] for k in BOARD_KEYS if v.get(k) not in (None, '')}
        ev = [{'d': e.get('d', ''), 'x': str(e.get('x') or '')[:140]}
              for e in (v.get('ev') or []) if isinstance(e, dict)][-EV_SHIP:]
        if ev:
            sr['ev'] = ev
        nd = _to_date(v.get('nd'))
        if v.get('st') == 'reset' and nd and nd > rd:
            sr['was'] = rd.isoformat()
            r['auction'] = nd.strftime('%m/%d/%Y')
        r['sr'] = sr
        n += 1
    return n


HOT_BEHIND, HOT_AHEAD = 2, 3     # sales this close to today are re-read every night


def read_order(win, res, today):
    """Which window cases to read tonight, in order. A night's budget (DEADLINE_S) does not cover a
    200-case window, and nearest-first alone re-read the same near half every night, so sales 2-4
    weeks out were never read. Order: sales within HOT_BEHIND days back / HOT_AHEAD days ahead
    first (a result or a last-minute motion lands there), then everything else by how old its
    verdict is (never read first), nearest sale breaking ties. Cases already read today for the
    same sale are skipped.

    What this guarantees is that no case starves, not a fixed cycle: the hot cases take their
    share every night, so the rest are covered in (cold cases / (reads per night - hot cases))
    nights. A verdict older than MAX_AGE_DAYS is hidden rather than shown stale, and main()
    prints coverage_gap() so a window that outgrows the budget shows up in the refresh log."""
    stamp = today.isoformat()
    out = []
    for c, (d, seen) in win.items():
        v = res.get(c) or {}
        if v.get('ts') == stamp and v.get('sale') == d.isoformat():
            continue
        dist = (d - today).days
        hot = -HOT_BEHIND <= dist <= HOT_AHEAD
        ts = _to_date(v.get('ts')) if v.get('sale') == d.isoformat() else None
        age = (today - ts).days if ts else 10 ** 6
        out.append((0 if hot else 1, 0 if hot else -age, abs(dist), c, d, seen))
    out.sort()
    return [(c, d, seen) for _, _, _, c, d, seen in out]


def coverage_gap(win, res, today):
    """Window cases with no verdict the board would show (none, another sale's, or older than
    MAX_AGE_DAYS). Above 0 night after night means the budget is short for the window."""
    n = 0
    for c, (d, _) in win.items():
        v = res.get(c) or {}
        ts = _to_date(v.get('ts'))
        if v.get('sale') != d.isoformat() or not ts or (today - ts).days > MAX_AGE_DAYS:
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
    order = {'redeemed': 0, 'vacated': 0, 'held': 1, 'cancelled': 2, 'reset': 3, 'at_risk': 4, 'unknown': 5, 'scheduled': 6}
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
        todo = read_order(win, res, today)
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
    gap = coverage_gap(win, res, today)
    if gap:
        print('sale results: %d of %d window case(s) have no current verdict and show no chip; if this '
              'stays above 0 night after night, raise SALE_RESULTS_DEADLINE (now %ds)' % (gap, len(win), DEADLINE_S))
    report(res, today)
    return 0


if __name__ == '__main__':
    sys.exit(main())
