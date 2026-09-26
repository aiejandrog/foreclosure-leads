"""_clerksessiontest -- the Miami-Dade Clerk sign-in (clerk_session.py), all HTTP mocked.

No request leaves this machine: every test hands ClerkSession a fake session that answers from a
script, and the CLI tests replace requests.Session with one that fails the test if it is used.
Credentials are fake (fake-user@example.com / Fake-Pass-123!) and the checks below prove they never
appear in output, exceptions or the status file.

PINS
  * login: the Registered User form is parsed from the page (a changed form fails, never guesses),
    the POST carries exactly the fields the browser sends, success is verified on BOTH apps
  * failure is named and loud: refused credentials, a locked account, a captcha on the login page
    (-> browser profile), missing env vars (-> no request at all)
  * ZERO SPEND: units/advanced-search/basket/order URLs are refused before sending, a redirect onto
    one stops the run, a units/payment prompt in a page stops the run, a drop in prepaid units stops
    the run; nothing outside the URL allowlist is ever requested
  * politeness: a minimum gap between requests and a per-run request budget
  * the records_liens hook is off unless DEALFLOW_CLERK_OR=1, and camoufox_qs defers to it
  * the stay gate does not depend on the login: stay_gate/sale_history never import clerk_session,
    and sale_history reads an active stay from an anonymous-shaped OCS docket on its own

    python _clerksessiontest.py
"""
import ast
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import contextlib

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import clerk_session as C  # noqa: E402

ok, bad = [], []
USER, PASS = 'fake-user@example.com', 'Fake-Pass-123!'
ENV = {C.ENV_USER: USER, C.ENV_PASS: PASS}


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:220]) if d else ''))


LOGIN_PAGE = '''<html><body>
<form method="post" class="form-horizontal" action="/usermanagementservices/Home/LoginOrRegister">
<input class="form-control" id="ApplicationCallID", name="ApplicationCallID" value="%s" hidden="hidden">
<label for="userName">User ID / Email</label><input class="form-control" type="text" id="userName" name="userName" autocomplete="off" />
<label for="password">Password</label><input class="form-control" type="password" id="password" name="password" autocomplete="off" />
<p>By logging in, you agree to the Terms and Conditions</p>
<input class="btn" type="submit" value="Login" name="btnCall"/>
<input class="btn btn-link" type="button" value="Forgot Password" />
<p>Supports purchasing and management of Units used in Advanced Searches of Official Records.</p>
<input class="form-check-input" type="radio" name="ServicesType" id="ServicesTypeIndividual" checked value="Individual">
<input class="form-check-input" type="radio" name="ServicesType" id="ServicesTypeAttorneys" value="Attorneys">
<input class="form-control" type="text" id="newEmail" name="newEmail" />
<input class="btn" type="submit" value="Register" name="btnCall"  />
</form></body></html>'''


class Resp:
    def __init__(self, status=200, body='', ctype='text/html', url='', history=(), headers=None):
        self.status_code, self._body, self.url, self.history = status, body, url, list(history)
        self.headers = dict(headers or {}, **{'Content-Type': ctype})

    @property
    def text(self):
        return self._body if isinstance(self._body, str) else json.dumps(self._body)

    def json(self):
        return self._body if not isinstance(self._body, str) else json.loads(self._body)


class FakeSession:
    """Scripted Clerk. state: logged_in per app, what the POST should do, units."""

    def __init__(self, post='ok', units=0, page_extra='', or_search_valid=False, or_units_after=None):
        self.headers, self.calls = {}, []
        self.post, self.units, self.page_extra = post, units, page_extra
        self.in_ = {'ocs': False, 'or': False}
        self.or_search_valid, self.or_units_after = or_search_valid, or_units_after

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if url.startswith(C.UMS + '/?hs='):
            app = url.rsplit('=', 1)[1]
            return Resp(body=(LOGIN_PAGE % app) + self.page_extra, url=url)
        if url == C.LOGIN_POST:
            if self.post == 'ok':
                self.in_ = {'ocs': True, 'or': True}
                return Resp(body='<html>Welcome</html>', url=C.OCS,
                            history=[Resp(status=302, url=C.LOGIN_POST, headers={'Location': C.OCS})])
            if self.post == 'basket':
                return Resp(body='<html>basket</html>', url=C.WWW2 + '/Basket/Default.aspx',
                            history=[Resp(status=302, url=C.LOGIN_POST, headers={'Location': '/Basket/Default.aspx'})])
            msg = {'bad': 'The User Name or Password is invalid. Please try again.',
                   'locked': 'Your account has been locked after too many failed attempts.'}[self.post]
            return Resp(body=(LOGIN_PAGE % 'ocsb').replace('<form', '<div class="err">%s</div><form' % msg), url=C.LOGIN_POST)
        if url == C.APPS['ocs'][1]:
            return Resp(body='true' if self.in_['ocs'] else 'false', ctype='application/json')
        if url == C.APPS['or'][1]:
            u = self.units
            if self.or_units_after is not None and any('standardsearch' in c[1] for c in self.calls):
                u = self.or_units_after
            return Resp(body={'isLoggedIn': self.in_['or'], 'units': u}, ctype='application/json')
        if 'standardsearch' in url:
            v = self.or_search_valid and self.in_['or']
            return Resp(body={'isValidSearch': v, 'qs': 'FAKEQS==' if v else None}, ctype='application/json')
        if 'GetSDocumentByEvent' in url:
            return Resp(body=[{'documentName': 'Redirect'}], ctype='application/json')
        if url.endswith('/Home/Logout'):
            self.in_ = {'ocs': False, 'or': False}
            return Resp(body='bye')
        if 'units-page' in url:
            return Resp(body="You don't have enough units to use Advanced Search.")
        return Resp(status=404, body='nope')


