"""_bklookuptest — CourtListener federal bankruptcy check, stubbed HTTP only.

Run:  python _bklookuptest.py

No network and no real token. Every owner name below is an example. The only phone
is 555-010-5550, and it must never land in the cache or the status file.
"""
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
import contextlib
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = pathlib.Path(tempfile.mkdtemp(prefix='bklookup_'))
TOKEN = 'cl-test-token-not-real'
NOW = 1_800_000_000.0

for _k in ('COURTLISTENER_TOKEN', 'DEALFLOW_BK_PROVIDER', 'DEALFLOW_BK_MAX_AGE_DAYS',
           'DEALFLOW_DIR', 'DEALFLOW_BK_CACHE', 'DEALFLOW_BK_FILINGS', 'DEALFLOW_BK_OVERRIDES',
           'DEALFLOW_BK_STATUS', 'DEALFLOW_BK_BUDGET', 'DEALFLOW_BK_PULL_STATE',
           'BK_MAX_RUNTIME_S', 'BK_FILED_AFTER_YEARS', 'DEALFLOW_BK_ALLOW_CL_CLEAR',
           'DEALFLOW_CLERK_BK'):
    os.environ.pop(_k, None)
os.environ['DEALFLOW_DIR'] = str(TMP / 'boot')

import bk_lookup as BL
import pipeline_alerts as PA
import stay_gate as SG

FAILS = []


def check(name, cond, detail=''):
    text = str(detail)
    if TOKEN in text:
        text = text.replace(TOKEN, '[redacted]')
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + text[:400]) if text and not cond else ''))
    if not cond:
        FAILS.append(name)


def isolate(name, token=TOKEN):
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    os.environ['DEALFLOW_DIR'] = str(d)
    os.environ['DEALFLOW_BK_CACHE'] = str(d / 'bk_lead_cache.json')
    os.environ['DEALFLOW_BK_FILINGS'] = str(d / 'bk_filings.json')
    os.environ['DEALFLOW_BK_OVERRIDES'] = str(d / 'bk_overrides.json')
    os.environ['DEALFLOW_BK_STATUS'] = str(d / 'bk_lookup_status.json')
    os.environ['DEALFLOW_BK_BUDGET'] = str(d / 'bk_budget.json')
    os.environ['DEALFLOW_BK_PULL_STATE'] = str(d / 'bk_pull_state.json')
    os.environ.pop('DEALFLOW_BK_PROVIDER', None)
    os.environ.pop('DEALFLOW_BK_MAX_AGE_DAYS', None)
    os.environ.pop('BK_MAX_RUNTIME_S', None)
    os.environ.pop('BK_FILED_AFTER_YEARS', None)
    os.environ.pop('DEALFLOW_BK_ALLOW_CL_CLEAR', None)
    os.environ.pop('DEALFLOW_CLERK_BK', None)
    BL._HOLD_MEMO = None
    if token is None:
        os.environ.pop('COURTLISTENER_TOKEN', None)
    else:
        os.environ['COURTLISTENER_TOKEN'] = token
    return d


def write_leads(d, rows):
    (d / 'broward_leads.json').write_text(json.dumps(rows), encoding='utf-8')


def clock_at(now):
    return BL.Clock(now=now, sleeper=lambda _s: None)


class Stub:
    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers or {})))
        status, hdrs, body = self.fn(url, headers, len(self.calls))
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        if isinstance(body, str):
            body = body.encode()
        return status, hdrs or {}, body


def recap(no, parties, court='flsb', filed='2026-09-20', terminated=None):
    row = {
        'court_id': court,
        'docketNumber': no,
        'dateFiled': filed,
        'party': parties,
        'caseName': parties[0] if parties else '',
    }
    if terminated:
        row['dateTerminated'] = terminated
    return row


def page(rows, nxt=None):
    return {'results': rows, 'next': nxt}


def q_of(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


# ------------------------------------------------------------------------------------------ names
print('-- names')
owners = BL.owners_from('José Núñez', 'first_last')
check('accents fold to an exact match', BL.match_owners(owners, ['Nunez, Jose']) == 'exact')
owners = BL.owners_from("O'Brien, Mary", 'last_first')
check('apostrophe is ignored', BL.match_owners(owners, ['Mary Obrien']) == 'exact')
owners = BL.owners_from('JUAN M GARCIA', 'first_last')
check('middle initial agrees -> exact', BL.match_owners(owners, ['Garcia, Juan M']) == 'exact')
check('middle initial conflicts -> plausible, not exact',
      BL.match_owners(owners, ['Garcia, Juan R']) == 'plausible')
owners = BL.owners_from('GARCIA LOPEZ, JUAN', 'last_first')
check('double surname, both present -> exact',
      BL.match_owners(owners, ['Garcia Lopez, Juan']) == 'exact')
check('double surname, only one present -> plausible',
      BL.match_owners(owners, ['Lopez, Juan']) == 'plausible')
check('different person is not a match', BL.match_owners(owners, ['Smith, John']) is None)
owners = BL.owners_from('SUNSHINE HOLDINGS LLC', 'first_last')
check('LLC owner matches the entity, tail ignored',
      BL.match_owners(owners, ['Sunshine Holdings, LLC']) == 'exact')
owners = BL.owners_from('THE GARCIA FAMILY TRUST', 'first_last')
check('TRUST owner matches on the name, not the word TRUST',
      BL.match_owners(owners, ['Garcia Family Trust']) == 'exact')
owners = BL.owners_from('GARCIA, JUAN & MARIA', 'last_first')
check('joint owner shares the surname with a bare first name',
      BL.match_owners(owners, ['Garcia, Maria']) == 'exact'
      and BL.match_owners(owners, ['Garcia, Juan']) == 'exact')
owners = BL.owners_from('JUAN GARCIA & MARIA LOPEZ', 'first_last')
check('joint owners keep their own surnames',
      BL.match_owners(owners, ['Lopez, Maria']) == 'exact'
      and BL.match_owners(owners, ['Garcia, Juan']) == 'exact')
plaus = BL.cases_from_hits(
    BL.owners_from('GARCIA LOPEZ, JUAN', 'last_first'),
    [{'court_id': 'flsb', 'no': '26-55577', 'parties': ['Lopez, Juan'], 'open': True, 'filed': '2026-09-01'}])
_v, why = BL.verdict_of(plaus, True, True)
check('plausible open case holds as possible bankruptcy: <case>',
      _v == 'possible' and why == 'possible bankruptcy: 26-55577', why)
exact = BL.cases_from_hits(
    BL.owners_from('GARCIA, MARIA ELENA', 'last_first'),
    [{'court_id': 'flsb', 'no': '26-55501', 'parties': ['Garcia, Maria Elena'], 'open': True,
      'filed': '2026-09-20'}])
_v, why = BL.verdict_of(exact, True, True)
check('exact open case is a hard hold',
      _v == 'active' and why == 'open federal bankruptcy 26-55501 (exact match)', why)
closed = BL.cases_from_hits(
    BL.owners_from('NUNEZ, JOSE', 'last_first'),
    [{'court_id': 'flsb', 'no': '26-55590', 'parties': ['Nunez, Jose'], 'open': False,
      'filed': '2020-01-01'}])
_v, why = BL.verdict_of(closed, True, True)
check('a closed case is not a hold once the search completed',
      _v == 'clear' and not closed[0]['open'], why)
district = BL.cases_from_hits(
    BL.owners_from('GARCIA, MARIA ELENA', 'last_first'),
    [{'court_id': 'flsd', 'no': '26-cv-55501', 'parties': ['Garcia, Maria Elena'], 'open': True}])
check('a district-court hit is not a bankruptcy hold', district == [])
kept = BL.merge_cases(plaus, [])
check('a plausible match is not dropped when a later search omits it',
      len(kept) == 1 and kept[0]['no'] == '26-55577')


# --------------------------------------------------------------------------------------- provider
print('-- provider and budget')
pcl = BL.get_provider('pacer', env={})
ok, _why = pcl.available({})
check('PACER Case Locator provider is not available', ok is False and pcl.name == BL.PROVIDER_PACER)
cl = BL.CourtListenerProvider({BL.ENV_TOKEN: TOKEN})
ok, _why = cl.available()
check('CourtListener is available when the env dict holds the token', ok is True)
check('auth header is Token <token>', cl.auth_headers().get('Authorization') == 'Token ' + TOKEN)
missing = BL.CourtListenerProvider({})
ok, why = missing.available()
check('missing token is unavailable and names the env var',
      ok is False and 'COURTLISTENER_TOKEN' in why and TOKEN not in why)
url = cl.filings_request('flsb', '2026-09-01')
check('filings request is type=d for court flsb',
      'type=d' in url and 'court=flsb' in url and 'filed_after=2026-09-01' in url
      and url.startswith('https://www.courtlistener.com/api/rest/v4/search/'))
parsed = cl.parse_search({'results': [recap('26-55501', ['Garcia, Maria Elena'])],
                          'next': 'https://evil.example/steal'})
check('an off-host next URL is dropped', parsed['next'] == '' and parsed['results'][0]['no'] == '26-55501')
check('a terminated docket is closed',
      cl.parse_search(page([recap('26-55590', ['Nunez, Jose'], terminated='2024-01-15')]))['results'][0]['open']
      is False)

now = NOW
check('5 hits inside the minute leave no room', BL.Budget([now - 10] * 5, now).room(now) is False)
check('5 hits just outside the minute leave room', BL.Budget([now - 61] * 5, now).room(now) is True)
check('50 hits inside the hour leave no room', BL.Budget([now - 10] * 50, now).room(now) is False)
# Older than an hour so the minute and hour caps are not what this is measuring.
aged = [now - 4000] * 115
check('115 hits plus the nightly reserve of 10 do not fit the day cap',
      BL.Budget(aged, now).room(now, reserve=10) is False)
check('115 hits with no reserve still fit under 125',
      BL.Budget(aged, now).room(now, reserve=0) is True)
check('125 hits inside the day leave no room', BL.Budget([now - 10] * 125, now).room(now) is False)
check('125 hits that just aged out of the day window leave room',
      BL.Budget([now - 86400] * 125, now).room(now) is True)

called = []


def _no_http(url, headers):
    called.append(url)
    return 200, {}, b'{}'


try:
    BL.http_get('https://www.courtlistener.com/api/rest/v4/search/?type=d', cl,
                _no_http, BL.Budget([now - 5] * 125, now), clock_at(now))
    raised = False
    err = ''
except BL.BudgetExhausted as e:
    raised = True
    err = str(e)
    check('budget error text does not contain the token', TOKEN not in err)
check('a full day budget raises before any HTTP', raised and called == [])


def _echo_token(url, headers):
    return 500, {}, ('echo ' + TOKEN).encode()


try:
    BL.http_get('https://www.courtlistener.com/api/rest/v4/search/?type=d', cl,
                _echo_token, BL.Budget([], now), clock_at(now))
    leaked = True
except BL.ProviderError as e:
    leaked = TOKEN in str(e)
check('an HTTP error body that echoes the token is not returned', leaked is False)


# ----------------------------------------------------------------------------------------- nightly
print('-- nightly pull')
d = isolate('nightly')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [
    {'case': 'CACE-99-555801', 'county': 'BROWARD', 'owners': 'GARCIA, MARIA ELENA',
     'phone': '555-010-5550', 'st': 'OK'},
    {'case': 'CACE-99-555802', 'county': 'BROWARD', 'owners': 'NUNEZ, JOSE', 'st': 'OK'},
    {'case': '502026CA005551XXXXMB', 'county': 'PALM BEACH', 'owners': 'GARCIA LOPEZ, JUAN CARLOS',
     'st': 'OK'},
])


