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

rows = [row('2099-000001-CA-01', '1 MAIN ST, MIAMI, FL 33101', 'Ann Smith'),
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
rec('no phones or emails on any lead', all(not l.phones and not l.emails for l in leads))
p = os.path.join(os.environ['DEALFLOW_DIR'], 'o.csv')
EX.write_csv(leads, EX.load_map(PS.MAP_FILE), p)
t = open(p, encoding='utf-8').read()
rec('csv has no phone or email data', '3055550100' not in t and 'example.com' not in t)
rec('csv header starts with address columns', t.splitlines()[0].startswith('Address,City,State,Zip'))
try:
    PS.build_all(rows, {}, set(), {}, {}, 'T', county='NOPE', never_contact=lambda c: False); ok = False
except PS.ExportError: ok = True
rec('unknown county refused', ok)
print('%d/%d' % (sum(R), len(R))); sys.exit(0 if all(R) else 1)
