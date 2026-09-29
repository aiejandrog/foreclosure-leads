"""_salehistlptest -- sale_history.py reads the Miami-Dade LIS PENDENS lane, never-read cases first.

Run:  python _salehistlptest.py     (exit 0 = pass; no network, no browser, synthetic data only)

2026-09-26: sale_history.py read leads_final.json (the Miami auction list) and nothing else, so the
~350 Miami-Dade lis pendens leads on the board's Fresh-filings lane never had a bankruptcy-stay read.
The board could not gate them (saleBkAct never set) and the send bridge's fail-closed stay gate
(stay_gate.py, #72) could only refuse every one as stay_unverified. This suite pins:
  * which LP rows are read (Miami-Dade county + a Miami-Dade civil case number, lp_leads' dedupe),
  * the order (near sales, then never-read -- auction before LP, newest filing first -- then the
    rest oldest-read first), and that --limit never starves an unread case for a re-read,
  * that an LP case lands in sale_history_cache.json in EXACTLY the auction format stay_gate reads,
  * that the LP board row gets the stay fields the board gates on, and a live read can clear them,
  * that the raw lis_pendens.json fallback is read-only.
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sale_history as S

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


# ---- docket fixtures: a clean case and one with an open petition ---------------------------------
CLEAN = [{'docketDescrition': 'Notice of Foreclosure Sale', 'eventDate': '01/05/2026'}]
STAYED = [{'docketDescrition': 'Suggestion of Bankruptcy', 'eventDate': '09/10/2026',
           'comments': 'CH 13 26-12345'}]
LIFTED = STAYED + [{'docketDescrition': 'Order Dismissing Chapter 13 Case', 'eventDate': '09/20/2026',
                    'comments': 'bankruptcy 26-12345 dismissed'}]
DOCKETS = {}
CALLS = []


def fake_fetch(session, case):
    CALLS.append(case)
    return DOCKETS.get(case, CLEAN)


S._fetch = fake_fetch
S.time.sleep = lambda s: None       # the courtesy pause is not under test

# ---- 1. which LP rows count ----------------------------------------------------------------------
check('md_civil_case: plain CA number', S.md_civil_case('2026-000123-CA-01') == '2026-000123-CA-01')
check('md_civil_case: CC number', S.md_civil_case('2026-000124-CC-05') == '2026-000124-CC-05')
check('md_civil_case: prefixed "CASE NO ..." still yields the clean number',
      S.md_civil_case('CASE NO 2026-000125-CA-01') == '2026-000125-CA-01')
check('md_civil_case: Broward / Palm Beach / blank are not Miami-Dade civil',
      not S.md_civil_case('CACE-26-013184') and not S.md_civil_case('50-2026-CA-001234-XXXA-MB')
      and not S.md_civil_case('') and not S.md_civil_case(None))


def world():
    d = tempfile.mkdtemp(prefix='shlp_')
    S.HERE = d
    S.CACHE = os.path.join(d, 'sale_history_cache.json')
    del CALLS[:]
    DOCKETS.clear()
    return d


def put(d, name, obj):
    json.dump(obj, open(os.path.join(d, name), 'w', encoding='utf-8'), indent=1)


def get(d, name):
    return json.load(open(os.path.join(d, name), encoding='utf-8'))


NOW = time.time()
OLD = NOW - 30 * 86400
V = S.CACHE_VER


def ent(a=False, t=NOW, v=V, **kw):
    e = {'s': 0, 'n': 1, 'd': 0, 'w': '', 'b': 0, 'a': a, 'bd': '', 'sl': '', 't': t, 'v': v}
    e.update(kw)
    return e


def lp(case, county='MIAMI-DADE', filed='2026-09-01', owner='TEST OWNER'):
    return {'county': county, 'st': 'LP', 'case': case, 'owners': owner, 'filedDate': filed}


def auction(case, days=40, **kw):
    t = time.localtime(NOW + days * 86400)
    r = {'Case #': case, 'AuctionDate': time.strftime('%m/%d/%Y', t), 'sale_type': kw.pop('st', 'FC')}
    r.update(kw)
    return r


d = world()
put(d, 'lp_leads.json', [
    lp('2026-000001-CA-01'),
    lp('CASE NO 2026-000002-CA-01'),
    lp('CACE-26-000003', county='BROWARD'),
    lp('2026-000004-CA-01', county='PALM BEACH'),     # a Miami-looking number under another county
    lp('LP-TESTOWNER'),                                # no case number at all
])
rows, whole, src = S.load_lp_rows()
check('lp_leads.json: only Miami-Dade rows with a Miami-Dade civil number are read',
      [c for c, _ in rows] == ['2026-000001-CA-01', '2026-000002-CA-01'], [c for c, _ in rows])
check('lp_leads.json is the writable source and the whole file (every county) is kept for the write',
      whole is not None and len(whole) == 5 and src == 'lp_leads.json')

d = world()
put(d, 'lis_pendens.json', [
    {'case': '2026-000011-CA-01', 'owner': 'TEST A', 'date': '9/1/2026'},              # no county = MD
    {'case': '2026-000011-CA-01', 'owner': 'TEST A', 'date': '9/1/2026'},              # dupe
    {'case': '2026-000012-CA-01', 'owner': '', 'date': '9/2/2026'},                    # no owner: not a lead
    {'case': 'CACE-26-000013', 'owner': 'TEST B', 'county': 'BROWARD', 'date': '9/3/2026'},
    {'case': '2026-000014-CC-05', 'owner': 'TEST C', 'county': 'MIAMI-DADE', 'date': '9/4/2026'},
])
rows, whole, src = S.load_lp_rows()
check('no lp_leads.json: raw feed read with lp_leads.build\'s dedupe (owner required, case key)',
      [c for c, _ in rows] == ['2026-000011-CA-01', '2026-000014-CC-05'], [c for c, _ in rows])
check('raw feed fallback is read-only (no whole file to write back)', whole is None and src == 'lis_pendens.json')

# ---- 2. the order --------------------------------------------------------------------------------
cache = {
    '2026-000101-CA-01': ent(t=OLD),                      # auction, read long ago
    '2026-000102-CA-01': ent(t=NOW - 10 * 86400),         # auction, read more recently
    '2026-000203-CA-01': {'s': 1, 'n': 2, 't': OLD, 'v': 3},   # LP, pre-v4 entry: no stay fields
}
leads = [auction('2026-000101-CA-01'), auction('2026-000102-CA-01'), auction('2026-000103-CA-01'),
         auction('2026-000104-CA-01', days=3), auction('2026-000105-CA-01', st='TD'),
         auction('2026-000106-CA-01')]
lp_rows = [(c, lp(c, filed=f)) for c, f in (('2026-000201-CA-01', '8/1/2026'),
                                            ('2026-000202-CA-01', '2026-09-15'),
                                            ('2026-000203-CA-01', '2026-09-01'),
                                            ('2026-000106-CA-01', '2026-06-01'))]
near = lambda r: r.get('Case #') == '2026-000104-CA-01'
order = [c for c, _, _ in S.plan(leads, lp_rows, cache, near)]
check('near sale first', order[0] == '2026-000104-CA-01', order)
check('then never-read AUCTION cases in file order', order[1:3] == ['2026-000103-CA-01', '2026-000106-CA-01'], order)
check('then never-read LP cases newest filing first (a pre-v4 entry counts as never read)',
      order[3:6] == ['2026-000202-CA-01', '2026-000203-CA-01', '2026-000201-CA-01'], order)
check('then cases already read, oldest read first', order[6:] == ['2026-000101-CA-01', '2026-000102-CA-01'], order)
check('tax-deed rows stay out', '2026-000105-CA-01' not in order)
grp = {c: rows for c, rows, _ in S.plan(leads, lp_rows, cache, near)}
check('a case on both lanes is ONE fetch carrying both rows',
      sorted(k for k, _ in grp['2026-000106-CA-01']) == ['fc', 'lp'])

# ---- 3. end to end through main() ----------------------------------------------------------------
d = world()
put(d, 'leads_final.json', [auction('2026-000301-CA-01'), auction('2026-000302-CA-01'),
                            auction('2026-000303-CA-01')])
put(d, 'sale_history_cache.json', {
    '2026-000301-CA-01': ent(t=OLD),                      # stale: due a re-read
    '2026-000302-CA-01': ent(t=OLD),                      # stale
    '2026-000303-CA-01': ent(a=True, bd='2026-08-01', t=NOW),   # fresh, active stay
})
put(d, 'lp_leads.json', [lp('2026-000401-CA-01', filed='2026-09-20'),
                         lp('2026-000402-CA-01', filed='2026-09-10'),
                         lp('CACE-26-000403', county='BROWARD')])
DOCKETS['2026-000401-CA-01'] = STAYED
S.main(['--limit', '2'])
check('--limit 2 spends both fetches on the never-read LP cases, not the stale auction re-reads',
      CALLS == ['2026-000401-CA-01', '2026-000402-CA-01'], CALLS)
c2 = get(d, 'sale_history_cache.json')
auction_keys = set(ent().keys())
check('LP cases land in the cache under their clean case number',
      '2026-000401-CA-01' in c2 and '2026-000402-CA-01' in c2)
check('CACHE FORMAT UNCHANGED: an LP entry has exactly the auction entry keys and version',
      set(c2['2026-000401-CA-01']) == auction_keys and c2['2026-000401-CA-01']['v'] == S.CACHE_VER,
      sorted(c2['2026-000401-CA-01']))
check('the stayed LP case is cached active with its filing date',
      c2['2026-000401-CA-01']['a'] is True and c2['2026-000401-CA-01']['bd'] == '2026-09-10'
      and not c2['2026-000401-CA-01']['sl'])
check('the clean LP case is cached as affirmatively clear', c2['2026-000402-CA-01']['a'] is False)
lpf = get(d, 'lp_leads.json')
check('the LP board row gets saleBkAct + saleBkD (the fields the board gates outreach on)',
      lpf[0].get('saleBkAct') is True and lpf[0].get('saleBkD') == '2026-09-10', lpf[0])
check('the clear LP row carries no stay flag', not lpf[1].get('saleBkAct'))
check('ranking fields are NOT stamped on LP rows (saleSurv / saleBK)',
      'saleSurv' not in lpf[0] and 'saleBK' not in lpf[0] and 'sale_survived' not in lpf[0])
check('the Broward LP row is written back untouched', lpf[2] == lp('CACE-26-000403', county='BROWARD'))
lf = get(d, 'leads_final.json')
check('budget spent: a FRESH auction entry later in the order is still applied (continue, not break)',
      lf[2].get('sale_bk_active') is True and lf[2].get('sale_bk_date') == '2026-08-01', lf[2])
check('stale entries past the budget are left for the next run (not re-read)',
      c2['2026-000301-CA-01']['t'] == OLD and c2['2026-000302-CA-01']['t'] == OLD)

# the next night: the stay is dismissed on the docket -> a live read clears the LP row
del CALLS[:]
DOCKETS['2026-000401-CA-01'] = LIFTED
S.main(['--case', '2026-000401-CA-01'])
c3 = get(d, 'sale_history_cache.json')['2026-000401-CA-01']
lpf = get(d, 'lp_leads.json')
check('a live read showing the dismissal clears the LP stay (cache a=False, lift date set)',
      c3['a'] is False and c3['sl'] == '2026-09-20', c3)
check('...and the LP board row loses saleBkAct and gets saleLift',
      not lpf[0].get('saleBkAct') and lpf[0].get('saleLift') == '2026-09-20', lpf[0])

# a cache hit never clears a stay already on the row (only a live read may)
d = world()
put(d, 'sale_history_cache.json', {'2026-000501-CA-01': ent(a=False, t=NOW)})
put(d, 'lp_leads.json', [dict(lp('2026-000501-CA-01'), saleBkAct=True, saleBkD='2026-09-01')])
S.main(['--limit', '5'])
check('a fresh cache entry is applied without a fetch', CALLS == [], CALLS)
check('a cache hit does not clear a stay flag already on an LP row',
      get(d, 'lp_leads.json')[0].get('saleBkAct') is True)

# --cache-only reaches LP rows too (the early-publish path)
d = world()
put(d, 'sale_history_cache.json', {'2026-000601-CA-01': ent(a=True, bd='2026-09-02', t=OLD)})
put(d, 'lp_leads.json', [lp('2026-000601-CA-01')])
S.main(['--cache-only'])
check('--cache-only stamps a cached LP stay with no fetch',
      CALLS == [] and get(d, 'lp_leads.json')[0].get('saleBkAct') is True)

# raw-feed fallback: read and cached, lis_pendens.json never written
d = world()
put(d, 'lis_pendens.json', [{'case': '2026-000701-CA-01', 'owner': 'TEST D', 'date': '9/5/2026'}])
before = open(os.path.join(d, 'lis_pendens.json'), 'rb').read()
S.main(['--limit', '5'])
check('fallback: the LP case is read and cached', CALLS == ['2026-000701-CA-01']
      and '2026-000701-CA-01' in get(d, 'sale_history_cache.json'))
check('fallback: lis_pendens.json is byte-for-byte unchanged (committed feed, never stamped)',
      open(os.path.join(d, 'lis_pendens.json'), 'rb').read() == before)
check('fallback: no lp_leads.json is invented', not os.path.exists(os.path.join(d, 'lp_leads.json')))

# pointing HERE elsewhere moves the LP files with it (what _stayfiletest relies on): a suite that
# redirects HERE and CACHE must never read -- or stamp -- the real lp_leads.json next to the code
d = world()
check('LP file paths follow HERE at call time, not import time',
      S.load_lp_rows() == ([], None, '') and not os.path.isabs(S.LP_LEADS))

# --no-lp restores the old scope
d = world()
put(d, 'leads_final.json', [auction('2026-000801-CA-01')])
put(d, 'lp_leads.json', [lp('2026-000802-CA-01')])
S.main(['--no-lp'])
check('--no-lp reads the auction list only', CALLS == ['2026-000801-CA-01'], CALLS)

# ---- 4. stay_gate.py reads what we wrote (only where #72's module is present) ---------------------
try:
    import stay_gate as G
except ImportError:
    G = None
if G is None:
    print('SKIP stay_gate round-trip: stay_gate.py is not on this branch (it arrives with #72)')
else:
    d = world()
    put(d, 'lp_leads.json', [lp('2026-000901-CA-01'), lp('2026-000902-CA-01')])
    DOCKETS['2026-000901-CA-01'] = STAYED
    S.main(['--limit', '5'])
    cp = os.path.join(d, 'sale_history_cache.json')
    check('stay_gate: the stayed LP case is refused as stay_active',
          G.check('2026-000901-CA-01', cp)['code'] == G.STAY_ACTIVE)
    check('stay_gate: the read, clear LP case clears', G.check('2026-000902-CA-01', cp)['code'] == G.CLEAR)
    check('stay_gate: an LP case never read is still refused (stay_unverified)',
          G.check('2026-000903-CA-01', cp)['code'] == G.UNVERIFIED)

print('\n%d failure(s)' % len(FAILS) if FAILS else '\nALL PASS')
sys.exit(1 if FAILS else 0)
