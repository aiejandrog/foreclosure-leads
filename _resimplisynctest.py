"""resimpli_sync.py - REsimpli skip-trace exports into the phone cache, add-only.

Invented data only: no network, no real leads, temp DEALFLOW_DIR / HOME / results file.
Pins the rules that matter for Call Mode:
  * a DNC-flagged REsimpli number lands with dnc=True (make_tracker turns that into phdnc)
  * same house with a different owner is skipped (not the homeowner's phone)
  * rows with no board lead are counted, never added
  * existing numbers and entries are never removed or replaced; a rerun adds nothing
  * every file is read (no newest-by-mtime pick); a duplicate download counts once
  * a non-REsimpli CSV is refused and nothing is written
"""
import csv, json, os, pathlib, sys, tempfile
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TMP = tempfile.mkdtemp()
os.environ['DEALFLOW_DIR'] = os.path.join(TMP, 'DEALFLOW')
os.environ['HOME'] = os.path.join(TMP, 'home')
os.environ['USERPROFILE'] = os.environ['HOME']
os.makedirs(os.path.join(os.environ['HOME'], 'Downloads'))

import resimpli_sync as RS
import skiptrace as S

ok, bad = [], []
def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))

HDR = ['firstName', 'lastName', 'fullName', 'firstName2', 'lastName2', 'fullName2',
       'propertyStreetAddress', 'propertyStreetAddress2', 'propertyZipCode']
for i in range(1, 11):
    HDR += ['Phone_%d' % i, 'Phone_%d_status' % i, 'Phone_%d_type' % i, 'Phone_%d_DNC' % i,
            'Phone_%d_IsLitigator' % i]


def row(last, first, street, z, phones):
    r = dict.fromkeys(HDR, '')
    r.update(firstName=first, lastName=last, fullName=first + ' ' + last,
             propertyStreetAddress=street, propertyZipCode=z)
    for i, (num, typ, dnc) in enumerate(phones, 1):
        r['Phone_%d' % i] = num
        r['Phone_%d_type' % i] = typ
        r['Phone_%d_DNC' % i] = 'Yes' if dnc else 'No'
        r['Phone_%d_status' % i] = '["DNC"]' if dnc else '[]'
    return r


def write_csv(path, rows, header=HDR):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


LEADS = [
    {'Case #': '2026-000001-CA-01', 'Address': '100 SW 10TH COURT, MIAMI, FL, 33100', 'owners': 'TESTER, ANA'},
    {'Case #': '2026-000002-CA-01', 'Address': '200 NW 20 AVE, MIAMI, FL, 33100', 'owners': 'SAMPLE, BO'},
    {'Case #': '2026-000003-CA-01', 'Address': '300 NE 3 ST, MIAMI, FL, 33100', 'owners': 'OTHER, CY'},
    {'Case #': '2026-000004-CA-01', 'Address': '400 SE 4 TER, MIAMI, FL, 33100', 'owners': 'DEEZ, DI'},
]
RES = os.path.join(TMP, 'skiptrace_results.json')
S.RESULTS = RES
S.load_all_leads = lambda: LEADS
json.dump({'2026-000002-CA-01': {'phones': [{'number': '3055550200', 'type': 'Mobile', 'dnc': False}],
                                 'source': 'tracerfy'}}, open(RES, 'w'))

rows = [
    row('Tester', 'Ana', '100 Sw 10th Ct', '33100', [('3055550101', 'Mobile', False),
                                                      ('(305) 555-0102', 'Landline', True)]),
    row('Sample', 'Bo', '200 Nw 20th Ave Unit 4', '33100', [('3055550200', 'Mobile', False),
                                                             ('13055550201', 'Mobile', False)]),
    row('Stranger', 'Zed', '300 Ne 3rd St', '33100', [('3055550300', 'Mobile', False)]),
    row('Deez', 'Di', '400 Se 4th Terrace', '33100', [('3055550400', 'Landline', True)]),
    row('Nobody', 'Al', '999 Nowhere Rd', '33100', [('3055550999', 'Mobile', False)]),
]
DL = os.path.join(os.environ['HOME'], 'Downloads')
write_csv(os.path.join(DL, 'SkipTrace_1.csv'), rows)
write_csv(os.path.join(DL, 'SkipTrace_1 (1).csv'), rows)                 # same download twice
write_csv(os.path.join(DL, 'SkipTrace_2.csv'), rows[:1])                  # older subset file

# --- normalizing
rec('street key folds suffix, ordinal, unit', RS.street_key('200 Nw 20th Ave Unit 4') ==
    RS.street_key('200 NW 20 AVENUE, MIAMI, FL') == '200 NW 20 AVE')
rec('11-digit number loses its leading 1', RS.row_phones(rows[1])[1]['number'] == '3055550201')

# --- dry run writes nothing
before = open(RES).read()
rc = RS.main(['--dry-run'])
rec('dry run exits 0 and leaves the cache alone', rc == 0 and open(RES).read() == before)
rec('dry run writes no status', not os.path.exists(os.path.join(os.environ['DEALFLOW_DIR'], 'resimpli_sync_status.json')))

# --- real run
rc = RS.main([])
d = json.load(open(RES))
st = json.load(open(os.path.join(os.environ['DEALFLOW_DIR'], 'resimpli_sync_status.json')))
rec('exit 0', rc == 0)
rec('duplicate download counted once, subset file still read', len(st['files']) == 2, [f['file'] for f in st['files']])
a = d.get('2026-000001-CA-01') or {}
rec('matched lead gets both numbers', [p['number'] for p in a.get('phones', [])] == ['3055550101', '3055550102'])
rec('REsimpli DNC flag lands as dnc=True', [p['dnc'] for p in a.get('phones', [])] == [False, True])
rec('phone dict has the keys make_tracker reads', all({'number', 'type', 'dnc'} <= set(p) for p in a['phones']))
b = d['2026-000002-CA-01']
rec('existing entry keeps its number and provider, gains only the new one',
    [p['number'] for p in b['phones']] == ['3055550200', '3055550201'] and b['source'] == 'tracerfy')
rec('same house, different owner: skipped', '2026-000003-CA-01' not in d)
rec('all-DNC row does not create an entry (skiptrace can still trace it)', '2026-000004-CA-01' not in d)
rec('unmatched row never becomes a lead', len(d) == 2)
t = st['total']
rec('counts', (t['owner_mismatch'], t['new_numbers'], t['new_numbers_dnc'], t['skipped_all_dnc_new_lead'],
               t['leads_new_phone']) == (1, 3, 1, 1, 1), t)
rec('status carries counts only, no names or numbers',
    not any(s in json.dumps(st) for s in ('Tester', 'TESTER', '305555')))
rec('backup written outside the repo',
    len(os.listdir(os.path.join(os.environ['DEALFLOW_DIR'], 'backups'))) == 1)
rec('exports copied into DEALFLOW imports',
    len(os.listdir(os.path.join(os.environ['DEALFLOW_DIR'], 'imports', 'resimpli'))) == 2)

# --- rerun is a no-op
snap = open(RES).read()
RS.main([])
rec('rerun adds nothing', open(RES).read() == snap)

# --- a foreign CSV is refused
bogus = os.path.join(TMP, 'other.csv')
write_csv(bogus, [{'a': '1'}], header=['a'])
rc = RS.main([bogus])
rec('non-REsimpli CSV refused with exit 2, cache untouched', rc == 2 and open(RES).read() == snap)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
