#!/usr/bin/env python
"""three_day.py — Jesse's list: sales within 3 business days, equity at face value, recent cases.

WHY (2026-09-16, Jesse): "call people three days before the sale, not ten. Three days out with no
attorney and no sale stopped means nobody is helping them." Equity at FACE value — the judgment as
posted against the county value — no deep research first. Case numbers 2024 or later only: a 2018 case
has been playing the game for years. Get the owner live, conference Jesse in, give him the case number.

ONE PREDICATE, THREE CONSUMERS. `is_three_day()` here; skiptrace.py `--cases-file` spends its budget on
these before anyone else; call_mode.py's `3-DAY` lane is the JS twin (keep them in step); this CLI writes
the morning list Jesse reads. The 2026-09-16 measurement that made this a step: 18 Tuesday leads on the
board, 3 with a phone — the nightly trace budget went to Tier A, not to "sale in 3 days".

Run:  python three_day.py                       # write three_day_lane.json + DEALFLOW\3DAY-<date>.md
      python three_day.py --trace --max-spend 3 # ...and skip-trace lane members with no phone first
      python three_day.py --days 5 --min-year 2023 --print
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'three_day_lane.json')
DAYS = 3
MIN_YEAR = 2024


def _is_hoa(plaintiff, ctype):
    """HOA/condo plaintiff? Bank-charter guard first ('U S BANK TRUST NATIONAL ASSOCIATION' is a bank) —
    the same classifier broward_liens uses for foreclosure type."""
    from broward_liens import _fc_type_plaintiff
    t = _fc_type_plaintiff(plaintiff)
    if t:
        return t == 'HOA'
    return str(ctype or '').upper().startswith('HOA')


def sale_date(r):
    m = re.match(r'\s*(\d{1,2})/(\d{1,2})/(\d{4})', str(r.get('auction') or ''))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def business_days_until(d, today):
    """Weekdays strictly between today and d, plus d itself when it is a weekday. Today -> 0.
    A Wednesday call for a Monday sale is 3 (Thu, Fri, Mon) — the weekend does not count."""
    if d < today:
        return -1
    n, cur = 0, today
    while cur < d:
        cur += datetime.timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def case_year(case):
    """2026 from '2026-001234-CA-01' (Miami-Dade), 'CACE-26-013184' (Broward), '502026CA001234XXXAMB' (PB)."""
    c = str(case or '').upper()
    m = re.match(r'(\d{4})-\d', c)
    if m:
        return int(m.group(1))
    m = re.match(r'[A-Z]{4}-(\d{2})-', c)
    if m:
        return 2000 + int(m.group(1))
    m = re.match(r'50(\d{4})[A-Z]{2}', c)
    if m:
        return int(m.group(1))
    return 0


def face_equity(r):
    """Value minus the debt as POSTED (payoff if accrued, else the judgment). None = not computable
    (no value or no posted debt) — not zero, and not in the lane."""
    try:
        v = float(r.get('value') or 0)
        owed = float(r.get('payoff') or 0) or float(r.get('judg') or 0)
    except (TypeError, ValueError):
        return None
    if v <= 0 or owed <= 0:
        return None
    return v - owed


def is_three_day(r, today=None, days=DAYS, min_year=MIN_YEAR):
    """The lane: an auction-dated (not LP / balloon) lead selling within `days` business days, with face
    equity > 0 and a case filed `min_year` or later. Dead / opt-out / BK-stay / cert-of-title / sold-sibling
    are the caller's job (they live in notes and ledgers this module does not read)."""
    if (r.get('st') or '') == 'LP' or r.get('bal'):
        return False
    d = sale_date(r)
    if d is None:
        return False
    today = today or datetime.date.today()
    bd = business_days_until(d, today)
    if bd < 0 or bd > days:
        return False
    eq = face_equity(r)
    if eq is None or eq <= 0:
        return False
    return case_year(r.get('case')) >= min_year


