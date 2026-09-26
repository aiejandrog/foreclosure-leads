"""paid_reads.py — ONE monthly dollar cap shared by every paid Miami-Dade read.

WHY THIS EXISTS (2026-09-26). Four things in this repo pay per Miami read, and each carried its own
per-run ceiling or none:

    records_liens.py    2Captcha Turnstile solves for Official Records owner searches (~$0.0033)
                        -- the nightly line had NO --max-spend at all, only --limit 120
    gen_records_qs.py   the same solve, to mint durable owner search tokens (--limit 40 owners,
                        up to three solves each)
    lis_pendens.py      the same solve, once per plaintiff name in the lender sweep (~34 names)
    run_documents.py    Claude vision / interpretation reads, and paid token mints, when the
                        document stage is switched on (DEALFLOW_DOCS=1)

The same day the cap was extended to every other paid read in the repo, so "$50 a month" means
all of them, not the nightly four:

    run_documents --backfill   the manual document backfill's Claude reads (its own cumulative
                        ceiling stays; the month is debited per read and pauses it when spent)
    run_owner_tokens.py the token-only batch's paid mints (its own $1.50 cutoff stays)
    records_probe.py    one Turnstile solve per probed search shape
    broward_plaintiff   the Broward clerk solve (stub_resolve and county_plaintiffs go through it)
    palmbeach_liens     Palm Beach Landmark reCAPTCHA v2 solves, including its worker pool, and
                        fl_lp/palmbeach and broward_judgment_dates --pb, which solve through it
    captcha_solver.py   its own live smoke test
BatchData keeps its own budget (bd_budget.py); it is not a per-read captcha or Claude spend.

A per-run cap bounds a night, not a month: four scripts each honouring their own ceiling still add
up, which is the exact lesson bd_budget.py records for BatchData. So every one of them asks THIS
module before it pays and writes what it paid here. Hands-off spend was estimated at ~$24/month
(month one ~$37, worst case ~$89); the cap is $50/month unless configured otherwise.

FAIL CLOSED. "I could not check" never reads as "go ahead":
  * the month's ledger total has reached the cap          -> paid work skipped, logged
  * the ledger exists but cannot be read or is malformed  -> paid work skipped, logged
  * the cap setting is present but is not a number >= 0   -> treated as 0, paid work skipped, logged
  * a spend could not be written to the ledger            -> no more paid work in this process
Free work (cached tokens, Camoufox mints, OCR, public court lookups) is never gated here.

Set the cap in ONE place: env DEALFLOW_PAID_MONTHLY_CAP (wins), else paid_reads.json beside the code
({"monthly_cap": 50}, gitignored), else 50. The month is the local calendar month (the laptop runs on
America/New_York). The ledger is per machine: DEALFLOW_DIR/paid_reads_ledger.json
({"2026-09": {"total": 12.34, "by": {"records_liens": 9.1, ...}}}), dollar totals only, no names.
DEALFLOW_PAID_LEDGER overrides the ledger path (the suites use it).

Precision: 2Captcha solves are counted when SUBMITTED, at the measured price (a failed solve may
still bill, and counting before the solve means a crash cannot forget it), so the ledger runs a
little high, never low. A PaidCutoffSolver task is reserved at the measured price and settled to its
own receipt. Claude reads are reserved at their worst case before the call and settled to the
billed price after; a call started and never settled keeps its worst case.

CONCURRENCY: debit() does the cap check and the ledger write as ONE step under a file lock
(ledger path + '.lock', taken over after 120s if a writer crashed), re-reading the ledger inside the
lock. Two spenders running at the same moment on this machine therefore cannot both take the last
few cents: the second re-reads the first one's write and is refused. The ledger is per machine; a
second machine running paid work would keep its own month.

    python paid_reads.py        # this month's spend, the cap, what is left, by spender
"""
import json
import math
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MONTHLY_CAP = 50.0
ENV_CAP = 'DEALFLOW_PAID_MONTHLY_CAP'
ENV_LEDGER = 'DEALFLOW_PAID_LEDGER'
CONFIG = os.path.join(HERE, 'paid_reads.json')          # gitignored — {"monthly_cap": 50}
LEDGER_NAME = 'paid_reads_ledger.json'
SOLVE_USD = 0.0033                                      # measured per Turnstile solve (records_liens.PAID_SOLVE_USD)
RECAPTCHA_V2_USD = 0.003                                # Palm Beach Landmark v2 solve (palmbeach_liens: '$0.003')

