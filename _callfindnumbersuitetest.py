"""Deterministic source regressions for the bounded Find Numbers view."""
from pathlib import Path

source = (Path(__file__).parent / "call_mode.py").read_text()

checks = {
    "bounded initial DOM": "var LKDONE = {}, LKSHOW = 200" in source and "var CAP = LKSHOW, shown = rows.slice(0, CAP);" in source,
    "incremental show-more": "LKSHOW += 200" in source and "id=\"lkmore\"" in source,
    "filter preserves scroll": "LKSCROLL = window.scrollY || 0" in source and "window.scrollTo(0, LKSCROLL)" in source,
    "number input is accessible": 'aria-label=\"Phone number or email for ' in source,
    "enter submits selected row": "if(ev.key === 'Enter')" in source and "inp.parentNode && inp.parentNode.querySelector('button.lkadd')" in source,
    "input cannot trigger arbitrary navigation": "ev.preventDefault(); var b=inp.parentNode" in source,
    "DNC gate remains on lookup rows": "return !hardSuppressed(lkAsDial(r, ''));" in source,
    "typed number remains normalized": "var num = _lkDigits(raw);" in source,
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("ok   " if ok else "FAIL ") + name)
if failed:
    raise SystemExit("%d failed" % len(failed))
print("ALL PASS (%d checks)" % len(checks))
