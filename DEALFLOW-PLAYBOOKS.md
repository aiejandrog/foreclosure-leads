# DealFlow operating playbooks

This is the task router for DealFlow, Call Mode, and Call Coach. Use one agent and select the playbook that matches the job. Do not create a separate agent for routine work that already belongs to one of these playbooks.

The detailed safety rules in `CLAUDE.md`, `MACHINE-HANDOFF.md`, and `CASE-REVIEW-PROCEDURE.md` remain authoritative. This file tells the agent how to run the work and prove the result. It does not weaken any gate in those files.

## The operating loop

For every task:

1. Select the playbook.
2. Read the linked rules and use the existing tools. Do not rebuild a script, template, or checklist that already exists.
3. Run read-only checks first. State any live action that needs Alejandro's approval.
4. Do the work.
5. Check the result against the playbook's proof list.
6. Report the verdict first, followed by the numbers and evidence.
7. If the process failed, fix the playbook or tool that allowed the failure. Do not patch the explanation in chat and leave the process broken.

Approval for a task is not blanket approval for an external action. Sending an email or text, placing a call, changing a live record, publishing, deploying, or spending money requires a direct instruction that covers that action. Reading, diagnosing, editing code, and running local tests do not.

Snapshot numbers are evidence, not permanent rules. For example, 116 verified leads and a 17 to 20 percent bounce rate describe the November 21, 2026 review. Recalculate current counts before using them.

## Playbook: DealFlow board

Use for board accuracy, lead ranking, filters, counts, equity display, duplicate handling, and board regression tests.

### Job

Keep the board accurate and honest. Put the best usable leads first without presenting estimated equity as verified fact.

### Rules

- Treat equity as `UNVERIFIED` until official records have been checked by owner and co-borrower name and every known mortgage and lien has been counted. A blocked or empty source is not proof of a clean title.
- Never repeat a board equity estimate as fact. Name the source and verification state.
- Default to Equity % descending when that is the selected sort. Preserve the sort when the user changes views, filters, or refreshes.
- A filter must use one definition everywhere. `Has equity` cannot produce different totals in different views.
- Totals, visible cards, hidden cards, and call queues must reconcile. Flag duplicate cards, stale counters, and filters that silently reset.
- Company-owned property needs a company-aware script. Do not ask an LLC whether it is "the owner."
- Deliverability is a list-quality signal. Investigate the source data and suppression path when hard bounces materially exceed the operating threshold. Do not blame copy without evidence.
- Follow the suppression ownership rules in `CLAUDE.md`. Report a suppression defect instead of editing that owned surface from another session.

### Toolbox

- Live board: <https://aiejandrog.github.io/dealflow-board/>
- Design source: `tracker_template.html`
- Generated board: `docs/index.html`. Never edit it by hand.
- Board build and census: `foreclosure_leads.py`
- Equity state and verification: `equity_state.py`, `deep_equity.py`, `CASE-REVIEW-PROCEDURE.md`
- Existing board tests: use the focused `_board*test.py`, `_equity*test.py`, `_filter*test.py`, and call-mode tests that cover the changed surface.

### Proof

Before replying, answer all of these:

- Do totals agree across views?
- Do the displayed counts equal the cards produced by the same filter?
- Is every equity figure labeled with its real verification state?
- Does the requested sort survive view and filter changes?
- Did the focused regression tests pass?

## Playbook: Call Mode

Use for a live dial block, call queue behavior, scripts, call outcomes, callbacks, and follow-up scheduling.

### Job

Help Alejandro complete the 2 PM to 7 PM dial block, reach real homeowners, and leave a complete call log.

### Rules

- During the dial block, work only on calls and call logging. Do not mix website development into the block.
- The daily target is 125 actual call attempts. Opening a card, copying a number, or preparing a script is not a dial.
- Queue only leads that pass the current eligibility and verified-equity rules. The November 21 count of 116 is a snapshot, not a hard-coded quota.
- Advance the script only through the `THEY SAID...` outcome buttons.
- Keep outcome controls and the call log visible. The script panel must not cover them.
- Log every call. Every answered call needs an outcome.
- Record a booked appointment with the date, time, homeowner, and assigned person.
- Handle a reply or callback within five minutes during the block.
- Use the approved follow-up schedule for cold leads. Do not invent dates or send a follow-up without authorization.
- The call log is the source of truth for dial totals. Report any mismatch between it and the board.
- A direct instruction to run the dial block authorizes call assistance and logging. It does not authorize bulk email or text sends.

