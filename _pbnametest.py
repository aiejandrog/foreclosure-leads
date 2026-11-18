"""_pbnametest -- Palm Beach owner-name resolver, and the fresh-filing card in Call Mode.

Run:  python _pbnametest.py   (exit 0 = safe; no network, invented names and parcels only)

WHAT THIS PINS
  1. queries(): roll-shaped prefixes ('LAST FIRST', then 'LAST'), entities as written.
  2. agrees(): every name token must be on the roll; a court middle NAME may match a roll INITIAL;
     a shared surname alone is never agreement.
  3. resolve_row(): one parcel + homestead = high; one parcel = medium; several = low with the
     candidates; none = none; a last name that fills the page is not searched; an unreachable roll
     is unknown, never "no such owner".
  4. run(): writes Broward-shaped entries, never overwrites a case another rung settled, and retries
     a 'none' only after the wait.
  5. The chain runs it before values, and a one-parcel Palm Beach match is treated as Broward's is.
  6. Call Mode: a fresh filing says what the complaint claims (or that it has not been read) and
     never "judgment not posted"; a case with a dated sale keeps the judgment wording.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fl_lp import palmbeach_resolve as PN  # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra[:220]) if extra else ''))
    if not ok:
        fails.append(name)


def parcel(pid, owner, addr='12 TEST LN', hs=0, jv=400000):
    return {'PARCEL_ID': pid, 'CO_NO': 60, 'OWN_NAME': owner, 'PHY_ADDR1': addr, 'PHY_CITY': 'BOCA RATON',
            'PHY_ZIPCD': '33431', 'JV': jv, 'JV_HMSTD': hs, 'OWN_ADDR1': '9 MAIL RD', 'OWN_CITY': 'BOCA RATON',
            'OWN_STATE': 'FL', 'OWN_ZIPCD': '33431'}


# ---- 1. queries --------------------------------------------------------------------------------
rec('1a: LAST, FIRST M -> roll prefixes', PN.queries('MARTINEZ, SHAWNA E') == ['MARTINEZ SHAWNA', 'MARTINEZ'], PN.queries('MARTINEZ, SHAWNA E'))
rec('1b: FIRST LAST -> LAST FIRST, then the name as written, then the surname',
    PN.queries('PAULA DRALUCK') == ['DRALUCK PAULA', 'PAULA DRALUCK', 'DRALUCK'], PN.queries('PAULA DRALUCK'))
rec('1f: a trailing suffix is not taken for the surname',
    PN.queries('PAT Q TESTER JR') == ['TESTER PAT', 'PAT Q', 'TESTER'], PN.queries('PAT Q TESTER JR'))
rec('1c: an entity is searched as written, then without its suffix',
    PN.queries('TESTCO HOLDINGS LLC') == ['TESTCO HOLDINGS LLC', 'TESTCO HOLDINGS'], PN.queries('TESTCO HOLDINGS LLC'))
rec('1d: a quote never reaches the query', "'" not in ' '.join(PN.queries("O'TESTLY, PAT")), PN.queries("O'TESTLY, PAT"))
rec('1e: nothing to search, no query', PN.queries('') == [] and PN.queries(None) == [])

# ---- 2. agrees ------------------------------------------------------------------------------------
rec('2a: every token on the roll', PN.agrees('MARTINEZ, SHAWNA', 'MARTINEZ SHAWNA &'))
rec('2b: a middle name matches a middle initial on the roll', PN.agrees('MARTINEZ, SHAWNA ELIZABETH', 'MARTINEZ SHAWNA E'))
rec('2c: a middle name with a DIFFERENT initial does not', not PN.agrees('MARTINEZ, SHAWNA ELIZABETH', 'MARTINEZ SHAWNA R'))
rec('2d: a middle name with no initial on the roll does not', not PN.agrees('MARTINEZ, SHAWNA ELIZABETH', 'MARTINEZ SHAWNA'))
rec('2e: a shared surname alone is never agreement', not PN.agrees('MARTINEZ, SHAWNA', 'MARTINEZ ROBERTO'))
rec('2f: a different last name is never agreement', not PN.agrees('MARTINEZ, SHAWNA', 'MARTIN SHAWNA'))
rec('2g: an entity needs every token', PN.agrees('TESTCO HOLDINGS LLC', 'TESTCO HOLDINGS LLC')
    and not PN.agrees('TESTCO HOLDINGS LLC', 'TESTCO PROPERTIES LLC'))

# ---- 3. resolve_row ---------------------------------------------------------------------------------
def roll(table):
    asked = []

    def fetch(where, n):
        asked.append(where)
        for prefix, rows in table.items():
            if "LIKE '%s%%'" % prefix in where:
                return rows() if callable(rows) else rows
        return []
    fetch.asked = asked
    return fetch


f = roll({'MARTINEZ SHAWNA': [parcel('00424700000000010', 'MARTINEZ SHAWNA E', hs=50000)]})
r = PN.resolve_row({'owner': 'MARTINEZ, SHAWNA E'}, fetch=f, pause=0)
rec('3a: one parcel + homestead is high', r['confidence'] == 'high' and r['needsHuman'] is False and r['rung'] == 'pb-name'
    and r['folio'] == '00424700000000010' and r['addr'] == '12 TEST LN' and r['zip'] == '33431' and r['value'] == 400000, r)
rec('3b: the query is Palm Beach only', all('CO_NO=60' in w for w in f.asked), f.asked)
rec('3c: the mailing address rides along', r.get('mailAddr', '').startswith('9 MAIL RD'), r.get('mailAddr'))
r = PN.resolve_row({'owner': 'MARTINEZ, SHAWNA'}, fetch=roll({'MARTINEZ SHAWNA': [parcel('1', 'MARTINEZ SHAWNA')]}), pause=0)
rec('3d: one parcel without homestead is medium and needs a person', r['confidence'] == 'medium' and r['needsHuman'] is True, r['confidence'])
two = [parcel('1', 'MARTINEZ SHAWNA', '1 A ST'), parcel('2', 'MARTINEZ SHAWNA', '2 B ST'), parcel('3', 'MARTINEZ ROBERTO', '3 C ST')]
r = PN.resolve_row({'owner': 'MARTINEZ, SHAWNA'}, fetch=roll({'MARTINEZ SHAWNA': two}), pause=0)
rec('3e: several parcels is low, with only the agreeing parcels as candidates',
    r['confidence'] == 'low' and [c['folio'] for c in r['candidates']] == ['1', '2'] and 'addr' not in r, r)
r = PN.resolve_row({'owner': 'MARTINEZ, SHAWNA'}, fetch=roll({'MARTINEZ SHAWNA': [parcel('3', 'MARTINEZ ROBERTO')]}), pause=0)
rec('3f: parcels that do not carry the name resolve nothing', r['confidence'] == 'none' and r['candidates'] == [], r)
full = [parcel(str(i), 'MARTINEZ PERSON%d' % i) for i in range(PN.PAGE)]
r = PN.resolve_row({'owner': 'MARTINEZ, SHAWNA'}, fetch=roll({'MARTINEZ SHAWNA': [], 'MARTINEZ': full}), pause=0)
rec('3g: a last name that fills the page is too common to search', r['confidence'] == 'none' and 'too common' in r['evidence'], r)


def dead(where, n):
    raise OSError('no route')


r = PN.resolve_row({'owner': 'MARTINEZ, SHAWNA'}, fetch=dead, pause=0)
rec('3h: an unreachable roll is unknown, not "no such owner"', r['confidence'] == 'none' and r.get('unreachable') is True, r)
r = PN.resolve_row({'owner': 'MARTINEZ, SHAWNA'}, fetch=roll({'MARTINEZ SHAWNA': [parcel('1', 'MARTINEZ SHAWNA', addr='', hs=1)]}), pause=0)
rec('3i: one parcel with no street address is not promoted', r['confidence'] == 'low' and r['needsHuman'] is True, r['confidence'])

# ---- 4. run --------------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    src, out = os.path.join(td, 'lis.json'), os.path.join(td, 'addr.json')
    json.dump([
        {'county': 'PALM BEACH', 'case': '502026CA000001XXXAMB', 'owner': 'MARTINEZ, SHAWNA E'},
        {'county': 'PALM BEACH', 'case': '502026CA000002XXXAMB', 'owner': 'SETTLED, ALREADY'},
        {'county': 'PALM BEACH', 'case': '502026CA000003XXXAMB', 'owner': 'NOBODY, HERE'},
        {'county': 'BROWARD', 'case': 'CACE-26-000001', 'owner': 'MARTINEZ, SHAWNA E'},
        {'county': 'PALM BEACH', 'case': '', 'owner': 'NO, CASE'},
    ], open(src, 'w'))
    settled = {'confidence': 'high', 'rung': 'lp-legal', 'addr': '1 KEEP ST', 'case': '502026CA000002XXXAMB', 'county': 'PALM BEACH'}
    json.dump({'502026CA000002XXXAMB': settled, 'CACE-26-000001': {'confidence': 'none', 'county': 'BROWARD'}}, open(out, 'w'))
    f = roll({'MARTINEZ SHAWNA': [parcel('00424700000000010', 'MARTINEZ SHAWNA E', hs=1)]})
    today = dt.date(2026, 11, 16)
    counts = PN.run(src=src, out=out, fetch=f, pause=0, today=today, say=lambda *a: None)
    got = json.load(open(out))
    e = got['502026CA000001XXXAMB']
    rec('4a: a resolved case is written in the Broward entry shape',
        counts == {'high': 1, 'none': 1} and e['confidence'] == 'high' and e['county'] == 'PALM BEACH'
        and e['lpOwner'] == 'MARTINEZ, SHAWNA E' and e['case'] == '502026CA000001XXXAMB' and e['asked'] == '2026-11-16', (counts, e))
    rec('4b: a case another rung settled is never overwritten', got['502026CA000002XXXAMB'] == settled)
    rec('4c: Broward rows are left alone', got['CACE-26-000001'] == {'confidence': 'none', 'county': 'BROWARD'})
    n1 = len(f.asked)
    PN.run(src=src, out=out, fetch=f, pause=0, today=today + dt.timedelta(days=3), say=lambda *a: None)
    rec('4d: nothing is asked again inside the wait', len(f.asked) == n1, (n1, len(f.asked)))
    PN.run(src=src, out=out, fetch=f, pause=0, today=today + dt.timedelta(days=PN.RETRY_NONE_DAYS), say=lambda *a: None)
    rec('4e: a name the roll did not carry is retried after the wait, a resolved one is not',
        len(f.asked) > n1 and not any('MARTINEZ' in w for w in f.asked[n1:]), f.asked[n1:])

# ---- 5. wiring -----------------------------------------------------------------------------------------------
ref = open(os.path.join(HERE, 'lp_refresh.py'), encoding='utf-8').read()
i_pb, i_val, i_bw = ref.find('palmbeach_resolve.py'), ref.find("run('VALUE (lp_values)'"), ref.find("broward_pin.py')]")
rec('5a: the chain resolves Palm Beach names before it values them', 0 < i_bw < i_pb < i_val, (i_bw, i_pb, i_val))
ll = open(os.path.join(HERE, 'lp_leads.py'), encoding='utf-8').read()
rec('5b: a one-parcel Palm Beach match gets the Broward one-parcel treatment', "in ('BROWARD', 'PALM BEACH')):" in ll)

# ---- 6. the Call Mode card --------------------------------------------------------------------------------------
import call_mode as CM  # noqa: E402
rec('6a: the row carries complaint figures and the recorded mortgage, no party name',
    CM._cmp_slim({'cmp': {'k': 'complaint', 'p': 268083, 'dd': '2025-06-01', 'jr': 3, 'pg': 34, 'rd': 34, 'jrk': ['irs']},
                  'orestface': 360000.0}) == {'p': 268083, 'dd': '2025-06-01', 'jr': 3, 'pg': 34, 'rd': 34, 'rm': 360000})
rec('6b: a Broward notice carries the recorded face', CM._cmp_slim({'cmp': {'face': 135000}}) == {'fc': 135000})
rec('6c: nothing known, nothing shipped', CM._cmp_slim({}) is None and CM._cmp_slim({'cmp': 'x', 'orestface': 'n/a'}) is None)
src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
a = src.index("    var eqStatus = '';")
block = src[a:src.index("    var links = fileLinks(", a)]
rec('6d: the key lists carry cm to the lookup rows', "'rp', 'eqc', 'cm', 'v'" in src and "'eqc','cm','v'" in src)
node = shutil.which('node')
if node:
    js = ("function money(v){return '$'+Math.round(v).toLocaleString('en-US');}\nfunction esc(s){return String(s);}\n"
          "function card(r){ var c=r.eqc; var eq = c && c.q && c.n != null ? 'Net equity ' + money(c.n)"
          " : typeof r.e === 'number' ? r.e + '% equity estimate' : 'equity unknown';\n" + block +
          "return [eq, eqStatus, owedLine]; }\n"
          "console.log(JSON.stringify(["
          "card({lp:1,v:237250,eqc:{f:1,j:null}}),"
          "card({lp:1,v:436000,eqc:{f:1,j:null},cm:{p:268083,dd:'2025-06-01',jr:3,pg:34,rd:34}}),"
          "card({lp:1,eqc:{f:1,j:null},cm:{rm:360000}}),"
          "card({lp:1,cm:{fc:135000}}),"
          "card({v:300000,eqc:{f:1,j:null}}),"
          "card({lp:1,cm:{o:100000,dd:'2026-09-19',pg:18,rd:15}}),"
          "card({lp:1,eqc:{f:1,j:250000,q:1,n:90000},cm:{p:1}})]));")
    out = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
    try:
        g = json.loads(out.stdout.strip())
    except ValueError:
        g = [[out.stderr[:300]]]
    flat = json.dumps(g[:4])
    rec('6e: a fresh filing never says "judgment not posted"', len(g) == 7 and 'judgment not posted' not in flat, g[:1])
    rec('6f: with no complaint read it says so', len(g) > 0 and g[0] == ['equity unknown', 'Recorded liens checked',
        'Complaint not read yet, so what is owed is not known'], g[0])
    rec('6g: with the complaint read it leads with the principal claimed',
        len(g) > 1 and g[1][0] == 'Complaint claims $268,083 principal' and 'In default since 2025-06-01' in g[1][2]
        and '3 other lienholders named' in g[1][2] and 'Read 34 of 34 pages' in g[1][2]
        and 'Value less that principal: $167,917' in g[1][2] and 'Interest and fees are on top' in g[1][2], g[1])
    rec('6h: complaint unread falls back to the recorded mortgage, labelled as recorded',
        len(g) > 2 and g[2][0] == 'Recorded open mortgage $360,000' and 'not the balance' in g[2][2], g[2])
    rec('6i: a Broward notice shows the recorded face', len(g) > 3 and g[3][0] == 'Foreclosed mortgage recorded at $135,000', g[3])
    rec('6j: a case that is not a fresh filing keeps the judgment wording',
        len(g) > 4 and g[4][1] == 'Recorded liens checked, judgment not posted' and g[4][2] == '', g[4])
    rec('6k: an amount stated only as the original loan is labelled as the amount borrowed',
        len(g) > 5 and g[5][0] == 'Complaint: original loan $100,000' and 'amount borrowed' in g[5][2]
        and 'Read 15 of 18 pages' in g[5][2], g[5])
    rec('6l: once a judgment is posted the complaint line steps aside',
        len(g) > 6 and g[6][0] == 'Net equity $90,000' and g[6][2] == '', g[6])
else:
    rec('6e: node not installed; the card was not executed (source checks only)',
        "'Complaint not read yet" in block and 'Complaint claims' in block)

# ---- 7. amounts in words, and the Broward complaint inbox --------------------------------------
import complaint_reader as CR  # noqa: E402
rec('7a: an amount written in words is read', CR.words_to_number('One Hundred Thousand and') == 100000.0
    and CR.words_to_number('Two Hundred Twenty-Five Thousand Five Hundred') == 225500.0)
rec('7b: garbled OCR words are never guessed into a number', CR.words_to_number('ofOne nundred') == 0.0 and CR.words_to_number('') == 0.0)
pw = CR.parse_complaint(['COMPLAINT Defendant executed a Promissory Note in the amount of One Hundred Thousand and 00/100 dollars.'])
rec('7c: a note stated in words gives the original loan, never a balance claimed',
    pw['orig_amount'] == 100000.0 and pw['principal'] is None and pw.get('orig_in_words') is True, pw)
rec('7d: the board row carries the original loan only when no balance is pleaded',
    CR.for_board(dict(pw, status='read', kind='complaint', pages=1, pages_read=1)).get('o') == 100000
    and 'o' not in CR.for_board({'status': 'read', 'kind': 'complaint', 'principal': 5000.0, 'orig_amount': 9000.0}))
with tempfile.TemporaryDirectory() as td:
    open(os.path.join(td, 'CACE-26-000123_COMPLAINT.pdf'), 'wb').write(b'%PDF-')
    open(os.path.join(td, 'cace26000456_complaint.pdf'), 'wb').write(b'%PDF-')
    open(os.path.join(td, 'notes.txt'), 'w').write('x')
    rec('7e: Broward complaints saved from the court site are found by case',
        sorted(CR.broward_inbox_files(td)) == ['CACE-26-000123', 'CACE-26-000456'], sorted(CR.broward_inbox_files(td)))

# ---- 8. v2 (2026-11-18): parcel printed in the filing, non-owner parties, version re-ask -------------
rec('8a: an association, a lender and an unknown-heirs caption are non-owner parties',
    PN.is_nonowner('SAMPLE LAKES HOMEOWNERS ASSOCIATION INC') and PN.is_nonowner('TEST BANK NA')
    and PN.is_nonowner('UNKNOWN HEIRS DEVISEES GRANTEES OF PAT Q TESTER') and not PN.is_nonowner('PAT Q TESTER')
    and not PN.is_nonowner('TESTCO HOLDINGS LLC'))
rec('8b: the person inside an unknown-heirs caption is found',
    PN.named_in_blob('UNKNOWN HEIRS BENEFICIARIES DEVISEES OF THE ESTATE OF PAT Q TESTER, DECEASED') == 'PAT Q TESTER',
    PN.named_in_blob('UNKNOWN HEIRS BENEFICIARIES DEVISEES OF THE ESTATE OF PAT Q TESTER, DECEASED'))
rec('8c: a caption naming nobody yields no name',
    PN.named_in_blob('UNKNOWN HEIRS DEVISEES GRANTEES ASSIGNEES LIENORS CREDITORS TRUSTEES AND ALL OTHER PARTIES') == ''
    and PN.named_in_blob('UNKNOWN TENANT') == '')
c = PN.candidate_owners({'owner': 'SAMPLE LAKES HOMEOWNERS ASSOCIATION INC', 'plaintiff': 'PAT Q TESTER'})
rec('8d: a recorded filing with the association in the owner slot searches the other party', c == ['PAT Q TESTER'], c)
c = PN.candidate_owners({'owner': 'SAMPLE LAKES HOMEOWNERS ASSOCIATION INC', 'plaintiff': 'PAT Q TESTER', 'src': 'court'})
rec('8e: a court caption is never swapped (the plaintiff there IS the plaintiff)', c == [], c)
c = PN.candidate_owners({'owner': 'TEST BANK NA', 'plaintiff': 'SAMPLE LAKES HOMEOWNERS ASSOCIATION INC'})
rec('8f: two non-owner parties give nothing to search', c == [], c)
c = PN.candidate_owners({'owner': 'TESTER, PAT', 'plaintiff': 'TEST BANK NA'})
rec('8g: an ordinary owner is searched as before', c == ['TESTER, PAT'], c)
r = PN.resolve_row({'owner': 'TEST BANK NA', 'plaintiff': 'SAMPLE ASSOCIATION INC'}, fetch=roll({}), pause=0)
rec('8h: nothing to search is "none" with the reason, and the roll is not asked',
    r['confidence'] == 'none' and 'non-owner' in r['evidence'], r)
r = PN.resolve_row({'owner': 'SAMPLE LAKES HOMEOWNERS ASSOCIATION INC', 'plaintiff': 'TESTER, PAT'},
                   fetch=roll({'TESTER PAT': [parcel('7', 'TESTER PAT', hs=50000)]}), pause=0)
rec('8i: the swapped owner resolves', r['confidence'] == 'high' and r['folio'] == '7', r)


def pcn_roll(rows, owner_rows=None):
    asked = []

    def fetch(where, n):
        asked.append(where)
        if 'PARCEL_ID=' in where:
            return [x for x in rows if "'%s'" % x['PARCEL_ID'] in where]
        return owner_rows or []
    fetch.asked = asked
    return fetch


LEG = 'Case Number: 502099CA000001XXXAMB L: 7 SUB: SAMPLE ESTATES PCN: 00-42-47-17-19-000-1730'
rec('8j: the parcel number printed in a recorded filing is read', PN.pcn_of(LEG) == '00424717190001730', PN.pcn_of(LEG))
rec('8k: none, a blank mask, or two different numbers read as none',
    PN.pcn_of('L: 7 SUB: X PCN:') == '' and PN.pcn_of('PCN: __-__-__-__-__-___-____') == ''
    and PN.pcn_of('PCN: 00-42-47-17-19-000-1730 PCN: 00-42-47-17-19-000-1740') == '')
f = pcn_roll([parcel('00424717190001730', 'TESTER PAT', '7 SAMPLE CT')])
r = PN.resolve_row({'owner': 'SAMPLE ESTATES HOMEOWNERS ASSOCIATION INC', 'legal': LEG}, fetch=f, pause=0)
rec('8l: the printed parcel is high, rung pb-pcn, with no owner name needed',
    r['confidence'] == 'high' and r['rung'] == 'pb-pcn' and r['folio'] == '00424717190001730'
    and r['addr'] == '7 SAMPLE CT' and r['ownerMismatch'] is False and len(f.asked) == 1, r)
r = PN.resolve_row({'owner': 'TESTER, PAT', 'legal': LEG}, fetch=pcn_roll([parcel('00424717190001730', 'OTHER PERSON NEW')]), pause=0)
rec('8m: a printed parcel now owned by someone else says so', r['confidence'] == 'high' and r['ownerMismatch'] is True, r)
r = PN.resolve_row({'owner': 'TESTER, PAT', 'legal': LEG},
                   fetch=pcn_roll([], [parcel('9', 'TESTER PAT', hs=1)]), pause=0)
rec('8n: a printed parcel the roll does not have falls back to the name', r['folio'] == '9' and r['rung'] == 'pb-name', r)
r = PN.resolve_row({'owner': 'TESTER, PAT', 'legal': LEG}, fetch=pcn_roll([parcel('00424717190001730', 'TESTER PAT')]),
                   pause=0, co_no=53, rung='md-name')
rec('8o: the parcel-number rung is Palm Beach only', r.get('rung') != 'pb-pcn', r)
today = dt.date(2026, 11, 18)
rec('8p: a v1 "none" is asked again at once; a v2 "none" waits',
    PN._due({'confidence': 'none', 'rung': None, 'asked': '2026-11-17'}, today)
    and not PN._due({'confidence': 'none', 'asked': '2026-11-17', 'v': PN.RESOLVER_V}, today))
rec('8q: a parcel another rung settled is still never overwritten',
    not PN._due({'confidence': 'high', 'rung': 'bcpa', 'asked': '2026-01-01'}, today))
full2 = [parcel(str(i), 'DRALUCK PERSON%d' % i) for i in range(PN.PAGE)]
r = PN.resolve_row({'owner': 'PAULA DRALUCK'},
                   fetch=roll({'DRALUCK PAULA': [], 'PAULA DRALUCK': [parcel('5', 'PAULA DRALUCK', hs=1)], 'DRALUCK': full2}), pause=0)
rec('8r: a filing written LAST FIRST without a comma is found by the name as written', r['folio'] == '5', r)

# ---- 9. the legal description on the county parcel layer (pb-legal) ---------------------------------
g = PN.parse_pb_legal('B: 4 L: 8 SUB: SAMPLE ESTATES # 01 PCN: Case Number: Case Number: 502099CC000001XXXASB')
rec('9a: block, lot and subdivision are read', g == [{'sub': 'SAMPLE ESTATES # 01', 'blk': '4', 'lot': '8', 'unit': ''}], g)
rec('9b: block 4 lot 8 -> the parcel-number pattern', PN.pcn_pattern(g[0]) == '__________004008_', PN.pcn_pattern(g[0]))
rec('9c: lot 173 with no block -> its last four digits', PN.pcn_pattern({'lot': '173'}) == '_____________173_')
rec('9d: a unit stands in for a lot; a letter unit or no number gives no pattern',
    PN.pcn_pattern({'unit': '205'}) == '_____________205_' and PN.pcn_pattern({'unit': 'C123'}) == ''
    and PN.pcn_pattern({}) == '')
rec('9e: the blank form template is not a legal',
    PN.parse_pb_legal('L: BLK: U: SUB: PH: BLDG: SEC: TWN: RNG: WK: COM: LEGAL: Parcel__-__') == [])


def gis_of(rows):
    asked = []

    def gis(where):
        asked.append(where)
        return rows
    gis.asked = asked
    return gis


def gp(pid, owner, sub='SAMPLE ESTATES PL 1', addr='8 SAMPLE CT', hs='Y'):
    return {'PARCEL_NUMBER': pid, 'SUBDIV_NAME': sub, 'OWNER_NAME1': owner, 'SITE_ADDR_STR': addr,
            'CITYNAME': 'BOCA RATON', 'ZIP1': '33431', 'HMSTD_FLG': hs, 'TOTAL_MARKET': 350000.0, 'PADDR1': '9 MAIL RD'}


LEG9 = 'B: 4 L: 8 SUB: SAMPLE ESTATES # 01 PCN: Case Number: 502099CC000001XXXASB'
gq = gis_of([gp('00424706040040080', 'TESTER PAT')])
r = PN.resolve_row({'owner': 'TESTER, PAT', 'legal': LEG9}, fetch=roll({}), pause=0, gis=gq)
rec('9f: one parcel whose owner carries the name is high, rung pb-legal',
    r['confidence'] == 'high' and r['rung'] == 'pb-legal' and r['folio'] == '00424706040040080'
    and r['value'] == 350000 and r['hs'] is True and r['mailAddr'].startswith('9 MAIL RD'), r)
rec('9g: the query names the subdivision and the block/lot pattern',
    "SUBDIV_NAME LIKE 'SAMPLE ESTATES%'" in gq.asked[0] and "PARCEL_NUMBER LIKE '__________004008_'" in gq.asked[0], gq.asked)
r = PN.resolve_row({'owner': 'SAMPLE ESTATES HOMEOWNERS ASSOCIATION INC', 'legal': LEG9}, fetch=roll({}), pause=0,
                   gis=gis_of([gp('00424706040040080', 'TESTER PAT')]))
rec('9h: one parcel and no owner name to compare is medium and needs a person',
    r['confidence'] == 'medium' and r['needsHuman'] is True, r)
r = PN.resolve_row({'owner': 'TESTER, PAT', 'legal': LEG9}, fetch=roll({}), pause=0,
                   gis=gis_of([gp('00424706040040080', 'OTHER PERSON')]))
rec('9i: one parcel owned by someone else is low with the candidate, never promoted',
    r['confidence'] == 'low' and r['candidates'] and 'addr' not in r, r)
r = PN.resolve_row({'owner': 'TESTER, PAT', 'legal': LEG9}, fetch=roll({}), pause=0,
                   gis=gis_of([gp('00424706040040080', 'TESTER PAT', sub='SAMPLE ESTATES PL 3')]))
rec('9j: plat 3 of the same name never answers for plat 1 (falls to the name rung, which finds nothing)',
    r['confidence'] == 'none', r)
two9 = [gp('00424706040040080', 'TESTER PAT'), gp('00424706050040080', 'OTHER PERSON', addr='9 SAMPLE CT')]
r = PN.resolve_row({'owner': 'TESTER, PAT', 'legal': LEG9}, fetch=roll({}), pause=0, gis=gis_of(two9))
rec('9k: two parcels fit and one owner agrees -> that one, high', r['confidence'] == 'high' and r['folio'] == '00424706040040080', r)
r = PN.resolve_row({'owner': 'SAMPLE ASSOCIATION INC', 'legal': LEG9}, fetch=roll({}), pause=0, gis=gis_of(two9))
rec('9l: two parcels and no name -> low with both candidates', r['confidence'] == 'low' and len(r['candidates']) == 2, r)
r = PN.resolve_row({'owner': 'TESTER, PAT', 'legal': LEG9}, fetch=roll({'TESTER PAT': [parcel('9', 'TESTER PAT', hs=1)]}),
                   pause=0, gis=gis_of([]))
rec('9m: nothing on the layer falls back to the owner name', r['rung'] == 'pb-name' and r['folio'] == '9', r)
r = PN.resolve_row({'owner': 'TESTER, PAT', 'legal': LEG9}, fetch=roll({'TESTER PAT': [parcel('9', 'TESTER PAT', hs=1)]}),
                   pause=0, gis=gis_of([gp('00424706040040080', 'OTHER PERSON')]))
rec('9n: a name hit on a DIFFERENT parcel than the legal is never taken (it may be the owner\'s home)',
    r['confidence'] == 'low' and 'addr' not in r and 'different parcel' in r['evidence'], r)
r = PN.resolve_row({'owner': 'TESTER, PAT', 'legal': LEG9},
                   fetch=roll({'TESTER PAT': [parcel('00424706040040080', 'TESTER PAT', hs=1)]}),
                   pause=0, gis=gis_of([gp('00424706040040080', 'TESTER PATRICIA Q', hs='N')]))
rec('9o: a name hit on the legal\'s own parcel stands', r['confidence'] == 'high' and r['folio'] == '00424706040040080', r)

print('\n%d checks, %d failed' % (len(checks), len(fails)))
print('ALL PASS' if not fails else 'FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)
