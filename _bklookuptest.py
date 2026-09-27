"""_bklookuptest — CourtListener federal bankruptcy check, stubbed HTTP only.

Run:  python _bklookuptest.py

No network and no real token. Every owner name below is an example. The only phone
is 555-010-5550, and it must never land in the cache or the status file.
"""
import io
import json
import os
import pathlib
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
           'BK_MAX_RUNTIME_S'):
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
check('closed case -> no hold, lead can clear',
      jose['ok'] is True and jose['code'] == SG.CLEAR
      and 'no open federal bankruptcy' in jose['why'], jose)
check('one-of-two surnames -> possible bankruptcy hold',
      juan['ok'] is False and juan['why'] == 'possible bankruptcy: 26-55577', juan)

flags = BL.flags_for_cases(['CACE-99-555801', 'CACE-99-555802', '502026CA005551XXXXMB'])
check('board flags hold the exact and the plausible lead only',
      flags.get('CACE-99-555801', {}).get('hard') is True
      and 'CACE-99-555802' not in flags
      and flags.get('502026CA005551XXXXMB', {}).get('why') == 'possible bankruptcy: 26-55577', flags)

status = json.loads((d / 'bk_lookup_status.json').read_text(encoding='utf-8'))
blob = json.dumps(status)
check('status is counts only',
      status.get('pull_ok') is True and status.get('filings', 0) >= 1
      and status.get('leads_checked', 0) >= 3 and status.get('holds') == 2
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
check('override clears the one named lead', a['ok'] is True and a['code'] == SG.CLEAR, a)
check('the same case still holds the other lead',
      b['ok'] is False and b['why'] == 'possible bankruptcy: 26-55501', b)
check('a second open case on the cleared lead still holds',
      c['ok'] is False and '26-55502' in c['why'], c)
flags = BL.flags_for_cases(['CACE-99-555801', 'CACE-99-555802'])
check('board drops only the overridden lead',
      'CACE-99-555801' not in flags and 'CACE-99-555802' in flags, flags)


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
check('call_rows keeps a Broward lead that has a fresh clear',
      {r.get('c') for r in rows} == {'CACE-99-555801'}, rows)

print()
print('==== %d FAIL(S) ====' % len(FAILS) if FAILS else '==== all CourtListener bankruptcy checks passed ====')
for f_ in FAILS:
    print('   FAIL', f_)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
