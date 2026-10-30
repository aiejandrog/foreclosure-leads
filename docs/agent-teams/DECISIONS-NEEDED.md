# Decisions needed from Alex

Standing rule (Alejandro, 2026-10-29): nothing spends, sends, or changes a rule without Alex.
Merges (amended twice the same day; latest: "every pr draft goes thru qa and have qa make the decision"):
every PR and agent branch goes through QA, and QA decides MERGE or REJECT, LEGAL REVIEW PRs included.
The LEAD executes QA's MERGE by a normal push from a separate worktree (never the main checkout, never
force). Still Alex's: spend and rule changes that are not a PR (entries below). A task that touches
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
- Status: MOVED TO QA 2026-10-29 (Alex: QA decides every PR). Verdict recorded in tests/AGENT-QA-REPORT.md on the merge train.

### D2: Spend ~$0.02 of 2Captcha on one live Miami folio probe? (equity, 2026-10-29, E-b)
- Touches: spend (2Captcha), records_liens folio search behind DEALFLOW_OR_FOLIO=1
- Option A: run one probe on 1-2 leads to confirm the clerk's folio search label ("Parcel ID") and folio format | Risk: ~$0.02; it hits the Official Records site, which needs a working login (the subscription lapsed 10/26, see D4)
- Option B: do not probe; leave DEALFLOW_OR_FOLIO off | Risk: the folio search stays unproven against the live site; Miami common-name lien mismatches continue
- Recommendation: A, after D4. Full first pass afterwards is estimated at ~$1.22 for ~370 Miami leads (per-solve price from code; lead count estimated).
- Fallback in progress: flag stays off; code merges after QA PASS (off = byte-identical).
- Status: OPEN

### D3: Lift the refusal of scan-read Miami judgment figures? (equity, 2026-10-29, E-c)
- Touches: a rule (judgment_for_analyze refuses scan-read figures until a 12-case review)
- Option A: do the 12-case review, then let verified court-copy figures feed analyze() with DEALFLOW_COURT_JUDGMENT=1 | Risk: a misread scan becomes a quoted judgment; eqc.may_quote could turn true on a wrong number
- Option B: keep the refusal | Risk: DEALFLOW_COURT_JUDGMENT=1 changes nothing for Miami (every judgment is a scan), so Miami equity stays docket-only
- Recommendation: A, but only after the 12 cases are checked by a person against the PDFs.
- Fallback in progress: refusal unchanged; wiring merges after QA PASS behind the off flag.
- Status: OPEN

### D4: Renew the Miami-Dade Official Records subscription? (equity, 2026-10-29, E-d)
- Touches: spend (subscription; price not verified from code)
- Option A: renew | Risk: recurring cost; enables (b)/(c) live runs and nightly lien refresh
- Option B: do not renew | Risk: Miami lien chains stay frozen at the last pull; folio search can't run
- Recommendation: Alex to check the renewal price first; see docs/agent-team/EQUITY-LIVE-RUN.md on claude/agents-equity.
- Fallback in progress: none needed; nothing runs live.
- Status: OPEN
