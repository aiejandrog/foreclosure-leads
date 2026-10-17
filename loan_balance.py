"""loan_balance — what a recorded mortgage probably owes TODAY, not what it was recorded for.

WHY THIS EXISTS (2026-10-17, Alejandro, on 8892 N Isles Cir, Tamarac): the board summed recorded
face amounts ($190,000 + $70,000 + $29,166 = $289,166) against a $310K-$367K house and read it as
no equity. PropStream showed the same three loans at an estimated $263,530 and ~$104K of equity.
Nothing in a county index carries a rate or a term (Broward AcclaimWeb, Miami-Dade Official
Records and the Palm Beach index publish a date, parties and a consideration amount, nothing
else), so PropStream ESTIMATES both: its "Est. Rate" on a loan recorded 12/21/2021 is 3.11%, the
Freddie Mac PMMS 30-year average for the week of 12/02/2021, and 3.11% / 360 months / 57 payments
reproduces its $170,375 to the dollar. This module does the same arithmetic:

    rate  = PMMS 30-year fixed average for the month the mortgage was recorded
    term  = 360 months
    paid  = whole months from recording to the as-of date (or to the filing date of the case that
            is foreclosing it: a loan in foreclosure stopped amortizing before the suit)
    B     = P * ((1+r)^n - (1+r)^t) / ((1+r)^n - 1)

It is an ESTIMATE and is labelled as one everywhere it shows. What it cannot know:
  * the real note rate and term (a 15-year loan pays down much faster, an ARM differently);
  * arrears, default interest, late fees and foreclosure costs on a loan in default, which sit ON
    TOP of the amortized principal (the judgment, when it posts, is the real number);
  * a revolving line of credit's draw: no amortization, the recorded line is kept;
  * an equity-share agreement (Hometap, Unison, Point, Splitero...): not a loan. The payoff is a
    share of the home's value at settlement and usually exceeds what was advanced, so the recorded
    figure is kept as a floor, never amortized down.

Rates come from `pmms_monthly.json` (tracked, public Freddie Mac data, monthly means of the weekly
survey). `python loan_balance.py --refresh` re-reads Freddie Mac's PMMS_history.csv into
DEALFLOW_DIR/pmms_monthly.json, which wins when it is newer. A recording month the table does not
cover keeps its recorded amount (never a guessed rate); a month after the table's last one uses the last.
"""
import json
import os
import re
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(HERE, 'pmms_monthly.json')
PMMS_CSV_URL = 'https://www.freddiemac.com/pmms/docs/PMMS_history.csv'
TERM_MONTHS = 360

# Not loans: the payoff is a share of value at settlement. Matched on the recorded party.
EQUITY_SHARE_RE = re.compile(r'HOMETAP|HOME\s*TAP|UNISON|POINT\s+DIGITAL|POINT\s+HOME|SPLITERO|'
                             r'UNLOCK\s+(?:PARTNERS|TECH)|HOMEBOUND|NOAH\s+(?:EQUITY|HOME)|'
                             r'REAL\s*FI|EQUITY\s+PARTNERS', re.I)
# Revolving lines: the balance is whatever was drawn; the recorded line is the honest ceiling.
CREDIT_LINE_RE = re.compile(r'LINE\s+OF\s+CREDIT|\bHELOC\b|CREDIT\s+LINE|REVOLV', re.I)


def _dealflow_dir():
    try:
        import paths as P
        return P.DEALFLOW_DIR
    except Exception:
        return os.environ.get('DEALFLOW_DIR') or os.path.join(os.path.expanduser('~'), 'DEALFLOW')


_TABLE = None


