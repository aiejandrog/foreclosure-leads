# worklog-wire (agent "wire", run 2026-11-08, worktree fl-loop-wire @ aac7a8a9, detached, read-only)

Counts and case ids only. Fixtures are invented (2099-* cases, 555 numbers). No bridge on a real port, no real
DEALFLOW_DIR, no network. Proof scripts are in `RUNDIR\wire-proofs\` (run each from a checkout root, e.g.
`cd %USERPROFILE%\projects\fl-loop-wire && python %USERPROFILE%\DEALFLOW\agent-logs\2026-11-08\wire-proofs\<name>.py`).

NOTE ON MESSAGING: the SendMessage tool does not exist in this agent's tool list (tried once, "No such tool").
Tickets could not be messaged to `chief` one by one. Every full ticket block is also in the final handback message.

## Step log

```
17:05 READ   OPERATING-RULES, AUDIT-LOOP-PROTOCOL, PATTERNS, OWNERS, OUTPUT-TEMPLATE, WIRING-MAP.md (edb6493, 2026-10-31), CI-TESTS
17:12 RAN    python _e2ewiringtest.py -> ALL PASS, 7 KNOWN GAPS asserted (D15 x3, D17, D18, 2x Complete-sheet). Passes here: the "known failing" status on main is environmental
17:14 GUARD  agent_guard BLOCKED three Bash commands that merely NAMED send_server.py / optout_sync.py / foreclosure_leads.py / a codes file in a grep or wc. Did the allowed thing: used the Grep/Read tools instead, imported send_server in a script file (import is allowed)
17:20 CHECK  merged-not-visible: git log -- docs/call/index.html -> newest build 42a48ea1 2026-11-04T21:30:53-05:00 (see table at bottom)
17:25 PROOF  seat_balloon_wp.py -> Carlos seat carries 6 of 12 balloon rows and 5 of 12 rows with Whitepages-sourced numbers   (F-wire-03)
17:28 PROOF  atty_not_suppressed.py -> after "They have an attorney" hardSuppressed=[] noState.hard=false                       (F-wire-01)
17:31 PROOF  dnc_bridge_down.py -> DNC saved on phone, 1 /notes attempt refused, 0 toasts, 0 retry timers                        (F-wire-02)
17:34 PROOF  disk_first_wins.py -> note/next/nextTs/textopen keep the OLD value on disk                                         (F-wire-04)
17:36 PROOF  funnel_attempts.py -> 5 leads worked in Call Mode, board attempts = 1                                              (F-wire-08)
17:38 PROOF  coach_cors.py -> Access-Control-Allow-Origin: * on an unauthenticated lead search                                   (F-wire-07)
17:40 RAN    _clienttest, _equityroutetest, _callstatstest, _seatcoveragetest, _householdseattest -> all pass; _callpickuptest browser half SKIP (no Chromium here)
17:45 WROTE  worklog-wire.md (this file), 10 tickets
```

## Tickets

### F-wire-01  "They have an attorney" is a route-only flag: the lead stays dialable, textable and in the Worker on both seats
- Type: FINDING
- Area: call-mode
- Severity: S1
- Evidence:
  - `call_mode.py:9312-9318` `setRouteAtty()` writes only `n.atty` (date) and `n.route={k:'none',why:'attorney'}`. It sets no status, no `no`, no `optout`.
  - The only reader of `atty` anywhere is `routeAtty()` -> `routeBlock()` (`call_mode.py:9289-9301`), which only decides whether the lead may be put on the mail or knock lists. `hardSuppressed()` (`5174-5190`), `noState()` (`5258`), `supReason()`/`suppressed()` never look at it. `tracker_template.html` reads `.atty` only inside `_mergeLead` (17770: merge, no behaviour). `send_server._merge_note` (943-945) only keeps it sticky. `optout_sync.ledger_from_notes` (267-288) triggers on status / optout / `no==hard` only.
  - Command and output (`python ...\wire-proofs\atty_not_suppressed.py`):
    ```
    before: hardSuppressed=[] noState.hard=false
    note after tap: {"status":"","note":"","atty":"2026-11-08","route":{"k":"none",...,"why":"attorney"}}
    after tap: hardSuppressed=[] noState={"hard":false,"soft":false,"open":false,"why":""}
    routeBlock (only reader of atty)=[has an attorney]
    ```
  - The same after-call screen still builds the Text button right under the attorney button (`call_mode.py:9841-9843`, text gates at 9757-9794 do not read `atty`).
  - Contrast: the script's own lawyer step says "Log it Do Not Contact" (`call_mode.py:983-988`, coach text in `call_coach_live.html:263`), so the operator's rule for "I have a lawyer" is DNC. The panel button is a second, weaker path to the same fact.
- Repro: run the proof script (extracts `setRouteAtty`, `hardSuppressed`, `noState`, `routeBlock` from `call_mode.py` and runs them under node, no network).
- Proposed fix (smallest): decide with Alex whether "has an attorney" means stop contact. If yes, `setRouteAtty` should also call the same path as the DNC outcome (`stopEverywhere` + `n.no='hard'`), or `hardSuppressed()` should return 'has an attorney' when `n.atty`; the board's `isFlaggedDead`/`_effOptout` need the same read. If "route only" is intended, relabel the button and the confirm text so nobody believes it stops the calls, and add a text/worker gate. Test: extend `_equityroutetest.py` with `hardSuppressed(r)` non-empty after `setRouteAtty`. The `hardSuppressed`/`noState` edit touches the callmode-dev "no / dnc / notint branches" exclusion in OWNERS.md and the board suppression functions, so route it to Alex.
- Owner role (OWNERS.md): callmode-dev for the button; the suppression half is report-only
- Touches: DNC, suppression, send (text, worker), copy (button label)
- Confidence: high on the mechanism (negative grep plus the run), medium on severity (the label says "do not route", so route-only may be intended; Alex's own lawyer rule says DNC)

### F-wire-02  A Do Not Contact tapped on a phone that cannot reach the bridge is saved locally, never ledgered, and says nothing
- Type: FINDING
- Area: call-mode (also bridge)
- Severity: S1
- Evidence:
  - The confirm promises it: `call_mode.py:9137` "This closes every channel, permanently, on every device."
  - `stopEverywhere` (9226-9257) writes `status:'DO NOT CONTACT'`, `no:'hard'`, `optout`, `dntph` locally. `saveNotes` (4803-4813) then calls `callNotesPushSoon`; `callNotesPush` (6132-6141) posts to `_bridgeBase()` = `http://127.0.0.1:8823` unless `dfBridgeBase` was typed in, which on a phone is the phone itself. Both promise callbacks only reset `_cnBusy`: no toast, no retry timer, no stored "unsent" flag, and the HTTP status is never read (a 400, a 500, or `ok:false`, `ledgered_dnc:0` from `send_server._handle_notes` 2652-2679 is just as silent).
  - The only other route to `optouts.json` is a board tab open on the bridge computer that pulls the team sync and re-posts `/notes`. `grep -ic supabase` over the Python files: only `call_mode.py` (7). Nothing server-side pulls the team store. Every email/text gate reads `optouts.json` or `worker_notes.json` (`cadence.py:770`, `outreach_email.py:443,583`, `send_server.py:2470`).
  - Command and output (`python ...\wire-proofs\dnc_bridge_down.py`, fetch stubbed to reject like a phone with no bridge):
    ```
    local DNC status on note: DO NOT CONTACT, optout=2026-11-08
    fcLeadNotes saved in phone storage: yes
    /notes attempts: 1 to http://127.0.0.1:8823/notes
    toasts shown: 0  retry timers queued after the failure: 0
    _cnBusy reset: true
    ```
  - The page already knows how to behave on a phone for text (`textPreflight` 6185-6192 has a "phone and bridge unreachable" branch); the notes push has no such branch. Documented in WIRING-MAP.md hop 3 as "a fetch rejection is swallowed", but the DNC consequence (a stop that never reaches the ledger unless a board tab is open) is not in the "Known silent drops" table.
