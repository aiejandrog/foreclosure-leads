#!/usr/bin/env python
"""clerk_session.py -- sign in to the Miami-Dade Clerk account (one login for OCS + Official Records).
ZERO SPEND, opt-in, and never on the send path.

WHAT THE LOGIN ACTUALLY ADDS (measured 2026-09-26, anonymous plain HTTP from a Linux box)
  OCS civil dockets          NOTHING. /ocs/api/CaseInfo/encrypt/<case> + GetSingleCaseResult answer a
                             plain anonymous request with the whole docket: 2025-000201-CA-01 came back
                             with all 95 entries, including 'Suggestion of Bankruptcy' (DIN 89, code
                             SGBK) and 'Notice of Bankruptcy' (DIN 64, code NOTBCV). That is the call
                             sale_history.py already makes, so the Miami-Dade stay check (stay_gate.py
                             reading sale_history_cache.json) never depended on PACER and does not need
                             this login. PACER is additive for Miami-Dade stems only.
  OCS docket images          NOTHING for the filings checked. GetSDocumentByEvent?qs=<encID> then
                             /ocs/api/CaseInfo/image served the Suggestion of Bankruptcy PDF (190 KB,
                             %PDF-1.5) anonymously. The OCS app redirects to the login page only when
                             that call answers [{"documentName":"Redirect"}] -- documents the Access
                             Security Matrix limits to registered users. clerk_session reports that
                             case as login_required so it is visible, never a silent miss.
  Official Records search    FEWER CHALLENGES, maybe. api/home/standardsearch always sends a Cloudflare
                             Turnstile token (x-recaptcha-token); anonymously, no token = isValidSearch
                             false. The site's own page says "Avoid verification challenges by signing
                             in". Whether a signed-in session is accepted WITHOUT a token cannot be
                             known without the credentials: `--probe` answers it with one search, and
                             the answer decides plain HTTP versus the browser-profile fallback.
  Official Records images    NOTHING. DocumentImage/getdocumenturl + proxypdf are anonymous already
                             (document_collectors.py).
  Advanced Search / units    FORBIDDEN. getAdvancedRecords spends prepaid units (the page calls
                             api/home/update-units after it); the account has 0 units and this module
                             refuses every such URL before it is sent.

THE LOGIN FLOW (no credentials were used to learn it)
  GET  https://www2.miamidadeclerk.gov/usermanagementservices/?hs=<app>   app: ocsb (OCS), orb (OR)
  POST /usermanagementservices/Home/LoginOrRegister   form: ApplicationCallID, userName, password,
       ServicesType=Individual, newEmail='', btnCall=Login (every named field the browser submits).
       No anti-forgery token and no captcha on this form. A failure answers 200 with the same page
       and a message ("User Name and Password are required to Login").
  verify: OCS  /ocs/api/settings/loggedin -> true|false
          OR   /officialrecords/api/home/isLoggedIn -> {"isLoggedIn":..,"units":..}
  logout: /usermanagementservices/Home/Logout
  The OCS pages load a reCAPTCHA Enterprise widget; it guards the OCS *search* forms (Captcha-Token
  header on PostSearchBy*), not the case-number calls this repo uses.

TERMS (read before arming): "By logging in, you agree to the Terms and Conditions" -- the Clerk's
Registration Agreement to View Records Online, 4(h): the Registered User agrees "to not use or permit
others to use the information obtained from this site for commercial or resale purposes and that all
activity on this site will be tracked and monitored". That is Alejandro's decision to make; nothing
here runs until DEALFLOW_CLERK_OR=1 is set.

CREDENTIALS come ONLY from the Windows user environment variables CLERK_USERNAME / CLERK_PASSWORD.
They are never printed, logged, written to a file, or put in an exception message (_scrub), and the
status file records no username.

FAILURE IS LOUD AND CHANGES NOTHING ELSE. A failed login returns a named failure and exit 2; the
records_liens hook then falls back to its existing free anonymous path. The stay gate does not
import this module, so no login outcome can loosen it.

    python clerk_session.py --plan            # what it would do; no network, no credentials read
    python clerk_session.py --dry-run         # --plan + are the env vars set (yes/no only)
    python clerk_session.py --check           # sign in over plain HTTP, verify both apps, units == 0
    python clerk_session.py --probe           # --check + does a signed-in OR search need a token?
    python clerk_session.py --browser-setup   # one-time: open the dedicated profile, sign in by hand
    python clerk_session.py --browser-check   # is the dedicated profile still signed in?
Exit: 0 ok, 2 login failed / not configured, 3 spend prompt refused, 4 browser profile needed.
"""
import argparse
import datetime as dt
import html as _html
import json
import os
import random
import re
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS = os.path.join(HERE, 'clerk_session_status.json')     # gitignored; never holds a username
ENV_USER, ENV_PASS = 'CLERK_USERNAME', 'CLERK_PASSWORD'
ENV_ENABLE = 'DEALFLOW_CLERK_OR'                              # records_liens hook is off unless '1'