_LOCK = threading.Lock()
_BROKEN = {'why': ''}      # set when a spend could not be written: this process pays for nothing more
_SAID = set()              # (source, reason) already logged, so a refused loop logs once, not per item


def ledger_path():
    p = os.environ.get(ENV_LEDGER, '').strip()
    if p:
        return p
    import paths as P
    return os.path.join(P.DEALFLOW_DIR, LEDGER_NAME)


def month(now=None):
    return time.strftime('%Y-%m', time.localtime(time.time() if now is None else now))


def cap():
    """(dollars, problem). problem is '' for a good setting; a bad one yields 0 (fail closed)."""
    raw, where = os.environ.get(ENV_CAP, '').strip(), ENV_CAP
    if not raw:
        where = os.path.basename(CONFIG)
        try:
            with open(CONFIG, encoding='utf-8') as f:
                cfg = json.load(f)
        except FileNotFoundError:
            return DEFAULT_MONTHLY_CAP, ''
        except Exception as e:
            return 0.0, '%s unreadable (%s)' % (where, str(e)[:80])
        if not isinstance(cfg, dict) or 'monthly_cap' not in cfg:
            return 0.0, '%s has no monthly_cap' % where
        raw = cfg['monthly_cap']
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0, '%s=%r is not a number' % (where, raw)
    if not math.isfinite(v) or v < 0:
        return 0.0, '%s=%r is not a dollar amount >= 0' % (where, raw)
    return v, ''


def _load():
    """(ledger dict, problem). A missing file is an empty ledger; anything else unreadable is a problem."""
    path = ledger_path()
    try:
        with open(path, encoding='utf-8') as f:
            led = json.load(f)
    except FileNotFoundError:
        return {}, ''
    except Exception as e:
        return None, 'paid-reads ledger %s unreadable (%s)' % (path, str(e)[:80])
    if not isinstance(led, dict):
        return None, 'paid-reads ledger %s is not a JSON object' % path
    return led, ''


def _month_total(entry):
    if isinstance(entry, dict):
        return float(entry.get('total', 0) or 0)
    return float(entry or 0)


def status(now=None):
    """{'month','cap','spent','remaining','by','ok','why'}. ok=False means: pay for nothing."""
    m = month(now)
    c, cproblem = cap()
    led, lproblem = _load()
    out = {'month': m, 'cap': c, 'spent': 0.0, 'remaining': 0.0, 'by': {}, 'ok': False, 'why': ''}
    if lproblem:
        out['why'] = lproblem
        return out
    try:
        ent = led.get(m)
        out['spent'] = round(_month_total(ent), 4)
        out['by'] = dict(ent.get('by') or {}) if isinstance(ent, dict) else {}
    except Exception as e:
        out['why'] = 'paid-reads ledger month %s malformed (%s)' % (m, str(e)[:80])
        return out
    out['remaining'] = round(max(0.0, c - out['spent']), 4)
    if _BROKEN['why']:
        out['why'] = _BROKEN['why']
    elif cproblem:
        out['why'] = 'monthly cap setting invalid: %s' % cproblem
    elif out['spent'] >= c:
        out['why'] = ('monthly paid-reads cap reached: $%.2f of $%.2f spent in %s'
                      % (out['spent'], c, m))
    else:
        out['ok'] = True
    return out


def _say(source, why):
    if (source, why) in _SAID:
        return
    _SAID.add((source, why))
    print('  PAID READS: %s -- %s skips paid work (free paths still run)' % (why, source))


def allow(usd, source):
    """(ok, why): may `source` spend `usd` more this month? Logs a refusal once per source+reason."""
    st = status()
    if st['ok'] and st['spent'] + max(0.0, float(usd)) > st['cap'] + 1e-9:
        st['ok'] = False
        st['why'] = ('monthly paid-reads cap: $%.4f left of $%.2f in %s, next read costs ~$%.4f'
                     % (st['remaining'], st['cap'], st['month'], float(usd)))
    if not st['ok']:
        _say(source, st['why'])
        return False, st['why']
    return True, ''


