"""Call Mode unit spelling (2026-11-02, Alex): "1200 BRICKELL AVE #415" shows "1200 BRICKELL AVE APT 415".

Display only. Pins _unit_addr / _addr_show (street line only; '#' after a street type, or a trailing
'#' unit; existing APT/UNIT/STE/SUITE untouched; never a house number or a legal description), that
call_rows ships the spelled address in `a` while the lead's own `addr` keeps its '#', and that
_ledger_addr_key still ties the shown string to the county string. Invented addresses only.
Run: DEALFLOW_DIR=<temp> python _addrunittest.py
"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='addrunit_')
os.environ.setdefault('DEALFLOW_DIR', _T)
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


U = call_mode._unit_addr
S = call_mode._addr_show
T = [
    ('100 BISCAYNE AVE #415, Miami, FL 33131', '100 BISCAYNE AVE APT 415, Miami, FL 33131'),
    ('100 Biscayne Ave #415, Miami, FL 33131', '100 Biscayne Ave Apt 415, Miami, FL 33131'),
    ('200 OCEAN DR #1504, Miami Beach, FL 33139', '200 OCEAN DR APT 1504, Miami Beach, FL 33139'),
    ('300 NE 2 ST #B-12, Miami', '300 NE 2 ST APT B-12, Miami'),
    ('300 NE 2 ST # 7, Miami', '300 NE 2 ST APT 7, Miami'),
    ('300 NE 2 ST#7, Miami', '300 NE 2 ST APT 7, Miami'),
    ('400 PALM BLVD #3', '400 PALM BLVD APT 3'),
    ('500 SW 8 TER #L7, Hialeah', '500 SW 8 TER APT L7, Hialeah'),
    ('600 SW 5 #3, Miami', '600 SW 5 APT 3, Miami'),           # trailing unit, no street type
    # unchanged
    ('100 BISCAYNE AVE APT 415, Miami', None), ('100 BISCAYNE AVE UNIT 4, Miami', None),
    ('100 BISCAYNE AVE STE 200, Miami', None), ('100 BISCAYNE AVE SUITE 200', None),
    ('100 BISCAYNE AVE APT #4, Miami', None), ('100 BISCAYNE AVE UNIT #4', None),
    ('#5 BISCAYNE AVE, Miami', None),                          # no house number first
    ('LOT 5 BLK #3, Miami', None), ('10 LOT 5 BLK #3', None), ('10 PB 12 PG #45', None),
    ('100 BISCAYNE AVE, Miami, FL #33131', None),              # '#' after the comma is not read
    ('100 BISCAYNE AVE #415 REAR, Miami', '100 BISCAYNE AVE APT 415 REAR, Miami'),
    ('100 BISCAYNE AVE', None), ('', ''), (None, None),
]
for src, want in T:
    got = U(src)
    want = src if want is None else want
    rec('%r -> %r' % (src, want), got == want, got)

print('\nWITH ORDINALS')
for src, want in [
    ('622 SE 5 AVE #415, Hialeah, FL 33010', '622 SE 5th AVE APT 415, Hialeah, FL 33010'),
    ('10 SW 8 ST#12', '10 SW 8th ST APT 12'),
    ('10 SW 8 St #12, Miami', '10 SW 8th St Apt 12, Miami'),
    ('622 SE 5 AVE APT 3, Hialeah', '622 SE 5th AVE APT 3, Hialeah'),
]:
    rec('%r -> %r' % (src, want), S(src) == want, S(src))

print('\nLEDGER KEY')
K = call_mode._ledger_addr_key
rec('shown and county spellings key alike',
    K('622 SE 5th AVE APT 415, Hialeah, FL 33010') == K('622 SE 5 AVE #415, Hialeah, FL 33010') != '')
rec("'1 21st St' and '12 1st St' still differ", K('1 21st St, Miami') != K('12 1st St, Miami'))
rec('no house number still keys to nothing', K('APT 4, Miami') == '' and K('HOMESTEAD, FL 33034') == '')

print('\nROWS')
A = '3055550101'
EX = '622 SE 5 AVE #415, Hialeah, FL 33010'
d = {'case': 'X1', 'county': 'BROWARD', 'st': 'FC', 'auction': '11/01/2026', 'days': 20, 'tier': 'B',
     'score': 50, 'value': 400000, 'phones': [A], 'phtype': ['mobile'], 'phsrc': ['st'], 'phdnc': [False],
     'phrank': ['ok'], 'phbest': 0, 'addr': EX}
rows, _ = call_mode.call_rows([d], None, None)
rec('dial row shows APT', rows and rows[0]['a'] == '622 SE 5th AVE APT 415, Hialeah, FL 33010', rows and rows[0]['a'])
rec('lead addr keeps the county #', d['addr'] == EX, d['addr'])
cov = call_mode.coverage_rows([dict(d, case='X2', phones=[], phtype=[], phsrc=[], phdnc=[], phrank=[], phbest=None)], set())[0]
rec('coverage row shows APT', cov and cov[0]['a'] == '622 SE 5th AVE APT 415, Hialeah, FL 33010', cov and cov[0]['a'])
print('\n%s' % ('ALL PASS' if not fails else 'FAILS: %d -> %s' % (len(fails), fails)))
sys.exit(1 if fails else 0)
