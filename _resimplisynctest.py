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
  * a lead never ends up with more numbers than the board bake keeps (phone_src.MAX_PHONES), and that
    counts the Whitepages numbers the bake appends after the skip-trace ones: nothing this tool adds
    pushes a number the row already shows off it
  * a line that names two people ('PEREZ JOSE & GARCIA MARIA') is two people; Jr and Sr are two people;
    a company, trust or estate anywhere on a line makes it nobody; a one-word second name borrows only
    the first person's surname
  * a number dnc_scrub.json holds as registry-listed is DNC on the lead it is added to
  * a missing, unreadable or concurrently changed cache or sidecar stops the run before anything is
    replaced; a torn sidecar is left alone, and stops the run when it has anything to flag; a
    failed write says what already landed and leaves no temp file, and a refused run no backup
  * every file is read (no newest-by-mtime pick); a duplicate download counts once
  * a stray text file is skipped by name; an export that cannot be read in full (not UTF-8, a flag
    column missing, another delimiter, an `opt` value that is neither yes nor no) is refused, because
    its DNC flags would be missing from the merge
  * opting out is about the person: a row that names someone another row (in any export) has opted out
    is held like that row, and a line cut short before its `opt` cell is held too
  * ZIP separates two homes with one street address; an error nobody planned for is one line and exit 4
  * every record dnc_scrub.json holds for a number REsimpli flags carries the REsimpli marker, and
    tracerfy_mcp's paid DNC lane does not re-scrub a marked, listed number
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

ok, bad, skips = [], [], []
def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))
def skip(n):
    skips.append(n)
    print('  SKIP ' + n)

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


def lead(n, addr, owners, zipc='33100', **extra):
    d = {'Case #': case(n), 'Address': '%s, MIAMI, FL, %s' % (addr, zipc), 'owners': owners}
    d.update(extra)
    return d


def case(n):
    return '2026-0000%02d-CA-01' % n


RES = os.path.join(TMP, 'skiptrace_results.json')
SIDE = os.path.join(TMP, 'dnc_scrub.json')
WPF = os.path.join(TMP, 'whitepages_lookup.json')
STATUS = os.path.join(DFDIR, 'resimpli_sync_status.json')


def left():
    """Temp files left beside the cache and the sidecar: the '.tmp' skiptrace.py and tracerfy_mcp.py write, and this
    tool's own (<file>.resimpli.<random>.tmp: a name no other run shares)."""
    return [f for f in os.listdir(TMP) if f in ('skiptrace_results.json.tmp', 'dnc_scrub.json.tmp')
            or ('.resimpli.' in f and f.endswith('.tmp'))]


def is_cache_tmp(path):
    """the cache's own temp file of this tool (for the tests that make writing it fail)"""
    b = os.path.basename(path)
    return b.startswith('skiptrace_results.json.resimpli.') and b.endswith('.tmp')
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
    lead(35, '3500 NW 35 ST', 'WEST, WES & WEST, WENDY'),
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


def fresh(cache=None, side=None, wp=None):
    for p in (RES, SIDE, WPF):
        if os.path.exists(p):
            os.remove(p)
    for f in left():
        os.remove(os.path.join(TMP, f))
    shutil.rmtree(DFDIR, ignore_errors=True)
    for f in os.listdir(DL):
        os.remove(os.path.join(DL, f))
    if cache is not None:
        with open(RES, 'w') as f:
            json.dump(cache, f)
    if side is not None:
        with open(SIDE, 'w') as f:
            json.dump(side, f)
    if wp is not None:
        with open(WPF, 'w') as f:
            json.dump(wp, f)


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


