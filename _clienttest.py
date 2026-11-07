"""Existing clients are not leads (clients.py, 2026-11-06).

Pins:
  - clients.load: missing file = nobody, a listed case matches whatever its punctuation, an
    unreadable file = nobody and says so (never raises)
  - stamp() sets client=1 on listed rows only; no person or household spread (same surname, other
    case, stays a lead)
  - Call Mode: a client is in no dial lane (so no mail/knock route) and not in Find a number; its
    coverage row carries client=1; the lookup screen gets 'CLIENT', not a do-not-contact reason
  - the board's funnel classifier (extracted verbatim into Call Mode) puts a client in OUT
  - board template: isFlaggedDead / mail-held / card banner read r.client; Call Mode page has the
    CLIENT bar on the lookup screen and the board-lane list
  - Whitepages contact_hold and the skip-trace exclusion refuse to spend on a client
  - nothing here writes an opt-out or a do-not-contact

Invented cases and names only.
Run: python _clienttest.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='clienttest_')
os.environ.setdefault('DEALFLOW_DIR', _T)
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')

import clients  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


CL = '502099CA000111XXXAMB'
OTHER = '502099CA000222XXXAMB'
CF = os.path.join(_T, 'clients.json')
clients.CLIENTS_FILE = CF

print('\nLIST')
rec('missing file = nobody', clients.load() == {} and not clients.is_client(CL))
json.dump({CL: {'d': '2026-11-06', 'by': 'test', 'note': 'never baked'}}, open(CF, 'w'))
rec('listed case matches', clients.is_client(CL))
rec('punctuation does not matter', clients.is_client('502099-ca-000111-xxxamb'))
rec('other case is not a client', not clients.is_client(OTHER))
rec('row flag alone counts', clients.is_client({'case': OTHER, 'client': 1}))
rows = [{'case': CL, 'owners': 'TESTER ALPHA'}, {'case': OTHER, 'owners': 'TESTER BETA'}]
rec('stamp marks the listed case only', clients.stamp(rows) == 1 and rows[0].get('client') == 1
    and 'client' not in rows[1], rows)
rec('stamp bakes no note or by', set(rows[0]) == {'case', 'owners', 'client'}, rows[0])
open(CF, 'w').write('{not json')
rec('unreadable file = nobody, no raise', clients.load() == {} and clients.error() != '')
json.dump({CL: {'d': '2026-11-06'}}, open(CF, 'w'))
os.utime(CF, None)
rec('fixed file reads again', clients.is_client(CL))

print('\nCALL MODE')
import call_mode  # noqa: E402

A, B = '3055550101', '3055550102'


def lead(case, ph, **extra):
    d = {'case': case, 'county': 'PALM BEACH', 'st': 'FC', 'auction': '11/01/2026', 'days': 20, 'tier': 'B',
         'score': 50, 'value': 400000, 'phones': [ph], 'phtype': ['mobile'], 'phsrc': ['st'], 'phdnc': [False],
         'phrank': ['ok'], 'phbest': 0, 'owners': 'TESTER ' + case[-4:]}
    d.update(extra)
    return d


plain, cl = lead(OTHER, A), lead(CL, B, client=1)
rows, _ = call_mode.call_rows([plain], None, None)
rec('fixture: a plain lead reaches the dial list', len(rows) == 1, rows)
rows, _ = call_mode.call_rows([plain, cl], None, None)
rec('client is in no dial lane', [r['c'] for r in rows] == [OTHER], [r.get('c') for r in rows])
nophone = lead(CL, B, client=1)
nophone['phones'] = []
plain_np = lead(OTHER, A)
plain_np['phones'] = []
lk = []
call_mode.call_rows([plain_np, nophone], None, None, lookup=lk)
rec('fixture: a plain no-phone lead reaches Find a number', any(x.get('c') == OTHER for x in lk), lk)
rec('client is not in Find a number', not any(x.get('c') == CL for x in lk), lk)
cov, _ = call_mode.coverage_rows([plain, cl], [OTHER])
cr = [x for x in cov if x.get('c') == CL]
rec('coverage row carries client=1 and is not opted out', cr and cr[0].get('client') == 1 and not cr[0].get('oo'), cov)
hold = call_mode.lookup_hold_fn([plain, cl])
rec('lookup screen hold is CLIENT', hold(cl) == 'CLIENT' and hold(plain) == '', (hold(cl), hold(plain)))
idx = call_mode.phone_index([plain, cl], hold)
t = {row[2]: row for row in idx['t']}
rec('phone index identifies the client with CLIENT', t.get(CL, [None] * 8)[7] == 'CLIENT', idx)

print('\nFUNNEL (board classifier, as Call Mode extracts it)')
tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
js = call_mode.extract_funnel_js(tpl)
node = shutil.which('node')
if node:
    prog = ('var notes={}; var AUDIT={}; var WRONG_OWNER={};\n' + js + '\n'
            "var base={case:'X1', auction:'', days:9999, phones:[1], emails:[]};\n"
            "var c=Object.assign({}, base, {case:'X2', client:1});\n"
            "console.log(JSON.stringify([_funnelStage(base), _funnelStage(c)]));")
    p = subprocess.run([node, '-e', prog], capture_output=True, text=True, timeout=60)
    try:
        a, b = json.loads(p.stdout.strip().splitlines()[-1])
        rec('client lands in OUT, plain lead does not', b == 'out' and a != 'out', (a, b))
    except Exception as e:
        rec('funnel ran under node', False, (e, p.stdout[-300:], p.stderr[-300:]))
else:
    rec('funnel source sends client to out', "if(r.client) return 'out';" in js)

print('\nPAGES')
i = tpl.index('function isFlaggedDead(r){')
rec('board isFlaggedDead reads r.client', 'if(r.client) return true;' in tpl[i:i + 2500])
rec('board mail-held names an existing client', "if(r.client) return 'existing client';" in tpl)
rec('board card paints a CLIENT banner', 'const clientBan = r.client' in tpl and "+who+clientBan+" in tpl)
src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
rec('Call Mode lookup paints the CLIENT bar', "if(_hs === 'CLIENT') return clientBar();" in src)
rec('Call Mode board-lane list says CLIENT', ': r.client ? ' in src)
rec('the CLIENT bar is not a do-not-contact', 'DO NOT CONTACT' not in src[src.index('function clientBar'):
                                                                         src.index('function screenLead')])

print('\nPAID LOOKUPS')
import whitepages_lookup as WL  # noqa: E402
WL.stay_gate = None
rec('Whitepages will not spend on a client', WL.contact_hold({'Case #': CL}) == 'existing client',
    WL.contact_hold({'Case #': CL}))
import skiptrace as ST  # noqa: E402
rec('skip trace holds a client', 'outreach_held' in ST.exclusion_reasons({'case': CL}, None),
    ST.exclusion_reasons({'case': CL}, None))
rec('skip trace leaves a plain lead alone', 'outreach_held' not in ST.exclusion_reasons({'case': OTHER}, None),
    ST.exclusion_reasons({'case': OTHER}, None))

print('\nNO SUPPRESSION WRITE')
cs = open(os.path.join(HERE, 'clients.py'), encoding='utf-8').read()
rec('clients.py writes nothing', 'json.dump' not in cs and "open(p, 'w'" not in cs and 'ledger_add' not in cs)

shutil.rmtree(_T, ignore_errors=True)
print('\n%d failure(s)' % len(fails) if fails else '\nALL PASS')
sys.exit(1 if fails else 0)
