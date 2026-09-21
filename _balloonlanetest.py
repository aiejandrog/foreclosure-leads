"""Pin the BALLOON lane's composer IN A REAL BROWSER, both directions.

WHY THIS EXISTS AND WHY IT IS A BROWSER SUITE. `_autorunstoptest.py` covers the balloon work by
reading source shapes under node; that catches a deleted line, not a wrong email. The defect this
lane shipped with could only be seen by actually composing: `genEmail()` branches BAL rows to
`genBalloonEmail` (the Refi Lane investor pitch), but `genPortfolioEmail` has NO such branch, and
`_workerQueue` groups by primary email -- so an LLC holding several parcels on ONE mailbox is a
"portfolio" by that definition and drew the HOMEOWNER foreclosure letter, homeowner MARS/Reg-O
disclaimers and all, addressed to an investor who is not in foreclosure. `_cardMail`'s
`r.st !== 'BAL'` is the guard. This suite composes that exact row and reads the subject.

BOTH DIRECTIONS, and the second is the point. Asserting only that the letter is absent passes
just as happily if the fixture never reaches the portfolio branch at all -- then the guard is
untested and nobody knows. So the board is built twice from one HTML string: once as shipped, and
once with the guard (and only the guard) removed, where the homeowner letter MUST appear. If the
counterfactual stops failing, this suite is lying and says so.

CONTAINER-SAFE, unlike `_lanetest`: fake leads only, no `leads_final.json`, no `site.codes`, no
gate, no fixed port, `file://` load, everything under a temp dir. Nothing in the repo is written --
in particular it does NOT import `build_preview` as a module, because importing it rebuilds the
tracked `design-preview.html` as a side effect; only its constants are lifted, so FAKE and the
placeholder list cannot drift from the preview builder.

Playwright: this container's chromium at /opt/pw-browsers is build 1194, which is what
`playwright==1.56.0` expects. Never run `playwright install` here (see CLAUDE.md / the image).
On the laptop any recent playwright with its own browsers works.
"""
import datetime, json, os, re, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import foreclosure_leads as F
import disclaimer as _D

FAILS = []
CHECKS = []


def rec(name, ok, extra=''):
    CHECKS.append(name)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:200]) if extra else ''))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- fixture data
def preview_constants():
    """FAKE + PREVIEW_EMPTY out of build_preview.py WITHOUT running its build.

    `import build_preview` writes design-preview.html, a tracked file. A test that dirties the
    working tree is a test people stop running. So its source is executed only as far as the
    first line that starts building, which is the first top-level `tpl = `.
    """
    src = open(os.path.join(HERE, 'build_preview.py'), encoding='utf-8').read()
    m = re.search(r'^tpl\s*=', src, re.M)
    if not m:
        raise SystemExit('build_preview.py no longer starts its build with a top-level `tpl =` -- '
                         'this lift has to be re-cut, do not let the suite silently skip')
    ns = {'__name__': '_balloon_preview_lift', '__file__': os.path.join(HERE, 'build_preview.py')}
    exec(compile(src[:m.start()], 'build_preview.py(prefix)', 'exec'), ns)
    return list(ns['FAKE']), dict(ns['PREVIEW_EMPTY'])


def bal_row(case, addr, days, owner, oname, email, **kw):
    r = {
        "tier": "A", "score": 80, "st": "BAL", "case": case, "filed": 2025,
        "owners": owner, "oname": oname, "addr": addr, "mail": addr,
        "value": 540000, "judg": 0, "eq": 40, "ctype": "Bank/Mortgage", "plaintiff": "",
        "bal": {"days": days, "lender": "Coastal Hard Money LLC", "amount": 312000},
        "phones": ["3055559001"], "phdnc": [False], "phsrc": ["st"], "phbest": 0,
        "emails": [email],
        "pa": "#", "zillow": "#", "tax": "#", "auc": "#", "people": "#", "docket": "#",
    }
    r.update(kw)
    return r


