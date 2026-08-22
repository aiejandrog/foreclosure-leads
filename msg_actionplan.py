#!/usr/bin/env python
"""msg_actionplan — the whole board, solved or planned, in one printable document.

WHY THIS EXISTS
The Notion board is 10 goals and 25 priorities. Read as a list it looks like 35 things. It is not — it
is a critical path with one bottleneck and a hard date. This document says which is which, so a week of
work goes into the item that unblocks twenty others instead of the item that feels productive.

Written to be read on paper by someone with a CDL job and evenings free. No jargon, no filler, dates
and owners on everything.

Run:  python msg_actionplan.py
"""
import datetime
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date(2026, 8, 14)
GATE = datetime.date(2026, 9, 1)

CSS = """
@page{size:Letter;margin:12mm 13mm}
*{box-sizing:border-box;margin:0;padding:0}
body{font:10pt/1.42 "Segoe UI",Arial,sans-serif;color:#111827}
h1{font-size:16pt;color:#0B1730}
.sub{font-size:9pt;color:#5a6577;margin:2pt 0 9pt;border-bottom:2.5pt solid #0B1730;padding-bottom:6pt}
.alarm{background:#7f1d1d;color:#fff;border-radius:7pt;padding:10pt 13pt;margin:9pt 0}
.alarm b{color:#fde68a}
.alarm .big{font-size:13pt;font-weight:700;color:#fde68a}
h2{font-size:12pt;color:#0B1730;margin:15pt 0 5pt;border-bottom:1.5pt solid #0B1730;padding-bottom:3pt}
h3{font-size:10.5pt;color:#0B1730;margin:10pt 0 4pt}
table{width:100%;border-collapse:collapse;margin:5pt 0;font-size:9.2pt}
td,th{padding:4pt 6pt;border-bottom:1pt solid #eef0f5;text-align:left;vertical-align:top}
th{background:#0B1730;color:#fff;font-size:8pt;text-transform:uppercase;letter-spacing:.04em}
.done{color:#065f46;font-weight:700}
.block{color:#92400e;font-weight:700}
.now{color:#7f1d1d;font-weight:700}
.step{border-left:3pt solid #0B1730;background:#f8fafc;padding:7pt 10pt;margin:6pt 0}
.step b{color:#0B1730}
.step .when{font-size:8.5pt;color:#6b7280;text-transform:uppercase;letter-spacing:.05em}
.note{font-size:8.8pt;color:#4b5563;line-height:1.4;margin-top:3pt}
.pg{page-break-before:always}
ul{margin-left:14pt}li{margin:3pt 0;font-size:9.3pt}
"""

