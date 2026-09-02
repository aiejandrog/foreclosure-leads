# MACHINE HANDOFF — read this before you work on DEALFLOW from a different computer

Last updated: **2026-08-26** (ninth task found and disabled; cadence send path hardened)

This repo is worked from more than one machine and is also refreshed by GitHub Actions.
Git carries the **code and the published site**. It does **not** carry the data, the secrets, or the
worker state. That asymmetry is the whole reason this file exists.

---

## 1. Who is the runner RIGHT NOW

| Machine | Role today | Tasks |
|---|---|---|
| **Laptop** | **ARMED — the live runner** | 8 pipeline tasks enabled |
| **DESKTOP-35NNMFL** (Gigabyte B450M) | Runner-in-waiting, fully stood down | **all 9 tasks Disabled** (as of 2026-08-26) |
| **GitHub Actions** | Backup / watchdog | `refresh.yml` 13:00 UTC daily, `freshness-watchdog.yml` 15:00 UTC |

Evidence the laptop is live: commits `9ff826b`/`43ed370`/`8efda91` (08-24) and `f7492a5`/`e0ab111`/
`b8b771e` (08-25) — the full nightly chain, plus `github-actions[bot]` on both days.

### ⚠ There is a NINTH task, and the installer cannot see it (found 2026-08-26)

For four days this section said "8 tasks registered, all Disabled" for the desktop. True, and it hid
the thing that mattered: **`DealFlow Cadence`** is a ninth task, registered outside
`install-tasks.ps1` and **enabled on the desktop** — so the laptop ran the pipeline while the desktop
emailed homeowners every morning at 09:00, off a board frozen at 08-20 and a local `optouts.json`
last touched 08-22. It had put 59 owners through steps 2–3 of the 4-touch sequence.

**It was disabled on 2026-08-26**, so the desktop is now genuinely stood down. Keep it that way
unless the desktop becomes the armed runner.

`install-tasks.ps1 -DisableLocal` / `-Enable` **do not know this task exists.** They will neither
stand it down nor bring it up, so a handoff done exactly as the sequence below describes leaves
outreach running on the machine it just disarmed. Handle it by name, on both boxes:

```
pwsh -c "Get-ScheduledTask | ? TaskName -like '*ealFlow*' | ft TaskName,State"   # audit — 9 rows
pwsh -c "Disable-ScheduledTask -TaskName 'DealFlow Cadence'"                     # or Enable-
```

Never audit runner state from the installer's list of eight. Enumerate the tasks.

(A disabled task still reports a `NextRunTime` — Windows keeps projecting the trigger. `State` is
the field that decides whether it fires.)

The send path was also hardened on 08-26 (`d53955d`): it re-reads the opt-out ledger before every
send instead of trusting the export-time filter. That closes the code hole, and it is why cadence is
now *safe* to run from a second box. It does **not** close the machine hole: `optouts.json` is
gitignored, so an unarmed box sends against whatever ledger it last hand-copied, while the armed box
keeps writing a different one. **Copy §3 before re-enabling cadence anywhere.**

**Only ONE machine may be armed.** `worker_notes.json`, `optouts.json`, `mail_sent.json` and every
cache are gitignored, so two armed machines do not share state — they fork it, and each one
overwrites the other's board on push.

### Arming sequence (strict order, never both)

```
0.  ON THE NEW BOX:   git config --get user.email     <- MUST return something, or every
                                                         refresh commit fails silently (§6.4)
1.  ON THE LAPTOP:    pwsh .\desktop-setup\install-tasks.ps1 -DisableLocal
2.  Copy live state   (see §3) laptop -> new runner
3.  ON THE NEW BOX:   pwsh .\desktop-setup\install-tasks.ps1 -Enable
4.  Next morning:     confirm a commit landed on origin/main from the new box
```

Use **`pwsh`**, never `powershell`. `install-tasks.ps1` is UTF-8 with no BOM, so Windows
PowerShell 5.1 reads it as CP1252, turns each em-dash into a smart quote, and dies with a bogus
`The '<' operator is reserved` at line 53.

---

## 2. The website — where the design actually lives

Live site: **https://aiejandrog.github.io/foreclosure-leads/** (GitHub Pages, served from `docs/`)

```
tracker_template.html      <- THE DESIGN. <style> blocks + render(). Edit THIS.
    |
    +-- python build_preview.py   -> design-preview.html   (fake leads, no gate, safe to open)
    |
    +-- foreclosure_leads.make_tracker()  -> docs/index.html  (real data, ENCRYPTED, published)
```

**Never hand-edit `docs/index.html`.** It is generated, it is 7.7 MB, it is encrypted against
`site.codes`, and line 1 carries a `DEALFLOW-COVERAGE` census the publish guard reads. Any manual
edit is destroyed by the next refresh — and if it lands in a commit it can trip the guard and
freeze the live site on its last good build.

Design iteration loop: edit `tracker_template.html` -> `python build_preview.py` -> open
`design-preview.html` -> repeat. No real people appear in the preview.

---

## 3. What git does NOT carry (the part that must be hand-copied)