class Clock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def session(fake, env=ENV, **kw):
    ck = Clock()
    logs = []
    cs = C.ClerkSession(session=fake, env=env, sleep=ck.sleep, clock=ck.clock, log=logs.append, **kw)
    return cs, ck, logs


# ---- 1. the form
action, fields = C.parse_login_form(LOGIN_PAGE % 'ocsb')
rec('the Registered User form is parsed from the page', action == C.LOGIN_POST and fields ==
    {'ApplicationCallID': 'ocsb', 'ServicesType': 'Individual', 'newEmail': '', 'btnCall': 'Login'}, (action, fields))
for name, page in (('no form', '<html>maintenance</html>'),
                   ('no password field', (LOGIN_PAGE % 'ocsb').replace('name="password"', 'name="pw"'))):
    try:
        C.parse_login_form(page); rec('a changed login page fails (%s)' % name, False)
    except C.LoginFailed:
        rec('a changed login page fails (%s)' % name, True)

# ---- 2. successful login
fake = FakeSession()
cs, ck, logs = session(fake)
res = cs.login_all()
rec('login: both OCS and Official Records verified signed in', res == {'ocs': True, 'or': True}, res)
posts = [c for c in fake.calls if c[0] == 'POST']
rec('exactly one login POST (the OR login reused the session)', len(posts) == 1, len(posts))
data = posts[0][2].get('data') or {}
rec('the POST carries exactly the browser\'s fields', set(data) == {'ApplicationCallID', 'userName', 'password',
    'ServicesType', 'newEmail', 'btnCall'} and data['btnCall'] == 'Login' and data['userName'] == USER, sorted(data))
rec('prepaid units are read (0)', cs.units.get('or') == 0.0, cs.units)
rec('nothing sensitive in the log lines', not any(PASS in l or USER in l for l in logs), logs)

# ---- 3. failures
for mode, want in (('bad', 'invalid'), ('locked', 'ACCOUNT LOCKED')):
    fake = FakeSession(post=mode)
    cs, ck, logs = session(fake)
    try:
        cs.login('ocs'); rec('refused login raises LoginFailed (%s)' % mode, False)
    except C.LoginFailed as e:
        rec('refused login raises LoginFailed naming why (%s)' % mode, want in str(e) and PASS not in str(e) and USER not in str(e), e)
fake = FakeSession(post='bad')
cs, ck, logs = session(fake)
res = cs.login_all()
rec('login_all reports a failed login as False, loudly', res == {'ocs': False, 'or': False}
    and any('login FAILED' in l for l in logs), logs[:1])
fake = FakeSession()
cs, ck, logs = session(fake, env={})
try:
    cs.login_all(); rec('no env vars -> NotConfigured', False)
except C.NotConfigured as e:
    rec('no env vars -> NotConfigured, and not one request was made', fake.calls == [] and C.ENV_USER in str(e), fake.calls)
fake = FakeSession(page_extra='<div class="cf-turnstile" data-sitekey="x"></div>')
cs, ck, logs = session(fake)
try:
    cs.login('ocs'); rec('a captcha on the login page -> BrowserNeeded', False)
except C.BrowserNeeded:
    rec('a captcha on the login page -> BrowserNeeded (the profile fallback)', not any(c[0] == 'POST' for c in fake.calls))

