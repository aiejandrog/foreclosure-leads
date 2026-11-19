# Amendment 2026-11-18 — rule 3 lifted (follow-ups unfrozen)

Reads after CLAUDE.md and after `OPERATING-RULES-2026-11-07.md`. Where this file conflicts with
rule 3 of that file, **this file wins**.

## What changed

Rule 3 of `OPERATING-RULES-2026-11-07.md` read:

> 3. **Never set `DEALFLOW_FOLLOWUPS_ON`**, on any machine, in any shell, task or test that touches a
>    real `DEALFLOW_DIR`.

That rule was left standing on 2026-11-13 only because Alex was not asked about it when rules 1 and
2 were lifted. It is now lifted, by Alex, in writing, on 2026-11-18.

## Rule 3 as it now reads

3. **Follow-ups are ON.** `DEALFLOW_FOLLOWUPS_ON` may be set in the task, shell, launcher or
   process that runs `mail_followups.py` against the real `DEALFLOW_DIR`. It must stay **unset** in
   unit tests, fixtures and preview builds — those use a temp `DEALFLOW_DIR` and invented people,
   and a follow-up send from a test is still a homeowner contact.

   Lifting this rule turns nothing on by itself. The variable still has to be set in the process
   that runs the follow-up batch. Setting it is now permitted; it is not automatic.

   **Every other send gate still refuses.** The opt-out ledger, its freshness and same-day sync
   check, the bankruptcy stay gate, `mail_guard`, and the daily caps all still block a send. The
   first-email gates removed on 2026-11-13 stay removed. Nothing in this amendment touches spend,
   vendor keys, or the LEGAL REVIEW surface.

## What this means for agents

An agent may no longer cite rule 3 as a reason to decline, defer or freeze a follow-up request.
"Frozen by OPERATING-RULES rule 3" is no longer a valid answer on this repo. If a follow-up is
genuinely blocked, name the real gate — opt-out, stay, `mail_guard`, daily cap, or a missing env
var — and say which one.

Rules 4 (spend), 5 (vendor keys), 6 (merged is not visible), 7–9 (merge, branch, LEGAL REVIEW) are
untouched and still in force.

## Pointer to add to the 11-07 file

Insert immediately under the existing `FREEZE LIFTED 2026-11-13` blockquote:

> **Rule 3 LIFTED 2026-11-18** — `DEALFLOW_FOLLOWUPS_ON` may be set against a real `DEALFLOW_DIR`.
> See `OPERATING-RULES-AMENDMENT-2026-11-18.md`. All other send gates unchanged.