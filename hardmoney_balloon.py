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

    # COUNTYWIDE SWEEP (fl_lp/broward_mortgages.py) — the volume unlock. These are hard-money-to-LLC
    # mortgages pulled straight from the recorder index, NOT scoped to the foreclosure book, so they
    # are the pre-balloon universe Jesse actually wants. No case number, no Sunbiz human yet (that's
    # a follow-up llc_officers pull keyed on the borrower name); the LLC + lender + amount + age are
    # the actionable core.
    for m in _rows(_load('broward_mortgages.json', [])):
        if not isinstance(m, dict):
            continue
        iso = str(m.get('origin') or '')[:10]
        if not re.match(r'\d{4}-\d{2}-\d{2}', iso) or iso < lo or iso > hi:
            continue
        hits.append({
            'case': '', 'county': m.get('county') or 'BROWARD', 'owner': str(m.get('borrower') or '').strip(),
            'addr': '', 'value': 0, 'lender': str(m.get('lender') or '').strip(),
            'amt': m.get('amt') or 0, 'origin': iso,
            'age_mo': round((today - datetime.date.fromisoformat(iso)).days / 30.4, 1),
            'agent': '', 'agent_ph': '', 'agent_addr': '', 'source': 'countywide',
            'parcel': str(m.get('parcel') or '').strip(),
        })

    # FOLD IN THE FREE ENRICHMENT (hardmoney_enrich.py): Sunbiz human + BCPA address, keyed by the
    # LLC borrower name. Loans in the institutional band are dropped from the book entirely — a
    # $100M+ private-equity borrower is not refinancing through a local broker, and leaving them in
    # buries the actual clients under paper nobody can work.
    enr = _load('hardmoney_enriched.json', {})
    for h in hits:
        e = enr.get(h['owner']) or {}
        sb, bc = (e.get('sunbiz') or {}), (e.get('bcpa') or {})
        if sb.get('officer'):
            h['agent'] = sb['officer']
            h['agent_addr'] = sb.get('officer_addr') or ''
            h['agent_title'] = sb.get('title') or ''
        elif sb.get('agent'):
            h['agent'] = sb['agent'] + ' (reg. agent)'
            h['agent_addr'] = sb.get('agent_addr') or ''
        if bc.get('addr'):
            h['addr'] = ', '.join(x for x in (bc.get('addr'), bc.get('city')) if x)
            h['folio'] = bc.get('folio') or ''
        elif bc.get('parcels'):
            h['addr'] = '(%d parcels — verify which)' % bc['parcels']
    hits = [h for h in hits if 75000 <= int(h.get('amt') or 0) <= 5000000]

    # FOLD IN THE LTV + PROGRAM VERDICT (hardmoney_values.py). This is what turns the book from a
    # list of names into a QUALIFIED list: Jesse's son underwrites on LTV, so a row without one
    # cannot be pitched, and sorting by loan size alone ranks by commission rather than by who can
    # actually close. Keyed on (borrower, lender, origin, amount) — the same tuple the sweep dedupes
    # on. Fail-soft: no hardmoney_values.json yet just leaves every row unscored, exactly as before.
    try:
        import hardmoney_values as HV
        vmap = {}
        for p in HV.priced(zone_only=False):
            vmap[(p['borrower'], p['lender'], p['origin'], p['amt'])] = p
        for h in hits:
            p = vmap.get((h['owner'], h['lender'], h['origin'], int(h.get('amt') or 0)))
            if not p:
                continue
            h['ltv'] = p.get('ltv') or 0
            h['verdict'] = p.get('verdict') or ''
            h['verdict_why'] = p.get('verdict_why') or ''
            h['fits'] = bool(p.get('fits'))
            h['jv'] = p.get('jv') or 0
            if p.get('addr') and not h.get('addr'):
                h['addr'] = ', '.join(x for x in (p.get('addr'), p.get('city')) if x)
    except Exception as e:                                   # never let pricing break the book
        print('  (LTV pass skipped: %s)' % str(e)[:70])

    # dedupe by (case, lender, origin) / (borrower, lender, origin) for swept rows
    seen, uniq = set(), []
    for h in sorted(hits, key=lambda x: x['origin'], reverse=True):
        k = (h['case'] or h['owner'], h['lender'], h['origin'])
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
    # THE BALLOON ZONE is 8-24 months old: past the 1-yr balloon, at or approaching the 2-yr. Those
    # are the loans coming due NOW — the actionable book. The table shows them, biggest loan first
    # (biggest loan = biggest refi commission), capped so the sheet stays a sheet; the KPIs still
    # count the whole universe.
    zone = [h for h in uniq if 8 <= h['age_mo'] <= 30]
    # FUNDABLE FIRST, then urgency, then size. Ordering by size alone put a $2M loan at 140% LTV —
    # which nobody can refinance — above a $300k loan at 61% that closes. The verdict has to lead.
    zone.sort(key=lambda h: (bool(h.get('fits')), (20 <= h['age_mo'] <= 30), h['amt']), reverse=True)
    _write_report(uniq, zone[:150], lenders, lo, hi, a.months)
    return uniq


