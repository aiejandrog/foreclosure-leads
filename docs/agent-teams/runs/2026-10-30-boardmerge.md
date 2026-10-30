# 2026-10-30 boardmerge: D14, the board's `_mergeLead` carries the after-the-yes unit

Owner: board-dev. Branch `claude/agents-boardmerge`, from origin/main 8a575e4 (local origin/main; no fetch).
Nothing here sends, spends or changes a copy line. Invented fixtures only.

## What changed (plain English)
`_mergeLead` in tracker_template.html (board sync; Call Mode extracts the same block) used to drop a teammate's
nextStep, stageAt, equityVerified, keepOrSell, afterYesAt, apptUnverified and `replied` on a lead the board already had,
so Carlos's next-step dates never showed on Alejandro's board. Now it uses the bridge's rule (`send_server._merge_note`, read, not edited):
- the side with the larger `afterYesAt` supplies the whole unit (nextStep, equityVerified, keepOrSell, apptUnverified, `next`, and its stage);
- `stageAt` dates union, earliest per stage;
- an older push never walks a stage back (the newer save's stage is re-applied after the generic status rule);
- `replied` (with repliedNote/repliedStop) is sticky: taken from the teammate when we lack it, never cleared;
- new const `_AY_STAGES` sits inside the block Call Mode extracts (right after `_DNC`), same list as `send_server._AFTER_YES_STAGES`.

## DNC / Dead precedence: how it is preserved
- The existing status block is untouched and runs first: DO NOT CONTACT from either side wins, then Dead, from either side.
- The new unit code runs only when `!_DEAD(out.status)` (`_DEAD` includes DNC), so a suppressed lead keeps its status and receives no nextStep, equity, keepOrSell, stage or afterYesAt from a teammate, and its stageAt is not merged.
- One existing line was tightened to match the bridge: the "sooner follow-up date" copy of `remote.next` now skips a suppressed lead, otherwise a DNC/Dead lead was still handed a teammate's `next`.
- The unit never writes `status` unless the winner's status is one of the after-the-yes stages, so it cannot set or clear DNC/Dead. `optout`, `wrongown`, `no*`, `dntph`, `badph` code is untouched.
- Proof: `_boardmergetest.py` cases "remote DNC wins", "remote DNC gets no next step", "local DNC not overridden / not given teammate next step", "local Dead not overridden", "remote Dead gets no next step", "dead remote: local unit untouched".
- LEGAL REVIEW trigger: not on the skill's list (no send_server.py, stay_gate, never_contact, opt-out, /notes or /text path edited; the owned suppression functions `_optedOutIdentities / _isOptedOutPerson / _textContactBlocked / _workerEligible / _laneStats` are untouched). It does sit next to the status merge, so the DNC/Dead behaviour above is stated and tested rather than assumed.

## Tests run
- `_boardmergetest.py` on 8a575e4 (template stashed): FAIL, 9 failures (newer next step, equity/keepOrSell, stage walk-back, DNC/Dead next, replied, stageAt earliest). After the fix: ok.
- `_boardstagetest`, `_verifytraintest`, `_callwiringtest`, `_mergenotetest`, `_funnelparitytest`: pass. Also `_nosynctest`, `_queueflowtest`, `_cmtabtest` (they run the extracted `_mergeLead`): pass.
- Full `ci_suite.py` in the background: see the result section below.

## Tests NOT run
- `_phonepagetest.py`: errors at `F._load_codes()[0][1]` (no access codes in this worktree), unrelated to the change.
- No browser run of the live board or Call Mode.

## Exposure
Board-only JS. A teammate's newer after-the-yes unit now replaces this device's older one; a clock-skewed or wrong `afterYesAt` on a phone would win. Same exposure the bridge already has. `replied` can no longer be cleared by hand on a device once a teammate has it (sticky by request).

## Decisions needed
D14 option A is implemented; Lead to mark D14 resolved on merge. Not fixed (report only, suppression surface): D15, `textopen` is still not merged by `_mergeLead`.

## Full ci_suite result (15bc4e4, DEALFLOW_DIR temp, background)
`CI SUMMARY: 244 pass / 12 fail, 39 skipped`. The 11 baseline failures all failed as expected (_ballooncurrencytest _callworkflowparitytest _judgmentpasstest _lpcoveragetest _openertest _resimplisynctest _syncgatetest _texttpltest _tracerfytest _uifixturetest _workerrunlogtest). One extra: `_pacerstaytest` failed in the suite with `/send` "timed out" (three ci_suite runs sharing the machine); rerun alone on this branch with a fresh DEALFLOW_DIR it passes ("all PACER stay checks passed"). It does not read tracker_template.html. Not rerun on a detached origin/main because it passes on the branch. `_autorunstoptest` passed this run.
