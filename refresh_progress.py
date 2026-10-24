"""refresh_progress.py - where the running refresh is, step by step, while it runs.

WHY (2026-10-24, Alejandro). refresh-dealflow.bat runs for hours. Its console printed one bare
label per step ("[3b/5] Skip-tracing owner + LLC-officer phones...") with no time, no count and no
end, and everything the step itself said went into leads-run.log. Watching it meant staring at a
line that might be ten seconds or two hours old. The board's status line (refresh_status.py) only
says how the LAST run ended, after the fact.

So the .bat now calls this file once before each step label:

    python -u refresh_progress.py step 3b          # entering the step whose marker key is 3b
    python -u refresh_progress.py done %RUNEXIT%   # once, next to the ENDED line

Each call stamps the time into refresh_progress.json (DEALFLOW_DIR, outside the repo) and prints a
progress line in the .bat's own console: step N of M, a bar, time in, and an estimate of time left
from the last runs' step times (refresh_progress_history.json, also DEALFLOW_DIR).

    python refresh_progress.py watch               # live screen: refresh-watch.bat opens this
    python refresh_progress.py watch --until-done  # what the .bat opens at its first step: one
                                                   # window, closes 10 minutes after the run ends
    python refresh_progress.py status              # the JSON the send bridge serves at GET /refresh

THE STEP LIST IS READ FROM THE .BAT. Each `refresh_progress.py step KEY` line names the step whose
`echo` label follows it, so adding, removing or reordering a step in the .bat changes this view
with no second list to keep in sync. _refreshprogresstest.py fails if a step label has no marker.

NEVER STOPS A RUN. Every command exits 0, whatever goes wrong. A broken progress display must not
cost a night's scrape, so the .bat never reads this file's errorlevel either.

COUNTS, TIMES AND STEP LABELS ONLY in the JSON and on the bridge. The live screen also shows the
tail of leads-run.log, which can carry names; that stays on the screen of the machine that wrote
it and is never written anywhere by this file.
"""
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BAT = os.path.join(HERE, 'refresh-dealflow.bat')
LOG = os.path.join(HERE, 'leads-run.log')
FLAG = os.path.join(HERE, 'refresh-running.flag')
HISTORY_KEEP = 14

_MARK = re.compile(r'^\s*python\s+-u\s+refresh_progress\.py\s+step\s+(\S+)\s*$', re.I)
_ECHO = re.compile(r'^\s*echo\s+(.*)$', re.I)
_TAG = re.compile(r'^\[([^\]]+)\]\s*(.*)$')
# The two steps above the code pull. They cannot carry a marker: the .bat pulls ITSELF at [0/4],
# and cmd reads a batch file by byte offset, so every byte up to that pull must stay identical
# across versions or the run that pulls a new .bat resumes mid-line. They are shown as one
# finished step, timed from the run's start flag to the first marker.
PRE = {'key': '_pre', 'tag': '0', 'label': 'Network check + code update'}


def _paths():
    try:
        import paths as P
        return P.out('refresh_progress.json'), P.out('refresh_progress_history.json')
    except Exception:      # noqa: BLE001 - never stop a run over where to write a status file
        return (os.path.join(HERE, 'refresh_progress.json'),
                os.path.join(HERE, 'refresh_progress_history.json'))


def _clean(label):
    """The .bat's echo text as plain words: carets unescaped, trailing dots gone."""
    s = label.replace('^', '').strip()
    s = re.sub(r'\.{2,}$|…$', '', s).strip()
    return re.sub(r'\s+', ' ', s)


def catalogue(bat_path=BAT):
    """[{key, tag, label}] in .bat order, from the marker lines and the echo that follows each."""
    try:
        with open(bat_path, encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out, want = [], None
    for line in lines:
        m = _MARK.match(line)
        if m:
            want = m.group(1)
            continue
        if want is None:
            continue
        if re.match(r'^\s*rem\b', line, re.I) or not line.strip():
            continue
        m = _ECHO.match(line)
        if m:
            text = _clean(m.group(1))
            t = _TAG.match(text)
            out.append({'key': want, 'tag': t.group(1) if t else '', 'label': t.group(2) if t else text})
        want = None
    return out


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, separators=(',', ':'))
    for _ in range(5):            # a reader can hold the file for a moment on Windows
        try:
            os.replace(tmp, path)
            return
        except OSError:
            time.sleep(0.2)
    os.replace(tmp, path)


