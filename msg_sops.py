#!/usr/bin/env python
"""msg_sops — the two signable operating rules that keep outreach legal.

WHY THESE TWO, AND WHY ON PAPER
Both are Notion ⚡-this-week Compliance items, and both are the kind of rule that is worth nothing as a
good intention and everything as a signed page with a date on it. If a complaint ever lands, the first
question is "what was your written policy, and who signed it?" A policy written after the complaint is
not evidence. This one is.

  SOP 1 — MANUAL-SEND-ONLY TEXTING (FTSA / TCPA)
    Florida's FTSA gives $500 per text, trebled to $1,500 for a wilful violation, and it is per MESSAGE.
    A single 100-text mistake is $150,000. There is no insurance for it yet and no entity to hide behind.
    The whole defence is: a human pressed send every single time, inside legal hours, against a scrubbed
    list, and stopped the instant someone said stop.

  SOP 2 — NO CALL RECORDING (FS 934.03)
    Florida is an ALL-PARTY consent state. Recording a homeowner without announced consent is a
    third-degree FELONY — not a fine, a felony. The safest posture is a platform that physically cannot
    record, so the rule cannot be broken by a mis-click or a helpful setting someone turns on.

Deliberately blunt, deliberately short. A rule nobody can recite is a rule nobody follows.

Run:  python msg_sops.py
"""
import datetime
import os
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))

CSS = """
@page{size:Letter;margin:14mm 15mm}
*{box-sizing:border-box;margin:0;padding:0}
body{font:11pt/1.45 "Segoe UI",Arial,sans-serif;color:#111827}
.doc{page-break-after:always}
.doc:last-child{page-break-after:auto}
h1{font-size:17pt;color:#0B1730;margin-bottom:2pt}
.sub{font-size:9pt;color:#5a6577;letter-spacing:.06em;text-transform:uppercase;margin-bottom:10pt;
     border-bottom:2pt solid #0B1730;padding-bottom:5pt}
.stake{background:#7f1d1d;color:#fff;border-radius:7pt;padding:9pt 12pt;margin:9pt 0;font-size:10.5pt}
.stake b{color:#fde68a}
h2{font-size:11.5pt;color:#0B1730;margin:12pt 0 5pt;padding-bottom:2pt;border-bottom:1pt solid #e5e7eb}
ol{margin-left:16pt}
li{margin:5pt 0;font-size:10.5pt;line-height:1.4}
li b{color:#7f1d1d}
.never{background:#fef2f2;border-left:3pt solid #7f1d1d;padding:8pt 11pt;margin:9pt 0;font-size:10.5pt}
.never ul{list-style:none;margin:0}
.never li{margin:4pt 0}
.never li:before{content:"\\2715  ";color:#7f1d1d;font-weight:700}
.sig{margin-top:16pt;border-top:1.5pt solid #0B1730;padding-top:10pt}
.sigrow{display:flex;gap:26pt;margin-top:16pt}
.sigbox{flex:1}
.sigline{border-bottom:1pt solid #374151;height:22pt}
.siglbl{font-size:8.5pt;color:#5a6577;text-transform:uppercase;letter-spacing:.07em;margin-top:3pt}
.note{font-size:9pt;color:#4b5563;margin-top:9pt;line-height:1.4}
"""

TEXTING = """
<div class="doc">
  <h1>Texting Policy &mdash; A Human Sends Every Message</h1>
  <div class="sub">Operating rule &middot; effective @@DATE@@ &middot; applies to everyone who contacts an owner</div>

  <div class="stake"><b>WHY THIS IS NOT NEGOTIABLE:</b> Florida law allows <b>$500 for every text</b> that
  breaks these rules, and <b>$1,500 each</b> if it looks deliberate. It is counted per message, not per
  person &mdash; one bad batch of 100 texts is <b>$150,000</b>. We have no insurance for this yet.</div>

  <h2>The five rules</h2>
  <ol>
    <li><b>A person presses send. Every time.</b> No autodialer, no bulk blast, no scheduler, no tool that
    sends while nobody is watching. If software could send a message without a human choosing to, we do
    not use that software.</li>
    <li><b>8:00 AM to 8:00 PM, the owner&rsquo;s local time.</b> Not ours &mdash; theirs. Nothing outside
    that window for any reason, including a same-day auction.</li>
    <li><b>Three commercial texts maximum per 24 hours to the same number</b>, and only if they have not
    replied. Once they reply, it is a conversation and you answer like a person.</li>
    <li><b>The number is scrubbed against the Do Not Call list BEFORE it is used, and the scrub date is
    written down.</b> An unscrubbed number is a violation on the first message.</li>
    <li><b>&ldquo;Stop&rdquo; ends everything, immediately and permanently</b> &mdash; texts, calls, mail,
    doors, every channel. It does not need to say STOP. &ldquo;Leave me alone&rdquo;, &ldquo;not
    interested&rdquo;, &ldquo;don&rsquo;t come back&rdquo;, or a hand waving you off a porch all count.
    Report it the same day so it is suppressed on every device.</li>
  </ol>

  <div class="never">
    <b>Never, under any circumstance:</b>
    <ul>
      <li>Send from a tool that can send by itself</li>
      <li>Text a number that has not been scrubbed</li>
      <li>Text again after someone asks you to stop &mdash; even once, even to apologise</li>
      <li>Text outside 8AM&ndash;8PM to &ldquo;catch&rdquo; a sale date</li>
      <li>Use a number that is not ours to send from</li>
    </ul>
  </div>

  <h2>What you write down, every time</h2>
  <ol>
    <li>Date, time, and the number texted</li>
    <li>The DNC scrub date for that number</li>
    <li>What was sent, and what came back</li>
    <li>Any stop request &mdash; the exact words and the time</li>
  </ol>
  <div class="note">The log is the defence. A log written that night from memory is not one. Write it
  as it happens.</div>

  @@SIG@@
</div>"""

