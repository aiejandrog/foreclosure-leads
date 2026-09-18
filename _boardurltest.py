"""One-shot: the published board can only ever point at the live board, and the Morning Worker's
subject/body can never render an empty property address. Gitignored _*.py, no network, no browser.

WHAT THIS GUARDS (both found on 2026-09-18, in the same Morning Worker run)

1. THE DEAD BUTTON. The board moved to aiejandrog.github.io/dealflow-board on 2026-09-17 and every
   hardcoded copy was hand-edited the same evening. The board that was ALREADY BUILT kept the old
   string, and because publish_site.py had never succeeded, that build stayed live -- so the
   operator's "Call these 30 now" button pointed at a repo that is private with Pages off. Nothing
   in the build had an opinion about it. Now board_url.check_board_urls() runs inside
   subst_build_facts(), which BOTH template readers go through.

2. THE EMPTY ADDRESS. Nine sends were refused at the bridge by mail_guard -- "the subject ends on
   'at' with nothing after it", "a value that rendered EMPTY near 'about .'" -- because the worker
   took the street straight off r.addr with no guard, while outreach_email.py (the unattended
   sender, same subject, same homeowner) had routed through mail_guard.safe_street since 09-17.
   The two builders must agree, so this compares them case by case against the SAME inputs rather
   than asserting each one separately.

Node is used to run the template's own JavaScript. If node is missing the JS half is SKIPPED and
said so out loud -- it is not silently passed.
"""
import io, os, re, subprocess, sys, tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, 'tracker_template.html')

import board_url as B
import mail_guard as MG

R = []
def rec(n, ok, d=''):
    R.append(bool(ok))
    print((('  PASS ' if ok else '  FAIL ') + n + (' | ' + str(d) if d else '')).encode('ascii', 'replace').decode())

tpl = io.open(TPL, encoding='utf-8').read()

# ---------------------------------------------------------------- 1. the address, one definition
rec('board_url.BOARD_URL is an absolute https URL ending in /',
    B.BOARD_URL.startswith('https://') and B.BOARD_URL.endswith('/'), B.BOARD_URL)
rec('the retired addresses are still remembered',
    any('foreclosure-leads' in u for u in B.RETIRED), ' '.join(B.RETIRED))
rec('call_url() composes off the one constant',
    B.call_url() == B.BOARD_URL + 'call/' and B.call_url('carlos') == B.BOARD_URL + 'call/carlos/',
    B.call_url('carlos'))

# offenders(): a retired address is caught, and so is one nobody has thought of yet.
rec('offenders() flags the retired board URL',
    B.offenders('<a href="%s/call/">x</a>' % B.RETIRED[0].rstrip('/')) != [])
rec('offenders() flags an unknown github.io path on this account',
    B.offenders('https://aiejandrog.github.io/some-future-name/') != [])
rec('offenders() passes the live board and its subpaths',
    B.offenders(B.BOARD_URL + ' ' + B.call_url() + ' ' + B.call_url('carlos')) == [], )
rec('offenders() ignores other accounts',
    B.offenders('https://facebook.github.io/watchman/') == [])

_raised = False
try:
    B.check_board_urls('go to ' + B.RETIRED[0], 'a test page')
except SystemExit:
    _raised = True
rec('check_board_urls() aborts the build on a retired address', _raised)
rec('check_board_urls() passes a clean page', B.check_board_urls(B.BOARD_URL, 'a test page') is True)

# ---------------------------------------------------------------- 2. the template itself
rec('the template hardcodes no board URL at all',
    B.offenders(tpl) == [] and B.BOARD_URL not in tpl,
    ', '.join(B.offenders(tpl))[:70] or 'clean')
rec('the template carries the __BOARDURL__ placeholder instead',
    tpl.count('__BOARDURL__') >= 2, '%d occurrence(s)' % tpl.count('__BOARDURL__'))

