#!/usr/bin/env python
"""auction_forecast.py -- "Deals on the Clock": the actionable near-term auction pipeline.

Turns the board into a dated, ranked action list — every REACHABLE, person-owned lead with a real
apparent equity cushion and an auction inside the window — so the operator opens the day knowing
which deals disappear when, and which few to work THIS week.

Promoted from a one-off overnight analysis (2026-08-02) into a standing tool: run it any day for a
fresh forecast instead of re-deriving it by hand. Reads the BAKED board (the Desktop plaintext copy
make_tracker writes) — NOT the raw lead files — so the forecast reflects exactly what the operator
sees, including the resolved county, the Redfin Estimate (rfval) that flags hidden equity, and the
same value the board shows. Brace-counts the `const RAW` blob (same technique _wp_bake_counts.py
uses; a naive regex truncates the 5MB ciphertext). Computes equity HONESTLY as value minus judgment
(the raw `eq` field mixes units), and days-to-auction from the auction DATE vs today (robust to a
stale build). Never touches the tracker or the deal math.

HONEST CAVEATS baked into the output:
  * equity here = value - judgment ONLY. Surviving 1st/2nd mortgages + HOA/assessments are NOT
    netted (that math is per-lead, client-side). So these are CEILINGS — verify the debt stack
    before quoting anyone. Condos especially.
  * reachable = a phone or email is on file. Excludes vacant / company-owned / BK-stayed.

RUN
    python auction_forecast.py                 # 21-day window -> Desktop\DEALFLOW\DEALS-ON-THE-CLOCK.md
    python auction_forecast.py --days 14
    python auction_forecast.py --min-equity 30000
    python auction_forecast.py --print          # also echo the top table to stdout
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html')
OUT = os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/DEALS-ON-THE-CLOCK.md')
_COMPANY_RE = re.compile(
    r'\b(LLC|INC|CORP|TRUST|BANK|COMPANY|HOLDINGS|LP|LTD|ASSOC|ASSN|PROPERT|REALTY|CAPITAL|GROUP|'
    r'INVEST|CHURCH|CONDOMINIUM)\b', re.I)


def load_board(path=BOARD):
    """Parse the `const RAW = [...]` lead array out of the baked plaintext board. Brace-counted, not
    regex — the 5MB blob contains `];` sequences earlier than the true end."""
    t = open(path, encoding='utf-8', errors='replace').read()
    start = t.find('[', t.find('const RAW ='))
    depth = 0; i = start; ins = False; esc = False; end = -1
    while i < len(t):
        c = t[i]
        if ins:
            if esc: esc = False
            elif c == '\\': esc = True
            elif c == '"': ins = False
        else:
            if c == '"': ins = True
            elif c == '[': depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0: end = i + 1; break
        i += 1
    return json.loads(t[start:end])


def _owner(r):
    raw = str(r.get('oname') or (r.get('owners') or '').split(';')[0]).strip()
    if ',' in raw and ' ' not in raw.split(',')[0]:  # "LAST, First" -> "First Last"
        last, first = raw.split(',', 1)
        raw = '{} {}'.format(first.strip(), last.strip())
    return raw.title() if raw else '(owner)'


def _addr(r):
    return str(r.get('addr') or '').strip()


def _sale(r):
    return str(r.get('auction') or '').strip()


def _days_to_auction(r, today):
    """Days from today to the auction DATE (robust to a stale build's cached `days`)."""
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', _sale(r))
    if not m:
        d = r.get('days')
        return int(d) if isinstance(d, (int, float)) and d < 9000 else None
    try:
        auc = dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        return (auc - today).days
    except Exception:
        return None


def _has_phone(r):
    return any(p for p in (r.get('phones') or []))


def _has_email(r):
    return any(e and '@' in str(e) for e in (r.get('emails') or []))


def _is_company(r):
    return bool(_COMPANY_RE.search(str(r.get('owners') or r.get('oname') or '')))


def _value(r):
    try:
        return float(r.get('value') or 0) or 0.0
    except Exception:
        return 0.0


def _judg(r):
    try:
        return float(r.get('judg') or 0) or 0.0
    except Exception:
        return 0.0


def _equity_ceiling(r):
    """value - judgment. Deliberately NOT the raw `equity` field (mixed units). A ceiling: surviving
    seniors + HOA are not subtracted here."""
    v = _value(r)
    if not v:
        return None
    return v - _judg(r)


def _county(r):
    return str(r.get('county') or 'MIAMI-DADE')


def _rf(r):
    try:
        return int(r.get('rfval') or 0)
    except Exception:
        return 0


def _money(n):
    return '${:,}'.format(int(round(n)))


def collect(leads, max_days, min_equity, today):
    out = []
    for r in leads:
        d = _days_to_auction(r, today)
        if d is None or d < 0 or d > max_days:
            continue
        if r.get('vac') or r.get('sibclaimed') or r.get('saleBkAct') or r.get('lpDismissed'):
            continue
        if _is_company(r):
            continue
        if not (_has_phone(r) or _has_email(r)):
            continue
        eq = _equity_ceiling(r)
        if eq is None or eq < min_equity:
            continue
        out.append((r, eq, d))
    # rank: urgency-weighted equity (closer auction scores higher for equal equity)
    out.sort(key=lambda t: t[1] * max(1, (max_days + 1) - t[2]), reverse=True)
    return out


def build_md(cand, max_days, min_equity, today):
    md = _county  # alias not needed; keep local
    md_leads = [t for t in cand if _county(t[0]) == 'MIAMI-DADE']
    lines = []
    A = lines.append
    A('# ⏰ Deals on the Clock — auction forecast (auto-generated {}, from the live board)'
      .format(today.isoformat()))
    A('')
    A('**What this is:** every reachable, person-owned lead with an auction in the next {} days and '
      '≥{} apparent owner-side equity, ranked by urgency × equity. Regenerate any day with '
      '`python auction_forecast.py`.'.format(max_days, _money(min_equity)))
    A('')
    A('## ⚠️ Read before acting on any number')
    A('- **Equity here = value − judgment only.** Surviving 1st/2nd mortgages and HOA/assessments '
      'are NOT netted in. These are CEILINGS — verify the full debt stack on the Call Sheet before '
      'quoting anyone. Condos especially (40-yr recert / special-assessment risk).')
    A('- Reachable = a phone or email is on file. Excludes vacant, company-owned, bankruptcy-stayed, '
      'sibling-claimed.')
    A('')
    # the calendar / cliffs
    byday = {}
    for t in cand:
        byday.setdefault(t[2], []).append(t)
    A('## The shape of the next {} days'.format(max_days))
    A('- **{} reachable ≥{}-equity auctions** in {} days across the 3 counties; **{} are Miami-Dade** '
      '(the only ones Carlos can door-knock).'.format(len(cand), _money(min_equity), max_days, len(md_leads)))
    # find the cliffs: days with the most combined equity
    ranked_days = sorted(byday.items(), key=lambda kv: sum(x[1] for x in kv[1]), reverse=True)[:2]
    if ranked_days:
        A('- **Auction cliffs (most equity landing on one day) — mark them:**')
        for d, ts in sorted(ranked_days, key=lambda kv: kv[0]):
            dtd = today + dt.timedelta(days=d)
            A('  - \U0001f534 **{} (+{}d): {} leads, ~{} combined est. equity.**'
              .format(dtd.strftime('%a %m/%d'), d, len(ts), _money(sum(x[1] for x in ts))))
    A('')
    # day-by-day
    A('### Day by day')
    for d in sorted(byday):
        dtd = today + dt.timedelta(days=d)
        ts = byday[d]
        A('- {} (+{}d): {} leads, ~{} equity'.format(dtd.strftime('%a %m/%d'), d, len(ts),
                                                      _money(sum(x[1] for x in ts))))
    A('')
    # Miami-Dade actionable table
    A('## Miami-Dade — call/door this week (Carlos’s lane + your warm calls)')
    A('| Auction | Owner | Address | Apparent eq (ceiling) | Flags |')
    A('|---|---|---|---|---|')
    for r, eq, d in [t for t in md_leads if t[2] <= 10][:12]:
        dtd = today + dt.timedelta(days=d)
        flags = []
        rf = _rf(r)
        if rf and _value(r) and rf > _value(r) * 1.15:
            flags.append('\U0001f4a1 Redfin {} > county {} — hidden equity, pull comps'
                         .format(_money(rf), _money(_value(r))))
        elif rf:
            flags.append('Redfin {}'.format(_money(rf)))
        if r.get('hs'):
            flags.append('\U0001f3e0 HS')
        if r.get('condo'):
            flags.append('CONDO')
        A('| {} | {} | {} | ~{} | {} |'.format(
            dtd.strftime('%m/%d'), _owner(r)[:24], (_addr(r) or '')[:34], _money(eq),
            ' · '.join(flags)))
    A('')
    # HIDDEN EQUITY — the Redfin cross-check's payoff: leads where Redfin's AVM is materially ABOVE
    # the county value the board uses. BUT a high Redfin/county ratio alone is a trap — Marcus Toney
    # is 1.48x ($1.05M county / $1.55M Redfin) yet has a $1.3M judgment, so real equity is NEGATIVE.
    # Rank by REAL best-case equity = max(county, Redfin) − judgment, and only surface leads where
    # that clears a meaningful floor. This is the number that actually decides whether to knock.
    def _hi_eq(r):
        return max(_value(r), _rf(r)) - _judg(r)
    hidden = sorted(
        [(r, eq, d) for r, eq, d in cand
         if _value(r) and _rf(r) > _value(r) * 1.15 and _hi_eq(r) >= 50000],
        key=lambda t: _hi_eq(t[0]), reverse=True)
    if hidden:
        A('## 💡 Hidden equity — Redfin values these ABOVE the county roll AND real equity survives the judgment')
        A('The board equity uses the *county* value; Redfin says these are worth 15%+ more. The last '
          'column is best-case equity — **max(county, Redfin) − judgment** — so leads whose judgment '
          'eats the spread (e.g. Toney: 1.48× but a $1.3M judgment) are correctly excluded. Pull comps '
          'to confirm Redfin, but this is the honest short-list of where the biggest upside hides.')
        A('| Auction | Owner | Address | County → Redfin | Judgment | Best-case eq |')
        A('|---|---|---|---|---|---|')
        for r, eq, d in hidden[:10]:
            dtd = today + dt.timedelta(days=d)
            ratio = _rf(r) / _value(r)
            hs = ' 🏠' if r.get('hs') else ''
            A('| {} | {} | {} | {} → **{}** ({:.2f}×) | {} | **~{}** |'.format(
                dtd.strftime('%m/%d'), _owner(r)[:22] + hs, (_addr(r) or '')[:26],
                _money(_value(r)), _money(_rf(r)), ratio, _money(_judg(r)), _money(_hi_eq(r))))
        A('')
    # CLEANEST EQUITY — the single best quality signal: a tiny judgment relative to value means the
    # owner's equity is almost entirely UNENCUMBERED, so little debt can eat it and the seller has the
    # most to save. A better primary filter than the Redfin cross-check (it caught Holly Morgan —
    # $1.09M home, $24k judgment — whom Redfin never flagged because the county value was already
    # right). Judgment < 25% of value, sorted by equity. Spans all counties (the biggest ones are
    # often Broward/PB — huge equity, phone/mail plays).
    clean = sorted(
        [(r, eq, d) for r, eq, d in cand
         if _value(r) >= 200000 and _judg(r) > 0 and (_judg(r) / _value(r)) <= 0.25],
        key=lambda t: t[1], reverse=True)
    if clean:
        A('## 🟢 Cleanest equity — tiny judgment vs value (the safest deals; verify no surviving senior)')
        A('Judgment is <25% of value here, so the equity is almost all unencumbered — the safest bets '
          'and the sellers with the most to save. The one risk left is a *surviving* 1st/2nd mortgage '
          'the judgment amount doesn\'t reveal; confirm the debt stack on the Call Sheet. 🏠 = homestead '
          '(owner-occupied → rescue framing). Several of the biggest are Broward/Palm Beach — too far '
          'to door, but a $600k–$1M equity phone/mail play is worth the reach.')
        A('| Auction | County | Owner | Address | Judgment (% of value) | Equity |')
        A('|---|---|---|---|---|---|')
        for r, eq, d in clean[:12]:
            dtd = today + dt.timedelta(days=d)
            hs = ' 🏠' if r.get('hs') else ''
            pct = 100.0 * _judg(r) / _value(r)
            A('| {} | {} | {} | {} | {} ({:.0f}%) | **~{}** |'.format(
                dtd.strftime('%m/%d'), _county(r)[:2], _owner(r)[:20] + hs, (_addr(r) or '')[:26],
                _money(_judg(r)), pct, _money(eq)))
        A('')
    # Broward / Palm Beach
    oth = [t for t in cand if _county(t[0]) != 'MIAMI-DADE' and t[2] <= 10]
    if oth:
        A('## Broward / Palm Beach — phone/email only (too far to door)')
        A('| Auction | County | Owner | Address | Apparent eq (ceiling) |')
        A('|---|---|---|---|---|')
        for r, eq, d in oth[:10]:
            dtd = today + dt.timedelta(days=d)
            A('| {} | {} | {} | {} | ~{} |'.format(dtd.strftime('%m/%d'), _county(r)[:2],
              _owner(r)[:22], (_addr(r) or '')[:30], _money(eq)))
        A('')
    A('## The honest read')
    A('The pipeline is not short of opportunity — {} reachable deals with real apparent equity in '
      '{} days. The constraint is conversations, not leads. Work the tightest near-term Miami-Dade '
      'cluster, lead with any Redfin-flagged hidden-equity leads, and let the door route + the daily '
      'golden window do the contacting.'.format(len(cand), max_days))
    A('')
    A('*Auto-generated by auction_forecast.py. Equity ceilings only — verify debt before quoting.*')
    return '\n'.join(lines) + '\n', md_leads


def main():
    ap = argparse.ArgumentParser(description='Deals on the Clock — near-term auction forecast.')
    ap.add_argument('--days', type=int, default=21, help='auction window in days (default 21)')
    ap.add_argument('--min-equity', type=int, default=20000, help='minimum apparent equity (default 20000)')
    ap.add_argument('--print', action='store_true', dest='echo', help='also echo the MD table to stdout')
    a = ap.parse_args()

    if not os.path.exists(BOARD):
        print('auction_forecast: board not found at {} — run a build first.'.format(BOARD))
        return 1
    leads = load_board()
    today = dt.date.today()  # days-to-auction is computed from the auction DATE, so this anchors it
    cand = collect(leads, a.days, a.min_equity, today)
    md, md_leads = build_md(cand, a.days, a.min_equity, today)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(md)
    print('auction_forecast: {} reachable deals (<= {}d, >= {} eq); {} Miami-Dade. Wrote {}'
          .format(len(cand), a.days, a.min_equity, len(md_leads), OUT))
    if a.echo:
        for r, eq, d in [t for t in md_leads if t[2] <= 10][:12]:
            print('  +{}d {:24} {:34} ~{}'.format(d, _owner(r)[:24], (_addr(r) or '')[:34],
                                                  _money(eq)))


if __name__ == '__main__':
    main()
