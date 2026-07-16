# DEALFLOW — Roadmap from 7/10 → 9/10

The honest verdict (2026-07-15): a strong **7** as an internal deal-intelligence tool. Three things cap it.
This is the structured plan to fix them, in leverage order.

---

## Pillar 1 — TRUST THE EQUITY NUMBER  *(7 → 8, the ceiling-breaker)*
**Problem:** the flagship promise is "know the real deal before you call," but the number that most decides a
deal — *is there a 2nd mortgage / open lien* — is exactly what the county bot-walls, so the site shows
gross equity that can be a fantasy. Hondroulis proved it: "$655k equity" was wrong (hidden ~$100k 2nd).

**Fix:** automate the recorded-mortgage/lien pull and feed it into the equity + verdict engine, so every
lead shows its REAL open-lien stack instead of a guess.

- [x] `records_liens.py` — reuse the reCAPTCHA-minting OR machinery (gen_records_qs) + the satisfied-vs-open
      mortgage parsing (lookup.py). Per lead: pull the owner's recorded docs, filter mortgages to the
      subject folio, mark SATISFIED vs OPEN, and identify the surviving junior (the open mortgage that is
      NOT the foreclosing 1st). Output → `records_liens.json` (gitignored), keyed by Case #.
- [x] Prove it on Hondroulis (must detect the 2007 $100k open 2nd).
- [x] `make_tracker` bakes `orliens` (the chain) + `orjunior` (suggested surviving 2nd) into each lead.
- [x] Site: the lookup report's "Recorded mortgages & liens" section renders the REAL chain when we have it
      (open/satisfied, amounts), and Deal analysis surfaces the suggested 2nd for one-click entry.
- [x] Batch-run: 136 leads pulled via cached tokens (6 surviving-2nd hits). The remaining ~82 need fresh
      token-mints, which the county's reCAPTCHA currently bot-walls headless (returned 0/68). The 158 cached
      tokens were minted across prior sessions when the wall was down — so coverage grows opportunistically,
      it can't be brute-forced. **Open item: a paid title-data API would remove the bot-wall dependency entirely
      and get to ~100% — the real path to a rock-solid 8.**
- [x] Wired `records_liens.py --all --cached-only` into refresh-dealflow.bat (runs every cycle after the
      token-mint, so newly-cached owners get their lien chain automatically).

## Pillar 2 — RESILIENCE  *(don't silently break)*  — DONE (core)
The whole thing rests on fragile county scraping. One page change and pieces go dark with no warning.
- [x] `healthcheck.py` — checks lead count/tier split, % enriched, FC case data, lien + phone coverage, and
      PINGS all 4 upstream sources (PA GIS, Property Appraiser, Clerk OCS, RealForeclose). Prints PASS/WARN/
      FAIL, writes health.json, exits non-zero on any FAIL. Wired into refresh-dealflow.bat (runs every cycle,
      warns loudly). Baseline: 11 ok / 1 warn (61% enriched) / 0 fail = DEGRADED-but-fine.
- [ ] Surface the source-health line in the site header (bake health.json into the build). *(polish, later)*
- [ ] Graceful fallbacks: if an enrichment source is down, keep the last good value + flag it stale. *(later)*

## Pillar 3 — SCALE  *(one county → real coverage)*  — RECON DONE, BUILD STARTED
Parallel recon mapped all 8 data sources for Broward + Palm Beach (2026-07-16). Key findings:

