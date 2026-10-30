# Email deliverability: how sending works today, what #290 and #258 change, DNS, copy, ramp

Branch `claude/agents-email-deliverability`, based on origin/main b829b54. Written 2026-10-29.
Nothing here sent mail, opened SMTP/IMAP, or spent money. Counts only; no addresses, names or phones.
Every claim cites `file:function` on main unless it says PR.

## 0. The short version

1. The system is already fenced on three sides: per-address ramp caps, a first-touch bounce pause, and a seed-test placement pause. The pauses are tuned to catch disaster (5% a day, 10% a week), not to hold bounces under 2%. Holding under 2% is a list-quality problem; the ramp only limits the damage (section 7).
2. DNS for all three domains already has SPF, DKIM and DMARC published. The gaps are DMARC `p=none` everywhere and a cold, unmeasured reputation, not a missing record (section 4).
3. The first-touch copy scores 1.5 (ok). The two flags are the word "free" (from the booking URL slug) and "foreclosure". A copy rewrite scores 0 (section 6). Placement into real inboxes is still unmeasured: no external seeds exist.
4. #290 holds follow-ups until a clean bounce scan covers the last email. #258 pauses warm-up and routes every lane through bsgflorida.com. They interact (section 3).

## 1. Sending path today (main)

### 1.1 Who sends and from where
- One login sends everything: the bsgflorida.com account in `bsg_gmail.key`. Aliases are Gmail "Send mail as" addresses. `senders.json` `_comment` says Google signs DKIM with the alias domain; `send_server._senders_active` turns the whole lane map off if the login is not a `@bsgflorida.com` account.
- Lane to From: `send_server._lane_from` reads `senders.json` `lanes`. replied/urgent/default = bsgflorida.com; active/balloon = biscaynesolutionsgroup.com; early = bsgfl.com. If the lane's alias is still at a zero cap, it falls back to the default (main domain) address.
- Follow-ups reuse the address that last mailed that person: `send_server._thread_from`.
- First touches (recipient never mailed by this system) are different: `send_server._first_touch_senders` / `_reserve_first_touch_from` rotate between the two addresses in `senders.json` `first_touch.from` (biscaynesolutionsgroup.com and bsgfl.com). The main domain is excluded in code (`_first_touch_senders` drops any address on `main_domain`). bsgflorida.com "rests" because 31 of 85 first touches bounced on 2026-09-19 (`senders.json` first_touch `_comment`).
- Reply-To is pinned to one scanned mailbox (`senders.json` `reply_to`), so replies reach `replies.py` regardless of how the alias domains are attached.

