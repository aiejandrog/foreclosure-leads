#!/usr/bin/env python
"""site_fresh.py -- replace the PUBLIC site repo's history with ONE fresh commit.

WHY
Every commit in the public site repo (aiejandrog/dealflow-board) shows its author and committer
email, and every old build of the board sits in its history. For months that meant the operator's
personal mailbox on ~every commit, and pages carrying identifiers that are now sealed. Fixing the
source and the next build removes them from the CURRENT page; only a history reset removes them from
the repo. The site repo holds nothing but built pages, so a reset loses nothing that a rebuild does
not recreate.

ORDER (do not skip a step -- the guards below enforce the ones they can)
  1. merge the footprint PR, let the nightly rebuild + publish with the new code
  2. python check_public_footprint.py            -> ZERO hits on the live pages
  3. python site_fresh.py                        -> DRY RUN: builds the single commit, runs every guard
  4. python site_fresh.py --push                 -> force-pushes it, resets this machine's site clone
Doing step 4 before step 1 would publish today's leaky build as the one and only commit.

WHAT IT DOES
  * runs publish_site.guard() against the engine docs/ about to be published: board encrypted, no
    phone-shaped number, no consumer mailbox, nothing from footprint.local.json outside the payload
  * stages README.md / .gitattributes / docs/.nojekyll from the site repo's HEAD, plus a byte-for-byte
    copy of docs/ -- WITHOUT *.pdf unless --keep-pdfs. PDFs are not built pages: the door books that
    reached the site repo are gitignored in the engine and ride in only because the mirror copies
    whatever sits in the local docs/ folder (their phone numbers are never scanned: guard() reads
    .html only). They are listed so you decide, not dropped silently.
  * makes ONE commit with the GitHub noreply identity (publish_site.SITE_GIT_*), in a throwaway repo
  * --push: `git push --force` that commit over main, then resets THIS machine's site clone to it so
    the next nightly publish is a fast-forward and not "PUSH REJECTED"

GitHub keeps unreachable commits viewable by SHA until it garbage-collects, and a fork keeps its own
copy. For a hard purge, delete and recreate the repo instead (commands are printed), or ask GitHub
Support to expire cached views. Pages needs no re-enabling after a force-push; it does after a
delete + recreate (Settings > Pages > main /docs).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import footprint as _footprint
import publish_site as PS

COMMIT_MSG = 'site: fresh history (single commit)'


def git(args, cwd, env=None, check=True):
    p = subprocess.run(['git'] + list(args), cwd=cwd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', env=env)
    if check and p.returncode:
        raise SystemExit('site_fresh: git %s failed (%d)\n%s%s' % (' '.join(args), p.returncode, p.stdout, p.stderr))
    return p


def stage(keep_pdfs):
    """Build the single-commit repo in a temp dir. Returns (dir, copied, excluded)."""
    tmp = tempfile.mkdtemp(prefix='site_fresh_')
    # root files and .nojekyll come from the site repo's own HEAD, never from the engine
    for rel in ('README.md', '.gitattributes', 'docs/.nojekyll'):
        r = git(['show', 'HEAD:' + rel], PS.SITE, check=False)
        if r.returncode == 0:
            dst = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'w', encoding='utf-8', newline='') as f:
                f.write(r.stdout)
    copied, excluded = 0, []
    for src in PS._pages():
        rel = os.path.relpath(src, PS.DOCS)
        if rel.lower().endswith('.pdf') and not keep_pdfs:
            excluded.append(rel.replace(os.sep, '/'))
            continue
        dst = os.path.join(tmp, 'docs', rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    return tmp, copied, excluded


def verify(tmp):
    """The staged repo may hold the built site and nothing else, and every page must be clean."""
    tracked = git(['ls-files'], tmp).stdout.splitlines()
    stray = sorted({p.split('/', 1)[0] for p in tracked if p.strip()} - PS.ALLOWED_TOP)
    if stray:
        raise SystemExit('site_fresh: the staged commit tracks non-site paths: %s' % ', '.join(stray[:6]))
    ids = git(['log', '--format=%ae%n%ce'], tmp).stdout.split()
    bad = [e for e in ids if not e.endswith('@users.noreply.github.com')]
    if bad:
        raise SystemExit('site_fresh: the commit identity is not a noreply address (%d).' % len(bad))
    import re
    for rel in tracked:
        if rel.lower().endswith('.html'):
            s = open(os.path.join(tmp, rel), encoding='utf-8', errors='replace').read()
            plain = re.sub(r'[A-Za-z0-9+/]{400,}={0,2}', '<enc>', s)
            p = _footprint.problems(plain)
            if p:
                raise SystemExit('site_fresh: %s still carries a personal identifier (%s). Rebuild first.'
                                 % (rel, '; '.join(p[:3])))
    return tracked


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--push', action='store_true', help='force-push the fresh commit over main (destructive)')
    ap.add_argument('--yes', action='store_true', help='skip the interactive confirmation for --push')
    ap.add_argument('--keep-pdfs', action='store_true', help='include *.pdf found under docs/')
    a = ap.parse_args()

    PS.tracked()
    PS.guard()
    old = git(['rev-list', '--count', 'HEAD'], PS.SITE).stdout.strip()
    tmp, copied, excluded = stage(a.keep_pdfs)
    try:
        git(['init', '-q'], tmp)
        git(['checkout', '-q', '-B', 'main'], tmp)
        env = PS._site_env()
        git(['add', '-A'], tmp)
        git(['-c', 'user.name=' + PS.SITE_GIT_NAME, '-c', 'user.email=' + PS.SITE_GIT_EMAIL,
             'commit', '-q', '-m', COMMIT_MSG], tmp, env=env)
        tracked = verify(tmp)
        size = sum(os.path.getsize(os.path.join(tmp, p)) for p in tracked if os.path.exists(os.path.join(tmp, p)))
        print('site_fresh: staged ONE commit, %d file(s), %.1f MB, identity %s' %
              (len(tracked), size / 1048576.0, PS.SITE_GIT_EMAIL))
        print('site_fresh: the site repo currently has %s commit(s) on this clone' % old)
        if excluded:
            print('site_fresh: %d PDF(s) NOT carried over (not built pages; pass --keep-pdfs to include):' % len(excluded))
            for e in excluded:
                print('    ' + e)
        if not a.push:
            print('site_fresh: DRY RUN -- nothing pushed. Re-run with --push to rewrite the public history.')
            return 0
        if not a.yes:
            print('\nThis FORCE-PUSHES over the public site repo and discards its %s commit(s) of history.' % old)
            if input('Type the word REWRITE to continue: ').strip() != 'REWRITE':
                print('site_fresh: aborted.')
                return 1
        url = git(['remote', 'get-url', 'origin'], PS.SITE).stdout.strip()
        git(['remote', 'add', 'origin', url], tmp)
        git(['push', '--force', 'origin', 'main'], tmp)
        git(['fetch', '-q', 'origin', 'main'], PS.SITE)
        git(['checkout', '-q', 'main'], PS.SITE, check=False)
        git(['reset', '-q', '--hard', 'origin/main'], PS.SITE)
        print('site_fresh: pushed, and %s reset to origin/main -- the next nightly publish fast-forwards.' % PS.SITE)
        print('\nTo expire GitHub\'s cached copies of the old commits too, either contact GitHub Support or\n'
              'delete and recreate the repo:\n'
              '    gh repo delete aiejandrog/dealflow-board --yes\n'
              '    gh repo create aiejandrog/dealflow-board --public\n'
              '    (then re-run this script with --push, and Settings > Pages > main /docs)')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
