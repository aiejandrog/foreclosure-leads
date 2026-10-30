# Decisions needed from Alex

Standing rule (Alejandro, 2026-10-29): nothing spends, sends, or changes a rule without Alex.
Merges (amended twice the same day; latest: "every pr draft goes thru qa and have qa make the decision"):
every PR and agent branch goes through QA, and QA decides MERGE or REJECT, LEGAL REVIEW PRs included.
The LEAD executes QA's MERGE by a normal push from a separate worktree (never the main checkout, never
force). Still Alex's: spend and rule changes that are not a PR (entries below). A task that touches
stay_gate.py, never_contact.json, DNC, opt-out, send_server.py send paths, text or email copy, or a
spend cap stops, writes its question here, and moves to its fallback. The Lead reads this file to Alex
at the end of every round.

Append only. Never edit or delete another entry. Lead commits this file and marks entries DECIDED.

## Entry format
### D<n>: <one-line question> (<owner>, <date>, <task>)
- Touches: <file:function and which trigger above>
- Option A: <exact change> | Risk: <what can go wrong, who gets contacted or what is spent>
- Option B: <exact alternative, incl. "do nothing"> | Risk: <...>
- Recommendation: <A/B and one-line why>
- Fallback in progress: <what the task does meanwhile>
- Status: OPEN | DECIDED <date> <choice>

## Decided
### D0: Keep automated follow-up emails going when #290/#293/#294 merge? (Lead, 2026-10-29)
- Status: DECIDED 2026-10-29: keep going. Set DEALFLOW_FOLLOWUPS_ON=1 in the same step as the #294 merge.
  Details: fl-lead docs/agent-team/CADENCE-STATE.md.

## Open
### D1: May the /notes merge let a newer after-the-yes save replace an older one? (Lead, 2026-10-29, callmode C-c)
- Touches: send_server.py `_merge_note` (the /notes merge; not a send path, but it decides status precedence next to DO NOT CONTACT, so treated as the DNC trigger)
- Option A: newer `afterYesAt` wins for nextStep / stage / equityVerified / keepOrSell; DO NOT CONTACT and Dead are never overridden; covered by a merge test | Risk: a bug here could walk a status back; the test pins DNC/Dead precedence
- Option B: do nothing | Risk: the first next-step date and stage saved for a lead stick forever through the bridge; later updates from Call Mode are silently dropped, so the board's after-yes counts and Overdue list go stale
- Recommendation: A. Without it the after-the-yes flow does not persist.
- Fallback in progress: callmode keeps the `_merge_note` change on its own branch claude/agents-mergenote (LEGAL REVIEW, not merged by Lead); the rest of Call Mode merges after QA PASS.
- Status: MOVED TO QA 2026-10-29 (Alex: QA decides every PR). Verdict recorded in tests/AGENT-QA-REPORT.md on the merge train.

### D2: Spend ~$0.02 of 2Captcha on one live Miami folio probe? (equity, 2026-10-29, E-b)
- Touches: spend (2Captcha), records_liens folio search behind DEALFLOW_OR_FOLIO=1
- Option A: run one probe on 1-2 leads to confirm the clerk's folio search label ("Parcel ID") and folio format | Risk: ~$0.02; it hits the Official Records site, which needs a working login (the subscription lapsed 10/26, see D4)
- Option B: do not probe; leave DEALFLOW_OR_FOLIO off | Risk: the folio search stays unproven against the live site; Miami common-name lien mismatches continue
- Recommendation: A, after D4. Full first pass afterwards is estimated at ~$1.22 for ~370 Miami leads (per-solve price from code; lead count estimated).
- Fallback in progress: flag stays off; code merges after QA PASS (off = byte-identical).
- Status: OPEN

### D3: Lift the refusal of scan-read Miami judgment figures? (equity, 2026-10-29, E-c)
- Touches: a rule (judgment_for_analyze refuses scan-read figures until a 12-case review)
- Option A: do the 12-case review, then let verified court-copy figures feed analyze() with DEALFLOW_COURT_JUDGMENT=1 | Risk: a misread scan becomes a quoted judgment; eqc.may_quote could turn true on a wrong number
- Option B: keep the refusal | Risk: DEALFLOW_COURT_JUDGMENT=1 changes nothing for Miami (every judgment is a scan), so Miami equity stays docket-only
- Recommendation: A, but only after the 12 cases are checked by a person against the PDFs.
- Fallback in progress: refusal unchanged; wiring merges after QA PASS behind the off flag.
- Status: OPEN

