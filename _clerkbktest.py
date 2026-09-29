"""_clerkbktest — clerk-docket bankruptcy check, stubbed HTTP only.

Run:  python _clerkbktest.py

No network and no API key. Docket lines are the public entry labels (suggestion of
bankruptcy, notice of filing, notice of stay, relief from stay). No party names.
"""
import json
import os
import pathlib
import sys
import tempfile
import time
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = pathlib.Path(tempfile.mkdtemp(prefix='clerkbk_'))
KEY = 'clerk-test-key-not-real'
NOW = 1_800_000_000.0
CASE = 'CACE-99-000123'
API = 'CACE99000123'
PALM = '509999CA000123XXXAMB'
MIAMI = '2099-000123-CA-01'

for _k in ('DEALFLOW_CLERK_BK', 'BROWARD_CLERK_API_KEY', 'DEALFLOW_CLERK_BK_MAX_AGE_DAYS',
           'CLERK_BK_MAX_RUNTIME_S', 'CLERK_BK_MAX_CASES', 'CLERK_BK_MIN_INTERVAL',
           'DEALFLOW_BK_ALLOW_CL_CLEAR', 'DEALFLOW_DIR', 'DEALFLOW_CLERK_BK_CACHE',
           'DEALFLOW_CLERK_BK_STATUS', 'DEALFLOW_BK_CACHE'):
    os.environ.pop(_k, None)
os.environ['DEALFLOW_DIR'] = str(TMP / 'boot')
os.environ['CLERK_BK_MIN_INTERVAL'] = '0'

import bk_lookup as BL
import clerk_bk as CK
import pipeline_alerts as PA
import stay_gate as SG

FAILS = []


def check(name, cond, detail=''):
    text = str(detail).replace(KEY, '[redacted]')
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + text[:400]) if text and not cond else ''))
    if not cond:
        FAILS.append(name)


def isolate(name):
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    os.environ['DEALFLOW_DIR'] = str(d)
    os.environ['DEALFLOW_CLERK_BK_CACHE'] = str(d / 'clerk_bk_cache.json')
    os.environ['DEALFLOW_CLERK_BK_STATUS'] = str(d / 'clerk_bk_status.json')
    os.environ['DEALFLOW_BK_CACHE'] = str(d / 'bk_lead_cache.json')
    os.environ.pop('DEALFLOW_CLERK_BK', None)
    os.environ.pop('BROWARD_CLERK_API_KEY', None)
    os.environ.pop('DEALFLOW_BK_ALLOW_CL_CLEAR', None)
    os.environ['CLERK_BK_MIN_INTERVAL'] = '0'
    CK._MEMO = None
    CK._COUNTY_MEMO = None
    CK._LEADS_HERE = None
    BL._HOLD_MEMO = None
    return d


def clock():
    return CK.Clock(now=NOW, sleeper=lambda _s: None)


def rows(*pairs):
    return [{'date': d, 'text': t} for d, t in pairs]


class Stub:
    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        path = urllib.parse.urlparse(url).path
        spec = self.routes.get(path)
        if spec is None:
            return 404, b'{}'
        if callable(spec):
            spec = spec(url)
        status, obj = spec
        if isinstance(obj, bytes):
            return status, obj
        if isinstance(obj, str):
            return status, obj.encode()
        return status, json.dumps(obj).encode()


def case_body(status='Pending', code='Open', number=API):
    return {'Case_Number': number, 'Disposition_Status': status, 'Disposition_Code': code,
            'Caption': 'EXAMPLE PLAINTIFF V EXAMPLE DEFENDANT'}


def events_body(items, number=API, total=None):
    body = {'Case_Number': number, 'EventList': [
        {'EventDate': d, 'Description': t, 'AdditionalText': note,
         'EventDocumentList': [{'Name': doc}] if doc else []}
        for d, t, note, doc in items
    ]}
    if total is not None:
        body['TotalCount'] = total
    return body


def routes_for(items, status='Pending', number=API):
    return {
        '/api/case/%s/case.json' % number: (200, case_body(status, number=number)),
        '/api/case/%s/events_and_documents.json' % number: (200, events_body(items, number=number)),
    }


def saved(d):
    p = d / 'clerk_bk_cache.json'
    return p.read_text(encoding='utf-8') if p.exists() else ''


