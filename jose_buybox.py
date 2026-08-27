"""Jose buy-box gate — filter the DealFlow board to deals that PROFIT ON THE BUY.

Implements the deal math from the Jose Masterclass playbook (vault:
5-projects/foreclosure-leads/Jose Masterclass — Foreclosure Investment Playbook.md, §1 + §12):

    Entry:   acquisition at <= 70% of market value ("I rarely ever bought a property where
             I didn't make money when I bought it") — <= 60% is the strong zone.
    Profit:  DSCR loan (85% of value, Jose's own calculator) minus payoff minus ~3% closing
             = net profit AT PURCHASE, before any rehab or appreciation.
    Rehab:   unknown per lead — the playbook's total-basis rule (purchase + rehab <= 75% of
             ARV) is printed as a reminder, never silently assumed satisfied.

WHAT STANDS IN FOR "ACQUISITION PRICE" ON A FORECLOSURE LEAD
The recorded FINAL JUDGMENT. That is a PAYOFF FLOOR, not the payoff — per-diem interest,
fees and advances accrue on top (Property Sheet Standard: principal != payoff). So the
entry % shown here is the OPTIMISTIC floor; a lead that fails at the floor fails harder in
real life, and a lead that passes still needs the real payoff pulled before an offer.

TRUTH RAILS — a lead enters the buy-box ONLY when its numbers are trusted:
    * value: the warn/warning field must be empty (VALUE UNVERIFIED classes stay out —
      the assv-leak lesson: a number nobody looked up is not a number)
    * debt: judgment_unknown / ju / judg<=0 leads are out (unknown debt has no entry %);
      LP-lane rows are pre-judgment by definition -> out
    * junior-lien fantasy: mr / eqfake / mortgage_risk leads are out — the shown judgment
      is an HOA/junior figure, the senior mortgage is hidden, the real basis is unknown
    * vacant land is out (the model buys homes, not dirt)
    * past auctions are out (nothing left to buy short of the sale)
This gate does deal MATH; it does not replace the board's door-verification (ownership
current, opt-outs, BK stay) — a PASS here still walks Jose's 08-16 gate before anyone acts.

Run:  python jose_buybox.py                      # rank the whole board
      python jose_buybox.py --county broward     # one county
      python jose_buybox.py --entry 60 --min-profit 30000 --ltv 85
Out:  console table + jose_buybox.json (gitignored — owner names on a public repo)
"""
import argparse
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'jose_buybox.json')


def _money(v):
    try:
        return round(float(v))
    except Exception:
        return 0


def _norm_md(r):
    """leads_final.json (Miami-Dade shape) -> the gate's common row."""
    return {
        'county': 'MIAMI-DADE', 'case': r.get('Case #', ''), 'addr': r.get('Address', ''),
        'owners': r.get('owners', ''), 'value': _money(r.get('market_value')),
        'judg': _money(r.get('judgment')), 'ju': bool(r.get('judgment_unknown')),
        'fake': bool(r.get('eq_fake') or r.get('mortgage_risk')),
        'warn': (r.get('warning') or '').strip(),
        'vac': 'VACANT' in str(r.get('dor_desc', '')).upper(),
        'hs': bool(r.get('homestead')), 'st': r.get('sale_type', 'FC'),
        'days': r.get('days_to_auction', -1), 'auction': r.get('AuctionDate', ''),
        'zest': _money(r.get('zest')), 'tier': r.get('tier', ''),
    }


def _norm_county(r):
    """<county>_leads.json / lp_leads.json (slim shape) -> the gate's common row."""
    return {
        'county': r.get('county', '?'), 'case': r.get('case', ''), 'addr': r.get('addr', ''),
        'owners': r.get('owners', ''), 'value': _money(r.get('value')),
        'judg': _money(r.get('judg')), 'ju': bool(r.get('ju')),
        'fake': bool(r.get('eqfake') or r.get('mr')),
        'warn': (r.get('warn') or '').strip(),
        'vac': bool(r.get('vac')), 'hs': bool(r.get('hs')), 'st': r.get('st', 'FC'),
        'days': r.get('days', -1), 'auction': r.get('auction', ''),
        'zest': _money(r.get('zest')), 'tier': r.get('tier', ''),
    }


