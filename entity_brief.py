#!/usr/bin/env python
"""entity_brief — the factual packet a Florida RE attorney needs to cure the entity-name problem.

WHAT THIS IS
An INVENTORY, not advice. It states which instruments name which entity, what the Florida register
says about each of those names as of the run date, and where each claim is generated from. Every
legal conclusion is left to the attorney; this exists so nobody has to reconstruct the facts from
memory, and so the register is quoted rather than remembered.

WHY IT MATTERS FOR ACOSTA SPECIFICALLY
The signed Acosta retainer names an entity that was never ours. A retainer in a foreclosure-related
transaction sits inside FS 501.1377 and the federal MARS rule (12 CFR 1015), so the identity of the
contracting party is not a clerical detail. Curing it is an attorney's call about which instrument
to amend, re-paper or rescind -- and that call needs the register, not a recollection.

RUN
    python entity_brief.py              # writes the brief, live Sunbiz lookups
    python entity_brief.py --offline    # skip the lookups (uses the cached verdict only)
"""
import argparse
import html
import io
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import entity  # noqa: E402
import paths as P  # noqa: E402

# Names that have appeared as a contracting or represented party. Each is looked up live.
NAMES = [
    ('Miami Solutions Group LLC',
     'The name on the SIGNED Acosta retainer and on the 2026-07-31 legal-pack drafts. Never ours.'),
    ('Biscayne Solutions Group LLC',
     'The current company. sender.json holds it as the canonical entity string.'),
    ('Biscayne Solutions Inc',
     'A DIFFERENT, active Florida company with a near-identical name. Relevant to FS 605.0112 '
     'distinguishability and to consumer-confusion exposure.'),
    ('DealFlow Investments LLC',
     'Was the hardcoded fallback on the door-step Identity card until 2026-08-23. Removed.'),
]

# Where an entity name reaches a person, and what generates it.
SURFACES = [
    ('Acosta retainer (SIGNED)', 'executed paper',
     'Names Miami Solutions Group LLC. NOT regenerable -- this is the instrument to cure.'),
    ('Retainer / Contract for Services', 'make_bsg_forms.py',
     'EN + ES. The entity is a DEFINED TERM: "... , LLC (“BSG”)". Regenerates from sender.json.'),
    ('Third-Party Authorization', 'make_bsg_forms.py',
     'EN + ES. Names the company as authorized representative, and in the revocation clause.'),
    ('Quit-claim deed', 'tracker_template.html genQuitClaim',
     'Grantee. Since 2026-08-23 it renders a FILL-IN BLANK while the entity is unverified -- a deed '
     'naming a grantee that does not exist cannot take title, and it gets RECORDED.'),
    ('Door letter / flyer / letterhead', 'bsg_letter.py, bsg_flyer.py, outreach_mail.py',
     'Footer entity line and the MARS 1015.4(a) disclosure. All gated by entity.py.'),
    ('Published board + Call Mode', 'tracker_template.html -> docs/',
     'SENDER_DEFAULTS, injected at build time from entity.py. Public site.'),
    ('Sent outreach archive', 'worker_notes.json sentArchive',
     '300 emails already DELIVERED under the old name, each carrying the disclosure as sent. '
     'Deliberately NOT rewritten -- it is the record of what each homeowner received.'),
]

CSS = """
body{font:14px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 color:#12212E;max-width:940px;margin:0 auto;padding:34px 26px}
h1{font-size:23px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:15px;margin:30px 0 9px;padding-bottom:5px;border-bottom:2px solid #12212E;
 text-transform:uppercase;letter-spacing:.07em}
.sub{color:#5B6B7A;margin:0 0 22px;font-size:12.5px}
table{border-collapse:collapse;width:100%;margin:8px 0 4px;font-size:13px}
th,td{text-align:left;vertical-align:top;padding:7px 9px;border-bottom:1px solid #E3E9EE}
th{background:#F5F8FA;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#5B6B7A}
code{background:#F5F8FA;padding:1px 5px;border-radius:3px;font-size:12px}
.ok{color:#1B6B45;font-weight:700}.bad{color:#A3261F;font-weight:700}.warn{color:#7A5A12;font-weight:700}
.note{background:#FFF8E8;border-left:3px solid #C9A227;padding:11px 14px;margin:14px 0;font-size:13px}
.foot{margin-top:30px;padding-top:12px;border-top:1px solid #E3E9EE;color:#5B6B7A;font-size:12px}
"""


def lookup(name, offline):
    if offline:
        return {'status': '(not checked -- --offline)', 'doc': '', 'filed': '', 'matched': '', 'ra': ''}
    import entity_check
    return entity_check.check(name)


