"""_paidcaptest -- one monthly dollar cap for every paid Miami read, failing closed.

Run:  python _paidcaptest.py     (exit 0 = pass; no network, no browser, no real spend)

2026-09-26: the nightly records_liens.py line carried no --max-spend at all, and the four paid
Miami spenders (records_liens and gen_records_qs 2Captcha solves, the lis pendens sweep's solves,
run_documents' Claude reads) each had at most a per-run ceiling. paid_reads.py adds one monthly
cap they all check before paying. This suite pins the cap setting, the ledger, the fail-closed
cases, and each spender's wiring, with fake solvers and a throwaway ledger.

Second pass (same day): the manual document backfill (run_documents --backfill), run_owner_tokens,
records_probe, the Broward clerk solve (broward_plaintiff, used by stub_resolve / county_plaintiffs)
and the Palm Beach Landmark v2 solves (palmbeach_liens, used by fl_lp/palmbeach and
broward_judgment_dates --pb) come under the same cap, and the cap check and the ledger write became
one locked step (debit), so concurrent spenders cannot overrun it. Sections 10-16.
"""
import io
import json
import os
import sys
import tempfile
import types
import contextlib
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix='paidcap_')
os.environ['DEALFLOW_PAID_LEDGER'] = os.path.join(TMP, 'paid_reads_ledger.json')
os.environ.pop('DEALFLOW_PAID_MONTHLY_CAP', None)

import paid_reads as PR

PR.CONFIG = os.path.join(TMP, 'paid_reads.json')      # never the real config beside the code
FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


def fresh(cap=None, spent=None, month=None):
    """A clean ledger (optionally pre-loaded with this month's spend) and a clean process state."""
    for p in (os.environ['DEALFLOW_PAID_LEDGER'], PR.CONFIG):
        if os.path.exists(p):
            os.remove(p)
    os.environ.pop(PR.ENV_CAP, None)
    if cap is not None:
        os.environ[PR.ENV_CAP] = str(cap)
    if spent is not None:
        json.dump({month or PR.month(): {'total': spent, 'by': {'earlier': spent}}},
                  open(os.environ['DEALFLOW_PAID_LEDGER'], 'w'))
    PR._BROKEN['why'] = ''
    PR._SAID.clear()


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        r = fn(*a, **k)
    return r, out.getvalue()


# ---- 1. the cap setting -------------------------------------------------------------------------
fresh()
check('default monthly cap is $50', PR.cap() == (50.0, ''))
fresh(cap='35')
check('env DEALFLOW_PAID_MONTHLY_CAP sets it', PR.cap() == (35.0, ''))
fresh()
json.dump({'monthly_cap': 20}, open(PR.CONFIG, 'w'))
check('paid_reads.json sets it when the env does not', PR.cap() == (20.0, ''))
os.environ[PR.ENV_CAP] = '40'
check('env wins over the file', PR.cap() == (40.0, ''))
for bad in ('fifty', 'nan', 'inf', '-5'):
    fresh(cap=bad)
    c, why = PR.cap()
    check('a bad cap setting (%s) fails CLOSED to $0, with a reason' % bad, c == 0.0 and why, (c, why))
    ok, _ = quiet(PR.allow, 0.0033, 'test')
    check('...and refuses paid work (%s)' % bad, ok[0] is False)
fresh()
open(PR.CONFIG, 'w').write('{not json')
check('an unreadable paid_reads.json fails closed', PR.cap()[0] == 0.0)
fresh(cap='0')
check('a cap of 0 means no paid reads at all', quiet(PR.allow, 0.0033, 'test')[0][0] is False)

# ---- 2. the ledger -------------------------------------------------------------------------------
fresh()
check('an empty month allows a solve', PR.allow(0.0033, 'test') == (True, ''))
check('record() writes', PR.record(0.25, 'records_liens') and PR.record(0.10, 'lis_pendens')
      and PR.record(0.05, 'records_liens'))
st = PR.status()
check('the month total and per-spender split add up',
      abs(st['spent'] - 0.40) < 1e-9 and abs(st['by']['records_liens'] - 0.30) < 1e-9
      and abs(st['by']['lis_pendens'] - 0.10) < 1e-9, st)
