# Fix tickets, run 2026-11-08 (written by chief; main pastes one section into each fixer's spawn prompt)

Baseline: origin/main @ aac7a8a9. Read first: `docs/agent-teams/OPERATING-RULES-2026-11-07.md`, `docs/agent-teams/AUDIT-LOOP-PROTOCOL.md`, `OWNERS.md`, the CLAUDE.md sections named in your ticket.

## Rules for every fixer (apply to all four tickets)

1. Worktree only, branch `claude/agents-fix-<id>` as named below, draft PR only. Never commit in the main checkout, never merge, never push to main, never force-push.
2. Write the failing test FIRST. Run it on the unchanged tree and paste the failing output into the PR body. Then change the code until it passes.
3. Run with a temp `DEALFLOW_DIR`, no network, no real bridge port, no `.bat`, no publish, no send, no paid call. Never set the follow-ups switch. Fixtures use invented people (2099-* case ids, 555 numbers, `example.com`).
4. Never print, copy or write a real personal value (phone, name, address, mailbox) anywhere: not in a commit message, PR body, test, log or chat. Counts and line numbers only.
5. Change only the files and functions named in your ticket. Do not edit `docs/index.html`, `docs/call/` (generated), `ci_suite.py`, any `*.key` / `*.pass` / `*.codes` / `*.url` file, `sender.json`, `footprint.local.json`, or anything listed "Forbidden" below. If you think another file must change, stop and tell chief with the exact change; do not edit it.
6. The owned suppression surface (CLAUDE.md "OWNED SURFACE: suppression") is forbidden. Reading a verdict the board already computes is allowed; producing a new verdict or changing an existing one is not.
7. Baseline rule for your test run: a suite counts as pre-existing failing only if it fails the same way on `%USERPROFILE%\projects\fl-loop-base` in the same environment (same Python, same locale setting, serial, temp DEALFLOW_DIR). Run your changed and neighbouring suites on both trees, with and without `PYTHONUTF8=1`, compare the failing line. Do not edit `ci_suite.py` to change the baseline. Browser tests: the bundled Chromium is not installed on this machine; launch with `channel='chrome'` (system Chrome) in your new test, falling back to a named skip only if no browser can start.
8. PR body (protocol section 7): plain-English summary; tests run with output; tests not run and why; `Exposure:` line; flags and defaults; the sentence "Merged is not visible: visible after the next publish."; the ticket id(s); the merge class line. End with: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <<email>>`.
9. Hand off to `gate` (qa-gate) when the PR is open. `gate` writes QA PASS or FAIL; chief accepts scope after that. A QA FAIL comes back to you, max 3 rounds.

---

## TICKET A: F-wire-02 page half

- Ticket id: F-wire-02 (page half only). Severity S1. Author: wire. Chief verdict: APPROVED-FIX #1.
- Owner role (OWNERS.md): callmode-dev. Spawn prompt names the role.
- Branch: `claude/agents-fix-f-wire-02`. Base: origin/main.
- What is wrong (re-run by chief): a Do Not Contact tapped in Call Mode on a phone that cannot reach the bridge is saved locally and POSTed once to `/notes`; if the POST rejects, or the bridge answers non-2xx or `ok:false`, nothing is shown and nothing is retried, so the opt-out may never reach `optouts.json`. Proof: `%USERPROFILE%\DEALFLOW\agent-logs\2026-11-08\wire-proofs\dnc_bridge_down.py` (prints 1 attempt, 0 toasts, 0 retry timers).
- Allowed files and functions: `call_mode.py`, only the notes-push block: `callNotesPush` and `callNotesPushSoon` (about lines 6130-6141) and a new small helper next to them, plus one JS-created strip element (create it with `document.createElement`, a fixed bar, no change to the HTML skeleton). New test file `_notespushtest.py`.
- Behavior to build:
  1. Read the response of `bridgeFetch('/notes', ...)`. Failure = promise rejection, HTTP status not 2xx, or JSON with `ok:false`. Success = 2xx and not `ok:false`.
  2. On success, store the set of DNC case ids that were in `notes` at that moment as acknowledged (a localStorage key such as `fcDncAck`, case ids only). A DNC note is one with `no==='hard'`, or `status==='DO NOT CONTACT'`, or an `optout` date.
  3. On failure, compute DNC case ids in `notes` that are not in the acknowledged set. If that set is non-empty: show a persistent strip (id `notes-unsent-strip`) reading "Not backed up to the bridge: N Do Not Contact change(s) have not reached the opt-out list. Keep this page open and get on the bridge network." and retry on the same 4 / 8 / 12 second ladder `queueSync` uses (3 retries, then keep the strip and stop retrying until the next `saveNotes`). If the set is empty, do exactly what the code does today (no strip, no extra retry).
  4. On a later success, remove the strip.
  5. Never block, delay or change the local save or `saveNotes`. Never change what is sent to the bridge.
- Forbidden: `stopEverywhere`, `logOutcome`, `noState`, `hardSuppressed`, `supReason`, `suppressed`, the no / dnc / notint outcome branches, every other function in `call_mode.py`, any seat logic (`CALL_SEATS`, `seat_rows`, `make_callmode`), Whitepages or balloon fields, `send_server.py` (the `/notes` handler is report-only: its `ok:false` half is DECISIONS-NEEDED D-B), `optout_sync.py`, `docs/call/`.
- Failing test first: `_notespushtest.py`. Node-based, extracting `callNotesPush`, `callNotesPushSoon` and the new helper from `call_mode.py` the way `_callwiringtest.py` does; stub `bridgeFetch`, `localStorage`, `notes`, `document`, `setTimeout`, `today`. Assert:
  (a) fetch rejects, `notes` holds one DNC lead not yet acknowledged -> strip text present and contains "Do Not Contact", one retry scheduled at 4000 ms, then 8000, then 12000, then none;
  (b) fetch rejects, `notes` holds no DNC lead -> no strip, no retry beyond the existing `_cnBusy` reset;
  (c) fetch resolves 200 `{ok:true}` -> strip removed, acknowledged set equals the DNC case ids;
  (d) fetch resolves 500 or `{ok:false}` -> treated as failure, same as (a);
  (e) an old DNC lead that was acknowledged earlier plus a later non-DNC failure -> no strip (no nagging);
  (f) the payload passed to `bridgeFetch` is byte-identical to today's (`_dealflow_notes`, `exported`, `device`, `notes`).
  It must fail on the unchanged tree for (a), (d). Also run `_callwiringtest.py`, `_e2ewiringtest.py` (compare with base per rule 7) and the DNC / call-mode suites you touch.
- LEGAL REVIEW: yes. PR title carries it.
- Merge class: `Merge: Alex only` (DNC delivery).
- Exposure: none to homeowners. Adds an operator warning and retries the existing POST /notes; no new route, no send, no change to what the bridge ledgers. Visible on phones only after the next publish.
- PR title: `LEGAL REVIEW: call mode: warn and retry when a Do Not Contact has not reached the bridge (F-wire-02)`
- Chief checks at acceptance: diff has hunks only inside the notes-push block plus the new test; no hunk in any forbidden function; payload unchanged; strip appears only on the DNC condition.

---

## TICKET B1: F-site-01 + F-site-02 + F-ideas-01 (three board surfaces, one helper)

- Ticket ids: F-site-01, F-site-02 (written channels), F-ideas-01 (Reply inbox). Severity S1. Chief verdict: APPROVED-FIX #2.
- Owner role (OWNERS.md): board-dev. Spawn prompt names the role.
- Branch: `claude/agents-fix-f-site-01`. Base: origin/main. This is the first of three branches on `tracker_template.html`; B2 and B3 start from this branch's head.
- What is wrong (re-run by chief): the board has one verdict for "this lead must not get an email": `_boardMailHeld(r)` (client, diligence hold, claimed on another case, owner gone / Dead / transferred, sale passed, plus everything `_textContactBlocked` returns). Three board surfaces do not ask it:
  1. Text composer, `textCardHtml` (about 6866-6935): `hardBlock` comes only from `_textContactBlocked`, so for a client / ddhold / sibclaimed / dead / transferred lead the composer shows no block and enables Send. Also `_contactLineHtml` (`canTxt`, about 3782) shows Text and WA on those leads.
  2. Call sheet, `_contactBlockHtml` (about 3854-3890): the plain `mailto:` link is not checked against any hold.
  3. Reply inbox, `genReplyInbox` card (about 12483-12497): every card, STOP ones included, shows a Call button and a mailto; the card never reads any hold. The STOP banner on the same card says "Nothing more to do".
  Proof, by chief and site: `site-work\tools\b6.py up` (client / ddhold / sibclaimed / dead / transferred show 2 enabled Send links, gate ""; optout and stay show 0); `ideas-proofs\reply_probe.py` (4 cards, 4 Call buttons, 4 mailto, the STOP card included).
- Allowed files and functions: `tracker_template.html` only. One new helper, for example `_boardWrittenHeld(r)`: returns `_boardMailHeld(r)` when it is one of the five non-suppression holds (client, ddhold, sibclaimed, owner gone / transferred) and returns '' otherwise, including for 'sale passed' (leave that alone). Use it in exactly these places:
  - `_contactLineHtml`: `canTxt` becomes false when the helper is non-empty (this removes Text and WA); leave the Call anchor as it is.
  - `textCardHtml`: treat a non-empty helper as one more block value with its own `txblock` sentence (operator-facing, e.g. "On hold: <reason>. Do not text."), so `canSend` and `canCopy` are false and the tone buttons are not drawn, like the existing hardBlock path.
  - `_contactBlockHtml`: no `mailto:` link when the helper is non-empty.
  - `genReplyInbox` card: when `rep.stop` or `_textContactBlocked(r)` is non-empty, draw neither the Call button nor the mailto (the same set `_contactBlockHtml` already hides entirely); when only the helper is non-empty, drop the mailto and keep Call; keep "Open call sheet".
  Also the mailto at the other board mailto sites you find by Grep `mailto:` are NOT in scope unless they are on the three surfaces above.
- Forbidden (never edit, never reword, no hunk may touch them): `_textContactBlocked`, `_optedOutIdentities`, `_isOptedOutPerson`, `_workerEligible`, `_laneStats`, `_boardNoState`, `_isOptedOut`, `_boardMailHeld` itself, `_replyList` / `_isReplied` / `_replyWaiting`, the STOP ordering in `_replyList`, `send_server.py`, `call_mode.py`, `docs/index.html`, `docs/call/`. Do not change the Call button on client / ddhold / sibclaimed / dead / transferred call sheets: whether Call stays there is Alex's decision (D-F). Do not change any homeowner-facing text, only new operator-facing block sentences.
- Failing test first: new `_boardmanualheldtest.py`, Chrome channel (`channel='chrome'`) through Python playwright, board built from invented rows (pattern: `%USERPROFILE%\DEALFLOW\agent-logs\2026-11-08\site-work\tools\build_board.py`, but the test must build its own fixture inside the repo with invented 2099-* cases and 555 numbers, bridge fetches refused, fake `/health` with `text_hold:false` at 11:00 ET so the hours gate is open). One lead each: control (no hold), optout, stay, client, ddhold, sibclaimed, dead, transferred. Assert:
  (a) composer for client / ddhold / sibclaimed / dead / transferred: zero enabled `a.txsend`, a `.txblock` sentence present;
  (b) call sheet for the same five: no `a.ctact-text`, no `.ctact-wa`, no `a[href^="mailto:"]` inside `#csmodal`; the Call anchor is still present for them (unchanged, not a requirement of the product, just proof you did not widen scope);
  (c) Reply inbox: a STOP reply card and an optout and a stay card have no `.rbbtn.call` and no mailto; client / ddhold / sibclaimed cards have no mailto; the control card still has Call and mailto;
  (d) control lead keeps Text, WA, mailto on the call sheet and an enabled Send in the composer (no over-blocking);
  (e) optout and stay behave exactly as before (0 Send, existing gate sentence).
  It must fail on the unchanged tree for (a), (b) and (c). Run also the existing board suites you can start here and compare per rule 7: `_suppressiontest.py`, `_optouttest.py`, `_dnctest.py` must be unchanged (you do not edit them).
