# DealFlow lead-generation audit: corrected second review

Reviewed September 21, 2026. This document supersedes the previous chat audit where they differ.

## Evidence and scope

- Local checkout: `C:\Users\olqbb\projects\foreclosure-leads`, HEAD `91a59c1` (September 18).
- Fetched GitHub main without changing the working tree: `ac5aa96` (September 20, 20:35 EDT). Findings below were checked against that revision. This matters: several phone and publishing improvements were absent from the older checkout.
- Public board retrieved during this review: build stamp `2026-09-20T20:33`, census 2,437 leads and 1,203 rows with a nonempty phone list (49.4%). Source: https://aiejandrog.github.io/dealflow-board/ . These are build-declared counts, not independently verified people, working numbers, or fresh source records.
- Local `health.json` is dated September 18, 06:45. Its historical `runner:true` flag does not prove this machine is the runner now. Local raw data and caches cannot establish current production coverage.
- Two independent code reviews traced ingestion/identity and quality/reporting. Read-only synthetic checks reproduced date, filter, reporting, and equity-classifier behavior. No scraper, enrichment purchase, outreach, schedule change, deployment, or production-data update was executed.
- This is a targeted code-path and source-contract review. It is not a measured county-wide recall study, a title examination, or a guarantee that no other bugs exist. Individual live records were not adjudicated against case documents.

## Corrections to the previous analysis

| Previous claim or implication | Corrected finding |
|---|---|
| The pipeline is effectively auction-only. | It has a separate three-county lis-pendens/fresh-filings lane. The auction source itself is late-stage; that alone is not a defect. |
| A static lender list limits all discovery. | The lender list restricts Miami-Dade LP discovery. Broward uses document type/date and paginates; Palm Beach defaults to document-type search. Missed-case counts were not measured. |
| Property identity has no robust safeguards. | LP processing has confidence thresholds, advisory guessed addresses, owner-mismatch flags, a revocation pass, and mortgage-PIN corroboration. The separate Miami-Dade first-defendant assumption remains a defect. |
| Missing lien data is generally treated as verified equity. | Existing code/UI distinguishes unchecked, unverified, unpriced, clear, and priced states; the dedicated door route rejects unresolved equity. A narrower classifier defect remains, described below. |
| Lien percentages measure complete debt coverage. | The health metric counts completed checks, including unpriced and qualifying empty results. It does not measure complete quantified debt or current payoff balances. |
| 315/909 establishes 35% contactability. | It measures nonempty cached phone arrays for the three auction files, excluding LP. It does not verify owner association, number validity, eligibility, or reachability. It is also historical. |
| 95% human-contact coverage proves a person to call. | The calculation excludes certain company-shell patterns; it is not independent verification of a human identity or contactability. |
| Scrape/publish safeguards are absent. | Per-county anti-collapse checks and publication gates exist. They do not prove per-page completeness or catch every source returning success with no data. |
| The LP wrapper is fail-open. | It stops on nonbenign child exit codes. Some source failures return success, and the enclosing nightly batch does not propagate a wrapper failure. These are distinct defects. |
| Ignoring `maxPages` is inherently wrong. | A source comment records unreliable headless pager text. The defect is lack of a positively verified last page/count, not the choice to ignore that field. |
| The 40-entry docket cache establishes missing legal-status protection. | It is an intentionally compact display cache, retains a total count, and is separate from Miami-Dade LP status checks. Its truncation is a display limitation, not proof of lost upstream court history. |
| The old local health report describes the current live board. | The board has a newer September 20 build. Build freshness still does not prove source freshness. |
| Contact information is undifferentiated. | Newer main adds phone-source tags, household/namesake distinctions, shared-number and listing-agent checks, improved ordering, and a larger configurable cap. Source labels still do not prove ownership. |
| Generic Palm Beach civil reports are a sufficient replacement. | Several catalog products explicitly exclude foreclosures. Dedicated foreclosure reports exist; their schema, delivery cadence, and acquisition method need validation. |
| CAPTCHA use proves unauthorized access. | The code uses solver services, but this review does not establish what permissions the operator holds. Miami-Dade OCS has explicit reuse terms; those terms cannot be generalized to other counties or RealForeclose. |

## Confirmed defects and bounded risks

Code references below use the fetched `ac5aa96` version. They are repository-relative, so line numbers in the older working checkout may differ.

### 1. Equity classifier can promote insufficient evidence

