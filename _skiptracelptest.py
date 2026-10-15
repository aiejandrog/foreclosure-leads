"""skiptrace.select: a dismissed / closed / owner-changed lis pendens is not traced by the nightly --all either."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skiptrace as S

FAILS = []
def check(n, c, d=''):
    print(('PASS ' if c else 'FAIL ') + n + (('  -- ' + str(d)) if d and not c else ''))
    if not c: FAILS.append(n)

def lead(case, **kw):
    r = {'Case #': case, 'owners': 'DOE, JOHN', 'tier': 'A', 'st': 'LP',
         'mailing_address': '123 MAIN ST, MIAMI, FL 33101', 'county': 'MIAMI-DADE'}
    r.update(kw); return r

rows = [lead('L-OK'), lead('L-DISM', lpDismissed=True), lead('L-CLOSED', lpClosed=True),
        lead('L-MOVED', ownerMismatch=True), dict(lead('A-1'), st='A', lpDismissed=True)]
args = types.SimpleNamespace(lp_fresh=0, case='', cases_file='', all=True, tier='A')
got = sorted(S._case(r) for r in S.select([dict(r) for r in rows], args))
check('nightly --all skips dead lis pendens, keeps live LP and non-LP rows', got == ['A-1', 'L-OK'], got)
args.case = 'L-DISM'; args.all = False
got = [S._case(r) for r in S.select([dict(r) for r in rows], args)]
check('an explicit --case still traces it', got == ['L-DISM'], got)

print('\n' + ('ALL PASS' if not FAILS else 'FAILED: %s' % FAILS))
sys.exit(1 if FAILS else 0)
