# Audit loop: senior dev team + ideas + QA chief + fixers + autopsy (2026-11-08)

Builds on, does not replace: `OPERATING-RULES-2026-11-07.md` (the freeze), `PATTERNS.md` (roster gate,
worktrees, QA PASS), `AUDIT-TEAM-PROMPT-2026-11-07.md` (one-shot audit, reports only), the
`fix-agent` skill and `OWNERS.md`. New here: a ticket loop with one triage authority, an ideas agent,
fixers that open PRs back through QA, a flight recorder on every tool call, and an autopsy of every
failure. Ticket format, states, board and merge classes: `AUDIT-LOOP-PROTOCOL.md`.

**Cost, once:** a full run is about 8 to 10 Sonnet contexts spread over five waves. The waves keep at
most 4 teammates alive at a time, and you can say "stop after wave N" at any point.

## The flow

```
            wave 1 AUDIT                        wave 2 IDEAS
  site ─┐   sec ─┐   wire ─┐  (+code/legal/data)   ideas ─┐
        └────────┴─────────┴──── tickets ───────────────┴──► chief (QA chief)
                                                              │ re-runs evidence, "case against",
                                                              │ APPROVED-FIX / -REPORT / DENIED
                          ┌───────────────────────────────────┘
            wave 3 FIX    ▼
  main spawns fixer-<id> (test first, owner role, own worktree, claude/agents-fix-<id>)
        └─► gate (qa-gate): re-runs tests + ci_suite vs baseline + diff checklist
               FAIL ─► back to fixer (3 FAILs = FAILED, to autopsy)
               PASS ─► draft PR ─► chief: scope check ─► SHIPPED-PR, merge class on the PR
            wave 4 AUTOPSY
  autopsy reads recorder log + transcripts + board ─► AUTOPSY.md + PROCESS tickets ─► chief
            wave 5 REPORT
  chief REPORT.md ─► main exports scrubbed records ─► one docs PR ─► Alex merges what he wants
```

