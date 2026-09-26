"""_pacerstaytest -- the PACER (PCL) federal bankruptcy lookup, with mocked HTTP, and the stay gate reading it.

Run:  python _pacerstaytest.py     (exit 0 = pass; no network, no real PACER account, no real spend)

pacer_stay.py logs in to PACER's Authentication API, searches the PACER Case Locator by owner name,
decides a stay verdict per lead and writes pacer_stay_cache.json, which stay_gate.py (#72) reads.
Every name, case number and credential below is invented. Sections:
  1 settings (credentials from env only, PACER_ENV, TOTP)       7 verdict mapping
  2 authentication + logout + token refresh                     8 missing credentials: off, no crash
  3 the search request (endpoint, body, headers, region)        9 API errors fail closed
  4 pagination and the common-name overflow                    10 the cache (shape, no names, QA split)
  5 cost accounting (receipt, quarter ledger, #74 month)        11 prioritisation
  6 names: parsing and matching                                 12 caps exhausted: nothing searched
                                                                13 the stay gate reads it (+ live /send)
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import datetime as dt

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = pathlib.Path(tempfile.mkdtemp(prefix='pacerstay_'))
os.environ['DEALFLOW_PAID_LEDGER'] = str(TMP / 'paid_reads_ledger.json')
os.environ.pop('DEALFLOW_PAID_MONTHLY_CAP', None)
os.environ.pop('DEALFLOW_PACER_MAX_AGE_DAYS', None)
for _k in ('PACER_USERNAME', 'PACER_PASSWORD', 'PACER_OTP_SECRET', 'PACER_CLIENT_CODE', 'PACER_ENV',
           'PACER_QUARTER_CAP', 'PACER_RUN_MAX', 'PACER_QUARTER_LEDGER', 'PACER_REDACT_FLAG'):
    os.environ.pop(_k, None)

import paid_reads as PR
import pacer_stay as PS
import stay_gate as SG

PR.CONFIG = str(TMP / 'paid_reads.json')
FAILS = []
NOW = time.time()                  # real clock: the live bridge below ages entries on the real clock
TODAY = dt.date.fromtimestamp(NOW)
Q = PS.quarter_of(TODAY)


def iso(days):
    """ISO date `days` from today (negative = past)."""
    return (TODAY + dt.timedelta(days=days)).isoformat()


def mdy(days):
    return (TODAY + dt.timedelta(days=days)).strftime('%m/%d/%Y')


LOOKBACK = PS.lookback_from(TODAY, 8)
TOKEN = 'T' * 128


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)[:400]) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


# ------------------------------------------------------------------------------------ fakes
class Resp:
    def __init__(self, status=200, body=None, headers=None, not_json=False):
        self.status_code, self._body, self.headers, self._nj = status, body, headers or {}, not_json

    def json(self):
        if self._nj:
            raise ValueError('not json')
        return self._body


def page(rows, pg=0, total_pages=1, fee='.10', receipt=True, pageinfo=True):
    d = {'content': rows, 'masterCase': None}
    if receipt:
        d['receipt'] = {'transactionDate': '2026-09-26T11:00:00.000-0500', 'billablePages': 1,
                        'loginId': 'fakeuser', 'search': 'x', 'searchFee': fee}
    if pageinfo:
        d['pageInfo'] = {'number': pg, 'size': 54, 'totalPages': total_pages,
                         'totalElements': len(rows) + 54 * (total_pages - 1),
                         'numberOfElements': len(rows), 'first': pg == 0, 'last': pg + 1 >= total_pages}
    return Resp(200, d)


def row(last, first, middle='', court='flsbk', no='1:99-bk-10001', filed=None, termed=None,
        dismissed=None, discharged=None, reopened=None, role='db', juris='Bankruptcy', ctype='bk', ch='13'):
    filed = filed or iso(-25)
    cc = {'courtId': court, 'caseType': ctype, 'caseNumberFull': no, 'dateFiled': filed,
          'jurisdictionType': juris, 'bankruptcyChapter': ch, 'caseTitle': first + ' ' + last}
    if termed:
        cc['dateTermed'] = termed
        cc['effectiveDateClosed'] = termed
    r = {'courtId': court, 'lastName': last, 'firstName': first, 'middleName': middle or ' ', 'generation': ' ',
         'partyType': 'pty', 'partyRole': role, 'jurisdictionType': juris, 'courtCase': cc,
         'bankruptcyChapter': ch, 'dateFiled': filed, 'caseNumberFull': no, 'caseType': ctype,
         'caseTitle': first + ' ' + last}
    for k, v in (('dateTermed', termed), ('dateDismissed', dismissed), ('dateDischarged', discharged),
                 ('dateReopened', reopened)):
        if v:
            r[k] = v
    return r


class FakeHTTP:
    """Routes the three PACER endpoints. `by_name` maps (LAST, FIRST) -> rows | Resp | Exception |
    callable(body, page, headers)."""

    def __init__(self, by_name=None, login=None, default=None):
        self.by_name = by_name or {}
        self.login = login
        self.default = default
        self.calls = []

    def finds(self):
        return [c for c in self.calls if c[0] == 'find']

    def post(self, url, json=None, headers=None, timeout=None):
        if url.endswith('/services/cso-auth'):
            self.calls.append(('auth', url, json, headers))
            r = self.login(json) if self.login else Resp(200, {'loginResult': '0', 'nextGenCSO': TOKEN,
                                                               'errorDescription': ''})
        elif url.endswith('/services/cso-logout'):
            self.calls.append(('logout', url, json, headers))
            r = Resp(200, {'loginResult': '0', 'errorDescription': ''})
        elif '/pcl-public-api/rest/parties/find?page=' in url:
            pg = int(url.rsplit('page=', 1)[1])
            self.calls.append(('find', url, json, dict(headers or {})))
            k = (str(json.get('lastName', '')).upper(), str(json.get('firstName', '')).upper())
            v = self.by_name.get(k, self.default)
            if callable(v) and not isinstance(v, (Resp, Exception)):
                v = v(json, pg, headers)
            if v is None:
                v = page([], pg)
            elif isinstance(v, list):
                v = page(v, pg)
            r = v
        else:
            raise AssertionError('unexpected URL ' + url)
        if isinstance(r, Exception):
            raise r
        return r


def work(files=None):
    d = pathlib.Path(tempfile.mkdtemp(prefix='pacerwork_', dir=TMP))
    for name, obj in (files or {}).items():
        (d / name).write_text(json.dumps(obj), encoding='utf-8')
    return d


def reset_ledgers():
    for p in (TMP / 'paid_reads_ledger.json', TMP / 'pacer_q.json'):
        if p.exists():
            p.unlink()
    PR._BROKEN['why'] = ''
    PR._SAID.clear()


def env(**kw):
    e = {'PACER_USERNAME': 'fakeuser', 'PACER_PASSWORD': 'fake-pass-123',
         'PACER_QUARTER_LEDGER': str(TMP / 'pacer_q.json')}
    e.update({k: str(v) for k, v in kw.items() if v is not None})
    for k in [k for k, v in kw.items() if v is None]:
        e.pop(k, None)
    return e


def run(d, http, e=None, args=None, now=NOW):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = PS.main(args or [], session=http, here=str(d), env=e if e is not None else env(), now=now)
    return rc, buf.getvalue()


def qledger():
    p = TMP / 'pacer_q.json'
    return json.loads(p.read_text()) if p.exists() else {}


def month_spent():
    return float(PR.status()['by'].get('pacer_stay', 0) or 0)


BR = [{'county': 'BROWARD', 'case': 'CACE-99-000101', 'owners': 'TESTPERSON QUINCY', 'auction': mdy(24), 'st': 'FC'}]


# ------------------------------------------------------------------------------------ 1 settings
print('-- 1 settings')
check('no PACER_USERNAME / PASSWORD -> no credentials', PS.credentials({}) is None
      and PS.credentials({'PACER_USERNAME': 'u'}) is None and PS.credentials({'PACER_PASSWORD': 'p'}) is None)
c = PS.credentials({'PACER_USERNAME': ' u ', 'PACER_PASSWORD': 'p', 'PACER_CLIENT_CODE': 'cc'})
check('credentials read from the environment only', c and c['user'] == 'u' and c['client'] == 'cc' and not c['redact'])
check('PACER_ENV defaults to prod; qa selects QA; junk refuses', PS.environment({}) == 'prod'
      and PS.environment({'PACER_ENV': 'QA'}) == 'qa' and PS.environment({'PACER_ENV': 'staging'}) == '')
check('QA hosts are qa-login / qa-pcl, production pacer.login / pcl',
      PS.HOSTS['qa'] == {'auth': 'https://qa-login.uscourts.gov', 'pcl': 'https://qa-pcl.uscourts.gov'}
      and PS.HOSTS['prod'] == {'auth': 'https://pacer.login.uscourts.gov', 'pcl': 'https://pcl.uscourts.gov'})
check('TOTP matches RFC 6238 (SHA1, T=59 -> 287082)', PS.totp('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ', now=59) == '287082')
check('TOTP at T=1111111109 -> 081804', PS.totp('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ', now=1111111109) == '081804')
check('a bad quarter cap is $0 (fail closed)', PS._money_setting('PACER_QUARTER_CAP', 25, {'PACER_QUARTER_CAP': 'lots'})[0] == 0
      and PS._money_setting('PACER_QUARTER_CAP', 25, {'PACER_QUARTER_CAP': '-3'})[0] == 0
      and PS._money_setting('PACER_QUARTER_CAP', 25, {})[0] == 25)
check('quarters and nights left', PS.quarter_of(dt.date(2026, 9, 26)) == '2026-Q3' and PS.quarter_days_left(dt.date(2026, 9, 26)) == 5
      and PS.quarter_days_left(dt.date(2026, 10, 1)) == 92 and PS.quarter_of(dt.date(2026, 12, 31)) == '2026-Q4')
check('auto run cap = quarter remaining / nights left, in whole pages, <= ceiling',
      PS.auto_run_cap({'ok': True, 'remaining': 25.0}, dt.date(2026, 7, 1), 2.0) == 0.2
      and PS.auto_run_cap({'ok': True, 'remaining': 25.0}, dt.date(2026, 9, 26), 2.0) == 2.0
      and PS.auto_run_cap({'ok': True, 'remaining': 0.05}, TODAY, 2.0) == 0.0
      and PS.auto_run_cap({'ok': False, 'remaining': 9}, TODAY, 2.0) == 0.0)
check('lookback is 8 years back from today (Feb 29 falls back to Feb 28)',
      PS.lookback_from(dt.date(2026, 9, 26), 8) == '2018-09-26'
      and PS.lookback_from(dt.date(2028, 2, 29), 8) == '2020-02-29'
      and PS.lookback_from(dt.date(2028, 2, 29), 1) == '2027-02-28')

# ------------------------------------------------------------------------------------ 2 auth
print('-- 2 authentication')
creds = {'user': 'fakeuser', 'pw': 'fake-pass-123', 'otp_secret': '', 'client': '', 'redact': False}
h = FakeHTTP()
p = PS.PCL('prod', creds, session=h)
p.login()
a = h.calls[0]
check('login POSTs to https://pacer.login.uscourts.gov/services/cso-auth',
      a[1] == 'https://pacer.login.uscourts.gov/services/cso-auth')
check('login body: loginId + password only (no client code, OTP or redaction flag unless set)',
      a[2] == {'loginId': 'fakeuser', 'password': 'fake-pass-123'}, a[2])
check('login sends/accepts JSON', a[3].get('Content-Type') == 'application/json' and a[3].get('Accept') == 'application/json')
check('token kept from nextGenCSO', p.token == TOKEN)
p.logout()
check('logout POSTs the token to /services/cso-logout and forgets it',
      h.calls[-1][1].endswith('/services/cso-logout') and h.calls[-1][2] == {'nextGenCSO': TOKEN} and p.token == '')
h = FakeHTTP()
p = PS.PCL('qa', dict(creds, client='CC1', otp_secret='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ', redact=True), session=h, now=59)
p.login()
check('QA login goes to qa-login; clientCode, otpCode (TOTP) and redactFlag when configured',
      h.calls[0][1] == 'https://qa-login.uscourts.gov/services/cso-auth'
      and h.calls[0][2] == {'loginId': 'fakeuser', 'password': 'fake-pass-123', 'clientCode': 'CC1',
                            'otpCode': '287082', 'redactFlag': '1'}, h.calls[0][2])
for label, resp in (('loginResult 13 (bad password / OTP)', Resp(200, {'loginResult': '13', 'nextGenCSO': '',
                                                                    'errorDescription': 'Invalid username, password, or one-time passcode.'})),
                    ('HTTP 500', Resp(500, {})), ('non-JSON', Resp(200, not_json=True)),
                    ('loginResult 0 but no token', Resp(200, {'loginResult': '0', 'nextGenCSO': ''}))):
    h = FakeHTTP(login=lambda b, r=resp: r)
    try:
        PS.PCL('prod', creds, session=h).login()
        ok = False
    except PS.AuthError:
        ok = True
    check('login fails closed on %s' % label, ok)
h = FakeHTTP(login=lambda b: Resp(200, {'loginResult': '0', 'nextGenCSO': TOKEN, 'errorDescription':
                                        'A required Client Code was not entered. You may continue to log in and perform other '
                                        'activities (e.g., e-file, request filing privileges), but you will not have PACER search privileges.'}))
p = PS.PCL('prod', creds, session=h)
try:
    p.login()
    ok = False
except PS.AuthError:
    ok = True
check('loginResult 0 with "no PACER search privileges" is a failure, and the token is still logged out',
      ok and p.token == TOKEN and p.logout() and h.calls[-1][0] == 'logout')
# token refresh from the response header
h = FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): Resp(200, page([])._body, headers={'X-NEXT-GEN-CSO': 'N' * 128})})
p = PS.PCL('prod', creds, session=h)
p.login()
p.find_parties({'lastName': 'TESTPERSON', 'firstName': 'QUINCY'}, 0)
check('a re-issued X-NEXT-GEN-CSO response header replaces the token', p.token == 'N' * 128)

# ------------------------------------------------------------------------------------ 3 search request
print('-- 3 the search request')
reset_ledgers()
d = work({'broward_leads.json': BR})
h = FakeHTTP()
rc, out = run(d, h)
f = h.finds()
check('one lead, one name -> one party search', rc == 0 and len(f) == 1, (rc, out))
url, body, hdr = f[0][1], f[0][2], f[0][3]
check('POST https://pcl.uscourts.gov/pcl-public-api/rest/parties/find?page=0',
      url == 'https://pcl.uscourts.gov/pcl-public-api/rest/parties/find?page=0', url)
check('body: lastName/firstName, starts-with (exactNameMatch false), bankruptcy only, filed since lookback, national',
      body == {'lastName': 'TESTPERSON', 'firstName': 'QUINCY', 'exactNameMatch': False,
               'courtCase': {'jurisdictionType': 'bk', 'dateFiledFrom': LOOKBACK}}, body)
check('headers: token in X-NEXT-GEN-CSO, JSON both ways, no client code header unless configured',
      hdr.get('X-NEXT-GEN-CSO') == TOKEN and hdr.get('Content-Type') == 'application/json'
      and 'X-CLIENT-CODE' not in hdr, hdr)
check('login first, logout last', h.calls[0][0] == 'auth' and h.calls[-1][0] == 'logout')
reset_ledgers()
h = FakeHTTP()
run(work({'broward_leads.json': BR}), h, env(PACER_CLIENT_CODE='CC1'), ['--region', 'fl'])
b = h.finds()[0][2]
check('--region fl limits the search to the three Florida bankruptcy courts; X-CLIENT-CODE sent when set',
      b['courtCase'].get('courtId') == ['flsbk', 'flmbk', 'flnbk'] and h.finds()[0][3].get('X-CLIENT-CODE') == 'CC1', b)

# ------------------------------------------------------------------------------------ 4 pagination
print('-- 4 pagination')


def three_pages(body, pg, headers):
    rows = [row('TESTPERSON', 'QUINCYX%d' % i, no='1:99-bk-2%04d' % i, termed='2020-01-01') for i in range(54 if pg < 2 else 3)]
    return page(rows, pg, total_pages=3)


reset_ledgers()
d = work({'broward_leads.json': BR})
h = FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): three_pages})
rc, out = run(d, h)
ent = json.loads((d / 'pacer_stay_cache.json').read_text())['CACE-99-000101']
check('3 pages of results, --max-pages 1: only page 0 is bought', len(h.finds()) == 1)
check('... and the lead is unverifiable (common name), never clear',
      ent['verdict'] == 'unverifiable' and 'common name' in ent['why'], ent)
reset_ledgers()
d = work({'broward_leads.json': BR})
h = FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): three_pages})
rc, out = run(d, h, args=['--max-pages', '3'])
ent = json.loads((d / 'pacer_stay_cache.json').read_text())['CACE-99-000101']
check('--max-pages 3 walks page=0,1,2', [c[1].rsplit('=', 1)[1] for c in h.finds()] == ['0', '1', '2'])
check('... all pages read and every row closed -> clear, 3 pages billed',
      ent['verdict'] == 'clear' and ent['pages'] == 3 and abs(ent['cost'] - 0.30) < 1e-9, ent)

# ------------------------------------------------------------------------------------ 5 cost
print('-- 5 cost accounting')
reset_ledgers()
d = work({'broward_leads.json': BR})
rc, out = run(d, FakeHTTP())
q = qledger().get(Q, {})
check('a search that finds nothing still costs its page: $0.10 in the quarter ledger',
      abs(q.get('total', 0) - 0.10) < 1e-9 and q.get('pages') == 1 and q.get('searches') == 1, q)
check('... and in #74\'s monthly paid-reads ledger as spender pacer_stay', abs(month_spent() - 0.10) < 1e-9, PR.status())
reset_ledgers()
d = work({'broward_leads.json': BR})
run(d, FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): page([], fee='.20')}))
check('settled to the receipt\'s searchFee ($0.20), both ledgers', abs(qledger()[Q]['total'] - 0.20) < 1e-9
      and abs(month_spent() - 0.20) < 1e-9)
reset_ledgers()
d = work({'broward_leads.json': BR})
run(d, FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): page([], receipt=False)}))
check('no receipt: the $0.10 reservation stands (runs high, never low)', abs(qledger()[Q]['total'] - 0.10) < 1e-9
      and abs(month_spent() - 0.10) < 1e-9)
check('receipt_fee: ".10" -> 0.1, billablePages 2 -> 0.2, nothing -> None',
      PS.receipt_fee({'searchFee': '.10'}) == 0.1 and PS.receipt_fee({'billablePages': 2}) == 0.2
      and PS.receipt_fee({}) is None and PS.receipt_fee(None) is None)
for code, kept in ((406, 0.0), (400, 0.0), (500, 0.10), (502, 0.10)):
    reset_ledgers()
    d = work({'broward_leads.json': BR})
    run(d, FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): Resp(code, {})}))
    check('HTTP %d: %s' % (code, 'refunded (PCL does not bill it)' if not kept else 'reservation kept (billing unknown)'),
          abs(qledger().get(Q, {}).get('total', 0) - kept) < 1e-9 and abs(month_spent() - kept) < 1e-9,
          (qledger(), month_spent()))
reset_ledgers()
d = work({'broward_leads.json': BR})
run(d, FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): TimeoutError('slow')}))
check('a timeout keeps the reservation', abs(month_spent() - 0.10) < 1e-9)
reset_ledgers()
calls = {'n': 0}


def expire_once(body, pg, headers):
    calls['n'] += 1
    return Resp(401, {}) if calls['n'] == 1 else page([])


h = FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): expire_once})
d = work({'broward_leads.json': BR})
rc, out = run(d, h)
check('401 (expired token): logs in again, retries once, bills one page not two',
      [c[0] for c in h.calls] == ['auth', 'find', 'auth', 'find', 'logout'] and abs(month_spent() - 0.10) < 1e-9
      and json.loads((d / 'pacer_stay_cache.json').read_text())['CACE-99-000101']['verdict'] == 'clear',
      ([c[0] for c in h.calls], month_spent()))
reset_ledgers()
d = work({'broward_leads.json': BR})
h = FakeHTTP()
rc, out = run(d, h, env(PACER_ENV='qa'))
check('QA: searches go to qa-pcl and debit NOTHING (not billable)',
      h.finds()[0][1].startswith('https://qa-pcl.uscourts.gov/') and qledger() == {} and month_spent() == 0)

# ------------------------------------------------------------------------------------ 6 names
print('-- 6 names')


def qs(raw, order):
    sj, problem = PS.parse_owner(raw, order)
    return [s.queries() for s in sj], problem


check('FDOR "LAST FIRST" -> one search', qs('TESTPERSON QUINCY', 'last_first') == ([[{'last': 'TESTPERSON', 'first': 'QUINCY'}]], ''))
check('recorder "LAST,FIRST M" -> one search', qs('TESTPERSON,QUINCY Z', 'last_first') == ([[{'last': 'TESTPERSON', 'first': 'QUINCY'}]], ''))
check('"LAST FIRST M" (initial is never a first name) -> one search',
      qs('TESTPERSON QUINCY Z', 'last_first') == ([[{'last': 'TESTPERSON', 'first': 'QUINCY'}]], ''))
check('ambiguous "LAST1 LAST2 FIRST" / "LAST FIRST MIDDLE" -> both readings searched',
      qs('FAKEGARCIA FAKELOPEZ MARIELA', 'last_first')[0] == [[{'last': 'FAKEGARCIA', 'first': 'FAKELOPEZ'},
                                                            {'last': 'FAKEGARCIA', 'first': 'MARIELA'}]])
check('Miami-Dade "FIRST MIDDLE LAST" / "FIRST LAST1 LAST2" -> both surnames searched',
      qs('MARIELA FAKEGARCIA FAKELOPEZ', 'first_last')[0] == [[{'last': 'FAKELOPEZ', 'first': 'MARIELA'},
                                                            {'last': 'FAKEGARCIA', 'first': 'MARIELA'}]])
check('co-owner bare first name shares the surname ("SMITH JOHN & MARY")',
      qs('TESTPERSON QUINCY & ROSALIND', 'last_first')[0] == [[{'last': 'TESTPERSON', 'first': 'QUINCY'}],
                                                             [{'last': 'TESTPERSON', 'first': 'ROSALIND'}]])
check('Miami-Dade "&W" co-owner shares the surname', qs('QUINCY TESTPERSON &W ROSALIND', 'first_last')[0]
      == [[{'last': 'TESTPERSON', 'first': 'QUINCY'}], [{'last': 'TESTPERSON', 'first': 'ROSALIND'}]])
check('co-owner "& MARY ANN" also searched as MARY (ANN) with the shared surname',
      {'last': 'TESTPERSON', 'first': 'ROSALIND'} in qs('TESTPERSON QUINCY & ROSALIND JEAN', 'last_first')[0][1])
check('"; "-joined appraiser owners are separate people', len(qs('QUINCY TESTPERSON; ROSALIND OTHERNAME', 'first_last')[0]) == 2)
check('entity -> one search on the name without its suffix',
      qs('FAKE HOLDINGS 12 LLC', 'last_first') == ([[{'last': 'FAKE HOLDINGS 12', 'first': ''}]], ''))
for raw, why in (('ESTATE OF QUINCY TESTPERSON', 'estate'), ('TESTPERSON QUINCY TR', 'trust'),
                 ('QUINCY TESTPERSON REVOCABLE TRUST', 'trust'), ('CITY OF FAKEVILLE', 'government'),
                 ('(owner via title search)', 'no owner'), ('TESTPERSON', 'one-word'),
                 ('A B C D E F', 'more than four'), ('', 'no owner')):
    sj, problem = PS.parse_owner(raw, 'last_first')
    check('not searched (no cost): %r -> %s' % (raw, why), not sj and why in problem, problem)
check('five owners on a lead is unverifiable', PS.lead_subjects([('A1 BB; C1 DD; E1 FF; G1 HH; I1 JJ', 'last_first')])[1] != '')
check('"UNKNOWN SPOUSE OF ..." placeholder dropped, the real owner still searched',
      qs('UNKNOWN SPOUSE OF QUINCY TESTPERSON; TESTPERSON,QUINCY', 'last_first')[0] == [[{'last': 'TESTPERSON', 'first': 'QUINCY'}]])
check('accents, hyphens and apostrophes normalise', PS.name_tokens("José O'Fake-Pérez") == ['JOSE', 'OFAKE', 'PEREZ'])
P1 = PS.parse_owner('TESTPERSON,QUINCY Z', 'last_first')[0][0]
check('match: same first + surname + middle initial -> strong', PS.match_row(row('Testperson', 'Quincy', 'Zeb'), P1) == 'strong')
check('match: no middle on the record -> strong', PS.match_row(row('TESTPERSON', 'QUINCY'), P1) == 'strong')
check('match: first initial only -> weak', PS.match_row(row('TESTPERSON', 'Q'), P1) == 'weak')
check('match: longer first name (starts-with hit) -> weak', PS.match_row(row('TESTPERSON', 'QUINCYANNE'), P1) == 'weak')
check('match: different middle initial -> weak (not dropped)', PS.match_row(row('TESTPERSON', 'QUINCY', 'A'), P1) == 'weak')
check('match: extra surname on the record -> weak', PS.match_row(row('TESTPERSON OTHERNAME', 'QUINCY'), P1) == 'weak')
check('match: different surname (starts-with noise) -> none', PS.match_row(row('TESTPERSONS', 'QUINCY'), P1) is None)
check('match: different first name -> none', PS.match_row(row('TESTPERSON', 'ROSALIND'), P1) is None)
P2 = PS.parse_owner('MARIELA FAKEGARCIA FAKELOPEZ', 'first_last')[0][0]
check('compound surname: record "FAKEGARCIA-FAKELOPEZ, MARIELA" -> strong',
      PS.match_row(row('FAKEGARCIA-FAKELOPEZ', 'MARIELA'), P2) == 'strong')
check('compound surname: record "FAKEGARCIA, MARIELA" -> strong (maternal surname dropped)',
      PS.match_row(row('FAKEGARCIA', 'MARIELA'), P2) == 'strong')
E1 = PS.parse_owner('FAKE HOLDINGS 12 LLC', 'last_first')[0][0]
check('entity match ignores the suffix', PS.match_row(row('Fake Holdings 12, L.L.C.', ''), E1) in ('strong',)
      and PS.match_row(row('FAKE HOLDINGS 12 II LLC', ''), E1) == 'weak')

# ------------------------------------------------------------------------------------ 7 verdicts
print('-- 7 verdict mapping')
S1 = [P1]


def verdict(rows, overflow=False):
    return PS.decide(S1, [(0, {'rows': rows, 'overflow': overflow, 'pages': 1})], TODAY, LOOKBACK)


v = verdict([])
check('no rows -> clear', v[0] == 'clear')
v = verdict([row('TESTPERSON', 'QUINCY', no='1:26-bk-10001', filed=iso(-6))])
check('exact name, open case in flsbk -> ACTIVE, filing date and case number recorded',
      v[0] == 'active' and v[3] == iso(-6) and v[2][0]['no'] == '1:26-bk-10001' and v[2][0]['open'], v)
check('the stored case has no name or caption', 'QUINCY' not in json.dumps(v[2]) and 'caseTitle' not in json.dumps(v[2]))
check('open case in the Middle District of Florida -> active', verdict([row('TESTPERSON', 'QUINCY', court='flmbk')])[0] == 'active')
check('closed case -> clear', verdict([row('TESTPERSON', 'QUINCY', termed='2024-01-05')])[0] == 'clear')
check('dismissed 60 days ago, not yet closed -> clear', verdict([row('TESTPERSON', 'QUINCY', dismissed=iso(-60))])[0] == 'clear')
check('dismissed 10 days ago, not yet closed -> unverifiable (reinstatement window)',
      verdict([row('TESTPERSON', 'QUINCY', dismissed=iso(-10))])[0] == 'unverifiable')
check('closed then reopened -> active', verdict([row('TESTPERSON', 'QUINCY', termed=iso(-400), reopened=iso(-56))])[0] == 'active')
check('discharged but still open -> active', verdict([row('TESTPERSON', 'QUINCY', discharged=iso(-117))])[0] == 'active')
check('exact name, open case in another state -> unverifiable (namesake possible), not clear, not active',
      verdict([row('TESTPERSON', 'QUINCY', court='nysbk')])[0] == 'unverifiable')
check('similar name (initial), open in flsbk -> unverifiable', verdict([row('TESTPERSON', 'Q')])[0] == 'unverifiable')
check('similar name but CLOSED -> clear', verdict([row('TESTPERSON', 'Q', termed='2021-01-01')])[0] == 'clear')
check('creditor row ignored', verdict([row('TESTPERSON', 'QUINCY', role='cr')])[0] == 'clear')
check('adversary proceeding ignored', verdict([row('TESTPERSON', 'QUINCY', ctype='ap')])[0] == 'clear')
check('civil case ignored', verdict([row('TESTPERSON', 'QUINCY', court='flsdc', juris='Civil', ctype='cv')])[0] == 'clear')
check('filed before the lookback ignored', verdict([row('TESTPERSON', 'QUINCY', filed='2015-01-01')])[0] == 'clear')
check('overflow (unread pages) -> unverifiable even with no match on page 0', verdict([], overflow=True)[0] == 'unverifiable')
check('active beats every doubt', verdict([row('TESTPERSON', 'Q'), row('TESTPERSON', 'QUINCY', no='1:26-bk-3')], overflow=True)[0] == 'active')
check('an unknown role still counts (fail closed)', verdict([row('TESTPERSON', 'QUINCY', role='')])[0] == 'active')

# ------------------------------------------------------------------------------------ 8 missing credentials
print('-- 8 missing credentials')
reset_ledgers()
d = work({'broward_leads.json': BR})
h = FakeHTTP()
rc, out = run(d, h, env(PACER_USERNAME=None, PACER_PASSWORD=None))
check('no credentials: exit 0, says the lookup is OFF, no HTTP at all, nothing written or spent',
      rc == 0 and 'OFF' in out and h.calls == [] and not (d / 'pacer_stay_cache.json').exists()
      and qledger() == {} and month_spent() == 0, (rc, out))
rc, out = run(d, h, env(PACER_PASSWORD=None))
check('username without password is also OFF', rc == 0 and 'OFF' in out and h.calls == [])
check('the password is never printed', 'fake-pass-123' not in out)
rc, out = run(d, FakeHTTP(), env(PACER_ENV='staging'))
check('PACER_ENV junk: refused (exit 3), no HTTP', rc == 3)

# ------------------------------------------------------------------------------------ 9 API errors
print('-- 9 API errors fail closed')
reset_ledgers()
d = work({'broward_leads.json': BR})
h = FakeHTTP(login=lambda b: Resp(200, {'loginResult': '13', 'nextGenCSO': '', 'errorDescription': 'Invalid username'}))
rc, out = run(d, h)
check('login refused: exit 3, no search, no cache, nothing spent', rc == 3 and not h.finds()
      and not (d / 'pacer_stay_cache.json').exists() and month_spent() == 0, out)
check('login refused: the password is not in the log', 'fake-pass-123' not in out)
reset_ledgers()
d = work({'broward_leads.json': BR})
rc, out = run(d, FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): Resp(500, {})}))
ent = json.loads((d / 'pacer_stay_cache.json').read_text())['CACE-99-000101']
check('HTTP 500, no earlier verdict: written as unverifiable with err (the gate refuses it)',
      ent['verdict'] == 'unverifiable' and ent.get('err') and SG.pacer_verdict(ent)[0] == SG.UNVERIFIED, ent)
old = {'CACE-99-000101': {'verdict': 'clear', 'a': False, 'env': 'prod', 't': NOW - 5 * 86400, 'q': 'x', 'src': 'pacer_pcl'}}
d = work({'broward_leads.json': BR, 'pacer_stay_cache.json': old})
reset_ledgers()
rc, out = run(d, FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): Resp(503, {})}), args=['--case', 'CACE-99-000101'])
ent = json.loads((d / 'pacer_stay_cache.json').read_text())['CACE-99-000101']
check('HTTP 503 on a re-check: the earlier clear is NOT refreshed (t unchanged, err + tried noted), so it ages out',
      ent['verdict'] == 'clear' and ent['t'] == NOW - 5 * 86400 and ent.get('err') and ent.get('tried') == round(NOW, 1), ent)
reset_ledgers()
three = [{'county': 'BROWARD', 'case': 'CACE-99-00020%d' % i, 'owners': 'FAKEONE%s PERSON' % 'ABCDE'[i],
          'auction': mdy(50 + i), 'st': 'FC'} for i in range(5)]
d = work({'broward_leads.json': three})
h = FakeHTTP(default=Resp(500, {}))
rc, out = run(d, h, args=['--max-spend', '2'])
check('three leads in a row fail: the run stops (exit 3) instead of paying for a dead API', rc == 3 and len(h.finds()) == 3, (rc, len(h.finds())))
reset_ledgers()
d = work({'broward_leads.json': three})
h = FakeHTTP(default=Resp(429, {}))
rc, out = run(d, h, args=['--max-spend', '2'])
check('HTTP 429: stop at once (exit 3), refunded, logout still called', rc == 3 and len(h.finds()) == 1 and month_spent() == 0
      and h.calls[-1][0] == 'logout')
reset_ledgers()
d = work({'broward_leads.json': BR})
h = FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): Resp(200, not_json=True)})
rc, out = run(d, h)
ent = json.loads((d / 'pacer_stay_cache.json').read_text())['CACE-99-000101']
check('garbage response: unverifiable, never clear', ent['verdict'] == 'unverifiable')
reset_ledgers()
d = work({'broward_leads.json': BR})
h = FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): Resp(200, {'receipt': {'searchFee': '.10'}, 'content': 'oops'})})
run(d, h)
check('content not a list: unverifiable', json.loads((d / 'pacer_stay_cache.json').read_text())['CACE-99-000101']['verdict'] == 'unverifiable')

# ------------------------------------------------------------------------------------ 10 cache
print('-- 10 the cache')
reset_ledgers()
leads_md = [{'Case #': '2099-000301-CA-01', 'owners': 'QUINCY TESTPERSON; ROSALIND TESTPERSON', 'AuctionDate': mdy(34)}]
d = work({'broward_leads.json': BR, 'leads_final.json': leads_md})
rc, out = run(d, FakeHTTP(by_name={('TESTPERSON', 'QUINCY'): [row('TESTPERSON', 'QUINCY', no='1:26-bk-77777', filed=iso(-6))]}),
              args=['--max-spend', '1'])
raw = (d / 'pacer_stay_cache.json').read_text()
cache = json.loads(raw)
e = cache.get('CACE-99-000101', {})
check('entry carries verdict, a/bd/sl, src, env, query time, county, cost and matched case numbers',
      e.get('verdict') == 'active' and e.get('a') is True and e.get('bd') == iso(-6) and e.get('sl') == ''
      and e.get('src') == 'pacer_pcl' and e.get('env') == 'prod'
      and abs(dt.datetime.fromisoformat(e.get('q')).timestamp() - NOW) < 2 and e.get('q')[-6] in '+-'
      and e.get('county') == 'BROWARD' and e['cases'][0]['no'] == '1:26-bk-77777', e)
check('no owner names, no captions anywhere in the cache file', 'QUINCY' not in raw.upper() and 'ROSALIND' not in raw.upper()
      and 'caseTitle' not in raw)
check('co-owners on the Miami-Dade lead were both searched (same case, same verdict)',
      cache.get('2099-000301-CA-01', {}).get('searches') == 2 and cache['2099-000301-CA-01']['verdict'] == 'active')
check('the log names no owner', 'QUINCY' not in out.upper() and 'ROSALIND' not in out.upper())
reset_ledgers()
d = work({'broward_leads.json': BR})
run(d, FakeHTTP(), env(PACER_ENV='qa'))
check('QA verdicts go to pacer_stay_cache_qa.json, never the file the gate reads',
      (d / 'pacer_stay_cache_qa.json').exists() and not (d / 'pacer_stay_cache.json').exists())
reset_ledgers()
d = work({'broward_leads.json': [{'county': 'BROWARD', 'case': 'CACE-99-000102', 'owners': 'TESTPERSON QUINCY TR',
                                  'auction': mdy(24)}]})
h = FakeHTTP()
run(d, h)
ent = json.loads((d / 'pacer_stay_cache.json').read_text())['CACE-99-000102']
check('an unsearchable owner is written unverifiable at no cost (the gate says why)', ent['verdict'] == 'unverifiable'
      and 'trust' in ent['why'] and not h.finds() and month_spent() == 0)

# ------------------------------------------------------------------------------------ 11 prioritisation
print('-- 11 prioritisation')
leads = {
    'CACE-99-000501': {'key': 'CACE-99-000501', 'county': 'BROWARD', 'owners': [('A1 BB', 'last_first')], 'auction': TODAY + dt.timedelta(days=40), 'filed': None},
    'CACE-99-000502': {'key': 'CACE-99-000502', 'county': 'BROWARD', 'owners': [('A1 BB', 'last_first')], 'auction': TODAY + dt.timedelta(days=5), 'filed': None},
    '2099-000503-CA-01': {'key': '2099-000503-CA-01', 'county': 'MIAMI-DADE', 'owners': [('A1 BB', 'first_last')], 'auction': TODAY + dt.timedelta(days=3), 'filed': None},
    '2099-000504-CA-01': {'key': '2099-000504-CA-01', 'county': 'MIAMI-DADE', 'owners': [('A1 BB', 'first_last')], 'auction': TODAY + dt.timedelta(days=30), 'filed': None},
    'CACE-99-000505': {'key': 'CACE-99-000505', 'county': 'MIAMI-DADE', 'owners': [('A1 BB', 'last_first')], 'auction': None, 'filed': TODAY - dt.timedelta(days=2)},
    'CACE-99-000506': {'key': 'CACE-99-000506', 'county': 'BROWARD', 'owners': [('A1 BB', 'last_first')], 'auction': TODAY + dt.timedelta(days=50), 'filed': None},
    'CACE-99-000507': {'key': 'CACE-99-000507', 'county': 'BROWARD', 'owners': [('A1 BB', 'last_first')], 'auction': TODAY + dt.timedelta(days=60), 'filed': None},
    'CACE-99-000508': {'key': 'CACE-99-000508', 'county': 'BROWARD', 'owners': [('A1 BB', 'last_first')], 'auction': TODAY - dt.timedelta(days=3), 'filed': None},
    'CACE-99-000509': {'key': 'CACE-99-000509', 'county': 'BROWARD', 'owners': [('A1 BB', 'last_first')], 'auction': TODAY + dt.timedelta(days=45), 'filed': None},
}
cache = {
    'CACE-99-000506': {'verdict': 'clear', 't': NOW - 20 * 86400},       # stale (> 12 days)
    'CACE-99-000507': {'verdict': 'clear', 't': NOW - 2 * 86400},        # fresh: skipped
    'CACE-99-000509': {'verdict': 'unverifiable', 't': NOW - 20 * 86400},  # ambiguous: 30-day cadence
}
order = [ld['key'] for ld, _, _ in PS.plan(leads, cache, TODAY, NOW)]
check('near-sale first, then never-checked, then stale; in each tier no-stem (PACER-only) leads before Miami-Dade, soonest first',
      order == ['CACE-99-000502', '2099-000503-CA-01', 'CACE-99-000501', 'CACE-99-000505', '2099-000504-CA-01',
                'CACE-99-000506'], order)
check('fresh clear, not-yet-due ambiguous and past sales are skipped', all(k not in order for k in
      ('CACE-99-000507', 'CACE-99-000508', 'CACE-99-000509')))
near_cache = {'CACE-99-000502': {'verdict': 'clear', 't': NOW - 4 * 86400}}
check('near-sale clear re-checked after 3 days', 'CACE-99-000502' in [ld['key'] for ld, _, _ in PS.plan(leads, near_cache, TODAY, NOW)])
far_cache = {'CACE-99-000501': {'verdict': 'clear', 't': NOW - 4 * 86400}}
check('a far clear is not re-checked at 4 days', 'CACE-99-000501' not in [ld['key'] for ld, _, _ in PS.plan(leads, far_cache, TODAY, NOW)])
act_cache = {'CACE-99-000501': {'verdict': 'active', 't': NOW - 8 * 86400}}
check('an active stay is re-checked after 7 days (to see a dismissal)', 'CACE-99-000501' in [ld['key'] for ld, _, _ in PS.plan(leads, act_cache, TODAY, NOW)])
err_cache = {'CACE-99-000501': {'verdict': 'clear', 't': NOW - 5 * 86400, 'err': 'PCL HTTP 500', 'tried': NOW - 1.1 * 86400}}
check('an errored lead is retried the next day', 'CACE-99-000501' in [ld['key'] for ld, _, _ in PS.plan(leads, err_cache, TODAY, NOW)])
check('recheck cadence stays inside the gate\'s 14-day expiry', PS.RECHECK['clear'] < SG.DEFAULT_PACER_MAX_AGE_DAYS)
reset_ledgers()
lf = [{'county': 'BROWARD', 'case': 'CACE-99-0006%02d' % i, 'owners': 'FAKEP%s PERSON' % chr(65 + i),
       'auction': (TODAY + dt.timedelta(days=30 + i)).strftime('%m/%d/%Y')} for i in range(6)]
d = work({'broward_leads.json': lf})
h = FakeHTTP()
rc, out = run(d, h, args=['--max-spend', '0.30'])
check('--max-spend 0.30 buys exactly three one-name leads, soonest first',
      [c[2]['firstName'] for c in h.finds()] == ['PERSON'] * 3 and len(h.finds()) == 3
      and sorted(json.loads((d / 'pacer_stay_cache.json').read_text())) == ['CACE-99-000600', 'CACE-99-000601', 'CACE-99-000602']
      and abs(month_spent() - 0.30) < 1e-9, (len(h.finds()), month_spent()))
reset_ledgers()
two = [{'county': 'BROWARD', 'case': 'CACE-99-000701', 'owners': 'TESTPERSON QUINCY & OTHERP ROSALIND', 'auction': mdy(20)},
       {'county': 'BROWARD', 'case': 'CACE-99-000702', 'owners': 'LONER SOLO', 'auction': mdy(21)}]
d = work({'broward_leads.json': two})
h = FakeHTTP()
rc, out = run(d, h, args=['--max-spend', '0.10'])
check('a lead whose searches do not all fit is not started; a cheaper one is',
      [c[2]['lastName'] for c in h.finds()] == ['LONER'], [c[2] for c in h.finds()])

# ------------------------------------------------------------------------------------ 12 caps exhausted
print('-- 12 caps exhausted: nothing searched')
reset_ledgers()
(TMP / 'pacer_q.json').write_text(json.dumps({Q: {'total': 25.0, 'pages': 250, 'searches': 250}}))
d = work({'broward_leads.json': BR})
h = FakeHTTP()
rc, out = run(d, h)
check('quarter at $25 of $25: no login, no search, exit 0', rc == 0 and h.calls == [] and 'quarter cap' in out, out)
reset_ledgers()
(TMP / 'pacer_q.json').write_text(json.dumps({Q: {'total': 24.95}}))
h = FakeHTTP()
rc, out = run(work({'broward_leads.json': BR}), h, args=['--max-spend', '5'])
check('quarter with $0.05 left: a $0.10 page does not fit -> nothing searched', h.finds() == [])
reset_ledgers()
json.dump({PR.month(NOW): {'total': 50.0, 'by': {'records_liens': 50.0}}}, open(os.environ['DEALFLOW_PAID_LEDGER'], 'w'))
h = FakeHTTP()
rc, out = run(work({'broward_leads.json': BR}), h, args=['--max-spend', '1'])
check('#74 monthly cap spent ($50 of $50): no PACER search, exit 0', rc == 0 and h.finds() == [] and h.calls == [], out)
reset_ledgers()
os.environ['DEALFLOW_PAID_MONTHLY_CAP'] = 'nonsense'
h = FakeHTTP()
rc, out = run(work({'broward_leads.json': BR}), h, args=['--max-spend', '1'])
os.environ.pop('DEALFLOW_PAID_MONTHLY_CAP', None)
check('#74 cap setting invalid: no PACER search', h.finds() == [])
reset_ledgers()
h = FakeHTTP()
rc, out = run(work({'broward_leads.json': BR}), h, env(PACER_QUARTER_CAP='oops'))
check('PACER_QUARTER_CAP invalid: no search', h.finds() == [] and rc == 0)
reset_ledgers()
json.dump({PR.month(NOW): {'total': 49.95, 'by': {'x': 49.95}}}, open(os.environ['DEALFLOW_PAID_LEDGER'], 'w'))
h = FakeHTTP()
rc, out = run(work({'broward_leads.json': lf}), h, args=['--max-spend', '1'])
check('month with $0.05 left: nothing searched (the page would overrun it)', h.finds() == [])
reset_ledgers()
json.dump({PR.month(NOW): {'total': 49.80, 'by': {'x': 49.8}}}, open(os.environ['DEALFLOW_PAID_LEDGER'], 'w'))
h = FakeHTTP()
rc, out = run(work({'broward_leads.json': lf}), h, args=['--max-spend', '1'])
check('month with $0.20 left: exactly two pages, then stop; month never passes $50',
      len(h.finds()) == 2 and PR.status()['spent'] <= 50.0 + 1e-9, (len(h.finds()), PR.status()))
reset_ledgers()
led = TMP / 'pacer_q.json'
led.write_text('{broken')
h = FakeHTTP()
rc, out = run(work({'broward_leads.json': BR}), h)
check('unreadable quarter ledger: no search', h.finds() == [])

# ------------------------------------------------------------------------------------ 13 the gate
print('-- 13 the stay gate reads it')
reset_ledgers()
md_cache = {'2099-000401-CA-01': {'a': False, 'bd': '', 'sl': '', 'v': 5, 't': NOW},
            '2099-000402-CA-01': {'a': False, 'bd': '', 'sl': '', 'v': 5, 't': NOW}}
leads_br = [{'county': 'BROWARD', 'case': 'CACE-99-000801', 'owners': 'CLEARP QUINCY', 'auction': mdy(24)},
            {'county': 'BROWARD', 'case': 'CACE-99-000802', 'owners': 'STAYEDP ROSALIND', 'auction': mdy(25)},
            {'county': 'BROWARD', 'case': 'CACE-99-000803', 'owners': 'COMMONP JOHN', 'auction': mdy(26)}]
leads_pb = [{'county': 'PALM BEACH', 'case': '509999CA000804XXXAMB', 'owners': 'PBCLEAR MARTA', 'auction': mdy(27)}]
leads_final = [{'Case #': '2099-000401-CA-01', 'owners': 'OSCAR MDSTAYED', 'AuctionDate': mdy(28)},
               {'Case #': '2099-000402-CA-01', 'owners': 'PAULA MDCLEAR', 'AuctionDate': mdy(29)}]
g = work({'broward_leads.json': leads_br, 'palmbeach_leads.json': leads_pb, 'leads_final.json': leads_final,
          'sale_history_cache.json': md_cache})
http = FakeHTTP(by_name={
    ('STAYEDP', 'ROSALIND'): [row('STAYEDP', 'ROSALIND', no='1:26-bk-12001', filed=iso(-11))],
    ('COMMONP', 'JOHN'): page([row('COMMONP', 'JOHNNY', termed='2020-01-01')] * 54, total_pages=4),
    ('MDSTAYED', 'OSCAR'): [row('MDSTAYED', 'OSCAR', no='0:26-bk-12002', filed=iso(-4), court='flsbk')],
    ('CLEARP', 'QUINCY'): [row('CLEARP', 'QUINCY', no='1:19-bk-30001', filed=iso(-2400), termed=iso(-2000))],
})
rc, out = run(g, http, args=['--max-spend', '2'])
sh = str(g / 'sale_history_cache.json')
chk = lambda c: SG.check(c, sh)
r = chk('CACE-99-000801')
check('gate: Broward lead PACER cleared (closed 2019 case only) -> ALLOWED', r['ok'] is True and r['code'] == SG.CLEAR
      and r.get('src') == 'pacer_pcl', r)
r = chk('CACE-99-000802')
check('gate: Broward lead with an open flsbk case -> refused stay_active, case number in the reason',
      r['ok'] is False and r['code'] == SG.STAY_ACTIVE and '1:26-bk-12001' in r['why'] and r['bd'] == iso(-11), r)
r = chk('CACE-99-000803')
check('gate: common name (4 pages) -> refused stay_unverified', r['ok'] is False and r['code'] == SG.UNVERIFIED, r)
r = chk('509999CA000804XXXAMB')
check('gate: Palm Beach lead with no bankruptcy -> ALLOWED', r['ok'] is True, r)
r = chk('case no. cace - 99 - 000801')
check('gate: the same Broward number with a label and odd spacing reads the same entry', r['ok'] is True, r)
r = chk('2099-000401-CA-01')
check('gate: Miami-Dade docket says clear but PACER finds an open case -> refused stay_active',
      r['ok'] is False and r['code'] == SG.STAY_ACTIVE and 'docket does not show it yet' in r['why'], r)
r = chk('2099-000402-CA-01')
check('gate: Miami-Dade docket clear and PACER clear -> allowed (unchanged)', r['ok'] is True, r)
r = chk('CACE-99-000999')
check('gate: a Broward lead PACER has not searched -> stay_unverified', r['code'] == SG.UNVERIFIED, r)
r = chk('CASE')
check('gate: a number too short to key -> stay_case_unresolvable', r['code'] == SG.UNRESOLVABLE, r)
pc = json.loads((g / 'pacer_stay_cache.json').read_text())
pc['CACE-99-000801']['t'] = NOW - 15 * 86400
(g / 'pacer_stay_cache.json').write_text(json.dumps(pc))
r = chk('CACE-99-000801')
check('gate: a PACER clear older than 14 days -> stay_unverified (not clear)', r['code'] == SG.UNVERIFIED and 'days old' in r['why'], r)
os.environ['DEALFLOW_PACER_MAX_AGE_DAYS'] = '30'
check('gate: DEALFLOW_PACER_MAX_AGE_DAYS=30 lets it clear again', chk('CACE-99-000801')['ok'] is True)
os.environ['DEALFLOW_PACER_MAX_AGE_DAYS'] = 'soon'
check('gate: a junk max-age setting clears nothing', chk('509999CA000804XXXAMB')['ok'] is False)
os.environ.pop('DEALFLOW_PACER_MAX_AGE_DAYS', None)
pc['509999CA000804XXXAMB']['env'] = 'qa'
(g / 'pacer_stay_cache.json').write_text(json.dumps(pc))
check('gate: an entry marked env qa never clears', chk('509999CA000804XXXAMB')['code'] == SG.UNVERIFIED)
(g / 'pacer_stay_cache.json').write_text('{half a file')
r1, r2 = chk('CACE-99-000802'), chk('2099-000402-CA-01')
check('gate: unreadable PACER file -> Broward refused (stay_unverified); Miami-Dade docket verdict unaffected',
      r1['code'] == SG.UNVERIFIED and r2['ok'] is True, (r1, r2))
(g / 'pacer_stay_cache.json').unlink()
r1, r2 = chk('CACE-99-000802'), chk('2099-000402-CA-01')
check('gate: no PACER file at all -> Broward stay_case_unresolvable (exactly #72\'s behaviour), Miami-Dade unchanged',
      r1['code'] == SG.UNRESOLVABLE and r2['ok'] is True, (r1, r2))
qa_dir = work({'broward_leads.json': leads_br, 'sale_history_cache.json': md_cache})
run(qa_dir, FakeHTTP(), env(PACER_ENV='qa'))
check('gate: a QA run (all clear) allows nothing -- its file is not the one the gate reads',
      SG.check('CACE-99-000801', str(qa_dir / 'sale_history_cache.json'))['code'] == SG.UNRESOLVABLE)


# ---- live /send through the real send_server.py bridge (#72) with the PACER file this run wrote
def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data,
                                 headers={'Content-Type': 'application/json'} if data else {},
                                 method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {'err': str(e)}


print('-- 13b live /send (send_server.py + stay_gate.py, fake SMTP)')
reset_ledgers()
srv = work({'broward_leads.json': leads_br, 'palmbeach_leads.json': leads_pb, 'leads_final.json': leads_final,
            'sale_history_cache.json': md_cache})
run(srv, FakeHTTP(by_name=http.by_name), args=['--max-spend', '2'])
for fn in ('send_server.py', 'stay_gate.py', 'mail_guard.py'):
    shutil.copy(HERE / fn, srv / fn)
(srv / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
(srv / 'sender.json').write_text(json.dumps({'name': 'Test Sender'}), encoding='utf-8')
(srv / 'optouts.json').write_text(json.dumps({'_dealflow_notes': True, 'notes': {}}), encoding='utf-8')
port = free_port()
(srv / '_run_bridge.py').write_text(
    'import sys, smtplib\n'
    'class _FakeSMTP:\n'
    '    def __init__(self, *a, **k): pass\n'
    '    def __enter__(self): return self\n'
    '    def __exit__(self, *a): return False\n'
    '    def login(self, u, p): pass\n'
    '    def send_message(self, m, **k):\n'
    '        open("smtp_calls.txt", "a", encoding="utf-8").write(str(m["To"]) + "\\n")\n'
    '        return {}\n'
    'smtplib.SMTP_SSL = _FakeSMTP\n'
    'sys.argv = ["send_server.py", "--port", "%d", "--limit", "50"]\n'
    'exec(open("send_server.py", encoding="utf-8").read())\n' % port, encoding='utf-8')
proc = subprocess.Popen([sys.executable, str(srv / '_run_bridge.py')], cwd=str(srv),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    up = False
    for _ in range(60):
        if call(port, '/health')[0] == 200:
            up = True
            break
        time.sleep(0.25)
    check('bridge starts', up)
    if up:
        n = [0]

        def send(case):
            n[0] += 1
            return call(port, '/send', {'to': 'owner%d@example.com' % n[0], 'subj': 'About 1 Main St',
                                        'body': 'Hello, a short note about 1 Main St.',
                                        'meta': {'owner': 'Jane', 'addr': '1 Main St', 'wl': 'active', 'c': case}})

        def smtp():
            p = srv / 'smtp_calls.txt'
            return p.read_text(encoding='utf-8').split() if p.exists() else []
        st, j = send('CACE-99-000801')
        check('/send: PACER-clear Broward lead -> 200, reached SMTP', st == 200 and j.get('ok') is True and len(smtp()) == 1, (st, j))
        st, j = send('CACE-99-000802')
        check('/send: PACER-active Broward lead -> 451 blocked=stay_active, nothing sent',
              st == 451 and j.get('blocked') == 'stay_active' and len(smtp()) == 1, (st, j))
        st, j = send('2099-000401-CA-01')
        check('/send: Miami-Dade docket-clear but PACER-active -> 451 stay_active', st == 451 and j.get('blocked') == 'stay_active', (st, j))
        st, j = send('CACE-99-000803')
        check('/send: ambiguous name -> 451 stay_unverified', st == 451 and j.get('blocked') == 'stay_unverified', (st, j))
        st, h2 = call(port, '/health')
        pz = (h2.get('stay_data') or {}).get('pacer') or {}
        check('/health stay_data.pacer counts the PACER file', pz.get('ok') is True and pz.get('active') == 2, h2.get('stay_data'))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

print()
print('==== %d FAIL(S) ====' % len(FAILS) if FAILS else '==== all PACER stay checks passed ====')
for f_ in FAILS:
    print('   FAIL', f_)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
