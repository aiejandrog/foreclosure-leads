#!/usr/bin/env python
"""call_sheet — today's dial order, written to the Desktop every morning. No browser, no code.

WHY THIS EXISTS (2026-08-27)
The board and Call Mode are both good and both require a decision to open. Measured that day:
133 leads had verified equity, a phone, and passed every gate — and ZERO had been touched; the
send log held exactly one text in nine days. The data was never the bottleneck. So this writes
the dial order to a file that is simply THERE at 7 AM, ordered so the top of the page is the
best call of the day.

ORDER = the same one Call Mode builds (call_mode.call_rows), so the sheet and the phone can
never disagree: band by runway (7-45d first — a real clock AND time to act), then VERIFIED
equity ahead of guessed, then equity high-to-low. Anything the diligence/ownership/opt-out
gates hold never reaches the page at all.

A '*' means the equity was proven against the traced recorded chain (equity_state clear/priced).
No star = the number is a working hypothesis: ask, do not assert. That distinction is the whole
point — quoting an unverified number is how Acosta happened.

Run:  python call_sheet.py            # -> Desktop\DEALFLOW\Call-Sheet-YYYY-MM-DD.txt
      python call_sheet.py --top 60   # longer sheet
      python call_sheet.py --print    # stdout as well
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 2026-09-06: this module predated the OneDrive evacuation and kept writing call sheets (owner
# names + phone numbers) into the SYNCED Desktop\DEALFLOW — 326 PII files re-accumulated in the
# folder paths.py had already retired, and the board twin it reads came from the same stale spot.
# One source of truth for the output root: paths.DEALFLOW_DIR (~/DEALFLOW, outside every sync root).
import paths as _P
OUTDIR = _P.DEALFLOW_DIR


def _fmt_phone(p):
    d = re.sub(r'\D', '', str(p or ''))
    return '(%s) %s-%s' % (d[:3], d[3:6], d[6:10]) if len(d) == 10 else (str(p or '') or '—')


def build(top=40):
    """-> (text, stats). Reads the BUILT board so the sheet reflects exactly what shipped."""
    import foreclosure_leads as F
    import call_mode as CM
    slim = F.build_slim() if hasattr(F, 'build_slim') else None
    if slim is None:
        # The board's own merged rows are what Call Mode consumes; rebuild them the same way
        # make_tracker does rather than re-deriving a second, drifting definition here.
        raise SystemExit('call_sheet: no slim builder available — run from make_tracker instead')
    rows, total = CM.call_rows(slim)
    return _render(rows, total, top)


def _render(rows, total, top):
    # The sheet is the HOMEOWNER dial order (Alejandro, evenings). Balloon-refi rows are LLC
    # investors worked by the advisor from Call Mode's Balloon lane with a different script; on
    # this page they would read as a distressed owner with "n/k" equity. Same rows, different desk.
    rows = [r for r in rows if r.get('st') != 'BAL']
    today = datetime.date.today()
    ver = sum(1 for r in rows if r.get('eqv'))
    L = []
    L.append('DEALFLOW CALL SHEET  ·  %s' % today.strftime('%A %B %d, %Y'))
    L.append('=' * 78)
    L.append('%d dialable  ·  %d qualifying  ·  %d with VERIFIED equity (*)' % (len(rows), total, ver))
    L.append('Ordered best-first. Start at #1 and work down — do not cherry-pick.')
    L.append("* = equity proven against the traced recorded chain. No star = ASK, don't assert.")
    L.append('')
    L.append('%-3s %-22s %-26s %-15s %4s %-7s %s' % ('#', 'WHO', 'PROPERTY', 'PHONE', 'DAYS', 'EQUITY', 'CASE'))
    L.append('-' * 78)
    for i, r in enumerate(rows[:top], 1):
        eq = ('%d%%' % round(r['e'])) if r.get('e') is not None else 'n/k'
        if r.get('eqv'):
            eq += '*'
        L.append('%-3d %-22s %-26s %-15s %4s %-7s %s' % (
            i, (r.get('on') or r.get('o') or '')[:22], (r.get('a') or '(no address)')[:26],
            _fmt_phone((r.get('p') or [''])[0]), r.get('d', ''), eq, r.get('c', '')))
    L.append('')
    L.append('Full interactive list (tap to dial, log the call): docs/call/ on the site.')
    L.append('Notes/calls sync between devices once BOTH have the same team key (board -> Sync).')
    return '\n'.join(L), {'dialable': len(rows), 'qualifying': total, 'verified': ver}


def write(rows, total, top=40):
    """Called by make_tracker right after Call Mode builds — same rows, zero drift."""
    text, stats = _render(rows, total, top)
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        path = os.path.join(OUTDIR, 'Call-Sheet-%s.txt' % datetime.date.today().isoformat())
        open(path, 'w', encoding='utf-8').write(text)
        # stable filename too, so a shortcut/AutoHotkey always opens today's
        open(os.path.join(OUTDIR, 'Call-Sheet-TODAY.txt'), 'w', encoding='utf-8').write(text)
        print('call sheet: %d dialable (%d verified) -> %s'
              % (stats['dialable'], stats['verified'], os.path.basename(path)))
        return path
    except Exception as e:
        print('call sheet: could not write (%s) — not fatal' % str(e)[:70])
        return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--print', dest='show', action='store_true')
    a = ap.parse_args()
    # standalone: read the built board's DATA via the same path the verifier uses
    import call_mode as CM
    board = os.path.join(OUTDIR, 'Foreclosure Lead Tracker.html')
    if not os.path.exists(board):
        raise SystemExit('call_sheet: no plaintext board at %s — run the nightly first' % board)
    src = open(board, encoding='utf-8', errors='replace').read()
    # 2026-09-08: the board gained an encrypted-payload path, and `DATA` became a derived binding
    # (`let DATA = Array.isArray(RAW) ? RAW : []`) that no longer holds a literal array. The rows
    # live in `RAW`. The old const-DATA regex matched nothing and the standalone sheet silently
    # froze at the last good build (09-05) while the board kept rebuilding. Read RAW, fall back to
    # a literal DATA for older boards. If RAW is an encrypted blob this correctly finds nothing —
    # in that case the sheet has to come from the nightly, which builds rows before they are sealed.
    m = (re.search(r'(?:const|let|var)\s+RAW\s*=\s*(\[.*?\]);\n', src, re.S)
         or re.search(r'(?:const|let|var)\s+DATA\s*=\s*(\[.*?\]);\n', src, re.S))
    if not m:
        raise SystemExit('call_sheet: could not read DATA from the board')
    rows, total = CM.call_rows(json.loads(m.group(1)))
    p = write(rows, total, a.top)
    if a.show:
        print()
        print(_render(rows, total, a.top)[0])


if __name__ == '__main__':
    main()
