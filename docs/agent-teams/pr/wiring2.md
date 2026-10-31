# Wiring map + end-to-end wiring test (no product code)

Adds `docs/agent-team/WIRING-MAP.md` (every hop, function:line, endpoint, fields, silent drops with decision ids)
and `_e2ewiringtest.py`. Nothing else changes. Not a LEGAL REVIEW item: no product code, no
`send_server.py` edit. The test does start `send_server.py` in a temp copy on a free port, with a temp
`DEALFLOW_DIR`, a temp fresh `optouts.json`, `smtplib` replaced by a raising stub and non-loopback requests refused.

## What the test proves
Invented leads (one GUESS equity card, two verified) -> `call_rows`/`build_html` -> the built page's own JS under
node (Appointment save, after-the-yes panel, Complete sheet, `saveNotes`, `callNotesPush`, `callTextLedger`,
`bridgeFetch` + device key) -> those exact requests replayed to a real bridge -> `worker_notes.json` -> board
`_funnelStats` / `_mergeLead` under node and `outcome_learn` counts. Gate: Offer made is refused on the GUESS
lead. Device key: a proxied request with no key or a wrong key is 401 and writes nothing.

## Known gaps, asserted as expected (the check fails when one is fixed)
D18 hand-logged Emailed not in `mail_sent.json`; D15 `wpe` / `replied` / soft-over-hard `no` on the disk merge;
D17-related `wpm` not merged; Complete-sheet-only lead not an attempt on the funnel or in `outcome_learn` (no
decision id yet).

## Run
`python _e2ewiringtest.py` (about 5 s, offline). Also rerun: `_callwiringtest _afteryestest _boardmergetest _mergenotetest _verifytraintest`.

Decisions needed: none new (no send_server.py / suppression change). Two gaps have no D-number; Lead to decide.
