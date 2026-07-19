# DEALFLOW — Gameplan: press the advantages, kill the disadvantages, make money

_Working strategy doc. Numbers marked (est.) are market-typical ranges to sanity-check against your
own comps — not quoted facts. Nothing here is legal advice; the tool's own compliance posture
(attorney review before contracts/surplus work) applies to everything below._

---

## 0. What the moat actually is (and isn't)

The asset is NOT the UI and NOT "leads scraped from auction sites." PropStream, BatchLeads, and
Auction.com all sell raw rows. The moat is the **verified-answer layer** on top:

- recorded **lien chains** that turn "equity" from a guess into a fact (`records_liens.py`)
- a **verdict brain** that knows HOA judgments leave the 1st mortgage surviving, condo 40-yr
  recert assessments, vacant-land false positives
- **skip-traced contacts + DNC flags**, refreshed daily across 3 counties
- a **compliance ledger** (FTSA opt-out audit trail) nobody else sells at any price

Every plan below either **compounds** that layer or **sells** it. Anything that doesn't do one of
those two things is a distraction.

---

## 1. Kill the disadvantages (from the investor review)

| Weakness | Fix | Effort |
|---|---|---|
| Ops reliability 5/10 — one laptop, one 9am task | Move the pipeline to GitHub Actions or a $6/mo VPS; `healthcheck.py` already exists → wire failure to an SMS/email alert; add proxy rotation for the bot-walled sources (captcha infra already in repo) | 1 weekend |
| Business model 4/10 — no revenue surface | Two surfaces in §3 (dispo + seats). Deliberately NOT accounts/billing infra — `access_codes.py` + Stripe Payment Link is enough to 20 seats | small |
| Defensibility 4/10 — "PropStream could clone it" | They can clone features. They cannot clone your **results database**, your **buyer relationships**, or your **compliance ledger**. Build those three (§2 P0) and the castle exists | ongoing |
| Market size 4/10 | Don't widen geography yet. Deepen the money per lead first; expand counties only after seats sell (§2 P3) | later |

---

## 2. Data plan — ranked by value ÷ effort

### P0 — Auction RESULTS (the single highest-leverage scrape)
Scrape completed-auction outcomes from all 3 clerks: sold price, cancelled, plaintiff bid, buyer
name / certificate of title. Today the tool knows *what might sell*; results tell you *what sold,
for how much, and to whom*. Unlocks four things at once:
1. **Win-rate comps** — what percentage of judgment/value do winning bids actually clear at, per
   county, per case type. Your verdict model calibrates on reality instead of estimates.
2. **Relist detection** — cancelled sales come back; a lead seen 3 times is a pattern, not noise.
3. **Cash-buyer database** — buyer names on certificates of title = proven, active auction buyers.
   This is your disposition list (§3.1). Nobody sells this list; it compounds daily for free.
4. **Track record** — "last 90 days: X sales analyzed, avg winning bid Y% of value" is the proof
   that sells seats (§3.2).

### P0 — Buyer/deed-transfer table
Post-auction deed transfers (Official Records, already scraped for liens) → table of who buys at
auction, what they pay, how often. Pure byproduct of results tracking + existing lien scraper.

### P1 — Automated ARV/comps
The tool currently warns "county value ≠ ARV" and moves on. Pull sold comps from county sales
records (SFREA is public) or Zillow sold data → a 3–5 comp pack per lead with an estimated ARV.
This converts the profit numbers from "model guess" to "defensible bid ceiling" — the difference
between a tool you browse and a tool you *bid from*.

### P1 — Probate filings with real property
County probate dockets are public. Fresh probate + real estate = motivated heirs, often
out-of-state, far less competition than foreclosure lists. Same skiptrace + cadence machinery
applies verbatim.

### P1 — Code enforcement / unsafe-structures dockets
Miami-Dade, Broward, and Palm Beach all publish code-lien and unsafe-structures actions. This is
distress 6–18 months BEFORE the foreclosure filing — the window where a seller still has options
and investors have none of the auction competition.

