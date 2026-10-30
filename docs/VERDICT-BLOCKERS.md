# Verdict blockers: why a lead's case facts stay unknown, and what it takes to prove them

Diagnosis only, 2026-10-30, worktree `claude/agents-verdict-blockers` on `origin/main` bf463f0. No code was changed, no scraper, paid lookup, send or `.bat` was run, nothing touched the network. Counts come from cached files on the laptop; the counting script is `%TEMP%\vb\verdict_blockers_count.py` (kept outside the repo). Public repo: counts, file:line and field names only.

**Missing inputs.** `SCRAPER-COVERAGE-MAP`, `MIAMI-TODO` and `PALM-BEACH-DOCUMENT-FINDINGS` are not in the repo (Palm Beach one moved out of git 2026-09-23, commit 7690913; the other two were never committed). Nothing here is reconstructed from them. The "county-pilots work" on disk (`C:\Users\olqbb\projects\fl-pilots`, branch `claude/county-pilots`) is two acceptance matrices only (`BROWARD-/PALM-BEACH-ACCEPTANCE-MATRIX-2026-10-03.md`, also on `origin/main`); no pilot code exists there.

## 0. Counting method (so a reader can recount)

| Source | What it is | Counted how |
|---|---|---|
| Board rows | The `const RAW = [...]` array inside the plaintext twin `~\DEALFLOW\Foreclosure Lead Tracker.html` (22 MB, mtime 2026-10-30 11:06). 4,634 rows: Miami-Dade 1,067, Broward 2,216, Palm Beach 1,351. Snapshot copied to `%TEMP%\vb\raw.json` (sha256 starts `130489cf4011`) | Parsed with `json.JSONDecoder().raw_decode` from the `const RAW = ` offset; counted by `county`, `st` (LP/FC/TD/BAL), `eqstate`, `judg`, `ju`, `cstatus`, `lpStatusChecked`, `bkAsk`, `saleBkAct`, `auction`, `jaccrued` |
| Equity card | `eqc` is **not** in this twin (it was baked before today's merge). `equity_state.card()` (`equity_state.py:363`) was called on each RAW row to simulate what the next bake will say | `import equity_state; card(row)` |
| Federal bankruptcy | `~\DEALFLOW\bk_lead_cache.json` (917 entries, written 2026-10-29 21:16), joined to rows with `stay_gate.pacer_key(row['case'])` | verdict per row, "no_cache_entry" if the key is absent |
| Documents | `~\DEALFLOW\documents\*\*\*.json` manifests (111), `documents\queue.db` (read-only URI), `dossiers\MIAMI-DADE\` (86 dossiers, `case-verdicts.json`) | glob + sqlite `mode=ro` |
| Lien chains | `records_liens.json`, `broward_liens.json`, `palmbeach_liens.json` in the main checkout (gitignored) through `equity_state.state_of` | per-chain state and `conf` |

Run: `python -I -B %TEMP%\vb\verdict_blockers_count.py --raw %TEMP%\vb\raw.json` (add no flag to re-extract from the live twin; the 11:40 refresh may have replaced it by then). Lead counts overlap between rows of the blocker table: one lead can be blocked on several fields.

## 1. Per county, per field: source, and the exact condition that yields unknown or partial

Legend for the "unknown" column: a lead is counted in the reason that fires first. Numbers are leads on the board.

### 1.1 Case open / closed

| County | Source today | Condition that yields unknown | Leads |
|---|---|---|---|
| Miami-Dade | OCS `caseStatus` (anonymous docket API, `docket.py:1-30`) stored as `cstatus`; LP rows via `lp_status.py` (`_lp_status_cache.json`) | `cstatus` empty (12 rows); 2 BAL rows are not court cases | 12 empty; 2 of 689 LP rows unchecked |
| Broward | none. `cstatus` is empty on every row (`county_leads.py` never writes it). Only `lp_status.py` could, and its Broward branch is **off** unless `DEALFLOW_LP_BROWARD=1` and `BROWARD_CLERK_API_KEY` set (`lp_status.py:31-38`). Neither is set in this process (`BROWARD_CLERK_API_KEY` unset; flag unset) | no adapter output, so `lpStatusChecked` is False on all LP rows; FC/TD rows are "open" only by being on the auction list when scraped | 2,216 unknown (1,571 LP, 298 BAL, 347 FC/TD INFERRED open) |
| Palm Beach | none. `lp_status.py:38` "Palm Beach has no adapter". eCaseView is reCAPTCHA v3 behind a headed browser (`pb_case_lookup.py:10-25`) and non-commercial (CLAUDE.md, `palmbeach_documents.py:1-6`) | no automated source | 1,351 unknown (922 LP, 429 FC/TD INFERRED open) |

### 1.2 Judgment amount

The board's `judg` is the **auction page's "Final Judgment Amount"** (`county_leads.py:221`, `foreclosure_leads.py:1039`), never a document read. The only document-backed path (`judgment_for_analyze` `miami_judgment.py:1088`, `court_judgment` `:1127`, applied in `records_liens.judgment_for` `:529`) needs `DEALFLOW_COURT_JUDGMENT=1` (unset) and `~\DEALFLOW\judgment_reports\<case>.json` (the folder does not exist). `judg_src == 'court copy'` is therefore on 0 rows.

| County | FC/TD amount from listing | Listing blank (`ju`) | TD: `judg` is really the opening bid | LP: no judgment on the row | BAL | Payoff accrual possible (`jaccrued`) |
|---|---|---|---|---|---|---|
| Miami-Dade | 365 | 4 | 7 | 689 | 2 | 342 of 376 FC+TD (`judgment_dates.json`, 817 entries, source `mdc-ocs-docket` only) |
| Broward | 175 | 102 | 70 | 1,571 | 298 | 0 of 347. `broward_judgment_dates.py:5-35`: clerk site Cloudflare-turnstiled, case-number search hits a captcha, name search resolved 2 of 4 in its own test; 75 recorded misses in `judgment_dates_misses.json` |
| Palm Beach | 257 | 0 | 172 | 922 | 0 | 0 of 429. Landmark publishes no amounts; eCaseView captcha + non-commercial; `pb_judgment_import.py` exists but only reads PDFs Alex saves by hand |

Condition for unknown/partial, as code: `judgment_unknown = (judg == 0)` for non-TD (`foreclosure_leads.py:1042`); county rows `judg_unknown = (st != 'TD') and (judg <= 0)` (`county_leads.py:124`); TD sets `judgment := Opening Bid` (`foreclosure_leads.py:1033-1037`). LP rows carry `judg = 0` by construction (the case has not reached judgment as of the filing); nothing on the row says whether it has since.

### 1.3 Sale date

Source: the same auction listing (`AuctionDate`), re-clocked nightly. Rows with a date: Miami-Dade 376, Broward 347, Palm Beach 429. Miami rows also carry docket-derived sale posture (`sale_history_cache.json`, 1,578 cases; `saleSurv`/`saleBK` on 376 rows). Broward and Palm Beach have **no** docket read, so a postponed, cancelled or stayed sale after the scrape is invisible (no `saleSched`, `sr`, `saleSurv` on those rows). Unknown by design: 3,182 LP + 300 BAL rows have no sale at all.

### 1.4 Liens (recorded chain)

| County | Source | Condition that yields the state | Unchecked | None | Unpriced | FACT (clear+priced) |
|---|---|---|---|---|---|---|
| Miami-Dade | Official Records search behind Cloudflare Turnstile; tokens via Camoufox (free) then 2Captcha (~$0.0033/solve, cap $2.50, $0.91 counted) then `records_qs.json` cache (`records_liens.py:1-35`; `miami_reads_spend.json`) | `none` = `conf` low (45+ name models or unanchored) or search failed (`equity_state.py` `_state_of`). Chain cache: 909 `none` = 576 `low` + 297 `none` + 36 `ok` with `nrec`=0 and no `second_fc` answer | 451 (LP 425, FC 24, BAL 2) | 248 | 204 | 164 (6 clear, 158 priced) |
| Broward | AcclaimWeb through native Windows `curl` (Cloudflare blocks other TLS stacks; no captcha) (`broward_liens.py:1-20`) | `unpriced` = common-name `low` chain forced to a ceiling (689 rows, `LOW_CEILING_WHY`) or recorded debt found, total not established (417). `none` = `conf` none 196, low 47, `ok` chain but a second foreclosure on the parcel 23 | 390 (BAL 298, FC 62, TD 14, LP 16) | 254 | 1,106 | 466 (98 clear, 368 priced) |
| Palm Beach | Landmark index (`palmbeach_liens.py`); **publishes no dollar amounts** (`equity_state.py` docstring, Palm Beach third state) | every PB chain is at best `unpriced`: 606 of 768 cached chains; 162 `none` (`conf` none) | 1,006 (LP 920, TD 64, FC 22) | 65 | 263 | 17 priced |

Chain-never-pulled is a queue gap, not a block: 920 of the 1,006 Palm Beach unchecked rows and 425 Miami ones are LP rows the nightly lien trace does not cover.

### 1.5 Stay / bankruptcy

| County | Sources | Condition | Result |
|---|---|---|---|
| Miami-Dade | Docket stay from the OCS docket (`stay_gate.py`, `sale_history_cache.json`; LP via `lp_status`), plus CourtListener | CourtListener cache entry exists for only 137 of 1,067 leads (45 `clear`, 47 `active`, 40 `possible`, 5 `unavailable`); 928 + 2 have none. `saleBkAct` (held) on 153 | docket stay known for the 376 auction rows and 687 of 689 LP rows; **federal** check unknown for 930 |
| Broward | CourtListener (token set; free tier 5/min, 50/hr, 125/day); `clerk_bk.py` is off (`DEALFLOW_CLERK_BK` unset, API key unset); PACER is by-hand only (`DEALFLOW_PACER_AUTO` unset) | no cache entry 2,012, no key (BAL) 98, `clear_unconfirmed` 37 (a CourtListener clear does not release Broward without `DEALFLOW_BK_ALLOW_CL_CLEAR=1`), active 36, possible 31, unavailable 2 | 2,050 rows are `bkAsk` (released only by the attorney waiver of 2026-10-13, `bk_waiver.json`); 68 held |
| Palm Beach | same | no cache entry 1,235, `clear_unconfirmed` 37, active 17, possible 59, unavailable 3 | 1,273 `bkAsk`; 78 held |

All 3,431 `bkUntil` stamps carry one value, Sun 2026-11-01 10:06 (`RELEASE_PAGE_HOURS = 48`, `bk_lookup.py:1592`): without a rebuild the waiver releases lapse on the page in two days. The last CourtListener pull (`bk_lookup_status.json`, 2026-10-29 21:16) used 19 requests, indexed 2,400 filings and **checked 0 leads** (`leads_checked: 0`).

### 1.6 Equity

Equity is derived, not sourced. It may be spoken only when `eqc.may_quote` (`equity_state.py:363`): lien state FACT **and** a known judgment. Simulated on the 4,634 rows: **116** quotable (Miami-Dade 68, Broward 32, Palm Beach 16). First missing prerequisite for the other 4,518: lien state `unchecked` 1,847, `unpriced` 1,573, `none` 567, lien FACT but LP row with no judgment 525, lien FACT but judgment unposted 6. Also: `value` is 0 on 1,193 Broward, 935 Palm Beach and 383 Miami-Dade rows (no basis to net against).

## 2. Hard blockers by type, ranked by leads blocked

Overlapping. "Blocked" means the field cannot reach PROVEN (section 5) for that many leads.

| Rank | Blocker | Type | Counties | Leads blocked | Field(s) | Evidence |
|---|---|---|---|---|---|---|
| 1 | No court document has been read for any lead. 111 documents stored (60 deeds, 30 quit-claims, 21 liens; 0 judgments), all `image_only`; `judgment_reports/` absent; 0 saved `*-timeline.json` | missing document + unscanned PDF | all | 4,634 | judgment, case status, sale, liens, stay | `man2` count; `case-verdicts.json`: 0 verdicts, 3 "no timeline saved" |
| 2 | Case open/closed unread | captcha / missing key / terms | Broward, Palm Beach | 3,567 | case status | `cstatus` empty; Broward API key unset; PB reCAPTCHA v3 + non-commercial |
| 3 | Federal bankruptcy check never run for the lead | rate limit + gate holding on missing data | Broward, Palm Beach (+ Miami federal 930) | 3,345 (Broward 2,110 incl. 98 BAL, Palm Beach 1,235) | stay | `bk_lead_cache` join; 125/day budget; last pull checked 0 leads |
| 4 | Recorded chain never pulled | gate (never queued) | Palm Beach LP 920, Miami LP 425, Broward BAL 298 + 92 other, Palm Beach 86 other, Miami 26 other | 1,847 | liens, equity | `eqstate == 'unchecked'` |
| 5 | Recorded chain found, total not established | parser / index limit (PB structural), common-name cap | Broward 1,106, Palm Beach 263, Miami 204 | 1,573 | liens, equity | `eqstate == 'unpriced'` |
| 6 | Judgment amount is the auction page's figure, no document behind it | missing document | all | 903 FC (+249 TD whose "judgment" is the opening bid) | judgment | 0 `judg_src == 'court copy'` |
| 7 | No judgment entry date, so no payoff or interest | login / captcha | Broward 347, Palm Beach 429, Miami 34 | 810 | judgment (payoff) | `jaccrued` false |
| 8 | Chain search failed or unanchored | captcha / Cloudflare / timeout | Broward 254, Miami 248, Palm Beach 65 | 567 | liens | `eqstate == 'none'`; chain caches `conf` none 655 / low 623 |
| 9 | LP judgment status unknown (pre-judgment, nothing says whether it is now entered) | structural | all LP | 3,182 | judgment, sale | `st == 'LP'` |
| 10 | Judgment listed but amount blank | parser (auction page) | Broward 102, Miami 4 | 106 | judgment | `ju` flag |
| 11 | OCR cannot run: PowerShell fails to start (`type initializer for System.Net.ServicePointManager threw an exception`, exit 4294901760). Reproduced in this session's own shell | OCR failure | Miami | 213 pages / 111 docs / 61 cases | every Miami read | `weak_pages[*].ocr_error` on 213 of 213; `document_store.py:593` `winocr` -> `fl_lp/broward_pin.ocr_images` |
| 12 | Paid vision reader unavailable: `anthropic.key` not beside the code | paywall / missing credential | Miami | all scanned Miami judgments | judgment amount | `judgment-night` log 2026-09-27 "STOP: no anthropic.key"; file absent from the main checkout today (unverified elsewhere) |
| 13 | Miami Official Records daily file lapsed 2026-10-26 (`units_before` 0, `gap: access lapsed`) | paywall | Miami | 0 gate effect (report-only, `or_daily_file.py:11-16`); 605 tracked matches stop refreshing | liens (evidence) | `or_daily/status.json` |
| 14 | Parser break / timeout | parser, timeout | - | not measurable from disk. `broward_or_feed` reports `bad_rows: 0`; healthcheck currently FAILs on skiptrace freshness and ARV-comp coverage (not verdict fields) | - | see "Could not verify" |

## 3. Memory: what persists, what is recomputed

**What `document_store.py` keeps (per document, under `~\DEALFLOW\documents\<COUNTY>\<case>\`, outside git/OneDrive via `case_review.output_path`):**
- the rebuilt PDF; a sidecar `<document_key>.json` with provenance: `source_ref`, `transport`, `source_urls` (these carry **per-request tokens**, so they are not a durable citation), `record_key` (book, page, CFN, doc type, recording date), `document_key` (stable identity, `document_store.py:75`), `source_sha256`, `rebuilt_sha256`, `fetches[]`, `pages`, `pages_expected`, `page_count_verified`, `retrieved_at` (`:267` `store`);
- the read result: `read_status`, `reader_version`, `pages_with_text`, `pages_unresolved`, `weak_pages[]` with per-page `outcome` and `ocr_error` (`:781` `record_read`);
- per-page text in `<key>-text/pNN.txt` plus `pages.json` (`:812` `save_page_text`), and kept page images under `-pages`;
- job state in `queue.db` (`document_queue.py`): status, attempts, `sha256`, `reader_version`, `read_status`, error. Today 90 done, 1 leased, kinds `recorded_instrument` only.

**What it drops (nothing writes it down):** any extracted fact. No judgment amount, judgment date, party, document class, sale date, lien amount, no page number or text offset for a fact, no confidence, no expiry. Classification (`document_classify.py`) and amount candidates (`miami_judgment` `amount_candidates`, `judgment_money`) are recomputed each run; they persist only if a caller passes `--out` (`miami_judgment.py:1123` `REPORT_DIR`) or `judgment_pass` writes `<case>-timeline.json` (`judgment_pass.py:111`) and neither exists on disk. `case_dossier` section c ("documents") is empty for the same reason. `case_verdict` therefore has nothing to read (`case_verdict.py:2317`).

**What other caches keep, each in its own shape** (no shared schema; the board bake keeps none of the provenance):

| Cache | Per-case fields | Provenance kept |
|---|---|---|
| `judgment_dates.json` | `d`, `label`, `rate`, `src`, `checked` | source name, check date |
| `_lp_status_cache.json` | `status`, `dismissed`, `terminal`, `codes`, `checked`, `at` | check date |
| `bk_lead_cache.json` | `verdict`, `why`, `cases[]`, `src`, `q`, `t`, `env`, `bd`, `ok_check` | source, query time |
| `sale_history_cache.json` | `s`,`n`,`d`,`b`,`a`,`bd`,`sl`,`t`,`v` | timestamp only |
| `county_plaintiffs.json` | `plaintiff`, `is_bank_first`, `case_type` | none |
| `records_liens.json` family | `liens[]` (`amt`, `st`, `d`, `src`), `conf`, `nrec`, `capped`, `second_fc` | partial |

### 3.1 Minimal case-facts store (design; reuse, not a new engine)

**Location.** `DEALFLOW_DIR/case_facts/<COUNTY>/<case>.json`, resolved through `case_review.output_path` exactly like `document_store.case_dir` (refuses OneDrive, sync roots, anything in a git repo). Atomic writes and reads reuse `document_store.pipeline_write` / `pipeline_load` (`:1008`, `:1013`). Never in the repo; the board bake reads it and publishes only `{state, value, asof}` per field (no URLs, no document ids).

**Schema** (`schema_version` 1):

```
{ "county": "...", "case": "...", "updated_at": "...",
  "facts": { "<field>": [ <fact>, ... newest first, keep 5 ] } }
fact = {
  "value": ..., "unit": "usd|date|enum|list",
  "state": "PROVEN | INFERRED | UNKNOWN",
  "reason": "<enum, required iff UNKNOWN>",
  "basis": "document | docket_entry | index_row | listing | derived",
  "source": { "kind": "...", "ref": "<document_key | docket entry id/DIN | book/page/CFN | listing AID>",
              "page": 3, "locator": "p03.txt:line", "sha256": "<doc or page-text hash>",
              "fetched_at": "...", "url_class": "ocs|official_records|realforeclose|..." },   // no tokens
  "read": { "method": "text|ocr|vision|human", "reader_version": 5,
            "exact_cents_verified": true, "human_reviewed": false },
  "confidence": "high|med|low",          // rule-derived enum, never a float
  "observed_at": "...", "expires_at": "...",
  "supersedes": "<fact id>", "derived_from": ["<fact id>", ...] }
```

Fields: `case_status`, `judgment_amount`, `judgment_date`, `sale_date`, `sale_status`, `liens` (list, one entry per instrument), `stay`, `equity` (always `derived_from` the others).

**Verified vs inferred marking (rules, not judgment calls).**
- PROVEN: `basis` in {document, docket_entry, index_row}, `source.ref` resolves to something in the local store (a `document_key` with `page_count_verified`, or an entry id in a saved docket), and for money `read.exact_cents_verified` with the existing `judgment_for_analyze` policy (`miami_judgment.py:1088`: scan-read figures need `sum_check` or human review).
- INFERRED: `basis` listing or derived, name-match results, arithmetic from other facts, anything that rests on a PROVEN fact plus an assumption. `equity` is INFERRED unless every `derived_from` is PROVEN and fresh.
- UNKNOWN: `reason` from a closed set: `never_queued`, `not_fetched`, `login_walled`, `captcha_blocked`, `rate_limited`, `budget_exhausted`, `paywall_lapsed`, `credential_missing`, `unscanned_no_ocr`, `ocr_failed`, `parser_unrecognized`, `timeout`, `county_no_adapter`, `terms_forbid`, `gate_held`, `pre_judgment`, `not_applicable`.

**Staleness per field** (all reuse numbers already in the code):

| Field | Expires | Reuse |
|---|---|---|
| `case_status` | 7 days; 24 h inside 3 days of a sale | `lp_status` re-check cadence |
| `sale_date` / `sale_status` | 24 h within 3 days of the sale, else 7 days; any new docket entry of kind sale/stay/vacatur expires it | Miami `sale_history_cache` |
| `judgment_amount` | event-driven: expires on a new judgment, vacatur or satisfaction entry; the amount never ages but the payoff does | `case_verdict` replacement logic |
| `judgment_date` | none once PROVEN | `judgment_dates.json` |
| `liens` | 14 days | `broward_liens.LINK_TTL_DAYS` |
| `stay` | 14 days (PACER clear), CourtListener `max_age_days`; the page-side release already dies at 48 h | `stay_gate`, `bk_lookup.release_until` |
| `equity` | min of its inputs | derived |

**Backfill at $0.** Thin adapters wrap the five existing caches above and the 111 document manifests, emitting facts with the provenance they already have (INFERRED or UNKNOWN until a document is read). One writer function (`record(county, case, field, fact)`) is the only code that writes the folder, mirroring the `optout_sync.ledger_add` single-writer rule.

## 4. Display path, traced for the judgment amount

| Step | Where | What happens | What is lost |
|---|---|---|---|
| 1. Scrape | `foreclosure_leads.scrape_date` `:558`; BW/PB `county_leads.to_slim` `:221` | the auction item's "Final Judgment Amount" text becomes `judg` via `F.money` | scrape time, source page, document id |
| 2. Cache | Miami `leads_final.json` (`judgment`, `judgment_unknown`; 369 of 376 carry the key); `broward_leads.json`, `palmbeach_leads.json` (`judg`) | frozen amount | same |
| 3. Type relabel | `foreclosure_leads.py:1033-1037` | for tax deeds `judgment := Opening Bid`, `judgment_unknown := False` | 249 TD rows carry an opening bid in the judgment field |
| 4. Bake | `make_tracker` slim row `:2556` (`judg`), `:2659` (`ju`); interest `:3220-3264` writes `payoff`/`jaccr`/`jdate` only where `judgment_dates.json` has a date (Miami 342; "Broward/PB ... stay unaccrued", `:3224`); court copy `:2755` (Miami loop only, gated on `DEALFLOW_COURT_JUDGMENT`); `eqc` stamped `:2756` (Miami) and `:2937` (other counties) | amount + card | a row cannot say "from the auction page" or "as of date" |
| 5. Card | `equity_state.card` `:363`, `jsrc` `:382-384` | `judgment_source` is `'court copy'` or **`'docket'`** or `'not posted'` | the auction-page figure is labelled `docket` (1,046 rows now, 0 `court copy`); a TD opening bid is labelled a docket judgment (249 rows, 14 of them quotable) |
| 6. Board | `tracker_template.html` `_eqcChip` `:3013` (state + may/do not quote, source only in a tooltip), many raw `r.judg` reads (`:2978`, `:4073`, `:7812` "Foreclosing judgment: $x or 'not posted'") | the chip is conservative, the raw reads are not | board text prints the figure with no source or date |
| 7. Call Mode rows | `call_mode.py:2444` `'jg': _n('judg')`, `_n` `:2337` maps 0 to None; `'eqc': _eqc_slim` `:1775`, `:2463` | `js` truncated to 14 chars, liens cut to 8 | 0, never-checked and pre-judgment all become `None` |
| 8. Rendered card | `call_mode.py:8424` `eqCardHtml`, chip `:7697` | "Judgment: $X from docket" or "not posted"; "judgment not posted" chip for `ju` | "not posted" covers three different facts |

**Does `eqc` already solve it?** Part. It solves the confident-guess problem for equity: a dollar net now needs a FACT lien state and a known judgment (116 of 4,634). It does not solve provenance: (a) the label `docket` is wrong for every non-court-copy figure; (b) the court-copy path is dead by default (flag unset, report folder absent), so a proven figure would be hidden behind the board's unproven one; (c) no `as of` or document id; (d) "not posted" merges pre-judgment (3,182 LP rows), listing-blank (106) and never-checked; (e) 525 LP rows with a VERIFIED lien chain show "Judgment: not posted, no net", which hides a proven fact behind a missing one; (f) it covers equity only, not case status, sale or stay; the board still prints raw `judg`.

**Where it could present an uncertain value confidently:** the 14 TD rows that pass `may_quote` on an opening bid; every Call Mode card line "from docket" on an auction-listing amount; `payoff`/`jaccr` on Miami rows built from a docket date but a listing amount (payoff = unproven amount + proven date, displayed as one number). **Where it hides a proven one:** the court-copy figure behind `DEALFLOW_COURT_JUDGMENT`; the proven lien chain on LP rows.

## 5. Verdict states: PROVEN / INFERRED / UNKNOWN(reason)

Today's states and where each lands:

| Existing state | Module | Maps to |
|---|---|---|
| `supported` / `incomplete` / `conflicted` | `case_verdict.py:48` (Miami only; 0 emitted) | supported = PROVEN for judgment+posture only (`QUALIFICATION`); incomplete = UNKNOWN(reason from `missing`); conflicted = UNKNOWN(conflict), shown first |
| `clear` / `priced` | `equity_state.FACT` | PROVEN for the lien set (recorded amounts, never a payoff); but Broward/PB are name-search so PROVEN only at `conf: ok` |
| `unpriced` | equity_state | INFERRED (a ceiling) |
| `none` / `unchecked` | equity_state | UNKNOWN(`parser_unrecognized`/`captcha_blocked`/`never_queued`) |
| `court copy` / `docket` / `not posted` | `card()` | PROVEN / INFERRED (it is the listing) / UNKNOWN |
| `active` / `possible` | `bk_lookup.verdict_of` `:1219` | PROVEN hold / INFERRED hold |
| `clear` (Miami) | bk_lookup | PROVEN only if `ok_check` and fresh; `clear_unconfirmed` = UNKNOWN(`terms_forbid` until PACER/clerk confirm) |
| `unavailable`, no cache entry | bk_lookup | UNKNOWN(`rate_limited`/`budget_exhausted`/`never_queued`) |
| `read_status`: `read` / `partial` / `image_only` / `not_read_back` | document_store | PROVEN source / UNKNOWN(`unscanned_no_ocr` or `ocr_failed`) |
| `lpDismissed`, `terminal`, `cstatus` | lp_status, OCS | PROVEN (docket status field cited by entry) / UNKNOWN(`county_no_adapter`) |

What each county needs to reach PROVEN, with the legal limits from CLAUDE.md:

| Field | Miami-Dade | Broward | Palm Beach |
|---|---|---|---|
| Case status | already reachable (anonymous OCS JSON, free). Persist it with the entry id | Commercial Data API key (`BROWARD_CLERK_API_KEY`, unset), then `DEALFLOW_LP_BROWARD=1`; disposition vocabulary **not validated** against live responses (`lp_status.py:31-38`); public Case Search is captcha + terms forbid commercial use, so no scraping | none automated. eCaseView: non-commercial terms, reCAPTCHA v3, "recurring storage permission is unresolved" (`palmbeach_documents.py:1-6`); operator-driven headed pilot only (PB matrix) |
| Judgment amount | OCS image (some behind the registered-user wall, `clerk_session.py` reports `login_required`), then a working OCR path (PowerShell is broken here) or the paid vision reader (key absent; spend needs Alex), then `judgment_money` exact-cents | recorded Final Judgment via AcclaimWeb name search, two-hop; HOA/CONO judgments often not recorded (`broward_judgment_dates.py`); then `broward_judgment_read` | judgment PDF saved by Alex, `pb_judgment_import.py` (no network); Landmark has no amounts |
| Sale date | listing is INFERRED; PROVEN needs the docket notice-of-sale entry, available for Miami only | clerk docket (API key) | none (eCaseView) |
| Liens | OR search tokens (Camoufox free / 2Captcha capped); OR daily file lapsed 10/26 (report-only) | AcclaimWeb curl works today; amounts present | at best a ceiling: Landmark has no consideration; amounts need a mortgage or judgment document read by hand |
| Stay | docket + CourtListener (125/day) + PACER by hand (`DEALFLOW_PACER_AUTO` off) | CourtListener cannot release alone (`DEALFLOW_BK_ALLOW_CL_CLEAR`); PACER by hand; `clerk_bk` needs the API key and the flag | same; `clerk_bk` holds every Palm Beach lead while the flag is on |
| Equity | derived: PROVEN only when lien set and judgment are PROVEN and fresh | same | same, ceiling-limited |

## 6. Prioritized fixes (recommendations only; nothing changed)

Effort S = under a day, M = a few days, L = a week or more. "Unblocks" counts leads whose field can move toward PROVEN or whose label becomes honest.

| # | Fix | Effort | Unblocks | Needs Alex? |
|---|---|---|---|---|
| 1 | Relabel `judgment_source`: listing figure = `auction listing` (INFERRED), TD value = `opening bid`, never `judgment`; split "not posted" into pre-judgment / listing blank / not checked (`equity_state.py:382-384`, `call_mode.py:8424`, `tracker_template.html:7812`) | S | 1,046 rows' label; removes 14 wrongly quotable TD rows; 3,288 "not posted" rows | no (display only) |
| 2 | Make the free `judgment_pass.py --collect` produce saved `*-timeline.json` for Miami FC so `case_verdict` has input and a verdict file stops being empty | S to run, M to schedule | Miami FC 369 (verdict states), dossiers 86 | run is $0 and read-only to the clerk; scheduling is a task change |
| 3 | Repair the OCR bridge: replace the PowerShell call in `fl_lp/broward_pin.ocr_images` / `document_store.winocr` with a non-PowerShell OCR (or fix the machine's PowerShell) | M | 213 pages / 111 docs / 61 Miami cases readable; prerequisite for every scanned judgment | no |
| 4 | Case-facts store (section 3.1) with adapters over the five existing caches | M | persistence for every field, all 4,634 leads; nothing is re-fetched | no |
| 5 | Queue and fetch Miami final-judgment images into `document_store`, read them, run `judgment_money`, write PROVEN facts; then turn on `DEALFLOW_COURT_JUDGMENT` | L (vision key and spend are the gate) | Miami FC 369 judgment PROVEN, 342 payoffs re-based on proven amounts | **yes**: Anthropic key and spend cap |
| 6 | Spend the CourtListener budget on leads before the filings index (last pull: 19 requests, 0 leads) and order by next contact | S | up to 125 leads/day of the 3,345 unchecked (about 27 days for the backlog, estimate) | no (inside the existing budget) |
| 7 | Broward clerk API key + `DEALFLOW_LP_BROWARD=1`, validate the disposition vocabulary on one pass | S code, key is the gate | Broward case status for 1,571 LP + clerk_bk stay for 2,216 | **yes**: key, cost, terms |
| 8 | Extend the lien trace queue to LP rows (Palm Beach 920, Miami 425, Broward 16) | M | up to 1,361 `unchecked` leads, Palm Beach capped at a ceiling | no |
| 9 | Palm Beach: keep manual. `pb_judgment_import.py` for judgments Alex saves; document the ceiling as final for liens | S | PB FC 257 judgments if PDFs supplied | **yes**: Alex does the saving; eCaseView terms |
| 10 | Broward judgment dates via the free OR daily files (`broward_or_feed.py`, 108 daily files, FJ/CFJ rows) before any captcha route | M | up to 347 Broward FC/TD payoffs | no, but the feed is off (`DEALFLOW_BROWARD_OR_FEED`) |
| 11 | Decide on the Miami OR daily file renewal (report-only today) | S | none for gates; 605 matched cases keep evidence fresh | **yes**: spend |

## 7. Could not verify

- `SCRAPER-COVERAGE-MAP`, `MIAMI-TODO`, `PALM-BEACH-DOCUMENT-FINDINGS`: absent (see top). Claims in module docstrings that cite them or the "30 of 349 verified on 2026-09-24" sweep (`judgment_pass.py:3-7`) cannot be recounted: no timelines exist on disk.
- The board numbers are from the 2026-10-30 11:06 twin on the laptop. A refresh was running at 11:40 (`refresh_progress.json` shows step `2` open); numbers will move after it publishes. The twin has no `eqc`; the 116 quotable figure is `card()` run on those rows, not read from a bake.
- OCR failure cause: shown for this machine/session (PowerShell will not start, reproduced). I did not check whether the nightly's environment, or the desktop, can run it.
- `anthropic.key` and `BROWARD_CLERK_API_KEY`: absence is from the main checkout file list and this process's environment. A key may exist on the desktop or in a scheduled task's environment.
- Share of Miami judgments behind the registered-user wall: unknown. No saved docket carries `login_required`; the case-verdict `login_walled` counts need timelines that do not exist.
- Parser breaks and timeouts: no per-run failure log was readable (`manual-refresh-20261030-1140.log` held only a prompt). Section 2 row 14 is therefore a gap, not a zero.
- Broward/Palm Beach sale postponement: inferred absent from the row fields, not tested against a live docket.
- The Broward disposition vocabulary, and whether the Commercial Data API terms allow this use, are unvalidated (`lp_status.py:31-38`).
- The `coverage` of `records_liens.json` (1,496 chains) against Miami rows was not joined case by case; the lien-state counts come from the baked `eqstate`.
- "Backlog about 27 days" in fix 6 is arithmetic (3,345 / 125), assuming one request per lead; real per-lead cost is several pages.
- Owned surface: nothing in this report touches suppression code. No owned-surface bug was found or fixed.
