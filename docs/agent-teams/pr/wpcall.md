# LEGAL REVIEW: Call Mode "Look up on Whitepages" button (POST /wp/lookup)

Visible after the next refresh-dealflow.bat (the button is in Call Mode; the endpoint needs a bridge restart or the self-reload).

PAID. One tap = $0.22, up to $0.44. Capped only by the existing `WP_DAILY_CAP` ($5/day) and `WP_MAX_SPEND`; no cap changed. See D17 in docs/agent-teams/DECISIONS-NEEDED.md. Alex does the first live lookup himself to confirm the key (last 200s 2026-10-22; an older note says 403 on 10-19). Nothing in this branch called the real API.

What it does
- Find-a-number card: button "Look up on Whitepages (about $0.22)". The labelled button is the confirmation. Spinner, then the owner's numbers as "owner" or "likely owner", tap to dial. Button locks as "Looked up, N numbers" (also when cached, also with 0 numbers).
- Numbers are saved as `notes.wpm` (call-only, existing rule) and `notes.wpl {ts, n, cached, k}`. No text path.
- `send_server.py`: `POST /wp/lookup {case}`, behind the existing device-key gate. Refuses before spending: unreadable/absent opt-out ledger, case on it, worker note Dead / DNC, never_contact.json, bankruptcy/dismissed lead flags (`contact_hold`), no clear `stay_gate.check` verdict (read-only, no CourtListener/PACER call). Cached case: $0, `cached:true`. Daily cap: `blocked:'budget'`. Missing key / 403: `blocked:'key'`, never echoes the key or a header. Returns only owner (`wp`) and likely (`nm`) numbers graded by `wp_contacts`; household and relatives are never returned; numbers on the opt-out ledger as `#digits` are dropped.
- `whitepages_lookup.py`: additive only, 12 lines. `AUTH_RAISES` (default False) makes a 401/403 raise `WPAuthError` instead of `os._exit(2)`; the CLI is unchanged. There was no importable single-lead function that survives a bad key inside a server.

Tests (all stubbed): `_wpcalltest.py` (bridge, fake Whitepages, egress guard), `_wpcallmodetest.py` (node, built page). Both fail on origin/main 649ce38.
