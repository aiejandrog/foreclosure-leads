## board: remove personal values from template comments (F-site-05, F-site-06 line 2633, F-sec-08 comments)

Tickets: F-site-05, F-site-06 (the case-id-and-name comment only), F-sec-08 (comment lines only; I-05's strict rule is this PR's test). Ticket B3, severity S4, chief verdict APPROVED-FIX #4. Owner role: board-dev.

**Predecessor.** This branch is built on `claude/agents-fix-f-site-08` (B2, draft PR #453, head d722a8bf), which sits on `claude/agents-fix-f-site-01` (B1, c38c7469). **B1 and B2 are not merged**, and B1 is itself incomplete (three of its edits were blocked by the guard and are delivered as a patch). Merge B1 and B2 first, or retarget this PR to main once they land. This PR touches none of their lines: its hunks are comment text only.

### What this does, in plain English
`tracker_template.html` is the source of the public board page. Its code comments are not encrypted, so a real phone number, owner name, case number or street address typed into a comment is published in clear text. This PR replaces those values, in comments only, with invented ones (`100 NW 1 ST`, `Owner Example`, `2099-000001-CA-01`, `CACE-99-000001`, 555 numbers, `person1@`). No code, no page text and no behavior changes. 31 lines changed, all inside comments.

Changed lines by class of value removed (never the values themselves; a line can be in more than one class, and 31 lines changed in all): owner name 5 lines (one is the F-site-06 name-beside-case-id line); street address 17 lines (including a five-line list of "real examples off the live board", now invented shapes); real-shaped case id 7 lines (court, Palm Beach, tax-deed and Miami shapes); non-555 phone number 2 lines (a texting owner's number, and an example typed into a reply comment); mailbox local parts 1 line.

### Failing first
`_commentpiitest.py` run on the unchanged template (counts and line numbers only):
```
FAIL (a) no mailbox in a comment :: 1 comment line(s): 16987
FAIL (b) no non-555 phone number in a comment :: 2 comment line(s): 12461 15334
FAIL (c) no case id without 2099/99 year in a comment :: 7 comment line(s): 2633 3163 5835 7755 7802 8609 16986
FAIL (d) no street address in a comment :: 17 comment line(s): 3164 3253 3267 4318 4871 5217 6736 6737 7154 8208 8209 8210 8211 8212 8213 16713 16715
4 failed
```
(Line numbers are for the B2 head.) The six control checks (an invented 305 number and case id are rejected; a 555 number and 2099 ids pass; and so on) pass on both trees.

### After the change
```
python _commentpiitest.py   -> 11 PASS, ALL PASS   (DEALFLOW_DIR=temp, no network)
```
Executable code is identical before and after: stripping comment text from both versions of `tracker_template.html` leaves 0 differing lines; 31 changed lines, same line count. No changed line is in the exempt places (below).

### Not changed on purpose (reported, not edited)
- The comment block above `function _optedOutIdentities` (names an opted-out owner; owned suppression surface; D-G b). The test exempts it by function name.
- The Playbook "Martin File" paragraph (Alex's content; D-G c). It is page text, not a comment, so the comment test never reads it.
- `ROUTE_ORIGIN` (a home-base street address in CODE, not a comment): not mine to edit; reported to chief.
- `badnum_audit.py` holds a phone number also removed from the template comment; `footprint.py` / `publish_site.py` have no generic phone rule. Alex's decision.
- `replies.py` and `_stoptexttest.py` carry the example number that was typed into the template comment; not edited (chief to decide).
- `docs/index.html` and `docs/call/` are generated and still carry the old comment text until the next publish. Git history keeps all of it; scrubbing history is D-G a (Alex).

### design-preview.html
Regenerated only with `python build_preview.py` (temp `DEALFLOW_DIR`, dead proxy, no gate, 7 sample leads). It ran cleanly. The committed preview was stale: the diff is larger than this PR's 31 lines because it also picks up earlier template changes. Counts: consumer-mailbox shapes in the preview 4 before, 0 after (stale mailboxes removed by the regeneration); comment lines the new test flags in the preview 29 before, 1 after (the exempt `_optedOutIdentities` block).

### Tests run
See the table in the run log (`docs/agent-teams/runs/2026-11-08-fix-f-site-05.md`): the new test, `_footprinttest.py`, and all 72 suites that read `tracker_template.html`, each on this tree and on `fl-loop-base`, with and without `PYTHONUTF8=1`, serial, temp `DEALFLOW_DIR`, no network.

### Tests not run, and why
- `ci_suite.py` in full (long; the 72 suites that read the template were run instead).
- Browser tests: no code changed, so no rendering can differ.
- Any test needing the bridge port, a `.bat`, a send, publish or paid call (frozen).

Exposure: none to homeowners. Page text does not change; only the removal of values from comments. The values stay in public repo history and on the live page until the next rebuild and publish.

Flags and defaults: none added.

Merged is not visible: visible after the next publish.

Merge: ready for Alex (after a clean QA PASS from gate; chief to accept scope).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
