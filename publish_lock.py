"""One publishing runner at a time, per machine.

WHY THIS EXISTS

Five .bat files in this repo rebuild `docs/index.html` + `docs/call` and push them:
refresh-dealflow.bat, run-leads.bat, run-phones-nightly.bat, run-replies-daily.bat and
run-phones.bat. Not one of them took a lock, held a mutex, wrote a pidfile or checked whether
another was already mid-run. The only thing keeping two of them apart was the clock on the
Task Scheduler triggers, and the clock is not a mechanism - it is a coincidence that has to be
re-established by hand every time a trigger moves or a run overruns.

It has already cost a board. On 2026-09-15 at 19:11 run-replies-daily.bat published a
2,266-lead / 709-phone board over a live 2,297 / 1,148, and ONE MINUTE LATER run-phones-nightly.bat
published 714 phones over the same board. The gates would have refused both; neither was asking
them at the time. The expensive part was not the one bad board: the bad publish became
origin/main, so it moved the baseline every later publish_guard run compared against.

The 2026-09-22 shape of the same hazard: DEALFLOW Refresh fires 05:30 and the measured chain runs
3h08m, so it is still rebuilding at 06:45 when DealFlow Replies fires - and run-replies-daily.bat
rebuilds and pushes the board too. Moving triggers apart (Phones 06:00 -> 09:30 on 2026-09-22) is
the workaround. This file is the fix.

WHAT IT GUARDS, AND WHAT IT DOES NOT

It is a LOCAL lock: one file on one machine's disk. It stops two runners on THIS box from
rebuilding and pushing at once. It says nothing about the other machine - the laptop and
DESKTOP-35NNMFL each keep their own lock, and two machines publishing at once is a different
problem, held by repo_guard.bat, publish_guard.py and MACHINE-HANDOFF's "only one machine is
armed" rule. Do not read a green lock as "nobody else is publishing".

STALE LOCKS. A runner killed mid-flight (the 2026-08-31 StopOnIdleEnd terminations, a reboot, a
2h scheduler kill) leaves its lock behind, and a lock nothing can break is a lock that stops all
publishing until a human notices. So a lock older than STALE_AFTER is broken and reported. The
budget is SIX HOURS because that is the longest runner budget in the project: DEALFLOW Refresh
carries ExecutionTimeLimit PT6H (see _refreshexittest.py), and its measured chain is 3h08m. A
runner still holding the lock at six hours is not working, it is stuck.

FAIL DIRECTION: CLOSED. If the lock cannot be created, read or broken, acquire refuses. The
alternative is a guard that disappears exactly when the disk or the permissions are wrong, and
this repo has already paid for a guard that failed the expensive way round (repo_guard.bat's
`find` note). A refusal costs one run; the live site stays on its last good build, which
CLAUDE.md names as correct behaviour.

EXIT CODES
    acquire   0 = the lock is yours, proceed
              9 = DID NOT ACQUIRE. Either another runner holds a live lock, or the lock could
                  not be written/read at all. The caller must NOT build or push. 9 is the
                  runner-level "another publishing runner is mid-run" code in all five .bat
                  files; it collides with nothing they already use (0-7).
    release   0 always, by construction. Releasing is the last thing a run does and it must
              never be the thing that turns a good run into a failed one. It only ever removes
              a lock THIS runner owns; a mismatch is logged and left alone.
    status    0 always. Prints the holder, for `python publish_lock.py status` by hand.

KNOWN EDGE CASE, stated rather than hidden: ownership is matched on the runner's own filename.
Two DIFFERENT runners can never release each other's lock. Two concurrent runs of the SAME
runner, where the first has gone stale and had its lock broken by the second, would let the
first one's release remove the second one's lock. Task Scheduler's MultipleInstancesPolicy is
IgnoreNew on these tasks, and the manual twin of a scheduled runner has a different filename
(run-phones.bat vs run-phones-nightly.bat), so reaching it needs a hand-started duplicate of one
file more than six hours after its twin. It is a real hole and it is narrower than the one this
file closes.
"""
import errno
import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, '.publish.lock')

# Six hours: the longest ExecutionTimeLimit any DEALFLOW task carries. See the header.
STALE_AFTER = 6 * 60 * 60

# The five runners that rebuild docs/ and push. _batsyntaxtest.py rediscovers this list from the
# .bat files themselves rather than trusting it - a name here that stops publishing, or a publisher
# missing from here, is exactly the kind of drift CLAUDE.md's publish table took a month to notice.
PUBLISHERS = (
    'refresh-dealflow.bat',
    'run-leads.bat',
    'run-phones-nightly.bat',
    'run-replies-daily.bat',
    'run-phones.bat',
)