def cl_clear(case):
    BL._dump(BL.cache_path(), {
        case: {'verdict': 'clear', 'why': 'no open federal bankruptcy matched this owner',
               'searched': True, 'ok_check': True, 'err': '', 't': time.time(), 'cases': [],
               'src': 'courtlistener'},
    })
    BL._HOLD_MEMO = None


# --------------------------------------------------------------------------------------- county + parser
print('-- parser')
check('broward civil prefixes are in scope',
      CK.county_of('CACE-99-000123') == 'broward' and CK.county_of('COCE-99-000123') == 'broward')
check('a palm beach UCN is in scope', CK.county_of(PALM) == 'palmbeach')
check('a Miami stem is not this check', CK.county_of(MIAMI) == '')
check('suggestion of bankruptcy is an active stay',
      CK.classify(rows(('01/02/2026', 'Suggestion of Bankruptcy')))['verdict'] == 'active')
check('notice of filing bankruptcy is an active stay',
      CK.classify(rows(('01/02/2026', 'Notice of Filing Bankruptcy')))['verdict'] == 'active')
check('a bare notice of stay is an active stay',
      CK.classify(rows(('01/02/2026', 'Notice of Stay')))['verdict'] == 'active')
lifted = CK.classify(rows(('01/02/2026', 'Suggestion of Bankruptcy'),
                          ('02/02/2026', 'Order Dismissing Bankruptcy')))
check('an order dismissing the bankruptcy lifts the stay',
      lifted['verdict'] == 'lifted' and lifted['sl'] == '2026-02-02', lifted)
check('a motion for relief from stay does not lift it',
      CK.classify(rows(('01/02/2026', 'Suggestion of Bankruptcy'),
                       ('02/02/2026', 'Motion for Relief from Stay')))['verdict'] == 'active')
# 2026-09-27: relief from stay goes to one creditor while the bankruptcy stays open (sale_history).
check('an order granting relief from stay does not lift it',
      CK.classify(rows(('01/02/2026', 'Suggestion of Bankruptcy'),
                       ('02/02/2026', 'Order Granting Relief from Stay')))['verdict'] == 'active')
check('an order denying relief from stay leaves it active',
      CK.classify(rows(('01/02/2026', 'Suggestion of Bankruptcy'),
                       ('02/02/2026', 'Order Denying Motion for Relief from Stay')))['verdict'] == 'active')
check('an order lifting the stay does not lift it (it may be relief for one creditor)',
      CK.classify(rows(('01/02/2026', 'Suggestion of Bankruptcy'),
                       ('02/02/2026', 'Order Lifting Stay')))['verdict'] == 'active')
check('a motion to lift the stay does not',
      CK.classify(rows(('01/02/2026', 'Suggestion of Bankruptcy'),
                       ('02/02/2026', 'Motion to Lift Stay')))['verdict'] == 'active')
check('a later petition stays active after an older dismissal',
      CK.classify(rows(('01/02/2026', 'Suggestion of Bankruptcy'),
                       ('02/02/2026', 'Order Dismissing Bankruptcy'),
                       ('03/02/2026', 'Notice of Filing Bankruptcy')))['verdict'] == 'active')
check('a stayed case status holds with no docket lines',
      CK.classify([], 'Stayed')['verdict'] == 'active')
check('an abated case status holds', CK.classify([], 'Abated')['verdict'] == 'active')
check('a pending case with no bankruptcy lines is none',
      CK.classify([], 'Pending')['verdict'] == 'none')
check('a blank docket line is a partial read',
      CK.classify([{'date': '01/02/2026', 'text': ''}])['verdict'] == 'partial')


# --------------------------------------------------------------------------------------- API read
print('-- api')
d = isolate('api')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
stub = Stub(routes_for([('01/02/2026', 'Suggestion of Bankruptcy', 'CHAPTER 13', '')]))
entry = CK.read_broward_case(CASE, KEY, stub, clock())
check('a suggestion on the API docket is active and fully read',
      entry['ok_read'] and entry['verdict'] == 'active' and entry['bd'] == '2026-01-02', entry)
check('the read asked only for this case summary and its events',
      [urllib.parse.urlparse(u).path for u in stub.urls] == [
          '/api/case/%s/case.json' % API, '/api/case/%s/events_and_documents.json' % API],
      stub.urls)
check('the key traveled as a query param and not in the stored entry',
      all(urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get('auth_key') == [KEY] for u in stub.urls)
      and KEY not in json.dumps(entry))
