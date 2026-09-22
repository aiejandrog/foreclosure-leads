#!/usr/bin/env python
"""lp_refresh.py — THE one way to run the lis pendens chain, in the only order that works.

WHY THIS EXISTS (2026-08-10, learned the expensive way): lp_resolve.main() merges its output
with whole-row replacement, which strips everything lp_values wrote; running lp_leads.py after
that puts value=0 / dor_desc='' / hs=False on the board and silently kills equity ranking for
every LP lead. The order is load-bearing and nothing enforced it — so now this does:

    lis_pendens  ->  lp_resolve  ->  lp_resolve2  ->  broward_resolve  ->  broward_pin  ->  lp_values
                 ->  lp_status  ->  lp_leads

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


DEGRADED = []      # steps that returned a benign-but-not-clean code; reported at the end


def run(label, args_, ok=(0,)):
    """Run one chain step. `ok` lists exit codes that are NOT failures — the LP phone step exits 5
    when the shared daily spend cap is used up, which is the budget working as designed, not a
    broken chain: the board must still rebuild with the leads it already has.

    "Benign" is not the same as "clean" (audit 2026-09-21, defect 7). A benign code used to print
    one line and vanish, so a night where the sweep only reached one county in three ended with
    the same exit 0 as a night where all three swept. Benign non-zero codes are collected and
    surfaced in this script's own exit code, so the batch above can report a degraded refresh."""
    print(f'\n===== {label} =====', flush=True)
    r = subprocess.run([sys.executable, '-u'] + args_, cwd=HERE)
    if r.returncode in ok:
        if r.returncode:
            DEGRADED.append(f'{label} (exit {r.returncode})')
            print(f'({label} exited {r.returncode} — benign, chain continues)', flush=True)
        return
    if r.returncode != 0:
        print(f'\nCHAIN STOPPED at {label} (exit {r.returncode}) — nothing after it ran, '
              f'the board was NOT touched.', file=sys.stderr)
        sys.exit(r.returncode)


def _newest_filing(rows):
    """Newest filing date across LP rows, as an ISO string ('' when none parse).

    This was `max(x['date'] for x in lp)` over raw M/D/YYYY TEXT (audit 2026-09-21, defect 12).
    Lexicographic order on that format is not chronological order: '9/9/2026' > '9/18/2026'
    because '9' > '1' at the second character, and every October date loses to every September
    one because '1' < '9'. So the stamp reported the freshest filing as older than it was, and
    got worse the closer the file came to a month boundary.

    Nothing currently reads lp_meta.json -- healthcheck.py parses lis_pendens.json itself and is
    correct -- so this fixed nothing downstream today. It is fixed because the stamp exists to be
    read, and the next reader would have inherited a wrong number with no reason to doubt it.
    The stored value is ISO so the next reader CAN compare it as text.
    """
    best = None
    for x in rows or []:
        try:
            m, d, y = str((x or {}).get('date') or '').split('/')
            got = datetime.date(int(y), int(m), int(d))
        except Exception:
            continue
        if best is None or got > best:
            best = got
    return best.isoformat() if best else ''


