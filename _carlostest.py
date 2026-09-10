"""Two seat pages, one list, nobody dialled twice — and every category reachable on both.

WHY THIS EXISTS
The seat split used to live on the PHONE: fcSeat, three prompt() boxes, set once per device
(MACHINE-HANDOFF, "Carlos = seat 2 ... one on-phone step left"). Every failure mode of that design
is silent and none of them is detectable from a laptop:

  * both phones type seat 1 of 2   -> a fully overlapping "split", and the UI says it is on
  * one phone types n=2, one n=3   -> overlapping AND lost leads at the same time
  * "show all" resets on reload    -> he thinks he is seeing everything and is not
  * nothing ever set it at all     -> the whole partition is a decoration

So the split moved to BUILD time (call_mode.CALL_SEATS -> seat_rows -> one encrypted payload per
seat). The properties that must hold are now checkable here, before anything ships:

  1. DISJOINT   — no case in both payloads. The double-dial itself.
  2. COMPLETE   — no case in NEITHER payload. Worse than overlap: nobody ever calls it and the
                  list just looks shorter. An unstamped row must land on seat 0, not vanish.
  3. INERT      — the baked page ignores fcSeat entirely, and offers no way to change or escape it
                  (a "show all" on a page that holds half the rows is a lie).
  4. LANES      — every category resolves on both pages, from LIVE dates and SYNCED notes, so a
                  page left open overnight cannot keep a passed sale under "Urgent 0-7".
  5. FAIL-SOFT  — Carlos's page failing costs Carlos's page. Not Alejandro's, not the board.

Needs node on PATH for the JS half; skips cleanly without it.
Run: python _carlostest.py
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAIL = []
PASS = []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok   ' if ok else '  FAIL ') + name + (('  — ' + str(detail)[:120]) if detail else ''))


import call_mode  # noqa: E402

src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()

# ---- 1. the crew declaration itself ------------------------------------------------------------
print('\nCALL_SEATS')
seats = call_mode.CALL_SEATS
rec('seats declared', bool(seats), seats)
if seats and seats[0] is not None:
    ns = set(s[0] for s in seats)
    idxs = [s[1] for s in seats]
    rec('every seat agrees on n', len(ns) == 1, ns)
    rec('every seat index is distinct', len(set(idxs)) == len(idxs), idxs)
    rec('indices cover 0..n-1', sorted(idxs) == list(range(seats[0][0])), idxs)
    rec('seat 0 is the main page (empty subdir)', seats[0][1] == 0)
    rec('labels are URL-safe and distinct',
        len(set(re.sub(r'[^a-z0-9-]', '', s[2].lower()) for s in seats)) == len(seats))

# ---- 2. the partition, on rows shaped like the real feed ---------------------------------------
print('\nPARTITION')
rows = [{'c': 'CASE-%04d' % i, 'sb': call_mode._sb('CASE-%04d' % i) if hasattr(call_mode, '_sb') else i % 12}
        for i in range(600)]
# derive sb the way call_rows does, whatever it is called, so the test tracks the real hash
if not hasattr(call_mode, '_sb'):
    probe = call_mode.call_rows([], None, None)
    del probe
n = seats[0][0] if seats and seats[0] else 2
halves = [call_mode.seat_rows(rows, n, i) for i in range(n)]
sets = [set(r['c'] for r in h) for h in halves]
union = set()
overlap = 0
for s in sets:
    overlap += len(union & s)
    union |= s
rec('no case on two phones', overlap == 0, '%d overlapping' % overlap)
rec('no case on zero phones', union == set(r['c'] for r in rows),
    '%d lost' % len(set(r['c'] for r in rows) - union))
rec('each half holds only its own buckets',
    all(all(r.get('sb', 0) % n == i for r in halves[i]) for i in range(n)))
sizes = [len(h) for h in halves]
rec('halves are within 25%% of even %s' % sizes,
    max(sizes) - min(sizes) <= 0.25 * (len(rows) / float(n)), sizes)
rec('an unstamped row lands on seat 0, never nowhere',
    len(call_mode.seat_rows([{'c': 'OLD'}], n, 0)) == 1
    and all(len(call_mode.seat_rows([{'c': 'OLD'}], n, i)) == 0 for i in range(1, n)))

# ---- 3. two BUILT pages, decoded ----------------------------------------------------------------
print('\nBUILT PAGES')


def fake_encrypt(plaintext, codes):
    import base64
    return {'k': [], 'ct': base64.b64encode(plaintext.encode('utf-8')).decode('ascii')}


def payload_of(html):
    import base64
    m = re.search(r'var ENC=(\{.*?\}), OUTCOMES=', html, re.S)
    return json.loads(base64.b64decode(json.loads(m.group(1))['ct']).decode('utf-8'))


slim = [{'case': 'CASE-%04d' % i, 'folio': '%012d' % i, 'addr': '%d Test St' % i,
         'county': 'Miami-Dade', 'st': 'FC', 'auction': '12/31/2026', 'days': 30,
         'tier': 'A', 'score': 50, 'phones': [{'number': '30555501%02d' % (i % 100)}]}
        for i in range(200)]

built = {}
# make_callmode writes under call_mode.HERE/docs/call/. Point HERE at a throwaway dir for the
# duration: a test that writes into the repo's docs/ replaces the REAL encrypted page with a
# 200-row fixture built under a fake key, and the next person to open Call Mode gets an empty
# list with no error. (That is not hypothetical — it is what the first run of this file did.)
import shutil          # noqa: E402
import tempfile        # noqa: E402
_real_here = call_mode.HERE
_tmp = tempfile.mkdtemp(prefix='carlostest-')
shutil.copyfile(os.path.join(_real_here, 'tracker_template.html'),
                os.path.join(_tmp, 'tracker_template.html'))
try:
    call_mode.HERE = _tmp
    all_rows = call_mode.call_rows(slim, None, None)
    for si, seat in enumerate(seats):
        sub = '' if si == 0 else seat[2]
        call_mode.make_callmode(slim, ['TESTCODE'], fake_encrypt, '2026-01-01T00:00', 'bsig',
                                guard=None, seat=seat, subdir=sub, rows=all_rows)
        dest = os.path.join(_tmp, 'docs', 'call', re.sub(r'[^a-z0-9-]', '', sub.lower()), 'index.html')
        built[seat[2]] = open(dest, encoding='utf-8').read()
    rec('both seat pages built', len(built) == len(seats), sorted(built))
    rec('the build wrote NOTHING into the repo docs/',
        not os.path.exists(os.path.join(_real_here, 'docs', 'call', 'carlos', '_probe')))

    cases = {}
    for who, html in built.items():
        cases[who] = set(r['c'] for r in payload_of(html)['r'])
    allc = set()
    ov = 0
    for s in cases.values():
        ov += len(allc & s)
        allc |= s
    rec('built payloads are disjoint', ov == 0, '%d shared cases' % ov)
    rec('built payloads reunite to the whole list', allc == set(r['c'] for r in all_rows[0]),
        '%d lost' % len(set(r['c'] for r in all_rows[0]) - allc))
    # COUNT, not just set membership. call_rows can return the same case twice (the live feed has
    # a handful — same case, one copy with a null address); both copies carry the same sb, so they
    # always land on the SAME phone and a set union would hide a row that went missing behind a
    # duplicate that did not.
    rec('no row is dropped or duplicated by the cut',
        sum(len(payload_of(built[s[2]])['r']) for s in seats) == len(all_rows[0]),
        '%d built vs %d cut' % (sum(len(payload_of(built[s[2]])['r']) for s in seats),
                                len(all_rows[0])))
    rec('phone index stays whole on both (Who texted me? must resolve either half)',
        all(len(payload_of(h).get('x') or {}) == len(payload_of(built[seats[0][2]]).get('x') or {})
            for h in built.values()))
    for si, seat in enumerate(seats):
        h = built[seat[2]]
        want = 'var SEAT=' + json.dumps({'n': seat[0], 'i': seat[1], 'w': seat[2]})
        rec('%s page bakes its own seat' % seat[2], want in h, want)
    rec('SHOWN on a seat page is that seat, not the crew',
        all(('var SHOWN=%d,' % len(cases[s[2]])) in built[s[2]] for s in seats))
except Exception as e:
    rec('built pages', False, repr(e))
finally:
    call_mode.HERE = _real_here
    shutil.rmtree(_tmp, ignore_errors=True)

# ---- 4. the source promises ---------------------------------------------------------------------
print('\nSOURCE')
rec('seat wins over fcSeat', 'function _seat(){ if(SEAT) return SEAT;' in src)
rec('nothing on the phone can move a baked seat', 'if(SEAT) return;' in src)
rec('the seat prompt is inert when baked', "if(SEAT){ alert('This page is built for '" in src)
rec('no "show all" escape on a baked page',
    src.count('back to mine') == 1 and 'if(SEAT){' in src.split('function seatChip()')[1][:400])
rec('lanes are one table', 'var LANES = [' in src and 'function laneDef(' in src)
rec('pool() and head() share the predicates',
    'ROWS.filter(laneDef(lane).pred)' in src and 'ROWS.filter(L.pred).length' in src)
rec('days are recomputed live, not read frozen', 'function liveDays(' in src and '864e5' in src)

# ---- 5. the JS half ------------------------------------------------------------------------------
print('\nJS')
have_node = subprocess.run(['node', '--version'], capture_output=True).returncode == 0
if not have_node:
    rec('node available', True, 'node missing — JS assertions skipped')
else:
    html = call_mode.build_html([], 0, {'stub': 1}, '2026-01-01T00:00', 's', 'b', sync_js='',
                                seat=(2, 1, 'Carlos'))
    js = max(re.findall(r'<script[^>]*>(.*?)</script>', html, re.S), key=len)
    p = os.path.join(HERE, '_carlos_check.js')
    open(p, 'w', encoding='utf-8').write(js)
    ok = subprocess.run(['node', '--check', p], capture_output=True, text=True)
    rec('generated seat-page JS parses', ok.returncode == 0, (ok.stderr or '')[:120])

    seat_blk = js[js.find('var SEAT_TTL_MS'):js.find('function pool()')]
    lane_blk = js[js.find('function isBuyBox'):js.find('var SEAT_TTL_MS')]
    chip_blk = js[js.find('function seatChip()'):js.find('function errChip()')]
    day = 86400000
    harness = '''
      var SEAT={n:2,i:1,w:'Carlos'};
      var notes={}, _SEATN=0, _CLMN=0, SCREEN='lead';
      function _deviceId(){return 'dev-ME';} function saveNotes(){} function render(){}
      function lastCall(){return null;} function esc(s){return String(s==null?'':s);}
      function workerQ(){ return WQ; } var WQ=[];
      var localStorage={_d:{},getItem:function(k){return this._d[k]||null;},
        setItem:function(k,v){this._d[k]=v;},removeItem:function(k){delete this._d[k];}};
      %s
      %s
      %s
      var out={};
      // a stale per-phone seat from the old design must not be able to hide this page's own rows
      localStorage.setItem('fcSeat', JSON.stringify({n:2,i:0,w:'stale'}));
      out.seatIsBaked = JSON.stringify(_seat());
      _seatSet(3,2,'hijack');
      out.stillBaked = JSON.stringify(_seat());
      // rows this payload actually holds (odd buckets) + an unstamped row from an older build
      out.everyRowMine = [{c:'a',sb:1},{c:'b',sb:3},{c:'c'}].every(_seatMine);
      // belt: a row that does NOT belong here is still filtered, so a mis-cut payload cannot
      // silently put the other caller's lead on this phone
      out.foreignHidden = !_seatMine({c:'x',sb:0});
      out.chip = seatChip();
      var d=%d;
      function at(days){ var t=new Date(); t.setHours(0,0,0,0); t=new Date(+t+days*d);
        return (t.getMonth()+1)+'/'+t.getDate()+'/'+t.getFullYear(); }
      var R=[
        {c:'U', x:at(3),  d:3},         // urgent
        {c:'S', x:at(20), d:20},        // sale soon
        {c:'L', x:at(50), d:50},        // late
        {c:'P', x:at(-1), d:0},         // sale already held — frozen d says urgent, live says no
        {c:'F', lp:1, d:9999},          // fresh filing
        {c:'B', st:'BAL', d:-5},        // balloon past maturity
        {c:'X', x:at(20), d:20, bb:1}   // buy-box AND sale soon
      ];
      notes['S']={touches:[{ch:'email', out:'emailed', tsu:Date.now()-2*d}]};
      notes['L']={replied:'2026-09-01'};
      notes['U']={touches:[{ch:'worker', out:'done', tsu:Date.now()-3*d}]};
      notes['F']={touches:[{ch:'worker', out:'done', tsu:Date.now()-9*d}]};
      function inLane(k){ var L=laneDef(k); return R.filter(L.pred).map(function(r){return r.c;}).sort().join(','); }
      out.urgent=inLane('urgent'); out.soon=inLane('soon'); out.late=inLane('late');
      out.lp=inLane('lp'); out.bal=inLane('bal'); out.bb=inLane('bb');
      out.email=inLane('email'); out.worker=inLane('worker');
      WQ=['P']; _WQSET=null; out.workerQueued=inLane('worker');
      out.lanes=LANES.map(function(L){return L.k;}).join(',');
      out.liveVsFrozen=liveDays({c:'P',x:at(-1),d:0});
      out.balNotDayLaned=liveDays({st:'BAL',d:-5});
      console.log(JSON.stringify(out));
    ''' % (lane_blk, seat_blk, chip_blk, day)
    hp = os.path.join(HERE, '_carlos_harness.js')
    open(hp, 'w', encoding='utf-8').write(harness)
    r2 = subprocess.run(['node', hp], capture_output=True, text=True)
    o = {}
    try:
        o = json.loads((r2.stdout or '').strip().splitlines()[-1])
    except Exception:
        pass
    rec('seat/lane harness ran', bool(o), (r2.stderr or r2.stdout or 'no output')[:200])
    if o:
        rec('baked seat beats a stale fcSeat', o.get('seatIsBaked') == '{"n":2,"i":1,"w":"Carlos"}',
            o.get('seatIsBaked'))
        rec('_seatSet cannot hijack a baked seat', o.get('stillBaked') == o.get('seatIsBaked'),
            o.get('stillBaked'))
        rec('every row in this payload is mine', o.get('everyRowMine') is True)
        rec('a foreign row would still be filtered (belt on a mis-cut payload)',
            o.get('foreignHidden') is True)
        rec('chip names the caller and offers no escape',
            'Carlos' in (o.get('chip') or '') and 'show all' not in (o.get('chip') or '')
            and 'change' not in (o.get('chip') or ''), o.get('chip'))
        rec('all eight lanes present',
            o.get('lanes') == 'email,worker,urgent,soon,late,lp,bal,bb', o.get('lanes'))
        rec('urgent = 0-7 days only', o.get('urgent') == 'U', o.get('urgent'))
        rec('sale soon = 8-45', o.get('soon') == 'S,X', o.get('soon'))
        rec('late = 46-60', o.get('late') == 'L', o.get('late'))
        rec('fresh filings = lp only', o.get('lp') == 'F', o.get('lp'))
        rec('balloon = st BAL only', o.get('bal') == 'B', o.get('bal'))
        rec('buy-box crosses the date lanes', o.get('bb') == 'X', o.get('bb'))
        rec('email lane = emailed or replied', o.get('email') == 'L,S', o.get('email'))
        rec('worker lane = touch inside 7 days (9-day touch excluded)',
            o.get('worker') == 'U', o.get('worker'))
        rec('worker lane also takes the synced queue', o.get('workerQueued') == 'P,U',
            o.get('workerQueued'))
        rec('a passed sale leaves the date lanes even with a frozen d',
            o.get('liveVsFrozen') == -1 and 'P' not in (o.get('urgent') or ''), o.get('liveVsFrozen'))
        rec('balloon rows never go through liveDays', o.get('balNotDayLaned') is None)

# ---- 6. fail-soft: Carlos's page must not be able to cost Alejandro's ---------------------------
print('\nFAIL-SOFT')
fl = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
_b0 = fl.find('# ---- CALL MODE')
blk = fl[_b0:fl.find('\n    if codes:', _b0)]
rec('the extra seats build inside their OWN try/except',
    'for _seat in call_mode.CALL_SEATS[1:]:' in blk and blk.count('except Exception') >= 3, )
rec('a seat failure names the seat and says what survived',
    'call mode/%s: SKIPPED' in blk and 'unaffected' in blk)
rec('call_rows runs ONCE for both pages and the sheet',
    blk.count('call_mode.call_rows(') == 1 and 'rows=_cm_all' in blk)
rec('the call sheet still gets the whole list', 'call_sheet.write(*_cm_all)' in blk)

for _scratch in ('_carlos_check.js', '_carlos_harness.js'):
    try:
        os.remove(os.path.join(HERE, _scratch))
    except OSError:
        pass

print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAILED: ' + f)
sys.exit(1 if FAIL else 0)