CK.store_read({}, CASE, entry, NOW)
blob = json.dumps(entry)
check('the cache entry has no caption, no docket text, and no key',
      'EXAMPLE' not in blob and 'Suggestion' not in blob and 'CHAPTER' not in blob and KEY not in blob, blob)

d = isolate('none')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
stub = Stub(routes_for([]))
entry = CK.read_broward_case(CASE, KEY, stub, clock())
check('a fully read docket with no bankruptcy line is none',
      entry['ok_read'] and entry['verdict'] == 'none' and entry['events'] == 0, entry)

d = isolate('lifted')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
stub = Stub(routes_for([
    ('01/02/2026', 'Suggestion of Bankruptcy', '', ''),
    ('02/02/2026', 'Order', '', 'Order Dismissing Bankruptcy Case'),
]))
entry = CK.read_broward_case(CASE, KEY, stub, clock())
check('the document title is read with the event, and a dismissal order lifts',
      entry['ok_read'] and entry['verdict'] == 'lifted', entry)

d = isolate('partial')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
stub = Stub(routes_for([('01/02/2026', 'Complaint', '', '')]))
stub.routes['/api/case/%s/events_and_documents.json' % API] = (
    200, events_body([('01/02/2026', 'Complaint', '', '')], total=4))
entry = CK.read_broward_case(CASE, KEY, stub, clock())
check('a short event list is partial and not a clear',
      entry['ok_read'] is False and entry['verdict'] == 'partial', entry)

d = isolate('blank')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
stub = Stub(routes_for([('01/02/2026', '', '', '')]))
entry = CK.read_broward_case(CASE, KEY, stub, clock())
check('an event with no description is not a clear',
      entry['ok_read'] is False and entry['verdict'] in ('partial', 'unparseable'), entry)

d = isolate('mismatch')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
stub = Stub({'/api/case/%s/case.json' % API: (200, case_body(number='CACE99000999'))})
entry = CK.read_broward_case(CASE, KEY, stub, clock())
check('a summary for a different case is not a clear',
      entry['ok_read'] is False and entry['verdict'] == 'error', entry)

d = isolate('captcha')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
stub = Stub({'/api/case/%s/case.json' % API: (200, b'<html>cf-turnstile</html>')})
entry = CK.read_broward_case(CASE, KEY, stub, clock())
check('a captcha page holds and does not fetch the docket',
      entry['verdict'] == 'captcha' and entry['ok_read'] is False and len(stub.urls) == 1, entry)

d = isolate('denied')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
stub = Stub({'/api/case/%s/case.json' % API: (401, {'message': 'bad ' + KEY})})
entry = CK.read_broward_case(CASE, KEY, stub, clock())
check('a refused key holds and the reason does not contain the key',
      entry['verdict'] == 'unavailable' and KEY not in json.dumps(entry), entry)

d = isolate('retry')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
n = {'i': 0}

def flaky(url):
    n['i'] += 1
    if n['i'] == 1:
        return 429, {'error': 'slow'}
    return 200, case_body()

stub = Stub({
    '/api/case/%s/case.json' % API: flaky,
    '/api/case/%s/events_and_documents.json' % API: (200, events_body([])),
})
entry = CK.read_broward_case(CASE, KEY, stub, clock())
check('a 429 is retried and a later 200 can clear',
      entry['ok_read'] and entry['verdict'] == 'none' and n['i'] == 2, entry)

d = isolate('keep-active')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
cache = {}
CK.store_read(cache, SG.pacer_key(CASE), CK._entry(
    'broward', 'active', True, bd='2026-01-02', now=NOW - 20 * 86400), NOW - 20 * 86400)
stub = Stub({'/api/case/%s/case.json' % API: (500, b'nope')})
failed = CK.read_broward_case(CASE, KEY, stub, clock())
kept = CK.store_read(cache, SG.pacer_key(CASE), failed, NOW)
check('a failed re-read does not wipe an earlier active stay',
      kept['verdict'] == 'active' and kept['ok_read'] is True and kept.get('tried'), kept)

check('party search and other hosts are refused',
      CK.allowed_url('https://api.browardclerk.org/api/search_cases_filed.json', API) is False
      and CK.allowed_url('https://www.browardclerk.org/Web2/CaseSearchECA/', API) is False
      and CK.allowed_url('https://api.browardclerk.org/api/case/%s/case.json' % API, API) is True)