def _ago(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return '%dh%02dm' % (h, m)
    if m:
        return '%dm%02ds' % (m, s)
    return '%ds' % s


def _say(msg):
    # stdout, because every caller redirects this into its own run log (leads-run.log,
    # phones-run.log, replies-run.log). run-phones.bat is interactive and does not redirect,
    # so the same lines land on its console. One format for both.
    sys.stdout.write(msg + '\n')
    sys.stdout.flush()


def _read_holder():
    """-> (dict|None, age_seconds|None, problem|None).

    A lock whose contents are unreadable is NOT treated as absent. It is a lock, and the only
    thing not knowable about it is who owns it, so it is aged off its mtime and otherwise
    honoured. 'The file is corrupt so I will ignore it' is how a guard turns into a no-op.
    """
    try:
        with open(LOCK, encoding='utf-8') as fh:
            raw = fh.read()
    except IOError as exc:
        if exc.errno == errno.ENOENT:
            return None, None, None
        return None, None, 'cannot read %s - %s' % (LOCK, exc)
    try:
        held = json.loads(raw)
        started = float(held.get('started_epoch'))
    except Exception:
        try:
            started = os.path.getmtime(LOCK)
        except OSError as exc:
            return None, None, 'lock exists but cannot be aged - %s' % exc
        held = {'runner': '<unreadable lock file>', 'pid': '?', 'host': '?',
                'started_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started))}
    return held, time.time() - started, None


def _describe(held, age):
    return 'held by %s  pid %s  on %s  since %s  age %s' % (
        held.get('runner', '?'), held.get('pid', '?'), held.get('host', '?'),
        held.get('started_at', '?'), _ago(age))


def _write_lock(runner):
    """Atomic create-or-fail. Returns True when this process made the file."""
    payload = json.dumps({
        'runner': runner,
        'pid': os.getpid(),
        'host': socket.gethostname(),
        'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'started_epoch': time.time(),
    }, indent=2)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return False
        raise
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write(payload)
    return True


def _break_stale(held, age):
    """Rename the stale lock out of the way, then delete it.

    The rename is the interlock: os.rename to a name that does not exist fails on Windows if the
    destination is taken, so of two runners both finding the same stale lock at the same instant,
    exactly one wins the rename and goes on to create a fresh lock. The loser's rename fails, it
    finds no lock to break, and its own O_EXCL create then fails against the winner's. Deleting
    the file directly has no such interlock - both would delete and both would acquire.
    """
    doomed = '%s.stale.%d.%d' % (LOCK, os.getpid(), int(time.time() * 1000))
    try:
        os.rename(LOCK, doomed)
    except OSError as exc:
        _say('     .. PUBLISH LOCK: another runner is breaking the same stale lock - %s' % exc)
        return False
    _say('     !! PUBLISH LOCK: BROKE A STALE LOCK - %s' % _describe(held, age))
    _say('     !! Over the %s budget, so the run that took it is dead, not working.'
         % _ago(STALE_AFTER))
    _say('     !! If this line appears every morning, a runner is dying mid-flight - find that,')
    _say('     !! do not raise the budget.')
    try:
        os.remove(doomed)
    except OSError:
        pass  # the lock is already out of the way; a leftover .stale file is litter, not a fault
    return True


def acquire(runner):
    for attempt in (1, 2):
        try:
            if _write_lock(runner):
                _say('     publish lock taken by %s  pid %d  on %s.'
                     % (runner, os.getpid(), socket.gethostname()))
                return 0
        except OSError as exc:
            _say('     !! PUBLISH LOCK: cannot create %s - %s' % (LOCK, exc))
            _say('     !! REFUSING to publish. A lock that cannot be written is not a lock.')
            return 9

        held, age, problem = _read_holder()
        if problem:
            _say('     !! PUBLISH LOCK: %s' % problem)
            _say('     !! REFUSING to publish - cannot tell whether another runner is mid-run.')
            return 9
        if held is None:
            # It vanished between the create and the read: the holder released in that gap.
            # One retry, then give up rather than spin.
            continue
        if age is not None and age > STALE_AFTER:
            if attempt == 1 and _break_stale(held, age):
                continue
            return _refuse(held, age, runner)
        return _refuse(held, age, runner)
    _say('     !! PUBLISH LOCK: the lock changed hands twice while starting - refusing.')
    return 9


def _refuse(held, age, runner):
    _say('     !! PUBLISH LOCK: another publishing runner is mid-run on this machine.')
    _say('     !! %s' % _describe(held, age))
    _say('     !! %s REFUSING to start. Nothing built, nothing pushed, live site untouched.' % runner)
    _say('     !! This is the guard working. Two runners rebuilding docs/ at once is how a')
    _say('     !! poorer board reached the live site on 2026-09-15 and moved every later baseline.')
    return 9


def release(runner):
    held, age, problem = _read_holder()
    if problem:
        _say('     !! PUBLISH LOCK: %s - leaving it alone.' % problem)
        return 0
    if held is None:
        _say('     note: publish lock was already gone at release - nothing to do.')
        return 0
    if held.get('runner') != runner:
        _say('     !! PUBLISH LOCK: %s will not release a lock it does not own - %s'
             % (runner, _describe(held, age)))
        return 0
    try:
        os.remove(LOCK)
    except OSError as exc:
        _say('     !! PUBLISH LOCK: could not remove %s - %s' % (LOCK, exc))
        _say('     !! The next runner will refuse until this file is deleted, or until it ages')
        _say('     !! past %s and is broken as stale.' % _ago(STALE_AFTER))
        return 0
    _say('     publish lock released by %s after %s.' % (runner, _ago(age)))
    return 0


def status():
    held, age, problem = _read_holder()
    if problem:
        _say('publish lock: %s' % problem)
    elif held is None:
        _say('publish lock: free - no runner is publishing on %s.' % socket.gethostname())
    else:
        _say('publish lock: %s' % _describe(held, age))
        if age > STALE_AFTER:
            _say('              STALE - past the %s budget. The next runner will break it.'
                 % _ago(STALE_AFTER))
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == 'status':
        return status()
    if len(argv) != 3 or argv[1] not in ('acquire', 'release'):
        sys.stderr.write('usage: publish_lock.py acquire|release <runner.bat>\n'
                         '       publish_lock.py status\n')
        # Usage errors fail CLOSED for acquire's sake: a caller that mistypes the verb must not
        # sail past the guard. 9 is the same "did not acquire" the runners already handle.
        return 9
    return acquire(argv[2]) if argv[1] == 'acquire' else release(argv[2])


if __name__ == '__main__':
    sys.exit(main(sys.argv))
