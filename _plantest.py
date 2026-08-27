"""One-shot: verify the new compliance-gate + cadence features on the live-built site.
Serves docs/ locally, drives it headless. Throwaway (gitignored _*.py)."""
import http.server, socketserver, threading, os, functools
from playwright.sync_api import sync_playwright
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, 'docs')
PORT = 8794
CODE = P.live_code()

Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DOCS)
class Q(socketserver.TCPServer): allow_reuse_address = True
srv = Q(('127.0.0.1', PORT), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

R = []
def rec(name, ok, detail=''):
    R.append((ok, name))
    print((('  PASS ' if ok else '  FAIL ') + name + (' | '+detail if detail else '')).encode('ascii','replace').decode('ascii'))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={'width': 1500, 'height': 1000})
    pg = ctx.new_page()
    errs = []
    pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: ' + str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg.wait_for_selector('#gatepw', timeout=15000)
    pg.fill('#gatepw', CODE); pg.click('#gatego')
    pg.wait_for_function("document.getElementById('gate') && getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)

    # 0) planmodal must be hidden BEFORE it's ever opened (regression: it was missing display:none/fixed
    #    entirely, so the empty shell rendered inline in the page — a floating box with just the close X)
    pm = pg.evaluate("""() => {
      const el = document.getElementById('planmodal');
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {display: cs.display, position: cs.position, w: r.width, h: r.height};
    }""")
    rec('planmodal hidden before first open', pm['display'] == 'none', str(pm))

    # 1) DNC hard gate: no tel: link may point at a DNC-flagged number; DNC numbers render as span.nodial
    chk = pg.evaluate("""() => {
      const dncSet = new Set();
      DATA.forEach(r => (r.phones||[]).forEach((ph,i) => { if((r.phdnc||[])[i]) dncSet.add(String(ph).replace(/\\D/g,'')); }));
      const dial = [...document.querySelectorAll('a[href^="tel:"]')].map(a=>a.getAttribute('href').replace(/\\D/g,''));
      const leaked = dial.filter(n => dncSet.has(n));
      const gated = document.querySelectorAll('span.phone.nodial').length;
      return {dnc: dncSet.size, leaked: leaked.length, gated: gated};
    }""")
    rec('DNC hard gate (no tel: to flagged numbers)', chk['leaked'] == 0 and (chk['dnc'] == 0 or chk['gated'] > 0),
        f"{chk['dnc']} DNC numbers, {chk['leaked']} leaked as dialable, {chk['gated']} gated spans")

    # 2) Plan-today opens with content
    pg.evaluate("() => document.getElementById('plan').click()")
    pg.wait_for_function("document.getElementById('planmodal').classList.contains('show')", timeout=5000)
    body = pg.inner_text('#planbody')
    rec('Plan-today modal opens', len(body) > 40, body[:70].replace('\\n',' '))
    has_sections = pg.evaluate("() => document.querySelectorAll('#planbody .planls li').length")
    rec('Plan lists due actions', has_sections > 0, f'{has_sections} action rows')

    # 3) planlog jumps to the per-lead log modal
    first_case = pg.evaluate("() => { const b=document.querySelector('#planbody .planlog'); return b ? b.dataset.c : ''; }")
    if first_case:
        pg.evaluate("() => document.querySelector('#planbody .planlog').click()")
        pg.wait_for_function("document.getElementById('logmodal').classList.contains('show')", timeout=5000)
        rec('Plan row opens the log modal', True, first_case)
    else:
        rec('Plan row opens the log modal', False, 'no planlog button found')

    # 4) STOP quick-log sets DO NOT CONTACT + stamps optout, and the lead's contact affordances vanish
    stop_ok = pg.evaluate("""(c) => {
      const btns=[...document.querySelectorAll('#logbody .qlog')];
      const stop=btns.find(b=>/STOP/i.test(b.textContent));
      if(!stop) return {err:'no STOP button'};
      stop.click();
      const n=notes[c]||{};
      return {status:n.status, optout:n.optout||''};
    }""", first_case)
    rec('STOP sets DO NOT CONTACT + optout stamp', stop_ok.get('status')=='DO NOT CONTACT' and bool(stop_ok.get('optout')),
        str(stop_ok))
    # close modal, confirm the row now shows the gate and no outreach generators
    pg.evaluate("() => { closeLogModal(); render(); }")
    gated_row = pg.evaluate("""(c) => {
      const btn=document.querySelector('.logbtn[data-c="'+c+'"]');
      if(!btn) return {gate:true, nogen:true, tel:0, hidden:true};   // filtered out of view entirely = strongest suppression
      const row=btn.closest('tr')||btn.closest('article')||btn.closest('div');
      if(!row) return {err:'no container'};
      return {gate: !!row.querySelector('span.phone.dncgate'),
              nogen: !row.querySelector('.lettergen') && !row.querySelector('.emailgen') && !row.querySelector('.scriptgen'),
              tel: row.querySelectorAll('a[href^="tel:"]').length};
    }""", first_case)
    rec('Opted-out lead: contact affordances suppressed', bool(gated_row.get('gate')) and bool(gated_row.get('nogen')) and gated_row.get('tel')==0,
        str(gated_row))
    # undo (leave no test residue in localStorage state semantics — set status back to '')
    pg.evaluate("""(c) => { const n=notes[c]; if(n){ n.status=''; delete n.optout; if(n.touches&&n.touches.length&&/stop/i.test(n.touches[n.touches.length-1].out||'')) n.touches.pop(); save(); render(); } }""", first_case)

    # 5) door quick-log exists
    pg.evaluate("(c) => openLogModal(c)", first_case)
    door = pg.evaluate("() => [...document.querySelectorAll('#logbody .qlog')].filter(b=>/Door/i.test(b.textContent)).length")
    rec('Door-knock quick-logs present', door >= 2, f'{door} door buttons')
    pg.evaluate("() => closeLogModal()")

    # 6) no console/page errors
    real = [e for e in errs if 'favicon' not in e]
    rec('No console/page errors', not real, '; '.join(real[:2]))

    b.close()
srv.shutdown()
ok = sum(1 for r in R if r[0])
print(f"\n==== {ok}/{len(R)} new-feature checks passed ====")
raise SystemExit(0 if ok == len(R) else 1)