- LEGAL REVIEW: yes (send path, DNC and stay display). PR title carries it.
- Merge class: `Merge: Alex only`.
- Exposure: sending: this makes the board stricter. Text, WhatsApp and mailto controls disappear on held leads in three board places. Nothing new can be sent. The Call button on held-lead call sheets is unchanged (D-F). Visible only after the next publish.
- PR title: `LEGAL REVIEW: board: withhold Text, WhatsApp and mailto on held leads in the composer, call sheet and Reply inbox (F-site-01, F-site-02, F-ideas-01)`
- Chief checks at acceptance: no hunk inside any forbidden function (diff the function bodies); the helper reads `_boardMailHeld` and does not duplicate its logic; tests cover all three surfaces; no new homeowner copy.

---

## TICKET B2: F-site-08

- Ticket id: F-site-08. Severity S2. Chief verdict: APPROVED-FIX #3.
- Owner role (OWNERS.md): board-dev. Spawn prompt names the role.
- Branch: `claude/agents-fix-f-site-08`. Base: head of `claude/agents-fix-f-site-01` (or origin/main if B1 has merged). Name the predecessor in the PR body.
- What is wrong (re-run by chief, `site-work\tools\b7.py`): a lead with `eqc: {state:'unpriced', may_quote:false}` shows `BUY $85,150 ... $570k in $655k` in the desktop row and `Money: value ... net est $369,000` on the call sheet with no "do not quote" chip. `_eqcChip` (about line 3018) exists and is called only in the mobile card (about line 19148). Call Mode shows the chip correctly.
- Allowed files and functions: `tracker_template.html`, display only: the desktop row money cell (about 19036-19049), the top of `_callSheet` (about 5265), and the `Money:` / "net est" line (about 5609). Reuse the existing `_eqcChip(r)`. Where `r.eqc && r.eqc.may_quote !== true`, word the "net est" figure as a ceiling ("at most $X, liens not verified", the wording `_EQ_TIP_CEIL` and `_eqRowHtml` already use). New test `_boardeqcchiptest.py`.
- Forbidden: `_eqcChip` text and `_EQC_NAME` (reuse, do not reword), the equity bake and `r.eqc` itself (`equity_state.py`, `diligence_flags.py`, `diligence_gate.py`, `foreclosure_leads.make_tracker` equity lines: equity-dev owns them), `_liensVerified`, `_eqCeiling`, the owned suppression functions, `call_mode.py`, `docs/`. No change to any number, only to how it is labelled. No new homeowner copy.
- Failing test first: `_boardeqcchiptest.py`, Chrome channel, invented board rows: one lead `may_quote:false` (unpriced), one `may_quote:true` (priced, net 369000), one with no `eqc`. At 1440 px assert: (a) the may_quote=false desktop row contains the chip text "do not quote" exactly once; (b) its call sheet carries the chip near the top and the net-est line is worded as a ceiling, not as a plain figure; (c) the may_quote=true lead shows "may quote", no ceiling wording, unchanged net figure; (d) the no-eqc lead is unchanged; (e) at 390 px the mobile card still shows exactly one chip (no doubling). It must fail on the unchanged tree for (a) and (b).
- LEGAL REVIEW: no. (Operator display, no homeowner copy; it never says "quote" where it did not.)
- Merge class: `Merge: ready for Alex` after a clean QA PASS.
- Exposure: none. A label on figures the board already shows; no number changes; visible after the next publish.
- PR title: `board: show the do-not-quote equity chip on desktop and the call sheet (F-site-08)`
- Chief checks at acceptance: no hunk in equity bake files; the chip function is reused; the desktop row does not gain a second chip at mobile widths.