### P2 — Flood zone + roof/insurance viability (Florida-specific deal-killer)
FEMA flood zone per parcel (free NFHL API) + roof-age signal from county permit records. In 2026
Florida, insurance viability decides whether your exit buyer can finance the purchase. A "flood +
20-yr roof" flag next to the verdict is worth real money and almost nobody surfaces it at the
lead-list stage.

### P2 — Eviction filings (tired landlords) & divorce-with-property
Both public dockets. Lower volume, decent conversion, sensitive-tone outreach required (the letter
generator already does neighbor-safe wording).

### P2 — Owner-portfolio clustering (zero new scraping)
Same owner name on 2+ distressed parcels = a landlord liquidating, and bulk deals price better.
This is pure computation over data you already have — one SQL-style pass, no new scrape.

### P2 — Mortgage payoff estimation
Recorded mortgage amount + recording date + standard amortization → estimated CURRENT balance,
sharper than "recorded balance." Feeds the equity number the verdict already shows.

### P3 — Tax certificate sales (earlier entry)
The May/June certificate sales are the top of the tax-deed funnel you already work at the bottom
of. Buying certificates or reaching owners at certificate stage = 2–3 years earlier entry.

### P3 — More counties — only after revenue
St. Lucie, Martin, Lee, Hillsborough, Duval. `county_leads.py` already generalizes this. Do it
when seats sell, not before — geography is a scaling move, not a survival move.

---

## 3. Money plans — ranked by speed-to-cash

### 3.1 Disposition engine — biggest check per hour, ~zero build (but gated on a closer)
The engine already finds STRONG deals; the money in this business is the assignment fee, not the
data. With the P0 buyer database you have a proven-buyer list; with the existing document
generators you have the underwriting pack. Run the play: weekly "vetted deal blast" to proven
auction buyers — verdict, lien chain, comp pack, photos, terms.
- Economics (est.): SFL assignment fees commonly $10–30k/deal. One deal per quarter covers all
  costs of everything on this page. One per month is a business.
- **Honest dependency — the closer.** Dispo money requires someone who can actually run a deal to
  the table: seller outreach → negotiate → contract → buyer → title → close. If the operator on
  this team is still in their first live deal, "$10–30k" is a spreadsheet number until the FIRST
  close lands. Checkpoint: **first deal closed within 90 days, yes or no.** If yes, dispo is the
  primary surface. If no, §3.2 seats become the first-money surface and dispo waits for a proven
  closer (or partners with one for a split).
- Realistic timeline for the first dispo dollar with a green operator: **month 4–6**, not weeks
  3–4. Send the blast as soon as the buyer list exists (it costs nothing and builds the buyer
  relationships early) — just don't budget rent money from it before month 4.

### 3.2 Sell the feed — seats, small build (do SECOND — but pick the customer first)
Don't sell software. Sell **answered questions**: "South Florida auctions, pre-underwritten —
surviving liens verified, contacts included, verdict + reasoning, every morning." 10–20 seats at
(est.) $150–300/mo = $3–6k/mo at near-zero marginal cost. The seat mechanism already exists
(`access_codes.py`, 6 live codes). Sell via a one-page proof site built from the P0 track record,
take payment by Stripe Payment Link, onboard by issuing a code. Do NOT build accounts, dashboards,
or billing until 10+ people are paying.
- **The price needs a named segment.** $150–300/mo is 1.5–3× PropStream, so "SFL wholesalers" is
  too broad — the sharp locals don't need you, the new ones can't pay $300. Segments this feed is
  actually worth that to: **(a) out-of-state investors** who don't know Florida lien rules and get
  burned by exactly the traps the verdict brain catches; **(b) fund/family-office scouts** who
  want one vetted metro feed instead of five raw ones; **(c) lenders/note buyers** monitoring
  collateral in one metro. Pick ONE before opening seats and write the proof page to them.

