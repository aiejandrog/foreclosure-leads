# Laptop loss, 2026-11-05 — desktop takes over as the runner

The laptop (the only armed machine: builder, publisher, sender, every scheduled task) died on
2026-11-05. Its charging port is damaged, it takes no power and does not boot. Nothing on it can be
read live. **DESKTOP-35NNMFL becomes the runner.** This file is the inventory and the stand-up order.
`MACHINE-HANDOFF.md` §1 points here.

**Nothing has been refreshed, sent or published from the desktop.** Every DealFlow task on it is
still `Disabled`. Stand-up below waits for Alejandro's go.

## 1. Desktop state, read on the box 2026-11-05 ~16:50

| | |
|---|---|
| repo | was on `claude/lien-coverage-map-o1935p`, 1,469 behind main. Now on `main` @ `98166e8e`, fast-forwarded, 0/0 with origin |
| desktop-only work kept | branch `backup/desktop-2026-11-05` (2 unpushed commits: a `docs/index.html` access-code edit, a `.gitignore` addition), `stash@{0}` (20 modified tracked caches, 11-01), and a full copy of both plus 5 untracked scripts in `~\DEALFLOW\desktop-backup-2026-11-05\` |
| `pr53` worktree | was holding `main`; detached at `cbe33736` to free it. Its untracked file is untouched |
| tools | Python 3.11.9, Node 24.14.1, pwsh 7.6.9, git 2.53, `user.email` set |
| missing pip deps | `paramiko`, `segno` (`pip install -r requirements.txt`) |
| `engine.id` | **absent** — `engine_drift.py` alarms without it; it only knows `laptop` / `cloud` |
| scheduled tasks | 8 registered, all `Disabled`, all `Interactive`, September-era times (Phones 06:00, Replies 06:45, Cadence 09:00). **Not registered here:** `DealFlow OptoutSync`, `DealflowSendServerDaily`. `BSG Warmup` `Disabled` (last ran 10-16) |
| secret files in repo root | 13 present (gmail, bsg_gmail, captcha, tracerfy, streetview, whitepages, zerobounce, imghash keys; site.codes; sheets webhook; tracerfy mcp url; sender.json). Most are 08-20 bundle vintage — any key rotated on the laptop since is stale here |
| user env vars | only `ANTHROPIC_API_KEY`, `BRAVE_SEARCH_API_KEY`, `VENICE_API_KEY` (names checked, values not read) |
| private ledgers | `~\DEALFLOW\ledgers-private` pulled to `448b422` (laptop's last union, **11-04 21:33**) |

## 2. Lost vs. recoverable

The laptop's last pipeline push was the 11-04 refresh chain (16:58 fresh leads → 21:30 lead + phone
update → 21:33 ledger union and alerts) and its last code push was 11-05 00:36. So the refresh
started 11-04 16:49 **finished and published**; there is no half-run to recover.

**Already safe (git, private repo, or desktop):**

| what | where | as of |
|---|---|---|
| all pushed code | `origin/main` | 11-05 00:36 |
| `optouts.json`, `mail_sent.json` (4,007 sends) | private ledgers repo | 11-04 21:33 |
| board twin (plaintext board with phones) | private ledgers repo | 11-04 21:33 |
| published board (encrypted, `site.codes` decrypts) | `docs/index.html` on main | 11-04 21:30 |
| tracked caches (lis_pendens, ownership, auction_archive, comps …) | git | last laptop commit |
| worker notes / dial outcomes | Supabase team-sync store (45s note sync) and Sheets CRM | **unverified** — confirm on first board open that synced notes appear |

**Lost unless the laptop SSD is read:**

| what | consequence | fallback |
|---|---|---|
| `leads_final.json` / `leads_raw.json` newer than desktop's 09-22 / 09-14 | first refresh rebuilds from scrape | the 11-04 board twin holds the built rows |
| `skiptrace_results.json` newer than desktop's 09-24 | **phones bought since 09-24 are not in the desktop's file**; a re-skiptrace re-spends | phones are on the 11-04 board twin; Tracerfy may hold past jobs (unverified) |
| `worker_notes.json` newer than 09-21 | — | team-sync store |
| untracked caches, `~\DEALFLOW` logs/status/dossiers, `browser-profile/` county logins | slower first runs; login-gated scrapers skip until re-logged | rebuild by running |
| `call_sessions/` audio + transcripts | gone | none |
| laptop-only task definitions (OptoutSync 07:15, SendServer 07:45, RefreshCache, evening readiness, bridge autostart, quick_publish) | must be recreated | templates exist only for Refresh, Cadence, OptoutSync |
| env-var credentials set only on the laptop: `COURTLISTENER_TOKEN`, `BROWARD_CLERK_API_KEY` / `CLERK_CDS_AUTHKEY`, `PACER_USERNAME` / `PACER_PASSWORD` / `PACER_OTP_SECRET`, `BROWARD_SFTP_PASSWORD`, `TRACERFY_API_KEY`, Twilio (`TWILIO_KEY_FILE`), the Venice admin key | those scrapers/senders fail or skip | re-issue or re-read from each vendor portal. **`PACER_OTP_SECRET` is a 2FA seed: if it is gone, PACER 2FA must be re-enrolled** |
| any unpushed laptop commits or uncommitted edits | gone | none |

**Reading the SSD** (USB enclosure, ~$10–15) would recover the second table except vendor keys you
would rather rotate anyway. Two caveats: Windows 11 device encryption / BitLocker is often on — the
recovery key is at account.microsoft.com/devices/recoverykey — and the env vars live in the user
registry hive (`NTUSER.DAT`), readable but not a copy-paste. Task XML is in `C:\Windows\System32\Tasks`.

## 3. ⚠ The two-machine rule now points the other way

The laptop's tasks are still `Ready` with `StartWhenAvailable=true`. **If it is ever repaired and
boots on a network, it catches up every missed run**: a refresh and publish from a 11-04 checkout,
and outreach sent against its 11-04 opt-out ledger while the desktop writes a newer one. Before it
boots anywhere: network off (Wi-Fi off, no cable), then
`pwsh .\desktop-setup\install-tasks.ps1 -DisableLocal`, then enumerate to confirm all `Disabled`.
Tell a repair shop not to connect it.

## 4. Stand-up order (on Alejandro's go — none of this has run)

1. **Credentials (Alejandro, never in chat).** For each missing name, in pwsh:
   `$n='COURTLISTENER_TOKEN'; [Environment]::SetEnvironmentVariable($n,(Read-Host $n -MaskInput),'User')`
   Then **sign out and back in** — running processes and Task Scheduler do not see new user env vars until then.
2. `pip install -r requirements.txt` (adds paramiko, segno).
3. Decide `engine.id` (one line). The desktop is taking the laptop's role, so `laptop` is the
   likely value — it names the role, and `engine_drift.py` knows no other non-cloud value.
4. `python ledger_sync.py` — unions the desktop's ledgers with the private repo (pulls the 11-04
   opt-outs and sends down; pushes nothing the repo lacks except desktop history). Must exit 0
   before any send path runs.
5. Open the board once, answer the device prompt `desktop`, confirm synced worker notes appear.
6. One manual `refresh-dealflow.bat`. **Expect `publish_guard` to refuse** if the rebuilt board has
   fewer phones than the live 11-04 one (desktop skiptrace file is 09-24). That refusal is correct —
   do not bypass; recover phones (SSD or board twin) first.
7. Register tasks at the laptop's times, then enable in this order, one morning apart:
   - no outbound mail: Refresh 05:30, OptoutSync 07:15, Replies 08:45, Phones 09:30
   - after a morning lands on `origin/main` from the desktop: SendServer 07:45, Morning Worker 08:00, Sheets CRM 08:05
   - last, after bounce rate checks: Cadence 10:00, BSG Warmup 09:15

   The desktop's own exports in `desktop-setup/tasks/` are September vintage and **win over
   templates**, so re-check every trigger after registering. All are `Interactive` today, which
   means they only fire while Alejandro is signed in — the desktop never sleeps, so stay signed in
   (lock, do not sign out), or switch Refresh/Phones/Replies to a `Password` principal
   (`schtasks /Change /TN "<task>" /RU <user> /RP *`) as §4 of MACHINE-HANDOFF describes.
8. Audit by enumerating: `pwsh -c "Get-ScheduledTask | ? TaskName -like '*ealFlow*' | ft TaskName,State"`.
