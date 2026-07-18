# AGENTS.md

## Cursor Cloud specific instructions

DEALFLOW is a Python data-pipeline + static-site project (no framework, no build system). It
scrapes South Florida foreclosure/tax-deed auctions, enriches each parcel, scores leads, and
renders a self-contained static HTML tracker into `docs/index.html` (published via GitHub Pages).
There is **no test suite and no linter config** in this repo.

<!-- [Cursor cloud edit] section added by the Cursor cloud agent -->
### Edit attribution — REQUIRED for the Cursor cloud agent

The repo owner works in a local Cursor/Claude session AND uses this background **Cursor cloud
agent**. To keep authorship clear, the Cursor cloud agent MUST tag every edit it makes so the
local agent can tell which changes came from the cloud agent.

Convention (applies to **future** edits only — do NOT retro-tag existing code):

- Put a short marker comment at the top of each block the cloud agent adds or modifies, using the
  file's own comment syntax:
  - JS: `// [Cursor cloud edit] <short note>`   (or `/* [Cursor cloud edit] <short note> */`)
  - CSS: `/* [Cursor cloud edit] <short note> */`
  - Python: `# [Cursor cloud edit] <short note>`
  - HTML / Markdown: `<!-- [Cursor cloud edit] <short note> -->`
- Keep it to one line — it's an authorship marker, not a changelog.
- The `cursor/…` branch prefix + commit author are the secondary authorship signal.

The local Claude/Cursor agent should read any `[Cursor cloud edit]` marker as "the Cursor cloud
agent authored this," and is free to keep, revise, or remove those markers as it edits.

### Services / how to run

- **The "application" is the static tracker site.** Serve it locally with the standard
  `python3 -m http.server 8799 --directory docs` (see `.claude/launch.json`, config
  `dealflow-docs`). Note the shipped `docs/index.html` is password-gated/encrypted, so it prompts
  for a password.
- **For UI/dev work, use `design-preview.html`** instead: run `python3 build_preview.py` to
  regenerate it from `tracker_template.html` with fake, plaintext, un-gated sample leads (no
  network needed). This is the fastest way to see the full rendered UI. The design lives in
  `tracker_template.html`; never hand-edit `docs/index.html` (it is generated + encrypted by
  `foreclosure_leads.make_tracker`).

### Lint / test / build

- **Lint (best available):** `python3 -m py_compile *.py`. The docstrings emit harmless
  `SyntaxWarning: invalid escape sequence` — these are not errors.
- **Tests:** none exist.
- **Build the UI (offline, safe):** `python3 build_preview.py` → writes `design-preview.html`.

### Gotchas

- Use `python3` (there is no `python` on the PATH).
- **The scrapers require live access to Miami-Dade / Broward / Palm Beach county sites and are
  IP-blocked from cloud/datacenter IPs** (by design — see `.github/workflows/freshness-watchdog.yml`,
  which notes the real scrape runs on a residential PC). So `foreclosure_leads.py`,
  `county_leads.py`, `skiptrace.py`, `records_liens.py`, etc. will not produce fresh data in the
  cloud VM. Use `build_preview.py` (fake data) to exercise/verify the rendering path instead.
- `foreclosure_leads.make_tracker()` and the `*.bat` files hardcode a Windows Desktop path
  (`C:\Users\...\Desktop\DEALFLOW`); do not run those on Linux (they create junk `C:\...`
  directories). The cross-platform path is `build_preview.py`.
- `healthcheck.py` expects `leads_final.json` (gitignored, absent in a fresh clone) and pings the
  four county upstream sources, so it reports FAIL/DOWN in the cloud VM — that is expected here,
  not a regression.
- Playwright is used for scraping; the chromium browser is installed by the update script
  (`python3 -m playwright install chromium`) and launches fine, but again the target sites are
  IP-blocked in the cloud.
