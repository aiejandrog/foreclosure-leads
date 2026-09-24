# Miami title-interest investigation

Opt-in only; no nightly registration, publication, sending or equity writes.

```
python miami_title_discovery.py --case YYYY-NNNNNN-CA-NN --captcha-max-spend 1.00 --vision-max-spend 1.00 --dry-run
```

Repeat `--case` for a shared capped run; remove `--dry-run` to request county records.
Use `--report-only` instead to reconcile previously saved evidence without county
requests or paid reading.
Only Miami leads are accepted. Evidence and the persistent CAPTCHA ledger live in
the private DealFlow `title_discovery` directory. The dossier receives an additive
`title_discovery` section; section d remains unchanged.

## Current boundaries

- Cached county queries and the existing Camoufox session path are enabled.
- Paid CAPTCHA fallback is fail-closed: the existing solver has no enforceable
  per-request dollar ceiling. The API reports cost after completion. Names that
  require that fallback are listed as `paid_cap_unenforceable`, not clean searches.
- This command reuses saved OCR/vision and uses local OCR for newly fetched
  documents. It does not invoke paid vision; `--vision-max-spend` records the
  caller's ceiling, not a promise that vision assessment occurred.
- Current and previous deeds are latest-known candidates, not a title opinion.
  Missing dates, same-date deeds, absent parcel anchors and incomplete party
  extraction remain gaps. Explicit document roles carry page evidence.
- Default work limits are three name-discovery rounds, three citation hops and
  thirty new document fetch attempts per case. Limits create named unknowns.
- A 500-row query is capped. Subdivision-only matches and conflicting folios do
  not establish a property match. Owner-search omissions require an actual
  baseline result comparison.
- Countywide money-judgment/tax-lien search results remain potential claims.
  Debtor identity, certification, exemption and attachment are not decided.
  A read release explicitly citing a claim is reported with its passage; it is
  not automatically applied to debt arithmetic.
- Book/page lookups unsupported by the county remain cited-but-not-fetched.
  Newly recovered deed names beyond this pass remain named unsearched gaps.

Run `_walktest.py`, `_titlepartiestest.py`, `_searchbudgettest.py`,
`_titlediscoverytest.py`, `_titleintegrationtest.py` and normal unittest discovery.
Fixtures are synthetic. No homeowner evidence belongs in Git.