led = json.load(open(os.environ['DEALFLOW_PAID_LEDGER']))
check('the ledger holds dollar totals keyed by month and spender, nothing else',
      list(led) == [PR.month()] and set(led[PR.month()]) == {'total', 'by'}, led)

fresh(spent=49.999)
ok, out = quiet(PR.allow, 0.0033, 'records_liens')
check('a solve that would pass the cap is refused', ok[0] is False and 'cap' in ok[1], ok)
check('the refusal is logged, naming the spender', 'PAID READS' in out and 'records_liens' in out, out)
_, out2 = quiet(PR.allow, 0.0033, 'records_liens')
check('...once per spender and reason, not once per item', out2 == '', out2)
fresh(spent=50.0)
check('a month that has reached the cap refuses everything', quiet(PR.allow, 0.0, 'x')[0][0] is False)
check('remaining() is 0 at the cap', quiet(PR.remaining, 'x')[0] == 0.0)
fresh(spent=60.0, month='1999-12')
check('another month\'s total does not count against this month', PR.allow(0.0033, 'x')[0] is True)
check('month() is the local calendar month', PR.month(0) == __import__('time').strftime(
    '%Y-%m', __import__('time').localtime(0)))

fresh()
open(os.environ['DEALFLOW_PAID_LEDGER'], 'w').write('{"2026-09": ')
ok, _ = quiet(PR.allow, 0.0033, 'x')
check('FAIL CLOSED: an unreadable ledger refuses paid work', ok[0] is False and 'unreadable' in ok[1], ok)
check('...and record() will not overwrite it', quiet(PR.record, 0.01, 'x')[0] is False)
json.dump({}, open(os.environ['DEALFLOW_PAID_LEDGER'], 'w'))     # readable again; process state kept
check('...and after a failed write this process pays for nothing more, even once it is readable',
      quiet(PR.allow, 0.0, 'x')[0][0] is False)
fresh()
json.dump([1, 2], open(os.environ['DEALFLOW_PAID_LEDGER'], 'w'))
check('FAIL CLOSED: a ledger that is not an object refuses', quiet(PR.allow, 0.0033, 'x')[0][0] is False)

fresh()
_real = os.environ['DEALFLOW_PAID_LEDGER']
os.environ['DEALFLOW_PAID_LEDGER'] = TMP            # a directory: the write cannot succeed
w, _ = quiet(PR.record, 0.01, 'x')
os.environ['DEALFLOW_PAID_LEDGER'] = _real
check('a spend that cannot be written returns False', w is False)
check('...and stops all further paid work in this process', quiet(PR.allow, 0.0, 'x')[0][0] is False)

fresh(spent=49.0)
check('clamp cuts a run cap to what the month has left', abs(quiet(PR.clamp, 5.0, 'x')[0] - 1.0) < 1e-9)
check('clamp never raises a run cap', PR.clamp(0.5, 'x') == 0.5)
fresh(spent=50.0)
check('clamp is 0 when the month is spent', quiet(PR.clamp, 1.0, 'x')[0] == 0.0)

# ---- 3. the solve wrapper ------------------------------------------------------------------------
calls = []
def fake_solve(*a, **k):
    calls.append(a)
    return None                                      # a failed solve: may still bill
fresh(cap='0.01')
g = PR.guarded(fake_solve, 'test')
for _ in range(5):
    quiet(g, 'key', 'https://example.invalid/')
check('guarded(): solves stop at the cap (3 x $0.0033 fit under $0.01, the 4th does not)', len(calls) == 3, len(calls))
check('guarded(): every submitted solve is counted, failed or not', abs(PR.status()['spent'] - 0.0099) < 1e-9,
      PR.status())

