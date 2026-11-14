"""The equity queue for Broward and Palm Beach (equity_counties.py), beside the Miami-Dade one.

2026-11-13 (Alejandro): "Broward and Palm Beach can do the same ... as miami dade do it for me."
The browser job copies the WHOLE search off the county's page in checksummed chunks; the county's
own scraper reads the copy. A blocked, challenged or half-copied search stores nothing and is never
read as "no liens"; a partial read never replaces a full chain; Palm Beach's checkbox parks the
lead for a person and is never ticked by the job.

Temp DEALFLOW_DIR, invented people, no network, no browser. The page scripts run under node against
a stub page whose data calls answer with fixtures in the shape the two county sites return
(Broward AcclaimWeb Search/GridResults, Landmark Search/GetSearchResults), several pages long.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix='eqc_')
os.environ['DEALFLOW_DIR'] = TMP
os.environ.pop('EQUITY_QUEUE_LIENS_DIR', None)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import equity_queue as Q          # noqa: E402
import equity_counties as C       # noqa: E402
import broward_liens as B         # noqa: E402
import palmbeach_liens as PBL     # noqa: E402

ok, bad = [], []


def rec(name, cond, detail=''):
    (ok if cond else bad).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)[:300]) if detail and not cond else ''))


def quiet(fn, *a, **k):
    b = io.StringIO()
    with contextlib.redirect_stdout(b):
        r = fn(*a, **k)
    return r, b.getvalue()


def items():
    return json.load(open(Q.qpath(), encoding='utf-8'))['items']


def item(case):
    return [i for i in items() if i['case'] == case][0]


Q._desktop_note = lambda q: None            # never write to the real Desktop from a test
Q.time.sleep = lambda s: None
rec('the queue this test writes lives under its temp DEALFLOW_DIR, never ~/DEALFLOW',
    Q.qdir().startswith(TMP) and not Q.qdir().startswith(os.path.join(os.path.expanduser('~'), 'DEALFLOW')), Q.qdir())

# ---- fixtures: the shape of the two county result grids, invented people --------------------------
DAY = 86400000


def brow(i, name='EXAMPLE,JANE', typ='Mortgage', party='From', cross='EXAMPLE BANK NA', cons=0, case=None,
         legal=None, parcel=None, ms=None):
    """One Broward AcclaimWeb Search/GridResults row."""
    return {'Name': name, 'TransactionItemId': 1000 + i, 'U': '', 'Party': party, 'CrossPartyName': cross,
            'RecordDate': '/Date(%d)/' % (ms if ms is not None else 1400000000000 + i * DAY), 'BookType': 'O',
            'BookPage': '%d/%d' % (30000 + i, 100 + i), 'InstrumentNumber': str(110000000 + i), 'TransactionId': i,
            'Comments': None, 'CaseNumber': case, 'Consideration': cons, 'DocLegalDescription': legal,
            'ParcelNumber': parcel, 'NumberOfDirectNames': 1, 'NumberOfReverseNames': 1,
            'DocTypeDescription': typ, 'GUID': 'g-%d' % i}


def pbrow(i, col4, direct, reverse, typ='MORTGAGE', date='06/01/2019', legal=''):
    """One Landmark Search/GetSearchResults row: an object keyed '0'..'30'."""
    r = {str(k): '' for k in range(31)}
    r.update({'4': col4, '5': 'nobreak_' + direct, '6': 'nobreak_' + reverse, '7': date, '9': typ, '10': 'O',
              '11': str(31000 + i), '12': str(200 + i), '13': str(20190000000 + i), '15': 'legalfield_' + legal})
    return r


def pack(county, rows):
    """What the page script does, in Python: for the tests that need a hand-made payload."""
    cols = C.H[C.norm(county)]['cols']
    seen, out = {}, []
    for r in rows:
        p = []
        for name, how in cols:
            v = r.get(name)
            if how == 'd' and v is not None:
                v = str(v)
                if v in seen:
                    v = seen[v]
                else:
                    seen[v] = len(seen)
            elif how == 'm' and isinstance(v, str) and v.startswith('/Date(') and v.endswith(')/'):
                v = int(v[6:-2])
            p.append(v)
        while p and p[-1] is None:
            p.pop()
        out.append(p)
    return out


def payload(county, rows, per=6, total=None, **meta):
    packed = pack(county, rows)
    cuts = [packed[i:i + per] for i in range(0, len(packed), per)]
    hdr = dict({'hdr': 1, 'n': len(packed), 'of': len(cuts), 'types': [],
                'total': len(packed) if total is None else total}, **meta)
    return [hdr] + [{'k': k, 'of': len(cuts), 'sum': Q.checksum(c), 'rows': c} for k, c in enumerate(cuts)]


# ---- queueing, per county ------------------------------------------------------------------------
BW1, BW2, BW3, BWX = '2099-BRW-000001', '2099-BRW-000002', '2099-BRW-000003', '2099-BRW-000009'
PB1, PB2, PB3 = '2099PB000001XXXXMB', '2099PB000002XXXXMB', '2099PB000003XXXXMB'
MD1 = '2099-000001-CA-01'
L_BW1 = {'case': BW1, 'owners': 'EXAMPLE, JANE Q', 'folio': '494100000010', 'judg': 0, 'defs': 'Sample, John'}
L_BW2 = {'case': BW2, 'owners': '(owner via title search)', 'folio': '', 'judg': 0,
         'defs': 'Sample, John; Example Condominium Association Inc; Sample, Mary'}
L_BW3 = {'case': BW3, 'owners': 'EXAMPLE HOLDINGS 777 LLC', 'folio': '', 'judg': 0, 'defs': ''}
L_BWX = {'case': BWX, 'owners': '(owner via title search)', 'folio': '', 'judg': 0, 'defs': ''}
L_PB1 = {'case': PB1, 'owners': 'EXAMPLE JANE Q', 'folio': '00-42-46-01-00-000-0010', 'judg': 0, 'ftype': 'MORTGAGE'}
L_PB2 = {'case': PB2, 'owners': '1 EXAMPLE PL APT 2', 'folio': '00424601000000020', 'judg': 0, 'ftype': 'HOA'}
L_PB3 = {'case': PB3, 'owners': 'SAMPLE JOHN', 'folio': '', 'judg': 0, 'ftype': 'MORTGAGE'}

Q.add(MD1, 'JANE EXAMPLE', 'EXAMPLE', 'JANE', '0100000000010', reason='not read', names=[('EXAMPLE', 'JANE')])
rec('a Broward lead with an owner is queued with the nightly\'s own name search',
    C.queue_lead('BROWARD', L_BW1, 'the search was blocked') is True)
rec('a Broward lead whose owner is not known yet is queued by the people the case names',
    C.queue_lead('Broward', L_BW2, 'owner not known yet') is True)
rec('a Broward company owner is queued under its full name', C.queue_lead('BROWARD', L_BW3, 'cut at 400 rows') is True)
rec('a Broward lead with no owner and no person to search is not queued (nothing to type)',
    C.queue_lead('BROWARD', L_BWX, 'owner not known yet') is False)
rec('Palm Beach leads are queued: by parcel and name, by parcel alone, by name alone',
    all(C.queue_lead('PALM BEACH', x, 'not read: captcha-gated') for x in (L_PB1, L_PB2, L_PB3)))
rec('flush writes all of them beside the Miami-Dade lead', Q.flush() == 7 and len(items()) == 7)
s_bw = {i['case']: i for i in items()}
rec('each county\'s row says what to type into that county\'s search',
    s_bw[BW1]['searches'] == [{'kind': 'name', 'q': 'EXAMPLE, JANE'}]
    and s_bw[BW2]['searches'] == [{'kind': 'defendant', 'q': 'SAMPLE, JOHN'}, {'kind': 'defendant', 'q': 'SAMPLE, MARY'}]
    and s_bw[BW3]['searches'] == [{'kind': 'name', 'q': 'EXAMPLE HOLDINGS 777 LLC'}]
    and s_bw[PB1]['searches'] == [{'kind': 'parcel', 'q': '00424601000000010'}, {'kind': 'name', 'q': 'EXAMPLE JANE'}]
    and s_bw[PB2]['searches'] == [{'kind': 'parcel', 'q': '00424601000000020'}]
    and s_bw[PB3]['searches'] == [{'kind': 'name', 'q': 'SAMPLE JOHN'}], {k: v.get('searches') for k, v in s_bw.items()})
rec('a lead searched by defendants is marked owner not known; the others are known',
    s_bw[BW2]['owner_known'] is False and s_bw[BW1]['owner_known'] is True and s_bw[PB2]['owner_known'] is True)
rec('county is stored in one spelling whatever the caller wrote',
    {i['county'] for i in items()} == {'MIAMI-DADE', 'BROWARD', 'PALM BEACH'})

# ---- Miami-Dade is what it was -------------------------------------------------------------------
_r, out = quiet(Q.cmd_next, 25)
rec('Miami-Dade unchanged: plain `next` hands out the Miami-Dade lead only, in the same shape',
    json.loads(out) == [{'case': MD1, 'names': [['EXAMPLE', 'JANE']], 'has_folio': True}], out)
mdjs = Q.js_for(MD1, -1)
rec('Miami-Dade unchanged: its page script is still the getStandardRecords one',
    mdjs.startswith('var out; { const K = -1,') and 'getStandardRecords' in mdjs and 'GridResults' not in mdjs)
rec('Miami-Dade unchanged: its rows are still refused on a bad file and its item untouched',
    Q.accept_search(MD1, [{'hdr': 1, 'n': 0, 'of': 0}])[0] is False and item(MD1)['status'] == 'pending')
_r, out = quiet(Q.cmd_next, 25, 'BROWARD')
nb = json.loads(out)
rec('`next --county BROWARD` hands out the Broward leads with their searches',
    [x['case'] for x in nb] == [BW1, BW2, BW3] and nb[1]['searches'][1] == {'index': 1, 'kind': 'defendant', 'q': 'SAMPLE, MARY'}, out)
_r, out = quiet(Q.cmd_next, 25, 'palm beach')
rec('`next --county "PALM BEACH"` hands out the Palm Beach leads', [x['case'] for x in json.loads(out)] == [PB1, PB2, PB3], out)

# ---- the page scripts, run under node against a stub of each county's page ----------------------
NODE = os.environ.get('NODE_BIN', 'node')
HARNESS = r"""
const fs = require('fs');
const S = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const vis = e => Object.assign({offsetParent: {}, offsetWidth: 300, offsetHeight: 150, children: {length: 0}}, e);
globalThis.window = globalThis;
globalThis.location = {hostname: S.host, pathname: S.path};
globalThis.document = {title: S.title || '', body: {innerText: S.body || ''},
  querySelector: s => (S.sel && s in S.sel) ? S.sel[s] : null,
  querySelectorAll: s => ((S.all && S.all[s]) || []).map(vis)};
