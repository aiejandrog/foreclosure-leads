"""Publish docs/ to the PUBLIC site repo (aiejandrog/dealflow-board).

WHY THIS EXISTS
Until 2026-09-17 this repository was public, and it carries far more than the pages it serves:
homeowner names, addresses and skip-traced phone numbers sit in committed data files
(pre_foreclosure_doors.json, lis_pendens.json, ownership.json, diligence_cache.json, ...) and in
every commit of history behind them. GitHub Pages cannot serve a private repo on the Free plan, so
the engine and the site were split instead of paying for it:

    foreclosure-leads PRIVATE  — code, data, history, and docs/ as built (publish_guard's baseline)
    dealflow-board    PUBLIC   — nothing but the built pages

The public repo deliberately does NOT reuse the name `foreclosure-leads`: every older clone of the
engine (the laptop, an old checkout) still pushes to that name, and GitHub keeps redirecting a
renamed repo's old name for a while — on 2026-09-17 that redirect is exactly what fed engine history
into a public repo. The engine keeps the old name so those clones stay harmless; the site gets a
name nothing else has ever pointed at. The board URL moved to /dealflow-board/ as a result.

This script is the bridge. The nightly builds docs/ exactly as before and commits it to the private
repo; then this mirrors those bytes into the site repo and pushes, which is what GitHub Pages
serves. No source, no lead data and no history ever reach the public side again.

GUARDS (a publish that fails one of these is not worth having)
  * the board must be ENCRYPTED. A build with no site.codes writes a plaintext board -- correct for
    the Desktop twin, a data breach on a public site.
  * no phone-shaped digit run may appear outside the encrypted payload, in any page. This is the
    check that would have caught REPLIES shipping an owner's number in the clear for three weeks.

Usage:  python publish_site.py            (called by refresh-dealflow.bat after each publish)
        python publish_site.py --dry-run  (show what would move, push nothing)
"""
import argparse
import filecmp
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, 'docs')
# WHERE THE SITE CLONE IS, and why this is a list (2026-09-18)
# This read `os.path.join(os.path.dirname(HERE), 'foreclosure-site')` and nothing else. The public
# repo is named dealflow-board, so `git clone .../dealflow-board` beside the engine produces
# `dealflow-board` — a name this file never looked for. It therefore exited on its first line of
# main() with "no site repo", every single run, and since NO runner read its exit code the failure
# was silent: between 2026-09-17 18:20 and 2026-09-18 13:30 the engine repo published three boards
# (09-17 21:08, 09-18 05:41 and one before them) and the live site served the 2026-09-17T06:45 build
# throughout, with every run reporting a successful publish.
# So: try the repo's own name first, keep the old name as a fallback for a clone that already used
# it, and when none of them is a git work tree say EVERY path that was tried — a resolver that
# reports only its last guess cannot be debugged from a log.
SITE_DIRS = ('dealflow-board', 'foreclosure-site')


def _find_site():
    """-> (path, tried). Env var wins outright; otherwise the first sibling that is a git work tree;
    otherwise the first candidate, so the error names something actionable."""
    env = os.environ.get('DEALFLOW_SITE_REPO')
    if env:
        return env, [env]
    parent = os.path.dirname(HERE)
    tried = [os.path.join(parent, n) for n in SITE_DIRS]
    for p in tried:
        if os.path.isdir(os.path.join(p, '.git')):
            return p, tried
    return tried[0], tried


SITE, SITE_TRIED = _find_site()
SITE_DOCS = os.path.join(SITE, 'docs')
KEEP = {'.nojekyll'}          # lives only in the site repo; never mirrored away


def _run(args, cwd, check=True):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and p.returncode:
        raise SystemExit('publish_site: %s failed (%d)\n%s%s' % (' '.join(args), p.returncode, p.stdout, p.stderr))
    return p


def _pages():
    for root, _dirs, files in os.walk(DOCS):
        for n in files:
            yield os.path.join(root, n)


