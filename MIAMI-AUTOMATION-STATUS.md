# Miami unattended analyzer: active implementation goal

The goal covers selection, fresh auction facts, full fetched docket inventory,
document/page acquisition and reading, exact amount verification, current title,
instrument-linked liens and orders, timelines, persistent orchestration, and
shared spending controls. This is not complete.

## Non-negotiable boundaries

- Miami only; integration branch only. No sending, nightly changes, equity writes,
  refresh-dealflow.bat edits, or records_liens dedupe/clear-rule changes.
- No subscription reader. Paid execution requires a fresh explicit authorization;
  the previous five-case vision allowance is not reusable authorization.
- Restricted or contradictory evidence remains incomplete/conflicted, never
  silently converted into a complete or clear-title result.
- Reuse document_collectors, document_store, document_queue and persistent budget
  accounting rather than build a competing collection system.
- A rank is not contact clearance. Current suppression and authority remain
  independent gates.

## Acceptance matrix

| Requirement | Evidence needed | Current state |
|---|---|---|
| Fresh selection | Current auction status joined to case/folio; stale dates cannot imply sold | Outstanding |
| Docket completeness | Full OCS response preserved; independent coverage reconciliation or explicit unverified gap | Full response supported; completeness unproven |
| All document/page outcomes | Attachment inventory and source-bound page outcomes; no count-only completion | `document_coverage`: one row per expected attachment, each read / read_partial / fetched_unread / queued / failed / restricted / access_gap / not_enumerated / county_no_document, saved with every timeline. A restricted or refused court filing stays a gap; a stored public Official Records copy of the same instrument (prints this case number, same kind, recorded -3/+120 days) is linked as `same_instrument_unverified`, never counted as the court copy. Where none is stored, `alternate_copy_needed` names the book/page the docket cites. Fetching that copy automatically is not wired: it needs the clerk endpoints, reachable only from the desktop |
| Official Records relevance | Current parcel/title identity, capped search gaps and retained later instruments | Candidate-only matching remains |
| Free-first reading | Embedded/layout OCR before vision; automatic cross-page extraction | Text and OCR rows are extracted and checked across page breaks with no transcription; vision buys the page before a total only when that total's own page cannot reproduce it and OCR shows money there. Not yet replayed on Walker and Blue Water |
| Amount verification | All typed charges and credits sum exactly; printed subtotals match; consistent across outputs | One contract (`judgment_money`) on the OCR/text, vision, saved-vision and timeline paths: exact cents, credits subtract, rates kept and reported, subtotal membership explicit or rows-above, no subset search, no tolerance. Not yet replayed on Walker and Blue Water |
| Current title | Continuous evidence-backed conveyance chain, entity/probate uncertainty explicit | Historical deed candidates only |
| Liens | Obligation, identity, attachment, amendment and satisfaction links; no missing-release inference | Candidate lists, not proven balances |
| Timeline | Document-supported scope, amendments, vacatur, stay/relief and sale status | Docket-index reconciliation: each final judgment is operative, superseded, vacated (or partially), satisfied (or partially) or unclear, with the entry that changed it; a controlling judgment is named only when exactly one is operative. Stays carry a history (stayed, relief, reinstated, bankruptcy dismissed). A dismissal naming some defendants is party-limited. A past sale date without a certificate is `unknown_no_certificate`. Judgment BODIES are not yet read for scope; the index can be wrong |
| One-command orchestration | Durable step leases, before-call reservations, idempotent restart and refresh | `run_documents --backfill --timeline [--collect-dockets]`: per case, a documents step then a whole-case timeline step, each recorded in the one backfill checkpoint with its own fingerprint; a restart resumes at the first unfinished step. Both steps draw on one vision ledger and one per-case share; reservations are durable before each call, cached page reads are free, unsettled calls are never retried. Title discovery and the owner-token worker stay separate commands (they spend captcha, which the backfill refuses) |
| Spend isolation | Fixed batch roster; protected pending-case shares; common paid-call controls | Per-case shares on the vision paths (run_documents, backfill, timeline); run_documents --token-budget now solves only through the real-balance PaidCutoffSolver (one try, free browser first) |
| Five-case replay | Reproduce prior evidence without hand-selection/transcription within approved cap | Replay tool built; not yet run on the saved pilot evidence |
| Twelve-case review | Explicit review-policy acceptance and unattended evidence report | Not passed; no gate removed |

## Implementation sequence

1. Docket-first prioritizer and fair persistent per-case spending allocation.
2. Automatic cross-page typed money extraction and one verification contract.
3. Instrument-scoped amendments, vacatur, satisfaction and stay reconciliation.
4. Connect these to existing queues through one resumable entry point.
5. Authorized session/alternate-copy coverage and source completeness reconciliation.
6. Current-title, entity, probate and defensible lien identity/linkage.
7. Fresh external facts, conditional ranking, unattended reporting and change alerts.

## Current increment

Priority 1 is wired, not yet proven on the pilot evidence.

- `document_prioritizer.recorded_read_order` ranks a case's stored Official Records documents
  for PAID reading before any call: the page prints this case number (tier 0), its recording
  date lines up with a docket judgment entry (tier 1), otherwise tier 2. It refuses to pay for a
  page printing another case number, anything recorded before the case-number year, and
  satisfactions, and names each refusal. `timeline_read_order` orders the whole-case timeline's
  amount reads by the docket plan. Neither decides which judgment controls; later docket orders
  are listed beside each judgment for review.
