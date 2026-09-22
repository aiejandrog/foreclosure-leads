# MACHINE HANDOFF — read this before you work on DEALFLOW from a different computer

Last updated: **2026-09-18** (§1 settled — the laptop is the only armed machine; the ninth task, `DealFlow Cadence`, is managed by the installer as of 2026-09-18, see §1 and §4)

This repo is worked from more than one machine and is also refreshed by GitHub Actions.
Git carries the **code and the published site**. It does **not** carry the data, the secrets, or the
worker state. That asymmetry is the whole reason this file exists.

---

## 0. Code you push from the other machine now takes effect the SAME night (2026-09-10)

Until today both runners had exactly one `git pull`, at the bottom, right before the push. A run
therefore **built with whatever code the checkout was holding**, committed the result, and only then
rebased the day's new commits into history — so the repo came out looking current while the
published pages were built from yesterday's code, with nothing in the log to say so.

Caught the morning of 09-10: two commits pushed from the desktop the night before were ancestors of
the 05:42 nightly commit and present in `origin/main`, yet `docs/call/index.html` shipped with no
baked seat and `docs/call/carlos/` was never created. It self-corrected only because the 06:00
phones task rebuilt after the 05:42 run's rebase had updated the checkout. Luck, not a mechanism.

`refresh-dealflow.bat` and `run-leads.bat` now open with `git pull --ff-only origin main`, non-fatal.
**`--ff-only` is deliberate** — NOT the `--rebase --autostash -X theirs` form used before the push.
That is the combination this repo took the 2026-08-19 outage from: the autostash reapply can write
conflict markers into `docs/index.html`, and a board beginning with `<<<<<<<` renders as a blank
site. At the top of a run there is nothing of ours to replay, so a fast-forward is the whole job and
it cannot merge, conflict or stash. A dirty tree or local commits make git refuse and change nothing,
and the run continues on the code already on disk.

Practical effect: push code from either box, and the next scheduled run builds with it. No more
day-late lag, and no more "the commit is in main so the site must have it".

## 1. Who is the runner RIGHT NOW

> ### ⚠️ CORRECTION 2026-09-18 ~15:45 — the block below was wrong about the desktop
>
> Read on DESKTOP-35NNMFL itself (`hostname` checked) at 15:40: **9 DealFlow tasks were `Ready`
> and had all run that day** (Refresh 05:30, Phones 06:00, Replies 06:45, SendServer 07:45, Morning
> Worker 08:00, Cadence 09:00, Sheets CRM 14:05). Its reflog holds `24b5155`, `c2d596f`, `c91eb0d`,
> `fd0ff4f` — so the 09-16/09-17 duplicate publishes really were two armed machines. The "re-verified
> on the box" check below was not run on this box. Disarmed at 15:45 with
> `install-tasks.ps1 -DisableLocal` (pwsh); all DealFlow tasks now `Disabled`, `BSG Warmup` still
> `Ready`. The laptop is now the only armed machine. The watchdog issue should close on its own once
> the 4-day window rolls past 09-18.

