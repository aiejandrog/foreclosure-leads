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
        rec('four lane tabs render (REPLIED + 3 day lanes)', tabs==4, f'{tabs} tabs')
        labels = await w.locator('.mwlane .lt').all_inner_texts()
        rec('tabs are REPLIED / URGENT / ACTIVE / EARLY with counts',
            len(labels)==4 and 'REPLIED' in labels[0], ' | '.join(x.replace(chr(10),' ') for x in labels))
        _cls = ' '.join([(await w.locator('.mwlane').nth(x).get_attribute('class')) or '' for x in range(tabs)])
        rec('a lane starts selected', 'on' in _cls)
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

        # EARLY was starved for months (125 lis-pendens leads, none with a traced phone/email) and
        # this asserted the honest empty state. The 2026-08-02 skip-trace funded it — 59 reachable
        # as of that run — so hardcoding "empty" now fails on a board that got BETTER. Assert
        # whichever state is real: a populated lane must render a card, an empty one must still
        # explain the DATA GAP rather than show a generic "nothing here".
        await w.locator('.mwlane[data-lane="early"]').click(); await w.wait_for_timeout(600)
        early_txt = await w.locator('#mwmain').inner_text()
        early_n = await pg.evaluate("() => _laneCount('early')")
        if early_n:
            has_card = bool((await w.locator('.mwname').inner_text()).strip())
            rec(f'EARLY is funded ({early_n} leads) and renders a real card', has_card,
                early_txt[:70].replace('\n', ' '))
        else:
            rec('EARLY explains WHY it is empty (data gap, not bug)',
                'traced phone or email' in early_txt, early_txt[:110].replace('\n', ' '))
            rec('EARLY points back at the funded lanes', 'Still open' in early_txt)
        await w.screenshot(path=str(SHOTS/'worker_early.png'), full_page=False)

        # CAP ENFORCEMENT, through the real path: the worker's script is IIFE-scoped (no test hooks
        # in production code), so drive it the way reality would — put today's send count over the
        # ceiling on the BOARD, then open a fresh worker and see what it bakes.
        await w.close()

        # FIRST, the inverse — this is the bug that produced nine commits of "sent" with an empty
        # Gmail Sent folder. DAILY_MAX opened composers is not DAILY_MAX delivered emails, and it
        # must NOT block the run. Attempts are free; only confirmed delivery spends the budget.
        await pg.evaluate("() => { window._wlogStats = function(){"
                          "  return {email: DAILY_MAX, sent:0, call:0, text:0, wp:0, done:0, skip:0}; }; }")
        async with ctx.expect_page() as npA:
            await pg.evaluate("() => openMorningWorker('urgent')")
        wA = await npA.value
        wA.on('pageerror', lambda e: werrs.append(str(e)))
        await wA.wait_for_timeout(1000)
        attempts_only = await wA.locator('#mwmain').inner_text()
        rec('DAILY_MAX opened composers does NOT block the run (attempts are not sends)',
            'sending limit' not in attempts_only.lower(), attempts_only[:70].replace('\n', ' '))
        await wA.close()

        # NOW the real ceiling: DAILY_MAX confirmed deliveries.
        await pg.evaluate("() => { window._wlogStats = function(){"
                          "  return {email:0, sent: DAILY_MAX, call:0, text:0, wp:0, done:0, skip:0}; }; }")
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
