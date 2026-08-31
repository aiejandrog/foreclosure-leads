"""_mirrortest.py -- PROVE genEmail() and outreach_email.py send the same email.

WHY THIS EXISTS
The browser composer (tracker_template.html genEmail) and the unattended sender
(outreach_email.py) are supposed to be byte-mirrors of each other. They have drifted THREE times:

    2026-08-22  genEmail said "not a lawyer" and dropped the foreclosure-rescue denial; the
                automated path sent disclaimer.identity(). Two disclosures, same homeowner.
    2026-08-28  a comment claiming "byte-mirror of genEmail" was false -- auto and manual sent
                different bodies.
    2026-08-29  the automated body had no senior-advisor framing and told owners an attorney
                could "pause the case"; the manual one did not.

Every one of those was found by reading, not by a test. Reading two long f-strings and believing
they match is exactly the thing humans are worst at. This runs both renderers on ONE real lead
with ONE sender identity and compares the actual strings.

It also runs the REAL replies.py regex against the composed subject. Reply attribution depends on
the subject ending in "Regarding your property at <STREET>"; a prefix is safe, a suffix silently
destroys it, and nothing else in the suite would notice.

    python _mirrortest.py
"""
import functools
import http.server
import json
import os
import re
import socketserver
import sys
import threading

from playwright.sync_api import sync_playwright

import paths as P

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, 'docs')
PORT = 8801
CODE = P.live_code()

SENDER = {'name': 'Jose Ramirez', 'llc': 'JR Property Group LLC', 'phone': '305-555-0142',
          'addr': '123 Main St, Miami FL 33172', 'title': 'Acquisitions',
          'email': 'jose@jrpg.com', 'web': 'jrpropertygroup.com'}

R = []


def rec(name, ok, detail=''):
    R.append(bool(ok))
    line = (' PASS ' if ok else ' FAIL ') + name + (' | ' + detail if detail else '')
    print(line.encode('ascii', 'replace').decode('ascii'))


def first_diff(a, b):
    """Human-readable location of the first difference between two strings."""
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return 'char %d: js=%r py=%r  ...context %r vs %r' % (
                i, a[i], b[i], a[max(0, i - 45):i + 25], b[max(0, i - 45):i + 25])
    if len(a) != len(b):
        return 'identical for %d chars then lengths differ (js=%d py=%d); tail js=%r py=%r' % (
            n, len(a), len(b), a[n:n + 90], b[n:n + 90])
    return ''


Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DOCS)


class Q(socketserver.TCPServer):
    allow_reuse_address = True

    def log_message(self, *a):
        pass


threading.Thread(target=Q(('127.0.0.1', PORT), Handler).serve_forever, daemon=True).start()

import outreach_copy as OC          # noqa: E402
import outreach_email as OE         # noqa: E402

