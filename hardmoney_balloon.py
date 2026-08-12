#!/usr/bin/env python
"""hardmoney_balloon — Jesse's play: private/hard-money loans to LLC investors, about to balloon.

The thesis (Jesse, 2026-08-12 call): hard-money loans to investors almost always carry a 1-2 year
BALLOON. So a mortgage that (a) was recorded by a private/hard-money lender, (b) to an LLC, and
(c) originated 8-24 months ago is very likely coming due NOW — and the borrower is a sophisticated
LLC we can reach through Sunbiz, refinance into a real 5/10/20-yr product, and close without a
door-knock. The maturity date itself is NOT in the recorded index (would need to read each mortgage
image), but the ORIGIN date + the lender's hard-money signature is a strong proxy for it.

WHAT THIS PROVES the system already has, from public records we already scrape:
  * lender NAME on every recorded mortgage  (lien['party'])
  * ORIGIN / recording date                 (lien['_dt'] or lien['d'])
  * OPEN vs SATISFIED status                 (lien['st'])
  * LLC ownership                            (owner string / board 'co' flag)
  * the HUMAN behind the LLC                 (llc_officers.json — Sunbiz officer + agent)

Run:  python hardmoney_balloon.py               # print the hits + write the one-pager
      python hardmoney_balloon.py --months 24   # origin window (default 24)
"""
import argparse
import datetime
import html as H
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# Hard-money / private-lender name signatures. These are the tokens Florida private lenders to
# investors put in their entity names — verified against live data (Benworth Capital Partners,
# HouseMax Funding, American Heritage Lending, Lendz Financial, SG Capital Partners...).
HM_POS = re.compile(r'\b(CAPITAL|LENDING|LENDER|FUND(?:ING|S)?|EQUITY|PRIVATE|BRIDGE|HARD\s*MONEY|'
                    r'INVEST(?:MENT|MENTS|ORS)?|FINANCIAL|FINANCE|LOANS?|MORTGAGE\s*FUND|'
                    r'REI\b|TRUST\s*DEED|ADVANCE|VENTURES?|HOLDINGS|GROUP)\b', re.I)
# Institutional lenders — a hard-money keyword inside one of these is a false positive, drop it.
BIG_BANK = re.compile(r'\b(WELLS\s*FARGO|BANK\s*OF\s*AMERICA|CHASE|JPMORGAN|CITIBANK|CITIMORTGAGE|'
                      r'U\.?S\.?\s*BANK|PNC|TRUIST|SUNTRUST|REGIONS|FIFTH\s*THIRD|MERS|MORTGAGE\s*'
                      r'ELECTRONIC|FREDDIE|FANNIE|QUICKEN|ROCKET|LOANDEPOT|PENNYMAC|HSBC|TD\s*BANK|'
                      r'NATIONSTAR|MR\.?\s*COOPER|FLAGSTAR|USAA|NAVY\s*FED|CREDIT\s*UNION|'
                      r'\bFHA\b|\bHUD\b|\bVA\b|CALIBER|FREEDOM\s*MORTGAGE|CARRINGTON|NEWREZ|'
                      r'CROSSCOUNTRY|GUARANTEED\s*RATE|HOMEPOINT|AMERIHOME)\b', re.I)
COMPANY = re.compile(r'\b(LLC|L\.L\.C|INC\b|CORP|CO\b|LP\b|LLP|LTD|TRUST|HOLDINGS|PROPERTIES|'
                     r'GROUP|ENTERPRISES|INVESTMENTS?|VENTURES?|REALTY|CAPITAL|PARTNERS)\b', re.I)


def _load(fn, default):
    try:
        return json.load(open(os.path.join(HERE, fn), encoding='utf-8'))
    except Exception:
        return default


def _rows(d):
    return d if isinstance(d, list) else list(d.values())


def _dt(lien):
    s = str(lien.get('_dt') or '')
    if re.match(r'\d{4}-\d{2}-\d{2}', s):
        return s[:10]
    d = str(lien.get('d') or '').strip()
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', d)
    if m:
        return '%s-%02d-%02d' % (m.group(3), int(m.group(1)), int(m.group(2)))
    return ''


def _is_open(lien):
    return str(lien.get('st', '')).upper().startswith('OPEN')