Every tool call, prompt, block and stop is written by the hook to
`%USERPROFILE%\DEALFLOW\agent-logs\<run>\events.jsonl`, and every transcript is copied to
`<run>\transcripts\`. Nobody merges. `Merge: Alex only` on anything touching sends, stays, DNC,
suppression, spend or copy; `Merge: ready for Alex` on the rest.

## Roster

| name | role file | wave | owns | edits product code? |
|---|---|---|---|---|
| main | (the session you paste into) | all | spawning, worktrees, shutdowns, final export PR | no |
| chief | `qa-chief` | 1-5 | `BOARD.md`, `REPORT.md` | no |
| site | `site-qa` | 1 | `worklog-site.md` | no |
| sec | `security-auditor` | 1 | `worklog-sec.md` | no |
| wire | `wiring-auditor` | 1 | `worklog-wire.md` | no |
| code / legal / data | `code-auditor` / `legal-auditor` / `data-auditor` | 1b, optional | their worklogs | no |
| ideas | `ideas-scout` | 2 | `worklog-ideas.md` | no |
| gate | `qa-gate` | 3 | QA lines in `worklog-gate.md` | no |
| fixer-<id> | `fixer` (acting as the owner role in `OWNERS.md`) | 3 | only that ticket's files | yes, on its branch |
| autopsy | `failure-reviewer` | 4 | `AUTOPSY.md` | no |

## Setup paste (PowerShell 7, `pwsh`, one paste, on the laptop where the team will run)

What it does: copies the guard from `origin/main` (or this PR's branch before it merges) to
`%USERPROFILE%\DEALFLOW\agent-hooks\agent_guard.py`; registers it as a hook in the main checkout's
`.claude\settings.local.json` (this machine only, kept out of git via `.git\info\exclude`); turns on
agent teams in your user settings if it is off; creates `DEALFLOW\agent-logs`; proves the guard blocks
`python send_server.py` and allows `git status`. It does not change your permission mode, start
anything, send, publish or spend. Backups: `*.bak-<timestamp>`. Undo: copy the backups back.

```powershell
$ErrorActionPreference='Stop'; $repo="$env:USERPROFILE\projects\foreclosure-leads"; if(-not (Test-Path "$repo\.git")){throw "Repo not found at $repo. Edit the first line to your clone path and paste again."}; Set-Location $repo; git fetch -q origin 2>$null; $src=$null; foreach($r in 'origin/main','origin/claude/project-thread-nt06sc'){ git cat-file -e "${r}:.claude/hooks/agent_guard.py" 2>$null; if($LASTEXITCODE -eq 0){$src=$r; break} }; if(-not $src){throw 'agent_guard.py is not on origin/main or the PR branch yet.'}; $hd="$env:USERPROFILE\DEALFLOW\agent-hooks"; New-Item -ItemType Directory -Force $hd,"$env:USERPROFILE\DEALFLOW\agent-logs" | Out-Null; $hook="$hd\agent_guard.py"; (git --no-pager show "${src}:.claude/hooks/agent_guard.py") -join "`n" | Set-Content $hook -Encoding utf8; $ts=Get-Date -Format yyyyMMdd-HHmmss; New-Item -ItemType Directory -Force "$repo\.claude" | Out-Null; $sl="$repo\.claude\settings.local.json"; if(Test-Path $sl){Copy-Item $sl "$sl.bak-$ts"; $s=Get-Content $sl -Raw | ConvertFrom-Json -AsHashtable}else{$s=@{}}; if(-not $s){$s=@{}}; $cmd='python "' + ($hook -replace '\\','/') + '"'; $h=@{type='command'; command=$cmd; timeout=20}; if(-not $s.hooks){$s.hooks=@{}}; foreach($e in 'PreToolUse','PostToolUse'){ $s.hooks[$e]=@(@{matcher='*'; hooks=@($h)}) }; foreach($e in 'UserPromptSubmit','Stop','SubagentStop','SessionEnd','TeammateIdle','TaskCompleted'){ $s.hooks[$e]=@(@{hooks=@($h)}) }; $s | ConvertTo-Json -Depth 20 | Set-Content $sl -Encoding utf8; $ex="$repo\.git\info\exclude"; if(-not (Test-Path $ex) -or -not (Select-String -Path $ex -SimpleMatch '.claude/settings.local.json' -Quiet)){Add-Content $ex '.claude/settings.local.json'}; $us="$env:USERPROFILE\.claude\settings.json"; New-Item -ItemType Directory -Force (Split-Path $us) | Out-Null; if(Test-Path $us){$u=Get-Content $us -Raw | ConvertFrom-Json -AsHashtable}else{$u=@{}}; if(-not $u){$u=@{}}; if(-not $u.env){$u.env=@{}}; if($u.env['CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS'] -ne '1'){ if(Test-Path $us){Copy-Item $us "$us.bak-$ts"}; $u.env['CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS']='1'; $u | ConvertTo-Json -Depth 20 | Set-Content $us -Encoding utf8; 'Agent teams: turned ON in user settings' } else { 'Agent teams: already on' }; $dn=@($u.permissions.deny).Count; "Deny rules in user settings: $dn  (0 means the 11-07 deny-list paste was not run; the guard still blocks, but run that paste too)"; $env:AGENT_RUN_ID='setup-smoke'; '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"python send_server.py"}}' | python $hook 2>$null; $b=$LASTEXITCODE; '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git status"}}' | python $hook; $a=$LASTEXITCODE; Remove-Item Env:AGENT_RUN_ID; if($b -eq 2 -and $a -eq 0){"GUARD OK (blocked send_server, allowed git status). Guard from $src. Hook file $hook"}else{"GUARD FAILED: block exit=$b allow exit=$a. Check that 'python' runs from this shell. Do NOT start the team."}; "Settings: $sl"; git --no-pager status -sb | Select-Object -First 3
```

Then: reload VS Code (Ctrl+Shift+P > Developer: Reload Window), open a new Claude Code chat **at the
main checkout root** (the hook is registered there), run `/hooks` and check PreToolUse lists
`agent_guard.py`, then paste the team prompt below.

## Team prompt (paste into the new Claude Code chat)

```
AUDIT LOOP RUN. Repo root = this folder (main checkout). You are "main", the Lead. You coordinate,
spawn, shut down and export. You do not audit, fix or review code yourself. Wait for teammates.
Open a NEW chat for the loop; do not use that window for other projects while the hook is active.

