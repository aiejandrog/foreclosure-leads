"""REsimpli bridge: address normalization, dedupe, CSV header mapping, and the export gates.

Invented people and addresses only. Runs in CI: no board, no ledger, no network. DEALFLOW_DIR
points at a temp folder so nothing lands in a real profile.
"""
import csv
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix='resimpli_')
os.environ['DEALFLOW_DIR'] = TMP

import resimpli_lead as RL          # noqa: E402
import export_to_resimpli as EX     # noqa: E402

R = []


def rec(n, ok, d=''):
    R.append(bool(ok))
    print(('  PASS ' if ok else '  FAIL ') + n + (' | ' + str(d) if d and not ok else ''))


# ---------------------------------------------------------------- normalization
print('address normalization')
same = ['842 Northwest 9th Street, Apt. 4, Miami, FL 33136-1234',
        '842 N.W. 9TH ST # 4, MIAMI, FL- 33136',
        '842 nw 9th st apt #4, Miami, FL 33136',
        '842  NW  9TH  STREET  APARTMENT 4 , MIAMI , FL , 33136']
parsed = [RL.parse_address(a) for a in same]
keys = {RL.dedupe_key(p[0], p[3]) for p in parsed}
rec('four spellings of one unit share one dedupe key', len(keys) == 1, keys)
rec('unit designator keeps its number', parsed[0][0].endswith('APT 4') or parsed[0][0].endswith('UNIT 4'), parsed[0])
rec('# / APT / APARTMENT key the same unit', len({RL.dedupe_key(x[0], x[3]) for x in parsed[1:]}) == 1, parsed[1:])
rec('ZIP+4 cut to 5 digits', parsed[0][3] == '33136', parsed[0])
rec('city and state parsed', parsed[0][1:3] == ('MIAMI', 'FL'), parsed[0])
rec('uppercase + USPS suffix', RL.parse_address('100 Park Avenue North, Miami Beach, FL 33139')[0] == '100 PARK AVE N')
rec('only the last word is a suffix', RL.parse_address('12 Court Street, Miami, FL 33101')[0] == '12 COURT ST')
rec('bad ZIP dropped, not guessed', RL.parse_address('1 MAIN ST, MIAMI, FL 3313')[3] == '')
rec('street-only address still keys (street + city)', RL.dedupe_key('3120 CORAL WAY', '', '') == '3120 CORAL WAY|~')
rec('different unit, different key',
    RL.dedupe_key('842 NW 9TH ST APT 4', '33136') != RL.dedupe_key('842 NW 9TH ST APT 5', '33136'))
rec('same street, different ZIP, different key',
    RL.dedupe_key('1 MAIN ST', '33101') != RL.dedupe_key('1 MAIN ST', '33102'))

print('owner names')
rec('LAST, FIRST roll order', RL.split_owner('JOHNSON, ROBERT A') == ('Robert', 'Johnson'))
rec('owner_clean preferred', RL.split_owner('JOHNSON, ROBERT', 'Robert Johnson') == ('Robert', 'Johnson'))
rec('joint owners keep the first person', RL.split_owner('ROBERT A JOHNSON JR & MARY JOHNSON') == ('Robert', 'Johnson'))
rec('company goes whole into last', RL.split_owner('OCEAN BREEZE 777 LLC') == ('', 'OCEAN BREEZE 777 LLC'))

# ---------------------------------------------------------------- dedupe
print('dedupe')
a = RL.Lead(property_address='1 MAIN ST', zip='33101', phones=['3055550001'], emails=['a@example.com'])
b = RL.Lead(property_address='1 Main Street', zip='33101', phones=['3055550002', '3055550001'])
c = RL.Lead(property_address='2 MAIN ST', zip='33101', phones=['3055550003'])
kept, dropped = RL.dedupe([a, b, c])
rec('one dupe dropped', dropped == 1 and len(kept) == 2, (dropped, len(kept)))
rec('first row wins and merges phones without repeats', kept[0].phones == ['3055550001', '3055550002'], kept[0].phones)
rec('emails kept on the survivor', kept[0].emails == ['a@example.com'])

# ---------------------------------------------------------------- header mapping
print('CSV header mapping')
cols = EX.load_map()
lead = RL.Lead(owner_first='Ana', owner_last='Perez', property_address='1 MAIN ST', city='MIAMI', state='FL',
               zip='33101', phones=['3055550001', '3055550002'], tags=['DealFlow', 'Miami-Dade'],
               est_equity=42.0, equity_verified=False, est_owed=None)