def _iso(t):
    return datetime.fromtimestamp(t).strftime('%Y-%m-%dT%H:%M:%S')


def _flag_time():
    """When this run started: the start flag's mtime, written by the .bat's first lines."""
    try:
        return os.path.getmtime(FLAG)
    except OSError:
        return None


def _log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def _fmt(sec):
    if sec is None:
        return '?'
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return '%dh%02dm' % (h, m)
    if m:
        return '%dm%02ds' % (m, s)
    return '%ds' % s


def _clock(t):
    d = datetime.fromtimestamp(t)
    return d.strftime('%I:%M %p').lstrip('0')


# ---- the two calls the .bat makes ---------------------------------------------------------------

def mark_step(key, now=None, state_path=None, hist_path=None):
    now = now or time.time()
    sp, hp = _paths()
    sp, hp = state_path or sp, hist_path or hp
    st = _load(sp, {})
    started = _flag_time() or now
    # A new run when there is no state, the last one ended, or the start flag is a different file.
    if not st or st.get('ended') or abs(float(st.get('started') or 0) - started) > 2:
        st = {'started': started, 'steps': [{'key': PRE['key'], 'start': started, 'end': now}],
              'ended': None, 'rc': None}
    steps = st.setdefault('steps', [])
    if steps and steps[-1].get('end') is None:
        steps[-1]['end'] = now
    steps.append({'key': key, 'start': now, 'end': None, 'log': _log_size()})
    st['updated'] = now
    _save(sp, st)
    _console(st, key, now, hp)
    return st


def mark_done(rc, now=None, state_path=None, hist_path=None):
    now = now or time.time()
    sp, hp = _paths()
    sp, hp = state_path or sp, hist_path or hp
    st = _load(sp, {})
    if not st or st.get('ended'):
        return st
    steps = st.get('steps') or []
    if steps and steps[-1].get('end') is None:
        steps[-1]['end'] = now
    try:
        st['rc'] = int(rc)
    except (TypeError, ValueError):
        st['rc'] = None
    st['ended'] = now
    st['updated'] = now
    _save(sp, st)
    hist = _load(hp, [])
    hist.append({'started': st.get('started'), 'ended': now, 'rc': st['rc'],
                 'secs': {s['key']: round(s['end'] - s['start'], 1) for s in steps
                          if s.get('end') is not None and s.get('key')}})
    _save(hp, hist[-HISTORY_KEEP:])
    _say('  ' + '=' * 72)
    _say('  REFRESH FINISHED %s  rc=%s (%s)  took %s' % (
        _clock(now), st['rc'], _why(st['rc']), _fmt(now - float(st.get('started') or now))))
    _say('  ' + '=' * 72)
    return st


def _why(rc):
    try:
        import refresh_status
        return refresh_status.WHY.get(rc, 'exit %s' % rc)
    except Exception:      # noqa: BLE001
        return 'exit %s' % rc


def _say(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode('ascii'))
    sys.stdout.flush()


# ---- the view ----------------------------------------------------------------------------------

def _typical(hist):
    """key -> median seconds over the runs that ran it."""
    by = {}
    for h in hist:
        for k, v in (h.get('secs') or {}).items():
            if isinstance(v, (int, float)):
                by.setdefault(k, []).append(v)
    return {k: statistics.median(v) for k, v in by.items() if v}


