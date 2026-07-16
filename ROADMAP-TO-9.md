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

## Pillar 3 — SCALE  *(one county → real coverage)*
- [ ] Broward (same RealAuction platform) — generalize the scraper with a county base-URL param → ~2–3× volume.
- [ ] Palm Beach next (same platform).

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
