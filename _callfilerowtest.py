"""Call Mode outcome screen, THE FILE block: the Property type line (F-site-05, 2026-11-13).

The line joined property type / beds / baths / sqft with the HTML entity and THEN escaped the whole
string, so the caller read a literal "&middot;" between the parts for the whole call.

Pins, on the real screenOutcome() of a page built from SOURCE and run under node:
  - the Property type row is on the screen with all four parts
  - the painted HTML of that row carries no double-escaped entity (no "&amp;middot;")
  - the text a person reads carries no literal "&middot;"
  - a hostile property-type value is still escaped (the fix must not un-escape lead text)

Invented lead only, temp DEALFLOW_DIR, no network.
Run: DEALFLOW_DIR=<temp> python _callfilerowtest.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='callfile_')
os.environ.setdefault('DEALFLOW_DIR', _T)
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')

import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


tpl = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 0, "shipped": 0}')
page = os.path.join(_T, 'call.html')
io.open(page, 'w', encoding='utf-8').write(html)

# Invented rows. X carries markup in the property type to prove the value is still escaped.
ROWS = [
    {'c': 'CASE-FILE-1', 'o': 'TEST OWNER', 'a': '100 NW 1st Ave', 'p': ['3055550101'], 'r': [''], 'k': 0,
     'd': 30, 'x': '12/01/2026', 'pk': 'PTF1', 'dd': 'Single Family', 'bd': 3, 'ba': 2, 'sf': 1500},
    {'c': 'CASE-FILE-2', 'o': 'TEST OWNER TWO', 'a': '200 NW 2nd Ave', 'p': ['3055550102'], 'r': [''], 'k': 0,
     'd': 30, 'x': '12/01/2026', 'pk': 'PTF2', 'dd': 'Condo <b>&', 'bd': 2, 'ba': 1, 'sf': 900},
]

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = []; const re = /<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi; let m;
while ((m = re.exec(html)) !== null) if (m[1].trim()) scripts.push(m[1]);
const store = {};
function El() {
  return {
    style: {}, dataset: {}, classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    _inner: '', set innerHTML(v){ this._inner = v; }, get innerHTML(){ return this._inner; },
    set outerHTML(v){}, textContent: '', value: '', onclick: null, onkeydown: null,
    appendChild(){}, addEventListener(){}, focus(){}, click(){},
    querySelectorAll(){ return []; }, querySelector(){ return null; },
    getAttribute(){ return null; }, setAttribute(){}, removeAttribute(){},
  };
}
const elCache = {};
const documentStub = {
  getElementById(id){ return elCache[id] || (elCache[id] = El()); },
  querySelector(){ return El(); }, querySelectorAll(){ return []; },
  createElement(){ return El(); }, addEventListener(){},
  body: El(), documentElement: El(), hidden: false, title: '',
};
const ctx = {
  console, document: documentStub,
  localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
                  removeItem: k => { delete store[k]; } },
  location: { hash: '', href: 'https://example.test/call/', search: '', pathname: '/call/index.html', reload(){} },
  navigator: { userAgent: 'harness', clipboard: { writeText: async () => {} } },
  history: { replaceState(){} },
  alert(){}, confirm(){ return false; }, prompt(){ return null; },
  setTimeout(){ return 0; }, clearTimeout(){}, setInterval(){ return 0; }, clearInterval(){},
  requestAnimationFrame(){}, queueMicrotask(){},
  fetch: async () => { throw new Error('offline harness'); },
  atob: s => Buffer.from(s, 'base64').toString('binary'),
  btoa: s => Buffer.from(s, 'binary').toString('base64'),
  crypto: { getRandomValues(a){ for (let i = 0; i < a.length; i++) a[i] = (i * 7 + 3) & 255; return a; },
            subtle: { importKey: async () => { throw new Error('no subtle in harness'); } } },
  TextEncoder: require('util').TextEncoder, TextDecoder: require('util').TextDecoder,
  JSON, Math, Date, Object, Array, String, Number, Boolean, RegExp, Promise, Error, parseInt,
  parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent, Set, Map,
};
ctx.addEventListener = function(){}; ctx.removeEventListener = function(){};
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);
for (let i = 0; i < scripts.length; i++) {
  try { vm.runInContext(scripts[i], ctx, { filename: 'inline-' + i + '.js', timeout: 20000 }); } catch (e) {}
}
if (typeof ctx.screenOutcome !== 'function') { console.error('PAGE MISSING screenOutcome'); process.exit(2); }
const R = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
store.fcCaller = 'Tester';
ctx.__R = R;
vm.runInContext('ROWS = __R; notes = {};', ctx);
const out = {};
R.forEach(function(r, k){
  ctx.__k = k;
  vm.runInContext('cur = ROWS[__k]; phIdx = 0; screenOutcome();', ctx);
  out[r.c] = documentStub.getElementById('app').innerHTML;
});
console.log(JSON.stringify(out));
"""
hpath = os.path.join(_T, 'harness.js')
io.open(hpath, 'w', encoding='utf-8').write(HARNESS)
rpath = os.path.join(_T, 'rows.json')
io.open(rpath, 'w', encoding='utf-8').write(json.dumps(ROWS))
p = subprocess.run([os.environ.get('NODE_BIN', 'node'), hpath, page, rpath], capture_output=True, text=True,
                   timeout=120, encoding='utf-8')
rec('node ran the real outcome screen', p.returncode == 0, p.stderr[-300:])
o = json.loads(p.stdout) if p.returncode == 0 else {}


def type_row(screen):
    """The <td> holding the Property type value, as painted."""
    key = '<td class="rk">Property type</td><td>'
    a = screen.find(key)
    if a < 0:
        return None
    a += len(key)
    return screen[a:screen.index('</td>', a)]


def as_read(cell):
    """What the browser shows for that cell: one pass of entity decoding, like the HTML parser."""
    import html as _h
    return _h.unescape(cell)


print('\nTHE FILE: PROPERTY TYPE LINE')
s1 = o.get('CASE-FILE-1', '')
rec('outcome screen painted THE FILE', 'THE FILE' in s1 and 'class="reft"' in s1, s1[:200])
c1 = type_row(s1)
rec('Property type row is there', c1 is not None)
c1 = c1 or ''
rec('all four parts are on the line', all(x in c1 for x in ('Single Family', '3BR', '2BA', '1500 sf')), c1)
rec('no double-escaped entity in the painted cell', '&amp;middot;' not in c1, c1)
rec('the text a person reads has no literal &middot;', '&middot;' not in as_read(c1), as_read(c1))
rec('the parts are separated by a middle dot', as_read(c1) == u'Single Family · 3BR · 2BA · 1500 sf', as_read(c1))

c2 = type_row(o.get('CASE-FILE-2', '')) or ''
rec('a property type with markup is still escaped', '<b>' not in c2 and '&lt;b&gt;' in c2 and '&amp;' in c2, c2)
rec('and still reads with no literal &middot;', '&middot;' not in as_read(c2) and u'· 2BR' in as_read(c2), as_read(c2))

print('\n%s' % ('ALL PASS' if not fails else 'FAILS: %d -> %s' % (len(fails), fails)))
sys.exit(1 if fails else 0)
