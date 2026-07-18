<!-- [Cursor cloud edit] deep-research report authored by the Cursor cloud agent -->
# Deep research: automatic bulk SMS to all DEALFLOW leads

**Prepared for:** DEALFLOW (South Florida foreclosure / tax-deed lead engine)
**Question:** "Automatically send bulk, prefilled text messages to all the phone numbers on the deals."
**Date:** 2026-07-18
**Status:** research + recommendation. **Not legal advice** — verify with a TCPA/FTSA attorney before sending anything at volume.

---

## 0. Bottom line up front

Automatically blasting texts to every scraped owner number is the single **highest-liability** thing DEALFLOW could do, and it collides with three independent walls — any one of which is enough to stop it:

1. **Carrier content ban (10DLC/A2P).** US carriers (AT&T, T-Mobile, Verizon) **prohibit** "debt relief / debt reduction / debt consolidation," "third-party lead generation," and "third-party mortgage" content on A2P SMS. Foreclosure-rescue outreach to distressed homeowners reads squarely as high-risk financial / debt-relief content. Campaigns like this get **rejected at registration and are not resubmittable** (e.g. Twilio error 30949), and unregistered traffic is filtered to <30% delivery within ~48h. So the automated channel is likely **closed to this content before any law is even reached.**
2. **Consent law (federal TCPA + Florida FTSA).** These are cold numbers with **no prior express written consent** and **no business relationship** — exactly what both statutes target. Statutory damages are **$500–$1,500 per text**, *per recipient*, and Florida has an aggressive plaintiff's bar (~330 TCPA cases in 2024).
3. **Do-Not-Call (DNC).** Since March 2024 the National DNC Registry's protections **explicitly extend to marketing texts**; you must scrub every ~31 days, and many of these owners are registered.

**What actually works** (in order): (A) the **assisted, one-at-a-time `sms:` sender already shipped** in the tracker — human-sent, DNC-gated, STOP line included; (B) a purpose-built **peer-to-peer (P2P) REI texting platform** (Launch Control / Smarter Contact / Lead Sherpa) that human-initiates each send, does litigator + DNC scrubbing and 10DLC — still with real consent/content risk; (C) lean harder on the **non-SMS channels DEALFLOW already generates** (direct mail letters, door hangers, email, manual calls), which carry far less texting-specific liability. A fully-automated API blast (option D) is **not recommended and largely not deliverable** for this content.

---

## 1. What "automatic bulk SMS" actually requires

DEALFLOW today is a **static site** (GitHub Pages). It has no server, so it cannot itself send SMS. The shipped "Bulk text" feature is deliberately an **assisted** sender: it opens the phone's Messages app via an `sms:` deep link, one owner at a time, human taps send.

"Automatic" bulk sending would require net-new infrastructure:

- A **backend service** (the current repo has none) to call an SMS API.
- A commercial **SMS/A2P provider** (Twilio, Bandwidth, Telnyx, Sinch, AWS End User Messaging, etc.).
- **10DLC brand + campaign registration** through The Campaign Registry (TCR) — mandatory; unregistered A2P is blocked.
- **Consent records**, **DNC scrubbing**, **opt-out (STOP/HELP) handling**, **quiet-hours** enforcement, **frequency caps**, and an **audit trail**.

Each of those is a compliance gate, not just an engineering task. The engineering is the easy 20%.

---

## 2. Wall #1 — Carrier / 10DLC content rules (the practical blocker)

All A2P SMS over US carriers must be registered via **10DLC** (brand + campaign in TCR). Carriers vet the *content*, and the following are **prohibited regardless of consent**:

- **High-risk financial services** — payday/short-term loans, **third-party mortgage loans**, student loans, crypto/investing.
- **Debt collection / debt relief** — **third-party debt collection, debt consolidation, debt reduction, credit repair**. "Almost all debt consolidation and forgiveness efforts are prohibited for SMS/MMS."
- **Third-party lead generation.**

A foreclosure investor texting "I can help before your sale / buy your house / you may have surplus" to homeowners in default is, from a carrier reviewer's seat, indistinguishable from **debt-relief / high-risk-financial / third-party** messaging. Consequences:

