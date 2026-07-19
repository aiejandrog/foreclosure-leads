"""One-time RealForeclose login capture — ALL THREE counties.

Opens a real browser window on each county's RealForeclose login page, one at a time. YOU type
your username and password yourself — nothing here reads, fills, clicks, or stores them; the
browser profile keeps the session cookies so the pipeline can scrape as a logged-in user.

WHY THREE: cookie jars are per-domain — logging into miamidade.realforeclose.com does NOT
authenticate broward.realforeclose.com or palmbeach.realforeclose.com. The auction-results
scraper (AUCTION-RESULTS-SPEC.md) is login-gated in all three, so all three jars must land in
the same browser-profile/. Miss one and that county's results silently skip.

Flow: for each county the browser navigates to that county's login page; you log in there,
confirm your account name shows at the top of the site, then come back HERE and press Enter.
After the third county, close the window (or just press Enter at the last prompt).
"""
import os
from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'browser-profile')

COUNTIES = [
    ("Miami-Dade", "miamidade"),
    ("Broward", "broward"),
    ("Palm Beach", "palmbeach"),
]

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(PROFILE_DIR, headless=False,
        viewport={"width": 1300, "height": 950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for i, (name, sub) in enumerate(COUNTIES, 1):
        page.goto(f"https://{sub}.realforeclose.com/index.cfm?zaction=USER&zmethod=LOGIN")
        try:
            input(f"[{i}/3] Log into {name} in the browser window (account name at top = good), "
                  f"then press Enter HERE -> ")
        except EOFError:
            # no console attached (double-clicked the file): fall back to waiting for window close
            print("No console — close the browser window when all counties are logged in.")
            try:
                page.wait_for_event('close', timeout=0)
            except Exception:
                pass
            break
    ctx.close()
print(f"Session(s) saved to {PROFILE_DIR}. The pipeline will now scrape logged-in on all three counties.")
