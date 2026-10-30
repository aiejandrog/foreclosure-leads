# Call Mode: "Complete" step before the lead advances

Pressing Next (lead card) or Next lead (after an outcome) opens "What did you do on this lead?". Tick Called / Texted / Emailed / Left a voicemail / Mailed a letter / Knocked on the door / Spoke to someone else (who + note) / Other (note), then "Log and next lead" or "Skip, log nothing". Already-logged-today actions are ticked and locked. Each chip is one `touches` entry with `src:'manual'` and one `saveNotes()`.

- It shows in the call log, and a manual call/text/email/letter/door moves the lead from Untouched to Reached out.
- "Spoke to someone else" is a note (ch `other`): no phone, no wpRelatives, not outreach to the owner.
- Not touched: dials, status, stage, no/dnc/notint, badph/dntph, opt-out, the DNC ledger, send_server.py. Nothing is posted to /text; nothing written to text_sent.json or mail_sent.json.
- **Cadence may still email a lead the operator already emailed by hand; the touch is only visible in Call Mode and the board.** A manual call/text counts toward the FTSA 3-in-24h cap (safe direction).
- Adds new content to `/notes` through the existing `saveNotes`; no new `/notes` or `/text` code. Raised as DECISIONS-NEEDED D18 (default: touches only).

Tests: `_callcompletetest.py` (new, fails on main). `_callnavtest.py` one assertion updated for the new wiring.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
