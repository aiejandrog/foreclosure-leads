# Audit loop protocol (tickets, board, logs, merge classes)

Used by the audit loop prompt (`AUDIT-LOOP-PROMPT-2026-11-08.md`) and every role it spawns. Read after
`OPERATING-RULES-2026-11-07.md`. Plain English, counts and case ids only, invented people in fixtures.

## 1. Where things live

| what | where | committed? |
|---|---|---|
| Run folder | `%USERPROFILE%\DEALFLOW\agent-logs\<run>\` (`<run>` = `AGENT_RUN_ID`, default today's UTC date) | no |
| Flight recorder | `<run>\events.jsonl`, one scrubbed line per tool call, prompt, stop; guard blocks marked `BLOCKED` | no |
| Transcripts | `<run>\transcripts\` copied on every Stop / SubagentStop. **Raw, not scrubbed** | never |
| Ticket board | `<run>\BOARD.md`, owned by `chief` | scrubbed copy at the end |
| Work logs | `<run>\worklog-<name>.md`, one per agent, owned by that agent | scrubbed copy at the end |
| Report, autopsy, digest | `<run>\REPORT.md` (chief), `AUTOPSY.md` (autopsy), `digest.md` (recorder) | scrubbed copy at the end |
| Final record | `agent-loop/<run>/` at the repo root, one draft PR `docs: agent loop <run> records (no code)` | yes, via `agent_guard.py export` |

Why the repo root and not `docs/`: `publish_site.py` copies all of `docs/` to the **public** site repo.

`DEALFLOW` is outside OneDrive (`paths.py`). Never put a run folder on the OneDrive Desktop.

## 2. Ticket format (one block per ticket, in the author's worklog, then messaged to `chief`)

```
### <ID>  <one-line title>
- Type: FINDING | IDEA | PROCESS
- Area: board | call-mode | carlos-seat | worker | balloon-book | playbook | bridge | engine | process
- Severity: S1 | S2 | S3 | S4 | S5        (FINDING only; see section 3)
- Evidence: file:line, the exact command, and the output excerpt (or screenshot path, fake data)
- Repro: steps another agent can run with a temp DEALFLOW_DIR and no network
- Proposed fix (smallest): what changes, in which file, and the test that would prove it
- Owner role (OWNERS.md): board-dev | callmode-dev | equity-dev | report-only | ...
- Touches: none | send | stay | DNC | suppression | spend | copy | Carlos-seat   (all that apply)
- Confidence: high | medium | low, and why
```

IDs: `F-<agent>-NN` for findings (`F-sec-03`), `I-NN` for ideas, `P-NN` for process tickets.
IDEA adds: `Problem evidence`, `Expected effect` (prefix `guess:` when not measured), `Counter that
would measure it`, `Effort S/M/L`, `$ cost` (must be 0).

## 3. Severity (the order Alex reads)

| | meaning | example |
|---|---|---|
| S1 | could contact someone who said stop, is under a stay, DNC, a client or represented | a held lead shows a Text button |
| S2 | could show or speak a wrong dollar figure | opening bid printed as "judgment" |
| S3 | breaks or slows dialing | a Call Mode outcome that does not save |
| S4 | security or personal data | lead field injected into innerHTML; PII in a public file |
| S5 | everything else | layout, copy drift, dead link |

## 4. Ticket states (only `chief` moves a ticket)

```
NEW -> NEEDS-INFO (max 1 round trip) -> NEW
NEW -> DENIED (reason) | DUPLICATE (of id) | APPROVED-REPORT (report-only surface, goes to Alex)
NEW -> APPROVED-FIX -> IN-FIX (fixer, branch) -> QA-FAIL (back to fixer, max 3) -> FAILED (to autopsy)
                                             -> QA-PASS (gate) -> SHIPPED-PR (chief accepted scope)
```

## 5. BOARD.md (chief)

```
# Board <run>   baseline: origin/main @ <sha>, ci_suite failures on main: <list or none>
| id | type | sev | area | author | state | owner role | branch / PR | merge class | note |
```
Below the table, a `## Conversation` list: one line per message that changed a state
(`ts  chief -> wire  F-wire-02 NEEDS-INFO: which route returned 200?`).

## 6. Work log (every agent, its own file)

Append, never rewrite. One line per meaningful step:
`ts  <verb>  <what>  <proof>` e.g. `18:40 RAN python _callmodenavtest.py -> 14 pass`.
Every message you send that hands work to someone gets a line (`-> chief F-site-04 READY`).
End with one OUTPUT-TEMPLATE block (`docs/agent-teams/OUTPUT-TEMPLATE.md`).

## 7. Merge classes (written on every PR body; no agent ever merges)

- `Merge: Alex only` — the diff touches sends, stays, DNC, suppression, spend, homeowner copy, or
  the PR title carries LEGAL REVIEW.
- `Merge: ready for Alex` — clean QA PASS from `gate`, scope accepted by `chief`, Exposure: none.

Every PR body also carries: plain-English summary, tests run with output, tests not run and why,
`Exposure:`, flags and defaults, "Merged is not visible: visible after the next publish", and the
ticket id.

## 8. Carlos seat

Carlos's page is `docs/call/carlos/`, built by `call_mode.py` from `CALL_SEATS`. No change may bring
Whitepages relatives, pasted Whitepages numbers or Balloon Book data to his seat. `gate` FAILs a diff
that touches seat logic and WP or balloon fields in the same change unless Alex approved it in writing.

## 9. The guard (`.claude/hooks/agent_guard.py`)

Installed by Alex's setup paste, never by an agent. It refuses (exit 2) the freeze breaks listed in
its docstring and logs the attempt. A BLOCKED line is not a crash: read the reason, do the allowed
thing instead (import in a test, write a DECISIONS-NEEDED entry, push your own branch). Never try to
get around it by rewording the command; the autopsy reads every block.