def _write_report(full, hits, lenders, lo, hi, months):
    # KPIs count the WHOLE universe; the table shows the balloon-zone display slice (`hits`).
    swept = [h for h in full if h.get('source') == 'countywide']
    zone_all = [h for h in full if 8 <= h['age_mo'] <= 30]
    n = len(full)
    zone_n = len(zone_all)
    total = sum(int(h['amt'] or 0) for h in zone_all)
    fits = [h for h in zone_all if h.get('fits')]
    fits_vol = sum(int(h['amt'] or 0) for h in fits)
    scored_n = sum(1 for h in zone_all if h.get('verdict'))
    # Verdict -> chip colour. Green = fundable today, amber = needs a look, grey = not scoreable.
    CHIP = {'DSCR-STRONG': 'g', 'DSCR-EDGE': 'g', 'DSCR-ANY': 'g', 'TIGHT': 'a', 'MULTI': 'a',
            'OVER': 'r', 'ARV': 'm', 'SOLD?': 'm', 'NOVALUE': 'm', 'NOFOLIO': 'm'}
    rows = ''
    for h in hits:
        v = h.get('verdict') or ''
        chip = ('<span class="v %s" title="%s">%s</span>'
                % (CHIP.get(v, 'm'), H.escape(h.get('verdict_why') or ''), H.escape(v))) if v \
            else '<span class="mut">unscored</span>'
        ltv = ('%.0f%%' % h['ltv']) if h.get('ltv') else '<span class="mut">&mdash;</span>'
        rows += ('<tr><td>%s</td><td class="r">%s mo</td><td>%s</td><td class="r">$%s</td>'
                 '<td class="r">%s</td><td>%s</td>'
                 '<td>%s</td><td>%s</td><td>%s%s</td></tr>' % (
                     H.escape(h['origin']), h['age_mo'], H.escape(h['lender']),
                     format(int(h['amt'] or 0), ','),
                     ltv,                                              # markup, do NOT escape
                     chip,                                             # markup, do NOT escape
                     H.escape(h['owner'][:34]),
                     H.escape((h.get('addr') or '').split(',')[0][:26] or '—'),
                     (H.escape(h['agent']) if h.get('agent')
                      else '<span class="mut">Sunbiz pull pending</span>'),   # markup, do NOT escape
                     (' &middot; ' + H.escape(h['agent_ph'])) if h.get('agent_ph') else ''))
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
.v{{display:inline-block;padding:2px 7px;border-radius:99px;font-size:11px;font-weight:700;white-space:nowrap;cursor:help}}
.v.g{{background:#dff5e3;color:#0b5d1e}} .v.a{{background:#fdf1d6;color:#7a5406}}
.v.r{{background:#fbe0e0;color:#8a1c1c}} .v.m{{background:#eef1f6;color:#69748a}}
.box{{background:#f3f6fb;border:1px solid #dbe3ef;border-radius:10px;padding:14px 16px;margin:18px 0}}
.box h3{{margin:0 0 8px;font-size:14px}} .yes{{color:#0b5d1e;font-weight:700}} .no{{color:#8a1c1c;font-weight:700}}
ul{{margin:6px 0 0 18px;columns:2}}
</style></head><body>
<h1>Hard-Money Balloon Book &mdash; investor loans coming due</h1>
<div class="sub">Miami Solutions Group &middot; built {date} from public mortgage records + Sunbiz &middot;
origin window {lo} to {hi} ({months} months)</div>
<div class="kpis">
  <div class="kpi"><div class="n">{fits_n}</div><div class="l">Fit his DSCR box (&le;80% LTV)</div></div>
  <div class="kpi"><div class="n">${fitsM}M</div><div class="l">Fundable loan volume</div></div>
  <div class="kpi"><div class="n">${saveM}M</div><div class="l">Interest saved / yr at 11%&rarr;7%</div></div>
  <div class="kpi"><div class="n">{zone_n}</div><div class="l">In the balloon zone (8&ndash;30mo)</div></div>
  <div class="kpi"><div class="n">{human_n}</div><div class="l">With a named human (Sunbiz)</div></div>
</div>
<div class="box"><h3>Priced against Jesse&rsquo;s program sheet &mdash; the column that decides the call</h3>
<p style="margin:0 0 8px">Every row carries an LTV computed from the loan amount against the county&rsquo;s
certified roll value, then graded against the ladder on his sheet
(<b>DSCR 70&ndash;80</b> &middot; hard equity 60&ndash;70 &middot; foreclosure bailout 50&ndash;60):</p>
<div><span class="v g">DSCR-STRONG</span> &le;70% &mdash; fits with cushion &nbsp;
<span class="v g">DSCR-EDGE</span> 70&ndash;80% &mdash; fits, no cushion &nbsp;
<span class="v g">DSCR-ANY</span> multi-parcel, clears every one &nbsp;
<span class="v a">TIGHT</span> 80&ndash;90% &mdash; roll lags market, appraisal may fit &nbsp;
<span class="v a">MULTI</span> which parcel secures it decides &nbsp;
<span class="v r">OVER</span> exceeds roll value &nbsp;
<span class="v m">ARV</span> vacant &mdash; construction paper</div>
<p style="margin:8px 0 0"><b>The table is sorted fundable-first</b>, then by balloon urgency, then by size.
A $2M loan at 140% LTV is not a lead; a $300k at 61% closes.</p></div>
<div class="box"><h3>This is the COUNTYWIDE sweep &mdash; not just the foreclosure book</h3>
<p style="margin:0">{swept_n} of these came from a direct sweep of the Broward recorder&rsquo;s mortgage index
(document type = MORTGAGE) over {months} months &mdash; <b>every</b> hard-money loan to an LLC, not only the ones
already in foreclosure. These are the balloons <b>about to pop, not the ones that already did</b>. Same scraper
rails as the lis-pendens sweeps.</p></div>
<div class="box"><h3>What each row gives you (thumbs up)</h3>
<div class="yes">&#10003; Borrower is an LLC &nbsp; &#10003; Lender is a private / hard-money lender (Kiavi, Lima One, Alto Capital, RBI, Velocity &mdash; every big bank + credit union filtered out)
&nbsp; &#10003; The exact loan amount &nbsp; &#10003; Origin date &rarr; age &rarr; balloon window &nbsp; &#10003; Parcel to pull the address &amp; the Sunbiz human behind the LLC</div></div>
<div class="box"><h3>The honest limits &mdash; read before quoting anyone</h3>
<div class="no">&times; <b>The exact MATURITY / balloon date is not in the recorded index.</b> It lives inside the
mortgage document image. Origin date is the proxy (hard-money = 1&ndash;2 yr balloon, exactly Jesse&rsquo;s tell).<br>
&times; <b>LTV is against the county&rsquo;s certified roll, not an appraisal.</b> The roll is annual and trails the
market, so these LTVs run CONSERVATIVE &mdash; a row at 85% may appraise inside the box. That is why 80&ndash;90%
is labelled TIGHT rather than dead.<br>
&times; <b>{unpriced} of the {zone_n} zone loans carry no usable LTV.</b> Either the LLC owns several parcels and
the recorded index does not say which one secures the loan (read the mortgage), or no parcel sits in that
LLC&rsquo;s name today &mdash; usually because it already sold, which means they are not a refi client at all.<br>
&times; <b>Vacant-land rows are never scored.</b> A ground-up construction loan is written against finished value;
calling it &ldquo;upside down&rdquo; against dirt would be wrong on the first call.</div></div>
<div class="box"><h3>Private lenders in the book (by volume of loans)</h3><ul>{lend_rows}</ul></div>
<table><thead><tr><th>Originated</th><th>Age</th><th>Lender</th><th>Loan</th><th>LTV</th><th>Verdict</th><th>Owner (LLC)</th><th>Property</th><th>Human behind the LLC</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>""".format(date=datetime.date.today().isoformat(), lo=lo, hi=hi, months=months,
                         n=n, zone_n=zone_n, swept_n=len(swept), totalM=round(total / 1e6, 1),
                         fits_n=len(fits), fitsM=round(fits_vol / 1e6, 1),
                         saveM=round(fits_vol * 0.04 / 1e6, 1), scored_n=scored_n,
                         unpriced=zone_n - scored_n + sum(1 for h in zone_all
                                                          if h.get('verdict') in ('MULTI', 'SOLD?',
                                                                                  'NOFOLIO', 'NOVALUE')),
                         nlend=len(lenders), human_n=sum(1 for h in zone_all if h.get('agent')),
                         lend_rows=lend_rows or '<li class="mut">none in this window</li>', rows=rows)
    # THE PUBLISHED COPY IS REDACTED. This repo is PUBLIC (verified 2026-08-12: raw.githubusercontent
    # serves its data files with HTTP 200), and GitHub Pages serves docs/ to the world, so the
    # "unguessable folder" convention borrowed from Carlos's door books is obscurity with a public
    # file tree behind it — i.e. no protection at all. docs/index.html solves this by ENCRYPTING its
    # payload; this book has no such gate.
    # So the site copy carries only corporate public record — LLC, lender, loan, LTV, verdict — and
    # the "Human behind the LLC" column (Sunbiz officer names and phones) is stripped. That column is
    # the calling detail; it stays on Alejandro's machine and out of a search index. The local and
    # Desktop copies below are the FULL book.
    pub = re.sub(r'<th>Human behind the LLC</th>', '<th>Human</th>', doc)
    pub = re.sub(r'(<tr>(?:<td[^>]*>.*?</td>){8})<td>.*?</td></tr>',
                 r'\1<td><span class="mut">on file</span></td></tr>', pub)
    pub = pub.replace('<div class="kpi"><div class="n">{human_n}</div>'.format(human_n=''), '')
    docs_out = os.path.join(HERE, 'docs', 'hm-balloon-q7v3n8', 'index.html')
    os.makedirs(os.path.dirname(docs_out), exist_ok=True)
    open(docs_out, 'w', encoding='utf-8').write(pub)

    outs = [os.path.join(HERE, 'HardMoney_Balloon_Book.html'),
            ]
    # DEALFLOW_NO_DESKTOP is set on the GitHub runner (the OneDrive path only exists on Alejandro's
    # machine); without this guard the cloud run creates a junk ~/OneDrive/Desktop tree every night.
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        outs.append(os.path.expanduser(os.path.join(
            '~', 'OneDrive', 'Desktop', 'HardMoney_Balloon_Book_%s.html' % datetime.date.today())))
    for o in outs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'w', encoding='utf-8').write(doc)
    print('\n-> %s' % outs[-1])
    print('-> LIVE: https://<pages-host>/hm-balloon-q7v3n8/  (rebuilt nightly)')


if __name__ == '__main__':
    main()
