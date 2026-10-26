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
  BROWARD: 319 rows
  PALM BEACH: 402 rows
  MIAMI-DADE: 88 rows
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
    check('county rows from that run, fixed order', s['rows'] == [['Broward', 319], ['Miami', 88], ['Palm Beach', 402]], str(s['rows']))

    # rc 7 says only that some step of the lis pendens chain was not clean. The chain's own lines
    # say which, and the 10-13 morning (paid-read cap spent, Palm Beach captcha refused) needed that.
    lp_1013 = LOG_1012.replace(
        '    !! LP CHAIN exit 4',
        '  PALM BEACH sweep blocked/portal down \u2014 other counties (if any) still merge.\n'
        'PARTIAL SWEEP: MIAMI-DADE, PALM BEACH did not run. The rows above are from BROWARD only; x\n'
        'CHAIN DONE.\n'
        'DEGRADED RUN \u2014 the chain finished, but not everything ran:\n'
        '  - SWEEP (lis_pendens, all 3 counties) (exit 4)\n'
        '  - PHONES (LP fast lane) (exit 2)\n'
        '\n'
        '    !! LP CHAIN exit 4')
    s = R.summary(write(lp_1013), now=now)
    check('rc 7 names the counties whose filings were not pulled, and the phone step',
          s['last']['why'] == 'no new lis pendens filings pulled for Miami or Palm Beach; phones for new filings not fully traced',
          str(s['last']))
    s = R.summary(write(LOG_1012.replace('    !! LP CHAIN exit 4',
                                         'CHAIN STOPPED at VALUE (lp_values) (exit 1) \u2014 nothing after it ran\n'
                                         '    !! LP CHAIN exit 1')), now=now)
    check('rc 7 from a stopped chain names the step', s['last']['why'] ==
          'lis pendens chain stopped at value, later steps did not run', str(s['last']))
    s = R.summary(write(LOG_1012.replace('    !! LP CHAIN exit 4', 'no LP sweep completed \u2014 every source blocked.')),
                  now=now)
    check('every county blocked', s['last']['why'] == 'no new lis pendens filings pulled in any county', str(s['last']))
    s = R.summary(write(LOG_1012.replace('    !! LP CHAIN exit 4',
                                         'sweep ran, no new filings in the window. FAILED: BROWARD\n')), now=now)
    check('empty sweep with a failed county', s['last']['why'] ==
          'no new lis pendens filings pulled for Broward', str(s['last']))
    s = R.summary(write(LOG_1012.replace('    !! LP CHAIN exit 4', '  - foo (exit 2)\nDEGRADED RUN \u2014 x\n'
                                         '  - RESOLVE BROWARD PARCEL (mortgage PIN) (exit 2)\nother line\n  - bar (exit 3)')),
                  now=now)
    check('only lines under DEGRADED RUN count', s['last']['why'] == 'Broward parcel match skipped', str(s['last']))
    s = R.summary(write(LOG_1012.replace('rc=7', 'rc=0').replace('    !! LP CHAIN exit 4',
                                         'PARTIAL SWEEP: BROWARD did not run.')), now=now)
    check('lp lines do not change a clean run', s['last']['why'] == 'ok', str(s['last']))

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

    manual = LOG_1012.split('==================== REFRESH Mon 10/12/2026 12:21')[0] + (
        '==== run Mon 10/12/2026  9:10:00.00 ==== \n'
        'scraping...\n')
    s = R.summary(write(manual), now=datetime(2026, 10, 12, 9, 40))
    check('run-leads.bat after a failed 05:30: the manual run baking its board is the last run, good',
          s['last']['at'] == '2026-10-12T09:10' and s['last']['rc'] is None and s['good'] == '2026-10-12T09:10'
          and s['earlier']['rc'] == 3, str(s))
    s = R.summary(write(manual + '==== done ==== \n'), now=datetime(2026, 10, 12, 9, 40))
    check('run-leads.bat done = rc 0', s['last']['rc'] == 0, str(s))
    s = R.summary(write(manual + 'SCRAPE FAILED - skipping commit/push, live site left intact \n'),
                  now=datetime(2026, 10, 12, 9, 40))
    check('run-leads.bat SCRAPE FAILED = rc 1, good stays 10/11', s['last']['rc'] == 1
          and s['good'] == '2026-10-11T05:30', str(s))
    killed = LOG_1012.replace('==== REFRESH ENDED rc=7 Mon 10/12/2026 16:20:00.00 ====\n', '') + (
        '==================== REFRESH Mon 10/12/2026 17:00:00.00 ====================\n'
        '==== REFRESH ENDED rc=3 Mon 10/12/2026 17:04:00.00 ====\n')
    s = R.summary(write(killed), now=datetime(2026, 10, 12, 17, 30))
    check('an earlier run with no ENDED line is not counted as good', s['good'] == '2026-10-11T05:30', str(s))

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
              'Refreshed today 12:21 PM, lis pendens lane partial &middot; auction lists Broward 319, Miami 88, Palm Beach 402' in r['h'] and '5:30 AM run failed (no network), recovered' in r['h']
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

    if node:
        def live(s, now_iso, j):
            js = ("var _N=new Date(%s).getTime();var _D=Date;Date=function(a){return arguments.length?new _D(a):new _D(_N);};"
                  "Date.now=function(){return _N;};Date.prototype=_D.prototype;"
                  "var W={document:{hidden:false},setInterval:function(){},setTimeout:function(){return 1;},clearTimeout:function(){},"
                  "fetch:function(u){W.url=u;return Promise.resolve({json:function(){return %s;}});}};"
                  "var el={style:{},innerHTML:'',ownerDocument:{defaultView:W}};"
                  "var document={getElementById:function(){return el;}};var window=W;"
                  % (json.dumps(now_iso), json.dumps(j)))
            src = R.page_html(s)
            src = src[src.index('<script>') + 8:src.rindex('</script>')]
            js += src + ";setTimeout(function(){console.log(JSON.stringify({h:el.innerHTML,c:el.style.cssText,u:W.url,f:typeof W.__dfRfs}));},20);"
            out = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
            return json.loads(out.stdout) if out.returncode == 0 else {'h': 'ERR ' + out.stderr, 'c': '', 'u': '', 'f': ''}

        base = {'baked': '2026-10-24T05:41', 'good': '2026-10-24T05:30', 'rows': [],
                'last': {'at': '2026-10-24T05:30', 'rc': None, 'why': None}, 'earlier': None}
        run = {'ok': True, 'state': 'running', 'running': True, 'started': '2026-10-24T11:36:02', 'elapsed': 4320,
               'eta': 3900, 'pct': 0.52, 'n': 57, 'done': 22,
               'step': {'i': 23, 'key': '3b', 'tag': '3b/5', 'label': 'Skip-tracing <owner> phones', 'secs': 260}}
        r = live(base, '2026-10-24T12:48', run)
        check('live: asks the local bridge', r['u'] == 'http://127.0.0.1:8823/refresh', r['u'])
        check('live: a running refresh paints step, time in, time left and a bar',
              'Refresh running' in r['h'] and 'step 23 of 57' in r['h'] and '1h12m in' in r['h']
              and 'about 1h05m left' in r['h'] and 'width:52%' in r['h'] and '#E8F0FE' in r['c'], r['h'])
        check('live: the step label is escaped', '&lt;owner&gt;' in r['h'] and '<owner>' not in r['h'], r['h'])
        check('live: the function is shared for the Morning Worker', r['f'] == 'function', r['f'])
        fin = {'ok': True, 'state': 'ended', 'running': False, 'started': '2026-10-24T11:36:02',
               'ended': '2026-10-24T14:48:00', 'elapsed': 11518, 'rc': 0, 'why': 'ok', 'n': 57, 'done': 57}
        r = live(base, '2026-10-24T15:00', fin)
        check('live: a newer finished run offers a reload', 'Refresh finished today 2:48 PM (took 3h11m)' in r['h']
              and 'Reload' in r['h'], r['h'])
        mine = dict(base, baked='2026-10-24T13:10', last={'at': '2026-10-24T11:36', 'rc': None, 'why': None})
        r = live(mine, '2026-10-24T15:00', fin)
        check('live: no reload offered on the page that run built', 'Reload' not in r['h']
              and 'Refreshed today 11:36 AM' in r['h'], r['h'])
        r = live(base, '2026-10-24T15:00', dict(fin, rc=6, why='publish guard blocked it'))
        check('live: a blocked publish says the live site kept the last good board',
              'did not publish: publish guard blocked it' in r['h'] and '#FBF1DC' in r['c'], r['h'])
        r = live(base, '2026-10-24T15:00', {'ok': True, 'state': 'stale', 'running': False,
                                             'started': '2026-10-24T11:36:02', 'n': 57})
        check('live: a run that stopped without finishing says so', 'stopped without finishing' in r['h'], r['h'])
        r = live(base, '2026-10-24T15:00', {'ok': False})
        check('live: a bridge that cannot answer keeps the baked line', r['h'] == '&#10003; Refreshed today 5:30 AM', r['h'])

    print('\n%d check(s) failed' % len(FAILS) if FAILS else '\nall passed')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
