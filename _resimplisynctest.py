"""resimpli_sync.py - REsimpli skip-trace exports into the phone cache, add-only.

Invented data only: no network, no real leads, temp DEALFLOW_DIR / HOME / cache / dnc_scrub.json.
Pins the rules that matter for Call Mode:
  * a number goes to a lead only when the owner it is listed under IS one of that lead's owners:
    surname AND given name, whatever the word order; the other owner's numbers stay out
  * the same house with a relative, a namesake, a company, an estate, a dismissed lead: skipped
  * a different unit in the same building, or a unit on one side only: skipped
  * DNC is read fail-closed (each flag column on its own), applied to the number wherever it is
    cached (even on another lead), recorded in dnc_scrub.json, and never cleared
  * a lead that would get nothing dialable gets no entry, so skiptrace.py can still trace it
  * existing numbers and entries are never removed or replaced; a rerun adds nothing
  * a number the cache already holds as DNC, on any lead, is DNC on the lead it is added to
  * a lead never ends up with more numbers than the board bake keeps (phone_src.MAX_PHONES)
  * a line that names two people ('PEREZ JOSE & GARCIA MARIA') is two people; Jr and Sr are two people
  * a missing, unreadable or concurrently changed cache or sidecar stops the run before anything is
    replaced; a torn sidecar is left alone; a failed write says what already landed
  * every file is read (no newest-by-mtime pick); a duplicate download counts once
  * a stray text file is skipped by name; an export that cannot be read in full (not UTF-8, a flag
    column missing) is refused, because its DNC flags would be missing from the merge
  * tracerfy_mcp's paid DNC lane does not re-scrub a number this tool flagged
"""
import contextlib, csv, datetime, io, json, os, pathlib, shutil, sys, tempfile
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TMP = tempfile.mkdtemp()
DFDIR = os.path.join(TMP, 'DEALFLOW')
HOME = os.path.join(TMP, 'home')
os.environ['DEALFLOW_DIR'] = DFDIR
os.environ['HOME'] = HOME
os.environ['USERPROFILE'] = HOME
DL = os.path.join(HOME, 'Downloads')
os.makedirs(DL)

import resimpli_sync as RS
import skiptrace as S

ok, bad = [], []
def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))

TODAY = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)

def epoch_ms(d):
    return str(int(datetime.datetime(d.year, d.month, d.day, 12, tzinfo=datetime.timezone.utc).timestamp() * 1000))

HDR = ['firstName', 'lastName', 'fullName', 'firstName2', 'lastName2', 'fullName2',
       'propertyStreetAddress', 'propertyStreetAddress2', 'propertyZipCode', 'opt', 'skipTracedDate']
for i in range(1, 11):
    HDR += ['Phone_%d' % i, 'Phone_%d_status' % i, 'Phone_%d_type' % i, 'Phone_%d_DNC' % i,
            'Phone_%d_IsLitigator' % i]


def put(r, slot, spec):
    """spec: (number, type, dnc) with consistent flags, or a dict that sets each flag column alone."""
    if isinstance(spec, dict):
        r['Phone_%d' % slot] = spec['n']
        r['Phone_%d_type' % slot] = spec.get('type', 'Mobile')
        r['Phone_%d_DNC' % slot] = spec.get('DNC', 'No')
        r['Phone_%d_status' % slot] = spec.get('status', '[]')
        r['Phone_%d_IsLitigator' % slot] = spec.get('lit', '')
    else:
        num, typ, dnc = spec
        r['Phone_%d' % slot] = num
        r['Phone_%d_type' % slot] = typ
        r['Phone_%d_DNC' % slot] = 'Yes' if dnc else 'No'
        r['Phone_%d_status' % slot] = '["DNC"]' if dnc else '[]'


def row(first, last, street, z, g1=(), g2=(), owner2=('', ''), unit='', traced=None, opt='No'):
    """One export row: owner 1 with phones in slots 1-5, owner 2 with phones in slots 6-10."""
    r = dict.fromkeys(HDR, '')
    r.update(firstName=first, lastName=last, fullName=(first + ' ' + last).strip(),
             firstName2=owner2[0], lastName2=owner2[1], fullName2=' '.join(owner2).strip(),
             propertyStreetAddress=street, propertyStreetAddress2=unit, propertyZipCode=z, opt=opt,
             skipTracedDate=epoch_ms(YESTERDAY) if traced is None else traced)
    for i, spec in enumerate(g1, 1):
        put(r, i, spec)
    for i, spec in enumerate(g2, 6):
        put(r, i, spec)
    return r


def write_csv(path, rows, header=HDR, encoding='utf-8'):
    with open(path, 'w', encoding=encoding, newline='') as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def lead(n, addr, owners, **extra):
    d = {'Case #': case(n), 'Address': addr + ', MIAMI, FL, 33100', 'owners': owners}
    d.update(extra)
    return d


def case(n):
    return '2026-0000%02d-CA-01' % n


RES = os.path.join(TMP, 'skiptrace_results.json')
SIDE = os.path.join(TMP, 'dnc_scrub.json')
STATUS = os.path.join(DFDIR, 'resimpli_sync_status.json')
TMPR, TMPS = RES + '.resimpli.tmp', SIDE + '.resimpli.tmp'      # this tool's own temp names
S.RESULTS = RES

LEADS = [
    lead(1, '100 SW 10TH COURT', 'TESTER, ANA'),
    lead(2, '200 NW 20 AVE', 'SAMPLE, BO'),
    lead(3, '300 NE 3 ST', 'OTHER, CY'),
    lead(4, '400 SE 4 TER', 'DEEZ, DAN'),
    lead(5, '500 SW 5 ST APT 4', 'UNITY, UMA'),
    lead(6, '600 NW 6 AVE #12', 'HASHTAG, HAL'),
    lead(7, '700 NE 7 ST', 'PLAIN, PAT'),
    lead(8, '800 SE 8 CT', 'FLAG, FAY'),
    lead(9, '900 SW 9 ST #12', 'NINE, NAN'),
    lead(10, '1000 NW 10 ST', 'KEEP, KAY'),
    lead(11, '1100 NE 11 ST', 'LEAD, LIZ'),
    lead(12, '1200 SW 12 ST', 'ACME HOLDINGS LLC'),
    lead(13, '1300 NE 13 ST', 'ESTATE OF ROY DEAD'),
    lead(14, '1400 NW 14 ST', 'DROP, DAVE', lpDismissed=True),
    lead(15, '1500 SW 15 ST', 'NUNEZ, JOSE'),
    lead(16, '1600 SE 16 ST', 'OCONNOR, MARY'),
    lead(17, '1700 NW 17 ST', 'ROE, RICK; ROE, RITA'),
    lead(18, '1800 SW 18 ST', 'GREEN, GUS; GREEN, GIA'),
    lead(19, '1900 NE 19 ST', 'ONE, OLA'),
    lead(20, '2000 SE 20 ST', 'OPTOUT, OZ'),
    lead(21, '2100 NW 21 ST', 'CACHE, CAL'),
    lead(22, '2200 SW 22 ST', 'DA COSTA, MARIA'),
    lead(23, '2300 NE 23 ST', 'TRUSTY, TED'),
    lead(25, '2500 NW 25 ST', 'AUDIT, ABE'),
    lead(26, '2600 NE 26 ST', 'FLAGGED, FRED'),
    lead(27, '2700 NW 27 ST', 'MISMATCH, MIA', ownerMismatch=True),
    lead(28, '2800 SW 28 ST', 'CLOSED, CLEO', lpClosed=True),
    lead(29, '2900 NW 29 ST', 'SLOT, SAM'),
    lead(30, '3000 NW 30 ST', 'CARRY, CARL'),
    lead(31, '3100 NW 31 ST', 'CARRY, CARA'),
    lead(32, '3200 NW 32 ST', 'CAP, CASS'),
    lead(34, '3400 NW 34 ST', 'PEREZ JOSE & GARCIA MARIA'),
    lead(35, '3500 NW 35 ST', '', oname='WEST, WES & WEST, WENDY'),
    lead(36, '3600 NW 36 ST', 'PEREZ, JOSE SR'),
    lead(37, '3700 NW 37 ST', 'DOE, DON', **{'Case #': 'LP-DOE, DON'}),        # lp_leads.py's key for a row with no case number
]
S.load_all_leads = lambda: LEADS


