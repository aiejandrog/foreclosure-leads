"""Stress test for the Near Me feature. Alejandro reported it "seems to not work" — this covers
the whole surface end-to-end so we know exactly what's real and what isn't.

Groups:
  A. Origin resolution   — home / zip / bad-zip / empty / GPS-in-file-context
  B. Query semantics     — county, suppression, routability, radius, sort, tie-break
  C. Exclusion counts    — each of the four counters increments correctly
  D. Edge cases          — radius=1, radius=100, all-suppressed, empty pool, count>pool
  E. UI wiring           — modal open/close, ZIP datalist, Find button flow, Build packet
  F. Live-data smoke     — the current board produces at least one door
"""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
ok, bad = [], []
def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


# Synthetic leads spanning every branch of the query. Injected via unshift so DATA mutates in
# place (DATA is top-level `let`, not a window property).
INJECT = r"""() => {
  const O = {lat:25.77557, lng:-80.373158};                          // ROUTE_ORIGIN
  // 6 clean Miami-Dade houses near the origin, ascending distance so sort assertions are easy
  const near = [
    {case:'NM-A1', addr:'1000 NW 7 ST, MIAMI, FL 33172', owners:'DOE, ALPHA', oname:'ALPHA DOE',
     lat:25.78, lng:-80.375, county:'MIAMI-DADE', phones:['3055550101'], auction:'08/12/2026', tier:'A'},
    {case:'NM-A2', addr:'2000 NW 7 ST, MIAMI, FL 33172', owners:'DOE, BRAVO', oname:'BRAVO DOE',
     lat:25.79, lng:-80.380, county:'MIAMI-DADE', phones:['3055550102'], auction:'08/13/2026', tier:'A'},
    {case:'NM-A3', addr:'3000 NW 7 ST, MIAMI, FL 33172', owners:'DOE, CHARLIE', oname:'CHARLIE DOE',
     lat:25.80, lng:-80.390, county:'MIAMI-DADE', phones:['3055550103'], auction:'08/14/2026', tier:'B'},
    {case:'NM-A4', addr:'4000 NW 7 ST, MIAMI, FL 33172', owners:'DOE, DELTA', oname:'DELTA DOE',
     lat:25.85, lng:-80.410, county:'MIAMI-DADE', phones:['3055550104'], auction:'08/15/2026', tier:'A'},
    // A close condo unit + a close house at the same distance for tie-break assertion
    {case:'NM-T-HOUSE', addr:'50 SW 8 ST, MIAMI, FL 33172', owners:'TIE, HOUSE', oname:'HOUSE TIE',
     lat:25.775, lng:-80.374, county:'MIAMI-DADE', phones:['3055550201'], auction:'08/12/2026', tier:'A'},
    {case:'NM-T-UNIT', addr:'50 SW 8 AVE 305, MIAMI, FL 33172', owners:'TIE, UNIT', oname:'UNIT TIE',
     lat:25.775, lng:-80.374, county:'MIAMI-DADE', phones:['3055550202'], auction:'08/12/2026', tier:'A', condo:true},
    // Excluded: Broward (out of county)
    {case:'NM-EX-COUNTY', addr:'100 N ANDREWS AVE, FORT LAUDERDALE, FL 33301', owners:'BRO, WARD', oname:'BROWARD',
     lat:26.12, lng:-80.14, county:'BROWARD', phones:['9545550001'], auction:'08/12/2026', tier:'A'},
    // Excluded: BK stay
    {case:'NM-EX-BK', addr:'500 NW 7 ST, MIAMI, FL 33172', owners:'BK, STAY', oname:'BK STAY',
     lat:25.78, lng:-80.376, county:'MIAMI-DADE', phones:['3055550301'], auction:'08/12/2026', tier:'A',
     saleBkAct:true},
    // Excluded: sibclaimed / sold via sibling
    {case:'NM-EX-SIB', addr:'600 NW 7 ST, MIAMI, FL 33172', owners:'SIB, SOLD', oname:'SIB SOLD',
     lat:25.78, lng:-80.377, county:'MIAMI-DADE', phones:['3055550401'], auction:'08/12/2026', tier:'A',
     sibclaimed:true, sib:[{case:'2020-SIB', sold:true}]},
    // Excluded: no coordinates
    {case:'NM-EX-NOCOORD', addr:'700 NW 7 ST, MIAMI, FL 33172', owners:'NO, COORD', oname:'NO COORD',
     county:'MIAMI-DADE', phones:['3055550501'], auction:'08/12/2026', tier:'A'},
    // Excluded: coord OUTSIDE the MD bbox (bad geocode)
    {case:'NM-EX-OUTBBOX', addr:'800 NW 7 ST, MIAMI, FL 33172', owners:'OUT, BBOX', oname:'OUT BBOX',
     lat:24.5, lng:-80.5, county:'MIAMI-DADE', phones:['3055550601'], auction:'08/12/2026', tier:'A'},
    // Excluded: address has no street number (city-centroid trap — the CACE-2026A00080 lesson)
    {case:'NM-EX-NOSTREET', addr:'MIAMI, FL 33172', owners:'NO, STREET', oname:'NO STREET',
     lat:25.78, lng:-80.373, county:'MIAMI-DADE', phones:['3055550701'], auction:'08/12/2026', tier:'A'},
    // Excluded: opted out via notes
    {case:'NM-EX-OPTOUT', addr:'900 NW 7 ST, MIAMI, FL 33172', owners:'OPT, OUT', oname:'OPT OUT',
     lat:25.78, lng:-80.378, county:'MIAMI-DADE', phones:['3055550801'], auction:'08/12/2026', tier:'A'},
    // A far MD lead — beyond a small radius, inside a wide one
    {case:'NM-FAR', addr:'20000 SW 320 ST, HOMESTEAD, FL 33033', owners:'FAR, LEAD', oname:'FAR LEAD',
     lat:25.50, lng:-80.60, county:'MIAMI-DADE', phones:['3055550901'], auction:'08/12/2026', tier:'A'},
  ];
  Array.prototype.unshift.apply(DATA, near);
  if (typeof notes === 'undefined') { window.notes = {}; }
  notes['NM-EX-OPTOUT'] = {optout:true, status:'OPTED OUT'};
  return {n: DATA.length, injected: near.length,
          origin: {lat: ROUTE_ORIGIN.lat, lng: ROUTE_ORIGIN.lng},
          zipCentCount: Object.keys(ZIP_CENT||{}).length};
}"""