### 1.2 Caps (all counted from the `mail_sent.json` ledger)
| Cap | Value | Where |
|---|---|---|
| Whole bridge, messages/day | 300 | `send_server.Handler.daily_cap`, enforced in the `/send` "daily cap" block |
| Recipients/day (BCC counts) | 450 | `send_server.Handler.recipient_cap` |
| bsgflorida.com, flat | 40/day | `senders.json` `main_domain_cap`, `send_server._ramp_cap` |
| Alias ramp, per alias, from `ramp_start` 2026-09-21 | day 1-3: 5; -7: 10; -14: 20; -21: 40; -28: 70; then 100 | `senders.json` `ramp`, `send_server._ramp_cap` (zero before `ramp_start`) |
| First-touch ramp, per alias, from `first_touch.ramp_start` 2026-09-27 | day 1-7: 10; -14: 15; -21: 20; -28: 30; then 40 | `senders.json` `first_touch.ramp`, `send_server._first_touch_cap` = min(this, the alias's own ramp cap) |
| Slow restart ceiling | 10 first touches per sending DOMAIN per day while the post-gate sample is under 20 sends, or the bounce list is missing | `send_server.FIRST_TOUCH_RESTART_CAP`, `_ft_restart_ceiling`, `_restart_ceiling_on` |
| Unverified-address probe lane | 10/day | `send_server.PROBE_DAILY_CAP`, `_probe_quota_left` |

With the dates above, today (2026-10-29) is ramp day 39 for the alias ramp and day 33 for first touches, so both are at the last rows (100 and 40 per alias per day), subject to the 10-per-domain restart ceiling and the bounce pause below. This is arithmetic from the dates, not a measurement of what went out; `python ramp_status.py` prints the live numbers (read-only; `ramp_status.main`).

### 1.3 Who may be mailed at all (before caps)
`send_server._recipient_verdict` returns a code; only `delivered`, `replied`, `zerobounce_valid` pass (`GATE_PASS`). `dead` (on `bounced_emails.json` or verifier says dead), `awaiting_delivery` (mailed under 2 days ago, `PROVEN_MIN_AGE_DAYS`), `catch_all`, `risky`, `unverified` are held. `FIRST_TOUCH_ADMIT_ZEROBOUNCE_VALID = True` lets a ZeroBounce "valid" through as a first touch. Other gates (stay, opt-out, bankruptcy) are outside this document.

### 1.4 Warm-up
`warmup.py` sends ordinary messages from the two alias addresses to company-owned inboxes only (`warmup.ALIASES`, `warmup.RECIPIENTS`). Quota `min(15, 3 + day)` per alias per day from `START_DATE` 2026-09-07 (`warmup.quota`, `day_number`), logged to `warmup_log.json`, not the outreach ledger. It is not a cap on cold mail; it is extra volume on the wire from the same aliases. `ramp_status.py` exists because the two were never summed (`ramp_status.cold_today`, `warm_today`: cold + warm = real volume).

### 1.5 The 5% bounce pause (first touches) and the 10% breaker (follow-ups)
`send_server._bounce_health` joins the ledger to `bounced_emails.json`:
- Cohort = first-touch rows (`touch='first'`) sent at or after the verified-send gate, 2026-09-26 18:21:42 UTC (`FIRST_TOUCH_GATE_CUTOFF`, `_post_gate_send`). Older rows do not feed the pause.
- `ft_rule = 'paused'` when, per day or in aggregate, the post-gate cohort rate is above `BOUNCE_DAY_CEILING` = 5% and at least `BOUNCE_DAY_MIN_SAMPLE` = 20 were sent; or a day has at least `FIRST_TOUCH_HARD_BOUNCES` = 2 bounces over 5% with at least `FIRST_TOUCH_HARD_MIN` = 10 sent. Exactly 5% does not trip.
- Under 20 post-gate sends the rule is `slow_restart` (10 per domain per day). A missing `bounced_emails.json` is `unmeasured` and gets the same cap. An unreadable one pauses.
- `send_server.first_touch_paused` / `_first_touch_bounce_paused` fail closed. `/send` refuses a first touch when paused (the `first_touch_paused` block in `Handler.do_POST`).
- Separate, for everything else: `BOUNCE_CEILING` 10% on the Wilson lower bound (z 1.96) of the trailing 7-day rate (`_wilson_lower`, `BOUNCE_WINDOW_DAYS`). `blocked` limits follow-ups to verified addresses; replies to a live conversation are exempt.
- Timing hole: `bounced_emails.json` is written by `bounces.py` (IMAP). A day's bounces are not on the list until the next harvest, so the pause trips a day late (`send_server` comment above `BOUNCE_DAY_CEILING`).

### 1.6 Placement hold (seed test)
`deliverability.py`: `placement --send` mails the real first-touch template from every sending address to seed mailboxes and finds it over IMAP (`run_placement`, `locate`). `verdict_for` writes `placement_status.json` (`write_status`). A sender whose probe lands in spam, goes missing or fails DMARC is `hold`. `placement_hold` / `_placement_hold` is called from `require_clear`, which both `send_server._smtp_send` and `outreach_email._smtp_send` call; cadence goes through `outreach_email`/`send_server`. A pause never expires by age; only a clean later test lifts it. `DEALFLOW_PLACEMENT_HOLD=0` turns it off; `DEALFLOW_PLACEMENT_REQUIRED=1` makes "never tested" a hold too. Exempt: `reply_send`, mail only to our own domains, and the probes (`EXEMPT_GATES`, `_internal_only`).
- Important limit (the module says so): with no `placement_seeds.json` the only seed is our own Workspace mailbox, which is intra-org and proves routing, not inbox placement (`deliverability` docstring). Real placement needs external Gmail/Yahoo seeds. I did not check whether `placement_seeds.json` exists on the laptop; the fresh worktree has none.
- Content score (`score_content`) is advisory unless `DEALFLOW_SPAM_HOLD_SCORE` is set (`content_hold_score`). Default WARN 4.0 / HIGH 8.0.
- Not covered by the hold: `warmup.py` (internal mail), and `cadence.py`'s legacy hand-built SMTP branch (per CLAUDE.md, reported not fixed).

## 2. PR #290 and PR #258: what would change

### #290 `origin/pr/290`: bounce follow-up hold (9 files, +941/-25)
- New `bounce_scan.py` (`followup_hold`, `last_clean_scan`). A follow-up (address already in the ledger) is held unless, for every mailbox a bounce could land in, there is a clean scan that started at least 2 hours (`SETTLE_HOURS`) after the last email, searched back to a day before it, with no failed run since, no older than 36 hours (`MAX_AGE_HOURS`), and the list the send read still contains every address that scan wrote (SHA-256 hashes in `bounce_scan_status.json`). A missing or old-format status file holds every follow-up.
- `bounces.py` now scans every mailbox in `replies._cred_map()` (it used to open only the first), also matches `postmaster` notices (Microsoft), records per-mailbox status, and returns exit 2 on a partial scan.
- Wired in `send_server.do_POST` (new `bounce_scan` 451 hold, `_followup_bounce_hold`), `cadence._run`, `outreach_email._eligible`, and `/health` (`_bounce_scan_health`). Switch: `DEALFLOW_BOUNCE_SCAN_GATE=0`.
- Effect: closes the 2026-10-19 case where two follow-ups hit addresses whose first email had already bounced. Cost: if `bounces.py` is not scheduled to run at least every 36 hours on a box that holds the right credentials, every follow-up from that box stops. It does not touch first touches.

### #258 `origin/pr/258`: warm-up pause and sender lanes (3 files)
- `warmup.off` (tracked) plus `DEALFLOW_WARMUP_OFF=1` make `warmup.main` exit before sending. The file's text says warm-up mail to the owned inboxes was landing in spam and 30 identical messages a day with no replies was feeding it (Alex, 2026-10-16).
- `senders.json`: `active`, `early`, `balloon` lanes now map to `alejandro@bsgflorida.com`. `first_touch.from` is NOT changed, so first touches still leave from the two alias domains.
- Consequences to decide on: (a) every non-first-touch send (replies, urgent, active, early, balloon follow-ups) now draws on the 40/day `main_domain_cap` and puts all follow-up volume on bsgflorida.com; (b) the alias domains get cold first-touch volume with no warm-up behind them, which is the situation warm-up was meant to soften, while main-domain reputation (the 09-19 bounce domain) carries the follow-ups; (c) `ramp_status.py` will show warm = 0. This is a reasoned reading of the diff, not a tested outcome.

## 3. How #290 and #258 combine
They can merge independently: #290 touches `send_server.py`, `cadence.py`, `outreach_email.py`, `bounces.py`; #258 touches `senders.json` and `warmup.py`. Under #258, follow-ups come from bsgflorida.com; under #290 those follow-ups also need a clean scan. Together they make follow-up volume small and slow, which is probably intended; they do nothing for first-touch list quality.

## 4. DNS (Google Workspace), measured 2026-10-29 with `nslookup -type=TXT <name> 8.8.8.8`

Domains: bsgflorida.com (main), biscaynesolutionsgroup.com and bsgfl.com (aliases). Source: `deliverability.OUR_DOMAINS`, `senders.json`.

### Currently published (all three)
| Domain | SPF | DMARC | DKIM selector `google` | MX |
|---|---|---|---|---|
| bsgflorida.com | `v=spf1 include:_spf.google.com ~all` | `v=DMARC1; p=none; rua=mailto:help@bsgflorida.com; pct=100` | present (RSA key, 2048-bit by length) | `smtp.google.com` pref 1 |
| biscaynesolutionsgroup.com | `v=spf1 include:_spf.google.com ~all` | `v=DMARC1; p=none; rua=mailto:postmaster@biscaynesolutionsgroup.com, mailto:dmarc@biscaynesolutionsgroup.com; pct=100; adkim=s; aspf=s` | present | `smtp.google.com` pref 1 |
| bsgfl.com | `v=spf1 include:_spf.google.com ~all` | `v=DMARC1; p=none; rua=mailto:postmaster@bsgfl.com, mailto:dmarc@bsgfl.com; pct=100; adkim=s; aspf=s` | present | `smtp.google.com` pref 1 |

Each domain has exactly one SPF record (more than one is a permerror; `deliverability.dns_report` flags it).

### The records each domain should carry (target state)
SPF, all three, unchanged (publish at the apex, one record only):
```
v=spf1 include:_spf.google.com ~all
```
`~all` is what Google's Workspace setup page documents (believed; I did not re-read Google's docs this run, unverified). `-all` is stricter; do not switch until DMARC reports show nothing legitimate is failing SPF.

