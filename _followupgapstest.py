"""_followupgapstest.py - reports/followup_gaps.py on invented fixtures in a temp dir.
Run: DEALFLOW_DIR=<temp> python _followupgapstest.py"""
import datetime as dt, json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'reports'))
import followup_gaps as G

T = dt.date(2026, 10, 29)
NOTES = {
  'T-1': {'status': 'Called - talked', 'touches': [{'d': '2026-10-20', 'ch': 'call', 'out': 'talked'}]},   # no next, stale
  'T-2': {'status': 'Called - talked', 'next': '2026-10-27', 'dials': [{'d': '2026-10-28', 'oc': 'talked'}]},  # overdue 2 (legacy field)
  'T-3': {'status': 'Appointment', 'nextStep': {'date': '2026-11-02', 'what': 'meet'}, 'next': '2026-11-02',
          'stageAt': {'Appointment': '2026-10-29'}},                                                          # scheduled
  'T-4': {'status': 'Offer made', 'nextStep': {'date': '2026-10-20', 'what': 'x'}, 'touches': []},          # overdue 9, never touched
  'T-5': {'status': 'Callback', 'next': '2026-10-29', 'touches': [{'d': '2026-10-29', 'ch': 'call', 'out': 'cb'}]},  # due today = scheduled
  'T-6': {'status': 'CLOSED'},                                                                                # not a gap
  'T-7': {'status': 'Called - talked', 'optout': True},                                                       # held out
  'T-8': {'status': 'Dead'},
  'T-9': {'status': 'Under contract', 'touches': [{'d': '2026-10-29', 'ch': 'email', 'out': 'morning batch'}]},  # machine send ignored
  '@p': {'status': 'Appointment'},
}


def main():
    fails = []

    def eq(n, g, w):
        if g != w:
            fails.append('%s: got %r want %r' % (n, g, w))
    r = G.analyse(NOTES, today=T)
    eq('no_next', sorted(r['no_next_step']), ['T-1', 'T-9'])
    eq('overdue', r['overdue'], [('T-4', 9), ('T-2', 2)])
    eq('scheduled', sorted(r['scheduled']), ['T-3', 'T-5'])
    eq('stale', sorted(r['stale_no_next_step']), ['T-1', 'T-9'])
    eq('never', sorted(r['never_touched']), ['T-4', 'T-9'])
    eq('closed counted', r['by_stage']['CLOSED']['leads'], 1)
    eq('optout out', 'T-7' in r['no_next_step'], False)
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'worker_notes.json')
    with open(p, 'w') as f:
        json.dump({'notes': NOTES}, f)
    out = subprocess.run([sys.executable, os.path.join(HERE, 'reports', 'followup_gaps.py'), '--notes', p],
                         capture_output=True, text=True, env=dict(os.environ, DEALFLOW_DIR=d))
    eq('cli rc', out.returncode, 0)
    eq('cli has T-4', 'T-4:' in out.stdout, True)
    if fails:
        print('FAIL')
        print('\n'.join(fails))
        sys.exit(1)
    print('followup gaps test: ok')


main()
