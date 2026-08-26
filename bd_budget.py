"""Shared BatchData spend ledger + HARD daily cap.

WHY THIS EXISTS (2026-08-02). The operator put $15 into BatchData and it vanished; he put in $50
and it was gone in about five minutes. Nothing was stealing it — the pipeline was simply built to
spend without a ceiling, from more places than anyone was tracking:

  * TWO BatchData products are billed, not one:
      skiptrace.py        -> /property/skip-trace          ~$0.15/lookup
      batchdata_liens.py  -> /property/lookup/all-attributes  (also billed, price unconfirmed)
  * refresh-dealflow.bat runs BOTH every time: `--all --limit 120` (=$18) + `--all --limit 80`.
  * TWO scheduled tasks ran that same .bat daily — "DEALFLOW Daily Scrape" 7am and
    "DEALFLOW Refresh" 9am — so the whole bill was paid twice a day.
  * A per-script --max-spend can't help: three scripts each honouring their own $70 ceiling still
    add up to $210. A ceiling is only real if every spender shares ONE counter.

So: every BatchData call in this repo asks THIS module for permission first and records what it
spent. The cap is per calendar day, shared across scripts, schedulers, and concurrent runs. Hitting
it is a clean stop (the caller exits non-zero), never a silent overspend.

Set the cap in ONE place — bd_budget.json  {"daily_cap": 1.50}  — or env BATCHDATA_DAILY_CAP.
$1.50/day is ~$45/month worst case; typical days cost far less because results are cached forever
and only NEW leads are ever charged.

  python bd_budget.py            # show today's spend, the cap, and what's left
  python bd_budget.py --cap 2    # set the daily cap
"""
import json, os, sys, threading
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, 'batchdata_spend.json')     # gitignored — {'YYYY-MM-DD': dollars}
CONFIG = os.path.join(HERE, 'bd_budget.json')           # gitignored — {'daily_cap': 1.50}
DEFAULT_CAP = 1.50


class BudgetExhausted(Exception):
    """Today's shared BatchData budget is spent. Callers stop cleanly; they do NOT keep calling."""


def cap():
    """Dollars allowed per calendar day, across every BatchData script. env wins over the file."""
    env = os.environ.get('BATCHDATA_DAILY_CAP', '').strip()
    if env:
        try: return float(env)
        except ValueError: pass
    try:
        return float(json.load(open(CONFIG, encoding='utf-8')).get('daily_cap', DEFAULT_CAP))
    except Exception:
        return DEFAULT_CAP


def _load():
    try:
        return json.load(open(LEDGER, encoding='utf-8'))
    except Exception:
        return {}


def _day_total(entry):
    """A day's total dollars, whichever ledger format the day was written in.

    Two formats coexist on purpose:
      old  {'2026-08-15': 10.0}                                   (plain float)
      new  {'2026-08-16': {'total': 10.0, 'by': {'wp': 6.6, ...}}} (attributed)
    The old days must keep loading forever — a ledger that forgets history the day its schema
    improves is a ledger that cannot be audited, which is the exact failure this fixes.
    """
    if isinstance(entry, dict):
        return float(entry.get('total', 0.0))
    return float(entry or 0.0)


def spent_today():
    return round(_day_total(_load().get(str(date.today()))), 4)


def remaining():
    return round(max(0.0, cap() - spent_today()), 4)


def can_spend(dollars):
    """True if one more call of this size fits inside today's shared cap."""
    return (spent_today() + float(dollars)) <= cap() + 1e-9


# In-process serialisation for charge(). The tmp+os.replace below is atomic against
# CORRUPTION, but it is still a load-modify-write: two threads that read the same ledger both add
# their own spend and the second write erases the first. That never mattered while every caller was
# single-threaded. palmbeach_liens.py --workers runs concurrent 2Captcha solves as of 2026-08-26,
# each charging $0.003 on completion, so the races are now real. Cross-PROCESS races remain (two
# schedulers on one box) -- unchanged from before, and the reason the cap is a soft ceiling on the
# 2captcha line rather than a hard gate.
_CHARGE_LOCK = threading.Lock()


def charge(dollars, note=''):
    """Record a spend. Called AFTER a billable request goes out — a call that reached the provider
    costs money whether or not it returned data, so misses are charged too (that is the honest
    accounting; assuming misses are free is how a budget silently overruns).

    The `note` is STORED now. It was accepted and discarded for the ledger's whole life, so a
    $10.00 cap-out day could not say who spent it — on 08-16 attributing $6.30 of pre-6AM spend
    took cross-referencing three logs. Every caller already passes a meaningful note ('wp',
    'wp-miss', 'tracerfy-dnc', ...); now it lands in the ledger."""
    with _CHARGE_LOCK:
        led = _load()
        k = str(date.today())
        day = led.get(k)
        if not isinstance(day, dict):
            day = {'total': _day_total(day), 'by': {}}
        day['total'] = round(day['total'] + float(dollars), 4)
        nk = note or 'unattributed'
        day['by'][nk] = round(float(day['by'].get(nk, 0.0)) + float(dollars), 4)
        led[k] = day
        tmp = LEDGER + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(led, f, indent=1)
        os.replace(tmp, LEDGER)                              # atomic: concurrent runs can't corrupt it
        return day['total']


def breakdown_today():
    """'wp $6.60 · skiptrace $3.40' — empty string for old-format days with no attribution."""
    day = _load().get(str(date.today()))
    if not isinstance(day, dict) or not day.get('by'):
        return ''
    by = sorted(day['by'].items(), key=lambda kv: -kv[1])
    return ' · '.join(f"{k} ${v:.2f}" for k, v in by)


def require(dollars, script=''):
    """Gate one billable call. Raises BudgetExhausted when today's shared cap is used up."""
    if not can_spend(dollars):
        bd = breakdown_today()
        raise BudgetExhausted(
            f"BatchData daily budget spent: ${spent_today():.2f} of ${cap():.2f} "
            f"(today, all scripts combined{': ' + bd if bd else ''}). {script} stopping.")


def banner(script, per_call):
    """One line every spender prints at startup so the operator always sees the money position."""
    return (f"budget: ${spent_today():.2f} spent today of ${cap():.2f} cap "
            f"-> ${remaining():.2f} left = ~{int(remaining() / per_call) if per_call else 0} more "
            f"lookups for {script}")


if __name__ == '__main__':
    if '--cap' in sys.argv:
        v = float(sys.argv[sys.argv.index('--cap') + 1])
        json.dump({'daily_cap': v}, open(CONFIG, 'w', encoding='utf-8'), indent=1)
        print(f"daily cap set to ${v:.2f}  (~${v*30:.0f}/month worst case)")
    led = _load()
    print(f"BatchData spend ledger ({LEDGER})")
    for d in sorted(led)[-14:]:
        e = led[d]
        if isinstance(e, dict) and e.get('by'):
            by = ' · '.join(f"{k} ${v:.2f}" for k, v in sorted(e['by'].items(), key=lambda kv: -kv[1]))
            print(f"  {d}  ${_day_total(e):.2f}   ({by})")
        else:
            print(f"  {d}  ${_day_total(e):.2f}")
    print(f"\ntoday: ${spent_today():.2f} of ${cap():.2f} cap -> ${remaining():.2f} left")
    print(f"worst case at this cap: ${cap()*30:.0f}/month")
