# Federal bankruptcy check: why `leads_checked: 0` (diagnosis only, 2026-10-30)

Base: origin/main edb6493. Counts and file:line only; no names, no tokens. Nothing was run
against CourtListener, no .bat, nothing written under ~/DEALFLOW. Line numbers are `bk_lookup.py`
unless stated.

## Root cause (3 sentences)
`run_nightly()` sets its 900 s deadline at the top (2492) and then runs `match_filings_to_leads()`
(call 2552, defined 2266) before any lead is searched; that function compares every lead to every
cached filing (4,247 leads x 2,400 filings, fuzzy `cases_from_hits`) and now takes longer than the
whole deadline (a 150-lead sample extrapolates to ~33 min on a loaded machine). The search loop is
guarded by `clock.time() < deadline` (2557); when that is false the loop is skipped and no `reason`
is set, so the status reads `reason: ok, errors: 0, leads_checked: 0` and no alert fires. The flsb
pull is not the cause: it used a few pages (19 requests that day) and the token and budget were fine.

## (1) Order a run spends time and requests
1. `deadline = now + max_runtime_s`: 2492 (BK_MAX_RUNTIME_S default 900, lines 88 and 997).
2. `pull_filings` (call 2523, defined 2428): resumes the flsb cursor, at most PULL_PAGE_CAP=20 pages
   (119), keeps NIGHTLY_PRESEND_RESERVE=10 requests free (125, used at 2470). It returns at once
   when caught up (2438). It cannot eat the whole budget: 20 pages max.
3. `match_filings_to_leads` (2552): pure CPU, no deadline check inside it, no requests. This is where
   the 900 s went on 10-29.
4. Search loop (2557-2610): leads sorted by `contact_rank` (1837: email -3000, phone -2000, mail
   -1000, then soonest auction); skips fresh entries (`_needs_party_search` 2400); breaks on deadline,
   `_slot_wait(..., reserve=10)` returning None (2569, reason `budget`), a 429, or 3 network errors.
   The minute cap of 5 means one request per 12 s at best, so 900 s allows at most ~75 requests even
   if matching cost nothing.
5. Evening: `pipeline_alerts.py evening` (1731-1747) calls `run_nightly()` again at 21:00 with the
   same 900 s. The 10-29 21:16 status is that run (inferred, see Could not verify).

Evidence the 21:16 run spent its time in step 3: bk_budget.json's last hit is 21:02:22, the status was
written 21:16:59 (start ~21:00 + 900 s + the overrun of one matching pass), so 14.5 minutes and zero
requests sit between. pull_state last_success_t is 21:02:22.

## (2) Budget and status
- bk_budget.json: 19 hits in the 24 h window (cap 125): 8 hits 18:39-18:42, 11 hits 21:00-21:02.
  Status: requests_day 19, hour 11, minute 0. About 85% of the day budget was unspent; no 429,
  errors 0.
- bk_pull_state.json: cursor empty, filed_after 2026-10-25, last_success_date 2026-10-29. Caught up.
- Status: pull_ok true, filings 2400, holds 600, truncated 0, reason `ok`.
- Earlier nightly runs did search, but few: leads-run.log lines 151500-188474 show checked 0-32 per run
  (about 15 typical), most ending `hit its time limit` at requests 43-119. The deadline was already
  the binding limit before 10-29, and matching has grown with filings (1,757 to 2,400 in that log).

## (3) Is `leads_checked` what we think?
No, it is per run. `checked` is a local reset to 0 (2551) and `write_status(leads_checked=checked)`
(2618) overwrites the field each run; `presend_check` adds 1 (2681); the crash path writes
`prog['checked']` (2766). It is never a cumulative count of leads with a fresh check. The real
coverage number is the cache (section 4).

## (4) Cache coverage (bk_lead_cache.json 917 entries, newest 2026-10-29; `load_leads` returns 4,247)
| county | no entry | stale/never searched | error, retry | fresh clear | fresh clear_unconfirmed | fresh possible | fresh active |
|---|---|---|---|---|---|---|---|
| BROWARD | 1,818 | 50 | 3 | 0 | 23 | 9 | 11 |
| PALM BEACH | 1,248 | 70 | 3 | 0 | 22 | 4 | 10 |
| MIAMI-DADE | 843 | 75 | 5 | 28 | 0 | 9 | 16 |

Broward + Palm Beach leads that need a search: 3,192 (3,066 with no entry). I could not reproduce
"about 3,345" (see Could not verify). `clear_unconfirmed` is not a release (CLAUDE.md), so the waiver
is what makes those callable. 151 Broward/PB leads are unsearchable (no owner or more than 4 owners;
`search_lead` 2338-2349): they cost no requests but are revisited every run (their entry has
searched=false). Query shape of the 3,192: 2,987 need 1 owner query, 53 need 2, 1 needs 3, 151
unsearchable.

## (5) What a night would need
Cost per lead = distinct owner queries x pages (cap 2, or 6 for leads with an email or phone;
lines 120-122) plus a narrow second pass when the wide one overflows (`truncated` 1-4 per run).
Realistically 1-2 requests for a 1-query lead. Binding limits per run: 125/day minus reserve 10 minus
the pull, and 5/min.
- 900 s x 5/min = ~75 requests per run if matching took 0 s. The 21:00 pass is a second run under the
  same 125/day.
- At 2 req/lead: ~55 leads/night, so 3,192 / 55 = ~58 nights. At 1.5: ~73/night, ~44 nights. At 3
  (some overflow): ~37/night, ~86 nights.