`equity_state.py:66-78` returns `priced` when any lien has an amount, before checking `conf='unpriced'`; a later branch also returns `priced` for a nonempty amountless list. It returns `clear` for a low-confidence empty chain. Its labels call these states VERIFIED, although low confidence can mean a missing property anchor or unreliable name search (`records_liens.py:540-542`).

Synthetic reproductions using the actual function:

| Input | Actual result |
|---|---|
| `conf=ok`, two liens, amounts 100000 and 0 | `priced` |
| `conf=unpriced`, two liens, amounts 100000 and 0 | `priced` |
| `conf=ok`, one lien with no amount | `priced` |
| `conf=low`, no liens | `clear` |

Historical local caches contain 199 Miami-Dade and 16 Broward low-confidence empty records classified `clear`. These are cache records, not a count of affected current board leads. No live incorrect payoff or property-specific loss was established.

Required correction: preserve source uncertainty; require every relevant instrument to be accounted for before declaring the list priced. Distinguish recorded instrument amounts from verified current balances. Keep existing UI safeguards, but correct the evidence supplied to them.

### 2. Miami-Dade party extraction omits a defendant and assumes owner identity

`foreclosure_leads.py:428-434` drops the first defendant from the additional-defendant list, regardless of whether that person matches the Property Appraiser owner, retains only six others, and uses defendant zero as owner when the owner is blank. Cleaning downstream cannot recover an omitted name.

Required correction: retain the full role-labelled party list separately from property ownership; remove duplicates by verified identity rather than list position. This review proves the assumption and information loss, not a specific misidentified homeowner.

### 3. Miami-Dade clerk lookup failure blanks enrichment

`foreclosure_leads.py:412` clears plaintiff, defendants, and docket URL before lookup. Missing/invalid responses and exceptions at 419-440 can leave those fields blank. There is no successful prior-value carry-forward in this function.

Required correction: retain last successful values with their source timestamp; separately record the failed refresh and retries. A failed refresh must not masquerade as a verified empty party list.

### 4. Same-day auction case excluded from party lookup

`county_plaintiffs.py:159`: `(r.get('days') or -1)` maps day zero to -1. The intended 0-30-day filter therefore rejects today's auction. Synthetic check: day 0 rejected, day 1 accepted, day 30 accepted.

Required correction: distinguish missing values from numeric zero.

### 5. County party cache can remain stale indefinitely

`county_plaintiffs.py:157` skips cached cases unless `--refresh`. The nightly Broward caller (`refresh-dealflow.bat:349`) omits that switch. `county_leads.py:374-388` deliberately carries prior party data forward.

Required correction: timestamp successful case detail and refresh by age/event; prioritize imminent auctions without discarding cached data on failure.

### 6. Source failure can be reported as a successful LP sweep

`lis_pendens.py:298-299` returns normally when all sources are blocked/empty. The wrapper sees exit zero and can continue over retained data. Partial county failures also need explicit reporting. Existing data is merged/preserved; this is not evidence of destructive replacement.

Required correction: distinguish verified empty results from failed requests, record a result per county, and propagate incomplete/failure state to the wrapper and status report.

### 7. Nightly batch does not propagate LP-chain failure

`lp_refresh.py:31-44` correctly stops on nonbenign failures. `refresh-dealflow.bat:217` invokes it without checking its return code, then continues. Earlier stages can have updated files before a later failure; the chain is not transactional. Keeping the last good LP output may be a valid availability choice, but a partially failed refresh needs an explicit degraded result.

Required correction: propagate the failure to the run summary; stage outputs and promote a completed set, or clearly retain and label the last successful set.

### 8. Palm Beach LP page completeness is unverified

`fl_lp/palmbeach.py:67-73,176-185` sets a bounded result count, retrieves one result window, and returns without validating total records or paging through the remainder. Actual missing-record counts were not measured.

Required correction: follow the supported source's pagination, or split queries by date until each window is provably complete; deduplicate on stable recording IDs. Use any required authorized access method.

### 9. Auction pagination can silently stop early

`foreclosure_leads.py:252` caps next-page clicks at 25. An unchanged first case after the wait also ends traversal without proving the last page. `scrape_guard.py` protects against large per-county count collapses, but smaller losses may pass.

Required correction: distinguish last page from timeout/cap, check stable page/item identifiers, and reconcile counts against an appropriately timed source snapshot. Auction status can change during scraping, so count mismatches require reconciliation rather than a blind equality rule.

### 10. Ongoing LP court-status refresh is Miami-Dade only