def _load_one(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        rates = d.get('rates') if isinstance(d, dict) and 'rates' in d else d
        return {k: v for k, v in rates.items()
                if re.match(r'^\d{4}-\d{2}$', k) and isinstance(v, (list, tuple)) and v and v[0]}
    except Exception:
        return {}


def table(refresh=False):
    """{'YYYY-MM': [rate30, rate15]} — the newer-ending of the bundled and the downloaded table."""
    global _TABLE
    if _TABLE is not None and not refresh:
        return _TABLE
    best = _load_one(BUNDLED)
    local = _load_one(os.path.join(_dealflow_dir(), 'pmms_monthly.json'))
    if local and (not best or max(local) >= max(best)):
        merged = dict(best)
        merged.update(local)
        best = merged
    _TABLE = best
    return _TABLE


def _ym(d):
    return '%04d-%02d' % (d.year, d.month)


def _parse(d):
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = str(d or '').strip()
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        y, mo, dd = map(int, m.groups())
    else:
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
        if not m:
            return None
        mo, dd, y = map(int, m.groups())
    try:
        return date(y, mo, dd)
    except ValueError:
        return None


def rate_for(recorded, tab=None):
    """PMMS 30-year average for the recording month, or None when the table does not reach it."""
    d = _parse(recorded)
    tab = table() if tab is None else tab
    if not d or not tab:
        return None
    k = _ym(d)
    if k in tab:
        return float(tab[k][0])
    if k > max(tab):                       # newer than the table: the latest survey is the best read
        return float(tab[max(tab)][0])
    return None                            # older than the table: no guessed rate


def months_between(a, b):
    """Whole monthly payments made from recording date a to date b (never negative)."""
    a, b = _parse(a), _parse(b)
    if not a or not b or b <= a:
        return 0
    n = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        n -= 1
    return max(0, n)


def amortized(principal, annual_rate, term_months, paid):
    """Remaining principal after `paid` level payments. 0 once the term is paid out."""
    if paid >= term_months:
        return 0.0
    if paid <= 0:
        return float(principal)
    r = annual_rate / 1200.0
    if r <= 0:
        return principal * (term_months - paid) / float(term_months)
    g = (1 + r) ** term_months
    return principal * (g - (1 + r) ** paid) / (g - 1)


def estimate(lien, as_of=None, stop=None, tab=None):
    """One recorded mortgage row -> {'est', 'rate', 'paid', 'how'} or None (no estimate possible).

    `lien` is a chain row ({'d', 'amt', 'party', 'doc'?, 'bal'?}). `stop` is the date payments are
    taken to have stopped (the foreclosure's filing date for the loan being foreclosed).
    how: 'amort' (PMMS rate, 360 months), 'vendor' (BatchData's own current estimate),
         'eqshare' / 'credit_line' (recorded figure kept, see the module docstring),
         'face' (no usable date, or a month the rate table does not cover: the recorded amount is
         kept, never dropped -- dropping it would understate the debt)."""
    if not isinstance(lien, dict):
        return None
    try:
        amt = float(lien.get('amt') or 0)
    except (TypeError, ValueError):
        amt = 0.0
    if amt <= 0:
        return None
    try:
        bal = float(lien.get('bal') or 0)
    except (TypeError, ValueError):
        bal = 0.0
    if bal > 0:
        return {'est': int(round(bal)), 'rate': None, 'paid': None, 'how': 'vendor'}
    party = str(lien.get('party') or '')
    if EQUITY_SHARE_RE.search(party):
        return {'est': int(round(amt)), 'rate': None, 'paid': None, 'how': 'eqshare'}
    if CREDIT_LINE_RE.search(str(lien.get('doc') or '')) or lien.get('heloc'):
        return {'est': int(round(amt)), 'rate': None, 'paid': None, 'how': 'credit_line'}
    face = {'est': int(round(amt)), 'rate': None, 'paid': None, 'how': 'face'}
    rec = _parse(lien.get('d'))
    if not rec:
        return face
    end = _parse(as_of) or date.today()
    st = _parse(stop)
    if st and st < end:
        end = st
    rate = rate_for(rec, tab)
    if rate is None:
        return face
    paid = months_between(rec, end)
    return {'est': int(round(amortized(amt, rate, TERM_MONTHS, paid))),
            'rate': round(rate, 2), 'paid': paid, 'how': 'amort'}


def stamp_lead(d, as_of=None, tab=None):
    """Bake estimated balances onto a board row that carries `orliens`.

    Per OPEN priced row: `est` (dollars), `estr` (rate used), `esth` (how). On the row:
      orest      sum of estimated balances of the open priced mortgages
      orestface  the recorded amounts of those same mortgages
      orestn     how many open mortgages were estimated
    A lis pendens row (no judgment yet) whose chain is fully priced also gets
      debtEst    = orest, the board's estimated debt when no judgment or payoff exists.
    On a lis pendens row every open loan stops amortizing at the filing date: which one is being
    foreclosed is not known there, and stopping all of them can only overstate the debt.
    Returns the number of rows estimated."""
    liens = d.get('orliens') or []
    if not isinstance(liens, list):
        return 0
    stop = d.get('filedDate') or d.get('filed') if str(d.get('st') or '') == 'LP' else None
    tot = face = n = 0
    for l in liens:
        if not isinstance(l, dict) or str(l.get('st') or 'OPEN').upper() != 'OPEN':
            continue
        e = estimate(l, as_of=as_of, stop=stop, tab=tab)
        if not e:
            continue
        l['est'] = e['est']
        l['esth'] = e['how']
        if e['rate'] is not None:
            l['estr'] = e['rate']
        tot += e['est']
        face += int(round(float(l.get('amt') or 0)))
        n += 1
    if n:
        d['orest'] = tot
        d['orestface'] = face
        d['orestn'] = n
        # not on an association (mr) row: there the mortgages survive the sale and are not the
        # debt being foreclosed, so they are no stand-in for the missing judgment
        if (str(d.get('st') or '') == 'LP' and str(d.get('eqstate') or '') == 'priced'
                and not d.get('mr') and 'HOA' not in str(d.get('lpkind') or '').upper()
                and not (d.get('judg') or d.get('payoff'))):
            d['debtEst'] = tot
            # lp_leads leaves `eq` None because no debt was known. Now an estimate is: Call Mode's
            # sort and -25 floor read `eq`, and eqfake (still True on every LP row) keeps it marked
            # approximate everywhere it renders.
            try:
                v = float(d.get('value') or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0 and d.get('eq') is None:
                d['eq'] = round((v - tot) / v * 100.0, 1)
                d['eqest'] = True
    return n


def refresh(timeout=30):
    """Download Freddie Mac's PMMS history and write monthly means to DEALFLOW_DIR. -> months."""
    import requests
    r = requests.get(PMMS_CSV_URL, timeout=timeout,
                     headers={'User-Agent': 'Mozilla/5.0 (DealFlow loan_balance)'})
    r.raise_for_status()
    sums = {}
    for line in r.text.splitlines()[1:]:
        parts = line.split(',')
        if len(parts) < 2:
            continue
        d = _parse(parts[0])
        try:
            r30 = float(parts[1])
        except ValueError:
            continue
        try:
            r15 = float(parts[3]) if len(parts) > 3 and parts[3].strip() else None
        except ValueError:
            r15 = None
        if not d or not (1.0 < r30 < 20.0):
            continue
        s = sums.setdefault(_ym(d), [0.0, 0, 0.0, 0])
        s[0] += r30; s[1] += 1
        if r15:
            s[2] += r15; s[3] += 1
    rates = {k: [round(s[0] / s[1], 2), (round(s[2] / s[3], 2) if s[3] else None)]
             for k, s in sorted(sums.items())}
    if len(rates) < 100:
        raise RuntimeError('PMMS history came back with only %d months' % len(rates))
    dst = os.path.join(_dealflow_dir(), 'pmms_monthly.json')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'source': PMMS_CSV_URL, 'fetched': date.today().isoformat(), 'rates': rates}, f)
    os.replace(tmp, dst)
    table(refresh=True)
    return len(rates)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--refresh', action='store_true', help='re-read Freddie Mac PMMS history')
    ap.add_argument('--amt', type=float, help='recorded amount, for a one-off estimate')
    ap.add_argument('--date', help='recording date YYYY-MM-DD or M/D/YYYY')
    ap.add_argument('--asof', help='as-of date (default today)')
    a = ap.parse_args()
    if a.refresh:
        print('PMMS months written:', refresh())
    if a.amt and a.date:
        print(estimate({'amt': a.amt, 'd': a.date}, as_of=a.asof))
    t = table()
    print('rate table: %d months, %s .. %s' % (len(t), min(t) if t else '-', max(t) if t else '-'))
