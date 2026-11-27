"""Audit fixes, second batch (2026-11-15). Invented cases and people, temp folders, no network.

  5/17  mail_sent.json: a ledger that exists but will not read is never saved over; one shared lock
  9     cadence: a reply or stop marked this run is not mailed this run
  10    replies: the opt-out verified stamp is written through a temp file
  13    publish_site: only the board, call pages, balloon book and images are published
  14    text count follows the person when their case list (and so their key) changes
  16    publish_guard: cannot-check is blocked; the baseline is a saved last-good count
  18    records_liens: the row cap is judged per name
  19    judgment_interest: quarterly rates; a response/opposition filing is not the judgment
"""
import json
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ok, bad = [], []


def rec(name, cond, detail=''):
    (ok if cond else bad).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + (('  | ' + str(detail)[:200]) if detail and not cond else ''))


def src(name):
    return open(os.path.join(HERE, name), encoding='utf-8').read()


# ---- 5 / 17 ------------------------------------------------------------------------------------
import send_server as S  # noqa: E402

d = tempfile.mkdtemp(prefix='auditfix2_')
led = os.path.join(d, 'mail_sent.json')
json.dump([{'to': 'a@example.com'}, {'to': 'b@example.com'}], open(led, 'w'))
S.append_ledger_row(led, {'to': 'c@example.com'})
rec('append keeps the history', len(json.load(open(led))) == 3)
open(led, 'w').write('{torn')
try:
    S.append_ledger_row(led, {'to': 'd@example.com'})
    rec('an unreadable ledger refuses the write', False)
except S.LedgerUnreadable:
    rec('an unreadable ledger refuses the write', True)
rec('the unreadable ledger is left as it was', open(led).read() == '{torn')
rec('the row for the delivered email is parked, not lost', os.path.exists(led + '.pending.jsonl'))
json.dump([{'to': 'a@example.com'}], open(led, 'w'))
S.append_ledger_row(led, {'to': 'e@example.com'})
rows = json.load(open(led))
rec('the next good append folds the parked row back in',
    [r['to'] for r in rows] == ['a@example.com', 'd@example.com', 'e@example.com']
    and not os.path.exists(led + '.pending.jsonl'), rows)
rec('no lock file is left behind', not os.path.exists(led + '.lock'))

json.dump([], open(led, 'w'))
errs = []


def _w(i):
    try:
        for j in range(15):
            S.append_ledger_row(led, {'to': 'w%d-%d@example.com' % (i, j)})
    except Exception as e:      # noqa: BLE001
        errs.append(e)


ts = [threading.Thread(target=_w, args=(i,)) for i in range(4)]
[t.start() for t in ts]
[t.join() for t in ts]
rec('four writers at once lose no row', not errs and len(json.load(open(led))) == 60, errs[:1])

# Windows "delete pending": creating the lock right after another writer removed it can raise
# PermissionError. The lock must wait and retry, and a remove a scanner blocks must be retried.
_real_open, _real_remove = os.open, os.remove
_fails = {'open': 2, 'remove': 2}


def _flaky_open(path, *a, **k):
    if path.endswith('.lock') and _fails['open'] > 0:
        _fails['open'] -= 1
        raise PermissionError(13, 'Permission denied')
    return _real_open(path, *a, **k)


def _flaky_remove(path):
    if path.endswith('.lock') and _fails['remove'] > 0:
        _fails['remove'] -= 1
        raise PermissionError(13, 'Permission denied')
    return _real_remove(path)


os.open, os.remove = _flaky_open, _flaky_remove
try:
    _ok = True
    try:
        with S.ledger_file_lock(led, timeout=5):
            pass
    except Exception as e:      # noqa: BLE001
        _ok = e
finally:
    os.open, os.remove = _real_open, _real_remove
