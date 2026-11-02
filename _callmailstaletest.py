"""Offline contract checks for the email composer re-check boundary."""
from pathlib import Path

src = Path(__file__).with_name('call_mode.py').read_text(encoding='utf-8')
block = src[src.index("$('mlgo').onclick"):src.index("$('mlback').onclick", src.index("$('mlgo').onclick"))]
checks = {
    'fresh mail gate before send': 'var fresh = mailCheck(r, M.to);' in block,
    'held/replied/stage refusal is surfaced': 'if(fresh.why)' in block,
    'sequence body rebuilt from fresh notes': 'M = composeMail(r, fresh.addr, lang());' in block,
    'send remains behind the fresh gate': 'mailViaBridge(r, M)' in block and block.index('if(fresh.why)') < block.index('mailViaBridge(r, M)'),
}
for label, ok in checks.items():
    print(('ok   ' if ok else 'FAIL ') + label)
if not all(checks.values()):
    raise SystemExit(1)
