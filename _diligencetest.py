"""Diligence stress test — CORRECTNESS, not presence.

WHY THIS EXISTS
Cursor's diligence_stress_report.md claims "Compose pass 50/50 · Live pass 15/15 · Empty-field hits:
none". Those are PRESENCE assertions: they check that a field got filled, not that the number in it
is true. Re-running the same 30 briefs with correctness assertions scored 0/30, because:

  29/30  delinquent taxes were never pulled, and `tax_certs or 0` then SUBTRACTED ZERO — silently
         asserting "no delinquent taxes" in the net-equity figure the operator would wire on. In
         Florida an unpaid bill becomes a tax certificate that survives the sale and outranks every
         other lien, so a fake zero there overstates the net by the one lien you cannot negotiate.
  11/30  plaintiff blank — root cause is a SCRAPER gap, not dig math: Miami-Dade captures the
         plaintiff on 234/239 leads, Broward on 0/189 and Palm Beach on 1/182. RealForeclose
         publishes "Plaintiff Max Bid" but never the plaintiff's name.
   3/30  net equity computed off the county roll while a real LIST PRICE sat in the same record
         (Capri: seed reasoned $40,855 off the $59,900 listing; the lead overlay replaced it with
         $47,362 off the $66,407 roll).

Run:  python _diligencetest.py
Reads the cached briefs + lead files; composes fresh money math through diligence.py so the
assertions exercise the real code path, not a fixture.
"""
import json, os, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_r = []
def rec(name, ok, note=''):
    _r.append(ok)
    print(('  PASS ' if ok else '  FAIL ') + name + (' | ' + note if note else ''))

def num(x):
    try: return float(x or 0)
    except Exception: return 0.0

def load(p):
    try: return json.load(open(os.path.join(HERE, p), encoding='utf-8'))
    except Exception: return None

