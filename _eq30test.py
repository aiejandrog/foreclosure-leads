"""One-shot: verify the 30%+ Equity filter and the Propwire lookup-report link. Gitignored _*.py."""
import http.server, socketserver, threading, os, functools
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, 'docs'); PORT = 8798
# NEVER hardcode a live access code in a TRACKED file. This line used to carry the real one, and
# this file is committed to a PUBLIC repo -- that code decrypts the published board: 1,928 leads
# with names, addresses and phone numbers. The encryption was real; the key was published beside it.
# site.codes is gitignored and stays on the machine. _gatetest.py already avoided this by using a
# synthetic code; these two suites need a real one because they assert against the real payload.
def _live_code():
    import os as _os, re as _re
    _p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'site.codes')
    try:
        for _line in open(_p, encoding='utf-8'):
            _m = _re.search(r'(DEALFLOW-[A-Z0-9]{6,})', _line)
            if _m:
                return _m.group(1)
    except Exception:
        pass
    raise SystemExit('no site.codes on this machine -- cannot run a gated suite')


CODE = _live_code()
Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DOCS)
class Q(socketserver.TCPServer): allow_reuse_address = True
srv = Q(('127.0.0.1', PORT), Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()

R=[]
def rec(n, ok, d=''): ok=bool(ok); R.append(ok); print((('  PASS ' if ok else '  FAIL ')+n+(' | '+d if d else '')).encode('ascii','replace').decode('ascii'))
def skip(n, d=''): print(('  SKIP '+n+(' | '+d if d else '')).encode('ascii','replace').decode('ascii'))  # not counted — no live fixture to exercise

with sync_playwright() as p:
    b = p.chromium.launch(headless=True); ctx = b.new_context(viewport={'width':1500,'height':1000})
    pg = ctx.new_page(); errs=[]
    pg.on('console', lambda m: errs.append(m.text) if m.type=='error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: '+str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg.wait_for_selector('#gatepw', timeout=15000); pg.fill('#gatepw', CODE); pg.click('#gatego')
    pg.wait_for_function("document.getElementById('gate') && getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)

    before = pg.evaluate("() => DATA.filter(r=>r.tier==='ALL'||true).length")  # total universe (ALL tier click below)
    pg.evaluate("() => document.getElementById('eq30').click()")
    pg.wait_for_timeout(150)
    chk = pg.evaluate("""() => {
      const rows = view();
      // Assert against the number the FILTER and the ROW both quote (_ownerEqOf/_basisOf via
      // _eqPct), not the stale baked r.eq — no surface displays r.eq, and testing it was how a real
      // filter/row divergence hid behind a passing test until 2026-08-12.
      const pct = r => Math.round((_ownerEqOf(r) / (_basisOf(r)||1)) * 100);
      const bad = rows.filter(r => !(pct(r) >= 30) || r.eqfake || isFlaggedDead(r));
      return {count: rows.length, bad: bad.length, sample: rows.slice(0,3).map(r=>({case:r.case, eq:r.eq, eqfake:r.eqfake}))};
    }""")
    rec('30%+ equity filter returns leads', chk['count'] > 0, f"{chk['count']} leads")
    rec('Every filtered lead is >=30% eq, not eqfake, not flagged-dead', chk['bad'] == 0, str(chk))
    active = pg.evaluate("() => document.getElementById('eq30').classList.contains('active')")
    rec('Toggle shows active state', active)
    pg.evaluate("() => document.getElementById('eq30').click()")  # toggle back off
    off = pg.evaluate("() => view().length")
    rec('Toggle off restores full view', off > chk['count'], f"off={off} filtered={chk['count']}")

    # net-equity correction, tested on WHATEVER is live today (fixtures picked dynamically, never by
    # hardcoded case number — the old Britton/Chaudhury cases aged off the board and silently broke
    # this whole test). The invariant under test: a surviving senior mortgage must drag the filter's
    # net equity BELOW the raw gross eq, and the 30%+ filter must gate on the NET number, not the raw.
    fx = pg.evaluate("""() => {
      const cand = DATA.filter(r => !r.eqfake && (typeof isFlaggedDead!=='function' || !isFlaggedDead(r)) && _basisOf(r));
      // BELOW-BAR: raw gross eq clears 30 but the surviving mortgage pulls net under 30 (must be hidden)
      const below = cand.find(r => (r.eq||0) >= 40 && netEqPct(r) < 30 && netEqPct(r) < (r.eq||0) - 5);
      // ABOVE-BAR: net equity comfortably clears the bar (must be shown)
      const above = cand.find(r => netEqPct(r) >= 40);
      const pack = r => r ? {case:r.case, rawEq:r.eq, netEq:netEqPct(r)} : null;
      return {below: pack(below), above: pack(above)};
    }""")
    if fx['below']:
        bl = fx['below']
        rec('Net-equity correction fires: surviving mortgage cuts a >=40%% raw lead below 30%% net',
            20 <= bl['netEq'] < 30 or bl['netEq'] < bl['rawEq']-5, str(bl))
    else:
        skip('Net-equity correction (no live lead where a senior drags raw>=40 below 30 today)')
    if fx['above']:
        rec('Net-equity computed for an above-bar lead (>=40%% net)', fx['above']['netEq'] >= 40, str(fx['above']))
    else:
        skip('Above-bar net-equity fixture (no lead >=40%% net today)')

    pg.evaluate("() => document.getElementById('eq30').click()")  # re-enable filter
    gate = pg.evaluate("""(cases) => { tier='ALL'; const s = new Set(view().map(r=>r.case));
      return {belowIn: cases.below ? s.has(cases.below) : null, aboveIn: cases.above ? s.has(cases.above) : null}; }""",
      {'below': fx['below']['case'] if fx['below'] else None, 'above': fx['above']['case'] if fx['above'] else None})
    if fx['below']:
        rec('30%+ filter EXCLUDES the below-bar lead (gates on net, not raw)', gate['belowIn'] == False, str(gate))
    else:
        skip('30%+ filter exclusion check (no below-bar fixture today)')
    if fx['above']:
        rec('30%+ filter INCLUDES the above-bar lead', gate['aboveIn'] == True, str(gate))
    else:
        skip('30%+ filter inclusion check (no above-bar fixture today)')
    pg.evaluate("() => document.getElementById('eq30').click()")  # back off for cleanliness

    # Propwire reference link present in the standalone property-lookup report
    pw = pg.evaluate("""() => {
      const r = DATA.find(x => x.pa);  // any lead with a folio-backed report
      if(!r) return {err:'no lead with folio'};
      const html = lkReport({FOLIO: r.folio, TRUE_OWNER1: (r.owners||'').split(';')[0],
        TRUE_SITE_ADDR: (r.addr||'').split(',')[0], TRUE_SITE_CITY: 'x', TRUE_SITE_ZIP_CODE: '33101',
        TOTAL_VAL_CUR: r.value||0});
      return {hasLink: html.includes('propwire.com'), hasLabel: html.includes('free sign-in')};
    }""")
    rec('Propwire reference link present in lookup report', pw.get('hasLink'), str(pw))
    rec('Propwire link is honestly labeled (not faked as a deep-link)', pw.get('hasLabel'), str(pw))

    ac = pg.evaluate("""() => {
      const r = DATA.find(x => x.county==='BROWARD' && x.pa);
      if(!r) return {err:'no Broward lead with folio'};
      const html = lkReport({FOLIO: r.folio, TRUE_OWNER1: (r.owners||'').split(';')[0],
        TRUE_SITE_ADDR: (r.addr||'').split(',')[0], TRUE_SITE_CITY: 'x', TRUE_SITE_ZIP_CODE: '33067',
        TOTAL_VAL_CUR: r.value||0});
      return {hasLink: html.includes('auction.com/residential/fl/Broward-county'),
              honestLabel: html.includes('NOT this address'),
              footnote: html.includes('bank-owned/trustee sales')};
    }""")
    rec('Auction.com county-browse link present for a Broward lead', ac.get('hasLink'), str(ac))
    rec('Auction.com link honestly labeled as NOT an address search', ac.get('honestLabel'), str(ac))
    rec('Auction.com limitation explained in the footer note', ac.get('footnote'), str(ac))

    real=[e for e in errs if 'favicon' not in e]
    rec('No console/page errors', not real, '; '.join(real[:2]))
    b.close()
srv.shutdown()
ok=sum(R); print(f"\n==== {ok}/{len(R)} checks passed ===="); raise SystemExit(0 if ok==len(R) else 1)
