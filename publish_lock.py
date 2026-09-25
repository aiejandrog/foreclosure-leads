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

How it looked on 2026-09-22, the morning this file was written: DEALFLOW Refresh fires 05:30 and
its measured chain runs 3h08m, so it was still rebuilding at 06:45 when DealFlow Replies fired -
and run-replies-daily.bat rebuilds and pushes the board too. That morning the triggers were moved
apart, Replies 06:45 -> 08:45 and Phones 06:00 -> 09:30, and the INSTALLED times are now the ones
in CLAUDE.md's publish table, not the ones in this paragraph. Read them there; a time written into
a source comment is a time that goes stale the next time someone opens Task Scheduler. Moving the
triggers was the workaround, and it holds only while every run finishes inside its slot - Refresh
has measured 2h11m, 2h49m, 3h08m and about 4h. This file is the fix.

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
              9 = DID NOT ACQUIRE, for one of two reasons, and the printed lines say which:
                  another runner holds a live lock, or the lock is UNUSABLE - it could not be
                  created, read or aged. Both are fail-closed and the caller must NOT build or
                  push either way, but they are different events: Greptile's 2026-09-25 point was
                  that reporting an unwritable lock as contention sends whoever reads
                  leads-run.log hunting for a run that never existed. So rc=9 means "the lock was
                  not obtained", the five runners say that rather than naming a cause, and
                  _refuse / _refuse_unusable print the cause. 9 collides with nothing the runners
                  already use (0-7).
    release   0 always, by construction. Releasing is the last thing a run does and it must
              never be the thing that turns a good run into a failed one. It only ever removes
              a lock THIS runner owns; a mismatch is logged and left alone.
    status    0 always. Prints the holder, for `python publish_lock.py status` by hand.
    break     0 = there is no lock now, 9 = refused to touch a live one. For a HUMAN clearing a
              lock nothing will release - a runner whose cmd.exe died between acquire and release
              leaves the file behind and every publisher then refuses until the six-hour budget
              ages it out. It refuses a lock still inside its budget unless --force, and --force
              says what it costs. It is deliberately NOT wired into any runner: a publish path that
              can break its way past the lock does not have a lock, and _batsyntaxtest asserts that
              no .bat calls it.

OWNERSHIP is matched on the runner's filename AND on the ppid recorded in the lock - the cmd.exe
that ran the .bat, which is shared by that run's acquire and its release and differs between two
runs of the same file. Two DIFFERENT runners could never release each other's lock; the filename
alone was not enough for two runs of the SAME one, where the first overruns six hours, has its
lock broken by the second, and then releases the second's live lock on its way out. That was
shipped documented-not-closed and Greptile refused it on 2026-09-25; it is closed. What remains is
narrower by an order of magnitude: Windows reuses pids, so two runs of one runner more than six
hours apart could in principle draw the same cmd.exe pid, which needs a pid collision ON TOP OF
the overrun. A lock file written before this change carries no ppid and falls back to the filename
match rather than wedging.
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

# What a human does about a lock nothing will release. Written once, printed by every refusal that
# leaves a lock in place, because the alternative - a person working out for themselves that the
# fix is deleting a file - is how a guard gets deleted during a live run instead of after a dead
# one. `break` checks the age first; --force is the escape hatch and says what it costs.
_REMEDY = ('if no runner is really publishing, clear it with: '
           'python publish_lock.py break')

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


def _identity(raw):
    """The bytes-level fingerprint of one particular lock file.

    Used to prove that the file being renamed out of the way as stale is still the SAME file that
    was read and judged stale, and not a fresh lock written by a new holder in the gap between the
    two. Greptile flagged that gap on 2026-09-25 and it is real: rename is an interlock over WHO
    gets to break a lock, and says nothing about WHICH lock it moved.

    stat alone is not enough - mtime granularity is coarse enough on NTFS that a replacement
    written in the same tick can share it - so the raw payload is part of the fingerprint.
    """
    try:
        st = os.stat(LOCK)
    except OSError:
        return None
    return (raw, st.st_size, st.st_mtime_ns)


def _raw_lock():
    try:
        with open(LOCK, encoding='utf-8') as fh:
            return fh.read()
    except IOError:
        return None


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
        # The pid of the cmd.exe running the .bat, not of this python. Both the acquire and the
        # release in one runner are spawned by the same cmd.exe, so this is the only thing the two
        # separate python processes share that is unique to ONE RUN of that runner. It is what
        # closes the hole Greptile flagged on 2026-09-25: release used to match on the runner's
        # FILENAME, so where a stale run's lock had been broken by a second run of the same file,
        # the first one's release removed the second one's live lock.
        'ppid': os.getppid(),
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