# --------------------------------------------------------------------------------------- gate
print('-- gate')
d = isolate('gate-off')
os.environ['DEALFLOW_BK_ALLOW_CL_CLEAR'] = '1'
cl_clear(SG.pacer_key(CASE))
CK.save_cache({SG.pacer_key(CASE): CK._entry('broward', 'active', True, bd='2026-01-02', now=NOW)})
sh = str(d / 'sale_history_cache.json')
v = SG.check(CASE, sh)
check('flag off: an active clerk row does not change a CourtListener clear',
      v.get('ok') is True and v.get('src') == 'courtlistener', v)
check('flag off: the dial queue does not add a clerk hold',
      BL.federal_hold(CASE) == (False, ''))

d = isolate('gate-on')
os.environ['DEALFLOW_BK_ALLOW_CL_CLEAR'] = '1'
os.environ['DEALFLOW_CLERK_BK'] = '1'
cl_clear(SG.pacer_key(CASE))
sh = str(d / 'sale_history_cache.json')
v = SG.check(CASE, sh)
check('flag on, docket unread: a CourtListener clear stays held',
      v.get('ok') is False and v.get('code') == SG.UNVERIFIED and 'not been read' in v.get('why', ''), v)
check('flag on, docket unread: the dial queue holds',
      BL.federal_hold(CASE)[0] is True)
held, why = BL.send_hold(CASE, here=str(d))
check('flag on, docket unread: email and letters stay held', held is True, why)
flags = BL.flags_for_cases([CASE])
check('flag on, docket unread: the board flag holds',
      flags.get(SG.pacer_key(CASE), {}).get('hold') is True, flags)

CK.save_cache({SG.pacer_key(CASE): CK._entry('broward', 'none', True, events=4, now=time.time())})
BL._HOLD_MEMO = None
v = SG.check(CASE, sh)
check('flag on, full read, no stay: the CourtListener clear can release',
      v.get('ok') is True, v)
check('flag on, full read, no stay: the dial queue can release',
      BL.federal_hold(CASE) == (False, ''))

CK.save_cache({SG.pacer_key(CASE): CK._entry(
    'broward', 'active', True, bd='2026-01-02', now=time.time())})
BL._HOLD_MEMO = None
v = SG.check(CASE, sh)
check('flag on, active stay: held even though CourtListener would clear',
      v.get('ok') is False and v.get('code') == SG.STAY_ACTIVE and 'ACTIVE' in v.get('why', ''), v)
check('the board flag is a hard hold for that active stay',
      BL.flags_for_cases([CASE]).get(SG.pacer_key(CASE), {}).get('hard') is True)

(d / 'sale_history_cache.json').write_text(json.dumps({
    MIAMI: {'a': False, 'bd': '', 'sl': ''},
}), encoding='utf-8')
v = SG.check(MIAMI, sh)
check('flag on does not change a Miami docket clear', v.get('ok') is True and v.get('code') == SG.CLEAR, v)

os.environ.pop('DEALFLOW_BK_ALLOW_CL_CLEAR', None)
BL._dump(BL.cache_path(), {
    PALM: {'verdict': 'clear', 'why': 'no open federal bankruptcy matched this owner',
           'searched': True, 'ok_check': True, 'err': '', 't': time.time(), 'cases': [],
           'src': 'courtlistener'},
})
BL._HOLD_MEMO = None
v = SG.check(PALM, sh)
check('flag on does not override an unconfirmed CourtListener hold',
      v.get('ok') is False and v.get('code') == 'clear_unconfirmed', v)
os.environ['DEALFLOW_BK_ALLOW_CL_CLEAR'] = '1'
BL._HOLD_MEMO = None
v = SG.check(PALM, sh)
check('flag on: Palm Beach stays held because its docket is not read',
      v.get('ok') is False and 'eCaseView' in v.get('why', ''), v)
stub = Stub({})
os.environ['BROWARD_CLERK_API_KEY'] = KEY
line = CK.accept_case(PALM, transport=stub, clock=clock(), write=False)
check('acceptance does not call Palm Beach',
      stub.urls == [] and 'verdict=unavailable' in line and 'eCaseView' in line, line)