def nightly_route(url, headers, n):
    if headers.get('Authorization') != 'Token ' + TOKEN:
        return 401, {}, {'detail': 'bad token'}
    q = q_of(url)
    if q.get('court') == ['flsb']:
        return 200, {}, page([recap('26-55501', ['Garcia, Maria Elena'])])
    query = (q.get('q') or [''])[0].upper()
    if 'NUNEZ' in query:
        return 200, {}, page([recap('26-55590', ['Nunez, Jose'], terminated='2024-01-15')])
    if 'JUAN' in query:
        return 200, {}, page([recap('26-55577', ['Lopez, Juan'])])
    if 'MARIA' in query:
        return 200, {}, page([recap('26-55501', ['Garcia, Maria Elena'])])
    return 200, {}, page([])


stub = Stub(nightly_route)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    out = BL.run_nightly(here=str(leads), transport=stub, clock=clock_at(time.time()))
log = buf.getvalue()
check('nightly log does not contain the token or an owner name',
      TOKEN not in log and 'GARCIA' not in log and '555-010' not in log, log)
check('nightly pull succeeded', out.get('pull_ok') is True, out)
filing_urls = [u for u, _h in stub.calls if q_of(u).get('court') == ['flsb']]
check('the pull asks CourtListener for flsb filings', len(filing_urls) == 1, filing_urls[:1])
check('every request used the Token header',
      all(h.get('Authorization') == 'Token ' + TOKEN for _u, h in stub.calls) and stub.calls)

sh = str(d / 'sale_history_cache.json')
maria = SG.check('CACE-99-555801', sh)
jose = SG.check('CACE-99-555802', sh)
juan = SG.check('502026CA005551XXXXMB', sh)
check('new filing matched -> hard hold',
      maria['ok'] is False and maria['code'] == SG.STAY_ACTIVE
      and maria['why'] == 'open federal bankruptcy 26-55501 (exact match)', maria)
check('a closed case is not a hard hold, and the CourtListener clear stays held',
      jose['ok'] is False and jose['code'] == 'clear_unconfirmed'
      and 'not a confirmed clear' in jose['why'], jose)
os.environ['DEALFLOW_BK_ALLOW_CL_CLEAR'] = '1'
try:
    jose_ok = SG.check('CACE-99-555802', sh)
finally:
    os.environ.pop('DEALFLOW_BK_ALLOW_CL_CLEAR', None)
check('a CourtListener clear releases a Broward lead only when the owner allows it',
      jose_ok['ok'] is True and jose_ok['code'] == SG.CLEAR, jose_ok)
check('one-of-two surnames -> possible bankruptcy hold',
      juan['ok'] is False and juan['why'] == 'possible bankruptcy: 26-55577', juan)

flags = BL.flags_for_cases(['CACE-99-555801', 'CACE-99-555802', '502026CA005551XXXXMB'])
check('board flags hold the exact, the plausible, and the unconfirmed clear',
      flags.get('CACE-99-555801', {}).get('hard') is True
      and flags.get('CACE-99-555802', {}).get('hold') is True
      and flags.get('CACE-99-555802', {}).get('hard') is not True
      and flags.get('502026CA005551XXXXMB', {}).get('why') == 'possible bankruptcy: 26-55577', flags)

status = json.loads((d / 'bk_lookup_status.json').read_text(encoding='utf-8'))
blob = json.dumps(status)
check('status is counts only',
      status.get('pull_ok') is True and status.get('filings', 0) >= 1
      and status.get('leads_checked', 0) >= 3 and status.get('holds') == 3
      and status.get('errors') == 0 and status.get('requests_used', 0) >= 1
      and status.get('reason') == 'ok' and 'GARCIA' not in blob and '555-010' not in blob
      and TOKEN not in blob and '26-55501' not in blob, blob)
pub = BL.public_status()
check('public status is readable and fresh',
      pub.get('readable') is True and pub.get('pull_ok') is True
      and pub.get('pull_age_h') is not None and pub['pull_age_h'] < 1, pub)
check('a fresh pull raises no bk-lookup alert',
      PA.bk_lookup_alert(pub, '2026-09-27T05:30:00-04:00') is None)
filings_txt = (d / 'bk_filings.json').read_text(encoding='utf-8')
check('debtor names stay in the DEALFLOW filings cache, not the status',
      'Garcia' in filings_txt and str(d / 'bk_filings.json').startswith(str(TMP)))
