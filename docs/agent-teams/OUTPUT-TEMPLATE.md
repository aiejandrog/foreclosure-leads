# OUTPUT-TEMPLATE: one block per task

Copy this block into `docs/agent-teams/runs/<date>-<team>.md` once per task. A task is not done until the block exists and `QA verdict` reads PASS. Plain English, counts and case ids only: no names, phones, addresses or keys (the repo is public; CLAUDE.md > Never commit).

```
### <task id>: <short title>
- Task:
- Owner:
- Branch + PR: <branch> @ <SHA> / <PR body path; Alex opens the draft>
- Files changed:
- What changed (plain English):
- Tests run (command + result):
- QA verdict (PASS/FAIL + by whom):
- Exposure: sending | suppression | stays | spend | none   (pick all that apply; say what)
- Flags (default off?):
- Owned-surface bugs found:
- Needs Alex (one line each):
- Decisions needed (link to the DECISIONS-NEEDED entry):
- Next run should know:
```

Field rules:
- **QA verdict**: written by the QA gate, never by the owner. PASS carries the command and its result.
- **Exposure**: anything but `none` needs a one-line reason. Touching stays, DNC, opt-out or `send_server.py` also means "LEGAL REVIEW" in the PR title (CLAUDE.md > OWNED SURFACE).
- **Decisions needed**: any spend, send, rule, stay, DNC, opt-out, send-path or copy question goes to DECISIONS-NEEDED.md first; link it here. An OPEN entry keeps the PR for Alex, not the Lead.
- **Flags**: name the env var and its default. New behavior ships default off unless Alex said otherwise.
- **Owned-surface bugs found**: report, do not fix (CLAUDE.md > OWNED SURFACE).
- **Tests run**: say which suites were skipped in CI and why they do not count (CI-TESTS.md).

## Example (invented data)

```
### X-1: Show the equity source on each card
- Task: X-1, card shows where the equity figure came from
- Owner: board-dev
- Branch + PR: claude/agents-board @ 1a2b3c4 / docs/agent-team/pr/board.md
- Files changed: tracker_template.html, _examplecardtest.py (new)
- What changed (plain English): Each lead card now shows a small label saying whether the equity number is a recorded fact, a ceiling, or a guess. Guess-labelled cards no longer show a dollar figure.
- Tests run (command + result): `python _examplecardtest.py` -> 12 pass, 0 fail (DEALFLOW_DIR=temp dir); `python ci_suite.py` -> 82 pass, 38 skipped, 0 fail
- QA verdict (PASS/FAIL + by whom): PASS by qa-gate, 2026-10-29, same commands, same output
- Exposure: none (display only; no send, no suppression change, no stay change, no spend)
- Flags (default off?): none
- Owned-surface bugs found: none
- Needs Alex (one line each): none
- Decisions needed (link to the DECISIONS-NEEDED entry): none
- Next run should know: visible only after the next refresh-dealflow.bat; the board bake for this label lives in foreclosure_leads.py, which equity-dev owns.
```
