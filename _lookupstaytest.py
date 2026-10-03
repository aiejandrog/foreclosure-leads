"""Call Mode "Who texted me?" lookup must not offer Call back / Text on a held case (2026-10-03).

phone_index ships every lead with a phone so the lookup can IDENTIFY a caller, but a case the dial
queue drops for a stay or suppression reason (never-contact / federal hold, docket bankruptcy stay,
ledger opt-out) must render DO NOT CONTACT instead of tel:/sms: links. A callable control case keeps
its links. Runs the real phLookup / hardSuppressed JS and the real lookup gate expression in node.
Fake cases only; no network, no contact.   python _lookupstaytest.py
"""
import json, re, subprocess, sys, types, os

repo = sys.argv[1] if len(sys.argv) > 1 else '.'
sys.path.insert(0, os.path.abspath(repo))
HELD, DOCKET, OPTED, OK = 'FAKE-2099-000001', 'FAKE-2099-000002', 'FAKE-2099-000004', 'FAKE-2099-000003'
bk = types.ModuleType('bk_lookup')
bk.federal_hold_index = lambda: object()
bk.federal_hold = lambda case, index=None, here=None: ((True, 'never_contact') if case == HELD else (False, ''))
sys.modules['bk_lookup'] = bk
import call_mode as cm

def lead(case, n, **kw):
    d = {'case': case, 'phones': ['(305) 555-01%02d' % n], 'phdnc': [0], 'oname': 'FAKE OWNER %d' % n,
         'addr': '%d Fake St, Miami' % n, 'days': 10}
    d.update(kw); return d
slim = [lead(HELD, 1), lead(DOCKET, 2, saleBkAct=1), lead(OK, 3), lead(OPTED, 4)]
optouts = {OPTED: {'ts': '2099-01-01'}}

idx = cm.phone_index(slim, cm.lookup_hold_fn(slim, optouts, {}))
src = open(os.path.join(repo, 'call_mode.py'), encoding='utf-8').read()
def js_fn(name):
    m = re.search(r'\nfunction ' + name + r'\(.*?\n}\n', src, re.S); assert m, name; return m.group(0)
gate = re.search(r"var _hs = (h\.h \|\| hardSuppressed\(r \|\| \{c:h\.c, p:\[h\.num\]\}\));", src)
assert gate, 'lookup gate expression not found: patch not applied'
js = ('var notes={}, _OPTPH=null, PHIDX=%s;\nfunction digitsOf(q){return String(q||"").replace(/\\D/g,"");}\n'
      % json.dumps(idx)) + js_fn('optPhones') + js_fn('hardSuppressed') + js_fn('phLookup') + '''
var out = {};
["3055550101","3055550102","3055550103","3055550104"].forEach(function(q){
  phLookup(q).forEach(function(h){ var r=null; var _hs = %s; out[h.c] = {who:h.owner, links:!_hs, why:_hs}; });
});
console.log(JSON.stringify(out));''' % gate.group(1)
res = subprocess.run(['node', '-e', js], capture_output=True, text=True)
assert res.returncode == 0, res.stderr
v = json.loads(res.stdout); print(json.dumps(v, indent=1))
for c in (HELD, DOCKET, OPTED):
    assert c in v and v[c]['who'], c + ' must still be identified'
    assert not v[c]['links'], c + ' must not get Call back / Text'
assert v[OK]['links'], 'control case must keep its links'
print('PASS')