> ### ✅ SETTLED 2026-09-18 — the laptop is the only armed machine. The table below is right.
>
> This block said the opposite for a day, so the correction and the evidence that forced it both
> stay here: an inference that reads as proof is worth more as a worked example than as a deletion.
>
> **What was claimed:** two machines are armed and both are publishing. **What settled it:** the
> desktop's own task list, read on the machine. Every DealFlow task is `Disabled` except
> `BSG Warmup`, most with `last=11/30/1999` — the never-run sentinel. There is nothing to disarm,
> and there has not been since 2026-08-26. `engine.id` on the live box reads `laptop`.
>
> **Why the commit log looked like two machines.** The evidence was real; the conclusion was not.
> On 09-16 and 09-17 the same scheduled job committed twice, and two commits stamped the same build
> minute carried different censuses:
>
> | day | job | commit A | commit B |
> |---|---|---|---|
> | 09-16 | reply bake | `9d2c500` 06:45 | `fd0ff4f` 06:45 |
> | 09-16 | phones | `1b5e7c2` 06:00 | `c91eb0d` 07:20 |
> | 09-17 | reply bake | `e5ff186` 06:45 | `c2d596f` 06:45 |
> | 09-17 | phones | `bade50f` 06:00 | `24b5155` 09:29 |
>
> | build stamp | leads | phones | authored | committed |
> |---|---|---|---|---|
> | `2026-09-17T06:45` | 2,297 | 1,148 | 09-17 06:45 | 09-17 **16:42** |
> | `2026-09-17T06:45` | 2,266 | 737 | 09-17 06:45 | 09-17 06:45 |
>
> One build cannot produce two censuses, and that is still true. But it does not take two machines
> to produce two commits — it takes **one machine whose pushes stopped landing**. This box's pushes
> had not reached origin since 09-14, so local auto-commits stacked up (three authored 09-14/09-16/
> 09-17 at 06:00, all committed 09-17 16:42) and then collided with its own earlier pushes on
> origin. The runners pull with `--rebase --autostash -X theirs` before pushing, which is how a
> replayed commit can carry an older board under a newer build stamp. A ten-hour gap between author
> date and committer date is the signature of exactly that, and it is one of the three things
> `freshness-watchdog.yml` now alarms on.
>
> **The lesson worth keeping:** author-vs-committer skew and duplicate same-day publishes are a
> genuine alarm, but they are evidence of *a push not landing*, which a second machine is only one
> possible cause of. Read the skew first and count machines second. `Get-ScheduledTask` on the box
> is the only thing that settles the machine question; git cannot.
>
> **It still cost real things.** 439 phone numbers were stripped off the live board on 09-15 by the
> then-ungated `run-replies-daily.bat` (see CLAUDE.md §"Publish gates"), and that publish became
> `origin/main`, moving the baseline every later gate compared against. That evening `main` itself
> was overwritten and emptied by a stray publish from a checkout that had lost its history,
> recovered from `rescue/main-ad1643f-2026-09-17`. Neither needed a second machine either.
>
> **To re-check the machine question at any time, on each box:**
>
> ```
> pwsh -c "Get-ScheduledTask | ? TaskName -like '*ealFlow*' | ft TaskName,State"
> ```
>
> Nine rows. Whichever box shows `Ready` is armed. If that is ever two boxes, disarm the loser
> **before** copying §3 state, or the disarmed box's ledgers overwrite the winner's.
>
> **Before arming or disarming anything, re-read §0 and the NINTH TASK warning below.** Correction
> to the paragraph below: `install-tasks.ps1 -DisableLocal` **does** catch `DealFlow Cadence` now —
> it enumerates live tasks with `-match '^(DEALFLOW|DealFlow)'` rather than working from its list of
> eight. `-Enable` still does **not** bring it back up, because that path only walks
> `desktop-setup/tasks/*.xml`. So disarming is complete and arming is not: after `-Enable`, turn
> cadence on by name or outreach stays off.
>
> **Second correction, 2026-09-18:** `-Enable` brings it back up now. The asymmetry was real for
> three weeks — disarming complete, arming silently incomplete — and it is closed by
> `desktop-setup/task-templates/DealFlow_Cadence.xml`, a tracked, SID-free template the installer
> substitutes and registers alongside the gitignored exports in `tasks/`. Arming a machine now arms
> outreach with the rest of the pipeline. **Read that as a change in blast radius, not just a fix:**
> `-Enable` on the wrong box used to leave outreach off by accident. It will not do that again.

| Machine | Role today | Tasks |
|---|---|---|
| **Laptop** | **ARMED — the live runner** | 8 pipeline tasks enabled; `engine.id` = `laptop` |
| **DESKTOP-35NNMFL** (Gigabyte B450M) | Runner-in-waiting, dark | all DealFlow tasks `Disabled` except `BSG Warmup` (re-verified on the box 2026-09-18; most read `last=11/30/1999`) |
| **GitHub Actions** | Watchdog only — **it does not publish** | `freshness-watchdog.yml`. `refresh.yml` is gone; `.github/workflows/` holds nothing else |

Evidence the laptop is live: commits `9ff826b`/`43ed370`/`8efda91` (08-24) and `f7492a5`/`e0ab111`/
`b8b771e` (08-25) — the full nightly chain, plus `github-actions[bot]` on both days.

**That evidence is from 08-24/08-25 and no longer describes 09-14 onward.** It is kept because it
shows what the laptop's nightly chain looks like in the log, which is how you will recognise which
machine produced any given commit.

### ⚠ There is a NINTH task — the installer could not see it (2026-08-26, closed 2026-09-18)

