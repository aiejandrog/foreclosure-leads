"""_codelienamounttest -- C2: code-lien amounts read off the recorded lien, labelled ESTIMATE.

Run:  python _codelienamounttest.py   (exit 0 = safe; no network, no browser, invented lien text only)

WHAT THIS PINS
  1. parse_lien_text() finds the recorded amount, the daily rate and the accrual start only where a
     phrase labels them; a per-day figure is never read as a total; ambiguity is reported.
  2. estimate() does the arithmetic from the right start date, never counts negative days, and every
     row says ESTIMATE and is_payoff=False.
  3. A lien whose CFN is unknown is a NAMED gap (not_in_index), never a guess and never a fetch.
  4. run() re-estimates cached liens daily without a county request, and respects --limit.
  5. The wiring: records_liens feeds the index, the nightly runs it inside [3k/5] before the [4/5]
     rebuild, the board shows it as ESTIMATE / NOT a payoff, and equity code does not read it.
"""
import datetime as dt
import json
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import code_lien_amounts as C  # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    if len(extra) > 200:
        extra = extra[:200] + ' ...'
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra) if extra else ''))
    if not ok:
        fails.append(name)


AS_OF = dt.date(2026, 9, 26)

# ---------------------------------------------------------------- 1. parsing
print('PARSE')
T1 = ('ORDER IMPOSING LIEN. Respondent is assessed a fine of $250.00 per day commencing on '
      'March 1, 2025 and continuing until compliance. Administrative costs of $1,200.50. '
      'Total amount due: $15,450.50 as of the date of this order.')
p = C.parse_lien_text(T1)
rec('a labelled total is the recorded amount', p['recorded_amount'] == 15450.50, p)
rec('the per-day figure is the daily rate', p['daily_rate'] == 250.0, p['daily_rate'])
rec('the start printed next to the rate is the accrual start', p['accrual_start'] == '2025-03-01',
    p['accrual_start'])
rec('a clean lien is not ambiguous', p['ambiguous'] is False)
rec('every number comes with the words it was read from', len(p['basis']) >= 3, p['basis'])

T2 = 'A daily fine of $100 shall accrue from 01/15/2026 until the violation is corrected.'
p2 = C.parse_lien_text(T2)
rec('"daily fine of $X" is a daily rate', p2['daily_rate'] == 100.0, p2)
rec('...with a numeric start date', p2['accrual_start'] == '2026-01-15', p2['accrual_start'])
rec('...and no total invented', p2['recorded_amount'] is None, p2['recorded_amount'])

T3 = 'The fine of $500 per day is imposed.'
p3 = C.parse_lien_text(T3)
rec('a per-day figure is NEVER read as the recorded amount', p3['recorded_amount'] is None
    and p3['daily_rate'] == 500.0, p3)

T4 = 'A lien in the amount of $3,000.00 is imposed. Fines of $750.00 and costs of $125.00.'
p4 = C.parse_lien_text(T4)
rec('"lien in the amount of" is a labelled total', p4['recorded_amount'] == 3000.0, p4)

T5 = 'Civil penalty of $1,000.00. Administrative costs of $300.00.'
p5 = C.parse_lien_text(T5)
rec('several unlabelled figures: the largest, flagged ambiguous', p5['recorded_amount'] == 1000.0
    and p5['ambiguous'] is True, p5)

T6 = 'Fines of $250.00 per day for a first violation and $500.00 per day for a repeat violation.'
p6 = C.parse_lien_text(T6)
rec('two daily rates: the larger, flagged', p6['daily_rate'] == 500.0 and p6['ambiguous'], p6)

p7 = C.parse_lien_text('NOTICE OF LIEN. See attached. Folio 30-0000-000-0000.')
rec('no labelled figure -> nothing', p7['recorded_amount'] is None and p7['daily_rate'] is None, p7)
rec('parse_date reads the county\'s formats',
    [C.parse_date(s) for s in ('3/14/25', '03/14/2025', 'March 14, 2025', 'Mar. 14th 2025', '2025-03-14')]
    == [dt.date(2025, 3, 14)] * 5)