`lp_status.py:105-107` explicitly excludes other counties. Miami-Dade does have age-based rechecks. This is a county coverage gap in that status component, not proof every other cancellation or bankruptcy safeguard is absent.

Required correction: add county-specific status adapters and expose last-verified case status. Do not treat an old LP filing alone as proof a case remains actionable.

### 11. Run summary produces false success and invalid totals

`run_report.py:88` accepts DOWN and missing health as OK when count thresholds pass. `pushed_today` is unused in that verdict and is inferred from a local commit date, which cannot prove a remote push or live deployment. Separate batch publish-verification code exists; this defect is in the summary.

`run_report.py:15-41` assigns an entire lead file to its first row's county, overwrites prior counts for that county, and counts all historical cached phones without intersecting current leads.

Read-only reproduction against the historical local files: Miami-Dade is reported as 1,361 leads because the mixed-county LP file overwrites its 350 auction rows. Broward is reported as 507 phones for 267 leads; Palm Beach 299 for 292. These are reporting errors, not new live production counts.

Required correction: count the current deduplicated board population by each row's county, intersect phone-bearing cases with that population, consume structured run/publish outcomes, and distinguish healthy/degraded/failed/unknown states.

### 12. LP metadata compares dates as text

`lp_refresh.py:85`: `max()` on M/D/YYYY strings chooses September 9 over September 18 and October 1. Reproduced without executing the pipeline.

Required correction: parse dates before comparison, store an ISO date, and separate successful run time from newest source filing. `healthcheck.py:205-219` already parses actual records correctly and does not use this faulty metadata; the prior analysis must not attribute this bug to its freshness calculation.

## Limitations that should not be inflated into confirmed incidents

- Miami-Dade's fixed lender discovery list creates a plausible recall gap, but quantifying it needs an independent filing universe and comparison by case ID.
- `gen_dockets.py:83` caps displayed parties at 14; 40 docket entries and a 21-day refresh are intentional compact-cache policies. Label truncation and provide the full-source link. Do not use this compact view as a complete party ledger or status authority.
- An HTTP 200 source probe establishes availability only to the extent its response is validated. It does not prove all lead queries, pages, or fields succeeded.
- Phone list presence, a skip-trace match, or a source tag cannot establish a verified owner phone or a successful contact. Report these as separate measurements.
- A record index and an amount on an instrument do not alone establish current payoff, release validity, lien priority, or complete title. These require additional evidence; no county feed promises every one of these outcomes.

## Corrected source recommendation

Use a measured hybrid migration: county case data for discovery/parties/events; recorded instruments and appraiser data for property corroboration; RealForeclose for auction events. Retain working components while evaluating new source coverage. First run prospective feeds in parallel and compare case-level recall, identity accuracy, lag, completeness, and cost before replacing production discovery.

### Miami-Dade: strongest documented alternative

The official service lists bulk access at $110 per folder/month, APIs at $0.20 per request, and a separate $420/month Official Records image option. Civil access requires registration, identity paperwork and verification. Files are available for 30 days, so bootstrap/backfill and missed-day recovery need explicit design. Prices checked September 21, 2026; do not assume a combined bill without confirming the selected subscription and API usage.

