"""DEALFLOW_COURT_JUDGMENT: a stored, verified court-copy figure reaches records_liens' judgment and
eqc.judgment_source. Invented fixtures, no network."""
import json, os, sys, tempfile
TMP = tempfile.mkdtemp()
os.environ['DEALFLOW_DIR'] = TMP
import records_liens as R, miami_judgment as MJ, equity_state as E

F = []
def chk(name, ok, d=''):
    print(('  PASS ' if ok else '  FAIL ') + name + (' | ' + d if d else ''))
    if not ok:
        F.append(name)

CASE = '2099-000001-CA-01'
os.makedirs(os.path.join(TMP, MJ.REPORT_DIR))
def put(case, cand_source):
    row = {'page_count_verified': True, 'read_status': 'read', 'case_tie': {'tier': 0},
           'amount_candidates': [{'amount': 412880.45, 'text_source': cand_source, 'sum_check': True}]}
    json.dump({'documents': [row]}, open(os.path.join(TMP, MJ.REPORT_DIR, case + '.json'), 'w'))

put(CASE, None)                                    # the county's own text layer
os.environ.pop('DEALFLOW_COURT_JUDGMENT', None)
chk('flag off: board figure, no source', R.judgment_for(CASE, 300000.0) == (300000.0, None))
os.environ['DEALFLOW_COURT_JUDGMENT'] = '1'
chk('flag on + verified text-layer report: court copy', R.judgment_for(CASE, 300000.0) == (412880.45, 'court copy'))
chk('flag on, no report: board figure', R.judgment_for('2099-000009-CA-01', 300000.0) == (300000.0, None))
put('2099-000002-CA-01', 'ocr')
chk('a scan-read figure is still refused (reviewed decision)', R.judgment_for('2099-000002-CA-01', 300000.0) == (300000.0, None))
open(os.path.join(TMP, MJ.REPORT_DIR, 'bad.json'), 'w').write('{not json')
chk('torn report -> board figure, no raise', R.judgment_for('bad', 5.0) == (5.0, None))
chk('path-like case cannot escape the folder', MJ.court_judgment('../../etc/passwd') is None)

lead = {'case': CASE, 'ctype': 'HOA/Condo', 'value': 600000, 'judg': 300000, 'ju': False}
E.apply(lead, {'conf': 'ok', 'liens': [], 'nrec': 12, 'second_fc': []})
c = E.card(lead)
chk('docket by default', c['judgment'] == 300000 and c['judgment_source'] == 'docket')
lead.update(judg_src='court copy', judgc=412880.45)
c = E.card(lead)
chk('eqc.judgment_source = court copy, figure and net use it',
    c['judgment_source'] == 'court copy' and c['judgment'] == 412880.45 and c['net'] == 600000 - 412880.45, str(c['net']))
print('FAIL: %s' % F if F else 'ALL PASS')
sys.exit(1 if F else 0)
