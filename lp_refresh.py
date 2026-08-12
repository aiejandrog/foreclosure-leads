#!/usr/bin/env python
"""lp_refresh.py — THE one way to run the lis pendens chain, in the only order that works.

WHY THIS EXISTS (2026-08-10, learned the expensive way): lp_resolve.main() merges its output
with whole-row replacement, which strips everything lp_values wrote; running lp_leads.py after
that puts value=0 / dor_desc='' / hs=False on the board and silently kills equity ranking for
every LP lead. The order is load-bearing and nothing enforced it — so now this does:

    lis_pendens  ->  lp_resolve  ->  lp_resolve2  ->  lp_values  ->  lp_status  ->  lp_leads

Each step is fail-fast: a non-zero exit stops the chain so a half-updated lis_pendens.json is
never promoted onto the board. `--rebuild` tacks make_tracker on the end. `--no-sweep` skips
the (captcha-billing) sweep and just re-runs enrichment over the existing file.

Run:  python lp_refresh.py                 # sweep 45d + full chain
      python lp_refresh.py --days 30
      python lp_refresh.py --no-sweep      # enrichment only, zero captcha spend
      python lp_refresh.py --rebuild       # ...and rebuild docs/index.html at the end
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(label, args_, ok=(0,)):
    """Run one chain step. `ok` lists exit codes that are NOT failures — the LP phone step exits 5
    when the shared daily spend cap is used up, which is the budget working as designed, not a
    broken chain: the board must still rebuild with the leads it already has."""
    print(f'\n===== {label} =====', flush=True)
    r = subprocess.run([sys.executable, '-u'] + args_, cwd=HERE)
    if r.returncode in ok:
        if r.returncode:
            print(f'({label} exited {r.returncode} — benign, chain continues)', flush=True)
        return
    if r.returncode != 0:
        print(f'\nCHAIN STOPPED at {label} (exit {r.returncode}) — nothing after it ran, '
              f'the board was NOT touched.', file=sys.stderr)
        sys.exit(r.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=45)
    ap.add_argument('--no-sweep', action='store_true', help='skip the captcha-billing sweep')
    ap.add_argument('--rebuild', action='store_true', help='make_tracker at the end')
    a = ap.parse_args()

    if not a.no_sweep:
        run('SWEEP (lis_pendens, all 3 counties)',
            ['lis_pendens.py', '--days', str(a.days), '--county', 'all'])
    run('RESOLVE (lp_resolve)', ['lp_resolve.py'])
    run('RESOLVE PASS 2 (lp_resolve2)', ['lp_resolve2.py'])
    # Broward rows carry no legal description, so the MD ladder above skips them — the BCPA
    # name ladder is their only path to an address. Runs before lp_values on purpose: it writes
    # its own value/homestead from the cadastral, and lp_values' MD cache would skip them anyway.
    run('RESOLVE BROWARD (BCPA name ladder)', [os.path.join('fl_lp', 'broward_resolve.py')])
    run('VALUE (lp_values)', ['lp_values.py'])
    run('CASE STATUS (lp_status)', ['lp_status.py'])
    run('BOARD ROWS (lp_leads)', ['lp_leads.py'])
    # FAST-LANE PHONES. A fresh filing with no phone is a lead you cannot be first to — the worker's
    # EARLY lane ("be the first call") has nothing to dial and the funnel dumps it into 'trace'.
    # Runs AFTER lp_leads so it only sees rows that actually resolved to a high-confidence address,
    # and it is bounded twice: --limit here and bd_budget's shared daily dollar cap inside skiptrace
    # (a spent budget exits 5, which run() treats as benign). ~$0.10/hit on Tracerfy.
    run('PHONES (LP fast lane)', ['skiptrace.py', '--lp-fresh', '45', '--limit', '25'], ok=(0, 5))
    if a.rebuild:
        run('REBUILD (make_tracker)', ['-c',
            "import json, foreclosure_leads as F; "
            "F.make_tracker(json.load(open('leads_final.json', encoding='utf-8')))"])

    # freshness stamp for healthcheck + anything that wants the as-of date
    try:
        lp = json.load(open(os.path.join(HERE, 'lis_pendens.json'), encoding='utf-8'))
        newest = max((x.get('date') or '' for x in lp), default='')
        json.dump({'ran': datetime.datetime.now().isoformat(timespec='seconds'),
                   'records': len(lp), 'newest_filing': newest},
                  open(os.path.join(HERE, 'lp_meta.json'), 'w', encoding='utf-8'), indent=1)
        print(f'\nCHAIN DONE: {len(lp)} LP records, newest filing {newest}')
    except Exception as e:
        print(f'meta stamp skipped: {e}')


if __name__ == '__main__':
    main()