### 3.3 Surplus funds recovery — high ticket, legally narrow (own sub-doc BEFORE any marketing)
The plan modal already flags surplus checks. After an auction, overage above the judgment belongs
to the former owner — routinely $20k–100k+ on the equity-rich leads this tool specializes in, and
the results scrape (P0) surfaces every candidate automatically. But the legal lane is narrower
than "attorney review before the first letter":
- **FL Bar Rule 4-5.4** prohibits fee-sharing between lawyers and non-lawyers — a non-attorney
  taking a **contingency cut** of the surplus is on the wrong side of it.
- **FS 45.032 / 45.033** govern who files the claim and how. The compliant shapes are: **flat-fee
  filing assistance** (you help the owner file their own claim for a fixed service fee) or an
  **attorney partnership** where the attorney's fee agreement carries the recovery.
- That changes the economics (flat fees, not 30% of $80k), so surplus gets its own sub-doc with
  the structure sketched by an actual Florida attorney BEFORE any outreach copy is written. The
  compliance ledger (opt-out audit trail) is already the evidence system a lawyer will ask for.

### 3.4 Monitoring alerts — cheap subscription, high volume (do FOURTH)
"Watch this address / this criterion — email me the morning it hits." Agents want listing signals,
landlords want their own portfolio watched, lenders want collateral watched. Est. $20–50/mo, and
it is nearly free to run once P0–P1 exist: the pipeline already diffs daily; an alert is a diff +
an email.

### 3.5 Done-for-you compliant outreach — margin on ops (add-on, later)
You already have Lob batching, bilingual compliant letters, DNC suppression, and the audit ledger.
Sell "we mail your list compliantly" as a seat add-on. Margin on operations, not data.

### What NOT to do
- Don't sell raw data rows — that's a price war against PropStream you lose.
- Don't build account/billing infrastructure before 10 paying seats exist.
- Don't add counties before the first revenue lands.

---

## 4. Order of operations

Each step makes the next one cheaper. Timelines are honest, not optimistic:

1. **Weeks 1–3:** P0 results scraper (3 clerks) + buyer table — realistically **2–3 weekends**:
   results sit behind the RealForeclose bidder login (verified 2026-07 — anonymous hits get the
   splash page), one of the three counties will likely need Playwright + proxy rotation on top of
   the already-flaky Broward path. Same sprint: pipeline moved to GitHub Actions/VPS with
   healthcheck failure alerts.
2. **Weeks 3–4:** First dispo blast goes out (costs nothing, starts buyer relationships) — but
   budget the first dispo DOLLAR for **month 4–6**, and set the 90-day closer checkpoint (§3.1).
3. **Month 2:** P1 comps/ARV + probate; proof page from the growing results record; pick the ONE
   seat segment (§3.2) and open sales to it.
4. **Month 3:** Surplus sub-doc drafted by a Florida attorney (§3.3) before any surplus outreach;
   alerts MVP; then — and only then — evaluate county expansion.

## 5. First five concrete moves (this week)

1. Run `login-setup.py` once (you type your own bidder credentials — nothing reads them) so the
   results scraper can see past-auction pages; without it the P0 build is blocked on day one.
2. Spec `auction_results.py` (see `AUCTION-RESULTS-SPEC.md`): per-clerk past-auction endpoints,
   the case-# join key, sold/cancelled/plaintiff schema. Store as `results.json`, gitignored.
3. Buyer table from certificates of title (Official Records — same scrape path as `records_liens.py`).
4. Move the 9am refresh to GitHub Actions with the encrypted secrets it already uses; failure → SMS.
   (Broward timed out and killed the refresh on 2026-07-18 — this move is overdue, not optional.)
5. Dispo blast template: the tool already renders the deal pack — add a one-page "for investors"
   PDF per STRONG lead.

---

_The honest edge: you are not going to out-feature PropStream. You are going to out-ANSWER them in
one metro — verified liens, real verdicts, real results, real buyers, compliant outreach — and that
is a thing a small sharp operator can own where a platform can't bother to._
