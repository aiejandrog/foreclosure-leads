# Email deliverability QA report

Worktree fl-email, branch claude/agents-email-deliverability, base origin/main b829b54.

## Baseline (FINAL, clean origin/main b829b54, ci_suite.py in fl-baseline)

Source: C:\Users\olqbb\AppData\Local\Temp\fl_baseline_ci.txt (rc= written). Totals per Lead: 224 pass / 11 fail / 39 skipped.

Baseline FAILs (pre-existing, not attributable to this branch):
_ballooncurrencytest.py, _callworkflowparitytest.py, _judgmentpasstest.py, _lpcoveragetest.py,
_openertest.py, _resimplisynctest.py, _syncgatetest.py, _texttpltest.py, _tracerfytest.py,
_uifixturetest.py, _workerrunlogtest.py

Any FAIL in the branch's final ci_suite.py run that is not in this list is a FAIL.

## Tasks 1-4 (branch HEAD 4f36274, content 1f03047) -- all PASS

Scope: `git diff origin/main...HEAD --stat` = _emailqualitytest.py, docs/EMAIL-DELIVERABILITY.md, docs/agent-teams/pr/email-deliverability.md, reports/email_quality.py, plus this report. No product code. call_objections.json shows modified in the worktree (test churn); not committed.

Static checks: no smtplib/imaplib/socket/urllib/requests/subprocess in reports/email_quality.py or _emailqualitytest.py; no import of cadence/outreach_email/send_server/warmup. Email addresses in committed files are the company's own published DNS/sender addresses (rua mailboxes, alejandro@ alias) or `example.test` fixtures; no homeowner names, phones or addresses. The report prints counts only.

### Task 1 -- doc cites and DNS: PASS
- Cited symbols exist on main (grep of `def`/constants in send_server.py, deliverability.py, warmup.py, ramp_status.py): _bounce_health, _first_touch_cap, _ramp_cap, _lane_from, _thread_from, _first_touch_senders, _reserve_first_touch_from, _senders_active, _recipient_verdict, _wilson_lower, _post_gate_send, BOUNCE_DAY_CEILING 0.05 / MIN_SAMPLE 20 / HARD 2 / HARD_MIN 10, BOUNCE_CEILING 0.10, PROBE_DAILY_CAP 10, PROVEN_MIN_AGE_DAYS 2, daily_cap 300, recipient_cap 450, require_clear called from send_server and outreach_email `_smtp_send`, placement_hold/_placement_hold, WARN 4.0 / HIGH 8.0, warmup.quota = min(15, 3+day), START_DATE 2026-09-07.
- senders.json matches the doc: main_domain_cap 40, alias ramp 5/10/20/40/70/100, first_touch ramp 10/15/20/30/40 from 2026-09-27; days 39 and 33 on 2026-10-29 verified.
- PR #258 (`git diff origin/main...origin/pr/258`): active/early/balloon lanes -> bsgflorida.com, warmup.off added, first_touch.from untouched. Matches.
- PR #290: bounce_scan.py SETTLE_HOURS 2.0, MAX_AGE_HOURS 36.0, DEALFLOW_BOUNCE_SCAN_GATE, SHA-256 hashes, postmaster matching, exit 2 on partial scan, 451 `bounce_scan` hold in send_server, cadence and outreach_email wiring. Matches (9 files, +941/-25).
- DNS: `nslookup -type=TXT _dmarc.<d> 8.8.8.8`, SPF TXT, `google._domainkey.<d>` TXT and MX for bsgflorida.com, biscaynesolutionsgroup.com, bsgfl.com. Records match the doc table exactly; each DKIM selector present; MX smtp.google.com pref 1.
- Unverified Google/DNS facts (~all wording, Admin console path, report-mailbox existence, Gmail bulk rules, alignment reasoning) are labelled believed/unverified in the doc.

### Task 2 -- tests and report: PASS
- `DEALFLOW_DIR=<temp> python _emailqualitytest.py` -> `PASS _emailqualitytest`, rc=0.
- `DEALFLOW_DIR=<temp> python reports/email_quality.py` -> rc=0, "EMAIL QUALITY (counts only)", all six files `missing`, `WARNING: ... missing or unreadable: the counts above are a floor, not the list.` Counts only.
- `python deliverability.py score --template` -> rc=0, `Content score 1.5 (ok; warn at 4, high at 8). 203 words.` flags `+1.0 free`, `+0.5 topic`. Purity: main(score) -> sample_first_touch -> score_content; inspect.getsource shows no smtp/imap/urlopen/requests/socket/write in those; the only `open(` calls in main are the `--body-file` and `headers` file branches, not `score`. `git status` unchanged afterwards (no writes).

### Task 3 -- copy rewrite (doc section 6): PASS
- Extracted the code block from the doc: `disclaimer.identity('en')` is in it verbatim; `outreach_copy.OPTOUT_LINE_EN` and `OPTOUT_LINE_ES` are in it verbatim (True/True/True).
- Forbidden-word regex (preapprov, deadline, free, no cost, costs you, fee, $, price, auction, highest bidder, dated countdowns) -> no matches.
- `deliverability.score_content` on a local render with from_addr='' (how `score --template` scores): booking slug `free-records-review` = 1.0 (flag: free); slug `records-review` = 0 (no flags). Matches the doc's 1.0 / 0.0. Note, not a defect: with a real From address the `links` rule adds +0.5 (cal.com differs from the sender domain), same as it would for today's template; and my render is 100 words vs the doc's 104 (signature block).
- Doc flags the rewrite as a legal call needing attorney sign-off and does not change code.

### Task 4 -- ramp math (doc section 7): PASS
Recomputed Wilson 95% upper bound (z=1.96): <=2% needs n = 189 (0 bounces), 280 (1), 361 (2), 437 (3). Allowed bounces at n=300/400/600/800/1000 = 1/2/5/8/11. Binomial table (rate > 2%) reproduced exactly: n=20 9.5/18.2/26.1/33.2, n=70 4.8/15.5/28.3/41.0, n=140 3.4/16.6/35.1/53.2, n=280 0.3/6.4/24.6/48.9. 31/85 = 36.5%, Wilson 27.0%-47.1%. Current-ramp combined cumulative 140/350/630/1050. All match.

### Full suite
`DEALFLOW_DIR=<temp> python ci_suite.py > <temp>\email_ci.txt 2>&1` -> `CI SUMMARY: 225 pass / 11 fail, 39 skipped`, rc=1.
FAIL set is identical to the baseline's 11 (_ballooncurrencytest, _callworkflowparitytest, _judgmentpasstest, _lpcoveragetest, _openertest, _resimplisynctest, _syncgatetest, _texttpltest, _tracerfytest, _uifixturetest, _workerrunlogtest). `comm` of the two FAIL lists is empty both ways. Pass count is baseline 224 + the new _emailqualitytest.py. No new FAIL.

## DECISIONS-NEEDED entries seen
D7 (booking slug contains "free"), D8 (copy rewrite, attorney), D9 (evidence-gated ramp), per emaildev's message and docs/agent-teams/pr/email-deliverability.md; I did not open DECISIONS-NEEDED.md itself (it lives in the fl-agents-patterns worktree, out of my scope).

## Verdict: tasks 1, 2, 3, 4 PASS
