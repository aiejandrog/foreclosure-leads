"""Palm Beach lis pendens rows whose grid CaseNumber is the word "CASE" get their real number from the legal text."""
import json, os, sys, tempfile
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import lis_pendens as L

ok = []; bad = []
def rec(n, c, d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))

rec('real number kept', L.pb_case('502026CA009491XXXAMB', 'Case Number: 502026CC000001XXXASB') == '502026CA009491XXXAMB')
rec('CASE -> number from legal', L.pb_case('CASE', 'Case Number: Case Number: 502026CA009528XXXAMB L: 35') == '502026CA009528XXXAMB')
rec('county civil recovered', L.pb_case('CASE', 'Case Number: Case Number: 502026CC014716XXXASB') == '502026CC014716XXXASB')
rec('family case not recovered', L.pb_case('CASE', 'Case Number: Case Number: 502026DR001448FZ') == '')
rec('truncated number not recovered', L.pb_case('CASE', 'Case Number: Case Number: 502026CA0094') == '')
rec('nothing in legal -> blank', L.pb_case('CASE', 'L: 8 SUB: WEITZER') == '')

rows = [{'county': 'PALM BEACH', 'case': 'CASE', 'legal': 'Case Number: 502026CA000111XXXAMB', 'instrument': '1'},
        {'county': 'PALM BEACH', 'case': 'CASE', 'legal': 'Case Number: 502026CA000222XXXAMB', 'instrument': '2'},
        {'county': 'BROWARD', 'case': 'CASE', 'legal': 'Case Number: 502026CA000333XXXAMB', 'instrument': '3'},
        {'case': '2026-000001-CA-01'}]
rec('repair touches only Palm Beach', L._repair_pb_cases(rows) == 2 and rows[2]['case'] == 'CASE'
    and [r['case'] for r in rows[:2]] == ['502026CA000111XXXAMB', '502026CA000222XXXAMB'])

src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fl_lp', 'palmbeach.py'), encoding='utf-8').read()
rec('the Palm Beach parser uses pb_case', "'case': pb_case(d.get('CaseNumber')" in src)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lp_leads.py'), encoding='utf-8').read()
rec('lp_leads keys Palm Beach rows on the repaired number', '_LP.pb_case(case, lp.get(\'legal\'))' in src)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
