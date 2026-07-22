# Debt-stack stress test — 2026-07-22 03:44 UTC

## What I did

1. Expanded `build_preview.py` with edge-case leads (HOA-all-senior, empty chain, fore-only, junior-not-named, multi-senior mixed juniors, unstamped inference).
2. Rebuilt `design-preview.html`.
3. Ran JS classifier checks against the live preview (`_chainGroups` / `_namedInSuit` / `_chainBoardHtml`).
4. Ran Python role-stamp / `_dt` vs `MM/DD/YYYY` ordering checks (the bug that used to mis-count juniors).
5. Rendered **Call sheet + Deal analysis** for every stress lead in Playwright and saved screenshots.

## Scoreboard

- JS cases: **7/7 PASS**
- Python checks: **3/3 PASS**
- Screenshots: **22** → `/opt/cursor/artifacts/screenshots/debt-stack-stress`

## Case-by-case

### `01-senior-fore-named-junior` — PASS

Classic BONY Mellon chain: senior JP Morgan survives; Solarcity named → wiped.

```
{
  "case": "2024-000101-CA-01",
  "senior": [
    "JP MORGAN CHASE BANK NA"
  ],
  "fore": [
    "BANK OF NEW YORK MELLON (THE)"
  ],
  "junior": [
    "SOLARCITY CORPORATION"
  ],
  "other": [],
  "ftype": "MTG"
}
```

<img alt="01-senior-fore-named-junior chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/01-senior-fore-named-junior-board-1.png" />

### `02-hoa-all-senior` — PASS

HOA foreclosure: every open mortgage sits ahead of the association claim → all SENIOR.

```
{
  "case": "2023-000212-CA-01",
  "senior": [
    "WELLS FARGO BANK NA",
    "BANK OF AMERICA NA"
  ],
  "fore": [],
  "junior": [],
  "other": [],
  "ftype": "HOA"
}
```

<img alt="02-hoa-all-senior chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/02-hoa-all-senior-board-1.png" />

### `03-empty-chain` — PASS

No orliens — board must show empty/manual pull message, not crash.

```
{
  "case": "2025-000318-CA-01",
  "senior": [],
  "fore": [],
  "junior": [],
  "other": [],
  "ftype": ""
}
```

<img alt="03-empty-chain chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/03-empty-chain-board-1.png" />

### `04-fore-only-zero-senior` — PASS

Only the foreclosing loan is open (satisfied prior ignored) → senior = none / $0.

```
{
  "case": "2022-000455-CA-01",
  "senior": [],
  "fore": [
    "JPMORGAN CHASE BANK NATIONAL ASSOCIATION"
  ],
  "junior": [],
  "other": [],
  "ftype": "MTG"
}
```

<img alt="04-fore-only-zero-senior chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/04-fore-only-zero-senior-board-1.png" />

### `05-junior-not-named-title-risk` — PASS

Junior Sunrun not in defs → TITLE RISK (the defect you inherit).

```
{
  "case": "2024-000777-CA-01",
  "senior": [],
  "fore": [
    "U.S. BANK NATIONAL ASSOCIATION"
  ],
  "junior": [
    "SUNRUN INC"
  ],
  "other": [],
  "ftype": "MTG"
}
```

<img alt="05-junior-not-named-title-risk chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/05-junior-not-named-title-risk-board-1.png" />

### `06-multi-senior-mixed-juniors` — PASS

Two seniors + named GreenSky + unnamed Orange Solar in one board.

```
{
  "case": "2023-000888-CA-01",
  "senior": [
    "CITIMORTGAGE INC",
    "COUNTRYWIDE HOME LOANS"
  ],
  "fore": [
    "DEUTSCHE BANK NATIONAL TRUST COMPANY"
  ],
  "junior": [
    "GREENSKY LLC",
    "ORANGE SOLAR HOLDINGS LLC"
  ],
  "other": [],
  "ftype": "MTG"
}
```

<img alt="06-multi-senior-mixed-juniors chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/06-multi-senior-mixed-juniors-board-1.png" />

### `07-unstamped-infer-roles` — PASS

No role stamps on liens — UI must infer fore from plaintiff name + date order.

```
{
  "case": "2022-000999-CA-01",
  "senior": [
    "HSBC BANK USA"
  ],
  "fore": [
    "NEWREZ LLC DBA SHELLPOINT MORTGAGE SERVICING"
  ],
  "junior": [
    "SYNCHRONY BANK"
  ],
  "other": [],
  "ftype": "MTG"
}
```

<img alt="07-unstamped-infer-roles chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/07-unstamped-infer-roles-board-1.png" />

## Python checks

- `PASS` **py-role-stamp** — `{'JP': 'senior', 'BONY': 'fore', 'SOLAR': 'junior'}`
- `PASS` **py-dt-vs-d-string** — `{'bad_string_sum': 130000, 'good_dt_sum': 30000}`
- `PASS` **py-hoa-senior**

