#!/usr/bin/env python
"""_releaseexpirytest -- a Broward / Palm Beach bankruptcy release ends on the page, not only on the server.

Run:  python _releaseexpirytest.py    (exit 0 = pass; no network, synthetic case numbers, no names)

2026-10-12 (ChatGPT's stress test, "operational race"): email and text are re-checked by the send
bridge at send time, but the board and Call Mode bake the release when they are built and a phone
call has no server check. A page left open, or a decrypted copy cached on a phone, kept a released
lead callable after its PACER clear expired or a later hold landed. The bake now stamps bkUntil
(bk_lookup.release_until) and both pages hold the lead once it passes.
"""
import pathlib
import re
import subprocess
import sys
import time
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  -- %r' % (detail,)))
    if not cond:
        FAILS.append(name)


# ---- 1 the bake stamps bkUntil on released non-Miami rows only
try:
    import playwright.sync_api  # noqa: F401  (foreclosure_leads imports it at module level)
except ImportError:              # a box without Playwright: this suite never drives a browser
    _pw = types.ModuleType('playwright')
    _pws = types.ModuleType('playwright.sync_api')
    _pws.sync_playwright = None
    sys.modules['playwright'], sys.modules['playwright.sync_api'] = _pw, _pws
import foreclosure_leads as FL

NOW = time.time()
stub = types.ModuleType('bk_lookup')
stub.FLAGS_DEGRADED = ''
stub.flags_for_cases = lambda cases: {'CACE-99-700002': {'hold': True, 'why': 'held', 'hard': False}}
stub.release_until = lambda key: None if key.startswith('2099-') else int(NOW + 3600)
real = sys.modules.get('bk_lookup')
sys.modules['bk_lookup'] = stub
try:
    slim = [{'case': 'CACE-99-700001'}, {'case': 'CACE-99-700002'}, {'case': '2099-700003-CA-01'},
            {'case': 'CACE-99-700004', 'saleBkAct': True}]
    FL.stamp_federal_bk(slim)
finally:
    if real is not None:
        sys.modules['bk_lookup'] = real
    else:
        sys.modules.pop('bk_lookup', None)
check('released Broward row gets bkUntil', slim[0].get('bkUntil') == int(NOW + 3600), slim[0])
check('held row gets no bkUntil (it is held already)', 'bkUntil' not in slim[1] and slim[1]['saleBkAct'], slim[1])
check('Miami-Dade stem row gets no bkUntil', 'bkUntil' not in slim[2], slim[2])
check('a row a docket stay already held gets no bkUntil', 'bkUntil' not in slim[3], slim[3])

stub2 = types.ModuleType('bk_lookup')
stub2.FLAGS_DEGRADED = ''
stub2.flags_for_cases = lambda cases: {}


def _boom(key):
    raise RuntimeError('x')


stub2.release_until = _boom
sys.modules['bk_lookup'] = stub2
try:
    slim = [{'case': 'CACE-99-700005'}]
    t0 = int(time.time())
    FL.stamp_federal_bk(slim)
finally:
    if real is not None:
        sys.modules['bk_lookup'] = real
    else:
        sys.modules.pop('bk_lookup', None)
check('release_until raising -> bkUntil is now (expired on the page, held)',
      slim[0].get('bkUntil') and slim[0]['bkUntil'] <= int(time.time()) and slim[0]['bkUntil'] >= t0, slim[0])

# ---- 2 the pages: the board holds, Call Mode drops, past bkUntil


def js_fn(src, name):
    i = src.index('function ' + name + '(')
    depth, j = 0, src.index('{', i)
    for k in range(j, len(src)):
        depth += {'{': 1, '}': -1}.get(src[k], 0)
        if depth == 0:
            return src[i:k + 1]
    raise ValueError(name)