# ---- 4. records_liens --------------------------------------------------------------------------
import records_liens as RL
_cs = types.ModuleType('captcha_solver')
_cs.calls = []
_cs.solve_turnstile = lambda *a, **k: _cs.calls.append(1)     # returns None: a failed solve
_cs.balance = lambda: 10.0
_real_cs = sys.modules.get('captcha_solver')
sys.modules['captcha_solver'] = _cs
_sv = dict(RL._SPEND)
try:
    fresh(cap='0.01')
    RL._SPEND.update(cap=None, submits=0, bal0=None, prior=0.0, ledger=None, stopped='', unit=RL.PAID_SOLVE_USD, lock=None)
    quiet(RL.fetch_via_turnstile, ('OWNERA', ''))
    quiet(RL.fetch_via_turnstile, ('OWNERB', ''))
    check('records_liens with NO --max-spend still stops at the monthly cap (3 solves, then none)',
          len(_cs.calls) == 3 and 'cap' in RL._SPEND['stopped'], (len(_cs.calls), RL._SPEND['stopped']))
    check('records_liens books each submitted solve on the monthly ledger',
          abs(PR.status()['by'].get('records_liens', 0) - 0.0099) < 1e-9, PR.status())
    _cs.calls[:] = []
    fresh()
    RL._SPEND.update(cap=0.50, submits=0, bal0=10.0, prior=0.0, ledger=None, stopped='', unit=RL.PAID_SOLVE_USD, lock=None)
    for _ in range(80):
        quiet(RL.fetch_via_turnstile, ('OWNERC', ''))
    check('--max-spend 0.50 still binds first when the month has room (~151 solves, not more)',
          len(_cs.calls) == 151 and 'cap' in RL._SPEND['stopped'], (len(_cs.calls), RL._SPEND['stopped']))
    _cs.calls[:] = []
    fresh(spent=50.0)
    RL._SPEND.update(cap=0.50, submits=0, bal0=10.0, prior=0.0, ledger=None, stopped='', unit=RL.PAID_SOLVE_USD, lock=None)
    quiet(RL.fetch_via_turnstile, ('OWNERD', ''))
    check('a month already at the cap: records_liens submits nothing', _cs.calls == [], len(_cs.calls))

    # ---- 5. gen_records_qs ----------------------------------------------------------------------
    import gen_records_qs as G
    _cs.calls[:] = []
    fresh(spent=50.0)
    r, _ = quiet(G.mint_qs, ('SURNAME', 'GIVEN'))
    check('gen_records_qs: the default (paid) mint submits nothing once the month is spent',
          r == (None, 0) and _cs.calls == [], (r, len(_cs.calls)))
    fresh()
    quiet(G.mint_qs, ('SURNAME', 'GIVEN'))
    check('gen_records_qs: with room, its solves are counted under its own name',
          len(_cs.calls) == 3 and abs(PR.status()['by'].get('gen_records_qs', 0) - 0.0099) < 1e-9, PR.status())
    _own = []
    fresh(spent=50.0)
    quiet(G.mint_qs, ('SURNAME', 'GIVEN'), solver=lambda *a: _own.append(1))
    check('gen_records_qs: a caller-supplied solver is left to its caller (run_documents guards it)', len(_own) == 3)

    # ---- 6. lis pendens sweep -------------------------------------------------------------------
    import lis_pendens as LP
    _cs.calls[:] = []
    fresh(spent=50.0)
    LP.SWEEP_BLOCKED[:] = []
    out, _ = quiet(LP.lp_sweep, days=30)
    check('lis pendens: a spent month searches no plaintiff and pays nothing', out == [] and _cs.calls == [])
    check('lis pendens: every unsearched name is reported blocked (main() then says failed, not clean)',
          LP.SWEEP_BLOCKED == list(LP.PLAINTIFFS), len(LP.SWEEP_BLOCKED))
    _cs.calls[:] = []
    fresh(cap='0.02')
    LP.SWEEP_BLOCKED[:] = []
    quiet(LP.lp_sweep, days=30)
    check('lis pendens: the sweep stops mid-list at the cap (6 solves under $0.02) and names the rest',
          len(_cs.calls) == 6 and len(LP.SWEEP_BLOCKED) == len(LP.PLAINTIFFS), (len(_cs.calls), len(LP.SWEEP_BLOCKED)))
finally:
    RL._SPEND.clear(); RL._SPEND.update(_sv)
    if _real_cs is not None:
        sys.modules['captcha_solver'] = _real_cs
    else:
        sys.modules.pop('captcha_solver', None)

