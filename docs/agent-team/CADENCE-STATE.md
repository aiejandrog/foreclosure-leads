# Cadence state: PRs #290, #293, #294

Checked 2026-10-29 against origin/main b829b54. Based on reading the diffs and running `git merge-tree`. No tests
were run on the PR heads, and nothing was run against real mail.

**Follow-up emails (steps 2–4 of the day 0/2/4/7 sequence) go out today with none of these guards.**
Main has a bounce pause for first touches only (`cadence.py` ~847, `first_touch_paused()`), and its own
comment says "Follow-ups fall through". The 10:00 `DealFlow Cadence` task mails follow-ups whenever one
is due.

## What each PR does

| PR | Change | Switch | Default once merged |
|---|---|---|---|
| #290 | A follow-up waits for a clean bounce scan of the mailbox that sent the earlier note. The scan must be newer than that note, no more than 36 h old, and must match the bounce list on disk. A follow-up that is held stays due. `/send` answers 451. | `DEALFLOW_BOUNCE_SCAN_GATE=0` turns it off. Tuned by `DEALFLOW_BOUNCE_SETTLE_H` (2) and `DEALFLOW_BOUNCE_SCAN_MAX_AGE_H` (36). | **On.** A mailbox that `bounces.py` has never scanned stops sending follow-ups. |
| #293 | One note per owner per run. Any other step due for the same case, owner, email or address moves to a later day. | None | **On**, always |
| #294 | Master switch for follow-ups in cadence, `POST /send` and `outreach_email` `Re:` rows. First touches are not affected. | `DEALFLOW_FOLLOWUPS_ON`: exactly `1` means on. Unset means the follow-up is listed and nothing is sent. Any other value fails closed (cadence exits 2). | **Off.** Nothing in main sets it: no `.bat`, no task XML, no `.py`. |

## Merge mechanics (verified with `git merge-tree`)

- #290 merges into main cleanly. #293 and #294 each conflict with main only in `.gitignore`, which is a trivial fix.
- #290 merges cleanly with #293 and with #294. **#293 and #294 conflict with each other in `cadence.py`**
  (both edit the send loop) and in `.gitignore`. The `cadence.py` conflict needs a hand merge.
- All three are 175–186 commits behind main. #379 (2026-10-26) changed `cadence.py` after they were
  written, and git merges that text without complaint. Merging cleanly is not the same as being correct,
  so rerun their tests after each merge: `_bouncefollowuptest.py`, `_cadenceonetouchtest.py`,
  `_followupswitchtest.py`, `_cadencesiblingtest.py`, `_cadencegatetest.py`, `_firsttouchtest.py`.
- Order: #290, then #293, then rebase #294 onto that result and resolve `cadence.py`. All three edit
  send code and fall under the CLAUDE.md suppression and send surface, so each one is a LEGAL REVIEW merge.

## DECIDED 2026-10-29 by Alejandro: keep follow-ups going

Merge #290 -> #293 -> #294 (rebased), and in the same step set `DEALFLOW_FOLLOWUPS_ON=1` where the
DealFlow Cadence task runs (user env var on the laptop). Skipping the variable silently stops follow-ups.
#290 will still hold follow-ups from any mailbox `bounces.py` has not scanned in 36 h, so confirm
`bounce_scan_status.json` lists every sending mailbox after the first refresh.

## The decision as it was put

**Should automated follow-up emails keep going out now, or pause until the bounce scanning is proven?**

- **Pause (recommended):** merge all three and leave `DEALFLOW_FOLLOWUPS_ON` unset. First touches
  continue, and follow-ups stop. Turn them back on by setting the variable to `1` where the Cadence task
  runs. Do that only after one `bounces.py` run has written `bounce_scan_status.json` for every sending
  mailbox. Otherwise #290 holds them anyway.
- **Keep going:** merge all three and set `DEALFLOW_FOLLOWUPS_ON=1` in the same step. Follow-ups continue,
  now behind the bounce check and the one-per-owner rule.

Why I recommend pausing: the funnel's bottleneck is the step from appointment to contract, not email
volume. Today a follow-up can reach an address that bounced the first note. The pause costs a few
automated touches a day and removes that risk.
