# Broward acceptance matrix

Written 2026-10-03 before any Broward pilot code runs against it. Do not edit a row after seeing output. Add a dated correction section instead.

Worktree `claude/county-pilots` at `ecc18ff` (prompt base `25eac03` is an ancestor). This matrix is not acceptance. The Miami pilot is not accepted, and nothing here becomes accepted because Miami code is reused.

Saved evidence checked this pass: code only, plus folder counts. No PDF was opened. No county site was called. The saved Broward official-records index through recording date 2026-09-28 is a seed and was not pulled live.

## Rules

| Id | Rule | Pass looks like |
|---|---|---|
| R1 | No false clean | A case is never `supported` on a keyword, a docket line, or an unread PDF. |
| R2 | Exact amount | A judgment total is `supported` only when its own line items reproduce it. A cents mismatch is `conflicted`. |
| R3 | No face pricing | The foreclosed debt is the judgment, never a mortgage face amount. |
| R4 | No false clear | A thin search, a CAPTCHA, a missing image, or a capped search is a named gap, never `CLEAR`. |
| R5 | Own case | This case's judgment and lis pendens are not another claim. Another person's instrument never sets this amount. |
| R6 | Gaps visible | Each failure mode is named on the verdict: CAPTCHA rejected, session expired, login-walled image, no image control, count mismatch, changed case header, capped search, wrong-person search. |
| R7 | Repeatable | Two runs on the same saved inputs return the same verdict, amount, and gaps. |
| R8 | Restartable | A run killed mid-case and resumed returns the same result, with no orphaned lease left open. |

Court-docket enumeration stays an `AccessGap` until a source is chosen. The Commercial Data API is not called. Holds, suppression, equity, and sends are out of scope.

## Cases

| Id | Case | Role | Expected now | Must show | Must not show |
|---|---|---|---|---|---|
| B1 | CACE-26-015348 | Ordinary civil number, cited from the public index as an example only | `GAP: no saved evidence` | The gap, named | A verdict, an amount, or `CLEAR` |
| B2 | none named | Final judgment indexed under a co-defendant (the two-hop path) | `GAP: no saved evidence` | The gap, named | A verdict built from the other party's instrument |

`GAP: no saved evidence` is a pass for honesty, not for coverage. A later live collect needs a yes, a case list, and a terms answer.

## Correction

None yet.