cache_txt = (d / 'bk_lead_cache.json').read_text(encoding='utf-8')
check('the lead cache keeps case numbers and not the phone or the token',
      '26-55501' in cache_txt and '555-010' not in cache_txt and TOKEN not in cache_txt
      and 'GARCIA' not in cache_txt)


# ------------------------------------------------------------------------------------ missing token
print('-- missing token')
d = isolate('notoken', token=None)
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{'case': 'CACE-99-555801', 'county': 'BROWARD', 'owners': 'GARCIA, MARIA ELENA'}])
stub = Stub(lambda url, headers, n: (_ for _ in ()).throw(AssertionError('http')))
try:
    with contextlib.redirect_stdout(io.StringIO()):
        out = BL.run_nightly(here=str(leads), transport=stub, clock=clock_at(time.time()))
    crashed = False
except Exception as e:
    crashed = True
    out = {'err': str(e)}
check('missing token does not call HTTP and does not crash',
      not crashed and stub.calls == [] and out.get('pull_ok') is False, out)
st = json.loads((d / 'bk_lookup_status.json').read_text(encoding='utf-8'))
check('missing token status reason is token_missing',
      st.get('reason') == 'token_missing' and st.get('pull_ok') is False and TOKEN not in json.dumps(st), st)
held = SG.check('CACE-99-555801', str(d / 'sale_history_cache.json'))
check('missing token leaves the lead held', held['ok'] is False, held)
flags = BL.flags_for_cases(['CACE-99-555801'])
check('board says the check has not run',
      flags.get('CACE-99-555801', {}).get('why') == 'federal bankruptcy check has not run for this lead', flags)
alert = PA.bk_lookup_alert(BL.public_status(), '2026-09-27T05:30:00-04:00')
check('a failed pull raises a counts-only fail alert',
      alert and alert['severity'] == 'fail' and 'GARCIA' not in alert['text']
      and '26-555' not in alert['text'] and TOKEN not in alert['text'], alert)


# ---------------------------------------------------------------------------------------------- 429
print('-- 429')
d = isolate('limited')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{'case': 'CACE-99-555801', 'county': 'BROWARD', 'owners': 'GARCIA, MARIA ELENA'}])
stub = Stub(lambda url, headers, n: (429, {'Retry-After': '0'}, {'detail': 'slow ' + TOKEN}))
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('CACE-99-555801', here=str(leads), transport=stub,
                          clock=clock_at(time.time()), max_wait=None)
check('429 exhaustion stops after the bounded retries',
      len(stub.calls) == BL.RETRY_429 + 1 and br.get('status') == 'rate_limit', (len(stub.calls), br))
check('429 why holds the lead and does not echo the token',
      br.get('verdict') != 'clear' and TOKEN not in json.dumps(br), br)
v = SG.check('CACE-99-555801', str(d / 'sale_history_cache.json'))
check('429 exhaustion leaves the lead held', v['ok'] is False and '429' in v.get('why', ''), v)


# ------------------------------------------------------------------------------------ budget spent
print('-- budget spent')
d = isolate('spent')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{'case': 'CACE-99-555801', 'county': 'BROWARD', 'owners': 'GARCIA, MARIA ELENA'}])
spent_now = time.time()
BL.save_budget(BL.Budget([spent_now] * 5, spent_now))
stub = Stub(lambda url, headers, n: (200, {}, page([])))
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('CACE-99-555801', here=str(leads), transport=stub,
                          clock=clock_at(spent_now))
check('a spent minute budget holds the lead with no HTTP',
      stub.calls == [] and br.get('status') == 'budget' and 'stays held' in br.get('why', ''), br)
v = SG.check('CACE-99-555801', str(d / 'sale_history_cache.json'))
check('budget exhaustion is a hold', v['ok'] is False, v)


# ----------------------------------------------------------------------------------------- override
print('-- override')
d = isolate('override')
now = time.time()
case_a = {'no': '26-55501', 'court': 'flsb', 'open': True, 'match': 'plausible', 'filed': '2026-09-01'}
case_b = {'no': '26-55502', 'court': 'flsb', 'open': True, 'match': 'exact', 'filed': '2026-09-02'}


def entry(cases):
    return {'verdict': 'possible', 'why': 'possible bankruptcy: 26-55501', 'searched': True,
            'ok_check': True, 'err': '', 't': now, 'cases': cases, 'src': 'courtlistener'}


BL._dump(BL.cache_path(), {
    'CACE-99-555801': entry([case_a]),
    'CACE-99-555802': entry([case_a]),
    'CACE-99-555803': entry([case_a, case_b]),
})
(d / 'bk_overrides.json').write_text(json.dumps({
    'CACE-99-555801': ['26-55501'],
    'clears': [{'lead_id': 'CACE-99-555801', 'case_number': '26-55501'}],
}), encoding='utf-8')
sh = str(d / 'sale_history_cache.json')
a = SG.check('CACE-99-555801', sh)
b = SG.check('CACE-99-555802', sh)
c = SG.check('CACE-99-555803', sh)
check('an override is not a confirmed clear, so the lead stays held',
      a['ok'] is False and a['code'] == 'clear_unconfirmed', a)
check('the same case still holds the other lead',
      b['ok'] is False and b['why'] == 'possible bankruptcy: 26-55501', b)
check('a second open case on the cleared lead still holds',
      c['ok'] is False and '26-55502' in c['why'], c)
flags = BL.flags_for_cases(['CACE-99-555801', 'CACE-99-555802'])
check('the board still holds the overridden lead until a clear is allowed',
      flags.get('CACE-99-555801', {}).get('hold') is True
      and flags['CACE-99-555801'].get('hard') is not True
      and 'CACE-99-555802' in flags, flags)
os.environ['DEALFLOW_BK_ALLOW_CL_CLEAR'] = '1'
try:
    a2 = SG.check('CACE-99-555801', sh)
    flags2 = BL.flags_for_cases(['CACE-99-555801', 'CACE-99-555802'])
finally:
    os.environ.pop('DEALFLOW_BK_ALLOW_CL_CLEAR', None)
check('allowing a CourtListener clear releases only the overridden lead',
      a2['ok'] is True and a2['code'] == SG.CLEAR
      and 'CACE-99-555801' not in flags2 and 'CACE-99-555802' in flags2, (a2, flags2))


# -------------------------------------------------------------------------------------------- miami
print('-- miami unchanged')
d = isolate('miami')
sh = d / 'sale_history_cache.json'
sh.write_text(json.dumps({'2099-000555-CA-01': {'a': False, 'bd': '', 'sl': ''}}), encoding='utf-8')
v = SG.check('2099-000555-CA-01', str(sh))
check('Miami docket clear stays clear when the lookup has not run', v['ok'] is True, v)
check('Miami is not board-held just because the lookup has not run',
      '2099-000555-CA-01' not in BL.flags_for_cases(['2099-000555-CA-01']))
check('an unchecked Broward lead is board-held',
      BL.flags_for_cases(['CACE-99-555810']).get('CACE-99-555810', {}).get('hold') is True)
BL._dump(BL.cache_path(), {
    '2099-000555-CA-01': {
        'verdict': 'clear', 'why': 'no open federal bankruptcy matched this owner',
        'searched': True, 'ok_check': True, 'err': '', 't': time.time(), 'cases': [],
    },
})
v = SG.check('2099-000555-CA-01', str(sh))
check('a CourtListener clear does not replace the Miami docket verdict', v['ok'] is True, v)
BL._dump(BL.cache_path(), {
    '2099-000555-CA-01': entry([{
        'no': '26-55501', 'court': 'flsb', 'open': True, 'match': 'exact', 'filed': '2026-09-20'}]),
})
v = SG.check('2099-000555-CA-01', str(sh))
check('an exact open CourtListener match holds a Miami docket-clear lead',
      v['ok'] is False and v['code'] == SG.STAY_ACTIVE and v.get('src') == 'courtlistener', v)


