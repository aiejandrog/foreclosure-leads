
### B2: do-not-quote equity chip on the desktop row and the call sheet (F-site-08)
- Task: B2, APPROVED-FIX #3, S2 (a dollar figure shown with no "do not quote" label)
- Owner: board-dev (fixer-f-site-08)
- Branch + PR: claude/agents-fix-f-site-08 @ 7afa1bf2 local (rebased onto origin/main aac7a8a9, no predecessor; remote head still the stacked d722a8bf until a force-push is allowed) / PR #453
- Files changed: tracker_template.html (three display edits), _boardeqcchiptest.py (new). Nothing under docs/.
- What changed (plain English): the desktop row and the call sheet show the same "may quote / do not quote" equity chip the phone card already had (existing _eqcChip, reused). On the call sheet Money line, when the equity card does not allow quoting, the figure reads "at most $X, liens not verified" instead of "net est $X". No number changed.
- Tests run (command + result): `python _boardeqcchiptest.py` unchanged tree -> 19 checks, 8 failed; new head -> 19 checks, 0 failed. Serial, temp DEALFLOW_DIR, both PYTHONUTF8 settings, branch vs fl-loop-base: _clienttest, _footprinttest, _navtest, _boardurltest, _callequitytest pass on both; _equityboardtest, _mirrortest, _optouttest, _dnctest fail identically (Playwright Executable doesn't exist); _suppressiontest 17/19 on both.
- QA verdict (PASS/FAIL + by whom): PASS by gate on d722a8bf (same code); chief bounced on scope (stacking, docs files), not a code FAIL
- Exposure: none
- Flags (default off?): none
- Owned-surface bugs found: none touched
- Needs Alex (one line each): none
- Decisions needed: none
- Next run should know: _clienttest pins "+who+clientBan+" so the chip is added with who += _eqcChip(r). Known residual F-b2-01: the clipboard "Copy all numbers" text (_copyCallSheet) prints Money/equity with no quote label. agent_guard blocks force-push even on one's own branch.
