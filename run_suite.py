"""Run every regression suite in this repo, SERIALLY, and report one summary line.

WHY SERIAL (2026-08-27): each suite launches its own browser. Run back-to-back with no gap while
the nightly is going (Camoufox + parallel captcha workers, ~50 browser/python processes), healthy
tests time out and report as FAILURES — a full run showed 6 reds that every passed individually
minutes later. A suite you cannot trust is worse than no suite: it trains you to ignore red.
The 2s gap lets each browser tear down before the next launches.

    python run_suite.py          # 53 suites, ~25 min

A TIMEOUT is reported distinctly from an assertion failure, because they mean different things:
an assertion failure is a defect, a timeout is usually this machine being busy. Re-run that one
suite alone before believing it.
"""
import glob, subprocess, sys, os, time
tests=sorted(set(glob.glob('_*test.py')+glob.glob('_workerui.py')))
oks=[]; fails=[]
for t in tests:
    try:
        r=subprocess.run([sys.executable,t], capture_output=True, text=True, encoding='utf-8',
                         errors='replace', timeout=240, env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        if r.returncode==0: oks.append(t); print('PASS', t, flush=True)
        else:
            fails.append(t); print('FAIL', t, flush=True)
            for ln in (r.stdout or '').splitlines():
                if 'FAIL' in ln: print('    ', ln.strip()[:140], flush=True)
    except subprocess.TimeoutExpired:
        fails.append(t); print('FAIL', t, '(TIMEOUT — suspect machine load, re-run alone)', flush=True)
    except Exception as ex:
        fails.append(t); print('FAIL', t, str(ex)[:70], flush=True)
    time.sleep(2)   # let each browser fully tear down before the next launches
print('\nSUMMARY: %d pass / %d fail (of %d)' % (len(oks),len(fails),len(tests)), flush=True)
print('FAILED:', ' '.join(fails) or '(none)', flush=True)
