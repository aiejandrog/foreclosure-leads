"""One-shot: verify the CyberBG address-search link. Gitignored _*.py."""
import http.server, socketserver, threading, os, functools
from playwright.sync_api import sync_playwright
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, 'docs'); PORT = 8802
CODE = P.live_code()
Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DOCS)
class Q(socketserver.TCPServer): allow_reuse_address = True
srv = Q(('127.0.0.1', PORT), Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()

R=[]
def rec(n, ok, d=''): R.append(ok); print((('  PASS ' if ok else '  FAIL ')+n+(' | '+d if d else '')).encode('ascii','replace').decode('ascii'))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True); ctx = b.new_context(viewport={'width':1500,'height':1000})
    pg = ctx.new_page(); errs=[]
    pg.on('console', lambda m: errs.append(m.text) if m.type=='error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: '+str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg.wait_for_selector('#gatepw', timeout=15000); pg.fill('#gatepw', CODE); pg.click('#gatego')
    pg.wait_for_function("document.getElementById('gate') && getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)

    n = pg.evaluate("() => DATA.filter(r=>r.cyberbgaddr).length")
    rec('Some leads carry a cyberbg address-search URL', n > 0, f'{n} leads')
    sample = pg.evaluate("() => { const r=DATA.find(x=>x.cyberbgaddr); return {case:r.case, url:r.cyberbgaddr}; }")
    ok_shape = bool(sample) and sample['url'].startswith('https://www.cyberbackgroundchecks.com/address/')
    rec('URL shape correct', ok_shape, str(sample))
    # FOCUS THE LEAD FIRST. The .dig links live in a lead's expanded panel, so querying the raw
    # document right after unlock only sees whatever rows happen to be open — measured: 4 of 121
    # rendered rows carried ANY cyberbg link and 0 carried an address one. That made this a coin
    # flip: the sibling name-search test passes on the same code purely because its 4 links
    # happened to be present. Filter to a lead we KNOW has the field, then assert.
    pg.fill('#q', sample['case'] if sample else '')
    pg.wait_for_timeout(700)
    link_href = pg.evaluate("""() => {
      const btn = document.querySelector('a[href*="cyberbackgroundchecks.com/address"]');
      return btn ? btn.getAttribute('href') : null;
    }""")
    rec('CyberBG-address button rendered', bool(link_href), str(link_href)[:90])
    real=[e for e in errs if 'favicon' not in e]
    rec('No console/page errors', not real, '; '.join(real[:2]))
    b.close()
srv.shutdown()
ok=sum(R); print(f"\n==== {ok}/{len(R)} CyberBG-address checks passed ===="); raise SystemExit(0 if ok==len(R) else 1)
