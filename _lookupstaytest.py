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
SHARED_OK, SHARED_HELD, DUP = 'FAKE-2099-000005', 'FAKE-2099-000006', 'FAKE-2099-000007'
DNCDUP, SH2_OK, SH2_HELD = 'FAKE-2099-000009', 'FAKE-2099-000011', 'FAKE-2099-000012'
slim = [lead(HELD, 1), lead(DOCKET, 2, saleBkAct=1), lead(OK, 3), lead(OPTED, 4),
        # one number on a callable case AND a stayed case; the callable one comes first
        lead(SHARED_OK, 5), dict(lead(SHARED_HELD, 6, saleBkAct=1), phones=['(305) 555-0105']),
        # the same case twice, the second copy stayed and carrying an extra number
        lead(DUP, 7), dict(lead(DUP, 7, saleBkAct=1), phones=['(305) 555-0108']),
        # held copy of a case whose only number is DNC: still holds the case's row
        lead(DNCDUP, 9), dict(lead(DNCDUP, 9, saleBkAct=1), phones=['(305) 555-0110'], phdnc=[1]),
        # callable lead first; a stayed lead carries the same number DNC-flagged
        lead(SH2_OK, 11), dict(lead(SH2_HELD, 12, saleBkAct=1), phones=['(305) 555-0111'], phdnc=[1])]
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
["3055550101","3055550102","3055550103","3055550104","3055550105","3055550107","3055550108","3055550109","3055550111"].forEach(function(q){
  phLookup(q).forEach(function(h){ var r=null; var _hs = %s; out[h.c + (q.slice(-2)==="08"?"#2":"")] = {who:h.owner, links:!_hs, why:_hs}; });
});
console.log(JSON.stringify(out));''' % gate.group(1)
res = subprocess.run(['node', '-e', js], capture_output=True, text=True)
assert res.returncode == 0, res.stderr
v = json.loads(res.stdout); print(json.dumps(v, indent=1))
for c in (HELD, DOCKET, OPTED):
    assert c in v and v[c]['who'], c + ' must still be identified'
    assert not v[c]['links'], c + ' must not get Call back / Text'
assert v[OK]['links'], 'control case must keep its links'
assert not v[SHARED_OK]['links'], 'a number a stayed case also carries must not get links'
assert not v[DUP]['links'] and not v[DUP + '#2']['links'], 'a case listed twice takes the held copy'
assert 'h' in idx and '3055550103' not in idx['h'], 'h lists only held numbers on unheld rows'
assert not v[DNCDUP]['links'], 'a held copy with only DNC numbers still holds the case'
assert not v[SH2_OK]['links'], 'a stayed lead holds a shared number even when its copy is DNC'
assert '3055550110' not in idx['d'], 'DNC-only numbers are still never serialized'
print('PASS')
