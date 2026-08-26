"""One-shot: verify the DNC contact-line contract — a Do-Not-Call number stays manually dialable but
must never offer Text or WhatsApp. Gitignored _*.py.

REWRITTEN 2026-08-26. This suite used to assert a compaction design ("all DNC numbers collapse into
ONE struck-through span.phone.dnc.nodial with the full list in a tooltip"). That design is gone —
numbers now render one <div class="ctline"> each, DNC ones carrying a "DNC · call only" tag, via
_contactLineHtml(). The suite kept asserting the old markup, so it had been failing against working
code and telling nobody anything. The dead `span.phone.nodial` CSS rule was removed with it.

What replaced it is worth more than what it tested. `canTxt` in _contactLineHtml is the FTSA/TCPA
boundary: DNC (and BK-stay, and landline, and a blocked lead) withhold Text and WhatsApp while Call
stays live, because a manual single dial to a homeowner about their own foreclosure is permitted and
an SMS to a DNC number is $500-$1,500 in statutory damages. Nothing tested that. Now this does.
"""
import http.server, socketserver, threading, os, functools
from playwright.sync_api import sync_playwright
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(HERE, 'docs'); PORT = 8797
CODE = P.live_code()
Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DOCS)
class Q(socketserver.TCPServer): allow_reuse_address = True
srv = Q(('127.0.0.1', PORT), Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()

R=[]
def rec(n, ok, d=''): R.append(ok); print((('  PASS ' if ok else '  FAIL ')+n+(' | '+d if d else '')).encode('ascii','replace').decode('ascii'))

# Read the contract off a rendered row: how many contact lines, how many carry the DNC tag, and
# whether any DNC line exposes a Text or WhatsApp action. `.ctacts` holds the verbs for its own line,
# so a per-line check is what actually proves the gate — a page-wide count would pass even if the
# wrong line got the wrong buttons.
_PROBE = """(c) => {
  const btn = document.querySelector('.logbtn[data-c="'+c+'"]');
  if(!btn) return {err:'no logbtn for '+c};
  const row = btn.closest('tr') || btn.closest('article') || btn.closest('.card') || btn.closest('div');
  const lines = [...row.querySelectorAll('.ctline')].map(function(L){
    return { dnc:   !!L.querySelector('.cttag.dnc'),
             call:  L.querySelectorAll('a.ctact-call').length,
             text:  L.querySelectorAll('a.ctact-text').length,
             waLink: L.querySelectorAll('a.ctact-wa').length };
  });
  return {n: lines.length, lines: lines,
          dnc: lines.filter(l=>l.dnc).length,
          dncWithText: lines.filter(l=>l.dnc && (l.text || l.waLink)).length,
          dncWithCall: lines.filter(l=>l.dnc && l.call).length,
          okWithText:  lines.filter(l=>!l.dnc && l.text).length};
}"""

with sync_playwright() as p:
    b = p.chromium.launch(headless=True); ctx = b.new_context(viewport={'width':390,'height':844})  # mobile width
    pg = ctx.new_page(); errs=[]
    pg.on('console', lambda m: errs.append(m.text) if m.type=='error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: '+str(e)))
    pg.goto(f'http://127.0.0.1:{PORT}/index.html', wait_until='domcontentloaded')
    pg.wait_for_selector('#gatepw', timeout=15000); pg.fill('#gatepw', CODE); pg.click('#gatego')
    pg.wait_for_function("document.getElementById('gate') && getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)
    pg.wait_for_selector('.card, #tbl tbody tr', timeout=20000)

    # a lead where EVERY number is DNC — nothing on it may offer a text
    allDnc = pg.evaluate("""() => {
      const r = DATA.find(x => (x.phones||[]).length>=2 && (x.phdnc||[]).filter(Boolean).length === (x.phones||[]).length
                               && !x.saleBkAct);
      return r ? r.case : null;
    }""")
    rec('Found a live all-DNC lead to test against', bool(allDnc), str(allDnc))
    if allDnc:
        c = pg.evaluate(_PROBE, allDnc)
        rec('All-DNC lead renders a contact line per number', not c.get('err') and c['n'] >= 2, str(c)[:120])
        rec('Every line on it is tagged DNC', c.get('dnc') == c.get('n'), str(c)[:120])
        rec('NO DNC line offers Text or WhatsApp', c.get('dncWithText') == 0, str(c)[:160])
        rec('DNC numbers stay manually dialable', c.get('dncWithCall') == c.get('dnc'), str(c)[:120])

    # a MIXED lead — the gate must be per-number, not per-lead
    mixed = pg.evaluate("""() => {
      const r = DATA.find(x => { const ph=x.phones||[], d=x.phdnc||[];
        return ph.length>=2 && d.some(Boolean) && d.some(v=>!v) && !x.saleBkAct
               && (x.phtype||[]).every(t => !t || t==='mobile'); });
      return r ? r.case : null;
    }""")
    if mixed:
        m = pg.evaluate(_PROBE, mixed)
        rec('Mixed lead: DNC lines still refuse Text/WA', m.get('dncWithText') == 0, str(m)[:160])
        rec('Mixed lead: the clean mobile DOES get Text', m.get('okWithText', 0) >= 1, str(m)[:160])
    else:
        rec('Mixed good+DNC all-mobile lead exists to test', False, 'none in current dataset — skipped')

    # an opted-out lead publishes no numbers at all, only the gate
    opted = pg.evaluate("""() => { const k=Object.keys(SERVER_OPTOUTS||{}); return k.length?k[0]:null; }""")
    if opted:
        g = pg.evaluate("""(c) => {
          const btn = document.querySelector('.logbtn[data-c="'+c+'"]');
          if(!btn) return {absent:true};
          const row = btn.closest('tr') || btn.closest('article') || btn.closest('.card') || btn.closest('div');
          return {gate: row.querySelectorAll('.phone.dncgate').length, lines: row.querySelectorAll('.ctline').length};
        }""", opted)
        if g.get('absent'):
            rec('Opted-out lead is off the board entirely', True, opted)
        else:
            rec('Opted-out lead shows the gate, not phone numbers',
                g.get('gate', 0) >= 1 and g.get('lines', 0) == 0, str(g))

    # ---- FTSA SEND WINDOW (FS 501.059) -------------------------------------------------------
    # 8am-8pm ET, $500-$1,500 statutory damages PER message outside it. Enforced in textCardHtml
    # via canSend, which turns the live <a class="txsend" href="sms:..."> into an inert
    # <span class="txsend off">. Nothing asserted it until now — the window was verified correct
    # only by a hand probe, which is not a guard. Boundaries matter as much as the middle:
    # FTSA_HR_END is EXCLUSIVE, so 19:00 must send and 20:00 must not.
    win = pg.evaluate("""() => {
      const lead = DATA.find(x => (x.phones||[]).length && !x.saleBkAct);
      if(!lead) return null;
      const real = _flHour, out = {};
      [7, 8, 10, 19, 20, 23].forEach(h => {
        _flHour = () => h;
        const html = textCardHtml(lead);
        out[h] = { within: _withinTextHours(),
                   live: /<a class="txsend"/.test(html) && /href="sms:/.test(html),
                   wa:   /<a class="txwa"/.test(html),
                   off:  /<span class="txsend off"/.test(html) };
      });
      _flHour = real;
      return out;
    }""")
    if not win:
        rec('FTSA window: a textable lead exists to test', False, 'none on this board')
    else:
        rec('FTSA window: 8am and 10am and 7pm CAN send',
            all(win[str(h)]['live'] for h in (8, 10, 19)),
            str({h: win[str(h)]['live'] for h in (8, 10, 19)}))
        rec('FTSA window: 7am, 8pm and 11pm CANNOT send',
            not any(win[str(h)]['live'] for h in (7, 20, 23)),
            str({h: win[str(h)]['live'] for h in (7, 20, 23)}))
        rec('FTSA window: no sms: href survives outside the window',
            all(win[str(h)]['off'] for h in (7, 20, 23)))
        rec('FTSA window: WhatsApp rides the same gate (no ungated side door)',
            not any(win[str(h)]['wa'] for h in (7, 20, 23)),
            'WA live outside hours: %s' % [h for h in (7, 20, 23) if win[str(h)]['wa']])

    real=[e for e in errs if 'favicon' not in e]
    rec('No console/page errors', not real, '; '.join(real[:2]))
    b.close()
srv.shutdown()
ok=sum(R); print(f"\n==== {ok}/{len(R)} DNC contact-gate checks passed ===="); raise SystemExit(0 if ok==len(R) else 1)