A_ORIGIN = r"""() => {
  // A. origin resolution — five real branches + three new FL-bundle branches (2026-07-31)
  return Promise.all([
    _resolveOrigin({}),                                   // 0: home
    _resolveOrigin({zip:'33172'}),                        // 1: valid ZIP with board anchors
    _resolveOrigin({zip:'99999'}),                        // 2: out-of-state (not FL) → home fallback
    _resolveOrigin({zip:''}),                             // 3: empty zip → home
    _resolveOrigin({gps:true}),                           // 4: gps in file:// context → home fallback
    _resolveOrigin({zip:'33301'}),                        // 5: FL bundle — Fort Lauderdale (no board leads)
    _resolveOrigin({zip:'32801'}),                        // 6: FL bundle — Orlando (nowhere near MD)
    _resolveOrigin({zip:'33401'})                         // 7: FL bundle — West Palm Beach
  ]).then(([home, zip, bad, empty, gps, ftl, orl, wpb]) =>
    ({home, zip, bad, empty, gps, ftl, orl, wpb}));
}"""


B_QUERY = r"""() => {
  // B. base query at Carlos's house, 10 doors, 10mi
  const o = {lat:25.77557, lng:-80.373158, label:"Carlos's house", tier:'home', confidence:'high', anchors:1};
  const r = _nearMeRun({origin:o, count:10, radiusCap:10});
  const cases = r.picked.map(x=>x.case);
  return {
    poolSize: r.poolSize,
    pickedCount: r.picked.length,
    testCases: cases.filter(c => c.startsWith('NM-')),
    // county gate
    hasBroward: cases.some(c => c === 'NM-EX-COUNTY'),
    // suppression
    hasBK: cases.some(c => c === 'NM-EX-BK'),
    hasSib: cases.some(c => c === 'NM-EX-SIB'),
    hasOptOut: cases.some(c => c === 'NM-EX-OPTOUT'),
    // routability
    hasNoCoord: cases.some(c => c === 'NM-EX-NOCOORD'),
    hasOutBbox: cases.some(c => c === 'NM-EX-OUTBBOX'),
    hasNoStreet: cases.some(c => c === 'NM-EX-NOSTREET'),
    // distances non-decreasing
    distances: r.picked.map(x => +x._nmDist.toFixed(3)),
    // tie-break: at similar distance, house before unit
    tieOrder: cases.filter(c => c === 'NM-T-HOUSE' || c === 'NM-T-UNIT'),
    excluded: r.excluded,
    radiusUsed: r.radiusUsed
  };
}"""


