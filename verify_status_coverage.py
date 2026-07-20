"""STRESS TEST: assert every lead in every county file has a listing status.

Exit 0  = 100% coverage — every lead carries zstatus (LISTED/PENDING/SOLD/RENTAL/OFF-MARKET/
          NO-ADDR). Nothing renders as a hole in the tracker.
Exit 1  = holes exist; prints every offender with folio, case, address, and the reason bucket.

Also cross-checks the RENDERED tracker build: counts zstatus keys embedded in the Desktop
plaintext HTML and compares against the lead-file totals, so a stale build can't pass.

Run:  python verify_status_coverage.py
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
TRACKER = r'C:\Users\olqbb\OneDrive\Desktop\DEALFLOW\Foreclosure Lead Tracker.html'
VALID = {'LISTED', 'PENDING', 'SOLD', 'RENTAL', 'OFF-MARKET', 'NO-ADDR'}


def _addr_of(r):
    return (r.get('Address') or r.get('addr') or '').strip()


def main():
    holes = []
    totals = {}
    for fn in ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json'):
        p = os.path.join(BASE, fn)
        if not os.path.exists(p):
            print(f'!! {fn} MISSING from disk')
            holes.append((fn, '-', '-', 'file missing'))
            continue
        leads = json.load(open(p, encoding='utf-8'))
        n_ok = 0
        for r in leads:
            s = r.get('zstatus', '')
            if s in VALID:
                n_ok += 1
                continue
            reason = ('INVALID value %r' % s) if s else (
                'no addr, no folio' if not _addr_of(r) and not re.sub(r'\D', '', str(r.get('Folio') or r.get('folio') or ''))
                else 'has addr/folio but never classified')
            holes.append((fn, str(r.get('Folio') or r.get('folio') or '?'),
                          (_addr_of(r) or str(r.get('Case #') or r.get('case') or '?'))[:45], reason))
        totals[fn] = (n_ok, len(leads))
        print(f'{fn}: {n_ok}/{len(leads)} covered')

    # Rendered-build cross-check: the tracker must embed at least as many zstatus values as the
    # per-county files (MD rows embed from leads_final; BW/PB ride in verbatim).
    if os.path.exists(TRACKER):
        html = open(TRACKER, encoding='utf-8').read()
        n_html = len(re.findall(r'"zstatus":\s*"[A-Z-]+"', html))
        n_files = sum(v[0] for v in totals.values())
        print(f'rendered build: {n_html} zstatus keys embedded (files hold {n_files})')
        if n_html < n_files:
            print('!! RENDERED BUILD IS STALE — rebuild with make_tracker before publishing')
            holes.append(('docs build', '-', '-', f'stale: {n_html} embedded < {n_files} in files'))
    else:
        print('!! Desktop tracker build not found — cannot cross-check rendered output')

    if holes:
        print(f'\nFAIL — {len(holes)} hole(s):')
        for fn, folio, ident, reason in holes[:40]:
            print(f'  [{fn}] folio={folio} | {ident} | {reason}')
        if len(holes) > 40:
            print(f'  ... and {len(holes) - 40} more')
        sys.exit(1)
    print('\nPASS — every lead in every file carries a valid listing status.')
    sys.exit(0)


if __name__ == '__main__':
    main()