RECORDING = """
<div class="doc">
  <h1>No Call Recording &mdash; Ever</h1>
  <div class="sub">Operating rule &middot; effective @@DATE@@ &middot; applies to everyone on every call</div>

  <div class="stake"><b>WHY THIS IS NOT NEGOTIABLE:</b> Florida requires <b>every person on the call to
  consent</b> before it can be recorded. Recording a homeowner without that is <b>a felony</b> &mdash;
  not a fine, not a warning. A single recorded call could end the business and follow you personally.</div>

  <h2>The rule</h2>
  <ol>
    <li><b>We do not record calls.</b> Not sales calls, not consults, not &ldquo;just for training&rdquo;,
    not a voice memo running in your pocket, not a screen recording that captures audio.</li>
    <li><b>Recording is switched OFF at the phone system level</b>, so it cannot be turned on by accident
    or by a helpful default. Whoever sets up the phone system confirms this in writing before the first
    call goes out.</li>
    <li><b>No personal recording apps on business calls.</b> This includes the built-in call recorder on
    an Android phone and any &ldquo;AI notetaker&rdquo; that joins calls.</li>
    <li><b>Take notes instead.</b> Written notes are what we use, and they are what we can defend.</li>
    <li><b>If the other person says they are recording</b>, that is their choice. Continue normally, be
    exactly as careful as always, and tell Alejandro afterwards.</li>
  </ol>

  <div class="never">
    <b>Never, under any circumstance:</b>
    <ul>
      <li>Record a homeowner &mdash; with or without telling them</li>
      <li>Record &ldquo;for training&rdquo; or &ldquo;so the advisor can hear it&rdquo;</li>
      <li>Put a call on speaker so a third person can listen without saying they are there</li>
      <li>Forward or replay any audio of an owner</li>
    </ul>
  </div>

  <div class="note">If someone genuinely needs to hear a conversation, the answer is to have that person
  on the call and introduce them by name at the start &mdash; never to record it.</div>

  @@SIG@@
</div>"""

SIG = """
<div class="sig">
  <div style="font-size:10.5pt"><b>I have read this rule, I understand it, and I will follow it.</b>
  I understand that breaking it can cost the business money it does not have and can create personal
  legal liability for me.</div>
  <div class="sigrow">
    <div class="sigbox"><div class="sigline"></div><div class="siglbl">Signature</div></div>
    <div class="sigbox"><div class="sigline"></div><div class="siglbl">Printed name</div></div>
    <div class="sigbox" style="flex:.55"><div class="sigline"></div><div class="siglbl">Date</div></div>
  </div>
</div>"""


def main():
    today = datetime.date.today()
    nice = today.strftime('%B %-d, %Y') if os.name != 'nt' else today.strftime('%B %d, %Y').replace(' 0', ' ')
    body = (TEXTING + RECORDING).replace('@@SIG@@', SIG).replace('@@DATE@@', nice)
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>Operating Rules</title>'
           '<style>%s</style></head><body>%s</body></html>' % (CSS, body))
    hp = os.path.join(HERE, 'MSG_Operating_Rules_%s.html' % today.isoformat())
    open(hp, 'w', encoding='utf-8').write(doc)

    from playwright.sync_api import sync_playwright
    outs = [os.path.join(HERE, 'MSG_Operating_Rules_%s.pdf' % today.isoformat())]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        outs.append(P.out('MSG_Operating_Rules_%s.pdf' % today.isoformat()))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto('file:///' + hp.replace(os.sep, '/'))
        pg.wait_for_timeout(300)
        pdf = pg.pdf(format='Letter', print_background=True, prefer_css_page_size=True,
                     margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    for o in outs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'wb').write(pdf)
        print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))
    print('\n2 signable rules: manual-send-only texting (FTSA) + no call recording (FS 934.03).')
    print('Everyone who contacts an owner signs BOTH before their first contact. Keep the originals.')


if __name__ == '__main__':
    main()