For four days this section said "8 tasks registered, all Disabled" for the desktop. True, and it hid
the thing that mattered: **`DealFlow Cadence`** is a ninth task, registered outside
`install-tasks.ps1` and **enabled on the desktop** — so the laptop ran the pipeline while the desktop
emailed homeowners every morning at 09:00, off a board frozen at 08-20 and a local `optouts.json`
last touched 08-22. It had put 59 owners through steps 2–3 of the 4-touch sequence.

**It was disabled on 2026-08-26**, so the desktop is now genuinely stood down. Keep it that way
unless the desktop becomes the armed runner.

**CLOSED 2026-09-18.** `install-tasks.ps1` manages it with the other eight now, from
`desktop-setup/task-templates/DealFlow_Cadence.xml`. The task it registers runs `cadence-daily.bat`
(not `cadence-run.bat`, which ends in `pause`), and that wrapper repo-guards the folder and refuses
to send outside 08:00–20:00. The window is not decoration: the task is `StartWhenAvailable=true` so
a run missed while the laptop slept is caught up rather than lost, and without a window that
catch-up is a batch of homeowner follow-ups going out at 22:40.

To add or arm it on ONE machine without re-registering that machine's other live tasks:

```
pwsh .\desktop-setup\install-tasks.ps1 -Only Cadence            # register, left disabled
pwsh .\desktop-setup\install-tasks.ps1 -Only Cadence -Enable    # arm it
```

**The history below is kept because the shape of it recurs.** For three weeks the installer's two
halves were asymmetric about this task: `-DisableLocal` enumerates live tasks by name match so it
*did* stand it down, while `-Enable` walked `desktop-setup/tasks/*.xml` so it did *not* bring it
up. A handoff done exactly as the sequence below describes therefore disarmed outreach and never
re-armed it, with nothing in any output to say so. Nor could it be fixed by exporting the task into
`tasks/` — that directory is gitignored precisely because a Windows export embeds the exporting
machine's principal SID. Handling it by name still works and is still the right audit:

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

Live site: **https://aiejandrog.github.io/dealflow-board/** (GitHub Pages, served from `docs/`)

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

**Sending identity (2026-09-07):** the bridge prefers `bsg_gmail.key`
(`alejandro@bsgflorida.com:<app password>`, quotes tolerated) over `gmail.key`, and with it applies
`senders.json` — Morning Worker lane → From alias (`replied`/`urgent` → alejandro@bsgflorida.com,
`active` → alejandro@biscaynesolutionsgroup.com, `early` → alejandro@bsgfl.com) with a per-alias
warm-up ramp (5/day on 09-07 → 100/day from day 29). The aliases are Workspace alias domains
registered under that account's Send-mail-as; DKIM signs with the alias domain. **A machine
without `bsg_gmail.key` keeps sending as its `gmail.key` login and `/health` reports
`senders_active: false`** — copy the key file (never commit it) to arm the lanes there. The worker
passes `meta.wl`; boards built before 09-07 send without it and fall to the `default` lane.

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

The nine below are the ones `install-tasks.ps1` registers, enables and disables as a set — eight
until 2026-09-18, see the NINTH TASK block in §1. They are identical on both machines.

**Times below are as confirmed on the LAPTOP on 2026-09-22.** Two moved that morning — Replies
06:45 → 08:45 and Phones 06:00 → 09:30 — and this table said the old times for a day afterwards,
which is the whole reason §1 tells you to enumerate rather than read a list in a file.

| Task | Time | Cadence | State on the laptop | Definition |
|---|---|---|---|---|
| DEALFLOW Refresh | 05:30 | daily | Ready | `tasks/` export, **or** `task-templates/` — tracked in git |
| DEALFLOW Daily Scrape | 07:00 | weekly | Ready | `tasks/` export |
| DealFlow Weekly Analyst | 07:30 | weekly | Ready | `tasks/` export |
| DealflowSendServerDaily | 07:45 | daily | Ready | `tasks/` export |
| DEALFLOW Morning Worker | 08:00 | daily | Ready | `tasks/` export |
| DealFlow Sheets CRM | 08:05 | daily | Ready | `tasks/` export |
| DealFlow Replies | **08:45** | daily | Ready | `tasks/` export |
| DEALFLOW Phones | **09:30** | daily | Ready | `tasks/` export |
| **DealFlow Cadence** | **10:00** | daily | **NOT REGISTERED** on the laptop; Disabled on the desktop | `task-templates/` — tracked in git |

