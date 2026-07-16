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
- [ ] Batch-run over all leads (best-effort, resumable — OR is bot-walled ~half the time headless).
- [ ] Wire the batch into the weekly refresh so it stays current.

## Pillar 2 — RESILIENCE  *(don't silently break)*
The whole thing rests on fragile county scraping. One page change and pieces go dark with no warning.
- [ ] `healthcheck.py` — after each refresh, assert: lead count in range, PA/GIS/Clerk reachable, % enriched,
      % with a value, records-pull success rate. Write a status line + fail loudly.
- [ ] Surface a "data freshness / health" line in the site header (already has the updated stamp; add source health).
- [ ] Graceful fallbacks: if an enrichment source is down, keep the last good value + flag it stale, never blank.

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