# ---------------------------------------------------------------------------------------- staleness
print('-- staleness and alerts')
d = isolate('stale')
old = time.time() - 20 * 86400
BL._dump(BL.cache_path(), {
    'CACE-99-555801': {
        'verdict': 'clear', 'why': 'no open federal bankruptcy matched this owner',
        'searched': True, 'ok_check': True, 'err': '', 't': old, 'cases': [],
    },
})
v = SG.check('CACE-99-555801', str(d / 'sale_history_cache.json'))
check('a clear older than 14 days is not a pass',
      v['ok'] is False and 'older than' in v.get('why', ''), v)
flags = BL.flags_for_cases(['CACE-99-555801'])
check('a stale clear is still a board hold',
      flags.get('CACE-99-555801', {}).get('hold') is True and 'older than' in flags['CACE-99-555801']['why'],
      flags)

d = isolate('alerts')
BL.write_status(pull_ok=True, pull_t=time.time() - 40 * 3600, filings=3, leads_checked=1,
                holds=0, errors=0, requests_used=2, provider='courtlistener', reason='ok')
alert = PA.bk_lookup_alert(BL.public_status(), '2026-09-27T05:30:00-04:00')
check('a pull older than 36h raises a fail alert',
      alert and alert['severity'] == 'fail' and '36h' in alert['text'] and '26-' not in alert['text'], alert)
os.remove(BL.status_path())
alert = PA.bk_lookup_alert(BL.public_status(), '2026-09-27T05:30:00-04:00')
check('a missing status file raises a fail alert',
      alert and alert['severity'] == 'fail' and 'no status file' in alert['text'], alert)


# --------------------------------------------------------------------------------------- queues
print('-- queues')
d = isolate('queues')
check('dial and knock queues do not drop a lead before any cache file exists',
      BL.federal_hold('CACE-99-555801') == (False, '')
      and BL.federal_hold('C107') == (False, ''))
held, why = BL.send_hold('CACE-99-555801', here=str(d))
check('a letter to a keyable non-stem lead is held with no cache file', held is True, why)
check('a case number this check cannot key is not a letter hold',
      BL.send_hold('C107', here=str(d)) == (False, ''))
BL._dump(BL.cache_path(), {})
BL._HOLD_MEMO = None
qheld, qwhy = BL.federal_hold('CACE-99-555801')
check('once the cache file exists, the dial queue holds an unchecked Broward lead',
      qheld is True and 'has not run' in qwhy, (qheld, qwhy))
check('the dial queue still does not hold a Miami stem the check has not touched',
      BL.federal_hold('2099-000555-CA-01') == (False, ''))


# ------------------------------------------------------------------------------------ sticky search
print('-- sticky plausible')
d = isolate('sticky')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{'case': 'CACE-99-555801', 'county': 'BROWARD', 'owners': 'GARCIA LOPEZ, JUAN'}])
now = time.time()
BL._dump(BL.cache_path(), {
    'CACE-99-555801': entry([case_a]),
})
stub = Stub(lambda url, headers, n: (200, {}, page([])))
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('CACE-99-555801', here=str(leads), transport=stub, clock=clock_at(now - 20 * 86400))
check('a later empty search does not auto-clear a plausible match',
      br.get('verdict') == 'possible' and stub.calls, br)


# --------------------------------------------------------------------------------------- pacer stub
print('-- provider swap')
d = isolate('pcl')
os.environ['DEALFLOW_BK_PROVIDER'] = 'pacer'
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{'case': 'CACE-99-555801', 'county': 'BROWARD', 'owners': 'GARCIA, MARIA ELENA'}])
stub = Stub(lambda url, headers, n: (200, {}, page([])))
with contextlib.redirect_stdout(io.StringIO()):
    out = BL.run_nightly(here=str(leads), transport=stub, clock=clock_at(time.time()))
check('selecting the PACER provider holds without HTTP',
      stub.calls == [] and out.get('pull_ok') is False, out)
st = json.loads((d / 'bk_lookup_status.json').read_text(encoding='utf-8'))
check('PACER-provider status says unavailable and is not courtlistener',
      st.get('reason') == 'provider_unavailable' and st.get('provider') == 'other', st)
os.environ.pop('DEALFLOW_BK_PROVIDER', None)


# ------------------------------------------------------------------------------------ minute waits
print('-- minute cap waits')
d = isolate('minute-wait')
now = NOW
budget = BL.Budget([now - 5] * 5, now)
clock = clock_at(now)
waited = []


def _after_wait(url, headers):
    waited.append(clock.time())
    return 200, {}, json.dumps(page([])).encode()


try:
    BL.http_get('https://www.courtlistener.com/api/rest/v4/search/?type=d', cl,
                _after_wait, budget, clock)
    minute_raised = False
except BL.BudgetExhausted:
    minute_raised = True
check('a full minute budget waits, then requests, and does not fail',
      minute_raised is False and len(waited) == 1 and waited[0] >= now + 50, waited)


# --------------------------------------------------------------------------------------- time + pull
print('-- pace, resume, priority')
d = isolate('pace')
os.environ['BK_MAX_RUNTIME_S'] = '70'
leads = d / 'leads'
leads.mkdir()
rows = []
for i in range(30):
    row = {
        'case': 'CACE-99-556%03d' % i,
        'county': 'BROWARD',
        'owners': 'SAMPLE, LEAD%02d' % i,
        'st': 'OK',
        'days': 100 + i,
        'phones': ['555010%04d' % i],
    }
    if i == 0:
        row = {
            'case': '2099-000101-CA-01',
            'county': 'MIAMI-DADE',
            'owners': 'SAMPLE, LEAD00',
            'st': 'OK',
            'days': 2,
            'emails': ['lead00@example.com'],
            'phones': ['5550100000'],
            'addr': '1 MAIN ST, MIAMI, FL 33101',
        }
    if i == 29:
        row = {
            'case': 'CACE-99-556029',
            'county': 'BROWARD',
            'owners': 'SAMPLE, LEAD29',
            'st': 'OK',
            'days': 900,
        }
    rows.append(row)
write_leads(leads, rows)
clock = BL.Clock(now=NOW, sleeper=lambda _s: None)
pages = []
violations = []


def _pace_route(url, headers, n):
    q = q_of(url)
    now_ = clock.time()
    hits = BL.load_budget(now_).hits
    minute = sum(1 for t in hits if now_ - t < BL.WIN_MINUTE) + 1
    hour = sum(1 for t in hits if now_ - t < BL.WIN_HOUR) + 1
    day = sum(1 for t in hits if now_ - t < BL.WIN_DAY) + 1
    if minute > BL.CAP_MINUTE or hour > BL.CAP_HOUR or day > BL.CAP_DAY:
        violations.append((minute, hour, day, url[:80]))
    if q.get('court') == ['flsb']:
        cur = int((q.get('cursor') or ['1'])[0])
        pages.append(cur)
        nxt = None
        if cur < 25:
            nxt = ('https://www.courtlistener.com/api/rest/v4/search/'
                   '?type=d&court=flsb&cursor=%d' % (cur + 1))
        return 200, {}, page([recap('26-7%04d' % cur, ['NOMATCH, PERSON'])], nxt)
    return 200, {}, page([])


stub = Stub(_pace_route)
first = None
first_state = {}
caught = False
for _i in range(40):
    before = len(stub.calls)
    with contextlib.redirect_stdout(io.StringIO()):
        out = BL.run_nightly(here=str(leads), transport=stub, clock=clock)
    if first is None:
        first = out
        first_state = json.loads((d / 'bk_pull_state.json').read_text(encoding='utf-8'))
    if len(stub.calls) == before:
        clock.now += 3600
    if out.get('pull_ok') and len([u for u, _h in stub.calls if q_of(u).get('court') != ['flsb']]) >= 30:
        caught = True
        break
party_urls = [u for u, _h in stub.calls if q_of(u).get('court') != ['flsb']]
_cur = str(first_state.get('cursor') or '')
check('runtime limit stops with time_budget and a saved cursor',
      first and first.get('pull_ok') is False and first.get('reason') == 'time_budget'
      and 'courtlistener.com' in _cur
      and q_of(_cur).get('cursor') not in (None, [], ['1']),
      (first, _cur))