# ---- 7. run_documents: vision is clamped to the month and booked as it lands ----------------------
import run_documents as RD
import document_vision as DV
import document_case_budget as CB
seen = {}


def process(entry, qs, **kw):
    alloc = kw.get('vision_budget')
    seen.setdefault('allocs', []).append(alloc)
    if alloc is not None:
        # one settled Claude read, through the same PersistentBudget calls document_vision makes
        alloc.budget.check(1000, 100)
        alloc.budget.record(1000, 100)
    return {'c_documents': {'fully_read': 0}, 'conclusion': 'test'}


def run_nightly(spent):
    fresh(spent=spent)
    seen.clear()
    rows = [{'Case #': '2099-00000%d-CA-01' % i, 'county': 'MIAMI-DADE', 'owner_clean': 'TEST'} for i in (1, 2)]
    with tempfile.TemporaryDirectory() as folder, \
            patch.object(RD, '_lead_rows', return_value=rows), \
            patch.object(RD, 'dossier_path', side_effect=lambda county, name: Path(folder) / (name + '.json')), \
            patch.object(RD, 'DocumentQueue', side_effect=lambda: __import__(
                'document_queue').DocumentQueue(str(Path(folder) / 'queue.db'))), \
            patch.object(DV.VisionReader, 'client', return_value=object()), \
            patch.object(RD, '_print_case', lambda d: None), \
            patch.object(RD, 'summarize', return_value={'cases': 0, 'skipped_no_token': 0,
                                                         'with_a_document_read': 0, 'judgment_found': 0,
                                                         'judgment_satisfied': 0, 'complete': 0}), \
            patch.object(RD, 'run_case', side_effect=process):
        code, out = quiet(RD.main, ['--enable', '--vision', '--vision-max-spend', '1.00', '--no-ocr'])
    return code, out


code, out = run_nightly(50.0)
check('run_documents: a spent month runs WITHOUT vision (free OCR path only), exit 0',
      code == 0 and seen['allocs'] and all(a is None for a in seen['allocs']), (code, seen))
check('run_documents: ...and says so in the log', 'PAID READS' in out and 'run_documents vision' in out, out[-400:])
code, out = run_nightly(49.60)
a0 = seen['allocs'][0]
check('run_documents: the night\'s $1.00 vision cap is cut to the $0.40 the month has left',
      isinstance(a0, CB.CaseAllocator) and abs(a0.budget.limit - 0.40) < 1e-9, getattr(getattr(a0, 'budget', None), 'limit', None))
_by = PR.status()['by'].get('run_documents vision', 0)
check('run_documents: each settled vision read is booked on the monthly ledger as it lands',
      _by > 0 and abs(_by - a0.budget.spent) < 1e-9, (_by, a0.budget.spent))
code, out = run_nightly(0.0)
check('run_documents: with room, the vision cap is unchanged (never raised)',
      abs(seen['allocs'][0].budget.limit - 1.00) < 1e-9)

# ---- 8. run_documents token path: CutoffGuard ----------------------------------------------------
from captcha_cost_cutoff import CutoffStopped


class FakeCutoff:
    def __init__(self):
        self.state = types.SimpleNamespace(data={'captcha_actual_decimal': '0'})
        self.n = 0

    def __call__(self, site_key, page_url):
        self.n += 1
        self.state.data['captcha_actual_decimal'] = str(float(self.state.data['captcha_actual_decimal']) + 0.00145)
        return 'tok'

    def finish(self):
        return {'finished': True}


fresh()
fc = FakeCutoff()
gd = PR.CutoffGuard(fc, 'run_documents tokens')
gd('k', 'https://example.invalid/'); gd('k', 'https://example.invalid/')
check('CutoffGuard books the ACTUAL receipt of each paid task, not an estimate',
      abs(PR.status()['by'].get('run_documents tokens', 0) - 0.0029) < 1e-9, PR.status())
check('CutoffGuard passes other attributes through (finish)', gd.finish() == {'finished': True})
fresh(spent=50.0)
try:
    quiet(gd, 'k', 'https://example.invalid/')
    raised = False
