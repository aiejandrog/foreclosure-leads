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
| Twelve-case review | Explicit review-policy acceptance and unattended evidence report | `case_verdict.py` emits the verdict per case - supported / incomplete / conflicted - from the saved timeline and dossier, at $0, with every reason in the producing module's own words. It restates states other modules computed and never upgrades one. Reproduces the five hand-written pilot verdicts (`_verdicttest`), but on one reading of a fork the container cannot settle: four of the five carry a same-day judgment entry the reconciliation infers is a duplicate without reading it, and if that twin is behind the county login rather than image-less, those four read incomplete instead. Has NOT been run on the desktop's real saved evidence, which is what decides it; no gate removed |

## Five-case acceptance (09-24, desktop, $0, saved evidence only)

| Case | Verdict | Evidence the code produced |
|---|---|---|
| 2024-014878 | supported | $1,746,032.70 verifies on court:232820355:1 pp. 2-3 (13 rows; per-diem 645.06 and 330.00 kept out). Controlling judgment #82 (232820355); #57 is a docket duplicate, #56 superseded by #67 and #82. Sale 2026-09-28 |
| 2022-012065 | supported | $373,482.79 verifies on court:231714504:1 pp. 2-4 (section total $48,738.51 and a running subtotal both reproduced). Controlling #174 (231714504) |
| 2024-009959 | supported (amount and posture) | $555,499.25 verifies on court:231457022:1 pp. 1-2. Controlling #79 (231457022). Contact authority for the estate is not established |
| 2023-020247 | supported | Stay in effect: #125 (233382143) p4 reinstates it. $305,151.92 verifies on both copies of 224002597 pp. 1-2. Controlling #91 (224002597) |
| 2018-026274 | conflicted; amount incomplete | Stay #93 (206433383), no relief order found, vs amended judgment #140 (232632335) and sale notice #143 for 2026-09-28. $785,670.31 does not verify: the judgment's own printed interest subtotal $225,243.83 is $0.60 below its eight yearly rows ($225,244.43) |

2024-009959's and 2022-012065's amounts were first produced by an agent transcribing pages; they now
come from the saved text and OCR alone. The verdict column was written from the code's output by
hand until 2026-09-25; `case_verdict.assess` now emits it, and `_verdicttest.PilotVerdictTests`
pins these five words to the states this table cites - with three limits stated below, because
earlier versions of this paragraph twice claimed more than the tests establish.

The sale under a stay is the first. Whether a foreclosure sale is currently scheduled is a
classification job, and `case_verdict` does not do classification: it answers live / unknown / none
from `miami_case_timeline`'s own labels, and where the classifier left a sale-worded entry
unlabelled - it labels "Notice of Foreclosure Sale" but not "Notice of Rescheduled Foreclosure Sale"
- the answer is "unknown", which holds the case as a gap. So a stayed case reads conflicted only when
the docket itself says a sale is running. Six earlier attempts here each broke the opposite way: by
status field (missed a stay filed after the notice), by entry kind (missed the rescheduled phrasings),
by regex over the entry's words (counted the judgment's own "shall sell the property", the petition
asking the court to stop the sale, and unruled motions and denials), and by reading only the labels of
the entries while short-circuiting on the derived `sale_held` summary - which hid a resale noticed
after a certificate, and read an entry's `description` where the producer classifies on description
plus the clerk's comments. Both of those were reachable false clean bills over a live stay; an
earlier version of this paragraph claimed no wrong verdict in either direction was reachable, which
is a claim the tests cannot establish and this one does not make. What the tests do establish is that
each phrasing they carry lands on a gap rather than a verdict.