def _trace_provider_ready():
    """The provider skiptrace WOULD pick, if its key is loadable. '' when the step should be skipped.

    ASKS SKIPTRACE, DOES NOT RE-DERIVE (2026-09-22, Greptile P1 on the first version of this
    function). The first version replicated the rule — env var, then a non-empty key file, tracerfy
    then batchdata — and that is how this whole class of bug is made: two functions answering the
    same question by different rules. skiptrace has TWO rules, not one, and they disagree:

      pick_provider()  chooses on os.path.exists(keyfile)  -- EXISTENCE only
      load_key()       requires the file to be NON-EMPTY

    So an empty or unreadable `tracerfy.key` beside a good `batchdata.key` made the replica answer
    "yes, batchdata" while skiptrace picked tracerfy on the bare existence of the empty file, failed
    to load it, and exited 1 — fatal, CHAIN STOPPED, which is the exact outcome this preflight was
    added to prevent. An empty key file is not exotic: a truncated write or a half-finished key
    rotation produces one.

    Importing skiptrace costs 0.16s and has no import-time side effects (stdlib + requests +
    bd_budget), which is cheaper than being wrong.

    NO FAILOVER, ON PURPOSE. This returns skiptrace's own choice and does not go looking for some
    other provider with a working key. BatchData was exited on 2026-08-11 (BATCHDATA-EXIT.md) and
    costs $0.15/hit against Tracerfy's $0.10; refresh-dealflow.bat's [3b/5] gate was narrowed to
    `if exist tracerfy.key` precisely because a Tracerfy key problem used to fail over to the
    provider we left. A preflight that passed `--provider batchdata` here would rebuild that bug.
    """
    try:
        import skiptrace
        provider = skiptrace.pick_provider()
        return provider if skiptrace.load_key(provider) else ''
    except Exception as e:                       # a broken import must not take the chain with it
        print(f'(skip-trace preflight failed, treating as no key: {e})')
        return ''


