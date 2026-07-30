"""Dismissed foreclosure cases must leave the funnel — and CLOSED must NOT scare off live leads."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))

JS = r"""() => {
  const lp   = DATA.filter(r => r.st === 'LP');
  const dis  = DATA.filter(r => r.lpDismissed);
  const clos = DATA.filter(r => r.lpClosed);
  const auction = DATA.filter(r => r.cstatus && _saleDays(r) != null && _saleDays(r) >= 0);
  return {
    lpN: lp.length, disN: dis.length, closN: clos.length,
    // dismissed leads must be OUT of the funnel and out of the worker
    disAllOut:   dis.every(r => _funnelStage(r) === 'out'),
    disNoWorker: dis.every(r => !_workerEligible(r)),
    disChip:     dis.length ? _caseDeadChip(dis[0]) : '',
    closChip:    clos.length ? _caseDeadChip(clos[0]) : '',
    // THE REGRESSION THAT MATTERS: auction leads read CLOSED as a NORMAL post-judgment state.
    // They must NOT get a chip and must NOT leave the funnel.
    auctionN:        auction.length,
    auctionClosedN:  auction.filter(r => /CLOSED/i.test(r.cstatus)).length,
    auctionAnyChip:  auction.some(r => _caseDeadChip(r) !== ''),
    // "is OUT" is not the same as "was pushed out BY case status" — 57 auction leads were already
    // OUT for bankruptcy stays, opt-outs and claimed sales before this feature existed. The real
    // question is whether the case-status flags ever fire on an auction lead at all.
    auctionOutTotal:  auction.filter(r => _funnelStage(r) === 'out').length,
    auctionFlagged:   auction.filter(r => r.lpDismissed || r.lpClosed).length,
    funnelCounts:     _funnelCounts(),
    // live LP leads keep working
    liveLpWorkable: lp.filter(r => !r.lpDismissed && _funnelStage(r) !== 'out').length,
    nan: dis.length ? (_caseDeadChip(dis[0]).indexOf('NaN') > -1) : false
  };
}"""

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); pg=await b.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)
        d=await pg.evaluate(JS)

        rec('dismissed cases were detected', d['disN']>0, f"{d['disN']} dismissed of {d['lpN']} LP")
        rec('every dismissed case is OUT of the funnel', d['disAllOut'])
        rec('every dismissed case is out of the Morning Worker', d['disNoWorker'])
        rec('CASE DISMISSED chip renders', 'CASE DISMISSED' in d['disChip'])
        rec('closed-without-dismissal gets a softer, different chip',
            'CASE CLOSED' in d['closChip'] and 'DISMISSED' not in d['closChip'], f"{d['closN']} rows")

        # the important negative
        rec('auction leads carrying a clerk status exist', d['auctionN']>0,
            f"{d['auctionN']} with status, {d['auctionClosedN']} of them CLOSED")
        rec('NO auction lead gets a dead-case chip (CLOSED there = final judgment)',
            not d['auctionAnyChip'])
        rec('NO auction lead carries a case-dead flag at all', d['auctionFlagged']==0,
            f"{d['auctionFlagged']} flagged; {d['auctionOutTotal']} out for OTHER reasons (BK/opt-out/claimed)")
        print(f"    funnel now: {d['funnelCounts']}")
        rec('live LP leads still workable', d['liveLpWorkable']>0, f"{d['liveLpWorkable']} live")
        rec('no NaN in the chip', not d['nan'])
        rec('no JS errors', not errs, errs[:2])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} case-status checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
