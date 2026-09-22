"""publish_watch.py -- did last night's board actually reach the live site?

WHY THIS EXISTS
The 05:30 nightly runs `refresh-dealflow.bat` on the laptop and ends by pushing the built board
to the PUBLIC mirror, which is what GitHub Pages serves. Three separate faults have each left
that chain silently dead for days at a time:

  * 2026-08-20 -> 2026-09-19, the '[5/5] publish' stage of refresh-dealflow.bat died on an
    unescaped ')' inside an `if errorlevel` block (fixed on main as 7b4d61b). The run reported
    success; the site stopped moving.
  * 2026-09-14 / 16 / 17, the laptop had no DNS at 05:30. The task fired, finished in thirty
    seconds, and looked like a thin scrape.
  * 2026-09-17, the engine/board split left an ALREADY-BUILT board live carrying the retired
    URL, because a hand-edit cannot reach a page generated yesterday.

Every one of them was found by a human eventually noticing the board looked old. This file is
the thing that notices instead, and it deliberately runs from ANYWHERE -- it reads the published
repo over the network and needs no laptop file, no key and no local build. That is the whole
point: a watchdog that can only run on the machine that failed is not a watchdog.

NOT engine_drift.py. That one is the LOCAL watchdog -- it reads this checkout's docs/index.html
and the gitignored enricher caches beside it, and answers "is this machine's build coherent".
This one never looks at local data; it answers "is the thing the public can see current". A
laptop can pass engine_drift and still be publishing nothing.

THE FAIL-LOUD RULE, borrowed from engine_drift.py and for the same reason: a state this file
cannot evaluate ALARMS, it never prints ok. Exit codes match that file so a caller can treat
them the same way:

    0 = CLEAN             every check ran and passed
    2 = ALARM             a check ran and failed: the board is stale, shrunken, or behind main
    3 = CANNOT EVALUATE   a check could not run at all

Exit 0 requires that every check actually RAN. A check that could not be evaluated -- an
unreadable stamp, a count that is not a number, an engine repo this machine cannot fetch -- is
exit 3, never exit 0, and never exit 2 either: exit 2 is reserved for a fault that was actually
observed. When both are present exit 2 wins, because a confirmed fault is the more useful thing
to report and the unevaluated check is listed alongside it.

WHAT IT READS
  * The mirror's commit list, via a blobless clone (`--filter=blob:none`). The built board is
    ~15 MB per commit, so fetching blobs to read a 200-byte header would pull a hundred-odd MB
    a day; the clone carries commits and trees only.
  * Each publish's DEALFLOW-COVERAGE stamp, via an HTTP Range request for the first 512 bytes
    of docs/index.html at that commit. make_tracker() writes that census on line 1 precisely so
    it can be read without decrypting or downloading the board.
  * This checkout's `origin/main`, to tell whether the live board was built from current code.

WHAT IT CANNOT SEE, and these are not oversights, they are the honest edges:
  * It reads the repo GitHub Pages BUILDS FROM, not the rendered page. A publish that lands in
    the mirror but fails to deploy, or a Pages outage, looks clean here.
  * It cannot tell a scrape that found nothing from a county that listed nothing.
  * It cannot start, wake, or fix the laptop. If the machine is asleep at 05:30 with no network,
    this file reports that fact the next morning and nothing more.

Usage:
    python publish_watch.py                 # human report
    python publish_watch.py --json          # machine-readable
    python publish_watch.py --max-age 26    # hours before a missing publish alarms
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

import board_url
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
COV_MARK = re.compile(r'<!--\s*DEALFLOW-COVERAGE\s*(\{.*?\})\s*-->')

# The board is rebuilt nightly at 05:30. 26h lets a late catch-up run (the task is
# StartWhenAvailable, so a run missed while the laptop slept fires when it wakes) count as last
# night's, while a fully skipped night still alarms before the next one starts.
DEFAULT_MAX_AGE_H = 26

# A drop smaller than this is normal: leads leave the board when an auction is sold, cancelled
# or redeemed. Measured on the mirror's own history -- 2,372 -> 2,368 -> 2,394 over 09-18 alone.
# So "count went down" is NOT a fault; "count fell off a cliff" is, and that is what
# publish_guard.py refuses to publish in the first place.
SHRINK_FRACTION = 0.05

# How long code may sit on main before the live board being older than it is a finding rather
# than just this evening's merge not being published yet.
MAIN_AHEAD_H = 24

# How far a build time may trail its own publish commit before the publish looks like it shipped
# a board that was already on disk.
STALE_BUILD_H = 6

HTTP_TIMEOUT = 30
HEADER_BYTES = 512

ALARMS = []
UNKNOWNS = []
NOTES = []


def _alarm(msg):    ALARMS.append(msg)
def _note(msg):     NOTES.append(msg)


def _unknown(msg):
    """A check that could not be evaluated. NOT an alarm -- see the exit codes in the docstring.

    Greptile caught the original conflation: a failed read of the PREVIOUS publish made the run
    exit 2, reporting a confirmed problem with the current board when all that actually happened
    was that one comparison could not be made. Exit 2 means "I checked and it is wrong"; this
    list means "I could not check".
    """
    UNKNOWNS.append(msg)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def mirror_repo():
    """Derive the mirror's git URL from board_url.BOARD_URL -- never a second copy of it.

    board_url.py is the one place that knows where the board lives, and it exists because the
    2026-09-17 move was hand-edited into nine files and still broke. A hardcoded
    'aiejandrog/dealflow-board' here would be the tenth.
    """
    m = re.match(r'^https://([\w.-]+)\.github\.io/([\w.-]+)/?$', board_url.BOARD_URL)
    if not m:
        _unknown('BOARD_URL %r is not a github.io project page, so the mirror repo cannot be '
                 'derived from it. Teach mirror_repo() the new shape.' % board_url.BOARD_URL)
        return None, None, None
    owner, repo = m.group(1), m.group(2)
    return owner, repo, 'https://github.com/%s/%s.git' % (owner, repo)


def _git(args, cwd=None, timeout=180):
    """Run git, return (rc, stdout). Never raises on a non-zero exit."""
    try:
        p = subprocess.run(['git'] + args, cwd=cwd, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return p.returncode, p.stdout.decode('utf-8', 'replace').strip()
    except FileNotFoundError:
        return 127, 'git not found on PATH'
    except subprocess.TimeoutExpired:
        return 124, 'git timed out after %ds' % timeout


def sync_mirror(git_url, cache):
    """Blobless clone of the mirror, or an incremental fetch if we already have one."""
    if os.path.isdir(os.path.join(cache, '.git')):
        rc, out = _git(['fetch', '--prune', 'origin'], cwd=cache)
        if rc == 0:
            return True
        _note('fetch of the cached mirror failed (%s) -- recloning.' % out.splitlines()[-1:])
        shutil.rmtree(cache, ignore_errors=True)
    rc, out = _git(['clone', '--filter=blob:none', '--no-checkout', git_url, cache])
    if rc != 0:
        _unknown('cannot reach the published mirror %s -- %s. The live board cannot be checked '
                 'from here at all, so this run proves nothing either way.'
                 % (git_url, out.splitlines()[-1] if out else 'no output'))
        return False
    return True


def publish_commits(cache, limit):
    """[(sha, committed_utc)] for the last `limit` commits that touched the built board."""
    rc, out = _git(['log', 'origin/HEAD', '--format=%H %cI', '-n', str(limit),
                    '--', 'docs/index.html'], cwd=cache)
    if rc != 0 or not out:
        rc, out = _git(['log', '--format=%H %cI', '-n', str(limit),
                        '--', 'docs/index.html'], cwd=cache)
    if rc != 0 or not out:
        _unknown('the mirror has no commit touching docs/index.html -- either the clone is '
                 'empty or the board is published under a different path now.')
        return []
    rows = []
    for line in out.splitlines():
        sha, _, iso = line.partition(' ')
        try:
            rows.append((sha, dt.datetime.fromisoformat(iso).astimezone(dt.timezone.utc)))
        except ValueError:
            _note('unparseable commit date %r on %s' % (iso, sha[:8]))
    return rows


def coverage_at(owner, repo, sha):
    """The DEALFLOW-COVERAGE census from line 1 of the board at `sha`.

    A Range request, not a download. The built board is ~15 MB; the stamp is ~200 bytes and is
    on line 1 exactly so that it is cheap to read.

    A read that fails is CANNOT EVALUATE, never an alarm -- including for the current board.
    ALARMS in this file means "I checked and it is wrong"; not being able to read a board is not
    a finding about that board. Reporting it as one meant a single flaky HTTP request against
    yesterday's publish produced "the published board has a problem".
    """
    url = 'https://raw.githubusercontent.com/%s/%s/%s/docs/index.html' % (owner, repo, sha)
    req = urllib.request.Request(url, headers={'Range': 'bytes=0-%d' % HEADER_BYTES,
                                               'User-Agent': 'dealflow-publish-watch'})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            head = r.read(HEADER_BYTES + 1).decode('utf-8', 'replace')
    except (urllib.error.URLError, OSError) as e:
        _unknown('could not read the published board at %s (%s). Without the coverage stamp this '
                 'run cannot say whether the site is current.' % (sha[:8], e))
        return None
    m = COV_MARK.search(head)
    if not m:
        _unknown('the published board at %s carries NO DEALFLOW-COVERAGE stamp on line 1. Either '
                 'docs/index.html was hand-edited -- which CLAUDE.md forbids because the next '
                 'refresh destroys it -- or it was built by a make_tracker that predates the '
                 'census.' % sha[:8])
        return None
    try:
        return json.loads(m.group(1))
    except ValueError as e:
        _unknown('the coverage stamp at %s is not valid JSON (%s).' % (sha[:8], e))
        return None


def _nth_weekday(year, month, weekday, n):
    """UTC-agnostic helper: the date of the nth `weekday` (0=Mon) of a month."""
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def eastern_offset(naive):
    """Hours west of UTC for a naive US/Eastern wall-clock time: 4 in EDT, 5 in EST.

    THE STAMP HAS NO TIMEZONE. make_tracker() writes `built` as a local wall clock on the machine
    that built it, and that machine is a laptop in Florida. Reading it with .astimezone() -- which
    assumes the timezone of whatever box is READING it -- put the build four hours in the past
    when this file ran from a UTC container, which overstates the board's age by four hours and
    would have fired a false "did not publish" alarm every morning between 01:30 and 05:30 ET.

    The rule is implemented here rather than through zoneinfo on purpose: zoneinfo on Windows has
    no system tz database and needs the `tzdata` package, so an import that works in a container
    raises on the laptop this watchdog is watching. US Eastern is DST from 02:00 on the second
    Sunday in March to 02:00 on the first Sunday in November, and has been since 2007.
    """
    start = dt.datetime.combine(_nth_weekday(naive.year, 3, 6, 2), dt.time(2, 0))
    end = dt.datetime.combine(_nth_weekday(naive.year, 11, 6, 1), dt.time(2, 0))
    return 4 if start <= naive < end else 5


def built_time(cov, sha):
    """The stamp's own build time as UTC. See eastern_offset() for why it is not .astimezone()."""
    raw = (cov or {}).get('built')
    if not raw:
        _unknown('the coverage stamp at %s has no "built" time, so the age of the live board '
                 'cannot be established.' % sha[:8])
        return None
    try:
        naive = dt.datetime.fromisoformat(raw)
    except ValueError:
        _unknown('unparseable build time %r in the stamp at %s.' % (raw, sha[:8]))
        return None
    if naive.tzinfo is not None:
        return naive.astimezone(dt.timezone.utc)
    return (naive + dt.timedelta(hours=eastern_offset(naive))).replace(tzinfo=dt.timezone.utc)


