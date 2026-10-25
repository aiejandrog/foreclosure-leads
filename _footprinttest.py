"""Personal identifiers never reach a published page (footprint.py).

For weeks the board and Call Mode carried, in plaintext on a public site: a home address (as a worked
example), a personal Gmail (inside a regex), the advisor's Gmail (Brief Jesse), a personal phone number
(in a comment) and the operator's name/phone/email (SENDER_DEFAULTS). The one guard that looked at
emails exempted "our own" addresses by name, so the allow-list was the leak.

These checks defend the replacement design:
  * the guard catches a consumer mailbox, and any value from the untracked local file, in plain,
    regex-escaped and oddly spaced form -- and does NOT fire on lookalikes or on base64
  * with no local file the generic rule still holds and every allow-list fails CLOSED
  * the template and Call Mode carry no identity literal; blank in the clear, real values sealed
  * in a real browser: blank while locked, the sealed identity merges on unlock, a retired default is
    healed off a device that saved it, the device's own saved value still wins, a saved value equal to
    a default is "unset", and Brief Jesse with no advisor degrades instead of throwing
  * the local twin gets the real identity written in, exactly once, or the build stops
  * commits into the PUBLIC site repo are pinned to a noreply identity

Every value here is invented (example.org / example.net). Run: python _footprinttest.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix='fp_')
LOCALF = os.path.join(TMP, 'footprint.local.json')
json.dump({'advisor_emails': ['Advisor.Person@example.org'],
           'retired_emails': ['retired.owner@example.net'],
           'retired_addr_prefixes': ['100 N Sample Ave', '300 NW 50'],
           'warmup_peers': ['peer.box@example.org'],
           'deny': ['Sample Holding Entity']}, open(LOCALF, 'w'))
os.environ['DEALFLOW_FOOTPRINT_JSON'] = LOCALF
os.environ.pop('DEALFLOW_ADVISOR_EMAILS', None)

import footprint as FP   # noqa: E402

ok, bad = [], []


def rec(name, cond, detail=''):
    (ok if cond else bad).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)) if (detail and not cond) else ''))


# ---- 1. the guard -----------------------------------------------------------------------------
print('=== guard rules ===')
CASES = [
    ('clean page, example.com is fine', '<p>help@example.com you@example.com</p>', False),
    ('consumer gmail, plain', 'write to someone.real@gmail.com', True),
    ('consumer gmail, dot escaped inside a regex literal', "/^\\s*somebody123@gmail\\.com\\s*$/i", True),
    ('other consumer providers', 'a@yahoo.com b@hotmail.com c@icloud.com d@aol.com', True),
    ('advisor address (local file), any case', 'var J = "ADVISOR.person@Example.org";', True),
    ('advisor mailbox name alone', 'ping advisor.person about it', True),
    ('retired email', 'RETIRED.OWNER@example.NET', True),
    ('retired address, plain', 'e.g. "100 N Sample Ave Apt 4"', True),
    ('retired address inside a regex literal', '/^\\s*100\\s+N\\.?\\s*Sample\\s+Ave/i', True),
    ('retired address, odd spacing and punctuation', '100,  N.  Sample   AVE', True),
    ('retired address whose letters are split by regex escapes (NW as N\\.?\\s*W)',
     '/^\\s*300\\s+N\\.?\\s*W\\.?\\s+50(th)?\\s+Ave/i', True),
    ('retired address as a comment example, with a suffix', '("300 NW 50th Ave Apt 9" -> x)', True),
    ('deny phrase', '// Sample Holding Entity owns it', True),
    ('a neighbouring house number is not a hit', '1300 NW 50th Ave', False),
    ('a different street number is not a hit', '1000 N Sample Avenue', False),
    ('tokens buried in base64 are not a hit', 'A100B/N+Sample7Ave' + 'x' * 60, False),
]
for name, text, want in CASES:
    rec(name, bool(FP.problems(text)) == want, FP.problems(text))
try:
    FP.assert_clean('x.long.name@gmail.com', 'page', exc=ValueError)
    rec('assert_clean raises the exception type it was given', False)
except ValueError as e:
    rec('assert_clean raises the exception type it was given', 'page' in str(e) and 'refusing' in str(e))

print('=== sealed side + fail-closed ===')
FP.private_identity = lambda: {'name': 'Test Operator', 'phone': '(305) 555-0100',
                               'email': 'ops@example.com', 'addr': '1 Test Plaza', 'title': ''}
side = FP.sealed_side()
rec('sealed side carries identity, advisor and retired', set(side) == {'sender', 'advisor', 'retired'}, side)
rec('empty identity fields are not sealed', 'title' not in side.get('sender', {}))
rec('advisor is lowercased', side.get('advisor') == 'advisor.person@example.org', side.get('advisor'))
rec('warmup peers come from the local file', FP.warmup_peers() == ['peer.box@example.org'])
os.environ['DEALFLOW_FOOTPRINT_JSON'] = os.path.join(TMP, 'does-not-exist.json')
rec('NO local file: nobody is an advisor (fail closed)', FP.advisor_emails() == set())
rec('NO local file: nothing sealed but the identity', set(FP.sealed_side()) == {'sender'})
rec('NO local file: the generic consumer-mailbox rule still holds', bool(FP.problems('a.b@gmail.com')))
os.environ['DEALFLOW_ADVISOR_EMAILS'] = 'Env.Advisor@example.org, other@example.org'
rec('env override supplies the advisor set (tests, CI)',
    FP.advisor_emails() == {'env.advisor@example.org', 'other@example.org'})
os.environ.pop('DEALFLOW_ADVISOR_EMAILS')
os.environ['DEALFLOW_FOOTPRINT_JSON'] = LOCALF

# ---- 2. no literals in tracked source ---------------------------------------------------------
print('=== no identity literals in the templates ===')
tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
cm = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
rec('board template: no consumer mailbox of any kind', not FP.CONSUMER_EMAIL.findall(tpl),
    FP.CONSUMER_EMAIL.findall(tpl)[:3])
rec('call_mode.py: no consumer mailbox of any kind', not FP.CONSUMER_EMAIL.findall(cm),
    FP.CONSUMER_EMAIL.findall(cm)[:3])
rec('board SENDER_DEFAULTS is token-fed, not a literal',
    '__SENDER_IDENTITY__' in tpl and not re.search(r"SENDER_DEFAULTS\s*=\s*\{\s*name\s*:", tpl))
rec('board _JESSE is token-fed, not a literal', 'var _JESSE = __ADVISOR_EMAIL__;' in tpl)
rec('board heal is data-driven (sealed retired list)',
    '_senderRetired(' in tpl and 'side.retired' in tpl and 'SENDER_RETIRED' in tpl)
rec('call page SENDER starts blank', re.search(r"var SENDER = \{name:'', phone:''", cm) is not None)
rec('call page JESSE starts blank', "var JESSE = '';" in cm)
rec('call page payload seals the identity', "'s': _sealed_identity()" in cm)

# ---- 3. twin keeps the real identity, exactly once --------------------------------------------
print('=== local twin ===')
def _shape(identity=True, retired=True, advisor=True):
    return ('const D = Object.assign({llc:"x"}, ' + FP.PUBLIC_IDENTITY_JS + ');\n' if identity else '') + \
           ('let SENDER_RETIRED = ' + FP.PUBLIC_RETIRED_JS + ';\n' if retired else '') + \
           ('var _JESSE = ' + FP.PUBLIC_ADVISOR_JS + ';\nvar other = "";\n' if advisor else '')


html = _shape()
out = FP.twin_identity(html)
rec('twin: real identity written in', 'Test Operator' in out and 'advisor.person@example.org' in out)
rec('twin: no placeholder literal left behind', FP.PUBLIC_IDENTITY_JS not in out and 'SENDER_RETIRED = {"email":[]' not in out)
rec('twin: unrelated empty strings are untouched', 'var other = "";' in out)
try:
    FP.twin_identity(_shape(advisor=False))
    rec('twin: a missing placeholder stops the build', False)
except SystemExit as e:
    rec('twin: a missing placeholder stops the build', 'placeholder' in str(e))
try:
    FP.twin_identity(html + _shape(identity=False, retired=False))
    rec('twin: a duplicated placeholder stops the build', False)
except SystemExit:
    rec('twin: a duplicated placeholder stops the build', True)

import foreclosure_leads as F   # noqa: E402
real = F.subst_build_facts(open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read(), 'now')
rec('real template: every identity token is substituted away', not re.search(r'__(SENDER_IDENTITY|SENDER_RETIRED|ADVISOR_EMAIL)__', real))
rec('real template: public build carries blank identity and no advisor',
    FP.PUBLIC_IDENTITY_JS in real and 'var _JESSE = "";' in real, None)
try:
    real_twin = FP.twin_identity(real)
    rec('real template: the twin swap hits each statement exactly once', 'Test Operator' in real_twin and
        'advisor.person@example.org' in real_twin and FP.PUBLIC_IDENTITY_JS not in real_twin)
except SystemExit as e:
    rec('real template: the twin swap hits each statement exactly once', False, e)

# ---- 4. site commits are pinned to a noreply identity ----------------------------------------
print('=== public site commits ===')
import publish_site as PS   # noqa: E402
env = PS._site_env()
rec('site commit email is a GitHub noreply address', PS.SITE_GIT_EMAIL.endswith('@users.noreply.github.com'),
    PS.SITE_GIT_EMAIL)
rec('author AND committer are both pinned',
    all(env.get('GIT_%s_%s' % (w, f)) for w in ('AUTHOR', 'COMMITTER') for f in ('NAME', 'EMAIL')))
rec('site commit is not a consumer mailbox', not FP.CONSUMER_EMAIL.search(PS.SITE_GIT_EMAIL))

# ---- 5. a real browser: board + call page -----------------------------------------------------
print('=== browser: board ===')
try:
    from playwright.sync_api import sync_playwright
except Exception as e:                                             # pragma: no cover
    sync_playwright = None
    rec('playwright available', False, e)

if sync_playwright:                    # the module can be installed with no browser (a bare CI runner)
    try:
        with sync_playwright() as _p:
            _p.chromium.launch().close()
    except Exception as e:
        print('  (skipped: no Chromium on this machine -- %s)' % str(e).splitlines()[0][:90])
        sync_playwright = None

SEAL = {'sender': {'name': 'Test Operator', 'phone': '(305) 555-0100', 'email': 'ops@example.com',
                   'addr': '1 Test Plaza, Testville, FL 33000', 'title': 'Acquisitions'},
        'advisor': 'advisor@example.org',
        'retired': {'email': ['old.default@example.com'], 'addr': ['9 Old Road']}}

if sync_playwright:
    pv = os.path.join(TMP, 'preview.html')
    r = subprocess.run([sys.executable, os.path.join(HERE, 'build_preview.py'), '--out', pv],
                       capture_output=True, text=True, cwd=HERE)
    rec('preview builds', r.returncode == 0 and os.path.exists(pv), (r.stderr or r.stdout)[-300:])
    page_html = open(pv, encoding='utf-8').read() if os.path.exists(pv) else ''
    rec('built preview page: no consumer mailbox, no local-file value', not FP.problems(page_html),
        FP.problems(page_html))
    with sync_playwright() as p:
        b = p.chromium.launch()

        def fresh(saved=None):
            pg = b.new_context().new_page()
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            if saved is not None:
                pg.add_init_script("localStorage.setItem('fcSender', %s)" % json.dumps(json.dumps(saved)))
            pg.goto('file://' + pv)
            pg.wait_for_timeout(500)
            return pg, errs

        pg, errs = fresh({'email': 'Old.Default@Example.com', 'addr': '9 Old Road Apt 4',
                          'phone': '(305) 555-0199'})
        locked = pg.evaluate("({d:{...SENDER_DEFAULTS}, j:_JESSE})")
        rec('locked page: defaults are blank', all(locked['d'].get(k) == '' for k in
                                                   ('name', 'phone', 'email', 'addr', 'title')), locked)
        rec('locked page: no advisor address', locked['j'] == '')
        pg.evaluate("_applyPayload(%s)" % json.dumps({'rows': [], 'side': SEAL}))
        s = pg.evaluate("({...sender})")
        j = pg.evaluate("_JESSE")
        rec('unlock: sealed name and title arrive', s['name'] == 'Test Operator' and s['title'] == 'Acquisitions', s)
        rec('unlock: the retired email is healed off the device', s['email'] == 'ops@example.com', s)
        rec('unlock: the retired address is healed off the device', s['addr'].startswith('1 Test Plaza'), s)
        rec("unlock: the device's own saved phone still wins", s['phone'] == '(305) 555-0199', s)
        rec('unlock: advisor address arrives sealed', j == 'advisor@example.org', j)
        rec('no page errors while locked and unlocking', not errs, errs)

        pg, errs = fresh({'email': 'mine@example.com', 'addr': '5 My Own St'})
        pg.evaluate("_applyPayload(%s)" % json.dumps({'rows': [], 'side': SEAL}))
        s = pg.evaluate("({...sender})")
        rec("an operator's own saved email/address are NOT healed away",
            s['email'] == 'mine@example.com' and s['addr'] == '5 My Own St', s)

        pg, errs = fresh({'name': '', 'phone': '', 'email': ''})
        pg.evaluate("_applyPayload(%s)" % json.dumps({'rows': [], 'side': SEAL}))
        s = pg.evaluate("({...sender})")
        rec('empty saved fields never clobber the sealed defaults', s['name'] == 'Test Operator' and
            s['phone'] == '(305) 555-0100', s)

        pg, errs = fresh()
        pg.evaluate("_applyPayload(%s)" % json.dumps({'rows': [], 'side': SEAL}))
        pg.evaluate("saveSender()")
        pg.evaluate("localStorage.setItem('fcSender', JSON.stringify(Object.assign(JSON.parse(localStorage.getItem('fcSender')), {phone:'(000) 000-0000'}))); "
                    "SENDER_DEFAULTS.phone='(305) 555-0100'; _rebuildSender()")
        rec('a locally saved value overrides a sealed default',
            pg.evaluate("sender.phone") == '(000) 000-0000')

        pg, errs = fresh()
        pg.evaluate("_JESSE=''")
        thrown = pg.evaluate("(()=>{ try{ briefJesse((DATA[0]||{}).case||'x'); return ''; }catch(e){ return String(e); } })()")
        rec('Brief Jesse with no advisor configured degrades, does not throw', thrown == '' and not errs,
            (thrown, errs))

        print('=== browser: call page ===')
        import call_mode   # noqa: E402
        ch = call_mode.build_html([], 0, {'stub': 1}, '2026-10-25T00:00', 'sig', 'bsig', sync_js='')
        rec('call page plaintext: no consumer mailbox, no local-file value', not FP.problems(ch), FP.problems(ch))
        cp = os.path.join(TMP, 'call.html')
        open(cp, 'w', encoding='utf-8').write(ch)
        pg = b.new_context().new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.add_init_script("localStorage.setItem('fcSender', JSON.stringify({phone:'(305) 555-0199'}))")
        pg.goto('file://' + cp)
        pg.wait_for_timeout(400)
        before = pg.evaluate("({n:SENDER.name, p:SENDER.phone, b:SENDER.bsigner, j:JESSE})")
        rec('call page locked: name/signer/advisor blank, saved phone kept',
            before == {'n': '', 'p': '(305) 555-0199', 'b': '', 'j': ''}, before)
        pg.evaluate("_applySealedSender(%s)" % json.dumps({'name': 'Test Operator', 'phone': '(305) 555-0100',
                                                           'bsigner': 'Test Advisor', 'advisor': 'advisor@example.org'}))
        after = pg.evaluate("({n:SENDER.name, p:SENDER.phone, b:SENDER.bsigner, j:JESSE, d:_SENDER_DEF})")
        rec('call page unlock: sealed name, signer, advisor arrive',
            after['n'] == 'Test Operator' and after['b'] == 'Test Advisor' and after['j'] == 'advisor@example.org', after)
        rec("call page unlock: the phone's own saved number still wins", after['p'] == '(305) 555-0199', after)
        rec('call page unlock: the default-owner record is replayed', after['d'].get('name') == 'Test Operator', after)
        rec('call page: no page errors', not errs, errs)
        b.close()

# ---- 6. the repo itself: no real value in ANY tracked file --------------------------------------------
# Only meaningful where the real footprint.local.json exists (the laptop). It is the check that would
# have caught the worst mistake available while writing this module: a real address used as an
# "example" in a comment. docs/ is generated output and is covered by the publish guard instead.
print('=== tracked files carry no value from the real footprint.local.json ===')
REAL = os.path.join(HERE, 'footprint.local.json')
if os.path.exists(REAL):
    os.environ['DEALFLOW_FOOTPRINT_JSON'] = REAL
    FP._CACHE.clear()
    files = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard'], cwd=HERE, capture_output=True,
                           text=True).stdout.split('\n')
    leaks = {}
    for f in files:
        if not f or f.startswith(('docs/', '.git')):
            continue
        try:
            t = open(os.path.join(HERE, f), encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        b = [x for x in FP.problems(re.sub(r'[A-Za-z0-9+/]{400,}={0,2}', '<enc>', t)) if 'consumer mailbox' not in x]
        if b:
            leaks[f] = b[:2]
    rec('no tracked file contains a value from footprint.local.json', not leaks, leaks)
else:
    print('  (skipped: no footprint.local.json on this machine)')

# ---- 7. PDFs never ride into the public site repo -----------------------------------------------
print('=== site publish skips PDFs ===')
import publish_site as PS   # noqa: E402
_d, _s = os.path.join(TMP, 'docs'), os.path.join(TMP, 'site_docs')
os.makedirs(os.path.join(_d, 'x'))
os.makedirs(os.path.join(_s, 'x'))
for _f in ('index.html', 'x/book.pdf', 'x/BOOK2.PDF'):
    open(os.path.join(_d, _f), 'w').write('x')
open(os.path.join(_s, 'x', 'book.pdf'), 'w').write('old')        # already published by an earlier mirror
_old = (PS.DOCS, PS.SITE_DOCS)
PS.DOCS, PS.SITE_DOCS = _d, _s
try:
    rel = sorted(os.path.relpath(p, _d).replace(os.sep, '/') for p in PS._pages())
    rec('_pages() yields no PDF', rel == ['index.html'], rel)
    PS.mirror()
    left = sorted(os.path.relpath(os.path.join(r, n), _s).replace(os.sep, '/')
                  for r, _x, fs in os.walk(_s) for n in fs)
    rec('mirror copies the page and removes a PDF that is still in the local docs/', left == ['index.html'], left)
finally:
    PS.DOCS, PS.SITE_DOCS = _old

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
