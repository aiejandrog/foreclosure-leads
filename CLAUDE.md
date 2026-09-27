# DEALFLOW — repo rules for Claude sessions

**Read [MACHINE-HANDOFF.md](MACHINE-HANDOFF.md) first.** This repo is worked from a laptop, a
desktop (DESKTOP-35NNMFL), and GitHub Actions. Git carries code and the published site; it does
**not** carry data, secrets, or worker state.

## Before doing anything

1. `git pull` — another machine or the cloud runner may have pushed since this checkout.
2. Check which machine is armed (`MACHINE-HANDOFF.md` §1). Only one may run the schedule.
3. Check the ownership claims below before editing anything they cover.

## OWNED SURFACE: suppression — do not edit from another session

**Claimed 2026-08-26 by the DESKTOP-35NNMFL session, at Alejandro's direction. Stands until he
lifts it.** Working parity (§3b) still applies to the whole rest of the repo; this is one surface.

On 2026-08-26 two Claude sessions edited opt-out logic in the same hours from different machines.
Nothing collided in git — the damage was subtler and it happened twice in one day:

- `f5856b0` ("privacy: the public board published 1,449 homeowner email addresses in plaintext",
  cited as `c047f7d` in `95231d8` — that SHA was rebased away, which is its own argument for
  quoting subjects and not just hashes here) dropped the `'@email'` identity keys, which emptied
  `_optedOutIdentities()` and silently removed person-level suppression. Caught and repaired in
  `95231d8`.
- `d53955d` made cadence's detected STOP write the shared ledger and the hard-suppression list —
  correct in itself, but it promoted a verdict produced by a **known-bad detector** into permanent
  cross-channel suppression. `f80f5e6` proved that detector matched "Can you stop the
  foreclosure?" hours later. Repaired in `e79e104`.

Neither is a merge conflict. Both are two correct-looking changes composing into a wrong system,
which is the specific failure mode a shared surface with two owners produces, and the blast radius
here is contacting a homeowner who told us to stop.

**In scope — do not edit without handing the surface back:**

| | |
|---|---|
| the ledger | `optouts.json` shape and every writer of it — `optout_sync.py`, `cadence.py`, `replies.py` |
| stop detection | `replies.is_stop_text()` and anything that decides a reply means stop |
| hard suppression | `bounced_emails.json` and the send-bridge list |
| person-level gates | board `_optedOutIdentities` / `_isOptedOutPerson` / `_textContactBlocked` / `_workerEligible` / `_laneStats` |
| send-time checks | `cadence.py`'s pre-send sweep, `outreach_email._load_optouts()` |
| the tests | `_suppressiontest.py`, `_optouttest.py`, `_dnctest.py` |

Touching adjacent code that merely *reads* a suppression verdict is fine. Changing what produces
one is not.

**If you find a suppression bug from another session:** do not fix it. Write it in the commit
message of whatever you were doing, or add a line here, and say so in your summary to Alejandro.
An unfixed, reported bug on this surface is cheaper than two uncoordinated fixes.

