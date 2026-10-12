"""lien_coverage — how much of a Miami lien chain is EVIDENCE and how much is a gap. Report-only.

WHY (2026-10-12): the board's eqstate says VERIFIED or CLEAR from whatever the owner-name search
returned. That search is keyed by surname, returns at most 500 records oldest-first, and most newer
mortgages carry no folio, so a mortgage is placed on the parcel by subdivision + surname. A design
review Alex pasted the same day put the rule plainly: better matching must not disguise incomplete
discovery. This module writes down, per chain, what the search could and could not prove, and
counts how many of today's VERIFIED / CLEAR verdicts rest on a complete, parcel-anchored search.

It changes nothing. It reads the cached chains and prints counts (no names, no case numbers, no
amounts), so the evidence can be reviewed with Alex before any verdict moves.

coverage(chain) -> {'search': ..., 'anchor': ..., 'gaps': [...], 'fact_supported': bool}

  search   complete   the search answered with records, below the 500 cap, and reached the parcel
           capped     the search returned the 500-record maximum: what is not on the page is unknown
           empty      the search answered with nothing (or an error-shaped empty answer)
           missed     the search answered but no record carried the subject folio (namesake risk)
           unknown    the chain predates the fields that say how it searched
  anchor   folio      a record in the search carries the subject folio and names its subdivision
           none       nothing tied the search to the parcel
  gaps     low_confidence             the chain's own conf is not 'ok' (>45 records, >4 open loans,
                                      or no anchor)
           lender_forecloses_separately  a lender's own foreclosure on this parcel (or one of the
                                      owner's units) was found: an open mortgage the list may miss
           subdivision_only_mortgage  an OPEN mortgage placed by subdivision + surname, no folio
           other_lien_unanchored      an open lien, judgment or warrant (not this case's own) whose
                                      row does not say it was tied to the parcel by folio
           modification_as_loan       an OPEN 'mortgage' row whose document is a modification,
                                      amendment, assumption, subordination, spreader, assignment or
                                      correction: counted as a
                                      second loan, which overstates the debt
           unpriced_instrument        an open instrument whose amount the index does not publish
           no_bank_fc_check           the chain never asked whether a lender forecloses separately
           release_inferred           a mortgage marked SATISFIED by the lender's name, its timing or
                                      a refinance, not by a release that names its book/page. A
                                      lender's PARTIAL release of a different parcel satisfies a loan
                                      this way (test_lien_traps.py), so the debt can be understated
  fact_supported   True only when the board's verdict is a FACT (clear / priced) AND the search is
                   complete AND anchored AND no gap above applies. Everything else is a ceiling
                   or a guess, whatever the board says.

BROWARD AND PALM BEACH (--county, 2026-10-12). Their chains come from broward_liens.analyze (Palm
Beach through palmbeach_liens), which has no 500 cap and no folio: Broward searches the owner's exact
name only, Palm Beach searches the parcel number and then the name, and its index publishes no
amounts. For those chains `search` is 'empty' or 'name_index', `anchor` is 'none', and three gaps
replace the Miami-only ones (subdivision placement, 500 cap, row anchors):
           name_only_search           Broward: nothing ties the records to the parcel but the name
           no_amounts_published       Palm Beach: the index carries no dollar figures at all
           other_liens_not_read       the chain predates other_liens.py (2026-10-12): a lien the
                                      index listed with no amount was dropped, not recorded
  and `release_inferred` reads the Broward row's own provenance (how == 'heur': the lender-name
  rules, not the recorder's link, satisfied it). The summary adds `kinds`: leads with an open lien
  of each kind (irs, state_tax, association, code, judgment, other), and how many are unpriced.

Run:  python lien_coverage.py                    # counts over records_liens.json
      python lien_coverage.py --days 60          # only leads selling within 60 days (leads_final.json)
      python lien_coverage.py --county broward   # broward_liens.json over broward_leads.json
      python lien_coverage.py --county palmbeach # palmbeach_liens.json over palmbeach_leads.json
"""
import argparse
import json
import os
import re
from collections import Counter

import equity_state as ES

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = 500
# every document the chain's own $0 rule says is not a loan (records_liens._UNPRICED_SKIP_RE), plus
# consolidations and extensions: priced, one of these is still not a second loan
_NOT_A_LOAN_RE = re.compile(r'MODIF|AMEND|ASSUMP|SUBORD|SPREAD|ASSIGN|RELEASE|SATIS|CORRECT|CONSOLID|EXTEN',
                            re.I)


