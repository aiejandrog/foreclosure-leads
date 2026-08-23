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

_SRC_DOWN = []          # names of sources that failed every retry — used for the systemic rule below

def ping(name, fn, tries=3):
    """Probe an upstream source. RETRIES before believing it is down, and records a single failure as
    a WARN rather than a FAIL.

    A county website blipping for 400ms at 6am is not a reason to fail the nightly build and email
    the owner — and doing so is precisely what trained the owner to ignore the emails while a real
    week-long outage went unnoticed. One source down is noise; TWO OR MORE down at once is a real
    signal (egress blocked, DNS, a shared CDN) and only that escalates to FAIL, below."""
    t = time.time(); last = ''
    for i in range(tries):
        try:
            ok, note = fn()
            if ok:
                add('PASS', name, f'{note} ({int((time.time()-t)*1000)}ms)')
                return
            last = note
        except Exception as e:
            last = str(e)[:60]
        if i < tries - 1:
            time.sleep(2 * (i + 1))                       # 2s, then 4s — ride out a momentary blip
    _SRC_DOWN.append(name)
    add('WARN', name, f'{last} — unreachable after {tries} tries ({int((time.time()-t)*1000)}ms)')

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

# ---- 2. lien-chain coverage (the ONE gap the deal desk still hunts by hand) --------------------
# A lead is CHECKED when a source actually read its records (conf ok/low/bd) — NOT when it merely has
# a row (a conf='none' entry means the search failed/was blocked, so it's an UNCHECKED lead the caller
# would still have to pull by hand). Coverage is measured per county across all three feeds:
#   Miami-Dade -> records_liens.py (2Captcha/Turnstile)   Broward -> broward_liens.py (AcclaimWeb)
#   Palm Beach -> palmbeach_liens.py (Landmark, 2Captcha v2) + batchdata_liens.py legacy cache
# (2026-08-11: palmbeach_liens.json was never in this load list, so PB coverage was understated
# and the old "Palm Beach has no free path" FAIL exemption below outlived its truth.)
_md   = load('records_liens.json') or {}
_bro  = load('broward_liens.json') or {}
_pb   = load('palmbeach_liens.json') or {}
_bd   = load('batchdata_liens.json') or {}
def _checked(d):
    # ok/low/bd = priced chains. Since the 2026-08-17 PB restatement, 'unpriced' (mortgages counted,
    # amounts unpublished by Landmark) and RESTATED 'none' (searched, genuinely no mortgage found —
    # carries chain_note/mtg_recorded) are also COMPLETED checks: the records were read and the
    # caller does not need to hand-pull the county. Bare legacy 'none' (no restatement marks) still
    # means failed/blocked and stays unchecked.
    out = set()
    for c, v in d.items():
        conf = v.get('conf')
        if conf in ('ok', 'low', 'bd', 'unpriced'):
            out.add(c)
        elif conf == 'none' and ('chain_note' in v or 'mtg_recorded' in v):
            out.add(c)
    return out
_chk = _checked(_md) | _checked(_bro) | _checked(_pb) | _checked(_bd)
_cty_tot, _cty_cov = {}, {}
for _fn, _ck in (('leads_final.json', 'Case #'), ('broward_leads.json', 'case'), ('palmbeach_leads.json', 'case')):
    for _r in (load(_fn) or []):
        _case = str(_r.get(_ck) or '')
        if not _case:
            continue
        _cy = (_r.get('county') or 'MIAMI-DADE').upper().split()[0]   # MIAMI / BROWARD / PALM
        _cty_tot[_cy] = _cty_tot.get(_cy, 0) + 1
        if _case in _chk:
            _cty_cov[_cy] = _cty_cov.get(_cy, 0) + 1