except CutoffStopped:
    raised = True
check('CutoffGuard raises CutoffStopped (stop paid minting) when the month is spent, no task sent',
      raised and fc.n == 2)


class Ladder:
    def __init__(self):
        self.solver = FakeCutoff()

    def search_token(self, owner, parts):
        return self.solver('k', 'https://example.invalid/'), 1


fresh()
ld = Ladder()
with patch.object(sys.modules['records_liens'], 'split_owner', return_value=('SMITH', 'JANE')), \
        patch.object(RD, 'QS_CACHE', os.path.join(TMP, 'qs.json')):
    quiet(RD.mint_token, 'SMITH JANE', {}, ladder=ld)
check('run_documents.mint_token wraps the ladder\'s paid solver in the monthly guard',
      isinstance(ld.solver, PR.CutoffGuard) and PR.status()['by'].get('run_documents tokens', 0) > 0)

# ---- 9. the nightly line ---------------------------------------------------------------------------
bat = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='utf-8', errors='replace').read()
rl = [ln for ln in bat.splitlines() if ln.startswith('python -u records_liens.py')]
check('refresh-dealflow.bat: the nightly records_liens line carries --max-spend 0.50',
      len(rl) == 1 and '--max-spend 0.50' in rl[0] and '--spend-ledger' not in rl[0], rl)

# ---- 10. debit(): the check and the count are ONE locked step ------------------------------------
fresh(cap='0.01')
r1 = PR.debit(0.006, 'a'); r2, out = quiet(PR.debit, 0.006, 'b')
check('debit(): takes room that exists, refuses what would pass the cap', r1 == (True, '') and r2[0] is False, (r1, r2))
check('debit(): a refused debit writes nothing', abs(PR.status()['spent'] - 0.006) < 1e-9, PR.status())
check('debit(): a refusal is not a broken ledger (other, smaller reads still fit)',
      PR._BROKEN['why'] == '' and PR.debit(0.003, 'c')[0] is True)
check('adjust(): a negative settlement gives money back', PR.adjust(-0.003, 'c') and abs(PR.status()['spent'] - 0.006) < 1e-9)
check('adjust(): totals never go below zero', PR.adjust(-5, 'a') and PR.status()['spent'] == 0.0
      and PR.status()['by'].get('a') == 0.0, PR.status())
fresh(cap='bogus')
check('debit(): a bad cap setting refuses (fail closed)', quiet(PR.debit, 0.001, 'x')[0][0] is False)

# many processes at once against a small cap: the total can never pass it
import subprocess
fresh(cap='0.05')
_race = ("import os,sys; sys.path.insert(0, %r); import paid_reads as PR\n"
         "n=0\n"
         "for _ in range(12):\n"
         "    n += PR.debit(0.0033, 'race')[0]\n"
         "print(n)\n") % HERE
_env = dict(os.environ)
procs = [subprocess.Popen([sys.executable, '-c', _race], env=_env, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True) for _ in range(8)]
granted = sum(int((p.communicate(timeout=120)[0] or '0').strip().splitlines()[-1] or 0) for p in procs)
st = PR.status()
check('8 processes racing for the last cents never pass the cap (%d debits granted)' % granted,
      st['spent'] <= 0.05 + 1e-9 and granted == 15 and abs(st['spent'] - granted * 0.0033) < 1e-9,
      (granted, st['spent']))

# CutoffGuard: reserve, then settle to the receipt (a task that never billed costs nothing)
class NoBill(FakeCutoff):
    def __call__(self, site_key, page_url):
        self.n += 1
        return None                                  # its own cutoff refused before createTask
fresh()
nb = PR.CutoffGuard(NoBill(), 'run_owner_tokens')
nb('k', 'u')
check('CutoffGuard: a task that never billed is refunded to $0 on the ledger',
      PR.status()['spent'] == 0.0 and nb._solver.n == 1, PR.status())