def M(n, typ='Mobile', dnc=False):
    return ('305555%04d' % n, typ, dnc)


CACHE = {
    case(2): {'phones': [{'number': '3055550200', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'},
    # skiptrace.py keeps an 11-digit number as the provider returned it
    case(8): {'phones': [{'number': '13055550801', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'},
    case(10): {'phones': [{'number': '3055551001', 'type': 'Mobile', 'dnc': True}], 'source': 'tracerfy'},
    # Tracerfy found nothing for this one
    case(11): {'phones': [], 'source': 'tracerfy', 'traced': '2026-09-10'},
    # numbers the export flags DNC on rows that match no lead, or that are opted out
    case(21): {'phones': [{'number': '3055552001', 'type': 'Mobile', 'dnc': False},
                          {'number': '305-555-9980', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'},
    # an earlier, looser merge put a namesake's number on lead 3 and a right number on lead 25
    case(3): {'phones': [{'number': '3055550301', 'type': 'Mobile', 'dnc': False, 'src': 'resimpli'},
                         {'number': '3055550302', 'type': 'Mobile', 'dnc': False, 'src': 'resimpli'}],
              'source': 'resimpli'},
    case(25): {'phones': [{'number': '3055552501', 'type': 'Mobile', 'dnc': False, 'src': 'resimpli'}],
               'source': 'resimpli'},
}

ROWS = [
    # owner 1's numbers land, owner 2's (a stranger to lead 1) do not
    row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[M(101), (' (305) 555-0102', 'Landline', True)],
        g2=[M(103)], owner2=('Ben', 'Zeta')),
    row('Bo', 'Sample', '200 Nw 20th Ave', '33100', g1=[M(200), ('13055550201', 'Mobile', False)]),
    # same house: a relative (same surname) and a namesake (same first name) are not the owner
    row('Zed', 'Other', '300 Ne 3rd St', '33100', g1=[M(300)]),
    row('Cy', 'Stranger', '300 Ne 3rd St', '33100', g1=[M(301)]),
    # every number DNC: nothing dialable, so no entry, but the flag is still recorded
    row('Dan', 'Deez', '400 Se 4th Terrace', '33100', g1=[M(400, 'Landline', True)]),
    # units: same unit matches (unit column vs inline APT), another unit in the building does not,
    # and neither does a unit on one side only, in either direction
    row('Uma', 'Unity', '500 Sw 5th St', '33100', g1=[M(501), M(501)], unit='Unit 4'),
    row('Uma', 'Unity', '500 Sw 5th St', '33100', g1=[M(502)], unit='Unit 9'),
    row('Zed', 'Zzzz', '500 Sw 5th St', '33100', g1=[M(503)], unit='Unit 7'),
    row('Hal', 'Hashtag', '600 Nw 6th Ave', '33100', g1=[M(601)]),
    row('Pat', 'Plain', '700 Ne 7th St Apt 2', '33100', g1=[M(701)]),
    # a DNC flag on a number the cache holds as clean (spelled 13055550801), plus one new clean number
    row('Fay', 'Flag', '800 Se 8th Ct', '33100', g1=[M(801, 'Mobile', True), M(802)]),
    # '#12' on the board side, '# 12' in the unit column: same unit
    row('Nan', 'Nine', '900 Sw 9th St', '33100', g1=[M(901)], unit='# 12'),
    # REsimpli says clean for a number the cache already holds as DNC: the flag stays
    row('Kay', 'Keep', '1000 Nw 10th St', '33100', g1=[M(1001)]),
    # all-DNC onto an entry that has no phones (Tracerfy no-hit): not appended, skiptrace can retry
    row('Liz', 'Lead', '1100 Ne 11th St', '33100', g1=[M(1101, 'Mobile', True)]),
    # a company is not a person; nor is an estate; nor a lead someone dismissed
    row('', 'Acme Holdings LLC', '1200 Sw 12th St', '33100', g2=[M(1201)], owner2=('Ann', 'Acme')),
    row('Roy', 'Dead', '1300 Ne 13th St', '33100', g1=[M(1301)]),
    row('Dave', 'Drop', '1400 Nw 14th St', '33100', g1=[M(1401)]),
    # accents and apostrophes fold to how the county roll writes them; the export has no date
    row('José', 'Núñez', '1500 Sw 15th St', '33100', g1=[M(1501)], traced=''),
    row('Mary', "O'Connor", '1600 Se 16th St', '33100', g1=[M(1601)]),
    # the second of two owners on the lead
    row('Rita', 'Roe', '1700 Nw 17th St', '33100', g1=[M(1701)]),
    # both owners on the lead, so both groups of numbers
    row('Gus', 'Green', '1800 Sw 18th St', '33100', g1=[M(1801)], g2=[M(1802)], owner2=('Gia', 'Green')),
    # a company owner with the person found behind it in owner 2: that person's numbers are theirs
    row('', 'Big Corp LLC', '1900 Ne 19th St', '33100', g2=[M(1901)], owner2=('Ola', 'One')),
    # opted out: not merged, and the number is DNC everywhere it is cached (lead 21 holds it)
    row('Oz', 'Optout', '2000 Se 20th St', '33100', g1=[M(2001)], opt='Yes'),
    # a shared particle is not a surname in common
    row('Maria', 'Da Silva', '2200 Sw 22nd St', '33100', g1=[M(2201)]),
    row('Ted', 'Trusty Revocable Living Trust', '2300 Ne 23rd St', '33100', g1=[M(2301)]),
    row('Abe', 'Audit', '2500 Nw 25th St', '33100', g1=[M(2501)]),
    # no lead at this address; the second cell is what Excel does to a phone column
    row('Al', 'Nobody', '999 Nowhere Rd', '33100', g1=[M(999), {'n': '3.05555E+09'}]),
    # no lead here either, but this number is DNC and lead 21 holds it as clean
    row('Xan', 'Xu', '998 Nowhere Rd', '33100', g1=[M(9980, 'Mobile', True)]),
]


def fresh(cache=None, side=None):
    for p in (RES, SIDE, RES + '.tmp', SIDE + '.tmp', TMPR, TMPS):
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(DFDIR, ignore_errors=True)
    for f in os.listdir(DL):
        os.remove(os.path.join(DL, f))
    if cache is not None:
        with open(RES, 'w') as f:
            json.dump(cache, f)
    if side is not None:
        with open(SIDE, 'w') as f:
            json.dump(side, f)


def run(args=(), files=None, encoding='utf-8'):
    """main() with the given exports dropped in Downloads. -> (exit code, stdout)."""
    for name, rows in (files or {}).items():
        write_csv(os.path.join(DL, name), rows, encoding=encoding)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = RS.main(list(args))
    return rc, buf.getvalue()


def load(p):
    return json.load(open(p)) if os.path.exists(p) else None


def nums(d, n):
    return [p['number'] for p in (d.get(case(n)) or {}).get('phones', [])]


def snapshot():
    out = {}
    for base, _, fs in os.walk(TMP):
        for f in fs:
            p = os.path.join(base, f)
            out[p] = open(p, 'rb').read()
    return out


try:
    # ---------------------------------------------------------------- pure functions
    rec('address split folds suffix and ordinal and reads the unit',
        RS.split_address('200 Nw 20th Ave Unit 4') == ('200 NW 20 AVE', '4') and
        RS.split_address('200 NW 20 AVENUE, MIAMI, FL') == ('200 NW 20 AVE', ''))
    rec('#12, # 12, Apt 12, Apartment 12, Unit 12 and Apt No 12 are one unit',
        {RS.split_address('1 A ST ' + u)[1] for u in ('#12', '# 12', 'Apt 12', 'APARTMENT 12', 'UNIT 12', 'Apt No 12')} == {'12'})
    rec('unit column wins, inline unit is the fallback',
        RS.row_address({'propertyStreetAddress': '500 Sw 5th St', 'propertyStreetAddress2': 'Unit 4'}) == ('500 SW 5 ST', '4') and
        RS.row_address({'propertyStreetAddress': '500 Sw 5th St Apt 7', 'propertyStreetAddress2': ''}) == ('500 SW 5 ST', '7'))
    rec('11-digit number loses its leading 1',
        RS.norm_number('13055550101') == '3055550101' == RS.norm_number('(305) 555-0101'))
    rec('numbers: float column, plus-one and punctuation read; sci-notation, extension, non-US do not',
        [RS.parse_number(v) for v in ('3055550101.0', '1-305-555-0101', ' (305) 555-0101 ', '3.05555E+09',
                                      '305-555-0101 x22', '1234567890', '555-0101', '', None)] ==
        ['3055550101', '3055550101', '3055550101', '', '', '', '', '', ''])
    rec('numbers: a sci-notation cell that would still read as ten digits is refused, not guessed',
        RS.parse_number('3.0555501E+09') == '' and RS.parse_number('3E+09') == '' and RS.parse_number('3055550109') == '3055550109')
    rec('owner slots: 1-5 are owner 1, 6-10 owner 2, anything beyond belongs to nobody we read',
        [RS.owner_of(s) for s in (1, 5, 6, 10, 11)] == [1, 1, 2, 2, 3])
    rec('type: mobile / landline / unknown', [RS.norm_type(v) for v in ('Mobile', 'mobile', 'Landline', 'VOIP', '', None)] ==
        ['Mobile', 'Mobile', 'Landline', '', '', ''])

    def flag(**kw):
        x = {'Phone_1_DNC': 'No', 'Phone_1_status': '[]', 'Phone_1_IsLitigator': ''}
        x.update({'Phone_1_' + k: v for k, v in kw.items()})
        return RS.read_flags(x, 1)[0]
    rec('flags: clean only when every column says so', flag() is False)
    rec('flags: the DNC column alone flags (status and litigator clean)',
        all(flag(DNC=v) for v in ('Yes', 'yes', 'Y', 'TRUE', '1', 'Do Not Call', '')))
    rec('flags: the status column alone flags, and a blank one is not "clean"',
        all(flag(status=v) for v in ('["DNC"]', '["Litigator"]', '["TCPA"]', '["Do Not Call"]', '[ "DNC" ]', 'DNC', '')))
    rec('flags: the litigator column alone flags; blank is not a flag',
        all(flag(IsLitigator=v) for v in ('Yes', 'true', 'TRUE', '1')) and not flag(IsLitigator='') and not flag(IsLitigator='No'))
    def lit(v):
        return RS.read_flags({'Phone_1_DNC': 'No', 'Phone_1_status': '[]', 'Phone_1_IsLitigator': v}, 1)
    rec('flags: litigator Yes / true / y / 1 is a plain flag; No / false / blank is clean; anything else flags and is counted',
        all(lit(v) == (True, False) for v in ('Yes', 'true', 'TRUE', 'y', '1')) and
        all(lit(v) == (False, False) for v in ('', 'No', 'false', 'n', '0')) and
        all(lit(v) == (True, True) for v in ('Maybe', 'pending', '2')), [(v, lit(v)) for v in ('Yes', 'No', 'Maybe')])
    rec('flags: an unrecognised value flags and is counted',
        RS.read_flags({'Phone_1_DNC': 'Maybe', 'Phone_1_status': '[]'}, 1) == (True, True) and
        RS.read_flags({'Phone_1_DNC': 'No', 'Phone_1_status': '["Wrong Number"]'}, 1) == (True, True) and
        RS.read_flags({'Phone_1_DNC': 'Yes', 'Phone_1_status': '["DNC"]'}, 1) == (True, False))
    rec('opt: No / blank is fine, anything else is opted out',
        [RS.opted_out({'opt': v}) for v in ('No', '', 'no', 'Yes', 'true', '1', 'Opted out')] ==
        [False, False, False, True, True, True, True])
    today = TODAY.isoformat()
    rec('traced: epoch ms and seconds and ISO read; the future is clamped to today; junk is empty',
        RS.row_traced({'skipTracedDate': epoch_ms(YESTERDAY)}, today) == YESTERDAY.isoformat() and
        RS.row_traced({'skipTracedDate': str(int(datetime.datetime(2026, 9, 28, 12, tzinfo=datetime.timezone.utc).timestamp()))}, '2026-09-29') == '2026-09-28' and
        RS.row_traced({'skipTracedDate': '2026-09-28T10:00:00Z'}, '2026-09-29') == '2026-09-28' and
        RS.row_traced({'skipTracedDate': epoch_ms(datetime.date(2099, 1, 1))}, '2026-09-29') == '2026-09-29' and
        RS.row_traced({'skipTracedDate': 'soon'}, today) == '' and RS.row_traced({}, today) == '')

    def same(csv_first, csv_last, board):
        ppl = RS.row_people({'firstName': csv_first, 'lastName': csv_last})
        owners = RS.owner_people({'owners': board})
        return bool(ppl) and any(RS.person_matches(f, l, o) for _, f, l in ppl for o in owners)
    rec('names: LAST, FIRST / FIRST LAST / LAST FIRST all agree, and middle names and suffixes do not matter',
        same('Maria', 'Garcia', 'GARCIA, MARIA') and same('Maria', 'Garcia', 'MARIA GARCIA') and
        same('Maria', 'Garcia', 'GARCIA MARIA ELENA') and same('Maria Elena', 'Garcia', 'GARCIA, MARIA') and
        same('Elliot', 'Levy', 'ELLIOT LEVY TRS') and same('Rosa', 'Martinez', 'ROSA M MARTINEZ LE') and
        same('Juan', 'Garcia', 'GARCIA LOPEZ, JUAN CARLOS') and same('Carlos', 'Lopez', 'GARCIA LOPEZ, JUAN CARLOS'))
    rec('names: two unrelated Marias, or relatives sharing a surname, are not the same person',
        not same('Maria', 'Lopez', 'GARCIA, MARIA') and not same('Jose', 'Garcia', 'GARCIA, MARIA') and
        not same('Maria', 'Garcia', 'GARCIA, JOSE'))
    rec('names: the surname repeated in the given-name column is not evidence of a given name',
        not same('Lopez', 'Lopez', 'LOPEZ, ANA') and not same('Garcia', 'Garcia', 'GARCIA, MARIA') and
        same('Ana', 'Lopez', 'LOPEZ, ANA'))
    rec('names: accents, apostrophes and hyphens fold',
        same('José', 'Núñez', 'NUNEZ, JOSE') and same('Mary', "O'Connor", 'OCONNOR, MARY') and
        same('Ana', 'Saint-Amour', 'SAINT AMOUR, ANA'))
    rec('names: a shared particle is not a surname', not same('Maria', 'Da Silva', 'DA COSTA, MARIA') and
        not same('Ana', 'De la Cruz', 'DE LA ROSA, ANA'))
    rec('names: companies, trusts, estates, heirs and placeholders are nobody',
        not same('Ann', 'Acme', 'ACME HOLDINGS LLC') and not same('Ann', 'Rivera', 'ANN RIVERA REVOCABLE LIVING TRUST') and
        not same('Roy', 'Dead', 'ESTATE OF ROY DEAD') and not same('Roy', 'Dead', 'DEAD, ROY DECEASED') and
        not same('Frank', 'Miller', 'UNKNOWN HEIRS OF FRANK MILLER') and not same('Owner', 'Title', '(owner via title search)') and
        RS.row_people({'firstName': '', 'lastName': 'Acme Holdings LLC'}) == [] and
        RS.row_people({'firstName': 'Ted', 'lastName': 'Trusty Revocable Living Trust'}) == [] and
        RS.row_people({'firstName': '', 'lastName': 'Madonna'}) == [] and RS.row_people({'firstName': 'Cher', 'lastName': ''}) == [])
    rec('names: two owners on the lead are each an owner', same('Rita', 'Roe', 'ROE, RICK; ROE, RITA') and
        same('Rick', 'Roe', 'ROE, RICK; ROE, RITA') and not same('Rex', 'Roe', 'ROE, RICK; ROE, RITA'))
    LINE = 'PEREZ JOSE & GARCIA MARIA'
    rec('names: a county-roll line that names two people is two people, not one who is Jose Garcia',
        not same('Jose', 'Garcia', LINE) and not same('Maria', 'Perez', LINE) and
        same('Jose', 'Perez', LINE) and same('Maria', 'Garcia', LINE) and
        same('Maria', 'Garcia', 'PEREZ JOSE AND GARCIA MARIA') and not same('Jose', 'Garcia', 'PEREZ JOSE AND GARCIA MARIA'))
    rec('names: a second name of one word shares the first person\'s surname',
        same('Maria', 'Perez', 'PEREZ JOSE & MARIA') and same('Jose', 'Perez', 'PEREZ JOSE & MARIA') and
        not same('Maria', 'Garcia', 'PEREZ JOSE & MARIA') and same('Mary', 'Smith', 'SMITH JOHN AND MARY'))
    rec('names: Jr and Sr are two people; a marker on one side only is not evidence either way',
        not same('Jose', 'Perez Jr', 'PEREZ, JOSE SR') and not same('Jose Jr', 'Perez', 'PEREZ, JOSE SR') and
        same('Jose', 'Perez Sr', 'PEREZ, JOSE SR') and same('Jose', 'Perez', 'PEREZ, JOSE SR') and
        same('Jose', 'Perez Jr', 'PEREZ, JOSE') and not same('Jose', 'Perez Iii', 'PEREZ, JOSE II'))
    rec('units: two units of one tower do not collapse into one, and "4 B" is "4B"',
        RS.split_address('1 A ST BLDG 1 APT 23')[1] != RS.split_address('1 A ST BLDG 12 APT 3')[1] and
        RS.split_address('1 A ST BLDG 1 APT 23')[1] == RS.split_address('1 A ST UNIT 1-23')[1] and
        RS.split_address('1 A ST APT 4 B')[1] == RS.split_address('1 A ST APT 4B')[1] == '4B',
        [RS.split_address('1 A ST ' + u)[1] for u in ('BLDG 1 APT 23', 'BLDG 12 APT 3', 'UNIT 1-23', 'APT 4 B')])
    rec('a lis pendens row with no case number ("LP-<owner name>") is not a case number',
        not RS.real_case('LP-DOE, DON') and not RS.real_case('') and not RS.real_case(None) and
        RS.real_case('2026-000123-CA-01') and RS.real_case('CACE25012345') and RS.real_case('502025CA001234XXXXMB'))
    rec('a trailing delimiter (a None key on the rows) does not break slot detection',
        RS.slots_of([{'Phone_1': '', 'Phone_2': '', None: ['']}]) == [1, 2] and RS.slots_of([]) == [])

    # ---------------------------------------------------------------- one full run
    fresh(CACHE)
    before = open(RES).read()
    rc, out = run(['--dry-run'], files={'SkipTrace_1.csv': ROWS})
    rec('dry run exits 0 and leaves everything alone',
        rc == 0 and open(RES).read() == before and not os.path.exists(SIDE) and not os.path.exists(STATUS)
        and not os.path.exists(os.path.join(DFDIR, 'backups')) and not os.path.exists(os.path.join(DFDIR, 'imports')), out[-300:])
    rec('dry run says what it would do', 'DRY RUN - nothing written' in out and 'new_numbers' in out)

    write_csv(os.path.join(DL, 'SkipTrace_1 (1).csv'), ROWS)                           # same download twice
    write_csv(os.path.join(DL, 'SkipTrace_2.csv'), ROWS[:1])                            # older subset file
    write_csv(os.path.join(DL, 'SkipTrace_junk.csv'), [{'a': '1'}], header=['a'])       # not an export
    rc, out = run([])
    d, side, st = load(RES), load(SIDE), load(STATUS)
    t = st['total']
    rec('exit 0', rc == 0, out[-400:])
    rec('duplicate download counted once, subset file still read', len(st['files']) == 2, [f['file'] for f in st['files']])
    rec('stray non-export files are skipped by name and do not block the real exports',
        st['skipped_files'] == ['SkipTrace_junk.csv'], st['skipped_files'])

    a = d.get(case(1)) or {}
    rec('owner 1 matched: only owner 1\'s numbers land (owner 2 is a stranger to the lead)',
        nums(d, 1) == ['3055550101', '3055550102'], nums(d, 1))
    rec('REsimpli DNC flag lands as dnc=True', [p['dnc'] for p in a.get('phones', [])] == [False, True])
    rec('phone dict has the keys make_tracker reads, a normalized type and the provenance tag',
        all({'number', 'type', 'dnc', 'src'} <= set(p) and p['src'] == 'resimpli' for p in a['phones']) and
        [p['type'] for p in a['phones']] == ['Mobile', 'Landline'])
    rec('new entry: source, county, owner label, no emails, REsimpli\'s own trace date',
        (a['source'], a['county'], a['name'], a['emails'], a['traced'], a['resimpli']) ==
        ('resimpli', 'MIAMI-DADE', 'TESTER, ANA', [], YESTERDAY.isoformat(), TODAY.isoformat()), a)
    rec('new entry carries the board\'s own address for the lead', a['address'].startswith('100 SW 10TH COURT'), a.get('address'))
    b = d[case(2)]
    rec('existing entry keeps its number and provider, gains only the new one',
        [p['number'] for p in b['phones']] == ['3055550200', '3055550201'] and b['source'] == 'tracerfy'
        and 'traced' not in b)
    rec('same house, a relative or a namesake: skipped', case(3) in d and nums(d, 3) == ['3055550301', '3055550302'] and
        '3055550300' not in json.dumps(d))
    rec('all-DNC row for a lead with no entry: no entry (skiptrace can still trace it)', case(4) not in d)
    rec('same unit (unit column vs inline APT): matched, that unit only, and a number listed twice lands once',
        nums(d, 5) == ['3055550501'], nums(d, 5))
    rec('different unit in the same building: skipped, surname or not',
        '3055550502' not in json.dumps(d) and '3055550503' not in json.dumps(d))
    rec('unit on the board side only: skipped', case(6) not in d)
    rec('unit on the REsimpli side only: skipped', case(7) not in d)
    rec('#12 against "# 12": matched', nums(d, 9) == ['3055550901'])
    rec('DNC flag tightens a number held as clean under another spelling, with no duplicate',
        [(p['number'], p['dnc']) for p in d[case(8)]['phones']] == [('13055550801', True), ('3055550802', False)],
        d[case(8)]['phones'])
    rec('a clean REsimpli flag never clears an existing DNC flag',
        d[case(10)]['phones'] == [{'number': '3055551001', 'type': 'Mobile', 'dnc': True}])
    rec('all-DNC row onto an entry with no phones: nothing appended, entry untouched',
        d[case(11)] == CACHE[case(11)])
    rec('company owner, estate owner, dismissed lead: skipped',
        case(12) not in d and case(13) not in d and case(14) not in d)
    rec('accented and apostrophe names match; a missing REsimpli date leaves traced unset',
        nums(d, 15) == ['3055551501'] and 'traced' not in d[case(15)] and nums(d, 16) == ['3055551601'])
    rec('the second of two owners on the lead matches', nums(d, 17) == ['3055551701'])
    rec('both owners on the lead: both owners\' numbers', nums(d, 18) == ['3055551801', '3055551802'])
    rec('a company row with the person found behind it in owner 2: that person\'s numbers land',
        nums(d, 19) == ['3055551901'])
    rec('an opted-out row is not merged', case(20) not in d)
    rec('a shared particle does not match; a trust row is nobody', case(22) not in d and case(23) not in d)
    rec('unmatched rows never become leads',
        sorted(d) == sorted({case(n) for n in (1, 2, 3, 8, 10, 11, 15, 16, 17, 18, 19, 21, 25, 5, 9)}), sorted(d))
    rec('lead 25 already had its number from the earlier merge: nothing added', nums(d, 25) == ['3055552501'])

    rec('every number in the export that is DNC is DNC on lead 21 too, on rows that matched nothing',
        [(p['number'], p['dnc']) for p in d[case(21)]['phones']] == [('3055552001', True), ('305-555-9980', True)],
        d[case(21)]['phones'])
    flagged6 = ['3055550102', '3055550400', '3055550801', '3055551101', '3055552001', '3055559980']
    want_keys = set(flagged6) | {'1' + n for n in flagged6} | {'305-555-9980'}    # the seam looks a number up by its exact stored string
    rec('dnc_scrub.json got every flagged number in the 10-digit and the 11-digit spelling, and as the cache spells it',
        set(side) == want_keys, sorted(side))
    rec('sidecar entries say where the flag came from and set a flag the board bake reads',
        all(v['national_dnc'] is True and v['source'] == 'resimpli' and v['checked'] == TODAY.isoformat()
            for v in side.values()))
    rec('the numbers REsimpli says are clean are not recorded', '3055551001' not in side and '3055550101' not in side)

    rec('counts',
        (t['rows'], t['rows_with_phone'], t['unmatched_rows_with_phone'], t['unit_mismatch'],
         t['unit_conflict_same_person'], t['entity_rows'], t['flagged_lead_rows'], t['owner_mismatch'],
         t['opt_rows'], t['matched_rows'], t['numbers_other_owner']) ==
        (29, 29, 2, 4, 3, 1, 1, 5, 1, 15, 2), t)
    rec('every row with a phone falls in exactly one bucket',
        t['rows_with_phone'] == sum(t[k] for k in ('unmatched_rows_with_phone', 'unit_mismatch', 'entity_rows',
                                                  'flagged_lead_rows', 'owner_mismatch', 'opt_rows', 'matched_rows')))
    rec('lead and number counts',
        (t['leads_matched'], t['leads_had_phone'], t['leads_new_phone'], t['leads_dnc_only_skipped'],
         t['new_numbers'], t['new_numbers_dnc'], t['new_mobile_clean'], t['entries_unusable']) ==
        (14, 4, 8, 2, 12, 1, 11, 0), t)
    rec('unreadable cells are counted, flags all in the expected vocabulary',
        (t['numbers_unreadable'], t['flags_unexpected']) == (1, 0), t)
    rec('global DNC counts',
        (t['dnc_flagged_numbers'], t['dnc_tightened'], t['dnc_sidecar_added'], t['dnc_sidecar_tightened']) == (6, 3, 13, 0), t)
    rec('audit: the earlier merge\'s namesake number is unconfirmed, the right one is confirmed',
        (t['cache_resimpli_numbers'], t['resimpli_confirmed'], t['resimpli_unconfirmed'], t['resimpli_unconfirmed_leads']) == (15, 13, 2, 1) and
        st['resimpli_unconfirmed_cases'] == [case(3)], (t, st['resimpli_unconfirmed_cases']))
    rec('audit says so in words and removes nothing', 'NOTE: 2 cached REsimpli numbers on 1 leads' in out and
        nums(d, 3) == ['3055550301', '3055550302'])
    rec('status carries counts and case numbers only, no names or numbers',
        not any(s in json.dumps(st) for s in ('Tester', 'TESTER', 'Ana', '305555')), json.dumps(st)[:200])
    bk = sorted(os.listdir(os.path.join(DFDIR, 'backups')))
    rec('backup of the cache written outside the repo; none of the absent sidecar',
        len(bk) == 1 and bk[0].startswith('skiptrace_results.pre-resimpli-'), bk)
    imp = sorted(os.listdir(os.path.join(DFDIR, 'imports', 'resimpli')))
    rec('exports copied into DEALFLOW imports, one per distinct file', len(imp) == 2, imp)
    rec('no temp file left beside the cache or the sidecar',
        not any(os.path.exists(x) for x in (RES + '.tmp', SIDE + '.tmp', TMPR, TMPS)))

    # ---------------------------------------------------------------- rerun
    snap = snapshot()
    rc, out = run([])
    rec('rerun changes nothing at all (cache, sidecar, backups, imports)', rc == 0 and
        {k: v for k, v in snapshot().items() if not k.endswith('resimpli_sync_status.json')} ==
        {k: v for k, v in snap.items() if not k.endswith('resimpli_sync_status.json')}, out[-300:])
    t2 = load(STATUS)['total']
    rec('rerun: nothing new, nothing tightened', (t2['new_numbers'], t2['dnc_tightened'], t2['dnc_sidecar_added']) == (0, 0, 0), t2)

    # ---------------------------------------------------------------- the sidecar
    fresh(CACHE, side={'3055550102': {'national_dnc': False, 'state_dnc': False, 'states': [], 'case': 'X',
                                      'checked': '2026-01-01'},
                       '3055550400': {'national_dnc': True, 'state_dnc': False, 'checked': '2026-01-01'},
                       '9999999999': {'national_dnc': False, 'state_dnc': False, 'checked': '2026-01-01'},
                       '3055550801': {'national_dnc': False, 'state_dnc': True, 'states': ['FL'], 'checked': '2026-01-01'}})
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    side, t = load(SIDE), load(STATUS)['total']
    rec('sidecar: an earlier registry miss is tightened, a hit (national or state) is left alone, strangers are untouched',
        side['3055550102'] == {'national_dnc': True, 'state_dnc': False, 'states': [], 'case': 'X',
                               'checked': '2026-01-01', 'resimpli': TODAY.isoformat(), 'source': 'resimpli'} and
        side['3055550400'] == {'national_dnc': True, 'state_dnc': False, 'checked': '2026-01-01'} and
        side['9999999999'] == {'national_dnc': False, 'state_dnc': False, 'checked': '2026-01-01'} and
        side['3055550801'] == {'national_dnc': False, 'state_dnc': True, 'states': ['FL'], 'checked': '2026-01-01'}, side)
    rec('sidecar: counts', (t['dnc_sidecar_added'], t['dnc_sidecar_tightened']) == (10, 1), t)
    rec('sidecar: backed up before it changed',
        any(f.startswith('dnc_scrub.pre-resimpli-') for f in os.listdir(os.path.join(DFDIR, 'backups'))))

    garbage = b'{"3055550102": {"national_dnc": tru'
    fresh(CACHE)
    open(SIDE, 'wb').write(garbage)
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('a torn sidecar is left exactly as found, said so, and the cache still updates',
        rc == 0 and open(SIDE, 'rb').read() == garbage and 'WARNING: dnc_scrub.json exists but cannot be read' in out
        and load(STATUS)['dnc_scrub_json'] == 'unreadable' and nums(load(RES), 1) == ['3055550101', '3055550102'], out[-400:])
    fresh(CACHE, side=['not', 'a', 'dict'])
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('a sidecar that is not an object is left alone too', rc == 0 and load(SIDE) == ['not', 'a', 'dict'])

    # ---------------------------------------------------------------- the cache
    fresh()
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('no cache: refused (wrong machine), nothing created',
        rc == 2 and 'no phone cache' in out and not os.path.exists(RES) and not os.path.exists(SIDE), out[-300:])
    rc, out = run(['--create'])
    rec('--create starts one', rc == 0 and nums(load(RES), 1) == ['3055550101', '3055550102'], out[-300:])

    fresh()
    open(RES, 'w').write('{"2026-000001-CA-01": {"phones": [')
    torn = open(RES, 'rb').read()
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('a torn cache is refused and left alone, never read as empty',
        rc == 2 and open(RES, 'rb').read() == torn and not os.path.exists(SIDE) and 'cannot be read' in out, out[-300:])
    fresh(cache=['a', 'list'])
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('a cache that is not an object is refused', rc == 2 and load(RES) == ['a', 'list'])

    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
    before = open(RES, 'rb').read()
    real_merge = RS.merge
    def merge_then_touch(*a, **k):
        r = real_merge(*a, **k)
        os.utime(RES, ns=(os.stat(RES).st_atime_ns, os.stat(RES).st_mtime_ns + 5_000_000_000))   # skiptrace.py saved
        return r
    RS.merge = merge_then_touch
    try:
        rc, out = run([])
    finally:
        RS.merge = real_merge
    rec('the cache changed while the merge ran: nothing replaced (exit 3), no temp, no sidecar, no status, no import copies',
        rc == 3 and 'CHANGED' in out and 'Nothing was replaced' in out and open(RES, 'rb').read() == before
        and not any(os.path.exists(x) for x in (RES + '.tmp', TMPR, TMPS)) and not os.path.exists(SIDE)
        and not os.path.exists(STATUS) and not os.path.exists(os.path.join(DFDIR, 'imports')), out[-300:])

    # the same, but the change lands while the temp file is being written
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
    before = open(RES, 'rb').read()
    real_write_tmp = RS.write_tmp
    def write_then_touch(path, obj):
        tmp = real_write_tmp(path, obj)
        if path == RES:
            os.utime(RES, ns=(os.stat(RES).st_atime_ns, os.stat(RES).st_mtime_ns + 5_000_000_000))
        return tmp
    RS.write_tmp = write_then_touch
    try:
        rc, out = run([])
    finally:
        RS.write_tmp = real_write_tmp
    rec('the cache changed while the temp file was written: nothing replaced (exit 3), temps removed, no sidecar, '
        'no status, no import copies',
        rc == 3 and 'Nothing was replaced' in out and open(RES, 'rb').read() == before
        and not any(os.path.exists(x) for x in (RES + '.tmp', TMPR, TMPS)) and not os.path.exists(SIDE)
        and not os.path.exists(STATUS) and not os.path.exists(os.path.join(DFDIR, 'imports')), out[-300:])

    # a write that fails stops the run, says what already landed, and never half-writes the cache
    fresh(CACHE)
    os.makedirs(DFDIR)
    with open(os.path.join(DFDIR, 'backups'), 'w') as fh:
        fh.write('a file where the backup folder should be')
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('a write failure before anything is replaced: exit 3, says nothing was written, cache and sidecar untouched, '
        'no temps, no status',
        rc == 3 and 'FAILED: could not write' in out and 'Written before the failure: nothing' in out and load(RES) == CACHE
        and not os.path.exists(SIDE) and not any(os.path.exists(x) for x in (RES + '.tmp', TMPR, TMPS))
        and not os.path.exists(STATUS), out[-300:])

    # an entry the tool cannot safely extend is skipped and counted, never rewritten
    fresh({case(1): {'phones': {'oops': 1}}, case(9): {'phones': None, 'source': 'tracerfy'}})
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    d, t = load(RES), load(STATUS)['total']
    rec('a malformed entry is skipped and counted', d[case(1)] == {'phones': {'oops': 1}} and t['entries_unusable'] == 1, t)
    rec('an entry with phones: null is a lead with no phones', nums(d, 9) == ['3055550901'] and d[case(9)]['source'] == 'tracerfy')

    # ---------------------------------------------------------------- refusals
    def refused(name, header, expect, rows=None, encoding='utf-8'):
        fresh(CACHE)
        snap = open(RES, 'rb').read()
        path = os.path.join(TMP, name)
        write_csv(path, rows if rows is not None else [{k: '' for k in header}], header=header, encoding=encoding)
        rc, out = run([path])
        rec('refused: ' + name, rc == 2 and expect in out and open(RES, 'rb').read() == snap and not os.path.exists(SIDE), out[-200:])

    refused('no_slot2_dnc.csv', [h for h in HDR if h != 'Phone_2_DNC'], 'Phone_2_DNC')
    refused('no_slot7_status.csv', [h for h in HDR if h != 'Phone_7_status'], 'Phone_7_status')
    refused('no_slot3_litigator.csv', [h for h in HDR if h != 'Phone_3_IsLitigator'], 'Phone_3_IsLitigator')
    refused('no_opt.csv', [h for h in HDR if h != 'opt'], 'opt')
    for col in ('firstName', 'lastName', 'propertyStreetAddress', 'propertyZipCode'):
        refused('no_%s.csv' % col, [h for h in HDR if h != col], col)
    refused('no_phones.csv', [h for h in HDR if not h.startswith('Phone_')], 'Phone_1')
    refused('slot11_no_flags.csv', HDR + ['Phone_11'], 'Phone_11_DNC')
    refused('cp1252.csv', HDR, 'not UTF-8', rows=[row('José', 'Núñez', '1 A St', '33100', g1=[M(1)])], encoding='cp1252')
    refused('utf16.csv', HDR, 'not UTF-8', rows=[row('Ana', 'Tester', '1 A St', '33100', g1=[M(1)])], encoding='utf-16')
    fresh(CACHE)
    rc, out = run([])
    rec('no export anywhere: exit 1, cache untouched', rc == 1 and 'no REsimpli export found' in out and load(RES) == CACHE, out[-200:])
    write_csv(os.path.join(DL, 'SkipTrace_junk.csv'), [{'a': '1'}], header=['a'])
    rc, out = run([])
    rec('only a stray file: exit 1, skipped by name, nothing written',
        rc == 1 and 'SKIPPED' in out and 'no usable REsimpli export found' in out and load(RES) == CACHE and not os.path.exists(SIDE), out[-200:])
    # in no-argument mode a stray text file is skipped by name and the good one still merges
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_a.csv'), [{'a': '1'}], header=['a'])
    write_csv(os.path.join(DL, 'SkipTrace_b.csv'), ROWS[:1])
    rc, out = run([])
    rec('a stray text file does not block the good one', rc == 0 and 'SKIPPED' in out and nums(load(RES), 1) == ['3055550101', '3055550102'], out[-300:])

    # an export that is one but cannot be read in full is NOT skipped: its DNC flags would be missing while
    # the other exports merge their numbers as clean
    def refused_dir(name, files, expect):
        fresh(CACHE)
        for fname, (rows, hdr, enc) in files.items():
            write_csv(os.path.join(DL, fname), rows, header=hdr, encoding=enc)
        rc, out = run([])
        rec('refused, nothing merged, nothing left behind: ' + name,
            rc == 2 and 'REFUSED' in out and expect in out and 'Move or delete that file' in out and load(RES) == CACHE
            and not os.path.exists(SIDE) and not os.path.exists(STATUS) and not os.path.exists(os.path.join(DFDIR, 'imports'))
            and not os.path.exists(os.path.join(DFDIR, 'backups')), out[-300:])
    clean = row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[M(101)])
    flags_it = row('José', 'Núñez', '1500 Sw 15th St', '33100', g1=[M(101, 'Mobile', True)])      # the same number, DNC
    refused_dir('a newer export re-saved in Excel (cp1252) lists as DNC the number the older one lists clean',
                {'SkipTrace_a.csv': ([clean], HDR, 'utf-8'), 'SkipTrace_b.csv': ([flags_it], HDR, 'cp1252')}, 'SkipTrace_b.csv')
    refused_dir('a UTF-16 export next to a good one',
                {'SkipTrace_a.csv': ([clean], HDR, 'utf-8'), 'SkipTrace_b.csv': ([flags_it], HDR, 'utf-16')}, 'SkipTrace_b.csv')
    refused_dir('an export missing one flag column next to a good one',
                {'SkipTrace_a.csv': ([clean], HDR, 'utf-8'),
                 'SkipTrace_b.csv': ([flags_it], [h for h in HDR if h != 'Phone_3_IsLitigator'], 'utf-8')}, 'Phone_3_IsLitigator')
    refused_dir('UTF-16 with no byte-order mark next to a good one',
                {'SkipTrace_a.csv': ([clean], HDR, 'utf-8'), 'SkipTrace_b.csv': ([flags_it], HDR, 'utf-16-le')}, 'SkipTrace_b.csv')
    refused_dir('an export with phone columns but no address column next to a good one',
                {'SkipTrace_a.csv': ([clean], HDR, 'utf-8'),
                 'SkipTrace_b.csv': ([flags_it], [h for h in HDR if h != 'propertyStreetAddress'], 'utf-8')}, 'propertyStreetAddress')
    fresh(CACHE)
    with open(os.path.join(DL, 'SkipTrace_bin.csv'), 'wb') as fh:                      # cannot be shown to be "not an export"
        fh.write(b'\xff\xfe\x00\x00\x80garbage')
    write_csv(os.path.join(DL, 'SkipTrace_a.csv'), [clean])
    rc, out = run([])
    rec('a binary file named like an export is refused too, by name, rather than guessed at',
        rc == 2 and 'SkipTrace_bin.csv' in out and 'Move or delete that file' in out and load(RES) == CACHE and not os.path.exists(SIDE), out[-300:])

    # ---------------------------------------------------------------- flags, end to end
    def flagged_row(**kw):
        return row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[dict(n='3055550101', **kw)])
    for label, kw in (('DNC column alone', dict(DNC='Yes')), ('status alone', dict(status='["DNC"]')),
                      ('litigator alone', dict(lit='Yes')), ('DNC column blank', dict(DNC='')),
                      ('DNC column odd', dict(DNC='Maybe')), ('status odd', dict(status='["Wrong Number"]'))):
        fresh(CACHE)
        rc, out = run([], files={'SkipTrace_1.csv': [flagged_row(**kw)]})
        rec('flag read as DNC, so the lead gets no dialable number: ' + label,
            rc == 0 and case(1) not in load(RES) and '3055550101' in load(SIDE), out[-200:])
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [flagged_row()]})
    rec('a number REsimpli calls clean in every column lands clean', nums(load(RES), 1) == ['3055550101'] and
        [p['dnc'] for p in load(RES)[case(1)]['phones']] == [False] and not os.path.exists(SIDE))
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [flagged_row(DNC='Maybe')]})
    rec('unexpected flag values are counted', load(STATUS)['total']['flags_unexpected'] == 1)

    # a number one row lists clean and another row (matching nothing) flags DNC: DNC on both, and the lead
    # whose only new number it is gets no entry at all
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [
        row('Fred', 'Flagged', '2600 Ne 26th St', '33100', g1=[M(2601)]),
        row('Fred', 'Flagged', '2600 Ne 26th St', '33100', g1=[M(2602, 'Mobile', True)]),
        row('Xan', 'Xu', '998 Nowhere Rd', '33100', g1=[M(2601, 'Mobile', True)])]})
    t = load(STATUS)['total']
    rec('a number flagged by another row is DNC on this lead too: no entry, not even a DNC-only one, '
        'and the lead counts once however many rows say so',
        rc == 0 and case(26) not in load(RES) and '3055552601' in load(SIDE) and t['leads_dnc_only_skipped'] == 1
        and t['new_numbers'] == 0, (t, out[-200:]))

    # the other flags that make skiptrace.py skip a lead
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [
        row('Mia', 'Mismatch', '2700 Nw 27th St', '33100', g1=[M(2701)]),
        row('Cleo', 'Closed', '2800 Sw 28th St', '33100', g1=[M(2801)])]})
    rec('a lead flagged ownerMismatch or lpClosed is skipped like a dismissed one',
        rc == 0 and case(27) not in load(RES) and case(28) not in load(RES) and load(STATUS)['total']['flagged_lead_rows'] == 2, out[-200:])

    # owner 1 has all five of its slots full and owner 2 all five of its: the split is between 5 and 6
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [
        row('Sam', 'Slot', '2900 Nw 29th St', ' 33100-0001', g1=[M(2901 + i) for i in range(5)],
            g2=[M(2906 + i) for i in range(5)], owner2=('Zed', 'Zzzz'))]})
    rec('the split between owner 1 and owner 2 is after slot 5 (and a ZIP+4 with a space still finds the lead): '
        'five numbers land, five are counted as the other owner',
        nums(load(RES), 29) == ['30555529%02d' % i for i in range(1, 6)] and load(STATUS)['total']['numbers_other_owner'] == 5,
        (nums(load(RES), 29), load(STATUS)['total']['numbers_other_owner']))

    # two exports disagree about one number: DNC wins whichever is read first
    for first, second in (('SkipTrace_a.csv', 'SkipTrace_b.csv'), ('SkipTrace_b.csv', 'SkipTrace_a.csv')):
        fresh(CACHE)
        clean = row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[M(101), M(102)])
        dnc = row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[M(102, 'Mobile', True)])
        write_csv(os.path.join(DL, first), [clean])
        write_csv(os.path.join(DL, second), [dnc])
        rc, out = run([])
        rec('two exports disagree, DNC wins whichever sorts first (%s)' % first,
            [(p['number'], p['dnc']) for p in load(RES)[case(1)]['phones']] == [('3055550101', False), ('3055550102', True)],
            load(RES).get(case(1)))

    # a foreign CSV named on the command line is refused
    fresh(CACHE)
    bogus = os.path.join(TMP, 'other.csv')
    write_csv(bogus, [{'a': '1'}], header=['a'])
    snap = open(RES, 'rb').read()
    rc, out = run([bogus])
    rec('non-REsimpli CSV refused with exit 2, cache untouched', rc == 2 and open(RES, 'rb').read() == snap)

    # ---------------------------------------------------------------- a number flagged DNC elsewhere in the cache
    elsewhere = {case(41): {'phones': [{'number': '13055553103', 'type': 'Mobile', 'dnc': True}], 'source': 'tracerfy'}}
    fresh(dict(CACHE, **elsewhere))
    rc, out = run([], files={'SkipTrace_1.csv': [
        row('Carl', 'Carry', '3000 Nw 30th St', '33100', g1=[('3055551001', 'Mobile', False)]),
        row('Cara', 'Carry', '3100 Nw 31st St', '33100', g1=[('3055551001', 'Mobile', False), M(3102), M(3103)])]})
    d, t = load(RES), load(STATUS)['total']
    rec('a number the cache holds as DNC on another lead (however it is spelled) is DNC on this one too, though REsimpli '
        'lists it clean: alone it gives no entry, beside a clean number it lands flagged',
        rc == 0 and case(30) not in d and t['leads_dnc_only_skipped'] == 1 and t['new_numbers'] == 3 and
        [(p['number'], p['dnc']) for p in d[case(31)]['phones']] ==
        [('3055553102', False), ('3055551001', True), ('3055553103', True)] and
        d[case(10)] == CACHE[case(10)] and d[case(41)] == elsewhere[case(41)], (t, d.get(case(31))))

    # ---------------------------------------------------------------- the board bake keeps MAX_PHONES numbers per lead
    was_max = RS.PS.MAX_PHONES
    RS.PS.MAX_PHONES = 7
    try:
        fresh({case(32): {'phones': [{'number': '30555532%02d' % i, 'type': 'Landline', 'dnc': False} for i in range(1, 6)],
                          'source': 'tracerfy'}})
        rc, out = run([], files={'SkipTrace_1.csv': [row('Cass', 'Cap', '3200 Nw 32nd St', '33100',
            g1=[M(3211, 'Landline', True), M(3212), M(3213, 'Landline'), M(3214), M(3215, 'Mobile', True)])]})
        d, t = load(RES), load(STATUS)['total']
        rec('an entry is filled up to the cap and no further, clean mobiles first; what is left out is counted, and a rerun changes nothing',
            rc == 0 and [p['number'] for p in d[case(32)]['phones']][5:] == ['3055553212', '3055553214'] and
            len(d[case(32)]['phones']) == 7 and t['numbers_over_cap'] == 3 and t['new_numbers'] == 2, (t, nums(d, 32)))
        before = open(RES, 'rb').read()
        rc, out = run([])
        rec('a lead already at the cap gets nothing more, and the cache is not rewritten',
            rc == 0 and open(RES, 'rb').read() == before and load(STATUS)['total']['new_numbers'] == 0)
        fresh({case(32): {'phones': [{'number': '30555532%02d' % i, 'type': 'Landline', 'dnc': False} for i in range(1, 10)],
                          'source': 'tracerfy'}})
        before = open(RES, 'rb').read()
        rc, out = run([], files={'SkipTrace_1.csv': [row('Cass', 'Cap', '3200 Nw 32nd St', '33100',
                                                         g1=[M(3212), M(3214), M(3216)])]})
        t = load(STATUS)['total']
        rec('an entry already over the cap gets nothing, and the overflow is counted',
            rc == 0 and open(RES, 'rb').read() == before and t['new_numbers'] == 0 and t['numbers_over_cap'] == 3, t)
    finally:
        RS.PS.MAX_PHONES = was_max

    # ---------------------------------------------------------------- two people on one county-roll line
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [
        row('Jose', 'Garcia', '3400 Nw 34th St', '33100', g1=[M(3401)]),
        row('Maria', 'Garcia', '3400 Nw 34th St', '33100', g1=[M(3402)]),
        row('Wendy', 'West', '3500 Nw 35th St', '33100', g1=[M(3501)]),
        row('Wes', 'West', '3500 Nw 35th St', '33100', g1=[M(3502)])]})
    d, t = load(RES), load(STATUS)['total']
    rec('"PEREZ JOSE & GARCIA MARIA": Maria Garcia is an owner and "Jose Garcia" is a stranger; each person on an oname line is an owner',
        nums(d, 34) == ['3055553402'] and nums(d, 35) == ['3055553501', '3055553502'] and t['owner_mismatch'] == 1, (t, nums(d, 34), nums(d, 35)))

    # ---------------------------------------------------------------- a Jr at the address of a Sr
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [
        row('Jose', 'Perez Jr', '3600 Nw 36th St', '33100', g1=[M(3601)]),
        row('Jose', 'Perez', '3600 Nw 36th St', '33100', g1=[M(3602)])]})
    rec('a Jr at the Sr\'s address is not the owner; the same name with no marker is',
        nums(load(RES), 36) == ['3055553602'] and load(STATUS)['total']['owner_mismatch'] == 1, nums(load(RES), 36))

    # ---------------------------------------------------------------- a lis pendens keyed by name
    fresh({'LP-DOE, DON': {'phones': [{'number': '3055553799', 'type': 'Mobile', 'dnc': False, 'src': 'resimpli'}],
                           'source': 'resimpli'}})
    rc, out = run([], files={'SkipTrace_1.csv': [row('Don', 'Doe', '3700 Nw 37th St', '33100', g1=[M(3701)])]})
    st = load(STATUS)
    t = st['total']
    rec('a lead keyed "LP-<owner name>" gets nothing, and the name never reaches the status file or the console',
        rc == 0 and t['unmatched_rows_with_phone'] == 1 and t['new_numbers'] == 0 and
        [p['number'] for p in load(RES)['LP-DOE, DON']['phones']] == ['3055553799'] and
        (t['resimpli_unconfirmed'], t['resimpli_unconfirmed_leads']) == (1, 1) and st['resimpli_unconfirmed_cases'] == [] and
        not any(x in json.dumps(st) + out for x in ('DOE', 'LP-')), (t, out[-300:]))

    # ---------------------------------------------------------------- a trailing delimiter on the data rows
    fresh(CACHE)
    p = os.path.join(DL, 'SkipTrace_1.csv')
    write_csv(p, [row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[M(101)])])
    lines = open(p, newline='').read().splitlines()
    with open(p, 'w', newline='') as fh:
        fh.write('\r\n'.join([lines[0]] + [ln + ',' for ln in lines[1:]]) + '\r\n')
    rc, out = run([])
    rec('a trailing delimiter on every data row does not stop the run', rc == 0 and nums(load(RES), 1) == ['3055550101'], out[-300:])

    # ---------------------------------------------------------------- the paid DNC lane in tracerfy_mcp.py
    import tracerfy_mcp as TM
    def registry(nat, **kw):
        return dict({'national_dnc': nat, 'state_dnc': False, 'states': [], 'case': 'X', 'checked': '2026-01-01'}, **kw)
    TM._board = lambda: LEADS
    TM._cache = lambda: {case(40): {'phones': [{'number': '30555540%02d' % i, 'type': 'Mobile', 'dnc': False} for i in (1, 2, 3, 4, 5)]}}
    TM._dnc_scrubbed = lambda: {'3055554001': registry(True, source='resimpli'),     # REsimpli's flag: a registry miss must not replace it
                                '3055554002': registry(False),                        # a registry miss past its TTL
                                '3055554003': registry(True),                         # the lane's own hit past its TTL
                                '3055554005': registry(False, source='resimpli')}     # nothing to protect
    rec('tracerfy_mcp dnc lane: a REsimpli verdict is never re-scrubbed; stale registry records and unchecked numbers still are',
        [n for _, n in TM._dnc_targets()] == ['3055554002', '3055554003', '3055554004', '3055554005'], TM._dnc_targets())

    # ---------------------------------------------------------------- the sidecar is checked like the cache
    for label, side0 in (('the sidecar changed while the run was writing (a tracerfy dnc checkpoint)',
                          {'9999999999': {'national_dnc': False, 'state_dnc': False, 'checked': '2026-01-01'}}),
                         ('another writer created the sidecar while the run was writing', None)):
        fresh(CACHE, side=side0)
        write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
        before_c = open(RES, 'rb').read()
        before_s = open(SIDE, 'rb').read() if side0 is not None else None
        def write_then_touch_sidecar(path, obj):
            tmp = real_write_tmp(path, obj)
            if path == SIDE:
                if os.path.exists(SIDE):
                    os.utime(SIDE, ns=(os.stat(SIDE).st_atime_ns, os.stat(SIDE).st_mtime_ns + 5_000_000_000))
                else:
                    with open(SIDE, 'w') as fh:
                        fh.write('{}')
            return tmp
        RS.write_tmp = write_then_touch_sidecar
        try:
            rc, out = run([])
        finally:
            RS.write_tmp = real_write_tmp
        rec(label + ': nothing replaced (exit 3), temps removed, no status, no import copies',
            rc == 3 and 'Nothing was replaced' in out and open(RES, 'rb').read() == before_c
            and (open(SIDE, 'rb').read() == before_s if side0 is not None else open(SIDE).read() == '{}')
            and not any(os.path.exists(x) for x in (RES + '.tmp', TMPR, TMPS)) and not os.path.exists(STATUS)
            and not os.path.exists(os.path.join(DFDIR, 'imports')), out[-300:])

    # the windows before the temp files: the cache changes right after it was read, the sidecar right after
    # it was read (the signature is taken BEFORE each read, so both are seen)
    def changed_after(real_fn, path):
        def wrapper(*a, **k):
            r = real_fn(*a, **k)
            os.utime(path, ns=(os.stat(path).st_atime_ns, os.stat(path).st_mtime_ns + 5_000_000_000))
            return r
        return wrapper
    for label, attr, target in (('the cache changed right after it was read', 'load_cache', RES),
                                ('the sidecar changed right after it was read', 'sidecar_plan', SIDE)):
        fresh(CACHE, side={'9999999999': {'national_dnc': False, 'state_dnc': False, 'checked': '2026-01-01'}})
        write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
        before_c, before_s = open(RES, 'rb').read(), open(SIDE, 'rb').read()
        real_fn = getattr(RS, attr)
        setattr(RS, attr, changed_after(real_fn, target))
        try:
            rc, out = run([])
        finally:
            setattr(RS, attr, real_fn)
        rec(label + ': nothing replaced (exit 3)',
            rc == 3 and 'Nothing was replaced' in out and open(RES, 'rb').read() == before_c and open(SIDE, 'rb').read() == before_s
            and not any(os.path.exists(x) for x in (TMPR, TMPS)), out[-300:])

    # ---------------------------------------------------------------- what a failed write leaves behind
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
    real_replace = os.replace
    def replace_fails_for_cache(src, dst):
        if os.path.abspath(dst) == os.path.abspath(RES):
            raise PermissionError('locked')
        return real_replace(src, dst)
    os.replace = replace_fails_for_cache
    try:
        rc, out = run([])
    finally:
        os.replace = real_replace
    rec('the cache cannot be replaced after the sidecar was: exit 3, says the sidecar landed, cache untouched, '
        'temps removed, no status, no import copies',
        rc == 3 and 'Written before the failure: dnc_scrub.json' in out and load(RES) == CACHE and load(SIDE) is not None
        and not any(os.path.exists(x) for x in (RES + '.tmp', TMPR, TMPS)) and not os.path.exists(STATUS)
        and not os.path.exists(os.path.join(DFDIR, 'imports')), out[-300:])

    fresh(CACHE)
    os.makedirs(DFDIR)
    with open(os.path.join(DFDIR, 'imports'), 'w') as fh:
        fh.write('a file where the imports folder should be')
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('the phone data is written but the import copies and the status file are not: a warning and exit 0, nothing rolled back',
        rc == 0 and 'WARNING: the phone data was written' in out and nums(load(RES), 1) == ['3055550101', '3055550102']
        and load(SIDE) is not None, out[-300:])

    # ---------------------------------------------------------------- flushed before replaced
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
    events = []
    real_fsync, real_replace = os.fsync, os.replace
    os.fsync = lambda fd: (events.append('fsync'), real_fsync(fd))[1]
    os.replace = lambda a, b: (events.append('replace ' + os.path.basename(a) + ' -> ' + os.path.basename(b)), real_replace(a, b))[1]
    try:
        rc, out = run([])
    finally:
        os.fsync, os.replace = real_fsync, real_replace
    rec('both temp files (under this tool\'s own temp name) are flushed to disk before either replaces its target, '
        'the sidecar first, the cache last',
        rc == 0 and events == ['fsync', 'fsync', 'replace dnc_scrub.json.resimpli.tmp -> dnc_scrub.json',
                               'replace skiptrace_results.json.resimpli.tmp -> skiptrace_results.json'], events)

finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