def engine_head():
    """(sha, committed_utc) for origin/main in THIS checkout, after a fetch.

    A fetch failure is not an alarm: plenty of places can read the public mirror without being
    able to read the private engine. But it is not nothing either, and the first version got that
    wrong -- it said the check "did not run" and then ran it anyway against whatever `origin/main`
    the checkout happened to hold. A remote-tracking ref that is a week old would have been
    compared as if it were current, which can produce a false alarm or, worse, a clean result.
    A failed fetch now returns nothing, so the check genuinely does not run.
    """
    rc, out = _git(['fetch', 'origin', 'main'], cwd=HERE)
    if rc != 0:
        _unknown('could not fetch the engine repo (%s), so origin/main here may be stale -- the '
                 '"is the live board built from current code" check did not run.'
                 % (out.splitlines()[-1] if out else 'no output'))
        return None, None
    rc, out = _git(['log', '-1', '--format=%H %cI', 'origin/main'], cwd=HERE)
    if rc != 0 or not out:
        _unknown('no origin/main in this checkout -- the main-ahead check did not run.')
        return None, None
    sha, _, iso = out.partition(' ')
    try:
        return sha, dt.datetime.fromisoformat(iso).astimezone(dt.timezone.utc)
    except ValueError:
        _unknown('unparseable origin/main date %r -- the main-ahead check did not run.' % iso)
        return None, None