DKIM, all three: host `google._domainkey.<domain>`, type TXT, value `v=DKIM1; k=rsa; p=<public key>`. The key value cannot be invented; it comes from Admin console, Apps, Google Workspace, Gmail, Authenticate email, one entry per domain, then "Start authentication" (path believed, unverified). The keys are already published; what DNS cannot show is whether authentication was started for each domain. `senders.json` `_comment` claims it was verified end to end for the two aliases on 2026-09-07 (DKIM `d=` is the alias). For bsgflorida.com nothing in the repo proves signing is on: save one received message ("Show original") and run `python deliverability.py headers message.eml` (reads a local file only).

DMARC, in two phases. Phase 1 (now, safe, no deliverability effect): keep `p=none`, make the three records alike, and make sure reports land in a mailbox that exists:
```
_dmarc.bsgflorida.com                TXT  v=DMARC1; p=none; rua=mailto:help@bsgflorida.com; adkim=r; aspf=r; pct=100
_dmarc.biscaynesolutionsgroup.com    TXT  (leave as published)
_dmarc.bsgfl.com                     TXT  (leave as published)
```
Phase 2 (after at least 14 days of aggregate reports showing every legitimate source aligned): `p=quarantine; pct=25`, then 100 after another 14 clean days. `p=reject` only after that. I would not do Phase 2 before first-touch volume exists to show in the reports, since `p=none` with no reports tells you nothing.

