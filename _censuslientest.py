"""_censuslientest -- per-lien-type Miami-Dade counts in the board's plaintext coverage census.

Run:  python _censuslientest.py     (exit 0 = pass; no network, synthetic data only)

The board is encrypted and records_liens.json / code_liens.json / county_taxes.json are gitignored,
so the DEALFLOW-COVERAGE marker on line 1 of docs/index.html is the only readable account of lien
coverage. lien_census.md_counts adds Miami-Dade per-type counts to it. This suite checks:
  [1] each count against a hand-built board (mortgages, HOA, judgments, tax liens, taxes, code,
      municipal/utility, other lis pendens, unpriced), and that other counties / the balloon lane /
      a cached chain the board never attached are left out;
  [2] the census stays FLAT INTEGERS (every reader cuts it with a non-greedy {.*?}), short, and free
      of any name, case number, folio, party or amount string;
  [3] publish_guard, healthcheck and engine_drift still read the marker, and publish_guard.FIELDS
      does not gate on the new keys;
  [4] make_tracker writes them: a real build in a throwaway copy of the repo, synthetic leads.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lien_census as LC

FAILS = []


def check(name, cond, detail=''):
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


# ---- synthetic board ------------------------------------------------------------------------
# Names, parties and case numbers are deliberately distinctive so [2] can prove none leaks.
PII = ['ZZOWNER ALPHA', 'ZZOWNER BRAVO', '2099-000101-CA-01', '2099-000102-CA-01', '2099-000103-CA-01',
       '0199990000101', 'ZZHOA CONDOMINIUM ASSN', 'ZZCARD BANK', 'ZZSTREET 12']


def _o(kind, amt, doc='CLAIM OF LIEN', st='OPEN', party='', **kw):
    r = {'kind': kind, 'amt': amt, 'doc': doc, 'st': st, 'party': party or 'ZZ PARTY'}
    r.update(kw)
    return r


chains = {
    # A: everything priced -- open 1st + satisfied old loan, HOA, money judgment, IRS, City, WASD
    '2099-000101-CA-01': {
        'conf': 'ok', 'mtg_open_unpriced': 0,
        'liens': [{'st': 'OPEN', 'amt': 250000}, {'st': 'SATISFIED', 'amt': 90000}, {'st': 'SATISFIED', 'amt': 1}],
        'other': [_o('association', 4200, party='ZZHOA CONDOMINIUM ASSN'),
                  _o('judgment', 8800, doc='JUDGMENT', party='ZZCARD BANK'),
                  _o('irs', 15000, doc='FEDERAL TAX LIEN'),
                  _o('code', 1200, party='CITY OF MIAMI'),
                  _o('code', 300, party='MIAMI-DADE WATER AND SEWER'),
                  # never counted: own filing, released, a lis pendens OF THIS CASE, a waiver, a notice
                  _o('association', 9000, own_case=True),
                  _o('judgment', 5000, doc='JUDGMENT', st='RELEASED'),
                  _o('lis_pendens', None, doc='LIS PENDENS', own_case=True),
                  _o('association', 100, doc='WAIVER OF LIEN'),
                  _o('other', 700, doc='NOTICE')]},
    # B: unpriced everywhere -- open mortgage with no amount, HOA no amount, state warrant no amount,
    #    other-lienor lien no amount, another open lis pendens (a second suit)
    '2099-000102-CA-01': {
        'conf': 'low', 'mtg_open_unpriced': 1,
        'liens': [{'st': 'OPEN', 'amt': 0}, {'st': 'OPEN', 'amt': 50000}],
        'other': [_o('association', None), _o('state_tax', None, doc='WARRANT'),
                  _o('other', None, doc='CONSTRUCTION LIEN'), _o('lis_pendens', None, doc='LIS PENDENS'),
                  _o('code', None, party='CITY OF HIALEAH'), _o('code', None, party='WASD'),
                  _o('judgment', None, doc='FINAL JUDGMENT'), _o('irs', None, doc='FEDERAL TAX LIEN')]},
    # C: a cached chain the board did NOT attach to its row -> must not be counted
    '2099-000103-CA-01': {'conf': 'ok', 'liens': [{'st': 'OPEN', 'amt': 1}],
                          'other': [_o('association', 5)]},
    # D: a Broward chain -> ignored with its row
    'BW-1': {'conf': 'ok', 'liens': [{'st': 'OPEN', 'amt': 1}], 'other': [_o('irs', 5)]},
}


def _attach(d, h):
    """What make_tracker stamps from a chain (orliens only with liens; orhoa whenever a chain)."""
    if h.get('liens'):
        d['orliens'] = h['liens']; d['orconf'] = h.get('conf', '')
    d['orhoa'] = h.get('hoa_open', 0); d['orcode'] = h.get('code_open', 0); d['orirs'] = h.get('irs_open', 0)
    return d


slim = [
    _attach({'case': '2099-000101-CA-01', 'county': 'MIAMI-DADE', 'st': 'FC', 'judg': 310000,
             'owner': PII[0], 'folio': PII[5], 'addr': PII[8], 'taxDue': 4100, 'taxCert': True,
             'codeliens': [{'case': 'ZZ-CE-1', 'st': '5', 'lien': True, 'lienRef': '1/2'}]},
            chains['2099-000101-CA-01']),
    _attach({'case': '2099-000102-CA-01', 'st': 'FC', 'judg': 0, 'ju': True, 'owner': PII[1],  # county missing = MD
             'codeliens': [{'case': 'ZZ-CE-2', 'st': '2', 'lien': False}]},
            chains['2099-000102-CA-01']),
    {'case': '2099-000103-CA-01', 'county': 'MIAMI-DADE', 'st': 'FC', 'judg': 120000, 'ju': False},
    {'case': '2099-000104-CA-01', 'county': 'MIAMI-DADE', 'st': 'LP', 'judg': 0, 'taxDue': 0},
    {'case': '2099-000105-CA-01', 'county': 'MIAMI-DADE', 'st': 'LP', 'judg': 0,
     'codeliens': [{'case': 'ZZ-CE-3', 'st': '9', 'lien': True}]},
    {'case': 'TD-1', 'county': 'MIAMI-DADE', 'st': 'TD', 'judg': 0, 'taxCert': True},
    {'case': 'BAL-1', 'county': 'MIAMI-DADE', 'st': 'BAL', 'judg': 0, 'taxDue': 900},
    _attach({'case': 'BW-1', 'county': 'BROWARD', 'st': 'FC', 'judg': 1, 'taxDue': 5,
             'codeliens': [{'lien': True}]}, chains['BW-1']),
    {'case': 'PB-1', 'county': 'PALM BEACH', 'st': 'LP'},
]

print('[1] counts against a hand-built board')
c = LC.md_counts(slim, chains)
want = {
    'md': 6, 'md_lp': 2, 'md_td': 1, 'md_rd': 2,
    'md_fcj': 2, 'md_fcj_u': 1,                           # A and C priced; B unknown; LP/TD not auction FC
    'md_mo': 2, 'md_mo_n': 3, 'md_ms': 1, 'md_ms_n': 2, 'md_mo_u': 1,
    'md_hoa': 2, 'md_hoa_u': 1, 'md_mj': 2, 'md_mj_u': 1,
    'md_fed': 2, 'md_st': 1, 'md_tl_u': 1,
    'md_muni': 2, 'md_muni_u': 1, 'md_util': 2, 'md_util_u': 1, 'md_oth': 1, 'md_oth_u': 1,
    'md_lpx': 1,
    'md_taxd': 1, 'md_taxc': 2,
    'md_code': 3, 'md_codel': 2, 'md_code_a': 1, 'md_code_na': 2,
    'md_unp': 2,                                          # B (many), LP-105 (code hit, no amount)
}
# md_unp: B yes; 105 yes (code hit, no amount). A: code hit priced by the City lien -> not unpriced.
for k, v in want.items():
    check('%s == %d' % (k, v), c.get(k) == v, 'got %r' % c.get(k))
check('key set is exactly lien_census.KEYS', set(c) == set(LC.KEYS) and len(LC.KEYS) == len(set(LC.KEYS)),
      sorted(set(c) ^ set(LC.KEYS)))
check('every key carries the md_ / md prefix', all(k == 'md' or k.startswith('md_') for k in c))
check('the cached-but-unattached chain (C) adds nothing',
      LC.md_counts([slim[2]], chains)['md_rd'] == 0 and LC.md_counts([slim[2]], chains)['md_hoa'] == 0)
check('Broward / Palm Beach / balloon rows are not counted',
      LC.md_counts(slim[6:], chains) == dict.fromkeys(LC.KEYS, 0))
check('no chains file / junk rows -> zeros, no crash',
      LC.md_counts([None, 'x', {}], None)['md'] == 1 and LC.md_counts([], {}) == dict.fromkeys(LC.KEYS, 0))
check('a junk chain entry and junk list items do not crash',
      LC.md_counts([{'case': 'Q', 'orhoa': 0, 'orliens': ['x', None], 'codeliens': [None]}],
                   {'Q': {'other': ['x', None, {'kind': None}]}})['md_rd'] == 1)

print('[2] flat, short, no PII')
check('all values are plain ints', all(type(v) is int for v in c.values()), c)
BASE = {"leads": 2584, "phones": 1132, "liens": 353, "wp": 8, "arv": 315, "rfval": 427, "zest": 413,
        "bkstay": 97, "codeliens": 69, "taxes": 86, "judgdt": 324, "ownflip": 40, "docs": 0,
        "built": "2026-09-25T08:45"}
cov = dict(BASE); cov.update(c); cov['sig'] = '145d4f246ff0'
marker = '<!-- DEALFLOW-COVERAGE ' + json.dumps(cov, separators=(',', ':')) + ' -->\n'
big = dict(BASE); big.update({k: 9999999 for k in LC.KEYS}); big['sig'] = 'f' * 12
big_marker = '<!-- DEALFLOW-COVERAGE ' + json.dumps(big, separators=(',', ':')) + ' -->\n'
check('marker has no nested object', marker.count('{') == 1 and marker.count('}') == 1)
check('worst-case marker (7-digit counts) stays under 1500 chars', len(big_marker) < 1500, len(big_marker))
blob = json.dumps(c)
check('no synthetic name / case / folio / party / address in the census',
      not any(p in marker for p in PII) and not re.search(r'\d{4}-\d{6}', blob), marker[:200])
check('no string values at all in the lien counts', not re.search(r':\s*"', blob))

print('[3] the marker readers still parse it; publish gate unchanged')
import publish_guard as PG
page = marker + '<!DOCTYPE html>\n<html></html>\n'
got = PG.cov_of_text(page)
check('publish_guard.cov_of_text reads the whole census', got == cov, got)
check('publish_guard.cov_of_text reads the worst case too', PG.cov_of_text(big_marker + '<!DOCTYPE html>') == big)
check('new keys are census-only (not in publish_guard.FIELDS)', not (set(PG.FIELDS) & set(LC.KEYS)))
hm = re.search(r'DEALFLOW-COVERAGE (\{.*?\})', page[:4000])            # healthcheck.py:370 / :456
check('healthcheck regex reads it', bool(hm) and json.loads(hm.group(1)) == cov)
_ed = open(os.path.join(HERE, 'engine_drift.py'), encoding='utf-8').read()
_em = re.search(r"COV_MARK = re\.compile\(r'(.+?)'\)", _ed)
check('engine_drift.COV_MARK reads it', bool(_em) and json.loads(re.compile(_em.group(1)).search(page).group(1)) == cov)
_fl = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
_i_cov, _i_upd, _i_sig = (_fl.find("    _cov = {\n"), _fl.find('_cov.update(_md_counts(slim, rl))'),
                          _fl.find("    _cov['sig'] = "))
check('make_tracker merges the counts after the census dict and before the signature',
      0 < _i_cov < _i_upd < _i_sig, (_i_cov, _i_upd, _i_sig))
_blk = _fl[_fl.rfind('    try:', 0, _i_upd):_fl.find('\n\n', _i_upd) if _fl.find('\n\n', _i_upd) > 0 else _i_upd + 300]
check('the merge is inside try/except (a census bug never stops a publish)',
      'except Exception' in _blk and _blk.index('try:') < _blk.index('_md_counts'), _blk[:200])

print('[4] a real make_tracker build writes the counts (throwaway repo copy)')
tmp = tempfile.mkdtemp(prefix='censuslien_')
try:
    files = subprocess.run(['git', 'ls-files'], cwd=HERE, capture_output=True, text=True).stdout.split('\n')
    files = [f for f in files if f and not f.startswith('docs/')] + ['lien_census.py', 'foreclosure_leads.py']
    for f in files:
        s = os.path.join(HERE, f)
        if os.path.isfile(s):
            os.makedirs(os.path.dirname(os.path.join(tmp, f)) or tmp, exist_ok=True)
            shutil.copy2(s, os.path.join(tmp, f))
    os.makedirs(os.path.join(tmp, 'docs'), exist_ok=True)
    leads = [{'Case #': k, 'owner_clean': PII[i], 'Folio': '01-9999-000-010%d' % i, 'Address': PII[8],
              'judgment': j, 'judgment_unknown': not j, 'AuctionDate': '12/01/2026', 'days_to_auction': 20,
              'tier': 'B', 'score': 50, 'sale_type': 'FC'}
             for i, (k, j) in enumerate([('2099-000101-CA-01', 310000), ('2099-000102-CA-01', 0)])]
    json.dump(leads, open(os.path.join(tmp, 'leads_final.json'), 'w'))
    json.dump({k: chains[k] for k in ('2099-000101-CA-01', '2099-000102-CA-01')},
              open(os.path.join(tmp, 'records_liens.json'), 'w'))
    json.dump({'0199990000100': [{'case': 'ZZ-CE-1', 'st': '5', 'stLabel': 'x', 'problem': 'x', 'lien': True,
                                  'lienRef': '1/2'}]}, open(os.path.join(tmp, 'code_liens.json'), 'w'))
    json.dump({'0199990000100': {'due': 4100, 'cert': True, 'county': 'MIAMI-DADE', 'checked': '2026-09-01',
                                 'years': [{'year': 2025}]}}, open(os.path.join(tmp, 'county_taxes.json'), 'w'))
    env = dict(os.environ, DEALFLOW_NO_DESKTOP='1', DEALFLOW_DIR=os.path.join(tmp, 'dfdir'))
    shim = ("import sys,types\n"
            "if 'playwright' not in sys.modules:\n"
            "    try:\n        import playwright.sync_api\n"
            "    except Exception:\n"
            "        p=types.ModuleType('playwright'); s=types.ModuleType('playwright.sync_api')\n"
            "        s.sync_playwright=lambda *a,**k: None; sys.modules['playwright']=p; sys.modules['playwright.sync_api']=s\n"
            "import json, foreclosure_leads as F\n"
            "F.make_tracker(json.load(open('leads_final.json', encoding='utf-8')))\n")
    r = subprocess.run([sys.executable, '-c', shim], cwd=tmp, env=env, capture_output=True, text=True, timeout=600)
    idx = os.path.join(tmp, 'docs', 'index.html')
    check('make_tracker ran to the end', r.returncode == 0 and os.path.exists(idx),
          (r.stdout + r.stderr)[-600:])
    if os.path.exists(idx):
        head = open(idx, encoding='utf-8', errors='replace').read(4000)
        live = PG.cov_of_text(head) or {}
        check('built marker parses and keeps every existing field',
              all(k in live for k in list(BASE) + ['sig']), sorted(live))
        check('built marker carries every lien key', all(k in live for k in LC.KEYS), sorted(set(LC.KEYS) - set(live)))
        check('built counts: 2 MD rows, 2 read, 1 priced judgment + 1 unknown',
              (live.get('md'), live.get('md_rd'), live.get('md_fcj'), live.get('md_fcj_u')) == (2, 2, 1, 1),
              {k: live.get(k) for k in ('md', 'md_rd', 'md_fcj', 'md_fcj_u')})
        check('built counts: mortgages, HOA, judgments, tax liens, lis pendens',
              (live.get('md_mo'), live.get('md_ms'), live.get('md_mo_u'), live.get('md_hoa'), live.get('md_hoa_u'),
               live.get('md_mj'), live.get('md_fed'), live.get('md_st'), live.get('md_lpx')) == (2, 1, 1, 2, 1, 2, 2, 1, 1),
              {k: live.get(k) for k in ('md_mo', 'md_ms', 'md_mo_u', 'md_hoa', 'md_hoa_u', 'md_mj', 'md_fed', 'md_st', 'md_lpx')})
        check('built counts: taxes and code joined by folio',
              (live.get('md_taxd'), live.get('md_taxc'), live.get('md_code'), live.get('md_code_a')) == (1, 1, 1, 1),
              {k: live.get(k) for k in ('md_taxd', 'md_taxc', 'md_code', 'md_code_a')})
        check('built marker: no synthetic PII', not any(p in head.split('-->')[0] for p in PII))
        check('publish_guard.integrity is clean on the built page', PG.integrity(idx) == [], PG.integrity(idx))
        check('the build log printed the counts (census line)', '"md_hoa":' in r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print('')
if FAILS:
    print('FAILED %d: %s' % (len(FAILS), ', '.join(FAILS)))
    sys.exit(1)
print('ALL PASS')