# cap_budget: the per-call reservation for Claude reads
import document_interpreter as DI
from document_backfill import PersistentBudget
from document_case_budget import MemoryState
fresh()
b = PR.cap_budget(PersistentBudget(1.0, MemoryState()), 'run_documents backfill')
w = b.price(1000, 100)
b.check(1000, 100)
check('cap_budget: the worst case is on the ledger BEFORE the call', abs(PR.status()['spent'] - w) < 1e-9)
b.record_read(1000, 20, 'k', {'x': 1})
check('cap_budget: settled to the real price after', abs(PR.status()['spent'] - b.price(1000, 20)) < 1e-9,
      (PR.status()['spent'], b.price(1000, 20)))
check('cap_budget: the budget\'s own books are unchanged', abs(b.spent - b.price(1000, 20)) < 1e-9)
fresh(spent=50.0)
try:
    b.check(1000, 100); raised = False
except DI.BudgetExhausted:
    raised = True
check('cap_budget: a spent month raises BudgetExhausted and marks the budget exhausted (backfill pauses)',
      raised and b.exhausted is True)
fresh()
tiny = PR.cap_budget(DI.Budget(0.0001), 'run_documents interpret')
try:
    tiny.check(100000, 4000); raised = False
except DI.BudgetExhausted:
    raised = True
check('cap_budget: when the budget\'s OWN cap refuses, the month\'s reservation is given back',
      raised and PR.status()['spent'] == 0.0, PR.status())

# ---- 11. run_documents --backfill: its own ledger stays, the month is debited AND respected ------
import document_backfill as BF


class _Usage:
    input_tokens, output_tokens = 1000, 50


class _Client:
    def with_options(self, **k):
        return self

    class messages:
        @staticmethod
        def count_tokens(**k):
            return types.SimpleNamespace(input_tokens=1000)

        @staticmethod
        def create(**k):
            return types.SimpleNamespace(content=[types.SimpleNamespace(type='text', text='{}')],
                                         stop_reason='end_turn', usage=_Usage())


def run_backfill(spent):
    fresh(spent=spent)
    seen.clear()
    rows = [{'Case #': '2099-00000%d-CA-01' % i, 'county': 'MIAMI-DADE', 'owner_clean': 'TEST'} for i in (1, 2)]

    def bf_process(entry, qs, **kw):
        share = kw['vision_budget'].for_case(entry['case'])
        seen.setdefault('cases', []).append(entry['case'])
        try:
            DV.VisionReader(client=_Client()).read_page(b'\x89PNG fake', share)
        except Exception as e:
            seen.setdefault('errs', []).append(type(e).__name__)
        kw['vision_budget'].finish(entry['case'])
        return {'complete': True}
    clients = []
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / 'leads.json'
        source.write_text(json.dumps(rows))
        with patch.object(RD, 'dossier_path', side_effect=lambda county, name: Path(folder) / (name + '.json')), \
                patch.object(RD, 'DocumentQueue', side_effect=lambda: __import__(
                    'document_queue').DocumentQueue(str(Path(folder) / 'queue.db'))), \
                patch.object(DV.VisionReader, 'client', side_effect=lambda *a, **k: clients.append(1) or _Client()), \
                patch.object(RD, 'run_case', side_effect=bf_process):
            code, out = quiet(RD.main, ['--backfill', '--enable', '--vision', '--leads-file', str(source),
                                        '--vision-max-spend', '1.00', '--no-ocr'])
            own = json.loads((Path(folder) / '_backfill_state.json').read_text()) \
                if (Path(folder) / '_backfill_state.json').exists() else {}
    return code, out, clients, own


code, out, clients, own = run_backfill(50.0)
check('backfill: a spent month PAUSES (exit 4) before any API client or case',
      code == 4 and clients == [] and not seen.get('cases'), (code, clients, seen))
check('backfill: ...and says why', 'monthly paid-reads cap' in out and 'PAUSED' in out, out[-300:])
code, out, clients, own = run_backfill(0.0)
_bf = PR.status()['by'].get('run_documents backfill', 0)
check('backfill: with room, each vision read is debited to the month under its own name',
      _bf > 0 and seen.get('cases') == ['2099-000001-CA-01', '2099-000002-CA-01'], (_bf, seen))