DOC = """<!doctype html><html><head><meta charset="utf-8"><title>Action Plan</title>
<style>@@CSS@@</style></head><body>

<h1>The Whole Board &mdash; What Is Done, What Is Blocked, What To Do</h1>
<div class="sub">Prepared @@TODAY@@ &middot; 10 goals and 25 priorities, sorted into a critical path</div>

<div class="alarm">
  <div class="big">The Sep 1 compliance gate is @@GATEDAYS@@ days away and 0 of its 5 items are done.</div>
  Every one of those five traces back to a single unfiled name. <b>Filing the LLC is not one task among
  twenty-five &mdash; it is the task that makes twenty of the others possible.</b> Miss the gate and
  outreach stops, because the alternative is doing regulated work, uninsured, under a name owned by
  another company.
</div>

<h2>1. The one bottleneck</h2>
<p style="font-size:9.5pt">Six separate priorities are waiting on the same thing. None of them can start
until the entity exists:</p>
<table>
  <tr><th>Waiting task</th><th>What it actually needs</th></tr>
  <tr><td>Postcards + print run (1,000 bilingual)</td><td>A name we own to print</td></tr>
  <tr><td>Buy the domain and deploy the site</td><td>A name we own to publish</td></tr>
  <tr><td>Business phone line</td><td>EIN, for A2P texting registration</td></tr>
  <tr><td>FTC Do Not Call registration</td><td>EIN</td></tr>
  <tr><td>Attorney cures the Acosta retainer</td><td>The entity document number</td></tr>
  <tr><td>E&amp;O / liability insurance</td><td>A formed entity to insure</td></tr>
</table>
<div class="note"><b>Why the current name cannot be used:</b> "Miami Solutions Group LLC" is registered to
another Florida company (L22000200556, active since 2022). Every generator now strips the "LLC" so we
never claim an entity we do not own &mdash; but that is a guardrail, not a fix. File a NEW LLC, not a
DBA under Tradervert: a DBA gives zero liability separation, so a complaint would reach the entity
holding the CDL income.</div>

<h2>2. The critical path, in order, with real turnaround times</h2>

<div class="step"><div class="when">Step 1 &middot; this week &middot; $125 &middot; 1 hour</div>
  <b>Pick a name and file the LLC on Sunbiz.</b> Check availability first &mdash; 14 candidates were
  already checked and every real word is crowded, so a coined name clears fastest (that is exactly why
  "Tradervert" was available).
  <div class="note">Sunbiz turnaround: 2&ndash;5 business days. Nothing else on this page starts until
  this is filed, so the cost of waiting a week is a week added to everything below.</div></div>

<div class="step"><div class="when">Step 2 &middot; same day the LLC approves &middot; free &middot; 15 min</div>
  <b>Get the EIN online at irs.gov.</b> Issued immediately once you have the document number.
  <div class="note">This unlocks the bank account, the DNC registration and the phone line at once.</div></div>

<div class="step"><div class="when">Step 3 &middot; next day &middot; free</div>
  <b>Open the business bank account.</b> Keeps company money separate from personal, which is the whole
  point of the liability firewall you just paid for.</div>

<div class="step"><div class="when">Step 4 &middot; same week &middot; free &middot; 45 min</div>
  <b>Register with the FTC Do Not Call Registry</b> (telemarketing.donotcall.gov). Pull area codes 305,
  786, 954, 754, 561 &mdash; free in FY2026 and they cover all three counties.
  <div class="note">Scrubbing before dialling is the FTSA/TCPA defence. Dialling an unscrubbed list is
  per-call statutory damages.</div></div>

<div class="step"><div class="when">Step 5 &middot; START IMMEDIATELY AFTER THE EIN &middot; ~$46/mo</div>
  <b>Business phone line with call recording disabled.</b>
  <div class="note"><b>This is the schedule risk.</b> A2P 10DLC texting registration commonly takes
  2&ndash;3 weeks to approve. Starting it the day the EIN lands is what keeps Sep 1 reachable; starting
  it a week later probably does not. Calls work immediately either way &mdash; it is texting that waits.
  Disabling recording is step one of setup and gets confirmed in writing (the signed rule is already
  written).</div></div>

<div class="step"><div class="when">Step 6 &middot; once the name exists &middot; $15 &middot; 1 hour</div>
  <b>Buy the domain and deploy the site.</b> The site is already built &mdash; stat wall, bilingual
  payoff calculator, MARS disclosure. It is static and free to host. Only the name is missing.
  <div class="note">Right now every door knock ends with a distressed person googling us and finding
  nothing, at the exact moment they are deciding whether a stranger at their door is a scam.</div></div>

<div class="step"><div class="when">Step 7 &middot; once the entity number exists &middot; ~$1,200</div>
  <b>Attorney reviews and cures the Acosta retainer + third-party authorization.</b> The signed paper
  names an entity that is not ours. Ask for a flat fee.</div>

<div class="pg"></div>
<h2>3. What is already done &mdash; do not re-do these</h2>
<table>
  <tr><th>Item</th><th>State</th></tr>
  <tr><td>Ownership-flip gate (stop routing anyone to a sold property)</td><td class="done">LIVE &mdash; 8 dead leads killed, 3 counties</td></tr>
  <tr><td>Jesse's HOA-co-defendant tell</td><td class="done">BUILT &mdash; <code>diligence_list.py</code>, 12 leads flagged</td></tr>
  <tr><td>Carlos: door book, fill-in letters, door log, field kit, no-solicit card</td><td class="done">PRINTED &amp; DELIVERED</td></tr>
  <tr><td>Texting rule + no-recording rule</td><td class="done">WRITTEN &mdash; needs signatures only</td></tr>
  <tr><td>Public PII exposure on the repo</td><td class="done">CLOSED on HEAD (history purge still pending)</td></tr>
  <tr><td>LLC claims stripped from every asset</td><td class="done">DONE &mdash; auto-restores when the real name is set</td></tr>
  <tr><td>Acosta position &amp; options report</td><td class="done">DRAFTED &mdash; needs scope confirm + estoppel</td></tr>
  <tr><td>Milouse lead + her back-taxes task</td><td class="done">CLOSED &mdash; she does not own the property</td></tr>
  <tr><td>Email bounce rate</td><td class="done">DIAGNOSED &mdash; it is a data ceiling, not a bug</td></tr>
</table>

<h2>4. Time-critical items that will not wait a week</h2>
<table>
  <tr><th>What</th><th>Deadline</th><th>Why it cannot slip</th></tr>
  <tr><td class="now">Acosta &mdash; get the Commodore Plaza estoppel</td><td>Sale 08/31 (@@ACOSTADAYS@@ days)</td>
      <td>It is the number that decides whether his deal has any equity. Every option he has needs it first.</td></tr>
  <tr><td class="now">The 12 flagged deep-dive leads</td><td>Soonest auction: 3 days</td>
      <td>Each has an association as co-defendant and real equity. Jesse's 3&ndash;5 minute check, before anyone drives.</td></tr>
  <tr><td class="now">File the LLC</td><td>Sunbiz takes 2&ndash;5 days; A2P takes 2&ndash;3 weeks</td>
      <td>A week of delay probably pushes texting past Sep 1 even if everything else goes right.</td></tr>
</table>

<h2>5. The goals, honestly scored</h2>
<table>
  <tr><th>Goal</th><th>Where it really stands</th><th>Verdict</th></tr>
  <tr><td>Pass the Sep 1 compliance gate</td><td>0 of 5 &mdash; all blocked on the entity</td><td class="now">AT RISK</td></tr>
  <tr><td>Hold 10 consults by Sep 30</td><td>~8 lifetime dials; Milouse turned out to be a non-owner</td><td class="now">BEHIND</td></tr>
  <tr><td>1,500 logged contact attempts by Oct 31</td><td>~8 before this week</td><td class="block">NOT STARTED</td></tr>
  <tr><td>First non-homeowner dollar ($1,000)</td><td>$0 &mdash; but needs no licence from anyone</td><td class="block">BEST NEAR-TERM LANE</td></tr>
  <tr><td>Know the real conversion rate</td><td>No data; hypothesis untested 1,392 times</td><td class="block">BLOCKED ON VOLUME</td></tr>
  <tr><td>Help people actually out of options</td><td>1 signed (an investor). 0 homeowners helped yet.</td><td class="now">THE POINT &mdash; still 0</td></tr>
  <tr><td>A licence that unlocks paid work</td><td>Nobody holds one</td><td class="block">1 YEAR</td></tr>
  <tr><td>Recurring data revenue ($3k/mo)</td><td>Product built, not deployed</td><td class="block">1 YEAR</td></tr>
  <tr><td>Business runs without you</td><td>Playbook + rails now exist in writing</td><td class="block">3 YEARS</td></tr>
  <tr><td>Real estate funded by operating cash</td><td>Pre-revenue</td><td class="block">3 YEARS</td></tr>
</table>
<div class="note"><b>The uncomfortable read:</b> nine of ten goals depend on contact volume, and contact
volume is blocked by the compliance gate, which is blocked by the entity. There is one exception &mdash;
the <b>data/brief lane needs no licence and no homeowner contact at all.</b> 688 briefable sales and
2,814 investor notes are already identified. If the entity slips, that is the only lane that can still
earn.</div>

<h2>6. If you only do four things</h2>
<table>
  <tr><th>#</th><th>Action</th><th>Cost</th><th>Unblocks</th></tr>
  <tr><td>1</td><td><b>Pick a name and file the LLC</b></td><td>$125</td><td>20 of 25 priorities</td></tr>
  <tr><td>2</td><td>EIN the day it approves, then start A2P immediately</td><td>free</td><td>Phone, DNC, bank</td></tr>
  <tr><td>3</td><td>Call Commodore Plaza for the Acosta estoppel</td><td>free</td><td>The only paying client</td></tr>
  <tr><td>4</td><td>Sign both operating rules; have Carlos sign too</td><td>free</td><td>Legal cover for every contact</td></tr>
</table>

<div class="note" style="margin-top:12pt"><b>Nothing in this document has been sent to anyone.</b> No
emails, no texts, no client deliveries. Every item above is internal and waits on your say-so.</div>
</body></html>"""


