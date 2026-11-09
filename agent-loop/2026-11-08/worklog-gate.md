19:00  QA PASS  F-wire-02  python _notespushtest.py (branch 6b55b38d, temp DEALFLOW_DIR)  27 ok, ALL PASS
19:00  QA PASS  F-wire-02  python _notespushtest.py with origin/main call_mode.py copied beside the test in a scratch dir (failing-first reproduced)  FAILED: 19 (strip, retry ladder, ack, ladder restart); (b)(e)(f) pass as the ticket expects
19:01  QA PASS  F-wire-02  _callwiringtest _e2ewiringtest _carlostest _equityroutetest _callstatstest, both trees, PYTHONUTF8 off and on  rc=0 on both trees every mode; _suppressiontest rc=1 same 2 FAIL lines on both trees (17/19; lob, board/worker gate needs the encrypted board inputs); _dnctest/_optouttest rc=1 on both trees: Playwright bundled Chromium missing (BrowserType.launch Executable doesn't exist), not a branch effect and NOT evidence
19:10  QA PASS  F-wire-02  python ci_suite.py (branch, serial, fresh temp DEALFLOW_DIR)  CI SUMMARY: 280 pass / 9 fail, 39 skipped; the 9 are all in the known-14 baseline list; each re-run alone on base and branch, same failing lines (differences only in temp dir names and a clock time)
19:12  QA PASS  F-wire-02  diff checklist  LEGAL REVIEW in PR title (gh pr view 452); Merge: Alex only (DNC delivery); "Merged is not visible: visible after the next publish."; Exposure none-to-homeowners honest; 4 call_mode.py hunks (6130-6196), all inside _cnT var..callNotesPush; bridgeFetch body/method/headers are unchanged context lines, test (f) pins them; 5 files in diff stat, DECISIONS-NEEDED append-only (0 removed lines); PII scan 0 findings; no seat/WP/balloon/forbidden-function token in any added line
19:13  NOTE  F-wire-02  non-blocking: (1) DECISIONS-NEEDED D24 "Touches" names helpers _cnDncIds/_cnAcked/_cnStrip/_cnPushed/_cnGood that do not exist (code has nested dncIds/acked/strip/good/done); correct the entry. (2) call_mode.py good(): a 2xx whose body is not JSON, or an exception inside good(), counts as success (fail-open; e.g. a captive-portal 200 page would be taken as delivered and mark ids acknowledged); matches the ticket's literal rule "2xx and not ok:false" but chief may want 2xx + parseable JSON. (3) PR body says _carlostest fails on both trees; here it passes (51 passed, 0 failed) on both, so that sentence is environment-specific.

### F-wire-02 QA: Call Mode warns and retries when a Do Not Contact has not reached the bridge (page half)
- Task: QA gate for PR #452 (S1, LEGAL REVIEW, Merge: Alex only)
- Owner: gate (qa-gate); product owner callmode-dev
- Branch + PR: claude/agents-fix-f-wire-02 @ 6b55b38d / docs/agent-teams/pr/fix-f-wire-02.md
- Files changed: none by gate (worklog-gate.md only)
- What changed (plain English): nothing; verification. The branch makes Call Mode read the /notes answer, show a red strip and retry 4/8/12 s when a Do Not Contact is not acknowledged, with an ids-only ack key.
- Tests run (command + result): _notespushtest 27 ok (base copy: 19 FAIL); wiring, carlos, equity, callstats pass both trees; suppression same 2 FAIL lines both trees; dnc/optout blocked by missing Playwright Chromium on both trees; ci_suite 280 pass / 9 fail / 39 skipped, 9 = known baseline, same lines on base. Not run: skipped browser suites (39), Chrome check of the strip, live bridge.
- QA verdict (PASS/FAIL + by whom): PASS by gate, with 3 non-blocking notes (D24 helper names wrong, fail-open on non-JSON 2xx, PR body carlostest sentence)
- Exposure: suppression/DNC delivery (operator warning and retry of existing POST /notes only); Merge: Alex only
- Flags (default off?): none; phone key fcDncAck
- Owned-surface bugs found: /notes answers ok:true when its DNC sweep fails (D21), already reported
- Needs Alex (one line each): merge decision (LEGAL REVIEW, DNC delivery)
- Decisions needed (link to the DECISIONS-NEEDED entry): D24
- Next run should know: merged is not visible, visible after the next publish; dnc/optout browser suites cannot run here without Chromium

20:10  QA PASS  F-site-08  python _boardeqcchiptest.py (branch d722a8bf, system Chrome, PYTHONUTF8 off and on)  19 checks, 0 failed (both)
20:10  QA PASS  F-site-08  BOARDEQC_TEMPLATE=<origin/claude/agents-fix-f-site-01 template> python _boardeqcchiptest.py (failing-first on the predecessor, the test's own template override)  19 checks, 8 failed (a x2, b x4, c x2)
20:11  QA PASS  F-site-08  python ci_suite.py (branch, serial, fresh temp DEALFLOW_DIR)  CI SUMMARY: 280 pass / 10 fail, 39 skipped; 9 fails are the known-baseline suites, 10th is _boardmanualheldtest (B1's test, EXPECTED red: B1's blocked edits are not applied; not a B2 regression, B1 not judged here)
20:11  QA PASS  F-site-08  20 suites alone on base and branch, PYTHONUTF8 off and on (the 9 baseline fails, _clienttest _equityboardtest _mirrortest _footprinttest _navtest _boardurltest _callequitytest _suppressiontest _optouttest _dnctest)  same rc and same failing lines on both trees in every mode; _clienttest/_footprinttest/_navtest/_boardurltest/_callequitytest rc=0 both; _equityboardtest/_mirrortest/_optouttest/_dnctest fail on both trees on missing Playwright Chromium (not evidence); _suppressiontest same 2 lines (17/19)
20:12  QA PASS  F-site-08  diff checklist  PR title has no LEGAL REVIEW and none needed (no send/stay/DNC/opt-out/copy token in the diff); Merge: ready for Alex; "Merged is not visible: visible after the next publish."; Exposure none honest; diff vs predecessor = 4 files; 3 template hunks (@@5313 _callSheet top, @@5619 _csDiligence Money line, @@19068 render desktop row prof cell), none in _eqcChip/_EQC_NAME/_eqCeiling/_liensVerified; no hunk in equity bake files, call_mode.py, docs/index.html, docs/call/; 2 removed template lines, both the two rewritten display lines, no number changed; PII scan 0 findings; no seat/WP/balloon token
20:12  NOTE  F-site-08  read: ceiling wording gated on r.eqc && may_quote !== true (no-eqc lead unchanged, test d); equity_state sets may_quote only for fact states, so chip and Money line never disagree; desktop row is built only under !_mob and the mobile card is separate, so one chip per width; _clienttest still passes and the "+who+clientBan+" return line is untouched. Not run: skipped browser suites, a mobile-device look, clipboard "Copy all numbers" text (fixer reports it still has no quote label: residual, outside ticket)

### F-site-08 QA: do-not-quote equity chip on the desktop row and the call sheet
- Task: QA gate for PR #453 (S2, Merge: ready for Alex), stacked on B1 (not judged)
- Owner: gate; product owner board-dev
- Branch + PR: claude/agents-fix-f-site-08 @ d722a8bf
- Files changed: none by gate
- What changed (plain English): nothing; verification. Desktop row and call sheet now carry the existing equity chip, and the call sheet Money line reads "at most $X, liens not verified" when the equity card says do not quote. No number changes.
- Tests run (command + result): _boardeqcchiptest 19/0 on branch, 19/8 on predecessor template; ci_suite 280 pass/10 fail/39 skipped, only the expected B1 test is new red; 20 suites compared on both trees, same lines.
- QA verdict (PASS/FAIL + by whom): PASS by gate (B2 own diff only)
- Exposure: none
- Flags (default off?): none
- Owned-surface bugs found: none
- Needs Alex (one line each): merge after B1 is resolved; B1's three blocked edits still wait on Alex
- Decisions needed (link to the DECISIONS-NEEDED entry): none
- Next run should know: _boardmanualheldtest red here only because B1 is incomplete; four browser suites cannot run without Chromium

20:50  QA PASS  F-wire-02 (round 2)  python _notespushtest.py (branch f49faa2b, fresh worktree, temp DEALFLOW_DIR)  76 ok, ALL PASS
20:50  QA PASS  F-wire-02 (round 2)  same test beside git show 6b55b38d:call_mode.py in a scratch dir (failing-first for h-o)  FAILED: 42 (HTML body, text body, JSON with no ok, no body, throwing body: no strip, 1 post only)
20:51  QA PASS  F-wire-02 (round 2)  _callwiringtest _e2ewiringtest _carlostest _suppressiontest _equityroutetest _callstatstest, both trees, PYTHONUTF8 off and on  same rc and failing lines on both (only _suppressiontest rc=1, same 2 lines 17/19)
20:51  QA PASS  F-wire-02 (round 2)  python ci_suite.py (branch, serial, temp DEALFLOW_DIR)  CI SUMMARY: 280 pass / 9 fail, 39 skipped; the 9 re-run alone on base and branch both modes, same rc and same failing lines
20:52  QA PASS  F-wire-02 (round 2)  checklist  diff stat = exactly 3 files (_notespushtest.py, call_mode.py, DECISIONS-NEEDED.md); 4 call_mode hunks 6130-6201 all inside the notes-push block, 3 removed lines all in it; 0 seat/WP/balloon/DNC-function tokens; D24 0 removed lines, helper names now match code; PII scan 0 findings; PR #452 title has LEGAL REVIEW, body has Merge: Alex only, the visible-after-publish sentence, honest Exposure, carlostest now stated as passing; PR docs files no longer in the branch
20:52  NOTE  F-wire-02 (round 2)  read good(): fail-closed true only for 2xx + r.json function + object (not array) + ok===true; array, null, HTML, text, no ok, thrown clone/json, rejected json all false. send_server /notes success always returns {ok:true,...} (incl. saved:false refused case, DNC sweep runs from the payload), so a real success cannot be misread as failure; success path always clears the strip. One residual: in done(), localStorage.setItem('fcDncAck') runs before strip(''), so if storage is full or blocked a real success leaves the strip up (setItem throws, caught); saveNotes would already be showing NOT SAVED then. Minor, non-blocking. D21 limit unchanged (server says ok:true when its own DNC sweep fails)

21:30  QA PASS  F-site-08 (PR #455, 7afa1bf2)  git diff --stat origin/main...HEAD  exactly 2 files (_boardeqcchiptest.py, tracker_template.html; 11 lines); nothing under docs/; 3 hunks (@@5304 _callSheet top, @@5612 _csDiligence Money line, @@19064 render desktop prof cell); grep _boardWrittenHeld in the tree = 0; added/removed lines of the template diff are byte-identical (same md5) to the earlier PASSed d722a8bf diff against the B1 head; test file identical to d722a8bf
21:30  QA PASS  F-site-08 (PR #455)  python _boardeqcchiptest.py, PYTHONUTF8 off and on  19 checks, 0 failed (both); with BOARDEQC_TEMPLATE = origin/main template: 19 checks, 8 failed (a x2, b x4, c x2)
21:31  QA PASS  F-site-08 (PR #455)  python ci_suite.py (branch, serial, fresh temp DEALFLOW_DIR)  CI SUMMARY: 280 pass / 9 fail, 39 skipped; the 9 are the known baseline suites only (no B1 test on this branch)
21:31  QA PASS  F-site-08 (PR #455)  19 suites alone on base and branch, PYTHONUTF8 off and on (9 baseline fails + _clienttest _equityboardtest _mirrortest _footprinttest _navtest _boardurltest _callequitytest _suppressiontest _optouttest _dnctest)  0 mismatches of rc or failing lines; _equityboardtest _mirrortest _optouttest _dnctest red on both on missing Playwright Chromium (not evidence); _suppressiontest same 2 lines (17/19)
21:32  QA PASS  F-site-08 (PR #455)  gh pr view 455 + scans  base main, draft true, title has no LEGAL REVIEW (none needed), body: Merge: ready for Alex, "Merged is not visible: visible after the next publish.", Exposure none honest, residual clipboard text named (F-b2-01); PII scan of added lines 0 findings; name-only diff has no report-only file; no seat/WP/balloon/DNC/send token in the template hunks. Not run: 39 CI-skipped browser suites, a phone look, the clipboard text

### F-site-08 re-pass QA (PR #455)
- Task: QA gate, F-site-08 re-based onto main (PR #455 replaces #453)
- Owner: gate; product owner board-dev
- Branch + PR: claude/agents-fix-f-site-08b @ 7afa1bf2
- Files changed: none by gate
- What changed (plain English): nothing; verification. Same three display hunks as the earlier PASS, now alone on main.
- Tests run (command + result): _boardeqcchiptest 19/0 (and 19/8 on main's template); ci_suite 280 pass/9 fail/39 skipped, baseline only; 19 suites compared on both trees, 0 mismatches
- QA verdict (PASS/FAIL + by whom): PASS by gate
- Exposure: none
- Flags (default off?): none
- Owned-surface bugs found: none
- Needs Alex (one line each): merge decision (ready for Alex)
- Decisions needed (link to the DECISIONS-NEEDED entry): none
- Next run should know: four browser suites cannot run here without Chromium; clipboard Copy-all-numbers text still unlabeled (F-b2-01)

22:40  QA PASS  F-site-05 (PR #456, ac4ebdb1)  git diff --stat origin/main...HEAD  exactly 3 files: tracker_template.html (62 lines), _commentpiitest.py (new), design-preview.html; nothing under docs/; one commit
22:40  QA PASS  F-site-05 (PR #456)  scratchpad comment-stripper (quote and template-literal aware) on origin/main vs branch blobs  non-comment nonblank lines 15606 vs 15606, differing non-comment groups 0; 19 hunks / 31 changed lines, all 31 inside comments per the stripper (23 start a comment line, 8 are continuation lines of block comments at 2633, 6720-6721, 16695, 16697, 16968-16970 whose opener and closer lines are identical in both files); counts of /* */ <!-- --> backtick ${ identical in both files (381/388, 7/7, 572, 311)
22:41  QA PASS  F-site-05 (PR #456)  excluded places  no changed line inside the comment run above function _optedOutIdentities (main L3647-3661) and none within 40 lines of the Playbook intel block / pb-intel lines (6084-6086); the diff name list holds none of the excluded files (badnum_audit, footprint, the publish script, replies, _stoptexttest, docs/index.html, docs/call/, the send bridge, call_mode)
22:42  QA PASS  F-site-05 (PR #456)  python _commentpiitest.py on the branch, PYTHONUTF8 off and on  ALL PASS (both); same test beside origin/main's template (PYTHONPATH=branch so the company constants import)  4 failed: (a) 1 line, (b) 2 lines, (c) 7 lines, (d) 17 lines, exactly the fixer's families; independent scan with different regexes: 0 consumer mailboxes, 0 non-555 non-trivial phones in branch comments (main: 2), addresses 18 -> 1 line outside the invented prefix (L18728, untouched by the diff, ZIP-like shape, not flagged by the test's rule)
22:43  QA PASS  F-site-05 (PR #456)  test file scan  company public lines are imported (outreach_copy.PHONE, bsg_cards.PHONE, outreach_mail.CALLBACK_PHONE), none typed; one non-555 phone shape (L209), one consumer mailbox shape (L216), one address shape outside the invented 100 NW 1 prefix (L214), one case id with year 26 (L209): all negative controls; none of the phone, mailbox or address controls occurs in any tracked file at origin/main (git grep 0), so none is copied from a removed value
22:44  QA PASS  F-site-05 (PR #456)  build_preview.py --out <scratch> in a git-archive copy of the branch (temp DEALFLOW_DIR, no network)  wrote, no refusal; differs from the committed design-preview.html in exactly 1 line (the "baked" timestamp); preview diff vs main +498/-99; consumer-mailbox shapes 4 -> 0; ADDED lines: 0 non-555 phones, 0 mailboxes, 0 non-invented addresses, 0 non-2099/99 case ids; REMOVED lines carry 3 phones, 4 mailboxes, 6 case ids, 21 addresses
22:45  QA PASS  F-site-05 (PR #456)  python ci_suite.py (branch, serial, fresh temp DEALFLOW_DIR)  CI SUMMARY: 280 pass / 9 fail, 39 skipped; the 9 are the known baseline suites; _commentpiitest and _footprinttest PASS in CI
22:46  QA PASS  F-site-05 (PR #456)  25 suites alone on base and branch, PYTHONUTF8 off and on (9 baseline fails + _footprinttest _navtest _boardurltest _clienttest _suppressiontest _optouttest _dnctest _mirrortest _e2ewiringtest _autorunstoptest _sendstaytest _docroomtest _redfintest _balloonlanetest _textprecheckstest)  same rc and failing lines everywhere; the one raw difference (_uifixturetest, UTF8 off) is the same UnicodeDecodeError at a different byte offset because the template shrank; _footprinttest 46 passed, 0 failed (both modes)
22:47  QA PASS  F-site-05 (PR #456)  gh pr view 456 + body scan  base main, draft true, Merge: ready for Alex, "Merged is not visible: visible after the next publish.", Exposure honest (values stay in public history and the live page until rebuild and publish; scrub = Alex D-G a), tests-not-run list present, no LEGAL REVIEW and none needed (no send/stay/DNC/opt-out/copy code touched, comments only); body scan 0 phones, 0 mailboxes, 1 street shape = the invented prefix; states #454 is superseded
22:47  NOTE  F-site-05 (PR #456)  read: replacement classes are 555 phones, 2099/99 case ids, the 100 NW 1 invented address prefix; none of the 24 removed phone/case/address tokens survives in a changed comment; 2 removed address tokens still exist OUTSIDE comments at L4873 and L6087 (6087 = excluded Playbook intel block paragraph, 4873 = page text or code); 14 lines hold non-555 phone shapes in non-comment text (L3948, 6093, 9507, 9509, 9520, 9521, 10213, 12189-12190, 12307-12308, 12316-12318) plus address shapes at L2368, 18728, 18826, 20095: all outside this ticket (comments only), untouched, reported for chief/Alex. The test guards comments only. Not run: 39 CI-skipped browser suites, any live page

### F-site-05 QA: remove personal values from template comments (PR #456)
- Task: QA gate for F-site-05 + F-site-06 L2633 + F-sec-08 comments (S4, Merge: ready for Alex)
- Owner: gate; product owner board-dev
- Branch + PR: claude/agents-fix-f-site-05b @ ac4ebdb1
- Files changed: none by gate
- What changed (plain English): nothing; verification. 31 comment lines in the board template lost real-looking phones, case ids, addresses and one mailbox; no code changed; design-preview regenerated.
- Tests run (command + result): comment-stripped diff 0 code lines differ; _commentpiitest ALL PASS (main template: 4 families fail); build_preview regenerates the committed file except 1 timestamp line; ci_suite 280/9/39 baseline only; 25 suites compared, same lines
- QA verdict (PASS/FAIL + by whom): PASS by gate
- Exposure: none to homeowners (comments only); values remain in public history and live page until publish
- Flags (default off?): none
- Owned-surface bugs found: none touched (comment block above _optedOutIdentities untouched, D-G b)
- Needs Alex (one line each): merge decision; history scrub (D-G a); non-comment residual shapes at the lines named in the NOTE line
- Decisions needed (link to the DECISIONS-NEEDED entry): D-G a, b, c (existing)
- Next run should know: _commentpiitest guards comments only; non-comment residuals listed above are unowned