check('backfill: its own cumulative ledger still counts the same reads (kept, not replaced)',
      abs(float(own.get('actual_usd') or 0) - _bf) < 1e-9, (own.get('actual_usd'), _bf))
check('backfill: the run header shows the month\'s cap', 'monthly paid-reads cap (shared' in out)

# ---- 12. run_owner_tokens -------------------------------------------------------------------------
import run_owner_tokens as OT
made = []


class FakePaid(FakeCutoff):
    def __init__(self, *a, **k):
        super().__init__()
        made.append(self)


def run_tokens(spent):
    fresh(spent=spent)
    made.clear()
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        rows = [{'Case #': '2099-00001%d-CA-01' % i, 'county': 'MIAMI-DADE', 'owner_clean': 'OWNER%d TEST' % i,
                 'AuctionDate': '12/01/2026'} for i in range(5)]
        (base / 'leads.json').write_text(json.dumps(rows))
        (base / 'key').write_text('synthetic-test-key')

        def ladder_search(self, owner, parts):
            return self.solver('k', 'u'), 1
        with patch('captcha_cost_cutoff.PaidCutoffSolver', FakePaid), \
                patch.object(OT.TokenLadder, 'search_token', ladder_search), \
                patch.object(OT.TokenLadder, 'close', lambda self: None):
            code, out = quiet(OT.main, ['--leads-file', str(base / 'leads.json'), '--state', str(base / 'state.json'),
                                        '--key-file', str(base / 'key'), '--qs-cache', str(base / 'cache.json'),
                                        '--token-budget', '5', '--captcha-max-spend', '1.50'])
    return code, out


code, out = run_tokens(50.0)
check('run_owner_tokens: a spent month exits 4 before any paid solver exists', code == 4 and made == [], (code, made))
# room for 1.5 solves at the measured price; each task reserves $0.0033 and settles to its $0.00145
# receipt, so two fit and the third is refused before it is sent
code, out = run_tokens(50.0 - 1.5 * PR.SOLVE_USD)
check('run_owner_tokens: stops at the monthly cap mid-run (2 paid tasks fit, the 3rd is refused, no task sent)',
      made and made[0].n == 2 and '"stopped": "CutoffStopped"' in out, (made and made[0].n, out[-300:]))
check('run_owner_tokens: its tasks are booked at their real receipts under its own name',
      abs(PR.status()['by'].get('run_owner_tokens', 0) - 2 * 0.00145) < 1e-9, PR.status())

# ---- 13. records_probe ----------------------------------------------------------------------------
import records_probe as RP
_cs2 = types.ModuleType('captcha_solver'); _cs2.calls = []
_cs2.solve_turnstile = lambda *a, **k: (_cs2.calls.append(1), 'tok')[1]
with patch.dict(sys.modules, {'captcha_solver': _cs2}):
    fresh(spent=50.0)
    tok, why = quiet(RP.mint_token)[0]
    check('records_probe: a spent month mints nothing and says why', tok is None and 'cap' in why and _cs2.calls == [],
          (tok, why))
    fresh()
    tok, why = RP.mint_token()
    check('records_probe: with room, the solve runs and is booked', tok == 'tok' and len(_cs2.calls) == 1
          and abs(PR.status()['by'].get('records_probe', 0) - PR.SOLVE_USD) < 1e-9, PR.status())

# ---- 14. Broward clerk (broward_plaintiff, and stub_resolve / county_plaintiffs through it) ------
import broward_plaintiff as BP
curls, solves = [], []
with patch.object(BP, '_curl', side_effect=lambda *a, **k: curls.append(1) or ''), \
        patch.object(BP.captcha_solver, 'solve_turnstile', side_effect=lambda *a, **k: solves.append(1) or ''):
    fresh(spent=50.0)
    r, _ = quiet(BP.fetch_case_html, 'CACE-24-000001', verbose=False)
    check('broward_plaintiff: a spent month makes no request and no solve', r == '' and curls == [] and solves == [])
    r, _ = quiet(BP.resolve, 'CACE-24-000001', verbose=False)
    check('broward_plaintiff.resolve (what stub_resolve / county_plaintiffs call) pays nothing either',
          curls == [] and solves == [])
    fresh()
    land = 'id="caseSearchForm" name="__RequestVerificationToken" type="hidden" value="abc"'
