LEGAL REVIEW: board: withhold Text, WhatsApp and mailto on held leads in the composer, call sheet and Reply inbox (F-site-01, F-site-02, F-ideas-01)

**STATUS: COMPLETE.** Three of the ticket's edits were refused by the agent_guard hook (their unchanged lines name a suppression function; the edits only add read-only calls to `_boardWrittenHeld`). Alex told the Lead to do the bypass edit on 2026-11-10, and the patch `fix-f-site-01.remaining-edits.patch` was applied with `git apply` in this worktree, not through the Edit tool. The guard setting `DEALFLOW_AGENT_UNLOCK` was not used. Review the diff of `tracker_template.html` as a change near the suppression surface.

## Summary (plain English)
The board has one answer to "this lead must not get an email" (`_boardMailHeld`). Three places ignored it for leads that are an existing client, under a diligence hold, claimed on another case, or whose owner is gone / transferred. The text composer enabled Send, the call sheet showed Text, WA and a mailto link, and the Reply inbox showed a Call button and a mailto on every card, STOP replies included. A new helper `_boardWrittenHeld(r)` returns that answer for those four holds only (never for opt-out, stay, wrong person or "sale passed") and the three places use it. Display is stricter only. The Call button on held-lead call sheets is unchanged (D-F, Alex's decision). No homeowner-facing text changed; the only new words are one operator-facing sentence in the composer ("On hold: <reason>. Do not text.").

All three places are now edited: `canTxt` in `_contactLineHtml` (removes Text and WA on the call sheet), `hardBlock` in `textCardHtml` (locks the composer) and the Reply inbox card. The F-b1-01 "marked dead" amendment is NOT in this change.

## Tests run
Failing first, on the unchanged tree (before any template edit), `PYTHONUTF8=1 python _boardmanualheldtest.py`:
```
46 checks, 24 failed
FAIL (a) composer client: no enabled Send, no WA, no tone buttons, no Copy  [{'send': 2, 'wa': 2, 'tones': 4, 'copy': 1, 'gate': []}]
FAIL (b) call sheet client: no Text, no WA, no mailto  [{'text': 2, 'wa': 2, 'mail': 1, 'call': 2}]
FAIL (c) inbox stopreply card: no Call button and no mailto, Open call sheet kept  [{'case': '2099-000908-CA-01', 'stop': True, 'call': 1, 'mail': 1, 'open': 1}]
```
(same three families failed for ddhold, sibclaimed, dead, transferred, optout, stay, stopreply; the control checks and the optout / stay composer checks passed.)

After the patch was applied to this branch, on the real template, with a temp `DEALFLOW_DIR` and `PYTHONUTF8=1`: `_boardmanualheldtest.py` 46 checks, 0 failed. `_boardaccuracytest.py` OK (0 failures), `_boardmergetest.py` ok, `_boardstagetest.py` ok.

Neighbouring suites, serial, temp `DEALFLOW_DIR`, with and without `PYTHONUTF8=1`, run on `fl-loop-base` and on this branch: `_suppressiontest.py` (17 of 19 checks; the two failing are a "lob ... path=None" check and the board check that needs the local access-code file), `_optouttest.py` and `_dnctest.py` (both stop at a Playwright "Executable doesn't exist" launch error) fail identically on both trees; output differs only in the file path inside the traceback line. None of the three was edited.

## Tests not run, and why
- The browser halves of `_suppressiontest.py`, `_optouttest.py`, `_dnctest.py`: bundled Chromium is not installed here and the local access-code file is absent; same on baseline.
- Full `ci_suite.py`: not run by the Lead; for `gate`.
- `python build_preview.py` / design-preview: not run, generated files are not committed.

## Exposure
sending: this makes the board stricter. Text, WhatsApp and mailto controls disappear on held leads in three board places. Nothing new can be sent. The Call button on held-lead call sheets is unchanged (D-F). Not covered, reported: a lead whose only mark is hand status "Dead" is outside `_boardMailHeld` and keeps its controls (D24).

## Flags and defaults
None. No env var, no switch.

Merged is not visible: visible after the next publish.

Tickets: F-site-01, F-site-02, F-ideas-01 (B1, APPROVED-FIX #2). Decision entry: D24 (appended, Lead renumbers).
Merge: Alex only

🤖 Generated with [Claude Code](https://claude.com/claude-code)
