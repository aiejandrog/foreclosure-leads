"""One-time RealForeclose login capture.

Opens a real browser window on the RealForeclose login page. YOU type your username and
password yourself — nothing here reads or stores them; the browser profile keeps the session
cookie so the weekly pipeline can scrape as a logged-in user. Close the window when you see
your account name at the top of the site.
"""
import os
from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'browser-profile')

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(PROFILE_DIR, headless=False,
        viewport={"width": 1300, "height": 950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://miamidade.realforeclose.com/index.cfm?zaction=USER&zmethod=LOGIN")
    print("Browser open. Log in with YOUR credentials, then close the window.")
    try:
        page.wait_for_event('close', timeout=0)
    except Exception:
        pass
    ctx.close()
print("Session saved to browser-profile/. The pipeline will now scrape logged-in.")