Sources: [Commercial Data Services](https://www.miamidadeclerk.gov/clerk/commercial-data-services.page), [Developer service](https://www2.miamidadeclerk.gov/developers).

The March 27, 2026 civil layout documents daily changed-case, party and docket files. Changed-case party/docket sets must replace the previous set for that case. Preserve raw snapshots separately for audit history. The published party-code examples should be resolved through the supplied dictionary rather than blindly hardcoded.

Source: [Civil layout](https://www.miamidadeclerk.gov/library/FTP_File_Layouts/FTP_Layout_Civil.pdf).

The official API also documents case details, dockets associated with images, and case/docket document-image retrieval. Bulk discovery plus targeted document retrieval is therefore a more concrete option than the first audit established. Access to the needed document types still requires validation.

Source: [API directory](https://www2.miamidadeclerk.gov/Developers/Help).

### Broward: case metadata API, separate document path

The Commercial Data Access API covers Circuit/County Civil case filings, party information, events, and judgments. Its page explicitly says electronic document downloads are unavailable through this service. It can improve discovery and party/status data, but does not alone replace complaint/mortgage document acquisition or OCR.

Source: [Broward API](https://www.browardclerk.org/Web2/Services/AboutAPI).

### Palm Beach: choose foreclosure-specific reports

The ClerkCart catalog lists `Foreclosure 08` (weekly housing/lis-pendens report) and `Foreclosures 01`. Several generic County/Circuit Civil products explicitly exclude foreclosures. The Clerk also directs users seeking new foreclosure cases to ClerkCart. Validate a specific product's columns, update semantics, automation method and timing before proposing it as a complete replacement. A weekly report does not satisfy a same-day discovery target by itself.

Sources: [Product catalog](https://appsgp.mypalmbeachclerk.com/clerkcart/ProductList.aspx), [Clerk foreclosure information](https://www.mypalmbeachclerk.com/departments/courts/foreclosures/certificate-of-title-information).

### Access finding

Miami-Dade OCS says reuse/storage beyond limited personal copies requires permission and points to Web API Services. That is direct evidence concerning OCS, not a finding that every existing access is unauthorized, nor a rule attributable to RealForeclose or every other county. Permissions held by the operator were not inspected.

Source: [OCS notice](https://www2.miamidadeclerk.gov/ocs/).

## Order of work supported by this review

1. Correct false-confidence equity classification, first-defendant handling, and false-success reporting. Add meaningful fixture tests for the reproduced failure conditions.
2. Correct day-zero filtering, parsed date metadata, cache aging, and per-county failure propagation; retain last-good data with explicit stale status.
3. Establish page/query completeness and county status refresh, then measure discovery gaps against a separate authoritative filing universe.
4. Pilot the exact county feed products after access is available, validate fields on sampled cases, and compare with the existing pipeline before migrating.

No production fixes were applied during this requested reanalysis. This report is the corrected, reviewable result; historical observations, reproduced code defects, and unmeasured risks are distinguished throughout.

---

## Second-reader verification, September 21, 2026

A project session re-checked every code citation above against `ac5aa96`, which was still the tip
of `main` at the time of the check. All twelve defects reproduce at the cited code. Three citations
pointed at the wrong line and were corrected in this copy; nothing substantive changed.

| Citation | As written | Corrected to | Why |
|---|---|---|---|
| Defect 6 | `lis_pendens.py:302-303` | `lis_pendens.py:298-299` | 302-303 is the MERGE comment block. The bare `return` on an empty sweep is at 298-299. |
| Defect 5 | `county_leads.py:367` | `county_leads.py:374-388` | 367 is the *photo* carry-forward print. The court-party carry-forward is the `_pcarry` block at 374-388. |
| Limitations | `gen_dockets.py:85` | `gen_dockets.py:83` | 83 is the `[:14]` party slice. 85 is the docket sort key. |

Spot-checks that confirmed the substance rather than the line number:

- `equity_state.state_of` (58-81) orders its branches exactly as described: the amount check at 66-67
  precedes the `conf == 'unpriced'` check at 68, `conf in ('ok','low') and not liens` returns `clear`
  at 73-76, and a nonempty amountless list falls through to `priced` at 77-78. `LABEL` does call
  `clear` and `priced` VERIFIED. `records_liens.py:540-542` is where `conf='low'` is set, for a
  missing subdivision anchor, more than four open mortgages, or more than 45 name matches.
- `run_report._health()` returns `''` when `health.json` is missing, and `''.upper() != 'FAIL'` is
  true, so the missing-health case really does pass the verdict at line 88. `_git_last()` is a local
  `git log -1`, which is why `pushed_today` cannot evidence a push.
- `lp_refresh.py:85` does `max()` over `x['date']`, and `lis_pendens.py:209` stores that field as
  `'6/8/2026'`, so the string comparison is real. `healthcheck.py:205-219` splits on `/` and builds
  `datetime.date` objects, and nothing in the repo reads `lp_meta.json`, so the audit is right that
  the bug does not reach the healthcheck.
- `refresh-dealflow.bat:217` and `:349` are as quoted, and `:369` runs `gen_dockets.py --stale 21`,
  which is the 21-day refresh referenced above.

Not verified, and stated here so nobody treats it as checked: the external source claims. The county
price and product pages are unreachable from the container that did this second read, so the $110 /
$0.20 / $420 Miami-Dade figures, the Broward document-download exclusion, and the Palm Beach catalog
contents carry the original reviewer's September 21 check and nothing more. Confirm them before any
money is committed.

One scope note for whoever picks up the work: `cadence.py` and everything else listed under the
reserved suppression surface in `CLAUDE.md` are untouched by these findings, and none of the twelve
corrections requires editing them.
