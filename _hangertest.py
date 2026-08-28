"""One-shot: verify the door-hanger generator. Serves docs/ locally, drives headless. Gitignored _*.py."""
import http.server, socketserver, threading, os, functools
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, 'docs'); PORT = 8795
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
def rec(n, ok, d=''): R.append(ok); print((('  PASS ' if ok else '  FAIL ')+n+(' | '+d if d else '')).encode('ascii','replace').decode('ascii'))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True); ctx = b.new_context(viewport={'width':1500,'height':1000})
    pg = ctx.new_page(); errs=[]
    pg.on('console', lambda m: errs.append(m.text) if m.type=='error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: '+str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg.wait_for_selector('#gatepw', timeout=15000); pg.fill('#gatepw', CODE); pg.click('#gatego')
    pg.wait_for_function("document.getElementById('gate') && getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)
    # set a sender name+phone so the hanger fills in
    pg.evaluate("() => { sender.name='Carlos'; sender.phone='(786) 555-0199'; sender.llc='Vertice Home Buyers LLC'; try{saveSender();}catch(e){} }")
    # pick a lead with a known owner
    # A CONTACTABLE lead, not DATA[0]. The board now bakes 79 active §362 bankruptcy-stay flags,
    # and the first lead is one of them — genHanger correctly returns the SUPPRESSION notice, which
    # has no owner name, no sender block and no handwritten blanks. Three assertions failed for a
    # document that was never supposed to be a hanger. Same gate the generator itself uses.
    _pick = pg.evaluate("""() => {
      const D=(typeof DATA!=='undefined')?DATA:[];
      const ok = (typeof _textContactBlocked==='function') ? (r=>!_textContactBlocked(r)) : (()=>true);
      const r = D.find(x => x.owners && x.case && ok(x));
      return r ? {case:r.case, owners:r.owners} : null;
    }""")
    assert _pick, 'no contactable lead with an owner in DATA'
    case = _pick['case']; owner = _pick['owners']
    # open the hanger in a captured popup
    with pg.context.expect_page() as pop:
        pg.evaluate("(c)=>{ const r=DATA.find(x=>x.case===c); genHanger(r); }", case)
    hp = pop.value; hp.wait_for_load_state('domcontentloaded')
    html = hp.content()
    first = pg.evaluate("(c)=>{ const r=DATA.find(x=>x.case===c); return (r.owners||'').split(';')[0].trim().split(' ')[0]; }", case)
    rec('Hanger opens + fills owner first name', first.lower() in html.lower(), 'first='+first)
    rec('Sender name/phone filled', 'Carlos' in html and '555-0199' in html)
    # Assertions updated 2026-08-11 to Jose's ACTUAL spec (the old ones tested a pre-Z-fold design):
    # privacy applies to the OUTSIDE panel only — the letter INSIDE names the auction plainly
    # because the fold hides it; the outside panel is HANDWRITTEN (blank .wline lines), so no
    # printed first name exists by design.
    outside = html.lower().split('fold 1')[0]
    rec('PRIVACY: outside panel never says foreclosure/auction', ('foreclos' not in outside) and ('auction' not in outside))
    rec('Outside panel is the handwritten blanks + instruction', 'I CAME BY BUT NOBODY ANSWERED' in html and 'wline' in html)
    rec('Both Z-fold lines present', 'FOLD 1' in html and 'FOLD 2' in html)
    rec('Tenant-intel script present', 'tenant' in html.lower() and 'owner of this property' in html.lower())
    rec('Mailbox warning present', 'mailbox' in html.lower())
    # ES toggle swaps the message
    hp.click('#es'); hp.wait_for_timeout(200)
    msg_es = hp.inner_text('#msg')
    rec('Spanish toggle works', 'No soy del banco' in msg_es or 'banco' in msg_es)
    real=[e for e in errs if 'favicon' not in e]
    rec('No console/page errors', not real, '; '.join(real[:2]))
    b.close()
srv.shutdown()
ok=sum(R); print(f"\n==== {ok}/{len(R)} hanger checks passed ===="); raise SystemExit(0 if ok==len(R) else 1)
