# Decisions needed from Alex

Standing rule (Alejandro, 2026-10-29): nothing spends, sends, or changes a rule without Alex.
Merges (amended the same day: "just merge it after QA approves"): every PR is a draft until QA passes;
then the LEAD (never a teammate) merges it to main by a normal push from a separate worktree, after
re-running QA's tests on the merged result. Still Alex's to merge: anything titled LEGAL REVIEW or with
an OPEN entry below. A task that touches
stay_gate.py, never_contact.json, DNC, opt-out, send_server.py send paths, text or email copy, or a
spend cap stops, writes its question here, and moves to its fallback. The Lead reads this file to Alex
at the end of every round.

Append only. Never edit or delete another entry. Lead commits this file and marks entries DECIDED.

## Entry format
### D<n>: <one-line question> (<owner>, <date>, <task>)
- Touches: <file:function and which trigger above>
- Option A: <exact change> | Risk: <what can go wrong, who gets contacted or what is spent>
- Option B: <exact alternative, incl. "do nothing"> | Risk: <...>
- Recommendation: <A/B and one-line why>
- Fallback in progress: <what the task does meanwhile>
- Status: OPEN | DECIDED <date> <choice>

## Decided
### D0: Keep automated follow-up emails going when #290/#293/#294 merge? (Lead, 2026-10-29)
- Status: DECIDED 2026-10-29: keep going. Set DEALFLOW_FOLLOWUPS_ON=1 in the same step as the #294 merge.
  Details: fl-lead docs/agent-team/CADENCE-STATE.md.

## Open
### D1: May the /notes merge let a newer after-the-yes save replace an older one? (Lead, 2026-10-29, callmode C-c)
- Touches: send_server.py `_merge_note` (the /notes merge; not a send path, but it decides status precedence next to DO NOT CONTACT, so treated as the DNC trigger)
- Option A: newer `afterYesAt` wins for nextStep / stage / equityVerified / keepOrSell; DO NOT CONTACT and Dead are never overridden; covered by a merge test | Risk: a bug here could walk a status back; the test pins DNC/Dead precedence
- Option B: do nothing | Risk: the first next-step date and stage saved for a lead stick forever through the bridge; later updates from Call Mode are silently dropped, so the board's after-yes counts and Overdue list go stale
- Recommendation: A. Without it the after-the-yes flow does not persist.
- Fallback in progress: callmode keeps the `_merge_note` change on its own branch claude/agents-mergenote (LEGAL REVIEW, not merged by Lead); the rest of Call Mode merges after QA PASS.
- Status: OPEN