os.environ.pop('BROWARD_CLERK_API_KEY', None)
stub = Stub(routes_for([]))
line = CK.accept_case(CASE, transport=stub, clock=clock(), write=False)
check('acceptance without a key does not call Broward',
      stub.urls == [] and 'not set' in line, line)

d = isolate('gate-raise')
os.environ['DEALFLOW_BK_ALLOW_CL_CLEAR'] = '1'
os.environ['DEALFLOW_CLERK_BK'] = '1'
cl_clear(SG.pacer_key(CASE))
sh = str(d / 'sale_history_cache.json')
real = CK.gate_opinion

def boom(*_a, **_k):
    raise RuntimeError('clerk check boom')

CK.gate_opinion = boom
try:
    v = SG.check(CASE, sh)
    check('flag on, clerk check raises: the lead stays held',
          v.get('ok') is False and v.get('code') == SG.UNVERIFIED and 'failed' in v.get('why', ''), v)
    BL._HOLD_MEMO = None
    check('flag on, clerk check raises: the dial queue holds',
          BL.federal_hold(CASE)[0] is True)
    flags = BL.flags_for_cases([CASE])
    check('flag on, clerk check raises: the board flag holds',
          flags.get(SG.pacer_key(CASE), {}).get('hold') is True, flags)
    os.environ.pop('DEALFLOW_CLERK_BK', None)
    BL._HOLD_MEMO = None
    v = SG.check(CASE, sh)
    check('flag off, even if the clerk check would raise: a CourtListener clear is unchanged',
          v.get('ok') is True and v.get('src') == 'courtlistener', v)
finally:
    CK.gate_opinion = real
    os.environ.pop('DEALFLOW_CLERK_BK', None)


# --------------------------------------------------------------------------------------- lead county
print('-- lead county')
# Numbers that do not match the Broward civil or Palm Beach UCN patterns. Made up.
SHAPES = (
    ('5099-1234TD', 'PALM BEACH'),             # tax deed
    ('12345', 'BROWARD'),                       # bare 5-digit
    ('FMCE-26-000123', 'BROWARD'),
    ('PR-C-26-000123', 'PALM BEACH'),
    ('509999SC000123XXXANB', 'PALM BEACH'),     # small claims
    ('CASE 26-0001234', 'BROWARD'),             # CASE label
)
MIAMI_ODD = '2019-12345'                        # Miami-Dade lead, no stem
CIVIL = 'CACE-99-000177'
check('these shapes are not a clerk county by number alone',
      all(CK.county_of(case) == '' for case, _county in SHAPES) and CK.county_of(MIAMI_ODD) == '')
check('the CASE label keys to its number', SG.pacer_key('CASE 26-0001234') == '26-0001234')
check('a 5-digit Miami-Dade number has no stem', SG.case_stem(MIAMI_ODD) == '')


def _lead_rows():
    rows = [{'case': case, 'county': county, 'owners': ''} for case, county in SHAPES]
    rows.append({'case': MIAMI_ODD, 'county': 'MIAMI-DADE', 'owners': ''})
    rows.append({'case': CIVIL, 'county': 'BROWARD', 'owners': ''})
    return rows


def _arm_clears(cases):
    data = {}
    for case in cases:
        key = SG.pacer_key(case)
        data[key] = {
            'verdict': 'clear', 'why': 'no open federal bankruptcy matched this owner',
            'searched': True, 'ok_check': True, 'err': '', 't': time.time(), 'cases': [],
            'src': 'courtlistener',
        }
    BL._dump(BL.cache_path(), data)
    BL._HOLD_MEMO = None


def _view(case, sh):
    verdict = SG.check(case, sh)
    flag = BL.flags_for_cases([case]).get(SG.pacer_key(case))
    return (
        verdict.get('ok'), verdict.get('code'), verdict.get('src'), verdict.get('why'),
        BL.federal_hold(case), flag,
    )


