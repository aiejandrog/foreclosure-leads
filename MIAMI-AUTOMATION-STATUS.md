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
| All document/page outcomes | Attachment inventory and source-bound page outcomes; no count-only completion | Partial acquisition/read coverage |
| Official Records relevance | Current parcel/title identity, capped search gaps and retained later instruments | Candidate-only matching remains |
| Free-first reading | Embedded/layout OCR before vision; automatic cross-page extraction | Targeted agent transcription still required in pilot |
| Amount verification | All typed charges and credits sum exactly; printed subtotals match; consistent across outputs | Validator exists; extraction/output integration incomplete |
| Current title | Continuous evidence-backed conveyance chain, entity/probate uncertainty explicit | Historical deed candidates only |
| Liens | Obligation, identity, attachment, amendment and satisfaction links; no missing-release inference | Candidate lists, not proven balances |
| Timeline | Document-supported scope, amendments, vacatur, stay/relief and sale status | Index rules plus selected body evidence; incomplete |
| One-command orchestration | Durable step leases, before-call reservations, idempotent restart and refresh | Not yet integrated |
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

The full goal remains active until the acceptance matrix is evidenced end to end.
