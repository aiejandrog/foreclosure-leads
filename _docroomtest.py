"""Doc Room print bridge: a click must actually yield a document (the popup-blocker path)."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
import paths as P
SRC = pathlib.Path(P.TWIN)
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); ctx=await b.new_context(); pg=await ctx.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)

        # ATTORNEY-APPROVAL GATE — restored 2026-08-02, DEED-ONLY (see tracker_template around
        # ATTY_APPROVALS for the full history). Contract under test: the quit claim is LOCKED on
        # the shipped board; every merge-blank form still prints freely.
        gate = await pg.evaluate("""() => ({
          hasGate: typeof _docApproved === 'function' && typeof ATTY_APPROVALS === 'object',
          deedLocked: typeof _docApproved === 'function' ? !_docApproved('quitclaim', _qcSrc()) : null,
          keys: typeof ATTY_APPROVALS === 'object' ? Object.keys(ATTY_APPROVALS) : []
        })""")
        rec('approval gate EXISTS and the deed ships locked',
            gate['hasGate'] and gate['deedLocked'], gate)
        # DEED + RETAINER (suite updated 2026-08-26): the 2026-08-11 deep sweep added the retainer
        # to the gate deliberately — as transcribed it carried an up-front deposit grid (12 CFR
        # 1015.5(a) + FS 501.1377 forbid fees before the promised result; FL adds $15k/violation)
        # and rescue-promise framing, so it must not print client-facing without counsel either.
        # The contract this check protects is unchanged: nothing ELSE quietly joins the gate.
        rec('the lock gates exactly the deed + the retainer (nothing else)',
            sorted(gate['keys']) == ['quitclaim', 'retainer'], gate['keys'])

        # merge-blank generators stay OPEN; the two deed generators must return the block shell
        gens = await pg.evaluate("""() => {
          const r = DATA.find(x => x.addr && x.case) || DATA[0];
          const out = {};
          [['quitclaim-single','genQuitClaimSingle'],['quitclaim-multi','genQuitClaimMulti'],
           ['cancel-notice','genCancelNotice'],['cancel-form','genCancelForm'],
           ['tpa','genThirdPartyAuth']].forEach(([k,fn]) => {
            try {
              const h = window[fn] ? window[fn](r) : '';
              // Strip embedded base64 before the NaN scan. The BSG letterhead logo is a data URI and
              // base64 alphabets happily contain the literal substring "NaN" — matching it there is a
              // false positive that would mask the real thing this guards: broken merge math printing
              // "NaN" into a document an owner is about to sign.
              const visible = h.replace(/data:[a-z\/+.-]+;base64,[A-Za-z0-9+\/=]+/g, '');
              out[k] = {len: h.length,
                        blocked: /awaiting attorney|will not print|Awaiting attorney/i.test(h),
                        nan: visible.indexOf('NaN')>-1};
            } catch(e){ out[k] = {err:String(e).slice(0,70)}; }
          });
          return out;
        }""")
        for k,v in gens.items():
            if k.startswith('quitclaim'):
                rec(f'{k} returns the approval page, not a printable deed',
                    (not v.get('err')) and v.get('blocked') is True, v)
            else:
                rec(f'{k} generates a real document', (not v.get('err')) and v.get('len',0)>500 and not v.get('blocked'), v)
            if v.get('len'): rec(f'{k} has no NaN', not v.get('nan'))

        # the deed must STILL carry its safety content — verified through the unlock path (inject
        # the correct fingerprint, render, then re-lock), which also proves approval works.
        deed = await pg.evaluate("""() => {
          const r = DATA.find(x=>x.addr)||DATA[0];
          const keep = ATTY_APPROVALS.quitclaim;
          ATTY_APPROVALS.quitclaim = {by:'TEST', date:'2026-08-02', fingerprint:_docFingerprint(_qcSrc())};
          const h = genQuitClaimMulti(r);
          ATTY_APPROVALS.quitclaim = keep;
          return {furman: h.indexOf('Furman')>-1, witnesses: h.indexOf('689.01')>-1,
                  notary: h.indexOf('695.03')>-1, homestead: h.indexOf('4(c)')>-1,
                  rescue: h.indexOf('501.1377')>-1, norecord: h.indexOf('DO NOT RECORD')>-1,
                  relocked: !_docApproved('quitclaim', _qcSrc())};
        }""")
        rec('gate re-locks after the probe (no approval leaked into the page)', deed['relocked'])
        rec('deed still carries the UPL note (Fla. Bar v. Furman)', deed['furman'])
        rec('deed still carries two-witness + notary statutes', deed['witnesses'] and deed['notary'])
        rec('deed still carries homestead spousal joinder', deed['homestead'])
        rec('deed still carries the 501.1377 consideration warning', deed['rescue'])
        rec('checklist still marked DO NOT RECORD', deed['norecord'])

        # the print bridge: open Doc Room, click a print button, assert a real tab with content
        # CONTACTABLE fixture: the board now bakes 79 active §362 bankruptcy-stay flags, and the
        # first lead with an address is one of them — openDocRoom serves the SUPPRESSION notice,
        # which has no print buttons at all. The assertion failed about a document that was never
        # meant to be the Doc Room. Same gate the generators themselves use.
        r0 = await pg.evaluate("""() => {
          const ok = (typeof _textContactBlocked==='function') ? (r=>!_textContactBlocked(r)) : (()=>true);
          const r = DATA.find(x=>x.addr && x.case && ok(x)) || DATA.find(x=>x.addr&&x.case) || DATA[0];
          return r.case;
        }""")
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
