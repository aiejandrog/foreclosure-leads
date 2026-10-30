# Palm Beach lis pendens dismissal check: options

Status: options only. Nothing is built, nothing was called, nothing was bought.
Researched 2026-10-29. Several clerk pages returned HTTP 403 to the fetch tool, so some figures
below come from search-result snippets and are marked UNVERIFIED. Re-check each before relying on it.

## The problem

`lp_status.py` can ask the Broward clerk API whether a Broward lis pendens case was dismissed
(`DEALFLOW_LP_BROWARD=1`, off by default). Palm Beach has no adapter, so a Palm Beach LP whose
foreclosure was voluntarily dismissed stays in the early lane as "not checked". The same gap exists
in the bankruptcy path: CLAUDE.md records that Palm Beach eCaseView is captcha-gated and its terms
limit it to non-commercial use, and `clerk_bk.py` therefore never calls it.

## What the Palm Beach Clerk's own terms say

- eCaseView registration terms: users agree "not to use or permit others to use the information
  obtained from this site for commercial or resale purposes", and the Clerk says all activity "will
  be tracked and monitored ... to enforce this provision". The site also describes the information as
  for "personal and public non-commercial (educational) use" and bars storing it in a retrieval system
  without written permission.
  Sources: [eCaseView FAQ](https://mypalmbeachclerk.com/about-us/frequently-asked-questions-faq/civil-criminal-and-traffic-records-search-faq/ecaseview-faq),
  [registration page](https://www.mypalmbeachclerk.com/records/court-records/court-records-search/ecaseview-user-registration),
  [eCaseView](https://appsgp.mypalmbeachclerk.com/eCaseView/) (search snippets, 2026-10-29; the pages
  themselves returned 403 to the fetch tool, so the wording is UNVERIFIED against the live page).
- No API, bulk-download or scraping policy for court cases was found on the Palm Beach Clerk's site.
  Contact listed for eCaseView: eCaseView-Support@mypalmbeachclerk.com (from the FAQ search result).

Conclusion: scraping or automating eCaseView is out. That matches CLAUDE.md and is not a close call.

## Options

### A. Do nothing; show "not checked" (cost $0, legal risk none)
Palm Beach LP rows stay in the early lane flagged unchecked. Call Mode asks about the case on the call.
Cost is wasted calls on dismissed cases. Cheapest and already the behaviour.

### B. Ask the Clerk in writing for commercial/bulk court-case access (cost unknown, risk low)
No published Palm Beach commercial court-data program was found, unlike its neighbours:
- Miami-Dade runs one: bulk case-data folders at $80.00 per folder per month, paid in advance, with a
  notarized registration form (per search result for
  [Miami-Dade Commercial Data Services](https://www.miamidade.gov/clerk/commercial-data-services.page),
  2026-10-29; UNVERIFIED, page not opened).
- Broward runs one: the Commercial Data API already used by `clerk_bk.py` / `lp_status.py`
  ([Broward API](https://www.browardclerk.org/web2/services/aboutapi)).
Ask Palm Beach whether it has an equivalent or will license one, and ask for the price and the
permitted-use language in writing. This is the only route that yields a docket status the Clerk
itself has blessed for commercial use. Output of the ask: a yes with a price, or a no we can cite.
Needs Alejandro (it is his entity's request and a legal-use question).

### C. Official Records FTP index (cost UNVERIFIED, different data)
The Clerk sells a subscription Electronic Distribution Index Service over FTP: one file a day of newly
verified official-records index data; subscribers sign an agreement and an FTP creation form and mail
a check; the Clerk "charges a special usage fee" whose amount was not found
([service page](https://www.mypalmbeachclerk.com/records/official-records/electronic-distribution-index-service),
[application](https://www.mypalmbeachclerk.com/records/official-records/electronic-distribution-index-service/ftp-subscription-application)).
A separate CD-ROM index is listed at $40.00 per year (search snippet, UNVERIFIED).
What it could give: recorded instruments, so a recorded notice, release or discharge of lis pendens
if one is recorded. What it cannot give: the foreclosure docket. A notice of voluntary dismissal is
filed in the court case, not recorded in the official records, so this will miss most dismissals.
Useful as a supplement, not as the dismissal check.

### D. Statewide CaseAccess subscription (cost low, commercial terms unknown)
The Florida Courts E-Filing Authority and the Florida Court Clerks & Comptrollers launched CaseAccess:
search of case information in all 67 counties, requires an E-Filing Portal account, "$20 per month or
$200 per year", and "is a research tool and is not the official court record"
([Florida Bar News, 2026-10-12](https://www.floridabar.org/the-florida-bar-news/new-service-offers-statewide-case-search-through-e-filing-portal/)).
The article does not say whether dockets or dispositions are included, whether commercial use is
allowed, or whether there is any automated or API access. It is a search screen as described, so
using it is a manual lookup per case, not a pipeline step. Open question for the E-Filing Authority:
are the terms of use compatible with a business checking leads, and is a disposition field shown.

### E. Commercial court-data vendor (cost high, coverage unconfirmed)
- UniCourt: a third-party listing puts the Enterprise API at about $2,250 per month (UNVERIFIED,
  third-party directory); UniCourt's own pricing page says billable calls apply to Search, Case,
  Download Document, Order Document Confirm and Update Cases, says it covers "all federal courts and
  many state courts", mentions neither Florida nor Palm Beach, and has no free trial
  ([UniCourt pricing](https://unicourt.com/pricing), fetched 2026-10-29).
- Trellis: publishes Palm Beach civil case pages and says it offers bulk access
  ([Trellis Palm Beach coverage](https://trellis.law/coverage/florida/palmbeach)); no published API price
  ([Trellis API](https://support.trellis.law/trellis-api)); sales contact needed.
- PacerPro: no pricing found.
For about 900 Palm Beach LP rows this is the only no-contact-with-the-Clerk route, but the per-month
price is far above the value of the check unless the vendor's Palm Beach docket data is current, and a
vendor's data is only as fresh as its scrape of the same Clerk system. Ask for a sample on 20 known
case numbers before paying anything.

### F. Federal bankruptcy side, for context only
The bankruptcy half of "is this lead alive" is handled by `bk_lookup.py` (CourtListener) and the
by-hand PACER tool, not by this question. A Palm Beach dismissal check does not replace either.

## Recommendation

1. Now: option A (no change) plus option B (a short written request to the Palm Beach Clerk for a
   commercial data program, price and permitted use). It costs nothing and the answer decides the rest.
2. In parallel, one question to the E-Filing Authority about option D's commercial-use terms. If
   allowed and a disposition shows, a person can spot-check the top of the call list at $20/month.
3. Option E only after B and D both fail, and only after a 20-case sample.
4. Do not build an adapter until a yes arrives in writing. When it does, copy the shape of the Broward
   pass in `lp_status.py`: one request per case, bounded by case count and runtime, a TTL, and a
   disposition vocabulary validated against live responses before leaving the flag on (CLAUDE.md
   already warns the Broward vocabulary was not).

## Unverified items (check before use)
- Live wording of the eCaseView commercial-use clause (pages gave 403).
- The Palm Beach FTP service fee and the $40 CD-ROM price.
- Miami-Dade $80/folder/month (comparison only).
- UniCourt $2,250/month (third-party listing).
- Whether CaseAccess returns dispositions and what its terms say about commercial or automated use.
