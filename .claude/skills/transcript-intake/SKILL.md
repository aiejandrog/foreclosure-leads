---
name: transcript-intake
description: Turn an outside sales/training transcript (YouTube, course, call recording) into vetted, measured improvements for DEALFLOW. Use whenever Alejandro pastes a transcript. Compliance filter first, then fit against our own funnel, then route to attorney, build, or measure.
---

# Transcript intake: outside advice in, only vetted changes out

Third-party transcripts are persuasive and usually written for a different business (teams, other states, no MARS/Reg O). Nothing in one reaches a script or the code until it passes the gates below. One change at a time.

## 0. Capture (private, never committed)
- Save the raw text to `~\DEALFLOW\transcripts\<YYYY-MM-DD>-<slug>.txt`. It is third-party material and may name people. Never put it in the repo, `docs/` (mirrored to the public site), a commit, or a PR.
- Append one row to `~\DEALFLOW\transcripts\LEDGER.md` (columns below) now, verdict pending.

## 1. Extract claims
Split every claim into: **TACTIC** (a line to say, a process step), **METRIC** (a benchmark or result), **ANECDOTE** (a story, a testimonial). Ignore anecdotes. A METRIC from a transcript is a reference, never a fact about us.

## 2. Gate 1: compliance (hard fails, no appeal by the Lead)
Reject any TACTIC that:
- mentions the foreclosure, a sale date or a lender to anyone not yet confirmed as the owner (CALL_SCRIPT_NEPQ.md NEVER SAY; 99.7% of our numbers are address-matched, not confirmed);
- steers a homeowner toward or away from bankruptcy, loan modification, reinstatement or counsel, or "casts doubt" on their plan;
- promises to stop a foreclosure or guarantees an outcome;
- pressures ("pin them down", false urgency, scarcity);
- records calls or uses a relative/neighbor script without the attorney's written yes (Florida is all-party consent, FS 934.03);
- needs compliance text not from `disclaimer.py`.
Rejected tactics are logged with the rule they broke. They are not "softened" into something similar.

## 3. Gate 2: fit with our own numbers
Name the funnel stage the claim targets: prospect -> contact -> real conversation -> appointment -> contract -> closed. Look at OUR measured conversion for that stage (board `_funnelStats`, `reports/`). Adopt only where our own number is weak. The source's method is the point, not its numbers: diagnose by stage, then change one thing.
- No data for that stage yet -> verdict MEASURE (add the counter first, change nothing else).
- Our stage is healthy -> verdict SKIP.
Minimum sample before judging any change: 100 dials or 30 real conversations, whichever the stage uses.

## 4. Route (one verdict per claim)
| Verdict | Meaning | Where it goes |
|---|---|---|
| REJECT | failed Gate 1 | ledger only |
| SKIP | not our weakness | ledger only |
| MEASURE | we lack the number | a fix item: counter or report, read-only, through `fix-agent`, QA-gated |
| ATTORNEY | wording we might use | proposed lines (wording only, marked NOT APPROVED) in `C:\Users\olqbb\projects\attorney-review\<slug>.md`, with explicit questions for the attorney |
| BUILD | approved, mechanical (UI, logging, report) | `fix-agent` branch, QA-gated, Lead merges |
Script wording changes never go to main without Alejandro's explicit yes AND the attorney's written approval. The cold opener changes only on an explicit yes from Alejandro.

## 5. Close the loop
After a change ships, log the stage's conversion before and after (same stage, >= minimum sample) in the ledger. Keep it only if it improved and no compliance signal got worse (opt-outs, wrong-number, complaints). Revert otherwise. Never ship two script changes that touch the same stage in the same window.

## Ledger columns
`date | source (title, speaker) | claim | type | gate-1 | stage | our baseline | verdict | owner | shipped | before -> after`

## Seeded history (verdicts already reached)
- Michael Mahon funnel/intro video: stage diagnosis method ADOPT as practice; "which of three paths" + cast-doubt intro REJECT (steers away from bankruptcy; foreclosure to unconfirmed owner).
- STEPS appointment video: sit-first, story, stay-after -> ATTORNEY (3 lines drafted); pin-down, steer-away, stop-the-sale REJECT.
- Daily 30-minute drill video: scoreboard + one-correction-at-a-time ADOPT as practice; call grading needs recording -> REJECT until attorney (FS 934.03); weekly self-review of last 10 outcomes -> MEASURE.
- Foreclosure setter/closer/relatives scripts: appointment logistics -> ATTORNEY; relatives script REJECT; unverified stats SKIP.