def check_freshness(built, commit_at, max_age_h):
    now = _now()
    age = (now - built).total_seconds() / 3600.0
    if age > max_age_h:
        _alarm('THE LIVE BOARD HAS NOT BEEN REBUILT IN %.0f HOURS -- last build %s, published %s. '
               'Last night\'s 05:30 run did not publish.'
               % (age, built.isoformat(timespec='minutes'),
                  commit_at.isoformat(timespec='minutes')))
    lag = (commit_at - built).total_seconds() / 3600.0
    if lag > STALE_BUILD_H:
        _alarm('the last publish shipped a board that was already %.0fh old when it was pushed '
               '(built %s, pushed %s) -- the publish step ran without a rebuild in front of it.'
               % (lag, built.isoformat(timespec='minutes'),
                  commit_at.isoformat(timespec='minutes')))
    return age


def check_shrink(cur, prev, prev_sha):
    """Only a MATERIAL drop is a fault. See SHRINK_FRACTION.

    A count that is missing or is not a number used to be skipped in silence, and the run then
    printed "every check ran and passed". make_tracker always writes both counts as integers, so
    one arriving malformed means something upstream changed -- exactly the case a watchdog must
    not sleep through.
    """
    for key, label in (('leads', 'leads'), ('phones', 'phones')):
        now_v, was_v = cur.get(key), (prev or {}).get(key)
        if not isinstance(now_v, int) or not isinstance(was_v, int) or was_v <= 0:
            _unknown('cannot compare %s: the stamps carry %r now and %r at %s, so the '
                     'did-the-board-shrink check did not run for it.'
                     % (label, now_v, was_v, prev_sha[:8]))
            continue
        if now_v < was_v * (1.0 - SHRINK_FRACTION):
            _alarm('%s fell from %d to %d (-%.0f%%) against the previous publish %s. '
                   'publish_guard.py is supposed to refuse a board this much poorer, so either '
                   'it was bypassed or the drop is real.'
                   % (label, was_v, now_v, 100.0 * (was_v - now_v) / was_v, prev_sha[:8]))
        elif now_v < was_v:
            _note('%s %d -> %d, a normal drop (sold, cancelled or redeemed).' % (label, was_v, now_v))