def _load(fn, default):
    try:
        with open(os.path.join(HERE, fn), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _board_rows():
    """The board's own merged rows (same source sheets_crm reads), else the slim county files."""
    import sheets_crm as S
    return S._leads()


def lane(rows=None, today=None, days=DAYS, min_year=MIN_YEAR):
    import sheets_crm as S
    rows = rows if rows is not None else _board_rows()
    notes = (_load('worker_notes.json', {}).get('notes') or {})
    st = _load('skiptrace_results.json', {})
    closed = S._closed_cases()
    dk = _load('dockets.json', {})
    out = []
    for r in rows:
        case = r.get('case') or ''
        if not case or not is_three_day(r, today, days, min_year):
            continue
        n = notes.get(case) or {}
        if case in closed or n.get('optout') or n.get('wrongown'):
            continue
        if str(n.get('status') or '').strip().upper() in ('DEAD', 'DO NOT CONTACT'):
            continue
        if r.get('saleBkAct') or r.get('sale_bk_active') or r.get('cert') or r.get('sibclaimed'):
            continue
        d = sale_date(r)
        k = dk.get(case) or r.get('dk') or {}
        datt = sorted({(p.get('a') or '').strip() for p in (k.get('parties') or [])
                       if str(p.get('t') or '').upper().startswith('DEFEND') and (p.get('a') or '').strip()})
        pl = r.get('plaintiff') or ''
        out.append({
            'case': case, 'county': r.get('county') or '', 'auction': r.get('auction'),
            'bdays': business_days_until(d, today or datetime.date.today()),
            'addr': r.get('addr') or '', 'owner': (r.get('owners') or '').split(';')[0].strip()[:40],
            'value': round(float(r.get('value') or 0)), 'owed': round(float(r.get('payoff') or 0) or float(r.get('judg') or 0)),
            'eq': round(face_equity(r) or 0), 'hs': bool(r.get('hs')),
            'plaintiff': pl[:40], 'hoa': _is_hoa(pl, r.get('ctype')),
            'chain': r.get('orconf') or '', 'phones': S._phones(case, st, n)[:4],
            'defatt': datt, 'status': n.get('status') or '', 'touches': len(n.get('touches') or []),
        })
    out.sort(key=lambda x: (x['bdays'], -x['eq']))
    return out


def write_md(rows, today, days=DAYS, min_year=MIN_YEAR):
    os.makedirs(P.DEALFLOW_DIR, exist_ok=True)
    path = os.path.join(P.DEALFLOW_DIR, '3DAY-%s.md' % today.isoformat())
    L = ['# 3-DAY list — %s — sales within %d business days, face equity, cases %d+' % (today.strftime('%a %m/%d/%Y'), days, min_year), '',
         '"Equity" is the county value minus the debt AS POSTED. HOA/condo plaintiffs (marked HOA) usually have a first mortgage the chain has not traced — ask on the call. '
         'Script: "You have a sale <day>. Good chance we can stop it — keep it or sell it?" Let them talk, then conference Jesse LIVE with the case number.', '',
         '| # | Sale | Case | Address | Owner | Value | Owed | Face eq | HS | Plaintiff | Def. attorney | Phones | Status |',
         '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for i, x in enumerate(rows, 1):
        L.append('| %d | %s | %s | %s | %s | $%s | $%s | $%s | %s | %s%s | %s | %s | %s |' % (
            i, x['auction'], x['case'], x['addr'], x['owner'], format(x['value'], ','), format(x['owed'], ','), format(x['eq'], ','),
            'Y' if x['hs'] else '', x['plaintiff'], ' **HOA**' if x['hoa'] else '', ', '.join(x['defatt']) or '— (none)',
            ' / '.join('(%s) %s-%s' % (p[:3], p[3:6], p[6:]) for p in x['phones']) or '— no phone', x['status']))
    if not rows:
        L.append('(no leads qualify today)')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    return path


def trace_missing(rows, max_spend):
    """Skip-trace lane members with no phone, ahead of everyone else, inside --max-spend."""
    need = [x['case'] for x in rows if not x['phones']]
    if not need:
        print('3-day: every lane lead already has a phone')
        return 0
    cf = os.path.join(HERE, '_three_day_cases.json')
    with open(cf, 'w', encoding='utf-8') as f:
        json.dump(need, f)
    print('3-day: tracing %d lane lead(s) with no phone (cap $%.2f)' % (len(need), max_spend))
    r = subprocess.run([sys.executable, '-u', os.path.join(HERE, 'skiptrace.py'), '--cases-file', cf,
                        '--max-spend', str(max_spend)], cwd=HERE)
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=DAYS)
    ap.add_argument('--min-year', type=int, default=MIN_YEAR)
    ap.add_argument('--trace', action='store_true', help='skip-trace lane members with no phone first')
    ap.add_argument('--max-spend', type=float, default=3.0)
    ap.add_argument('--print', action='store_true')
    a = ap.parse_args()
    today = datetime.date.today()
    rows = lane(None, today, a.days, a.min_year)
    rc = 0
    if a.trace and rows:
        rc = trace_missing(rows, a.max_spend)
        rows = lane(None, today, a.days, a.min_year)          # re-read: phones just landed
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'built': today.isoformat(), 'days': a.days, 'min_year': a.min_year, 'cases': [x['case'] for x in rows],
                   'rows': rows}, f, indent=1)
    path = write_md(rows, today, a.days, a.min_year)
    n_ph = sum(1 for x in rows if x['phones'])
    print('3-day: %d lead(s), %d with a phone, %d HOA-plaintiff -> %s' % (len(rows), n_ph, sum(1 for x in rows if x['hoa']), path))
    if a.print:
        for x in rows:
            print('  %s %-18s %-32s eq $%-8s %s %s' % (x['auction'], x['case'], x['addr'][:32], format(x['eq'], ','),
                                                     'HOA' if x['hoa'] else '   ', '/'.join(x['phones']) or 'no phone'))
    return 0 if rc in (0, 5) else rc


if __name__ == '__main__':
    raise SystemExit(main())
