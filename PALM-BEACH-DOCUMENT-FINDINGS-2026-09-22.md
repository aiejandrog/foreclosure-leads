# Palm Beach judgment acquisition — observed 2026-09-22

## Result

Guest access in the connected Chrome browser successfully returned a three-page final judgment
and a two-page later satisfaction. No paid service, manual CAPTCHA, registration or purchase was
needed in this session. This is one successful case, not a reliability claim across repeated sessions.

Pilot: `50-2026-CA-000685-XXXA-MB` (compact `502026CA000685XXXAMB`).
Search uses `2026CA000685`. Exact full UCN must match before opening the case.

## Actual sequence and transport

1. Open https://appsgp.mypalmbeachclerk.com/eCaseView/ and select Login as Guest User.
2. Fill Case Number and select Start Search. The matching case is a BUTTON, not an anchor.
3. Open the exact case, select Dockets & Documents, and select All in entries per page.
4. The site reported 47 docket entries; 47 unique DIN rows became visible. 43 offered View image.
   DIN 1, 31, 42 and 46 had no image control. No absence of attachments was inferred from that.
5. DIN 30 was the final judgment. Its image button's county-issued `formaction` was
   `/eCaseView/CaseData/Dockets?DocketId=230344314&Din=30&handler=ViewImage`.
6. Clicking View image produced a GET response, status 200, `application/pdf`, BEFORE the site's
   Download File / Cancel dialog. Download File saved `502026CA000685XXXAMB_30.pdf`.
7. DIN 40 followed the same path with DocketId 232445965 and yielded the satisfaction PDF.

For a collector, keep one guest browser session and observe the response to the real image button.
Do not manufacture docket IDs, export cookies, replay another case's URL, or assume a copied link
works without its original session. Treat challenge rejection, VOR, no image control, changed case
header and count mismatch as explicit incomplete states. A visible window alone is not proof that
reCAPTCHA will keep accepting scheduled runs.

`palmbeach_documents.py` implements the observed page/session interface for a caller-provided
headed Playwright page. It is not integrated into a scheduled runner. The local standalone
Playwright binary failed to launch (`spawn UNKNOWN`); browser-controlled steps succeeded through
the installed Chrome connection. The Python adapter has not yet passed a live end-to-end run.

## Evidence and interpretation

Private originals: `C:\Users\olqbb\DEALFLOW\palmbeach-pilot-20260922`.

| Document | Pages | SHA-256 |
|---|---:|---|
| judgment-din30.pdf | 3 | 746ae616cbc20b02f33a5dc3ccba2b85341b4cd45bda2b426a2902ce751d59aa |
| satisfaction-din40.pdf | 2 | f897bab03bff5f30eea10fd3f918390cb09ef61555164074a508a1c11e5c6e86 |

All five pages were visually inspected by Codex. Both PDFs have scanned bodies; text extraction
mostly returns the NOT A CERTIFIED COPY watermark. Body interpretation cannot use that text layer.

Judgment p1 prints total $993,885.33. Components are $908,094.39 principal, $24,782.49 interest
(the following 1 is a footnote), $26,340.14 insurance, $28,610.09 attorney fees, $2,349.19 court
costs and $3,709.03 fees; title expense, taxes and credits are zero. These sum to $993,885.33.
The printed subtotal $984,618.19 conflicts with the components and total; preserve that discrepancy.
P2 supplies legal description, parcel and sale terms. P3 supplies the signed May 4, 2026 order.

Satisfaction p1 expressly acknowledges full payment and satisfaction of the May 4 judgment and
states it was recorded May 7 in Official Records Book 36502, Page 322. P2 is signed and notarized
July 28. This is a filed acknowledgment, not a new court finding. The initial judgment must not be
presented as current unpaid debt without this later evidence. The substituted plaintiff differs
from the original judgment plaintiff; the docket includes the July 11 substitution order.

The handoff's statement that Palm Beach Official Records carry NO final judgments is too broad.
This docket and satisfaction explicitly identify a recorded judgment. Direct retrieval from the
recorder was not tested here, so recorder availability remains unproven.

## Terms: what is actually established

The live eCaseView homepage says the content is intended for personal and public non-commercial
educational use. It permits limited copies but requires prior written permission for broader
reproduction and storage in an information storage/retrieval system. Those clauses are visible at:
https://appsgp.mypalmbeachclerk.com/eCaseView/

The official user guide permits users to view public redacted PDFs and describes the image, lock,
clock, and shopping-cart controls, a 200-result search limit and docket pagination:
https://appsgp.mypalmbeachclerk.com/eCaseView/External/eCaseViewUserGuide

The MFA/reCAPTCHA guidance advises against third-party interface programs:
https://appsgp.mypalmbeachclerk.com/eCaseView/External/MfaRecaptcha

The linked full privacy/terms page returned 403 in web and direct HTTP requests, but succeeded
in connected Chrome and redirected to the canonical page below. It was read in full. It prohibits
circumventing authentication or accessing data without permission; it does not supply an explicit
bulk-collection license or resolve the homepage's narrower copying/storage language:
https://www.mypalmbeachclerk.com/about-us/privacy-policy-terms-of-use

These sources establish public viewing and limited copying, but do not establish permission for
DealFlow's recurring commercial acquisition and permanent evidence database. Confirm that use
with the Clerk or obtain an approved data channel before enabling bulk collection. This is a
reading of the published terms, not a legal opinion about their enforceability. No request was sent.

## Remaining work

- Run the Python adapter in a working headed runtime and prove resume/session expiration behavior.
- Integrate with PR #49's queue, evidence store and reader, preserving its current expense controls.
- Reconcile all subsequent relevant documents, not just the final judgment.
- Resolve recurring collection/storage authorization and complete laptop rollout.
- Keep the original three-county goal open; this bounded pilot does not complete it.
