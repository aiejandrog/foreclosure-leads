# Agent team brief, round 2 (every teammate reads this first)

Goal: ONE signed, assigned, paid wholesale contract. The funnel breaks between appointment and
contract (444 dials, 373 leads, 32 conversations, 6 appointments, 0 contracts). Two jobs this round:
(1) VERIFIED EQUITY: no appointment booked or price spoken on a guess; (2) the after-the-yes flow:
every conversation gets a dated next step the board counts. Simple, wired end to end, proven by a
test that ran. Round 1 is cancelled; its partial work is in docs/agent-team/salvage/ for reference only.

## Read before any code
In your worktree: CLAUDE.md (all), HOMEOWNER-OPTIONS.md, PLAYBOOK.md, CALL_SCRIPT_NEPQ.md,
CI-TESTS.md, then the top docstring of every file you own.

## Facts (verify in code)
- equity_state.py gives every lead `eqstate`: clear / priced = FACT (may be spoken); unpriced =
  CEILING; none / unchecked = GUESS (never pitched). Bake: foreclosure_leads.py ~2565 / ~2652.
- Call Mode carries only `eqv` 0/1 (call_mode.py ~2410) and shows equity % from diligence_flags
  basis_of / known_debt_of (_call_equity_parts ~1725). call_mode.py ~8024 prints value minus
  payoff/judgment as "equity" with NO eqstate check: a rule-6 risk Call Mode Dev must close.
- Appointment (call_mode.py ~8586, o.k==='appt') checks no equity.
- records_liens.py searches Official Records by OWNER NAME (records_by_qs ~142, mint_and_fetch ~591);
  analyze() ~920 takes a judgment argument. miami_judgment.py is tested but not wired in.
- OR subscription expired 10/26; Turnstile needs 2Captcha (paid). Build and fixture-test only.
- send_server._merge_note (the /notes merge) is fill-only: a newer nextStep or stage never replaces
  an older one. The after-yes save is not real until that is fixed (salvage patch shows one fix;
  drop its wpm/wpe hunk, that is Whitepages territory).

## Hard rules (break one = PR rejected)
1. Git: NEVER touch the main checkout C:\Users\olqbb\projects\foreclosure-leads (a refresh is
   running). Work only in your worktree. Push only your own claude/agents-* branch. Never push to
   main, merge into main, or force-push. `gh` is NOT installed: write the PR body to
   docs/agent-team/pr/<suffix>.md in your branch; Alex opens the drafts.
2. $0: never run any .bat, refresh, publish_site.py, access_codes.py, records_liens live,
   miami_judgment live, whitepages_lookup.py, Tracerfy, pacer_stay.py, live bk_lookup.py, Twilio,
   2Captcha, or any paid / rate-limited call. Never change a spend cap. Tests stub the network.
3. No homeowner contact: never run send_server.py on its real port or real DEALFLOW_DIR, cadence.py,
   outreach_email.py, reply_send.py --send. ALWAYS run tests with DEALFLOW_DIR=<temp dir> (paths.py
   honours it), a free port, stubbed SMTP. Never hand-edit docs/index.html or docs/call.
4. Owned suppression surface (CLAUDE.md table) and Call Mode's no / dnc / notint branches next to
   the appt line: do NOT edit; report bugs in the PR body. Touching stay_gate.py, never_contact.json,
   bk_lookup.py, DNC, opt-out or send_server.py => "LEGAL REVIEW" in the PR title.
5. PUBLIC repo: never commit caches, logs, leads_*.json, worker_notes.json, optouts.json,
   mail_sent.json, *.key/*.pass/*.codes/*.url, anything from ~\DEALFLOW, real names/phones/addresses.
   Fixtures use invented people. Never print or ask for a key. Reports: counts and case ids only.
   Several suites rewrite call_objections.json: never commit that file.
6. Legal/copy: no sale number, offer or "you have $X in equity" unless eqstate is FACT; a recorded
   amount is never called a payoff. Keep-or-sell first, bankruptcy never first, no buy-back /
   rent-back. No fake deadlines, no "preapproved", no stop-the-sale fee, no fee before performance
   (s.501.1377 / FDUTPA). Opener never "is this X?". Disclaimer text only from disclaimer.py. Texts
   keep the EN/ES let-me-know line, no "Reply STOP". Never store cash buyer or title attorney info.
7. Do not touch whitepages_lookup.py, wp_contacts.py, balloon code.
8. FILE OWNERSHIP: only the owner edits a file (see PLAN.md). Need a change elsewhere: message the
   owner with the exact change and wait. Anyone may add their own new _<name>test.py.
9. DONE = QA wrote PASS with the command and its output.

## STANDING RULE (Alejandro, 2026-10-29), overrides anything below
Nothing spends, sends, or changes a rule without Alex. Teammates never merge. After QA PASS the Lead
merges to main (amended 2026-10-29), EXCEPT anything titled LEGAL REVIEW or with an OPEN entry in
DECISIONS-NEEDED.md: those wait for Alex. So push your branch after PASS and tell `main`. Any task touching stay_gate.py, never_contact.json, DNC, opt-out,
send_server.py send paths, text or email copy, or a spend cap STOPS, appends its question to
C:\Users\olqbb\projects\fl-agents-patterns\docs\agent-teams\DECISIONS-NEEDED.md (format in that file:
exact options + risk each + recommendation + fallback), then moves to its fallback. Do not build the
gated change past that point.

## Coordination
Names: `equity`, `callmode`, `board`, `qa`; Lead is `main`. Status lines (uncommitted) in
C:\Users\olqbb\projects\fl-lead\docs\agent-team\status\<name>.md. Hand to QA: worktree, branch, SHA,
exact test commands, what changed. Push only after PASS. 3 FAILs on one task: stop, tell `main`.
Needs spend / legal / owned-surface edit: stop that task, write it under "Needs Alex" in your status.

## PR body (docs/agent-team/pr/<suffix>.md)
Plain-English summary; tests run with output; `Exposure:` sending / suppression / stays / spend /
none; flags (default off); "Visible after the next refresh-dealflow.bat"; owned-surface bugs found.
