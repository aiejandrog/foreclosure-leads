"""Verify Jose's email asks: emails on the sheet + CSV, and the auto-filled per-prospect email. Throwaway."""
import http.server, socketserver, threading, os, functools
from playwright.sync_api import sync_playwright
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, 'docs')
PORT = 8798; CODE = P.live_code()
Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DOCS)
class Q(socketserver.TCPServer): allow_reuse_address = True
threading.Thread(target=Q(('127.0.0.1', PORT), Handler).serve_forever, daemon=True).start()
R = []
def rec(n, ok, d=''):
    R.append(ok)
    print(((' PASS ' if ok else ' FAIL ')+n+(' | '+d if d else '')).encode('ascii','replace').decode('ascii'))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True); ctx = b.new_context(accept_downloads=True); pg = ctx.new_page()
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg.wait_for_selector('#gatepw', timeout=12000); pg.fill('#gatepw', CODE); pg.click('#gatego')
    pg.wait_for_function("()=>getComputedStyle(document.getElementById('gate')).display==='none'", timeout=12000)
    pg.wait_for_selector('#tbl tbody tr', timeout=8000)
    # set a sender identity so the email signs off + is compliant
    pg.evaluate("""()=>{ sender.name='Jose Ramirez'; sender.llc='JR Property Group LLC'; sender.phone='305-555-0142'; sender.addr='123 Main St, Miami FL 33172'; sender.title='Acquisitions'; sender.email='jose@jrpg.com'; sender.web='jrpropertygroup.com'; saveSender&&saveSender(); }""")

    # CONTACTABLE fixture, same gate genEmail uses. Unfiltered, this could land on one of the
    # board's ~78 active §362 bankruptcy-stay leads, where genEmail correctly returns the
    # SUPPRESSION notice — every body assertion below then describes a document that is not an
    # email at all.
    lead = pg.evaluate("""()=>{
      const ok = (typeof _textContactBlocked==='function') ? (r=>!_textContactBlocked(r)) : (()=>true);
      const has = x => x.emails && x.emails.length && ok(x);
      const r = DATA.find(x => has(x) && x.st!=='TD' && x.plaintiff) || DATA.find(has);
      return r ? {case: r.case} : null;
    }""")
    if lead:
        lead = pg.evaluate("(c)=>{const r=DATA.find(x=>x.case===c); return {case:r.case, email:(r.emails||[])[0], addr:r.addr, auction:r.auction, owner:r.owners, plaintiff:r.plaintiff||'', st:r.st};}", lead['case'])
    rec('A lead with skip-traced email exists', bool(lead), lead['email'] if lead else 'none')
    if not lead: b.close(); raise SystemExit
    first = lead['owner'].split(';')[0].strip().split()[0].title()
    street = lead['addr'].split(',')[0].strip()

    # 1) email chip (mailto) renders on the sheet
    pg.fill('#q', lead['case']); pg.wait_for_timeout(300)
    chip = pg.evaluate("""()=>{ const a=document.querySelector('#tbl tbody tr a.email'); return a?{href:a.getAttribute('href'), txt:a.textContent}:null; }""")
    rec('Email shown on the sheet (mailto chip)', bool(chip) and chip['href'].startswith('mailto:') and '@' in chip['txt'], chip['txt'] if chip else 'missing')
    rec('Email action link present', pg.evaluate("()=>!!document.querySelector('#tbl tbody tr a.emailgen')"))

    # 2) generate the auto-filled email (same code the link runs)
    with ctx.expect_page(timeout=6000) as pi:
        pg.evaluate("(c)=>{ const r=DATA.find(x=>x.case===c); genEmail(r); }", lead['case'])
    ep = pi.value; ep.wait_for_load_state('domcontentloaded'); ep.wait_for_timeout(200)
    subj = ep.inner_text('#subjline'); body = ep.inner_text('#bodybox'); toHref = ep.evaluate("()=>{const a=document.querySelector('.to a'); return a?a.getAttribute('href'):'';}")
    rec('Subject professional (Regarding + street)', ('regarding your property' in subj.lower()) and (street.split()[0].lower() in subj.lower()), subj)
    # The body prints the STREET, not the board's full "12535 SW 33 ST, MIAMI, FL- 33175" --
    # that stray hyphen and shouted city are tidied for display. Assert what a human reads.
    rec('Body auto-filled (street + sale date + company)',
        (street in body) and (lead['auction'] in body) and ('JR Property Group' in body),
        f"street={street in body} date={lead['auction'] in body} co={'JR Property Group' in body}")
    rec('NO em/en dashes (reads human)', ('—' not in body) and ('–' not in body))
    # The old "I hope you are doing well / look forward to speaking with you" filler was replaced
    # by the parachute copy: acknowledge their existing plan, offer a free backup, one ask.
    # Assert the SHAPE that copy has to keep, not the sentences it happened to use in 2026-07.
    rec('Human tone: acknowledges their plan and makes one ask',
        ('parachute' in body.lower() or 'backup' in body.lower())
        and ('costs nothing' in body.lower() or 'no fee' in body.lower())
        and ('reply' in body.lower()),
        body.strip().splitlines()[0][:60] if body.strip() else '(empty)')
    # The copy deliberately says "the bank" and never names the plaintiff. Naming the lender to a
    # homeowner reads as inside knowledge of their case and buys nothing, so it was dropped.
    rec('Does NOT name the foreclosing party', (not lead.get('plaintiff')) or (lead['plaintiff'] not in body),
        lead.get('plaintiff') or '(no plaintiff)')
    rec('Professional signature (title + phone + email)', ('Acquisitions' in body) and ('Phone:' in body) and ('Email: jose@jrpg.com' in body))
    # SPLIT, because these had very different answers and one blended FAIL hid it.
    rec('Compliance: opt-out line present', 'stop' in body.lower() and 'contact you again' in body.lower())
    rec('Compliance: physical mailing address present', '123 Main St' in body)
    # OPEN, NOT A TEST BUG. outreach_email.py:467 puts D.identity() -- "I am not your lender, not
    # the government, not a foreclosure-rescue company, and not an attorney" -- into every
    # AUTOMATED send. The board's genEmail(), which is the manual copy-and-send path an operator
    # actually uses, says none of it. Same channel, same homeowner, two different disclosures.
    # Left FAILING on purpose: the fix is a change to homeowner-facing copy and Alejandro removed
    # the MARS block from the flyers deliberately, so this is his call, not a silent edit.
    rec('Compliance: identity disclosure (matches outreach_email.py)',
        ('not an attorney' in body.lower()) or ('no soy abogado' in body.lower()),
        'MISSING from genEmail; outreach_email.py sends D.identity() -- surfaces disagree')
    rec('Pre-addressed to the prospect (mailto recipient)', toHref.startswith('mailto:') and '@' in toHref, toHref)
    ep.click('#es'); ep.wait_for_timeout(150)
    es_body = ep.inner_text('#bodybox')
    # Old ES copy said 'inversionista de bienes raices'. The current Spanish is the parachute
    # translation; assert it is genuinely Spanish and carries the same date, not old wording.
    rec('Spanish version toggles', ('paraca' in es_body.lower() or 'respaldo' in es_body.lower())
        and (lead['auction'] in es_body), es_body.strip().splitlines()[0][:50] if es_body.strip() else '(empty)')
    ep.close()

    # 3) CSV export includes the email column + a populated value
    with pg.expect_download(timeout=6000) as di:
        pg.click('#exp')
    path = di.value.path()
    txt = open(path, encoding='utf-8', errors='ignore').read()
    header = txt.splitlines()[0]
    rec('CSV has an email column (after phone)', 'phone,email' in header)
    rec('CSV email column populated', '@' in txt, 'at least one address exported' if '@' in txt else 'no @ found')
    b.close()
print(f"\n==== {sum(R)}/{len(R)} checks passed ====")
# See _btntest: a suite that reports failures and still exits 0 is not a suite.
raise SystemExit(0 if sum(R) == len(R) else 1)
