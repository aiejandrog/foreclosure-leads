"""Morning-worker portfolio consolidation + per-recipient dedupe (added 2026-07-31).

Given a repeatable synthetic setup (three leads sharing hector14@gmail.com), asserts:
  A. _recentlyEmailedTo() reads the sent archive and returns a hit inside the 24h window.
  B. _workerQueue() folds same-email leads into ONE entry with .portfolio populated.
  C. When the address was mailed inside 24h, the WHOLE group is deferred (dropped from today).
  D. genPortfolioEmail() composes one message that names every property in the group and
     stashes it on window.__lastEmail for the worker card's gurl builder.
  E. Existing single-lead genEmail() is unchanged for leads whose email is NOT shared.
"""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
import paths as P

SRC = pathlib.Path(P.TWIN)
ok, bad = [], []
def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


SETUP = r"""() => {
  // A. Inject three synthetic leads that share a primary email, plus one lone lead. Deliberately
  //    inserted at the top of DATA so the URGENT lane picks them up regardless of what real leads
  //    exist. `days` is short (sale in 4 days) so the URGENT lane test accepts them.
  const _fmt = t => { const d = new Date(Date.now() + t*864e5);
    return String(d.getMonth()+1).padStart(2,'0')+'/'+String(d.getDate()).padStart(2,'0')+'/'+d.getFullYear(); };
  const inject = [
    // RELATIVE auction dates (2026-08-26): these were hardcoded calendar strings, and the board
    // now RECOMPUTES days from the auction string instead of trusting a baked r.days (the
    // "baked days lies" truth fix) — so once the dates aged into the past every synthetic read
    // as auction-passed and was correctly gated out of every lane. 0/15 checks could even run.
    // A fixture with a calendar date in it is a time bomb; build them off today's clock.
    {case:'TEST-A', addr:'100 A ST', owners:'DOE, JANE', oname:'JANE DOE',
     emails:['portfolio14@example.com','alt@example.com'], phones:['3055551000'],
     plaintiff:'Bank A', auction:_fmt(4), days:4, tier:'A', st:'', filed:'20260701',
     folio:'AAA'},
    {case:'TEST-B', addr:'200 B ST', owners:'DOE, JANE', oname:'JANE DOE',
     emails:['portfolio14@example.com'], phones:[],
     plaintiff:'Bank B', auction:_fmt(5), days:5, tier:'B', st:'', filed:'20260702',
     folio:'BBB'},
    {case:'TEST-C', addr:'300 C ST', owners:'DOE, JANE', oname:'JANE DOE',
     emails:['portfolio14@example.com'], phones:['3055553000'],
     plaintiff:'Bank C', auction:_fmt(6), days:6, tier:'C', st:'', filed:'20260703',
     folio:'CCC'},
    {case:'TEST-SOLO', addr:'999 Z ST', owners:'ROE, JOHN', oname:'JOHN ROE',
     emails:['solo@example.com'], phones:['3055559000'],
     plaintiff:'Bank Z', auction:_fmt(6), days:6, tier:'A', st:'', filed:'20260704',
     folio:'ZZZ'}
  ];
  // DATA is a top-level `let` (not a window property) — reassigning window.DATA does not reach
  // the closure that _workerQueue/_workerCard captured. Mutate in place with unshift so the
  // closure sees the injected leads at the front of the array.
  Array.prototype.unshift.apply(DATA, inject);
  if (typeof notes === 'undefined') { window.notes = {}; }
  // Clear any prior sent-archive so the first assertion has a clean slate.
  try { localStorage.removeItem('fcSentArchive'); } catch(e){}
  return {n: DATA.length, injected: 4, headCases: DATA.slice(0,5).map(r=>r.case)};
}"""


PROBE_QUEUE = r"""() => {
  // Raise the daily send cap for the test so real leads don't crowd TEST-SOLO out of the queue.
  window.DAILY_MAX = 500;
  const q = _workerQueue('urgent');
  const head = q.find(r => r.case === 'TEST-A' || r.case === 'TEST-B' || r.case === 'TEST-C');
  return {
    portfolioCasesInTop: q.filter(r => (r._portfolio||[]).length).slice(0,10).map(r => r.case),
    solo: q.find(r => r.case === 'TEST-SOLO') ? 'present' : 'missing',
    queueLen: q.length,
    testCaseCount: q.filter(r => r.case && r.case.startsWith('TEST-') && r.case !== 'TEST-SOLO').length,
    headSiblings: head ? (head._portfolio || []).length : -1,
    headCase: head ? head.case : null
  };
}"""


PROBE_DEDUPE = r"""() => {
  // Stamp the sent archive as if we emailed portfolio14@example.com an hour ago, then re-run
  // the queue. The whole group should be dropped; the solo lead should still be present.
  _sentAdd({d: new Date().toISOString().slice(0,10),
            t: Date.now() - 3600000,
            ts: new Date().toLocaleString(),
            c: 'TEST-A', ch: 'email', to: 'portfolio14@example.com',
            who: 'JANE DOE', addr: '100 A ST', subj: 'x', body: 'x'});
  const recent = _recentlyEmailedTo('portfolio14@example.com', 24);
  const q = _workerQueue('urgent');
  return {
    recentHit: !!recent,
    recentHours: recent ? recent.hoursAgo : null,
    portfolioStillQueued: q.some(r => (r.case||'').startsWith('TEST-') && r.case !== 'TEST-SOLO'),
    soloStillQueued: q.some(r => r.case === 'TEST-SOLO')
  };
}"""


