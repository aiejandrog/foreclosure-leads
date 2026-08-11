# BATCHDATA-EXIT — Phase 0: Inventory

*2026-08-11. Read-only pass by four parallel reviewers over the files named in the task brief.
No code changed, no county site visited, no terms page accepted. Every claim carries file:line.
Line numbers drift — two load-bearing comments already cite stale ones (see §7) — so future
phases must match anchors by content, not number.*

---

## 0. The verdict up front

**BatchData is already 90% exited.** Cloud billing is dead (refresh.yml:49 hardcodes
`BATCHDATA_API_KEY: ''`), local spend is capped at $1.50/day (bd_budget.py:33) with
batchdata_liens limited to 4 lookups/run (~$0.92, refresh-dealflow.bat:65). The auto-provider
in skiptrace.py prefers Tracerfy and only falls to BatchData because `tracerfy.key` does not
exist (skiptrace.py:122-127). **Dropping a funded tracerfy.key file exits BatchData for
skip-tracing with zero code changes.**

The real work in this plan is not killing BatchData. It is:
1. **fixing the LP sweeper, which is broken NOW** (§1 — the pre-foreclosure lane is a month stale),
2. **backfilling 168 board leads whose lien chains exist only in the BatchData cache** (§5 —
   publish_guard will hard-block a naive exit), and
3. **not repeating two documented money-math bugs** when a fourth feed touches the lien merge (§6).

---

## 1. CRITICAL FINDING (pre-existing, unrelated to BatchData): the LP sweeper is statically broken

`lis_pendens.py:65` runs `.replace('SITEKEY', G.SITE_KEY)` at module level, but the rewritten
`gen_records_qs.py` no longer defines `SITE_KEY` (it uses `records_liens.TS_SITE_KEY` via
`R.` since the Turnstile migration). **`import lis_pendens` raises AttributeError — verified by
execution 2026-08-11.** Consequence: no Miami-Dade lis pendens sweep has run since the
migration. The newest filing in lis_pendens.json is dated **7/9/2026 — 33 days stale**. Every
"PRE-FORECLOSURE / just filed" pitch in Carlos's door book is running on month-old filings.
This is the first thing Phase 1 must fix, before any multi-county work.

---

## 2. Complete billing call-site inventory

| # | Site | What | Bills today? | Free replacement |
|---|------|------|--------------|------------------|
| 1 | `skiptrace.py:272-278` | POST /property/skip-trace, $0.15/call, charged even on miss | **Only if tracerfy.key absent** (auto-provider prefers Tracerfy :122-127) | Tracerfy $0.10 (already wired, identical output schema via shared `_collect` :480-487) — zero-code exit. TPS free lane exists but is NOT semantically interchangeable (§4). |
| 2 | `batchdata_liens.py:239` | POST /property/lookup/all-attributes, ~$0.23/call (derived :35-41), charged incl. misses :248 | **Yes** — refresh-dealflow.bat:65, `--limit 4` ≈ $0.92/run max | records_liens.py (MD, 2Captcha ~$0.003/solve) + broward_liens.py (free, CF-fingerprint) + palmbeach_liens.py (2Captcha v2) — all three EXIST and work; gap is coverage backfill, not machinery (§5). |
| 3 | `refresh-dealflow.bat:65` | Local nightly batchdata_liens run | **Yes** (the only live lien biller) | Delete the line; add palmbeach_liens.py to the [2b/5] block (PB was the "no other path" county — no longer true). |
| 4 | `refresh-dealflow.bat:93` | Local skiptrace run | Only if tracerfy.key missing | Keep a funded tracerfy.key present. |
| 5 | `refresh.yml:280` | Cloud batchdata_liens step | **DEAD** since 2026-08-02 (:49 `BATCHDATA_API_KEY: ''`) | On exit: delete the step + the commented secret line :48 so nobody restores it. |
| 6 | `refresh.yml:345-350` | Cloud skiptrace step | Tracerfy-only in cloud (BD env is '') | Simplify gate to TRACERFY_API_KEY only. |
| 7 | `bd_budget.py:33` | Not a biller — the shared $1.50/day hard cap, ledger batchdata_spend.json | — | KEEP until last BD call dies; `BATCHDATA_DAILY_CAP=0` is the instant kill switch. Then repurpose as a request budget (its gate-inside-retry-loop pattern at skiptrace.py:269-272 is worth preserving verbatim). |
| 8 | **Windows Task Scheduler "DEALFLOW Phones" 6AM** (outside repo) | Nightly skiptrace | **Yes, invisible to the repo** | Audit `schtasks` at exit time. bd_budget.py:10-12 records that two scheduled tasks once double-billed the same day. |

