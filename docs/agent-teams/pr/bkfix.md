# LEGAL REVIEW (stay surface): federal bankruptcy check says when its search was skipped; match step indexed

Root cause (docs/BK-CHECK-DIAGNOSIS.md): `run_nightly()` sets a 900 s deadline, then `match_filings_to_leads()` compares every lead with every cached filing and outruns it, so the per-lead search loop never runs and the status read `reason: ok, errors: 0, leads_checked: 0`.

Two commits, reviewed separately.

**Commit 1 - visibility (status and alert only)**
- Deadline passed before any lead was searched: status reason `time_budget` (other reason codes unchanged). Counts added: `needing_search`, `searched_this_run`, `match_s`, `requests_this_run`, `starved_runs`.
- Failed pull: `last_http_status`, `pull_attempts`, `pull_fail` (http_error / rate_limited / network). Backoff unchanged, no new retries.
- `pipeline_alerts.bk_lookup_alert`: warning after 3 starved runs in a row with leads unsearched.
- Counts and codes only; no token, name or party.
- Test: `_bkstatusreasontest.py` (fails on 64fe209, passes after).

**Commit 2 - speed (see run log for the numbers)**
- Filings indexed by name token; a lead is compared only with filings sharing a token. Output identical to the full cross product (`_bkmatchindextest.py` runs the old matcher and the new one on the same data).

Exposure: stays (read-only effect on holds: none). No lead is released or held differently. Decision entry: DECISIONS-NEEDED D19.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
