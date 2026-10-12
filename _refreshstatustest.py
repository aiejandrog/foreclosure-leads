"""_refreshstatustest.py - refresh_status.py reads leads-run.log the way the 2026-10-12 morning needed.

That day the 05:33 run refused at net-ready (rc=3), a later run scraped and ended rc=7, and the
wrapper logged "did not start - cross-machine lease exit 7", which was read as a lease refusal. rc=7
is the inner run's own code passed through runner_lock.py; only 9 and 10 are the lease's.
Then the page script is run in node (when node is on PATH) against a few clocks.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refresh_status as R  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, ('  -- ' + detail) if detail and not ok else ''))
    if not ok:
        FAILS.append(name)


LOG_1012 = """
==================== REFRESH Sun 10/11/2026  5:30:02.11 ====================
[0/5] stuff
==== REFRESH ENDED rc=0 Sun 10/11/2026  7:41:10.02 ====

==================== REFRESH Mon 10/12/2026  5:30:01.50 ====================
    !! NETWORK NOT UP - refusing to start. Nothing scraped, live site untouched.
==== REFRESH ENDED rc=3 Mon 10/12/2026  5:34:12.90 ====
[Mon 10/12/2026  5:34:13.01] refresh-dealflow.bat did not start - cross-machine lease exit 3. See runner-lock.log.

==================== REFRESH Mon 10/12/2026 12:21:05.12 ====================
    !! LP CHAIN exit 4 - pre-foreclosure lane did NOT fully refresh.
==== REFRESH ENDED rc=7 Mon 10/12/2026 16:20:00.00 ====
[Mon 10/12/2026 16:20:00.10] refresh-dealflow.bat did not start - cross-machine lease exit 7. See runner-lock.log.
"""


def write(text):
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'leads-run.log')
    open(p, 'w', encoding='utf-8').write(text)
    return p


def main():
    rs = R.runs(LOG_1012)
    check('three runs, not five (pass-through lease lines are not runs)', len(rs) == 3, str(rs))
    check('run times parsed from cmd %date% %time%', [r['at'] for r in rs] ==
          ['2026-10-11T05:30', '2026-10-12T05:30', '2026-10-12T12:21'], str(rs))
    check('rc read from the ENDED line', [r['rc'] for r in rs] == [0, 3, 7])

    now = datetime(2026, 10, 12, 17, 1)
    s = R.summary(write(LOG_1012), now=now)
    check('last = the 12:21 run, rc 7, lis pendens reason',
          s['last'] == {'at': '2026-10-12T12:21', 'rc': 7, 'why': R.WHY[7]}, str(s['last']))
    check('earlier same-day failure is the 05:30 network refusal',
          s['earlier'] and s['earlier']['rc'] == 3 and s['earlier']['at'] == '2026-10-12T05:30', str(s['earlier']))
    check('good = 12:21 (rc 7 still scraped)', s['good'] == '2026-10-12T12:21', str(s['good']))

    lease = LOG_1012 + '[Mon 10/12/2026 17:30:00.00] refresh-dealflow.bat did not start - cross-machine lease exit 9. See runner-lock.log.\n'
    s = R.summary(write(lease), now=datetime(2026, 10, 12, 17, 31))
    check('lease exit 9 IS a run that did not start', s['last']['rc'] == 9 and s['good'] == '2026-10-12T12:21', str(s))

    running = LOG_1012 + '==================== REFRESH Tue 10/13/2026  5:30:00.00 ====================\n'
    s = R.summary(write(running), now=datetime(2026, 10, 13, 5, 41))
    check('a run with no ENDED line yet is in progress (rc None), and counts as good',
          s['last']['rc'] is None and s['good'] == '2026-10-13T05:30', str(s))
    s = R.summary(write(running), now=datetime(2026, 10, 13, 13, 0))
    check('no ENDED line after 6h = killed (-1), good falls back',
          s['last']['rc'] == -1 and s['good'] == '2026-10-12T12:21', str(s))

    s = R.summary(os.path.join(tempfile.mkdtemp(), 'missing.log'), now=now)
    check('no log: empty summary, no crash', s['last'] is None and s['good'] is None)

    html = R.page_html({'baked': '2026-10-12T17:01', 'good': None, 'last': None, 'earlier': None})
    check('page_html is one div + one script', html.startswith('<div id="rfstat"') and html.count('<script>') == 1)
    check('page_html never raises', R.page_html(object()) == '')
    leak = R.page_html({'baked': '</script><b>', 'good': None, 'last': None, 'earlier': None})
    check('a </script> in the data cannot close the tag', leak.count('</script>') == 1)

    node = shutil.which('node')
    if not node:
        print('SKIP  page script under node (node not on PATH)')
    else:
        def render(s, now_iso):
            js = ("var el={style:{},innerHTML:''};var document={getElementById:function(){return el;}};"
                  "var _N=new Date(%s).getTime();Date.now=function(){return _N;};"
                  "var _D=Date;Date=function(a){return arguments.length?new _D(a):new _D(_N);};"
                  "Date.now=function(){return _N;};Date.prototype=_D.prototype;"
                  % json.dumps(now_iso))
            src = R.page_html(s)
            src = src[src.index('<script>') + 8:src.rindex('</script>')]
            js += src + ";console.log(JSON.stringify({h:el.innerHTML,c:el.style.cssText}));"
            out = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
            return json.loads(out.stdout) if out.returncode == 0 else {'h': 'ERR ' + out.stderr, 'c': ''}

        s = R.summary(write(LOG_1012), now=now)
        r = render(s, '2026-10-12T18:00')
        check('10-12 board: amber, says the 12:21 run had a problem and the 5:30 run failed',
              'Refreshed today 12:21 PM, lis pendens lane partial' in r['h'] and '5:30 AM run failed (no network), recovered' in r['h']
              and '#FBF1DC' in r['c'], r['h'])
        r = render(s, '2026-10-13T20:00')
        check('same board 27h later with nothing rebuilt: red, says how old', 'No refresh since' in r['h']
              and '#B42318' in r['c'], r['h'])
        s2 = R.summary(write(LOG_1012.split('==================== REFRESH Mon 10/12/2026 12:21')[0]),
                       now=datetime(2026, 10, 12, 9, 30))
        r = render(s2, '2026-10-12T09:31')
        check('phones rebuild after a failed 05:30: red FAILED, leads from the 10/11 refresh',
              'Refresh FAILED today 5:30 AM: no network' in r['h'] and 'leads from 10/11 5:30 AM' in r['h']
              and '#B42318' in r['c'], r['h'])
        ok = R.summary(write('==================== REFRESH Mon 10/12/2026  5:30:01.50 ====================\n'
                             '==== REFRESH ENDED rc=0 Mon 10/12/2026  7:41:00.00 ====\n'), now=datetime(2026, 10, 12, 9, 30))
        r = render(ok, '2026-10-12T10:00')
        check('clean morning: green', r['h'] == '&#10003; Refreshed today 5:30 AM' and '#E7F4EA' in r['c'], r['h'])

    print('\n%d check(s) failed' % len(FAILS) if FAILS else '\nall passed')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