### Toolbox

- Call Mode on the DealFlow site
- Call log and outcome controls in Call Mode
- Approved scripts and message rules in the shared toolbox below
- Existing `_call*test.py` and `_callworkflow*` tests for changed behavior

### Proof

Before replying, answer all of these:

- Does the dial total equal the number of logged call attempts?
- Does every answered call have an outcome?
- Does every appointment have a complete record?
- Were callbacks handled within the required window?
- Did the script and outcome controls remain usable?

## Playbook: Call Coach

Use for transcription, diarization, recording outcomes, live coach behavior, summaries, and call-quality review.

### Job

Produce a call record that Alejandro can trust when he reviews what he and the homeowner actually said.

### Rules

- `YOU` is Alejandro. `THEM` is the homeowner. Verify labels against the audio instead of trusting diarization blindly.
- A carrier recording, automated message, or disconnected-number message is not Alejandro.
- If the live coach remains on `THINKING` and fails, report the failure plainly.
- A booked appointment must appear in the recording outcome, not only in a transcript or note.
- Check money, dates, times, addresses, and phone numbers against the audio. Do not normalize a number that changes its meaning.
- Do not invent names. Flag unclear speech instead of guessing.
- Flag missing audio or transcript gaps with timestamps. Do not present an incomplete transcript as complete.
- Every real recording needs a summary and a note. Mark silence captures as silence, not as calls.
- The recording length and player duration must agree within the product's accepted tolerance. Repeated drift is a bug.

### Toolbox

- Call analysis: <http://127.0.0.1:8777/calls>
- Original recording audio
- Recording outcome, transcript, summary, and notes shown by the app
- Existing call analysis tests for the surface being changed

### Proof

Before replying, answer all of these:

- Do speaker labels match the audio?
- Do all material numbers match the audio?
- Is every appointment recorded as an outcome?
- Is the transcript complete, or are all gaps clearly marked?
- Did the live coach load?
- Do the list duration and player duration agree?

## Shared toolbox

Reuse these rules. Do not recreate them in each prompt or script.

### Calls and objections

- BSG opener: identify who the call is for in the first five seconds. Give one real example, state the cost, and ask for one next step.
- Attorney objection: agree that having an attorney is sensible. Explain that the free consultation covers financial options, not legal representation. Do not record this objection as a rejection unless the homeowner actually declines.

### Text and email

- First text: lead with the useful reason for contacting them, address the likely concern, name the person and property, offer one concrete time, and end with a question.
- Never open with `FINAL NOTICE`.
- A stop request is a hard opt-out. Do not contact that person again. Follow the canonical stop detection and ledger rules in `CLAUDE.md`.
- Match payment-reminder tone to the existing conversation. Aly uses sentence case, exclamation marks, emojis, and `partner`. Dennis uses natural sentence case and flowing messages. Giselle is professional, warm, and concise.
- Check the Sent folder and the DealFlow ledger before claiming that a message was or was not sent.
- Use `reply_send.py` for homeowner replies and one-off email. Do not bypass its gates.

### Verification

- Equity: search official records by owner and co-borrower, count all known mortgages and liens, and leave the result `UNVERIFIED` when a required source is blocked or empty.
- Docket watch: read-only every eight hours. Report changes only.
- Sends: verify against the Sent folder and the DealFlow send ledger before stating the result.

## Output rules

- Start with the verdict.
- Follow with the numbers, evidence, and any failed check.
- Use short paragraphs and plain language.
- Do not use em dashes.
- When Alejandro corrects a fact, remove the old fact from the active work in the same turn.
- Separate a verified fact, a current estimate, and an unverified claim. Never blend them.