rec('...including "Sept"', C.parse_date('Sept 14, 2025') == dt.date(2025, 9, 14))

# ---------------------------------------------------------------- 2. estimate
print('\nESTIMATE')
e = C.estimate(p, '2025-04-10', AS_OF)
rec('total + rate: accrues from the RECORDING date (the total counts fines to the order)',
    e['status'] == 'estimated' and e['accrual_from'] == '2025-04-10' and e['days'] == 534, e)
rec('...estimate = recorded + rate x days', e['estimate_total'] == round(15450.50 + 250 * 534, 2),
    e['estimate_total'])
rec('every row is an ESTIMATE, never a payoff', e['kind'] == 'ESTIMATE' and e['is_payoff'] is False
    and 'not a payoff' in e['label'].lower() and 'ESTIMATE' in e['label'], e['label'])
rec('the label names the real way to a balance ($78 letter)', '$78' in e['label'])
e2 = C.estimate(p2, '2026-02-01', AS_OF)
rec('rate only: accrues from the stated start', e2['accrual_from'] == '2026-01-15'
    and e2['estimate_total'] == 100.0 * (AS_OF - dt.date(2026, 1, 15)).days, e2)
e3 = C.estimate(p3, '2026-06-01', AS_OF)
rec('rate, no start, no total: from recording, and says it understates',
    e3['accrual_from'] == '2026-06-01' and 'understates' in e3['how'], e3['how'])
e4 = C.estimate(p4, '2026-01-01', AS_OF)
rec('amount without a daily rate is a FLOOR', e4['status'] == 'recorded_amount_only'
    and e4['estimate_total'] == 3000.0 and 'floor' in e4['how'], e4)
e5 = C.estimate(p7, '2026-01-01', AS_OF)
rec('nothing labelled -> no_amount_found, no figure', e5['status'] == 'no_amount_found'
    and e5['estimate_total'] is None, e5)
e6 = C.estimate(p2, '2026-02-01', dt.date(2025, 1, 1))
rec('never negative days', e6['days'] == 0 and e6['estimate_total'] == 0.0, e6)

# ---------------------------------------------------------------- 3. gaps and inputs
print('\nINPUTS AND GAPS')
CL = {'3000000000001': [{'case': 'C1', 'st': '4', 'lien': True, 'lienRef': '31000/1234'},
                        {'case': 'C1b', 'st': '4', 'lien': True, 'lienRef': '031000/01234'},
                        {'case': 'C2', 'st': '1', 'lien': False, 'lienRef': ''}],
      '3000000000002': [{'case': 'C3', 'st': '4', 'lien': True, 'lienRef': '32000/55'},
                        {'case': 'C4', 'st': '4', 'lien': True, 'lienRef': '/'}]}
L = C.recorded_liens(CL)
rec('one row per recorded instrument, padded book/page deduped, open cases and blanks skipped',
    sorted((b, pg) for _, _, b, pg in L) == [('31000', '1234'), ('32000', '55')], L)


class FakeIndex:
    def __init__(self, rows):
        self.rows = rows

    def get(self, b, pg):
        return self.rows.get('%s/%s' % (b, pg))


text, meta = C.read_lien('3000000000001', '31000', '1234', FakeIndex({}))
rec('no CFN in the index -> a NAMED gap and no fetch', text == '' and meta['status'] == 'not_in_index'
    and 'CFN' in meta['reason'], meta)

# ---------------------------------------------------------------- 4. run()
print('\nRUN')
tmp = tempfile.mkdtemp(prefix='cla-')
out = os.path.join(tmp, 'code_lien_amounts.json')
calls = []
_real = C.read_lien


def fake_read(folio, book, page, index, collector=None, ocr=None):
    calls.append((book, page))
    if book == '31000':
        return T1, {'status': 'read', 'rec_date': '2025-04-10', 'doc_type': 'LIEN', 'pages_unread': []}
    return '', {'status': 'not_in_index', 'reason': 'CFN unknown'}