check('the 25-page pull is resumed, not restarted, and then pull_ok',
      caught and pages == list(range(1, 26)) and out.get('pull_ok') is True, (pages, out))
check('minute, hour, and day caps were never exceeded', violations == [], violations[:3])
check('a Miami lead next to be contacted is searched before a far Broward lead',
      party_urls and 'LEAD00' in party_urls[0].upper()
      and any('LEAD29' in u.upper() for u in party_urls)
      and next(i for i, u in enumerate(party_urls) if 'LEAD00' in u.upper())
      < next(i for i, u in enumerate(party_urls) if 'LEAD29' in u.upper()),
      party_urls[:2])
st = json.loads((d / 'bk_lookup_status.json').read_text(encoding='utf-8'))
check('a caught-up pull is pull_ok and the status has no owner name',
      st.get('pull_ok') is True and 'SAMPLE' not in json.dumps(st) and TOKEN not in json.dumps(st), st)


# ------------------------------------------------------------------------------------ eight requests
print('-- cli --case')
d = isolate('cli')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{
    'case': 'CACE-99-555801', 'county': 'BROWARD',
    'owners': 'GARCIA, JUAN; LOPEZ, MARIA; NUNEZ, JOSE; RIVERA, ANA', 'st': 'OK',
}])
clock = clock_at(NOW)


def _eight(url, headers, n):
    cur = (q_of(url).get('cursor') or ['1'])[0]
    if cur == '2':
        return 200, {}, page([])
    return 200, {}, page([], 'https://www.courtlistener.com/api/rest/v4/search/?type=d&cursor=2')


stub = Stub(_eight)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = BL.main(['--case', 'CACE-99-555801'], clock=clock, transport=stub, here=str(leads))
text = buf.getvalue()
check('an 8-request --case finishes and paces past the minute cap',
      rc == 0 and len(stub.calls) == 8 and clock.time() >= NOW + 50,
      (rc, len(stub.calls), clock.time() - NOW))
check('the --case line has no owner name and no token',
      'flagged=' in text and 'GARCIA' not in text and 'LOPEZ' not in text
      and 'RIVERA' not in text and TOKEN not in text, text)

d = isolate('cli-two')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [
    {'case': 'CACE-99-555801', 'county': 'BROWARD', 'owners': 'GARCIA, MARIA ELENA', 'st': 'OK'},
    {'case': 'CACE-99-555802', 'county': 'BROWARD', 'owners': 'NUNEZ, JOSE', 'st': 'OK'},
])


def _two(url, headers, n):
    query = (q_of(url).get('q') or [''])[0].upper()
    if 'MARIA' in query:
        return 200, {}, page([recap('26-55501', ['Garcia, Maria Elena'])])
    return 200, {}, page([])


stub = Stub(_two)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = BL.main(['--case', 'CACE-99-555801', '--case', 'CACE-99-555802'],
                 clock=clock_at(NOW), transport=stub, here=str(leads))
lines = [ln.strip() for ln in buf.getvalue().splitlines() if 'flagged=' in ln]
check('two --case arguments print one line each, with no names',
      rc == 0 and len(lines) == 2
      and lines[0] == 'CACE-99-555801 flagged=yes match=exact bk=26-55501'
      and lines[1] == 'CACE-99-555802 flagged=no match=none bk=-'
      and 'GARCIA' not in buf.getvalue() and 'NUNEZ' not in buf.getvalue()
      and TOKEN not in buf.getvalue(), lines)


# -------------------------------------------------------------------------------- unreadable budget
print('-- unreadable budget')
d = isolate('bad-budget')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{'case': 'CACE-99-555801', 'county': 'BROWARD', 'owners': 'GARCIA, MARIA ELENA'}])
(d / 'bk_budget.json').write_text('not-json', encoding='utf-8')
b = BL.load_budget(NOW)
check('an unreadable budget file is a spent day',
      b.unreadable is True and b.counts(NOW)['day'] >= BL.CAP_DAY, b.counts(NOW))
stub = Stub(lambda url, headers, n: (200, {}, page([])))
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('CACE-99-555801', here=str(leads), transport=stub, clock=clock_at(NOW))
check('an unreadable budget holds the lead with no HTTP',
      stub.calls == [] and br.get('status') == 'budget', br)
check('the unreadable budget file is not reset',
      (d / 'bk_budget.json').read_text(encoding='utf-8') == 'not-json')


# --------------------------------------------------------------------------------------- call rows
print('-- call rows')
d = isolate('callrows')
BL._dump(BL.cache_path(), {})
BL._HOLD_MEMO = None
import call_mode as CM
import morning_planner as MP

_reads = {'n': 0}
_orig_index = BL.federal_hold_index


def _counting_index():
    _reads['n'] += 1
    return _orig_index()


BL.federal_hold_index = _counting_index
miami = {
    'case': '2099-000427-CA-01', 'county': 'MIAMI-DADE', 'st': 'LP', 'stage': 'LP',
    'owners': 'ROE,MARY', 'oname': 'Mary Roe', 'addr': '10 EAST ST, Miami, FL 33101',
    'folio': '555501210050', 'mail': '', 'value': 0, 'judg': 0, 'eq': None, 'eqfake': False,
    'days': 9999, 'auction': '', 'phones': ['9545550101'], 'phdnc': [False], 'phsrc': ['st'],
    'phrank': [''], 'phbest': 0, 'emails': [], 'vac': False, 'warn': '', 'ctype': 'Bank/Mortgage',
}
broward = dict(miami)
broward['case'] = 'CACE-99-555801'
broward['county'] = 'BROWARD'
broward['addr'] = '10 EAST ST, Sample City, FL 33301'
try:
    rows, _n = CM.call_rows([miami, broward], max_days=60)
finally:
    BL.federal_hold_index = _orig_index
cases = {r.get('c') for r in rows}
check('call_rows keeps an unverified Miami LP lead', '2099-000427-CA-01' in cases, cases)
check('call_rows drops a Broward lead with no fresh clear', 'CACE-99-555801' not in cases, cases)
check('call_rows reads the bankruptcy cache once', _reads['n'] == 1, _reads)
check('the knock planner keeps the Miami lead and drops the Broward lead',
      MP._knock_eligible(miami) is True and MP._knock_eligible(broward) is False)
fresh = dict(broward)
BL._dump(BL.cache_path(), {
    'CACE-99-555801': {
        'verdict': 'clear', 'why': 'no open federal bankruptcy matched this owner',
        'searched': True, 'ok_check': True, 'err': '', 't': time.time(), 'cases': [],
        'src': 'courtlistener',
    },
})
BL._HOLD_MEMO = None
rows, _n = CM.call_rows([fresh], max_days=60)
check('call_rows drops a Broward lead whose CourtListener clear is unconfirmed',
      rows == [], rows)
held, why = BL.send_hold('CACE-99-555801', here=str(d))
blocked, bwhy = BL.contact_blocked_reason('CACE-99-555801', here=str(d))
check('email, letters, and text stay held on an unconfirmed CourtListener clear',
      held is True and blocked is True and 'not a confirmed clear' in why
      and 'not a confirmed clear' in bwhy, (held, why, blocked, bwhy))
os.environ['DEALFLOW_BK_ALLOW_CL_CLEAR'] = '1'
try:
    BL._HOLD_MEMO = None
    rows, _n = CM.call_rows([fresh], max_days=60)
    released = BL.send_hold('CACE-99-555801', here=str(d))
finally:
    os.environ.pop('DEALFLOW_BK_ALLOW_CL_CLEAR', None)
    BL._HOLD_MEMO = None
check('those channels release the Broward lead only when a CourtListener clear is allowed',
      {r.get('c') for r in rows} == {'CACE-99-555801'} and released == (False, ''), rows)


