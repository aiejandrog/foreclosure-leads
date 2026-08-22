#!/usr/bin/env python
"""msg_doorlog — the door log sheet Carlos fills in AT THE DOOR. Carlos's task, ⚡ this week.

WHY THIS EXISTS AND WHY IT IS SEPARATE FROM THE DOOR BOOK
The door book (carlos_pre_packet.py) is organised by ROUTE and now carries a per-door tick-strip
for the outcome. This is different: it is the chronological LEGAL RECORD, and it captures the three
fields the book deliberately does not, because they are the ones that win an FTSA/TCPA complaint:

  * the TIME of contact           (proves the 9am-7pm / no-Sunday window was honoured)
  * a NO-SOLICITING sign, yes/no  (FS 501.062, eff. July 1 2026 — knocking a posted door is the
                                   violation; the log is the proof Carlos skipped it)
  * the DNC scrub date            (a dialled number must have been scrubbed first; the date is the
                                   defence, and it is worthless if written from memory that night)

THE ONE RULE PRINTED ON EVERY PAGE: fill it in at the door, in the moment. A log reconstructed at
the kitchen table that night is not evidence — a contemporaneous one is. That single discipline is
the whole reason this sheet exists on paper instead of an app he'd fill in later.

It is blank on purpose. It logs EVERY door — including postcard-only drops and doors not in the
book — so it is the complete record, not a subset of the route.

Landscape letter, ~18 doors a page, 3 pages. English with small Spanish subtitles (Carlos works
both). No PII, no data source — it always builds.

Run:  python msg_doorlog.py
"""
import argparse
import datetime
import os
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS_PER_PAGE = 18
PAGES = 3

CSS = """
@page{size:letter landscape;margin:9mm 8mm}
*{box-sizing:border-box;margin:0;padding:0}
body{font:11px/1.3 "Segoe UI",Arial,sans-serif;color:#111827}
.pg{page-break-after:always;height:100%}
.pg:last-child{page-break-after:auto}
.hd{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid #0B1730;padding-bottom:5px;margin-bottom:6px}
h1{font-size:17px;color:#0B1730}
h1 span{font-weight:400;font-size:11px;color:#6b7280}
.who{font:600 10px Arial;color:#374151;text-align:right}
.who .bl{display:inline-block;border-bottom:1px solid #6b7280;min-width:120px;height:12px;margin-left:4px}
.rule{background:#7f1d1d;color:#fff;border-radius:6px;padding:5px 9px;margin-bottom:6px;font-size:10.5px}
.rule b{color:#fde68a}
table{width:100%;border-collapse:collapse}
th{background:#0B1730;color:#fff;font:700 8.5px Arial;text-transform:uppercase;letter-spacing:.03em;padding:5px 4px;border:1px solid #0B1730;text-align:left;vertical-align:bottom}
th small{display:block;font-weight:400;color:#c7cfdb;letter-spacing:0;text-transform:none;font-size:7.5px}
td{border:1px solid #cbd5e1;height:30px;padding:2px 4px;vertical-align:top}
tr:nth-child(even) td{background:#f8fafc}
.c{text-align:center;color:#9ca3af;font-size:8px}
.foot{margin-top:6px;font-size:8.5px;color:#4b5563;line-height:1.35}
.foot b{color:#7f1d1d}
"""

HEAD_ROW = (
    '<tr>'
    '<th style="width:3%">#</th>'
    '<th style="width:7%">Time<small>hora</small></th>'
    '<th style="width:24%">Address<small>direcci&oacute;n</small></th>'
    '<th style="width:7%">No-solicit sign?<small>&iquest;letrero?</small></th>'
    '<th style="width:13%">Who answered<small>qui&eacute;n abri&oacute;</small></th>'
    '<th style="width:16%">Outcome<small>resultado</small></th>'
    '<th style="width:7%">Letter left?<small>&iquest;carta?</small></th>'
    '<th style="width:16%">Best # + when to call / notes<small>mejor n&uacute;mero</small></th>'
    '</tr>')


def page(n):
    rows = []
    for i in range(1, ROWS_PER_PAGE + 1):
        idx = (n - 1) * ROWS_PER_PAGE + i
        rows.append(
            '<tr><td class="c">%d</td><td></td><td></td>'
            '<td class="c">Y / N</td><td></td><td></td><td class="c">Y / N</td><td></td></tr>' % idx)
    return (
        '<div class="pg">'
        '<div class="hd"><h1>Door Log <span>&mdash; fill in AT THE DOOR, not from memory later</span></h1>'
        '<div class="who">DATE <span class="bl"></span> &nbsp; AREA <span class="bl"></span> &nbsp; '
        'NAME <span class="bl"></span></div></div>'
        '<div class="rule"><b>9:00 AM&ndash;7:00 PM only. No Sundays.</b> A posted NO-SOLICITING sign = '
        'skip the door, mark it, keep moving (FS 501.062). Anyone dialled must be DNC-scrubbed first &mdash; '
        'write the scrub date. <b>Stop / not interested / don&rsquo;t come back = done permanently, text Alex.</b></div>'
        '<table>%s%s</table>'
        '<div class="foot">This sheet is the record that a contact was lawful &mdash; the time window, the '
        'sign, the outcome. <b>It only counts if it is written in the moment.</b> Outcome shorthand: '
        'NH nobody home &middot; T talked &middot; LL left letter &middot; # got number &middot; NI not interested &middot; '
        'DNC do-not-contact &middot; NS no-solicit sign (skipped) &middot; GATE gated/couldn&rsquo;t reach. '
        'Hand every page back to Alex &mdash; it goes into the system and it is the FTSA/TCPA defence.</div>'
        '</div>') % (HEAD_ROW, ''.join(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pages', type=int, default=PAGES)
    a = ap.parse_args()
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>Door Log</title>'
           '<style>%s</style></head><body>%s</body></html>'
           % (CSS, ''.join(page(n) for n in range(1, a.pages + 1))))
    today = datetime.date.today().isoformat()
    hp = os.path.join(HERE, 'MSG_Door_Log_%s.html' % today)
    open(hp, 'w', encoding='utf-8').write(doc)

    from playwright.sync_api import sync_playwright
    outs = [os.path.join(HERE, 'MSG_Door_Log_%s.pdf' % today)]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        outs.append(P.out('MSG_Door_Log_%s.pdf' % today))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto('file:///' + hp.replace(os.sep, '/'))
        pg.wait_for_timeout(300)
        pdf = pg.pdf(width='11in', height='8.5in', print_background=True, landscape=True,
                     margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    for o in outs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'wb').write(pdf)
        print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))
    print('%d pages x %d doors = %d door lines. Print a stack; one date per sheet.'
          % (a.pages, ROWS_PER_PAGE, a.pages * ROWS_PER_PAGE))


if __name__ == '__main__':
    main()
