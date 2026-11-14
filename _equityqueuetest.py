"""The equity queue: misses are logged, organized by day, worked by the browser job, and cleaned up.

2026-11-13 (Alejandro): "everything has to be logged, everything has to be organized". A lead the
free lien search missed used to be one line in leads-run.log. It is now a row in
DEALFLOW_DIR/equity-queue/queue-<day>.json with a status the browser job moves along.

Temp DEALFLOW_DIR, invented people, no network, no browser, no real scraper run.
"""
import datetime as dt
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix='eqq_')
os.environ['DEALFLOW_DIR'] = TMP
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import equity_queue as Q  # noqa: E402

ok, bad = [], []


def rec(name, cond, detail=''):
    (ok if cond else bad).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)[:300]) if detail and not cond else ''))


Q._desktop_note = lambda q: None            # never write to the real Desktop from a test
notes = []
real_note = Q._desktop_note

rec('the queue lives under DEALFLOW_DIR, not the repo and not the Desktop',
    Q.qdir().startswith(TMP) and os.path.isdir(Q.qdir()), Q.qdir())
rec('nothing queued: flush writes no file', Q.flush() == 0 and not os.path.exists(Q.qpath()))

Q.add('2099-000001-CA-01', 'JANE EXAMPLE', 'EXAMPLE', 'JANE', '0100000000010', reason='free search missed')
Q.add('2099-000002-CA-01', 'JOHN SAMPLE', 'SAMPLE', 'JOHN', '0100000000020', reason='free search missed')
Q.add('', 'NO CASE', 'CASE', 'NO')
rec('flush writes today\'s file with the two real misses', Q.flush() == 2 and os.path.exists(Q.qpath()))
q = json.load(open(Q.qpath(), encoding='utf-8'))
rec('each row carries the case, the name parts, the folio, a reason and status pending',
    [i['case'] for i in q['items']] == ['2099-000001-CA-01', '2099-000002-CA-01']
    and all(i['status'] == 'pending' and i['last'] and i['folio'] and i['reason'] for i in q['items']), q)

Q.add('2099-000001-CA-01', 'JANE EXAMPLE', 'EXAMPLE', 'JANE')
Q.add('2099-000003-CA-01', 'ANA TESTER', 'TESTER', 'ANA', '0100000000030')
Q.add('2099-000004-CA-01', 'NO FOLIO', 'FOLIO', 'NO')
Q.add('2099-000009-CA-01', 'EXAMPLE BANK N A', 'EXAMPLE BANK N A', '', kind='entity')
rec('a second run the same day adds only the new leads', Q.flush() == 3
    and len(json.load(open(Q.qpath(), encoding='utf-8'))['items']) == 5)
import contextlib  # noqa: E402
_b = io.StringIO()
with contextlib.redirect_stdout(_b):
    Q.cmd_next(25)
_nx = [x['case'] for x in json.loads(_b.getvalue())]
rec('the browser job is never handed a bank/LLC/association row', '2099-000009-CA-01' not in _nx and len(_nx) == 3, _nx)
rec('a lead with no folio is not handed out either: its rows could not be tied to the parcel', '2099-000004-CA-01' not in _nx)
_c = Q._counts(json.load(open(Q.qpath(), encoding='utf-8')))
rec('entity rows are counted apart from the ones waiting for the browser', _c['entity'] == 1 and _c['pending'] == 4, _c)

# ---- worker side
rec('token for a case not on today\'s list is refused', Q.cmd_token('2099-999999-CA-01', 'x' * 40) == 1)
rec('an empty token is refused', Q.cmd_token('2099-000001-CA-01', '') == 1)
rec('a token is stored in the scraper\'s own cache under the owner name', Q.cmd_token('2099-000001-CA-01', 'q' * 60) == 0
    and json.load(open(Q.P.records_qs(), encoding='utf-8')).get('JANE EXAMPLE') == 'q' * 60)
rec('a browser miss is recorded', Q.cmd_miss('2099-000002-CA-01', 'no results for that name') == 0)
st = {i['case']: i['status'] for i in json.load(open(Q.qpath(), encoding='utf-8'))['items']}
st.pop('2099-000009-CA-01', None); st.pop('2099-000004-CA-01', None)
rec('statuses moved: token, miss, pending', st == {'2099-000001-CA-01': 'token', '2099-000002-CA-01': 'miss',
                                                    '2099-000003-CA-01': 'pending'}, st)

Q.add('2099-000002-CA-01', 'JOHN SAMPLE', 'SAMPLE', 'JOHN')
os.remove(Q.qpath())
rec('a lead the browser missed is not queued again the next run', Q.flush() == 0)

# ---- reading rows off the page, in checksummed chunks
Q.add('2099-000005-CA-01', 'RITA READER', 'READER', 'RITA', '01-0000-000-0050')
Q.flush()
js = Q.js_for('2099-000005-CA-01', 1)
rec('the page script is built for the lead: its folio, the chunk asked for, the twelve fields, no token returned',
    js and 'const FOLIO = "100000000050", K = 1,' in js and 'foliO_NUMBER' in js and "qs:" not in js
    and 'out = JSON.stringify({k: K' in js, (js or '')[:200])
rec('no script for a case that is not on today\'s list', Q.js_for('2099-999999-CA-01') is None)


def row(i, folio=100000000050):
    return ['MORTGAGE - MOR', folio, 'EXAMPLE BANK NA', 'READER RITA', 100 + i, 30000 + i,
            '2019-0%d-01T00:00:00' % (i % 9 + 1), 20190000 + i, 0, 0, 'EXAMPLE SUB', '%d/%d' % (30000 + i, 100 + i)]


