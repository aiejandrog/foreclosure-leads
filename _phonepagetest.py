"""docs/call/index.html — the PHONE page, runtime. The surface that does the actual work.

WHY THIS EXISTS (2026-08-26). Every other suite tests the BOARD. Nothing loaded the phone page in
a browser, and the ledgers say that is where the business happens: last week 107 of 107 dials and
92 of 92 confirmed texts were written by Call Mode, none by the board. The one page carrying 100%
of live contact had zero runtime coverage, so its bugs could only be found by Alejandro finding
them mid-call — which is how "sometimes the buttons do not work" became a field report with no
traceback.

Covers the contract that matters on a phone in a driveway: it boots and unlocks, the queue is not
empty, the current lead has a dialable number, an outcome writes BOTH records the scorecard reads
(n.touches AND n.dials — the split that made dial-through read 0.0% while 107 dials existed),
suppression holds client-side, the FTSA cap warns, the worker-queue tombstone works, and nothing
throws.

Run: python _phonepagetest.py     (serves docs/ locally, same pattern as _cstest.py)
"""
import functools
import http.server
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

import foreclosure_leads as F

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, 'docs')
PORT = 8837
_secret = F._load_codes()[0][1]
CODE, _, PHRASE = _secret.partition('\x1f')

srv = ThreadingHTTPServer(('127.0.0.1', PORT),
                          functools.partial(SimpleHTTPRequestHandler, directory=DOCS))
threading.Thread(target=srv.serve_forever, daemon=True).start()

ok, bad = [], []