- Steady state: each result expires after RECHECK_DAYS=14, so ~55/night keeps only ~770 leads fresh at
  once. Keeping all ~4,200 on a 14-day cycle needs ~300 searches (~600 requests) a night, about five
  times the 125/day cap. The free-tier cap cannot cover the whole list; ordering (`contact_rank`)
  decides who is covered. Today even 55/night is not reached (0-32).

## Does the status say WHY, and the original request
Request: a failed pull records HTTP status, attempt count and reason, with bounded backoff inside the
existing budget.
- Reason code: PARTLY exists. `reason` is written (2620; allowed list 2207) with ok, token_missing,
  provider_unavailable, pull_truncated, pull_failed, budget, rate_limit, network, time_budget,
  cache_unreadable, crash:<Type> (shown by pipeline_alerts 626-631). It is NOT set when the search
  loop is skipped because matching used the deadline (2557), so that failure reads `ok`.
- HTTP status: NOT in the status file. `ProviderError('CourtListener HTTP %s')` (http_get, ~1096) is
  stored only as the per-lead cache `err` text.
- Attempt count: NOT recorded. `attempt` is local to `http_get` (1055-1077).
- Bounded backoff inside the budget: EXISTS. RETRY_429=3, RETRY_SLEEP=(2,4,8) or Retry-After
  (126-127, http_get ~1079-1090), capped by `deadline` and `max_wait`, and every attempt is recorded
  in the budget. Network errors stop the run after 3 (192, 2597).
- Alert/banner: `pipeline_alerts.bk_lookup_alert` (622-644) fails only on a missing status, `pull_ok`
  false, or a pull older than 36 h. It does not read `leads_checked`, `reason: time_budget`, or the
  count of unchecked leads, so `leads_checked: 0` with `pull_ok: true` never alerts. No board banner
  reads the status file (grep of foreclosure_leads.py and the templates found none); per-lead `bkWhy`
  shows only on a lead.

## Prioritized fix list
All are LEGAL REVIEW (bk_lookup.py is the stay surface). None may change any hold, verdict, clear
rule, waiver, or cache entry content. Not built.
1. S: Honest reason. When the search loop is skipped or cut because the deadline passed during or
   before matching, set `reason='time_budget'` (or `match_overran`) and record `match_s` and
   `needing_search` counts in the status (allowed list 2207). `bk_lookup_alert` warns when
   `leads_checked == 0` and `needing_search > 0` two nights running. Status and alert only.
2. M: Make matching cheap. Index filings by name token once per run and call `cases_from_hits` only
   for filings sharing a token with the lead (or re-match only filings added since the last run plus
   new or changed leads). Acceptance: a fixture test asserts the resulting cache entries are
   identical to today's full cross product, so no hold changes. This returns most of the 15 minutes
   to the search loop.
3. S: Give the search stage its own clock (deadline measured from the end of matching), or raise
   `BK_MAX_RUNTIME_S` for the 21:00 evening pass only. A 900 s run tops out near 75 requests; the
   125/day cap and the 10-request reserve stay unchanged.
4. S: Status fields `last_http_status` and `attempts` (counts only) on a failed request, per the
   original request; the backoff already exists.
5. M: Stop re-visiting the 151 unsearchable leads each run, and add a `needing_search` backlog count
   to `public_status` for the board health line.
6. L (decision for Alex, not code): the free-tier cap cannot keep 4,200 leads on a 14-day cycle; the
   attorney waiver is the practical path for the remainder.

## Could not verify
- The duration of the 10-29 21:00 matching pass: not logged. The 14.5 min gap is inferred from
  timestamps; the 33 min figure is an extrapolation from 150 leads while a refresh was running, so
  treat it as an order of magnitude. Either exceeds 900 s.
- Which process wrote the 21:16 status: inferred to be the 21:00 evening pass (pipeline_alerts 1731)
  from timestamps; no evening log line was found in leads-run.log.
- "About 3,345": I get 3,192 needing a search (3,066 with no entry) from the current lead files. Lead
  files change nightly, and the figure may include `clear_unconfirmed` (+45).
- Requests per lead in practice: there is no per-lead request log. 1-2 is derived from query counts
  and page caps; the log's 5-9 requests per checked lead includes the pull and narrow passes.
- Whether the 18:39 hits (8) were a manual run: not determinable.

## How to recount (read-only)
```
cat ~/DEALFLOW/bk_lookup_status.json ~/DEALFLOW/bk_pull_state.json
python -I -c "import json,time;print([time.strftime('%H:%M:%S',time.localtime(t)) for t in json.load(open('C:/Users/olqbb/DEALFLOW/bk_budget.json'))['hits']])"
python -I docs/bk-diag/recount.py C:/Users/olqbb/projects/fl-bkdx C:/Users/olqbb/projects/foreclosure-leads
python -I docs/bk-diag/timematch.py C:/Users/olqbb/projects/fl-bkdx C:/Users/olqbb/projects/foreclosure-leads
grep -n "CourtListener: status" C:/Users/olqbb/projects/foreclosure-leads/leads-run.log | tail -12
```
The scripts are committed under docs/bk-diag/ (recount.py, timematch.py, qcount.py). recount.py calls
`load_leads(main repo)` and `load_cache()`, buckets each lead by `_needs_party_search` and the cache
verdict, by county. timematch.py times `match_filings_to_leads` on 150 leads with a throwaway cache
that is never saved.