if _cty_tot:
    # COLD-CACHE GUARD. Every lien cache here is gitignored and lives only in the CI cache, and CI has
    # no 2Captcha key, so it cannot regenerate Miami-Dade chains itself. When the cache misses (evicted
    # after 7 idle days, or orphaned because the actions/cache path list changed and moved the version
    # hash), EVERY county reads 0 — which is an infrastructure condition, not a data regression.
    # Failing there used to fail the job, which SKIPPED the cache save, which guaranteed the next run
    # missed too: a spiral that ran 2026-07-20 -> 07-26 and could not self-heal. Warn loudly, stay
    # green, let the save step re-seed the cache, and let the next run come back clean.
    _cold = (sum(_cty_cov.values()) == 0)
    if _cold:
        add('WARN', 'lien cache', 'COLD — no chains restored (cache miss/evicted). '
                                  'Re-seeds from this run; next run should read normally.')
    for _cy in sorted(_cty_tot):
        _t, _c = _cty_tot[_cy], _cty_cov.get(_cy, 0)
        _pct = round(100 * _c / _t) if _t else 0
        # PALM's old FAIL exemption is retired: palmbeach_liens.py exists, so a low % there is a
        # scraper/coverage problem like any other county, not a funding call.
        _lvl = 'PASS' if _pct >= 60 else ('WARN' if _pct >= 25 or _cold else 'FAIL')
        _tail = ' — run palmbeach_liens.py to lift' if _cy == 'PALM' and _pct < 60 else ''
        if _cold: _tail = ' — cold cache, not a coverage regression'
        add(_lvl, f'lien coverage · {_cy}', f'{_c}/{_t} checked ({_pct}%){_tail}')
    _surv2 = sum(1 for v in list(_md.values()) + list(_bro.values()) if v.get('open_count', 0) >= 2)
    add('PASS', 'surviving-2nd flags', f'{len(_chk)} leads checked total, {_surv2} with a possible surviving 2nd')
else:
    # No lead files at all — nothing was checkable. (This clause sat orphaned on the LP-freshness
    # try/except below for a while, firing a bogus WARN on every CLEAN run instead.)
    add('WARN', 'recorded-lien coverage', 'none yet — run records_liens.py / broward_liens.py')

# ---- 2b. LIS PENDENS FRESHNESS (the alarm that was missing twice) ------------------------------
# The LP sweeper died silently at the Turnstile migration and NOTHING noticed for 33 days — the
# "just filed" lane ran on month-old filings, printed as present-tense fact in door books. New
# filings land every business day in a county this size, so a stale newest-date is never normal:
#   > 7 days  -> FAIL (sweeper is dead or blocked; the pre-foreclosure lane is lying about itself)
#   > 3 days  -> WARN (long weekend tolerance)
# Weekends don't excuse 7 days. If lis_pendens.json is missing entirely that's the same FAIL.
try:
    import datetime as _dt
    _lp = load('lis_pendens.json') or []
    _lpd = []
    for _x in (_lp if isinstance(_lp, list) else []):
        try:
            _m, _d2, _y = str(_x.get('date') or '').split('/')
            _lpd.append(_dt.date(int(_y), int(_m), int(_d2)))
        except Exception:
            pass
    if not _lpd:
        add('FAIL', 'LP freshness', 'lis_pendens.json missing/empty/undated — sweep never ran')
    else:
        _age = (_dt.date.today() - max(_lpd)).days
        _lvl = 'FAIL' if _age > 7 else ('WARN' if _age > 3 else 'PASS')
        add(_lvl, 'LP freshness', f'newest filing {max(_lpd).isoformat()} ({_age}d old, {len(_lp)} records)'
            + (' — run lis_pendens.py, the sweeper is stale' if _lvl != 'PASS' else ''))
except Exception as _e:
    add('WARN', 'LP freshness', f'check errored: {str(_e)[:80]}')

