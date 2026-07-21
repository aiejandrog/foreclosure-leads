"""Pillar 2 (roadmap to 9) — RESILIENCE. Tell us when DEALFLOW breaks instead of it rotting silently.

Run standalone (`python healthcheck.py`) or from refresh-dealflow.bat after a build. It checks the two
things that fail quietly: (1) the data we shipped is sane, and (2) every upstream source is still alive.
Prints a PASS/WARN/FAIL report, writes health.json (baked into the site header), exits non-zero on any FAIL.
"""
import json, os, re, sys, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
R = []   # (level, name, detail)   level: PASS | WARN | FAIL

def add(level, name, detail=''):
    R.append((level, name, detail))

def load(fn):
    p = os.path.join(HERE, fn)
    if not os.path.exists(p): return None
    try: return json.load(open(p, encoding='utf-8'))
    except Exception: return None

def ping(name, fn):
    t = time.time()
    try:
        ok, note = fn()
        ms = int((time.time() - t) * 1000)
        add('PASS' if ok else 'FAIL', name, f'{note} ({ms}ms)')
    except Exception as e:
        add('FAIL', name, f'{str(e)[:60]}')

# ---- 1. the data we shipped -------------------------------------------------------------------
leads = load('leads_final.json')
if not leads:
    add('FAIL', 'leads_final.json', 'missing or unreadable — the site cannot be built')
else:
    n = len(leads)
    add('FAIL' if n < 20 else 'PASS', 'lead count', f'{n} leads')
    tiers = {t: sum(1 for r in leads if r.get('tier') == t) for t in ('A', 'B', 'C')}
    add('PASS', 'tier split', f"A={tiers['A']} B={tiers['B']} C={tiers['C']}")
    withval = sum(1 for r in leads if (r.get('market_value') or 0) > 0)
    pct = round(withval / n * 100)
    add('FAIL' if pct < 55 else 'WARN' if pct < 75 else 'PASS', 'enriched (has value)', f'{pct}% ({withval}/{n})')
    fc = [r for r in leads if r.get('sale_type') != 'TD']
    withpl = sum(1 for r in fc if (r.get('plaintiff') or '').strip())
    add('WARN' if fc and withpl / len(fc) < 0.7 else 'PASS', 'FC case data (plaintiff)',
        f'{round(withpl/len(fc)*100) if fc else 0}% of FC')
    soon = sum(1 for r in leads if 0 <= (r.get('days_to_auction') or -1) <= 45)
    add('PASS', 'auctions within 45d', f'{soon} leads')

# ---- 2. enrichment coverage (the value-adds) --------------------------------------------------
liens = load('records_liens.json') or {}
if liens:
    ok = [v for v in liens.values() if v.get('conf') == 'ok']
    hits = [v for v in ok if v.get('open_count', 0) >= 2]
    cov = round(len(liens) / len(leads) * 100) if leads else 0
    add('PASS', 'recorded-lien coverage', f'{len(liens)} traced ({cov}%), {len(ok)} confident, {len(hits)} surviving-2nd')
else:
    add('WARN', 'recorded-lien coverage', 'none yet — run records_liens.py')
phones = load('skiptrace_results.json') or {}
add('PASS' if phones else 'WARN', 'skip-trace coverage', f'{len(phones)} leads with phones')

# ---- 2b. RETROACTIVITY WATCHDOG (2026-07-20) --------------------------------------------------
# Every enrichment "rule" must keep applying to future scrapes, not just today's. If a pipeline
# step silently breaks, its coverage on the merged board crashes toward 0 — this catches that and
# turns the daily workflow RED (which emails the owner) BEFORE the site quietly loses the feature.
# Floors sit well under the achievable rate so normal day-to-day variance never false-alarms; a
# real break (a step that stopped running / a source that changed shape) trips them. Uses the same
# merged board make_tracker publishes, so it measures what actually reaches the site.
def _all_leads():
    out = list(leads or [])
    for fn in ('broward_leads.json', 'palmbeach_leads.json'):
        d = load(fn)
        if isinstance(d, list):
            out += d
    return out

def _pct(hits, tot):
    return round(hits / tot * 100) if tot else 0

def _rule(name, pct, floor, detail):
    # TWO-TIER teeth (the watchdog used to WARN only — the workflow stayed green and NOBODY got
    # emailed, i.e. exactly the silent failure this section exists to catch). Below HALF the floor
    # = the enrichment step died = FAIL -> non-zero exit -> red workflow -> GitHub's failure email.
    # Between half-floor and floor = a dip worth seeing in the log, not worth a 2am page.
    lvl = 'FAIL' if pct < floor / 2 else ('WARN' if pct < floor else 'PASS')
    add(lvl, name, f'{detail} (floor {floor}%, page under {floor // 2}%)')