PROBE_COMPOSE = r"""() => {
  // Reset the archive so the group is back on the queue, then invoke genPortfolioEmail on the
  // head with the two siblings.
  try { localStorage.removeItem('fcSentArchive'); } catch(e){}
  const q = _workerQueue('urgent');
  // Pin explicitly to the TEST-A synthetic portfolio -- the earlier `find first with portfolio`
  // was fragile: as the real board grows and real portfolios bubble up ahead of the synthetic
  // one (soonest-sale wins), the compose probe started measuring real portfolios instead of the
  // one this test seeded. Look up by case id so the assertion is deterministic regardless of
  // what the live board looks like today.
  const head = q.find(r => r.case === 'TEST-A');
  if(!head) return {err:'no portfolio head found'};
  window.__lastEmail = null;
  genPortfolioEmail(head, head._portfolio, true);
  const LE = window.__lastEmail || {};
  const subj = LE.subj || '';
  const body = LE.body || '';
  return {
    subj: subj,
    to: LE.to,
    mentionsAllAddrs: ['100 A ST','200 B ST','300 C ST'].every(a => body.indexOf(a) > -1),
    mentionsAllPlaintiffs: ['Bank A','Bank B','Bank C'].every(p => body.indexOf(p) > -1),
    singleGreeting: (body.match(/Hi Jane,/g)||[]).length,
    subjSaysCount: /\b3 properties\b/.test(subj),
    portfolioMeta: LE.portfolio || []
  };
}"""


PROBE_SOLO_UNAFFECTED = r"""() => {
  // A lead whose email is NOT shared must still route through the single-lead composer.
  // DATA is a top-level `let` (not a window property), so use it directly rather than window.DATA.
  window.__lastEmail = null;
  const solo = DATA.find(r => r.case === 'TEST-SOLO');
  if(!solo) return {err: 'TEST-SOLO not found in DATA'};
  genEmail(solo, true);
  const LE = window.__lastEmail || {};
  return {
    subjIsSingle: /Regarding your property at 999 Z ST/.test(LE.subj||''),
    bodyIsSingle: /Bank Z/.test(LE.body||'') && !/portfolio/i.test(LE.body||''),
    to: LE.to
  };
}"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context()
        pg = await ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri())
        await pg.wait_for_timeout(3000)

        seed = await pg.evaluate(SETUP)
        rec('setup injected 4 synthetic leads', seed.get('injected') == 4, seed)

        q = await pg.evaluate(PROBE_QUEUE)
        # Assertion: three synthetic same-email leads → one queued head, TWO siblings folded in
        rec('same-email leads fold into ONE queue slot',
            q.get('testCaseCount') == 1, q)
        rec('folded head carries TWO siblings',
            q.get('headSiblings') == 2, {'headSiblings': q.get('headSiblings'), 'head': q.get('headCase')})
        rec('solo lead passes through unaffected', q.get('solo') == 'present', q)
        # Head selection: soonest sale first (TEST-A, 4d out) should win over B (5d) and C (6d)
        rec('queue head is the SOONEST-SALE lead in the group',
            q.get('headCase') == 'TEST-A', {'head': q.get('headCase')})

        d = await pg.evaluate(PROBE_DEDUPE)
        rec('_recentlyEmailedTo hit inside 24h window', d.get('recentHit'), d)
        rec('portfolio group DEFERRED after recent send', not d.get('portfolioStillQueued'), d)
        rec('solo lead unaffected by unrelated recent send', d.get('soloStillQueued'), d)

        c = await pg.evaluate(PROBE_COMPOSE)
        rec('portfolio email subject names count of properties', c.get('subjSaysCount'), c.get('subj'))
        rec('portfolio email body lists ALL 3 addresses', c.get('mentionsAllAddrs'), c)
        rec('portfolio email body lists ALL 3 plaintiffs', c.get('mentionsAllPlaintiffs'), c)
        rec('portfolio email has ONE greeting (not per-property)',
            c.get('singleGreeting') == 1, {'greetings': c.get('singleGreeting')})
        rec('__lastEmail.portfolio captures all 3 case IDs',
            len(c.get('portfolioMeta') or []) == 3, c.get('portfolioMeta'))

        s = await pg.evaluate(PROBE_SOLO_UNAFFECTED)
        rec('solo-email lead still uses single-lead genEmail() composer',
            s.get('subjIsSingle') and s.get('bodyIsSingle'), s)

        rec('no page-level JS errors during test', not errs, errs[:3] if errs else '')

        await b.close()
        total = len(ok) + len(bad)
        print(f'\n==== {len(ok)}/{total} portfolio/dedupe checks passed ====')
        return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