def rec(n, c, d=''):
    (ok if c else bad).append(n)
    print(('  PASS ' if c else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        # a real phone viewport — this page is never opened on a desktop
        ctx = b.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True,
                            has_touch=True)
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)[:160]))
        pg.on('console', lambda m: errs.append('console: ' + m.text[:120])
              if m.type == 'error' and 'favicon' not in m.text else None)

        pg.goto(f'http://127.0.0.1:{PORT}/call/index.html', wait_until='domcontentloaded')
        pg.wait_for_timeout(800)

        # ---- unlock -------------------------------------------------------------------------
        # the phone gate is its OWN markup (#code/#go), not the board's (#gatepw/#gatego)
        rec('gate renders (payload is encrypted at rest)', pg.locator('#code').count() > 0)
        pg.fill('#code', CODE)
        pg.click('#go')
        pg.wait_for_timeout(2500)
        st = pg.evaluate("""() => ({
          unlocked: typeof ROWS !== 'undefined' && Array.isArray(ROWS),
          rows: (typeof ROWS !== 'undefined' && ROWS) ? ROWS.length : -1,
          caller: (function(){ try { return localStorage.getItem('fcCaller') || ''; } catch(e){ return 'ERR'; } })(),
          pool: (typeof pool === 'function') ? pool().length : -1,
          lanes: (typeof lane !== 'undefined') ? lane : null,
        })""")
        rec('unlocks with a real access code', st['unlocked'], f"{st['rows']} rows")
        rec('the code identifies WHO is calling (stamps every touch)', bool(st['caller']), st['caller'])
        rec('a dialable pool exists after suppression', st['pool'] > 0,
            f"{st['pool']} of {st['rows']} survive suppression")

        # ---- the current card is workable ----------------------------------------------------
        card = pg.evaluate("""() => {
          const P = pool(); if(!P.length) return {none:true};
          const r = P[0];
          return {case:r.c, phones:(r.p||[]).length, addr:!!r.a,
                  callBtn: !!document.querySelector('[onclick*="screenOutcome"], .oc button, #callbtn'),
                  bodyHasNumber: /\\(\\d{3}\\)\\s?\\d{3}-\\d{4}/.test(document.body.innerText)};
        }""")
        if card.get('none'):
            rec('current card has a traced phone', False, 'pool empty')
        else:
            rec('current card has a traced phone', card['phones'] > 0,
                f"{card['case']}: {card['phones']} number(s)")
            rec('a formatted number is on screen (tap-to-dial target)', card['bodyHasNumber'])

        # ---- THE SPLIT THAT BROKE DIAL-THROUGH ------------------------------------------------
        # An outcome must write BOTH records: n.touches (channel/cadence/compliance) and n.dials
        # (the per-dial disposition analyst.py counts). Losing either silently zeroes a metric.
        dual = pg.evaluate("""() => {
          const P = pool(); if(!P.length) return {none:true};
          const r = P[0], c = r.c;
          notes[c] = notes[c] || {status:'', note:''};
          const t0 = (notes[c].touches||[]).length, d0 = (notes[c].dials||[]).length;
          const o = {k:'noans', t:'No answer', h:24};
          const fresh = logOutcome(r, o, '3055551234');
          const n = notes[c] || {};
          const lastT = (n.touches||[]).slice(-1)[0] || {};
          const lastD = (n.dials||[]).slice(-1)[0] || {};
          return {case:c, fresh:fresh,
                  touchAdded:(n.touches||[]).length === t0+1,
                  dialAdded:(n.dials||[]).length === d0+1,
                  touchCh:lastT.ch, touchBy:lastT.by, touchOut:lastT.out,
                  dialBy:lastD.by, dialOc:lastD.oc, dialHasDate:!!lastD.d,
                  cooldown:n.cooldownH};
        }""")
        if not dual.get('none'):
            rec('an outcome writes a TOUCH (channel + who)',
                dual['touchAdded'] and dual['touchCh'] == 'call' and bool(dual['touchBy']),
                f"ch={dual['touchCh']} by={dual['touchBy']}")
            rec('...and a DIAL record (what dial-through counts)',
                dual['dialAdded'] and bool(dual['dialHasDate']),
                f"oc={dual['dialOc']} by={dual['dialBy']}")
            rec('the outcome carries its own cooldown (no-answer returns sooner)',
                dual['cooldown'] == 24, dual['cooldown'])

        # ---- FTSA cap + worker-queue tombstone -------------------------------------------------
        guard = pg.evaluate("""() => {
          const c = '__FTSATEST__';
          const d = today();
          notes[c] = {status:'', note:'', touches:[
            {d:d, ch:'call', out:'no answer'}, {d:d, ch:'call', out:'left message'},
            {d:d, ch:'text', out:'Text sent'}]};
          const before3 = _telephonicToday(notes[c]);
          notes[c].touches.push({d:d, ch:'call', out:'no answer'});
          const tripped = _ftsaCapToast(notes[c]);
          const marked = !!notes[c].touches.slice(-1)[0].capExceeded;
          // worker-queue tombstone: retire must survive an add-only merge
          const q = '__WQTEST__';
          notes[q] = {status:'', note:'', wq:'2026-08-01'};
          const inQ = workerQ().indexOf(q) >= 0;
          retireFromWorkerQ(q);
          const goneQ = workerQ().indexOf(q) < 0;
          notes[q] = _mergeLead(notes[q], {wq:'2026-08-01'});   // stale remote blob
          const stillGone = workerQ().indexOf(q) < 0;
          delete notes[c]; delete notes[q];
          return {before3, tripped, marked, inQ, goneQ, stillGone};
        }""")
        rec('FTSA: 3 telephonic touches today do NOT warn', guard['before3'] == 3)
        rec('FTSA: the 4th warns and marks the record', guard['tripped'] and guard['marked'])
        rec('worker queue: a synced .wq lead appears', guard['inQ'])
        rec('worker queue: retiring hides it', guard['goneQ'])
        rec('worker queue: a stale remote blob cannot resurrect it', guard['stillGone'])

        # ---- suppression is read back live, not baked --------------------------------------------
        supp = pg.evaluate("""() => {
          const P = pool(); if(!P.length) return {none:true};
          const r = P[0], c = r.c, n0 = pool().length;
          notes[c] = notes[c] || {status:'', note:''};
          notes[c].status = 'DO NOT CONTACT';
          const after = pool().length;
          const hidden = !pool().some(x => x.c === c);
          delete notes[c].status;
          return {dropped: n0 - after, hidden};
        }""")
        if not supp.get('none'):
            rec('a DNC set right now suppresses immediately (no rebuild)',
                supp['hidden'] and supp['dropped'] >= 1, f"pool -{supp['dropped']}")

        real = [e for e in errs if 'ERR_CONNECTION_REFUSED' not in e]
        rec('no page/console errors on the phone', not real, real[:2])
        b.close()

    print(f'\n==== {len(ok)}/{len(ok) + len(bad)} phone-page checks passed ====')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
