"""The worker's Email tap must ACTUALLY open Gmail — and never claim it did when it did not."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        ctx=await b.new_context()
        # never actually hit Gmail from a test
        await ctx.route('**://mail.google.com/**', lambda r: r.fulfill(status=200, body='stub'))
        pg=await ctx.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)

        # cards must carry a baked compose URL
        d = await pg.evaluate("""() => {
          const q=_workerQueue('urgent').map(_workerCard);
          const withEm=q.filter(c=>c.emails.length);
          return {n:q.length, withEmail:withEm.length,
                  withGurl:withEm.filter(c=>c.gurl&&c.gurl.indexOf('mail.google.com')===0===false&&c.gurl.indexOf('https://mail.google.com')===0).length,
                  sample:withEm[0]?{to:withEm[0].emails[0],gurlHasTo:withEm[0].gurl.indexOf(encodeURIComponent(withEm[0].emails[0]))>-1,len:withEm[0].gurl.length}:null};
        }""")
        rec('every emailable card carries a baked Gmail URL', d['withGurl']==d['withEmail'] and d['withEmail']>0,
            f"{d['withGurl']}/{d['withEmail']} cards")
        rec('the URL is prefilled with the recipient', bool(d['sample'] and d['sample']['gurlHasTo']), d['sample'])

        # open the worker
        async with ctx.expect_page() as np:
            await pg.evaluate("() => openMorningWorker('urgent')")
        w = await np.value
        werrs=[]; w.on('pageerror', lambda e: werrs.append(str(e)))
        await w.wait_for_timeout(1200)

        # HAPPY PATH: tap Email -> a real new page opens at mail.google.com
        if await w.locator('#mw-email.disabled').count():
            # advance until we hit a card with an email
            for _ in range(10):
                await w.locator('#mw-skip').click(); await w.wait_for_timeout(300)
                if not await w.locator('#mw-email.disabled').count(): break
        async with ctx.expect_page() as gp:
            await w.locator('#mw-email').click()
        g = await gp.value
        await w.wait_for_timeout(700)
        rec('tapping Email opens a REAL new tab', g is not None)
        rec('...at Gmail compose with content', g.url.startswith('https://mail.google.com/mail/') and 'body=' in g.url,
            g.url[:80])
        log1 = await w.locator('#mwlog').inner_text()
        rec('log claims success only now', 'Gmail compose opened' in log1)
        board_log = await pg.evaluate("() => _wlogToday().filter(e=>e.a==='email').length")
        rec('board archived the send', board_log >= 1, f"{board_log} email entries")
        await g.close()

        # BLOCKED PATH: window.open returns null (what a popup blocker does) -> NO false success
        await w.evaluate("() => { window.open = function(){ return null; }; }")
        await w.locator('#mw-email').click(); await w.wait_for_timeout(600)
        log2 = await w.locator('#mwlog').inner_text()
        first = log2.split('\n')[0] if log2 else ''
        rec('blocked popup logs a FAILURE, not success', 'did not open' in log2 or 'blocked' in log2.split('Gmail compose opened')[0],
            first[:70])
        opened_claims = log2.count('Gmail compose opened')
        rec('no new false "opened" claim was added', opened_claims == log1.count('Gmail compose opened'),
            f"{opened_claims} claims")

        # AUTO-RUN with blocked popups -> tap-to-open button, not a silent skip
        await w.evaluate("() => { auto=false; }") if False else None
        await w.locator('#mw-auto').click(); await w.wait_for_timeout(900)
        body = await w.locator('#mwmain').inner_text()
        rec('auto-run under a blocker shows the tap-to-open rescue', 'Composer blocked' in body or 'Open Gmail draft now' in body,
            body[:70].replace('\n',' '))
        rec('no JS errors in worker', not werrs, werrs[:2])
        rec('no JS errors on board', not errs, errs[:2])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} worker-email checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