def _is_hardmoney(party):
    p = str(party or '')
    return bool(HM_POS.search(p)) and not BIG_BANK.search(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=24, help='origin window: keep mortgages recorded within N months')
    ap.add_argument('--min-months', type=int, default=0, help='and no NEWER than this many months (0=off)')
    a = ap.parse_args()

    today = datetime.date.today()
    lo = (today - datetime.timedelta(days=int(a.months * 30.4))).isoformat()
    hi = (today - datetime.timedelta(days=int(a.min_months * 30.4))).isoformat() if a.min_months else today.isoformat()

    # property context (owner / address / value / county) keyed by case + folio
    board = {}
    for fn, ck in (('leads_final.json', 'Case #'), ('broward_leads.json', 'case'),
                   ('palmbeach_leads.json', 'case'), ('lp_leads.json', 'case')):
        for r in _rows(_load(fn, [])):
            if isinstance(r, dict):
                c = str(r.get(ck) or r.get('case') or '')
                if c:
                    board.setdefault(c, r)
    officers = _load('llc_officers.json', {})

    hits = []
    for fn, county in (('records_liens.json', 'MIAMI-DADE'), ('batchdata_liens.json', ''),
                       ('broward_liens.json', 'BROWARD'), ('palmbeach_liens.json', 'PALM BEACH')):
        d = _load(fn, {})
        keyed = d if isinstance(d, dict) else {}
        src = keyed.items() if keyed else [(None, r) for r in _rows(d)]
        for case, rec in src:
            if not isinstance(rec, dict):
                continue
            owner = str(rec.get('owner') or (board.get(case, {}) or {}).get('owners') or '').split(';')[0]
            lead = board.get(case, {})
            owner_is_llc = bool(COMPANY.search(owner)) or bool(lead.get('co'))
            if not owner_is_llc:
                continue
            for lien in (rec.get('liens') or []):
                if not _is_open(lien):
                    continue
                if not _is_hardmoney(lien.get('party')):
                    continue
                iso = _dt(lien)
                if not iso or iso < lo or iso > hi:
                    continue
                off = officers.get(case) or {}
                offc = (off.get('officers') or [{}])
                human = next((p for p in offc if p.get('n')), {}) if offc else {}
                hits.append({
                    'case': case or '', 'county': county or lead.get('county') or '',
                    'owner': owner.strip(), 'addr': lead.get('addr') or lead.get('Address') or '',
                    'value': lead.get('value') or lead.get('Assessed Value') or 0,
                    'lender': str(lien.get('party') or '').strip(),
                    'amt': lien.get('amt') or 0, 'origin': iso,
                    'age_mo': round((today - datetime.date.fromisoformat(iso)).days / 30.4, 1),
                    'agent': human.get('n') or off.get('agent') or '',
                    'agent_ph': human.get('ph') or '', 'agent_addr': human.get('a') or '',
                })

    # dedupe by (case, lender, origin)
    seen, uniq = set(), []
    for h in sorted(hits, key=lambda x: x['origin'], reverse=True):
        k = (h['case'], h['lender'], h['origin'])
        if k not in seen:
            seen.add(k)
            uniq.append(h)

    print('HARD-MONEY BALLOON CANDIDATES: %d' % len(uniq))
    print('(open mortgage · hard-money lender · LLC owner · originated %s..%s)' % (lo, hi))
    for h in uniq[:40]:
        print('  %-9s | %5.1fmo | $%9s | %-32s | %s'
              % (h['origin'], h['age_mo'], format(int(h['amt'] or 0), ','),
                 h['lender'][:32], h['owner'][:30]))
    lenders = {}
    for h in uniq:
        lenders[h['lender']] = lenders.get(h['lender'], 0) + 1
    _write_report(uniq, lenders, lo, hi, a.months)
    return uniq


