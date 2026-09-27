# HOMEOWNER OPTIONS — keep, sell, refinance or bankruptcy, case by case

_Biscayne Solutions Group. Written 2026-09-24 at Alejandro's ask. The code version is
`options_strategy.assess()`; `_optionstest.py` pins every rule below._

**Status: designed, not applied.** Nothing on the board or in Call Mode uses this yet. It goes live
on real cases only after the Miami read verification (judgments, documents, mortgages) reports,
because every branch leans on the equity state and the lien chain being right.

Operating document, not legal advice. Items marked *attorney to confirm* are my reading, not a
lawyer's.

---

## The one idea

Find out what the owner wants, then tell them the truth about the path that fits, **even when that
path pays us nothing.** Only one path pays us: a wholesale sale, with the fee paid by the cash
buyer at closing. The other paths are free hand-offs. Handing them off honestly is what keeps us
outside Florida's foreclosure-rescue law, and it's how an owner ends up trusting us enough to sell
when selling is the right answer.

## Stop before strategy

| On the card | What it means |
|---|---|
| Bankruptcy stay active | No contact of any kind until it lifts. No strategy. |
| CLAIMED (title transferred) | Nothing is left to decide. |
| Case dismissed | There's no sale to plan around. |
| Sale date passed | Only surplus or redemption questions remain, and a lawyer answers those. |
| Owner has a lawyer in the case | Jesse hears about it first. Nothing is proposed around their counsel. |

## Step 1: ask, never assume

> "Do you want to keep the house, or sell it?"

If they say **undecided**, lay both sides out and let them choose. We don't pick for them.

## Step 2: keep

| Situation | First path | Who does it | Also mention |
|---|---|---|---|
| Sale is under 30 days away | Ask the court for time | Attorney | Workout, bankruptcy consult |
| Association (HOA) case, can pay going forward | Pay the association judgment | Owner, using the plaintiff attorney's payoff letter | Court time, bankruptcy consult |
| Bank case, can pay going forward | Lender workout (repayment plan, forbearance, modification) | Servicer plus a HUD-approved counselor (free) | Court time, bankruptcy consult |
| Can't carry a payment | Lender workout, said gently | Servicer plus counselor | Ask once whether selling is worth a look. Don't push. |
| Verified equity of 30% or more, can pay, lives there | (alternative) Refinance | Licensed loan originator | |

We earn nothing on any keep path. **We never offer "sell it to us and rent it or buy it back."**
Florida law treats that as a foreclosure-rescue transaction (s. 501.1377(5)–(6)). It needs a
statutory contract and a 3-business-day cancellation right. A buy-back price over 17% a year is
presumed unconscionable, and a lease-option is presumed to be a mortgage.

## Step 3: sell

The gate comes first. If the equity state isn't **VERIFIED** or **CLEAR**, there's no number, no
offer and no path. Ask who holds the first mortgage, roughly what's owed, and whether it's current,
then get the signed third-party authorization for the payoff.

| Verified equity | Time to sale | First path | Who |
|---|---|---|---|
| Any | Under 12 days | Ask the court for time | Attorney |
| Underwater | Any | Short sale or deed in lieu | Servicer plus a licensed agent or attorney |
| Thin (under 15%) | 75+ days, decent condition | List it | Licensed agent. Tell them a cash discount leaves them little. |
| Thin | Short, or poor condition | Wholesale | **Us** |
| 15% or more | 75+ days, decent condition | List it, with wholesale as the alternative | Licensed agent. Offer speed and certainty as the reason to take cash. |
| 15% or more | Under 75 days, or poor condition | **Wholesale** | **Us** |

**This is the lane that pays:** verified equity, a sale date too close to list, or a house that
won't show. Those rows are where a cash offer is truly the owner's best deal.

## Bankruptcy

It is never the first path we name. We never say "file." We say a bankruptcy attorney can tell
them whether it's an option, and we make that referral for free. If they had a bankruptcy case in
the last 12 months, the attorney needs to know up front, because the new stay is limited
(11 U.S.C. 362(c)(3)).

## Never say

- that we can stop the sale
- an equity or offer number while the equity is unverified
- that they should file bankruptcy
- buy-it-back, rent-back or lease-option as a way to keep the house
- that we charge anything, now or later, for keeping the house

## Thresholds (operating defaults, not law; set in `options_strategy.py`)

- **12 days to close** a purchase: the seller's 3-business-day cancel window, plus closing at
  least 3 business days before the sale, plus title and payoff turnaround.
- **75 days to list**: roughly 30–45 days to a contract and 30–45 to close.
- **15% and 30% equity lines**: 30% is Jesse's qualify line.
- **30 days**: inside this, only a court filing can move a sale date.

---

## Conflicts with PLAYBOOK.md that need a lawyer before anyone acts on them

Each one is my reading of the law, not settled. I haven't changed PLAYBOOK.md.

1. **§1.2 and §2.2, the "stop the sale" fee ($2,500 + costs).** s. 501.1377(3)(b) bars collecting
   any payment for "stopping, avoiding, or delaying foreclosure" before every service is fully
   performed. The attorney exemption covers a lawyer's own services, not ours. AFTER-THE-YES already
   parks this lane. The playbook still lists it as a product.
2. **§1.3 and §2.3, "hard equity loan, 2 points to us."** On a homeowner's own house, a referral
   fee on the loan is believed barred by RESPA §8 (12 U.S.C. 2607). Taking points is believed to
   require a Florida loan originator license (ch. 494). *Attorney to confirm.* In this document,
   refinance is a free hand-off.
3. **§1.7, "Realtors pay 1–2% referral."** Paying a commission to an unlicensed person is believed
   barred by ch. 475. *Attorney to confirm.* In this document, listing referrals are free.
4. **§5, "Attorney on contingency, 20% theirs / 10% ours."** A lawyer splitting a fee with a
   non-lawyer is believed barred by Florida Bar Rule 4-5.4. *Attorney to confirm.*
5. **RENT-BACK-OCCUPANCY-AGREEMENT-TEMPLATE.md.** A rent-back after buying from an owner in
   foreclosure falls under s. 501.1377(5)–(6). It needs attorney paper every time and isn't a
   keep-the-house offer.

## Sources

- s. 501.1377, Fla. Stat. (2025), read 2026-09-24:
  https://law.justia.com/codes/florida/title-xxxiii/chapter-501/part-i/section-501-1377/
  - Definitions: "foreclosure-related rescue services" means stopping, avoiding or delaying
    foreclosure, or curing a default.
  - Exemptions: attorneys, financial institutions, licensed mortgage lenders and brokers, 501(c)(3)
    counselors and HUD-authorized agencies.
  - (3)(b): no fee before full performance.
  - (4): 3-business-day cancellation on a service agreement.
  - (5): foreclosure-rescue transactions (statutory contract, cancellation by 5 p.m. on the 3rd
    business day, 30-day cure up to three times, ability to pay verified, over 17% a year presumed
    unconscionable).
  - (6): a lease-option is presumed to be a mortgage.
  - (7): up to $15,000 per violation.
- Everything marked *believed* or *attorney to confirm* comes from general knowledge, not a source
  read today.