- Repro: run the proof script. For the server half, post a payload with one DNC note to a test bridge (the way `_e2ewiringtest.py` does) and compare `ledgered_dnc` in the reply.
- Proposed fix (smallest): in `callNotesPush`, read the response; on a reject, a non-200 or `ledgered_dnc` shortfall, keep an `fcNotesUnsent` stamp, show a persistent strip ("Not backed up to the bridge: N changes, including a Do Not Contact") and retry with the same 4/8/12 s ladder `queueSync` uses. Test: new case in `_e2ewiringtest.py` (fetch rejects, assert the strip text and one retry). Bridge-side half (make `/notes` return `ok:false` when `ledger_from_notes` raised) is `send_server.py`, report-only.
- Owner role (OWNERS.md): callmode-dev (page); send_server half report-only
- Touches: DNC, suppression, send
- Confidence: high on the page behaviour (run), medium on exposure (depends on whether the phones have `dfBridgeBase` + key set and how often a board tab is open; I cannot see the phones)

### F-wire-03  Carlos's page carries balloon rows and Whitepages-sourced numbers; its nav strip links the Balloon Book
- Type: FINDING
- Area: carlos-seat
- Severity: S4
- Evidence:
  - `call_mode.py:2702` gives every row, balloon rows included (`row['st']='BAL'`, 2656), a `sb = hash(case) % 12` bucket. `seat_rows` (3854-3864) keeps `sb % 2 == i`; `make_callmode` (3924) applies it. Nothing excludes `st=='BAL'` or WP fields for `i==1`. Row field `sx` (2518) tags each number w/h/n for Whitepages owner/household/name-only.
  - `dealflow_nav.py:46` puts `hm-balloon-q7v3n8/` in every Call Mode strip, Carlos's included. That page is a static, unencrypted public page (`docs/hm-balloon-q7v3n8/index.html`, 86 KB, 151 table rows, 0 phones, 0 emails by regex count; no unlock script).
  - The Carlos page also ships the Whitepages "Find a number" lookup button (`wpLookup`, `/wp/lookup`, paid) because no code branches on `SEAT.i` (grep `SEAT\.i|SEAT\.w` shows only labels and warnings).
  - Command and output (`python ...\wire-proofs\seat_balloon_wp.py`, 12 invented balloon + 12 invented FC leads, half with phsrc `wp`):
    ```
    Alejandro rows 12 BAL 6 ps containing wp 0
    Carlos rows 12 BAL 6 ps containing wp 0
    Alejandro rows with sx containing w/h/n: 1 of 12
    Carlos rows with sx containing w/h/n: 5 of 12
    ```
  - `_carlostest.py` only asserts the lane predicate for balloon rows, not their absence from Carlos.
