"""The equity queue: every unread lead is logged, organized by day, worked by the browser job, cleaned up.

2026-11-13 (Alejandro): "everything has to be logged, everything has to be organized", then:
"DON'T LET IT SKIP BANK OR ASSOCIATIONS ... I WANT VERIFIED EQUITY ... THE WHOLE SEARCH".
A lead the nightly lien search could not read is a row in DEALFLOW_DIR/equity-queue/queue-<day>.json.
The browser job copies the WHOLE search result for every name on the lead, in checksummed chunks,
and the scraper's own analyze() reads it. Nothing is skipped on what the owner field holds.

Temp DEALFLOW_DIR, invented people, no network, no browser, no real scraper run.
"""
import contextlib
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


def quiet(fn, *a):
    b = io.StringIO()
    with contextlib.redirect_stdout(b):
        r = fn(*a)
    return r, b.getvalue()


def items():
    return json.load(open(Q.qpath(), encoding='utf-8'))['items']


Q._desktop_note = lambda q: None            # never write to the real Desktop from a test

rec('the queue lives under DEALFLOW_DIR, not the repo and not the Desktop',
    Q.qdir().startswith(TMP) and os.path.isdir(Q.qdir()), Q.qdir())
rec('nothing queued: flush writes no file', Q.flush() == 0 and not os.path.exists(Q.qpath()))

P1, P2, ENT, NOF = '2099-000001-CA-01', '2099-000002-CA-01', '2099-000009-CA-01', '2099-000004-CA-01'
Q.add(P1, 'JANE EXAMPLE', 'EXAMPLE', 'JANE', '0100000000010', reason='not read', names=[('EXAMPLE', 'JANE')])
Q.add(P2, 'JOHN SAMPLE', 'SAMPLE', 'JOHN', '0100000000020', reason='not read',
      names=[('SAMPLE', 'JOHN'), ('SAMPLE', 'MARY')])
Q.add(ENT, 'EXAMPLE BANK N A', 'EXAMPLE BANK N A', '', '0100000000090', kind='entity',
      names=[('OWNER', 'REAL'), ('SPOUSE', 'REAL')])
Q.add(NOF, 'NO FOLIO', 'FOLIO', 'NO', '', names=[('FOLIO', 'NO')], address='1 EXAMPLE ST')
Q.add('', 'NO CASE', 'CASE', 'NO')
rec('flush writes today\'s file with every real lead', Q.flush() == 4 and os.path.exists(Q.qpath()))
rec('each row carries the case, the names to search, a reason and status pending',
    all(i['status'] == 'pending' and i['names'] for i in items()), items())
Q.add(P1, 'JANE EXAMPLE', 'EXAMPLE', 'JANE')
rec('a second run the same day adds nothing already there', Q.flush() == 0 and len(items()) == 4)

_r, out = quiet(Q.cmd_next, 25)
nx = {x['case']: x for x in json.loads(out)}
rec('NOTHING is skipped: the bank-named lead and the lead with no folio are both handed out',
    set(nx) == {P1, P2, ENT, NOF}, sorted(nx))
rec('a bank-named lead is searched by the people the case names, not by the bank',
    nx[ENT]['names'] == [['OWNER', 'REAL'], ['SPOUSE', 'REAL']], nx[ENT])
rec('a lead with no folio says so and carries its address for the appraiser lookup',
    nx[NOF]['has_folio'] is False and nx[NOF]['address'] == '1 EXAMPLE ST' and 'address' not in nx[P1], nx[NOF])
rec('a folio found in the browser is stored; a short one is refused',
    quiet(Q.cmd_folio, NOF, '12')[0] == 1 and quiet(Q.cmd_folio, NOF, '01-0000-000-0040')[0] == 0
    and [i for i in items() if i['case'] == NOF][0]['folio'] == '100000000040')

# ---- the page script
js = Q.js_for(P1, -1)
rec('the page script copies the whole search (no parcel filter), never returns the token, and can be re-run',
    js and js.startswith('var out; { const K = -1,') and 'keep.add' not in js and 'FOLIO' not in js
    and "out = JSON.stringify({hdr: 1" in js and 'qs:' not in js, (js or '')[:160])