def _write_report(hits, lenders, lo, hi, months):
    n = len(hits)
    with_human = sum(1 for h in hits if h['agent'])
    total = sum(int(h['amt'] or 0) for h in hits)
    rows = ''
    for h in hits:
        rows += ('<tr><td>%s</td><td class="r">%s mo</td><td>%s</td><td class="r">$%s</td>'
                 '<td>%s</td><td>%s</td><td>%s%s</td></tr>' % (
                     H.escape(h['origin']), h['age_mo'], H.escape(h['lender']),
                     format(int(h['amt'] or 0), ','), H.escape(h['owner'][:34]),
                     H.escape((h['addr'] or '').split(',')[0][:26] or '—'),
                     H.escape(h['agent'] or '<span class="mut">Sunbiz pull pending</span>'),
                     (' &middot; ' + H.escape(h['agent_ph'])) if h['agent_ph'] else ''))
    lend_rows = ''.join('<li><b>%s</b> &times;%d</li>' % (H.escape(k), v)
                        for k, v in sorted(lenders.items(), key=lambda x: -x[1]))
    doc = """<!doctype html><html><head><meta charset="utf-8"><title>Hard-Money Balloon Book</title>
<style>
body{{font:14px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#0e1b33;max-width:1000px;margin:0 auto;padding:26px}}
h1{{font-size:23px;margin:0 0 3px}} .sub{{color:#5a6577;margin-bottom:18px}}
.kpis{{display:flex;gap:14px;flex-wrap:wrap;margin:0 0 20px}}
.kpi{{flex:1;min-width:150px;background:#0B1730;color:#fff;border-radius:10px;padding:14px 16px}}
.kpi .n{{font-size:26px;font-weight:800;color:#F4E5A7}} .kpi .l{{font-size:12px;color:#c9d4ea;text-transform:uppercase;letter-spacing:.05em}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}} th,td{{padding:7px 9px;border-bottom:1px solid #e4e8f0;text-align:left}}
th{{background:#0B1730;color:#F4E5A7;position:sticky;top:0}} td.r{{text-align:right;font-variant-numeric:tabular-nums}}
tr:nth-child(even){{background:#fafcff}} .mut{{color:#9aa4b6}}
.box{{background:#f3f6fb;border:1px solid #dbe3ef;border-radius:10px;padding:14px 16px;margin:18px 0}}
.box h3{{margin:0 0 8px;font-size:14px}} .yes{{color:#0b5d1e;font-weight:700}} .no{{color:#8a1c1c;font-weight:700}}
ul{{margin:6px 0 0 18px;columns:2}}
</style></head><body>
<h1>Hard-Money Balloon Book &mdash; investor loans coming due</h1>
<div class="sub">Miami Solutions Group &middot; built {date} from public mortgage records + Sunbiz &middot;
origin window {lo} to {hi} ({months} months)</div>
<div class="kpis">
  <div class="kpi"><div class="n">{n}</div><div class="l">Balloon candidates</div></div>
  <div class="kpi"><div class="n">${totalM}M</div><div class="l">Loan volume in view</div></div>
  <div class="kpi"><div class="n">{human}</div><div class="l">With a Sunbiz human already</div></div>
  <div class="kpi"><div class="n">{nlend}</div><div class="l">Distinct private lenders</div></div>
</div>
<div class="box"><h3>What the system CAN do today (thumbs up)</h3>
<div class="yes">&#10003; Owner is an LLC &nbsp; &#10003; Lender name matches a hard-money signature (excludes Wells/BofA/Chase &amp; every big bank)
&nbsp; &#10003; Mortgage is OPEN, not satisfied &nbsp; &#10003; Origin date inside the balloon window &nbsp; &#10003; Sunbiz officer/agent behind the LLC (name + phone where pulled)</div></div>
<div class="box"><h3>What it CANNOT do from the record alone (the honest limit)</h3>
<div class="no">&times; The exact MATURITY / balloon date is not in the recorded index &mdash; it lives inside the mortgage
document image and needs a per-doc read. We use the ORIGIN date as the proxy (hard-money = 1&ndash;2 yr balloon), which is exactly the tell Jesse described.</div></div>
<div class="box"><h3>Why this list is small &mdash; and how it gets big</h3>
<p style="margin:0">This proof runs on the mortgages we already pull, which are scoped to properties <b>already in
foreclosure</b>. Those are the balloons that <b>already blew</b> &mdash; the borrower couldn&rsquo;t refi in time.
The real money is catching them <b>8&ndash;24 months in, before the balloon hits</b>, and those loans are not in
the foreclosure file. Reaching them is a <b>countywide recorder sweep by lender name + document type = MORTGAGE +
date range</b> &mdash; the exact same scraper machinery that already sweeps lis pendens by date (Broward
AcclaimWeb, Miami-Dade official records). It is a build, not a maybe: ~1 day to wire the mortgage-doctype sweep,
then this same filter runs against the whole county instead of just the foreclosure book.</p></div>
<div class="box"><h3>Private lenders showing up in your data</h3><ul>{lend_rows}</ul></div>
<table><thead><tr><th>Originated</th><th>Age</th><th>Lender</th><th>Loan</th><th>Owner (LLC)</th><th>Property</th><th>Human behind the LLC</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>""".format(date=datetime.date.today().isoformat(), lo=lo, hi=hi, months=months,
                         n=n, totalM=round(total / 1e6, 1), human=with_human, nlend=len(lenders),
                         lend_rows=lend_rows or '<li class="mut">none in this window</li>', rows=rows)
    outs = [os.path.join(HERE, 'HardMoney_Balloon_Book.html'),
            os.path.expanduser(os.path.join('~', 'OneDrive', 'Desktop',
                               'HardMoney_Balloon_Book_%s.html' % datetime.date.today()))]
    for o in outs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'w', encoding='utf-8').write(doc)
    print('\n-> %s' % outs[-1])


if __name__ == '__main__':
    main()