def court_params(url):
    """Every court= value, in order. The live server keeps only the last one."""
    query = urllib.parse.urlparse(url).query
    return [v for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True) if k == 'court']


def effective_courts(url):
    vals = court_params(url)
    if not vals:
        return []
    return vals[-1].split()


def party_q(url):
    return (q_of(url).get('q') or [''])[-1]


# ---------------------------------------------------------------------------------- filtered search
print('-- filtered party search')
url = cl.party_request('party:(GARCIA AND MARIA)')
pq = q_of(url)
vals = court_params(url)
ids = vals[0].split() if len(vals) == 1 else []
check('party search sends one court value covering the bankruptcy courts',
      len(vals) == 1 and len(pq.get('court') or []) == 1
      and 'flsb' in ids and 'flmb' in ids and 'flnb' in ids
      and all(part.endswith('b') for part in ids) and len(ids) >= 90
      and pq.get('filed_after') == [BL.default_filed_after()]
      and pq.get('order_by') == ['dateFiled desc'] and pq.get('type') == ['d'],
      (len(vals), len(ids), pq.get('filed_after'), pq.get('order_by')))
os.environ['BK_FILED_AFTER_YEARS'] = '3'
check('filed-after years are configurable',
      BL.default_filed_after()[:4] == str(BL.dt.date.today().year - 3))
os.environ.pop('BK_FILED_AFTER_YEARS', None)
check('the narrow window is shorter than the eligible window',
      BL.narrow_filed_after() > BL.default_filed_after())
nurl = cl.party_request('party:(GARCIA AND MARIA)', filed_after=BL.narrow_filed_after(),
                        courts=BL.FL_BK_COURT_IDS)
nq = party_q(nurl)
check('the narrow request is the three Florida bankruptcy courts and the name only',
      court_params(nurl) == ['flsb flmb flnb']
      and not re.search(r'\d', nq) and 'MIAMI' not in nq.upper() and '"' not in nq
      and q_of(nurl).get('filed_after') == [BL.narrow_filed_after()],
      (court_params(nurl), nq))
saved_ids, saved_fl = BL.BK_COURT_IDS, BL.FL_BK_COURT_IDS
try:
    BL.BK_COURT_IDS = BL.FL_BK_COURT_IDS
    os.environ['BK_FILED_AFTER_YEARS'] = '1'
    check('a narrow scope that is not narrower is not built', BL.narrow_bounds() is None)
finally:
    BL.BK_COURT_IDS = saved_ids
    BL.FL_BK_COURT_IDS = saved_fl
    os.environ.pop('BK_FILED_AFTER_YEARS', None)
check('a phone lead may follow more pages than a lead with no channel',
      BL.party_page_cap({'phone': True}) == BL.PRIORITY_PAGE_CAP
      and BL.party_page_cap({}) == BL.PARTY_PAGE_CAP
      and BL.party_page_cap({}, cli=True) == BL.CLI_PAGE_CAP
      and BL.narrow_page_cap(BL.PARTY_PAGE_CAP) >= BL.NARROW_PAGE_CAP)


FL_COURTS = {'flsb', 'flmb', 'flnb'}


def _live_server(url, headers, n):
    """Behaves like CourtListener: only the last court= value counts, and party: is names.
    A ZIP or city inside party: matches nothing. Repeated court= keys therefore search
    vib alone and come back empty."""
    ids = effective_courts(url)
    q = party_q(url)
    qu = q.upper()
    cur = (q_of(url).get('cursor') or [''])[-1]
    if q and (re.search(r'\d', q) or 'MIAMI' in qu):
        return 200, {}, page([])
    if ids == ['vib']:
        return 200, {}, page([])
    if 'LOPEZ' in qu or cur.startswith('lp'):
        nxt = 'https://www.courtlistener.com/api/rest/v4/search/?type=d&cursor=lp%d' % n
        return 200, {}, page([], nxt)
    if set(ids) == FL_COURTS:
        if 'GARCIA' in qu and 'MARIA' in qu:
            return 200, {}, page([recap('26-15796', ['Garcia, Maria'])])
        return 200, {}, page([])
    if 'flsb' in ids and len(ids) > 3:
        if 'NUNEZ' in qu:
            return 200, {}, page([])
        if 'RIVERA' in qu:
            nxt = 'https://www.courtlistener.com/api/rest/v4/search/?type=d&cursor=riv2'
            return 200, {}, page([recap('15-10001', ['Rivera, Ana'])], nxt)
        nxt = 'https://www.courtlistener.com/api/rest/v4/search/?type=d&cursor=w2'
        return 200, {}, page([recap('11-00001', ['Other, Person'])], nxt)
    if cur == 'w2':
        nxt = 'https://www.courtlistener.com/api/rest/v4/search/?type=d&cursor=w3'
        return 200, {}, page([recap('11-00001', ['Other, Person'])], nxt)
    if cur == 'riv2':
        nxt = 'https://www.courtlistener.com/api/rest/v4/search/?type=d&cursor=riv3'
        return 200, {}, page([recap('11-00001', ['Other, Person'])], nxt)
    return 200, {}, page([])


def _named_calls(calls):
    """Party-search URLs (they carry q=). Cursor follow-ups do not."""
    return [u for u, _h in calls if party_q(u)]


print('-- narrow after overflow')
d = isolate('narrow')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [
    {'case': '2025-000201-CA-01', 'county': 'MIAMI-DADE', 'owners': 'GARCIA, MARIA',
     'addr': '10 EAST ST, MIAMI, FL 33101', 'st': 'OK', 'days': 900},
    {'case': 'CACE-99-555890', 'county': 'BROWARD', 'owners': 'NUNEZ, JOSE', 'st': 'OK', 'days': 900},
])
stub = Stub(_live_server)
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('2025-000201-CA-01', here=str(leads), transport=stub, clock=clock_at(NOW))
opened = _named_calls(stub.calls)
wide = [u for u in opened if set(effective_courts(u)) != FL_COURTS]
narrow = [u for u in opened if set(effective_courts(u)) == FL_COURTS]
narrow_q = party_q(narrow[0]) if narrow else ''
check('a common name that overflows is held by the narrow search, not left without the case',
      br.get('verdict') in ('possible', 'active') and '26-15796' in (br.get('why') or '')
      and 'truncated' not in (br.get('why') or '') and len(narrow) == 1 and len(wide) == 1,
      (br, len(wide), len(narrow), narrow_q))
check('the request has one court value, and the narrow query has no digits or city',
      wide and len(court_params(wide[0])) == 1
      and 'flsb' in effective_courts(wide[0]) and 'flmb' in effective_courts(wide[0])
      and 'flnb' in effective_courts(wide[0]) and len(effective_courts(wide[0])) >= 90
      and narrow and court_params(narrow[0]) == ['flsb flmb flnb']
      and not re.search(r'\d', narrow_q) and 'MIAMI' not in narrow_q.upper() and '"' not in narrow_q,
      (court_params(wide[0])[:1], court_params(narrow[0]) if narrow else None, narrow_q))
check('the narrow query is not sent until the filtered search overflows',
      stub.calls and set(effective_courts(opened[0])) != FL_COURTS
      and set(effective_courts(opened[-1])) == FL_COURTS)
before = len(stub.calls)
with contextlib.redirect_stdout(io.StringIO()):
    br2 = BL.presend_check('CACE-99-555890', here=str(leads), transport=stub, clock=clock_at(NOW))
added = _named_calls(stub.calls[before:])
check('a filtered search that fits in the page cap does not run the narrow query',
      br2.get('verdict') == 'clear_unconfirmed' and len(added) == 1
      and set(effective_courts(added[0])) != FL_COURTS
      and len(court_params(added[0])) == 1
      and not re.search(r'\d', party_q(added[0])) and '"' not in party_q(added[0]),
      br2)