def cls(status):
    return 'ok' if status == 'ACTIVE' else ('bad' if status in ('NOT_FOUND', 'INEXACT') else 'warn')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--offline', action='store_true')
    a = ap.parse_args()
    e = html.escape
    today = date.today().isoformat()

    rows = []
    for name, why in NAMES:
        v = lookup(name, a.offline)
        st = v.get('status') or 'UNKNOWN'
        rows.append(
            '<tr><td><b>%s</b><br><span style="color:#5B6B7A">%s</span></td>'
            '<td class="%s">%s</td><td><code>%s</code></td><td>%s</td><td>%s</td></tr>'
            % (e(name), e(why), cls(st), e(st), e(v.get('doc') or '-'),
               e(v.get('filed') or '-'), e(v.get('matched') or '-')))

    surf = ''.join('<tr><td><b>%s</b></td><td><code>%s</code></td><td>%s</td></tr>'
                   % (e(n), e(src), e(note)) for n, src, note in SURFACES)

    disp, doc, warn = entity.display_llc()
    gate = ('<span class="ok">OPEN</span> &mdash; verified, document number <code>%s</code>' % e(doc)
            if entity.verified() else
            '<span class="bad">SHUT</span> &mdash; the &ldquo;LLC&rdquo; suffix is withheld on every '
            'generated surface. Currently printing <b>%s</b>.' % e(disp))

    doc_html = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Entity-name brief &mdash; for FL real-estate counsel</title><style>%s</style></head><body>
<h1>Entity-name brief &mdash; prepared for Florida real-estate counsel</h1>
<p class="sub">Generated %s from live Sunbiz lookups. Factual inventory only &mdash; no legal
conclusions are drawn here.</p>

<div class="note"><b>The question for counsel.</b> A signed retainer in a foreclosure-related
transaction names a Florida entity that the client does not own and never did. What has to happen to
that instrument &mdash; amendment, re-papering, rescission, notice to the homeowner &mdash; and does
anything else already delivered need to be cured alongside it?</div>

<h2>1. The names, as the register has them today</h2>
<table><tr><th>Name / why it appears</th><th>Status</th><th>Document #</th><th>Filed</th>
<th>Exact registered name</th></tr>%s</table>
<p class="sub">Looked up through the Florida Division of Corporations entity-name search on %s.
&ldquo;NOT_FOUND&rdquo; means no exact match; the search lists alphabetically from the term, so a
contiguous window with no gap is what evidences an absence.</p>

<h2>2. Where an entity name reaches a person</h2>
<table><tr><th>Instrument / surface</th><th>Generated by</th><th>Notes</th></tr>%s</table>

<h2>3. Current state of the software guard</h2>
<p>Entity gate: %s</p>
<p>Since 2026-08-23 no generated document asserts an entity the register cannot substantiate. The
guard is driven by a live lookup rather than a configuration flag, and the published board is
scanned for the claim before any publish is allowed. <b>This does not reach paper already
signed.</b></p>

<h2>4. Points already flagged in the file</h2>
<ul>
<li>FS 501.1377(5)(a) requires the cancellation notice in at least <b>12-point uppercase type
immediately above the seller&rsquo;s signature line</b>. The drafts&rsquo; identified facial defect
is <b>placement</b>, not size.</li>
<li>Cancellation is <b>3 business days by 5 p.m.</b> under (5)(b). The advance-fee bar is (3)(b).</li>
<li>There is <b>no subsection (9) and no treble damages</b>; remedies are FDUTPA part-II plus a civil
penalty up to <b>$15,000 per violation</b>. An earlier internal draft overstated this &mdash; brief
counsel from the statute, not from that draft.</li>
<li>Assignment to an entity does not launder individual liability: it attaches to the individual
signer at the moment of signature.</li>
<li>The legal-pack drafts remain attorney-gated and have never been shown to a seller.</li>
</ul>

<h2>5. Open items that are not software</h2>
<ul>
<li>Confirm the Division accepted <b>Biscayne Solutions Group LLC</b> as distinguishable from the
active <b>Biscayne Solutions Inc.</b> (FS 605.0112), and supply the document number.</li>
<li>The correspondence inbox printed on client paper still carries the former company name.</li>
<li>The carrier CNAM record for the outbound line still shows the former name.</li>
</ul>

<div class="foot">Generated by <code>entity_brief.py</code>. Contains no homeowner data.
Regenerate after any Sunbiz change so counsel is reading current facts.</div>
</body></html>""" % (CSS, e(today), ''.join(rows), e(today), surf, gate)

    out_repo = os.path.join(HERE, 'Entity_Brief_%s.html' % today)
    io.open(out_repo, 'w', encoding='utf-8', newline='').write(doc_html)
    print('wrote %s' % out_repo)
    try:
        out2 = P.out('Entity_Brief_%s.html' % today)
        io.open(out2, 'w', encoding='utf-8', newline='').write(doc_html)
        print('wrote %s' % out2)
    except Exception as ex:
        print('(second copy skipped: %s)' % ex)
    print('\nSend this to the FL real-estate attorney alongside the Desktop legal-pack drafts.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