def _search(chain):
    try:
        n = int(chain.get('nrec')) if chain.get('nrec') is not None else None
    except (TypeError, ValueError):
        n = None
    if n is None or 'parcel_found' not in chain:
        return 'unknown'
    if chain.get('capped') or chain.get('truncated') or n >= CAP:
        return 'capped'
    if n <= 0:
        return 'empty'
    if not chain.get('parcel_found'):
        return 'missed'
    return 'complete'


def _int(x):
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 1          # unreadable count: treat as present, never as zero


def _rows(x):
    return [l for l in x if isinstance(l, dict)] if isinstance(x, list) else []


COUNTIES = {'broward': ('broward_liens.json', 'broward_leads.json'),
            'palmbeach': ('palmbeach_liens.json', 'palmbeach_leads.json')}


def _county_coverage(chain, lead, county):
    """coverage() for a Broward / Palm Beach chain (broward_liens.analyze shape)."""
    try:
        state = ES.state_of(chain, lead)
    except Exception:  # noqa: BLE001
        state = 'unchecked'
    try:
        n = int(chain.get('nrec') or 0)
    except (TypeError, ValueError):
        n = 0
    search = 'name_index' if n > 0 else 'empty'
    gaps = []
    liens, others = _rows(chain.get('liens')), _rows(chain.get('other'))
    opens = [l for l in liens if l.get('st') == 'OPEN']
    if str(chain.get('conf') or '').lower() != 'ok':
        gaps.append('low_confidence')
    if chain.get('second_fc') or chain.get('second_fc_unsure'):
        gaps.append('lender_forecloses_separately')
    if county == 'broward':
        gaps.append('name_only_search')
    if county == 'palmbeach':
        gaps.append('no_amounts_published')
    if 'other_v' not in chain:
        gaps.append('other_liens_not_read')
    if any(o.get('st') == 'OPEN' and not o.get('own_case') and o.get('kind') != 'lis_pendens'
           and o.get('anchor') != 'folio' for o in others):
        gaps.append('other_lien_unanchored')
    if any(_NOT_A_LOAN_RE.search(str(l.get('doc') or '')) for l in opens):
        gaps.append('modification_as_loan')
    if _int(chain.get('mtg_open_unpriced')) > 0 or _int(chain.get('other_open_unpriced')) > 0 \
            or any(not l.get('amt') for l in opens):
        gaps.append('unpriced_instrument')
    if 'second_fc' not in chain:
        gaps.append('no_bank_fc_check')
    if any(l.get('st') == 'SATISFIED' and l.get('how') in (None, 'heur') for l in liens):
        gaps.append('release_inferred')
    return {'search': search, 'anchor': 'none', 'gaps': gaps, 'state': state, 'fact_supported': False}


def coverage(chain, lead=None, county=None):
    """The evidence behind one chain. Never raises; a non-dict chain is all gap."""
    if county in COUNTIES and isinstance(chain, dict):
        return _county_coverage(chain, lead, county)
    if not isinstance(chain, dict):
        return {'search': 'unknown', 'anchor': 'none', 'gaps': [], 'state': 'unchecked',
                'fact_supported': False}
    try:
        state = ES.state_of(chain, lead)
    except Exception:  # noqa: BLE001 -- a malformed cached chain is a gap, never a crash
        state = 'unchecked'
    search = _search(chain)
    anchor = 'folio' if (chain.get('subdiv') or chain.get('anchor')) and chain.get('parcel_found') else 'none'
    gaps = []
    liens, others = _rows(chain.get('liens')), _rows(chain.get('other'))
    opens = [l for l in liens if l.get('st') == 'OPEN']
    if str(chain.get('conf') or '').lower() != 'ok':
        gaps.append('low_confidence')              # >45 records, >4 open loans, or no anchor
    if chain.get('second_fc') or chain.get('second_fc_unsure'):
        gaps.append('lender_forecloses_separately')
    if any(l.get('anchor') == 'subdivision' for l in opens):
        gaps.append('subdivision_only_mortgage')
    # a lien other than a mortgage that rides on the parcel was placed the same way (folio, else
    # subdivision); its rows do not record which, so an open one is a gap until they do
    if any(o.get('st') == 'OPEN' and not o.get('own_case') and o.get('anchor') != 'folio' for o in others):
        gaps.append('other_lien_unanchored')
    if any(_NOT_A_LOAN_RE.search(str(l.get('doc') or '')) for l in opens):
        gaps.append('modification_as_loan')
    if _int(chain.get('mtg_open_unpriced')) > 0 or _int(chain.get('other_open_unpriced')) > 0 \
            or any(not l.get('amt') for l in opens):
        gaps.append('unpriced_instrument')
    if 'second_fc' not in chain:
        gaps.append('no_bank_fc_check')
    if any(l.get('st') == 'SATISFIED' and l.get('sat_by') != 'book/page' for l in liens) \
            or _int(chain.get('mtg_unpriced_release_inferred')) > 0:
        gaps.append('release_inferred')
    # a mortgage row from before rows carried 'anchor' cannot say how it was placed
    if opens and any('anchor' not in l for l in opens):
        gaps.append('row_anchor_unrecorded')
    ok = state in ES.FACT and search == 'complete' and anchor == 'folio' and not gaps
    return {'search': search, 'anchor': anchor, 'gaps': gaps, 'state': state, 'fact_supported': ok}


