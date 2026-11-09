# worklog-site (role: site-qa), run 2026-11-08, baseline origin/main @ aac7a8a9, worktree %USERPROFILE%\projects\fl-loop-site (detached, clean, never edited)

Harness (reproducible, fake data only): `%USERPROFILE%\DEALFLOW\agent-logs\2026-11-08\site-work\tools\` (scripts), `...\site-work\shots\` (screenshots).
All paths below are relative to `site-work` unless absolute. Every script sets `DEALFLOW_DIR=site-work\df` (empty temp dir), serves `site-work\tree` on a free 127.0.0.1 port, drives the installed Chrome (`channel='chrome'`) through Python playwright, and refuses every request that is not a GET to its own fixture server (writes to 127.0.0.1:8823 and 8765 are logged and answered 403; nothing left the machine).
Rebuild: `cd <worktree>; python site-work\tools\build_pages.py` (Call Mode all/seat0/Carlos via call_mode.call_rows + coverage_rows + build_html, test code TESTCODE), `python site-work\tools\build_board.py` (board with 11 invented leads, one hold each), `python build_preview.py --out site-work\preview.html` (the stock preview).
Tooling note: node `require('playwright')` fails here (MODULE_NOT_FOUND). Python playwright imports; its bundled Chromium is NOT installed (default `chromium.launch()` prints "Executable doesn't exist"), the installed Chrome channel works. See P-site-11.

## Step log
17:20 READ  CLAUDE.md, OPERATING-RULES, AUDIT-LOOP-PROTOCOL, PATTERNS, OWNERS, OUTPUT-TEMPLATE, site-qa.md  -> done
17:28 RAN   python build_preview.py --out site-work\preview.html -> "wrote ... (7 sample leads, plaintext, no gate)"; git status clean
17:31 RAN   build_pages.py -> Call Mode builds from invented slim rows; call_rows dropped held leads (dial rows 32, held leads 2-8 absent), coverage rows for them carry no numbers
17:40 RAN   nav2.py / nav3.py (nav strip, 4 pages x 390/1024/1440): 6 items on every page, no off-screen item, no horizontal scroll, every relative link resolves 200; #playbook opens the modal, #worker=open opens the worker window, board buttons open call/ carlos/ balloon
17:44 RAN   btn1.py (click sweep, 115 board buttons at 1440, 59 at 390, 25 Call Mode, 24 Carlos): 0 page errors, 0 console errors (only my own 403/404 for blocked network and missing fixture images). Refresh/Lock/Export/Send/Sync buttons skipped on purpose.
17:46 RAN   auto1.py: board2.html#worker=morning on an unarmed device with a fake-OK bridge -> a worker window opens, POSTs seen: /notes and /runlog only, no /send, no run started (fine)
17:48 RAN   copy1.py / copy2.py: board SMS (4 tones x EN/ES/both) and all email bodies carry the approved lines from outreach_copy.py; Call Mode TEXT_T / TEXT_T_ES and the voicemail carry theirs; identity line from disclaimer.py present in every email body. No drift found.
17:50 RAN   live read-only GET (Range 0-4095) of the 4 published URLs from board_url.BOARD_URL: all HTTP 206; board line 1 carries DEALFLOW-COVERAGE (built 2026-11-04T22:48, leads 4761); call/, call/carlos/, balloon have no census line (expected). I did NOT run footprint.assert_clean on the live text: it needs footprint.local.json, which I may not read and which is absent in this worktree. I ran my own regex scan on the tracked copies instead (F-site-05, -06).
17:55 NOTE  SendMessage is not exposed in this session (no such tool), so tickets could not be messaged to chief one by one. Every ticket below is complete and is repeated in the final hand-off message to main.

## Passed (no ticket)
- Check 1 nav: 4 pages x 3 viewports, 0 failures (nav2.py output: all rows "bad []", scrollW == vw).
- Check 2 console: 0 page errors on load and across the click sweeps. Observation (not a defect, by design): the board POSTs /runlog and /notes to 127.0.0.1:8823 on load and on clicks, the Worker POSTs /runlog on open, and opening a call sheet starts `_runDiligence(auto)` which POSTs 127.0.0.1:8765/diligence {live:false}. All blocked by the harness. A human running QA with a live bridge would write run-log and note rows.
- Check 3 Call Mode: held leads (case opt-out, all-number DNC, stay, Dead, client, claimed, rfHold) are absent from the dial queue and ship in coverage rows with 0 numbers. Morning Worker queue (`_workerQueue`) for urgent/active/early/replied/balloon holds only control + 2 eqc leads; no held lead in any lane.
- Check 4 Call Mode and Worker: `textingHeld()` is True with the bridge unreachable, banner "HOLD texting: ..." shows, the lookup Text link is replaced by the hold text, Worker shows `#mwtexthold` and a click on Text logs the refusal, no sms navigation. With a fake-OK /health (text_hold:false) Call Mode releases, as designed (TEXTHOLDLIVE).
- Check 5 Call Mode: a may_quote=false lead (eqc.q 0) shows "Equity not verified -- do not quote a number (a ceiling, not a figure)", no net equity dollars (call-card-009.png).
- Check 7: see copy1/copy2 above. No drift.

