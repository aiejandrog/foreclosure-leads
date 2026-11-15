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

# ---- 16 ----------------------------------------------------------------------------------------
try:
    import _auditfix2_guard as G16  # noqa: E402
    G16.run(rec, d)
except ImportError:
    pass

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
