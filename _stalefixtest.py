"""Verify the previously-invisible enrichment fields now actually render."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))

JS = r"""() => {
  const mm  = DATA.filter(r => r.ownerMismatch);
  const gs  = DATA.filter(r => String(r.addrGuess||'').trim());
  const o = {mmCount: mm.length, gsCount: gs.length};
  // chips must produce markup for flagged rows and NOTHING for clean ones
  o.mmChip     = mm.length ? _mismatchChip(mm[0]) : '';
  o.mmChipClean= _mismatchChip(DATA.find(r => !r.ownerMismatch));
  o.gsChip     = gs.length ? _guessAddrChip(gs[0]) : '';
  o.gsChipClean= _guessAddrChip(DATA.find(r => !String(r.addrGuess||'').trim()));
  // the guessed address must be inside the tooltip but NEVER in r.addr
  o.guessLeaked = gs.some(r => String(r.addr||'').trim() === String(r.addrGuess||'').trim() && r.addr);
  o.guessShown  = gs.length ? o.gsChip.indexOf(gs[0].addrGuess) > -1 : false;
  // dial-ready banner
  o.drWith    = mm.length ? genDialReady(mm[0]) : '';
  o.drClean   = genDialReady(DATA.find(r => !r.ownerMismatch && (r.phones||[]).length) || DATA[0]);
  // worker card carries the flag
  o.wcard     = mm.length ? _workerCard(mm[0]) : null;
  o.wcardClean= _workerCard(DATA.find(r => !r.ownerMismatch));
  o.nan = (o.mmChip+o.gsChip+o.drWith).indexOf('NaN') > -1
       || (o.mmChip+o.gsChip+o.drWith).indexOf('undefined') > -1;
  return o;
}"""

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); pg=await b.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)
        d=await pg.evaluate(JS)

        rec('owner-mismatch rows exist to test', d['mmCount']>0, f"{d['mmCount']} rows")
        rec('unconfirmed-address rows exist to test', d['gsCount']>0, f"{d['gsCount']} rows")
        rec('OWNER CHANGED chip renders on flagged rows', 'OWNER CHANGED' in d['mmChip'])
        rec('...and renders nothing on clean rows', d['mmChipClean']=='')
        rec('ADDRESS UNCONFIRMED chip renders on advisory rows', 'ADDRESS UNCONFIRMED' in d['gsChip'])
        rec('...and renders nothing on clean rows', d['gsChipClean']=='')
        rec('the guessed address is visible to the operator', d['guessShown'])
        rec('the guess NEVER contaminates r.addr', not d['guessLeaked'])
        rec('Dial-Ready warns before you use a stale name', 'different owner' in d['drWith'])
        rec('...and stays quiet on clean leads', 'different owner' not in d['drClean'])
        rec('worker card carries the mismatch flag', bool(d['wcard'] and d['wcard'].get('mismatch')))
        rec('...and not on clean leads', not (d['wcardClean'] or {}).get('mismatch'))
        rec('no NaN/undefined leaks', not d['nan'])
        rec('no JS errors', not errs, errs[:2])

        # visible in the real DOM, not just in the function
        html = await pg.content()
        rec('chips reach the rendered board', 'OWNER CHANGED' in html or 'ADDRESS UNCONFIRMED' in html,
            'in default view' if ('OWNER CHANGED' in html or 'ADDRESS UNCONFIRMED' in html) else 'LP rows are in the Fresh-filings lane')
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} stale-fix checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