Things I could not verify:
- Whether `postmaster@` and `dmarc@` on the two alias domains are real mailboxes. For a Workspace alias domain, mail to `dmarc@alias` reaches the user only if `dmarc@` exists on the primary domain (user, alias or Group); otherwise the reports bounce. Check by looking for DMARC report email from Google or others since 2026-09-07. Mailing reports for one domain to an address on another domain requires an authorization record `<reported-domain>._report._dmarc.<receiving-domain> TXT "v=DMARC1"` (RFC 7489 section 7.1; believed).
- Strict alignment (`aspf=s`) on the aliases: with Gmail send-as through the one login, the SMTP envelope sender is probably the login's domain, so SPF would pass for bsgflorida.com but not align with the alias From. DMARC then passes on DKIM alone (`adkim=s`, `d=` equals the From domain). That works as long as DKIM stays on for both aliases; if either key is rotated or authentication is stopped, mail from that alias fails DMARC. This is reasoned from how Gmail send-as works, not tested here.
- Gmail's published sender guidelines (SPF + DKIM + DMARC, one-click unsubscribe for bulk senders at 5,000/day) are not checked here. Daily volume is well under 5,000, so the bulk rules likely do not apply (unverified).

### Check commands (free, public)
```
nslookup -type=TXT bsgflorida.com 8.8.8.8
nslookup -type=TXT _dmarc.bsgflorida.com 8.8.8.8
nslookup -type=TXT google._domainkey.bsgflorida.com 8.8.8.8
nslookup -type=TXT biscaynesolutionsgroup.com 8.8.8.8
nslookup -type=TXT _dmarc.biscaynesolutionsgroup.com 8.8.8.8
nslookup -type=TXT google._domainkey.biscaynesolutionsgroup.com 8.8.8.8
nslookup -type=TXT bsgfl.com 8.8.8.8
nslookup -type=TXT _dmarc.bsgfl.com 8.8.8.8
nslookup -type=TXT google._domainkey.bsgfl.com 8.8.8.8
nslookup -type=MX bsgflorida.com 8.8.8.8
python deliverability.py dns          # same lookups through dns.google; read-only network
python deliverability.py headers message.eml
```

