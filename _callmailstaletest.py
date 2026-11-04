"""Offline contract checks for the rebased email composer boundary."""
from pathlib import Path
src = Path(__file__).with_name("call_mode.py").read_text(encoding="utf-8")
start = src.index("  $('mlgo').onclick")
end = src.index("  $('mlback').onclick", start)
block = src[start:end]
checks = {
    "fresh mail gate before send": "var fresh = mailCheck(r, M.to);" in block,
    "held/replied/stage refusal is surfaced": "if(fresh.why)" in block,
    "sequence body rebuilt from fresh notes": "M = composeMail(r, fresh.addr, lang());" in block,
    "send remains behind fresh gate": "mailViaBridge(r, M)" in block and block.index("if(fresh.why)") < block.index("mailViaBridge(r, M)"),
    "sending state starts after fresh gate": block.index("sending = true;") > block.index("if(fresh.why)"),
}
for label, ok in checks.items():
    print(("ok   " if ok else "FAIL ") + label)
if not all(checks.values()):
    raise SystemExit(1)