---

### F-site-01  Board text composer sends enabled for client, diligence-hold, claimed-at-auction, Dead and transferred leads
- Type: FINDING
- Area: board
- Severity: S1
- Evidence: tracker_template.html:3707-3718 `_textContactBlocked(r)` returns only optout / wrong / bk; `textCardHtml` (tracker_template.html:6870-6935) takes `hardBlock` from it, so no other hold reaches the composer. `_boardMailHeld` (tracker_template.html:3725-3737) already knows client / ddhold / sibclaimed / dead / sale passed for email, text does not read it.
  Command: `python site-work\tools\build_board.py` then `python site-work\tools\b6.py up` (fake /health {text_hold:false}, clock 11:00 ET, `openTextSingle(lead)` for 11 invented leads). Output excerpt (send = count of enabled `a.txsend` Send links):
  `control send 2 | optout send 0 gate "OPTED OUT" | stay send 0 gate "ACTIVE BANKRUPTCY STAY" | dead send 2 gate "" | client send 2 gate "" | ddhold send 2 gate "" | sibclaimed send 2 gate "" | transferred send 2 gate ""`
  `_workerEligible` for the same leads (b7.py) is False for client / ddhold / sibclaimed / dead / transferred, so the worker holds them and the manual board path does not. Screenshot: site-work\shots\board-callsheet-client.png (banner "NOT A LEAD ... No cold call, text" above enabled Text).
- Repro: (1) `cd %USERPROFILE%\projects\fl-loop-site`; (2) `python %USERPROFILE%\DEALFLOW\agent-logs\2026-11-08\site-work\tools\build_board.py` (invented leads: case 2026-000904 Dead via SERVER_DEADS, 905 client:true, 906 ddhold:true, 907 sibclaimed:true, 908 title_status:transferred); (3) `python ...\b6.py up`. Bridge-down variant `b6.py down`: every lead is refused with "Texting is held" (correct). No network, temp DEALFLOW_DIR.
- Proposed fix (smallest): a new board helper (not an edit of the suppression functions) that returns the `_boardMailHeld` reason for the five non-suppression holds, used by `_contactLineHtml` (`canTxt`) and by `textCardHtml` as an extra `hardBlock` value with its own `txblock` sentence. Test: new `_boardmanualheldtest.py` that loads the stock preview with one lead per hold and asserts zero `a.txsend`. Whether `_textContactBlocked` itself should grow these cases is a decision for the suppression-surface owner.
- Owner role (OWNERS.md): report-only for `_textContactBlocked` (owned suppression surface); board-dev for the new helper and the call-sheet wiring.
- Touches: send, suppression, copy
- Confidence: high that the composer enables Send (reproduced, repeatable, same code path as a real lead). Medium that product intends manual texting of a "client" to stay possible; the client banner says "No cold call, text ..." so the screen contradicts itself.