# ---- 4. zero spend
for u in (C.OR + 'api/home/update-units/advanced-search', C.OR + 'api/home/getAdvancedRecords',
          C.WWW2 + '/Basket/Default.aspx', C.OR + 'api/Order/certifiedCopies', C.OCS + 'api/CaseInfo/GetAddToBasket'):
    fake = FakeSession()
    cs, ck, logs = session(fake)
    try:
        cs.request('GET', u); rec('spend URL refused: %s' % u.rsplit('/', 1)[1], False)
    except C.SpendRefused:
        rec('spend URL refused before sending: %s' % u.split('.gov')[1][:40], fake.calls == [])
fake = FakeSession(post='basket')
cs, ck, logs = session(fake)
try:
    cs.login('ocs'); rec('a redirect onto the basket stops the run', False)
except C.SpendRefused as e:
    rec('a redirect onto the basket stops the run', 'Basket' in str(e), e)
fake = FakeSession()
cs, ck, logs = session(fake)
C.ALLOWED.append(__import__('re').compile(r'units-page$'))
try:
    cs.request('GET', 'https://www2.miamidadeclerk.gov/ocs/units-page'); rec('a units prompt in a page stops the run', False)
except C.SpendRefused:
    rec('a units prompt in a page stops the run', True)
finally:
    C.ALLOWED.pop()
rec('the login page\'s own "purchasing ... of Units" blurb is NOT a spend prompt',
    not C.SPEND_TEXT.search(LOGIN_PAGE))
fake = FakeSession(or_search_valid=True, units=5, or_units_after=4)
cs, ck, logs = session(fake)
cs.login_all(); u0 = dict(cs.units)
cs.or_search_qs('DOE JOHN'); cs.verify('or')
try:
    cs.assert_no_units_spent(u0); rec('a drop in prepaid units stops the run', False)
except C.SpendRefused as e:
    rec('a drop in prepaid units stops the run', '5.0 to 4.0' in str(e), e)
fake = FakeSession()
cs, ck, logs = session(fake)
try:
    cs.request('GET', 'https://evil.example.com/steal'); rec('URLs off the allowlist are refused', False)
except C.ClerkError:
    rec('URLs off the allowlist are refused before sending', fake.calls == [])

# ---- 5. politeness
fake = FakeSession()
cs, ck, logs = session(fake, min_interval=2.5, jitter=0.0)
for _ in range(3):
    cs.verify('ocs')
rec('>= 2.5s between requests', len(ck.slept) == 2 and all(s >= 2.5 for s in ck.slept), ck.slept)
cs, ck, logs = session(FakeSession(), max_requests=2, jitter=0.0)
cs.verify('ocs'); cs.verify('ocs')
try:
    cs.verify('ocs'); rec('the per-run request budget is enforced', False)
except C.ClerkError as e:
    rec('the per-run request budget is enforced', 'budget' in str(e))

# ---- 6. the data calls
cs, ck, logs = session(FakeSession())
try:
    cs.docket_documents('ENCID'); rec('a Redirect document = LoginRequired, not a silent miss', False)
except C.LoginRequired:
    rec('a Redirect document = LoginRequired, not a silent miss', True)
fake = FakeSession(or_search_valid=True)
cs, ck, logs = session(fake)
rec('anonymous OR search without a token -> None', cs.or_search_qs('DOE JOHN') is None)
cs.login_all()
rec('signed-in OR search accepted -> the qs', cs.or_search_qs('DOE JOHN') == 'FAKEQS==')
hdr = [c[2]['headers'] for c in fake.calls if 'standardsearch' in c[1]][-1]
rec('...sent with an EMPTY captcha header, never a minted token', hdr.get('x-recaptcha-token') == '')
rec('probe says True when the Clerk accepts it', cs.probe_or_http() is True)
cs2, _, _ = session(FakeSession(or_search_valid=False)); cs2.login_all()
rec('probe says False when it does not', cs2.probe_or_http() is False)

# ---- 7. the records_liens hook
rec('hook OFF unless DEALFLOW_CLERK_OR=1', C.or_qs_source(log=lambda m: None, env=dict(ENV)) is None)
msgs = []
rec('hook ON without credentials -> None, and says why',
    C.or_qs_source(log=msgs.append, env={C.ENV_ENABLE: '1'}) is None and 'not set' in ' '.join(msgs), msgs)