C.read_lien = fake_read
try:
    r1 = C.run(limit=5, as_of=AS_OF, code_liens=CL, out_path=out, index=FakeIndex({}), ocr=None,
               pause=0, log=lambda *a: None)
    row = r1['liens']['31000/1234']
    rec('a read lien carries its estimate', row['estimate']['status'] == 'estimated', row['estimate'])
    rec('a gap is kept and says why', r1['liens']['32000/55']['read_status'] == 'not_in_index')
    rec('a gap does not count against the fetch limit', r1['fetched_this_run'] == 1, r1['fetched_this_run'])
    rec('the output keeps no lien text, only snippets and numbers',
        'Respondent is assessed a fine of $250.00 per day commencing on March 1, 2025 and continuing '
        'until compliance. Administrative' not in open(out, encoding='utf-8').read())
    calls.clear()
    r2 = C.run(limit=5, as_of=AS_OF + dt.timedelta(days=10), code_liens=CL, out_path=out,
               index=FakeIndex({}), ocr=None, pause=0, log=lambda *a: None)
    rec('a lien already read is NOT fetched again', ('31000', '1234') not in calls, calls)
    rec('...but its estimate moves with the calendar',
        r2['liens']['31000/1234']['estimate']['days'] == 544, r2['liens']['31000/1234']['estimate']['days'])
    calls.clear()
    C.run(fetch=False, as_of=AS_OF, code_liens=CL, out_path=out, index=FakeIndex({}), ocr=None,
          pause=0, log=lambda *a: None)
    rec('--no-fetch makes no county request at all', calls == [], calls)
    calls.clear()
    C.run(limit=0, as_of=AS_OF, code_liens=CL, out_path=os.path.join(tmp, 'b.json'),
          index=FakeIndex({}), ocr=None, pause=0, log=lambda *a: None)
    rec('--limit 0 fetches nothing', calls == [], calls)
    fb = C.for_board(r1)
    rec('the board gets only real estimates, labelled', list(fb) == ['31000/1234']
        and fb['31000/1234']['estKind'] == 'ESTIMATE', fb)
finally:
    C.read_lien = _real

# ---------------------------------------------------------------- 5. wiring
print('\nWIRING')
src = lambda f: open(os.path.join(HERE, f), encoding='utf-8', errors='replace').read()  # noqa: E731
rl = src('records_liens.py')
rec('records_liens feeds every OR result into the index before analyze()',
    rl.find('_CLA.index_models(models)') != -1
    and rl.find('_CLA.index_models(models)') < rl.find('res = analyze(models, folio, judg'))
bat = src('refresh-dealflow.bat')
i_cl, i_cla, i_4 = bat.find('python -u code_liens.py'), bat.find('python -u code_lien_amounts.py'), bat.find('echo [4/5]')
rec('the nightly runs it right after code_liens and before the [4/5] rebuild',
    -1 < i_cl < i_cla < i_4, (i_cl, i_cla, i_4))
rec('...and a failure there cannot fail the run',
    'code_lien_amounts failed' in bat)
tpl = src('tracker_template.html')
rec('the board says ESTIMATE and NOT a payoff wherever it shows the figure',
    "ESTIMATE '" in tpl and 'NOT a payoff' in tpl)
fl = src('foreclosure_leads.py')
rec('the bake attaches the estimate to the code-lien hit only', '_CLA.for_board(' in fl)
eq = src('equity_state.py')
rec('equity never reads the estimate', 'code_lien_amounts' not in eq and 'estimate_total' not in eq)
mod = src('code_lien_amounts.py')
rec('no paid path is reachable from this module',
    not re.search(r'fetch_via_turnstile|captcha_solver|paid_reads|getAdvancedRecords|document_vision', mod))
gi = src('.gitignore')
rec('the output file is gitignored', 'code_lien_amounts.json' in gi)

print('\n%s -- %d check(s), %d failure(s)' % ('PASSED' if not fails else 'FAILED', len(checks), len(fails)))
for f in fails:
    print('  FAILED: ' + f)
sys.exit(1 if fails else 0)