### F-site-02  Board call sheet shows Call, Text, WA and a mailto link on leads the sheet itself labels do-not-contact
- Type: FINDING
- Area: board
- Severity: S1
- Evidence: tracker_template.html:3856-3860 `_contactBlockHtml` hides contacts only for `block` = wrong / optout / bk; client, ddhold, sibclaimed, Dead, transferred fall through to `_contactLineHtml` (line 3782 `canTxt`, line 3890 plain `mailto:` anchor with no `_boardMailHeld` check).
  Command: `python site-work\tools\b5.py` (bridge down). Output excerpt:
  `== sibclaimed ['tel:+1305555 ctx=(305) 555-0101CallText WA', 'tel:+1786555 ...', 'mailto:teste ctx=<email>']` while the same sheet's banner reads "CLAIMED - THIS PROPERTY ALREADY SOLD AT AUCTION ... Do not knock, call, or mail."
  `== client` the same three controls under "EXISTING CLIENT, NOT A LEAD ... No cold call, text, letter, door". `== dead` and `ddhold` show Call + mailto, no banner on ddhold at all.
  For contrast `b3.py down`: optout and stay sheets show 0 dial / text / email controls (correct).
  Screenshots: site-work\shots\board-cs2-sibclaimed.png (banner), board-cs2-client.png, board-cs2-dead.png.
- Repro: `python site-work\tools\build_board.py`, `python site-work\tools\b5.py`; or `b3.py down` for the per-surface control list. Fake data, no network.
- Proposed fix (smallest): in `_contactBlockHtml` return the same one-line `csbad` notice used for optout/bk when `_boardMailHeld(r)` is non-empty for the five holds (leave "sale passed" alone); keep DNC numbers call-only as designed (see below). Test: extend the same new `_boardmanualheldtest.py`, assert no `a[href^=tel:|mailto:]` in `#csmodal` for those holds.
- Owner role (OWNERS.md): board-dev
- Touches: send, DNC, copy
- Confidence: high on the controls rendering. Note for chief: an all-number-DNC lead (phdnc true on every number) keeps a "DNC - call only" Call button on the board by an explicit code comment (tracker_template.html:3802 "DNC numbers stay dialable (manual single dial ...)"), unlike Call Mode which drops them. I did not file that as a bug, it is a documented board/Call Mode policy difference; the audit check 3 wording ("DNC never shows a dial") disagrees with it, Alex or legal should say which is right.

### F-site-03  Carlos's Call Mode page carries Balloon lane rows, the balloon script and a "Balloon" lane button
- Type: FINDING
- Area: carlos-seat
- Severity: S4
- Evidence: call_mode.py:2652-2661 puts `st:'BAL'` rows and their `bal` object (lender, amount, LTV, verdict) into the dial list, and call_mode.py:2702 gives them the same case-hash seat bucket as any lead; `seat_rows` (call_mode.py:3854) filters only by bucket. Nothing excludes balloon rows from seat 1.
  Command: `python site-work\tools\carlos1.py` (fixture: <street>:BAL leads). Output:
  `alejandro {... "n": 17, "bal": 6, "balCov": 6, "balField": 6 ...}` / `carlos {"seat": {"n": 2, "i": 1, "w": "Carlos"}, "n": 15, "bal": 6, "balCov": 6, "balField": 6 ...}` and `carlos balloon lane button: ['Balloon · 6']`.
  `python site-work\tools\carlos3.py`: `balWithBalObj: 6`, `script has bal: True`. The tracked, live-mirrored page docs/call/carlos/index.html also carries the balloon script phrase in clear (`balloon script phrase present in clear page: True`), so the lane and script ship to his seat today; whether real balloon rows reach his payload depends on the nightly balloon_leads.json, which I cannot read (the rows are inside the encrypted payload).
  Screenshot: site-work\shots\carlos-balloon-lane-390.png.
- Repro: `python site-work\tools\build_pages.py`; `python site-work\tools\carlos1.py`; `python site-work\tools\carlos2.py` (clicks the Balloon lane on the Carlos page). Temp DEALFLOW_DIR, no network.
- Proposed fix (smallest): drop `st == 'BAL'` rows (and their coverage rows) from `seat_rows` / the coverage cut when `seat[1] != 0`, and hide the lane button when `SEAT.i != 0`. Test: new case in `_seattest.py` or `_balloonlanetest.py`: build seat (2,1) from a slim list with balloon rows, assert zero `st:'BAL'` in rows and coverage.
- Owner role (OWNERS.md): callmode-dev (call_mode.py; not the no/dnc/notint outcome branches)
- Touches: Carlos-seat
- Confidence: high on page behavior with invented rows; medium on production exposure (rule in AUDIT-LOOP-PROTOCOL section 8 and loop prompt rule 5 says never). Severity S4 chosen because the rows hold phone numbers of the people behind the LLCs; chief may rate it S5 if it is only a policy rule.