**2026-09-26, at Alejandro's direction (legal side approved that day):** PR #70 edits this surface.
`optout_sync.ledger_add()` stays the only writer of the opt-out ledger. A stale or unreadable
ledger blocks sends. `is_stop_text()` treats "not a good time" / "try next month" / "no es buen
momento" as a permanent opt-out, and strips our own opt-out sentence before matching so quoting it
back is not itself a stop. Every email keeps the EN line and the ES line ("Si ahora no es buen
momento, solo dígamelo y no lo vuelvo a contactar."). Every text keeps "Reply STOP to opt out." /
"Responda STOP para no recibir más mensajes." `quo_sync --messages` runs after every sync; carrier
STOP words plus PARE / BASTA / NO MAS ledger an inbound text. A failed or stale inbound read holds
all texting and Call Mode says so; it does not hold email past the 07:15 opt-out sync gate. The
board Morning Worker follows the 09-02 no policy: a hard no is person-wide and never re-contacted,
and "Not interested" is retired until its one event-driven resurface rather than a 30-day cooldown.
The Quo `/messages` request shape follows the published OpenPhone v1 listing and has not been
exercised against the live API from a machine holding `quo.key`. `ledger_add` refuses to replace
an opt-out ledger it cannot parse (backup copy, original left in place, sends stay blocked), and
it applies that same refusal to `bounced_emails.json`. An empty `ledger_add` does not refresh the
ledger mtime; the 07:15 sweep does that itself after it finishes. A Quo `/messages` response that
is denied, missing, or not a listing holds texting (`ok: false`), and so does a scan that hits
the page cap. The inbound number set is that dial/text list unioned with Quo `GET /v1/conversations`
participants whose thread was active in the same 60 days (live check 2026-09-26: `data`,
`nextPageToken`, `totalItems`; item `participants`, `phoneNumberId`, `lastActivityAt`). A failed
or truncated conversations read holds texting. Message bodies are read from `text`; inbound
direction is `incoming`. The status file records pages read and conversations scanned, counts
only. A 429 is retried a bounded number of times before it counts as an error. An unreadable
`text_sent.json` is skipped and counted, and that holds texting. `ledger_add` writes the opt-out
keys before it refuses a torn `bounced_emails.json`. An unreadable bounce list, and an unreadable
`optouts.json`, each raise a `pipeline_alerts` fail. The message `createdAfter` is the earlier of
`--days` and the last ok scan minus 12 hours, and never further than 60 days; no ok scan, or an
unreadable status, uses that 60-day lookback. The status file records the window as a day count.
`/conversations` asks for 100 per page and stops once a whole page is older than the lookback.
An unreadable `mail_sent.json` is skipped and counted, and that holds texting, same as `text_sent.json`.
The board and Call Mode hold texting until a fresh inbound scan is confirmed; bridge-down is a
hold. `cadence.py` enforces the same 07:15 opt-out sync hold as `cadence-daily.bat`. Review this
as a change to this surface, not as ordinary copy.

**2026-09-26, attorney signed off the same day:** outbound texts no longer say "Reply STOP to opt
out." or "Responda STOP para no recibir más mensajes." Board batch (`stopEN`/`stopES`), Call Mode
(`TEXT_OPTOUT`), and `outreach_copy.sms()` use "If now's not a good time, just let me know and I
won't text you again." and "Si ahora no es buen momento, solo dígamelo y no le vuelvo a escribir."
`is_stop_text()` strips this new sentence before matching, the same way it strips the
email line, so quoting it back is not a stop and a real "not a good time" still is.

**2026-09-27:** that line invites a reply, so `is_sms_stop()` opts out a whole-message `no` / `nope` /
`nah` / `no thanks` / `no thank you` / `not now` / `no gracias` / `ahora no` / `revoke` /
`lose my number`, after the EN/ES text line and punctuation are stripped. Wrong-number phrases
(`wrong number`, `you have the wrong number`, `wrong person`, `wrong #`, `número equivocado`,
`se equivocó de número`) opt out anywhere in the message. `no` and `not now` stay whole-message
only, so "no problem, call me" is not a stop. "not interested" / "no me interesa" are still
undecided. Quoting the line and answering `no` is a stop; the line alone is not. A
`sync_messages(phones=...)` run (`--phone` / `--case`) records counts and does not write `ok: true`
or refresh `ts`. Only a full scan may release the text hold or move the next window.

State as of the claim: cadence calls `replies.is_stop_text()` (no local detector), the ledger write
is add-only with both case and `'@email'` keys plus `bounced_emails.json`, cadence re-reads the
ledger before every send, and identity keys publish hashed via `'@' + _addr_key(email)`.

## Full case research

For requests to review dockets, judgments, parties, attorneys, probate or recorded instruments,
follow [CASE-REVIEW-PROCEDURE.md](CASE-REVIEW-PROCEDURE.md). Create a pending inventory with
`case_review.py` from full raw docket JSON. The compact board docket is not a complete case file.
Document metadata and keyword signals must never be represented as documents read or verified findings.

## The website

Live: https://aiejandrog.github.io/dealflow-board/ (GitHub Pages from `docs/`)

- **Design source of truth is `tracker_template.html`** — the `<style>` blocks + `render()`.
- Preview loop: edit `tracker_template.html` → `python build_preview.py` → open `design-preview.html`
  (fake leads, no gate, no real people).
- **The board's own address lives in `board_url.py` and nowhere else.** `subst_build_facts()` bakes
  it into `__BOARDURL__`, and `check_board_urls()` aborts the build if a page carries any other
  `aiejandrog.github.io` URL. On 2026-09-17 the move to `dealflow-board` was hand-edited into nine
  files; the board that was **already built** kept the old string and stayed live, so on 09-18 the
  Morning Worker's "Call these 30 now" button opened a retired URL that 404s. A hand-edit cannot
  reach a page generated yesterday. Change the address in `board_url.py` and rebuild.
- **NEVER hand-edit `docs/index.html`.** It is generated by `foreclosure_leads.make_tracker()`,
  encrypted against `site.codes`, and line 1 is a `DEALFLOW-COVERAGE` census the publish guard
  reads. Manual edits are destroyed on the next refresh and can freeze the live site.

## Output paths

Anything written outside the repo goes through **`paths.py`** — `P.out(name)`, `P.DEALFLOW_DIR`,
`P.TWIN`, `P.TRANSFER_DIR`. It resolves to `~\DEALFLOW`, **outside OneDrive**.

**Never write homeowner data to `~\OneDrive\Desktop`.** Known Folder Move puts the Desktop inside
OneDrive on this account, so anything landing there is replicated to consumer cloud storage. Twenty
modules did exactly that until 2026-08-22 — the plaintext board with phone numbers, skip-trace CSVs,
call lists, the transfer bundle. `P.DESKTOP` exists for shortcuts and count-only status files; check
`P.DESKTOP_IS_SYNCED` before putting anything else there.

## Disclaimers

All homeowner-facing compliance language comes from **`disclaimer.py`** — `mars()` (12 CFR 1015.4(a)
MARS/Reg O), `identity()` ("I am not your lender / the government / a rescue company / an attorney"),
`sig_tag()`. Every surface imports it: `bsg_letter`, `bsg_flyer`, `outreach_mail`, `outreach_email`.

**Never re-type this text into a new module.** It was hand-copied across four files once already and
drifted into five versions, each weaker than the last in a different way. Add a caller, not a copy.
Changing the wording is a legal decision — raise it, don't just edit it.

## Never commit

Homeowner PII, `leads_*.json`, `worker_notes.json`, `optouts.json`, `mail_sent.json`, and any
`*.key` / `*.pass` / `*.codes` / `*.url`. `.gitignore` blocks these by kind — if you add a script
that carries a name or number inline, gitignore it in the same commit.

## Publish gates — never bypass

`healthcheck.py` (exit 2 = compliance hard block, exit 1 = advisory) and `publish_guard.py`
(refuses a board materially poorer than the live one) run before the push in **every path that
publishes the board**. A blocked publish leaving the site on its last good build is correct
behaviour, not a bug to route around.

**Five paths publish, and all five are gated (the last three only since 2026-09-17):**

| path | when | gates |
|---|---|---|
| `refresh-dealflow.bat` | nightly 5:30 | healthcheck + publish_guard |
| `run-leads.bat` | manual | healthcheck + publish_guard |
| `run-replies-daily.bat` | daily 7:00 | healthcheck + publish_guard |
| `run-phones-nightly.bat` | nightly 6:00 | healthcheck + publish_guard |
| `run-phones.bat` | manual one-click | healthcheck + publish_guard (since 2026-09-19) |

`run-phones.bat` was missing from this table entirely, which is how it stayed an ungated publish
path for a month after the other four were gated. It is the manual twin of `run-phones-nightly.bat`
and does the same rebuild, but it rebases onto `main` with `-X theirs` before pushing, so a poorer
local board wins the merge. The table is the memory; a path absent from it does not get audited.

`run-replies-daily.bat` had **no gates at all** until 2026-09-17, and it is the one that rebuilds and
pushes `docs/index.html` + `docs/call` fastest — so it was the shortest route from a bad local build
to the live site. On 09-15 19:11 it published a 709-phone board over a live 1,148 (commit subject
"replies: morning scan baked into board (auto)"). publish_guard would have refused it. The compounding
part is the part to remember: **that publish became `origin/main`, so it moved the baseline every
later gate compared against**, and subsequent poorer builds then passed legitimately. One ungated
publish does not cost one board, it costs the reference.

If you add a fourth publish path, gate it in the same commit. `grep -l publish_guard *.bat` is the
check — anything that does `git add docs/` and pushes, and is not in that list, is a hole.

## Federal bankruptcy check (CourtListener)

Broward and Palm Beach have no Miami-Dade docket stay. They stay held until a federal
bankruptcy check has run for that lead and found no open matching case. The check is
`bk_lookup.py`. CourtListener (Free Law Project) REST API v4 is the provider. The parked
PACER Case Locator client is still `pacer_stay.py` (#76); it is not called from here.
`BankruptcyProvider` is the seam: `CourtListenerProvider` searches, and
`PacerCaseLocatorProvider.available()` stays false until a PCL account exists.
`DEALFLOW_BK_PROVIDER=pacer` selects that stub and holds every lead that needs the check.

**Token.** `COURTLISTENER_TOKEN`, a Windows user env var on the laptop. Sent as
`Authorization: Token <token>`. Never log it, print it, or write it into a status file.
No token means the check is unavailable and the lead stays held.

**Rate budget (free account, rolling, all concurrent):** 5/minute, 50/hour, 125/day.
A full minute or hour window waits until a slot frees. Only the daily cap, or a 429 that
survives the bounded retries, stops the run. `BK_MAX_RUNTIME_S` (default 900) caps one
nightly run so the 5:30 refresh is not held up; the flsb cursor is saved after every page
and the next run resumes that pull instead of restarting the 14 days. `pull_ok` becomes
true once the cursor is caught up, even if that took several runs. The 5:30 run does not
wait out a full hour. `pipeline_alerts.py evening` (the existing 21:00 task) runs a second
pass when `COURTLISTENER_TOKEN` is set, so leftover daily budget is used after that hour
frees, still under 125/day. Request timestamps use the real clock, so time spent waiting
on HTTP counts toward the windows and toward `BK_MAX_RUNTIME_S`. `python bk_lookup.py
--case 2025-000201` resolves a Miami stem to the `-CA-01` lead. A number with no lead
prints `no lead for this case` and does not write the cache. A cut-off run writes
status reason `time_budget` and still exits 0. The nightly pull reserves 10 requests so a
pre-send search can still run. The send bridge's pre-send check sleeps at most a few
seconds (`PRESEND_MAX_WAIT`); a longer wait returns the lead held. `python bk_lookup.py
--case A --case B` paces fully and prints one line per case (flagged, match type,
bankruptcy case number) with no names. An unreadable `bk_budget.json` is treated as a
spent day, not reset.

**Two reads, both cached under `DEALFLOW_DIR` (never the repo):**

- Nightly, step `[3g2/5]` of `refresh-dealflow.bat` (not a new scheduled task): new
  bankruptcy filings in the Southern District of Florida, court id `flsb`, since the last
  successful pull. Search `type=d`, `court` + `filed_after`, paginated. Matched locally.
  The index can hold a lead. It cannot clear one.
- Once per lead: a party-name search of federal bankruptcy courts only (CourtListener
  court ids ending in `b`, including `flsb`, `flmb`, and `flnb`), filed within
  `BK_FILED_AFTER_YEARS` (default 10), newest first. The court list is one `court`
  parameter with the ids separated by spaces. Repeated `court=` keys are not a list —
  CourtListener keeps the last one — so that form searches a single court and must not
  be used. Re-checked every 14 days, and again before a first touch older than that.
  If that search still overflows the page cap, the same name-token query is run again,
  limited to `flsb flmb flnb` and a shorter filed-after window, with more pages. A
  city, ZIP, or exact phrase is not put in `party:` (that field is names, and party
  names are filed in both orders). Hits from both passes are kept. An open exact or
  plausible match from either pass holds the lead. A lead can clear only when the
  full-court, full-window search finishes without overflowing and finds no open match.
  If the narrow pass overflows or cannot be built, the lead stays truncated and held.
  A finished narrow pass also cannot clear, because it does not cover the whole window.
  That count is `truncated` on the status file, not an error. The nightly run and the
  CLI follow more pages on that second pass. The send bridge keeps the short page cap
  and its few-second wait. CLI and leads next to be contacted may follow more pages
  on the first pass too. A finished full-court search with no open match is not, by
  itself, a release for Broward or Palm Beach. While the PACER provider is parked, that
  result is `clear_unconfirmed` and stays held for first-touch email, follow-up, letters,
  text, and Call Mode. `DEALFLOW_BK_ALLOW_CL_CLEAR=1` is the only switch that lets a
  CourtListener clear release those leads, and setting it is a business decision for the
  owner. An exact name match outside `flsb`, `flmb`, and `flnb` with no address or county
  evidence is `possible`, not a hard hold. Florida exact matches stay hard holds. The Miami
  docket date is cross-checked only while that stay is active (`entry_stay_active`): one
  page of FLSB and FLMB filings for that day, inside the same 125/day budget. A miss does
  not add a hold. A lifted or closed docket bankruptcy does not add one either, and the
  Miami stay-check verdict stays the docket's unless CourtListener found an open match.
  Email and letters (`send_hold`) refuse a keyable
  non-stem lead as soon as this module is importable. Text uses that same stay-gate
  verdict in the send bridge, and its pre-send check will not sleep out a rate window.
  Call Mode and the knock planner
  (`federal_hold`) read `bk_lead_cache.json` once per queue build. Before that file
  exists they still honor a baked `saleBkAct`. Once it exists, a Broward or Palm Beach
  lead with no confirmed clear is dropped, and a Miami lead that is not docket-clear
  (including a `stay_unverified` lis pendens) stays callable unless CourtListener flagged
  it. Within the daily budget the nightly search checks leads next to be contacted
  (email, then phone, then a letter address, soonest auction first), Miami included,
  not only after every non-Miami lead. A case number with fewer than five digits is not
  a bankruptcy key and is not held by this check. If the hold cannot be evaluated, the
  dial queue and the knock planner hold anything that is not a Miami-Dade case number.

**Matching.** Names are folded (case, accents, punctuation). Middle initials, Hispanic
double surnames, `LLC` / `TRUST` owners, and joint owners are all read. An exact open-case
match in `flsb`, `flmb`, or `flnb` is a hard hold. The same name in another bankruptcy
court, with no address or county on the hit, is only `possible`. Any weaker plausible
match is a hold whose reason is `possible bankruptcy: <case number>` and is never
auto-cleared. A closed case is not a hold. `DEALFLOW_DIR/bk_overrides.json` drops one
bankruptcy case number for one lead id after a person has verified the false positive.
Another lead on that case stays held. Dropping the only case still does not release a
Broward or Palm Beach lead unless `DEALFLOW_BK_ALLOW_CL_CLEAR=1`.

Miami-Dade keeps the #72 docket gate. This lookup only adds a hold there. A CourtListener
clear does not lift a docket stay or a PACER active hit.

**Status.** Counts only (`bk_lookup_status.json`: last pull time, filings cached, leads
checked, holds, errors, truncated, requests used) go into `pipeline_alerts` as `bk-lookup`. A failed
pull, a missing status file, or a pull older than 36 hours is a fail alert. Filings carry
debtor names and stay in `DEALFLOW_DIR`. Nothing from this check is committed or published
with a name, a phone, or the token.

**Terms.** CourtListener's terms may restrict revenue-positive commercial use. Alejandro
is asking Free Law Project. Until that is answered, keep the provider swappable and do
not add a second CourtListener client.

## Clerk docket bankruptcy check (Broward / Palm Beach)

`clerk_bk.py` reads the foreclosure case's own docket for a bankruptcy stay. It is off
unless `DEALFLOW_CLERK_BK=1`. With the flag off, sending is unchanged, including
`DEALFLOW_BK_ALLOW_CL_CLEAR`. Miami-Dade is not read here.

With the flag on, a Broward or Palm Beach lead can be contacted only when this source has
fully read the docket and found no active stay, and every other gate still passes. An
active stay holds. A missing read, a stale read, a partial list, a captcha, a bad key, an
unparseable docket, or a check that throws holds. A clerk clear does not override a
CourtListener hold, a PACER active hit, or a Miami docket stay. `DEALFLOW_BK_ALLOW_CL_CLEAR`
is unchanged: a CourtListener clear alone still does not release Broward or Palm Beach.

Broward is read only through the clerk's Commercial Data API (`api.browardclerk.org`),
two GETs per case: `case.json` and `events_and_documents.json`. The key is the Windows
user env var `BROWARD_CLERK_API_KEY`. It is never printed or stored. The public Case
Search site is captcha-gated and its terms forbid commercial use, so this module does not
scrape it and does not solve a captcha. No key means Broward is not read.

Palm Beach eCaseView is captcha-gated and its terms limit the site to non-commercial use.
There is no official docket API. This module does not call it. With the flag on, a Palm
Beach lead stays held.

The 5:30 refresh already runs `bk_lookup.py`, which calls this check only when the flag
is on, and that step still exits 0. `CLERK_BK_MAX_RUNTIME_S` (default 900),
`CLERK_BK_MAX_CASES` (default 25), and `CLERK_BK_MIN_INTERVAL` (default 2.5 seconds)
bound a run. `DEALFLOW_CLERK_BK_MAX_AGE_DAYS` (default 14) is how long a full read keeps
clearing. Cache and status (`clerk_bk_cache.json`, `clerk_bk_status.json`) live in
`DEALFLOW_DIR` and carry counts, verdicts, and dates only.

Acceptance, on the laptop, against real case numbers, one line each and no party names:

```
python clerk_bk_accept.py --case CACE-99-000123
python clerk_bk_accept.py --case 509999CA000123XXXAMB
python clerk_bk_accept.py --entries docket.json --case CACE-99-000123
```

`--entries` classifies a local JSON file (`{"status": "Pending", "entries": [{"date", "text"}]}`).
It is not a live read and it does not clear a lead.

## Scheduled tasks

Register/enable/disable only via `pwsh .\desktop-setup\install-tasks.ps1` — **`pwsh`, not
`powershell`** (the file is BOM-less UTF-8; 5.1 misparses the em-dashes in the header).

**Nine tasks, from two directories.** `desktop-setup/tasks/` holds Windows exports and is
**gitignored** — an export embeds the exporting machine's principal SID and user paths, so those
travel in the transfer bundle. `desktop-setup/task-templates/` is the **tracked** half: SID-free XML
with `__REPO__` / `__PROFILE__` / `__USER__` placeholders the installer substitutes at install time.
An export wins over a template of the same task name. **A new task goes in `task-templates/`** — put
one in `tasks/` and it exists only on the box that made it.

That split exists because of `DealFlow Cadence`. It is the only **unattended** outreach sender in
the project (10:00 daily, `cadence.py` over SMTP, real email to homeowners) and for three weeks it
was registered by hand, outside the installer. `-DisableLocal` caught it — that path enumerates live
tasks by name match — but `-Enable` walked `tasks/*.xml` only, so **disarming was complete and
arming was not**: every handoff done exactly as documented left outreach off, silently. Closed
2026-09-18 by `task-templates/DealFlow_Cadence.xml`.

- The task runs **`cadence-daily.bat`**, never `cadence-run.bat` — that one ends in `pause` and a
  scheduled task cannot answer a prompt.
- `cadence-daily.bat` repo-guards the folder and **refuses to send outside 08:00–20:00**. The task
  is `StartWhenAvailable=true` so a run missed while the laptop slept is caught up rather than lost;
  without the window, that catch-up mails homeowners at whatever hour the machine woke. Do not
  remove one without the other. The window lives in the `.bat` and not in `cadence.py` on purpose —
  `cadence.py` is the reserved suppression surface above.
- `-Only <pattern>` scopes every mode to matching tasks, which is how one task is added or armed on
  a machine whose others are already live:
  `pwsh .\desktop-setup\install-tasks.ps1 -Only Cadence -Enable`.
- **Audit by enumerating, never from a list in a file:**
  `pwsh -c "Get-ScheduledTask | ? TaskName -like '*ealFlow*' | ft TaskName,State"`