WWW2 = 'https://www2.miamidadeclerk.gov'
UMS = WWW2 + '/usermanagementservices'
LOGIN_POST = UMS + '/Home/LoginOrRegister'
LOGOUT = UMS + '/Home/Logout'
OCS = WWW2 + '/ocs/'
OR = 'https://onlineservices.miamidadeclerk.gov/officialrecords/'
APPS = {                     # app -> (ApplicationCallID, logged-in endpoint)
    'ocs': ('ocsb', OCS + 'api/settings/loggedin'),
    'or': ('orb', OR + 'api/home/isLoggedIn'),
}
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/126.0 Safari/537.36')

# Every URL this module may request. Anything else is refused before it is sent.
ALLOWED = [re.compile(p, re.I) for p in (
    r'^https://www2\.miamidadeclerk\.gov/usermanagementservices/?(\?hs=[a-z]+)?$',
    r'^https://www2\.miamidadeclerk\.gov/usermanagementservices/Home/(LoginOrRegister|Logout)(\?hs=[a-z]+)?$',
    r'^https://www2\.miamidadeclerk\.gov/ocs/api/settings/loggedin$',
    r'^https://www2\.miamidadeclerk\.gov/ocs/api/home/UserLogin$',
    r'^https://www2\.miamidadeclerk\.gov/ocs/api/CaseInfo/(encrypt/[0-9A-Z-]+|GetSingleCaseResult\?qs=.+|GetSDocumentByEvent\?qs=.+|image\?imagePath=.+)$',
    r'^https://onlineservices\.miamidadeclerk\.gov/officialrecords/api/home/(isLoggedIn|GetDate)$',
    r'^https://onlineservices\.miamidadeclerk\.gov/officialrecords/api/home/standardsearch\?.+$',
    r'^https://onlineservices\.miamidadeclerk\.gov/officialrecords/api/SearchResults/getStandardRecords\?qs=.+$',
)]
# Anything that spends, orders or pays. Refused on the way out AND if a redirect lands on it.
SPEND_URL = re.compile(r'update-units|getAdvancedRecords|AdvancedSearch|certifiedCopies|GetAddToBasket|'
                       r'/Basket/|checkout|payment|purchase|buyunits|addunits|/Order/', re.I)
# The same thing said in a page: a units or payment prompt means stop using the account.
SPEND_TEXT = re.compile(r"(add|buy|purchase)\s+(more\s+)?units|n[o']t\s+(have\s+)?enough\s+units|"
                        r'proceed to checkout|payment (information|method)|credit card|'
                        r'units? (will be|are) (deducted|charged)', re.I)
CAPTCHA_MARK = re.compile(r'g-recaptcha|grecaptcha|cf-turnstile|challenges\.cloudflare\.com/turnstile|h-captcha', re.I)
MIN_INTERVAL, JITTER, MAX_REQUESTS = 2.5, 1.0, 400          # polite: one request per 2.5-3.5 s
LOCKED_TEXT = re.compile(r'locked|too many (failed )?attempts|disabled|suspended', re.I)


class ClerkError(RuntimeError):
    code = 2


class NotConfigured(ClerkError):
    code = 2


class LoginFailed(ClerkError):
    code = 2


class SpendRefused(ClerkError):
    code = 3


class BrowserNeeded(ClerkError):
    code = 4


class LoginRequired(ClerkError):
    """A document the anonymous session was redirected away from (registered-user only)."""
    code = 2