- Repro: run the proof script.
- Proposed fix (smallest): this is Alex's rule (AUDIT-LOOP-PROMPT rule 5, PROTOCOL section 8), and the code never enforced it. Decision for Alex first: is "never gets" a rule for future changes or a statement about today? If today: in `make_callmode`, when `seat[1] != 0`, drop `st=='BAL'` rows, strip `sx`/`pw`/`wpr`/`ws` and the Find-a-number lane, and drop the Balloon Book strip item for that seat (a `dealflow_nav.strip(..., skip=('balloon',))` argument). Test: `_carlostest.py` asserts zero BAL rows and no `sx` in the Carlos payload. Note the publish-side exposure (balloon page is plaintext on the public site) belongs to `sec`.
- Owner role (OWNERS.md): callmode-dev (`call_mode.py`); `dealflow_nav.py` unowned
- Touches: Carlos-seat, copy
- Confidence: high on the data (run), low on whether Alex wants it (decision needed). Add to DECISIONS-NEEDED.

### F-wire-04  On disk, `note`, `next`, `nextTs` and `textopen` keep the FIRST value forever; later callbacks and typed notes never reach worker_notes.json
- Type: FINDING
- Area: bridge
- Severity: S3
- Evidence:
  - `send_server.py:946-951` `_merge_note` copies any other key only `if k not in out` (first write wins). `note`, `next`, `nextTs`, `textopen` are not in the arrays or the after-the-yes unit. The new-case path (`_merge_payload` 961-967) stores the whole first note, which Call Mode and the board create as `{status:'', note:''}` (`call_mode.py:10031`), so `note` exists as `''` from the first push on and never updates.
  - Writers: `call_mode.py:8910-8919` `setCallback` sets `n.next`, `n.nextTs` and appends to `n.note` on every callback chip; board `tracker_template.html:20199` writes `notes[c].note` from the notes textarea (19107); board `_setNext` writes `n.next`.
  - Disk readers: `deal_desk.py:103-139` (note, next, next_due), `reports/followup_gaps.py:45` (next), `sheets_crm.py:253-258,411-412` (Follow-up, Notes), `outcome_learn.py:105` (open-deal next step). There is no GET /notes; the board never restores from disk.
  - WIRING-MAP lists first-wins for `wpe`, `replied`, `wq`, `no`; not `note`/`next`/`nextTs`/`textopen`.
  - Command and output (`python ...\wire-proofs\disk_first_wins.py`, calls `send_server._merge_note` and `_merge_payload` directly, temp DEALFLOW_DIR):
    ```
    note | disk kept: '2026-11-08 callback set tomorrow' | newer incoming: '...\n2026-11-10 callback set in 3 days' | KEPT_OLD
    next | disk kept: '2026-11-09' | newer incoming: '2026-11-13' | KEPT_OLD
    nextTs | disk kept: 1 | newer incoming: 2 | KEPT_OLD
    textopen | disk kept: '2026-11-08' | newer incoming: '2026-11-10' | KEPT_OLD
    --- empty first, text later
    disk note after merge: '' | incoming: 'owner works nights, call after 6pm'
    payload note: '' {'new_cases': 0, 'new_rows': 0}
    ```
