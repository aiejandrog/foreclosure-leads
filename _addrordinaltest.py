"""Call Mode street ordinals (2026-10-30): "622 SE 5 AVE, Hialeah, FL 33010" reads "622 SE 5th AVE, ...".

Pins _ord_addr (insert-only suffix; head before first comma; never the house number, routes, units,
ZIPs), that call_rows and coverage_rows ship the spelled address in `a`, and that the built page's
text-template {street} and the card address show it (node). Invented data only.
Run: DEALFLOW_DIR=<temp> python _addrordinaltest.py
"""
import io, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='addrord_')
os.environ.setdefault('DEALFLOW_DIR', _T)
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


f = getattr(call_mode, '_ord_addr', None)
rec('helper exists', callable(f))
if not callable(f):
    print('FAILS: helper missing'); sys.exit(1)

T = [
    ('622 SE 5 AVE, Hialeah, FL 33010', '622 SE 5th AVE, Hialeah, FL 33010'),
    ('10 NW 1 ST, Miami, FL 33130', '10 NW 1st ST, Miami, FL 33130'),
    ('10 NW 2 ST', '10 NW 2nd ST'), ('10 NW 3 ST', '10 NW 3rd ST'), ('10 NW 4 ST', '10 NW 4th ST'),
    ('10 SW 6 CT', '10 SW 6th CT'), ('10 NE 7 TER', '10 NE 7th TER'),
    ('10 SW 11 ST', '10 SW 11th ST'), ('10 SW 12 ST', '10 SW 12th ST'), ('10 SW 13 ST', '10 SW 13th ST'),
    ('10 SW 21 ST', '10 SW 21st ST'), ('10 SW 22 ST', '10 SW 22nd ST'), ('10 SW 23 ST', '10 SW 23rd ST'),
    ('10 SW 101 ST', '10 SW 101st ST'), ('10 SW 111 ST', '10 SW 111th ST'),
    ('10 SW 112 ST', '10 SW 112th ST'), ('10 SW 113 ST', '10 SW 113th ST'),
    ('10 N 5 AVE', '10 N 5th AVE'), ('10 S 5 AVE', '10 S 5th AVE'), ('10 E 5 AVE', '10 E 5th AVE'),
    ('10 W 5 AVE', '10 W 5th AVE'), ('1234 W 49 ST, Hialeah, FL 33012', '1234 W 49th ST, Hialeah, FL 33012'),
    ('622 5 AVE, Miami, FL 33010', '622 5th AVE, Miami, FL 33010'),
    ('622 se 5 ave, Hialeah', '622 se 5th ave, Hialeah'),
    ('622 SE  5 AVE', '622 SE  5th AVE'),
    ('622 SE 5 AVE APT 3, Hialeah, FL 33010', '622 SE 5th AVE APT 3, Hialeah, FL 33010'),
    # unchanged
    ('1500 SR 7, Miami', None), ('1500 US 1, Miami', None), ('1500 HWY 27, Miami', None),
    ('1500 STATE RD 7, Miami', None), ('1500 I 95, Miami', None), ('100 NW HWY 27 ST', None),
    ('10 SW 5 APT 3, Miami', None), ('10 SW 5, Miami', None), ('12345 SW 5 BLDG, Miami', None),
    ('622 SE 5th AVE, Hialeah', None), ('622 SE 5TH AVE, Hialeah', None), ('622 SE 1st AVE', None),
    ('5 AVE, Miami', None), ('55 SE 8 PL 3, Miami', '55 SE 8th PL 3, Miami'), ('Miami, FL 33010 5 AVE', None),
    ('10 SW 5 STREETWISE', None), ('', ''), (None, None),
]
for src, want in T:
    got = f(src)
    want = src if want is None else want
    rec('%r -> %r' % (src, want), got == want, got)

# insert-only: removing the added suffix gives back the input
import re
for src, want in T:
    if src and want and want != src:
        rec('insert-only: ' + src, re.sub(r'(?<=\d)(st|nd|rd|th)\b', '', want) == re.sub(r'(?<=\d)(st|nd|rd|th)\b', '', src))

A = '3055550101'
EX = '622 SE 5 AVE, Hialeah, FL 33010'
OK = '622 SE 5th AVE, Hialeah, FL 33010'


def lead(case, addr):
    return {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': '11/01/2026', 'days': 20, 'tier': 'B',
            'score': 50, 'value': 400000, 'phones': [A], 'phtype': ['mobile'], 'phsrc': ['st'], 'phdnc': [False],
            'phrank': ['ok'], 'phbest': 0, 'addr': addr}


print('\nROWS')
rows, _ = call_mode.call_rows([lead('X1', EX), lead('X2', OK)], None, None)
rec('dial row spelled', rows[0]['a'] == OK, rows[0]['a'])
rec('dial row already 5th unchanged', rows[1]['a'] == OK, rows[1]['a'])
cov = call_mode.coverage_rows([dict(lead('X3', EX), phones=[], phtype=[], phsrc=[], phdnc=[], phrank=[], phbest=None)], set())[0]
rec('coverage row spelled', cov and cov[0]['a'] == OK, cov and cov[0]['a'])

print('\nTHE PAGE, UNDER NODE')
tpl = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html(rows, 2, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 0, "shipped": 0}')
rec('page builds with the dial row', 'function esc(' in html)
a = html.index('function ')
def fn(name):
    s = html.index('function %s(' % name)
    return html[s:html.index('\n}\n', s) + 3]
# {street} / {st1} substitution: find the function holding the .split('{street}')
i = html.index(".split('{street}')")
s = html.rfind('\nfunction ', 0, i) + 1
name = html[s:html.index('(', s)].split()[-1]
body = fn(name)
js = ("var SENDER={name:'Test',bsigner:''};\n" + fn('esc') +
      "\nfunction firstName(r){return '';}\nfunction _vmSale(){return '';}\n" + body +
      "\nvar R=%s;\nconsole.log(JSON.stringify(%s('Visit {street} / {st1}', R)));\n" % (json.dumps(rows[0]), name))
path = os.path.join(_T, 'st.js')
io.open(path, 'w', encoding='utf-8').write(js)
p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
rec('node ran', p.returncode == 0, p.stderr[-400:])
out = json.loads(p.stdout) if p.returncode == 0 else ''
rec('text {street} prints 5th AVE with city', OK in out and '622 SE 5th AVE' in out, out)
rec('card address line prints the row text', "esc(r.a||'(property address not resolved)')" in html)
print('\n%s' % ('ALL PASS' if not fails else 'FAILS: %d -> %s' % (len(fails), fails)))
sys.exit(1 if fails else 0)
