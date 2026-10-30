# Equity: one eqc card per lead, folio-keyed lien search, court-copy judgment (flags off)

## Plain English
1. `equity_state.card(lead)` builds one object per board row, baked as `eqc`, that says whether an
   equity figure may be spoken: state (clear / priced / unpriced / none / unchecked), the judgment and
   where it came from (court copy / docket / not posted), the recorded liens (amounts are recorded,
   never called payoffs), the basis, and `may_quote`, true only for clear or priced WITH a known
   judgment. It reuses `state_of`, `lender_foreclosure`, `demote_for_bank_fc` and diligence_flags
   `basis_of` / `known_debt_of`; nothing is re-derived. Call Mode and the board read it.
2. `DEALFLOW_OR_FOLIO=1` (default off): the Miami lien pull searches by folio first, drops any
   instrument recorded on a different folio from the name search, and adds the instruments the folio
   search found. A common-name owner stops pulling another parcel's mortgage. If only the folio result
   exists the chain is `truncated`, so it can never read clear or priced.
3. `DEALFLOW_COURT_JUDGMENT=1` (default off): a stored, verified miami_judgment report supplies the
   judgment to `analyze`; the chain records `judgment_source: 'court copy'`; eqc shows it.

## Tests run (DEALFLOW_DIR=<temp>)
- `python _eqcardtest.py`: ALL PASS (16 checks: five states, lender demotion, second-case demotion, judgment not posted, court copy, no-raise)
- `python _foliosearchtest.py`: ALL PASS (16 checks incl. common-name owner no longer pulls another parcel's mortgage; default search URL unchanged; folio-only chain is not a FACT)
- `python _courtjudgmenttest.py`: ALL PASS (8 checks; scan-read figures still refused; torn report and path-like case are safe)
QA output: see the QA verdict in status/equity.md.

Exposure: none (no sending, suppression or stay code touched). Spend: none by default; with the flags
on, (b) adds about one 2Captcha solve (~$0.0033) per lead searched, inside the existing per-run and
monthly caps. See EQUITY-LIVE-RUN.md.

Flags (default off): DEALFLOW_OR_FOLIO, DEALFLOW_OR_FOLIO_TYPE (label override), DEALFLOW_COURT_JUDGMENT.

Visible after the next refresh-dealflow.bat: `eqc` on every board row (the flags stay off, so lien and
judgment values do not change).

## Caveats
- The folio search label (`Parcel ID`) and folio format are unverified against the live clerk; the
  first live probe is one case (EQUITY-LIVE-RUN.md). A response not carrying the folio is discarded.
- (c) changes nothing today: `judgment_for_analyze` still refuses every scan-read figure, and every
  Miami judgment is a scan, until the 12-case review is done. Not lifted here.
- `eqc.judgment` is the as-entered judgment at bake time; post-judgment interest accrual runs later
  in the build and is not in the card.
- `eqc.net` uses `known_debt_of` (judgment plus the largest single recorded chain figure): a
  direction, not a payoff.
- `_*.py` is gitignored: the three new tests were added with `git add -f`.
- Owned-surface bugs found: none.
