# worklog-ideas (role: ideas-scout), run 2026-11-08, baseline origin/main @ aac7a8a9, worktree %USERPROFILE%\projects\fl-loop-ideas (detached, read-only, never edited)

Counts and case ids only. Fixtures are invented. Temp DEALFLOW_DIR for every run, no network, no bridge, no .bat, no paid call.
Proof scripts are copied to `RUNDIR\ideas-proofs\` (run each from the worktree root, e.g.
`cd %USERPROFILE%\projects\fl-loop-ideas && python -I %USERPROFILE%\DEALFLOW\agent-logs\2026-11-08\ideas-proofs\<name>.py`).

MESSAGING NOTE: SendMessage is not in this agent's tool list. Chief reads this file directly (pull model, per the task). Every ticket is written in full here, as it was produced.

## Step log

```
17:50 READ   CLAUDE.md (sections touched), OPERATING-RULES-2026-11-07, AUDIT-LOOP-PROTOCOL, PATTERNS, OWNERS, OUTPUT-TEMPLATE, ideas-scout.md, PLAYBOOK.md, BOARD.md, worklog-site / -wire / -sec
17:58 READ   funnel source: %USERPROFILE%\DEALFLOW\DealFlow-Scorecard\2026-09-15_scorecard.json (counts only; it also holds reply addresses, NOT copied here). It is the newest scorecard on disk, 54 days old, from the machine that died 2026-11-05.
18:00 RAN    ideas-proofs\reply_probe.py (site-qa board fixture, Chrome channel, all traffic refused) -> I-01 evidence and a side finding (see "Note to chief")
18:03 RAN    ideas-proofs\baseline_probe.py + tb_probe.py (9 of the 14 suites the board lists as failing on main, serial, temp DEALFLOW_DIR) -> I-02
18:05 RAN    ideas-proofs\notvisible_probe.py (read-only git) -> I-03
18:06 RAN    python -I -c "import outcome_learn ... signals(...)" -> I-04
18:07 RAN    ideas-proofs\pii_ratchet_probe.py (counts only) -> I-05; Grep count on tracker_template.html -> I-06
18:08 RAN    ideas-proofs\browser_probe.py and `python -m unittest discover -s ui_tests` -> I-07
18:09 GUARD  agent_guard BLOCKED two of my own Bash commands: one whose script text named the follow-ups env var (a harmless env.pop line), one whose grep text named the batch-file glob. Did the allowed thing both times: rewrote the script without the line / used the Grep tool. No reword of a blocked command.
18:09 WROTE  this file, 7 tickets (I-01 .. I-07); 1 note to chief; the "for Alex / attorney" list
```

Funnel numbers used (source: 2026-09-15_scorecard.json, the last scorecard the old machine wrote; the board's own dials/convos
tiles are not readable offline): leads 2,270, with a phone 710, with an email 668, unreachable 1,554; calls ever 354, calls in 7 days
39 on 3 dial days; conversations ever 30 (8.5% of calls), in 7 days 1; appointments 6 (20% of conversations), offers 1, contracts 1;
status "Wrong number" 23 and "Called - no answer" 48; replies 9, hot replies 7, replies waiting past SLA 3 (38 to 39 days each). The analyst status
file next to it (DEALFLOW-ANALYST-STATUS.txt, same minute) says "5 reply(ies) STILL UNANSWERED, oldest 43d". These are 54 days old: read them as
the shape of the funnel, not today's count. The counter named in each ticket would measure today.

---

### I-01  Reply inbox: answered replies leave the badge, unanswered ones sort first and show how many days they have waited
- Type: IDEA
- Area: board
- Problem evidence:
  - Funnel (scorecard 2026-09-15): 9 replies, 7 hot, 3 past SLA at 38 to 39 days; analyst status: "5 STILL UNANSWERED, oldest 43d". A reply is the only warm lead the system produces; 20% of logged conversations became appointments (6 of 30), so a reply waiting 5 weeks is the most expensive miss in the funnel.
  - The board already knows the answer: `_replyWaiting(r)` (tracker_template.html:12421-12446) returns `ageDays` and returns null once any later call / text / email / letter / door or a bridge send exists. Only the Worker reads it (`repAge`, line 13449). The Reply inbox does not: `_replyList()` (12464) uses `_isReplied`, so answered replies stay, and it sorts STOP first, then soonest sale. The header badge (18874-18877) is `_replyList().length`.
  - Re-run (fixture, 4 replies: 1 STOP, 1 waiting 30 days, 1 waiting 3 days, 1 already answered): `python -I RUNDIR\ideas-proofs\reply_probe.py` ->
    ```
    inbox order (label, stop, waiting-age-days or null=answered/stop): [('stay', True, None), ('control', False, 29), ('noquote', False, None), ('quoteok', False, 2)]
    header badge text: '4'
    cards: 4 | the word "waiting" in cards: 0 | Call buttons: 4 | mailto links: 4
    ```
    Badge 4 for 2 owners actually waiting. No card says how long. The only chip is "20 days to sale", the same on all four.
- Proposed fix (smallest): display only, in `tracker_template.html`. (1) In `genReplyInbox` after the STOP block, sort non-STOP replies by `_replyWaiting` age descending (answered ones last, dimmed with "answered"), keep STOP first exactly as is. (2) On each unanswered card add a chip "waiting N d" from `_replyWaiting(r).ageDays`. (3) Header badge counts STOP + unanswered, not answered. Test: new `_replyinboxtest.py` (or a case in the board fixture suite) with the four notes above; assert order, chip text, badge 3 (1 STOP + 2 waiting). Do NOT touch `_isReplied`, the STOP ordering, `_laneStats` or any suppression function.
- Repro: `python -I RUNDIR\ideas-proofs\reply_probe.py` from the worktree (needs the site-qa fixture at RUNDIR\site-work, already built; Chrome channel, no download).
- Expected effect: guess: a reply stops sitting more than 2 days because the oldest one is on top with its age. Today the 5-week-old replies were visible in the list but looked the same as a fresh one.
- Counter that would measure it: `reply_sla_violations` (count) and `replies_hot` in the weekly analyst scorecard (analyst.py), plus the median age of `_replyWaiting` rows; target 0 violations at each Monday run.
- Effort: S
- $ cost: 0
- Owner role (OWNERS.md): board-dev (tracker_template.html; none of the owned suppression functions)
- Touches: none (display and ordering only; no send, no stay, no DNC change). Same file and nearby lines as F-site-01/02, so sequence it after that PR (one board-dev fixer, rebase).
- Confidence: high on the mechanism and the re-run; medium on the effect size (54-day-old funnel, 3 to 5 replies).

### I-02  Make "failing on main" true: 4 test-side encoding bugs, one missing tzdata, then the baseline drops from 14
- Type: IDEA
- Area: process
- Problem evidence:
  - BOARD header: 14 suites fail on main (274 pass / 14 fail / 39 skipped). Every fixer and the gate is judged against that list, so a wrong list makes real regressions look old and old noise look new.
  - Re-run of 9 of the 14, serial, temp DEALFLOW_DIR, default locale vs `PYTHONUTF8=1` (`python -I RUNDIR\ideas-proofs\baseline_probe.py`):
    ```
    _ballooncurrencytest      default rc=1 (UnicodeDecodeError cp1252)   utf8 rc=0 OK
    _texttpltest              default rc=1 (FAIL sale tomorrow says tomorrow)   utf8 rc=0 PASSED, 74 checks
    _workerrunlogtest         default rc=1 (UnicodeDecodeError)   utf8 rc=0
    _callworkflowparitytest   default rc=1 (FAIL canonical bytes match JS)   utf8 rc=0 ALL PASS
    _uifixturetest            default rc=1 (UnicodeDecodeError)   utf8 rc=0 OK
    _bouncefollowuptest       default rc=0 in 4.5 s (listed as failing; serial run is green)
    _alertstest               rc=1 both ways: ModuleNotFoundError: No module named 'tzdata' (ZoneInfo America/New_York)
    _judgmentpasstest         rc=1 both ways: NotImplementedError PosixPath (known test bug, tests/AGENT-QA-REPORT.md)
    _resimplisynctest         rc=1 both ways: 433 passed, 1 failed
    ```
  - Where the decode errors sit (`python -I RUNDIR\ideas-proofs\tb_probe.py`, repo frames only): `_ballooncurrencytest.py:10` (tax_merge) and `:25`, `_workerrunlogtest.py:71`, `_uifixturetest.py:12` (`board.read_text()` with no encoding; `ui_fixture.py:32` already carries the comment explaining why it writes utf-8). `_callworkflowparitytest.py:45` pipes `json.dumps(...)` to node through `subprocess.run(..., text=True)` with no encoding. All five are in the TEST files, not in product code. `_texttpltest.py` has no failing frame; its cause is not shown.
  - tests/AGENT-QA-REPORT.md (2026-10-29) already says "cured by PYTHONUTF8=1" for the same suites and the gate runs with it, but `ci_suite.py:76` still runs children with only `PYTHONIOENCODING`, so the raw baseline stays red on a Windows box and every new machine, including the new laptop.
  - Correction to P-site-11: `_uifixturetest.py` is "no browser" by its own docstring. It fails on cp1252, not on a missing Chromium.
- Proposed fix (smallest): one PR, no product code. (a) add `encoding='utf-8'` to the four test lines above and to the `subprocess.run` at `_callworkflowparitytest.py:45` (and `:83`); (b) put `tzdata; platform_system=="Windows"` in requirements.txt, or make `_alertstest.py` skip with the reason printed when ZoneInfo cannot load; (c) leave `ci_suite.py` alone, so the suites still run in the locale production runs in (setting PYTHONUTF8 there would hide a real default-encoding bug in product code; today none is shown, but the suite is the only thing that would show one). Test: `python ci_suite.py` before and after; the "CI SUMMARY ... fail" count is the proof.
- Repro: `python -I RUNDIR\ideas-proofs\baseline_probe.py` and `tb_probe.py` from the worktree root.
- Expected effect: measured here: 4 of the 14 go green from (a) alone (_ballooncurrencytest, _workerrunlogtest, _uifixturetest, _callworkflowparitytest), 1 more (_alertstest) from (b), so 14 -> 9 at most; `_texttpltest` still needs its cause read. guess: each later QA verdict stops spending a "same as main?" look on ~5 suites.
- Counter that would measure it: the `CI SUMMARY: N pass / M fail` line from `python ci_suite.py` on main (today 274 / 14).
- Effort: S
- $ cost: 0 (tzdata is the PSF-maintained zone-data wheel; Alex can say no and take the skip option)
- Owner role (OWNERS.md): fixer with the named test files (qa-gate edits no product code and owns only the QA report and `_e2ewiringtest.py`); `requirements.txt` and `ci_suite.py` have no owner row, so chief names the role.
- Touches: none
- Confidence: high on (a) (the failing frame is a test line and the re-run is green), medium on (b) (one suite, not yet applied).

### I-03  `reports/not_visible_yet.py`: one command that lists what is merged but not on the phones or the board
- Type: IDEA
- Area: process
- Problem evidence:
  - OPERATING-RULES rule 6: never say a merge is live. worklog-wire had to build the "Merged is not visible" table by hand: live pages last built `42a48ea1` (2026-11-04 21:30 ET), main at `aac7a8a9`, 7 commit groups that change what a rebuild produces.
  - Prototype (read-only git, `python -I RUNDIR\ideas-proofs\notvisible_probe.py`):
    ```
    last commit touching a live page: 42a48ea1 2026-11-04T21:30:53-05:00
    commits on main since then: 18 | of those touching page-source files: 7
    per source file (commits): {'tracker_template.html': 3, 'call_mode.py': 5, 'dealflow_nav.py': 0, 'outreach_copy.py': 1, 'disclaimer.py': 0}
    lines changed in page-source files since the build: 3 files changed, 425 insertions(+), 35 deletions(-)
    ```
    It reproduces wire's 7 without any hand work.
  - `publish_watch.py` answers "is the live board current?" but reads the network (blobless clone, range requests) and gives no list; it is a watchdog with exit codes, a poor place to add this.
- Proposed fix (smallest): new `reports/not_visible_yet.py`, local git only: find the last commit that touched `docs/index.html`, `docs/call/index.html` or `docs/call/carlos/index.html`; print the date, commit count, and one line per commit that touched a page-source file (short hash, date, subject, files), using a constant list (tracker_template.html, call_mode.py, dealflow_nav.py, outreach_copy.py, disclaimer.py, foreclosure_leads.py, equity files). Ends with the sentence "Merged, visible after the next publish." Exit 0 always. Test: new `_notvisibletest.py` builds a temp git repo with invented commits (one page-source, one docs-only) and asserts the list. No network, no write, no publish, no counter file.
- Repro: `python -I RUNDIR\ideas-proofs\notvisible_probe.py`.
- Expected effect: guess: saves the ~20 minutes a hand audit takes each round and removes the one wording mistake rule 6 exists for.
- Counter that would measure it: none automatic; chief or main notes at the next run whether the hand table was needed (0 expected).
- Effort: S
- $ cost: 0
- Owner role (OWNERS.md): board-dev (owns `reports/`)
- Touches: none
- Confidence: high that it works (prototype ran); low on how much Alex will use it.

### I-04  Add "hour of day" and "weekday" to outcome_learn so the next dial windows are chosen from the data
- Type: IDEA
- Area: playbook
- Problem evidence:
  - Funnel (scorecard 2026-09-15): 354 calls ever, 30 conversations (8.5%), 39 calls in the last 7 days over 3 dial days. Calls are the scarce input, and the legal window is only 12 hours (8 AM to 8 PM ET). Nothing in the repo tells the dialer which hours answer.
  - `outcome_learn.signals()` has 11 signals and none is time: `python -I -c "import outcome_learn as O; print(sorted(O.signals(...)))"` ->
    ```
    11 signals: ['caller', 'county', 'days to sale', 'equity %', 'equity state', 'homestead', 'phone position', 'phone source', 'phone type', 'stage', 'tier']
    hour-or-weekday signal present: False
    MIN_TRUST 20
    ```
  - The data already exists on every outcome row: `call_mode.py:10073` writes `tsu: Date.now()` (ms) on each dial row, so no new field and no new homeowner data. (`ts` is `toLocaleString()`, device-locale text, do not parse it.)
- Proposed fix (smallest): in `outcome_learn.signals()` add two keys, `hour (ET)` in four buckets (8-11, 11-14, 14-17, 17-20, else "outside") and `weekday` (Mon..Sun), computed from `d['tsu']`; rows without `tsu` land in "unknown". Existing `(thin)` flag at MIN_TRUST = 20 stays. Convert to Eastern by a fixed rule (UTC-4 between the second Sunday of March and the first Sunday of November, else UTC-5) or `ZoneInfo` only if tzdata is present (see I-02: this box has none). Test: extend `_outcomelearntest.py` with a fixture of invented dial rows at known timestamps; assert bucket counts.
- Repro: the `python -I -c` above (read-only; it imports outcome_learn with a temp DEALFLOW_DIR).
- Expected effect: guess: when the table has 20+ dials per bucket (about 2 to 3 more weeks at the 2026-09 pace of 13 dials per dial day, faster if volume rises), it shows whether mornings or evenings answer more; a re-aimed window could lift answers, size unknown. Until then the buckets print "(thin)" and say nothing, which is the honest state.
- Counter that would measure it: the new rows of `python outcome_learn.py --days 30` (convo% per bucket, with the existing thin flag); `call_stats.py` `answer_rate` before and after a change of dial hours.
- Effort: S
- $ cost: 0
- Owner role (OWNERS.md): board-dev (owns outcome_learn.py)
- Touches: none (read-only report; counts only; no new data stored)
- Confidence: medium (the code change is small and safe; whether 354 dials is enough to see an hour effect is doubtful).

### I-05  Source ratchet test: no new real-looking phone or consumer mailbox in tracked source (counts per file, no values stored)
- Type: IDEA
- Area: engine
- Problem evidence:
  - F-site-05 (a real-looking number in a comment at tracker_template.html:15316, in 4 tracked files), F-site-06, F-sec-08, F-sec-11 (133 phone-shaped values; design-preview.html still has consumer mailboxes after the 2026-10-25 cleanup). `footprint.assert_clean` checks only finished pages and local-config values, never tracked source.
  - Shape scan of tracked text files (`python -I RUNDIR\ideas-proofs\pii_ratchet_probe.py`, prints counts and file names only):
    ```
    tracked files scanned (text kinds, excl docs/img): 818 skipped 1
    consumer-mailbox-shaped, non-placeholder: 45 hits in 21 files   (top: _deliverabilitytest.py 13, _firsttouchhinttest.py 4, _workerreachtest.py 4 ...)
    real-looking local-area-code phone shapes (555, repeating and sequential excluded): 316 hits in 21 files   (top: listing_status_cache.json 193, _phonesrctest.py 43, _autorunstoptest.py 31 ...)
    hits in files that reach the PUBLIC site (docs/ or design-preview.html): ['design-preview.html']
    tracker_template.html: 3 phone hits (one is the F-site-05 comment)
    ```
    Most are test fixtures (sec's earlier read agrees). The ones that matter are in the files that build public pages.
- Proposed fix (smallest): new `_trackedsourceshapetest.py`, no change to `footprint.py`. Two rules: (1) strict, zero tolerance, for the page-building files (tracker_template.html, call_mode.py, dealflow_nav.py, outreach_copy.py, hardmoney_balloon.py, bsg-site/, any `docs/**/*.html`): any consumer mailbox or non-555 local-area-code phone fails the test; (2) ratchet for everything else: a dict of per-file counts (numbers only, no values) may only go down. Today rule 1 fails on tracker_template.html until the approved F-site-05 comment fix lands, so land this test in the same train or after it. Test proof: the test itself, plus one negative case that feeds it a temp file with an invented `<email>`-style mailbox and a 555 number and expects a pass, then a 305 number and expects a fail. The test file and the baseline carry no real value (CLAUDE.md: a regex that matches a real address is the same leak; these are generic shape regexes).
- Repro: `python -I RUNDIR\ideas-proofs\pii_ratchet_probe.py`.
- Expected effect: guess: the next "real number in a comment" is caught by `python ci_suite.py` the day it is written instead of by an audit weeks after it is public; the 45 + 316 baseline can only shrink.
- Counter that would measure it: the per-file baseline dict in the test (sum must trend to the fixtures-only floor); `git grep -c` on tracker_template.html = 0 after the F-site-05 fix.
- Effort: S
- $ cost: 0
- Owner role (OWNERS.md): anyone may add a new `_<name>test.py` (fixer or qa-gate); `footprint.py` is untouched
- Touches: none
- Confidence: high that it runs; medium that the phone shape has few false positives (the probe excluded 555, repeating and sequential digits; chief can read the top-8 list).

### I-06  Ratchet test for the inline-handler pattern behind F-sec-09: the 25 sites may not grow while the fix waits in the backlog
- Type: IDEA
- Area: board
- Problem evidence: F-sec-09 (backlog, first in line, fix verified; escJs at 25 sites). The pattern `onclick="fn('"+esc(x)+"')"` is how all 25 got there, and the board is a hot file many sessions edit, so the backlog item can grow while it waits. Count now: `Grep` of the pattern `\''+esc(...)+'\'` on tracker_template.html -> 25 lines; on call_mode.py -> 0.
- Proposed fix (smallest): new `_inlinehandlerratchettest.py`: read tracker_template.html and call_mode.py, count lines matching `\\''\s*\+\s*esc\(`, assert the count is `<= 25` for tracker_template.html and `== 0` for call_mode.py, with a failure message naming the F-sec-09 fix ("use escJs, not esc, inside a quoted JS string"). When the F-sec-09 fix lands, the fixer sets the limit to 0 in the same PR. No product edit, so it can ship while the cap is full.
- Repro: Grep tool, pattern `\\'\s*'\s*\+\s*esc\(` on both files (25 and 0).
- Expected effect: guess: no 26th site is added before the fix; it also hands the F-sec-09 fixer its own regression check.
- Counter that would measure it: the count the test asserts (25 -> 0).
- Effort: S
- $ cost: 0
- Owner role (OWNERS.md): anyone may add a new test (fixer / qa-gate)
- Touches: none
- Confidence: high on the count; low-medium on value, it only guards a low-likelihood issue (F-sec-09 is S4, low likelihood). Fold it into F-sec-09's PR if chief prefers.