### F-site-04  Carlos's Call Mode page carries Whitepages-sourced numbers and the Whitepages search / paste / Relative controls
- Type: FINDING
- Area: carlos-seat
- Severity: S4
- Evidence: Command `python site-work\tools\carlos3.py` -> `{'seat': {'n': 2, 'i': 1, 'w': 'Carlos'}, 'rows': 15, 'withWPsrc': 5, ...} script has bal: True | paste box: True`. `withWPsrc` counts Carlos rows whose number-source tags are `nm` / `hh` (Whitepages name-match / household, `ps` field). `python site-work\tools\carlos2.py` prints `WP controls visible: ['Whitepages addr', 'Whitepages', 'Copy for Whitepages search', 'Whitepages by address', 'Whitepages by name', 'Relative', 'pcclip:Paste']`. Code: call_mode.py:6916-6990 (paste box, Owner / Relative / Not sure) has no `SEAT` test; the tracked docs/call/carlos/index.html contains `pcclip` in clear.
- Repro: `python site-work\tools\build_pages.py`, `python site-work\tools\carlos2.py` and `carlos3.py`. Fixture leads 60-69 carry `phsrc ['wp','nm']`, no network.
- Proposed fix (smallest): when `SEAT.i != 0`, do not render the paste box / Whitepages links / Relative chooser, and drop rows' `nm`/`hh`/`wp`-sourced numbers in `seat_rows` for seat 1; or get Alex's written approval that Carlos may use the paste box (protocol section 8 says no change may bring pasted Whitepages numbers to his seat, this is existing behaviour, not a change, so chief decides whether it is in scope). Test: `_seattest.py` assertion that the seat-1 page has no `#pcclip`.
- Owner role (OWNERS.md): callmode-dev
- Touches: Carlos-seat
- Confidence: medium. The behaviour is reproduced; whether it counts as "Whitepages data" under the rule (links and a paste box versus delivered records) is a policy call. The relatives number tag stays log-only (RELDIAL false) as designed.

