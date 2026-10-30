# LEGAL REVIEW: send_server._merge_note keeps the newest after-the-yes save (D1)

## What it does, plain English
The /notes merge was fill-only for scalars: once a case had a `nextStep`, a newer one sent from the
phone never replaced it, and the activity-time rule could keep an old stage. Call Mode's new "After the
yes" panel (branch claude/agents-callmode) writes the stage (`status`), `stageAt`, `nextStep`, `next`,
`equityVerified`, `keepOrSell` and `afterYesAt` (epoch ms) as one unit. This change makes the unit follow
the larger `afterYesAt`: a newer save replaces the older one, an older push never walks a newer stage or
date back, `stageAt` keeps the earliest date per stage.

## Suppression is untouched in effect
DO NOT CONTACT and Dead are never overridden by this code, from either side, and a suppressed lead is not
handed a next step by the fill loop (the keys are skipped when the final status is DNC / Dead). Notes
without `afterYesAt` merge exactly as before. No other function in send_server.py changes. This is not a
send path, but `_merge_note` is the /notes write path and sits beside the opt-out ledger, hence LEGAL REVIEW.

## Tests
`DEALFLOW_DIR=<temp> python _mergenotetest.py` -> ALL PASS (12 checks: newer replaces incl. a later date,
older never walks back, stageAt earliest, stored/incoming DNC and Dead, no-afterYesAt notes unchanged).

Exposure: suppression (adjacent: /notes merge; no ledger writer changed), none for sending, stays, spend.
Flags: none (default behaviour only changes for notes that carry afterYesAt, which only the new panel writes).
Visible after the next refresh-dealflow.bat: n/a (bridge code; the send bridge self-restarts per CLAUDE.md).
Owned-surface bugs found: none.

Until D1 is decided, the callmode branch still works on its own, but saves made through the bridge will
NOT replace an older nextStep/stage (fill-only merge). The browser-side `_mergeLead` in
tracker_template.html is board territory and is not part of this PR.
