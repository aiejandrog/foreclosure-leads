# AUCTION-RESULTS-SPEC — `auction_results.py` (P0 build)

_Verified against the live sites 2026-07-19. Where something still needs a logged-in discovery
session it is marked **[OPEN]** — those items are answered by driving the logged-in UI, not by
guessing harder._

## 1. What we verified today (not guesses)

- All three counties run the same RealForeclose platform: `{sub}.realforeclose.com/index.cfm`
  (`miamidade`, `broward`, `palmbeach` — see `county_leads.py` COUNTIES).
- The existing pipeline's URL grammar works for UPCOMING sales:
  `?zaction=USER&zmethod=CALENDAR&selCalDate=MM/DD/YYYY` then
  `?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=MM/DD/YYYY` (`foreclosure_leads.py:162-201`).
- **Past auction data is login-gated.** Anonymous requests for past dates render the splash/login
  page (verified for CALENDAR on a past date and for `zmethod=RESULTS`/`ARCHIVE`/`SALESRESULT` —
  all return the identical splash). No anonymous results endpoint exists on this platform.
- `login-setup.py` already exists for exactly this: it captures a persistent `browser-profile/`
  session (user types their own credentials; nothing reads them). **No `browser-profile/` exists on
  this machine right now — Alejandro must run it once before any results work.**
- **The login is PER-COUNTY.** Cookie jars are per-domain: a Miami-Dade session does NOT
  authenticate `broward.realforeclose.com` or `palmbeach.realforeclose.com`. `login-setup.py`
  steps through all three county login pages in one browser session so all three jars land in the
  same `browser-profile/`. Missing one county = that county's results silently skip.

## 2. Deliverable

`auction_results.py` — a weekly (not daily) scraper that writes `results.json` (gitignored like
`leads_final.json`), joining every closed auction back to the lead it came from.

### Record schema (one per case per sale date)
```json
{
  "case": "2024-000101-CA-01",
  "county": "MIAMI-DADE",
  "sale_type": "FC",
  "sale_date": "07/16/2026",
  "status": "sold | cancelled | plaintiff",
  "final_bid": 412000,
  "buyer": "OCEAN BREEZE 777 LLC",
  "judgment": 286000,
  "value_at_sale": 655000,
  "bid_pct_judgment": 144,
  "bid_pct_value": 63,
  "first_seen": "2026-06-02",
  "times_listed": 2
}
```
- `status` three-way: **sold** (third-party buyer), **plaintiff** (bank took it back — the bid
  pattern usually shows it), **cancelled** (sale pulled; feeds relist detection).
- `buyer` when the platform publishes the winning bidder / certificate holder; feeds the buyer
  table (GAMEPLAN §2 P0). May require the certificate-of-title pass through Official Records
  (`records_liens.py` path) where the site only shows "sold."
- `judgment` / `value_at_sale` are copied from the lead at join time so every result row is
  self-contained (the lead list rotates; results must not).
- `times_listed` — how many prior calendar appearances this case had (relist pattern).

### Join key
Normalized `(county, case_number)`:
- Miami-Dade: `2024-000101-CA-01` — strip spaces, uppercase.
- Broward: `CACE-26-002995` — same normalization.
- Palm Beach tax deeds: certificate number (`2026A00219`) is the stable key, NOT a case number —
  store it in `case` with `sale_type: "TD"` and treat cert# as canonical.
Normalizer lives in one function shared with `make_tracker` so the keys can't drift.

## 3. Endpoint discovery plan (first build session)

With the logged-in `browser-profile/`:
1. Past-date CALENDAR → collect the per-day link grammar for completed sales **[OPEN]** — expected
   to hang off `zaction=AUCTION` with a results-flavored zmethod or a per-day link from the
   calendar cell; verify by clicking through, then freeze the grammar in one constant block like
   `discover_dates` does.
2. Per-day results page → identify the results table markup (likely the same `#Area_W
   .AUCTION_DETAILS` family as PREVIEW, with extra sold/bid columns) **[OPEN]**.
3. Pager: reuse the proven "click Next until first case stops changing" loop from `scrape_date`.
4. Backfill window: start with the last 90 days, then keep a rolling weekly pull of "days that
   closed since last run" (cheaper than re-pulling history).
5. Buyer gaps → certificate-of-title pass via Official Records for `status=sold` rows missing
   `buyer`.

## 4. Pipeline slot & UI landing

- Slot: runs after the weekly scrape, before `make_tracker`; `make_tracker` joins `results.json`
  onto leads by the §2 key (exactly how `records_liens.json` and skiptrace results merge today).
- Tracker UI (additive, no logic rewrites):
  - Verdict area per lead with a result: `last sold 63% of value (07/16)` — the comp-table flex.
  - Deal modal: small "auction history" block (times listed, prior cancellations, last outcome).
  - New digest tile later: "90-day clear rate — winning bids avg X% of value" (the seat-proof stat).
- `healthcheck.py` extends to results freshness (results older than 8 days = warn).

## 5. Reliability, ToS & account risk (honest)

- **2–3 weekends, not one.** Broward's existing path is already flaky (killed the refresh on
  2026-07-18); expect one county's results view to need Playwright + proxy rotation, which the repo
  has captcha/cookie infra for (`captcha.key`, `tps_cookies.txt` patterns).
- **ToS / ban risk is real, plan for it.** The results pages carry
  `<meta name="robots" content="noindex,nofollow">` — the platform does not want them indexed.
  The underlying data is public record (only the access layer is account-gated), so this is a
  gray zone, not a clear violation — but the bidder ACCOUNT can be banned if detection trips.
  Mitigations:
  1. **Crawl politely**: jittered `time.sleep(3–6s)` between page fetches, weekly cadence (not
     daily) — results don't age like live leads, and the slow-human pattern keeps the account
     boring.
  2. **Official Records fallback**: the certificate of title (deed transfer) is ground truth for
     `status=sold` + buyer + effective price, reachable via the existing `records_liens.py` path.
     If the RealForeclose account is ever banned, the sold column degrades to records-confirmed
     instead of going dark. Build the join so the records pass can stand alone.
- Failure mode: a county's results view changes markup → that county skips with a loud log line
  (same `try/except` per-county pattern as `make_tracker`'s county merge), never kills the build.

## 6. Definition of done

- `results.json` with ≥90 days of MD+BW+PB results, ≥95% of sold rows joined to a lead or
  explainably unjoined (never listed).
- One logged re-run is idempotent (no duplicate case+sale_date rows).
- Tracker renders the `sold X% of value` line on at least one real lead with zero console errors
  in the existing 22-check suite + a new results-check in `_redesign_verify.py`.
