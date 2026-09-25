# Miami whole-case timeline

`run_case_timeline.py` consumes the existing Miami collector, document store and resumable queue. It is not a second collector and does not publish, send, schedule, or write equity inputs.

```powershell
python -u run_case_timeline.py --case <Miami-case-number> --collect --vision-max-spend 1.00
```

Add `--vision` only to read money-bearing pages using the Anthropic API. The existing private `title_discovery/vision-budget.json` ledger is shared across cases and commands; it must exist, cannot be reset by this command, and the cumulative authorization cannot exceed $1. Cached API responses are reused. OCR is free. No subscription reader is used.

Every acquired page uses existing OCR or receives supplemental 300-DPI OCR where the canonical reader used embedded text. Supplemental results checkpoint by PDF hash and page; the original embedded text is preserved. Failed OCR, missing images/attachments/pages, unverified docket pagination and capped amount reads remain named gaps. OCR-only amounts and vision transcriptions are unverified observations, not accepted balances.

Outputs are `<case>-timeline.json` and `<case>-timeline.md` beside the private case dossier. A pointer is added to the dossier without changing its section d. No evidence files belong in Git.

Status uses dated operative entries, not the county's summary case-status label. Motions are not orders; calendar events are not proof a sale occurred. Same-date conflicting entries and activity conflicting with an unresolved bankruptcy stay remain `unclear`. A docket bankruptcy notice is evidence of a reported filing, not an independent adjudication of the stay's scope. Body headings override index classifications only when recognized; `index_agrees` retains disagreement. Unknown filers are not guessed from listed parties.

Pending motions mean no matching disposition was identified in the available record, not proof the court has never disposed of them. Only explicit docket deadlines are reported; no legal deadline is computed. A missing complete docket or illegible page prevents a claim of complete case coverage.

Tests:

```powershell
python -m unittest _casetimelinetest _timelineacquisitiontest _timelineamounttest _timelineocrtest
```