d = isolate('lead-county')
CK._LEADS_HERE = str(d)
(d / 'sample_leads.json').write_text(json.dumps(_lead_rows()), encoding='utf-8')
_arm_clears([case for case, _county in SHAPES] + [MIAMI_ODD, CIVIL])
sh = str(d / 'sale_history_cache.json')
os.environ['DEALFLOW_BK_ALLOW_CL_CLEAR'] = '1'
try:
    calls = {'n': 0}
    real_load = BL.load_leads

    def _counting(here=BL.HERE):
        calls['n'] += 1
        return real_load(here)

    BL.load_leads = _counting
    try:
        CK._COUNTY_MEMO = None
        loaded, ok_map = CK.county_index()
        again, ok_again = CK.county_index()
        check('the county map is memoized on the lead files',
              ok_map and ok_again and calls['n'] == 1 and loaded == again
              and loaded.get('12345') == 'BROWARD'
              and loaded.get('5099-1234TD') == 'PALM BEACH'
              and loaded.get('FMCE-26-000123') == 'BROWARD'
              and loaded.get('PR-C-26-000123') == 'PALM BEACH'
              and loaded.get('509999SC000123XXXANB') == 'PALM BEACH'
              and loaded.get('26-0001234') == 'BROWARD'
              and loaded.get(MIAMI_ODD) == 'MIAMI-DADE'
              and loaded.get(CIVIL) == 'BROWARD', loaded)
    finally:
        BL.load_leads = real_load

    os.environ.pop('DEALFLOW_CLERK_BK', None)
    BL._HOLD_MEMO = None
    off = {case: _view(case, sh) for case, _county in SHAPES}
    off_miami = _view(MIAMI_ODD, sh)
    check('flag off: each unreadable shape still releases on a CourtListener clear',
          all(v[0] is True and v[1] == SG.CLEAR and v[2] == 'courtlistener' and v[4] == (False, '')
              and v[5] is None for v in list(off.values()) + [off_miami]),
          off)
    os.environ['DEALFLOW_CLERK_BK'] = '1'
    BL._HOLD_MEMO = None
    for case, county in SHAPES:
        verdict, hold, flag = SG.check(case, sh), BL.federal_hold(case), BL.flags_for_cases([case])
        key = SG.pacer_key(case)
        row = flag.get(key) or {}
        check('flag on: %s (%s) is held on the stay gate' % (case, county),
              verdict.get('ok') is False and verdict.get('code') == SG.UNVERIFIED
              and verdict.get('src') == 'clerk_docket'
              and 'not a clerk-readable civil case' in (verdict.get('why') or ''), verdict)
        check('flag on: %s is held on the dial queue' % case,
              hold[0] is True and 'not a clerk-readable civil case' in hold[1], hold)
        check('flag on: %s is held on the board flag' % case,
              row.get('hold') is True and row.get('hard') is False
              and 'not a clerk-readable civil case' in (row.get('why') or ''), row)
    on_miami = _view(MIAMI_ODD, sh)
    check('flag on: a Miami-Dade lead with no stem is unchanged', on_miami == off_miami, on_miami)
    os.environ.pop('DEALFLOW_CLERK_BK', None)
    BL._HOLD_MEMO = None
    check('flag off again matches the first pass',
          {case: _view(case, sh) for case, _county in SHAPES} == off
          and _view(MIAMI_ODD, sh) == off_miami)

    os.environ['DEALFLOW_CLERK_BK'] = '1'
    CK.save_cache({SG.pacer_key(CIVIL): CK._entry('broward', 'none', True, events=2, now=time.time())})
    BL._HOLD_MEMO = None
    civil = _view(CIVIL, sh)
    check('flag on: a Broward civil number with a fresh full read can still release',
          civil[0] is True and civil[1] == SG.CLEAR and civil[4] == (False, '') and civil[5] is None,
          civil)
    still = BL.federal_hold('5099-1234TD')
    check('that fresh civil read does not release a tax-deed number',
          still[0] is True and 'not a clerk-readable civil case' in still[1], still)

    rows = _lead_rows()
    for row in rows:
        if row['case'] == '12345':
            row['county'] = 'MIAMI-DADE'
    (d / 'sample_leads.json').write_text(json.dumps(rows), encoding='utf-8')
    moved, moved_ok = CK.county_index()
    check('a rewritten lead file changes the memoized county',
          moved_ok and moved.get('12345') == 'MIAMI-DADE' and moved.get('5099-1234TD') == 'PALM BEACH',
          moved.get('12345'))
    check('the dial queue follows the rewritten county without a manual cache clear',
          BL.federal_hold('12345') == (False, ''))
    check('the stay gate follows the rewritten county',
          SG.check('12345', sh).get('ok') is True)
    kept = BL.flags_for_cases(['5099-1234TD']).get('5099-1234TD') or {}
    check('a Palm Beach tax deed in that same file stays held',
          kept.get('hold') is True and 'not a clerk-readable civil case' in (kept.get('why') or ''), kept)