def fc_row(case, addr, out_days, owner, oname, email):
    """A homeowner foreclosure row with a LIVE auction date, so it lands in a day lane today.

    build_preview's own FAKE rows are all stale-dated (their auctions are in the past), so
    `_aucPassed` makes every one of them worker-ineligible and all four homeowner lanes read 0.
    That is fine for a design preview and useless for this suite: a "no BAL row leaked into the
    ACTIVE lane" check against an empty lane passes without testing anything. These rows are
    dated off today so the comparison lanes actually hold leads.
    """
    auc = (datetime.date.today() + datetime.timedelta(days=out_days)).strftime('%m/%d/%Y')
    return {
        "tier": "A", "score": 88, "st": "FC", "case": case, "auction": auc, "days": out_days,
        "filed": 2025, "bought": 2009, "bprice": 210000,
        "owners": owner, "oname": oname, "addr": addr, "mail": addr,
        "value": 610000, "judg": 240000, "eq": 60, "hs": True, "ctype": "Bank/Mortgage",
        "plaintiff": "Coastal Savings Bank", "defs": oname,
        "phones": ["3055558001"], "phdnc": [False], "phsrc": ["st"], "phbest": 0,
        "emails": [email],
        "pa": "#", "zillow": "#", "tax": "#", "auc": "#", "people": "#", "docket": "#",
    }


def build_html(fake, preview_empty):
    tpl = F.subst_build_facts(
        open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read(),
        "2026-09-20 12:00")
    tpl = tpl.replace('__MOTIONJS__', F._motion_js())
    for k, v in preview_empty.items():
        tpl = tpl.replace(k, v)
    tpl = tpl.replace('__IDENT_EN__', F._esc_js(_D.identity('en', as_html=False)))
    tpl = tpl.replace('__IDENT_ES__', F._esc_js(_D.identity('es', as_html=False)))
    tpl = F._bake_alex_email(tpl, '_balloonlanetest')
    html = tpl.replace("__DATA__", F._esc_json(fake))
    F.assert_no_placeholders(html, '_balloonlanetest')
    return html


# The counterfactual is built by rewriting `_cardMail`'s isPort assignment so it no longer
# excludes BAL -- matched by shape, not by the exact line, so reformatting or a reworded guard
# does not turn this suite into a source-text pin. What it must NOT do is quietly skip: if the
# assignment cannot be found the counterfactual is recorded as a failure and the behavioural
# direction still runs, because "the guard could not be tested" and "the guard works" are
# different results and only one of them is safe to read as green.
ISPORT = re.compile(r'var isPort\s*=\s*[^;]*;')
UNGUARDED = 'var isPort = r._portfolio && r._portfolio.length;'

PROBE = r"""() => {
  const out = {};
  out.laneStats = {};
  ['replied','urgent','active','early','balloon'].forEach(k => {
    try { out.laneStats[k] = _laneStats(k).n; } catch(e){ out.laneStats[k] = 'ERR '+e.message; }
  });
  const doc = genMorningWorker('balloon', false);
  out.title = (doc.match(/<title>([^<]*)<\/title>/)||[])[1] || '';
  out.tabs = [...doc.matchAll(/data-lane="(\w+)"/g)].map(m=>m[1]);
  out.hasRunBar = doc.includes('id="mwrunbar"');
  out.runBarBeforeMain = doc.indexOf('id="mwrunbar"') < doc.indexOf('id="mwmain"');
  out.balloonBanner = doc.includes('notes on this contact');
  const m = doc.match(/var LANES=(\{[\s\S]*?\}), LMETA=/);
  const L = m ? JSON.parse(m[1]) : null;
  out.baked = L ? Object.keys(L) : null;
  out.cards = {};
  ['balloon','urgent','active','early','replied'].forEach(k => {
    out.cards[k] = ((L && L[k]) || []).map(c => ({
      // the worker card's case id is `c`, not `case` -- reading `c.case` gives
      // undefined, and every per-lead assertion below then prints "None" and keys
      // its dicts on null, which quietly collapses two leads into one.
      case: c.c, owner: c.owner, st: c.st,
      chip: c.chip && c.chip.t, smsHref: c.smsHref, phones: c.phones,
      portfolioN: (c.portfolio||[]).length,
      mailTo: c.mailTo, mailSubj: c.mailSubj,
      body: String(c.mailBody||'')
    }));
  });
  return out;
}"""