**Cadence is the one row that is not live anywhere.** The template ships in the repo, so
`install-tasks.ps1 -Only Cadence -Enable` is a *first-time registration of a real sending path*,
not a checkbox. Do not run it while the bridge's bounce breaker is blocking (see §5) — cadence does
not consult that gate.

**Cadence's 10:00 is a dependency on Replies, not a preference.** `replies.py` then
`optout_sync.py` is what carries a detected STOP into `optouts.json`, and cadence re-reads that
ledger every run: a queue exported yesterday knows nothing about a homeowner who said stop this
morning. It sat at 09:00 against a 06:45 Replies. When Replies moved to 08:45 that became a
fifteen-minute gap on a job that also rebuilds and publishes the board, so cadence moved to 10:00.
**If Replies moves again, move cadence too** — cadence has no ledger-staleness gate of its own
(`send_server./send` refuses an `optouts.json` older than 2 days; cadence does not), so this
ordering is the only thing enforcing it. `_taskinstalltest.py` asserts the gap, so shortening it
fails there rather than in a morning's mail.

`tasks/` is gitignored: a Windows export embeds the exporting machine's principal SID and user
paths, so those definitions travel in the transfer bundle. `task-templates/` is the tracked,
SID-free half — `__REPO__` / `__PROFILE__` / `__USER__` placeholders substituted at install time.
An export always wins over a template of the same task name.

**Two of the nine now have a tracked template (2026-09-21).** `DEALFLOW Refresh` joined
`DealFlow Cadence` there, and the reason is the two settings called out at the bottom of this
section: they decide whether the nightly fires at all, and until today they could not be read,
diffed or reviewed from anywhere except the armed laptop. Adding the template changes nothing on
that laptop — the export still wins — but the hardening is now in the repo where a commit can
carry it, and `_refreshexittest.py` asserts it. The other seven are still export-only; each is one
`task-templates/` file away from the same treatment.

**DealFlow Cadence sends real email to homeowners.** It runs `cadence-daily.bat`, which repo-guards
the folder, refuses to send outside 08:00–20:00, then runs `python -u cadence.py`. Log:
`~\DEALFLOW\cadence-run.log`. Status file: `~\DEALFLOW\DEALFLOW-CADENCE-STATUS.txt`. Both sit
outside the repo because the log carries homeowner email addresses.

**Outside the installer entirely:**

| Task | Time | Cadence | Where |
|---|---|---|---|
| BSG Warmup | 09:15 | daily | `warmup.py`, company-owned mailboxes only — the one task deliberately left running on the disarmed desktop |

**`warmup.py` does not write `mail_sent.json`, and that is correct** — warm-up mail goes to
company-owned mailboxes, so counting it as outreach would corrupt every reply and bounce rate here.
But every cap in the project meters off that one ledger, so an alias's real daily volume is not
visible anywhere. `python ramp_status.py` adds the two back together; `--days N` also prints the
date the cold ramp first matches the warm-up quota, which is the first day stopping BSG Warmup
does not cut a warming alias's volume. Read-only.

A runner audit still has to **enumerate** tasks rather than trust any list in this file — see §1.

The tracked installer templates set `DisallowStartIfOnBatteries=false`,
`StopIfGoingOnBatteries=false`, and `StartWhenAvailable=true`. Those settings prevent a laptop
unplug or missed wake from killing/skipping a run. Older exported XML in `desktop-setup/tasks/`
wins over a template, however, so do not infer the live settings from this file. Audit `.Settings`
on the runner after installation.

The three unattended network jobs (`DEALFLOW Refresh`, `DEALFLOW Phones`, `DealFlow Replies`) also
need a `Password` principal on the laptop runner. `Interactive` cannot run while logged out; `S4U`
has no network/stored-credential access and can fail the final Git Credential Manager HTTPS push.
After changing them, verify `.Principal.LogonType` is `Password`. A Windows password change requires
running `schtasks /Change ... /RP *` again.

---

## 5. Publish gates — do not bypass these

- `healthcheck.py` — exit **2** = compliance/systemic fail (lost §362 bankruptcy-stay flags, or 2+
  upstream sources down) -> **hard block**. exit **1** = coverage floor only -> advisory.