# replies.py is imported for its REAL constants -- read textually so importing this test can never
# open an IMAP connection or need gmail.key.
_rep_src = open(os.path.join(HERE, 'replies.py'), encoding='utf-8').read()
SUBJ_TAG = eval(re.search(r'^SUBJ_TAG\s*=\s*(.+)$', _rep_src, re.M).group(1))
ADDR_RE = re.search(r"re\.search\(r'(property at[^']*)'", _rep_src).group(1)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_context().new_page()
    pg.goto('http://127.0.0.1:%d/index.html' % PORT, wait_until='domcontentloaded')
    pg.wait_for_selector('#gatepw', timeout=15000)
    pg.fill('#gatepw', CODE)
    pg.click('#gatego')
    pg.wait_for_function(
        "()=>getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)
    pg.wait_for_selector('#tbl tbody tr', timeout=10000)

    pg.evaluate("""(s)=>{ Object.assign(sender, s); if(typeof saveSender==='function') saveSender(); }""",
                SENDER)

    rec('board exposes the baked copy + the mirrored fill', pg.evaluate(
        "()=>typeof _ALEXMAIL_EN==='string' && _ALEXMAIL_EN.length>500 "
        "&& typeof _alexFill==='function' && typeof _subjLine==='function'"),
        'len=%s' % pg.evaluate("()=>(_ALEXMAIL_EN||'').length"))

    rec('baked template is byte-identical to outreach_copy.email_body_template()',
        pg.evaluate("()=>_ALEXMAIL_EN") == OC.email_body_template(),
        'js=%d py=%d chars' % (len(pg.evaluate("()=>_ALEXMAIL_EN") or ''),
                               len(OC.email_body_template())))

    rec('SUBJECT_STYLE agrees across the two files',
        pg.evaluate("()=>SUBJECT_STYLE") == OE.SUBJECT_STYLE,
        'board=%r outreach_email=%r' % (pg.evaluate("()=>SUBJECT_STYLE"), OE.SUBJECT_STYLE))

    # A COLD, contactable, English, foreclosure-with-a-date lead -- the path Alejandro's copy owns.
    # Unfiltered this lands on a §362 stay (suppression notice, not an email) or a follow/final
    # stage, and every body assertion below would then describe a different document.
    lead = pg.evaluate("""()=>{
      const blocked = (typeof _textContactBlocked==='function') ? (r=>!!_textContactBlocked(r)) : (()=>false);
      const stage = (typeof _mailStage==='function') ? (r=>_mailStage(r)) : (()=>'cold');
      const ok = r => (r.emails||[]).filter(_mailable).length && !blocked(r)
                      && r.st!=='TD' && r.auction && stage(r)==='cold';
      const r = DATA.find(ok);
      if(!r) return null;
      return {case:r.case, owners:r.owners, addr:r.addr, auction:r.auction, st:r.st,
              plaintiff:r.plaintiff||'', emails:(r.emails||[]).slice(),
              jsOwner:_ownerName((r.owners||'').split(';')[0].trim(), r),
              jsFirst:_firstName(_ownerName((r.owners||'').split(';')[0].trim(), r)),
              stage:stage(r)};
    }""")
    rec('found a COLD contactable FC lead with a sale date', bool(lead),
        (lead['case'] + ' | ' + lead['addr']) if lead else 'none on this board')
    if not lead:
        b.close()
        print('\n==== %d/%d mirror checks passed ====' % (sum(R), len(R)))
        raise SystemExit(1)

    js = pg.evaluate("""(c)=>{ const r=DATA.find(x=>x.case===c); genEmail(r, true);
                               return {subj:window.__lastEmail.subj, body:window.__lastEmail.body,
                                       subjES:window.__lastEmail.subjES, stage:window.__lastEmail.stage}; }""",
                     lead['case'])

    # Same row, same sender, through the AUTOMATED composer.
    row = {'case': lead['case'], 'owners': lead['owners'], 'addr': lead['addr'],
           'auction': lead['auction'], 'st': lead['st'], 'plaintiff': lead['plaintiff'],
           'emails': lead['emails']}
    py = OE._compose_single(row, SENDER, 'en')
    py_es = OE._compose_single(row, SENDER, 'es')

    rec('name derivation agrees (first name feeds the greeting)',
        OE._first_name(row) == lead['jsFirst'],
        'py=%r js=%r' % (OE._first_name(row), lead['jsFirst']))

    # A lead with no parseable owner name is where the two paths used to part ways: the Python
    # composer falls back to the whole owner string, the browser greets "Hi,". Prove they now agree
    # on a synthetic nameless row rather than hoping the board never produces one.
    nameless = dict(row, owners='', case=row['case'] + '-NONAME')
    js_nameless = pg.evaluate("""(r)=>{ genEmail(r, true); return window.__lastEmail.body; }""",
                              nameless)
    py_nameless = OE._compose_single(nameless, SENDER, 'en')['body']
    rec('nameless lead: greeting is identical on both paths',
        js_nameless == py_nameless and js_nameless.startswith('Hi,'),
        js_nameless.splitlines()[0] if js_nameless == py_nameless
        else 'js=%r py=%r' % (js_nameless.splitlines()[0], py_nameless.splitlines()[0]))

    rec('SUBJECT is identical (EN)', js['subj'] == py['subj'],
        js['subj'] if js['subj'] == py['subj'] else
        'js=%r\n                py=%r' % (js['subj'], py['subj']))
    rec('SUBJECT is identical (ES)', js['subjES'] == py_es['subj'],
        js['subjES'] if js['subjES'] == py_es['subj'] else
        'js=%r py=%r' % (js['subjES'], py_es['subj']))
    rec('BODY is identical (EN cold)', js['body'] == py['body'],
        '%d chars, byte for byte' % len(js['body']) if js['body'] == py['body']
        else first_diff(js['body'], py['body']))

    # ---- the subject still has to survive replies.py -------------------------------------------
    reply_subj = 'Re: ' + js['subj']
    street = (lead['addr'].split(',')[0] or lead['addr']).strip()
    m = re.search(ADDR_RE, reply_subj, re.I)
    rec('replies.py IMAP search still finds it (SUBJ_TAG substring)', SUBJ_TAG in reply_subj,
        SUBJ_TAG)
    rec('replies.py PASS-2 still captures the street cleanly',
        bool(m) and m.group(1).strip() == street,
        'captured %r from %r' % (m.group(1) if m else None, reply_subj))
    rec('subject ENDS with the tag + street (nothing appended after it)',
        js['subj'].endswith(SUBJ_TAG + ' ' + street), js['subj'][-60:])

    # ---- Alejandro's copy is actually the copy that shipped -------------------------------------
    body = js['body']
    for probe in ('Your home is scheduled for foreclosure auction on',
                  'We specialize in Urgent Foreclosure Cases',
                  '15-minute private phone consultation',
                  'There are no tricks, no hidden fees, and no pressure.',
                  'Reply now or call me today.'):
        rec('his copy present: %r' % probe[:48], probe in body)
    rec('URGENT framing is on the subject', js['subj'].startswith('URGENT:'), js['subj'][:48])

    # ---- and it is still compliant ---------------------------------------------------------------
    import disclaimer as D
    rec('identity disclosure baked from disclaimer.py',
        D.identity('en', as_html=False) in body)
    rec('MARS block present on BOTH paths',
        ('may not agree to change your loan' in body and 'stop doing business' in body
         and 'may not agree to change your loan' in py['body']))
    rec('CAN-SPAM physical mailing address present', SENDER['addr'] in body)
    rec('signature carries title + phone + email',
        SENDER['title'] in body and 'Phone: ' + SENDER['phone'] in body
        and 'Email: ' + SENDER['email'] in body)
    rec('opt-out line present', 'reply STOP' in body)
    rec('no sentinel survived into the sent body',
        not any(t in body for t in OC.TOK.values()),
        'tokens found: %s' % [k for k, t in OC.TOK.items() if t in body])

    # ---- the style switch moves BOTH sides ------------------------------------------------------
    js_m = pg.evaluate("""(c)=>{ const old=SUBJECT_STYLE; SUBJECT_STYLE='measured';
                                 const r=DATA.find(x=>x.case===c); genEmail(r, true);
                                 const s=window.__lastEmail.subj; SUBJECT_STYLE=old; return s; }""",
                       lead['case'])
    _old = OE.SUBJECT_STYLE
    OE.SUBJECT_STYLE = 'measured'
    py_m = OE._compose_single(row, SENDER, 'en')['subj']
    OE.SUBJECT_STYLE = _old
    rec("SUBJECT_STYLE='measured' flips BOTH files to the same string", js_m == py_m,
        js_m if js_m == py_m else 'js=%r py=%r' % (js_m, py_m))
    rec("'measured' still ends with the tag + street",
        js_m.endswith(SUBJ_TAG + ' ' + street) and not js_m.startswith('URGENT'), js_m)

    b.close()

print('\n---- the email both paths now produce ----')
print('SUBJECT: ' + js['subj'])
print()
print(js['body'])
print('\n==== %d/%d mirror checks passed ====' % (sum(R), len(R)))
raise SystemExit(0 if sum(R) == len(R) else 1)
