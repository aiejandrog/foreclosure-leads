# Agent build summary, 2026-10-29 to 10-30

Written by the Lead. Every item below was decided MERGE by QA and is on main; the board and Call Mode show it only after the next refresh finishes.

## Merged
- Equity card (`eqc`): a dollar equity figure is quoted only when the state is verified and a judgment is known. Tax-deed opening bids are never quotable and are labelled "opening bid"; auction-page amounts are "auction listing", not "docket" (may_quote 116 -> 102 on the 4,634-row snapshot).
- Call Mode: equity card, appointment gate (unverified equity forces a "Verify equity" next step), after-the-yes panel, device key on every bridge call, outcomes posted to /notes and texts to /text, ordinal street numbers (5 AVE -> 5th AVE), fluid column width, Complete step ("what did you do?"), Whitepages lookup button (POST /wp/lookup, LEGAL REVIEW, capped by WP_DAILY_CAP).
- Board: after-the-yes stage counts and Overdue follow-ups; `_mergeLead` keeps a teammate's after-the-yes unit (DNC/Dead never overridden).
- Cadence/bridge/stay PRs #290 #293 #294 #192 #246 #258 #309 #321 #373 #364 #372 #381 (follow-ups kept ON via cadence-daily.bat).
- Docs and tooling: agent-team playbook, fixer skill and role, email deliverability doc and report, verdict-blockers diagnosis, DECISIONS-NEEDED master list.

## Not built
- D15 ruling (a newer hard no replaces an older soft no): ruled by the Lead, not built; it is on the suppression surface, so it waits for Alex's word.
- Fixes from VERDICT-BLOCKERS beyond fix #1 (OCR without PowerShell, case-facts store, Broward clerk API key): no spend allowed this round.
- Rejected: #229 (conflicts with main's bankruptcy routing, a legal call), #112 (placeholder letter was mailable; copy broke the no-deadline rule).

## Needs Alex
- Refresh: both runs on 10-30 died on the laptop's Wi-Fi dropping every 20-30 s. Fix the connection, then start refresh-dealflow.bat.
- footprint.local.json: add advisor_emails and retired addresses (deny list already filled).
- Spend: paid_reads.json monthly_cap is 0 (so lis pendens and lien solves stop). Twilio key parked as twilio.key.off. The desktop needs the same.
- D7 booking slug, D8 copy rewrite (attorney), D9 bounce-gated ramp: still open.
- First live Whitepages lookup (about $0.22) to confirm the key.
- Close PRs #229 and #112; about 20 leftover fl-* worktrees.