def _secrets(env):
    out = []
    for k in (ENV_USER, ENV_PASS):
        v = (env.get(k) or '').strip()
        if len(v) >= 3:
            out += [v, urllib.parse.quote(v), urllib.parse.quote_plus(v), _html.escape(v)]
    return sorted(set(out), key=len, reverse=True)


def _scrub(text, env=None):
    """Remove the configured username/password (raw, URL- and HTML-encoded) from any text."""
    s = str(text)
    for v in _secrets(os.environ if env is None else env):
        s = s.replace(v, '***')
    return s


def configured(env=None):
    env = os.environ if env is None else env
    return bool((env.get(ENV_USER) or '').strip() and (env.get(ENV_PASS) or '').strip())


def _page_text(body, limit=400):
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', body or '', flags=re.S | re.I)
    t = _html.unescape(re.sub(r'<[^>]+>', ' ', t))
    return re.sub(r'\s+', ' ', t).strip()[:limit]


def parse_login_form(body):
    """The Registered User form on the UMS page: (action, fields). Raises LoginFailed if it is not
    the form this module was written against (a changed page must fail, not guess)."""
    m = re.search(r'<form[^>]*action="([^"]*LoginOrRegister[^"]*)"[^>]*>(.*?)</form>', body or '', re.S | re.I)
    if not m:
        raise LoginFailed('login page has no LoginOrRegister form (site changed?)')
    action, inner = _html.unescape(m.group(1)), m.group(2)
    names = set(re.findall(r'<input[^>]*\bname="([^"]+)"', inner, re.I))
    if not {'userName', 'password'} <= names:
        raise LoginFailed('login form lacks userName/password fields (site changed?)')
    fields = {}
    for tag in re.findall(r'<input[^>]*>', inner, re.I):
        name = re.search(r'\bname="([^"]+)"', tag)
        if not name:
            continue
        n = name.group(1)
        typ = (re.search(r'\btype="([^"]+)"', tag) or [None, 'text'])[1].lower()
        val = _html.unescape((re.search(r'\bvalue="([^"]*)"', tag) or [None, ''])[1])
        if typ in ('submit', 'button') or n in ('userName', 'password'):
            continue
        if typ == 'radio':
            if re.search(r'\bchecked\b', tag, re.I):
                fields[n] = val
            continue
        fields.setdefault(n, val)              # hidden ApplicationCallID, anti-forgery if ever added
    fields['btnCall'] = 'Login'
    return urllib.parse.urljoin(UMS + '/', action), fields


