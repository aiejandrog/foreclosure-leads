#!/usr/bin/env python
"""_ordailytest.py — the Clerk CDS Official Records daily file.

Synthetic fixtures only: fake names, fake CFNs, a fake HTTP session. No network,
no credentials, no real lead file. Proves the ingest is off unless asked, fails
soft, never calls a purchase URL, honors I/U/D, matches conservatively, and
stays report-only.

Run: python _ordailytest.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile

import or_daily_compare as cmp
import or_daily_file as daily

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = daily.dt.date(2026, 9, 26)
USER = 'clerkdev-user@example.com'
SECRET = 's3cret-PASSWORD-value'
FAIL = []


def check(name, cond, got=None):
    if not cond:
        FAIL.append(name)
        print('FAIL  %s  -> %r' % (name, got))
    else:
        print('ok    %s' % name)


def capture(argv, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = daily.main(argv, pause=0, sleeper=lambda _s: None, **kw)
    return rc, buf.getvalue()


def row(**kw):
    base = {name: '' for name in daily.FIELDS}
    base.update({
        'cfn_year': '2026', 'cfn_seq': '0000000001', 'group_id': '00001',
        'rec_date_raw': '09262026', 'rec_time': '093013',
        'book': '00000000000100', 'page': '00001', 'book_type': 'O ',
        'doc_type': 'MOR', 'doc_desc': 'MORTGAGE',
        'party_name': 'SAMPLETON JANE', 'party_code': 'D', 'cross_party': 'FAKE BANK',
        'folio': '000000000000000', 'case_number': '2026-000111-CA-01 FAKE COURT',
        'consideration_1': '0000000000000100.00',
        'key': '0000000000001', 'txn': 'I', 'party_seq': '00001', 'modified_raw': '09262026',
    })
    base.update(kw)
    return '^'.join(base[name] for name in daily.FIELDS)


CONTROL = '00000000092620260001'


def exp_text(*lines):
    return CONTROL + '\r\n' + '\r\n'.join(lines) + '\r\n'


def make_zip(stamp, text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr(stamp + '.exp', text.encode('utf-8'))
    return buf.getvalue()


class FakeClerk(object):
    def __init__(self, files, units=10, login_ok=True, html=None, redirect=None, drop_after=None):
        self.files = files
        self.units = units
        self.login_ok = login_ok
        self.html = html
        self.redirect = redirect
        self.drop_after = drop_after
        self.downloads = 0
        self.calls = []

    def page(self):
        if self.html is not None:
            return self.html
        rows = []
        for item in self.files:
            rows.append(
                '<tr><td>%s</td><td>ZIP</td><td>1 KB</td>'
                '<td><a href="/Developers/FTP/FTP/DownloadFile/%s">Download</a></td></tr>'
                % (item['name'], item['id']))
        return (
            '<h2>Records</h2>'
            'Files Available: %d | Size: 1 KB | Updated: 9/26/2026 '
            'Expiration Date: Monday, October 26, 2026 '
            'Days Left: 30 '
            '<table>%s</table>'
            '<a href="/Developers/FTP/ViewMore">View More</a>'
            '<h2>Civil</h2>'
            '<a href="/Developers/FTP/AddToCart/9">Add To Basket</a>'
            '<a href="/Developers/Home/ExtendPurchase">Extend</a>'
            '<a href="/Developers/FTP/DownloadDirectory/1">Download directory</a>'
            '<a href="/Developers/Basket">Basket</a>'
            '<a href="/Developers/Account/AddUnits">Add units</a>'
            % (len(self.files), ''.join(rows)))

    def __call__(self, method, url, data=None, timeout=45):
        self.calls.append((method, url))
        lowered = url.lower()
        for bad in ('addtocart', 'extendpurchase', 'downloaddirectory', 'basket',
                    'addunits', 'viewmore', '/buy', 'purchase'):
            if bad in lowered:
                raise AssertionError('blocked URL was requested: %s' % url)
        if url == daily.LOGIN_URL and method == 'GET':
            return daily.HttpResp(200, b'<form action="/Developers/Account/Login"></form>', {})
        if url == daily.LOGIN_URL and method == 'POST':
            if self.redirect:
                return daily.HttpResp(302, b'', {'location': self.redirect})
            if not self.login_ok:
                return daily.HttpResp(200, b'<div class="validation-summary">Invalid User</div>', {})
            return daily.HttpResp(200, b'<a href="/Developers/Account/SignOut">Sign out</a>', {})
        if url == daily.ACCOUNT_URL:
            if self.drop_after is not None and self.downloads >= self.drop_after and self.units == 10:
                self.units = 9
            body = ('<p>Balance</p><p>%d units</p>' % self.units).encode()
            return daily.HttpResp(200, body, {})
        if url == daily.FTP_URL:
            return daily.HttpResp(200, self.page().encode('utf-8'), {})
        if url.startswith(daily.DOWNLOAD_URL):
            self.downloads += 1
            fid = url.rsplit('/', 1)[-1]
            for item in self.files:
                if item['id'] == fid:
                    return daily.HttpResp(200, item['blob'], {'content-type': 'application/zip'})
            return daily.HttpResp(404, b'no', {})
        raise AssertionError('unexpected URL %s' % url)


def env_on():
    return {daily.ENV_ENABLE: '1', daily.ENV_USER: USER, daily.ENV_PASS: SECRET}


def leads(folder):
    json.dump([
        {'Case #': '2026-000111-CA-01', 'county': 'MIAMI-DADE',
         'owner_clean': 'JANE SAMPLETON', 'folio': '012345678901234'},
        {'Case #': 'CACE-26-000111', 'county': 'BROWARD',
         'owner_clean': 'JANE SAMPLETON', 'folio': '999'},
    ], open(os.path.join(folder, 'leads_final.json'), 'w', encoding='utf-8'))


def no_secret(text, label):
    check(label + ' has no credential', SECRET not in text and USER not in text, text[:180])


# ---- parser, categories, expiry --------------------------------------------------------------
records, bad, control = daily.parse_exp(exp_text(row(), '1^2^3', row(txn='X', key='0000000000098')))
check('control line skipped', control == 1, control)
check('one good record and the bad txn still splits', len(records) == 2, len(records))
check('short line counted bad', bad == 1, bad)
check('42 fields', len(records[0]) == 42 and records[0]['book_type'] == 'O ', records[0]['book_type'])
norm = daily.normalize_record(records[0], 'dly_records_09262026')
check('case number keeps the first YYYY-NNNNNN-XX-NN', norm['case_norm'] == '2026-000111-CA-01', norm['case_norm'])
check('all-zero folio is absent', norm['folio_norm'] == '', norm['folio_norm'])
check('money parses and zero is empty',
      daily.money_amount('0000000000000100.00') == 100.0 and daily.money_amount('0000000000000000.00') is None)
check('bad txn is not a row', daily.normalize_record(records[1], 'x') is None)

check('MOR is a mortgage and REL of a mortgage is a release',
      daily.category_of('MOR', 'MORTGAGE') == 'mortgage'
      and daily.category_of('REL', 'RELEASE OF MORTGAGE') == 'release')
check('AMO and DCP are not reclassified by a loose description',
      daily.category_of('AMO', 'ASSIGNMENT OF MORTGAGE') == ''
      and daily.category_of('DCP', 'COURT PAPER LIEN') == '')
check('SJU is a judgment only when the description says so',
      daily.category_of('SJU', 'SJU') == '' and daily.category_of('SJU', 'SUMMARY JUDGMENT') == 'judgment')
check('LIS LIE JUD',
      daily.category_of('LIS', 'LIS PENDENS') == 'lis_pendens'
      and daily.category_of('LIE', 'LIEN') == 'lien'
      and daily.category_of('JUD', 'JUDGMENT') == 'judgment')

near = daily.dt.date(2026, 10, 21)
far = daily.dt.date(2026, 10, 20)
exp = daily.dt.date(2026, 10, 26)
check('warns 5 days out, not 6', daily.expiry_is_near(exp, None, near) and not daily.expiry_is_near(exp, None, far))
check('page day-count warns on its own', daily.expiry_is_near(exp, 5, far) and not daily.expiry_is_near(exp, 6, far))
check('fallback date is the purchased month', daily.fallback_expiry({}).isoformat() == '2026-10-26')
check('configured date replaces the fallback',
      daily.fallback_expiry({daily.ENV_EXPIRES: '2026-11-01'}).isoformat() == '2026-11-01')

page = FakeClerk([]).page()
parsed = daily.parse_ftp_page(page)
check('expiry parsed from the Records header', parsed['expires'] == exp and parsed['days_left'] == 30, parsed)
check('not lapsed while days remain', not daily.access_lapsed(parsed, TODAY))
lapsed = daily.parse_ftp_page(
    '<h2>Records</h2> Expiration Date: Monday, September 21, 2026 Days Left: 0 '
    'your subscription has expired <tr><td>dly_records_09212026</td></tr>')
check('a zero day-count in the Records header is lapsed', daily.access_lapsed(lapsed, TODAY))
check('Not Verified is not a lapse',
      not daily.access_lapsed(daily.parse_ftp_page(page + ' Request history Not Verified'), TODAY))

ftp = ('<h2>Records</h2> Files Available: 2 Expiration Date: Monday, October 26, 2026 Days Left: 30'
       '<table>'
       '<tr><td>dly_records_09262026</td><td>ZIP</td>'
       '<td><a href="/Developers/FTP/FTP/DownloadFile/7">Download</a></td></tr>'
       '<tr><td>dly_records_09252026</td><td>ZIP</td>'
       '<td><a href="/Developers/FTP/FTP/DownloadFile/6">Download</a></td></tr>'
       '</table><a href="/Developers/FTP/ViewMore">View More</a>'
       '<h2>Civil</h2><tr><td>civil.zip</td><td><a href="/Developers/FTP/AddToCart/3">Add</a></tr>')
listed = daily.parse_ftp_page(ftp)
check('both listed daily files, oldest first',
      [item['name'] for item in listed['files']] == ['dly_records_09252026', 'dly_records_09262026'],
      listed['files'])
check('ids are the digits only', [item['id'] for item in listed['files']] == ['6', '7'])
check('units balance', daily.parse_units('<div>Balance</div> 10 units') == 10)
check('units balance with markup between number and word (live page shape)',
      daily.parse_units('<div class="panel-heading">Balance</div> <div class="panel-body"> <h2> '
                        '<p class="text-center">10 <small class="upper-sm">units</small></p> </h2></div>') == 10)
check('units balance missing is None', daily.parse_units('<div>Balance</div> <p>n/a</p>') is None)
live_shape = daily.parse_ftp_page(
    '<li class="list-group-item"><label class="label-control">Expiration Date:</label>'
    '<span class="badge ocs-badge-text badge-success">Monday, October 26, 2026</span></li>'
    '<li class="list-group-item"><label class="label-control">Days Left:</label>'
    '<span class="badge ocs-badge-text badge-success">29</span></li>'
    '<table><tr><td>dly_records_09252026</td><td>ZIP</td>'
    '<td><a href="/Developers/FTP/FTP/DownloadFile/123" class="btn"></a></td></tr></table>')
check('expiry and days left read through span markup (live page shape)',
      live_shape['expires'] == daily.dt.date(2026, 10, 26) and live_shape['days_left'] == 29
      and [f['id'] for f in live_shape['files']] == ['123'], live_shape)

# ---- URL gate ---------------------------------------------------------------------------------
for url in (
        'https://www2.miamidadeclerk.gov/Developers/FTP/AddToCart/1',
        'https://www2.miamidadeclerk.gov/Developers/Home/ExtendPurchase',
        'https://www2.miamidadeclerk.gov/Developers/FTP/DownloadDirectory/1',
        'https://www2.miamidadeclerk.gov/Developers/Basket',
        'https://www2.miamidadeclerk.gov/Developers/Account/AddUnits',
        'https://www2.miamidadeclerk.gov/Developers/FTP/ViewMore',
        'https://evil.example/Developers/FTP/FTP/DownloadFile/1',
        'https://www2.miamidadeclerk.gov/Developers/FTP/FTP/DownloadFile/12?x=1',
):
    try:
        daily.assert_allowed(url)
        check('refuses %s' % url, False)
    except daily.Denied:
        check('refuses %s' % url.rsplit('/', 1)[-1][:40], True)
check('download URL rebuilt from the id is allowed',
      daily.download_url('42').endswith('/DownloadFile/42'))
try:
    daily.download_url('../AddToCart/1')
    check('a non-numeric id is not a download', False)
except daily.Denied:
    check('a non-numeric id is not a download', True)

check('same person, either order', daily.name_matches('JANE SAMPLETON', 'SAMPLETON JANE'))
check('a different given name does not match', not daily.name_matches('JANE SAMPLETON', 'SAMPLETON JOHN'))
check('a missing middle token does not match',
      not daily.name_matches('JANE MARIE SAMPLETON', 'SAMPLETON JANE'))
check('an owner initial the party lacks does not match',
      not daily.name_matches('JANE M SAMPLETON', 'SAMPLETON JANE'))
check('one thin token is not an identity', daily.name_identity('SAMPLETON') is None)

# ---- sqlite I/U/D -----------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as folder:
    conn = daily.open_db(os.path.join(folder, 'or_daily.sqlite'))
    first = daily.normalize_record(daily.parse_exp(row())[0][0], 'f')
    daily.write_row(conn, first, '2026-09-26')
    daily.write_row(conn, first, '2026-09-26')
    check('insert twice is one row',
          conn.execute('SELECT COUNT(*) FROM rows').fetchone()[0] == 1)
    updated = dict(first)
    updated['txn'] = 'U'
    updated['party_name'] = 'SAMPLETON JANET'
    updated['deleted'] = 0
    daily.write_row(conn, updated, '2026-09-27')
    got = conn.execute('SELECT party_name, deleted FROM rows').fetchone()
    check('update replaces the party and does not duplicate',
          got['party_name'] == 'SAMPLETON JANET' and got['deleted'] == 0 and
          conn.execute('SELECT COUNT(*) FROM rows').fetchone()[0] == 1, dict(got))
    deleted = dict(updated)
    deleted['txn'] = 'D'
    deleted['deleted'] = 1
    daily.write_row(conn, deleted, '2026-09-28')
    check('delete drops the row from the live set',
          len(daily.live_party_rows(conn)) == 0)
    daily.write_row(conn, first, '2026-09-29')
    check('a later insert revives the key', len(daily.live_party_rows(conn)) == 1)
    text = exp_text(row(), '1^2^3')
    once = daily.apply_text(conn, 'dly_records_09262026', text, '2026-09-26')
    twice = daily.apply_text(conn, 'dly_records_09262026', text, '2026-09-26')
    check('applying a file is idempotent', once['applied'] and not twice['applied'] and once['bad_rows'] == 1,
          (once, twice))
    conn.close()

# ---- matching ---------------------------------------------------------------------------------
cases = {
    '2026-000111-CA-01': {'case': '2026-000111-CA-01', 'folios': {'12345678901234'},
                          'owners': {'JANE SAMPLETON'}},
    '2026-000222-CA-01': {'case': '2026-000222-CA-01', 'folios': set(),
                          'owners': {'JOHN EXAMPLE'}},
    '2026-000333-CA-01': {'case': '2026-000333-CA-01', 'folios': set(),
                          'owners': {'JANE SAMPLETON'}},
}
docs = [
    {'doc_id': 'm', 'cfn': daily.cfn_join('2026', '1'), 'book': '100', 'page': '1',
     'bp': ('100', '1'), 'orig_bp': None, 'orig_cfns': [], 'doc_type': 'MOR', 'doc_desc': 'MORTGAGE',
     'category': 'mortgage', 'rec_date': '2026-09-26', 'cases': ['2026-000111-CA-01'],
     'folios': [], 'parties': [{'name': 'SOMEONE ELSE', 'code': 'D', 'cross': 'FAKE BANK'}],
     'amount': 100.0, 'source_file': 'dly_records_09262026', 'keys': ['1']},
    {'doc_id': 'lien', 'cfn': daily.cfn_join('2026', '2'), 'book': '100', 'page': '2',
     'bp': ('100', '2'), 'orig_bp': None, 'orig_cfns': [], 'doc_type': 'LIE', 'doc_desc': 'LIEN',
     'category': 'lien', 'rec_date': '2026-09-26', 'cases': [], 'folios': [],
     'parties': [{'name': 'SAMPLETON JANE', 'code': 'D', 'cross': 'FAKE COUNTY'}],
     'amount': None, 'source_file': 'dly_records_09262026', 'keys': ['2']},
    {'doc_id': 'rel', 'cfn': daily.cfn_join('2026', '3'), 'book': '100', 'page': '3',
     'bp': ('100', '3'), 'orig_bp': None, 'orig_cfns': [daily.cfn_join('2026', '1')],
     'doc_type': 'REL', 'doc_desc': 'RELEASE', 'category': 'release', 'rec_date': '2026-09-26',
     'cases': [], 'folios': [], 'parties': [{'name': 'FAKE BANK', 'code': 'D', 'cross': ''}],
     'amount': None, 'source_file': 'dly_records_09262026', 'keys': ['3']},
    {'doc_id': 'mtg-name', 'cfn': daily.cfn_join('2026', '4'), 'book': '100', 'page': '4',
     'bp': ('100', '4'), 'orig_bp': None, 'orig_cfns': [], 'doc_type': 'MOR', 'doc_desc': 'MORTGAGE',
     'category': 'mortgage', 'rec_date': '2026-09-26', 'cases': [], 'folios': [],
     'parties': [{'name': 'SAMPLETON JANE', 'code': 'D', 'cross': 'FAKE BANK'}],
     'amount': 50.0, 'source_file': 'dly_records_09262026', 'keys': ['4']},
]
unique = {k: v for k, v in cases.items() if k != '2026-000333-CA-01'}
found = daily.match_documents(docs, unique, {}, {})
by = {}
for match in found:
    by.setdefault(match['doc_id'], match)
check('case number is a high-confidence match',
      by['m']['case'] == '2026-000111-CA-01' and by['m']['confidence'] == 'high' and 'case' in by['m']['via'],
      by.get('m'))
check('a release follows the original CFN',
      by['rel']['case'] == '2026-000111-CA-01' and 'orig_cfn' in by['rel']['via'], by.get('rel'))
check('a folio-less lien matches one owner, at low confidence',
      by['lien']['confidence'] == 'low' and by['lien']['via'] == ['party'], by.get('lien'))
check('a mortgage with no folio and no case number is not a party match',
      'mtg-name' not in by, list(by))
ambiguous = daily.match_documents(docs, cases, {}, {})
check('two cases with the same name are not a party match',
      all(m['doc_id'] != 'lien' for m in ambiguous), [m['doc_id'] for m in ambiguous])
folio_doc = dict(docs[1])
folio_doc.update(doc_id='by-folio', folios=['12345678901234'], cases=[],
                 parties=[{'name': 'NOBODY ELSE', 'code': 'D', 'cross': ''}])
folio_hit = daily.match_documents([folio_doc], unique, {}, {})
check('folio match is high and does not need the name',
      folio_hit and folio_hit[0]['confidence'] == 'high' and 'folio' in folio_hit[0]['via'], folio_hit)

# ---- full ingest against the fake site --------------------------------------------------------
with tempfile.TemporaryDirectory() as folder:
    repo = os.path.join(folder, 'repo')
    data = os.path.join(folder, 'data')
    os.makedirs(repo)
    leads(repo)
    day = exp_text(
        row(),
        row(doc_type='LIE', doc_desc='LIEN', case_number='', folio='000000000000000',
            key='0000000000002', cfn_seq='0000000002', page='00002',
            party_name='SAMPLETON JANE', cross_party='FAKE COUNTY'),
        row(doc_type='LIE', doc_desc='LIEN', case_number='', folio='000000000000000',
            key='0000000000003', cfn_seq='0000000003', page='00003',
            party_name='SAMPLETON JOHN', cross_party='FAKE COUNTY'),
        row(doc_type='JUD', doc_desc='JUDGMENT', case_number='', folio='012345678901234',
            key='0000000000004', cfn_seq='0000000004', page='00004'),
        row(doc_type='REL', doc_desc='RELEASE', case_number='', folio='000000000000000',
            key='0000000000005', cfn_seq='0000000005', page='00005',
            orig_cfn_year='2026', orig_cfn_seq='0000000001', party_name='FAKE BANK'),
    )
    other = exp_text(row(doc_type='DCP', doc_desc='COURT PAPER', case_number='',
                         folio='000000000000000', key='0000000000099', cfn_seq='0000000099',
                         rec_date_raw='09252026'))
    fake = FakeClerk([
        {'name': 'dly_records_09262026', 'id': '102', 'blob': make_zip('09262026', day)},
        {'name': 'dly_records_09252026', 'id': '101', 'blob': make_zip('09252026', other)},
    ])
    rc, out = capture(['--enable'], env=env_on(), transport=fake, data_dir=data, repo_dir=repo, today=TODAY)
    check('enabled run exits 0', rc == 0, rc)
    no_secret(out, 'log')
    urls = [url for _m, url in fake.calls]
    check('both listed files downloaded',
          urls.count(daily.DOWNLOAD_URL + '101') == 1 and urls.count(daily.DOWNLOAD_URL + '102') == 1, urls)
    status = json.load(open(os.path.join(data, 'status.json'), encoding='utf-8'))
    check('units unchanged', status['units_before'] == 10 and status['units_after'] == 10 and not status['units_dropped'],
          status)
    check('expiry came from the page', status['expires'] == '2026-10-26' and status['expires_source'] == 'page', status)
    no_secret(json.dumps(status), 'status')
    evidence = json.load(open(os.path.join(data, 'evidence.json'), encoding='utf-8'))
    check('evidence is report-only',
          evidence['report_only'] is True and evidence['affects_send'] is False
          and evidence['affects_callable'] is False and evidence['affects_hold'] is False)
    vias = {(m['category'], tuple(m['via']), m['confidence']) for m in evidence['matches']}
    check('case, folio, original CFN, and one party lien matched',
          any(m['category'] == 'mortgage' and 'case' in m['via'] for m in evidence['matches']),
          vias)
    check('the jane lien is the low party match',
          any(m['category'] == 'lien' and m['via'] == ['party'] and m['confidence'] == 'low'
              for m in evidence['matches']), vias)
    check('john is not a match',
          not any('JOHN' in json.dumps(m.get('parties')) for m in evidence['matches']),
          [m.get('parties') for m in evidence['matches']])
    check('judgment matched on the folio',
          any(m['category'] == 'judgment' and 'folio' in m['via'] and m['confidence'] == 'high'
              for m in evidence['matches']))
    check('release matched on the original CFN',
          any(m['category'] == 'release' and 'orig_cfn' in m['via'] for m in evidence['matches']))
    check('court paper was loaded and not surfaced',
          evidence['counts']['mortgage'] >= 1 and 'DCP' not in {m['doc_type'] for m in evidence['matches']})
    no_secret(json.dumps(evidence), 'evidence')
    second = FakeClerk(fake.files)
    rc2, out2 = capture(['--enable'], env=env_on(), transport=second, data_dir=data, repo_dir=repo, today=TODAY)
    check('second run downloads nothing new',
          rc2 == 0 and not any(url.startswith(daily.DOWNLOAD_URL) for _m, url in second.calls),
          [url for _m, url in second.calls])
    no_secret(out2, 'second log')

    drop_dir = os.path.join(folder, 'drop')
    drop = FakeClerk(fake.files, drop_after=1)
    rc3, out3 = capture(['--enable'], env=env_on(), transport=drop, data_dir=drop_dir, repo_dir=repo, today=TODAY)
    dropped = json.load(open(os.path.join(drop_dir, 'status.json'), encoding='utf-8'))
    check('a units drop aborts and stays non-fatal',
          rc3 == 0 and dropped['units_dropped'] and dropped['gap'] == 'units dropped'
          and dropped['downloaded'] == ['dly_records_09252026'],
          (rc3, dropped.get('gap'), dropped.get('downloaded'), dropped.get('units_before'), dropped.get('units_after')))
    check('the second file was not requested after the drop',
          ('GET', daily.DOWNLOAD_URL + '102') not in drop.calls, drop.calls)
    no_secret(out3, 'drop log')

# ---- fail soft --------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as folder:
    data = os.path.join(folder, 'data')
    repo = os.path.join(folder, 'repo')
    os.makedirs(repo)

    def quiet(**kw):
        return capture(['--enable'], env=env_on(), data_dir=data, repo_dir=repo, today=TODAY, **kw)

    bad_login = FakeClerk([], login_ok=False)
    rc, out = quiet(transport=bad_login)
    st = json.load(open(os.path.join(data, 'status.json'), encoding='utf-8'))
    check('bad login is a gap and exit 0', rc == 0 and st['gap'] == 'login failed', (rc, st['gap'], out))
    check('bad login downloads nothing', not any(u.startswith(daily.DOWNLOAD_URL) for _m, u in bad_login.calls))
    no_secret(out, 'login-fail log')

    class Down(object):
        def __init__(self):
            self.calls = []

        def __call__(self, method, url, data=None, timeout=45):
            self.calls.append(url)
            raise ConnectionError('timed out')

    down = Down()
    rc, out = quiet(transport=down)
    st = json.load(open(os.path.join(data, 'status.json'), encoding='utf-8'))
    check('a down site is a gap and exit 0', rc == 0 and st['gap'] == 'clerk site unreachable', (rc, st['gap']))
    no_secret(out, 'down log')

    lapsed_html = ('<h2>Records</h2> Expiration Date: Monday, September 21, 2026 '
                   'Days Left: 0 your subscription has expired')
    lapsed = FakeClerk([], html=lapsed_html)
    rc, out = quiet(transport=lapsed)
    st = json.load(open(os.path.join(data, 'status.json'), encoding='utf-8'))
    check('lapsed access downloads nothing and exits 0',
          rc == 0 and st['lapsed'] and st['gap'] == 'access lapsed'
          and not any(u.startswith(daily.DOWNLOAD_URL) for _m, u in lapsed.calls),
          (rc, st.get('gap'), st.get('lapsed')))

    redir = FakeClerk([], redirect='https://www2.miamidadeclerk.gov/Developers/Home/ExtendPurchase')
    rc, out = quiet(transport=redir)
    st = json.load(open(os.path.join(data, 'status.json'), encoding='utf-8'))
    check('a redirect at a purchase URL is refused before it is sent',
          rc == 0 and st['gap'] == 'refused a blocked URL'
          and not any('ExtendPurchase' in u for _m, u in redir.calls),
          (st.get('gap'), redir.calls))

    broken = FakeClerk([], html='<div>dly_records_09262026 is here but not in a row</div>'
                       'Expiration Date: Monday, October 26, 2026 Days Left: 30')
    rc, out = quiet(transport=broken)
    st = json.load(open(os.path.join(data, 'status.json'), encoding='utf-8'))
    check('an unreadable file list is a gap, not a silent empty success',
          rc == 0 and st['gap'] == 'daily file list could not be read', st.get('gap'))

    rc, out = capture(['--enable'], env={daily.ENV_ENABLE: '1'}, data_dir=os.path.join(folder, 'nocred'),
                      repo_dir=repo, today=TODAY, transport=Down())
    st = json.load(open(os.path.join(folder, 'nocred', 'status.json'), encoding='utf-8'))
    check('missing credentials do not call the site',
          rc == 0 and st['gap'] == 'credentials not set', (rc, st['gap'], out))

    rc, out = capture([], env={}, today=TODAY, transport=Down())
    check('off when the env is unset: exit 0 and no network', rc == 0 and 'off' in out, (rc, out))

    rc, out = capture(['--plan'], env={daily.ENV_USER: USER, daily.ENV_PASS: SECRET}, today=TODAY)
    check('plan says whether credentials exist without printing them',
          rc == 0 and 'credentials set: yes' in out and SECRET not in out and USER not in out, out)

    rc, out = capture(['--enable'], env=env_on(), data_dir=HERE, repo_dir=repo, today=TODAY, transport=Down())
    check('a data dir inside the repo is refused', rc == 0 and 'inside the repo' in out, out)
    rc, out = capture(['--enable'], env=env_on(), data_dir=os.path.join(folder, 'OneDrive', 'x'),
                      repo_dir=repo, today=TODAY, transport=Down())
    check('a data dir inside OneDrive is refused', rc == 0 and 'OneDrive' in out, out)

# ---- health -----------------------------------------------------------------------------------
check('health is silent when the stage is off and has no status',
      daily.health_checks(today=far, env={}, path=os.path.join(tempfile.gettempdir(), 'missing-or-daily-status.json')) == [])
warn = daily.health_checks(status={'expires': '2026-10-26', 'ok': True, 'matched': 3, 'gap': ''}, today=near, env={})
check('health warns inside five days',
      any(level == 'WARN' and 'expires 2026-10-26' in detail and '5 days' in detail
          for level, _name, detail in warn), warn)
ok = daily.health_checks(status={'expires': '2026-10-26', 'ok': True, 'matched': 3, 'gap': '',
                                 'units_dropped': False}, today=TODAY, env={})
check('health passes when the subscription is not close',
      ok and ok[0][0] == 'PASS', ok)
dropped = daily.health_checks(status={'expires': '2026-10-26', 'gap': 'units dropped', 'units_dropped': True,
                                      'units_before': 10, 'units_after': 9, 'lapsed': False}, today=TODAY, env={})
check('health warns when units dropped',
      any('balance dropped 10 -> 9' in detail for _l, _n, detail in dropped), dropped)
hc = open(os.path.join(HERE, 'healthcheck.py'), encoding='utf-8').read()
check('healthcheck.py asks this module and does not treat the warning as a compliance fail',
      'or_daily_file' in hc and 'official records' not in hc.split('_CRITICAL_FAIL')[1][:800])

# ---- day comparison ---------------------------------------------------------------------------
daily_matches = [
    {'case': '2026-000111-CA-01', 'category': 'mortgage', 'bp': '100/1', 'rec_date': '2026-09-26'},
    {'case': '2026-000111-CA-01', 'category': 'lien', 'bp': '100/2', 'rec_date': '2026-09-26'},
    {'case': '2026-000111-CA-01', 'category': 'judgment', 'bp': '100/4', 'rec_date': '2026-09-26'},
]
scraper = {
    '2026-000111-CA-01': [
        {'category': 'mortgage', 'bp': ('100', '1'), 'rec_date': '2026-09-26'},
        {'category': 'mortgage', 'bp': ('40', '2'), 'rec_date': '2020-01-02'},
        {'category': 'lien', 'bp': ('100', '9'), 'rec_date': '2026-09-26'},
        {'category': 'judgment', 'bp': ('100', '4'), 'rec_date': '2026-09-26'},
    ],
}
report = cmp.build_report('2026-09-26', daily_matches, scraper)
check('mortgage overlap and the old chain mortgage is not a same-day miss',
      report['categories']['mortgage']['overlap'] == 1
      and report['categories']['mortgage']['daily_only'] == []
      and report['categories']['mortgage']['scraper_only'] == []
      and report['categories']['mortgage']['scraper_chain'] == 2, report['categories']['mortgage'])
check('lien each side missed one',
      report['categories']['lien']['daily_only'] == [{'case': '2026-000111-CA-01', 'bp': '100/2'}]
      and report['categories']['lien']['scraper_only'] == [{'case': '2026-000111-CA-01', 'bp': '100/9'}]
      and report['categories']['lien']['not_in_chain'] == [{'case': '2026-000111-CA-01', 'bp': '100/2'}],
      report['categories']['lien'])
check('judgment overlap', report['categories']['judgment']['overlap'] == 1, report['categories']['judgment'])
text = cmp.render(report)
check('the comparison report says it changes nothing', 'does not change send, callable, or hold' in text, text[:240])

with tempfile.TemporaryDirectory() as folder:
    repo = os.path.join(folder, 'repo')
    os.makedirs(repo)
    leads(repo)
    liens = {
        '2026-000111-CA-01': {
            'liens': [{'d': '9/26/2026', 'bp': '100/1', 'st': 'OPEN', 'amt': 100},
                      {'d': '1/2/2020', 'bp': '40/2', 'st': 'OPEN', 'amt': 80}],
            'other': [{'d': '9/26/2026', 'doc': 'LIEN', 'kind': 'code', 'bp': '100/9', 'st': 'OPEN'},
                      {'d': '9/26/2026', 'doc': 'JUDGMENT', 'kind': 'judgment', 'bp': '100/4', 'st': 'OPEN'}],
        }
    }
    json.dump(liens, open(os.path.join(repo, 'records_liens.json'), 'w', encoding='utf-8'))
    db = os.path.join(folder, 'or_daily.sqlite')
    conn = daily.open_db(db)
    body = exp_text(
        row(),
        row(doc_type='LIE', doc_desc='LIEN', case_number='', folio='000000000000000',
            key='0000000000002', cfn_seq='0000000002', page='00002', party_name='SAMPLETON JANE'),
        row(doc_type='JUD', doc_desc='JUDGMENT', case_number='', folio='012345678901234',
            key='0000000000004', cfn_seq='0000000004', page='00004'),
    )
    daily.apply_text(conn, 'dly_records_09262026', body, '2026-09-26')
    conn.close()
    out = os.path.join(folder, 'compare-2026-09-26.md')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmp.main(['--date', '2026-09-26'], repo_dir=repo, db_path=db,
                      liens_path=os.path.join(repo, 'records_liens.json'), out_path=out)
    written = open(out, encoding='utf-8').read()
    check('compare writes the short report outside the repo and exits 0',
          rc == 0 and 'Overlap: 1' in written and SECRET not in written, (rc, written[:400]))
    tracked = daily.load_tracked_cases(repo)
    check('tracked-case loader ignores the Broward row',
          'CACE-26-000111' not in tracked and '2026-000111-CA-01' in tracked, list(tracked))

# ---- wiring and the public repo --------------------------------------------------------------
bat = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='utf-8').read()
check('the refresh calls the ingest before records_liens',
      bat.index('python -u or_daily_file.py') < bat.index('python -u records_liens.py'))
check('the stage is described as off unless DEALFLOW_OR_DAILY=1', 'DEALFLOW_OR_DAILY=1' in bat)
check('the lock wrapper was not restructured around this stage',
      'or_daily' not in open(os.path.join(HERE, 'run-locked.bat'), encoding='utf-8').read())
for name in ('cadence.py', 'stay_gate.py', 'send_server.py', 'equity_state.py',
             'diligence_gate.py', 'mail_guard.py', 'outreach_email.py', 'foreclosure_leads.py'):
    body = open(os.path.join(HERE, name), encoding='utf-8').read()
    check('%s does not read the daily file' % name, 'or_daily_file' not in body and 'or_daily.sqlite' not in body)

for rel in ('or_daily/or_daily.sqlite', 'dly_records_09252026.zip', 'or_daily.sqlite',
            'or_daily/evidence.json'):
    rc = subprocess.run(['git', 'check-ignore', '-q', rel], cwd=HERE).returncode
    check('gitignored %s' % rel, rc == 0, rc)
rc = subprocess.run(['git', 'check-ignore', '-q', '_ordailytest.py'], cwd=HERE).returncode
check('the test itself is not gitignored', rc != 0, rc)

print('\n%d failed' % len(FAIL))
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
