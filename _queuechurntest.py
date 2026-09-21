"""_queuechurntest -- why the same people came back to the text batch and the dial list every day.

Run:  python _queuechurntest.py   (exit 0 = safe; no network, no browser, no board, no real data)

REPORTED 2026-09-19 by Alejandro: "why do i keep texting the same people on the call mode and
morning worker why is it the same people over and over again never new people to text?"

Three independent defects, all of them the same shape -- a queue whose ORDER never changes:

  1. fcCallQueue NEVER RETIRED ON A TEXT OR AN EMAIL. The durable call-queue ledger is add-only,
     and only a logged CALL OUTCOME (`callout`) or a bad-number report took an entry out of it.
     genMorningWorker re-bakes that ledger into PERSISTQ on every open, and PERSISTQ seeds TEXTQ
     FIRST and IN INSERTION ORDER. So a confirmed text wrote its CRM touch, the lead vanished for
     its 72h cooldown, and then came back to the IDENTICAL position at the head of the batch. The
     cap is consumed by that head, so leads that joined later were never reached at all. Now every
     branch that establishes a confirmed contact calls one shared `_callqRetire`.

  2. PERSISTQ ARRIVED IN INSERTION ORDER, which is append-only and therefore frozen. It is now
     sorted least-recently-contacted first, so a lead nobody has ever touched opens the morning.

  3. THE PHONE-ONLY BUCKET ROTATED BY 7 A DAY THROUGH A 60-WIDE WINDOW. Consecutive mornings
     overlapped by 53 of 60 -- 88% the same faces, which from the operator's chair is
     indistinguishable from no rotation. The stride is now the window, so consecutive days are
     disjoint. Same defect on the phone: call_mode.call_rows sliced `out[:cap]` off a fully
     deterministic sort, so the ~400 that shipped were the same ~400 every night and anything
     ranked past the cap never reached the handset. The head is protected (that is the point of
     the rank); only the remaining slots rotate.

HOW THIS TESTS. The board JS is EXTRACTED BY NAME from tracker_template.html and run under node, so
what is asserted is the code that ships, not a copy that can drift. call_rows is called for real on
synthetic rows. Anything that cannot be executed is asserted as an anchored source shape, and the
anchor is named in the failure so a rename fails loudly here instead of silently on the board.

NOTHING HERE TOUCHES THE RESERVED SUPPRESSION SURFACE. _workerEligible, _isOptedOutPerson,
_laneStats, the opt-out ledger and the send-time gates are read, never changed -- see CLAUDE.md.
"""
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, 'tracker_template.html')

fails = []
checks = []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    if len(extra) > 180:
        extra = extra[:180] + ' ...'
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra) if extra else ''))
    if not ok:
        fails.append(name)


SRC = io.open(TPL, encoding='utf-8').read()


def grab(anchor, end, what):
    """Slice the shipped source between two anchors, or fail loudly naming the anchor."""
    i = SRC.find(anchor)
    if i < 0:
        rec('anchor present: ' + what, False, 'missing anchor ' + repr(anchor[:60]))
        return ''
    j = SRC.find(end, i + len(anchor))
    if j < 0:
        rec('anchor present: ' + what, False, 'missing end anchor ' + repr(end[:60]))
        return ''
    rec('anchor present: ' + what, True)
    return SRC[i:j]


def node(js):
    """Run a snippet under node; return (rc, stdout, stderr)."""
    fd, path = tempfile.mkstemp(suffix='.js')
    os.close(fd)
    io.open(path, 'w', encoding='utf-8').write(js)
    try:
        p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path],
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.remove(path)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 1. ONE RETIRE HELPER, AND EVERY CONFIRMED CONTACT CALLS IT
# ══════════════════════════════════════════════════════════════════════════════════════════════
print('\n-- 1. fcCallQueue retires on every confirmed contact --')

def grab_fn(name):
    """The whole of a top-level function, closing brace included."""
    i = SRC.find('function ' + name + '(')
    if i < 0:
        rec('anchor present: ' + name, False, 'no such function in the shipped source')
        return ''
    j = SRC.find('\n}\n', i)
    rec('anchor present: ' + name, j > 0)
    return SRC[i:j + 2] if j > 0 else ''


