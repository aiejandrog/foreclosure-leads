"""Morning Worker one-retry token (proposal, 2026-10-22). OFF unless fcAutoRetry === 'true'.

On 10-21 the 8 AM run found the bridge not confirmed up and stopped, and the per-day autostart key
it had already written made every later open that day manual. This guards the proposal that lets a
second scheduled open (e.g. 08:30) try once more, and only after that one skip:
  - the worker writes the retry token only in its bridge_not_confirmed branch;
  - the board spends it only on an armed runner with fcAutoRetry, before 10, once a day;
  - the token is spent before the worker opens, and the stale-board check still applies.
Run: python _autoretrytest.py"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
FAILS = []


def check(name, ok, why=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else '  :: ' + str(why)))
    if not ok:
        FAILS.append(name)


# 1. the worker writes the token in the bridge_not_confirmed branch and nowhere else
writes = [m.start() for m in re.finditer(r'localStorage\.setItem\(_k, "1"\)', SRC)]
check('the worker writes the retry token in exactly one place', len(writes) == 1, len(writes))
bnc = SRC.find('why:"bridge_not_confirmed"')
end = SRC.find("'renderRunBar(); return;'", bnc)
check('that place is the bridge_not_confirmed skip', writes and bnc != -1 and bnc < writes[0] < end,
      (bnc, writes, end))
check('it is keyed by the local day', 'dealflow_autostart_retry_"+_d.getFullYear()' in SRC)
for why in ('bridge_hold', 'stale', 'cap', 'empty'):
    i = SRC.find('why:"%s"' % why)
    if i != -1:
        j = SRC.find('return;', i)
        check('a %s skip does not write the token' % why, 'dealflow_autostart_retry_' not in SRC[i:j])

# 2. the board branch
boot = SRC[SRC.find('if(/worker=morning/.test(location.hash||\'\')){\n    try{ _runlog(\'task_open\''):]
na = boot.find('if(!_armed){')
rb = boot.find('} else if(_already && _retryToken()){')
al = boot.find('} else if(_already){')
check('the retry branch comes after the not-armed block', na != -1 and na < rb, (na, rb))
check('the retry branch comes before the plain already-ran branch', rb != -1 and rb < al, (rb, al))
br = boot[rb:al]
spend, opens = br.find("localStorage.removeItem('dealflow_autostart_retry_'"), br.find('openMorningWorker(')
check('the token is spent before the worker opens', spend != -1 and spend < opens, (spend, opens))
check('the _used mark is written with it', "+ '_used', String(Date.now())" in br)
check('the stale-board check still applies on the retry', 'openMorningWorker(_autoRunLane(), !_stale)' in br)
check('the retry is recorded', "_runlog('retry_after_bridge'" in br)

# 3. _retryToken behaviour under node, with a fake localStorage and clock
fn = re.search(r'function _retryToken\(\)\{.*?\n  \}\n', SRC, re.S)
td = re.search(r'function _today\(\)\{[^\n]*\}', SRC)
check('_retryToken and _today are found', fn and td)
node = shutil.which('node')
if node and fn and td:
    js = '''
var store = {};
var localStorage = {getItem: function(k){ return k in store ? store[k] : null; },
                    setItem: function(k, v){ store[k] = String(v); }, removeItem: function(k){ delete store[k]; }};
var HOUR = 8; var RealDate = Date;
Date = function(){ var d = new RealDate(2026, 9, 22, HOUR, 30, 0); return d; };
%s
%s
var out = {};
function c(name){ out[name] = _retryToken(); }
var K = 'dealflow_autostart_retry_' + _today();
c('nothing set');
store[K] = '1'; c('token but no fcAutoRetry');
store.fcAutoRetry = 'yes'; c('fcAutoRetry not exactly true');
store.fcAutoRetry = 'true'; c('armed and token');
HOUR = 10; c('at 10');
HOUR = 8; store[K + '_used'] = '1'; c('already used');
delete store[K + '_used']; delete store[K]; store['dealflow_autostart_retry_2026-10-21'] = '1'; c('yesterday token');
console.log(JSON.stringify(out));
''' % (td.group(0), fn.group(0))
    r = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        o = {}
        check('node ran', False, r.stderr[:300])
    want = {'nothing set': False, 'token but no fcAutoRetry': False, 'fcAutoRetry not exactly true': False,
            'armed and token': True, 'at 10': False, 'already used': False, 'yesterday token': False}
    for k, v in want.items():
        check('_retryToken: %s -> %s' % (k, v), o.get(k) is v, o.get(k))
else:
    print('SKIP node behaviour checks (node not on PATH)')

print('\n==== %d FAIL(S) ====' % len(FAILS) if FAILS else '\n==== all auto-retry checks passed ====')
sys.exit(1 if FAILS else 0)
