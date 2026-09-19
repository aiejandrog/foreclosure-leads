"""BOARD-LANE PARITY — the nine funnel lanes must read the same on the phone as on the board.

WHY THIS TEST EXISTS (2026-09-17, Alejandro)
He reads nine counts off the board — WARM 19 / URGENT 129 / CALL 610 / WRITE 14 / LETTER 190 /
DOOR 1 / TRACE 1068 / WAITING 68 / OUT 198 — and asked for the same nine on the phone. They were
not there at all: Call Mode had nine lanes of its OWN (3-DAY, Emailed, Worker, Urgent 0-7, Sale
soon, 46-60, Fresh filings, Balloon, Buy-box) over ~400 dialable rows out of 2,297 leads. Two
vocabularies over two populations, so no number agreed and no lead could be followed between them.

Parity now rests on two things, and this test checks both, because either alone is worthless:

  1. ONE CLASSIFIER. _funnelStage is extracted from tracker_template.html at build time and runs on
     the phone as the same bytes (call_mode.extract_funnel_js). A COPY would drift, which is the
     documented reason extract_sync_js exists and the reason this codebase already carried three
     lane vocabularies.
  2. THE WHOLE BOOK. Every lead reaches the phone — the dialable ones as full rows, the rest as
     slim coverage rows (coverage_rows). A perfect classifier over two thirds of the leads still
     prints the wrong number on the button.

So this runs the board's classifier over board-shaped leads, runs the phone's over the rows the
phone actually ships, and compares LEAD BY LEAD, not just the totals — two lanes can be off by the
same lead in opposite directions and still total correctly.

No Playwright, no browser, no real data: node evaluates the extracted block directly. That is on
purpose — a test that needs the live board cannot run on a machine that has no leads_final.json,
which is every machine this repo is checked out on except the armed runner.

    python _funnelparitytest.py
"""
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_mode as CM                                                   # noqa: E402

ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


TODAY = datetime.date.today()
NOW = int(time.time() * 1000)


def _auc(n):
    """An auction date n days out, in the board's m/d/Y — the format _saleDays parses."""
    return (TODAY + datetime.timedelta(days=n)).strftime('%m/%d/%Y')


def build_fixture():
    """Leads that land in all nine lanes, plus the gates that used to drop leads off the phone.

    Deliberately includes the cases the dial queue REFUSES — no number, sale past its 60-day
    window, do-not-call on every number, already suppressed. Those are exactly the leads coverage
    rows exist to carry, and a fixture of dialable leads would prove nothing.
    """
    leads, notes = [], {}
    n = [0]

    def lead(**kw):
        n[0] += 1
        i = n[0]
        d = {'case': '2024-%06d-CA-01' % i, 'owners': 'OWNER %d' % i, 'oname': 'Owner %d' % i,
             'addr': '%d NW 1ST ST, MIAMI, FL 33136' % (100 + i),
             'mail': '%d NW 1ST ST, MIAMI, FL 33136' % (100 + i),
             'value': 500000, 'judg': 200000, 'eq': 60, 'st': 'FC', 'county': 'MIAMI-DADE',
             'folio': '0101000000%04d' % i, 'phones': [], 'phdnc': [], 'emails': [],
             'eqstate': 'clear', 'pkey': 'P%d' % i}
        d.update(kw)
        leads.append(d)
        return d['case']

    def ph():
        return ['305555%04d' % n[0]]

    def touch(case, ch, out='', ago_h=1):
        notes.setdefault(case, {'status': '', 'touches': []})['touches'].append(
            {'ch': ch, 'out': out, 'tsu': NOW - int(ago_h * 3600000)})

    for _ in range(5):                      # URGENT — sale inside a week
        lead(auction=_auc(3), days=3, phones=ph(), phdnc=[False])
    for _ in range(7):                      # CALL — phone, never called
        lead(auction=_auc(30), days=30, phones=ph(), phdnc=[False])
    for _ in range(9):                      # CALL, but BEYOND the dial queue's 60-day horizon
        lead(auction=_auc(120), days=120, phones=ph(), phdnc=[False])
    for _ in range(11):                     # TRACE — no phone, no email
        lead(auction=_auc(30), days=30)
    for _ in range(3):                      # every number do-not-call — see coverage_rows on `np`
        lead(auction=_auc(30), days=30, phones=ph(), phdnc=[True])
    for _ in range(4):                      # WRITE — email only
        lead(auction=_auc(30), days=30, emails=['o%d@example.com' % n[0]])
    for _ in range(2):                      # OUT — the lis pendens was dismissed
        lead(auction='', days=9999, st='LP', lpDismissed=True, phones=ph(), phdnc=[False])
    for _ in range(2):                      # OUT — the auction already happened
        lead(auction=_auc(-5), days=-5, phones=ph(), phdnc=[False])
    for _ in range(6):                      # fresh lis pendens, no sale date
        lead(auction='', days=9999, st='LP', phones=ph(), phdnc=[False])
    for _ in range(3):                      # WARM — somebody actually spoke to them
        touch(lead(auction=_auc(30), days=30, phones=ph(), phdnc=[False]), 'call', 'spoke with owner', 200)
    for _ in range(5):                      # WAITING — touched inside the 48h cooldown
        touch(lead(auction=_auc(30), days=30, phones=ph(), phdnc=[False]), 'call', 'no answer', 2)
    for _ in range(6):                      # LETTER — called + written, mail still has runway
        c = lead(auction=_auc(40), days=40, phones=ph(), phdnc=[False],
                 emails=['l%d@example.com' % n[0]])
        touch(c, 'call', 'no answer', 200)
        touch(c, 'email', 'sent', 190)
    for _ in range(2):                      # DOOR — everything else was tried
        c = lead(auction=_auc(40), days=40, phones=ph(), phdnc=[False],
                 emails=['d%d@example.com' % n[0]])
        for ch in ('call', 'email', 'letter'):
            touch(c, ch, 'done', 200)
    return leads, notes