helper = grab_fn('_callqRetire')
rec('the helper reads and rewrites fcCallQueue',
    "localStorage.getItem('fcCallQueue')" in helper and "setItem('fcCallQueue'" in helper, helper[:80])
rec('the helper stamps the wqx tombstone (a deleted wq cannot survive the add-only merge)',
    'wqx' in helper and 'n.wq' in helper)

# Exactly one place may write the ledger back, or the four callers can drift apart again.
writes = len(re.findall(r"setItem\('fcCallQueue'", SRC))
rec('exactly one writer of the retire (the helper) plus the callq ADD',
    writes == 2, 'found %d setItem(fcCallQueue) sites, expected 2 (add + retire)' % writes)

# Every branch that establishes a confirmed contact must retire. `textopen` and `email` must NOT:
# they are attempts, and an attempt that retired a lead is the 2026-08-02 drain all over again.
BRANCHES = SRC[SRC.find("\n  } else if(d.k === 'callout'){"):SRC.find("\n  } else if(d.k === 'wait'){")]
for k, want in (('callout', True), ('text', True), ('textopen', False),
                ('wp', False), ('badnum', True), ('callq', False)):
    i = BRANCHES.find("d.k === '" + k + "'")
    j = BRANCHES.find("} else if(d.k ===", i + 5)
    body = BRANCHES[i:(j if j > 0 else len(BRANCHES))]
    got = '_callqRetire(' in body
    rec("branch '%s' %s retire the durable call queue" % (k, 'DOES' if want else 'does NOT'),
        got == want and bool(body), 'body %d chars, retire=%s' % (len(body), got))

# The email path: 'sent' is confirmed delivery and retires; 'email' is a composer-opened attempt
# and must not. They live above `callout`, so slice them separately.
EMAILB = SRC[SRC.find("\n  if(d.k === 'email'){"):SRC.find("\n  } else if(d.k === 'callout'){")]
attempt = EMAILB[:EMAILB.find("\n  } else if(d.k === 'sent'){")]
sent = EMAILB[EMAILB.find("\n  } else if(d.k === 'sent'){"):]
rec("branch 'email' (composer opened) does NOT retire -- an attempt is not a contact",
    '_callqRetire(' not in attempt and len(attempt) > 100, len(attempt))
rec("branch 'sent' (confirmed delivery) DOES retire",
    '_callqRetire(' in sent and len(sent) > 100, len(sent))

# Behaviour, for real, under node.
rc, out, err = node(helper + """
var _store = {fcCallQueue: JSON.stringify([{c:'A',ts:1},{c:'B',ts:2},{c:'C',ts:3}])};
var localStorage = {getItem:function(k){return _store[k];},
                    setItem:function(k,v){_store[k]=v;}};
var notes = {A:{wq:'2026-09-18'}, B:{}};
function _today(){ return '2026-09-19'; }
var r1 = _callqRetire('A', notes.A);
var r2 = _callqRetire('B', notes.B);
var r3 = _callqRetire('ZZZ', null);
console.log(JSON.stringify({left:JSON.parse(_store.fcCallQueue).map(function(x){return x.c;}),
                            r1:r1, r2:r2, r3:r3, wqxA:notes.A.wqx||null, wqxB:notes.B.wqx||null}));
""")
rec('_callqRetire runs under node', rc == 0, (err or '')[:200])
if rc == 0:
    got = json.loads(out.strip().splitlines()[-1])
    rec('a retired case leaves the ledger', got['left'] == ['C'], got['left'])
    rec('it reports whether it removed anything', got['r1'] is True and got['r3'] is False, got)
    rec('a case that was SYNCED (wq set) gets the wqx tombstone so other devices retire too',
        got['wqxA'] == '2026-09-19', got['wqxA'])
    rec('a case never synced gets no tombstone (nothing to tomb)', got['wqxB'] is None, got['wqxB'])
    rec('retiring a case that is not in the ledger is a safe no-op', got['r3'] is False)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 2. PERSISTQ IS ORDERED BY WHO WE HAVE GONE LONGEST WITHOUT CONTACTING