Also on paid rails but **not** BatchData and **not** on the shared ledger:
- `captcha_solver.py` (2Captcha): MD Turnstile ~6s/solve, PB reCAPTCHA v2 1-3 min/solve. The
  LP sweep costs up to 34 plaintiffs × 3 solves/run. This spend SURVIVES the exit and grows with it.
- `whitepages_lookup.py:161`: Whitepages Pro ~$0.10/call, capped only per-run (200,
  :51) — **escapes the daily dollar ceiling entirely.** Fold into the shared ledger in Phase 5 or
  the exit recreates the exact multi-spender failure bd_budget was built to stop (bd_budget.py:3-18).

---

## 3. What each BatchData product actually provides (what must be replaced)

**Skip-trace** (skiptrace.py): phones with `type/carrier/dnc` flags + emails + entity/county.
Cache census: 809 rows, 100% source=batchdata, 93.4% with ≥1 phone, **84.6% with ≥1 email**,
3,185 phone dicts all carrying the provider dnc/type flags.

**Lien/all-attributes** (batchdata_liens.py): recorded mortgage chain + **two things no free
feed has**: per-lien `currentEstimatedBalance` (payoff-ish 'bal', :107) and the `bd_value` /
`bd_eqpct` AVM (:152-154). 330 cached rows, 301 with chains. **Keep the cache file forever** —
this data is bought and paid for; the exit only stops NEW purchases.

---

## 4. Skip-trace free path: the "interchangeable" claim is structurally true, semantically false

Verified write-path-by-write-path (skiptrace.py:480-487 vs skiptrace_free.py:280-287):

| Field | Paid (BD/Tracerfy) | Free (TPS) | Downstream consequence |
|---|---|---|---|
| `phones[].dnc` | provider flag | **hardcoded `False`** | foreclosure_leads.py:1292-1297 bakes phdnc from it → DNC-unknown numbers presented to the caller as affirmatively safe. **Compliance hazard, not a nice-to-have.** |
| `phones[].type` | mobile/landline/voip | `''` | :1298 labels every TPS phone "landline" → UI mutes it, withholds WhatsApp. Free numbers render as dead weight even when mobile. |
| `emails` | populated (84.6% of rows) | **always `[]`** | The email machine — the #1 outreach channel — silently starves. No error anywhere. |
| entity/LLC handling | traces Sunbiz officer behind company owners (:224-239) | skips all companies (:102) | Eligible universe materially smaller. |
| failure signaling | exit codes 0-5 taxonomy | **exits 0 after a captcha-wall abort** (:269-277) | Nightly automation reads cookie death as success. |
| operator loop | consumes retrace_queue.json (bad-number button) + `--retry-empty` | neither | Worker bad-number flags only heal through the paid script. |

Plus cookie fragility: manual Chrome export, days-to-weeks lifespan, DataDome kills sessions,
recovery is a human re-export (:23-26, :52-70). **Recommendation: Phase 4 as written
(TPS-as-default) is the weakest part of the plan. The $0.10 Tracerfy path IS the exit for
skip-trace; TPS stays what it is today — a manual supplement.** The ToS exposure of
cookie-scraping TPS is a real risk being traded against a ~$22 total backlog cost.

---

## 5. Lien coverage: the numbers that gate Phases 3 and 5

Live board marker (docs/index.html, 2026-08-11 06:00): 871 leads, 251 lien chains, 476 phones.