def view(st=None, now=None, cat=None, hist=None):
    """Everything a screen needs, as plain data. Counts, times and labels only."""
    now = now or time.time()
    sp, hp = _paths()
    st = _load(sp, {}) if st is None else st
    cat = catalogue() if cat is None else cat
    hist = _load(hp, []) if hist is None else hist
    typ = _typical(hist)
    flag = _flag_time()
    if not st:
        return {'ok': True, 'state': 'none', 'running': bool(flag),
                'started': _iso(flag) if flag else None, 'n': len(cat)}
    started = float(st.get('started') or now)
    steps = st.get('steps') or []
    seen = {s['key']: s for s in steps if s.get('key')}
    cur = steps[-1] if steps and steps[-1].get('end') is None and not st.get('ended') else None
    order = [PRE] + cat
    keys = [c['key'] for c in order]
    idx = keys.index(cur['key']) if cur and cur['key'] in keys else None
    rows = []
    for i, c in enumerate(order):
        s = seen.get(c['key'])
        if s and s.get('end') is not None:
            state, secs = 'done', s['end'] - s['start']
        elif s:
            state, secs = 'running', now - s['start']
        elif st.get('ended') or (idx is not None and i < idx):
            state, secs = 'skipped', None
        else:
            state, secs = 'todo', None
        rows.append({'key': c['key'], 'tag': c['tag'], 'label': c['label'], 'state': state,
                     'secs': round(secs, 1) if secs is not None else None,
                     'typ': round(typ[c['key']], 1) if c['key'] in typ else None})
    running = not st.get('ended')
    stale = False
    if running and (flag is None or abs(flag - started) > 2):
        # No start flag (deleted at the final exit), or a NEWER run's flag: this run is not going on.
        stale = True
    eta = None
    if running and not stale and typ:
        eta = 0.0
        for r in rows:
            if r['state'] == 'todo':
                eta += r['typ'] or 0
            elif r['state'] == 'running' and r['typ']:
                eta += max(0.0, r['typ'] - (r['secs'] or 0))
    done_n = sum(1 for r in rows if r['state'] in ('done', 'skipped'))
    elapsed = (float(st['ended']) if st.get('ended') else now) - started
    if eta is not None and elapsed + eta > 0:
        pct = elapsed / (elapsed + eta)
    else:
        pct = done_n / max(1, len(rows))
    if st.get('ended'):
        pct = 1.0
    out = {'ok': True, 'state': 'stale' if stale else ('running' if running else 'ended'),
           'running': running and not stale, 'started': _iso(started),
           'updated': _iso(float(st.get('updated') or started)),
           'elapsed': round(elapsed), 'eta': round(eta) if eta is not None else None,
           'pct': round(pct, 3), 'n': len(rows), 'done': done_n,
           'ended': _iso(float(st['ended'])) if st.get('ended') else None,
           'rc': st.get('rc'), 'why': _why(st['rc']) if st.get('ended') and st.get('rc') is not None else None,
           'rows': rows}
    if cur and idx is not None:
        out['step'] = {'i': idx + 1, 'key': cur['key'], 'tag': order[idx]['tag'],
                       'label': order[idx]['label'], 'secs': round(now - cur['start']),
                       'typ': rows[idx]['typ'], 'log': cur.get('log', 0)}
    if flag and stale and (st.get('ended') or abs(flag - started) > 2):
        out['next'] = {'started': _iso(flag)}      # a newer run is starting, before its first step
    return out


def public(now=None):
    """GET /refresh on the send bridge. No log text, ever. Never raises."""
    try:
        v = view(now=now)
        rows = v.pop('rows', [])
        v['recent'] = [[r['tag'], r['label'], r['secs']] for r in rows if r['state'] == 'done'][-3:]
        v['skipped'] = sum(1 for r in rows if r['state'] == 'skipped')
        if v.get('step'):
            v['step'].pop('log', None)
        return v
    except Exception as e:      # noqa: BLE001 - the bridge must answer something
        return {'ok': False, 'err': str(e)[:120]}


def _bar(pct, width, full='#', empty='.'):
    n = int(round(max(0.0, min(1.0, pct)) * width))
    return full * n + empty * (width - n)


