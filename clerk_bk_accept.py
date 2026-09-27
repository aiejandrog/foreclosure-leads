#!/usr/bin/env python
"""Acceptance check for the Broward / Palm Beach clerk-docket bankruptcy read.

Run this on the laptop, against real case numbers. It prints one line per case:
county, verdict, event count, and a short reason. It does not print party names,
docket text, or the API key.

    python clerk_bk_accept.py --case CACE-24-000001
    python clerk_bk_accept.py --case 502024CA000001XXXAMB
    python clerk_bk_accept.py --entries docket.json --case CACE-24-000001

Broward is a live read only when BROWARD_CLERK_API_KEY is set (Commercial Data API).
That read is stored in the clerk cache. It does not by itself make a lead contactable.
DEALFLOW_CLERK_BK stays off until you decide the lines match the cases you already know.

Palm Beach is reported unread. eCaseView is not called.

--entries classifies a local JSON file and does not call the clerk and does not clear
a lead. Shape: {"status": "Pending", "entries": [{"date": "01/02/2026", "text": "..."}]}
"""
import sys

import clerk_bk


if __name__ == '__main__':
    sys.exit(clerk_bk.accept_cli(sys.argv[1:]))
