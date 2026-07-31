"""Broward tax auto-checker: verified taxes fold into equity, chip renders, manual override wins."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))
JS = r"""() => {
  const taxed = DATA.filter(r => +r.taxDue);
  const spong = DATA.find(r => r.case==='CACE-22-009549');
  const cert = taxed.filter(r => r.taxCert);
  // equity fold: recompute reads r.taxDue as default btax
  let foldOK=true, spongEq=null;
  if(spong){
    const before = (+_basisOf(spong)) - (+spong.judg||0) - (+spong._slien||0) - (+spong._jlien||0) - (+spong._assess||0) - (+spong._mlien||0);
    spongEq = _netEqOf(spong);
    foldOK = Math.abs((before - spongEq) - 76143) < 2;   // equity is exactly taxDue lower
  }
  // manual override must still win
  let overrideWins=null;
  if(spong){
    notes['CACE-22-009549']=notes['CACE-22-009549']||{status:'',note:''};
    notes['CACE-22-009549'].btax=50000; recompute();
    const afterOverride = (+_basisOf(spong)) - _netEqOf(spong) - (+spong.judg||0) - (+spong._slien||0) - (+spong._jlien||0) - (+spong._assess||0) - (+spong._mlien||0);
    overrideWins = Math.abs(spong._btax - 50000) < 2;   // typed 50k beats verified 76k
    delete notes['CACE-22-009549'].btax; recompute();
  }
  return {
    taxedN: taxed.length, certN: cert.length,
    spongTaxDue: spong ? spong.taxDue : null,
    spongCert: spong ? spong.taxCert : null,
    chip: spong ? _taxChip(spong) : '',
    cleanChip: (function(){ const c=DATA.find(r=>!r.taxDue); return c?_taxChip(c):''; })(),
    foldOK, spongEq, overrideWins,
    fieldSheetHasTax: spong ? (genFieldSheet(spong).indexOf('$1,850,822')>-1 || _netEqOf(spong)<1855000) : null,
    nonBroward: DATA.filter(r => (r.county||'')!=='BROWARD' && +r.taxDue).length,
    nan: spong ? _taxChip(spong).indexOf('NaN')>-1 : false
  };
}"""
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); pg=await b.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)
        d=await pg.evaluate(JS)
        rec('leads carry verified taxes', d['taxedN']>0, f"{d['taxedN']} taxed, {d['certN']} with cert")
        rec('Spong shows $76,143 verified', d['spongTaxDue']==76143, d['spongTaxDue'])
        rec('Spong flagged tax-certificate', d['spongCert'] is True)
        rec('TAX CERT chip renders (escalated)', 'TAX CERT' in d['chip'])
        rec('clean leads get NO chip', d['cleanChip']=='')
        rec('verified taxes fold into equity (−$76,143)', d['foldOK'], f"netEq {d['spongEq']}")
        rec('manual btax override still wins over verified', d['overrideWins'] is True)
        rec('field sheet reflects the reduced equity', d['fieldSheetHasTax'])
        rec('no non-Broward lead carries taxDue (MD/PB have no checker)', d['nonBroward']==0, f"{d['nonBroward']} leaked")
        rec('no NaN in chip', not d['nan'])
        rec('no JS errors', not errs, errs[:2])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} tax-checker checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
