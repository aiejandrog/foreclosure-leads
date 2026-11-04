"""Call Mode "Has equity" lane (2026-11-04, Alex: "create a section on the call mode where its for
leads that have equity").

The lane predicate is the STRICT reading: the baked equity card says a figure may be spoken
(eqc.q, equity_state.card() may_quote) and that net figure is positive (eqc.n > 0). Everything
looser (eqc.f traced but no judgment, an unpriced ceiling, the scraped `e` %) stays out. Order is
soonest sale first inside the reply / worker / contact tiers. Exclusions are not re-implemented
here: pool() runs supReason(), the seat split and the claim filter on every lane, and this suite
pins that the lane goes through the same LANES table those run over.

JS is extracted by name from call_mode.py and run under node, like _queueviewtest.py.
"""
import io, json, os, re, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:200]) if extra else ''))
    if not ok:
        fails.append(name)


def grab_fn(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}', i)
    k = SRC.find('\n', j + 2)
    rec('function present: ' + name, i >= 0 and j > 0)
    return SRC[i:k + 1] if i >= 0 and j > 0 else ''


def node(js):
    fd, path = tempfile.mkstemp(suffix='.js')
    os.close(fd)
    io.open(path, 'w', encoding='utf-8').write(js)
    try:
        p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.remove(path)


def mdy(days):
    import datetime
    d = datetime.date.today() + datetime.timedelta(days=days)
    return '%d/%d/%d' % (d.month, d.day, d.year)


ROWS = [
    {'c': 'Q_FAR',   'x': mdy(40), 'eqc': {'q': 1, 'f': 1, 'n': 90000}},
    {'c': 'Q_SOON',  'x': mdy(5),  'eqc': {'q': 1, 'f': 1, 'n': 15000}},
    {'c': 'Q_LP',    'lp': 1,      'eqc': {'q': 1, 'f': 1, 'n': 50000}},
    {'c': 'Q_MID',   'x': mdy(12), 'eqc': {'q': 1, 'f': 1, 'n': 40000}},
    {'c': 'UNDER',   'x': mdy(3),  'eqc': {'q': 1, 'f': 1, 'n': -20000}},
    {'c': 'ZERO',    'x': mdy(3),  'eqc': {'q': 1, 'f': 1, 'n': 0}},
    {'c': 'FACTONLY', 'x': mdy(3), 'eqc': {'q': 0, 'f': 1}},
    {'c': 'GUESS',   'x': mdy(3),  'e': 80, 'eqv': 0},
    {'c': 'OLDBAKE', 'x': mdy(3),  'e': 60, 'eqv': 1},
    {'c': 'BAL',     'st': 'BAL', 'd': 20, 'eqc': {'q': 1, 'f': 1, 'n': 200000}},
    {'c': 'NOTNUM',  'x': mdy(3),  'eqc': {'q': 1, 'f': 1, 'n': '50000'}},
]

JS = ('function _heldNow(){ return false; }\n'
      + ''.join(grab_fn(n) for n in ('isBalloon', 'liveDays', 'isEquity', '_eqSaleKey', '_freshFirst'))
      + r"""
var TIER = {Q_MID: 1};                       /* Q_MID already reached on another channel */
function _contactTier(r){ return TIER[r.c] || 0; }
function _wkTier(r){ return 2; }
function _filedMs(r){ return 0; }
var ROWS = """ + json.dumps(ROWS) + r""";
var inLane = ROWS.filter(isEquity).map(function(r){ return r.c; });
var order = _freshFirst(ROWS.filter(isEquity), 'eq').map(function(r){ return r.c; });
var orderSoon = _freshFirst(ROWS.filter(isEquity), 'soon').map(function(r){ return r.c; });
console.log(JSON.stringify({inLane: inLane, order: order, orderSoon: orderSoon}));
""")

rc, out, err = node(JS)
rec('node ran', rc == 0, err[-400:])
res = json.loads(out.strip().splitlines()[-1]) if rc == 0 and out.strip() else {}
lane = set(res.get('inLane') or [])
rec('quotable positive-equity rows are in the lane', {'Q_FAR', 'Q_SOON', 'Q_LP', 'Q_MID'} <= lane, sorted(lane))
for c, why in (('UNDER', 'underwater'), ('ZERO', 'zero net'), ('FACTONLY', 'traced chain, no judgment'),
               ('GUESS', 'scraped % only'), ('OLDBAKE', 'old bake with no card'), ('BAL', 'balloon investor row'),
               ('NOTNUM', 'non-numeric net')):
    rec('excluded: ' + why, c not in lane, sorted(lane))
rec('equity lane: soonest sale first, no-date last, contact tier still wins',
    res.get('order') == ['Q_SOON', 'Q_FAR', 'Q_LP', 'Q_MID'], res.get('order'))
rec('other lanes keep their own order (no sale-date key)',
    res.get('orderSoon') == ['Q_FAR', 'Q_SOON', 'Q_LP', 'Q_MID'], res.get('orderSoon'))

# ---- wiring: the lane goes through the one LANES table that pool() / supReason() / seat split run over
m = re.search(r'var LANES = \[(.*?)\n\];', SRC, re.S)
body = m.group(1) if m else ''
keys = re.findall(r"\{k:'(\w+)'", body)
rec('LANES has the eq lane wired to isEquity', "{k:'eq',     lbl:'Has equity',        pred:isEquity" in body, keys)
rec("eq sits after every date lane and before 'all' (_openCase takes the first lane)",
    'eq' in keys and keys[-1] == 'all' and keys.index('eq') == len(keys) - 2, keys)
rec('head() draws Has equity right after All lanes',
    "[laneDef('all'), laneDef('eq')].concat(LANES.filter(function(L){ return L.k !== 'all' && L.k !== 'eq'; }))" in SRC)
pool_src = SRC[SRC.find('function pool('):SRC.find('function _filedMs(')]
rec('pool() filters every lane through supReason(r, lane)', 'supReason(r, lane)' in pool_src
    and 'ROWS.filter(laneDef(lane).pred)' in pool_src)
rec('pool() applies the seat split and claim filter after the lane', '_seatMine(r)' in pool_src and '_clmOwner(r.c)' in pool_src)

print('\n%s: %d failure(s)' % ('FAIL' if fails else 'PASS', len(fails)))
raise SystemExit(1 if fails else 0)
