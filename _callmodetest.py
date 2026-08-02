"""Morning Worker CALL MODE — the channel the worker never had (added 2026-08-02).

Before this, the worker could not place a call. `r.phones` sat on every card unused, and the
`done` button blind-stamped every lead 'Called - no answer' whether or not anyone had dialled.
So "the phone isn't hitting" was an untestable feeling: nothing recorded what happened on a call.

These checks defend the properties that make call outcomes trustworthy:
  * the Call button exists, is FIRST (it is the highest-converting, uncapped channel), and is
    disabled only when no phone is traced
  * tapping it renders the six-outcome disposition bar -- and does NOT navigate the tab away
    (an early build set window.location.href='tel:...', which tore the worker down mid-click
     and silently lost the lead)
  * each outcome writes a real touch with the real label, not a hardcoded default
  * DNC suppresses the owner immediately and permanently -- _workerEligible must go false
  * a wrong number suppresses too (it is a data problem, not a lead)
  * cooldowns are outcome-aware: no-answer/voicemail 24h, talked/not-interested 72h
  * calls NEVER touch the email/text daily cap
"""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
ok, bad = [], []
def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


async def advance_to_callable(w, tries=16):
    """Skip forward until the current card has a traced phone."""
    for _ in range(tries):
        live = await w.evaluate(
            "() => { const b=document.getElementById('mw-call');"
            "        return !!(b && !b.classList.contains('disabled')); }")
        if live:
            return True
        if not await w.evaluate("() => !!document.getElementById('mw-skip')"):
            return False
        await w.click('#mw-skip')
        await w.wait_for_timeout(300)
    return False


