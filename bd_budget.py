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
import json, os, sys
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


def spent_today():
    return round(float(_load().get(str(date.today()), 0.0)), 4)


def remaining():
    return round(max(0.0, cap() - spent_today()), 4)


def can_spend(dollars):
    """True if one more call of this size fits inside today's shared cap."""
    return (spent_today() + float(dollars)) <= cap() + 1e-9


def charge(dollars, note=''):
    """Record a spend. Called AFTER a billable request goes out — a call that reached the provider
    costs money whether or not it returned data, so misses are charged too (that is the honest
    accounting; assuming misses are free is how a budget silently overruns)."""
    led = _load()
    k = str(date.today())
    led[k] = round(float(led.get(k, 0.0)) + float(dollars), 4)
    tmp = LEDGER + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(led, f, indent=1)
    os.replace(tmp, LEDGER)                              # atomic: concurrent runs can't corrupt it
    return led[k]


def require(dollars, script=''):
    """Gate one billable call. Raises BudgetExhausted when today's shared cap is used up."""
    if not can_spend(dollars):
        raise BudgetExhausted(
            f"BatchData daily budget spent: ${spent_today():.2f} of ${cap():.2f} "
            f"(today, all scripts combined). {script} stopping.")


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
        print(f"  {d}  ${led[d]:.2f}")
    print(f"\ntoday: ${spent_today():.2f} of ${cap():.2f} cap -> ${remaining():.2f} left")
    print(f"worst case at this cap: ${cap()*30:.0f}/month")
