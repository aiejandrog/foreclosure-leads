"""Verify the three-lane worker + daily cap against the real board data."""
import asyncio, json, os, pathlib
from playwright.async_api import async_playwright

SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))

JS = r"""() => {
  const out = {};
  out.laneCounts = {urgent:_laneCount('urgent'), active:_laneCount('active'), early:_laneCount('early')};
  out.cap = _capState();
  out.queues = {};
  ['urgent','active','early'].forEach(k => {
    const q = _workerQueue(k);
    out.queues[k] = {
      n: q.length,
      sample: q.slice(0,2).map(r => ({c:r.case, days:(typeof r.days==='number'&&r.days>=0)?r.days:null, st:r.st, tier:r.tier,
                                      ph:(r.phones||[]).length, em:(r.emails||[]).length}))
    };
  });
  // lane predicates must be mutually exclusive — a lead in two lanes gets contacted twice
  const seen = {}, dupes = [];
  ['urgent','active','early'].forEach(k => _workerQueue(k).forEach(r => {
    if (seen[r.case]) dupes.push(r.case + ' in ' + seen[r.case] + ' and ' + k);
    seen[r.case] = k;
  }));
  out.dupes = dupes;
  // suppression must hold in every lane
  const bad = [];
  ['urgent','active','early'].forEach(k => _workerQueue(k).forEach(r => {
    if (r.sibclaimed) bad.push(r.case+' sibclaimed');
    if (r.saleBkAct)  bad.push(r.case+' active BK stay');
    if (!((r.phones||[]).length || (r.emails||[]).length)) bad.push(r.case+' unreachable');
  }));
  out.suppressionLeaks = bad;
  // the doc must actually build for every lane, and carry the lane furniture
  out.docs = {};
  ['urgent','active','early'].forEach(k => {
    const h = genMorningWorker(k);
    out.docs[k] = {len:h.length, tabs:(h.match(/class="mwlane/g)||[]).length,
                   capMeter:h.indexOf('id="mwcap"')>-1, on:h.indexOf('mwlane on')>-1,
                   nulld:h.indexOf('(nulld)')>-1};
  });
  // EARLY email must not render an empty sale date
  const early = _workerQueue('early')[0];
  if (early) {
    const row = DATA.filter(r => r.case===early.case)[0];
    const doc = genEmail(row, true);
    out.earlyEmail = {case:row.case, auction:row.auction||'',
                      blankDate: /scheduled for \.|subasta el \./.test(doc),
                      hasEarlyCopy: doc.indexOf('No auction date has been set yet')>-1};
  }
  return out;
}"""

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri())
        await pg.wait_for_timeout(2500)
        res = await pg.evaluate(JS)
        await b.close()
        print(json.dumps(res, indent=1))
        if errs: print('PAGE ERRORS:', errs)

asyncio.run(main())
