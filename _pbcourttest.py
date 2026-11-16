"""_pbcourttest -- the Palm Beach court route: the local inbox and the filings merge.

Run:  python _pbcourttest.py   (exit 0 = safe; loopback only, invented cases and parties)

WHAT THIS PINS
  1. The inbox only takes posts from the court site's own origin, only saves PDFs, and builds the
     file name itself: nothing the caller sends reaches the path.
  2. A court search row becomes a lis_pendens.json row in the file's own shape; a timeshare, a
     closed case, a row with no ' V ' or a bad case number is not a filing.
  3. merge(): an existing Palm Beach case is never added twice (matched on the first 14 characters)
     and no existing row is changed.
  4. complaint_reader finds posted complaints by case; a later docket number wins.
  5. The Palm Beach collector reads RECORDED documents by the direct link, and no longer claims
     that every Palm Beach document needs a captcha.
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pb_court_inbox as IN  # noqa: E402
import pb_court_filings as PF  # noqa: E402
import complaint_reader as CR  # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra[:200]) if extra else ''))
    if not ok:
        fails.append(name)


CASE = '502026CA099999XXXAMB'

# ---- 1. inbox ----------------------------------------------------------------------------------
rec('1a: the file name is built from a validated case, docket number and kind',
    IN.doc_name('50-2026-CA-099999-XXXA-MB', '3', 'Complaint') == CASE + '_DIN3_COMPLAINT.pdf')
for bad in (('../../x', '3', 'C'), (CASE, '3;rm', 'C'), ('CACE-26-000001', '3', 'C')):
    try:
        IN.doc_name(*bad)
        rec('1b: a bad case or docket number is refused %r' % (bad[:2],), False)
    except ValueError:
        rec('1b: a bad case or docket number is refused %r' % (bad[:2],), True)
rec('1c: path characters in the kind never reach the name',
    IN.doc_name(CASE, '3', '..\\..\\evil/../x') == CASE + '_DIN3_EVIL_X.pdf', IN.doc_name(CASE, '3', '..\\..\\evil/../x'))

with tempfile.TemporaryDirectory() as td:
    srv = ThreadingHTTPServer(('127.0.0.1', 0), IN.Handler)
    srv.folder = td
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    def post(path, body, origin=IN.ORIGIN):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=body, method='POST')
        if origin:
            req.add_header('Origin', origin)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or '{}')

    try:
        st, _ = post('/doc?case=%s&din=3&kind=COMPLAINT' % CASE, b'%PDF-1.4 fake')
        rec('1d: a PDF from the court origin is saved', st == 200 and os.path.exists(os.path.join(td, CASE + '_DIN3_COMPLAINT.pdf')), st)
        st, _ = post('/doc?case=%s&din=4&kind=COMPLAINT' % CASE, b'%PDF-1.4 fake', origin='https://evil.example')
        rec('1e: any other origin is refused', st == 403 and not os.path.exists(os.path.join(td, CASE + '_DIN4_COMPLAINT.pdf')), st)
        st, _ = post('/doc?case=%s&din=5&kind=COMPLAINT' % CASE, b'%PDF-1.4 fake', origin=None)
        rec('1f: a post with no origin is refused', st == 403, st)
        st, _ = post('/doc?case=%s&din=6&kind=COMPLAINT' % CASE, b'<html>login</html>')
        rec('1g: a body that is not a PDF is refused', st == 400 and not os.path.exists(os.path.join(td, CASE + '_DIN6_COMPLAINT.pdf')), st)
        st, _ = post('/doc?case=..%2F..%2Fx&din=3&kind=C', b'%PDF-1.4 fake')
        rec('1h: a traversal case number is refused', st == 400 and len(os.listdir(td)) == 1, (st, os.listdir(td)))
        st, body = post('/filings', json.dumps({'window': ['2026-11-01', '2026-11-15'], 'rows': [{'case': 'x'}]}).encode())
        rec('1i: a filings list is saved', st == 200 and body.get('rows') == 1
            and any(f.startswith('filings_') for f in os.listdir(td)), body)
        st, _ = post('/filings', b'{"rows": 5}')
        rec('1j: a filings post with no row list is refused', st == 400, st)

        # ---- 4. complaint_reader sees the posted complaint -----------------------------------------
        post('/doc?case=%s&din=12&kind=AMENDED_COMPLAINT' % CASE, b'%PDF-1.4 later')
        files = CR.pb_inbox_files(td)
        rec('4a: posted complaints are found by case; the later docket number wins',
            list(files) == [CASE] and files[CASE].endswith('_DIN3_COMPLAINT.pdf') is False, files)
    finally:
        srv.shutdown()
        srv.server_close()
rec('4b: Palm Beach cases match on the first 14 characters',
    CR.pb_key('50-2026-CA-099999-XXXX-MB') == CR.pb_key(CASE) == '502026CA099999' and CR.pb_key('CACE-26-1') == '')

# ---- 2. court row -> filing --------------------------------------------------------------------------
kind_of = lambda parties: 'BANK-1st' if 'BANK' in parties.upper() else 'OTHER/PRIVATE'
court = {'case': '50-2026-CA-099999-XXXA-MB', 'type': 'HR FORECLOSURE > $50K, < $250K',
         'filed': '11/13/2026', 'style': 'TEST BANK NA V BORROWER, PAT Q', 'status': 'Open'}
row = PF.to_row(court, kind_of)
rec('2a: a court row becomes a lis pendens row in the file shape',
    row and row['case'] == CASE and row['date'] == '11/13/2026' and row['plaintiff'] == 'TEST BANK NA'
    and row['owner'] == 'BORROWER, PAT Q' and row['county'] == 'PALM BEACH' and row['kind'] == 'BANK-1st'
    and row['parties'] == 'TEST BANK NA; BORROWER, PAT Q' and row['src'] == 'court', row)
need = {'bookpage', 'case', 'county', 'date', 'docType', 'instrument', 'kind', 'legal', 'owner', 'parties', 'plaintiff'}
rec('2b: it carries every key the existing rows carry', need <= set(row), need - set(row))
rec('2c: an association lien foreclosure is the HOA lane',
    PF.to_row(dict(court, type='ASSN LIEN FORECLOSURE =< $50K', style='PALM GROVE HOA INC V BORROWER, PAT'), kind_of)['kind'] == 'HOA/JUNIOR')
rec('2d: a timeshare is not a lead', PF.to_row(dict(court, type='TIMESHARE / FORECLOSURE'), kind_of) is None)
rec('2e: a closed case is not a lead', PF.to_row(dict(court, status='Closed'), kind_of) is None)
rec('2f: a non-foreclosure case type is not a lead', PF.to_row(dict(court, type='CONTRACT & INDEBTEDNESS'), kind_of) is None)
rec('2g: a style with no " V " is not usable', PF.to_row(dict(court, style='IN RE: SOMETHING'), kind_of) is None)
rec('2h: a bad case number is not usable', PF.to_row(dict(court, case='2026-CA-1'), kind_of) is None)
rec('2i: an ISO filing date is accepted', PF.to_row(dict(court, filed='2026-11-13'), kind_of)['date'] == '11/13/2026')

# ---- 3. merge ---------------------------------------------------------------------------------------------
existing = [{'county': 'PALM BEACH', 'case': '502026CA011111XXXXMB', 'date': '08/26/2026', 'kind': 'BANK-1st', 'instrument': '2026030'},
            {'county': 'BROWARD', 'case': 'CACE-26-000001', 'date': '11/13/2026', 'kind': 'BANK-1st'}]
snapshot = json.dumps(existing, sort_keys=True)
rows = [court, dict(court), dict(court, case='50-2026-CA-011111-XXXA-MB'), dict(court, type='TIMESHARE / FORECLOSURE', case='50-2026-CA-022222-XXXA-MB')]
merged, added, dup, bad = PF.merge(existing, rows, kind_of)
rec('3a: one new filing is added once', added == 1 and len(merged) == 3, (added, dup, bad))
rec('3b: a case already on file (other suffix) and a repeat are skipped', dup == 2, dup)
rec('3c: an unusable row is counted, not added', bad == 1, bad)
rec('3d: no existing row is changed', json.dumps(merged[:2], sort_keys=True) == snapshot)
rec('3e: the searches cover the homestead, non-homestead and association categories',
    any(t.startswith('HR FORECLOSURE') for t in PF.CASE_TYPES) and any(t.startswith('NON HR') for t in PF.CASE_TYPES)
    and any(t.startswith('ASSN') for t in PF.CASE_TYPES) and not any('TIMESHARE' in t for t in PF.CASE_TYPES))

# ---- 5. the collector --------------------------------------------------------------------------------------
import document_collectors as DC  # noqa: E402
import pb_recorded as PR  # noqa: E402
col = DC.PalmBeachCollector()
saved = PR._get
PR._get = lambda url, run=None: b'%PDF-1.4 five pages'
try:
    got = col.retrieve_document({'bp': 'O 36315/910'})
    rec('5a: the Palm Beach collector returns a recorded document by book/page',
        got['content'].startswith(b'%PDF-') and got['transport'] == 'palmbeach_landmark_direct_link', got.get('transport'))
    PR._get = lambda url, run=None: b'<html>error</html>'
    try:
        col.retrieve_document({'bp': 'O 36315/910'})
        rec('5b: a non-PDF answer is an access gap', False)
    except DC.AccessGap:
        rec('5b: a non-PDF answer is an access gap', True)
    try:
        col.retrieve_document({'bp': ''})
        rec('5c: a row with no reference is an access gap, not a fetch', False)
    except DC.AccessGap:
        rec('5c: a row with no reference is an access gap, not a fetch', True)
finally:
    PR._get = saved
src = open(os.path.join(HERE, 'document_collectors.py'), encoding='utf-8').read()
rec('5d: the collector no longer says Palm Beach is "Not implemented ... behind reCAPTCHA"',
    'Not implemented. Landmark/eCaseView is behind reCAPTCHA' not in src)

print('\n%d checks, %d failed' % (len(checks), len(fails)))
print('ALL PASS' if not fails else 'FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)
