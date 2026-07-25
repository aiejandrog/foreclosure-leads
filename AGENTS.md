# Agent notes

## Cursor Cloud specific instructions

### Call sheet Chrome-parity UX (additive only)
- **Chain story** (`_chainStory` in `tracker_template.html`): presentational timeline from `orliens[]` — OPEN/SATISFIED by date + REFI WINDOW tags. Does **not** change equity math or the satisfaction matcher.
- **Auction verify**: already on the call-sheet Clock (`r.auc` → RealForeclose). Do not re-add.
- **Tax chip** (`_taxStatusHtml`): honest `TAXES — not checked` until the operator types `btax` (incl. `0` = current). Delinquency is Cloudflare-walled — do not scrape it.
- **Do not** rewrite `records_liens.analyze` satisfaction matching (Echeverri false-sat risk).
- **Defer** docket “defendant dropped” parsing until a labeled corpus exists; if added, use orthogonal `dropped_parties[]` that never feeds lien math.

### Velima / empty `orliens` = acquisition, not parsing
- Case `2025-016135-CA-01` / folio `3022320150450` / VELIMA AUGUSTIN: CI logged `ok … 3 case(s)` on OCS then `-- … no records` on Official Records **alongside dozens of other owners the same day** → systemic OR fetch/mint failure (Turnstile/token), not a Velima-only name bug. Fix upstream in `gen_records_qs` / `records_liens` mint path; do not “fix” satisfaction for missing chains.

### Captcha
- OR Turnstile needs `captcha.key` / `TWOCAPTCHA_KEY` (Actions secrets; often absent in Cloud Agent VMs).