# ---- 2c. SKIPTRACE PIPELINE (caught late on 2026-08-17: the run stopped at 95/100 on the wrong
# budget cap and only the operator noticed, days of "feels stale" later). Two silent failure modes:
# the nightly stops running at all, or it stops EARLY mid-run (budget/wall) while still exiting
# in a way the bat ignores. Both must surface here, not in a 1MB log. --------------------------------
try:
    import datetime as _dt2
    _str = load('skiptrace_results.json') or {}
    _tds = []
    for _v in _str.values():
        try:
            _tds.append(_dt2.date.fromisoformat(str(_v.get('traced') or '')))
        except Exception:
            pass
    if _tds:
        _sage = (_dt2.date.today() - max(_tds)).days
        _slvl = 'FAIL' if _sage > 4 else ('WARN' if _sage > 2 else 'PASS')
        add(_slvl, 'skiptrace freshness', f'newest trace {max(_tds).isoformat()} ({_sage}d old, {len(_str)} cached)'
            + (' — the nightly skiptrace is not running' if _slvl != 'PASS' else ''))
    else:
        add('WARN', 'skiptrace freshness', 'no dated traces in skiptrace_results.json')
    _rl = os.path.join(HERE, 'leads-run.log')
    if os.path.exists(_rl):
        _log = open(_rl, encoding='utf-8', errors='replace').read()
        _cut = _log.rfind('REFRESH ')                     # most recent run only — old stops are history
        _tail = _log[_cut:] if _cut >= 0 else _log[-120000:]
        if 'DAILY BUDGET REACHED' in _tail:
            add('WARN', 'skiptrace early stop', 'last run stopped mid-queue on a budget cap — '
                'leads left untraced today. Tracerfy is prepaid; raise TRACERFY_DAILY_CAP if this repeats.')
        if 'WALLED: hit the block twice' in _tail:
            add('WARN', 'TPS free path', 'truepeoplesearch is walled (stale cookies) — tracerfy covers '
                'the gap on its next run, but the free path needs a manual cookie re-export to revive.')
except Exception as _e:
    add('WARN', 'skiptrace pipeline', f'check errored: {str(_e)[:80]}')
# ---- callable-contact coverage: is there a PERSON + a number on every property? -----------------
# Two metrics: an auto-filled PHONE (the ideal), and at minimum a named PERSON to call (a human owner
# always is one; an LLC counts only once llc_officers.py resolves its Sunbiz officer — otherwise the
# row is a company shell with just the free People links). The gap the deal desk feels = shell LLCs.
import re as _re
phones = load('skiptrace_results.json') or {}
_ph_cases = {c for c, v in phones.items() if v.get('phones')}
_off = load('llc_officers.json') or {}
_off_named = {c for c, v in _off.items() if [p for p in (v.get('officers') or []) if p and p.get('n')]}
# strict corporate — a nameless shell. TRUST/ESTATE are excluded on purpose: they name a trustee in
# the owner string (e.g. "WILLIAMS, VIRGINIA TRS"), so they ARE a person to call, not a shell.
_CO = _re.compile(r'\b(LLC|CORP|INC|COMPANY|HOLDINGS|LP|LTD|GROUP|PROPERT|INVEST|REALTY|CAPITAL|VENTURES)\b', _re.I)
_cl = []
for _fn, _ck in (('leads_final.json', 'Case #'), ('broward_leads.json', 'case'), ('palmbeach_leads.json', 'case')):
    for _r in (load(_fn) or []):
        _c = str(_r.get(_ck) or '')
        if _c:
            _cl.append((_c, (_r.get('owners') or _r.get('owner') or '')))
_tot = len(_cl) or 1
_hasphone = sum(1 for _c, _o in _cl if _c in _ph_cases)
_shell = [_c for _c, _o in _cl if _CO.search(_o.split(';')[0]) and _c not in _off_named and _c not in _ph_cases]
_human = len(_cl) - len(_shell)
_pph, _phum = round(100 * _hasphone / _tot), round(100 * _human / _tot)
add('PASS' if _pph >= 60 else 'WARN', 'auto-phone coverage', f'{_hasphone}/{len(_cl)} have a dialable number ({_pph}%)')
add('PASS' if _phum >= 90 else 'WARN', 'human-contact coverage',
    f'{_human}/{len(_cl)} name a person to call ({_phum}%)' + (f'; {len(_shell)} shell LLCs left — run llc_officers.py' if _shell else ''))
