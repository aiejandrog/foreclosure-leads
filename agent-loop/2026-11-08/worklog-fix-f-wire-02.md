
### F-wire-02: Call Mode page half, warn and retry when a Do Not Contact has not reached the bridge
- Task: F-wire-02 (page half only), severity S1, APPROVED-FIX #1
- Owner: callmode-dev (acted by fixer-f-wire-02)
- Branch + PR: claude/agents-fix-f-wire-02 / PR #452 (draft); round 2 after chief's scope bounce
- Files changed: call_mode.py (notes-push block only), _notespushtest.py (new), docs/agent-teams/DECISIONS-NEEDED.md (D24 appended). The pr/ and runs/ files from round 1 were removed from the branch (docs/ is mirrored to the public site).
- What changed (plain English): A Do Not Contact tapped on a phone that cannot reach the bridge now shows a red strip and retries 4/8/12 s. A push counts as delivered only for a 2xx with a JSON object body and ok === true; an HTML or text body, a missing ok field, ok:false, a non-2xx, a rejection or an error while reading all count as failure. Local save and payload unchanged.
- Tests run (command + result): `python _notespushtest.py` -> 76 ok, ALL PASS (failed 42 checks on the previous head with the new cases, 19 on the unchanged tree); `_callwiringtest.py`, `_e2ewiringtest.py` ALL PASS on branch and base; `_carlostest.py` 51/0 both trees; `_suppressiontest.py` 17/19 both trees (pre-existing)
- QA verdict (PASS/FAIL + by whom): round 1 PASS by gate; chief bounced on scope; re-gate pending
- Exposure: none to homeowners (operator warning plus retry of the existing POST /notes). Merge: Alex only (DNC delivery).
- Flags (default off?): none
- Owned-surface bugs found: /notes answers ok:true when its DNC sweep fails (D21); the page cannot see that
- Needs Alex (one line each): merge decision (LEGAL REVIEW); whether to do the server half of D21
- Decisions needed (link to the DECISIONS-NEEDED entry): D24
- Next run should know: merged is not visible, visible after the next publish. Strip look and position unchecked on a real phone. The shared scratchpad is overwritten by other agents; use a private subfolder.