READ FIRST (you, and tell every teammate to read them in its spawn prompt):
docs/agent-teams/OPERATING-RULES-2026-11-07.md, docs/agent-teams/AUDIT-LOOP-PROTOCOL.md,
docs/agent-teams/PATTERNS.md, OWNERS.md, CLAUDE.md sections your files touch (grep the heading).
RUN = today's date (YYYY-MM-DD). RUNDIR = %USERPROFILE%\DEALFLOW\agent-logs\<RUN>.
The recorder names its folder by UTC date, so a run that crosses 00:00 UTC writes into two folders:
pass both to digest and to autopsy, and count guard blocks from both.
HOOK = %USERPROFILE%\DEALFLOW\agent-hooks\agent_guard.py.

HARD RULES (any break = stop that agent; the guard enforces most of them and logs every attempt):
1. $0. No .bat, refresh, publish, send, bridge on its real port or real DEALFLOW_DIR, scheduled task,
   DEALFLOW_FOLLOWUPS_ON, Whitepages, Tracerfy, Twilio, PACER, CourtListener, clerk API, 2Captcha,
   Deepgram. Tests use a temp DEALFLOW_DIR, a free port, stubbed SMTP and network.
2. Git: never commit, pull, stash or edit in the main checkout. Every teammate works in its own
   worktree beside it. Branches claude/agents-* only. Never push main, never force-push, never merge.
3. Never read, print or ask for *.key, *.pass, *.codes, *.url, sender.json, footprint.local.json.
   Worklogs, tickets, PRs: counts and case ids only, never names, phones, emails, addresses.
4. Report-only (never edited by any agent): the owned suppression surface, stay_gate.py,
   never_contact.json, bk_lookup.py, bk_waiver.json, send_server.py, reply_send.py, disclaimer.py,
   whitepages_lookup.py, wp_contacts.py, docs/index.html, docs/call/, .bat, desktop-setup/,
   .github/workflows/, .claude/settings*, .claude/hooks/. A fix there = APPROVED-REPORT for Alex.
5. Carlos's seat (docs/call/carlos/, CALL_SEATS in call_mode.py) never gets Whitepages or Balloon
   Book data. LEGAL REVIEW in the PR title near stays, DNC, opt-out, copy or send paths.
6. A guard BLOCK means do the allowed thing instead. Rewording a blocked command to get past the
   guard = that agent is shut down and the autopsy records it.
7. Honest results: a claim without the command and its output does not count. Merged is not
   visible: say "merged, visible after the next publish".

WAVE 0 (you alone, before any spawn)
a. Guard check: run `echo agent-guard-probe.bat`. It must come back "agent_guard BLOCKED" (the deny
   list does not cover this name, so only the guard can block it).
   If it runs, STOP and tell Alex "guard not active, run the setup paste". Do not continue.
b. git fetch origin. git worktree add ..\fl-loop-base --detach origin/main. In it, with
   DEALFLOW_DIR set to a new temp folder: python ci_suite.py > %TEMP%\ci-base.txt 2>&1, then grep
   FAIL lines. Note origin/main SHA and the baseline failures. Run it twice, serially, once in the
   default locale and once with PYTHONUTF8=1, with no other session running, and write both lists:
   a suite that is red only inside the full run is noise. Before any browser suite set PW_CHROMIUM
   to the system Chrome and write whether _dnctest.py honoured it; if it did not, every LEGAL REVIEW
   PR body says "DNC/opt-out browser suites not run".