rec('the lock waits through a PermissionError on create (Windows delete pending)', _ok is True and _fails['open'] == 0, _ok)
rec('a blocked remove of the lock file is retried, so no lock is left behind',
    _fails['remove'] == 0 and not os.path.exists(led + '.lock'))
rec('outreach_email, cadence and ledger_sync write through the same lock',
    '_SS.append_ledger_row(SENT_LEDGER, entry)' in src('outreach_email.py')
    and '_ss._append_ledger(' in src('cadence.py')
    and '_SS.ledger_file_lock(local_p)' in src('ledger_sync.py'))

# ---- 9 -----------------------------------------------------------------------------------------
c = src('cadence.py')
i = c.index("_touch_of = _touch_index(state)")
loop = c[i:i + 900]
rec('cadence send loop skips anyone no longer active', "if s.get('status') != 'active':\n            continue" in loop, loop[:300])

# ---- 10 ----------------------------------------------------------------------------------------
import replies as R  # noqa: E402

_h = R.HERE
R.HERE = d
try:
    op = os.path.join(d, 'optouts.json')
    json.dump({'_dealflow_notes': 1, 'notes': {'2099-000001': {'status': 'DO NOT CONTACT'}}}, open(op, 'w'))
    rec('the verified stamp is written', R._stamp_optout_verified('box@example.com', 14) is True)
    got = json.load(open(op))
    rec('the stamp keeps every note', got['notes'] == {'2099-000001': {'status': 'DO NOT CONTACT'}} and 'optout_verified' in got)
    rec('the stamp goes through a temp file and os.replace',
        "os.replace(tmp, p)" in src('replies.py') and "json.dump(raw, open(p, 'w'" not in src('replies.py'))
finally:
    R.HERE = _h

# ---- 13 ----------------------------------------------------------------------------------------
import publish_site as PS  # noqa: E402

yes = ['index.html', 'call/index.html', 'call/carlos/index.html', 'hm-balloon-q7v3n8/index.html', 'img/abc_sv.jpg']
no = ['index.html.bak-20261109-183612', 'call/index.html.bak-judg-20261109-184856', 'call/carlos/index.html.bak-1',
      'agent-teams/OPERATING-RULES.md', 'bk-diag/qcount.py', 'VERDICT-BLOCKERS.md', 'routes/book.pdf', 'img/x.json']
rec('the site files are published', all(PS._published(x) for x in yes), [x for x in yes if not PS._published(x)])
rec('backups, notes, scripts and PDFs are not', not any(PS._published(x) for x in no), [x for x in no if PS._published(x)])

# ---- 14 ----------------------------------------------------------------------------------------
import foreclosure_leads as F  # noqa: E402

_fh = F.HERE
F.HERE = d
try:
    def _row(case, pkey):
        return {'ch': 'text', 'case': case, 'pkey': pkey, 'confirmed': True, 'ts_utc': '2026-11-01T12:00:00+00:00'}
    # the person had cases A and B (key Pold). B left the board, so A is now keyed CA.
    json.dump([_row('2099-A', 'Pold'), _row('2099-A', 'Pold'), _row('2099-B', 'Pold')], open(os.path.join(d, 'text_sent.json'), 'w'))
    _, per = F._text_ledger({'2099-A': 'C2099-A'})
    rec("three past texts count under the person's key today", per.get('C2099-A', {}).get('n') == 3, per)
    _, per0 = F._text_ledger()
    rec('with no key map the stored key is used, as before', per0 == {'Pold': {'n': 3, 'opens': 0, 'last': per0['Pold']['last']}}, per0)
finally:
    F.HERE = _fh

# ---- 15 ----------------------------------------------------------------------------------------
bat = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='utf-8', errors='replace').read()
i = bat.index("F.make_tracker(json.load(open('leads_final.json',encoding='utf-8')))")
rec('the nightly reads the rebuild exit code and ends as 8', 'set "RUNEXIT=8"' in bat[i:i + 700] and 'goto :end' in bat[i:i + 700])
rl = open(os.path.join(HERE, 'run-leads.bat'), encoding='utf-8', errors='replace').read()
i = rl.index('F.make_tracker(')
rec('run-leads stops before the push when the rebuild fails', 'REBUILD FAILED' in rl[i:i + 400])

