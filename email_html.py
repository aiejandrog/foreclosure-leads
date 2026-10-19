"""email_html -- the BSG design around an outreach email, as a text/html part beside the text.

WHY (2026-10-19, at Alejandro's direction: "send that off for all email sends", then "avoid spam").
He approved a branded layout (logo, headline rule, footer band) for homeowner email. Every send
path built a text/plain message only. This module gives those messages an HTML alternative in that
layout. It does not change a word of any message.

WHAT IT KEEPS, ON PURPOSE
- The plain-text part. set_content(body) still runs first and is still what mail_guard checks.
  This module only adds a second part with the SAME words. A text part identical to the HTML part
  is what spam filters expect from a person, and a message that is HTML only is a spam signal.
- The words. The HTML is built from the plain body line by line: paragraphs, "  • " bullets, links.
  No headline, no extra footer copy, no disclaimer that the text part lacks. Any wording change
  belongs in outreach_copy / disclaimer, where it is a legal call, never here.
- Light weight. At most one logo image (none by default), inline CSS only, no tracking pixel, no web fonts, no images in the
  body. Image-heavy mail from a young domain is the other common spam trigger.

WHO GETS IT
Only mail sent under the outreach gates: send_server (/send), outreach_email and cadence.
reply_send stays plain text: a reply to a homeowner who wrote in is a person answering, and a
branded layout there reads as a mass mailer. Mail to ourselves (advisor digests, smoke tests) is
ungated and stays plain. DEALFLOW_EMAIL_HTML=0 turns the HTML part off everywhere.
"""
import html as _html
import os
import re

# The logo, as a public https URL. Empty by default, which shows a text wordmark instead: an email
# with no image at all is the safest shape for a young sending domain, and the only BSG logo online
# today sits on the company dashboard site, whose address does not belong in homeowner mail (and
# _boardurltest refuses any GitHub Pages address in a module). Set DEALFLOW_EMAIL_LOGO_URL to
# a logo hosted on bsgflorida.com to show it.
LOGO_URL = os.environ.get('DEALFLOW_EMAIL_LOGO_URL', '').strip()

# The outreach gates (mail_guard.gated labels) whose mail gets the design.
HTML_GATES = frozenset({'send_server', 'outreach_email', 'cadence'})

_BLUE = '#1d4f9c'
_INK = '#333d4a'
_FONT = 'Arial,Helvetica,sans-serif'

_URL = re.compile(r'https?://[^\s<>"]+[^\s<>".,;:!?)]')
_MAIL = re.compile(r'(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_PHONE = re.compile(r'(?<![\d-])\(?\b(\d{3})\)?[ .-]?(\d{3})[ .-](\d{4})\b(?![\d-])')
_BULLET = re.compile(r'^\s*[•\-*]\s+')


def enabled():
    return str(os.environ.get('DEALFLOW_EMAIL_HTML', '1')).strip().lower() not in ('0', 'false', 'off', 'no')


def wants_html(gate):
    """True when a message sent under mail_guard gate `gate` should carry the HTML part."""
    return enabled() and str(gate or '').strip() in HTML_GATES


def _link(a_text, href):
    return '<a href="%s" style="color:%s;text-decoration:none;">%s</a>' % (
        _html.escape(href, quote=True), _BLUE, a_text)


def _inline(line):
    """Escape one line of text and turn URLs, email addresses and US phone numbers into links."""
    out, pos = [], 0
    spans = []
    for rx, kind in ((_URL, 'url'), (_MAIL, 'mail'), (_PHONE, 'tel')):
        for m in rx.finditer(line):
            if any(m.start() < e and s < m.end() for s, e, _ in spans):
                continue
            spans.append((m.start(), m.end(), kind))
    for s, e, kind in sorted(spans):
        out.append(_html.escape(line[pos:s]))
        raw = line[s:e]
        if kind == 'url':
            href = raw
        elif kind == 'mail':
            href = 'mailto:' + raw
        else:
            href = 'tel:+1' + re.sub(r'\D', '', raw)
        out.append(_link(_html.escape(raw), href))
        pos = e
    out.append(_html.escape(line[pos:]))
    return ''.join(out)


def _blocks(body):
    """Split the plain body into ('p', [lines]) and ('ul', [items]) blocks on blank lines."""
    blocks, cur = [], []
    for raw in str(body or '').replace('\r\n', '\n').split('\n'):
        if raw.strip():
            cur.append(raw.rstrip())
            continue
        if cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    out = []
    for b in blocks:
        if all(_BULLET.match(l) for l in b):
            out.append(('ul', [_BULLET.sub('', l) for l in b]))
        else:
            out.append(('p', b))
    return out


def render(body, logo_url=None):
    """The HTML part for plain-text `body`. Same words, same order, in the BSG layout."""
    logo = LOGO_URL if logo_url is None else logo_url
    parts = []
    for kind, lines in _blocks(body):
        if kind == 'ul':
            items = ''.join('<li style="margin:0 0 6px 0;">%s</li>' % _inline(l) for l in lines)
            parts.append('<ul style="margin:0 0 16px 0;padding:0 0 0 22px;">%s</ul>' % items)
        else:
            parts.append('<p style="margin:0 0 16px 0;">%s</p>' % '<br>'.join(_inline(l) for l in lines))
    logo_row = (
        '<tr><td align="left" style="padding:22px 28px 6px 28px;font-family:Georgia,\'Times New Roman\','
        'serif;font-size:15px;font-weight:bold;letter-spacing:2px;color:%s;">BISCAYNE SOLUTIONS GROUP'
        '</td></tr>' % _BLUE)
    if logo:
        logo_row = (
            '<tr><td align="left" style="padding:22px 28px 6px 28px;">'
            '<img src="%s" alt="Biscayne Solutions Group" width="88" style="display:block;width:88px;'
            'height:auto;border:0;font-family:%s;font-size:14px;font-weight:bold;color:%s;"></td></tr>'
            % (_html.escape(logo, quote=True), _FONT, _BLUE))
    return (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
        '<body style="margin:0;padding:0;background-color:#f2f4f7;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background-color:#f2f4f7;"><tr><td align="center" style="padding:20px 10px;">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;max-width:600px;background-color:#ffffff;border:1px solid #dde2ea;">'
        + logo_row +
        '<tr><td style="padding:10px 28px 0 28px;"><table role="presentation" width="64" cellpadding="0" '
        'cellspacing="0" border="0"><tr><td style="height:3px;line-height:3px;font-size:3px;'
        'background-color:%s;">&nbsp;</td></tr></table></td></tr>' % _BLUE +
        '<tr><td style="padding:20px 28px 12px 28px;font-family:%s;font-size:15px;line-height:1.6;'
        'color:%s;">' % (_FONT, _INK) + ''.join(parts) + '</td></tr>'
        '<tr><td style="padding:0;height:8px;line-height:8px;font-size:8px;background-color:#f8f9fb;'
        'border-top:1px solid #e6e9ef;">&nbsp;</td></tr>'
        '</table></td></tr></table></body></html>')


def attach(msg, body, gate):
    """Add the HTML alternative to EmailMessage `msg` when `gate` gets the design. Call it right
    after msg.set_content(body) and before any attachment. Returns True when a part was added."""
    if not wants_html(gate):
        return False
    msg.add_alternative(render(body), subtype='html')
    return True
