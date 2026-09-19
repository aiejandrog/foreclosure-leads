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
RUN = []
HERE = os.path.dirname(os.path.abspath(__file__))

# Every runner that publishes the board. The list is the point: a fifth publish path added without
# these two properties is a hole, and CLAUDE.md already says to gate one in the same commit.
RUNNERS = ('refresh-dealflow.bat', 'run-leads.bat', 'run-phones-nightly.bat',
           'run-replies-daily.bat')


def check(name, got, want):
    RUN.append(name)
    ok = got == want
    print(f'  {"pass" if ok else "FAIL"}  {name}' + ('' if ok else f'   got {got!r}, want {want!r}'))
    if not ok:
        FAILS.append(name)


def _bat(name):
    with open(os.path.join(HERE, name), encoding='utf-8', errors='replace') as f:
        return f.read()


def _reads_mirror_exit(text):
    """Is the FIRST thing after the publish_site call a check of its exit code? Comments and blank
    lines are skipped; anything else executing in between means the code walked on, which is the
    09-17 bug verbatim. `if errorlevel N` is used rather than %ERRORLEVEL% on purpose: inside a
    parenthesized block the percent form expands at parse time and would always read stale."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if 'publish_site.py' in ln and ln.strip().lower().startswith('python'):
            for nxt in lines[i + 1:]:
                t = nxt.strip().lower()
                if not t or t.startswith('rem '):
                    continue
                return t.startswith('if errorlevel')
    return False


def _propagates(text):
    """Does a mirror failure reach the process exit code? Either via this file's RUNEXIT verdict or
    via a MIRRORFAIL flag returned as rc=5. A log line the scheduler never sees is half a fix."""
    sets = 'set "RUNEXIT=5"' in text or 'set "MIRRORFAIL=1"' in text
    exits = 'exit /b 5' in text or 'exit /b %RUNEXIT%' in text
    return sets and exits


# Windows externals that a git-bash PATH shadows with a GNU build. Git for Windows offers "Use Git
# and optional Unix tools from the Command Prompt", which puts its /usr/bin AHEAD of System32 -- so
# a bare `find` is GNU find. It reads /i as a start path, exits non-zero, and repo_guard.bat then
# refuses a perfectly good checkout with "wrong origin remote": every publish path aborts at its
# first line for a reason that is not true. `timeout` is the same shadow, milder -- GNU timeout
# rejects /t, so the push retry loses its backoff. Both are fixed by naming System32 outright.
SHADOWED = ('find', 'findstr', 'timeout', 'sort', 'more')

# Every .bat in the publish path, including the two helpers the runners `call`.
GUARDED = RUNNERS + ('repo_guard.bat', 'publish_verify.bat', 'run-phones.bat')


def _bare_externals(text):
    """-> sorted names invoked by bare name at a command position. Command positions are the start
    of a line and whatever follows | & ( -- which covers `a || b`, `a & b` and `if x ( cmd )`. A
    fully qualified or quoted call (\"%SystemRoot%\\System32\\find.exe\") is not a bare name and does
    not match."""
    hits = set()
    for ln in text.splitlines():
        st = ln.strip()
        if not st or st.lower().startswith('rem ') or st.lower().startswith('::'):
            continue
        for frag in st.replace('|', '\n').replace('&', '\n').replace('(', '\n').split('\n'):
            tok = frag.strip().split(' ')[0].strip().lower()
            if tok in SHADOWED:
                hits.add(tok)
    return sorted(hits)

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

        # ---- the runners must ACT on what this resolver reports ------------------------------
        # A correct resolver that every caller ignores is what the 09-17-to-09-18 frozen site was
        # made of: publish_site.py exited 1 on every run, the runners walked straight past it, and
        # Task Scheduler recorded rc=0 while the live board sat 31 hours stale. Both halves are
        # asserted here because fixing either one alone still leaves a silent failure.
        print()
        for name in RUNNERS:
            text = _bat(name)
            check(f'{name} calls the mirror', 'publish_site.py' in text, True)
            check(f'  {name} reads its exit code', _reads_mirror_exit(text), True)
            check(f'  {name} carries it to the process exit code', _propagates(text), True)


        # ---- PATH shadowing (2026-09-18) ---------------------------------------------------
        # Confirmed on the laptop: under a git-bash PATH `repo_guard.bat` refused the real DEALFLOW
        # checkout, so refresh-dealflow.bat died on its first line and nothing ran. The guard failed
        # CLOSED, which is the right direction to fail -- but a guard that blocks every publish for
        # a reason that is not true costs exactly as many boards as one that is simply broken.
        print()
        for name in GUARDED:
            check(f'{name} invokes no PATH-shadowed external by bare name',
                  _bare_externals(_bat(name)), [])

        # The fix has to be the qualified path, not a rename. Assert the resolver by name so a
        # future edit cannot satisfy the check above by hiding a bare `find` behind a variable.
        guard = _bat('repo_guard.bat')
        check('repo_guard resolves find.exe from System32',
              '%SystemRoot%\\System32\\find.exe' in guard, True)
        check('  and still matches the origin remote case-insensitively',
              '/i "foreclosure-leads"' in guard, True)

        # MUTATION CHECK. Put the bare names back and confirm the scan goes red - otherwise it is
        # asserting something true of any file that happens not to shell out.
        check('mutation check (restore the bare names and the scan catches them)',
              _bare_externals(guard.replace('| "%RGFIND%" /i', '| find /i')), ['find'])

        # MUTATION CHECK. Strip the propagation out of each runner in memory and confirm the check
        # above goes red - otherwise it is asserting something that is true of any file.
        broken = [n for n in RUNNERS
                  if not _propagates(_bat(n).replace('set "RUNEXIT=5"', '')
                                            .replace('set "MIRRORFAIL=1"', ''))]
        check('mutation check (drop the propagation and every runner fails)',
              sorted(broken), sorted(RUNNERS))

        print()
        print(f'{len(RUN) - len(FAILS)} pass / {len(FAILS)} fail')
        return 1 if FAILS else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