async def outcome_run(w, pg, oc):
    """Place a call on the current card, pick outcome `oc`, return the board-side effects."""
    if not await advance_to_callable(w):
        return None
    case = await w.evaluate("() => (window.__mwCase||'')")
    await w.click('#mw-call')
    await w.wait_for_timeout(600)
    if not await w.evaluate(f"() => !!document.querySelector('.mwoc[data-oc=\"{oc}\"]')"):
        return {'err': 'disposition bar missing for ' + oc}
    await w.click(f'.mwoc[data-oc="{oc}"]')
    await w.wait_for_timeout(800)
    return await pg.evaluate("""() => {
      const calls = _wlogToday().filter(e => e.a === 'call');
      const last  = calls[calls.length - 1] || {};
      const n     = notes[last.c] || {};
      const row   = DATA.find(x => x.case === last.c);
      const t     = (n.touches || []);
      return {
        case: last.c || '',
        detail: last.detail || '',
        cooldownH: (typeof n.cooldownH === 'number') ? n.cooldownH : null,
        status: n.status || '',
        optout: !!n.optout,
        wrongown: !!n.wrongown,
        lastTouchCh: (t[t.length-1] || {}).ch || '',
        lastTouchOut: (t[t.length-1] || {}).out || '',
        eligible: row ? _workerEligible(row) : null,
        cap: _sentTodayCount()
      };
    }""")


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context()
        pg = await ctx.new_page()
        berr, werr = [], []
        pg.on('pageerror', lambda e: berr.append(str(e)))
        await pg.goto(SRC.as_uri())
        await pg.wait_for_timeout(2600)

        cap0 = await pg.evaluate("() => _sentTodayCount()")

        async with ctx.expect_page() as np:
            await pg.evaluate("openMorningWorker('urgent')")
        w = await np.value
        w.on('pageerror', lambda e: werr.append(str(e)))
        await w.wait_for_timeout(2000)

        # ---------- button presence + ordering ----------------------------------------------
        layout = await w.evaluate("""() => {
          const ids = Array.from(document.querySelectorAll('.mwacts .mwbtn')).map(b => b.id);
          const c = document.getElementById('mw-call');
          return {ids: ids,
                  callIdx: ids.indexOf('mw-call'),
                  emailIdx: ids.indexOf('mw-email'),
                  txt: (c ? c.textContent : '').replace(/\\s+/g,' ')};
        }""")
        rec('call button exists', 'mw-call' in layout['ids'], layout['ids'])
        rec('call sits BEFORE email (primary channel)',
            0 <= layout['callIdx'] < layout['emailIdx'],
            {'call': layout['callIdx'], 'email': layout['emailIdx']})
        rec('call button says calls are never capped',
            'capped' in layout['txt'] or 'skip-trace' in layout['txt'], layout['txt'][:70])

        # ---------- disposition bar renders, tab survives ------------------------------------
        had = await advance_to_callable(w)
        rec('found a lead with a traced phone', had)
        url_before = w.url
        await w.click('#mw-call')
        await w.wait_for_timeout(700)
        disp = await w.evaluate("""() => ({
          header: (document.querySelector('.mwpace .h') || {}).textContent || '',
          oc: Array.from(document.querySelectorAll('.mwoc')).map(b => b.dataset.oc),
          labels: Array.from(document.querySelectorAll('.mwoc')).map(b => b.textContent)
        })""")
        rec('tapping Call renders the disposition bar', 'How did the call go' in disp['header'],
            disp['header'][:50])
        rec('all six outcomes render',
            disp['oc'] == ['noanswer','voicemail','talked','wrong','notint','dnc'], disp['oc'])
        # REGRESSION: window.location.href='tel:' used to navigate the tab away before the bar
        # could render. The anchor-click dial must leave the document alone.
        rec('dialing does NOT navigate the worker tab away', w.url == url_before,
            {'before': url_before[:40], 'after': w.url[:40]})
        rec('DNC option is visibly the hard one',
            any('do not contact' in (l or '').lower() for l in disp['labels']), disp['labels'])

        # ---------- talked -> 72h, status Contacted ------------------------------------------
        await w.click('.mwoc[data-oc="talked"]')
        await w.wait_for_timeout(800)
        r = await pg.evaluate("""() => {
          const c = _wlogToday().filter(e => e.a === 'call');
          const last = c[c.length-1] || {}; const n = notes[last.c] || {};
          const t = n.touches || [];
          return {detail:last.detail||'', cool:n.cooldownH, status:n.status||'',
                  ch:(t[t.length-1]||{}).ch||'', out:(t[t.length-1]||{}).out||'',
                  cap:_sentTodayCount()};
        }""")
        rec('TALKED logs the real outcome, not a default',
            'Talked' in r['detail'] and 'no answer' not in r['detail'].lower(), r['detail'])
        rec('TALKED sets a 72h cooldown', r['cool'] == 72, r['cool'])
        rec('TALKED writes a ch:call touch carrying the label',
            r['ch'] == 'call' and 'Talked' in (r['out'] or ''), {'ch': r['ch'], 'out': r['out']})
        rec('TALKED sets status Contacted', r['status'] == 'Contacted', r['status'])
        rec('a call does NOT charge the email cap', r['cap'] == cap0,
            {'before': cap0, 'after': r['cap']})

        # ---------- no answer -> 24h ---------------------------------------------------------
        na = await outcome_run(w, pg, 'noanswer')
        if na and not na.get('err'):
            rec('NO ANSWER comes back in 24h, not 72h', na['cooldownH'] == 24, na['cooldownH'])
            rec('NO ANSWER still logs a call touch', na['lastTouchCh'] == 'call', na['lastTouchCh'])
        else:
            rec('NO ANSWER comes back in 24h, not 72h', False, na)

        # ---------- wrong number -> suppressed ------------------------------------------------
        wr = await outcome_run(w, pg, 'wrong')
        if wr and not wr.get('err'):
            rec('WRONG NUMBER flags wrongown', wr['wrongown'], wr)
            rec('WRONG NUMBER drops the lead from the queue', wr['eligible'] is False,
                wr['eligible'])
        else:
            rec('WRONG NUMBER flags wrongown', False, wr)

        # ---------- DNC -> hard, permanent suppression ---------------------------------------
        dn = await outcome_run(w, pg, 'dnc')
        if dn and not dn.get('err'):
            rec('DNC writes the opt-out flag', dn['optout'], dn)
            rec('DNC sets status DO NOT CONTACT', dn['status'] == 'DO NOT CONTACT', dn['status'])
            rec('DNC makes the lead permanently ineligible', dn['eligible'] is False, dn['eligible'])
            rec('DNC still did not touch the email cap', dn['cap'] == cap0,
                {'before': cap0, 'after': dn['cap']})
        else:
            rec('DNC writes the opt-out flag', False, dn)

        # ---------- stats strip surfaces calls -----------------------------------------------
        strip = await w.evaluate("() => (document.getElementById('mwlogstats')||{}).textContent||''")
        rec('log strip counts calls separately from sends',
            'calls' in strip.lower() and 'delivered' in strip.lower(), strip[:70])

        rec('no JS errors in the worker tab', not werr, werr[:2] if werr else '')
        rec('no JS errors on the board', not berr, berr[:2] if berr else '')

        await b.close()
        total = len(ok) + len(bad)
        print(f'\n==== {len(ok)}/{total} call-mode checks passed ====')
        return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
