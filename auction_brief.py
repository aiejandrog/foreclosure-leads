#!/usr/bin/env python3
"""auction_brief.py — the sellable deliverable: a bid brief for ONE auction property.

WHY THIS EXISTS
Every distressed-listing site (Tranchi, Auction.com, PropStream, the county calendar itself)
shows an auction buyer the same three things: address, "estimated value", and photos. None of
them answer the only questions that decide whether the bid makes or loses money:

    1. What SURVIVES the sale and lands on me the morning after?
    2. What does the plaintiff actually have to be paid — TODAY, not on judgment day?
    3. Can I even get clean title, or is there an heir/life-estate/probate gate?

Bidders who guess at #1 buy a $10k HOA judgment and inherit a $400k first mortgage. Bidders who
guess at #2 underestimate the credit bid by six figures because a judgment accrues (FS 55.03).
This module renders the answers we already compute — board enrichment + diligence.py's title and
lien work + judgment_interest.py's accrual — into one page a buyer pays for.

EVERY NUMBER IS SOURCED OR ABSENT. No estimate is dressed as a fact; anything unverified is
printed as UNVERIFIED with the reason. That is the whole product: a bidder can act on it.

Usage:
  python auction_brief.py --case 2018-011148-CA-01
  python auction_brief.py --case X --pdf            # also render PDF (headless Edge)
  python auction_brief.py --auction 08/19/2026      # every property on one sale date
  python auction_brief.py --list                    # upcoming sales, briefable now
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop', 'DEALFLOW',
                     'Foreclosure Lead Tracker.html')
OUT_DIR = os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop', 'DEALFLOW', 'Auction-Briefs')
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'


def _load_board():
    """The built board already carries every enrichment (liens, taxes, comps, accrual), so the
    brief renders from ONE verified source instead of re-running six scrapers per order."""
    if not os.path.exists(BOARD):
        sys.exit(f'board not found: {BOARD}\nRun the rebuild first.')
    txt = open(BOARD, encoding='utf-8', errors='ignore').read()
    m = re.search(r'const RAW\s*=\s*(\[.*?\]);\s*\n', txt, re.S)
    if not m:
        sys.exit('could not parse the board payload (is this the PLAINTEXT desktop twin?)')
    return json.loads(m.group(1))


def _money(n, dash='—'):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return dash
    return dash if n == 0 else f'${n:,.0f}'


def _esc(s):
    return html.escape(str(s if s is not None else ''))


def _d(s):
    for f in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(str(s).strip(), f).date()
        except (ValueError, TypeError):
            continue
    return None


def survival_analysis(r, dil):
    """WHAT LANDS ON THE WINNING BIDDER. The single most valuable section.

    Florida rule set actually applied here:
      - Foreclosure wipes junior liens, NEVER seniors. On an HOA/COA case (FS 718.116/720.3085)
        the first mortgage is senior -> the buyer takes it subject to the whole mortgage.
      - Ad valorem taxes and tax certificates are first-priority and survive everything (FS 197.122).
      - Municipal/code-enforcement liens commonly survive; IRS liens carry a 120-day redemption.
      - On a first-mortgage case, juniors recorded AFTER the foreclosing mortgage are wiped.
    Anything we could not verify is returned as an UNKNOWN row, not silently omitted.
    """
    rows, unknown = [], []
    ftype = (dil.get('foreclosure_type') or r.get('ftype') or '').upper()
    is_hoa = ftype == 'HOA' or bool(r.get('mr'))
    is_td = (r.get('st') == 'TD')

    surv_senior = float(r.get('orsurvsen') or 0)
    if is_hoa:
        if surv_senior > 0:
            rows.append(('SURVIVES', 'First mortgage', surv_senior,
                         'Association foreclosure — the senior mortgage is NOT wiped (FS 718.116). '
                         'You take title subject to it.'))
        else:
            unknown.append(('First mortgage', 'Association case, but no verified open mortgage in the '
                                              'recorded chain. Could be free-and-clear — or an unindexed '
                                              'senior. PULL THE CHAIN BEFORE BIDDING.'))
    elif is_td:
        rows.append(('SURVIVES', 'Municipal / code liens', float(r.get('orcode') or 0),
                     'Tax-deed sale: governmental liens can survive (FS 197.552).'))
    else:
        rows.append(('WIPED', 'Junior liens recorded after the foreclosing mortgage',
                     float(r.get('orjuniors') or 0),
                     'First-mortgage foreclosure extinguishes juniors named and served.'))
        if surv_senior > 0:
            rows.append(('SURVIVES', 'Senior mortgage ahead of the plaintiff', surv_senior,
                         'Recorded BEFORE the foreclosing lien — survives the sale.'))

    tax = float(r.get('taxDue') or 0)
    if tax > 0:
        rows.append(('SURVIVES', 'Delinquent property taxes', tax,
                     'First priority, survives any foreclosure (FS 197.122). Verified on the county '
                     'tax collector.'))
    elif r.get('county') in ('MIAMI-DADE', 'BROWARD'):
        rows.append(('CLEAR', 'Delinquent property taxes', 0,
                     'Checked on the county tax collector — nothing delinquent found.'))
    else:
        unknown.append(('Delinquent property taxes',
                        'Palm Beach is not on the automated tax platform — pull the bill manually.'))

    code = float(r.get('orcode') or 0)
    if code > 0 and not is_td:
        rows.append(('SURVIVES', 'Code-enforcement / municipal liens', code,
                     'Recorded against the property; these routinely survive and keep accruing.'))
    irs = float(r.get('orirs') or 0)
    if irs > 0:
        rows.append(('SURVIVES*', 'IRS lien', irs,
                     'The United States holds a 120-day right of redemption after the sale (26 USC 7425).'))
    hoa_open = float(r.get('orhoa') or 0)
    if hoa_open > 0 and not is_hoa:
        rows.append(('PARTIAL', 'HOA / association dues', hoa_open,
                     'A purchaser is liable for unpaid assessments; the safe-harbor cap applies only '
                     'to a first-mortgagee. Get an estoppel before you bid.'))
    return rows, unknown


def _best_diligence(r, dcache):
    """FRESHEST title work wins, and it is usually NOT the one baked on the board.

    Three copies of a case's diligence can exist: baked into the board at build time, in
    diligence_cache.json, and in diligence/{case}.json which is what a human re-trace rewrites.
    Caught on 1212 NE 91 ST: a hand re-trace had replaced "Lady Bird / life estate unknown" with
    the real finding (sole vesting -> life estate + remaindermen, no probate opened), but the brief
    still printed the stale guess because the board copy was older. Shipping a stale title finding
    in a product sold as VERIFIED is the one defect that would end this business, so prefer the
    per-case file whenever it is newer than the cache."""
    case = r.get('case') or ''
    best = (r.get('diligence') or dcache.get(case) or {})
    fp = os.path.join(HERE, 'diligence', re.sub(r'[^A-Za-z0-9._-]', '_', case) + '.json')
    if os.path.exists(fp):
        try:
            live = json.load(open(fp, encoding='utf-8')) or {}
            if _d(live.get('traced')) and (not _d(best.get('traced'))
                                           or _d(live['traced']) >= _d(best['traced'])):
                return live
        except Exception:
            pass
    return best


def build_brief(r, dil):
    """Assemble the buyer-facing model. Returns a dict the renderer turns into HTML."""
    case = r.get('case') or ''
    value = float(r.get('value') or 0)
    arv = float(r.get('arv') or 0)
    arv_ok = arv > 0 and r.get('arvconf') == 'ok'
    exit_value = arv if arv_ok else value

    # THE PAYOFF, not the judgment (FS 55.03 — see judgment_interest.py).
    judg = float(r.get('judg') or 0)
    payoff = float(r.get('payoff') or 0) or judg
    accrued = bool(r.get('jaccrued'))

    surv_rows, unknown = survival_analysis(r, dil)
    survives_total = sum(a for st, _, a, _ in surv_rows if st.startswith('SURVIVES') and a)

    # Opening bid: the plaintiff normally credit-bids up to its judgment, so a third party must
    # clear the payoff (not the stale judgment) plus everything that survives.
    is_td = (r.get('st') == 'TD')
    opening = float(r.get('obid') or 0) if is_td else payoff
    all_in_at_opening = opening + survives_total

    # Disciplined ceiling: 70% of exit value less what survives and less resale costs — the same
    # posture the board's tax-deed model uses. This is GUIDANCE, and labeled as such.
    resale_cost = exit_value * 0.08
    max_bid = max(0.0, exit_value * 0.70 - survives_total - resale_cost)

    return {
        'case': case, 'r': r, 'dil': dil,
        'value': value, 'arv': arv, 'arv_ok': arv_ok, 'exit_value': exit_value,
        'judg': judg, 'payoff': payoff, 'accrued': accrued,
        'surv_rows': surv_rows, 'unknown': unknown, 'survives_total': survives_total,
        'opening': opening, 'all_in_at_opening': all_in_at_opening,
        'max_bid': max_bid, 'resale_cost': resale_cost, 'is_td': is_td,
        'headroom': exit_value - all_in_at_opening,
    }


CSS = """
:root{--navy:#122048;--gold:#b58a1f;--ink:#1b2437;--mut:#5b6472;--line:#e2e6ef;--red:#b3372f;--grn:#1e7a3c;--soft:#f7f8fc}
*{box-sizing:border-box}
body{font:12.5px/1.55 'Segoe UI',Arial,sans-serif;color:var(--ink);margin:0;padding:24px 30px;background:#fff}
.hdr{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid var(--navy);padding-bottom:10px;margin-bottom:4px}
h1{font-size:19px;margin:0;color:var(--navy);letter-spacing:-.01em}
.brand{font-size:10px;letter-spacing:.22em;color:var(--gold);font-weight:700;text-transform:uppercase}
.sub{color:var(--mut);font-size:11.5px;margin:6px 0 14px}
h2{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);margin:18px 0 6px;border-bottom:1px solid var(--line);padding-bottom:3px}
.big{display:flex;gap:10px;margin:12px 0}
.card{flex:1;border:1px solid var(--line);border-radius:9px;padding:11px 13px;background:var(--soft)}
.card.hero{background:var(--navy);border-color:var(--navy)}
.card .k{font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);font-weight:700}
.card.hero .k{color:#9fb0d4}
.card .v{font-size:21px;font-weight:800;color:var(--navy);margin-top:2px;line-height:1.1}
.card.hero .v{color:var(--gold)}
.card .s{font-size:10.5px;color:var(--mut);margin-top:3px}
.card.hero .s{color:#c3cee6}
table{width:100%;border-collapse:collapse;font-size:11.5px;margin:6px 0}
td,th{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:9.5px;letter-spacing:.07em;color:var(--mut);text-transform:uppercase}
.n{text-align:right;font-weight:700;white-space:nowrap}
.tag{display:inline-block;font-size:9px;font-weight:800;letter-spacing:.06em;padding:2px 7px;border-radius:20px}
.t-surv{background:#fdecea;color:var(--red);border:1px solid #e7b3ae}
.t-wipe{background:#eaf5ec;color:var(--grn);border:1px solid #bfe0c8}
.t-clear{background:#eef2fa;color:var(--navy);border:1px solid #ccd7ea}
.t-unk{background:#fff6e5;color:#8a6100;border:1px solid #e8cf96}
.flag{background:#fdecea;border:1px solid #e7b3ae;color:var(--red);border-radius:8px;padding:10px 13px;margin:9px 0;font-weight:600}
.note{background:#f4f7ff;border-left:4px solid var(--navy);border-radius:6px;padding:9px 13px;margin:9px 0}
.ok{background:#eaf5ec;border:1px solid #bfe0c8;color:var(--grn);border-radius:8px;padding:9px 13px;margin:9px 0}
ul,ol{margin:5px 0;padding-left:20px}li{margin:4px 0}
.est{color:var(--mut);font-size:10px}
.mono{font-family:Consolas,monospace;font-size:10.5px}
.foot{margin-top:18px;border-top:1px solid var(--line);padding-top:8px;font-size:9.5px;color:var(--mut)}
@media print{body{padding:14px 18px}.card{background:#fff}}
"""


def render_html(b):
    r, dil = b['r'], b['dil']
    case, addr = b['case'], r.get('addr') or ''
    auc = r.get('auction') or ''
    days = r.get('days')
    daytxt = (f'{days} days out' if isinstance(days, int) and 0 <= days < 9999
              else ('sale date passed' if isinstance(days, int) and days < 0 else 'no date set'))

    # ---- survival table
    tag_cls = {'SURVIVES': 't-surv', 'SURVIVES*': 't-surv', 'WIPED': 't-wipe',
               'CLEAR': 't-clear', 'PARTIAL': 't-unk'}
    surv = ''
    for st, label, amt, why in b['surv_rows']:
        surv += (f'<tr><td><span class="tag {tag_cls.get(st,"t-unk")}">{_esc(st)}</span></td>'
                 f'<td><b>{_esc(label)}</b><div class="est">{_esc(why)}</div></td>'
                 f'<td class="n">{_money(amt)}</td></tr>')
    for label, why in b['unknown']:
        surv += (f'<tr><td><span class="tag t-unk">UNVERIFIED</span></td>'
                 f'<td><b>{_esc(label)}</b><div class="est">{_esc(why)}</div></td>'
                 f'<td class="n">?</td></tr>')

    # ---- payoff line, sourced
    if b['accrued']:
        pay_note = (f"Judgment {_money(b['judg'])} entered {_esc(r.get('jdate',''))} + "
                    f"{_money(r.get('jaccr'))} post-judgment interest (FS 55.03, rate resets each "
                    f"Jan 1) = <b>{_money(b['payoff'])}</b> to satisfy on {_esc(r.get('jasof',''))}. "
                    f"Entry date verified on the clerk's docket.")
    elif b['judg'] > 0:
        pay_note = (f"Judgment {_money(b['judg'])} <b>as entered</b>. We could not verify the entry "
                    f"date for this county, so no interest is accrued here — the true payoff is "
                    f"HIGHER by roughly 8–9%/yr since entry. Confirm with the plaintiff's payoff letter.")
    else:
        pay_note = 'No final judgment amount published yet — the debt is unknown. Do not bid blind.'

    # ---- title gates from the diligence engine
    killers = dil.get('killer_issues') or []
    title = dil.get('title') or {}
    gates = ''
    if killers:
        gates = '<div class="flag">⚠️ TITLE / STRUCTURAL ISSUES<ul>' + ''.join(
            f'<li>{_esc(k)}</li>' for k in killers) + '</ul></div>'
    elif title.get('lady_bird'):
        gates = ('<div class="flag">⚠️ Lady Bird / enhanced life estate on title — confirm every '
                 'remainderman before bidding.</div>')

    # ---- the verdict band
    hr = b['headroom']
    if b['judg'] <= 0:
        vb, vt = 'flag', 'DO NOT BID YET — no published judgment; the debt is unknown.'
    elif hr <= 0:
        vb, vt = 'flag', (f'NO ROOM AT THE OPENING. Clearing the payoff plus surviving liens costs '
                          f'{_money(b["all_in_at_opening"])} against a {_money(b["exit_value"])} exit. '
                          f'This is a plaintiff take-back, not a buy.')
    elif hr < b['exit_value'] * 0.15:
        vb, vt = 'note', (f'THIN. Only {_money(hr)} between all-in at the opening and exit value — '
                          f'before rehab, holding and eviction. Bid only with verified rehab numbers.')
    else:
        vb, vt = 'ok', (f'ROOM EXISTS: {_money(hr)} between all-in at the opening '
                        f'({_money(b["all_in_at_opening"])}) and exit value ({_money(b["exit_value"])}). '
                        f'Discipline below still applies.')

    occ = r.get('hs') and r.get('mail') and r.get('addr') and \
        str(r.get('mail')).split(',')[0].strip().upper() == str(r.get('addr')).split(',')[0].strip().upper()

    cites = dil.get('citations') or []
    cite_html = ''.join(
        f'<li>{_esc(c.get("label",""))}: <span class="mono">{_esc(c.get("url_or_bp",""))}</span></li>'
        for c in cites[:10])
    if r.get('auc'):
        cite_html = f'<li>Auction listing: <span class="mono">{_esc(r["auc"])}</span></li>' + cite_html

    today = datetime.date.today().isoformat()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auction Bid Brief — {_esc(addr)}</title><style>{CSS}</style></head><body>

<div class="hdr">
  <div><div class="brand">Auction Bid Brief</div>
       <h1>{_esc(addr or case)}</h1></div>
  <div style="text-align:right">
    <div class="brand" style="color:var(--navy)">SALE {_esc(auc) or '—'}</div>
    <div class="est">{_esc(daytxt)}</div></div>
</div>
<div class="sub">Case <span class="mono">{_esc(case)}</span> · {_esc(r.get('county',''))} County ·
Folio <span class="mono">{_esc(r.get('folio',''))}</span> ·
{_esc(r.get('ctype') or r.get('ftype') or 'Foreclosure')} sale ·
Prepared {today} from primary sources (clerk docket, official records, county tax collector, property appraiser).</div>

<div class="big">
  <div class="card hero"><div class="k">Max disciplined bid</div><div class="v">{_money(b['max_bid'])}</div>
    <div class="s">70% of exit, less surviving liens &amp; resale cost. Guidance, not an appraisal.</div></div>
  <div class="card"><div class="k">To clear at the opening</div><div class="v">{_money(b['all_in_at_opening'])}</div>
    <div class="s">payoff {_money(b['opening'])} + {_money(b['survives_total'])} surviving</div></div>
  <div class="card"><div class="k">Exit value</div><div class="v">{_money(b['exit_value'])}</div>
    <div class="s">{'comps ARV (' + str(r.get('arvn', 0)) + ' comps)' if b['arv_ok'] else 'county just value — no verified comps'}</div></div>
</div>

<div class="{vb}">{vt}</div>

<h2>What survives the sale — what lands on you</h2>
<table><tr><th>Status</th><th>Item</th><th class="n">Amount</th></tr>{surv}</table>
<div class="est">Junior liens are extinguished only against parties properly named and served. Verify
service on the docket before relying on a WIPED row.</div>

<h2>What the plaintiff must actually be paid</h2>
<div class="note">{pay_note}</div>

{gates}

<h2>Property &amp; occupancy</h2>
<table>
<tr><td>Type</td><td>{_esc(r.get('dor_desc') or ('Condo' if r.get('condo') else 'Single family'))}
  {' · <b>HOMESTEAD</b>' if r.get('hs') else ''}</td></tr>
<tr><td>Owner of record</td><td>{_esc(r.get('oname') or r.get('owners') or '—')}</td></tr>
<tr><td>Occupancy</td><td>{'<b>Likely OWNER-OCCUPIED</b> — budget eviction time and cost; FS 83.561 tenant protections may apply.' if occ else 'No homestead/mailing match — occupancy unconfirmed. Inspect from the street before the sale.'}</td></tr>
<tr><td>Plaintiff</td><td>{_esc(r.get('plaintiff') or '—')}</td></tr>
</table>

<h2>Bidding discipline — read before the auction</h2>
<ol>
<li><b>You cannot inspect the interior.</b> Every number above assumes an unknown interior condition.
    Hold back a rehab reserve or do not bid.</li>
<li><b>The plaintiff can credit-bid</b> up to its judgment without cash. Below that number you are
    bidding against the bank, not against other buyers.</li>
<li><b>Deposit rules are unforgiving</b> — Miami-Dade requires 5% immediately and the balance by
    the deadline, or you forfeit the deposit.</li>
<li><b>Confirm the sale is still on</b> the morning of. Sales get cancelled for payoff, bankruptcy
    or loss-mitigation constantly; this brief is accurate as of {today}.</li>
<li><b>Title insurance after a foreclosure sale is not automatic.</b> Budget a quiet-title action
    where an heir, life estate or service defect appears above.</li>
</ol>

<h2>Sources — verify any line yourself</h2>
<ul>{cite_html or '<li>See the case docket and official records for this folio.</li>'}</ul>

<div class="foot">
Prepared by Miami Solutions Group. Compiled from public records believed accurate on {today}; every
figure is traceable to the sources listed. <b>This is research, not legal, tax or investment advice,
and not a title opinion.</b> Auction outcomes, property condition and title status can change without
notice — confirm the sale status and obtain a title search before bidding.
</div>
</body></html>"""


def write_brief(r, dil, make_pdf=False):
    os.makedirs(OUT_DIR, exist_ok=True)
    b = build_brief(r, dil)
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', b['case'] or 'case')
    base = os.path.join(OUT_DIR, f'Bid-Brief-{safe}')
    html_path = base + '.html'
    open(html_path, 'w', encoding='utf-8').write(render_html(b))
    out = [html_path]
    if make_pdf and os.path.exists(EDGE):
        pdf = base + '.pdf'
        try:
            subprocess.run([EDGE, '--headless', '--disable-gpu', '--no-pdf-header-footer',
                            f'--print-to-pdf={pdf}', 'file:///' + html_path.replace('\\', '/')],
                           check=True, capture_output=True, timeout=90)
            out.append(pdf)
        except Exception as e:
            print(f'   (pdf skipped: {str(e)[:70]})')
    return b, out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--case', default='')
    ap.add_argument('--auction', default='', help='render every property on this sale date (MM/DD/YYYY)')
    ap.add_argument('--list', action='store_true', help='upcoming sales briefable right now')
    ap.add_argument('--pdf', action='store_true')
    ap.add_argument('--limit', type=int, default=25)
    a = ap.parse_args()

    data = _load_board()
    dcache = {}
    p = os.path.join(HERE, 'diligence_cache.json')
    if os.path.exists(p):
        try:
            dcache = json.load(open(p, encoding='utf-8')) or {}
        except Exception:
            dcache = {}

    if a.list:
        today = datetime.date.today()
        up = [r for r in data if _d(r.get('auction')) and _d(r.get('auction')) >= today
              and float(r.get('judg') or 0) > 0]
        up.sort(key=lambda r: _d(r.get('auction')))
        print(f'{len(up)} upcoming sale(s) with a published judgment:\n')
        for r in up[:a.limit]:
            pay = float(r.get('payoff') or r.get('judg') or 0)
            flag = '' if r.get('jaccrued') else '  (judgment as-entered)'
            print(f"  {r.get('auction')}  {str(r.get('case')):22} {str(r.get('addr'))[:40]:40} "
                  f"payoff {_money(pay):>12}{flag}")
        return 0

    targets = []
    if a.case:
        targets = [r for r in data if (r.get('case') or '') == a.case]
        if not targets:
            sys.exit(f'case not on the board: {a.case}')
    elif a.auction:
        targets = [r for r in data if (r.get('auction') or '') == a.auction
                   and float(r.get('judg') or 0) > 0][:a.limit]
        if not targets:
            sys.exit(f'no judgment-bearing properties on {a.auction}')
    else:
        sys.exit('pass --case, --auction or --list')

    for r in targets:
        dil = _best_diligence(r, dcache)
        b, paths = write_brief(r, dil, make_pdf=a.pdf)
        print(f"\n{r.get('addr')}  [{r.get('case')}]")
        print(f"   exit {_money(b['exit_value'])} · payoff {_money(b['payoff'])}"
              f"{'' if b['accrued'] else ' (as entered)'} · survives {_money(b['survives_total'])}")
        print(f"   MAX BID {_money(b['max_bid'])} · headroom at opening {_money(b['headroom'])}")
        for x in paths:
            print(f'   -> {x}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