- Simulated merge: orconf distribution **ok 43 / low 40 / bd 168** / no-chain 495.
- **Dropping the bd feed sinks liens 251 → 83 (−67%).** publish_guard.py compares the coverage
  marker vs origin/main with thresholds liens(ratio 0.50 / abs 20) — **a naive exit is BLOCKED
  at publish.** The 168 bd rows must be re-traced through county records BEFORE the feed is cut.
- Healthcheck edges: MD lien coverage 59%→34% (WARN) without bd; **Broward sits exactly on the
  25% FAIL edge with or without bd** — one lost chain tips it red, so a broward_liens.py catch-up
  belongs EARLY, not in Phase 5. Palm Beach reads 32%→0% but is FAIL-exempt via a **stale**
  "no free path" comment (healthcheck.py:103-105) — palmbeach_liens.py exists, and healthcheck's
  cache list (:73-75) doesn't even load palmbeach_liens.json. Fix both in the same phase.
- `actions/cache` trap: removing batchdata_liens.json from refresh.yml's restore/save path lists
  (:125, :510) silently changes the cache VERSION hash and orphans the whole state cache. Bump
  `dealflow-state-v11 → v12` deliberately and expect one cold run (comment :147-161).
- Verdict shifts are expected and must be messaged: 'bd' trust is 0.72 (tracker:2524) and is
  excluded from nonMortConf; re-tracing those 168 as 'ok'/'low' raises trust to 1.0/0.85 and
  enables "verified none" on HOA/code/IRS buckets — equity verdicts and Closers membership WILL move.

---

## 6. The two money-math bugs any new feed must not resurrect

1. **Seniors-only vs juniors-bundled** — `_senior_surviving` (foreclosure_leads.py:767-784) is
   the single seam: records/broward emit surv = seniors+juniors (subtract juniors after),
   BatchData emits seniors-only. Applying the records-style subtraction to a seniors-only feed
   **erased an $811,577 first mortgage to $0** (case 502024CA012300XXXAMB, docstring).
   A fourth feed adds a branch HERE and nowhere else. Regression test required.
2. **HOA-election bug** (batchdata_liens.py:121-130 verbatim): electing "the foreclosing loan"
   on HOA cases deleted a $248,000 surviving mortgage and **turned $46,680 of real equity into
   $294,680 of fiction.** Related: `orsurvsen` phantom guard (foreclosure_leads.py:792-798 —
   "$458,777 of invented first mortgage… fixing one without the other exposes the phantom").
3. Provenance floor: the "hardcoded $0 ≠ verified none" rule (tracker_template.html:2699-2702)
   must carry into the new orconf tiers — 'partial' must be excluded from nonMortConf exactly
   as 'bd' is today.

---

## 7. Stale line references in load-bearing comments (fix opportunistically)

- tracker_template.html:2700 cites `batchdata_liens.py:124` — the hardcoded $0s now live at :150.
- foreclosure_leads.py:773 cites `batchdata_liens.py:113` — the seniors-only sum now lives at :139.

---

## 8. LP pipeline generalization seams (Phase 1-2 map)

Zero BatchData anywhere in this lane (grep-verified). The canonical row shape is made in exactly
one function — `lis_pendens.normalize()` (:148-170) — and every rung of lp_resolve reaches the
network through exactly one choke point — `q(where)` (lp_resolve.py:78, lp_resolve2.py:42). The
county dispatch inserts at:

| Seam | File:line | What swaps per county |
|---|---|---|
| a | lis_pendens.normalize() :148 / lp_sweep() :173 | recorder client (raw clerk schema → canonical row) |
| b | lp_resolve LAYER/FIELDS :62-66 + q() :78 | parcel endpoint + column map (FOLIO/OWNER1/OWNER2/SITE_ADDR/CITY/ZIP/LEGAL) |
| c | lp_resolve2.q() :42 | same adapter |
| d | lp_values._fetch_one() :71 | MD → PA proxy; all others → **fl_cadastral.enrich()** (statewide FDOR, all 67 counties, market value + homestead + sqft + year; loses beds/baths/DOR text) |
| e | lp_status.check() :63 | per-county case-status client (OCS is MD-only, sibling_cases.py:14); TERMINAL/DISMISS docket vocabularies must be re-measured per clerk, never copied |
| f | lp_leads.build() :81 | county literal :148 + deep-link URL table :103,:171,:174-175 |

