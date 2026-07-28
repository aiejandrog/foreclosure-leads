# Diligence stress report

- Compose pass (lead-backed numbers): **50/50**
- Live pass (15 cases, 5/county): **15/15**
- Post-fix 5 random: **5/5**
- Empty-field hits: none

## Root causes
- Call Sheet showed blank Diligence card while waiting on auto live OR (2–5 min / hang) — no provisional numbers
- load_or_chain auto-invoked live on every uncached open, blocking POST /diligence
- Capri forced live on every dig even when seed+lead had numbers
- API 500 on scrape error left UI with toast only and no judgment/sale/money table
- localStorage could persist empty stubs; UI now refuses to overwrite good briefs
- BW/PB lead files have blank plaintiff on nearly all rows (data gap — not dig math)

## Fixes
- diligence.apply_lead_backed_numbers() — always overlay judgment/sale/money_math/net_equity from lead
- load_or_chain: live only when force_live=True (CLI --live / Refresh Diligence)
- API POST accepts live:bool; auto-open uses live:false; Refresh uses live:true
- API exception path returns degraded lead-backed brief instead of blank 500 when possible
- UI: _csLeadDiligenceStub + _csPatchDiligenceFromLead — numbers visible immediately on open
- _fcDiligenceStore refuses provisional/empty stubs overwriting good cache
- Capri no longer force-lives on every open; seed merge when OR thin
- Desktop tracker rebaked from tracker_template.html

## Re-open Dealflow
1. Close any open Foreclosure Lead Tracker.html tab/window
2. Open Desktop DEALFLOW\Foreclosure Lead Tracker.html (rebaked)
3. Hard refresh if browser cached old file (Ctrl+F5)
4. Confirm Diligence API: curl http://127.0.0.1:8765/health — captcha_key true
5. If down: double-click run_diligence_silent.vbs or install_diligence_autostart.bat
6. Open any Call Sheet — Judgment / Sale / Money table populate immediately from lead
7. Tap Refresh Diligence only when you want live Official Records (PB ~1–2 min)
8. Optional: clear stale fcDiligence in DevTools→Application→Local Storage if an old empty stub persists
