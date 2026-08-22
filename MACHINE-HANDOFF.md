# MACHINE HANDOFF — read this before you work on DEALFLOW from a different computer

Last updated: **2026-08-22** (desktop set-up + audit session)

This repo is worked from more than one machine and is also refreshed by GitHub Actions.
Git carries the **code and the published site**. It does **not** carry the data, the secrets, or the
worker state. That asymmetry is the whole reason this file exists.

---

## 1. Who is the runner RIGHT NOW

| Machine | Role today | Tasks |
|---|---|---|
| **Laptop** | **ARMED — the live runner** | 8 scheduled tasks enabled |
| **DESKTOP-35NNMFL** (Gigabyte B450M) | Runner-in-waiting | 8 tasks registered, **all Disabled** |
| **GitHub Actions** | Backup / watchdog | `refresh.yml` 13:00 UTC daily, `freshness-watchdog.yml` 15:00 UTC |

Evidence the laptop is live: commits `2dfac36` (08-22 10:21) and `977ee77` (08-22 10:33).

**Only ONE machine may be armed.** `worker_notes.json`, `optouts.json`, `mail_sent.json` and every
cache are gitignored, so two armed machines do not share state — they fork it, and each one
overwrites the other's board on push.

### Arming sequence (strict order, never both)

```
1.  ON THE LAPTOP:    pwsh .\desktop-setup\install-tasks.ps1 -DisableLocal
2.  Copy live state   (see §3) laptop -> new runner
3.  ON THE NEW BOX:   pwsh .\desktop-setup\install-tasks.ps1 -Enable
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

## 4. Scheduled tasks (identical on both machines)

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

Two settings decide whether these actually fire:

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

---

## 7. Housekeeping

- The WeTransfer bundle at `OneDrive\Desktop\wetransfer_dealflow_transfer_2026-08-20-zip_2026-08-21_1537`
  holds **8 live API keys and homeowner PII** and is syncing to consumer OneDrive. Move it off once
  the handoff is confirmed.
- `OneDrive\Documents\DEALFLOW` is a **website-work hub only** — notes, launchers, design
  references. No code, no data, no keys. The code lives here in git.
- Origin has a stray Claude cloud branch `claude/phone-number-lookup-u4gvzm`. Merge or delete it;
  don't let it rot.
