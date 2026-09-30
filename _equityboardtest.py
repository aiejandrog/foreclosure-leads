#!/usr/bin/env python3
"""_equityboardtest.py -- the browser half of _equityhonestytest.py (2026-09-30).

Drives the board template in headless Chromium on synthetic rows: "eq unknown" for a missing
judgment and a lis pendens, the "<=" ceiling for unverified liens, the 30%+ equity filter, the
cure block and deal paths. CI has no browser, so this suite is in ci_suite.py's BROWSER skip
list; the Python half (classify, interest puller, bake, brief, Call Mode, call list) runs in CI.
"""
import sys
import _equityhonestytest as T

if __name__ == '__main__':
    T.board_suite()
    print('\n%d checks, %d failed' % (len(T.CHECKS), len(T.FAILS)))
    sys.exit(1 if T.FAILS else 0)