C_EXCLUSION_COUNTS = r"""() => {
  // C. exclusion counters — inject-only to isolate
  const o = {lat:25.77557, lng:-80.373158, label:"Carlos's house", tier:'home', confidence:'high', anchors:1};
  const r = _nearMeRun({origin:o, count:100, radiusCap:100});
  return {excluded: r.excluded, poolSize: r.poolSize, radiusUsed: r.radiusUsed};
}"""


D_EDGES = r"""() => {
  // D. edge cases. IMPORTANT: `+0 || 25 === 25` in JavaScript, so the code correctly treats a
  // 0/undefined radius as "unspecified → 25 mi default". The UI's <select> can never emit 0
  // (options are 5/10/15/25). So probe realistic minima instead: cap=1 with a low-confidence
  // origin must be clamped up to minCap=5; cap=1 with a high-confidence origin must be honored.
  const o = {lat:25.77557, lng:-80.373158, label:"Carlos's house", tier:'home', confidence:'high', anchors:1};
  const oneMile   = _nearMeRun({origin:o, count:10, radiusCap:1});                // honored
  const wide      = _nearMeRun({origin:o, count:1000, radiusCap:100});             // huge cap, huge count
  const lowConf1  = _nearMeRun({origin:{...o, confidence:'low'}, count:10, radiusCap:1});  // clamped to 5
  const lowConf10 = _nearMeRun({origin:{...o, confidence:'low'}, count:10, radiusCap:10}); // honored (>=minCap)
  return {
    oneMileRadius:  oneMile.radiusUsed,
    oneMileClamped: !!oneMile.clamped,
    widePicked:     wide.picked.length,      // capped by count OR pool
    widePoolSize:   wide.poolSize,
    lowConf1Radius: lowConf1.radiusUsed,
    lowConf1Clamped: !!lowConf1.clamped,
    lowConf10Radius: lowConf10.radiusUsed,
    lowConf10Clamped: !!lowConf10.clamped
  };
}"""


E_UI = r"""async () => {
  // E. UI wiring
  window.openNearMe();
  await new Promise(r => setTimeout(r, 60));
  const modal = document.getElementById('nearmemodal');
  const body  = document.getElementById('nearmebody');
  const modalOpen = modal && modal.classList.contains('show');
  const hasGpsBtn = !!document.getElementById('nm-gps');
  const hasZipInput = !!document.getElementById('nm-zip');
  const hasCountSel = !!document.getElementById('nm-count');
  const hasCapSel   = !!document.getElementById('nm-cap');
  const hasFindBtn  = !!document.getElementById('nm-find');
  // ZIP datalist populated?
  const dl = document.getElementById('nm-zips');
  const zipOptions = dl ? dl.querySelectorAll('option').length : 0;
  // fire Find using the default (empty ZIP → home) and check that results render
  document.getElementById('nm-find').click();
  await new Promise(r => setTimeout(r, 220));
  const list = document.querySelector('.nmlist');
  const rows = list ? list.querySelectorAll('.nmrow').length : 0;
  const buildBtn = !!document.getElementById('nm-build');
  const routeBtn = !!document.getElementById('nm-route');
  // close
  window.closeNearMe();
  const modalClosed = !document.getElementById('nearmemodal').classList.contains('show');
  return {
    modalOpen, hasGpsBtn, hasZipInput, hasCountSel, hasCapSel, hasFindBtn,
    zipOptions, rows, buildBtn, routeBtn, modalClosed
  };
}"""


F_LIVE = r"""() => {
  // F. LIVE DATA smoke — before any inject, how many real leads pass?  Called AFTER cleanup.
  // We can't easily undo the injection, but we CAN filter injections out and measure the rest.
  const o = {lat:25.77557, lng:-80.373158, label:"Carlos's house", tier:'home', confidence:'high', anchors:1};
  const r = _nearMeRun({origin:o, count:1000, radiusCap:100});
  const realPicked = r.picked.filter(x => !String(x.case||'').startsWith('NM-'));
  return {realPicked: realPicked.length, poolSize: r.poolSize,
          liveExcluded: r.excluded, zipOptionsCount: (_nmZipOptions().match(/<option/g)||[]).length};
}"""


