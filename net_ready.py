"""net_ready — wait for the network before a run that cannot survive without it.

WHY THIS EXISTS — 2026-09-18, and it cost four days of leads
The 5:30 refresh fired on time on 09-14, 09-16 and 09-17 and died thirty seconds later, every
time, because the laptop had no DNS at that hour:

    fatal: unable to access 'https://github.com/...': Could not resolve host: github.com
    playwright._impl._errors.Error: Page.goto: net::ERR_NAME_NOT_RESOLVED
        at https://miamidade.realforeclose.com/index.cfm?...
    [FAIL] upstream sources  4 sources unreachable at once ... systemic, not a blip

`foreclosure_leads.py` exited non-zero, `refresh-dealflow.bat` printed "scrape failed or too few
leads" and jumped to its tail. Nothing was scraped, nothing was published, and `leads_final.json`
went 94 hours stale while the later jobs kept rebuilding and publishing a board from it. The task's
own exit code was 0.

The same fault hit `replies.py` at 06:45 the same morning (`[Errno 11001] getaddrinfo failed`) and
was fixed there with a bounded retry. This is that fix for the scrape: one systemic morning-network
problem, two casualties 75 minutes apart, and only one of them had a guard.

WHAT THIS IS NOT
It does not fix the network. A laptop that resolves nothing at 05:30 has a machine-level cause —
sleep, Wi-Fi power management, a VPN client that has not come up yet — and a retry loop only buys
time for it to settle. If every attempt here fails, the honest outcome is a run that refuses and
says so loudly, not a run that reports success having done nothing.

THE RULE
Wait, briefly and with a ceiling, then decide. REQUIRED hosts gate the run: without them the work is
impossible, so the caller must not proceed. ADVISORY hosts are reported and never block — the
healthcheck already grades upstream coverage, and duplicating a blocking rule in two files means
fixing one and believing you fixed both (same reasoning as publish_guard vs healthcheck on bkstay).

Usage:  python net_ready.py               # default wait, required hosts gate
        python net_ready.py --quick       # one pass, no waiting (for a manual run)
        python net_ready.py --timeout 600 # raise the ceiling
Exit:   0 = every required host resolves and answers on 443
        3 = still unreachable after the last attempt; the caller must NOT proceed
"""
import argparse
import socket
import sys
import time

# The two the run genuinely cannot proceed without: git (pull the code, push the board) and the
# auction calendar itself (no calendar, no leads, and scrape_guard would refuse a thin write anyway).
REQUIRED = [
    ('github.com', 443),
    ('www.miamidade.realforeclose.com', 443),
]

# Named in healthcheck's upstream-sources block. Enrichment degrades without them; the board still
# builds, so they are reported here and graded there.
ADVISORY = [
    ('gisweb.miamidade.gov', 443),
    ('apps.miamidadepa.gov', 443),
    ('www2.miamidadeclerk.gov', 443),
]

# Back off rather than hammer: a box whose Wi-Fi is still associating recovers in seconds, one whose
# adapter is asleep takes longer, and one that is genuinely offline is not going to be talked round.
# ~4 minutes of patience total, against a 6-hour task budget.
BACKOFF = (0, 5, 15, 30, 60, 120)


def probe(host, port, timeout=8):
    """-> (ok, detail). Never raises. Resolution and connection are reported separately because
    they fail for different reasons and the distinction is the whole finding here: DNS failing
    while the link is up looks nothing like a blocked or throttled site."""
    try:
        socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return False, f'DNS did not resolve ({e.__class__.__name__}: {str(e)[:60]})'
    except OSError as e:
        return False, f'DNS lookup failed ({str(e)[:60]})'
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, 'ok'
    except OSError as e:
        return False, f'resolved, but no answer on {port} ({str(e)[:60]})'


def check(hosts, timeout=8):
    """-> (all_ok, [(host, ok, detail), ...])."""
    rows = [(h, *probe(h, p, timeout)) for h, p in hosts]
    return all(ok for _, ok, _ in rows), rows


def wait_for_network(quick=False, ceiling=None):
    """-> (ok, rows_required, rows_advisory). Retries only the REQUIRED set; advisory hosts are
    probed once at the end so a slow appraiser server never adds minutes to every run."""
    schedule = (0,) if quick else BACKOFF
    started = time.time()
    rows = []
    for attempt, pause in enumerate(schedule, 1):
        if pause:
            if ceiling is not None and (time.time() - started) + pause > ceiling:
                break
            print(f'  network not ready — waiting {pause}s before attempt {attempt}')
            time.sleep(pause)
        ok, rows = check(REQUIRED)
        if ok:
            waited = int(time.time() - started)
            print(f'net-ready: OK after {waited}s'
                  + (f' ({attempt} attempts)' if attempt > 1 else ''))
            return True, rows, check(ADVISORY)[1]
        for host, host_ok, detail in rows:
            if not host_ok:
                print(f'  {host}: {detail}')
    return False, rows, check(ADVISORY)[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--quick', action='store_true', help='one pass, no waiting')
    ap.add_argument('--timeout', type=int, default=None,
                    help='seconds of patience overall (default: the full backoff, ~4 min)')
    a = ap.parse_args()

    ok, req, adv = wait_for_network(quick=a.quick, ceiling=a.timeout)
    down_adv = [h for h, host_ok, _ in adv if not host_ok]
    if down_adv:
        # Not a gate. Say it anyway: an advisory host down alongside the required ones up is a very
        # different morning from everything down, and the log should let you tell them apart.
        print(f'net-ready: advisory sources unreachable: {", ".join(down_adv)}')
    if ok:
        return 0

    print()
    print('net-ready: REFUSING to start — the network is not up.')
    for host, host_ok, detail in req:
        print(f'  {"ok  " if host_ok else "DOWN"}  {host}: {detail}')
    print()
    print('  This is the 2026-09-14/16/17 failure: the task fires at 05:30, the box cannot resolve')
    print('  anything, the scrape dies in seconds and the board is republished from a stale file.')
    print('  Refusing here means the run says so instead of reporting a successful morning.')
    print('  If this repeats at the same time every day the cause is on this machine, not upstream:')
    print('  check sleep/hibernate, the Wi-Fi adapter\'s "allow the computer to turn off this')
    print('  device to save power" setting, and whether the task is set to wake the box.')
    return 3


if __name__ == '__main__':
    sys.exit(main())