def check_main_ahead(built, head_sha, head_at):
    """Did code land on main that the live board was built before?

    WHAT THIS PROVES, AND WHAT IT DOES NOT. The coverage stamp carries a build time and a content
    signature, but no engine commit, so the only thing comparable here is ORDER: was the board
    built before or after that commit landed. A board built AFTER it is not thereby proven to
    CONTAIN it -- a publish path building from a checkout that failed to pull produces a fresh
    timestamp over old code, which is not hypothetical: it is the 2026-09-10 bug, where every
    push from the other machine landed a day late while the repo looked up to date.

    So a clean result here means "no code has been sitting on main unpublished", not "the live
    board is built from main". Closing that gap needs the engine SHA baked into the stamp by
    make_tracker(), which is a generator change and not this file's to make.
    """
    if head_sha is None or head_at is None:
        return
    if built >= head_at:
        return
    behind = (_now() - head_at).total_seconds() / 3600.0
    if behind > MAIN_AHEAD_H:
        _alarm('main has carried code the live board does not have for %.0f hours -- head %s '
               'landed %s, the live board was built %s. Nothing merged since then is on the '
               'board or the phone until refresh-dealflow.bat runs.'
               % (behind, head_sha[:8], head_at.isoformat(timespec='minutes'),
                  built.isoformat(timespec='minutes')))
    else:
        _note('main is ahead of the live board by %.0fh (head %s) -- expected until the next '
              'nightly rebuild.' % (behind, head_sha[:8]))


