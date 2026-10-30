# Agent team plan, round 2

Base: origin/main b829b54 (2026-10-29). Lead = main session. Rules: [BRIEF.md](BRIEF.md).
`gh` is not installed: branches are pushed, PR bodies live in docs/agent-team/pr/, Alex opens drafts.

## Tasks

| # | Task | Owner | Worktree | Branch | Base | Status |
|---|---|---|---|---|---|---|
| E-a | equity_state.card() + baked `eqc` + _eqcardtest.py | equity | fl-equity | claude/agents-equity | origin/main | todo |
| E-b | folio-keyed lien search, DEALFLOW_OR_FOLIO=1 | equity | fl-equity | claude/agents-equity | origin/main | todo |
| E-c | court-copy judgment, DEALFLOW_COURT_JUDGMENT=1 | equity | fl-equity | claude/agents-equity | origin/main | todo |
| E-d | EQUITY-LIVE-RUN.md | equity | fl-equity | claude/agents-equity | origin/main | todo |
| C-a | Call Mode equity card from eqc | callmode | fl-callmode | claude/agents-callmode | equity after E-a PASS | todo |
| C-b | Appointment gate / offer refusal | callmode | fl-callmode | claude/agents-callmode | same | todo |
| C-c | After-the-yes panel + _merge_note fix (LEGAL REVIEW) | callmode | fl-callmode | claude/agents-callmode | same | todo |
| B-a | board shows eqc state + may_quote | board | fl-board | claude/agents-board | origin/main | todo |
| B-b | _funnelStats/outcome_learn stages + Overdue list | board | fl-board | claude/agents-board | origin/main (stack on #364 if conflict) | todo |
| B-c | reports/funnel_truth.py, reports/followup_gaps.py | board | fl-board | claude/agents-board | origin/main | salvage staged |
| Q0 | ci_suite baseline; #364 / #381 retest | qa | fl-qa, fl-pr364, fl-pr381 | claude/agents-qa | origin/main | baseline running |
| Q1 | WIRING-MAP.md + _e2ewiringtest.py | qa | fl-qa | claude/agents-qa | origin/main | todo |
| L1 | CADENCE-STATE.md | Lead | fl-lead | claude/agents-lead | origin/main | written (diff + merge-tree verified) |
| I | integration (equity -> callmode -> board), never pushed | qa | fl-integration | - | - | after all PASS |

## File ownership (rule 8)
| Owner | Files |
|---|---|
| equity | equity_state.py, diligence_flags.py, diligence_gate.py, records_liens.py, miami_judgment.py, judgment_money.py, the equity-bake function in foreclosure_leads.py |
| callmode | call_mode.py (not the no/dnc/notint branches), CALL_SCRIPT_NEPQ.md, send_server._merge_note only |
| board | tracker_template.html, outcome_learn.py, reports/ |
| qa | tests/AGENT-QA-REPORT.md, _e2ewiringtest.py, docs/agent-team/WIRING-MAP.md |
| Lead | docs/agent-team/PLAN.md, CADENCE-STATE.md, docs/AGENT-BUILD-SUMMARY-<date>.md |

## Round 1 (cancelled 2026-10-29, superseded by round 2)
- claude/agents-cadence 32fba9f: followup_gaps.py now staged in fl-board; CADENCE-STATE draft kept as reference.
- claude/agents-phoneless 793af42 (phoneless_queue.py, PB-LP-STATUS-OPTIONS.md): parked, out of scope (phones are not this round). Not pushed.
- fl-bkstatus (uncommitted bk_lookup pull-failure status): parked, patch in salvage/. Not pushed.
- fl-afteryes / fl-wiring uncommitted WIP: patches in salvage/, reference for callmode / board.

## Needs Alex
- Open the draft PRs from the pushed branches (no `gh` on this machine).
- Cadence (DECIDED 2026-10-29: keep follow-ups going): merge #290 -> #293 -> #294 (hand-merge cadence.py) and set DEALFLOW_FOLLOWUPS_ON=1 for the Cadence task in the same step.