def probe(page, path):
    errs = []
    page.on('pageerror', lambda e: errs.append(str(e)))
    page.goto('file://' + path)
    page.wait_for_timeout(1200)
    r = page.evaluate(PROBE)
    r['_errs'] = errs
    return r


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('playwright is not installed -- this suite needs a browser and cannot be faked.')
        print('  pip install playwright==1.56.0   (matches the container chromium at /opt/pw-browsers)')
        print('  do NOT run `playwright install` in the container.')
        return 2

    fake, preview_empty = preview_constants()
    base_n = len(fake)

    # TWO parcels, ONE LLC, ONE mailbox. This is the shape that grouped into a portfolio: the
    # most valuable row in the lane and the one that drew the wrong letter.
    fake.append(bal_row('2025-000901-CA-01', '1200 NW 12TH AVE, MIAMI, FL 33136', 12,
                        'PALMWOOD HOLDINGS LLC', 'Palmwood Holdings LLC',
                        'notes@palmwoodholdings.example'))
    fake.append(bal_row('2025-000902-CA-01', '1310 NW 14TH ST, MIAMI, FL 33125', 40,
                        'PALMWOOD HOLDINGS LLC', 'Palmwood Holdings LLC',
                        'notes@palmwoodholdings.example'))
    # A single-parcel balloon row on its own mailbox, so the lane is not only portfolios.
    fake.append(bal_row('2025-000903-CA-01', '77 SW 3RD ST, MIAMI, FL 33130', -9,
                        'KEYSTONE NOTE PARTNERS LLC', 'Keystone Note Partners LLC',
                        'ops@keystonenote.example'))
    # THE CONTROL GROUP: a real homeowner holding two properties on one mailbox, sale ~20 days
    # out so it lands in ACTIVE. This is who genPortfolioEmail was written for, and the guard
    # must leave it completely alone -- otherwise the "fix" is just a composer switched off.
    fake.append(fc_row('2025-000801-CA-01', '901 NE 5TH ST, MIAMI, FL 33132', 20,
                       'DELIA R SANTOS', 'Delia Santos', 'delia.santos@example.com'))
    fake.append(fc_row('2025-000802-CA-01', '4120 SW 8TH ST, MIAMI, FL 33134', 24,
                       'DELIA R SANTOS', 'Delia Santos', 'delia.santos@example.com'))

    html = build_html(fake, preview_empty)

    found = ISPORT.findall(html)
    can_mutate = len(found) == 1 and "'BAL'" in found[0]
    rec("_cardMail's portfolio decision exists once and excludes BAL",
        can_mutate, found or 'no `var isPort = ...;` in the built board')
    ungated = ISPORT.sub(UNGUARDED, html, count=1) if can_mutate else html

    tmp = tempfile.mkdtemp(prefix='balloonlane-')
    p_gated = os.path.join(tmp, 'gated.html')
    p_ungated = os.path.join(tmp, 'ungated.html')
    open(p_gated, 'w', encoding='utf-8').write(html)
    open(p_ungated, 'w', encoding='utf-8').write(ungated)
    print('built 2 boards in %s  (%d fake leads: %d preview + 3 BAL)\n' % (tmp, len(fake), base_n))

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        try:
            g = probe(b.new_page(), p_gated)
            u = probe(b.new_page(), p_ungated)
        finally:
            b.close()

    # ---------------------------------------------------------- the lane is reachable at all
    print('--- the lane exists and is baked ---')
    rec('the board loads with no page errors', not g['_errs'], g['_errs'][:2])
    rec('all five lanes report stats',
        all(isinstance(v, int) for v in g['laneStats'].values()), g['laneStats'])
    rec('all five lanes are baked into the worker document, balloon last',
        g['baked'] == ['replied', 'urgent', 'active', 'early', 'balloon'], g['baked'])
    rec('the balloon lane has a tab a human can click', 'balloon' in (g['tabs'] or []), g['tabs'])
    rec('the run bar is in the document and outside #mwmain',
        g['hasRunBar'] and g['runBarBeforeMain'])
    rec('the document title counts lanes instead of claiming 3',
        '3 lanes' not in g['title'], g['title'])

    bal = g['cards']['balloon']
    # Three BAL rows, two mailboxes: _workerQueue groups by primary email, so the lane holds TWO
    # entries covering three parcels. One email per mailbox is the intended behaviour and is
    # exactly what made the wrong composer reachable -- the grouping stays, only the copy changed.
    rec('the three balloon rows grouped into two queue entries, one per mailbox',
        len(bal) == 2, [(c['case'], c['portfolioN']) for c in bal])
    if len(bal) != 2:
        print('\nThe fixture did not reach the lane, so nothing below is meaningful. Stopping.')
        return 1

    port = [c for c in bal if c['portfolioN'] >= 1]
    rec('the two same-mailbox parcels grouped into ONE queue entry with a portfolio',
        len(port) == 1 and port[0]['portfolioN'] == 1,
        [(c['case'], c['portfolioN']) for c in bal])
    pcard = port[0] if port else None

    # ---------------------------------------------------------- what the lane composes
    print('\n--- the composer, as shipped ---')
    for c in bal:
        rec('%s is a BAL row' % c['case'], c['st'] == 'BAL', c['st'])
        rec('%s shows a maturity chip, not a sale countdown' % c['case'],
            'MATUR' in str(c['chip']).upper() or 'WALL' in str(c['chip']).upper(), c['chip'])
        # Balloon leads are never texted: _workerCard returns '' for smsHref on BAL, so a
        # phone-only investor lands in the Call list, not the text batch. Deliberate.
        rec('%s is not textable' % c['case'], c['smsHref'] == '', repr(c['smsHref']))
        subj = str(c['mailSubj'] or '')
        rec('%s composed a real subject (the send is not silently empty)' % c['case'],
            bool(subj.strip()), subj)
        rec('%s is NOT sent the homeowner portfolio letter' % c['case'],
            'properties in your name' not in subj.lower(), subj)
        low = str(c['body'] or '').lower()
        rec('%s does not carry the homeowner identity disclosure' % c['case'],
            'not your lender' not in low, low[:120])
        rec('%s does not carry the MARS/Reg-O homeowner block' % c['case'],
            'mars' not in low and 'stop your foreclosure' not in low, low[:120])

    # ---------------------------------------------------------- the counterfactual
    print('\n--- with ONLY the guard removed, the bug must come back ---')
    if not can_mutate:
        print('  skipped: the isPort assignment could not be located, which is already a failure')
    if can_mutate:
        rec('the unguarded board also loads clean (the mutation is the guard, not a syntax error)',
            not u['_errs'], u['_errs'][:2])
        rec('the unguarded board reaches the same portfolio row',
            len([c for c in u['cards']['balloon'] if c['portfolioN'] >= 1]) == 1,
            [(c['case'], c['portfolioN']) for c in u['cards']['balloon']])
    ubal = u['cards']['balloon']
    uport = [c for c in ubal if c['portfolioN'] >= 1]
    if can_mutate and uport and pcard:
        uc = uport[0]
        usubj = str(uc['mailSubj'] or '')
        rec('WITHOUT the guard the LLC gets "Regarding N properties in your name"',
            'properties in your name' in usubj.lower(), usubj)
        rec('WITHOUT the guard the LLC gets the homeowner identity disclosure',
            'not your lender' in str(uc['body'] or '').lower(),
            str(uc['body'] or '')[:160])
        rec('the guard is what changes the subject, not the fixture',
            usubj != str(pcard['mailSubj'] or ''),
            '%r vs %r' % (pcard['mailSubj'], usubj))
        # The single-parcel rows have no portfolio, so the guard must be a no-op for them --
        # otherwise the "fix" is really just suppressing the balloon composer everywhere.
        solo_g = {c['case']: c['mailSubj'] for c in bal if c['portfolioN'] == 0}
        solo_u = {c['case']: c['mailSubj'] for c in ubal if c['portfolioN'] == 0}
        rec('the guard changes nothing for the single-parcel balloon row',
            solo_g == solo_u and len(solo_g) == 1, [solo_g, solo_u])

    # ---------------------------------------------------------- no leakage into other lanes
    print('\n--- the homeowner lanes are untouched ---')
    act = g['cards']['active']
    hport = [c for c in act if c['portfolioN'] >= 1]
    rec('the homeowner control group reached the ACTIVE lane as one portfolio entry',
        len(hport) == 1 and hport[0]['portfolioN'] == 1,
        [(c['case'], c['portfolioN']) for c in act])
    if hport:
        hs = str(hport[0]['mailSubj'] or '')
        # If this ever stops saying it, the homeowner portfolio letter has been broken by the
        # guard and the two-property owner is now getting one property's letter.
        rec('the homeowner portfolio STILL gets "Regarding N properties in your name"',
            'properties in your name' in hs.lower(), hs)
    for lane in ('urgent', 'active', 'early', 'replied'):
        cases = [c['case'] for c in g['cards'][lane]]
        rec('no BAL row leaked into the %s lane' % lane,
            not any(c['st'] == 'BAL' for c in g['cards'][lane]), cases[:4])
        if can_mutate:
            rec('the %s lane composes identically with and without the guard' % lane,
                [c['mailSubj'] for c in g['cards'][lane]] == [c['mailSubj'] for c in u['cards'][lane]],
                lane)

    rec("the card's balloon banner talks about notes, not consolidated properties",
        g['balloonBanner'])

    print('\n%s -- %d of %d checks failed' % ('FAILED' if FAILS else 'PASSED', len(FAILS), len(CHECKS)))
    for f in FAILS:
        print('  - ' + f)
    # The two boards are kept on a failure and only then: they are what you open to see what the
    # composer actually did, and a passing run should not leave a 3MB pair of them behind.
    if FAILS:
        print('\nthe two boards are in %s -- open them and click into the BALLOON tab' % tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
