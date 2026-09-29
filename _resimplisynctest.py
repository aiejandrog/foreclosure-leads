"""resimpli_sync.py - REsimpli skip-trace exports into the phone cache, add-only.

Invented data only: no network, no real leads, temp DEALFLOW_DIR / HOME / results file.
Pins the rules that matter for Call Mode:
  * a DNC-flagged REsimpli number lands with dnc=True (make_tracker turns that into phdnc)
  * the same flag on a number the cache holds as clean tightens it; a clean flag never loosens one
  * same house with a different owner is skipped (not the homeowner's phone)
  * a different unit in the same building is skipped, whether or not the surname matches
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


def row(last, first, street, z, phones, unit=''):
    r = dict.fromkeys(HDR, '')
    r.update(firstName=first, lastName=last, fullName=first + ' ' + last,
             propertyStreetAddress=street, propertyStreetAddress2=unit, propertyZipCode=z)
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


def lead(n, addr, owners):
    return {'Case #': '2026-0000%02d-CA-01' % n, 'Address': addr + ', MIAMI, FL, 33100', 'owners': owners}


def case(n):
    return '2026-0000%02d-CA-01' % n


LEADS = [
    lead(1, '100 SW 10TH COURT', 'TESTER, ANA'),
    lead(2, '200 NW 20 AVE', 'SAMPLE, BO'),
    lead(3, '300 NE 3 ST', 'OTHER, CY'),
    lead(4, '400 SE 4 TER', 'DEEZ, DI'),
    lead(5, '500 SW 5 ST APT 4', 'UNITY, UMA'),
    lead(6, '600 NW 6 AVE #12', 'HASHTAG, HAL'),
    lead(7, '700 NE 7 ST', 'PLAIN, PAT'),
    lead(8, '800 SE 8 CT', 'FLAG, FAY'),
    lead(9, '900 SW 9 ST #12', 'NINE, NAN'),
    lead(10, '1000 NW 10 ST', 'KEEP, KAY'),
]
RES = os.path.join(TMP, 'skiptrace_results.json')
S.RESULTS = RES
S.load_all_leads = lambda: LEADS
json.dump({
    case(2): {'phones': [{'number': '3055550200', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'},
    # skiptrace.py keeps an 11-digit number as the provider returned it
    case(8): {'phones': [{'number': '13055550801', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'},
    case(10): {'phones': [{'number': '3055551001', 'type': 'Mobile', 'dnc': True}], 'source': 'tracerfy'},
}, open(RES, 'w'))

rows = [
    row('Tester', 'Ana', '100 Sw 10th Ct', '33100', [('3055550101', 'Mobile', False),
                                                      ('(305) 555-0102', 'Landline', True)]),
    row('Sample', 'Bo', '200 Nw 20th Ave', '33100', [('3055550200', 'Mobile', False),
                                                     ('13055550201', 'Mobile', False)]),
    row('Stranger', 'Zed', '300 Ne 3rd St', '33100', [('3055550300', 'Mobile', False)]),
    row('Deez', 'Di', '400 Se 4th Terrace', '33100', [('3055550400', 'Landline', True)]),
    row('Nobody', 'Al', '999 Nowhere Rd', '33100', [('3055550999', 'Mobile', False)]),
    # units: same unit matches (unit column), a different unit in the same building does not,
    # a unit on one side only does not, in either direction
    row('Unity', 'Uma', '500 Sw 5th St', '33100', [('3055550501', 'Mobile', False)], unit='Unit 4'),
    row('Unity', 'Uma', '500 Sw 5th St', '33100', [('3055550502', 'Mobile', False)], unit='Unit 9'),
    row('Zzzz', 'Zed', '500 Sw 5th St', '33100', [('3055550503', 'Mobile', False)], unit='Unit 7'),
    row('Hashtag', 'Hal', '600 Nw 6th Ave', '33100', [('3055550601', 'Mobile', False)]),
    row('Plain', 'Pat', '700 Ne 7th St Apt 2', '33100', [('3055550701', 'Mobile', False)]),
    # a DNC flag on a number the cache holds as clean, plus one new clean number
    row('Flag', 'Fay', '800 Se 8th Ct', '33100', [('3055550801', 'Mobile', True),
                                                   ('3055550802', 'Mobile', False)]),
    # '#12' on the board side, '# 12' in the unit column: same unit
    row('Nine', 'Nan', '900 Sw 9th St', '33100', [('3055550901', 'Mobile', False)], unit='# 12'),
    # REsimpli says clean for a number the cache already holds as DNC: the flag stays
    row('Keep', 'Kay', '1000 Nw 10th St', '33100', [('3055551001', 'Mobile', False)]),
]
DL = os.path.join(os.environ['HOME'], 'Downloads')
write_csv(os.path.join(DL, 'SkipTrace_1.csv'), rows)
write_csv(os.path.join(DL, 'SkipTrace_1 (1).csv'), rows)                 # same download twice
write_csv(os.path.join(DL, 'SkipTrace_2.csv'), rows[:1])                  # older subset file
write_csv(os.path.join(DL, 'SkipTrace_junk.csv'), [{'a': '1'}], header=['a'])  # not an export
with open(os.path.join(DL, 'SkipTrace_bin.csv'), 'wb') as fh:                   # not even text
    fh.write(b'\xff\xfe\x00\x00\x80garbage')

# --- normalizing
rec('address split folds suffix, ordinal, and reads the unit',
    RS.split_address('200 Nw 20th Ave Unit 4') == ('200 NW 20 AVE', '4') and
    RS.split_address('200 NW 20 AVENUE, MIAMI, FL') == ('200 NW 20 AVE', ''))
rec('#12, # 12, Apt 12, Unit 12 and Apt No 12 are one unit',
    {RS.split_address('1 A ST ' + u)[1] for u in ('#12', '# 12', 'Apt 12', 'UNIT 12', 'Apt No 12')} == {'12'})
rec('unit column wins, inline unit is the fallback',
    RS.row_address({'propertyStreetAddress': '500 Sw 5th St', 'propertyStreetAddress2': 'Unit 4'}) == ('500 SW 5 ST', '4') and
    RS.row_address({'propertyStreetAddress': '500 Sw 5th St Apt 7', 'propertyStreetAddress2': ''}) == ('500 SW 5 ST', '7'))
rec('11-digit number loses its leading 1', RS.norm_number('13055550101') == '3055550101' == RS.norm_number('(305) 555-0101'))
rec('11-digit CSV number is stored as 10 digits', RS.row_phones(rows[1])[1]['number'] == '3055550201')

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
rec('stray non-export files are skipped by name and do not block the real exports',
    sorted(st['skipped_files']) == ['SkipTrace_bin.csv', 'SkipTrace_junk.csv'], st['skipped_files'])
a = d.get(case(1)) or {}
rec('matched lead gets both numbers', [p['number'] for p in a.get('phones', [])] == ['3055550101', '3055550102'])
rec('REsimpli DNC flag lands as dnc=True', [p['dnc'] for p in a.get('phones', [])] == [False, True])
rec('phone dict has the keys make_tracker reads', all({'number', 'type', 'dnc'} <= set(p) for p in a['phones']))
b = d[case(2)]
rec('existing entry keeps its number and provider, gains only the new one',
    [p['number'] for p in b['phones']] == ['3055550200', '3055550201'] and b['source'] == 'tracerfy')
rec('same house, different owner: skipped', case(3) not in d)
rec('all-DNC row does not create an entry (skiptrace can still trace it)', case(4) not in d)
rec('same unit (unit column vs inline APT): matched, that unit only',
    [p['number'] for p in (d.get(case(5)) or {}).get('phones', [])] == ['3055550501'])
rec('different unit in the same building: skipped',
    '3055550502' not in json.dumps(d) and '3055550503' not in json.dumps(d))
rec('unit on the board side only: skipped', case(6) not in d)
rec('unit on the REsimpli side only: skipped', case(7) not in d)
rec('#12 against "# 12": matched', [p['number'] for p in (d.get(case(9)) or {}).get('phones', [])] == ['3055550901'])
f8 = d[case(8)]['phones']
rec('DNC flag tightens a number held as clean, spelled differently, with no duplicate',
    [(p['number'], p['dnc']) for p in f8] == [('13055550801', True), ('3055550802', False)], f8)
rec('a clean REsimpli flag never clears an existing DNC flag',
    d[case(10)]['phones'] == [{'number': '3055551001', 'type': 'Mobile', 'dnc': True}])
rec('unmatched row never becomes a lead', len(d) == 6, sorted(d))
t = st['total']
rec('counts', (t['rows'], t['rows_with_phone'], t['addr_match'], t['unit_mismatch'],
               t['unit_conflict_same_surname'], t['owner_mismatch'],
               t['matched_rows'], t['leads_matched'], t['leads_had_phone'], t['leads_new_phone'],
               t['new_numbers'], t['new_numbers_dnc'], t['new_mobile_clean'], t['dnc_tightened'],
               t['skipped_all_dnc_new_lead'], t['unmatched_rows_with_phone']) ==
    (14, 14, 13, 4, 3, 1, 8, 8, 4, 3, 6, 1, 5, 1, 1, 1), t)
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