## Photo index

- `00-dashboard`: Dashboard overview → `/opt/cursor/artifacts/screenshots/debt-stack-stress/00-dashboard-overview.png`
  <img alt="Dashboard overview" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/00-dashboard-overview.png" />
- `01-senior-fore-named-junior-full`: 01-senior-fore-named-junior full call+deal → `/opt/cursor/artifacts/screenshots/debt-stack-stress/01-senior-fore-named-junior-full.png`
- `01-senior-fore-named-junior-board-1`: 01-senior-fore-named-junior chainboard #1 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/01-senior-fore-named-junior-board-1.png`
  <img alt="01-senior-fore-named-junior chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/01-senior-fore-named-junior-board-1.png" />
- `01-senior-fore-named-junior-board-2`: 01-senior-fore-named-junior chainboard #2 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/01-senior-fore-named-junior-board-2.png`
- `02-hoa-all-senior-full`: 02-hoa-all-senior full call+deal → `/opt/cursor/artifacts/screenshots/debt-stack-stress/02-hoa-all-senior-full.png`
- `02-hoa-all-senior-board-1`: 02-hoa-all-senior chainboard #1 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/02-hoa-all-senior-board-1.png`
  <img alt="02-hoa-all-senior chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/02-hoa-all-senior-board-1.png" />
- `02-hoa-all-senior-board-2`: 02-hoa-all-senior chainboard #2 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/02-hoa-all-senior-board-2.png`
- `03-empty-chain-full`: 03-empty-chain full call+deal → `/opt/cursor/artifacts/screenshots/debt-stack-stress/03-empty-chain-full.png`
- `03-empty-chain-board-1`: 03-empty-chain chainboard #1 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/03-empty-chain-board-1.png`
  <img alt="03-empty-chain chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/03-empty-chain-board-1.png" />
- `03-empty-chain-board-2`: 03-empty-chain chainboard #2 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/03-empty-chain-board-2.png`
- `04-fore-only-zero-senior-full`: 04-fore-only-zero-senior full call+deal → `/opt/cursor/artifacts/screenshots/debt-stack-stress/04-fore-only-zero-senior-full.png`
- `04-fore-only-zero-senior-board-1`: 04-fore-only-zero-senior chainboard #1 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/04-fore-only-zero-senior-board-1.png`
  <img alt="04-fore-only-zero-senior chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/04-fore-only-zero-senior-board-1.png" />
- `04-fore-only-zero-senior-board-2`: 04-fore-only-zero-senior chainboard #2 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/04-fore-only-zero-senior-board-2.png`
- `05-junior-not-named-title-risk-full`: 05-junior-not-named-title-risk full call+deal → `/opt/cursor/artifacts/screenshots/debt-stack-stress/05-junior-not-named-title-risk-full.png`
- `05-junior-not-named-title-risk-board-1`: 05-junior-not-named-title-risk chainboard #1 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/05-junior-not-named-title-risk-board-1.png`
  <img alt="05-junior-not-named-title-risk chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/05-junior-not-named-title-risk-board-1.png" />
- `05-junior-not-named-title-risk-board-2`: 05-junior-not-named-title-risk chainboard #2 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/05-junior-not-named-title-risk-board-2.png`
- `06-multi-senior-mixed-juniors-full`: 06-multi-senior-mixed-juniors full call+deal → `/opt/cursor/artifacts/screenshots/debt-stack-stress/06-multi-senior-mixed-juniors-full.png`
- `06-multi-senior-mixed-juniors-board-1`: 06-multi-senior-mixed-juniors chainboard #1 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/06-multi-senior-mixed-juniors-board-1.png`
  <img alt="06-multi-senior-mixed-juniors chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/06-multi-senior-mixed-juniors-board-1.png" />
- `06-multi-senior-mixed-juniors-board-2`: 06-multi-senior-mixed-juniors chainboard #2 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/06-multi-senior-mixed-juniors-board-2.png`
- `07-unstamped-infer-roles-full`: 07-unstamped-infer-roles full call+deal → `/opt/cursor/artifacts/screenshots/debt-stack-stress/07-unstamped-infer-roles-full.png`
- `07-unstamped-infer-roles-board-1`: 07-unstamped-infer-roles chainboard #1 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/07-unstamped-infer-roles-board-1.png`
  <img alt="07-unstamped-infer-roles chainboard #1" src="/opt/cursor/artifacts/screenshots/debt-stack-stress/07-unstamped-infer-roles-board-1.png" />
- `07-unstamped-infer-roles-board-2`: 07-unstamped-infer-roles chainboard #2 → `/opt/cursor/artifacts/screenshots/debt-stack-stress/07-unstamped-infer-roles-board-2.png`
