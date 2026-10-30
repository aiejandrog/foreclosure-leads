---
name: fix-agent
description: Run ONE fix item (from docs/VERDICT-BLOCKERS.md or docs/agent-teams/DECISIONS-NEEDED.md) through test-first, smallest-change, QA-gated delivery on the DEALFLOW repo. Use when the Lead hands a fixer a single fix to build.
---

# Fix agent: one fix, test first, QA gated

You get ONE fix item. Deliver it with the loop below. Do not take on a second item.

## 0. Read first (grep-then-range for any file over 1,500 lines)
- `OWNERS.md`, `docs/agent-teams/PATTERNS.md`, the newest `docs/agent-teams/runs/*.md`.
- The CLAUDE.md sections your files touch.
- From the spawn prompt: which owner role you act for, and the exact fix item.

## 1. Ownership
- Edit only the files your role owns (`OWNERS.md`). For any other file, SendMessage its owner with the exact change and wait. If no owner is running, tell the Lead.
- The "Nobody edits" list in `OWNERS.md` is report-only. Put what you find in the run log and in DECISIONS-NEEDED.

## 2. Worktree
- `git worktree add ../fl-<fix> -b claude/agents-<fix> origin/main`. If `git fetch` fails, use the local `origin/main` and say so.
- Never pull, check out, stash, edit or run anything in `C:\Users\olqbb\projects\foreclosure-leads`. A refresh may be running there, and `refresh-dealflow.bat` pulls itself mid-run.

## 3. Failing test first
- Write `_<fix>test.py`. Use invented fixtures, a temp `DEALFLOW_DIR`, and no network. For board or Call Mode JS, run the code through node the way `_callequitytest.py` and `_boardstagetest.py` do. Add it with `git add -f`, because `.gitignore` blocks `_*.py`.
- Run it on unchanged `origin/main` and keep the failing output. A test that passes before the fix proves nothing.

## 4. Smallest change that passes
No refactors, no renames, no "while I'm here". A change that touches rule 6 (quoting a dollar figure) may only make quoting **stricter**.

## 5. Verify
- Run your new test, then the suites that import the files you changed (grep for them).
- Then run the full suite once, **in the background**: `DEALFLOW_DIR=<temp> python ci_suite.py > <temp>/ci.txt 2>&1`. Grep the FAIL lines afterwards. Never dump the full output.
- Compare against the baseline failures recorded for that main SHA in the newest run log. Only a FAIL outside that list counts against you. To prove a FAIL is pre-existing, rerun just that suite on `origin/main` in a second detached worktree. Do not rerun the whole suite there.
- Never `taskkill` or `Stop-Process` python.exe, node.exe, or any process you didn't start. Other agents' tests are running.

## 6. Legal triggers
If the change touches `send_server.py`, `stay_gate.py`, `never_contact.json`, DNC, opt-out, or the `/notes` and `/text` bridge paths:
- Put LEGAL REVIEW in the PR title.
- Append an entry to `docs/agent-teams/DECISIONS-NEEDED.md`. Re-read the file on `origin/main` first and take the next free D-number, using the entry format in that file. The Lead merges the master list.
- Never write "Decisions needed: none" for these.

## 7. Hand off
- Append to `docs/agent-teams/runs/<YYYY-MM-DD>-<fix>.md` (your own file) one OUTPUT-TEMPLATE block covering:
  - what changed, in plain English;
  - the tests run, each with its command and result;
  - the tests NOT run, and why;
  - Exposure;
  - Decisions needed.
- Commit with a message ending `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`. Write the PR body to `docs/agent-teams/pr/<fix>.md`, because `gh` is not installed.
- SendMessage `qa` with the worktree, branch, SHA and the exact commands, and say which earlier failures are pre-existing.
- Fix whatever QA FAILs. After 3 FAILs on the same item, stop and tell the Lead what is blocking.
- You never push to main and never merge. After QA PASS, the Lead merges.

## 8. Hard limits
- No spend, sends or scrapers: no paid API, 2Captcha, Whitepages, Tracerfy, PACER, live bk_lookup, records_liens, or miami_judgment.
- Never run any `.bat`, `send_server.py` on a real port or real `DEALFLOW_DIR`, `cadence.py`, `outreach_email.py`, or `reply_send --send`.
- No data, keys, names, phones or addresses in the repo. Never commit `call_objections.json`, because test churn rewrites it.
