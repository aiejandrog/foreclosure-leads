"""_boardstagetest.py - the board's after-the-yes counts, Overdue list and equity-card chip.
Non-browser: the functions are extracted verbatim from tracker_template.html and evaluated in node,
the same way _funnelparitytest.py does. Invented data only.
Run: DEALFLOW_DIR=<temp> python _boardstagetest.py"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()


def fn(name):
    """Source of `function name(...) {...}` by brace matching."""
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


def line(prefix):
    i = SRC.index(prefix)
    return SRC[i:SRC.index('\n', i)]


def region(a, b):
    i = SRC.index(a)
    return SRC[i:SRC.index(b, i)]


BLOCK = '\n'.join([
    line("const esc = s =>"),
    fn('_today'), fn('_effOptout'), fn('_isWrongOwner'),
    region("const _AY_OPEN", "function _nowTS()"),
    line("var _EQC_NAME"), fn('_eqcChip'),
])

HARNESS = r'''
var fs = require('fs');
var fx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
var notes = fx.notes;
eval(fs.readFileSync(process.argv[3], 'utf8'));
var T = '2026-10-29';
console.log(JSON.stringify({
  stats: _funnelStats(T),
  overdue: _overdueFollowups(notes, T),
  chips: fx.rows.map(function(r){ return _eqcChip(r, function(v){ return '$' + Math.round(v/1000) + 'k'; }); })
}));
'''

NOTES = {
  'A-1': {'status': 'Appointment', 'stageAt': {'Appointment': '2026-10-20'}, 'nextStep': {'date': '2026-10-25', 'what': 'meet'}, 'next': '2026-10-25'},   # overdue 4
  'A-2': {'status': 'Offer made', 'stageAt': {'Appointment': '2026-10-10', 'Offer made': '2026-10-15'}, 'nextStep': {'date': '2026-10-20', 'what': 'x'}},    # overdue 9
  'A-3': {'status': 'Under contract', 'stageAt': {'Under contract': '2026-10-28'}, 'nextStep': {'date': '2026-11-05', 'what': 'title'}},                     # fine
  'A-4': {'status': 'Appointment', 'apptUnverified': '2026-10-29'},                                                                                         # open, no next step
  'A-5': {'status': 'CLOSED', 'closedAmt': 12000, 'stageAt': {'Appointment': '2026-09-01'}},
  'A-6': {'status': 'Dead', 'stageAt': {'Appointment': '2026-10-01', 'Offer made': '2026-10-02'}, 'next': '2026-10-03'},                                   # reached offer, dead: counts, not overdue
  'A-7': {'status': 'Called - talked', 'next': '2026-10-28'},                                                                                                 # legacy overdue 1
  'A-8': {'status': 'Called - talked', 'next': '2026-10-29'},                                                                                                 # due today: not overdue
  'A-9': {'status': 'Appointment', 'next': '2026-10-01', 'optout': '2026-10-02'},                                                                             # opted out: off the list
  'A-10': {'status': 'Called - no answer', 'next': '2026-10-01'},                                                                                              # not a conversation yet
  '@p': {'status': 'Appointment', 'next': '2026-01-01'},
}
ROWS = [
  {'eqc': {'state': 'clear', 'may_quote': True, 'net': 300000, 'why': 'ok'}},
  {'eqc': {'state': 'clear', 'may_quote': False, 'net': 300000, 'why': 'judgment not posted'}},
  {'eqc': {'state': 'unchecked', 'may_quote': False, 'net': None}},
  {'eqc': {'state': 'bogus', 'may_quote': True, 'net': 5}},
  {},
]


def main():
    fails = []

    def eq(n, g, w):
        if g != w:
            fails.append('%s: got %r want %r' % (n, g, w))
    d = tempfile.mkdtemp()
    fx, blk = os.path.join(d, 'fx.json'), os.path.join(d, 'block.js')
    hj = os.path.join(d, 'h.js')
    json.dump({'notes': NOTES, 'rows': ROWS}, open(fx, 'w'))
    open(blk, 'w', encoding='utf-8').write(BLOCK)
    open(hj, 'w').write(HARNESS)
    out = subprocess.run([os.environ.get('NODE', 'node'), hj, fx, blk], capture_output=True, text=True)
    if out.returncode:
        print('FAIL node:', out.stderr[-600:]); sys.exit(1)
    r = json.loads(out.stdout)
    s = r['stats']
    eq('attempts', s['attempts'], 10)
    eq('contacts', s['contacts'], 9)
    eq('appointments', s['appointments'], 7)      # status OR stageAt: A-6 is Dead but reached Appointment
    eq('offers', s['offers'], 4)
    eq('contracts', s['contracts'], 2)
    eq('closes', s['closes'], 1)
    eq('revenue', s['revenue'], 12000)
    eq('apptUnverified', s['apptUnverified'], 1)
    eq('openDeals', s['openDeals'], 4)            # opted-out A-9 is not a deal in motion
    eq('noNext', s['noNext'], 1)
    eq('overdue count', s['overdue'], 2)
    eq('overdue list', [(o['c'], o['days']) for o in r['overdue']], [('A-2', 9), ('A-1', 4), ('A-7', 1)])
    eq('overdue what', r['overdue'][1]['what'], 'meet')
    c = r['chips']
    eq('chip may quote', ('may quote' in c[0], 'net ~$300k' in c[0]), (True, True))
    eq('chip no quote hides net', ('do not quote' in c[1], '300k' in c[1]), (True, False))
    eq('chip unchecked', 'Not checked' in c[2], True)
    eq('chip unknown state is not a fact', ('Not checked' in c[3]), True)
    eq('chip unknown state never quotes', 'do not quote' in c[3], True)
    eq('chip absent', c[4], '')
    # outcome_learn.after_yes_counts must agree with the board's _funnelStats on the same notes
    sys.path.insert(0, HERE)
    import datetime as dt
    import outcome_learn as OL
    a = OL.after_yes_counts(NOTES, dt.date(2026, 10, 29))
    eq('OL reached Appointment', a.get('reached Appointment'), s['appointments'])
    eq('OL reached Offer made', a.get('reached Offer made'), s['offers'])
    eq('OL reached Under contract', a.get('reached Under contract'), s['contracts'])
    eq('OL reached CLOSED', a.get('reached CLOSED'), s['closes'])
    eq('OL open deals', a.get('open deals'), s['openDeals'])
    eq('OL no next step', a.get('open deals with no dated next step'), s['noNext'])
    eq('OL overdue', a.get('open deals with next step overdue'), s['overdue'])
    eq('OL unverified appts', a.get('appointments booked on unverified equity'), s['apptUnverified'])
    if fails:
        print('FAIL\n' + '\n'.join(fails)); sys.exit(1)
    print('board stage test: ok')


main()