rec('no script for a case that is not on today\'s list', Q.js_for('2099-999999-CA-01') is None)

TYPES = ['MORTGAGE - MOR', 'SATISFACTION OF MORTGAGE - SMO', 'LIEN - LIE', 'FINAL JUDGMENT - FJU']


def packed(i, t=0, folio=100000000010):
    return [t, folio, 'EXAMPLE BANK NA', 'EXAMPLE JANE', 100 + i, 30000 + i, '6/%d/2019' % (i % 27 + 1),
            20190000 + i, 0, 0, 'EXAMPLE SUB']


def search(rows, per=4, types=TYPES):
    cuts = [rows[i:i + per] for i in range(0, len(rows), per)]
    return [{'hdr': 1, 'n': len(rows), 'of': len(cuts), 'dsfx': True, 'bpd': True, 'types': types}] + [
        {'k': k, 'of': len(cuts), 'sum': Q.checksum(c), 'rows': c} for k, c in enumerate(cuts)]


rows1 = [packed(i, i % 4) for i in range(10)]
s = search(rows1)
badc = json.loads(json.dumps(s)); badc[2]['rows'][0][5] += 1          # one digit of a book number copied wrong
rec('a search with one mis-copied digit is refused whole: nothing is stored',
    Q.accept_search(P1, badc)[0] is False and not os.path.exists(Q.rows_path(P1)))
short = json.loads(json.dumps(s)); short.pop()
rec('a search missing a chunk is refused', Q.accept_search(P1, short)[0] is False)
rec('a file without the header is refused', Q.accept_search(P1, s[1:])[0] is False)
okk, msg = Q.accept_search(P1, s)
rec('the whole search is stored', okk and 'Every name on this lead is stored' in msg, msg)
m0 = json.load(open(Q.rows_path(P1), encoding='utf-8'))['parts'][0]['models'][0]
rec('rows are rebuilt to the parser\'s own fields: type from the header list, date suffix and book/page restored',
    m0['doC_TYPE'] == 'MORTGAGE - MOR' and m0['reC_DATE'] == '6/1/2019 12:00:00 AM'
    and m0['reC_BOOKPAGE'] == '30000/100' and m0['reC_BOOK'] == 30000 and set(m0) == set(Q.FIELDS), m0)
rec('the python checksum is the one the page script computes (31-multiplier over the joined values)',
    Q.checksum([['A', 1, None]]) == ((((ord('A') * 31 + ord('|')) * 31 + ord('1')) * 31 + ord('|')) % Q.MOD))

# two names on one lead: full only when both are stored
okk, msg = Q.accept_search(P2, search([packed(i, 0, 100000000020) for i in range(5)]), 0)
rec('a lead with two names says which is still to search', okk and 'Names still to search: [1]' in msg, msg)
mf, full = Q._models_file([i for i in items() if i['case'] == P2][0])
rec('one of two names stored: the read is NOT full', full is False and json.load(open(mf, encoding='utf-8'))['full'] is False)
dup = [packed(i, 0, 100000000020) for i in range(3)] + [packed(50, 2, 0)]
rec('the second name is stored', Q.accept_search(P2, search(dup), 1)[0] is True)
mf, full = Q._models_file([i for i in items() if i['case'] == P2][0])
doc = json.load(open(mf, encoding='utf-8'))
rec('both names stored: the read is full, and a row both searches returned is kept once',
    full is True and doc['full'] is True and len(doc['recordingModels']) == 6 and doc['searched'] == 'SAMPLE JOHN; SAMPLE MARY', doc.get('searched'))
rec('rows for a bank-named lead are accepted like any other',
    Q.accept_search(ENT, search([packed(1, 0, 100000000090)]), 0)[0] is True)
