"""_boardmergetest.py - D14: the board's _mergeLead carries the after-the-yes unit from a teammate's note.
Non-browser: _mergeLead and its helpers are extracted verbatim from tracker_template.html and run in node,
the way _boardstagetest.py does. Invented data only.
Run: DEALFLOW_DIR=<temp> python _boardmergetest.py"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()


def fn(name):
    i = SRC.index('function %s(' % name)
    j = SRC.index('{', i)
    d = 0
    for k in range(j, len(SRC)):
        if SRC[k] == '{': d += 1
        elif SRC[k] == '}':
            d -= 1
            if d == 0:
                return SRC[i:k + 1]
    raise AssertionError(name)


def region(a, b):
    i = SRC.index(a)
    return SRC[i:SRC.index(b, i)]


BLOCK = '\n'.join([fn('_today'), fn('_nowTS'), fn('_devSrc'),
                   region("const _DNC = s =>", "function mergeNotes(")])

HARNESS = r'''
var fs = require('fs');
var cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
eval(fs.readFileSync(process.argv[3], 'utf8'));
console.log(JSON.stringify(cases.map(function(c){ return _mergeLead(c[0], c[1])[0]; })));
'''

UNIT_OLD = {'status': 'Appointment', 'afterYesAt': 100, 'stageAt': {'Appointment': '2026-10-20'},
            'nextStep': {'date': '2026-10-25', 'what': 'old'}, 'next': '2026-10-25', 'keepOrSell': 'sell',
            'equityVerified': {'ok': False}}
UNIT_NEW = {'status': 'Offer made', 'afterYesAt': 200, 'stageAt': {'Appointment': '2026-10-22', 'Offer made': '2026-10-28'},
            'nextStep': {'date': '2026-11-03', 'what': 'new'}, 'next': '2026-11-03', 'keepOrSell': 'keep',
            'equityVerified': {'ok': True}}


def main():
    fails = []

    def eq(n, g, w):
        if g != w:
            fails.append('%s: got %r want %r' % (n, g, w))
    C = [
        (dict(UNIT_OLD), dict(UNIT_NEW)),                                      # 0 teammate newer wins
        (dict(UNIT_NEW), dict(UNIT_OLD)),                                      # 1 older push: no walk back
        ({'status': 'Appointment', 'afterYesAt': 100, 'touches': [{'d': '2026-10-01', 'ch': 'call', 'out': 'x'}]},
         dict(UNIT_NEW, status='DO NOT CONTACT')),                             # 2 DNC remote, no next step
        (dict(UNIT_OLD, status='DO NOT CONTACT'), dict(UNIT_NEW)),             # 3 DNC local stays
        (dict(UNIT_OLD, status='Dead'), dict(UNIT_NEW)),                       # 4 Dead local stays
        ({'status': 'Contacted'}, dict(UNIT_NEW, status='Dead')),              # 5 Dead remote, no next step
        ({'status': 'Contacted'}, {'status': 'Contacted', 'replied': '2026-10-29 10:00:00', 'repliedNote': 'hi'}),  # 6
        ({'status': 'Contacted', 'replied': '2026-10-01 09:00:00'}, {'status': 'Contacted'}),   # 7 sticky local
        ({'status': 'Appointment', 'afterYesAt': 100, 'stageAt': {'Appointment': '2026-10-20', 'Offer made': '2026-10-30'}},
         {'status': 'Offer made', 'afterYesAt': 200, 'stageAt': {'Appointment': '2026-10-22', 'Offer made': '2026-10-28'}}),  # 8
        ({'status': 'Appointment', 'afterYesAt': 100, 'stageAt': {'Appointment': '2026-10-20'}, 'nextStep': {'date': '2026-10-25', 'what': 'a'}},
         dict(UNIT_NEW, status='Dead')),                                       # 9 dead remote vs local with unit
    ]
    d = tempfile.mkdtemp()
    fx, blk, hj = (os.path.join(d, n) for n in ('fx.json', 'block.js', 'h.js'))
    json.dump(C, open(fx, 'w'))
    open(blk, 'w', encoding='utf-8').write(BLOCK)
    open(hj, 'w').write(HARNESS)
    out = subprocess.run([os.environ.get('NODE', 'node'), hj, fx, blk], capture_output=True, text=True)
    if out.returncode:
        print('FAIL node:', out.stderr[-800:]); sys.exit(1)
    r = json.loads(out.stdout)
    eq('newer next step wins', (r[0]['nextStep'], r[0]['next'], r[0]['afterYesAt']), (UNIT_NEW['nextStep'], '2026-11-03', 200))
    eq('newer unit: equity+keepOrSell', (r[0]['equityVerified'], r[0]['keepOrSell']), ({'ok': True}, 'keep'))
    eq('newer unit stage', r[0]['status'], 'Offer made')
    eq('older push keeps next step', r[1]['nextStep'], UNIT_NEW['nextStep'])
    eq('older push keeps stage', r[1]['status'], 'Offer made')
    eq('older push keeps afterYesAt', r[1]['afterYesAt'], 200)
    eq('remote DNC wins', r[2]['status'], 'DO NOT CONTACT')
    eq('remote DNC gets no next step', ('nextStep' in r[2], 'next' in r[2], 'keepOrSell' in r[2]), (False, False, False))
    eq('local DNC not overridden', r[3]['status'], 'DO NOT CONTACT')
    eq('local DNC not given teammate next step', r[3]['nextStep'], UNIT_OLD['nextStep'])
    eq('local DNC afterYesAt untouched', r[3]['afterYesAt'], 100)
    eq('local Dead not overridden', r[4]['status'], 'Dead')
    eq('local Dead keeps its own next step', r[4]['nextStep'], UNIT_OLD['nextStep'])
    eq('remote Dead wins', r[5]['status'], 'Dead')
    eq('remote Dead gets no next step', ('nextStep' in r[5], 'next' in r[5], 'afterYesAt' in r[5]), (False, False, False))
    eq('replied taken from teammate', (r[6].get('replied'), r[6].get('repliedNote')), ('2026-10-29 10:00:00', 'hi'))
    eq('replied sticky when remote lacks it', r[7].get('replied'), '2026-10-01 09:00:00')
    eq('stageAt earliest per stage', r[8]['stageAt'], {'Appointment': '2026-10-20', 'Offer made': '2026-10-28'})
    eq('dead remote: local unit untouched', (r[9]['status'], r[9]['nextStep'], r[9]['afterYesAt']), ('Dead', {'date': '2026-10-25', 'what': 'a'}, 100))
    if fails:
        print('FAIL\n' + '\n'.join(fails)); sys.exit(1)
    print('board merge test: ok')


main()
