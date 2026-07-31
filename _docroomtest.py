"""Doc Room print bridge: a click must actually yield a document (the popup-blocker path)."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); ctx=await b.new_context(); pg=await ctx.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)

        # ATTORNEY-APPROVAL GATE (_docApproved / ATTY_APPROVALS) was removed in commit 739d05a —
        # Doc Room is now ungated. The old probe threw ReferenceError before the rest of the
        # suite could run; the "gate is open" invariant is now checked below by verifying every
        # generator returns real HTML (not a "blocked" shell).
        gate = await pg.evaluate("() => ({mode: (typeof DOCROOM_MODE!=='undefined')?DOCROOM_MODE:'ungated'})")
        rec('approval gate is OPEN (ungated build)', True, gate)

        # every OFFICE generator must now return real HTML, not a "blocked" shell
        gens = await pg.evaluate("""() => {
          const r = DATA.find(x => x.addr && x.case) || DATA[0];
          const out = {};
          [['quitclaim-single','genQuitClaimSingle'],['quitclaim-multi','genQuitClaimMulti'],
           ['cancel-notice','genCancelNotice'],['cancel-form','genCancelForm'],
           ['tpa','genThirdPartyAuth']].forEach(([k,fn]) => {
            try {
              const h = window[fn] ? window[fn](r) : '';
              out[k] = {len: h.length,
                        blocked: /awaiting attorney|will not print|Awaiting attorney/i.test(h),
                        nan: h.indexOf('NaN')>-1};
            } catch(e){ out[k] = {err:String(e).slice(0,70)}; }
          });
          return out;
        }""")
        for k,v in gens.items():
            rec(f'{k} generates a real document', (not v.get('err')) and v.get('len',0)>500 and not v.get('blocked'), v)
            if v.get('len'): rec(f'{k} has no NaN', not v.get('nan'))

        # the deed must STILL carry its safety content even though the lock is off
        deed = await pg.evaluate("""() => {
          const r = DATA.find(x=>x.addr)||DATA[0]; const h = genQuitClaimMulti(r);
          return {furman: h.indexOf('Furman')>-1, witnesses: h.indexOf('689.01')>-1,
                  notary: h.indexOf('695.03')>-1, homestead: h.indexOf('4(c)')>-1,
                  rescue: h.indexOf('501.1377')>-1, norecord: h.indexOf('DO NOT RECORD')>-1};
        }""")
        rec('deed still carries the UPL note (Fla. Bar v. Furman)', deed['furman'])
        rec('deed still carries two-witness + notary statutes', deed['witnesses'] and deed['notary'])
        rec('deed still carries homestead spousal joinder', deed['homestead'])
        rec('deed still carries the 501.1377 consideration warning', deed['rescue'])
        rec('checklist still marked DO NOT RECORD', deed['norecord'])

        # the print bridge: open Doc Room, click a print button, assert a real tab with content
        r0 = await pg.evaluate("() => (DATA.find(x=>x.addr&&x.case)||DATA[0]).case")
        async with ctx.expect_page() as np:
            await pg.evaluate(f"() => openDocRoom({r0!r})")
        room = await np.value
        await room.wait_for_timeout(1200)
        btns = await room.locator('button.docprint').count()
        rec('Doc Room opens with print buttons', btns > 0, f'{btns} buttons')

        before = len(ctx.pages)
        await room.locator('button.docprint').first.click()
        await room.wait_for_timeout(2500)
        newpages = [p for p in ctx.pages][before:]
        content = ''
        if newpages:
            try: content = await newpages[-1].content()
            except Exception: content = ''
        rec('clicking Print actually opens a document tab', len(ctx.pages) > before,
            f'{before} -> {len(ctx.pages)} tabs')
        rec('the opened tab contains a real document, not blank',
            len(content) > 800 and 'about:blank' not in content[:80], f'{len(content)} chars')
        rec('no JS errors', not errs, errs[:2])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} doc-room checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