- `document_case_budget.CaseAllocator` splits one cumulative cap over a fixed roster. A case may
  spend its own share plus what finished cases left, never a pending case's share. Reservations
  record their page, and an unsettled paid call is a named `uncertain_paid_call` gap, never
  retried. Wired into `run_documents` (nightly and backfill) and `run_case_timeline`.
- `replay_paid_selection.py` replays selection and shares over saved evidence at $0. It has not
  been run on the five pilot cases: their evidence is on the desktop.

Priority 2 is wired, not yet proven on Walker and Blue Water.

- `judgment_money` is the one exact-cents check. A printed total is verified by one contiguous
  run of printed rows ending at it, which may start up to two pages earlier; every row in the run
  counts, credits subtract, rates are kept out and reported, and every printed subtotal must equal
  its members (the reader's ids, continued from the page before when they open the page, or for
  text the rows directly above it). A run cannot cross an unread page, an unlabelled figure or a
  different total; two runs that differ by more than zero rows are ambiguous. Vision rows must
  cover every row on the total's own page.
- The retired 2-to-6-figure subset search no longer admits anything (it could hit a five-figure
  total by coincidence and never knew which figure was which). `labeled_sum_check`,
  `vision_candidates`, `saved_vision_candidates` and the timeline's `read_amounts` all call the
  same contract; timeline figures inside a verified table are `in_verified_table`, still not an
  award or an equity input.
- `replay_money_check.py --all [--grep NAME]` re-checks every saved text and vision reading at $0
  and reports, per total, the run, pages, credits, rates, subtotal membership, and where the
  retired subset search's verdict differs. It has not been run: the evidence is on the desktop.
- Not closable in code alone: a credit whose parentheses AND label OCR both lost reads as a
  charge; the check refuses that table rather than guessing, so it stays unverified until vision
  or a person reads it. The vision instruction was deliberately not changed (it is part of every
  cached read's key, and changing it would re-bill pages already paid for); it has no `credit`
  kind, so a vision credit verifies only when the reader keeps its minus sign or parentheses.

Priority 3 is wired on the docket index, not yet on judgment bodies.

- `miami_case_timeline.reconcile_judgments`: newest is not controlling. An amended/corrected/
  substituted judgment supersedes the judgment whose date it cites, or the only operative one; a
  vacatur and a satisfaction act the same way; a supplemental fee/cost judgment adds to one and
  replaces nothing; two unlinked plain judgments are both `unclear`. `no_satisfaction_found` is
  stated as exactly that, never an open balance. `document_prioritizer.prioritize` carries the
  same reconciliation, and each paid-read candidate is labelled with its docket judgment's status
  (superseded/vacated/satisfied ones are bought after operative ones in the same tier).
- Stays: "order reinstating stay" and "order vacating the order granting relief" are
  `stay_reinstated` (they used to match relief_from_stay and read as the stay ENDING); a
  dismissed bankruptcy is `bankruptcy_dismissed` (it used to match order_of_dismissal and close
  the foreclosure). `stay_history` and `stay_in_effect` are in every timeline.
- Dismissals, vacaturs and satisfactions that name some defendants (by docket party name, or
  unknown tenant/spouse/heirs) and not the action or all defendants are `limited_scope`.
- Not closable from the index alone: what a vacatur or an amended judgment actually changes is in
  its body. When the index cites no date and more than one judgment could be meant, the answer
  is `unclear`, not a guess. The 6828 and McCray acceptance runs need their saved dockets, which
  are on the desktop.

Priority 4 is wired, not yet run end to end on the desktop.

- `run_case_timeline.timeline_case` is the one per-case timeline body; the standalone command and
  the backfill both call it, so they cannot drift.
- `run_documents --backfill --timeline` runs each case's documents step, then its timeline step,
  under one checkpoint (`_backfill_state`) and one `CaseAllocator`. `State.steps` records each
  step with its own fingerprint, so a crash mid-timeline resumes at the timeline without re-running
  the documents step or re-buying a page. A case with no saved OCS docket is a named
  `timeline:` gap unless `--collect-dockets` refreshes it (free).
- Not unified, on purpose: title discovery and `run_owner_tokens` spend captcha, and the backfill
  refuses paid token/name searches. They keep their own ledgers (`captcha/`, `title_discovery/`),
  so the total exposure of a night that runs all three is the sum of their caps, stated here rather
  than hidden. The standalone `run_case_timeline` keeps the `title_discovery/vision-budget.json`
  ledger the pilot's authorization was recorded in.

Priority 5 is wired for inventory and linking; retrieval of missing public copies is not.

- `document_coverage.coverage` states what became of every attachment the docket claims, in the
  timeline JSON and markdown. Restricted filings ("County login required", confidential, sealed)
  are their own state and stay gaps.
- Authorized alternate copies are public recorded copies already in the store, linked only when
  exactly one fits; two candidates are `ambiguous` and not chosen.
- Not closable here: the docket's own document counts cannot be verified against OCS, and a
  login-walled filing cannot be read without an account this project does not have. Both are
  written into every coverage block rather than hidden.

The full goal remains active until the acceptance matrix is evidenced end to end.