### I-07  Browser suites cannot start on this machine: 5 UI test classes error in setUpClass, 0 tests run; a 6-second path with system Chrome already exists
- Type: IDEA
- Area: process
- Problem evidence:
  - `python -m unittest discover -s ui_tests` (temp DEALFLOW_DIR, PYTHONUTF8=1): `Ran 0 tests ... FAILED (errors=5)`, every error `BrowserType.launch: Executable doesn't exist at ...chromium_headless_shell...` (5 classes: NumbersCardUI, OperationsUI, PersonUnitsUI, WorkerStopRejectionUI, WhitepagesEvidenceUI). So none of the UI contract tests count here, and the wire role's `_callpickuptest.py` browser half is a SKIP with rc 0.
  - A path already exists: `python -I RUNDIR\ideas-proofs\browser_probe.py` ->
    ```
    no PW_CHROMIUM             rc=0 1s | ... SKIP browser half (Chromium will not launch ...)
    PW_CHROMIUM=system Chrome  rc=0 6s | ... ok: pickup tap -> talk time
    ```
    `_callpickuptest.py:55,137` honours `PW_CHROMIUM`; the ui_tests do not (`ui_tests/test_operations.py:33`, `test_numbers_card.py:52` call a bare `chromium.launch(headless=True)`). 8 root-level suites already pass `channel=` or `executable_path` (`_callchipcompletetest`, `_calldncpastetest`, `_calllayouttest`, `_callpickuptest`, `_callrelativetest`, `_callwppastetest`, `_lookuplanetest`, `_workerreachtest`); 34 others launch bare.
  - The audit's own harness (site-qa) used `channel='chrome'` the whole run, so it works here.
