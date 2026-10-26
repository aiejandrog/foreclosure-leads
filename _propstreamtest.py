"""PropStream export: suppression gates, no contact data, header map. Invented data only."""
import csv, json, os, sys, tempfile
os.environ['DEALFLOW_DIR'] = tempfile.mkdtemp(prefix='propstream_')
import export_to_propstream as PS   # noqa: E402
import export_to_resimpli as EX     # noqa: E402

R = []
def rec(n, ok, d=''):
    R.append(bool(ok)); print(('  PASS ' if ok else '  FAIL ') + n + (' | ' + str(d) if d and not ok else ''))

def row(case, addr, owner, **kw):
    r = {'case': case, 'addr': addr, 'owners': owner, 'oname': owner, 'county': 'MIAMI-DADE',
         'pkey': owner.lower(), 'phones': ['3055550100'], 'emails': ['x@example.com'], 'days': 20}
    r.update(kw); return r

rows = [row('2099-000001-CA-01', '1 MAIN ST, MIAMI, FL 33101', 'Ann Smith', folio='01-2345-678-9000'),
        row('2099-000002-CA-01', '2 MAIN ST, MIAMI, FL 33101', 'Bob Jones'),
        row('2099-000003-CA-01', '3 MAIN ST, MIAMI, FL 33101', 'Cy Park'),
        row('2099-000004-CA-01', '4 MAIN ST, MIAMI, FL 33101', 'Di Lee'),
        row('2099-000005-CA-01', '5 MAIN ST, MIAMI, FL 33101', 'Ed Ray'),
        row('2099-000006-CA-01', '', 'Fay Wu'),
        row('2099-000007-CA-01', '6 MAIN ST, MIAMI, FL 33101', 'Gus Ho', vac=1),
        row('2099-000008-CA-01', '1 Main Street, Miami, FL 33101', 'Ann Smith'),   # dupe of row 1
        row('2099-000009-CA-01', '9 MAIN ST, MIAMI, FL 33101', 'Hal Fox', phones=[], emails=[])]
optouts = {'2099-000002-CA-01': {'optout': 1}}
deads = {'2099-000003-CA-01': {'status': 'Dead'}}
notes = {'2099-000004-CA-01': {'no': 'hard'}}
leads, s = PS.build_all(rows, optouts, {'2099-000002-CA-01'}, deads, notes, 'T',
                        never_contact=lambda c: c == '2099-000005-CA-01')
addrs = sorted(l.property_address for l in leads)
rec('only clean, addressed, non-dupe leads written', addrs == ['1 MAIN ST', '9 MAIN ST'], addrs)
rec('no-phone lead is included', '9 MAIN ST' in addrs)
rec('every hold counted', s['held'] == {'opted_out': 1, 'dead_ledger': 1, 'hard_no': 1, 'never_contact': 1,
    'no_address': 1, 'vacant_land': 1}, s['held'])
rec('dupe dropped', s['dupes_dropped'] == 1)
rec('apn carried', leads[0].folio == '0123456789000', leads[0].folio)
rec('no phones or emails on any lead', all(not l.phones and not l.emails for l in leads))
p = os.path.join(os.environ['DEALFLOW_DIR'], 'o.csv')
PS.write_csv(leads, PS.load_map(PS.MAP_FILE), p)
t = open(p, encoding='utf-8').read()
rec('csv has no phone or email data', '3055550100' not in t and 'example.com' not in t)
rec('header is PropStream template verbatim', t.splitlines()[0] == 'Address,Unit#,City,State,Zip,County,FIPS,APN#', t.splitlines()[0])
rec('unit split off street', PS.split_unit('842 NW 9TH ST APT 4') == ('842 NW 9TH ST', '4') and PS.split_unit('1 MAIN ST') == ('1 MAIN ST', ''))
rec('bare unit after a suffix is split', PS.split_unit('777 SW 9 AVE 101') == ('777 SW 9 AVE', '101') and PS.split_unit('7825 NW 107 AVE 510') == ('7825 NW 107 AVE', '510'))
cases = {'3301 NE 1 AVE PH-7': ('3301 NE 1 AVE', 'PH-7'), '8848 NW 5 AVE UNITS 1': ('8848 NW 5 AVE', '1'),
         '2881 NE 33 CT 4C': ('2881 NE 33 CT', '4C'), '5110 NESTING WAY C': ('5110 NESTING WAY', 'C'),
         '13380 POLO RD W 103': ('13380 POLO RD W', '103'), '2751 N PALM AIRE DR 102': ('2751 N PALM AIRE DR', '102'),
         '14701 CUMBERLAND DR 208A': ('14701 CUMBERLAND DR', '208A'), '187 SAXONY D': ('187 SAXONY D', ''),
         '100 PARK AVE N': ('100 PARK AVE N', ''), '5361 GODFREY RD': ('5361 GODFREY RD', ''),
         '16539 BOTANIKO DR N': ('16539 BOTANIKO DR N', ''), '8410 STANIEL CAY': ('8410 STANIEL CAY', '')}