with patch.object(BP, '_curl', side_effect=lambda *a, **k: curls.append(1) or land), \
        patch.object(BP.captcha_solver, 'solve_turnstile', side_effect=lambda *a, **k: solves.append(1) or ''):
    quiet(BP.fetch_case_html, 'CACE-24-000001', verbose=False)
    check('broward_plaintiff: with room, the solve runs and is booked under its name',
          solves == [1] and abs(PR.status()['by'].get('broward_plaintiff', 0) - PR.SOLVE_USD) < 1e-9, PR.status())

# ---- 15. Palm Beach Landmark (palmbeach_liens; fl_lp/palmbeach + broward_judgment_dates --pb) ------
import palmbeach_liens as PBL
v2 = []
_cs3 = types.ModuleType('captcha_solver')
_cs3.solve_recaptcha_v2 = lambda *a, **k: (v2.append(1), 'v2tok')[1]
_cs3.has_key = lambda: True
with patch.dict(sys.modules, {'captcha_solver': _cs3}):
    fresh(spent=50.0)
    t, _ = quiet(PBL.solve_token_2captcha)
    check('palmbeach_liens: a spent month solves nothing', t == '' and v2 == [])
    import time as _t
    pool = PBL._TokenPool(3)
    t0 = _t.time()
    got = pool.get(timeout=60)
    waited = _t.time() - t0
    quiet(pool.close)
    check('palmbeach_liens pool: a spent month stops every worker and get() returns at once, not after 60s',
          got == '' and waited < 5 and getattr(pool, 'refused', False) and v2 == [], (got, round(waited, 1), getattr(pool, 'refused', None), len(v2)))
    fresh()
    t = PBL.solve_token_2captcha()
    check('palmbeach_liens: with room, a v2 solve runs and is booked at the v2 price',
          t == 'v2tok' and v2 == [1] and abs(PR.status()['by'].get('palmbeach_liens', 0) - PR.RECAPTCHA_V2_USD) < 1e-9,
          PR.status())
for f in ('fl_lp/palmbeach.py', 'broward_judgment_dates.py'):
    src = open(os.path.join(HERE, f), encoding='utf-8').read()
    check('%s solves only through palmbeach_liens.solve_token_2captcha (so the cap covers it)' % f,
          'solve_token_2captcha()' in src and 'solve_recaptcha' not in src.replace('solve_token_2captcha', '')
          and 'captcha_solver' not in src)

# ---- 16. every paid 2Captcha call site in the repo goes through paid_reads ----------------------
import re as _re, subprocess as _sp
_files = _sp.run(['git', 'ls-files', '*.py'], cwd=HERE, capture_output=True, text=True).stdout.split()
_sites = []
for f in _files:
    if os.path.basename(f).startswith(('_', 'test_')) or f in ('captcha_solver.py', 'paid_reads.py'):
        continue
    for n, line in enumerate(open(os.path.join(HERE, f), encoding='utf-8', errors='replace'), 1):
        if _re.search(r'\bsolve_(turnstile|recaptcha_v[23])\(|PaidCutoffSolver\(', line) and 'def ' not in line:
            _sites.append('%s:%d' % (f, n))
_ok_sites = {'records_liens.py', 'gen_records_qs.py', 'lis_pendens.py', 'records_probe.py', 'broward_plaintiff.py',
             'palmbeach_liens.py', 'run_documents.py', 'run_owner_tokens.py'}
check('every file that calls a paid solver is one this suite covers', {s.split(':')[0] for s in _sites} <= _ok_sites,
      sorted({s.split(':')[0] for s in _sites} - _ok_sites))
_cs_src = open(os.path.join(HERE, 'captcha_solver.py'), encoding='utf-8').read()
check('captcha_solver.py\'s own live smoke test is guarded too', "paid_reads.guarded(solve_turnstile" in _cs_src)

print('\n%d failure(s)' % len(FAILS) if FAILS else '\nALL PASS')
sys.exit(1 if FAILS else 0)