c. Spawn ONLY "chief" from qa-chief (Sonnet). Send it: RUN, RUNDIR, the SHA, the baseline failures,
   and this wave plan for the roster gate (PATTERNS.md section 2):
     wave 1 site, sec, wire (+ code, legal, data only if docs/agent-teams/audits/2026-11-07-*.md
     do not exist on origin/main; if they exist, chief imports their QA PASS findings as tickets)
     wave 2 ideas; wave 3 gate + up to 2 fixers at a time; wave 4 autopsy. Never more than 4
     teammates alive at once. Chief answers APPROVE or DECLINE with one line; DECLINE = drop 1b.
   Chief creates BOARD.md now.

WAVE 1 AUDIT
For each of site (site-qa), sec (security-auditor), wire (wiring-auditor), all Sonnet:
  git worktree add ..\fl-loop-<name> --detach origin/main   (read-only audit, no branch, no commits)
  spawn prompt (under 30 lines): role file name, worktree path, RUN, RUNDIR, "write tickets in
  RUNDIR\worklog-<name>.md in the AUDIT-LOOP-PROTOCOL format and message each one to chief as you
  write it (if you have no SendMessage, end the worklog with `READY: <ids>`); this loop replaces
  the docs/agent-teams/audits/ report path in your role file", scope split by primary surface:
  site = board, Reply inbox, Playbook, Balloon Book page; wire = Call Mode, Carlos seat, Morning
  Worker, bridge routes, funnel; sec = public footprint, repo and Pages exposure, bridge auth. A
  finding on another agent's surface is one line `SEE <name>` in your worklog, not a ticket (sec
  keeps payload and access). Stop at 12 tickets or when your checks are done, end with an
  OUTPUT-TEMPLATE block. Scratch: write only under <scratchpad>\<your-name>\; a bare name in the
  scratchpad root belongs to nobody. A probe that starts the bridge runs in its own throwaway
  worktree (git worktree add --detach ..\fl-probe-<name> origin/main) with DEALFLOW_DIR set,
  because the bridge writes state files beside the code; main removes it.
Chief is a subagent and ends at every hand-back. Main resumes it once per batch: after the auditors,
after ideas, after each gate PASS. Chief's hand-back ends with `NEXT NEEDED FROM MAIN:`. When an
auditor finishes and chief has no open NEEDS-INFO for it, shut it down. Then wave 1b if approved,
same pattern.

WAVE 2 IDEAS
Spawn "ideas" from ideas-scout in ..\fl-loop-ideas (--detach origin/main) once wave-1 auditors are
shut down. Tell it to read the APPROVED tickets on RUNDIR\BOARD.md first. Chief approves at most 3.
Shut ideas down when chief has ruled on every idea.

WAVE 3 FIX
Spawn "gate" from qa-gate when the first fixer hands back (a gate with nothing to check stops at
once). Tell it: baseline SHA and failures, its QA lines go in
RUNDIR\worklog-gate.md, and its diff checklist adds: Carlos seat gets no WP or balloon data;
merge class line present; nothing on the report-only list changed; tests not run are listed;
the diff name list has no path under docs/.
Take chief's APPROVED-FIX list, S1 first, then S2, S3, S4, S5; at most 4 fixes this run, at most
2 fixers alive at once, and never two fixers on the same file at once. Every fixer branches from
origin/main, never from another fixer's branch; "queue" means one after the other in time, not
stacked; stack only on a textual dependency, with a written fallback if the earlier PR is blocked.
Nobody merges during a run.
For each ticket <id>: git worktree add ..\fl-fix-<id> -b claude/agents-fix-<id> origin/main, then
spawn "fixer-<id>" from fixer (Sonnet) with: the ticket block verbatim, the owner role from
OWNERS.md it acts as, the worktree, the branch, the test to write first, LEGAL REVIEW yes/no, and
"hand to gate; after QA PASS push your branch and open a DRAFT PR (gh pr create --draft --body-file
RUNDIR\pr-<id>.md; the body file stays in RUNDIR, never in the repo) with the merge class chief
gave; then message chief the PR link". Fixer follows the fix-agent skill.
Gate FAIL goes back to the fixer; 3 FAILs = chief marks FAILED. Chief does the scope check after
PASS and marks SHIPPED-PR. Shut each fixer down after SHIPPED-PR or FAILED.
If a fixer is QA-BLOCKED, chief re-plans in the same reply (narrow to the edits the guard allows, or
APPROVED-REPORT) so the fix slot is not left idle.

