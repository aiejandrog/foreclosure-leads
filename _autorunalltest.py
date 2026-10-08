"""The 8am Morning Worker auto-run works ALL lanes (2026-10-08, Alejandro: "auto-run all lanes, i
would like for the morning worker when it pops up to automatically run all lanes").

Static checks on tracker_template.html, no browser, no data. The browser half (which lane
_autoRunLane() picks) is in _workerreachtest.py.

Pins:
  - the armed, fresh-board 8am path opens _autoRunLane(), not a hard-coded 'urgent'
  - the auto-start sets autoAll=true, so the run walks every later lane (same walk as the button)
  - REPLIED is never the auto-run's opening lane
  - the unarmed-device and already-ran-today paths still open in MANUAL mode (autostart false)
  - the stale-board gate still decides autostart
"""
import pathlib, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
T = pathlib.Path(__file__).with_name('tracker_template.html').read_text(encoding='utf-8')
ok, bad = [], []
def rec(n, c, d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))

rec('armed fresh path opens the auto-run lane', "openMorningWorker(_autoRunLane(), !_stale);" in T)
rec("no armed path still forces 'urgent' with autostart on",
    not re.search(r"openMorningWorker\('urgent',\s*(?:true|!)", T))
m = re.search(r"function _autoRunLane\(\)\{(.*?)\n\}", T, re.S)
rec('_autoRunLane exists', bool(m))
body = m.group(1) if m else ''
rec('_autoRunLane never opens REPLIED', "'replied'" not in body, body[:80])
rec('_autoRunLane opens the first lane whose real queue is non-empty (no earlier lane skipped)',
    "_workerQueue(k).length > 0" in body)
rec('_autoRunLane falls back to urgent', "var pick = 'urgent';" in body)
a = T.find("'if(AUTOSTART && Q.length){'")
blk = T[a:a + 6000]
rec('auto-start block found', a > 0)
rec('auto-start sets autoAll=true before runOne', re.search(r"auto=true; autoAll=true;.*?runOne\(\)", blk, re.S) is not None)
rec('countdown says all lanes', 'all lanes from "+lane.toUpperCase()+"' in blk)
rec('unarmed device stays manual', T.count("openMorningWorker('urgent', false);") >= 2)
rec('stale board still blocks the unattended start', "var _stale = (_age != null && _age >= 1);" in T)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