print('-- wide hit is kept')
d = isolate('keep-wide')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{
    'case': 'CACE-99-555892', 'county': 'BROWARD', 'owners': 'RIVERA, ANA',
    'addr': '10 EAST ST, MIAMI, FL 33101', 'st': 'OK', 'days': 900,
}])
stub = Stub(_live_server)
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('CACE-99-555892', here=str(leads), transport=stub, clock=clock_at(NOW))
check('a match from the first search is kept when the narrow search is empty',
      br.get('verdict') in ('possible', 'active') and '15-10001' in (br.get('why') or '')
      and br.get('verdict') != 'clear',
      br)


print('-- narrow finishes without a match')
d = isolate('narrow-empty')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{
    'case': 'CACE-99-555893', 'county': 'BROWARD', 'owners': 'DOE, JANE',
    'addr': '10 EAST ST, MIAMI, FL 33101', 'st': 'OK', 'days': 900,
}])
stub = Stub(_live_server)
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('CACE-99-555893', here=str(leads), transport=stub, clock=clock_at(NOW))
st = json.loads((d / 'bk_lookup_status.json').read_text(encoding='utf-8'))
check('a finished narrow search does not clear when the eligible search overflowed',
      br.get('verdict') != 'clear' and 'truncated' in (br.get('why') or '')
      and st.get('truncated') == 1 and int(st.get('errors') or 0) == 0,
      (br, st))


print('-- narrow also overflows')
d = isolate('still-trunc')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{
    'case': 'CACE-99-555891', 'county': 'BROWARD', 'owners': 'LOPEZ, MARIA',
    'addr': '10 EAST ST, MIAMI, FL 33101', 'st': 'OK', 'days': 900,
}])
stub = Stub(_live_server)
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('CACE-99-555891', here=str(leads), transport=stub, clock=clock_at(NOW),
                          max_wait=None)
st = json.loads((d / 'bk_lookup_status.json').read_text(encoding='utf-8'))
opened = _named_calls(stub.calls)
narrow = [u for u in opened if set(effective_courts(u)) == FL_COURTS]
narrow_at = next(i for i, (u, _h) in enumerate(stub.calls) if u in narrow)
check('a narrow query that also overflows is truncated, and that is not an error',
      'truncated' in (br.get('why') or '') and st.get('truncated') == 1 and int(st.get('errors') or 0) == 0
      and narrow and not re.search(r'\d', party_q(narrow[0])) and 'MIAMI' not in party_q(narrow[0]).upper()
      and len(stub.calls) - narrow_at >= BL.NARROW_PAGE_CAP,
      (br, st, len(stub.calls) - narrow_at))


# --------------------------------------------------------------------------------------------- clock
print('-- request clock')
ticks = {'t': NOW}


def _wall():
    return ticks['t']


clock = BL.Clock(wall=_wall, sleeper=lambda _s: None)


def _slow_http(url, headers):
    ticks['t'] += 3.5
    return 200, {}, json.dumps(page([])).encode()


budget = BL.Budget([], NOW)
BL.http_get('https://www.courtlistener.com/api/rest/v4/search/?type=d', cl, _slow_http, budget, clock)
check('a request timestamp includes time spent waiting on HTTP',
      budget.hits and abs(budget.hits[-1] - (NOW + 3.5)) < 0.01, budget.hits)
real = BL.Clock()
check('the production clock is the real clock', abs(real.time() - time.time()) < 2)


# --------------------------------------------------------------------------------------- short case
print('-- short case id')
d = isolate('short')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{
    'case': '2025-000201-CA-01', 'county': 'MIAMI-DADE', 'owners': 'GARCIA, MARIA', 'st': 'OK',
}])
stub = Stub(lambda url, headers, n: (200, {}, page([])))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = BL.main(['--case', '2025-000201'], clock=clock_at(NOW), transport=stub, here=str(leads))
text = buf.getvalue()
cache = json.loads((d / 'bk_lead_cache.json').read_text(encoding='utf-8'))
check('a short Miami case id resolves to the -CA-01 lead',
      rc == 0 and '2025-000201-CA-01 flagged=' in text and '2025-000201-CA-01' in cache
      and 'GARCIA' not in text and TOKEN not in text, text)
d = isolate('nolead')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [])
quiet = Stub(lambda url, headers, n: (200, {}, page([])))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = BL.main(['--case', '2099-000199'], clock=clock_at(NOW), transport=quiet, here=str(leads))
check('a case with no lead is reported and does not write the cache',
      rc == 0 and buf.getvalue().strip() == 'no lead for this case'
      and not (d / 'bk_lead_cache.json').exists() and quiet.calls == [],
      buf.getvalue())


# -------------------------------------------------------------------------------- namesake / docket
print('-- namesake and docket date')
owners = BL.owners_from('GARCIA, MARIA', 'last_first')
base = {'parties': ['Garcia, Maria'], 'open': True, 'filed': '2026-03-15'}
fl_hit = BL.cases_from_hits(owners, [dict(base, court_id='flsb', no='26-10001')])
tx_hit = BL.cases_from_hits(owners, [dict(base, court_id='txwb', no='24-10002')])
ev_hit = BL.cases_from_hits(
    owners, [dict(base, court_id='waeb', no='24-10003', parties=['Garcia, Maria', 'BROWARD COUNTY'])],
    county='BROWARD')
check('a Florida exact match stays a hard hold',
      fl_hit and fl_hit[0]['match'] == 'exact' and BL.verdict_of(fl_hit, True, True)[0] == 'active', fl_hit)
check('an out-of-state exact name with no address or county is only possible',
      tx_hit and tx_hit[0]['match'] == 'plausible' and BL.verdict_of(tx_hit, True, True)[0] == 'possible',
      tx_hit)
check('county evidence keeps an out-of-state exact match a hard hold',
      ev_hit and ev_hit[0]['match'] == 'exact' and BL.verdict_of(ev_hit, True, True)[0] == 'active', ev_hit)

BD = '2026-03-15'
d = isolate('docket-miss')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{
    'case': '2099-000201-CA-01', 'county': 'MIAMI-DADE', 'owners': 'GARCIA, MARIA', 'st': 'OK',
}])
(leads / 'sale_history_cache.json').write_text(json.dumps({
    '2099-000201-CA-01': {'a': True, 'bd': BD, 'sl': '', 'b': 1, 'v': 5, 't': 0},
}), encoding='utf-8')


def _window_miss(url, headers, n):
    q = q_of(url)
    if q.get('q'):
        return 200, {}, page([])
    if set(effective_courts(url)) == {'flsb', 'flmb'} and q.get('filed_before'):
        # A next URL must not be followed: one window, one page.
        return 200, {}, page(
            [recap('11-00001', ['Other, Person'], court='flsb', filed=BD)],
            nxt='https://www.courtlistener.com/api/rest/v4/search/?cursor=more')
    return 200, {}, page([])


stub = Stub(_window_miss)
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('2099-000201-CA-01', here=str(leads), transport=stub, clock=clock_at(NOW))
windows = [u for u, _h in stub.calls if not party_q(u) and q_of(u).get('filed_before')]
active_stay = SG.check('2099-000201-CA-01', str(leads / 'sale_history_cache.json'))
check('an active docket stay is still held, and a missed page does not replace that verdict',
      active_stay.get('ok') is False and active_stay.get('code') == SG.STAY_ACTIVE
      and br.get('verdict') != 'docket_bk_unconfirmed'
      and len(windows) == 1
      and len(court_params(windows[0])) == 1
      and set(effective_courts(windows[0])) == {'flsb', 'flmb'}
      and (q_of(windows[0]).get('filed_after') or [''])[0] == BD
      and (q_of(windows[0]).get('filed_before') or [''])[0] == BD,
      (active_stay, br, len(windows), len(stub.calls)))

d = isolate('docket-hit')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{
    'case': '2099-000202-CA-01', 'county': 'MIAMI-DADE', 'owners': 'GARCIA, MARIA', 'st': 'OK',
}])
(leads / 'sale_history_cache.json').write_text(json.dumps({
    '2099-000202-CA-01': {'a': True, 'bd': BD, 'sl': '', 'b': 1, 'v': 5, 't': 0},
}), encoding='utf-8')


