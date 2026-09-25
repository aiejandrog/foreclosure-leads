"""One-shot: ramp_status counts both ledgers the way the caps do, and never confuses
"no ledger on this machine" with "nothing was sent". Gitignored _*.py. No network, no mail.

WHY THE None MATTERS MORE THAN THE COUNTS. `mail_sent.json` and `warmup_log.json` are both
gitignored and rooted at the machine that wrote them, so on any box that is not the sender they
simply do not exist. `send_server._alias_sent_today()` returns 0 in that case, which is right for a
cap (refusing to send is safe) and wrong for a report: it reads as "this alias is quiet today" on a
machine that has never sent anything. The project has already made the machine-blindness mistake
twice - the 40x bounce-rate split between the two boxes, and "the desktop is dark" established from
a task list that could not see the Startup folder. So the counts here are Optional[int], and absent
is printed as "?".

The other half is the sum itself. warmup.py deliberately does not write the outreach ledger, and
that is correct - warm-up mail goes to company-owned mailboxes and counting it as outreach would
corrupt every reply and bounce rate in the project. But nothing then adds the two back together,
and Gmail does not care which file a message was logged in.
"""
import ast
import datetime as dt
import io
import json
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))

import ramp_status as RS
import send_server as SS
import warmup as WU

R = []
def rec(n, ok, d=''):
    R.append(bool(ok))
    print((('  PASS ' if ok else '  FAIL ') + n + (' | ' + str(d) if d else '')).encode('ascii', 'replace').decode())

