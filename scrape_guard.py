"""scrape_guard — refuse to replace a good lead file with a thin one. RATIO, not a fixed floor.

WHY (2026-08-27 audit)
Every writer had a guard and every guard was an absolute floor set for a much smaller board:

    county_leads.py   MIN = 10   against files holding 249 (Broward) and 314 (Palm Beach)
    foreclosure_leads len < 20   against 370 (Miami-Dade)
    lp_leads.py       none at all against 1007

So a county could collapse from 249 leads to 11 and every guard would pass, print
"DONE: 11 BROWARD leads", and publish. The board-wide publish_guard cannot catch it either: it
measures the WHOLE board (1,940), and wiping Broward to its floor still leaves 88% — well over
its 70% bar. There is no per-county coverage metric anywhere.

That is reachable, not theoretical. scrape_county() catches a per-platform calendar failure and
continues, so if the mortgage-foreclosure site is blocked while the tax-deed site answers with 15
items, the run writes a 15-row tax-deed-only file over 249 real leads — and the only visible
difference is a smaller number in a log nobody reads at 7 AM.

THE RULE
Compare against what is already on disk. A scrape may grow freely and may shrink for real
reasons (auctions happen, cases close), so the bar is deliberately loose — but a collapse to a
fraction of the previous file is a FAILURE, not a result. Absolute floor stays as the
first-run/empty case.

    keep = new >= max(min_abs, prev * min_ratio)

min_ratio 0.55 tolerates a genuinely heavy churn day — Broward 249 -> 137 still writes — while a
scrape that lost most of its rows is refused and the last good file survives to be published
again. An operator who really means it passes force=True (--force on the CLI).
"""
import json
import os


def prev_count(path):
    """How many rows the file on disk holds. 0 when absent/unreadable — i.e. first run, allow."""
    if not os.path.exists(path):
        return 0
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        return 0
    try:
        return len(data)
    except Exception:
        return 0


def check(label, new_count, path, min_abs=10, min_ratio=0.55, force=False):
    """-> (ok, message). ok=False means DO NOT WRITE; the caller keeps the existing file.

    Never raises and never writes anything itself — the decision belongs to the caller, so a
    guard bug can't be the thing that destroys the data it exists to protect."""
    prev = prev_count(path)
    bar = max(int(min_abs), int(prev * min_ratio))
    passes = new_count >= bar
    if passes:
        extra = ''
        if prev and new_count < prev:
            extra = ' (down %d from %d — within tolerance)' % (prev - new_count, prev)
        return True, '%s: %d row(s)%s' % (label, new_count, extra)
    if force:
        # Say FORCED. A forced write that logged "within tolerance" would read, in tomorrow's log,
        # exactly like a normal run — and the whole point of this module is that a log must never
        # make a data loss look routine.
        return True, ('%s: %d row(s) — FORCED over the guard (existing file holds %d)'
                      % (label, new_count, prev))
    return False, (
        'ABORT %s: scraped %d row(s) but the existing file holds %d — that is a %.0f%% collapse, '
        'not a result. Keeping the last good file. A blocked source, a changed selector and a '
        'genuinely empty day all look identical here, so this refuses rather than guesses. '
        'Re-run when the source is healthy, or pass --force if the drop is real.'
        % (label, new_count, prev, (1 - (new_count / prev)) * 100 if prev else 0))