- `publish_guard.py` — refuses to publish a board materially **poorer** than the one already live.
  It exists because the cloud runner builds a correct-but-gutted board (no 2Captcha key, no phone
  budget) and was silently stripping lien chains and phones off the live site every night.

Both gates run before the push in `refresh-dealflow.bat`. If a gate blocks, the live site stays on
its last good build — that is the intended behaviour, not a failure.

**They did not run everywhere, and that made them optional (fixed 2026-09-17).** `refresh-dealflow.bat`
was the only runner with both. `run-phones-nightly.bat` had neither and `run-leads.bat` had no
healthcheck, so the compliance hard block was walkable: refresh refuses to publish on exit 2 but
leaves the rebuilt board on disk, and the phones job rebuilt from the same `leads_final.json` and
published it 30 minutes later with nothing asking. Measured on 09-15: the reply bake put a 709-phone
board over the live 1,148-phone one at 19:11 and the phones job put 714 over it at 19:12 — both
drops publish_guard would have blocked. Both jobs now run healthcheck (exit 2 blocks) then
publish_guard, the same order and the same advisory treatment of exit 1.

**And no runner ever checked that its push landed.** Every one of them ended with `git push`, an
optional 6s retry, and then an unconditional "published, live in ~1-2 min" — neither exit code was
read. That cost two multi-day blackouts a month apart (08-16, and 09-14→09-17) in which the board
was rebuilt locally every morning while `DEALFLOW-PHONES-STATUS.txt` said `OK - published`.
`publish_verify.bat` now asks the remote whether `HEAD` is an ancestor of `origin/main` and writes
the status file from the answer. A push that did not land says so, loudly, and does not retry.

**A publish run from the wrong folder destroyed the GitHub copy of the repo (2026-09-17, ~17:41).**
A publish was run on the desktop from a directory that was **not** the project checkout — it held the
built site and little else. Its `git push origin main` replaced main on GitHub with a single commit,
`ec8bf31` "site: first publish of the built DEALFLOW pages": `docs/` and a README, **318 source
modules, every `.bat` and the whole commit history gone from the remote in one push.** Recovered
only because a cloud session happened to be holding a clone from eighteen minutes earlier; main is
back at the real history and the docs-only commit is kept on `docs-first-publish-ec8bf31`.

`repo_guard.bat` now runs first in `refresh-dealflow.bat`, `run-phones-nightly.bat` and
`run-leads.bat`. It refuses to build or publish unless the pipeline's own files are present
(`foreclosure_leads.py`, `tracker_template.html`, `paths.py`, `CLAUDE.md`), the checkout is a git
work tree, **the history is more than 20 commits**, and origin is this project. The history check is
the one that matters: a built-site folder is a perfectly valid git repo pointing at the right
remote — the offending directory passed every git check there is — and the only thing that
distinguished it was that the code was absent and its history was one commit deep. The guard only
ever refuses; it never resets or re-clones.

The cloud watchdog was blind to all of this because it read the `Updated` stamp on the live page,
which is the **build** time — and the Phones/Replies jobs rebuild every morning whether or not any new
data arrived. `freshness-watchdog.yml` now also reads what reached `origin/main` and alarms on two
runners publishing the same job in one day, on commits that sat unpushed, and on the 05:30 refresh
going quiet. `node _watchdogtest.js` is its contract test (it extracts the shipped script out of the
YAML rather than re-typing it); `--live` runs it against this repo's current log.

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

---

## Session coordination — 2026-09-02 (evening)

Two Claude sessions are working this repo concurrently on the SAME main checkout. Division:

- **DEALFLOW session** (WIP live in call_mode.py as of 17:26): no-means-no chain — notint 720h
  + retroactive floor, pcs sibling suppression, person-keyed stopEverywhere, sync-key-from-phone
  (576eaa0, local/unpushed). Owns the next rebuild+push.
- **Takeover/Quo session**: teamRecheck already-called takeover (602dbad, pushed), quo_sync +
  coach (f6c0238), `_cm_teamtest.js` (this commit).

**`node _cm_teamtest.js` is the contract test** for "a logged NO disappears from tomorrow's
queue, person-wide." It tests the BUILT page — rebuild before trusting a red. First run: 12
pass / 3 fail; two fails were source-newer-than-page (guards saved 17:26, page built 17:20 —
ship them by rebuilding), one is real and OPEN:

