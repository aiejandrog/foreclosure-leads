"""_emailhtmltest -- email_html adds a text/html part with the SAME words, only under outreach gates.

    python _emailhtmltest.py
"""
import html
import os
import re
import sys
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop('DEALFLOW_EMAIL_HTML', None)
os.environ.pop('DEALFLOW_EMAIL_LOGO_URL', None)

import email_html as EH
import outreach_copy as OC

FAILS = []


def check(name, ok):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAILS.append(name)


def words(s):
    return re.sub(r'\s+', ' ', s.replace('•', ' ')).strip()


def html_text(h):
    body = re.search(r'<td style="padding:20px 28px 12px.*?">(.*)</td></tr><tr><td style="padding:0;height:8px', h, re.S).group(1)
    body = re.sub(r'<br>|</p>|</li>', ' ', body)
    return words(html.unescape(re.sub(r'<[^>]+>', '', body)))


bodies = {
    'long': OC.email_body('Maria', '2026-11-04', sig_block='Alejandro Gonzalez\nBiscayne Solutions Group\n'
                          '(305) 555-0100\n8400 NW 33rd St, Ste 310, PMB 2008, Doral, FL 33122'),
    'short': OC.email_body_short('Maria', '2026-11-04'),
    'tricky': 'Hi <Ann> & Co,\n\nSee https://example.com/a?b=1&c=2, or mail a.b@bsgflorida.com.\n'
              'Call (305) 555-0100.\n\n  • one\n  • two\n\n' + OC.OPTOUT_LINE_EN + '\n' + OC.OPTOUT_LINE_ES,
}
for k, b in bodies.items():
    h = EH.render(b)
    check('%s: same words as the text part' % k, html_text(h) == words(b))
    check('%s: no script, no image by default' % k, '<script' not in h.lower() and '<img' not in h)
    hl = EH.render(b, logo_url='https://bsgflorida.com/logo.png')
    check('%s: one image when a logo url is set' % k, hl.count('<img') == 1 and html_text(hl) == words(b))

h = EH.render(bodies['tricky'])
check('escapes markup in the body', '&lt;Ann&gt; &amp; Co' in h and '<Ann>' not in h)
check('links the url with & escaped', 'href="https://example.com/a?b=1&amp;c=2"' in h)
check('links the email', 'href="mailto:a.b@bsgflorida.com"' in h)
check('links the phone', 'href="tel:+13055550100"' in h)
check('a dashed case number is not a phone', 'tel:' not in EH.render('Case 13-2025-CA-012345-0000-01'))
check('bullets become a list', '<ul' in h and h.count('<li') == 2)
check('opt-out lines survive', html.escape(OC.OPTOUT_LINE_EN) in h and OC.OPTOUT_LINE_ES in html.unescape(h))
check('wordmark when no logo', 'BISCAYNE SOLUTIONS GROUP' in EH.render('x', logo_url=''))

for gate, want in (('send_server', True), ('outreach_email', True), ('cadence', True),
                   ('reply_send', False), ('', False), ('anything', False)):
    m = EmailMessage()
    m.set_content(bodies['short'])
    EH.attach(m, bodies['short'], gate)
    types = [p.get_content_type() for p in m.walk()]
    check('gate %r -> html %s' % (gate, want), ('text/html' in types) == want and 'text/plain' in types)

m = EmailMessage()
m.set_content('Hi,\n\nworkup attached.')
EH.attach(m, 'Hi,\n\nworkup attached.', 'send_server')
m.add_attachment(b'%PDF-1.4', maintype='application', subtype='pdf', filename='a.pdf')
types = [p.get_content_type() for p in m.walk()]
check('attachment after html -> mixed(alternative(plain, html), pdf)',
      types == ['multipart/mixed', 'multipart/alternative', 'text/plain', 'text/html', 'application/pdf'])
m = EmailMessage()
m.set_content('x')
EH.attach(m, 'x', 'send_server')
check('plain part comes first (clients fall back to it)', [p.get_content_type() for p in m.iter_parts()] == ['text/plain', 'text/html'])

os.environ['DEALFLOW_EMAIL_HTML'] = '0'
m = EmailMessage()
m.set_content('x')
check('DEALFLOW_EMAIL_HTML=0 turns it off', not EH.attach(m, 'x', 'send_server') and not m.is_multipart())
os.environ.pop('DEALFLOW_EMAIL_HTML')

for f in ('send_server.py', 'outreach_email.py'):
    src = open(os.path.join(HERE, f), encoding='utf-8').read()
    i = src.index('def _smtp_send')
    seg = src[i:src.index('\ndef ', i + 10)]
    check('%s: html attached right after set_content, under the gate' % f,
          seg.index('msg.set_content(body)') < seg.index('_EH.attach(msg, body, _MG.current_gate())')
          and ('add_attachment' not in seg or seg.index('_EH.attach') < seg.index('add_attachment')))

print('\n%d failed' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
