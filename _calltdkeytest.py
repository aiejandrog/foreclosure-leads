"""Call Mode row key 'td' is the TAX-DEED FLAG only; back-taxes dollars ride on 'txd' (F-data-06).

call_rows built its row from one dict literal that named 'td' twice: the tax-deed flag, then the
back-taxes amount. Python keeps the last one, so a FORECLOSURE lead with back taxes carried
td = <dollars>, and the email and text composers (which read td as "this is a tax deed sale")
picked the tax-deed wording for it.

Pins:
  - a foreclosure lead with back taxes: no truthy td, txd == the dollars
  - a foreclosure lead with no back taxes: no td, no truthy txd
  - composeMail and boardTextBody (the page's own JS, run under node) hand td=false to the body
    builders for the foreclosure lead, and td=true for a row that carries the flag (call_rows keeps
    tax-deed auctions off the dial list today, so that control row is the same row with td set to 1)
  - the two back-taxes renderers read r.txd, not r.td
  - no dict literal in call_mode.py repeats a constant key (AST pass)

Invented leads only; no network. Run: DEALFLOW_DIR=<temp> python _calltdkeytest.py
"""
import ast
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='calltd_')
os.environ.setdefault('DEALFLOW_DIR', _T)
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')

import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


A = '3055550101'


def lead(case, st='FC', **extra):
    d = {'case': case, 'county': 'BROWARD', 'st': st, 'auction': '11/01/2099', 'days': 20, 'tier': 'B',
         'score': 50, 'value': 400000, 'owner': 'Testy Mc Invented', 'addr': '1 Invented Way, Nowhere, FL 33000',
         'phones': [A], 'phtype': ['mobile'], 'phsrc': ['st'], 'phdnc': [False], 'phrank': ['ok'], 'phbest': 0}
    d.update(extra)
    return d


def row(d):
    rows, _ = call_mode.call_rows([d], None, None)
    return rows[0] if rows else None


print('\nROW PAYLOAD')
fc = row(lead('2099-CA-000001', taxDue=5200))
clean = row(lead('2099-CA-000003'))
rec('rows built', bool(fc and clean))
fc, clean = fc or {}, clean or {}
rec('foreclosure + back taxes: no truthy td', not fc.get('td'), fc.get('td'))
rec('foreclosure + back taxes: txd is the dollars', fc.get('txd') == 5200, fc.get('txd'))
rec('foreclosure, no back taxes: no td, no txd', not clean.get('td') and not clean.get('txd'),
    (clean.get('td'), clean.get('txd')))

print('\nTHE COMPOSERS, RUN UNDER NODE')
tpl = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 0, "shipped": 0}')


def fn(name):
    a = html.index('function %s(' % name)
    return html[a:html.index('\n}\n', a) + 3]


# The body builders are replaced by a recorder that throws: only the composer's OWN reading of the
# row is under test, so everything after the builder call never runs.
js = """
function Cap(v){ this.v = v; }
function _mailSender(){ return {name:'', llc:'', phone:''}; }
function _bView(r){ return {auction: r.x || ''}; }
function mailStageCM(){ return 'cold'; }
function _safeStreet(a){ return String(a||'').split(',')[0]; }
function _safeAddr(a){ return String(a||''); }
function _mailSig(){ return ''; }
function firstName(){ return 'Testy'; }
function liveDays(r){ return r.d; }
function textStage(){ return 0; }
function _textTone(){ return 'first'; }
var SENDER = {name:''};
function _mailBodies(v){ throw new Cap(v); }
function _textBodies(v){ throw new Cap(v); }
""" + fn('composeMail') + fn('boardTextBody') + """
function grab(f){ try { f(); } catch(e){ if(e instanceof Cap) return e.v.td; throw e; } return 'builder never called'; }
var R = %s, out = {};
Object.keys(R).forEach(function(k){
  out[k] = {mail: grab(function(){ composeMail(R[k], 'x@example.invalid', 'en'); }),
            text: grab(function(){ boardTextBody(R[k]); })};
});
console.log(JSON.stringify(out));
""" % json.dumps({'fc': fc, 'td': dict(fc, td=1), 'clean': clean})
path = os.path.join(_T, 'td.js')
io.open(path, 'w', encoding='utf-8').write(js)
p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
rec('node ran', p.returncode == 0, p.stderr[-300:])
o = json.loads(p.stdout) if p.returncode == 0 else {}
rec('email composer: foreclosure + back taxes is NOT a tax deed', o.get('fc', {}).get('mail') is False, o.get('fc'))
rec('text composer: foreclosure + back taxes is NOT a tax deed', o.get('fc', {}).get('text') is False, o.get('fc'))
rec('email composer: a tax deed is still a tax deed', o.get('td', {}).get('mail') is True, o.get('td'))
rec('text composer: a tax deed is still a tax deed', o.get('td', {}).get('text') is True, o.get('td'))
rec('composers: plain foreclosure unchanged', o.get('clean') == {'mail': False, 'text': False}, o.get('clean'))

print('\nTHE BACK-TAXES RENDERERS')
rec('lead sheet line reads txd', "if(r.txd) L.push('Back taxes due: ' + money(r.txd));" in html)
rec('money chip reads txd', "if(r.txd)" in html and "Math.round(r.txd).toLocaleString()" in html)
rec('no renderer prints td as dollars', 'money(r.td)' not in html and 'Math.round(r.td)' not in html)

print('\nNO DICT LITERAL REPEATS A KEY')
src = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
dups = []
for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.Dict):
        seen = {}
        for k in node.keys:
            if isinstance(k, ast.Constant):
                key = (type(k.value).__name__, k.value)
                if key in seen:
                    dups.append('%r at line %d (first at line %d)' % (k.value, k.lineno, seen[key]))
                else:
                    seen[key] = k.lineno
rec('call_mode.py: no duplicate constant key in any dict literal', not dups, dups)

print('\n%d failed' % len(fails) if fails else '\nall passed')
sys.exit(1 if fails else 0)
