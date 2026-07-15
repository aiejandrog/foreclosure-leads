"""DEALFLOW Lookup — full workup on ANY Miami-Dade property, on demand.

What it pulls for an address (or 13-digit folio):
  1. Property Appraiser: owner, mailing, beds/baths/sqft/year/pool, homestead, value history, sales chain.
  2. Foreclosure/tax-deed cross-check against the current DEALFLOW leads (case, judgment, sale date, links).
  3. Official Records (best-effort; the county bot-walls automation): the owner's recorded mortgage/lien
     chain with satisfied-vs-open matching and loan amounts inferred from intangible tax (0.2% of loan).
  4. A seller-net calculator (list price -> commission/closing/payoff -> what the owner walks with) —
     the exact math used to talk a seller/agent off a fantasy price.
Output: a styled HTML report in Desktop\DEALFLOW\Lookups\, opened in the browser.

Usage:
  python lookup.py 1760 NE 160 ST
  python lookup.py "888 BRICKELL KEY DR" --unit 807
  python lookup.py 0142060580800
"""
import argparse, json, os, re, sys, time, urllib.parse, webbrowser
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop')
OUTDIR = os.path.join(DESKTOP, 'DEALFLOW', 'Lookups')
PA = "https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx"
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD)\b', re.I)

S = requests.Session()
S.headers.update({"User-Agent": UA, "Referer": "https://apps.miamidadepa.gov/PropertySearch/"})

def num(x):
    try: return float(x or 0)
    except Exception: return 0

def money(n): return ('-$' if n < 0 else '$') + f"{abs(round(n)):,}"