def _break_stale(held, age, sig):
    """Rename the stale lock out of the way, then delete it - but only if it is still the same file.

    The rename is the interlock over WHO breaks: os.rename to a name that does not exist fails on
    Windows if the destination is taken, so of two runners both finding the same stale lock at the
    same instant, exactly one wins the rename and goes on to create a fresh lock. The loser's
    rename fails, it finds no lock to break, and its own O_EXCL create then fails against the
    winner's. Deleting the file directly has no such interlock - both would delete and both would
    acquire.

    What the rename does NOT do is prove which file it moved, and that is the race Greptile found
    on 2026-09-25: read an expired lock, have its owner release and a THIRD runner take a fresh
    one in the gap, and this rename carries the fresh lock away and clears the path for a second
    publisher. So the fingerprint taken at read time is re-checked twice - once immediately before
    the rename, and once on the renamed file, which is the only check that cannot be raced, because
    by then nothing else can touch it. If the renamed file turns out to be fresh, it is put back
    byte-for-byte and this runner refuses. Restoring is an O_EXCL create rather than a rename back,
    so it can never overwrite a lock somebody took in the meantime.
    """
    if sig is not None and _identity(_raw_lock()) != sig:
        _say('     .. PUBLISH LOCK: the lock changed while it was being judged stale - not breaking it.')
        return False
    doomed = '%s.stale.%d.%d' % (LOCK, os.getpid(), int(time.time() * 1000))
    try:
        os.rename(LOCK, doomed)
    except OSError as exc:
        _say('     .. PUBLISH LOCK: another runner is breaking the same stale lock - %s' % exc)
        return False
    if sig is not None:
        try:
            with open(doomed, encoding='utf-8') as fh:
                moved = fh.read()
        except IOError:
            moved = None
        if moved != sig[0]:
            # A fresh lock was written between the check above and the rename. Put it back.
            _say('     !! PUBLISH LOCK: the stale lock was replaced by a live one mid-break.')
            restored = False
            if moved is not None:
                try:
                    fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except OSError:
                    pass
                else:
                    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                        fh.write(moved)
                    restored = True
            _say('     !! Its holder is still publishing, so it was %s and this runner refuses.'
                 % ('put back' if restored else 'already replaced again'))
            try:
                os.remove(doomed)
            except OSError:
                pass
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
            return _refuse_unusable('cannot create %s - %s' % (LOCK, exc), runner)

        raw = _raw_lock()
        sig = _identity(raw)
        held, age, problem = _read_holder()
        if problem:
            return _refuse_unusable(problem, runner)
        if held is None:
            # It vanished between the create and the read: the holder released in that gap.
            # One retry, then give up rather than spin.
            continue
        if age is not None and age > STALE_AFTER:
            if attempt == 1 and _break_stale(held, age, sig):
                continue
            return _refuse(held, age, runner)
        return _refuse(held, age, runner)
    return _refuse_unusable('the lock changed hands twice while starting', runner)


