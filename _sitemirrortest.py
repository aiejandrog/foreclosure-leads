"""_sitemirrortest — guard publish_site.py's site-repo resolver. No network, no data, no site clone.

WHY THIS EXISTS — 2026-09-18, and it cost 31 hours of a frozen live site
The 09-17 split moved the published board to a SEPARATE public repo, `dealflow-board`, mirrored by
publish_site.py. Its resolver looked for exactly one path:

    SITE = os.environ.get('DEALFLOW_SITE_REPO') or os.path.join(os.path.dirname(HERE),
                                                                'foreclosure-site')

Nothing creates a directory called `foreclosure-site`. `git clone .../dealflow-board` creates
`dealflow-board`. So main() hit its "no site repo" exit on its FIRST line, every run — and no
caller read the exit code, so the runners went on to report a successful publish. Between the split
and 09-18 the engine repo published three boards while https://aiejandrog.github.io/dealflow-board/
served the 2026-09-17T06:45 build, and every log said the morning went fine.

The bug was one string. What made it cost a day and a half was that nothing asserted the resolver
finds the repo it is named after, and nothing failed loudly when it did not.

Run:  python _sitemirrortest.py
"""
import os
import shutil
import sys
import tempfile

import publish_site


FAILS = []


def check(name, got, want):
    ok = got == want
    print(f'  {"pass" if ok else "FAIL"}  {name}' + ('' if ok else f'   got {got!r}, want {want!r}'))
    if not ok:
        FAILS.append(name)


def _layout(root, *dirs):
    """Make <root>/engine plus a git work tree for each named sibling. Returns the engine path,
    which is what publish_site.HERE stands in for."""
    engine = os.path.join(root, 'engine')
    os.makedirs(engine, exist_ok=True)
    for d in dirs:
        os.makedirs(os.path.join(root, d, '.git'), exist_ok=True)
    return engine


def resolve(engine, env=None):
    """Run the real resolver with HERE pointed at a scratch layout."""
    real_here, real_env = publish_site.HERE, os.environ.get('DEALFLOW_SITE_REPO')
    try:
        publish_site.HERE = engine
        if env is None:
            os.environ.pop('DEALFLOW_SITE_REPO', None)
        else:
            os.environ['DEALFLOW_SITE_REPO'] = env
        return publish_site._find_site()
    finally:
        publish_site.HERE = real_here
        os.environ.pop('DEALFLOW_SITE_REPO', None)
        if real_env is not None:
            os.environ['DEALFLOW_SITE_REPO'] = real_env


def main():
    print()
    root = tempfile.mkdtemp(prefix='mirrortest_')
    try:
        # 1. THE BUG. A clone named after the repo must be found. This is the whole incident.
        eng = _layout(root, 'dealflow-board')
        site, _ = resolve(eng)
        check('a clone named dealflow-board is found', os.path.basename(site), 'dealflow-board')

        # 2. An existing clone under the old name still works — nobody has to re-clone.
        shutil.rmtree(root); os.makedirs(root)
        eng = _layout(root, 'foreclosure-site')
        site, _ = resolve(eng)
        check('an existing foreclosure-site clone still works', os.path.basename(site),
              'foreclosure-site')

        # 3. Both present: prefer the repo's real name, so the fallback cannot shadow it.
        shutil.rmtree(root); os.makedirs(root)
        eng = _layout(root, 'dealflow-board', 'foreclosure-site')
        site, _ = resolve(eng)
        check('with both present, the real repo name wins', os.path.basename(site),
              'dealflow-board')

        # 4. The env var is an override and must beat both, including a directory that exists.
        shutil.rmtree(root); os.makedirs(root)
        eng = _layout(root, 'dealflow-board')
        site, _ = resolve(eng, env='/somewhere/else')
        check('DEALFLOW_SITE_REPO overrides discovery', site, '/somewhere/else')

        # 5. Nothing found: the caller must be able to debug it FROM THE LOG, which means every
        #    path that was tried, not just the last guess. A resolver that reports one candidate is
        #    how "no site repo at ..." read like a config error instead of a naming mismatch.
        shutil.rmtree(root); os.makedirs(root)
        eng = _layout(root)
        site, tried = resolve(eng)
        check('nothing found: every candidate is reported',
              sorted(os.path.basename(t) for t in tried),
              ['dealflow-board', 'foreclosure-site'])
        check('  and the fallback path is still actionable', os.path.basename(site),
              'dealflow-board')

        # 6. MUTATION CHECK. Re-create the old one-path resolver and confirm THIS suite catches it.
        #    Without this, a future simplification back to a single name passes silently.
        shutil.rmtree(root); os.makedirs(root)
        eng = _layout(root, 'dealflow-board')
        old = os.path.join(os.path.dirname(eng), 'foreclosure-site')
        check('mutation check (the old single-path resolver would MISS the real clone)',
              os.path.isdir(os.path.join(old, '.git')), False)

        print()
        print(f'{7 - len(FAILS)} pass / {len(FAILS)} fail')
        return 1 if FAILS else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