### D4: Renew the Miami-Dade Official Records subscription? (equity, 2026-10-29, E-d)
- Touches: spend (subscription; price not verified from code)
- Option A: renew | Risk: recurring cost; enables (b)/(c) live runs and nightly lien refresh
- Option B: do not renew | Risk: Miami lien chains stay frozen at the last pull; folio search can't run
- Recommendation: Alex to check the renewal price first; see docs/agent-team/EQUITY-LIVE-RUN.md on claude/agents-equity.
- Fallback in progress: none needed; nothing runs live.
- Status: OPEN

### D5: The paid-reads monthly cap is effectively off (Lead, 2026-10-29, found while checking the refresh)
- Touches: spend cap. paid_reads.json beside the code (gitignored, laptop) says {"monthly_cap": 100000}; the code default is $50. The October ledger total is $80.57: pacer_stay $59.00, records_liens $8.67, lis_pendens $6.01, gen_records_qs $2.92, broward_plaintiff $2.76, palmbeach_liens $1.21.
- Option A: set monthly_cap back to a real number (e.g. 100) | Risk: paid steps stop once it's reached, so the nightly leaves those enrichments unfilled
- Option B: leave it | Risk: no ceiling on 2Captcha/PACER spend if a step loops
- Recommendation: A. Also check why pacer_stay spent $59 in October when CLAUDE.md (2026-10-16) says PACER is by-hand only.
- Fallback in progress: nothing changed.
- Status: OPEN

### D6: Stop the nightly paying 2Captcha to mint Miami Records links that come back "blocked"? (Lead, 2026-10-29)
- Touches: spend. refresh step [2/5] gen_records_qs.py solves Turnstile ($) per owner, but the Official Records login lapsed 10/26 (D4), so every owner tonight printed "no records / blocked"
- Option A: skip gen_records_qs until D4 is renewed | Risk: no new Records deep links; the board falls back to open-the-search + copy-the-name
- Option B: leave it | Risk: paying for solves that cannot work, every night
- Recommendation: A (a one-line guard or env flag; that is a .bat change, so it goes through QA as a PR).
- Status: OPEN

### D7: Rename the booking page slug so the first-touch email stops scoring a "free" hit? (deliverability, 2026-10-29, task 4)
- Touches: outreach_copy.BOOKING_URL (cal.com/bsgflorida/free-records-review) and the cal.com page itself. Email copy trigger.
- Option A: rename the cal.com slug to something without "free" (e.g. records-review), keep the old slug redirecting if cal.com allows, change BOOKING_URL in outreach_copy.py | Risk: old links in already-sent mail must still resolve; copy change goes through QA and the attorney
- Option B: leave it | Risk: +1.0 on the content score (1.5 total, still "ok"); a filter may weigh the word "free" in a URL
- Recommendation: A, low value alone; do it together with D8.
- Fallback in progress: none needed; score is advisory.
- Status: OPEN

### D8: Adopt the first-touch rewrite in docs/EMAIL-DELIVERABILITY.md section 6? (deliverability, 2026-10-29, task 4)
- Touches: outreach_copy.email_body (and the ES twin), homeowner-facing copy, so the attorney's call (SWAP 1 and 2 were approved wording). Also adds the ES opt-out line to every English email.
- Option A: use the rewrite: no auction date or countdown, no "costs you nothing", no "foreclosure" outside the mandated sentences; identity sentence and EN/ES opt-out lines verbatim from disclaimer.py / outreach_copy | Risk: loses the dated personalization that may drive replies; new sentence "a public court record shows an open case on it" needs sign-off
- Option B: keep the current copy | Risk: scores 1.5 (ok) by the lint, but the date countdown and "costs you nothing" are things the lint cannot see
- Recommendation: B until placement is measured with external seeds; then A/B the two with placement --send before changing live copy.
- Fallback in progress: doc only, no code change.
- Status: OPEN