- Repro: run the proof script.
- Proposed fix (smallest): in `_merge_note`, take `next`/`nextTs` from the side with the larger `nextTs` (or newer activity) and `note` from the longer/newer side (the page appends, so "incoming startswith current" means newer). Test: a case in `_e2ewiringtest.py` and `_equityroutetest.py`-style merge test. `send_server.py` is report-only, so this goes to Alex as APPROVED-REPORT with LEGAL REVIEW (same function ledgers DNC).
- Owner role (OWNERS.md): report-only (send_server.py)
- Touches: none directly (no send); data the deal desk and Sheets CRM read
- Confidence: high (function run directly); medium on severity (backup/report readers, not the live dialer)

### F-wire-05  A missing clients.json is silent: every client goes back into the dial lanes with no message
- Type: FINDING
- Area: engine (build) / call-mode
- Severity: S1
- Evidence:
  - `clients.py:43-52,100-110`: a missing file returns `{}`, sets no error, prints nothing; only an unreadable file prints. `foreclosure_leads.py:4106-4114` prints the "clients: N of M" line only when something was stamped or the list is non-empty, so a missing list prints no line at all.
  - `clients.json` is gitignored (`.gitignore:339`); it travels in the transfer bundle (`make_transfer.py:44`) and nowhere else. No gate looks for it: `grep clients` over `healthcheck.py`, `publish_guard.py`, `pipeline_alerts.py`, `quick_publish.py`, `publish_site.py`: 0 hits.
  - Command and output:
    ```
    python -c "import clients ... clients.load('C:/nonexistent/clients.json') ..."
    missing file -> {} stamped 0 printed: '' error(): ''
    clients.json in this worktree: absent (gitignored); in the main checkout: present
    ```
  - Protocol severity table lists "a client" under S1. `clients.py` itself calls a reappearing client "a nuisance, not a legal wall", so Alex may rate it lower.
- Repro: the command above; or build `call_mode.call_rows` on a row with `client:1` and the same row without it.
- Proposed fix (smallest): add one `pipeline_alerts` line / healthcheck advisory: "clients.json missing on the build machine" (advisory exit 1, not a block), and have `foreclosure_leads` print `clients: no list on this machine` when the file is absent. Test: extend `_clienttest.py`. Do not make it a hard block without Alex.
- Owner role (OWNERS.md): board-dev / equity-dev area (build print); healthcheck is a publish gate, so report to Alex
- Touches: send (a client worked as a lead), Carlos-seat (both seats)
- Confidence: high on silence (run); medium on exposure (the desktop checkout has the file today; a rebuild on any machine that lacks it, including the new laptop after stand-up, would not warn)

### F-wire-06  clients.json is a build-time list only: the send bridge and cadence never read it
- Type: FINDING
- Area: bridge
- Severity: S1 (conditional: follow-ups are off by default, `DEALFLOW_FOLLOWUPS_ON` unset)
- Evidence:
  - Readers of `clients`: `call_mode.py` (drops the row), `tracker_template.html` (`isFlaggedDead`, `_boardMailHeld`, `_funnelStage`), `outreach_email._eligible` (first touch only, 637), `three_day`, `morning_planner`, `skiptrace`, `whitepages_lookup`, `wp_contacts`.
  - Grep count of `clients` in `cadence.py`, `send_server.py`, `mail_guard.py`, `text_hold.py`, `reply_send.py`: 0 matches in all five. Cadence follow-ups run from `cadence_state.json` (enrolled earlier, `cadence.py:665-691`) and `send_server /send` and `/text` gate on ledger, stay, bounce, hold, but not on clients. Cadence has its own separate skip list (`load_skip`, `cadence.py:410-438`) that must be updated by hand.
  - Result: a client added to `clients.json` after a first touch (the normal case: someone we mailed turns into a client) stays in cadence state and keeps getting steps 2-4 once follow-ups are switched on; and any caller of `/send` or `/text` that bypasses the board is not stopped.
