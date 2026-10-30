# Email deliverability: documentation, list-quality report, copy proposal

Branch `claude/agents-email-deliverability` (from origin/main b829b54). No production code changed. Nothing sent, no SMTP/IMAP, $0.

## What is in it
- `docs/EMAIL-DELIVERABILITY.md`: how sending, caps, warm-up, the 5% first-touch bounce pause, the 10% follow-up breaker and the placement hold work on main (file:function cited), what PR #290 and #258 change, the SPF/DKIM/DMARC state of bsgflorida.com, biscaynesolutionsgroup.com and bsgfl.com (measured with public DNS, with check commands), the first-touch copy score and a rewrite proposal (doc only), and an evidence-gated ramp with the math.
- `reports/email_quality.py`: read-only, local files, counts only. Not wired into anything.
- `_emailqualitytest.py`: invented `example.test` fixture in a temp dir.

## Findings
- DNS: SPF, DKIM and DMARC already exist on all three domains; DMARC is `p=none` everywhere. Nothing is missing at the registrar.
- `deliverability.py score --template`: 1.5 (ok). Flags: "free" (from the booking URL slug) and "foreclosure".
- The existing pauses trip at 5%/day and 10%/week; they do not hold bounces under 2%. List quality does.
- Real-data run of the report found none of the six input files in `~/DEALFLOW` (they live beside the code in the main checkout); counts are 0 and the report says so.

## Test plan
- `python _emailqualitytest.py` -> PASS
- `python deliverability.py score --template` -> exit 0
- `python reports/email_quality.py` -> prints counts and a missing-files warning

## Needs Alex
See docs/agent-teams/DECISIONS-NEEDED.md D7 (booking slug), D8 (copy rewrite, attorney), D9 (evidence-gated ramp).

Co-Authored-By: Claude Sonnet <noreply@anthropic.com>