def esc(s): return str(s or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

# ---- 1. resolve the folio ------------------------------------------------------------------
def resolve_folio(addr, unit=''):
    digits = re.sub(r'\D', '', addr)
    if len(digits) == 13 and digits == re.sub(r'\s', '', addr).replace('-',''):
        return digits, None
    r = S.get(PA, params={"Operation":"GetAddress","clientAppName":"PropertySearch",
                          "myAddress":addr, "myUnit":unit, "from":"1","to":"50"}, timeout=25).json()
    hits = r.get("MinimumPropertyInfos") or []
    if not hits:
        sys.exit(f"No parcel found for '{addr}'" + (f" unit {unit}" if unit else '') + " — check the spelling (PA wants e.g. '1760 NE 160 ST').")
    if len(hits) > 1:
        print(f"{len(hits)} parcels match — re-run with the folio:")
        for h in hits[:25]:
            print(f"  {h.get('Strap','')}  {h.get('SiteAddress','')} {h.get('SiteUnit','')}  ({h.get('Owner1','')})")
        sys.exit(0)
    h = hits[0]
    return re.sub(r'\D','', h.get('Strap','')), h

# ---- 2. full PA record ---------------------------------------------------------------------
def pa_record(folio):
    return S.get(PA, params={"Operation":"GetPropertySearchByFolio","clientAppName":"PropertySearch",
                             "folioNumber":folio}, timeout=25).json()

# ---- 3. DEALFLOW leads cross-check ---------------------------------------------------------
def leads_hit(folio):
    f = os.path.join(HERE, 'leads_final.json')
    if not os.path.exists(f): return None
    try: leads = json.load(open(f, encoding='utf-8'))
    except Exception: return None
    for r in leads:
        if re.sub(r'\D','', str(r.get('Folio','') or r.get('year_folio','') or '')) == folio:
            return r
    return None

# ---- 4. Official Records chain (best-effort; captcha-walled) -------------------------------
OR_BASE = 'https://onlineservices.miamidadeclerk.gov/officialrecords/'
def or_chain(owner_last_first, budget_sec=75):
    """Returns (models, None) on success, (None, reason) when the county bot-wall wins."""
    try:
        from playwright.sync_api import sync_playwright
        import foreclosure_leads  # noqa: F401  (not required, but keeps env parity)
    except Exception as e:
        return None, f'playwright unavailable ({e})'
    src = open(os.path.join(HERE, 'gen_records_qs.py'), encoding='utf-8').read()
    m = re.search(r'JS = r"""(.*?)"""', src, re.S)
    key = re.search(r"SITE_KEY = '([^']+)'", src).group(1)
    js = m.group(1).replace('SITEKEY', key)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_context(user_agent=UA, viewport={'width':1400,'height':1000}).new_page()
            t0 = time.time()
            res = None
            for _ in range(3):
                if time.time() - t0 > budget_sec: break
                try:
                    pg.goto(OR_BASE, timeout=40000, wait_until='domcontentloaded')
                    pg.wait_for_timeout(3000)
                    res = pg.evaluate(js, owner_last_first)
                    if res and res.get('success') and res.get('qs'): break
                except Exception:
                    pg.wait_for_timeout(2500)
            b.close()
        if not (res and res.get('success') and res.get('qs')):
            return None, 'county bot-check blocked the automated search'
        g = requests.get(OR_BASE + 'api/SearchResults/getStandardRecords?qs=' + res['qs'],
                         headers={'User-Agent': UA, 'Accept': 'application/json', 'Referer': OR_BASE}, timeout=30).json()
        return g.get('recordingModels', []), None
    except Exception as e:
        return None, str(e)[:80]

def mortgage_table(models, folio):
    """Sorted doc chain + satisfied-matching: an SMO whose orig book/page equals a MOR's book/page closes it."""
    def key_bp(r): return (str(r.get('reC_BOOK','')).strip(), str(r.get('reC_PAGE','')).strip())
    satisfied = set()
    for r in models:
        if 'SATISFACTION' in (r.get('doC_TYPE','') or '').upper():
            satisfied.add((str(r.get('oriG_REC_BOOK','')).strip(), str(r.get('oriG_REC_PAGE','')).strip()))
    rows, open_total = [], 0
    def sortdate(r):
        d = (r.get('reC_DATE','') or '').strip().split(' ')[0]
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', d)
        return (m.group(3), m.group(1).zfill(2), m.group(2).zfill(2)) if m else ('0000','00','00')
    for r in sorted(models, key=sortdate):
        dt = (r.get('doC_TYPE','') or '')
        it = num(r.get('intangible')); amt = num(r.get('consideratioN_1'))
        loan = it/0.002 if it > 0 else amt
        status = ''
        if dt.upper().startswith('MORTGAGE'):
            status = 'SATISFIED' if key_bp(r) in satisfied else 'OPEN (no satisfaction on record)'
            if status.startswith('OPEN') and loan: open_total += loan
        rows.append({'date': (r.get('reC_DATE','') or '')[:10], 'type': dt, 'amt': loan,
                     'party': (r.get('seconD_PARTY','') or ''), 'case': r.get('casE_NUM','') or '',
                     'bp': r.get('reC_BOOKPAGE','') or '', 'status': status})
    return rows, open_total

# ---- 5. the report -------------------------------------------------------------------------
def build_report(folio, d, lead, orrows, oropen, orfail, owner_query):
    pi = d.get('PropertyInfo') or {}
    owners = [o.get('Name','') for o in (d.get('OwnerInfos') or []) if o.get('Name')]
    mail = d.get('MailingAddress') or {}
    mail_s = ' '.join(str(mail.get(k,'') or '') for k in ('Address1','City','State','ZipCode')).strip()
    site = (d.get('SiteAddress') or [{}])
    site_s = (site[0].get('Address','') if isinstance(site, list) and site else '') or ''
    assess = (d.get('Assessment') or {}).get('AssessmentInfos') or []
    val = num(assess[0].get('TotalValue')) if assess else 0
    hs = any('Homestead' in str(x.get('Description','')) for x in ((d.get('Benefit') or {}).get('BenefitInfos') or []))
    pool = any('POOL' in str(x.get('Description','')).upper() for x in ((d.get('ExtraFeature') or {}).get('ExtraFeatureInfos') or []))
    sales = (d.get('SalesInfos') or [])
    condo = 'CONDO' in str(pi.get('DORDescription','') or '').upper()
    fol_fmt = f"{folio[:2]}-{folio[2:6]}-{folio[6:9]}-{folio[9:]}"
    zip5 = (re.search(r'(\d{5})', str(mail.get('ZipCode','') or '')) or re.search(r'(\d{5})', site_s or ''))
    zip5 = zip5.group(1) if zip5 else ''
    is_co = bool(owners and COMPANY_RE.search(owners[0]))
    # People links (reuse the tracker's logic)
    ptoks = [t.strip('.') for t in re.sub(r'\s*&\s*[WH]\b.*$','', owners[0] if owners else '', flags=re.I).split() if len(t.strip('.'))>1]
    people = ('https://www.truepeoplesearch.com/results?name=' + urllib.parse.quote(ptoks[0]+' '+ptoks[-1]) + ('&citystatezip='+zip5 if zip5 else '')) if len(ptoks)>=2 and not is_co else ''
    try:
        import foreclosure_leads as F
        peopleaddr = F.people_addr_url(mail_s and (mail.get('Address1','')+', '+str(mail.get('City',''))+', '+str(mail.get('State',''))+' '+str(mail.get('ZipCode',''))) or '', site_s+', MIAMI, FL '+zip5, is_co)
    except Exception:
        peopleaddr = ''

    links = [
        ('Appraiser', f'https://apps.miamidadepa.gov/PropertySearch/#/?folio={folio}'),
        ('Taxes', f'https://miamidade.county-taxes.com/public/real_estate/parcels/{folio}'),
        ('Zillow', 'https://www.zillow.com/homes/' + urllib.parse.quote(f'{site_s} MIAMI FL' if site_s else fol_fmt) + '_rb/'),
        ('Maps', 'https://www.google.com/maps/search/' + urllib.parse.quote(site_s + ', Miami, FL')),
    ]
    if people: links.append(('People (name)', people))
    if peopleaddr: links.append(('People (address)', peopleaddr))
    links.append(('Court cases (paste owner)', 'https://www2.miamidadeclerk.gov/ocs/'))
    links.append(('Official Records (paste owner)', OR_BASE))

    def row(k, v): return f'<div class="kv"><span>{esc(k)}</span><b>{v}</b></div>'
    prop = ''.join([
        row('Owner(s)', esc('; '.join(owners) or '—') + (' <span class="chip">COMPANY</span>' if is_co else '')),
        row('Site', esc(site_s or '—')), row('Mailing', esc(mail_s or '—') + (' <span class="chip warn">ABSENTEE?</span>' if mail_s and site_s and mail_s.split()[0] != site_s.split()[0] else '')),
        row('Folio', fol_fmt),
        row('Type', esc(pi.get('DORDescription','')) + (' <span class="chip">CONDO</span>' if condo else '')),
        row('Bed / Bath / SqFt', f"{pi.get('BedroomCount','?')} / {pi.get('BathroomCount','?')} / {pi.get('BuildingHeatedArea','?')}"),
        row('Built / Lot', f"{pi.get('YearBuilt','?')} / {pi.get('LotSize','?')} sqft" + (' · POOL' if pool else '')),
        row('Homestead', 'YES' if hs else 'no'),
    ])
    vals = ''.join(row(str(a.get('Year','')), f"market {money(num(a.get('TotalValue')))} · assessed {money(num(a.get('AssessedValue')))}") for a in assess[:3])
    saleh = ''.join(row(str(s.get('DateOfSale','')), money(num(s.get('SalePrice')))) for s in sales[:6] if num(s.get('SalePrice'))>0) or '<div class="kv"><span>—</span><b>no priced sales on record</b></div>'

    if lead:
        td = (lead.get('sale_type') == 'TD')
        fc = ''.join([
            row('Status', '<span class="chip hot">' + ('TAX-DEED SALE' if td else 'FORECLOSURE') + '</span> ' + esc(lead.get('case_type',''))),
            row('Case', esc(lead.get('Case #',''))),
            row('Auction', esc(lead.get('AuctionDate','')) + f" ({lead.get('days_to_auction','?')} days)"),
            row('Judgment / Opening bid', money(num(lead.get('judgment'))) if not td else money(num(lead.get('opening_bid')))),
            row('Plaintiff', esc(lead.get('plaintiff','') or '—')),
            row('Also named', esc(lead.get('defendants','') or '—')),
        ])
        if lead.get('docket_url'): links.insert(0, ('Docket (this case)', lead['docket_url']))
        if lead.get('auction_url'): links.insert(1, ('Auction page', lead['auction_url']))
    else:
        fc = '<div class="kv"><span>DEALFLOW leads</span><b>not on the current auction list (no pending FC/TD sale found in the tracker data)</b></div>'

    if orrows is not None:
        trs = ''.join(f"<tr><td>{esc(r['date'])}</td><td>{esc(r['type'])}</td><td>{money(r['amt']) if r['amt'] else ''}</td>"
                      f"<td>{esc(r['party'][:34])}</td><td>{esc(r['case'])}</td><td class=\"{ 'ok' if r['status'].startswith('SATISFIED') else ('bad' if r['status'] else '')}\">{esc(r['status'])}</td></tr>"
                      for r in orrows)
        orhtml = (f'<table><tr><th>Recorded</th><th>Document</th><th>Amount</th><th>Counter-party</th><th>Case</th><th>Status</th></tr>{trs}</table>'
                  f'<div class="note">OPEN mortgage originals total <b>{money(oropen)}</b> — original loan amounts (from intangible tax), NOT payoffs. '
                  f'Real payoffs need lender letters; amounts shrink with principal paid and grow with arrears.</div>')
    else:
        orhtml = (f'<div class="note warn">Automated search blocked by the county bot-check ({esc(orfail)}). '
                  f'Open <a href="{OR_BASE}" target="_blank">Official Records</a>, search Name/Document for '
                  f'<b>{esc(owner_query)}</b>, and look for MORTGAGE (MOR) docs with no matching SATISFACTION (SMO).</div>')

    payoff_default = int(oropen) if oropen else (int(num(lead.get('judgment'))) if lead else 0)
    linkhtml = ' '.join(f'<a class="lnk" href="{esc(u)}" target="_blank">{esc(t)}</a>' for t, u in links)
    title = esc(site_s or fol_fmt)
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lookup — {title}</title><style>
body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#0d1830;color:#e8edf7;margin:0;padding:28px 18px 60px;line-height:1.45}}
.wrap{{max-width:860px;margin:0 auto}} h1{{font-size:21px;margin:0}} .sub{{color:#8fa1c5;font-size:13px;margin:2px 0 18px}}
.card{{background:#132244;border:1px solid #24365f;border-radius:12px;padding:14px 16px;margin:12px 0}}
.sec{{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#c9a44a;font-weight:800;margin:0 0 8px}}
.kv{{display:flex;justify-content:space-between;gap:14px;padding:4px 0;border-bottom:1px solid #1b2c52;font-size:14px}}
.kv span{{color:#8fa1c5}} .kv b{{text-align:right}}
.chip{{background:#24365f;border-radius:5px;padding:1px 7px;font-size:11px;font-weight:800;vertical-align:1px}}
.chip.hot{{background:#b4402e;color:#fff}} .chip.warn{{background:#b7791f;color:#fff}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}} th{{text-align:left;color:#8fa1c5;font-weight:700;padding:4px 6px;border-bottom:1px solid #24365f}}
td{{padding:4px 6px;border-bottom:1px solid #1b2c52}} td.ok{{color:#4ade80}} td.bad{{color:#fbbf24;font-weight:700}}
.note{{font-size:12.5px;color:#8fa1c5;margin-top:8px}} .note.warn{{color:#fbbf24}} .note a{{color:#c9a44a}}
.lnk{{display:inline-block;background:#1b2c52;border:1px solid #2c4272;color:#dbe4f5;text-decoration:none;border-radius:7px;padding:6px 11px;font-size:13px;margin:3px 4px 3px 0}}
.calc label{{display:block;font-size:13px;margin:7px 0 2px;color:#8fa1c5}} .calc input{{width:130px;background:#0d1830;border:1px solid #2c4272;color:#fff;border-radius:6px;padding:6px 8px;font-size:14px}}
.calc .out{{font-size:15px;margin-top:10px}} .calc .out b{{color:#4ade80;font-size:20px}}
</style></head><body><div class="wrap">
<h1>{title}</h1><div class="sub">DEALFLOW property lookup · generated {time.strftime('%Y-%m-%d %H:%M')} · county data — verify anything load-bearing on the linked sources</div>
<div class="card"><div class="sec">Property</div>{prop}</div>
<div class="card"><div class="sec">Value (Property Appraiser)</div>{vals}<div class="sec" style="margin-top:12px">Sales history</div>{saleh}</div>
<div class="card"><div class="sec">Foreclosure / auction status</div>{fc}</div>
<div class="card"><div class="sec">Recorded mortgages &amp; liens (owner name search)</div>{orhtml}</div>
<div class="card calc"><div class="sec">Seller-net calculator (the talk-them-off-the-price math)</div>
<label>List / sale price $</label><input id="lp" type="number" value="{int(val) or ''}">
<label>Commission %</label><input id="cm" type="number" value="6" step="0.5">
<label>Closing costs %</label><input id="cc" type="number" value="2" step="0.5">
<label>Total payoff (mortgages + liens + HOA) $</label><input id="po" type="number" value="{payoff_default}">
<div class="out">Seller walks with ≈ <b id="net">—</b></div>
<div class="note">PA market value: {money(val)}. Payoff prefill = {'open recorded mortgage originals' if oropen else ('foreclosure judgment' if lead else 'unknown — enter it')}; replace with real payoff letters + the HOA estoppel.</div>
<script>function rc(){{const lp=+document.getElementById('lp').value||0,cm=+document.getElementById('cm').value||0,cc=+document.getElementById('cc').value||0,po=+document.getElementById('po').value||0;document.getElementById('net').textContent=(lp-lp*cm/100-lp*cc/100-po).toLocaleString('en-US',{{style:'currency',currency:'USD',maximumFractionDigits:0}});}}document.querySelectorAll('.calc input').forEach(i=>i.addEventListener('input',rc));rc();</script></div>
<div class="card"><div class="sec">Dig deeper</div>{linkhtml}</div>
</div></body></html>"""
    return html, site_s or fol_fmt

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('query', nargs='+', help='street address (PA style, e.g. 1760 NE 160 ST) or 13-digit folio')
    ap.add_argument('--unit', default='', help='unit/apt for condos')
    ap.add_argument('--no-records', action='store_true', help='skip the (slow) Official Records pull')
    a = ap.parse_args()
    q = ' '.join(a.query).strip()
    folio, _hit = resolve_folio(q, a.unit)
    print(f"folio {folio} — pulling the Property Appraiser record...")
    d = pa_record(folio)
    lead = leads_hit(folio)
    print("  leads cross-check:", (lead.get('Case #') + ' (' + str(lead.get('AuctionDate','')) + ')') if lead else 'not on the auction list')
    owners = [o.get('Name','') for o in (d.get('OwnerInfos') or []) if o.get('Name')]
    orrows = oropen = None; orfail = 'skipped'; owner_query = ''
    toks = [t.strip('.') for t in re.sub(r'\s*&\s*[WH]\b.*$','', owners[0] if owners else '', flags=re.I).split() if len(t.strip('.'))>1]
    if len(toks) >= 2 and owners and not COMPANY_RE.search(owners[0]):
        owner_query = ' '.join(toks[1:]) + ' ' + toks[0]              # LAST FIRST
        if not a.no_records:
            print(f"  Official Records search: {owner_query} (best-effort, ~30-90s)...")
            models, orfail = or_chain([' '.join(toks[1:]), toks[0]])
            if models is not None:
                orrows, oropen = mortgage_table(models, folio)
                print(f"  {len(orrows)} recorded docs · open mortgage originals ≈ {money(oropen)}")
            else:
                print(f"  blocked: {orfail} (report includes the manual link)")
    html, name = build_report(folio, d, lead, orrows, oropen or 0, orfail, owner_query)
    os.makedirs(OUTDIR, exist_ok=True)
    safe = re.sub(r'[^A-Za-z0-9 ]+','', name).strip()[:60] or folio
    out = os.path.join(OUTDIR, f"{safe}.html")
    open(out, 'w', encoding='utf-8').write(html)
    print(f"\nreport -> {out}")
    try: os.startfile(out)
    except Exception: webbrowser.open('file:///' + out.replace('\\','/'))

if __name__ == '__main__':
    main()