_ALL = _all_leads()
if _ALL:
    N = len(_ALL)
    # property type (dor_desc) — MD via PA, BW/PB via property_types.py
    dor = _pct(sum(1 for r in _ALL if (r.get('dor_desc') or '').strip()), N)
    _rule('RULE: property-type coverage', dor, 40, f'{dor}% carry dor_desc')
    # listing status (zstatus) — listing_status.py; should be near-total since NO-ADDR counts
    zst = _pct(sum(1 for r in _ALL if (r.get('zstatus') or '').strip()), N)
    _rule('RULE: listing-status coverage', zst, 70, f'{zst}% carry zstatus')
    # ARV comps — comps.py (all 3 counties); comps.json is the source of truth
    comps = load('comps.json') or {}
    arv = _pct(sum(1 for r in _ALL if comps.get(r.get('case') or r.get('Case #'))), N)
    _rule('RULE: ARV-comp coverage', arv, 30, f'{arv}% have comps')
    # sale-history survival count — sale_history.py (Miami-Dade docket). Measured against MD leads only
    # (BW/PB use the filing-year proxy), so a drop toward 0 means the OCS docket enrich stopped running.
    md = [r for r in _ALL if re.match(r'\d{4}-\d+-\w+-\d+', str(r.get('Case #') or r.get('case') or '')) and (r.get('sale_type') or r.get('st')) != 'TD']
    surv = _pct(sum(1 for r in md if r.get('saleSurv') is not None or r.get('sale_survived') is not None), len(md))
    _rule('RULE: sale-history coverage (MD)', surv, 60, f'{surv}% of MD FC leads scored')
    # per-parcel tax deep-link — county_leads.py / foreclosure_leads.py from the folio.
    # MD raw leads carry it as tax_url, county files as tax — check both so the measure is honest.
    def _deep(r):
        t = r.get('tax') or r.get('tax_url') or ''
        return '/parcels/' in t or 'ParcelID' in t
    withfolio = [r for r in _ALL if (r.get('folio') or r.get('Folio'))]
    tax = _pct(sum(1 for r in withfolio if _deep(r)), len(withfolio))
    _rule('RULE: tax deep-link coverage', tax, 55, f'{tax}% of folio leads')

# ---- 3. upstream sources still alive ----------------------------------------------------------
def chk_gis():
    r = requests.get('https://gisweb.miamidade.gov/arcgis/rest/services/MD_ComparableSales/MapServer/5/query',
                     params={'where': "FOLIO='0142060580800'", 'outFields': 'FOLIO', 'returnGeometry': 'false', 'f': 'json'},
                     headers={'User-Agent': UA}, timeout=20)
    j = r.json(); return (bool(j.get('features')), 'property lookup live')
def chk_pa():
    r = requests.get('https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx',
                     params={'Operation': 'GetPropertySearchByFolio', 'clientAppName': 'PropertySearch', 'folioNumber': '0142060580800'},
                     headers={'User-Agent': UA}, timeout=20)
    return (r.status_code == 200 and 'PropertyInfo' in r.text, 'appraiser API 200')
def chk_clerk():
    r = requests.get('https://www2.miamidadeclerk.gov/ocs/api/CaseInfo/encrypt/2024-023366-CA-01',
                     headers={'User-Agent': UA, 'Referer': 'https://www2.miamidadeclerk.gov/ocs/'}, timeout=20)
    return (r.status_code == 200 and r.json().get('qs'), 'court OCS API live')
def chk_rf():
    r = requests.get('https://www.miamidade.realforeclose.com/index.cfm', headers={'User-Agent': UA}, timeout=20)
    return (r.status_code == 200, f'auction site {r.status_code}')
ping('source · PA GIS (lookup)', chk_gis)
ping('source · Property Appraiser', chk_pa)
ping('source · Clerk OCS (cases)', chk_clerk)
ping('source · RealForeclose (scrape)', chk_rf)

# ---- 4. shipped site freshness ----------------------------------------------------------------
docs = os.path.join(HERE, 'docs', 'index.html')
if os.path.exists(docs):
    age_h = (time.time() - os.path.getmtime(docs)) / 3600
    txt = open(docs, encoding='utf-8', errors='ignore').read(4000)
    enc = 'enc' in txt[:2000] or 'gatepw' in open(docs, encoding='utf-8', errors='ignore').read()[:20000]
    add('WARN' if age_h > 24 * 8 else 'PASS', 'site freshness', f'built {age_h:.0f}h ago')
else:
    add('FAIL', 'docs/index.html', 'not built')

# ---- report + health.json ---------------------------------------------------------------------
fails = [x for x in R if x[0] == 'FAIL']; warns = [x for x in R if x[0] == 'WARN']
icon = {'PASS': 'ok  ', 'WARN': 'WARN', 'FAIL': 'FAIL'}
print(f"\n=== DEALFLOW health · {time.strftime('%Y-%m-%d %H:%M')} ===")
for lvl, name, detail in R:
    print(f"  [{icon[lvl]}] {name:32} {detail}")
status = 'DOWN' if fails else ('DEGRADED' if warns else 'HEALTHY')
print(f"\n  STATUS: {status}   ({len(R)-len(fails)-len(warns)} ok · {len(warns)} warn · {len(fails)} fail)")
json.dump({'status': status, 'checked': time.strftime('%Y-%m-%d %H:%M'),
           'checks': [{'level': l, 'name': n, 'detail': d} for l, n, d in R],
           'sources_ok': sum(1 for l, n, d in R if n.startswith('source') and l == 'PASS'),
           'sources_total': sum(1 for l, n, d in R if n.startswith('source'))},
          open(os.path.join(HERE, 'health.json'), 'w', encoding='utf-8'), indent=1)
sys.exit(1 if fails else 0)
