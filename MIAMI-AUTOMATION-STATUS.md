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
| Fresh selection | Current auction status joined to case/folio; stale dates cannot imply sold | `miami_ranking`: the auction calendar (archive last_seen vs the newest scrape) joined to the docket status. A past date is `past_date_outcome_unknown`, a future date missing from the newest calendar is `dropped_from_calendar_unknown`, and only a certificate on the docket is sold. Appraiser owner (7 days), Tax Collector (30 days) and skip-trace (180 days) each carry their read date; a stale or missing one holds the case. Not yet run on the desktop's data |
| Docket completeness | Full OCS response preserved; independent coverage reconciliation or explicit unverified gap | Full response supported; completeness unproven |
| All document/page outcomes | Attachment inventory and source-bound page outcomes; no count-only completion | `document_coverage`: one row per expected attachment, each read / read_partial / fetched_unread / queued / failed / restricted / access_gap / not_enumerated / county_no_document, saved with every timeline. A restricted or refused court filing stays a gap; a stored public Official Records copy of the same instrument (prints this case number, same kind, recorded -3/+120 days) is linked as `same_instrument_unverified`, never counted as the court copy. Where none is stored, `alternate_copy_needed` names the book/page the docket cites. Fetching that copy automatically is not wired: it needs the clerk endpoints, reachable only from the desktop |
| Official Records relevance | Current parcel/title identity, capped search gaps and retained later instruments | A deed without this parcel's folio is kept as `legal_description_match_required` with its parties and the legal description it prints (a deed with another parcel's folio is kept as `folio_conflict`), never dropped and never the current deed on its own. Matching the legal description to the parcel is still a person's job; title discovery does not yet fetch unanchored deeds |
| Free-first reading | Embedded/layout OCR before vision; automatic cross-page extraction | Text and OCR rows are extracted and checked across page breaks with no transcription; vision buys the page before a total only when that total's own page cannot reproduce it and OCR shows money there. Replayed at $0 on the desktop 09-24: 2024-009959, 2022-012065, 6828 and 2023-020247 verify from saved text/OCR alone |
| Amount verification | All typed charges and credits sum exactly; printed subtotals match; consistent across outputs | One contract (`judgment_money`) on the OCR/text, vision, saved-vision and timeline paths: exact cents, credits subtract, rates kept and reported, subtotal membership explicit or rows-above, no subset search, no tolerance. Replayed at $0 on 09-24: 258 saved totals in 78 cases, 65 verify (17 across a page break); nothing that verified earlier stopped verifying. A total that fails names each printed subtotal its rows do not reproduce and by how much |
| Current title | Continuous evidence-backed conveyance chain, entity/probate uncertainty explicit | `present_title.ownership`: the newest folio-anchored deed as a candidate, a chain-of-title link per consecutive deed pair (continuous / names_differ / court_transfer_not_compared / unknown), and `possibly_conveyed_later` when an unanchored later deed names the current grantee as grantor. Entity grantees get a Sunbiz record (`--sunbiz`, free, cached 30 days) with title and contact authority `not_established` and `call_ready` False. Death and probate stay quoted flags, never findings |
| Liens | Obligation, identity, attachment, amendment and satisfaction links; no missing-release inference | `present_title.claims`: one row per instrument, labelled by parcel link (folio_matched / subdivision_only / name_search_only) and by whose name it was found under (current_grantee / prior_title_party / defendant_not_on_title / lead_owner_not_on_title / other_name). `debt` is always `not_established`; no satisfaction found reads `no_satisfaction_found_not_proof_open`. A claim under a historical grantee's name is kept and never counted against the current owner. Attachment by legal description and amounts from the recorded body remain open |
| Timeline | Document-supported scope, amendments, vacatur, stay/relief and sale status | Docket-index reconciliation: each final judgment is operative, superseded, vacated (or partially), satisfied (or partially) or unclear, with the entry that changed it; a controlling judgment is named only when exactly one is operative. Stays carry a history (stayed, relief, reinstated, bankruptcy dismissed). A dismissal naming some defendants is party-limited. A past sale date without a certificate is `unknown_no_certificate`. A filing that carries a judgment copy (motion, notice, memorandum, affidavit…) is classified by its description, not the attached body; an image-less plain judgment on the same day as an imaged one is `docket_duplicate`. Judgment BODIES are not yet read for scope; the index can be wrong |
| One-command orchestration | Durable step leases, before-call reservations, idempotent restart and refresh | `run_documents --backfill --timeline [--collect-dockets]`: per case, a documents step then a whole-case timeline step, each recorded in the one backfill checkpoint with its own fingerprint; a restart resumes at the first unfinished step. Both steps draw on one vision ledger and one per-case share; reservations are durable before each call, cached page reads are free, unsettled calls are never retried. Title discovery and the owner-token worker stay separate commands (they spend captcha, which the backfill refuses) |
| Spend isolation | Fixed batch roster; protected pending-case shares; common paid-call controls | Per-case shares on the vision paths (run_documents, backfill, timeline); run_documents --token-budget now solves only through the real-balance PaidCutoffSolver (one try, free browser first) |
| Five-case replay | Reproduce prior evidence without hand-selection/transcription within approved cap | Passed at $0 on the desktop (09-24, four rounds), no hand selection or transcription; see "Five-case acceptance" below |
| Twelve-case review | Explicit review-policy acceptance and unattended evidence report | Not passed; no gate removed |