row = dict(zip([c['header'] for c in cols], EX.render_row(lead, cols)))
rec('every map column renders', len(row) == len(cols))
rec('list index -> one phone per column', row.get('Phone 1') == '3055550001' and row.get('Phone 3') == '', row)
rec('list join -> tags', row.get('Tags') == 'DealFlow, Miami-Dade', row.get('Tags'))
rec('None renders blank, never 0', row.get('Est Owed') == '')
rec('bool renders Yes/No', row.get('Equity Verified') == 'No')
rec('whole float renders as integer', row.get('Est Equity % (unverified unless Equity Verified = Yes)') == '42')

custom = os.path.join(TMP, 'map.json')
json.dump({'columns': [{'header': 'Owner Last', 'field': 'owner_last'},
                       {'header': 'Src', 'value': 'DF'},
                       {'header': 'Mobile', 'field': 'phones', 'index': 1}]}, open(custom, 'w'))
cc = EX.load_map(custom)
p = os.path.join(TMP, 'custom.csv')
EX.write_csv([lead], cc, p)
got = list(csv.reader(open(p, encoding='utf-8')))
rec('headers come from the map file, no code edit', got[0] == ['Owner Last', 'Src', 'Mobile'], got[0])
rec('constant + index columns', got[1] == ['Perez', 'DF', '3055550002'], got[1])
for bad, why in (({'columns': [{'header': 'X', 'field': 'no_such_field'}]}, 'unknown field'),
                 ({'columns': [{'header': 'X', 'field': 'zip'}, {'header': 'X', 'field': 'city'}]}, 'duplicate header'),
                 ({'columns': []}, 'empty map')):
    json.dump(bad, open(custom, 'w'))
    try:
        EX.load_map(custom)
        rec('map with %s is refused' % why, False)
    except EX.ExportError:
        rec('map with %s is refused' % why, True)

# ---------------------------------------------------------------- gates
print('export gates')


def mk(case, addr, phones, dnc=None, **kw):
    r = {'case': case, 'addr': addr, 'mail': addr, 'owners': 'PEREZ, ANA', 'oname': 'Ana Perez',
         'county': 'MIAMI-DADE', 'st': 'FC', 'days': 20, 'auction': '10/20/2026',
         'phones': phones, 'phdnc': dnc if dnc is not None else [False] * len(phones),
         'phsrc': ['tr'] * len(phones), 'emails': [], 'value': 400000, 'judg': 150000, 'eq': 62,
         'eqstate': 'unknown', 'plaintiff': 'Example Bank, N.A.'}
    r.update(kw)
    return r


rows = [
    mk('2099-000101-CA-01', '10 SW 1ST ST, MIAMI, FL 33130', ['3055550101', '3055550102'], [False, True],
       emails=['ok@example.com', 'dead@example.com'], eqstate='priced', arv=450000),
    mk('2099-000102-CA-01', '20 SW 2ND ST, MIAMI, FL 33130', ['3055550201']),                  # case opt-out
    mk('2099-000103-CA-01', '30 SW 3RD ST, MIAMI, FL 33130', ['3055550301'], emails=['stop@example.com']),  # email opt-out
    mk('2099-000104-CA-01', '40 SW 4TH ST, MIAMI, FL 33130', ['3055550401'], [True]),         # all DNC
    mk('2099-000105-CA-01', '50 SW 5TH ST, MIAMI, FL 33130', ['3055550501'], saleBkAct=True),  # active stay flag
    mk('2099-000106-CA-01', '60 SW 6TH ST, MIAMI, FL 33130', ['3055550601']),                  # stay gate refuses
    mk('2099-000107-CA-01', '70 SW 7TH ST, MIAMI, FL 33130', ['3055550701']),                  # dead ledger
    mk('2099-000108-CA-01', '', ['3055550801']),                                               # no address
    mk('2099-000109-CA-01', '10 Southwest 1st Street, Miami, FL 33130-0001', ['3055550901']),  # dupe of 101
    mk('2099-000110-CA-01', '100 SW 10TH ST, MIAMI, FL 33130', ['3055551001'], phsrc=['ag']),  # agent number only
    mk('2099-000111-CA-01', '110 SW 11TH ST, MIAMI, FL 33130', ['3055551101'], title_status='transferred'),
    mk('2099-000112-CA-01', '120 SW 12TH ST, MIAMI, FL 33130', ['3055551201', '3055551202'], [False, True]),  # phone opt-out on its DNC number
]
optouts, opt_cases, opt_emails = EX.load_suppression(
    optouts_path=(lambda p: (json.dump({'notes': {'2099-000102-CA-01': {'optout': 'stop'},
                                                  '@stop@example.com': {'optout': 'stop'},
                                                  '#3055551202': {'optout': 'stop'}}}, open(p, 'w')), p)[1])(
        os.path.join(TMP, 'optouts.json')),
    notes_keys=set())