DAY = dt.date(2026, 9, 22)
def _code_only(path):
    """Source with every docstring removed, so a grep sees behaviour and not prose.

    Stripped by node position rather than by splitting on triple quotes: a split silently starts
    checking a different slice of the file the moment anything in it gains a second docstring,
    which narrows the check while still reporting PASS.
    """
    src = io.open(path, encoding='utf-8').read()
    doc = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(node, 'body', None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                doc.update(range(b[0].lineno, (b[0].end_lineno or b[0].lineno) + 1))
    return '\n'.join(l for i, l in enumerate(src.splitlines(), 1) if i not in doc), len(doc)


A1 = 'alejandro@biscaynesolutionsgroup.com'
A2 = 'alejandro@bsgfl.com'
MAIN = 'alejandro@bsgflorida.com'

print('\n-- absent ledgers report as unknown, never as zero --')
RS.SENT_LEDGER = os.path.join(tempfile.gettempdir(), '_no_such_mail_sent.json')
RS.WARMUP_LOG = os.path.join(tempfile.gettempdir(), '_no_such_warmup_log.json')
rec('cold count is None when mail_sent.json is absent', RS.cold_today(A1, DAY) is None)
rec('warm count is None when warmup_log.json is absent', RS.warm_today(A1, DAY) is None)
rec('None prints as "?" and not as 0', RS._n(None) == '?' and RS._n(0) == '0')

print('\n-- the cold count uses _alias_sent_today\'s exact predicate --')
tmp = tempfile.mkdtemp()
ledger = os.path.join(tmp, 'mail_sent.json')
iso, other = DAY.isoformat(), (DAY - dt.timedelta(days=1)).isoformat()
rows = [
    {'d': iso, 'from': A1, 'message_id': '<1@x>'},                      # counts
    {'d': iso, 'from': A1, 'message_id': '<2@x>'},                      # counts
    {'d': iso, 'from': A1.upper(), 'message_id': '<3@x>'},              # counts (case)
    {'d': iso, 'from': A1, 'message_id': '<4@x>', 'test_mode': True},   # excluded
    {'d': iso, 'from': A1, 'message_id': '<5@x>', 'error': 'boom'},     # excluded
    {'d': iso, 'from': A1},                                             # excluded: no message_id
    {'d': other, 'from': A1, 'message_id': '<6@x>'},                    # excluded: other day
    {'d': iso, 'from': A2, 'message_id': '<7@x>'},                      # other alias
]
json.dump(rows, io.open(ledger, 'w', encoding='utf-8'))
RS.SENT_LEDGER = ledger
rec('counts only real, non-test, non-failed sends for the day', RS.cold_today(A1, DAY) == 3,
    RS.cold_today(A1, DAY))
_only_bare = os.path.join(tmp, 'mail_sent_bare.json')
json.dump([{'d': iso, 'from': A1}], io.open(_only_bare, 'w', encoding='utf-8'))
_keep = RS.SENT_LEDGER
RS.SENT_LEDGER = _only_bare
rec('a row with no message_id is not a send', RS.cold_today(A1, DAY) == 0,
    'a ledger holding only that row counts 0, not 1')
RS.SENT_LEDGER = _keep
rec('the other alias is counted separately', RS.cold_today(A2, DAY) == 1, RS.cold_today(A2, DAY))
rec('an alias with no rows reads 0, not None', RS.cold_today(MAIN, DAY) == 0)

# the arithmetic must not drift from the function the caps actually call
# _alias_sent_today hardcodes dt.date.today() and takes no day argument, so comparing it against
# cold_today(.., DAY) only ran on 2026-09-22 itself and was a literal True every other day -- the one
# check guarding the divergence that would make the whole report lie. A second fixture dated today
# makes it run every day, on both senders' ledger path at once.
_live = os.path.join(tmp, 'mail_sent_today.json')
_now = dt.date.today()
_niso = _now.isoformat()
json.dump([{'d': r['d'] if r['d'] == other else _niso, **{k: v for k, v in r.items() if k != 'd'}}
           for r in rows], io.open(_live, 'w', encoding='utf-8'))
_real_ss, _real_rs = SS.SENT_LEDGER, RS.SENT_LEDGER
try:
    SS.SENT_LEDGER = RS.SENT_LEDGER = _live
    _theirs, _ours = SS._alias_sent_today(A1), RS.cold_today(A1, _now)
    rec('agrees with send_server._alias_sent_today, on any day this runs',
        _theirs == _ours == 3, '%s vs %s' % (_theirs, _ours))
    rec('and agrees on the second alias too',
        SS._alias_sent_today(A2) == RS.cold_today(A2, _now) == 1)
    rec('and on an alias with no rows at all',
        SS._alias_sent_today(MAIN) == RS.cold_today(MAIN, _now) == 0)
finally:
    SS.SENT_LEDGER, RS.SENT_LEDGER = _real_ss, _real_rs

rec('a corrupt ledger reads unknown, not zero',
    (json.dump({'not': 'a list'}, io.open(ledger, 'w', encoding='utf-8')) or RS.cold_today(A1, DAY)) is None)

print('\n-- the warm-up count reads warmup.py\'s own shape --')
wlog = os.path.join(tmp, 'warmup_log.json')
json.dump({'days': {iso: {A1: [{'to': 'a'}] * 15, A2: [{'to': 'b'}] * 15},
                    other: {A1: [{'to': 'c'}] * 9}}},
          io.open(wlog, 'w', encoding='utf-8'))
RS.WARMUP_LOG = wlog
rec('counts today\'s messages for the alias', RS.warm_today(A1, DAY) == 15, RS.warm_today(A1, DAY))
rec('does not bleed across days', RS.warm_today(A1, DAY - dt.timedelta(days=1)) == 9)
rec('an alias absent from the log reads 0', RS.warm_today(MAIN, DAY) == 0)
rec('a day absent from the log reads 0', RS.warm_today(A1, DAY + dt.timedelta(days=400)) == 0)

# warmup._save_log rewrites this file with a non-atomic json.dump after every single sendmail, so a
# half-written log is the ordinary outcome of an interrupted run -- and the traversal that reads it
# used to sit outside the try, so each of these raised out of a read-only report instead of
# reporting unknown. cold_today already guarded the same class with an isinstance check; this side
# did not, and nothing asserted the asymmetry, which is how the suite passed with the hole open.
for _label, _blob in [('a JSON list', []), ('null', None), ('a string', 'nope'),
                      ('days as a list', {'days': []}), ('days as null', {'days': None}),
                      ("today's entry not a dict", {'days': {iso: 7}}),
                      ("the alias's sends not countable", {'days': {iso: {A1: 15}}})]:
    json.dump(_blob, io.open(wlog, 'w', encoding='utf-8'))
    try:
        _got = RS.warm_today(A1, DAY)
    except Exception as _e:
        _got = '%s: %s' % (type(_e).__name__, _e)
    rec('a malformed log (%s) reads unknown, not zero and not a traceback' % _label,
        _got is None, _got)
json.dump('truncated', io.open(wlog, 'w', encoding='utf-8'))
io.open(wlog, 'w', encoding='utf-8').write('{"days": {')
rec('a truncated log reads unknown too', RS.warm_today(A1, DAY) is None, RS.warm_today(A1, DAY))

print('\n-- the finding the report exists to make --')
cfg = SS._load_senders()
cap = SS._ramp_cap(cfg, A1, today=DAY)
quota = WU.quota(WU.day_number(DAY))
rec('on 2026-09-22 the cold cap is 5', cap == 5, cap)
rec('on 2026-09-22 the warm-up quota is 15', quota == 15, quota)
rec('so real volume can be 20 while every cap reads 5', cap + quota == 20 > cap,
    'and the cap is what _alias_sent_today is measured against, so 15 of the 20 are unmetered')
# every docstring stripped by position, not by splitting on triple quotes: warmup.py has exactly
# one today, and split('"""')[2] would silently start checking a different slice the moment any
# function in it gains a docstring -- narrowing the check while still passing.
_wu_code, _wu_doc = _code_only(os.path.join(HERE, 'warmup.py'))
rec('warmup does not write the outreach ledger', 'mail_sent' not in _wu_code,
    'named only in its %d docstring lines, to say it does not' % _wu_doc)

# the crossover is cap arithmetic off two checked-in constants, so it is pinned. it is NOT
# the date to stop BSG Warmup: the 2026-09-23 audit found no live cold path, so the cap
# climbing past the quota does not mean real volume did. see ramp_status.py's own output.
cross = None
for i in range(60):
    d = DAY + dt.timedelta(days=i)
    if min(SS._ramp_cap(cfg, x, today=d) for x in WU.ALIASES) >= WU.quota(WU.day_number(d)):
        cross = d
        break
rec('the cold ramp first reaches the warm-up quota on 2026-09-28', cross == dt.date(2026, 9, 28), cross)

print('\n-- read-only --')
src, _rs_doc = _code_only(os.path.join(HERE, 'ramp_status.py'))
rec('never opens a file for writing',
    not re.search(r"open\([^)]*['\"][wa]", src) and not re.search(r"\bmode\s*=\s*['\"][wa]", src)
    and 'write_text' not in src and 'write_bytes' not in src and 'makedirs' not in src
    and 'shutil' not in src, 'checked against %d lines of code, docstrings excluded' % len(src.splitlines()))
rec('imports no mail transport', 'smtplib' not in src and 'sendmail' not in src)
rec('writes no ledger', '_append_ledger' not in src and 'json.dump' not in src)
rec('shells out to nothing', 'subprocess' not in src and 'os.system' not in src and 'requests' not in src)
rec('does not import cadence or the reserved surface', 'import cadence' not in src and 'optout' not in src)
rec('reuses the caps rather than reimplementing them', '_ramp_cap' in src and 'def _ramp_cap' not in src,
    'a second copy of the arithmetic is a second answer')

print('\n-- the report itself runs, and says so when a cap number is not trustworthy --')
# senders.json supplies every cap printed. _load_senders() returns {} for missing OR malformed, so a
# broken hand-edit used to print cap 0 across the board and then conclude, confidently, that the
# cold ramp never reaches the warm-up quota. It is tracked in git, so this is the hand-edit case.
import contextlib


def _run(argv, cfg_override=None):
    _lc = SS._load_senders
    if cfg_override is not None:
        SS._load_senders = lambda: cfg_override
        RS._SS._load_senders = SS._load_senders
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = RS.main(argv)
    except Exception as e:
        return '%s: %s' % (type(e).__name__, e), buf.getvalue()
    finally:
        SS._load_senders = _lc
        RS._SS._load_senders = _lc
    return code, buf.getvalue()

_code, _out = _run([])
rec('a plain run exits 0', _code == 0, _code)
rec('and marks unknown ledgers as "?" rather than 0', '?' in _out or 'unknown' in _out)

_code, _out = _run(['--days', '8'])
rec('--days 8 exits 0', _code == 0, _code)
rec('and calls the crossover a cap date, not a date to act on',
    'not a measurement' in _out and '15 -> 0' in _out,
    'the 2026-09-23 audit found no live cold path, so the cap passing the quota proves nothing')

_code, _out = _run(['--days', '8'], cfg_override={})
rec('an unreadable senders.json still exits 0, it does not traceback', _code == 0, _code)
rec('and says every cap is untrustworthy instead of printing 0 quietly',
    'senders.json is missing or unreadable' in _out, _out.splitlines()[2:3])

_code, _out = _run([], cfg_override={'ramp_start': '2026-09-21', 'main_domain': 'bsgflorida.com',
                                     'main_domain_cap': 40, 'lanes': {'a': None, 'b': A2},
                                     'ramp': [{'through_day': 9999, 'cap': 5}]})
rec('a lane mapped to null does not crash the alias list', _code == 0, _code)

_code, _out = _run(['--date', 'not-a-date'])
rec('a bad --date is a message and exit 2, not a traceback', _code == 2, _code)

print('\n%d/%d passed.' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