# ══════════════════════════════════════════════════════════════════════════════════════════════
print('\n-- 2. PERSISTQ order --')

pq = grab('  var persistq = [];', '  // Deliverability state bakes in at open',
          'persistq build + sort')
rec('persistq carries a last-touch key for the sort', '_lt:_lastTouchMs(' in pq, pq[-400:-200])
rec('persistq is SORTED, not left in append order', 'persistq.sort(' in pq)
rec('the sort is least-recently-contacted first, queue age as tie-break',
    'a._lt - b._lt' in pq and 'a._ts - b._ts' in pq)
rec('the scratch sort keys are stripped before the list is baked into the worker document',
    'delete x._lt' in pq and 'delete x._ts' in pq)
rec('persistq still drops ineligible leads through the RESERVED gate, unchanged',
    '_workerEligible(r)' in pq)

rc, out, err = node("""
var persistq = [{c:'old-untouched', _lt:0, _ts:5},
                {c:'fresh-untouched', _lt:0, _ts:99},
                {c:'texted-today', _lt:1000000, _ts:1},
                {c:'texted-last-week', _lt:500000, _ts:2}];
persistq.sort(function(a, b){ return (a._lt - b._lt) || (a._ts - b._ts); });
persistq.forEach(function(x){ delete x._lt; delete x._ts; });
console.log(JSON.stringify(persistq.map(function(x){return x.c;})));
console.log(JSON.stringify(Object.keys(persistq[0])));
""")
rec('the persistq sort runs under node', rc == 0, (err or '')[:200])
if rc == 0:
    lines = out.strip().splitlines()
    order = json.loads(lines[0])
    rec('never-contacted leads open the morning, oldest queue entry first',
        order[:2] == ['old-untouched', 'fresh-untouched'], order)
    rec('the person texted today sorts LAST -- they are not owed another contact',
        order[-1] == 'texted-today', order)
    rec('nothing but the payload keys survive into the baked list',
        json.loads(lines[1]) == ['c'], lines[1])


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 3. THE PHONE-ONLY WINDOW ACTUALLY MOVES
# ══════════════════════════════════════════════════════════════════════════════════════════════
print('\n-- 3. the phone-only bucket rotation --')

rot = grab('  var _PH_WINDOW = 60;', '  /* BOUND THE EMAIL QUEUE', 'phone-only rotation block')
rec('the window size is named once and used for both the stride and the slice',
    '_PH_WINDOW' in rot and 'new Date().getDate() * _PH_WINDOW' in rot, rot[-160:])
rec('the slice at the end of _workerQueue uses the same constant, not a second literal 60',
    'return _out.concat(_ph.slice(0, _PH_WINDOW));' in SRC)
rec('the old 7-a-day stride is gone',
    'new Date().getDate() * 7' not in SRC)

# Consecutive days must not re-serve the same faces. Asserted against the OLD formula too, so this
# file records the size of the defect rather than just the fix.
rc, out, err = node("""
function windows(stride, W, N){
  var seen = [];
  for(var day = 1; day <= 28; day++){
    var rot = (day * stride) % N, ph = [];
    for(var k = 0; k < W; k++) ph.push((rot + k) % N);
    seen.push(ph);
  }
  return seen;
}
function overlap(a, b){
  var s = {}; a.forEach(function(x){ s[x] = 1; });
  return b.filter(function(x){ return s[x]; }).length;
}
function worst(w){ var m = 0; for(var i = 1; i < w.length; i++) m = Math.max(m, overlap(w[i-1], w[i])); return m; }
function covered(w, N){ var s = {}; w.forEach(function(d){ d.forEach(function(x){ s[x] = 1; }); }); return Object.keys(s).length; }
var N = 420, W = 60;
var oldW = windows(7, W, N), newW = windows(W, W, N);
console.log(JSON.stringify({oldWorst: worst(oldW), newWorst: worst(newW),
                            oldCover: covered(oldW, N), newCover: covered(newW, N), N: N}));
""")
rec('the rotation arithmetic runs under node', rc == 0, (err or '')[:200])
if rc == 0:
    g = json.loads(out.strip().splitlines()[-1])
    rec('THE DEFECT: the old stride re-served 53 of 60 leads the next morning',
        g['oldWorst'] == 53, g['oldWorst'])
    rec('the new stride makes consecutive mornings DISJOINT',
        g['newWorst'] == 0, g['newWorst'])
    rec('and it still covers the whole bucket within a month',
        g['newCover'] == g['N'], '%d of %d' % (g['newCover'], g['N']))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 4. CALL MODE: THE CAP IS A WINDOW, NOT A CEILING