_HARNESS = r'''
const fs = require('fs');
const fx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
var notes = fx.notes || {};
/* The board bakes a case-keyed server opt-out straight into notes (SERVER_OPTOUTS in
   tracker_template.html) — mirror that, or the board side of this comparison is not the board. */
notes[fx.optcase] = {optout: '2026-09-17', status: 'DO NOT CONTACT'};
eval(fs.readFileSync(process.argv[3], 'utf8'));      // the board's classifier, extracted verbatim
var ROWS = fx.rows;
eval(fs.readFileSync(process.argv[4], 'utf8'));      // the phone's adapter + counters
COV = fx.cov;   /* AFTER the eval: the block declares `var COV = []` and would wipe a pre-set value */

var boardStage = {}, boardCounts = {}, phoneStage = {};
FUNNEL_ORDER.forEach(function(k){ boardCounts[k] = 0; });
fx.slim.forEach(function(r){
  var s = _funnelStage(r);
  boardStage[r.case] = s;
  if (s && boardCounts[s] != null) boardCounts[s]++;
});
ROWS.concat(COV).forEach(function(r){ phoneStage[r.c] = funnelOf(r); });

console.log(JSON.stringify({
  boardCounts: boardCounts,
  phoneCounts: funnelCounts(),
  order: FUNNEL_ORDER,
  labels: FUNNEL_ORDER.reduce(function(a,k){ a[k]=FUNNEL[k].t; return a; }, {}),
  missing: fx.slim.filter(function(r){ return !(r.case in phoneStage); }).map(function(r){ return r.case; }),
  differing: fx.slim.filter(function(r){
      return (r.case in phoneStage) && phoneStage[r.case] !== boardStage[r.case]; })
    .map(function(r){ return r.case + ' board=' + boardStage[r.case] + ' phone=' + phoneStage[r.case]; }),
  onPhone: Object.keys(phoneStage).length
}));
'''