class ClerkSession:
    """A requests session that only talks to the allowed Clerk URLs, politely, and never spends."""

    def __init__(self, session=None, env=None, sleep=time.sleep, clock=time.monotonic,
                 min_interval=None, jitter=None, max_requests=None, log=print):
        if session is None:
            import requests
            session = requests.Session()
        self.s = session
        self.s.headers.update({'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9'})
        self.env = os.environ if env is None else env
        self.sleep, self.clock = sleep, clock
        self.min_interval = MIN_INTERVAL if min_interval is None else min_interval
        self.jitter = JITTER if jitter is None else jitter
        self.max_requests = MAX_REQUESTS if max_requests is None else max_requests
        self.log = log
        self.count = 0
        self._last = None
        self.units = {}
        self.logged_in = {}

    # ---- plumbing
    def _say(self, msg):
        self.log(_scrub(msg, self.env))

    def _throttle(self):
        if self.count >= self.max_requests:
            raise ClerkError('request budget for this run used up (%d)' % self.max_requests)
        if self._last is not None:
            wait = self.min_interval + random.uniform(0, self.jitter) - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        self.count += 1

    def request(self, method, url, **kw):
        if SPEND_URL.search(url):
            raise SpendRefused('refused a spend/order URL before sending: %s' % url.split('?')[0])
        if not any(p.search(url) for p in ALLOWED):
            raise ClerkError('URL not on the allowlist: %s' % url.split('?')[0])
        self._throttle()
        kw.setdefault('timeout', 30)
        try:
            r = self.s.request(method, url, **kw)
        except Exception as e:
            raise ClerkError('network error: %s' % _scrub(str(e)[:120], self.env))
        for h in list(getattr(r, 'history', []) or []) + [r]:
            u = str(getattr(h, 'url', '') or '')
            loc = str((getattr(h, 'headers', {}) or {}).get('Location', '') or '')
            hit = u if SPEND_URL.search(u) else loc if SPEND_URL.search(loc) else ''
            if hit:
                raise SpendRefused('a redirect led to a spend/payment page (%s); stopped' % hit.split('?')[0])
        ctype = str((r.headers or {}).get('Content-Type', '')).lower()
        if 'pdf' not in ctype and 'image' not in ctype and 'octet' not in ctype:
            body = r.text[:200000] if hasattr(r, 'text') else ''
            if SPEND_TEXT.search(body or ''):
                raise SpendRefused('the Clerk answered with a units/payment prompt; stopped using the account')
        return r

    # ---- login
    def credentials(self):
        u, p = (self.env.get(ENV_USER) or '').strip(), (self.env.get(ENV_PASS) or '')
        if not u or not p.strip():
            raise NotConfigured('%s / %s are not set for this Windows user' % (ENV_USER, ENV_PASS))
        return u, p

    def login(self, app='ocs'):
        call_id, _ = APPS[app]
        user, pw = self.credentials()
        page = self.request('GET', '%s/?hs=%s' % (UMS, call_id))
        if page.status_code != 200:
            raise LoginFailed('login page answered HTTP %s' % page.status_code)
        if CAPTCHA_MARK.search(page.text or ''):
            raise BrowserNeeded('the login page now carries a captcha; use --browser-setup')
        action, fields = parse_login_form(page.text)
        if action.split('?')[0].lower() != LOGIN_POST.lower():
            raise LoginFailed('login form posts somewhere unexpected: %s' % action.split('?')[0])
        data = dict(fields, userName=user, password=pw)
        r = self.request('POST', LOGIN_POST, data=data,
                         headers={'Referer': '%s/?hs=%s' % (UMS, call_id),
                                  'Origin': WWW2, 'Content-Type': 'application/x-www-form-urlencoded'})
        body = r.text or ''
        if re.search(r'id="password"', body) and 'LoginOrRegister' in body:
            msg = _scrub(_page_text(body, 2000), self.env)
            m = re.search(r'([^.]*?(required|invalid|incorrect|not match|locked|attempts|disabled)[^.]*)', msg, re.I)
            why = m.group(1).strip()[:160] if m else 'the login page came back (credentials refused)'
            if LOCKED_TEXT.search(why):
                why = 'ACCOUNT LOCKED/DISABLED - ' + why
            raise LoginFailed(why)
        if CAPTCHA_MARK.search(body) and 'LoginOrRegister' in body:
            raise BrowserNeeded('the login answered with a captcha; use --browser-setup')
        return self.verify(app)

    def verify(self, app):
        _, url = APPS[app]
        r = self.request('GET', url, headers={'Accept': 'application/json', 'Referer': OCS if app == 'ocs' else OR})
        try:
            j = r.json()
        except Exception:
            j = (r.text or '').strip().lower() == 'true'
        if isinstance(j, dict):
            ok = bool(j.get('isLoggedIn'))
            if 'units' in j:
                try:
                    self.units[app] = float(j.get('units') or 0)
                except (TypeError, ValueError):
                    self.units[app] = None
        else:
            ok = bool(j)
        self.logged_in[app] = ok
        return ok

    def login_all(self):
        """Sign in for OCS, then OR (reusing the session if the first login already covers OR).
        Returns {'ocs': bool, 'or': bool}; raises only on spend/captcha/credentials problems."""
        self.credentials()                       # NotConfigured before any request is made
        out = {}
        for app in ('ocs', 'or'):
            try:
                out[app] = self.verify(app) or self.login(app)
            except LoginFailed as e:
                self._say('  clerk %s login FAILED: %s' % (app, e))
                out[app] = False
        return out

    def assert_no_units_spent(self, before):
        """Zero-spend check: units read at the start vs now. Any decrease is a hard stop."""
        now = self.units.get('or')
        b = before.get('or') if isinstance(before, dict) else before
        if b is not None and now is not None and now < b:
            raise SpendRefused('prepaid units went from %s to %s during this run' % (b, now))

    def logout(self):
        try:
            self.request('GET', LOGOUT)
        except ClerkError:
            pass

    # ---- what the login is for
    def docket(self, case):
        """Full OCS docket JSON for one Miami-Dade case (works signed in or not)."""
        e = self.request('GET', OCS + 'api/CaseInfo/encrypt/%s' % case, headers={'Referer': OCS}).json()
        qs = (e or {}).get('qs')
        if not qs:
            raise ClerkError('OCS would not encrypt %s' % case)
        return self.request('POST', OCS + 'api/CaseInfo/GetSingleCaseResult?qs=' + qs, data='""',
                            headers={'Referer': OCS, 'Content-Type': 'application/json'}).json()

    def docket_documents(self, enc_id):
        """The document list for one docket entry. A 'Redirect' entry = registered users only."""
        j = self.request('GET', OCS + 'api/CaseInfo/GetSDocumentByEvent?qs=' + enc_id,
                         headers={'Referer': OCS, 'Accept': 'application/json'}).json()
        if isinstance(j, list) and len(j) == 1 and (j[0] or {}).get('documentName') == 'Redirect':
            raise LoginRequired('this docket document is limited to signed-in registered users')
        return j or []

    def or_search_qs(self, party, date_from='', date_to='', doc_type='', token=''):
        """One Official Records standard search; the qs, or None when the Clerk says not valid.
        Without a token this only works if a signed-in session is exempt (see --probe)."""
        q = urllib.parse.urlencode({'partyName': party, 'dateRangeFrom': date_from, 'dateRangeTo': date_to,
                                    'documentType': doc_type, 'searchT': doc_type, 'firstQuery': 'y',
                                    'searchtype': 'Name/Document'}, quote_via=urllib.parse.quote)
        r = self.request('POST', OR + 'api/home/standardsearch?' + q, data='',
                         headers={'Accept': 'application/json', 'x-recaptcha-token': token or '',
                                  'content-type': 'application/json; charset=utf-8', 'Referer': OR})
        try:
            j = r.json() or {}
        except Exception:
            return None
        return j.get('qs') if j.get('isValidSearch') else None

    def or_records(self, qs):
        r = self.request('GET', OR + 'api/SearchResults/getStandardRecords?qs=' + urllib.parse.quote(qs, safe=''),
                         headers={'Accept': 'application/json', 'Referer': OR})
        return (r.json() or {}).get('recordingModels') or []

    def probe_or_http(self):
        """Does a SIGNED-IN plain-HTTP standard search go through without a Turnstile token?
        One search for a public body over one week of recordings. True / False."""
        today = dt.date.today()
        frm = (today - dt.timedelta(days=14)).strftime('%m/%d/%Y')
        to = (today - dt.timedelta(days=7)).strftime('%m/%d/%Y')
        return self.or_search_qs('MIAMI-DADE COUNTY', frm, to) is not None


