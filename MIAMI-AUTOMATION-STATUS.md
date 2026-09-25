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
where it decides a posture (:446, `reconcile_judgments` :675, `sale_held` :544) and compensates for the
undated half by forcing status `unclear` - but only for entries `_transition` recognises. `case_verdict`
mirrors the first half and deliberately not the second: an entry dated after `as_of` says nothing about
the case at it and is dropped, while an undated entry is never dropped, because it is more unknown, not
less, and the checks it reaches raise gaps rather than clear them. Copying the producer's skip whole
emptied a gap check for one commit: an undated "Notice of Rescheduled Foreclosure Sale", a title the
classifier does not label, stopped raising the unlabelled-sale gap and a case under a live bankruptcy
stay read `supported` (fourteenth review).

Two more of each half were open a round later. `sale_held` bounds its money-row scan by the cutoff
(:544) and its certificate scan (:550) does not, so a certificate dated after the run's own `as_of`
sets `sale_held['certificate']`; reading that bare field suppressed the held-sale gap and a sale the
clerk held before the cutoff, under a live stay, read `supported`. And the producer's one net for
undated entries - :505 forces status `unclear` - covers only the kinds `_transition` recognises, which
excludes `sale_bid` and `sale_deposit` (not in its map) and a limited-scope satisfaction or dismissal
(returned `None` at :248), while `reconcile_judgments` :673 skips every undated entry. An undated "Bid
Amount" over a read judgment, and an undated partial satisfaction of that judgment, both read
`supported` with nothing in `missing`. Each now raises a gap naming the entry and the label it carries
(fifteenth review).

**The sale reader is read for every case.** `_sale_state` answers the one question a caller most wants
off this page - is a sale still running - and for fifteen rounds `assess` consulted it only inside
`if stay is True`. On every other docket the answer was computed and discarded: a sale noticed for
2026-04-01, a cutoff in September, nothing on the docket cancelling it and no certificate, read
`supported`. Nothing else covered it, because `sale_outcome` is written only while the FINAL status is
`sale_scheduled` with a parsed sale date (:508) and a later judgment entry moves the status off it,
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
kind and `reconcile_judgments` keys on `kind == 'final_judgment'` (:672), so the superseded judgment
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
writes a satisfaction onto the judgment whose date the entry's text CITES (`_target` :783 filters by
role, not by status) and onto that row alone (:741). `_judgment_record` read only the controlling row
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
sale. The entry fell between labelled and consumed; it now raises a gap naming the producer's own label.

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
:508's past-sale check is written `and status.get('sale_date') and ... < today`, so it never runs and
no `sale_outcome` is saved. The docket where LESS was known was the one reading `supported`: the same
docket with the date printed in its description was already `incomplete`. A status of `sale_scheduled`
with no parsed date is now a gap of its own. The acceptance fixture for 2024-014878 had hidden this
for sixteen rounds by omitting the `sale_date` key that `_transition` always writes for that status -
the same failure mode as the coverage fixture's missing `document` (thirteenth review).

**Reported, not changed (`miami_case_timeline`, not this module's surface).** :505 overwrites the
whole status when any undated dispositive entry exists, including a status already carrying one of the
three evidence-vs-evidence contradiction reasons. A docket with both a same-date conflict and an
undated dispositive entry therefore reaches `case_verdict` with reason "Undated dispositive entry
prevents reliable chronology", so the verdict is `incomplete` on a file that also holds a
contradiction. `case_verdict` restates the producer faithfully; the loss is upstream.

`attached_document_kind` discards the document's own title (:357 does
`attached, body_kind, title = body_kind, None, None`), so a bare "Notice of Filing" whose document reads
AMENDED FINAL JUDGMENT OF FORECLOSURE saves only `attached_document_kind='final_judgment'` and nothing
in the file distinguishes it from an exhibit copy. `_replaces` mirrors the producer's own `_REPLACES`
over the docket words, so it cannot see that amendment, and the case reads `supported` with the
superseded figure. Closing it needs the producer to keep the title (an `attached_document_title`).

`reconcile_judgments`' `_ADDS_TO` matches a bare `attorney'?s? fees?` over `operative_text` plus
`description` plus `comments`, so a final judgment whose clerk comments merely mention attorney's fees
is typed `role: supplemental`, excluded from `operative`, and the case reads "no operative judgment"
and therefore `incomplete`. Also upstream, also reported rather than fixed here.

**The limitation behind most of this, stated plainly.** `miami_case_timeline` :383 does
`if e['calendar_event'] and e['kind'] != 'notice_of_sale': e['kind'] = 'hearing'`, so any docket entry
whose OCS eventType is a hearing loses its real label, and every summary the producer builds afterwards
is keyed on the label that is gone: `stay_history` and `stay_in_effect` (:462), `sale_held` with its
certificate and its sale-day bankruptcy list (:544, :550, :552), `_transition`'s status kind (:247) and
`reconcile_judgments` (:722). Ten review rounds each found this reaching one more summary than the last
round had enumerated, so `case_verdict` stopped chasing sites: where a posture-deciding label was lost,
it names the entry and holds the case, whatever consumed it. An earlier version of this paragraph said
the case is held "as a gap" as though that covered the whole override; it covered the stay path only,
and a vacated judgment, a satisfied one and an order resetting a sale each read `supported` past it.

`case_verdict` cannot do better than a gap here, and neither could a fix in the producer without
evidence this repo does not hold. A calendar event genuinely can be a hearing ABOUT a motion rather
than the thing itself, which is what :383 is for, and nothing in a saved timeline says which a given
entry is. Deciding it needs a count of how often OCS puts a hearing eventType on an order row, which
is the desktop's to measure; a read-only script for that is in the project files. The producer is
therefore left alone on purpose, not by the earlier reasoning in this paragraph, which said the fix
would move the §362 stay flags the board hard-gates on. That was wrong and was checked: the board's
`saleBkAct` / `sale_bk_active` is built by `sale_history.py` (:391-447) from its own fresh docket pull,
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