# ══════════════════════════════════════════════════════════════════════════════════════════════
print('\n-- 4. call_mode.call_rows cap rotation --')

sys.path.insert(0, HERE)
import call_mode  # noqa: E402


def slim(n, start=0):
    """n synthetic leads, ranked strictly by their index (equity descending)."""
    return [{'case': 'C%04d' % i, 'owners': 'OWNER %d' % i, 'oname': 'Owner %d' % i,
             'addr': '%d MAIN ST, MIAMI FL 33101' % i, 'phones': ['305555%04d' % i],
             'phdnc': [False], 'days': 30, 'auction': '10/20/2026',
             'value': 300000, 'eq': 90 - (i * 0.1)}
            for i in range(start, start + n)]


def cases(rows):
    return [r['c'] for r in rows]


import contextlib  # noqa: E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rows, total = call_mode.call_rows(slim(600))
noise = buf.getvalue()
rec('call_rows still reports the qualified total, not the shipped count', total == 600, total)
rec('it still ships exactly the cap', len(rows) == 400, len(rows))
rec('NO SILENT CAP: it says out loud that it shipped a window',
    'qualified' in noise and 'rotate daily' in noise, noise.strip().splitlines()[-1:])

head = cases(rows)[:200]
rec('the protected head is the top of the rank, in rank order -- the session still opens on the '
    'highest-converting leads', head == ['C%04d' % i for i in range(200)], head[:3] + head[-2:])

# The tail must actually move between days. call_rows keys off the real date, so drive it by
# monkeypatching the module's date handle rather than by waiting a day.
class _FakeDate:
    day = 1

    @classmethod
    def today(cls):
        return cls


real = call_mode._dt.date
seen_tails = {}
try:
    call_mode._dt.date = _FakeDate
    for d in range(1, 29):
        _FakeDate.day = d
        with contextlib.redirect_stdout(io.StringIO()):
            r, _ = call_mode.call_rows(slim(600))
        seen_tails[d] = cases(r)[200:]
finally:
    call_mode._dt.date = real

rec('the head never rotates away (checked across 28 days)',
    all(cases(rows)[:200] == head for _ in [0]))
day_pairs = [(seen_tails[d], seen_tails[d + 1]) for d in range(1, 28)]
worst = max(len(set(a) & set(b)) for a, b in day_pairs)
rec('consecutive days do not re-serve the same tail', worst == 0, 'worst overlap %d' % worst)
reached = set()
for d in seen_tails:
    reached |= set(seen_tails[d])
rec('every lead past the head reaches the phone within the month',
    reached == {'C%04d' % i for i in range(200, 600)},
    '%d of 400 tail leads reached' % len(reached))

# Under the cap nothing rotates and nothing is dropped -- the small-book path must be untouched.
with contextlib.redirect_stdout(io.StringIO()):
    small, stotal = call_mode.call_rows(slim(120))
rec('a book under the cap ships whole, in pure rank order, with no rotation',
    stotal == 120 and cases(small) == ['C%04d' % i for i in range(120)], len(small))

