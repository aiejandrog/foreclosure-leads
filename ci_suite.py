"""ci_suite.py - the regression suites that can run on a clean Linux checkout, for GitHub Actions.

WHY A SEPARATE RUNNER (2026-09-26). run_suite.py runs every suite on the armed PC, where the
gitignored inputs exist (site.codes, leads_final.json, optouts.json, the Playwright/Camoufox
browsers). A fresh clone on a GitHub runner has none of them - by design, because they are homeowner
data or secrets and this repo is public. Run as-is there, 38 of 119 suites fail for that reason
alone, and a CI that is red on main every day teaches everyone to ignore red. So this file runs
EVERY tracked suite EXCEPT the ones named in SKIP below, each with the reason it cannot run in CI.

The list is an explicit exclusion list, not an inclusion list, on purpose: a NEW suite is picked up
and must pass in CI unless someone adds it here with a reason. See CI-TESTS.md for the categories.

    python ci_suite.py            # what the workflow runs; exit 1 on any failure
    python ci_suite.py --list     # what would run, and what is skipped and why
    python ci_suite.py --skipped  # ALSO try the skipped ones and report them (never fails the run)

Serial, like run_suite.py, and for the same reason: several suites start local servers or browsers.
Needs no secrets and no network beyond what the suites themselves stub.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 240

BROWSER = 'browser: drives the board in Playwright Chromium (and most need a real built board too)'
CODES = 'live data: needs the gitignored site.codes / leads_final.json (the encrypted board inputs)'
LEDGER = 'live data: needs a real optouts.json - send_server fails CLOSED without one, so every /send is refused'
FEED = 'live data: reads the real lead feed / replies / board on disk'
WINDOWS = 'machine: hard-codes the laptop path C:\\Users\\olqbb\\...'

SKIP = {
    # --- browser ---
    '_2165test.py': BROWSER, '_balloonlanetest.py': BROWSER, '_callmodetest.py': BROWSER,
    '_casestatustest.py': BROWSER, '_codelientest.py': BROWSER, '_cyberaddrtest.py': BROWSER,
    '_deedtest.py': BROWSER, '_dnctest.py': BROWSER, '_docroomtest.py': BROWSER,
    '_emailtest.py': BROWSER, '_fieldsheettest.py': BROWSER, '_funneltest.py': BROWSER,
    '_gallerytest.py': BROWSER, '_lanetest.py': BROWSER, '_mirrortest.py': BROWSER,
    '_nearmetest.py': BROWSER, '_optouttest.py': BROWSER, '_plantest.py': BROWSER,
    '_portfoliotest.py': BROWSER, '_redfintest.py': BROWSER, '_senderdefaulttest.py': BROWSER,
    '_stalefixtest.py': BROWSER, '_taxtest.py': BROWSER, '_workeremailtest.py': BROWSER,
    '_workerui.py': BROWSER,
    # --- live data ---
    '_cstest.py': CODES, '_eq30test.py': CODES, '_filtertest.py': CODES, '_gatetest.py': CODES,
    '_hangertest.py': CODES, '_phonepagetest.py': CODES,
    '_suppressiontest.py': CODES + ' (17 of its 19 checks do pass in CI; the board/worker gate needs site.codes)',
    '_attachtest.py': LEDGER, '_ledgerwritetest.py': LEDGER, '_sendbridgetest.py': LEDGER,
    '_digesttest.py': FEED, '_dupetest.py': FEED,
    # --- machine ---
    '_lktest.py': WINDOWS,
}


def tracked_suites():
    """Tracked files only - an untracked scratch probe on a laptop must never decide CI."""
    try:
        out = subprocess.run(['git', 'ls-files'], cwd=HERE, capture_output=True, text=True, check=True).stdout
        files = out.split()
    except Exception:
        files = os.listdir(HERE)
    py = sorted(f for f in files if '/' not in f and f.endswith('.py') and
                ((f.startswith('_') and f.endswith('test.py')) or f.startswith('test_') or f == '_workerui.py'))
    js = sorted(f for f in files if '/' not in f and f.startswith('_') and f.endswith('test.js'))
    return py + js


def run_one(t):
    cmd = ['node', t] if t.endswith('.js') else [sys.executable, t]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=TIMEOUT, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        return p.returncode, (p.stdout or '') + (p.stderr or ''), time.time() - t0
    except subprocess.TimeoutExpired as e:
        return 'TIMEOUT', str(e.stdout or '')[-2000:], time.time() - t0


def main(argv):
    suites = tracked_suites()
    skipped = [t for t in suites if t in SKIP]
    run = [t for t in suites if t not in SKIP]
    gone = sorted(set(SKIP) - set(suites))
    if '--list' in argv:
        print('RUN (%d):' % len(run))
        for t in run:
            print('   ', t)
        print('SKIPPED (%d):' % len(skipped))
        for t in skipped:
            print('    %-28s %s' % (t, SKIP[t]))
        if gone:
            print('SKIP entries for suites that no longer exist (remove them):', ' '.join(gone))
        return 0
    fails = []
    for t in run:
        rc, out, secs = run_one(t)
        if rc == 0:
            print('PASS %-32s %5.1fs' % (t, secs), flush=True)
        else:
            fails.append(t)
            print('FAIL %-32s %5.1fs  rc=%s' % (t, secs, rc), flush=True)
            lines = [l for l in out.splitlines() if l.strip()]
            for l in [l for l in lines if 'FAIL' in l][:15] or lines[-15:]:
                print('       ' + l[:200], flush=True)
    if '--skipped' in argv:
        print('\n--- skipped suites, tried anyway (informational, never fails the run) ---')
        for t in skipped:
            rc, _, secs = run_one(t)
            print('%s %-32s %5.1fs  (%s)' % ('pass' if rc == 0 else 'fail', t, secs, SKIP[t].split(':')[0]))
    if gone:
        print('\nnote: SKIP names suites that no longer exist - remove them from ci_suite.py:', ' '.join(gone))
    print('\nCI SUMMARY: %d pass / %d fail, %d skipped (see CI-TESTS.md)' % (len(run) - len(fails), len(fails), len(skipped)))
    if fails:
        print('FAILED:', ' '.join(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
