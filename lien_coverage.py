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
  gaps     subdivision_only_mortgage  an OPEN mortgage placed by subdivision + surname, no folio
           modification_as_loan       an OPEN 'mortgage' row whose document is a modification,
                                      amendment, assumption, spreader or correction: counted as a
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

Run:  python lien_coverage.py                    # counts over records_liens.json
      python lien_coverage.py --days 60          # only leads selling within 60 days (leads_final.json)
"""
import argparse
import json
import os
import re
from collections import Counter

import equity_state as ES

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = 500
_NOT_A_LOAN_RE = re.compile(r'MODIF|AMEND|ASSUMP|SPREAD|CORRECT|CONSOLID|EXTEN', re.I)


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


def coverage(chain, lead=None):
    """The evidence behind one chain. Never raises; a non-dict chain is all gap."""
    if not isinstance(chain, dict):
        return {'search': 'unknown', 'anchor': 'none', 'gaps': [], 'state': 'unchecked',
                'fact_supported': False}
    state = ES.state_of(chain, lead)
    search = _search(chain)
    anchor = 'folio' if (chain.get('subdiv') or chain.get('anchor')) and chain.get('parcel_found') else 'none'
    gaps = []
    opens = [l for l in (chain.get('liens') or []) if isinstance(l, dict) and l.get('st') == 'OPEN']
    if any(l.get('anchor') == 'subdivision' for l in opens):
        gaps.append('subdivision_only_mortgage')
    if any(_NOT_A_LOAN_RE.search(str(l.get('doc') or '')) for l in opens):
        gaps.append('modification_as_loan')
    if (chain.get('mtg_open_unpriced') or 0) > 0 or (chain.get('other_open_unpriced') or 0) > 0 \
            or any(not l.get('amt') for l in opens):
        gaps.append('unpriced_instrument')
    if 'second_fc' not in chain:
        gaps.append('no_bank_fc_check')
    if any(isinstance(l, dict) and l.get('st') == 'SATISFIED' and l.get('sat_by') != 'book/page'
           for l in (chain.get('liens') or [])):
        gaps.append('release_inferred')
    # a mortgage row from before rows carried 'anchor' cannot say how it was placed
    if opens and any('anchor' not in l for l in opens):
        gaps.append('row_anchor_unrecorded')
    ok = state in ES.FACT and search == 'complete' and anchor == 'folio' and not gaps
    return {'search': search, 'anchor': anchor, 'gaps': gaps, 'state': state, 'fact_supported': ok}


def summarize(chains, leads=None):
    """Counts only. chains: Case # -> chain. leads: Case # -> lead row (for the lender rule)."""
    by_state = Counter()
    by_search = Counter()
    gaps = Counter()
    fact = supported = 0
    for case, ch in (chains or {}).items():
        cv = coverage(ch, (leads or {}).get(case))
        by_state[cv['state']] += 1
        by_search[cv['search']] += 1
        for g in cv['gaps']:
            gaps[g] += 1
        if cv['state'] in ES.FACT:
            fact += 1
            supported += cv['fact_supported']
    return {'chains': sum(by_state.values()), 'board_fact': fact, 'fact_supported': supported,
            'state': dict(by_state), 'search': dict(by_search), 'gaps': dict(gaps)}


def _load(path):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chains', default=os.path.join(HERE, 'records_liens.json'))
    ap.add_argument('--leads', default=os.path.join(HERE, 'leads_final.json'))
    ap.add_argument('--days', type=int, default=0, help='only leads whose sale is within N days')
    a = ap.parse_args()
    chains = _load(a.chains)
    if not isinstance(chains, dict):
        print('lien-coverage: %s unreadable or missing' % os.path.basename(a.chains))
        return 1
    rows = _load(a.leads) or []
    leads = {r.get('Case #'): r for r in rows if isinstance(r, dict) and r.get('Case #')}
    if a.days:
        keep = set()
        for c, r in leads.items():
            try:
                d = int(r.get('days_to_auction'))
            except (TypeError, ValueError):
                continue
            if 0 <= d <= a.days:
                keep.add(c)
        chains = {c: v for c, v in chains.items() if c in keep}
    s = summarize(chains, leads)
    print('lien-coverage (counts only%s)' % (', sale within %d days' % a.days if a.days else ''))
    print('  chains                 %d' % s['chains'])
    print('  board says VERIFIED/CLEAR  %d' % s['board_fact'])
    print('  ...backed by a complete, anchored, gap-free search  %d' % s['fact_supported'])
    print('  state   ' + json.dumps(s['state'], sort_keys=True))
    print('  search  ' + json.dumps(s['search'], sort_keys=True))
    print('  gaps    ' + json.dumps(s['gaps'], sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
