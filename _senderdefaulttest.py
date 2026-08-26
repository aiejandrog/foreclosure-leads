"""One-shot: verify the prefilled outreach identity (Alejandro Gonzalez / the gated company name /
(786) 631-1823), the stale-'Jose' migration, and that a custom saved name is left alone. Gitignored _*.py.

The company name is NOT hardcoded here. Until 2026-08-26 it was, as 'Miami Solutions Group' -- a name
that belongs to a different Florida company -- so this suite asserted the bug was correct and went
green while the live board prefilled every operator's composer with it. The expected value now comes
from entity.display_llc(), the same gate the board is built from, and a separate check asserts it is
not a name entity.RETIRED knows to be dead. A test may not be the place a stale identity survives."""
import http.server, socketserver, threading, os, functools
from playwright.sync_api import sync_playwright
import entity
import paths as P

CO = entity.display_llc()[0]

HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, 'docs'); PORT = 8806
CODE = P.live_code()
Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DOCS)
class Q(socketserver.TCPServer): allow_reuse_address = True
srv = Q(('127.0.0.1', PORT), Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()

R=[]
def rec(n, ok, d=''): R.append(ok); print((('  PASS ' if ok else '  FAIL ')+n+(' | '+d if d else '')).encode('ascii','replace').decode('ascii'))

def fresh_load(ctx):
    pg = ctx.new_page()
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg.wait_for_selector('#gatepw', timeout=15000); pg.fill('#gatepw', CODE); pg.click('#gatego')
    pg.wait_for_function("document.getElementById('gate') && getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)
    return pg

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)

    # The gate itself must not be handing back a name we retired. display_llc() strips an
    # unsubstantiated " LLC" suffix, but it has no opinion on whether the NAME is still ours, so a
    # poisoned sender.json passed straight through it and onto the board. This is that missing check.
    rec('The gated company name is not a RETIRED one', bool(CO) and entity.forward(CO) == CO, CO)

    # scenario 1: fresh device, nothing saved -> full prefilled identity
    ctx1 = b.new_context(viewport={'width':1500,'height':1000})
    pg1 = fresh_load(ctx1)
    s1 = pg1.evaluate("() => ({name: sender.name, llc: sender.llc, phone: sender.phone})")
    rec('Fresh device prefills name/company/phone', s1['name']=='Alejandro Gonzalez' and s1['llc']==CO and s1['phone']=='(786) 631-1823', str(s1))

    # the generated email actually carries all three (no [YOUR NAME]/[YOUR COMPANY]/[YOUR PHONE] left)
    case = pg1.evaluate("() => { const r=DATA.find(x=>x.case && x.case.length>3); return r.case; }")
    with pg1.context.expect_page() as pop:
        pg1.evaluate("(c)=>{ genEmail(DATA.find(x=>x.case===c)); }", case)
    ep = pop.value; ep.wait_for_load_state('domcontentloaded')
    eh = ep.content()
    rec('Generated email has NO unfilled placeholders', ('[YOUR NAME]' not in eh) and ('[YOUR COMPANY]' not in eh) and ('[YOUR PHONE]' not in eh))
    rec('Generated email carries the real identity', 'Alejandro Gonzalez' in eh and CO in eh and '786' in eh)

    # scenario 2: device with stale 'Jose' saved from the earlier build -> migrates to the new default
    ctx2 = b.new_context(viewport={'width':1500,'height':1000})
    pg0 = ctx2.new_page()
    pg0.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg0.evaluate("() => localStorage.setItem('fcSender', JSON.stringify({name:'Jose', llc:'Miami Solutions Group'}))")
    pg2 = fresh_load(ctx2)
    s2 = pg2.evaluate("() => ({name: sender.name, llc: sender.llc})")
    rec('Stale "Jose" migrates to Alejandro Gonzalez', s2['name']=='Alejandro Gonzalez', str(s2))
    # The fixture above saves the RETIRED company too, and until now nothing asserted on it. That is
    # the operator-device half of this bug: Carlos's and Jose's browsers hold an fcSender from before
    # the rename, and a saved non-empty value wins the merge, so without the normalizer they keep
    # printing the dead company on their own paper no matter what the build says.
    rec('Stale saved company migrates forward too', s2['llc']==CO, str(s2))

    # scenario 3: a deliberately custom saved name is left alone
    ctx3 = b.new_context(viewport={'width':1500,'height':1000})
    pg00 = ctx3.new_page()
    pg00.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg00.evaluate("() => localStorage.setItem('fcSender', JSON.stringify({name:'Carlos Ramirez', llc:'X'}))")
    pg3 = fresh_load(ctx3)
    s3 = pg3.evaluate("() => sender.name")
    rec('A custom saved name (Carlos Ramirez) is left alone', s3=='Carlos Ramirez', s3)

    # scenario 4: THE REPORTED BUG — a saved sender full of EMPTY strings must NOT wipe the prefilled
    # defaults (this is what produced the live [YOUR NAME]/[YOUR COMPANY]/[YOUR PHONE] email).
    ctx4 = b.new_context(viewport={'width':1500,'height':1000})
    pg000 = ctx4.new_page()
    pg000.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg000.evaluate("() => localStorage.setItem('fcSender', JSON.stringify({name:'', llc:'', phone:'', addr:'', title:'', email:'', web:'', partner:''}))")
    pg4 = fresh_load(ctx4)
    s4 = pg4.evaluate("() => ({name: sender.name, llc: sender.llc, phone: sender.phone}))".replace('}))','})'))
    rec('Empty saved sender does NOT clobber the prefilled defaults', s4['name']=='Alejandro Gonzalez' and s4['llc']==CO and s4['phone']=='(786) 631-1823', str(s4))
    # and the generated email has no placeholders in that state
    case4 = pg4.evaluate("() => { const r=DATA.find(x=>x.case && x.case.length>3); return r.case; }")
    with pg4.context.expect_page() as pop4:
        pg4.evaluate("(c)=>{ genEmail(DATA.find(x=>x.case===c)); }", case4)
    ep4 = pop4.value; ep4.wait_for_load_state('domcontentloaded')
    eh4 = ep4.content()
    rec('Email from an empty-saved device has NO [YOUR ...] placeholders', ('[YOUR NAME]' not in eh4) and ('[YOUR COMPANY]' not in eh4) and ('[YOUR PHONE]' not in eh4))

    # scenario 5: a partial saved sender (user typed only an email) keeps their email AND the prefilled rest
    ctx5 = b.new_context(viewport={'width':1500,'height':1000})
    pg0000 = ctx5.new_page()
    pg0000.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg0000.evaluate("() => localStorage.setItem('fcSender', JSON.stringify({name:'', llc:'', phone:'', email:'me@msg.com'}))")
    pg5 = fresh_load(ctx5)
    s5 = pg5.evaluate("() => ({name: sender.name, email: sender.email}))".replace('}))','})'))
    rec('Partial save keeps the typed field + the prefilled defaults', s5['name']=='Alejandro Gonzalez' and s5['email']=='me@msg.com', str(s5))

    b.close()
srv.shutdown()
ok=sum(R); print(f"\n==== {ok}/{len(R)} sender-prefill checks passed ===="); raise SystemExit(0 if ok==len(R) else 1)