const calls = [];
globalThis.fetch = async (u, o) => {
  const b = String((o && o.body) || ''); calls.push(u);
  const res = (status, text) => ({ok: status === 200, status: status, text: async () => text});
  if(u === '/AcclaimWeb/Search/GridResults'){
    const p = Number(/page=(\d+)/.exec(b)[1]), size = Number(/size=(\d+)/.exec(b)[1]);
    if(S.fail_page === p) return res(503, '<html>Just a moment</html>');
    return res(200, JSON.stringify({data: S.rows.slice((p - 1) * size, p * size), total: S.total == null ? S.rows.length : S.total}));
  }
  if(u === '/Search/ShowCaptcha') return res(200, S.gate || 'False');
  if(u === '/Search/GetSearchResults'){
    const st = Number(/start=(\d+)/.exec(b)[1]), len = Number(/length=(\d+)/.exec(b)[1]);
    if(S.fail_start === st) return res(200, '<title>Error</title>');
    return res(200, JSON.stringify({draw: 1, recordsTotal: S.total == null ? S.rows.length : S.total, recordsFiltered: S.rows.length, data: S.rows.slice(st, st + len)}));
  }
  return res(404, '');
};
const run = src => new AsyncFunction(src.replace(/\nout\s*$/, '\nreturn out'))();
(async () => {
  const out = [await run(S.hdr)];
  let h = {}; try { h = JSON.parse(out[0]); } catch(e) {}
  for(let k = 0; k < (h.of || 0) + (S.extra || 0); k++) out.push(await run(S.chunk.replace('K = 123456', 'K = ' + k)));
  if(S.chunk_only) out.push(await run(S.chunk.replace('K = 123456', 'K = 0')));
  process.stdout.write(JSON.stringify({out: out, calls: calls}));
})();
"""
hp = os.path.join(TMP, 'harness.js')
io.open(hp, 'w', encoding='utf-8').write(HARNESS)


def page(case, **scn):
    """Run the header script and every chunk script for `case` against a stub page. -> (answers, calls)"""
    it = item(case)
    scn.setdefault('hdr', Q.js_for(case, -1))
    scn['chunk'] = Q.js_for(case, 123456)
    if C.norm(it['county']) == 'BROWARD':
        scn.setdefault('host', 'officialrecords.broward.org')
        scn.setdefault('path', '/AcclaimWeb/Search/SearchTypeName')
        scn.setdefault('body', 'Displaying items 1 - 500 of 620')
        scn.setdefault('sel', {'[name=SearchOnName]': {'value': 'x'}, '[name=PartyType]': {'value': 'Both'},
                               '[name="DocTypesDisplay-input"]': {'value': 'All'}, '[name=BookTypesDisplay]': {'value': 'All'},
                               '[name=RecordDateFrom]': {'value': '1/1/1977'}})
        scn.setdefault('all', {'iframe': [{'src': 'https://www.google.com/recaptcha/api2/anchor?k=x', 'offsetWidth': 256, 'offsetHeight': 60}]})
    else:
        scn.setdefault('host', 'erec.mypalmbeachclerk.com')
        scn.setdefault('path', '/search/index')
        scn.setdefault('all', {'#resultsTable tbody tr': [{}], 'select[id^=numberOfRecords]': [{'value': '700'}]})
    sp = os.path.join(TMP, 'scn.json')
    json.dump(scn, io.open(sp, 'w', encoding='utf-8'))
    p = subprocess.run([NODE, hp, sp], capture_output=True, text=True, timeout=120, encoding='utf-8')
    if p.returncode != 0:
        return [json.dumps({'err': 'node: ' + p.stderr[-300:]})], []
    r = json.loads(p.stdout)
    return [json.loads(x) for x in r['out']], r['calls']


# Broward: 620 rows -> two pages of the grid (500 + 120), prefix matches of the name, several doc types
bw_rows = []
for i in range(620):
    nm = ('EXAMPLE,JANE', 'EXAMPLE,JANE Q', 'EXAMPLE,JANET')[i % 3]
    if i == 5:
        bw_rows.append(brow(i, nm, 'Mortgage', 'From', 'EXAMPLE BANK NA', 250000, ms=1400000000000 + 700 * DAY))   # newer than every release
    elif i == 6:
        bw_rows.append(brow(i, 'EXAMPLE,JANE', 'Lis Pendens', 'From', 'EXAMPLE BANK NA', 0, case='CACE-99-000001', legal='LOT 1 EXAMPLE SUB', parcel='494100000010'))
    else:
        bw_rows.append(brow(i, nm, ('Lien', 'Notice', 'Release/Revoke/Satisfy or Terminate', 'Final Judgment')[i % 4],
                            ('To', 'From')[i % 2], 'SAMPLE CREDITOR %d' % (i % 40), 0.5 if i == 9 else (1234.56 if i % 50 == 0 else 0)))
bw_rows[5]['Name'] = 'EXAMPLE,JANE'
ans, calls = page(BW1, rows=bw_rows)
hdr = ans[0]
rec('Broward reader: the header reports every row of both grid pages and matches the county\'s total',
    hdr.get('hdr') == 1 and hdr.get('n') == 620 and hdr.get('total') == 620 and calls.count('/AcclaimWeb/Search/GridResults') == 2, hdr)
rec('Broward reader: every answer fits the browser tool\'s size limit',
    all(len(json.dumps(a, separators=(',', ':'))) < 1000 for a in ans), max(len(json.dumps(a, separators=(',', ':'))) for a in ans))
rec('Broward reader: the page computes the same checksum Python does, chunk by chunk',
    len(ans) == hdr['of'] + 1 and all(a['sum'] == Q.checksum(a['rows']) for a in ans[1:]))
rec('Broward reader: the page packs rows exactly as the Python reference does',
    [r for a in ans[1:] for r in a['rows']] == pack('BROWARD', bw_rows))
okk, msg = Q.accept_search(BW1, ans, 0)
rec('Broward: the whole search is stored', okk and 'Every search on this lead is stored' in msg, msg)
m = json.load(open(Q.rows_path(BW1), encoding='utf-8'))['parts'][0]['models']
want = {k for k, _ in C.COLS_BROWARD}
rec('Broward: rows are rebuilt to the scraper\'s own fields, value for value',
    len(m) == 620 and all(set(x) == want for x in m) and all(m[i][k] == bw_rows[i][k] for i in range(620) for k in want),
    [(i, k) for i in range(620) for k in want if m[i][k] != bw_rows[i][k]][:3])
rec('Broward: the rows file says how many rows and that it is not capped',
    item(BW1)['status'] == 'rows' and item(BW1)['rows'] == 620)

ans, calls = page(BW3, rows=[], body='Please start your search now Search Tips')
rec('Broward reader: a page where no search was run gives no rows, it does not read as an empty result',
    'err' in ans[0] and 'no search' in ans[0]['err'] and not calls, ans[0])
ans, _c = page(BW3, rows=bw_rows[:3], all={'iframe': [{'src': 'https://www.google.com/recaptcha/api2/bframe?x=1', 'offsetWidth': 400, 'offsetHeight': 580}]})
rec('Broward reader: a visible challenge stops the read (the job never answers one)', ans[0].get('err') == 'captcha', ans[0])
rec('Broward: a challenge answer stores nothing and the lead keeps waiting',
    Q.accept_search(BW3, ans, 0)[0] is False and item(BW3)['status'] == 'pending' and not os.path.exists(Q.rows_path(BW3)))
ans, _c = page(BW3, rows=bw_rows, fail_page=2)
rec('Broward reader: a page of the grid that does not answer means no header, not a half result',
    'err' in ans[0] and len(ans) == 1, ans[0])
ans, _c = page(BW3, rows=bw_rows[:3], sel={'[name=SearchOnName]': {'value': 'x'}, '[name=PartyType]': {'value': 'Direct'},
                                           '[name="DocTypesDisplay-input"]': {'value': 'All'}, '[name=BookTypesDisplay]': {'value': 'All'}})
rec('Broward reader: a narrowed search form (not All / All / All) is refused: it would not be the whole search',
    'err' in ans[0] and 'defaults' in ans[0]['err'], ans[0])
ans, _c = page(BW3, rows=bw_rows[:3], chunk_only=1, hdr=Q.js_for(BW3, 0))
rec('a chunk script on a page with no header read answers with an error, never with old rows', all('err' in a for a in ans), ans)

# ---- accept_search: all or nothing ---------------------------------------------------------------
co_rows = [brow(i, 'EXAMPLE HOLDINGS 777 LLC', 'Lien', 'From', 'SAMPLE CITY') for i in range(14)]
good = payload('BROWARD', co_rows)
x = json.loads(json.dumps(good)); x[2]['rows'][1][7] = x[2]['rows'][1][7][:-1] + '0'       # one digit of an instrument number
rec('checksum failure: one mis-copied digit refuses the whole search and nothing is stored',
    Q.accept_search(BW3, x, 0)[0] is False and 'checksum mismatch in chunk 1' in Q.accept_search(BW3, x, 0)[1]
    and not os.path.exists(Q.rows_path(BW3)) and item(BW3)['status'] == 'pending')
x = json.loads(json.dumps(good)); x.pop()
rec('a missing chunk refuses the search', Q.accept_search(BW3, x, 0)[0] is False)
rec('a header whose row count is below the county\'s total is refused: that is not the whole search',
    Q.accept_search(BW3, payload('BROWARD', co_rows, total=900), 0)[0] is False)
rec('rows that are not for the name searched are refused (the page was still showing another search)',
    Q.accept_search(BW3, payload('BROWARD', bw_rows[:12]), 0)[0] is False and not os.path.exists(Q.rows_path(BW3)))
x = json.loads(json.dumps(good)); x[1]['rows'][0][1] = 999; x[1]['sum'] = Q.checksum(x[1]['rows'])
rec('a row pointing at text that was never sent is refused', Q.accept_search(BW3, x, 0)[0] is False)
rec('a search index the lead does not have is refused', Q.accept_search(BW3, good, 3)[0] is False)
rec('a good copy is stored', Q.accept_search(BW3, good, 0)[0] is True and item(BW3)['status'] == 'rows')

# owner not known: one of two defendant searches stored
okk, msg = Q.accept_search(BW2, payload('BROWARD', [brow(i, 'SAMPLE,JOHN', 'Mortgage', 'From', 'EXAMPLE BANK NA', 100000) for i in range(3)]), 0)
rec('a lead with two searches says which is still to run', okk and 'Searches still to run: [1]' in msg, msg)

# ---- Palm Beach: the checkbox ---------------------------------------------------------------------
ans, calls = page(PB1, rows=[], gate='True', all={'#resultsTable tbody tr': [], 'select[id^=numberOfRecords]': [{'value': '200'}]})
rec('Palm Beach reader: checkbox required and no result on the page -> captcha, and the results call is never made',
    ans[0].get('err') == 'captcha' and '/Search/GetSearchResults' not in calls, (ans[0], calls))
ans, calls = page(PB1, rows=[], gate='False', all={'div,span,label,p': [{'textContent': 'Invalid Captcha'}], '#resultsTable tbody tr': [],
                                                    'select[id^=numberOfRecords]': [{'value': '200'}]})
rec('Palm Beach reader: a page saying Invalid Captcha -> captcha', ans[0].get('err') == 'captcha', ans[0])
rec('Palm Beach: a captcha answer stores nothing', Q.accept_search(PB1, ans, 0)[0] is False and not os.path.exists(Q.rows_path(PB1)))
rec('the page scripts never touch the checkbox: no click, no token read, no solver',
    not any(w in (C.JS_PB + C.JS_BROWARD + C.JS_CHUNK) for w in ('.click(', 'g-recaptcha-response', 'grecaptcha', '2captcha', 'getResponse')))
r, out = quiet(C.mark_human, 'PALM BEACH', '', 'checkbox on the search form')
rec('`captcha --county "PALM BEACH"` parks every waiting Palm Beach lead as needs_human_captcha',
    r == 3 and all(item(c)['status'] == C.HUMAN for c in (PB1, PB2, PB3)), r)
rec('...and touches no other county', item(BW3)['status'] == 'rows' and item(MD1)['status'] == 'pending' and item(BW1)['status'] == 'rows')
_r, out = quiet(Q.cmd_next, 25, 'PALM BEACH')
rec('parked leads are not handed to the unattended job', json.loads(out) == [], out)
_r, out = quiet(Q.cmd_next, 25, 'PALM BEACH', True)
rec('`next --human` hands them out for a session with a person at the screen',
    [x['case'] for x in json.loads(out)] == [PB1, PB2, PB3] and json.loads(out)[0]['status'] == C.HUMAN, out)
rec('needs_human_captcha is counted on its own, not as read and not as a miss',
    Q._counts({'items': items()})['needs_human_captcha'] == 3 and Q._counts({'items': items()})['miss'] == 0
    and Q._counts({'items': items()})['done'] == 0)

# Palm Beach, person present: parcel search (2 pages, 520 rows) then the name search
PCN = '00424601000000010'
pb_parcel = [pbrow(i, PCN, 'EXAMPLE JANE Q', 'EXAMPLE BANK NA', ('MORTGAGE', 'SATISFACTION', 'LIEN', 'LIS PENDENS')[i % 4],
                   legal='Case Number: 2099CA%06d' % i if i % 4 == 3 else 'LOT 1') for i in range(520)]
ans, calls = page(PB1, rows=pb_parcel, gate='False')
hdr = ans[0]
rec('Palm Beach reader: every page of the result is read (500 + 20) and matches the county\'s total',
    hdr.get('n') == 520 and hdr.get('total') == 520 and hdr.get('cap') == 700 and calls.count('/Search/GetSearchResults') == 2, hdr)
rec('Palm Beach reader: answers fit the size limit and checksums agree with Python',
    all(len(json.dumps(a, separators=(',', ':'))) < 1000 for a in ans) and all(a['sum'] == Q.checksum(a['rows']) for a in ans[1:]))
okk, msg = Q.accept_search(PB1, ans, 0)
rec('Palm Beach: the parcel search is stored for a lead that was parked for a person', okk and 'Searches still to run: [1]' in msg, msg)
m = json.load(open(Q.rows_path(PB1), encoding='utf-8'))['parts'][0]['models']
rec('Palm Beach: rows are rebuilt to the Landmark columns the scraper reads, value for value',
    len(m) == 520 and all(m[i][k] == pb_parcel[i][k] for i in range(520) for k, _ in C.COLS_PB))
rec('Palm Beach: a parcel result for another parcel is refused',
    Q.accept_search(PB2, payload('PALM BEACH', pb_parcel[:8], cap=700), 0)[0] is False)
pb_name = [pbrow(600 + i, 'EXAMPLE JANE Q', 'EXAMPLE JANE Q', 'SAMPLE HOA INC', 'LIEN') for i in range(4)]
ans, _c = page(PB1, rows=pb_parcel, fail_start=500, gate='False')
rec('Palm Beach reader: a results call that fails part-way means no header (half-copied is nothing)', 'err' in ans[0] and len(ans) == 1, ans[0])
rec('Palm Beach: the name search is stored', Q.accept_search(PB1, payload('PALM BEACH', pb_name, cap=700), 1)[0] is True)
# a search that hit the form's record limit
capped = [pbrow(i, '00424601000000020', 'SAMPLE OWNER', 'EXAMPLE BANK NA') for i in range(200)]
rec('Palm Beach: a search that returned as many rows as the form\'s record limit is stored but not a full read',
    Q.accept_search(PB2, payload('PALM BEACH', capped, per=9, cap=200), 0)[0] is True
    and C.docs_file(item(PB2))[1] is False)

# ---- full or not ----------------------------------------------------------------------------------
p1, f1 = C.docs_file(item(BW1))
rec('Broward: every search stored, owner known -> full', f1 is True and json.load(open(p1, encoding='utf-8'))['full'] is True)
p2, f2 = C.docs_file(item(BW2))
d2 = json.load(open(p2, encoding='utf-8'))
rec('Broward: one of two searches stored, owner not known -> never full, and the file says who it was read as',
    f2 is False and d2['full'] is False and d2['owner_as'] == 'SAMPLE, JOHN' and d2['searches'] == 1 and d2['searches_on_lead'] == 2)
p3, f3 = C.docs_file(item(PB1))
rec('Palm Beach: parcel and name both stored -> full', f3 is True and len(json.load(open(p3, encoding='utf-8'))['parts']) == 2)

# ---- the county scrapers read the copy (--docs-file), in a scratch folder ---------------------------
LD = os.path.join(TMP, 'liens')
os.makedirs(LD)
json.dump([L_BW1, L_BW2, L_BW3, L_BWX], open(os.path.join(LD, 'broward_leads.json'), 'w'))
json.dump([L_PB1, L_PB2, L_PB3], open(os.path.join(LD, 'palmbeach_leads.json'), 'w'))
B_OUT, P_OUT = os.path.join(LD, 'broward_liens.json'), os.path.join(LD, 'palmbeach_liens.json')
B.OUT0, B.LEADS0, PBL.OUT0, PBL.LEADS0 = B.OUT, B.LEADS, PBL.OUT, PBL.LEADS


def scraper(mod, case, path, out, leads):
    argv = sys.argv
    sys.argv = [mod.__name__ + '.py', '--case', case, '--docs-file', path, '--out', out, '--leads', leads] + (
        ['--no-links'] if mod is B else [])
    try:
        return quiet(mod.main)
    finally:
        sys.argv = argv
        mod.OUT, mod.LEADS = mod.OUT0, mod.LEADS0


def saved(path, case):
    return (json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}).get(case)


rc, out = scraper(B, BW1, p1, B_OUT, os.path.join(LD, 'broward_leads.json'))
h = saved(B_OUT, BW1) or {}
rec('Broward scraper: a full browser copy is analyzed and saved as a whole-search chain',
    rc == 0 and ('  ok  ' + BW1) in out and h.get('browser_read') and not h.get('truncated') and h.get('n_raw') == 620
    and h.get('open_count') == 1 and h.get('conf') in ('ok', 'low') and h.get('nrec', 0) > 0, (rc, out[-200:], {k: h.get(k) for k in ('conf', 'nrec', 'open_count', 'truncated')}))
rec('Broward scraper: it wrote only to the scratch folder', not os.path.exists(os.path.join(HERE, 'broward_liens.json')) or
    BW1 not in json.load(open(os.path.join(HERE, 'broward_liens.json'), encoding='utf-8')))
rc, out = scraper(B, BW2, p2, B_OUT, os.path.join(LD, 'broward_leads.json'))
h2 = saved(B_OUT, BW2) or {}
rec('Broward scraper: a partial / owner-unknown read is saved TRUNCATED and never as ok',
    rc == 0 and h2.get('truncated') is True and h2.get('conf') != 'ok' and h2.get('owner_assumed') and 'truncated' in out, (rc, out[-200:], h2.get('conf')))
# no overwrite: a full chain is already saved for BW2's case, a partial read arrives
full_chain = dict(h, owner='KEEP ME')
o = json.load(open(B_OUT, encoding='utf-8')); o[BW2] = full_chain; json.dump(o, open(B_OUT, 'w'))
rc, out = scraper(B, BW2, p2, B_OUT, os.path.join(LD, 'broward_leads.json'))
rec('Broward scraper: a partial read never replaces a full chain, and prints no ok line',
    saved(B_OUT, BW2) == full_chain and ('  ok  ' + BW2) not in out and 'not written over' in out, out[-200:])
empty = os.path.join(TMP, 'empty.docs.json')
json.dump({'case': BW1, 'full': True, 'searched': 'name EXAMPLE, JANE', 'owner_as': '', 'parts': [{'kind': 'name', 'q': 'EXAMPLE, JANE', 'rows': []}]}, open(empty, 'w'))
before = saved(B_OUT, BW1)
rc, out = scraper(B, BW1, empty, B_OUT, os.path.join(LD, 'broward_leads.json'))
rec('Broward scraper: an empty read never replaces a saved chain ("found nothing" is not "no liens")',
    saved(B_OUT, BW1) == before and ('  ok  ' + BW1) not in out, out[-200:])
rc, out = scraper(B, BWX, empty, B_OUT, os.path.join(LD, 'broward_leads.json'))
rec('Broward scraper: a lead with no owner to read the chain as writes nothing', rc == 3 and saved(B_OUT, BWX) is None, (rc, out))
rc, out = scraper(B, '2099-BRW-NOPE', empty, B_OUT, os.path.join(LD, 'broward_leads.json'))
rec('Broward scraper: an unknown case writes nothing', rc == 3 and saved(B_OUT, '2099-BRW-NOPE') is None)

rc, out = scraper(PBL, PB1, p3, P_OUT, os.path.join(LD, 'palmbeach_leads.json'))
hp1 = saved(P_OUT, PB1) or {}
rec('Palm Beach scraper: a full browser copy is analyzed with the nightly\'s own chain rules (unpriced, never "clean")',
    rc == 0 and ('  ok  ' + PB1) in out and hp1.get('browser_read') and not hp1.get('truncated') and hp1.get('captcha') == 'browser'
    and hp1.get('conf') == 'unpriced' and hp1.get('mtg_recorded', 0) > 0 and hp1.get('n_raw', 0) > 500, (rc, out[-200:], {k: hp1.get(k) for k in ('conf', 'mtg_recorded', 'n_raw')}))
pc, fc = C.docs_file(item(PB2))
rc, out = scraper(PBL, PB2, pc, P_OUT, os.path.join(LD, 'palmbeach_leads.json'))
rec('Palm Beach scraper: a capped read is saved TRUNCATED', rc == 0 and (saved(P_OUT, PB2) or {}).get('truncated') is True)
o = json.load(open(P_OUT, encoding='utf-8')); o[PB2] = dict(hp1, owner='KEEP ME'); json.dump(o, open(P_OUT, 'w'))
rc, out = scraper(PBL, PB2, pc, P_OUT, os.path.join(LD, 'palmbeach_leads.json'))
rec('Palm Beach scraper: a truncated read never replaces a full chain',
    saved(P_OUT, PB2)['owner'] == 'KEEP ME' and ('  ok  ' + PB2) not in out, out[-200:])

# ---- the nightly never undoes a whole-search browser read, and never re-queues it -----------------
today = B.datetime.date.today()
fresh = {'browser_read': today.isoformat(), 'conf': 'low'}
rec('a whole-search browser read from the last days is fresh; a truncated or old one is not',
    B._browser_fresh(fresh) and not B._browser_fresh(dict(fresh, truncated=True))
    and not B._browser_fresh({'browser_read': (today - B.datetime.timedelta(days=B.BROWSER_KEEP_DAYS + 1)).isoformat()})
    and not B._browser_fresh({}))
cand = dict((r['case'], why) for r, why in B.queue_candidates(
    [L_BW1, L_BW2, L_BW3, L_BWX], {BW1: {'truncated': True, 'conf': 'low'}, BW3: {'truncated': True, 'browser_read': today.isoformat()}}))
rec('the Broward scraper picks for the browser: owner-unknown leads and chains cut at 400 rows, not ones the browser already read',
    set(cand) == {BW1, BW2, BWX} and '400' in cand[BW1], cand)
src_b = io.open(os.path.join(HERE, 'broward_liens.py'), encoding='utf-8').read()
rec('the Broward nightly keeps a fresh whole-search browser read when its own read is narrower',
    "if _browser_fresh(out.get(case)) and (res.get('truncated') or res['conf'] == 'none'):" in src_b)

# ---- scrapers can not reach the live queue from a test or a scratch run ---------------------------
rec('a scraper pointed at scratch files does not queue (test isolation)',
    C.live_ok(B.__file__, B_OUT, B.LEADS, 'broward_liens.json', 'broward_leads.json') is False
    and C.live_ok(PBL.__file__, PBL.OUT, os.path.join(LD, 'palmbeach_leads.json'), 'palmbeach_liens.json', 'palmbeach_leads.json') is False)
env = dict(os.environ); env.pop('DEALFLOW_DIR', None)
p = subprocess.run([sys.executable, '-c',
                    'import equity_counties as C, broward_liens as B, os; '
                    'print(int(C.live_ok(B.__file__, B.OUT, B.LEADS, "broward_liens.json", "broward_leads.json")), '
                    'int(os.path.isfile(".git")))'], cwd=HERE, env=env, capture_output=True, text=True, timeout=60)
flag = (p.stdout or '').split()[-2:]
rec('a linked git worktree with no DEALFLOW_DIR never reaches the live queue; the engine checkout does',
    len(flag) == 2 and (flag == ['0', '1'] or flag == ['1', '0']), (p.stdout, p.stderr[-200:]))
os.environ['PYTEST_CURRENT_TEST'] = 'x'
rec('under pytest nothing is queued', C.live_ok(B.__file__, B.OUT, B.LEADS, 'broward_liens.json', 'broward_leads.json') is False)
os.environ.pop('PYTEST_CURRENT_TEST')
env2 = dict(os.environ); env2.pop('DEALFLOW_DIR', None)
p = subprocess.run([sys.executable, '-c', 'import equity_queue as Q, equity_counties as C; '
                    'C.queue_lead("BROWARD", {"case": "2099-BRW-000777", "owners": "EXAMPLE, JANE"}, "t"); print(len(Q._PENDING))'],
                   cwd=HERE, env=env2, capture_output=True, text=True, timeout=60)
rec('an invented 2099- case is never queued outside a scratch DEALFLOW_DIR', (p.stdout or '').strip().endswith('0'), (p.stdout, p.stderr[-200:]))

# ---- finish: each county's own scraper, nothing read as "no liens" --------------------------------
calls = []


class _P:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def fake_run(argv, **k):
    calls.append(argv)
    case = argv[argv.index('--case') + 1]
    return _P(0, '  ..  %s a full chain is already saved\n' % case) if case == BW3 else _P(0, '  ok  %s   3 recs\n' % case)


real_run = Q.subprocess.run
Q.subprocess.run = fake_run
Q.accept_search(MD1, [{'hdr': 1, 'n': 1, 'of': 1, 'dsfx': True, 'bpd': True, 'types': ['MORTGAGE - MOR']},
                      {'k': 0, 'of': 1, 'sum': Q.checksum([[0, 100000000010, 'EXAMPLE BANK NA', 'EXAMPLE JANE', 1, 2, '6/1/2019', 3, 0, 0, 'S']]),
                       'rows': [[0, 100000000010, 'EXAMPLE BANK NA', 'EXAMPLE JANE', 1, 2, '6/1/2019', 3, 0, 0, 'S']]}])
rec('finish exits 0', quiet(Q.cmd_finish)[0] == 0)
by = {c[c.index('--case') + 1]: c for c in calls}
rec('finish: Broward leads go to broward_liens.py --docs-file, Palm Beach to palmbeach_liens.py --docs-file',
    'broward_liens.py' in by[BW1] and '--docs-file' in by[BW1] and 'palmbeach_liens.py' in by[PB1] and '--docs-file' in by[PB1], by)
rec('Miami-Dade unchanged: its lead still goes to records_liens.py --cached-only --models-file',
    'records_liens.py' in by[MD1] and '--cached-only' in by[MD1] and '--models-file' in by[MD1] and '--docs-file' not in by[MD1], by[MD1])
st = {i['case']: (i['status'], i.get('full')) for i in items()}
rec('finish: saved chains are done and say whether the whole search was copied; one the scraper did not save is failed',
    st[BW1] == ('done', True) and st[BW2] == ('done', False) and st[BW3][0] == 'failed' and st[PB1] == ('done', True)
    and st[PB2] == ('done', False) and st[MD1] == ('done', True), st)
rec('finish: a Palm Beach lead still waiting for a person stays needs_human_captcha and reached no scraper',
    st[PB3][0] == C.HUMAN and PB3 not in by)
rec('the counts separate whole-search reads from the rest', Q._counts({'items': items()})['verified'] == 3)
rec('the raw rows are deleted once read', not os.path.isdir(os.path.join(Q.qdir(), 'rows')))
Q.subprocess.run = real_run
os.environ['EQUITY_QUEUE_LIENS_DIR'] = LD
a = C.finish_argv({'case': PB1, 'county': 'PALM BEACH'}, 'x.json')
os.environ.pop('EQUITY_QUEUE_LIENS_DIR')
rec('a proof run can point the scrapers at a scratch folder; a normal run does not',
    a[a.index('--out') + 1] == P_OUT and '--out' not in C.finish_argv({'case': PB1, 'county': 'PALM BEACH'}, 'x.json'))

# ---- --queue-only: the scrapers build the list without searching anything -------------------------
BW4, PB4 = '2099-BRW-000004', '2099PB000004XXXXMB'
json.dump([L_BW1, {'case': BW4, 'owners': '(owner via title search)', 'defs': 'Sample, Peter', 'judg': 0}],
          open(os.path.join(LD, 'broward_leads.json'), 'w'))
json.dump([L_PB1, {'case': PB4, 'owners': 'SAMPLE PETER', 'folio': '00424601000000040', 'judg': 0}],
          open(os.path.join(LD, 'palmbeach_leads.json'), 'w'))
real_live, C.live_ok = C.live_ok, (lambda *a, **k: True)        # stand in for the engine checkout


def boom(*a, **k):
    raise AssertionError('--queue-only touched the network')


B_HERE, B.HERE = B.HERE, LD                                     # no lp_leads.json fold-in from the checkout
B.start_session = B.search_docs = PBL.start_session = PBL.search = PBL.show_captcha = boom
for mod, stem in ((B, 'broward'), (PBL, 'palmbeach')):
    mod.OUT, mod.LEADS = os.path.join(LD, stem + '_liens.json'), os.path.join(LD, stem + '_leads.json')
    argv, sys.argv = sys.argv, [stem + '_liens.py', '--queue-only']
    try:
        quiet(mod.main)
    finally:
        sys.argv = argv
        mod.OUT, mod.LEADS = mod.OUT0, mod.LEADS0
B.HERE, C.live_ok = B_HERE, real_live
got = {i['case']: i for i in items()}
rec('--queue-only puts the leads only the browser can read on the list, searching nothing',
    got.get(BW4, {}).get('status') == 'pending' and got[BW4]['searches'] == [{'kind': 'defendant', 'q': 'SAMPLE, PETER'}]
    and got.get(PB4, {}).get('status') == 'pending' and got[PB4]['searches'][0] == {'kind': 'parcel', 'q': '00424601000000040'})
rec('--queue-only leaves alone a lead that already has a saved chain or is already on the list',
    got[BW1]['status'] == 'done' and got[PB1]['status'] == 'done' and len(items()) == 9, len(items()))

log = io.open(os.path.join(Q.qdir(), 'log.jsonl'), encoding='utf-8').read()
rec('the log is counts only: no case number, no name', '2099' not in log and 'EXAMPLE' not in log and 'SAMPLE' not in log)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
if bad:
    print('FAILED:\n  ' + '\n  '.join(bad))
sys.exit(1 if bad else 0)