@contextlib.contextmanager
def with_leads(extra):
    """The board leads for one scenario: the standing ones plus `extra`."""
    real = S.load_all_leads
    S.load_all_leads = lambda: LEADS + extra
    try:
        yield
    finally:
        S.load_all_leads = real


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
    rec('opt: No / False / N / 0 / blank is not opted out, anything else is; no cell at all (a line cut short) is',
        [RS.opted_out({'opt': v}) for v in ('No', '', 'no', 'FALSE', 'n', '0', ' No ', 'Yes', 'true', '1', 'Opted out')] ==
        [False] * 7 + [True] * 4 and RS.opted_out({}) and RS.opted_out({'opt': None}))
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
    rec('names: a company, trust or estate anywhere on a line makes it nobody, so no co-owner on it is an owner',
        not same('John', 'Smith', 'SMITH JOHN & MARY TR') and not same('Mary', 'Smith', 'SMITH JOHN & MARY TR') and
        not same('John', 'Smith', 'SMITH JOHN TR & SMITH MARY TR') and not same('Jose', 'Perez', 'JOSE PEREZ AND SONS INC') and
        not same('Jose', 'Garcia', 'GARCIA MARIA EST & GARCIA JOSE') and not same('Maria', 'Garcia', 'GARCIA MARIA EST & GARCIA JOSE') and
        RS.owner_people({'owners': 'SMITH JOHN & MARY TR'}) == [] and
        same('John', 'Smith', 'SMITH JOHN & MARY') and same('Jose', 'Perez', 'PEREZ JOSE & GARCIA MARIA H/E') and
        same('Mary', 'Smith', 'SMITH JOHN TR; SMITH MARY'),
        RS.owner_people({'owners': 'SMITH JOHN & MARY TR'}))
    rec('names: a one-word second name borrows the first person\'s surname and nothing else, never into a fragment cut at the roll\'s ceiling',
        same('Mary', 'Thomas', 'THOMAS JAMES & MARY') and same('James', 'Thomas', 'THOMAS JAMES & MARY') and
        not same('Mary', 'James', 'THOMAS JAMES & MARY') and
        len('MARTINEZ JOSE ANTONIO & GARCIA') == 30 and not same('Jose', 'Garcia', 'MARTINEZ JOSE ANTONIO & GARCIA') and
        not same('Antonio', 'Garcia', 'MARTINEZ JOSE ANTONIO & GARCIA') and same('Jose', 'Martinez', 'MARTINEZ JOSE ANTONIO & GARCIA') and
        len('RODRIGUEZ JOSE LUIS & PEREZ M') == 29 and not same('Luis', 'Perez', 'RODRIGUEZ JOSE LUIS & PEREZ M') and
        same('Jose', 'Rodriguez', 'RODRIGUEZ JOSE LUIS & PEREZ M') and
        len('PEREZ JOSE MANUEL ANTONIO & MARIA') >= 30 and not same('Maria', 'Perez', 'PEREZ JOSE MANUEL ANTONIO & MARIA') and
        same('Maria', 'Perez', 'PEREZ JOSE & MARIA'))
    rec('names: the ceiling is exactly 30 characters: a line of 29 is whole, so its one-word second name inherits; a line '
        'of 30 may be cut, so it does not; and only the last part of a cut line is a fragment',
        len('SMITH JOHN ANTHONY & MARYANNE') == 29 and same('Maryanne', 'Smith', 'SMITH JOHN ANTHONY & MARYANNE') and
        len('SMITH JOHN ANTHONY & MARYANNEE') == 30 and not same('Maryannee', 'Smith', 'SMITH JOHN ANTHONY & MARYANNEE') and
        len('SMITH JOHN & MARY & JONES ANTHONY') >= 30 and same('Mary', 'Smith', 'SMITH JOHN & MARY & JONES ANTHONY') and
        not same('Garcia', 'Martinez', 'MARTINEZ JOSE ANTONIO & GARCIA'))
    rec('names: every one-word second name borrows the FIRST person\'s surname, not the previous one\'s',
        len('SMITH JO & JONES AL & MARY') < 30 and same('Mary', 'Smith', 'SMITH JO & JONES AL & MARY') and
        not same('Mary', 'Jones', 'SMITH JO & JONES AL & MARY'))
    rec('name words keep the order written: role suffixes, particles and initials dropped',
        RS.name_words('PEREZ, JOSE L TRS') == ['PEREZ', 'JOSE'] and RS.name_words("O'Connor, Mary") == ['OCONNOR', 'MARY'] and
        RS.name_words('DE LA CRUZ ANA') == ['CRUZ', 'ANA'])
    rec('whitepages numbers: owners, residents and every person-search record count, by digits; odd shapes read as none',
        RS.wp_numbers({'result': {'ownership_info': {'person_owners': [{'phones': [{'number': '(305) 555-0001'},
                                                                                     {'number': '1-305-555-0001'}]}]},
                                  'residents': [{'phones': [{'number': '3055550002'}]}]},
                       '_person': [{'name': 'X', 'response': [{'phones': [{'number': '305.555.0003'}]}]}]}) ==
        {'3055550001', '3055550002', '3055550003'} and
        RS.wp_numbers(None) == set() and RS.wp_numbers('x') == set() and RS.wp_numbers({}) == set() and
        RS.wp_numbers({'result': None, '_person': 'x'}) == set() and
        RS.wp_numbers({'_prop_id': 'abc', '_http': 404}) == set() and
        RS.wp_numbers({'result': {'ownership_info': 'x', 'residents': [None, 'x', {'phones': 'x'},
                                                                        {'phones': [None, 5, {'number': None}, {'number': '123'}]}]},
                       '_person': [None, {'response': 'x'}, {'response': [None, {'phones': None}]}]}) == set())
    rec('registry digits: national or state listed, whatever the spelling of the key, ten digits only',
        RS.registry_digits({'3055550001': {'national_dnc': True}, '13055550002': {'state_dnc': True},
                            '(305) 555-0003': {'national_dnc': True}, '3055550004': {'national_dnc': False, 'state_dnc': False},
                            '3055550005': 'x', '12345': {'national_dnc': True}}) == {'3055550001', '3055550002', '3055550003'} and
        RS.registry_digits(None) == set() and RS.registry_digits({}) == set())
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
    d, side, st = load(RES), (load(SIDE) or {}), load(STATUS)
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
    rec('sidecar entries say where the flag came from, carry the REsimpli marker and set a flag the board bake reads',
        all(v['national_dnc'] is True and v['source'] == 'resimpli' and v['checked'] == TODAY.isoformat()
            and v['resimpli'] == TODAY.isoformat() for v in side.values()))
    rec('the numbers REsimpli says are clean are not recorded', '3055551001' not in side and '3055550101' not in side)

    rec('counts',
        (t['rows'], t['rows_with_phone'], t['unmatched_rows_with_phone'], t['unit_mismatch'],
         t['unit_conflict_same_person'], t['entity_rows'], t['flagged_lead_rows'], t['owner_mismatch'],
         t['opt_rows'], t['matched_rows'], t['numbers_other_owner']) ==
        (29, 29, 2, 4, 3, 1, 1, 5, 1, 15, 2), t)
    rec('every row with a phone falls in exactly one bucket',
        t['rows_with_phone'] == sum(t[k] for k in ('unmatched_rows_with_phone', 'unit_mismatch', 'entity_rows',
                                                  'flagged_lead_rows', 'owner_mismatch', 'opt_rows', 'opt_person_rows',
                                                  'matched_rows')))
    rec('lead and number counts',
        (t['leads_matched'], t['leads_had_phone'], t['leads_new_phone'], t['leads_dnc_only_skipped'],
         t['new_numbers'], t['new_numbers_dnc'], t['new_mobile_clean'], t['entries_unusable']) ==
        (14, 4, 8, 2, 12, 1, 11, 0), t)
    rec('unreadable cells are counted, flags all in the expected vocabulary',
        (t['numbers_unreadable'], t['flags_unexpected']) == (1, 0), t)
    rec('global DNC counts',
        (t['dnc_flagged_numbers'], t['dnc_tightened'], t['dnc_sidecar_added'], t['dnc_sidecar_tightened'],
         t['dnc_sidecar_marked']) == (6, 3, 13, 0, 0), t)
    rec('the status file records how many Whitepages leads were read (none here), counts only', st['whitepages_cases'] == 0)
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
        not left())

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
    side, t = (load(SIDE) or {}), load(STATUS)['total']
    rec('sidecar: an earlier registry miss is tightened; a registry hit (national or state) keeps its verdict and gains the '
        'REsimpli marker; strangers are untouched',
        side['3055550102'] == {'national_dnc': True, 'state_dnc': False, 'states': [], 'case': 'X',
                               'checked': '2026-01-01', 'resimpli': TODAY.isoformat(), 'source': 'resimpli'} and
        side['3055550400'] == {'national_dnc': True, 'state_dnc': False, 'checked': '2026-01-01',
                               'resimpli': TODAY.isoformat()} and
        side['9999999999'] == {'national_dnc': False, 'state_dnc': False, 'checked': '2026-01-01'} and
        side['3055550801'] == {'national_dnc': False, 'state_dnc': True, 'states': ['FL'], 'checked': '2026-01-01',
                               'resimpli': TODAY.isoformat()}, side)
    rec('sidecar: counts', (t['dnc_sidecar_added'], t['dnc_sidecar_tightened'], t['dnc_sidecar_marked']) == (10, 1, 2), t)
    snap = snapshot()
    rc, out = run([])
    rec('sidecar: a rerun changes nothing, marked records included', rc == 0 and
        {k: v for k, v in snapshot().items() if not k.endswith('resimpli_sync_status.json')} ==
        {k: v for k, v in snap.items() if not k.endswith('resimpli_sync_status.json')} and
        load(STATUS)['total']['dnc_sidecar_marked'] == 0, out[-300:])
    rec('sidecar: backed up before it changed',
        any(f.startswith('dnc_scrub.pre-resimpli-') for f in os.listdir(os.path.join(DFDIR, 'backups'))))

    def nothing_written():
        return (load(RES) == CACHE and not os.path.exists(STATUS) and not os.path.exists(os.path.join(DFDIR, 'backups'))
                and not os.path.exists(os.path.join(DFDIR, 'imports')) and not left())
    garbage = b'{"3055550102": {"national_dnc": tru'
    for extra in ([], ['--dry-run']):
        fresh(CACHE)
        open(SIDE, 'wb').write(garbage)
        rc, out = run(extra, files={'SkipTrace_1.csv': ROWS})
        rec('a torn sidecar while the run has numbers to flag: refused, exit 2, everything left as found%s'
            % (' (also on a dry run)' if extra else ''),
            rc == 2 and 'REFUSED: dnc_scrub.json exists but cannot be read' in out and 'numbers to flag as DNC' in out
            and open(SIDE, 'rb').read() == garbage and nothing_written() and 'TOTAL' not in out, out[-500:])
    fay = [row('Fay', 'Flag', '800 Se 8th Ct', '33100', g1=[M(801, 'Mobile', True), M(802)])]     # the cache holds 13055550801, clean
    fresh(CACHE)
    open(SIDE, 'wb').write(garbage)
    rc, out = run([], files={'SkipTrace_1.csv': fay})
    rec('a torn sidecar is refused even when the number to flag is already in the cache: skiptrace.py can replace that '
        'entry, and the flag with it, and the sidecar is what would keep it',
        rc == 2 and open(SIDE, 'rb').read() == garbage and nothing_written(), out[-300:])
    clean1 = [row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[M(101), M(102)])]              # nothing here is flagged
    fresh(CACHE)
    open(SIDE, 'wb').write(garbage)
    rc, out = run([], files={'SkipTrace_1.csv': clean1})
    rec('a torn sidecar when the run has nothing to flag: left exactly as found, said so, and the cache still updates',
        rc == 0 and open(SIDE, 'rb').read() == garbage and 'WARNING: dnc_scrub.json exists but cannot be read' in out
        and load(STATUS)['dnc_scrub_json'] == 'unreadable' and nums(load(RES), 1) == ['3055550101', '3055550102'], out[-400:])
    fresh(CACHE, side=['not', 'a', 'dict'])
    rc, out = run([], files={'SkipTrace_1.csv': fay})
    rec('a sidecar that is not an object is a torn one: refused when there is something to flag',
        rc == 2 and load(SIDE) == ['not', 'a', 'dict'] and nothing_written(), out[-300:])
    fresh(CACHE, side=['not', 'a', 'dict'])
    rc, out = run([], files={'SkipTrace_1.csv': clean1})
    rec('...and left alone when there is nothing to flag',
        rc == 0 and load(SIDE) == ['not', 'a', 'dict'] and nums(load(RES), 1) == ['3055550101', '3055550102'], out[-300:])

    # ---------------------------------------------------------------- the cache
    fresh(CACHE)
    real_leads = S.load_all_leads
    def unreadable_leads():
        raise FileNotFoundError(2, 'No such file or directory', 'leads_final.json')
    S.load_all_leads = unreadable_leads
    try:
        rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    finally:
        S.load_all_leads = real_leads
    rec('no board leads file (wrong folder): refused with exit 2, nothing written, no traceback',
        rc == 2 and 'board leads cannot be read' in out and load(RES) == CACHE and not os.path.exists(SIDE)
        and not os.path.exists(STATUS), out[-300:])

    fresh(CACHE)
    open(WPF, 'w').write('{"2026-000001-CA-01": {"result": [')
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('a torn whitepages_lookup.json is refused: it decides how many numbers a lead can take; nothing written',
        rc == 2 and 'whitepages_lookup.json' in out and 'cannot be read' in out and load(RES) == CACHE and not os.path.exists(SIDE)
        and not os.path.exists(STATUS) and not os.path.exists(os.path.join(DFDIR, 'backups')), out[-300:])
    fresh(CACHE, wp=['a', 'list'])
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('a whitepages_lookup.json that is not an object is refused', rc == 2 and load(RES) == CACHE and not os.path.exists(SIDE), out[-300:])
    fresh(CACHE, wp={case(50): {'_http': 404}})
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('a readable whitepages_lookup.json changes nothing for leads it has no numbers for; the status file counts its leads',
        rc == 0 and nums(load(RES), 1) == ['3055550101', '3055550102'] and load(STATUS)['whitepages_cases'] == 1, out[-300:])

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
    rec('the cache changed while the merge ran: nothing replaced (exit 3), no temp, no sidecar, no backup, no status, '
        'no import copies',
        rc == 3 and 'CHANGED' in out and 'Nothing was replaced' in out and open(RES, 'rb').read() == before
        and not left() and not os.path.exists(SIDE)
        and not os.path.exists(os.path.join(DFDIR, 'backups'))
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
        'no backup, no status, no import copies',
        rc == 3 and 'Nothing was replaced' in out and open(RES, 'rb').read() == before
        and not left() and not os.path.exists(SIDE)
        and not os.path.exists(os.path.join(DFDIR, 'backups'))
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
        and not os.path.exists(SIDE) and not left()
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

    # an export re-saved with another list separator, or with a line above the header, is not "some other CSV":
    # skipping it would drop its DNC flags while an older export lists the same numbers clean
    def write_delimited(path, rows, delimiter=',', pre=None, header=HDR):
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            if pre:
                fh.write(pre + '\r\n')
            w = csv.DictWriter(fh, fieldnames=header, delimiter=delimiter, extrasaction='ignore')
            w.writeheader()
            w.writerows(rows)
    for label, kw in (("';' as the list separator", dict(delimiter=';')), ('tab-delimited', dict(delimiter='\t')),
                      ("a 'sep=,' line above the header", dict(pre='sep=,')),
                      ('lower-cased column names', dict(header=[h.lower() for h in HDR]))):
        fresh(CACHE)
        write_csv(os.path.join(DL, 'SkipTrace_a.csv'), [clean])
        write_delimited(os.path.join(DL, 'SkipTrace_b.csv'), [flags_it], **kw)
        rc, out = run([])
        rec('refused, nothing merged, nothing left behind: an export re-saved with %s next to a good one' % label,
            rc == 2 and 'REFUSED' in out and 'SkipTrace_b.csv' in out and 'column names' in out and 'Move or delete that file' in out
            and load(RES) == CACHE and not os.path.exists(SIDE) and not os.path.exists(STATUS)
            and not os.path.exists(os.path.join(DFDIR, 'imports')) and not os.path.exists(os.path.join(DFDIR, 'backups')), out[-300:])
    for label, hdr in (('only its address column name', ['propertyStreetAddress', 'firstName']), ('only its phone column name', ['Phone_1', 'a'])):
        fresh(CACHE)
        write_csv(os.path.join(DL, 'SkipTrace_a.csv'), [clean])
        write_delimited(os.path.join(DL, 'SkipTrace_b.csv'), [dict.fromkeys(hdr, '1')], delimiter=';', header=hdr)
        rc, out = run([])
        rec('refused, not skipped: a ";"-separated file that shows %s' % label,
            rc == 2 and 'SkipTrace_b.csv' in out and 'column names' in out and load(RES) == CACHE and not os.path.exists(SIDE), out[-300:])
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_a.csv'), [clean])
    write_delimited(os.path.join(DL, 'SkipTrace_c.csv'), [{'a': '1'}], header=['a'])
    rc, out = run([])
    rec('a file with none of REsimpli\'s column names is still just skipped by name', rc == 0 and 'SKIPPED' in out and 'SkipTrace_c.csv' in out
        and nums(load(RES), 1) == ['3055550101'], out[-300:])

    # ---------------------------------------------------------------- the `opt` column is read in plain words or not at all
    one = [row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[M(101)])]
    for val in ('None', 'NULL', 'N/A', '-', 'Unknown', 'nan', 'Maybe', '2'):
        refused('opt_%s.csv' % val.replace('/', '_'), HDR, 'of value in its `opt` column', rows=[dict(one[0], opt=val)])
    refused_dir('an export whose `opt` column says None next to a good one',
                {'SkipTrace_a.csv': ([clean], HDR, 'utf-8'), 'SkipTrace_b.csv': ([dict(clean, opt='None')], HDR, 'utf-8')},
                'SkipTrace_b.csv')
    fresh(CACHE)
    path = os.path.join(TMP, 'opt_num.csv')
    write_csv(path, [dict(one[0], opt='3055550101'), dict(one[0], opt='Zzz'), dict(one[0], opt='Qqq'), dict(one[0], opt='Ppp')])
    rc, out = run([path])
    rec('the refusal names how many kinds of value and shows at most three, never one with a digit in it',
        rc == 2 and '4 kinds of value' in out and "'<number>'" in out and '305555' not in out and 'Zzz' not in out and ', ...' in out,
        out[-300:])
    fresh(CACHE)
    path = os.path.join(TMP, 'opt_long.csv')
    write_csv(path, [dict(one[0], opt='Opted Out Forever And Ever')])
    rc, out = run([path])
    rec('a long odd `opt` value is cut short in the refusal', rc == 2 and "'Opted Out Fo'" in out and 'Forever' not in out, out[-300:])
    good_vals = ('', 'No', 'no', 'NO', 'False', 'FALSE', 'N', 'n', '0', ' No ', 'Yes', 'yes', 'TRUE', 'True', 'Y', '1')
    path = os.path.join(TMP, 'opt_vocab.csv')
    write_csv(path, [dict(one[0], opt=v) for v in good_vals])
    try:
        got, err = RS.read_export(path), None
    except RS.SyncError as e:
        got, err = [], e
    rec('every spelling of yes and no (any case, padded, blank) is read, and read the right way round',
        len(got) == len(good_vals) and [RS.opted_out(x) for x in got] == [False] * 10 + [True] * 6, err)
    # a line cut short before its `opt` cell (here `opt` is the last column) has no answer: it is held
    hdr_last = [h for h in HDR if h != 'opt'] + ['opt']
    fresh(CACHE)
    p_short = os.path.join(DL, 'SkipTrace_1.csv')
    write_csv(p_short, [row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[M(101)]),
                        row('Bo', 'Sample', '200 Nw 20th Ave', '33100', g1=[M(201)])], header=hdr_last)
    text = open(p_short, newline='').read()
    with open(p_short, 'w', newline='') as fh:
        fh.write(text.rstrip('\r\n').rsplit(',', 1)[0] + '\r\n')                # the last line loses its final cell
    rc, out = run([])
    d, t = load(RES), load(STATUS)['total']
    rec('a line cut short before its `opt` cell is opted out, not read as "no": its number is flagged and not merged, '
        'and the complete line above it merges',
        rc == 0 and nums(d, 1) == ['3055550101'] and nums(d, 2) == ['3055550200'] and t['opt_rows'] == 1 and
        '3055550201' in (load(SIDE) or {}) and '3055550101' not in (load(SIDE) or {}), (t, nums(d, 2), out[-200:]))

    # ---------------------------------------------------------------- flags, end to end
    def flagged_row(**kw):
        return row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[dict(n='3055550101', **kw)])
    for label, kw in (('DNC column alone', dict(DNC='Yes')), ('status alone', dict(status='["DNC"]')),
                      ('litigator alone', dict(lit='Yes')), ('DNC column blank', dict(DNC='')),
                      ('DNC column odd', dict(DNC='Maybe')), ('status odd', dict(status='["Wrong Number"]'))):
        fresh(CACHE)
        rc, out = run([], files={'SkipTrace_1.csv': [flagged_row(**kw)]})
        rec('flag read as DNC, so the lead gets no dialable number: ' + label,
            rc == 0 and case(1) not in load(RES) and '3055550101' in (load(SIDE) or {}), out[-200:])
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
        rc == 0 and case(26) not in load(RES) and '3055552601' in (load(SIDE) or {}) and t['leads_dnc_only_skipped'] == 1
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

    # ---------------------------------------------------------------- a number the registry lists
    reg = {'3055553001': {'national_dnc': True, 'state_dnc': False, 'states': [], 'case': 'X', 'checked': '2026-09-01'},
           '13055553101': {'national_dnc': False, 'state_dnc': True, 'states': ['FL'], 'case': 'X', 'checked': '2026-09-01'}}
    other = {case(50): {'phones': [{'number': '3055553001', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'}}
    fresh(dict(CACHE, **other), side=reg)
    rc, out = run([], files={'SkipTrace_1.csv': [
        row('Carl', 'Carry', '3000 Nw 30th St', '33100', g1=[M(3001)]),
        row('Cara', 'Carry', '3100 Nw 31st St', '33100', g1=[M(3101), M(3102)])]})
    d, t, sd = load(RES), load(STATUS)['total'], (load(SIDE) or {})
    rec('a number the registry lists is DNC on the lead it is added to whatever REsimpli says: alone it gives no entry '
        '(skiptrace can still trace the lead), beside a clean number it lands flagged, last',
        rc == 0 and case(30) not in d and t['leads_dnc_only_skipped'] == 1 and
        [(p['number'], p['dnc']) for p in d[case(31)]['phones']] == [('3055553102', False), ('3055553101', True)], (t, d.get(case(31))))
    rec('...and nothing else changes: another lead holding the number as clean is left to the bake, and the registry records '
        'get no REsimpli marker, since REsimpli did not flag them',
        d[case(50)] == other[case(50)] and sd == reg and t['dnc_tightened'] == 0 and t['dnc_sidecar_added'] == 0, (t, sd))

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
        # the bake appends a lead's Whitepages numbers after its skip-trace numbers and cuts the row again, and the
        # person-level opt-out checks read that row: what this tool adds must leave room for them
        four = {case(32): {'phones': [{'number': '30555532%02d' % i, 'type': 'Landline', 'dnc': False} for i in range(1, 5)],
                           'source': 'tracerfy'}}
        cand = [row('Cass', 'Cap', '3200 Nw 32nd St', '33100', g1=[M(3212), M(3214), M(3216)])]
        def wp_rec(owner=(), resident=(), person=()):
            return {'result': {'ownership_info': {'person_owners': [{'name': 'X', 'phones': [{'number': n, 'type': 'MOBILE'} for n in owner]}]},
                               'residents': [{'name': 'Y', 'phones': [{'number': n} for n in resident]}]},
                    '_person': [{'name': 'Z', 'response': [{'phones': [{'number': n} for n in person]}]}]}
        def union(d, wp):
            return {RS.norm_number(p['number']) for p in d[case(32)]['phones']} | RS.wp_numbers(wp.get(case(32)))
        wp3 = {case(32): wp_rec(owner=('305-555-9901',), resident=('3055559902',), person=('13055559903',))}
        fresh(four, wp=wp3)
        before = open(RES, 'rb').read()
        rc, out = run([], files={'SkipTrace_1.csv': cand})
        t = load(STATUS)['total']
        rec('whitepages numbers fill the row: 4 cache numbers plus 3 Whitepages (owner, resident, person search) is MAX_PHONES, '
            'so nothing is added, the cache is not rewritten, and the held numbers are counted',
            rc == 0 and open(RES, 'rb').read() == before and t['new_numbers'] == 0 and t['numbers_over_cap'] == 3, t)
        wp1 = {case(32): wp_rec(owner=('3055559901',))}
        fresh(four, wp=wp1)
        rc, out = run([], files={'SkipTrace_1.csv': cand})
        d, t = load(RES), load(STATUS)['total']
        rec('one Whitepages number leaves room for two: clean mobiles first, the third is counted, and the cache and '
            'Whitepages numbers together are exactly MAX_PHONES',
            rc == 0 and nums(d, 32)[4:] == ['3055553212', '3055553214'] and t['new_numbers'] == 2 and t['numbers_over_cap'] == 1
            and len(union(d, wp1)) == 7, (t, nums(d, 32)))
        wpdup = {case(32): wp_rec(owner=('3055553201', '3055559901'))}          # one of them is already a cache number
        fresh(four, wp=wpdup)
        rc, out = run([], files={'SkipTrace_1.csv': cand})
        d, t = load(RES), load(STATUS)['total']
        rec('a Whitepages number the cache already holds is not counted twice',
            rc == 0 and t['new_numbers'] == 2 and len(union(d, wpdup)) == 7, (t, nums(d, 32)))
        wpp = {case(32): wp_rec(person=('3055559901', '3055559902', '3055559903', '3055559904'))}
        fresh(four, wp=wpp)
        rc, out = run([], files={'SkipTrace_1.csv': cand})
        rec('person-search numbers count too, whether or not the bake would keep them (over-counting only leaves room)',
            rc == 0 and load(STATUS)['total']['new_numbers'] == 0 and load(RES) == four, out[-200:])
        fresh(four, wp={case(99): wp_rec(owner=('3055559901',), resident=('3055559902',), person=('3055559903',))})
        rc, out = run([], files={'SkipTrace_1.csv': cand})
        rec('another lead\'s Whitepages numbers do not take this lead\'s room', rc == 0 and load(STATUS)['total']['new_numbers'] == 3)
    finally:
        RS.PS.MAX_PHONES = was_max

    # the same at the real MAX_PHONES: 6 skip-trace numbers, 1 Whitepages number, and 4 clean REsimpli mobiles is one too many
    room = RS.PS.MAX_PHONES
    fresh({case(32): {'phones': [{'number': '30555532%02d' % i, 'type': 'Landline', 'dnc': False} for i in range(1, room - 3)],
                      'source': 'tracerfy'}},
          wp={case(32): {'result': {'ownership_info': {'person_owners': [{'phones': [{'number': '3055559901'}]}]}}}})
    rc, out = run([], files={'SkipTrace_1.csv': [row('Cass', 'Cap', '3200 Nw 32nd St', '33100',
                                                      g1=[M(3212), M(3214), M(3216), M(3218)])]})
    d, t = load(RES), load(STATUS)['total']
    rec('with the real cap: a lead with MAX-4 skip-trace numbers and a Whitepages number takes 3 of 4 clean mobiles, so the '
        'Whitepages number stays on the baked row',
        rc == 0 and len(d[case(32)]['phones']) == room - 1 and t['new_numbers'] == 3 and t['numbers_over_cap'] == 1, (t, len(d[case(32)]['phones'])))

    # ---------------------------------------------------------------- two people on one county-roll line
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [
        row('Jose', 'Garcia', '3400 Nw 34th St', '33100', g1=[M(3401)]),
        row('Maria', 'Garcia', '3400 Nw 34th St', '33100', g1=[M(3402)]),
        row('Wendy', 'West', '3500 Nw 35th St', '33100', g1=[M(3501)]),
        row('Wes', 'West', '3500 Nw 35th St', '33100', g1=[M(3502)])]})
    d, t = load(RES), load(STATUS)['total']
    rec('"PEREZ JOSE & GARCIA MARIA": Maria Garcia is an owner and "Jose Garcia" is a stranger; each person on a comma-form line is an owner',
        nums(d, 34) == ['3055553402'] and nums(d, 35) == ['3055553501', '3055553502'] and t['owner_mismatch'] == 1, (t, nums(d, 34), nums(d, 35)))

    # ---------------------------------------------------------------- opting out is about the person, not the row
    ol = [lead(43, '4300 NW 43 ST', 'OPTED, OLGA'),
          lead(44, '4400 NW 44 ST', 'OPTED, OLGA; OPTED, OMAR'),
          lead(45, '4500 NW 45 ST', 'PERSON, PAT')]
    oa = row('Olga', 'Opted', '4300 Nw 43rd St', '33100', g1=[M(4301)], owner2=('Pat', 'Person'), opt='Yes')   # her co-owner is out too
    ob = row('Olga', 'Opted', '4400 Nw 44th St', '33100', g1=[M(4401), M(4402)])                     # her other property, opt blank there
    oc = row('Omar', 'Opted', '4400 Nw 44th St', '33100', g1=[M(4403)])                              # someone else at that address
    od = row('', 'Big Corp LLC', '4400 Nw 44th St', '33100', g2=[M(4404)], owner2=('Olga', 'Opted'))  # a company, Olga behind it
    oe = row('Pat', 'Person', '4500 Nw 45th St', '33100', g1=[M(4501)])                              # the co-owner of the opted-out row
    ope = RS.opt_people_of([('f', 'h', [oa])])
    def held(r, people=None):
        return RS.row_opted_out(r, RS.row_people(r), ope if people is None else people)
    jr = RS.opt_people_of([('f', 'h', [row('Jose', 'Perez Jr', '1 A St', '33100', g1=[M(1)], opt='Yes')])])
    rec('opt person: a Jr opted out does not hold a Sr, nor the other way round',
        held(row('Jose', 'Perez Jr', '2 A St', '33100', g1=[M(2)]), jr) and not held(row('Jose', 'Perez Sr', '2 A St', '33100', g1=[M(2)]), jr) and
        not held(row('Jose', 'Perez Jr', '2 A St', '33100', g1=[M(2)]),
                 RS.opt_people_of([('f', 'h', [row('Jose', 'Perez Sr', '1 A St', '33100', g1=[M(1)], opt='Yes')])])))
    rec('opt person: the person on an opted-out row, and her co-owner on that row, are held wherever else they are named, in '
        'either owner slot; a relative, a namesake with another surname, and a company with nobody behind it are not',
        len(ope) == 2 and held(oa) and held(ob) and held(od) and held(oe) and not held(oc) and
        not held(row('Olga', 'Other', '1 A St', '33100', g1=[M(1)])) and not held(row('Rita', 'Opted', '1 A St', '33100', g1=[M(1)])) and
        not held(row('', 'Acme Holdings LLC', '1 A St', '33100', g1=[M(1)])) and
        RS.flagged_numbers([ob], ope) == {'3055554401', '3055554402'} and RS.flagged_numbers([ob]) == set() and
        RS.opt_people_of([]) == [])
    for label, files in (('one export', {'SkipTrace_1.csv': [oa, ob, oc, od, oe]}),
                         ('the opted-out row in the older export', {'SkipTrace_a.csv': [oa], 'SkipTrace_b.csv': [ob, oc, od, oe]}),
                         ('the opted-out row in the newer export', {'SkipTrace_a.csv': [ob, oc, od, oe], 'SkipTrace_b.csv': [oa]})):
        with with_leads(ol):
            fresh(CACHE)
            rc, out = run([], files=files)
            d, t, sd = load(RES), load(STATUS)['total'], (load(SIDE) or {})
        rec('opt person, %s: the same person under another property is held and her numbers flagged; her opted-out row '
            'is held; another owner of that lead still merges' % label,
            rc == 0 and case(43) not in d and nums(d, 44) == ['3055554403'] and
            case(45) not in d and (t['opt_rows'], t['opt_person_rows'], t['matched_rows']) == (1, 3, 1) and
            {'3055554301', '3055554401', '3055554402', '3055554404', '3055554501'} <= set(sd) and '3055554403' not in sd and
            t['rows_with_phone'] == sum(t[k] for k in ('unmatched_rows_with_phone', 'unit_mismatch', 'entity_rows',
                                                      'flagged_lead_rows', 'owner_mismatch', 'opt_rows', 'opt_person_rows',
                                                      'matched_rows')), (t, sorted(sd), nums(d, 44)))

    # ---------------------------------------------------------------- the ZIP separates two homes with one street address
    zl = [lead(38, '12345 NW 45 ST', 'FIVE, FRED'),                                # a five-digit house number is not a ZIP
          lead(39, '3900 NW 39 ST', 'ZIP, ZED', zipc='33101'),
          lead(40, '3900 NW 39 ST', 'ZIP, ZED', zipc='33102')]
    with with_leads(zl):
        fresh(CACHE)
        rc, out = run([], files={'SkipTrace_1.csv': [
            row('Fred', 'Five', '12345 Nw 45th St', '33100', g1=[M(3801)]),
            row('Zed', 'Zip', '3900 Nw 39th St', '33101', g1=[M(3901)]),
            row('Zed', 'Zip', '3900 Nw 39th St', '33102', g1=[M(4001)]),
            row('Zed', 'Zip', '3900 Nw 39th St', '33103', g1=[M(4101)])]})
        d, t = load(RES), load(STATUS)['total']
    rec('the same street address in two ZIPs is two homes: each row reaches the lead in its own ZIP, a ZIP with no lead '
        'is unmatched, and a five-digit house number is not read as the lead\'s ZIP',
        rc == 0 and nums(d, 38) == ['3055553801'] and nums(d, 39) == ['3055553901'] and nums(d, 40) == ['3055554001'] and
        t['unmatched_rows_with_phone'] == 1 and '3055554101' not in json.dumps(d), (t, sorted(d)))

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
    TM._cache = lambda: {case(40): {'phones': [{'number': '30555540%02d' % i, 'type': 'Mobile', 'dnc': False} for i in (1, 2, 3, 4, 5, 6)]}}
    TM._dnc_scrubbed = lambda: {'3055554001': registry(True, source='resimpli'),     # a record the first version of this tool wrote
                                '3055554002': registry(False),                        # a registry miss past its TTL
                                '3055554003': registry(True),                         # the lane's own hit past its TTL
                                '3055554005': registry(False, source='resimpli'),     # nothing to protect
                                '3055554006': registry(True, resimpli='2026-09-29')}  # the lane's own hit, marked by this tool
    rec('tracerfy_mcp dnc lane: a REsimpli verdict, marked or tagged, is never re-scrubbed; stale registry records and '
        'unchecked numbers still are',
        [n for _, n in TM._dnc_targets()] == ['3055554002', '3055554003', '3055554004', '3055554005'], TM._dnc_targets())
    fresh(CACHE, side={'3055550400': registry(True), '3055559999': registry(True)})    # two lane hits, past their 30 days
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    disk = load(SIDE)
    TM._dnc_scrubbed = lambda: disk
    TM._cache = lambda: {case(60): {'phones': [{'number': '3055550400', 'type': 'Mobile', 'dnc': False},     # skiptrace traced them again,
                                                {'number': '3055559999', 'type': 'Mobile', 'dnc': False}]}}  # its modeled flag says clean
    rec('end to end: a number the lane already listed and REsimpli then flags is not re-scrubbed after 30 days (a registry miss '
        'would replace the flag); a lane hit REsimpli did not flag still is',
        rc == 0 and disk['3055550400'].get('resimpli') == TODAY.isoformat() and disk['3055550400']['national_dnc'] is True
        and 'resimpli' not in disk['3055559999'] and [n for _, n in TM._dnc_targets()] == ['3055559999'], TM._dnc_targets())

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
        rec(label + ': nothing replaced (exit 3), temps removed, no backup, no status, no import copies',
            rc == 3 and 'Nothing was replaced' in out and open(RES, 'rb').read() == before_c
            and (open(SIDE, 'rb').read() == before_s if side0 is not None else open(SIDE).read() == '{}')
            and not left() and not os.path.exists(STATUS)
            and not os.path.exists(os.path.join(DFDIR, 'backups'))
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
                                ('the sidecar changed right after it was read', 'load_sidecar', SIDE)):
        fresh(CACHE, side={'9999999999': {'national_dnc': False, 'state_dnc': False, 'checked': '2026-01-01'}})
        write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
        before_c, before_s = open(RES, 'rb').read(), open(SIDE, 'rb').read()
        real_fn = getattr(RS, attr)
        setattr(RS, attr, changed_after(real_fn, target))
        try:
            rc, out = run([])
        finally:
            setattr(RS, attr, real_fn)
        rec(label + ': nothing replaced (exit 3), no temp, no backup',
            rc == 3 and 'Nothing was replaced' in out and open(RES, 'rb').read() == before_c and open(SIDE, 'rb').read() == before_s
            and not left() and not os.path.exists(os.path.join(DFDIR, 'backups')), out[-300:])

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
        and not left() and not os.path.exists(STATUS)
        and not os.path.exists(os.path.join(DFDIR, 'imports')), out[-300:])

    # a disk that fills while the cache temp file is written leaves no half-written phone file behind, and neither temp file
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
    real_dump = json.dump
    def dump_fails_for_cache(obj, fh, **k):
        if is_cache_tmp(fh.name):
            fh.write('{"partial": [')
            raise OSError(28, 'No space left on device')
        return real_dump(obj, fh, **k)
    json.dump = dump_fails_for_cache
    try:
        rc, out = run([])
    finally:
        json.dump = real_dump
    rec('the disk fills while the cache temp file is written: exit 3, both temp files gone, cache untouched, no sidecar, '
        'no backup, no status',
        rc == 3 and 'FAILED: could not write' in out and 'Written before the failure: nothing' in out and load(RES) == CACHE
        and not os.path.exists(SIDE) and not left()
        and not os.path.exists(os.path.join(DFDIR, 'backups')) and not os.path.exists(STATUS), out[-300:])
    def dump_raises_type_error(obj, fh, **k):
        if is_cache_tmp(fh.name):
            raise TypeError('boom')
        return real_dump(obj, fh, **k)
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
    json.dump = dump_raises_type_error
    raised = False
    try:
        run([])
    except TypeError:
        raised = True
    finally:
        json.dump = real_dump
    rec('any other error while the temp files are written also leaves none behind (and is not swallowed)',
        raised and not left() and load(RES) == CACHE and not os.path.exists(SIDE))

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
    import re as _re
    rec('both temp files (each under a temp name of this tool\'s own that no other run shares) are flushed to disk before '
        'either replaces its target, the sidecar first, the cache last',
        rc == 0 and len(events) == 4 and events[:2] == ['fsync', 'fsync'] and
        _re.fullmatch(r'replace dnc_scrub\.json\.resimpli\.[0-9a-f]{8}\.tmp -> dnc_scrub\.json', events[2]) and
        _re.fullmatch(r'replace skiptrace_results\.json\.resimpli\.[0-9a-f]{8}\.tmp -> skiptrace_results\.json', events[3]), events)

    # ---------------------------------------------------------------- line endings, a header csv cannot parse, glob characters
    fresh(CACHE)
    p_cr = os.path.join(DL, 'SkipTrace_1.csv')
    write_csv(p_cr, ROWS[:1])
    text = open(p_cr, newline='').read()
    with open(p_cr, 'w', newline='') as fh:
        fh.write(text.replace('\r\n', '\r'))
    rc, out = run([])
    rec('a CSV with old-Mac (CR-only) line endings is read in full, not a traceback',
        rc == 0 and nums(load(RES), 1) == ['3055550101', '3055550102'] and [p['dnc'] for p in load(RES)[case(1)]['phones']] == [False, True], out[-300:])
    fresh(CACHE)
    p_huge = os.path.join(TMP, 'huge.csv')
    with open(p_huge, 'w') as fh:
        fh.write('x' * 200000 + '\n' + 'a\n')
    rc, out = run([p_huge])
    rec('a header line csv cannot parse is refused by name, not a traceback',
        rc == 2 and 'huge.csv' in out and 'cannot be read as CSV' in out and load(RES) == CACHE, out[-300:])

    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_a.csv'), [clean])
    with open(os.path.join(DL, 'SkipTrace_b.csv'), 'w') as fh:
        fh.write('x' * 200000 + '\n')
    rc, out = run([])
    rec('...and in discovery mode it stops the run too: it cannot be shown to be "not an export"',
        rc == 2 and 'SkipTrace_b.csv' in out and 'Move or delete that file' in out and load(RES) == CACHE and not os.path.exists(SIDE), out[-300:])

    real_main, real_merge = RS.main, RS.merge
    def cli_out(argv=()):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return RS.cli(list(argv)), buf.getvalue()
    def main_raises(argv=None):
        raise KeyError('PEREZ JOSE')
    RS.main = main_raises
    try:
        rc, out = cli_out()
    finally:
        RS.main = real_main
    rec('an error nothing anticipated ends as one line and exit 4: its type and where, never its message (it can be a name)',
        rc == 4 and out.startswith('FAILED: unexpected error (KeyError at _resimplisynctest.py:') and 'PEREZ' not in out
        and out.count('\n') == 1, out)
    def main_exits(argv=None):
        sys.exit(7)
    RS.main = main_exits
    try:
        try:
            RS.cli([])
            code = None
        except SystemExit as e:
            code = e.code
    finally:
        RS.main = real_main
    rec('a SystemExit passes through cli untouched', code == 7)
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
    rc, out = cli_out(['--dry-run'])
    rec('cli returns main\'s own exit code', rc == 0 and 'DRY RUN - nothing written' in out and not os.path.exists(SIDE), out[-200:])
    def merge_raises(*a, **k):
        raise RuntimeError('boom')
    RS.merge = merge_raises
    try:
        rc, out = cli_out()
    finally:
        RS.merge = real_merge
    rec('an unexpected error in the middle of a real run: exit 4 and nothing written',
        rc == 4 and 'FAILED: unexpected error (RuntimeError at' in out and load(RES) == CACHE and not os.path.exists(SIDE)
        and not left() and not os.path.exists(STATUS), out[-300:])
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
    def dump_raises_type_error2(obj, fh, **k):
        if is_cache_tmp(fh.name):
            raise TypeError('boom')
        return real_dump(obj, fh, **k)
    json.dump = dump_raises_type_error2
    try:
        rc, out = cli_out()
    finally:
        json.dump = real_dump
    rec('an unexpected error while the temp files are written: exit 4, and the temp files are still removed',
        rc == 4 and 'FAILED: unexpected error (TypeError at' in out and load(RES) == CACHE and not os.path.exists(SIDE)
        and not left(), out[-300:])

    # run as a script: the entry point is cli(), so the same error is exit 4 there too
    import runpy
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_1.csv'), ROWS)
    real_lal, real_argv = S.load_all_leads, sys.argv
    def leads_raise():
        raise RuntimeError('boom')
    S.load_all_leads = leads_raise
    sys.argv = ['resimpli_sync.py']
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            runpy.run_path(str(HERE / 'resimpli_sync.py'), run_name='__main__')
        code = None
    except SystemExit as e:
        code = e.code
    finally:
        S.load_all_leads, sys.argv = real_lal, real_argv
    rec('run as a script, an unexpected error is exit 4 with one line',
        code == 4 and buf.getvalue().startswith('FAILED: unexpected error (RuntimeError at') and load(RES) == CACHE, (code, buf.getvalue()[-200:]))

    real_home = os.environ['HOME']
    weird = os.path.join(TMP, 'ho[me]')
    weird_imports = os.path.join(TMP, 'DEAL[FLOW]', 'imports', 'resimpli')
    os.makedirs(os.path.join(weird, 'Downloads'))
    os.makedirs(weird_imports)
    open(os.path.join(weird, 'Downloads', 'SkipTrace_9.csv'), 'w').write('a\n')
    open(os.path.join(weird_imports, 'abcd1234_SkipTrace_old.csv'), 'w').write('a\n')
    os.environ['HOME'] = weird
    try:
        found = RS.discover(weird_imports)
    finally:
        os.environ['HOME'] = real_home
    rec('a profile folder or DEALFLOW folder with [ ] in its name is a folder name, not a pattern',
        [os.path.basename(f) for f in found] == ['SkipTrace_9.csv', 'abcd1234_SkipTrace_old.csv'] or
        sorted(os.path.basename(f) for f in found) == ['SkipTrace_9.csv', 'abcd1234_SkipTrace_old.csv'], found)


    # ================================================================ what the third round of review found
    import random as _random
    import re as _re
    import time as _time

    # ---------------------------------------------------------------- a lead's owners are its raw owner line, nothing derived from it
    # The pipeline stores copies of that line on every lead: owner_clean (Miami, foreclosure_leads.qualify) and oname / rname
    # (county rolls and lis pendens, county_leads._clean_owner / _rec_name). They drop the Jr / Sr, trust and company words,
    # drop everything after an '&', or weld two people into one name. These are the real derivations, written out.
    DERIVED = {
        'PEREZ, JOSE SR': dict(owner_clean='JOSE PEREZ', oname='JOSE PEREZ', rname='PEREZ, JOSE'),
        'PEREZ JOSE SR': dict(owner_clean='PEREZ JOSE', oname='PEREZ JOSE', rname='PEREZ, JOSE'),
        'SMITH JOHN & MARY TR': dict(owner_clean='SMITH JOHN & MARY TR', oname='SMITH JOHN & MARY TR', rname='SMITH, JOHN'),
        'PEREZ JOSE & SONS INC': dict(owner_clean='PEREZ JOSE & SONS INC', oname='PEREZ JOSE & SONS INC', rname='PEREZ, JOSE'),
        'GARCIA,MARIA & PEREZ,JOSE': dict(owner_clean='MARIA & PEREZ,JOSE GARCIA', oname='MARIA & PEREZ,JOSE GARCIA',
                                          rname='GARCIA, MARIA'),
        'PEREZ,JOSE MANUEL ANTONIO & MARIA': dict(owner_clean='JOSE MANUEL ANTONIO & MARIA PEREZ',
                                                  oname='JOSE MANUEL ANTONIO & MARIA PEREZ', rname='PEREZ, JOSE MANUEL ANTONIO'),
    }
    try:
        import types as _types
        sys.modules.setdefault('playwright', _types.ModuleType('playwright'))
        _api = _types.ModuleType('playwright.sync_api')
        _api.sync_playwright = None
        sys.modules.setdefault('playwright.sync_api', _api)
        import county_leads as CL
        _real_derivations = True
    except Exception as e:                                # the cross-check needs the pipeline's own module
        _real_derivations, _why = False, type(e).__name__
    if _real_derivations:
        rec('the derived owner fields above are what county_leads derives today (or this test has drifted from the pipeline)',
            all(CL._clean_owner(raw) == d['oname'] and CL._rec_name(raw) == d['rname'] for raw, d in DERIVED.items()),
            [(raw, CL._clean_owner(raw), CL._rec_name(raw)) for raw in DERIVED])
    else:
        skip('the derived owner fields were not checked against county_leads (%s)' % _why)

    dl = [lead(80 + i, '%d NW %d ST' % ((80 + i) * 100, 80 + i), raw, **d) for i, (raw, d) in enumerate(DERIVED.items())]
    dl.append(lead(86, '8600 NW 86 ST', 'PEREZ, JOSE SR', **DERIVED['PEREZ, JOSE SR']))
    def dst(n):
        return '%d Nw %d%s St' % (n * 100, n, 'th')
    with with_leads(dl):
        fresh(CACHE)
        rc, out = run([], files={'SkipTrace_1.csv': [
            row('Jose', 'Perez Jr', dst(80), '33100', g1=[M(8001)]),           # a Jr at a Sr's address (owner_clean drops SR)
            row('Jose', 'Perez Jr', dst(81), '33100', g1=[M(8101)]),
            row('John', 'Smith', dst(82), '33100', g1=[M(8201)]),              # a trust line (rname keeps only JOHN)
            row('Jose', 'Perez', dst(83), '33100', g1=[M(8301)]),              # 'PEREZ JOSE & SONS INC' (rname keeps only JOSE)
            row('Jose', 'Garcia', dst(84), '33100', g1=[M(8401)]),             # neither owner (oname welds the line)
            row('Maria', 'Garcia', dst(84), '33100', g1=[M(8402)]),            # an owner
            row('Jose', 'Perez', dst(84), '33100', g1=[M(8403)]),              # the other owner
            row('Maria', 'Perez', dst(85), '33100', g1=[M(8501)]),             # a line the roll cut inside 'MARIA'
            row('Jose', 'Perez Sr', dst(86), '33100', g1=[M(8601)])]})         # the Sr himself, through the raw line
        d, t = load(RES), load(STATUS)['total']
    rec('owners are read from the raw line only: a Jr at a Sr\'s address, a trust, a company, a welded two-person line and a cut '
        'fragment are not owners, whatever the derived copies on the lead say',
        rc == 0 and all(case(n) not in d for n in (80, 81, 82, 83, 85)) and nums(d, 84) == ['3055558402', '3055558403'] and
        nums(d, 86) == ['3055558601'] and t['owner_mismatch'] == 6 and t['matched_rows'] == 3,
        (t['owner_mismatch'], t['matched_rows'], sorted(d)))
    rec('owner_people reads owners / owner / Owner and nothing else',
        RS.owner_people({'owner_clean': 'JOSE PEREZ', 'oname': 'JOSE PEREZ', 'rname': 'PEREZ, JOSE'}) == [] and
        RS.owner_people({'owner': 'PEREZ, JOSE'}) == RS.owner_people({'Owner': 'PEREZ, JOSE'}) == RS.owner_people({'owners': 'PEREZ, JOSE'})
        and len(RS.owner_people({'owners': 'PEREZ, JOSE'})) == 1)

    # ---------------------------------------------------------------- naming one file does not narrow who is held
    with with_leads(ol):
        fresh(CACHE)
        rc, out = run([], files={'SkipTrace_a.csv': [oa]})                    # run 1: the export that opts Olga out is kept
        os.remove(os.path.join(DL, 'SkipTrace_a.csv'))
        pb = os.path.join(TMP, 'named_b.csv')
        write_csv(pb, [ob, oc, od, oe])
        rc, out = run([pb])                                                   # run 2: one file, named, from another folder
        d, t, sd, st = load(RES), load(STATUS)['total'], (load(SIDE) or {}), load(STATUS)
    rec('one-file mode reads the kept copies too: the person an earlier export opted out is held under another property, '
        'her numbers are flagged, and another owner of that lead still merges',
        rc == 0 and nums(d, 44) == ['3055554403'] and case(43) not in d and case(45) not in d and
        (t['opt_rows'], t['opt_person_rows'], t['matched_rows']) == (1, 3, 1) and
        {'3055554301', '3055554401', '3055554402', '3055554404', '3055554501'} <= set(sd) and '3055554403' not in sd,
        (t, nums(d, 44)))
    rec('the run says how many people it holds and from how many exports; the status file counts them and lists the leads '
        'a person-level hold kept a row off (case numbers only)',
        'opt-out hold: 2 people, from 2 exports' in out and t['opt_people_held'] == 2 and
        st['opt_person_cases'] == [case(44), case(45)] and not any(x in json.dumps(st) for x in ('Olga', 'OPTED', 'Pat')), (out[-500:], st['opt_person_cases']))
    imp_dir = os.path.join(DFDIR, 'imports', 'resimpli')
    copy_a = [os.path.join(imp_dir, f) for f in sorted(os.listdir(imp_dir)) if f.endswith('SkipTrace_a.csv')][0]
    with with_leads(ol):
        rc, out = run([copy_a])
    rec('naming a kept copy itself reads it once, with the others', rc == 0 and len(load(STATUS)['files']) == 2 and
        load(STATUS)['total']['opt_person_rows'] == 3 and 'from 2 exports (2 files found)' in out, out[-300:])
    # ... and the audit sees what those earlier exports attached
    fresh({})
    rc, out = run([], files={'SkipTrace_a.csv': [row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[M(101)])]})
    os.remove(os.path.join(DL, 'SkipTrace_a.csv'))
    pc = os.path.join(TMP, 'named_c.csv')
    write_csv(pc, [row('Bo', 'Sample', '200 Nw 20th Ave', '33100', g1=[M(201)])])
    rc, out = run([pc])
    t = load(STATUS)['total']
    rec('one-file mode does not call an earlier export\'s numbers unconfirmed',
        rc == 0 and (t['resimpli_unconfirmed'], t['cache_resimpli_numbers']) == (0, 2) and 'NOTE' not in out and
        nums(load(RES), 1) == ['3055550101'] and nums(load(RES), 2) == ['3055550201'], (t, out[-300:]))
    # a dry run reads the kept copies too and writes nothing
    before = snapshot()
    rc, out = run(['--dry-run', pc])
    rec('a dry run with a named file reads the kept copies and writes nothing', rc == 0 and 'DRY RUN' in out and
        'from 2 exports (3 files found)' in out and snapshot() == before, out[-300:])
    # a kept copy that cannot be read stops a one-file run too, and the message says where it is
    fresh({})
    os.makedirs(imp_dir)
    write_csv(os.path.join(imp_dir, 'abcd1234_SkipTrace_bad.csv'), [flags_it], encoding='cp1252')       # 'José Núñez': not UTF-8
    rc, out = run([pc])
    rec('an unreadable kept copy refuses a one-file run and names the folder it is in',
        rc == 2 and 'abcd1234_SkipTrace_bad.csv' in out and imp_dir in out and 'Deleting it forgets them' in out and
        'Put the original export back over it' in out and 'Move or delete that file' not in out and load(RES) == {}, out[-500:])
    fresh({})
    write_csv(os.path.join(DL, 'SkipTrace_bad.csv'), [flags_it], encoding='cp1252')
    write_csv(os.path.join(DL, 'SkipTrace_ok.csv'), one)
    rc, out = run([])
    rec('...and a refused file found by discovery says which folder it is in', rc == 2 and DL in out and 'SkipTrace_bad.csv' in out, out[-400:])

    # ---------------------------------------------------------------- the kept copies are found in a folder with [ ] in its name
    import paths as _P
    real_dfdir, weird_df = _P.DEALFLOW_DIR, os.path.join(TMP, 'DEAL[KEPT]')
    _P.DEALFLOW_DIR = weird_df
    try:
        fresh(CACHE)
        os.makedirs(os.path.join(weird_df, 'imports', 'resimpli'))
        write_csv(os.path.join(weird_df, 'imports', 'resimpli', 'abcd1234_SkipTrace_old.csv'), one)      # a kept copy: Ana
        pw = os.path.join(TMP, 'named_w.csv')
        write_csv(pw, [row('Bo', 'Sample', '200 Nw 20th Ave', '33100', g1=[M(201)])])
        rc, out = run([pw])
        wst = load(os.path.join(weird_df, 'resimpli_sync_status.json'))
    finally:
        _P.DEALFLOW_DIR = real_dfdir
        shutil.rmtree(weird_df, ignore_errors=True)
    rec('one-file mode finds the kept copies in a DEALFLOW folder with [ ] in its name: a folder name, not a pattern',
        rc == 0 and wst is not None and len(wst['files']) == 2 and nums(load(RES), 1) == ['3055550101'], out[-300:])

    # ---------------------------------------------------------------- an opted-out row with no phone on it still names its person
    gone = [lead(72, '7200 NW 72 ST', 'GONE, GUS; GONE, GAIL')]
    with with_leads(gone):
        fresh(CACHE)
        rc, out = run([], files={'SkipTrace_1.csv': [
            row('Gus', 'Gone', '7300 Nw 73rd St', '33100', g1=[], opt='Yes'),        # opted out, not one phone on the row
            row('Gus', 'Gone', '7200 Nw 72nd St', '33100', g1=[M(7201)])]})          # the same person, another property
        d, t, sd = load(RES), load(STATUS)['total'], (load(SIDE) or {})
    rec('an opted-out row with no phone still holds its person wherever else they are listed',
        rc == 0 and case(72) not in d and t['opt_person_rows'] == 1 and '3055557201' in sd and t['opt_people_held'] == 1, (t, sorted(sd)))

    # ---------------------------------------------------------------- which leads a person-level hold kept a row off
    pc_leads = [lead(90, '9100 NW 91 ST', 'HELD, HAL'),                       # the person, at the row's address
                lead(91, '9100 NW 91 ST APT 2', 'HELD, HAL'),                 # ... in another unit of the building
                lead(92, '9100 NW 91 ST', 'OTHER, OLLIE'),                    # someone else at that address
                lead(93, '9300 NW 93 ST', 'HELD, HAL', lpDismissed=True)]     # a lead nobody is working
    with with_leads(pc_leads):
        fresh(CACHE)
        rc, out = run([], files={'SkipTrace_1.csv': [
            row('Hal', 'Held', '5 Elsewhere Rd', '33100', g1=[M(9001)], opt='Yes'),        # the opt-out
            row('Hal', 'Held', '9100 Nw 91st St', '33100', g1=[M(9002)]),                  # the same person at lead 90's address
            row('Hal', 'Held', '9300 Nw 93rd St', '33100', g1=[M(9003)])]})                # ... and at the dead lead's
        st = load(STATUS)
    rec('the status file lists the live leads a person-level hold kept a row off: this unit, this person, case numbers only',
        rc == 0 and st['opt_person_cases'] == [case(90)] and (st['total']['opt_rows'], st['total']['opt_person_rows']) == (1, 2) and
        not any(case(n) in load(RES) for n in (90, 91, 92, 93)), (st['opt_person_cases'], st['total']))

    # ---------------------------------------------------------------- a lead with no ZIP is not indexed
    nz = [dict(lead(73, '7300 NW 73 ST', 'NOZIP, NAT'), Address='7300 NW 73 ST, MIAMI, FL')]
    with with_leads(nz):
        fresh(CACHE)
        rc, out = run([], files={'SkipTrace_1.csv': [row('Nat', 'Nozip', '7300 Nw 73rd St', '', g1=[M(7301)])]})
        d, t = load(RES), load(STATUS)['total']
    rec('a lead whose address has no ZIP matches no row, not even one with no ZIP either',
        rc == 0 and case(73) not in d and t['unmatched_rows_with_phone'] == 1, t)

    # ---------------------------------------------------------------- discovery looks in Downloads, Desktop and OneDrive\Desktop
    disc_home = os.path.join(TMP, 'disc_home')
    for sub in ('Downloads', 'Desktop', os.path.join('OneDrive', 'Desktop'), 'Documents'):
        os.makedirs(os.path.join(disc_home, sub))
        open(os.path.join(disc_home, sub, 'SkipTrace_%s.csv' % sub.replace(os.sep, '_')), 'w').write('a\n')
    real_home = os.environ['HOME']
    os.environ['HOME'] = os.environ['USERPROFILE'] = disc_home
    try:
        found = sorted(os.path.basename(f) for f in RS.discover(os.path.join(TMP, 'no_imports')))
    finally:
        os.environ['HOME'] = os.environ['USERPROFILE'] = real_home
    rec('discovery looks in Downloads, Desktop and OneDrive\\Desktop, and nowhere else',
        found == ['SkipTrace_Desktop.csv', 'SkipTrace_Downloads.csv', 'SkipTrace_OneDrive_Desktop.csv'], found)

    # ---------------------------------------------------------------- the person hold is indexed, and each person is held once
    same_twice = [row('Gus', 'Gone', '1 A St', '33100', g1=[M(1)], opt='Yes'), row('Gus', 'Gone', '2 A St', '33100', g1=[M(2)], opt='Yes')]
    rec('opt hold: one person opted out on two rows is one person', len(RS.opt_people_of([('f', 'h', same_twice)])) == 1 and
        len(RS.opt_people_of([('f', 'h', same_twice[:1]), ('g', 'i', same_twice[1:])])) == 1)
    rng = _random.Random(20260929)
    def word(prefix, i):                                   # a name made of letters only: digits do not survive name_words
        return prefix + ''.join(chr(65 + (i // 26 ** k) % 26) for k in range(3))
    POOL = [word('W', i) for i in range(60)]
    def rperson():
        f, l = set(rng.sample(POOL, rng.choice([1, 1, 2]))), set(rng.sample(POOL, rng.choice([1, 1, 2])))
        if rng.random() < 0.15:
            (f if rng.random() < 0.5 else l).add(rng.choice(['@JR', '@SR']))
        return f, l
    held_people = [rperson() for _ in range(80)]
    hold = RS.OptHold(held_people)
    agree, checked, pos = True, 0, 0
    for _ in range(4000):
        people = [(g,) + rperson() for g in (1, 2)][:rng.choice([0, 1, 2])]
        want = any(RS.person_matches(f, l, of | ol_) for _, f, l in people for of, ol_ in held_people)
        checked += 1
        pos += want
        if want != RS.row_opted_out({'opt': 'No'}, people, hold):
            agree = False
            break
    rec('opt hold: the index answers exactly what comparing every row with every person answers (%d random rows, %d of them held; '
        'given names and surnames drawn from one pool, so swapped columns are covered)' % (checked, pos),
        agree and len(hold) <= 80 and len(set(hold)) == len(hold) and 200 < pos < checked - 200, (agree, pos, len(hold)))
    held_rows = [{'opt': 'Yes', 'firstName': word('G', i), 'lastName': word('S', i)} for i in range(4000)]
    big = RS.opt_people_of([('f', 'h', held_rows)])
    big_rows = [{'opt': 'No', 'firstName': word('G', i + (0 if i % 2 == 0 else 4000)), 'lastName': word('S', i + (0 if i % 2 == 0 else 4000))}
                for i in range(4000)]
    t0 = _time.time()
    n_held = sum(RS.row_opted_out(x, RS.row_people(x), big) for x in big_rows)
    took = _time.time() - t0
    rec('opt hold: 4,000 rows against %d opted-out people take seconds, not the minutes that comparing each row with each person did'
        % len(big), len(big) == 4000 and n_held == 2000 and took < 5, '%.2fs, %d held' % (took, n_held))

    # ---------------------------------------------------------------- a DNC number goes onto a lead that already has phones
    f4 = [lead(70, '7000 NW 70 ST', 'DNC, DEE'), lead(71, '7100 NW 71 ST', 'NOHIT, NOA')]
    cache4 = {case(70): {'phones': [{'number': '3055557001', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'},
              case(71): {'phones': [], 'source': 'tracerfy', 'traced': '2026-09-10'}}
    with with_leads(f4):
        fresh(cache4)
        rc, out = run([], files={'SkipTrace_1.csv': [
            row('Dee', 'Dnc', '7000 Nw 70th St', '33100', g1=[M(7002, 'Mobile', True)]),        # all DNC, the lead has a callable phone
            row('Noa', 'Nohit', '7100 Nw 71st St', '33100', g1=[M(7102, 'Mobile', True)])]})    # all DNC, the lead has none
        d, t = load(RES), load(STATUS)['total']
    rec('an all-DNC row lands on a lead that already has phones (the bake then shows that number DNC, not as a clean Whitepages '
        'copy); a lead with no phones still gets nothing, so skiptrace can retry it',
        rc == 0 and [(p['number'], p['dnc']) for p in d[case(70)]['phones']] == [('3055557001', False), ('3055557002', True)] and
        d[case(71)] == cache4[case(71)] and (t['leads_dnc_only_skipped'], t['new_numbers'], t['new_numbers_dnc']) == (1, 1, 1), (t, d.get(case(70))))

    # ---------------------------------------------------------------- an export with REsimpli's own column names is never just skipped
    for label, hdr, refuse in (
            ('its name and ZIP columns but the phone and address ones renamed', ['firstName', 'lastName', 'opt', 'propertyZipCode', 'Tel 1', 'Street'], True),
            ('only its trace-date column', ['skipTracedDate', 'x'], True),
            ('a phone slot spelled with a space', ['Phone 1', 'x'], True),
            ('a phone slot spelled with a dash', ['Phone-1', 'x'], True),
            ('a lower-case second phone slot', ['phone_2', 'x'], True),
            ('its second owner\'s columns', ['firstName2', 'lastName2'], True),
            ('generic contact columns', ['name', 'email', 'phone'], False),
            ('a bare opt column', ['opt', 'x'], False)):
        fresh(CACHE)
        write_csv(os.path.join(DL, 'SkipTrace_a.csv'), one)
        write_csv(os.path.join(DL, 'SkipTrace_b.csv'), [dict.fromkeys(hdr, '1')], header=hdr)
        rc, out = run([])
        if refuse:
            rec('refused, not skipped: a file with %s' % label,
                rc == 2 and 'SkipTrace_b.csv' in out and 'not a complete REsimpli skip-trace export' in out and
                'If it is a REsimpli export' in out and 'rename it so it no longer starts with SkipTrace_' in out and
                load(RES) == CACHE and not os.path.exists(SIDE) and not os.path.exists(STATUS), out[-500:])
        else:
            rec('still just skipped by name: a file with %s' % label,
                rc == 0 and 'SKIPPED' in out and 'SkipTrace_b.csv' in out and nums(load(RES), 1) == ['3055550101'], out[-300:])

    # ---------------------------------------------------------------- no two runs share a temp file
    t1, t2 = RS.write_tmp(RES, {'a': 1}), RS.write_tmp(RES, {'b': 2})
    rec('two temp files for one target are two files, each holding its own content, beside the target',
        t1 != t2 and json.load(open(t1)) == {'a': 1} and json.load(open(t2)) == {'b': 2} and
        os.path.dirname(t1) == os.path.dirname(t2) == os.path.dirname(RES) and is_cache_tmp(t1) and is_cache_tmp(t2), (t1, t2))
    RS.drop([t1, t2])
    real_urandom = os.urandom
    os.urandom = lambda n: b'\x07' * n
    try:
        t3 = RS.write_tmp(RES, {'c': 3})
        try:
            RS.write_tmp(RES, {'d': 4})
            clash = None
        except FileExistsError as e:
            clash = e
    finally:
        os.urandom = real_urandom
    rec('a temp name another run already holds is neither reused nor removed',
        clash is not None and os.path.exists(t3) and json.load(open(t3)) == {'c': 3}, clash)
    RS.drop([t3])
    rec('no temp file left from those', not left())

    # ---------------------------------------------------------------- digits are ASCII digits
    rec('digits: a fullwidth digit is the ASCII digit, and no other script\'s digit is a digit',
        RS.norm_number('３０５５５５０１０１') == '3055550101' == RS.parse_number('３０５-５５５-０１０１') and
        RS.norm_number('٣٠٥٥٥٥٠١٠١') == '' and RS.parse_number('٣٠٥٥٥٥٠١٠١') == '' and RS.parse_number('305555０101') == '3055550101' and
        RS.parse_number('３０５５５５０１０１.０') == '3055550101' == RS.parse_number('3055550101.0'))
    fresh({case(1): {'phones': [{'number': '3055550101', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'}})
    rc, out = run([], files={'SkipTrace_1.csv': [row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[dict(n='３０５５５５０１０１', DNC='Yes')])]})
    rec('a DNC flag on a number written in fullwidth digits flags the number written normally',
        rc == 0 and load(RES)[case(1)]['phones'][0]['dnc'] is True and '3055550101' in (load(SIDE) or {}), out[-300:])

    # ---------------------------------------------------------------- a flag on a cell that holds more than one number
    rec('numbers written in a cell of text: an extension, a note or two numbers are found; a short, foreign or sci-notation cell is not',
        RS.numbers_in('305-555-0101 x22') == {'3055550101'} and RS.numbers_in('(305) 555-0101, 305.555.0102') == {'3055550101', '3055550102'} and
        RS.numbers_in('+1 305 555 0103 (work)') == {'3055550103'} and RS.numbers_in('3.05555E+09') == set() and
        RS.numbers_in('555-0101') == set() and RS.numbers_in('1234567890') == set() and RS.numbers_in('30555501011') == set() and
        RS.numbers_in(None) == set() and RS.numbers_in('') == set() and RS.numbers_in('23055550101') == set() and RS.numbers_in('13055550101 x5') == {'3055550101'} and RS.numbers_in('305-155-0101') == set() and
        RS.numbers_in('３０５-５５５-０１０１ x2') == {'3055550101'})
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [
        row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[dict(n='305-555-0101 x12', DNC='Yes')]),         # flagged, and not one number
        row('Zed', 'Nobody', '999 Nowhere Rd', '33100', g1=[dict(n='305-555-0104, 305-555-0105')], opt='Yes'),   # opted out, two numbers
        row('Dan', 'Deez', '400 Se 4th Terrace', '33100', g1=[M(101)]),                                          # the same number, clean, elsewhere
        row('Bo', 'Sample', '200 Nw 20th Ave', '33100', g1=[dict(n='305-555-0106 x3')])]})                       # clean, and not one number
    d, sd, t = load(RES), (load(SIDE) or {}), load(STATUS)['total']
    rec('a flag on a cell that is not one number still flags the numbers written in it, so the same number listed clean on '
        'another row is DNC there (no entry for a lead with nothing else); a clean row\'s odd cell flags nothing',
        rc == 0 and {'3055550101', '3055550104', '3055550105'} <= set(sd) and '3055550106' not in sd and case(4) not in d and
        nums(d, 2) == ['3055550200'] and t['numbers_unreadable'] == 3 and t['leads_dnc_only_skipped'] == 1, (t, sorted(sd)))

    # ---------------------------------------------------------------- opt: the words it is read as, and the way forward when it is not one
    yes_words = ('Yes', 'Opted Out', 'opted-out', 'Opt Out', 'opt-out', 'OPTOUT', 'Unsubscribed', 'STOP', 'dnc', '1.0', ' true ')
    no_words = ('', 'No', 'FALSE', 'n', '0', '0.0', ' 0.00 ')
    rec('opt: the unmistakable yes words, and a spreadsheet\'s 1.0, are opted out; 0.0 is not',
        all(RS.opted_out({'opt': v}) for v in yes_words) and not any(RS.opted_out({'opt': v}) for v in no_words) and
        [RS.opt_word(v) for v in ('1.0', '0.0', ' Yes ', '1.5', '10.0', '2.0')] == ['1', '0', 'yes', '1.5', '10.0', '2.0'])
    rec('opt: a value nobody can read is opted out, not "no" (fail closed, if one ever gets past the file check)',
        RS.opted_out({'opt': 'Maybe'}) and RS.opted_out({}) and not RS.opted_out({'opt': 'No'}))
    path = os.path.join(TMP, 'opt_words.csv')
    write_csv(path, [dict(one[0], opt=v) for v in yes_words + no_words])
    try:
        got, err = RS.read_export(path), None
    except RS.SyncError as e:
        got, err = [], e
    rec('opt: a file that uses those words is read, not refused', len(got) == len(yes_words) + len(no_words) and
        [RS.opted_out(x) for x in got] == [True] * len(yes_words) + [False] * len(no_words), err)
    fresh(CACHE)
    path = os.path.join(TMP, 'opt_maybe.csv')
    write_csv(path, [dict(one[0], opt='Maybe')])
    rc, out = run([path])
    write_csv(path, [dict(one[0], opt='Maybe'), dict(one[0], opt=' Maybe ')])
    rc, out2 = run([path])
    rec('the `opt` refusal counts kinds of value, and padding does not make a second kind', rc == 2 and 'has 1 kind of value' in out2, out2[-300:])
    rec('the `opt` refusal says how to go on: tell Claude, or change the cells in a copy, or download again',
        rc == 2 and 'tell Claude' in out and 'in a copy of the file (save it as CSV UTF-8)' in out and 'then delete the original' in out and
        'download the export from REsimpli again' in out, out[-500:])

    # ---------------------------------------------------------------- what to do about a torn sidecar or a torn Whitepages file
    fresh(CACHE)
    open(SIDE, 'wb').write(garbage)
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('a torn sidecar: the refusal does not tell anyone to move it aside (that would lose the registry verdicts), and says '
        'that there may be no backup', rc == 2 and 'Do not just move it aside' in out and 'registry verdict' in out and
        'there may be none' in out, out[-600:])
    fresh(CACHE)
    open(WPF, 'w').write('{"2026-000001-CA-01": {"result": [')
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    rec('a torn whitepages_lookup.json: the refusal says what to do', rc == 2 and 'restore a copy' in out and 'whitepages_lookup.py' in out, out[-500:])

    # ---------------------------------------------------------------- a run that only tightens still writes the cache
    fresh({case(1): {'phones': [{'number': '3055550101', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'}})
    rc, out = run([], files={'SkipTrace_1.csv': [row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[M(101, 'Mobile', True)])]})
    t = load(STATUS)['total']
    rec('a run whose only change is a flag tightened on a cached number writes the cache, after backing it up',
        rc == 0 and t['new_numbers'] == 0 and t['dnc_tightened'] == 1 and load(RES)[case(1)]['phones'][0]['dnc'] is True and
        any(f.startswith('skiptrace_results.pre-resimpli-') for f in os.listdir(os.path.join(DFDIR, 'backups'))), (t, out[-300:]))

    # ---------------------------------------------------------------- two generation markers on one line are not one
    rec('names: a Jr against a line that carries two markers (JR II) is not that person either',
        not same('Jose', 'Perez Jr', 'PEREZ, JOSE JR II') and same('Jose', 'Perez Jr', 'PEREZ, JOSE JR') and
        not same('Jose', 'Perez Sr', 'PEREZ, JOSE JR II'))

    # ---------------------------------------------------------------- Whitepages numbers REsimpli flags are counted
    fresh(CACHE, wp={case(4): {'result': {'ownership_info': {'person_owners': [{'phones': [{'number': '3055550400'}, {'number': '(305) 555-0102'},
                                                                                            {'number': '3055557777'}]}]}}},
                     case(8): {'result': {'residents': [{'phones': [{'number': '3055550801'}]}]}}})
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    t = load(STATUS)['total']
    rec('flagged numbers a Whitepages record also lists are counted, and the run says so (the bake would append them clean)',
        rc == 0 and t['flagged_also_whitepages'] == 3 and 'are also Whitepages numbers of some lead' in out, (t['flagged_also_whitepages'], out[-300:]))
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': ROWS})
    t = load(STATUS)['total']
    rec('...none with no Whitepages file, and the full run holds one opted-out person',
        (t['flagged_also_whitepages'], t['opt_people_held']) == (0, 1) and 'Whitepages numbers of some lead' not in out, t)

    # ================================================================ what the fourth look found
    # ---------------------------------------------------------------- a temp file left by a killed run is gitignored
    gi_path = os.path.join(RS.HERE, '.gitignore')
    if os.path.exists(gi_path):
        import fnmatch as _fnmatch
        gi = [ln.strip() for ln in open(gi_path, encoding='utf-8') if ln.strip() and not ln.strip().startswith(('#', '!'))]
        made = [RS.write_tmp(RES, {}), RS.write_tmp(SIDE, {})]
        tmp_names = [os.path.basename(m) for m in made] + ['skiptrace_results.json.resimpli.tmp', 'dnc_scrub.json.resimpli.tmp']
        RS.drop(made)
        loose = [n for n in tmp_names if not any(_fnmatch.fnmatchcase(n, g) for g in gi)]
        rec('the temp files a killed run leaves beside the cache and the sidecar (this version\'s random names and the earlier fixed '
            'ones) are gitignored: a crash between writing them and replacing the files must not leave homeowner numbers one '
            '`git add` from a public repo', not loose, loose)
    else:
        skip('.gitignore is not beside resimpli_sync.py: the temp file names were not checked against it')

    # ---------------------------------------------------------------- the order the rows come in decides nothing
    st_leads = [lead(94, '9400 NW 94 ST', 'SETTLE, SAM'), lead(95, '9500 NW 95 ST', 'ONLY, DEE'), lead(96, '9600 NW 96 ST', 'CAPPED, CAL'),
                lead(97, '9700 NW 97 ST', 'TWICE, TIM'), lead(98, '9800 NW 98 ST', 'FULL, FLO'), lead(99, '9900 NW 99 ST', 'SWAP, SUE')]

    def phones_at(d, n):
        return [(p['number'], p['dnc']) for p in (d.get(case(n)) or {}).get('phones', [])]

    dnc_first = row('Sam', 'Settle', '9400 Nw 94th St', '33100', g1=[M(9401, 'Mobile', True), M(9402, 'Landline', True)])      # all DNC
    callable_ = row('Sam', 'Settle', '9400 Nw 94th St', '33100', g1=[M(9403), M(9404, 'Landline')])                         # the same lead, dialable
    only = row('Dee', 'Only', '9500 Nw 95th St', '33100', g1=[M(9501, 'Mobile', True), M(9502, 'Landline', True)])           # a lead with nothing but DNC
    want94 = [('3055559403', False), ('3055559404', False), ('3055559401', True), ('3055559402', True)]
    finals = {}
    for label, files in (('one file, the flagged row first', {'SkipTrace_1.csv': [dnc_first, callable_, only]}),
                         ('one file, the callable row first', {'SkipTrace_1.csv': [callable_, dnc_first, only]}),
                         ('the flagged export sorts first', {'SkipTrace_a.csv': [dnc_first, only], 'SkipTrace_b.csv': [callable_]}),
                         ('the flagged export sorts last', {'SkipTrace_a.csv': [callable_], 'SkipTrace_b.csv': [dnc_first, only]})):
        with with_leads(st_leads):
            fresh(CACHE, wp={case(94): {'result': {'residents': [{'phones': [{'number': '3055559401'}]}]}}})
            rc, out = run([], files=files)
            d, t = load(RES), load(STATUS)['total']
        finals[label] = (phones_at(d, 94), phones_at(d, 95))
        rec('flagged numbers are placed after every callable one, whichever row or export comes first (%s): the lead ends with its '
            'callable numbers, then the flagged ones (mobile first); a lead with only flagged numbers gets nothing and is counted once' % label,
            rc == 0 and phones_at(d, 94) == want94 and phones_at(d, 95) == [] and case(95) not in d and
            (t['leads_dnc_only_skipped'], t['new_numbers'], t['new_numbers_dnc'], t['new_mobile_clean'], t['numbers_over_cap']) == (1, 4, 2, 1, 0),
            (t, phones_at(d, 94)))
    rec('...and the four orders end with the same phones', len({repr(v) for v in finals.values()}) == 1, finals)

    # what is placed at the end is in the totals, and not in the per-file lines and entries, which are what each file added
    with with_leads(st_leads):
        fresh(CACHE)
        rc, out = run([], files={'SkipTrace_1.csv': [dnc_first, callable_]})
        st = load(STATUS)
    f0 = st['files'][0]
    rec('the flagged numbers are counted in the totals, not per file: the per-file entry and line say what the file added that is callable',
        rc == 0 and st['total']['new_numbers'] == 4 and st['total']['new_numbers_dnc'] == 2 and st['total']['leads_dnc_only_skipped'] == 0 and
        f0['new_numbers'] == 2 and 'new_numbers_dnc' not in f0 and 'leads_dnc_only_skipped' not in f0 and
        'SkipTrace_1.csv: 2 rows, 2 with phones, 1 leads matched, 2 new callable numbers, 1 leads had no phone' in out, (st['total'], f0, out[:300]))

    # the room is shared: callable numbers first, then what still fits, the rest counted
    def with_cap(n, fn):
        was = RS.PS.MAX_PHONES
        RS.PS.MAX_PHONES = n
        try:
            return fn()
        finally:
            RS.PS.MAX_PHONES = was

    flagged3 = row('Cal', 'Capped', '9600 Nw 96th St', '33100', g1=[M(9601, 'Mobile', True), M(9602, 'Mobile', True), M(9603, 'Mobile', True)])
    dialable4 = row('Cal', 'Capped', '9600 Nw 96th St', '33100', g1=[M(9604), M(9605), M(9606), M(9607)])
    for label, rows_ in (('flagged row first', [flagged3, dialable4]), ('flagged row last', [dialable4, flagged3])):
        def go():
            with with_leads(st_leads):
                fresh(CACHE)
                rc_, out_ = run([], files={'SkipTrace_1.csv': rows_})
                return rc_, load(RES), load(STATUS)['total']
        rc, d, t = with_cap(5, go)
        rec('the cap: 4 callable numbers fill the entry first, the one flagged number that still fits follows, the other two are counted, '
            'not added (%s)' % label,
            rc == 0 and phones_at(d, 96) == [('3055559604', False), ('3055559605', False), ('3055559606', False), ('3055559607', False),
                                             ('3055559601', True)] and (t['numbers_over_cap'], t['new_numbers'], t['new_numbers_dnc']) == (2, 5, 1),
            (t, phones_at(d, 96)))

    # a lead that already holds nine numbers has room for one more: the callable number gets it, whichever row comes first
    full = {case(98): {'phones': [{'number': '30555598%02d' % i, 'type': 'Mobile', 'dnc': False} for i in range(9)], 'source': 'tracerfy'}}
    flagged_row = row('Flo', 'Full', '9800 Nw 98th St', '33100', g1=[M(9891, 'Mobile', True)])
    callable_row = row('Flo', 'Full', '9800 Nw 98th St', '33100', g1=[M(9892)])
    for label, rows_ in (('flagged row first', [flagged_row, callable_row]), ('callable row first', [callable_row, flagged_row])):
        with with_leads(st_leads):
            fresh(full)
            rc, out = run([], files={'SkipTrace_1.csv': rows_})
            d, t = load(RES), load(STATUS)['total']
        rec('a flagged number never takes the last place a callable one wants on a lead that already has phones (%s)' % label,
            rc == 0 and phones_at(d, 98)[9:] == [('3055559892', False)] and len(phones_at(d, 98)) == 10 and
            (t['numbers_over_cap'], t['new_numbers'], t['new_numbers_dnc'], t['new_mobile_clean']) == (1, 1, 0, 1), (t, phones_at(d, 98)[8:]))

    # a flagged number Whitepages also lists for the lead moves from the Whitepages part of the row to the entry: it needs no extra place
    wp_swap = {case(99): {'result': {'residents': [{'phones': [{'number': '3055559991'}]}]}}}
    at_cap = {case(99): {'phones': [{'number': '30555599%02d' % i, 'type': 'Mobile', 'dnc': False} for i in range(4)], 'source': 'tracerfy'}}
    both = row('Sue', 'Swap', '9900 Nw 99th St', '33100', g1=[M(9992, 'Mobile', True), M(9991, 'Landline', True)])
    def swap_run():
        with with_leads(st_leads):
            fresh(at_cap, wp=wp_swap)
            rc_, out_ = run([], files={'SkipTrace_1.csv': [both]})
            first, t1_ = load(RES), load(STATUS)['total']
            rc2_, out2_ = run([])
            return rc_ or rc2_, first, t1_, load(RES), load(STATUS)['total']
    rc, d, t, d2, t2 = with_cap(5, swap_run)
    rec('at the cap, the flagged number Whitepages lists for the lead is stored (the bake would otherwise append that copy as clean), '
        'the other flagged number is counted over the cap, and a second run changes nothing',
        rc == 0 and phones_at(d, 99)[4:] == [('3055559991', True)] and len(phones_at(d, 99)) == 5 and
        (t['new_numbers'], t['new_numbers_dnc'], t['numbers_over_cap']) == (1, 1, 1) and d2 == d and
        (t2['new_numbers'], t2['numbers_over_cap']) == (0, 1), (t, t2, phones_at(d, 99)))

    # the same flagged number on two rows is stored once
    with with_leads(st_leads):
        fresh(CACHE)
        rc, out = run([], files={'SkipTrace_1.csv': [
            row('Tim', 'Twice', '9700 Nw 97th St', '33100', g1=[M(9701, 'Mobile', True)]),
            row('Tim', 'Twice', '9700 Nw 97th St', '33100', g1=[M(9701, 'Mobile', True)]),
            row('Tim', 'Twice', '9700 Nw 97th St', '33100', g1=[M(9702)])]})
        d, t = load(RES), load(STATUS)['total']
    rec('a flagged number listed on two rows is stored once, after the callable one', phones_at(d, 97) == [('3055559702', False), ('3055559701', True)] and
        (t['new_numbers'], t['new_numbers_dnc']) == (2, 1), (t, phones_at(d, 97)))

    # a lead whose entry already has phones takes the flagged numbers too, after them
    with with_leads(st_leads):
        fresh({case(94): {'phones': [{'number': '3055559400', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'}})
        rc, out = run([], files={'SkipTrace_1.csv': [dnc_first]})
        t = load(STATUS)['total']
    rec('a lead that already has phones takes the flagged numbers (mobile first), and is not counted as skipped',
        rc == 0 and phones_at(load(RES), 94) == [('3055559400', False), ('3055559401', True), ('3055559402', True)] and
        (t['leads_dnc_only_skipped'], t['new_numbers'], t['new_numbers_dnc']) == (0, 2, 2), (t, phones_at(load(RES), 94)))

    # ---------------------------------------------------------------- a cell is digits and phone punctuation, or it is not read
    rec('numbers: only digits and phone punctuation are read; a letter, another script\'s digit, a note or a slash is not guessed at',
        [RS.parse_number(v) for v in ('(305) 555-0101', '+1 305 555 0101', '305.555.0101', '3055550101.0', '305555٠101.0', '305555O101.0',
                                      'Mobile 305-555-0101', '305/555/0101', '3٠' + '5-555-0101', '305 555 0101 ext')] ==
        ['3055550101', '3055550101', '3055550101', '3055550101', '', '', '', '', '', ''])
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [row('Ana', 'Tester', '100 Sw 10th Ct', '33100', g1=[dict(n='305555٠101.0'), dict(n='305555O101.0')])]})
    rec('a cell that would read as a different valid number once its odd characters are dropped adds nothing to any lead',
        rc == 0 and case(1) not in load(RES) and load(STATUS)['total']['numbers_unreadable'] == 2, out[-300:])
    # only the flagged slot's odd cell flags the numbers written in it, not another odd cell on the same row
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_1.csv': [row('Ana', 'Tester', '100 Sw 10th Ct', '33100',
                                                     g1=[dict(n='305-555-0104 x1', DNC='Yes'), dict(n='305-555-0105 x2')])]})
    sd = load(SIDE) or {}
    rec('an unreadable cell flags the numbers written in it only when its own slot is flagged: a second, clean odd cell on the row flags nothing',
        rc == 0 and '3055550104' in sd and '3055550105' not in sd and load(STATUS)['total']['numbers_unreadable'] == 2, sorted(sd))

    # ---------------------------------------------------------------- naming one file says how many other exports it did not read
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_waiting.csv'), [row('Bo', 'Sample', '200 Nw 20th Ave', '33100', g1=[M(201)])])       # another export in Downloads
    write_csv(os.path.join(DL, 'SkipTrace_same.csv'), one)                                                                        # a copy of the named file
    pn = os.path.join(TMP, 'named_n.csv')
    write_csv(pn, one)
    rc, out = run([pn])
    st = load(STATUS)
    rec('one-file mode says how many other exports in Downloads / Desktop it did not read (a copy of the named file is not one), '
        'and the status file counts them',
        rc == 0 and 'NOTE: 1 other SkipTrace_*.csv file in Downloads / Desktop was not read' in out and st['unread_files'] == 1 and
        nums(load(RES), 2) == ['3055550200'], (st['unread_files'], out[:600]))
    fresh(CACHE)
    write_csv(os.path.join(DL, 'SkipTrace_waiting.csv'), [row('Bo', 'Sample', '200 Nw 20th Ave', '33100', g1=[M(201)])])
    write_csv(os.path.join(DL, 'SkipTrace_more.csv'), [row('Bo', 'Sample', '200 Nw 20th Ave', '33100', g1=[M(202)])])
    rc, out = run([pn])
    rec('...in the plural too', rc == 0 and 'NOTE: 2 other SkipTrace_*.csv files in Downloads / Desktop were not read' in out and
        'An opt-out or DNC flag in them is not in force' in out and load(STATUS)['unread_files'] == 2, out[:600])
    fresh(CACHE)
    rc, out = run([], files={'SkipTrace_a.csv': one})
    rec('...and a run with no path reads every export, so has none to report', rc == 0 and 'not read' not in out and load(STATUS)['unread_files'] == 0, out[:400])

    # a file named by a relative path is still a named file: a stray one is refused, not skipped
    stray_hdr = ['name', 'email', 'phone']
    write_csv(os.path.join(TMP, 'stray.csv'), [dict.fromkeys(stray_hdr, '1')], header=stray_hdr)
    fresh(CACHE)
    cwd0 = os.getcwd()
    os.chdir(TMP)
    try:
        rc, out = run(['stray.csv'])
    finally:
        os.chdir(cwd0)
    rec('a stray file named by a relative path is refused like one named by a full path (exit 2, nothing written)',
        rc == 2 and 'REFUSED' in out and load(RES) == CACHE and not os.path.exists(STATUS), out[:300])

    # the kept copies are the .csv files in the folder, nothing else that happens to be there
    fresh(CACHE)
    kept_dir = os.path.join(DFDIR, 'imports', 'resimpli')
    os.makedirs(kept_dir)
    open(os.path.join(kept_dir, 'notes.txt'), 'w').write('not an export\n')
    rc, out = run([pn])
    rec('a file in the kept-copies folder that is not a .csv is left alone', rc == 0 and 'from 1 exports (1 files found)' in out, out[:300])

    # ---------------------------------------------------------------- settle() places flagged numbers in a fixed order
    # two flagged rows for one lead with room for one: the winner is the same whichever row is read first
    rx = row('Cal', 'Capped', '9600 Nw 96th St', '33100', g1=[M(9603, 'Mobile', True)])
    ry = row('Cal', 'Capped', '9600 Nw 96th St', '33100', g1=[M(9601, 'Landline', True)])
    for label, rows_ in (('the higher number first', [rx, ry, dialable4]), ('the lower number first', [ry, rx, dialable4])):
        def go():
            with with_leads(st_leads):
                fresh(CACHE)
                rc_, out_ = run([], files={'SkipTrace_1.csv': rows_})
                return rc_, load(RES), load(STATUS)['total']
        rc, d, t = with_cap(5, go)
        rec('with room for one flagged number of two, the mobile one is stored, not the one that was read first (%s)' % label,
            rc == 0 and phones_at(d, 96)[4:] == [('3055559603', True)] and len(phones_at(d, 96)) == 5 and t['numbers_over_cap'] == 1, (t, phones_at(d, 96)))

    rz1 = row('Cal', 'Capped', '9600 Nw 96th St', '33100', g1=[M(9603, 'Mobile', True)])
    rz2 = row('Cal', 'Capped', '9600 Nw 96th St', '33100', g1=[M(9601, 'Mobile', True)])
    for label, rows_ in (('the higher number first', [rz1, rz2, dialable4]), ('the lower number first', [rz2, rz1, dialable4])):
        def go():
            with with_leads(st_leads):
                fresh(CACHE)
                rc_, out_ = run([], files={'SkipTrace_1.csv': rows_})
                return rc_, load(RES)
        rc, d = with_cap(5, go)
        rec('with room for one of two flagged mobiles, the lower number is stored whichever row was read first (%s)' % label,
            rc == 0 and phones_at(d, 96)[4:] == [('3055559601', True)] and len(phones_at(d, 96)) == 5, phones_at(d, 96))

    # flagged numbers set aside while reading several exports are all placed, after the callable ones, on both leads
    with with_leads(st_leads):
        fresh(CACHE)
        rc, out = run([], files={
            'SkipTrace_a.csv': [row('Sam', 'Settle', '9400 Nw 94th St', '33100', g1=[M(9401, 'Mobile', True)])],
            'SkipTrace_b.csv': [row('Sam', 'Settle', '9400 Nw 94th St', '33100', g1=[M(9402, 'Landline', True)]),
                                row('Tim', 'Twice', '9700 Nw 97th St', '33100', g1=[M(9701, 'Mobile', True)])],
            'SkipTrace_c.csv': [callable_, row('Tim', 'Twice', '9700 Nw 97th St', '33100', g1=[M(9702)])]})
        d, t = load(RES), load(STATUS)['total']
    rec('flagged numbers set aside while reading three exports are all placed after the callable ones, on both leads',
        rc == 0 and phones_at(d, 94) == want94 and phones_at(d, 97) == [('3055559702', False), ('3055559701', True)] and
        (t['new_numbers'], t['new_numbers_dnc'], t['leads_dnc_only_skipped']) == (6, 3, 0), (t, phones_at(d, 94), phones_at(d, 97)))
    fresh({case(8): {'phones': [{'number': '13055550801', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'}})
    rc, out = run([], files={'SkipTrace_1.csv': [row('Fay', 'Flag', '800 Se 8th Ct', '33100', g1=[M(801), M(802)])]})
    rec('a number the cache holds in its 11-digit spelling is not added again by a row that lists it in ten digits',
        rc == 0 and nums(load(RES), 8) == ['13055550801', '3055550802'], (nums(load(RES), 8), out[-200:]))

    # a Whitepages number the entry lacks keeps its place on the row: a flagged number that is not one of them cannot take it
    reserved = {case(99): {'phones': [{'number': '30555599%02d' % i, 'type': 'Mobile', 'dnc': False} for i in range(3)], 'source': 'tracerfy'}}
    two_flagged = row('Sue', 'Swap', '9900 Nw 99th St', '33100', g1=[M(9993, 'Mobile', True), M(9994, 'Mobile', True)])
    def reserve_run():
        with with_leads(st_leads):
            fresh(reserved, wp={case(99): {'result': {'residents': [{'phones': [{'number': '3055559909'}]}]}}})      # a clean number, not flagged
            rc_, out_ = run([], files={'SkipTrace_1.csv': [two_flagged]})
            return rc_, load(RES), load(STATUS)['total']
    rc, d, t = with_cap(5, reserve_run)
    rec('a place the bake will give a Whitepages number is kept for it: with 3 numbers, 1 Whitepages number and room for 5, one of two flagged '
        'numbers is stored and the other is counted over the cap',
        rc == 0 and len(phones_at(d, 99)) == 4 and phones_at(d, 99)[3] == ('3055559993', True) and t['numbers_over_cap'] == 1, (t, phones_at(d, 99)))

    # a lead at its cap takes no flagged number, and its entry is left exactly as it was
    capped_entry = {case(98): {'phones': [{'number': '30555598%02d' % i, 'type': 'Mobile', 'dnc': False} for i in range(10)], 'source': 'tracerfy'}}
    with with_leads(st_leads):
        fresh(capped_entry)
        rc, out = run([], files={'SkipTrace_1.csv': [flagged_row]})
        d, t = load(RES), load(STATUS)['total']
    rec('a lead at its cap takes no flagged number: the entry is exactly as it was (no marker either) and the number is counted over the cap',
        rc == 0 and d == capped_entry and (t['new_numbers'], t['numbers_over_cap'], t['leads_dnc_only_skipped']) == (0, 1, 0), (t, out[-200:]))

    # the marker the sync leaves on an entry it changed is left by the flagged numbers too
    with with_leads(st_leads):
        fresh({case(94): {'phones': [{'number': '3055559400', 'type': 'Mobile', 'dnc': False}], 'source': 'tracerfy'}})
        rc, out = run([], files={'SkipTrace_1.csv': [dnc_first]})
        d = load(RES)
    rec('an entry that only gained flagged numbers carries the sync marker (today\'s date)', rc == 0 and d[case(94)].get('resimpli') == TODAY.isoformat(), d.get(case(94)))

    # settle() on its own, on random leads: the order the numbers were held in changes nothing, nothing is duplicated or lost,
    # the entry and the Whitepages numbers together never outgrow the room the bake has, and only held numbers are added
    import copy as _copy
    r2 = _random.Random(20260930)
    POOLN = ['30555590%02d' % i for i in range(40)]
    def held_phone(n, t):
        return {'number': n, 'type': t, 'carrier': '', 'dnc': True, 'src': 'resimpli'}
    def wp_rec(ns):
        return {'result': {'residents': [{'phones': [{'number': n} for n in ns]}]}}
    problems, seen_kinds = [], {'added': 0, 'over': 0, 'swap': 0, 'already_held': 0, 'none': 0}
    was_max = RS.PS.MAX_PHONES
    try:
        for trial in range(600):
            RS.PS.MAX_PHONES = cap = r2.randint(3, 10)
            have_n = r2.sample(POOLN, r2.randint(1, cap))
            wp_n = r2.sample(POOLN, r2.randint(0, 3))
            held = [held_phone(r2.choice(POOLN), r2.choice(['Mobile', 'Landline', ''])) for _ in range(r2.randint(1, 9))]
            entry = {'phones': [{'number': ('1' + n) if r2.random() < 0.3 else n, 'type': 'Mobile', 'dnc': False} for n in have_n]}
            runs = []
            for k in range(3):
                held_k = list(held)
                r2.shuffle(held_k)
                res = {'C': _copy.deepcopy(entry)}
                counts = RS.settle(res, {'dnc_held': {'C': held_k}}, {'C': wp_rec(wp_n)}, '2026-09-30')
                runs.append((json.dumps(res, sort_keys=True), counts))
            res, counts = json.loads(runs[0][0]), runs[0][1]
            got = res['C']['phones']
            added = got[len(have_n):]
            added_n = [p['number'] for p in added]
            got_n = {RS.norm_number(p['number']) for p in got}
            before = len(have_n) + len(set(wp_n) - set(have_n))
            after = len(got) + len(set(wp_n) - got_n)
            ok_ = (len({r[0] for r in runs}) == 1 and all(r[1] == runs[0][1] for r in runs) and          # the order held in decides nothing
                   got[:len(have_n)] == entry['phones'] and len(got_n) == len(got) and    # nothing lost or doubled
                   all(p['dnc'] is True and p['number'] in {h['number'] for h in held} and p['number'] not in have_n for p in added) and
                   (after <= max(cap, before)) and len(got) <= max(cap, len(have_n)) and                # never outgrows the room
                   counts['new_numbers'] == counts['new_numbers_dnc'] == len(added) and
                   ('resimpli' in res['C']) == bool(added) and counts['leads_dnc_only_skipped'] == 0 and
                   counts['numbers_over_cap'] == len({h['number'] for h in held} - set(have_n) - set(added_n)) and
                   all(cap - len(got) - len(set(wp_n) - got_n) + (h['number'] in set(wp_n) - got_n) < 1
                       for h in held if h['number'] not in have_n and h['number'] not in added_n))         # nothing that still fits was left out
            if not ok_:
                problems.append((trial, cap, have_n, wp_n, [(h['number'], h['type']) for h in held], added_n, counts))
            seen_kinds['added'] += bool(added)
            seen_kinds['over'] += counts['numbers_over_cap'] > 0
            seen_kinds['swap'] += any(n in wp_n for n in added_n)
            seen_kinds['already_held'] += any(h['number'] in have_n for h in held)
            seen_kinds['none'] += not added
    finally:
        RS.PS.MAX_PHONES = was_max
    rec('settle(): on 600 random leads, the order the numbers were held in changes nothing, no number is lost or doubled, the entry '
        'and its Whitepages numbers never outgrow the room, and every kind of case came up (numbers added, numbers over the cap, a number '
        'Whitepages also lists, a held number the entry has, a lead with nothing added)',
        not problems and all(v > 15 for v in seen_kinds.values()), problems[:2] or seen_kinds)
    # a lead that has no phones at the end gets nothing and is counted, whatever else was held for other leads
    res = {'A': {'phones': []}, 'B': {'phones': [{'number': '3055559000', 'type': 'Mobile', 'dnc': False}]}}
    counts = RS.settle(res, {'dnc_held': {'A': [held_phone('3055559001', 'Mobile')], 'B': [held_phone('3055559002', 'Mobile')],
                                          'Z': [held_phone('3055559003', 'Mobile')]}}, {}, '2026-09-30')
    rec('settle(): a lead with an empty entry, or no entry, gets nothing and is counted once each; another lead is served',
        res['A'] == {'phones': []} and 'Z' not in res and [p['number'] for p in res['B']['phones']] == ['3055559000', '3055559002'] and
        (counts['leads_dnc_only_skipped'], counts['new_numbers'], counts['numbers_over_cap']) == (2, 1, 0), (res, counts))

    # ---------------------------------------------------------------- an export that cannot be read still counts as not read
    fresh(CACHE)
    os.mkdir(os.path.join(DL, 'SkipTrace_dir.csv'))                # matches the pattern, cannot be read as a file
    rc, out = run([pn])
    rec('one-file mode counts a file in Downloads it cannot read as not read (the safe side), and still runs',
        rc == 0 and 'NOTE: 1 other SkipTrace_*.csv file in Downloads / Desktop was not read' in out and load(STATUS)['unread_files'] == 1, out[:300])

finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('\n%d passed, %d failed%s' % (len(ok), len(bad), ', %d skipped (see SKIP above)' % len(skips) if skips else ''))
sys.exit(1 if bad else 0)
