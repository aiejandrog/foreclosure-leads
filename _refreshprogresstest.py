"""_refreshprogresstest.py - the live refresh progress (refresh_progress.py) and its .bat hooks.

What has to hold:
  - every step label in refresh-dealflow.bat after the code pull has a marker line right above it,
    with a key no other step uses, so the live list is the .bat's own list;
  - nothing of this lands at or above the [0/4] self-pull (cmd reads the file by byte offset, so the
    run that pulls a new .bat resumes at the old offset in the new file);
  - no marker sits between a command and the `if errorlevel` that reads it (a marker exits 0);
  - a run is timed step by step, skipped steps show as skipped, the end closes the run and the next
    run's estimate comes from the last runs' step times;
  - a run whose start flag is gone without an ENDED mark reads as stopped, not as running forever;
  - the bridge view carries no log text; the commands never return non-zero.

Run: python _refreshprogresstest.py
"""
import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refresh_progress as R  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, ('  -- ' + str(detail)[:300]) if detail and not ok else ''))
    if not ok:
        FAILS.append(name)


def bat_checks():
    lines = open(R.BAT, encoding='utf-8').read().split('\n')
    pull = next(i for i, l in enumerate(lines) if l.startswith('git pull --ff-only origin main'))
    end_pull = next(i for i in range(pull, len(lines)) if lines[i].startswith(')'))
    above = '\n'.join(lines[:end_pull + 1])
    check('nothing from refresh_progress at or above the [0/4] self-pull block',
          'refresh_progress' not in above)
    labels = [i for i, l in enumerate(lines) if re.match(r'^echo \[[0-9a-z]+(?:/[0-9])?\]', l)]
    after = [i for i in labels if i > end_pull]
    missing = [lines[i][:60] for i in after if not R._MARK.match(lines[i - 1])]
    check('every step label after the pull has a marker on the line above it', not missing, missing)
    cat = R.catalogue()
    keys = [c['key'] for c in cat]
    check('one catalogue entry per step label after the pull', len(cat) == len(after), (len(cat), len(after)))
    check('marker keys are unique', len(keys) == len(set(keys)), keys)
    check('labels are read clean (no caret, no trailing dots)',
          all('^' not in c['label'] and not c['label'].endswith('...') for c in cat),
          [c['label'] for c in cat if '^' in c['label'] or c['label'].endswith('...')])
    check('first step is the scrape, last is the healthcheck',
          cat and cat[0]['tag'] == '1/4' and cat[-1]['key'] == 'health', (cat[:1], cat[-1:]))
    bad = []
    for i, l in enumerate(lines):
        if R._MARK.match(l):
            j = i + 2
            while j < len(lines) and (not lines[j].strip() or lines[j].lower().startswith('rem')):
                j += 1
            if 'errorlevel' in lines[j].lower() or ':lpcode' in lines[j].lower():
                bad.append(i + 1)
    check('no marker sits between a command and the errorlevel test that reads it', not bad, bad)
    done = [i for i, l in enumerate(lines) if 'refresh_progress.py done %RUNEXIT%' in l]
    ended = [i for i, l in enumerate(lines) if l.startswith('echo ==== REFRESH ENDED rc=%RUNEXIT%')]
    check('done is called once, right after the ENDED line', len(done) == 1 and ended and done[0] > ended[0]
          and done[0] - ended[0] <= 4, (done, ended))