### F-site-05  A real homeowner's phone number sits in a code comment that ships in clear text to the public site
- Type: FINDING
- Area: board
- Severity: S4
- Evidence: tracker_template.html:15316 comment "One of those was (###) ###-####, whose owner texted in that evening asking to sell a house ..." (digits masked here on purpose).
  Command (counts only, number not printed): `git grep -c -E <that number> HEAD --` -> `HEAD:badnum_audit.py:1`, `HEAD:design-preview.html:1`, `HEAD:docs/index.html:1`, `HEAD:tracker_template.html:1`. docs/index.html is the published board, so the number is in clear text on the public page now (visible today; merged-and-published already).
  Why the guard missed it: footprint.assert_clean looks for consumer mailboxes and values from footprint.local.json, it has no generic local-phone detector (my own scan of tracker_template.html / call_mode.py / balloon and copy modules found this one local-area non-555 number in a comment; the other hits are a law firm's public line, hotlines, and the company line in outreach_copy.py).
- Repro: `cd <worktree>; python -c "import re;s=open('tracker_template.html',encoding='utf-8').read();i=s.find('was being used as \"next\"');print(bool(re.search(r'\(\d{3}\) \d{3}-\d{4}', s[i:i+300])))"` -> True (prints a boolean only). Then check the tracked public page the same way on docs/index.html.
- Proposed fix (smallest): delete the number from the comment in tracker_template.html (and badnum_audit.py), rebuild, and have footprint.problems() flag any non-555, non-toll-free 10-digit phone in the clear text. docs/index.html and design-preview.html change only on rebuild: "merged, visible after the next publish" (and the history of the public repo still holds it, that is Alex's call).
- Owner role (OWNERS.md): board-dev for tracker_template.html; report-only for docs/index.html (generated) and for footprint.py's rule (not in OWNERS, chief to assign); badnum_audit.py has no owner row.
- Touches: none (personal data, no send)
- Confidence: high that the number is in the four tracked files and in the clear text of the public page; medium that it is a real person's number (the comment says the owner texted that evening, I did not look the number up).

### F-site-06  Two code comments in the public board pair a homeowner's full name with a case number, one an opted-out owner
- Type: FINDING
- Area: board
- Severity: S4
- Evidence: tracker_template.html:2633 ("Born from <full name> (<case-id>): dismissed 07/31, still pitched as Tier A ...") and tracker_template.html:3656 (comment in `_optedOutIdentities` naming an opted-out owner with case <case-id> and saying one address maps to a live lead). Command: a scan of tracker_template.html and call_mode.py for lines with a case id and a person-like name -> `tracker_template.html [2633, 3656]`, `call_mode.py [3797]` (call_mode.py:3797 is a Python comment above `_PAGE`, not shipped). `git grep -c -F <case id> HEAD --` shows both ids in `docs/index.html` (3 and 2 lines) and `design-preview.html`, i.e. in the published clear text. Names are deliberately not copied here.
- Repro: `grep -n "Born from" tracker_template.html` and `grep -n "already suppressed on its own" tracker_template.html` (read the two comments yourself); then `git grep -c -F <case-id> HEAD -- docs/index.html design-preview.html`.
- Proposed fix (smallest): replace the two names with invented ones (CLAUDE.md > Personal identifiers: "no real personal value is ever a literal ... a comment"), rebuild; add an assert in footprint.problems() or `_footprinttest.py` that a case id in a comment may not sit within 80 characters of a capitalised name pair. Also tell Alex: the comment on line 3656 names a person who opted out, which makes "who opted out" public.
- Owner role (OWNERS.md): board-dev (tracker_template.html is its file; neither comment is inside the protected functions' code, only above them, but 3656 sits in the `_optedOutIdentities` comment block, so ask the suppression-surface owner before touching)
- Touches: suppression (comment block of a protected function), copy
- Confidence: high that the comments exist and are published; they are public-record case numbers, the sensitivity is the name-plus-opt-out pairing.

### F-site-07  Balloon Book published copy lists 150 property street addresses with owner entity names in clear text
- Type: FINDING
- Area: balloon-book
- Severity: S4
- Evidence: docs/hm-balloon-q7v3n8/index.html (tracked, 86 KB). Command (counts only): 150 data rows, owner column entity-looking in 150 of 150, street address present in 150 of 150, "Human" column is `on file` for every row (the redaction worked). hardmoney_balloon.py:346-356 documents this as intentional ("the site copy carries only corporate public record").
  The page has no encryption or gate; the folder name is the only barrier and the code comment itself says that is "no protection at all".
- Repro: `python -c "import re;s=open('docs/hm-balloon-q7v3n8/index.html',encoding='utf-8').read();r=[re.findall(r'<td[^>]*>(.*?)</td>',x,flags=re.S) for x in re.findall(r'<tr>(.*?)</tr>',s,flags=re.S)];print(len([c for c in r if len(c)>=9]))"` -> 150.
- Proposed fix (smallest): none until Alex decides; options are encrypt it like the board or drop the address column from the published copy. Check 6 in site-qa.md says no address in clear outside the payload, so this is filed for a decision, not as a defect against the code's stated design.
- Owner role (OWNERS.md): report-only (hardmoney_balloon.py and the generated docs file are not in a dev role's list)
- Touches: none
- Confidence: low as a defect (documented design, property addresses of entities), high as a fact.

### F-site-08  Board desktop shows BUY and net-equity dollars on a lead whose eqc.may_quote is false and never shows the "do not quote" chip
- Type: FINDING
- Area: board
- Severity: S2
- Evidence: `_eqcChip` is called only in the mobile card (tracker_template.html:19148 inside `if(_mob)`), so the desktop row and the call sheet never show "Equity: Ceiling only - do not quote". Command `python site-work\tools\b7.py` (lead 2026-000909 with `eqc {state:'unpriced', may_quote:false}`) -> row text `BUY $85,150 15% ROI . $570k in $655k`, call sheet 14 dollar figures incl. `Money: value $655,000 - FJ $286,000 - senior $0 - tax not pulled -> net est $369,000` (unqualified), `eqc chip in doc: 0`. The same lead at 390px shows `Equity: Ceiling only - do not quote` (b8.py last line), so the state is known and only drawn on one layout. Some lines are qualified correctly ("at most $369,000 (liens not verified)", "NOT YET VERIFIED").
  Call Mode is correct for the same lead (cm3.py: "Equity not verified -- do not quote a number (a ceiling, not a figure)").
- Repro: `python site-work\tools\build_board.py`, `python site-work\tools\b8.py`, screenshot site-work\shots\board-mobile-390.png for the chip.
- Proposed fix (smallest): render `_eqcChip(r)` in the desktop row's money cell and at the top of `_callSheet`, and word the `Money: ... net est` line as a ceiling when `!eqMay`-equivalent (`r.eqc && !r.eqc.may_quote`). Test: board fixture with a may_quote=false lead, assert the chip is present at 1440px.
- Owner role (OWNERS.md): board-dev (display); equity-dev owns the bake of eqc, unchanged
- Touches: copy
- Confidence: medium. The board labels most figures as ceilings, so a reader may see enough; the check as written asks for none and the desktop BUY figure is unqualified by the chip.

### F-site-09  Board call-sheet Text buttons do not say the text hold, and the composer tooltip points at a Copy button that is not there
- Type: FINDING
- Area: board
- Severity: S5
- Evidence: Command `python site-work\tools\b9.py` with the bridge down (TEXT_HOLD set): `call-sheet text controls: ['Text|title=Text via Phone Link ...', 'Text|...', 'Messages - Text / Phone Link|title=']`, `held notice anywhere in call sheet: False`; after a click the composer says "Texting is held: the send bridge is not running ..." (correct, nothing sends). In the composer the greyed Send carries the title "Sending disabled - see the notice above (hours / cap). Use Copy instead." but `canCopy` is false under a hold (tracker_template.html:6881-6883), and the button list in the output has no Copy. The Morning Worker's Text button (`#mw-text`, "Text composer") also reads the same while held; its page does show the `#mwtexthold` banner and the click logs the refusal (w5.py), so only the label is silent there.
- Repro: `python site-work\tools\b9.py` and `python site-work\tools\w5.py`. Fake data, no network.
- Proposed fix (smallest): when `TEXT_HOLD` is set, render the contact-line Text anchor and the Messages bar as the same greyed "Texting held" span used for DNC numbers; change the off-span title when `textHeld`. Test: board fixture, bridge blocked, assert no `a.ctact-text` and a held label.
- Owner role (OWNERS.md): board-dev
- Touches: copy
- Confidence: high.

### F-site-10  Board at 390px: the action row (Reply inbox, Fresh filings, Miscalculated, Near me) sits off screen with no scrollbar
- Type: FINDING
- Area: board
- Severity: S5
- Evidence: Command `python site-work\tools\lay2.py` -> `{'ox': 'auto', 'flexWrap': 'nowrap', 'sw': 892, 'cw': 364, 'kids': [['nextup',13,124],['closers',131,225],['due',232,306],['plan',313,426],['replybtn',433,532],['freshlp',539,662],['mislist',669,795],['nearmebtn',802,903]], 'sbw': 'none'}`. Only 3.5 of 8 buttons show; `Plan` is cut at the right edge (a weak swipe cue) and the other four are fully off screen. Same pattern dealflow_nav.py fixed for the nav strip on 2026-10-26 ("reads as missing"). The Reply inbox, which lists STOP requests first, is one of the hidden ones. Screenshot: site-work\shots\board-actions-390.png. Nav strips themselves pass at 390 / 1024 / 1440 (nav2.py).
- Repro: `python site-work\tools\lay2.py` (390px viewport, fixture board), no network.
- Proposed fix (smallest): let `.actions` wrap at <= 520px (like `.dfnav` does, tracker_template.html:2109), or add a visible scroll fade. Test: `_calllayouttest`-style check that every `.actions .abtn` has `right <= innerWidth` at 390px.
- Owner role (OWNERS.md): board-dev
- Touches: none
- Confidence: high on geometry, medium on severity (it scrolls, the cut-off button hints at it).

### P-site-11  The browser UI suite cannot start on this machine: Python playwright has no bundled Chromium and node playwright is not installed
- Type: PROCESS
- Area: process
- Severity: S5
- Evidence: `node -e "require('playwright')"` -> `node:internal/modules/cjs/loader:1459 throw err` (MODULE_NOT_FOUND). `python -c "from playwright.sync_api import sync_playwright; ..."` with default `chromium.launch()` -> "Executable doesn't exist" banner; `launch(channel='chrome')` -> `chrome ok 157.0.8103.38`. `ui_tests/test_operations.py` calls `chromium.launch(headless=True)` with no channel, so `python -m unittest discover -s ui_tests` would error in setUpClass here; UI-TESTS.md assumes `python -m playwright install --with-deps chromium`.
- Repro: run the two one-liners above in the worktree.
- Proposed fix (smallest): none in code. Either run `python -m playwright install chromium` on the operator machine (a download, Alex's call, $0) or let `ui_tests` fall back to `channel='chrome'` when the bundle is missing (UI-TESTS.md edit + one line per suite). Harness used for this audit already uses the Chrome channel.
- Owner role (OWNERS.md): qa-gate (tests/) / chief for the process call
- Touches: none
- Confidence: high.

---

## OUTPUT-TEMPLATE

### site-qa-2026-11-08: Site QA of board, Call Mode, Carlos seat, Worker, Playbook, Balloon Book from fake rows
- Task: audit-loop ticket pass, site role, 11 tickets (10 findings, 1 process), no code edited
- Owner: site (site-qa)
- Branch + PR: none (read-only audit, detached worktree %USERPROFILE%\projects\fl-loop-site @ aac7a8a9, nothing committed)
- Files changed: none in the repo. Run folder only: worklog-site.md, site-work\tools\*.py, site-work\shots\*.png, site-work\tree\ (generated fake pages)
- What changed (plain English): nothing in the product. Built the board, Call Mode, Carlos seat and Balloon Book from invented rows in a temp folder, drove them in Chrome with all outside traffic refused, and wrote tickets for what broke the rules.
- Tests run (command + result): nav2.py / nav3.py -> 0 failures (4 pages x 3 widths); btn1.py -> 223 buttons clicked, 0 JS errors; b3.py / b5.py / b6.py / b7.py / b9.py -> held-lead matrix (see F-site-01, -02, -08); carlos1/2/3.py -> F-site-03, -04; copy1/copy2.py -> no copy drift; lay1.py -> no horizontal overflow except the F-site-10 action row; live GET of 4 published URLs -> 206 each. Existing suites were not run (the 14 known-failing suites are the baseline; this role does not rerun them).
- QA verdict (PASS/FAIL + by whom): not applicable (no code change); findings await chief triage
- Exposure: sending (F-site-01, -02: a board Text/Call/mailto on held leads, nothing sent by me), suppression (F-site-01 via `_textContactBlocked`, F-site-06 comment block), none for the rest; spend: none, $0
- Flags (default off?): none touched; DEALFLOW_RELATIVE_DIAL left at its default (off) in every build
- Owned-surface bugs found: `_textContactBlocked` (tracker_template.html:3707) does not cover client / diligence hold / claimed-at-auction / Dead / transferred, so the manual text composer is open for them (F-site-01). Reported, not fixed.
- Needs Alex (one line each): (1) F-site-05 a homeowner's phone number is in the public board's clear text right now and in the public repo history; (2) F-site-06 same for two named homeowners, one opted out; (3) F-site-03/04 Carlos's page carries balloon rows/script and the Whitepages paste/search UI; (4) F-site-07 Balloon Book public copy lists 150 property addresses, keep or encrypt; (5) all-number-DNC leads keep a call-only button on the board but not in Call Mode, which rule is right (F-site-02 note)
- Decisions needed (link to the DECISIONS-NEEDED entry): none written (the role may not write under docs/); items (3), (4), (5) above are decisions
- Next run should know: harness is reusable (site-work\tools, Chrome channel, Python playwright); live board was built 2026-11-04T22:48 (4 days old, publishing is frozen); several public-record caches (batchdata_liens.json, comps.json, diligence_cache.json, geocode_cache.json, ownership.json, sibling_cases.json, wp_prop_ids.json) are tracked and carry real case ids, which is data-auditor territory; SendMessage was not available to this role so chief received tickets only through the hand-off message; I did not run footprint.assert_clean on live text because it reads footprint.local.json.