def remaining(source=None):
    """Dollars this month may still spend; 0 when anything is wrong (logged if source is given)."""
    st = status()
    if not st['ok']:
        if source:
            _say(source, st['why'])
        return 0.0
    return st['remaining']


def clamp(run_cap, source):
    """A per-run dollar cap cut down to what the month has left. 0 = do no paid work this run.
    Never raises a cap: the result is at most run_cap."""
    left = remaining(source)
    if left <= 0:
        return 0.0
    if left < run_cap:
        print('  PAID READS: %s run cap $%.2f cut to $%.4f, what is left of the monthly cap'
              % (source, run_cap, left))
        return left
    return run_cap


class _FileLock:
    """Cross-process lock around the ledger's read-modify-write. A lock older than 120s is a crashed
    writer's and is taken over. Not obtained within ~10s -> the write fails (and so the spend stops)."""

    def __init__(self, path):
        self.path = path + '.lock'
        self.held = False

    def __enter__(self):
        for _ in range(100):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 120:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                time.sleep(0.1)
            except OSError:
                break
        return self

    def __exit__(self, *a):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass


def _write(usd, source, enforce):
    """Add `usd` (may be negative: a settlement refund) to this month under the ledger lock.

    enforce=True is debit(): the cap is re-checked INSIDE the lock, against the ledger as it is on
    disk at that moment, so two processes cannot both take the last few cents -- the second one
    re-reads the first one's write and is refused. Returns (ok, why)."""
    usd = float(usd or 0)
    path = ledger_path()
    why = ''
    with _LOCK:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        except OSError:
            pass
        with _FileLock(path) as lk:
            if not lk.held:
                _BROKEN['why'] = why = 'paid-reads ledger %s is locked by another writer' % path
            else:
                led, problem = _load()
                if problem:
                    _BROKEN['why'] = why = problem
                else:
                    m = month()
                    ent = led.get(m)
                    if not isinstance(ent, dict):
                        ent = {'total': _month_total(ent), 'by': {}}
                    total = float(ent.get('total', 0) or 0)
                    if enforce:
                        c, cproblem = cap()
                        if _BROKEN['why']:
                            why = _BROKEN['why']
                        elif cproblem:
                            why = 'monthly cap setting invalid: %s' % cproblem
                        elif total + usd > c + 1e-9:
                            why = ('monthly paid-reads cap: $%.4f left of $%.2f in %s, next read costs ~$%.4f'
                                   % (max(0.0, c - total), c, m, usd))
                    if not why:
                        ent['total'] = max(0.0, round(total + usd, 6))
                        by = ent.setdefault('by', {})
                        by[source] = max(0.0, round(float(by.get(source, 0) or 0) + usd, 6))
                        led[m] = ent
                        tmp = path + '.tmp'
                        for i in range(6):      # Windows: an indexer or AV scan can hold the file a moment
                            try:
                                with open(tmp, 'w', encoding='utf-8') as f:
                                    json.dump(led, f, indent=1, sort_keys=True)
                                os.replace(tmp, path)
                                return True, ''
                            except OSError:
                                time.sleep(0.25 * (i + 1))
                        _BROKEN['why'] = why = 'paid-reads ledger %s could not be written' % path
    _say(source, why)
    return False, why


def record(usd, source):
    """Add a spend that already happened. True when written. On False nothing more is paid in this
    process. Never refuses on the cap: the money is gone either way, and the ledger must say so."""
    usd = float(usd or 0)
    if usd <= 0:
        return True
    return _write(usd, source, False)[0]


def debit(usd, source):
    """ATOMIC check-and-count BEFORE a paid call: (ok, why). The cap check and the write happen
    under one file lock, so concurrent spenders on this machine cannot overrun the cap between
    "is there room?" and "I took it". Refused -> nothing written, logged once per source+reason."""
    usd = float(usd or 0)
    if usd <= 0:
        return allow(0, source)
    return _write(usd, source, True)


