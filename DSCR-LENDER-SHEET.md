
# DSCR Lender Contact Sheet — Capital Stack Addition

> **For:** foreclosure-leads / DealFlow portfolio track  
> **Last updated:** 2026-08-24  
> **Source:** Jose Masterclass intelligence (2026-08-12 call) + Alejandro credit profile verification  
> **Status:** ACTIVE — contacts need verification before first submission

---

## WHAT IS DSCR?

**Debt Service Coverage Ratio (DSCR)** financing is a **non-QM (non-qualified mortgage)** loan product for investment properties. Instead of qualifying the borrower by personal income (W-2, tax returns), the lender qualifies the **property's cash flow**:

> **DSCR = Gross Rental Income ÷ Total Debt Service (PITIA)**

If the property's rent covers the mortgage payment by a sufficient ratio (typically 1.0x–1.25x), the loan funds. The borrower's personal DTI is irrelevant.

This is the financing lane that makes the **portfolio track** (PLAYBOOK.md §2.5) viable: $3,200 rent vs $1,400 payment = 2.29x DSCR = easy approval.

---

## ALEJANDRO'S CURRENT PROFILE (as of 2026-08-12)

Per Jose's assessment and credit pull:

| Parameter | Value |
|---|---|
| **Max LTV** | 80–85% |
| **Rate range** | 5.5%–6.75% (market-adjusted; verify current) |
| **Speed** | 5–10 business days (faster at ~7% rate) |
| **Credit requirement** | "A1" profile — 700+ FICO, clean recent history |
| **Property types** | SFR, duplex, triplex, fourplex (non-homestead) |
| **Loan purpose** | Purchase or refinance (rate/term or cash-out) |
| **Prepay penalty** | Typically 3-year stepdown (3%/2%/1%) or none |
| **Points** | 1–2 points typical |

**Key structural note:** Jose's "profit at purchase" model (buy at 70% of value, finance at 80–85%) requires the appraisal to support the higher value. The DSCR lender orders the appraisal; it must come in at or above the target LTV value.

---

## LENDER CONTACTS (PLACEHOLDERS — VERIFY BEFORE USE)

> **ACTION REQUIRED:** These are skeleton entries. Alejandro must populate with real contacts from Jose's rolodex or independent broker relationships. Do NOT submit loans to placeholder contacts.

| Lender / Broker | Contact | Product | Min DSCR | Max LTV | Rate (est.) | Points | Speed | Notes |
|---|---|---|---|---|---|---|---|---|
| **Jose Preferred — TBD** | [PHONE] | DSCR purchase + refi | 1.0x | 85% | 5.5–6.5% | 1.5 | 5–7 days | Jose's primary relationship; get contact from him |
| **Jose Backup — TBD** | [PHONE] | DSCR cash-out | 1.1x | 80% | 6.0–7.0% | 2.0 | 7–10 days | For deals that don't qualify at primary |
| **Kiavi (formerly LendingHome)** | kiavi.com / 855-XXX-XXXX | DSCR, bridge, fix-and-flip | 1.0x | 80% | 6.5–8.5% | 2–3 | 10–14 days | National lender; online application; good for backup |
| ** Lima One Capital** | limaone.com / 855-XXX-XXXX | DSCR, bridge, new construction | 1.2x | 80% | 6.0–8.0% | 2–3 | 10–14 days | Strong in Southeast; relationship-driven |
| **Visio Lending** | visiolending.com / 512-XXX-XXXX | DSCR only (pure play) | 1.0x | 80% | 6.5–7.5% | 1.5–2 | 14–21 days | Longer close but competitive rates |
| **Angel Oak Prime Bridge** | angeloak.com / 404-XXX-XXXX | DSCR, non-QM | 1.0x | 85% | 6.0–7.5% | 2–3 | 14–21 days | Wholesale-only; need mortgage broker license |
| **New Silver** | newsilver.com | DSCR, fix-and-flip | 1.0x | 80% | 7.0–9.0% | 2–3 | 7–10 days | Faster but more expensive; good for bridge |

---

## SUBMISSION CHECKLIST

Before submitting ANY loan package to a DSCR lender:

- [ ] **Property rent survey:** Current lease or market rent estimate (3 comparable rentals)
- [ ] **DSCR calculation:** Rent ÷ PITIA ≥ lender's minimum (usually 1.0x or 1.2x)
- [ ] **Appraisal order:** Lender orders; borrower pays ~$500–700 upfront
- [ ] **Entity docs:** LLC operating agreement, EIN letter, articles of organization
- [ ] **Purchase contract:** If purchase, fully executed with all addenda
- [ ] **Title commitment:** Clean title with no surprises (liens, judgments, HOA issues)
- [ ] **Insurance quote:** Hazard insurance bound at closing
- [ ] **Liquidity verification:** Some lenders require 6–12 months PITIA in reserves

---

## THE "INSIDE STRUCTURE" (Jose's Method)

This is how Jose engineers the day-one equity capture:

```
Step 1: Contract to buy Property A at $250,000 (70% of $357k market value)
        ↓
Step 2: Form LLC-B (the holding entity)
        ↓
Step 3: LLC-A (purchase entity) sells to LLC-B at $345,000
        ↓
Step 4: DSCR lender finances LLC-B at 90% of $345k = $310,500
        ↓
Step 5: At closing: $310,500 loan - $250,000 payoff to LLC-A = $60,500 gross
        ↓
Step 6: Seller covers ~3% closing costs (~$10,350)
        ↓
Step 7: Net profit at purchase ≈ $50,000+ before first tenant
```

The $345k sale from LLC-A to LLC-B must be **arm's-length** with a legitimate appraisal. The appraisal must be defensible: comparable sales, proper adjustments, no undue influence on the appraiser.

**Integration with DealFlow:** The `leads_final.json` schema should track:
- `arv_estimate` (after-repair value)
- `purchase_price` (contract price)
- `dscr_appraisal_target` (the value the lender's appraisal must support)
- `holding_llc` (the entity that will own long-term)
- `purchase_llc` (the entity that contracts)

---

## RATE ENVIRONMENT (VERIFY CURRENT)

Rates move daily. The 5.5–6.75% range cited by Jose on 2026-08-12 may have shifted. Verify with each lender before quoting to sellers.

**Current market indicators to check:**
- 10-year Treasury yield (proxy for mortgage rates)
- SOFR / 30-day average (benchmark for most DSCR ARMs)
- Fannie Mae Multifamily rate sheet (for comparison)

**Integration:** Add a `rate_check.py` script that scrapes or API-calls current DSCR rate indicators from 2–3 lender websites weekly, storing results in `dscr_rates.json` for the tracker.

---

## NEXT ACTIONS

1. **Get Jose's actual contacts:** Schedule 15-minute call with Jose to populate the "TBD" rows with real names/phones.
2. **Verify Kiavi/Lima One rates:** Call or apply online for a **rate quote** (no credit pull) to confirm current pricing.
3. **Broker license check:** If Alejandro will be submitting loans directly (not through a licensed mortgage broker), verify whether Florida requires an **MLO license** or **mortgage broker license** for DSCR submissions. (Likely: no license needed if referring to a licensed broker; license needed if taking applications or negotiating terms.)
4. **Build `rate_check.py`:** Automated weekly rate scrape for the tracker.
5. **Add to tracker schema:** `dscr_lender`, `dscr_rate_locked`, `dscr_appraisal_value`, `dscr_loan_amount` fields.

---

*Compiled by Kimi K2.6 on 2026-08-24 from Jose Masterclass intelligence. Integrates with PLAYBOOK.md §2.5 (DSCR hold / portfolio track).*