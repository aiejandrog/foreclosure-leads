#!/usr/bin/env python3
"""county_plaintiffs.py — the missing PLAINTIFF for Broward + Palm Beach leads (0% -> classified).

Miami-Dade leads carry the plaintiff from the OCS docket, so make_tracker/the model already know if a
BANK is foreclosing the 1st (nothing senior survives -> equity real) vs an HOA/junior/individual (a
senior mortgage survives -> the shown equity is a trap). Broward + PB auctions never expose the
plaintiff, so those 368 leads default to "unknown senior" caution. This resolves it from the
authoritative court sources cracked by the workflow:
  - Broward -> broward_plaintiff.resolve()      (Broward Clerk CaseSearch, 2Captcha reCAPTCHA v2, ~$0.003)
  - Palm Beach -> palmbeach_plaintiff.resolve_cases()  (15th-Circuit eCaseView, HEADED browser, free)

For each resolved lead it PATCHES the county <name>_leads.json in place: sets `plaintiff`, then re-runs
the SAME equity classification county_leads.py uses (ftype/mr/eqfake/eff_eq/score/tier) so a junior-lien
lead can no longer masquerade as a 98%-equity STRONG bank deal. Cached in county_plaintiffs.json so
re-runs are incremental; capped by --limit so a run can't overspend/overrun the captcha throughput.

Run:
  python county_plaintiffs.py --near [--limit N]        # near-auction (days<=30) first  (default)
  python county_plaintiffs.py --all  [--limit N]
  python county_plaintiffs.py --county "palm beach" --limit 20
  python county_plaintiffs.py --case CACE-24-005649
"""
import argparse
import json
import os
import re

import foreclosure_leads as F   # _fc_type_plaintiff / _fc_type

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'county_plaintiffs.json')
FILES = {'BROWARD': 'broward_leads.json', 'PALM BEACH': 'palmbeach_leads.json'}
_INDIV = re.compile(r'\b(LLC|CORP|INC|BANK|ASSOC|ASSN|CONDO|HOMEOWNER|COMPANY|HOLDINGS|LP|LTD|TRUST|FUND|CAPITAL|SERVICING|NA|N\.A\.)\b', re.I)


