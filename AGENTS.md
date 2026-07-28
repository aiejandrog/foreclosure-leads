# AGENTS.md

## Cursor Cloud specific instructions

DEALFLOW is a **Python 3 batch data pipeline** (Miami-Dade / South Florida foreclosure & tax-deed
lead scraper), not a client/server app. There is no database, no Node, no docker, and no background
service to keep running. The "app" is a set of standalone Python scripts that scrape + enrich county
data and emit a single self-contained static tracker at `docs/index.html`. Canonical run order lives
in `.github/workflows/refresh.yml`; local Windows wrappers are the `*.bat` files.

`python` is aliased to Python 3 on this VM and deps are already installed (see the startup update
script + `requirements.txt` + Playwright Chromium). No lint or unit-test framework exists;
`healthcheck.py` is the closest validation (it needs `leads_final.json` to exist first).

Non-obvious caveats when running things here:

- **RealForeclose bot-walls this datacenter IP.** `python foreclosure_leads.py` and
  `county_leads.py` do a Playwright calendar scrape of `*.realforeclose.com`, which returns 403 /
  hangs from the cloud VM. Do not expect the full scrape to complete here. The Miami-Dade Property
  Appraiser proxy (`apps.miamidadepa.gov`) and Clerk OCS (`miamidadeclerk.gov`) **are** reachable,
  so enrichment, `lookup.py`, and `healthcheck.py` upstream pings work.
- **To exercise the pipeline without the blocked scrape**, feed committed real auction data
  (`auctions_*.json`, which are the scrape output format — note the file is a JSON-encoded string,
  so `json.loads` twice) through `enrich` → `enrich_clerk` → `qualify` → `make_tracker` from
  `foreclosure_leads.py`, after adding an `AuctionDate` (`%m/%d/%Y`) and `sale_type` to each item.
  That runs the true enrich/score/build engine against the live reachable county APIs.
- **Always set `DEALFLOW_NO_DESKTOP=1`** when running the pipeline/scripts — otherwise scripts try to
  write to a Windows `OneDrive/Desktop` path.
- **`make_tracker` requires `leads_final.json`** (gitignored, produced by the pipeline). `docs/index.html`
  is committed — rebuilding overwrites it, so `git checkout -- docs/index.html` if you only rebuilt it for a test.
- **`lookup.py` (and some scripts) call `webbrowser.open`/`os.startfile` at the end**, which spawns
  Chrome and looks like a hang on this VM. The output HTML is written *before* that call — run with a
  `timeout` and ignore the trailing hang.
- **Preview the tracker UI:** `python -m http.server 8799 --directory docs` then open
  `http://localhost:8799/`. The tracker is fully client-side (filters, letter generation, notes in
  `localStorage`); the core action is filling "Sender identity" under **Filters & setup**, then
  clicking **✉ Letter** on a lead.
- All third-party API keys (`TRACERFY_API_KEY`, `BATCHDATA_API_KEY`, `GOOGLE_STREET_VIEW_KEY`, Lob) are
  **optional** — without them the pipeline still builds a public, phone-free tracker.