c0 = [row(i) for i in range(Q.CHUNK)]
c1 = [row(i) for i in range(Q.CHUNK, Q.CHUNK + 2)]
bad_rows = [list(r) for r in c0]; bad_rows[0][5] += 1                 # one digit of a book number copied wrong
rec('a chunk whose rows do not match the page checksum is refused',
    Q.accept_chunk('2099-000005-CA-01', {'k': 0, 'of': 2, 'sum': Q.checksum(c0), 'rows': bad_rows})[0] is False)
rec('chunk 1 before chunk 0 is refused',
    Q.accept_chunk('2099-000005-CA-01', {'k': 1, 'of': 2, 'sum': Q.checksum(c1), 'rows': c1})[0] is False)
rec('chunk 0 is stored and the lead reads as half done',
    Q.accept_chunk('2099-000005-CA-01', {'k': 0, 'of': 2, 'sum': Q.checksum(c0), 'rows': c0, 'onParcel': 7})[0] is True
    and Q._counts(json.load(open(Q.qpath(), encoding='utf-8')))['reading'] == 1)
rec('the last chunk completes it: every row stored under the parser\'s own field names',
    Q.accept_chunk('2099-000005-CA-01', {'k': 1, 'of': 2, 'sum': Q.checksum(c1), 'rows': c1})[0] is True
    and len(json.load(open(Q.rows_path('2099-000005-CA-01'), encoding='utf-8'))['recordingModels']) == Q.CHUNK + 2
    and json.load(open(Q.rows_path('2099-000005-CA-01'), encoding='utf-8'))['recordingModels'][0]['reC_BOOK'] == 30000)
rec('rows for a bank-named lead or a lead with no folio are refused',
    Q.accept_chunk('2099-000009-CA-01', {'k': 0, 'of': 1, 'sum': Q.checksum(c1), 'rows': c1})[0] is False
    and Q.accept_chunk('2099-000004-CA-01', {'k': 0, 'of': 1, 'sum': Q.checksum(c1), 'rows': c1})[0] is False)
rec('the python checksum is the one the page script computes (31-multiplier over the joined values)',
    Q.checksum([['A', 1, None]]) == ((((ord('A') * 31 + ord('|')) * 31 + ord('1')) * 31 + ord('|')) % Q.MOD))

# ---- finish, with the scraper stubbed
Q.add('2099-000001-CA-01', 'JANE EXAMPLE', 'EXAMPLE', 'JANE')
Q.add('2099-000003-CA-01', 'ANA TESTER', 'TESTER', 'ANA')
Q.flush()
Q.cmd_token('2099-000001-CA-01', 'q' * 60)
Q.cmd_token('2099-000003-CA-01', 'r' * 60)
calls = []


class _P:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def fake_run(argv, **k):
    calls.append(argv)
    case = argv[argv.index('--case') + 1]
    return _P(0, '  ok  %s   X   1 open mtg\n' % case) if case.endswith(('001-CA-01', '005-CA-01')) else _P(0, 'DONE: 0 traced\n')


Q.subprocess.run = fake_run
Q.time.sleep = lambda s: None
rec('finish exits 0', Q.cmd_finish() == 0)
rec('finish reads each lead through the scraper\'s free cached-only path, one case at a time',
    len(calls) == 3 and all('--cached-only' in c and 'records_liens.py' in c for c in calls), calls)
rec('a lead whose rows came off the page is analyzed from that file',
    sum(1 for c in calls if '--models-file' in c) == 1, calls)
rec('the raw rows are deleted once read', not os.path.isdir(os.path.join(Q.qdir(), 'rows')))
st = {i['case']: i['status'] for i in json.load(open(Q.qpath(), encoding='utf-8'))['items']}
rec('a chain the scraper saved is done; a token that read nothing is failed, not done',
    st.get('2099-000001-CA-01') == 'done' and st.get('2099-000003-CA-01') == 'failed' and st.get('2099-000005-CA-01') == 'done', st)

# ---- self-cleaning
old = (dt.date.today() - dt.timedelta(days=Q.KEEP_DAYS + 3)).isoformat()
recent = (dt.date.today() - dt.timedelta(days=1)).isoformat()
for d in (old, recent):
    json.dump({'date': d, 'items': []}, open(Q.qpath(d), 'w'))
io.open(os.path.join(Q.qdir(), 'x.tmp'), 'w').write('x')
Q.clean()
rec('queue files past the keep window and stray temp files are deleted; recent ones stay',
    not os.path.exists(Q.qpath(old)) and os.path.exists(Q.qpath(recent))
    and not os.path.exists(os.path.join(Q.qdir(), 'x.tmp')))
for _ in range(Q.LOG_LINES + 50):
    Q._log('t', n=1)
n = len(io.open(os.path.join(Q.qdir(), 'log.jsonl'), encoding='utf-8').read().splitlines())
rec('the log is trimmed to its cap', n == Q.LOG_LINES, n)
log = io.open(os.path.join(Q.qdir(), 'log.jsonl'), encoding='utf-8').read()
rec('the log is counts only: no case number, no name', '2099-' not in log and 'EXAMPLE' not in log)

# ---- the Desktop note is counts only
import inspect  # noqa: E402
src = inspect.getsource(sys.modules['equity_queue'])
a = src.index('def _desktop_note'); b = src.index('def clean')
body = src[a:b]
rec('the Desktop note writes counts and a path, never a row field',
    "it['" not in body and "['case']" not in body and "['owner']" not in body and 'Leads the free scraper could not read' in body)

rl = io.open(os.path.join(HERE, 'records_liens.py'), encoding='utf-8').read()
rec('the lien scraper queues a spend-cap miss and flushes once at the end, both inside try/except',
    '_EQ.add(case, oc, _l, _f, folio,' in rl and '_n = _EQ.flush()' in rl)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