# geocode coverage — lat/lng per lead is what the origin-anchored door route + Near-home filter need;
# a lead with no coordinates silently drops out of both, so watch it like the other coverages.
_geo = load('geocode_cache.json') or {}
_geoc = sum(1 for _c, _o in _cl if (_geo.get(_c) or {}).get('lat'))
_gpct = round(100 * _geoc / _tot)
add('PASS' if _gpct >= 70 else 'WARN', 'geocode coverage', f'{_geoc}/{len(_cl)} have lat/lng for map+route ({_gpct}%); run geo_enrich.py' if _gpct < 100 else f'{_geoc}/{len(_cl)} geocoded')

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
    # Redfin Estimate — redfin_value.py sidecar. rfval never lands in the lead files (it's a
    # build-time post-pass merge), so measure the CACHE against folio-carrying leads. Redfin indexes
    # fewer parcels than Zillow, so the floor is lower; WARN-tier — a blocked cloud run is expected
    # and the local nightly backfills. Denominator is leads with a resolvable folio.
    _rf = load('redfin_cache.json') or {}
    _rf_ok = {k for k, v in _rf.items() if isinstance(v, dict) and int(v.get('v') or 0) > 0}
    _folioed = [re.sub(r'\D', '', str(r.get('folio') or r.get('Folio') or '')) for r in _ALL]
    _folioed = [f for f in _folioed if f]
    rfp = _pct(sum(1 for f in _folioed if f in _rf_ok), len(_folioed))
    _rule('RULE: redfin-estimate coverage', rfp, 20, f'{rfp}% of folio leads carry a Redfin Estimate')
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
    # COMPLIANCE INTEGRITY (2026-07-21 hole): the cache knew 67 active §362 stays while a published
    # build carried ZERO — every one an outreach-enabled federal landmine. If the cache says stays
    # exist but the merged board carries none, the compliance layer got stripped somewhere between
    # cache and build. That is never a WARN.
    _shc = load('sale_history_cache.json') or {}
    cache_act = sum(1 for e in _shc.values() if isinstance(e, dict) and e.get('a'))
    lead_act = sum(1 for r in _ALL if r.get('sale_bk_active') or r.get('saleBkAct'))
    if cache_act:
        add('FAIL' if lead_act == 0 else 'PASS', 'RULE: §362 stay flags reach the build',
            f'cache {cache_act} active stays -> board {lead_act}')

# ---- 2b. ENTITY CLAIM ---------------------------------------------------------------------------
# Twice the company name was asserted to homeowners without anyone reading the register, and the
# second time it reached the PUBLIC board. entity.py now gates the suffix on a Sunbiz verdict, but
# this check deliberately does NOT trust that: it scans the BUILT ARTIFACT for the assertion.
# Auditing known code paths is what missed six leaks on 2026-08-23 -- two letterhead fallbacks, the
# quit-claim deed grantee, and a second fictitious entity on the door-step identity card. Checking
# the output cannot be fooled by a path nobody remembered.
def chk_entity():
    try:
        import entity
    except Exception as e:
        add('WARN', 'entity claim', f'entity.py unavailable ({e})')
        return
    raw = (entity.sender().get('llc') or '').strip()
    if not raw:
        add('WARN', 'entity claim', 'no company name set in sender.json')
        return
    st, ok = entity.status(), entity.verified()
    if ok:
        add('PASS', 'entity claim', f"{st.get('matched') or raw} ACTIVE doc={st.get('doc') or '?'}")
    else:
        add('WARN', 'entity claim',
            f"{raw} not verified ({st.get('status') or 'never checked'}) — suffix withheld; run entity_check.py")

    # The assertion guard. If the published board carries the full entity string while the register
    # cannot substantiate it, something bypassed entity.py — block the publish.
    idx = os.path.join(HERE, 'docs', 'index.html')
    if not ok and os.path.exists(idx):
        # FAIL-CLOSED. The first cut of this used io.open() without importing io; the NameError was
        # swallowed by a bare except and the guard reported PASS over a board that DID carry the
        # claim. A compliance guard that cannot read its artifact must never report clean.
        try:
            with open(idx, encoding='utf-8', errors='replace') as f:
                leaked = f.read().count(raw)
        except Exception as e:
            add('FAIL', 'entity claim in published board',
                f'could not read docs/index.html to check for an unsubstantiated entity claim '
                f'({type(e).__name__}: {e}) — refusing to report clean')
            return
        if leaked:
            add('FAIL', 'entity claim in published board',
                f'{leaked} occurrence(s) of "{raw}" in docs/index.html while UNVERIFIED — '
                'a surface is bypassing entity.py')
        else:
            add('PASS', 'entity claim in published board', 'no unsubstantiated entity claim')

