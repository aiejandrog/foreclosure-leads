"""morning_sync.py - the 07:15 opt-out sync, before the 08:00 Morning Worker. Records whether it worked.

WHO WRITES THE SUPPRESSION LIST (decided 2026-09-26): Foreclosure Bot's opt-out sync pipeline, and
nothing else. That pipeline is exactly the three steps below, in this order:

    1. replies.py      scan the inbox; a reply that says stop is flagged in replies.json
    2. optout_sync.py  carry every flagged STOP into optouts.json through `ledger_add` (#70), the one
                       write path, add-only
    3. ledger_sync.py  add-only union of optouts.json / mail_sent.json with the other machine through
                       the private ledgers repo, so both PCs hold the same list

This file only RUNS those scripts; it does not change what any of them decides (they are the reserved
suppression surface in CLAUDE.md). _ledgerownertest.py is what keeps "nothing else writes it" true.

WHY 07:15 AND WHY A SEPARATE TASK. The reply scan and opt-out sync lived only inside
run-replies-daily.bat, which rebuilds and PUBLISHES the board - and which was moved to 08:45 on
2026-09-22 so it would not collide with the 05:30 refresh (3-4h) that also publishes. At 08:45 it
runs 45 minutes AFTER the 08:00 Morning Worker starts sending, so the worker mailed against a list
that did not yet include overnight STOPs. This job does the sync only - no rebuild, no publish, no
push to this repo - so it can run at 07:15 while the refresh is still working. The 08:45 bake stays
as it is; its second pass of replies.py + optout_sync.py is add-only and harmless.

WHAT IT RECORDS. sync_status.json (gitignored, next to optouts.json), written as "running" at the start
and "finished" with every step's result at the end. sync_gate.py reads it: unless today's run
finished with every step OK, send_server's /send and cadence-daily.bat HOLD every send.

What counts as a step succeeding:
  * replies.py      exit 0 AND replies.json rewritten during this run. replies.py exits 0 even when
                    the IMAP login fails or gmail.key is missing (it prints and returns), so its exit
                    code alone would pass a scan that never happened. A successful scan always
                    rewrites replies.json; a failed one returns before the write.
  * optout_sync.py  exit 0 (it raises on any failure)
  * ledger_sync.py  exit 0 (FAIL-LOUD by design: non-zero whenever it cannot prove local == repo)
Every step runs even when an earlier one failed - each only ADDS suppression - but one failure makes
the whole morning a failure, and the sends hold.

    python morning_sync.py        # exit 0 = all three OK, 1 = something failed (sends will hold)
"""
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS = os.path.join(HERE, 'sync_status.json')
REPLIES_JSON = os.path.join(HERE, 'replies.json')
WRITER = "Foreclosure Bot opt-out sync (replies.py -> optout_sync.ledger_add -> ledger_sync.py)"

# (name, script, timeout seconds, needs replies.json rewritten)
STEPS = (
    ('replies', 'replies.py', 25 * 60, True),
    ('optout_sync', 'optout_sync.py', 10 * 60, False),
    ('ledger_sync', 'ledger_sync.py', 10 * 60, False),
)


def _write(st):
    tmp = STATUS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATUS)


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def run_step(name, script, timeout, needs_replies):
    t0 = time.time()
    print('[sync] %s: python %s' % (name, script), flush=True)
    if not os.path.exists(os.path.join(HERE, script)):
        return {'name': name, 'rc': None, 'ok': False, 'secs': 0, 'why': '%s not found' % script}
    before = _mtime(REPLIES_JSON)
    try:
        p = subprocess.run([sys.executable, '-u', script], cwd=HERE, timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return {'name': name, 'rc': 'timeout', 'ok': False, 'secs': round(time.time() - t0),
                'why': 'killed after %dm' % (timeout // 60)}
    except OSError as e:
        return {'name': name, 'rc': None, 'ok': False, 'secs': round(time.time() - t0), 'why': str(e)[:120]}
    res = {'name': name, 'rc': rc, 'ok': rc == 0, 'secs': round(time.time() - t0)}
    if rc != 0:
        res['why'] = 'exit code %s' % rc
    elif needs_replies:
        after = _mtime(REPLIES_JSON)
        if after is None or (before is not None and after <= before) or after < t0 - 2:
            res['ok'] = False
            res['why'] = ('exited 0 but did not rewrite replies.json - the inbox was not scanned '
                          '(IMAP login/network failure, or gmail.key missing); see the log above')
    print('[sync] %s: %s%s' % (name, 'OK' if res['ok'] else 'FAILED', (' - ' + res['why']) if res.get('why') else ''),
          flush=True)
    return res


def main():
    now = time.time()
    st = {'date': dt.date.today().isoformat(), 'started_at': int(now), 'state': 'running', 'ok': False,
          'host': socket.gethostname(), 'writer': WRITER, 'steps': []}
    _write(st)                      # a run killed from here on leaves "running" -> sends hold
    steps = [run_step(*s) for s in STEPS]
    ok = all(s['ok'] for s in steps)
    st.update(state='finished', finished_at=int(time.time()), ok=ok, steps=steps)
    _write(st)
    if ok:
        print('[sync] opt-out sync OK - today\'s sends may proceed.', flush=True)
    else:
        print('[sync] !! opt-out sync FAILED (%s) - every send is HELD until a clean run today. '
              'Fix the step above and re-run run-optout-sync.bat.'
              % ', '.join(s['name'] for s in steps if not s['ok']), flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