board = (HERE / 'tracker_template.html').read_text(encoding='utf-8')
cm = (HERE / 'call_mode.py').read_text(encoding='utf-8')
js = ('const BUILTAT = "2026-10-12 07:00";\n' + js_fn(board, '_expireReleases') + '\n' + js_fn(cm, '_dropExpired') + '\n'
      'const now = Date.now()/1000;\n'
      'const b = _expireReleases([{case:"A", bkUntil: now-10, saleLift:"x"}, {case:"B", bkUntil: now+3600},'
      ' {case:"C"}, {case:"D", bkUntil: now-10, saleBkAct:true, bkWhy:"docket"}]);\n'
      'const c = _dropExpired([{c:"A", bu: now-10}, {c:"B", bu: now+3600}, {c:"C"}]);\n'
      'console.log(JSON.stringify({b:b.map(r=>[r.case, !!r.saleBkAct, r.bkWhy||"", "saleLift" in r]), c:c.map(r=>r.c)}));\n')
try:
    out = subprocess.run(['node', '-e', js], capture_output=True, text=True, timeout=60)
    res = __import__('json').loads(out.stdout.strip() or '{}')
except Exception as e:
    res = {'err': str(e)}
b = {r[0]: r for r in res.get('b', [])}
check('board: an expired release is held, with a reason, and loses its stay-lifted lane',
      b.get('A', [0, 0])[1] is True and 'expired' in b['A'][2] and b['A'][3] is False, res)
check('board: an unexpired release stays released', b.get('B', [0, 1])[1] is False, res)
check('board: a row with no bkUntil (Miami) is untouched', b.get('C', [0, 1])[1] is False, res)
check('board: a docket-held row keeps its own reason', b.get('D', [0, 0, ''])[2] == 'docket', res)
check('Call Mode: the expired row leaves the queue, the others stay', res.get('c') == ['B', 'C'], res)
check('Call Mode ships bu on dial rows', "'bu': d.get('bkUntil') or None" in cm)
check('both payload paths of Call Mode run _dropExpired', cm.count('_dropExpired(') >= 3)
check('the board applies it to both the plain and the unlocked payload',
      'let DATA = _expireReleases(' in board and 'return _expireReleases((pl && pl.rows)' in board
      and 'if(Array.isArray(pl)) return _expireReleases(pl);' in board)

# ---- 3 a page left open: the row expires mid-session, not only at the next load
js2 = ('var renders = 0; function render(){ renders++; }\n' + js_fn(cm, '_dropExpired') + '\n'
       + js_fn(cm, '_expireTick') + '\n'
       'var ROWS = [{c:"A", bu: Date.now()/1000 + 0.2}, {c:"B", bu: Date.now()/1000 + 3600}];\n'
       '_expireTick(); var before = [ROWS.length, renders];\n'
       'setTimeout(function(){ _expireTick(); console.log(JSON.stringify({before: before, after: [ROWS.map(r=>r.c), renders]})); }, 400);\n')
try:
    out = subprocess.run(['node', '-e', js2], capture_output=True, text=True, timeout=60)
    r2 = __import__('json').loads(out.stdout.strip() or '{}')
except Exception as e:
    r2 = {'err': str(e)}
check('Call Mode open tab: nothing expired -> no repaint; once bu passes, the row leaves ROWS and the screen repaints',
      r2.get('before') == [2, 0] and r2.get('after') == [['B'], 1], r2)
check('Call Mode re-checks on a timer and on return to the tab',
      'setInterval(_expireTick, 60*1000)' in cm and "_expireTick(); });" in cm)
check('board re-checks on a timer and on return to the tab, and repaints only on a change',
      'setInterval(_expTick, 60*1000)' in board and '_expTick(); _dayTick();' in board
      and 'try{ recompute(); }catch(e){} try{ render(); }catch(e){}' in board)

print()
print('==== %d FAIL(S) ====' % len(FAILS) if FAILS else '==== all release-expiry checks passed ====')
sys.exit(1 if FAILS else 0)