src_http = C.HttpQsSource(session(FakeSession(or_search_valid=True))[0], log=lambda m: None)
src_http.cs.login_all()
rec('HttpQsSource returns the qs for an owner', src_http.qs_for(('DOE', 'JOHN')) == 'FAKEQS==')
import records_liens as R  # noqa: E402
rec('records_liens.camoufox_qs defers to the signed-in source', R.camoufox_qs(src_http, ('DOE', 'JOHN')) == 'FAKEQS==')
tmpd = pathlib.Path(tempfile.mkdtemp(prefix='clerk_'))
C.STATUS = str(tmpd / 'clerk_session_status.json')
spend = C.HttpQsSource(session(FakeSession())[0], log=lambda m: None)
spend.cs.request = lambda *a, **k: (_ for _ in ()).throw(C.SpendRefused('units prompt'))
rec('a spend prompt switches the source off for the run', spend.qs_for(('DOE', 'JOHN')) is None and spend.off
    and spend.qs_for(('ROE', 'JANE')) is None)

# ---- 8. CLI: no network for --plan/--dry-run, status file never holds the username
class Boom:
    def __init__(self, *a, **k):
        raise AssertionError('network used')


import requests  # noqa: E402
real_session = requests.Session
requests.Session = Boom
C.MIN_INTERVAL, C.JITTER = 0.0, 0.0          # the CLI builds its own session; no real waiting in tests
old_env = {k: os.environ.get(k) for k in (C.ENV_USER, C.ENV_PASS)}
os.environ.update(ENV)
try:
    for flag in ('--plan', '--dry-run'):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = C.main([flag])
        out = buf.getvalue()
        rec('%s: exit 0, no network, no credential values printed' % flag, rc == 0 and PASS not in out and USER not in out, out[-120:])
    rec('--dry-run says the env vars are set (yes/no only)', 'CLERK_USERNAME set: yes' in out)
    requests.Session = lambda: FakeSession(post='bad')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.main(['--check'])
    st = json.loads(pathlib.Path(C.STATUS).read_text(encoding='utf-8'))
    rec('--check with refused credentials: exit 2, status ok=false', rc == 2 and st['ok'] is False, st)
    rec('the status file never holds the username or password',
        USER not in json.dumps(st) and PASS not in json.dumps(st))
    requests.Session = lambda: FakeSession(or_search_valid=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.main(['--probe'])
    st = json.loads(pathlib.Path(C.STATUS).read_text(encoding='utf-8'))
    rec('--probe signed in: exit 0, records or_http_search=true, units 0', rc == 0 and st.get('or_http_search') is True
        and st.get('units') == 0, st)
    requests.Session = lambda: FakeSession(post='basket')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.main(['--check'])
    rec('--check that meets a payment redirect exits 3', rc == 3, buf.getvalue()[-120:])
finally:
    requests.Session = real_session
    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
rec('_scrub removes raw and URL-encoded secrets', C._scrub('u=%s p=%s' % ('fake-user%40example.com', PASS), ENV) == 'u=*** p=***')

# ---- 9. the stay gate does not depend on the login
for f in ('stay_gate.py', 'sale_history.py', 'send_server.py'):
    tree = ast.parse((HERE / f).read_text(encoding='utf-8'))
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    rec('%s never imports clerk_session' % f, 'clerk_session' not in names | mods)
import sale_history as SH  # noqa: E402
DOCKET = [   # shaped like the anonymous OCS answer; fake text, no parties
    {'eventDate': '01/10/2099', 'docketCode': 'CMPL', 'docketDescrition': 'Complaint', 'comments': ''},
    {'eventDate': '03/02/2099', 'docketCode': 'NOTBCV', 'docketDescrition': 'Notice of Bankruptcy', 'comments': ''},
]
act = SH._bk_stay(DOCKET)
rec('sale_history reads an active stay from an anonymous-shaped docket (Notice of Bankruptcy)', act[0] is True, act)
act = SH._bk_stay(DOCKET[:1] + [{'eventDate': '05/05/2099', 'docketCode': 'SGBK',
                                  'docketDescrition': 'Suggestion of Bankruptcy', 'comments': ''}])
rec('...and from a Suggestion of Bankruptcy', act[0] is True and act[1] == '2099-05-05', act)
rec('clerk_session_status.json is gitignored',
    subprocess.run(['git', 'check-ignore', '-q', 'clerk_session_status.json'], cwd=str(HERE)).returncode == 0)
src = (HERE / 'clerk_session.py').read_text(encoding='utf-8')
rec('clerk_session.py reads credentials only from CLERK_USERNAME / CLERK_PASSWORD',
    "ENV_USER, ENV_PASS = 'CLERK_USERNAME', 'CLERK_PASSWORD'" in src and '.key' not in src.split('import urllib.parse')[1][:4000])

print('\n==== %d/%d clerk-session checks passed ====' % (len(ok), len(ok) + len(bad)))
sys.exit(1 if bad else 0)