- Proposed fix (smallest): new `ui_tests/_browser.py` with `launch(p)`: try bundled Chromium, then `PW_CHROMIUM`, then `channel='chrome'`, then `channel='msedge'`; on failure raise `unittest.SkipTest("no browser: set PW_CHROMIUM or run python -m playwright install chromium")` so a missing browser is a named skip, not 5 errors. Use it at the two launch lines (the other 3 classes inherit the setup). One sentence in UI-TESTS.md. Do not change `ci_suite.py` or the 34 root suites in this PR. Test: the ui_tests themselves on this box (expect tests to run) and, with the browser hidden (`PW_CHROMIUM` pointing nowhere and no Chrome), a named skip.
- Repro: both commands above.
- Expected effect: measured: 0 UI tests run here today; guess: all five classes run in about a minute on any box that has Chrome or Edge, which includes the new laptop. Whether they pass is not known (not run past setup).
- Counter that would measure it: `python -m unittest discover -s ui_tests` -> "Ran N tests" with N > 0 (today 0).
- Effort: S
- $ cost: 0 (no download; uses the Chrome already installed; the optional Chromium download stays Alex's call, P-site-11)
- Owner role (OWNERS.md): no owner row for `ui_tests/`; fixer with the named files, or qa-gate (tests only, no product code)
- Touches: none
- Confidence: high on the failure and the working path; medium that the 5 classes then pass.

---

## Note to chief (not an idea ticket; chief may open it as a finding)

Reply inbox cards offer Call and mailto on STOP and held leads. The same re-run as I-01 (`reply_probe.py`):
```
card STOP   | has Call button: True | has mailto: True
card normal | has Call button: True | has mailto: True   (x3)
```
Code: tracker_template.html:12483-12497 builds `ph` from `phdnc` only and `em` from `emails[0]` unfiltered; no `_boardMailHeld`, no `_textContactBlocked`, no `saleBkAct` check. The STOP banner right above says "Nothing more to do" while a green Call button sits under it. The fixture's STOP lead also carried an active-stay flag (`saleBkAct`), so a held lead shows contact controls in this view too. This is the same class as F-site-01/F-site-02 (a surface that skips the board's own hold verdict), a third surface they do not list. Suggest folding it into the F-site-01 helper's scope (same file, same helper) rather than waiting. It is S1-class by the protocol table ("could contact someone who said stop"). Evidence is a Chrome run on invented rows, nothing sent.

