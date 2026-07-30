"""Open the real Morning Worker tab and drive the lane tabs + cap meter like the operator would."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
SHOTS = pathlib.Path(os.environ.get('TEMP','.'))/'dealflow_shots'; SHOTS.mkdir(exist_ok=True)
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); ctx = await b.new_context(); pg = await ctx.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(2500)
        async with ctx.expect_page() as np:
            await pg.evaluate("() => openMorningWorker('urgent')")
        w = await np.value
        werrs=[]; w.on('pageerror', lambda e: werrs.append(str(e)))
        await w.wait_for_timeout(1200)

        tabs = await w.locator('.mwlane').count()
        rec('three lane tabs render', tabs==3, f'{tabs} tabs')
        labels = await w.locator('.mwlane .lt').all_inner_texts()
        rec('tabs are URGENT / ACTIVE / EARLY with counts', len(labels)==3, ' | '.join(x.replace('\n',' ') for x in labels))
        rec('URGENT starts selected', 'on' in (await w.locator('.mwlane').nth(0).get_attribute('class')))
        # String-concat bugs in the baker surface as literal NaN/undefined in the chrome. A stray
        # unary + put "call these firstNaN" in the subtitle and every assertion above still passed.
        chrome = await w.locator('.mwtop, .mwlanes, .mwcap').all_inner_texts()
        chrome = ' '.join(chrome)
        rec('no NaN/undefined leaks into the worker chrome', 'NaN' not in chrome and 'undefined' not in chrome, chrome[:80].replace(chr(10),' '))

        capn = await w.locator('#mwcapn').inner_text()
        captx = await w.locator('#mwcaptx').inner_text()
        rec('cap meter shows n / max', '/' in capn, capn)
        rec('cap copy states sends remaining', 'sends left today' in captx, captx[:60])
        rec('cap not blocked at zero sends', 'blocked' not in (await w.locator('#mwcap').get_attribute('class')))

        prog = await w.locator('#mwprog').inner_text()
        name = await w.locator('.mwname').inner_text()
        rec('a lead card renders in URGENT', bool(name.strip()), f'{prog} · {name.strip()[:34]}')
        facts = await w.locator('.mwfacts').inner_text()
        rec('no 9999-day sentinel leaks into the card', '9999' not in facts and 'nulld' not in facts)
        await w.screenshot(path=str(SHOTS/'worker_urgent.png'), full_page=False)

        # switch to ACTIVE
        await w.locator('.mwlane[data-lane="active"]').click(); await w.wait_for_timeout(600)
        rec('ACTIVE becomes selected on tap', 'on' in (await w.locator('.mwlane[data-lane="active"]').get_attribute('class')))
        rec('URGENT deselects', 'on' not in (await w.locator('.mwlane[data-lane="urgent"]').get_attribute('class')))
        sub = await w.locator('.mwtitle .sub').inner_text()
        rec('header subtitle follows the lane', 'ACTIVE' in sub, sub[:52])
        log = await w.locator('#mwlog').inner_text()
        rec('lane switch is written to the activity log', 'switched to ACTIVE' in log)
        rec('card re-renders for the new lane', bool((await w.locator('.mwname').inner_text()).strip()))

        # switch to EARLY (known-empty: honest empty state, not a generic one)
        await w.locator('.mwlane[data-lane="early"]').click(); await w.wait_for_timeout(600)
        empty = await w.locator('#mwmain').inner_text()
        rec('EARLY explains WHY it is empty (data gap, not bug)', 'traced phone or email' in empty, empty[:110].replace('\n',' '))
        rec('EARLY points back at the funded lanes', 'Still open' in empty)
        await w.screenshot(path=str(SHOTS/'worker_early.png'), full_page=False)

        # CAP ENFORCEMENT, through the real path: the worker's script is IIFE-scoped (no test hooks
        # in production code), so drive it the way reality would — put today's send count over the
        # ceiling on the BOARD, then open a fresh worker and see what it bakes.
        await w.close()
        await pg.evaluate("() => { window._wlogStats = function(){ return {email: DAILY_MAX, text:0, wp:0, done:0, skip:0}; }; }")
        async with ctx.expect_page() as np2:
            await pg.evaluate("() => openMorningWorker('urgent')")
        w = await np2.value
        w.on('pageerror', lambda e: werrs.append(str(e)))
        await w.wait_for_timeout(1000)
        blocked = await w.locator('#mwmain').inner_text()
        rec('hitting the cap replaces the card with a hard stop', 'sending limit' in blocked.lower(), blocked[:70].replace('\n',' '))
        rec('blocked state still tells him to keep calling', 'not capped' in blocked or 'Calling is not capped' in blocked)
        rec('cap meter turns red at the ceiling', 'blocked' in (await w.locator('#mwcap').get_attribute('class')))
        await w.screenshot(path=str(SHOTS/'worker_capped.png'), full_page=False)

        rec('no JS errors in the worker tab', not werrs, werrs[:2])
        rec('no JS errors on the board', not errs, errs[:2])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} worker UI checks passed ====')
    print('shots:', SHOTS)
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
