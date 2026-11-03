"""_quickpublishtest.py - quick_publish.py, the minutes-not-hours path from a merge to the live board.

What has to hold:
  - a fresh refresh flag or a live lease refuses before any git call (no pull mid-refresh);
  - not on main, or no leads_final.json, refuses before building;
  - a failed fast-forward builds nothing;
  - healthcheck runs before publish_guard, both before any commit or publish_site;
  - healthcheck exit 2 or a publish_guard block publishes nothing and restores docs/;
  - healthcheck exit 1 is advisory: publish_guard decides;
  - a rejected engine push still mirrors the live site, then undoes the local commit;
  - publish_site's exit code is the run's result;
  - the file never uses -X theirs, --autostash, or a force push.

Run: python _quickpublishtest.py
"""
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import quick_publish as Q  # noqa: E402

FAILS = []


def rec(name, ok):
    print('%s  %s' % ('ok  ' if ok else 'FAIL', name))
    if not ok:
        FAILS.append(name)


def harness(blocked='', branch='main', leads=True, pull_rc=0, rebuild_rc=0, hc=0, pg=0,
            commit_rc=0, push_rc=0, site_rc=0, argv=()):
    log = []

    def fake_git(*args, check=False):
        log.append(('git',) + args)
        out, rc = '', 0
        if args[:2] == ('rev-parse', '--show-toplevel'):
            out = HERE
        elif args[:2] == ('rev-parse', '--abbrev-ref'):
            out = branch
        elif args[:1] == ('rev-parse',):
            out = 'abc1234'
        elif args[:1] == ('pull',):
            rc = pull_rc
        elif args[:1] == ('commit',):
            rc = commit_rc
        elif args[:1] == ('push',):
            rc = push_rc
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr='')

    def fake_py(*args):
        log.append(('py',) + args)
        if args[0] == '-c':
            return rebuild_rc
        return {'healthcheck.py': hc, 'publish_guard.py': pg, 'publish_site.py': site_rc}[args[0]]

    Q.git, Q.py = fake_git, fake_py
    Q.blocked_reason = lambda: blocked
    real_isfile = os.path.isfile
    Q.os.path.isfile = lambda p: leads if p == Q.LEADS else real_isfile(p)
    Q.say = lambda m: None
    try:
        rc = Q.main(list(argv))
    finally:
        Q.os.path.isfile = real_isfile
    return rc, log


def idx(log, pred):
    for i, e in enumerate(log):
        if pred(e):
            return i
    return -1


def has(log, *head):
    return idx(log, lambda e: e[:len(head)] == head) >= 0


# refresh running: nothing at all touches git
rc, log = harness(blocked='refresh-running')
rec('refresh flag refuses with 1', rc == 1)
rec('refresh flag: zero git or python calls', log == [])

rc, log = harness(branch='claude/x')
rec('not on main refuses', rc == 1 and not has(log, 'pull') and not has(log, 'py', '-c'))

rc, log = harness(leads=False)
rec('no leads file refuses before pulling', rc == 1 and not has(log, 'pull'))

rc, log = harness(pull_rc=1)
rec('failed fast-forward builds nothing', rc == 1 and not has(log, 'py', '-c'))
rec('pull is --ff-only', has(log, 'git', 'pull', '--ff-only', 'origin', 'main'))

rc, log = harness(argv=['--no-pull'])
rec('--no-pull skips the pull', rc == 0 and not has(log, 'git', 'pull'))

rc, log = harness(rebuild_rc=1)
rec('rebuild failure = 3, no gates, no publish', rc == 3 and not has(log, 'py', 'healthcheck.py')
    and not has(log, 'py', 'publish_site.py'))

# happy path order
rc, log = harness()
i_hc = idx(log, lambda e: e[:2] == ('py', 'healthcheck.py'))
i_pg = idx(log, lambda e: e[:2] == ('py', 'publish_guard.py'))
i_commit = idx(log, lambda e: e[:2] == ('git', 'commit'))
i_site = idx(log, lambda e: e[:2] == ('py', 'publish_site.py'))
i_build = idx(log, lambda e: e[:2] == ('py', '-c'))
rec('happy path exit 0', rc == 0)
rec('order: build < healthcheck < publish_guard < commit < publish_site',
    -1 < i_build < i_hc < i_pg < i_commit < i_site)
rec('stages only docs/index.html + docs/call', has(log, 'git', 'add', '--', 'docs/index.html', 'docs/call'))
rec('happy path never resets', not has(log, 'git', 'reset'))

rc, log = harness(hc=2)
rec('healthcheck 2 blocks: exit 2', rc == 2)
rec('healthcheck 2: no publish_guard, commit or publish_site',
    not has(log, 'py', 'publish_guard.py') and not has(log, 'git', 'commit') and not has(log, 'py', 'publish_site.py'))
rec('healthcheck 2: built pages restored', has(log, 'git', 'checkout', '-q', 'HEAD', '--', 'docs/index.html', 'docs/call'))

rc, log = harness(hc=1)
rec('healthcheck 1 is advisory', rc == 0 and has(log, 'py', 'publish_guard.py') and has(log, 'py', 'publish_site.py'))

rc, log = harness(hc=1, pg=2)
rec('publish_guard block: exit 2, nothing committed or mirrored',
    rc == 2 and not has(log, 'git', 'commit') and not has(log, 'py', 'publish_site.py'))

rc, log = harness(argv=['--dry-run'])
rec('dry run: gates run, nothing committed or mirrored, pages restored',
    rc == 0 and has(log, 'py', 'publish_guard.py') and not has(log, 'git', 'commit')
    and not has(log, 'py', 'publish_site.py') and has(log, 'git', 'checkout', '-q', 'HEAD', '--', 'docs/index.html', 'docs/call'))

rc, log = harness(push_rc=1)
i_site = idx(log, lambda e: e[:2] == ('py', 'publish_site.py'))
i_reset = idx(log, lambda e: e[:3] == ('git', 'reset', '-q') and '--soft' in e)
rec('rejected push still mirrors live, exit 0', rc == 0 and i_site >= 0)
rec('rejected push: commit undone after the mirror', i_reset > i_site)

rc, log = harness(commit_rc=1)
rec('nothing changed: no push, mirror still runs', rc == 0 and not has(log, 'git', 'push')
    and has(log, 'py', 'publish_site.py'))

rc, log = harness(site_rc=1)
rec('mirror failure is exit 5', rc == 5)

src = open(os.path.join(HERE, 'quick_publish.py'), encoding='utf-8').read()
code = '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('#'))
code = re.sub(r'"""[\s\S]*?"""', '', code)
rec('no -X theirs / --autostash / force push in code',
    not re.search(r"'-X'|'theirs'|--autostash|'--force|'-f'", code))

claude = open(os.path.join(HERE, 'CLAUDE.md'), encoding='utf-8').read()
rec('CLAUDE.md publish table lists quick_publish.py', '`quick_publish.py`' in claude)

print('\n%d failed' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