def _console(st, key, now, hist_path):
    """The two lines the .bat window shows above each step label."""
    try:
        v = view(st=st, now=now, hist=_load(hist_path, []))
        s = v.get('step') or {}
        left = ('  ~%s left' % _fmt(v['eta'])) if v.get('eta') is not None else ''
        prev = [r for r in v['rows'] if r['state'] == 'done' and r['key'] != key]
        _say('')
        _say('  ' + '-' * 72)
        _say('  %s | step %s of %s | %3d%% [%s] | %s in%s' % (
            _clock(now), s.get('i', '?'), v['n'], round(v['pct'] * 100), _bar(v['pct'], 20),
            _fmt(v['elapsed']), left))
        if prev:
            last = max(prev, key=lambda r: [x['key'] for x in v['rows']].index(r['key']))
            _say('  last step took %s: %s' % (_fmt(last['secs']), last['label'][:60]))
    except Exception:      # noqa: BLE001
        pass


def _ansi():
    if os.name == 'nt':
        os.system('')      # turns on VT escape handling in a Windows console
    return True


def _tail(log_off, n, width):
    try:
        with open(LOG, 'rb') as f:
            f.seek(0, 2)
            end = f.tell()
            start = max(log_off or 0, end - 64 * 1024)
            f.seek(start)
            text = f.read().decode('utf-8', 'replace')
    except OSError:
        return []
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return [ln[:width] for ln in lines[-n:]]