## For Alex / attorney (not proposed to build)

1. DNC taps made during the freeze. F-wire-02 says a DNC tapped on a phone reaches `optouts.json` only through the bridge or a board tab open on the bridge machine; the bridge is frozen and the old laptop is gone. Before "go" arms any send, someone should compare the DNC / opt-out marks in the phones' and board's notes (team sync) with the ledger. That is the suppression surface: Alex's or the owner's call, report-only for agents.
2. Reply-inbox STOP / held cards should show no Call or mailto (Note to chief). Display of a suppression verdict: legal wording question for the attorney if it changes the card text; the code half belongs with F-site-01.
3. Whether a reply that has waited more than N days should trigger anything automatic (a reminder text, a call card) is homeowner contact and copy. Off limits. I-01 only shows the age.
4. "They have an attorney" as a stop (D-A, already filed as F-wire-01).
5. `ci_suite.py` could run children with `PYTHONUTF8=1` (the QA report's own gate rule does). I did not propose it as a build because it could hide a default-encoding bug in product code on a Windows scheduled task; Alex decides whether he prefers a truthful locale or a green baseline.

## OUTPUT-TEMPLATE

```
### ideas-2026-11-08: idea tickets for the next two weeks of dialing, from the approved findings and the funnel
- Task: up to 8 IDEA tickets in protocol format, grounded in approved findings, page evidence and the 2026-09-15 scorecard; no code
- Owner: ideas (ideas-scout), triage by chief
- Branch + PR: none (detached worktree %USERPROFILE%\projects\fl-loop-ideas @ aac7a8a9, nothing committed, nothing under docs/)
- Files changed: none in the repo. Run folder only: worklog-ideas.md, ideas-proofs\*.py (6 read-only probe scripts)
- What changed (plain English): nothing in the product. Seven ideas: (I-01) the Reply inbox shows how long each reply has waited and stops counting answered ones; (I-02) fix the five tests that fail only because of Windows text encoding or a missing time-zone package, so "failing on main" is true; (I-03) a one-command list of what is merged but not yet on the phones; (I-04) answer-rate by hour and weekday in the existing report; (I-05) a test that stops a new real-looking phone or personal email from entering source; (I-06) a test that stops the F-sec-09 pattern from growing; (I-07) let the UI tests use the Chrome that is already installed. One side finding for chief: the Reply inbox shows Call and mailto on STOP and held leads.
- Tests run (command + result): ideas-proofs\reply_probe.py -> badge 4 for 2 waiting, 0 "waiting" labels, STOP card has Call + mailto; baseline_probe.py / tb_probe.py -> 5 of 9 listed suites rc=0 with PYTHONUTF8=1, 1 rc=0 serial, 3 still red (tzdata, PosixPath, 1 of 434); notvisible_probe.py -> 18 commits since the last live build, 7 touching page source, matches wire's 7; outcome_learn.signals -> 11 signals, no time signal; pii_ratchet_probe.py -> 45 mailbox-shaped hits in 21 files, 316 phone-shaped in 21; Grep -> 25 inline sites; unittest discover -s ui_tests -> Ran 0 tests, 5 errors; browser_probe.py -> SKIP vs ok with PW_CHROMIUM. All with a temp DEALFLOW_DIR, no network, no bridge, no .bat. Not run: the other 5 listed-failing suites (bridge starts), python ci_suite.py (serial full run not needed for a read-only idea pass).
- QA verdict (PASS/FAIL + by whom): not applicable (no code change); ideas await chief triage
- Exposure: none from this pass (no send, no stay, no spend). I-01 touches the Reply inbox display near suppression readers but not the owned functions.
- Flags (default off?): none added; none touched
- Owned-surface bugs found: none in the owned functions. A neighbouring surface (Reply inbox cards, tracker_template.html:12483-12497) skips the hold verdict: reported in the Note to chief, not fixed.
- Needs Alex (one line each): (1) compare DNC marks made on phones/board during the freeze with optouts.json before "go" arms sends; (2) should a Reply-inbox STOP card hide Call and mailto (legal wording); (3) prefer a truthful default locale in ci_suite or a green UTF-8 baseline (I-02)
- Decisions needed (link to the DECISIONS-NEEDED entry): none written (this role writes nothing under docs/); the three lines above are the entries
- Next run should know: the funnel numbers on disk are 54 days old (2026-09-15 scorecard from the dead laptop) so every effect here is "guess:" until the new machine writes a scorecard; this python has no tzdata so any ZoneInfo code fails here; SendMessage was not available to this role, chief reads this file; the guard blocks a command whose text merely names the follow-ups env var or a batch-file glob, write the script with the Write tool and run it.
```
