"""_complaintreadertest -- what the complaint reader takes off a complaint, and the board's owed line.

Run:  python _complaintreadertest.py   (exit 0 = safe; no network, invented parties and numbers only)

WHAT THIS PINS
  1. parse_complaint(): the balance the lender says is owed (never the original loan amount), the
     default date, the note, the rate, the mortgage's Official Records reference (never a plat
     book), and each lienholder the complaint names with what its claim is.
  2. Nothing is inferred: a complaint that states none of it returns None / [].
  3. pick_complaint(): the latest amended complaint, never an answer or a motion about one.
  4. A Broward notice never carries a pleaded balance; the foreclosed mortgage's recorded face comes
     from the chain only when the instrument matches.
  5. for_board()/stamp_rows(): figures and counts only, no party name; Palm Beach matches on the
     first 14 characters of the case number.
  6. The board's Copy all line never says "Owed $0" for a lead with no judgment.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import complaint_reader as CR  # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra[:220]) if extra else ''))
    if not ok:
        fails.append(name)


PAGE1 = """IN THE CIRCUIT COURT OF THE ELEVENTH JUDICIAL CIRCUIT
VERIFIED COMPLAINT TO FORECLOSE MORTGAGE
1. This is an action to foreclose a mortgage on real property in Miami-Dade County, Florida.
2. On March 4, 2019, TEST BORROWER executed and delivered a promissory note in the original
principal amount of $300,000.00 and a mortgage securing payment of the note. The Mortgage was
recorded on March 12, 2019 in Official Records Book 31350, Page 1204, of the Public Records.
The property is Lot 4, Block 2, according to the plat recorded in Plat Book 136, Page 82.
3. Defendant has defaulted under the note and mortgage by failing to pay the payment due
June 1, 2025 and all subsequent payments.
4. Plaintiff declares the full amount payable. Defendant owes Plaintiff $268,083.36 that is due
on principal, plus interest from May 1, 2025, late charges, and costs.
"""
PAGE2 = """5. Defendant, SECOND LIEN BANK, N.A., may claim some interest in the property by virtue of a
mortgage recorded in Official Records Book 32001, Page 77, in the amount of $45,000.00. Said interest
is subordinate to the lien of Plaintiff.
6. Defendant, PALM GROVE HOMEOWNERS ASSOCIATION, INC., may claim an interest in the property by
virtue of unpaid assessments. Said interest is inferior.
7. Defendant, UNITED STATES OF AMERICA, may claim some interest by virtue of a federal tax lien.
8. Defendant, UNKNOWN TENANT #1, may claim an interest as a tenant in possession.
9. Defendant, UNKNOWN SPOUSE OF TEST BORROWER, may claim some interest as spouse.
"""
EXHIBIT = """NOTE
Interest will be charged on unpaid principal at a yearly rate of 6.375%.
"""
p = CR.parse_complaint([PAGE1, PAGE2, EXHIBIT])
rec('1a: the balance owed is the pleaded principal, not the original loan', p['principal'] == 268083.36, p['principal'])
rec('1b: the original loan amount is kept apart', p['orig_amount'] == 300000.0, p['orig_amount'])
rec('1c: the default date', p['default_date'] == '2025-06-01', p['default_date'])
rec('1d: the note date', p['note_date'] == '2019-03-04', p['note_date'])
rec('1e: the rate comes off the exhibit (every page is read)', p['rate'] == 6.375, p['rate'])
rec('1f: the mortgage reference is Official Records, never the plat book', p['mtg_bp'] == '31350/1204', p['mtg_bp'])
basis = [j['basis'] for j in p['juniors']]
rec('1g: every party the complaint names is listed with its claim',
    basis == ['mortgage', 'association', 'irs', 'tenant', 'spouse'], basis)
j0 = p['juniors'][0]
rec('1h: a named second mortgage carries its reference and amount',
    j0['bp'] == '32001/77' and j0['amt'] == 45000 and 'SECOND LIEN BANK' in j0['who'], j0)
rec('1i: tenants and spouses are not lienholders', [j['basis'] for j in CR.lien_juniors(p)] == ['mortgage', 'association', 'irs'])
rec('1j: it is recognised as a complaint', p['is_complaint'] is True)

only_orig = CR.parse_complaint(['COMPLAINT\nBorrower executed a note in the principal amount of $150,000.00.'])
rec('1k: "principal amount of" alone is the original loan, never the balance claimed',
    only_orig['principal'] is None and only_orig['orig_amount'] == 150000.0, only_orig)
bal = CR.parse_complaint(['COMPLAINT\nThe unpaid principal balance is $958,859.60 as of the date of this Complaint.'])
rec('1l: "unpaid principal balance is $X"', bal['principal'] == 958859.60, bal['principal'])

e = CR.parse_complaint(['NOTICE OF APPEARANCE\nCounsel appears for the defendant.', ''])
rec('2a: nothing stated, nothing inferred',
    e['principal'] is None and e['default_date'] is None and e['juniors'] == [] and e['rate'] is None, e)
rec('2b: dates parse in the shapes complaints use',
    CR.parse_date('June 1, 2025') == '2025-06-01' and CR.parse_date('06/01/2025') == '2025-06-01'
    and CR.parse_date('Sept. 3rd, 2024') == '2024-09-03' and CR.parse_date('the first') is None)

# ---- 3. pick_complaint ------------------------------------------------------------------------------
ents = [{'d': 'CIVIL COVER SHEET'}, {'d': 'COMPLAINT'}, {'d': 'ANSWER TO COMPLAINT'},
        {'d': 'MOTION TO DISMISS COMPLAINT'}, {'d': 'AMENDED COMPLAINT'}, {'d': 'SUMMONS ISSUED'}]
rec('3a: the latest amended complaint is the one read', CR.pick_complaint(ents, lambda x: x['d'])['d'] == 'AMENDED COMPLAINT')
rec('3b: with no amended one, the complaint itself', CR.pick_complaint(ents[:4], lambda x: x['d'])['d'] == 'COMPLAINT')
rec('3c: an answer or a motion is never picked', CR.pick_complaint(ents[2:4], lambda x: x['d']) is None)

# ---- 4. Broward notice --------------------------------------------------------------------------------
chain = {'liens': [{'amt': 81600, 'bp': '47337/398', 'inst': '109552619', 'st': 'SATISFIED'},
                   {'amt': 360000, 'bp': '', 'inst': '118773412', 'st': 'OPEN'}]}
rec('4a: the notice instrument ties to the chain row', CR.foreclosed_face({'mtg_inst': '118773412'}, chain) == 360000)
rec('4b: or its book/page does', CR.foreclosed_face({'mtg_bp': '47337/398'}, chain) == 81600)
rec('4c: no match, no face (never the largest loan by default)', CR.foreclosed_face({'mtg_inst': '1'}, chain) is None
    and CR.foreclosed_face({}, chain) is None and CR.foreclosed_face({'mtg_inst': '118773412'}, None) is None)
src = open(os.path.join(HERE, 'complaint_reader.py'), encoding='utf-8').read()
body = src[src.index('def read_broward_notice('):src.index('def foreclosed_face(')]
rec('4d: a notice never keeps a pleaded balance', "e['principal'] = None" in body and "'lp_notice'" in body)

run_src = src[src.index('def run('):src.index('def for_board(')]
rec('4e: the nightly limit is per county, so Miami cannot use up Broward's share',
    run_src.count('n = 0   ') >= 2 and run_src.index("if 'BROWARD' in counties") < run_src.rindex('n = 0   '))

# ---- 5. board fields -------------------------------------------------------------------------------------
entry = dict(p, status='read', kind='complaint', pages=3, pages_read=3)
b = CR.for_board(entry)
rec('5a: the board row carries figures and counts',
    b == {'k': 'complaint', 'pg': 3, 'rd': 3, 'p': 268083, 'dd': '2025-06-01', 'r': 6.375, 'jr': 3,
          'jrk': ['association', 'irs', 'mortgage']}, b)
rec('5b: no party name reaches the board row', 'SECOND LIEN' not in json.dumps(b) and 'who' not in json.dumps(b))
rec('5c: a gap stamps nothing', CR.for_board({'status': 'gap_image'}) is None and CR.for_board(None) is None)
with tempfile.TemporaryDirectory() as td:
    json.dump({'2026-000001-CA-01': entry, '502026CA012740XXXAMB': dict(entry, principal=111000.0)},
              open(os.path.join(td, 'complaints.json'), 'w', encoding='utf-8'))
    rows = [{'case': '2026-000001-CA-01'}, {'case': '50-2026-CA-012740-XXXX-MB'}, {'case': 'CACE-26-000001'}]
    n = CR.stamp_rows(rows, here=td)
    rec('5d: rows are stamped by case; Palm Beach on the first 14 characters',
        n == 2 and rows[0]['cmp']['p'] == 268083 and rows[1]['cmp']['p'] == 111000 and 'cmp' not in rows[2], rows)
    rec('5e: no file, nothing stamped, nothing raised', CR.stamp_rows([{'case': 'x'}], here=os.path.join(td, 'none')) == 0)
fl = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
rec('5f: the board build stamps complaints onto its rows', '_cr.stamp_rows(slim)' in fl)

# ---- 6. the Copy all line ------------------------------------------------------------------------------------
tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
rec("6a: Copy all no longer prints 'Owed '+payoff unconditionally",
    "' / Owed '+fmtK(_payoffOf(r))" not in tpl and "'+_owedLine(r)+" in tpl)
node = shutil.which('node')
if node:
    a = tpl.index('function _owedLine(r){')
    fn = tpl[a:tpl.index('\n}', a) + 2]
    js = ("function fmtK(v){return '$'+Math.round(v/1000)+'k';}\n"
          "function _payoffOf(r){ return (+((r||{}).payoff) > 0) ? +r.payoff : (+((r||{}).judg) || 0); }\n"
          + fn + "\nconsole.log(JSON.stringify(["
          "_owedLine({judg:250000}),"
          "_owedLine({judg:0}),"
          "_owedLine({judg:0,orestface:360000,orestn:1,orest:351000,eqoth:4}),"
          "_owedLine({judg:0,cmp:{p:268083,dd:'2025-06-01',jr:3}}),"
          "_owedLine({judg:0,cmp:{face:135000}})]));")
    out = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
    try:
        got = json.loads(out.stdout.strip())
    except ValueError:
        got = [out.stderr[:200]]
    rec('6b: a posted judgment still reads Owed $X', got[0] == 'Owed $250k', got[:1])
    rec('6c: no judgment is "not posted", never $0', len(got) > 1 and got[1] == 'Owed not posted (no judgment yet)' and '$0' not in got[1], got[1:2])
    rec('6d: the recorded open mortgage and the unpriced liens are named',
        len(got) > 2 and got[2] == 'Owed not posted: Recorded open mortgage $360k (est. balance $351k) + 4 open liens with no amount', got[2:3])
    rec('6e: what the complaint claims leads the line',
        len(got) > 3 and got[3].startswith('Owed not posted: complaint claims $268k principal (default 2025-06-01) + 3 other lienholders named'), got[3:4])
    rec('6f: a Broward notice shows the recorded face, labelled as recorded',
        len(got) > 4 and got[4] == 'Owed not posted: foreclosed mortgage recorded at $135k', got[4:5])
else:
    rec('6b: node not installed; the owed line was not executed (source checks only)', True)

print('\n%d checks, %d failed' % (len(checks), len(fails)))
print('ALL PASS' if not fails else 'FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)