### What Alex must do at the registrar
Nothing is missing. SPF, DKIM and DMARC exist on all three domains. Optional now: the Phase 1 DMARC line for bsgflorida.com. Later: Phase 2 DMARC. In the Admin console (not the registrar): confirm DKIM authentication is started on all three domains, and confirm the DMARC report mailboxes exist.

## 5. Email list quality (`reports/email_quality.py`)
Read-only, local files, counts only; look-up order DEALFLOW_DIR, `--data-dir`, then beside the code. Test: `python _emailqualitytest.py` (invented `example.test` fixture in a temp dir; PASS).

Real run, 2026-10-29, against `~/DEALFLOW` (`paths.P.DEALFLOW_DIR`): all six inputs (`leads_final.json`, `skiptrace_results.json`, `mail_sent.json`, `bounced_emails.json`, `optouts.json`, `verified_emails.json`) were missing, so every count is 0 and the report says so. These gitignored ledgers sit beside the code in the main checkout, which this run was told not to touch. To get real numbers: on the laptop run `python reports/email_quality.py --data-dir <main checkout path>`. Nothing about list quality is claimed from this run.

Report definitions: source = where an address first appeared (`skiptrace[:provider]` or `leads:<file>`; leads rows carry no per-email provenance); opted out = address key `@addr` or the lead's case key in `optouts.json`; clean sendable = no prior bounce, no opt-out, not verifier-dead; "clean and never sent" = the first-touch pool. It does not apply the stay, bankruptcy or `never_contact.json` gates, so it is an upper bound on what the bridge would send.

## 6. First-touch copy: score and rewrite

### Score (`python deliverability.py score --template`)
Pure: `main` -> `sample_first_touch` (imports `outreach_copy`, renders a made-up owner) -> `score_content` (regex only). No network, no file writes, no SMTP. Confirmed by reading those functions.

Result: **1.5, level ok** (warn at 4, high at 8), 203 words. Flags:
- +1.0 `free` in the body: the word comes from the booking URL slug `cal.com/bsgflorida/free-records-review`, not the prose.
- +0.5 `topic`: "foreclosure" in "mortgages and foreclosure workouts".
The mandated sentences are stripped before scoring (`_strip_mandated`), so the identity sentence and opt-out line add nothing.

What the lint does NOT catch but a human reader and a real filter weigh: the dated auction countdown ("scheduled for auction on ...", "If nothing changes before that date, the house is sold ..."), "It costs you nothing" (fee/no-cost language the regex misses because it only matches "no cost/fee/charge" and "no obligation"), and the date-specific urgency. The score is a lint, not a placement measurement.

### Rewrite proposal (doc only; no code changed)
Subject stays: `Regarding your property` (`outreach_copy.email_subject`; already neutral).