# The repo's OTHER python surfaces must not grow their own copy. Prose (README, CLAUDE.md,
# MACHINE-HANDOFF) is documentation and is allowed to print the address; code is not.
_own = []
for f in sorted(os.listdir(HERE)):
    # `_*.py` are the gitignored one-shot suites; they describe the incident in prose and are not
    # a surface anything is built from. board_url.py is the definition itself.
    if not f.endswith('.py') or f.startswith('_') or f == 'board_url.py':
        continue
    try:
        txt = io.open(os.path.join(HERE, f), encoding='utf-8').read()
    except Exception:
        continue
    if 'aiejandrog.github.io' in txt:
        _own.append(f)
rec('no python module retypes the board address', not _own, ', '.join(_own) or 'none')

# ---------------------------------------------------------------- 3. the built board, if present
_built = os.path.join(HERE, 'docs', 'index.html')
if os.path.exists(_built):
    _b = io.open(_built, encoding='utf-8', errors='replace').read()
    rec('docs/index.html points only at the live board',
        B.offenders(_b) == [], ', '.join(B.offenders(_b))[:70] or 'clean')
    rec('docs/index.html has no unbaked placeholder left', '__BOARDURL__' not in _b)
else:
    print('  SKIP  docs/index.html not in this checkout')

# ---------------------------------------------------------------- 4. the never-empty address
# The cases that actually appear on the board, including the three shapes that produced the nine
# refusals: empty, whitespace, punctuation-only.
CASES = [
    '',
    '   ',
    ',',
    ' , - ',
    '455 NE 210 TER, MIAMI, FL- 33179',
    '842 NW 9TH ST, MIAMI, FL 33136',
    '1450 SW 27TH AVE, MIAMI BEACH, FL 33139',
    '3120 CORAL WAY',
    ', MIAMI, FL 33145',
    '12535 SW 33 ST, FT. LAUDERDALE, FL- 33312',
]

_js = re.search(r'function _safeAddr\(raw, fallback\)\{.*?\n\}\n(?:/\*.*?\*/\n)?'
                r'function _safeStreet\(raw, fallback\)\{.*?\n\}\n', tpl, re.S)
rec('the template defines _safeAddr and _safeStreet', bool(_js))

if _js:
    prog = (_js.group(0) + '\nconst CASES = ' + repr(CASES).replace("'", '"') + ';\n'
            'console.log(JSON.stringify(CASES.map(function(c){'
            'return [_safeAddr(c), _safeStreet(c)]; })));\n')
    try:
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
            fh.write(prog); _path = fh.name
        out = subprocess.run([os.environ.get('NODE', 'node'), _path],
                             capture_output=True, text=True, timeout=30)
        os.unlink(_path)
        if out.returncode != 0:
            rec('the template JS runs under node', False, (out.stderr or '').strip()[:90])
        else:
            import json as _json
            got = _json.loads(out.stdout.strip())
            want = [[MG.safe_addr(c), MG.safe_street(c)] for c in CASES]
            bad = [(c, g, w) for c, g, w in zip(CASES, got, want) if g != w]
            rec('the worker and mail_guard agree on every address, case by case',
                not bad, '' if not bad else '%r -> js %r vs py %r' % bad[0])
            rec('no case renders an empty street',
                all(g[1].strip() for g in got))
            rec('an address-less lead falls back, it does not blank',
                got[0] == ['your property', 'your property'], got[0])
            rec('a real address is unchanged but tidied',
                got[4][1] == '455 NE 210 TER' and 'FL 33179' in got[4][0], got[4])
            rec('a shouted multi-word city title-cases like python str.title()',
                got[6][0] == MG.safe_addr(CASES[6]), got[6][0])
    except FileNotFoundError:
        print('  SKIP  node not installed — the JavaScript half of this suite did NOT run')
    except Exception as e:                                       # pragma: no cover - defensive
        rec('the template JS runs under node', False, str(e)[:90])

# ---------------------------------------------------------------- 5. the subject still routes
rec('genEmail derives its street through _safeStreet',
    re.search(r'const street = _safeStreet\(addr\);', tpl) is not None)
# replies.py matches on this tail; a fallback that dropped it would break reply attribution.
rec("the subject tail replies.py anchors on is intact",
    "'Regarding your property at ' + String(street||'')" in tpl)

print('\n%d/%d passed' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
