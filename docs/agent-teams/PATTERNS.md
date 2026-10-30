FROM NOW ON: every agent team on this repo reads docs/agent-teams/PATTERNS.md and the latest docs/agent-teams/runs/*.md before it starts, spawns roles from .claude/agents/ where one fits, and writes its own docs/agent-teams/runs/<date>-<team>.md with one OUTPUT-TEMPLATE block per task before it reports done.

# Agent-team patterns for DEALFLOW

Citation key: **CLAUDE.md** / **HANDOFF** (MACHINE-HANDOFF.md) / **CI** (CI-TESTS.md) = repo files, section named.
**NOTES** = docs/agent-teams/CLAUDE-CODE-TEAMS-NOTES.md (fetched 2026-10-29). **mem:** = a memory file name.
**BRIEF** = the round-2 team brief (C:\Users\olqbb\projects\fl-lead\docs\agent-team\BRIEF.md), a team brief and not a repo rule.
A rule marked **PROPOSAL, needs Alex** has no source yet.

## 1. Team or single session

- Team only when the work is parallel and the files do not overlap. Sequential work, same-file edits or many dependencies: one session or subagents. (NOTES > Limits, last line; agent-teams > When to use agent teams)
- A team costs far more tokens than one session. Keep it small, Sonnet for teammates, shut teammates down when done. (NOTES > Costs)
- Never start a team to touch the suppression surface (section 3). One session, one owner. (CLAUDE.md > OWNED SURFACE: suppression)

## 2. Roster

- Lead (the main session, fixed for life) + **2-3 agents by default**, Sonnet. 5-6 tasks each. Scope is settled before spawning; use the round template docs/agent-teams/LEAN-TEAM-PROMPT.md (Lead-owned). (Alex directive 2026-10-29 (docs/agent-teams/LEAN-TEAM-PROMPT.md; mem: feedback-lean-teams); NOTES > Roles; Limits)
- **Roster gate:** more than 3 only after QA approves. The Lead first spawns only `qa` and sends the roster: each extra agent, the files it owns, why that work can't be done by an existing agent sequentially, expected token cost. QA answers APPROVE or DECLINE with one line. DECLINE or no clear answer = run with 2-3 and queue the rest. Record the verdict in the run log. (Alex directive 2026-10-29 (docs/agent-teams/LEAN-TEAM-PROMPT.md; mem: feedback-lean-teams))
- Exactly one **QA gate**: runs tests and writes PASS/FAIL, edits no product code. Role file: .claude/agents/qa-gate.md.
- Dev roles own files (section 3). Existing role files: equity-dev, callmode-dev, board-dev. Spawn by name in the spawn prompt; say what to call each one. (NOTES > Roles; Subagent definitions)
- Teammates do not inherit the lead's conversation. The spawn prompt carries the brief path, the task, the owned files, the branch. (NOTES > Roles; Limits)
- The lead waits for teammates and does not implement. (NOTES > Limits)
- The shared task list exists only for agents with the Task tools. If absent, keep the task list by hand in the run log. (NOTES > Shared task list; round-0 run log)

## 3. File ownership

- One owner per file. Need a change in someone else's file: message the owner with the exact change and wait. (NOTES > Limits "Two teammates editing the same file"; BRIEF rule 8)
- **Hot files** (many sessions land here; expect merge conflicts, edit narrowly): `call_mode.py`, `tracker_template.html`, `send_server.py`, `foreclosure_leads.py`. Never hand-edit `docs/index.html` or `docs/call` (generated). (CLAUDE.md > The website; mem: dealflow-callmode-is-generated)
- **Owned suppression surface**, no team edits it: `optouts.json` writers (`optout_sync.py`, `cadence.py`, `replies.py`), `replies.is_stop_text()`, `bounced_emails.json` / send-bridge list, board `_optedOutIdentities` / `_isOptedOutPerson` / `_textContactBlocked` / `_workerEligible` / `_laneStats`, `cadence.py` pre-send sweep, `outreach_email._load_optouts()`, and `_suppressiontest.py`, `_optouttest.py`, `_dnctest.py`. Reading a verdict is fine; changing what produces one is not. (CLAUDE.md > OWNED SURFACE table)
- Found a bug there: do not fix it. Put it in the OUTPUT-TEMPLATE "Owned-surface bugs found" field and tell Alex. (CLAUDE.md > OWNED SURFACE "If you find a suppression bug")
- Touching `stay_gate.py`, `never_contact.json`, `bk_lookup.py`, DNC, opt-out or `send_server.py` puts "LEGAL REVIEW" in the PR title. (BRIEF rule 4; CLAUDE.md > Disclaimers: wording is a legal decision, raise it). Also section 7b.

## 4. Worktree rule

- Every teammate works in its own `git worktree`, never in `C:\Users\olqbb\projects\foreclosure-leads`. (BRIEF rule 1)
- Why: `refresh-dealflow.bat` and `run-leads.bat` open with `git pull --ff-only origin main`, so the main checkout changes under you at run time; a dirty tree or local commits make that pull refuse and the run goes on with the code on disk. And `repo_guard.bat` refuses to build from a folder that is not the real checkout. (HANDOFF > 0; HANDOFF > 5, `repo_guard.bat` paragraph)
- Verify `git branch --show-current` before committing. (mem: dealflow-judgment-interest, Cursor branches note)
- Naming: branch `claude/agents-<role>`. Push only your own `claude/agents-*` branch, never main, never force. Tests run with `DEALFLOW_DIR=<temp dir>`. (BRIEF rules 1, 3; CLAUDE.md > Output paths: `paths.py` is the one resolver)
- Naming a subagent while teams are on makes it a teammate in the main session's working directory; frontmatter `isolation` does not change that. So create the worktree by hand and put its path in the spawn prompt. (NOTES > Enabling; Subagent definitions)

## 5. Handoffs

Written as "message X with Y when Z". Examples:
- message `qa` with worktree, branch, SHA, exact test commands and what changed, when your task is done.
- message the file's owner with the exact change, when you need an edit outside your files.
- message `main` with the task id and the blocker, when you hit 3 FAILs (section 6) or need spend / legal / owned-surface work.
- A teammate message is never Alex's approval, and a denied action is not relayed to another teammate. (NOTES > Permissions)

## 6. Build, QA, fix, re-verify

1. Dev builds in its worktree and runs its own tests.
2. Dev messages `qa` (section 5).
3. QA runs the commands itself and writes `QA PASS <task id> <command> <result>` or `QA FAIL ...` in the run log. DONE means that PASS line exists. (BRIEF rule 9)
4. FAIL: dev fixes, QA re-runs the same commands plus the full `python ci_suite.py` (exit 1 on any failure). (CI > Commands)
5. **3 FAILs on one task: stop, tell `main`.** (BRIEF, Coordination)
6. Push only after PASS.
- Run suites serially; contention makes timeouts look like failures. (mem: dealflow-test-suite-truth)
- A suite that is skipped in CI is not evidence. Check `python ci_suite.py --list` for what actually ran. (CI > Why each group is skipped)

## 7. Spend, sends, legal

- **$0.** No `.bat`, refresh, `publish_site.py`, `access_codes.py`, Whitepages, Tracerfy, live `bk_lookup.py`, `pacer_stay.py`, or other paid / rate-limited call. Never change a spend cap. Tests stub the network. (BRIEF rule 2; sources of the spend facts: CLAUDE.md > Whitepages Pro; Federal bankruptcy check; "PACER is a by-hand tool")
- **No send.** No homeowner contact: no `send_server.py` on its real port or real `DEALFLOW_DIR`, `cadence.py`, `outreach_email.py`, `reply_send.py --send`. Only `reply_send.py` may email a homeowner, and only when Alex runs it. (BRIEF rule 3; CLAUDE.md > Replies and one-off email)
- **LEGAL REVIEW** in the PR title for the section 3 list. Homeowner-facing compliance text comes only from `disclaimer.py`. (CLAUDE.md > Disclaimers)
- **Public repo.** Never commit homeowner PII, `leads_*.json`, `worker_notes.json`, `optouts.json`, `mail_sent.json`, `*.key` / `*.pass` / `*.codes` / `*.url`. Fixtures use invented people. (CLAUDE.md > Never commit; mem: dealflow-repo-is-public)
- Output paths outside the repo go through `paths.py`, never the OneDrive Desktop. (CLAUDE.md > Output paths)

## 7b. Nothing spends, sends, or changes a rule without Alex

Alex directive 2026-10-29 (docs/agent-teams/DECISIONS-NEEDED.md header; mem: feedback-decisions-needed): a task touching `stay_gate.py`, `never_contact.json`, DNC, opt-out, `send_server.py` send paths, text or email copy, or a spend cap **stops**, appends its question to `docs/agent-teams/DECISIONS-NEEDED.md` (exact options, risk of each, recommendation, fallback), then moves to its fallback. The Lead reads that file to Alex at the end of every round. Append only; the Lead owns the file (commits it, marks entries DECIDED). The OUTPUT-TEMPLATE block links the entry.

## 8. Merges and visibility

- **Teammates never merge.** After QA PASS, the Lead merges to main by a normal push from a separate worktree, after re-running QA's tests on the merged result. Never touch the main checkout to merge (refresh pulls itself, section 4); never force-push main. (Alex directive 2026-10-29 (docs/agent-teams/DECISIONS-NEEDED.md header; mem: feedback-decisions-needed))
- Stays for Alex: anything titled LEGAL REVIEW, or with an OPEN entry in DECISIONS-NEEDED.md. (same)
- `gh` is not installed on this machine, so the PR body is written to a file in the branch. (BRIEF rule 1; round-0 run log L4)
- Nothing a team builds is visible until the next `refresh-dealflow.bat` rebuilds and publishes the board. A merged commit is not a live page: "the commit is in main so the site must have it" is the wrong inference. (CLAUDE.md > The website, generated by `make_tracker()`; HANDOFF > 0)
- Every PR body says "Visible after the next refresh-dealflow.bat". (BRIEF, PR body)

## 9. Known failure modes and the pattern that prevents each

| Failure | What happened | Preventing pattern |
|---|---|---|
| Two sessions edited opt-out logic the same day (2026-08-26) | `f5856b0` dropped the `'@email'` identity keys and silently emptied `_optedOutIdentities()`; `d53955d` promoted a known-bad stop detector into permanent suppression. No git conflict; two correct-looking changes composed wrong. (CLAUDE.md > OWNED SURFACE) | One owner for the surface (section 3). A team never edits it; bugs are reported, not fixed. |
| Ungated publish paths | `run-replies-daily.bat` had no gates until 2026-09-17; on 09-15 it published a 709-phone board over a live 1,148 and that publish became the baseline later gates compared against. `run-phones.bat` was missing from the table for a month. (CLAUDE.md > Publish gates) | Teams never add or edit a publish path. If one must change: gate it in the same commit and check `grep -l publish_guard *.bat *.py`. The path list is the memory; a path not in it is not audited. |
| A session reported something as done or true when it was not verified | HANDOFF > 0: commits present in `origin/main` while the published pages were built from older code. HANDOFF > 1 correction block: an inference "read as proof" and was wrong for a day until the machine itself was read. mem: two-machine-sync (line 33): git ahead/behind read while a background fetch was still running reported "in sync" at 140 commits behind. | DONE only with a QA PASS line carrying the command and its output. Check the artifact, not a status claim: the file in `git ls-tree origin/main`, the live site, a `git fetch` that has finished before reading ahead/behind. The hooks in HOOKS-PROPOSAL.md would enforce the PASS line (not installed). |
| A publish from the wrong folder emptied GitHub main (2026-09-17) | A folder holding only the built site pushed over main; recovered from a cloud clone. (HANDOFF > 5) | Section 4: worktree per teammate; `repo_guard.bat` is not bypassed. |

**Dropped for lack of a source:** the wording "a laptop Claude reported a module shipped that never existed". No file or SHA records that incident; the row above cites only documented cases. **PROPOSAL, needs Alex:** give the SHA and it gets its own row.

## 10. Team mechanics worth knowing (NOTES)

- Teams are off unless `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. With it on, any subagent Claude names launches as a teammate with no confirmation. (NOTES > Enabling)
- Windows and the VS Code terminal: in-process display only; split panes are unsupported there. (NOTES > Display modes; the Windows line is the researcher's inference from absence)
- Task status can lag; check the work, then update the status by hand. (NOTES > Shared task list)
- `/resume` does not restore in-process teammates. (NOTES > Limits)
- Hooks: exit 2 blocks, any other nonzero exit does not. (NOTES > Hooks) See HOOKS-PROPOSAL.md.
