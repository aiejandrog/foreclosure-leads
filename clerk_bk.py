#!/usr/bin/env python
"""clerk_bk.py — bankruptcy stay read from the foreclosure case's own clerk docket.

Miami-Dade already reads its civil docket (sale_history.py). Broward and Palm Beach do not.
CourtListener party-name search is not a release for those two counties (clear_unconfirmed
unless DEALFLOW_BK_ALLOW_CL_CLEAR=1). This module is a second source: the state case's own
docket lines (suggestion of bankruptcy, notice of filing bankruptcy, notice of stay, an
order granting relief from stay or lifting the stay, and a stayed or abated case status).

IT DOES NOT CHANGE SENDING UNLESS DEALFLOW_CLERK_BK=1. The flag defaults off. With it off,
nothing here is consulted by the stay gate, Call Mode, letters, or the board. The owner
turns it on only after clerk_bk_accept.py agrees with cases whose bankruptcy is already known.

WHAT EACH COUNTY ALLOWS (checked 2026-09-27).

Broward. The public Case Search (browardclerk.org/Web2/CaseSearchECA) shows civil dockets
to an anonymous user, but the case-number form is behind Cloudflare Turnstile. robots.txt
does not forbid /Web2/CaseSearchECA/; it does forbid the older /Web2/CaseSearch result
path. The website disclaimer forbids commercial use of the site and says the clerk may
suspend excessive automated use. The Florida Standards for Access to Electronic Court
Records allow an automated program only on the indices, and only when that clerk has
authorized it. This module does not scrape Case Search and does not solve a captcha.

The authorized programmatic path is the clerk's Commercial Data API
(https://api.browardclerk.org), documented at Web2/Services/AboutAPI. It requires a
subscribed account, a notarized agreement, and an API key the subscriber creates. Case
detail includes events_and_documents (EventDate, Description, AdditionalText). That is
the only Broward request this module sends: GET case.json and GET events_and_documents.json
for one case number. No party search, no document image. The key is BROWARD_CLERK_API_KEY,
a Windows user environment variable. It is never printed, logged, or written to the cache
or the status file. No key means Broward is not read and, once the flag is on, the lead
stays held.

Palm Beach. eCaseView (appsgp.mypalmbeachclerk.com/eCaseView) lets a guest search by case
number and open Dockets & Documents. Every such page is behind Google reCAPTCHA, which the
clerk describes as a check that the caller is a person and not an automated program.
Registered access is a mailed form plus multi-factor authentication, and the registered-user
agreement forbids using information from the site for commercial purposes. The public terms
limit the site to personal, non-commercial use and forbid storing the site in a retrieval
system without permission. No official docket API was found. This module does not call
eCaseView. A Palm Beach lead has no clerk-docket clear. With the flag on, that lead stays
held no matter what CourtListener said.

VERDICTS from a fully read docket:
  active   an open stay (or the case status still says stayed/abated) — this source holds
  lifted   a bankruptcy stay was opened and an order closed it — this source does not hold
  none     the docket was fully read and no bankruptcy/stay line was found — this source
           does not hold
Anything else holds: error, timeout, partial list, captcha, unparseable body, denied key,
a description-less event, a case number that did not match. A failed read never becomes a
clear, and it does not erase an earlier active stay.

The gate still requires every other check. A clerk "none" or "lifted" does not override a
CourtListener hold, a PACER active hit, or DEALFLOW_BK_ALLOW_CL_CLEAR. Miami-Dade is not
read here.

COUNTY. county_of() recognizes a Broward civil number and a Palm Beach UCN. The gate also
reads the county stored on the lead (bk_lookup.load_leads), memoized on those files'
mtimes. While the flag is on, a lead recorded as Broward or Palm Beach stays held unless
the number is a Broward civil case with a fresh full read of none or lifted. A tax deed,
a bare number, an FMCE or PR-C number, a Palm Beach small-claims number, or a CASE label
is not a clerk-readable civil case. A Miami-Dade lead whose number has no stem keeps the
verdict it already had. If the lead files cannot be read, every case with no Miami-Dade
stem stays held.

CACHE. DEALFLOW_DIR/clerk_bk_cache.json (override DEALFLOW_CLERK_BK_CACHE). Case key, county,
verdict, dates, counts. No party name, no docket text, no API key. Status file
clerk_bk_status.json is counts only.

    python clerk_bk.py                         # Broward reads, when run by hand
    python clerk_bk.py --status                # counts only
    python clerk_bk_accept.py --case CACE-99-000123
    python clerk_bk_accept.py --case 509999CA000123XXXAMB
    python clerk_bk_accept.py --entries docket.json --case CACE-99-000123

The 5:30 refresh calls bk_lookup.py, which calls run_nightly here only when
DEALFLOW_CLERK_BK=1. A failure still exits 0. Exit 0 always.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

ENV_ENABLE = 'DEALFLOW_CLERK_BK'
ENV_KEY = 'BROWARD_CLERK_API_KEY'
ENV_MAX_AGE = 'DEALFLOW_CLERK_BK_MAX_AGE_DAYS'
ENV_MAX_RUNTIME = 'CLERK_BK_MAX_RUNTIME_S'
ENV_MAX_CASES = 'CLERK_BK_MAX_CASES'
ENV_MIN_INTERVAL = 'CLERK_BK_MIN_INTERVAL'

CACHE_NAME = 'clerk_bk_cache.json'
STATUS_NAME = 'clerk_bk_status.json'
API_HOST = 'api.browardclerk.org'
DEFAULT_MAX_AGE_DAYS = 14.0
DEFAULT_MAX_RUNTIME = 900.0
DEFAULT_MAX_CASES = 25
DEFAULT_MIN_INTERVAL = 2.5
RETRY_DAYS = 1.0
MAX_BODY = 2_000_000
HTTP_TIMEOUT = 25.0
RETRY_429 = 3
RETRY_SLEEP = (2.0, 4.0, 8.0)

SRC = 'clerk_docket'
VERDICT_ACTIVE = 'active'
VERDICT_LIFTED = 'lifted'
VERDICT_NONE = 'none'
VERDICT_PARTIAL = 'partial'
VERDICT_ERROR = 'error'
VERDICT_CAPTCHA = 'captcha'
VERDICT_UNPARSEABLE = 'unparseable'
VERDICT_UNAVAILABLE = 'unavailable'
CLEARING = (VERDICT_NONE, VERDICT_LIFTED)

_BR_PREFIX = ('CACE', 'COCE', 'CONO', 'COSO', 'COWE')
_BR_RE = re.compile(r'^(?:CACE|COCE|CONO|COSO|COWE)\d{6,12}$')
_PB_RE = re.compile(r'^50\d{4}(?:CA|CC)[A-Z0-9]{6,12}$')
_CAPTCHA = re.compile(
    r'g-recaptcha|grecaptcha|cf-turnstile|challenges\.cloudflare\.com|h-captcha', re.I)
_STATUS_DONE = re.compile(r'\b(lifted|terminated|vacated|annulled|dismissed|discharged)\b', re.I)
_STATUS_OPEN = re.compile(
    r'^(open|pending|closed|disposed|reopened|active)(\b.*)?$', re.I)
_STATUS_HOLD = re.compile(r'\b(stayed|abated|abatement)\b|\bbankrupt|\bstay\b', re.I)
_NOTICE_STAY = re.compile(r'\bnotice of stay\b', re.I)
_BANKRUPT_WORD = re.compile(r'bankrupt', re.I)
_LIFT_ORDER = re.compile(r'\border\b', re.I)
_LIFT_VERB = re.compile(r'\blift\w*\b', re.I)
_STAY_WORD = re.compile(r'\bstay\b', re.I)
_DENY = re.compile(r'\b(deny|denied|denying|denial)\b', re.I)
_ASK = re.compile(r'\bmotion\b|\brequest\b|\bapplication\b|notice of hearing', re.I)
_GRANT = re.compile(r'\bgrant\w*\b', re.I)

PALM_WHY = ('Palm Beach eCaseView dockets are not available to this check '
            '(captcha and terms). Lead stays held.')
UNREAD_WHY = 'Broward clerk docket has not been read. Lead stays held.'
STALE_WHY = 'Broward clerk docket read is older than %g days. Lead stays held.'
ACTIVE_WHY = ('ACTIVE bankruptcy stay on the clerk docket (filed %s). '
              'Contact stays held.')
UNREADABLE_WHY = 'Clerk docket cache is unreadable. Lead stays held.'
NOT_READABLE_WHY = 'not a clerk-readable civil case. Lead stays held.'
LEAD_LIST_WHY = 'Lead list is unreadable. Lead stays held.'
_RECORDED_HOLD = ('BROWARD', 'PALM BEACH')

_MEMO = None  # (path, mtime_ns, size, data)
_LEADS_HERE = None  # tests point this at a temp directory; production uses the repo
_COUNTY_MEMO = None  # (leads-file sig, ok, {case key: county})
_COUNTY_LOCK = threading.Lock()


def log(msg):
    print(msg, flush=True)


def enabled(env=None):
    """True only when the owner has turned the clerk-docket release on."""
    env = os.environ if env is None else env
    return str(env.get(ENV_ENABLE) or '').strip() == '1'


def api_key(env=None):
    env = os.environ if env is None else env
    return str(env.get(ENV_KEY) or '').strip()


def scrub(text, key):
    s = str(text or '')
    if key:
        s = s.replace(key, '[redacted]')
        quoted = urllib.parse.quote(key, safe='')
        if quoted and quoted != key:
            s = s.replace(quoted, '[redacted]')
    return s[:180]


def max_age_days(env=None):
    env = os.environ if env is None else env
    raw = str(env.get(ENV_MAX_AGE) or '').strip()
    try:
        v = float(raw) if raw else DEFAULT_MAX_AGE_DAYS
    except ValueError:
        return 0.0
    return v if v > 0 else 0.0


def max_runtime_s(env=None):
    env = os.environ if env is None else env
    raw = str(env.get(ENV_MAX_RUNTIME) or '').strip()
    try:
        v = float(raw) if raw else DEFAULT_MAX_RUNTIME
    except ValueError:
        return DEFAULT_MAX_RUNTIME
    return v if v > 0 else DEFAULT_MAX_RUNTIME


def max_cases(env=None):
    env = os.environ if env is None else env
    raw = str(env.get(ENV_MAX_CASES) or '').strip()
    try:
        v = int(raw) if raw else DEFAULT_MAX_CASES
    except ValueError:
        return DEFAULT_MAX_CASES
    return v if v > 0 else DEFAULT_MAX_CASES


def min_interval(env=None):
    env = os.environ if env is None else env
    raw = str(env.get(ENV_MIN_INTERVAL) or '').strip()
    try:
        v = float(raw) if raw else DEFAULT_MIN_INTERVAL
    except ValueError:
        return DEFAULT_MIN_INTERVAL
    return v if v > 0 else DEFAULT_MIN_INTERVAL


def _norm(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def county_of(case):
    """'broward', 'palmbeach', or '' (Miami-Dade and anything this check does not cover)."""
    try:
        import stay_gate
        if stay_gate.case_stem(case):
            return ''
        key = stay_gate.pacer_key(case)
    except Exception:
        key = _norm(case)
    compact = _norm(key)
    if _BR_RE.match(compact):
        return 'broward'
    if _PB_RE.match(compact):
        return 'palmbeach'
    return ''


def case_key(case):
    try:
        import stay_gate
        return stay_gate.pacer_key(case)
    except Exception:
        return ''


def broward_api_case(case):
    """Compact case number the Broward API examples use (CACE09006578), or ''."""
    compact = _norm(case)
    return compact if _BR_RE.match(compact) else ''


def state_dir():
    d = str(os.environ.get('DEALFLOW_DIR') or '').strip()
    if not d:
        import paths as P
        d = P.DEALFLOW_DIR
    os.makedirs(d, exist_ok=True)
    return d


def _path(name, env_key):
    override = str(os.environ.get(env_key) or '').strip()
    if override:
        parent = os.path.dirname(override)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return override
    return os.path.join(state_dir(), name)


def cache_path():
    return _path(CACHE_NAME, 'DEALFLOW_CLERK_BK_CACHE')


def status_path():
    return _path(STATUS_NAME, 'DEALFLOW_CLERK_BK_STATUS')


def cache_sig():
    path = cache_path()
    try:
        st = os.stat(path)
        return (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return (path, None, None)


def _load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f), True, ''
    except FileNotFoundError:
        return None, False, ''
    except Exception as e:
        return None, True, str(e)[:80]


def load_cache():
    """(data, exists, err). err is set when the file is there and cannot be used."""
    global _MEMO
    path = cache_path()
    try:
        st = os.stat(path)
    except FileNotFoundError:
        _MEMO = None
        return {}, False, ''
    except OSError as e:
        _MEMO = None
        return None, True, str(e.strerror or e)[:80]
    if _MEMO and _MEMO[0] == path and _MEMO[1] == st.st_mtime_ns and _MEMO[2] == st.st_size:
        return _MEMO[3], True, ''
    data, exists, err = _load_json(path)
    if err or not isinstance(data, dict):
        _MEMO = None
        return None, True, err or 'not an object'
    _MEMO = (path, st.st_mtime_ns, st.st_size, data)
    return data, True, ''


def save_cache(data):
    global _MEMO
    path = cache_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(tmp, path)
    _MEMO = None


# --------------------------------------------------------------------------------------------------
# docket classification. The ordered-vs-motion rules are sale_history.py's, which Miami already
# runs. Two extra fail-closed readings are applied before that: a bare "Notice of Stay" is a
# stay, and an order lifting the stay (not a motion, not a denial) is stay relief.
# --------------------------------------------------------------------------------------------------
def _us_date(value):
    s = str(value or '').strip()
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return '%d/%d/%s' % (int(m.group(2)), int(m.group(3)), m.group(1))
    return s


def _status_holds(status):
    s = ' '.join(str(status or '').split())
    if not s:
        return False
    if _STATUS_DONE.search(s) and not re.search(r'\b(stayed|abated)\b', s, re.I):
        return False
    if _STATUS_OPEN.match(s):
        return bool(re.search(r'bankrupt|\bstay|\babat', s, re.I))
    return bool(_STATUS_HOLD.search(s))


def _prepare(text):
    tx = ' '.join(str(text or '').split())
    if _NOTICE_STAY.search(tx) and not _BANKRUPT_WORD.search(tx):
        tx = tx + ' Notice of Bankruptcy Stay'
    if _LIFT_ORDER.search(tx) and _LIFT_VERB.search(tx) and _STAY_WORD.search(tx):
        asked = bool(_ASK.search(tx)) and not (_GRANT.search(tx) and _LIFT_ORDER.search(tx))
        if not _DENY.search(tx) and not asked and not re.search(r'automatic stay', tx, re.I):
            tx = tx + ' lifting automatic stay'
    return tx


def classify(entries, status=''):
    """Verdict for one fully read docket. entries are {date, text}. Nothing here is stored."""
    dks = []
    for e in entries or []:
        if not isinstance(e, dict):
            return {'verdict': VERDICT_UNPARSEABLE, 'bd': '', 'sl': '', 'bk_lines': 0,
                    'status_hold': False, 'err': 'docket event was not an object'}
        text = e.get('text')
        if text is None:
            text = e.get('description') or e.get('docketDescrition') or e.get('docketDescription')
        if not str(text or '').strip():
            return {'verdict': VERDICT_PARTIAL, 'bd': '', 'sl': '', 'bk_lines': 0,
                    'status_hold': False, 'err': 'docket event had no description'}
        dks.append({'docketDescrition': _prepare(text), 'comments': '',
                    'eventDate': _us_date(e.get('date') or e.get('eventDate') or '')})
    import sale_history as SH
    active, latest, lifted = SH._bk_stay(dks)
    opens, closes = SH._bk_lines(dks)
    n = len(opens) + len(closes)
    if _status_holds(status):
        return {'verdict': VERDICT_ACTIVE, 'bd': latest or '', 'sl': '', 'bk_lines': n,
                'status_hold': True, 'err': ''}
    if active:
        return {'verdict': VERDICT_ACTIVE, 'bd': latest or '', 'sl': '', 'bk_lines': n,
                'status_hold': False, 'err': ''}
    if lifted:
        return {'verdict': VERDICT_LIFTED, 'bd': latest or '', 'sl': lifted, 'bk_lines': n,
                'status_hold': False, 'err': ''}
    return {'verdict': VERDICT_NONE, 'bd': '', 'sl': '', 'bk_lines': n,
            'status_hold': False, 'err': ''}


def _event_text(ev):
    desc = ev.get('Description')
    if desc is None:
        desc = ev.get('description')
    notes = ev.get('AdditionalText')
    if notes is None:
        notes = ev.get('additional_text') or ''
    names = []
    docs = ev.get('EventDocumentList') or ev.get('event_document_list') or []
    if isinstance(docs, list):
        for d in docs:
            if isinstance(d, dict) and str(d.get('Name') or '').strip():
                names.append(str(d.get('Name')))
    parts = [str(desc or '').strip(), str(notes or '').strip()] + names
    return ' '.join(p for p in parts if p)


def parse_case_payload(payload, expect):
    """(status, err) from case.json. status is the disposition label, or ''."""
    if not isinstance(payload, dict):
        return '', 'case summary was not an object'
    got = _norm(payload.get('Case_Number') or payload.get('case_number'))
    if got != expect:
        return '', 'case summary did not match the requested case'
    status = payload.get('Disposition_Status')
    if status is None:
        status = payload.get('disposition_status') or ''
    code = payload.get('Disposition_Code') or payload.get('disposition_code') or ''
    label = ' '.join(str(status or '').split())
    code_s = ' '.join(str(code or '').split())
    if code_s and code_s.lower() not in label.lower():
        label = (label + ' ' + code_s).strip()
    return label[:80], ''


def parse_events_payload(payload, expect):
    """(entries, err). entries is None when the docket is not a complete read."""
    if not isinstance(payload, dict):
        return None, 'docket was not an object'
    got = _norm(payload.get('Case_Number') or payload.get('case_number'))
    if got != expect:
        return None, 'docket did not match the requested case'
    events = payload.get('EventList')
    if events is None:
        events = payload.get('event_list')
    if not isinstance(events, list):
        return None, 'docket event list was missing'
    for k in ('TotalCount', 'Total_Count', 'total', 'TotalEvents', 'total_count'):
        if k in payload and payload.get(k) is not None:
            try:
                reported = int(payload.get(k))
            except (TypeError, ValueError):
                return None, 'docket total was not a number'
            if reported != len(events):
                return None, 'docket event list was shorter than the reported total'
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            return None, 'docket event was not an object'
        text = _event_text(ev)
        if not text:
            return None, 'docket event had no description'
        out.append({'date': ev.get('EventDate') or ev.get('event_date') or '', 'text': text})
    return out, ''


# --------------------------------------------------------------------------------------------------
# Broward API. One case, two GETs. Palm Beach is not requested.
# --------------------------------------------------------------------------------------------------
def allowed_url(url, api_case):
    p = urllib.parse.urlparse(url)
    if p.scheme != 'https' or p.netloc != API_HOST or p.username or p.password:
        return False
    base = '/api/case/%s/' % api_case
    return p.path in (base + 'case.json', base + 'events_and_documents.json')


def _api_url(api_case, kind, key):
    path = 'case.json' if kind == 'case' else 'events_and_documents.json'
    return 'https://%s/api/case/%s/%s?auth_key=%s' % (
        API_HOST, api_case, path, urllib.parse.quote(key, safe=''))


class _SameHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).netloc != API_HOST:
            raise urllib.error.HTTPError(req.full_url, code, 'redirect refused', headers, fp)
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def urllib_transport(url, timeout):
    opener = urllib.request.build_opener(_SameHost)
    req = urllib.request.Request(url, headers={'Accept': 'application/json',
                                               'User-Agent': 'DealFlow-clerk-bk'})
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(MAX_BODY + 1)
            return getattr(resp, 'status', 200), body
    except urllib.error.HTTPError as e:
        raw = e.read(MAX_BODY + 1) if e.fp else b''
        return e.code, raw


def _as_text(body):
    if isinstance(body, bytes):
        return body.decode('utf-8', 'replace')
    return str(body or '')


def _body_problem(status, body):
    if status == 429:
        return 'rate_limited'
    if status in (401, 403):
        return 'denied'
    if status != 200:
        return 'http_%s' % (status or 0)
    if len(body) > MAX_BODY if isinstance(body, (bytes, str)) else False:
        return 'truncated'
    text = _as_text(body)
    if _CAPTCHA.search(text):
        return 'captcha'
    if text.lstrip().startswith('<'):
        return 'not_json'
    return ''


def _problem_verdict(problem):
    if problem == 'captcha':
        return VERDICT_CAPTCHA, 'clerk answered with a captcha. Lead stays held.'
    if problem == 'denied':
        return VERDICT_UNAVAILABLE, 'Broward clerk API refused the key. Lead stays held.'
    if problem == 'truncated':
        return VERDICT_PARTIAL, 'clerk docket response was cut off. Lead stays held.'
    if problem == 'not_json':
        return VERDICT_UNPARSEABLE, 'clerk docket was not JSON. Lead stays held.'
    if problem == 'rate_limited':
        return VERDICT_ERROR, 'Broward clerk API rate limited the read. Lead stays held.'
    return VERDICT_ERROR, 'clerk docket read failed. Lead stays held.'


class Clock:
    def __init__(self, now=None, sleeper=None):
        self._now = now
        self.sleeper = sleeper or time.sleep

    def time(self):
        return time.time() if self._now is None else self._now

    def sleep(self, seconds):
        seconds = max(0.0, float(seconds))
        if self._now is not None:
            self._now += seconds
        if seconds:
            self.sleeper(seconds)


def _get_json(url, key, transport, clock, timeout, deadline):
    """(status, payload_or_None, problem). Retries 429/503 a bounded number of times."""
    problem = ''
    status, body = 0, b''
    for attempt in range(RETRY_429 + 1):
        if deadline is not None and clock.time() >= deadline:
            return 0, None, 'time_budget'
        try:
            status, body = transport(url, timeout)
        except Exception as e:
            return 0, None, scrub(e, key) or 'transport_error'
        if isinstance(body, str):
            body = body.encode('utf-8')
        problem = _body_problem(status, body)
        if problem == 'rate_limited' or status in (503, 502):
            if attempt >= RETRY_429:
                break
            delay = RETRY_SLEEP[min(attempt, len(RETRY_SLEEP) - 1)]
            if deadline is not None and clock.time() + delay > deadline:
                return status, None, 'time_budget'
            clock.sleep(delay)
            continue
        break
    if problem:
        return status, None, problem
    try:
        payload = json.loads(_as_text(body))
    except Exception:
        return status, None, 'not_json'
    return status, payload, ''


def read_broward_case(case, key, transport, clock, deadline=None, gap=None):
    """One Broward case. Returns a cache entry dict. Does not store names or the key."""
    api_case = broward_api_case(case)
    if not api_case:
        return _entry('broward', VERDICT_UNPARSEABLE, False, err='not a Broward civil case number',
                      now=clock.time())
    if not key:
        return _entry('broward', VERDICT_UNAVAILABLE, False, err='Broward clerk API key is not set',
                      now=clock.time())
    gap = DEFAULT_MIN_INTERVAL if gap is None else gap

    def _pace():
        last = getattr(clock, 'last_http', None)
        if last is None or gap <= 0:
            return True
        wait = gap - (clock.time() - last)
        if wait > 0:
            if deadline is not None and clock.time() + wait > deadline:
                return False
            clock.sleep(wait)
        return True

    status_label = ''
    n_http = 0
    for kind in ('case', 'events'):
        if not _pace():
            return _entry('broward', VERDICT_ERROR, False, err='time budget before the docket read',
                          now=clock.time())
        url = _api_url(api_case, kind, key)
        if not allowed_url(url, api_case):
            return _entry('broward', VERDICT_ERROR, False, err='refused a request outside the case API',
                          now=clock.time())
        status, payload, problem = _get_json(url, key, transport, clock, HTTP_TIMEOUT, deadline)
        clock.last_http = clock.time()
        n_http += 1
        if problem == 'time_budget':
            return _entry('broward', VERDICT_ERROR, False, err='time budget during the docket read',
                          now=clock.time())
        if problem or payload is None:
            verdict, why = _problem_verdict(problem or 'error')
            return _entry('broward', verdict, False, err=scrub(why, key), now=clock.time(),
                          requests=n_http)
        if kind == 'case':
            status_label, err = parse_case_payload(payload, api_case)
            if err:
                return _entry('broward', VERDICT_ERROR, False, err=err, now=clock.time(),
                              requests=n_http)
        else:
            entries, err = parse_events_payload(payload, api_case)
            if err or entries is None:
                kind_v = VERDICT_PARTIAL if err and 'shorter' in err else VERDICT_UNPARSEABLE
                if err and 'no description' in err:
                    kind_v = VERDICT_PARTIAL
                return _entry('broward', kind_v, False, err=err or 'docket unreadable',
                              now=clock.time(), requests=n_http)
            found = classify(entries, status_label)
            if found.get('verdict') not in (VERDICT_ACTIVE, VERDICT_LIFTED, VERDICT_NONE):
                return _entry('broward', found.get('verdict') or VERDICT_UNPARSEABLE, False,
                              err=found.get('err') or 'docket unreadable', now=clock.time(),
                              requests=n_http)
            return _entry('broward', found['verdict'], True, bd=found.get('bd') or '',
                          sl=found.get('sl') or '', events=len(entries),
                          bk_lines=found.get('bk_lines') or 0,
                          status_hold=bool(found.get('status_hold')), now=clock.time(),
                          requests=n_http)
    return _entry('broward', VERDICT_ERROR, False, err='docket read did not finish',
                  now=clock.time(), requests=n_http)


def _entry(county, verdict, ok_read, bd='', sl='', events=0, bk_lines=0, status_hold=False,
           err='', now=0.0, requests=0):
    return {
        'county': county,
        'verdict': verdict,
        'ok_read': bool(ok_read),
        'bd': str(bd or '')[:10],
        'sl': str(sl or '')[:10],
        'events': int(events or 0),
        'bk_lines': int(bk_lines or 0),
        'status_hold': bool(status_hold),
        't': round(float(now or 0), 1),
        'src': 'broward_api' if county == 'broward' else 'none',
        'err': scrub(err, '')[:160],
        'requests': int(requests or 0),
    }


def _age_days(ent, field, now):
    try:
        return (float(now) - float(ent.get(field) or 0)) / 86400.0
    except (TypeError, ValueError):
        return None


def needs_fetch(ent, now, env=None):
    """True when this cache row should be read again. A fresh full read waits out the max age.
    A failed read waits a day. An active stay is kept, and re-read on that same max age."""
    if not isinstance(ent, dict):
        return True
    limit = max_age_days(env)
    if ent.get('ok_read') and ent.get('verdict') in (VERDICT_NONE, VERDICT_LIFTED, VERDICT_ACTIVE):
        age = _age_days(ent, 't', now)
        due = age is None or limit <= 0 or age < -1 or age > limit
        if not due:
            return False
        tried = _age_days(ent, 'tried', now) if ent.get('err') else None
        if tried is not None and 0 <= tried < RETRY_DAYS:
            return False
        return True
    age = _age_days(ent, 't', now)
    return age is None or age >= RETRY_DAYS


def store_read(cache, key, entry, now):
    """Write `entry`. A failed re-read does not wipe an earlier active stay."""
    entry = dict(entry)
    entry.pop('requests', None)
    prev = cache.get(key) if isinstance(cache.get(key), dict) else None
    if (not entry.get('ok_read') and isinstance(prev, dict) and prev.get('ok_read')
            and prev.get('verdict') == VERDICT_ACTIVE):
        kept = dict(prev)
        kept['err'] = str(entry.get('err') or '')[:160]
        kept['tried'] = round(float(now), 1)
        cache[key] = kept
        return kept
    cache[key] = entry
    return entry


def _fresh_clear(ent, now, env=None):
    if not isinstance(ent, dict) or not ent.get('ok_read'):
        return False
    if ent.get('verdict') not in CLEARING:
        return False
    if ent.get('status_hold'):
        return False
    age = _age_days(ent, 't', now)
    limit = max_age_days(env)
    return age is not None and limit > 0 and -1 <= age <= limit


def leads_root():
    """Directory load_leads reads. Tests set _LEADS_HERE; production is the repo."""
    if _LEADS_HERE:
        return _LEADS_HERE
    import bk_lookup
    return bk_lookup.HERE


def _norm_county(value):
    return ' '.join(str(value or '').upper().split())


def _county_file_sig(here):
    """(path, mtime_ns, size) per lead file, or None when a directory cannot be stated.

    A missing file is (path, None, None). That is a readable empty contribution, not a failure."""
    import bk_lookup
    try:
        paths = bk_lookup.lead_paths(here)
    except Exception:
        return None
    sig = []
    for path in paths:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            sig.append((path, None, None))
            continue
        except OSError:
            return None
        sig.append((path, st.st_mtime_ns, st.st_size))
    return tuple(sig)


def county_sig(here=None):
    """Stable signature of the lead files. ('unreadable',) when they cannot be stated."""
    sig = _county_file_sig(here or leads_root())
    if sig is None:
        return ('unreadable',)
    return sig


def _hold(why):
    return {'blocks': True, 'ok': False, 'code': 'stay_unverified', 'bd': '',
            'why': why, 'src': SRC}


def _has_stem(case):
    try:
        import stay_gate
        return bool(stay_gate.case_stem(case))
    except Exception:
        return False


def _build_counties(here, sig):
    """({case key: county}, ok) from one already-stated signature."""
    if all(slot[1] is None for slot in sig):
        return {}, True
    for path, mtime, _size in sig:
        if mtime is None:
            continue
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return {}, False
        if not isinstance(data, list):
            return {}, False
    try:
        import bk_lookup
        leads = bk_lookup.load_leads(here)
    except Exception:
        return {}, False
    if not isinstance(leads, dict):
        return {}, False
    mapping = {}
    for key, ld in leads.items():
        if isinstance(ld, dict):
            mapping[str(key)] = _norm_county(ld.get('county'))
    return mapping, True


def county_index(here=None):
    """({case key: county}, ok). ok is False when a lead file exists and cannot be read.

    Memoized on lead_paths' mtimes and sizes. Missing files are an empty map, which is
    readable. Never raises."""
    global _COUNTY_MEMO
    try:
        here = here or leads_root()
        sig = _county_file_sig(here)
    except Exception:
        return {}, False
    if sig is None:
        return {}, False
    with _COUNTY_LOCK:
        memo = _COUNTY_MEMO
    if memo and memo[0] == sig:
        return memo[2], memo[1]
    try:
        mapping, ok = _build_counties(here, sig)
    except Exception:
        mapping, ok = {}, False
    with _COUNTY_LOCK:
        current = _COUNTY_MEMO
        if current and current[0] == sig:
            return current[2], current[1]
        _COUNTY_MEMO = (sig, ok, mapping)
    return mapping, ok


def _opinion_readable(case, county, now, env):
    """Opinion for a number county_of already called broward or palmbeach."""
    key = case_key(case)
    if not key:
        return _hold('case number cannot be keyed to a clerk docket. Lead stays held.')
    data, exists, err = load_cache()
    if err or data is None:
        return _hold(UNREADABLE_WHY)
    ent = data.get(key) if isinstance(data, dict) else None
    if _fresh_clear(ent, now, env):
        return {'blocks': False, 'ok': True, 'code': 'clear', 'bd': '', 'sl': ent.get('sl') or '',
                'why': 'clerk docket fully read, no active stay', 'src': SRC}
    if isinstance(ent, dict) and ent.get('ok_read') and ent.get('verdict') == VERDICT_ACTIVE:
        when = ent.get('bd') or 'date unknown'
        return {'blocks': True, 'ok': False, 'code': 'stay_active', 'bd': str(ent.get('bd') or ''),
                'why': ACTIVE_WHY % when, 'src': SRC}
    if county == 'palmbeach' and not isinstance(ent, dict):
        why = PALM_WHY
    elif isinstance(ent, dict) and ent.get('ok_read') and ent.get('verdict') in CLEARING:
        why = STALE_WHY % (max_age_days(env) or 0)
    elif isinstance(ent, dict) and ent.get('err'):
        why = str(ent.get('err'))[:160]
    elif county == 'palmbeach':
        why = PALM_WHY
    else:
        why = UNREAD_WHY
    return _hold(why)


def gate_opinion(case, now=None, env=None):
    """stay_gate / bk_lookup view. None when the flag is off, this is a Miami-Dade stem,
    or the lead is not recorded as Broward or Palm Beach.

    A Broward civil number can clear on a fresh full read of none or lifted. Any other
    lead recorded as Broward or Palm Beach blocks. If the lead list cannot be read, every
    case without a Miami-Dade stem blocks. An active stay blocks even when the read is
    old. Never raises."""
    env = os.environ if env is None else env
    if not enabled(env):
        return None
    try:
        if _has_stem(case):
            return None
        now = time.time() if now is None else now
        try:
            counties, ok = county_index()
        except Exception:
            counties, ok = {}, False
        county = county_of(case)
        known = county in ('broward', 'palmbeach')
        op = _opinion_readable(case, county, now, env) if known else None
        if not ok:
            if isinstance(op, dict) and op.get('blocks'):
                return op
            return _hold(LEAD_LIST_WHY)
        if known:
            return op
        recorded = _norm_county(counties.get(case_key(case)))
        if recorded in _RECORDED_HOLD:
            return _hold(NOT_READABLE_WHY)
        return None
    except Exception:
        if _has_stem(case):
            return None
        return _hold('clerk docket check failed. Lead stays held.')


def health_counts(now=None):
    now = time.time() if now is None else now
    data, exists, err = load_cache()
    if err or data is None:
        return {'ok': False, 'cases': 0, 'holds': 0, 'clear': 0, 'enabled': enabled()}
    if not exists:
        return {'ok': True, 'cases': 0, 'holds': 0, 'clear': 0, 'enabled': enabled()}
    holds = clear = 0
    for k, ent in data.items():
        if str(k).startswith('_') or not isinstance(ent, dict):
            continue
        if ent.get('ok_read') and ent.get('verdict') == VERDICT_ACTIVE:
            holds += 1
        elif _fresh_clear(ent, now):
            clear += 1
        elif ent.get('verdict') and ent.get('verdict') not in CLEARING:
            holds += 1
    return {'ok': True, 'cases': holds + clear, 'holds': holds, 'clear': clear, 'enabled': enabled()}


def _stamp():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def write_status(**fields):
    cur, exists, err = _load_json(status_path())
    if not isinstance(cur, dict):
        cur = {}
    allowed = ('pull_ok', 'pull_t', 'pull_ts', 'leads_checked', 'holds', 'clears', 'errors',
               'skipped_palm', 'requests_used', 'reason', 'enabled')
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ('reason',):
            v = re.sub(r'[^a-z0-9_:-]', '', str(v or '').lower())[:40]
        elif k == 'pull_ts':
            v = str(v)[:40]
        elif k in ('pull_ok', 'enabled'):
            v = bool(v)
        elif k == 'pull_t':
            try:
                v = round(float(v), 1)
            except (TypeError, ValueError):
                continue
        else:
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
        cur[k] = v
    path = status_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cur, f, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(tmp, path)


def public_status():
    data, exists, err = _load_json(status_path())
    if not exists or err or not isinstance(data, dict):
        return {'readable': False}
    try:
        pull_t = float(data.get('pull_t') or 0)
    except (TypeError, ValueError):
        pull_t = 0.0
    age_h = round((time.time() - pull_t) / 3600.0, 2) if pull_t > 0 else None

    def num(k):
        try:
            return int(data.get(k) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        'readable': True,
        'pull_ok': bool(data.get('pull_ok')),
        'pull_age_h': age_h,
        'leads_checked': num('leads_checked'),
        'holds': num('holds'),
        'clears': num('clears'),
        'errors': num('errors'),
        'skipped_palm': num('skipped_palm'),
        'requests_used': num('requests_used'),
        'reason': re.sub(r'[^a-z0-9_:-]', '', str(data.get('reason') or '').lower())[:40],
        'enabled': bool(data.get('enabled')),
    }


def _broward_queue(here):
    import bk_lookup
    leads = bk_lookup.load_leads(here)
    broward, palm = [], 0
    for ld in leads.values():
        which = county_of(ld.get('case'))
        if which == 'broward':
            broward.append(ld)
        elif which == 'palmbeach':
            palm += 1
    broward.sort(key=bk_lookup.contact_rank)
    return broward, palm


def run_nightly(here=HERE, env=None, transport=None, clock=None, force_cases=None):
    """Read Broward dockets that are due. Palm Beach is counted and not requested.

    Always returns a dict. Writes a status file. Does not raise on a clerk failure."""
    env = os.environ if env is None else env
    clock = clock or Clock()
    transport = transport or urllib_transport
    deadline = clock.time() + max_runtime_s(env)
    key = api_key(env)
    gap = min_interval(env)
    checked = errors = requests = holds = clears = 0
    reason = 'ok'
    pull_ok = True
    try:
        data, _exists, err = load_cache()
        if err or data is None:
            write_status(pull_ok=False, pull_t=clock.time(), pull_ts=_stamp(), reason='cache_unreadable',
                         enabled=enabled(env), errors=1)
            log('Clerk docket: cache is unreadable. Leads stay held while the check is on.')
            return {'pull_ok': False, 'checked': 0, 'holds': 0, 'clears': 0, 'errors': 1,
                    'reason': 'cache_unreadable'}
        cache = data if isinstance(data, dict) else {}
        if force_cases is not None:
            queue = [{'key': case_key(c), 'case': c} for c in force_cases if county_of(c) == 'broward']
            palm = sum(1 for c in force_cases if county_of(c) == 'palmbeach')
        else:
            queue, palm = _broward_queue(here)
        if not key:
            reason = 'key_missing'
            pull_ok = False
            log('Clerk docket: BROWARD_CLERK_API_KEY is not set. Broward dockets were not read.')
        else:
            cap = len(queue) if force_cases is not None else max_cases(env)
            due_left = False
            for ld in queue:
                if clock.time() >= deadline:
                    reason = 'time_budget'
                    pull_ok = False
                    log('Clerk docket: time budget reached. Progress saved.')
                    break
                ck = ld.get('key') or case_key(ld.get('case'))
                if not ck:
                    continue
                if force_cases is None and not needs_fetch(cache.get(ck), clock.time(), env):
                    continue
                if checked >= cap:
                    due_left = True
                    break
                entry = read_broward_case(ld.get('case'), key, transport, clock, deadline=deadline,
                                          gap=gap)
                requests += int(entry.get('requests') or 0)
                entry.pop('requests', None)
                if not entry.get('ok_read'):
                    errors += 1
                    if entry.get('verdict') == VERDICT_ERROR and 'time budget' in str(entry.get('err') or ''):
                        reason = 'time_budget'
                        pull_ok = False
                        store_read(cache, ck, entry, clock.time())
                        save_cache(cache)
                        break
                store_read(cache, ck, entry, clock.time())
                save_cache(cache)
                checked += 1
            if due_left:
                reason = 'case_cap'
                pull_ok = False
                log('Clerk docket: case cap reached. The rest stay unread until the next run.')
        for ent in cache.values():
            if not isinstance(ent, dict):
                continue
            if ent.get('ok_read') and ent.get('verdict') == VERDICT_ACTIVE:
                holds += 1
            elif _fresh_clear(ent, clock.time(), env):
                clears += 1
        write_status(pull_ok=pull_ok, pull_t=clock.time(), pull_ts=_stamp(),
                     leads_checked=checked, holds=holds, clears=clears, errors=errors,
                     skipped_palm=palm, requests_used=requests,
                     reason=reason, enabled=enabled(env))
        log('Clerk docket: checked=%d holds=%d clears=%d errors=%d palm_unread=%d requests=%d' % (
            checked, holds, clears, errors, palm, requests))
    except Exception as e:
        pull_ok = False
        reason = 'failed'
        try:
            write_status(pull_ok=False, pull_t=clock.time(), pull_ts=_stamp(), reason=reason,
                         enabled=enabled(env), errors=errors + 1)
        except Exception:
            pass
        log('Clerk docket: run failed (%s). Leads stay held while the check is on.' % scrub(e, key)[:80])
    return {'pull_ok': pull_ok, 'checked': checked, 'holds': holds, 'clears': clears,
            'errors': errors, 'reason': reason}


def format_report(case, county, verdict, why, events=0, bk_lines=0, src=''):
    """One public line. Counts and a short reason. No docket text and no names."""
    why_s = ' '.join(str(why or '').split())
    why_s = re.sub(r'\s+', ' ', why_s)[:160]
    return 'case=%s county=%s verdict=%s events=%s bk_lines=%s src=%s why=%s' % (
        str(case).strip()[:40], county or '-', verdict or '-', int(events or 0),
        int(bk_lines or 0), src or '-', why_s)


def accept_case(case, entries_path='', env=None, transport=None, clock=None, write=True):
    """Report one case. A live Broward read is stored when `write` is true. Palm Beach is not fetched."""
    env = os.environ if env is None else env
    clock = clock or Clock()
    county = county_of(case) or 'other'
    if entries_path:
        try:
            with open(entries_path, encoding='utf-8') as f:
                raw = json.load(f)
        except Exception:
            return format_report(case, county, VERDICT_UNPARSEABLE,
                                 'entries file could not be read', src='file')
        if isinstance(raw, list):
            rows, status = raw, ''
        elif isinstance(raw, dict):
            rows, status = raw.get('entries') or [], raw.get('status') or ''
        else:
            return format_report(case, county, VERDICT_UNPARSEABLE,
                                 'entries file was not a docket', src='file')
        norm = []
        for e in rows:
            if not isinstance(e, dict):
                return format_report(case, county, VERDICT_UNPARSEABLE,
                                     'docket event was not an object', src='file')
            text = e.get('text') or e.get('description') or e.get('docketDescrition') or ''
            norm.append({'date': e.get('date') or e.get('eventDate') or '', 'text': text})
        found = classify(norm, status)
        why = found.get('err') or _report_why(found)
        return format_report(case, county, found.get('verdict'),
                             why + ' Not a live clerk read.',
                             events=len(norm), bk_lines=found.get('bk_lines') or 0, src='file')
    if county == 'palmbeach':
        return format_report(case, county, VERDICT_UNAVAILABLE, PALM_WHY, src='none')
    if county != 'broward':
        return format_report(case, county, VERDICT_UNAVAILABLE,
                             'this check only reads Broward and Palm Beach civil cases', src='none')
    key = api_key(env)
    if not key:
        return format_report(case, county, VERDICT_UNAVAILABLE,
                             'BROWARD_CLERK_API_KEY is not set. Docket was not read.', src='none')
    transport = transport or urllib_transport
    entry = read_broward_case(case, key, transport, clock, gap=min_interval(env))
    entry.pop('requests', None)
    if write:
        data, _exists, err = load_cache()
        cache = data if isinstance(data, dict) and not err else {}
        ck = case_key(case)
        if ck:
            store_read(cache, ck, entry, clock.time())
            save_cache(cache)
    why = entry.get('err') or _report_why(entry)
    return format_report(case, county, entry.get('verdict'), why, events=entry.get('events') or 0,
                         bk_lines=entry.get('bk_lines') or 0, src=entry.get('src') or 'broward_api')


def _report_why(found):
    verdict = found.get('verdict')
    if verdict == VERDICT_ACTIVE:
        return ACTIVE_WHY % (found.get('bd') or 'date unknown')
    if verdict == VERDICT_LIFTED:
        return 'stay on the clerk docket was lifted %s' % (found.get('sl') or 'date unknown')
    if verdict == VERDICT_NONE:
        return 'docket fully read, no bankruptcy entry found'
    return found.get('err') or 'docket was not read'


def accept_cli(argv):
    ap = argparse.ArgumentParser(description='Clerk-docket bankruptcy acceptance check')
    ap.add_argument('--case', action='append', default=[])
    ap.add_argument('--entries', default='', help='local JSON docket ({status, entries}); not a live read')
    args = ap.parse_args(argv)
    cases = [c.strip() for c in (args.case or []) if str(c or '').strip()]
    if not cases:
        log('pass --case for each foreclosure case number')
        return 0
    clock = Clock()
    for case in cases:
        log(accept_case(case, entries_path=args.entries, clock=clock))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='Clerk docket bankruptcy check (Broward API; Palm Beach unread)')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--accept', action='store_true')
    ap.add_argument('--case', action='append', default=[])
    ap.add_argument('--entries', default='')
    args = ap.parse_args(argv)
    if args.status:
        log('Clerk docket status: ' + json.dumps(public_status(), sort_keys=True))
        return 0
    if args.accept or args.entries or args.case:
        return accept_cli(_accept_argv(args))
    try:
        run_nightly()
    except Exception:
        log('Clerk docket: nightly run failed. Leads stay held while the check is on.')
        try:
            write_status(pull_ok=False, pull_t=time.time(), pull_ts=_stamp(), reason='failed',
                         enabled=enabled())
        except Exception:
            pass
    return 0


def _accept_argv(args):
    out = []
    if args.entries:
        out += ['--entries', args.entries]
    for c in args.case or []:
        out += ['--case', c]
    return out


if __name__ == '__main__':
    sys.exit(main())