big = [packed(i) for i in range(Q.CLERK_CAP)]
Q.add('2099-000007-CA-01', 'COMMON NAME', 'NAME', 'COMMON', '0100000000070', names=[('NAME', 'COMMON')])
Q.flush()
Q.accept_search('2099-000007-CA-01', search(big, per=50))
rec('a search that hit the clerk\'s 500-row cap is never full',
    Q._models_file([i for i in items() if i['case'] == '2099-000007-CA-01'][0])[1] is False)

# ---- a browser miss
rec('a browser miss is recorded', quiet(Q.cmd_miss, NOF, 'no results for that name')[0] == 0)
Q.add(NOF, 'NO FOLIO', 'FOLIO', 'NO', '', names=[('FOLIO', 'NO')])
keep = Q.qpath() + '.keep'
os.replace(Q.qpath(), keep)
rec('a lead the browser missed is not queued again the next run', Q.flush() == 0)
os.replace(keep, Q.qpath())

# ---- finish, with the scraper stubbed
calls = []


class _P:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def fake_run(argv, **k):
    calls.append(argv)
    case = argv[argv.index('--case') + 1]
    return _P(0, 'DONE: 0 traced\n') if case == ENT else _P(0, '  ok  %s   X   1 open mtg\n' % case)


Q.subprocess.run = fake_run
Q.time.sleep = lambda s: None
rec('finish exits 0', quiet(Q.cmd_finish)[0] == 0)
rec('finish analyzes each stored lead through the scraper, from its own models file, spending nothing',
    len(calls) == 4 and all('--cached-only' in c and '--models-file' in c and 'records_liens.py' in c for c in calls), calls)
st = {i['case']: (i['status'], i.get('full')) for i in items()}
rec('a chain the scraper saved is done and says whether the whole search was copied; one it did not save is failed',
    st[P1] == ('done', True) and st[P2] == ('done', True) and st[ENT][0] == 'failed'
    and st['2099-000007-CA-01'] == ('done', False) and st[NOF][0] == 'miss', st)
rec('the counts separate whole-search reads from the rest', Q._counts({'items': items()})['verified'] == 2)
rec('the raw rows are deleted once read', not os.path.isdir(os.path.join(Q.qdir(), 'rows')))

# a folio found in the browser reaches the scraper
Q.add('2099-000008-CA-01', 'LATE FOLIO', 'FOLIO', 'LATE', '', names=[('FOLIO', 'LATE')], address='2 EXAMPLE ST')
Q.flush()
quiet(Q.cmd_folio, '2099-000008-CA-01', '0100000000080')
Q.accept_search('2099-000008-CA-01', search([packed(1, 0, 100000000080)]))
del calls[:]
quiet(Q.cmd_finish)
rec('a folio the browser found is passed to the scraper for that lead',
    len(calls) == 1 and calls[0][calls[0].index('--folio') + 1] == '100000000080', calls)

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

import inspect  # noqa: E402
src = inspect.getsource(sys.modules['equity_queue'])
a = src.index('def _desktop_note'); b = src.index('def clean')
body = src[a:b]
rec('the Desktop note writes counts and a path, never a row field',
    "it['" not in body and "['case']" not in body and "['owner']" not in body and 'Leads the free scraper could not read' in body)

rl = io.open(os.path.join(HERE, 'records_liens.py'), encoding='utf-8').read()
rec('the lien scraper queues EVERY lead it could not read, and a lead with no folio, then flushes once',
    rl.count('_queue_for_browser(case, oc, folio, _co, r,') == 2 and '_n = _EQ.flush() if _real_run() else 0' in rl)
rec('nothing in the scraper\'s hand-off skips a bank or association: it searches the defendants instead',
    "names = ([] if ent or not sp[1] else [(sp[0], sp[1])]) + [tuple(x) for x in (co or [])]" in rl)
rec('a browser read is truncated unless the whole search was copied, and a truncated one never replaces a full chain',
    "if not _FILE_META.get('full'):\n                    res['truncated'] = True" in rl
    and "if res.get('truncated') and _prev.get('conf') in ('ok', 'low') and not _prev.get('truncated'):" in rl)
rec('tests cannot write the live queue', 'def _real_run():' in rl and "startswith('2099-')" in src)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
