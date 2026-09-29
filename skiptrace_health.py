"""Why a stale skip-trace date is stale.

From 2026-09-18 through 2026-09-24 the nightly skip-trace ran every night and Tracerfy refused it
(`Insufficient credits. Instant trace requires 5 credits per lookup. You have 1 credits.` and the
`TOP UP TRACERFY` line). healthcheck still wrote `the nightly skiptrace is not running`, because a
stale newest-trace date was the only signal it had. The job was running. The account was empty.

Two inputs, both optional:

  * the latest run slice of leads-run.log and phones-run.log (the refresh and the phones nightly
    both call skiptrace.py; an older run's refusal is not this run's)
  * tracerfy_mcp.balance_soft(), the free check_balance probe. It costs no credits and returns
    None when the URL file is missing or the network is down — a failed probe is not a balance.

The freshness line stays FAIL/WARN on age, the same thresholds as before. Only the reason changes.
A low balance (default 500 credits, env TRACERFY_LOW_CREDITS) warns while lookups still fit, so
the account can be topped up before a refusal freezes the newest-trace date.
"""
import os

# Instant lookup bills 5 credits on a hit and 0 on a miss (Tracerfy POST /v1/api/trace/lookup/).
TRACERFY_INSTANT_CREDITS = 5
DEFAULT_LOW_CREDITS = 500

# Printed by skiptrace.py on a credit refusal, and the provider's own 402 body. Exit code 2 alone
# is not enough: that code is also a bad key.
_EXHAUSTED_MARKERS = (
    'Insufficient credits',
    'TOP UP TRACERFY',
)

_LOGS = (
    ('leads-run.log', ('==================== REFRESH ',)),
    ('phones-run.log', ('==== phones-nightly ', '==== phones run ')),
)


def tracerfy_low_credit_threshold():
    """Credits at or above which the account is not 'low'. Env TRACERFY_LOW_CREDITS overrides."""
    raw = os.environ.get('TRACERFY_LOW_CREDITS', '').strip()
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return DEFAULT_LOW_CREDITS


def latest_run_slice(text, markers):
    """The most recent run in one pipeline log. No header: the tail, same bound healthcheck uses."""
    if not text:
        return ''
    cut = -1
    for marker in markers:
        i = text.rfind(marker)
        if i > cut:
            cut = i
    if cut >= 0:
        return text[cut:]
    return text[-120000:]


def latest_skiptrace_logs(directory):
    """Latest-run text from the refresh log and the phones log, concatenated. Missing files are skipped."""
    chunks = []
    for name, markers in _LOGS:
        path = os.path.join(directory, name)
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError:
            continue
        chunks.append(latest_run_slice(text, markers))
    return '\n'.join(c for c in chunks if c)


def logs_show_tracerfy_exhausted(text):
    return any(marker in (text or '') for marker in _EXHAUSTED_MARKERS)


def _as_credits(balance):
    """An int credit count, or None. True is an int in Python and is not a balance."""
    if isinstance(balance, bool) or not isinstance(balance, int):
        return None
    return balance


def freshness_cause(log_text, balance):
    """Why the newest trace is old, on the runner.

    exhausted  — cannot pay for one instant lookup, or the last run was refused and the live
                 balance could not be read (so a top-up cannot be assumed).
    ran-dry    — the last run was refused for credits, and the account can pay for a lookup now.
    not-running — nothing in the latest run says the job started and was refused.
    """
    logs_dry = logs_show_tracerfy_exhausted(log_text)
    bal = _as_credits(balance)
    if bal is not None and bal < TRACERFY_INSTANT_CREDITS:
        return 'exhausted'
    if logs_dry and bal is None:
        return 'exhausted'
    if logs_dry:
        return 'ran-dry'
    return 'not-running'


def freshness_level(age_days):
    """Unchanged from healthcheck: >4 days FAIL, >2 WARN, else PASS."""
    if age_days > 4:
        return 'FAIL'
    if age_days > 2:
        return 'WARN'
    return 'PASS'


def _credit_word(n):
    return 'credit' if n == 1 else 'credits'


def skiptrace_freshness_detail(when_iso, age_days, cached, level, is_runner, stale_note, cause,
                               balance=None):
    base = f'newest trace {when_iso} ({age_days}d old, {cached} cached)'
    if level == 'PASS':
        return base
    # A non-runner's trace file is a frozen copy. Do not explain it as a live outage.
    if not is_runner:
        return base + (stale_note or '')
    if cause == 'exhausted':
        clause = ' — Tracerfy credits exhausted: top up'
        bal = _as_credits(balance)
        if bal is not None:
            clause += f' ({bal} {_credit_word(bal)} left)'
    elif cause == 'ran-dry':
        clause = ' — last run refused: Tracerfy credits were exhausted (balance can pay for a lookup now)'
    else:
        clause = ' — the nightly skiptrace is not running'
    return base + clause + (stale_note or '')


def tracerfy_balance_warning(balance, low_at=None, suppress_exhausted=False):
    """(level, name, detail) or None. None when the probe did not return a number."""
    bal = _as_credits(balance)
    if bal is None:
        return None
    if low_at is None:
        low_at = tracerfy_low_credit_threshold()
    if bal < TRACERFY_INSTANT_CREDITS:
        if suppress_exhausted:
            return None
        return ('WARN', 'tracerfy balance',
                f'{bal} {_credit_word(bal)} — Tracerfy credits exhausted: top up')
    if bal < low_at:
        return ('WARN', 'tracerfy balance',
                f'{bal} credits left — low (warn under {low_at}); top up before the nightly runs dry')
    return None


def probe_tracerfy_balance():
    """Free credit balance, or None. Never raises and never records spend."""
    try:
        import tracerfy_mcp
        return tracerfy_mcp.balance_soft()
    except Exception:
        return None


def report_skiptrace_health(add, age_days, when_iso, cached, is_runner, stale_note,
                            log_text, balance, low_at=None):
    """The freshness line plus the low-balance warning. `add` is healthcheck.add."""
    if low_at is None:
        low_at = tracerfy_low_credit_threshold()
    level = freshness_level(age_days)
    cause = freshness_cause(log_text if is_runner else '', balance)
    add(level, 'skiptrace freshness', skiptrace_freshness_detail(
        when_iso, age_days, cached, level, is_runner, stale_note, cause, balance))
    # On the runner the FAIL line already says to top up. A non-runner's freshness line does
    # not (its trace file is a frozen copy), so the account-level warning still has to fire.
    warn = tracerfy_balance_warning(
        balance, low_at,
        suppress_exhausted=(is_runner and level != 'PASS' and cause == 'exhausted'))
    if warn:
        add(*warn)
    return level, cause