chk_entity()

# ---- 3. upstream sources still alive ----------------------------------------------------------
def chk_gis():
    r = requests.get('https://gisweb.miamidade.gov/arcgis/rest/services/MD_ComparableSales/MapServer/5/query',
                     params={'where': "FOLIO='0142060580800'", 'outFields': 'FOLIO', 'returnGeometry': 'false', 'f': 'json'},
                     headers={'User-Agent': UA}, timeout=20)
    j = r.json()
    # Health of the SERVICE, not of one parcel. This pinned a single folio into a comparable-SALES
    # layer, whose rows roll as sales age out — so the probe was destined to start failing on a
    # perfectly healthy service. A valid ArcGIS envelope with no `error` means the endpoint is up;
    # the folio having rows today is incidental.
    if isinstance(j, dict) and j.get('error'):
        return (False, 'ArcGIS error: ' + str(j['error'])[:60])
    ok = isinstance(j, dict) and 'features' in j
    return (ok, 'property lookup live' + ('' if j.get('features') else ' (probe folio has no rows — service fine)'))
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
# SYSTEMIC RULE. One county site down after 3 tries is their problem and it self-heals; the run stays
# green. Two or more down at once means something on OUR side or the whole network path — that is
# worth failing the build and firing the email, because the next scrape will produce garbage.
if len(_SRC_DOWN) >= 2:
    add('FAIL', 'upstream sources', f'{len(_SRC_DOWN)} sources unreachable at once ({", ".join(s.split("·")[-1].strip() for s in _SRC_DOWN)}) — systemic, not a blip')

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

# TIERED EXIT (2026-08-20). A FAIL is not one thing. COMPLIANCE/systemic fails must HARD-BLOCK the
# publish — a board that lost its §362 stay flags, or built while ≥2 upstream sources were down, is
# dangerous or unreliable. COVERAGE-floor fails (value/lien/rule %) are quality metrics that a
# fresh-filing-heavy day legitimately dips below (185 of 385 MD leads had no folio on 08-20, so they
# can't be priced) — those should NOT block a build that publish_guard already proved is richer than
# live and not corrupt, or the automatic pipeline goes stale every busy day.
#   exit 2 = compliance/systemic FAIL -> caller MUST skip publish
#   exit 1 = coverage-floor FAIL only -> advisory; caller may publish if publish_guard is clean
#   exit 0 = healthy
_CRITICAL_FAIL = {'RULE: §362 stay flags reach the build', 'upstream sources',
                  'entity claim in published board'}
_crit = [n for l, n, d in R if l == 'FAIL' and n in _CRITICAL_FAIL]
if _crit:
    print(f"  !! COMPLIANCE FAIL (blocks publish): {', '.join(_crit)}")
sys.exit(2 if _crit else (1 if fails else 0))
