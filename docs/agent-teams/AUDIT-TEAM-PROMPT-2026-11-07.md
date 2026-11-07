# Audit team prompt (2026-11-07)

Paste the block below into a fresh Claude Code session opened at the repo root, with
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` set. This is an audit round: reports and draft PRs only.

```
Goal: find out, with evidence, what is wrong with DEALFLOW before Alex starts dialing again from the
new setup. Audit only. No fixes in this round except ones QA proves only make quoting stricter.

READ FIRST, every teammate: docs/agent-teams/OPERATING-RULES-2026-11-07.md (the migration freeze;
it overrides older briefs), CLAUDE.md in full, OWNERS.md, docs/agent-teams/PATTERNS.md, the newest
docs/agent-teams/runs/*.md, CI-TESTS.md, docs/VERDICT-BLOCKERS.md.

HARD RULES (breaking one = report rejected):
1. Freeze: no .bat, refresh, publish, send, bridge, scheduled-task change, DEALFLOW_FOLLOWUPS_ON,
   paid or rate-limited call. Tests run with DEALFLOW_DIR=<temp dir> and stubbed network.
2. Git: never edit, pull, stash or commit in the main checkout. Each teammate:
   git fetch origin; git worktree add ..\fl-audit-<role> -b claude/agents-audit-<role> origin/main
   Draft PRs only, never push to main, never merge. If gh is missing, write the PR body to
   docs/agent-teams/pr/audit-<role>.md on your branch.
3. Keys: never read, print or ask for *.key, *.pass, *.codes, *.url, sender.json,
   footprint.local.json. Reports carry counts and case ids, never names, phones, addresses.
4. Suppression, stays, DNC, opt-out, copy and send paths are report-only. Any PR near them gets
   "LEGAL REVIEW" in the title.
5. Honest results. A finding without file:line or repro steps does not count. A skipped suite is
   not evidence.

Create a team of 4 teammates using Sonnet, from the agent definitions in .claude/agents/. You, the
main session, are LEAD and do not audit yourself. Roster is above 3, so first spawn only "qa"
(qa-gate) and send it the roster with one line per extra agent; record its APPROVE/DECLINE in the
run log (PATTERNS.md section 2). If DECLINE, run site-qa and legal first, then code and data.

- "site" from site-qa: board, Morning Worker, Playbook, Call Mode, Carlos seat, Balloon Book, built
  from fake rows. Report docs/agent-teams/audits/2026-11-07-site-qa.md.
- "code" from code-auditor: CI truth, publish gates, send gates, secrets/PII in tracked files, task
  drift, agent commits straight to main. Report docs/agent-teams/audits/2026-11-07-code.md.
- "legal" from legal-auditor: opt-out ledger, stop detection, person-level suppression, stays and the
  bankruptcy waiver, copy pins, s.501.1377, clients and represented leads.
  Report docs/agent-teams/audits/2026-11-07-legal.md; every gap also appended to DECISIONS-NEEDED.md.
- "data" from data-auditor: tax-deed opening bid as judgment (confirm the may_quote fix, list the raw
  judg reads still printing it), "not posted" split, payoff mixing, court-copy hidden, Has-equity lane,
  equity overstatement on PR #439 fixtures. Report docs/agent-teams/audits/2026-11-07-data.md.
- "qa" from qa-gate: re-runs every command a report cites and writes QA PASS / QA FAIL per finding.

LOOP: auditor writes finding -> qa re-runs it -> PASS stays, FAIL goes back. 3 FAILs on one finding:
drop it and log why.

LEAD finishes with docs/agent-teams/runs/2026-11-07-audit.md: one OUTPUT-TEMPLATE block per
teammate, then a TOP 10 list for Alex ranked by (a) could contact someone who said stop or is under a
stay, (b) could show a wrong dollar figure to a homeowner, (c) breaks dialing, (d) everything else.
Each line: finding, evidence path, owner role, needs Alex yes/no. Then one draft PR titled
"docs: 2026-11-07 audit reports (no code)" with "Exposure: none". Stop there.
```

## After the round
Alex reads the TOP 10. Fixes run later, one per fixer (`.claude/agents/fixer.md`, `fix-agent`
skill), and only after the freeze is lifted for code work. Nothing in this round is visible on the
board or Call Mode; a merge reaches them only after a publish, which is frozen.