def guard():
    """Refuse to publish anything personal. Runs against the BYTES about to be pushed."""
    board = os.path.join(DOCS, 'index.html')
    if not os.path.exists(board):
        raise SystemExit('publish_site: docs/index.html is missing — nothing to publish.')
    html = open(board, encoding='utf-8', errors='replace').read()
    if not re.search(r'(?m)^const RAW = \{"enc"', html):
        raise SystemExit('publish_site: the board is NOT encrypted (no site.codes at build time?) — '
                         'refusing to publish it to a public repo.')
    # base64 ciphertext is one long unbroken run; strip it so the scan only sees what a reader sees
    for p in sorted(_pages()):
        if not p.lower().endswith('.html'):
            continue
        s = open(p, encoding='utf-8', errors='replace').read()
        plain = re.sub(r'[A-Za-z0-9+/]{400,}={0,2}', '<enc>', s)
        hits = sorted(set(re.findall(r'(?<!\d)(?:786|305|954|561|754)\d{7}(?!\d)', plain)))
        if hits:
            raise SystemExit('publish_site: %s carries %d phone-shaped number(s) outside the encrypted '
                             'payload (e.g. %s) — refusing to publish.'
                             % (os.path.relpath(p, HERE), len(hits), hits[0]))
    return True


ALLOWED_TOP = {'docs', 'README.md', '.gitattributes'}


def tracked():
    """The site repo may contain the built pages and nothing else.

    This is the check that turns a silent disaster into a refused publish: if engine code or lead
    data has found its way into the site clone (a stray branch, a bad merge, someone cloning the
    wrong repo into this path), stop before pushing it to a PUBLIC repo.
    """
    out = _run(['git', 'ls-files'], SITE).stdout.splitlines()
    stray = sorted({p.split('/', 1)[0] for p in out if p.strip()} - ALLOWED_TOP)
    if stray:
        raise SystemExit('publish_site: the site repo tracks %d path(s) that are not the built site '
                         '(%s) — refusing to push it to a public repo.'
                         % (len(stray), ', '.join(stray[:6])))
    return True


def mirror(dry=False):
    """Make SITE/docs match docs/ byte for byte. Returns (copied, removed)."""
    copied, removed = 0, 0
    for src in _pages():
        rel = os.path.relpath(src, DOCS)
        dst = os.path.join(SITE_DOCS, rel)
        if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
            continue
        if not dry:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        copied += 1
    for root, _dirs, files in os.walk(SITE_DOCS):
        for n in files:
            dst = os.path.join(root, n)
            rel = os.path.relpath(dst, SITE_DOCS)
            if rel in KEEP or os.path.basename(rel) in KEEP:
                continue
            if not os.path.exists(os.path.join(DOCS, rel)):
                if not dry:
                    os.remove(dst)
                removed += 1
    return copied, removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if not os.path.isdir(os.path.join(SITE, '.git')):
        raise SystemExit(
            'publish_site: NO SITE REPO FOUND - THE LIVE SITE WAS NOT UPDATED.\n'
            '  tried: %s\n'
            '  The live board is https://github.com/aiejandrog/dealflow-board (GitHub Pages serves\n'
            '  it, not this repo). Clone it beside this one:\n'
            '      git clone https://github.com/aiejandrog/dealflow-board\n'
            '  ...or point DEALFLOW_SITE_REPO at an existing clone. The board that was just built\n'
            '  is committed and safe in this repo either way; it simply is not published yet.'
            % '\n         '.join(SITE_TRIED))
    guard()
    copied, removed = mirror(dry=a.dry_run)
    print('publish_site: %d page(s) changed, %d removed' % (copied, removed))
    if a.dry_run:
        return 0
    if not copied and not removed:
        print('publish_site: site already matches — nothing to push')
        return 0
    tracked()
    _run(['git', 'add', 'docs'], SITE)
    if not _run(['git', 'diff', '--staged', '--quiet'], SITE, check=False).returncode:
        print('publish_site: no staged change — nothing to push')
        return 0
    _run(['git', 'commit', '-m', 'site: publish (auto)'], SITE)
    for attempt in (1, 2):
        if not _run(['git', 'push', 'origin', 'main'], SITE, check=False).returncode:
            print('publish_site: pushed')
            return 0
        time.sleep(6 * attempt)
    # NEVER `pull --rebase` HERE. On 2026-09-17 that line published the whole engine to the public
    # repo: the fetch followed GitHub's rename redirect to the PRIVATE repo, rebased these site
    # commits onto its history, and pushed lead data, scrapers and 1,100 commits into the open. A
    # rejected push means this clone and the site repo disagree about what is published, which is a
    # thing to look at, never a thing to merge. The build is safe in the private repo either way.
    print('publish_site: PUSH REJECTED — the site repo has commits this clone does not. Inspect it '
          'by hand; do NOT pull/rebase engine history into the public repo.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