Moving the runner means moving these. They are gitignored on purpose.

**Worker state — stale copies cause real damage:**

| File | Why it matters if stale |
|---|---|
| `optouts.json` | An out-of-date copy **re-contacts people who opted out** |
| `mail_sent.json` | Re-mails addresses already mailed; drives the bounce rate back up |
| `worker_notes.json` | Resurrects leads Jose/Carlos already worked |
| `leads_final.json` / `leads_raw.json` | The board itself |
| `skiptrace_results.json` | Re-spends money on phones already bought |
| `sender.json` | Per-engine identity **and flags**. `"quo_record": true` is what bakes the recording-consent ask onto Call Mode's dial screen; an engine without it publishes a dial screen with **no consent line while Quo keeps recording**. The cloud runner carries the same flag as `QUO_RECORD` in `refresh.yml` |

**Secrets (never commit, never put in OneDrive):**
`site.codes`, `captcha.key`, `tracerfy.key`, `tracerfy_mcp.url`, `gmail.key`, `streetview.key`,
`whitepages.key`, `zerobounce.key`, `sheets_crm_webhook.url`

> Desktop status 2026-08-22: it holds the `DEALFLOW_TRANSFER_2026-08-20` snapshot, so its worker
> state is **frozen at 08-20** while the laptop has kept working. Re-copy §3 at arming time or the
> desktop's first refresh will publish a board that has forgotten two days of work.
>
> `site.codes` IS present, so an armed desktop still publishes the ENCRYPTED board with phones.
> (`site.pass` is absent, but it is only the legacy single-password fallback — `_load_codes()`
> reads `site.codes` first.)

---

## 3b. Working parity vs. arming — two different things

**Both machines may EDIT at the same time.** Clone, pull, branch, build, push — that is just git and
it is safe. Only the *schedule* is exclusive (§1).

To bring a second machine to full editing parity:

```
git pull                                        # code
pip install -r requirements.txt                 # deps
pip install "camoufox[geoip]"                   # LOCAL-ONLY dep, see requirements.txt
python -m camoufox fetch                        # ~500 MB browser
python -m playwright install chromium           # must re-run after camoufox pins playwright 1.60
git config --get user.email                     # must return something (§6.4)
```

Then the gitignored half, which git cannot carry — on the **source** machine run:

```
python prep_desktop.py
```

It writes `DEALFLOW_TRANSFER_<today>.zip` to `~/secure` (outside OneDrive). Carry it by USB or a
private folder — it holds 8 live API keys and homeowner PII. Unzip `secrets/`, `ledgers/`, `data/`
and `browser-profile/` into the repo root on the target.

**DESKTOP-35NNMFL status, 2026-08-22:** code current, all deps installed, all 8 secrets and
`site.codes` present, playwright 1.60 + camoufox both verified against live county portals. It can
develop and push today. Three gaps remain, all needing a copy from the laptop:

| Gap | Consequence |
|---|---|
| `browser-profile/` is **empty** | auction-results scraper is login-gated in all 3 counties and skips them silently |
| ledgers are **08-13 / 08-20** vintage | do NOT run outreach from here until refreshed — `optouts.json` is 9 days stale |
| `lob.key` absent | no physical mail send (may simply be unconfigured everywhere) |

## 4. Scheduled tasks

The eight below are the ones `install-tasks.ps1` registers, enables and disables as a set. They are
identical on both machines.

| Task | Time | Cadence |
|---|---|---|
| DEALFLOW Refresh | 05:30 | daily |
| DEALFLOW Phones | 06:00 | daily |
| DealFlow Replies | 06:45 | daily |
| DEALFLOW Daily Scrape | 07:00 | weekly |
| DealFlow Weekly Analyst | 07:30 | weekly |
| DealflowSendServerDaily | 07:45 | daily |
| DEALFLOW Morning Worker | 08:00 | daily |
| DealFlow Sheets CRM | 08:05 | daily |

**Outside that set, and outside the installer entirely:**

| Task | Time | Cadence | Where |
|---|---|---|---|
| **DealFlow Cadence** | 09:00 | daily | Disabled on DESKTOP-35NNMFL since 2026-08-26 (§1) |

Runs `python -u cadence.py`, logs to `~\DEALFLOW\cadence-run.log`. It sends real email to
homeowners. Because the installer does not manage it, every arm/disarm has to name it explicitly,
and a runner audit has to enumerate tasks rather than trust the list of eight — see §1.

Two settings decide whether the eight actually fire:

- `DisallowStartIfOnBatteries` / `StopIfGoingOnBatteries` are **true on 6 of the 8 tasks**. On a
  desktop that is inert. On a laptop it means the task is skipped on battery and **killed mid-run**
  if you unplug it.
- `StartWhenAvailable` is **false** on *Daily Scrape, Morning Worker, Sheets CRM, SendServerDaily*.
  If the machine is asleep at the trigger time those four runs are **silently skipped, never caught
  up**. The other four catch up late — which is why the laptop's 05:30/06:00 tasks landed at
  10:21/10:33 on 08-22 instead of on time.

---

## 5. Publish gates — do not bypass these

