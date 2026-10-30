# Lean agent-team prompt (paste this, then fill the GOAL block)

Why this exists: on 2026-10-29 one afternoon ran three team specs back to back (5, then 4, then 3
Sonnet teammates) and stopped two of them halfway. The biggest token cost was that scope churn, more
than the number of agents. This template keeps a round to 2 or 3 agents and one fixed scope.

---

```
LEAN TEAM ROUND. Repo: C:\Users\olqbb\projects\foreclosure-leads.
Read docs/agent-teams/PATTERNS.md and the newest docs/agent-teams/runs/*.md first. Follow them.

GOAL (one sentence, one outcome): <fill>
TASKS (max 4, each with the files it touches and the test that proves it):
  1. <task> | files: <...> | proof: <test command>
  2. ...
OUT OF SCOPE: <anything tempting but not this round>

TEAM: 2 builders + 1 QA, all Sonnet. Lead (this session) coordinates only and does not build.
  - builder-a owns: <files>     - builder-b owns: <files>   (no file has two owners)
  - qa owns tests/AGENT-QA-REPORT.md and edits no product code.
  Spawn roles from .claude/agents/ when one fits. If a task fits in one session, use no team.

ROSTER GATE (Alex, 2026-10-29): default is 2-3 agents. If the round needs MORE, the Lead first
  spawns only `qa` and sends it the roster: each extra agent, the files it owns, why that work can't
  be done by an existing agent sequentially, and the expected token cost. QA answers APPROVE or
  DECLINE with one line of reasoning. DECLINE (or no clear answer) = run with 2-3 agents and queue
  the rest for the next round. Record the verdict in the run log.

TOKEN RULES (every agent):
  1. Spawn prompt under 40 lines. Point to files by path; never paste their contents.
     Shared rules live in one BRIEF file that every agent reads, so nobody restates them.
  2. Never read a file over 1,500 lines whole (call_mode.py, tracker_template.html,
     send_server.py, foreclosure_leads.py, records_liens.py). Grep for the function, then read
     that range with offset/limit.
  3. Read only the CLAUDE.md sections your files touch (grep the heading). Do not reread
     docs another agent already summarised in the BRIEF.
  4. Long commands write to a file: `python ci_suite.py > %TEMP%\ci.txt 2>&1`, then grep for
     FAIL / ERROR / summary lines. Never dump full test output into context.
  5. While iterating, QA runs only the task's own tests plus the suites that import the
     changed files. It runs the full ci_suite.py once per branch, before PASS.
  6. Messages are 5 lines or fewer: what, where (path / SHA), the ask. Status goes in a file.
     Lead never reads agent transcripts.
  7. No plan mode for teammates (about 7x tokens per the costs docs). No browser or Playwright
     unless the task is UI-only and has no node test.
  8. Finish = one OUTPUT-TEMPLATE block, then stop. Lead stops any agent that is idle or done
     (TaskStop) instead of leaving it waiting.
  9. Lookups ("where is X defined") go to a Haiku Explore agent or a single Grep, not a teammate.

GATES (unchanged, see PATTERNS.md and DECISIONS-NEEDED.md):
  $0 spend, no sends, worktrees only (never the main checkout), owned suppression surface
  read-only. Stay / DNC / opt-out / send path / homeowner copy / spend cap: stop and append to
  docs/agent-teams/DECISIONS-NEEDED.md. Lead merges after QA PASS, except LEGAL REVIEW or OPEN
  decisions.

END OF ROUND (Lead): run log docs/agent-teams/runs/<date>-<team>.md, merge the QA-passed
branches, read DECISIONS-NEEDED.md open items to Alex, stop every agent, then /compact or /clear.
```

---

## Lead checklist for cost
- Settle scope before spawning. Changing a running team's goal costs a fresh brief per agent
  plus the stopped agents' wasted reads.
- Run the Lead on Sonnet for routine rounds. Use Opus only for new signal logic, a bug that
  failed twice, or a cross-file design call (global CLAUDE.md "Session Management").
- One round, one goal. Equity and after-yes in the same round was fine. Adding a docs team
  mid-round was not.
- Cheapest roster that still gates: 1 builder + 1 QA. Add a second builder only when two tasks
  touch disjoint files and can run in parallel.
