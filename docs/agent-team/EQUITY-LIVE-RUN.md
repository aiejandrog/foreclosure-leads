# Equity live run: turning on the folio search and the court-copy judgment

Nothing here has been run. Both switches are default off and, off, the code behaves as before.
Everything below that spends money is Alex's call; the numbers say what is verified in code and what
is an estimate.

Run from the laptop checkout only (the pipeline engine), never from a worktree, and never on the
desktop.

## Before either switch

1. Pull the branch that carries #b and #c (claude/agents-equity once merged), then confirm the offline
   tests pass: `python _foliosearchtest.py`, `python _courtjudgmenttest.py`, `python _eqcardtest.py`.
2. Miami-Dade Official Records access: the brief says the OR subscription expired 10/26. I could not
   verify from code what a renewal costs or whether the anonymous search endpoints below need it. The
   repo only documents the Clerk bulk service ($110 per folder per month, $420 per month for the
   Official Records image option, prices checked 2026-09-21 in DEALFLOW-AUDIT-2026-09-21.md); that is
   a different product and may not be the one that lapsed. Confirm what lapsed before paying anything.
3. 2Captcha key present and funded (the paid path). The shared monthly cap in `paid_reads.py` is $50
   and `--max-spend` is capped at $5.00 a run (`MAX_SPEND_CEILING`). Do not raise either.

## (b) Folio-keyed lien search: DEALFLOW_OR_FOLIO=1

What it does: before the name search it asks the clerk for the instruments indexed on the lead's
folio, then scopes the name-search results to that parcel (a record under a different non-blank
folio is dropped) and adds the folio search's own records. If the name search finds nothing and only
the folio result stands, the chain is marked `folio_only` + `truncated`, so it can never read as
clear or priced.

UNVERIFIED: the folio search label and folio format the Miami-Dade standardsearch accepts have never
been exercised from this repo; only `Name/Document` has. The default label is `Parcel ID`, override
with `DEALFLOW_OR_FOLIO_TYPE`. A result that does not carry the folio asked for is discarded, so a
wrong label costs solves and returns nothing; it cannot corrupt a chain. Probe ONE case first.

Commands (PowerShell, laptop, repo root):

```
$env:DEALFLOW_OR_FOLIO = '1'
python records_liens.py --case 2025-023462-CA-01 --max-spend 0.02 --spend-ledger "$env:USERPROFILE\DEALFLOW\folio_probe_ledger.json"
```

Read the output: `ok ... open mtg` with a changed `searched_as`/chain is a hit; `(no records / blocked)`
means the folio label or format is wrong (try `DEALFLOW_OR_FOLIO_TYPE` with the label shown in the
clerk's search menu). Only after one case works, a bounded batch:

```
python records_liens.py --repull --limit 20 --max-spend 0.50 --spend-ledger "$env:USERPROFILE\DEALFLOW\folio_ledger.json"
```

Cost, 2Captcha:
- Verified in code: `PAID_SOLVE_USD = 0.0033` per submitted solve, counted on submit (a failed solve
  may still bill); a search retries up to 3 solves. The folio search goes straight to the paid path
  (Camoufox is not used for it), so it is one solve per lead the first time, and a stored
  `folio:<digits>` token is reused free afterwards if the clerk still honours it (token lifetime is
  unmeasured).
- Estimate: about 370 Miami-Dade leads x 1 solve x $0.0033 = about $1.22 for a full first pass, up to
  about $3.66 if every search needed 3 tries. The 20-lead batch above is about $0.07 to $0.20.
- It adds to the name search's cost, it does not replace it (the name search still runs as before).
- Count against the $50/month shared cap. OR subscription renewal: not verified, see step 2.

Expected effect (estimate, not measured): a common-name owner stops inheriting a namesake's mortgage
that shared the subdivision, so the surviving-debt number drops for those leads, and an
on-parcel loan the surname search missed is added. Chains that were `clear` stay `clear` only if the
name search also ran; folio-only chains are held at UNVERIFIED on purpose. Measure by diffing
`records_liens.json` open_count before and after on the same 20 cases.

To turn it off: unset the variable. Cached chains are unchanged until re-pulled.

## (c) Court-copy judgment: DEALFLOW_COURT_JUDGMENT=1

What it does: records_liens uses a verified final-judgment figure from a stored miami_judgment run
report as `analyze`'s judgment (it picks which open mortgage is the foreclosing first), records
`judgment_source: 'court copy'` on the chain, and the bake stamps `eqc.judgment_source = 'court copy'`
with that figure as the eqc judgment. It reads a file, spends nothing, and returns the board's own
figure whenever the report is missing or fails any check.

The honest limit: `miami_judgment.judgment_for_analyze` refuses every figure read off a scan, and
every Miami judgment seen so far is a scan (the refusal was confirmed by Alex 2026-09-22). So with the
flag on, nothing changes until about 12 corroborated figures have been checked by eye
(`REVIEW_THRESHOLD = 12`, `python judgment_review.py`) and the refusal is lifted in a reviewed commit.
This change does NOT lift it. Expected effect today: none. It makes the lift a one-line decision later.

To produce a report for one case (this is the part that fetches; it needs the case's recorded rows
from an owner or folio search saved as JSON, and OR access):

```
python miami_judgment.py 2026-020206-CC-25 --records or_rows.json --judgments-only --out judgment_reports/2026-020206-CC-25.json
```

The report must land at `DEALFLOW_DIR\judgment_reports\<case>.json`. Then:

```
$env:DEALFLOW_COURT_JUDGMENT = '1'
python records_liens.py --case 2026-020206-CC-25 --reanalyze
```

Cost: OCR is free (Windows). Adding `--vision` is metered Anthropic spend and needs `--max-spend`
(private cumulative ledger, authorization cannot exceed $1 per MIAMI-CASE-TIMELINE.md); not needed to
test the wiring. Document fetches use the OR endpoints: whether they need the lapsed subscription is
unverified. `--reanalyze` is $0 (cached tokens only).

To turn it off: unset the variable.
