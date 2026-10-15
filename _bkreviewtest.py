"""bk_review.py: sheet lists open plausible holds; --apply adds only marked live pairs, add-only."""
import json, os, sys, tempfile
os.environ['DEALFLOW_DIR'] = tempfile.mkdtemp(prefix='bkrev-')
import bk_review as R, bk_lookup as BK
ok = []; bad = []
def rec(n, c, d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))
cache = {
 '2099-000001-CA-01': {'searched': True, 'ok_check': True, 'cases': [
     {'no': '25-11111-ABC', 'court': 'flsb', 'open': True, 'match': 'plausible', 'filed': '2025-01-02'}]},
 '2099-000002-CA-01': {'searched': True, 'ok_check': True, 'cases': [
     {'no': '25-22222-ABC', 'court': 'flsb', 'open': True, 'match': 'exact', 'filed': '2025-01-02'}]},
 '2099-000003-CA-01': {'searched': True, 'ok_check': True, 'cases': []}}
leads = {'2099-000001-CA-01': {'county': 'BROWARD', 'owners_raw': [('DOE JOHN', 'last_first')]}}
rows = R.possible_rows(leads, cache, {})
rec('only the plausible lead is listed', [r['lead_id'] for r in rows] == ['2099-000001-CA-01'], rows)
sheet = R.write_sheet(rows)
txt = open(sheet, encoding='utf-8-sig').read()
rec('sheet carries owner, case, link', 'DOE JOHN' in txt and '25-11111-ABC' in txt and 'courtlistener' in txt)
ov = BK.overrides_path()
rec('unmarked sheet changes nothing', R.apply_sheet(sheet, rows)[0] == 0 and not os.path.exists(ov))
txt2 = txt.replace('\r\n', '\n').split('\n')
txt2[1] = 'clear' + txt2[1]
open(sheet, 'w', encoding='utf-8-sig').write('\n'.join(txt2))
a, s, e = R.apply_sheet(sheet, rows)
rec('marked row added', a == 1 and not e, (a, s, e))
rec('override now drops the hold', BK.load_overrides().get('2099-000001-CA-01') == {BK.case_no_key('25-11111-ABC')})
a, s, e = R.apply_sheet(sheet, rows)
rec('second apply adds nothing', a == 0 and s == 1)
rows2 = R.possible_rows(leads, cache, BK.load_overrides())
rec('cleared lead leaves the sheet', rows2 == [])
# a marked row for a pair that is not a live hold is ignored (exact match must never clear this way)
bogus = open(sheet, encoding='utf-8-sig').read().replace('2099-000001-CA-01', '2099-000002-CA-01').replace('25-11111-ABC', '25-22222-ABC')
open(sheet, 'w', encoding='utf-8-sig').write(bogus)
a, s, e = R.apply_sheet(sheet, rows)
rec('non-live pair ignored', a == 0 and s == 1)
open(ov, 'w').write('[ 1')
a, s, e = R.apply_sheet(sheet, rows)
rec('torn override left as is', e and open(ov).read() == '[ 1')
print('\n%d passed, %d failed' % (len(ok), len(bad))); sys.exit(1 if bad else 0)
