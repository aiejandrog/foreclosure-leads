"""_callstatstest.py - call_stats.py and the page's callStatsRows()/callStatsCsv() must agree on one fixture.
Run: python _callstatstest.py   (needs node; skips the JS half with a message if node is missing)."""
import json, os, re, shutil, subprocess, sys, tempfile
import call_stats as C

HERE = os.path.dirname(os.path.abspath(__file__))
T = 1760000000000
NOTES = {
  'A': {'dials': [
    {'d': '2026-10-15', 'ph4': '1111', 'oc': 'pending', 'by': 'Alex', 'tsu': T},
    {'d': '2026-10-15', 'ph4': '1111', 'oc': 'talked', 'by': 'Alex', 'tsu': T + 240000, 'tsd': T},
    {'d': '2026-10-15', 'ph4': '2222', 'oc': 'noanswer', 'by': 'Alex', 'tsu': T + 300000}]},
  'B': {'dials': [
    {'d': '2026-10-15', 'ph4': '3333', 'oc': 'pending', 'by': 'Alex', 'tsu': T + 400000},
    {'d': '2026-10-15', 'ph4': '4444', 'oc': 'appt', 'by': 'Alex', 'tsu': T + 900000, 'tsd': T + 500000},
    {'d': '2026-10-15', 'ph4': '5555', 'oc': 'wrong', 'by': 'Alex', 'tsu': T}]},
  'C': {'dials': [
    {'d': '2026-10-15', 'ph4': '6666', 'oc': 'voicemail', 'by': 'Carlos', 'tsu': T},
    {'d': '2026-10-15', 'ph4': '6666', 'oc': 'badnum', 'tsu': T},
    {'d': '2026-10-14', 'ph4': '7777', 'oc': 'dnc', 'by': 'Alex', 'tsu': T + 9000000, 'tsd': T},
    {'d': '2026-10-14', 'ph4': '8888', 'oc': 'mystery', 'by': 'Alex'},
    {'d': '2026-10-01', 'ph4': '9999', 'oc': 'noanswer', 'by': 'Alex'}]},
  'D': {'dials': [{'d': '2026-10-15', 'ph4': '0000', 'oc': 'redial', 'by': 'Alex', 'tsu': T}]},
  '@person': {'dials': [{'d': '2026-10-15', 'oc': 'talked', 'by': 'Alex'}]},
}

def main():
    fails = []
    def eq(name, got, want):
        if got != want: fails.append('%s: got %r want %r' % (name, got, want))
    import datetime as dt
    rows = C.rows_from_notes(NOTES, 'Alex', 14, dt.date(2026, 10, 15))
    by = {(r['date'], r['seat']): r for r in rows}
    t = by[('2026-10-15', 'Alex')]
    eq('total', t['dials'], 5)            # talked, noanswer, appt, wrong + D's unresolved redial; pendings followed by an outcome count once
    eq('answered', t['answered'], 2); eq('noans', t['no_answer_vm'], 1); eq('wrong', t['wrong_or_disconnected'], 1)
    eq('appts', t['appointments'], 1); eq('no_outcome', t['no_outcome'], 1)
    eq('timed', t['timed_calls'], 2); eq('min', round(t['dial_to_log_min'], 3), round((240 + 400) / 60.0, 3))
    y = by[('2026-10-14', 'Alex')]
    eq('gap>1h untimed', y['timed_calls'], 0); eq('other', y['other'], 1)
    eq('unstamped kept', ('2026-10-15', '') in by, True); eq('other seat hidden', ('2026-10-15', 'Carlos') in by, False)
    eq('window', ('2026-10-01', 'Alex') in by, False)
    eq('person ledger skipped', t['dials'], 5)
    csv = C.to_csv(rows)
    node = shutil.which('node')
    if not node:
        print('node missing: JS half skipped')
    else:
        src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
        m = re.search(r'/\* CALLSTATS-BEGIN.*?/\* CALLSTATS-END \*/', src, re.S)
        if not m: fails.append('CALLSTATS block not found in call_mode.py')
        else:
            js = m.group(0) + """
var N=%s; var rows=callStatsRows(N,'Alex',14,new Date(2026,9,15,12).getTime());
process.stdout.write(callStatsCsv(rows));""" % json.dumps(NOTES)
            d = tempfile.mkdtemp(); p = os.path.join(d, 't.js'); open(p, 'w').write(js)
            out = subprocess.run([node, p], capture_output=True, text=True)
            if out.returncode: fails.append('node failed: ' + out.stderr[:300])
            else: eq('python vs page csv', out.stdout.strip(), csv.strip())
    if fails:
        print('FAIL\n' + '\n'.join(fails)); return 1
    print('ok: call_stats.py and the page agree'); return 0

if __name__ == '__main__':
    sys.exit(main())