finally:
    CK._LEADS_HERE = None
    CK._COUNTY_MEMO = None
    os.environ.pop('DEALFLOW_CLERK_BK', None)
    os.environ.pop('DEALFLOW_BK_ALLOW_CL_CLEAR', None)

d = isolate('lead-unreadable')
CK._LEADS_HERE = str(d)
(d / 'broward_leads.json').write_text('{', encoding='utf-8')
UNREAD_CIVIL = 'CACE-99-000188'
UNREAD_ACTIVE = 'CACE-99-000189'
os.environ['DEALFLOW_BK_ALLOW_CL_CLEAR'] = '1'
_arm_clears([UNREAD_CIVIL, UNREAD_ACTIVE, MIAMI_ODD])
CK.save_cache({
    SG.pacer_key(UNREAD_CIVIL): CK._entry('broward', 'none', True, events=2, now=time.time()),
    SG.pacer_key(UNREAD_ACTIVE): CK._entry(
        'broward', 'active', True, bd='2026-01-02', now=time.time()),
})
(d / 'sale_history_cache.json').write_text(json.dumps({
    MIAMI: {'a': False, 'bd': '', 'sl': ''},
}), encoding='utf-8')
sh = str(d / 'sale_history_cache.json')
try:
    os.environ.pop('DEALFLOW_CLERK_BK', None)
    BL._HOLD_MEMO = None
    off_civil = _view(UNREAD_CIVIL, sh)
    off_odd = _view(MIAMI_ODD, sh)
    off_stem = _view(MIAMI, sh)
    check('flag off: an unreadable lead list does not change a clear',
          off_civil[0] is True and off_civil[4] == (False, '') and off_civil[5] is None
          and off_odd[0] is True and off_stem[0] is True and off_stem[1] == SG.CLEAR,
          (off_civil, off_odd, off_stem))
    os.environ['DEALFLOW_CLERK_BK'] = '1'
    BL._HOLD_MEMO = None
    for case, label in ((UNREAD_CIVIL, 'Broward civil clear'), (MIAMI_ODD, 'Miami-Dade number with no stem')):
        verdict = SG.check(case, sh)
        hold = BL.federal_hold(case)
        row = BL.flags_for_cases([case]).get(SG.pacer_key(case)) or {}
        check('unreadable lead list, flag on: %s stays held on the stay gate' % label,
              verdict.get('ok') is False and verdict.get('code') == SG.UNVERIFIED
              and 'unreadable' in (verdict.get('why') or ''), verdict)
        check('unreadable lead list, flag on: %s stays held on the dial queue' % label,
              hold[0] is True and 'unreadable' in hold[1], hold)
        check('unreadable lead list, flag on: %s stays held on the board flag' % label,
              row.get('hold') is True and 'unreadable' in (row.get('why') or ''), row)
    on_stem = _view(MIAMI, sh)
    check('unreadable lead list, flag on: a Miami-Dade stem is unchanged',
          on_stem == off_stem, on_stem)
    active = SG.check(UNREAD_ACTIVE, sh)
    active_flag = BL.flags_for_cases([UNREAD_ACTIVE]).get(SG.pacer_key(UNREAD_ACTIVE)) or {}
    check('unreadable lead list still reports an active clerk stay',
          active.get('code') == SG.STAY_ACTIVE and 'ACTIVE' in (active.get('why') or '')
          and active_flag.get('hard') is True, (active, active_flag))
finally:
    CK._LEADS_HERE = None
    CK._COUNTY_MEMO = None
    os.environ.pop('DEALFLOW_CLERK_BK', None)
    os.environ.pop('DEALFLOW_BK_ALLOW_CL_CLEAR', None)

d = isolate('clerk-clock')
os.environ['DEALFLOW_BK_ALLOW_CL_CLEAR'] = '1'
os.environ['DEALFLOW_CLERK_BK'] = '1'
key = SG.pacer_key(CASE)

def _cl_at(t):
    BL._dump(BL.cache_path(), {
        key: {'verdict': 'clear', 'why': 'no open federal bankruptcy matched this owner',
              'searched': True, 'ok_check': True, 'err': '', 't': t, 'cases': [],
              'src': 'courtlistener'},
    })
    BL._HOLD_MEMO = None