A saved summary is only as good as the field it was built from. `sale_held` is computed upstream from
a bare `kind` as well (`sale_held`, for the clerk's bid and deposit rows and for the certificate), so
the same override emptied it: sale-day money rows on a calendar event made the block `None` and the
verdict `supported` over a sale the saved entries say was held, and a calendar-typed certificate made
the report print "no certificate of sale has followed" about a docket carrying one. The verdict now
re-reads those entries rather than trusting the summary. In the other direction, `index_kind` is read
ONLY where the override actually fired: `kind = body_kind or ik`, so wherever the producer opened the
document, `kind` is what the document says and the docket index is the weaker label - treating them as
co-equal let a docket line reading "Certificate of Sale" close a sale whose own document is a notice
of sale, and made a line reading "Notice of Filing Bankruptcy Petition" hold a case whose document
reads "ORDER DENYING MOTION TO COMPEL".

**Two producer fields were written and read by nothing**, which is its own class of defect: a field
saved and never consumed reads as handled. `attached_document_kind` carries the real label whenever a
dispositive document is filed under a covering title - "Notice of Filing Satisfaction of Judgment",
which `_FILED_ABOUT_RE`'s bare `notice\b` matches - and `reconcile_judgments`' `unmatched` list carries
every dispositive event it could not link to a judgment, which a satisfaction citing the mortgage's
recording date rather than the judgment's normally is. Each left a satisfied, vacated, dismissed or
sold case reading `supported` with the amount vouched for to the cent; the verdict now names both. The
producer-side halves are reported, not changed: `_FILED_ABOUT_RE` swallowing a bare "Notice of Filing
<dispositive document>", `_target` finding no target whenever a satisfaction's text cites any unrelated
date, and `scope_of`'s bare `\bonly\b` turning a whole-action dismissal into a limited-scope one.
Reading the fields the producer already saves is the cheaper fix and invents nothing. A third such
field is `limited_scope` on a dismissal: `_transition` declines to move the status for one, and unlike
a limited-scope satisfaction - which `reconcile_judgments` records as `partially_satisfied` - nothing
reconciles a dismissal, so a voluntarily dismissed action read `supported` with the status still
`judgment_entered`.

**One limit of the amount column, and it is not closable here.** An amount check's `entry_id` comes
from the middle segment of `court:<entry>:<document>` (`run_case_timeline` :53), so every attachment
filed under the judgment's entry produces checks carrying the judgment's entry id. An Affidavit of
Indebtedness filed as a second attachment has its own additive table and its own grand total. Nothing
in a saved timeline says which attachment on an entry IS the judgment, and a judgment filed with its
legal-description exhibit is the ordinary shape, so holding every multi-document entry as a gap would
make routine dockets `incomplete` for good - it was written that way for one commit and would have
flipped a pilot case. So it is reported twice and blocks nothing: the standing qualification carries
the general form, and a case whose judgment entry has more than one READ document gets a note of its
own naming those documents and the copy the figure verified on. Without the per-case note a reader
saw a confident figure with nothing on the page to say a sibling document could have produced it. The producer-side reason it
cannot be resolved here is reported: `miami_case_timeline` :349 merges the pages of all matched
documents on an entry and `_body_kind` reads `pages[:1]`, while `run_case_timeline.load_rows` :45
iterates `sorted(glob('*.json'))` over sha256 filenames, so which attachment supplies a
multi-document entry's label is arbitrary.

**Entries after the run's cutoff, and entries with no date at all.** `miami_case_timeline` skips both
where it decides a posture (:460, `reconcile_judgments` :755, `sale_held` :568) and compensates for the
undated half by forcing status `unclear` - but only for entries `_transition` recognises. `case_verdict`
mirrors the first half and deliberately not the second: an entry dated after `as_of` says nothing about
the case at it and is dropped, while an undated entry is never dropped, because it is more unknown, not
less, and the checks it reaches raise gaps rather than clear them. Copying the producer's skip whole
emptied a gap check for one commit: an undated "Notice of Rescheduled Foreclosure Sale", a title the
classifier does not label, stopped raising the unlabelled-sale gap and a case under a live bankruptcy
stay read `supported` (fourteenth review).

Two more of each half were open a round later. `sale_held` bounds its money-row scan by the cutoff
(:568) and its certificate scan (:574) does not, so a certificate dated after the run's own `as_of`
sets `sale_held['certificate']`; reading that bare field suppressed the held-sale gap and a sale the
clerk held before the cutoff, under a live stay, read `supported`. And the producer's one net for
undated entries - :519 forces status `unclear` - covers only the kinds `_transition` recognises, which
excludes `sale_bid` and `sale_deposit` (not in its map) and a limited-scope satisfaction or dismissal
(returned `None` at :248), while `reconcile_judgments` :753 skips every undated entry. An undated "Bid
Amount" over a read judgment, and an undated partial satisfaction of that judgment, both read
`supported` with nothing in `missing`. Each now raises a gap naming the entry and the label it carries
(fifteenth review).

**The sale reader is read for every case.** `_sale_state` answers the one question a caller most wants
off this page - is a sale still running - and for fifteen rounds `assess` consulted it only inside
`if stay is True`. On every other docket the answer was computed and discarded: a sale noticed for
2026-04-01, a cutoff in September, nothing on the docket cancelling it and no certificate, read
`supported`. Nothing else covered it, because `sale_outcome` is written only while the FINAL status is
`sale_scheduled` with a parsed sale date (:522) and a later judgment entry moves the status off it,
while the held-sale block needs the clerk's sale-day money rows, which a sale nobody has held does not
have. The gap is now raised whenever the producer's labels leave a sale live or unreadable and the
status is `judgment_entered` - the only settled posture that can sit over an unresolved sale, since the
producer's loop takes the latest transition, so `dismissed`, `satisfied_redeemed`, `sold` and
`sale_cancelled` all mean the thing that ended the case is newer than the sale entries. Reporting on
every kind would have held those four routine shapes `incomplete` for good (sixteenth review).

That scoping was right for `live` and wrong for `unknown`, and a round later both halves were
corrected. `unknown` is built from entries `classify` leaves `'other'` - "Notice of Rescheduled
Foreclosure Sale", "Notice of Cancellation of Foreclosure Sale" - and `_transition` has no entry for
`'other'`, so those entries produce no transition and are invisible to the status loop. They can be
arbitrarily newer than whatever set the settled kind, and a cancellation order between a notice and a
rescheduling flipped identical evidence from `incomplete` to `supported`. `unknown` is now reported on
every kind, with the completed-sale case excluded inside `_sale_state` by the producer's own
certificate label: a cancellation leaves room for a later notice, a certificate does not, and the
clerk's "Disbursement of Sale Proceeds" and "Surplus Funds from Sale" both classify as `'other'` and
both carry the word. That exclusion went wider than its own argument for one round and suppressed a
RESALE noticed after the certificate; the producer's own date parser separates the two, since a
proceeds entry prints no sale date after the certificate and a rescheduled-sale notice does.

The round after that closed the last hole of the same kind: `unknown` was reported on every status
kind but never COMPUTED for a live sale, because `_sale_state` returned `live` off the bare status kind
before any path that looks for an unlabelled sale-worded entry. So on the one posture where a later
unread entry matters most - the live sale itself - a docket where strictly LESS was known read
`supported`: a notice of sale followed by "Notice of Cancellation of Foreclosure Sale" or a
rescheduling to a different date, both of which `classify` leaves `'other'`, while the same entry after
a LABELLED cancellation order was already `incomplete` (eighteenth review).

That fix arrived floored at the wrong entry, and the round after corrected it in both directions. The
live-sale branches ask whether an unlabelled entry might be the cancellation or the rescheduling of the
sale now on the calendar, so an entry dated BEFORE the notice that put it there cannot be one - and a
routine docket carries such entries, since `classify` leaves "Order Setting Foreclosure Sale",
"Plaintiff's Bid at Sale" and "Statement of Amounts Due at Sale" as `'other'`. Flooring them at the
newest cancellation, which is nothing at all when no cancellation exists, held the ordinary live-lead
shape `incomplete` for good. They now floor at the notice, as the `opening` branch always did. And
`_sale_dates_of` now reads the producer's own `sale_passages` - the docket line plus every body line of
a READ page matching the producer's sale vocabulary, the field `_transition` itself takes `sale_date`
from - rather than the docket words alone, with the producer's reset vocabulary as a second
discriminator. Feeding the parser less than the producer gave it made a resale whose new date is
printed inside the document, and a rescheduling with no parseable date anywhere, both read `supported`
after a certificate. `sale_passages` was a producer field nothing in the repo read (nineteenth review).

The notice floor was still not enough, because the routine paperwork of a noticed sale is filed AFTER
the notice - a statement of amounts due in the run-up, a bid at the sale itself - so no date floor
reaches it. Those branches ask one narrow question, whether an entry could BE the cancellation or the
rescheduling of the sale now on the calendar, and every phrasing they exist for says so in words, so
they now require them (`cancel`, `vacat`, `withdraw`, `reset`, `reschedul`, `continu`, `postpon`). The
final branch, which asks about a FRESH notice after a cancellation, is deliberately not filtered
(twentieth review).

**A judgment superseded by one filed under a covering title.** `_FILED_ABOUT_RE`'s bare `notice\b`
matches "Notice of Filing Amended Final Judgment of Foreclosure", so the producer moves the real label
to `attached_document_kind` and leaves `kind` as `notice_of_filing`. `_transition` has no entry for that
kind and `reconcile_judgments` keys on `kind == 'final_judgment'` (:752), so the superseded judgment
stays `operative` and controlling and the verdict vouched for the OLD figure to the cent with the
amendment named nowhere. `attached_document_kind == 'final_judgment'` is still excluded - a judgment
body on page 1 of a motion, memorandum or status report is an exhibit, and holding those made routine
dockets `incomplete` for good (twelfth review) - unless the entry's own words match the producer's own
`_REPLACES`, which is the test `reconcile_judgments` itself uses for a replacement (twentieth review).

That covered only the half where the run OPENED the document. `attached_document_kind` is written only
then (:353); when nobody opened the filing - the ordinary case behind the county login - `classify`
falls back to a COVER label, `notice_of_filing` / `certificate_of_service` / `affidavit`, which names
the envelope and not the subject. None of those is in `DECIDING_KINDS`, none was in `UNLABELLED_KINDS`,
`_transition` has no entry for any of them and `reconcile_judgments` keys on `kind == 'final_judgment'`,
so the unread half reached no check at all: a cover-titled satisfaction, vacatur, dismissal, certificate
of title or notice of sale read `supported` with the amount vouched to the cent, while the very same
entry with its document read was already `incomplete`. The docket where LESS was known was the clean
bill again. `_cover_subject` now strips the cover head with the producer's own `_FILED_ABOUT_RE` and
re-runs the producer's own `classify` on the rest - the two functions composed the way the producer
composes them when it does have the document - and the cover labels joined `UNLABELLED_KINDS` so a
cover-titled SALE subject routes through `_sale_state`'s scan and keeps the floors and closing-word
guards three rounds were spent calibrating (twenty-first review).

One cover head still escaped, because the producer's own two functions disagree about it: `classify`
(:158) lists `certificate of (?:service|mailing|compliance|filing)` and `_FILED_ABOUT_RE` (:275) leaves
`filing` out. So "Certificate of Filing Satisfaction of Judgment" got a cover label from one and no
match from the other, the head was never stripped, and a satisfied, vacated, dismissed, sold or bankrupt
case read `supported` - with the stay column printing "none on the docket" over a live Chapter 13.
Fixed in `case_verdict` with a fallback for that one head, deliberately not in the producer: adding
`filing` to `_FILED_ABOUT_RE` would move the READ half's posture too, which is a producer decision with
its own blast radius. The misalignment is **reported and not changed**, and a test fails if the producer
ever aligns them, so the local fallback can then go.

The two halves also print different sentences now. On the unread half nobody opened anything - the
county may index no image at all - and the label comes from the docket TITLE through the producer's
classifier, so "the document under it reads as" told the reader a satisfaction of judgment had been
opened, and on a title whose read document is an actual certificate of service it said the opposite of
what the producer saved. That is CLAUDE.md's own rule: document metadata and keyword signals must never
be represented as documents read (twenty-second review).

**A satisfaction attached to a judgment row that is not the controlling one.** `reconcile_judgments`
writes a satisfaction onto the judgment whose date the entry's text CITES (`_target` :863 filters by
role, not by status) and onto that row alone (:821). `_judgment_record` read only the controlling row
and the `unmatched` sweep held only satisfactions with no target at all, so the middle case reached
nothing: an original judgment, an amended one, and a satisfaction citing the original's date read
`supported` with the amount vouched to the cent, while the same satisfaction on a docket carrying one
judgment - strictly less known - was already `incomplete`. The limited-scope variant was quieter still,
since `_transition` returns None for it and even the posture word stayed `judgment_entered`. The test
suite asserted the opposite as settled fact ("a limited-scope satisfaction is caught, because
reconcile_judgments records partially_satisfied"), which is why no fixture ever built the shape; that
comment is corrected (twenty-third review).

**`nonbankruptcy_stay`.** `classify` (:209) labels an order about a stay with no bankruptcy words, and
nothing else in the repo reads that label: no `_transition` entry, not in `stay_history`'s kinds, not in
`sale_held`'s, never in `reconcile_judgments`. And because the producer DID label it, `_sale_state`'s
unlabelled scan could not see it either, so an "Order Staying Foreclosure Sale" filed after a notice of
sale left the status `sale_scheduled` and the case `supported`, over a court order staying that very
sale. The entry fell between labelled and consumed; it now raises a gap naming the producer's own label
- but only where no posture-deciding entry outlives it. That check shipped without a floor for one
commit, and `classify` reaches `order.*stay` before `order.*motion`, so an "Order Granting Motion to
Stay Discovery" from 2024 held a docket whose own later entries are a final judgment, a noticed sale and
a certificate of title `incomplete` for ever. An older one is a note.

**A second judgment row the reconciliation left operative.** `reconcile_judgments` (:758) tests
`_ADDS_TO` before `_REPLACES` over `operative_text` + `description` + `comments`, so an entry whose
words say BOTH "Amended Final Judgment of Foreclosure" AND "awarding attorneys fees and costs" is typed
`role='supplemental'`: that branch only records `adds_to` and never calls `_target`, so the judgment it
amends is never marked `superseded`, and :832 keeps supplemental rows out of `operative` - leaving the
ORIGINAL judgment controlling with its figure vouched to the cent and the amendment named nowhere.
Deleting the fee words from the same docket line, knowing strictly less, was already `incomplete`. A
self-declared amendment (the producer's own `_REPLACES` over its own docket words) now holds the case; a
genuine supplemental judgment for fees is a note, since the Amount column is then the controlling
judgment's alone and understates the total (twenty-fourth review).

**Two producers disagreeing about whether a filing was read.** `document_coverage` :152 emits
`restricted_likely` for a docket-linked document the docket counts as 0, and it does so BEFORE it looks
at the rows actually acquired, while `build_timeline` :417 sets `image_status` `'read'` for the same
entry once its pages are read. So once one of those filings is obtained the two disagree for good, and
the report printed "behind the clerk's login" beside "amount verified to the cent" on that entry's own
court copy - a sentence the same file refutes - and held the case `incomplete` however much was read.
The disagreement is now stated in both modules' own words. Upgrading the state to `read` would not be
this module's to do; the branch order in `document_coverage` is **reported, not changed**.

**The report prints the posture and the cutoff.** `dismissed`, `sold` and `sale_cancelled` are all
`SETTLED_KINDS` and the producer folds none of them into the judgment row or the amount, so all three
are `supported` under this module's scope - and all three printed as a clean row with a judgment amount
beside them and nothing on the page saying the foreclosure was over. A `sold` case means a third party
holds the certificate of title. `docket_status` was in the JSON and not in the table; it now has a
column, and the run's `as_of` is printed under the title, since several gap strings say "this run's
as_of" and a replay at an old cutoff otherwise reads like a run made today.

The same round closed the other half of the eighth review's defect. `_transition` takes the sale date
from `sale_passages` - the docket line plus the body lines of READ pages - and falls back to the
entry's own date only for a calendar event (:264). A notice of sale whose description carries no
parseable date and whose document sits behind the county login therefore leaves `sale_date` None, and
:522's past-sale check is written `and status.get('sale_date') and ... < today`, so it never runs and
no `sale_outcome` is saved. The docket where LESS was known was the one reading `supported`: the same
docket with the date printed in its description was already `incomplete`. A status of `sale_scheduled`
with no parsed date is now a gap of its own. The acceptance fixture for 2024-014878 had hidden this
for sixteen rounds by omitting the `sale_date` key that `_transition` always writes for that status -
the same failure mode as the coverage fixture's missing `document` (thirteenth review).

**A regex given a narrower text than the producer it mirrors.** `_replaces` asks
`miami_case_timeline._REPLACES` - the producer's own regex - whether an entry's words say it replaces a
judgment, and that is right; it asked it about `description` + `comments`, and that was not.
`reconcile_judgments` builds its role text at :755 as `operative_text` + `description` + `comments`, and
`operative_text` is the TITLE OF THE DOCUMENT the run actually read (:363, `title or index_text`). So the
docket whose amending judgment was OPENED - the strictly stronger evidence - was the one reading
`supported`: `_ADDS_TO` matched "ATTORNEYS FEES" in the title the producer saw, the row went
`role='supplemental'`, the original judgment stayed controlling with its superseded figure verified to
the cent, and the amendment printed as a note saying it merely adds to the total. `_replaces` now
composes the producer's own text. A read title that says supplemental fees and nothing about amending is
still a note (twenty-fifth review).

**Two date floors of mine that read an undated entry as settled.** Both are the asymmetry
`_after_cutoff` exists to avoid, and both were added by the two rounds before this one.
`stay_floor` compared `str(entry.get('date') or '')` against the floor, so an UNDATED order the run
labelled `nonbankruptcy_stay` fell to a note and the case to `supported` - though nothing on a docket
can be shown to outlive an entry with no date at all. `_later_unlabelled` floored on the date too, so
once a cancellation or a notice set the floor, every undated sale-worded entry the classifier left
unlabelled was dropped: an undated "Notice of Rescheduled Foreclosure Sale" after a cancellation - the
exact phrasing that scan exists for, which `classify` leaves `'other'` - stopped raising its gap and was
named NOWHERE in the report, which is the fourteenth review's defect back through a different gate. An
undated entry now passes every floor. The twentieth review's calibration was the closing-word filter and
not the date floor, so the routine live-lead docket still reads `supported` (twenty-fifth review).

**The same finding in the sibling function, one round later.** The round before gave `_replaces` the
producer's composed text and left `_cover_subject` and the sale-word scan reading the clerk's line only.
`miami_case_timeline` sets `operative_text = title or index_text` (:363), so a bland "Notice of Filing"
whose first page reads NOTICE OF FILING SATISFACTION OF FINAL JUDGMENT takes its cover label from the
document's OWN title - and `attached_document_kind` stays None, because `notice_of_filing` is not in
`_DISPOSITIVE_BODIES` (:279) so :353 never fires. Asking `description` + `comments` found only "Notice
of Filing", `classify` gave `'other'`, and a satisfied, vacated, dismissed, sold or amended case read
`supported` with the judgment's figure verified to the cent and the entry named NOWHERE - not in
`missing`, not in `notes`, not on the page. The same words in the clerk's own line, with nothing opened,
were already `incomplete`. Every scan that asks what an entry IS now reads `_producer_text` - the
producer's own composition - and there is a third sentence for this third shape, because the run did
open the document and the unread half's "nobody opened it" is a claim the same file refutes, which is the
twenty-second review's defect mirrored. The same root cause reached `_sale_state`'s unlabelled scan: a
sale noticed under a read cover title beside a live bankruptcy stay said nothing at all (twenty-sixth
review).

**The round before's fix, half applied, and a floor narrower than its own comment.** Three things, none
a false `supported`. The cover sweep's new sentence was picked from where the SUBJECT STRING came from,
and what it asserts is whether anyone opened the document: `_body_kind` only accepts a first page whose
own title matches its whitelist (:231), so an OPENED filing whose page 1 is a cover sheet, a stamp or a
caption block keeps `kind_source` `'docket_text'` with `image_status` `'read'`, and the report told the
reader nobody opened a document the same timeline records as read - the twenty-second review's defect
mirrored a second time. `DOCUMENT_SOURCES` was defined three hundred lines away and read by nothing,
which is the test the sentence needed; there are now three states and three sentences. Second,
`stay_floor`'s own comment scopes it to "an order no posture-deciding entry outlives" while its list
held four SALE labels, so the docket carrying the STRONGER disposition was the one held: a cancelled sale
cleared a discovery stay and an order of dismissal, a voluntary dismissal or a satisfaction did not, for
ever, since reading the order's own pages still classifies its title `nonbankruptcy_stay`. The
bankruptcy labels stay out of that floor on purpose - a bankruptcy filed after a non-bankruptcy stay
order settles nothing about what that order stays. Third, `_cover_subject`'s parser guard returned a bare
`None` while both success paths return a tuple and the call site unpacks, so a missing parser would have
filed a file that parses fine under `broken` (twenty-seventh review).

**The same fix again, one level in, in both its halves.** `_was_read` tested `image_status == 'read'`,
and two other statuses only exist when the run HAD pages for the entry: `unreadable_pages` (:415, where
`failed` is a subset of `pages`) and `missing_attachments` (:426, which tests `and pages` explicitly).
This module's own NAMES table already calls `unreadable_pages` **part_read**. So a document whose page 2
failed OCR - strictly LESS known than one fully read - printed "nobody opened it", indistinguishable
from an entry nothing was fetched for. And there was a fourth shape: `kind_source` `'document'` means the
producer DID recognise a title on page 1 and labelled the entry from it, so when that title is a bare
cover ("NOTICE OF FILING") the subject cannot come out of it, the round before's flag was False, and the
sentence said no title was recognised about an entry whose `operative_text` IS the recognised title.
Four shapes, four sentences, each keyed on the fact it asserts: the read title that names the filing, the
read title that is a bare cover, the read document whose first page carried no recognised title, and
nothing opened (twenty-eighth review).

**A bankruptcy the file proves existed, read as no bankruptcy at all.** Two rounds in a row had found
no false `supported`, and both had been drawn to the newest code; this one went back over ground no
recent round touched and found one in the bankruptcy sweep, untouched since the sixth review.
`BANKRUPTCY_KINDS` lists the labels that RAISE a stay, which is right, and the stay-ENDING labels were
left out of the docket sweep entirely, which was not: an order granting relief from a bankruptcy stay, or
dismissing or discharging the bankruptcy, can only exist if the bankruptcy existed BEFORE it. :476 skips
undated and post-`as_of` entries before building `stay_history`, so on a docket whose only bankruptcy
entry was one of those three, dated after the cutoff, the history was empty, `stay_in_effect` None, the
sweep returned nothing, and the case read `supported` with the stay column saying "none on the docket"
and not one word about the bankruptcy anywhere on the page. A post-cutoff PETITION - which says strictly
less, since it does not establish that a stay was open at the cutoff - was already a gap. Undated, the
label was named in `missing` while the column still said "none on the docket". They now count when the
history took in no bankruptcy at all; where it holds the petition the order ends, the order is redundant
and does not hold the case, which is the sixth review's fixture. Two wordings went with it: the
two-producer disagreement check tested `image_status == 'read'` exactly, three hundred lines after
`OPENED_STATUSES` was added for that same question, so the docket whose page 2 failed OCR got the
"behind the clerk's login" sentence that block exists to prevent; and the round before's claim that
`unassessed_pages` means no page was read is false - :428-422 OVERWRITES `image_status`, `'read'`
included - so `_was_read` now reads the discriminator the producer saves in that entry's own gap row
(twenty-ninth review).

**A label the producer now saves, and a judgment status with no reader.** Two false `supported`, both on
ground no round had revisited, plus two sentences the round before's own fixes got wrong.

main's E1 work saves the label the calendar override replaces, in `pre_calendar_kind` (:393), and nothing
in the repo read it. On the one `_producer_labels` branch that can recover the label no other way -
`'document_passage'`, where the passage overwrote `operative_text` - a READ order saying the automatic
stay is reinstated returned the clerk's bland `index_kind`, so `stay_history` was empty, the bankruptcy
sweep never saw the entry, `_relabelled` skipped it as post-cutoff, and the case read `supported` with
the stay column saying "none on the docket" over a docket whose own read document puts a §362 stay back
in force. The saved label is now read, and it decides the SENTENCE only - holding on every saved label
would hold a notice of appearance, which is the eleventh review's regression. The old sentence "which is
saved nowhere" was true when written and is not any more.

The producer's judgment `status` vocabulary is operative / docket_duplicate_inferred / superseded /
unclear / satisfied / vacated / partially_vacated. `unclear` forces `controlling_entry` None, which is
already a gap; superseded and docket_duplicate_inferred are read; satisfied is read since the
twenty-third review. `vacated` had no reader at all - and `_target` filters candidates by ROLE, not
status, exactly as it does for satisfactions, so a vacatur citing the ORIGINAL judgment's date marks the
superseded row vacated and leaves the amending row controlling. An order VACATING a final judgment then
appeared nowhere in the verdict, with the amending judgment's figure verified to the cent, while the same
docket with a satisfaction in place of the vacatur - weaker evidence against the posture - was already
`incomplete`.

Two wordings from the round before: widening the coverage disagreement check to `OPENED_STATUSES`
swallowed the pairs where the two producers say the SAME fact (coverage's `read_partial` is the
timeline's `unreadable_pages`, `not_enumerated` is `missing_attachments`) while the sentence still
hard-coded `'read'`, so it named a status the file does not carry, claimed a disagreement that was not
there, and its `continue` ate the accurate "only partly read" line; and `_was_read`'s gap-row
discriminator matched the producer's GENERIC per-entry row, whose `pages` holds the pages that FAILED
and is empty when none did, so `1 not in []` made it claim a document was read when not one page of it
was - the twenty-seventh review's defect in the worse direction. Only the per-document rows carry the
discriminator, and every one of them has to exclude page 1 (thirtieth review).

**Thirty-first review.** Three producer fields with no reader here at all.

`attached_document_title`: on the one path where the producer moves the label to
`attached_document_kind` it also sets `title = None` (:358), so `operative_text` falls back to the
CLERK's line and the read document's own title survives in that field alone (:370). `_replaces` was
asked about the clerk's line, so the docket whose index read "Notice of Filing Amended Final Judgment"
was `incomplete` while the SAME read document under a bland "Notice of Filing" read `supported`, with
the superseded figure printed verified to the cent. It reproduced on every cover line the producer
routes that way - `Notice of Filing`, `Notice of Filing Order`, `Certificate of Service`, `Affidavit`.
`_replaces` now reads that title too, and the sentence prints it, so a person sees WHICH judgment the
cover carried. The twelfth review's calibration still holds: a plain copy of the same judgment filed as
an exhibit carries no replacing word in any producer string, and a page whose own title says PROPOSED
gets no `attached_document_kind` from the producer at all. The calibration test's fixture had used the
AMENDED page, which contradicted its own stated rationale; it uses a plain copy now, and the amended
page is pinned in the other direction.

`judgments['controlling_scope']`: :546 writes `judgment_scope` for every read final judgment and :549
copies the controlling one in. A judgment reading "as to Count II only", "in rem only" and "no
deficiency", and a docket defendant the judgment body never names, reached no page. They are NOTES and
not gaps, deliberately: the producer's own docstring says the field never moves the verdict, and
`scope_of` sets `limited` off a bare `\bcount\s+[IVX\d]+` (:590) that an ordinary judgment reciting
"Count I of the Complaint" trips, so a gap there would hold routine dockets forever.

The coverage disagreement check compared a PER-DOCUMENT state (`document_coverage` emits one row per
attachment) against a PER-ENTRY `image_status`. On an entry with two attachments, one read and one
behind the county login, both producers are right and neither disagrees - and the `continue` ate the
accurate "behind the clerk's login" line. The claim now requires that no row for the entry says a
document was opened; the twenty-ninth review's single-walled-row case still prints it. Recorded while
there: `AGREEING_STATES`' `read_partial` pair is dead at that site, since `read_partial` lives in
`PART_READ` and never in the three tuples tested, so only the `not_enumerated` pair is ever consulted.

**Thirty-second review.** The sale scans were reading a narrower text than the producer saved, and
the round before's own widening of `_replaces` had reopened a calibration.

`_sale_state`'s three scans asked `_producer_text` - `operative_text` + `description` + `comments` -
while the producer saves `sale_passages` (:395, :398): the docket line when it carries "sale", plus
every line of every READ page matching its sale vocabulary. `operative_text` is the page-1 TITLE line
and only when `_body_kind`'s whitelist recognised it (:234), so a read document titled STIPULATION
contributed no body text at all. A read stipulation saying "the foreclosure sale set for 12/28/2026
shall proceed as scheduled" was invisible to the scans while the producer's own field held the
sentence: `supported`, the amount vouched for to the cent, the entry named nowhere - and `supported`
again with a s362 stay in force. The docket whose CLERK line also said "sale", strictly more indexed,
was `incomplete`. The same gap claimed the opposite in another shape: the conflict sentence said "with
no cancellation or certificate on or after it" over a file whose own `sale_passages` hold a later line
saying the sale is cancelled. `_sale_dates_of` has read that field since the nineteenth review and its
docstring already says why - "Reading only the docket words gave the producer's parser a narrower input
than the producer gave it" - so this was that fix applied to the DATES side and not the WORDS side. The
calibrations hold: the judgment's own "shall sell the property" never enters, because the scans are
scoped to entries the classifier left unlabelled, and the twentieth review's routine sale paperwork is
kept out by the closing-word filter rather than by the narrowness of the input.

`_replaces` had been widened to search the whole of `attached_document_title`, and `_body_kind` (:236)
appends up to two following ALL-CAPS lines to a title, which is caption text. So "FINAL JUDGMENT OF
FORECLOSURE SUBSTITUTED PLAINTIFF US BANK NA" - a plain copy of the controlling judgment under a common
foreclosure caption - matched `substitut\w*` and reopened the twelfth review's exhibit calibration. The
title test is ANCHORED now, and that is the producer's own construction rather than a bound of ours:
the whitelist anchors the document noun at the start of the line after an optional
`amended |agreed |amended agreed ` prefix, so an amending signal the producer can put in an attached
title is always a prefix of it. The clerk's own line is still searched unanchored, because
`reconcile_judgments` searches its text unanchored.

Two smaller things from the same round. The `in_rem_only` note now prints the passage the producer
already saved, since `in_rem_only` is a bare `\bin\s+rem\b` search over up to 30,000 characters (:611,
:637) that also fires on "the motion for an in rem judgment was denied", and the note had asserted a
conclusion a reader could not check. And the unnamed-defendant note is a COUNT plus the entry id, not
the names: `defendants_not_named` comes from `docket_defendants` (:666), which includes individual
homeowner defendants, and for thirty-one rounds `case-verdicts.json`/`.md` carried only case numbers,
entry ids and amounts. The write is already guarded into `DEALFLOW_DIR` by `case_review.output_path`,
so naming them broke no rule, but it changed what this report carries as a side effect of a note, and
the count asks the same question.

**Thirty-third review.** Two of the round before's own fixes, and one of them was a false
`supported` again.

The anchor on `attached_document_title` rested on a premise that is false. `_body_kind`'s whitelist
(:234) anchors the DOCUMENT NOUN, and `order ` is one of the nouns it accepts, so the amending word
need not be first: "ORDER AMENDING FINAL JUDGMENT OF FORECLOSURE" - which the producer's own
`_REPLACES` matches, and which `classify` reads as `final_judgment` through its bare `final judgment`
substring (:213) - has the word at position 6 and was dropped. That put the thirty-first review's
false `supported` straight back, with the superseding order's own $225,000.00 on the page the run
read. "FINAL JUDGMENT OF FORECLOSURE RE-ENTERED" too. The title is SEARCHED again, and the one shape
the anchor existed for is excluded on its own terms: a `substitut*` naming a party role is not a
substituted judgment, which is the producer's own sentence (`superseded` at :730 reads "an
amended/corrected/substituted JUDGMENT replaces it"), and it only withdraws the signal when it is the
title's only replacing word. That exclusion is the one bound in this file that is not the producer's,
and it is written where it can be seen.

The `controlling_scope` notes cited one passage for two facts. `judgment_scope` appends the deficiency
window first (:635) and the in-rem window after it (:639), so `passages[0]` is the deficiency one
whenever a deficiency label matched - the ordinary pairing on a Florida foreclosure judgment. The
in-rem note was therefore handed the deficiency window, truncated before the words "in rem" appear,
so the passage offered as the check could not perform it, and the judgment whose body said MORE got
the wrong citation. Each note cites its own passage now.

Pointing the closing-word filter at `sale_passages` over-fired. That field takes EVERY body line
matching its sale vocabulary (:398) with no requirement that the line be operative, so the clerk's
routine "Statement of Amounts Due at Sale", whose body carries the ordinary conditional "In the event
the sale is cancelled or continued, these amounts must be recomputed", held a live noticed sale
`incomplete` for good - and the twin where that statement was never opened read `supported`, so
reading more made the verdict permanently worse. `_closes_a_sale` now asks whether a closing word is
the ACT, using the producer's own `_NOT_OPERATIVE_RE` (:294) the way the producer uses it, against the
text ending right before the match. Its vocabulary has no "in the event", so this file adds the
conditional openers that vocabulary needs; that is the second bound here that is not the producer's,
and it can only withdraw a hold from a phrasing that is not the act.

**Found while testing the above, reported and not fixed.** "ORDER GRANTING CORRECTED FINAL JUDGMENT"
on page 1 of an entry the clerk indexed "Notice of Filing" reads `supported` with the superseded
figure vouched for. It is a DIFFERENT path from the one above: `classify` reads that line as
`order_on_motion`, so the producer labels the entry from the document (`kind_source='document'`) and
`attached_document_kind` stays None, and neither the cover block nor `_cover_subject` reaches a
deciding kind. `_replaces` on the producer's own text is True, so the signal is there. Closing it
needs a producer-grounded answer to "does this order's own text say it replaces a JUDGMENT", and
"Order Granting Amended Motion" shows why the naive form over-fires. Left for the next round rather
than patched blind in the same pass, which is how the last eight rounds each produced the next one's
defect. The producer also saves `motion_disposition_passages` (:397) for exactly this kind, and
nothing here reads it.

**Thirty-fourth review.** The gap the round before left open, closed; and that round's own fix had
become a false `supported` of its own.

`classify`'s check list reaches `('order_on_motion', r'order.*motion|order (?:granting|denying|
awarding)')` at :210, six rows before its bare `('final_judgment', r'final judgment')` at :213, and
`order_on_motion` is not in `_DISPOSITIVE_BODIES` (:279). So a read page titled "ORDER GRANTING
CORRECTED FINAL JUDGMENT" is labelled by its DISPOSITION: :353 never moves the label to
`attached_document_kind`, the cover sweep's gate needs `index_kind` and only gets it when the calendar
override fired, `_FILED_ABOUT_RE` (:274) has no `order` noun so the head could not be stripped anyway,
and `reconcile_judgments` keys on `kind == 'final_judgment'` (:752). The superseded judgment stayed
operative and controlling and the case read `supported` with its figure verified to the cent, while the
same document titled "ORDER AMENDING FINAL JUDGMENT" - no more known - held. Four title shapes reach
it, and so does the clerk line alone on the unread half. `order_on_motion` is the only `classify` label
that both can carry a read judgment-replacing title and reaches no check here; every other route was
driven and is covered.

`_order_grants_a_replacement` restates and invents nothing. The producer's own granting head is
stripped off the string the producer took the label from, its own classifier is re-run on the
remainder, and its own `_REPLACES` must match in that same remainder - the composition
`_cover_subject` already makes, one check-list row along. Only the GRANTING dispositions: an order
denying, or a bare "order on motion", does not say on its face that a judgment was entered, and
deciding that it did would be a classification of ours. It is applied to `operative_text` AND the index
text, so the unread half cannot read better. The second trigger is
`motion_disposition_passages` (:397), which the producer saves for exactly this kind and nothing here
read - an OR-branch and never a precondition, because the producer saves it only from pages it READ, so
requiring it would let the less-read docket read better. The producer's granted/denied vocabulary is
split, so a DENIED line holds nothing. Calibration driven: "Order Granting Amended Motion", "...Motion
to Amend Complaint", "...to Amend the Case Style", "...to Substitute Party Plaintiff", "...for Summary
Judgment", "Order Granting Final Judgment" and "Order Denying Motion for Corrected Final Judgment" all
stay `supported`, and the twelfth review's exhibit calibration survives under all five covers.

The round before's `_closes_a_sale` had become a false `supported`. The producer's
`_NOT_OPERATIVE_RE` guards `until` because on the STAY side it reads "until the stay is reinstated";
borrowed wholesale for the sale side it dropped "the foreclosure sale set for 10/28/2026 is, until
further notice, cancelled" - which IS the cancellation - and the case read `supported` over a live sale
a read document says is off. That docket was `incomplete` at 03d7821 and `supported` at 2b3c469, so the
fix caused it. The idiom is removed from the window before the producer's guard sees it, which leaves
every other `until` the producer's to judge, and the window is now the SIXTY characters the producer
itself uses at its own call site (:310) - searching the whole prefix let a marker anywhere earlier
guard this verb, and `_sale_text` joins its parts with a bare space, so there is not even a sentence
boundary between the docket line and each body line.

And the round before's `_PARTY_SUBSTITUTION_RE` narrowing was applied on one side of a check and not
the other - the same family as most of these rounds. `_producer_text` carries the clerk's `comments`,
where OCS dockets put "Substituted Plaintiff: US Bank NA", and that branch had no narrowing at all, so
the boilerplate held a routine docket `incomplete` while the same docket without the comment read
`supported`. There is one `_replacing_words` helper now, used by every branch. Its role list also
missed two caption phrasings that held a routine docket for ever, since nothing a later run reads
changes a caption: "substitution OF counsel" broke the pattern on the `of`, and "substituted service"
is service of process and names no party at all.

**Reported, not fixed (producer surface).** A read page 1 titled "CORRECTED FINAL JUDGMENT OF
FORECLOSURE" under a bland "Notice of Filing" reads `supported`. `_body_kind`'s prefix list (:234)
allows only `amended |agreed |amended agreed ` before the document noun, so a line starting CORRECTED
is not a title at all: `body_kind` is None, `attached_document_title` is absent, and no producer field
carries the string, so nothing here can read it - the producer's two lists disagree with each other,
since its own `_REPLACES` (:697) does treat `corrected` and `re-?entered` as replacing words. The
prefix list should accept the words that regex recognises. Also: `sale_passages` (:398) is per-line and
keys on the sale vocabulary, so an OCR-wrapped cancellation whose verb falls on the next line ("...is
hereby" / "cancelled by agreement...") is saved nowhere and reads `supported`; that one predates these
rounds and is equally unreachable from here.

**Thirty-fifth review.** One finding, and it was the round before's own trigger reaching one of the
heads `classify` can emit and not the rest.

`('order_on_motion', r'order.*motion|order (?:granting|denying|awarding)')` at :210 is reached by a
SEARCH, so the label is emitted for far more heads than an anchored `order (granting|awarding)` covers.
Three shapes read `supported` with the superseded figure verified to the cent: "ORDER ON MOTION FOR
ENTRY OF AMENDED FINAL JUDGMENT" (no granting word at all), "ORDER GRANTING IN PART AND DENYING IN
PART MOTION FOR AMENDED FINAL JUDGMENT" (the head matched, then the remainder classified as `motion`
rather than `final_judgment`, so the test failed), and "AMENDED ORDER GRANTING MOTION FOR ENTRY OF
FINAL JUDGMENT" - where the head's own `amended\s+` prefix ATE the replacing word the test then went
looking for, which is self-inflicted. Hold the clerk line at "Notice of Filing Amended Final Judgment",
which the cover sweep holds on its own, and vary only whether the document was opened: reading it
relabelled the entry `order_on_motion`, took it out of the cover sweep, and the docket where MORE was
read came out clean. `_replaces` on the same entry was True in all three, so the gate was stricter than
a test this file trusts at three other sites.

The anchored head and the `_classify(rest)` step are gone. `_says_a_replacing_judgment` asks of each of
the producer's own strings for the entry whether IT carries the producer's `final judgment` row and a
surviving replacing word, per string rather than over the concatenation, and excludes a pure denial by
reading the producer's own two halves together: an order DENYING a corrected final judgment entered
nothing, while "granting in part and denying in part" entered something, so a denial only excludes when
nothing in the same string grants. Comments are included: excluding them was tried and reverted,
because `operative_text` is `title or index_text` (:364) and `index_text` is description + comments, so
with nothing read the comment is already inside the string - excluding it would have made the READ half
read better than the unread one, which is the shape all of these rounds have been chasing. Nine
ordinary Florida order titles stay `supported`, and both denial shapes and the partial grant are
pinned.

The review found nothing else wrong in the diff it was pointed at, with reproductions either way: the
widened `_PARTY_SUBSTITUTION_RE` can only ever suppress a `substitut*` hit and never one of the other
five replacing words, so it cannot drop a real replacement (ten real-shaped strings driven); and
`_closes_a_sale`'s sixty-character window is provably neutral for every guard, since each guard is
`$`-anchored with a reach of at most 57 characters, so the window cannot clip one the whole prefix
would have caught.

**Reported, not fixed.** Three `_closes_a_sale` over-fires that predate that helper and come from
`_CONDITIONAL_RE`'s and the producer's own regex reach rather than from these rounds: a period between
the marker and the verb blocks the producer's `[^.;]{0,40}$` ("In the event of a bankruptcy filing under
11 U.S.C. 362, the sale will be cancelled"), and "Plaintiff respectfully moves this Honorable Court for
an order to cancel the foreclosure sale" and "If the borrower reinstates the loan prior to the sale date
of 10/28/2026, the sale will be cancelled" both read as the act. All are clause-4 over-fires, none is a
false `supported`, and the producer flattens and abbreviation-normalises its text before applying the
guard (:308) where `_sale_text` does not. A round of its own.

**Thirty-sixth review.** Four findings. Three were one-change restatements; the fourth was the round
before's sentence asserting something the producer never recorded.

`_SALE_WORD_RE` - the gap check that notices a sale-worded entry the classifier left unlabelled - had
`sale` and not `auction`, while the producer's own saved sale vocabulary (:398) is
`sale|sell|auction|reset|reschedul*`. So "Notice of Cancellation of Foreclosure AUCTION", whose read
page says the auction set for 10/28/2026 is cancelled, reached no check at all and the case read
`supported` over a live sale with the entry named nowhere, while the same notice worded `sale` was
`incomplete`. "Notice of Rescheduled Auction" after a certificate of title did the same, which is the
nineteenth review's resale defect still live for the county's other word for the same event. `auction`
is in the list now. `sell` is deliberately not: a judgment's own "shall sell the property" reaches
these scans through a read cover's `sale_passages`, which is the risk :476 already records.

`_order_grants_a_replacement` read the producer's strings as `operative_text`, `description` and
`comments`. `operative_text` is `title or index_text` (:364), so once a document was read the
description and the comment were only reachable through `_index_text`, and reading them separately
tested the PAIR nowhere: a `final judgment` in the description with the replacing word in the comment
held only while nobody opened the document. That is the read half reading better, one door along from
the door the round before closed, and exactly what `_index_text`'s own docstring was written against.
`_index_text` is back in the tuple.

The same trigger read a denial as a grant. :397 saves a `motion_disposition_passages` row for this kind
from every read line that grants or denies a motion. An order titled "ORDER ON MOTION FOR ENTRY OF
AMENDED FINAL JUDGMENT" carries no denial word of its own, so the title branch fired - over the
producer's own saved row reading "Plaintiff's Motion for Entry of an Amended Final Judgment is hereby
DENIED" - and the report printed that the order's words say it grants a judgment, about a document that
entered nothing. A printed reason the producing module's own state contradicts is the thing this file
must never do. Where the producer saved disposition rows and none of them grants, the title branch is
now suppressed; a granted row beside a denied one still holds. That makes the read half read better
than the unread one, which is the one sound direction: the read page carries positive evidence that
nothing was entered, where the unread docket carries no evidence either way and holds.

And the sentence overclaimed twice. The trigger fires on any producer string carrying the `final
judgment` row and a replacing word, which is equally true of an order that merely RESTATES one ("Order
on Motion to Enforce the Amended Final Judgment" entered nothing and amended nothing), and there is no
restatement-only discriminator in the producer's saved state to tell the two apart - the review said so
plainly and would not invent one, and neither will this file. Worse, where the reconciliation itself
typed a judgment row `role='replacement'` (:758), the amending judgment IS of record, so "which no
summary this verdict rests on took in" was flatly false about the sharpest case the trigger catches.
The hold stands in both shapes, because which judgment the order acted on is genuinely unsettled here;
the reason now says only that the order's words name a final judgment that amends or replaces an
earlier one, and names the replacement judgment of record when there is one.

**Reported, not fixed.** The residual clause-4 calibration on that trigger - it holds a restatement
order as well as a real one - needs a discriminator nothing the producer saves provides, so it is a
round of its own rather than a guess made here. The five `_closes_a_sale` over-fires (three from the
round before, plus the two the producer's flattening would have caught) stay as recorded above.

**Thirty-seventh review.** Two findings, and both were the round before's own two fixes reaching one
side of a check and not the strictly weaker side. That is family (e) for the twelfth consecutive round.

The disposition guard asked its question of rows about a different motion. :397's test is per-LINE -
`\bmotion\b` AND the granted/denied vocabulary - and the producer records nothing about WHICH motion a
row belongs to, so the premise written into the round before ("the document itself says the motion was
denied and nothing was entered") was false about that field. One read line denying an unrelated motion,
"The Motion to Continue the Sale is hereby DENIED.", cancelled the hold that the SAME page without that
line read still raised: the docket where one more line was read vouched to the cent for the superseded
judgment and named the entry nowhere. Sharper still, the same page plus the decretal sentence that
positively enters the amended judgment - which carries no `motion`, so the producer saves no row for it -
also read `supported`. The guard is scoped now to rows that carry the producer's `final judgment` row and
a surviving replacing word, which are the two tests this file already trusts for that question. The
scoping fixes the other direction too: a denial of THIS motion beside an unrelated grant now exempts,
where the unscoped version held it.

The new `auction` word reached the scan and not the date parser. The producer seeds `sale_passages` from
the docket line only on `\bsale\b` (:395) and appends read body lines on the wider
`sale|sell|auction|reset|reschedul*` (:398), so an auction-worded clerk line is never in that field - and
`_sale_dates_of` read the passages *or* the docket line, so as soon as any read body line filled the
field the docket line was skipped entirely. A notice of a rescheduled auction after a certificate of
title therefore printed its date while UNREAD and lost it once the document was opened, the
completed-sale filter discarded the resale, and the case read `supported` with the entry named nowhere:
the nineteenth review's defect back through the very word the round before added. It reads both inputs
now. Five completed-sale calibrations - disbursement of sale proceeds, surplus funds, a statement of
amounts due at a past sale, disbursement of auction proceeds, and a read proceeds document - stay
`supported`.

The review cleared the rest with reproductions: `_index_text` back in the trigger's tuple is correct
both ways; no producer field bearing on a verdict is saved and consumed by nothing (`stay_passages`
reaches the verdict through `kind='stay_reinstated'` and `operative_text` at :385-388; `calendar_override`
and `judgment_scope` are report-only by their own docstrings); and no further weaker-vs-stronger
inversion in the sale label ordering.

**Reported, not fixed.** `_replacement_of_record` returns the first `role == 'replacement'` row
regardless of that row's `status`, so a replacement the reconciliation later typed `unclear`, `vacated`
or `superseded` is still printed "of record". Wording only - the hold stands in both branches and the
entry is named either way. And `_bankruptcy_entries` reads producer labels only, so a bankruptcy-worded
entry `classify` leaves `'other'` (a bare "Chapter 13 Plan") reaches no check; closing that is a new word
scan rather than a restatement, and it over-fires on any judgment reciting "no bankruptcy is pending", so
it wants a round that calibrates it deliberately.

**Thirty-eighth review.** Three findings, all three in the round before's own two changes. One was a
false `supported`; two were report-only and both new in that commit.

The scoped disposition guard asked the wrong question. Scoping it to rows that NAME a replacing final
judgment is not the same as scoping it to rows about the motion for ENTRY of one, and :397 records no
motion identity at all, so a read line denying a DIFFERENT motion that names the same judgment - to
vacate it, to enforce it, for rehearing of it, to set it aside, to cancel the sale it set - still
withdrew the hold that the same page without that line raises. The sharpest case withdrew a hold the
thirty-sixth review had decided deliberately must stand: an order on a motion to ENFORCE the amended
final judgment holds, and one read line denying that very motion made the case `supported` with the
superseded judgment's figure verified to the cent and the entry named nowhere.

The guard is gone, and that is a reversal of both rounds before it. Two consecutive attempts to read one
of those rows as the document's answer about the motion for entry each produced a false `supported`
within one round, because any such reading is a guess about motion identity the producer does not save,
and a read-side exemption is the one shape that can make reading a document produce a worse-informed
verdict. The hold now stands whatever the rows say, and the reason CARRIES them: the producer's own
"... is hereby DENIED" line is printed beside the sentence, which keeps the contract the guard existed
for - the reason never asserts a grant the producer contradicts - without the exemption that kept
breaking the other one. What is left is a clause-4 over-fire: an order whose read body denies the motion
is held, with the denial in front of the reader. Three pinned tests were rewritten to say this, and the
reversal is written into each of them rather than quietly applied.

`_sale_dates_of` appended the docket line unconditionally, so any dated clerk string reached the
producer's sale-date parser even with no sale word in it: a bare "Notice of Filing 07/07/2026" printed
its FILING date as that entry's sale date, while the same line without the date printed nothing. The
producer seeds that field only on its own sale vocabulary (:395, :398), so the appended line is gated on
that vocabulary now.

And `_index_text` joined `description` and `comments` including the empty one, while the producer drops
empties (:346). A comment-less entry - the common case - therefore carried a trailing space the
producer's string does not have, `text not in passages` never matched, and the docket line was parsed
twice: the report told the reader a noticed sale under a stay had two sale dates where the producer has
one. It composes the way the producer does now, which is also what its own docstring always claimed.

The review cleared the rest with reproductions: combining the two sale inputs cannot produce a false
`supported` through the producer's `reset or regular` preference, since any reset-context date implies
the reset word is inside `_sale_text` and the `_RESET_WORD_RE` branch rescues the entry; and the round
before's scoping did fix the direction it claimed.

**Thirty-ninth review.** Three findings, all three inside the round before's own three changes. That is
family (e) for the fourteenth consecutive round, and one of the three was a false `supported` produced by
a change written up as report-only.

The producer-vocabulary gate went into `_sale_dates_of` itself, and that helper is read on two sides: the
sale date this file PRINTS (:651) and the filter that keeps a resale noticed after a certificate of sale
from being discarded (:670). So the gate narrowed the HOLD as well. An entry whose sale wording lives
only in the READ body has sale passages with no date in them - the producer appends body lines on the
wider `sale|sell|auction|reset|reschedul*` (:398) but seeds the docket line only on `\bsale\b` (:395) -
so the clerk line is the only date the producer's parser can reach for it, and dropping it let "Amended
Notice 02/10/2027", read and saying the property shall be sold at public sale, read `supported` after a
completed sale with the entry named nowhere. That is the eighteenth review's defect back. The gate is the
caller's now: on by default at the print site, off at the hold. The round before's own defect - a
dateless "Notice of Filing 07/07/2026" printing its filing date - stays fixed, and three completed-sale
calibrations stay `supported`.

The `.strip()` added to remove the duplicate sale date re-made it. The producer saves `index_text`
verbatim (:346, :395), so comparing a stripped copy against `sale_passages` failed for any description
with surrounding whitespace, and for a whitespace-only `comments` field, which the producer's `if x`
keeps. The membership test is unstripped again and only the truthiness check strips.

And the printed disposition rows - which are the whole justification for keeping that hold - dropped the
denial. :397 saves one row per line in the court's own page order, which is not a priority, so `rows[:2]`
printed two earlier grants and hid the "no": reading more of the document hid the evidence. A single
300-character Florida recital lost its verb to the 200-character cut as well, because a Florida order's
disposition sits at the END of the sentence. Denials print first now, and `_disposition_excerpt` anchors
the cut at the end when the verb is not in the head.

**Fortieth review.** Three findings, all three inside the round before's own three changes - family (e)
for the fifteenth consecutive round. One was a false `supported`; two were the report not printing
evidence it holds.

The resale filter's own premise was false for an undated notice. It kept a later unlabelled sale entry
only when the entry printed a sale date past the certificate or carried `reset`/`reschedul*`. The
producer's `notice_of_sale` row (:214) labels none of "Amended Notice of Foreclosure Auction", "Notice of
Continued Foreclosure Sale" or "Notice of Postponement of Foreclosure Sale", so this filter is the only
thing that can hold them - and undated, all three read `supported` after a certificate of sale with the
entry named nowhere, while the same clerk line WITH a date was held. A resale's date is the ordinary
thing to be missing: it lives in the document behind the county login, which is `_RESET_WORD_RE`'s own
stated argument. The rescue is `_RESALE_WORD_RE` now (`reset|reschedul*|continu*|postpon*`, the last two
already this file's vocabulary for moving the sale on the calendar), plus `_SALE_NOTICE_HEAD_RE` for the
`auction` phrasing, which carries no resale word at all. That head rule is asked of the producer's title
strings only, never the body, and it excludes the clerk's post-sale paperwork by `_PROCEEDS_RE`, because
without that it held "Notice of Disbursement of Sale Proceeds" for ever. Eleven pieces of routine
completed-sale paperwork and the routine live-sale docket stay `supported`.

`_disposition_excerpt` protected the wrong verb. Anchoring the cut at the line's end only when NO verb
was in the head is fine until the head grants one motion and the tail denies the one that matters: one
Florida `ORDERED AND ADJUDGED` line routinely disposes of two motions in opposite directions, and there
the head-anchored cut printed the grant and dropped the "no" - the same sentence the round before's third
finding was about, one door along. The cut anchors on the MATCH now, denial first, which also reaches a
verb in the middle of a recital that neither a head nor a line-end anchor can.

And the duplicate sale date had a second source. The producer can save the same date in two passages -
the clerk's "on 12/28/2026" and the read notice's own "December 28, 2026" - so the docket where the
document was OPENED told the reader a noticed sale has two sale dates. `_sale_dates_of` de-duplicates in
the producer's own order; no caller depends on multiplicity, since the resale filter asks `any()`.

**Reported, not fixed (not this module).** `_watchdogtest.js`'s case "alerts: a fresh empty file stays
quiet" passes no `now`, so it reads the real clock against a `published_at` fixed at 2026-09-26T14:00Z
and starts failing thirty hours later, which is 2026-09-27 20:00Z. It is a time bomb in that test's own
fixture, it has nothing to do with this module, and until it is given an explicit `now` it reds
`ci_suite.py` for every branch in the repo.

**Producer line citations re-pointed.** main's E1 commit moved `miami_case_timeline` by fourteen lines in
its first half and about eighty in its judgments half, so every citation in this file and in
`case_verdict.py` was stale. 90 were remapped by matching each cited line's text to its new position and
spot-checked against the anchors; `judgment_money`, `document_coverage` and `_casetimelinetest` citations
were left alone.

**Reported, not changed (`miami_case_timeline`, not this module's surface).** :519 overwrites the
whole status when any undated dispositive entry exists, including a status already carrying one of the
three evidence-vs-evidence contradiction reasons. A docket with both a same-date conflict and an
undated dispositive entry therefore reaches `case_verdict` with reason "Undated dispositive entry
prevents reliable chronology", so the verdict is `incomplete` on a file that also holds a
contradiction. `case_verdict` restates the producer faithfully; the loss is upstream.

CLOSED in the thirty-first review. main's E1 work made the producer keep the attachment's own title in
`attached_document_title` (:370), which is what this item said closing it would need, and `_replaces`
and the sentence both read it now.

`_body_kind`'s title whitelist (:237) allows only the `amended |agreed |amended agreed ` prefixes, so a
page 1 reading CORRECTED FINAL JUDGMENT OF FORECLOSURE or RE-ENTERED FINAL JUDGMENT yields no
`body_kind`, hence no `attached_document_kind` and no `attached_document_title` - although `corrected`
and `re-?entered` are both in the producer's own `_REPLACES` (:697). That docket reads `supported` and no
change to `case_verdict.py` reaches it, because the producer saved nothing for it to read.

`reconcile_judgments`' `_ADDS_TO` matches a bare `attorney'?s? fees?` over `operative_text` plus
`description` plus `comments`, so a final judgment whose clerk comments merely mention attorney's fees
is typed `role: supplemental`, excluded from `operative`, and the case reads "no operative judgment"
and therefore `incomplete`. Also upstream, also reported rather than fixed here.

**The limitation behind most of this, stated plainly.** `miami_case_timeline` :394 does
`if e['calendar_event'] and e['kind'] != 'notice_of_sale': e['kind'] = 'hearing'`, so any docket entry
whose OCS eventType is a hearing loses its real label, and every summary the producer builds afterwards
is keyed on the label that is gone: `stay_history` and `stay_in_effect` (:476), `sale_held` with its
certificate and its sale-day bankruptcy list (:568, :574, :576), `_transition`'s status kind (:247) and
`reconcile_judgments` (:802). Ten review rounds each found this reaching one more summary than the last
round had enumerated, so `case_verdict` stopped chasing sites: where a posture-deciding label was lost,
it names the entry and holds the case, whatever consumed it. An earlier version of this paragraph said
the case is held "as a gap" as though that covered the whole override; it covered the stay path only,
and a vacated judgment, a satisfied one and an order resetting a sale each read `supported` past it.

`case_verdict` cannot do better than a gap here, and neither could a fix in the producer without
evidence this repo does not hold. A calendar event genuinely can be a hearing ABOUT a motion rather
than the thing itself, which is what :394 is for, and nothing in a saved timeline says which a given
entry is. Deciding it needs a count of how often OCS puts a hearing eventType on an order row, which
is the desktop's to measure; a read-only script for that is in the project files. The producer is
therefore left alone on purpose, not by the earlier reasoning in this paragraph, which said the fix
would move the §362 stay flags the board hard-gates on. That was wrong and was checked: the board's
`saleBkAct` / `sale_bk_active` is built by `sale_history.py` (:405-447) from its own fresh docket pull,
that module has no `eventType` reference at all, and nothing outside `case_verdict`, `miami_ranking`
and `document_prioritizer` reads the timeline's stay state.

2018-026274's amount reason is the second. The $0.60 breakdown in the row below is what the console
printed on the run that found it, and it does NOT reach a saved timeline: `verify_document` builds its
rows through `vision_rows`, which marks every row `explicit`, and `_resolve_subtotal` never returns
None for an explicit row, so the notes `disagreeing_subtotals` is collected from are never written on
that path. What the saved check carries is the reason "printed subtotal lacks valid members or
disagrees with its own items", which `judgment_money` raises both for a subtotal whose members could
not be read and for one whose members read fine and do not add up. `case_verdict` names that reason
and says the file cannot tell the two apart, so the amount reads incomplete - which is what this
table says - while the case is conflicted on the stay against the sale. The breakdown itself exists
in `miami_judgment`'s text path as `sum_check_disagreeing_subtotals`; joining the two is not done.

One caveat on that, because an earlier version of this paragraph overstated it. `reconcile_judgments`
reaches ONE operative judgment on 2024-014878 (#57), 2024-009959 (#79/#80), 2023-020247 (#91/#92)
and 2022-012065 (#174/#177) by inferring that a same-day entry is the same judgment listed twice -
its own reason ends "(inferred, not read)". Where the county indexes no document for that twin there
is nothing to read and the inference rests on the docket index, which the whole reconciliation rests
on; `case_verdict` notes it and the case can still be supported. Where the twin is behind the county
login a document exists that nobody read, and the uniqueness of the controlling judgment - which is
what "supported" is scoped to - rests on it, so the case reads incomplete. Which of the two each
pilot twin is, only the desktop's saved evidence says. If it is the login, four of these five
verdicts become incomplete: that is the finding for the acceptance run to report, not a rule to
loosen, and `_verdicttest` pins both outcomes so it cannot arrive as a surprise.

Two things that column does NOT mean:
"supported" is scoped to the controlling judgment, its posture and its amount, not to every
attachment on the docket (requiring that would make every case incomplete forever, since the clerk
publishes no pagination cursor); and no verdict is contact clearance - `miami_ranking.qualify`
remains the only gate on who may be called.

## 12-case verification defects (09-24)

Source: `verify-12/MIAMI-VERIFY-12-2026-09-24.md` in the project files. Fixed on this branch,
because each one is in code this branch adds or made worse by it:

| Defect | Fix |
|---|---|
| D5 other people's instruments corroborate this case's judgment | `document_prioritizer.case_tie()` tags every recorded row; only tier 0 (prints this case) or tier 1 (recorded -3/+120 days of a docket judgment) can set or corroborate the amount. The rest are listed under `judgment_amount_other_instruments` |
| D6 creditor-name hits flood the claims | `present_title` drops name-search-only hits on another name and counts them in `unlisted_other_name_hits` |
| D9 bankruptcy orders filed as "Notice of Filing:" | notices that carry bankruptcy context (chapter N, debtor, 11 USC, 362, bankruptcy court, US trustee) reach the stay history; reinstated, reimposed, chapter-N dismissed and discharged wordings are classified |
| D10 held sale missed | `sale_held()` reads Bid Amount (BIDSCV) and Mortgage Foreclosure Deposit (MFDPCV) entries: `sale_outcome = held_no_certificate_yet`, a bankruptcy filed the same day is named |
| D11 login-walled entries called "no image" / "no document" | a Judgment entry that counts 0 documents but links one is fetched; its gap is `login_required` / `login_required_likely` in the timeline and `restricted_likely` in coverage. A duplicate judgment needs the same day and the same docket code |
| D12 watermark-only pages marked read | `document_coverage.page_is_read()` needs 12 characters that are not watermark words; the timeline uses the same test |
| D14 stale timelines ranked | a timeline older than a day or without judgment and stay blocks disqualifies the case; `miami_ranking.py --refresh-timelines` rebuilds them first |

Fixed in the follow-up (main code):

| Defect | Fix |
|---|---|
| D8 exhibit page stamps and a condominium declaration followed as instruments | a run of stamps that steps with the pages is one instrument, its first page; declaration and plat recitals are not followed (only the text leading to a citation decides, so a mortgage on the same line is still followed). Both are listed in the walk's `not_followed` and left out of the dossier's `cited_but_not_fetched` |
| D7 and the D6 root: this case's own judgment counted as a claim | `document_walk.run_name_searches(this_case=...)` moves a judgment or lis pendens recorded at a docket book/page, or between this case's plaintiff and anyone and recorded near a docket judgment date (a lis pendens near the first docket entry), into `own_case_instruments` with `own_case` and `this_case`. Lender names made only of generic words never match |
| D3 capped or wrong-parcel searches | title discovery reports `search_capped`, `parcel_found` and `names_left_unsearched` (a name whose search failed counts as unsearched). The three rounds and the 500 cap stay: raising them costs owner-search tokens and waits for Alex |

Still open: D13's paid read of 2025-023462's recorded copy while its court copy had text. The
recorded-copy reader (`miami_judgment.run`) cannot see the court copies yet; checking that needs
the saved evidence on the desktop. Paid reads run only with Alex's go, so nothing spends meanwhile.
2023-020247's amounts need image OCR, and 2023-013492's unverified-extraction label is policy.

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