- **`_teammateCall` does not walk `r.pcs`** — a teammate's call on a SIBLING case suppresses
  the next queue build but does NOT take over the already-painted screen. Whoever lands last
  on call_mode.py folds this in (one loop, same shape as suppressed()'s sibling walk).

Also open: `origin/claude/call-mode-ready` (c690b98) forked from 07d6fad, BEFORE 602dbad —
expect a small call_mode.py merge when it lands.

### Red/green proof for the no-means-no guards (supervisor item, 2026-09-02 evening)

| scenario | `_cm_teamtest.js` (BUILT page, 17:20) | `_cm_sourcecheck.js` (source, 17:26) |
|---|---|---|
| sibling-case suppression | **RED** — guard absent from page | **GREEN** |
| retroactive 72h→720h floor | **RED** — guard absent from page | **GREEN** |
| expired ordinary cooldown stays dialable | — | **GREEN** (fail-capability proof) |

Reading: the guards are CORRECT in source and NOT YET on the phone. The pair separates
"fix written" / "fix live" / "fix wrong" — one test alone cannot. **After the next rebuild,
`node _cm_teamtest.js` must go green on both rows; if it does not, the build is broken, not
the code.**

**OWNED OPEN ITEMS**
- `_teammateCall` pcs-walk (sibling-case teammate call must trigger the on-screen takeover):
  **owner = takeover/Quo session**, lands immediately after DEALFLOW's next push, unless that
  push already contains it. Not "whoever lands last" — named, with a trigger.
- notint policy — 720h is a WINDOW, not a retirement. Whether an explicit "no" resurfaces at
  all is **Alejandro's decision, asked directly 2026-09-02 evening**, not a buried constant.
  Until he answers, the 30-day window in the DEALFLOW WIP stands.

### notint policy — CONVERGED SPEC, awaiting Alejandro's yes (2026-09-02 late)

Supervisor session and takeover/Quo session agree; do NOT implement until he answers.

- **HARD NO** ("stop calling me"): its own outcome button -> permanent, person-keyed DNC.
  Checked at queue build AND at dial time. No resurface, no exceptions. (Mostly exists:
  stopEverywhere; the new part is splitting the BUTTON so reps stop logging hard nos as notint.)
- **SOFT NO** ("we're set, thanks"): retire from the automatic rotation — NOT a cooldown.
  Eligible for exactly ONE deliberate resurface, EVENT-driven, never calendar-driven:
    * lead has a sale date -> resurface at T-14 before auction;
    * LP lead (no date — where MOST nos happen, 1,115/1,471 rows) -> resurface when a sale
      date APPEARS. Without this branch the feature is silently dead for the whole LP lane —
      the succeeds-while-doing-nothing class, again.
  After the one resurface: any second no of either kind = retired permanently.
- Until he answers, the 720h window in the DEALFLOW WIP stands as the interim.

### notint policy — CLOSED (Alejandro decided 2026-09-02): SPLIT IT

Final, no longer open. The 30-day calendar cooldown is DEAD.

- **HARD NO / "stop calling"** — permanent, person-keyed DNC. Checked at queue build AND dial
  time. Never resurfaces, no exceptions. Its own outcome button (reps must stop logging hard
  nos as "not interested"), and it behaves like `dnc` in the after-call panel (skips it).
- **SOFT NO / "we're set, thanks"** — retired from automatic rotation, NOT a cooldown.
  Eligible for exactly ONE deliberate resurface, event-driven:
    * had a sale date at soft-no time  -> resurface at **T-14** before that auction;
    * was LP / no date at soft-no time -> resurface when **a sale date appears** (covers the
      1,115/1,471 LP rows the calendar version left dead).
  One resurface only; the next no of either kind = permanent.

**OWNERSHIP OF THE BUILD:** DEALFLOW owns `call_mode.py` WIP and lands the implementation.
This session provides the acceptance gate and stands down on code in that file.

**ACCEPTANCE GATE (this session, `_cm_notint_spec.js`):** RED today by design — 7 fail, and a
do-nothing suppressor CANNOT turn it green (the "retired" cases demand suppression, the trigger
cases demand release, so only a real implementation satisfies both). Covers all three required
harness cases: hard-no never reappears (incl. dial-time); soft-no with a date resurfaces once
inside T-14; soft-no LP lead resurfaces once when a date lands; plus the second-no-is-permanent
and legacy-calendar-is-dead guards. State schema proposed in the file header — if DEALFLOW
represents it differently, they update the test IN THE SAME COMMIT.

**pcs-walk (`_teammateCall` sibling walk) — DONE by DEALFLOW** in their uncommitted WIP
(2026-09-02): `_teammateCall` now iterates `[r.c].concat(r.pcs||[])`. This session's ownership
item is CLOSED; no separate landing needed.

**FLAG for DEALFLOW (not blocking):** the same WIP drops "or STOP to opt out" from all three
`TEXT_T` templates. Confirm STOP is handled at the carrier/10DLC layer before that ships — an
outreach text with no opt-out is the kind of thing FS 501.1377 / TCPA notices exist around.

### Carlos = seat 2 — the split is BAKED now (2026-09-09), no on-phone step

**The on-phone step is gone.** `fcSeat` + the three `prompt()` boxes are dead: they depended on two
people typing matching numbers into two devices, and every way that goes wrong is silent (both pick
seat 1 and the UI still says the split is on; one picks n=2 and the other n=3 and you get overlap
AND lost leads at once; "show all" reverts on reload). The partition moved to build time.

- **`call_mode.CALL_SEATS`** in call_mode.py is the whole declaration:
  `[(2, 0, 'Alejandro'), (2, 1, 'Carlos')]` — `(n, i, label)`, and the label is the URL segment.
  The nightly builds ONE PAGE PER SEAT from one `call_rows()` call, each with its own encrypted
  payload holding only that seat's rows.
- **URLs:** Alejandro `…/foreclosure-leads/call/` (unchanged, still what the board's Call Mode
  button opens). Carlos `…/foreclosure-leads/call/carlos/` — Add to Home Screen on his phone.
  The board now has a second button, **Call Mode · Carlos**, next to the first.
- **Access code:** unchanged — `Carlos 2` in site.codes (gitignored). The code is what stamps `by`
  on every dial, so he must still unlock with his own. Either code opens either page (`fcPw` is
  per-origin); the page warns if the name on the code does not match the seat it was built for.
- **Team sync key still matters.** The partition prevents the double-dial; the 45s note sync is
  what makes each phone show "already called by Carlos" on shared/sibling cases. Both phones need
  the same `fcTeamKey`.
- **To change the crew:** edit `CALL_SEATS`, rebuild. One caller again = `[None]` (whole list, one
  page). Three callers = three entries with the same `n` and distinct `i`. `python _carlostest.py`
  asserts n agrees, indices are distinct and cover `0..n-1`, and the built payloads are disjoint
  and reunite to the whole list.
- **Categories on both pages (2026-09-09):** Emailed/replied · Worker · Urgent 0-7 · Sale soon 8-45
  · 46-60 · Fresh filings · Balloon · Buy-box. One `LANES` table drives both the buttons and the
  filter, and day-lanes recompute the countdown from the baked auction date on every paint — the
  frozen `r.d` used to keep a passed sale sitting under "Urgent" on a page left open overnight.
  Emailed/replied and Worker read the SYNCED notes, so they populate on a phone that never opened
  the board.
- **Proven:** `python _carlostest.py` (49 checks: partition, built-payload disjointness, the baked
  seat cannot be changed or escaped, all eight lane predicates, fail-soft). `python _seattest.py`
  (14) still green. `node _cm_teamtest.js` takeover unchanged.
- **Fail-soft:** each extra seat builds in its own try/except. Carlos's page failing prints
  `call mode/carlos: SKIPPED (…)` and costs nothing else — not Alejandro's page, not the call
  sheet, not the board.

## 2026-09-05 — email-safety build complete (laptop)
Items 1-4 all shipped: d0f9044 (bridge refuses owner sends when optouts.json >2d old/missing),
8dc22c8 (armed-runner gate on #worker=morning — default OFF, arm the scheduler machine only via
localStorage.setItem('fcArmedRunner','true'); device audit labels via _devSrc(); ledger_sync.py).
DESKTOP on next pull: (1) run `python ledger_sync.py` — unions your send history + case-keyed
opt-outs into the shared private ledgers repo, no USB needed for these two files; (2) do NOT set
fcArmedRunner here; (3) answer the device-label prompt "desktop" on first board open; (4) bridge
may relaunch only after ledger_sync exits 0 — the staleness guard will block owner sends otherwise.