deads = {'2099-000107-CA-01': {'status': 'Dead'}}


def stay(case):
    return {'ok': case != '2099-000106-CA-01', 'code': 'stay_active' if case == '2099-000106-CA-01' else 'clear'}


leads, s = EX.build(rows, optouts, opt_cases, opt_emails, deads, {'dead@example.com'}, 'Test List', stay_check=stay)
cases = [ld.case_number for ld in leads]
print('  summary:', json.dumps(s))
rec('only the clean lead is written', cases == ['2099-000101-CA-01'], cases)
rec('case opt-out held', '2099-000102-CA-01' not in cases and s['held_optout'] >= 1)
rec('identity (email) opt-out held', '2099-000103-CA-01' not in cases)
rec('identity (phone) opt-out held, even on a DNC-flagged number', '2099-000112-CA-01' not in cases)
rec('all-DNC lead held as no dialable phone', '2099-000104-CA-01' not in cases and s['held_no_dialable_phone'] >= 1)
rec('board stay flag held', '2099-000105-CA-01' not in cases)
rec('stay_gate refusal held and counted by code', s['held_stay_gate'] == 1 and s['stay_codes'].get('stay_active') == 1, s)
rec('dead ledger held', '2099-000107-CA-01' not in cases)
rec('missing address dropped and counted', s['dropped_missing_address'] == 1, s)
rec('dupe dropped and counted', s['dupes_dropped'] == 1, s)
rec('not-the-owner-only lead held', '2099-000110-CA-01' not in cases)
rec('title-transferred lead held', '2099-000111-CA-01' not in cases)
rec('every input row is accounted for',
    s['rows_in'] == s['held_optout'] + s['held_call_mode'] + s['held_no_dialable_phone'] + s['held_stay_gate']
    + s['dropped_missing_address'] + s['dupes_dropped'] + s['rows_written'], s)
w = leads[0]
rec('DNC number never written', '3055550102' not in w.phones, w.phones)
rec('dupe phone merged into survivor', '3055550901' in w.phones, w.phones)
rec('bounced email never written', 'dead@example.com' not in w.emails and 'ok@example.com' in w.emails, w.emails)
rec('underwriting passed through, not computed', w.est_arv == 450000 and w.est_owed == 150000 and w.est_equity == 62)
rec('equity_verified only from equity_state', w.equity_verified is True)
rec('verdict left blank (board-only)', w.verdict == '')

p = os.path.join(TMP, 'gated.csv')
EX.write_csv(leads, EX.load_map(), p)
blob = open(p, encoding='utf-8').read()
leak = [x for x in ('3055550102', '3055550201', '3055550301', 'stop@example.com', '3055550401',
                    '3055550501', '3055550601', '3055550701', 'dead@example.com', '3055551201') if x in blob]
rec('no held phone or email appears anywhere in the CSV', not leak, leak)

# real stay_gate: the never-contact list refuses even with no cache on disk
v = EX.default_stay_check('2025-000201-CA-01')
rec('real stay_gate refuses a NEVER_CONTACT case', not v.get('ok'), v.get('code'))

# ---------------------------------------------------------------- whole-file holds
print('whole-file holds')
holds = EX.preflight()
rec('no ledger / sync / inbound scan on this machine -> export held', bool(holds), holds)
out = os.path.join(TMP, 'out')
rc = EX.main(['--out-dir', out, '--twin', os.path.join(TMP, 'none.html')])
rec('held export exits 3 and writes nothing', rc == 3 and not os.path.exists(out), rc)

print('\n%d/%d passed' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