def _stamp():
    """Write lp_meta.json — the as-of record for the LIS PENDENS data on disk.

    CALLED THE MOMENT THE DATA IS FINAL, which is right after lp_leads.py, NOT at the end of the
    script (moved 2026-09-22). It used to sit below the PHONES step, so the stamp recorded "the
    whole chain reached the bottom" rather than "the filings on disk are this fresh" — and those
    are different facts the moment any later step can fail.

    It did fail: on 2026-09-21 the fast-lane trace hit skiptrace.py's exit 2 (provider rejected the
    call — balance dry or key expired), the chain stopped there, and the stamp never got written.
    lp_meta.json therefore still read `ran: 2026-09-18 / newest_filing: 9/9/2026` on a night that
    had just swept 46 paid solves and folded in 408 Miami-Dade filings never pulled before. Two
    readers in a row — the operator and a project session — took that stamp at face value and went
    looking for a dead sweeper, a missing captcha.key and an empty 2Captcha balance. All three were
    fine. A phone vendor's outage must not be able to make the filings look stale.
    """
    try:
        lp = json.load(open(os.path.join(HERE, 'lis_pendens.json'), encoding='utf-8'))
        newest = _newest_filing(lp)
        json.dump({'ran': datetime.datetime.now().isoformat(timespec='seconds'),
                   'records': len(lp), 'newest_filing': newest},
                  open(os.path.join(HERE, 'lp_meta.json'), 'w', encoding='utf-8'), indent=1)
        print(f'\nLP DATA STAMPED: {len(lp)} LP records, newest filing {newest or "unknown"}')
    except Exception as e:
        print(f'meta stamp skipped: {e}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=45)
    ap.add_argument('--no-sweep', action='store_true', help='skip the captcha-billing sweep')
    ap.add_argument('--rebuild', action='store_true', help='make_tracker at the end')
    a = ap.parse_args()

    if not a.no_sweep:
        # 4 = PARTIAL sweep: at least one county ran, at least one was blocked (lis_pendens.py's
        # EXIT_PARTIAL). Benign for the CHAIN -- the counties that did sweep are worth resolving
        # and the board should still rebuild -- but the run is not clean, so it is carried to our
        # own exit code at the end rather than swallowed here. 3 = nothing got through anywhere,
        # which is NOT benign and stops the chain on the spot.
        run('SWEEP (lis_pendens, all 3 counties)',
            ['lis_pendens.py', '--days', str(a.days), '--county', 'all'], ok=(0, 4))
    run('RESOLVE (lp_resolve)', ['lp_resolve.py'])
    run('RESOLVE PASS 2 (lp_resolve2)', ['lp_resolve2.py'])
    # Broward rows carry no legal description, so the MD ladder above skips them — the BCPA
    # name ladder is their only path to an address. Runs before lp_values on purpose: it writes
    # its own value/homestead from the cadastral, and lp_values' MD cache would skip them anyway.
    run('RESOLVE BROWARD (BCPA name ladder)', [os.path.join('fl_lp', 'broward_resolve.py')])
    # The name ladder (and lp_resolve2's revocations) leave a defendant who owns several parcels at `low`.
    # The mortgage the lis pendens forecloses prints the parcel: OCR its PIN and promote only on an exact
    # folio match. Bounded (40 rows / 15 min, cached per case) and optional — it exits 2 when AcclaimWeb or
    # Windows OCR is unavailable, which must not stop the chain. Before lp_values so a promoted row is priced.
    run('RESOLVE BROWARD PARCEL (mortgage PIN)', [os.path.join('fl_lp', 'broward_pin.py')], ok=(0, 2))
    run('VALUE (lp_values)', ['lp_values.py'])
    run('CASE STATUS (lp_status)', ['lp_status.py'])
    run('BOARD ROWS (lp_leads)', ['lp_leads.py'])
    # THE LIS PENDENS DATA IS NOW FINAL. Everything below this line is phones and an optional
    # rebuild; neither changes a filing. Stamp here so the as-of date survives whatever they do.
    _stamp()
    # FAST-LANE PHONES. A fresh filing with no phone is a lead you cannot be first to — the worker's
    # EARLY lane ("be the first call") has nothing to dial and the funnel dumps it into 'trace'.
    # Runs AFTER lp_leads so it only sees rows that actually resolved to a high-confidence address,
    # and it is bounded twice: --limit here and bd_budget's shared daily dollar cap inside skiptrace
    # (a spent budget exits 5, which run() treats as benign). ~$0.10/hit on Tracerfy.
    #
    # A VENDOR OUTAGE HERE IS BENIGN, and it took a real night to see why (2026-09-22). This is the
    # LAST step: by the time it runs, the sweep, both resolve passes, values, status and the board
    # rows have all already succeeded. So "stop the chain" buys nothing — there is nothing after it
    # to protect — while costing the run its verdict and (until the stamp moved above) its freshness
    # record. `ok` listed only 5, the shared daily budget cap, so every other vendor outcome killed
    # the chain instead: 2 = the provider rejected the call, balance dry or key expired (skiptrace's
    # TraceAborted — the one that actually fired on 09-21); 3 = the provider looks down, aborted
    # after MAX_STRIKES consecutive failures (a Tracerfy DNS failure did this the same night);
    # 4 = the run would breach --max-spend so nothing was traced. All four mean one thing to this
    # chain: the fresh filings shipped without phones. Worth reporting, not worth withholding the
    # board for — so they land in DEGRADED and are carried out in our own exit 4.
    #
    # 1 IS DELIBERATELY NOT IN THAT LIST. skiptrace exits 1 both for an unloadable API key
    # (skiptrace.py:528) and for any uncaught exception, so treating 1 as benign would swallow a
    # genuine crash. The key half is a PRECONDITION, not a failure, so it is answered by skipping
    # the step outright — the same shape as refresh-dealflow.bat's own `if exist tracerfy.key`
    # guard at [3b/5], which this chain never had. _trace_provider_ready() asks skiptrace which
    # provider it would pick and whether that key loads, so the preflight cannot disagree with the
    # process it is gating.
    _provider = _trace_provider_ready()
    if _provider:
        run('PHONES (LP fast lane)', ['skiptrace.py', '--lp-fresh', '45', '--limit', '25'],
            ok=(0, 2, 3, 4, 5))
    else:
        DEGRADED.append('PHONES (LP fast lane) (no loadable skip-trace key on this machine)')
        print('\n===== PHONES (LP fast lane) =====\n(skipped: the provider skiptrace would pick '
              'has no loadable key — fresh filings ship without phones)', flush=True)
    if a.rebuild:
        run('REBUILD (make_tracker)', ['-c',
            "import json, foreclosure_leads as F; "
            "F.make_tracker(json.load(open('leads_final.json', encoding='utf-8')))"])

    print('\nCHAIN DONE.')
    if DEGRADED:
        print('\nDEGRADED RUN — the chain finished, but not everything ran:')
        for d in DEGRADED:
            print(f'  - {d}')
        sys.exit(4)


if __name__ == '__main__':
    main()
