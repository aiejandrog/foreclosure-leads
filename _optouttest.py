"""The server opt-out ledger must suppress on EVERY device, not just the one that imported it."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
import paths as P
SRC = pathlib.Path(P.TWIN)
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))

JS = r"""() => {
  const o = {};
  o.ledger = Object.keys(SERVER_OPTOUTS || {});
  o.ledgerN = o.ledger.length;
  const c = o.ledger[0];
  o.case = c;
  const n = notes[c] || {};
  o.status = n.status; o.optout = n.optout; o.hasLog = !!(n.optlog||[]).length;
  const r = DATA.find(x => x.case === c);
  o.leadFound = !!r;
  if (r) {
    // every suppression path must now refuse this lead
    o.docGate      = _docGate(r).ok;                       // must be false
    o.workerElig   = _workerEligible(r);                   // must be false
    o.funnelStage  = _funnelStage(r);                      // must be 'out'
    o.isOptedOut   = typeof _isOptedOut === 'function' ? _isOptedOut(n) : null;
    o.inView       = view().some(x => x.case === c);
  }
  return o;
}"""

# Visible is CORRECT (hideDead defaults off — you want to know the lead exists so you do not
# re-research it). What matters is that it is visibly MARKED, not silently normal.
# SEPARATE PASS, driven through the board's own search box: the table lazy-renders (~124 rows of
# ~1900), so scanning document rows finds nothing when the opted-out lead sorts deep — this check
# spent a run FAILING with 'row-not-rendered' while the board was actually fine (verified
# 2026-08-26: idx 1880 of 1898, 124 rendered; searched row renders AND carries the mark). The
# guarantee we care about is "when a human LOOKS at this lead, it is marked" — searching is how
# a human looks.
JS_MARK = r"""(c) => {
  const q = document.querySelector('#q,#search,input[type=search]');
  if(!q) return 'no-search-box';
  q.value = c; q.dispatchEvent(new Event('input', {bubbles:true}));
  return 'typed';
}"""
JS_MARK2 = r"""(c) => {
  const tr = Array.from(document.querySelectorAll('tbody tr')).find(t => t.innerHTML.indexOf(c) > -1);
  if(!tr) return 'row-not-rendered-even-after-search';
  const t = tr.innerText.toUpperCase();
  return (t.indexOf('DO NOT CONTACT') > -1 || t.indexOf('OPT') > -1 || t.indexOf('DNC') > -1) ? 'marked' : tr.innerText.slice(0,90);
}"""

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        # FRESH context = empty localStorage = a device that never imported optouts.json.
        # This is the exact scenario the bug allowed: a teammate's phone, a reinstall, a 2nd profile.
        ctx=await b.new_context(); pg=await ctx.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)
        d=await pg.evaluate(JS)
        typed = await pg.evaluate(JS_MARK, d.get('case'))
        await pg.wait_for_timeout(800)
        d['rowMarked'] = (await pg.evaluate(JS_MARK2, d.get('case'))) if typed == 'typed' else typed

        rec('server ledger reached the page', d['ledgerN']>0, f"{d['ledgerN']} opt-out(s): {d['ledger']}")
        rec('the opted-out lead is on the board', d.get('leadFound'), d.get('case'))
        rec('status forced to DO NOT CONTACT on a FRESH device', d.get('status')=='DO NOT CONTACT', d.get('status'))
        rec('optout date carried over', bool(d.get('optout')), d.get('optout'))
        rec('an audit entry was written', d.get('hasLog'))
        rec('_isOptedOut agrees', d.get('isOptedOut') is True)
        rec('Doc Room refuses the lead', d.get('docGate') is False)
        rec('Morning Worker will not queue it', d.get('workerElig') is False)
        rec('funnel puts it in OUT', d.get('funnelStage')=='out', d.get('funnelStage'))
        rec('still VISIBLE on the board (correct — you must not re-research it)', d.get('inView') is True)
        rec('...but visibly marked as do-not-contact', d.get('rowMarked')=='marked', d.get('rowMarked'))
        rec('no JS errors', not errs, errs[:2])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} opt-out ledger checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