- `healthcheck.py` — exit **2** = compliance/systemic fail (lost §362 bankruptcy-stay flags, or 2+
  upstream sources down) -> **hard block**. exit **1** = coverage floor only -> advisory.
- `publish_guard.py` — refuses to publish a board materially **poorer** than the one already live.
  It exists because the cloud runner builds a correct-but-gutted board (no 2Captcha key, no phone
  budget) and was silently stripping lien chains and phones off the live site every night.

Both gates run before the push in `refresh-dealflow.bat`. If a gate blocks, the live site stays on
its last good build — that is the intended behaviour, not a failure.

---

## 6. Desktop-specific deviations (DESKTOP-35NNMFL)

1. **Python path** — the Sheets CRM task XML hardcodes `C:\Program Files\Python311\pythonw.exe`,
   which does not exist here. Python 3.11.9 is user-local at
   `%LOCALAPPDATA%\Programs\Python\Python311` and that is where playwright/requests/pymupdf live.
   The XML is patched to the real interpreter. The installer's prereq warning about this path is a
   false positive on this box.
2. **`install_send_server_autostart.bat` was not run** — it blocks on `pause` and creates
   `DealflowSendServerDaily` **enabled**, which would break the disabled state. Only its safe half
   was done: `%APPDATA%\...\Startup\DealflowSendServer.vbs` written with REPO_PATH baked in.
3. Never sleeps (standby idle = 0 on AC and DC). No battery, so no ride-through on a power cut —
   a mid-run outage loses that run.
4. **Git identity was unset until 2026-08-22** — `user.name` / `user.email` were empty in both the
   local and global config, so every `git commit` on this box failed with *"Author identity
   unknown"*. Had the desktop been armed in that state, each refresh would have scraped, enriched,
   rebuilt the board — and then failed silently at the commit step, publishing nothing while
   `leads-run.log` filled with fatal errors. Now set to
   `Alejandro Gonzalez <agonzalez0311707@gmail.com>`, matching the laptop's commits.
   **Check this on any new machine before arming it:** `git config --get user.email`

---

## 7. Housekeeping

- **The WeTransfer bundle has been moved off OneDrive** (2026-08-22). It now lives at
  `C:\Users\olqbb\secure\dealflow-transfer-2026-08-20` on DESKTOP-35NNMFL — outside OneDrive, ACL
  restricted to `DESKTOP-35NNMFL\olqbb`. All 318 files were SHA256-verified at the destination
  before the OneDrive copy was deleted. It still holds the 8 live API keys and the 08-20 worker
  state, so it is the fallback if a re-arm needs the original ledgers.
  Two things that move did **not** do, both still open:
  1. Deleting from a synced folder puts the cloud copy in the **OneDrive recycle bin for 30 days**.
     Empty it at onedrive.live.com or the keys are still in Microsoft's cloud.
  2. Those 8 keys sat in consumer cloud storage for two days. Rotating them is the only thing that
     actually closes that exposure; moving the file does not.
- **All DealFlow output moved off OneDrive** (2026-08-22, commit `1638d9a`). Twenty modules each
  hardcoded `~\OneDrive\Desktop\DEALFLOW`; `paths.py` owns it now and it resolves to **`~\DEALFLOW`**,
  outside every sync root. A Desktop shortcut (`DEALFLOW.lnk`, 865 bytes) keeps the double-click
  workflow. `DEALFLOW_DIR` env override still wins, so the cloud runner's tmp path is unchanged.

  **On the laptop, pulling that commit is not the whole job.** Three things need doing there:
  1. The existing `OneDrive\Desktop\DEALFLOW` folder is **not** evacuated by the pull. The next
     refresh writes to the new location and leaves the old one sitting in OneDrive full of PII.
     Move it to `~\DEALFLOW`, delete the original, then **empty the OneDrive recycle bin**.
  2. Check the synced Desktop root for `Tracerfy_*.csv` (case + name + street + city + zip),
     `HardMoney_Balloon_Book_*.html`, `DealFlow-Scorecard\`, `BSG-Meeting-Agendas\` and any
     `DEALFLOW_TRANSFER_*.zip`. All of those used to land there and all now go to `~\DEALFLOW`.
  3. `acosta_report.py` and `amlong_brief.py` are **gitignored** (they carry PII inline), so the
     fix did not travel. Change `os.path.expanduser(os.path.join('~','OneDrive','Desktop','DEALFLOW', …))`
     to `P.out(…)` with `import paths as P` in the laptop's copies by hand.

  Two files stay on the synced Desktop deliberately: `DEALFLOW-STATUS.txt` (`run_report.py` — counts
  only, no names, and the point is that it is visible after an unattended run) and
  `make_bsg_emblem.py`'s brand artwork (no homeowner data, and it has its own `BSG_BRAND_OUT`).
- `OneDrive\Documents\DEALFLOW` is a **website-work hub only** — notes, launchers, design
  references. No code, no data, no keys. The code lives here in git.
- Origin has a stray Claude cloud branch `claude/phone-number-lookup-u4gvzm`. Merge or delete it;
  don't let it rot.
