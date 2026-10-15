"""refresh_status.py - the one line at the top of the board and Call Mode that says how the last
refresh went.

WHY (2026-10-12). The 05:30 refresh refused at net-ready (rc=3, no DNS), a later run scraped and
ended rc=7, and nobody looking at the board could tell either from a clean morning. The laptop's
own reading of that day was wrong twice: first "no scrape today", then "rc=7 is the lease refusing a
second scrape". rc=7 is refresh-dealflow.bat's own RUNEXIT (the lis pendens chain did not fully
refresh), which runner_lock.py passes through unchanged; the outer wrapper then logs it as
"did not start - cross-machine lease exit 7", which is only true for exit 9 or 10. So this file
reads the ENDED line for the verdict and counts a lease line only when it carries 9 or 10.

Source: leads-run.log only (the REFRESH banners, the "REFRESH ENDED rc=N" lines, lease
refusals). Nothing new is written anywhere. leads_final.json's mtime is NOT used: two dozen scripts
rewrite that file, so its age says nothing about the last scrape.
The bake time is the board's own build time; the page compares it with the viewer's clock, so a
board that nothing rebuilt still turns red by itself when it gets old.

Counts and times only. No name, phone, address or case number reaches the page from here.

    python refresh_status.py          # print what the line would say right now
"""
import json
import os
import re
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'leads-run.log')
TAIL_BYTES = 400 * 1024

# refresh-dealflow.bat RUNEXIT, and runner_lock.py's own refusals (NOT_ACQUIRED 9, LOST 10).
WHY = {
    0: 'ok',
    1: 'scrape failed',
    2: 'compliance block',
    3: 'no network',
    4: 'a source is down',
    5: 'live site did not update',
    6: 'publish guard blocked it',
    7: 'lis pendens lane partial',
    9: 'run lease refused',
    10: 'lease lost mid-run',
}
# Codes where no new scrape reached the board.
FAILED = {1, 3, 9, 10}

_START = re.compile(r'^=+ REFRESH (?!ENDED)(.*?) =+\s*$')
_ENDED = re.compile(r'^=+ REFRESH ENDED rc=(-?\d+)(.*?)=*\s*$')
_LEASE = re.compile(r'^\[(.*?)\] (?:refresh-dealflow|run-leads)\.bat did not start - cross-machine lease exit (\d+)')
# run-leads.bat, the manual scrape + publish: "==== run <date> <time> ====" ... "==== done ====",
# with no ENDED line, so its outcome is read from the lines it prints.
_MANUAL = re.compile(r'^=+ run (.*?) =+\s*$')
# The scrape's per-county row counts, as foreclosure_leads.py prints them ("BROWARD: 319 rows").
_COUNTY = re.compile(r'\b(MIAMI-DADE|BROWARD|PALM BEACH):\s*(\d+)\s*row')
_MANUAL_DONE = re.compile(r'^=+ done =+\s*$')
# What lp_refresh.py and lis_pendens.py print when the lane does not fully refresh (rc 7). rc 7 alone
# says only that SOME step of an eight-script chain was not clean; these lines say which.
_LP_PARTIAL = re.compile(r'^PARTIAL SWEEP: (.*?) did not run')
_LP_NONE_NEW = re.compile(r'^sweep ran, no new filings in the window\. FAILED: (.*)$')
_LP_ALL = re.compile(r'^no LP sweep completed')
_LP_STOPPED = re.compile(r'^CHAIN STOPPED at (.*?) \(exit (-?\d+)\)')
_LP_DEGRADED = re.compile(r'^- (.*?) \((?:exit -?\d+|no loadable skip-trace key[^)]*)\)$')
_LP_COUNTY = {'MIAMI-DADE': 'Miami', 'BROWARD': 'Broward', 'PALM BEACH': 'Palm Beach'}
# cmd's %date% %time% on this laptop: "Mon 10/12/2026 12:21:05.12" (en-US). ISO is accepted too.
_US = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})\D+(\d{1,2}):(\d{2})')
_ISO = re.compile(r'(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})')


def _when(text):
    """'2026-10-12T05:33' from a cmd date/time string, or None."""
    m = _US.search(text or '')
    if m:
        mo, d, y, h, mi = (int(x) for x in m.groups())
    else:
        m = _ISO.search(text or '')
        if not m:
            return None
        y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi).strftime('%Y-%m-%dT%H:%M')
    except ValueError:
        return None


