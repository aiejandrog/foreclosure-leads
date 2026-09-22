"""_publishwatchtest — guard for publish_watch.py. Runs on ANY checkout: no network, no board.

publish_watch.py is the thing that notices the live site stopped moving. A watchdog that is
itself wrong is worse than none, because it converts "nobody checked" into "something checked
and said it was fine" — so the cases below are the ones where a wrong answer costs a morning:

  1. the timezone of the build stamp. It is a naive Florida wall clock, and reading it with the
     READER's timezone put a UTC-container run four hours in the past. That error is invisible
     for most of the day and fires a false "did not publish" between 01:30 and 05:30 ET.
  2. a stale board must ALARM with exit 2, and a current one must exit 0.
  3. a board that SHRANK a little must not alarm. Leads leave when an auction sells; the
     mirror's own history goes 2,372 -> 2,368 -> 2,394 inside one day. An alarm on every small
     drop is an alarm nobody reads by week two.
  4. an unreadable mirror must exit 3 (CANNOT EVALUATE), never 0. This is engine_drift.py's
     fail-loud rule and the reason both files share exit codes.
  5. the mirror repo is DERIVED from board_url.BOARD_URL, never a second hardcoded copy of the
     address. Nine hand-edited copies is what broke the 09-17 move.

Nothing here opens a socket or runs git: sync_mirror, publish_commits and coverage_at are
replaced with scripted stand-ins.

Run:  python _publishwatchtest.py
"""
import datetime as dt
import io
import sys

import board_url
import publish_watch as pw

FAILS = []
CHECKS = [0]


def check(label, got, want):
    CHECKS[0] += 1
    ok = got == want
    print('%s %s' % ('  ok  ' if ok else '  FAIL', label))
    if not ok:
        print('         got  %r' % (got,))
        print('         want %r' % (want,))
        FAILS.append(label)
    return ok


def _stamp(built, leads=2400, phones=1200, sig='deadbeefcafe'):
    return {'leads': leads, 'phones': phones, 'built': built, 'sig': sig}


def _run(publishes, stamps, argv):
    """Drive main() with scripted mirror data. `publishes` is [(sha, commit_utc)]."""
    pw.ALARMS[:] = []
    pw.UNKNOWNS[:] = []
    pw.NOTES[:] = []
    real = (pw.sync_mirror, pw.publish_commits, pw.coverage_at, pw.engine_head, sys.argv)
    try:
        pw.sync_mirror = lambda url, cache: publishes is not None
        pw.publish_commits = lambda cache, limit: list(publishes or [])[:limit]
        def _cov(owner, repo, sha):
            # mirror the real function: a failed read records an UNKNOWN and returns None
            v = stamps.get(sha)
            if v is None:
                pw._unknown('scripted read failure at %s' % sha)
            return v
        pw.coverage_at = _cov
        pw.engine_head = lambda: (None, None)
        sys.argv = ['publish_watch.py'] + list(argv)
        buf, real_out = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            rc = pw.main()
        finally:
            sys.stdout = real_out
        return rc, list(pw.ALARMS), list(pw.NOTES), list(pw.UNKNOWNS)
    finally:
        (pw.sync_mirror, pw.publish_commits, pw.coverage_at,
         pw.engine_head, sys.argv) = real