def render(v, width=100, height=40, tail=None):
    """The live screen as a list of text lines (tested without a console)."""
    G, Y, R, B, D, W, X = ('\033[32m', '\033[33m', '\033[31m', '\033[36m', '\033[2m', '\033[1m',
                           '\033[0m')
    out = []
    if v.get('state') == 'none':
        if v.get('running'):
            out.append(B + W + ' DEALFLOW REFRESH  STARTING' + X + '   started %s' % _clock_iso(v.get('started')))
            out.append(D + ' Checking the network and updating the code. The step list appears at the first step.' + X)
        else:
            out.append(W + ' DEALFLOW REFRESH' + X + '   no run recorded yet on this machine.')
            out.append(D + ' This screen fills in as soon as refresh-dealflow.bat reaches its first step.' + X)
        return out
    st = v['state']
    if st == 'running':
        head = B + W + ' DEALFLOW REFRESH  RUNNING' + X
        tail_t = '   started %s  |  %s in' % (_clock_iso(v['started']), _fmt(v['elapsed']))
        if v.get('eta') is not None:
            tail_t += '  |  about %s left' % _fmt(v['eta'])
    elif st == 'ended':
        bad = v.get('rc') not in (0, 7, None)
        head = (R if bad else G) + W + ' DEALFLOW REFRESH  FINISHED' + X
        tail_t = '   %s  |  rc=%s %s  |  took %s' % (_clock_iso(v['ended']), v.get('rc'), v.get('why') or '',
                                                  _fmt(v['elapsed']))
    else:
        head = R + W + ' DEALFLOW REFRESH  STOPPED' + X
        tail_t = '   the run that started %s ended without its final line (window closed or killed)' % (
            _clock_iso(v['started']))
    out.append(head + tail_t)
    if v.get('next'):
        out.append(Y + ' A new refresh started %s and is checking the network / updating code.' % (
            _clock_iso(v['next']['started'])) + X)
    bw = max(10, min(60, width - 30))
    out.append(' [%s] %3d%%   %d of %d steps' % (_bar(v['pct'], bw, '█', '░'),
                                               round(v['pct'] * 100), v['done'], v['n']))
    out.append('')
    rows = v['rows']
    cur = next((i for i, r in enumerate(rows) if r['state'] == 'running'), None)
    if cur is None:
        cur = max((i for i, r in enumerate(rows) if r['state'] in ('done', 'skipped')), default=0)
    room = max(8, height - 22)
    lo = max(0, cur - room // 2)
    hi = min(len(rows), lo + room)
    lo = max(0, hi - room)
    if lo:
        out.append(D + '   ... %d earlier step(s)' % lo + X)
    for r in rows[lo:hi]:
        lab = ('%-7s %s' % (r['tag'], r['label']))[:max(20, width - 34)]
        typ = (' (usually %s)' % _fmt(r['typ'])) if r['typ'] else ''
        if r['state'] == 'done':
            out.append(G + ' ✓ ' + X + lab.ljust(width - 34) + ' ' + _fmt(r['secs']))
        elif r['state'] == 'running':
            out.append(B + W + ' ▶ ' + lab.ljust(width - 34) + ' ' + _fmt(r['secs']) + X + D + typ + X)
        elif r['state'] == 'skipped':
            out.append(D + ' - ' + lab.ljust(width - 34) + ' skipped' + X)
        else:
            out.append(D + ' ○ ' + lab + X)
    if hi < len(rows):
        out.append(D + '   ... %d more step(s)' % (len(rows) - hi) + X)
    if tail is not None:
        out.append('')
        out.append(W + ' What it is doing now' + X + D + '  (live from leads-run.log)' + X)
        out.extend('   ' + ln for ln in (tail or ['(nothing written yet in this step)']))
    if st == 'ended':
        slow = sorted((r for r in rows if r['state'] == 'done' and r['key'] != PRE['key']),
                      key=lambda r: -(r['secs'] or 0))[:3]
        if slow:
            out.append('')
            out.append(' Slowest steps: ' + ', '.join('%s %s' % (r['tag'], _fmt(r['secs'])) for r in slow))
        out.append(D + ' Waiting for the next refresh. Close this window any time; it never affects the run.' + X)
    return out


def _clock_iso(s):
    try:
        return _clock(datetime.strptime(s, '%Y-%m-%dT%H:%M:%S').timestamp())
    except (TypeError, ValueError):
        return '?'


def _alive_path():
    try:
        import paths as P
        return P.out('refresh_watch.alive')
    except Exception:      # noqa: BLE001
        return os.path.join(HERE, 'refresh_watch.alive')


def watch(interval=2.0, until_done=False, linger=600):
    """The live screen. until_done (how the .bat opens it): close `linger` seconds after this run
    ends, and do not open a second window when one is already showing."""
    alive = _alive_path()
    if until_done:
        try:
            if time.time() - os.path.getmtime(alive) < 3 * interval + 2:
                return
        except OSError:
            pass
    _ansi()
    born = time.time()
    try:
        import shutil
        while True:
            try:
                with open(alive, 'w') as f:
                    f.write(str(os.getpid()))
            except OSError:
                pass
            size = shutil.get_terminal_size((100, 40))
            v = view()
            if until_done:
                ended_at = None
                if v.get('state') == 'ended' and v.get('ended'):
                    ended_at = datetime.strptime(v['ended'], '%Y-%m-%dT%H:%M:%S').timestamp()
                elif v.get('state') == 'stale' or (v.get('state') == 'none' and not v.get('running')):
                    ended_at = born
                if ended_at and not v.get('next') and time.time() - max(ended_at, born) > linger:
                    break
            step = v.get('step') or {}
            n_tail = 10 if size.lines >= 34 else 5
            tail = _tail(step.get('log'), n_tail, size.columns - 4) if v.get('state') == 'running' else None
            lines = render(v, size.columns - 1, size.lines, tail)
            sys.stdout.write('\033[H\033[2J' + '\n'.join(lines[:size.lines - 1]) + '\n')
            sys.stdout.write('\033[2m Ctrl+C closes this window; it never affects the run. %s\033[0m'
                             % datetime.now().strftime('%I:%M:%S %p'))
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
    finally:
        try:
            os.remove(alive)
        except OSError:
            pass


def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'status'
    try:
        if cmd == 'step' and len(argv) > 2:
            mark_step(argv[2])
        elif cmd == 'done':
            mark_done(argv[2] if len(argv) > 2 else None)
        elif cmd == 'watch':
            watch(until_done='--until-done' in argv)
        elif cmd == 'status':
            print(json.dumps(public(), indent=1))
        else:
            print(__doc__)
    except Exception as e:      # noqa: BLE001 - see "NEVER STOPS A RUN"
        if cmd in ('step', 'done'):
            print('  (progress display skipped: %s)' % str(e)[:100])
        else:
            raise
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
