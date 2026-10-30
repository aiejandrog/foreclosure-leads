"""_funneltruthtest.py - reports/funnel_truth.py on invented fixtures in a temp dir.
Run: DEALFLOW_DIR=<temp> python _funneltruthtest.py"""
import datetime as dt, json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'reports'))
import funnel_truth as F

T = dt.date(2026, 10, 29)
NOTES = {
  'T-1': {'status': 'Appointment', 'stageAt': {'Appointment': '2026-10-28'},
          'dials': [{'d': '2026-10-28', 'oc': 'appt'}, {'d': '2026-10-27', 'oc': 'noanswer'}]},
  'T-2': {'status': 'Under contract', 'stageAt': {'Appointment': '2026-10-20', 'Offer made': '2026-10-25', 'Under contract': '2026-10-29'},
          'dials': [{'d': '2026-10-29', 'oc': 'pending'}, {'d': '2026-10-29', 'oc': 'talked'}]},
  'T-3': {'status': 'Offer made', 'outcomeAt': '2026-10-27', 'dials': [{'d': '2026-10-27', 'oc': 'pending'}]},
  'T-4': {'status': 'CLOSED', 'outcomeAt': '2026-10-26'},
  'T-5': {'status': 'Called - talked', 'dials': [{'d': '2026-09-01', 'oc': 'talked'}]},   # outside window
  '@p': {'status': 'Appointment', 'dials': [{'d': '2026-10-29', 'oc': 'appt'}]},
}


def main():
    fails = []

    def eq(n, g, w):
        if g != w:
            fails.append('%s: got %r want %r' % (n, g, w))
    rows = {r['date']: r for r in F.compute(NOTES, days=7, today=T)}
    eq('10-29 dials', rows['2026-10-29']['dials'], 1)           # pending marker folded into the talked row
    eq('10-29 convo', rows['2026-10-29']['conversations'], 1)
    eq('10-29 contracts', rows['2026-10-29']['contracts'], 1)
    eq('10-28 appts', rows['2026-10-28']['appointments'], 1)
    eq('10-28 dials', rows['2026-10-28']['dials'], 1)
    eq('10-27 dials', rows['2026-10-27']['dials'], 2)           # noanswer + lone pending marker
    eq('10-27 offers (legacy)', rows['2026-10-27']['offers'], 1)
    eq('10-25 offers', rows['2026-10-25']['offers'], 1)
    eq('10-26 contract (CLOSED legacy)', rows['2026-10-26']['contracts'], 1)
    eq('window cut', '2026-09-01' in rows, False)
    eq('legacy', F.legacy_count(NOTES), 2)
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'worker_notes.json')
    with open(p, 'w') as f:
        json.dump({'notes': NOTES}, f)
    out = subprocess.run([sys.executable, os.path.join(HERE, 'reports', 'funnel_truth.py'), '--notes', p, '--days', '400'],
                         capture_output=True, text=True, env=dict(os.environ, DEALFLOW_DIR=d))
    eq('cli rc', out.returncode, 0)
    eq('cli no case ids', 'T-1' in out.stdout, False)
    if fails:
        print('FAIL\n' + '\n'.join(fails)); sys.exit(1)
    print('funnel truth test: ok')


main()
