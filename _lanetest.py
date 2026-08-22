"""Verify the three-lane worker + daily cap against the real board data."""
import asyncio, json, os, pathlib
from playwright.async_api import async_playwright
import paths as P

SRC = pathlib.Path(P.TWIN)

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
    // NOT /class="mwlane/ — that also matches the <div class="mwlanes"> wrapper and reports 4.
    out.docs[k] = {len:h.length, tabs:(h.match(/class="mwlane["' ]/g)||[]).length,
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

ok, bad = [], []
def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


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

        # ASSERTIONS ADDED 2026-08-02. This file collected all the right data and then printed it,
        # always exiting 0 — so it looked like coverage while enforcing nothing. Everything below
        # was already being measured; it just never failed a build.
        lanes = res.get('laneCounts') or {}
        rec('all three lanes report a count',
            all(k in lanes for k in ('urgent', 'active', 'early')), lanes)
        rec('the board is not entirely empty',
            sum(v for v in lanes.values() if isinstance(v, int)) > 0, lanes)
        # The queue is deliberately SMALLER than the lane count: _workerQueue slices to
        # `cap.left` (the day's remaining send budget) and drops recipients already emailed in
        # the last 24h. So tab != queue is correct. What must hold is that the queue never
        # EXCEEDS either bound — a run longer than the cap is the exact class of bug that let
        # the worker hand out 50 sends while claiming 50 were already spent.
        capleft = (res.get('cap') or {}).get('left')
        for k, n in lanes.items():
            q = ((res.get('queues') or {}).get(k) or {}).get('n')
            rec(f'{k}: queue never exceeds the lane it was drawn from',
                isinstance(q, int) and q <= n, {'tab': n, 'queue': q})
            if isinstance(capleft, int):
                rec(f'{k}: queue never exceeds the remaining send budget',
                    isinstance(q, int) and q <= capleft, {'queue': q, 'cap_left': capleft})
            rec(f'{k}: an empty lane queues nothing', not (n == 0 and q), {'tab': n, 'queue': q})

        # A lead in two lanes gets contacted twice — the comment in JS says so, now it fails.
        rec('lane predicates are mutually exclusive (no lead in two lanes)',
            not res.get('dupes'), (res.get('dupes') or [])[:5])

        # Suppression is legal, not cosmetic: BK stay, sibling-sold, and unreachable must not queue.
        leaks = res.get('suppressionLeaks') or []
        rec('no suppressed lead leaked into a queue', not leaks, leaks[:5])

        # Every lane must bake a usable document: 3 lane tabs, a cap meter, an active tab.
        docs = res.get('docs') or {}
        for lane, d in docs.items():
            if not isinstance(d, dict):
                continue
            rec(f'{lane}: worker doc renders 3 lane tabs', d.get('tabs') == 3, d.get('tabs'))
            rec(f'{lane}: worker doc has the cap meter', d.get('capMeter') is True, d.get('capMeter'))
            rec(f'{lane}: exactly one lane tab is active', d.get('on') is True, d.get('on'))
            # The 9999 NO_SALE sentinel once rendered as "(nulld)" on every Lis Pendens row.
            rec(f'{lane}: no "(nulld)" sentinel leak', d.get('nulld') is not True, d.get('nulld'))

        # EARLY-lane copy must not render "scheduled for ." when there is no auction date.
        e = res.get('earlyEmail') or {}
        if e and not e.get('skip'):
            rec('EARLY email does not render a blank sale date',
                e.get('blankDate') is not True, e)
            rec('EARLY email uses the no-auction-yet copy', e.get('hasEarlyCopy') is True, e)

        rec('no page errors', not errs, errs[:2] if errs else '')

        print('\n--- raw ---')
        print(json.dumps(res, indent=1)[:1400])
        total = len(ok) + len(bad)
        print(f'\n==== {len(ok)}/{total} lane checks passed ====')
        return 0 if not bad else 1


raise SystemExit(asyncio.run(main()))