# ---- status file ----------------------------------------------------------------------------------
def write_status(**kw):
    st = dict(kw, ts=dt.datetime.now().isoformat(timespec='seconds'))
    tmp = STATUS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATUS)
    return st


def read_status(path=None):
    try:
        with open(path or STATUS, encoding='utf-8') as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


# ---- browser-profile fallback -------------------------------------------------------------------
def profile_dir():
    """The job's OWN Chromium profile, outside the repo (never committed): ~/DEALFLOW/clerk-profile.
    Not the user's Chrome profile and never its cookies."""
    try:
        import paths as P
        return P.out('clerk-profile')
    except Exception:
        return os.path.join(os.path.expanduser('~'), 'DEALFLOW', 'clerk-profile')


def _page_logged_in(page):
    try:
        j = page.evaluate("async () => { const r = await fetch('/officialrecords/api/home/isLoggedIn');"
                          " return r.ok ? await r.json() : null; }")
    except Exception:
        return None
    return j if isinstance(j, dict) else None


def open_profile(playwright, headless=False):
    return playwright.chromium.launch_persistent_context(profile_dir(), headless=headless,
                                                         viewport={'width': 1300, 'height': 950})


def browser_sign_in(ctx, env=None, wait_s=0):
    """Sign the dedicated profile in: from CLERK_USERNAME/CLERK_PASSWORD when set (typed into the
    Clerk's own form, never printed), otherwise by hand (wait_s > 0). Returns the isLoggedIn dict."""
    env = os.environ if env is None else env
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(OR, wait_until='domcontentloaded', timeout=60000)
    st = _page_logged_in(page)
    if st and st.get('isLoggedIn'):
        return st
    page.goto('%s/?hs=%s' % (UMS, APPS['or'][0]), wait_until='domcontentloaded', timeout=60000)
    if configured(env):
        page.fill('#userName', env[ENV_USER].strip())
        page.fill('#password', env[ENV_PASS])
        page.click('input[name="btnCall"][value="Login"]')
        page.wait_for_load_state('domcontentloaded', timeout=60000)
    elif wait_s:
        print('  sign in to the Clerk in the browser window (you have %d min)...' % (wait_s // 60))
    deadline = time.time() + max(wait_s, 20)
    while time.time() < deadline:
        if SPEND_URL.search(page.url or ''):
            raise SpendRefused('the browser landed on a spend/payment page; stopped')
        if 'officialrecords' in (page.url or ''):
            st = _page_logged_in(page)
            if st and st.get('isLoggedIn'):
                return st
        else:
            try:
                page.goto(OR, wait_until='domcontentloaded', timeout=60000)
            except Exception:
                pass
        time.sleep(3)
    raise BrowserNeeded('the dedicated profile is not signed in; run: python clerk_session.py --browser-setup')


# ---- the records_liens hook ---------------------------------------------------------------------
class HttpQsSource:
    """records_liens' token-source slot (it calls camoufox_qs(source, owner_lf), which defers to
    .qs_for when present): a signed-in plain-HTTP standard search, used only after --probe proved
    the Clerk accepts it without a captcha token. A spend prompt switches it off for the run."""

    def __init__(self, cs, log=print):
        self.cs, self.log, self.off = cs, log, False
        self.units0 = dict(cs.units)

    def qs_for(self, owner_lf):
        if self.off:
            return None
        last, first = (owner_lf if not isinstance(owner_lf, str) else (owner_lf, ''))
        try:
            qs = self.cs.or_search_qs(' '.join(x for x in (last, first) if x).strip())
            if self.cs.count % 25 == 0:
                self.cs.verify('or')
                self.cs.assert_no_units_spent(self.units0)
            return qs
        except SpendRefused as e:
            self.off = True
            self.log('  !! clerk: %s - account use STOPPED for this run' % e)
            write_status(ok=False, mode='http', reason='spend refused: %s' % e)
            return None
        except ClerkError as e:
            self.log('  clerk: %s' % e)
            return None


def or_qs_source(log=print, env=None):
    """For records_liens: an object to use in place of the Camoufox browser, or None (reason logged)
    when the signed-in path is off or unusable. Never raises.
      * HttpQsSource  when `--probe` recorded that a signed-in search needs no captcha token
      * the dedicated Playwright profile (a BrowserContext) otherwise -- records_liens' own
        camoufox_qs drives the real search page in it, signed in"""
    env = os.environ if env is None else env
    if (env.get(ENV_ENABLE) or '').strip() != '1':
        return None
    if not configured(env):
        log('  clerk: %s=1 but %s/%s are not set - signed-in path OFF' % (ENV_ENABLE, ENV_USER, ENV_PASS))
        return None
    try:
        if read_status().get('or_http_search') is True:
            cs = ClerkSession(env=env, log=log)
            if not cs.login_all().get('or'):
                log('  clerk: plain-HTTP login failed - signed-in path OFF for this run')
                return None
            log('  clerk: signed in (plain HTTP, no captcha token needed)')
            return HttpQsSource(cs, log=log)
        log('  clerk: signed-in plain HTTP not proven (run --probe); using the dedicated browser profile')
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        ctx = open_profile(pw, headless=False)
        browser_sign_in(ctx, env=env)
        log('  clerk: dedicated browser profile signed in')
        return ctx
    except SpendRefused as e:
        log('  !! clerk: %s - account use STOPPED' % e)
        write_status(ok=False, mode='refused', reason=str(e))
        return None
    except Exception as e:
        log('  clerk: signed-in path unavailable (%s)' % _scrub(str(e)[:120], env))
        return None


# ---- CLI -----------------------------------------------------------------------------------------
PLAN = """clerk_session plan (no network, no credentials read):
  1. read %(u)s / %(p)s from this Windows user's environment (value never shown)
  2. GET  %(ums)s/?hs=ocsb        parse the Registered User form (fail if it changed or has a captcha)
  3. POST %(post)s   userName/password + the form's own fields
  4. GET  %(ocs)sapi/settings/loggedin                 must be true
  5. repeat 2-4 with hs=orb; GET %(or)sapi/home/isLoggedIn   must be true, record units
  6. --probe only: ONE standardsearch with no captcha token (a public body, one week)
  rate: >= %(gap).1fs between requests + up to %(jit).1fs jitter, at most %(max)d requests per run
  refused before sending: %(spend)s
  allowed: %(n)d URL patterns (UMS login/logout, OCS login status + case docket/documents,
           OR login status + standard search + results)
  writes: clerk_session_status.json (ok, mode, units, reason; no username)"""


def main(argv=None):
    ap = argparse.ArgumentParser(description='Miami-Dade Clerk sign-in, zero spend.')
    g = ap.add_mutually_exclusive_group(required=True)
    for f in ('plan', 'dry-run', 'check', 'probe', 'browser-setup', 'browser-check'):
        g.add_argument('--' + f, action='store_true')
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    if a.plan or a.dry_run:
        print(PLAN % {'u': ENV_USER, 'p': ENV_PASS, 'ums': UMS, 'post': LOGIN_POST, 'ocs': OCS, 'or': OR,
                      'gap': MIN_INTERVAL, 'jit': JITTER, 'max': MAX_REQUESTS, 'spend': SPEND_URL.pattern[:90] + '...',
                      'n': len(ALLOWED)})
        if a.dry_run:
            print('  %s set: %s | %s set: %s | %s=%s | profile %s: %s' % (
                ENV_USER, 'yes' if (os.environ.get(ENV_USER) or '').strip() else 'NO',
                ENV_PASS, 'yes' if (os.environ.get(ENV_PASS) or '').strip() else 'NO',
                ENV_ENABLE, os.environ.get(ENV_ENABLE, '(unset)'), profile_dir(),
                'exists' if os.path.isdir(profile_dir()) and os.listdir(profile_dir()) else 'not created'))
            st = read_status()
            print('  last status: %s' % (json.dumps({k: st.get(k) for k in ('ts', 'ok', 'mode', 'or_http_search', 'reason')})
                                         if st else 'none'))
        return 0
    try:
        if a.browser_setup or a.browser_check:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                ctx = open_profile(pw, headless=False)
                try:
                    st = browser_sign_in(ctx, env={} if a.browser_setup else os.environ,
                                         wait_s=300 if a.browser_setup else 0)
                finally:
                    ctx.close()
            units = st.get('units')
            print('  dedicated profile signed in. prepaid units: %s' % units)
            write_status(ok=True, mode='browser', units=units, reason='')
            return 0
        cs = ClerkSession()
        res = cs.login_all()
        ok = bool(res.get('ocs') and res.get('or'))
        units = cs.units.get('or')
        probe = None
        if ok and a.probe:
            probe = cs.probe_or_http()
            cs.verify('or')
            cs.assert_no_units_spent({'or': units})
        print('  OCS signed in: %s | Official Records signed in: %s | prepaid units: %s%s' % (
            res.get('ocs'), res.get('or'), units,
            '' if probe is None else ' | signed-in search without a captcha token: %s'
            % ('WORKS (plain HTTP)' if probe else 'refused -> use --browser-setup')))
        if units:
            print('  note: the account holds prepaid units; this module never uses them.')
        cs.logout()
        prev = read_status()
        write_status(ok=ok, mode='http', units=units, apps=res,
                     or_http_search=probe if probe is not None else prev.get('or_http_search'),
                     reason='' if ok else 'login failed for %s' % ','.join(k for k, v in res.items() if not v))
        return 0 if ok else 2
    except ClerkError as e:
        print('  clerk: %s' % _scrub(str(e)))
        try:
            write_status(ok=False, mode=type(e).__name__, reason=_scrub(str(e))[:200])
        except Exception:
            pass
        return e.code


if __name__ == '__main__':
    sys.exit(main())
