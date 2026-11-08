board: show the do-not-quote equity chip on desktop and the call sheet (F-site-08)

Based on `claude/agents-fix-f-site-01` (B1, F-site-01 / F-site-02 / F-ideas-01). **B1 is not merged** and is itself incomplete (three of its edits were blocked by the guard and travel as a patch). This branch contains B1's helper, test and PR files; none of B1's blocked edits are applied or touched here. Merge B1 first, or rebase this onto main (the diff to `tracker_template.html` is three small hunks and does not overlap B1's).

## Summary (plain English)
The board bakes an equity card on each lead (`r.eqc`) that says whether a dollar figure may be spoken. The phone card showed that as a chip ("Equity: Ceiling only, do not quote"). The desktop row and the call sheet did not, so a lead that is not allowed to be quoted still showed `BUY ... in $655k` in the row and `Money: ... net est $369,000` on the sheet with no warning. Now:
- the desktop row carries the same chip above the figures (existing `_eqcChip`, reused, text not changed);
- the call sheet carries the chip right under the address block;
- on the call sheet's Money line, when `r.eqc` exists and `may_quote` is not true, the figure reads "at most $X, liens not verified" (the wording the row tooltip `_EQ_TIP_CEIL` already uses) instead of "net est $X".
No number changes. Leads with `may_quote: true` or with no `eqc` look exactly as before. Mobile cards are untouched and do not double up (the desktop row is not built at phone width).

Files: `tracker_template.html` (render row cell, top of `_callSheet`, `_csDiligence` Money line), new `_boardeqcchiptest.py`. No equity-bake file, no `_eqcChip` / `_EQC_NAME` edit, no suppression function, no generated page.

## Tests run
Failing first, unchanged tree, `python _boardeqcchiptest.py` (system Chrome, invented 2099-* rows, refused network, fake /health):
```
FAIL (a) noq desktop row: chip text "do not quote" exactly once (text) and one chip element  [text matches=0 chips=None]
FAIL (b) noq call sheet: exactly one chip, "do not quote"
FAIL (b) noq call sheet: chip is near the top ...  [chipAt=-1 moneyAt=1461]
FAIL (b) noq call sheet: net est is worded as a ceiling ...  [Money: value $655,000 - FJ $286,000 - senior $0 - tax not pulled -> net est $369,000 ...]
FAIL (c) ok desktop row: one "may quote" chip, no "do not quote"
FAIL (c) ok call sheet: one "may quote" chip near the top
19 checks, 8 failed
```
After the change: `19 checks, 0 failed`, exit 0. Includes (e): at 390 px each lead with an eqc has exactly one chip, the no-eqc lead none, and no desktop rows are built.

Neighbouring suites, serial, temp `DEALFLOW_DIR`, with and without `PYTHONUTF8=1`, on this branch and on `fl-loop-base`: `_clienttest`, `_equityhonestytest`, `_equityroutetest`, `_equitylanetest`, `_taxdeedeqctest`, `_boardaccuracytest`, `_boardstagetest`, `_boardmergetest`, `_docboardtest`, `_funnelparitytest`, `_footprinttest`, `_navtest`, `_boardurltest`, `_callequitytest` all pass on both. `_equityboardtest` and `_mirrortest` fail identically on both trees (Playwright "Executable doesn't exist", no bundled Chromium). `_clienttest` pins the text `"+who+clientBan+"` in the call-sheet return, which is why the chip is appended to `who` rather than edited into that line (an earlier attempt failed that suite; fixed before commit).

## Tests not run, and why
- Full `ci_suite.py`: not run; neighbouring suites only. For `gate`.
- `_equityboardtest`, `_mirrortest`: need the bundled Chromium, not installed here (same failure on baseline).
- `_boardmanualheldtest.py` (B1's): same 23 failing checks on B1's head and on this branch, because B1's blocked edits are not applied. Not affected by this change.
- `python build_preview.py`: not run, generated files are not committed.

## Exposure
none. A label on figures the board already shows; no number changes. LEGAL REVIEW: no (operator display, no homeowner copy).

## Flags and defaults
None.

Residual, reported not fixed: the clipboard text ("Copy all numbers" / live-call brief, `Money: value ... equity ~`) still prints equity with no quote label. It is outside this ticket's allowed lines.

Merged is not visible: visible after the next publish.

Ticket: F-site-08 (B2, APPROVED-FIX #3). Predecessor: B1 (`claude/agents-fix-f-site-01`), not merged.
Merge: ready for Alex (after a clean QA PASS from gate)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