- **Campaign rejected at registration** and **not eligible for resubmission** (Twilio documents this as error **30949** — "Debt reduction or consolidation content detected").
- If sent anyway (e.g. unregistered), **T-Mobile issues Sev-0 non-compliance fines** and carriers **block** the traffic; new unregistered senders see **delivery collapse below ~30% within 48h** and burned numbers.

**Implication:** even setting the law aside, mainstream automated SMS is effectively **not available** for this exact content. This is the first and often final wall.

---

## 3. Wall #2 — Consent law

### 3.1 Federal TCPA (47 U.S.C. § 227)

- **Marketing texts** to wireless numbers sent with an **autodialer (ATDS)** or prerecorded voice require **prior express written consent (PEWC)**.
- **ATDS after *Facebook v. Duguid* (2021):** an ATDS is equipment using a **random or sequential number generator**. Sending to a **curated, uploaded list** (like DEALFLOW's scraped numbers) may fall **outside** the §227(b) ATDS definition — this is the theory the P2P vendors rely on. But that does **not** make cold texting safe, because:
  - **§227(c) / DNC** applies to *solicitations* regardless of autodialer (see Wall #3), and
  - **state mini-TCPAs** (FTSA) have their own tests.
- **"One-to-one consent" rule: vacated.** The FCC's 2023 rule requiring per-seller consent was struck down by the 11th Circuit in *Insurance Marketing Coalition v. FCC* (Jan 24, 2025); the FCC finalized a rule removing it (2025). So lead-gen one-to-one consent is **not** currently required — but **base PEWC for marketing still is**.
- **Damages:** **$500 per text**, trebled to **$1,500** for willful/knowing violations; **private right of action** + class actions. FCC forfeitures can reach **$10,000+/text**.

### 3.2 Florida FTSA (Fla. Stat. § 501.059) — this is the one that matters most here

DEALFLOW's leads are Florida homeowners, so the **FTSA** (Florida's "mini-TCPA") governs, and it is plaintiff-friendly:

- Prohibits an **unsolicited "telephonic sales call"** (explicitly **including text messages**) made with **"an automated system for the selection and dialing of telephone numbers"** without **prior express written consent**.
  - The **2023 amendment (HB 761)** changed "selection **or** dialing" → "selection **and** dialing," narrowing the autodialer definition toward the federal ATDS test, and limited liability to **unsolicited** calls (a prior/existing business relationship or an express request is exempt).
- **PEWC** must be a **signed written agreement**, name the **specific number**, and include a clear disclosure (a checkbox / affirmative reply to a campaign can satisfy the signature).
- **Text safe harbor:** the recipient must reply **STOP**; the sender then has **15 days** to stop; a lawsuit is only allowed if texts continue **after** that 15-day window. (So honoring STOP within 15 days is a genuine legal shield — DEALFLOW already logs opt-outs.)
- **Quiet hours:** **8:00 AM – 8:00 PM** local (one hour tighter than the federal 9 PM).
- **Frequency cap:** **no more than 3 solicitations per 24 hours** per recipient.
- **Damages:** **$500 per call/text**, up to **$1,500** willful. Private right of action + class actions; Florida saw **~330 TCPA cases in 2024**.

### 3.3 FTC Telemarketing Sales Rule + Fla. Stat. § 501.1377

- The **TSR** adds calling-time and DNC duties; civil penalties can exceed **$50,000 per violation**.
- **Fla. Stat. § 501.1377** ("foreclosure-rescue" / equity-purchaser protections) already governs how you *transact* with owners in foreclosure — DEALFLOW references it. It doesn't ban texting, but it colors the whole activity as consumer-protection-sensitive.

---

## 4. Wall #3 — Do-Not-Call

- Effective **March 26, 2024**, the FCC codified that the **National DNC Registry protections extend to marketing texts** (47 CFR § 64.1200(e)): no marketing text to a wireless number on the registry without prior express invitation/permission.
- You must **scrub the list against the National DNC Registry before every campaign** (industry norm: **every 31 days**), maintain an **internal DNC list**, and consult the **Reassigned Numbers Database** for older consents.
- **Circuit split (live in 2026):** the **7th Circuit** (*Steidinger v. Blackstone*, Jul 14, 2026) held texts are **not** "calls" under the DNC private right of action §227(c)(5); a **Louisiana** federal court (*McGonigle*, Feb 13, 2026) held the **opposite**. No Supreme Court resolution. **FCC regulatory enforcement is unaffected either way**, and **FTSA is independent** — so DNC risk for FL texts remains real.

DEALFLOW already tracks a `phdnc` (do-not-call) flag per number and a `DO NOT CONTACT` opt-out ledger — but that reflects the *skip-trace vendor's* DNC signal, **not** a fresh National DNC Registry scrub, which a real sending program must add.

---

## 5. Penalty math for *this exact* use case

Worst-case profile: cold, scraped numbers · no PEWC · distressed FL homeowners · debt-relief-adjacent content · automated send.

- 500 leads × ~1 text = 500 potential violations.
- At **$500** each = **$250,000**; at **$1,500** willful = **$750,000** — before counting DNC ($50k+/violation TSR), carrier fines, and class-action aggregation.
- A single misfired blast can exceed a year of profit. This is why even the REI-texting vendors call unstructured "text blasting" a business-ending mistake.

---

## 6. Options, ranked

### Option A — Keep the assisted `sms:` sender (already shipped) ✅ recommended baseline
The tracker's **Bulk text** / per-lead **Text** feature opens Messages with a prefilled EN/ES message, **one owner at a time**, **skips DNC numbers and opt-outs**, and includes a **STOP** line.
- **Pros:** no backend, no 10DLC, no carrier content review (it's *your* phone/iMessage), human-in-the-loop = not an ATDS blast, and it's the fastest safe way to "get a hold of them." Honors the FTSA STOP safe harbor DEALFLOW already logs.
- **Cons:** manual pace; still subject to FTSA/DNC on the *content* and *who* you text — so pair with a National DNC scrub and quiet-hours discipline.
- **Enhancement ideas:** add a National-DNC scrub gate, enforce **FL 8am–8pm** + **≤3/24h** in the UI, and a required consent/opt-out ledger check before showing a number.

### Option B — Peer-to-peer (P2P) REI texting platform (realistic "bulk-ish")
Tools built exactly for this: **Launch Control, Smarter Contact, Lead Sherpa, Batch Leads, REI Reply**.
- **How they thread the needle:** each message is **human-initiated** ("manual"/P2P) to avoid ATDS classification; they handle **10DLC** brand/campaign registration, **DNC + "litigator" scrubbing**, **opt-out automation**, number rotation, and audit logs.
- **Reality check:** they reduce risk, they **do not eliminate it** — *"the sender always owns the legal liability."* And they, too, are subject to carrier content review; foreclosure/debt-relief phrasing can still get campaigns rejected or filtered. Even these vendors discourage "text blasting."
- **Fit:** export DEALFLOW leads (respecting `phdnc`/opt-outs) → import into the platform → work them there. This is the path most serious FL investors actually use.

### Option C — Non-SMS channels DEALFLOW already generates (lower liability)
- **Direct mail** (the existing **Letter** + **Door hanger** generators): **not** governed by TCPA/DNC texting rules — the highest-volume-safe channel for reaching distressed owners. Best ROI/risk ratio for cold outreach.
- **Email** (existing **Email** generator): governed by **CAN-SPAM** (much lighter — clear identification + working unsubscribe + physical address).
- **Manual calls** (existing tap-to-call, DNC-gated): person-to-person, still honor DNC + FL 8am–8pm.

### Option D — Fully automated API blast (Twilio/etc.) ❌ not recommended
- **Blocked by Wall #1** (content rejected at 10DLC registration) and **maximizes** TCPA/FTSA/DNC exposure. Even if a campaign slipped through, automated sending to non-consented numbers is the textbook violation. Do not build this for cold foreclosure numbers.

---

## 7. If you still want to build automated SMS (what it takes)

Only viable for **consented** contacts (e.g. owners who replied/opted in), never a cold blast. Architecture, mapped to this repo:

1. **Backend** (new): a small service (Python fits the repo) — e.g. FastAPI/Flask — since a static site can't send. Reads `leads_final.json` + `skiptrace_results.json`.
2. **Provider + numbers:** Twilio/Bandwidth/Telnyx; local 10DLC numbers (rotate 3–5 per campaign) or toll-free (separate verification).
3. **10DLC registration:** brand (~$4/mo + ~$44 one-time standard vetting) + campaign ($10–17/mo + ~$50 T-Mobile activation) via TCR. Expect **3–7+ business days** and **content vetting** (which this use case likely fails).
4. **Throughput:** gated by Brand Trust Score — low/unverified <4 MPS; standard ~25–225 MPS. Fine for hundreds of leads; irrelevant if the campaign is rejected.
5. **Per-message cost:** provider (~$0.0079/segment Twilio) + carrier surcharge (~$0.003–0.005/segment).
6. **Mandatory compliance middleware (the real work):**
   - **Consent ledger** — store PEWC per number (source, timestamp, exact opt-in language, IP for web forms). No consent → do not send.
   - **National DNC scrub** (≤31-day cycle) + **internal DNC** + **litigator list** + **Reassigned Numbers Database**.
   - **Opt-out webhook** — handle inbound STOP/UNSUBSCRIBE/HELP; suppress within the FTSA 15-day window (immediate is safer); send one confirmation only. Wire into DEALFLOW's existing opt-out ledger.
   - **Quiet-hours + frequency** — enforce recipient-local **8am–8pm (FL)** and **≤3 texts/24h**; safe cross-zone window ~11am–8pm ET.
   - **Per-seller identification** in every message + "msg & data rates may apply" on opt-in confirmation.
   - **Immutable audit log** — "show me the consent record for this number" is the first discovery request in any suit.
7. **Repo touchpoints that already help:** `phdnc` flags, the `DO NOT CONTACT`/opt-out ledger, the cadence/`logTouch` engine, and the bilingual message templates from the shipped Bulk-text feature can all be reused.

---

## 8. Recommendation

1. **Use Option A now** — the assisted Bulk-text tool already in the tracker is the fastest *and* safest way to reach owners; harden it with a National-DNC scrub gate and FL quiet-hours/frequency guards.
2. **For scale, use Option B** (a P2P REI platform) rather than building an API blaster — they've solved the 10DLC/scrub/opt-out plumbing, and they still leave liability with you, so keep messages non-debt-relief in tone.
3. **Lean on Option C** (mail/door/email) as the compliance-safe backbone of cold outreach — DEALFLOW already generates all three.
4. **Do not build Option D** (automated cold API blast) for scraped foreclosure numbers.
5. **Before any volume texting, get a Florida TCPA/FTSA attorney to review** your consent flow, message copy, and suppression process.

---

## 9. Sources (accessed 2026-07-18)

- Fla. Stat. § 501.059 (FTSA), Online Sunshine / FL House 2025 statutes.
- Burr & Forman; Greenspoon Marder; Pillsbury — FTSA 2023 amendment (HB 761) analyses.
- *Insurance Marketing Coalition Ltd. v. FCC*, No. 24-10277 (11th Cir. Jan 24, 2025) — one-to-one consent vacated; Day Pitney, Kelley Drye, Consumer Financial Services Law Monitor summaries + FCC 2025 final rule.
- FCC Second Report & Order / 47 CFR § 64.1200(e),(f)(9) — DNC extended to texts (eff. Mar 26, 2024); Federal Register 89 FR (Jan 26, 2024); DA-24-910A1.
- *Steidinger v. Blackstone Medical Services* (7th Cir. Jul 14, 2026); *McGonigle v. Shopperschoice.com* (M.D. La. Feb 13, 2026) — texts-as-"calls" circuit split.
- Twilio A2P error **30949** (debt content rejected); AWS "Direct Lenders" prohibited activities; 10DLC.org / Ring.io / HighLevel prohibited-content lists (debt relief, third-party lead gen, high-risk financial).
- Twilio 10DLC brand/campaign docs + Telphi/Tuco cost & throughput breakdowns (2026).
- TCPA Guide / Mac Murray & Shuster / Infobip / leadgen-economy — quiet hours (fed 8am–9pm; FL 8am–8pm) + FL 3-per-24h cap.
- Launch Control, Smarter Contact, Lead Sherpa — P2P REI texting compliance posture (human-initiated, litigator/DNC scrub, "sender owns liability").
- NAR "Telemarketing & Cold-Calling"; Draco Automation & Televista — REI cold-texting risk guides (2026).

*This document is research, not legal advice. Statutes, FCC rules, carrier policies, and case law change; confirm current requirements with qualified counsel before sending.*
