LEGAL REVIEW: call mode: warn and retry when a Do Not Contact has not reached the bridge (F-wire-02)

Merge: Alex only (DNC delivery).
Ticket: F-wire-02, page half only (severity S1, APPROVED-FIX #1).

## What this does, in plain English
A Do Not Contact tapped in Call Mode on a phone that cannot reach the bridge was saved on the phone and sent once to `/notes`. If that send failed (no connection, an HTTP error, or an `ok:false` answer) nothing was shown and nothing was retried, so the opt-out might never reach `optouts.json` and nobody would know.

Now Call Mode reads the answer. Failure is a rejected request, a non-2xx status, or `ok:false`. On success it remembers which Do Not Contact ids the bridge has received (`fcDncAck` in phone storage, case ids only) and removes the warning. On failure, if a Do Not Contact is not yet confirmed, a red strip (`notes-unsent-strip`) says "Not backed up to the bridge: N Do Not Contact change(s) have not reached the opt-out list. Keep this page open and get on the bridge network." and the page retries after 4, 8 and 12 seconds (the `queueSync` ladder), then keeps the strip and stops until the next save. If nothing unconfirmed is at stake, behaviour is exactly as before. The local save, `saveNotes`, and the body sent to the bridge are unchanged (byte-identical, pinned by the test).

A Do Not Contact note is one with `no==='hard'`, `status==='DO NOT CONTACT'`, or an `optout` date. The strip counts case ids; the `#phone` person keys that `stopEverywhere` also writes are not counted separately.

Diff scope: `call_mode.py` hunks only inside the notes-push block (`callNotesPushSoon`, `callNotesPush`, and helpers nested inside it so the existing wiring tests that extract those two functions keep working), new `_notespushtest.py`, one appended entry D24 in DECISIONS-NEEDED, this PR body and the run log. No hunk in `stopEverywhere`, `logOutcome`, `noState`, `hardSuppressed`, seat logic, `send_server.py`, `optout_sync.py`, or `docs/call/`.

Known limit (report only, D21): the bridge's `/notes` still answers `ok:true` when its own DNC sweep fails. This page treats that as delivered, so the strip cannot see a ledger failure behind a 2xx. The server half is Alex's call.

## Failing first (unchanged tree, aac7a8a9)
`DEALFLOW_DIR=<temp> python _notespushtest.py` -> 19 failed checks, e.g.
```
FAIL rejects: strip appears with Do Not Contact wording | {'text1': None, 'strip_after': False, 'ms': [], 'left': 0, 'calls': 1, ...}
FAIL rejects: retries at 4000, 8000, 12000 then none
FAIL HTTP 500: strip appears with Do Not Contact wording | {'text1': None, ...}
FAIL 200 with ok:false: retries at 4000, 8000, 12000 then none
FAIL c: strip was up, then removed on 200 ok:true | {'had': False, ...}
FAIL g: ladder spent, then saveNotes -> debounce 4000, then a fresh 4000/8000/12000 | {'spent': 0, 'first': [4000], 'ms': [4000], 'strip': False}
FAILED: 19
```
(b), (e), (f) pass on the unchanged tree, as the ticket expects: they pin "no change" behaviour.

## Tests run (after the fix)
- `DEALFLOW_DIR=<temp> python _notespushtest.py` -> 27 ok, ALL PASS.
- 83 suites that import `call_mode` (every `_*test.py` containing `call_mode`, including `_callwiringtest.py`, `_e2ewiringtest.py`, `_calldncpastetest.py`, `_callwrongnumtest.py`, `_callrelativetest.py`, `_carlostest.py`, `_suppressiontest.py`), serial, a fresh temp `DEALFLOW_DIR` each, on this branch and on `fl-loop-base` (origin/main aac7a8a9), with and without `PYTHONUTF8=1`:
  - UTF8 off: branch fails `_autorunstoptest`, `_carlostest`, `_phonevalidatetest`, `_suppressiontest`, `_texttpltest`, `_wplinktest`; base fails the same six plus `_contactsuptest` (that one passed on the branch and failed on base, so it is flaky or locale dependent, not caused by this change). Failing lines identical on both trees.
  - UTF8 on: branch and base both fail `_carlostest`, `_phonevalidatetest`, `_suppressiontest`, `_wplinktest`, same lines.
  - `_callwiringtest.py` and `_e2ewiringtest.py` pass on the branch, as on base. A first draft with top-level helpers broke both (their harnesses extract only the two named functions); that is why the helpers are nested inside `callNotesPush`.

## Tests not run, and why
- Full `ci_suite.py`: not run. Only the notes-push block of `call_mode.py` changed; the 83 suites that import `call_mode` were compared on both trees instead.
- Real browser or phone check of the strip's look and position (fixed bar at the top, z-index 70, above `.wkban` at 61): not run; the test is node-based per the ticket. Check on a phone after the next publish.
- Live bridge, `send_server.py`, network, scheduled scripts: frozen, not run.

## Exposure
none to homeowners. Adds an operator warning and retries the existing POST /notes; no new route, no send, no change to what the bridge ledgers.

## Flags and defaults
No flag. Active once the page is rebuilt. New phone-storage key `fcDncAck` (case ids only). No env var.

Merged is not visible: visible after the next publish.

Decisions needed: D24 (appended).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