def _window_hit(url, headers, n):
    q = q_of(url)
    if q.get('q'):
        return 200, {}, page([])
    filed = (q.get('filed_after') or [''])[0]
    if set(effective_courts(url)) == {'flsb', 'flmb'} and filed == BD:
        return 200, {}, page([recap('26-15796', ['Garcia, Maria'], court='flsb', filed=BD)])
    return 200, {}, page([recap('11-00001', ['Other, Person'], court='flmb', filed=BD)])


stub = Stub(_window_hit)
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('2099-000202-CA-01', here=str(leads), transport=stub, clock=clock_at(NOW))
check('a filing inside the docket window is a Florida hard hold, not left unconfirmed',
      br.get('verdict') == 'active' and '26-15796' in (br.get('why') or '')
      and br.get('verdict') != 'docket_bk_unconfirmed', br)

d = isolate('docket-lifted')
leads = d / 'leads'
leads.mkdir()
write_leads(leads, [{
    'case': '2099-000203-CA-01', 'county': 'MIAMI-DADE', 'owners': 'GARCIA, MARIA', 'st': 'OK',
}])
sh = leads / 'sale_history_cache.json'
sh.write_text(json.dumps({
    '2099-000203-CA-01': {'a': False, 'bd': BD, 'sl': '2026-04-01', 'b': 1, 'v': 5, 't': 0},
}), encoding='utf-8')


def _lifted_clear(url, headers, n):
    return 200, {}, page([])


stub = Stub(_lifted_clear)
with contextlib.redirect_stdout(io.StringIO()):
    br = BL.presend_check('2099-000203-CA-01', here=str(leads), transport=stub, clock=clock_at(NOW))
lifted_windows = [u for u, _h in stub.calls if not party_q(u) and q_of(u).get('filed_before')]
# The search clock is not wall time. The four readers below use a fresh clear.
BL._dump(BL.cache_path(), {
    '2099-000203-CA-01': {
        'verdict': 'clear', 'why': 'no open federal bankruptcy matched this owner',
        'searched': True, 'ok_check': True, 'err': '', 't': time.time(), 'cases': [],
        'src': 'courtlistener',
    },
})
BL._HOLD_MEMO = None
lifted = SG.check('2099-000203-CA-01', str(sh))
lifted_dial = BL.federal_hold('2099-000203-CA-01')
lifted_mail = BL.send_hold('2099-000203-CA-01', here=str(leads))
lifted_flags = BL.flags_for_cases(['2099-000203-CA-01'])
check('a lifted docket stay plus a CourtListener clear stays ok',
      br.get('verdict') == 'clear' and lifted_windows == []
      and lifted.get('ok') is True and lifted.get('code') == SG.CLEAR
      and lifted_dial == (False, '') and lifted_mail == (False, '')
      and '2099-000203-CA-01' not in lifted_flags,
      (br, lifted, lifted_dial, lifted_mail, lifted_flags, len(stub.calls)))
BL._dump(BL.cache_path(), {
    '2099-000203-CA-01': {
        'verdict': 'docket_bk_unconfirmed', 'docket_bk_unconfirmed': True,
        'why': 'Miami docket bankruptcy date was not matched',
        'searched': True, 'ok_check': True, 'err': '', 't': time.time(), 'cases': [],
        'src': 'courtlistener',
    },
})
BL._HOLD_MEMO = None
stale = SG.check('2099-000203-CA-01', str(sh))
stale_dial = BL.federal_hold('2099-000203-CA-01')
stale_mail = BL.send_hold('2099-000203-CA-01', here=str(leads))
stale_flags = BL.flags_for_cases(['2099-000203-CA-01'])
check('a stored unconfirmed date does not hold a lifted Miami stay',
      stale.get('ok') is True and stale_dial == (False, '') and stale_mail == (False, '')
      and '2099-000203-CA-01' not in stale_flags,
      (stale, stale_dial, stale_mail, stale_flags))

# ------------------------------------------------------------------ 2026-09-29: failures leave a record
print('-- a failed nightly run records why and its own counters')
d = isolate('netfail')
leads = d
write_leads(d, [
    {'case': 'CACE-99-556%03d' % i, 'county': 'BROWARD', 'owners': 'GARCIA, MARIA %s' % chr(65 + i),
     'st': 'OK'} for i in range(6)
])


def net_route(url, headers, n):
    if q_of(url).get('court') == ['flsb']:
        return 200, {}, page([recap('26-55501', ['Someone, Else'])])
    raise TimeoutError('timed out reading GARCIA')


stub = Stub(net_route)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    out = BL.run_nightly(here=str(leads), transport=stub, clock=clock_at(time.time()))
st = json.loads((d / 'bk_lookup_status.json').read_text(encoding='utf-8'))
party_calls = [u for u, _h in stub.calls if q_of(u).get('court') != ['flsb']]
check('a network error on a party search does not end the run', out.get('pull_ok') is True, out)
check('the searches stop after the network-error limit',
      out.get('reason') == 'network' and out.get('errors') == BL.NIGHTLY_NET_ERROR_LIMIT
      and len(party_calls) == BL.NIGHTLY_NET_ERROR_LIMIT, (out, len(party_calls)))
check('the status file carries that run, not an older one',
      st.get('reason') == 'network' and st.get('errors') == BL.NIGHTLY_NET_ERROR_LIMIT
      and st.get('requests_used') == len(stub.calls), st)
held = [BL.send_hold('CACE-99-556%03d' % i, here=str(leads))[0] for i in range(6)]
check('every lead the failed searches did not clear stays held', all(held), held)
check('the network error text is not kept', 'GARCIA' not in buf.getvalue()
      and 'GARCIA' not in json.dumps(json.loads((d / 'bk_lead_cache.json').read_text(encoding='utf-8'))
                                     .get('CACE-99-556000', {}).get('why', '')), buf.getvalue())

d = isolate('crash')
leads = d
write_leads(d, [{'case': 'CACE-99-557001', 'county': 'BROWARD', 'owners': 'GARCIA, MARIA', 'st': 'OK'}])
BL.write_status(pull_ok=True, pull_t=NOW - 86400, filings=762, leads_checked=31, errors=0,
                requests_used=84, reason='ok', provider='courtlistener')
_real_match = BL.match_filings_to_leads


def _boom(*_a, **_k):
    raise KeyError('GARCIA, MARIA')


BL.match_filings_to_leads = _boom
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        stub = Stub(lambda url, headers, n: (200, {}, page([recap('26-5560%d' % n, ['Other, Person'])])))
        rc = BL.main([], clock=clock_at(time.time()), transport=stub, here=str(leads))
finally:
    BL.match_filings_to_leads = _real_match
log = buf.getvalue()
st = json.loads((d / 'bk_lookup_status.json').read_text(encoding='utf-8'))
pub = BL.public_status()
check('a crash after the pull still exits 0', rc == 0, rc)
check('the crash log names the stage, the class and the line, not the value',
      'during match' in log and 'KeyError' in log and 'bk_lookup' not in log.split('at ')[0]
      and '_bklookuptest.py:' in log and 'GARCIA' not in log, log)
check('the crash status carries a reason and this run\'s counters',
      st.get('reason') == 'crash:keyerror' and st.get('pull_ok') is False and st.get('errors') == 1
      and st.get('filings') == 1 and st.get('leads_checked') == 0
      and st.get('requests_used') == len(stub.calls) and st.get('requests_used') != 84, st)
check('public status passes the reason through', pub.get('reason') == 'crash:keyerror', pub)
al = PA.bk_lookup_alert(pub, 'now')
check('the bk-lookup alert names the reason',
      al and 'failed (crash:keyerror)' in al.get('text', ''), al)
al = PA.bk_lookup_alert({'readable': True, 'pull_ok': False}, 'now')
check('an alert without a reason keeps the old wording',
      al and al.get('text') == 'Federal bankruptcy pull failed. Non-Miami leads stay held.', al)

print()
print('==== %d FAIL(S) ====' % len(FAILS) if FAILS else '==== all CourtListener bankruptcy checks passed ====')
for f_ in FAILS:
    print('   FAIL', f_)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
