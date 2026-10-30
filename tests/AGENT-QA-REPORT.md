# Agent team QA report

QA gate for the round-2 agent team. Verdicts are appended below the baseline, newest last.
Repo is public: counts, case ids, commands and suite names only.

## Q0 Baseline (origin/main)

- Date: 2026-10-29. Base SHA: b829b54cbedfa45cad5c3db81cf23d3ab3947cef (origin/main, worktree fl-qa).
- Machine: Windows 11, Python 3.11.7, Git Bash. No Chromium/Playwright browsers used by ci_suite.
- Command (bash, in fl-qa): `DEALFLOW_DIR=$(mktemp -d) python ci_suite.py > ci.log 2>&1`
- Result: **CI SUMMARY: 223 pass / 12 fail, 39 skipped** (skips = the SKIP table in ci_suite.py, CI-TESTS.md).

### The 12 failures that already exist on main

| suite | cause on this Windows box | cured by PYTHONUTF8=1? |
|---|---|---|
| _ballooncurrencytest.py | cp1252 UnicodeDecodeError reading a file | yes (rc=0) |
| _callworkflowparitytest.py | Node/Python canonical bytes differ under cp1252 | yes (rc=0) |
| _uifixturetest.py | cp1252 UnicodeDecodeError | yes (rc=0) |
| _workerrunlogtest.py | cp1252 UnicodeDecodeError | yes (rc=0) |
| _openertest.py | Spanish dated opener text under cp1252 | yes (rc=0) |
| _texttpltest.py | "sale tomorrow says tomorrow" (1 of 74) | yes (rc=0) |
| _judgmentpasstest.py | `NotImplementedError: cannot instantiate 'PosixPath' on your system` (test bug on Windows) | no |
| _lpcoveragetest.py | `test_whole_miami_sweep_is_off_by_default_and_cuts_to_kinds`: "a recurring paid read stays off until Alex enables it" asserts False but got True (env var present on this box, likely) | no |
| _syncgatetest.py | sync-gate scenarios, rc=1 on this box | no |
| _tracerfytest.py | 3 FAIL lines ("400 credits warns", "last run was refused but the account can pay now"), 7d-old / balance wording | no |
| _resimplisynctest.py | `could not write (FileExistsError/PermissionError/OSError)` 87s, Windows file semantics | not rerun |
| _doccollecttest.py | TIMEOUT at the 240s ci_suite cap | not rerun |

**Gate rule used from here:** run suites with `PYTHONUTF8=1 DEALFLOW_DIR=<temp>`. A branch is
compared against the table above: the same suites failing for the same reason are "existing on
main"; any other failing suite (or a new failure line inside one of these) is a new failure = FAIL.
The baseline with PYTHONUTF8=1 across the full suite has not been rerun; only the 8 UTF-8-sensitive
suites were rerun individually (6 pass, _syncgatetest and _tracerfytest still fail).

### Network / real ~\DEALFLOW

- ci_suite itself makes no network calls I could observe; the suites that import urlopen/requests
  (grep: _attachtest, _bridgereloadtest, _firsttouchtest, _hcpingtest, _ledgerwritetest, _lktest,
  _notesbridgetest, _pacerstaytest, _publishwatchtest, _sendbridgetest, _sendstaytest, _sheetscrmtest,
  _siblingoptouttest, _sunbizblocktest, _suppressionfixtest, _syncgatetest, _textprecheckstest,
  _texttripwiretest, _workerrunlogtest) are local-port or stubbed. Not proven by packet capture.
- Real `~\DEALFLOW`: two files changed during/after the run: `BSG_CRM.csv` and
  `refresh_watch.alive`, both stamped 20:06 after ci_suite had finished. `refresh_watch.alive` is the
  refresh watcher heartbeat and `BSG_CRM.csv` is the CRM export, so a live refresh on the main
  checkout is the likely writer, not a suite. Not attributable with certainty.

### call_objections.json shows modified in every worktree

`call_mode.load_objections()` rewrites `call_objections.json` (call_mode.py line ~603,
`json.dump(..., open(_DRILL_CACHE, 'w'))`). On Windows text mode writes CRLF, so the tracked LF file
shows modified, with no content change (`git diff -w` is empty). Confirmed trigger: `_seattest.py`
(running it alone dirties the file); `_callrankingtest`, `_queueviewtest`, `_openertest` did not.
Any suite whose path reaches `load_objections` with a vault present may do it. Never commit it; run
`git checkout call_objections.json` after a suite run.

## Verdicts