bad = {k: PS.split_unit(k) for k, v in cases.items() if PS.split_unit(k) != v}
rec('real board unit shapes split, look-alikes left alone', not bad, bad)
rec('route numbers and plain streets are not split', PS.split_unit('100 STATE RD 826') == ('100 STATE RD 826', '') and PS.split_unit('2435 NW 168 ST') == ('2435 NW 168 ST', '') and PS.split_unit('100 PARK AVE N') == ('100 PARK AVE N', ''))
rec('fips and apn written', '12086' in t and 'Miami-Dade' in t)
held_cases = {'2099-000001-CA-01': (True, 'bankruptcy_hold'), '2099-000009-CA-01': (False, '')}
l2, s2 = PS.build_all(rows, optouts, {'2099-000002-CA-01'}, deads, notes, 'T',
                      never_contact=lambda c: c == '2099-000005-CA-01',
                      send_hold=lambda c: held_cases.get(c, (True, 'gate_unevaluable:X')))
rec('send scope drops held and unevaluable leads', [l.property_address for l in l2] == ['9 MAIN ST'] and s2['held'].get('bankruptcy_hold') == 1, s2['held'])
# the command-line path itself: --scope send must hand the per-lead stay check to the builder
_saved = (EX.preflight, EX.read_twin, EX.load_suppression, EX.load_deads, EX.load_notes, PS.default_send_hold)
_seen = []
def _hold(c):
    _seen.append(c); return (True, 'bankruptcy_hold') if c == '2099-000001-CA-01' else (False, '')
EX.preflight = lambda: []; EX.read_twin = lambda p=None: rows
EX.load_suppression = lambda: ({}, set(), set()); EX.load_deads = lambda: {}; EX.load_notes = lambda: {}
PS.default_send_hold = _hold
import contextlib, io
def _cli(scope):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = PS.main(['--scope', scope, '--dry-run', '--list-name', 'T'])
    return rc, out.getvalue()
rc, o = _cli('send')
rec('CLI --scope send runs the stay check on every lead and drops the held one', rc == 0 and '2099-000001-CA-01' in _seen and len(_seen) >= 3 and '"bankruptcy_hold": 1' in o, (rc, _seen, o[-300:]))
_seen.clear(); rc, o = _cli('all')
rec('CLI --scope all does not run it', rc == 0 and not _seen)
(EX.preflight, EX.read_twin, EX.load_suppression, EX.load_deads, EX.load_notes, PS.default_send_hold) = _saved
rec('default send hold fails closed', PS.default_send_hold('not-a-case')[0] is True)
try:
    PS.build_all(rows, {}, set(), {}, {}, 'T', county='NOPE', never_contact=lambda c: False); ok = False
except PS.ExportError: ok = True
rec('unknown county refused', ok)
import propstream_copy as PC    # noqa: E402
sheet = PC.build()
rec('copy sheet: every text carries the EN and ES let-me-know lines', sheet.count("if now's not a good time, just let me know") == 4 and sheet.count('si ahora no es buen momento, solo d') >= 4)
rec('copy sheet: letter carries MARS and the EN+ES opt-out', 'not associated with the government' in sheet and 'lo vuelvo a contactar' in sheet)
print('%d/%d' % (sum(R), len(R))); sys.exit(0 if all(R) else 1)
