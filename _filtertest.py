"""Verify county+tier compose (smart filters): picking a county shows the WHOLE county (tier->ALL),
tier buttons narrow within the county, and the two never reset each other. Gitignored _*.py.
Run: python _filtertest.py
"""
import http.server, threading, os, functools
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright
import foreclosure_leads as F

HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, 'docs'); PORT = 8829
_secret = F._load_codes()[0][1]
CODE, _, PHRASE = _secret.partition('\x1f')

srv = ThreadingHTTPServer(('127.0.0.1', PORT), functools.partial(SimpleHTTPRequestHandler, directory=DOCS))
threading.Thread(target=srv.serve_forever, daemon=True).start()

R = []
def rec(n, ok, d=''):
    R.append(ok)
    print((('  PASS ' if ok else '  FAIL ') + n + (' | ' + d if d else '')).encode('ascii', 'replace').decode('ascii'))

def unlock(pg):
    pg.wait_for_selector('#gatepw', timeout=15000)
    pg.fill('#gatepw', CODE)
    if PHRASE: pg.fill('#gatephrase', PHRASE)
    pg.click('#gatego')
    pg.wait_for_function("document.getElementById('gate') && getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)
    pg.wait_for_timeout(700)

def state(pg):
    return pg.evaluate("""() => ({
      tier: typeof tier!=='undefined' ? tier : null,
      county: typeof county!=='undefined' ? county : null,
      rows: document.querySelectorAll('#tb tr[data-case]').length,
      tierActive: (document.querySelector('.tf.active')||{}).dataset?.t || null,
      cfActive: (document.querySelector('.cf.active')||{}).dataset?.cty || null
    })""")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={'width': 1500, 'height': 1000})
    pg = ctx.new_page(); errs = []
    pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: ' + str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    unlock(pg)

    s0 = state(pg)
    rec('Default view loads (Tier A, no county)', s0['rows'] > 0, str(s0))

    # click Miami-Dade county pill -> tier should flip to ALL, county=MIAMI-DADE, show the WHOLE county
    pg.click('.cf[data-cty="MIAMI-DADE"]'); pg.wait_for_timeout(400)
    s1 = state(pg)
    rec('Click Miami-Dade -> tier auto-flips to ALL', s1['tier'] == 'ALL' and s1['tierActive'] == 'ALL', str(s1))
    rec('Click Miami-Dade -> shows WHOLE county (not just Tier A)', s1['county'] == 'MIAMI-DADE' and s1['rows'] > 200, str(s1))
    md_all = s1['rows']

    # now narrow to Tier A -> county MUST stay Miami-Dade
    pg.click('.tf[data-t="A"]'); pg.wait_for_timeout(400)
    s2 = state(pg)
    rec('Tier A within Miami-Dade -> county STAYS, count shrinks', s2['county'] == 'MIAMI-DADE' and 0 < s2['rows'] < md_all, str(s2))
    md_a = s2['rows']

    # back to ALL tiers -> should restore the full county count
    pg.click('.tf[data-t="ALL"]'); pg.wait_for_timeout(400)
    s3 = state(pg)
    rec('Tier ALL restores full Miami-Dade count', s3['county'] == 'MIAMI-DADE' and s3['rows'] == md_all, f"{s3['rows']} vs {md_all}")

    # switch county to Broward -> tier flips to ALL again, shows whole Broward
    pg.click('.cf[data-cty="BROWARD"]'); pg.wait_for_timeout(400)
    s4 = state(pg)
    rec('Switch to Broward -> tier ALL, whole county', s4['county'] == 'BROWARD' and s4['tier'] == 'ALL' and s4['rows'] > 0, str(s4))

    # 'All counties' pill -> keeps current tier (ALL), shows everything
    pg.click('.cf[data-cty=""]'); pg.wait_for_timeout(400)
    s5 = state(pg)
    rec('All-counties pill clears county scope', (not s5['county']) and s5['rows'] > md_all, str(s5))

    # THE 30%-EQUITY FILTER MUST AGREE WITH THE ROW IT FILTERS. netEqPct() used to be a FOURTH
    # private reading of the money — raw r.orsurv for the chain, raw r.value for worth (skipping the
    # ARV confidence gate), a stale baked r.eq fallback, and the judgment subtracted even on a tax
    # deed. It disagreed with the equity shown on the row on 336 leads: 62 leads that DO have >=30%
    # equity were hidden BY the >=30% filter, 26 of them verdict STRONG. He clicks the filter to
    # surface his best deals and it buried them.
    eq = pg.evaluate("""() => {
      let disagree = 0, hiddenButRich = 0, strongHidden = 0;
      DATA.forEach(r => {
        if (r._nodata) return;
        const filt = netEqPct(r), basis = _basisOf(r) || 0;
        const truth = basis > 0 ? Math.round((_netEqOf(r) || 0) / basis * 100) : 0;
        if (Math.abs(filt - truth) >= 5) {
          disagree++;
          if (filt < 30 && truth >= 30) { hiddenButRich++; if (r._verdict === 'STRONG') strongHidden++; }
        }
      });
      // researching a lien moves the row, so it must move the filter too — it never used to
      const t = DATA.find(x => (+x._chainSurv || 0) > 1000 && !x._nodata);
      let moves = false;
      if (t) {
        const before = netEqPct(t);
        notes[t.case] = notes[t.case] || {}; notes[t.case].slien = 0; recompute();
        moves = netEqPct(t) > before;
        delete notes[t.case].slien; recompute();
      }
      return {disagree, hiddenButRich, strongHidden, moves, probed: !!t};
    }""")
    rec('Equity filter agrees with the equity shown on the row', eq['disagree'] == 0,
        f"{eq['disagree']} leads diverge")
    rec('No qualifying lead is hidden by the equity filter',
        eq['hiddenButRich'] == 0 and eq['strongHidden'] == 0,
        f"{eq['hiddenButRich']} hidden ({eq['strongHidden']} STRONG)")
    rec('A researched lien moves the equity filter', eq['moves'] and eq['probed'], '')

    real = [e for e in errs if 'favicon' not in e]
    rec('No console/page errors', not real, '; '.join(real[:3])[:200])
    b.close()

srv.shutdown()
ok = sum(R); print(f"\n==== {ok}/{len(R)} filter checks passed ====")
raise SystemExit(0 if ok == len(R) else 1)
