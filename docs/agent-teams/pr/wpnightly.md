# Nightly Whitepages web lookup (off by default)

Automates `wp_web.py --pick-callable N` as one new step `[3w/5]` of `refresh-dealflow.bat`, before the `[4/5]` rebuild, so new leads get owner phones and emails the same night. Tracerfy is unfunded, so this is the owner-phone source.

**OFF unless `WP_WEB_NIGHTLY=1`.** Merging changes nothing until Alejandro sets it.

## Terms / ban-risk reminder
whitepages.com's terms forbid automated access and commercial use; this can get the account suspended or banned. Alejandro accepted that in writing on 2026-10-22 and said "go on and automate it now" on 2026-11-02. The cap, throttle and stop rules below exist to look human and to stop early, not to make it allowed.

## What changed
- `refresh-dealflow.bat`: one step `[3w/5]` with a `refresh_progress.py step 3w` marker, `>> "%LOG%" 2>&1`, guarded on `WP_WEB_NIGHTLY=1`. Cap N comes from `python wp_web.py --nightly-cap` (`WP_WEB_NIGHTLY_MAX`, default 15, hard max 25; falls back to 15). A nonzero exit logs one line and the refresh continues.
- `wp_web.py`:
  - total wall-clock cap `WP_WEB_MAX_RUNTIME_S` (default 900), checked between leads; on cap it keeps what it has, reason `time_budget`, exit 0.
  - a login wall, bot check/captcha, HTTP 403/429 or "sign in" page stops the whole run at once (`_Stop`): no retry, no solving, no workaround. Exit 0.
  - `wp_web_status.json` in `DEALFLOW_DIR` (via `paths.py`): `ts`, `reason` in {ok, time_budget, login_required, challenge, rate_limited, error}, `attempted`, `cached`, and `cooldown_until` for challenge/rate_limited. No name, phone, email, address or case number.
  - after challenge or rate_limited, automated runs do nothing for 24 h. `--plan` is not blocked; `--ignore-cooldown` overrides by hand.
  - never types or stores a password (unchanged).
- `pipeline_alerts.py`: one new check `wp-web`. Active only with `WP_WEB_NIGHTLY=1`; WARN (not fail) on login_required / challenge / rate_limited / error, a missing status, or a status older than 36 h; silent when off and on `ok` / `time_budget`. Existing checks untouched.
- `_wpwebnightlytest.py` (new).

## Behavior change to know
Before this, a "sign in" page with nothing parsed counted as a miss and the run carried on to the next lead. It now stops the run. Same for a login-URL redirect.

## Publish path check
The new step does not `git add`, commit, push or publish anything; it only writes `whitepages_lookup.json` and `wp_web_status.json` (both gitignored / outside the repo). `healthcheck.py` and `publish_guard.py` lines are untouched and still run after the rebuild (asserted in the test). Not a legal surface (no send_server / stay_gate / DNC / opt-out / bk_lookup).

## UNVERIFIED (could not be tested from here)
Live behavior: login persistence when unattended, and the real-page parse. Nothing was run against whitepages.com, no login was attempted. Note the browser opens headed (`headless=False`) on the laptop during the 5:30 refresh.

Alejandro must first, in this order:
1. `python wp_web.py --login` (sign in by hand once).
2. `python wp_web.py --case <one case> --save-text out.txt` and confirm the parsed counts.
3. Only then set `WP_WEB_NIGHTLY=1` (and optionally `WP_WEB_NIGHTLY_MAX`, `WP_WEB_MAX_RUNTIME_S`).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
