"""Code-enforcement lien equity guard: flags real hits, silent on clean, MD-only, no NaN."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))
JS = r"""() => {
  const flagged = DATA.filter(r => r.codeConcern);
  const lien = DATA.filter(r => r.codeConcern==='lien');
  const fc = DATA.filter(r => r.codeConcern==='foreclosing');
  const clean = DATA.find(r => !r.codeConcern && (r.county||'MIAMI-DADE')==='MIAMI-DADE');
  const nonMD = DATA.filter(r => (r.county||'')!=='MIAMI-DADE' && (r.county||''));
  return {
    flaggedN: flagged.length, lienN: lien.length, fcN: fc.length,
    lienChip: lien.length ? _codeLienChip(lien[0]) : '',
    fcChip: fc.length ? _codeLienChip(fc[0]) : '',
    cleanChip: clean ? _codeLienChip(clean) : 'no-clean-lead',
    nonMDflagged: nonMD.filter(r => r.codeConcern).length,
    nan: flagged.some(r => _codeLienChip(r).indexOf('NaN')>-1 || _codeLienChip(r).indexOf('undefined')>-1),
    sample: lien.slice(0,2).map(r=>({c:r.case, addr:(r.addr||'').slice(0,24), n:(r.codeliens||[]).length}))
  };
}"""
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); pg=await b.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)
        d=await pg.evaluate(JS)
        rec('leads were flagged with code concerns', d['flaggedN']>0, f"{d['flaggedN']} flagged, {d['lienN']} lien, {d['fcN']} foreclosing")
        rec('CODE LIEN chip renders on lien leads', 'CODE LIEN' in d['lienChip'])
        rec('CODE FORECLOSURE chip renders on status-9 leads', 'CODE FORECLOSURE' in d['fcChip'] if d['fcN'] else True)
        rec('clean leads get NO chip', d['cleanChip']=='')
        rec('non-Miami-Dade leads never flagged (CCVIOL is MD-only)', d['nonMDflagged']==0, f"{d['nonMDflagged']} leaked")
        rec('no NaN/undefined in any chip', not d['nan'])
        rec('chip reaches the rendered DOM', 'CODE LIEN' in (await pg.content()) or 'CODE CASE' in (await pg.content()) or 'CODE FORECLOSURE' in (await pg.content()))
        rec('no JS errors', not errs, errs[:2])
        print('    sample:', d['sample'])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} code-lien checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