## Five-case acceptance (09-24, desktop, $0, saved evidence only)

| Case | Verdict | Evidence the code produced |
|---|---|---|
| 2024-014878 | supported | $1,746,032.70 verifies on court:232820355:1 pp. 2-3 (13 rows; per-diem 645.06 and 330.00 kept out). Controlling judgment #82 (232820355); #57 is a docket duplicate, #56 superseded by #67 and #82. Sale 2026-09-28 |
| 2022-012065 | supported | $373,482.79 verifies on court:231714504:1 pp. 2-4 (section total $48,738.51 and a running subtotal both reproduced). Controlling #174 (231714504) |
| 2024-009959 | supported (amount and posture) | $555,499.25 verifies on court:231457022:1 pp. 1-2. Controlling #79 (231457022). Contact authority for the estate is not established |
| 2023-020247 | supported | Stay in effect: #125 (233382143) p4 reinstates it. $305,151.92 verifies on both copies of 224002597 pp. 1-2. Controlling #91 (224002597) |
| 2018-026274 | conflicted; amount incomplete | Stay #93 (206433383), no relief order found, vs amended judgment #140 (232632335) and sale notice #143 for 2026-09-28. $785,670.31 does not verify: the judgment's own printed interest subtotal $225,243.83 is $0.60 below its eight yearly rows ($225,244.43) |

2024-009959's and 2022-012065's amounts were first produced by an agent transcribing pages; they now
come from the saved text and OCR alone. The verdict column is written from the code's output by
hand: no module emits supported / incomplete / conflicted yet.

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

Priority 2 is wired and proven at $0 on the five pilot cases (see "Five-case acceptance").

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
  is `unclear`, not a guess. The 2024-014878 and 2023-020247 acceptance runs passed on the desktop's
  saved dockets on 09-24 (see "Five-case acceptance").

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

Priority 6 is wired as a report; nothing here changes equity or the board.

- `miami_title_parties` keeps unanchored deeds, builds the chain of title and questions the
  current deed when a later unanchored deed conveys away from its grantee.
- `miami_present_title.present_title` is saved as `present_title` in every title-discovery report
  and dossier (live and `--report-only`).
- `sunbiz_entities` wraps `llc_officers._lookup` (now with an injectable fetch) so an unreachable
  registry is an `error`, never `not_found`. Trusts, estates and institutions are
  `not_applicable`. Officers and the registered agent are people to research, each labelled with
  the limit of what the filing shows. `--report-only --sunbiz` reads the cache and makes no request.
- Not closable here: matching a legal description to a parcel automatically (condo unit and
  plat-lot descriptions vary too much to decide without a person), and any statement that a debt
  is open. Both stay explicit.

Priority 7 is wired as a daily report; it does not feed the board.

- `python -u miami_ranking.py --all [--refresh-appraiser] [--refresh-tax]` writes
  `reports/miami-ranking-<date>.json` and `.md`: qualified cases ranked by sale date, then fewest
  open gaps; every other case held with its reasons; and every fact that changed since the previous
  report. Without the refresh flags it reads caches only. All sources are free public pages.
- Held unless: sale scheduled on the newest calendar (or no sale date yet), docket status clear with
  one controlling judgment, no stay in effect, ownership `candidate`, appraiser owner fresh, `clear`
  and matching a deed grantee, taxes read within 30 days, and a fresh phone traced for a person on
  the current deed. An entity-only owner, or a number that belongs to an LLC officer, is never
  call-ready.
- Equity is not an input (#51 owns it). Tax amounts and certificates are reported as facts with no
  priority or survival conclusion. "Qualified" is an evidence statement: opt-out, DNC and send-time
  gates are untouched and still decide every contact.
- Not closable here: a sale that happened but whose certificate has not reached the docket reads as
  `past_date_outcome_unknown` until it does, and a calendar row that disappears cannot be told apart
  between cancelled and reset without the docket.

The full goal remains active until the acceptance matrix is evidenced end to end.