# ---- 16 ----------------------------------------------------------------------------------------
import publish_guard as PG  # noqa: E402

# main() runs a real `git fetch origin` (180 s timeout) before it compares. The test stubs the
# baseline it compares against, so that fetch only costs time, and on a slow runner it hung past
# 180 s and failed the suite (main, 2026-11-16). Stub it here; publish_guard itself is unchanged.
import types  # noqa: E402
_pg = (PG.DOCS, PG.LAST_GOOD, PG.cov_of_git, PG.integrity, sys.argv, PG.subprocess)
PG.subprocess = types.SimpleNamespace(run=lambda *a, **k: None)
gd = tempfile.mkdtemp(prefix='auditfix2_guard_')
PG.DOCS = os.path.join(gd, 'index.html')
PG.LAST_GOOD = os.path.join(gd, 'lastgood.json')
PG.integrity = lambda path: []
sys.argv = ['publish_guard.py']


_MARK = '<!-- DEALFLOW-COVERAGE %s -->'


def _board(cov):
    open(PG.DOCS, 'w', encoding='utf-8').write(_MARK % json.dumps(cov) + chr(10) + 'const RAW = {"enc":"x"}' + chr(10))


try:
    PG.cov_of_git = lambda ref: None
    rec('a missing board is blocked', PG.main() == 2)
    open(PG.DOCS, 'w', encoding='utf-8').write('no marker\nconst RAW = {"enc":"x"}\n')
    rec('a build with no census marker is blocked', PG.main() == 2)
    f = next(iter(PG.FIELDS))
    _board({f: 1000})
    rec('no baseline at all is blocked', PG.main() == 2)
    sys.argv = ['publish_guard.py', '--first-build']
    rec('--first-build allows it and saves the day baseline', PG.main() == 0 and os.path.exists(PG.LAST_GOOD))
    sys.argv = ['publish_guard.py']
    # each publish loses a little against the board before it, never enough to trip on its own
    ratio = PG.FIELDS[f][0]
    step = 1000
    codes = []
    for _ in range(6):
        prev = step
        step = int(prev * (ratio + (1 - ratio) * 0.5))
        PG.cov_of_git = lambda ref, _p=prev: {f: _p}
        _board({f: step})
        codes.append(PG.main())
    rec('a slow shrink across the day is caught against the day baseline', codes[0] == 0 and 2 in codes, codes)
finally:
    PG.DOCS, PG.LAST_GOOD, PG.cov_of_git, PG.integrity, sys.argv, PG.subprocess = _pg

# ---- 18 ----------------------------------------------------------------------------------------
r = src('records_liens.py')
rec('the row limits are judged on the longest single name search',
    '_one = len(models) if busiest is None else int(busiest)' in r and "'capped': _one >= 500" in r
    and "if _one > 45: conf = 'low'" in r)
rec('the browser read passes the longest search only when every name was copied in full',
    "busiest=(_FILE_META.get('busiest') if a.models_file and _FILE_META.get('full') else None)" in r)
rec('the queue writes the longest single search into the file', "'busiest': max([int(p.get('n') or 0) for p in cur['parts']] or [0])" in src('equity_queue.py'))

# ---- 19 ----------------------------------------------------------------------------------------
import datetime  # noqa: E402
import judgment_interest as JI  # noqa: E402

