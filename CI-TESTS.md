# CI tests — what runs on GitHub, what is skipped, and why

`.github/workflows/tests.yml` runs `python ci_suite.py` on every pull request and every push to
`main` (board-only pushes under `docs/` are ignored: the runners' publishes change no code). It needs
**no secrets** and has a read-only token.

`ci_suite.py` runs **every tracked suite** (`_*test.py`, `test_*.py`, `_workerui.py`, `_*test.js`)
**except** the ones named in its `SKIP` table. It is an exclusion list on purpose: a new suite is
picked up automatically and has to pass in CI unless someone adds it to `SKIP` with a reason.

Measured on a clean checkout of `main` at `ee2237d` (2026-09-26, Python 3.13, Node 20, the packages
in `requirements.txt`, no Playwright browsers): **81 suites pass, 38 are skipped, 0 fail.**

`run_suite.py` is unchanged and is still the full suite on the armed PC, where the skipped inputs exist.

## Why each group is skipped

| group | suites | why it cannot run in CI |
|---|---|---|
| **browser** (25) | `_2165test`, `_balloonlanetest`, `_callmodetest`, `_casestatustest`, `_codelientest`, `_cyberaddrtest`, `_deedtest`, `_dnctest`, `_docroomtest`, `_emailtest`, `_fieldsheettest`, `_funneltest`, `_gallerytest`, `_lanetest`, `_mirrortest`, `_nearmetest`, `_optouttest`, `_plantest`, `_portfoliotest`, `_redfintest`, `_senderdefaulttest`, `_stalefixtest`, `_taxtest`, `_workeremailtest`, `_workerui` | They drive the board in Playwright Chromium. The browser is not installed in CI, and most of them also open a real built board. |
| **live data: board inputs** (7) | `_cstest`, `_eq30test`, `_filtertest`, `_gatetest`, `_hangertest`, `_phonepagetest`, `_suppressiontest` | They need the gitignored `site.codes` and/or `leads_final.json`: the access codes and the lead file, which must never be on a public runner. `_suppressiontest` passes 17 of its 19 checks in CI. The two it cannot run are the board/worker person-level gate, which needs `site.codes`. |
| **live data: opt-out ledger** (3) | `_attachtest`, `_ledgerwritetest`, `_sendbridgetest` | They start `send_server.py`, which **fails closed** without a real `optouts.json` (the 2026-09-05 staleness guard). With no ledger, every `/send` is refused, which is the correct behaviour and the reason they cannot pass here. |
| **live data: feed** (2) | `_digesttest`, `_dupetest` | They read the real lead feed, replies and board on disk. |
| **machine** (1) | `_lktest` | It hard-codes the laptop's `C:\Users\olqbb\...` path. |

**What is *not* skipped, although you might expect it to be:**
- **`fitz` (PyMuPDF)** installs from `requirements.txt` on Linux, and every suite that imports it passes, so they all run.
- **The `.bat` suites** (`_batsyntaxtest`, `_refreshexittest`, `_taskinstalltest`) read the files as text and run fine on Linux.

## Turning a skipped suite back on

Most of the **live data** group can come back with a small, fake, committed fixture: a throwaway
`site.codes`, a five-lead `leads_final.json` with invented people, and an `optouts.json` with fake
keys, all written into a temp directory by the suite itself. None of these should ever be real
data; the repo is public. The **browser** group can come back by adding
`python -m playwright install --with-deps chromium` to the workflow, but most of those suites also
need a built board, so each one needs checking separately.

## Commands

```
python ci_suite.py            # what CI runs; exit 1 on any failure
python ci_suite.py --list     # what runs and what is skipped, with reasons
python ci_suite.py --skipped  # also try the skipped ones and report (informational)
```