def _num(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def reclassify(r, plaintiff, is_bank_first, case_type=''):
    """Mirror county_leads.py's equity guard, now that we KNOW the plaintiff. is_bank_first (from the
    court source, which reads the real case type too) is the authoritative senior-survives signal."""
    st = r.get('st', '')
    val = _num(r.get('value'))
    judg = _num(r.get('judg'))
    days = r.get('days', -1)
    hs = bool(r.get('hs'))
    eqp = round((val - judg) / val * 100) if val else 0
    ftype = F._fc_type_plaintiff(plaintiff) or ('HOA' if not is_bank_first and re.search(r'ASSOC|CONDO|HOMEOWNER|HOA', (plaintiff or '').upper()) else r.get('ftype', '') or F._fc_type(r.get('case', '')))
    # senior survives whenever it is NOT a bank foreclosing the 1st (the court source decided this).
    mr = (not is_bank_first)
    # individual (person) plaintiff -> the classic private/2nd-note trap; flag it for the UI warning.
    ip = bool(plaintiff) and not _INDIV.search(plaintiff) and not is_bank_first
    eqfake = mr
    judg_unknown = (st != 'TD') and (judg <= 0)
    eff_eq = 0 if (eqfake or judg_unknown) else eqp
    score = max(0, min(100, round(eff_eq) + (10 if hs else 0) + (10 if 0 <= days <= 30 else 0))) if val else 0
    tier = 'A' if (val and eff_eq >= 40 and 0 <= days <= 45) else ('B' if val and eff_eq >= 15 else 'C')
    if judg_unknown:
        tier = 'C'; score = min(score, 40)
    r['plaintiff'] = plaintiff
    r['ftype'] = ftype
    r['ctype'] = 'HOA' if ftype == 'HOA' else 'Bank/Mortgage'
    r['mr'] = mr
    r['ip'] = ip
    r['eqfake'] = eqfake
    if not r.get('no_street'):                              # don't undo a city-only cap
        r['eff_eq'] = eff_eq; r['score'] = score
        if r.get('tier') != 'C' or tier == 'C':            # never PROMOTE past an existing C disqualifier
            r['tier'] = tier
    r['warn'] = ('HOA/assoc case - senior mortgage survives, verify' if ftype == 'HOA'
                 else 'private/individual plaintiff - senior mortgage may survive, verify' if ip
                 else '')


def resolve_broward(cases, verbose=False):
    import broward_plaintiff as B
    out = {}
    for c in cases:
        try:
            d = B.resolve(c, verbose=verbose)
            if d.get('ok') or d.get('plaintiff'):
                out[c] = {'plaintiff': d.get('plaintiff', ''), 'is_bank_first': bool(d.get('isBankForeclosingFirst')),
                          'case_type': d.get('caseType', '') or d.get('case_type', '')}
                print(f"  BRO ok  {c:20} bank1st={out[c]['is_bank_first']}  {out[c]['plaintiff'][:40]}")
            else:
                print(f"  BRO --  {c:20} ({d.get('error') or 'no plaintiff'})")
        except Exception as e:
            print(f"  BRO !!  {c:20} {str(e)[:80]}")
    return out


def resolve_pb(cases, headless=False):
    import palmbeach_plaintiff as P
    out = {}
    if not cases:
        return out
    for d in P.resolve_cases(cases, headless=headless):
        c = d.get('case')
        if d.get('found') and d.get('plaintiff'):
            out[c] = {'plaintiff': d['plaintiff'], 'is_bank_first': bool(d.get('is_bank_foreclosing_first')),
                      'case_type': d.get('case_type', '')}
            print(f"  PB  ok  {c:24} bank1st={out[c]['is_bank_first']}  {out[c]['plaintiff'][:40]}")
        else:
            print(f"  PB  --  {c:24} ({d.get('note') or 'not found'})")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--near', action='store_true', help='near-auction (days<=30) only (default if neither --all nor --case)')
    ap.add_argument('--county', default='', help='"broward" | "palm beach"')
    ap.add_argument('--case', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--headed-pb', action='store_true', default=True)
    a = ap.parse_args()
    near = a.near or (not a.all and not a.case)

    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    leadsets = {}
    for cty, fn in FILES.items():
        p = os.path.join(HERE, fn)
        leadsets[cty] = (fn, json.load(open(p, encoding='utf-8'))) if os.path.exists(p) else (fn, [])

    pick = {'BROWARD': [], 'PALM BEACH': []}
    for cty, (fn, leads) in leadsets.items():
        if a.county and cty != a.county.upper():
            continue
        for r in leads:
            c = str(r.get('case') or '')
            if not c:
                continue
            if a.case and c != a.case:
                continue
            if not a.case:
                if c in cache and not a.refresh:
                    continue
                if near and not (0 <= (r.get('days') or -1) <= 30):
                    continue
                if r.get('st') == 'TD':                     # tax deeds: no mortgage survives a tax sale
                    continue
            pick[cty].append(c)
    if a.limit:
        # split the cap across the two counties, near-auction first (already filtered)
        pick['BROWARD'] = pick['BROWARD'][:a.limit]
        pick['PALM BEACH'] = pick['PALM BEACH'][:a.limit]

    n = len(pick['BROWARD']) + len(pick['PALM BEACH'])
    print(f"resolving plaintiffs: {len(pick['BROWARD'])} Broward (2Captcha), {len(pick['PALM BEACH'])} Palm Beach (headed browser)")
    if not n:
        print("nothing to resolve."); return

    got = {}
    got.update(resolve_broward(pick['BROWARD']))
    got.update(resolve_pb(pick['PALM BEACH'], headless=not a.headed_pb))
    cache.update(got)
    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=1)

    # patch the county lead files in place with plaintiff + reclassification
    patched = 0
    for cty, (fn, leads) in leadsets.items():
        changed = False
        for r in leads:
            d = cache.get(str(r.get('case') or ''))
            if d and d.get('plaintiff'):
                reclassify(r, d['plaintiff'], d['is_bank_first'], d.get('case_type', ''))
                changed = True; patched += 1
        if changed:
            json.dump(leads, open(os.path.join(HERE, fn), 'w', encoding='utf-8'), indent=1)
    print(f"\nDONE: resolved {len(got)} this run, patched {patched} county leads. -> {os.path.basename(CACHE)} + *_leads.json")
    print("Rebuild to publish: python -c \"import json,foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json',encoding='utf-8')))\"")


if __name__ == '__main__':
    main()