---

## TICKET B3: F-site-05 + F-site-06 (line 2633 only) + F-sec-08 (comment lines only)

- Ticket ids: F-site-05, F-site-06 (line 2633 only), F-sec-08 (comment lines only; I-05's strict rule folded in as this ticket's test). Severity S4. Chief verdict: APPROVED-FIX #4.
- Owner role (OWNERS.md): board-dev. Spawn prompt names the role.
- Branch: `claude/agents-fix-f-site-05`. Base: head of `claude/agents-fix-f-site-08` (or origin/main if merged). Name the predecessor in the PR body.
- What is wrong (re-run by chief, counts only): code COMMENTS in `tracker_template.html` carry real personal values that ship in clear text to the public page. (1) Line about 15316: a personal-looking phone number (the same value is in 4 tracked files including `docs/index.html`). (2) Line about 2633: a homeowner's name beside a case id. (3) F-sec-08's list of comment lines quoting real street addresses or owners: about 3246, 3335, 3349, 5286, 6792, 8269-8274, 16774-16776, 20166 (judge each by reading it: invented or masked examples stay; anything that looks real is replaced). (4) Line about 12443: an example phone number inside a comment about owners typing a number into a reply (the same value is in `replies.py` and `_stoptexttest.py`): read the line and decide; if it is not a public business line, replace it in `tracker_template.html` with an invented 555 number and report the other two files to chief, do not edit them. CLAUDE.md "Personal identifiers never ship": no real personal value is ever a literal, a comment included; use invented values (`100 NW 1st Ave`, `<email>`, 555 numbers, `Owner Example`).
- Allowed files: `tracker_template.html`, COMMENT text only (`//`, `/* */`, `<!-- -->`). New test `_commentpiitest.py`. After the edit, regenerate `design-preview.html` with `python build_preview.py` (never by hand). If `build_preview.py` or `footprint.assert_clean` refuses, do not bypass it: leave `design-preview.html` alone and say so in the PR body. If the regenerated diff also removes stale mailboxes, say so with counts.
- Forbidden: any non-comment code in `tracker_template.html`; the comment block above `_optedOutIdentities` (line about 3656: names an opted-out owner; it is the owned suppression surface, report to chief and Alex, D-G b); the Playbook intel paragraph (about 6086-6095; Alex's content, D-G c); `badnum_audit.py` (unowned, reported to Alex); `footprint.py` and `publish_site.py` (publish gates: a generic phone rule there is Alex's decision); `replies.py`, `_stoptexttest.py`, `docs/index.html`, `docs/call/`. Never print the values you remove.
- Failing test first: `_commentpiitest.py`, plain Python, reads `tracker_template.html`, extracts comment text only. Assert for comments: (a) no consumer mailbox (gmail, yahoo, outlook, hotmail, icloud, aol, live, msn) except obvious placeholders (`you@`, `user@`, `someone@`, `test@`, `example.com`); (b) no 10-digit phone shape except 555 numbers, repeating or sequential digit runs, and a short explicit allow-list of the company's public lines, imported from the constants that already hold them (do not type a real value into the test; if you cannot import it, allow the line by file and line text hash and tell chief); (c) any case id shape must start with `2099-` or `99-`; (d) no street-address shape (`\d{3,5} (NW|NE|SW|SE) \d+ ...`) except a short allow-list of invented examples. Exempt only the comment block directly above `function _optedOutIdentities` (found by name, not by line number) and the Playbook block, each with a comment in the test naming the DECISIONS-NEEDED entry that owns it. Include one negative case that feeds the checker a temp string with an invented 305 number and a case id and expects failure, and one with a 555 number and expects pass. It must fail on the unchanged tree. Run `python _footprinttest.py` and compare per rule 7.
- LEGAL REVIEW: no (comments only, no behavior).
- Merge class: `Merge: ready for Alex` after a clean QA PASS.
- Exposure: none to homeowners. The page text does not change except removal of the values from comments. The values stay in the public repo history and on the live page until the next rebuild and publish; scrubbing history is Alex's decision (D-G a). Visible after the next publish.
- PR title: `board: remove personal values from template comments (F-site-05, F-site-06 line 2633, F-sec-08 comments)`
- Chief checks at acceptance: every hunk in `tracker_template.html` is inside a comment (check with a diff that strips comments: executable code identical before and after); the three excluded places are untouched; the test has no real value in it.

---

## Order and file sharing (for main)

- Fixer A (callmode-dev): Ticket A on `call_mode.py`. Runs now, in parallel.
- Fixer B (board-dev): B1, then B2, then B3. All three edit `tracker_template.html`: one at a time, each branch started from the previous one's head. Never two fixers on `tracker_template.html`.
- Two fixers alive at most. `gate` checks each PR as it opens.
- Not in the four, queue behind them if the cap lifts: I-01 (Reply inbox sort and waiting chip: needs B1 merged first, same lines), F-sec-09 with I-06 (escJs at 25 sites, `esc(JSON.stringify())` not `&#39;`), I-02 (test encoding), I-03 (`reports/not_visible_yet.py`), F-wire-08, F-site-09, F-site-10, P-site-11 / I-07.
