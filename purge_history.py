#!/usr/bin/env python
"""purge_history.py -- rewrite the engine repo's git history so the footprint.local.json values are gone.

NEVER PUSHES. It rewrites a fresh MIRROR CLONE in a directory you name, verifies it, and prints the
push commands for you to run (or not). The rewrite changes every commit id, so read "BEFORE YOU PUSH".

    python purge_history.py --plan                 # read-only: how much of this repo's history is affected
    python purge_history.py --run D:\\purge-work    # clone --mirror into that EMPTY dir, rewrite, verify

What it removes -- every value comes from footprint.local.json; nothing real is in this file:
  * file contents, at every commit: each advisor / retired email (plain, regex-escaped, and the
    mailbox name alone), each retired address (any spacing or escaping), each deny string
  * commit messages: the same
  * commit author and committer email: any retired / advisor address (plus --map-email values) is
    replaced by the GitHub noreply identity (default: publish_site.SITE_GIT_EMAIL). Names are kept.

BEFORE YOU PUSH (the part that decides whether this was worth doing)
  1. Make the repo PRIVATE first. A rewrite does not undo what was already copied.
  2. A public fork keeps its own full copy of the old history. Rewriting this repo does nothing to it;
     only its owner deleting it, or GitHub Support, does.
  3. GitHub keeps old commits viewable by SHA after a force-push until it garbage-collects them. For a
     hard purge, contact GitHub Support (sensitive data removal) after pushing.
  4. Every commit id changes: open PRs break, and EVERY clone (laptop, desktop, the bridge's checkout)
     must be re-cloned afterwards. Do the push when nothing is mid-run.
  5. The nightly publishes docs/ on top of whatever this machine has. Re-clone, then re-run it.

Needs `git filter-repo` (pip install git-filter-repo) and free disk of about 2x the repo size.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

import footprint as FP

PLACEHOLDER_EMAIL = 'redacted@example.invalid'
PLACEHOLDER_NAME = 'redacted'
PLACEHOLDER_ADDR = '100 Example Ave'
PLACEHOLDER_TEXT = '[redacted]'


def run(args, cwd=None, check=True, input_=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                       input=input_)
    if check and p.returncode:
        raise SystemExit('purge_history: %s failed (%d)\n%s%s' % (' '.join(args[:4]), p.returncode, p.stdout[-800:], p.stderr[-1200:]))
    return p


def emails():
    return sorted({e.lower() for e in list(FP.advisor_emails()) + FP.retired()['email']})


def _rx(value):
    """The guard's own tolerant matcher as a filter-repo regex rule. `.pattern` drops the compiled
    IGNORECASE flag, so it is put back inline -- without it the purge would be case-sensitive while the
    guard that decides whether it worked is not."""
    rx = FP.needle_regex(value)
    return None if rx is None else '(?i)' + rx.pattern


def rules():
    """git-filter-repo --replace-text lines, longest value first so a full address is replaced before
    its own mailbox name. Every rule is the SAME tolerant matcher the guard uses, so anything the guard
    would flag in a page is something this removes from history (a dot escaped in the local part, a
    value split by regex escapes, odd spacing, any case)."""
    out = []
    for e in emails():
        full = _rx(e)
        if full:
            out.append('regex:%s==>%s' % (full, PLACEHOLDER_EMAIL))
    for e in emails():
        loc = _rx(e.partition('@')[0])
        if loc:
            out.append('regex:%s==>%s' % (loc, PLACEHOLDER_NAME))
    for a in FP._list('retired_addr_prefixes'):
        r = _rx(a)
        if r:
            out.append('regex:%s==>%s' % (r, PLACEHOLDER_ADDR))
    for d in FP._list('deny'):
        r = _rx(d)
        if r:
            out.append('regex:%s==>%s' % (r, PLACEHOLDER_TEXT))
    return out


def email_callback(extra):
    olds = sorted(set(emails()) | {x.lower() for x in extra})
    import publish_site
    new = publish_site.SITE_GIT_EMAIL
    return ('return %r if email.lower() in %r else email'
            % (new.encode(), set(o.encode() for o in olds)))


def plan(args):
    if not (emails() or FP._list('retired_addr_prefixes') or FP._list('deny')):
        raise SystemExit('purge_history: footprint.local.json lists nothing to remove.')
    n = run(['git', 'rev-list', '--count', '--all']).stdout.strip()
    print('commits in this repo: %s%s' % (n, '  (SHALLOW clone: counts are partial)' if
          run(['git', 'rev-parse', '--is-shallow-repository']).stdout.strip() == 'true' else ''))
    wanted = set(emails()) | {x.lower() for x in args.map_email}
    for who, fmt in (('authored', '%ae'), ('committed', '%ce')):
        c = {}
        for e in run(['git', 'log', '--all', '--format=' + fmt]).stdout.split():
            if e.lower() in wanted:
                c[e.lower()] = c.get(e.lower(), 0) + 1
        for e, k in sorted(c.items()):
            print('  %-9s %6d commit(s) by %s***' % (who, k, e[:2]))
    print('replacement rules that would be applied: %d (values never printed)' % len(rules()))
    print('\nRun with --run <empty dir> to rewrite a mirror clone. Nothing here changes this repo.')
    return 0


def rewrite(args):
    work = os.path.abspath(args.run)
    if os.path.exists(work) and os.listdir(work):
        raise SystemExit('purge_history: %s is not empty. Name an empty or new directory.' % work)
    origin = args.origin or run(['git', 'remote', 'get-url', 'origin']).stdout.strip()
    print('cloning --mirror %s -> %s ...' % (origin, work))
    run(['git', 'clone', '--mirror', origin, work])
    n0 = run(['git', 'rev-list', '--count', '--all'], cwd=work).stdout.strip()
    fd, rf = tempfile.mkstemp(prefix='purge_rules_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(rules()) + '\n')
        os.chmod(rf, 0o600)
        print('rewriting %s commit(s) ...' % n0)
        run(['git', 'filter-repo', '--force', '--replace-text', rf, '--replace-message', rf,
             '--email-callback', email_callback(args.map_email)], cwd=work)
    finally:
        try:
            os.remove(rf)
        except OSError:
            pass
    n1 = run(['git', 'rev-list', '--count', '--all'], cwd=work).stdout.strip()
    print('rewritten: %s -> %s commit(s)' % (n0, n1))

    bad = []
    wanted = set(emails()) | {x.lower() for x in args.map_email}
    for fmt in ('%ae', '%ce'):
        left = {e.lower() for e in run(['git', 'log', '--all', '--format=' + fmt], cwd=work).stdout.split()} & wanted
        if left:
            bad.append('%d author/committer email(s) still present' % len(left))
    heads = run(['git', 'for-each-ref', '--format=%(refname)'], cwd=work).stdout.split()
    needles = [(e, FP.needle_regex(e)) for e in emails()] + \
              [(a, FP.needle_regex(a)) for a in FP._list('retired_addr_prefixes') + FP._list('deny')]
    for ref in heads:
        if not ref.startswith(('refs/heads/', 'refs/tags/')):
            continue
        tree = run(['git', 'ls-tree', '-r', '--name-only', ref], cwd=work).stdout.split('\n')
        for path in tree:
            if not path or path.startswith('docs/img/'):
                continue
            blob = subprocess.run(['git', 'show', '%s:%s' % (ref, path)], cwd=work, capture_output=True).stdout
            if len(blob) > 60_000_000 or b'\0' in blob[:4096]:
                continue
            text = re.sub(r'[A-Za-z0-9+/]{400,}={0,2}', '<enc>', blob.decode('utf-8', 'replace'))
            for label, rx in needles:
                if rx is not None and rx.search(text):
                    bad.append('%s:%s still contains a value (%s***)' % (ref, path, label[:2]))
    if args.deep:
        print('deep verify: scanning EVERY commit (slow)...')
        for label, rx in needles:
            if rx is None:
                continue
            c = run(['git', 'log', '--all', '--oneline', '-G', rx.pattern], cwd=work, check=False).stdout.strip()
            if c:
                bad.append('history still contains %s*** in %d commit(s)' % (label[:2], len(c.splitlines())))
    if bad:
        print('\nVERIFY FAILED -- do NOT push:')
        for b in bad[:20]:
            print('  - ' + b)
        return 1
    print('verify: no author/committer email and no value in the tip of any branch or tag%s.'
          % (' or any commit' if args.deep else ' (add --deep to scan every commit)'))
    print('\nREVIEW, THEN (this is destructive; read BEFORE YOU PUSH in --help first):\n'
          '    git -C "%s" remote add origin %s\n'
          '    git -C "%s" push --force --mirror origin\n' % (work, origin, work))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split('\n\n', 1)[1])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--plan', action='store_true', help='read-only report for this repo')
    g.add_argument('--run', metavar='DIR', help='mirror-clone into DIR (empty), rewrite, verify; never pushes')
    ap.add_argument('--origin', default='', help='clone URL (default: this repo\'s origin)')
    ap.add_argument('--map-email', action='append', default=[], metavar='ADDR',
                    help='also map this author/committer email to the noreply identity (repeatable)')
    ap.add_argument('--deep', action='store_true', help='after rewriting, scan every commit for every value (slow)')
    a = ap.parse_args()
    return plan(a) if a.plan else rewrite(a)


if __name__ == '__main__':
    sys.exit(main())