WAVE 4 AUTOPSY
Shut down gate. Run: python %USERPROFILE%\DEALFLOW\agent-hooks\agent_guard.py digest <RUN>.
Spawn "autopsy" from failure-reviewer with RUN and RUNDIR. Chief rules on its PROCESS tickets.

WAVE 5 REPORT AND RECORD
a. Chief writes RUNDIR\REPORT.md (protocol: TOP 10 by severity, every PR with merge class, decisions
   for Alex with options, denied ideas one line each, autopsy top 3, guard blocks count).
b. You: git worktree add ..\fl-loop-rec -b claude/agents-loop-<RUN> origin/main; then
   python %USERPROFILE%\DEALFLOW\agent-hooks\agent_guard.py export <RUN> ..\fl-loop-rec\agent-loop\<RUN>
   Read every file the export says needed masking. Apply chief-approved PROCESS edits to
   docs/agent-teams/*.md or .claude/agents/*.md in this same worktree (docs only).
   Commit, push claude/agents-loop-<RUN>, open a DRAFT PR "docs: agent loop <RUN> records (no code)"
   with "Exposure: none" and "Merge: ready for Alex".
c. Shut every teammate down. Remove the read-only worktrees (fl-loop-base, fl-loop-site, -sec,
   -wire, -ideas, and 1b ones); keep fl-fix-* until Alex merges or closes those PRs.
d. Your final message to Alex, under 25 lines: the TOP 10 (one line each: severity, finding, ticket,
   PR or "report only"), the PR list with merge class, decisions he must make, guard blocks count,
   and "nothing was sent, published or spent".
```

## Reading a run afterwards

- `RUNDIR\REPORT.md` first, then `BOARD.md`. `AUTOPSY.md` says what went wrong and what to change.
- `python %USERPROFILE%\DEALFLOW\agent-hooks\agent_guard.py digest <run>` re-counts blocks and failures.
- A single moment: `Select-String -Path RUNDIR\events.jsonl -Pattern 'F-wire-03'`, then the
  transcript copy in `RUNDIR\transcripts\` for the full conversation. Transcripts are raw: never
  commit, attach or paste them anywhere public.
- Clean-up: delete old run folders by hand when you no longer need them; nothing else reads them.

## Known limits (honest)

- The guard matches command text and file paths. A determined agent could still do damage by a path
  it does not know about (for example, writing a new script that calls `smtplib`). The deny list,
  the role rules, `gate`'s diff review and the autopsy are the other layers; none of them is a
  sandbox. Running with `bypassPermissions` keeps that risk; `acceptEdits` is the safer mode.
- Hook input fields for teammates (`agent_id`, `agent_type`, `teammate_name`) vary by Claude Code
  version; when none is present the recorder writes `main` and the session id tells agents apart.
- The hook runs `python`. If `python` is not on PATH for VS Code, the hook fails open: nothing is
  recorded or blocked. Wave 0 step (a) catches that before any teammate starts.
- Untested against the live hook runtime on the new laptop; `_agentguardtest.py` drives the script
  exactly as a hook would (JSON on stdin, exit codes) on Linux CI.