def main():
    import diligence

    leads = {}
    for f in ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json'):
        d = load(f)
        if not d: continue
        for r in (d if isinstance(d, list) else d.get('leads', d)):
            k = r.get('case') or r.get('Case #')
            if k: leads[k] = r
    cache = load('diligence_cache.json') or {}
    cases = [c for c in cache if c in leads][:30]
    if len(cases) < 5:
        print('SKIP: fewer than 5 cached briefs with matching leads'); return

    print(f'composing {len(cases)} briefs through diligence.apply_lead_backed_numbers()\n')

    fake_zero_tax = 0        # the defect that scored 29/30
    unflagged_prov = 0
    tax_visible = 0
    md_briefs = 0            # tax-visibility is asserted on MD briefs only (the county with a source)
    bw_tax_visible = 0
    net_self_consistent = 0
    roll_over_list = 0
    for c in cases:
        lead = leads[c]
        # strip the money block so apply_lead_backed_numbers RECOMPUTES it — exercising the real path
        base = dict(cache[c])
        mm = dict(base.get('money_math') or {})
        mm.pop('net_equity_est', None)
        mm.pop('tax_certs', None)                      # simulate "nobody pulled the tax bill"
        base['money_math'] = mm
        out = diligence.apply_lead_backed_numbers(base, diligence._norm_lead(lead, lead.get('county', '')))
        m = out.get('money_math') or {}

        v, j, s = num(m.get('value')), num(m.get('judg')), num(m.get('surviving_senior'))
        ne = num(m.get('net_equity_est'))
        # 1. an unpulled tax must NOT be silently subtracted as a verified zero
        if m.get('tax_checked') is False and m.get('net_provisional') is not True:
            unflagged_prov += 1
        # 2. the net must equal its own stated inputs (no hidden term)
        if v and abs((v - j - s) - ne) <= 2:
            net_self_consistent += 1
        # 3. an unchecked tax must be announced, not assumed
        if m.get('tax_checked') is False and 'PROVISIONAL' in str(m.get('notes') or ''):
            pass
        else:
            if m.get('tax_checked') is False:
                fake_zero_tax += 1
        # 4. something real about taxes should still surface — but only where a source EXISTS:
        # est_annual_tax is enriched from the Miami-Dade PA; Broward briefs have no annual-tax
        # source yet, so counting them measures the known pipeline gap, not this code path.
        _cty = str(lead.get('county') or 'MIAMI-DADE').upper()
        if _cty == 'MIAMI-DADE':
            md_briefs += 1
            if num(m.get('tax_annual')) > 0 or num((out.get('taxes') or {}).get('annual')) > 0:
                tax_visible += 1
        elif num(m.get('tax_annual')) > 0 or num((out.get('taxes') or {}).get('annual')) > 0:
            bw_tax_visible += 1
        # 5. a real list price should not be ignored in favour of the roll
        lp = num(m.get('list_price'))
        if lp > 0 and abs(v - lp) > 1:
            roll_over_list += 1

    n = len(cases)
    rec('An unpulled tax is never subtracted as a verified $0', fake_zero_tax == 0,
        f'{fake_zero_tax}/{n} still silently zeroed')
    rec('A net computed without tax data is flagged PROVISIONAL', unflagged_prov == 0,
        f'{unflagged_prov}/{n} unflagged')
    rec('Net equity equals its own stated inputs', net_self_consistent == n,
        f'{net_self_consistent}/{n} self-consistent')
    rec('An annual tax figure is visible even when certificates were not pulled',
        md_briefs == 0 or tax_visible >= md_briefs * 0.5,
        f'{tax_visible}/{md_briefs} MD briefs show one · Broward briefs with one: {bw_tax_visible} '
        f'(BW annual-tax enrichment does not exist yet — the known pipeline gap, tracked, not tested)')

    # board-level: the statewide counties must carry a tax figure at all
    cov = collections.Counter()
    for f, cty in (('leads_final.json', 'MIAMI-DADE'), ('broward_leads.json', 'BROWARD'),
                   ('palmbeach_leads.json', 'PALM BEACH')):
        d = load(f)
        if not d: continue
        rows = d if isinstance(d, list) else d.get('leads', d)
        for r in rows:
            if num(r.get('value')): cov[cty + ':withvalue'] += 1
    rec('Broward and Palm Beach leads carry assessed values to price tax from',
        cov['BROWARD:withvalue'] > 0 and cov['PALM BEACH:withvalue'] > 0,
        f"BW {cov['BROWARD:withvalue']} · PB {cov['PALM BEACH:withvalue']}")

    # the plaintiff scraper gap, asserted so it cannot be forgotten.
    # FORECLOSURES ONLY (2026-08-26): a TAX DEED has no plaintiff BY DEFINITION — the county
    # forecloses on the certificate, nobody sues anybody, and the call sheet teaches exactly that.
    # 37 TAXDEED rows joined the board and dragged the blended ratio to 328/365 = 0.898, tripping
    # the >0.9 bar while FC capture sat at a perfect 328/328. Assert the thing the scraper is
    # actually responsible for.
    md = load('leads_final.json') or []
    mdrows = md if isinstance(md, list) else md.get('leads', md)
    fcrows = [r for r in mdrows if (r.get('sale_type') or 'FC') != 'TD']
    mdp = sum(1 for r in fcrows if str(r.get('plaintiff') or '').strip())
    rec('Miami-Dade still captures the plaintiff (the reference implementation)',
        fcrows and mdp / max(len(fcrows), 1) > 0.9,
        f'{mdp}/{len(fcrows)} FC ({len(mdrows) - len(fcrows)} tax deeds excluded — no plaintiff exists on those)')

    # --- comps: the trimmed median must be an HONEST median ------------------------------------
    # core[len(core)//2] is the median only on an ODD core. The 15% trim makes EVEN cores the norm
    # (default pool 14 -> core 10), so it returned the UPPER middle value on 425 of 534 conf='ok'
    # entries. The bias is one-directional — ARV never came out low, only high — and it multiplies
    # by subject sqft straight into _basisOf(), inflating worth, equity, the offer and est. profit,
    # while making the 2.5x county ceiling easier to clear so rejected ARVs got promoted.
    def _med(psfs):
        k = max(1, round(len(psfs) * 0.15))
        core = psfs[k:-k] if len(psfs) > 2 * k + 1 else psfs
        m = len(core) // 2
        return core[m] if len(core) % 2 else (core[m - 1] + core[m]) / 2
    a = [180,195,205,210,215,220,240,260,275,290,300,310,330,350]      # the audit's n=14 case
    rec('Trimmed median averages the two middle values on an even core',
        _med(a) == 250, f'n=14 core -> {_med(a)} (upper-middle bug gave 260)')
    rec('Trimmed median is unchanged on an odd core',
        _med([100,200,300]) == 200, '')
    b = sorted([200,238,314,342])                                       # the audit's live n=4 case
    rec('Small even pools are not biased to the 75th percentile',
        _med(b) == 276, f'n=4 -> {_med(b)} (bug gave 314)')

    # --- comps.py actually contains the honest median, not just this test ----------------------
    src = open(os.path.join(HERE, 'comps.py'), encoding='utf-8').read()
    rec('comps.py computes an averaged median for even cores',
        'len(core) % 2' in src and 'core[_m - 1]' in src, '')

    # --- batchdata: an association case must not elect a "foreclosing loan" --------------------
    # `fore` was computed for every case with a judgment — which every HOA case has — so the
    # elif ftype=='HOA' branch was unreachable and the whole surviving stack reported as $0.
    bsrc = open(os.path.join(HERE, 'batchdata_liens.py'), encoding='utf-8').read()
    rec('BatchData does not elect a foreclosing loan on an association case',
        "ftype != 'HOA'" in bsrc, '')

    # ACTIVE LISTING BEATS THE COUNTY ROLL. An MLS listing IS market evidence — the seller has
    # publicly said "this is what I want." The roll is a tax number, Save Our Homes-capped and
    # typically 20-40% off retail. Capri's brief was reasoning off $66,407 while $59,900 sat in the
    # same record — every "owner walks with", every seller-net, every quoted profit was framed off
    # the wrong basis. Now: when a listing exists AND diverges from the roll by more than 5%, the
    # listing IS the money-math value, and value_source records the swap.
    import diligence as _dil
    lead = {'value': 66407, 'zprice': 59900, 'judg': 15321, 'plaintiff': 'CAPRI H'}
    brief = {'money_math': {'value': 66407, 'judg': 15321}, 'title': {}}
    out = _dil.apply_lead_backed_numbers(brief, _dil._norm_lead(lead, 'PALM BEACH'))
    mm = out['money_math']
    rec('Active listing overrides the county roll in money math',
        mm.get('value') == 59900 and mm.get('value_source') == 'active_listing',
        f"value {mm.get('value')} source {mm.get('value_source')}")
    lead2 = {'value': 500000, 'zprice': 495000, 'judg': 200000, 'plaintiff': 'X'}
    brief2 = {'money_math': {'judg': 200000}, 'title': {}}
    out2 = _dil.apply_lead_backed_numbers(brief2, _dil._norm_lead(lead2, 'MIAMI-DADE'))
    rec('Roll basis is kept when the listing is within 5% of it',
        out2['money_math'].get('value') == 500000, str(out2['money_math'].get('value')))

    print(f'\n==== {sum(_r)}/{len(_r)} diligence correctness checks passed ====')
    raise SystemExit(0 if all(_r) else 1)

if __name__ == '__main__':
    main()
