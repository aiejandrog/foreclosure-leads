# -*- coding: utf-8 -*-
"""Print the outreach copy as a PDF for the team to critique.

Shows the words and nothing else. No editorial, no change log — the copy is Alejandro's and the
team is reviewing HIS work, not a diff against it.
"""
import datetime as dt
import html
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import outreach_copy as C  # noqa: E402

SALE = dt.date(2026, 9, 16)
FIRST = 'Ann'
OUT = os.path.join(HERE, 'BSG_Outreach_Copy_%s.pdf' % dt.date.today().isoformat())


def esc(s):
    return html.escape(str(s or '')).replace('\n', '<br>')


CSS = """
@page { size: Letter; margin: 0.6in 0.55in; }
* { box-sizing: border-box; }
body { font-family:'Segoe UI',Arial,sans-serif; color:#10182b; font-size:10pt; margin:0; }
h1 { font-size:20pt; margin:0 0 3px; color:#0d1b3e; letter-spacing:-.3px; }
.sub { color:#5a6785; font-size:9pt; margin-bottom:16px; }
h2 { font-size:12pt; color:#0d1b3e; margin:0 0 8px; padding-bottom:4px;
     border-bottom:2px solid #ffd76b; text-transform:uppercase; letter-spacing:.5px; }
.ch { page-break-inside:avoid; margin-bottom:22px; }
.copy { background:#f8fafc; border:1px solid #dde3ee; border-left:4px solid #0d1b3e;
        border-radius:5px; padding:13px 15px; white-space:pre-wrap; font-size:9.5pt;
        line-height:1.55; }
.subj { background:#0d1b3e; color:#fff; padding:8px 13px; border-radius:5px 5px 0 0;
        font-size:9.5pt; font-weight:600; }
.subj + .copy { border-radius:0 0 5px 5px; border-top:none; }
.meta { font-size:8.5pt; color:#5a6785; margin:6px 0 0; }
.note { background:#eef3fa; border-left:4px solid #2b5b9e; padding:10px 13px; border-radius:4px;
        margin:10px 0 0; font-size:9pt; }
.note b { display:block; margin-bottom:3px; }
.small { font-size:8pt; color:#5a6785; line-height:1.55; margin-top:16px; }
"""


def build_html():
    return """<!doctype html><html><head><meta charset="utf-8"><style>%s</style></head><body>

<h1>Outreach copy &mdash; for review</h1>
<div class="sub">Biscayne Solutions Group &middot; %s &middot; email, text and letter &middot;
example uses a sale date of Sept 16</div>

<div class="ch">
<h2>1 &middot; Email &mdash; short version</h2>
<div class="subj">Subject: %s</div>
<div class="copy">%s</div>
</div>

<div class="ch">
<h2>2 &middot; Email &mdash; long version</h2>
<div class="subj">Subject: %s</div>
<div class="copy">%s</div>
</div>

<div class="ch">
<h2>3 &middot; Text message &mdash; %d characters</h2>
<div class="copy">%s</div>
<p class="meta">Two segments. Sent by hand, one at a time &mdash; never auto-blasted.</p>
</div>

<div class="ch">
<h2>4 &middot; Letter</h2>
<div class="copy">%s</div>
<p class="meta">Sealed envelope, first name + PERSONAL on the front. Door handle, not the mailbox.</p>
</div>

<div class="ch">
<h2>5 &middot; The one paragraph that has to stay</h2>
<div class="note"><b>The disclosure block at the bottom of each message</b>
Federal rule 16 CFR 1015.4(a) requires it on any commercial message offering to help a homeowner
with a foreclosure, and a free consultation to review their options counts. It is the same wording
on every channel because it comes from one file &mdash; the text, the email and the letter cannot
drift apart. Everything above it is ours to argue about; this paragraph is not.</div>
</div>

<p class="small">Generated from outreach_copy.py &mdash; change the words there and all three
channels change together. Worth an attorney's eyes before any volume send.</p>
</body></html>""" % (
        CSS,
        dt.date.today().strftime('%B ') + str(dt.date.today().day) + ', 2026',
        esc(C.email_subject(SALE, short=True)), esc(C.email_body_short(FIRST, SALE)),
        esc(C.email_subject(SALE)), esc(C.email_body(FIRST, SALE)),
        len(C.sms(FIRST, SALE)), esc(C.sms(FIRST, SALE)),
        esc(C.letter(FIRST, SALE)),
    )


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content(build_html(), wait_until='load')
        pg.pdf(path=OUT, prefer_css_page_size=True, print_background=True)
        b.close()
    print('wrote %s (%.0f KB)' % (OUT, os.path.getsize(OUT) / 1024))
    import shutil
    desk = r'C:\Users\olqbb\OneDrive\Desktop\%s' % os.path.basename(OUT)
    shutil.copy(OUT, desk)
    print('desktop: %s' % desk)


if __name__ == '__main__':
    main()