def run_checks():
    d = tempfile.mkdtemp()
    sp, hp = os.path.join(d, 'state.json'), os.path.join(d, 'hist.json')
    R._paths = lambda: (sp, hp)
    R.FLAG = os.path.join(d, 'refresh-running.flag')
    R.LOG = os.path.join(d, 'leads-run.log')
    open(R.LOG, 'w').write('first line\n')
    cat = R.catalogue()
    t0 = 1_800_000_000.0
    open(R.FLAG, 'w').write('Sat 10/24/2026 11:36:02.15\n')
    os.utime(R.FLAG, (t0, t0))

    buf = io.StringIO()
    with redirect_stdout(buf):
        R.mark_step('1', now=t0 + 60)
    out = buf.getvalue()
    check('the .bat window gets a step line', 'step 2 of %d' % (len(cat) + 1) in out, out)
    open(R.LOG, 'a').write('BROWARD: 319 rows\nscrape done\n')
    with redirect_stdout(io.StringIO()):
        R.mark_step('1b', now=t0 + 600)
        R.mark_step('2', now=t0 + 900)        # 1c, 1d, 1e were skipped
    v = R.view(now=t0 + 960, cat=cat, hist=[])
    rows = {r['key']: r for r in v['rows']}
    check('state is running', v['state'] == 'running' and v['running'], v['state'])
    check('pre-steps timed from the start flag', rows['_pre']['state'] == 'done' and rows['_pre']['secs'] == 60,
          rows['_pre'])
    check('a finished step carries its time', rows['1']['secs'] == 540, rows['1'])
    check('steps jumped over read as skipped', all(rows[k]['state'] == 'skipped' for k in ('1c', '1d', '1e')),
          [rows[k]['state'] for k in ('1c', '1d', '1e')])
    check('the current step is the one running', v['step']['key'] == '2' and v['step']['secs'] == 60, v.get('step'))
    check('no estimate without history', v['eta'] is None, v['eta'])
    check('later steps are still to do', rows['health']['state'] == 'todo')

    with redirect_stdout(io.StringIO()):
        R.mark_done('7', now=t0 + 1200)
    v = R.view(now=t0 + 1300)
    check('done ends the run with its rc and words', v['state'] == 'ended' and v['rc'] == 7
          and 'lis pendens' in (v['why'] or ''), (v['state'], v['rc'], v['why']))
    check('ended run reads 100%', v['pct'] == 1.0)
    hist = json.load(open(hp))
    check('history keeps the step times', len(hist) == 1 and hist[0]['secs'].get('1') == 540, hist)
    with redirect_stdout(io.StringIO()):
        R.mark_done('0', now=t0 + 1400)
    check('a second done is ignored', len(json.load(open(hp))) == 1)

    # the next run: a new flag, and an estimate from the first run's times
    t1 = t0 + 86400
    os.utime(R.FLAG, (t1, t1))
    with redirect_stdout(io.StringIO()):
        R.mark_step('1', now=t1 + 30)
    v = R.view(now=t1 + 100)
    check('a new flag starts a new run', v['state'] == 'running' and v['step']['key'] == '1', v.get('step'))
    check('estimate left comes from history', v['eta'] is not None and v['eta'] > 0, v['eta'])
    pub = R.public(now=t1 + 100)
    blob = json.dumps(pub)
    check('bridge view has no rows, no log offset, no log text',
          'rows' not in pub and '"log"' not in blob and 'BROWARD' not in blob and 'scrape done' not in blob, blob)
    check('bridge view says which step', pub.get('step', {}).get('label', '').startswith('Pulling new auction'),
          pub.get('step'))

    # killed: the flag is gone and no done mark came
    os.remove(R.FLAG)
    v = R.view(now=t1 + 200)
    check('a run whose flag vanished without an end reads as stopped', v['state'] == 'stale'
          and not v['running'], v['state'])
    lines = R.render(v, 100, 40)
    check('the screen says STOPPED', any('STOPPED' in ln for ln in lines))

    # the live screen while running
    open(R.FLAG, 'w').write('x')
    t2 = os.path.getmtime(R.FLAG)
    with redirect_stdout(io.StringIO()):
        R.mark_step('3b', now=t2 + 5)
    v = R.view(now=t2 + 65)
    lines = R.render(v, 100, 40, tail=['tracing 12 owners'])
    text = '\n'.join(lines)
    check('the screen shows RUNNING, the step and the log tail',
          'RUNNING' in text and 'Skip-tracing' in text and 'tracing 12 owners' in text, text[:400])
    check('the screen fits its height', len(lines) <= 40, len(lines))

    # never fails the .bat
    real = R.mark_step
    R.mark_step = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'))
    with redirect_stdout(io.StringIO()):
        rc = R.main(['x', 'step', '1'])
    R.mark_step = real
    check('a failing step mark still exits 0', rc == 0)
    with open(sp, 'w') as f:
        f.write('{torn')
    with redirect_stdout(io.StringIO()):
        rc = R.main(['x', 'step', '1'])
    check('a torn state file starts over instead of failing', rc == 0 and json.load(open(sp))['steps'][-1]['key'] == '1')
    check('public() never raises', isinstance(R.public(), dict))


def main():
    bat_checks()
    run_checks()
    print('\n%d failed' % len(FAILS) if FAILS else '\nall passed')
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
