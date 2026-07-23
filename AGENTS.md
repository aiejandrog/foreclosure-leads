# Agent notes

## Cursor Cloud specific instructions

### Captcha / county Official Records
- Miami-Dade Official Records uses **Cloudflare Turnstile**. Solves need `captcha.key` or `TWOCAPTCHA_KEY` / `CAPTCHA_KEY` (GitHub Actions secrets — usually **not** injected into Cloud Agent VMs).
- Without a captcha key, skip live OR sweeps (`records_liens.py`, `lis_pendens.py`); use fixtures or Actions runs.

### Lis Pendens (Fresh filings)
- **Working free method:** `python lis_pendens.py --days 14` — lender/HOA **name** + `documentType=LIS PENDENS - LIS` + date window (Turnstile). Cap with `--limit N` for smoke tests.
- **Walled:** blank-name document-type/date search always returns `isValidSearch:false` (do not burn solves on `--blank` except as a regression probe).
- **Full docket (paid):** Clerk [Commercial Data Services](https://www.miamidadeclerk.gov/clerk/commercial-data-services.page) Official Records folder ≈ $110/mo.
- Output `lis_pendens.json` (gitignored) is merged by `make_tracker` as `stage`/`st`=`LP`. UI: **Fresh filings** button. Daily refresh runs the sweep when `captcha.key` is present (`refresh.yml` `[2c/5]`).

### Board / PLAY
- Every lead gets a PLAY badge (`_playFor` in `tracker_template.html`). LP rows get **LP-EARLY**.
- Standard pipeline commands and sequencing: see `.github/workflows/refresh.yml` and `HOW-TO-USE.md`.
