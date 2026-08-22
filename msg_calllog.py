#!/usr/bin/env python
"""msg_calllog — the call sheet you fill in WHILE you dial. The phone twin of the door log.

WHY THIS EXISTS RIGHT NOW
Alejandro went from ~8 lifetime dials to 11-15 in a single day. That is the most valuable thing
happening in this business, and it is currently happening with no written record — while 31% of the
phone numbers in the system (1,660 of 5,392) carry a DNC flag.

Two consequences, both fixed by one sheet:
  * LEGAL. A call to a DNC-flagged number is per-call statutory exposure, and the only defence is a
    contemporaneous log showing the number was checked before it was dialled. A log reconstructed that
    evening is not evidence. The DNC column is therefore the FIRST column after the number — you tick
    it before the phone rings, not after.
  * COMMERCIAL. Nobody can improve a call they did not write down. Connects per dial, what the
    objection actually was, whether a consult got booked — those are the numbers that tell you if the
    pitch is working, and right now they exist only as a feeling.

DESIGNED FOR THE ACTUAL MOTION: one row per dial, ticked in under three seconds, with an outcome
shorthand printed at the bottom so nothing has to be spelled out mid-call. The tally box at the end of
each page is what turns a day of calling into a conversion rate.

Landscape letter, 20 dials a page, 3 pages. No PII, no data source — it always builds.

Run:  python msg_calllog.py
"""
import argparse
import datetime
import os
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS = 20
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
.who .bl{display:inline-block;border-bottom:1px solid #6b7280;min-width:110px;height:12px;margin-left:4px}
.rule{background:#7f1d1d;color:#fff;border-radius:6px;padding:5px 9px;margin-bottom:6px;font-size:10.5px}
.rule b{color:#fde68a}
table{width:100%;border-collapse:collapse}
th{background:#0B1730;color:#fff;font:700 8.5px Arial;text-transform:uppercase;letter-spacing:.03em;
   padding:5px 4px;border:1px solid #0B1730;text-align:left;vertical-align:bottom}
th small{display:block;font-weight:400;color:#c7cfdb;letter-spacing:0;text-transform:none;font-size:7.5px}
td{border:1px solid #cbd5e1;height:29px;padding:2px 4px;vertical-align:top}
tr:nth-child(even) td{background:#f8fafc}
.c{text-align:center;color:#9ca3af;font-size:8px}
.dnc{background:#fef2f2 !important;text-align:center;font:700 9px Arial;color:#7f1d1d}
.foot{margin-top:6px;display:flex;gap:14px;align-items:flex-start}
.legend{flex:1;font-size:8.5px;color:#4b5563;line-height:1.35}
.legend b{color:#7f1d1d}
.tally{border:1.5px solid #0B1730;border-radius:6px;padding:6px 9px;min-width:290px}
.tally .t{font:700 9px Arial;text-transform:uppercase;letter-spacing:.06em;color:#0B1730;margin-bottom:3px}
.tally table{width:100%}
.tally td{border:0;height:auto;padding:2px 3px;font-size:9.5px}
.tally .box{border-bottom:1px solid #6b7280;width:38px;display:inline-block;height:12px}
"""

HEAD = ('<tr>'
        '<th style="width:3%">#</th>'
        '<th style="width:7%">Time</th>'
        '<th style="width:13%">Name</th>'
        '<th style="width:11%">Number dialed</th>'
        '<th style="width:7%">DNC checked?<small>tick BEFORE dialing</small></th>'
        '<th style="width:9%">Outcome<small>see codes</small></th>'
        '<th style="width:25%">What they said</th>'
        '<th style="width:12%">Next step + when</th>'
        '<th style="width:7%">Consult booked?</th>'
        '</tr>')


def page(n):
    rows = []
    for i in range(1, ROWS + 1):
        rows.append('<tr><td class="c">%d</td><td></td><td></td><td></td>'
                    '<td class="dnc">CLEAR</td><td></td><td></td><td></td>'
                    '<td class="c">Y / N</td></tr>' % ((n - 1) * ROWS + i))
    return (
        '<div class="pg">'
        '<div class="hd"><h1>Call Log <span>&mdash; fill in AS YOU DIAL, not afterwards</span></h1>'
        '<div class="who">DATE <span class="bl"></span> &nbsp; NAME <span class="bl"></span></div></div>'
        '<div class="rule"><b>Before every dial: look at the number&rsquo;s DNC chip on the board.</b> '
        'If it is flagged, do not call it &mdash; cross the row out and move on. '
        '<b>8AM&ndash;8PM their time. Stop means stop, on every channel, forever.</b></div>'
        '<table>%s%s</table>'
        '<div class="foot">'
        '  <div class="legend"><b>OUTCOME CODES:</b> '
        '  <b>NA</b> no answer &middot; <b>VM</b> left voicemail &middot; <b>T</b> talked &middot; '
        '  <b>BK</b> booked the consult &middot; <b>CB</b> call back (write when) &middot; '
        '  <b>NI</b> not interested &middot; <b>DNC</b> asked to stop &mdash; log it and tell Alex the same day &middot; '
        '  <b>WN</b> wrong number &middot; <b>DIS</b> disconnected<br><br>'
        '  <b>You are selling ONE thing:</b> the free five-minute call with the advisor. Not the deal, '
        '  not the solution. Ask <i>&ldquo;what happened with the house?&rdquo;</i> and then stop talking &mdash; '
        '  their answer is the whole call. If they push for detail: <i>&ldquo;I don&rsquo;t want to guess and '
        '  give you wrong information.&rdquo;</i><br><br>'
        '  This sheet is the record that a call was lawful, and the only way to know whether the pitch '
        '  is improving. <b>It only counts if it is written in the moment.</b></div>'
        '  <div class="tally"><div class="t">End of page &mdash; total it up</div><table>'
        '  <tr><td>Dials</td><td><span class="box"></span></td><td>Talked (T)</td><td><span class="box"></span></td></tr>'
        '  <tr><td>Voicemails</td><td><span class="box"></span></td><td>Consults booked</td><td><span class="box"></span></td></tr>'
        '  <tr><td>No answer</td><td><span class="box"></span></td><td>Not interested</td><td><span class="box"></span></td></tr>'
        '  <tr><td>Wrong / dead</td><td><span class="box"></span></td><td>DNC requests</td><td><span class="box"></span></td></tr>'
        '  </table><div style="font-size:8px;color:#6b7280;margin-top:3px">'
        '  Talked &divide; Dials = contact rate &middot; Booked &divide; Talked = the number that actually matters</div>'
        '  </div>'
        '</div></div>') % (HEAD, ''.join(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pages', type=int, default=PAGES)
    a = ap.parse_args()
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>Call Log</title>'
           '<style>%s</style></head><body>%s</body></html>'
           % (CSS, ''.join(page(n) for n in range(1, a.pages + 1))))
    today = datetime.date.today().isoformat()
    hp = os.path.join(HERE, 'MSG_Call_Log_%s.html' % today)
    open(hp, 'w', encoding='utf-8').write(doc)

    from playwright.sync_api import sync_playwright
    outs = [os.path.join(HERE, 'MSG_Call_Log_%s.pdf' % today)]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        outs.append(P.out('MSG_Call_Log_%s.pdf' % today))
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page()
        pg.goto('file:///' + hp.replace(os.sep, '/')); pg.wait_for_timeout(300)
        pdf = pg.pdf(width='11in', height='8.5in', print_background=True, landscape=True,
                     margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    for o in outs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'wb').write(pdf)
        print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))
    print('\n%d pages x %d dials = %d call lines.' % (a.pages, ROWS, a.pages * ROWS))
    print('Tick the DNC box BEFORE the phone rings. Total each page — that is your conversion rate.')


if __name__ == '__main__':
    main()
