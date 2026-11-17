"""_lienamountstest -- amounts read off recorded liens and judgments, and the Palm Beach direct link.

Run:  python _lienamountstest.py   (exit 0 = safe; no network, invented text and numbers only)

WHAT THIS PINS
  1. parse_amount(): a labelled total wins; with none the largest figure is used and flagged; a
     per-day figure is never a total; an unread page with no labelled total is NOT priced.
  2. wants()/row_key(): only an open, counted, amountless claim is read, and each county's row is
     addressed the way its image route needs (Broward instrument, Palm Beach / Miami book+page).
  3. apply_chain(): prices the row, adds to the same bucket a priced index row would join, lowers
     other_open_unpriced, and is idempotent.
  4. lookup() never raises and ignores a cache entry from an older parser.
  5. other_liens.rows keeps the instrument number and uses a cached document amount.
  6. pb_recorded: book/page parsing, the URL it asks for, and a non-PDF body is Unavailable.
  7. The OCR bridge tries the Python engine before Windows PowerShell 5.1.
  8. The Miami claim filters mirror the ones inside records_liens.analyze.
  9. The nightly runs lien_amounts and complaint_reader before the rebuild.
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
import lien_amounts as LA  # noqa: E402
import pb_recorded as PR  # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra[:200]) if extra else ''))
    if not ok:
        fails.append(name)


# ---- 1. parse_amount ---------------------------------------------------------------------------
p = LA.parse_amount('FINAL JUDGMENT\nPrincipal $ 21,890.90\nCosts $ 50.00\nGrand total due $ 42,659.47\n')
rec('1a: a labelled total is the amount', p['amt'] == 42659 and p['status'] == 'priced' and p['basis'] == 'labelled', p)
p = LA.parse_amount('Total Costs\nSubtotal\nTOTAL\n$ 21,890.90\n$ 39,304.97\n$ 42,659.47\n')
rec('1b: labels and figures on separate OCR lines: the largest figure, flagged',
    p['amt'] == 42659 and p['basis'] == 'largest_of_n' and p['ambiguous'] is True, p)
p = LA.parse_amount('a fine of $250.00 per day shall accrue. Respondent shall pay $1,500.00 as set forth.')
rec('1c: a per-day figure is never the total', p['amt'] == 1500, p)
p = LA.parse_amount('Costs in the amount of $\nthe defendant shall submit specimens.')
rec('1d: a handwritten (unread) amount is no figure, not zero', p['amt'] is None and p['status'] == 'no_figure', p)
p = LA.parse_amount('Costs $ 300.00\nFees $ 900.00\n', complete=False)
rec('1e: an unread page with no labelled total is NOT priced', p['amt'] is None and p['status'] == 'partial', p)
p = LA.parse_amount('Plaintiff shall recover from Defendant the sum of $ 242,530.45 that shall bear interest', complete=False)
rec('1f: a labelled total prices even when another page was unread', p['amt'] == 242530, p)
p = LA.parse_amount('Total costs $ 150.00\nJudgment $ 9,000.00\n')
rec('1g: a labelled SUBtotal never undercuts a larger figure', p['amt'] == 9000 and p['basis'] == 'largest_over_label', p)
p = LA.parse_amount('amount due $ 21.890.90')
rec('1h: OCR writes a dot for the thousands comma', p['amt'] == 21891, p)
p = LA.parse_amount('recording fee $ 18.50 doc stamps $ 0.70')
rec('1i: fees under $100 are not a lien amount', p['amt'] is None, p)

# ---- 2. wants / row_key ---------------------------------------------------------------------------
open_row = {'d': '2025-08-06', 'doc': 'FINAL JUDGMENT', 'kind': 'judgment', 'party': 'ACME FUNDING',
            'bp': '', 'amt': None, 'st': 'OPEN', 'counted': 'unpriced', 'inst': '120000111'}
rec('2a: an open counted amountless claim is wanted', LA.wants('BROWARD', open_row))
rec('2b: a released claim is not', not LA.wants('BROWARD', dict(open_row, st='RELEASED')))
rec('2c: the case own filing is not', not LA.wants('BROWARD', dict(open_row, own_case=True)))
rec('2d: a lis pendens is not', not LA.wants('BROWARD', dict(open_row, kind='lis_pendens')))
rec('2e: a priced row is not', not LA.wants('BROWARD', dict(open_row, amt=500)))
rec('2f: a Broward row the chain did not count is not', not LA.wants('BROWARD', dict(open_row, counted='legacy')))
rec('2g: Broward is addressed by instrument', LA.row_key('BROWARD', open_row) == 'BROWARD:I:120000111')
rec('2h: a Broward row with no instrument cannot be addressed', LA.row_key('BROWARD', dict(open_row, inst='')) is None)
rec('2i: Palm Beach is addressed by book/page', LA.row_key('PALM BEACH', {'bp': 'O 36315/0910'}) == 'PALM BEACH:BP:36315/910')
rec('2j: Miami is addressed by book/page', LA.row_key('MIAMI-DADE', {'bp': '33210/1234'}) == 'MIAMI-DADE:BP:33210/1234')
md = {'doc': 'CLAIM OF LIEN', 'kind': 'other', 'amt': None, 'st': 'OPEN', 'bp': '33210/1234'}
rec('2k: a Miami claim of lien is wanted', LA.wants('MIAMI-DADE', md))
rec('2l: a Miami financing statement is never priced', not LA.wants('MIAMI-DADE', dict(md, doc='FINANCING STATEMENT')))
rec('2m: a Miami "other" that is no claim is never priced', not LA.wants('MIAMI-DADE', dict(md, doc='AGREEMENT')))

# ---- 3. apply_chain ------------------------------------------------------------------------------------
cache = {'BROWARD:I:120000111': {'status': 'priced', 'amt': 9000, 'v': LA.PARSER_VERSION},
         'BROWARD:I:120000222': {'status': 'no_figure', 'amt': None, 'v': LA.PARSER_VERSION}}
chain = {'conf': 'ok', 'code_open': 100, 'irs_open': 0, 'hoa_open': 0, 'other_open_unpriced': 2,
         'other': [dict(open_row), dict(open_row, inst='120000222', kind='irs')]}
n = LA.apply_chain('BROWARD', chain, cache)
r0 = chain['other'][0]
rec('3a: the priced row gets its amount and source', n == 1 and r0['amt'] == 9000 and r0['amt_src'] == 'doc', r0)
rec('3b: the amount joins the bucket a priced index row would', chain['code_open'] == 9100 and r0['counted'] == 'code_open')
rec('3c: other_open_unpriced drops by the rows priced, no further', chain['other_open_unpriced'] == 1)
rec('3d: a document with no figure leaves its row unpriced', chain['other'][1]['amt'] is None and chain['irs_open'] == 0)
rec('3e: applying twice changes nothing', LA.apply_chain('BROWARD', chain, cache) == 0 and chain['code_open'] == 9100)
irs = {'other_open_unpriced': 1, 'other': [dict(open_row, kind='irs')]}
LA.apply_chain('BROWARD', irs, cache)
rec('3f: a tax lien joins irs_open', irs.get('irs_open') == 9000)

# ---- 4. lookup ---------------------------------------------------------------------------------------------
rec('4a: lookup returns the cached amount', LA.lookup('BROWARD', open_row, cache) == 9000)
rec('4b: an entry from an older parser is ignored',
    LA.lookup('BROWARD', open_row, {'BROWARD:I:120000111': {'status': 'priced', 'amt': 9000, 'v': 0}}) is None)
rec('4c: lookup never raises on junk', LA.lookup('BROWARD', None, cache) is None and LA.lookup('X', {'bp': 5}, 7) is None)
today = dt.date(2026, 11, 16)
rec('4d: a county gap is retried after the wait, a read document is not',
    LA._due({'status': 'gap_not_in_index', 'read': '2026-10-20', 'v': LA.PARSER_VERSION}, today)
    and not LA._due({'status': 'gap_not_in_index', 'read': '2026-11-10', 'v': LA.PARSER_VERSION}, today)
    and LA._due({'status': 'gap_pdf', 'read': (today - dt.timedelta(days=1)).isoformat(), 'v': LA.PARSER_VERSION}, today)
    and not LA._due({'status': 'gap_pdf', 'read': today.isoformat(), 'v': LA.PARSER_VERSION}, today)
    and not LA._due({'status': 'no_figure', 'read': '2026-01-01', 'v': LA.PARSER_VERSION}, today))

# ---- 5. other_liens.rows keeps the instrument and uses the cache --------------------------------------
import other_liens as OL  # noqa: E402
pool = [{'DocTypeDescription': 'FINAL JUDGMENT', 'CrossPartyName': 'ACME FUNDING', 'Consideration': '0',
         'RecordDate': '/Date(1754452800000)/', 'InstrumentNumber': '120000111', 'BookPage': '', 'CaseNumber': '',
         'ParcelNumber': '', 'Party': 'To'}]
with tempfile.TemporaryDirectory() as td:
    saved = LA.CACHE
    LA.CACHE = os.path.join(td, 'lien_amounts.json')
    LA._CACHE.update(mtime=None, data=None)
    try:
        got = OL.rows(pool)
        rec('5a: a lien row keeps its instrument number', got['other'][0].get('inst') == '120000111', got['other'][0])
        rec('5b: with nothing read it stays unpriced', got['other_open_unpriced'] == 1 and got['other'][0]['amt'] is None)
        LA.save_cache(cache)
        got = OL.rows(pool)
        rec('5c: a document amount in the cache prices the row on the next trace',
            got['other'][0]['amt'] == 9000 and got['other'][0].get('amt_src') == 'doc'
            and got['other_open_unpriced'] == 0 and got['add']['code_open'] == 9000, got['other'][0])
    finally:
        LA.CACHE = saved
        LA._CACHE.update(mtime=None, data=None)

# ---- 6. pb_recorded -------------------------------------------------------------------------------------------
rec('6a: stored references parse', PR.parse_bp('O 36315/910') == ('36315', '910')
    and PR.parse_bp('36315/0910') == ('36315', '910') and PR.parse_bp('0/0') is None and PR.parse_bp('abc') is None)
asked = []
pdf = PR.by_book_page('36315', '910', run=lambda url: asked.append(url) or b'%PDF-1.4 x')
rec('6b: the direct link is asked with booktype=O',
    asked == ['https://erec.mypalmbeachclerk.com/Document/GetDocumentByBookPage/?booktype=O&booknumber=36315&pagenumber=910']
    and pdf.startswith(b'%PDF-'), asked)
try:
    PR.by_book_page('36315', '910', run=lambda url: b'<html>The resource cannot be found</html>')
    rec('6c: a non-PDF body is Unavailable', False)
except PR.Unavailable:
    rec('6c: a non-PDF body is Unavailable', True)
try:
    PR.by_book_page('36315', "910&x=1", run=lambda url: b'%PDF-')
    rec('6d: a non-numeric page never reaches the URL', False)
except ValueError:
    rec('6d: a non-numeric page never reaches the URL', True)
asked = []
PR.fetch('20260300001', run=lambda url: asked.append(url) or b'%PDF-')
rec('6e: a CFN uses the CFN link', asked and asked[0].endswith('/Document/GetDocumentByCFN/?cfn=20260300001'), asked)

# ---- 7-9. wiring ------------------------------------------------------------------------------------------------
pin = open(os.path.join(HERE, 'fl_lp', 'broward_pin.py'), encoding='utf-8').read()
body = pin[pin.index('def ocr_images('):pin.index('def _details_html(')]
rec('7a: the OCR bridge tries the Python engine before PowerShell',
    'winocr_py' in body and body.index('winocr_py') < body.index('subprocess.run([POWERSHELL'))
import winocr_py as W  # noqa: E402
rec('7b: no images is an empty result, no engine needed', W.ocr_images([]) == {})
rl = open(os.path.join(HERE, 'records_liens.py'), encoding='utf-8').read()
m1 = re.search(r"_CLAIM_DOC_RE = re\.compile\(r'([^']+)'", rl)
m2 = re.search(r"_NOT_CLAIM_DOC_RE = re\.compile\(r'([^']+)'", rl)
rec('8a: the Miami claim filter mirrors records_liens', bool(m1) and m1.group(1) == LA._MD_CLAIM_DOC_RE.pattern)
rec('8b: the Miami not-a-claim filter mirrors records_liens', bool(m2) and m2.group(1) == LA._MD_NOT_CLAIM_DOC_RE.pattern)
rec('8c: records_liens consults the document cache', "lien_amounts.lookup('MIAMI-DADE', row)" in rl)
src_la = open(os.path.join(HERE, 'lien_amounts.py'), encoding='utf-8').read()
rec('8d: a blocked download is asked again the next run and once more in the same run',
    LA.RETRY_TRANSIENT_DAYS == 1 and 'gap_pdf' in LA.TRANSIENT and 'SECOND PASS' in src_la and "report['recovered']" in src_la)
bat = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='cp1252').read()
rec('9c: the nightly reads 250 lien documents', 'lien_amounts.py --limit 250' in bat)
i_la, i_cr, i_build = bat.find('lien_amounts.py --limit'), bat.find('complaint_reader.py --limit'), bat.find('F.make_tracker(')
rec('9a: the nightly reads lien amounts and complaints before the rebuild', 0 < i_la < i_cr < i_build, (i_la, i_cr, i_build))
gi = open(os.path.join(HERE, '.gitignore'), encoding='utf-8').read().split()
rec('9b: the amount cache and the complaint file are ignored', 'lien_amounts.json' in gi and 'complaints.json' in gi)

print('\n%d checks, %d failed' % (len(checks), len(fails)))
print('ALL PASS' if not fails else 'FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)
