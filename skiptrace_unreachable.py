"""skiptrace_unreachable.py -- target the contactability ceiling; TRIGGER HELD BY DEFAULT.

WHY THIS EXISTS
Every session someone says "we have 126 leads with no phone or email, let's skiptrace them."
Doing that directly is wrong for two reasons that only surface when you look at the data:

    1. The 126 collapses hard. Most of them have no NAME or no ADDRESS on the raw row (94/126
       on tonight's board), which means the vendor cannot key on anything -- Tracerfy and
       BatchData both need at least those two.
    2. Most of the rest already failed. ~31/32 of the attemptable ones already got a Tracerfy
       call that returned NOTHING. Re-running Tracerfy on them is a $0.02 credit each with the
       same result. The real move is a cross-provider retry (BatchData), because different
       vendors buy different data streams and one's dead lead is sometimes the other's hit.

So this script segments the 126 into three buckets, prints the dollar cost of each action, and
requires an EXPLICIT --go BUCKET flag to fire anything paid. --go is off by default. There is
also no combined --go=all: choosing a bucket forces a per-segment cost decision.

SPEND AUTHORIZATION IS ALEJANDRO'S ALONE. This tool computes and displays; it does not decide.
The rule that put it here: verify_emails once burned ~$683/mo because a "cap" was a REPORT
rather than a bind. Every paid call in this file is gated by --go and by the underlying
skiptrace.py's own --limit; unset either and nothing spends.

BUCKETS:
    fresh    Never traced at all. Cheapest, safest first spend. Provider: whichever is set up.
    retry    Tracerfy said no data. Only actionable when BatchData is also configured.
    blocked  Missing name or address -- no vendor can key on them. Needs upstream enrichment
             (docket, appraiser) before skiptrace can help; report so it is visible.

Usage:
    python skiptrace_unreachable.py                     # DRY REPORT — projected cost, no spend
    python skiptrace_unreachable.py --go fresh          # trace the "never tried" bucket
    python skiptrace_unreachable.py --go retry --limit 10  # cross-provider retry, capped
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Advertised per-hit costs, per skiptrace.py's own header. Vendor pricing changes; if the number
# in the report ever looks wrong, check skiptrace.py first -- there is no third source of truth.
COST = {'tracerfy': 0.10, 'batchdata': 0.15}


def _s(v):
    return '' if v is None else str(v)


def _has_name(r):
    return any(_s(r.get(k)).strip() for k in ('owners', 'owner_clean', 'defendants',
                                              'people_name', 'oname', 'rname'))


def _has_addr(r):
    return any(_s(r.get(k)).strip() for k in ('Address', 'address', 'addr', 'mailing_address'))


def _case(r):
    return _s(r.get('Case #') or r.get('case')).strip()


def _who(r):
    for k in ('owner_clean', 'owners', 'oname', 'people_name'):
        v = _s(r.get(k)).strip()
        if v:
            return v[:38]
    return '(no name on row)'


def _addr(r):
    for k in ('Address', 'address', 'mailing_address'):
        v = _s(r.get(k)).strip()
        if v:
            return v[:44]
    return '(no addr on row)'


def key_present(name):
    return os.path.exists(os.path.join(HERE, name))


def bucketize():
    L = json.load(open(os.path.join(HERE, 'leads_final.json'), encoding='utf-8'))
    try:
        S = json.load(open(os.path.join(HERE, 'skiptrace_results.json'), encoding='utf-8'))
    except Exception:
        S = {}

    fresh, retry, blocked = [], [], []
    for r in L:
        c = _case(r)
        ent = S.get(c) or {}
        # Unreachable = the shipped board has no phones AND no emails after every enrichment ran.
        reach = bool(ent.get('phones') or ent.get('emails'))
        if reach:
            continue
        if not (_has_name(r) and _has_addr(r) and c):
            blocked.append(r)
        elif c in S:
            retry.append(r)              # tried, returned nothing — cross-provider is the option
        else:
            fresh.append(r)
    return fresh, retry, blocked


def _run_skiptrace(cases, provider):
    """Invoke skiptrace.py once per case. Failure of one call NEVER short-circuits — the caller
    already authorized the whole batch, and a mid-batch abort would leave the cache inconsistent
    with the money already spent."""
    n_ok = n_fail = 0
    for c in cases:
        args = [sys.executable, 'skiptrace.py', '--case', c]
        if provider:
            args += ['--provider', provider]
        r = subprocess.run(args, cwd=HERE)
        if r.returncode == 0:
            n_ok += 1
        else:
            n_fail += 1
    return n_ok, n_fail


def _est(cases, per_hit):
    """Cost is per-CALL, not per-hit. A miss still returns 200 and Tracerfy charges nothing on
    a real miss -- but the "credit reserved" behavior is vendor-specific. Estimate the upper
    bound (call * price) so no run silently exceeds it."""
    return len(cases) * per_hit


def report(fresh, retry, blocked):
    have = {'tracerfy': key_present('tracerfy.key'), 'batchdata': key_present('batchdata.key')}
    W = 78
    print('=' * W)
    print('  SKIPTRACE THE UNREACHABLE — DRY REPORT (nothing spent)')
    print('=' * W)
    print('  providers configured : ' + ', '.join(k for k, v in have.items() if v) or '(none)')
    print()
    print('  BUCKETS')
    print('    fresh   %3d  never traced' % len(fresh))
    print('            upper-bound cost: $%.2f (Tracerfy) / $%.2f (BatchData)'
          % (_est(fresh, COST['tracerfy']), _est(fresh, COST['batchdata'])))
    print()
    print('    retry   %3d  Tracerfy returned nothing — CROSS-PROVIDER only actionable option'
          % len(retry))
    print('            upper-bound cost: $%.2f (BatchData) — requires batchdata.key'
          % _est(retry, COST['batchdata']))
    print()
    print('    blocked %3d  no name or no address on the row — no vendor can key on them.'
          % len(blocked))
    print('            Needs upstream enrichment (docket, appraiser) before skiptrace helps.')
    print()
    if fresh[:6] or retry[:3]:
        print('  SAMPLES')
        for r in fresh[:6]:
            print('    fresh   %-22s  %-30s  %s' % (_case(r)[:22], _who(r), _addr(r)))
        for r in retry[:3]:
            print('    retry   %-22s  %-30s  %s' % (_case(r)[:22], _who(r), _addr(r)))
        print()
    print('  TO FIRE THIS: pick a bucket, then run one of these — nothing happens without --go.')
    print('    python skiptrace_unreachable.py --go fresh')
    print('    python skiptrace_unreachable.py --go retry --limit 10')
    print('=' * W)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--go', default='', choices=['', 'fresh', 'retry'],
                    help='the ONLY flag that authorizes real spend; leave empty for a dry report')
    ap.add_argument('--limit', type=int, default=0,
                    help='cap the number of cases to trace on this run (default: no cap)')
    a = ap.parse_args()

    fresh, retry, blocked = bucketize()

    if not a.go:
        report(fresh, retry, blocked)
        return 0

    if a.go == 'fresh':
        target = fresh
        provider = ''                    # auto-detect via skiptrace.py
    else:  # retry
        target = retry
        provider = 'batchdata'
        if not key_present('batchdata.key'):
            print('!! REFUSING to run --go retry: batchdata.key is missing.')
            print('   Tracerfy already returned nothing on these leads; retrying Tracerfy would')
            print('   burn credits for no chance of a different result. Set up a BatchData key')
            print('   ($50 minimum deposit) then re-run.')
            return 2

    if a.limit and a.limit > 0:
        target = target[:a.limit]

    if not target:
        print('  Nothing to do — the "%s" bucket is empty.' % a.go)
        return 0

    per_hit = COST['batchdata' if provider == 'batchdata' else 'tracerfy']
    est = _est(target, per_hit)

    # A LAST HONEST NUMBER, printed EVERY RUN — never behind a --verbose flag. Someone reading
    # the console history should be able to see exactly how much this shell hit for.
    print('  ABOUT TO SPEND UP TO $%.2f on %d skiptrace call(s) via %s.'
          % (est, len(target), (provider or 'auto-detect')))
    print('  Ctrl-C to abort. Starting in 5 seconds...')
    try:
        import time
        time.sleep(5)
    except KeyboardInterrupt:
        print('\n  ABORTED by operator. Zero spent.')
        return 0

    ok, fail = _run_skiptrace([_case(r) for r in target], provider)
    print('  done. %d ok, %d failed. Rebuild the board so Call Mode picks them up:' % (ok, fail))
    print('    python -c "import json, foreclosure_leads as F; '
          'F.make_tracker(json.load(open(\'leads_final.json\',encoding=\'utf-8\')))"')
    return 0 if not fail else 1


if __name__ == '__main__':
    sys.exit(main())
