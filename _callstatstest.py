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
  # TALK TIME (2026-11-05), its own day so the 10-15 asserts above stay as they were.
  'E': {'dials': [
    {'d': '2026-10-13', 'ph4': '1212', 'oc': 'talked', 'by': 'Alex', 'tsd': T - 10000, 'tsa': T, 'tsu': T + 200000, 'talk': 200},  # (i) timed
    {'d': '2026-10-13', 'ph4': '1313', 'oc': 'talked', 'by': 'Alex', 'tsd': T, 'tsu': T + 60000},                    # (ii) no pickup tap
    {'d': '2026-10-13', 'ph4': '1414', 'oc': 'noanswer', 'by': 'Alex', 'tsa': T, 'tsu': T + 50000, 'talk': 50},       # (iii) not answered
    {'d': '2026-10-13', 'ph4': '1515', 'oc': 'callback', 'by': 'Alex', 'tsa': T + 500000, 'tsu': T + 400000},          # (iv) tsa > tsu
    {'d': '2026-10-13', 'ph4': '1616', 'oc': 'appt', 'by': 'Alex', 'tsu': T, 'talk': -5},
    {'d': '2026-10-13', 'ph4': '1717', 'oc': 'notint', 'by': 'Alex', 'tsu': T, 'talk': 5000},
    {'d': '2026-10-13', 'ph4': '1818', 'oc': 'gate', 'by': 'Alex', 'tsu': T, 'talk': 12.5},
    {'d': '2026-10-13', 'ph4': '1919', 'oc': 'dnc', 'by': 'Alex', 'tsa': T, 'tsu': T + 100000, 'talk': 100}]},
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
    eq('no pickup taps that day: talk 0', (t['talk_s'], t['talked_calls']), (0, 0))
    k = by[('2026-10-13', 'Alex')]
    eq('talk day answered (all 7 answered rows, pickup or not)', k['answered'], 7)
    eq('talked_calls = only valid talk on answered rows', k['talked_calls'], 2)
    eq('talk seconds', k['talk_s'], 300)
    eq('dial_to_log_min still counts the untapped talked call', k['timed_calls'], 2)
    csv = C.to_csv(rows)
    line = [x for x in csv.splitlines() if x.startswith('2026-10-13,Alex,')][0].split(',')
    col = dict(zip(C.COLS, line))
    eq('csv talk_min', col['talk_min'], '5.0'); eq('csv talked_calls', col['talked_calls'], '2')
    eq('csv avg_talk_min', col['avg_talk_min'], '2.5')
    l15 = dict(zip(C.COLS, [x for x in csv.splitlines() if x.startswith('2026-10-15,Alex,')][0].split(',')))
    eq('csv avg blank when no talked calls', (l15['talk_min'], l15['talked_calls'], l15['avg_talk_min']), ('0.0', '0', ''))
    eq('new columns sit right after timed_calls', C.COLS[9:14],
       ['dial_to_log_min', 'timed_calls', 'talk_min', 'talked_calls', 'avg_talk_min'])
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
process.stdout.write(callStatsCsv(rows)+'\\n@@NOTE@@'+CS_NOTE);""" % json.dumps(NOTES)
            d = tempfile.mkdtemp(); p = os.path.join(d, 't.js'); open(p, 'w').write(js)
            out = subprocess.run([node, p], capture_output=True, text=True)
            if out.returncode: fails.append('node failed: ' + out.stderr[:300])
            else:
                pcsv, _, pnote = out.stdout.partition('\n@@NOTE@@')
                eq('python vs page csv', pcsv.strip(), csv.strip())
                eq('python vs page definition note', pnote, C.NOTE)
    # logOutcome's tsd capture, run from the page source: pending -> outcome must be timed
    if node:
        m2 = re.search(r'/\* CALLSTATS-TSD-BEGIN.*?/\* CALLSTATS-TSD-END \*/', src, re.S)
        if not m2: fails.append('TSD block not found')
        else:
            js2 = ("function today(){return 'D';}\nfunction run(dials,digits){var n={dials:dials};var _lastD=n.dials[n.dials.length-1];"
                   + m2.group(0) + "\nif(_lastD&&_lastD.oc==='pending'&&_lastD.d===today()&&_lastD.ph4===String(digits).slice(-4))n.dials.pop();return _tsd;}\n"
                   "var r=[run([{d:'D',ph4:'1111',oc:'pending',tsu:777}],'5551111'),run([{d:'D',ph4:'1111',oc:'redial',tsu:888}],'5551111'),"
                   "run([{d:'D',ph4:'2222',oc:'pending',tsu:5}],'5551111'),run([{d:'D',ph4:'1111',oc:'talked',tsu:5}],'5551111'),run([],'5551111')];"
                   "process.stdout.write(JSON.stringify(r));")
            p2 = os.path.join(tempfile.mkdtemp(), 't2.js'); open(p2, 'w').write(js2)
            o2 = subprocess.run([node, p2], capture_output=True, text=True)
            eq('tsd capture', o2.stdout.strip(), '[777,888,0,0,0]')
        m3 = re.search(r'/\* CALLSTATS-TSA-BEGIN.*?/\* CALLSTATS-TSA-END \*/', src, re.S)
        if not m3: fails.append('TSA block not found')
        else:
            js3 = ("function today(){return 'D';}\nfunction run(dials,digits){var n={dials:dials};var _lastD=n.dials[n.dials.length-1];"
                   + m3.group(0) + "\nreturn _tsa;}\n"
                   "var r=[run([{d:'D',ph4:'1111',oc:'pending',tsu:1,tsa:777}],'5551111'),run([{d:'D',ph4:'1111',oc:'redial',tsu:1,tsa:888}],'5551111'),"
                   "run([{d:'D',ph4:'1111',oc:'pending',tsu:1}],'5551111'),run([{d:'D',ph4:'2222',oc:'pending',tsa:5}],'5551111'),"
                   "run([{d:'X',ph4:'1111',oc:'pending',tsa:5}],'5551111'),run([{d:'D',ph4:'1111',oc:'talked',tsa:5}],'5551111'),run([],'5551111')];"
                   "process.stdout.write(JSON.stringify(r));")
            p3 = os.path.join(tempfile.mkdtemp(), 't3.js'); open(p3, 'w').write(js3)
            o3 = subprocess.run([node, p3], capture_output=True, text=True)
            eq('tsa capture (same row rules as tsd)', o3.stdout.strip(), '[777,888,0,0,0,0,0]')
    if fails:
        print('FAIL\n' + '\n'.join(fails)); return 1
    print('ok: call_stats.py and the page agree'); return 0

if __name__ == '__main__':
    sys.exit(main())