Known ordering hazard (bit us 2026-08-10): `lp_resolve.main()` merges with whole-row replacement
(:723-728), stripping lp_values enrichment. Canonical order:
`lis_pendens → lp_resolve → lp_resolve2 → lp_values → lp_status → lp_leads`. Encode it in one
driver script in Phase 2 so it can't be run wrong.

County scraper reuse (all machinery exists):
- **Broward** (broward_liens.py): no captcha; Cloudflare TLS-fingerprint wall — only native
  Windows System32 curl passes (:3-6, 47-51); doctype codes harvested live off the form with
  DOCTYPES_FALLBACK (:40-44). Date-range + doctype search: supported by the portal's own form.
  **FTP bulk feed: no code comment about it exists in the repo — the brief's claim comes from
  the county's disclaimer page, which was deliberately not visited in this pass. Confirming and
  accepting those terms is the operator's click, not the tooling's.**
- **Palm Beach** (palmbeach_liens.py): every search reCAPTCHA-v2 gated, one-shot tokens, 1-3 min
  per 2Captcha solve — the captcha-fragile county.
- **Miami-Dade** (records_liens.py): Turnstile-gated mint (~6s/solve), results endpoint UNGATED,
  one-word partyName quirk (:103-108).

---

## 9. What free data CANNOT replace (the honest list)

1. Per-lien `currentEstimatedBalance` (payoff proxy) — recorded chains carry ORIGINAL amounts only.
2. The bd AVM (`bd_value`/`bd_eqpct`) — comps.py ARV + assessed value are different numbers.
3. Provider DNC/type/carrier flags on phones — no free source; a separate scrub step or the flags
   stay unknown (and must be rendered as unknown, never as safe).
4. Emails at skip-trace time — TPS yields none; 84.6% of the current cache's email coverage is
   provider-sourced.
5. Effortless Palm Beach — the free path exists but costs a 1-3 minute captcha solve per search.

---

## 10. Recommended plan deltas vs the original brief

1. **Phase 1 gains a step 0:** fix the lis_pendens.py import break and re-sweep MD — the lane is
   33 days stale TODAY. This is a one-line fix (`G.SITE_KEY` → `R.TS_SITE_KEY` via records_liens)
   plus a sweep run, and it un-blocks everything else.
2. **Phase 4 inverts:** Tracerfy (funded key, $0.10, schema-identical) becomes the default
   provider; TPS stays a manual supplement. This exits BatchData for skip-trace on day one with
   zero code risk and no ToS exposure. The brief's own acceptance test (`_skipfreetest.py`,
   schema-identical incl. DNC flags) cannot pass on TPS as written — see §4 table.
3. **Broward catch-up moves to Phase 1.5** (before the exit) because its healthcheck already sits
   on the FAIL edge independent of BatchData.
4. **Phase 5 adds:** fold whitepages_lookup.py into the shared request ledger; audit Task
   Scheduler for out-of-repo spenders; reconcile the ~$99 open BatchData invoice question
   (operator task — repo cannot see billing).

## 11. Residual risk (one paragraph)

Palm Beach is the captcha-fragile county: one-shot reCAPTCHA tokens at 1-3 minutes per solve
mean a portal tweak or 2Captcha outage stalls PB sweeps silently — pair it with a
records-returned-zero-on-a-weekday healthcheck FAIL as the brief specifies. Broward's wall is a
TLS fingerprint coin-flip that currently only native Windows curl passes; a Cloudflare policy
change breaks it without warning, which is exactly why the county's official bulk feed (if its
terms allow automated use — operator's call to accept) is the highest-leverage hardening
available. Miami-Dade is the most stable (6-second Turnstile solves, ungated results endpoint)
but its clerk has already migrated captcha vendors once and killed the sweeper for a month
without anyone noticing — the missing piece is not scraping muscle, it is the freshness alarm:
"LP sweep returned 0 records for a county on a weekday" must FAIL, and the board must print the
sweep's as-of date, because stale-data-presented-as-current is the one failure mode this
pipeline has now demonstrated twice.