- Repro: `grep -c clients` on the five files (use the Grep tool; the guard blocks Bash commands that name `send_server.py`).
- Proposed fix (smallest): decision for Alex whether a client should be sent a follow-up. If no: in `cadence.py`'s send-time sweep (the section at 693-760) and in `send_server` `/send` + `/text` gate add `clients.is_client({'case': c})` -> held 'existing client' (fail open if the file is missing, same as the picker). Test: `_clienttest.py` plus a `_sendbridgetest.py` case. Cadence and `send_server.py` are on the owned/report-only surface.
- Owner role (OWNERS.md): report-only
- Touches: send, suppression (adjacent: not a DNC verdict), copy none
- Confidence: high on "never read" (0 hits), medium on exposure (follow-ups are off; first touch is gated)

### F-wire-07  The live call coach answers any web page: CORS `*`, no auth, lead search and session list on loopback
- Type: FINDING
- Area: engine (new in 4ed06b5d / a96a4af1; merged, visible after the next publish for the Call Mode link only)
- Severity: S4
- Evidence:
  - `call_coach_server.py:233` sends `Access-Control-Allow-Origin: *` on every response; there is no key, no Host or Origin check; GET `/api/lead?q=` searches `leads_final.json` (address, owners, case, people name, mailing address) and returns owners, address, judgment, plaintiff, equity (`lead_card`, 80-92); `/api/sessions` lists call recordings; POST `/api/session/start|audio|transcript|end` creates folders and writes audio with simple (preflight-free) requests.
  - Command and output (`python ...\wire-proofs\coach_cors.py`: the module's own Handler on a free loopback port, one invented lead, temp dirs, forged `Origin: https://evil.example` and `Host: attacker.example`):
    ```
    status 200 | Access-Control-Allow-Origin: * | matches returned: 1
    fields in a card: ['address','auction','case','days','equity','homestead','judgment','opening_bid','owners','plaintiff','score','tier']
    sessions list status 200 *
    ```
  - Any page open in the operator's browser while the coach is running can read the lead book through it (browsers permit calls to 127.0.0.1 from a public page unless blocked by Private Network Access, which is not guaranteed on every browser), and a DNS-rebinding host name is accepted.
  - Not run live: nothing was started on port 8777.
- Repro: run the proof script.
- Proposed fix (smallest): drop the ACAO header (the page is same-origin), reject any request whose `Host` is not `127.0.0.1:<port>` or `localhost:<port>`, and require a per-run token in the URL that Call Mode's Coach link does not need (the link is for the laptop only). Test: new `_coachservertest.py`. A new file nobody owns; Alex should rule on whether the coach is meant to run at all (call recording, consent) before any fix.
- Owner role (OWNERS.md): unowned (new module); recording/consent copy is legal
- Touches: none (no send); personal data, possibly legal (call recording)
- Confidence: high on the headers and the open lookup (run); medium on exploitability from a browser (PNA behaviour varies)

### F-wire-08  Board "attempts" skips every no-answer, voicemail and route (Mailed / Knocked) lead: 5 worked leads show as 1
- Type: FINDING
- Area: board
- Severity: S5
- Evidence:
  - `tracker_template.html:4241` `if(!st && rung < 0) return;` leaves out any lead with no status and no after-the-yes stage. No Answer and Voicemail leave `status` empty (`call_mode.py:10087-10113`); the new route touches (`call_mode.py:9328`, `ch:'letter'|'door'`, `src:'route'`) leave it empty too. WIRING-MAP hop 7 says this was code-read, not asserted, and did not cover the route work.
  - Command and output (`python ...\wire-proofs\funnel_attempts.py`, the board's own `_funnelStats` extracted and run under node on 5 invented notes: no-answer, voicemail, route letter mailed, route door knocked, talked):
    ```
    board _funnelStats -> {"attempts":1,"contacts":1,"appointments":0,...}
    ```
- Repro: run the proof script.
- Proposed fix (smallest): count a lead as an attempt when it has any `dials[]` row with a non-pending `oc` or any touch with `ch` in call/letter/door/text/email, and keep `contacts` as is. Test: extend `_funneltest.py`/`_e2ewiringtest.py` (turn the KNOWN GAP into a normal check). `_laneStats` and neighbours are the owned suppression surface; `_funnelStats` is not in the list but sits beside them, so keep the edit to that function.
- Owner role (OWNERS.md): board-dev
- Touches: none
- Confidence: high

### F-wire-09  The equity re-route "Mail a letter / Knock" choice is read only by Call Mode's own two lists; nothing that mails or plans doors reads it
- Type: FINDING (design gap, S5)
- Area: call-mode
- Severity: S5
- Evidence:
  - `route` is written by `call_mode.py:9304-9337`, merged by `send_server.py:930-945` and `tracker_template.html:17763-17770`, and read only by `routeOf/routeLists` (`call_mode.py:9285-9375`). Grep of the Python files for `get('route')`: only `send_server.py` (merge) and `_equityroutetest.py`. `bsg_mail_campaign`, `bsg_daily_routes`, `morning_planner`, the board mail queue and the funnel LETTER / DOOR lanes do not read it. The mail list's own text tells the operator to use the letter from `bsg_mail_campaign` by hand.
  - So a lead routed to Mail: the board shows nothing, the campaign does not pick it up, and "Mailed" writes a `letter` touch that the board funnel does not count (F-wire-08).
- Repro: Grep `get\('route'\)` over `*.py`.
- Proposed fix (smallest): none needed if the lists are a hand to-do (say so on the board card); otherwise have `morning_planner`/`bsg_daily_routes` read `n.route` from `worker_notes.json`. Alex's call; either is a new feature.
- Owner role (OWNERS.md): callmode-dev / board-dev
- Touches: none
- Confidence: high that nothing reads it; the intent is unknown

### F-wire-10  Coach outcomes dead-end: a different vocabulary, no Do Not Contact button, nothing reads outcomes.jsonl
- Type: FINDING
- Area: call-mode (coach)
- Severity: S5
- Evidence:
  - `call_coach_live.html:231-247` picker offers talked / appointment / callback / not_interested / voicemail / bad_number / skip. Call Mode's keys are talked / appt / callback / notint / voicemail / badnum / dnc / wrong. `call_coach_server.py:186-214` writes `outcome` to `meta.json` and `call_sessions/outcomes.jsonl`; no reader of `outcomes.jsonl` exists in the repo (Grep). The picker text itself says "Log the same outcome in Call Mode so the cadence moves."
  - There is no stop / do-not-contact choice in the coach picker even though the coach's own consent line (`call_coach_live.html:263`) tells the operator to "mark Do Not Contact" on a lawyer. A stop said on a coached call is recorded nowhere unless the operator also taps Call Mode's button. The auto-stop after 90 s of quiet (`call_coach_live.html:762`) finishes the session with outcome `auto_silence`.
- Repro: Read the two files; no run needed.
- Proposed fix (smallest): add a "They asked to stop" button to the picker that opens Call Mode's DNC path for that case (or at least shows a red reminder), and map the vocabulary; Alex decides whether the coach is used at all before any change.
- Owner role (OWNERS.md): unowned (new module)
- Touches: DNC (adjacent), copy
- Confidence: high on the mechanism, low on how often it matters

## Hop table (extends docs/agent-team/WIRING-MAP.md; that map's hops 1-9 still hold on aac7a8a9, `_e2ewiringtest.py` ALL PASS)

| page | action | function (file:line) | route | handler | file written | reader | proof command | status |
|---|---|---|---|---|---|---|---|---|
| Call Mode | outcome buttons (talked, no answer, callback, appt, notint, gate) | `logOutcome` call_mode.py:10030 | `POST /notes` via `saveNotes` 4803 -> `callNotesPush` 6132 | `send_server._handle_notes` 2624 -> `_write_notes` -> `_merge_payload` 955 -> `_merge_note` 836 | worker_notes.json | `outcome_learn.compute`, `call_stats.py`, board `_funnelStats` | `python _e2ewiringtest.py` -> ALL PASS | OK on disk; DROPS on board count for no-answer (F-wire-08) |
| Call Mode | Do Not Contact | `logOutcome` o.k==dnc -> `stopEverywhere` 9226 | `POST /notes` -> `ledger_from_notes` (optout_sync.py:267) | same | worker_notes.json, optouts.json (via sweep) | cadence, `/send`, `/text/check`, board | `wire-proofs\dnc_bridge_down.py` | DROPS when bridge unreachable, silent (F-wire-02) |
| Call Mode | They have an attorney | `setRouteAtty` 9312 | `POST /notes` | `_merge_note` 943 (sticky date) | worker_notes.json `atty`, `route` | only `routeBlock` 9295 | `wire-proofs\atty_not_suppressed.py` | BROKEN as a stop (F-wire-01) |
| Call Mode | equity re-route Mail / Knock / Neither | `setRoute` 9304, `routeMark` 9321 | `POST /notes` | `_merge_note` 930-942 (newest ts wins) | worker_notes.json `route`, touches `src:route` | Call Mode `routeLists` 9345 only | `python _equityroutetest.py` -> 0 failures | OK write; reader island (F-wire-09); not a board count (F-wire-08) |
| Call Mode | callback chips (3 h / tomorrow / 3 days) | `setCallback` 8903 | `POST /notes` | `_merge_note` fill-only 946 | worker_notes.json `next`, `nextTs`, `note` (first write only) | deal_desk, followup_gaps, sheets_crm, outcome_learn | `wire-proofs\disk_first_wins.py` | DROPS (F-wire-04) |
| Call Mode | notes (Complete sheet) | `cmpLog` 7163 | `POST /notes` | touches array union | worker_notes.json touches | board mergeNotes, outcome_learn (no) | `_e2ewiringtest.py` | OK; known gap D18 (no mail/text ledger) and Complete-only not an attempt |
| Call Mode | Text (sms: link) | `afterCall` tx 9910, `textPreflight` 6176, `callTextLedger` 6145 | `POST /text/check`, `POST /text` | send_server text handlers | text_sent.json | 3-touch ladder, board | `_e2ewiringtest.py` (/text, confirmed once) | OK; `callTextLedger` swallows a refusal (known); atty not a gate (F-wire-01) |
| Call Mode | Voicemail button / full-screen script | `vmlog` -> `logOutcome` voicemail | `POST /notes` | same as outcomes | worker_notes.json | same | `_e2ewiringtest.py` | OK; leaves status empty (F-wire-08) |
| Call Mode | They picked up (talk time) | `logOutcome` 10064-10079 (`tsa`, `talk`) | `POST /notes` | `_ARRAY_KEYS` dials key (ts, ph4): the `pending` row pushed at dial time stays on disk beside the outcome row | worker_notes.json dials | `call_stats.py` (resolves pending), `outcome_learn.marker_counts` (links by `tsd`) | `python _callstatstest.py` -> ok | OK (readers already handle the duplicate); browser half of `_callpickuptest` not run (no Chromium) |
| Call Mode | paste an email | `mailViaBridge` 7263 | `POST /send` | send gate | mail_sent.json | cadence, ledger | read only (no send run) | OK: refusal reason is shown |
| Call Mode | after-call picker (Next lead, number walk) | `afterCall` 9709 | none | none | local | local | read | OK |
| Call Mode | CLIENT banner / client rows | `call_rows` 2247 drop; board `_callSheet` banner | none (build time) | `clients.stamp` in make_tracker 4106 | `client:1` in rows | board, Call Mode | `python _clienttest.py` -> ALL PASS | OK with list present; DROPS silently when file missing (F-wire-05); not read at bridge/cadence (F-wire-06) |
| Call Mode | Coach link | `call_mode.py:8195` | `127.0.0.1:8777` | `call_coach_server.Handler` | call_sessions/ (gitignored) | nobody | `wire-proofs\coach_cors.py` | DROPS (F-wire-10); open to any page (F-wire-07) |
| Call Mode / Carlos | page strip (Board, Call Mode, Carlos, Worker, Balloon Book, Playbook) | `dealflow_nav.links` 36 | static hrefs | board handles `#playbook` (tracker 20466), `#worker=open` (20504); balloon page exists | none | none | read; every target path exists under docs/ | OK |
| Carlos seat | page build | `make_callmode` 3867, `seat_rows` 3854 | none | `sb = hash % 12` 2702 | docs/call/carlos/index.html | Carlos's phone | `wire-proofs\seat_balloon_wp.py` | BROKEN vs the rule (F-wire-03) |
| Board | Morning Worker "Send to Call Mode" | `sendToCallMode` tracker 14240 -> `post('callq',{qc,hand:1})` | opener postMessage, else `POST /mark` | board replay stamps `.wq` (15888) -> team sync | worker_marks.jsonl, notes `wq` | Call Mode Worker lane | `_e2ewiringtest.py` (/mark, /marks, consumed) | OK; needs a board tab open (documented); `wq` first-wins on disk (D15) |
| Board | funnel counts | `_funnelStats` 4227 | none | none | none | board header | `wire-proofs\funnel_attempts.py` | DROPS Call Mode work (F-wire-08) |
| Board | Balloon Book nav | `dealflow_nav` | static | none | docs/hm-balloon-q7v3n8/ | public page | read | OK (page is plaintext and public: for `sec`) |
| Bridge | `/notes /mark /text /send /health` | `do_POST` 2681 | | | | | `python _e2ewiringtest.py` -> ALL PASS | OK |

## Merged is not visible: live pages vs main

Live `docs/call/`, `docs/call/carlos/`, `docs/index.html` last built `42a48ea1` (2026-11-04 21:30:53 -05:00, "refresh: auto lead + phone update"; `git log -- docs/call/index.html`). Main is `aac7a8a9` (2026-11-08). Merged since, changing what a rebuild would produce, and not on the phones or the board until the next publish ("merged, visible after the next publish"):

| commit | what | affects |
|---|---|---|
| 4ed06b5d, a96a4af1 | live call coach + Coach link on every Call Mode card | Call Mode (link only; the coach itself is a local server) |
| 2329cf05, 6f1879fe | opener asks instead of tells; "I have a lawyer" one-line exit | Call Mode script (homeowner-facing copy; LEGAL REVIEW class) |
| 7e117a60 (#443) | equity re-route Mail / Knock, route lists, attorney button | Call Mode, board `_mergeLead`, bridge `_merge_note` (F-wire-01, F-wire-09) |
| 099a0572 | "They picked up" talk time, call-stats columns | Call Mode, `call_stats.py` |
| 20e546b6 | clients.json: client rows leave every lane, CLIENT banner | Call Mode, board, pickers (F-wire-05, F-wire-06) |
| 5dc433db | restored the approved just-filed text | board and Call Mode text bodies |
| 98166e8e, 8d2d674f, c6c31d20, 8c69de5f, #444/#445/#447/#449/#450 | held cold-email copy, alerts, docs, agent loop | no page change |

Not verified: whether the bridge computer's `send_server.py` has already self-restarted onto the new `_merge_note` (route/atty merge, 930-945); that needs the machine, not the repo.

## OUTPUT-TEMPLATE

```
### wire-2026-11-08: trace board -> Call Mode / Carlos seat -> Worker -> bridge -> worker_notes -> funnel
- Task: prove where data silently drops or a button does nothing, across board, Call Mode, Carlos seat, Morning Worker, Balloon Book, Playbook and the bridge
- Owner: wire (wiring-auditor), read-only
- Branch + PR: none (detached worktree %USERPROFILE%\projects\fl-loop-wire @ aac7a8a9); tickets F-wire-01..10 in RUNDIR\worklog-wire.md, proof scripts in RUNDIR\wire-proofs\
- Files changed: none in the repo. Written outside it: worklog-wire.md, wire-proofs\*.py
- What changed (plain English): nothing in product code. Ten tickets: the Call Mode "has an attorney" button does not stop contact; a DNC tapped on a phone that cannot reach the bridge is never ledgered and says nothing; Carlos's page carries balloon rows and Whitepages-sourced numbers; follow-up dates and notes keep their first value on disk; a missing clients.json is silent and the send paths never read it; the coach server answers any web page; board "attempts" skips no-answer/voicemail/route work; two design dead ends (route lists, coach outcomes).
- Tests run (command + result): `python _e2ewiringtest.py` -> ALL PASS (7 known gaps asserted); `_clienttest`, `_equityroutetest`, `_callstatstest`, `_seatcoveragetest`, `_householdseattest` -> pass; six proof scripts (outputs in the tickets). All with a temp DEALFLOW_DIR, no network, no real port. `_callpickuptest.py` browser half skipped: no Chromium installed here, so it is not evidence.
- QA verdict (PASS/FAIL + by whom): none yet; QA verdict is written by the gate, not by wire
- Exposure: sending (F-wire-01, -02, -06: contact after a stop, an attorney flag or a client), suppression (F-wire-01, -02), none for the rest. Report only; nothing was changed.
- Flags (default off?): none added
- Owned-surface bugs found: F-wire-04 and F-wire-06 sit in send_server.py (report-only); F-wire-01 touches the board suppression readers and Call Mode's no/dnc branches; F-wire-02's bridge half is send_server.py. Reported, not fixed.
- Needs Alex (one line each): is "has an attorney" a stop (F-wire-01)? does the Carlos rule mean balloon + Whitepages are hidden today (F-wire-03)? should a client get cadence follow-ups (F-wire-06)? is the call coach meant to run, given recording consent (F-wire-07, -10)?
- Decisions needed (link to the DECISIONS-NEEDED entry): not written (agents append only through chief); the four questions above are the entries to add
- Next run should know: SendMessage is not available to this agent, so tickets were delivered in the handback, not one by one. The agent_guard blocks any Bash command that merely names send_server.py, optout_sync.py, foreclosure_leads.py or a codes file: use the Grep/Read tools for those. Live pages were built 2026-11-04; everything in the merged-since table is not on the phones. Not checked: real browser clicks (no Chromium), Supabase team sync, phone-side `dfBridgeBase` settings.
```