def runs(log_text):
    """Each refresh in the log, oldest first: {'at', 'rc', 'why'}. rc None = started, no ENDED line
    yet (still running, or killed from outside)."""
    out = []
    manual = None                     # the open run-leads.bat run, if the last banner was one
    for line in log_text.splitlines():
        line = line.strip()
        _lp_line(out[-1] if out and out[-1]['rc'] is None else None, line)
        m = _COUNTY.search(line)
        if m and out and (out[-1]['rc'] is None or out[-1] is manual):
            out[-1].setdefault('rows', {})[m.group(1)] = int(m.group(2))
        m = _MANUAL.match(line)
        if m:
            manual = {'at': _when(m.group(1)), 'rc': None, 'end': None}
            out.append(manual)
            continue
        if manual is not None and manual['rc'] is None:
            if 'NETWORK NOT UP' in line:
                manual['rc'] = 3
            elif line.startswith('SCRAPE FAILED'):
                manual['rc'] = 1
            elif 'MIRROR DID NOT PUBLISH' in line:
                manual['mirror'] = True
            elif _MANUAL_DONE.match(line):
                manual['rc'] = 5 if manual.get('mirror') else 0
            if manual['rc'] is not None:
                manual = None
                continue
        m = _ENDED.match(line)
        if m:
            rc = int(m.group(1))
            if out and out[-1]['rc'] is None:
                out[-1]['rc'] = rc
                out[-1]['end'] = _when(m.group(2))
            else:   # an ENDED with no banner above it in the tail we read
                out.append({'at': _when(m.group(2)), 'rc': rc, 'end': _when(m.group(2))})
            continue
        m = _START.match(line)
        if m:
            manual = None
            out.append({'at': _when(m.group(1)), 'rc': None, 'end': None})
            continue
        m = _LEASE.match(line)
        # Any other code on this line is the INNER run's own exit, already on its ENDED line.
        if m and int(m.group(2)) in (9, 10):
            t = _when(m.group(1))
            out.append({'at': t, 'rc': int(m.group(2)), 'end': t})
    for r in out:
        r.pop('mirror', None)
        r.setdefault('rows', {})
        lp = r.pop('lp', None)
        r['why'] = WHY.get(r['rc'], 'exit %s' % r['rc']) if r['rc'] is not None else None
        if r['rc'] == 7 and lp:
            r['why'] = _lp_why(lp)
    return out


def _lp_line(run, line):
    """Note on the open run what the lis pendens chain said about itself, if this line is one of those."""
    if run is None:
        return
    lp = run.setdefault('lp', {})
    m = _LP_PARTIAL.match(line) or _LP_NONE_NEW.match(line)
    if m:
        lp['failed'] = [c.strip() for c in m.group(1).split(',') if c.strip()]
        return
    if _LP_ALL.match(line):
        lp['failed'] = list(_LP_COUNTY)
        return
    m = _LP_STOPPED.match(line)
    if m:
        lp['stopped'] = m.group(1)
        return
    if line.startswith('DEGRADED RUN'):
        lp['_list'] = True                           # the "  - <step> (exit N)" lines follow
        return
    m = _LP_DEGRADED.match(line) if lp.get('_list') else None
    if not m:
        lp.pop('_list', None)
        return
    if not m.group(1).startswith('SWEEP'):           # a partial sweep is already named by county
        lp.setdefault('degraded', []).append(m.group(1))


def _step(label):
    """'PHONES (LP fast lane)' -> the plain words for that step."""
    if label.startswith('PHONES'):
        return 'phones for new filings not fully traced'
    if label.startswith('RESOLVE BROWARD PARCEL'):
        return 'Broward parcel match skipped'
    return label.split(' (')[0].lower() + ' step did not run'


def _lp_why(lp):
    """rc 7 in words a caller can act on, from what the chain printed. Falls back to the generic
    reason when the chain printed none of the lines we know."""
    parts = []
    failed = [_LP_COUNTY.get(c, c.title()) for c in _LP_COUNTY if c in (lp.get('failed') or [])]
    failed += [c for c in (lp.get('failed') or []) if c not in _LP_COUNTY]
    if failed:
        if len(failed) == len(_LP_COUNTY):
            parts.append('no new lis pendens filings pulled in any county')
        else:
            parts.append('no new lis pendens filings pulled for ' + ' or '.join(failed))
    if lp.get('stopped'):
        parts.append('lis pendens chain stopped at ' + lp['stopped'].split(' (')[0].lower()
                     + ', later steps did not run')
    parts += [_step(d) for d in lp.get('degraded') or []]
    return '; '.join(parts) if parts else WHY[7]


