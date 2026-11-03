"""quick_publish.py - put newly merged code on the live board and Call Mode in minutes, not hours.

WHY (2026-11-03, Alejandro: "i want it to merge instantly"). A merged change to Call Mode or the
board reaches the phones only when docs/index.html and docs/call/ are rebuilt and mirrored to the
public site. Until now only refresh-dealflow.bat did that for code changes, and that run scrapes,
skip-traces, pulls dockets and checks bankruptcy for hours before its [4/5] rebuild - and on a
partial night (rc 2) it never publishes at all. The rebuild itself needs none of that: it reads
leads_final.json and the caches the last refresh already left on this machine.

So this does only the tail of the refresh, with the same gates:

    1. refuse while a refresh owns the repo (fresh refresh-running.flag, or a live runner lease)
    2. git pull --ff-only origin main            (the new code; refuses rather than merging)
    3. make_tracker(leads_final.json)            (same call as the [4/5] step, no network, no spend)
    4. healthcheck.py, then publish_guard.py     (same exit-code rules as the [gate] step)
    5. commit docs/index.html + docs/call, push  (best effort; the publish_guard baseline)
    6. publish_site.py                           (the LIVE mirror; its exit code is this run's result)

WHAT IT DOES NOT DO. No scrape, no skip-trace, no paid lookup, no send, no text, no opt-out or
ledger write. Lead data is exactly what the last refresh produced; only the code around it is new.
It never uses `-X theirs` or `--autostash` (the 08-19 conflict-marker outage) and never force-pushes.
If the engine push fails, the local commit is undone so main is not left ahead of origin - otherwise
the next 04:30/05:30 `git pull --ff-only` refuses and the night builds yesterday's code.

Usage:
    python quick_publish.py               # pull, rebuild, gate, publish
    python quick_publish.py --no-pull     # rebuild what is already checked out
    python quick_publish.py --dry-run     # pull + rebuild + gates, then discard the build

Exit codes: 0 live site updated (or already current) | 1 refused before building (refresh running,
not on main, pull failed, no leads file) | 2 a publish gate blocked it | 3 rebuild failed |
5 gates passed but the live mirror did not publish.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
BUILT = ['docs/index.html', 'docs/call']
REBUILD = ("import json, foreclosure_leads as F; "
           "F.make_tracker(json.load(open('leads_final.json', encoding='utf-8')))")


def say(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))
    sys.stdout.flush()


def git(*args, check=False):
    r = subprocess.run(['git', '--no-pager'] + list(args), cwd=HERE,
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError('git %s failed: %s' % (' '.join(args), (r.stderr or r.stdout).strip()))
    return r


def py(*args):
    return subprocess.run([sys.executable, '-u'] + list(args), cwd=HERE).returncode


def blocked_reason():
    """'' when this run may touch git, else why not. Same rule pipeline_alerts uses for the 07:15
    sync: a refresh-running.flag younger than 6h, or this machine's live runner lease."""
    try:
        import pipeline_alerts
        return pipeline_alerts.git_blocked(HERE)
    except Exception as e:          # noqa: BLE001 - cannot prove the repo is free, so wait
        return 'could not read the refresh flag (%s)' % e


def gates_pass():
    """healthcheck exit 2 = compliance hard block; exit 1 = coverage advisory, publish_guard decides;
    publish_guard non-zero = a board poorer than live. Identical to refresh-dealflow.bat [gate]."""
    h = py('healthcheck.py')
    if h >= 2:
        say('  !! GATE: healthcheck COMPLIANCE fail - nothing published. Live site left as it was.')
        return False
    if h == 1:
        say('  note: healthcheck coverage below floor - advisory, publish_guard decides.')
    if py('publish_guard.py') != 0:
        say('  !! GATE: publish_guard blocked a board poorer than the live one - nothing published.')
        return False
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--no-pull', action='store_true', help='rebuild the code already checked out')
    ap.add_argument('--dry-run', action='store_true', help='build and gate, publish nothing')
    a = ap.parse_args(argv)

    why = blocked_reason()
    if why:
        say('REFUSED: %s - the refresh owns the repo. It rebuilds with the newest code itself;'
            ' run this again after it ends.' % why)
        return 1
    if os.path.realpath(git('rev-parse', '--show-toplevel').stdout.strip() or '.') != os.path.realpath(HERE):
        say('REFUSED: this folder is not the engine checkout.')
        return 1
    branch = git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    if branch != 'main':
        say('REFUSED: checked out on %r, not main. The live board is built from main only.' % branch)
        return 1
    if not os.path.isfile(LEADS):
        say('REFUSED: no leads_final.json here. Only the machine that runs the refresh can rebuild.')
        return 1

    if not a.no_pull:
        say('[1/5] Pulling the merged code (fast-forward only)...')
        git('checkout', '-q', 'HEAD', '--', 'pipeline_alerts.json')
        r = git('pull', '--ff-only', 'origin', 'main')
        if r.returncode != 0:
            say((r.stderr or r.stdout).strip())
            say('REFUSED: git could not fast-forward (offline, uncommitted edits, or local commits).'
                ' Nothing built. `git status` shows which.')
            return 1
    head = git('rev-parse', '--short', 'HEAD').stdout.strip()
    say('      building code at %s' % head)

    say('[2/5] Rebuilding the board + Call Mode from the last refresh\'s lead data...')
    if py('-c', REBUILD) != 0:
        say('REBUILD FAILED - nothing published. The live site is unchanged.')
        git('checkout', '-q', 'HEAD', '--', *BUILT)
        return 3

    say('[3/5] Publish gates (healthcheck, publish_guard)...')
    if not gates_pass():
        git('checkout', '-q', 'HEAD', '--', *BUILT)
        return 2
    if a.dry_run:
        git('checkout', '-q', 'HEAD', '--', *BUILT)
        say('DRY RUN: gates passed. Built pages discarded; nothing committed or pushed.')
        return 0

    say('[4/5] Committing the built pages (engine repo, publish_guard baseline)...')
    base = git('rev-parse', 'HEAD').stdout.strip()
    git('add', '--', *BUILT)
    committed = git('commit', '-q', '-m', 'quick: rebuild board + Call Mode at %s (no new lead data)'
                    % head).returncode == 0
    pushed = True
    if committed:
        pushed = git('push', 'origin', 'main').returncode == 0
        if not pushed:
            say('      engine push rejected or timed out - the live mirror below still runs.')
    else:
        say('      built pages identical to the last commit.')

    say('[5/5] Publishing to the live site (publish_site.py -> dealflow-board)...')
    live = py('publish_site.py') == 0

    if committed and not pushed:
        # Never leave local main ahead of origin: the next refresh's --ff-only pull would refuse
        # and build yesterday's code. The nightly rebuilds and commits these pages itself.
        git('reset', '-q', '--soft', base)
        git('reset', '-q', '--', *BUILT)
        git('checkout', '-q', 'HEAD', '--', *BUILT)
        say('      local commit undone (main == origin again).')

    if not live:
        say('!! LIVE MIRROR DID NOT PUBLISH - gates passed but the live site is unchanged.'
            ' See publish_site output above.')
        return 5
    say('DONE - live board + Call Mode built from %s. Reload Call Mode in ~1-2 min.' % head)
    return 0


if __name__ == '__main__':
    sys.exit(main())
