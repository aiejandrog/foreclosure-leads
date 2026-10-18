"""Value coverage: free sources join, and the board counts any of them.

Synthetic case numbers and parcel ids only. No names, addresses, phones, or emails.

Run:  python _valuejointest.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import comps
import publish_guard
import value_join as V

FAILS = []


def check(name, cond, detail=''):
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


# ---- parcel ids -------------------------------------------------------------------------------
check('lettered Broward id is tried before its digits',
      V.parcel_forms('514205AA0390') == ['514205AA0390', '5142050390'])
check('digits-only id is unchanged, leading zeros kept',
      V.parcel_forms('00424632090005670') == ['00424632090005670'])
check('stripping non-digits is not the only candidate',
      '5142050390' in V.parcel_forms('514205AA0390') and V.parcel_forms('514205AA0390')[0] != '5142050390')
check('PA deep link keeps the lettered folio',
      V.row_forms({'folio': '5142050390', 'pa': 'https://bcpa.net/RecInfo.asp?URL_Folio=514205AA0390'})[:1]
      == ['514205AA0390'] or '514205AA0390' in V.row_forms(
          {'folio': '5142050390', 'pa': 'https://bcpa.net/RecInfo.asp?URL_Folio=514205AA0390'}))


# ---- joins ------------------------------------------------------------------------------------
RF_LETTER = {'514205AA0390': {'v': 281000, 'conf': 'ok', 'url': '', 't': 0}}
row = {'case': 'CACE-99-000001', 'folio': '5142050390', 'value': 0, 'rfval': 0, 'zest': 0}
n = V.attach([row], redfin=RF_LETTER)
check('crippled folio picks up the lettered Redfin entry', row.get('rfval') == 281000 and n['rfval'] == 1, row)
check('Redfin is not copied into county value', not row.get('value'), row)

# Miami case slug must not alias onto a digit string.
SLUG = {'2025011165ca01': {'v': 999000, 'conf': 'ok', 'url': '', 't': 1}}
idx = V.index_entries(SLUG)
check('case slug is not aliased onto its digits', '202501116501' not in idx)
slug_row = {'case': '2025-011165-CA-01', 'folio': '', 'value': 0, 'rfval': 0}
other = {'case': 'CACE-99-000099', 'folio': '202501116501', 'value': 0, 'rfval': 0}
V.attach([slug_row, other], redfin=SLUG)
check('the case itself still finds its slug-keyed estimate', slug_row.get('rfval') == 999000, slug_row)
check('a different row with those digits does not', not other.get('rfval'), other)

nom = {'case': 'CACE-99-000003', 'folio': '514016130201', 'rfval': 0, 'value': 0}
V.attach([nom], redfin={'514016130201': {'v': 0, 'conf': 'nomatch', 't': 1}})
check('nomatch / zero estimate is not a value', not nom.get('rfval'))

kept = {'case': 'CACE-99-000004', 'folio': '494117190490', 'zest': 400000, 'value': 125000}
V.attach([kept], listing={'494117190490': {'z': 999}}, pa={
    'BROWARD|494117190490|': {'market_value': 1, 'parcel_id': '494117190490'}})
check('existing zest and county value are left alone', kept['zest'] == 400000 and kept['value'] == 125000, kept)

filled = {'case': 'CACE-99-000005', 'folio': '5142050390', 'value': 0, 'zest': 0}
V.attach([filled],
         listing={'514205AA0390': {'z': 270000, 's': 'OFF-MARKET'}},
         pa={'BROWARD|514205AA0390|100 NW|4': {'market_value': 281290, 'parcel_id': '514205AA0390'}})
check('lettered Zestimate fills a digit folio', filled.get('zest') == 270000, filled)
check('PA just value fills a missing county value',
      filled.get('value') == 281290 and filled.get('vsrc') == 'pa-cache', filled)
check('street is not part of the stored row', 'addr' not in filled and 'owner' not in filled)

miss_pa = {'case': 'CACE-99-000006', 'folio': '514016130201', 'value': 0}
V.attach([miss_pa], pa={'BROWARD|514016130201|': {'miss': True, 'market_value': 999}})
check('a PA cache miss does not become a value', not miss_pa.get('value'))


# ---- comps JV passthrough (no network) --------------------------------------------------------
sub = {'c': (1, 2), 'sqft': 1000, 'jv': 250000}

def _fetch(dist):
    return [{'psf': 100, 'addr': '', 'price': 1, 'yr': 2024, 'sqft': 1000},
            {'psf': 200, 'addr': '', 'price': 1, 'yr': 2024, 'sqft': 1000},
            {'psf': 300, 'addr': '', 'price': 1, 'yr': 2024, 'sqft': 1000}]

res = comps.compute(sub, 16, _fetch=_fetch)
check('comps keep the roll JV beside the ARV', res and res.get('jv') == 250000, res)
check('JV is not the ARV (median $/sqft still drives it)', res and res.get('arv') == 200000, res)
bare = {'case': 'CACE-99-000007', 'value': 0}
check('roll JV lands on a valueless row', V.note_roll_value(bare, res) and bare['value'] == 250000
      and bare['vsrc'] == 'cadastral-jv')
has = {'case': 'CACE-99-000008', 'value': 10}
check('an existing county value is not replaced by JV', V.note_roll_value(has, res) is False and has['value'] == 10)
check('note_roll_value does not write equity or tier', 'eq' not in bare and 'tier' not in bare and 'score' not in bare)


# ---- who gets a comp lookup -------------------------------------------------------------------
groups = [
    (16, [
        {'case': 'CACE-99-000010', 'folio': '514016130201'},
        {'case': 'CACE-99-000011', 'folio': '514205AA0390'},
    ]),
    ('MD', [
        {'Case #': '2099-000001-CA-01', 'Folio': '3040150170910'},
        {'Case #': '2099-000002-CA-01', 'Folio': '123'},
    ]),
]
todo = comps.select_todo(groups, {}, {}, 1_000_000, limit=0)
cases = [t[0] for t in todo]
check('lettered parcel is ahead of a digit-only one', cases[0] == 'CACE-99-000011', cases)
check('short Miami folio is not sent to PaGis', '2099-000002-CA-01' not in cases, cases)
check('13-digit Miami folio is queued', '2099-000001-CA-01' in cases)
cached = comps.select_todo(groups, {'CACE-99-000011': {'arv': 1}}, {}, 1_000_000, limit=1)
check('a cached comp is not redone, and the cap keeps one', [t[0] for t in cached] == ['CACE-99-000010'], cached)
held = {'CACE-99-000010': {'t': 1_000_000, 'why': 'no-parcel', 'forms': ['514016130201']}}
check('a fresh miss is not retried',
      'CACE-99-000010' not in [t[0] for t in comps.select_todo(groups, {}, held, 1_000_000 + 10)])
repaired = [(16, [{'case': 'CACE-99-000010', 'folio': '514205AA0390'}])]
check('a new lettered id is tried even inside the miss window',
      [t[0] for t in comps.select_todo(repaired, {}, held, 1_000_000 + 10)] == ['CACE-99-000010'])
check('miss blocks only while the same ids were tried',
      comps.miss_blocks(held['CACE-99-000010'], ['514016130201'], 1_000_000 + 10)
      and not comps.miss_blocks(held['CACE-99-000010'], ['514205AA0390', '5142050390'], 1_000_000 + 10))


# ---- census: before = best single source, after = any source ---------------------------------
# Miniature of the 2026-10-18 board. Old rule: max(arv, rfval, zest) / leads.
# 2 arv, 3 rfval, 1 zest, 6 county-only, 8 with nothing. 20 leads.
# best single = 3/20 = 15% (under 20). union = 12/20 = 60%.
board = (
    [{'case': 'CACE-99-1000%02d' % i, 'arv': 100, 'value': 0} for i in range(2)]
    + [{'case': 'CACE-99-2000%02d' % i, 'rfval': 100, 'value': 0} for i in range(3)]
    + [{'case': 'CACE-99-3000%02d' % i, 'zest': 100, 'value': 0} for i in range(1)]
    + [{'case': 'CACE-99-4000%02d' % i, 'value': 200000} for i in range(6)]
    + [{'case': 'CACE-99-5000%02d' % i, 'value': 0} for i in range(8)]
)
bits = V.census_bits(board)
old_cov = {'leads': 20, 'arv': 2, 'rfval': 3, 'zest': 1}
new_cov = dict(old_cov, pval=bits['pval'], valued=bits['valued'])
old_n, old_txt = V.valued_detail(old_cov, 20)
new_n, new_txt = V.valued_detail(new_cov, 20)
check('old census is the best single source (3/20)', old_n == 3 and 'best single' in old_txt, old_txt)
check('new census counts county value in the union (12/20)', new_n == 12 and bits['pval'] == 6, (bits, new_txt))
check('union detail names each source', 'county 6' in new_txt and 'rfval 3' in new_txt, new_txt)
check('15% would warn and 60% would pass the 20% floor',
      old_n / 20 < 0.20 and new_n / 20 >= 0.20)
print('  coverage miniature: before %s' % old_txt)
print('  coverage miniature: after  %s' % new_txt)


# ---- wiring: census is not a publish gate; the bake and the healthcheck call this module -----
check('publish_guard does not gate on valued or pval',
      'valued' not in publish_guard.FIELDS and 'pval' not in publish_guard.FIELDS)
src_fl = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
src_hc = open(os.path.join(HERE, 'healthcheck.py'), encoding='utf-8').read()
check('make_tracker fills from the sidecars', 'value_join' in src_fl and '.attach(' in src_fl)
check('make_tracker publishes valued', "'valued':" in src_fl and 'census_bits' in src_fl)
check('a comps JV is applied when the row has no county value', 'note_roll_value' in src_fl)
check('healthcheck reads the union when the board published it', 'valued_detail' in src_hc)


if FAILS:
    print('\nFAIL (%d): %s' % (len(FAILS), ', '.join(FAILS)))
    sys.exit(1)
print('\n==== value join: all checks passed ====')