def render_text(owner, repo, rows, cur, prev, age, head_sha, head_at):
    L = []
    W = 78
    L.append('=' * W)
    L.append('  PUBLISHED BOARD WATCH')
    L.append('=' * W)
    L.append('  mirror          : %s/%s' % (owner, repo))
    L.append('  live page       : %s' % board_url.BOARD_URL)
    if cur:
        L.append('  live build      : %s  (%.1fh old)' % (cur.get('built', '?'), age if age is not None else -1))
        L.append('  leads / phones  : %s / %s' % (cur.get('leads', '?'), cur.get('phones', '?')))
        L.append('  signature       : %s' % cur.get('sig', '?'))
    if prev:
        L.append('  previous build  : %s  (%s leads / %s phones)'
                 % (prev.get('built', '?'), prev.get('leads', '?'), prev.get('phones', '?')))
    if head_sha:
        L.append('  engine main     : %s  %s' % (head_sha[:8], head_at.isoformat(timespec='minutes')))
    L.append('')
    L.append('  RECENT PUBLISHES')
    for sha, when in rows[:6]:
        L.append('    %s  %s' % (sha[:8], when.isoformat(timespec='minutes')))
    L.append('')
    if ALARMS:
        L.append('  !! ALARM  %d problem(s) with the published board' % len(ALARMS))
        for a in ALARMS:
            L.append('     - ' + a)
    if UNKNOWNS:
        L.append('  ?? CANNOT EVALUATE  %d check(s) did not run' % len(UNKNOWNS))
        for u in UNKNOWNS:
            L.append('     - ' + u)
    if NOTES:
        L.append('  .. NOTES  %d observation(s), not a hard fail' % len(NOTES))
        for n in NOTES:
            L.append('     - ' + n)
    if not ALARMS and not UNKNOWNS:
        L.append('  OK -- the live board is current.')
    L.append('=' * W)
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--max-age', type=float, default=DEFAULT_MAX_AGE_H,
                    help='hours before a missing publish alarms (default %d)' % DEFAULT_MAX_AGE_H)
    ap.add_argument('--history', type=int, default=8, help='publish commits to list')
    ap.add_argument('--cache', default=None,
                    help='where the blobless mirror clone lives (default: DEALFLOW_DIR)')
    a = ap.parse_args()

    owner, repo, git_url = mirror_repo()
    cache = a.cache or P.out('publish-watch-mirror')

    rows, cur, prev, age = [], None, None, None
    head_sha = head_at = None
    if git_url and sync_mirror(git_url, cache):
        rows = publish_commits(cache, a.history)
        if rows:
            cur = coverage_at(owner, repo, rows[0][0])
            if cur:
                built = built_time(cur, rows[0][0])
                if built:
                    age = check_freshness(built, rows[0][1], a.max_age)
                    head_sha, head_at = engine_head()
                    check_main_ahead(built, head_sha, head_at)
                if len(rows) > 1:
                    prev = coverage_at(owner, repo, rows[1][0])
                    if prev:
                        check_shrink(cur, prev, rows[1][0])
                        if prev.get('sig') and prev.get('sig') == cur.get('sig'):
                            _note('this publish has the same signature as the previous one (%s) '
                                  '-- the rebuild produced an identical board.' % cur.get('sig'))

    # Exit 0 requires that every check RAN. A confirmed fault (2) outranks an unevaluated check
    # (3) because it is the more useful thing to report, and the unevaluated one is printed
    # beside it either way. What neither may do is collapse into 0.
    if ALARMS:
        exit_code = 2
    elif UNKNOWNS or cur is None or age is None:
        exit_code = 3
    else:
        exit_code = 0

    if a.json:
        print(json.dumps({
            'mirror': '%s/%s' % (owner, repo) if owner else None,
            'board_url': board_url.BOARD_URL,
            'live': cur, 'previous': prev, 'age_hours': age,
            'engine_main': {'sha': head_sha,
                            'at': head_at.isoformat() if head_at else None},
            'publishes': [{'sha': s, 'at': w.isoformat()} for s, w in rows],
            'alarms': ALARMS, 'unknowns': UNKNOWNS, 'notes': NOTES, 'exit': exit_code,
        }, indent=1))
    else:
        txt = render_text(owner, repo, rows, cur, prev, age, head_sha, head_at)
        try:
            print(txt)
        except UnicodeEncodeError:
            print(txt.encode('ascii', 'replace').decode('ascii'))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