```
Hi {first},

I'm writing about your property. A public court record shows an open case on it, so I wanted to introduce myself.

My name is Alex with Biscayne Solutions Group. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.

Our senior advisor has spent over 30 years in mortgages and loan workouts, and he personally reviews each case. If you'd like, I can set up a short phone call to go over where things stand and the options you have. Most calls take about five minutes.

You can pick a time at {booking_url}, reply with the best number and time to reach you, or call me directly at {phone}.

Best regards,

{signature block as today}

If now's not a good time, just tell me and I won't reach out again.
Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```
- Disclaimer: the identity sentence is `disclaimer.identity('en')` verbatim, as today. Spanish copy keeps `disclaimer.identity('es')`.
- Opt-out: `outreach_copy.OPTOUT_LINE_EN` and `OPTOUT_LINE_ES` verbatim. Today the English email carries only the EN line; this proposal adds the ES line to every email as requested, which is a copy change.
- Removed: the auction date, the "sold to the highest bidder" countdown (deadline), "It costs you nothing" and "free" (fee language), the "walked them through it with their equity intact" claim, the "some you may not have heard of" bullets. No "preapproved", no fee or price language.
- Scored with the real `score_content` on a local render: **1.0** with the current booking URL (the slug's "free" still fires), **0.0** with a slug without "free" (e.g. `cal.com/bsgflorida/records-review`), 104 words. A first touch under ~150 words reads best per the lint's own note.
- Risks: the "public court record shows an open case" sentence is true for any lead on the board, but it is new homeowner-facing wording and the "30 years" and equity sentences were attorney-approved swaps (`outreach_copy.email_body` comments, SWAP 1 and 2). Changing homeowner-facing copy is a legal call (CLAUDE.md); this needs the attorney's sign-off before it goes anywhere near code. Removing the date also removes the personalization that likely drives replies; whether it trades reply rate for inbox rate is unmeasured. Test with `placement --send` using external seeds before and after.
- The slug rename is a booking-page change outside this run's files; it is in DECISIONS-NEEDED.

## 7. Proposed daily ramp that keeps bounces under 2%

### Inputs (what is known and what is not)
- 2026-09-19: 31 of 85 first touches hard-bounced = 36.5% (Wilson 95%: 27% to 47%) (`senders.json` first_touch `_comment`; arithmetic mine). That was before the verified-send gate.
- A prior unverified segment (yahoo/aol-heavy) showed a 56% false-valid rate on "proven" addresses (`send_server` comment in the deliverability-evidence block, reported there, not re-measured).
- Post-gate first-touch bounce rate: unknown. The ledgers were not readable in this run (section 5). Every figure below is conditional on it.
- Current pause thresholds: 5% per day at 20+ sends, or 2 bounces at 10+ sends. That is 2.5 times the 2% target.

### Math
A daily rate of 2% cannot be observed at small volume: at 20 sends one bounce is 5%. So hold the 2% as a cumulative, post-gate rate and use the 95% Wilson upper bound. Smallest sample whose upper bound is at most 2%: 0 bounces needs 189 sends; 1 needs 280; 2 needs 361; 3 needs 437. Largest bounces allowed at n sends: n=300 -> 1; 400 -> 2; 600 -> 5; 800 -> 8; 1000 -> 11. (Wilson, z=1.96, computed by me.)
How likely a given week looks bad if the true rate is p (binomial; "bad" = observed rate above 2%):
| sends in the window | p=0.5% | p=1% | p=1.5% | p=2% |
|---|---|---|---|---|
| 20 | 9.5% | 18% | 26% | 33% |
| 70 | 4.8% | 16% | 28% | 41% |
| 140 | 3.4% | 17% | 35% | 53% |
| 280 | 0.3% | 6.4% | 25% | 49% |
Reading it: to hold a 140-send week under 2% with ~97% odds, the true bounce rate has to be about 0.5% or lower. The ramp cannot deliver that; only the pre-send filter can. Volume decides how many bounces you take while learning, the filter decides the rate.

### Ramp (first touches per day, both alias addresses combined; replaces the shape in `senders.json` `first_touch.ramp` only if Alex adopts it)
| Tier | Per day | Advance when | Cumulative sends at advance |
|---|---|---|---|
| 1 | 20 (10 per alias) | 0 bounces after the first 190 sends | 190 (about day 10) |
| 2 | 30 | at most 1 bounce at 280, or at most 2 at 360 | 360 (about day 16) |
| 3 | 40 | at most 5 at 600 | 600 (about day 22) |
| 4 | 60 | at most 11 at 1,000 | 1,000 (about day 29) |
| 5 | 80 | steady state; stop-loss below stays on | |

Stop-loss, in addition to the existing 5% pause: if cumulative bounces exceed the tier's allowance, do not advance; if the last 7 days of first touches exceed 2% on 70 or more sends, drop one tier. These counts are cumulative from 2026-09-26 18:21 UTC (the gate cutoff), so the 1,000-send checkpoint includes whatever has already gone out since then; once the ledger is read, tier placement should be set by that count, not restarted at 190.
Compared with today's `first_touch.ramp`: 20 -> 30 -> 40 -> 60 -> 80 per day combined is the same shape (current cumulative: 140 by day 7, 350 by day 14, 630 by day 21, 1,050 by day 28). The difference is the evidence gate on each step. The existing config advances on the calendar whether or not a single bounce was observed.

Conditions for the 2% target to be realistic (not guaranteed): only `delivered`, `replied` or `zerobounce_valid` recipients (already `GATE_PASS`), run `bounces.py` daily so the next day's pause sees yesterday's bounces, and measure the real post-gate `zerobounce_valid` bounce rate from the ledger first. If that measured rate is above 1%, no ramp gets under 2% and the list or the verifier is the problem.
