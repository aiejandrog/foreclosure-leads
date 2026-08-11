"""Funnel + LP-address verification: stage assignment, equity ranking, clock compression, UI."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
SHOTS = pathlib.Path(os.environ.get('TEMP','.'))/'dealflow_shots'; SHOTS.mkdir(exist_ok=True)
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))

JS = r"""() => {
  const c = _funnelCounts();
  const total = Object.values(c).reduce((a,b)=>a+b,0);
  // every lead must land in exactly one stage
  const unassigned = DATA.filter(r => !_funnelStage(r)).map(r=>r.case);
  // suppression must never leak out of 'out'
  const leaks = DATA.filter(r => {
    const s=_funnelStage(r);
    if(s==='out') return false;
    const n=notes[r.case]||{};
    return r.sibclaimed || r.saleBkAct || n.optout;
  }).map(r=>r.case);
  // clock compression: nothing inside 7 days may sit in LETTER
  const badLetter = DATA.filter(r => {
    if(_funnelStage(r)!=='letter') return false;
    const d=_saleDays(r); return d!=null && d < MAIL_STAGE_DAYS;
  }).map(r=>({c:r.case, d:_saleDays(r)}));
  // urgent must be exactly the <=7day live leads that aren't suppressed/warm
  const urgentBad = DATA.filter(r => {
    if(_funnelStage(r)!=='urgent') return false;
    const d=_saleDays(r); return d==null || d>URGENT_DAYS || d<0;
  }).map(r=>r.case);
  // equity ordering inside a stage
  const callRows = _funnelRows('call');
  let eqOrdered = true;
  for(let i=1;i<callRows.length;i++){ if(_netEqOf(callRows[i-1]) < _netEqOf(callRows[i])) { eqOrdered=false; break; } }
  const buckets = _doorBuckets();
  // LP address wiring
  const lp = DATA.filter(r => r.st==='LP');
  const lpAddr = lp.filter(r => (r.addr||'').trim());
  const lpGuess = lp.filter(r => (r.addrGuess||'').trim());
  const lpBoth = lp.filter(r => (r.addr||'').trim() && (r.addrGuess||'').trim());
  const lpMismatch = lp.filter(r => r.ownerMismatch);
  return {
    counts:c, total, dataLen:DATA.length,
    unassigned: unassigned.length, leaks: leaks.slice(0,5), leakN: leaks.length,
    badLetter: badLetter.slice(0,5), badLetterN: badLetter.length,
    urgentBad: urgentBad.slice(0,5), urgentBadN: urgentBad.length,
    eqOrdered, callTop: callRows.slice(0,3).map(r=>({c:r.case, eq:Math.round(_netEqOf(r)), d:_saleDays(r)})),
    doorPlan: buckets.plan.length, doorNow: buckets.now.length, doorNowAll: buckets.nowAll.length,
    lpTotal: lp.length, lpAddr: lpAddr.length, lpGuess: lpGuess.length,
    lpBoth: lpBoth.length, lpMismatch: lpMismatch.length,
    lpSample: lpAddr.slice(0,2).map(r=>({c:r.case, a:r.addr, f:r.folio}))
  };
}"""

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)
        d = await pg.evaluate(JS)

        rec('every lead lands in exactly one stage', d['unassigned']==0 and d['total']==d['dataLen'],
            f"{d['total']} staged / {d['dataLen']} leads, {d['unassigned']} unassigned")
        rec('suppressed leads never leak out of OUT', d['leakN']==0, d['leaks'])
        rec('nothing inside the mail window sits in LETTER', d['badLetterN']==0, d['badLetter'])
        rec('URGENT holds only live leads inside 7 days', d['urgentBadN']==0, d['urgentBad'])
        rec('CALL is ordered by net equity, descending', d['eqOrdered'],
            ' → '.join(f"${x['eq']:,}" for x in d['callTop']))
        rec('door work splits into a plannable route vs emergency', d['doorNow']<=3,
            f"route {d['doorPlan']} · emergency {d['doorNow']} shown of {d['doorNowAll']}")
        print(f"    counts: {d['counts']}")

        # LP address wiring. NOT an exact count — that froze at 85 in the single-county (MD-only) era
        # and broke the moment Broward+PB LP came online. The invariant is "a healthy share of LP
        # leads resolve to a high-confidence address," so assert a floor + sane ceiling instead.
        rec('LP leads got high-confidence addresses', 60 <= d['lpAddr'] <= d['lpTotal'],
            f"{d['lpAddr']} of {d['lpTotal']} ({round(100*d['lpAddr']/max(1,d['lpTotal']))}%)")
        rec('advisory addresses stay OUT of the addr field', d['lpBoth']==0,
            f"{d['lpGuess']} advisory, {d['lpBoth']} contaminating addr")
        rec('owner-mismatch flag survived into the board', d['lpMismatch']>0, f"{d['lpMismatch']} flagged")
        print(f"    sample: {d['lpSample']}")

        # UI
        chips = await pg.locator('.fchip').count()
        rec('funnel chips render', chips>=5, f'{chips} chips')
        labels = await pg.locator('#funnelbar').inner_text()
        rec('no NaN/undefined in the funnel bar', 'NaN' not in labels and 'undefined' not in labels,
            labels.replace('\n',' ')[:90])

        before = await pg.locator('#stats').inner_text()
        await pg.locator('.fchip[data-fstage="call"]').click(); await pg.wait_for_timeout(700)
        after = await pg.locator('#stats').inner_text()
        rec('tapping CALL filters the board', before!=after, after.split('·')[0].strip())
        rec('active chip is marked', 'on' in (await pg.locator('.fchip[data-fstage="call"]').get_attribute('class')))
        pill = await pg.locator('#activefilters').inner_text()
        rec('a removable pill appears so he is never stranded', 'Funnel' in pill, pill.replace('\n',' ')[:70])
        await pg.screenshot(path=str(SHOTS/'funnel.png'))

        await pg.locator('.fchip[data-fstage="call"]').click(); await pg.wait_for_timeout(600)
        rec('tapping the active chip toggles it back off',
            (await pg.locator('#stats').inner_text())==before)

        rec('no JS errors', not errs, errs[:2])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} funnel checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