def adjust(delta, source):
    """Settle a debit against what the call really cost: negative gives back an over-reservation
    (a task that never billed, a read cheaper than its worst case). Totals never go below zero."""
    delta = float(delta or 0)
    if abs(delta) < 1e-12:
        return True
    return _write(delta, source, False)[0]


def guarded(solve, source, unit=SOLVE_USD):
    """Wrap a 2Captcha solve function: check the month first, count the solve BEFORE submitting it.
    Returns None (a failed solve, which every caller already handles) when the cap refuses."""
    def _solve(*args, **kwargs):
        ok, _ = debit(unit, source)            # checked and counted in one locked step
        if not ok:
            return None
        return solve(*args, **kwargs)
    _solve._paid_reads = True
    return _solve


class CutoffGuard:
    """Wrap a captcha_cost_cutoff.PaidCutoffSolver: check the month before each paid task, record
    what that task actually cost (its own receipts) after. Refusal raises CutoffStopped, which the
    run_documents token path already treats as "stop paid minting for this run"."""
    _paid_reads = True

    def __init__(self, solver, source, unit=SOLVE_USD):
        self._solver, self._source, self._unit = solver, source, unit

    def _actual(self):
        try:
            return float(self._solver.state.data.get('captcha_actual_decimal') or 0)
        except Exception:
            return None

    def __call__(self, *args, **kwargs):
        from captcha_cost_cutoff import CutoffStopped
        # reserve the measured price in one locked check-and-count, then settle to the receipt
        ok, why = debit(self._unit, self._source)
        if not ok:
            raise CutoffStopped(why)
        before = self._actual()
        try:
            return self._solver(*args, **kwargs)
        finally:
            after = self._actual()
            # an unknown receipt still cost something: keep the measured price, never nothing
            if before is not None and after is not None:
                adjust((after - before) - self._unit, self._source)

    def __getattr__(self, name):
        return getattr(self._solver, name)


def cap_budget(budget, source):
    """Put a Claude-read budget (document_interpreter.Budget, document_backfill.PersistentBudget) under
    the monthly cap, per call and atomically: each call's WORST case is debited (one locked
    check-and-count) before the call is made, and settled to its real price when it is recorded.
    Refused -> BudgetExhausted, the budget's own "stop paying" signal, with `exhausted` set so a
    backfill PAUSES instead of walking every remaining case. A call started and never settled keeps
    its worst case on the ledger (runs high, never low). Returns the same object, patched."""
    from document_interpreter import BudgetExhausted
    if getattr(budget, '_paid_reads', False):
        return budget
    inner_check = budget.check
    settle_name = 'record_read' if hasattr(budget, 'record_read') else 'record'
    inner_settle = getattr(budget, settle_name)
    held = []

    def check(input_tokens, max_output_tokens):
        worst = budget.price(input_tokens, max_output_tokens)
        ok, why = debit(worst, source)
        if not ok:
            if hasattr(budget, 'exhausted'):
                budget.exhausted = True
            raise BudgetExhausted(why)
        try:
            out = inner_check(input_tokens, max_output_tokens)
        except BaseException:
            adjust(-worst, source)                 # the budget's own cap refused: nothing was sent
            raise
        held.append(worst)
        return out

    def settle(input_tokens, output_tokens, *args, **kwargs):
        out = inner_settle(input_tokens, output_tokens, *args, **kwargs)
        if held:
            adjust(budget.price(input_tokens, output_tokens) - held.pop(), source)
        return out

    budget.check = check
    setattr(budget, settle_name, settle)          # PersistentBudget.record() calls self.record_read()
    budget._paid_reads = True
    return budget

def main(argv=None):
    st = status()
    print('paid reads, %s: $%.2f spent of $%.2f cap -> $%.2f left  (ledger %s)'
          % (st['month'], st['spent'], st['cap'], st['remaining'], ledger_path()))
    for k, v in sorted(st['by'].items(), key=lambda kv: -kv[1]):
        print('  %-28s $%.4f' % (k, v))
    if not st['ok']:
        print('  PAID WORK IS OFF: %s' % st['why'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