- **THE UNLOCK — statewide enrichment, one CORS-open API.** The FL DOR "Florida_Statewide_Cadastral"
  ArcGIS layer (services9.arcgis.com/Gh9awoU677aKree0/…/FeatureServer/0) is public + `Access-Control-
  Allow-Origin: *` + covers all 67 counties (keyed CO_NO). Returns owner, market value (JV), assessed,
  homestead (JV_HMSTD), living sqft, year, last 2 sales — by parcel or address. So **owner+value for ANY
  FL county needs NO per-county Property Appraiser integration**, and it works straight from the browser.
  Gap: no beds/baths (annual roll). CO_NO alphabetical from 11: Broward=16, Miami-Dade=53, Palm Beach=60.
  - [x] Built `fl_cadastral.py` (enrich by parcel or county+address; backoff for the free host's throttling).
- **BUILD BROWARD FIRST (not Palm Beach).** Why: Broward's clerk is a plain no-captcha API
  (browardclerk.org/Web2/CaseSearchECA) and Broward has a FREE bulk Official-Records feed (no captcha) —
  which *solves the reCAPTCHA lien-wall that capped Miami-Dade at 62%*. Palm Beach walls both its clerk
  (eCaseView reCAPTCHA v3) and its records (Landmark reCAPTCHA v2). Auctions are identical for both
  (countyname.realforeclose.com, browser-render like Miami-Dade). Broward folio=12-digit, PB PCN=17-digit.
- [x] Broward auction scrape (broward.py) + statewide-cadastral enrich + make_tracker merge + a
      county filter/chip on the site. LIVE: 209 Broward leads (100 enriched, 42 Tier A). 490 leads total.
- [x] **Broward lien chain — THE Pillar-1 win for county #2 (`broward_liens.py`).** Broward's Official
      Records (AcclaimWeb) is DISCLAIMER-gated, NOT reCAPTCHA-gated — so unlike Miami-Dade we get the full
      recorded mortgage/satisfaction chain per owner. (Cloudflare blocks python-requests + headless + curl_cffi;
      the native Windows curl binary passes, so the module shells out to curl. `Search/GridResults` returns
      clean Telerik JSON.) Conservative analyze() mirrors records_liens.py: exact (last,first) + borrower-side
      (Party="From") + same-institution satisfaction match + hard guards (common name / MERS / >3 open → conf
      'low'). Batch: 193 traced, 57 lien chains on the site, **5 confident surviving-2nds** (GEDEUS $88k,
      ARZOLA $60k, KREHMEYER $55k, …). Wired into make_tracker via a generic `<county>_liens.json` merge +
      refresh-dealflow.bat. So Broward's equity number is now backed by real records, not a guess.
- [x] Multi-county data model: leads tagged by county; auto-populated county filter + BROWARD chip.
- [x] In-site lookup → statewide via fl_cadastral for non-Miami-Dade (MD keeps its richer GIS). Any FL
      address/parcel resolves; Promise.race hard-timeout so a slow/down free host degrades to "No match"
      instead of hanging; report header is county-aware (no more "Miami-Dade" on a Broward lookup).
- [x] Palm Beach second (auctions + fl_cadastral enrich; clerk/liens browser-gated like Miami-Dade).
      LIVE: 155 PB leads (133 enriched, 57 Tier A). PB records ARE captcha-walled (Landmark reCAPTCHA) so
      PB liens are the next target — the Broward curl-session pattern won't port (different vendor).

## Pillar 4 — DEPTH  *(from the original roadmap, sequence after 1–3)*
- [ ] Surplus-recovery module (capital-light income: former-owner surplus after over-bid sales).
- [ ] Lis-pendens front-of-funnel (catch owners 12–24 mo earlier — the biggest structural edge, hardest data).

---

## What "9" looks like
A tool where the equity number is **trustworthy without leaving the browser** (Pillar 1), that **tells you
when it's broken** instead of lying quietly (Pillar 2), across **more than one county** (Pillar 3). Pillars
1–2 are the jump from 7→8; Pillar 3 + real usage is 8→9.

## Non-goals / guardrails
- Not becoming a SaaS with auth/billing — it's an internal tool; keep it a gated static site.
- Never surface a fabricated number: a suggested lien from records is labeled "suggested — verify," and the
  money math only trusts what the user confirms.
- Compliance lives in the operation, not the tool (FS 501.1377 etc. — already surfaced).


## TESTED & RULED OUT (2026-07-16) — don't re-buy these
The path to ~100% lien coverage was blocked by the county's reCAPTCHA bot-wall. I tested every option:
- **Paid property-data API (BatchData, existing key):** RULED OUT. Returned mortgageHistory=0 / freeAndClear=true
  on Hondroulis — a property in DOUBLE foreclosure with ~$291k of mortgages. Aggregator mortgage data is
  stale/incomplete; it would re-introduce the fantasy-equity lie. Keep BatchData for skip-trace only.
- **Captcha-solver (2Captcha, $5):** RULED OUT. Solves the reCAPTCHA v3 fine, but the county server rejects
  the solved tokens (isValidSearch:false at min_score 0.3 / 0.7 / 0.9). Its score validation beats the solver.
- **VERDICT:** the FREE county Official Records are the most accurate source (records_liens.py got it right);
  the only barrier is the bot-wall, which can't be bought or solved cheaply. Coverage (136/218 = 62%) grows
  opportunistically via the weekly refresh when the wall relaxes. Do NOT escalate (proxies/farms) or buy data
  APIs for a two-person op. Per-deal gap is covered by the in-tool Records link + the title agent.