rec('a judgment entered in October 2023 starts at that quarter\'s rate', JI.entry_rate(datetime.date(2023, 10, 15)) == 8.54)
rec('a January judgment keeps the January rate', JI.entry_rate(datetime.date(2023, 2, 1)) == JI.JAN1_RATES[2023])
a = JI.accrue(500000, datetime.date(2023, 10, 1), datetime.date(2024, 1, 1))
if True:
    rec('$500,000 from 10/1/2023 to year end accrues at 8.54%', abs(a['interest'] - round(500000 * 0.0854 * 92 / 365, 2)) < 0.02, a['interest'])
    b = JI.accrue(500000, datetime.date(2023, 10, 1), datetime.date(2024, 3, 1))
    rec('and the rate moves to the January rate on January 1', [s_['rate'] for s_ in b['segments']] == [8.54, 9.09], b['segments'])
import re as _re  # noqa: E402
rec('a response in opposition to a final judgment is not the judgment',
    bool(_re.search(JI.FJ_EXCLUDE, 'RESPONSE IN OPPOSITION TO FINAL JUDGMENT', _re.I))
    and not _re.search(JI.FJ_EXCLUDE, 'FINAL JUDGMENT OF FORECLOSURE', _re.I))

# ---- 20 ----------------------------------------------------------------------------------------
import jose_buybox as JB  # noqa: E402


def _bb(case, st, value, judg, days):
    return {'case': case, 'st': st, 'value': value, 'judg': judg, 'days': days, 'warn': '', 'fake': False,
            'ju': False, 'vac': False, 'hs': False, 'county': 'MIAMI-DADE', 'addr': '', 'auction': ''}


out, _, _ = JB.gate([_bb('2099-TD', 'TD', 1000000, 12000, 20), _bb('2099-FC', 'FC', 500000, 200000, 30)])
rec('a foreclosure ranks first; a tax deed is listed after it', [x['case'] for x in out] == ['2099-FC', '2099-TD'], out)
rec('a tax deed carries no seller-room or profit figure', out[1]['seller_room'] is None and out[1]['profit_at_target'] is None and out[1]['zone'] == 'TAXDEED')

# ---- value join: the equity card is re-baked, and unvalued parcels are listed --------------------
fl = src('foreclosure_leads.py')
i = fl.index("_vs = _vj.attach(slim")
rec('the equity card is re-baked for rows the value join just valued',
    "_noval = [_r for _r in slim" in fl[i - 200:i] and "_r['eqc'] = _es.card(_r)" in fl[i:i + 900])
rec('leads with a parcel number and no value are listed for the free county lookup',
    "'value_missing.json'" in fl[i:i + 2600] and 'def fill_missing(' in src('pa_values.py')
    and 'pa_values.py --missing' in bat)

# ---- association cases in the recorded-debt tier ------------------------------------------------
import equity_state as ES  # noqa: E402


def _lead(**kw):
    d = {'case': '2099-HOA-1', 'st': 'LP', 'value': 400000, 'ftype': 'HOA', 'ctype': 'HOA', 'eqstate': 'priced',
         'orliens': [{'st': 'OPEN', 'amt': 150000}], 'ju': True}
    d.update(kw)
    return d


k = ES.card(_lead())
rec('an association case with a priced chain joins the tier', k['recorded'] and k['recorded_debt'] == 150000 and k['recorded_net'] == 250000, k)
rec('nothing on it may be quoted', k['may_quote'] is False and k['net'] is None and 'association' in k['why'].lower())
k = ES.card(_lead(eqstate='clear', orliens=[]))
rec('an association case with a verified clear chain joins the tier', k['recorded'] and k['recorded_net'] == 400000, k)
k = ES.card(_lead(eqstate='none'))
rec('an unverified chain never joins it', not k['recorded'])
k = ES.card(_lead(orliens=[{'st': 'OPEN', 'amt': 150000}, {'st': 'OPEN', 'amt': 0}]))
rec('an open loan with no amount keeps it out', not k['recorded'])
k = ES.card(_lead(value=0))
rec('no property value keeps it out', not k['recorded'])
k = ES.card(_lead(st='FC'))
rec('only a fresh filing qualifies, not an auction row', not k['recorded'])

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
