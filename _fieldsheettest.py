"""Carlos field sheet: renders every must-have, no NaN, equity matches the deal screen."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
import paths as P
SRC = pathlib.Path(P.TWIN)
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))
JS = r"""() => {
  // pick a lead with an auction date so the clock/deadline branches exercise
  const r = DATA.find(x => typeof x.days==='number' && x.days>=3 && x.addr && x.auction) || DATA[0];
  const h = genFieldSheet(r);
  const eq = _netEqOf(r), fm = '$'+Math.round(eq).toLocaleString();
  return {
    case: r.case, days: r.days,
    hasAddr: h.indexOf(r.addr.split(',')[0])>-1,
    hasAuction: !r.auction || h.indexOf(r.auction)>-1,
    hasDeadline: h.indexOf('deal deadline')>-1,
    hasOpenerEN: h.indexOf('Biscayne Solutions Group')>-1,
    hasOpenerES: h.indexOf('no soy prestamista ni abogado')>-1,
    retiredClaimEN: h.toLowerCase().indexOf('local home buyer')>-1,
    retiredClaimES: h.toLowerCase().indexOf('comprador de casas')>-1,
    hasRails: h.indexOf('501.1377')>-1 && h.indexOf('signature at the door')>-1,
    hasOutcome: h.indexOf('Said STOP')>-1 && h.indexOf('No answer')>-1,
    equityMatchesScreen: h.indexOf(fm)>-1,
    hasPrintBtn: h.indexOf('window.print()')>-1,
    nan: h.indexOf('NaN')>-1 || h.indexOf('undefined')>-1,
    // a lead with NO auction date must not print a broken clock
    dateless: (function(){ const lp=DATA.find(x=>!x.auction); return lp? genFieldSheet(lp).indexOf('No auction date set')>-1 : true; })()
  };
}"""
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); pg=await b.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)
        d=await pg.evaluate(JS)
        rec('address renders', d['hasAddr'], d['case'])
        rec('auction date renders', d['hasAuction'])
        rec('deal deadline (auction minus 3) renders', d['hasDeadline'], f"{d['days']}d")
        rec('bilingual opener present (EN + ES)', d['hasOpenerEN'] and d['hasOpenerES'])
        # REGRESSION GUARD, not a style check. 'local home buyer' / 'comprador de casas local' was
        # ordered out on 2026-08-10 because IT WAS NOT TRUE. English was rewritten that day; the
        # SPANISH line was missed and kept saying "Soy comprador de casas local" until 08-27 — an
        # untrue identity claim, in the script read aloud at the door, to the Spanish-speaking
        # homeowners who are the most likely to get that version, in the one context FS 501.1377 /
        # MARS actually police. This test asserted the RETIRED wording was PRESENT, so it demanded
        # the bug. It now fails if the claim ever comes back, in either language.
        rec('retired "local home buyer" claim absent (EN)', not d['retiredClaimEN'])
        rec('retired "comprador de casas" claim absent (ES)', not d['retiredClaimES'])
        rec('do-NOT-say rails present (501.1377 + no door signature)', d['hasRails'])
        rec('after-the-knock outcomes present', d['hasOutcome'])
        rec('net equity matches the deal screen exactly', d['equityMatchesScreen'])
        rec('Print / Save as PDF button present', d['hasPrintBtn'])
        rec('dateless lead shows "No auction date set", not a broken clock', d['dateless'])
        rec('NO NaN/undefined anywhere on the sheet', not d['nan'])
        rec('no JS errors', not errs, errs[:2])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} field-sheet checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