def main():
    days_gate = (GATE - TODAY).days
    doc = (DOC.replace('@@CSS@@', CSS)
              .replace('@@TODAY@@', TODAY.strftime('%B %d, %Y').replace(' 0', ' '))
              .replace('@@GATEDAYS@@', str(days_gate))
              .replace('@@ACOSTADAYS@@', str((datetime.date(2026, 8, 31) - TODAY).days)))
    assert '@@' not in doc, 'unreplaced token'
    hp = os.path.join(HERE, 'MSG_Action_Plan_%s.html' % TODAY.isoformat())
    open(hp, 'w', encoding='utf-8').write(doc)
    from playwright.sync_api import sync_playwright
    outs = [os.path.join(HERE, 'MSG_Action_Plan_%s.pdf' % TODAY.isoformat())]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        outs.append(os.path.expanduser(os.path.join(
            '~', 'OneDrive', 'Desktop', 'DEALFLOW', 'MSG_Action_Plan_%s.pdf' % TODAY.isoformat())))
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page()
        pg.goto('file:///' + hp.replace(os.sep, '/')); pg.wait_for_timeout(350)
        pdf = pg.pdf(format='Letter', print_background=True, prefer_css_page_size=True,
                     margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    for o in outs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'wb').write(pdf)
        print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))


if __name__ == '__main__':
    main()