def main():
    print('publish_watch guard')
    print()

    # 1. THE TIMEZONE BUG. A 17:41 Florida build is 21:41Z in September and 22:41Z in December.
    #    Reading it as the reader's local clock is what this asserts against.
    check('a September build stamp reads as EDT (UTC-4)',
          pw.built_time(_stamp('2026-09-20T17:41'), 'abc12345').isoformat(),
          '2026-09-20T21:41:00+00:00')
    check('a December build stamp reads as EST (UTC-5)',
          pw.built_time(_stamp('2026-12-20T17:41'), 'abc12345').isoformat(),
          '2026-12-20T22:41:00+00:00')
    check('DST starts 02:00 on the second Sunday in March',
          (pw.eastern_offset(dt.datetime(2026, 3, 8, 1, 59)),
           pw.eastern_offset(dt.datetime(2026, 3, 8, 2, 0))), (5, 4))
    check('DST ends 02:00 on the first Sunday in November',
          (pw.eastern_offset(dt.datetime(2026, 11, 1, 1, 59)),
           pw.eastern_offset(dt.datetime(2026, 11, 1, 2, 0))), (4, 5))
    check('a stamp that DOES carry an offset is respected, not shifted again',
          pw.built_time(_stamp('2026-09-20T17:41+00:00'), 'abc12345').isoformat(),
          '2026-09-20T17:41:00+00:00')
    check('a stamp with no build time cannot be dated, and says so',
          (pw.built_time({'leads': 1}, 'abc12345'), bool(pw.UNKNOWNS)), (None, True))
    pw.UNKNOWNS[:] = []

    now = pw._now()
    eastern = now - dt.timedelta(hours=pw.eastern_offset(now.replace(tzinfo=None)))

    def built_hours_ago(h):
        return (eastern - dt.timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M')

    # 2. FRESH vs STALE. Two hours old is last night's run; forty is a skipped night.
    rc, alarms, _, unknowns = _run(
        [('aaa1', now - dt.timedelta(hours=2)), ('bbb2', now - dt.timedelta(hours=26))],
        {'aaa1': _stamp(built_hours_ago(2), leads=2400),
         'bbb2': _stamp(built_hours_ago(26), leads=2390, sig='0ther5ig')},
        ['--json'])
    check('a board built two hours ago is clean, exit 0', (rc, alarms), (0, []))

    rc, alarms, _, unknowns = _run(
        [('aaa1', now - dt.timedelta(hours=40)), ('bbb2', now - dt.timedelta(hours=64))],
        {'aaa1': _stamp(built_hours_ago(40), leads=2400),
         'bbb2': _stamp(built_hours_ago(64), leads=2390, sig='0ther5ig')},
        ['--json'])
    check('a board 40 hours old ALARMS, exit 2', rc, 2)
    check('  and the alarm names the missed nightly',
          bool(alarms) and 'HAS NOT BEEN REBUILT' in alarms[0], True)

    # A publish whose build predates its own push means the publish step ran with no rebuild in
    # front of it — the 09-17 failure, where an already-built board stayed live.
    rc, alarms, _, unknowns = _run(
        [('aaa1', now - dt.timedelta(hours=1))],
        {'aaa1': _stamp(built_hours_ago(20))},
        ['--json'])
    check('a publish that shipped a 19h-old build ALARMS', rc, 2)
    check('  and says the rebuild did not run',
          any('without a rebuild in front of it' in a for a in alarms), True)

    # 3. SHRINK. A small drop is life; a cliff is publish_guard being bypassed.
    rc, _, notes, unknowns = _run(
        [('aaa1', now - dt.timedelta(hours=2)), ('bbb2', now - dt.timedelta(hours=26))],
        {'aaa1': _stamp(built_hours_ago(2), leads=2390, phones=1203),
         'bbb2': _stamp(built_hours_ago(26), leads=2400, phones=1205, sig='0ther5ig')},
        ['--json'])
    check('leads 2400 -> 2390 is a note, not an alarm, exit 0', rc, 0)
    check('  and the drop is still reported',
          any('normal drop' in n for n in notes), True)

    rc, alarms, _, unknowns = _run(
        [('aaa1', now - dt.timedelta(hours=2)), ('bbb2', now - dt.timedelta(hours=26))],
        {'aaa1': _stamp(built_hours_ago(2), leads=1400, phones=700),
         'bbb2': _stamp(built_hours_ago(26), leads=2400, phones=1205, sig='0ther5ig')},
        ['--json'])
    check('leads 2400 -> 1400 ALARMS, exit 2', rc, 2)
    check('  and names publish_guard, which should have refused it',
          any('publish_guard' in a for a in alarms), True)

    # 4. FAIL-LOUD. Nothing readable is exit 3, never exit 0.
    rc, alarms, _, unknowns = _run(None, {}, ['--json'])
    check('an unreachable mirror is CANNOT EVALUATE, exit 3', rc, 3)

    rc, alarms, _, unknowns = _run([('aaa1', now - dt.timedelta(hours=2))], {'aaa1': None}, ['--json'])
    check('a board with no readable coverage stamp is exit 3, not exit 0', rc, 3)

    rc, _, _, unknowns = _run([], {}, ['--json'])
    check('a mirror with no publish commit at all is exit 3', rc, 3)

    # 6. THE FOUR GREPTILE FINDINGS, each pinned so it cannot come back.
    #    All four were the same mistake in different places: a check that did not run being
    #    reported as a check that passed, or as a check that failed.

    #    (a) a failed read of the PREVIOUS publish is not a fault in the CURRENT board.
    rc, alarms, _, unknowns = _run(
        [('aaa1', now - dt.timedelta(hours=2)), ('bbb2', now - dt.timedelta(hours=26))],
        {'aaa1': _stamp(built_hours_ago(2)), 'bbb2': None},
        ['--json'])
    check('an unreadable PREVIOUS publish is exit 3, not exit 2', (rc, alarms), (3, []))
    check('  and is listed as a check that did not run', len(unknowns), 1)

    #    (b) a malformed count cannot be skipped in silence.
    rc, alarms, _, unknowns = _run(
        [('aaa1', now - dt.timedelta(hours=2)), ('bbb2', now - dt.timedelta(hours=26))],
        {'aaa1': _stamp(built_hours_ago(2), leads='2400'),
         'bbb2': _stamp(built_hours_ago(26), leads=2400, sig='0ther5ig')},
        ['--json'])
    check('a non-numeric lead count is exit 3, never a silent exit 0', rc, 3)
    check('  and names the check that could not run',
          any('did not run' in u for u in unknowns), True)

    #    (c) a failed engine fetch must not fall back on a possibly-stale origin/main.
    real_git = pw._git
    try:
        pw.ALARMS[:] = []
        pw.UNKNOWNS[:] = []
        calls = []

        def _fetch_fails(args, cwd=None, timeout=180):
            calls.append(args[0])
            return (1, 'fatal: could not read from remote repository') if args[0] == 'fetch' \
                else (0, 'deadbeefcafe1234 2026-09-01T00:00:00+00:00')
        pw._git = _fetch_fails
        check('a failed engine fetch returns nothing at all', pw.engine_head(), (None, None))
        check('  so the stale ref is never even read', 'log' in calls, False)
        check('  and it is recorded as a check that did not run', len(pw.UNKNOWNS), 1)
        check('  not as an alarm', pw.ALARMS, [])
    finally:
        pw._git = real_git
        pw.ALARMS[:] = []
        pw.UNKNOWNS[:] = []

    #    (d) the main-ahead check may not claim a build CONTAINS a commit it merely postdates.
    #        Only order is provable from a timestamp, so the docstring has to say so.
    doc = pw.check_main_ahead.__doc__ or ''
    check('check_main_ahead documents that a timestamp proves order, not content',
          ('not thereby proven to' in doc.replace('\n', ' ').replace('  ', ' ')
           or 'CONTAIN' in doc), True)

    # 5. ONE address, derived. board_url.py exists because nine hand-written copies drifted.
    owner, repo, url = pw.mirror_repo()
    check('the mirror repo is derived from BOARD_URL',
          url, 'https://github.com/%s/%s.git' % (owner, repo))
    check('  and BOARD_URL is the page that repo publishes',
          board_url.BOARD_URL, 'https://%s.github.io/%s/' % (owner, repo))
    # The real assertion is behavioural, not a grep: move the address and the mirror must move
    # with it. A grep would trip over the docstrings, which cite the old address on purpose.
    real_url = board_url.BOARD_URL
    try:
        board_url.BOARD_URL = 'https://someoneelse.github.io/new-board/'
        check('  and moving BOARD_URL moves the mirror with it',
              pw.mirror_repo(), ('someoneelse', 'new-board',
                                 'https://github.com/someoneelse/new-board.git'))
        pw.UNKNOWNS[:] = []
        board_url.BOARD_URL = 'https://example.com/not-a-pages-site/'
        check('  and an address it cannot parse refuses to guess',
              (pw.mirror_repo(), bool(pw.UNKNOWNS)), ((None, None, None), True))
        pw.UNKNOWNS[:] = []
    finally:
        board_url.BOARD_URL = real_url

    print()
    print('%d pass / %d fail' % (CHECKS[0] - len(FAILS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