def _read_tail(path):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            return f.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def summary(log_path=LOG, now=None):
    now = now or datetime.now()
    rs = runs(_read_tail(log_path))
    last = rs[-1] if rs else None
    # A run with no ENDED line that started more than 6 hours ago did not finish: it was killed.
    if last and last['rc'] is None and last['at']:
        try:
            if (now - datetime.strptime(last['at'], '%Y-%m-%dT%H:%M')).total_seconds() > 6 * 3600:
                last = dict(last, rc=-1, why='never finished')
        except ValueError:
            pass
    # The most recent failure earlier the same day as the last run, so a recovered morning says so.
    earlier = None
    if last and last['at']:
        for r in reversed(rs[:-1]):
            if not r['at'] or r['at'][:10] != last['at'][:10]:
                break
            if r['rc'] in FAILED:
                earlier = {'at': r['at'], 'rc': r['rc'], 'why': r['why']}
                break
    # The last run that got past the scrape. rc None is the run baking this very board: a run that
    # fails its scrape or its network check goes straight to :end and never builds one.
    good = None
    rows = {}
    for r in reversed(rs[:-1] + [last] if rs else []):
        # An earlier run with no outcome was killed or lost its lease: not known to have scraped.
        if r['rc'] is None and r is not last:
            continue
        if r['at'] and r['rc'] not in FAILED and r['rc'] != -1:
            good, rows = r['at'], r.get('rows') or {}
            break
    # Rows scraped per county by that run, in a fixed order (counts only). These are the pending
    # auction calendar rows each county's scrape kept (scrape_guard's "BROWARD: 329 row(s)"), NOT
    # leads on the board and NOT lis pendens filings, so the page labels them "auction lists".
    rows = [[n, rows[k]] for k, n in (('BROWARD', 'Broward'), ('MIAMI-DADE', 'Miami'),
                                      ('PALM BEACH', 'Palm Beach')) if k in rows]
    return {'baked': now.strftime('%Y-%m-%dT%H:%M'), 'good': good, 'rows': rows,
            'last': ({k: last[k] for k in ('at', 'rc', 'why')} if last else None),
            'earlier': earlier}


# Self-contained: no board CSS, no board JS. The verdict is decided at VIEW time, so a board that
# nothing rebuilt still goes red on its own once it is old.
_JS = r"""(function(){
var S=%s, el=document.getElementById('rfstat'); if(!el) return;
function t(s){ if(!s) return null; var d=new Date(s); return isNaN(d.getTime())?null:d; }
function hm(s){ var d=t(s); if(!d) return '?'; var h=d.getHours(), m=('0'+d.getMinutes()).slice(-2);
  var day=(d.toDateString()===new Date().toDateString())?'today':(d.getMonth()+1)+'/'+d.getDate();
  return day+' '+((h+11)%%12+1)+':'+m+(h<12?' AM':' PM'); }
function age(s){ var d=t(s); if(!d) return ''; var h=(Date.now()-d.getTime())/36e5;
  return h<1?'under an hour old':h<48?Math.round(h)+'h old':Math.round(h/24)+' days old'; }
var L=S.last, G=S.good, lvl='ok', msg;
var oldH = t(S.baked) ? (Date.now()-t(S.baked).getTime())/36e5 : 0;
var from=' &middot; leads from '+(G?hm(G)+' ('+age(G)+')':'an older run');
if(L && (L.rc===1||L.rc===3||L.rc===9||L.rc===10||L.rc===-1)){
  lvl='bad'; msg='&#10007; Refresh FAILED '+hm(L.at)+': '+L.why+from;
} else if(oldH>26){
  lvl='bad'; msg='&#10007; No refresh since '+hm(S.baked)+' &middot; board '+age(S.baked);
} else if(L && L.rc===null){
  msg='&#10003; Refreshed '+hm(L.at);
} else if(L && L.rc!==0){
  lvl='warn'; msg='&#9888; Refreshed '+hm(L.at)+', '+L.why;
} else if(L){
  msg='&#10003; Refreshed '+hm(L.at);
} else {
  lvl='warn'; msg='No refresh record on the machine that built this board';
}
if(lvl!=='bad' && S.rows && S.rows.length) msg+=' &middot; auction lists '+S.rows.map(function(x){return x[0]+' '+x[1];}).join(', ');
if(S.earlier && lvl!=='bad') msg+=' &middot; '+hm(S.earlier.at).replace(/^today /,'')+' run failed ('+S.earlier.why+'), recovered';
var c={ok:['#E7F4EA','#1E5B2E'],warn:['#FBF1DC','#7A4E05'],bad:['#B42318','#FFFFFF']}[lvl];
el.style.cssText='display:block;font:600 13px/1.35 system-ui,-apple-system,Segoe UI,sans-serif;padding:6px 12px;'
  +'background:'+c[0]+';color:'+c[1]+';text-align:center';
el.innerHTML=msg;
})();"""


def page_html(s=None):
    """The <div> and its script, for __REFRESHSTATUS__. Never raises: a broken status line must not
    stop a board from building, so any failure gives an empty string and the page goes without."""
    try:
        s = s if s is not None else summary()
        data = json.dumps(s, separators=(',', ':')).replace('</', '<\\/')
        return '<div id="rfstat" style="display:none"></div><script>' + (_JS % data) + '</script>'
    except Exception as e:      # noqa: BLE001 - see docstring
        print('refresh status line skipped (%s)' % e)
        return ''


if __name__ == '__main__':
    print(json.dumps(summary(), indent=1))