# The head is env-tunable, and a head of 0 means the whole cap rotates.
# Pinned to day 2: on day 21 the offset is 21*400 % 600 == 0, so an unpinned run of this check
# fails one day a month for a reason that has nothing to do with the code.
os.environ['CALLMODE_HEAD'] = '0'
try:
    call_mode._dt.date = type('D2', (_FakeDate,), {'day': 2})
    with contextlib.redirect_stdout(io.StringIO()):
        allrot, _ = call_mode.call_rows(slim(600))
finally:
    call_mode._dt.date = real
    os.environ.pop('CALLMODE_HEAD', None)
rec('CALLMODE_HEAD=0 rotates the whole cap', len(allrot) == 400 and cases(allrot)[0] != 'C0000',
    cases(allrot)[:2])


# FRESH FILINGS (2026-09-21). On the live 09-21 book, 30 of the 32 leads filed in the last week sat
# at ranks 222-251 — the front of the tail — and the rotation skipped that front two days in three,
# so they were on neither phone. A lead filed within CALLMODE_FRESH_DAYS must ship EVERY day.
def _fresh_book(today=None):
    today = today or dt.date.today()
    b = slim(600)
    for i in range(230, 262):                      # just past the head, like the real book
        b[i]['filedDate'] = (today - dt.timedelta(days=3)).strftime('%m/%d/%Y')
    b[500]['filedDate'] = '1/5/2024'               # an old filing is NOT pinned
    return b


class _RealFakeDate(dt.date):
    """A real date (so date arithmetic works) whose today() is the day under test."""
    pinned = dt.date(2026, 9, 1)

    @classmethod
    def today(cls):
        return cls.pinned


fresh_cases = {'C%04d' % i for i in range(230, 262)}
missed_days = []
try:
    call_mode._dt.date = _RealFakeDate
    for d in range(1, 29):
        _RealFakeDate.pinned = _RealFakeDate(2026, 9, d)
        with contextlib.redirect_stdout(io.StringIO()):
            r, _ = call_mode.call_rows(_fresh_book(dt.date(2026, 9, d)))
        if not fresh_cases <= set(cases(r)) or len(r) != 400:
            missed_days.append(d)
finally:
    call_mode._dt.date = real
rec('a lead filed in the last CALLMODE_FRESH_DAYS ships every day of the month, never waiting on '
    'its rotation day', not missed_days, 'missed on days %s' % missed_days[:5])
with contextlib.redirect_stdout(io.StringIO()):
    fr, _ = call_mode.call_rows(_fresh_book())
rec('the pinned fresh filings ride directly behind the head, in rank order',
    cases(fr)[200:232] == ['C%04d' % i for i in range(230, 262)], cases(fr)[200:203])
rec('pinning does not touch the protected head', cases(fr)[:200] == ['C%04d' % i for i in range(200)])
# Pins are capped at half the slots, so a flood of fresh filings cannot freeze the rotation.
_flood = slim(600)
for _r in _flood[200:]:
    _r['filedDate'] = dt.date.today().strftime('%m/%d/%Y')
try:
    call_mode._dt.date = _RealFakeDate
    _RealFakeDate.pinned = _RealFakeDate(2026, 9, 2)      # day 2: rotation offset is non-zero
    for _r in _flood[200:]:
        _r['filedDate'] = '9/1/2026'
    with contextlib.redirect_stdout(io.StringIO()):
        fl, _ = call_mode.call_rows(_flood)
finally:
    call_mode._dt.date = real
rec('fresh pins are capped at half the rotating slots', len(fl) == 400 and cases(fl)[300] != 'C0300',
    cases(fl)[299:302])
# The cap scales: a two-seat crew passes cap=800 and the head scales with it.
with contextlib.redirect_stdout(io.StringIO()):
    big, _ = call_mode.call_rows(slim(1000), cap=800)
rec('cap=800 ships 800 with a 400-row protected head',
    len(big) == 800 and cases(big)[:400] == ['C%04d' % i for i in range(400)], len(big))


# ══════════════════════════════════════════════════════════════════════════════════════════════
print('\n%d checks, %d failed' % (len(checks), len(fails)))
if fails:
    print('FAILED:')
    for f in fails:
        print('  - ' + f)
sys.exit(1 if fails else 0)
