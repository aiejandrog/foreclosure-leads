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
little high, never low. Claude reads are recorded at their billed token price as each one settles.
Spenders on one machine run one after another in the nightly, so the check-then-spend window is one
solve or one page read; two paying processes at the same moment could each take the last few cents.

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


def record(usd, source):
    """Add a spend to this month. True when written. On False nothing more is paid in this process."""
    usd = float(usd or 0)
    if usd <= 0:
        return True
    path = ledger_path()
    with _LOCK:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        except OSError:
            pass
        with _FileLock(path) as lk:
            if not lk.held:
                _BROKEN['why'] = 'paid-reads ledger %s is locked by another writer' % path
            else:
                led, problem = _load()
                if problem:
                    _BROKEN['why'] = problem
                else:
                    m = month()
                    ent = led.get(m)
                    if not isinstance(ent, dict):
                        ent = {'total': _month_total(ent), 'by': {}}
                    ent['total'] = round(float(ent.get('total', 0) or 0) + usd, 6)
                    by = ent.setdefault('by', {})
                    by[source] = round(float(by.get(source, 0) or 0) + usd, 6)
                    led[m] = ent
                    tmp = path + '.tmp'
                    for i in range(6):          # Windows: an indexer or AV scan can hold the file a moment
                        try:
                            with open(tmp, 'w', encoding='utf-8') as f:
                                json.dump(led, f, indent=1, sort_keys=True)
                            os.replace(tmp, path)
                            return True
                        except OSError:
                            time.sleep(0.25 * (i + 1))
                    _BROKEN['why'] = 'paid-reads ledger %s could not be written' % path
    _say(source, _BROKEN['why'])
    return False


def guarded(solve, source, unit=SOLVE_USD):
    """Wrap a 2Captcha solve function: check the month first, count the solve BEFORE submitting it.
    Returns None (a failed solve, which every caller already handles) when the cap refuses."""
    def _solve(*args, **kwargs):
        ok, _ = allow(unit, source)
        if not ok or not record(unit, source):
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
        ok, why = allow(self._unit, self._source)
        if not ok:
            raise CutoffStopped(why)
        before = self._actual()
        try:
            return self._solver(*args, **kwargs)
        finally:
            after = self._actual()
            # an unknown receipt still cost something: count the measured price, never nothing
            spent = (after - before) if (before is not None and after is not None) else self._unit
            if spent > 0 and not record(spent, self._source):
                pass                                   # _BROKEN is set; the next call refuses

    def __getattr__(self, name):
        return getattr(self._solver, name)


class MirrorState:
    """Wrap a document_backfill-style state ({'actual_usd', 'reserved'} + save()) so every settled
    Claude read is also written to this month's ledger as it lands, not only at the end of the run."""

    def __init__(self, inner, source):
        self._inner, self._source = inner, source
        self.data = inner.data
        self._seen = float(inner.data.get('actual_usd') or 0)

    def save(self):
        self._inner.save()
        now = float(self.data.get('actual_usd') or 0)
        if now > self._seen:
            record(now - self._seen, self._source)
            self._seen = now

    def __getattr__(self, name):
        return getattr(self._inner, name)


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