def load_rows(county_filter=''):
    rows = []
    md = os.path.join(HERE, 'leads_final.json')
    if os.path.exists(md):
        try:
            rows += [_norm_md(r) for r in json.load(open(md, encoding='utf-8'))]
        except Exception as e:
            print('WARN: leads_final.json unreadable (%s) — Miami-Dade skipped' % e)
    # same merge rule as make_tracker: every *_leads.json, skipping _-prefixed scratch
    for f in sorted(glob.glob(os.path.join(HERE, '*_leads.json'))):
        if os.path.basename(f).startswith('_'):
            continue
        try:
            rows += [_norm_county(r) for r in json.load(open(f, encoding='utf-8'))
                     if isinstance(r, dict)]
        except Exception as e:
            print('WARN: %s unreadable (%s) — skipped' % (os.path.basename(f), e))
    if county_filter:
        cf = county_filter.upper().replace('-', ' ').replace('_', ' ').strip()
        rows = [r for r in rows if cf in r['county'].upper().replace('-', ' ')]
    # dedupe on case+auction (a case can appear in MD and an archive twin)
    seen, out = set(), []
    for r in rows:
        k = (r['case'], r['auction'])
        if r['case'] and k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def gate(rows, entry_max=70.0, ltv=85.0, closing_pct=3.0, min_profit=0):
    """(passes, fails_entry, excluded_by_reason) — the buy-box, floor-honest."""
    passes, fail_entry = [], 0
    excl = {'value untrusted': 0, 'debt unknown (incl. LP lane)': 0,
            'junior-lien fantasy (senior debt hidden)': 0, 'vacant land': 0,
            'auction already passed': 0}
    for r in rows:
        if r['days'] < 0:
            excl['auction already passed'] += 1
        elif not r['value'] or r['warn']:
            excl['value untrusted'] += 1
        elif r['fake']:
            excl['junior-lien fantasy (senior debt hidden)'] += 1
        elif r['ju'] or r['judg'] <= 0 or r['st'] == 'LP':
            excl['debt unknown (incl. LP lane)'] += 1
        elif r['vac']:
            excl['vacant land'] += 1
        else:
            # THE BOX (playbook §12, Jose's own calculator): buy at the 70% target, finance at
            # the DSCR ltv, pay ~3% closing on the purchase. Profit is a % OF VALUE — on the
            # $500k example this computes his exact $64,500. The JUDGMENT is only the FLOOR
            # under the negotiation: 'room' = what the 70% entry leaves to clear the debt AND
            # pay the seller. Room <= 0 means the debt alone busts the box — that IS the gate.
            # (Never price profit at payoff-only: on a tax deed the certs are pennies and the
            # auction is competitive — 'loan minus certs' on a $3M homestead is fantasy math.)
            floor_pct = r['judg'] / r['value'] * 100
            target = r['value'] * entry_max / 100.0
            loan = r['value'] * ltv / 100.0
            closing = target * closing_pct / 100.0
            profit = round(loan - target - closing)
            room = round(target - r['judg'])
            if room <= 0 or profit < min_profit:
                fail_entry += 1
                continue
            r = dict(r)
            r['entry_floor_pct'] = round(floor_pct, 1)
            r['target_purchase'] = round(target)
            r['dscr_loan'] = round(loan)
            r['closing'] = round(closing)
            r['profit_at_target'] = profit
            r['seller_room'] = room
            r['zone'] = 'STRONG' if floor_pct <= 60 else 'PASS'
            passes.append(r)
    passes.sort(key=lambda x: -x['seller_room'])
    return passes, fail_entry, excl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--county', default='')
    ap.add_argument('--entry', type=float, default=70.0, help='max entry %% of value (playbook: 70)')
    ap.add_argument('--ltv', type=float, default=85.0, help='DSCR loan %% of value (Jose\'s calculator: 85)')
    ap.add_argument('--closing', type=float, default=3.0, help='closing costs %% of purchase')
    ap.add_argument('--min-profit', type=int, default=0, help='net-profit-at-purchase floor in dollars')
    ap.add_argument('--top', type=int, default=25)
    a = ap.parse_args()

    rows = load_rows(a.county)
    passes, fail_entry, excl = gate(rows, a.entry, a.ltv, a.closing, a.min_profit)

    print('JOSE BUY-BOX — buy at %.0f%% of value, finance %.0f%% DSCR, profit ON THE BUY (playbook §1+§12)'
          % (a.entry, a.ltv))
    print('%d lead(s) scanned%s\n' % (len(rows), (' [%s]' % a.county) if a.county else ''))
    print('excluded before the math (untrusted numbers never enter the box):')
    for k, v in excl.items():
        print('   %5d  %s' % (v, k))
    print('   %5d  debt busts the %.0f%% entry / profit under $%s' % (fail_entry, a.entry, format(a.min_profit, ',')))
    print('\n%d lead(s) IN THE BOX — ranked by SELLER ROOM (the %.0f%% entry minus the judgment floor:'
          % (len(passes), a.entry))
    print('what clears the debt AND pays the seller while Jose still profits on the buy):\n')
    for r in passes[:a.top]:
        print('  %-6s %-2s %-11s %-24s  val $%-9s judg $%-9s room $%-9s profit@%.0f%% $%-8s %s%s'
              % (r['zone'], r['st'], r['county'][:11], (r['addr'] or '(no addr)')[:24],
                 format(r['value'], ','), format(r['judg'], ','), format(r['seller_room'], ','),
                 a.entry, format(r['profit_at_target'], ','), r['case'][:20],
                 '  [homestead]' if r['hs'] else ''))
    if len(passes) > a.top:
        print('  ... %d more in %s' % (len(passes) - a.top, os.path.basename(OUT)))
    print('\nreminders: the judgment is the payoff FLOOR (interest+fees accrue) - pull the real payoff')
    print('before any offer; TD rows are competitive-auction lane (room = pre-auction negotiation');
    print('window with the owner, not an auction price); total basis (purchase + rehab) must stay')
    print("<= 75% of ARV; a PASS here still walks Jose's door-verification gate on the board.")

    tmp = OUT + '.tmp'
    json.dump(passes, open(tmp, 'w', encoding='utf-8'), indent=1)
    os.replace(tmp, OUT)
    print('\n-> %s (%d rows)' % (os.path.basename(OUT), len(passes)))


if __name__ == '__main__':
    main()
