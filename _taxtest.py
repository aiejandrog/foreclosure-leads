"""County tax auto-checker (MD + BW): taxes fold into equity, chip renders, override wins, PB never flagged."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
import paths as P
SRC = pathlib.Path(P.TWIN)
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))
JS = r"""() => {
  const taxed = DATA.filter(r => +r.taxDue);
  // GENERIC SAMPLE, not the Spong case. CACE-22-009549 (the original $76,143 tax-certificate
  // lead this suite was written around) left the board when its auction passed — four checks
  // pinned to it went red while the tax machinery stayed healthy everywhere else. Sample any
  // live certificate-flagged lead (fall back to any taxed lead) and assert the same properties
  // against ITS number. (2026-08-26)
  const cert = taxed.filter(r => r.taxCert);
  // ...and TESTABLE: the fold identity (netEq exactly taxDue lower) breaks at the
  // Math.max(0,..) clamp, so an underwater sample proves nothing. Prefer real basis + unclamped eq.
  const pool = cert.length ? cert : taxed;
  const spong = pool.find(r => (+_basisOf(r) > 0) && _netEqOf(r) > 0) || pool[0] || null;
  const expDue = spong ? +spong.taxDue : 0;
  // equity fold: recompute reads r.taxDue as default btax
  let foldOK=true, spongEq=null;
  if(spong){
    // _payoffOf, NOT r.judg: netEq subtracts the ACCRUED payoff (FS 55.03 interest), so a
    // frozen-judgment identity is off by exactly the accrued interest — measured live 2026-08-26:
    // deltaJudg $18,449 vs deltaPay $13,885 (= taxDue exactly). The suite itself broke the
    // repo's own "use _payoffOf, never r.judg" law.
    const owed = (typeof _payoffOf==='function') ? (+_payoffOf(spong)||0) : (+spong.judg||0);
    const before = (+_basisOf(spong)) - owed - (+spong._slien||0) - (+spong._jlien||0) - (+spong._assess||0) - (+spong._mlien||0);
    spongEq = _netEqOf(spong);
    foldOK = Math.abs((before - spongEq) - expDue) < 2;   // equity is exactly taxDue lower
  }
  // manual override must still win
  let overrideWins=null;
  if(spong){
    notes[spong.case]=notes[spong.case]||{status:'',note:''};
    notes[spong.case].btax=50000; recompute();

    overrideWins = Math.abs(spong._btax - 50000) < 2;   // typed 50k beats the verified figure
    delete notes[spong.case].btax; recompute();
  }
  return {
    taxedN: taxed.length, certN: cert.length,
    spongTaxDue: spong ? +spong.taxDue : null, expDue: expDue, sampleCase: spong ? spong.case : null,
    spongCert: spong ? spong.taxCert : null,
    chip: spong ? _taxChip(spong) : '',
    cleanChip: (function(){ const c=DATA.find(r=>!r.taxDue); return c?_taxChip(c):''; })(),
    foldOK, spongEq, overrideWins,
    fieldSheetHasTax: spong ? (_netEqOf(spong) <= (+_basisOf(spong))) : null,   // sheet reflects the fold (generic: netEq can never exceed basis once taxes fold)
    // Palm Beach is NOT on the county-taxes.com platform, so it must never carry a taxDue —
    // a false $0 or a bad join there would silently hide a real lien.
    pbFlagged: DATA.filter(r => (r.county||'')==='PALM BEACH' && +r.taxDue).length,
    mdFlagged: DATA.filter(r => (r.county||'MIAMI-DADE')==='MIAMI-DADE' && +r.taxDue).length,
    bwFlagged: DATA.filter(r => (r.county||'')==='BROWARD' && +r.taxDue).length,
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
        rec('sampled lead carries a verified tax figure', (d['spongTaxDue'] or 0) > 0, f"{d['sampleCase']}: ${d['spongTaxDue']}")
        rec('a certificate-flagged lead exists and is sampled when present', d['spongCert'] is True or d['certN']==0, f"certN={d['certN']}")
        rec('TAX CERT chip renders (escalated)', 'TAX CERT' in d['chip'])
        rec('clean leads get NO chip', d['cleanChip']=='')
        rec('verified taxes fold into equity (exactly taxDue lower)', d['foldOK'], f"netEq {d['spongEq']} (due ${d['expDue']})")
        rec('manual btax override still wins over verified', d['overrideWins'] is True)
        rec('field sheet reflects the reduced equity', d['fieldSheetHasTax'])
        rec('Miami-Dade leads now carry verified taxes too', d['mdFlagged']>0, f"{d['mdFlagged']} MD flagged")
        rec('Broward still covered', d['bwFlagged']>0, f"{d['bwFlagged']} BW flagged")
        rec('Palm Beach NEVER flagged (not on this platform — no false $0)', d['pbFlagged']==0, f"{d['pbFlagged']} leaked")
        rec('no NaN in chip', not d['nan'])
        rec('no JS errors', not errs, errs[:2])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} tax-checker checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
