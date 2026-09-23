# Miami document backfill

Preview every unique Miami case in `leads_final.json`, without clients, downloads,
OCR, checkpoint writes, API billing or captcha solves:

```powershell
python -u run_documents.py --backfill --vision --vision-max-spend 1.00 --dry-run
```

`--leads-file` can select a different local snapshot. `--backfill` includes
ownerless cases as explicit gaps and ignores the nightly default case limit.
Conflicting duplicate case rows stop the run rather than choosing one silently.

After explicit spending approval, replace `--dry-run` with `--enable`.
This is **not** a nightly activation instruction. The preview's dollar figure is
the cumulative ceiling, not an estimated price to finish every case. The current
case reader targets judgments and optional citation walks; this mode does not
promise complete court-document or title coverage.

The cap is required, positive and finite. It is shared across all cases and
resumptions of the private `_backfill_state.json` checkpoint. Raising the cap
explicitly authorizes a larger total, not a fresh allowance. An interrupted API
request conservatively retains its worst-case reservation. Do not delete the
checkpoint to get more budget. Automatic paid retries are disabled.

Normal restarts resume interrupted cases and reuse the existing document queue
and cached paid page reads. Completed *attempts* with gaps are not called complete;
use `--retry-gaps` after access or evidence improves. Changed lead inputs or
processing options are reconsidered. This is a one-pass backfill checkpoint, not
a substitute for nightly docket refresh. Only one backfill worker can own it.

Captcha minting, paid name searches and the separate interpretation path are
rejected in this mode. Vision uses the API only. Without `--vision`, collection
and local OCR remain free, but the explicit cap is still required.

Nothing writes equity, leads, the public board, or the refresh batch file.
Private evidence and checkpoints must never be committed.
