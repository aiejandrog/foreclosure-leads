# Operating rules from 2026-11-07 (machine migration freeze)

Every agent, team and Claude session on this repo reads this file before it starts, after CLAUDE.md
and before PATTERNS.md. Where this file is stricter than an older brief, this file wins. It stands
until Alex lifts it in writing.

## Why
The old laptop died on 2026-11-05. Its vendor credentials and local data are gone. Alex is standing
up a new laptop (Claude Code in VS Code). The desktop (DESKTOP-35NNMFL) has the current code. Until
Alex picks the operator machine and says "go" in the migration thread, nothing on any machine is
allowed to contact a homeowner, spend, or change the live site.

## The freeze (no exceptions)
1. **All DealFlow scheduled tasks stay DISABLED on every machine.** Do not run
   `install-tasks.ps1 -Enable`, `Enable-ScheduledTask`, `Start-ScheduledTask` or `schtasks /run`.
   Only Alex's "go", written in the migration thread, re-arms them.
2. **No refresh, no send, no publish, no bridge.** Do not run any `.bat`
   (`refresh-dealflow.bat`, `run-leads.bat`, `run-replies-daily.bat`, `run-phones*.bat`,
   `cadence-daily.bat`, `cadence-run.bat`), `send_server.py` on its real port, `cadence.py`,
   `outreach_email.py`, `reply_send.py --send`, `publish_site.py`, `quick_publish.py`,
   `access_codes.py`, `warmup.py`.
3. **Never set `DEALFLOW_FOLLOWUPS_ON`**, on any machine, in any shell, task or test that touches a
   real `DEALFLOW_DIR`.
4. **$0.** No Whitepages, Tracerfy, Twilio, PACER, live CourtListener (`bk_lookup.py`), clerk API,
   2Captcha, Deepgram, or any paid or rate-limited call. Do not change a spend cap.
5. **Vendor keys are re-issued by the vendor and entered by Alex by hand, masked, on the machine.**
   Never ask for a key in chat, never print one, never read `*.key`, `*.pass`, `*.codes`, `*.url`,
   `sender.json`, `footprint.local.json` or `pacer.pass`, never write one into a file, test, log,
   memory or commit.
6. **Merged is not visible.** A merge changes the board and Call Mode only after a rebuild
   (refresh or `quick_publish.py`), and both are frozen. Never tell Alex a fix is "live" or
   "on the phones" because it merged. Say "merged, visible after the next publish".
7. **Merges.** A thread or team may merge its own PR after a clean review and green CI, except PRs
   that touch spend, a LEGAL waiver, homeowner sends, suppression, stays or DNC. Those wait for
   Alex. Agent teams still never push to main and never merge; the Lead or Alex merges.
8. **Branches.** `claude/agents-*` for agent teams, worktrees only, draft PRs only. Never commit in
   the main checkout, never force-push, never commit caches, logs, ledgers, `leads_*.json`, audio,
   keys or homeowner data. Fixtures use invented people.
9. **LEGAL REVIEW** in the PR title for any change near `stay_gate.py`, `never_contact.json`,
   `bk_lookup.py`, DNC, opt-out, text or email copy, disclaimers, or `send_server.py` send paths.
   The suppression surface in CLAUDE.md stays report-only.

## What you may do
Read code, run unit tests with a temp `DEALFLOW_DIR` and stubbed network, build preview pages from
fake rows (`python build_preview.py`, `call_mode.build_html` with invented rows), audit, write
reports, and open draft PRs.

## Where things are
- Stand-up of the new laptop: the five stand-up prompts in the migration thread, and PR #441's
  checklist. Do not redo them.
- Audit team prompt: `docs/agent-teams/AUDIT-TEAM-PROMPT-2026-11-07.md`.
- Audit roles: `.claude/agents/site-qa.md`, `code-auditor.md`, `legal-auditor.md`, `data-auditor.md`.
- Audit loop with QA chief, fixers and autopsy (2026-11-08): `docs/agent-teams/AUDIT-LOOP-PROMPT-2026-11-08.md`,
  `AUDIT-LOOP-PROTOCOL.md`; guard + flight recorder `.claude/hooks/agent_guard.py` (installed by Alex's setup paste only).