def _refuse_unusable(problem, runner):
    """rc=9 for a lock that could not be created, read or aged - NOT for contention.

    Both refusals exit 9 and both are fail-closed, because a guard that disappears when the disk
    or the permissions are wrong is not a guard. But they are different events and Greptile was
    right that saying "another publishing runner is mid-run" for this one sends whoever reads
    leads-run.log looking for a run that was never there. rc=9 means the lock was NOT OBTAINED;
    these lines say which of the two reasons it was.
    """
    _say('     !! PUBLISH LOCK: %s' % problem)
    _say('     !! The lock is UNUSABLE - this is not another runner holding it.')
    _say('     !! %s REFUSING to start. Nothing built, nothing pushed, live site untouched.' % runner)
    _say('     !! Fix the file or the folder, then re-run. Do not delete the guard to get past it.')
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
    # SAME RUNNER, DIFFERENT RUN. Matching on the runner's filename alone was the one ownership
    # hole this file shipped documented rather than closed, and Greptile refused it on 2026-09-25:
    # if one run of a runner overruns six hours and a second run of the SAME file breaks its stale
    # lock and takes a fresh one, the first run's release would then delete the second run's live
    # lock and let a third publisher in. The recorded ppid is the cmd.exe that ran the .bat, so it
    # is the same for this runner's acquire and its release and different for a different run.
    #
    # Refusing here cannot lose a lock that should have been dropped: the worst case is a lock left
    # behind, which is exactly what a crashed run leaves, and the six-hour stale break is already
    # the backstop for that. An older lock file with no 'ppid' key falls back to the filename match
    # rather than wedging on a key that did not exist when it was written.
    #
    # Residual, smaller than what it replaces: Windows reuses pids, so two runs of one runner more
    # than six hours apart could in principle draw the same cmd.exe pid. That needs a pid collision
    # ON TOP OF the overrun this closes.
    own = held.get('ppid')
    if own is not None and own != os.getppid():
        _say('     !! PUBLISH LOCK: this lock belongs to a DIFFERENT run of %s - not releasing.' % runner)
        _say('     !! %s' % _describe(held, age))
        _say('     !! Its run started under process %s, this one under %s, so this is not the run'
             % (own, os.getppid()))
        _say('     !! that took the lock. Either a run that overran the %s budget had its lock'
             % _ago(STALE_AFTER))
        _say('     !! broken and the run that took it is still going, or this release is running')
        _say('     !! outside the run that acquired - a holder whose cmd.exe died without')
        _say('     !! releasing, or a release typed by hand. Leaving the lock alone either way:')
        _say('     !! %s' % _REMEDY)
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
        else:
            _say('              %s' % _REMEDY)
    return 0


def break_lock(force=False):
    """Clear a lock by hand. 0 = there is no lock now, 9 = refused to touch a live one.

    This exists because of what the guard costs when it is holding a lock nobody will release: a
    runner whose cmd.exe died between acquire and release leaves the file behind, and every
    publisher then refuses until the six-hour budget ages it out. That is the right default - the
    alternative is a guard that lets go whenever a process disappears, which is how a live 3h08m
    refresh gets its lock stolen at hour one - but "wait six hours or work out that you are meant
    to delete a file" is not an operational procedure. This is the procedure.

    It is NOT wired into any runner and must not be: a publish path that can break its way past the
    lock does not have a lock. `_batsyntaxtest` asserts that no .bat calls it.
    """
    held, age, problem = _read_holder()
    if held is None and not problem:
        _say('publish lock: already free on %s - nothing to break.' % socket.gethostname())
        return 0
    if held is not None and age is not None and age <= STALE_AFTER and not force:
        _say('     !! PUBLISH LOCK: refusing to break a lock that is still inside its budget.')
        _say('     !! %s' % _describe(held, age))
        _say('     !! It has %s of its %s left, so the run that took it may well be working.'
             % (_ago(STALE_AFTER - age), _ago(STALE_AFTER)))
        _say('     !! Check first - python publish_lock.py status, and look for that pid. If the')
        _say('     !! run is genuinely dead, break it with: python publish_lock.py break --force')
        return 9
    what = _describe(held, age) if held is not None else problem
    try:
        os.remove(LOCK)
    except OSError as exc:
        _say('     !! PUBLISH LOCK: could not remove %s - %s' % (LOCK, exc))
        return 9
    _say('     !! PUBLISH LOCK BROKEN BY HAND - %s' % what)
    if force and held is not None and age is not None and age <= STALE_AFTER:
        _say('     !! It was still inside its %s budget and --force took it anyway. If that run'
             % _ago(STALE_AFTER))
        _say('     !! was in fact alive, two runners can now rebuild and push at once.')
    _say('     publish lock: free on %s.' % socket.gethostname())
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == 'status':
        return status()
    if len(argv) >= 2 and argv[1] == 'break':
        return break_lock(force='--force' in argv[2:])
    if len(argv) != 3 or argv[1] not in ('acquire', 'release'):
        sys.stderr.write('usage: publish_lock.py acquire|release <runner.bat>\n'
                         '       publish_lock.py status\n'
                         '       publish_lock.py break [--force]\n')
        # Usage errors fail CLOSED for acquire's sake: a caller that mistypes the verb must not
        # sail past the guard. 9 is the same "did not acquire" the runners already handle.
        return 9
    return acquire(argv[2]) if argv[1] == 'acquire' else release(argv[2])


if __name__ == '__main__':
    sys.exit(main(sys.argv))