_cl_at(NOW)
CK.save_cache({key: CK._entry('broward', 'none', True, events=4, now=NOW)})
BL._HOLD_MEMO = None
fresh = BL.federal_hold_index(now=NOW).hold(CASE)
check('the dial queue judges a clerk read at the index clock', fresh == (False, ''), fresh)
_cl_at(NOW + 20 * 86400)
aged = BL.federal_hold_index(now=NOW + 20 * 86400).hold(CASE)
check('the same read is held once it is older than the max age at that clock',
      aged[0] is True and 'clerk docket read is older' in aged[1], aged)
os.environ.pop('DEALFLOW_CLERK_BK', None)
os.environ.pop('DEALFLOW_BK_ALLOW_CL_CLEAR', None)


# --------------------------------------------------------------------------------------- nightly
print('-- nightly')
d = isolate('nightly')
os.environ['BROWARD_CLERK_API_KEY'] = KEY
os.environ['CLERK_BK_MAX_CASES'] = '1'
(d / 'broward_leads.json').write_text(json.dumps([
    {'case': 'CACE-99-000123', 'county': 'BROWARD', 'owners': '', 'days': 10},
    {'case': 'CACE-99-000124', 'county': 'BROWARD', 'owners': '', 'days': 30},
    {'case': PALM, 'county': 'PALM BEACH', 'owners': '', 'days': 1},
]), encoding='utf-8')

def both(url):
    path = urllib.parse.urlparse(url).path
    number = path.split('/')[3]
    if path.endswith('/case.json'):
        return 200, case_body(number=number)
    return 200, events_body([], number=number)

stub = Stub({})
stub.routes_fn = both

def transport(url, timeout):
    stub.urls.append(url)
    return both(url)[0], json.dumps(both(url)[1]).encode()

out = CK.run_nightly(here=str(d), transport=transport, clock=clock())
asked = {urllib.parse.urlparse(u).path.split('/')[3] for u in stub.urls}
check('one run stops at the case cap and does not call Palm Beach',
      out['checked'] == 1 and out['pull_ok'] is False and out['reason'] == 'case_cap'
      and asked == {API} and PALM not in ''.join(stub.urls), (out, asked))
text = saved(d) + (d / 'clerk_bk_status.json').read_text(encoding='utf-8')
check('status and cache do not contain the key or a caption',
      KEY not in text and 'EXAMPLE' not in text, text)
stub.urls.clear()
out2 = CK.run_nightly(here=str(d), transport=transport, clock=CK.Clock(now=NOW + 10, sleeper=lambda _s: None))
asked2 = {urllib.parse.urlparse(u).path.split('/')[3] for u in stub.urls}
check('the next run reads the case the cap left behind',
      out2['checked'] == 1 and asked2 == {'CACE99000124'}, (out2, asked2))

d = isolate('no-key')
out = CK.run_nightly(here=str(d), transport=transport, clock=clock())
check('no API key writes a failed status and does not pretend the pull worked',
      out['pull_ok'] is False and out['reason'] == 'key_missing' and CK.public_status()['pull_ok'] is False, out)

os.environ['DEALFLOW_CLERK_BK'] = '1'
alert = PA.clerk_bk_alert(CK.public_status(), '2026-09-27T21:00:00-04:00')
check('with the flag on, a failed run raises a clerk-bk alert',
      alert and alert['severity'] == 'fail' and 'did not finish' in alert['text'], alert)
os.environ.pop('DEALFLOW_CLERK_BK', None)
check('with the flag off, the same status raises no alert',
      PA.clerk_bk_alert(CK.public_status(), '2026-09-27T21:00:00-04:00') is None)
check('alerts_from stays quiet about clerk-bk while the flag is off',
      'clerk-bk' not in {a['key'] for a in PA.alerts_from({}, PA.now_local())})

os.environ.pop('DEALFLOW_CLERK_BK', None)
CK._MEMO = None
before = CK.public_status()
BL._maybe_clerk_docket()
check('the refresh hook does not run the clerk check while the flag is off',
      CK.public_status() == before)
os.environ['DEALFLOW_CLERK_BK'] = '1'
BL._maybe_clerk_docket()
check('the refresh hook runs it when the flag is on, and still has a status',
      CK.public_status().get('readable') is True and CK.public_status().get('reason') == 'key_missing')


print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
