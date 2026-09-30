"""Call Mode ranking: who gets dialed first inside a band, and what equity number it uses.

WHY THIS EXISTS (2026-09-30 audit)
  (The audit also said a traced -20% lead sorted ahead of an untraced 60% one. It cannot: the
   diligence gate holds a lead whose known debt reaches its value before the sort runs. The
   first section pins that, so a change to the gate shows up here.)
  1. A judgment not yet posted made qualify() write equity_pct = 0, and Call Mode shipped that as a
     KNOWN 0% that sorted above genuinely unknown leads.
  2. Call Mode used equity against the judgment as entered while its card prints the payoff with
     interest, so the sort, the -25 floor and the "Equity Y%" line ignored the interest the card shows.

Invented leads only. Run: python _callrankingtest.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='callrank_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')   # no federal cache: no holds

import call_mode  # noqa: E402

FAIL, PASS = [], []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok   ' if ok else '  FAIL ') + name + (('  -- ' + str(detail)[:200]) if not ok and detail else ''))


def lead(case, days, **extra):
    d = {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': '11/01/2026', 'days': days,
         'tier': 'B', 'score': 50, 'phones': [{'number': '9545550101'}], 'value': 400000}
    d.update(extra)
    return d


def order(leads):
    rows, _ = call_mode.call_rows(leads, None, None)
    return [r['c'] for r in rows], {r['c']: r for r in rows}


print('\nTRACED AND UNDERWATER NEVER REACHES THE SORT')
ids, by = order([
    lead('UNTRACED-60', 20, eq=60, judg=160000),
    lead('TRACED-NEG20', 20, eq=-20, judg=480000, eqstate='priced'),
    lead('TRACED-45', 20, eq=45, judg=220000, eqstate='priced'),
    lead('UNTRACED-UNKNOWN', 20),
])
rec('a traced lead whose judgment exceeds the value is held by the diligence gate', 'TRACED-NEG20' not in by, ids)
rec('a traced lead with equity still opens the band', ids[0] == 'TRACED-45', ids)
rec('an untraced lead with unknown equity sorts after untraced leads with a number',
    ids.index('UNTRACED-60') < ids.index('UNTRACED-UNKNOWN'), ids)

print('\nJUDGMENT NOT POSTED IS UNKNOWN EQUITY, NEVER 0%')
ids, by = order([
    lead('JU', 20, eq=0, judg=0, ju=True),
    lead('KNOWN-10', 20, eq=10, judg=360000),
    lead('NO-EQ-FIELD', 20),
])
rec('a lead whose judgment is not posted ships no equity number', 'e' not in by['JU'], by['JU'].get('e'))
rec('it sorts with the unknowns, after a known 10%', ids.index('KNOWN-10') < ids.index('JU'), ids)
rec('the -25 floor does not drop a lead for an unknown judgment',
    'JU' in order([lead('JU', 20, eq=-90, judg=0, ju=True)])[1])

print('\nINTEREST ON THE JUDGMENT COMES OFF THE PERCENTAGE')
# basis 400k, judgment 300k -> 25% as scraped; payoff 340k -> 15% after interest
ids, by = order([lead('PAY', 20, eq=25, judg=300000, payoff=340000)])
rec('equity is taken against the payoff when the payoff is higher', by['PAY'].get('e') == 15.0, by['PAY'].get('e'))
ids, by = order([lead('PAY-LOW', 20, eq=25, judg=300000, payoff=290000)])
rec('a payoff below the judgment never raises the number', by['PAY-LOW'].get('e') == 25.0, by['PAY-LOW'].get('e'))
ids, by = order([lead('PAY-NOJ', 20, eq=25, judg=0, payoff=340000)])
rec('a payoff with no judgment figure still counts', by['PAY-NOJ'].get('e') == 15.0, by['PAY-NOJ'].get('e'))
ids, by = order([lead('NO-DEBT', 20, eq=25)])
rec('no debt figure on the row: the number is left as scraped', by['NO-DEBT'].get('e') == 25.0, by['NO-DEBT'].get('e'))

print('\nA RECORDED LIEN ON THE CHAIN COMES OFF TOO')
# basis 400k, judgment 200k -> 50% as scraped; a surviving 100k on the chain -> 25%
ids, by = order([lead('CHAIN', 20, eq=50, judg=200000, orsurv=100000)])
rec('equity counts the chain lien the diligence gate counts', by['CHAIN'].get('e') == 25.0, by['CHAIN'].get('e'))
rec('a lead the chain sinks is held, not ranked',
    'CHAIN-SUNK' not in order([lead('CHAIN-SUNK', 20, eq=50, judg=200000, orsurv=250000)])[1])
# basis 400k, payoff 390k -> 2.5%: a thin-but-arguable deal the -25 floor keeps
rec('a thin lead after interest still ships', 'THIN' in order([lead('THIN', 20, eq=5, judg=380000, payoff=390000)])[1])
rec('reordering by interest: 25%->15% falls below a 20% lead with no interest',
    order([lead('PAY', 20, eq=25, judg=300000, payoff=340000),
           lead('TWENTY', 20, eq=20, judg=320000)])[0] == ['TWENTY', 'PAY'])

print('\nEDGES')
ids, by = order([lead('JU-ALIAS', 20, eq=0, judg=0, judgment_unknown=True)])
rec('judgment_unknown is read the same as ju', 'e' not in by['JU-ALIAS'], by['JU-ALIAS'].get('e'))
# ARV 500k accepted (conf ok, within 0.7-2.5x value): basis 500k, payoff 300k -> 40%, but the
# scraped 25% against the county value is lower, so 25% stands
ids, by = order([lead('ARV-UP', 20, eq=25, judg=300000, arv=500000, arvconf='ok')])
rec('an ARV above the county value never raises the number', by['ARV-UP'].get('e') == 25.0, by['ARV-UP'].get('e'))
# ARV 320k accepted: basis 320k, judgment 300k -> 6.2%
ids, by = order([lead('ARV-DN', 20, eq=25, judg=300000, arv=320000, arvconf='ok')])
rec('an accepted ARV below the county value lowers it, as on the board',
    by['ARV-DN'].get('e') == 6.2, by['ARV-DN'].get('e'))
os.environ['DEALFLOW_DILIGENCE_GATE'] = 'off'
try:
    kept = order([lead('SUNK-OFF', 20, eq=5, judg=380000, payoff=520000)])[1]
finally:
    os.environ.pop('DEALFLOW_DILIGENCE_GATE', None)
rec('with the diligence gate off, the -25 floor drops a lead sunk by interest', 'SUNK-OFF' not in kept, list(kept))
cov = call_mode.coverage_rows([lead('COV-JU', 20, eq=0, judg=0, ju=True),
                               lead('COV-PAY', 20, eq=25, judg=300000, payoff=340000)], set())
cov = {r.get('c'): r for r in (cov[0] if isinstance(cov, tuple) else cov)}
rec('the coverage list ships no equity for an unposted judgment', cov.get('COV-JU', {}).get('e') is None, cov.get('COV-JU'))
rec('the coverage list shows the same after-interest equity as the dial queue',
    cov.get('COV-PAY', {}).get('e') == 15.0, cov.get('COV-PAY'))

print('\nTHE CARD SAYS WHEN A LIEN WAS TAKEN OFF')
ids, by = order([lead('EL', 20, eq=50, judg=200000, orsurv=100000),
                 lead('NO-EL', 20, eq=25, judg=300000, payoff=340000)])
rec('a lead with a chain lien off its equity ships that lien', by['EL'].get('el') == 100000, by['EL'].get('el'))
rec('a lead with only interest off it ships no lien note', by['NO-EL'].get('el') is None, by['NO-EL'].get('el'))
rec('the card renders the lien note', 'recorded lien, indicative' in open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read())
ids, by = order([lead('LOW', 20, eq=50, judg=200000, orsurv=100000, orconf='low')])
rec('a low-confidence chain does not rank a lead down', by['LOW'].get('e') == 50.0 and by['LOW'].get('el') is None,
    (by['LOW'].get('e'), by['LOW'].get('el')))

print('\n==== %d FAIL(S) ====' % len(FAIL) if FAIL else '\n==== all %d Call Mode ranking checks passed ====' % len(PASS))
sys.exit(1 if FAIL else 0)