### D9: Gate each first-touch ramp step on observed bounces instead of the calendar? (deliverability, 2026-10-29, task 7)
- Touches: senders.json first_touch.ramp (a spend-free rule/cap change); send_server first-touch cap logic if automated.
- Option A: the evidence-gated tiers in docs/EMAIL-DELIVERABILITY.md section 7 (20 -> 30 -> 40 -> 60 -> 80 per day, each step needs a bounce count under a 2% Wilson upper bound) | Risk: slower than the calendar if bounces occur; needs the post-gate bounce count read from the ledger first
- Option B: keep the calendar ramp | Risk: volume climbs whether or not bounces stay low; the 5% pause is 2.5x the 2% target
- Recommendation: A, applied by hand at each checkpoint (edit senders.json) before anyone automates it.
- Fallback in progress: none; no config changed.
- Status: OPEN

### D10: Which call script wins: main's, or PR #229's keep-or-sell branches? (QA, 2026-10-29, train)
- Touches: call_mode.py script flow (q1/q2, 'selling', bankruptcy routes), tracker_template.html text bodies. Text and call copy trigger.
- Option A: callmode re-cuts #229's PERMISSION / KEEP / SELL steps on current main as a call_mode.py-only PR, keeping main's 'selling' branch and bankruptcy routes ("already filed" -> end:dnc, "planning" -> obj:6) | Risk: #229 replaces those routes with a 'bkref' referral to a bankruptcy attorney; which bankruptcy policy applies is a legal call, so the attorney must see it
- Option B: drop #229 | Risk: Chad's posture (permission first, keep-or-sell then silence) never reaches the call flow
- Recommendation: A, without the text-body edits (main's 10-22/10-26 short texts already superseded them).
- Fallback in progress: #229 REJECTED in the train (5 conflict hunks, design not merge resolution). Nothing lost; branch untouched.
- Status: OPEN

### D11: Redo the notice letter (#112) as a layout over bsg_mail_campaign.letter()? (QA, 2026-10-29, train)
- Touches: homeowner letter copy, disclaimer.py, outreach_mail --variant notice. Copy trigger.
- Option A: rebuild the duplex EN/ES layout on top of letter() (MARS sentences from disclaimer.py, approved opt-out line from outreach_copy), with a hard refusal while IS_PLACEHOLDER is true | Risk: needs attorney sign-off on the layout and wording before any mailing
- Option B: drop #112 | Risk: no separate notice letter variant
- Recommendation: A only if the notice variant is still wanted; as written #112 is mailable with placeholder text, uses a "TIME-SENSITIVE" headline and a "Call or text before {SALE}" deadline, hand-types disclosures instead of disclaimer.py, and predates the 2026-10-15 MARS work.
- Fallback in progress: #112 REJECTED in the train.
- Status: OPEN

### D12: Where does DEALFLOW_FOLLOWUPS_ON=1 live so the send bridge sees it? (QA, 2026-10-29, train, PR #294)
- Touches: send_server.py /send follow-up switch; cadence-daily.bat.
- Option A: set DEALFLOW_FOLLOWUPS_ON=1 as a Windows user environment variable on the laptop (covers cadence, outreach_email and the bridge) and restart the bridge | Risk: none new; it is the D0 decision applied everywhere. Remove it to pause every follow-up.
- Option B: rely on the line the train adds to cadence-daily.bat | Risk: only the 10:00 cadence run sends follow-ups; Morning Worker / Call Mode follow-ups through /send come back {"blocked":"followups_off","dry_run":true} until the bridge process has the variable.
- Recommendation: A. D0 said "keep follow-ups going" for every path.
- Fallback in progress: train sets it in cadence-daily.bat only.
- Status: OPEN

### D13: Confirm the sender-lane move in PR #258 still stands (QA, 2026-10-29, train)
- Touches: senders.json lanes (active, early, balloon -> alejandro@bsgflorida.com), warmup.off. Sender reputation, not a gate.
- Option A: keep it | Risk: cold follow-ups and the balloon lane now send from the brand domain that took the 2026-09-19 bounces (first touches are unaffected: first_touch.from still rotates the warm-up aliases); main_domain_cap 40 still limits it
- Option B: revert senders.json to the warm-up aliases and keep only warmup.off | Risk: the aliases carry volume while their warm-up is paused
- Recommendation: Alex's call; the PR text records "option 1" on 2026-10-16/17, so the train merged it. Say so if that no longer holds.
- Fallback in progress: merged in the train as written.
- Status: OPEN

### D14: Who fixes the board's merge of the after-the-yes unit? (callfix, 2026-10-30)
- Touches: tracker_template.html `_mergeLead` (board sync, also extracted into Call Mode); no send, no spend
- Finding: a remote note for a lead the board already has contributes only status and next. nextStep, stageAt, equityVerified, keepOrSell, afterYesAt, apptUnverified, replied, textopen are dropped (node-verified on fixtures).
- Option A: board owner takes the unit from the side with the larger afterYesAt, never on DO NOT CONTACT / Dead, plus a test | Risk: touches the shared sync block both pages run; needs QA on both
- Option B: leave it; the disk copy gets the unit via Call Mode's new /notes push | Risk: the board and other phones never show the next step
- Recommendation: A.
- Fallback in progress: none; reported only.
- Status: OPEN

### D15: _merge_note fill-only gaps (callfix, 2026-10-30) -- suppression surface, report only
- Touches: send_server._merge_note (no / DNC fields)
- Finding: stored no:'soft' stays over incoming no:'hard'; a second wpe address, a newer wq and replied are not taken. DO NOT CONTACT status still wins and /notes ledgers it, so no stop is lost today.
- Option A: Alex/attorney-owned fix: hard beats soft, union wpe | Risk: edits the suppression surface and send_server.py (LEGAL REVIEW)
- Option B: leave | Risk: disk copy can show a soft no for a person who said hard no, until status DNC is merged
- Recommendation: A, by the suppression-surface owner.
- Fallback in progress: none.
- Status: OPEN

### D16: Call Mode now posts /notes and /text to the bridge (callfix, 2026-10-30) -- LEGAL REVIEW in PR title
- Touches: suppression (POST /notes runs optout_sync.ledger_from_notes, so a DNC tapped in Call Mode reaches optouts.json sooner, add-only) and the text ledger (POST /text writes text_sent.json). No send path, no copy, no gate changed; send_server.py untouched.
- Option A: ship both posts (recommended: the board already does the same; Call Mode alone was the gap) | Risk: more writers of the ledger sweep input; a bad note in a phone's localStorage would be ledgered like the board's
- Option B: ship only the device key and drop the /notes and /text posts | Risk: Call Mode work stays off disk unless a board tab is open on the bridge PC
- Recommendation: A.
- Fallback in progress: none needed; if Alex says B, remove callNotesPushSoon() from saveNotes() and the two callTextLedger() calls.
- Status: OPEN

### D18: Manual "Complete" touches bypass the mail_sent and text ledgers (fix-complete, 2026-10-30)
- Touches: Call Mode only (call_mode.py, new Complete sheet; new /notes content via saveNotes, no new /notes or /text code). No send_server.py, no suppression code.
- Finding: Complete writes a touches entry (src:'manual') for Called / Texted / Emailed / voicemail / letter / door / other. A manual "Emailed" is not a row in mail_sent.json, so the nightly cadence cannot see it and may still email a lead the operator already emailed by hand; the touch shows only in Call Mode and the board. A manual "Texted" is not a text_sent.json row (nothing is posted to /text). A manual call or text does count toward the FTSA 3-in-24h cap (safe direction).
- Option A: keep it a touches entry only (as built) | Risk: cadence / worker double-contact on a hand-emailed lead until the operator also uses the real send path
- Option B: Complete also posts /text (confirmed) and writes a mail_sent row | Risk: a new writer of two ledgers from a free-form tap, no proof a message actually left; touches LEGAL REVIEW surface
- Recommendation: A (the default).
- Fallback in progress: A is shipped.
- Status: OPEN