def page_glue(page):
    """The adapter + counters out of _PAGE: everything between the injected funnel block and the
    board-lane UI. Sliced rather than duplicated here for the same reason the block itself is
    extracted rather than copied — a copy in a TEST is the worst copy of all, because it passes."""
    i = page.find('__FUNNELJS__')
    assert i > 0, '__FUNNELJS__ is gone from _PAGE'
    i = page.find('*/', i) + 2
    j = page.find("THE BOARD'S NINE LANES, ON THE PHONE")
    assert j > i, 'the board-lane UI marker moved; update page_glue'
    glue = page[i:page.rfind('/*', i, j)]
    # A __PLACEHOLDER__ in this window is a block injected between the funnel code and the board-lane
    # UI. node gets it as a bare identifier and dies with a module-loader stack that says nothing
    # about why — so name the cause here instead. Move the injection out of this window, or widen
    # the slice deliberately and glue that block in too.
    stray = re.findall(r'__[A-Z0-9_]+__', glue)
    assert not stray, ('page_glue sliced an uninjected placeholder (%s) — something new is being '
                       'injected into _PAGE between __FUNNELJS__ and the board-lane UI. This runs '
                       'under node, so a placeholder token here is a syntax error.' % ', '.join(sorted(set(stray))))
    return glue


def main():
    print('BOARD-LANE PARITY')
    src = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()

    # 1) the extraction itself — anchors present, every name resolves
    try:
        funnel_js = CM.extract_funnel_js(src)
        rec('funnel block extracts from tracker_template.html', True, '%d bytes' % len(funnel_js))
    except Exception as e:
        rec('funnel block extracts from tracker_template.html', False, str(e)[:200])
        return 1

    leads, notes = build_fixture()
    optouts = {leads[0]['case']: {'src': 'test'}}
    rows, _total = CM.call_rows(leads, optouts, {})
    cov, _sup = CM.coverage_rows(leads, [r['c'] for r in rows], optouts, {})

    # 2) the whole book reaches the phone
    rec('every lead is carried (dial queue + coverage)', len(rows) + len(cov) == len(leads),
        '%d dial + %d coverage vs %d leads' % (len(rows), len(cov), len(leads)))
    rec('the dial queue is genuinely a subset — this fixture is not all-dialable',
        len(rows) < len(leads), '%d of %d' % (len(rows), len(leads)))

    # 3) NO PHONE NUMBERS ON A COVERAGE ROW. Structural, not a flag: what is not in the payload
    #    cannot be dialled, and this is the page whose founding rule is exactly that.
    blob = json.dumps(cov)
    import re as _re
    leak = _re.findall(r'(?<!\d)\d{10}(?!\d)', blob)
    rec('coverage rows carry no phone number', not leak, leak[:3])
    rec('coverage rows carry no email address', '@' not in blob)

    with tempfile.TemporaryDirectory() as td:
        fxp = os.path.join(td, 'fx.json')
        fjs = os.path.join(td, 'funnel.js')
        gjs = os.path.join(td, 'glue.js')
        hjs = os.path.join(td, 'h.js')
        json.dump({'slim': leads, 'rows': rows, 'cov': cov, 'notes': notes,
                   'optcase': leads[0]['case']}, open(fxp, 'w'))
        open(fjs, 'w', encoding='utf-8').write(funnel_js)
        open(gjs, 'w', encoding='utf-8').write(page_glue(CM._PAGE))
        open(hjs, 'w', encoding='utf-8').write(_HARNESS)
        try:
            out = subprocess.run([os.environ.get('NODE', 'node'), hjs, fxp, fjs, gjs],
                                 capture_output=True, timeout=120, text=True)
        except FileNotFoundError:
            rec('node is available to run the extracted block', False,
                'node not found — install it or set NODE=<path>')
            return 1
        if out.returncode != 0:
            rec('the extracted block runs', False, (out.stderr or '')[-400:])
            return 1
        rec('the extracted block runs', True)
        R = json.loads(out.stdout.strip().splitlines()[-1])

    rec('every lead classified on the phone too', not R['missing'], R['missing'][:5])
    rec('no lead lands in a different lane on the two surfaces', not R['differing'],
        '\n        '.join(R['differing'][:6]))

    print('\n  lane          board   phone')
    for k in R['order']:
        b, p = R['boardCounts'][k], R['phoneCounts'][k]
        print('    %-10s %5d   %5d %s' % (R['labels'][k], b, p, '' if b == p else '  <-- MISMATCH'))
    print('')
    for k in R['order']:
        rec('lane %s counts the same on both' % R['labels'][k],
            R['boardCounts'][k] == R['phoneCounts'][k],
            'board %d vs phone %d' % (R['boardCounts'][k], R['phoneCounts'][k]))

    print('\n%d passed, %d failed' % (len(ok), len(bad)))
    if bad:
        print('FAILED: ' + ', '.join(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