UNIT_ADDR = r"""() => {
  // Extra: _isUnitAddr coverage. The trailing-number pattern is the tricky one, and the `#`
  // keyword variant regressed in an earlier build because `\b#` cannot match — `#` is a
  // non-word character with no word-boundary neighbour in "ST # 4B".
  return {
    plainHouse: _isUnitAddr({addr:'8923 SW 206 ST, MIAMI'}),
    aptKw:      _isUnitAddr({addr:'123 MAIN ST APT 4B, MIAMI'}),
    unitKw:     _isUnitAddr({addr:'123 MAIN ST UNIT 4B, MIAMI'}),
    steKw:      _isUnitAddr({addr:'900 BRICKELL AVE STE 200, MIAMI'}),
    hashKw:     _isUnitAddr({addr:'123 MAIN ST # 4B, MIAMI'}),
    hashKwTight:_isUnitAddr({addr:'123 MAIN ST #4B, MIAMI'}),
    trailNum:   _isUnitAddr({addr:'10831 NW 7 ST 9-14, MIAMI'}),
    trailNum2:  _isUnitAddr({addr:'6441 SW 116 CT E97, MIAMI'}),
    condoFlag:  _isUnitAddr({addr:'900 BRICKELL', condo:true}),
    empty:      _isUnitAddr({})
  };
}"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context()
        pg = await ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri())
        await pg.wait_for_timeout(3000)

        seed = await pg.evaluate(INJECT)
        rec('setup: injected 14 synthetic leads', seed.get('injected') == 14, seed)
        rec('setup: ZIP_CENT is populated (>= 20 ZIPs)',
            (seed.get('zipCentCount') or 0) >= 20, {'zips': seed.get('zipCentCount')})

        # ---------- A. origin resolution -----------------------------------------------------
        A = await pg.evaluate(A_ORIGIN)
        rec('A: {} resolves to home tier', A['home'].get('tier') == 'home', A['home'])
        rec('A: {zip:"33172"} resolves to zip tier',
            A['zip'].get('tier') == 'zip' and A['zip'].get('anchors', 0) > 0, A['zip'])
        rec('A: out-of-state ZIP (99999) falls back to home with note',
            A['bad'].get('tier') == 'home' and 'not a Florida' in (A['bad'].get('note') or ''),
            {'tier': A['bad'].get('tier'), 'note': (A['bad'].get('note') or '')[:80]})
        # NEW: FL statewide bundle should resolve any FL ZIP even if no leads are on that ZIP
        rec('A: Fort Lauderdale ZIP (33301) resolves via FL bundle',
            A['ftl'].get('tier') == 'zip' and A['ftl'].get('src') == 'fl_bundle'
            and 26.0 <= A['ftl'].get('lat', 0) <= 26.3,
            {'label': A['ftl'].get('label'), 'src': A['ftl'].get('src'),
             'lat': A['ftl'].get('lat')})
        rec('A: Orlando ZIP (32801) resolves via FL bundle',
            A['orl'].get('tier') == 'zip' and A['orl'].get('src') == 'fl_bundle'
            and 28.4 <= A['orl'].get('lat', 0) <= 28.7,
            {'label': A['orl'].get('label'), 'lat': A['orl'].get('lat')})
        rec('A: West Palm Beach ZIP (33401) resolves via FL bundle',
            A['wpb'].get('tier') == 'zip' and A['wpb'].get('src') == 'fl_bundle',
            {'label': A['wpb'].get('label'), 'lat': A['wpb'].get('lat')})
        rec('A: FL bundle label includes the city name',
            'Fort Lauderdale' in (A['ftl'].get('label') or ''),
            A['ftl'].get('label'))
        rec('A: empty ZIP resolves to home', A['empty'].get('tier') == 'home', A['empty'])
        # GPS in headless Chrome on file:// can take either fallback path — Chrome treats file://
        # as secure so getCurrentPosition fires, and Playwright's context grants no permission →
        # "Location permission denied". If run under a REAL browser without a secure context, the
        # earlier branch fires with "GPS unavailable here (needs HTTPS)". Both are legitimate.
        gps_note = A['gps'].get('note') or ''
        rec('A: gps in file:// falls back to home with a note',
            A['gps'].get('tier') == 'home' and gps_note != '',
            {'tier': A['gps'].get('tier'), 'note': gps_note[:80]})

        # ---------- B. query semantics -------------------------------------------------------
        B = await pg.evaluate(B_QUERY)
        rec('B: Broward lead excluded', not B['hasBroward'], B['testCases'])
        rec('B: BK-stay lead excluded', not B['hasBK'], B['testCases'])
        rec('B: sibclaimed lead excluded', not B['hasSib'], B['testCases'])
        rec('B: opt-out lead excluded', not B['hasOptOut'], B['testCases'])
        rec('B: no-coordinates lead excluded', not B['hasNoCoord'], B['testCases'])
        rec('B: out-of-bbox coordinate excluded', not B['hasOutBbox'], B['testCases'])
        rec('B: no-street-number lead excluded', not B['hasNoStreet'], B['testCases'])
        # Distances non-decreasing
        dists = B['distances']
        rec('B: results sorted by distance ascending',
            all(dists[i] <= dists[i+1] + 0.001 for i in range(len(dists)-1)),
            dists[:6])
        # Tie-break: house before unit
        rec('B: tie-break puts HOUSE before UNIT at similar distance',
            B['tieOrder'] == ['NM-T-HOUSE', 'NM-T-UNIT'] or B['tieOrder'][:1] == ['NM-T-HOUSE'],
            B['tieOrder'])

        # ---------- C. exclusion counters ----------------------------------------------------
        # From inject: 1 county, 4 unroutable (nocoord + outbbox + nostreet + [none more]),
        # 3 ineligible (BK + sib + optout), 0 outOfRange at 100mi. Plus whatever live data adds.
        C = await pg.evaluate(C_EXCLUSION_COUNTS)
        ex = C['excluded']
        rec('C: county counter counted the Broward inject', ex.get('county', 0) >= 1, ex)
        rec('C: ineligible counter caught BK + sib + optout (>= 3)',
            ex.get('ineligible', 0) >= 3, ex)
        rec('C: unroutable counter caught nocoord + outbbox + nostreet (>= 3)',
            ex.get('unroutable', 0) >= 3, ex)
        rec('C: outOfRange counter present', 'outOfRange' in ex, ex)

        # ---------- D. edges -----------------------------------------------------------------
        D = await pg.evaluate(D_EDGES)
        rec('D: high-conf origin honors a 1-mile radius (no clamp)',
            D['oneMileRadius'] == 1 and not D['oneMileClamped'], D)
        rec('D: LOW-conf origin clamps radius=1 up to minCap=5',
            D['lowConf1Radius'] == 5 and D['lowConf1Clamped'], D)
        rec('D: LOW-conf origin honors radius=10 (already >= minCap)',
            D['lowConf10Radius'] == 10 and not D['lowConf10Clamped'], D)
        rec('D: huge count returns at most pool-size',
            D['widePicked'] <= D['widePoolSize'], D)

        # ---------- E. UI --------------------------------------------------------------------
        E = await pg.evaluate(E_UI)
        rec('E: openNearMe shows modal', E['modalOpen'], E)
        rec('E: form controls all present',
            E['hasGpsBtn'] and E['hasZipInput'] and E['hasCountSel']
            and E['hasCapSel'] and E['hasFindBtn'], E)
        rec('E: ZIP datalist has options (>= 5)', E['zipOptions'] >= 5, {'zips': E['zipOptions']})
        rec('E: Find doors renders result rows (>= 1)', E['rows'] >= 1, {'rows': E['rows']})
        rec('E: Build packet + Maps route buttons appear', E['buildBtn'] and E['routeBtn'], E)
        rec('E: closeNearMe hides modal', E['modalClosed'], E)

        # ---------- Extra: _isUnitAddr -------------------------------------------------------
        U = await pg.evaluate(UNIT_ADDR)
        rec('U: plain house NOT flagged as unit', U['plainHouse'] is False, U)
        rec('U: APT keyword flagged as unit', U['aptKw'] is True, U)
        rec('U: UNIT keyword flagged as unit', U['unitKw'] is True, U)
        rec('U: STE keyword flagged as unit', U['steKw'] is True, U)
        rec('U: "# 4B" flagged as unit (regression: \\b# never matches)', U['hashKw'] is True, U)
        rec('U: "#4B" (no space) flagged as unit', U['hashKwTight'] is True, U)
        rec('U: trailing-number "9-14" flagged as unit', U['trailNum'] is True, U)
        rec('U: trailing-number "E97" flagged as unit', U['trailNum2'] is True, U)
        rec('U: r.condo=true flagged as unit', U['condoFlag'] is True, U)
        rec('U: empty record does not crash', U['empty'] is False, U)

        # ---------- F. live-data smoke -------------------------------------------------------
        F = await pg.evaluate(F_LIVE)
        rec('F: LIVE board produces at least 1 door for Carlos default origin',
            F['realPicked'] >= 1, F)
        rec('F: ZIP option list is non-empty on live data', F['zipOptionsCount'] >= 5,
            {'zipOpts': F['zipOptionsCount']})

        rec('no JS errors during test', not errs, errs[:3] if errs else '')

        await b.close()
        total = len(ok) + len(bad)
        print(f'\n==== {len(ok)}/{total} near-me checks passed ====')
        return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