def summarize(chains, leads=None, county=None):
    """Counts only. chains: Case # -> chain. leads: Case # -> lead row (for the lender rule)."""
    by_state = Counter()
    by_search = Counter()
    gaps = Counter()
    kinds = Counter()
    fact = supported = fact_low = 0
    no_chain = sum(1 for c in (leads or {}) if c not in (chains or {}))
    for case, ch in (chains or {}).items():
        cv = coverage(ch, (leads or {}).get(case), county)
        seen = set()
        for o in _rows((ch or {}).get('other') if isinstance(ch, dict) else None):
            if o.get('st') != 'OPEN' or o.get('own_case') or o.get('kind') == 'lis_pendens':
                continue
            k = str(o.get('kind') or 'other')
            seen.add(k)
            if not o.get('amt'):
                seen.add(k + '_unpriced')
        for k in seen:
            kinds[k] += 1
        by_state[cv['state']] += 1
        if ES.low_capped(ch):
            fact_low += 1
        by_search[cv['search']] += 1
        for g in cv['gaps']:
            gaps[g] += 1
        if cv['state'] in ES.FACT:
            fact += 1
            supported += cv['fact_supported']
    return {'chains': sum(by_state.values()), 'no_chain': no_chain, 'board_fact': fact,
            'fact_supported': supported, 'fact_low': fact_low,
            'state': dict(by_state), 'search': dict(by_search), 'gaps': dict(gaps), 'kinds': dict(kinds)}


def _load(path):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--county', choices=sorted(COUNTIES), help='a Broward or Palm Beach cache instead of Miami')
    ap.add_argument('--chains')
    ap.add_argument('--leads')
    ap.add_argument('--days', type=int, default=0, help='only leads whose sale is within N days')
    a = ap.parse_args()
    _c, _l = COUNTIES.get(a.county, ('records_liens.json', 'leads_final.json'))
    a.chains = a.chains or os.path.join(HERE, _c)
    a.leads = a.leads or os.path.join(HERE, _l)
    chains = _load(a.chains)
    if not isinstance(chains, dict):
        print('lien-coverage: %s unreadable or missing' % os.path.basename(a.chains))
        return 1
    rows = _load(a.leads) or []
    leads = {(r.get('Case #') or r.get('case')): r for r in rows
             if isinstance(r, dict) and (r.get('Case #') or r.get('case'))}
    if a.days:
        keep = set()
        for c, r in leads.items():
            try:
                d = int(r.get('days_to_auction') if r.get('days_to_auction') is not None else r.get('days'))
            except (TypeError, ValueError):
                continue
            if 0 <= d <= a.days:
                keep.add(c)
        chains = {c: v for c, v in chains.items() if c in keep}
        leads = {c: v for c, v in leads.items() if c in keep}
    s = summarize(chains, leads, a.county)
    print('lien-coverage%s (counts only%s)' % (' ' + a.county if a.county else '',
                                                ', sale within %d days' % a.days if a.days else ''))
    print('  chains                 %d' % s['chains'])
    if leads:
        print('  leads with no chain (never searched, or the search failed)  %d' % s['no_chain'])
    print('  board says VERIFIED/CLEAR  %d' % s['board_fact'])
    print('  ...priced chains the common-name (low confidence) cap turned into a CEILING  %d' % s['fact_low'])
    print('  ...backed by a complete, anchored, gap-free search  %d' % s['fact_supported'])
    print('  state   ' + json.dumps(s['state'], sort_keys=True))
    print('  search  ' + json.dumps(s['search'], sort_keys=True))
    print('  gaps    ' + json.dumps(s['gaps'], sort_keys=True))
    print('  leads with an open lien, by kind  ' + json.dumps(s['kinds'], sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
